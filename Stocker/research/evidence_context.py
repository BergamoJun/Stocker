"""Timestamped SEC facts, approved ETF trends and unreviewed news links."""

from decimal import Decimal
import json
from pathlib import Path
from urllib.parse import urlparse

from . import fetch_context, official_news
from .news import canonical_url, validate_collection
from .rights import as_utc, price_uses, require_entitlement
from .sec_report import SourceAdapter, MetricEngine
from .swing_models import load_price_snapshot

TOPIC_NAMES = {"company": "BE 관련 기업 뉴스", "sector": "연료전지·전력 공급 업종 뉴스",
    "macro_politics": "경제·정책·정치 위험 뉴스", "commodities": "가스·원유·구리 등 원자재 뉴스"}
FINANCIAL = {
    "revenue_q3_yoy_pct": ("작년 같은 분기보다 매출이 늘었나요?", "매출 증가율", "매출은 사업에서 벌어들인 판매 금액입니다. 이익과는 다릅니다."),
    "operating_margin_q3_pct": ("본업에서 이익을 남겼나요?", "영업이익률", "영업이익률은 매출 중 본업의 이익이 차지하는 비율입니다."),
    "free_cash_flow_9m_musd": ("사업에서 들어온 현금으로 설비투자를 감당했나요?", "설비투자 후 현금흐름", "영업에서 들어온 현금에서 설비투자를 뺀 값입니다. 분기 매출 증가율과 측정 기간이 다릅니다.")}
FINANCIAL_PERIODS = {
    "revenue_q3_yoy_pct": "2026년 3분기(7~9월)를 2025년 같은 분기와 비교",
    "operating_margin_q3_pct": "2026년 3분기(7~9월), 미국 회계기준",
    "free_cash_flow_9m_musd": "2026년 1월 1일~9월 30일(9개월 누적)"}


def financial(path, as_of):
    snapshot, facts, _ = SourceAdapter.load(Path(path), as_of)
    if snapshot["identity"]["symbol"] != "BE" or snapshot["is_illustrative"]:
        raise ValueError("Reviewed real BE SEC snapshot required")
    if as_utc(snapshot["reviewed_as_of"]) > as_utc(as_of) or any(as_utc(source["retrieved_at"]) > as_utc(as_of) for source in snapshot["sources"]):
        raise ValueError("Saved SEC evidence was observed after the context cutoff")
    metrics, calculations, missing = MetricEngine.calculate(facts)
    exact = {row["metric"]: row for row in calculations}
    by_name = {row["name"]: row for row in metrics}
    cards = []
    for key, (question, label, meaning) in FINANCIAL.items():
        row = by_name[key]
        value = exact[key]["decimal_value"]
        sign = None if value is None else ("positive" if Decimal(value) > 0 else "negative" if Decimal(value) < 0 else "zero")
        cards.append({"metric": key, "question": question, "label": label, "value": value, "unit": row["unit"],
            "period": row["period"], "period_ko": FINANCIAL_PERIODS[key], "sign": sign, "meaning": meaning, "source_ids": row["evidence_ids"]})
    return {"status": "reviewed_saved_facts", "snapshot_reviewed_at": snapshot["reviewed_as_of"],
        "effective_period_end": snapshot["effective_period_end"], "latest_filing_search_performed": False,
        "cards": cards, "sources": snapshot["sources"], "missing": missing,
        "limitation": "저장된 공시에서 선택한 수치를 사용하며, 더 최근 공시가 없는지 자동 확인한 결과는 아닙니다."}


