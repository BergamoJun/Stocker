"""ILLUSTRATIVE — NOT LIVE MARKET DATA. Calendar plans have no price outcomes."""

import json
from copy import deepcopy
import jsonschema
import pytest
from research import historical_preflight
from research.sec_report import ROOT


def settings():
    return json.loads((ROOT / "config/historical_evaluation_protocol.json").read_text(encoding="utf-8"))


def test_shared_chronological_partitions_purge_every_crossing_five_session_label():
    result = historical_preflight.partitions(settings())
    train, validation, final = result["partitions"]
    assert train["start"] < validation["start"] < final["start"]
    assert train["purged_references"] and validation["purged_references"]
    for phase in result["partitions"]:
        for row in phase["references"]:
            assert len(row["label_sessions"]) == 5
            assert row["reference_session"] < row["label_sessions"][0]
            assert not phase["end_exclusive"] or row["label_sessions"][-1] < phase["end_exclusive"]
    assert final["performance_results_permitted_now"] is False


def test_capacity_is_an_example_and_does_not_report_a_real_evaluation():
    budget = {"massive_basic": {"requests_per_minute": 5, "history_years": 2}}
    value = historical_preflight.capacity(settings(), budget)
    assert value["symbols_assumed"] == 1100
    assert value["api_rate_floor_minutes_excluding_metadata_and_retries"] == "220"
    assert value["is_capacity_example_not_a_selected_universe"] is True
    assert value["llm_tokens"] == 0


def test_protocol_keeps_fixed_engine_and_existing_forward_job_separate():
    value = settings()
    assert value["minimum_symbols_per_sector"] == 100
    assert value["first_pass"]["fit_or_optimize_parameters"] is False
    assert value["tuning_after_first_pass"]["old_policies_and_BE_background_job_changed"] is False
    assert value["partitions"]["final_results_locked_during_tuning"] is True
    assert value["universe"]["exclude_from_untouched_symbol_test"] == ["BE"]


@pytest.mark.parametrize("mutation", ["pretend_real_run", "unlock_final"])
def test_contract_does_not_turn_preparation_into_executed_results(mutation):
    value = settings()
    result = {"status": "planning_only_no_real_panel_run", "as_of": "2026-11-07T15:30:00Z", "protocol_id": value["id"],
        "policy_sha256": "f" * 64, "calendar_plan": historical_preflight.partitions(value),
        "capacity": historical_preflight.capacity(value, {"massive_basic": {"requests_per_minute": 5, "history_years": 2}}),
        "real_symbols_evaluated": 0, "new_models_fitted": 0, "network_requests": 0, "released_score": None}
    schema = json.loads((ROOT / "schemas/historical_preflight.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    validator.validate(result)
    if mutation == "pretend_real_run": result["real_symbols_evaluated"] = 1100
    else: result["calendar_plan"]["partitions"][-1]["performance_results_permitted_now"] = True
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(result)
