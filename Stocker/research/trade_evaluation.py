"""Fresh independent-seed cost screen evaluation. Synthetic inputs only."""

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import gzip
import json
import os
from pathlib import Path

import jsonschema

from . import learned_peak as learned, learned_research as workflow, peak_model as base, robustness, sample_audit, trade_screen
from .rights import as_utc
from .sec_report import ROOT
from .swing_models import XnysCalendar, replay_five_sessions
from .validation import digest, now, save_new, stable_hash


def protocol():
    settings = json.loads((ROOT / "config/trade_screen_evaluation.json").read_text(encoding="utf-8"))
    audit, generation = sample_audit.protocol()
    old_seeds = set(audit["seeds"]) | set(generation["seeds"]) | {20261107, 20261108}
    if (settings["screen_policy_sha256"] != digest(ROOT / "config/trade_screen_policy.json")
            or settings["sample_audit_policy_sha256"] != digest(ROOT / "config/sample_audit_protocol.json")
            or settings["real_market_release_allowed"] is not False or settings["monthly_data_budget_usd"] != 0
            or len(settings["fresh_seeds"]) != 24 or len(set(settings["fresh_seeds"])) != 24
            or set(settings["fresh_seeds"]) & old_seeds):
        raise ValueError("Fresh cost-screen evaluation must pin policies and new independent seeds")
    return settings, audit, generation


def paired_change_bounds(screen, original, cost):
    if screen["screen_decision"] == "numerical_candidate":
        return Decimal(0), Decimal(0)  # Identical execution, including shared ambiguity.
    if screen["screen_decision"] == "stand_aside":
        lower, upper = robustness.return_bounds(original, cost)
        return -upper, -lower
    raise ValueError("Unknown screen result cannot be cash")


def screened_execution(screen, original):
    if screen["screen_decision"] == "numerical_candidate":
        return original
    if screen["screen_decision"] == "stand_aside":
        return {"outcome": "stand_aside_no_long_plan", "net_return_pct": "0"}
    return {"outcome": "unavailable", "net_return_pct": None}


def summarize(cases, costs):
    comparable = [row for row in cases if row.get("screen", {}).get("screen_decision") in {"numerical_candidate", "stand_aside"}]
    reasons = Counter(code for row in comparable for code in row["screen"]["reason_codes"])
    result = {"planned_cases": len(cases), "comparable_forecasts": len(comparable),
        "unavailable_forecast_or_screen": len(cases) - len(comparable),
        "accepted_references": sum(row["screen"]["screen_decision"] == "numerical_candidate" for row in comparable),
        "rejected_references": sum(row["screen"]["screen_decision"] == "stand_aside" for row in comparable),
        "reason_counts": dict(reasons), "costs": []}
    for cost in costs:
        row = {"cost_bps_per_side": cost, "same_reference_cohort": len(comparable), "models": {}}
        for name in ("unfiltered", "screened"):
            outcomes = [item["realized"][cost][name] for item in comparable]
            bounds = [robustness.return_bounds(item, cost) for item in outcomes]
            row["models"][name] = {"outcome_counts": dict(Counter(item["outcome"] for item in outcomes)),
                "entered_cases": sum(item["outcome"] in {"target", "stop", "timeout", "ambiguous_same_bar"} for item in outcomes),
                "mean_net_return_pct_bounds": ([str(sum((value[i] for value in bounds), Decimal(0)) / len(bounds)) for i in (0, 1)] if bounds else None)}
        differences = [paired_change_bounds(item["screen"], item["realized"][cost]["unfiltered"], cost) for item in comparable]
        row["paired_mean_delta_pct_bounds"] = ([str(sum((value[i] for value in differences), Decimal(0)) / len(differences)) for i in (0, 1)] if differences else None)
        result["costs"].append(row)
    return result


