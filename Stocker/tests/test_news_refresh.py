"""ILLUSTRATIVE — NOT LIVE MARKET DATA. Free metadata refresh and pacing."""

from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse

import pytest

from research import issuer_context_demo as demo, news_refresh as refresh, issuer_news, issuer_profiles
from research.local_files import atomic_json
from research.rights import as_utc


@pytest.fixture
def case(tmp_path, monkeypatch):
    return demo.test_setup(tmp_path, monkeypatch)


def test_multiissuer_refresh_filters_company_identity_and_discards_publisher_body(case):
    result, _ = demo.refresh(case)
    assert result["massive_requests"] == 2 and result["gdelt_requests"] == 4
    contexts = [json.loads(open(row["context_path"], encoding="utf-8").read()) for row in result["issuers"]]
    assert {c["symbol"] for c in contexts} == {"BE", "DEMOC"}
    for context in contexts:
        company = [a for a in context["news"]["articles"] if "company" in a["categories"]]
        assert len(company) == 1 and context["symbol"] in company[0]["id"]
        assert "BODY_MUST_BE_DROPPED" not in json.dumps(context)
        assert not any({"description", "image_url", "insights", "body", "summary"} & set(a) for a in context["news"]["articles"])
        assert context["news"]["score_component"] is None


def test_cached_success_preserves_source_observation_and_makes_no_provider_calls(case):
    first, _ = demo.refresh(case)
    before = len(case["opener"].calls)
    case["clock"].advance(20)
    repeated, _ = demo.refresh(case)
    assert repeated["massive_requests"] == repeated["gdelt_requests"] == 0
    assert len(case["opener"].calls) == before
    for a, b in zip(first["issuers"], repeated["issuers"]):
        old = json.loads(open(a["context_path"], encoding="utf-8").read())
        new = json.loads(open(b["context_path"], encoding="utf-8").read())
        assert [r["retrieved_at"] for r in old["news"]["articles"]] == [r["retrieved_at"] for r in new["news"]["articles"]]
        assert all(p.get("cache_reused") for p in new["news"]["providers"])
        assert b["capture"]["new_versions"] == 0


def test_provider_429_shares_account_cooldown_stops_gdelt_and_is_not_empty_success(case):
    case["opener"].failure = HTTPError("https://api.massive.com/", 429, "fixture", {"Retry-After": "1200"}, None)
    result, _ = demo.refresh(case)
    assert result["massive_requests"] == result["gdelt_requests"] == 1
    assert all(r["company_status"] == "deferred" for r in result["issuers"])
    budget = json.loads((case["client"].root / "budget.json").read_text(encoding="utf-8"))
    assert as_utc(budget["blocked_until"]) > as_utc(case["clock"]())
    case["clock"].advance(2)
    view = issuer_news.snapshot("1001", case["clock"](), root=case["news_root"], profile_root=case["profile_root"], illustrative=True, clock=case["clock"])
    assert not view["snapshot"]["coverage_current_at_cutoff"]
    assert all(t["discovery_status"] == "not_verified" for t in view["snapshot"]["coverage"])
    again, _ = demo.refresh(case)
    assert again["massive_requests"] == again["gdelt_requests"] == 0


def test_plaintext_gdelt_throttle_preserves_company_news_and_unknown_other_topics(case, monkeypatch):
    original = case["opener"].open
    def response(request, timeout):
        if urlparse(request.full_url).hostname == "api.gdeltproject.org":
            case["opener"].calls.append(request.full_url)
            return demo.Response(b"Please limit requests to one every 5 seconds")
        return original(request, timeout)
    monkeypatch.setattr(case["opener"], "open", response)
    result, _ = demo.refresh(case)
    assert result["gdelt_requests"] == 1
    assert all(r["company_status"] == "links_available" and r["sector_status"] == "deferred" for r in result["issuers"])
    context = json.loads(open(result["issuers"][0]["context_path"], encoding="utf-8").read())
    assert context["news"]["topics"][0]["discovery_status"] == "query_completed_links_unreviewed"
    assert all(t["discovery_status"] == "not_verified" for t in context["news"]["topics"][1:])


def test_existing_shared_account_block_prevents_all_company_requests(case):
    atomic_json(case["client"].root / "budget.json", {"blocked_until": "2026-11-08T14:30:00Z", "requests_attempted": 9})
    result, _ = demo.refresh(case)
    assert result["massive_requests"] == 0
    assert all(r["company_status"] == "deferred" for r in result["issuers"])
    assert json.loads((case["client"].root / "budget.json").read_text(encoding="utf-8"))["requests_attempted"] == 9


def test_price_account_pacing_is_shared_and_at_least_fifteen_seconds(case, monkeypatch):
    times = []
    original = case["opener"].open
    def response(request, timeout):
        if urlparse(request.full_url).hostname == "api.massive.com":
            times.append(as_utc(case["clock"]()))
        return original(request, timeout)
    monkeypatch.setattr(case["opener"], "open", response)
    demo.refresh(case)
    assert len(times) == 2 and (times[1] - times[0]).total_seconds() >= 15


