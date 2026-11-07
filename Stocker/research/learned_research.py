"""Supervised price-only benchmark and a fail-closed real-data entry point."""

import argparse
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import json
import math
from pathlib import Path
import random
import re

import jsonschema

from .advance import verify_saved_raw
from . import learned_peak as learned, peak_model as base, peak_simulation
from .peak_research import validate_candidate
from .rights import RightsError, as_utc, price_uses, require_entitlement
from .sec_report import ROOT
from .swing_models import Bar, XnysCalendar, _reference_baseline, load_diagnostic_snapshot, replay_five_sessions
from .validation import digest, now, save_new, stable_hash

LABEL = "ILLUSTRATIVE — NOT LIVE MARKET DATA"


def latest_training_snapshot():
    """Select by saved capture directory; do not parse prices to select inputs."""
    records = sorted((path for path in (ROOT / "reports/private/market_data").glob("*/price_entitlement.json")
                      if re.fullmatch(r"\d{8}T\d{6}\d*Z", path.parent.name)),
                     key=lambda path: path.parent.name, reverse=True)
    for record in records:
        prices = record.with_name("prices.json")
        if prices.is_file():
            return prices, record
    raise ValueError("No saved price snapshot with an entitlement record")


def validate(value):
    schema = json.loads((ROOT / "schemas/learned_research.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(value)
    for experiment in value["experiments"]:
        if experiment["is_illustrative"] != value["is_illustrative"]:
            raise ValueError("Real and illustrative experiment classifications must match")
        for case in experiment["cases"]:
            for prediction in case.get("predictions", {}).values():
                validate_candidate(prediction)


def synthetic_bars(kind, count=1100, seed=20261107):
    """Known smooth-drift signal or unpredictable independent returns; never BE."""
    if kind not in {"signal", "null", "shuffled"}:
        raise ValueError("Unknown synthetic process")
    calendar = XnysCalendar("2020-01-01", "2027-01-31")
    sessions = calendar.sessions("2020-01-02", "2026-11-06")[:count]
    rng, price, bars = random.Random(seed), Decimal("100"), []
    for index, session in enumerate(sessions):
        signal = .006 * math.sin(index / 18) + .003 * math.sin(index / 43) if kind != "null" else 0
        noise_scale = .002 if kind != "null" else .012
        opening = price * Decimal(str(math.exp(rng.gauss(0, noise_scale / 3))))
        closing = opening * Decimal(str(math.exp(signal + rng.gauss(0, noise_scale))))
        spread = Decimal(str(.003 + abs(rng.gauss(0, noise_scale / 2))))
        high, low = max(opening, closing) * (1 + spread), min(opening, closing) * (1 - spread)
        volume = Decimal(str(100000 * math.exp(30 * signal + rng.gauss(0, .05))))
        bars.append(Bar(session, opening, high, low, closing, volume, calendar.close(session)))
        price = closing
    return bars, calendar


def constant_forecast(training, bars, index, calendar, as_of):
    reference, atr = bars[index].close, base.atr14(bars[:index + 1])
    if any(reference + atr * value <= 0 for row in training for path in row.normalized_path for value in path):
        raise ValueError("Unconditional baseline has nonpositive rescaled price")
    return base.assemble(bars, index, calendar, as_of, training, training,
        [1 / len(training)] * len(training), len(training), 0,
        base.features(bars[:index + 1]), base.policy())


def summarize(cases, settings):
    names = ("learned", "fixed_ewma", "unconditional_training")
    common = [row for row in cases if all(name in row["metrics"] for name in names)]
    summary = {"held_out_cases": len(cases), "common_forecastable_cases": len(common),
        "excluded_from_common_comparison": len(cases) - len(common), "models": {}, "execution": []}
    for name in names:
        available = sum(name in row["metrics"] for row in cases)
        summary["models"][name] = {"available_cases": available, "withheld_cases": len(cases) - available,
            "common_cohort_means": ({key: sum(float(row["metrics"][name][key]) for row in common) / len(common)
                for key in common[0]["metrics"][name]} if common else None)}
    for cost in base.policy()["cost_bps_per_side"]:
        rows = [row["execution"][cost] for row in common]
        # Ambiguous paths remain counted, and are excluded jointly, not per model.
        comparable = [row for row in rows if all(row[key]["net_return_pct"] is not None
                      for key in ("learned", "fixed_ewma", "atr_baseline"))]
        summary["execution"].append({"cost_bps_per_side": cost, "common_resolved_cases": len(comparable),
            "ambiguous_or_untradeable_cases": len(rows) - len(comparable),
            "mean_net_return_pct": ({key: str(sum((Decimal(row[key]["net_return_pct"]) for row in comparable), Decimal(0)) / len(comparable))
                for key in ("learned", "fixed_ewma", "atr_baseline", "five_session_hold")} if comparable else None)})
    summary["minimum_sample_gate_passed"] = len(common) >= settings["minimum_held_out_cases_for_review"] and all(
        row["common_resolved_cases"] >= settings["minimum_held_out_cases_for_review"] for row in summary["execution"])
    return summary


def experiment(bars, calendar, as_of, settings, *, shuffle_training=False, reference_cache=None):
    rows = base.samples(bars)
    train, calibration, held, counts = learned.partitions(rows, settings)
    if shuffle_training:
        permuted = train.copy()
        random.Random(20261108).shuffle(permuted)
        train = [replace(row, normalized_path=other.normalized_path, peak_atr=other.peak_atr,
                         peak_day=other.peak_day, minimum_atr=other.minimum_atr)
                 for row, other in zip(train, permuted)]
    regressor = learned.fit(train, calibration, settings)
    if reference_cache is not None and reference_cache["input_sha256"] != stable_hash([repr(row) for row in bars]):
        raise ValueError("Baseline cache belongs to a different price path")
    cases = []
    for actual in held:
        index = actual.reference_index
        case = {"reference_session": bars[index].session, "reference_index": index,
            "label_end_index": actual.label_end_index, "label_period_ended_at": calendar.close(bars[actual.label_end_index].session),
            "label_available_at": max((row.available_at for row in bars[index + 1:actual.label_end_index + 1]), key=as_utc),
            "actual_peak_atr": actual.peak_atr, "actual_peak_day": actual.peak_day,
            "predictions": {}, "metrics": {}, "unavailable": {}, "execution": {}}
        calls = {"learned": lambda: learned.forecast(regressor, bars, calendar, as_of, query_index=index),
            "fixed_ewma": lambda: peak_simulation.forecast(bars, calendar, as_of, query_index=index),
            "unconditional_training": lambda: constant_forecast(train, bars, index, calendar, as_of)}
        for name, call in calls.items():
            try:
                if reference_cache is not None and name != "learned":
                    cached = reference_cache["cases"][index]
                    if name in cached["unavailable"]:
                        raise ValueError(cached["unavailable"][name])
                    prediction = cached["predictions"][name]
                else:
                    prediction = call()
                case["predictions"][name] = prediction
                case["metrics"][name] = learned.distribution_metrics(prediction, actual)
            except ValueError as error:
                case["unavailable"][name] = str(error)
        future = bars[index + 1:index + 6]
        atr_plan = _reference_baseline(bars[:index + 1], calendar, as_of)
        for cost in base.policy()["cost_bps_per_side"]:
            outcomes = {}
            for name in ("learned", "fixed_ewma"):
                if name not in case["predictions"]:
                    outcomes[name] = {"outcome": "unavailable", "net_return_pct": None}
                elif case["predictions"][name]["plan"] is None:
                    outcomes[name] = {"outcome": "stand_aside_no_long_plan", "net_return_pct": "0"}
                else:
                    outcomes[name] = replay_five_sessions(case["predictions"][name]["plan"], future, calendar, Decimal(cost))
            outcomes["atr_baseline"] = replay_five_sessions(atr_plan, future, calendar, Decimal(cost))
            outcomes["five_session_hold"] = {"outcome": "five_session_hold", "net_return_pct": str(base.net_return(future[0].open, future[-1].close, cost))}
            case["execution"][cost] = outcomes
        cases.append(case)
    return {"counts": counts, "split": {"latest_training_label_index": regressor.train_label_end,
        "first_calibration_reference_index": calibration[0].reference_index,
        "latest_calibration_label_index": regressor.calibration_label_end,
        "first_held_out_reference_index": held[0].reference_index},
        "summary": summarize(cases, settings), "cases": cases, "model": regressor.artifact()}


def markdown(result):
    lines = ["# 수치 예측 ML 비교 실험", "", f"- 상태: {result['status']}",
        f"- 기준 시각: {result['as_of']}", "- LLM 호출/토큰/데이터 구매 비용: 0", ""]
    if result["status"] != "research_only_unvalidated":
        lines += ["실제 가격은 학습 전에 차단됨: " + result["reason"], ""]
    for item in result.get("experiments", []):
        lines += [f"## {item['name']}", "", item["data_label"], "", f"- 구간별 사례: {item['counts']}",
            f"- 동일 모집단 예측 가능: {item['summary']['common_forecastable_cases']}",
            "- 지표는 낮을수록 좋음. 구간 포함률은 20~80 분위 범위의 경험적 포함 빈도이며 보장 확률이 아님.", "",
            "| 모델 | 고점 MAE(ATR) | 평균 pinball loss | joint Brier |", "|---|---:|---:|---:|"]
        for name, summary in item["summary"]["models"].items():
            values = summary["common_cohort_means"]
            if values:
                lines.append(f"| {name} | {values['peak_median_absolute_error_atr']:.4f} | {values['mean_pinball_loss_atr']:.4f} | {values['joint_brier']:.4f} |")
        lines += ["", "세부 도달일·비용 10/25bp·모호한 체결·보류 사례와 모델 계수는 JSON에 기록함.", ""]
    lines += ["합성 실험은 BE 실증 성능이 아니다. 실제 학습·전향 검증·뉴스 특징 연결이 완료되기 전 추천 점수/확률/목표 제공 값은 null이다.", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local supervised numerical model; no network or LLM calls")
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument("--prices", type=Path)
    parser.add_argument("--price-entitlement", type=Path)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports/private/learned_research")
    args = parser.parse_args(argv)
    if not args.output_dir.resolve().is_relative_to(ROOT / "reports/private"):
        parser.error("Learned research artifacts must remain under reports/private")
    if bool(args.prices) != bool(args.price_entitlement) or (args.synthetic and args.prices):
        parser.error("Use --synthetic alone, or paired prices and entitlement, or latest saved BE")
    as_of, settings = now(), learned.policy()
    if as_utc(as_of) < as_utc(settings["frozen_at"]):
        raise ValueError("Run precedes supervised policy freeze")
    destination = args.output_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    registration = {"registered_at": as_of, "policy_sha256": digest(ROOT / "config/learned_peak_policy.json"),
        "base_policy_sha256": settings["base_policy_sha256"],
        "code_sha256": {name: digest(ROOT / "research" / name) for name in ("learned_peak.py", "learned_research.py", "peak_model.py", "peak_simulation.py", "swing_models.py")}}
    result = {"schema_version": "1.0.0", "status": "research_only_unvalidated", "as_of": as_of,
        "methodology_id": settings["id"], "registration": registration, "is_illustrative": args.synthetic,
        "data_label": LABEL if args.synthetic else "REAL PERSONAL DATA — RESEARCH ONLY",
        "point_in_time_validated": False, "real_market_performance_validated": False,
        "released_price_plan": None, "released_score": None, "released_expected_target_sessions": None,
        "validation_id": None, "external_llm_calls": 0, "llm_tokens": 0,
        "monthly_data_budget_usd": 0, "experiments": []}
    try:
        if args.synthetic:
            save_new(destination / "registration.json", registration)
            for name in ("signal", "null", "shuffled"):
                bars, calendar = synthetic_bars(name)
                result["experiments"].append({"name": name, "data_label": LABEL,
                    "source_ids": ["local-synthetic-generator-20261107"], "is_illustrative": True,
                    "input_sha256": stable_hash([repr(row) for row in bars]),
                    **experiment(bars, calendar, as_of, settings, shuffle_training=name == "shuffled")})
        else:
            prices, rights = (args.prices, args.price_entitlement) if args.prices else latest_training_snapshot()
            record = json.loads(rights.read_text(encoding="utf-8"))
            # Do not even parse/hash prices until the real/synthetic use is cleared.
            require_entitlement(record, price_uses("local_research") + ["model_training"], as_of, synthetic=False)
            data, bars, calendar = load_diagnostic_snapshot(prices, record, as_of)
            if data["provider"] == "massive":
                verify_saved_raw(prices, record)
            if data["symbol"] != "BE" or data["is_illustrative"]:
                raise ValueError("Real supervised pilot requires non-illustrative BE inputs")
            registration.update(price_snapshot_sha256=digest(prices), entitlement_sha256=digest(rights), source_ids=data["source_ids"])
            save_new(destination / "registration.json", registration)
            result["experiments"].append({"name": "BE_current_vintage_diagnostic", "data_label": result["data_label"],
                "source_ids": data["source_ids"], "is_illustrative": False, **experiment(bars, calendar, as_of, settings)})
    except (ValueError, OSError, KeyError) as error:
        result.update(status="data_use_not_cleared" if isinstance(error, RightsError) else "input_or_fit_unavailable",
                      reason=str(error))
    validate(result)
    save_new(destination / "learned_research.json", result)
    (destination / "learned_research.md").write_text(markdown(result), encoding="utf-8")
    print(json.dumps({"status": result["status"], "is_illustrative": result["is_illustrative"],
        "output_dir": str(destination.resolve()), "experiments": len(result["experiments"]),
        "released_score": None, "llm_tokens": 0}))
    return 0 if result["status"] == "research_only_unvalidated" else 2


if __name__ == "__main__":
    raise SystemExit(main())
