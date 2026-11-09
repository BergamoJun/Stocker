"""Korean explanations of evidence coverage and conditional research scenarios."""

from decimal import Decimal, localcontext

from .news_connection_report import text, link, kst, STATUS
from .report_language import REASONS
from .sec_financial_report import NAMES


REASONS_KO = {"NO_REVIEWED_ISSUER_PRICE_ADAPTER": "이 기업에 검토된 가격·업종 연결 설정이 없습니다.",
    "NO_REVIEWED_ISSUER_NEWS_ADAPTER": "이 기업에 검토된 뉴스 연결 설정이 없습니다.",
    "NO_INPUT_COMMITTED_BEFORE_CUTOFF": "기준시각 이전에 연결 저장한 가격 입력이 없습니다.", "INPUT_NOT_SELECTED": "저장된 가격 입력을 확보하지 못했습니다.",
    "PRICE_RIGHTS_NOT_CLEARED": "개인용 수집·저장·계산 사용권을 확인하지 못했습니다.", "PRICE_SOURCE_VALIDATION_FAILED": "가격의 원본·정규화·기업 식별 검사에 실패했습니다.",
    "CAPTURED_PRICE_RIGHTS_OR_SOURCE_UNUSABLE": "보관 입력의 현재 사용권 또는 원본 검사가 통과하지 못했습니다.",
    "PRICE_STALE_OR_UNUSABLE": "가장 최근 완료 거래일의 가격이 없거나 입력이 유효하지 않습니다.",
    "FIXED_STATISTICAL_SCENARIO_UNAVAILABLE": "현재 입력에서 고정 통계 시나리오를 산출하지 못했습니다.",
    "REFERENCE_STOCK_SESSION_UNAVAILABLE": "비교할 기업의 현재 기준 거래일을 확인하지 못했습니다.",
    "PROXY_STALE_OR_REFERENCE_MISMATCH": "관련 ETF 가격이 오래됐거나 기업의 기준 거래일과 다릅니다.",
    "LATEST_NEWS_COVERAGE_NOT_CONFIRMED": "최근 시점의 뉴스 검색 범위를 확인하지 못했습니다."}
TOPICS = {"company": "기업", "sector": "업종", "macro_politics": "거시경제·정치", "commodities": "원자재"}


def number(value, *, million=False):
    if value is None:
        return "보류"
    with localcontext() as precision:
        precision.prec = 160
        value = Decimal(str(value)) / (Decimal(1000000) if million else Decimal(1))
        return format(value.quantize(Decimal("0.01")), ",f")


def percent(value):
    return number(value) + "%" if value is not None else "보류"


def reasons(codes):
    return [REASONS_KO.get(c, REASONS.get(c, "추가 자료 확인이 필요합니다.")) for c in codes]


