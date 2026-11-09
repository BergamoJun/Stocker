"""Generic SEC-bound equities and reviewed ETF EOD inputs, local use only."""

import argparse
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
import os
from pathlib import Path
import uuid
from zoneinfo import ZoneInfo
import jsonschema

from research import fetch_prices, panel_prices
from research.local_files import atomic_json
from research.panel_transport import Deferred, SourceUnavailable, detail_url, price_url
from research.project_compatibility import check as historical_check
from research.rights import as_utc, require_entitlement, price_uses
from research.sec_report import ROOT
from research.sec_transport import now
from research.swing_models import XnysCalendar
from research.validation import digest, stable_hash
from . import scope
from .market_client import Client

BASE = Path(__file__).resolve().parent
FILES = ["extensions/market_inputs.py", "extensions/market_client.py", "extensions/market_policy.json", "extensions/market_inputs.schema.json", "research/panel_prices.py", "research/panel_transport.py", "research/fetch_prices.py", "config/issuer_profiles.json"]


def read(path): return json.loads(Path(path).read_text(encoding="utf-8"))
def methods(): return {p: digest(ROOT / p) for p in FILES}


def policy():
    value = read(BASE / "market_policy.json")
    if value["monthly_data_budget_usd"] != 0 or value["external_model_input_allowed"] or value["model_training_enabled"]:
        raise ValueError("MARKET_SCOPE_CHANGED")
    registered = {p["cik"]: p for p in read(ROOT / "config/issuer_profiles.json")["profiles"]}
    if any(cik not in registered for cik in value["issuers"]):
        raise ValueError("MARKET_ISSUER_NOT_REGISTERED")
    for cik, profile in value["issuers"].items():
        original = registered[cik]
        if (original["symbol"], original["market_proxy"]["symbol"], original["sector_proxy"]["symbol"]) != (profile["symbol"], profile["market"], profile["sector"]):
            raise ValueError("MARKET_ROLE_MAPPING_CONFLICT")
    return value


def identity(raw, ticker, settings):
    data = json.loads(raw)
    result = data.get("results", {})
    stock = next(((cik, p) for cik, p in settings["issuers"].items() if p["symbol"] == ticker), None)
    if data.get("status") != "OK" or result.get("ticker") != ticker or result.get("active") is not True or result.get("market") != "stocks" or result.get("locale") != "us" or str(result.get("currency_name", "")).lower() != "usd":
        raise ValueError("MARKET_REFERENCE_IDENTITY_INVALID")
    kind = "equity" if stock else "etf"
    if stock:
        cik, _ = stock
        if result.get("type") != "CS" or str(result.get("cik", "")).zfill(10) != cik or not result.get("share_class_figi") or result.get("primary_exchange") not in {"XNYS", "XNAS", "XASE", "ARCX"}:
            raise ValueError("STOCK_CIK_OR_SHARE_CLASS_CONFLICT")
    else:
        fund = settings["funds"].get(ticker)
        if not fund or fund["leveraged_or_inverse"] or result.get("type") != "ETF" or result.get("primary_exchange") != fund["listing_exchange"] or not (result.get("share_class_figi") or result.get("composite_figi")):
            raise ValueError("UNAPPROVED_FUND_IDENTITY_OR_STRUCTURE")
        cik = None
    return {"symbol": ticker, "instrument_type": kind, "type": result["type"], "cik": cik, "primary_exchange": result["primary_exchange"],
        "share_class_figi": result.get("share_class_figi"), "composite_figi": result.get("composite_figi"), "leveraged_or_inverse": False if kind == "etf" else None}


