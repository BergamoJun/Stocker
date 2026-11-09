"""ILLUSTRATIVE — NOT LIVE MARKET DATA. Offline SEC collector contracts."""

from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from research import sec_facts as facts, sec_refresh as refresh, sec_transport as transport
from research import sec_refresh_demo as demo
from research.local_files import atomic_json, exclusive_lock
from research.rights import as_utc
from research.sec_report import ROOT
from research.validation import digest


@pytest.fixture
def case(tmp_path):
    routes, watchlist = demo.fixture()
    clock = demo.Clock()
    opener = demo.Opener(routes)
    client = transport.Client("Stocker fixture analyst@fixture.invalid", illustrative=True, opener=opener,
        root=tmp_path / "http", raw_root=tmp_path / "raw", clock=clock, sleep=clock.advance)
    return {"root": tmp_path, "routes": routes, "watchlist": watchlist, "clock": clock, "opener": opener,
        "client": client, "store": tmp_path / "store", "issuer": "0000001001"}


def run(case, **kwargs):
    return refresh.refresh(case["watchlist"], client=case["client"], store=case["store"], clock=case["clock"], **kwargs)


def financials(case):
    issuer = case["issuer"]
    payload = case["routes"][f"https://data.sec.gov/submissions/CIK{issuer}.json"]
    rows = facts.filings(payload, issuer, transport.policy()["tracked_forms"])
    value = case["routes"][f"https://data.sec.gov/api/xbrl/companyfacts/CIK{issuer}.json"]
    return facts.normalize(value, issuer, rows, "fixture:source")["sections"]


def quarter(case):
    return next(s for s in financials(case) if s["form"] == "10-Q")


@pytest.mark.parametrize("value", [0, -1, "0", "abc", "12345678901", "../1", 1.5, None, True])
def test_invalid_cik_fails_closed(value):
    with pytest.raises(ValueError): facts.cik(value)


def test_resolves_three_issuers_and_deduplicates_share_classes(case):
    reference = case["routes"][refresh.REFERENCE]
    reference["data"] += [[1001, "ILLUSTRATIVE DEMOA", "DEMOA.A", "FIXTURE"], [1001, "ILLUSTRATIVE DEMOA", "DEMOA.B", "FIXTURE"]]
    case["watchlist"]["entities"] += [{"symbol": "demoa-a"}, {"symbol": "DEMOA.B"}]
    entities, errors = facts.resolve(case["watchlist"], reference)
    assert len(entities) == 3 and errors == []
    assert entities[0]["symbols"] == ["DEMOA", "DEMOA-A", "DEMOA.B"]


@pytest.mark.parametrize("entry,reason", [({"symbol": "UNKNOWN"}, "UNKNOWN_CURRENT_SYMBOL"), ({"symbol": "DEMOA", "cik": "1002"}, "CIK_SYMBOL_MISMATCH")])
def test_unknown_or_conflicting_ticker_never_substitutes(case, entry, reason):
    entities, errors = facts.resolve({"entities": [entry]}, case["routes"][refresh.REFERENCE])
    assert entities == [] and errors[0]["reason"] == reason


def test_ambiguous_symbols_are_not_selected_by_first_row(case):
    reference = case["routes"][refresh.REFERENCE]
    reference["data"].append([1002, "ILLUSTRATIVE OTHER", "DEMOA", "FIXTURE"])
    entities, errors = facts.resolve({"entities": [{"symbol": "DEMOA"}]}, reference)
    assert entities == [] and errors[0]["reason"] == "AMBIGUOUS_SYMBOL"


def test_pinned_cik_works_without_current_symbol_or_reference_request(case):
    case["watchlist"] = {"entities": [{"cik": "1001"}]}
    result = run(case)
    assert len(result["entities"]) == 1
    assert refresh.REFERENCE not in [c["url"] for c in case["opener"].calls]


