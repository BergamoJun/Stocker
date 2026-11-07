"""Experimental conditional distribution of five-session highs and timing.

No LLM, fitted model service, future-feature scaler, or guaranteed peak sale.
Money uses Decimal; robust distances/kernel weights use bounded float math.
"""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP, localcontext
import json
import math
from pathlib import Path

from .sec_report import ROOT
from .rights import as_utc
from .swing_models import Bar, atr14, replay_five_sessions


def policy():
    data = json.loads((ROOT / "config/peak_model_policy.json").read_text(encoding="utf-8"))
    if data["release_enabled"] is not False or sum(data["score_weights"].values()) != 100:
        raise ValueError("Peak policy must remain research-only and total 100 points")
    if data["horizon_sessions"] != 5 or data["minimum_history_sessions"] != 60:
        raise ValueError("Unsupported peak-model history/horizon")
    return data


@dataclass(frozen=True)
class Sample:
    reference_index: int
    label_end_index: int
    features: tuple
    peak_atr: float
    peak_day: int
    minimum_atr: float
    normalized_path: tuple


def features(history):
    if len(history) < 60:
        raise ValueError("Peak features require sixty completed sessions")
    with localcontext() as context:
        context.prec = 34
        current, prior = history[-1], history[-21]
        atr, old_atr = atr14(history), atr14(history[:-20])
        sma20 = sum((bar.close for bar in history[-20:]), Decimal(0)) / 20
        sma60 = sum((bar.close for bar in history[-60:]), Decimal(0)) / 60
        high, low = max(bar.high for bar in history[-20:]), min(bar.low for bar in history[-20:])
        volume = sum((Decimal(bar.volume) for bar in history[-21:-1]), Decimal(0)) / 20
        if volume <= 0 or high <= low:
            raise ValueError("No usable volume/range feature")
        values = [(current.close - history[-2].close) / atr,
                  (current.close - history[-6].close) / atr,
                  (current.close - prior.close) / atr,
                  (current.close - sma20) / atr, (current.close - sma60) / atr,
                  (current.close - low) / (high - low),
                  (atr / current.close) / (old_atr / prior.close),
                  Decimal(str(math.log(max(1e-8, float(Decimal(current.volume) / volume)))))]
    result = tuple(float(value) for value in values)
    if not all(math.isfinite(value) for value in result):
        raise ValueError("Non-finite peak feature")
    return result


def samples(bars):
    result = []
    for index in range(59, len(bars) - 5, 5):
        history, future = bars[:index + 1], bars[index + 1:index + 6]
        atr, reference = atr14(history), history[-1].close
        maximum = max(bar.high for bar in future)
        day = next(i for i, bar in enumerate(future, 1) if bar.high == maximum)
        result.append(Sample(index, index + 5, features(history), float((maximum - reference) / atr), day,
            float((min(bar.low for bar in future) - reference) / atr),
            tuple(tuple((getattr(bar, key) - reference) / atr for key in ("open", "high", "low", "close"))
                  for bar in future)))
    return result


def quantile(values, weights, q):
    if not values or len(values) != len(weights) or not 0 <= q <= 1:
        raise ValueError("Invalid weighted quantile input")
    if any(not math.isfinite(float(v)) for v in values) or any(not math.isfinite(w) or w < 0 for w in weights):
        raise ValueError("Invalid weighted quantile values")
    total = sum(weights)
    if total <= 0:
        raise ValueError("No positive quantile mass")
    cumulative = 0.0
    for value, weight in sorted(zip(values, weights), key=lambda row: row[0]):
        cumulative += weight
        if cumulative + 1e-14 >= q * total:
            return value
    return max(values)


