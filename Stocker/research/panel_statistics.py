"""Deterministic descriptive statistics and paired calendar-block uncertainty.

No forecast, fitting, source requests, independent-row bootstrap or final tests.
Money/return means retain Decimal; bootstrap estimates use float64 statistics.
"""

from collections import Counter, defaultdict
from decimal import Decimal
import hashlib
import math

import numpy as np


def number(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("INVALID_DIAGNOSTIC_NUMBER")
    try:
        result = Decimal(str(value))
    except ArithmeticError:
        raise ValueError("INVALID_DIAGNOSTIC_NUMBER") from None
    if not result.is_finite():
        raise ValueError("INVALID_DIAGNOSTIC_NUMBER")
    return result


def mean(values):
    return str(sum(values, Decimal(0)) / len(values)) if values else None


def scenario(row, cost):
    return next(item for item in row["cost_scenarios"] if item["cost_bps_per_side"] == cost)


def point_summary(rows, settings):
    rows = sorted(rows, key=lambda row: (row["reference_session"], row["cik"], row["symbol"]))
    usable = [row for row in rows if row["status"] == "retrospective_diagnostic_not_validation"]
    result = {"expected_slots": len(rows), "forecastable_slots": len(usable), "unavailable_slots": len(rows) - len(usable),
        "unavailable_reason_counts": dict(Counter(row.get("reason_code") for row in rows if row["status"] == "unavailable")),
        "screen_decision_counts": dict(Counter(row["screen_decision"] for row in rows)),
        "mean_absolute_peak_error_atr": mean([number(row["absolute_peak_error_atr"]) for row in usable]),
        "mean_peak_day_error_sessions": mean([number(row["peak_day_absolute_error"]) for row in usable]),
        "modal_price_bin_hit_fraction": mean([Decimal(int(row["modal_price_bin_hit"])) for row in usable]),
        "cost_scenarios": []}
    for cost in settings["cost_bps_per_side"]:
        pairs = [(row, scenario(row, cost)) for row in usable]
        matched = [(row, item) for row, item in pairs if item["screened_net_return_pct_bounds"] is not None]
        result_row = {"cost_bps_per_side": cost, "matched_slots": len(matched),
            "matched_slot_fraction": str(Decimal(len(matched)) / len(rows)) if rows else "0",
            "matched_issuers": len({row["cik"] for row, _ in matched}),
            "matched_reference_dates": len({row["reference_session"] for row, _ in matched}),
            "hold_mean_net_return_pct": mean([number(item["five_session_hold_net_return_pct"]) for _, item in matched]),
            "unfiltered_outcome_counts": dict(Counter(item["unfiltered_outcome"]["outcome"] for _, item in pairs)),
            "screened_entry_slots": sum(row["screen_decision"] == "numerical_candidate" and item["unfiltered_outcome"]["outcome"] != "untradeable_open_gap" for row, item in matched)}
        for name in ("unfiltered", "screened"):
            result_row[name + "_mean_net_return_pct_bounds"] = [mean([number(item[name + "_net_return_pct_bounds"][i]) for _, item in matched]) for i in (0, 1)]
            result_row[name + "_mean_excess_to_hold_pct_points_bounds"] = [mean([number(item[name + "_net_return_pct_bounds"][i]) - number(item["five_session_hold_net_return_pct"]) for _, item in matched]) for i in (0, 1)]
        timing = [item for _, item in pairs if item["target_timing_evaluable"]]
        result_row.update(conditional_target_timing_cases=len(timing),
            conditional_target_timing_coverage=mean([Decimal(int(item["target_hit_inside_conditional_timing_range"])) for item in timing]),
            timing_support_sufficient=len(timing) >= settings["support"]["minimum_conditional_target_hits_for_timing_note"])
        outcomes = result_row["unfiltered_outcome_counts"]
        result_row["target_before_stop_fraction_bounds"] = [str(Decimal(outcomes.get("target", 0) + extra) / len(usable)) if usable else None
                                                           for extra in (0, outcomes.get("ambiguous_same_bar", 0))]
        result_row["target_fraction_denominator"] = "all_forecastable_slots_including_no_plan_and_declined_entries"
        result["cost_scenarios"].append(result_row)
    return result


def calendar_draws(reference_dates, phase, block_length, settings):
    """One shared date-index draw moves every issuer and both strategies together."""
    n = len(reference_dates)
    if not 1 <= block_length <= n:
        raise ValueError("INVALID_BLOCK_LENGTH")
    seed_text = str(settings["bootstrap"]["seed"]) + ":" + phase + ":" + str(block_length) + ":" + ",".join(reference_dates)
    seed = int.from_bytes(hashlib.sha256(seed_text.encode()).digest()[:8], "big")
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, n - block_length + 1, size=(settings["bootstrap"]["replications"], math.ceil(n / block_length)))
    return (starts[:, :, None] + np.arange(block_length)).reshape(len(starts), -1)[:, :n], seed


