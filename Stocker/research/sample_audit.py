"""Independent-seed and held-out-length audit. Synthetic data only.

The decision requires intervals inside preregistered practical margins; a
non-significant difference alone never authorizes the next research process.
"""

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import gzip
import json
import math
import os
from pathlib import Path
import statistics

import jsonschema

from . import learned_peak as learned, learned_research as workflow, robustness
from .rights import as_utc
from .sec_report import ROOT
from .swing_models import XnysCalendar
from .validation import digest, now, save_new, stable_hash


def protocol():
    data = json.loads((ROOT / "config/sample_audit_protocol.json").read_text(encoding="utf-8"))
    base = robustness.protocol()
    if (data["base_robustness_protocol_sha256"] != digest(ROOT / "config/robustness_protocol.json")
            or data["release_enabled"] is not False or data["real_price_input_enabled"] is not False
            or data["is_illustrative"] is not True or data["monthly_data_budget_usd"] != 0):
        raise ValueError("Sample audit must pin the frozen synthetic protocol and zero budget")
    seeds = data["seeds"]
    if len(seeds) != 24 or len(set(seeds)) != 24 or set(seeds) & set(base["seeds"]):
        raise ValueError("Require twenty-four new independent preregistered seed paths")
    if data["seed_batches"] != [12, 12] or data["confidence"]["decision_intervals"] != len(base["scenarios"]) * 4:
        raise ValueError("The fixed cluster interval design changed")
    return data, base


def mean_interval(values, critical):
    values = [float(value) for value in values]
    if len(values) < 2 or not all(math.isfinite(value) for value in values):
        raise ValueError("Need at least two finite independent-cluster measurements")
    mean = statistics.mean(values)
    error = statistics.stdev(values) / math.sqrt(len(values))
    return {"mean": mean, "standard_error": error, "interval": [mean - critical * error, mean + critical * error], "clusters": len(values)}


def batch_difference(first, second, critical):
    if len(first) != 12 or len(second) != 12:
        raise ValueError("Two fixed independent batches of twelve seeds required")
    if not all(math.isfinite(float(value)) for value in first + second):
        raise ValueError("Nonfinite cluster metric")
    difference = statistics.mean(second) - statistics.mean(first)
    error = math.sqrt(statistics.variance(first) / len(first) + statistics.variance(second) / len(second))
    return {"mean": difference, "standard_error": error,
        "interval": [difference - critical * error, difference + critical * error], "clusters": [len(first), len(second)]}


def equivalent(estimate, margin):
    if not math.isfinite(margin) or margin <= 0:
        raise ValueError("Finite positive practical margin required")
    lower, upper = estimate["interval"]
    return -margin <= lower <= upper <= margin


def endpoints(summary, settings):
    scenario = next(row for row in summary["execution"] if row["cost_bps_per_side"] == settings["primary_cost_bps_per_side"])
    bounds = scenario["models"][settings["primary_variant"]]["mean_net_return_pct_bounds"]
    return {"paired_peak_mae_delta_against_fixed": summary["paired_peak_mae_delta_against_fixed"][settings["primary_variant"]],
        "mean_net_return_pct_lower_bound": float(bounds[0]) if bounds else None}


def compact_experiment(experiment):
    # Preserve model/ML forecasts, outcomes and all metrics; baseline forecasts
    # can be reproduced from the archived engine and their saved fingerprints.
    result = {key: value for key, value in experiment.items() if key != "cases"}
    result["cases"] = []
    for original in experiment["cases"]:
        row = {key: value for key, value in original.items() if key != "predictions"}
        row["predictions"] = {key: value for key, value in original["predictions"].items() if key == "learned"}
        row["baseline_prediction_sha256"] = {key: stable_hash(value) for key, value in original["predictions"].items() if key != "learned"}
        result["cases"].append(row)
    return result