def normalize(raw, profile, record, evidence):
    require_entitlement(record, price_uses("local_research"), evidence["retrieved_at"], synthetic=record["is_illustrative"])
    if profile["instrument_type"] == "equity":
        return panel_prices.normalize(raw, profile, record, evidence)
    start, end = panel_prices.window_from_url(evidence["source_url"], profile["symbol"])
    if hashlib.sha256(raw).hexdigest() != evidence["raw_sha256"]:
        raise ValueError("FUND_SOURCE_HASH_CHANGED")
    calendar = XnysCalendar((date.fromisoformat(start) - timedelta(days=30)).isoformat(), (date.fromisoformat(end) + timedelta(days=40)).isoformat())
    def invalid(_): raise ValueError("NONFINITE_FUND_NUMBER")
    payload = json.loads(raw, parse_float=Decimal, parse_constant=invalid)
    if payload.get("status") not in {"OK", "DELAYED"} or payload.get("ticker") != profile["symbol"] or payload.get("adjusted") is not True or payload.get("next_url"):
        raise ValueError("FUND_PRICE_IDENTITY_OR_PARTIAL_RESPONSE")
    rows = payload.get("results", [])
    if not isinstance(rows, list) or not 1 <= len(rows) <= 550:
        raise ValueError("FUND_PRICE_HISTORY_MISSING")
    bars = []
    for row in rows:
        if type(row["t"]) is not int: raise ValueError("INVALID_FUND_TIMESTAMP")
        instant = datetime.fromtimestamp(row["t"] / 1000, timezone.utc)
        local = instant.astimezone(ZoneInfo("America/New_York"))
        session = local.date().isoformat()
        if local.time().isoformat() != "00:00:00" or not start <= session <= end:
            raise ValueError("INVALID_FUND_DAILY_START")
        opening, high, low, close, volume = [Decimal(str(row[k])) for k in ("o", "h", "l", "c", "v")]
        if not all(n.is_finite() for n in (opening, high, low, close, volume)) or min(opening, high, low, close) <= 0 or volume < 0 or not low <= min(opening, close) <= max(opening, close) <= high:
            raise ValueError("INVALID_FUND_OHLCV")
        ended = calendar.close(session)
        if as_utc(ended) > as_utc(evidence["retrieved_at"]): raise ValueError("INCOMPLETE_FUND_BAR")
        bars.append({"session": session, "open": str(opening), "high": str(high), "low": str(low), "close": str(close), "volume": str(volume),
            "available_at": evidence["retrieved_at"], "period_ended_at": ended, "provider_bar_start_at": instant.isoformat(), "published_at": None})
    if [b["session"] for b in bars] != sorted({b["session"] for b in bars}): raise ValueError("DUPLICATE_OR_UNSORTED_FUND_SESSIONS")
    return {"schema_version": "1.1.0", "symbol": profile["symbol"], "instrument_type": "etf", "currency": "USD", "quote_kind": "eod",
        "listing_exchange": profile["primary_exchange"], "exchange": "XNYS", "adjustment": "split_adjusted_current_share_basis", "dividend_adjusted": False,
        "point_in_time_verified": False, "retrieved_at": evidence["retrieved_at"], "source_url": evidence["source_url"], "raw_sha256": evidence["raw_sha256"],
        "is_illustrative": record["is_illustrative"], "volume_representation": "exact_decimal_string_shares", "bars": bars}


def latest_completed(cutoff):
    at = as_utc(cutoff)
    calendar = XnysCalendar((at.date() - timedelta(days=45)).isoformat(), (at.date() + timedelta(days=2)).isoformat())
    sessions = calendar.sessions((at.date() - timedelta(days=30)).isoformat(), at.astimezone(ZoneInfo("America/New_York")).date().isoformat())
    return next(s for s in reversed(sessions) if as_utc(calendar.close(s)) <= at)


def returns(data, count):
    bars = data["bars"]
    if len(bars) <= count: return None
    selected = bars[-count-1:]
    calendar = XnysCalendar((date.fromisoformat(selected[0]["session"]) - timedelta(days=10)).isoformat(), (date.fromisoformat(selected[-1]["session"]) + timedelta(days=10)).isoformat())
    if [b["session"] for b in selected] != calendar.sessions(selected[0]["session"], selected[-1]["session"]): return None
    with localcontext() as context:
        context.prec = 60
        value = (Decimal(selected[-1]["close"]) / Decimal(selected[0]["close"]) - 1) * 100
        return {"value_percent": format(value.quantize(Decimal("0.000001")), "f"), "start": selected[0]["session"], "end": selected[-1]["session"]}


