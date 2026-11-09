"""ILLUSTRATIVE - no live financial/news content and no network."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import pytest

from extensions import scope, text_sources as source, text_store as store, text_collect as collect
from research import project_compatibility as legacy
from research.local_files import atomic_json
from research.sec_report import ROOT
from research.validation import digest

NOW = "2026-11-10T12:00:00Z"
FED = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20261108a.htm"
EIA = "https://www.eia.gov/todayinenergy/detail.php?id=12345"
PARA = "ILLUSTRATIVE official narrative for software testing. This describes a hypothetical event without real prices or investment advice."


def html(provider="federal_reserve_board", *, paragraph=PARA, date="2026-11-08", extra=""):
    return (f'<html><head><meta name="DC.date" content="{date}"><title>ILLUSTRATIVE</title></head><body>'
            f'<nav><p>Not article text</p></nav><div id="article"><h1>ILLUSTRATIVE TEST</h1><p>{paragraph}</p>{extra}</div></body></html>').encode()


def parsed(*, paragraph=PARA, at=NOW):
    return {**source.article(html(paragraph=paragraph), FED, "federal_reserve_board", at, source.policy(lambda: at)), "retrieved_at": at}


@pytest.fixture
def baseline(tmp_path):
    files = legacy.project_files(ROOT)
    files.update(scope.inventory(ROOT))
    files.update({str(p.relative_to(ROOT)).replace("\\", "/"): p for p in (ROOT / "context_adapters").rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"})
    path = tmp_path / "baseline.json"
    atomic_json(path, {"version": "1.0.0", "root": str(ROOT), "files": {k: digest(p) for k, p in files.items()}, "allowed_modified": [],
        "allowed_new": [], "allowed_extensions": [], "jobs": [], "reports": [], "additional_reports": [], "adapter_allow_new": []})
    return path


@pytest.mark.parametrize("url,provider", [
    ("https://evil.test/todayinenergy/detail.php?id=2", "us_eia"),
    (EIA + "&token=secret", "us_eia"), (EIA + "#part", "us_eia"),
    ("https://www.eia.gov/todayinenergy/detail.php?id=%31", "us_eia"),
    ("http://www.eia.gov/todayinenergy/detail.php?id=2", "us_eia"),
    ("https://www.federalreserve.gov/aboutthefed.htm", "federal_reserve_board"),
    ("https://www.federalreserve.gov:443/newsevents/pressreleases/monetary20261108a.htm", "federal_reserve_board"),
])
def test_nonreviewed_origin_paths_and_extra_parameters_are_rejected(url, provider):
    with pytest.raises(ValueError):
        source.approved_url(url, provider)


def test_extracts_only_narrative_and_keeps_date_precision():
    value = source.article(html(extra='<figure><p>Picture caption</p><img src="x"></figure><table><tr><td>Excluded table</td></tr></table><script>executeBadCode()</script><p class="source">Third party caption</p>'), FED, "federal_reserve_board", NOW, source.policy(lambda: NOW))
    assert value["text"] == PARA
    assert value["published_at"] is None and value["time_precision"] == "date"
    assert not value["publication_time_verified"] and not value["html_retained"]


@pytest.mark.parametrize("flag", ["Copyright protected", "LSEG information", "Used with permission", "Reuters report"])
def test_protected_selected_narrative_is_not_retained(flag):
    with pytest.raises(ValueError, match="RIGHTS_NOT_CLEARED"):
        source.article(html(paragraph=PARA + " " + flag), FED, "federal_reserve_board", NOW, source.policy(lambda: NOW))


def test_no_whole_page_fallback_when_article_template_changes():
    with pytest.raises(ValueError, match="CONTAINER"):
        source.article(html().replace(b'id="article"', b'id="unknown"'), FED, "federal_reserve_board", NOW, source.policy(lambda: NOW))


def test_document_specific_rights_notice_outside_paragraphs_is_withheld():
    with pytest.raises(ValueError, match="RIGHTS_NOT_CLEARED"):
        source.article(html(extra='<div class="copyright">Protected document</div>'), FED, "federal_reserve_board", NOW, source.policy(lambda: NOW))


@pytest.mark.parametrize("date", ["2026-11-11", "2026-11-07"])
def test_future_or_url_conflicting_publication_is_withheld(date):
    with pytest.raises(ValueError):
        source.article(html(date=date), FED, "federal_reserve_board", NOW, source.policy(lambda: NOW))


def test_missing_or_conflicting_publication_date_is_withheld():
    settings = source.policy(lambda: NOW)
    for raw in [html().replace(b'<meta name="DC.date" content="2026-11-08">', b''), html().replace(b'</head>', b'<meta name="date" content="2026-11-07"></head>')]:
        with pytest.raises(ValueError):
            source.article(raw, FED, "federal_reserve_board", NOW, settings)


def test_expired_policy_does_not_allow_collection_or_export():
    with pytest.raises(ValueError, match="POLICY_NOT_CURRENT"):
        source.policy(lambda: "2026-12-10T00:00:00Z")


def test_first_availability_equal_second_and_duplicate_receipts(baseline, tmp_path):
    root = tmp_path / "store"
    folder, value = store.capture(parsed(), FED, "federal_reserve_board", baseline, root=root, illustrative=True, clock=lambda: NOW)
    assert not store.packet(NOW, root=root, illustrative=True, clock=lambda: NOW)["documents"]
    later = "2026-11-10T12:00:01Z"
    packet = store.packet(later, root=root, illustrative=True, clock=lambda: later)
    assert packet["documents"][0]["paragraphs"][0]["text"] == PARA
    duplicate, same = store.capture(parsed(at=later), FED, "federal_reserve_board", baseline, root=root, illustrative=True, clock=lambda: later)
    assert duplicate == folder and same["available_at"] == NOW


def test_changed_and_reverted_content_does_not_revive_prior_version(baseline, tmp_path):
    root = tmp_path / "store"
    ids = []
    for second, text in enumerate((PARA, PARA + " New hypothetical fact.", PARA)):
        at = f"2026-11-10T12:00:0{second}Z"
        _, value = store.capture(parsed(paragraph=text, at=at), FED, "federal_reserve_board", baseline, root=root, illustrative=True, clock=lambda: at)
        ids.append(value["source_id"])
    assert len(set(ids)) == 3
    assert store.packet("2026-11-10T12:00:03Z", root=root, illustrative=True, clock=lambda: "2026-11-10T12:00:03Z")["documents"][0]["source_id"] == ids[-1]
    assert store.packet("2026-11-10T12:00:01Z", root=root, illustrative=True, clock=lambda: "2026-11-10T12:00:03Z")["documents"][0]["source_id"] == ids[0]


def test_modified_source_text_and_offsets_cannot_be_exported(baseline, tmp_path):
    folder, _ = store.capture(parsed(), FED, "federal_reserve_board", baseline, root=tmp_path, illustrative=True, clock=lambda: NOW)
    (folder / "document.txt").write_text(PARA + " tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="SOURCE_MODIFIED"):
        store.validate(folder)


def test_record_change_is_detected(baseline, tmp_path):
    folder, _ = store.capture(parsed(), FED, "federal_reserve_board", baseline, root=tmp_path, illustrative=True, clock=lambda: NOW)
    value = json.loads((folder / "record.json").read_text(encoding="utf-8")); value["available_at"] = "2026-11-09T00:00:00Z"
    (folder / "record.json").write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError):
        store.validate(folder)


def test_real_text_store_cannot_be_written_outside_private_workspace(baseline, tmp_path):
    with pytest.raises(ValueError, match="PRIVATE_TEXT_STORE"):
        store.capture(parsed(), FED, "federal_reserve_board", baseline, root=tmp_path, clock=lambda: NOW)


def test_archive_cannot_backdate_retrieval(baseline, tmp_path):
    with pytest.raises(ValueError, match="CLOCK_REVERSED"):
        store.capture(parsed(at="2026-11-10T12:00:02Z"), FED, "federal_reserve_board", baseline, root=tmp_path, illustrative=True, clock=lambda: NOW)


def rss(url):
    return f'<rss><channel><item><link>{url.replace("&", "&amp;")}</link><pubDate>Sun, 08 Nov 2026 12:00:00 GMT</pubDate></item></channel></rss>'.encode()


def test_feed_rejects_entities_and_foreign_links():
    with pytest.raises(ValueError):
        source.feed_links(b'<!DOCTYPE x [<!ENTITY y "evil">]><rss/>', "us_eia", NOW, source.policy(lambda: NOW))
    assert source.feed_links(rss("https://evil.test/a"), "us_eia", NOW, source.policy(lambda: NOW)) == []


class Reply:
    def __init__(self, raw): self.raw = raw
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, maximum): return self.raw[:maximum]


class Opener:
    def __init__(self, *, robots=b"User-agent: *\nDisallow:\n", fail_articles=False):
        self.calls, self.robots, self.fail_articles = [], robots, fail_articles
    def open(self, request, timeout):
        self.calls.append(request.full_url)
        url = request.full_url
        if url.endswith("/robots.txt"): return Reply(self.robots)
        if "disclaimer" in url or "copyrights_reuse" in url: return Reply(policy_html())
        if url.endswith(".xml"): return Reply(rss(FED if "federalreserve" in url else EIA))
        if self.fail_articles: raise OSError("ILLUSTRATIVE_TRANSPORT_FAILURE")
        return Reply(html())


def collector(baseline, tmp_path, opener):
    clock = [datetime(2026, 11, 10, 12, tzinfo=timezone.utc)]
    def utc(): return clock[0].isoformat()
    def pause(seconds): clock[0] += timedelta(seconds=seconds)
    value = collect.Collector(baseline, root=tmp_path / "transport", clock=utc, pause=pause, opener=opener, illustrative=True)
    value.settings = deepcopy(value.settings)
    for profile in value.settings["sources"].values():
        profile["policy_text_sha256"] = source.policy_hash(policy_html())
    return value


def test_end_to_end_cache_reuses_original_times_without_second_http(baseline, tmp_path):
    opener = Opener(); client = collector(baseline, tmp_path, opener)
    one, _ = client.collect(store_root=tmp_path / "store", output=tmp_path / "runs")
    two, _ = client.collect(store_root=tmp_path / "store", output=tmp_path / "runs")
    assert one["stored_documents"] == two["stored_documents"] == 2
    assert one["actual_requests"] == 8 and two["actual_requests"] == 0
    assert [r["available_at"] for r in one["documents"]] == [r["available_at"] for r in two["documents"]]
    assert all(not r["impact_reviewed"] for _, r in store.records(tmp_path / "store", True))


def test_robots_disallow_prevents_feed_and_article_requests(baseline, tmp_path):
    opener = Opener(robots=b"User-agent: *\nDisallow: /\n")
    result, _ = collector(baseline, tmp_path, opener).collect(store_root=tmp_path / "store", output=tmp_path / "runs")
    assert result["stored_documents"] == 0
    assert not any(url.endswith(".xml") or "monetary2026" in url or "detail.php" in url for url in opener.calls)


def test_robots_long_crawl_delay_is_not_silently_ignored(baseline, tmp_path):
    opener = Opener(robots=b"User-agent: *\nDisallow:\nCrawl-delay: 120\n")
    result, _ = collector(baseline, tmp_path, opener).collect(store_root=tmp_path / "store", output=tmp_path / "runs")
    assert result["stored_documents"] == 0 and set(result["providers"].values()) == {"robots_delay_exceeds_bounded_run"}


def test_changed_policy_stops_before_feed_or_article(baseline, tmp_path):
    opener = Opener(); client = collector(baseline, tmp_path, opener)
    for p in client.settings["sources"].values(): p["policy_text_sha256"] = "0" * 64
    result, _ = client.collect(store_root=tmp_path / "store", output=tmp_path / "runs")
    assert result["stored_documents"] == 0 and len(opener.calls) == 4


def test_failed_articles_are_cached_without_body_or_retry(baseline, tmp_path):
    opener = Opener(fail_articles=True); client = collector(baseline, tmp_path, opener)
    one, _ = client.collect(store_root=tmp_path / "store", output=tmp_path / "runs")
    two, _ = client.collect(store_root=tmp_path / "store", output=tmp_path / "runs")
    assert one["stored_documents"] == 0 and two["actual_requests"] == 0
    assert all(r["reason_code"] == "OFFICIAL_TEXT_TRANSPORT_FAILURE" for r in one["documents"])


def policy_html(extra=""):
    return ('<html><p>ILLUSTRATIVE public domain policy text for software testing only. This is not an actual source license and grants no rights to real data.</p>' + extra + '</html>').encode()


def test_policy_content_change_is_detected_while_html_decoration_is_ignored():
    original = policy_html()
    assert source.policy_hash(original) == source.policy_hash(original.replace(b'<html>', b'<html data-token="rotating"><script>ignored()</script>'))
    assert source.policy_hash(original) != source.policy_hash(policy_html('<p>ILLUSTRATIVE new restriction.</p>'))


def test_eia_date_is_scoped_to_article_not_archive_navigation():
    raw = html().replace(b'<meta name="DC.date" content="2026-11-08">', b'').replace(b'<div id="article">', b'<div class="tie-article"><span class="date">November 8, 2026</span>')
    raw = raw.replace(b'</body>', b'<li class="date">October 1, 2026</li></body>')
    result = source.article(raw, EIA, "us_eia", NOW, source.policy(lambda: NOW))
    assert result["published_date"] == "2026-11-08"


def test_fed_article_time_date_is_recognized_without_inventing_exact_time():
    raw = html().replace(b'<meta name="DC.date" content="2026-11-08">', b'').replace(b'<h1>', b'<p class="article__time">November 8, 2026</p><h1>')
    result = source.article(raw, FED, "federal_reserve_board", NOW, source.policy(lambda: NOW))
    assert result["published_date"] == "2026-11-08" and result["published_at"] is None
    assert "November 8, 2026" not in result["text"]


def test_fed_official_heading_outside_body_provides_publication_date():
    raw = html().replace(b'<meta name="DC.date" content="2026-11-08">', b'').replace(b'<div id="article">', b'<div class="heading col-xs-12 col-sm-8 col-md-8"><p class="article__time">November 8, 2026</p></div><div id="article">')
    value = source.article(raw, FED, "federal_reserve_board", NOW, source.policy(lambda: NOW))
    assert value["published_date"] == "2026-11-08"


def test_only_robots_404_is_allowed_not_source_404_or_robots_403(baseline, tmp_path):
    from urllib.error import HTTPError
    class Errors:
        def __init__(self, code): self.code = code
        def open(self, request, timeout): raise HTTPError(request.full_url, self.code, "ILLUSTRATIVE", {}, None)
    client = collector(baseline, tmp_path, Errors(404))
    robots = client.cached('https://www.eia.gov/robots.txt', 'us_eia', 'robots', lambda raw, _: raw)
    article = client.cached(EIA, 'us_eia', 'article', lambda raw, _: raw)
    assert robots['status'] == 'available' and article['status'] == 'unavailable'
    other = collector(baseline, tmp_path / 'other', Errors(403))
    assert other.cached('https://www.eia.gov/robots.txt', 'us_eia', 'robots', lambda raw, _: raw)['status'] == 'unavailable'
