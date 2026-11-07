"""Preregistered, synthetic-only matched-noise and feature-ablation experiment."""

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
import math
from pathlib import Path
import random

import jsonschema

from . import learned_peak as learned, learned_research as workflow, peak_model as base
from .rights import as_utc
from .sec_report import ROOT
from .swing_models import Bar, XnysCalendar
from .validation import digest, now, save_new, stable_hash


def protocol():
    settings = json.loads((ROOT / "config/robustness_protocol.json").read_text(encoding="utf-8"))
    if (settings["release_enabled"] is not False or settings["real_price_input_enabled"] is not False
            or settings["base_learned_policy_sha256"] != digest(ROOT / "config/learned_peak_policy.json")
            or settings["external_llm_calls"] != 0 or settings["monthly_data_budget_usd"] != 0):
        raise ValueError("Synthetic protocol, source budget or frozen learned policy changed")
    if len(set(settings["seeds"])) != len(settings["seeds"]) or not settings["seeds"]:
        raise ValueError("Unique preregistered seeds required")
    return settings


def innovations(seed, count):
    rng = random.Random(seed)
    return [tuple(rng.gauss(0, 1) for _ in range(5)) for _ in range(count)]


def first_held_out_reference(count, settings):
    indices = list(range(59, count - 5, 5))
    first = int(len(indices) * settings["training_fraction"])
    second = first + int(len(indices) * settings["calibration_fraction"])
    return indices[second]


def generate(scenario, seed, settings, calendar=None):
    """All matched scenarios consume identical independent innovations."""
    count = settings["bars_per_dataset"]
    calendar = calendar or XnysCalendar("2020-01-01", "2027-01-31")
    sessions = calendar.sessions("2020-01-02", "2026-11-06")[:count]
    if len(sessions) != count:
        raise ValueError("Calendar does not cover the preregistered synthetic dataset")
    stress_index = settings.get("fixed_stress_start_index", first_held_out_reference(count, learned.policy()) + 1)
    draws, previous, bars = innovations(seed, count), Decimal("100"), []
    for index, (session, noise) in enumerate(zip(sessions, draws)):
        signal = scenario["signal_strength"] * (.006 * math.sin(index / 18) + .003 * math.sin(index / 43))
        sigma = settings["matched_daily_noise_sigma"]
        if index >= stress_index:
            if scenario["stress"] == "reverse_signal_at_first_held_out_reference_plus_one":
                signal = -signal
            elif scenario["stress"] == "triple_noise_at_first_held_out_reference_plus_one":
                sigma *= 3
        opening = previous * Decimal(str(math.exp(sigma * noise[0] / 3)))
        closing = opening * Decimal(str(math.exp(signal + sigma * noise[1])))
        spread = Decimal(str(.003 + abs(sigma * noise[2] / 2)))
        high = max(opening, closing) * (1 + spread)
        low = min(opening, closing) * (1 - spread)
        volume = Decimal(str(100000 * math.exp(30 * signal + .05 * noise[3])))
        bars.append(Bar(session, opening, high, low, closing, volume, calendar.close(session)))
        previous = closing
    return bars, calendar, stress_index


def return_bounds(outcome, cost):
    status = outcome["outcome"]
    if status in {"stand_aside_no_long_plan", "untradeable_open_gap"}:
        return Decimal(0), Decimal(0)
    if status == "ambiguous_same_bar":
        return (base.net_return(Decimal(outcome["entry"]), Decimal(outcome["stop"]), cost),
                base.net_return(Decimal(outcome["entry"]), Decimal(outcome["target"]), cost))
    if outcome.get("net_return_pct") is None:
        raise ValueError("Cannot interpret unresolved execution as a neutral return")
    value = Decimal(outcome["net_return_pct"])
    return value, value


