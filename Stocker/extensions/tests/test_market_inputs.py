"""ILLUSTRATIVE - synthetic prices only; no external requests."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import InvalidOperation
import hashlib
import io
import json
from urllib.error import HTTPError
from zoneinfo import ZoneInfo
import pytest

from extensions import market_client, market_inputs as market
from research.local_files import atomic_json, exclusive_lock, LockBusy
from research.panel_transport import Deferred, SourceUnavailable, detail_url, price_url
from research.rights import RightsError
from research.swing_models import XnysCalendar
from research.validation import stable_hash


class Clock:
    def __init__(self): self.at = datetime(2026, 11, 10, 12, tzinfo=timezone.utc); self.elapsed = 0
    def __call__(self): return self.at.isoformat()
    def pause(self, seconds): self.at += timedelta(seconds=seconds); self.elapsed += seconds


def entitlement():
    return {"id": "ILLUSTRATIVE", "provider": "massive", "plan": "stocks_basic", "is_illustrative": True, "monthly_cost_usd": 0,
        "reviewer": "ILLUSTRATIVE", "reviewed_at": "2026-11-01T00:00:00Z", "review_due_at": "2027-01-01T00:00:00Z", "evidence_url": "https://example.com/rights",
        "uses": {"ingest": "allowed", "store": "allowed", "derived_analytics": "allowed"}}


def reference(ticker="PBW"):
    issuers = market.policy()["issuers"]
    cik = next((c for c, p in issuers.items() if p["symbol"] == ticker), None)
    return {"status": "OK", "results": {"ticker": ticker, "active": True, "market": "stocks", "locale": "us", "currency_name": "usd",
        "type": "CS" if cik else "ETF", "cik": cik, "primary_exchange": "XNYS" if cik else "ARCX", "share_class_figi": "ILLUSTRATIVE", "composite_figi": "ILLUSTRATIVE"}}


def daily(ticker="PBW"):
    sessions = XnysCalendar("2026-09-01", "2026-11-20").sessions("2026-10-01", "2026-11-09")
    return {"status": "OK", "ticker": ticker, "adjusted": True, "results": [{"t": int(datetime.fromisoformat(s).replace(tzinfo=ZoneInfo("America/New_York")).timestamp() * 1000),
        "o": "100", "h": "103", "l": "99", "c": str(100 + i % 3), "v": "1000.125"} for i, s in enumerate(sessions)]}


def normalized(payload=None, profile=None, record=None, retrieved_at="2026-11-10T12:00:00Z"):
    payload = payload or daily(); raw = json.dumps(payload).encode()
    profile = profile or market.identity(json.dumps(reference()).encode(), "PBW", market.policy())
    evidence = {"source_url": price_url(profile["symbol"], "2024-12-10", "2026-11-09"), "raw_sha256": hashlib.sha256(raw).hexdigest(), "retrieved_at": retrieved_at}
    return market.normalize(raw, profile, record or entitlement(), evidence)


def setup(tmp_path, *, maximum_requests=16):
    baseline = tmp_path / "baseline.json"; root = tmp_path / "project"; root.mkdir()
    atomic_json(baseline, {"version": "1.0.0", "root": str(root), "files": {}, "allowed_modified": [], "allowed_new": [], "allowed_extensions": [], "adapter_allow_new": [], "jobs": [], "reports": [], "additional_reports": []})
    clock = Clock()
    class Opener:
        def __init__(self): self.calls = []; self.failure = None
        def open(self, request, timeout):
            self.calls.append((request.full_url, clock()))
            with pytest.raises(LockBusy):
                with exclusive_lock(tmp_path / "account/request.lock"): pass
            if self.failure: raise self.failure
            ticker = request.full_url.split("/tickers/")[-1].split("?")[0] if "/tickers/" in request.full_url else request.full_url.split("/ticker/")[1].split("/")[0]
            return io.BytesIO(json.dumps(reference(ticker) if "/tickers/" in request.full_url else daily(ticker)).encode())
    opener = Opener()
    client = market_client.Client(entitlement(), "illustrative-key", baseline, root=tmp_path / "account", opener=opener, clock=clock, sleep=clock.pause, maximum_requests=maximum_requests)
    client.queue.monotonic = lambda: clock.elapsed
    return client, opener, clock, baseline


def test_identity_separates_issuer_and_fund_playbooks():
    stock = market.identity(json.dumps(reference("BE")), "BE", market.policy())
    fund = market.identity(json.dumps(reference()), "PBW", market.policy())
    assert stock["cik"] == "0001664703" and stock["instrument_type"] == "equity"
    assert fund["cik"] is None and fund["instrument_type"] == "etf" and fund["leveraged_or_inverse"] is False


@pytest.mark.parametrize("field,value", [("cik", "789019"), ("type", "ETF"), ("active", False), ("currency_name", "eur"), ("ticker", "XOM"), ("share_class_figi", None), ("primary_exchange", "UNKNOWN")])
def test_stock_identity_mismatches_cannot_be_guessed(field, value):
    raw = reference("BE"); raw["results"][field] = value
    with pytest.raises(ValueError): market.identity(json.dumps(raw), "BE", market.policy())


@pytest.mark.parametrize("change", ["leverage", "unregistered", "type", "exchange"])
def test_fund_identity_requires_reviewed_nonleveraged_structure(change):
    settings, raw = market.policy(), reference()
    if change == "leverage": settings["funds"]["PBW"]["leveraged_or_inverse"] = True
    if change == "unregistered": del settings["funds"]["PBW"]
    if change == "type": raw["results"]["type"] = "CS"
    if change == "exchange": raw["results"]["primary_exchange"] = "XNAS"
    with pytest.raises(ValueError): market.identity(json.dumps(raw), "PBW", settings)


def test_exact_fractional_volume_and_current_vintage_provenance_are_retained():
    result = normalized()
    assert result["bars"][-1]["volume"] == "1000.125" and result["point_in_time_verified"] is False
    assert result["dividend_adjusted"] is False and result["bars"][-1]["published_at"] is None


@pytest.mark.parametrize("change", ["duplicate", "unsorted", "negative_volume", "nonfinite", "envelope", "partial", "unadjusted", "wrong_ticker", "intraday", "float_timestamp", "weekend", "incomplete"])
def test_fund_response_rejects_bad_identity_timing_and_bars(change):
    value = daily(); retrieved = "2026-11-10T12:00:00Z"
    if change == "duplicate": value["results"].append(deepcopy(value["results"][-1]))
    if change == "unsorted": value["results"].reverse()
    if change == "negative_volume": value["results"][0]["v"] = "-1"
    if change == "nonfinite": value["results"][0]["c"] = "NaN"
    if change == "envelope": value["results"][0]["h"] = "1"
    if change == "partial": value["next_url"] = "https://example.com/more"
    if change == "unadjusted": value["adjusted"] = False
    if change == "wrong_ticker": value["ticker"] = "SPY"
    if change == "intraday": value["results"][0]["t"] += 3600000
    if change == "float_timestamp": value["results"][0]["t"] = float(value["results"][0]["t"])
    if change == "weekend": value["results"][-1]["t"] = int(datetime(2026, 11, 8, tzinfo=ZoneInfo("America/New_York")).timestamp() * 1000)
    if change == "incomplete": retrieved = "2026-11-09T19:00:00Z"
    with pytest.raises((ValueError, InvalidOperation)): normalized(value, retrieved_at=retrieved)


def test_equity_path_reuses_frozen_generic_normalizer():
    profile = market.identity(json.dumps(reference("BE")), "BE", market.policy())
    value = normalized(daily("BE"), profile)
    assert value["cik"] == "0001664703" and value["source_ids"]


def test_rights_checked_before_price_parsing():
    record = entitlement(); record["uses"]["derived_analytics"] = "denied"
    with pytest.raises(RightsError): market.normalize(b"not json", {}, record, {"retrieved_at": "2026-11-10T12:00:00Z"})


def test_return_windows_require_every_exchange_session():
    data = normalized(); assert market.returns(data, 5)["end"] == "2026-11-09"
    data["bars"].pop(-3); assert market.returns(data, 5) is None
    assert market.returns({"bars": data["bars"][:3]}, 5) is None


def test_latest_completed_respects_weekends_and_session_close():
    assert market.latest_completed("2026-11-09T20:59:59Z") == "2026-11-06"
    assert market.latest_completed("2026-11-09T21:00:00Z") == "2026-11-09"
    assert market.latest_completed("2026-11-08T12:00:00Z") == "2026-11-06"


def test_transport_cache_preserves_retrieval_and_does_not_consume_requests(tmp_path):
    client, opener, clock, _ = setup(tmp_path)
    url = detail_url("BE", "2026-11-09"); first = client.get(url); clock.pause(5); second = client.get(url)
    assert first["retrieved_at"] == second["retrieved_at"] and second["cache_reused"] is True
    assert len(opener.calls) == client.network_requests == 1


def test_market_requests_share_completion_budget_and_lock(tmp_path):
    client, opener, clock, _ = setup(tmp_path)
    client.get(detail_url("BE", "2026-11-09")); client.get(detail_url("SPY", "2026-11-09"))
    assert (datetime.fromisoformat(opener.calls[1][1]) - datetime.fromisoformat(opener.calls[0][1])).total_seconds() == 15
    assert json.loads((client.root / "budget.json").read_text())["requests_attempted"] == 2


def test_market_cap_and_rights_stop_before_http(tmp_path):
    client, opener, *_ = setup(tmp_path, maximum_requests=1)
    client.get(detail_url("BE", "2026-11-09"))
    with pytest.raises(SourceUnavailable, match="MARKET_RUN_REQUEST_CAP"): client.get(detail_url("SPY", "2026-11-09"))
    client.record["uses"]["store"] = "denied"
    with pytest.raises(RightsError): client.get(detail_url("BE", "2026-11-09"))
    assert len(opener.calls) == 1


def test_429_cooldown_is_shared_and_cached_without_retry(tmp_path):
    client, opener, clock, _ = setup(tmp_path)
    opener.failure = HTTPError("https://api.massive.com/", 429, "ILLUSTRATIVE", {"Retry-After": "1200"}, None)
    for ticker in ("BE", "BE", "SPY"):
        with pytest.raises(Deferred): client.get(detail_url(ticker, "2026-11-09"))
    assert len(opener.calls) == 1 and "12:20:00" in json.loads((client.root / "budget.json").read_text())["blocked_until"]


def test_failed_http_cache_cannot_be_reported_as_empty_success(tmp_path):
    client, opener, *_ = setup(tmp_path); opener.failure = HTTPError("https://api.massive.com/", 403, "ILLUSTRATIVE", {}, None)
    for _ in range(2):
        with pytest.raises(SourceUnavailable, match="HTTP_403"): client.get(detail_url("BE", "2026-11-09"))
    assert len(opener.calls) == 1


def test_record_replay_alignment_first_availability_and_stale_withholding(tmp_path):
    client, opener, clock, baseline = setup(tmp_path)
    result, folder = market.collect(["1664703"], baseline, client=client, output=tmp_path / "output", clock=clock)
    assert result["requests"] == 6
    with pytest.raises(ValueError, match="NOT_AVAILABLE"): market.view(folder, clock(), cik="1664703", illustrative=True)
    clock.pause(1.01); value = market.view(folder, clock(), cik="1664703", illustrative=True)
    assert len(value["comparisons"]) == 6 and all(r["status"] == "available" for r in value["roles"].values())
    assert value["released_score"] is None and value["external_model_input_allowed"] is False
    stale = market.view(folder, "2026-11-10T21:00:01Z", cik="1664703", illustrative=True)
    assert stale["comparisons"] == [] and all(r["status"] == "stale" for r in stale["roles"].values())
    with pytest.raises((ValueError, RightsError)): market.view(folder, clock(), cik="1664703", illustrative=False)
    with pytest.raises(ValueError, match="ROLE_MAPPING"): market.view(folder, clock(), cik="789019", illustrative=True)
    (folder / "sources/BE.daily.json").write_text("ILLUSTRATIVE CHANGED")
    with pytest.raises(ValueError, match="SOURCE_CHANGED"): market.view(folder, clock(), cik="1664703", illustrative=True)


def test_failed_identity_is_withheld_without_substituting_other_issuer_prices(tmp_path):
    client, opener, clock, baseline = setup(tmp_path)
    original = client.get
    def wrong_identity(url):
        value = original(url)
        if "/tickers/BE?" in url:
            raw = reference("BE"); raw["results"]["cik"] = "789019"; value["raw"] = json.dumps(raw).encode()
        return value
    client.get = wrong_identity
    result, folder = market.collect(["1664703"], baseline, client=client, output=tmp_path / "output", clock=clock)
    assert result["instruments"]["BE"]["status"] == "withheld" and result["requests"] == 5
    clock.pause(1.01); value = market.view(folder, clock(), cik="1664703", illustrative=True)
    assert value["roles"]["stock"]["status"] == "unavailable" and value["comparisons"] == []
