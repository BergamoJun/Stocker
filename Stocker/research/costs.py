"""Offline request/token budget calculations; never reads a key or calls an API."""

import argparse
from decimal import Decimal, ROUND_CEILING
import json
from pathlib import Path

from .sec_report import ROOT


def calculate(settings):
    assumptions = settings["report_token_assumptions"]
    multiplier = Decimal(assumptions["overhead_multiplier"])
    if multiplier < 1:
        raise ValueError("Token headroom must be at least one")
    per_input = int((sum(Decimal(step["input"]) for step in assumptions["steps"]) * multiplier).to_integral_value(rounding=ROUND_CEILING))
    per_output = int((sum(Decimal(step["output"]) for step in assumptions["steps"]) * multiplier).to_integral_value(rounding=ROUND_CEILING))
    workloads = []
    for workload in settings["workloads"]:
        if any(type(workload[key]) is not int or workload[key] < 1 for key in ("tickers", "sectors", "reports_per_trading_day", "news_polls_per_calendar_day")):
            raise ValueError("Positive integer workload dimensions required")
        reports = workload["reports_per_trading_day"] * settings["trading_days_per_month_assumed"]
        inputs, outputs = per_input * reports, per_output * reports
        daily_news = (workload["tickers"] + workload["sectors"] + 2) * workload["news_polls_per_calendar_day"]
        price_batch = workload["tickers"] + workload["sectors"] + 1
        cost = {}
        for name, rates in settings["openai"]["prices_usd_per_million_standard"].items():
            amount = (Decimal(inputs) * Decimal(rates["input"]) + Decimal(outputs) * Decimal(rates["output"])) / 1000000
            cost[name] = {"monthly_usd": str(amount), "credit_sufficiency": {
                credit: Decimal(credit) >= amount for credit in settings["openai"]["conditional_credit_examples_usd"]}}
        workloads.append({**workload, "monthly_reports": reports, "monthly_input_tokens": inputs,
            "monthly_output_tokens": outputs, "monthly_total_tokens": inputs + outputs,
            "daily_news_requests": daily_news, "monthly_news_requests": daily_news * settings["calendar_days_per_month_assumed"],
            "news_daily_requests_with_25pct_headroom": int((Decimal(daily_news) * Decimal("1.25")).to_integral_value(rounding=ROUND_CEILING)),
            "gnews_free_nominal_request_capacity_sufficient": daily_news <= settings["gnews_free"]["requests_per_day"],
            "gnews_free_freshness_is_realtime": False,
            "gnews_essential_request_capacity_sufficient": daily_news <= settings["gnews_essential"]["requests_per_day"],
            "price_requests_per_eod_batch": price_batch,
            "monthly_price_requests": price_batch * settings["trading_days_per_month_assumed"],
            "safe_price_batch_spacing_seconds_assumed": 15,
            "price_batch_minimum_spacing_duration_seconds": (price_batch - 1) * 15,
            "llm_estimates": cost})
    return {"version": "1.0.0", "checked_on": settings["checked_on"],
        "current_implemented_llm_calls": 0, "current_algorithm_llm_tokens": 0, "current_algorithm_llm_cost_usd": "0",
        "future_estimates_are_not_measured_usage": True,
        "per_report_input_tokens_including_headroom": per_input, "per_report_output_tokens_including_headroom": per_output,
        "verified_available_api_credit_usd": settings["llm_free_credit_usd_verified"],
        "gdelt_daily_capacity_guaranteed": False, "paid_subscriptions_activated": False,
        "workloads": workloads, "limitations": [assumptions["note"], settings["scaling_note"], settings["openai"]["credit_conditions"]]}


def markdown(result):
    lines = ["# 데이터 요청·LLM 토큰 비용 산출", "", f"공식 가격 확인: {result['checked_on']}",
        "현재 수치 계산·목표/기간 시뮬레이션은 로컬 코드이며 LLM 호출/토큰/추가 API 비용은 0이다.",
        "아래는 향후 3단계 LLM 문안 생성의 가정치다. 호출을 활성화하거나 결제하지 않았다.",
        f"보고서당 입력 {result['per_report_input_tokens_including_headroom']} / 출력 {result['per_report_output_tokens_including_headroom']} 토큰(25% 여유 포함). 월 22거래일, 뉴스 30일을 가정한다.", "",
        "| 시나리오 | 월 보고서 | 월 입력/출력 토큰 | 뉴스 요청/일 | Luna USD/월 | Terra USD/월 | Sol USD/월 |",
        "|---|---:|---:|---:|---:|---:|---:|"]
    for row in result["workloads"]:
        values = [row['llm_estimates'][key]['monthly_usd'] for key in ('gpt-6-luna','gpt-6.1-terra','gpt-6.1-sol')]
        lines.append(f"| {row['id']} | {row['monthly_reports']} | {row['monthly_input_tokens']}/{row['monthly_output_tokens']} | {row['daily_news_requests']} | {' | '.join(values)} |")
    lines += ["", "가격/GDELT 요청 수와 LLM 토큰은 서로 다른 한도다. Massive Basic은 분당 5회·2년 EOD이며 배치 간격으로 처리한다.",
        "GDELT에는 확인된 유료 업그레이드/보장 일일 할당량이 없다. 429만으로 유료가 필수라고 결론 내리지 않는다.",
        "GNews Free는 하루 100회이지만 12시간 지연이고, NewsAPI Developer는 100회/일·24시간 지연·개발/테스트 전용이다.",
        "GNews Essential은 월 €49.99·하루 1000회·실시간 메타데이터 제공 범위다. 본문/AI 사용권은 별도 확인한다. 환율/세금과 LLM 비용은 합산하지 않았다.",
        "OpenAI Free 사용 티어의 $100/month 표시는 지출 상한이며 무료 크레딧 지급액이 아니다.",
        "일부 Pro/Business 구독에는 활성화 가능한 API 크레딧이 있다. 본인 자격·잔액·만료·모델 접근/속도 제한 확인 전 실제 무료량으로 가정하지 않는다.", "",
        "공식 근거: [Massive](https://massive.com/stocks), [GDELT](https://www.gdeltproject.org/about.html#termsofuse), [GNews](https://gnews.io/pricing), [NewsAPI](https://newsapi.org/pricing), [OpenAI 가격](https://developers.openai.com/api/docs/pricing), [구독 API 크레딧](https://developers.openai.com/api/docs/guides/chatgpt-api-credits).", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Calculate capacity and hypothetical token spend offline")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports/private/cost_estimate")
    args = parser.parse_args(argv)
    config = json.loads((ROOT / "config/service_budget_policy.json").read_text(encoding="utf-8"))
    result = calculate(config)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "cost_estimate.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "cost_estimate.md").write_text(markdown(result), encoding="utf-8")
    print(json.dumps({"output_dir": str(args.output_dir.resolve()), "current_algorithm_llm_cost_usd": "0",
                     "workloads": result["workloads"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
