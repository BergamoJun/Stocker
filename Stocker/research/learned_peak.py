"""Local supervised OHLC-path regression. No LLM/API calls or paid dependencies.

Training, residual estimation and evaluation use three disjoint chronological
blocks. The CLI clears model_training rights BEFORE reading real price inputs.
"""

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
import json

import numpy as np

from . import peak_model as base
from .rights import as_utc
from .sec_report import ROOT
from .validation import digest, stable_hash


def policy():
    settings = json.loads((ROOT / "config/learned_peak_policy.json").read_text(encoding="utf-8"))
    if (settings["release_enabled"] is not False or settings["horizon_sessions"] != 5
            or settings["base_policy_sha256"] != digest(ROOT / "config/peak_model_policy.json")):
        raise ValueError("Learned policy must be research-only and pin the unchanged base policy")
    return settings


def partitions(rows, settings):
    if not rows or any(a.reference_index >= b.reference_index for a, b in zip(rows, rows[1:])):
        raise ValueError("Require unique chronological supervised cases")
    if any(row.label_end_index != row.reference_index + 5 for row in rows):
        raise ValueError("Labels must cover exactly five future sessions")
    boundaries = settings.get("fixed_partition_case_boundaries")
    if boundaries is not None:
        if (not isinstance(boundaries, list) or len(boundaries) != 2
                or any(type(value) is not int for value in boundaries)):
            raise ValueError("Fixed chronological boundaries require two integer case indices")
        first, second = boundaries
    else:
        first = int(len(rows) * settings["training_fraction"])
        second = first + int(len(rows) * settings["calibration_fraction"])
    if not 0 < first < second < len(rows):
        raise ValueError("Insufficient cases for three chronological blocks")
    train = [row for row in rows[:first] if row.label_end_index < rows[first].reference_index]
    calibration = [row for row in rows[first:second] if row.label_end_index < rows[second].reference_index]
    held = rows[second:]
    if len(train) < settings["minimum_training_cases"] or len(calibration) < settings["minimum_calibration_cases"]:
        raise ValueError("Insufficient purged training/calibration cases")
    return train, calibration, held, {"raw_cases": len(rows), "training": len(train),
        "calibration": len(calibration), "held_out": len(held),
        "purged_boundary_cases": len(rows) - len(train) - len(calibration) - len(held)}


def path_array(rows):
    values = np.array([[float(value) for day in row.normalized_path for value in day] for row in rows])
    if values.shape != (len(rows), 20) or not np.isfinite(values).all():
        raise ValueError("Invalid supervised OHLC response")
    return values


def project(values):
    """Restore each OHLC envelope, keeping the complete residual path together."""
    shaped = np.asarray(values, dtype=float).reshape(-1, 5, 4).copy()
    if not np.isfinite(shaped).all():
        raise ValueError("Non-finite predicted path")
    shaped[:, :, 1] = np.maximum(shaped[:, :, 1], np.maximum(shaped[:, :, 0], shaped[:, :, 3]))
    shaped[:, :, 2] = np.minimum(shaped[:, :, 2], np.minimum(shaped[:, :, 0], shaped[:, :, 3]))
    return shaped


@dataclass
class PathRegressor:
    center: np.ndarray
    scale: np.ndarray
    coefficients: np.ndarray
    residuals: np.ndarray
    training_cases: int
    train_label_end: int
    calibration_label_end: int
    settings: dict

    def design(self, values, *, check_support=True):
        values = np.atleast_2d(np.asarray(values, dtype=float))
        if values.shape[1] != 8 or not np.isfinite(values).all():
            raise ValueError("Require eight finite causal price features")
        z = (values - self.center) / self.scale
        active = self.settings.get("active_feature_indices", list(range(8)))
        if not active or len(set(active)) != len(active) or any(type(i) is not int or not 0 <= i < 8 for i in active):
            raise ValueError("Invalid active feature indices")
        z[:, [i for i in range(8) if i not in active]] = 0
        if check_support and np.max(np.abs(z)) > self.settings["maximum_absolute_standardized_feature"]:
            raise ValueError("Feature state outside training support")
        return np.column_stack((np.ones(len(z)), z, z * z))

    def paths(self, query, reference_index):
        if self.calibration_label_end >= reference_index:
            raise ValueError("Calibration labels must end strictly before query reference")
        mean = self.design(query) @ self.coefficients
        return project(mean + self.residuals)

    def artifact(self):
        return {"engine": "supervised_ridge_residual_paths", "feature_center": self.center.tolist(),
            "feature_scale": self.scale.tolist(), "coefficients": self.coefficients.tolist(),
            "calibration_residual_paths": self.residuals.tolist(), "training_cases": self.training_cases,
            "latest_training_label_index": self.train_label_end,
            "latest_calibration_label_index": self.calibration_label_end,
            "active_feature_indices": self.settings.get("active_feature_indices", list(range(8))),
            "ridge_alpha": self.settings["ridge_alpha"], "numpy_version": np.__version__}


