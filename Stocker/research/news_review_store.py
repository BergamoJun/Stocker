"""Append-only human reviews bound to immutable news metadata incarnations."""

from datetime import datetime, timezone
import json
from pathlib import Path

import jsonschema

from . import event_review, news_archive
from .local_files import atomic_json, exclusive_lock
from .news import canonical_url
from .rights import as_utc
from .sec_report import ROOT, SYNTHETIC_LABEL
from .validation import digest, stable_hash

LEGACY_FILES = ["research/event_review.py", "research/news.py", "config/news_review_policy.json",
                "config/swing_candidate_policy.json", "schemas/news_event_review.schema.json"]


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def precedes(available, cutoff):
    # Older stores use second-resolution clocks. Cross-store order within the
    # same second is deliberately not inferred from a newer clock's precision.
    return as_utc(available).replace(microsecond=0) < as_utc(cutoff).replace(microsecond=0)


def policy():
    value = read(ROOT / "config/news_connection_policy.json")
    if value["monthly_data_budget_usd"] != 0 or value["release_enabled"] is not False or value["model_training_enabled"] is not False or value["numeric_news_score_enabled"] is not False or value["publisher_body_fetch_enabled"] is not False or value["network_requests"] != 0 or value["external_llm_calls"] != 0:
        raise ValueError("NEWS_CONNECTION_RESEARCH_BOUNDARY_CHANGED")
    return value


def validate(value, kind):
    schema = read(ROOT / "schemas/news_connections.schema.json")
    jsonschema.Draft202012Validator({"$ref": "#/$defs/" + kind, "$defs": schema["$defs"]}, format_checker=jsonschema.FormatChecker()).validate(value)


def namespace(root, illustrative):
    root = Path(root or ROOT / "reports/private/news_connections").resolve()
    if not root.is_relative_to(ROOT / "reports/private"):
        raise ValueError("PRIVATE_NEWS_CONNECTION_STORE_REQUIRED")
    return root / ("illustrative" if illustrative else "real")


def metadata(article):
    result = {key: article.get(key) for key in news_archive.policy()["content_version_fields"]}
    result["url"] = canonical_url(article["url"])
    return result


def identity(article):
    meta = metadata(article)
    return stable_hash(meta["url"])[:24], stable_hash(meta)


def archive_state(cutoff, *, archive_root=None, illustrative=False):
    """Keep an era ID so reverting to an older title does not revive its review."""
    view = news_archive.as_of_view(cutoff, root=archive_root, illustrative=illustrative)
    folder = news_archive.archive_folder(archive_root or ROOT / "reports/private/news_archive", illustrative)
    packets = []
    for path in (folder / "captures").glob("*.json"):
        value = read(path)
        if as_utc(value["available_at"]) <= as_utc(cutoff):
            # as_of_view already checks the source bytes, but recheck the packet
            # read for era indexing rather than trusting a concurrent edit.
            news_archive.validate(value, "capture")
            if stable_hash(value["news_packet"]) != value["news_packet_sha256"] or path.stem != value["news_packet_sha256"] or value["is_illustrative"] is not illustrative:
                raise ValueError("ARCHIVE_PACKET_CHANGED")
            packets.append(value)
    eras = {}
    for packet in sorted(packets, key=lambda value: (as_utc(value["available_at"]), value["capture_order"])):
        for article in packet["articles"]:
            key, content = article["article_key"], article["content_sha256"]
            if key not in eras or eras[key]["content_sha256"] != content:
                eras[key] = {"content_sha256": content,
                    "incarnation_id": stable_hash([key, content, packet["news_packet_sha256"], packet["capture_order"]]),
                    "incarnation_available_at": packet["available_at"]}
    current = {article["article_key"]: {**article, **eras[article["article_key"]]} for article in view["articles"]}
    if any(stable_hash(article["metadata"]) != article["content_sha256"] for article in current.values()):
        raise ValueError("ARCHIVE_VERSION_CHANGED_DURING_READ")
    return {**view, "by_article_key": current, "source_captures": [{"path": str((folder / "captures" / (packet["news_packet_sha256"] + ".json")).resolve()),
        "sha256": digest(folder / "captures" / (packet["news_packet_sha256"] + ".json")), "available_at": packet["available_at"]} for packet in packets]}


