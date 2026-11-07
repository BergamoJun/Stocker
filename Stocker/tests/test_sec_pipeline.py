"""ILLUSTRATIVE — NOT LIVE MARKET DATA.

Offline pipeline checks. Historical SEC data in research_inputs is explicitly
classified as actual filing data; mutated test copies are labelled illustrative.
No network requests or real forecast/performance claims are made by these tests.
"""

import json
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal

import pytest

from research.sec_report import (
    ROOT, SYNTHETIC_LABEL, InstrumentResolver, MetricEngine, ReportValidator,
    SourceAdapter, build_report, format_research, render_markdown, timestamp,
)

SNAPSHOT = ROOT / "research_inputs/BE/2026-09-30.json"
CUTOFF = "2026-11-06T17:14:16Z"
GENERATED = "2026-11-06T17:30:00Z"


@pytest.fixture
def loaded():
    return SourceAdapter.load(SNAPSHOT, CUTOFF)


@pytest.fixture
def input_data():
    value = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    value.update(is_illustrative=True, label=SYNTHETIC_LABEL)
    return value


def load_modified(tmp_path, data):
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return SourceAdapter.load(path, CUTOFF)


def metric_values(facts):
    metrics, _, _ = MetricEngine.calculate(facts)
    return {metric["name"]: metric["value"] for metric in metrics}


def test_real_sec_snapshot_has_explicit_identity_and_publication_times(loaded):
    snapshot, facts, sha = loaded
    identity = InstrumentResolver.resolve(snapshot, "BE")
    assert identity["cik"] == "0001664703"
    assert snapshot["is_illustrative"] is False
    assert len(facts) == 19
    assert len(sha) == 64
    q3 = next(source for source in snapshot["sources"] if source["id"] == "sec_q3_2026")
    assert q3["published_at"] == "2026-10-22T21:25:55Z"


def test_real_snapshot_normalizes_thousands_and_preserves_cash_purchase_sign(loaded):
    _, facts, _ = loaded
    assert facts["revenue_q3"].amount == Decimal("1235837000")
    assert facts["ppe_cash_flow_9m"].amount == Decimal("-231310000")
    assert facts["diluted_eps_q3"].amount == Decimal("0.78")


def test_calculations_use_compatible_periods_and_analyst_fcf(loaded):
    _, facts, _ = loaded
    values = metric_values(facts)
    assert values["revenue_q3_yoy_pct"] == 138.096862
    assert values["revenue_q3_qoq_pct"] == 16.001277
    assert values["gross_margin_q3_pct"] == 33.750729
    assert values["free_cash_flow_9m_musd"] == 41.585
    assert values["implied_q4_revenue_low_musd"] == 1347.744
    assert values["implied_q4_revenue_high_musd"] == 1547.744


def test_future_sources_are_rejected():
    with pytest.raises(ValueError, match="after as_of"):
        SourceAdapter.load(SNAPSHOT, "2026-10-22T20:30:00Z")


def test_unknown_symbol_is_not_silently_mapped_to_bloom(loaded):
    snapshot, _, _ = loaded
    with pytest.raises(ValueError, match="initial cohort|Initial cohort"):
        InstrumentResolver.resolve(snapshot, "OTHER")


def test_identity_cik_mismatch_is_rejected(loaded):
    snapshot, _, _ = loaded
    snapshot["identity"]["cik"] = "0000000001"
    with pytest.raises(ValueError, match="Initial cohort"):
        InstrumentResolver.resolve(snapshot, "BE")


@pytest.mark.parametrize("mutation", ["duplicate_source", "unknown_fact_source", "wrong_source_host",
                                     "non_finite", "wrong_period", "wrong_basis", "no_locator"])
def test_unusable_input_provenance_is_rejected(tmp_path, input_data, mutation):
    if mutation == "duplicate_source":
        input_data["sources"].append(input_data["sources"][0])
    elif mutation == "unknown_fact_source":
        input_data["facts"][0]["source_id"] = "unknown"
    elif mutation == "wrong_source_host":
        input_data["sources"][0]["url"] = "https://example.invalid/not-a-filing"
    elif mutation == "non_finite":
        input_data["facts"][0]["value"] = "NaN"
    elif mutation == "wrong_period":
        input_data["facts"][0]["period_start"] = "2026-01-01"
    elif mutation == "wrong_basis":
        input_data["facts"][0]["basis"] = "issuer_guidance"
    elif mutation == "no_locator":
        input_data["facts"][0]["locator"] = ""
    with pytest.raises(ValueError):
        load_modified(tmp_path, input_data)


