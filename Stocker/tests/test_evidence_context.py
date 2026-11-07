"""ILLUSTRATIVE — NOT LIVE MARKET DATA. Offline context and disclosure checks."""

from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import jsonschema
import pytest

from research import context_report, evidence_context, fetch_context, fetch_prices, massive_news, news, report_language
from research.sec_report import ROOT
from research.swing_models import XnysCalendar

AT = "2026-11-07T12:00:00Z"
LABEL = "ILLUSTRATIVE — NOT LIVE MARKET DATA"


def company_metadata(**changes):
    item = {"tickers": ["BE"], "article_url": "https://example.com/article?utm_source=fixture", "title": LABEL,
        "publisher": {"name": "Illustrative publisher"}, "published_utc": "2026-11-07T10:00:00Z",
        "description": "ARTICLE BODY MUST NOT BE KEPT", "insights": [{"sentiment": "positive"}], "image_url": "https://example.com/image"}
    item.update(changes)
    raw = json.dumps({"status": "OK", "results": [item]}).encode()
    return massive_news.normalize(raw, AT, "2026-11-04T12:00:00Z", AT)


def gdelt_metadata(*, failed=False, category="sector"):
    entitlement = json.loads((ROOT / "config/source_entitlements.json").read_text(encoding="utf-8"))["records"][0]
    def request(_):
        if failed:
            raise OSError("Illustrative API failure")
        return {"articles": [{"url": "https://example.com/article", "title": LABEL, "domain": "example.com", "seendate": "20261107T100000Z"}]}
    return news.collect_news(entitlement, topics=[category], request=request, sleep=lambda _: None, now=lambda: AT)


def test_basic_news_discards_bodies_sentiment_images_and_does_not_verify_publication():
    result = company_metadata()
    assert result["status"] == "links_available"
    assert result["raw_payload_retained"] is False
    assert result["summary_sentiment_and_article_text_retained"] is False
    row = result["articles"][0]
    assert row["published_at"] is row["event_at"] is None
    assert row["score_eligible"] is False
    assert row["url"] == "https://example.com/article"
    assert not {"description", "insights", "image_url"} & set(row)
    assert "ARTICLE BODY MUST NOT BE KEPT" not in json.dumps(result)


@pytest.mark.parametrize("changes", [{"tickers": ["OTHER"]}, {"published_utc": "2026-11-08T00:00:00Z"}, {"article_url": "javascript:alert(1)"}])
def test_invalid_provider_records_are_failed_coverage_not_an_empty_success(changes):
    result = company_metadata(**changes)
    assert result["status"] == "unavailable" and result["dropped_records"] == 1
    context = evidence_context.news_context(None, result, AT, fetch_context.policy())
    assert context["topics"][0]["discovery_status"] == "not_verified"
    assert context["score_component"] is None


def test_company_link_does_not_clear_other_topics_or_score():
    context = evidence_context.news_context(gdelt_metadata(failed=True), company_metadata(), AT, fetch_context.policy())
    assert context["topics"][0]["fresh_link_count"] == 1
    assert all(row["discovery_status"] == "not_verified" for row in context["topics"][1:])
    assert context["all_topics_discovered"] is context["all_impacts_reviewed"] is False
    assert context["score_component"] is None


def test_duplicate_link_keeps_both_topic_associations_and_one_article():
    context = evidence_context.news_context(gdelt_metadata(), company_metadata(), AT, fetch_context.policy())
    assert len(context["articles"]) == 1
    row = context["articles"][0]
    assert set(row["categories"]) == {"company", "sector"}
    assert set(row["providers"]) == {"gdelt_project", "massive"}
    assert len(row["source_ids"]) == 2
    assert row["first_seen_at"] is not None and row["provider_published_at"] is not None
    assert row["published_at"] is None and row["score_eligible"] is False
    assert context["topics"][0]["highlight_ids"] == context["topics"][1]["highlight_ids"]


def test_old_metadata_links_are_retained_but_do_not_claim_fresh_coverage():
    context = evidence_context.news_context(None, company_metadata(), "2026-11-07T12:31:00Z", fetch_context.policy())
    assert len(context["articles"]) == 1
    assert context["status"] == "unavailable_or_stale"
    assert not any(row["highlight_ids"] for row in context["topics"])
    assert context["topics"][0]["discovery_status"] == "not_verified"


def test_new_company_observation_does_not_refresh_an_old_sector_query():
    old = gdelt_metadata()
    old["checked_at"] = "2026-11-07T11:29:00Z"
    old["articles"][0]["retrieved_at"] = old["checked_at"]
    context = evidence_context.news_context(old, company_metadata(), AT, fetch_context.policy())
    assert len(context["articles"]) == 1
    assert context["topics"][0]["highlight_ids"]
    assert context["topics"][1]["highlight_ids"] == []
    assert context["topics"][1]["discovery_status"] == "not_verified"


def test_invalid_metadata_response_shape_is_rejected():
    with pytest.raises(ValueError, match="Invalid provider"):
        massive_news.normalize(b"[]", AT, "2026-11-04T12:00:00Z", AT)


