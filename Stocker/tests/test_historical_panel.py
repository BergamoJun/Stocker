"""ILLUSTRATIVE — NOT LIVE MARKET DATA. No vendor requests or performance proof."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import json
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

import jsonschema
import pytest

from research import historical_panel, panel_job, panel_prices, panel_transport, sector_universe
from research.local_files import atomic_json
from research.rights import RightsError
from research.sec_report import ROOT
from research.swing_models import Bar, XnysCalendar

AT = "2026-11-07T17:00:00Z"
LABEL = "ILLUSTRATIVE — NOT LIVE MARKET DATA"


@pytest.fixture
def record():
    return {"id": "panel-fixture-only", "provider": "massive", "reviewer": LABEL,
        "reviewed_at": "2026-11-07T00:00:00Z", "review_due_at": "2027-01-01T00:00:00Z",
        "evidence_url": "https://example.invalid/fixture", "plan": "fixture_free", "monthly_cost_usd": 0,
        "is_illustrative": True, "uses": {key: "allowed" for key in ("ingest", "store", "derived_analytics")}}


@pytest.fixture
def approved():
    return {"symbol": "CON", "ticker": "CON", "cik": "0000000001", "share_class_figi": "FIXTURE-CLASS",
        "primary_exchange": "XNAS", "type": "CS", "instrument_type": "equity", "sector_id": "3",
        "symbol_partition": "development_and_validation"}


@pytest.fixture
def bars():
    calendar = XnysCalendar("2024-12-01", "2026-12-31")
    days = calendar.sessions("2024-12-09", "2025-03-21")
    values = [Bar(day, Decimal(100) + Decimal(i) / 10, Decimal(102) + Decimal(i) / 10,
                  Decimal(99) + Decimal(i) / 10, Decimal(101) + Decimal(i) / 10, Decimal("1000.125"), AT)
              for i, day in enumerate(days)]
    return values, calendar


def response(bars, ticker="CON"):
    return json.dumps({"fixture_label": LABEL, "status": "OK", "ticker": ticker, "adjusted": True,
        "results": [{"t": int(datetime.fromisoformat(row.session).replace(tzinfo=ZoneInfo("America/New_York")).timestamp() * 1000),
            **{key: str(getattr(row, name)) for key, name in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"), ("v", "volume"))}} for row in bars]}).encode()


def evidence(raw, ticker="CON"):
    return {"source_url": panel_transport.price_url(ticker, "2024-12-09", "2026-11-06"),
        "retrieved_at": AT, "raw_sha256": hashlib.sha256(raw).hexdigest()}


def test_generic_common_stock_decimal_adapter_rebuilds_raw_and_identity(tmp_path, bars, approved, record):
    raw = response(bars[0])
    path = panel_prices.save(tmp_path, raw, approved, record, evidence(raw))
    data, loaded, _ = panel_prices.load(path, approved, record, AT)
    assert data["symbol"] == "CON" and data["listing_exchange"] == "XNAS"
    assert data["exchange"] == "XNYS" and data["point_in_time_verified"] is False
    assert loaded[0].volume == Decimal("1000.125") and loaded[0].available_at == AT
    changed = deepcopy(approved)
    changed["cik"] = "0000000002"
    with pytest.raises(ValueError, match="identity/provenance"):
        panel_prices.load(path, changed, record, AT)
    path.with_name("raw_response.json").write_bytes(raw + b" ")
    with pytest.raises(ValueError, match="hash"):
        panel_prices.load(path, approved, record, AT)


def test_rights_are_checked_before_even_parsing_price_data(tmp_path, approved, record):
    record["uses"]["derived_analytics"] = "unknown"
    with pytest.raises(RightsError):
        panel_prices.load(tmp_path / "does-not-exist.json", approved, record, AT)


@pytest.mark.parametrize("change", ["wrong_ticker", "wrong_source", "duplicate", "fractional_timestamp", "unadjusted", "future_observation", "missing_session"])
def test_price_adapter_and_loader_fail_closed(change, tmp_path, approved, record, bars):
    payload = json.loads(response(bars[0]))
    if change == "wrong_ticker": payload["ticker"] = "BE"
    if change == "duplicate": payload["results"].append(payload["results"][-1])
    if change == "fractional_timestamp": payload["results"][0]["t"] += .5
    if change == "unadjusted": payload["adjusted"] = False
    if change == "missing_session": del payload["results"][2]
    raw = json.dumps(payload).encode()
    meta = evidence(raw)
    if change == "wrong_source": meta["source_url"] = meta["source_url"].replace("/CON/", "/BE/")
    if change == "future_observation": meta["retrieved_at"] = "2025-02-01T00:00:00Z"
    with pytest.raises((ValueError, RightsError)):
        path = panel_prices.save(tmp_path, raw, approved, record, meta)
        panel_prices.load(path, approved, record, AT)


def test_shared_calendar_locks_final_and_unseen_symbols_before_price_reads(tmp_path, approved, record):
    refs = historical_panel.references()
    assert CounterLike(refs) == {"development": 50, "validation": 16}
    with pytest.raises(ValueError, match="locked"):
        historical_panel.references(("final_locked",))
    approved["symbol_partition"] = "unseen_symbol_final_only"
    with pytest.raises(ValueError, match="Unseen-symbol"):
        historical_panel.evaluate_stock(tmp_path / "missing.json", approved, record, tmp_path)


def CounterLike(refs):
    return {name: sum(row["phase"] == name for row in refs) for name in {row["phase"] for row in refs}}


def test_future_mutation_cannot_change_forecast_or_cost_decision(bars):
    values, calendar = bars
    ref = historical_panel.references()[0]
    before = historical_panel.evaluate_case(values, calendar, ref, as_of=AT, source_ids=[LABEL], is_illustrative=True)
    index = next(i for i, row in enumerate(values) if row.session == ref["reference_session"])
    changed = values[:index + 1] + [replace(row, open=row.open * 2, high=row.high * 2, low=row.low * 2, close=row.close * 2) for row in values[index + 1:]]
    after = historical_panel.evaluate_case(changed, calendar, ref, as_of=AT, source_ids=[LABEL], is_illustrative=True)
    assert before["forecast"] == after["forecast"]
    assert before["screen_decision"] == after["screen_decision"]
    assert before["actual_peak_atr"] != after["actual_peak_atr"]


def test_missing_label_is_unavailable_and_not_a_cash_return(bars):
    values, calendar = bars
    ref = historical_panel.references()[0]
    index = next(i for i, row in enumerate(values) if row.session == ref["reference_session"])
    row = historical_panel.evaluate_case(values[:index + 3], calendar, ref, as_of=AT, source_ids=[LABEL], is_illustrative=True)
    assert row["reason_code"] == "MISSING_FIVE_SESSION_LABEL"
    assert row["cost_scenarios"] == []
    summary = historical_panel.summarize([row])
    assert summary["unavailable_cases"] == 1 and summary["cost_scenarios"][0]["matched_cases"] == 0


def test_ambiguous_execution_retains_bounds_and_screen_can_decline(bars):
    values, calendar = bars
    future = values[-5:]
    future = [replace(row, open=Decimal(100), high=Decimal(105), low=Decimal(95), close=Decimal(101)) for row in future]
    prediction = {"plan": {"reference_session": values[-6].session, "first_target_price": "103", "invalidation_price": "97", "timing": {"sessions": [row.session for row in future]}},
        "execution": [{"cost_bps_per_side": "10", "experimental_first_target_hit_sessions": [1, 3]}]}
    row = historical_panel.execution(prediction, {"screen_decision": "stand_aside"}, future, calendar, "10")
    low, high = map(Decimal, row["unfiltered_net_return_pct_bounds"])
    assert low < 0 < high and row["screened_net_return_pct_bounds"] == ["0", "0"]
    assert row["target_timing_evaluable"] is False


def test_replay_resumes_without_recomputing_or_opening_final(tmp_path, bars, approved, record, monkeypatch):
    raw = response(bars[0])
    path = panel_prices.save(tmp_path / "prices", raw, approved, record, evidence(raw))
    refs = historical_panel.references()[:2]
    monkeypatch.setattr(historical_panel, "references", lambda phases: refs)
    first = historical_panel.evaluate_stock(path, approved, record, tmp_path / "eval", as_of=AT)
    original = (tmp_path / "eval/cases" / (refs[0]["reference_session"] + ".json")).read_bytes()
    monkeypatch.setattr(historical_panel, "evaluate_case", lambda *a, **kw: pytest.fail("Checkpoint should be reused"))
    second = historical_panel.evaluate_stock(path, approved, record, tmp_path / "eval", as_of=AT)
    assert first == second and first["completed_cases"] == 2 and first["final_cases_executed"] == 0
    assert (tmp_path / "eval/cases" / (refs[0]["reference_session"] + ".json")).read_bytes() == original


def test_source_ranges_and_issuer_selection_are_frozen_before_prices(approved):
    text = "\n".join(f" {i} X Group {i}\n {i * 100:04d}-{i * 100 + 9:04d}" for i in range(1, 13))
    groups = sector_universe.parse_definitions(text)
    assert len(groups) == 12 and sector_universe.sector_for("0301", groups) == "3"
    profiles = [{**approved, "cik": f"{i:010d}", "symbol": "BE" if i == 1 else f"T{i}", "share_class_figi": f"CLASS{i}"} for i in range(1, 111)]
    profiles.append({**profiles[0], "symbol": "BE.B"})  # A second share class is not a second company.
    first = sector_universe.freeze(profiles, groups, discovery_complete=True)
    second = sector_universe.freeze(list(reversed(profiles)), groups, discovery_complete=True)
    assert first["selected"] == second["selected"] and len(first["selected"]) == 100
    assert len({row["cik"] for row in first["selected"]}) == 100
    assert sum(row["symbol_partition"] == "unseen_symbol_final_only" for row in first["selected"]) == 20
    assert all(row["symbol_partition"] != "unseen_symbol_final_only" for row in first["selected"] if row["symbol"] == "BE")
    assert all(row["symbol_partition"] != "unseen_symbol_final_only" for row in first["selected"] if row["cik"] == "0000000001")
    assert first["all_sector_size_requirements_met"] is False
    with pytest.raises(ValueError, match="before"):
        sector_universe.freeze(profiles, groups, discovery_complete=False)


def test_conflicting_issuer_and_missing_classification_are_not_invented(approved):
    groups = [{"id": str(i), "name_ko": str(i), "ranges": [[i * 100, i * 100 + 9]]} for i in range(1, 13)]
    result = sector_universe.freeze([approved, {**approved, "symbol": "CON.B", "sector_id": "6"}], groups, discovery_complete=True)
    assert not result["selected"] and result["conflicting_issuer_ids"] == [approved["cik"]]
    assert sector_universe.sector_for(None, groups) is None and sector_universe.sector_for(True, groups) is None


def test_pagination_and_last_identity_reject_external_routes_and_reused_tickers(approved):
    with pytest.raises(ValueError):
        sector_universe.catalog_page({"status": "OK", "results": [], "next_url": "https://bad.invalid/x"})
    with pytest.raises(ValueError):
        sector_universe.catalog_page({"status": "OK", "results": [], "next_url": "https://api.massive.com/v3/reference/tickers?api_key=SECRET"})
    raw = {"status": "OK", "results": {"ticker": "CON", "type": "CS", "cik": approved["cik"], "share_class_figi": "REUSED-TICKER"}}
    with pytest.raises(ValueError, match="CHANGED"):
        sector_universe.verify_last_identity(raw, approved, {}, "2026-11-06")
    assert panel_transport.disk_name("CON") == "ticker-CON"


class Reply:
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, count): return b'{"fixture_label":"ILLUSTRATIVE - NOT LIVE MARKET DATA","status":"OK","results":[]}'


def test_transport_cache_shared_spacing_and_key_redaction(tmp_path, record):
    stamp = [AT]
    requests = []
    class Opener:
        def open(self, request, timeout):
            assert request.get_header("Authorization") == "Bearer FIXTURE-SECRET"
            assert "FIXTURE-SECRET" not in request.full_url and timeout == 20
            requests.append(request.full_url)
            return Reply()
    def sleep(seconds):
        from research.rights import as_utc
        stamp[0] = (as_utc(stamp[0]) + timedelta(seconds=seconds)).isoformat()
    client = panel_transport.Client(record, "FIXTURE-SECRET", root=tmp_path, clock=lambda: stamp[0], sleep=sleep, opener=Opener())
    first = client.get(panel_transport.catalog_url("2024-12-09"))
    second = client.get(panel_transport.catalog_url("2024-12-09"))
    client.get(panel_transport.detail_url("CON", "2024-12-09"))
    assert first["cache_hit"] is False and second["cache_hit"] is True and len(requests) == 2
    assert "FIXTURE-SECRET" not in "".join(path.read_text() for path in tmp_path.rglob("*.json"))
    from research.rights import as_utc
    assert (as_utc(stamp[0]) - as_utc(AT)).total_seconds() == 15


def test_provider_429_defers_without_long_sleep_or_secret_in_errors(tmp_path, record):
    calls = []
    class Opener:
        def open(self, request, timeout):
            calls.append(1)
            raise HTTPError("https://bad.invalid/?key=FIXTURE-SECRET", 429, "FIXTURE-SECRET", {"Retry-After": "120"}, None)
    client = panel_transport.Client(record, "FIXTURE-SECRET", root=tmp_path, clock=lambda: AT,
        sleep=lambda _: pytest.fail("No blocking cooldown sleep"), opener=Opener())
    for _ in range(2):
        with pytest.raises(panel_transport.Deferred) as caught:
            client.get(panel_transport.catalog_url("2024-12-09"))
        assert "FIXTURE-SECRET" not in str(caught.value)
    assert len(calls) == 1


def test_progress_contract_rejects_early_release_and_false_final_counts(tmp_path):
    value = panel_job.progress({"status": "waiting", "stage": "profiles"}, tmp_path)
    schema = json.loads((ROOT / "schemas/historical_panel.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator({"$ref": "#/$defs/progress", "$defs": schema["$defs"]})
    validator.validate(value)
    for key, invalid in (("released_score", 50), ("final_cases_executed", 1), ("model_training_performed", True), ("performance_validated", True), ("unseen_symbols_evaluated", 1)):
        changed = {**value, key: invalid}
        with pytest.raises(jsonschema.ValidationError): validator.validate(changed)


def test_profile_checkpoint_and_price_failure_stay_in_denominator(tmp_path, approved, monkeypatch):
    definitions = [{"id": str(i), "name_ko": str(i), "ranges": []} for i in range(1, 13)]
    atomic_json(tmp_path / "industry_definitions.json", {"groups": definitions})
    atomic_json(tmp_path / "catalog.json", {"rows": [{"ticker": "CON"}]})
    atomic_json(tmp_path / "profiles/ticker-CON.json", {"status": "unresolved", "symbol": "CON", "reason_code": "MISSING_SIC"})
    state = {"stage": "profiles", "profile_index": 0}
    class NoNetwork:
        def get(self, url): pytest.fail("A completed profile must not be fetched twice")
    panel_job.step({"settings": sector_universe.policy()}, state, tmp_path, NoNetwork(), None)
    assert state["profile_index"] == 1 and state["profiles_unresolved"] == 1
    state["stock_results"] = {"CON": {"status": "unresolved", "reason_code": "DELISTED_OR_MISSING"}}
    value = panel_job.progress({**state, "status": "waiting"}, tmp_path)
    assert value["price_snapshots_unresolved"] == 1 and value["symbols_evaluated"] == 0


@pytest.fixture
def worker_fixture(tmp_path, record, monkeypatch):
    # Mock operational state only; no real or synthetic price is passed as live data.
    settings = sector_universe.policy()
    job = {"settings": settings, "task_name": "Stocker-HistoricalPanel-" + "a" * 16, "fixture_label": LABEL}
    record = {**record, "is_illustrative": False}
    atomic_json(tmp_path / "price_entitlement.json", record)
    atomic_json(tmp_path / "industry_definitions.json", {"groups": []})
    monkeypatch.setattr(panel_job, "check", lambda path: job)
    monkeypatch.setattr(panel_job, "key_from_user", lambda: "MOCK-DO-NOT-LOG")
    return tmp_path / "job.json", job


def test_worker_429_checkpoint_prevents_an_immediate_repeat(worker_fixture):
    path, _ = worker_fixture
    calls = []
    class MockClient:
        def __init__(self, record, key, **kwargs): self.attempt = kwargs["on_attempt"]
        def get(self, url):
            self.attempt()
            calls.append(1)
            raise panel_transport.Deferred("2026-11-07T17:02:00Z")
    first = panel_job.run(path, clock=lambda: AT, client_factory=MockClient, disable=lambda _: pytest.fail("Waiting is not terminal"))
    second = panel_job.run(path, clock=lambda: AT, client_factory=MockClient, disable=lambda _: pytest.fail("Waiting is not terminal"))
    assert first["network_requests"] == second["network_requests"] == 1 and len(calls) == 1
    assert second["status"] == "waiting" and second["reason_code"] == "RATE_OR_INVOCATION_DEFERRED"


def test_worker_request_ceiling_self_disables_and_never_makes_another_call(worker_fixture):
    path, job = worker_fixture
    atomic_json(path.parent / "state.json", {"status": "waiting", "stage": "catalog", "network_requests": 16000, "started_at": AT})
    stopped = []
    class MockClient:
        def __init__(self, record, key, **kwargs): self.attempt = kwargs["on_attempt"]
        def get(self, url):
            self.attempt()
            pytest.fail("Request beyond the limit")
    first = panel_job.run(path, clock=lambda: AT, client_factory=MockClient, disable=lambda name: stopped.append(name) or "disabled")
    second = panel_job.run(path, clock=lambda: AT, client_factory=lambda *a, **kw: pytest.fail("Terminal job must not restart"), disable=lambda _: "disabled")
    assert first["status"] == second["status"] == "exhausted"
    assert first["network_requests"] == 16000 and stopped == [job["task_name"]]


def test_changed_frozen_cohort_stops_before_prices_are_read(worker_fixture):
    path, _ = worker_fixture
    atomic_json(path.parent / "cohort.json", {"selected": [], "sectors": []})
    atomic_json(path.parent / "state.json", {"status": "waiting", "stage": "prices", "network_requests": 0,
        "started_at": AT, "cohort_sha256": "0" * 64})
    result = panel_job.run(path, clock=lambda: AT, client_factory=lambda *a, **kw: None, disable=lambda _: "disabled")
    assert result["status"] == "withheld" and result["reason_code"] == "FROZEN_COHORT_CHANGED"
    assert result["network_requests"] == 0


def test_sector_summary_keeps_missing_cases_and_distinguishes_result_from_validation(tmp_path, bars, approved, record, monkeypatch):
    raw = response(bars[0])
    path = panel_prices.save(tmp_path / "prices", raw, approved, record, evidence(raw))
    refs = [historical_panel.references()[0], historical_panel.references()[-1]]
    monkeypatch.setattr(historical_panel, "references", lambda phases: refs)
    symbol_folder = tmp_path / "evaluation" / panel_transport.disk_name(approved["symbol"])
    result = historical_panel.evaluate_stock(path, approved, record, symbol_folder, as_of=AT)
    assert result["partitions"]["validation"]["unavailable_cases"] == 1
    sector = {"sector_id": "3", "name_ko": "제조", "selected_unique_issuers": 1, "available_unique_issuers": 1,
        "heldout_symbols": 0, "minimum_met": False}
    cohort = {"selected": [approved], "sectors": [sector]}
    atomic_json(tmp_path / "cohort.json", cohort)
    output = panel_job.finish_summary(tmp_path, cohort, {"evaluated": [approved["symbol"]], "completed_cases": 2})
    assert output["sectors"][0]["development_and_validation"]["unavailable_cases"] == 1
    assert output["performance_validated"] is False and output["final_cases_executed"] == 0
    assert output["sectors"][0]["breakdowns"]["phase"]["validation"]["cost_scenarios"][0]["matched_cases"] == 0
    assert (tmp_path / "REPORT.ko.md").is_file()