def price_context(profile, prices, entitlement, stock_session, as_of):
    record = json.loads(Path(entitlement).read_text(encoding="utf-8"))
    require_entitlement(record, price_uses("local_research"), as_of, synthetic=False)
    fetch_context.verify(prices, record)
    data, bars, _ = load_price_snapshot(Path(prices), record, as_of)
    if data["symbol"] != profile["symbol"] or data["instrument_type"] != "etf" or bars[-1].session != stock_session:
        raise ValueError("Context proxy identity or reference session differs")
    close = bars[-1].close
    mean20 = sum((row.close for row in bars[-20:]), Decimal(0)) / 20
    difference = (close / bars[-6].close - 1) * 100
    return {"status": "current_price_context", "role": profile["role"], "symbol": profile["symbol"],
        "name": profile["name"], "explanation": profile["explanation_ko"], "limitation": profile["limitation_ko"],
        "reference_session": bars[-1].session, "retrieved_at": data["retrieved_at"], "source_ids": data["source_ids"],
        "five_session_price_change_pct": str(difference), "close_vs_twenty_session_mean": "above" if close > mean20 else "below" if close < mean20 else "equal",
        "comparison_rule": "recent_close_vs_twenty_session_simple_mean", "evidence_url": profile["evidence_url"],
        "total_return_including_dividends": False, "return_forecast": None}


