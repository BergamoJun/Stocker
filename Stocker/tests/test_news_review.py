"""ILLUSTRATIVE — NOT LIVE MARKET DATA. Synthetic feeds and analyst assertions."""

from copy import deepcopy
from decimal import Decimal
import json
from urllib.error import HTTPError

import pytest

from research import context_report, event_review, evidence_context, fetch_context, official_news
from research.sec_report import ROOT
from research.validation import stable_hash

AT = "2026-11-07T13:00:00Z"
LABEL = "ILLUSTRATIVE — NOT LIVE MARKET DATA"


def rss(profile, *, date="Sat, 07 Nov 2026 11:00:00 GMT", title=LABEL, link=None, encoding="UTF-8"):
    link = link or "https://" + profile["article_host"] + "/fixture"
    return (f'<?xml version="1.0" encoding="{encoding}"?><rss version="2.0"><channel><title>Fixture</title>'
        f'<item><title>{title}</title><link>{link}</link><pubDate>{date}</pubDate>'
        '<description>DO NOT RETAIN THIS ARTICLE BODY</description></item></channel></rss>').encode(encoding)


def feed_fixture():
    settings = official_news.policy()
    profile = settings["feeds"][1]
    feed = official_news.normalize(rss(profile), profile, AT, settings)
    return {"version": "1.0.0", "provider": "official_primary_feeds", "status": "available", "checked_at": AT,
        "feeds": [feed], "articles": feed["articles"], "broad_topic_coverage_cleared": False}


def test_original_rss_metadata_does_not_fetch_bodies_or_verify_event_impact():
    result = feed_fixture()
    row = result["articles"][0]
    assert row["published_at"] is row["event_at"] is None
    assert row["score_eligible"] is row["impact_reviewed"] is False
    assert "DO NOT RETAIN THIS ARTICLE BODY" not in json.dumps(result)
    assert result["feeds"][0]["raw_retained"] is False
    news = evidence_context.news_context(None, None, AT, fetch_context.policy(), official=result)
    assert len(news["articles"]) == 1
    assert news["articles"][0]["fresh_metadata"] is True
    assert all(row["discovery_status"] == "not_verified" for row in news["topics"])
    assert news["topics"][1]["highlight_ids"] and news["topics"][3]["highlight_ids"]
    assert news["all_topics_discovered"] is False and news["score_component"] is None


def test_official_iso_8859_1_declaration_is_honored_without_silently_replacing_text():
    settings = official_news.policy()
    profile = settings["feeds"][1]
    result = official_news.normalize(rss(profile, title="ILLUSTRATIVE Pi\u00f1ata", encoding="ISO-8859-1"), profile, AT, settings)
    assert result["articles"][0]["title"] == "ILLUSTRATIVE Pi\u00f1ata"
    assert result["status"] == "links_available"


@pytest.mark.parametrize("problem", ["foreign_host", "future", "timezone", "title"])
def test_invalid_official_entry_is_not_an_empty_success(problem):
    settings = official_news.policy()
    profile = settings["feeds"][0]
    kwargs = {"link": "https://publisher.example/story"} if problem == "foreign_host" else {"date": "Sun, 08 Nov 2026 11:00:00 GMT"} if problem == "future" else {"date": "Sat, 07 Nov 2026 11:00:00"} if problem == "timezone" else {"title": ""}
    result = official_news.normalize(rss(profile, **kwargs), profile, AT, settings)
    assert result["status"] == "unavailable" and result["invalid_records"] == 1


@pytest.mark.parametrize("raw", [b'<!DOCTYPE rss [<!ENTITY x "boom">]><rss><channel/></rss>', '<!DOCTYPE rss><rss><channel/></rss>'.encode('utf-16'), b'<html>not RSS</html>'])
def test_unsafe_xml_and_nonfeeds_are_rejected(raw):
    settings = official_news.policy()
    with pytest.raises(ValueError):
        official_news.normalize(raw, settings["feeds"][0], AT, settings)


def test_older_official_entries_are_explicitly_outside_the_bounded_window():
    settings = official_news.policy()
    profile = settings["feeds"][0]
    result = official_news.normalize(rss(profile, date="Wed, 28 Oct 2026 18:00:00 GMT"), profile, AT, settings)
    assert result["status"] == "no_recent_entries_in_bounded_feed"
    assert result["aged_out_records"] == 1 and result["invalid_records"] == 0


def test_official_cache_preserves_observation_time_and_limits_repeated_requests(tmp_path):
    settings = official_news.policy()
    profiles = {row["url"]: row for row in settings["feeds"]}
    calls, waits = [], []
    def request(url, _):
        calls.append(url)
        return rss(profiles[url])
    first = official_news.collect(settings=settings, state_dir=tmp_path, request=request, clock=lambda: AT, sleep=waits.append)
    second = official_news.collect(settings=settings, state_dir=tmp_path, request=request, clock=lambda: "2026-11-07T13:01:00Z", sleep=waits.append)
    assert first["actual_requests"] == 2 and second["actual_requests"] == 0
    assert len(calls) == 2 and waits == [15]
    assert second["articles"][0]["retrieved_at"] == first["articles"][0]["retrieved_at"] == AT
    assert all(row["cache_reused"] for row in second["feeds"])


