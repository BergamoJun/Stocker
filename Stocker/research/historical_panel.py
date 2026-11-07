"""Checkpointed, price-only replay with shared calendar and untouched finals."""

from collections import Counter
from decimal import Decimal
import json
from pathlib import Path
import time

from . import peak_model, peak_simulation, trade_screen
from .historical_preflight import partitions
from .local_files import atomic_json
from .panel_prices import load
from .sec_report import ROOT
from .swing_models import atr14, replay_five_sessions
from .validation import digest, now, stable_hash


ENGINE_FILES = ["research/peak_simulation.py", "research/peak_model.py", "research/swing_models.py",
    "research/trade_screen.py", "research/panel_prices.py", "research/historical_panel.py",
    "config/peak_model_policy.json", "config/trade_screen_policy.json", "config/historical_evaluation_protocol.json",
    "config/historical_panel_policy.json"]


def engine_hashes():
    return {name: digest(ROOT / name) for name in ENGINE_FILES}


def references(phases=("development", "validation")):
    if not phases or any(name not in {"development", "validation"} for name in phases) or len(set(phases)) != len(phases):
        raise ValueError("Final evaluation remains locked; only development/validation may run")
    settings = json.loads((ROOT / "config/historical_evaluation_protocol.json").read_text(encoding="utf-8"))
    plan = partitions(settings)
    return [{"phase": phase["phase"], **row} for phase in plan["partitions"] if phase["phase"] in phases for row in phase["references"]]


def execution(prediction, screen, future, calendar, cost):
    hold = peak_model.net_return(future[0].open, future[-1].close, cost)
    plan = prediction["plan"]
    if plan is None:
        outcome = {"outcome": "stand_aside_no_positive_plan", "net_return_pct": "0", "hit_session": None}
        bounds = [Decimal(0), Decimal(0)]
    else:
        outcome = replay_five_sessions(plan, future, calendar, Decimal(cost))
        if outcome["outcome"] == "ambiguous_same_bar":
            bounds = [peak_model.net_return(future[0].open, Decimal(plan[key]), cost)
                      for key in ("invalidation_price", "first_target_price")]
        elif outcome["outcome"] == "untradeable_open_gap":
            # The frozen execution rule declines entry when the first open is outside the bracket.
            bounds = [Decimal(0), Decimal(0)]
        else:
            bounds = [Decimal(outcome["net_return_pct"])] * 2
    screened = bounds if screen["screen_decision"] == "numerical_candidate" else [Decimal(0), Decimal(0)]
    if screen["screen_decision"] == "unavailable":
        screened = None  # A broken calculation is never a profitable-looking cash decision.
    timing = next((row["experimental_first_target_hit_sessions"] for row in prediction["execution"] if row["cost_bps_per_side"] == cost), None)
    hit = outcome.get("hit_session")
    return {"cost_bps_per_side": cost, "unfiltered_outcome": outcome,
        "unfiltered_net_return_pct_bounds": list(map(str, bounds)),
        "screened_net_return_pct_bounds": list(map(str, screened)) if screened else None,
        "five_session_hold_net_return_pct": str(hold),
        "target_timing_evaluable": hit is not None and timing is not None,
        "target_hit_inside_conditional_timing_range": timing[0] <= hit <= timing[1] if hit is not None and timing else None}


