"""ILLUSTRATIVE — NOT LIVE MARKET DATA. Synthetic records and mocked clocks."""

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json

import pytest
import jsonschema

from research import advance, validation
from research.swing_models import Bar, XnysCalendar, load_price_snapshot

AT = "2026-11-07T12:00:00Z"
LATER = "2026-11-14T12:00:00Z"
LABEL = "ILLUSTRATIVE — NOT LIVE MARKET DATA"


@pytest.fixture
def synthetic(tmp_path):
    calendar = XnysCalendar("2025-11-01", "2026-12-31")
    sessions = calendar.sessions("2026-01-02", "2026-11-06")
    bars = []
    for index, session in enumerate(sessions):
        close = Decimal(100) + Decimal(index) / 10
        bars.append(Bar(session, close, close + 1, close - 1, close, Decimal("1000.1"), AT))
    record = {"id": "fixture-rights", "provider": "fixture-provider", "plan": LABEL,
        "monthly_cost_usd": 0, "reviewer": "fixture", "reviewed_at": "2026-01-01T00:00:00Z",
        "review_due_at": "2027-01-01T00:00:00Z", "evidence_url": "https://example.invalid/fixture",
        "is_illustrative": True, "uses": {key: "allowed" for key in ("ingest", "store", "derived_analytics")}}
    data = {"schema_version": "1.1.0", "symbol": "BE", "is_illustrative": True,
        "snapshot_id": "fixture-snapshot", "source_ids": ["fixture-source"], "provider": record["provider"],
        "entitlement_id": record["id"], "currency": "USD", "exchange": "XNYS",
        "point_in_time_verified": False, "adjustment": "split_adjusted_current_share_basis",
        "volume_representation": "exact_decimal_string_shares", "label": LABEL,
        "bars": [{**bar.__dict__, **{key: str(getattr(bar, key)) for key in ("open", "high", "low", "close", "volume")}}
                 for bar in bars]}
    path = tmp_path / "fixture-prices.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return data, bars, calendar, record, path


def test_retrospective_diagnostic_does_not_relabel_observations_or_release(tmp_path, synthetic):
    data, bars, calendar, rights, path = synthetic
    # Old final session: strict current-plan loader must still reject it.
    data["bars"] = data["bars"][:-1]
    path.write_text(json.dumps(data), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="stale"):
        load_price_snapshot(path, rights, AT)
    summary = validation.retrospective_diagnostic(path, rights, AT, tmp_path / "diagnostic")
    result = json.loads((tmp_path / "diagnostic/retrospective_diagnostic.json").read_text())
    assert summary["status"] == "retrospective_diagnostic_not_validation"
    assert path.read_bytes() == before
    assert result["released_score"] is None and result["release_validation_id"] is None
    for scenario in result["scenarios"]:
        train, held = scenario["training_cases"], scenario["held_out_cases"]
        assert train and held
        assert train[-1]["label_period_ended_at"] <= held[0]["hypothetical_decision_at"]
        assert all(case["label_available_at"] == AT for case in train + held)
        assert all(case["point_in_time_verified"] is False for case in train + held)
        assert sum(scenario["summary"]["held_out"]["outcomes"].values()) == len(held)
    with pytest.raises(FileExistsError):
        validation.retrospective_diagnostic(path, rights, AT, tmp_path / "diagnostic")


def test_changed_candidate_policy_invalidates_frozen_protocol(tmp_path, monkeypatch):
    root = validation.ROOT
    (tmp_path / "config").mkdir()
    (tmp_path / "config/validation_protocol.json").write_bytes((root / "config/validation_protocol.json").read_bytes())
    (tmp_path / "config/swing_candidate_policy.json").write_text("{}")
    monkeypatch.setattr(validation, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="changed after protocol freeze"):
        validation.protocol()


def test_diagnostic_screening_never_hides_unresolved_cases():
    screening = validation.protocol()["diagnostic"]["screening_only"]
    cases = [{"outcome": "target", "net_return_pct": "1", "comparator_net_return_pct": "0"}] * 40
    cases += [{"outcome": "ambiguous_same_bar", "net_return_pct": None}] * 10
    result = validation.partition_summary(cases, screening)
    assert result["cases"] == 50 and result["resolved"] == 40
    assert result["screening_passed"] is False
    assert result["screening_flags"]["unresolved_fraction"] is False
    assert result["release_validation_id"] is None


