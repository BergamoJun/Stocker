"""ILLUSTRATIVE — NOT LIVE MARKET DATA. Quarter and TTM interval contracts."""

from copy import deepcopy
from pathlib import Path
from urllib.error import HTTPError

import jsonschema
import pytest

from research import sec_chain_demo as demo, sec_chain_sources as sources, sec_chain_math as math, sec_chain_review as review, sec_chain_report as reports
from research import sec_refresh, sec_fact_review as original, sec_review_demo as old_demo
from research.local_files import atomic_json
from research.sec_report import ROOT
from research.validation import digest


@pytest.fixture
def case(tmp_path):
    value = demo.setup(tmp_path, symbols=["DEMOA"])
    value["issuer"] = "1001"
    return value


def sheet(case):
    return review.template(case["issuer"], cutoff=case["clock"](), packet_root=case["packet_root"], review_root=case["review_root"], source_store=case["source_store"], illustrative=True)


def submit(case, manifest=None):
    case["counter"] += 1
    path = case["folder"] / f"chain-{case['counter']}.json"
    atomic_json(path, manifest or demo.illustrative_manifest(sheet(case), case["clock"]()))
    return review.store(path, root=case["root"], packet_root=case["packet_root"], review_root=case["review_root"], source_store=case["source_store"], illustrative=True, clock=case["clock"])


def report(case, cutoff=None):
    return reports.build(case["issuer"], cutoff or case["clock"](), root=case["root"], packet_root=case["packet_root"], review_root=case["review_root"], period_root=case["period_root"], source_store=case["source_store"], illustrative=True, clock=case["clock"])


def reviewed(case):
    submit(case)
    case["clock"].advance(2)
    return report(case)


def fact(value, start, end, *, tag="us-gaap:Revenues", scope="consolidated"):
    return {"value": str(value), "start": start, "end": end, "unit": "USD", "scope": scope, "tag": tag, "source_id": "fixture:source", "fact_id": "fixture:fact"}


def legs():
    return {"current_income_quarter": fact(450, "2026-07-01", "2026-09-30"), "previous_income_quarter": fact(300, "2026-04-01", "2026-06-30"),
        "current_ytd": fact(1000, "2026-01-01", "2026-09-30"), "previous_ytd": fact(550, "2026-01-01", "2026-06-30"), "earlier_ytd": fact(250, "2026-01-01", "2026-03-31"),
        "annual": fact(1200, "2025-01-01", "2025-12-31"), "prior_year_ytd": fact(800, "2025-01-01", "2025-09-30")}


def test_income_qoq_and_ttm_periods_are_contiguous():
    result = math.calculate("revenue", legs(), False)
    assert result["qoq"]["percent_change"] == "50.000000"
    assert result["ttm"]["value"] == "1400" and result["ttm"]["start"] == "2025-10-01" and result["ttm"]["end"] == "2026-09-30"
    assert result["current_quarter"]["kind"] == "direct_reported_quarter"


def test_quarter_cashflow_uses_ytd_difference_not_ytd_as_quarter():
    values = legs()
    values["current_ytd"]["value"] = "140"
    values["previous_ytd"]["value"] = "90"
    values["earlier_ytd"]["value"] = "40"
    result = math.calculate("operating_cash_flow", values, False)
    assert result["current_quarter"]["value"] == result["previous_quarter"]["value"] == "50"
    assert result["current_quarter"]["start"] == "2026-07-01" and result["previous_quarter"]["start"] == "2026-04-01"


def test_fiscal_q4_and_first_quarter_have_correct_dates():
    values = {"current_income_quarter": fact(400, "2026-07-01", "2026-09-30"), "current_ytd": fact(400, "2026-07-01", "2026-09-30"),
        "annual": fact(1200, "2025-07-01", "2026-06-30"), "prior_year_ytd": fact(300, "2025-07-01", "2025-09-30"), "earlier_ytd": fact(900, "2025-07-01", "2026-03-31")}
    result = math.calculate("revenue", values, True)
    assert result["previous_quarter"]["value"] == "300" and result["previous_quarter"]["start"] == "2026-04-01"
    assert result["ttm"]["value"] == "1300" and result["ttm"]["start"] == "2025-10-01"


