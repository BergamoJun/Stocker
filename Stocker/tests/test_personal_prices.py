"""ILLUSTRATIVE — NOT LIVE MARKET DATA. Mock HTTP responses, no vendor calls."""

from copy import deepcopy
from datetime import datetime
from decimal import Decimal
import json
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

import pytest

from research import fetch_prices, prepare_models
from research.rights import RightsError, price_uses, require_entitlement
from research.sec_report import ROOT
from research.swing_models import XnysCalendar, load_price_snapshot

AT = "2026-11-07T12:00:00Z"
LABEL = "ILLUSTRATIVE — NOT LIVE MARKET DATA"


@pytest.fixture
def record():
    return fetch_prices.source_record()


def response_for(sessions):
    return json.dumps({"fixture_label": LABEL, "status": "OK", "ticker": "BE", "adjusted": True,
                       "results": [{"t": int(datetime.fromisoformat(day).replace(tzinfo=ZoneInfo("America/New_York")).timestamp() * 1000),
                                    "o": 100.01, "h": 102.02, "l": 99.03, "c": 101.04, "v": 1000.0}
                                   for day in sessions]}).encode()


def test_personal_rights_need_account_attestation_and_never_clear_public(record):
    with pytest.raises(RightsError, match="eligibility"):
        require_entitlement(record, price_uses("local_research"), AT)
    confirmed = fetch_prices.confirmed_record(record, True, AT)
    require_entitlement(confirmed, price_uses("local_research"), AT)
    assert record["account_eligibility_verified"] is False
    with pytest.raises(RightsError, match="public_display"):
        require_entitlement(confirmed, price_uses("public_report"), AT)
    confirmed["account_confirmed_at"] = "2026-11-08T00:00:00Z"
    with pytest.raises(RightsError, match="cutoff"):
        require_entitlement(confirmed, price_uses("local_research"), AT)


def test_basic_window_excludes_current_et_day_and_stays_in_free_history():
    # Nov 6 session closed, but Basic cannot request Nov 6 until midnight ET.
    start, end, _ = fetch_prices.basic_window("2026-11-07T01:00:00Z")
    assert end == "2026-11-05"
    assert (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days == 699
    assert fetch_prices.basic_window("2026-11-07T05:01:00Z")[1] == "2026-11-06"
    # Weekend, DST, holiday and shortened Friday use actual sessions.
    assert fetch_prices.basic_window("2026-11-28T12:00:00Z")[1] == "2026-11-27"
    with pytest.raises(fetch_prices.PriceFetchError, match="100-700"):
        fetch_prices.basic_window(AT, 731)


def test_download_keeps_key_out_of_url_and_sanitizes_errors():
    class Reply:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, size):
            assert size == fetch_prices.MAX_BYTES + 1
            return b'{"status":"OK"}'
    class Opener:
        def open(self, request, timeout):
            assert "PRIVATE-KEY" not in request.full_url
            assert request.get_header("Authorization") == "Bearer PRIVATE-KEY"
            assert timeout == 20
            return Reply()
    assert fetch_prices.download("2026-01-01", "2026-11-06", "PRIVATE-KEY", Opener())
    class Failure:
        def open(self, *args, **kwargs):
            raise HTTPError("https://bad.invalid/?key=PRIVATE-KEY", 429, "PRIVATE-KEY", {}, None)
    with pytest.raises(fetch_prices.PriceFetchError, match="HTTP 429") as caught:
        fetch_prices.download("2026-01-01", "2026-11-06", "PRIVATE-KEY", Failure())
    assert "PRIVATE-KEY" not in str(caught.value)
    class Echo(Reply):
        def read(self, _):
            return b'{"credential":"PRIVATE-KEY"}'
    class EchoOpener:
        def open(self, *args, **kwargs):
            return Echo()
    with pytest.raises(fetch_prices.PriceFetchError, match="credential"):
        fetch_prices.download("2026-01-01", "2026-11-06", "PRIVATE-KEY", EchoOpener())
    assert fetch_prices.NoRedirect().redirect_request(None, None, 302, "", {}, "https://bad.invalid") is None


