"""ILLUSTRATIVE — NOT LIVE MARKET DATA. Comparability and immutable review tests."""

from copy import deepcopy
from pathlib import Path

import jsonschema
import pytest

from research import sec_growth as growth, sec_period_review as periods, sec_growth_report as reports
from research import sec_growth_demo as demo, sec_review_demo as current_demo, sec_refresh_demo as transport_demo
from research import sec_fact_review as original, sec_refresh
from research.local_files import atomic_json
from research.sec_report import ROOT
from research.sec_transport import Client
from research.validation import digest


@pytest.fixture
def case(tmp_path, request):
    routes, watchlist = demo.fixture()
    if getattr(request, "param", None) == "conflict":
        for url, value in list(routes.items()):
            if isinstance(value, bytes) and url.endswith("demo-2.htm"):
                extra = b'<tr><td>Alternative prior total</td><td><ix:nonFraction id="alternate_prior" name="us-gaap:Revenues" contextRef="income_prior" unitRef="usd_prior">1100</ix:nonFraction></td></tr>'
                routes[url] = value.replace(b'</table></body>', extra + b'</table></body>')
    watchlist["entities"] = watchlist["entities"][:1]
    clock = transport_demo.Clock()
    client = Client("Stocker fixture analyst@fixture.invalid", root=tmp_path / "http", raw_root=tmp_path / "raw", illustrative=True, opener=transport_demo.Opener(routes), clock=clock, sleep=clock.advance)
    source, review_root, period_root = tmp_path / "store", tmp_path / "current", tmp_path / "periods"
    sec_refresh.refresh(watchlist, client=client, store=source, clock=clock)
    clock.advance(2)
    snapshot = sec_refresh.latest(source, "1001")
    for filing in snapshot["filings"]:
        if filing["form"] not in {"10-K", "10-Q"}: continue
        worksheet = original.template("1001", filing["accession"], cutoff=clock(), source_store=source, illustrative=True)
        path = tmp_path / (filing["form"] + ".json")
        atomic_json(path, current_demo.illustrative_manifest(worksheet, clock()))
        original.store(path, root=review_root, source_store=source, illustrative=True, clock=clock)
    clock.advance(2)
    accession = next(f["accession"] for f in snapshot["filings"] if f["form"] == "10-Q")
    result = {"folder": tmp_path, "routes": routes, "watchlist": watchlist, "clock": clock, "client": client,
        "source_store": source, "review_root": review_root, "period_root": period_root, "accession": accession, "counter": 0}
    result["worksheet"] = periods.template("1001", accession, cutoff=clock(), review_root=review_root, source_store=source, illustrative=True)
    return result


def submit(case, manifest=None):
    case["counter"] += 1
    path = case["folder"] / f"period-{case['counter']}.json"
    value = manifest or demo.illustrative_manifest(case["worksheet"], case["clock"]())
    atomic_json(path, value)
    return periods.store(path, root=case["period_root"], review_root=case["review_root"], source_store=case["source_store"], illustrative=True, clock=case["clock"])


def report(case, cutoff=None):
    return reports.build("1001", cutoff or case["clock"](), root=case["period_root"], review_root=case["review_root"], source_store=case["source_store"], illustrative=True, clock=case["clock"])


def reviewed(case):
    submit(case)
    case["clock"].advance(2)
    return report(case)


def q_section(value):
    return next(s for s in value["growth"]["sections"] if s["form"] == "10-Q")


@pytest.mark.parametrize("current,prior,percent,transition,delta", [("100", "50", "100.000000", "increased_positive", "50"),
    ("-20", "-50", None, "negative_magnitude_decreased", "30"), ("20", "-50", None, "negative_to_positive", "70"),
    ("-20", "50", "-140.000000", "positive_to_negative", "-70"), ("20", "0", None, "zero_to_positive", "20"),
    ("0", "0", None, "unchanged_zero", "0"), ("-80", "-50", None, "negative_magnitude_increased", "-30"),
    ("0", "20", "-100.000000", "positive_to_zero", "-20"), ("0", "-20", None, "negative_to_zero", "20")])
def test_percentage_and_sign_transition_have_distinct_meaning(current, prior, percent, transition, delta):
    result = growth.change(current, prior)
    assert result["percent_change"] == percent and result["transition"] == transition and result["absolute_change"] == delta


def test_large_decimal_change_is_not_binary_float():
    assert growth.change("9007199254740993.125", "9007199254740992.125")["absolute_change"] == "1.000"


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_growth_is_rejected(value):
    with pytest.raises(ValueError): growth.change(value, "1")


