"""Deterministic Korean research explanation; no external language model."""

from decimal import Decimal
from zoneinfo import ZoneInfo

from .rights import as_utc

REASONS = {
    "ENGINEERING_PILOT_NOT_REPRESENTATIVE": "이미 살펴본 한 종목의 실행 점검 자료",
    "INSUFFICIENT_SELECTED_ISSUERS": "산업군별 선정 기업 수 부족",
    "INSUFFICIENT_MATCHED_ISSUERS": "비교 가능한 기업 수 부족",
    "INSUFFICIENT_MATCHED_COVERAGE": "전체 예정 사례 중 비교 가능한 비율 부족",
    "INSUFFICIENT_REFERENCE_DATES": "서로 다른 평가 기준일 부족",
    "INSUFFICIENT_CALENDAR_BLOCKS": "연속 기간 묶음 수 부족",
    "REFERENCE_DATE_WITHOUT_COMPARISONS": "비교 자료가 전혀 없는 기준일 존재",
    "EXPECTED_CASE_MISSING": "예정한 기준일의 결과 파일 없음",
    "STOCK_INPUT_UNRESOLVED_OR_EVALUATION_MISSING": "기업의 가격·식별 또는 평가 결과 미확인",
    "MISSING_REFERENCE_OR_INSUFFICIENT_HISTORY": "기준일 가격 또는 이전 이력 부족",
    "MISSING_FIVE_SESSION_LABEL": "이후 5거래일의 실제 가격 부족",
    "FORECAST_UNSUPPORTED_OR_INVALID": "기존 수식의 계산 지원 범위 밖이거나 입력 검사 실패",
    "COST_SCREEN_UNAVAILABLE": "비용·위험 조건 계산 불가"}
PRIORITIES = {"REVIEW_MISSING_DATA_BEFORE_TUNING": "수식 조정보다 먼저 누락 자료의 원인과 빠진 기업의 특성을 확인해야 합니다.",
    "ALL_SCREENED_RESULTS_ARE_CASH": "조건 적용 후 진입 사례가 모두 0건입니다. 비교 성적은 관망 결과이며 매매 이익을 입증하지 않습니다.",
    "CONDITIONAL_TARGET_TIMING_SUPPORT_LOW": "목표에 먼저 닿아 시기를 비교할 수 있는 사례가 적어 기간 추정의 안정성이 미확인입니다.",
    "RESULT_DEPENDS_ON_ONE_MONTH": "한 달을 제외하면 수익 차이의 방향이 유지되지 않는 경우가 있습니다. 특정 시기 의존성을 먼저 확인해야 합니다.",
    "LONGER_TIME_COVERAGE_NEEDED": "기간 묶음 수가 부족한 설정이 있습니다. 종목 수 증가와 별도로 더 긴 시간 범위가 필요합니다."}
PRIORITIES["COST_ADJUSTED_PROFIT_NOT_POSITIVE"] = "비용 후 평균 수익의 상한도 양수가 아닙니다. 보유 대비 차이와 실제 이익 발생 여부를 구별해 확인해야 합니다."
DIRECTIONS = {"positive_sample_excess": "관측 기간에서 보유 대비 양의 차이", "negative_sample_excess": "관측 기간에서 보유 대비 음의 차이",
    "inconclusive": "보유 대비 우위·열위의 구분 불확실", "insufficient_support": "자료 조건 부족으로 추정 보류"}


def fmt(value, suffix=""):
    return "미산출" if value is None else f"{Decimal(str(value)):.3f}{suffix}"


def interval(values, suffix=""):
    return "미산출" if not values or any(value is None for value in values) else " ~ ".join(fmt(value, suffix) for value in values)


