"""ILLUSTRATIVE — NOT LIVE MARKET DATA. Freeze decisions before future outcomes."""

from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path

import pytest

from research import context_journal
from research.sec_report import ROOT
from research.swing_models import Bar, XnysCalendar
from research.validation import save_new

AT = "2026-11-07T12:00:00Z"
LATER = "2026-11-14T12:00:00Z"
LABEL = "ILLUSTRATIVE — NOT LIVE MARKET DATA"


@pytest.fixture
def case(tmp_path, monkeypatch):
    for name in ("context_policy.json", "trade_screen_policy.json"):
        path = tmp_path / "config" / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes((ROOT / "config" / name).read_bytes())
    monkeypatch.setattr(context_journal, "ROOT", tmp_path)
    monkeypatch.setattr(context_journal, "now", lambda: AT)
    monkeypatch.setattr(context_journal.peak_research, "settle_records", lambda *args: [])
    calendar = XnysCalendar("2026-01-01", "2026-12-31")
    sessions = calendar.sessions("2026-11-09", "2026-11-13")
    plan = {"reference_session": "2026-11-06", "reference_price": "100", "first_target_price": "104", "invalidation_price": "98",
        "timing": {"sessions": sessions, "review_at": calendar.close(sessions[-1])}}
    prediction = {"plan": plan, "engine": "illustrative-fixed-statistics"}
    screen = {"reference_session": "2026-11-06", "screen_decision": "stand_aside"}
    snapshot = {"symbol": "BE", "is_illustrative": True, "source_ids": ["illustrative-source"]}
    evidence = tmp_path / "reports/private/context/evidence.json"
    save_new(evidence, {"as_of": AT, "fixture_label": LABEL})
    base = tmp_path / "reports/private/base/forecast.json"
    save_new(base, {"record_id": "illustrative-base", "prediction": deepcopy(prediction), "fixture_label": LABEL})
    monkeypatch.setattr(context_journal.peak_research, "prospective_record", lambda *args: {"status": "already_recorded", "path": str(base.parent)})
    future = [Bar(day, Decimal(100), Decimal(105), Decimal(99), Decimal(104), Decimal(1000), LATER) for day in sessions]
    return {"calendar": calendar, "snapshot": snapshot, "prediction": prediction, "screen": screen,
        "evidence": evidence, "base": base, "future": future, "root": tmp_path}


def register(case):
    return context_journal.register(case["prediction"], case["screen"], case["snapshot"], [], case["calendar"], AT, case["evidence"])


def test_record_is_saved_before_entry_and_not_replaced_by_later_context(case):
    result = register(case)
    assert result["status"] == "prospective_context_record"
    path = Path(result["path"]) / "forecast.json"
    original = path.read_bytes()
    case["evidence"].write_text(json.dumps({"as_of": LATER}), encoding="utf-8")
    repeat = register(case)
    assert repeat["status"] == "already_recorded"
    assert path.read_bytes() == original
    record = json.loads(original)
    assert record["decision_scope"] == "price_cost_screen_only"
    assert record["paper_action"] == "cash"
    assert record["entry_at"].startswith("2026-11-09T14:30")
    assert record["earliest_basic_outcome_check_at"] == "2026-11-14T00:02:00-05:00"


def test_context_must_be_known_by_cutoff_and_forecast_must_match(case):
    case["evidence"].write_text(json.dumps({"as_of": LATER}), encoding="utf-8")
    with pytest.raises(ValueError, match="Future context"):
        register(case)
    case["evidence"].write_text(json.dumps({"as_of": AT}), encoding="utf-8")
    case["prediction"]["plan"]["first_target_price"] = "110"
    assert register(case)["status"] == "withheld"


def test_after_entry_registration_is_withheld(case, monkeypatch):
    monkeypatch.setattr(context_journal, "now", lambda: "2026-11-09T14:31:00Z")
    assert register(case)["status"] == "withheld"


def write_base_outcome(case, as_of=LATER):
    save_new(case["base"].with_name("outcome.json"), {"record_id": "illustrative-base", "as_of": as_of,
        "source_ids": ["illustrative-future-source"], "outcomes": [{"cost_bps_per_side": "25",
            "result": {"outcome": "target", "net_return_pct": "3", "label_available_at": LATER}}]})


@pytest.mark.parametrize("condition,expected", [
    ("missing_session", "awaiting_five_session_outcome"), ("before_close", "awaiting_five_session_outcome"),
    ("future_observation", "future_observation_withheld"), ("changed_evidence", "revision_withheld"),
    ("future_base_result", "future_outcome_withheld"), ("wrong_base_identity", "outcome_identity_withheld")])
def test_incomplete_future_or_changed_evidence_cannot_become_a_result(case, monkeypatch, condition, expected):
    register(case)
    cutoff, bars = LATER, case["future"]
    if condition == "missing_session":
        bars = bars[:-1]
    elif condition == "before_close":
        cutoff = "2026-11-13T20:00:00Z"
    elif condition == "future_observation":
        cutoff = "2026-11-14T11:59:00Z"
    elif condition == "changed_evidence":
        case["evidence"].write_text("changed after registration", encoding="utf-8")
    write_base_outcome(case, "2026-11-15T12:00:00Z" if condition == "future_base_result" else LATER)
    if condition == "wrong_base_identity":
        outcome = case["base"].with_name("outcome.json")
        data = json.loads(outcome.read_text(encoding="utf-8"))
        data["record_id"] = "different-forecast"
        outcome.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(context_journal, "now", lambda: cutoff)
    result = context_journal.settle(case["snapshot"], bars, case["calendar"], cutoff)
    assert result["context_records"][0]["status"] == expected
    assert not list((case["root"] / "reports/private/context_ledger").glob("*/outcome.json"))


def test_cash_is_zero_only_after_the_full_observation_period_and_result_is_immutable(case, monkeypatch):
    registered = register(case)
    pending = context_journal.settle(case["snapshot"], [], case["calendar"], AT)
    assert pending["context_records"][0]["status"] == "awaiting_five_session_outcome"
    write_base_outcome(case)
    monkeypatch.setattr(context_journal, "now", lambda: LATER)
    result = context_journal.settle(case["snapshot"], case["future"], case["calendar"], LATER)
    assert result["context_records"][0]["status"] == "observed_context_outcome"
    path = Path(registered["path"]) / "outcome.json"
    original = path.read_bytes()
    row = json.loads(original)
    assert row["outcomes"][0]["result"]["net_return_pct"] == "0"
    assert row["outcomes"][0]["result"]["label_available_at"] == LATER
    assert row["released_score"] is row["validation_id"] is None
    assert context_journal.settle(case["snapshot"], case["future"], case["calendar"], LATER)["context_records"][0]["status"] == "already_evaluated"
    assert path.read_bytes() == original
