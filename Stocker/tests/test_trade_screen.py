"""ILLUSTRATIVE — NOT LIVE MARKET DATA. Cost gates and causal selection."""

from copy import deepcopy
from decimal import Decimal
import json

import jsonschema
import pytest

from research import learned_research, trade_evaluation, trade_readiness, trade_screen

AT = "2026-11-07T12:00:00Z"


def prediction():
    return {"status": "research_only_unvalidated", "methodology_id": "illustrative-paths", "as_of": AT,
        "reference_session": "2026-11-06", "reference_price": "100", "engine": "supervised_ridge_residual_paths",
        "effective_paths": 40, "released_score": None, "validation_id": None,
        "plan": {"reference_price": "100", "first_target_price": "110", "invalidation_price": "95",
            "reference_session": "2026-11-06", "as_of": AT, "timing": {"expires_at": "2026-11-09T21:00:00Z"}},
        "execution": [{"cost_bps_per_side": cost, "estimated_net_return_pct_bounds": ["1", "2"],
            "estimated_target_before_stop_mass_bounds": [.6, .8], "experimental_first_target_hit_sessions": [2, 4]}
            for cost in ("10", "25")]}


def screen(value):
    return trade_screen.assess(value, as_of=AT, source_ids=["illustrative-fixture"], is_illustrative=True, mode="current_research")


def test_candidate_keeps_uncalibrated_and_released_values_null():
    result = screen(prediction())
    assert result["screen_decision"] == "numerical_candidate"
    assert result["released_score"] is result["released_price_plan"] is result["calibrated_probability"] is None
    metrics = result["metrics"]
    assert Decimal(metrics[1]["net_reference_to_target_pct"]) < Decimal(metrics[0]["net_reference_to_target_pct"])
    assert Decimal(metrics[1]["net_reward_risk"]) > 1


def test_nearby_positive_target_can_be_negative_after_cost():
    value = prediction()
    value["plan"]["first_target_price"] = "100.1"
    result = screen(value)
    assert result["screen_decision"] == "stand_aside"
    assert "NONPOSITIVE_NET_TARGET_UPSIDE" in result["reason_codes"]
    assert "INSUFFICIENT_NET_REWARD_RISK" in result["reason_codes"]


def test_optimistic_same_bar_bound_cannot_hide_negative_conservative_ev():
    value = prediction()
    value["execution"][1]["estimated_net_return_pct_bounds"] = ["-1", "10"]
    result = screen(value)
    assert result["screen_decision"] == "stand_aside"
    assert "NONPOSITIVE_ESTIMATED_PATH_EV" in result["reason_codes"]
    value["execution"][1]["estimated_net_return_pct_bounds"] = ["0", "10"]
    assert "NONPOSITIVE_ESTIMATED_PATH_EV" in screen(value)["reason_codes"]


def test_missing_timing_low_support_and_expired_current_reference():
    value = prediction()
    value["effective_paths"] = 10
    value["execution"][1]["experimental_first_target_hit_sessions"] = None
    value["plan"]["timing"]["expires_at"] = "2026-11-06T21:00:00Z"
    reasons = screen(value)["reason_codes"]
    assert {"INSUFFICIENT_EFFECTIVE_SUPPORT", "TARGET_TIMING_UNAVAILABLE", "EXPIRED_REFERENCE_PLAN"} <= set(reasons)
    historical = trade_screen.assess(value, as_of=AT, source_ids=["fixture"], is_illustrative=True, mode="historical_diagnostic")
    assert "EXPIRED_REFERENCE_PLAN" not in historical["reason_codes"]


def test_simulation_path_count_is_not_observed_support():
    value = prediction()
    value.update(engine="fixed_ewma_simulation", simulated_paths=100000, parameter_effective_observations=10)
    assert "INSUFFICIENT_EFFECTIVE_SUPPORT" in screen(value)["reason_codes"]


@pytest.mark.parametrize("mutation", ["nonfinite", "missing_cost", "duplicate_cost", "mismatched_reference", "invalid_timing"])
def test_invalid_inputs_withhold_instead_of_zero_score(mutation):
    value = prediction()
    if mutation == "nonfinite":
        value["execution"][1]["estimated_net_return_pct_bounds"] = ["NaN", "1"]
    elif mutation == "missing_cost":
        value["execution"].pop()
    elif mutation == "duplicate_cost":
        value["execution"].append(deepcopy(value["execution"][0]))
    elif mutation == "mismatched_reference":
        value["plan"]["reference_session"] = "2026-11-05"
    else:
        value["execution"][1]["experimental_first_target_hit_sessions"] = [4, 2]
    result = screen(value)
    assert result["screen_decision"] == "unavailable"
    assert result["released_score"] is None


