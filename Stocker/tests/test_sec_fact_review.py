"""ILLUSTRATIVE — NOT LIVE MARKET DATA. Source review and report boundaries."""

from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import jsonschema
import pytest

from research import sec_fact_review as review, sec_financial_context as financial, sec_financial_report as reports
from research import sec_filing_evidence as evidence, sec_refresh as collector, sec_refresh_demo as collection_demo, sec_review_demo as demo
from research.local_files import atomic_json
from research.sec_report import ROOT
from research.sec_transport import Client
from research.validation import digest


@pytest.fixture
def case(tmp_path):
    routes, watchlist = demo.fixture()
    watchlist["entities"] = watchlist["entities"][:1]
    clock, opener = collection_demo.Clock(), collection_demo.Opener(routes)
    client = Client("Stocker fixture analyst@fixture.invalid", root=tmp_path / "http", raw_root=tmp_path / "raw", illustrative=True,
        opener=opener, clock=clock, sleep=clock.advance)
    store, root = tmp_path / "store", tmp_path / "reviews"
    collector.refresh(watchlist, client=client, store=store, clock=clock)
    clock.advance(2)
    snapshot = collector.latest(store, "1001")
    accession = next(f["accession"] for f in snapshot["filings"] if f["form"] == "10-Q")
    result = {"root": tmp_path, "source_store": store, "reviews": root, "routes": routes, "watchlist": watchlist,
        "clock": clock, "opener": opener, "client": client, "accession": accession, "counter": 0}
    result["worksheet"] = review.template("1001", accession, source_store=store, illustrative=True, cutoff=clock())
    return result


def submit(case, value=None):
    case["counter"] += 1
    path = case["root"] / (f"review-{case['counter']}.json")
    manifest = value or demo.illustrative_manifest(case["worksheet"], case["clock"]())
    atomic_json(path, manifest)
    return review.store(path, root=case["reviews"], source_store=case["source_store"], illustrative=True, clock=case["clock"])


def context(case, cutoff=None):
    return financial.build("1001", cutoff or case["clock"](), root=case["reviews"], source_store=case["source_store"], illustrative=True)


def report(case):
    return reports.build("1001", case["clock"](), root=case["reviews"], source_store=case["source_store"], illustrative=True, clock=case["clock"])


def reviewed(case):
    submit(case)
    case["clock"].advance(2)
    return next(s for s in context(case)["sections"] if s["form"] == "10-Q")


@pytest.mark.parametrize("text,attrs,expected", [("1,234.50", {"scale": "3"}, "1234500.00"), ("87,140", {"scale": "3", "sign": "-"}, "-87140000"),
    ("—", {"format": "ixt:zerodash"}, "0"), ("0.125", {"scale": "0"}, "0.125"), ("9007199254740993.125", {}, "9007199254740993.125")])
def test_numeric_transform_keeps_scale_sign_and_decimal_precision(text, attrs, expected):
    assert evidence.number(text, attrs) == expected


@pytest.mark.parametrize("text,attrs", [("1.234,56", {"format": "ixt:numcommadecimal"}), ("NaN", {}), ("1,23", {}),
    ("-2", {"sign": "-"}), ("0", {"xsi:nil": "true"}), ("10", {"continuedat": "later"}), ("4", {"scale": "99"}), ("9" * 61, {})])
def test_unsupported_number_formats_never_guess(text, attrs):
    with pytest.raises(ValueError): evidence.number(text, attrs)


def test_nested_numeric_tags_have_distinct_stable_fact_ids():
    raw = b'<html><ix:nonFraction name="us-gaap:A" id="outer"><ix:nonFraction name="us-gaap:B" id="inner">12</ix:nonFraction></ix:nonFraction></html>'
    items = evidence.parse(raw, hashlib.sha256(raw).hexdigest(), "1001")
    assert [f["value"] for f in items] == ["12", "12"]
    assert items[0]["fact_id"] != items[1]["fact_id"]


def test_entity_declarations_are_not_processed():
    with pytest.raises(ValueError, match="UNSAFE"):
        evidence.parse(b'<!ENTITY external SYSTEM "file:///secret"><html></html>', "0" * 64, "1001")


