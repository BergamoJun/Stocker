"""ILLUSTRATIVE — NOT LIVE MARKET DATA. Integration boundaries and replay."""

from copy import deepcopy
from pathlib import Path
import hashlib
import json
import socket

import jsonschema
import pytest

from research import integrated_inputs as inputs, integrated_report as report, integrated_report_demo as demo
from research import news_archive, news_connection_demo, news_review_store
from research.integrated_report_render import markdown
from research.local_files import atomic_json
from research.sec_report import ROOT, SYNTHETIC_LABEL
from research.validation import digest


@pytest.fixture(autouse=True)
def isolated_news_store(tmp_path, monkeypatch):
    # Preserve the existing connector's private-directory restriction in tests.
    from research import news_connections
    names = set(news_connections.CORE + news_review_store.LEGACY_FILES + ["config/news_archive_policy.json", "schemas/news_archive.schema.json"])
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / name).read_bytes())
    for module in (news_archive, news_connections, news_review_store):
        monkeypatch.setattr(module, "ROOT", tmp_path)


@pytest.fixture
def case(tmp_path):
    return demo.setup(tmp_path / "reports/private/fixture")


def market(case, at=None):
    return report.market("1001", "BE", at or case["clock"](), input_root=case["input_root"], illustrative=True)


def capture(case, selected=None):
    case["clock"].advance(2)
    result = inputs.capture("1001", "BE", selected if selected is not None else case["selected"], root=case["input_root"], illustrative=True, clock=case["clock"])
    case["clock"].advance(2)
    return result


def change_snapshot(pair, **changes):
    prices = pair[0]
    data = json.loads(prices.read_text(encoding="utf-8"))
    data.update(changes)
    snapshot = {k: v for k, v in data.items() if k != "raw_sha256"}
    raw = json.dumps({"label": SYNTHETIC_LABEL, "snapshot": snapshot}, sort_keys=True).encode()
    prices.with_name("raw_response.json").write_bytes(raw)
    data["raw_sha256"] = hashlib.sha256(raw).hexdigest()
    prices.write_text(json.dumps(data), encoding="utf-8")


def test_complete_report_connects_all_sections_at_one_cutoff(case):
    result = demo.build(case)
    assert result["identity"] == {"cik": "0000001001", "symbol": "BE", "name": "ILLUSTRATIVE BE"}
    assert result["coverage"]["qoq_calculated"] == result["coverage"]["ttm_calculated"] == 5
    assert result["coverage"]["price_scenario_available"] and result["coverage"]["proxies_available"] == 2
    assert result["coverage"]["news_highlights"] == 2
    assert all(result[section]["as_of"] == result["as_of"] for section in ("financial", "market", "news"))
    assert result["market"]["stock"]["forecast"]["as_of"] == result["as_of"]
    assert {e["opinions"][0]["direction"] for e in result["news"]["snapshot"]["events"]} == {-1, 1}
    assert report.validate(result) == result


def test_missing_issuer_adapters_keep_financial_report_without_be_contamination(case):
    result = demo.build(case, "1003")
    assert result["identity"]["symbol"] == "DEMOC"
    assert result["financial"]["base_report"]["base_report"]["context"]["review_coverage"]["reviewed_metrics"] > 0
    assert result["coverage"]["qoq_calculated"] == result["coverage"]["ttm_calculated"] == 0
    assert result["market"]["stock"]["reason_codes"] == ["NO_REVIEWED_ISSUER_PRICE_ADAPTER"]
    assert all(p["symbol"] is None for p in result["market"]["proxies"])
    assert result["news"]["snapshot"] is result["input_binding"] is None
    assert not any(s["kind"] in {"personal_price", "news_original_link"} for s in result["source_catalog"])


def test_future_cutoff_rejected(case):
    with pytest.raises(ValueError, match="FUTURE_CUTOFF"):
        demo.build(case, cutoff="2026-11-09T00:00:00Z")


def test_unidentified_issuer_cannot_receive_a_corporate_report(case):
    with pytest.raises(ValueError, match="ISSUER_IDENTITY_UNAVAILABLE"):
        demo.build(case, "9999")


