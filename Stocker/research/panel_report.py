"""Plain Korean explanation of private panel outcomes, with no release claim."""

from decimal import Decimal


def fmt(value, suffix=""):
    return "미산출" if value is None else f"{Decimal(str(value)):.3f}{suffix}"


def bounds(values):
    return "미산출" if not values or any(value is None for value in values) else " ~ ".join(fmt(value, "%") for value in values)


def markdown(result):
    lines = ["# 여러 산업군의 과거 가격 평가 결과", "",
        "## 무엇을 평가했나요?", "",
        "향후 약 1주일(미국 증시의 5거래일) 동안 가격이 어디까지 오를지, 언제 고점이나 목표가에 닿을지 계산하는 기존 수식을 살펴봤습니다.",
        "산업별로 성과를 보기 전에 기업을 골랐습니다. 같은 기업의 주식 종류가 여러 개 있어도 기업 한 개로 셌습니다.",
        "기업 100개 확보와 수익 예측의 정확성은 다른 조건입니다. 이번에는 수식을 조정하거나 AI 모델을 학습시키지 않았습니다.", "",
        f"고정한 기업 {result['selected_unique_issuers']}개 중 개발·중간 구간을 평가한 기업 {result['symbols_evaluated']}개, 처리한 예측 기준일 {result['completed_cases']}건입니다.",
        "나머지 기업 중 일부는 마지막 시험용으로 보관했고, 일부는 자료나 기업 식별이 부족할 수 있습니다. 마지막 시험 기간도 열지 않았습니다.", "",
        "## 지표는 어떤 뜻인가요?", "",
        "- 고점 구간 적중: 이후 5거래일의 실제 최고가가 예상한 가격 구간에 들어간 비율입니다. 매매 승률과 다릅니다.",
        "- 고점 발생일 오차: 예상한 고점 날짜가 실제와 평균 몇 거래일 차이 났는지입니다. 목표가에 처음 닿는 시점과 다릅니다.",
        "- 고점 가격 오차(ATR): 가격 오차를 최근 하루 평균 가격 변동 폭으로 나눈 값입니다. 작을수록 오차가 적습니다.",
        "- 비용 후 수익 범위: 다음 날 시가에 진입한다는 가정에서 매수·매도 비용을 각각 반영한 결과입니다. 하루에 목표가와 손절가가 모두 닿으면 두 체결 순서를 모두 남깁니다.",
        "- 단순 보유 대비 차이: 같은 기간을 그대로 보유한 경우와 비교한 수익률 차이입니다. 단위는 %포인트입니다.",
        "- 거래 보류: 계산이 가능하지만 비용·손익비 등의 조건을 충족하지 못한 경우입니다. 자료 부족이나 계산 오류와 구별합니다.", "",
        "## 산업군별 결과", "",
        "| 산업군 | 고정 기업 | 100개 조건 | 평가 기준일 | 계산 가능 | 고점 구간 적중 | 고점 날짜 오차(거래일) | 고점 가격 오차(ATR) |",
        "|---|---:|---|---:|---:|---:|---:|---:|"]
    for row in result["sectors"]:
        summary = row["development_and_validation"]
        lines.append(f"| {row['name_ko']} | {row['selected_unique_issuers']} | {'충족' if row['minimum_met'] else '부족'} | {summary['cases']} | {summary['forecastable_cases']} | {fmt(summary['modal_price_bin_hit_fraction'])} | {fmt(summary['mean_peak_day_absolute_error_sessions'])} | {fmt(summary['mean_absolute_peak_error_atr'])} |")
    lines += ["", "적중 비율은 0~1 범위입니다. 예를 들어 0.200은 20%를 뜻합니다. 서로 다른 산업의 가격 폭이 달라 ATR로 가격 오차를 비교했습니다.", "",
        "## 비용을 고려하면 어떤가요?", "",
        "아래는 계산 가능한 같은 기준일끼리 비교한 건별 단순 평균입니다. 실제 계좌의 누적 수익률이나 포트폴리오 수익률이 아닙니다.",
        "10bp는 매수 0.10%와 매도 0.10%, 25bp는 각각 0.25%의 비용 가정입니다. 현금 이자·배당·시장 충격은 반영하지 않았습니다.", "",
        "| 산업군 | 편도 비용 | 비교 건수 | 계산 조건 적용 후 평균 수익 범위 | 단순 보유 평균 수익 | 단순 보유 대비 차이(%p) |",
        "|---|---:|---:|---:|---:|---:|"]
    for sector in result["sectors"]:
        for row in sector["development_and_validation"]["cost_scenarios"]:
            excess = row["screened_mean_excess_to_hold_pct_bounds"]
            excess_text = "미산출" if any(x is None for x in excess) else " ~ ".join(fmt(x) for x in excess)
            lines.append(f"| {sector['name_ko']} | {row['cost_bps_per_side']}bp | {row['matched_cases']} | {bounds(row['screened_mean_net_return_pct_bounds'])} | {fmt(row['hold_mean_net_return_pct'], '%')} | {excess_text} |")
    lines += ["", "## 결과를 어디까지 믿을 수 있나요?", "",
        "동일한 날짜의 주식은 함께 오르내리므로, 예측 건수를 서로 독립인 실험 수로 세면 신뢰도를 과장하게 됩니다. 날짜 묶음을 보존한 오차 범위 검사는 다음 검토에서 진행해야 합니다.",
        "무료 계정에서 확보한 약 2년의 자료는 여러 시장 국면을 모두 대표하지 않습니다. 가격과 산업 코드는 현재 시점에 관측한 과거 자료이며 당시 원본 상태를 인증하지 못했습니다.",
        "상장 폐지·자료 누락·기업 식별 실패가 있는 기업을 수익이 좋은 다른 기업으로 바꾸지 않았습니다. 수집 실패 목록과 구간·월·변동성·거래대금별 결과는 panel_result.json에 보존했습니다.",
        "현재 뉴스와 재무정보를 과거 예측에 끼워 넣지 않았습니다. 이 결과만으로 추천 점수나 검증된 수익성을 승인하지 않습니다.", "",
        "다음은 부족한 산업군과 자료 누락을 검토하고, 날짜 간 의존성을 반영한 오차 범위를 계산한 뒤 후보 수식의 변경안을 별도로 등록하는 단계입니다. 마지막 시험용 정답은 그 이후 한 번의 비교를 위해 남겨둡니다.", ""]
    return "\n".join(lines)