def neighborhood(training, query, settings, *, unconditional=False):
    if len(training) < settings["minimum_training_paths"]:
        raise ValueError("Insufficient prior resolved analog paths")
    if unconditional:
        return training, [1 / len(training)] * len(training), float(len(training)), 0.0
    scales = []
    for column in zip(*(row.features for row in training)):
        equal = [1.0] * len(column)
        scales.append(max(settings["robust_scale_floor"], quantile(column, equal, .75) - quantile(column, equal, .25)))
    feature_weights = settings["feature_weights"]
    distances = []
    for row in training:
        distance = math.sqrt(sum(weight * ((value - observed) / scale) ** 2
            for value, observed, scale, weight in zip(row.features, query, scales, feature_weights)) / sum(feature_weights))
        distances.append((distance, row.reference_index, row))
    nearest = sorted(distances, key=lambda row: row[:2])[:settings["neighbors"]]
    if nearest[0][0] > settings["maximum_nearest_standardized_distance"]:
        raise ValueError("Current feature state is outside observed analog support")
    bandwidth = nearest[-1][0] or 1.0
    weights = [math.exp(-.5 * (row[0] / bandwidth) ** 2) for row in nearest]
    total = sum(weights)
    weights = [weight / total for weight in weights]
    effective = 1 / sum(weight * weight for weight in weights)
    if effective < settings["minimum_effective_neighbors"]:
        raise ValueError("Insufficient effective analog support")
    return [row[2] for row in nearest], weights, effective, nearest[0][0]


def bin_index(value, settings):
    index = math.floor(value / settings["peak_bin_width_atr"])
    return max(settings["peak_bin_min_index"] - 1, min(settings["peak_bin_max_index"] + 1, index))


def joint_distribution(training, selected, weights, effective, settings):
    prior, local = defaultdict(float), defaultdict(float)
    for row in training:
        prior[(bin_index(row.peak_atr, settings), row.peak_day)] += 1 / len(training)
    for row, weight in zip(selected, weights):
        local[(bin_index(row.peak_atr, settings), row.peak_day)] += weight
    strength = settings["global_prior_effective_paths"]
    cells = {key: (effective * local[key] + strength * prior[key]) / (effective + strength)
             for key in set(prior) | set(local)}
    price_mass = defaultdict(float)
    for (index, _), mass in cells.items():
        price_mass[index] += mass
    modal_bin = min(price_mass, key=lambda index: (-price_mass[index], index))
    conditional_days = [cells.get((modal_bin, day), 0) / price_mass[modal_bin] for day in range(1, 6)]
    return cells, price_mass, modal_bin, conditional_days


def net_return(entry, exit_price, cost_bps):
    fee = Decimal(cost_bps) / 10000
    return (exit_price * (1 - fee) / (entry * (1 + fee)) - 1) * 100


def execution_statistics(selected, weights, plan, reference, atr, calendar, cost):
    outcomes = defaultdict(float)
    lower_ev = upper_ev = Decimal(0)
    hit_days, hit_weights = [], []
    for row, weight in zip(selected, weights):
        future = [Bar(session, *(reference + atr * value for value in prices), Decimal(1), calendar.close(session))
                  for session, prices in zip(plan["timing"]["sessions"], row.normalized_path)]
        if any(min(bar.open, bar.high, bar.low, bar.close) <= 0 for bar in future):
            raise ValueError("Analog price rescaling produced an invalid price; do not silently drop its mass")
        outcome = replay_five_sessions(plan, future, calendar, Decimal(cost))
        outcomes[outcome["outcome"]] += weight
        if outcome["outcome"] == "untradeable_open_gap":
            low = high = Decimal(0)
        elif outcome["outcome"] == "ambiguous_same_bar":
            low = net_return(future[0].open, Decimal(plan["invalidation_price"]), cost)
            high = net_return(future[0].open, Decimal(plan["first_target_price"]), cost)
        else:
            low = high = Decimal(outcome["net_return_pct"])
        lower_ev += Decimal(str(weight)) * low
        upper_ev += Decimal(str(weight)) * high
        if outcome["outcome"] == "target":
            hit_days.append(outcome["hit_session"])
            hit_weights.append(weight)
    return {"cost_bps_per_side": cost, "outcome_weight": dict(sorted(outcomes.items())),
        "estimated_target_before_stop_mass_bounds": [outcomes["target"], outcomes["target"] + outcomes["ambiguous_same_bar"]],
        "estimated_net_return_pct_bounds": [str(lower_ev), str(upper_ev)],
        "simulated_target_hits": len(hit_days),
        "experimental_first_target_hit_sessions": ([quantile(hit_days, hit_weights, .2), quantile(hit_days, hit_weights, .8)]
            if len(hit_days) >= policy()["minimum_simulated_target_hits_for_timing"] else None),
        "conditioning": "Timing among simulated target-hit paths only; untradeable paths keep zero return and ambiguous paths keep bounds"}