def evaluate_seed(seed, settings, base_settings, cutoff, destination):
    destination = Path(destination)
    calendar = XnysCalendar("2020-01-01", "2027-01-31")
    records = []
    generation = deepcopy(base_settings)
    generation.update(bars_per_dataset=settings["bars_per_dataset"], fixed_stress_start_index=settings["fixed_stress_start_index"])
    for scenario in base_settings["scenarios"]:
        record = {"scenario": scenario["id"], "seed": seed, "status": "unavailable", "reason": None,
            "is_illustrative": True, "source_ids": ["local-matched-synthetic-generator-v1"],
            "short_endpoints": None, "long_endpoints": None, "short_summary": None, "long_summary": None}
        try:
            bars, _, stress = robustness.generate(scenario, seed, generation, calendar)
            fingerprint = stable_hash([repr(row) for row in bars])
            variants, cache = {}, None
            for variant in base_settings["variants"]:
                fit_settings = deepcopy(learned.policy())
                fit_settings.update(id=settings["id"] + "-" + variant["id"],
                    active_feature_indices=variant["active_feature_indices"],
                    fixed_partition_case_boundaries=settings["fixed_partition_case_boundaries"])
                experiment = workflow.experiment(bars, calendar, cutoff, fit_settings, reference_cache=cache)
                if experiment["counts"]["training"] != settings["fixed_training_cases"] or experiment["counts"]["calibration"] != settings["fixed_calibration_cases"] or experiment["counts"]["held_out"] != settings["long_held_out_cases"]:
                    raise ValueError("Training/calibration changed while extending held-out samples")
                if experiment["cases"][0]["reference_index"] + 1 != stress:
                    raise ValueError("Stress must begin after the unchanged first held-out reference")
                variants[variant["id"]] = experiment
                if cache is None:
                    cache = {"input_sha256": fingerprint, "cases": {row["reference_index"]: row for row in experiment["cases"]}}
            long_summary = robustness.paired_summary(variants)
            short = {name: {"cases": item["cases"][:settings["short_held_out_cases"]]} for name, item in variants.items()}
            short_summary = robustness.paired_summary(short)
            artifact = {"schema_version": "1.0.0", "status": "research_only_unvalidated", "is_illustrative": True,
                "data_label": workflow.LABEL, "source_ids": record["source_ids"], "seed": seed, "scenario": scenario,
                "input_sha256": fingerprint, "fixed_partition_case_boundaries": settings["fixed_partition_case_boundaries"],
                "variants": {key: compact_experiment(value) for key, value in variants.items()},
                "released_score": None, "validation_id": None}
            path = destination / f"{scenario['id']}_{seed}.json.gz"
            with gzip.open(path, "xt", encoding="utf-8") as output:
                json.dump(artifact, output, ensure_ascii=False, separators=(",", ":"))
            record.update(status="completed", input_sha256=fingerprint,
                artifact={"path": path.name, "sha256": digest(path)},
                short_endpoints=endpoints(short_summary, settings), long_endpoints=endpoints(long_summary, settings),
                short_summary=short_summary, long_summary=long_summary)
        except (ValueError, OSError) as error:
            record["reason"] = str(error)
        save_new(destination / f"{scenario['id']}_{seed}_progress.json", record)
        print(json.dumps({"seed": seed, "scenario": scenario["id"], "status": record["status"],
            "long_common_cases": (record["long_summary"] or {}).get("common_forecastable_cases")}), flush=True)
        records.append(record)
    return records


