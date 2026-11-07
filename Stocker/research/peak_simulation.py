"""Fixed statistical scenario equations, not AI/ML model training.

Historical moments are direct personal analytics. Gaussian/gap/bridge and drift
assumptions are fixed in policy, never optimized against a test outcome.
"""

from decimal import Decimal
import hashlib
import math
import random

from . import peak_model as model
from .swing_models import atr14
from .rights import as_utc


def parameters(history, settings):
    if len(history) < 60:
        raise ValueError("Simulation requires sixty completed sessions")
    config = settings["simulation"]
    pairs = list(zip(history, history[1:]))[-config["maximum_return_observations"]:]
    gaps = [math.log(float(row.open / prior.close)) for prior, row in pairs]
    intraday = [math.log(float(row.close / row.open)) for _, row in pairs]
    range_variance = [math.log(float(row.high / row.low)) ** 2 / (4 * math.log(2)) for _, row in pairs]
    decay = 2 ** (-1 / config["ewma_half_life_sessions"])
    weights = [decay ** index for index in range(len(pairs) - 1, -1, -1)]
    total = sum(weights)
    weights = [weight / total for weight in weights]
    mean_gap, mean_day = (sum(value * weight for value, weight in zip(values, weights)) for values in (gaps, intraday))
    correction = 1 - sum(weight * weight for weight in weights)
    gap_var = sum(weight * (value - mean_gap) ** 2 for value, weight in zip(gaps, weights)) / correction
    day_var = sum(weight * (value - mean_day) ** 2 for value, weight in zip(intraday, weights)) / correction
    covariance = sum(weight * (gap - mean_gap) * (day - mean_day) for gap, day, weight in zip(gaps, intraday, weights)) / correction
    gap_sigma = math.sqrt(max(0, gap_var))
    day_sigma = math.sqrt(max(day_var, sum(weight * value for weight, value in zip(weights, range_variance))))
    if gap_sigma + day_sigma <= 1e-12 or max(gap_sigma, day_sigma) > 1:
        raise ValueError("Degenerate or unsupported log-return scale")
    rho = covariance / math.sqrt(gap_var * day_var) if gap_var > 0 and day_var > 0 else 0
    rho = max(-config["maximum_gap_intraday_correlation"], min(config["maximum_gap_intraday_correlation"], rho))
    def drift(mean, sigma):
        limit = config["maximum_log_drift_in_sigma_units"] * sigma
        return max(-limit, min(limit, config["drift_shrinkage"] * mean))
    return {"observations": len(pairs), "effective_observations": 1 / sum(weight * weight for weight in weights),
        "gap_log_drift": drift(mean_gap, gap_sigma), "intraday_log_drift": drift(mean_day, day_sigma),
        "gap_log_sigma": gap_sigma, "intraday_log_sigma": day_sigma, "gap_intraday_correlation": rho}


def forecast(bars, calendar, as_of, *, query_index=None, zero_drift=False):
    settings = model.policy()
    index = len(bars) - 1 if query_index is None else query_index
    history = bars[:index + 1]
    if any(as_utc(bar.available_at) > as_utc(as_of) for bar in history):
        raise ValueError("Simulation inputs were not yet observed at as_of")
    stats = parameters(history, settings)
    config = settings["simulation"]
    seed_material = str(config["seed"]) + ":" + history[-1].session
    seed = int.from_bytes(hashlib.sha256(seed_material.encode()).digest()[:8], "big")
    rng = random.Random(seed)
    reference, atr = history[-1].close, atr14(history)
    feature_values = model.features(history)
    simulated = []
    steps = config["intraday_steps"]
    rho = stats["gap_intraday_correlation"]
    mu_gap = 0 if zero_drift else stats["gap_log_drift"]
    mu_day = 0 if zero_drift else stats["intraday_log_drift"]
    for path_id in range(config["paths"]):
        previous, path = 1.0, []
        for _ in range(5):
            z_gap, z_day = rng.gauss(0, 1), rng.gauss(0, 1)
            opening = previous * math.exp(mu_gap + stats["gap_log_sigma"] * z_gap)
            daily = mu_day + stats["intraday_log_sigma"] * (rho * z_gap + math.sqrt(1 - rho * rho) * z_day)
            noise = [rng.gauss(0, 1) for _ in range(steps)]
            mean_noise = sum(noise) / steps
            level, levels = math.log(opening), [opening]
            for value in noise:
                level += daily / steps + stats["intraday_log_sigma"] / math.sqrt(steps) * (value - mean_noise)
                levels.append(math.exp(level))
            previous = levels[-1]
            prices = (opening, max(levels), min(levels), previous)
            path.append(tuple((reference * Decimal(str(value)) - reference) / atr for value in prices))
        maximum = max(row[1] for row in path)
        day = next(day for day, row in enumerate(path, 1) if row[1] == maximum)
        simulated.append(model.Sample(path_id, index, feature_values, float(maximum), day,
                                      float(min(row[2] for row in path)), tuple(path)))
    weights = [1 / len(simulated)] * len(simulated)
    result = model.assemble(bars, index, calendar, as_of, simulated, simulated, weights,
                            stats["effective_observations"], 0, feature_values, settings)
    result.update(engine="fixed_ewma_simulation", training_paths=0, simulated_paths=len(simulated),
        observed_return_sessions=stats["observations"], parameter_effective_observations=stats["effective_observations"],
        statistical_parameters=stats, random_seed=seed,
        estimation_note="Fixed EWMA moments and Gaussian/bridge equations; no AI/ML fitting. Simulated paths are not independent observed trades")
    return result