def execution_summary(rows, prediction_rows, cost):
    bounds = [return_bounds(row, cost) for row in rows]
    outcomes = Counter(row["outcome"] for row in rows)
    tradeable = sum(outcomes.get(key, 0) for key in ("target", "stop", "timeout", "ambiguous_same_bar"))
    timing_errors, timing_contains, missing_timing = [], [], 0
    for row, prediction in zip(rows, prediction_rows):
        if row["outcome"] != "target":
            continue
        timing = (prediction.get("execution") or [{}])[0].get("experimental_first_target_hit_sessions") if prediction else None
        if timing is None:
            missing_timing += 1
        else:
            timing_errors.append(abs(row["hit_session"] - sum(timing) / 2))
            timing_contains.append(timing[0] <= row["hit_session"] <= timing[1])
    return {"cases": len(rows), "outcome_counts": dict(outcomes),
        "mean_net_return_pct_bounds": ([str(sum((item[i] for item in bounds), Decimal(0)) / len(bounds)) for i in (0, 1)] if bounds else None),
        "tradeable_cases": tradeable,
        "observed_target_before_stop_frequency_bounds": ([outcomes.get("target", 0) / tradeable,
            (outcomes.get("target", 0) + outcomes.get("ambiguous_same_bar", 0)) / tradeable] if tradeable else None),
        "target_timing_evaluable_hits": len(timing_errors), "target_hits_missing_timing_estimate": missing_timing,
        "target_hit_midpoint_mae_sessions": sum(timing_errors) / len(timing_errors) if timing_errors else None,
        "target_hit_interval_contains_frequency": sum(timing_contains) / len(timing_contains) if timing_contains else None}


def paired_summary(variants):
    full, ablated = variants["with_volume"], variants["without_volume"]
    by_index = {item["reference_index"]: item for item in ablated["cases"]}
    cases = []
    names = ("with_volume", "without_volume", "fixed_ewma", "unconditional_training")
    for first in full["cases"]:
        second = by_index[first["reference_index"]]
        if any(first[key] != second[key] for key in ("reference_session", "actual_peak_atr", "actual_peak_day")):
            raise ValueError("Ablation comparison requires the identical held-out price path")
        metrics = {"with_volume": first["metrics"].get("learned"), "without_volume": second["metrics"].get("learned"),
            "fixed_ewma": first["metrics"].get("fixed_ewma"), "unconditional_training": first["metrics"].get("unconditional_training")}
        cases.append({"full": first, "ablated": second, "metrics": metrics})
    common = [row for row in cases if all(row["metrics"][name] is not None for name in names)]
    result = {"held_out_cases": len(cases), "common_forecastable_cases": len(common),
        "excluded_from_common_cohort": len(cases) - len(common), "models": {}, "execution": []}
    for name in names:
        available = sum(row["metrics"][name] is not None for row in cases)
        result["models"][name] = {"available_cases": available, "withheld_cases": len(cases) - available,
            "common_cohort_means": ({key: sum(float(row["metrics"][name][key]) for row in common) / len(common)
                for key in common[0]["metrics"][name]} if common else None)}
    result["paired_peak_mae_delta_against_fixed"] = {
        name: (sum(row["metrics"][name]["peak_median_absolute_error_atr"] - row["metrics"]["fixed_ewma"]["peak_median_absolute_error_atr"] for row in common) / len(common) if common else None)
        for name in ("with_volume", "without_volume")}
    result["paired_volume_inclusion_mae_delta"] = (sum(row["metrics"]["with_volume"]["peak_median_absolute_error_atr"] - row["metrics"]["without_volume"]["peak_median_absolute_error_atr"] for row in common) / len(common) if common else None)
    for cost in base.policy()["cost_bps_per_side"]:
        stats = {}
        for name in ("with_volume", "without_volume", "fixed_ewma", "atr_baseline", "five_session_hold"):
            source = "ablated" if name == "without_volume" else "full"
            key = "learned" if name in {"with_volume", "without_volume"} else name
            outcomes = [row[source]["execution"][cost][key] for row in common]
            predictions = [row[source]["predictions"].get(key) for row in common]
            stats[name] = execution_summary(outcomes, predictions, cost)
        result["execution"].append({"cost_bps_per_side": cost, "cohort": "same_common_forecastable_with_cash_and_ambiguity_bounds", "models": stats})
    return result