def test_first_input_commit_and_entire_equal_second_are_unavailable(case):
    for at in ("2026-11-08T13:15:21Z", "2026-11-08T13:15:22.999999Z"):
        result, binding = market(case, at)
        assert binding is None
        assert result["stock"]["forecast"] is None
    assert market(case, "2026-11-08T13:15:23Z")[0]["stock"]["current_input"]


def test_input_capture_deduplicates_without_resetting_first_availability(case):
    original = case["receipt"]
    repeated = capture(case)
    assert repeated["status"] == "unchanged" and repeated["available_at"] == original["available_at"]
    assert len(inputs.receipts("1001", root=case["input_root"], illustrative=True)) == 1


def test_changed_source_and_reversion_receive_new_sequences(case):
    original = market(case)[1]
    pair = case["selected"]["stock"]
    before = {name: pair[0].with_name(name).read_bytes() for name in inputs.NAMES}
    demo.price_files(pair[0].parent, shift=10)
    capture(case)
    second = market(case)[1]
    for name, raw in before.items():
        pair[0].with_name(name).write_bytes(raw)
    capture(case)
    third = market(case)[1]
    assert (original["sequence"], second["sequence"], third["sequence"]) == (1, 2, 3)
    assert original["packet_id"] != third["packet_id"]
    assert original["available_at"] != third["available_at"]
    assert inputs.as_of("1001", "2026-11-08T13:15:25Z", root=case["input_root"], illustrative=True)["sequence"] == 1


def test_original_price_files_can_change_without_rewriting_saved_report(case):
    value = demo.build(case)
    demo.price_files(case["selected"]["stock"][0].parent, shift=20)
    assert report.validate(value) == value


def test_bad_latest_input_does_not_fall_back_to_an_older_valid_one(case):
    case["selected"]["stock"][0].with_name("raw_response.json").write_text("corrupt", encoding="utf-8")
    capture(case)
    prices, _ = market(case)
    assert prices["stock"]["forecast"] is None
    assert prices["stock"]["reason_codes"] == ["PRICE_SOURCE_VALIDATION_FAILED"]
    assert all(p["reason_codes"] == ["REFERENCE_STOCK_SESSION_UNAVAILABLE"] for p in prices["proxies"])


def test_rights_are_checked_before_reading_price_content(case, monkeypatch):
    pair = case["selected"]["stock"]
    record = json.loads(pair[1].read_text(encoding="utf-8"))
    record["uses"]["derived_analytics"] = "denied"
    pair[1].write_text(json.dumps(record), encoding="utf-8")
    original = inputs.sec_refresh.read
    def guarded(path):
        if Path(path) == pair[0]:
            raise AssertionError("PRICE_PARSED_BEFORE_RIGHTS")
        return original(path)
    monkeypatch.setattr(inputs.sec_refresh, "read", guarded)
    capture(case)
    assert market(case)[0]["stock"]["reason_codes"] == ["PRICE_RIGHTS_NOT_CLEARED"]


@pytest.mark.parametrize("change", [
    {"symbol": "OTHER"}, {"cik": "0000001002"}, {"currency": "EUR"}, {"quote_kind": "delayed"},
    {"instrument_type": "etf"}, {"is_illustrative": False}, {"retrieved_at": "2026-11-09T00:00:00Z"},
])
def test_invalid_price_identity_basis_and_time_are_withheld(case, change):
    change_snapshot(case["selected"]["stock"], **change)
    capture(case)
    assert market(case)[0]["stock"]["forecast"] is None


def test_leveraged_or_wrong_proxy_cannot_enter_context(case):
    change_snapshot(case["selected"]["sector"], leveraged_or_inverse=True)
    change_snapshot(case["selected"]["market"], symbol="OTHER")
    capture(case)
    result, _ = market(case)
    assert result["stock"]["status"] == "available_research_scenario"
    assert all(p["status"] == "unavailable" for p in result["proxies"])


def test_stale_stock_withholds_plan_and_proxy_alignment(case):
    demo.price_files(case["selected"]["stock"][0].parent, end="2026-11-05")
    capture(case)
    prices, _ = market(case)
    assert prices["stock"]["reason_codes"] == ["PRICE_STALE_OR_UNUSABLE"]
    assert "reference_close" not in prices["stock"]["input"]
    assert all(p["status"] == "unavailable" for p in prices["proxies"])