def evaluate_seed(seed, evaluation, audit, generation, screen_settings, cutoff, destination):
    generation = deepcopy(generation)
    generation.update(bars_per_dataset=audit["bars_per_dataset"], fixed_stress_start_index=audit["fixed_stress_start_index"])
    fit_settings = deepcopy(learned.policy())
    fit_settings.update(id=evaluation["id"], fixed_partition_case_boundaries=audit["fixed_partition_case_boundaries"], active_feature_indices=list(range(8)))
    calendar = XnysCalendar("2020-01-01", "2027-01-31")
    records = []
    for scenario in generation["scenarios"]:
        record = {"seed": seed, "scenario": scenario["id"], "status": "unavailable", "reason": None, "summary": None}
        try:
            bars, _, _ = robustness.generate(scenario, seed, generation, calendar)
            train, calibration, held, counts = learned.partitions(base.samples(bars), fit_settings)
            regressor = learned.fit(train, calibration, fit_settings)
            cases = []
            for actual in held:
                index = actual.reference_index
                case = {"reference_session": bars[index].session, "reference_index": index,
                    "source_ids": ["local-matched-synthetic-generator-v1"], "is_illustrative": True, "realized": {}}
                try:
                    prediction = learned.forecast(regressor, bars, calendar, cutoff, query_index=index)
                    screened = trade_screen.assess(prediction, as_of=cutoff, source_ids=case["source_ids"],
                        is_illustrative=True, mode="historical_diagnostic", settings=screen_settings)
                    case.update(screen=screened, forecast_sha256=stable_hash(prediction))
                    for cost in screen_settings["cost_bps_per_side"]:
                        original = replay_five_sessions(prediction["plan"], bars[index + 1:index + 6], calendar, Decimal(cost)) if prediction["plan"] else {"outcome": "stand_aside_no_long_plan", "net_return_pct": "0"}
                        after = screened_execution(screened, original)
                        case["realized"][cost] = {"unfiltered": original, "screened": after}
                except ValueError as error:
                    case["unavailable_reason"] = str(error)
                cases.append(case)
            result = {"schema_version": "1.0.0", "status": "research_only_unvalidated", "is_illustrative": True,
                "data_label": workflow.LABEL, "source_ids": ["local-matched-synthetic-generator-v1"],
                "seed": seed, "scenario": scenario, "input_sha256": stable_hash([repr(row) for row in bars]),
                "model": regressor.artifact(), "counts": counts, "cases": cases,
                "summary": summarize(cases, screen_settings["cost_bps_per_side"]), "released_score": None, "validation_id": None}
            path = Path(destination) / f"{scenario['id']}_{seed}.json.gz"
            with gzip.open(path, "xt", encoding="utf-8") as output:
                json.dump(result, output, ensure_ascii=False, separators=(",", ":"))
            record.update(status="completed", summary=result["summary"], artifact={"path": path.name, "sha256": digest(path)})
        except (ValueError, OSError) as error:
            record["reason"] = str(error)
        save_new(Path(destination) / f"{scenario['id']}_{seed}_progress.json", record)
        print(json.dumps({"scenario": scenario["id"], "seed": seed, "status": record["status"],
            "accepted": (record["summary"] or {}).get("accepted_references"), "comparable": (record["summary"] or {}).get("comparable_forecasts")}), flush=True)
        records.append(record)
    return records


def aggregate(records, evaluation, generation):
    result = []
    for scenario in generation["scenarios"]:
        selected = [row for row in records if row["scenario"] == scenario["id"]]
        valid = [row for row in selected if row["status"] == "completed"]
        row = {"scenario": scenario["id"], "planned_seeds": len(evaluation["fresh_seeds"]), "completed_seeds": len(valid),
            "planned_cases": sum(item["summary"]["planned_cases"] for item in valid),
            "comparable_forecasts": sum(item["summary"]["comparable_forecasts"] for item in valid),
            "accepted_references": sum(item["summary"]["accepted_references"] for item in valid), "costs": []}
        for cost in ("10", "25"):
            summaries = [next(item for item in record["summary"]["costs"] if item["cost_bps_per_side"] == cost) for record in valid]
            available = [item for item in summaries if item["paired_mean_delta_pct_bounds"] is not None]
            stats = {"cost_bps_per_side": cost, "seed_count_with_comparison": len(available), "models": {}}
            for name in ("unfiltered", "screened"):
                means = [[float(item["models"][name]["mean_net_return_pct_bounds"][i]) for item in available] for i in (0, 1)]
                stats["models"][name] = {"mean_of_seed_return_pct_bounds": ([sum(values) / len(values) for values in means] if available else None),
                    "entered_cases": sum(item["models"][name]["entered_cases"] for item in available),
                    "lower_mean_nominal_99_8_interval": (sample_audit.mean_interval(means[0], 3.486) if len(available) == 24 else None)}
            differences = [float(item["paired_mean_delta_pct_bounds"][0]) for item in available]
            stats["paired_delta_lower_nominal_99_8_interval"] = sample_audit.mean_interval(differences, 3.486) if len(available) == 24 else None
            stats["all_cash_after_screen"] = bool(available) and stats["models"]["screened"]["entered_cases"] == 0
            row["costs"].append(stats)
        result.append(row)
    return result