def aggregate(runs, settings):
    result = []
    for scenario in settings["scenarios"]:
        selected = [item for item in runs if item["scenario"] == scenario["id"]]
        successful = [item for item in selected if item["status"] == "completed" and item["paired_summary"]["common_forecastable_cases"]]
        stats = {"scenario": scenario["id"], "planned_seeds": len(settings["seeds"]), "completed_seeds": sum(item["status"] == "completed" for item in selected),
            "seeds_with_common_cases": len(successful), "failed_runs": [item["seed"] for item in selected if item["status"] != "completed"],
            "held_out_cases": sum((item.get("paired_summary") or {}).get("held_out_cases", 0) for item in selected),
            "common_forecastable_cases": sum((item.get("paired_summary") or {}).get("common_forecastable_cases", 0) for item in selected), "models": {}}
        for name in ("with_volume", "without_volume", "fixed_ewma", "unconditional_training"):
            means = [item["paired_summary"]["models"][name]["common_cohort_means"] for item in successful]
            entry = {"mean_of_seed_metrics": ({key: sum(row[key] for row in means) / len(means) for key in means[0]} if means else None)}
            if name in {"with_volume", "without_volume"}:
                deltas = [item["paired_summary"]["paired_peak_mae_delta_against_fixed"][name] for item in successful]
                entry.update(paired_mae_delta_mean=sum(deltas) / len(deltas) if deltas else None,
                    paired_mae_delta_seed_range=[min(deltas), max(deltas)] if deltas else None,
                    seeds_with_lower_mae_than_fixed=sum(value < 0 for value in deltas))
            stats["models"][name] = entry
        stats["seed_volume_effect_mae_deltas"] = [item["paired_summary"]["paired_volume_inclusion_mae_delta"] for item in successful]
        result.append(stats)
    return result