def test_official_rights_fail_before_request_and_failures_do_not_clear_coverage(tmp_path):
    records = json.loads((ROOT / "config/official_news_entitlements.json").read_text(encoding="utf-8"))["records"]
    records[0]["monthly_cost_usd"] = 1
    def forbidden(*args):
        pytest.fail("Rights must be checked before network")
    with pytest.raises(ValueError, match="budget"):
        official_news.collect(entitlements=records, state_dir=tmp_path, request=forbidden, clock=lambda: AT)
    def failure(url, _):
        raise HTTPError(url, 429, "fixture", {}, None)
    result = official_news.collect(state_dir=tmp_path, request=failure, clock=lambda: AT, sleep=lambda _: None)
    assert result["status"] == "partial" and result["articles"] == []
    assert result["broad_topic_coverage_cleared"] is False
    assert all(row["reason"] == "HTTP_429" for row in result["feeds"])


def news_fixture():
    return {"status": "links_awaiting_review", "all_topics_discovered": True,
        "providers": [{"provider": "illustrative", "status": "available", "fresh": True, "checked_at": AT}],
        "topics": [{"id": key, "discovery_status": "query_completed_links_unreviewed"} for key in ("company", "sector", "macro_politics", "commodities")],
        "articles": [{"id": "fixture_article", "title": LABEL, "url": "https://publisher.example/story", "publisher": LABEL,
            "categories": ["company"], "retrieved_at": "2026-11-07T12:55:00Z", "first_seen_at": None,
            "provider_published_at": "2026-11-07T11:00:00Z", "published_at": None, "impact_reviewed": False,
            "score_eligible": False, "fresh_metadata": True}], "score_component": None, "publisher_full_text_fetched": False}


def manifest_fixture(news):
    return {"version": "1.0.0", "policy_id": official_news.policy()["id"], "news_context_sha256": stable_hash(news),
        "template_only": False, "reviewer_id": LABEL, "submitted_at": AT, "events": [{
            "event_id": "fixture_event", "review_status": "reviewed", "review_id": LABEL, "source_ids": ["fixture_article"],
            "original_urls": ["https://publisher.example/story"], "rationale": LABEL, "five_session_relevance_explanation": LABEL,
            "counterargument": LABEL, "monitoring_event": LABEL,
            "reviewed_at": "2026-11-07T12:59:00Z", "published_at": "2026-11-07T11:00:00Z", "first_public_at": "2026-11-07T11:00:00Z",
            "direction": 1, "impact": "high", "relevance": "direct_company", "reliability": "primary", "novelty": "new",
            "original_checks": [{"source_id": "fixture_article", "url": "https://publisher.example/story",
                "checked_at": "2026-11-07T12:58:00Z", "publication_verified": True, "published_at": "2026-11-07T11:00:00Z"}]}]}


def test_recent_good_and_bad_events_have_symmetric_signed_effects():
    news = news_fixture()
    manifest = manifest_fixture(news)
    good = event_review.assess(news, manifest, AT)
    manifest["events"][0]["direction"] = -1
    bad = event_review.assess(news, manifest, AT)
    assert good["status"] == bad["status"] == "research_only_unvalidated"
    assert Decimal(good["experimental_component"]) > 20 > Decimal(bad["experimental_component"])
    assert Decimal(good["experimental_component"]) + Decimal(bad["experimental_component"]) == 40
    assert good["public_component"] is good["released_score"] is None
    assert good["pending_article_ids"] == []


def test_republished_old_event_does_not_get_a_new_freshness_bonus_and_recycled_is_zero():
    news = news_fixture()
    manifest = manifest_fixture(news)
    recent = event_review.assess(news, manifest, AT)
    manifest["events"][0]["first_public_at"] = "2026-11-06T11:00:00Z"
    older = event_review.assess(news, manifest, AT)
    assert 20 < Decimal(older["experimental_component"]) < Decimal(recent["experimental_component"])
    manifest["events"][0]["novelty"] = "recycled"
    assert Decimal(event_review.assess(news, manifest, AT)["experimental_component"]) == 20


def test_duplicate_event_is_not_multiplied_and_conflicting_directions_are_withheld():
    news = news_fixture()
    manifest = manifest_fixture(news)
    one = event_review.assess(news, manifest, AT)
    manifest["events"].append(deepcopy(manifest["events"][0]))
    repeated = event_review.assess(news, manifest, AT)
    assert repeated["experimental_component"] == one["experimental_component"]
    manifest["events"][1]["direction"] = -1
    assert event_review.assess(news, manifest, AT)["experimental_component"] is None