def test_stale_proxy_cannot_masquerade_as_current_context(case):
    demo.price_files(case["selected"]["sector"][0].parent, "PBW", end="2026-11-05")
    capture(case)
    prices, _ = market(case)
    assert prices["stock"]["current_input"]
    assert prices["proxies"][0]["status"] == "available_price_observation"
    assert prices["proxies"][1]["reason_codes"] == ["PROXY_STALE_OR_REFERENCE_MISMATCH"]


def test_missing_price_role_preserves_other_context_and_financials(case):
    capture(case, {k: v for k, v in case["selected"].items() if k != "sector"})
    result = demo.build(case)
    assert result["coverage"]["proxies_available"] == 1
    assert result["coverage"]["qoq_calculated"] == 5


def test_input_copy_tampering_is_detected(case):
    packet = inputs.as_of("1001", case["clock"](), root=case["input_root"], illustrative=True)
    path = Path(packet["folder"]) / "stock/prices.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="INPUT_FILE_CHANGED"):
        market(case)


def test_future_input_packet_cannot_break_an_earlier_report(case):
    value = demo.build(case)
    demo.price_files(case["selected"]["stock"][0].parent, shift=8)
    capture(case)
    packet = inputs.as_of("1001", case["clock"](), root=case["input_root"], illustrative=True)
    (Path(packet["folder"]) / "stock/prices.json").write_text("corrupt", encoding="utf-8")
    assert report.validate(value) == value


def test_news_same_second_review_is_excluded_from_all_report_provenance(case):
    a = report.news("1001", "BE", "2026-11-08T13:15:20.999999Z", case["clock"](), root=case["news_root"], archive_root=case["archive_root"], illustrative=True)
    assert a["snapshot"]["review_receipt_refs"] == []
    assert a["snapshot"]["highlights"] == []


def test_stale_news_loses_highlights_without_neutral_or_zero_score(case):
    value = report.news("1001", "BE", "2026-11-08T14:00:00Z", "2026-11-08T14:00:01Z", root=case["news_root"], archive_root=case["archive_root"], illustrative=True)
    assert value["snapshot"]["highlights"] == []
    assert not value["snapshot"]["coverage_current_at_cutoff"]
    assert value["snapshot"]["experimental_news_score"] is None
    assert value["reason_codes"] == ["LATEST_NEWS_COVERAGE_NOT_CONFIRMED"]


def test_news_source_update_does_not_rewrite_original_report(case):
    value = demo.build(case)
    changed = deepcopy(case["context"])
    changed["as_of"] = "2026-11-08T13:20:00Z"
    changed["news"]["articles"][0]["title"] = SYNTHETIC_LABEL + " changed"
    for a in changed["news"]["articles"]:
        a["retrieved_at"] = changed["as_of"]
    path = case["folder"] / "changed_context.json"
    atomic_json(path, changed)
    news_archive.capture(path, root=case["archive_root"], clock=lambda: "2026-11-08T13:20:02Z")
    assert report.validate(value) == value
    current = report.news("1001", "BE", "2026-11-08T13:20:10Z", "2026-11-08T13:20:11Z", root=case["news_root"], archive_root=case["archive_root"], illustrative=True)
    assert len(current["snapshot"]["highlights"]) == 1


@pytest.mark.parametrize("field", ["released_score", "released_price_plan", "released_expected_target_sessions", "validation_id"])
def test_integration_cannot_enable_released_prediction_fields(case, field):
    value = demo.build(case)
    value[field] = 50
    with pytest.raises(jsonschema.ValidationError):
        report.validate(value)


def test_any_changed_calculation_or_cutoff_fails_replay(case):
    original = demo.build(case)
    for field in ("market", "news", "coverage", "source_catalog", "one_line_ko"):
        value = deepcopy(original)
        if field == "market":
            value[field]["stock"]["input"]["reference_close"] = "999999"
        elif field == "news":
            value[field]["snapshot"]["highlights"] = []
        elif field == "coverage":
            value[field]["news_highlights"] = 99
        elif field == "source_catalog":
            value[field][0]["url"] = "https://example.com/unrelated"
        else:
            value[field] = "changed"
        with pytest.raises(ValueError, match="REPLAY_MISMATCH"):
            report.validate(value)


