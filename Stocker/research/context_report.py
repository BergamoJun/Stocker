"""Readable personal BE evidence packet and immutable forward record."""

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import jsonschema

from . import context_journal, event_review, evidence_context, fetch_context, trade_readiness
from .learned_research import latest_training_snapshot
from .report_language import escaped, percent, screen_explanation
from .sec_report import ROOT
from .swing_models import load_price_snapshot
from .validation import digest, now, save_new

CONTEXT_CODE_FILES = ("context_report.py", "evidence_context.py", "context_journal.py", "report_language.py", "fetch_context.py", "massive_news.py", "official_news.py", "event_review.py")


def latest_optional(folder, filename):
    found = sorted((ROOT / folder).glob(f"*/{filename}"), key=lambda path: path.parent.name)
    return found[-1] if found else None


def read_optional(path):
    return json.loads(Path(path).read_text(encoding="utf-8")) if path else None


def load_news_context(gdelt_path, company_path, official_path, cutoff, settings):
    valid, failures = [None, None, None], []
    for index, (provider, path) in enumerate(zip(("gdelt_project", "massive", "official_primary_feeds"), (gdelt_path, company_path, official_path))):
        try:
            trial = [None, None, None]
            trial[index] = read_optional(path)
            evidence_context.news_context(trial[0], trial[1], cutoff, settings, official=trial[2])
            valid[index] = trial[index]
        except (ValueError, OSError, KeyError, TypeError, jsonschema.ValidationError):
            failures.append(provider)
    result = evidence_context.news_context(valid[0], valid[1], cutoff, settings, official=valid[2])
    if failures:
        result["validation_reason"] = "Invalid or future news metadata excluded by source; valid sources retained"
        result["invalid_input_providers"] = failures
    return result