def test_research_artifact_contract_rejects_released_scores(tmp_path, synthetic):
    _, _, _, rights, path = synthetic
    validation.retrospective_diagnostic(path, rights, AT, tmp_path / "diagnostic")
    result = json.loads((tmp_path / "diagnostic/retrospective_diagnostic.json").read_text())
    result["released_score"] = 50
    with pytest.raises(jsonschema.ValidationError):
        validation.validate_artifact(result)


def test_news_outage_backoff_does_not_convert_failure_to_neutral(tmp_path, monkeypatch):
    monkeypatch.setattr(advance, "ROOT", tmp_path)
    collection = {"version": "1.0.0", "provider": "gdelt_project", "attribution": "Data from the GDELT Project (https://www.gdeltproject.org/).",
        "entitlement_id": "fixture-rights", "checked_at": AT, "status": "unavailable", "completed_topics": [],
        "failed_topics": [{"category": "company", "reason": "TimeoutError"}],
        "coverage": "bounded configured-query results, not exhaustive worldwide coverage", "poll_interval_seconds": 900,
        "provider_delay_seconds": None, "realtime_guaranteed": False, "articles": [], "warnings": [],
        "score_component": None, "model_status": "unvalidated"}
    path = tmp_path / "reports/private/advancement/fixture/news_metadata.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(collection), encoding="utf-8")
    cached, basis = advance.cached_news("2026-11-07T12:10:00Z")
    assert cached["status"] == "unavailable" and cached["score_component"] is None
    assert basis == "upstream_failure_15_minute_backoff"
    assert advance.cached_news("2026-11-07T12:16:00Z") == (None, None)


def test_forecast_is_immutable_and_must_precede_next_open(tmp_path, monkeypatch, synthetic):
    data, bars, calendar, _, _ = synthetic
    monkeypatch.setattr(validation, "now", lambda: AT)
    result = validation.register_forecast(data, bars, calendar, AT, tmp_path / "ledger")
    path = tmp_path / "ledger" / result["record_id"] / "forecast.json"
    before = path.read_bytes()
    assert validation.register_forecast(data, bars, calendar, AT, tmp_path / "ledger")["status"] == "already_recorded"
    assert path.read_bytes() == before
    monkeypatch.setattr(validation, "now", lambda: "2026-11-09T15:00:00Z")
    with pytest.raises(ValueError, match="before the next regular open"):
        validation.register_forecast(data, bars, calendar, "2026-11-09T15:00:00Z", tmp_path / "other")


def future_history(bars, calendar):
    sessions = calendar.review_window(bars[-1].session)["sessions"]
    future = []
    for session in sessions:
        last = bars[-1]
        future.append(Bar(session, last.close, last.close + 1, last.close - 1,
                          last.close, Decimal("1000.1"), LATER))
    return [replace(bar, available_at=LATER) for bar in bars] + future


def test_prospective_outcome_waits_for_all_five_and_uses_retrieval_time(tmp_path, monkeypatch, synthetic):
    data, bars, calendar, _, _ = synthetic
    monkeypatch.setattr(validation, "now", lambda: AT)
    ledger = tmp_path / "ledger"
    registration = validation.register_forecast(data, bars, calendar, AT, ledger)
    assert validation.settle_forecasts(data, bars, calendar, AT, ledger)[0]["status"] == "awaiting_five_session_outcome"
    history = future_history(bars, calendar)
    monkeypatch.setattr(validation, "now", lambda: LATER)
    result = validation.settle_forecasts(data, history, calendar, LATER, ledger)
    assert result[0]["status"] == "prospective_research_outcome"
    outcome_path = ledger / registration["record_id"] / "outcome.json"
    before = outcome_path.read_bytes()
    outcome = json.loads(before)
    assert outcome["scenarios"][0]["result"]["label_available_at"] == "2026-11-14T12:00:00+00:00"
    assert outcome["scenarios"][0]["result"]["label_period_ended_at"] == calendar.close("2026-11-13")
    assert validation.settle_forecasts(data, history, calendar, LATER, ledger)[0]["status"] == "already_settled"
    assert outcome_path.read_bytes() == before


