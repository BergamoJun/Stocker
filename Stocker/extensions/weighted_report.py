"""Beginner-readable local research ledger; never output an approved score."""

from decimal import Decimal

GROUPS = {"profit_opportunity": "수익 기회", "price_setup": "가격·거래량", "fundamentals": "기업 실적·재무", "market_sector": "시장·업종", "latest_events": "최신 사건"}


def safe(value): return str(value).replace("|", "\\|").replace("\n", " ")
def shown(value): return format(Decimal(str(value)).quantize(Decimal(".001")), "f")


def render(value):
    assessment = value["assessment"]
    lines = [f"# {safe(value['identity']['symbol'])} 자동 가중 분석 · 연구용", "", "**RESEARCH ONLY — UNVALIDATED · 정식 추천도·승률·매매 지시가 아닙니다.**", "",
        f"생성: {value['generated_at']} · 사용 자료의 기준시각: {value['evidence_as_of']}", "", value["one_line_ko"], "",
        "## 무엇을 계산했나", "", "수익 기회 25·가격 흐름 15·기업 실적 10·시장/업종 10·최신 사건 40의 설계 가중치를 23개 변수에 나눴습니다. 계수는 과거 성과에 맞춰 학습한 값이 아닙니다.", "",
        "각 변수는 -1~1의 방향 값으로 바꾸고 중요도 계수를 곱합니다. 양수는 가산, 음수는 감점입니다. 0은 확인된 중립/조건 미발생이며 자료 부족과 구분합니다. 이격 변수는 급등 감점만 적용합니다.", "",
        "연구 지수 산식: `50 + 전체 가감점 합계 ÷ 2`, 1~100 범위·반올림. 모든 변수가 있을 때만 experimental_score를 계산합니다. 정식 추천도는 항상 보류합니다.", "",
        f"계산 가능한 변수: {sum(f['status']=='available' for f in value['factors'])}/23 · 계산 가능한 계수 합: {assessment['observed_weight']}/100", "",
        "## 가중치별 계산 상태", "", "| 영역 | 설계 가중치 | 확인된 가감점 합 | 부족한 가중치 |", "|---|---:|---:|---:|"]
    for group, row in assessment["groups"].items(): lines.append(f"| {GROUPS[group]} | {row['maximum_weight']} | {shown(row['known_signed_points'])} | {row['missing_weight']} |")
    if assessment["experimental_score"] is None:
        lines += ["", "**종합 연구 지수: 보류.** 부족한 항목을 0이나 평균으로 채우거나 나머지 가중치를 늘리지 않았습니다.",
            f"미확인 변수가 허용 범위 안에서 달라질 때 가능한 산술 범위: {assessment['completion_score_bounds'][0]}~{assessment['completion_score_bounds'][1]}. 이는 예측 구간·신뢰구간·목표 점수가 아닙니다."]
    else: lines += ["", f"연구용 experimental_score: **{assessment['experimental_score']}**. 계수·예측 성능 검증 전 수치이며 정식 매력도 점수는 아닙니다."]
    lines += ["", "## 어떤 변수가 가산·감점됐나", "", "| 변수 | 계수 | 방향 값 | 가감점 | 근거/보류 사유 |", "|---|---:|---:|---:|---|"]
    for row in value["factors"]:
        lines.append("| " + " | ".join(safe(x) for x in [row["label"], row["weight"], shown(row["value"]) if row["value"] is not None else "보류", shown(row["signed_points"]) if row["signed_points"] is not None else "—", f"출처 {len(row['source_ids'])}개 · 계산식/원시 값은 ENGINE.json" if row["status"] == "available" else row["reason_code"]]) + " |")
    lines += ["", "변수별 계산식·원시 값·기간·출처 ID는 ENGINE.json의 factors에 저장했습니다. 같은 가격에서 나온 추세·상대 변화·모형 수익은 서로 독립적인 예측 증거가 아닙니다.", "",
        "## 미래 가격 모형과 비용·위험", "", "기존 고정 EWMA 통계·5거래일 경로 모형을 재사용했습니다. AI 학습이나 과거 평가 결과에 맞춘 튜닝을 수행하지 않았습니다. 모형의 경로 빈도·손익은 검증된 확률·기대수익률이 아닙니다."]
    screen = value["numeric_research"].get("cost_risk_screen")
    if screen:
        lines += ["", f"비용·위험 계산 상태: {safe(screen['screen_decision'])} · 사유: {safe(', '.join(screen['reason_codes']) or '수치 조건 통과')}.",
            "다른 가점은 비용·위험 조건 실패를 해제하지 않습니다. 정식 가격 계획·도달 시기는 보류합니다."]
    lines += ["", "## 자동으로 읽은 공식 사건과 사업 경로", "", "아래는 허용된 공식 원문의 문구를 고정 규칙으로 찾은 조건부 해석입니다. 주요 사건의 원문 링크를 보존하며 사실·해석·반론을 나눕니다. 기존 사용자 검토 파일을 필수로 요구하지 않습니다."]
    events = value["analysis"]["events"]
    seen = set()
    for event in events:
        if event["cluster_id"] in seen: continue
        seen.add(event["cluster_id"])
        lines += ["", f"- [공식 원문]({event['source_url']}) · 자동 분류: {safe(event['rule_id'])} · 시각 정밀도: {safe(event['time_precision'])}",
            "  - 조건부 사업 경로: " + safe(event["conditional_business_channel"]), "  - 반론: " + safe(event["counterargument"]), "  - 점수 반영: 보류 — 단기 영향·시장 기대·정확한 시각을 확정하지 못했습니다."]
    if not events: lines += ["", "지원하는 공식 사건 문구를 찾지 못했습니다. 이를 사건 없음·위험 없음으로 해석하지 않습니다."]
    lines += ["", "신선도는 `1 + 2^(-경과시간/24시간)`으로 호재·악재의 크기를 동일하게 높이는 연구식입니다. 재보도는 최초 공개 시각을 사용하고 같은 사건을 중복 가산하지 않습니다. 뉴스 주제별 수집·해석 범위가 부족하면 해당 변수는 보류합니다.", "",
        "## 반론·누락·검사 결과", ""]
    for item in value["critic"]["findings"]: lines.append(f"- {safe(item['code'])}: {safe(item['message'])}")
    lines += ["", "로컬 규칙 분석과 숫자·출처·누락·위험 검사를 실행했습니다. 외부 AI 모델 호출·학습·주문은 0회입니다. 별도 ResearchEngine의 자유로운 사업/뉴스 추론과 실증적인 계수 검증은 후속 단계입니다.", ""]
    return "\n".join(lines)