def block_comparison(rows, reference_dates, phase, sector, cost, block_length, settings, *, pilot=False):
    point = point_summary(rows, settings)
    paired_point = next(item for item in point["cost_scenarios"] if item["cost_bps_per_side"] == cost)
    support = settings["support"]
    reasons = []
    n = len(reference_dates)
    if pilot:
        reasons.append("ENGINEERING_PILOT_NOT_REPRESENTATIVE")
    if sector["selected_unique_issuers"] < support["minimum_selected_issuers_per_sector"]:
        reasons.append("INSUFFICIENT_SELECTED_ISSUERS")
    if paired_point["matched_issuers"] < support["minimum_matched_issuers_per_phase"]:
        reasons.append("INSUFFICIENT_MATCHED_ISSUERS")
    if number(paired_point["matched_slot_fraction"]) < number(support["minimum_matched_slot_fraction"]):
        reasons.append("INSUFFICIENT_MATCHED_COVERAGE")
    if n < support["minimum_reference_dates"]:
        reasons.append("INSUFFICIENT_REFERENCE_DATES")
    if n // block_length < support["minimum_nominal_nonoverlapping_blocks"]:
        reasons.append("INSUFFICIENT_CALENDAR_BLOCKS")
    if support["require_every_reference_date_has_comparisons"] and paired_point["matched_reference_dates"] != n:
        reasons.append("REFERENCE_DATE_WITHOUT_COMPARISONS")
    result = {"block_references": block_length, "approximate_exchange_sessions": block_length * settings["bootstrap"]["reference_stride_exchange_sessions"],
        "reference_dates": n, "nominal_nonoverlapping_blocks": n // block_length,
        "independent_sample_count": None, "status": "withheld" if reasons else "computed_exploratory",
        "reason_codes": reasons, "replications": 0, "seed": None, "intervals": None,
        "direction": "insufficient_support", "performance_validated": False}
    if reasons:
        return result
    # Columns: count, net-low, net-high, excess-low, excess-high, peak error,
    # peak-day error and interval-hit indicator. Missing rows add no return/count.
    daily = np.zeros((n, 8), dtype=np.float64)
    locations = {day: i for i, day in enumerate(reference_dates)}
    for row in sorted(rows, key=lambda row: (row["reference_session"], row["cik"], row["symbol"])):
        if row["status"] == "unavailable":
            continue
        item = scenario(row, cost)
        if item["screened_net_return_pct_bounds"] is None:
            continue
        low, high = map(number, item["screened_net_return_pct_bounds"])
        hold = number(item["five_session_hold_net_return_pct"])
        daily[locations[row["reference_session"]]] += np.array([1, float(low), float(high), float(low - hold), float(high - hold),
            float(number(row["absolute_peak_error_atr"])), float(number(row["peak_day_absolute_error"])), int(row["modal_price_bin_hit"])])
    draws, seed = calendar_draws(reference_dates, phase, block_length, settings)
    totals = daily[draws].sum(axis=1)
    estimates = totals[:, 1:] / totals[:, :1]
    if not np.isfinite(estimates).all():
        raise ValueError("INVALID_BOOTSTRAP_ESTIMATE")
    quantiles = np.quantile(estimates, settings["bootstrap"]["percentiles"], axis=0, method="linear")
    names = ["screened_net_lower_pct", "screened_net_upper_pct", "excess_lower_pct_points", "excess_upper_pct_points",
        "peak_error_atr", "peak_day_error_sessions", "modal_bin_hit_fraction"]
    intervals = {name: [float(value) for value in quantiles[:, index]] for index, name in enumerate(names)}
    envelope = [intervals["excess_lower_pct_points"][0], intervals["excess_upper_pct_points"][1]]
    intervals["conservative_excess_envelope_pct_points"] = envelope
    intervals["conservative_net_envelope_pct"] = [intervals["screened_net_lower_pct"][0], intervals["screened_net_upper_pct"][1]]
    result.update(status="computed_exploratory", replications=len(draws), seed=seed, intervals=intervals,
        direction="positive_sample_excess" if envelope[0] > 0 else "negative_sample_excess" if envelope[1] < 0 else "inconclusive")
    return result