def test_prospective_revision_cannot_silently_change_price_basis(tmp_path, monkeypatch, synthetic):
    data, bars, calendar, _, _ = synthetic
    monkeypatch.setattr(validation, "now", lambda: AT)
    ledger = tmp_path / "ledger"
    validation.register_forecast(data, bars, calendar, AT, ledger)
    history = future_history(bars, calendar)
    history[0] = replace(history[0], close=history[0].close + Decimal("0.01"))
    assert validation.settle_forecasts(data, history, calendar, LATER, ledger)[0]["status"] == "input_revision_detected_outcome_withheld"
    assert not list(ledger.rglob("outcome.json"))


def test_equivalent_decimal_volume_is_not_a_revision(synthetic):
    _, bars, _, _, _ = synthetic
    one = replace(bars[0], volume=Decimal("1000.00"))
    two = replace(bars[0], volume=Decimal("1000"))
    assert validation.value_fingerprint(one) == validation.value_fingerprint(two)


def test_provider_release_boundary_and_weekend():
    pending = advance.release_state("2026-11-07T01:00:00Z")
    assert pending["latest_completed_session"] == "2026-11-06"
    assert pending["basic_eligible_session"] == "2026-11-05"
    assert pending["next_action_at"] == "2026-11-07T05:02:00+00:00"
    assert advance.release_state("2026-11-07T05:02:00Z")["next_action_at"] is None


def test_workflow_lock_and_bounded_deferred_run(tmp_path, monkeypatch):
    monkeypatch.setattr(advance, "ROOT", tmp_path)
    monkeypatch.setattr(advance, "now", lambda: AT)
    calls = []
    def run_once(**kwargs):
        calls.append(kwargs)
        return {"status": "research_completed_models_unvalidated", "output_dir": str(tmp_path),
                "price_release": {"next_action_at": None}}
    monkeypatch.setattr(advance, "run_once", run_once)
    assert advance.main(["--no-news", "--no-fetch", "--defer-until-price"]) == 0
    assert len(calls) == 1
    assert not (tmp_path / "reports/private/advance_control/active.lock").exists()
    state = json.loads((tmp_path / "reports/private/advance_control/process_state.json").read_text())
    assert state["status"] == "finished"
    lock = tmp_path / "reports/private/advance_control/active.lock"
    lock.write_text("{}")
    with pytest.raises(SystemExit):
        advance.main(["--no-news"])
    assert lock.exists()


def test_deferred_wait_refreshes_only_once(tmp_path, monkeypatch):
    monkeypatch.setattr(advance, "ROOT", tmp_path)
    clock = {"now": "2026-11-07T05:01:59Z"}
    monkeypatch.setattr(advance, "now", lambda: clock["now"])
    monkeypatch.setattr(advance.time, "sleep", lambda seconds: clock.update(now="2026-11-07T05:02:01Z"))
    calls = []
    def run_once(**kwargs):
        calls.append(kwargs)
        return {"status": "waiting_for_basic_release", "output_dir": str(tmp_path),
                "price_release": {"next_action_at": "2026-11-07T05:02:00Z"}}
    monkeypatch.setattr(advance, "run_once", run_once)
    assert advance.main(["--no-news", "--defer-until-price"]) == 0
    assert len(calls) == 2


def test_initial_work_crossing_release_time_refreshes_without_extra_wait(tmp_path, monkeypatch):
    monkeypatch.setattr(advance, "ROOT", tmp_path)
    monkeypatch.setattr(advance, "now", lambda: "2026-11-07T05:03:00Z")
    monkeypatch.setattr(advance.time, "sleep", lambda _: pytest.fail("Already due; no additional sleep"))
    calls = []
    def run_once(**kwargs):
        calls.append(kwargs)
        return {"status": "waiting_for_basic_release", "output_dir": str(tmp_path),
                "price_release": {"next_action_at": "2026-11-07T05:02:00Z"}}
    monkeypatch.setattr(advance, "run_once", run_once)
    assert advance.main(["--no-news", "--defer-until-price"]) == 0
    assert len(calls) == 2