def test_future_or_preapproved_company_metadata_is_rejected():
    with pytest.raises(ValueError, match="after cutoff"):
        evidence_context.news_context(None, company_metadata(), "2026-11-07T11:59:00Z", fetch_context.policy())
    data = company_metadata()
    data["articles"][0]["score_eligible"] = True
    with pytest.raises(ValueError, match="cannot assert"):
        evidence_context.news_context(None, data, AT, fetch_context.policy())


def test_other_ticker_and_observation_after_provider_check_are_rejected():
    data = company_metadata()
    data["symbol"] = "OTHER"
    with pytest.raises(ValueError, match="scope differs"):
        evidence_context.news_context(None, data, AT, fetch_context.policy())
    data = company_metadata()
    data["articles"][0]["retrieved_at"] = "2026-11-07T12:01:00Z"
    with pytest.raises(ValueError, match="observation order"):
        evidence_context.news_context(None, data, "2026-11-07T12:02:00Z", fetch_context.policy())


def test_one_invalid_supplement_does_not_discard_valid_company_links(tmp_path):
    company = tmp_path / "company.json"
    company.write_text(json.dumps(company_metadata()), encoding="utf-8")
    invalid = tmp_path / "invalid_official.json"
    invalid.write_text("not JSON", encoding="utf-8")
    result = context_report.load_news_context(None, company, invalid, AT, fetch_context.policy())
    assert len(result["articles"]) == 1 and result["topics"][0]["fresh_link_count"] == 1
    assert result["invalid_input_providers"] == ["official_primary_feeds"]
    assert result["all_topics_discovered"] is False and result["score_component"] is None


def test_saved_sec_cards_have_exact_values_periods_sources_and_no_latest_claim(tmp_path):
    path = ROOT / "research_inputs/BE/2026-09-30.json"
    result = evidence_context.financial(path, AT)
    assert result["latest_filing_search_performed"] is False
    assert {row["metric"] for row in result["cards"]} == set(evidence_context.FINANCIAL)
    sources = {row["id"] for row in result["sources"]}
    for row in result["cards"]:
        assert row["question"] and row["meaning"] and row["period"] and row["unit"]
        assert row["period_ko"] and "2026년" in row["period_ko"]
        assert set(row["source_ids"]) <= sources
        assert Decimal(row["value"]).is_finite()
    altered = json.loads(path.read_text(encoding="utf-8"))
    altered["reviewed_as_of"] = "2026-11-08T00:00:00Z"
    future = tmp_path / "future_sec.json"
    future.write_text(json.dumps(altered), encoding="utf-8")
    with pytest.raises(ValueError, match="observed after"):
        evidence_context.financial(future, AT)


@pytest.mark.parametrize("symbol", ["SPY", "PBW"])
def test_etf_identity_raw_provenance_and_matching_sessions_are_required(tmp_path, symbol):
    calendar = XnysCalendar("2026-01-01", "2026-12-31")
    sessions = calendar.sessions("2026-01-02", "2026-11-06")
    payload = {"fixture_label": LABEL, "status": "OK", "ticker": symbol, "adjusted": True, "results": [
        {"t": int(datetime.fromisoformat(day).replace(tzinfo=ZoneInfo("America/New_York")).timestamp() * 1000),
            "o": 100, "h": 101, "l": 99, "c": 100, "v": 1000} for day in sessions]}
    raw = json.dumps(payload).encode()
    entitlement = fetch_prices.confirmed_record(fetch_prices.source_record(), True, AT)
    data = fetch_prices.normalize(raw, entitlement, AT, "2026-01-01", "2026-11-06", calendar, symbol=symbol)
    path, rights = tmp_path / "prices.json", tmp_path / "price_entitlement.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    rights.write_text(json.dumps(entitlement), encoding="utf-8")
    path.with_name("raw_response.json").write_bytes(raw)
    profile = next(row for row in fetch_context.policy()["proxies"] if row["symbol"] == symbol)
    result = evidence_context.price_context(profile, path, rights, "2026-11-06", AT)
    assert result["return_forecast"] is None and result["total_return_including_dividends"] is False
    assert result["five_session_price_change_pct"] == "0"
    assert result["close_vs_twenty_session_mean"] == "equal"
    assert data["instrument_type"] == "etf" and data["listing_exchange"] == "ARCX"
    with pytest.raises(ValueError, match="reference session differs"):
        evidence_context.price_context(profile, path, rights, "2026-11-05", AT)
    payload["ticker"] = "OTHER"
    with pytest.raises(ValueError):
        fetch_prices.normalize(json.dumps(payload).encode(), entitlement, AT, "2026-01-01", "2026-11-06", calendar, symbol=symbol)
    path.with_name("raw_response.json").write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash differs"):
        evidence_context.price_context(profile, path, rights, "2026-11-06", AT)


def test_context_symbols_are_a_reviewed_allowlist():
    with pytest.raises(ValueError):
        fetch_prices.request_url("2026-01-01", "2026-11-06", symbol="UNREVIEWED")
    assert "/ticker/BE/" in fetch_prices.request_url("2026-01-01", "2026-11-06")