@pytest.mark.parametrize("mutation", ["cik", "columns", "accession"])
def test_malformed_submissions_are_rejected(case, mutation):
    payload = deepcopy(case["routes"]["https://data.sec.gov/submissions/CIK0000001001.json"])
    if mutation == "cik": payload["cik"] = 1002
    if mutation == "columns": payload["filings"]["recent"]["reportDate"].pop()
    if mutation == "accession": payload["filings"]["recent"]["accessionNumber"][0] = "../escape"
    with pytest.raises(ValueError): facts.filings(payload, "1001", transport.policy()["tracked_forms"])


def test_accession_prefix_may_identify_filing_agent_not_company(case):
    rows = facts.filings(case["routes"]["https://data.sec.gov/submissions/CIK0000001001.json"], "1001", transport.policy()["tracked_forms"])
    assert all(r["accession"].startswith("0000009999-") and r["source_url"] for r in rows)


def test_unapproved_document_path_keeps_metadata_without_fetching(case):
    payload = case["routes"]["https://data.sec.gov/submissions/CIK0000001001.json"]
    payload["filings"]["recent"]["primaryDocument"][0] = "../../other.htm"
    result = run(case)
    assert result["status"] == "completed_batch"
    snap = refresh.latest(case["store"], "1001")
    row = next(r for r in snap["filings"] if r["form"] == "10-K")
    assert row["source_url"] is None and row["document_status"] == "unsupported_document_name"


@pytest.mark.parametrize("url", ["http://data.sec.gov/submissions/CIK0000001001.json", "https://data.sec.gov.evil.test/submissions/CIK0000001001.json",
    "https://data.sec.gov@evil.test/submissions/CIK0000001001.json", "https://data.sec.gov/submissions/CIK0000001001.json?token=x",
    "https://www.sec.gov/Archives/edgar/data/1001/000000999926010011/../fake.htm", "https://www.sec.gov/Archives/edgar/data/1001/000000999926010011/file.js"])
def test_transport_only_allows_reviewed_sec_endpoints(url):
    with pytest.raises(ValueError): transport.endpoint(url)


def test_fake_contact_never_enables_live_transport():
    assert not transport.valid_user_agent("Research contact@example.com")
    assert not transport.valid_user_agent("Research user@fixture.invalid")
    assert not transport.valid_user_agent("Research user@valid.test\r\nSecret: x")
    with pytest.raises(ValueError): transport.Client("Research user@fixture.invalid", illustrative=True)


def test_missing_agent_makes_zero_requests_and_keeps_operator_report(case):
    case["client"].agent = ""
    result = run(case)
    assert result["status"] == "awaiting_user_agent" and result["network_requests"] == 0
    assert Path(result["report_path"]).exists()


def test_repeated_refresh_has_zero_requests_and_no_new_versions(case):
    first = run(case)
    versions = list(case["store"].glob("entities/*/snapshots/*.json"))
    second = run(case)
    assert len(first["entities"]) == len(second["entities"]) == 3
    assert second["network_requests"] == 0
    assert all(r["status"] == "unchanged" for r in second["entities"])
    assert list(case["store"].glob("entities/*/snapshots/*.json")) == versions


def test_304_keeps_original_availability_and_content_version(case):
    first = run(case)
    before = refresh.latest(case["store"], "1001")
    case["clock"].advance(86401)
    second = run(case)
    after = refresh.latest(case["store"], "1001")
    assert second["network_requests"] > 0 and before["available_at"] == after["available_at"]
    assert before["snapshot_id"] == after["snapshot_id"]
    assert any(c["conditional"] for c in case["opener"].calls)


@pytest.mark.parametrize("code", [403, 429, 503])
def test_upstream_limit_uses_shared_cooldown_without_retry_storm(case, code):
    url = refresh.REFERENCE
    case["routes"][url] = HTTPError(url, code, "fixture", {"Retry-After": "1800"}, None)
    first = run(case)
    second = run(case)
    assert first["status"] == second["status"] == "deferred"
    assert first["network_requests"] == 1 and second["network_requests"] == 0
    assert (as_utc(first["next_attempt_at"]) - as_utc(first["started_at"])).total_seconds() >= 1800


