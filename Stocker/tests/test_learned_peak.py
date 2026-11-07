"""ILLUSTRATIVE — NOT LIVE MARKET DATA. Causal learning and contrary cases."""

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json
import random

import jsonschema
import numpy as np
import pytest

from research import learned_peak as learned, learned_research as workflow, peak_model as base
from research.validation import stable_hash

AT = "2026-11-07T12:00:00Z"


def oracle_rows(count=140):
    rng, rows = random.Random(43), []
    for index in range(count):
        x = tuple(rng.uniform(-1, 1) for _ in range(8))
        peak = 3 + 2 * x[0] + .8 * x[1] ** 2
        path = tuple(tuple(Decimal(str(value)) for value in day) for day in
            [(0, .5, -.3, .2), (.2, 1, 0, .6), (.6, peak, .4, .8), (.8, 1.2, .2, .5), (.5, .7, .1, .3)])
        rows.append(base.Sample(59 + index * 5, 64 + index * 5, x, peak, 3, -.3, path))
    return rows


@pytest.fixture(scope="module")
def controlled():
    rows, settings = oracle_rows(), learned.policy()
    train, calibration, held, counts = learned.partitions(rows, settings)
    return train, calibration, held, counts, settings


def test_three_blocks_purge_boundary_labels(controlled):
    train, calibration, held, counts, _ = controlled
    assert counts["purged_boundary_cases"] == 2
    assert max(row.label_end_index for row in train) < calibration[0].reference_index
    assert max(row.label_end_index for row in calibration) < held[0].reference_index
    assert counts["held_out"] == len(held)


def test_supervised_model_learns_unseen_signal_and_shuffled_labels_do_not(controlled):
    train, calibration, held, _, settings = controlled
    model = learned.fit(train, calibration, settings)
    shuffled = train.copy()
    random.Random(22).shuffle(shuffled)
    corrupted = [replace(row, normalized_path=other.normalized_path) for row, other in zip(train, shuffled)]
    bad = learned.fit(corrupted, calibration, settings)
    def error(regressor):
        return np.mean([abs(float(np.median(regressor.paths(row.features, row.reference_index)[:, :, 1].max(axis=1))) - row.peak_atr) for row in held])
    assert error(model) < .2
    assert error(bad) > 3 * error(model)
    assert np.linalg.norm(model.coefficients[1:]) > .1


def test_scaling_sees_training_only_and_model_serializes_without_pickle(controlled):
    train, calibration, _, _, settings = controlled
    fitted = learned.fit(train, calibration, settings)
    changed = [replace(row, features=tuple(v * .5 for v in row.features)) for row in calibration]
    alternative = learned.fit(train, changed, settings)
    assert np.array_equal(fitted.center, alternative.center)
    assert np.array_equal(fitted.scale, alternative.scale)
    assert np.array_equal(fitted.coefficients, alternative.coefficients)
    assert json.loads(json.dumps(fitted.artifact()))["engine"] == "supervised_ridge_residual_paths"


def test_overlapping_labels_future_queries_and_ood_are_blocked(controlled):
    train, calibration, held, _, settings = controlled
    with pytest.raises(ValueError, match="overlap"):
        learned.fit(train, [replace(calibration[0], reference_index=train[-1].label_end_index), *calibration[1:]], settings)
    fitted = learned.fit(train, calibration, settings)
    with pytest.raises(ValueError, match="strictly before"):
        fitted.paths(held[0].features, fitted.calibration_label_end)
    with pytest.raises(ValueError, match="outside training support"):
        fitted.paths([1e6] * 8, held[0].reference_index)
    with pytest.raises(ValueError, match="finite"):
        fitted.paths([float("nan")] * 8, held[0].reference_index)


def test_projection_preserves_whole_path_and_ohlc_envelope():
    raw = np.tile([2, -1, 5, 3], (2, 5, 1))
    result = learned.project(raw)
    assert np.all(result[:, :, 0] == 2)
    assert np.all(result[:, :, 3] == 3)
    assert np.all(result[:, :, 1] == 3)
    assert np.all(result[:, :, 2] == 2)


