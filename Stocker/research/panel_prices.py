"""Generic common-stock adapter, separate from the pinned BE/ETF adapter.

Historical bars retain their actual download time. Replaying an earlier date is
a current-vintage diagnostic, never proof the same inputs were known then.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from .local_files import atomic_json
from .panel_transport import symbol, validate_url
from .rights import as_utc, price_uses, require_entitlement
from .swing_models import XnysCalendar, load_diagnostic_snapshot


def window_from_url(url, ticker):
    parsed = urlparse(validate_url(url))
    parts = parsed.path.split("/")
    if len(parts) != 10 or parts[4] != symbol(ticker) or parts[5:8] != ["range", "1", "day"]:
        raise ValueError("Price source does not identify the approved stock")
    start, end = parts[8:10]
    date.fromisoformat(start)
    date.fromisoformat(end)
    if start > end or parse_qs(parsed.query).get("adjusted") != ["true"]:
        raise ValueError("Invalid adjusted-price request window")
    return start, end


def normalize(raw, profile, record, evidence):
    require_entitlement(record, price_uses("local_research"), evidence["retrieved_at"], synthetic=record["is_illustrative"])
    ticker = symbol(profile["symbol"])
    if profile.get("instrument_type") != "equity" or profile.get("type") != "CS" or not profile.get("cik") or not profile.get("share_class_figi"):
        raise ValueError("Verified common-stock issuer and share-class identity required")
    if record["provider"] != "massive" or profile.get("primary_exchange") not in {"XNYS", "XNAS", "XASE", "ARCX"}:
        raise ValueError("Unsupported provider or listing exchange")
    start, end = window_from_url(evidence["source_url"], ticker)
    raw_hash = hashlib.sha256(raw).hexdigest()
    if raw_hash != evidence["raw_sha256"]:
        raise ValueError("Raw price hash changed")
    calendar = XnysCalendar((date.fromisoformat(start) - timedelta(days=30)).isoformat(),
                            (date.fromisoformat(end) + timedelta(days=40)).isoformat())
    def invalid_constant(_):
        raise ValueError("Nonfinite JSON number")
    try:
        payload = json.loads(raw, parse_float=Decimal, parse_constant=invalid_constant)
        if payload.get("status") not in {"OK", "DELAYED"} or payload.get("ticker") != ticker or payload.get("adjusted") is not True or payload.get("next_url"):
            raise ValueError("Unapproved identity, adjustment or partial response")
        results = payload.get("results")
        if not isinstance(results, list) or not 1 <= len(results) <= 550:
            raise ValueError("Missing or excessive daily results")
        bars = []
        for item in results:
            if type(item["t"]) is not int:
                raise ValueError("Integer bar-start timestamp required")
            instant = datetime.fromtimestamp(item["t"] / 1000, timezone.utc)
            local = instant.astimezone(ZoneInfo("America/New_York"))
            session = local.date().isoformat()
            if local.time().isoformat() != "00:00:00" or not start <= session <= end:
                raise ValueError("Unexpected daily timestamp")
            prices = [Decimal(str(item[key])) for key in ("o", "h", "l", "c")]
            volume = Decimal(str(item["v"]))
            if any(not number.is_finite() or number <= 0 for number in prices) or not volume.is_finite() or volume < 0:
                raise ValueError("Invalid price/volume")
            opening, high, low, close = prices
            if not low <= min(opening, close) <= max(opening, close) <= high:
                raise ValueError("Invalid OHLC envelope")
            ended = calendar.close(session)
            if as_utc(ended) > as_utc(evidence["retrieved_at"]):
                raise ValueError("Incomplete daily bar")
            bars.append({"session": session, **dict(zip(("open", "high", "low", "close"), map(str, prices))),
                "volume": str(volume), "available_at": evidence["retrieved_at"], "period_ended_at": ended,
                "provider_bar_start_at": instant.isoformat(), "published_at": None})
        if [bar["session"] for bar in bars] != sorted({bar["session"] for bar in bars}):
            raise ValueError("Duplicate or unsorted sessions")
    except (ValueError, TypeError, KeyError, AttributeError, InvalidOperation, OverflowError, OSError):
        raise ValueError("INVALID_PANEL_DAILY_RESPONSE") from None
    sid = "massive-panel-" + ticker + "-" + raw_hash[:20]
    return {"schema_version": "1.1.0", "snapshot_id": sid, "provider": "massive", "entitlement_id": record["id"],
        "is_illustrative": record["is_illustrative"], "symbol": ticker, "cik": profile["cik"],
        "share_class_figi": profile["share_class_figi"], "instrument_type": "equity", "currency": "USD",
        "exchange": "XNYS", "calendar_semantics": "US_regular_session_calendar_not_listing_MIC",
        "listing_exchange": profile["primary_exchange"], "quote_kind": "eod",
        "adjustment": "split_adjusted_current_share_basis", "dividend_adjusted": False, "point_in_time_verified": False,
        "volume_representation": "exact_decimal_string_shares", "retrieved_at": evidence["retrieved_at"],
        "source_ids": [sid], "source_url": evidence["source_url"], "raw_sha256": raw_hash,
        "availability_semantics": "Actual download; not historical publication or simulated decision time",
        "limitations": ["Current split-adjusted/corrected vintage; no certified historical availability",
            "No dividend, borrow, market-impact or portfolio-capital model; cash interest assumed zero",
            "Daily bars cannot resolve intraday target/stop order", "Personal local analysis; no external LLM input"],
        "bars": bars}


def save(folder, raw, profile, record, evidence):
    folder = Path(folder)
    result = normalize(raw, profile, record, evidence)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "prices.json"
    if target.exists():
        if json.loads(target.read_text(encoding="utf-8")) != result or (folder / "raw_response.json").read_bytes() != raw:
            raise ValueError("Immutable stock snapshot differs")
        return target
    (folder / "raw_response.json").write_bytes(raw)
    atomic_json(folder / "profile.json", profile) if not (folder / "profile.json").exists() else None
    atomic_json(folder / "prices.json", result)
    return target


def load(path, profile, record, as_of):
    # The rights gate intentionally precedes parsing any licensed price input.
    require_entitlement(record, price_uses("local_research"), as_of, synthetic=record["is_illustrative"])
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = path.with_name("raw_response.json").read_bytes()
    evidence = {key: data[key] for key in ("source_url", "retrieved_at", "raw_sha256")}
    rebuilt = normalize(raw, profile, record, evidence)
    if data != rebuilt:
        raise ValueError("Normalized price identity/provenance changed")
    return load_diagnostic_snapshot(path, record, as_of)
