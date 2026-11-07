"""Two fixed official RSS metadata feeds, bounded and locally cached."""

import argparse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, build_opener
from xml.etree import ElementTree as ET

from .fetch_prices import NoRedirect
from .news import canonical_url
from .rights import as_utc, require_entitlement
from .sec_report import ROOT
from .validation import now, save_new

ENDPOINTS = {"fed_monetary": "https://www.federalreserve.gov/feeds/press_monetary.xml",
    "eia_today_energy": "https://www.eia.gov/rss/todayinenergy.xml"}


def policy():
    result = json.loads((ROOT / "config/news_review_policy.json").read_text(encoding="utf-8"))
    if result["release_enabled"] is not False or result["monthly_data_budget_usd"] != 0 or result["official_feeds_clear_broad_topic_coverage"] is not False:
        raise ValueError("News review must remain private research with limited source coverage")
    if {row["id"]: row["url"] for row in result["feeds"]} != ENDPOINTS:
        raise ValueError("Only reviewed official feed endpoints are supported")
    return result


def download(url, maximum):
    if url not in ENDPOINTS.values():
        raise ValueError("Unapproved feed URL")
    request = Request(url, headers={"User-Agent": "Stocker-personal-metadata/0.4", "Accept": "application/rss+xml,application/xml,text/xml"})
    with build_opener(NoRedirect()).open(request, timeout=20) as response:
        raw = response.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("Oversized feed")
    return raw


def normalize(raw, profile, retrieved_at, settings):
    if profile["id"] not in ENDPOINTS or profile["url"] != ENDPOINTS[profile["id"]]:
        raise ValueError("Unapproved source identity")
    if b"\x00" in raw:
        raise ValueError("Only reviewed single-byte or UTF-8 XML encodings are supported")
    declaration = re.search(br'encoding\s*=\s*[\'\"]([A-Za-z0-9_-]+)[\'\"]', raw[:200], re.IGNORECASE)
    encoding = declaration.group(1).decode("ascii").lower() if declaration else "utf-8-sig"
    if encoding not in {"utf-8", "utf-8-sig", "iso-8859-1", "us-ascii"}:
        raise ValueError("Unsupported feed encoding")
    text = raw.decode(encoding)
    if len(raw) > settings["maximum_feed_bytes"] or "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("Unsafe XML envelope")
    root = ET.fromstring(text)
    if root.tag != "rss" or root.find("channel") is None:
        raise ValueError("RSS channel required")
    rows, seen, invalid, aged_out = [], set(), 0, 0
    for item in root.findall("./channel/item")[:settings["maximum_items_per_feed"]]:
        try:
            url = canonical_url(item.findtext("link"))
            if urlparse(url).hostname != profile["article_host"] or urlparse(url).scheme != "https":
                raise ValueError("Non-original or non-HTTPS article")
            date = parsedate_to_datetime(item.findtext("pubDate"))
            if date.tzinfo is None:
                raise ValueError("Publication timezone missing")
            date = date.astimezone(timezone.utc)
            age = (as_utc(retrieved_at) - date).total_seconds() / 3600
            if age < 0:
                raise ValueError("Future provider publication")
            if age > settings["lookback_hours"]:
                aged_out += 1
                continue
            title = (item.findtext("title") or "").replace("\r", " ").replace("\n", " ").strip()[:300]
            if not title:
                raise ValueError("Missing title")
            if url in seen:
                continue
            seen.add(url)
            rows.append({"id": "official_news_" + hashlib.sha256(url.encode()).hexdigest()[:16],
                "url": url, "title": title, "publisher": profile["name_ko"], "categories": profile["categories"],
                "retrieved_at": retrieved_at, "provider_published_at": date.isoformat().replace("+00:00", "Z"),
                "published_at": None, "event_at": None, "score_eligible": False, "impact_reviewed": False,
                "feed_id": profile["id"], "feed_url": profile["url"], "coverage_scope": "supplementary_official_source_only"})
        except (ValueError, TypeError, AttributeError, OverflowError):
            invalid += 1
    return {"feed_id": profile["id"], "provider": profile["provider"], "checked_at": retrieved_at,
        "status": ("partial" if rows else "unavailable") if invalid else ("links_available" if rows else "no_recent_entries_in_bounded_feed"),
        "articles": rows, "invalid_records": invalid, "aged_out_records": aged_out, "scope_ko": profile["scope_ko"],
        "feed_url": profile["url"], "raw_sha256": hashlib.sha256(raw).hexdigest(), "raw_retained": False}


