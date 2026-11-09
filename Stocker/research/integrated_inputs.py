"""Immutable local price/proxy input copies with actual first-commit availability."""

from pathlib import Path
import hashlib

import jsonschema

from . import fetch_context, sec_refresh
from .advance import verify_saved_raw
from .local_files import atomic_json, exclusive_lock
from .news_review_store import precedes
from .rights import as_utc, price_uses, require_entitlement, RightsError
from .sec_report import ROOT, SYNTHETIC_LABEL
from .sec_transport import now
from .swing_models import load_diagnostic_snapshot
from .validation import digest


FILES = ["research/integrated_inputs.py", "config/integrated_report_policy.json", "schemas/integrated_report.schema.json",
    "research/advance.py", "research/fetch_prices.py", "research/fetch_context.py", "research/swing_models.py", "research/rights.py",
    "research/local_files.py", "research/validation.py", "research/news_review_store.py", "config/context_policy.json", "config/swing_candidate_policy.json"]
NAMES = ("prices.json", "price_entitlement.json", "raw_response.json")


def policy():
    value = sec_refresh.read(ROOT / "config/integrated_report_policy.json")
    if any(value[k] is not False for k in ("model_training_enabled", "release_enabled", "recommendation_score_enabled", "news_score_enabled", "commodity_price_inference_enabled")) or any(value[k] != 0 for k in ("monthly_data_budget_usd", "network_requests", "external_llm_calls")) or value["price_engine"] != "fixed_ewma_simulation":
        raise ValueError("INTEGRATED_RESEARCH_BOUNDARY_CHANGED")
    return value


def methods():
    return {name: digest(ROOT / name) for name in FILES}


def validate(value, kind):
    schema = sec_refresh.read(ROOT / "schemas/integrated_report.schema.json")
    jsonschema.Draft202012Validator({"$ref": "#/$defs/" + kind, "$defs": schema["$defs"]}, format_checker=jsonschema.FormatChecker()).validate(value)


def namespace(root=None, illustrative=False):
    folder = Path(root or ROOT / "reports/private/integrated_inputs").resolve()
    if not illustrative and not folder.is_relative_to((ROOT / "reports/private").resolve()):
        raise ValueError("PRIVATE_INTEGRATED_INPUT_STORE_REQUIRED")
    return folder / ("illustrative" if illustrative else "real")


def profile(issuer, symbol, illustrative=False):
    value = policy()["illustrative_price_adapters" if illustrative else "price_adapters"].get(symbol)
    return value if value and value["cik"] == str(issuer).zfill(10) else None


def verify(prices, entitlement, *, role, issuer, symbol, cutoff, illustrative=False):
    # Check permission before reading any prices or raw payload.
    record = sec_refresh.read(entitlement)
    require_entitlement(record, price_uses("local_research"), cutoff, synthetic=illustrative)
    data = sec_refresh.read(prices)
    selected = profile(issuer, symbol, illustrative)
    expected = symbol if role == "stock" else selected.get(role) if selected else None
    if not selected or data.get("symbol") != expected or data.get("is_illustrative") is not illustrative:
        raise ValueError("INPUT_IDENTITY_OR_CLASSIFICATION_MISMATCH")
    kind, exchange = ("equity", "XNYS") if role == "stock" else ("etf", "ARCX")
    if data.get("instrument_type", "equity" if role == "stock" else None) != kind or data.get("listing_exchange", "XNYS" if role == "stock" else None) != exchange or as_utc(data["retrieved_at"]) > as_utc(cutoff):
        raise ValueError("INPUT_CLASS_OR_RETRIEVAL_TIME_INVALID")
    if role == "stock" and data.get("cik") != selected["cik"]:
        raise ValueError("INPUT_CIK_MISMATCH")
    if data.get("quote_kind") != "eod" or (role != "stock" and data.get("leveraged_or_inverse") is not False):
        raise ValueError("INPUT_QUOTE_OR_PROXY_STRUCTURE_UNSUPPORTED")
    raw = Path(prices).with_name("raw_response.json")
    if digest(raw) != data["raw_sha256"]:
        raise ValueError("INPUT_RAW_CHANGED")
    if illustrative:
        fixture = sec_refresh.read(raw)
        if fixture.get("label") != SYNTHETIC_LABEL or fixture.get("snapshot") != {k: v for k, v in data.items() if k != "raw_sha256"}:
            raise ValueError("ILLUSTRATIVE_RAW_MISMATCH")
    else:
        if record["provider"] != "massive" or record["plan"] != "stocks_basic":
            raise ValueError("UNSUPPORTED_REAL_PRICE_ROUTE")
        (verify_saved_raw if role == "stock" else fetch_context.verify)(prices, record)
    load_diagnostic_snapshot(Path(prices), record, cutoff)
    return data


def discovered_inputs():
    """Choose filenames only; permissions precede parsing their price contents."""
    from .learned_research import latest_training_snapshot
    result = {}
    try:
        result["stock"] = latest_training_snapshot()
    except ValueError:
        pass
    for role, ticker in (("market", "SPY"), ("sector", "PBW")):
        try:
            result[role] = fetch_context.latest(ticker)
        except ValueError:
            pass
    return result


