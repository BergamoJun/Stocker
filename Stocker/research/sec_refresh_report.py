"""Korean operator and financial-context views; no price target or investment score."""

from pathlib import Path
import html
import uuid

from .sec_report import ROOT


STATUS = {"completed_batch": "이번 순번의 수집을 완료했습니다", "partial": "일부 자료를 받지 못했습니다",
    "awaiting_user_agent": "SEC 요청자 식별 환경변수 설정을 기다리고 있습니다", "deferred": "요청 제한 또는 실행 예산으로 다음 실행을 기다립니다",
    "source_unavailable": "SEC 자료를 현재 받을 수 없습니다", "unresolved_watchlist": "기업 식별을 확인하지 못했습니다", "budget_deferred": "이번 실행의 시간 한도에 도달했습니다"}


def safe(value):
    return html.escape(str(value)).replace("|", "\\|").replace("\r", " ").replace("\n", " ").replace("[", "&#91;").replace("]", "&#93;")


def render_run(result):
    rows = result["entities"]
    lines = ["# SEC 다종목 자동 갱신 결과", "", "ILLUSTRATIVE — NOT LIVE MARKET DATA" if result["is_illustrative"] else "실제 SEC 공개 자료 수집 상태 · 개인 로컬 연구", "",
        STATUS.get(result["status"], result["status"]) + ".", "",
        "이 검사는 여러 기업의 공시와 재무 수치를 빠짐없이 수집·보관하는지 확인합니다. 주가 예측 정확도나 투자 수익을 평가하는 검사는 아닙니다.", "",
        f"- 관심 목록 항목: {result['watchlist_entries']}개. 여러 주식 종류가 같은 기업이면 CIK(SEC 기업 식별번호) 하나로 합칩니다.",
        f"- 이번 실행에서 처리한 기업: {len(rows)}개. 기업 식별 보류: {len(result['unresolved'])}개.",
        f"- SEC 실제 요청: {result.get('network_requests', 0)}회. 저장 자료를 재사용하면 요청하지 않습니다.",
        f"- 시작 / 종료(UTC): {result['started_at']} / {result['finished_at']}", "",
        "| 기업 (CIK) | 수집 결과 | 보관 공시 수 | 재무 수치 대기 | 원문 대기 | 원문 오류 |", "|---|---|---:|---:|---:|---:|"]
    labels = {"updated": "새 버전 저장", "unchanged": "변경 없음", "unavailable": "수집 보류"}
    for row in rows:
        name = '/'.join(row.get('symbols', [])) or row.get('name') or '기업 식별번호'
        lines.append(f"| {safe(name)} ({row['cik']}) | {labels.get(row['status'], row['status'])} | {row.get('known_filings', '—')} | {row.get('awaiting_facts', '—')} | {row.get('pending_documents', '—')} | {row.get('document_errors', '—')} |")
    lines += ["", "10-K는 연간 보고서, 10-Q는 분기 보고서, 8-K는 주요 사건 공시입니다. /A는 수정 공시이며 원본과 별도로 저장합니다.", "",
        "재무 수치는 표준 회계 항목에서 자동 추출한 미검토 값입니다. 같은 공시·기간·통화·단위가 확인된 항목만 계산하며, 기업 고유 항목이나 해석이 필요한 수정 공시는 보류합니다.", "",
        "새 자료의 이용 가능 시점은 로컬에 처음 저장한 때입니다. 과거 실적의 결산일이나 공시 접수일로 되돌려 과거 예측에 끼워 넣지 않습니다.", "",
        "다음 확인: 미수집 자료 재시도 → 원문과 재무 의미 대조 → 별도 투자 보고서 연결. 현재 점수·목표가·매매 신호는 만들지 않습니다.", ""]
    if result.get("reason"):
        lines += [f"처리 보류 코드: `{result['reason']}`", ""]
    if result["unresolved"]:
        lines += ["기업 식별 보류:", ""] + [f"- {r.get('symbol') or r.get('cik')}: `{r['reason']}`" for r in result["unresolved"]]
    return "\n".join(lines)


def render_context(snapshot):
    names = {"revenue": "매출", "operating_income": "영업이익", "net_income": "순이익", "cash": "현금 및 현금성 자산", "assets": "자산", "liabilities": "부채",
        "operating_cash_flow": "영업활동 현금흐름", "capital_expenditure": "유형자산 취득 지출", "diluted_eps": "희석 주당순이익"}
    states = {"unreviewed_standard_fact": "표준 수치 추출 · 원문 대조 전", "conflicting_standard_facts": "값 또는 기간 충돌로 보류", "missing_exact_filing_period_unit": "공시·기간·단위가 맞는 값 없음"}
    lines = ["# SEC 자동 재무 참고자료", "", snapshot["label"], "", f"기업: {safe(snapshot.get('name'))} · CIK {snapshot['cik']}", "",
        f"로컬 이용 가능 시점(UTC): {snapshot.get('available_at', '저장 시점 확인 필요')}", "",
        "아래 수치는 SEC 표준 회계 자료에서 자동 추출했습니다. 원문 대조를 마친 투자 의견이 아니며, 최신 자료로 과거 예측을 다시 쓰지 않습니다.", ""]
    for section in snapshot["financials"]["sections"]:
        lines += [f"## {section['form']} · 결산 기준일 {section['report_date']}", "", f"공시 번호: {section['accession']}", "",
            "| 항목 | 값 | 단위 | 기간 | 확인 상태 |", "|---|---:|---|---|---|"]
        for name, metric in section["metrics"].items():
            basis = {"quarter": "해당 분기", "annual": "연간", "cumulative_ytd": "연초부터 누적", "instant": "결산일 현재"}.get(metric.get("basis"), "확인 필요")
            lines.append(f"| {names[name]} | {metric['value'] if metric['value'] is not None else '보류'} | {metric['unit']} | {basis} | {states.get(metric['status'], '확인 필요')} |")
        if section["amendment_review_pending"]:
            lines += ["", "수정 공시의 적용 범위를 검토하기 전까지 원본·수정본을 합치거나 파생 재무 비율을 확정하지 않습니다."]
        lines += [""]
    lines += ["## 원문과 한계", "", "USD는 미국 달러 원 단위, USD/shares는 주당 달러입니다. 누락은 0을 뜻하지 않습니다. 기업 고유 회계 항목, IFRS 및 다른 통화는 현재 자동 해석하지 않습니다.", ""]
    missing = snapshot["coverage"].get("missing_base_forms", [])
    if missing:
        lines += ["보관 목록에서 확인하지 못한 기본 공시: " + ", ".join(missing) + ". 다른 CIK의 전신·후신 기업 공시를 임의로 합치지 않습니다.", ""]
    for sid, item in snapshot["sources"].items():
        lines += [f"- [{item['kind']}]({item['source_url']}) · `{sid}` · 원본 해시 `{item['raw_sha256']}`"]
    return "\n".join(lines) + "\n"


def write_context(snapshot, root=None):
    part = "illustrative" if snapshot["is_illustrative"] else "real"
    folder = Path(root or ROOT / "reports/private/sec_context") / part / snapshot["cik"] / uuid.uuid4().hex
    folder.mkdir(parents=True, exist_ok=False)
    path = folder / "REPORT.ko.md"
    path.write_text(render_context(snapshot), encoding="utf-8")
    return path