def test_screen_does_not_select_on_realized_future_outcomes():
    value = prediction()
    before = screen(value)
    # Untrusted extra labels are not arguments used by the screen.
    value.update(realized_future_return_pct="-99", actual_peak_day=5, future_news="fabricated fixture")
    assert screen(value) == before


def test_rejected_cases_keep_denominator_and_forecast_failures_remain_missing():
    accepted, rejected = screen(prediction()), screen(None)
    rejected.update(screen_decision="stand_aside", reason_codes=["NONPOSITIVE_ESTIMATED_PATH_EV"])
    good = {"outcome": "target", "net_return_pct": "10"}
    bad = {"outcome": "stop", "net_return_pct": "-10"}
    cash = {"outcome": "stand_aside_no_long_plan", "net_return_pct": "0"}
    rows = [{"screen": accepted, "realized": {"25": {"unfiltered": good, "screened": good}}},
        {"screen": rejected, "realized": {"25": {"unfiltered": bad, "screened": cash}}},
        {"unavailable_reason": "OOD", "realized": {}}]
    result = trade_evaluation.summarize(rows, ["25"])
    assert result["planned_cases"] == 3 and result["comparable_forecasts"] == 2
    assert result["unavailable_forecast_or_screen"] == 1
    assert result["costs"][0]["models"]["screened"]["mean_net_return_pct_bounds"] == ["5", "5"]
    assert result["costs"][0]["models"]["unfiltered"]["mean_net_return_pct_bounds"] == ["0", "0"]


def test_shared_execution_ambiguity_cancels_in_paired_difference():
    original = {"outcome": "ambiguous_same_bar", "entry": "100", "stop": "90", "target": "110", "net_return_pct": None}
    assert trade_evaluation.paired_change_bounds({"screen_decision": "numerical_candidate"}, original, "25") == (Decimal(0), Decimal(0))
    low, high = trade_evaluation.paired_change_bounds({"screen_decision": "stand_aside"}, original, "25")
    assert low < 0 < high
    with pytest.raises(ValueError, match="cannot be cash"):
        trade_evaluation.paired_change_bounds({"screen_decision": "unavailable"}, original, "25")


def test_unknown_screen_execution_and_empty_aggregate_are_not_cash():
    unknown = trade_evaluation.screened_execution({"screen_decision": "unavailable"}, {"outcome": "target", "net_return_pct": "10"})
    assert unknown == {"outcome": "unavailable", "net_return_pct": None}
    evaluation, _, generation = trade_evaluation.protocol()
    rows = [{"scenario": item["id"], "seed": seed, "status": "unavailable", "summary": None}
        for item in generation["scenarios"] for seed in evaluation["fresh_seeds"]]
    result = trade_evaluation.aggregate(rows, evaluation, generation)
    assert result[0]["completed_seeds"] == 0
    assert result[0]["costs"][0]["models"]["screened"]["mean_of_seed_return_pct_bounds"] is None
    assert result[0]["costs"][0]["all_cash_after_screen"] is False


def test_fresh_seeds_and_explicit_research_authorization_preserve_old_audit():
    evaluation, audit, generation = trade_evaluation.protocol()
    assert not set(evaluation["fresh_seeds"]) & set(audit["seeds"])
    assert not set(evaluation["fresh_seeds"]) & set(generation["seeds"])
    assert trade_screen.policy()["progress_authorization"]["research_progress_authorized_despite_inconclusive_sample_audit"] is True
    assert trade_screen.policy()["progress_authorization"]["sample_stability_assessment_overridden"] is False


def test_latest_default_ignores_older_diagnostic_folder_without_price_parse(tmp_path, monkeypatch):
    monkeypatch.setattr(learned_research, "ROOT", tmp_path)
    root = tmp_path / "reports/private/market_data"
    for name in ("diagnostic-20261107T012438339097Z", "20261107T050202630929Z"):
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "price_entitlement.json").write_text("fixture")
        (folder / "prices.json").write_text("INVALID JSON; selection must not parse prices")
    prices, rights = learned_research.latest_training_snapshot()
    assert prices.parent.name == "20261107T050202630929Z"
    assert rights.parent == prices.parent


def test_schema_rejects_released_scores():
    root = trade_readiness.ROOT
    schema = json.loads((root / "schemas/trade_screen.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator({"$ref": "#/$defs/screen", "$defs": schema["$defs"]})
    result = screen(prediction())
    validator.validate(result)
    result["released_score"] = 70
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(result)