@pytest.mark.parametrize("metric", ["diluted_eps", "cash", "assets", "liabilities"])
def test_non_additive_metrics_are_never_subtracted_or_summed(metric):
    result = math.calculate(metric, legs(), False)
    assert result["qoq"] is result["ttm"] is None
    assert result["ttm_status"] == "non_additive_metric_not_supported"


@pytest.mark.parametrize("change", ["unit", "scope", "tag"])
def test_cross_basis_components_are_rejected(change):
    values = legs()
    values["annual"][change] = {"unit": "EUR", "scope": "parent_attributable", "tag": "us-gaap:SalesRevenueNet"}[change]
    result = math.calculate("revenue", values, False)
    assert result["qoq"] is result["ttm"] is None


def test_income_overlap_difference_withholds_qoq_without_changing_raw_values():
    values = legs()
    values["previous_ytd"]["value"] = "551"
    result = math.calculate("revenue", values, False)
    assert result["qoq"] is None and "overlap" in result["qoq_status"]
    assert result["current_quarter"]["value"] == "450" and result["ttm"]["value"] == "1400"


@pytest.mark.parametrize("change", ["gap", "prior_start", "partial_annual"])
def test_ttm_never_fills_period_gaps(change):
    values = legs()
    if change == "gap": values["current_ytd"]["start"] = "2026-01-02"
    if change == "prior_start": values["prior_year_ytd"]["start"] = "2025-04-01"
    if change == "partial_annual": values["annual"]["start"] = "2025-04-01"
    assert math.calculate("revenue", values, False)["ttm"] is None


def test_missing_components_do_not_become_zero():
    values = legs()
    values.pop("prior_year_ytd")
    result = math.calculate("revenue", values, False)
    assert result["ttm"] is None and result["qoq"] is not None


def test_cumulative_capex_decrease_is_not_negative_spending_recommendation():
    values = legs()
    values["current_ytd"]["value"] = "10"
    result = math.calculate("capital_expenditure", values, False)
    assert result["current_quarter"] is None and "negative" in result["qoq_status"]


def test_annual_data_is_never_divided_by_four():
    values = legs()
    values["earlier_ytd"]["end"] = "2026-06-30"
    result = math.calculate("revenue", values, True)
    assert result["previous_quarter"] is None


def test_supplemental_sources_are_immutable_and_repeated_capture_uses_cache(case):
    before = case["client"].network_requests
    packet = sources.as_of("1001", case["clock"](), root=case["packet_root"], illustrative=True)
    repeated = sources.capture("1001", root=case["packet_root"], source_store=case["source_store"], client=case["client"], illustrative=True, clock=case["clock"])
    assert repeated["status"] == "unchanged" and repeated["packet_id"] == packet["packet_id"]
    assert case["client"].network_requests == before


def test_source_packet_is_not_available_before_first_capture(case):
    assert sources.as_of("1001", "2025-01-01T00:00:00Z", root=case["packet_root"], illustrative=True) is None


def test_worksheet_keeps_current_anchors_and_does_not_approve_itself(case):
    value = sheet(case)
    assert value["manifest"]["template_only"] is True
    assert len(value["candidates"]["revenue"]["current_ytd"]) == 1
    with pytest.raises(ValueError, match="DRAFT"): submit(case, value["manifest"])


def test_illustrative_helper_rejects_real_data(case):
    value = sheet(case)
    value["manifest"]["is_illustrative"] = False
    with pytest.raises(ValueError, match="CANNOT_REVIEW_REAL"): demo.illustrative_manifest(value, case["clock"]())


@pytest.mark.parametrize("change", ["scope", "signature", "fact", "row", "method", "future", "extra_value"])
def test_invalid_compatibility_review_fails_closed(case, change):
    value = demo.illustrative_manifest(sheet(case), case["clock"]())
    item = value["findings"][0]
    if change == "scope": item["scope"] = "segment"
    if change == "signature": item["current_signature"] = "0" * 64
    if change == "fact": item["roles"]["annual"] = "invented"
    if change == "row": item["row_labels"]["annual"] = "invented"
    if change == "method": value["method_hashes"]["research/sec_chain_math.py"] = "0" * 64
    if change == "future": value["reviewed_at"] = "2027-01-01T00:00:00Z"
    if change == "extra_value": item["amount"] = "9999"
    with pytest.raises((ValueError, jsonschema.ValidationError)): submit(case, value)