def validate(value):
    schema = json.loads((ROOT / "schemas/robustness.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(value)
    pairs = [(item["scenario"], item["seed"]) for item in value["runs"]]
    planned = [(item["id"], seed) for item in value["protocol"]["scenarios"] for seed in value["protocol"]["seeds"]]
    if pairs != planned:
        raise ValueError("Missing, duplicated or reordered preregistered experiment runs")


def markdown(result):
    lines = ["# 같은 잡음·거래량 제외·다중 시드 비교", "", workflow.LABEL, "",
        f"- 상태: {result['status']}", f"- 프로토콜: {result['protocol']['id']}",
        "- 실제 BE 학습/실증 성능 검증: 미수행", "- LLM 호출·토큰·유료 데이터 사용: 0", "",
        "MAE는 ATR 단위이며 낮을수록 좋다. 모든 모델은 각 시드 내 동일 예측 가능 사례에서 비교한다. 표는 시드별 평균의 평균이다.", "",
        "| 시장 조건 | 공통 사례/전체 | 고정 EWMA | 거래량 포함 ML | 거래량 제외 ML |", "|---|---:|---:|---:|---:|"]
    for row in result["aggregate"]:
        def number(name):
            metrics = row["models"][name]["mean_of_seed_metrics"]
            return f"{metrics['peak_median_absolute_error_atr']:.4f}" if metrics else "보류"
        lines.append(f"| {row['scenario']} | {row['common_forecastable_cases']}/{row['held_out_cases']} | {number('fixed_ewma')} | {number('with_volume')} | {number('without_volume')} |")
    lines += ["", "시드 범위·고점일/목표 도달기간·가격 구간 포함 빈도·보류 이유·10/25bp 비용 후 손익 경계는 JSON과 개별 실험 파일을 참조한다.",
        "급변 시 공통 사례 감소를 성능 개선으로 해석하지 않는다. 같은 일봉 목표/무효화 동시 도달은 손절~목표 체결 손익 경계로 보존하며 선후를 추정하지 않는다.",
        "3개 시드·합성 생성기 한 종류의 민감도 검사이며 유의성·실제 수익성·승률의 증거가 아니다. 기존 엔진 교체와 보고서 점수 활성화는 보류한다.", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Frozen synthetic robustness suite; cannot load real price files")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports/private/robustness")
    args = parser.parse_args(argv)
    if not args.output_dir.resolve().is_relative_to(ROOT / "reports/private"):
        parser.error("Research outputs must remain under reports/private")
    settings, cutoff = protocol(), now()
    if as_utc(cutoff) < as_utc(settings["frozen_at"]):
        raise ValueError("Run precedes protocol freeze")
    destination = args.output_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    registration = {"registered_at": cutoff, "protocol_sha256": digest(ROOT / "config/robustness_protocol.json"),
        "learned_policy_sha256": settings["base_learned_policy_sha256"],
        "base_policy_sha256": digest(ROOT / "config/peak_model_policy.json"),
        "code_sha256": {name: digest(ROOT / "research" / name) for name in ("robustness.py", "learned_peak.py", "learned_research.py", "peak_model.py", "peak_simulation.py", "swing_models.py")}}
    save_new(destination / "registration.json", {**registration, "protocol": settings})
    for name, expected in registration["code_sha256"].items():
        source = ROOT / "research" / name
        if digest(source) != expected:
            raise ValueError("Registered source changed before archiving")
        archive = destination / "registered_sources" / name
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(source.read_bytes())
    print(json.dumps({"status": "registered", "output_dir": str(destination.resolve()), "datasets": len(settings["scenarios"]) * len(settings["seeds"])}), flush=True)
    result = {"schema_version": "1.0.0", "status": "research_only_unvalidated", "as_of": cutoff,
        "is_illustrative": True, "data_label": workflow.LABEL, "protocol": settings, "registration": registration,
        "real_market_performance_validated": False, "released_price_plan": None, "released_score": None,
        "released_expected_target_sessions": None, "validation_id": None, "external_llm_calls": 0, "llm_tokens": 0,
        "runs": [], "aggregate": []}
    calendar = XnysCalendar("2020-01-01", "2027-01-31")
    for scenario in settings["scenarios"]:
        for seed in settings["seeds"]:
            record = {"scenario": scenario["id"], "seed": seed, "status": "unavailable", "reason": None, "paired_summary": None, "artifacts": {}}
            try:
                bars, _, stress_index = generate(scenario, seed, settings, calendar)
                fingerprint = stable_hash([repr(row) for row in bars])
                record.update(input_sha256=fingerprint, stress_start_index=stress_index,
                    first_held_out_reference_index=stress_index - 1,
                    source_ids=["local-matched-synthetic-generator-v1"], is_illustrative=True)
                variants, cache = {}, None
                for variant in settings["variants"]:
                    fit_settings = deepcopy(learned.policy())
                    fit_settings.update(id=settings["id"] + "-" + variant["id"], active_feature_indices=variant["active_feature_indices"])
                    experiment = workflow.experiment(bars, calendar, cutoff, fit_settings, reference_cache=cache)
                    artifact = {"schema_version": "1.0.0", "status": "research_only_unvalidated", "is_illustrative": True,
                        "data_label": workflow.LABEL, "scenario": scenario["id"], "seed": seed, "input_sha256": fingerprint,
                        "source_ids": record["source_ids"], "variant": variant, "fit_settings": fit_settings,
                        "registration": registration, "experiment": experiment, "released_score": None, "validation_id": None}
                    path = destination / f"{scenario['id']}_{seed}_{variant['id']}.json"
                    save_new(path, artifact)
                    variants[variant["id"]] = experiment
                    record["artifacts"][variant["id"]] = {"path": path.name, "sha256": digest(path)}
                    if cache is None:
                        cache = {"input_sha256": fingerprint, "cases": {row["reference_index"]: row for row in experiment["cases"]}}
                record.update(status="completed", paired_summary=paired_summary(variants))
            except ValueError as error:
                record["reason"] = str(error)
            result["runs"].append(record)
            save_new(destination / f"progress_{len(result['runs']):02d}.json", record)
            print(json.dumps({"scenario": record["scenario"], "seed": seed, "status": record["status"],
                "common_cases": (record.get("paired_summary") or {}).get("common_forecastable_cases")}), flush=True)
    result["aggregate"] = aggregate(result["runs"], settings)
    validate(result)
    save_new(destination / "robustness.json", result)
    (destination / "robustness.md").write_text(markdown(result), encoding="utf-8")
    print(json.dumps({"status": result["status"], "output_dir": str(destination.resolve()),
        "completed_datasets": sum(row["status"] == "completed" for row in result["runs"]), "released_score": None}), flush=True)
    return 0 if all(row["status"] == "completed" for row in result["runs"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
