"""ILLUSTRATIVE — NOT LIVE MARKET DATA. No providers, fitting or final tests."""

from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path

import jsonschema
import numpy as np
import pytest

from research import historical_panel, panel_diagnostics as diag, panel_statistics as stats
from research import panel_diagnostic_report as report, panel_job
from research.local_files import atomic_json
from research.rights import RightsError
from research.sec_report import ROOT
from research.swing_models import XnysCalendar
from research.validation import digest

LABEL = "ILLUSTRATIVE — NOT LIVE MARKET DATA"


@pytest.fixture(scope="module")
def calendar_refs():
    calendar = XnysCalendar("2023-01-01", "2026-12-31")
    days = calendar.sessions("2024-01-02", "2026-11-06")
    return [{"phase": "development", "reference_session": days[i], "label_sessions": days[i + 1:i + 6]} for i in range(59, 59 + 96 * 5, 5)]


@pytest.fixture
def settings():
    # Some loader fixtures deliberately redirect the module's workspace root.
    return json.loads((ROOT / "config/panel_diagnostics_policy.json").read_text(encoding="utf-8"))


def fixture_dataset(refs, issuers=80, selected=100, shift=None, width=0):
    profiles = [{"symbol": f"T{i}", "cik": f"{i + 1:010d}", "sector_id": "3",
        "symbol_partition": "development_and_validation" if i < issuers else "unseen_symbol_final_only"} for i in range(selected)]
    rows = []
    for j, ref in enumerate(refs):
        baseline = Decimal((j % 7) - 3) / 10
        change = Decimal(str(shift(j, ref) if shift else .1 + ((j // 4) % 3 - 1) / 10))
        for profile in profiles[:issuers]:
            scenarios = []
            for cost in ("10", "25"):
                hold = baseline - Decimal(cost) / 50
                bounds = [hold + change - Decimal(str(width)), hold + change + Decimal(str(width))]
                scenarios.append({"cost_bps_per_side": cost, "unfiltered_outcome": {"outcome": "timeout" if not width else "ambiguous_same_bar", "hit_session": None},
                    "unfiltered_net_return_pct_bounds": list(map(str, bounds)), "screened_net_return_pct_bounds": list(map(str, bounds)),
                    "five_session_hold_net_return_pct": str(hold), "target_timing_evaluable": False, "target_hit_inside_conditional_timing_range": None})
            rows.append({**ref, **{key: profile[key] for key in ("symbol", "cik", "sector_id")},
                "reference_month": ref["reference_session"][:7], "status": "retrospective_diagnostic_not_validation", "reason_code": None,
                "screen_decision": "numerical_candidate", "absolute_peak_error_atr": .5, "peak_day_absolute_error": j % 3,
                "modal_price_bin_hit": j % 4 == 0, "joint_brier": 1.0,
                "volatility_group": "atr_2_to_4pct", "liquidity_group": "dollar_volume_at_least_10m", "cost_scenarios": scenarios})
    return {"status": "ready", "source_kind": "synthetic", "is_illustrative": True, "source_ids": ["fixture-only"],
        "source_progress": {"status": "illustrative"}, "profiles": profiles,
        "sectors": [{"sector_id": "3", "name_ko": "가상 산업", "selected_unique_issuers": selected}],
        "references": refs, "cases": rows, "sources": [], "fixture_label": LABEL}


def raw_case(row):
    return {**row, "is_illustrative": True, "point_in_time_verified": False, "released_score": None,
        "source_ids": ["fixture-only"], "fixture_label": LABEL}


def test_block_draws_keep_contiguous_dates_and_same_seed_across_sectors(calendar_refs, settings):
    refs = [row["reference_session"] for row in calendar_refs[:50]]
    draws, seed = stats.calendar_draws(refs, "development", 4, settings)
    again, same_seed = stats.calendar_draws(refs, "development", 4, settings)
    assert np.array_equal(draws, again) and seed == same_seed
    assert draws.shape == (2000, 50) and draws.min() >= 0 and draws.max() < 50
    full = draws[:, :48].reshape(2000, 12, 4)
    assert np.all(np.diff(full, axis=2) == 1)
    other, other_seed = stats.calendar_draws(refs, "validation", 4, settings)
    assert seed != other_seed and not np.array_equal(draws, other)


def test_one_hundred_perfectly_correlated_stocks_do_not_narrow_interval(calendar_refs, settings):
    policy = deepcopy(settings)
    policy["support"]["minimum_matched_issuers_per_phase"] = 1
    many = fixture_dataset(calendar_refs[:64])
    one_rows = [row for row in many["cases"] if row["symbol"] == "T0"]
    dates = [row["reference_session"] for row in many["references"]]
    sector = many["sectors"][0]
    one = stats.block_comparison(one_rows, dates, "development", sector, "25", 4, policy)
    all_stocks = stats.block_comparison(many["cases"], dates, "development", sector, "25", 4, policy)
    assert one["intervals"]["excess_lower_pct_points"] == pytest.approx(all_stocks["intervals"]["excess_lower_pct_points"], abs=1e-12)
    assert one["independent_sample_count"] is all_stocks["independent_sample_count"] is None


def test_strategy_and_hold_use_paired_identical_date_draws(calendar_refs, settings):
    data = fixture_dataset(calendar_refs[:64], shift=lambda j, ref: .7)
    result = stats.group_diagnostics(data["cases"], data["references"], "development", data["sectors"][0], settings)
    for cost in result["uncertainty"]:
        for block in cost["blocks"]:
            assert block["intervals"]["conservative_excess_envelope_pct_points"] == pytest.approx([.7, .7], abs=1e-12)
            assert block["direction"] == "positive_sample_excess"
        assert cost["sensitivity_status"] == "consistent_positive"


def test_ambiguous_execution_keeps_two_sampling_endpoints(calendar_refs, settings):
    data = fixture_dataset(calendar_refs[:64], shift=lambda j, ref: 0, width=1)
    group = stats.group_diagnostics(data["cases"], data["references"], "development", data["sectors"][0], settings)
    primary = group["uncertainty"][0]["blocks"][0]
    assert primary["intervals"]["conservative_excess_envelope_pct_points"] == pytest.approx([-1, 1])
    assert primary["intervals"]["excess_lower_pct_points"] != primary["intervals"]["excess_upper_pct_points"]
    assert primary["direction"] == "inconclusive" and primary["performance_validated"] is False


def test_many_stocks_do_not_replace_missing_time_blocks(calendar_refs, settings):
    data = fixture_dataset(calendar_refs[:16])
    group = stats.group_diagnostics(data["cases"], data["references"], "development", data["sectors"][0], settings)
    for cost in group["uncertainty"]:
        primary, short, long = cost["blocks"]
        assert primary["intervals"] is None and "INSUFFICIENT_CALENDAR_BLOCKS" in primary["reason_codes"]
        assert short["status"] == "computed_exploratory" and long["status"] == "withheld"
        assert cost["primary_direction"] == "insufficient_support"  # Do not choose the favorable shorter block.
        assert cost["sensitivity_status"] == "not_all_lengths_supported"


def test_missing_date_is_retained_and_withholds_interval(calendar_refs, settings):
    data = fixture_dataset(calendar_refs[:64])
    for index, row in enumerate(data["cases"]):
        if row["reference_session"] == data["references"][10]["reference_session"]:
            profile = next(profile for profile in data["profiles"] if profile["symbol"] == row["symbol"])
            data["cases"][index] = diag.unavailable(profile, data["references"][10], "EXPECTED_CASE_MISSING")
    group = stats.group_diagnostics(data["cases"], data["references"], "development", data["sectors"][0], settings)
    assert group["point"]["expected_slots"] == 5120 and group["point"]["unavailable_slots"] == 80
    assert group["point"]["cost_scenarios"][0]["matched_slots"] == 5040
    assert "REFERENCE_DATE_WITHOUT_COMPARISONS" in group["uncertainty"][0]["blocks"][0]["reason_codes"]


def test_month_dependence_is_reported_without_optimizing_any_parameter(calendar_refs, settings):
    first_month = calendar_refs[0]["reference_session"][:7]
    data = fixture_dataset(calendar_refs[:64], shift=lambda j, ref: 15 if ref["reference_session"].startswith(first_month) else -.01)
    group = stats.group_diagnostics(data["cases"], data["references"], "development", data["sectors"][0], settings)
    assert all(row["direction_not_preserved_after_leaving_out_one_month"] for row in group["monthly_sensitivity"])
    assert "RESULT_DEPENDS_ON_ONE_MONTH" in group["review_priorities"]
    assert all(row["is_formal_significance_test"] is False for row in group["monthly_sensitivity"])


def test_cash_and_missing_data_have_different_denominators(calendar_refs, settings):
    data = fixture_dataset(calendar_refs[:16], issuers=2, selected=2)
    for row in data["cases"]:
        row["screen_decision"] = "stand_aside"
        for item in row["cost_scenarios"]: item["screened_net_return_pct_bounds"] = ["0", "0"]
    row = data["cases"][0]
    data["cases"][0] = diag.unavailable(data["profiles"][0], data["references"][0], "EXPECTED_CASE_MISSING")
    result = diag.analyze(data, settings)
    point = result["groups"][0]["point"]
    assert point["expected_slots"] == 32 and point["unavailable_slots"] == 1
    assert point["cost_scenarios"][0]["matched_slots"] == 31
    assert point["cost_scenarios"][0]["screened_mean_net_return_pct_bounds"] == ["0", "0"]
    assert "ALL_SCREENED_RESULTS_ARE_CASH" in result["groups"][0]["review_priorities"]
    assert "매매 이익을 입증하지 않습니다" in report.markdown(result, settings)


def test_point_means_match_existing_fixed_panel_definitions(calendar_refs, settings):
    data = fixture_dataset(calendar_refs[:16], issuers=2, selected=2, width=.3)
    old = historical_panel.summarize(data["cases"])
    new = stats.point_summary(data["cases"], settings)
    for left, right in zip(old["cost_scenarios"], new["cost_scenarios"]):
        assert list(map(Decimal, left["screened_mean_excess_to_hold_pct_bounds"])) == list(map(Decimal, right["screened_mean_excess_to_hold_pct_points_bounds"]))
        assert left["matched_cases"] == right["matched_slots"]


def test_reordering_records_does_not_change_results(calendar_refs, settings):
    data = fixture_dataset(calendar_refs[:32], issuers=60, selected=100)
    first = stats.group_diagnostics(data["cases"], data["references"], "development", data["sectors"][0], settings)
    second = stats.group_diagnostics(list(reversed(data["cases"])), data["references"], "development", data["sectors"][0], settings)
    assert first == second


@pytest.mark.parametrize("mutation", ["final", "unseen", "duplicate_issuer", "missing_case", "duplicate_case", "fake_sector_size", "mixed_classification", "crossing_labels"])
def test_input_denominator_identity_and_final_guards(mutation, calendar_refs, settings):
    data = fixture_dataset(calendar_refs[:2], issuers=2, selected=3)
    if mutation == "final": data["references"][0] = {**data["references"][0], "phase": "final_locked"}
    if mutation == "unseen": data["cases"][0]["symbol"] = data["profiles"][-1]["symbol"]
    if mutation == "duplicate_issuer": data["profiles"][1]["cik"] = data["profiles"][0]["cik"]
    if mutation == "missing_case": data["cases"].pop()
    if mutation == "duplicate_case": data["cases"].append(deepcopy(data["cases"][0]))
    if mutation == "fake_sector_size": data["sectors"][0]["selected_unique_issuers"] = 100
    if mutation == "mixed_classification": data["is_illustrative"] = False
    if mutation == "crossing_labels": data["references"][0] = {**data["references"][0], "label_sessions": ["2099-01-01"] * 5}
    with pytest.raises(diag.InputError): diag.analyze(data, settings)


@pytest.mark.parametrize("mutation", ["nan", "reversed", "cash_not_zero", "wrong_timing", "missing_cost", "wrong_phase", "wrong_classification"])
def test_malformed_saved_results_are_rejected(mutation, calendar_refs):
    data = fixture_dataset(calendar_refs[:1], issuers=1, selected=1)
    row = raw_case(data["cases"][0])
    if mutation == "nan": row["cost_scenarios"][0]["screened_net_return_pct_bounds"] = ["NaN", "1"]
    if mutation == "reversed": row["cost_scenarios"][0]["screened_net_return_pct_bounds"] = ["2", "1"]
    if mutation == "cash_not_zero": row["screen_decision"] = "stand_aside"
    if mutation == "wrong_timing": row["cost_scenarios"][0]["target_timing_evaluable"] = True
    if mutation == "missing_cost": row["cost_scenarios"].pop()
    if mutation == "wrong_phase": row["phase"] = "final_locked"
    if mutation == "wrong_classification": row["is_illustrative"] = False
    with pytest.raises(ValueError): diag.compact_case(row, data["profiles"][0], calendar_refs[0], True)


def test_unknown_final_file_is_not_opened(tmp_path, calendar_refs, monkeypatch):
    data = fixture_dataset(calendar_refs[:1], issuers=1, selected=1)
    (tmp_path / "cases").mkdir()
    (tmp_path / "cases/2099-01-01.json").write_text("DO NOT READ FINAL DATA", encoding="utf-8")
    monkeypatch.setattr(diag, "read", lambda *args, **kwargs: pytest.fail("Final content must not be opened"))
    with pytest.raises(diag.InputError, match="FINAL"):
        diag.cases_from_folder(tmp_path, data["profiles"][0], calendar_refs[:1], True, [])


@pytest.fixture
def saved_job(tmp_path, monkeypatch):
    folder = tmp_path / "reports/private/historical_panel" / ("a" * 16)
    folder.mkdir(parents=True)
    atomic_json(folder / "job.json", {"fixture_label": LABEL})
    record = {"id": "operational-fixture-only", "provider": "massive", "reviewer": LABEL, "reviewed_at": "2026-11-07T00:00:00Z",
        "review_due_at": "2027-01-01T00:00:00Z", "evidence_url": "https://example.invalid/fixture", "plan": "fixture_free", "monthly_cost_usd": 0,
        "is_illustrative": False, "uses": {key: "allowed" for key in ("ingest", "store", "derived_analytics")}}
    atomic_json(folder / "price_entitlement.json", record)
    monkeypatch.setattr(diag, "ROOT", tmp_path)
    monkeypatch.setattr(panel_job, "check", lambda path: {"job_id": "a" * 16})
    return folder


def test_running_job_is_waiting_without_opening_any_outcomes(saved_job, monkeypatch, settings):
    folder = saved_job
    atomic_json(folder / "state.json", {"status": "running", "stage": "profiles", "profile_index": 10})
    original_read = diag.read
    def read(path, sources=None):
        assert "evaluation" not in str(path) and "cohort" not in str(path)
        return original_read(path, sources)
    monkeypatch.setattr(diag, "read", read)
    data = diag.load_job(folder / "job.json")
    result = diag.analyze(data, settings)
    assert result["status"] == "waiting_for_panel_results" and result["case_slots"] == 0 and result["groups"] == []


def test_unresolved_stock_is_counted_and_unseen_folder_is_not_read(saved_job, monkeypatch, calendar_refs):
    folder = saved_job
    profiles = [{"symbol": "DEV", "cik": "0000000001", "sector_id": "3", "symbol_partition": "development_and_validation"},
                {"symbol": "LOCKED", "cik": "0000000002", "sector_id": "3", "symbol_partition": "unseen_symbol_final_only"}]
    cohort = {"selected": profiles, "sectors": [{"sector_id": "3", "name_ko": "가상 산업", "selected_unique_issuers": 2}]}
    atomic_json(folder / "cohort.json", cohort)
    atomic_json(folder / "state.json", {"status": "complete_with_gaps", "stage": "done", "cohort_sha256": digest(folder / "cohort.json"),
        "stock_results": {"DEV": {"status": "unresolved"}}, "evaluated": []})
    poison = folder / "evaluation/ticker-LOCKED/cases/2099-01-01.json"
    poison.parent.mkdir(parents=True)
    poison.write_text("DO NOT READ UNSEEN OUTCOMES", encoding="utf-8")
    monkeypatch.setattr(historical_panel, "references", lambda: calendar_refs[:2])
    original_read = diag.read
    def read(path, sources=None):
        assert "LOCKED" not in str(path)
        return original_read(path, sources)
    monkeypatch.setattr(diag, "read", read)
    data = diag.load_job(folder / "job.json")
    assert len(data["cases"]) == 2 and all(row["status"] == "unavailable" and row["cost_scenarios"] == [] for row in data["cases"])


def test_rights_are_checked_before_state_or_result_parsing(saved_job, monkeypatch):
    folder = saved_job
    record = json.loads((folder / "price_entitlement.json").read_text(encoding="utf-8"))
    record["uses"]["derived_analytics"] = "unknown"
    atomic_json(folder / "price_entitlement.json", record, replace=True)
    original_read = diag.read
    def read(path, sources=None):
        assert Path(path).name == "price_entitlement.json"
        return original_read(path, sources)
    monkeypatch.setattr(diag, "read", read)
    with pytest.raises(RightsError): diag.load_job(folder / "job.json")


def test_schema_prevents_release_and_misclassification(calendar_refs, settings):
    data = fixture_dataset(calendar_refs[:2], issuers=2, selected=2)
    result = diag.analyze(data, settings)
    schema = json.loads((ROOT / "schemas/panel_diagnostics.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    validator.validate(result)
    for key, invalid in (("released_score", 50), ("performance_validated", True), ("final_cases_read", 1), ("heldout_symbols_read", 1), ("model_training_performed", True), ("calibrated_win_probability", .9), ("is_illustrative", False)):
        with pytest.raises(jsonschema.ValidationError): validator.validate({**result, key: invalid})


def test_complete_save_separates_illustrative_outputs_and_preserves_input(tmp_path, monkeypatch, calendar_refs):
    for name in diag.CORE:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    data = fixture_dataset(calendar_refs[:2], issuers=2, selected=2)
    before = deepcopy(data)
    monkeypatch.setattr(diag, "ROOT", tmp_path)
    result, folder = diag.save(data)
    assert data == before and folder.is_relative_to(tmp_path / "reports/private/panel_diagnostics/illustrative")
    assert (folder / "inputs.json").exists() and (folder / "REPORT.ko.md").exists()
    assert LABEL in (folder / "REPORT.ko.md").read_text(encoding="utf-8")
    assert result["network_requests"] == result["external_llm_calls"] == 0


def test_schema_failure_cannot_dump_private_result_values_to_console(monkeypatch, capsys):
    private = "PRIVATE-DERIVED-RESULT-DO-NOT-PRINT"
    monkeypatch.setattr(diag, "load_job", lambda path: {})
    def broken_save(dataset):
        raise jsonschema.ValidationError(private)
    monkeypatch.setattr(diag, "save", broken_save)
    assert diag.main(["--job", "fixture-only.json"]) == 1
    output = capsys.readouterr()
    assert private not in output.out + output.err
    assert json.loads(output.out)["status"] == "withheld"