def evaluate_case(bars, calendar, ref, *, as_of, source_ids, is_illustrative):
    case = {**ref, "status": "unavailable", "reason_code": None, "is_illustrative": is_illustrative,
        "point_in_time_verified": False, "actual_evaluation_at": as_of,
        "hypothetical_decision_at": calendar.close(ref["reference_session"]), "source_ids": source_ids,
        "screen_decision": "unavailable", "cost_scenarios": [], "released_score": None}
    index = next((i for i, bar in enumerate(bars) if bar.session == ref["reference_session"]), None)
    if index is None or index < 59:
        return {**case, "reason_code": "MISSING_REFERENCE_OR_INSUFFICIENT_HISTORY"}
    # Only the prefix is passed to the forecast and screen. Future prices are read afterwards.
    try:
        prediction = peak_simulation.forecast(bars[:index + 1], calendar, as_of)
        screen = trade_screen.assess(prediction, as_of=as_of, source_ids=source_ids,
            is_illustrative=is_illustrative, mode="historical_diagnostic")
    except (ValueError, ArithmeticError):
        return {**case, "reason_code": "FORECAST_UNSUPPORTED_OR_INVALID"}
    future = bars[index + 1:index + 6]
    if [bar.session for bar in future] != ref["label_sessions"]:
        return {**case, "reason_code": "MISSING_FIVE_SESSION_LABEL"}
    if prediction["plan"] and prediction["plan"]["timing"]["sessions"] != ref["label_sessions"]:
        raise ValueError("Forecast and frozen calendar disagree")
    if screen["screen_decision"] == "unavailable":
        return {**case, "reason_code": "COST_SCREEN_UNAVAILABLE"}
    maximum = max(bar.high for bar in future)
    peak_day = next(day for day, bar in enumerate(future, 1) if bar.high == maximum)
    actual_z = float((maximum - bars[index].close) / Decimal(prediction["atr14"]))
    actual_bin = peak_model.bin_index(actual_z, peak_model.policy())
    peak = prediction["peak"]
    cells = peak["joint_cells"]
    p_actual = sum(row["estimated_mass"] for row in cells if row["bin_index"] == actual_bin and row["day"] == peak_day)
    atr_pct = atr14(bars[:index + 1]) / bars[index].close * 100
    dollar_volume = sum((bar.close * bar.volume for bar in bars[max(0, index - 19):index + 1]), Decimal(0)) / min(20, index + 1)
    modal_mid = (peak["modal_bin_index"] + .5) * peak_model.policy()["peak_bin_width_atr"]
    case.update(status="retrospective_diagnostic_not_validation", screen_decision=screen["screen_decision"],
        screen_reason_codes=screen["reason_codes"], forecast=prediction,
        label_last_session=ref["label_sessions"][-1], label_available_at=max(bar.available_at for bar in future),
        actual_peak_atr=actual_z, actual_peak_day=peak_day,
        absolute_peak_error_atr=abs(actual_z - modal_mid),
        modal_price_bin_hit=actual_bin == peak["modal_bin_index"],
        reference_month=ref["reference_session"][:7],
        volatility_group="atr_below_2pct" if atr_pct < 2 else "atr_2_to_4pct" if atr_pct < 4 else "atr_at_least_4pct",
        liquidity_group="dollar_volume_below_1m" if dollar_volume < 1_000_000 else "dollar_volume_1m_to_10m" if dollar_volume < 10_000_000 else "dollar_volume_at_least_10m",
        peak_day_absolute_error=abs(peak["conditional_day_mode"] - peak_day),
        joint_brier=1 + sum(row["estimated_mass"] ** 2 for row in cells) - 2 * p_actual,
        cost_scenarios=[execution(prediction, screen, future, calendar, cost) for cost in peak_model.policy()["cost_bps_per_side"]])
    return case


def summarize(cases):
    usable = [row for row in cases if row["status"] == "retrospective_diagnostic_not_validation"]
    result = {"cases": len(cases), "forecastable_cases": len(usable), "unavailable_cases": len(cases) - len(usable),
        "reason_counts": dict(Counter(row["reason_code"] for row in cases if row["reason_code"])),
        "screen_counts": dict(Counter(row["screen_decision"] for row in cases)), "cost_scenarios": [],
        "modal_price_bin_hit_fraction": sum(row["modal_price_bin_hit"] for row in usable) / len(usable) if usable else None,
        "mean_peak_day_absolute_error_sessions": sum(row["peak_day_absolute_error"] for row in usable) / len(usable) if usable else None,
        "mean_joint_brier": sum(row["joint_brier"] for row in usable) / len(usable) if usable else None,
        "mean_absolute_peak_error_atr": sum(row["absolute_peak_error_atr"] for row in usable) / len(usable) if usable else None,
        "independent_sample_count": None, "uncertainty_intervals": None, "performance_validated": False}
    for cost in peak_model.policy()["cost_bps_per_side"]:
        rows = [next(item for item in row["cost_scenarios"] if item["cost_bps_per_side"] == cost) for row in usable]
        shared = [row for row in rows if row["screened_net_return_pct_bounds"] is not None]
        def mean(values):
            return str(sum(values, Decimal(0)) / len(values)) if values else None
        scenarios = {"cost_bps_per_side": cost, "matched_cases": len(shared),
            "ambiguous_cases": sum(row["unfiltered_outcome"]["outcome"] == "ambiguous_same_bar" for row in rows),
            "entry_gap_declined_cases": sum(row["unfiltered_outcome"]["outcome"] == "untradeable_open_gap" for row in rows),
            "hold_mean_net_return_pct": mean([Decimal(row["five_session_hold_net_return_pct"]) for row in shared])}
        outcomes = Counter(row["unfiltered_outcome"]["outcome"] for row in rows)
        timing = [row for row in rows if row["target_timing_evaluable"]]
        scenarios.update(outcome_counts=dict(outcomes), target_first_fraction_denominator="all_forecastable_cases_including_stand_aside",
            target_before_stop_fraction_bounds=[str(Decimal(outcomes["target"] + extra) / len(rows)) if rows else None
                                               for extra in (0, outcomes["ambiguous_same_bar"])],
            target_timing_evaluable_cases=len(timing),
            conditional_target_timing_coverage=mean([Decimal(int(row["target_hit_inside_conditional_timing_range"])) for row in timing]))
        for name in ("unfiltered", "screened"):
            scenarios[name + "_mean_net_return_pct_bounds"] = [mean([Decimal(row[name + "_net_return_pct_bounds"][i]) for row in shared]) for i in (0, 1)]
            scenarios[name + "_mean_excess_to_hold_pct_bounds"] = [mean([Decimal(row[name + "_net_return_pct_bounds"][i]) - Decimal(row["five_session_hold_net_return_pct"]) for row in shared]) for i in (0, 1)]
        result["cost_scenarios"].append(scenarios)
    return result


