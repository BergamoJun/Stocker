"""Personal-only Massive Stocks Basic EOD download; no paid/LLM endpoints.

Historical bars downloaded now are retrospective, not proven historical vintages.
The provider's bar-start timestamp is never treated as publication/availability.
"""

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

from .rights import as_utc, price_uses, require_entitlement
from .sec_report import ROOT
from .swing_models import XnysCalendar, load_price_snapshot

API_HOST = "https://api.massive.com"
MAX_BYTES = 2_000_000
SYMBOL = "BE"  # Initial identity already checked against the SEC pilot; no symbol guessing.


class PriceFetchError(ValueError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def current_time():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def source_record():
    records = json.loads((ROOT / "config/source_entitlements.json").read_text(encoding="utf-8"))["records"]
    return deepcopy(next(record for record in records if record["provider"] == "massive"))


def confirmed_record(record, confirmed, at):
    if not confirmed:
        raise PriceFetchError("Confirm your own free personal/non-professional account and required subscriber terms first")
    record = deepcopy(record)
    record["account_eligibility_verified"] = True
    record["account_confirmed_at"] = at
    record["account_confirmation_basis"] = "user_cli_attestation; not independently verified"
    require_entitlement(record, price_uses("local_research"), at)
    return record


def basic_window(at, lookback_days=700):
    """Basic cannot request the current ET day, even after the exchange close."""
    if type(lookback_days) is not int or not 100 <= lookback_days <= 700:
        raise PriceFetchError("Use 100-700 calendar days within the free two-year allowance")
    cutoff = as_utc(at)
    calendar = XnysCalendar((cutoff.date() - timedelta(days=750)).isoformat(),
                            (cutoff.date() + timedelta(days=45)).isoformat())
    yesterday_et = cutoff.astimezone(ZoneInfo("America/New_York")).date() - timedelta(days=1)
    eligible = calendar.sessions((yesterday_et - timedelta(days=20)).isoformat(), yesterday_et.isoformat())
    end = eligible[-1]
    start = (datetime.fromisoformat(end).date() - timedelta(days=lookback_days - 1)).isoformat()
    return start, end, calendar


def request_url(start, end, *, symbol=SYMBOL):
    if symbol not in {SYMBOL, "SPY", "PBW"}:
        raise PriceFetchError("Unapproved price symbol")
    return (f"{API_HOST}/v2/aggs/ticker/{symbol}/range/1/day/{start}/{end}"
            "?adjusted=true&sort=asc&limit=50000")


def download(start, end, api_key, opener=None, *, symbol=SYMBOL):
    """One bounded request; no retries, pagination, redirects, or pay-per-call flow."""
    if not api_key or api_key != api_key.strip() or any(ord(char) < 33 or ord(char) > 126 for char in api_key):
        raise PriceFetchError("MASSIVE_API_KEY is missing or invalid; configure it in your local process environment")
    request = Request(request_url(start, end, symbol=symbol), headers={"Authorization": "Bearer " + api_key,
                      "Accept": "application/json", "User-Agent": "Stocker-personal-research/0.3"})
    opener = opener or build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=20) as response:
            raw = response.read(MAX_BYTES + 1)
    except HTTPError as error:
        # Do not print provider error bodies/URLs/headers, which could echo a token.
        raise PriceFetchError(f"Massive HTTP {error.code}; no retry, upgrade or purchase attempted") from None
    except Exception:
        raise PriceFetchError("Massive transport failed; no retry or fallback feed used") from None
    if len(raw) > MAX_BYTES:
        raise PriceFetchError("Massive response exceeds size limit")
    if api_key.encode("ascii") in raw:
        raise PriceFetchError("Response unexpectedly contains credential material; refusing to save it")
    return raw