def markdown(value):
    who, market, news, coverage = value["identity"], value["market"], value["news"], value["coverage"]
    finance = value["financial"]
    chain, base = finance["chain"], finance["base_report"]["base_report"]["context"]
    lines = [f"# {text(who['symbol'] or who['cik'])} 재무·가격·뉴스 종합 보고서", "", value["label"], "",
        f"기업: {text(who['name'])} · SEC 기업 식별번호(CIK): {who['cik']}",
        f"자료 기준시각: {kst(value['as_of'])} · UTC {value['as_of']}", f"보고서 생성시각: {kst(value['generated_at'])}", "",
        "**한줄평:** " + value["one_line_ko"], "",
        "## 어떤 요소를 확인했나요?", "", "| 영역 | 확인하려는 질문 | 현재 확인 수준 |", "|---|---|---|"]
    lines += [f"| 공시 재무 | 매출·이익·현금흐름은 어떠하며, 같은 기간끼리 비교했는가? | 원문 검토 {coverage['financial_reviewed']}/{coverage['financial_expected']}개, 전년 비교 {coverage['yoy_reviewed']}개 |",
        f"| 기간별 비교 | 바로 전 분기와 최근 1년 실적은 어떠한가? | 전분기 {coverage['qoq_calculated']}/5개, 최근 12개월 {coverage['ttm_calculated']}/5개 |",
        "| 가격 연구 | 5거래일 가격 범위와 비용·위험 조건은 어떠한가? | " + ("연구 계산 연결" if coverage["price_scenario_available"] else "자료·계산 보류") + " |",
        f"| 시장·업종 | 해당 기업에 맞는 ETF의 관측 흐름은 어떠한가? | 검토된 연결 {coverage['proxies_available']}/2개 |",
        f"| 뉴스 사건 | 원문과 영향 검토가 같은 기사 버전·시각에 맞는가? | 보관 기사 {coverage['news_articles']}건, 주요 검토 사건 {coverage['news_highlights']}건 |", "",
        "개수는 연결·검토 가능한 항목 수입니다. 예측 정확도나 독립된 거래 표본 수로 해석하지 않습니다.", "",
        "## 1. 5거래일 가격 연구 시나리오", "", "정식 목표가·투자 매력도·예상 수익실현 시기는 검증 대기입니다. 아래 가격은 가정을 둔 연구 계산이며 종가는 실시간 현재가와 다릅니다.", ""]
    stock = market["stock"]
    if stock["input"]:
        data = stock["input"]
        lines += [f"- 가격 기준 거래일: {data['reference_session']} · 실제 조회: {kst(data['retrieved_at'])}"]
        if stock["current_input"]:
            lines.append(f"- 최신 확보 정규장 종가: {number(data['reference_close'])} USD. 배당 포함 총수익 가격과 구분합니다.")
    if stock["status"] == "available_research_scenario":
        forecast, screen = stock["forecast"], stock["screen"]
        interval = forecast["peak"]["modal_price_interval"]
        lines += [f"- 모형 내 고점이 가장 많이 모인 가격 구간: {number(interval[0])}~{number(interval[1])} USD.",
            "- 이 구간은 다음 5거래일 전체의 최고가에 관한 계산입니다. 첫 번째 작은 반등의 꼭짓점이나 검증된 도달 확률과 구분합니다."]
        plan = forecast["plan"]
        if plan:
            window = plan["timing"]
            lines += [f"- 조건부 목표 후보: {number(plan['first_target_price'])} USD · 가설 무효화 후보: {number(plan['invalidation_price'])} USD.",
                f"- 관찰할 거래일: {window['sessions'][0]}~{window['sessions'][-1]}의 정규장 5회.",
                f"- 자료 유효기한: {kst(window['expires_at'])} · 관찰기간 말 재평가: {kst(window['review_at'])}.",
                "- 위 관찰기간은 고정된 평가 창입니다. 특정 날짜의 수익실현을 예상한 값으로 바꾸지 않습니다."]
        lines += ["", "**비용·위험 검사:** " + ("검사한 수치 조건 충족, 실제 성능 검증 필요" if screen["screen_decision"] == "numerical_candidate" else "수치 조건 미충족 또는 입력 보류"), ""]
        lines += ["- " + explanation for explanation in reasons(screen["reason_codes"])]
        if screen["metrics"]:
            lines += ["", "| 매수·매도 각각의 가정 비용 | 종가→목표 비용 후 여력 | 비용 후 손익비 |", "|---|---:|---:|"]
            for row in screen["metrics"]:
                lines.append(f"| {row['cost_bps_per_side']}bp | {percent(row['net_reference_to_target_pct'])} | {number(row['net_reward_risk'])} |")
            lines += ["", "1bp는 0.01%입니다. 손익비는 예상 손실 1에 대한 이익의 비율이며 다음 시가의 실제 체결이나 세후 수익을 보장하지 않습니다."]
    else:
        lines += ["- " + reason for reason in reasons(stock["reason_codes"])]
    lines += ["", "## 2. 기업 실적: 전분기와 최근 12개월", "",
        "전분기 대비(QoQ)는 인접한 두 분기의 변화이고 최근 12개월(TTM)은 직전 연간+올해 누적−전년 같은 누적입니다. 금액 단위는 **백만 USD**입니다.", "",
        "| 항목 | 현재 분기 | 이전 분기 | 전분기 변화율 | 최근 12개월 |", "|---|---:|---:|---:|---:|"]
    for metric, item in chain["metrics"].items():
        def amount(key):
            return number(item[key]["value"], million=True) if item[key] else "보류"
        change = item["qoq"]["percent_change"] if item["qoq"] else None
        lines.append(f"| {NAMES[metric]} | {amount('current_quarter')} | {amount('previous_quarter')} | {percent(change)} | {amount('ttm')} |")
    for metric, item in chain["metrics"].items():
        if item["current_quarter"]:
            p = item["current_quarter"]
            origin = "원문 3개월 값" if p["kind"] == "direct_reported_quarter" else "누적값 차감으로 계산"
            lines.append(f"- {NAMES[metric]} 현재 분기: {p['start']}~{p['end']} · {origin}.")
    lines += ["", "영업이익은 회계상 본업 손익이고 영업현금흐름은 사업에서 실제 드나든 현금입니다. 두 값의 방향이 다르면 운전자본 등 원인을 원문에서 추가 확인합니다. 유형자산 취득 지출은 현금 지출을 양수로 표시합니다.",
        "현재 이익과 현금흐름은 원문 범위·기간이 맞는 경우만 연결합니다. 이전 값이 0 이하이면 변화율을 보류하고 증감액·손익 전환은 상세 자료에 남깁니다.", "",
        "### 같은 기간의 전년 대비", "", "| 공시 기준일 | 항목 | 비교한 현재 기간 | 전년 대비 |", "|---|---|---|---:|"]
    for section in finance["base_report"]["growth"]["sections"]:
        current = next((s for s in base["sections"] if s["accession"] == section["accession"]), None)
        if not current:
            continue
        for metric in chain["metrics"]:
            item = section["metrics"][metric]
            fact = current["metrics"][metric]
            period = f"{fact['start']}~{fact['end']}" if fact["start"] else fact["end"]
            change = item["calculation"]["percent_change"] if item["calculation"] else None
            lines.append(f"| {section['report_date']} | {NAMES[metric]} | {text(period)} | {percent(change)} |")
    lines += ["", "검토 공백과 귀속 범위·정정 공시 사유: [현재·연간 재무](CURRENT.ko.md), [전년 대비 상세](YOY.ko.md), [전분기·최근 12개월 산식과 원문](FINANCIAL.ko.md).", "",
        "## 3. 시장·업종의 가격 관측", ""]
    for card in market["proxies"]:
        if card["status"] != "available_price_observation":
            lines.append("- " + ("시장" if card["role"] == "market" else "업종") + ": " + " ".join(reasons(card["reason_codes"])))
            continue
        observation = card["observation"]
        direction = {"above": "위", "below": "아래", "equal": "동일 수준"}[observation["close_vs_twenty_session_mean"]]
        lines += [f"- **{card['symbol']}**: {observation['explanation_ko']}.",
            f"  - {observation['reference_session']} 종가는 20거래일 평균 {direction}, 최근 5거래일 가격 변화 {percent(observation['five_session_price_change_pct'])}.",
            "  - " + observation["limitation_ko"], "  - " + link(observation["evidence_url"], "ETF 운용사 설명")]
    lines += ["", "ETF는 여러 기업을 담아 거래되는 상품입니다. 해당 기업의 사업 구성이나 미래 수익을 그대로 대변하지 않으며, 관측 흐름을 목표가 가산점으로 자동 변환하지 않습니다.", "",
        "## 4. 뉴스 검색 범위와 주요 검토 사건", ""]
    snapshot = news["snapshot"]
    if snapshot:
        coverage_by_topic = {row["id"]: row for row in (snapshot["coverage"] or [])}
        lines += ["| 주제 | 최근 범위 확인 |", "|---|---|"]
        for key, label in TOPICS.items():
            item = coverage_by_topic.get(key)
            current = bool(snapshot["coverage_current_at_cutoff"] and item and item["discovery_status"] != "not_verified")
            lines.append(f"| {label} | " + ("제한된 검색 확인; 사건 영향은 별도 검토" if current else "미확인·오래됨·제한된 보완 자료") + " |")
        highlighted = [event for event in snapshot["events"] if event["highlight"]]
        if not highlighted:
            lines += ["", "**현재 주요 사건 강조 조건을 충족한 항목은 없습니다.** 기사 링크의 존재와 검토된 호재·악재를 구분해야 하며, 위험이 없다는 판정으로 해석하지 않습니다."]
        for event in highlighted:
            opinion = next(o for o in event["opinions"] if o["status"] == "review_linked")
            lines += ["", "### " + ("긍정 영향으로 검토한 사건" if opinion["direction"] == 1 else "부정 영향으로 검토한 사건"), "",
                "원문: " + " · ".join(link(url, f"원문 {i}") for i, url in enumerate(opinion["original_urls"], 1)),
                "- 근거: " + text(opinion["rationale"]), "- 반론: " + text(opinion["counterargument"]), "- 다음 확인: " + text(opinion["monitoring_event"])]
        lines += ["", "### 원문·영향 검토 대기 또는 연결 상태", ""]
        for article in snapshot["articles"]:
            lines.append("- " + link(article["metadata"]["url"], text(article["metadata"]["title"])) + " — " + STATUS[article["status"]] + (" · 메타데이터 확인 오래됨" if not article["metadata_fresh_at_cutoff"] else ""))
        lines += ["", "기사 공급자의 발행 표기와 원문 확인 시각은 구분합니다. 공식 발표 몇 건으로 세계 정치·원자재 위험 전체를 확인했다고 판단하지 않습니다. [기사 버전·검토 시각 상세](NEWS.ko.md)."]
    else:
        lines += ["- " + reason for reason in reasons(news["reason_codes"])]
    lines += ["", "원자재 **가격 수치**는 이 보고서에 연결되지 않았습니다. 뉴스 제목으로 원유·가스 가격을 추정하거나 미확인을 0으로 채우지 않습니다.", "",
        "## 5. 앞으로 확인할 조건", "",
        "- 강세 경로 점검: 가격 후보가 있을 때 목표·무효화 중 어느 조건이 먼저 발생하는지, 검토된 호재와 재무 개선이 지속되는지 확인합니다.",
        "- 기준 경로 점검: 두 가격 조건이 모두 발생하지 않으면 관찰기간 말에 새 가격·공시·뉴스로 재평가합니다.",
        "- 약세 경로 점검: 무효화 조건, 새 악재·정정 공시, 이익 대비 현금 유출이 나타나면 기존 가정을 재검토합니다.",
        "- 가격 자료가 보류된 기업은 조건 가격부터 새로 확보해야 합니다. 같은 일봉에서 양쪽에 도달하면 선후 순서를 추측하지 않습니다.", "",
        "위 경로는 관찰할 조건이며 확률·자동 주문·확정된 미래 사건과 구분합니다. 기존 예측 기록과 관찰 결과는 이 보고서 생성으로 바뀌지 않습니다.", "",
        "## 자료 시점·출처·한계", ""]
    if value["input_binding"]:
        lines.append("- 이 보고서용 가격 입력 최초 연결: " + kst(value["input_binding"]["available_at"]) + ". 원래 가격 조회 시각과 별도로 보존했습니다.")
    lines += ["- " + item for item in value["limitations"]]
    lines += ["- 추가 API 요청·외부 LLM API·학습·주문: 0회. 전체 구조와 계산 근거는 같은 폴더의 `report.json`에 보존합니다.", ""]
    for source in value["source_catalog"]:
        lines.append("- " + link(source["url"], source["kind"]) + " · " + text(source["id"]) + " · 실제 조회 " + text(source["retrieved_at"]))
    return "\n".join(lines) + "\n"