@pytest.mark.parametrize("problem", ["wrong_snapshot", "draft", "unverified_publication", "future_review", "wrong_url", "before_observation", "missing_explanation", "boolean_direction"])
def test_missing_or_invalid_review_cannot_produce_a_news_total(problem):
    news = news_fixture()
    manifest = manifest_fixture(news)
    event = manifest["events"][0]
    if problem == "wrong_snapshot": manifest["news_context_sha256"] = "0" * 64
    elif problem == "draft": manifest["template_only"] = True
    elif problem == "unverified_publication": event["original_checks"][0]["publication_verified"] = False
    elif problem == "future_review": event["reviewed_at"] = "2026-11-07T13:01:00Z"
    elif problem == "wrong_url": event["original_checks"][0]["url"] = "https://publisher.example/other"
    elif problem == "before_observation": event["original_checks"][0]["checked_at"] = "2026-11-07T12:54:00Z"
    elif problem == "missing_explanation": del event["five_session_relevance_explanation"]
    else: event["direction"] = True
    result = event_review.assess(news, manifest, AT)
    assert result["experimental_component"] is None
    assert result["invalid_review_count"] >= 1
    assert result["released_score"] is None


def test_reviewing_one_article_does_not_clear_missing_macro_news():
    news = news_fixture()
    news["topics"][2]["discovery_status"] = "not_verified"
    news["all_topics_discovered"] = False
    result = event_review.assess(news, manifest_fixture(news), AT)
    assert result["reviewed_article_count"] == 1
    assert result["missing_discovery_topics"] == ["macro_politics"]
    assert result["experimental_component"] is None
    assert "BROAD_DISCOVERY_INCOMPLETE" in result["reason_codes"]


def test_new_link_requires_new_review_and_stale_collection_cannot_score():
    news = news_fixture()
    manifest = manifest_fixture(news)
    extra = {**deepcopy(news["articles"][0]), "id": "second", "url": "https://publisher.example/second"}
    news["articles"].append(extra)
    manifest["news_context_sha256"] = stable_hash(news)
    result = event_review.assess(news, manifest, AT)
    assert result["pending_article_ids"] == ["second"] and result["experimental_component"] is None
    news = news_fixture()
    result = event_review.assess(news, manifest_fixture(news), "2026-11-07T13:31:00Z")
    assert result["experimental_component"] is None


def test_review_queue_is_incomplete_by_default_and_never_claims_verified_publication():
    news = news_fixture()
    queue = event_review.queue(news)
    template = event_review.template(news)
    assert len(queue["pending"]) == 1 and len(queue["pending"][0]["questions_ko"]) == 5
    assert template["template_only"] is True and template["events"] == []
    assert event_review.assess(news, template, AT)["experimental_component"] is None


def test_empty_unobserved_collection_does_not_become_a_neutral_score():
    news = news_fixture()
    news["providers"] = []
    news["articles"] = []
    manifest = manifest_fixture(news)
    manifest["events"] = []
    result = event_review.assess(news, manifest, AT)
    assert result["experimental_component"] is None
    assert "NO_FRESH_COLLECTION" in result["reason_codes"]


def test_stale_article_is_withheld_instead_of_dropped_into_an_empty_neutral_set():
    news = news_fixture()
    news["providers"][0]["checked_at"] = "2026-11-07T13:31:00Z"
    result = event_review.assess(news, manifest_fixture(news), "2026-11-07T13:31:00Z")
    assert result["experimental_component"] is None
    assert "STALE_ARTICLE_METADATA" in result["reason_codes"]


def test_review_cli_preserves_explicit_illustrative_classification(tmp_path, monkeypatch, capsys):
    news = news_fixture()
    context = tmp_path / "context.json"
    context.write_text(json.dumps({"is_illustrative": True, "news": news}), encoding="utf-8")
    reviews = tmp_path / "reviews.json"
    reviews.write_text(json.dumps(manifest_fixture(news)), encoding="utf-8")
    schema = tmp_path / "schemas/news_event_review.schema.json"
    schema.parent.mkdir()
    schema.write_bytes((ROOT / "schemas/news_event_review.schema.json").read_bytes())
    for name in ("config/news_review_policy.json", "config/swing_candidate_policy.json", "research/event_review.py", "research/news.py"):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    monkeypatch.setattr(event_review, "ROOT", tmp_path)
    monkeypatch.setattr(event_review, "now", lambda: AT)
    output = tmp_path / "reports/private/news_review"
    assert event_review.main(["--context", str(context), "--reviews", str(reviews), "--output-dir", str(output)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["is_illustrative"] is True and summary["data_label"] == LABEL
    result = json.loads(next(output.glob("*/news_signal.json")).read_text(encoding="utf-8"))
    assert result["is_illustrative"] is True and result["data_label"] == LABEL
    assert result["released_score"] is None
    assert result["registration"]["review_manifest_sha256"]