def test_same_calendar_year_and_noncomparable_duration_are_rejected():
    a = {"start": "2026-07-01", "end": "2026-09-30", "is_instant": False}
    assert not periods.is_year_pair(a, {**a, "start": "2026-04-01", "end": "2026-06-30"})
    assert not periods.is_year_pair(a, {**a, "start": "2025-01-01", "end": "2025-09-30"})
    assert periods.is_year_pair(a, {**a, "start": "2025-07-01", "end": "2025-09-30"})


def test_52_53_week_difference_is_bounded_not_arbitrary():
    a = {"start": "2026-06-29", "end": "2026-09-27", "is_instant": False}
    assert periods.is_year_pair(a, {"start": "2025-06-30", "end": "2025-09-28", "is_instant": False})
    assert not periods.is_year_pair(a, {"start": "2025-05-01", "end": "2025-09-28", "is_instant": False})


def test_instant_balance_change_cannot_mix_with_duration():
    a = {"start": None, "end": "2026-09-30", "is_instant": True}
    assert periods.is_year_pair(a, {**a, "end": "2025-09-30"})
    assert not periods.is_year_pair(a, {"start": "2025-07-01", "end": "2025-09-30", "is_instant": False})


def test_template_is_draft_and_keeps_original_current_anchor(case):
    value = case["worksheet"]
    assert value["manifest"]["template_only"] and value["current_anchors"]["net_income"]["scope"] == "consolidated"
    assert len(value["candidates"]["revenue"]) == 1
    assert value["candidates"]["operating_cash_flow"][0]["start"] == "2025-01-01"
    with pytest.raises(ValueError, match="DRAFT"): submit(case, value["manifest"])


def test_current_revenue_definition_does_not_accept_contract_only_comparator(case):
    assert all(f["tag"] == "us-gaap:Revenues" for f in case["worksheet"]["candidates"]["revenue"])


@pytest.mark.parametrize("change", ["source", "anchor", "scope", "fact", "label", "future", "method"])
def test_invalid_comparison_cannot_be_committed(case, change):
    value = demo.illustrative_manifest(case["worksheet"], case["clock"]())
    first = value["findings"][0]
    if change == "source": value["binding"]["raw_sha256"] = "0" * 64
    if change == "anchor": first["current_signature"] = "0" * 64
    if change == "scope": first["scope"] = "segment"
    if change == "fact": first["prior_fact_id"] = first["current_fact_id"]
    if change == "label": first["row_label"] = "not in the source"
    if change == "future": value["reviewed_at"] = "2027-01-01T00:00:00Z"
    if change == "method": value["method_hashes"]["research/sec_growth.py"] = "0" * 64
    with pytest.raises(ValueError): submit(case, value)


def test_manifest_cannot_invent_a_prior_amount(case):
    value = demo.illustrative_manifest(case["worksheet"], case["clock"]())
    value["findings"][0]["prior_value"] = "9999"
    with pytest.raises(jsonschema.ValidationError): submit(case, value)


def test_helper_never_auto_approves_real_data(case):
    worksheet = deepcopy(case["worksheet"])
    worksheet["manifest"]["is_illustrative"] = False
    with pytest.raises(ValueError, match="CANNOT_APPROVE_REAL"): demo.illustrative_manifest(worksheet, case["clock"]())


def test_comparisons_reach_report_but_negative_base_percent_is_null(case):
    value = reviewed(case)
    section = q_section(value)
    assert section["metrics"]["revenue"]["calculation"]["percent_change"] == "100.100000"
    assert section["metrics"]["operating_income"]["calculation"]["percent_change"] is None
    assert section["metrics"]["operating_income"]["calculation"]["absolute_change"] == "150"
    assert section["metrics"]["operating_income"]["calculation"]["transition"] == "negative_to_positive"
    assert section["derived"]["operating_margin_change"]["unit"] == "percentage_points"
    assert section["derived"]["free_cash_flow_change"]["calculation"]["prior"] == "-150"
    assert section["derived"]["free_cash_flow_change"]["calculation"]["percent_change"] is None
    assert value["growth"]["coverage"]["reviewed_pairs"] == 9
    assert value["base_report"]["swing"]["attractiveness"] is None