def collect(*, settings=None, entitlements=None, state_dir=None, request=download, clock=now, sleep=time.sleep):
    settings = settings or policy()
    entitlements = entitlements or json.loads((ROOT / "config/official_news_entitlements.json").read_text(encoding="utf-8"))["records"]
    at = clock()
    by_provider = {row["provider"]: row for row in entitlements}
    for profile in settings["feeds"]:
        require_entitlement(by_provider[profile["provider"]], ["ingest_metadata", "store_metadata", "display_metadata_links"], at)
    folder = Path(state_dir) if state_dir else ROOT / "local_data/official_news_cache"
    folder.mkdir(parents=True, exist_ok=True)
    lock = folder / "request.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return {"provider": "official_primary_feeds", "status": "deferred", "checked_at": at, "feeds": [], "articles": [], "actual_requests": 0}
    os.close(descriptor)
    feeds, actual_requests = [], 0
    try:
        for profile in settings["feeds"]:
            path = folder / (profile["id"] + ".json")
            try:
                cached = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
                if cached and (cached["feed_id"] != profile["id"] or cached["provider"] != profile["provider"] or cached["feed_url"] != profile["url"]):
                    raise ValueError("Cache source identity differs")
                age = (as_utc(clock()) - as_utc(cached["checked_at"])).total_seconds() if cached else None
            except (ValueError, OSError, KeyError, TypeError):
                cached, age = None, None
            if age is not None and 0 <= age < settings["poll_interval_seconds"]:
                feeds.append({**cached, "cache_reused": True})
                continue
            try:
                if actual_requests:
                    sleep(settings["minimum_request_spacing_seconds"])
                actual_requests += 1
                raw = request(profile["url"], settings["maximum_feed_bytes"])
                row = normalize(raw, profile, clock(), settings)
            except (ValueError, OSError, ET.ParseError) as error:
                row = {"feed_id": profile["id"], "provider": profile["provider"], "checked_at": clock(), "articles": [],
                    "status": "unavailable", "reason": "HTTP_" + str(error.code) if isinstance(error, HTTPError) else "transport_or_format_failure",
                    "scope_ko": profile["scope_ko"], "feed_url": profile["url"]}
            row["cache_reused"] = False
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
            feeds.append(row)
    finally:
        lock.unlink(missing_ok=True)
    articles = [row for feed in feeds for row in feed["articles"]]
    complete = all(row["status"] in {"links_available", "no_recent_entries_in_bounded_feed"} for row in feeds)
    return {"version": "1.0.0", "provider": "official_primary_feeds", "status": "available" if complete else "partial",
        "checked_at": clock(), "feeds": feeds, "articles": articles, "actual_requests": actual_requests,
        "broad_topic_coverage_cleared": False, "publisher_full_text_fetched": False, "score_component": None}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Two free official RSS metadata sources, not complete world-news coverage")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports/private/official_news")
    args = parser.parse_args(argv)
    if not args.output_dir.resolve().is_relative_to(ROOT / "reports/private"):
        parser.error("Private output required")
    result = collect()
    folder = args.output_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    save_new(folder / "news_metadata.json", result)
    print(json.dumps({"status": result["status"], "article_count": len(result["articles"]), "actual_requests": result["actual_requests"],
        "feeds": [{key: row[key] for key in ("feed_id", "status")} for row in result["feeds"]], "output_dir": str(folder.resolve())}))
    return 0 if result["status"] == "available" else 2


if __name__ == "__main__":
    raise SystemExit(main())
