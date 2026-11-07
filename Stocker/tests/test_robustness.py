"""ILLUSTRATIVE — NOT LIVE MARKET DATA. Matched simulations and failure cases."""

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

import numpy as np
import jsonschema
import pytest

from research import learned_peak as learned, peak_model as base, robustness


@pytest.fixture(scope="module")
def synthetic():
    settings = robustness.protocol()
    scenario = next(row for row in settings["scenarios"] if row["id"] == "strong")
    bars, calendar, stress_index = robustness.generate(scenario, settings["seeds"][0], settings)
    return bars, calendar, stress_index, settings


def test_matched_noise_is_identical_across_signal_strengths(synthetic):
    strong, calendar, _, settings = synthetic
    null = next(row for row in settings["scenarios"] if row["id"] == "null")
    flat, _, _ = robustness.generate(null, settings["seeds"][0], settings, calendar)
    assert robustness.innovations(77011, 12) == robustness.innovations(77011, 12)
    assert robustness.innovations(77011, 12) != robustness.innovations(77023, 12)
    for index in (1, 100, 600, 1000):
        assert float(strong[index].open / strong[index - 1].close) == pytest.approx(float(flat[index].open / flat[index - 1].close), abs=1e-14)
        assert float(strong[index].high / max(strong[index].open, strong[index].close)) == pytest.approx(float(flat[index].high / max(flat[index].open, flat[index].close)), abs=1e-14)


@pytest.mark.parametrize("name", ["reversal", "volatility_shift"])
def test_regime_change_begins_only_after_first_heldout_reference(synthetic, name):
    normal, calendar, stress_index, settings = synthetic
    scenario = next(row for row in settings["scenarios"] if row["id"] == name)
    changed, _, actual = robustness.generate(scenario, settings["seeds"][0], settings, calendar)
    assert actual == stress_index
    assert normal[:stress_index] == changed[:stress_index]
    assert normal[stress_index:] != changed[stress_index:]
    train, calibration, held, _ = learned.partitions(base.samples(normal), learned.policy())
    assert held[0].reference_index + 1 == stress_index
    assert max(row.label_end_index for row in calibration) < held[0].reference_index


def test_volume_ablation_does_not_use_excluded_feature_or_its_support(synthetic):
    bars, _, _, _ = synthetic
    settings = deepcopy(learned.policy())
    settings["active_feature_indices"] = list(range(7))
    train, calibration, held, _ = learned.partitions(base.samples(bars), settings)
    fitted = learned.fit(train, calibration, settings)
    altered_train = [replace(row, features=(*row.features[:7], 1e10)) for row in train]
    altered_cal = [replace(row, features=(*row.features[:7], -1e10)) for row in calibration]
    other = learned.fit(altered_train, altered_cal, settings)
    assert np.array_equal(fitted.center, other.center)
    assert np.array_equal(fitted.coefficients, other.coefficients)
    assert np.array_equal(fitted.residuals, other.residuals)
    row = held[0]
    assert np.array_equal(fitted.paths(row.features, row.reference_index), fitted.paths((*row.features[:7], 1e20), row.reference_index))
    assert np.all(fitted.coefficients[[8, 16]] == 0)
    assert fitted.artifact()["active_feature_indices"] == list(range(7))


def test_same_bar_profit_bounds_include_stop_and_target_and_cost():
    outcome = {"outcome": "ambiguous_same_bar", "entry": "100", "stop": "90", "target": "110", "net_return_pct": None}
    low, high = robustness.return_bounds(outcome, "10")
    assert low < -10 and 0 < high < 10
    expensive = robustness.return_bounds(outcome, "25")
    assert expensive[0] < low and expensive[1] < high
    assert robustness.return_bounds({"outcome": "untradeable_open_gap"}, "10") == (Decimal(0), Decimal(0))
    with pytest.raises(ValueError, match="unresolved"):
        robustness.return_bounds({"outcome": "unavailable", "net_return_pct": None}, "10")
    result = robustness.execution_summary([outcome], [None], "10")
    assert result["outcome_counts"] == {"ambiguous_same_bar": 1}
    assert result["observed_target_before_stop_frequency_bounds"] == [0, 1]
    assert result["mean_net_return_pct_bounds"] == [str(low), str(high)]