def news_context(gdelt, company, as_of, settings, *, official=None):
    cutoff = as_utc(as_of)
    articles, providers, completed = {}, [], set()
    if gdelt:
        validate_collection(gdelt)
        age = (cutoff - as_utc(gdelt["checked_at"])).total_seconds()
        if age < 0:
            raise ValueError("News metadata is after context cutoff")
        fresh = age <= settings["news_max_age_seconds"]
        providers.append({"provider": "gdelt_project", "status": gdelt["status"], "fresh": fresh,
            "checked_at": gdelt["checked_at"], "failed_topics": gdelt["failed_topics"]})
        failures = {row["category"] for row in gdelt["failed_topics"]}
        if fresh:
            completed.update(set(gdelt["completed_topics"]) - failures)
        for row in gdelt["articles"]:
            url = canonical_url(row["url"])
            articles[url] = {"id": row["id"], "source_ids": [row["id"]], "title": row["title"], "url": url,
                "publisher": row["publisher"], "categories": list(row["categories"]), "retrieved_at": row["retrieved_at"],
                "fresh_categories": list(row["categories"]) if fresh else [],
                "provider_published_at": None, "first_seen_at": row["first_seen_at"], "published_at": None,
                "impact_reviewed": False, "score_eligible": False, "fresh_metadata": fresh, "providers": ["gdelt_project"]}
    if company:
        if company.get("provider") != "massive":
            raise ValueError("Unknown company news provider")
        if company.get("articles") and (company.get("symbol") != "BE" or company.get("query_topics") != ["company"]):
            raise ValueError("Company-news ticker or query scope differs")
        age = (cutoff - as_utc(company["checked_at"])).total_seconds()
        if age < 0:
            raise ValueError("Company-news metadata is after cutoff")
        fresh = age <= settings["news_max_age_seconds"]
        providers.append({"provider": "massive", "status": company["status"], "fresh": fresh,
            "checked_at": company["checked_at"], "failed_topics": []})
        if fresh and company["status"] in {"links_available", "no_links_in_bounded_query"}:
            completed.add("company")
        for row in company["articles"]:
            if row.get("score_eligible") is not False or row.get("published_at") is not None:
                raise ValueError("Company links cannot assert reviewed publication/impact")
            if not as_utc(row["provider_published_at"]) <= as_utc(row["retrieved_at"]) <= as_utc(company["checked_at"]) <= cutoff:
                raise ValueError("Company news observation order invalid")
            url = canonical_url(row["url"])
            if url in articles:
                articles[url]["providers"].append("massive")
                articles[url]["source_ids"].append(row["id"])
                if "company" not in articles[url]["categories"]:
                    articles[url]["categories"].append("company")
                if fresh and "company" not in articles[url]["fresh_categories"]:
                    articles[url]["fresh_categories"].append("company")
                articles[url]["provider_published_at"] = row["provider_published_at"]
                articles[url]["fresh_metadata"] |= fresh
                continue
            articles[url] = {"id": row["id"], "source_ids": [row["id"]], "title": row["title"], "url": url,
                "publisher": row["publisher"], "categories": ["company"], "retrieved_at": row["retrieved_at"],
                "fresh_categories": ["company"] if fresh else [],
                "provider_published_at": row["provider_published_at"], "first_seen_at": None, "published_at": None,
                "impact_reviewed": False, "score_eligible": False, "fresh_metadata": fresh, "providers": ["massive"]}
    if official:
        if official.get("provider") != "official_primary_feeds" or as_utc(official["checked_at"]) > cutoff:
            raise ValueError("Unknown or future official metadata collection")
        source_policy = official_news.policy()
        profiles = {row["id"]: row for row in source_policy["feeds"]}
        feeds = {row["feed_id"]: row for row in official["feeds"]}
        providers.append({"provider": "official_primary_feeds", "status": official["status"], "fresh": (cutoff - as_utc(official["checked_at"])).total_seconds() <= settings["news_max_age_seconds"],
            "checked_at": official["checked_at"], "failed_topics": [], "scope": "supplementary_only_does_not_clear_broad_queries",
            "feeds": [{key: row[key] for key in ("feed_id", "status", "checked_at", "scope_ko")} for row in official["feeds"]]})
        for row in official["articles"]:
            feed, profile = feeds[row["feed_id"]], profiles[row["feed_id"]]
            url = canonical_url(row["url"])
            if urlparse(url).hostname != profile["article_host"] or row["feed_url"] != profile["url"] or set(row["categories"]) != set(profile["categories"]):
                raise ValueError("Official article identity or source scope differs")
            if row.get("score_eligible") is not False or row.get("published_at") is not None:
                raise ValueError("Official feed metadata is not an original-source review")
            if not as_utc(row["provider_published_at"]) <= as_utc(row["retrieved_at"]) <= as_utc(feed["checked_at"]) <= as_utc(official["checked_at"]) <= cutoff:
                raise ValueError("Official observation timestamps are inconsistent")
            age = (cutoff - as_utc(row["retrieved_at"])).total_seconds()
            event_age = (cutoff - as_utc(row["provider_published_at"])).total_seconds() / 3600
            fresh = age <= settings["news_max_age_seconds"] and event_age <= source_policy["lookback_hours"] and feed["status"] in {"links_available", "partial"}
            if url in articles:
                articles[url]["providers"].append(profile["provider"])
                articles[url]["source_ids"].append(row["id"])
                articles[url]["categories"] = sorted(set(articles[url]["categories"]) | set(row["categories"]))
                if fresh:
                    articles[url]["fresh_categories"] = sorted(set(articles[url]["fresh_categories"]) | set(row["categories"]))
                articles[url]["fresh_metadata"] |= fresh
                continue
            articles[url] = {"id": row["id"], "source_ids": [row["id"]], "title": row["title"], "url": url,
                "publisher": row["publisher"], "categories": row["categories"], "fresh_categories": row["categories"] if fresh else [],
                "retrieved_at": row["retrieved_at"], "provider_published_at": row["provider_published_at"], "first_seen_at": None,
                "published_at": None, "impact_reviewed": False, "score_eligible": False, "fresh_metadata": fresh,
                "providers": [profile["provider"]], "coverage_scope": "supplementary_official_source_only"}
    topics = []
    for key in settings["news_topics"]:
        links = [row for row in articles.values() if key in row["fresh_categories"]]
        topics.append({"id": key, "name": TOPIC_NAMES[key], "discovery_status": "query_completed_links_unreviewed" if key in completed else "not_verified",
            "fresh_link_count": len(links), "highlight_ids": [row["id"] for row in links[:settings["maximum_links_per_topic"]]], "impact_assessment": "not_reviewed"})
    return {"status": "links_awaiting_review" if any(row["fresh_metadata"] for row in articles.values()) else "unavailable_or_stale",
        "providers": providers, "topics": topics, "articles": list(articles.values()),
        "all_topics_discovered": set(settings["news_topics"]) <= completed, "all_impacts_reviewed": False,
        "score_component": None, "publisher_full_text_fetched": False,
        "limitation": "조회 실패·기사 미발견·영향 미검토는 서로 다르며, 확인하지 못한 위험을 중립으로 평가하지 않습니다."}
