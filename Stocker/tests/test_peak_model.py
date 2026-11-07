"""ILLUSTRATIVE — NOT LIVE MARKET DATA. Controlled synthetic paths only."""

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json

import jsonschema
import pytest

from research import peak_model as model, peak_simulation, peak_research
from research.swing_models import Bar, XnysCalendar

AT = "2026-11-07T12:00:00Z"
LABEL = "ILLUSTRATIVE — NOT LIVE MARKET DATA"


@pytest.fixture
def flat():
    calendar = XnysCalendar("2025-01-01", "2027-01-31")
    sessions = calendar.sessions("2025-10-01", "2026-11-06")[-260:]
    bars = [Bar(day, Decimal(100), Decimal(101), Decimal(99), Decimal(100), Decimal("1000.1"), calendar.close(day)) for day in sessions]
    return bars, calendar


def pool(bars, peak=2.4, peak_day=3, minimum=-.3, path=None):
    feature = model.features(bars)
    if path is None:
        path = [(0, .6, minimum, .4), (.4, 1.1, .1, .8), (.8, peak, .5, 1.9), (1.9, 2, 1.3, 1.5), (1.5, 1.8, 1, 1.4)]
    normalized = tuple(tuple(Decimal(str(value)) for value in row) for row in path)
    return [model.Sample(59 + index * 5, 64 + index * 5, feature, peak, peak_day, minimum, normalized) for index in range(40)]


def test_joint_peak_target_and_first_passage_timing(flat):
    bars, calendar = flat
    result = model.forecast(bars, calendar, AT, training_samples=pool(bars))
    assert result["peak"]["modal_bin_index"] == 4
    assert result["peak"]["modal_price_interval"] == ["104.0", "105.0"]
    assert result["plan"]["first_target_price"] == "104.25"
    assert result["peak"]["conditional_day_mode"] == 3
    assert result["execution"][0]["experimental_first_target_hit_sessions"] == [3, 3]
    assert sum(row["estimated_mass"] for row in result["peak"]["joint_cells"]) == pytest.approx(1)
    assert result["peak"]["calibrated_probability"] is None
    assert result["released_price_plan"] is None
    assert result["plan"]["timing"]["expected_target_sessions"] is None


def test_target_arrival_day_and_peak_formation_day_are_distinct(flat):
    bars, calendar = flat
    path = [(0, .6, -.3, .4), (.4, 1.1, .1, .8), (.8, 2.2, .5, 1.9), (1.9, 2.2, 1.3, 1.5), (1.5, 2.4, 1, 1.4)]
    result = model.forecast(bars, calendar, AT, training_samples=pool(bars, peak_day=5, path=path))
    assert result["peak"]["conditional_day_mode"] == 5
    assert result["execution"][0]["experimental_first_target_hit_sessions"] == [3, 3]


def test_future_supervised_paths_do_not_enter_neighbors(flat):
    bars, calendar = flat
    earlier = model.forecast(bars, calendar, AT, training_samples=pool(bars))
    extra = model.Sample(999, 1004, model.features(bars), 999, 5, -100, pool(bars)[0].normalized_path)
    after = model.forecast(bars, calendar, AT, training_samples=pool(bars) + [extra])
    assert earlier == after


def test_future_price_changes_do_not_change_fixed_simulation(flat):
    bars, calendar = flat
    before = peak_simulation.forecast(bars, calendar, AT, query_index=220)
    changed = bars[:221] + [replace(bar, close=Decimal(999), high=Decimal(1000)) for bar in bars[221:]]
    assert peak_simulation.forecast(changed, calendar, AT, query_index=220) == before
    assert before["training_paths"] == 0
    assert before["simulated_paths"] == model.policy()["simulation"]["paths"]
    assert before["parameter_effective_observations"] < before["simulated_paths"]
    assert before["statistical_parameters"]["intraday_log_sigma"] > 0
    with pytest.raises(ValueError, match="not yet observed"):
        peak_simulation.forecast(bars, calendar, "2026-11-05T12:00:00Z")