def test_normalized_bar_start_is_not_historical_availability(record):
    calendar = XnysCalendar("2026-01-01", "2026-12-31")
    data = fetch_prices.normalize(response_for(["2026-11-05", "2026-11-06"]), record, AT,
                                   "2026-01-01", "2026-11-06", calendar)
    assert data["point_in_time_verified"] is False
    assert data["dividend_adjusted"] is False
    assert data["bars"][-1]["close"] == "101.04"
    assert data["bars"][-1]["available_at"] == AT
    assert data["bars"][-1]["published_at"] is None
    assert data["bars"][-1]["provider_bar_start_at"] == "2026-11-06T05:00:00+00:00"
    assert data["bars"][-1]["period_ended_at"] == "2026-11-06T21:00:00Z"
    assert data["schema_version"] == "1.1.0"
    assert data["bars"][-1]["volume"] == "1000.0"


@pytest.mark.parametrize("mutation", ["adjustment", "identity", "pagination", "duplicate", "bad_ohlc", "negative_volume", "future"])
def test_invalid_or_partial_provider_data_is_not_saved(record, mutation):
    calendar = XnysCalendar("2026-01-01", "2026-12-31")
    payload = json.loads(response_for(["2026-11-05", "2026-11-06"]))
    if mutation == "adjustment":
        payload["adjusted"] = False
    elif mutation == "identity":
        payload["ticker"] = "OTHER"
    elif mutation == "pagination":
        payload["next_url"] = "https://bad.invalid/?apiKey=should-not-follow"
    elif mutation == "duplicate":
        payload["results"].append(payload["results"][-1])
    elif mutation == "bad_ohlc":
        payload["results"][0]["h"] = 1
    elif mutation == "negative_volume":
        payload["results"][0]["v"] = -1.5
    elif mutation == "future":
        payload = json.loads(response_for(["2026-11-09"]))
    with pytest.raises(fetch_prices.PriceFetchError, match="Invalid/incomplete"):
        fetch_prices.normalize(json.dumps(payload).encode(), record, AT, "2026-01-01", "2026-11-06", calendar)


def test_fractional_provider_volume_is_preserved_without_rounding(tmp_path, record):
    calendar = XnysCalendar("2026-01-01", "2026-12-31")
    sessions = calendar.sessions("2026-01-02", "2026-11-06")
    raw = response_for(sessions).replace(b'"v": 1000.0', b'"v": 1000.0000000000001')
    confirmed = fetch_prices.confirmed_record(record, True, AT)
    data = fetch_prices.normalize(raw, confirmed, AT, "2026-01-01", "2026-11-06", calendar)
    assert data["bars"][-1]["volume"] == "1000.0000000000001"
    path = tmp_path / "prices.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    _, bars, _ = load_price_snapshot(path, confirmed, AT)
    assert bars[-1].volume == Decimal("1000.0000000000001")
    assert bars[-1].volume != 1000


@pytest.mark.parametrize("volume", ["NaN", "Infinity", "-1", "nonsense", True, 1.5])
def test_decimal_volume_snapshot_rejects_invalid_values(tmp_path, record, volume):
    calendar = XnysCalendar("2026-01-01", "2026-12-31")
    sessions = calendar.sessions("2026-01-02", "2026-11-06")
    confirmed = fetch_prices.confirmed_record(record, True, AT)
    data = fetch_prices.normalize(response_for(sessions), confirmed, AT, "2026-01-01", "2026-11-06", calendar)
    data["bars"][-1]["volume"] = volume
    path = tmp_path / "prices.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="volume"):
        load_price_snapshot(path, confirmed, AT)