def test_margin_change_is_percentage_points_not_relative_growth():
    def fact(value): return {"value": value, "start": "2026-07-01", "end": "2026-09-30", "unit": "USD"}
    pairs = {"revenue": {"current": fact("100"), "prior": fact("100"), "scope": "consolidated"}, "operating_income": {"current": fact("20"), "prior": fact("10"), "scope": "consolidated"}}
    result = growth._derived(pairs)["operating_margin_change"]
    assert result["current"] == "20.000000" and result["prior"] == "10.000000" and result["change"] == "10.000000"
    assert result["unit"] == "percentage_points"
    pairs["operating_income"]["scope"] = "parent_attributable"
    assert growth._derived(pairs)["operating_margin_change"]["change"] is None


def test_no_comparative_reviews_preserves_current_values_and_withholds_comparison(case):
    value = report(case)
    row = q_section(value)["metrics"]["revenue"]
    assert row["current_value"] == "2001" and row["calculation"] is None
    assert value["growth"]["coverage"]["reviewed_pairs"] == 0


def test_first_commit_not_claimed_old_review_time_controls_availability(case):
    before = case["clock"]()
    value = demo.illustrative_manifest(case["worksheet"], before)
    case["clock"].advance(60)
    receipt = submit(case, value)
    assert report(case, before)["growth"]["coverage"]["reviewed_pairs"] == 0
    assert report(case, receipt["available_at"])["growth"]["coverage"]["reviewed_pairs"] == 0
    case["clock"].advance(2)
    assert report(case)["growth"]["coverage"]["reviewed_pairs"] == 9


def test_duplicate_import_does_not_reset_availability(case):
    manifest = demo.illustrative_manifest(case["worksheet"], case["clock"]())
    first = submit(case, manifest)
    case["clock"].advance(60)
    second = submit(case, manifest)
    assert second["status"] == "already_stored" and first["available_at"] == second["available_at"]


def test_current_review_update_requires_new_comparability_review(case):
    reviewed(case)
    worksheet = original.template("1001", case["accession"], cutoff=case["clock"](), source_store=case["source_store"], illustrative=True)
    manifest = current_demo.illustrative_manifest(worksheet, case["clock"](), metrics=["revenue"])
    path = case["folder"] / "current-updated.json"
    atomic_json(path, manifest)
    original.store(path, root=case["review_root"], source_store=case["source_store"], illustrative=True, clock=case["clock"])
    case["clock"].advance(2)
    value = report(case)
    assert value["growth"]["coverage"]["reviewed_pairs"] == 8
    assert value["growth"]["coverage"]["current_review_changed"] == 1


def test_explicit_unresolved_revision_does_not_reuse_old_pair(case):
    reviewed(case)
    value = demo.illustrative_manifest(case["worksheet"], case["clock"](), metrics=["revenue"])
    value["findings"][0].update(decision="unresolved", prior_fact_id=None, scope=None, comparability_rationale="ILLUSTRATIVE revised judgment: the comparative definition is unresolved")
    submit(case, value)
    case["clock"].advance(2)
    item = q_section(report(case))["metrics"]["revenue"]
    assert item["calculation"] is None and item["status"] == "comparative_review_unresolved"


def test_source_change_and_reversion_never_revive_pair(case):
    reviewed(case)
    cutoff = case["clock"]()
    old = report(case, cutoff)["growth"]
    url = case["worksheet"]["manifest"]["binding"]["source_url"]
    raw = case["routes"][url]
    case["routes"][url] = raw.replace(b'1000</ix:nonFraction>', b'1001</ix:nonFraction>')
    case["clock"].advance(86401)
    sec_refresh.refresh(case["watchlist"], client=case["client"], store=case["source_store"], clock=case["clock"])
    assert report(case)["growth"]["coverage"]["reviewed_pairs"] == 0
    case["routes"][url] = raw
    case["clock"].advance(86401)
    sec_refresh.refresh(case["watchlist"], client=case["client"], store=case["source_store"], clock=case["clock"])
    assert report(case)["growth"]["coverage"]["reviewed_pairs"] == 0
    assert report(case, cutoff)["growth"] == old


def test_later_amendment_withholds_old_comparison(case):
    reviewed(case)
    transport_demo.amendment(case["routes"])
    case["clock"].advance(3601)
    sec_refresh.refresh(case["watchlist"], client=case["client"], store=case["source_store"], clock=case["clock"])
    value = report(case)
    assert value["growth"]["coverage"]["reviewed_pairs"] == 0