def test_redirect_is_rejected_without_following(case):
    url = refresh.REFERENCE
    case["routes"][url] = HTTPError(url, 302, "fixture", {"Location": "https://evil.test"}, None)
    result = run(case)
    assert result["status"] == "source_unavailable" and result["network_requests"] == 1


def test_shared_request_pacing_applies_across_client_instances(case):
    client = case["client"]
    first = client.get(refresh.REFERENCE)
    second_client = transport.Client(client.agent, root=case["root"] / "http", raw_root=case["root"] / "raw", illustrative=True,
        opener=case["opener"], clock=case["clock"], sleep=case["clock"].advance)
    second = second_client.get("https://data.sec.gov/submissions/CIK0000001001.json")
    assert (as_utc(second["checked_at"]) - as_utc(first["checked_at"])).total_seconds() >= 1


def test_daily_request_cap_waits_until_next_utc_day(case):
    client = case["client"]
    atomic_json(client.root / "request_state.json", {"day": "2026-11-08", "requests_today": transport.policy()["maximum_requests_per_day"]})
    result = run(case)
    assert result["status"] == "deferred" and result["network_requests"] == 0
    assert result["reason"] == "SEC_DAILY_BUDGET_REACHED"


def test_oversized_response_is_not_stored_but_counts_downloaded_bytes(case, monkeypatch):
    settings = transport.policy()
    settings["maximum_bytes"]["reference"] = 8
    monkeypatch.setattr(transport, "policy", lambda: settings)
    result = run(case)
    assert result["status"] == "source_unavailable"
    state = refresh.read(case["client"].root / "request_state.json")
    assert state["bytes_today"] == 9 and not list(case["client"].raw_root.glob("*/*.raw"))


def test_contact_echo_is_never_persisted(case):
    case["routes"][refresh.REFERENCE] = b"Access failed for analyst@fixture.invalid"
    result = run(case)
    assert result["reason"] == "SEC_CONTACT_ECHO_REJECTED"
    for path in case["root"].rglob("*"):
        if path.is_file(): assert b"analyst@fixture.invalid" not in path.read_bytes()


def test_tampered_raw_cache_is_not_used(case):
    entry = case["client"].get(refresh.REFERENCE)
    Path(entry["raw_path"]).write_bytes(b"corrupted")
    with pytest.raises(transport.Unavailable, match="SEC_CACHE_INTEGRITY_CHANGED"):
        case["client"].get(refresh.REFERENCE)


def test_decimal_input_and_output_never_round_through_binary_float(case):
    value = facts.loads(b'{"val": 9007199254740993.125}')
    assert value["val"] == Decimal("9007199254740993.125")
    payload = case["routes"]["https://data.sec.gov/api/xbrl/companyfacts/CIK0000001001.json"]
    payload["facts"]["us-gaap"]["Revenues"]["units"]["USD"][1]["val"] = value["val"]
    assert quarter(case)["metrics"]["revenue"]["value"] == "9007199254740993.125"


@pytest.mark.parametrize("literal", [b'NaN', b'Infinity', b'-Infinity'])
def test_nonfinite_json_is_not_a_fact(literal):
    with pytest.raises(ValueError): facts.loads(b'{"val":' + literal + b'}')


def test_quarter_income_and_ytd_cashflow_keep_different_period_labels(case):
    value = quarter(case)
    assert value["metrics"]["revenue"]["basis"] == "quarter"
    assert value["metrics"]["operating_cash_flow"]["basis"] == "cumulative_ytd"
    assert value["derived"]["free_cash_flow"]["value"] == "125"
    assert value["derived"]["free_cash_flow"]["basis"] == "cumulative_ytd"


def test_revenue_alias_conflict_withholds_value_instead_of_adding(case):
    payload = case["routes"]["https://data.sec.gov/api/xbrl/companyfacts/CIK0000001001.json"]["facts"]["us-gaap"]
    payload["SalesRevenueNet"] = deepcopy(payload["Revenues"])
    payload["SalesRevenueNet"]["units"]["USD"][1]["val"] += 1
    value = quarter(case)
    assert value["metrics"]["revenue"]["value"] is None
    assert value["metrics"]["revenue"]["status"] == "conflicting_standard_facts"
    assert value["derived"]["operating_margin_percent"]["value"] is None