def test_duplicate_dom_id_is_not_eligible_source():
    raw = demo.inline_document("1001", "10-Q").replace(b'id="revenue"', b'id="assets"')
    items = evidence.parse(raw, hashlib.sha256(raw).hexdigest(), "1001")
    assert next(f for f in items if f["tag"] == "us-gaap:Revenues")["status"] == "duplicate_dom_id"


def test_worksheet_stays_draft_and_lists_competing_revenue_meanings(case):
    assert case["worksheet"]["manifest"]["template_only"] is True
    assert len(case["worksheet"]["candidates"]["revenue"]) == 2
    assert {f["tag"] for f in case["worksheet"]["candidates"]["net_income"]} == {"us-gaap:ProfitLoss"}
    with pytest.raises(ValueError, match="DRAFT"): submit(case, case["worksheet"]["manifest"])


def test_fixture_auto_choices_explicitly_refuse_real_sources(case):
    worksheet = deepcopy(case["worksheet"])
    worksheet["manifest"]["is_illustrative"] = False
    with pytest.raises(ValueError, match="CANNOT_REVIEW_REAL"): demo.illustrative_manifest(worksheet, case["clock"]())


@pytest.mark.parametrize("change", ["other_entity", "dimensions", "hidden", "bad_unit", "wrong_end", "annual_income", "missing_label"])
def test_candidate_requires_correct_scope_period_and_source_row(case, change):
    fact = deepcopy(case["worksheet"]["candidates"]["revenue"][0])
    spec = review.policy()["metrics"]["revenue"]
    filing = {"form": "10-Q", "report_date": "2026-09-30"}
    if change == "other_entity": fact["same_entity"] = False
    if change == "dimensions": fact["dimensions"] = ["us-gaap:ProductOrServiceAxis"]
    if change == "hidden": fact["hidden"] = True
    if change == "bad_unit": fact["unit"] = "EUR"
    if change == "wrong_end": fact["end"] = "2026-06-30"
    if change == "annual_income": fact["start"] = "2026-01-01"
    if change == "missing_label": fact["row_text"] = ""
    assert review.eligible(fact, filing, spec) is False


def test_review_must_reject_conflicting_alternative_explicitly(case):
    value = demo.illustrative_manifest(case["worksheet"], case["clock"]())
    next(f for f in value["findings"] if f["metric"] == "revenue")["rejected_fact_ids"] = []
    with pytest.raises(ValueError, match="REJECT_OTHER_CONFLICTING"): submit(case, value)


@pytest.mark.parametrize("change", ["wrong_fact", "row_label", "scope", "empty_definition", "future_time", "old_time", "method"])
def test_invalid_review_cannot_enter_report(case, change):
    value = demo.illustrative_manifest(case["worksheet"], case["clock"]())
    first = value["findings"][0]
    if change == "wrong_fact": first["fact_id"] = "ix:invented"
    if change == "row_label": first["row_label"] = "Not in the source"
    if change == "scope": first["scope"] = "segment_only"
    if change == "empty_definition": first["definition"] = ""
    if change == "future_time": value["reviewed_at"] = "2027-01-01T00:00:00Z"
    if change == "old_time": value["reviewed_at"] = "2025-01-01T00:00:00Z"
    if change == "method": value["method_hashes"]["research/sec_fact_review.py"] = "0" * 64
    with pytest.raises(ValueError): submit(case, value)


def test_unknown_fields_cannot_fabricate_an_approved_amount(case):
    value = demo.illustrative_manifest(case["worksheet"], case["clock"]())
    value["findings"][0]["value"] = "999999999"
    with pytest.raises(jsonschema.ValidationError): submit(case, value)


def test_review_import_is_idempotent_and_does_not_reset_availability(case):
    value = demo.illustrative_manifest(case["worksheet"], case["clock"]())
    first = submit(case, value)
    case["clock"].advance(10)
    second = submit(case, value)
    assert first["receipt_id"] == second["receipt_id"] and first["available_at"] == second["available_at"]
    assert second["status"] == "already_stored"


