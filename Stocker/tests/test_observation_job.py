"""ILLUSTRATIVE — NOT LIVE MARKET DATA. Fake jobs, outcomes and API calls."""

from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path

import pytest

from research import fetch_prices, observe, outcome_report
from research.local_files import LockBusy, atomic_json, exclusive_lock
from research.sec_report import ROOT
from research.swing_models import Bar, XnysCalendar, replay_five_sessions
from research.validation import digest, save_new

AT = "2026-11-07T14:00:00Z"
DUE = "2026-11-14T05:02:00Z"
LABEL = "ILLUSTRATIVE — NOT LIVE MARKET DATA"


@pytest.fixture
def job_case(tmp_path, monkeypatch):
    schema = tmp_path / "schemas/observation_job.schema.json"
    schema.parent.mkdir()
    schema.write_bytes((ROOT / "schemas/observation_job.schema.json").read_bytes())
    for folder, names in (("research", observe.CORE_FILES), ("config", observe.POLICY_FILES)):
        for name in names:
            path = tmp_path / folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((ROOT / folder / name).read_bytes())
    monkeypatch.setattr(observe, "ROOT", tmp_path)
    monkeypatch.setattr(observe, "now", lambda: AT)
    monkeypatch.setattr(observe, "key_from_user", lambda: "ILLUSTRATIVE-KEY-NEVER-LOG")
    calendar = XnysCalendar("2026-01-01", "2026-12-31")
    sessions = calendar.sessions("2026-11-09", "2026-11-13")
    plan = {"reference_session": "2026-11-06", "reference_price": "100", "first_target_price": "104", "invalidation_price": "98",
        "timing": {"sessions": sessions, "review_at": calendar.close(sessions[-1])}}
    base_path = tmp_path / ("reports/private/peak_ledger/" + "a" * 24 + "/forecast.json")
    base = {"record_id": "a" * 24, "symbol": "BE", "is_illustrative": False, "fixture_label": LABEL, "recorded_at": AT,
        "prediction": {"plan": plan, "peak": {"modal_price_interval": ["104", "106"], "conditional_day_mode": 3}}}
    save_new(base_path, base)
    context_path = tmp_path / ("reports/private/context_ledger/" + "b" * 24 + "/forecast.json")
    save_new(context_path, {"record_id": "b" * 24, "symbol": "BE", "is_illustrative": False, "fixture_label": LABEL,
        "recorded_at": AT, "sessions": sessions, "earliest_basic_outcome_check_at": DUE, "base_forecast_path": str(base_path), "paper_action": "cash"})
    legacy = tmp_path / ("reports/private/prospective_ledger/" + "c" * 24 + "/forecast.json")
    save_new(legacy, {"record_id": "c" * 24, "symbol": "BE", "is_illustrative": False, "fixture_label": LABEL, "candidate": plan})
    rights = tmp_path / "reports/private/market_data/fixture/price_entitlement.json"
    save_new(rights, fetch_prices.confirmed_record(fetch_prices.source_record(), True, AT))
    monkeypatch.setattr(observe, "latest_training_snapshot", lambda: (rights.with_name("prices.json"), rights))
    path = observe.prepare_job()
    data = {"is_illustrative": False, "source_ids": [LABEL], "snapshot_id": "illustrative", "retrieved_at": DUE}
    bars = [Bar(day, Decimal(100), Decimal(105 if index == 2 else 102), Decimal(99), Decimal(101), Decimal(1000), DUE) for index, day in enumerate(sessions)]
    return {"path": path, "job": observe.read(path), "data": data, "bars": bars, "calendar": calendar, "root": tmp_path}


def mocked_settle(case, job, data, bars, calendar, cutoff):
    for target in job["targets"]:
        if not Path(target["path"]).with_name("outcome.json").exists():
            save_new(Path(target["path"]).with_name("outcome.json"), {"record_id": target["record_id"], "as_of": cutoff,
                "source_ids": [LABEL], "outcomes": [{"cost_bps_per_side": cost,
                    "result": replay_five_sessions(observe.read(target["path"]).get("candidate", case["job_plan"]), bars, calendar, Decimal(cost))} for cost in ("10", "25")]})
    return [{"record_id": row["record_id"], "status": "observed_context_outcome"} for row in job["targets"]]


def test_preparation_is_idempotent_and_binds_first_forecasts_and_due_time(job_case):
    path, job = job_case["path"], job_case["job"]
    original = path.read_bytes()
    assert observe.prepare_job() == path and path.read_bytes() == original
    assert len(job["targets"]) == 3 and job["due_at"] == DUE
    assert job["released_score"] is None
    assert observe.check_job(path)["job_id"] == job["job_id"]


def test_before_due_does_not_read_prices_call_api_or_disable_task(job_case, monkeypatch):
    def forbidden(*args):
        pytest.fail("Price access is premature")
    monkeypatch.setattr(observe, "snapshot_for_job", forbidden)
    result = observe.run(job_case["path"], clock=lambda: AT, disable=forbidden)
    assert result["status"] == "not_due" and result["network_attempts"] == 0
    saved = (job_case["path"].parent / "state.json").read_text(encoding="utf-8")
    assert "ILLUSTRATIVE-KEY-NEVER-LOG" not in saved