def test_altered_method_and_financial_identity_are_rejected(case):
    original = demo.build(case)
    altered = deepcopy(original)
    altered["provenance"]["method_hashes"]["research/integrated_report.py"] = "0" * 64
    with pytest.raises(ValueError, match="METHOD_CHANGED"):
        report.validate(altered)
    altered = deepcopy(original)
    altered["identity"]["cik"] = "0000001003"
    with pytest.raises(ValueError, match="IDENTITY_OR_TIME_CHANGED"):
        report.validate(altered)


def test_written_bundle_validates_and_detects_changed_markdown(case):
    value = demo.build(case)
    folder = report.write(value, case["folder"] / "out")
    assert report.validate_file(folder / "report.json") == value
    with pytest.raises(ValueError, match="ALREADY_EXISTS"):
        report.write(value, case["folder"] / "out")
    (folder / "NEWS.ko.md").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="FILE_CHANGED"):
        report.validate_file(folder / "report.json")


def test_rendering_defines_units_periods_research_and_contrary_cases(case):
    value = demo.build(case)
    output = markdown(value)
    for required in ("백만 USD", "전분기 대비(QoQ)", "최근 12개월(TTM)", "실시간 현재가", "긍정 영향", "부정 영향", "강세 경로", "약세 경로", "원자재 **가격 수치**", "정식 목표가"):
        assert required in output
    assert "https://example.com/illustrative-story-1" in output
    assert "https://example.com/illustrative-story-2" in output


def test_no_network_key_training_or_old_forecast_mutation(case, monkeypatch):
    from research import context_journal, validation, learned_peak
    def forbidden(*args, **kwargs):
        raise AssertionError("UNAUTHORIZED_SIDE_EFFECT")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(context_journal, "register", forbidden)
    monkeypatch.setattr(context_journal, "settle", forbidden)
    monkeypatch.setattr(validation, "register_forecast", forbidden)
    monkeypatch.setattr(validation, "settle_forecasts", forbidden)
    if hasattr(learned_peak, "fit"):
        monkeypatch.setattr(learned_peak, "fit", forbidden)
    before = digest(case["context_path"])
    value = demo.build(case)
    assert digest(case["context_path"]) == before
    assert value["provenance"]["network_requests"] == value["provenance"]["external_llm_calls"] == 0
    assert value["provenance"]["model_training_performed"] is False
    assert not value["original_forecasts_modified"]


def test_illustrative_report_cannot_be_relabelled_as_real(case):
    value = demo.build(case)
    value["is_illustrative"] = False
    with pytest.raises(ValueError, match="IDENTITY_OR_TIME_CHANGED"):
        report.validate(value)


def test_demo_matches_golden_contract(tmp_path):
    result = demo.run(tmp_path / "reports/private/golden")
    expected = json.loads((ROOT / "examples/integrated_report_golden.json").read_text(encoding="utf-8"))
    assert result["golden"] == expected


def test_report_cli_validation_only_emits_status(case, capsys):
    value = demo.build(case)
    folder = report.write(value, case["folder"] / "cli")
    assert report.main(["--validate", str(folder / "report.json")]) == 0
    assert json.loads(capsys.readouterr().out) == {"integrated_report_replay_valid": True}


def test_commit_time_is_sampled_after_copy_and_clock_reversal_rejected(case):
    demo.price_files(case["selected"]["stock"][0].parent, shift=3)
    ticks = iter(["2026-11-08T13:15:25Z", "2026-11-08T13:16:05Z"])
    receipt = inputs.capture("1001", "BE", case["selected"], root=case["input_root"], illustrative=True, clock=lambda: next(ticks))
    assert receipt["available_at"] == "2026-11-08T13:16:05Z"
    assert market(case, "2026-11-08T13:16:04Z")[1]["sequence"] == 1
    demo.price_files(case["selected"]["stock"][0].parent, shift=4)
    ticks = iter(["2026-11-08T13:16:10Z", "2026-11-08T13:16:09Z"])
    with pytest.raises(ValueError, match="CLOCK_NOT_ADVANCED"):
        inputs.capture("1001", "BE", case["selected"], root=case["input_root"], illustrative=True, clock=lambda: next(ticks))
    assert len(inputs.receipts("1001", root=case["input_root"], illustrative=True)) == 2