def collect(issuers, baseline, *, client, output=None, commit=True, clock=now):
    settings = policy(); proof = scope.check(baseline)
    selected = {str(c).zfill(10): settings["issuers"][str(c).zfill(10)] for c in issuers}
    symbols = sorted({p[role] for p in selected.values() for role in ("symbol", "market", "sector")})
    start, end, _ = fetch_prices.basic_window(clock(), settings["lookback_days"])
    initial, instruments, blobs = client.network_requests, {}, {}
    for ticker in symbols:
        scope.check(baseline, expected_sha256=proof["baseline_sha256"])
        try:
            reference = client.get(detail_url(ticker, end))
            profile = identity(reference["raw"], ticker, settings)
            evidence = client.get(price_url(ticker, start, end))
            data = normalize(evidence["raw"], profile, client.record, evidence)
            instruments[ticker] = {"symbol": ticker, "status": "captured", "profile": profile,
                "identity_evidence": {k: reference[k] for k in ("source_url", "retrieved_at", "raw_sha256")},
                "daily_evidence": {k: evidence[k] for k in ("source_url", "retrieved_at", "raw_sha256")}, "data": data}
            blobs[ticker + ".identity.json"] = reference["raw"]; blobs[ticker + ".daily.json"] = evidence["raw"]
        except (ValueError, OSError, KeyError, TypeError, AttributeError, InvalidOperation, OverflowError, Deferred, SourceUnavailable) as error:
            reason = error.code if isinstance(error, SourceUnavailable) else "REQUEST_DEFERRED" if isinstance(error, Deferred) else str(error)
            instruments[ticker] = {"symbol": ticker, "status": "withheld", "reason": reason if reason.isascii() and reason.replace("_", "").isalnum() else "SOURCE_VALIDATION_FAILED"}
    result = {"instruments": instruments, "issuers": selected, "requests": client.network_requests - initial, "source_window": {"start": start, "end": end}, "is_illustrative": client.record["is_illustrative"]}
    if not commit: return result, None
    destination = Path(output or ROOT / "reports/private/market_inputs").resolve()
    if not client.record["is_illustrative"] and not destination.is_relative_to((ROOT / "reports/private").resolve()): raise ValueError("PRIVATE_MARKET_STORE_REQUIRED")
    folder = destination / ("illustrative" if client.record["is_illustrative"] else "real") / ("market_" + uuid.uuid4().hex)
    folder.mkdir(parents=True, exist_ok=False)
    files = {}
    for name, raw in blobs.items():
        path = folder / "sources" / name; path.parent.mkdir(exist_ok=True); path.write_bytes(raw)
        files["sources/" + name] = hashlib.sha256(raw).hexdigest()
    value = {"contract": "multi-issuer-eod-source-v1", "record_id": folder.name, "available_at": clock(), **result, "entitlement": client.record,
        "method_hashes": methods(), "files": files, "baseline_path": str(Path(baseline).resolve()), "baseline_sha256": proof["baseline_sha256"],
        "external_model_input_allowed": False, "released_score": None, "price_plan": None}
    value["record_sha256"] = stable_hash(value)
    scope.check(baseline, expected_sha256=proof["baseline_sha256"])
    atomic_json(folder / "record.json", value)
    return value, folder


def record(path):
    path = Path(path); path = path / "record.json" if path.is_dir() else path
    value = read(path)
    jsonschema.Draft202012Validator(read(BASE / "market_inputs.schema.json"), format_checker=jsonschema.FormatChecker()).validate(value)
    if value["method_hashes"] != methods() or path.parent.name != value["record_id"] or stable_hash({k: v for k, v in value.items() if k != "record_sha256"}) != value["record_sha256"]:
        raise ValueError("MARKET_INPUT_RECORD_CHANGED")
    historical_check(value["baseline_path"], expected_sha256=value["baseline_sha256"])
    for name, sha in value["files"].items():
        target = (path.parent / name).resolve()
        if not target.is_relative_to(path.parent.resolve()) or digest(target) != sha: raise ValueError("MARKET_INPUT_SOURCE_CHANGED")
    return path.parent, value


