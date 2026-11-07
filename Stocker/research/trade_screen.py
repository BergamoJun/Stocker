"""Deterministic reference-price cost/risk screen, separate from forecasting.

Inputs are a causal candidate forecast and explicit provenance. Realized future
prices, labels and returns are deliberately absent from this interface.
"""

from decimal import Decimal, InvalidOperation
import json

from .peak_model import net_return
from .rights import as_utc
from .sec_report import ROOT


def policy():
    settings = json.loads((ROOT / "config/trade_screen_policy.json").read_text(encoding="utf-8"))
    if settings["release_enabled"] is not False or settings["monthly_data_budget_usd"] != 0 or settings["external_llm_calls"] != 0:
        raise ValueError("Cost screen must remain private unvalidated zero-budget research")
    return settings


def number(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("Missing or invalid finite numeric input")
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("Invalid decimal input") from None
    if not result.is_finite():
        raise ValueError("Nonfinite screen input")
    return result


def assess(prediction, *, as_of, source_ids, is_illustrative, mode, settings=None):
    settings = settings or policy()
    if type(is_illustrative) is not bool or not source_ids or mode not in {"current_research", "historical_diagnostic"}:
        raise ValueError("Explicit provenance, classification and screen mode required")
    if as_utc(as_of) < as_utc(settings["frozen_at"]):
        raise ValueError("Screen precedes policy freeze")
    result = {"schema_version": "1.0.0", "status": "research_only_unvalidated", "methodology_id": settings["id"],
        "as_of": as_of, "is_illustrative": is_illustrative, "source_ids": source_ids, "mode": mode,
        "screen_decision": "unavailable", "reason_codes": [], "reference_session": None,
        "forecast_methodology_id": None, "metrics": [], "effective_observed_paths": None,
        "entry_assumption": settings["entry_assumption"], "released_price_plan": None, "released_score": None,
        "released_expected_target_sessions": None, "calibrated_probability": None, "validation_id": None,
        "limitations": [settings["estimated_ev_note"], settings["uncertainty_note"],
            "Passing this screen is a numerical research candidate, never a recommendation or validated positive expected return"]}
    if prediction is None:
        result["reason_codes"] = ["NO_FORECAST"]
        return result
    try:
        if prediction.get("status") != "research_only_unvalidated" or not prediction.get("methodology_id"):
            raise ValueError("Unrecognized forecast status/methodology")
        if as_utc(prediction["as_of"]) > as_utc(as_of):
            raise ValueError("Forecast is after screen cutoff")
        if prediction.get("released_score") is not None or prediction.get("validation_id") is not None:
            raise ValueError("Unreviewed release metadata")
        result.update(reference_session=prediction["reference_session"], forecast_methodology_id=prediction["methodology_id"])
        engine = prediction.get("engine")
        if engine == "fixed_ewma_simulation":
            effective = number(prediction["parameter_effective_observations"])
        elif engine in {"supervised_ridge_residual_paths", "historical_analogs"}:
            effective = number(prediction["effective_paths"])
        else:
            raise ValueError("Unsupported forecast engine")
        result["effective_observed_paths"] = str(effective)
        if effective < number(settings["minimum_effective_observed_paths"]):
            result["reason_codes"].append("INSUFFICIENT_EFFECTIVE_SUPPORT")
        plan = prediction.get("plan")
        if plan is None:
            result.update(screen_decision="stand_aside", reason_codes=result["reason_codes"] + ["NO_POSITIVE_LONG_PLAN"])
            return result
        if plan.get("reference_session") != prediction["reference_session"] or plan.get("as_of") != prediction["as_of"]:
            raise ValueError("Plan and forecast reference metadata disagree")
        if mode == "current_research" and as_utc(plan["timing"]["expires_at"]) <= as_utc(as_of):
            result["reason_codes"].append("EXPIRED_REFERENCE_PLAN")
        reference, target, stop = (number(plan[key]) for key in ("reference_price", "first_target_price", "invalidation_price"))
        if not target > reference > stop > 0:
            raise ValueError("Invalid reference/target/invalidation envelope")
        if number(prediction["reference_price"]) != reference:
            raise ValueError("Reference prices disagree")
        estimates = {row["cost_bps_per_side"]: row for row in prediction["execution"]}
        if len(estimates) != len(prediction["execution"]):
            raise ValueError("Duplicate cost scenario")
        primary = settings["primary_cost_bps_per_side"]
        if primary not in settings["cost_bps_per_side"]:
            raise ValueError("Primary cost is absent from policy")
        for cost in settings["cost_bps_per_side"]:
            fee = number(cost) / 10000
            if not 0 <= fee < Decimal("0.1"):
                raise ValueError("Unbounded cost input")
            scenario = estimates[cost]
            ev = [number(value) for value in scenario["estimated_net_return_pct_bounds"]]
            mass = [number(value) for value in scenario["estimated_target_before_stop_mass_bounds"]]
            if len(ev) != 2 or ev[0] > ev[1] or len(mass) != 2 or not 0 <= mass[0] <= mass[1] <= Decimal("1.000000000001"):
                raise ValueError("Invalid expected-return or estimated-mass bounds")
            upside, downside = net_return(reference, target, cost), net_return(reference, stop, cost)
            if downside >= 0:
                raise ValueError("Invalid net stop loss")
            ratio = upside / -downside
            result["metrics"].append({"cost_bps_per_side": cost, "net_reference_to_target_pct": str(upside),
                "net_reference_to_stop_pct": str(downside), "net_reward_risk": str(ratio),
                "estimated_path_ev_pct_bounds": [str(value) for value in ev],
                "estimated_target_first_mass_bounds": [str(value) for value in mass]})
            if cost == primary:
                if upside <= number(settings["minimum_net_reference_target_pct_exclusive"]):
                    result["reason_codes"].append("NONPOSITIVE_NET_TARGET_UPSIDE")
                if ratio < number(settings["minimum_net_reward_risk_inclusive"]):
                    result["reason_codes"].append("INSUFFICIENT_NET_REWARD_RISK")
                if ev[0] <= number(settings["minimum_estimated_path_ev_lower_pct_exclusive"]):
                    result["reason_codes"].append("NONPOSITIVE_ESTIMATED_PATH_EV")
                timing = scenario.get("experimental_first_target_hit_sessions")
                if settings["require_target_hit_timing_estimate"]:
                    if timing is None:
                        result["reason_codes"].append("TARGET_TIMING_UNAVAILABLE")
                    elif not isinstance(timing, list) or len(timing) != 2 or any(type(value) is not int for value in timing) or not 1 <= timing[0] <= timing[1] <= 5:
                        raise ValueError("Invalid five-session target timing range")
        result["screen_decision"] = "stand_aside" if result["reason_codes"] else "numerical_candidate"
    except (ValueError, KeyError, TypeError) as error:
        result.update(screen_decision="unavailable", reason_codes=["INVALID_OR_MISSING_FORECAST_INPUT"], input_reason=str(error))
    return result