def test_no_positive_modal_target_does_not_force_a_long_plan(flat):
    bars, calendar = flat
    path = [(-.5, -.1, -1, -.4)] * 5
    result = model.forecast(bars, calendar, AT, training_samples=pool(bars, peak=-.1, minimum=-1, path=path))
    assert result["plan"] is None and result["profit_opportunity"] is None
    assert model.combined_score(result, {}, {})["experimental_score"] is None


def test_upside_bonus_accounts_for_early_loss_and_execution_ambiguity(flat):
    bars, calendar = flat
    good = model.forecast(bars, calendar, AT, training_samples=pool(bars))
    stopped = model.forecast(bars, calendar, AT, training_samples=pool(bars, minimum=-1))
    assert Decimal(stopped["profit_opportunity"]["experimental_points"]) < Decimal(good["profit_opportunity"]["experimental_points"])
    path = [(0, 2.4, -1, .4)] + [(0, 1, -.5, .4)] * 4
    ambiguous = model.forecast(bars, calendar, AT, training_samples=pool(bars, peak_day=1, minimum=-1, path=path))
    execution = ambiguous["execution"][0]
    assert execution["estimated_target_before_stop_mass_bounds"] == pytest.approx([0, 1])
    assert Decimal(execution["estimated_net_return_pct_bounds"][0]) < 0
    assert Decimal(execution["estimated_net_return_pct_bounds"][1]) > 0
    assert execution["experimental_first_target_hit_sessions"] is None


def test_more_net_upside_adds_opportunity_points_and_costs_reduce_ev(flat):
    bars, calendar = flat
    small = model.forecast(bars, calendar, AT, training_samples=pool(bars))
    larger = model.forecast(bars, calendar, AT, training_samples=pool(bars, peak=4.4))
    assert Decimal(larger["profit_opportunity"]["experimental_points"]) > Decimal(small["profit_opportunity"]["experimental_points"])
    assert Decimal(small["execution"][1]["estimated_net_return_pct_bounds"][0]) < Decimal(small["execution"][0]["estimated_net_return_pct_bounds"][0])


def test_score_keeps_news_40_and_missing_inputs_withhold_total(flat):
    bars, calendar = flat
    prediction = model.forecast(bars, calendar, AT, training_samples=pool(bars))
    features = {key: {"value": "1", "source_ids": ["synthetic"], "methodology_id": "fixture"} for key in ("fundamentals", "market_sector")}
    assert model.combined_score(prediction, features, {"status": "unavailable"})["score"] is None
    bullish = model.combined_score(prediction, features, {"status": "research_only_unvalidated", "experimental_component": "40"})
    bearish = model.combined_score(prediction, features, {"status": "research_only_unvalidated", "experimental_component": "0"})
    assert bullish["experimental_score"] - bearish["experimental_score"] == 40
    assert bullish["score"] is None
    assert sum(model.policy()["score_weights"].values()) == 100


def test_small_analog_dataset_and_ood_state_are_withheld(flat):
    bars, calendar = flat
    with pytest.raises(ValueError, match="Insufficient prior"):
        model.forecast(bars[:150], calendar, AT)
    far = [replace(row, features=tuple(value + 1000 for value in row.features)) for row in pool(bars)]
    with pytest.raises(ValueError, match="outside observed analog"):
        model.forecast(bars, calendar, AT, training_samples=far)