def forecast(bars, calendar, as_of, *, query_index=None, training_samples=None, unconditional=False):
    settings = policy()
    index = len(bars) - 1 if query_index is None else query_index
    if not 59 <= index < len(bars):
        raise ValueError("Invalid reference index")
    if any(as_utc(bar.available_at) > as_utc(as_of) for bar in bars[:index + 1]):
        raise ValueError("Peak inputs were not yet observed at as_of")
    query = features(bars[:index + 1])
    all_samples = training_samples if training_samples is not None else samples(bars[:index])
    training = [row for row in all_samples if row.label_end_index < index]
    chosen, weights, effective, distance = neighborhood(training, query, settings, unconditional=unconditional)
    result = assemble(bars, index, calendar, as_of, training, chosen, weights, effective, distance, query, settings)
    result["engine"] = "historical_analogs"
    return result


def assemble(bars, index, calendar, as_of, training, chosen, weights, effective, distance, query, settings):
    cells, price_mass, modal, days = joint_distribution(training, chosen, weights, effective, settings)
    if not settings["peak_bin_min_index"] <= modal <= settings["peak_bin_max_index"]:
        raise ValueError("Modal peak is in an overflow tail; target withheld")
    width = Decimal(str(settings["peak_bin_width_atr"]))
    reference, atr = bars[index].close, atr14(bars[:index + 1])
    low_z, high_z = Decimal(modal) * width, Decimal(modal + 1) * width
    target_z = low_z + width * Decimal(settings["simulation"]["target_fraction_inside_modal_bin"])
    target = (reference + atr * target_z).quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
    stop_z = min(-settings["minimum_stop_distance_atr"], quantile([row.minimum_atr for row in chosen], weights, settings["stop_adverse_path_quantile"]))
    stop = (reference + atr * Decimal(str(stop_z))).quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
    peak_quantiles = [quantile([row.peak_atr for row in chosen], weights, q) for q in (.2, .5, .8)]
    conditional_day_mode = min(range(1, 6), key=lambda day: (-days[day - 1], day))
    joint_mode = min(cells, key=lambda cell: (-cells[cell], cell))
    result = {"schema_version": "1.0.0", "status": "research_only_unvalidated", "methodology_id": settings["id"],
        "as_of": as_of, "reference_session": bars[index].session, "reference_price": str(reference),
        "atr14": str(atr), "training_paths": len(training), "selected_paths": len(chosen),
        "effective_paths": effective, "nearest_standardized_distance": distance,
        "features": dict(zip(settings["features"], query)),
        "peak": {"definition": settings["peak_definition"], "modal_bin_index": modal,
            "modal_price_interval": [str(reference + atr * low_z), str(reference + atr * high_z)],
            "estimated_modal_bin_mass": price_mass[modal], "calibrated_probability": None,
            "estimated_joint_mode": {"bin_index": joint_mode[0], "day": joint_mode[1], "mass": cells[joint_mode]},
            "conditional_day_mode": conditional_day_mode,
            "conditional_day_range": [quantile(list(range(1, 6)), days, .2), quantile(list(range(1, 6)), days, .8)],
            "empirical_peak_atr_quantiles_20_50_80": peak_quantiles,
            "empirical_peak_return_pct_quantiles_20_50_80": [str(atr * Decimal(str(z)) / reference * 100) for z in peak_quantiles],
            "joint_cells": [{"bin_index": key[0], "day": key[1], "estimated_mass": cells[key]} for key in sorted(cells)]},
        "plan": None, "execution": [], "profit_opportunity": None, "price_setup_points": None,
        "released_price_plan": None, "released_score": None, "released_expected_target_sessions": None,
        "validation_id": None, "limitations": settings["limitations"]}
    if not target > reference > stop > 0:
        result["plan_unavailable_reason"] = "Modal target has no positive long opportunity or downside price is invalid"
        return result
    ratio = (target - reference) / (reference - stop)
    plan = {"status": "research_only_unvalidated", "released_price_plan": None, "as_of": as_of,
        "methodology_id": settings["id"], "reference_session": bars[index].session,
        "reference_price": str(reference), "first_target_price": str(target), "invalidation_price": str(stop),
        "reward_risk_ratio": str(ratio), "timing": {**calendar.review_window(bars[index].session), "expected_target_sessions": None}}
    result["plan"] = plan
    result["execution"] = [execution_statistics(chosen, weights, plan, reference, atr, calendar, cost)
                           for cost in settings["cost_bps_per_side"]]
    cost = settings["cost_bps_per_side"][0]
    def clipped(value):
        return max(Decimal(0), min(Decimal(1), value))
    anchors, sub = settings["score_anchors"], settings["opportunity_subweights"]
    upside = net_return(reference, target, cost)
    expected = Decimal(result["execution"][0]["estimated_net_return_pct_bounds"][0])
    concentration = max(0.0, 1 - (peak_quantiles[2] - peak_quantiles[0]) / (2 * max(abs(float((low_z + high_z) / 2)), .5)))
    contributions = {"net_target_upside": Decimal(sub["net_target_upside"]) * clipped(upside / Decimal(anchors["full_upside_points_at_net_pct"])),
        "worst_case_expected_net": Decimal(sub["worst_case_expected_net"]) * clipped(expected / Decimal(anchors["full_expected_net_points_at_pct"])),
        "reward_risk": Decimal(sub["reward_risk"]) * clipped((ratio - 1) / 2),
        "peak_concentration": Decimal(sub["peak_concentration"]) * Decimal(str(concentration))}
    reliability = clipped(Decimal(str(effective)) / Decimal(anchors["full_sample_reliability_at_effective_paths"]))
    points = sum(contributions.values()) * reliability
    result["profit_opportunity"] = {"maximum_points": settings["score_weights"]["profit_opportunity"],
        "experimental_points": str(points), "sample_reliability_multiplier": str(reliability),
        "contributions_before_reliability": {key: str(value) for key, value in contributions.items()},
        "net_reference_to_target_pct": str(upside),
        "net_modal_peak_interval_pct": [str(net_return(reference, price, cost)) for price in (reference + atr * low_z, reference + atr * high_z)],
        "cost_bps_per_side": cost, "reward_risk": str(ratio)}
    setup = [query[3] > 0 and query[4] > query[3], query[1] > 0, query[0] > 0 and query[7] >= 0]
    result["price_setup_points"] = str(Decimal(settings["score_weights"]["price_setup"]) * Decimal(sum(setup)) / 3)
    return result


