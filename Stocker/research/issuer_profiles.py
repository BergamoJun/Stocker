"""First-commit issuer-specific news queries and explicitly limited ETF references."""

from pathlib import Path
import jsonschema

from . import sec_refresh
from .local_files import atomic_json, exclusive_lock
from .news import canonical_url
from .news_review_store import precedes
from .rights import as_utc
from .sec_report import ROOT
from .sec_transport import now
from .validation import digest


FILES = ["research/issuer_profiles.py", "config/issuer_profiles.json", "config/news_refresh_policy.json", "schemas/issuer_context.schema.json"]


def methods():
    return {name: digest(ROOT / name) for name in FILES}


def validate(value, kind):
    schema = sec_refresh.read(ROOT / "schemas/issuer_context.schema.json")
    jsonschema.Draft202012Validator({"$ref": "#/$defs/" + kind, "$defs": schema["$defs"]}, format_checker=jsonschema.FormatChecker()).validate(value)


def policy():
    value = sec_refresh.read(ROOT / "config/news_refresh_policy.json")
    if any(value[k] is not False for k in ("release_enabled", "model_training_enabled", "publisher_body_fetch_enabled", "numeric_news_score_enabled", "recommendation_score_enabled", "official_feeds_clear_broad_topic_coverage")) or value["monthly_data_budget_usd"] != 0 or value["external_llm_calls"] != 0:
        raise ValueError("ISSUER_CONTEXT_RESEARCH_BOUNDARY_CHANGED")
    return value


def namespace(root=None, illustrative=False):
    folder = Path(root or ROOT / "reports/private/issuer_profiles").resolve()
    if not illustrative and not folder.is_relative_to((ROOT / "reports/private").resolve()):
        raise ValueError("PRIVATE_ISSUER_PROFILES_REQUIRED")
    return folder / ("illustrative" if illustrative else "real")


def identity(snapshot, profile, illustrative):
    if not snapshot or snapshot["is_illustrative"] is not illustrative or snapshot["cik"] != profile["cik"] or profile["symbol"] not in snapshot["identity"]["symbols"] or snapshot["entity_type"] != "operating":
        raise ValueError("ISSUER_PROFILE_SEC_IDENTITY_MISMATCH")
    return {"cik": profile["cik"], "symbol": profile["symbol"], "entity_type": snapshot["entity_type"]}


def capture(*, registry_path=None, root=None, source_store=None, illustrative=False, clock=now):
    policy()
    at = clock()
    registry = sec_refresh.read(registry_path or ROOT / "config/issuer_profiles.json")
    validate(registry, "registry")
    if registry["is_illustrative"] is not illustrative or not as_utc(registry["reviewed_at"]) <= as_utc(at) < as_utc(registry["review_due_at"]):
        raise ValueError("ISSUER_PROFILE_CLASSIFICATION_OR_REVIEW_TIME_INVALID")
    if len({p["cik"] for p in registry["profiles"]}) != len(registry["profiles"]) or len({p["symbol"] for p in registry["profiles"]}) != len(registry["profiles"]):
        raise ValueError("ISSUER_PROFILE_IDENTITY_CONFLICT")
    identities = []
    for profile in registry["profiles"]:
        for role in ("market_proxy", "sector_proxy"):
            canonical_url(profile[role]["source_url"])
        snapshot = sec_refresh.as_of(source_store or ROOT / "reports/private/sec_store/real", profile["cik"], at)
        identities.append(identity(snapshot, profile, illustrative))
    content = {"registry": registry, "identities": identities, "source_store": str(Path(source_store or ROOT / "reports/private/sec_store/real").resolve()),
        "method_hashes": methods(), "is_illustrative": illustrative}
    rid = sec_refresh.canonical_hash(content)
    folder = namespace(root, illustrative)
    with exclusive_lock(folder / "write.lock"):
        values = receipts(root=root, illustrative=illustrative)
        if values and values[-1]["content_sha256"] == rid:
            return {"receipt_id": values[-1]["receipt_id"], "available_at": values[-1]["available_at"], "status": "unchanged"}
        committed = clock()
        if as_utc(committed) < as_utc(at) or (values and not precedes(values[-1]["available_at"], committed)):
            raise ValueError("ISSUER_PROFILE_CLOCK_NOT_ADVANCED")
        order = values[-1]["store_order"] + 1 if values else 1
        receipt_id = f"{order:08d}-" + rid[:24]
        value = {"version": "1.0.0", **content, "content_sha256": rid, "receipt_id": receipt_id, "store_order": order, "available_at": committed}
        value["receipt_sha256"] = sec_refresh.canonical_hash(value)
        atomic_json(folder / "receipts" / (receipt_id + ".json"), value)
    return {"receipt_id": receipt_id, "available_at": committed, "status": "stored"}


def receipts(*, root=None, illustrative=False, cutoff=None):
    values = []
    for path in (namespace(root, illustrative) / "receipts").glob("*.json"):
        item = sec_refresh.read(path)
        if cutoff is not None and not precedes(item["available_at"], cutoff):
            continue
        if item["receipt_sha256"] != sec_refresh.canonical_hash({k: v for k, v in item.items() if k != "receipt_sha256"}) or path.stem != item["receipt_id"] or item["is_illustrative"] is not illustrative:
            raise ValueError("ISSUER_PROFILE_RECEIPT_CHANGED")
        content = {k: item[k] for k in ("registry", "identities", "source_store", "method_hashes", "is_illustrative")}
        if item["content_sha256"] != sec_refresh.canonical_hash(content) or item["receipt_id"] != f"{item['store_order']:08d}-" + item["content_sha256"][:24]:
            raise ValueError("ISSUER_PROFILE_CONTENT_CHANGED")
        validate(item["registry"], "registry")
        values.append(item)
    values.sort(key=lambda item: item["store_order"])
    if len({v["store_order"] for v in values}) != len(values) or any(not precedes(a["available_at"], b["available_at"]) for a, b in zip(values, values[1:])):
        raise ValueError("ISSUER_PROFILE_ORDER_CONFLICT")
    return values


def as_of(issuer, cutoff, *, root=None, illustrative=False):
    values = receipts(root=root, illustrative=illustrative, cutoff=cutoff)
    if not values:
        return None
    latest = values[-1]
    if latest["method_hashes"] != methods():
        raise ValueError("ISSUER_PROFILE_METHOD_CHANGED")
    if not as_utc(latest["registry"]["reviewed_at"]) <= as_utc(cutoff) < as_utc(latest["registry"]["review_due_at"]):
        return None
    profile = next((p for p in latest["registry"]["profiles"] if p["cik"] == str(issuer).zfill(10)), None)
    if profile is None:
        return None
    snapshot = sec_refresh.as_of(latest["source_store"], profile["cik"], cutoff)
    checked = identity(snapshot, profile, illustrative)
    if checked not in latest["identities"]:
        raise ValueError("ISSUER_PROFILE_IDENTITY_CHANGED")
    return {**profile, "binding": {**{k: latest[k] for k in ("receipt_id", "receipt_sha256", "available_at")},
        "cik": profile["cik"], "symbol": profile["symbol"], "profile_sha256": sec_refresh.canonical_hash(profile)}, "reviewer_kind": latest["registry"]["reviewer_kind"]}