def test_equal_aliases_are_not_double_counted(case):
    payload = case["routes"]["https://data.sec.gov/api/xbrl/companyfacts/CIK0000001001.json"]["facts"]["us-gaap"]
    payload["SalesRevenueNet"] = deepcopy(payload["Revenues"])
    assert quarter(case)["metrics"]["revenue"]["value"] == "2001"


@pytest.mark.parametrize("change", ["currency", "accession", "end", "form", "duration", "custom"])
def test_wrong_filing_period_currency_or_taxonomy_never_fills_missing_value(case, change):
    payload = case["routes"]["https://data.sec.gov/api/xbrl/companyfacts/CIK0000001001.json"]["facts"]
    tag = payload["us-gaap"]["Revenues"]
    row = tag["units"]["USD"][1]
    if change == "currency": tag["units"]["EUR"] = tag["units"].pop("USD")
    if change == "accession": row["accn"] = "0000009999-26-000001"
    if change == "end": row["end"] = "2026-06-30"
    if change == "form": row["form"] = "10-K"
    if change == "duration": row["start"] = "2026-01-01"
    if change == "custom": payload["custom"] = {"Revenues": payload["us-gaap"].pop("Revenues")}
    metric = quarter(case)["metrics"]["revenue"]
    assert metric["value"] is None and metric["status"] == "missing_exact_filing_period_unit"


def test_period_mismatch_prevents_free_cash_flow_calculation(case):
    payload = case["routes"]["https://data.sec.gov/api/xbrl/companyfacts/CIK0000001001.json"]["facts"]["us-gaap"]
    payload["PaymentsToAcquirePropertyPlantAndEquipment"]["units"]["USD"][1]["start"] = "2026-07-01"
    assert quarter(case)["derived"]["free_cash_flow"]["value"] is None


def test_amendment_is_separate_and_suppresses_unreviewed_derived_merge(case):
    demo.amendment(case["routes"])
    sections = financials(case)
    original = next(s for s in sections if s["form"] == "10-Q")
    amended = next(s for s in sections if s["form"] == "10-Q/A")
    assert original["accession"] != amended["accession"]
    assert original["metrics"]["revenue"]["value"] == "2001"
    assert all(s["derived"]["free_cash_flow"]["value"] is None for s in (original, amended))


def test_asof_does_not_backdate_to_reporting_or_acceptance_date(case):
    run(case)
    assert refresh.as_of(case["store"], "1001", "2026-11-07T23:59:59Z") is None
    assert refresh.latest(case["store"], "1001")["financials"]["score"] is None


def test_later_amendment_never_rewrites_earlier_snapshot(case):
    run(case)
    cutoff = case["clock"]()
    old = refresh.as_of(case["store"], "1001", cutoff)
    case["clock"].advance(3601)
    demo.amendment(case["routes"])
    run(case)
    again = refresh.as_of(case["store"], "1001", cutoff)
    current = refresh.latest(case["store"], "1001")
    assert old == again
    assert not any(r["is_amendment"] for r in again["filings"])
    assert any(r["is_amendment"] for r in current["filings"])


def test_recent_window_absence_does_not_delete_known_filings(case):
    run(case)
    columns = case["routes"]["https://data.sec.gov/submissions/CIK0000001001.json"]["filings"]["recent"]
    for key in columns: columns[key] = columns[key][1:]
    case["clock"].advance(3601)
    run(case)
    current = refresh.latest(case["store"], "1001")
    assert any(r["form"] == "10-K" for r in current["filings"])
    assert any(s["form"] == "10-K" for s in current["financials"]["sections"])