def fit(train, calibration, settings):
    if len(train) < settings["minimum_training_cases"] or len(calibration) < settings["minimum_calibration_cases"]:
        raise ValueError("Insufficient training/calibration observations")
    if max(row.label_end_index for row in train) >= min(row.reference_index for row in calibration):
        raise ValueError("Training labels overlap calibration reference")
    values = np.array([row.features for row in train], dtype=float)
    if values.shape != (len(train), 8) or not np.isfinite(values).all():
        raise ValueError("Require eight finite training features")
    for index in range(8):
        if index not in settings.get("active_feature_indices", list(range(8))):
            values[:, index] = 0
    center, scale = values.mean(axis=0), np.maximum(values.std(axis=0), settings["standard_deviation_floor"])
    result = PathRegressor(center, scale, np.empty((17, 20)), np.empty((0, 20)), len(train),
        max(row.label_end_index for row in train), max(row.label_end_index for row in calibration), settings)
    design = result.design(values, check_support=False)
    penalty = np.eye(17) * settings["ridge_alpha"]
    penalty[0, 0] = 0  # Intercept is unpenalized; scaler only sees training rows.
    result.coefficients = np.linalg.solve(design.T @ design + penalty, design.T @ path_array(train))
    # Preserve all calibration observations. Support problems withhold the run;
    # never silently select easier residuals after looking at their outcomes.
    result.residuals = path_array(calibration) - result.design([row.features for row in calibration]) @ result.coefficients
    return result


def forecast(regressor, bars, calendar, as_of, *, query_index=None):
    index = len(bars) - 1 if query_index is None else query_index
    history = bars[:index + 1]
    if any(as_utc(row.available_at) > as_utc(as_of) for row in history):
        raise ValueError("Price feature input is after the observation cutoff")
    query = base.features(history)
    paths = regressor.paths(query, index)
    reference, atr = history[-1].close, base.atr14(history)
    if any(reference + atr * Decimal(str(value)) <= 0 for value in paths.flatten()):
        raise ValueError("Residual distribution includes nonpositive prices; whole candidate withheld")
    chosen = []
    for path_id, path in enumerate(paths):
        peak = float(path[:, 1].max())
        chosen.append(base.Sample(path_id, regressor.calibration_label_end, query, peak,
            int(np.argmax(path[:, 1])) + 1, float(path[:, 2].min()),
            tuple(tuple(Decimal(str(value)) for value in day) for day in path)))
    settings = deepcopy(base.policy())
    settings["id"] = regressor.settings["id"]
    settings["limitations"] += regressor.settings["limitations"]
    weights = [1 / len(chosen)] * len(chosen)
    result = base.assemble(bars, index, calendar, as_of, chosen, chosen, weights, len(chosen), 0, query, settings)
    result.update(engine="supervised_ridge_residual_paths", training_paths=regressor.training_cases,
        calibration_paths=len(chosen), model_fingerprint=stable_hash(regressor.artifact()),
        estimation_note="Learned five-day OHLC regression plus disjoint whole-path residuals; uncalibrated research distribution")
    return result


def distribution_metrics(prediction, actual):
    cells = prediction["peak"]["joint_cells"]
    actual_bin = base.bin_index(actual.peak_atr, base.policy())
    mass = sum(row["estimated_mass"] for row in cells if row["bin_index"] == actual_bin and row["day"] == actual.peak_day)
    quantiles = prediction["peak"]["empirical_peak_atr_quantiles_20_50_80"]
    losses = []
    for q, value in zip((.2, .5, .8), quantiles):
        residual = actual.peak_atr - value
        losses.append(max(q * residual, (q - 1) * residual))
    return {"joint_brier": 1 + sum(row["estimated_mass"] ** 2 for row in cells) - 2 * mass,
        "peak_median_absolute_error_atr": abs(actual.peak_atr - quantiles[1]),
        "mean_pinball_loss_atr": sum(losses) / 3,
        "peak_interval_20_80_contains_actual": quantiles[0] <= actual.peak_atr <= quantiles[2],
        "conditional_peak_day_absolute_error": abs(actual.peak_day - prediction["peak"]["conditional_day_mode"])}