def test_review_claim_time_does_not_backdate_actual_availability(case):
    original = case["clock"]()
    value = demo.illustrative_manifest(case["worksheet"], original)
    case["clock"].advance(60)
    receipt = submit(case, value)
    assert context(case, original)["review_coverage"]["reviewed_metrics"] == 0
    assert context(case, receipt["available_at"])["review_coverage"]["reviewed_metrics"] == 0
    case["clock"].advance(2)
    assert context(case)["review_coverage"]["reviewed_metrics"] == 9


def test_reviewed_metrics_and_derived_values_reach_report(case):
    section = reviewed(case)
    assert section["metrics"]["revenue"]["value"] == "2001"
    assert section["metrics"]["net_income"]["value"] == "50"
    assert section["derived"]["free_cash_flow"]["value"] == "125"
    assert section["derived"]["free_cash_flow"]["basis"] == "cumulative_ytd"
    result = report(case)
    assert result["context"]["review_coverage"] == {"expected_metrics": 18, "reviewed_metrics": 9, "withheld_metrics": 9, "stale_reviews": 0, "method_outdated_reviews": 0}
    assert result["swing"]["attractiveness"] is result["swing"]["price_plan"] is None
    assert reports.validate(result)


def test_no_reviews_leave_all_metrics_null_without_default_score(case):
    result = report(case)
    assert all(m["value"] is None for s in result["context"]["sections"] for m in s["metrics"].values())
    assert result["observations"] == [] and result["swing"]["attractiveness"] is None


def test_unresolved_finding_cannot_silently_reuse_old_approval(case):
    reviewed(case)
    value = demo.illustrative_manifest(case["worksheet"], case["clock"](), metrics=["revenue"])
    value["findings"][0].update(decision="unresolved", fact_id=None, scope=None, rationale="ILLUSTRATIVE semantic conflict remains unresolved")
    submit(case, value)
    case["clock"].advance(2)
    section = next(s for s in context(case)["sections"] if s["form"] == "10-Q")
    assert section["metrics"]["revenue"]["value"] is None and section["metrics"]["revenue"]["status"] == "review_unresolved"
    assert section["derived"]["operating_margin_percent"]["value"] is None


def test_disagreeing_reviewers_withhold_value_instead_of_latest_wins(case):
    reviewed(case)
    value = demo.illustrative_manifest(case["worksheet"], case["clock"](), reviewer_id="ILLUSTRATIVE_SECOND", metrics=["revenue"])
    alternative = next(f for f in case["worksheet"]["candidates"]["revenue"] if f["dom_id"] == "contract_revenue")
    chosen = next(f for f in case["worksheet"]["candidates"]["revenue"] if f["dom_id"] == "revenue")
    value["findings"][0].update(fact_id=alternative["fact_id"], row_label="Revenue from customer contracts only", rejected_fact_ids=[chosen["fact_id"]], rationale="ILLUSTRATIVE deliberately conflicting reviewer interpretation")
    submit(case, value)
    case["clock"].advance(2)
    section = next(s for s in context(case)["sections"] if s["form"] == "10-Q")
    assert section["metrics"]["revenue"]["status"] == "conflicting_reviews" and section["metrics"]["revenue"]["value"] is None


def test_original_document_change_and_reversion_never_revive_approval(case):
    reviewed(case)
    original_view = context(case)
    cutoff = case["clock"]()
    binding = case["worksheet"]["manifest"]["binding"]
    url, raw = binding["source_url"], case["routes"][binding["source_url"]]
    case["routes"][url] = raw.replace(b'2001</ix:nonFraction>', b'2002</ix:nonFraction>')
    case["clock"].advance(86401)
    collector.refresh(case["watchlist"], client=case["client"], store=case["source_store"], clock=case["clock"])
    assert context(case)["review_coverage"]["reviewed_metrics"] == 0
    case["routes"][url] = raw
    case["clock"].advance(86401)
    collector.refresh(case["watchlist"], client=case["client"], store=case["source_store"], clock=case["clock"])
    assert context(case)["review_coverage"]["reviewed_metrics"] == 0
    assert context(case, cutoff) == original_view


