"""One approved personal Basic news-metadata request; no bodies or sentiment."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from .fetch_prices import NoRedirect
from .learned_research import latest_training_snapshot
from .news import canonical_url
from .rights import as_utc, price_uses, require_entitlement
from .sec_report import ROOT
from .validation import now, save_new


def normalize(raw, retrieved_at, start, end):
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("status") != "OK" or not isinstance(payload.get("results"), list):
        raise ValueError("Invalid provider news-metadata response")
    articles, seen, dropped = [], set(), 0
    for item in payload["results"][:25]:
        try:
            if "BE" not in item.get("tickers", []):
                raise ValueError("News ticker mismatch")
            url = canonical_url(item["article_url"])
            published = as_utc(item["published_utc"])
            if not as_utc(start) <= published <= as_utc(end) <= as_utc(retrieved_at):
                raise ValueError("News time outside requested window")
            title = str(item["title"]).replace("\r", " ").replace("\n", " ")[:300]
            if not title.strip():
                raise ValueError("Empty title")
            if url in seen:
                continue
            seen.add(url)
            articles.append({"id": "massive_news_" + hashlib.sha256(url.encode()).hexdigest()[:16],
                "title": title, "url": url, "publisher": str(item.get("publisher", {}).get("name", ""))[:120],
                "categories": ["company"], "retrieved_at": retrieved_at,
                "provider_published_at": item["published_utc"], "published_at": None, "event_at": None,
                "publication_verification": "provider_metadata_only_not_independently_reviewed",
                "impact_direction": "unassessed", "score_eligible": False})
        except (ValueError, KeyError, TypeError, AttributeError):
            dropped += 1
    return {"version": "1.0.0", "provider": "massive", "scope": "own_personal_news_metadata_only", "symbol": "BE",
        "status": ("partial" if articles else "unavailable") if dropped else ("links_available" if articles else "no_links_in_bounded_query"), "checked_at": retrieved_at,
        "requested_start": start, "requested_end": end, "query_topics": ["company"], "articles": articles,
        "dropped_records": dropped, "raw_sha256": hashlib.sha256(raw).hexdigest(), "raw_payload_retained": False,
        "summary_sentiment_and_article_text_retained": False, "score_component": None,
        "limitations": ["Basic news is documented as updated hourly; not a real-time guarantee",
            "Ticker-only bounded discovery is not complete sector/macro/commodity/political coverage",
            "Provider publication field is not an independently reviewed original article or event time",
            "Publisher text, summaries, images and vendor sentiment are discarded; no article fetch or LLM input"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Private BE news-metadata fallback; one request, no retries or paid add-ons")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports/private/context_news_massive")
    args = parser.parse_args(argv)
    if not args.output_dir.resolve().is_relative_to(ROOT / "reports/private"):
        parser.error("Private output required")
    cutoff = now()
    start = (as_utc(cutoff) - timedelta(hours=72)).isoformat().replace("+00:00", "Z")
    _, entitlement = latest_training_snapshot()
    record = json.loads(entitlement.read_text(encoding="utf-8"))
    require_entitlement(record, price_uses("local_research"), cutoff, synthetic=False)
    if record["provider"] != "massive" or record["plan"] != "stocks_basic":
        raise ValueError("Reviewed own Basic account required")
    key = os.environ.get("MASSIVE_API_KEY", "")
    if not key or key != key.strip() or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise ValueError("MASSIVE_API_KEY is missing or invalid")
    url = "https://api.massive.com/v2/reference/news?" + urlencode({"ticker": "BE", "published_utc.gte": start,
        "published_utc.lte": cutoff, "order": "desc", "sort": "published_utc", "limit": 25})
    request = Request(url, headers={"Authorization": "Bearer " + key, "Accept": "application/json", "User-Agent": "Stocker-personal-metadata/0.3"})
    try:
        with build_opener(NoRedirect()).open(request, timeout=20) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000 or key.encode("ascii") in raw:
            raise ValueError("Refusing oversized or credential-bearing response")
        result = normalize(raw, now(), start, cutoff)
    except HTTPError as error:
        result = {"provider": "massive", "status": "unavailable", "checked_at": now(), "articles": [],
            "reason": f"HTTP_{error.code}; no retry or paid upgrade", "score_component": None}
    except (ValueError, OSError):
        result = {"provider": "massive", "status": "unavailable", "checked_at": now(), "articles": [],
            "reason": "Invalid response or unavailable transport; no retry", "score_component": None}
    folder = args.output_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    save_new(folder / "news_metadata.json", result)
    print(json.dumps({"provider": "massive", "status": result["status"], "article_count": len(result["articles"]),
        "output_dir": str(folder.resolve()), "score_component": None}))
    return 0 if result["status"] != "unavailable" else 2


if __name__ == "__main__":
    raise SystemExit(main())
