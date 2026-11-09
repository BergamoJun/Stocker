"""Local report rendering; private price values never enter model input."""


LABELS = {"revenue": "매출", "operating_income": "영업손익", "net_income": "순손익", "cash": "현금", "assets": "자산", "liabilities": "부채",
          "operating_cash_flow": "영업현금흐름", "capital_expenditure": "유형자산 취득 현금 지출", "diluted_eps": "희석 주당순손익"}


def safe(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def render(value):
    ai, local = value["ai_input"], value["local_input"]
    identity = ai["identity"]
    lines = [f"# {safe(identity['symbol'])} 공통 분석 입력 연결 보고서", "", f"자료 기준시각: {ai['as_of']}", "",
        "이 문서는 재무·공식 원문·가격 입력의 연결 상태를 설명합니다. 자동 AI 해석은 실행하지 않았으며 정식 목표가·매력도 점수는 보류합니다.", "",
        f"기업: {safe(identity['name'])} · SEC 식별번호: {identity['cik']}", "",
        "## 재무 수치", "", "금액·주당 수치는 원문 단위와 기간을 함께 표시합니다. 분기 손익과 누적 현금흐름을 혼합하지 않습니다.", "",
        "| 항목 | 기준 | 시작 | 종료 | 값 | 단위 | 귀속 범위 | 원문 |", "|---|---|---|---|---:|---|---|---|"]
    for fact in ai["financial_facts"]:
        fields = [LABELS.get(fact["metric"], fact["metric"]), fact["basis"], fact["start"] or "—", fact["end"], fact["value"], fact["unit"], fact["scope"]]
        lines.append("| " + " | ".join(safe(x) for x in fields) + " | [SEC](" + fact["source_refs"][0]["url"] + ") |")
    market = local.get("market", {})
    lines += ["", "## 가격·시장·업종", "", "가격과 가격에서 계산한 값은 이 개인용 로컬 문서에만 포함하며 AI 입력 파일에는 넣지 않습니다.", ""]
    roles = market.get("roles", {})
    if roles:
        lines += ["| 역할 | 종목 | 상태 | 최근 완료 거래일 | 종가 | 통화 |", "|---|---|---|---|---:|---|"]
        for role, row in roles.items():
            quote = row.get("quote", {})
            lines.append("| " + " | ".join(safe(x) for x in [role, row.get("symbol", "—"), row.get("status", "unavailable"), quote.get("session", "—"), quote.get("close", "—"), quote.get("currency", "—")]) + " |")
        for row in market.get("comparisons", []):
            lines.append(f"- {safe(row['stock'])} / {safe(row['proxy'])}: {row['sessions']}거래일 수익률 차이 {safe(row['difference_percentage_points'])}%p. 같은 거래일을 사용한 과거 변화 비교이며 미래 수익이 아닙니다.")
    else:
        lines += ["기존 가격 구성요소를 보존했습니다. 새 다종목 가격 연결은 별도 구성요소가 제공되는 경우 표시합니다."]
    lines += ["", "## 원문·자료 부족", "", f"AI 입력에 포함한 공식 기관 문서: {len(ai['official_documents'])}건. 기업 직접 기사 본문과 구분합니다.", ""]
    for doc in ai["official_documents"]:
        lines.append(f"- [{safe(doc['source'])} 원문]({doc['url']}) · 발행일 {doc['published_date']} · {len(doc['paragraphs'])}개 문단 · 날짜 정밀도 {doc['time_precision']}")
    for gap in local.get("financial_gaps", []):
        lines.append(f"- 재무 보완: {safe(gap.get('metric','회계기간'))} — {safe(gap.get('status','withheld'))}: {safe(gap.get('reason','자료 부족'))}")
    lines += ["", "## 다음 연결", "", "준비된 AI 입력을 자동 사업·사건 해석과 반론 엔진에 전달하는 과정은 후속 구현입니다. 출력 양식의 not_run은 분석 미실행을 뜻합니다. 사람의 기사별 해석 입력을 정상 사용의 필수 단계로 요구하지 않습니다.", ""]
    return "\n".join(lines)