def test_review_receipt_tampering_is_detected(case):
    receipt = submit(case)
    path = case["period_root"] / "illustrative/receipts" / (receipt["receipt_id"] + ".json")
    value = sec_refresh.read(path)
    value["selected_pairs"]["revenue"]["prior"]["value"] = "999"
    atomic_json(path, value, replace=True)
    case["clock"].advance(2)
    with pytest.raises(ValueError, match="INTEGRITY_CHANGED"): report(case)


@pytest.mark.parametrize("change", ["value", "unit", "narrative", "score", "current_scope", "source_root"])
def test_report_replay_rejects_modified_data_or_meaning(case, change):
    value = reviewed(case)
    if change == "value": q_section(value)["metrics"]["revenue"]["calculation"]["percent_change"] = "999"
    if change == "unit": q_section(value)["derived"]["operating_margin_change"]["unit"] = "percent"
    if change == "narrative": value["limitations"] = ["Guaranteed returns"]
    if change == "score": value["growth"]["score"] = 80
    if change == "current_scope": value["base_report"]["context"]["sections"][0]["metrics"]["net_income"]["scope"] = "parent_attributable"
    if change == "source_root": value["provenance"]["review_root"] = str(case["folder"] / "elsewhere")
    with pytest.raises((ValueError, jsonschema.ValidationError)): reports.validate(value)


def test_no_network_or_old_method_file_changes(case):
    before = original.methods()
    count = case["client"].network_requests
    value = reviewed(case)
    folder = reports.write(value, case["folder"] / "reports")
    assert reports.validate(sec_refresh.read(folder / "report.json"))
    assert original.methods() == before and case["client"].network_requests == count
    assert value["growth"]["qoq"].startswith("withheld") and value["growth"]["ttm"].startswith("withheld")


def test_korean_report_explains_percentage_points_and_negative_base(case):
    text = reports.render(reviewed(case))
    assert "퍼센트포인트" in text and "보류(이전 값 ≤ 0)" in text
    assert "회계연도 누적" in text and "음수→양수" in text and "향후 수익 예측" in text


def test_multi_issuer_golden(tmp_path):
    value = demo.run(tmp_path)
    value.pop("report_paths")
    assert value == sec_refresh.read(ROOT / "examples/sec_growth_golden.json")


@pytest.mark.parametrize("case", ["conflict"], indirect=True)
def test_competing_comparative_reviewers_withhold_instead_of_latest_wins(case):
    reviewed(case)
    manifest = demo.illustrative_manifest(case["worksheet"], case["clock"](), reviewer_id="ILLUSTRATIVE_SECOND", metrics=["revenue"])
    alternative = next(f for f in case["worksheet"]["candidates"]["revenue"] if f["dom_id"] == "alternate_prior")
    first = next(f for f in case["worksheet"]["candidates"]["revenue"] if f["dom_id"] == "revenue_prior")
    manifest["findings"][0].update(prior_fact_id=alternative["fact_id"], row_label="Alternative prior total", rejected_fact_ids=[first["fact_id"]])
    submit(case, manifest)
    case["clock"].advance(2)
    row = q_section(report(case))["metrics"]["revenue"]
    assert row["status"] == "conflicting_comparative_reviews" and row["calculation"] is None


def test_growth_method_change_does_not_clear_current_filing_reviews(case, monkeypatch):
    reviewed(case)
    old = periods.methods()
    monkeypatch.setattr(periods, "methods", lambda: {**old, "research/sec_growth.py": "0" * 64})
    value = report(case)
    assert value["base_report"]["context"]["review_coverage"]["reviewed_metrics"] == 18
    assert value["growth"]["coverage"]["reviewed_pairs"] == 0 and value["growth"]["coverage"]["method_changed"] == 1


def test_future_receipt_corruption_does_not_change_prior_cutoff(case):
    reviewed(case)
    cutoff = case["clock"]()
    before = report(case, cutoff)["growth"]
    case["clock"].advance(60)
    later = submit(case, demo.illustrative_manifest(case["worksheet"], case["clock"](), metrics=["cash"]))
    path = case["period_root"] / "illustrative/receipts" / (later["receipt_id"] + ".json")
    value = sec_refresh.read(path)
    value["selected_pairs"]["cash"]["prior"]["value"] = "corrupt"
    atomic_json(path, value, replace=True)
    assert report(case, cutoff)["growth"] == before


def test_clock_rollback_cannot_backdate_new_period_receipt(case):
    submit(case)
    case["clock"].advance(-1)
    manifest = demo.illustrative_manifest(case["worksheet"], case["clock"](), metrics=["revenue"])
    with pytest.raises(ValueError): submit(case, manifest)