def test_due_run_writes_readable_result_once_and_disables_without_second_api(job_case, monkeypatch):
    job_case["job_plan"] = observe.read(next(row["path"] for row in job_case["job"]["targets"] if row["kind"] == "peak"))["prediction"]["plan"]
    calls, disabled = [], []
    def snapshot(*args):
        calls.append(1)
        return job_case["data"], job_case["bars"], job_case["calendar"]
    monkeypatch.setattr(observe, "snapshot_for_job", snapshot)
    monkeypatch.setattr(observe, "settle_targets", lambda *args: mocked_settle(job_case, *args))
    first = observe.run(job_case["path"], clock=lambda: DUE, disable=lambda name: disabled.append(name) or "disabled")
    assert first["status"] == "complete"
    report = Path(first["result_dir"]) / "observation.json"
    original = report.read_bytes()
    second = observe.run(job_case["path"], clock=lambda: "2026-11-14T05:17:00Z", disable=lambda name: "disabled")
    assert second["status"] == "complete" and calls == [1] and len(disabled) == 1
    assert report.read_bytes() == original
    result = json.loads(original)
    assert result["observed_records"] == 1 and result["actual_broker_execution"] is False
    assert result["peak_interval_contains_observed_high"] is True
    assert result["original_screen_paper_action"] == "cash"
    assert all(row["original_screen_action_return_pct_bounds"] == ["0", "0"] for row in result["cost_scenarios"])
    assert "1건" in (report.with_name("report.md")).read_text(encoding="utf-8")


def test_retry_spacing_and_network_budget_are_persisted(job_case, monkeypatch):
    calls = []
    def delayed(record, cutoff, state, persist, key):
        calls.append(cutoff)
        state["network_attempts"] += 1
        state["last_network_at"] = cutoff
        persist()
        raise observe.RetryLater("PRICE_SESSION_NOT_COMPLETE")
    monkeypatch.setattr(observe, "snapshot_for_job", delayed)
    no_disable = lambda _: pytest.fail("Transient delay must remain scheduled")
    first = observe.run(job_case["path"], clock=lambda: DUE, disable=no_disable)
    assert first["status"] == "retry_wait" and first["network_attempts"] == 1
    observe.run(job_case["path"], clock=lambda: "2026-11-14T05:16:00Z", disable=no_disable)
    assert len(calls) == 1
    third = observe.run(job_case["path"], clock=lambda: "2026-11-14T05:17:00Z", disable=no_disable)
    assert third["network_attempts"] == 2 and len(calls) == 2


@pytest.mark.parametrize("condition", ["attempts", "window"])
def test_retry_exhaustion_stops_network_and_disables(job_case, monkeypatch, condition):
    atomic_json(job_case["path"].with_name("state.json"), {"status": "retry_wait", "network_attempts": 97 if condition == "attempts" else 1,
        "first_due_execution_at": "2026-11-13T05:02:00Z" if condition == "window" else DUE, "last_network_at": None})
    monkeypatch.setattr(observe, "snapshot_for_job", lambda *args: pytest.fail("Exhausted budget cannot call API"))
    result = observe.run(job_case["path"], clock=lambda: DUE, disable=lambda _: "disabled")
    assert result["status"] == "exhausted" and result["task_control"] == "disabled"


@pytest.mark.parametrize("condition", ["forecast", "code", "policy"])
def test_changed_original_or_calculation_is_withheld_before_network(job_case, monkeypatch, condition):
    path = Path(job_case["job"]["targets"][0]["path"]) if condition == "forecast" else job_case["root"] / ("research/swing_models.py" if condition == "code" else "config/peak_model_policy.json")
    path.write_text("modified fixture", encoding="utf-8")
    monkeypatch.setattr(observe, "snapshot_for_job", lambda *args: pytest.fail("Changed input must not call network"))
    result = observe.run(job_case["path"], clock=lambda: DUE, disable=lambda _: "disabled")
    assert result["status"] == "withheld" and result["network_attempts"] == 0


def test_overlapping_worker_is_rejected_and_lock_releases(job_case):
    with exclusive_lock(job_case["path"].with_name("worker.lock")):
        assert observe.run(job_case["path"])["status"] == "already_running"
    assert observe.run(job_case["path"], clock=lambda: AT)["status"] == "not_due"


def test_core_settlement_filter_ignores_other_records(tmp_path):
    from research import peak_research
    root = tmp_path / "other/forecast.json"
    save_new(root, {"record_id": "unselected"})
    assert peak_research.settle_records({}, [], None, AT, ledger_dir=tmp_path, record_ids={"selected"}) == []


def test_atomic_immutable_write_cannot_replace_existing(tmp_path):
    path = tmp_path / "immutable.json"
    atomic_json(path, {"fixture_label": LABEL})
    with pytest.raises(FileExistsError):
        atomic_json(path, {"changed": True})
    assert json.loads(path.read_text(encoding="utf-8"))["fixture_label"] == LABEL