def test_missing_timing_is_counted_and_is_not_a_failed_target():
    outcomes = [{"outcome": "target", "net_return_pct": "2", "hit_session": 3}] * 2
    prediction = {"execution": [{"experimental_first_target_hit_sessions": [2, 4]}]}
    result = robustness.execution_summary(outcomes, [prediction, None], "10")
    assert result["target_timing_evaluable_hits"] == 1
    assert result["target_hits_missing_timing_estimate"] == 1
    assert result["target_hit_midpoint_mae_sessions"] == 0
    assert result["target_hit_interval_contains_frequency"] == 1


def test_failed_runs_remain_visible_in_aggregation():
    settings = robustness.protocol()
    records = [{"scenario": item["id"], "seed": seed, "status": "unavailable", "paired_summary": None}
        for item in settings["scenarios"] for seed in settings["seeds"]]
    result = robustness.aggregate(records, settings)
    assert len(result) == len(settings["scenarios"])
    assert result[0]["completed_seeds"] == 0
    assert result[0]["failed_runs"] == settings["seeds"]
    assert result[0]["models"]["with_volume"]["mean_of_seed_metrics"] is None


def test_common_cohort_excludes_unavailable_variant_from_every_model():
    def cases():
        result = []
        for index in range(3):
            metric = {"peak_median_absolute_error_atr": float(index + 1), "joint_brier": 1.0}
            names = ("learned", "fixed_ewma", "unconditional_training")
            result.append({"reference_index": index, "reference_session": str(index), "actual_peak_atr": 2, "actual_peak_day": 3,
                "metrics": {name: metric.copy() for name in names}, "predictions": {},
                "execution": {cost: {name: {"outcome": "stand_aside_no_long_plan", "net_return_pct": "0"}
                    for name in ("learned", "fixed_ewma", "atr_baseline", "five_session_hold")} for cost in ("10", "25")}})
        return result
    full, ablated = cases(), cases()
    ablated[1]["metrics"].pop("learned")
    full[1]["metrics"]["fixed_ewma"]["peak_median_absolute_error_atr"] = 999
    summary = robustness.paired_summary({"with_volume": {"cases": full}, "without_volume": {"cases": ablated}})
    assert summary["held_out_cases"] == 3
    assert summary["common_forecastable_cases"] == 2
    for item in summary["models"].values():
        assert item["common_cohort_means"]["peak_median_absolute_error_atr"] == 2
    assert summary["execution"][0]["models"]["with_volume"]["cases"] == 2


def test_contract_blocks_released_results_and_omitted_runs():
    settings = robustness.protocol()
    records = [{"scenario": item["id"], "seed": seed, "status": "unavailable", "paired_summary": None,
        "reason": "controlled failure", "artifacts": {}} for item in settings["scenarios"] for seed in settings["seeds"]]
    artifact = {"schema_version": "1.0.0", "status": "research_only_unvalidated", "as_of": "2026-11-07T12:00:00Z",
        "is_illustrative": True, "data_label": "ILLUSTRATIVE \u2014 NOT LIVE MARKET DATA", "protocol": settings,
        "registration": {"registered_at": "2026-11-07T12:00:00Z", "protocol_sha256": "fixture", "learned_policy_sha256": "fixture",
            "base_policy_sha256": "fixture", "code_sha256": {}}, "real_market_performance_validated": False,
        "released_price_plan": None, "released_score": None, "released_expected_target_sessions": None,
        "validation_id": None, "external_llm_calls": 0, "llm_tokens": 0, "runs": records, "aggregate": []}
    robustness.validate(artifact)
    bad = deepcopy(artifact)
    bad["released_score"] = 80
    with pytest.raises(jsonschema.ValidationError):
        robustness.validate(bad)
    bad = deepcopy(artifact)
    bad["runs"].pop()
    with pytest.raises(ValueError, match="Missing"):
        robustness.validate(bad)