def test_missing_credentials_or_confirmation_never_calls_network(tmp_path, monkeypatch, record, capsys):
    monkeypatch.setattr(fetch_prices, "ROOT", tmp_path)
    monkeypatch.setattr(fetch_prices, "source_record", lambda: deepcopy(record))
    monkeypatch.setattr(fetch_prices, "current_time", lambda: AT)
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    def fail(*args, **kwargs):
        pytest.fail("No network permitted")
    monkeypatch.setattr(fetch_prices, "download", fail)
    assert fetch_prices.main(["--confirm-personal-nonprofessional"]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["network_called"] is False
    assert not list(tmp_path.rglob("prices.json"))
    monkeypatch.setenv("MASSIVE_API_KEY", "PRIVATE-KEY")
    assert fetch_prices.main([]) == 2
    assert "PRIVATE-KEY" not in capsys.readouterr().out


def test_download_to_personal_preparation_does_not_fake_backtest(tmp_path, monkeypatch, record, capsys):
    # Mock response uses synthetic prices. All artifacts stay in pytest's temp folder.
    monkeypatch.setattr(fetch_prices, "ROOT", tmp_path)
    monkeypatch.setattr(fetch_prices, "source_record", lambda: deepcopy(record))
    monkeypatch.setattr(fetch_prices, "current_time", lambda: AT)
    monkeypatch.setenv("MASSIVE_API_KEY", "PRIVATE-KEY")
    calendar = XnysCalendar("2026-01-01", "2026-12-31")
    raw = response_for(calendar.sessions("2026-01-02", "2026-11-06"))
    monkeypatch.setattr(fetch_prices, "download", lambda *args: raw)
    assert fetch_prices.main(["--confirm-personal-nonprofessional"]) == 0
    destination = json.loads(capsys.readouterr().out)["output_dir"]
    path = tmp_path / destination
    snapshot, _, _ = load_price_snapshot(path / "prices.json",
        json.loads((path / "price_entitlement.json").read_text()), AT)
    assert snapshot["point_in_time_verified"] is False
    output = tmp_path / "prepared"
    assert prepare_models.main(["--prices", str(path / "prices.json"), "--price-entitlement",
        str(path / "price_entitlement.json"), "--as-of", AT, "--output-dir", str(output)]) == 0
    readiness = json.loads(next(output.rglob("model_readiness.json")).read_text(encoding="utf-8"))
    assert readiness["scope"] == "local_research"
    assert readiness["price_research"]["candidate"]["status"] == "research_only_unvalidated"
    assert readiness["price_research"]["evaluation"]["status"] == "blocked"
    assert readiness["released_score"] is None
    assert all("public-price" not in blocker for blocker in readiness["blockers"])
    assert all("PRIVATE-KEY" not in file.read_text(encoding="utf-8") for file in tmp_path.rglob("*.json"))


def test_market_snapshot_output_cannot_be_redirected_to_public_source_tree(tmp_path):
    with pytest.raises(SystemExit) as caught:
        fetch_prices.main(["--check", "--output-dir", str(tmp_path)])
    assert caught.value.code == 2


def test_confirmed_account_is_separate_from_stale_price_and_process_key(tmp_path, monkeypatch, record):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    calendar = XnysCalendar("2026-01-01", "2026-12-31")
    confirmed = fetch_prices.confirmed_record(record, True, AT)
    sessions = calendar.sessions("2026-01-02", "2026-11-05")
    data = fetch_prices.normalize(response_for(sessions), confirmed, AT, "2026-01-01", "2026-11-05", calendar)
    prices = tmp_path / "prices.json"
    entitlement = tmp_path / "rights.json"
    prices.write_text(json.dumps(data), encoding="utf-8")
    entitlement.write_text(json.dumps(confirmed), encoding="utf-8")
    output = tmp_path / "prepared"
    assert prepare_models.main(["--prices", str(prices), "--price-entitlement", str(entitlement),
        "--as-of", AT, "--output-dir", str(output)]) == 0
    readiness = json.loads(next(output.rglob("model_readiness.json")).read_text(encoding="utf-8"))
    assert readiness["personal_price_source"]["api_key_present"] is False
    assert readiness["personal_price_source"]["terms_review"] == "operator_record_passed_technical_checks_not_legal_proof"
    assert readiness["price_research"]["status"] == "blocked"
    assert "stale" in readiness["price_research"]["reason"]
    assert all("confirmed own free account" not in blocker for blocker in readiness["blockers"])
    assert "Fresh licensed BE snapshot required for current price plan" in readiness["blockers"]
    assert readiness["released_price_plan"] is None
    assert readiness["released_score"] is None