def assess(records, settings, base_settings):
    decisions, coverage_ok, complete = [], True, True
    scenarios = []
    by_pair = {(row["scenario"], row["seed"]): row for row in records}
    if len(by_pair) != len(records):
        raise ValueError("Duplicate cluster records")
    for scenario in base_settings["scenarios"]:
        rows = [by_pair.get((scenario["id"], seed)) for seed in settings["seeds"]]
        available = [row for row in rows if row and row["status"] == "completed"]
        measurements = {"scenario": scenario["id"], "completed_seed_paths": len(available), "estimates": {}}
        valid = len(available) == len(settings["seeds"]) and all(
            row[size + "_endpoints"][metric] is not None
            for row in available for size in ("short", "long") for metric in settings["endpoints"])
        complete &= valid
        minimum_coverage = min((row["long_summary"]["common_forecastable_cases"] / settings["long_held_out_cases"] for row in available), default=0)
        coverage_ok &= minimum_coverage >= settings["minimum_common_cohort_fraction"]
        measurements["minimum_seed_common_cohort_fraction"] = minimum_coverage
        for metric in settings["endpoints"]:
            if not valid:
                decisions.extend({"scenario": scenario["id"], "metric": metric, "comparison": comparison,
                    "estimate": None, "equivalent_within_margin": False, "reason": "Missing preregistered independent clusters"}
                    for comparison in settings["stability_comparisons"])
                continue
            short = [row["short_endpoints"][metric] for row in available]
            long = [row["long_endpoints"][metric] for row in available]
            critical = settings["confidence"]["mean_and_paired_df23_critical_upper_rounded"]
            measurements["estimates"][metric] = {"short": mean_interval(short, critical), "long": mean_interval(long, critical)}
            differences = {"within_seed_long_minus_short": mean_interval([b - a for a, b in zip(short, long)], critical),
                "second_seed_batch_minus_first": batch_difference(long[:12], long[12:], settings["confidence"]["independent_batches_conservative_df11_critical_upper_rounded"])}
            margin = settings["practical_tolerances"][metric]
            decisions.extend({"scenario": scenario["id"], "metric": metric, "comparison": name,
                "estimate": value, "practical_margin": margin, "equivalent_within_margin": equivalent(value, margin)} for name, value in differences.items())
        scenarios.append(measurements)
    passed = complete and coverage_ok and len(decisions) == settings["confidence"]["decision_intervals"] and all(row["equivalent_within_margin"] for row in decisions)
    return {"status": "stable_within_research_margins" if passed else "sample_stability_not_established",
        "advance_next_research_process": bool(passed), "all_clusters_complete": bool(complete),
        "coverage_gate_passed": bool(coverage_ok), "stability_intervals": len(decisions),
        "equivalent_intervals": sum(row["equivalent_within_margin"] for row in decisions),
        "scenarios": scenarios, "decisions": decisions,
        "real_market_validation": False, "note": "Non-significance is not equivalence; t intervals have finite-variance/approximate-normal cluster assumptions"}


