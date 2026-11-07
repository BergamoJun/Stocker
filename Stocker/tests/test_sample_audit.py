"""ILLUSTRATIVE — NOT LIVE MARKET DATA. Sample independence and decision gates."""

from copy import deepcopy
import math
import statistics

import pytest
import jsonschema

from research import learned_peak as learned, peak_model as base, robustness, sample_audit


def fixed_records(settings, base_settings, *, spread=0, drift=0, coverage=1):
    result = []
    for scenario in base_settings["scenarios"]:
        for index, seed in enumerate(settings["seeds"]):
            noise = spread if index % 2 else -spread
            short = {"paired_peak_mae_delta_against_fixed": -.2 + noise, "mean_net_return_pct_lower_bound": -.1 + noise}
            long = {key: value + drift for key, value in short.items()}
            result.append({"scenario": scenario["id"], "seed": seed, "status": "completed", "reason": None,
                "short_endpoints": short, "long_endpoints": long,
                "short_summary": {"common_forecastable_cases": 43},
                "long_summary": {"common_forecastable_cases": int(86 * coverage)}})
    return result


def test_new_seeds_and_independent_units_are_preregistered():
    settings, original = sample_audit.protocol()
    assert len(settings["seeds"]) == 24
    assert not set(settings["seeds"]) & set(original["seeds"])
    assert settings["confidence"]["decision_intervals"] == 20
    assert settings["real_price_input_enabled"] is False


def test_extra_evaluation_rows_do_not_change_training_or_calibration():
    settings, generator = sample_audit.protocol()
    generator = deepcopy(generator)
    generator.update(bars_per_dataset=1315, fixed_stress_start_index=885)
    scenario = next(row for row in generator["scenarios"] if row["id"] == "strong")
    bars, _, stress = robustness.generate(scenario, settings["seeds"][0], generator)
    fit_settings = deepcopy(learned.policy())
    fit_settings["fixed_partition_case_boundaries"] = settings["fixed_partition_case_boundaries"]
    old = learned.partitions(base.samples(bars[:1100]), fit_settings)
    new = learned.partitions(base.samples(bars), fit_settings)
    assert old[0] == new[0] and old[1] == new[1]
    assert len(old[2]) == 43 and len(new[2]) == 86
    assert new[3]["training"] == 123 and new[3]["calibration"] == 40
    assert learned.fit(old[0], old[1], fit_settings).artifact() == learned.fit(new[0], new[1], fit_settings).artifact()
    assert new[2][0].reference_index + 1 == stress == 885
    with pytest.raises(ValueError, match="integer"):
        learned.partitions(base.samples(bars), {**fit_settings, "fixed_partition_case_boundaries": [True, 165]})


def test_cluster_mean_interval_and_batch_standard_errors():
    values = list(range(24))
    estimate = sample_audit.mean_interval(values, 3.486)
    assert estimate["clusters"] == 24
    assert estimate["mean"] == 11.5
    expected = statistics.stdev(values) / math.sqrt(24)
    assert estimate["standard_error"] == pytest.approx(expected)
    assert estimate["interval"] == pytest.approx([11.5 - 3.486 * expected, 11.5 + 3.486 * expected])
    batches = sample_audit.batch_difference(values[:12], values[12:], 4.026)
    assert batches["mean"] == 12
    assert batches["clusters"] == [12, 12]
    with pytest.raises(ValueError, match="twelve"):
        sample_audit.batch_difference(values[:3], values[3:], 4.026)
    with pytest.raises(ValueError, match="finite"):
        sample_audit.mean_interval([1, float("nan")], 3.486)


def test_nonsignificant_point_difference_is_not_equivalence():
    # Difference is exactly zero, but independent-seed uncertainty is enormous.
    interval = sample_audit.batch_difference([-1, 1] * 6, [-1, 1] * 6, 4.026)
    assert interval["mean"] == 0
    assert interval["interval"][0] < 0 < interval["interval"][1]
    assert sample_audit.equivalent(interval, .05) is False
    assert sample_audit.equivalent({"interval": [-.01, .02]}, .05) is True


def test_gate_requires_all_intervals_inside_margins_and_full_coverage():
    settings, original = sample_audit.protocol()
    records = fixed_records(settings, original)
    result = sample_audit.assess(records, settings, original)
    assert result["advance_next_research_process"] is True
    assert result["equivalent_intervals"] == result["stability_intervals"] == 20
    changed = sample_audit.assess(fixed_records(settings, original, drift=.2), settings, original)
    assert changed["advance_next_research_process"] is False
    assert changed["equivalent_intervals"] < 20
    uncertain = sample_audit.assess(fixed_records(settings, original, spread=1), settings, original)
    assert uncertain["advance_next_research_process"] is False
    coverage = sample_audit.assess(fixed_records(settings, original, coverage=.8), settings, original)
    assert coverage["coverage_gate_passed"] is False
    assert coverage["advance_next_research_process"] is False


def test_missing_independent_path_cannot_be_dropped_to_pass():
    settings, original = sample_audit.protocol()
    records = fixed_records(settings, original)
    records[0].update(status="unavailable", reason="controlled failure", short_endpoints=None, long_endpoints=None,
                      short_summary=None, long_summary=None)
    assessment = sample_audit.assess(records, settings, original)
    assert assessment["all_clusters_complete"] is False
    assert assessment["advance_next_research_process"] is False
    assert assessment["stability_intervals"] == 20
    with pytest.raises(ValueError, match="Duplicate"):
        sample_audit.assess(records + [records[1]], settings, original)


def test_contract_recomputes_advance_and_blocks_released_values():
    settings, original = sample_audit.protocol()
    records = fixed_records(settings, original)
    artifact = {"schema_version": "1.0.0", "status": "research_only_unvalidated", "as_of": "2026-11-07T12:00:00Z",
        "is_illustrative": True, "data_label": "ILLUSTRATIVE \u2014 NOT LIVE MARKET DATA", "protocol": settings,
        "base_protocol": original, "registration": {"registered_at": "2026-11-07T12:00:00Z", "audit_policy_sha256": "fixture",
            "base_protocol_sha256": "fixture", "code_sha256": {}}, "records": records,
        "assessment": sample_audit.assess(records, settings, original), "real_market_performance_validated": False,
        "released_score": None, "released_price_plan": None, "released_expected_target_sessions": None,
        "validation_id": None, "external_llm_calls": 0, "llm_tokens": 0}
    sample_audit.validate(artifact)
    changed = deepcopy(artifact)
    changed["assessment"]["advance_next_research_process"] = False
    with pytest.raises(ValueError, match="Advance"):
        sample_audit.validate(changed)
    changed = deepcopy(artifact)
    changed["released_score"] = 80
    with pytest.raises(jsonschema.ValidationError):
        sample_audit.validate(changed)
    changed = deepcopy(artifact)
    changed["records"].pop()
    with pytest.raises(ValueError, match="every preregistered"):
        sample_audit.validate(changed)
