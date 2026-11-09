"""Bounded multi-issuer news refresh, immutable archives and manual-review queues."""

import argparse
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import time
import uuid

from . import issuer_profiles as profiles, issuer_news, project_compatibility as guard, official_news, news, news_archive
from .context_transport import Client
from .learned_research import latest_training_snapshot
from .local_files import atomic_json
from .news_connection_report import text, link
from .panel_transport import Deferred, SourceUnavailable
from .news_transport import NewsRequestDeferred
from .rights import as_utc, RightsError
from .sec_refresh import read
from .sec_report import ROOT, SYNTHETIC_LABEL
from .sec_transport import now
from .validation import digest, stable_hash


FILES = ["research/news_refresh.py", "research/context_transport.py", "research/issuer_news.py", "research/issuer_profiles.py",
    "research/project_compatibility.py", "config/news_refresh_policy.json", "config/issuer_profiles.json", "schemas/issuer_context.schema.json",
    "research/news_transport.py", "research/official_news.py", "config/official_news_entitlements.json", "config/source_entitlements.json"]


def methods():
    return {**issuer_news.methods(), **{name: digest(ROOT / name) for name in FILES}}


def normalize_massive(raw, profile, start, end, observed):
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("status") != "OK" or not isinstance(payload.get("results"), list):
        raise ValueError("INVALID_COMPANY_METADATA_ENVELOPE")
    rows, dropped, seen = [], 0, set()
    for item in payload["results"][:25]:
        try:
            if profile["symbol"] not in item.get("tickers", []):
                raise ValueError("WRONG_NEWS_ISSUER")
            url = news.canonical_url(item["article_url"])
            if not as_utc(start) <= as_utc(item["published_utc"]) <= as_utc(end) <= as_utc(observed):
                raise ValueError("FUTURE_NEWS_TIME")
            title = str(item["title"]).replace("\r", " ").replace("\n", " ").strip()[:300]
            if not title:
                raise ValueError("EMPTY_NEWS_TITLE")
            if url in seen:
                continue
            seen.add(url)
            rows.append({"id": "massive_" + profile["symbol"] + "_" + hashlib.sha256(url.encode()).hexdigest()[:16],
                "title": title, "url": url, "publisher": str(item.get("publisher", {}).get("name", ""))[:120],
                "retrieved_at": observed, "provider_published_at": item["published_utc"], "first_seen_at": None,
                "categories": ["company"], "provider": "massive"})
        except (ValueError, KeyError, TypeError, AttributeError):
            dropped += 1
    status = "partial" if dropped or payload.get("next_url") else "links_available" if rows else "bounded_no_links"
    return {"provider": "massive", "status": status, "checked_at": observed, "articles": rows, "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "dropped_records": dropped, "pagination_not_followed": bool(payload.get("next_url"))}


def cached(key, call, *, cache_root, illustrative, clock):
    path = Path(cache_root) / ("illustrative" if illustrative else "real") / (stable_hash(key) + ".json")
    previous = read(path) if path.exists() else None
    if previous and previous.get("cache_sha256") == stable_hash(previous["value"]) and previous.get("key") == key and previous.get("is_illustrative") is illustrative:
        age = (as_utc(clock()) - as_utc(previous["value"]["checked_at"])).total_seconds()
        if 0 <= age < profiles.policy()["poll_interval_seconds"]:
            return {**previous["value"], "cache_reused": True}
    result = call()
    atomic_json(path, {"key": key, "is_illustrative": illustrative, "value": result, "cache_sha256": stable_hash(result)}, replace=True)
    return {**result, "cache_reused": False}


def query_failure(provider, error, clock):
    code = error.code if isinstance(error, SourceUnavailable) else "DEFERRED" if isinstance(error, (Deferred, NewsRequestDeferred)) else "SOURCE_RIGHTS_NOT_CLEARED" if isinstance(error, RightsError) else "TRANSPORT_OR_FORMAT_FAILURE"
    retry = getattr(error, "due_at", getattr(error, "retry_at", None))
    return {"provider": provider, "status": "deferred" if retry else "unavailable", "checked_at": clock(), "articles": [], "reason_code": code, "retry_at": retry}


def combine(profile, company, sector, globals_, official, cutoff):
    rows, providers, completed, topic_times = [], [], set(), {}
    for topic, result in [("company", company), ("sector", sector), *globals_.items()]:
        if result is None:
            continue
        fresh = 0 <= (as_utc(cutoff) - as_utc(result["checked_at"])).total_seconds() <= profiles.policy()["metadata_freshness_seconds"]
        providers.append({k: result[k] for k in ("provider", "status", "checked_at")})
        providers[-1].update(topic=topic, fresh=fresh, cache_reused=result.get("cache_reused", False))
        if fresh and result["status"] in {"links_available", "bounded_no_links"}:
            completed.add(topic)
            topic_times[topic] = result["checked_at"]
        for row in result["articles"]:
            rows.append({**row, "categories": [topic], "provider": result["provider"]})
    for feed in official.get("feeds", []):
        providers.append({"provider": feed["provider"], "status": feed["status"], "checked_at": feed["checked_at"], "fresh": 0 <= (as_utc(cutoff) - as_utc(feed["checked_at"])).total_seconds() <= 1800,
            "scope": "official_supplement_does_not_clear_broad_queries"})
        for row in feed["articles"]:
            rows.append({**row, "provider": feed["provider"], "first_seen_at": None})
    articles = {}
    for row in sorted(rows, key=lambda r: as_utc(r["retrieved_at"])):
        url = news.canonical_url(row["url"])
        previous = articles.get(url)
        source_ids = sorted(set((previous or {}).get("source_ids", []) + [row["id"]]))
        publishers = sorted(set((previous or {}).get("providers", []) + [row["provider"]]))
        categories = sorted(set((previous or {}).get("categories", []) + row["categories"]))
        fresh = 0 <= (as_utc(cutoff) - as_utc(row["retrieved_at"])).total_seconds() <= 1800
        articles[url] = {"id": row["id"], "source_ids": source_ids, "providers": publishers, "url": url, "title": row["title"], "publisher": row["publisher"],
            "categories": categories, "retrieved_at": row["retrieved_at"], "first_seen_at": row.get("first_seen_at"), "provider_published_at": row.get("provider_published_at"),
            "published_at": None, "impact_reviewed": False, "score_eligible": False, "fresh_metadata": fresh}
    topics = [{"id": topic, "discovery_status": "query_completed_links_unreviewed" if topic in completed else "not_verified", "checked_at": topic_times.get(topic),
        "bounded_empty": topic in completed and not any(topic in a["categories"] for a in articles.values())} for topic in ("company", "sector", "macro_politics", "commodities")]
    return {"status": "links_awaiting_review", "providers": providers, "topics": topics, "articles": list(articles.values()),
        "issuer_binding": {"cik": profile["cik"], "symbol": profile["symbol"], "profile_binding": profile["binding"]},
        "all_topics_discovered": len(completed) == 4, "all_impacts_reviewed": False, "score_component": None, "publisher_full_text_fetched": False}


def queue_markdown(context):
    p = context["symbol"]
    lines = [f"# {p} 원문·영향 검토 대기", "", "원문 링크·발행 시각·기업별 관련성·반론·후속 사건을 검토한 뒤 별도 양식을 제출합니다. 자동 승인·본문 수집·감정 점수는 수행하지 않습니다.", "",
        "| 원문 | 공급자 발행 표기 | 확인 질문 |", "|---|---|---|"]
    for row in context["news"]["articles"]:
        lines.append("| " + link(row["url"], text(row["title"])) + " | " + text(row["provider_published_at"]) + " | " + p + "의 사업·5거래일에 미칠 영향, 새 사실, 반론과 이후 확인할 사건 |")
    lines += ["", "뉴스 검색 범위가 확인되지 않은 주제는 중립이나 위험 없음으로 처리하지 않습니다. 같은 원문이라도 다른 기업에 대한 영향 검토는 별도로 제출해야 합니다.", ""]
    return "\n".join(lines)


def run(issuers, baseline, *, client, profile_root=None, news_root=None, registry_path=None, source_store=None, output=None, cache_root=None,
        illustrative=False, clock=now, sleep=time.sleep, official_collect=official_news.collect):
    start_hash, source_methods = digest(baseline), methods()
    def gate():
        guard.check(baseline, expected_sha256=start_hash)
        if methods() != source_methods:
            raise ValueError("NEWS_REFRESH_METHOD_CHANGED_DURING_RUN")
    gate()
    if client.record["is_illustrative"] is not illustrative:
        raise ValueError("NEWS_CLIENT_CLASSIFICATION_MISMATCH")
    receipt = profiles.capture(registry_path=registry_path, root=profile_root, source_store=source_store, illustrative=illustrative, clock=clock)
    due = as_utc(receipt["available_at"]).replace(microsecond=0) + timedelta(seconds=1)
    delay = (due - as_utc(clock())).total_seconds()
    if delay > 0:
        sleep(min(delay + .01, 1.05))
    selected = [profiles.as_of(i, clock(), root=profile_root, illustrative=illustrative) for i in sorted({str(i).zfill(10) for i in issuers})]
    if not selected or any(p is None for p in selected):
        raise ValueError("UNREVIEWED_NEWS_ISSUER")
    folder = Path(output or ROOT / "reports/private/news_refresh").resolve() / ("illustrative" if illustrative else "real") / uuid.uuid4().hex
    if not illustrative and not folder.is_relative_to((ROOT / "reports/private").resolve()):
        raise ValueError("PRIVATE_NEWS_REFRESH_OUTPUT_REQUIRED")
    cache_root = cache_root or ROOT / "reports/private/issuer_news_cache"
    settings = profiles.policy()
    initial_massive, initial_gdelt = client.massive_requests, client.gdelt_requests
    def company(p):
        def fetch():
            try:
                if client.massive_requests - initial_massive >= settings["maximum_massive_requests"]:
                    raise SourceUnavailable("RUN_REQUEST_CAP")
                end = clock(); start = (as_utc(end) - timedelta(hours=settings["lookback_hours"])).isoformat()
                raw = client.massive(p["symbol"], start, end)
                return normalize_massive(raw["raw"], p, start, end, raw["retrieved_at"])
            except (ValueError, OSError, Deferred, SourceUnavailable) as error:
                return query_failure("massive", error, clock)
        return cached(["massive", p["cik"], p["symbol"], p["binding"]], fetch, cache_root=cache_root, illustrative=illustrative, clock=clock)
    companies = {}
    for p in selected:
        gate(); companies[p["cik"]] = company(p)
    queries = [("macro_politics", settings["global_queries"]["macro_politics"]), ("commodities", settings["global_queries"]["commodities"])] + [(p["cik"], p["sector_query"]) for p in selected]
    gdelt, stopped = {}, False
    for topic, query in queries:
        gate()
        if stopped:
            gdelt[topic] = {"provider": "gdelt_project", "status": "deferred", "checked_at": clock(), "articles": [], "reason_code": "DEFERRED_AFTER_GDELT_FAILURE"}
            continue
        def fetch_gdelt():
            try:
                if not illustrative:
                    news_archive.require_metadata_rights({"gdelt_project"}, clock())
                if client.gdelt_requests - initial_gdelt >= settings["maximum_gdelt_requests"]:
                    raise SourceUnavailable("RUN_REQUEST_CAP")
                payload = client.gdelt(query)
                observed = clock()
                rows, dropped = news.normalize_articles(payload, topic if topic in settings["global_queries"] else "sector", observed)
                return {"provider": "gdelt_project", "status": "partial" if dropped else "links_available" if rows else "bounded_no_links", "checked_at": observed, "articles": rows}
            except (ValueError, OSError, Deferred, SourceUnavailable) as error:
                return query_failure("gdelt_project", error, clock)
        gdelt[topic] = cached(["gdelt_project", topic, query], fetch_gdelt, cache_root=cache_root, illustrative=illustrative, clock=clock)
        stopped = gdelt[topic]["status"] not in {"links_available", "bounded_no_links"}
    gate()
    official = official_collect()
    cutoff, outputs = clock(), []
    globals_ = {t: gdelt[t] for t in settings["global_queries"]}
    for p in selected:
        context = {"version": "1.0.0", "cik": p["cik"], "symbol": p["symbol"], "profile_binding": p["binding"], "is_illustrative": illustrative,
            "data_label": SYNTHETIC_LABEL if illustrative else None, "as_of": cutoff, "news": combine(p, companies[p["cik"]], gdelt[p["cik"]], globals_, official, cutoff)}
        dest = folder / p["cik"]
        gate()
        atomic_json(dest / "context.json", context)
        capture = issuer_news.capture(dest / "context.json", root=news_root, profile_root=profile_root, illustrative=illustrative, clock=clock)
        atomic_json(dest / "review_input.template.json", issuer_news.template(context))
        (dest / "REVIEW_QUEUE.ko.md").write_text(queue_markdown(context), encoding="utf-8")
        outputs.append({"cik": p["cik"], "symbol": p["symbol"], "article_count": len(context["news"]["articles"]), "company_status": companies[p["cik"]]["status"],
            "sector_status": gdelt[p["cik"]]["status"], "context_path": str(dest / "context.json"), "review_template_path": str(dest / "review_input.template.json"), "capture": capture})
    gate()
    result = {"version": "1.0.0", "as_of": cutoff, "is_illustrative": illustrative, "issuers": outputs,
        "massive_requests": client.massive_requests - initial_massive, "gdelt_requests": client.gdelt_requests - initial_gdelt,
        "official_requests": official.get("actual_requests", 0), "global_statuses": {t: gdelt[t]["status"] for t in globals_},
        "method_hashes": source_methods, "baseline_path": str(Path(baseline).resolve()), "baseline_sha256": start_hash, "released_score": None, "external_llm_calls": 0}
    atomic_json(folder / "RESULTS.json", result)
    return result, folder


def main(argv=None):
    import os
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--ciks", nargs="+", required=True)
    args = parser.parse_args(argv)
    guard.check(args.baseline)
    _, record_path = latest_training_snapshot()
    record = read(record_path)
    client = Client(record, os.environ.get("MASSIVE_API_KEY", ""), args.baseline)
    result, folder = run(args.ciks, args.baseline, client=client)
    print(json.dumps({"output_dir": str(folder), "issuers": [{k: r[k] for k in ("cik", "symbol", "article_count", "company_status", "sector_status")} for r in result["issuers"]],
        "massive_requests": result["massive_requests"], "gdelt_requests": result["gdelt_requests"], "official_requests": result["official_requests"], "released_score": None}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