def receipt_id(value):
    return stable_hash({key: value[key] for key in ("symbol", "is_illustrative", "news_context_sha256", "manifest", "bindings", "connection_policy_sha256")})


def check_receipt(value, path=None):
    validate(value, "receipt")
    if stable_hash({key: item for key, item in value.items() if key != "receipt_sha256"}) != value["receipt_sha256"] or receipt_id(value) != value["receipt_id"]:
        raise ValueError("STORED_REVIEW_INTEGRITY_CHANGED")
    if path is not None and Path(path).stem != value["receipt_id"]:
        raise ValueError("REVIEW_FILENAME_IDENTITY_CHANGED")
    if stable_hash(value["news_packet"]) != value["news_context_sha256"] or value["manifest"]["news_context_sha256"] != value["news_context_sha256"]:
        raise ValueError("REVIEW_SOURCE_PACKET_CHANGED")
    event_review.validate_manifest(value["manifest"])
    if as_utc(value["manifest"]["submitted_at"]) > as_utc(value["available_at"]):
        raise ValueError("REVIEW_RECEIPT_PRECEDES_SUBMISSION")
    if value["legacy_sha256"] != {name: digest(ROOT / name) for name in LEGACY_FILES}:
        raise ValueError("REVIEW_VALIDATION_METHOD_CHANGED")
    if value["connection_policy_sha256"] != digest(ROOT / "config/news_connection_policy.json"):
        raise ValueError("REVIEW_CONNECTION_POLICY_CHANGED")
    return value


def reviews_as_of(cutoff, *, root=None, illustrative=False):
    folder = namespace(root, illustrative) / "reviews"
    result = []
    for path in folder.glob("*.json"):
        value = read(path)
        if as_utc(value["available_at"]) > as_utc(cutoff):
            continue
        check_receipt(value, path)
        if value["is_illustrative"] is not illustrative:
            raise ValueError("REAL_AND_ILLUSTRATIVE_REVIEWS_MIXED")
        result.append(value)
    return sorted(result, key=lambda value: (as_utc(value["available_at"]), value["store_order"]))


