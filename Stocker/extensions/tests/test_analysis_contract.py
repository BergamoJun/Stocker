"""ILLUSTRATIVE - contract checks, no model or live market inputs."""

from copy import deepcopy
import hashlib
import json
import pytest
import jsonschema
from extensions import analysis_contract as contract

CUTOFF = "2026-11-10T12:00:00Z"


def data():
    identity = {"cik": "0000001001", "symbol": "DEMO", "name": "ILLUSTRATIVE"}
    fact = {"fact_id": "fin_fixture_revenue", "cik": identity["cik"], "metric": "revenue", "value": "1000", "unit": "USD", "basis": "quarter", "start": "2026-07-01", "end": "2026-09-30", "scope": "consolidated",
        "available_at": "2026-11-09T11:00:00Z", "validation_kind": "recorded_source_review", "source_refs": [{"source_id": "sec_fixture", "url": "https://www.sec.gov/Archives/edgar/data/1001/fixture.htm", "raw_sha256": "b" * 64,
        "locator": {"fact_id": "fixture", "dom_id": None, "tag": "us-gaap:Revenues", "context_ref": "fixture", "row_number": 1, "source_sha256": "b" * 64}}]}
    text = "ILLUSTRATIVE source evidence. A hypothetical policy can have different effects across companies."
    doc = {"source_id": "text_fixture", "source": "us_eia", "url": "https://www.eia.gov/todayinenergy/detail.php?id=12345", "title": "ILLUSTRATIVE", "published_date": "2026-11-08", "published_at": None, "time_precision": "date",
        "available_at": "2026-11-09T11:00:00Z", "record_sha256": "c" * 64, "text_sha256": hashlib.sha256(text.encode()).hexdigest(), "attribution": "ILLUSTRATIVE", "age_days": 2, "within_seven_calendar_days": True,
        "untrusted_source_text": True, "impact_reviewed": False, "paragraphs": [{"id": "p0001", "start": 0, "end": len(text), "sha256": hashlib.sha256(text.encode()).hexdigest(), "selector": "fixture/p[1]", "text": text}]}
    local = {"market": {"ILLUSTRATIVE_price": "987654321.123"}, "news": {"title": "ILLUSTRATIVE_VENDOR_HEADLINE"}}
    return identity, [fact], [doc], local


def inputs():
    i, f, d, local = data()
    return contract.assemble(i, CUTOFF, f, d, local, illustrative=True)


def output(ai):
    result = contract.output_template(ai)
    result.update(status="complete", model={"provider": "ILLUSTRATIVE", "model_id": "fixture", "prompt_version": "fixture-v1", "run_id": "fixture"})
    result["findings"] = [{"id": "f1", "kind": "fact", "statement": "ILLUSTRATIVE financial fact", "direction": "unknown", "horizon": "unknown", "evidence_quality": "medium",
        "evidence": [{"kind": "financial_fact", "source_id": "fin_fixture_revenue", "paragraph_id": None, "quote": None}], "numeric_support": [{"fact_id": "fin_fixture_revenue", "value": "1000", "unit": "USD"}]},
        {"id": "f2", "kind": "counterargument", "statement": "ILLUSTRATIVE effect may vary", "direction": "mixed", "horizon": "unknown", "evidence_quality": "low",
        "evidence": [{"kind": "official_paragraph", "source_id": "text_fixture", "paragraph_id": "p0001", "quote": "different effects across companies"}], "numeric_support": []}]
    return result


def test_licensed_prices_and_vendor_metadata_do_not_enter_model_payload():
    ai, local = inputs()
    raw = json.dumps(ai)
    assert "987654321.123" not in raw and "ILLUSTRATIVE_VENDOR_HEADLINE" not in raw
    assert local["external_model_input_allowed"] is False and ai["financial_facts"][0]["value"] == "1000"


def test_reserved_local_fields_cannot_override_boundary():
    i, f, d, local = data(); local["external_model_input_allowed"] = True
    with pytest.raises(ValueError, match="RESERVED"): contract.assemble(i, CUTOFF, f, d, local, illustrative=True)


def test_duplicate_financial_identity_is_rejected():
    i, f, d, local = data(); f.append(deepcopy(f[0]))
    with pytest.raises(ValueError, match="DUPLICATE"): contract.assemble(i, CUTOFF, f, d, local, illustrative=True)


@pytest.mark.parametrize("at", [CUTOFF, "2026-11-10T12:00:00.500000Z", "2026-11-10T12:00:01Z"])
def test_equal_second_and_future_receipts_cannot_be_used(at):
    i, f, d, local = data(); f[0]["available_at"] = at
    with pytest.raises(ValueError): contract.assemble(i, CUTOFF, f, d, local, illustrative=True)


def test_cross_issuer_fact_is_rejected():
    i, f, d, local = data(); f[0]["cik"] = "0000001002"
    with pytest.raises(ValueError): contract.assemble(i, CUTOFF, f, d, local, illustrative=True)


@pytest.mark.parametrize("change", ["future_period", "foreign_source", "nan", "extra_price_field"])
def test_nonfactual_or_unapproved_financial_input_is_rejected(change):
    i, f, d, local = data()
    if change == "future_period": f[0]["end"] = "2026-12-31"
    if change == "foreign_source": f[0]["source_refs"][0]["url"] = "https://vendor.example/data"
    if change == "nan": f[0]["value"] = "NaN"
    if change == "extra_price_field": f[0]["price"] = "999"
    with pytest.raises((ValueError, jsonschema.ValidationError)): contract.assemble(i, CUTOFF, f, d, local, illustrative=True)