def test_analog_real_data_gate_does_not_infer_training_rights(tmp_path, monkeypatch):
    monkeypatch.setattr(peak_research, "ROOT", tmp_path)
    folder = tmp_path / "reports/private/test"
    folder.mkdir(parents=True)
    rights = {"id": "fixture", "provider": "fixture", "plan": LABEL, "is_illustrative": True,
        "reviewer": "fixture", "reviewed_at": "2026-01-01T00:00:00Z", "review_due_at": "2027-01-01T00:00:00Z",
        "monthly_cost_usd": 0, "evidence_url": "https://example.invalid/fixture", "uses": {"model_training": "unknown"}}
    entitlement = folder / "rights.json"
    entitlement.write_text(json.dumps(rights))
    # No price file is read and no real-data model fitting occurs after the denied gate.
    assert peak_research.main(["--prices", str(folder / "missing.json"), "--price-entitlement", str(entitlement),
        "--engine", "historical_analogs", "--output-dir", str(folder / "out")]) == 2


def test_peak_artifact_contract_rejects_calibrated_or_released_numbers(flat):
    bars, calendar = flat
    prediction = model.forecast(bars, calendar, AT, training_samples=pool(bars))
    result = {"schema_version": "1.0.0", "status": "research_only_unvalidated", "as_of": AT,
        "methodology_id": model.policy()["id"], "engine": "historical_analogs",
        "registration": {"registered_at": AT, "policy_sha256": "fixture", "price_snapshot_sha256": "fixture", "code_sha256": {}},
        "current_candidate": prediction, "diagnostic": {"status": "retrospective_diagnostic_not_validation",
            "point_in_time_verified": False, "partitions": {}, "validation_id": None},
        "released_price_plan": None, "released_score": None, "released_expected_target_sessions": None, "validation_id": None}
    peak_research.validate(result)
    bad = deepcopy(result)
    bad["current_candidate"]["peak"]["calibrated_probability"] = .9
    with pytest.raises(jsonschema.ValidationError):
        peak_research.validate(bad)
    result["released_score"] = 50
    with pytest.raises(jsonschema.ValidationError):
        peak_research.validate(result)


def test_peak_prospective_records_wait_and_preserve_observed_outcome_time(tmp_path, monkeypatch, flat):
    bars, calendar = flat
    actual_root = peak_research.ROOT
    for sub in ("config", "schemas"):
        (tmp_path / sub).mkdir()
    for relative in ("config/peak_model_policy.json", "schemas/peak_research.schema.json"):
        (tmp_path / relative).write_bytes((actual_root / relative).read_bytes())
    monkeypatch.setattr(peak_research, "ROOT", tmp_path)
    monkeypatch.setattr(peak_research, "now", lambda: AT)
    prediction = model.forecast(bars, calendar, AT, training_samples=pool(bars))
    data = {"symbol": "BE", "is_illustrative": True, "snapshot_id": "synthetic", "source_ids": [LABEL], "point_in_time_verified": False}
    recorded = peak_research.prospective_record(prediction, data, bars, calendar, AT)
    forecast_path = tmp_path / "reports/private/peak_ledger" / recorded["record_id"] / "forecast.json"
    before = forecast_path.read_bytes()
    assert peak_research.prospective_record(prediction, data, bars, calendar, AT)["status"] == "already_recorded"
    assert forecast_path.read_bytes() == before
    assert peak_research.settle_records(data, bars, calendar, AT)[0]["status"] == "awaiting_five_session_outcome"
    later = "2026-11-14T12:00:00Z"
    future = [Bar(day, Decimal(100), Decimal(101), Decimal(99), Decimal(100), Decimal(1000), later)
              for day in prediction["plan"]["timing"]["sessions"]]
    monkeypatch.setattr(peak_research, "now", lambda: later)
    result = peak_research.settle_records(data, bars + future, calendar, later)
    assert result[0]["status"] == "prospective_research_outcome"
    outcome = json.loads(forecast_path.with_name("outcome.json").read_text(encoding="utf-8"))
    assert outcome["outcomes"][0]["result"]["label_available_at"] == later
    assert outcome["validation_id"] is None
    assert peak_research.settle_records(data, bars + future, calendar, later)[0]["status"] == "already_settled"
