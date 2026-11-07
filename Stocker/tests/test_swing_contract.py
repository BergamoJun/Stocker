"""ILLUSTRATIVE — NOT LIVE MARKET DATA.

These tests enforce JSON contracts only. Synthetic prices/model IDs do not
implement or validate a trading strategy, calendar, entitlement or score model.
"""

import json
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[1]
LABEL = "ILLUSTRATIVE — NOT LIVE MARKET DATA"


def read_json(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


@pytest.fixture
def validator():
    return jsonschema.Draft202012Validator(
        read_json("schemas/research_report.schema.json"),
        format_checker=jsonschema.FormatChecker(),
    )


@pytest.fixture
def available_report():
    report = read_json("examples/illustrative_report.json")
    report["subject"].update(
        name=LABEL, symbol="FIXTURE", instrument_type="equity", horizon="swing"
    )
    report["data_status"].update(
        stale=False, quote_as_of="2026-11-06T21:00:00Z",
        missing=["검증된 실서비스 매력도/도달기간 모델"],
    )
    report["sources"] = [{
        "id": "fixture_market",
        "title": LABEL,
        "publisher": LABEL,
        "source_type": "market_vendor",
        "url": "https://example.invalid/fixtures/market.json",
        "published_at": "2026-11-06T21:00:00Z",
        "retrieved_at": "2026-11-06T21:05:00Z",
    }]
    report["swing"].update(
        status="available", headline=LABEL, reasons=[],
        price_plan={
            "direction": "long",
            "reference_quote": {
                "instrument_id": "fixture_equity",
                "name": LABEL, "symbol": "FIXTURE", "instrument_type": "equity",
                "exchange": "FIXTURE_EXCHANGE",
                "exchange_timezone": "America/New_York",
                "session_date": "2026-11-06",
                "observed_at": "2026-11-06T21:00:00Z",
                "quote_type": "eod", "delay_minutes": None,
                "price": 100, "currency": "USD",
                "price_basis": "current_share_basis",
                "history_adjustment": "split_adjusted",
                "source_ids": ["fixture_market"],
            },
            "proxy": None,
            "first_target_price": 106,
            "invalidation_price": 98,
            "invalidation_condition": LABEL,
            "upside_pct": 6, "downside_pct": 2, "reward_risk_ratio": 3,
            "return_basis": "gross_price_change",
            "assumptions": [LABEL],
            "methodology_id": "fixture_target_only",
            "methodology_version": "0.0.0-test",
            "validation_id": "fixture_validation_only",
            "input_snapshot_id": "fixture_snapshot",
            "calculation_ids": ["fixture_calculation"],
            "source_ids": ["fixture_market"],
            "eligibility": {
                "public_display": "verified", "derived_analytics": "verified",
                "llm_input": "verified", "rights_review_id": "fixture_review_only",
                "price_freshness": "fresh",
            },
            "timing": {
                "holding_sessions": 5,
                "calendar_id": "fixture_calendar_not_a_live_calendar",
                "calendar_version": "0.0.0-test",
                "review_at": "2026-11-13T21:00:00Z",
                "expires_at": "2026-11-09T21:00:00Z",
                "take_profit_condition": LABEL,
                "time_review_condition": LABEL,
                "expected_target_sessions": None,
                "methodology_id": None, "methodology_version": None,
                "validation_id": None,
            },
        },
    )
    return report


def enable_synthetic_score(report, score=72):
    report["swing"]["attractiveness"] = {
        "status": "available", "score": score, "validation_status": "validated",
        "methodology_id": "fixture_score_only",
        "methodology_version": "0.0.0-test",
        "validation_id": "fixture_validation_only",
        "input_snapshot_id": "fixture_snapshot",
        "components": [{
            "factor": "fixture_factor", "impact": "mixed",
            "rationale": LABEL, "source_ids": ["fixture_market"],
        }],
        "reasons": [],
    }


def not_requested_swing():
    return {
        "status": "not_requested", "headline": None, "reasons": [],
        "price_plan": None,
        "attractiveness": {
            "status": "not_requested", "score": None,
            "validation_status": "not_applicable",
            "methodology_id": None, "methodology_version": None,
            "validation_id": None, "input_snapshot_id": None,
            "components": [], "reasons": [],
        },
    }


def test_no_price_fixture_explicitly_withholds_swing_values(validator):
    report = read_json("examples/illustrative_report.json")
    validator.validate(report)
    assert report["subject"]["horizon"] == "swing"
    assert report["swing"]["status"] == "unavailable"
    assert report["swing"]["price_plan"] is None
    assert report["swing"]["attractiveness"]["score"] is None
    assert report["swing"]["reasons"]


@pytest.mark.parametrize("horizon", ["short", "medium", "long"])
def test_existing_horizons_retain_research_without_swing(validator, horizon):
    report = read_json("examples/illustrative_report.json")
    report["subject"]["horizon"] = horizon
    report["swing"] = not_requested_swing()
    validator.validate(report)


def test_swing_request_cannot_silently_be_not_requested(validator):
    report = read_json("examples/illustrative_report.json")
    report["swing"] = not_requested_swing()
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(report)


def test_other_horizon_cannot_publish_swing_plan(validator, available_report):
    available_report["subject"]["horizon"] = "medium"
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(available_report)


def test_price_plan_can_be_available_without_a_score_or_timing_model(
    validator, available_report
):
    validator.validate(available_report)
    assert available_report["swing"]["attractiveness"]["score"] is None
    assert available_report["swing"]["price_plan"]["timing"]["expected_target_sessions"] is None


@pytest.mark.parametrize("score", [1, 100])
def test_documented_integer_score_boundaries(validator, available_report, score):
    enable_synthetic_score(available_report, score)
    validator.validate(available_report)


@pytest.mark.parametrize("score", [0, 101, 1.5, True])
def test_out_of_range_or_noninteger_scores_are_rejected(
    validator, available_report, score
):
    enable_synthetic_score(available_report, score)
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(available_report)


@pytest.mark.parametrize("field", [
    "methodology_id", "methodology_version", "validation_id", "input_snapshot_id",
])
def test_numeric_score_requires_model_and_snapshot_metadata(
    validator, available_report, field
):
    enable_synthetic_score(available_report)
    available_report["swing"]["attractiveness"][field] = None
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(available_report)


def test_unvalidated_model_cannot_publish_a_score(validator, available_report):
    enable_synthetic_score(available_report)
    score = available_report["swing"]["attractiveness"]
    score.update(validation_status="not_validated", validation_id=None)
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(available_report)


def test_unavailable_score_cannot_keep_a_numeric_default(validator, available_report):
    available_report["swing"]["attractiveness"]["score"] = 50
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(available_report)


def test_available_score_requires_explanatory_components(validator, available_report):
    enable_synthetic_score(available_report)
    available_report["swing"]["attractiveness"]["components"] = []
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(available_report)


def test_unavailable_plan_cannot_keep_price_values(validator, available_report):
    available_report["swing"].update(status="unavailable", reasons=[LABEL])
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(available_report)


def test_unavailable_plan_cannot_keep_an_available_score(validator, available_report):
    enable_synthetic_score(available_report)
    available_report["swing"].update(
        status="unavailable", reasons=[LABEL], price_plan=None,
    )
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(available_report)


def test_unavailable_plan_requires_a_reason(validator):
    report = read_json("examples/illustrative_report.json")
    report["swing"]["reasons"] = []
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(report)


@pytest.mark.parametrize("field", [
    "reference_quote", "methodology_id", "methodology_version", "validation_id",
    "input_snapshot_id", "calculation_ids", "eligibility", "timing", "source_ids",
])
def test_price_plan_requires_auditable_inputs(validator, available_report, field):
    del available_report["swing"]["price_plan"][field]
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(available_report)


@pytest.mark.parametrize("field", ["public_display", "derived_analytics", "llm_input"])
def test_unknown_price_permissions_cannot_enable_a_plan(
    validator, available_report, field
):
    available_report["swing"]["price_plan"]["eligibility"][field] = "unknown"
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(available_report)


@pytest.mark.parametrize("field", [
    "instrument_id", "source_ids", "observed_at", "currency",
    "exchange_timezone", "session_date", "price_basis", "history_adjustment",
])
def test_quote_requires_identity_provenance_and_price_basis(
    validator, available_report, field
):
    del available_report["swing"]["price_plan"]["reference_quote"][field]
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(available_report)


def test_quote_cannot_have_empty_sources(validator, available_report):
    available_report["swing"]["price_plan"]["reference_quote"]["source_ids"] = []
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(available_report)


@pytest.mark.parametrize("field", [
    "first_target_price", "invalidation_price", "reward_risk_ratio",
])
def test_price_plan_requires_positive_price_and_ratio(
    validator, available_report, field
):
    available_report["swing"]["price_plan"][field] = 0
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(available_report)


def test_stale_report_cannot_publish_available_swing(validator, available_report):
    available_report["data_status"]["stale"] = True
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(available_report)


def test_available_swing_requires_quote_as_of(validator, available_report):
    available_report["data_status"]["quote_as_of"] = None
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(available_report)


def test_delayed_quote_requires_known_positive_delay(validator, available_report):
    quote = available_report["swing"]["price_plan"]["reference_quote"]
    quote["quote_type"] = "delayed"
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(available_report)
    quote["delay_minutes"] = 15
    validator.validate(available_report)


def test_unknown_identity_cannot_publish_a_plan(validator, available_report):
    available_report["subject"]["instrument_type"] = "unknown"
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(available_report)


def test_sector_price_plan_requires_named_fund_proxy(validator, available_report):
    available_report["subject"].update(instrument_type="sector", symbol=None)
    plan = available_report["swing"]["price_plan"]
    plan["reference_quote"]["instrument_type"] = "fund"
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(available_report)
    plan["proxy"] = {
        "instrument_id": "fixture_equity", "name": LABEL, "symbol": "FIXTURE",
        "instrument_type": "fund", "selection_rationale": LABEL,
        "source_ids": ["fixture_market"],
    }
    validator.validate(available_report)
    plan["proxy"]["instrument_type"] = "equity"
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(available_report)


def test_fund_cannot_publish_equity_quote(validator, available_report):
    available_report["subject"]["instrument_type"] = "fund"
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(available_report)


def test_equity_cannot_use_an_undeclared_proxy(validator, available_report):
    available_report["swing"]["price_plan"]["proxy"] = {
        "instrument_id": "fixture_proxy", "name": LABEL, "symbol": "FIXTURE",
        "instrument_type": "fund", "selection_rationale": LABEL,
        "source_ids": ["fixture_market"],
    }
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(available_report)


def test_target_time_estimate_requires_its_own_model(validator, available_report):
    timing = available_report["swing"]["price_plan"]["timing"]
    timing["expected_target_sessions"] = {"min": 2, "max": 5}
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(available_report)
    timing.update(
        methodology_id="fixture_timing_only", methodology_version="0.0.0-test",
        validation_id="fixture_validation_only",
    )
    validator.validate(available_report)


def test_absent_time_estimate_cannot_claim_a_model(validator, available_report):
    timing = available_report["swing"]["price_plan"]["timing"]
    timing.update(
        methodology_id="fixture_timing_only", methodology_version="0.0.0-test",
        validation_id="fixture_validation_only",
    )
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(available_report)


def test_holding_window_cannot_silently_change(validator, available_report):
    available_report["swing"]["price_plan"]["timing"]["holding_sessions"] = 7
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(available_report)


def test_timestamps_must_be_parseable(validator, available_report):
    available_report["swing"]["price_plan"]["timing"]["expires_at"] = "next Friday"
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(available_report)


def test_legacy_version_is_not_silently_accepted(validator):
    report = read_json("examples/illustrative_report.json")
    report["schema_version"] = "0.1.0"
    del report["swing"]
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(report)


def test_synthetic_swing_citations_reference_fixture_sources(available_report):
    enable_synthetic_score(available_report)
    referenced = set()

    def collect(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"source_ids", "evidence_ids"}:
                    referenced.update(child)
                else:
                    collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(available_report["swing"])
    assert referenced == {"fixture_market"}
    assert referenced <= {source["id"] for source in available_report["sources"]}