def combined_score(prediction, other_features, event_signal):
    settings = policy()
    base = {"status": "unavailable", "score": None, "experimental_score": None, "validation_id": None}
    if prediction.get("profit_opportunity") is None:
        return {**base, "reason": "No feasible modal-peak long target"}
    if event_signal.get("status") != "research_only_unvalidated":
        return {**base, "reason": "Missing reviewed current news; weights not redistributed"}
    contributions = {"profit_opportunity": Decimal(prediction["profit_opportunity"]["experimental_points"]),
                     "price_setup": Decimal(prediction["price_setup_points"])}
    for key in ("fundamentals", "market_sector"):
        value = other_features.get(key)
        if not value or not value.get("source_ids") or not value.get("methodology_id"):
            return {**base, "reason": "Missing versioned feature: " + key}
        normalized = Decimal(str(value["value"]))
        if not normalized.is_finite() or not 0 <= normalized <= 1:
            raise ValueError("Score inputs must be normalized finite values")
        contributions[key] = normalized * settings["score_weights"][key]
    news = Decimal(str(event_signal["experimental_component"]))
    if not news.is_finite() or not 0 <= news <= settings["score_weights"]["latest_events"]:
        raise ValueError("News contribution outside supported signed-recency mapping")
    contributions["latest_events"] = news
    total = max(Decimal(1), min(Decimal(100), sum(contributions.values())))
    return {"status": "research_only_unvalidated", "score": None, "validation_id": None,
        "experimental_score": int(total.quantize(Decimal(1), rounding=ROUND_HALF_UP)),
        "contributions": {key: str(value) for key, value in contributions.items()},
        "reason": "Research weights and uncalibrated analog estimates; never a win probability"}