def test_newly_discovered_old_filing_is_not_new_market_event(case):
    run(case)
    columns = case["routes"]["https://data.sec.gov/submissions/CIK0000001001.json"]["filings"]["recent"]
    for key, value in zip(columns, ("0000009999-20-000001", "8-K", "2020-01-01", "2020-01-01", "old.htm", "2020-01-01T20:00:00Z")):
        columns[key].append(value)
    case["clock"].advance(3601)
    result = run(case)
    one = next(r for r in result["entities"] if r["cik"] == "0000001001")
    assert one["newly_observed_arrivals"] == [] and one["older_unseen_archive_additions"] == 1


def test_round_robin_cursor_resumes_other_issuers(case):
    first = run(case, max_entities=1)
    second = run(case, max_entities=1)
    third = run(case, max_entities=1)
    assert [r["entities"][0]["cik"] for r in (first, second, third)] == ["0000001001", "0000001002", "0000001003"]


def test_one_bad_issuer_does_not_stop_other_companies(case):
    case["routes"]["https://data.sec.gov/submissions/CIK0000001001.json"]["cik"] = "9999"
    result = run(case)
    assert result["status"] == "partial"
    assert len(result["entities"]) == 3 and result["entities"][0]["status"] == "unavailable"
    assert result["entities"][1]["status"] == "updated"


def test_funds_do_not_receive_operating_company_financial_playbook(case):
    case["routes"]["https://data.sec.gov/submissions/CIK0000001001.json"]["entityType"] = "investment"
    run(case)
    value = refresh.latest(case["store"], "1001")
    assert value["financials"]["sections"] == []
    assert "non_operating_or_unknown_entity_type" in value["financials"]["unsupported"]


def test_xbrl_lag_is_explicit_and_retried_after_short_delay(case):
    url = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000001001.json"
    original = case["routes"][url]
    case["routes"][url] = {"cik": 1001, "facts": {}}
    first = run(case)
    assert first["entities"][0]["awaiting_facts"] == 2
    case["routes"][url] = original
    case["clock"].advance(901)
    second = run(case)
    assert second["entities"][0]["awaiting_facts"] == 0


def test_unavailable_document_preserves_prior_raw_and_queues_retry(case):
    run(case)
    before = refresh.latest(case["store"], "1001")
    row = next(r for r in before["filings"] if r["form"] == "10-Q")
    case["routes"][row["source_url"]] = HTTPError(row["source_url"], 404, "fixture", {}, None)
    case["clock"].advance(86401)
    result = run(case)
    after = refresh.latest(case["store"], "1001")
    assert result["status"] == "partial"
    assert next(r for r in after["filings"] if r["form"] == "10-Q")["document_source_ids"] == row["document_source_ids"]
    assert refresh.read(case["store"] / "entities/0000001001/state.json")["pending_documents"]


def test_duplicate_worker_does_not_issue_requests(case):
    with exclusive_lock(case["store"] / "run.lock"):
        result = run(case)
    assert result["status"] == "another_refresh_running" and result["network_requests"] == 0


def test_snapshot_schema_and_source_links_fail_closed(case):
    run(case)
    snap = refresh.latest(case["store"], "1001")
    snap.pop("available_at"); snap.pop("sequence")
    snap["financials"]["sections"][0]["metrics"]["revenue"]["source_ids"] = ["unregistered"]
    with pytest.raises(ValueError, match="SEC_METRIC_SOURCE_MISSING"): refresh.validate_snapshot(snap)


def test_tampered_committed_snapshot_is_detected(case):
    run(case)
    path = next((case["store"] / "entities/0000001001/snapshots").glob("*.json"))
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="SEC_SNAPSHOT_INTEGRITY_CHANGED"): refresh.latest(case["store"], "1001")


def test_large_watchlist_resolves_without_using_be_specific_adapter():
    rows = [[i, "ILLUSTRATIVE", "FIX" + str(i), "FIXTURE"] for i in range(1, 1201)]
    watchlist = {"entities": [{"symbol": r[2]} for r in rows]}
    issuers, errors = facts.resolve(watchlist, {"fields": ["cik", "name", "ticker", "exchange"], "data": rows})
    assert len(issuers) == 1200 and errors == []