def test_realistic_components_reach_report_with_source_arithmetic(case):
    value = reviewed(case)
    assert value["chain"]["coverage"]["qoq_calculated"] == value["chain"]["coverage"]["ttm_calculated"] == 5
    assert value["chain"]["metrics"]["revenue"]["ttm"]["value"] == "1400"
    assert value["chain"]["derived"]["ttm"]["free_cash_flow"] == "100"
    assert value["price_plan"] is value["attractiveness"] is None
    assert reports.validate(value)


def test_claimed_review_time_does_not_backdate_actual_availability(case):
    before = case["clock"]()
    manifest = demo.illustrative_manifest(sheet(case), before)
    case["clock"].advance(60)
    receipt = submit(case, manifest)
    assert report(case, before)["chain"]["coverage"]["qoq_calculated"] == 0
    assert report(case, receipt["available_at"])["chain"]["coverage"]["qoq_calculated"] == 0
    case["clock"].advance(2)
    assert report(case)["chain"]["coverage"]["qoq_calculated"] == 5


def test_duplicate_import_does_not_reset_availability(case):
    manifest = demo.illustrative_manifest(sheet(case), case["clock"]())
    a = submit(case, manifest)
    case["clock"].advance(60)
    b = submit(case, manifest)
    assert b["status"] == "already_stored" and a["available_at"] == b["available_at"]


def test_unresolved_revision_never_reuses_old_components(case):
    reviewed(case)
    manifest = demo.illustrative_manifest(sheet(case), case["clock"]())
    manifest["findings"] = manifest["findings"][:1]
    manifest["findings"][0].update(decision="unresolved", scope=None, roles={}, row_labels={}, rejected_fact_ids={})
    submit(case, manifest)
    case["clock"].advance(2)
    assert report(case)["chain"]["metrics"]["revenue"]["ttm"] is None


def test_source_change_and_reversion_do_not_revive_old_review(case):
    reviewed(case)
    before, old = case["clock"](), report(case)["chain"]
    packet = sources.as_of("1001", case["clock"](), root=case["packet_root"], illustrative=True)
    annual = packet["plan"]["filings"]["annual"]
    url, raw = annual["source_url"], case["routes"][annual["source_url"]]
    case["routes"][url] = raw.replace(b'>1200</ix:nonFraction>', b'>1201</ix:nonFraction>')
    case["clock"].advance(86401)
    sources.capture("1001", root=case["packet_root"], source_store=case["source_store"], client=case["client"], illustrative=True, clock=case["clock"])
    case["clock"].advance(2)
    assert report(case)["chain"]["coverage"]["ttm_calculated"] == 0
    case["routes"][url] = raw
    case["clock"].advance(86401)
    sources.capture("1001", root=case["packet_root"], source_store=case["source_store"], client=case["client"], illustrative=True, clock=case["clock"])
    case["clock"].advance(2)
    assert report(case)["chain"]["coverage"]["ttm_calculated"] == 0
    assert report(case, before)["chain"] == old


def test_receipt_tampering_is_detected(case):
    result = submit(case)
    path = case["root"] / "illustrative/receipts" / (result["receipt_id"] + ".json")
    value = sec_refresh.read(path)
    value["selected_components"]["revenue"]["annual"]["value"] = "9999"
    atomic_json(path, value, replace=True)
    case["clock"].advance(2)
    with pytest.raises(ValueError, match="INTEGRITY_CHANGED"): report(case)


@pytest.mark.parametrize("change", ["value", "formula", "scope", "narrative", "score"])
def test_report_replay_rejects_data_or_meaning_changes(case, change):
    value = reviewed(case)
    if change == "value": value["chain"]["metrics"]["revenue"]["ttm"]["value"] = "999"
    if change == "formula": value["chain"]["metrics"]["revenue"]["ttm"]["terms"][2]["coefficient"] = 1
    if change == "scope": value["chain"]["metrics"]["net_income"]["scope"] = "parent_attributable"
    if change == "narrative": value["limitations"] = ["Guaranteed returns"]
    if change == "score": value["attractiveness"] = 99
    with pytest.raises((ValueError, jsonschema.ValidationError)): reports.validate(value)


def test_report_generation_does_not_touch_frozen_methods_or_make_requests(case):
    before, requests = review.periods.methods(), case["client"].network_requests
    value = reviewed(case)
    folder = reports.write(value, case["folder"] / "reports")
    assert reports.validate(sec_refresh.read(folder / "report.json"))
    assert review.periods.methods() == before and case["client"].network_requests == requests