def validate(value):
    schema = json.loads((ROOT / "schemas/evidence_report.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(value)
    news = value["context"]["news"]
    if news["score_component"] is not None or any(row["score_eligible"] or row["impact_reviewed"] for row in news["articles"]):
        raise ValueError("Unreviewed discovery links cannot become scored events")
    review = value["context"].get("news_review")
    if review and (review["released_score"] is not None or review["public_component"] is not None or review["external_llm_calls"] != 0):
        raise ValueError("News research cannot release a recommendation or invoke an external LLM")
    if review and review["experimental_component"] is not None and (not news["all_topics_discovered"] or review["pending_article_ids"] or review["reason_codes"]):
        raise ValueError("Incomplete discovery or reviews cannot yield a news total")


def kst(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y년 %m월 %d일 %H:%M")


def markdown(result):
    context, journal = result["context"], result["journal"]
    screen = result["price_readiness"].get("screen")
    lines = ["# BE 투자 검토 준비 보고서", "", "## 지금 알 수 있는 결론", "", screen_explanation(screen),
        "가격 계산 외에 회사 실적, 시장·업종 흐름, 최근 뉴스의 확인 수준을 함께 보여드립니다. 아직 검증된 매수·매도 추천이나 투자 매력도 점수를 제공하는 단계는 아닙니다.",
        "", f"작성 시각: {kst(result['as_of'])} (한국 시간)", "",
        "## 어떤 내용을 평가했나요?", "",
        "| 평가 항목 | 확인하려는 질문 | 현재 확인 수준 |", "|---|---|---|"]
    lines.append("| 가격·비용·위험 | 비용을 빼고도 예상 이익이 위험에 비해 충분한가? | " + ("수치 조건 미충족" if screen and screen["screen_decision"] == "stand_aside" else "수치 조건만 충족, 실제 성능 검증 필요" if screen and screen["screen_decision"] == "numerical_candidate" else "계산 자료 확인 필요") + " |")
    lines.append("| 회사 실적 | 매출·본업 이익·투자 후 현금흐름은 어떠한가? | " + ("저장된 공시 수치 연결" if context["financial"]["status"] == "reviewed_saved_facts" else "자료 확인 필요") + " |")
    for role, label in (("market", "시장"), ("sector", "청정에너지 업종")):
        card = next(row for row in context["price_context"] if row["role"] == role)
        lines.append(f"| {label} 흐름 | 관련 기업 바구니의 가격 흐름은 어떠한가? | " + ("최근 종가 자료 연결" if card["status"] == "current_price_context" else "자료 확인 필요") + " |")
    lines.append("| 최신 뉴스 | 새 사건이 사업과 가격에 어떤 영향을 주는가? | 원문 링크 확인과 실제 영향 검토를 구분해 진행 중 |")
    lines += ["", "## 1. 가격 계산에서 왜 보류됐나요?", ""]
    if screen:
        from .report_language import REASONS
        lines += ["- " + REASONS.get(code, "추가 계산 확인이 필요합니다.") for code in screen["reason_codes"]] or ["검사한 수치 조건은 충족했지만, 실제 시장에서 같은 결과를 내는지는 아직 확인되지 않았습니다."]
        lines += ["", f"가격 기준 거래일은 {screen['reference_session']}입니다. 매수와 매도 각각 0.25%의 비용을 가정해 검사했습니다. 자세한 계산과 용어 설명은 같은 폴더의 가격·비용 보고서를 참조하세요.",
            "", "[가격·비용 점검 상세](price_readiness/" + Path(result["price_readiness_path"]).parent.name + "/trade_readiness.md)"]
    else:
        lines.append("최신 가격 입력을 확인하지 못해 계산을 보류했습니다. 확인하지 못한 값을 0점이나 중립으로 채우지 않았습니다.")
    lines += ["", "## 2. 회사의 사업 실적은 어떤가요?", ""]
    financial = context["financial"]
    if financial["status"] == "reviewed_saved_facts":
        for card in financial["cards"]:
            if card["value"] is None:
                answer = "확인되지 않음"
            elif card["unit"] == "%":
                answer = percent(card["value"])
            else:
                answer = f"{Decimal(card['value']):,.2f} 백만 달러"
            lines += [f"- **{card['question']}** {answer} / 측정 기간: {escaped(card.get('period_ko', card['period']))}", "  - " + card["meaning"]]
        lines += ["", financial["limitation"], "매출 성장이나 흑자 자체가 다음 주 주가 상승을 보장하지는 않습니다.", ""]
        for source in financial["sources"]:
            lines.append(f"- 공시 원문: [{escaped(source['title'])}](<{source['url']}>) — 공개 시각 {kst(source['published_at'])} (한국 시간)")
    else:
        lines.append("저장된 공시 자료를 확인하지 못했습니다. 회사 실적이 나쁘다고 판정한 것은 아닙니다.")
    lines += ["", "## 3. 시장과 업종의 흐름은 어떤가요?", "", "ETF는 여러 종목을 담아 거래소에서 사고팔 수 있는 상품입니다. 아래 ETF의 가격을 주변 환경을 살펴보는 참고 자료로 사용했습니다."]
    for card in context["price_context"]:
        if card["status"] != "current_price_context":
            lines += ["", f"- {card['symbol']}: 현재 자료를 확인하지 못했습니다."]
            continue
        direction = {"above": "높습니다", "below": "낮습니다", "equal": "같습니다"}[card["close_vs_twenty_session_mean"]]
        lines += ["", f"- **{card['symbol']}: {card['explanation']}**", f"  - 최근 종가는 최근 20거래일(약 한 달) 평균보다 {direction}. 최근 5거래일 가격 변화는 {percent(card['five_session_price_change_pct'])}입니다.",
            f"  - 가격 기준일: {card['reference_session']}. {card['limitation']}", f"  - [ETF 운용사 설명](<{card['evidence_url']}>)"]
    lines += ["", "이 흐름은 배당을 포함한 총수익률이나 앞으로 오를 확률이 아닙니다. 주변 시장 환경을 보여주는 관측값입니다.",
        "", "## 4. 최근 뉴스는 어디까지 확인됐나요?", "",
        "아래는 추가 검토할 기사 목록입니다. 제목만으로 중요 뉴스나 호재·악재로 확정하지 않았고 투자 추천 점수에 가산하지 않았습니다."]
    news = context["news"]
    by_id = {row["id"]: row for row in news["articles"]}
    printed = set()
    for topic in news["topics"]:
        lines += ["", f"### {topic['name']}"]
        if topic["discovery_status"] == "not_verified":
            lines.append("조회가 실패했거나 최신 확인이 부족합니다. 관련 위험이 없다는 뜻은 아닙니다.")
            if topic["highlight_ids"]:
                lines.append("아래 공식 발표 등 보완 자료를 찾았지만, 이 주제의 일반 뉴스 검색을 완료한 것은 아닙니다.")
        elif not topic["highlight_ids"]:
            lines.append("이번 제한된 검색에서 링크를 찾지 못했습니다. 해당 주제의 뉴스가 전혀 없다는 뜻은 아닙니다.")
        for key in topic["highlight_ids"]:
            item = by_id[key]
            if item["url"] in printed:
                lines.append("위의 다른 항목에도 나온 같은 기사이므로 새로운 근거로 중복 계산하지 않습니다.")
                continue
            printed.add(item["url"])
            stamp = item["provider_published_at"] or item["first_seen_at"]
            stamp_label = "공급자가 표기한 발행 시각" if item["provider_published_at"] else "뉴스 목록에서 발견된 시각"
            lines += [f"- **[{escaped(item['title'])}](<{item['url']}>)**", f"  - {escaped(item['publisher'])} / {stamp_label}: {kst(stamp)} (한국 시간)",
                "  - 원문의 실제 발행·사건 시각과 사업 영향은 아직 별도로 검토하지 않았습니다."]
    review = context.get("news_review")
    if review:
        lines += ["", f"원문·영향 검토를 입력한 기사: {review['reviewed_article_count']}건. 검토 대기 기사: {len(review['pending_article_ids'])}건.",
            "원문 발행 시각, BE에 미칠 영향, 약 5거래일 안의 관련성, 새로운 사실인지를 확인해야 합니다. 검색하지 못한 주제가 있거나 검토가 남으면 뉴스 전체 점수를 계산하지 않습니다."]
        if review["experimental_component"] is not None:
            lines.append(f"연구용 뉴스 부분 점수: {review['experimental_component']}/40. 검증된 투자 매력도나 상승 확률이 아닙니다.")
        else:
            lines.append("현재 뉴스 부분 점수: 계산 보류. 확인하지 못한 영향을 0점이나 중립으로 채우지 않았습니다.")
    lines += ["", "공식 보완 자료는 미국의 통화정책·에너지 발표에 한정됩니다. 세계 정치 위험이나 모든 업종 뉴스를 확인한 자료가 아닙니다.",
        "뉴스 API는 지연·누락이 있을 수 있고 세계 뉴스를 빠짐없이 확인하는 서비스가 아닙니다. 실패한 주제를 중립이나 안전으로 판정하지 않았습니다.",
        "", "## 5. 실제로 예측이 맞았는지는 언제 알 수 있나요?", ""]
    if journal["status"] in {"prospective_context_record", "already_recorded"}:
        record = json.loads((Path(journal["path"]) / "forecast.json").read_text(encoding="utf-8"))
        lines += [f"가격·비용 판단과 그때 확인한 근거를 거래 시작 전에 저장했습니다. 관찰 기간은 {record['sessions'][0]}부터 {record['sessions'][-1]}까지 5거래일입니다.",
            f"처음 기록한 시각은 {kst(record['recorded_at'])} (한국 시간)입니다. 보고서를 다시 작성해도 검증에 사용할 첫 기록은 덮어쓰지 않습니다.",
            f"가장 빠른 결과 자료 조회 예상 시각은 {kst(record['earliest_basic_outcome_check_at'])} (한국 시간)입니다. 제공자 지연이 있으면 더 늦어집니다.",
            "검증에는 처음 저장한 예측을 사용합니다. 나중에 나온 뉴스나 가격으로 과거 판단을 고치지 않습니다."]
    else:
        lines.append("다음 거래 시작 전 저장 원칙을 충족하는 유효한 계산이 있어야 새 검증 기록을 만들 수 있습니다.")
    evaluated = sum(row["status"] in {"observed_context_outcome", "already_evaluated"} for row in result["outcomes"]["context_records"])
    lines += [f"현재 이 연결 단계에서 결과까지 확인한 실제 기록: {evaluated}건. 아직 관찰 기간이 끝나지 않은 기록은 성공·실패로 판정하지 않습니다.",
        "", "## 다음에 확인할 일", "", "- 뉴스 원문과 사건 영향을 검토하고, 확인하지 못한 주제를 보완합니다.",
        "- 5거래일이 지난 뒤 새 가격 자료를 받아 처음 기록한 계산과 실제 결과를 비교합니다.",
        "- 실제 예측 성능 검증과 전체 정보 검토가 끝나야 정식 목표가·매력도 점수·수익 실현 시기 제공을 판단할 수 있습니다.", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Personal BE price, SEC, ETF and news context with a readable report")
    parser.add_argument("--gdelt-news", type=Path)
    parser.add_argument("--company-news", type=Path)
    parser.add_argument("--official-news", type=Path)
    parser.add_argument("--event-reviews", type=Path)
    parser.add_argument("--sec-snapshot", type=Path, default=ROOT / "research_inputs/BE/2026-09-30.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports/private/context_report")
    args = parser.parse_args(argv)
    if not args.output_dir.resolve().is_relative_to(ROOT / "reports/private"):
        parser.error("Private report directory required")
    destination = args.output_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination.mkdir(parents=True, exist_ok=False)
    trade_readiness.main(["--output-dir", str(destination / "price_readiness")])
    price_path = next((destination / "price_readiness").glob("*/trade_readiness.json"))
    readiness = json.loads(price_path.read_text(encoding="utf-8"))
    cutoff, settings = now(), fetch_context.policy()
    context = {"as_of": cutoff, "policy_id": settings["id"], "is_illustrative": False, "financial": {"status": "unavailable"}, "price_context": []}
    try:
        context["financial"] = evidence_context.financial(args.sec_snapshot, cutoff)
    except (ValueError, OSError, KeyError) as error:
        context["financial"]["reason"] = str(error)
    reference = (readiness.get("screen") or {}).get("reference_session")
    for profile in settings["proxies"]:
        try:
            prices, entitlement = fetch_context.latest(profile["symbol"])
            card = evidence_context.price_context(profile, prices, entitlement, reference, cutoff)
        except (ValueError, OSError, KeyError) as error:
            card = {"status": "unavailable", "role": profile["role"], "symbol": profile["symbol"], "reason": str(error)}
        context["price_context"].append(card)
    gdelt_path = args.gdelt_news or latest_optional("reports/private/context_news", "news_metadata.json")
    company_path = args.company_news or latest_optional("reports/private/context_news_massive", "news_metadata.json")
    official_path = args.official_news or latest_optional("reports/private/official_news", "news_metadata.json")
    context["news"] = load_news_context(gdelt_path, company_path, official_path, cutoff, settings)
    try:
        reviews = read_optional(args.event_reviews)
    except (ValueError, OSError):
        reviews = {}
    context["news_review"] = event_review.assess(context["news"], reviews, cutoff)
    evidence_path = destination / "evidence_context.json"
    save_new(evidence_path, context)
    save_new(destination / "news_review_queue.json", event_review.queue(context["news"]))
    save_new(destination / "review_input.template.json", event_review.template(context["news"]))
    journal, outcomes = {"status": "withheld"}, {"base_forecasts": [], "context_records": []}
    if readiness.get("forecast") is not None:
        prices, entitlement = latest_training_snapshot()
        record = json.loads(entitlement.read_text(encoding="utf-8"))
        data, bars, calendar = load_price_snapshot(prices, record, cutoff)
        journal = context_journal.register(readiness["forecast"], readiness["screen"], data, bars, calendar, cutoff, evidence_path)
        outcomes = context_journal.settle(data, bars, calendar, cutoff)
    result = {"schema_version": "1.0.0", "status": "research_only_unvalidated", "as_of": cutoff, "symbol": "BE",
        "is_illustrative": False, "context": context, "price_readiness": readiness, "price_readiness_path": str(price_path.resolve()),
        "journal": journal, "outcomes": outcomes, "released_score": None, "released_price_plan": None,
        "released_expected_target_sessions": None, "validation_id": None, "external_llm_calls": 0, "llm_tokens": 0,
        "full_recommendation_ready": False, "real_ml_fit_performed": False,
        "registration": {"policy_sha256": digest(ROOT / "config/context_policy.json"), "evidence_context_sha256": digest(evidence_path),
            "news_review_policy_sha256": digest(ROOT / "config/news_review_policy.json"), "official_news_entitlements_sha256": digest(ROOT / "config/official_news_entitlements.json"),
            "sec_snapshot_sha256": digest(args.sec_snapshot) if args.sec_snapshot.exists() else None, "price_readiness_sha256": digest(price_path),
            "news_sha256": {str(path): digest(path) if path.exists() else None for path in (gdelt_path, company_path, official_path, args.event_reviews) if path},
            "code_sha256": {name: digest(ROOT / "research" / name) for name in CONTEXT_CODE_FILES}}}
    validate(result)
    save_new(destination / "integrated_readiness.json", result)
    (destination / "report.md").write_text(markdown(result), encoding="utf-8")
    print(json.dumps({"status": result["status"], "output_dir": str(destination.resolve()),
        "financial_context": context["financial"]["status"], "price_context_available": sum(card["status"] == "current_price_context" for card in context["price_context"]),
        "fresh_news_links": sum(row["fresh_metadata"] for row in context["news"]["articles"]),
        "all_news_topics_confirmed": context["news"]["all_topics_discovered"], "journal_status": journal["status"],
        "observed_outcomes": sum(row["status"] in {"observed_context_outcome", "already_evaluated"} for row in outcomes["context_records"]), "released_score": None}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