def validate(value):
    schema = json.loads((ROOT / "schemas/sample_audit.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(value)
    expected = {(scenario["id"], seed) for scenario in value["base_protocol"]["scenarios"] for seed in value["protocol"]["seeds"]}
    actual = [(row["scenario"], row["seed"]) for row in value["records"]]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError("Audit must retain every preregistered cluster record")
    recomputed = assess(value["records"], value["protocol"], value["base_protocol"])
    if value["assessment"] != recomputed:
        raise ValueError("Advance decision differs from the frozen sample-stability rule")


def markdown(value):
    assessment = value["assessment"]
    lines = ["# 표본 규모 재검토", "", workflow.LABEL, "",
        "기존 3개 독립 시드를 새 시드 24개로 확장하고, 계수 학습 123개·오차 추정 40개를 고정한 채 평가 사례를 시드당 43→86개로 늘렸다.",
        "조건/변형 간 공통 난수와 경로 내 시계열 의존을 독립 표본으로 세지 않는다. 동일 시드의 전체 평균을 불확실성 계산 단위로 사용한다.", "",
        f"- 판정: {assessment['status']}", f"- 다음 연구 단계 진행 조건 충족: {assessment['advance_next_research_process']}",
        f"- 사전 정의한 허용 범위 안의 안정성 구간: {assessment['equivalent_intervals']}/{assessment['stability_intervals']}",
        f"- 공통 사례 비율 조건: {assessment['coverage_gate_passed']}", "",
        "| 조건 | 확장 평가 ML−EWMA 고점 오차(ATR) | 비용 25bp 후 평균 손익 하한(%) |", "|---|---|---|"]
    for scenario in assessment["scenarios"]:
        def interval(metric):
            estimate = scenario["estimates"].get(metric, {}).get("long")
            return f"{estimate['mean']:.4f} [{estimate['interval'][0]:.4f}, {estimate['interval'][1]:.4f}]" if estimate else "불충분"
        lines.append(f"| {scenario['scenario']} | {interval('paired_peak_mae_delta_against_fixed')} | {interval('mean_net_return_pct_lower_bound')} |")
    lines += ["", "대괄호는 독립 시드 단위 명목 99.8% t 구간이다. 수익률은 기준일별 평균이며 누적 수익률이 아니다.",
        "추가 평가 길이 효과와 독립 12시드 두 묶음 차이를 0.05 ATR / 0.10%p의 사전 연구 허용오차로 검사했다. 유의하지 않다는 이유로 같은 결과라고 판정하지 않는다.",
        "분포/유한분산 가정 아래의 명목 구간이며 시장 예측 확률이나 분포 무관 보장이 아니다. 같은 생성기를 반복해도 실제 BE 검증을 대체하지 못한다.", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Synthetic-only sample-size audit with fixed independent seed clusters")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports/private/sample_audit")
    args = parser.parse_args(argv)
    if not args.output_dir.resolve().is_relative_to(ROOT / "reports/private"):
        parser.error("Private research output required")
    settings, base_settings = protocol()
    cutoff = now()
    if as_utc(cutoff) < as_utc(settings["frozen_at"]):
        raise ValueError("Run precedes audit freeze")
    destination = args.output_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    names = ("sample_audit.py", "robustness.py", "learned_peak.py", "learned_research.py", "peak_model.py", "peak_simulation.py", "swing_models.py")
    registration = {"registered_at": cutoff, "audit_policy_sha256": digest(ROOT / "config/sample_audit_protocol.json"),
        "base_protocol_sha256": digest(ROOT / "config/robustness_protocol.json"),
        "code_sha256": {name: digest(ROOT / "research" / name) for name in names}}
    save_new(destination / "registration.json", {**registration, "protocol": settings, "base_protocol": base_settings})
    for name, expected in registration["code_sha256"].items():
        source = ROOT / "research" / name
        if digest(source) != expected:
            raise ValueError("Source changed after registration")
        target = destination / "registered_sources" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    # Bound BLAS threads before Windows spawn; independent Python workers do
    # not share fitted models, random generators or external API credentials.
    for key in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[key] = "1"
    print(json.dumps({"status": "registered", "output_dir": str(destination.resolve()), "independent_seeds": len(settings["seeds"]), "datasets": len(settings["seeds"]) * len(base_settings["scenarios"]), "workers": settings["workers"]}), flush=True)
    records = []
    with ProcessPoolExecutor(max_workers=settings["workers"]) as pool:
        futures = {pool.submit(evaluate_seed, seed, settings, base_settings, cutoff, str(destination)): seed for seed in settings["seeds"]}
        for future in as_completed(futures):
            seed = futures[future]
            try:
                records.extend(future.result())
            except Exception as error:
                # Keep complete child progress even after a worker/process failure.
                for scenario in base_settings["scenarios"]:
                    path = destination / f"{scenario['id']}_{seed}_progress.json"
                    if path.exists():
                        records.append(json.loads(path.read_text(encoding="utf-8")))
                    else:
                        records.append({"scenario": scenario["id"], "seed": seed, "status": "unavailable", "reason": type(error).__name__,
                            "short_endpoints": None, "long_endpoints": None, "short_summary": None, "long_summary": None})
    records.sort(key=lambda row: (row["scenario"], row["seed"]))
    result = {"schema_version": "1.0.0", "status": "research_only_unvalidated", "as_of": cutoff,
        "is_illustrative": True, "data_label": workflow.LABEL, "protocol": settings, "base_protocol": base_settings,
        "registration": registration, "records": records, "assessment": assess(records, settings, base_settings),
        "real_market_performance_validated": False, "released_score": None, "released_price_plan": None,
        "released_expected_target_sessions": None, "validation_id": None, "external_llm_calls": 0, "llm_tokens": 0}
    validate(result)
    save_new(destination / "sample_audit.json", result)
    (destination / "sample_audit.md").write_text(markdown(result), encoding="utf-8")
    print(json.dumps({"status": result["assessment"]["status"], "advance_next_research_process": result["assessment"]["advance_next_research_process"], "completed_datasets": sum(row["status"] == "completed" for row in records), "output_dir": str(destination.resolve())}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