def receipts(issuer, *, root=None, illustrative=False, cutoff=None):
    folder = namespace(root, illustrative) / str(issuer).zfill(10)
    result = []
    for path in (folder / "receipts").glob("*.json"):
        receipt = sec_refresh.read(path)
        if cutoff is not None and not precedes(receipt["available_at"], cutoff):
            continue
        packet_folder = folder / "packets" / receipt["packet_id"]
        if packet_folder.parent != folder / "packets" or digest(packet_folder / "packet.json") != receipt["packet_sha256"]:
            raise ValueError("INTEGRATED_INPUT_PACKET_CHANGED")
        packet = sec_refresh.read(packet_folder / "packet.json")
        validate(packet, "packet")
        if set(packet["roles"]) != set(policy()["roles"]):
            raise ValueError("INTEGRATED_INPUT_ROLES_CHANGED")
        if any(packet[k] != receipt[k] for k in ("packet_id", "available_at", "sequence")) or packet["cik"] != str(issuer).zfill(10) or packet["is_illustrative"] is not illustrative:
            raise ValueError("INTEGRATED_INPUT_RECEIPT_CHANGED")
        content = {k: packet[k] for k in ("cik", "symbol", "is_illustrative", "method_hashes", "roles")}
        if sec_refresh.canonical_hash(content) != packet["content_sha256"] or packet["packet_id"] != f"{packet['sequence']:08d}-" + packet["content_sha256"][:24]:
            raise ValueError("INTEGRATED_INPUT_CONTENT_CHANGED")
        for role, item in packet["roles"].items():
            if item["status"] != "captured":
                continue
            if set(item["files"]) != set(NAMES):
                raise ValueError("INTEGRATED_INPUT_FILES_CHANGED")
            for name, sha in item["files"].items():
                if digest(packet_folder / role / name) != sha:
                    raise ValueError("INTEGRATED_INPUT_FILE_CHANGED")
        result.append({**packet, "packet_sha256": receipt["packet_sha256"], "folder": str(packet_folder)})
    result.sort(key=lambda p: p["sequence"])
    if len({p["sequence"] for p in result}) != len(result) or any(not precedes(a["available_at"], b["available_at"]) for a, b in zip(result, result[1:])):
        raise ValueError("INTEGRATED_INPUT_ORDER_CONFLICT")
    return result


def as_of(issuer, cutoff, *, root=None, illustrative=False):
    values = receipts(issuer, root=root, illustrative=illustrative, cutoff=cutoff)
    return values[-1] if values else None


def capture(issuer, symbol, selected, *, root=None, illustrative=False, clock=now):
    if not profile(issuer, symbol, illustrative):
        raise ValueError("NO_REVIEWED_ISSUER_PRICE_ADAPTER")
    began = clock()
    roles, blobs = {}, {}
    for role in policy()["roles"]:
        pair = selected.get(role)
        roles[role] = {"status": "unavailable", "reason_code": "INPUT_NOT_SELECTED"}
        if not pair:
            continue
        prices, entitlement = map(Path, pair)
        try:
            verify(prices, entitlement, role=role, issuer=issuer, symbol=symbol, cutoff=began, illustrative=illustrative)
            paths = {"prices.json": prices, "price_entitlement.json": entitlement, "raw_response.json": prices.with_name("raw_response.json")}
            blobs[role] = {name: path.read_bytes() for name, path in paths.items()}
            hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in blobs[role].items()}
            # Verify the captured bytes again below, closing source-file change races.
            roles[role] = {"status": "captured", "files": hashes}
        except RightsError:
            roles[role]["reason_code"] = "PRICE_RIGHTS_NOT_CLEARED"
        except (ValueError, OSError, KeyError, TypeError):
            roles[role]["reason_code"] = "PRICE_SOURCE_VALIDATION_FAILED"
    content = {"cik": str(issuer).zfill(10), "symbol": symbol, "is_illustrative": illustrative, "method_hashes": methods(), "roles": roles}
    content_sha = sec_refresh.canonical_hash(content)
    folder = namespace(root, illustrative) / content["cik"]
    with exclusive_lock(folder / "write.lock"):
        previous = receipts(issuer, root=root, illustrative=illustrative)
        if previous and previous[-1]["content_sha256"] == content_sha:
            return {"packet_id": previous[-1]["packet_id"], "available_at": previous[-1]["available_at"], "status": "unchanged"}
        sequence = previous[-1]["sequence"] + 1 if previous else 1
        packet_id = f"{sequence:08d}-" + content_sha[:24]
        packet_folder = folder / "packets" / packet_id
        for role, files in blobs.items():
            for name, raw in files.items():
                target = packet_folder / role / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
            verify(packet_folder / role / "prices.json", packet_folder / role / "price_entitlement.json", role=role, issuer=issuer, symbol=symbol, cutoff=began, illustrative=illustrative)
        at = clock()
        if as_utc(at) < as_utc(began) or (previous and not precedes(previous[-1]["available_at"], at)):
            raise ValueError("INTEGRATED_INPUT_CLOCK_NOT_ADVANCED")
        packet = {"version": "1.0.0", **content, "packet_id": packet_id, "sequence": sequence, "content_sha256": content_sha, "available_at": at}
        validate(packet, "packet")
        atomic_json(packet_folder / "packet.json", packet)
        receipt = {"packet_id": packet_id, "packet_sha256": digest(packet_folder / "packet.json"), "available_at": at, "sequence": sequence}
        atomic_json(folder / "receipts" / (packet_id + ".json"), receipt)
    return {"packet_id": packet_id, "available_at": at, "status": "captured"}