def evaluate_stock(price_path, profile, record, folder, *, as_of=None, phases=("development", "validation"),
                   pilot=False, finish_before=None):
    if profile.get("symbol_partition") == "unseen_symbol_final_only":
        raise ValueError("Unseen-symbol outcomes remain locked")
    if not pilot and profile.get("symbol_partition") != "development_and_validation":
        raise ValueError("A frozen cohort partition is required outside the engineering pilot")
    as_of = as_of or now()
    data, bars, calendar = load(price_path, profile, record, as_of)
    refs = references(phases)
    folder = Path(folder)
    fingerprint = {"price_sha256": digest(price_path), "profile_sha256": stable_hash(profile),
        "engines": engine_hashes(), "phases": list(phases), "pilot": pilot}
    manifest_path = folder / "manifest.json"
    if manifest_path.exists():
        if json.loads(manifest_path.read_text(encoding="utf-8")) != fingerprint:
            raise ValueError("Frozen replay inputs/code changed; use a new run")
    else:
        atomic_json(manifest_path, fingerprint)
    completed = []
    for ref in refs:
        target = folder / "cases" / (ref["reference_session"] + ".json")
        if target.exists():
            row = json.loads(target.read_text(encoding="utf-8"))
            if row.get("reference_session") != ref["reference_session"] or row.get("phase") != ref["phase"] or row.get("label_sessions") != ref["label_sessions"]:
                raise ValueError("Checkpoint calendar changed")
        else:
            if finish_before is not None and time.monotonic() >= finish_before:
                break
            row = evaluate_case(bars, calendar, ref, as_of=as_of, source_ids=data["source_ids"], is_illustrative=data["is_illustrative"])
            atomic_json(target, row)
        completed.append(row)
    result = {"schema_version": "1.0.0", "status": "engineering_pilot_not_sector_evidence" if pilot else "research_panel_price_only",
        "is_illustrative": data["is_illustrative"], "symbol": profile["symbol"], "sector_id": profile["sector_id"],
        "source_ids": data["source_ids"], "manifest_sha256": digest(manifest_path), "as_of": as_of,
        "expected_cases": len(refs), "completed_cases": len(completed), "complete": len(completed) == len(refs),
        "partitions": {phase: summarize([row for row in completed if row["phase"] == phase]) for phase in phases},
        "final_evaluation_locked": True, "final_cases_executed": 0, "model_training_performed": False,
        "released_score": None, "point_in_time_verified": False,
        "limitations": ["Retrospective current-vintage diagnostic; not a validated live forecast",
            "Same-date stocks are correlated; case count is not an independent sample count",
            "Arithmetic case means are not compounded portfolio returns or a deployment approval",
            "Missing/invalid cases remain unavailable; no cash-return substitution for data failures",
            "No historical news/fundamentals were filled using current knowledge",
            "Conditional target-hit timing differs from the day of the highest price"]}
    import jsonschema
    schema = json.loads((ROOT / "schemas/historical_panel.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator({"$ref": "#/$defs/symbol_result", "$defs": schema["$defs"]}).validate(result)
    atomic_json(folder / "result.json", result, replace=True)
    return result
