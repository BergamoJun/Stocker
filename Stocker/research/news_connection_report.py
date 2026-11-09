"""Local Korean metadata/reviewer explanation, never a publisher body reader."""

from html import escape
from urllib.parse import quote
from zoneinfo import ZoneInfo

from .rights import as_utc

STATUS = {"archive_unavailable_at_cutoff": "당시 보관 버전 확인 불가", "conflicting_reviews": "검토자 판단 충돌",
    "review_linked": "같은 기사 버전의 검토 연결", "irrelevant_reviewed": "관련 없음으로 검토",
    "review_needs_update": "버전·시각 확인 또는 재검토 필요", "awaiting_review": "원문·영향 검토 대기"}
REASONS = {"ARTICLE_NOT_ARCHIVED_AT_CUTOFF": "기준 시각까지 보관된 기사 버전 없음", "ARTICLE_VERSION_CHANGED": "보관된 메타데이터 버전이 다름",
    "ARTICLE_REVERTED_REVIEW_REQUIRED": "중간에 다른 버전이 나타나 재검토 필요", "ARCHIVE_ORDER_NOT_PRIOR_TO_CUTOFF": "같은 초의 기록 순서를 확인하지 못함",
    "ARTICLE_NOT_IN_FROZEN_CONTEXT_VERSION": "검토가 인용한 모든 자료가 기존 예측의 같은 버전에 포함되지 않음",
    "REVIEW_EXPIRED": "검토 또는 원문 확인 후 24시간 경과", "REVIEW_AFTER_CUTOFF": "검토가 기준 시각 이후",
    "REVIEWER_DISAGREEMENT": "여러 검토자의 방향·영향·신뢰 판단이 일치하지 않음"}


def text(value):
    return escape(str(value or "미기록")).replace("|", "\\|").replace("[", "\\[").replace("]", "\\]").replace("\r", " ").replace("\n", " ")


def link(url, label="원문"):
    return f"[{label}]({quote(url, safe=':/?=&%#~-_')})"


def kst(value):
    return as_utc(value).astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S KST")