def test_project_conflict_stops_before_any_source_request(case):
    case["anchor"].write_text("changed original model", encoding="utf-8")
    with pytest.raises(ValueError, match="PROJECT_CONFLICT"):
        demo.refresh(case)
    assert case["opener"].calls == []


def test_conflict_during_refresh_stops_before_next_request_or_archive(case, monkeypatch):
    original = case["opener"].open
    def response(request, timeout):
        value = original(request, timeout)
        case["anchor"].write_text("changed during source call", encoding="utf-8")
        return value
    monkeypatch.setattr(case["opener"], "open", response)
    with pytest.raises(ValueError, match="PROJECT_CONFLICT"):
        demo.refresh(case)
    assert len(case["opener"].calls) == 1
    assert not list(case["news_root"].rglob("captures/*.json"))


def test_usage_rights_are_checked_before_company_request(case):
    case["client"].record["uses"]["derived_analytics"] = "denied"
    result, _ = demo.refresh(case)
    assert result["massive_requests"] == 0
    assert all(r["company_status"] == "unavailable" for r in result["issuers"])
    assert all(urlparse(u).hostname != "api.massive.com" for u in case["opener"].calls)


def payload(**changes):
    row = {"tickers": ["MSFT"], "article_url": "https://example.com/article", "title": "ILLUSTRATIVE", "published_utc": "2026-11-08T12:00:00Z", "publisher": {"name": "fixture"}}
    row.update(changes)
    return json.dumps({"status": "OK", "results": [row]}).encode()


@pytest.mark.parametrize("change", [{"tickers": ["OTHER"]}, {"article_url": "javascript:alert(1)"}, {"published_utc": "2026-11-09T00:00:00Z"}])
def test_invalid_provider_records_are_partial_not_successful_empty_queries(change):
    value = refresh.normalize_massive(payload(**change), {"symbol": "MSFT"}, "2026-11-07T00:00:00Z", "2026-11-08T13:00:00Z", "2026-11-08T13:00:01Z")
    assert value["status"] == "partial" and value["dropped_records"] == 1 and value["articles"] == []


def test_empty_result_is_explicit_bounded_query_and_pagination_is_partial():
    args = ({"symbol": "MSFT"}, "2026-11-07T00:00:00Z", "2026-11-08T13:00:00Z", "2026-11-08T13:00:01Z")
    value = refresh.normalize_massive(b'{"status":"OK","results":[]}', *args)
    assert value["status"] == "bounded_no_links"
    row = json.loads(payload()); row["next_url"] = "https://api.massive.com/next?apiKey=SHOULD_NOT_KEEP"
    value = refresh.normalize_massive(json.dumps(row).encode(), *args)
    assert value["status"] == "partial" and "SHOULD_NOT_KEEP" not in json.dumps(value)


def test_cached_failure_keeps_original_attempt_timestamp(tmp_path):
    at = ["2026-11-08T13:00:00Z"]
    calls = []
    def fail():
        calls.append(1)
        return {"status": "unavailable", "checked_at": at[0], "articles": []}
    first = refresh.cached(["failure"], fail, cache_root=tmp_path, illustrative=True, clock=lambda: at[0])
    at[0] = "2026-11-08T13:05:00Z"
    second = refresh.cached(["failure"], fail, cache_root=tmp_path, illustrative=True, clock=lambda: at[0])
    assert calls == [1] and second["checked_at"] == first["checked_at"]
    assert second["cache_reused"]


def test_active_pinned_observer_has_priority_over_news_requests(case):
    from research.context_transport import Client
    from research.validation import digest
    from research.panel_transport import Deferred
    job_path = case["folder"] / "isolated_project/reports/private/observation_jobs/fixture/job.json"
    atomic_json(job_path, {"version": "ILLUSTRATIVE"})
    baseline = json.loads(case["baseline"].read_text(encoding="utf-8"))
    baseline["jobs"] = [str(job_path)]
    baseline["files"][str(job_path.relative_to(Path(baseline["root"]))).replace("\\", "/")] = digest(job_path)
    target = case["folder"] / "observer_baseline.json"
    atomic_json(target, baseline)
    lock = job_path.with_name("worker.lock")
    lock.write_text("ILLUSTRATIVE active observer", encoding="utf-8")
    old = case["client"]
    client = Client(old.record, old.key, target, root=old.root, gdelt_root=old.gdelt_root, clock=old.clock, sleep=old.sleep, opener=old.opener)
    with pytest.raises(Deferred):
        client.massive("BE", "2026-11-07T00:00:00Z", case["clock"]())
    assert client.massive_requests == 0
    lock.unlink()
    assert client.massive("BE", "2026-11-07T00:00:00Z", case["clock"]())["raw"]
    assert client.massive_requests == 1