def validate(value):
    schema = json.loads((ROOT / "schemas/trade_evaluation.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(value)
    planned = {(scenario["id"], seed) for scenario in value["generation"]["scenarios"] for seed in value["evaluation"]["fresh_seeds"]}
    actual = [(row["scenario"], row["seed"]) for row in value["records"]]
    if len(actual) != len(set(actual)) or set(actual) != planned:
        raise ValueError("Missing or duplicate preregistered cost-screen dataset")


def markdown(result):
    lines = ["# 비용·위험 필터 새 시드 평가", "", workflow.LABEL, "",
        "같은 예측 모델의 필터 전후를 비교한다. 거절한 기준일은 현금 0으로 유지하고 예측 불가는 별도로 센다.",
        "표의 수익률은 시드별 기준일 평균의 평균이며 누적 포트폴리오 수익률이 아니다. 체결 모호성은 경계로 보존한다.", "",
        "| 조건 | 연구 후보/비교 가능 | 편도 25bp 필터 전 손익 경계(%) | 필터 후 손익 경계(%) |", "|---|---:|---|---|"]
    for row in result["aggregate"]:
        cost = next(item for item in row["costs"] if item["cost_bps_per_side"] == "25")
        def interval(name):
            values = cost["models"][name]["mean_of_seed_return_pct_bounds"]
            return f"[{values[0]:.4f}, {values[1]:.4f}]" if values else "보류"
        lines.append(f"| {row['scenario']} | {row['accepted_references']}/{row['comparable_forecasts']} | {interval('unfiltered')} | {interval('screened')} |")
    lines += ["", "모든 거래를 거절한 경우 수익률 0은 미진입 결과이며 수익성을 검증한 것이 아니다. 손익 구간은 주어진 일봉 체결 가정의 경계이며 보장 수익/통계적 신뢰구간과 다르다.",
        "시드 간 근사 t 구간과 기준일별 결과는 JSON을 참조한다. 실제 BE 학습, 확률 보정, 뉴스/실적/시장 점수 검증은 수행하지 않았다. 보고서 목표·추천 점수·기간은 null이다.", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fresh synthetic cost-screen evaluation; no real price input or API calls")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports/private/trade_evaluation")
    args = parser.parse_args(argv)
    if not args.output_dir.resolve().is_relative_to(ROOT / "reports/private"):
        parser.error("Private research output required")
    evaluation, audit, generation = protocol()
    cutoff, screen_settings = now(), trade_screen.policy()
    if as_utc(cutoff) < as_utc(evaluation["frozen_at"]):
        raise ValueError("Run precedes evaluation registration")
    destination = args.output_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    names = ("trade_evaluation.py", "trade_screen.py", "learned_peak.py", "learned_research.py", "peak_model.py", "robustness.py", "sample_audit.py", "swing_models.py")
    registration = {"registered_at": cutoff, "screen_policy_sha256": evaluation["screen_policy_sha256"],
        "evaluation_policy_sha256": digest(ROOT / "config/trade_screen_evaluation.json"),
        "sample_audit_policy_sha256": evaluation["sample_audit_policy_sha256"],
        "code_sha256": {name: digest(ROOT / "research" / name) for name in names}}
    save_new(destination / "registration.json", {**registration, "evaluation": evaluation, "generation": generation, "screen_policy": screen_settings})
    for name in names:
        path = destination / "registered_sources" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / "research" / name).read_bytes())
    for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[key] = "1"
    print(json.dumps({"status": "registered", "output_dir": str(destination.resolve()), "fresh_seed_paths": 24, "datasets": 120}), flush=True)
    records = []
    with ProcessPoolExecutor(max_workers=evaluation["workers"]) as pool:
        futures = {pool.submit(evaluate_seed, seed, evaluation, audit, generation, screen_settings, cutoff, str(destination)): seed for seed in evaluation["fresh_seeds"]}
        for future in as_completed(futures):
            seed = futures[future]
            try:
                records.extend(future.result())
            except Exception as error:
                for scenario in generation["scenarios"]:
                    path = destination / f"{scenario['id']}_{seed}_progress.json"
                    records.append(json.loads(path.read_text(encoding="utf-8")) if path.exists() else
                        {"seed": seed, "scenario": scenario["id"], "status": "unavailable", "reason": type(error).__name__, "summary": None})
    records.sort(key=lambda row: (row["scenario"], row["seed"]))
    result = {"schema_version": "1.0.0", "status": "research_only_unvalidated", "as_of": cutoff, "is_illustrative": True,
        "data_label": workflow.LABEL, "evaluation": evaluation, "generation": generation, "screen_policy": screen_settings,
        "registration": registration, "records": records, "aggregate": aggregate(records, evaluation, generation),
        "real_market_performance_validated": False, "released_score": None, "released_price_plan": None,
        "released_expected_target_sessions": None, "validation_id": None, "external_llm_calls": 0, "llm_tokens": 0}
    validate(result)
    save_new(destination / "trade_evaluation.json", result)
    (destination / "trade_evaluation.md").write_text(markdown(result), encoding="utf-8")
    print(json.dumps({"status": result["status"], "output_dir": str(destination.resolve()),
        "completed_datasets": sum(row["status"] == "completed" for row in records), "released_score": None}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