def test_future_price_changes_do_not_change_heldout_prediction():
    bars, calendar = workflow.synthetic_bars("signal", count=700)
    settings = learned.policy()
    train, calibration, held, _ = learned.partitions(base.samples(bars), settings)
    fitted = learned.fit(train, calibration, settings)
    index = held[0].reference_index
    before = learned.forecast(fitted, bars, calendar, AT, query_index=index)
    changed = bars[:index + 1] + [replace(row, open=row.open * 2, high=row.high * 2,
        low=row.low * 2, close=row.close * 2) for row in bars[index + 1:]]
    after = learned.forecast(fitted, changed, calendar, AT, query_index=index)
    assert before == after
    assert before["training_paths"] == len(train)
    assert before["calibration_paths"] == len(calibration)
    assert sum(row["estimated_mass"] for row in before["peak"]["joint_cells"]) == pytest.approx(1)
    assert before["peak"]["calibrated_probability"] is None
    assert before["released_score"] is None
    fitted.residuals -= 1e9
    with pytest.raises(ValueError, match="nonpositive"):
        learned.forecast(fitted, bars, calendar, AT, query_index=index)


def test_same_bar_ambiguity_is_counted_on_common_execution_cohort():
    metric = {"joint_brier": 1, "peak_median_absolute_error_atr": 1,
        "mean_pinball_loss_atr": .5, "peak_interval_20_80_contains_actual": True,
        "conditional_peak_day_absolute_error": 2}
    cases = []
    for ambiguous in (False, True):
        cases.append({"metrics": {name: metric for name in ("learned", "fixed_ewma", "unconditional_training")},
            "execution": {cost: {name: {"net_return_pct": None if ambiguous and name == "learned" else "1"}
                for name in ("learned", "fixed_ewma", "atr_baseline", "five_session_hold")}
                for cost in base.policy()["cost_bps_per_side"]}})
    result = workflow.summarize(cases, learned.policy())
    assert result["common_forecastable_cases"] == 2
    assert result["execution"][0]["common_resolved_cases"] == 1
    assert result["execution"][0]["ambiguous_or_untradeable_cases"] == 1
    assert result["minimum_sample_gate_passed"] is False


@pytest.mark.parametrize("route", ["explicit", "default"])
def test_real_cli_denies_training_before_reading_missing_price_file(tmp_path, monkeypatch, route):
    # Unknown permissions must fail even if ingestion/analysis would otherwise pass.
    monkeypatch.setattr(workflow, "ROOT", tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "schemas").mkdir()
    (tmp_path / "research").mkdir()
    original_root = learned.ROOT
    for relative in ("config/learned_peak_policy.json", "schemas/learned_research.schema.json",
            "research/learned_peak.py", "research/learned_research.py", "research/peak_model.py",
            "research/peak_simulation.py", "research/swing_models.py"):
        (tmp_path / relative).write_bytes((original_root / relative).read_bytes())
    record = {"id": "fixture-real", "provider": "fixture", "plan": "free", "monthly_cost_usd": 0,
        "reviewer": "fixture", "reviewed_at": "2026-01-01T00:00:00Z", "review_due_at": "2027-01-01T00:00:00Z",
        "evidence_url": "https://example.invalid/fixture", "is_illustrative": False,
        "uses": {"ingest": "allowed", "store": "allowed", "derived_analytics": "allowed", "model_training": "not_cleared"}}
    rights = tmp_path / "rights.json"
    rights.write_text(json.dumps(record))
    monkeypatch.setattr(workflow, "now", lambda: AT)
    args = ["--prices", str(tmp_path / "absent_prices.json"), "--price-entitlement", str(rights)]
    if route == "default":
        folder = tmp_path / "reports/private/market_data/20261107T000000Z"
        folder.mkdir(parents=True)
        folder.joinpath("price_entitlement.json").write_text(json.dumps(record))
        folder.joinpath("prices.json").write_text("INVALID JSON MUST NOT BE PARSED BEFORE RIGHTS CHECK")
        args = []
    code = workflow.main(args)
    assert code == 2
    output = next((tmp_path / "reports/private/learned_research").glob("*/learned_research.json"))
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["status"] == "data_use_not_cleared"
    assert "model_training" in result["reason"]
    assert result["experiments"] == []
    assert "price_snapshot_sha256" not in result["registration"]
    bad = deepcopy(result)
    bad["released_score"] = 70
    with pytest.raises(jsonschema.ValidationError):
        workflow.validate(bad)
    bad = deepcopy(result)
    bad["real_market_performance_validated"] = True
    with pytest.raises(jsonschema.ValidationError):
        workflow.validate(bad)


def test_frozen_base_policy_and_small_samples_fail_closed(controlled):
    train, calibration, _, _, settings = controlled
    assert learned.policy()["release_enabled"] is False
    with pytest.raises(ValueError, match="Insufficient"):
        learned.fit(train[:10], calibration, settings)
    with pytest.raises(ValueError, match="chronological"):
        learned.partitions(list(reversed(train)), settings)
    assert stable_hash(settings) == stable_hash(learned.policy())