def test_unrelated_issuer_metadata_update_does_not_expire_unchanged_filing(case):
    reviewed(case)
    payload = case["routes"]["https://data.sec.gov/submissions/CIK0000001001.json"]
    payload["name"] = "ILLUSTRATIVE RENAMED"
    case["clock"].advance(3601)
    collector.refresh(case["watchlist"], client=case["client"], store=case["source_store"], clock=case["clock"])
    assert context(case)["review_coverage"]["reviewed_metrics"] == 9


def test_new_amendment_requires_original_filing_review_again(case):
    reviewed(case)
    collection_demo.amendment(case["routes"])
    case["clock"].advance(3601)
    collector.refresh(case["watchlist"], client=case["client"], store=case["source_store"], clock=case["clock"])
    result = context(case)
    assert result["review_coverage"]["reviewed_metrics"] == 0 and result["review_coverage"]["stale_reviews"] == 1
    section = next(s for s in result["sections"] if s["form"] == "10-Q")
    assert section["amendment_scope_pending"] is True


def test_method_change_withholds_previous_review(case, monkeypatch):
    reviewed(case)
    original = review.methods()
    monkeypatch.setattr(review, "methods", lambda: {**original, "research/sec_fact_review.py": "0" * 64})
    result = context(case)
    assert result["review_coverage"]["reviewed_metrics"] == 0 and result["review_coverage"]["method_outdated_reviews"] == 1


def test_receipt_edits_are_detected(case):
    receipt = submit(case)
    path = case["reviews"] / "illustrative/receipts" / (receipt["receipt_id"] + ".json")
    value = collector.read(path)
    value["selected_evidence"]["revenue"]["value"] = "9999"
    atomic_json(path, value, replace=True)
    case["clock"].advance(2)
    with pytest.raises(ValueError, match="RECEIPT_CHANGED"): context(case)


@pytest.mark.parametrize("change", ["value", "calculation", "source_id", "observation", "narrative", "label", "score"])
def test_report_replay_rejects_unsupported_changes(case, change):
    reviewed(case)
    result = report(case)
    section = next(s for s in result["context"]["sections"] if s["form"] == "10-Q")
    if change == "value": section["metrics"]["revenue"]["value"] = "999"
    if change == "calculation": section["derived"]["free_cash_flow"]["value"] = "999"
    if change == "source_id": section["metrics"]["revenue"]["source_ids"] = ["invented"]
    if change == "observation": result["observations"][0]["text"] = "Guaranteed profit"
    if change == "narrative": result["counterpoints"] = ["Guaranteed profit"]
    if change == "label": result["label"] = "LIVE VALIDATED FORECAST"
    if change == "score": result["swing"]["attractiveness"] = 80
    with pytest.raises((ValueError, jsonschema.ValidationError)): reports.validate(result)


def test_parent_attributable_income_does_not_enter_consolidated_net_margin(case):
    section = reviewed(case)
    section["metrics"]["net_income"]["scope"] = "parent_attributable"
    assert financial.derived(section["metrics"], False)["net_margin_percent"]["value"] is None


def test_period_mismatch_and_negative_capex_withhold_free_cash_flow(case):
    section = reviewed(case)
    values = deepcopy(section["metrics"])
    values["capital_expenditure"]["start"] = "2026-07-01"
    assert financial.derived(values, False)["free_cash_flow"]["value"] is None
    values = deepcopy(section["metrics"])
    values["capital_expenditure"]["value"] = "-75"
    assert financial.derived(values, False)["free_cash_flow"]["value"] is None


def test_report_creation_is_offline_and_keeps_source_and_review_files(case):
    reviewed(case)
    paths = list(case["source_store"].rglob("*.json")) + list(case["reviews"].rglob("*.json"))
    hashes = {str(p): digest(p) for p in paths}
    before = case["client"].network_requests
    result = report(case)
    folder = reports.write(result, case["root"] / "reports")
    assert (folder / "REPORT.ko.md").is_file() and reports.validate(collector.read(folder / "report.json"))
    assert case["client"].network_requests == before and all(digest(Path(p)) == h for p, h in hashes.items())