def view(path, cutoff, *, cik, illustrative=False):
    folder, value = record(path)
    require_entitlement(value["entitlement"], price_uses("local_research"), cutoff, synthetic=illustrative)
    if value["is_illustrative"] is not illustrative or as_utc(value["available_at"]) >= as_utc(cutoff).replace(microsecond=0): raise ValueError("MARKET_INPUT_NOT_AVAILABLE_AT_CUTOFF")
    cik = str(cik).zfill(10)
    selected = policy()["issuers"][cik]
    if value["issuers"].get(cik) != selected: raise ValueError("MARKET_ISSUER_ROLE_MAPPING_CHANGED")
    expected = latest_completed(cutoff)
    roles = {}
    for role, ticker in (("stock", selected["symbol"]), ("market", selected["market"]), ("sector", selected["sector"])):
        item = value["instruments"].get(ticker, {"status": "withheld", "reason": "NOT_COLLECTED"})
        if item["status"] != "captured":
            roles[role] = {"symbol": ticker, "status": "unavailable", "reason": item["reason"]}; continue
        reference_raw = (folder / "sources" / (ticker + ".identity.json")).read_bytes()
        if hashlib.sha256(reference_raw).hexdigest() != item["identity_evidence"]["raw_sha256"] or item["identity_evidence"]["source_url"] != detail_url(ticker, value["source_window"]["end"]):
            raise ValueError("MARKET_REFERENCE_PROVENANCE_CHANGED")
        profile = identity(reference_raw, ticker, policy())
        data = normalize((folder / "sources" / (ticker + ".daily.json")).read_bytes(), profile, value["entitlement"], item["daily_evidence"])
        if data != item["data"] or profile != item["profile"]: raise ValueError("MARKET_NORMALIZATION_REPLAY_CHANGED")
        if any(as_utc(item[k]["retrieved_at"]) > as_utc(value["available_at"]) for k in ("identity_evidence", "daily_evidence")):
            raise ValueError("MARKET_CAPTURE_BACKDATED")
        last = data["bars"][-1]
        status = "available" if last["session"] == expected else "stale"
        roles[role] = {"symbol": ticker, "status": status, "instrument_type": profile["instrument_type"], "listing_exchange": profile["primary_exchange"],
            "quote": {"session": last["session"], "close": last["close"], "currency": "USD", "kind": "eod", "retrieved_at": data["retrieved_at"]},
            "returns": {str(n): returns(data, n) if status == "available" else None for n in policy()["return_sessions"]}, "history": data,
            "source_ids": [value["record_id"] + ":" + ticker], "point_in_time_verified": False}
    comparisons = []
    for role in ("market", "sector"):
        if roles["stock"]["status"] != "available" or roles[role]["status"] != "available": continue
        for count in policy()["return_sessions"]:
            a, b = roles["stock"]["returns"][str(count)], roles[role]["returns"][str(count)]
            if not a or not b or (a["start"], a["end"]) != (b["start"], b["end"]): continue
            comparisons.append({"stock": selected["symbol"], "proxy": selected[role], "sessions": count, "start": a["start"], "end": a["end"],
                "difference_percentage_points": format(Decimal(a["value_percent"]) - Decimal(b["value_percent"]), "f")})
    return {"contract": "issuer-market-input-v1", "cik": cik, "symbol": selected["symbol"], "as_of": cutoff, "available_at": value["available_at"], "record_sha256": value["record_sha256"],
        "roles": roles, "comparisons": comparisons, "external_model_input_allowed": False, "price_plan": None, "released_score": None,
        "limitations": ["Latest completed EOD only; current-vintage historical bars are not point-in-time historical evidence", "Split adjusted, not dividend total returns; proxies are references, not the issuer itself"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path); parser.add_argument("--ciks", nargs="+", required=True); parser.add_argument("--probe", action="store_true")
    args = parser.parse_args(argv)
    from research.learned_research import latest_training_snapshot
    _, entitlement = latest_training_snapshot()
    client = Client(read(entitlement), os.environ.get("MASSIVE_API_KEY", ""), args.baseline)
    result, folder = collect(args.ciks, args.baseline, client=client, commit=not args.probe)
    print(json.dumps({"folder": str(folder) if folder else None, "requests": result["requests"], "instruments": [{"symbol": k, "status": v["status"], "reason": v.get("reason"), "bars": len(v.get("data", {}).get("bars", []))} for k, v in result["instruments"].items()]}))


if __name__ == "__main__": main()
