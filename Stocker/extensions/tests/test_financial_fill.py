"""ILLUSTRATIVE - filing fixtures; no live prices or external API."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import pytest

from extensions import financial_fill as fill, analysis_contract
from research import sec_fact_review as reviews, sec_filing_evidence as evidence
from research.local_files import atomic_json
from research.rights import as_utc

CIK = "0002115436"
AT = "2026-11-10T12:00:00Z"
FILING = {"accession": "0002115436-26-000020", "form": "10-Q", "report_date": "2026-09-30"}


def document():
    rule = fill.policy(AT)["xom_sales"]
    contexts, rows = [], []
    for i, role in enumerate(("sales", "affiliates", "other", "total")):
        member = '<xbrli:segment><xbrldi:explicitMember dimension="' + rule["axis"] + '">' + rule[role + "_member"] + '</xbrldi:explicitMember></xbrli:segment>' if role != "total" else ""
        contexts.append(f'<xbrli:context id="c{i}"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{CIK}</xbrli:identifier>{member}</xbrli:entity><xbrli:period><xbrli:startDate>2026-07-01</xbrli:startDate><xbrli:endDate>2026-09-30</xbrli:endDate></xbrli:period></xbrli:context>')
        number = ("100", "2", "3", "105")[i]
        rows.append(f'<tr><td>{rule[role + "_label"]}</td><td><ix:nonFraction id="f{i}" name="us-gaap:Revenues" contextRef="c{i}" unitRef="usd" scale="6">{number}</ix:nonFraction></td></tr>')
    return ('<html>ILLUSTRATIVE<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>' + "".join(contexts) + '<table>' + "".join(rows) + '</table></html>').encode()


def selections(raw=None, *, metric="revenue", issuer=CIK, facts=None):
    raw = raw or document()
    facts = facts or evidence.parse(raw, hashlib.sha256(raw).hexdigest(), issuer)
    return fill.select(metric, facts, fill.members(raw), FILING, reviews.policy()["metrics"][metric], fill.policy(AT), issuer)


@pytest.fixture
def case(tmp_path, monkeypatch):
    class Clock:
        def __init__(self): self.at = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
        def __call__(self): return self.at.isoformat()
        def pause(self, seconds): self.at += timedelta(seconds=seconds)
    clock = Clock(); raw = document(); path = tmp_path / "filing.htm"; path.write_bytes(raw)
    source = {"source_url": "https://www.sec.gov/Archives/edgar/data/2115436/000211543626000020/xom-20260930.htm", "raw_path": str(path), "raw_sha256": hashlib.sha256(raw).hexdigest(), "kind": "document", "available_at": "2026-11-09T10:00:00Z"}
    state = {"snapshot": {"cik": CIK, "filings": [FILING], "sources": {"sec_fixture": source}}, "bindings": {FILING["accession"]: {"source_id": "sec_fixture", "incarnation_available_at": "2026-11-09T10:00:00Z", "amendments": []}}}
    metrics = {k: {"status": "review_unresolved" if k in {"revenue", "operating_income"} else "reviewed_source_fact", "value": None if k in {"revenue", "operating_income"} else "1", "start": "2026-07-01", "end": "2026-09-30", "scope": "consolidated", "review_receipt_ids": ["ILLUSTRATIVE"], "source_locator": None} for k in reviews.policy()["metrics"]}
    context = {"cik": CIK, "symbols": ["XOM"], "entity_type": "operating", "snapshot_available_at": "2026-11-09T10:00:00Z", "sections": [{**FILING, "amendment_scope_pending": False, "metrics": metrics}], "review_coverage": {"stale_reviews": 0, "method_outdated_reviews": 0}}
    result = {"clock": clock, "state": state, "context": context, "path": path, "changed_at": None, "next_state": None, "next_context": None}
    def state_at(issuer, cutoff, **kw): return deepcopy(result["next_state"] if result["changed_at"] and as_utc(cutoff) >= as_utc(result["changed_at"]) and result["next_state"] else result["state"])
    def context_at(issuer, cutoff, **kw): return deepcopy(result["next_context"] if result["changed_at"] and as_utc(cutoff) >= as_utc(result["changed_at"]) and result["next_context"] else result["context"])
    monkeypatch.setattr(fill.reviews, "state", state_at); monkeypatch.setattr(fill.sec_financial_context, "build", context_at)
    root = tmp_path / "project"; root.mkdir(); baseline = tmp_path / "baseline.json"
    atomic_json(baseline, {"version": "1.0.0", "root": str(root), "files": {}, "allowed_modified": [], "allowed_new": [], "allowed_extensions": [], "adapter_allow_new": [], "jobs": [], "reports": [], "additional_reports": []})
    result["baseline"] = baseline; result["output"] = tmp_path / "output"
    return result


def calculate(case): return fill.compute(CIK, case["clock"](), illustrative=True)
def capture(case): return fill.capture(CIK, case["baseline"], output=case["output"], illustrative=True, clock=case["clock"])


def test_xom_sales_selects_exact_member_and_reconciles_all_four_rows():
    fact, status, definition = selections()
    assert status == "deterministic_source_match" and fact["value"] == "100000000"
    assert "excluding" in definition["scope"] and len(definition["reconciliation_fact_ids"]) == 4


@pytest.mark.parametrize("change", ["wrong_member", "extra_axis", "typed", "bad_label", "wrong_entity", "wrong_unit", "bad_total", "wrong_period", "far_rows", "hidden"])
def test_xom_exception_does_not_admit_wrong_scope_period_or_unreconciled_total(change):
    raw = document()
    if change == "wrong_member": raw = raw.replace(b"xom:SalesAndOtherOperatingRevenueMember", b"xom:UnapprovedMember")
    if change == "extra_axis": raw = raw.replace(b"</xbrli:segment>", b'<xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis">xom:Segment</xbrldi:explicitMember></xbrli:segment>', 1)
    if change == "typed": raw = raw.replace(b"explicitMember", b"typedMember")
    if change == "bad_label": raw = raw.replace(b"Sales and other operating revenue</td>", b"Segment revenue</td>")
    if change == "wrong_entity": raw = raw.replace(CIK.encode(), b"0000789019")
    if change == "wrong_unit": raw = raw.replace(b"iso4217:USD", b"iso4217:EUR")
    if change == "bad_total": raw = raw.replace(b">105</ix:", b">106</ix:")
    if change == "wrong_period": raw = raw.replace(b"2026-07-01", b"2026-01-01", 1)
    if change == "far_rows": raw = raw.replace(b"Total revenues and other income", b"".join([b"</td></tr><tr><td>" for _ in range(10)]) + b"Total revenues and other income")
    if change == "hidden": raw = raw.replace(b'<table>', b'<ix:hidden><table>').replace(b'</table>', b'</table></ix:hidden>')
    fact, status, _ = selections(raw)
    assert fact is None and status != "deterministic_source_match"


def test_conflicting_same_member_values_are_withheld():
    raw = document(); facts = evidence.parse(raw, hashlib.sha256(raw).hexdigest(), CIK)
    other = deepcopy(facts[0]); other.update(value="999", ordinal=9); facts.append(other)
    assert selections(raw, facts=facts)[1] == "conflicting_source_values"


@pytest.mark.parametrize("raw", [b'<xbrli:context id="a"></xbrli:context><xbrli:context id="a"></xbrli:context>', b'<xbrli:context id="a">', b'<!ENTITY external SYSTEM "file:///secret">'])
def test_dimension_parser_rejects_ambiguous_or_unterminated_contexts(raw):
    with pytest.raises(ValueError): fill.members(raw)


def test_automatic_fact_is_distinct_from_original_review_and_keeps_exact_locator(case):
    value = calculate(case)
    assert value["coverage"] == {"expected_metrics": 9, "recorded_reviewed_metrics": 7, "deterministic_matched_metrics": 1, "withheld_metrics": 1}
    fact = value["facts"][0]
    assert fact["validation_kind"] == "deterministic_source_match" and fact["source_refs"][0]["locator"]["context_ref"] == "c0"
    assert next(g for g in value["gaps"] if g["metric"] == "operating_income")["status"] == "explicit_review_unresolved"
    assert next(g for g in value["gaps"] if g["metric"] == "qoq_ttm")["status"] == "missing_same_cik_annual_history"


@pytest.mark.parametrize("change", ["amendment", "conflict", "stale", "outdated", "equal_second", "future_period"])
def test_pending_conflicting_stale_and_future_inputs_cannot_clear_gap(case, change):
    section = case["context"]["sections"][0]
    if change == "amendment": section["amendment_scope_pending"] = True
    if change == "conflict": section["metrics"]["revenue"]["status"] = "conflicting_reviews"
    if change == "stale": case["context"]["review_coverage"]["stale_reviews"] = 1
    if change == "outdated": case["context"]["review_coverage"]["method_outdated_reviews"] = 1
    if change == "equal_second": case["state"]["snapshot"]["sources"]["sec_fixture"]["available_at"] = case["clock"]()
    if change == "future_period": case["state"]["snapshot"]["filings"] = [{**FILING, "report_date": "2027-03-31"}]
    assert calculate(case)["facts"] == []


def test_existing_review_is_not_replaced_by_automatic_rule(case):
    case["context"]["sections"][0]["metrics"]["revenue"].update(status="reviewed_source_fact", value="999")
    assert calculate(case)["facts"] == []


@pytest.mark.parametrize("change", ["cik", "symbol", "entity"])
def test_financial_identity_mismatch_is_rejected(case, change):
    if change == "cik": case["context"]["cik"] = "0000789019"
    if change == "symbol": case["context"]["symbols"] = ["BE"]
    if change == "entity": case["context"]["entity_type"] = "fund"
    with pytest.raises(ValueError, match="IDENTITY"): calculate(case)


def test_overlay_first_commit_and_ai_fact_contract(case):
    value, folder = capture(case)
    with pytest.raises(ValueError, match="NOT_AVAILABLE"): fill.view(folder, case["clock"](), cik=CIK, illustrative=True)
    case["clock"].pause(1.01); result = fill.view(folder, case["clock"](), cik=CIK, illustrative=True)
    assert result["facts"][0]["available_at"] == value["available_at"]
    ai, local = analysis_contract.assemble({"cik": CIK, "symbol": "XOM", "name": "ILLUSTRATIVE"}, case["clock"](), result["facts"], [], {"financial_gaps": result["gaps"]}, illustrative=True)
    assert ai["financial_facts"][0]["value"] == "100000000" and "definition" not in ai["financial_facts"][0]
    assert "financial_gaps" in local
    with pytest.raises(ValueError, match="IDENTITY_OR_NAMESPACE"): fill.view(folder, case["clock"](), cik=CIK, illustrative=False)
    with pytest.raises(ValueError, match="IDENTITY_OR_NAMESPACE"): fill.view(folder, case["clock"](), cik="789019", illustrative=True)


def test_changed_source_bytes_cannot_replay_overlay(case):
    _, folder = capture(case); case["clock"].pause(2)
    case["path"].write_bytes(document().replace(b">100</ix:", b">101</ix:"))
    with pytest.raises(ValueError, match="SOURCE_CHANGED"): fill.view(folder, case["clock"](), cik=CIK, illustrative=True)


def test_later_review_requires_new_overlay_but_original_cutoff_stays_replayable(case):
    _, folder = capture(case); case["clock"].pause(2); historical_cutoff = case["clock"](); case["clock"].pause(2)
    case["changed_at"] = case["clock"](); case["next_context"] = deepcopy(case["context"])
    case["next_context"]["sections"][0]["metrics"]["revenue"].update(status="conflicting_reviews", review_receipt_ids=["ILLUSTRATIVE_NEW"])
    with pytest.raises(ValueError, match="RECAPTURE_REQUIRED"): fill.view(folder, case["clock"](), cik=CIK, illustrative=True)
    assert len(fill.view(folder, historical_cutoff, cik=CIK, illustrative=True)["facts"]) == 1


def test_tampering_with_saved_overlay_is_rejected(case):
    value, folder = capture(case); case["clock"].pause(2)
    value["payload"]["facts"][0]["value"] = "999"
    (folder / "record.json").write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="RECORD_CHANGED"): fill.view(folder, case["clock"](), cik=CIK, illustrative=True)


def test_standard_mapping_rejects_other_income_and_cumulative_quarter_income():
    raw = document(); facts = evidence.parse(raw, hashlib.sha256(raw).hexdigest(), CIK)
    fact = deepcopy(facts[0]); fact.update(dimensions=[], context_ref="standard", row_text="Revenues 100", same_entity=True)
    args = (FILING, reviews.policy()["metrics"]["revenue"], fill.policy(AT), "0001664703")
    assert fill.select("revenue", [fact], {"standard": []}, *args)[0] is not None
    fact["row_text"] = "Total revenues and other income 105"
    assert fill.select("revenue", [fact], {"standard": []}, *args)[0] is None
    fact.update(row_text="Revenues 100", start="2026-01-01")
    assert fill.select("revenue", [fact], {"standard": []}, *args)[0] is None
