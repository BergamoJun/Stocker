"""Private v2 prototype, causal historical diagnostics, and prospective record.

The old ATR protocol/ledger is left intact. Outputs are not investment reports.
"""

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

import jsonschema

from .advance import latest_snapshot, verify_saved_raw
from . import peak_model as model
from . import peak_simulation
from .rights import as_utc, require_entitlement
from .sec_report import ROOT
from .swing_models import _reference_baseline, load_diagnostic_snapshot, load_price_snapshot, replay_five_sessions
from .validation import digest, now, save_new, stable_hash, value_fingerprint


def validate(value):
    schema = json.loads((ROOT / "schemas/peak_research.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(value)


def validate_candidate(value):
    schema = json.loads((ROOT / "schemas/peak_research.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator({"$ref": "#/$defs/candidate", "$defs": schema["$defs"]},
                                   format_checker=jsonschema.FormatChecker()).validate(value)


def diagnostic(bars, calendar, as_of, engine="fixed_ewma_simulation"):
    settings = model.policy()
    history = model.samples(bars) if engine == "historical_analogs" else None
    cases = []
    for index in range(59, len(bars) - 5, 5):
        future = bars[index + 1:index + 6]
        atr = model.atr14(bars[:index + 1])
        maximum = max(row.high for row in future)
        actual_peak_atr = float((maximum - bars[index].close) / atr)
        actual_peak_day = next(day for day, row in enumerate(future, 1) if row.high == maximum)
        case = {"reference_session": bars[index].session, "label_period_ended_at": calendar.close(future[-1].session),
                "label_available_at": max((row.available_at for row in future), key=as_utc),
                "point_in_time_verified": False, "status": "unavailable", "reason": None, "cost_scenarios": []}
        try:
            if engine == "historical_analogs":
                prediction = model.forecast(bars, calendar, as_of, query_index=index, training_samples=history)
                unconditional = model.forecast(bars, calendar, as_of, query_index=index, training_samples=history, unconditional=True)
            else:
                prediction = peak_simulation.forecast(bars, calendar, as_of, query_index=index)
                unconditional = peak_simulation.forecast(bars, calendar, as_of, query_index=index, zero_drift=True)
            actual_bin = model.bin_index(actual_peak_atr, settings)
            case.update(status="retrospective_diagnostic_not_validation", actual_peak_atr=actual_peak_atr,
                        actual_peak_day=actual_peak_day, actual_peak_bin=actual_bin,
                        prediction_training_paths=prediction["training_paths"],
                        latest_training_label_index=(max(row.label_end_index for row in history if row.label_end_index < index) if history else None),
                        last_parameter_observation_index=index,
                        reference_index=index, has_long_plan=prediction["plan"] is not None)
            for name, forecast in (("conditional", prediction), ("unconditional", unconditional)):
                cells = forecast["peak"]["joint_cells"]
                p_actual = sum(row["estimated_mass"] for row in cells if row["bin_index"] == actual_bin and row["day"] == actual_peak_day)
                case[name] = {"joint_brier": 1 + sum(row["estimated_mass"] ** 2 for row in cells) - 2 * p_actual,
                    "modal_price_bin_hit": forecast["peak"]["modal_bin_index"] == actual_bin,
                    "modal_price_mass": forecast["peak"]["estimated_modal_bin_mass"],
                    "peak_day_absolute_error": abs(forecast["peak"]["conditional_day_mode"] - actual_peak_day)}
            try:
                baseline = _reference_baseline(bars[:index + 1], calendar, as_of)
            except ValueError:
                baseline = None
            for cost in settings["cost_bps_per_side"]:
                if prediction["plan"] is None:
                    outcome = {"outcome": "stand_aside_no_positive_modal_target", "net_return_pct": "0"}
                else:
                    outcome = replay_five_sessions(prediction["plan"], future, calendar, Decimal(cost))
                atr_outcome = (replay_five_sessions(baseline, future, calendar, Decimal(cost)) if baseline else
                               {"outcome": "invalid_target_configuration", "net_return_pct": None})
                hold = model.net_return(future[0].open, future[-1].close, cost)
                case["cost_scenarios"].append({"cost_bps_per_side": cost, "peak_model": outcome,
                    "atr_baseline": atr_outcome, "five_session_hold_net_pct": str(hold)})
        except ValueError as error:
            case["reason"] = str(error)
        cases.append(case)
    split = int(Decimal(len(cases)) * Decimal(settings["evaluation"]["training_fraction"]))
    parts = {}
    for name, rows in (("training", cases[:split]), ("held_out", cases[split:])):
        eligible = [row for row in rows if row["status"] != "unavailable"]
        summary = {"cases": len(rows), "forecastable_cases": len(eligible),
                   "unavailable_cases": len(rows) - len(eligible), "minimum_sample_gate_passed": False}
        if eligible:
            summary.update({key + "_mean_joint_brier": sum(row[key]["joint_brier"] for row in eligible) / len(eligible)
                            for key in ("conditional", "unconditional")})
            summary["modal_price_bin_coverage"] = sum(row["conditional"]["modal_price_bin_hit"] for row in eligible) / len(eligible)
            summary["mean_peak_day_absolute_error"] = sum(row["conditional"]["peak_day_absolute_error"] for row in eligible) / len(eligible)
        summary["execution"] = []
        for cost in settings["cost_bps_per_side"]:
            scenario = [next(item for item in row["cost_scenarios"] if item["cost_bps_per_side"] == cost) for row in eligible]
            # All comparators use the same jointly resolved cohort. Others remain counted.
            resolved = [row for row in scenario if row["peak_model"]["net_return_pct"] is not None
                        and row["atr_baseline"]["net_return_pct"] is not None]
            totals = {"peak_model": None, "atr_baseline": None, "five_session_hold": None}
            if resolved:
                totals = {key: str(sum((Decimal(row[key]["net_return_pct"]) for row in resolved), Decimal(0)) / len(resolved))
                          for key in ("peak_model", "atr_baseline")}
                totals["five_session_hold"] = str(sum((Decimal(row["five_session_hold_net_pct"]) for row in resolved), Decimal(0)) / len(resolved))
            summary["execution"].append({"cost_bps_per_side": cost, "same_cohort_resolved": len(resolved),
                "excluded_ambiguous_or_untradeable": len(scenario) - len(resolved), "mean_net_return_pct": totals})
        summary["minimum_sample_gate_passed"] = bool(summary["execution"]) and all(
            row["same_cohort_resolved"] >= settings["evaluation"]["minimum_held_out_resolved_for_review"] for row in summary["execution"])
        parts[name] = {"summary": summary, "cases": rows}
    return {"status": "retrospective_diagnostic_not_validation", "engine": engine, "point_in_time_verified": False,
            "partitions": parts, "validation_id": None,
            "limitations": ["Current corrected/split-adjusted historical vintage; not historical availability",
                "Fixed simulation moments use observations through reference; supervised analog labels must end strictly before reference",
                "Warm-up/OOD/unavailable cases are retained; model can stand aside with cash return zero",
                "Joint Brier compares conditional and unconditional estimates on the same fixed categories",
                "Small single-stock hold-out; no tuning or calibration approval from this diagnostic"]}


def prospective_record(prediction, snapshot, bars, calendar, as_of):
    validate_candidate(prediction)
    if prediction.get("plan") is None:
        return {"status": "withheld", "reason": prediction.get("plan_unavailable_reason", "No plan")}
    plan = prediction["plan"]
    recorded = now()
    entry = calendar.calendar.session_open(plan["timing"]["sessions"][0]).isoformat()
    if as_utc(as_of) > as_utc(recorded) or as_utc(recorded) >= as_utc(entry):
        return {"status": "withheld", "reason": "Research forecast must be recorded before the next open"}
    key = stable_hash({"symbol": snapshot["symbol"], "session": plan["reference_session"], "engine": prediction["engine"], "policy": digest(ROOT / "config/peak_model_policy.json")})[:24]
    folder = ROOT / "reports/private/peak_ledger" / key
    if (folder / "forecast.json").exists():
        return {"status": "already_recorded", "path": str(folder.resolve()), "record_id": key}
    save_new(folder / "forecast.json", {"schema_version": "1.0.0", "status": "prospective_research_forecast",
        "record_id": key, "symbol": snapshot["symbol"], "recorded_at": recorded, "as_of": as_of,
        "entry_at": entry, "snapshot_id": snapshot["snapshot_id"], "source_ids": snapshot["source_ids"],
        "is_illustrative": snapshot["is_illustrative"],
        "policy_sha256": digest(ROOT / "config/peak_model_policy.json"), "prediction": prediction,
        "history_fingerprints": {bar.session: value_fingerprint(bar) for bar in bars},
        "point_in_time_history_verified": snapshot["point_in_time_verified"], "validation_id": None})
    return {"status": "prospective_research_forecast", "path": str(folder.resolve()), "record_id": key}


def settle_records(snapshot, bars, calendar, as_of, ledger_dir=None, *, record_ids=None):
    ledger = Path(ledger_dir) if ledger_dir else ROOT / "reports/private/peak_ledger"
    by_day, results = {bar.session: bar for bar in bars}, []
    for path in sorted(ledger.glob("*/forecast.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record_ids is not None and record["record_id"] not in record_ids:
            continue
        if record["symbol"] != snapshot["symbol"] or record["is_illustrative"] != snapshot["is_illustrative"]:
            continue
        output = path.with_name("outcome.json")
        if output.exists():
            results.append({"record_id": record["record_id"], "status": "already_settled"})
            continue
        prediction = record["prediction"]
        validate_candidate(prediction)
        sessions = prediction["plan"]["timing"]["sessions"]
        if any(day not in by_day for day in sessions) or as_utc(calendar.close(sessions[-1])) > as_utc(as_of):
            results.append({"record_id": record["record_id"], "status": "awaiting_five_session_outcome"})
            continue
        overlap = record["history_fingerprints"].keys() & by_day.keys()
        if (record["policy_sha256"] != digest(ROOT / "config/peak_model_policy.json") or
                prediction["reference_session"] not in overlap or
                any(record["history_fingerprints"][day] != value_fingerprint(by_day[day]) for day in overlap)):
            results.append({"record_id": record["record_id"], "status": "policy_or_input_revision_outcome_withheld"})
            continue
        future = [by_day[day] for day in sessions]
        observed = max((row.available_at for row in future), key=as_utc)
        if as_utc(observed) > as_utc(as_of) or as_utc(observed) < as_utc(record["recorded_at"]):
            results.append({"record_id": record["record_id"], "status": "outcome_observation_inconsistent"})
            continue
        outcomes = []
        for cost in model.policy()["cost_bps_per_side"]:
            outcome = replay_five_sessions(prediction["plan"], future, calendar, Decimal(cost))
            outcome["label_period_ended_at"] = outcome["label_available_at"]
            outcome["label_available_at"] = observed
            outcomes.append({"cost_bps_per_side": cost, "result": outcome})
        save_new(output, {"schema_version": "1.0.0", "status": "prospective_research_outcome",
            "record_id": record["record_id"], "evaluated_at": now(), "as_of": as_of,
            "outcome_snapshot_id": snapshot["snapshot_id"], "source_ids": snapshot["source_ids"],
            "outcomes": outcomes, "validation_id": None, "released_score": None})
        results.append({"record_id": record["record_id"], "status": "prospective_research_outcome"})
    return results


def markdown(result):
    prediction = result.get("current_candidate") or {}
    lines = ["# 단기 고점·수익 기회 연구 결과", "", "**미검증 연구용. 투자 추천 보고서가 아니다.**", "",
        f"기준: {result['as_of']} / 모델: {result['methodology_id']}",
        "고점은 향후 5세션 최고 고가이며, 실제 매도 가능한 시각/체결가를 보장하지 않는다.", ""]
    if prediction.get("peak"):
        peak = prediction["peak"]
        lines += [f"- 최신 확보 종가: {prediction['reference_price']}",
            f"- 고점 최빈 가격 구간(USD): {peak['modal_price_interval']}",
            f"- 해당 구간 조건부 고점 형성 세션 범위: {peak['conditional_day_range']}",
            f"- 엔진: {prediction['engine']} / 시뮬레이션·선택 경로 {prediction['selected_paths']}개 / 실제 관측 기반 유효 표본 {prediction['effective_paths']}"]
    if prediction.get("plan"):
        plan = prediction["plan"]
        lines += [f"- 연구용 1차 목표: {plan['first_target_price']} / 무효화: {plan['invalidation_price']}",
            f"- 손익비: {plan['reward_risk_ratio']}",
            f"- 비용 후 기준가격→목표 수익률(%): {prediction['profit_opportunity']['net_reference_to_target_pct']}",
            f"- 고점 구간의 비용 후 잠재 수익률 범위(%): {prediction['profit_opportunity']['net_modal_peak_interval_pct']}",
            f"- 수익 기회 연구 요소: {prediction['profit_opportunity']['experimental_points']} / 25점",
            f"- 목표 우선 도달 경로의 조건부 도달 세션 범위: {prediction['execution'][0]['experimental_first_target_hit_sessions']}"]
    else:
        lines.append("- 양의 목표 후보 보류: " + prediction.get("plan_unavailable_reason", result.get("current_candidate_reason", "입력 부족")))
    lines += ["", "전체 1~100점은 뉴스·실적·시장/섹터 입력 검토 없이는 산출하지 않는다. 관측 빈도는 검증된 확률이 아니다.",
              "가격·기간·추천 점수의 보고서 제공 값과 검증 ID는 null이다. 상세 사례와 평가 지표는 JSON을 참조한다.", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Private modal-peak prototype and diagnostic; no API or LLM calls")
    parser.add_argument("--prices", type=Path)
    parser.add_argument("--price-entitlement", type=Path)
    parser.add_argument("--engine", choices=["fixed_ewma_simulation", "historical_analogs"], default="fixed_ewma_simulation")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports/private/peak_research")
    args = parser.parse_args(argv)
    if not args.output_dir.resolve().is_relative_to(ROOT / "reports/private"):
        parser.error("Peak artifacts must stay under reports/private")
    if bool(args.prices) != bool(args.price_entitlement):
        parser.error("Supply both prices and entitlement, or neither to use the latest saved BE snapshot")
    snapshot_path, entitlement_path = (args.prices, args.price_entitlement) if args.prices else latest_snapshot()
    cutoff = now()
    settings = model.policy()
    if as_utc(cutoff) < as_utc(settings["frozen_at"]):
        raise ValueError("Run precedes model policy registration")
    entitlement = json.loads(entitlement_path.read_text(encoding="utf-8"))
    if args.engine == "historical_analogs":
        try:
            require_entitlement(entitlement, ["model_training"], cutoff, synthetic=entitlement.get("is_illustrative") is True)
        except ValueError as error:
            destination = args.output_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            save_new(destination / "peak_readiness.json", {"status": "data_use_not_cleared", "engine": args.engine,
                "reason": str(error), "released_score": None, "validation_id": None})
            print(json.dumps({"status": "data_use_not_cleared", "output_dir": str(destination.resolve()), "released_score": None}))
            return 2
    verify_saved_raw(snapshot_path, entitlement)
    data, bars, calendar = load_diagnostic_snapshot(snapshot_path, entitlement, cutoff)
    if data["symbol"] != "BE":
        raise ValueError("First peak-model profile is BE only")
    destination = args.output_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    registration = {"registered_at": cutoff, "policy_sha256": digest(ROOT / "config/peak_model_policy.json"),
        "price_snapshot_sha256": digest(snapshot_path), "raw_sha256": data["raw_sha256"],
        "code_sha256": {p.name: digest(p) for p in (Path(__file__), ROOT / "research/peak_model.py", ROOT / "research/peak_simulation.py", ROOT / "research/swing_models.py")}}
    save_new(destination / "registration.json", registration)
    result = {"schema_version": "1.0.0", "status": "research_only_unvalidated", "as_of": cutoff,
        "methodology_id": settings["id"], "registration": registration,
        "source_ids": data["source_ids"], "snapshot_id": data["snapshot_id"], "is_illustrative": data["is_illustrative"],
        "current_candidate": None, "current_candidate_reason": None, "prospective_record": None,
        "prospective_outcomes": settle_records(data, bars, calendar, cutoff),
        "engine": args.engine, "diagnostic": diagnostic(bars, calendar, cutoff, args.engine), "released_price_plan": None, "released_score": None,
        "released_expected_target_sessions": None, "validation_id": None}
    try:
        _, current, _ = load_price_snapshot(snapshot_path, entitlement, cutoff)
        result["current_candidate"] = (model.forecast(current, calendar, cutoff) if args.engine == "historical_analogs" else
                                       peak_simulation.forecast(current, calendar, cutoff))
        result["prospective_record"] = prospective_record(result["current_candidate"], data, current, calendar, cutoff)
    except ValueError as error:
        result["current_candidate_reason"] = str(error)
    result["attractiveness"] = model.combined_score(result["current_candidate"] or {}, {}, {"status": "unavailable"})
    validate(result)
    save_new(destination / "peak_research.json", result)
    with (destination / "peak_research.md").open("x", encoding="utf-8") as output:
        output.write(markdown(result))
    print(json.dumps({"status": result["status"], "output_dir": str(destination.resolve()),
        "price_bars": len(bars), "diagnostic_cases": sum(part["summary"]["cases"] for part in result["diagnostic"]["partitions"].values()),
        "held_out_forecastable_cases": result["diagnostic"]["partitions"]["held_out"]["summary"]["forecastable_cases"],
        "candidate_available": result["current_candidate"] is not None,
        "feasible_long_plan": bool((result["current_candidate"] or {}).get("plan")),
        "released_score": None}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