@pytest.mark.parametrize("change", ["foreign_source", "extra_price_field", "modified_paragraph", "future_publication"])
def test_official_text_is_exact_and_scoped(change):
    i, f, d, local = data()
    if change == "foreign_source": d[0]["url"] = "https://vendor.example/article"
    if change == "extra_price_field": d[0]["price_data"] = "999"
    if change == "modified_paragraph": d[0]["paragraphs"][0]["text"] += " changed"
    if change == "future_publication": d[0]["published_date"] = "2026-12-31"
    with pytest.raises((ValueError, jsonschema.ValidationError)): contract.assemble(i, CUTOFF, f, d, local, illustrative=True)


def test_not_run_template_has_no_model_or_invented_analysis():
    ai, _ = inputs(); template = contract.output_template(ai)
    assert contract.validate_output(template, ai)["findings"] == 0
    template["findings"] = output(ai)["findings"]
    with pytest.raises(ValueError, match="NOT_RUN"): contract.validate_output(template, ai)


@pytest.mark.parametrize("field", ["packet_id", "input_sha256", "as_of"])
def test_output_binds_exact_input_and_cutoff(field):
    ai, _ = inputs(); value = output(ai)
    value[field] = "2026-11-10T11:00:00Z" if field == "as_of" else "different"
    with pytest.raises(ValueError, match="BINDING"): contract.validate_output(value, ai)


def test_complete_output_has_citations_numeric_support_and_counterargument():
    ai, _ = inputs()
    result = contract.validate_output(output(ai), ai)
    assert result["findings"] == 2 and result["semantic_truth_independently_proven"] is False


@pytest.mark.parametrize("field,value", [("value", "1001"), ("unit", "USD/shares"), ("fact_id", "missing")])
def test_numeric_output_cannot_change_input_facts(field, value):
    ai, _ = inputs(); result = output(ai); result["findings"][0]["numeric_support"][0][field] = value
    with pytest.raises(ValueError, match="NUMERIC"): contract.validate_output(result, ai)


@pytest.mark.parametrize("quote", ["invented quote", "different companies effects", ""])
def test_quote_must_be_contiguous_in_exact_paragraph(quote):
    ai, _ = inputs(); result = output(ai); result["findings"][1]["evidence"][0]["quote"] = quote
    with pytest.raises(ValueError, match="QUOTE"): contract.validate_output(result, ai)


def test_model_output_cannot_claim_human_review_or_add_price_plan():
    ai, _ = inputs()
    for field, value in [("human_reviewed", True), ("price_plan", {"target": "999"})]:
        result = output(ai); result[field] = value
        with pytest.raises(jsonschema.ValidationError): contract.validate_output(result, ai)


def test_complete_requires_counterargument_but_partial_can_record_total_withholding():
    ai, _ = inputs(); result = output(ai); result["findings"] = result["findings"][:1]
    with pytest.raises(ValueError, match="COUNTERARGUMENT"): contract.validate_output(result, ai)
    result.update(status="partial", findings=[], limitations=["ILLUSTRATIVE all claims withheld"])
    assert contract.validate_output(result, ai)["findings"] == 0


def test_free_text_cannot_be_used_to_smuggle_a_price_target():
    ai, _ = inputs(); result = output(ai); result["findings"][0]["statement"] = "ILLUSTRATIVE target price 999"
    with pytest.raises(ValueError, match="PRICE_PLAN"): contract.validate_output(result, ai)


def test_default_cutoff_is_frozen_once_before_source_reads(monkeypatch, tmp_path):
    calls = []
    def clock():
        calls.append(True)
        return "2026-11-10T12:00:00.00000" + str(len(calls)) + "Z"
    monkeypatch.setattr(contract, "historical_check", lambda *a, **k: {})
    def source(*args, **kwargs):
        assert kwargs["clock"]() == "2026-11-10T12:00:00.000001Z"
        raise RuntimeError("ILLUSTRATIVE_SOURCE_REACHED")
    monkeypatch.setattr(contract.expanded_report, "build", source)
    with pytest.raises(RuntimeError, match="SOURCE_REACHED"):
        contract.build("1001", tmp_path / "baseline.json", clock=clock, illustrative=True)
    assert len(calls) == 1


def test_replay_keeps_validated_component_ids_instead_of_generating_new_reports(monkeypatch, tmp_path):
    path = tmp_path / "baseline.json"; path.write_text("{}")
    identity = data()[0]
    base = {"report_id": "ILLUSTRATIVE_ORIGINAL_ID", "as_of": CUTOFF, "is_illustrative": True,
        "base_report": {"identity": identity, "market": {}}, "news": {}, "coverage": {"news_articles": 0, "news_highlights": 0}}
    calls = []
    monkeypatch.setattr(contract, "historical_check", lambda *a, **k: {})
    monkeypatch.setattr(contract.expanded_report, "validate", lambda value: calls.append(value["report_id"]))
    monkeypatch.setattr(contract.expanded_report, "build", lambda *a, **k: pytest.fail("must reuse validated source component"))
    monkeypatch.setattr(contract.sec_financial_context, "build", lambda *a, **k: {"sections": [], "review_coverage": {}})
    monkeypatch.setattr(contract.text_store, "packet", lambda *a, **k: {"documents": []})
    value = contract.build("1001", path, CUTOFF, base_report=base, illustrative=True, clock=lambda: CUTOFF)
    assert value["base_report"]["report_id"] == "ILLUSTRATIVE_ORIGINAL_ID" and calls == ["ILLUSTRATIVE_ORIGINAL_ID"]
    wrong = deepcopy(base); wrong["base_report"]["identity"]["cik"] = "0000001002"
    with pytest.raises(ValueError, match="COMPONENT_IDENTITY"):
        contract.build("1001", path, CUTOFF, base_report=wrong, illustrative=True, clock=lambda: CUTOFF)