def test_job_pins_code_but_records_dynamic_watchlist_content(tmp_path, monkeypatch):
    for name in refresh.JOB_FILES:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / name).read_bytes())
    watchlist = tmp_path / "config/watchlist.json"
    atomic_json(watchlist, {"entities": [{"cik": "1001"}]})
    monkeypatch.setattr(refresh, "ROOT", tmp_path)
    job = refresh.prepare_job(watchlist)
    assert refresh.check_job(job)["watchlist_content_is_dynamic"] is True
    atomic_json(watchlist, {"entities": [{"cik": "1002"}]}, replace=True)
    assert refresh.check_job(job)
    path = tmp_path / "config/sec_fact_policy.json"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="SEC_JOB_SOURCE_CHANGED"): refresh.check_job(job)


def test_korean_report_explains_scope_and_no_profit_claim(case):
    result = run(case)
    text = Path(result["report_path"]).read_text(encoding="utf-8")
    assert "CIK(SEC 기업 식별번호)" in text and "주가 예측 정확도" in text and demo.LABEL in text
    assert "점수·목표가·매매 신호는 만들지 않습니다" in text


def test_multi_issuer_amendment_demo_matches_golden_contract(tmp_path):
    result = demo.run(tmp_path)
    result.pop("report_path")
    assert result == refresh.read(ROOT / "examples/sec_refresh_golden.json")


def test_changed_fact_and_reversion_have_three_separate_admission_times(case):
    run(case)
    first = refresh.latest(case["store"], "1001")
    row = case["routes"]["https://data.sec.gov/api/xbrl/companyfacts/CIK0000001001.json"]["facts"]["us-gaap"]["Revenues"]["units"]["USD"][1]
    row["val"] = 2500
    case["clock"].advance(86401)
    run(case)
    second = refresh.latest(case["store"], "1001")
    row["val"] = 2001
    case["clock"].advance(86401)
    run(case)
    third = refresh.latest(case["store"], "1001")
    assert first["available_at"] < second["available_at"] < third["available_at"]
    assert third["sequence"] == 3
    assert refresh.as_of(case["store"], "1001", second["available_at"])["snapshot_id"] == second["snapshot_id"]
    assert first["financials"] == third["financials"]


def test_raw_changed_after_snapshot_is_detected_by_asof(case):
    run(case)
    snap = refresh.latest(case["store"], "1001")
    item = next(iter(snap["sources"].values()))
    Path(item["raw_path"]).write_bytes(b"modified source")
    with pytest.raises(ValueError, match="SEC_SNAPSHOT_RAW_INTEGRITY_CHANGED"):
        refresh.latest(case["store"], "1001")


@pytest.mark.parametrize("stage", ["receipt", "latest_pointer"])
def test_crash_between_snapshot_receipt_and_pointer_recovers_without_rewrite(case, monkeypatch, stage):
    original, interrupted = refresh.atomic_json, False
    def fail_once(path, value, **kwargs):
        nonlocal interrupted
        path = Path(path)
        target = path.parent.name == "receipts" if stage == "receipt" else path.name == "latest.json"
        if target and not interrupted:
            interrupted = True
            raise OSError("ILLUSTRATIVE interrupted commit")
        return original(path, value, **kwargs)
    monkeypatch.setattr(refresh, "atomic_json", fail_once)
    with pytest.raises(OSError): run(case)
    original_path = next((case["store"] / "entities/0000001001/snapshots").glob("*.json"))
    original_sha = digest(original_path)
    case["clock"].advance(2)
    result = run(case)
    assert result["status"] == "completed_batch"
    assert digest(original_path) == original_sha
    assert refresh.latest(case["store"], "1001")["sequence"] == 1


def test_clock_reversal_cannot_backdate_new_identity_information(case):
    run(case)
    previous = refresh.latest(case["store"], "1001")
    case["clock"].advance(-100)
    case["watchlist"]["entities"] = [{"cik": "1001"}]
    result = run(case)
    assert result["entities"][0]["status"] == "unavailable"
    assert refresh.latest(case["store"], "1001")["snapshot_id"] == previous["snapshot_id"]