def markdown(value):
    lines = ["# 뉴스 기사·검토·예측의 연결 결과", "", f"자료 기준 시각: {kst(value['as_of'])}", f"연결 보고서 생성: {kst(value['generated_at'])}", "",
        value["data_label"] or "개인용 로컬 자료 연결 기록", "",
        "기사의 주소·보관 버전·검토 저장 시각을 맞춰 어떤 검토를 참고할 수 있었는지 설명합니다. 기사 본문을 내려받거나 자동으로 의미를 판독하는 기능은 아닙니다.", ""]
    if value["status"] == "frozen_forecast_news_availability_audit":
        lines += [f"연결 대상 기존 예측: `{value['forecast']['record_id']}`", "",
            "이 문서는 기존 예측의 자료 기준 시각을 바꾸지 않은 사후 가용성 점검입니다. 원래 가격·비용 판단이나 주문 행동을 바꾸지 않습니다.",
            "당시 연결 가능한 검토가 있었다는 사실과 원래 예측에 뉴스가 실제 사용됐다는 주장은 다릅니다. 기존 예측은 가격·비용만의 기록입니다.", ""]
        if value["forecast"]["cutoff_source"] == "pinned_legacy_context_as_of":
            lines += ["구버전 예측의 기준 시각은 예약 작업의 원본 해시와 일치하는 근거 파일에 선언된 시각으로 확인했습니다. 현재 시각이나 임의의 대체 시각으로 채우지 않았습니다.", ""]
    lines += ["## 주요 검토 사건과 원문", ""]
    highlighted = [event for event in value["events"] if event["highlight"]]
    if not highlighted:
        lines += ["현재 기준에서 버전·시각·신선도·검토 조건을 충족한 주요 사건 강조 항목은 없습니다. 위험이나 악재가 없다는 뜻은 아닙니다.", ""]
    for event in highlighted:
        opinions = [opinion for opinion in event["opinions"] if opinion["status"] == "review_linked"]
        opinion = opinions[0]
        direction = "긍정 영향으로 검토" if opinion["direction"] == 1 else "부정 영향으로 검토"
        lines += [f"### {text(event['event_id'])} — {direction}", "",
            "원문: " + " · ".join(link(url, f"원문 {index}") for index, url in enumerate(opinion["original_urls"], 1)), "",
            f"- 검토 근거: {text(opinion['rationale'])}", f"- 약 5거래일과의 관련성: {text(opinion['five_session_relevance_explanation'])}",
            f"- 반론: {text(opinion['counterargument'])}", f"- 이후 확인할 사건: {text(opinion['monitoring_event'])}",
            f"- 검토자: {text(opinion['reviewer_id'])}; 검토 저장 시각: {kst(opinion['review_available_at'])}", ""]
    lines += ["## 기사별 연결 상태", "", "| 보관된 기사 제목 | 원문 | 연결 상태 | 확인할 내용 |", "|---|---|---|---|"]
    for article in value["articles"]:
        reasons = "; ".join(REASONS.get(code, "추가 확인 필요") for code in article["reason_codes"])
        related = [event for event in value["events"] if article["article_key"] in event["article_keys"]]
        if not reasons:
            reasons = "; ".join(sorted({REASONS.get(code, "추가 확인 필요") for event in related for code in event["reason_codes"]}))
        if article["last_source_observed_at"] and not article["metadata_fresh_at_cutoff"]:
            reasons = "; ".join(filter(None, (reasons, "최근 자료 확인이 오래됨: " + kst(article["last_source_observed_at"]))))
        lines.append(f"| {text(article['metadata']['title'])} | {link(article['metadata']['url'])} | {STATUS[article['status']]} | {text(reasons or '연결 시점·본문 변경 여부는 별도 확인')} |")
    lines += ["", "제목·URL·출처·공급자가 표시한 발행 시각은 기사 메타데이터입니다. 실제 원문 발행 시각을 확인했다는 검토와 구별합니다.", "",
        "## 재사용과 보류의 기준", "",
        "- 같은 주소라도 제목·출처·발행 표기가 달라지면 다른 버전입니다. A→B→A로 돌아온 경우도 새 확인 구간으로 보아 이전 검토를 자동 부활시키지 않습니다.",
        "- 검토의 최초 저장 시각을 사용합니다. 사람이 입력한 과거 검토일이나 기사 발행일로 저장 시각을 소급하지 않습니다. 서로 다른 저장소의 같은 초 기록은 앞뒤 순서가 불확실해 보류합니다.",
        "- 같은 검토자가 같은 사건의 새 판단을 저장하면 이후 조회에 적용합니다. 서로 다른 검토자가 방향·영향 등을 다르게 판단하면 충돌 상태로 남겨 강조를 보류합니다.",
        "- 한 사건이 여러 기사를 인용하면 모두 같은 버전으로 확인돼야 합니다. 예측과 연결할 때는 모두 원래 보관된 예측의 자료에도 포함돼야 합니다.",
        "- 검토나 원문 확인 후 24시간이 지나면 재검토가 필요합니다. 메타데이터를 새로 조회했다고 오래된 검토 시각을 새로 만들지 않습니다.", "",
        "## 남아 있는 확인 사항", "",
        "기업·업종·거시경제/정치·원자재의 검색 범위가 충분했는지와 자료의 최신성은 별도입니다. 일부 링크나 공식 피드가 있다고 전체 주제를 확인한 것으로 바꾸지 않습니다.",
        "검토 내용은 사람이 확인했다고 입력한 주장입니다. 이 프로그램이 원문을 읽고 사실성을 독립 검증한 결과는 아닙니다. 메타데이터가 같아도 원문 본문이 바뀌었는지는 알 수 없습니다.",
        "기사와 사건의 의미상 중복 여부는 검토자가 사건 ID를 일관되게 부여해야 합니다. 이 연결 기능만으로 주가 예측력이 개선됐다고 판단하지 않습니다.",
        "기사 버전별 검토는 설명용으로 재사용합니다. 기존 전체 뉴스 묶음에 연결된 점수 검사를 새 묶음으로 자동 바꾸지 않습니다. 전체 뉴스 점수·추천 점수·상승 확률은 비워 둡니다.",
        "추가 API 요청·외부 AI 호출·모델 학습은 0회입니다. 이전 기사·검토·예측 파일을 덮어쓰지 않습니다.", ""]
    if not value["coverage_current_at_cutoff"]:
        lines += ["현재 조회는 저장 자료를 읽었습니다. 기준 시각 부근의 새 뉴스 수집이 확인되지 않아 최신 뉴스 전체를 확인한 상태가 아닙니다.", ""]
    return "\n".join(lines)