def normalize(raw, record, retrieved_at, start, end, calendar, *, symbol=SYMBOL):
    request_url(start, end, symbol=symbol)  # Explicit allowlist; default identity remains BE.
    def invalid_constant(_):
        raise ValueError("Non-finite JSON number")
    try:
        payload = json.loads(raw, parse_float=Decimal, parse_constant=invalid_constant)
        if (payload.get("status") not in {"OK", "DELAYED"} or payload.get("ticker") != symbol
                or payload.get("adjusted") is not True):
            raise ValueError("Unexpected identity, status or split-adjustment response")
        if payload.get("next_url"):
            raise ValueError("Unexpected pagination; refusing partial history or arbitrary next URL")
        results = payload.get("results")
        if not isinstance(results, list) or not results or len(results) > 550:
            raise ValueError("Missing or excessive daily results")
        digest = hashlib.sha256(raw).hexdigest()
        bars = []
        for item in results:
            timestamp = item["t"]
            if type(timestamp) is not int:
                raise ValueError("Bar start must be an integer Unix millisecond timestamp")
            instant = datetime.fromtimestamp(timestamp / 1000, timezone.utc)
            local = instant.astimezone(ZoneInfo("America/New_York"))
            session = local.date().isoformat()
            if local.time().isoformat() != "00:00:00" or not start <= session <= end:
                raise ValueError("Unexpected daily bar start/date range")
            prices = [Decimal(str(item[key])) for key in ("o", "h", "l", "c")]
            if any(not value.is_finite() or value <= 0 for value in prices):
                raise ValueError("Invalid price")
            opening, high, low, close = prices
            if not low <= min(opening, close) <= max(opening, close) <= high:
                raise ValueError("Invalid OHLC envelope")
            volume = Decimal(str(item["v"]))
            if not volume.is_finite() or volume < 0:
                raise ValueError("Invalid volume")
            ended = calendar.close(session)
            if as_utc(ended) > as_utc(retrieved_at):
                raise ValueError("Incomplete/future daily session")
            bars.append({"session": session, **dict(zip(("open", "high", "low", "close"), map(str, prices))),
                         "volume": str(volume), "available_at": retrieved_at,
                         "period_ended_at": ended, "provider_bar_start_at": instant.isoformat(),
                         "published_at": None})
        if [bar["session"] for bar in bars] != sorted({bar["session"] for bar in bars}):
            raise ValueError("Unsorted or duplicate daily bars")
    except (ValueError, TypeError, KeyError, AttributeError, InvalidOperation, OverflowError, OSError):
        raise PriceFetchError("Invalid/incomplete Massive daily response; no price snapshot released") from None
    result = {"schema_version": "1.1.0", "snapshot_id": f"massive-{symbol}-" + digest[:20], "provider": "massive",
            "entitlement_id": record["id"], "is_illustrative": False,
            "symbol": symbol, "cik": "0001664703" if symbol == SYMBOL else None, "currency": "USD", "exchange": "XNYS",
            "quote_kind": "eod", "adjustment": "split_adjusted_current_share_basis",
            "dividend_adjusted": False, "point_in_time_verified": False,
            "volume_representation": "exact_decimal_string_shares",
            "retrieved_at": retrieved_at, "source_ids": [f"massive-{symbol}-" + digest[:20]],
            "source_url": request_url(start, end, symbol=symbol), "raw_sha256": digest,
            "availability_semantics": "Observed at download, not original publication; bar t is period start",
            "limitations": ["Retrospective split adjustments/corrections; no verified historical vintages",
                            "Provider daily eligible-trade aggregates; no tick-level execution inference",
                            "Personal use only; no redistribution or external LLM input",
                            "Keep account/plan valid; deletion on license end follows provider terms"],
            "bars": bars}
    if symbol != SYMBOL:
        result.update(instrument_type="etf", listing_exchange="ARCX", leveraged_or_inverse=False)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Personal-only free BE EOD snapshot; does not generate a trading report")
    parser.add_argument("--check", action="store_true", help="Check prerequisites without network or data download")
    parser.add_argument("--confirm-personal-nonprofessional", action="store_true",
                        help="Attest own Stocks Basic account eligibility and completion of required subscriber terms")
    parser.add_argument("--lookback-days", type=int, default=700)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports/private/market_data")
    args = parser.parse_args(argv)
    destination_root = args.output_dir.resolve()
    if not any(destination_root.is_relative_to(root) for root in (ROOT / "reports/private", ROOT / "local_data")):
        parser.error("Market snapshots must stay under ignored reports/private/ or local_data/")
    at = current_time()
    api_key = os.environ.get("MASSIVE_API_KEY", "")
    record = source_record()
    manifest = {"as_of": at, "scope": "local_research", "provider": "massive", "plan": "stocks_basic",
                "monthly_data_budget_usd": 0, "api_key_present": bool(api_key.strip()),
                "account_eligibility_confirmed": args.confirm_personal_nonprofessional,
                "public_distribution_enabled": False, "external_price_llm_input_enabled": False,
                "status": "prerequisites_checked" if args.check else "blocked",
                "network_called": False, "released_score": None, "released_price_plan": None}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = destination_root / stamp
    destination.mkdir(parents=True, exist_ok=False)
    exit_code = 0
    if not args.check:
        try:
            record = confirmed_record(record, args.confirm_personal_nonprofessional, at)
            if not api_key.strip():
                raise PriceFetchError("MASSIVE_API_KEY is missing; set it locally, never in chat")
            start, end, calendar = basic_window(at, args.lookback_days)
            manifest["network_called"] = True
            raw = download(start, end, api_key)
            retrieved = current_time()
            snapshot = normalize(raw, record, retrieved, start, end, calendar)
            (destination / "raw_response.json").write_bytes(raw)
            (destination / "prices.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (destination / "price_entitlement.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
            manifest.update(status="downloaded_research_only", retrieved_at=retrieved,
                            latest_session=snapshot["bars"][-1]["session"],
                            bars=len(snapshot["bars"]), point_in_time_verified=False)
            try:
                load_price_snapshot(destination / "prices.json", record, retrieved)
                manifest["current_plan_input_status"] = "input_checks_passed_model_unvalidated"
            except ValueError as error:
                manifest["current_plan_input_status"] = "blocked"
                manifest["input_limitation"] = str(error)
        except (ValueError, OSError) as error:
            manifest["reason"] = str(error)
            exit_code = 2
    if manifest["status"] == "downloaded_research_only":
        manifest["next_step"] = ("Run model preparation with the saved snapshot/entitlement; current input limitations still apply. "
                                 "No validated recommendation or historical vintage is implied")
    elif not manifest["api_key_present"] or not manifest["account_eligibility_confirmed"]:
        manifest["next_step"] = ("Set your own free key in process environment and confirm account eligibility; "
                                 "no credential value belongs in chat, source control or reports")
    else:
        manifest["next_step"] = "Inspect the sanitized reason; do not repeat key setup or activate a paid plan"
    (destination / "fetch_readiness.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(destination), "status": manifest["status"],
                      "api_key_present": manifest["api_key_present"], "network_called": manifest["network_called"]}))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