def test_korean_report_explains_direct_derived_and_ttm(case):
    text = reports.render(reviewed(case))
    assert "원문 직접 분기값" in text and "동일 회계연도 누적값 차이" in text and "연간+올해 누적−전년 누적" in text
    assert "가중평균 주식수" in text and "목표가·매력도·가격 계획: 보류" in text


def test_calendar_fiscal_and_missing_history_golden(tmp_path):
    value = demo.run(tmp_path)
    value.pop("report_paths")
    assert value == sec_refresh.read(ROOT / "examples/sec_chain_golden.json")


def amendment(case, changed=False):
    columns = case["routes"]["https://data.sec.gov/submissions/CIK0000001001.json"]["filings"]["recent"]
    acc = "0000009999-26-099999"
    for key, value in zip(columns, (acc, "10-Q/A", "2026-08-02", "2026-06-30", "amended.htm", "2026-08-02T20:00:00Z")):
        columns[key].append(value)
    original_url = next(u for u in case["routes"] if u.endswith("previous.htm") and "/1001/" in u)
    body = case["routes"][original_url]
    if changed: body = body.replace(b'>550</ix:nonFraction>', b'>551</ix:nonFraction>')
    case["routes"]["https://www.sec.gov/Archives/edgar/data/1001/" + acc.replace("-", "") + "/amended.htm"] = body
    case["clock"].advance(3601)
    sec_refresh.refresh(case["watchlist"], client=case["client"], store=case["source_store"], clock=case["clock"])
    sources.capture("1001", root=case["packet_root"], source_store=case["source_store"], client=case["client"], illustrative=True, clock=case["clock"])
    case["clock"].advance(2)


def test_amendment_needs_explicit_review_even_if_numbers_match(case):
    amendment(case)
    manifest = demo.illustrative_manifest(sheet(case), case["clock"]())
    manifest["amendment_assessments"][0]["decision"] = "unresolved"
    with pytest.raises(ValueError, match="AMENDMENT_REVIEW_UNRESOLVED"): submit(case, manifest)


def test_unchanged_selected_amended_components_have_separate_proof(case):
    amendment(case)
    result = reviewed(case)
    assert result["chain"]["coverage"]["qoq_calculated"] == 5
    assert len(result["chain"]["amendment_assessments"]) == 1
    assert "수정 공시" in reports.render(result)


def test_changed_amended_component_cannot_be_cleared_by_assertion(case):
    amendment(case, changed=True)
    with pytest.raises(ValueError, match="AMENDED_COMPONENTS_DIFFER_OR_CONFLICT"): submit(case)


def test_old_review_not_revived_after_new_amendment(case):
    reviewed(case)
    amendment(case)
    assert report(case)["chain"]["coverage"]["ttm_calculated"] == 0


def test_future_receipt_corruption_does_not_change_past_report(case):
    reviewed(case)
    cutoff, before = case["clock"](), report(case)["chain"]
    case["clock"].advance(60)
    receipt = submit(case)
    path = case["root"] / "illustrative/receipts" / (receipt["receipt_id"] + ".json")
    value = sec_refresh.read(path)
    value["selected_components"]["revenue"]["annual"]["value"] = "changed"
    atomic_json(path, value, replace=True)
    assert report(case, cutoff)["chain"] == before


def test_current_review_update_invalidates_chain_even_with_same_raw_numbers(case):
    reviewed(case)
    snapshot = sec_refresh.latest(case["source_store"], "1001")
    current = next(f for f in snapshot["filings"] if f["form"] == "10-Q" and f["report_date"] == "2026-09-30")
    worksheet = original.template("1001", current["accession"], cutoff=case["clock"](), source_store=case["source_store"], illustrative=True)
    manifest = old_demo.illustrative_manifest(worksheet, case["clock"](), metrics=["revenue"])
    path = case["folder"] / "current-revised.json"
    atomic_json(path, manifest)
    original.store(path, root=case["review_root"], source_store=case["source_store"], illustrative=True, clock=case["clock"])
    case["clock"].advance(2)
    assert report(case)["chain"]["coverage"]["qoq_calculated"] == 0