def group_diagnostics(rows, refs, phase, sector, settings, *, pilot=False):
    dates = [row["reference_session"] for row in refs if row["phase"] == phase]
    result = {"sector_id": sector["sector_id"], "name_ko": sector["name_ko"], "phase": phase,
        "selected_unique_issuers": sector["selected_unique_issuers"], "point": point_summary(rows, settings),
        "uncertainty": [], "breakdowns": {}}
    lengths = [settings["bootstrap"]["primary_block_references"], *settings["bootstrap"]["sensitivity_block_references"]]
    for cost in settings["cost_bps_per_side"]:
        blocks = [block_comparison(rows, dates, phase, sector, cost, length, settings, pilot=pilot) for length in lengths]
        complete = all(row["status"] == "computed_exploratory" for row in blocks)
        directions = {row["direction"] for row in blocks}
        sensitivity = ("consistent_positive" if directions == {"positive_sample_excess"} else "consistent_negative" if directions == {"negative_sample_excess"}
                       else "mixed_or_inconclusive") if complete else "not_all_lengths_supported"
        result["uncertainty"].append({"cost_bps_per_side": cost, "primary_direction": blocks[0]["direction"],
            "sensitivity_status": sensitivity, "blocks": blocks})
    for key in ("reference_month", "volatility_group", "liquidity_group", "symbol"):
        grouped = defaultdict(list)
        for row in rows:
            grouped[row.get(key, "unavailable")].append(row)
        result["breakdowns"][key] = {name: point_summary(items, settings) for name, items in sorted(grouped.items())}
    result["monthly_sensitivity"] = []
    for cost in settings["cost_bps_per_side"]:
        monthly = defaultdict(list)
        for row in rows:
            if row["status"] != "unavailable":
                item = scenario(row, cost)
                if item["screened_net_return_pct_bounds"] is not None:
                    monthly[row["reference_month"]].append(item)
        count = sum(map(len, monthly.values()))
        totals = [sum((number(item["screened_net_return_pct_bounds"][i]) - number(item["five_session_hold_net_return_pct"])
                       for items in monthly.values() for item in items), Decimal(0)) for i in (0, 1)]
        leave_out = []
        for month, items in sorted(monthly.items()):
            remaining = count - len(items)
            if remaining:
                leave_out.append({"excluded_month": month, "remaining_matched_slots": remaining,
                    "mean_excess_pct_points_bounds": [str((totals[i] - sum((number(item["screened_net_return_pct_bounds"][i]) - number(item["five_session_hold_net_return_pct"]) for item in items), Decimal(0))) / remaining) for i in (0, 1)]})
        direction = "positive" if totals[0] > 0 else "negative" if totals[1] < 0 else "uncertain"
        fragile = (any(number(row["mean_excess_pct_points_bounds"][0]) <= 0 for row in leave_out) if direction == "positive" else
                   any(number(row["mean_excess_pct_points_bounds"][1]) >= 0 for row in leave_out) if direction == "negative" else None)
        result["monthly_sensitivity"].append({"cost_bps_per_side": cost, "matched_months": len(monthly),
            "point_direction": direction, "direction_not_preserved_after_leaving_out_one_month": fragile if leave_out else None,
            "leave_one_month_out": leave_out, "is_formal_significance_test": False})
    priorities = []
    primary = next(row for row in result["point"]["cost_scenarios"] if row["cost_bps_per_side"] == settings["primary_cost_bps_per_side"])
    if result["point"]["unavailable_slots"]:
        priorities.append("REVIEW_MISSING_DATA_BEFORE_TUNING")
    if result["point"]["forecastable_slots"] and primary["screened_entry_slots"] == 0:
        priorities.append("ALL_SCREENED_RESULTS_ARE_CASH")
    if primary["screened_mean_net_return_pct_bounds"][1] is not None and number(primary["screened_mean_net_return_pct_bounds"][1]) <= 0:
        priorities.append("COST_ADJUSTED_PROFIT_NOT_POSITIVE")
    if not primary["timing_support_sufficient"]:
        priorities.append("CONDITIONAL_TARGET_TIMING_SUPPORT_LOW")
    if any(row["direction_not_preserved_after_leaving_out_one_month"] for row in result["monthly_sensitivity"]):
        priorities.append("RESULT_DEPENDS_ON_ONE_MONTH")
    if any("INSUFFICIENT_CALENDAR_BLOCKS" in block["reason_codes"] for item in result["uncertainty"] for block in item["blocks"]):
        priorities.append("LONGER_TIME_COVERAGE_NEEDED")
    result["review_priorities"] = priorities
    return result
