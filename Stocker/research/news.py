"""GDELT metadata discovery and reviewed-event research signals.

Article bodies/images are never fetched. An indexing timestamp is never
substituted for an article's original publication time or a material update.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

import jsonschema

from .rights import as_utc, require_entitlement
from .policy import candidate_policy
from .news_transport import request_metadata

ATTRIBUTION = "Data from the GDELT Project (https://www.gdeltproject.org/)."
TOPICS = {
    "company": '"Bloom Energy"',
    "sector": '("fuel cell" OR "data center power" OR "distributed generation")',
    "macro_politics": '("Federal Reserve" OR "energy policy" OR sanctions OR tariffs)',
    "commodities": '("natural gas" OR "copper price" OR "oil price")',
}
IMPACT = {"low": Decimal("0.25"), "medium": Decimal("0.5"), "high": Decimal(1)}
RELEVANCE = {"direct_company": Decimal(1), "sector": Decimal("0.75"), "macro": Decimal("0.5")}
RELIABILITY = {"primary": Decimal(1), "independently_confirmed": Decimal("0.8"),
               "reputable_single": Decimal("0.6"), "unverified": Decimal(0)}


def canonical_url(value):
    if not isinstance(value, str) or any(ord(char) < 33 or char in '<>"`\\' for char in value):
        raise ValueError("Unsafe characters in article URL")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Article link must be a public HTTP(S) URL")
    host = parsed.hostname.lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal")):
        raise ValueError("Private article host")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("Private article address")
    query = [(key, item) for key, item in parse_qsl(parsed.query, keep_blank_values=True)
             if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}]
    return urlunparse((parsed.scheme, parsed.netloc.lower(), parsed.path or "/", "", urlencode(sorted(query)), ""))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Unexpected API redirect; no arbitrary destinations are fetched")


def gdelt_request(query):
    url = "https://api.gdeltproject.org/api/v2/doc/doc?" + urlencode({
        "query": query, "mode": "artlist", "format": "json", "timespan": "72h",
        "maxrecords": 25, "sort": "datedesc",
    })
    request = Request(url, headers={"User-Agent": "EquityResearchLab/0.3 metadata-only"})
    return request_metadata(request, build_opener(NoRedirect))


def normalize_articles(payload, category, retrieved_at):
    if not isinstance(payload, dict) or not isinstance(payload.get("articles"), list):
        raise ValueError("GDELT did not return an article-list JSON object")
    rows, warnings = [], []
    for item in payload["articles"][:25]:
        try:
            url = canonical_url(item["url"])
            observed = datetime.strptime(item["seendate"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            if observed > as_utc(retrieved_at):
                raise ValueError("Future indexing timestamp")
            title = str(item["title"]).replace("\r", " ").replace("\n", " ")[:300]
            if not title.strip():
                raise ValueError("Empty title")
            rows.append({
                "id": "gdelt_" + hashlib.sha256(url.encode()).hexdigest()[:16],
                "url": url, "title": title, "publisher": urlparse(url).hostname,
                "category": category, "first_seen_at": observed.isoformat().replace("+00:00", "Z"),
                "retrieved_at": retrieved_at, "published_at": None, "event_at": None,
                "impact_direction": "unassessed", "assessment_status": "needs_review",
                "score_eligible": False,
                "limitation": "GDELT seendate is an observation time, not verified publication/event time.",
            })
        except (ValueError, TypeError, KeyError):
            warnings.append("Dropped an article with invalid URL/title/index timestamp")
    return rows, warnings


def collect_news(entitlement, *, topics=None, request=gdelt_request, sleep=time.sleep, now=None):
    now = now or (lambda: datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"))
    start = now()
    require_entitlement(entitlement, ["ingest_metadata", "store_metadata", "display_metadata_links"], start)
    selected = topics or TOPICS
    if not selected or len(selected) > 4 or not set(selected) <= set(TOPICS):
        raise ValueError("Only up to four configured topic queries are allowed")
    articles, warnings, failures, completed = {}, [], [], []
    for index, category in enumerate(selected):
        if index:
            sleep(5)
        try:
            payload = request(TOPICS[category])
            retrieved = now()
            normalized, dropped = normalize_articles(payload, category, retrieved)
            warnings.extend(dropped)
            if dropped:
                failures.append({"category": category, "reason": "Invalid article records dropped; coverage is partial, not empty/complete"})
            for row in normalized:
                # Keep topic coverage without treating the same URL as new evidence.
                if row["id"] not in articles:
                    articles[row["id"]] = {**row, "categories": [category]}
                elif category not in articles[row["id"]]["categories"]:
                    articles[row["id"]]["categories"].append(category)
            completed.append(category)
        except (OSError, ValueError, HTTPError, URLError) as error:
            failure_kind = f"HTTP_{error.code}" if isinstance(error, HTTPError) else type(error).__name__
            retry = getattr(error, "retry_at", None)
            failures.append({"category": category,
                             "reason": f"{failure_kind}: request failed/invalid response, not an empty-news result"
                                       + (f"; retry_at={retry}" if retry else "")})
    checked = now()
    result = {
        "version": "1.0.0", "provider": "gdelt_project", "attribution": ATTRIBUTION,
        "entitlement_id": entitlement["id"], "checked_at": checked,
        "status": "available" if not failures else ("partial" if completed else "unavailable"),
        "completed_topics": completed, "failed_topics": failures,
        "coverage": "bounded configured-query results, not exhaustive worldwide coverage",
        "poll_interval_seconds": 900, "provider_delay_seconds": None,
        "realtime_guaranteed": False, "articles": list(articles.values()), "warnings": warnings,
        "score_component": None, "model_status": "unvalidated",
    }
    validate_collection(result)
    return result


def validate_collection(collection):
    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / "schemas/market_context.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(collection)
    cutoff = as_utc(collection["checked_at"])
    known = set()
    for article in collection["articles"]:
        if article["id"] in known:
            raise ValueError("Duplicate news metadata ID")
        known.add(article["id"])
        canonical_url(article["url"])
        if not as_utc(article["first_seen_at"]) <= as_utc(article["retrieved_at"]) <= cutoff:
            raise ValueError("News collection contains future/inconsistent observation timestamps")
    if collection["status"] == "available" and collection["failed_topics"]:
        raise ValueError("Failed query coverage cannot be represented as available")
    if collection["status"] == "available" and not collection["completed_topics"]:
        raise ValueError("Available collection requires at least one completed query")


def reviewed_event_signal(events, collection, as_of):
    """Experimental 0..40 component; never a released attractiveness score."""
    cutoff = as_utc(as_of)
    policy = candidate_policy()
    settings = policy["news"]
    maximum_points = Decimal(policy["attractiveness"]["weights"]["latest_events"])
    neutral_points = maximum_points / 2
    if (collection.get("status") != "available" or set(collection.get("completed_topics", [])) != set(TOPICS) or
            not Decimal(0) <= Decimal(str((cutoff - as_utc(collection["checked_at"])).total_seconds())) <= settings["collection_max_age_minutes"] * 60):
        return {"status": "unavailable", "experimental_component": None, "public_component": None,
                "reason": "News collection missing, partial, future-dated or older than 30 minutes"}
    known_articles = {item["id"]: item for item in collection.get("articles", [])}
    assessed, omitted, covered = {}, [], set()
    for event in events:
        try:
            if event.get("review_status") not in {"reviewed", "irrelevant_reviewed"} or not event.get("review_id") or not event.get("rationale"):
                raise ValueError("Event impact not reviewed")
            if not event.get("source_ids") or not event.get("original_urls"):
                raise ValueError("Event requires source IDs and original links")
            if not set(event["source_ids"]) <= known_articles.keys():
                raise ValueError("Unknown event source IDs")
            cited = [known_articles[key] for key in event["source_ids"]]
            if not {canonical_url(item["url"]) for item in cited} <= {
                    canonical_url(url) for url in event["original_urls"]}:
                raise ValueError("Original links do not match the cited articles")
            if any(as_utc(item[key]) > cutoff for item in cited for key in ("first_seen_at", "retrieved_at")):
                raise ValueError("Cited article was unavailable at the decision cutoff")
            reviewed = as_utc(event["reviewed_at"])
            if not max(as_utc(item["retrieved_at"]) for item in cited) <= reviewed <= cutoff:
                raise ValueError("Relevance/impact review cannot precede actual retrieval or follow as_of")
            if event["review_status"] == "irrelevant_reviewed":
                covered.update(event["source_ids"])
                continue
            for url in event["original_urls"]:
                canonical_url(url)
            moments = {key: as_utc(event[key]) for key in (
                "published_at", "first_public_at", "first_seen_at", "retrieved_at", "reviewed_at")}
            if any(moment > cutoff for moment in moments.values()):
                raise ValueError("Event knowledge/review is after as_of")
            if type(event["direction"]) is not int or event["direction"] not in {-1, 0, 1}:
                raise ValueError("Unresolved mixed direction is not a bullish default")
            if moments["first_public_at"] > moments["published_at"]:
                raise ValueError("Original public knowledge cannot follow this publication")
            if moments["reviewed_at"] < max(moments["retrieved_at"], moments["published_at"]):
                raise ValueError("Impact review cannot precede retrieval/publication")
            if event.get("novelty") not in {"new", "material_update", "recycled"}:
                raise ValueError("Explicit event novelty assessment required")
            if event["novelty"] == "material_update" and not event.get("material_update_at"):
                raise ValueError("Material update needs a verified update timestamp")
            origin = min(moments["published_at"], moments["first_public_at"])
            if event.get("material_update_at") is not None:
                update = as_utc(event["material_update_at"])
                if not event.get("material_change_reviewed") or not origin <= update <= reviewed <= cutoff:
                    raise ValueError("Material update requires a verified new-fact review")
                origin = update
            age = Decimal(str((cutoff - origin).total_seconds())) / 3600
            novelty = Decimal(0) if event.get("novelty") == "recycled" else Decimal(1)
            with localcontext() as context:
                context.prec = 34
                freshness = (Decimal(0) if age > settings["maximum_event_age_hours"] else
                             Decimal(2) ** (-age / settings["half_life_hours"]))
                strength = (Decimal(event["direction"]) * IMPACT[event["impact"]] *
                            RELEVANCE[event["relevance"]] * RELIABILITY[event["reliability"]] *
                            freshness * novelty)
            record = {"event_id": event["event_id"], "strength": strength,
                      "age_hours": age, "original_urls": event["original_urls"],
                      "source_ids": event["source_ids"], "rationale": event["rationale"]}
            previous = assessed.get(event["event_id"])
            if previous and (previous["strength"] > 0 > strength or previous["strength"] < 0 < strength):
                raise ValueError("Conflicting direction inside one event cluster")
            if previous is None or abs(strength) > abs(previous["strength"]):
                assessed[event["event_id"]] = record
            covered.update(event["source_ids"])
        except (ValueError, TypeError, KeyError):
            omitted.append("Unreviewed/incomplete/future/conflicting event: contribution withheld")
    if known_articles.keys() - covered:
        omitted.append("Discovered links still need relevance/impact review; unknown news is not neutral news")
    if omitted:
        return {"status": "unavailable", "experimental_component": None, "public_component": None,
                "reason": "Event assessment incomplete; no silent neutral substitution", "limitations": omitted}
    top = sorted(assessed.values(), key=lambda item: (-abs(item["strength"]), item["event_id"]))[:settings["maximum_scoring_events"]]
    net = max(Decimal(-1), min(Decimal(1), sum((item["strength"] for item in top), Decimal(0))))
    return {"status": "research_only_unvalidated", "public_component": None,
            "experimental_component": str((neutral_points + neutral_points * net).quantize(Decimal("0.000001"))),
            "event_count": len(top), "events": [
                {**item, "strength": str(item["strength"]), "age_hours": str(item["age_hours"])} for item in top],
            "reason": "Candidate policy weights, not a calibrated forecast or investment recommendation"}


def highlights_markdown(collection):
    validate_collection(collection)
    expiry = as_utc(collection["checked_at"]) + timedelta(minutes=candidate_policy()["news"]["collection_max_age_minutes"])
    lines = ["# 최신 뉴스 링크 · 검토 후보", "", f"조회: {collection['checked_at']} / 수집 당시 상태: {collection['status']}",
             f"점수 검토용 재조회 기한: {expiry.isoformat()} (UTC). 이 파일은 정적 스냅샷이며 기한 이후 재조회가 필요하다.",
             "", "발행·사건 시각과 투자 영향은 아직 미확인이다. 아래 정렬은 GDELT 관측시각 기준이며 호재 순위가 아니다.",
             "기사 본문·이미지는 수집하지 않는다. 원문에서 확인한다.", ""]
    ordered = sorted(collection["articles"], key=lambda row: row["first_seen_at"], reverse=True)
    chosen, selected_ids = [], set()
    # Retain company/sector context even when newer broad political stories dominate.
    for category in TOPICS:
        for item in [row for row in ordered if category in row["categories"] and row["id"] not in selected_ids][:2]:
            chosen.append(item)
            selected_ids.add(item["id"])
    for item in ordered:
        if len(chosen) >= 8:
            break
        if item["id"] not in selected_ids:
            chosen.append(item)
            selected_ids.add(item["id"])
    for item in chosen:
        title = item["title"].replace("[", "\\[").replace("]", "\\]").replace("<", "&lt;").replace(">", "&gt;")
        lines.extend([f"- **[{title}](<{item['url']}>)**", f"  - {item['publisher']} / {', '.join(item['categories'])}",
                      f"  - GDELT 관측 {item['first_seen_at']} / 원 발행시각 미확인 / 점수 미반영"])
    if not collection["articles"]:
        lines.append("수집 가능한 링크가 없다. 수집 실패와 뉴스 부재를 동일하게 해석하지 않는다.")
    lines.extend(["", ATTRIBUTION, "", "실시간 무지연 또는 전체 뉴스 커버리지를 보장하지 않는다."])
    return "\n".join(lines) + "\n"
