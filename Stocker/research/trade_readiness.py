"""Offline personal BE fixed-statistical candidate and numerical screening.

No real-data ML fit, network, purchases, public release or external LLM input.
Price values remain private; stdout contains only statuses, reasons and paths.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import jsonschema

from .advance import verify_saved_raw
from .learned_research import latest_training_snapshot
from . import peak_simulation, trade_screen
from .rights import as_utc, price_uses, require_entitlement
from .sec_report import ROOT
from .swing_models import load_price_snapshot
from .validation import digest, now, save_new
from .report_language import trade_markdown


def validate(value):
    schema = json.loads((ROOT / "schemas/trade_screen.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(value)


def markdown(result):
    return trade_markdown(result)


def technical_markdown(result):
    screen = result.get("screen")
    lines = ["# BE 개인용 비용·위험 연구 준비 상태", "", f"- 상태: {result['status']}",
        f"- 기준 시각: {result['as_of']}", "- 분석 범위: 종가 기준 5거래일, 고정 통계 경로 + 비용 필터", ""]
    if screen:
        lines += [f"- 기준 세션: {screen['reference_session']}", f"- 수치 검토 상태: {screen['screen_decision']}",
            f"- 사유: {', '.join(screen['reason_codes']) or '수치 연구 조건 충족'}", "",
            "| 편도 비용(bp) | 기준종가→목표 비용 후 여력(%) | 비용 후 손익비 | 경로 추정 평균 수익 경계(%) |",
            "|---|---:|---:|---|"]
        for row in screen["metrics"]:
            lines.append(f"| {row['cost_bps_per_side']} | {row['net_reference_to_target_pct']} | {row['net_reward_risk']} | {row['estimated_path_ev_pct_bounds']} |")
    else:
        lines += ["- 입력/계산 보류: " + result.get("reason", "입력 부족")]
    lines += ["", "기준종가에서의 연구 필터이며 다음 시가의 진입 가능 여부를 보장하지 않는다. 경로 평균은 보정된 확률이나 통계적 신뢰 하한이 아니다.",
        "추천 보고서 목표·점수·기간은 null이다. 실제 ML 학습은 별도 허용 확인이 필요하며 이번 경로에서 수행하지 않았다. 검토한 최신 뉴스/시장 특징 및 실제 성능 검증이 완료돼야 전체 추천도를 검토할 수 있다.", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Private real-BE numerical readiness without network or ML fitting")
    parser.add_argument("--prices", type=Path)
    parser.add_argument("--price-entitlement", type=Path)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports/private/trade_readiness")
    args = parser.parse_args(argv)
    if not args.output_dir.resolve().is_relative_to(ROOT / "reports/private"):
        parser.error("Private output directory required")
    if bool(args.prices) != bool(args.price_entitlement):
        parser.error("Supply paired prices/entitlement or use the latest saved snapshot")
    cutoff, settings = now(), trade_screen.policy()
    if as_utc(cutoff) < as_utc(settings["frozen_at"]):
        raise ValueError("Readiness precedes screen policy freeze")
    destination = args.output_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    result = {"schema_version": "1.0.0", "status": "research_only_unvalidated", "as_of": cutoff,
        "is_illustrative": False, "symbol": "BE", "screen": None, "forecast": None,
        "released_score": None, "released_price_plan": None, "released_expected_target_sessions": None,
        "validation_id": None, "external_llm_calls": 0, "llm_tokens": 0, "real_ml_fit_performed": False,
        "readiness": {"full_recommendation": "not_ready", "missing_features": ["validated_model", "reviewed_current_news", "bound_sector_market_fundamental_features"]},
        "registration": {"registered_at": cutoff, "screen_policy_sha256": digest(ROOT / "config/trade_screen_policy.json"),
            "code_sha256": {name: digest(ROOT / "research" / name) for name in ("trade_readiness.py", "trade_screen.py", "peak_simulation.py", "peak_model.py", "swing_models.py", "learned_research.py", "advance.py", "rights.py", "fetch_prices.py", "validation.py")}}}
    try:
        prices, entitlement = (args.prices, args.price_entitlement) if args.prices else latest_training_snapshot()
        record = json.loads(entitlement.read_text(encoding="utf-8"))
        require_entitlement(record, price_uses("local_research"), cutoff, synthetic=False)
        if record["provider"] != "massive" or record["plan"] != "stocks_basic":
            raise ValueError("Real pilot requires the reviewed personal Massive Basic path")
        verify_saved_raw(prices, record)
        data, bars, calendar = load_price_snapshot(prices, record, cutoff)
        if data["symbol"] != "BE" or data["is_illustrative"]:
            raise ValueError("Real readiness requires the identified BE snapshot")
        result["registration"].update(price_snapshot_sha256=digest(prices), entitlement_sha256=digest(entitlement), source_ids=data["source_ids"])
        save_new(destination / "registration.json", result["registration"])
        prediction = peak_simulation.forecast(bars, calendar, cutoff)
        result.update(forecast=prediction, screen=trade_screen.assess(prediction, as_of=cutoff,
            source_ids=data["source_ids"], is_illustrative=False, mode="current_research"))
        result["readiness"].update(price_input="current", fixed_statistical_forecast="available",
            ml_training_rights=record.get("uses", {}).get("model_training", "unknown"),
            numerical_screen=result["screen"]["screen_decision"])
    except (ValueError, OSError, KeyError) as error:
        result.update(status="input_unavailable", reason=str(error))
    validate(result)
    save_new(destination / "trade_readiness.json", result)
    (destination / "trade_readiness.md").write_text(markdown(result), encoding="utf-8")
    print(json.dumps({"status": result["status"], "output_dir": str(destination.resolve()),
        "screen_decision": (result["screen"] or {}).get("screen_decision"),
        "reason_codes": (result["screen"] or {}).get("reason_codes"), "real_ml_fit_performed": False,
        "released_score": None, "llm_tokens": 0}))
    return 0 if result["screen"] is not None else 2


if __name__ == "__main__":
    raise SystemExit(main())
