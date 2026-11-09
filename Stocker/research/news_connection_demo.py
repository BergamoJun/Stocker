"""ILLUSTRATIVE — NOT LIVE MARKET DATA. No feeds, bodies or market prices."""

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from . import event_review, news_archive, news_connections as links, news_review_store as store
from .local_files import atomic_json
from .sec_report import ROOT, SYNTHETIC_LABEL
from .validation import digest, stable_hash

SOURCE = "2026-11-08T11:15:00Z"
CAPTURE = "2026-11-08T11:15:02Z"
REVIEW = "2026-11-08T11:15:20Z"
DECISION = "2026-11-08T11:15:25Z"
LATER = "2026-11-08T11:17:30Z"


def context():
    news = {"status": "links_awaiting_review", "all_topics_discovered": False,
        "providers": [{"provider": "fixture", "status": "available", "fresh": True, "checked_at": SOURCE}],
        "topics": [{"id": key, "discovery_status": "query_completed_links_unreviewed" if key == "company" else "not_verified"} for key in ("company", "sector", "macro_politics", "commodities")],
        "articles": [{"id": f"fixture_{i}", "source_ids": [f"fixture_{i}"], "title": f"{SYNTHETIC_LABEL} article {i}",
            "url": f"https://example.com/illustrative-story-{i}", "publisher": "Illustrative publisher", "providers": ["fixture"],
            "categories": ["company"], "retrieved_at": SOURCE, "first_seen_at": None, "provider_published_at": "2026-11-08T10:00:00Z",
            "published_at": None, "impact_reviewed": False, "score_eligible": False, "fresh_metadata": True} for i in (1, 2)],
        "score_component": None, "publisher_full_text_fetched": False}
    return {"is_illustrative": True, "data_label": SYNTHETIC_LABEL, "as_of": SOURCE, "news": news}


def manifest(ctx, *, reviewer="illustrative_reviewer", direction=1):
    value = event_review.template(ctx["news"])
    value.update(template_only=False, reviewer_id=reviewer, submitted_at="2026-11-08T11:15:15Z", events=[])
    for i, article in enumerate(ctx["news"]["articles"], 1):
        value["events"].append({"event_id": f"illustrative_event_{i}", "review_status": "reviewed", "review_id": f"example-review-{i}",
            "source_ids": [article["id"]], "original_urls": [article["url"]], "rationale": SYNTHETIC_LABEL,
            "five_session_relevance_explanation": SYNTHETIC_LABEL, "counterargument": SYNTHETIC_LABEL, "monitoring_event": SYNTHETIC_LABEL,
            "reviewed_at": "2026-11-08T11:15:10Z", "published_at": article["provider_published_at"], "first_public_at": article["provider_published_at"],
            "direction": direction if i == 1 else -direction, "impact": "high", "relevance": "direct_company", "reliability": "primary", "novelty": "new",
            "original_checks": [{"source_id": article["id"], "url": article["url"], "checked_at": "2026-11-08T11:15:05Z",
                "publication_verified": True, "published_at": article["provider_published_at"]}]})
    return value


def forecast(folder, ctx_path, *, cutoff=DECISION):
    key = stable_hash(["illustrative-news-forecast", str(folder), cutoff])[:24]
    base = Path(folder) / "base.json"
    atomic_json(base, {"symbol": "BE", "is_illustrative": True, "data_label": SYNTHETIC_LABEL})
    path = Path(folder) / "forecasts" / key / "forecast.json"
    atomic_json(path, {"record_id": key, "status": "prospective_context_record", "symbol": "BE", "is_illustrative": True,
        "data_label": SYNTHETIC_LABEL, "as_of": cutoff, "recorded_at": cutoff, "entry_at": "2026-11-09T14:30:00Z",
        "sessions": [f"2026-11-{day:02d}" for day in range(9, 14)], "decision_scope": "price_cost_screen_only",
        "context_path": str(ctx_path.resolve()), "context_sha256": digest(ctx_path), "base_forecast_path": str(base.resolve()), "base_forecast_sha256": digest(base)})
    return path


def run():
    folder = ROOT / "reports/private/news_connection_demo" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    archive_root, root = folder / "archive", folder / "connections"
    ctx = context()
    ctx_path, review_path = folder / "context.json", folder / "review.json"
    atomic_json(ctx_path, ctx)
    news_archive.capture(ctx_path, root=archive_root, clock=lambda: CAPTURE)
    atomic_json(review_path, manifest(ctx))
    receipt = store.store(ctx_path, review_path, root=root, archive_root=archive_root, illustrative=True, clock=lambda: REVIEW)
    path = forecast(folder, ctx_path)
    original_sha = digest(path)
    bound, binding = links.bind_forecast(path, root=root, archive_root=archive_root, illustrative=True, clock=lambda: "2026-11-08T11:15:30Z")
    report = links.save_snapshot(bound, root=root)
    changed = deepcopy(ctx)
    changed["as_of"] = "2026-11-08T11:16:00Z"
    changed["news"]["articles"][0]["title"] = SYNTHETIC_LABEL + " revised metadata"
    for article in changed["news"]["articles"]:
        article["retrieved_at"] = changed["as_of"]
    atomic_json(folder / "changed.json", changed)
    news_archive.capture(folder / "changed.json", root=archive_root, clock=lambda: "2026-11-08T11:16:02Z")
    after_change = links.snapshot("2026-11-08T11:16:10Z", root=root, archive_root=archive_root, illustrative=True, clock=lambda: LATER)
    changed_report = links.save_snapshot(after_change, root=root)
    reverted = deepcopy(ctx)
    reverted["as_of"] = "2026-11-08T11:17:00Z"
    for article in reverted["news"]["articles"]:
        article["retrieved_at"] = reverted["as_of"]
    atomic_json(folder / "reverted.json", reverted)
    news_archive.capture(folder / "reverted.json", root=archive_root, clock=lambda: "2026-11-08T11:17:02Z")
    current = links.snapshot(LATER, root=root, archive_root=archive_root, illustrative=True, clock=lambda: LATER)
    reverted_report = links.save_snapshot(current, root=root)
    again, same_binding = links.bind_forecast(path, root=root, archive_root=archive_root, illustrative=True, clock=lambda: LATER)
    if digest(path) != original_sha or same_binding != binding or again != bound:
        raise ValueError("ILLUSTRATIVE_ORIGINAL_OR_BINDING_CHANGED")
    return {"status": "illustrative_news_connection_workflow", "is_illustrative": True, "article_count": len(bound["articles"]),
        "initial_highlights": len(bound["highlights"]), "after_change_highlights": len(after_change["highlights"]),
        "after_reversion_highlights": len(current["highlights"]), "original_forecast_unchanged": True,
        "immutable_binding_reused": True, "network_requests": 0, "external_llm_calls": 0,
        "output_dir": str(folder.resolve()), "initial_report": str((report / "REPORT.ko.md").resolve()),
        "changed_report": str((changed_report / "REPORT.ko.md").resolve()), "reverted_report": str((reverted_report / "REPORT.ko.md").resolve())}