def test_korean_report_contains_period_units_review_reasons_and_limits(case):
    reviewed(case)
    text = reports.render(report(case))
    assert "회계연도 시작부터 누적" in text and "원문 대조 기록 있음" in text and "선택 이유" in text
    assert "범위" in text and "검토 기록" in text and "가격 계획·목표가·매력도 점수는 보류" in text
    assert "ILLUSTRATIVE — NOT LIVE MARKET DATA" in text


def test_multi_issuer_review_report_demo_matches_golden(tmp_path):
    result = demo.run(tmp_path)
    result.pop("report_paths")
    assert result == collector.read(ROOT / "examples/sec_review_golden.json")


def test_net_income_tag_cannot_silently_change_its_attribution_scope(case):
    value = demo.illustrative_manifest(case["worksheet"], case["clock"](), metrics=["net_income"])
    value["findings"][0]["scope"] = "parent_attributable"
    with pytest.raises(ValueError, match="TAXONOMY_ATTRIBUTION_SCOPE_MISMATCH"): submit(case, value)


def test_future_review_corruption_cannot_change_earlier_asof_result(case):
    reviewed(case)
    cutoff = case["clock"]()
    original = context(case, cutoff)
    case["clock"].advance(60)
    later = submit(case, demo.illustrative_manifest(case["worksheet"], case["clock"](), metrics=["cash"]))
    path = case["reviews"] / "illustrative/receipts" / (later["receipt_id"] + ".json")
    value = collector.read(path)
    value["selected_evidence"]["cash"]["value"] = "changed"
    atomic_json(path, value, replace=True)
    assert context(case, cutoff) == original


def test_review_clock_reversal_never_creates_an_earlier_receipt(case):
    reviewed(case)
    case["clock"].advance(-1)
    value = demo.illustrative_manifest(case["worksheet"], case["clock"](), metrics=["cash"])
    # Move below the first receipt while keeping the original source already available.
    case["clock"].advance(-2)
    with pytest.raises(ValueError): submit(case, value)


def test_missing_past_source_and_non_operating_issuer_stay_unsupported(case):
    old = context(case, "2025-01-01T00:00:00Z")
    assert old["status"] == "source_unavailable_at_cutoff" and old["sections"] == []
    case["routes"]["https://data.sec.gov/submissions/CIK0000001001.json"]["entityType"] = "investment"
    case["clock"].advance(3601)
    collector.refresh(case["watchlist"], client=case["client"], store=case["source_store"], clock=case["clock"])
    assert context(case)["status"] == "unsupported_entity_type" and context(case)["sections"] == []


def test_no_parent_attributable_margin_or_amendment_merge_is_reported(case):
    section = reviewed(case)
    values = deepcopy(section["metrics"])
    values["net_income"]["scope"] = "parent_attributable"
    assert financial.derived(values, False)["net_margin_percent"]["status"] == "attribution_scope_mismatch"
    assert all(item["value"] is None for item in financial.derived(section["metrics"], True).values())


def test_raw_source_edits_block_existing_approval(case):
    reviewed(case)
    binding = case["worksheet"]["manifest"]["binding"]
    snapshot = collector.latest(case["source_store"], "1001")
    Path(snapshot["sources"][binding["source_id"]]["raw_path"]).write_bytes(b"modified filing")
    with pytest.raises(ValueError, match="RAW_INTEGRITY_CHANGED"): context(case)


def test_unresolved_semantic_reason_is_visible_in_report(case):
    value = demo.illustrative_manifest(case["worksheet"], case["clock"](), metrics=["revenue"])
    value["findings"][0].update(decision="unresolved", fact_id=None, scope=None, rationale="ILLUSTRATIVE total contains other income; operating revenue scope is unresolved")
    submit(case, value)
    case["clock"].advance(2)
    text = reports.render(report(case))
    assert "total contains other income" in text and "검토 후 미해결" in text