def store(context_path, manifest_path, *, symbol="BE", root=None, archive_root=None, illustrative=False, clock=now):
    settings = policy()
    if symbol not in settings["allowed_symbols"]:
        raise ValueError("UNSUPPORTED_NEWS_REVIEW_SYMBOL")
    at = clock()
    if not illustrative and not Path(context_path).resolve().is_relative_to(ROOT / "reports/private"):
        raise ValueError("PRIVATE_CONTEXT_REQUIRED")
    context = read(context_path)
    if context.get("is_illustrative") is not illustrative or as_utc(context["as_of"]) > as_utc(at):
        raise ValueError("CONTEXT_CLASSIFICATION_OR_TIME_INVALID")
    if not illustrative:
        news_archive.require_metadata_rights({provider for article in context["news"]["articles"] for provider in article["providers"]}, at)
    # Importing an old review must never re-archive an old packet as a new
    # apparent article reversion. Capture is a separate, explicit operation.
    archive_folder = news_archive.archive_folder(archive_root or ROOT / "reports/private/news_archive", illustrative)
    capture_path = archive_folder / "captures" / (stable_hash(context["news"]) + ".json")
    if not capture_path.exists() or as_utc(read(capture_path)["available_at"]) > as_utc(at):
        raise ValueError("ARCHIVE_CONTEXT_BEFORE_IMPORTING_REVIEW")
    manifest = read(manifest_path)
    event_review.validate_manifest(manifest)
    if manifest["template_only"] or not manifest["events"] or not manifest["reviewer_id"] or manifest["submitted_at"] is None:
        raise ValueError("DRAFT_OR_EMPTY_REVIEW_CANNOT_BE_COMMITTED")
    assessed = event_review.assess(context["news"], manifest, at)
    if assessed["invalid_review_count"]:
        raise ValueError("REVIEW_EVIDENCE_OR_TIME_INVALID")
    if len({event["event_id"] for event in manifest["events"]}) != len(manifest["events"]):
        raise ValueError("ONE_ASSERTION_PER_EVENT_PER_MANIFEST_REQUIRED")
    state = archive_state(at, archive_root=archive_root, illustrative=illustrative)
    articles = {article["id"]: article for article in context["news"]["articles"]}
    bindings = {}
    for event in manifest["events"]:
        for field in ("rationale", "five_session_relevance_explanation"):
            if not event[field].strip():
                raise ValueError("REVIEW_EXPLANATION_IS_BLANK")
        if event.get("novelty") == "material_update":
            update = event.get("material_update_at")
            if event.get("material_change_reviewed") is not True or not update or not as_utc(event["first_public_at"]) <= as_utc(update) <= as_utc(event["reviewed_at"]):
                raise ValueError("MATERIAL_UPDATE_NOT_VERIFIED")
        for source_id in event["source_ids"]:
            article = articles[source_id]
            key, content = identity(article)
            current = state["by_article_key"].get(key)
            if current is None or current["content_sha256"] != content:
                raise ValueError("REVIEW_ARTICLE_VERSION_IS_NOT_CURRENT")
            check = next(item for item in event["original_checks"] if item["source_id"] == source_id)
            if not precedes(current["incarnation_available_at"], check["checked_at"]):
                raise ValueError("REVIEW_CHECK_PRECEDES_CURRENT_VERSION")
            bindings[source_id] = {"article_key": key, "content_sha256": content, "incarnation_id": current["incarnation_id"],
                "incarnation_available_at": current["incarnation_available_at"], "url": metadata(article)["url"]}
    value = {"version": "1.0.0", "policy_id": settings["id"], "symbol": symbol, "is_illustrative": illustrative,
        "data_label": SYNTHETIC_LABEL if illustrative else None,
        "available_at": at, "store_order": 0, "news_context_sha256": stable_hash(context["news"]),
        "news_packet": context["news"], "manifest": manifest, "bindings": bindings,
        "source_context_path": str(Path(context_path).resolve()), "source_context_sha256": digest(context_path),
        "source_review_path": str(Path(manifest_path).resolve()), "source_review_sha256": digest(manifest_path),
        "archive_capture_path": str(capture_path.resolve()), "archive_capture_sha256": digest(capture_path),
        "legacy_sha256": {name: digest(ROOT / name) for name in LEGACY_FILES},
        "connection_policy_sha256": digest(ROOT / "config/news_connection_policy.json"),
        "legacy_validation_reason_codes": assessed["reason_codes"], "original_text_assertions_independently_verified": False,
        "released_score": None, "experimental_news_score": None, "model_training_performed": False,
        "network_requests": 0, "external_llm_calls": 0}
    value["receipt_id"] = receipt_id(value)
    folder = namespace(root, illustrative)
    path = folder / "reviews" / (value["receipt_id"] + ".json")
    with exclusive_lock(folder / "review_writer.lock"):
        if path.exists():
            previous = check_receipt(read(path), path)
            return {"status": "already_stored", "path": str(path.resolve()), "receipt_id": previous["receipt_id"], "available_at": previous["available_at"]}
        existing = reviews_as_of(at, root=root, illustrative=illustrative)
        # Check all receipt timestamps for a backwards clock, including ones
        # excluded by the historical query above.
        all_times = [as_utc(read(item)["available_at"]) for item in (folder / "reviews").glob("*.json")]
        if all_times and max(all_times) > as_utc(at):
            raise ValueError("REVIEW_STORE_CLOCK_MOVED_BACKWARDS")
        value["store_order"] = max((item["store_order"] for item in existing), default=0) + 1
        committed_at = clock()
        if as_utc(committed_at) < as_utc(at):
            raise ValueError("REVIEW_STORE_CLOCK_MOVED_BACKWARDS")
        value["available_at"] = committed_at
        value["receipt_sha256"] = stable_hash(value)
        validate(value, "receipt")
        atomic_json(path, value)
    return {"status": "stored", "path": str(path.resolve()), "receipt_id": value["receipt_id"], "available_at": value["available_at"]}