def test_source_packet_tamper_is_detected(case):
    packet = sources.as_of("1001", case["clock"](), root=case["packet_root"], illustrative=True)
    path = sources.namespace(case["packet_root"], True) / "0000001001/packets" / (packet["packet_id"] + ".json")
    value = sec_refresh.read(path)
    value["plan"]["filings"]["annual"]["report_date"] = "2025-12-30"
    atomic_json(path, value, replace=True)
    with pytest.raises(ValueError, match="SOURCE_PACKET_CHANGED"): report(case)


def test_missing_history_uses_no_cross_cik_requests(tmp_path):
    case = demo.setup(tmp_path, symbols=["DEMOC"])
    before = case["client"].network_requests
    result = sources.capture("1003", root=case["packet_root"], source_store=case["source_store"], client=case["client"], illustrative=True, clock=case["clock"])
    assert result["plan_status"] == "same_cik_annual_unavailable_cross_cik_merge_disabled" and result["network_requests"] == 0
    assert case["client"].network_requests == before


def test_known_annual_review_uncertainty_cannot_be_overridden_by_chain(case):
    reviewed(case)
    annual = sec_refresh.latest(case["source_store"], "1001")
    filing = next(f for f in annual["filings"] if f["form"] == "10-K")
    worksheet = original.template("1001", filing["accession"], cutoff=case["clock"](), source_store=case["source_store"], illustrative=True)
    manifest = old_demo.illustrative_manifest(worksheet, case["clock"](), metrics=["revenue"])
    manifest["findings"][0].update(decision="unresolved", fact_id=None, scope=None, rationale="ILLUSTRATIVE annual revenue interpretation remains unresolved")
    path = case["folder"] / "annual-unresolved.json"
    atomic_json(path, manifest)
    original.store(path, root=case["review_root"], source_store=case["source_store"], illustrative=True, clock=case["clock"])
    case["clock"].advance(2)
    value = report(case)
    assert value["chain"]["status"] == "annual_original_review_unresolved" and value["chain"]["coverage"]["ttm_calculated"] == 0


def test_request_cap_can_resume_without_repeating_cached_documents(case, monkeypatch):
    settings = sources.policy()
    settings["maximum_document_requests_per_issuer"] = 1
    monkeypatch.setattr(sources, "policy", lambda: settings)
    case["clock"].advance(86401)
    first = sources.capture("1001", root=case["packet_root"], source_store=case["source_store"], client=case["client"], illustrative=True, clock=case["clock"])
    assert first["network_requests"] == 1 and first["missing_count"] > 0
    for _ in range(4):
        case["clock"].advance(2)
        later = sources.capture("1001", root=case["packet_root"], source_store=case["source_store"], client=case["client"], illustrative=True, clock=case["clock"])
        assert later["network_requests"] <= 1
    assert later["document_count"] == 4 and later["missing_count"] == 0


def test_source_429_is_shared_cooldown_not_a_retry_storm(case):
    packet = sources.as_of("1001", case["clock"](), root=case["packet_root"], illustrative=True)
    url = packet["plan"]["filings"]["current"]["source_url"]
    case["routes"][url] = HTTPError(url, 429, "fixture", {"Retry-After": "1800"}, None)
    case["clock"].advance(86401)
    first = sources.capture("1001", root=case["packet_root"], source_store=case["source_store"], client=case["client"], illustrative=True, clock=case["clock"])
    case["clock"].advance(2)
    second = sources.capture("1001", root=case["packet_root"], source_store=case["source_store"], client=case["client"], illustrative=True, clock=case["clock"])
    assert first["network_requests"] == 1 and second["network_requests"] == 0


def test_conflicting_review_recipes_are_not_resolved_by_latest_wins(case):
    reviewed(case)
    manifest = demo.illustrative_manifest(sheet(case), case["clock"]())
    manifest["reviewer_id"] = "ILLUSTRATIVE_SECOND_REVIEWER"
    manifest["findings"] = manifest["findings"][:1]
    item = manifest["findings"][0]
    for key in ("roles", "row_labels", "rejected_fact_ids"): item[key].pop("annual")
    submit(case, manifest)
    case["clock"].advance(2)
    value = report(case)["chain"]["metrics"]["revenue"]
    assert value["review_status"] == "conflicting_chain_reviews" and value["ttm"] is value["qoq"] is None


def test_previous_income_overlap_also_requires_exact_reconciliation():
    values = legs()
    values["earlier_ytd"]["value"] = "249"
    result = math.calculate("revenue", values, False)
    assert result["qoq"] is None and "overlap" in result["qoq_status"]
    assert result["ttm"]["value"] == "1400"