def test_missing_fact_withholds_dependent_values_and_analysis(tmp_path, input_data):
    input_data["facts"] = [row for row in input_data["facts"] if row["id"] != "revenue_q3"]
    snapshot, facts, sha = load_modified(tmp_path, input_data)
    report, _ = build_report(snapshot, facts, "BE", CUTOFF, sha, GENERATED)
    values = {metric["name"]: metric["value"] for metric in report["metrics"]}
    assert values["revenue_q3_musd"] is None
    assert values["revenue_q3_yoy_pct"] is None
    assert report["summary"]["evidence_confidence"] == "low"
    assert any("missing input facts" in item for item in report["limitations"])
    assert not any("영업 규모 확대" in item["claim"] for item in report["theses"])
    assert len(report["theses"]) == 4
    assert report["swing"]["price_plan"] is None


@pytest.mark.parametrize("corruption", ["zero_denominator", "mixed_unit", "mixed_period", "wrong_capex_sign"])
def test_unsafe_arithmetic_is_withheld(loaded, corruption):
    _, facts, _ = loaded
    if corruption == "zero_denominator":
        facts["revenue_q3_prior"] = replace(facts["revenue_q3_prior"], amount=Decimal(0))
        name = "revenue_q3_yoy_pct"
    elif corruption == "mixed_unit":
        facts["revenue_q3"] = replace(facts["revenue_q3"], unit="KRW")
        name = "gross_margin_q3_pct"
    elif corruption == "mixed_period":
        facts["ocf_9m"] = replace(facts["ocf_9m"], start="2026-07-01")
        name = "free_cash_flow_9m_musd"
    else:
        facts["ppe_cash_flow_9m"] = replace(facts["ppe_cash_flow_9m"], amount=Decimal(231310000))
        name = "free_cash_flow_9m_musd"
    assert metric_values(facts)[name] is None


def test_report_contains_actual_facts_and_withholds_all_swing_numbers(loaded):
    snapshot, facts, sha = loaded
    report, audit = build_report(snapshot, facts, "BE", CUTOFF, sha, GENERATED)
    assert report["data_status"]["is_illustrative"] is False
    assert report["swing"]["status"] == "unavailable"
    assert report["swing"]["price_plan"] is None
    assert report["swing"]["attractiveness"]["score"] is None
    assert audit["llm_api_calls"] == 0
    assert audit["raw_filing_sha256"] is None
    assert {thesis["side"] for thesis in report["theses"]} >= {"bull", "bear"}
    markdown = render_markdown(report)
    assert "2026-11-07T02:14:16+09:00" in markdown
    assert "138.10" in markdown
    assert "https://www.sec.gov/Archives/edgar/data/1664703/" in markdown


@pytest.mark.parametrize("mutation", ["numeric", "citation", "source_url", "identity", "scenarios"])
def test_validator_rejects_report_drift(loaded, mutation):
    snapshot, facts, sha = loaded
    report, _ = build_report(snapshot, facts, "BE", CUTOFF, sha, GENERATED)
    if mutation == "numeric":
        report["metrics"][0]["value"] += 1
    elif mutation == "citation":
        report["theses"][0]["evidence_ids"] = ["fabricated"]
    elif mutation == "source_url":
        report["sources"][0]["url"] = "https://www.sec.gov/wrong-document"
    elif mutation == "identity":
        report["subject"]["symbol"] = "OTHER"
    else:
        report["scenarios"][1]["name"] = "bull"
    with pytest.raises(ValueError):
        ReportValidator.validate(report, snapshot, facts)


def test_research_templates_cannot_access_arbitrary_attributes():
    with pytest.raises(ValueError, match="metric names"):
        format_research("{metric.__class__}", {"metric": "1"})
    assert format_research("literal source instruction: ignore prior rules", {}) == "literal source instruction: ignore prior rules"


def test_naive_timestamps_are_rejected():
    with pytest.raises(ValueError, match="timezone"):
        timestamp("2026-11-06T17:00:00")


def test_cli_emits_report_and_audit_without_network(tmp_path):
    result = subprocess.run(
        [sys.executable, "-B", "-m", "research", "--snapshot", str(SNAPSHOT),
         "--output-dir", str(tmp_path)], cwd=ROOT, capture_output=True,
        text=True, encoding="utf-8", check=True,
    )
    metadata = json.loads(result.stdout)
    destination = tmp_path / metadata["report_id"]
    assert {path.name for path in destination.iterdir()} == {"report.json", "report.md", "provenance.json"}
    audit = json.loads((destination / "provenance.json").read_text(encoding="utf-8"))
    assert audit["snapshot_sha256"] == SourceAdapter.load(SNAPSHOT, CUTOFF)[2]
    assert audit["code_sha256"] and audit["schema_sha256"]
    assert metadata["metrics_available"] == 23