def test_invocation_request_budget_keeps_backlog_and_resumes(case, monkeypatch):
    settings = transport.policy()
    settings["maximum_requests_per_invocation"] = 5
    monkeypatch.setattr(refresh, "policy", lambda: settings)
    first = run(case)
    assert first["status"] == "deferred" and first["network_requests"] == 5
    state = refresh.read(case["store"] / "entities/0000001001/state.json")
    assert state["pending_documents"]
    for _ in range(5):
        case["clock"].advance(901)
        run(case)
    assert refresh.latest(case["store"], "1003") is not None


def test_existing_cached_material_can_be_read_without_user_agent(case):
    run(case)
    case["client"].agent = ""
    result = run(case)
    assert result["status"] == "completed_batch" and result["network_requests"] == 0


def test_companyfacts_cik_mismatch_is_not_other_company_financials(case):
    case["routes"]["https://data.sec.gov/api/xbrl/companyfacts/CIK0000001001.json"]["cik"] = 1002
    result = run(case)
    assert result["entities"][0]["status"] == "unavailable"
    assert refresh.latest(case["store"], "1001") is None


def test_changed_existing_filing_metadata_is_revision_not_new_arrival(case):
    run(case)
    columns = case["routes"]["https://data.sec.gov/submissions/CIK0000001001.json"]["filings"]["recent"]
    columns["primaryDocument"][1] = "replaced.htm"
    accession = columns["accessionNumber"][1]
    case["routes"][f"https://www.sec.gov/Archives/edgar/data/1001/{accession.replace('-', '')}/replaced.htm"] = b"ILLUSTRATIVE replacement"
    case["clock"].advance(3601)
    result = run(case)
    row = result["entities"][0]
    assert row["changed_known_filing_metadata"] == 1 and row["newly_observed_arrivals"] == []
    current = refresh.latest(case["store"], "1001")
    assert any(s["source_url"].endswith("replaced.htm") for s in current["sources"].values())


def test_cohort_import_copies_ids_only_and_preserves_source_and_partitions(tmp_path, monkeypatch):
    cohort = tmp_path / "cohort.json"
    value = {"selected": [{"cik": "1001", "symbol": "DEMOA", "symbol_partition": "unseen_symbol_final_only"},
        {"cik": "1001", "symbol": "DEMOA.A", "symbol_partition": "unseen_symbol_final_only"},
        {"cik": "1002", "symbol": "DEMOB", "symbol_partition": "development_and_validation"}]}
    atomic_json(cohort, value)
    before = digest(cohort)
    monkeypatch.setattr(refresh, "ROOT", tmp_path)
    output = tmp_path / "config/imported.json"
    result = refresh.import_cohort(cohort, output)
    assert result["unique_issuers"] == 2 and digest(cohort) == before
    assert all(set(r) == {"cik", "label"} for r in refresh.read(output)["entities"])
    with pytest.raises(FileExistsError): refresh.import_cohort(cohort, output)


def test_no_watchlist_over_1500_or_empty_can_start_network(case):
    for entities in ([], [{"cik": "1001"}] * 1501):
        case["watchlist"] = {"entities": entities}
        with pytest.raises(ValueError, match="SEC_WATCHLIST_SIZE_OUT_OF_BOUNDS"): run(case)
    assert case["client"].network_requests == 0


def test_context_explains_missing_base_form_and_escapes_untrusted_company_name(case):
    from research.sec_refresh_report import render_context
    run(case)
    value = refresh.latest(case["store"], "1001")
    value["name"] = '<script>x</script>|[click](https://evil.test)'
    value["coverage"]["missing_base_forms"] = ["10-K"]
    text = render_context(value)
    assert '<script>' not in text and '[click]' not in text
    assert "표준 수치 추출 · 원문 대조 전" in text
    assert "확인하지 못한 기본 공시: 10-K" in text