def text(value):
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def markdown(result, settings):
    stamp = as_utc(result["as_of"]).astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST")
    nominal_pct = (Decimal(str(settings["bootstrap"]["percentiles"][1])) - Decimal(str(settings["bootstrap"]["percentiles"][0]))) * 100
    lines = ["# 과거 예측의 성적과 불확실성 해석", "", f"생성 시각: {stamp}", "", result["label"], "",
        "이 문서는 저장된 예측과 실제 결과를 읽어 정확도·비용 후 수익·자료 부족을 설명합니다. 예측 수식을 바꾸거나 학습시키는 과정은 아닙니다.",
        "확인 질문은 ‘고점과 시기를 얼마나 가깝게 예상했나’, ‘거래 비용 후 단순 보유보다 나았나’, ‘특정 기간에만 좋았나’, ‘결론을 추정할 자료가 충분한가’입니다.", ""]
    if result["status"] in {"waiting_for_panel_results", "source_incomplete"}:
        progress = result["source_progress"]
        stages = {"catalog": "과거 명부 수집", "profiles": "기업·산업분류 확인", "cohort": "평가 기업 선정", "prices": "가격·식별 확인",
            "evaluation": "개발·중간 확인 구간 평가", "summary": "기존 결과 집계", "done": "기존 작업 종료"}
        lines += ["## 아직 성적을 산출하지 않은 이유", "",
            "기존 작업이 완료되기 전에는 일부 기업의 성적만으로 전체 결과를 해석하지 않습니다.",
            f"현재 원본 작업 단계: {stages.get(progress.get('stage'), '시작 전 또는 상태 확인 필요')}",
            f"기업·분류 확인 처리 {progress.get('profile_index', 0)}개 / 명부 후보 {progress.get('catalog_eligible_symbols', 0)}종목.",
            "수집·평가가 완료된 뒤 같은 명령을 다시 실행하면 전체 자료와 실패 분모를 함께 읽습니다. 기존 작업이 한도 도달이나 입력 확인 불가로 멈춘 경우에는 그 원인을 먼저 확인합니다.",
            "이번 실행의 추가 API 요청·외부 AI 호출·학습은 모두 0회입니다. 마지막 시험용 기업과 기간도 읽지 않았습니다.", ""]
        return "\n".join(lines)
    if result["source_kind"] == "pilot":
        lines += ["BE 한 종목의 기존 실행 점검 자료를 읽었습니다. 이 문서의 역할은 기록 읽기·분모·설명 경로 확인이며 산업군 일반화 추정은 보류합니다.", ""]
    for phase, period in result["reference_periods"].items():
        name = "개발" if phase == "development" else "중간 확인"
        lines += [f"{name} 기준일 범위: {period['first_reference']} ~ {period['last_reference']}; 실제 결과의 마지막 거래일: {period['last_label_session']}."]
    lines += [""]
    lines += ["## 지표를 읽는 방법", "",
        "- 가격 오차(ATR)는 최근 14거래일의 평균적인 하루 가격 변동 폭을 단위로 삼습니다. 작은 값일수록 고점 가격 오차가 작습니다.",
        "- 고점 날짜 오차는 예상한 최고가 형성일과 실제 날짜의 차이입니다. 목표가에 먼저 도달한 사례의 시기와 구별합니다.",
        "- 수익률 차이의 단위 %p는 퍼센트포인트입니다. 모델의 비용 후 가상 수익률에서 같은 기간 단순 보유의 비용 후 수익률을 뺍니다.",
        "- 같은 날 목표·손실 기준을 모두 건드려 체결 순서를 모르면 가능한 손익의 하한·상한을 모두 유지합니다.",
        "- 자료·계산 부족은 수익을 계산할 수 없는 상태입니다. 계산 결과에 따른 거래 보류의 현금 수익 0%와 구별하며 전체 예정 사례 수에 남깁니다.", "",
        "## 정확도와 자료 범위", "",
        "| 산업군 / 구간 | 예정 기준일·기업 조합 | 계산 가능 | 자료·계산 부족 | 평균 고점 가격 오차(ATR) | 평균 고점 날짜 오차(거래일) | 고점 구간 적중 비율 |",
        "|---|---:|---:|---:|---:|---:|---:|"]
    for group in result["groups"]:
        point = group["point"]
        phase = "개발" if group["phase"] == "development" else "중간 확인"
        lines.append(f"| {text(group['name_ko'])} / {phase} | {point['expected_slots']} | {point['forecastable_slots']} | {point['unavailable_slots']} | {fmt(point['mean_absolute_peak_error_atr'])} | {fmt(point['mean_peak_day_error_sessions'])} | {fmt(point['modal_price_bin_hit_fraction'])} |")
    lines += ["", "비율은 0~1로 표시합니다. 많은 기준일·기업 조합이 있어도 같은 날짜의 종목들은 함께 움직일 수 있어 모두 독립 실험으로 세지 않습니다.", "",
        "## 거래 비용을 반영한 비교", "",
        "10bp는 매수·매도 각각 0.10%, 25bp는 각각 0.25%의 가정입니다. 비교 가능한 동일 사례끼리 건별 평균을 냅니다. 실제 계좌의 누적 수익률은 아닙니다.", "",
        "| 산업군 / 구간 | 편도 비용 | 비교 건수 | 조건 적용 후 진입 건수 | 조건 적용 후 평균 수익 범위 | 단순 보유 대비 차이(%p) |",
        "|---|---:|---:|---:|---:|---:|"]
    for group in result["groups"]:
        phase = "개발" if group["phase"] == "development" else "중간 확인"
        for row in group["point"]["cost_scenarios"]:
            lines.append(f"| {text(group['name_ko'])} / {phase} | {row['cost_bps_per_side']}bp | {row['matched_slots']} | {row['screened_entry_slots']} | {interval(row['screened_mean_net_return_pct_bounds'], '%')} | {interval(row['screened_mean_excess_to_hold_pct_points_bounds'])} |")
    lines += ["", "## 성적은 기간을 달리 묶어도 유지되나요?", "",
        "같은 날짜의 모든 기업을 함께 유지하고 연속된 기준일 묶음을 반복 추출했습니다. 모델과 단순 보유에도 같은 묶음을 적용합니다. 개발 구간과 중간 확인 구간은 섞지 않습니다.",
        f"기본 묶음은 기준일 {settings['bootstrap']['primary_block_references']}개이며 보조 묶음 {', '.join(map(str, settings['bootstrap']['sensitivity_block_references']))}개도 함께 표시합니다. 조건을 충족한 경우 {settings['bootstrap']['replications']}회 계산합니다.",
        f"아래는 두 체결 경계의 명목 {nominal_pct:g}% 백분위 구간에서 보수적으로 잡은 외곽 범위입니다. 다음 매매에서 이길 확률을 뜻하지 않습니다. 반복 계산 횟수도 새로운 시장 표본 수에 더하지 않습니다.",
        f"표는 기준 비용 {settings['primary_cost_bps_per_side']}bp와 기본 기간 묶음의 결과입니다. 다른 비용과 2·8개 기준일 묶음의 전체 결과는 diagnostics.json에 함께 보존했습니다.", "",
        "| 산업군 / 구간 | 선정 / 비교 기업 | 서로 다른 기준일 | 기본 기간 묶음 수 | 보유 대비 차이의 참고 범위(%p) | 해석 또는 보류 이유 |",
        "|---|---:|---:|---:|---:|---|"]
    for group in result["groups"]:
        phase = "개발" if group["phase"] == "development" else "중간 확인"
        cost = next(row for row in group["uncertainty"] if row["cost_bps_per_side"] == settings["primary_cost_bps_per_side"])
        point = next(row for row in group["point"]["cost_scenarios"] if row["cost_bps_per_side"] == settings["primary_cost_bps_per_side"])
        block = cost["blocks"][0]
        values = block["intervals"]["conservative_excess_envelope_pct_points"] if block["intervals"] else None
        note = DIRECTIONS[block["direction"]] if values else "; ".join(REASONS.get(code, "입력 또는 표본 조건 확인 필요") for code in block["reason_codes"])
        lines.append(f"| {text(group['name_ko'])} / {phase} | {group['selected_unique_issuers']} / {point['matched_issuers']} | {block['reference_dates']} | {block['nominal_nonoverlapping_blocks']} | {interval(values)} | {note} |")
    coverage = Decimal(settings["support"]["minimum_matched_slot_fraction"]) * 100
    lines += ["", f"최소 확인 조건은 산업군 선정 기업 {settings['support']['minimum_selected_issuers_per_sector']}개, 구간별 비교 기업 {settings['support']['minimum_matched_issuers_per_phase']}개, 기준일 {settings['support']['minimum_reference_dates']}개, 명목 기간 묶음 {settings['support']['minimum_nominal_nonoverlapping_blocks']}개, 예정 사례 대비 비교 비율 {coverage}%입니다.",
        "이 값들은 결과 전에 정한 연구용 최소 확인 조건이며 통계적 보정이나 충분한 시장 대표성을 보장하지 않습니다. 큰 묶음의 결과를 계산하지 못하면 기간 묶음 선택에 따른 일관성도 미확인으로 남깁니다.", "",
        "## 어디를 먼저 확인해야 하나요?", ""]
    for group in result["groups"]:
        point = group["point"]
        primary = next(row for row in group["uncertainty"] if row["cost_bps_per_side"] == settings["primary_cost_bps_per_side"])
        label = text(group["name_ko"]) + (" 개발 구간" if group["phase"] == "development" else " 중간 확인 구간")
        lines.append(f"- {label}: {DIRECTIONS[primary['primary_direction']]}. " + ("기간 묶음 길이별 확인을 모두 끝내지 못했습니다." if primary["sensitivity_status"] == "not_all_lengths_supported" else "기간 묶음 길이에 따른 결과는 diagnostics.json에 함께 보존했습니다."))
        lines.extend("  " + PRIORITIES[code] for code in group["review_priorities"])
        if point["unavailable_slots"]:
            reasons = "; ".join(f"{REASONS.get(code, '자료·계산 확인 필요')} {count}건" for code, count in sorted(point["unavailable_reason_counts"].items()))
            lines.append(f"  자료 부족 내역: {reasons}.")
    lines += ["", "월·가격 변동 폭·거래대금·기업별 성적과 거래 보류·목표/손실 우선 도달 내역은 diagnostics.json에 보존했습니다. 목표 도달 시기는 실제로 목표에 먼저 닿고 시기 비교가 가능한 사례만의 조건부 통계입니다.", "",
        "## 이번 결과로 결정할 수 있는 범위", "",
        "우위가 불확실하면 추가 검증이 필요하다는 결론으로 남깁니다. 차이를 확인하지 못한 결과를 효과가 없거나 같다는 증명으로 바꾸지 않습니다.",
        "구간은 정해 둔 기업·관측 기간·비교 가능한 자료에 조건부입니다. 현재에 받은 수정·분할 조정 가격의 한계, 짧은 시간 범위, 시장 국면 변화, 표본 누락의 영향을 별도로 확인해야 합니다.",
        "여러 산업·비용·묶음을 함께 살핀 탐색 결과이며 다중 비교를 보정한 확정적 발견으로 제시하지 않습니다. 현금 이자·배당·시장 충격·계좌 자금 배분과 누적 수익률은 모형화하지 않았습니다.",
        "최종 시험용 기업·기간은 읽지 않았습니다. 기존 작업 파일도 수정하지 않았습니다. 수식 개선 후보와 비교 기준을 별도로 등록한 뒤 최종 시험을 진행해야 합니다.",
        "이번 작업의 학습·추가 API 요청·외부 AI 호출은 0회이며 추천 점수와 상승 확률은 제공하지 않습니다.", ""]
    return "\n".join(lines)