def report_fixture():
    settings = fetch_context.policy()
    return {"schema_version": "1.0.0", "status": "research_only_unvalidated", "as_of": AT, "symbol": "BE",
        "is_illustrative": False, "context": {"as_of": AT, "policy_id": settings["id"], "financial": {"status": "unavailable"},
            "price_context": [{"status": "unavailable", "role": row["role"], "symbol": row["symbol"]} for row in settings["proxies"]],
            "news": evidence_context.news_context(None, company_metadata(), AT, settings)},
        "price_readiness": {"screen": None}, "journal": {"status": "withheld"}, "outcomes": {"base_forecasts": [], "context_records": []},
        "registration": {"fixture_label": LABEL}, "released_score": None, "released_price_plan": None,
        "released_expected_target_sessions": None, "validation_id": None, "external_llm_calls": 0, "llm_tokens": 0,
        "full_recommendation_ready": False, "real_ml_fit_performed": False}


@pytest.mark.parametrize("mutation", ["score", "news_score", "article_score", "ml_fit", "partial_topics"])
def test_integrated_contract_cannot_promote_links_or_research_into_approval(mutation):
    data = report_fixture()
    context_report.validate(data)
    if mutation == "score":
        data["released_score"] = 80
    elif mutation == "news_score":
        data["context"]["news"]["score_component"] = "40"
    elif mutation == "article_score":
        data["context"]["news"]["articles"][0]["score_eligible"] = True
    elif mutation == "ml_fit":
        data["real_ml_fit_performed"] = True
    else:
        data["context"]["news"]["topics"].pop()
    with pytest.raises((ValueError, jsonschema.ValidationError)):
        context_report.validate(data)


def test_beginner_report_names_questions_unknown_coverage_and_original_link():
    text = context_report.markdown(report_fixture())
    assert "어떤 내용을 평가했나요?" in text
    assert "위험이 없다는 뜻은 아닙니다" in text
    assert "https://example.com/article" in text
    assert "0건" in text
    assert "NONPOSITIVE" not in text
    assert report_language.percent("-0.0009") == "0.01% 미만의 손실"
    assert report_language.percent(None) == "확인되지 않음"
    assert "&lt;script&gt;" in report_language.escaped("<script>fake</script>")


def test_cost_report_explains_the_model_and_units_without_internal_reason_codes():
    screen = {"reference_session": "2026-11-06", "screen_decision": "stand_aside",
        "reason_codes": ["NONPOSITIVE_ESTIMATED_PATH_EV"], "metrics": [{"cost_bps_per_side": "25",
        "net_reference_to_target_pct": "0.2", "net_reward_risk": "0.5", "estimated_path_ev_pct_bounds": ["-0.0009", "0.1"]}]}
    text = report_language.trade_markdown({"as_of": AT, "screen": screen})
    assert "1bp는 0.01%" in text and "손익비 1은" in text
    assert "실제로 예측을 검증한 횟수가 아닙니다" in text
    assert "0.01% 미만의 손실" in text and "-0.00%" not in text
    assert "NONPOSITIVE_ESTIMATED_PATH_EV" not in text


def test_missing_financials_and_bad_news_still_produce_an_explicit_partial_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(context_report, "ROOT", tmp_path)
    for name in ("config/context_policy.json", "config/news_review_policy.json", "config/official_news_entitlements.json", *("research/" + name for name in context_report.CONTEXT_CODE_FILES)):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    schema = tmp_path / "schemas/evidence_report.schema.json"
    schema.parent.mkdir()
    schema.write_bytes((ROOT / "schemas/evidence_report.schema.json").read_bytes())
    def no_prices():
        raise ValueError("Illustrative missing proxy")
    monkeypatch.setattr(fetch_context, "latest", lambda _: no_prices())
    monkeypatch.setattr(context_report, "now", lambda: AT)
    def readiness(argv):
        path = Path(argv[1]) / "illustrative/trade_readiness.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"forecast": None, "screen": None, "fixture_label": LABEL}), encoding="utf-8")
    monkeypatch.setattr(context_report.trade_readiness, "main", readiness)
    bad = tmp_path / "bad_news.json"
    bad.write_text("not JSON", encoding="utf-8")
    output = tmp_path / "reports/private/context_report"
    assert context_report.main(["--sec-snapshot", str(tmp_path / "missing_sec.json"), "--company-news", str(bad), "--output-dir", str(output)]) == 0
    result = json.loads(next(output.glob("*/integrated_readiness.json")).read_text(encoding="utf-8"))
    assert result["context"]["financial"]["status"] == "unavailable"
    assert result["context"]["news"]["status"] == "unavailable_or_stale"
    assert result["context"]["news"]["validation_reason"]
    assert result["journal"]["status"] == "withheld"
    assert result["released_score"] is None and result["full_recommendation_ready"] is False
    assert json.loads(capsys.readouterr().out)["fresh_news_links"] == 0