def test_simulation_failure_keeps_current_observed_proxy_data(case, monkeypatch):
    def unsupported(*args, **kwargs):
        raise ValueError("ILLUSTRATIVE unavailable model support")
    monkeypatch.setattr(report.peak_simulation, "forecast", unsupported)
    value = demo.build(case)
    assert not value["coverage"]["price_scenario_available"]
    assert value["coverage"]["proxies_available"] == 2
    assert value["coverage"]["qoq_calculated"] == 5
    assert value["market"]["stock"]["current_input"]


def test_missing_all_prices_preserves_reviewed_news_and_financials(case):
    capture(case, {})
    value = demo.build(case)
    assert not value["coverage"]["price_scenario_available"]
    assert value["coverage"]["proxies_available"] == 0
    assert value["coverage"]["news_highlights"] == 2
    assert value["coverage"]["ttm_calculated"] == 5


def test_new_conflicting_news_review_suppresses_both_directions(case):
    original = demo.build(case)
    manifest = demo.shifted(news_connection_demo.manifest(news_connection_demo.context(), reviewer="contrary", direction=-1))
    from research.validation import stable_hash
    manifest["news_context_sha256"] = stable_hash(case["context"]["news"])
    path = case["folder"] / "contrary.json"
    atomic_json(path, manifest)
    news_review_store.store(case["context_path"], path, root=case["news_root"], archive_root=case["archive_root"], illustrative=True, clock=lambda: "2026-11-08T13:15:30Z")
    assert report.validate(original) == original
    value = report.news("1001", "BE", "2026-11-08T13:15:35Z", "2026-11-08T13:15:36Z", root=case["news_root"], archive_root=case["archive_root"], illustrative=True)
    assert value["snapshot"]["highlights"] == []
    assert {e["status"] for e in value["snapshot"]["events"]} == {"conflicting_reviews"}


def test_empty_news_archive_leaves_topic_coverage_unknown(case):
    value = report.news("1001", "BE", case["clock"](), case["clock"](), root=case["folder"] / "empty_reviews", archive_root=case["folder"] / "empty_archive", illustrative=True)
    assert value["snapshot"]["articles"] == value["snapshot"]["highlights"] == []
    assert value["snapshot"]["coverage"] is None
    assert not value["snapshot"]["coverage_current_at_cutoff"]


def test_illustrative_price_rights_cannot_authorize_real_inputs(case):
    from research.rights import RightsError
    with pytest.raises(RightsError, match="cannot be interchanged"):
        inputs.verify(*case["selected"]["stock"], role="stock", issuer="1664703", symbol="BE", cutoff=case["clock"](), illustrative=False)
    assert inputs.profile("1001", "BE", False) is None
    assert inputs.profile("1664703", "BE", True) is None


def test_rehashed_markdown_change_cannot_claim_valid_render(case):
    folder = report.write(demo.build(case), case["folder"] / "render")
    (folder / "REPORT.ko.md").write_text("changed", encoding="utf-8")
    bundle = json.loads((folder / "bundle.json").read_text(encoding="utf-8"))
    bundle["files"]["REPORT.ko.md"] = digest(folder / "REPORT.ko.md")
    (folder / "bundle.json").write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(ValueError, match="RENDER_CHANGED"):
        report.validate_file(folder / "report.json")


def test_runtime_does_not_read_provider_keys(case, monkeypatch):
    import os
    original = os.environ.get
    def guard(key, *args):
        if key in {"MASSIVE_API_KEY", "SEC_USER_AGENT"}:
            raise AssertionError("INTEGRATION_READ_PROVIDER_KEY")
        return original(key, *args)
    monkeypatch.setattr(os.environ, "get", guard)
    assert demo.build(case)["provenance"]["external_llm_calls"] == 0
