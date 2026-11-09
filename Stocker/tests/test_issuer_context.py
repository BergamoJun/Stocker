"""ILLUSTRATIVE — NOT LIVE MARKET DATA. Issuer-bound reviews and report versions."""

from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path

import jsonschema
import pytest

from research import issuer_context_demo as demo, issuer_profiles as profiles, issuer_news as news, expanded_report as report
from research.local_files import atomic_json
from research.rights import as_utc
from research.sec_report import ROOT
from research.validation import digest


@pytest.fixture
def case(tmp_path, monkeypatch):
    return demo.test_setup(tmp_path, monkeypatch)


def ready(case):
    result, _ = demo.refresh(case)
    case["clock"].advance(2)
    return result


def view(case, issuer="1001", cutoff=None):
    return news.snapshot(issuer, cutoff or case["clock"](), root=case["news_root"], profile_root=case["profile_root"], illustrative=True, clock=case["clock"])


def test_profiles_require_first_commit_and_exclude_equal_second(case):
    assert profiles.as_of("1001", case["clock"](), root=case["profile_root"], illustrative=True) is None
    receipt = profiles.capture(registry_path=case["registry_path"], root=case["profile_root"], source_store=case["source_store"], illustrative=True, clock=case["clock"])
    equal = as_utc(receipt["available_at"]).replace(microsecond=999999).isoformat()
    assert profiles.as_of("1001", equal, root=case["profile_root"], illustrative=True) is None
    case["clock"].advance(2)
    first = profiles.as_of("1001", case["clock"](), root=case["profile_root"], illustrative=True)
    second = profiles.as_of("1003", case["clock"](), root=case["profile_root"], illustrative=True)
    assert first["symbol"] == "BE" and second["symbol"] == "DEMOC"
    assert first["binding"] != second["binding"]


def test_wrong_sec_identity_cannot_be_approved_by_a_profile(case):
    value = json.loads(case["registry_path"].read_text(encoding="utf-8"))
    value["profiles"][0]["cik"] = "0000001999"
    path = case["folder"] / "wrong_registry.json"
    atomic_json(path, value)
    with pytest.raises(ValueError, match="SEC_IDENTITY_MISMATCH"):
        profiles.capture(registry_path=path, root=case["profile_root"], source_store=case["source_store"], illustrative=True, clock=case["clock"])


def test_profile_deduplication_preserves_availability(case):
    ready(case)
    first = profiles.receipts(root=case["profile_root"], illustrative=True)[0]
    result = profiles.capture(registry_path=case["registry_path"], root=case["profile_root"], source_store=case["source_store"], illustrative=True, clock=case["clock"])
    assert result["status"] == "unchanged" and result["available_at"] == first["available_at"]


def test_pending_news_is_issuer_specific_and_unscored(case):
    ready(case)
    for issuer, symbol in (("1001", "BE"), ("1003", "DEMOC")):
        value = view(case, issuer)["snapshot"]
        assert value["symbol"] == symbol and len(value["articles"]) == 4
        assert value["highlights"] == [] and value["experimental_news_score"] is None
        assert value["article_status_counts"] == {"awaiting_review": 4}


def test_archive_first_commit_is_not_replaced_by_source_download_time(case):
    result = ready(case)
    at = result["issuers"][0]["capture"]["available_at"]
    equal = as_utc(at).replace(microsecond=999999).isoformat()
    assert view(case, cutoff=equal)["snapshot"]["articles"] == []
    assert len(view(case)["snapshot"]["articles"]) == 4


def test_draft_and_unspecified_reviewer_kind_cannot_be_imported(case):
    result = ready(case)
    row = result["issuers"][0]
    with pytest.raises(ValueError, match="DRAFT_OR_EMPTY"):
        news.import_review(row["context_path"], row["review_template_path"], root=case["news_root"], profile_root=case["profile_root"], illustrative=True, clock=case["clock"])


def test_review_and_article_cannot_be_relabelled_as_another_issuer(case):
    result = ready(case)
    be, other = result["issuers"]
    path, _ = demo.submit(case, be)
    with pytest.raises(ValueError, match="ANOTHER_ISSUER"):
        news.import_review(other["context_path"], path, root=case["news_root"], profile_root=case["profile_root"], illustrative=True, clock=case["clock"])
    context = json.loads(Path(be["context_path"]).read_text(encoding="utf-8"))
    selected = profiles.as_of("1003", case["clock"](), root=case["profile_root"], illustrative=True)
    context.update(cik="0000001003", symbol="DEMOC", profile_binding=selected["binding"])
    fake = case["folder"] / "relabelled_context.json"
    atomic_json(fake, context)
    with pytest.raises(ValueError, match="PACKET_FOR_ANOTHER_ISSUER"):
        news.capture(fake, root=case["news_root"], profile_root=case["profile_root"], illustrative=True, clock=case["clock"])


def test_positive_and_negative_reviews_link_only_to_their_issuer(case):
    result = ready(case)
    for row in result["issuers"]:
        demo.submit(case, row, direction=1 if row["symbol"] == "BE" else -1)
    for issuer, direction in (("1001", 1), ("1003", -1)):
        value = view(case, issuer)["snapshot"]
        assert len(value["highlights"]) == 1
        assert value["events"][0]["opinions"][0]["direction"] == direction
        assert value["released_score"] is None


def test_repeat_review_import_preserves_actual_first_receipt_time(case):
    result = ready(case)
    row = result["issuers"][0]
    path, first = demo.submit(case, row)
    repeated = news.import_review(row["context_path"], path, root=case["news_root"], profile_root=case["profile_root"], illustrative=True, clock=case["clock"])
    assert repeated["status"] == "unchanged" and repeated["available_at"] == first["available_at"]
    assert len(news.receipts("1001", root=case["news_root"], illustrative=True)) == 1


def test_review_check_must_follow_first_archive_commit(case):
    result = ready(case)
    row = result["issuers"][0]
    path, _ = demo.submit(case, row)
    value = json.loads(path.read_text(encoding="utf-8"))
    context = json.loads(Path(row["context_path"]).read_text(encoding="utf-8"))
    observed = next(a for a in context["news"]["articles"] if "company" in a["categories"])["retrieved_at"]
    value["manifest"]["events"][0]["original_checks"][0]["checked_at"] = (as_utc(observed) + timedelta(seconds=1)).isoformat()
    bad = case["folder"] / "too_early_check.json"
    atomic_json(bad, value)
    with pytest.raises(ValueError, match="CURRENT_VERSION_RECHECK_REQUIRED"):
        news.import_review(row["context_path"], bad, root=case["news_root"], profile_root=case["profile_root"], illustrative=True, clock=case["clock"])


def test_same_second_review_receipt_is_not_available(case):
    result = ready(case)
    _, receipt = demo.submit(case, result["issuers"][0])
    cutoff = as_utc(receipt["available_at"]).replace(microsecond=999999).isoformat()
    assert view(case, cutoff=cutoff)["snapshot"]["events"] == []


def test_conflicting_reviewers_suppress_highlights_and_keep_original_report(case):
    result = ready(case)
    demo.submit(case, result["issuers"][0])
    original = demo.build(case)
    demo.submit(case, result["issuers"][0], direction=-1, reviewer="ILLUSTRATIVE_CONTRARY_REVIEWER")
    current = view(case)["snapshot"]
    assert current["highlights"] == [] and current["events"][0]["status"] == "conflicting_reviews"
    assert report.validate(original) == original


def test_source_change_and_reversion_cannot_revive_older_reviews(case):
    result = ready(case)
    row = result["issuers"][0]
    demo.submit(case, row)
    original = demo.build(case)
    context = json.loads(Path(row["context_path"]).read_text(encoding="utf-8"))
    title = context["news"]["articles"][0]["title"]
    for index, replacement in enumerate((title + " changed", title)):
        case["clock"].advance(5)
        changed = deepcopy(context)
        changed["as_of"] = case["clock"]()
        changed["news"]["articles"][0]["title"] = replacement
        for a in changed["news"]["articles"]:
            a["retrieved_at"] = case["clock"]()
        path = case["folder"] / f"changed-{index}.json"
        atomic_json(path, changed)
        news.capture(path, root=case["news_root"], profile_root=case["profile_root"], illustrative=True, clock=case["clock"])
        case["clock"].advance(2)
        assert view(case)["snapshot"]["highlights"] == []
    assert report.validate(original) == original


def test_stale_metadata_and_per_topic_queries_do_not_look_current(case):
    result = ready(case)
    demo.submit(case, result["issuers"][0])
    case["clock"].advance(1900)
    value = view(case)["snapshot"]
    assert not value["coverage_current_at_cutoff"] and value["highlights"] == []
    assert all(t["discovery_status"] == "not_verified" for t in value["coverage"])


def test_invalid_partial_queries_without_articles_do_not_clear_fresh_coverage(case, monkeypatch):
    def invalid(*args, **kwargs):
        return demo.Response(b'{"status":"OK","results":[{}],"articles":[{}]}')
    monkeypatch.setattr(case["opener"], "open", invalid)
    ready(case)
    value = view(case)["snapshot"]
    assert value["articles"] == []
    assert not value["coverage_current_at_cutoff"]


def test_profile_change_invalidates_old_news_and_reviews_without_rewriting_history(case):
    result = ready(case)
    demo.submit(case, result["issuers"][0])
    original = demo.build(case)
    case["clock"].advance(4)
    registry = json.loads(case["registry_path"].read_text(encoding="utf-8"))
    registry["profiles"][0]["sector_proxy"]["limitation_ko"] += " ILLUSTRATIVE re-review"
    path = case["folder"] / "new_registry.json"
    atomic_json(path, registry)
    profiles.capture(registry_path=path, root=case["profile_root"], source_store=case["source_store"], illustrative=True, clock=case["clock"])
    case["clock"].advance(2)
    value = view(case)["snapshot"]
    assert value["highlights"] == [] and not value["coverage_current_at_cutoff"]
    assert value["outdated_profile_or_method_reviews"] == 1
    assert report.validate(original) == original


def test_new_methods_cannot_reuse_old_reviews(case, monkeypatch):
    result = ready(case)
    demo.submit(case, result["issuers"][0])
    old = news.methods()
    monkeypatch.setattr(news, "methods", lambda: {**old, "research/issuer_news.py": "0" * 64})
    value = view(case)["snapshot"]
    assert value["highlights"] == [] and value["outdated_profile_or_method_reviews"] == 1


def test_foreign_issuer_has_news_but_no_inherited_be_price_or_sector_quote(case):
    ready(case)
    value = demo.build(case, "1003")
    assert value["profile"]["symbol"] == "DEMOC" and value["coverage"]["news_articles"] == 4
    assert not value["coverage"]["price_scenario_available"] and value["coverage"]["proxies_available"] == 0
    assert value["base_report"]["news"]["snapshot"] is None


def test_expanded_report_keeps_original_and_new_news_contracts_separate(case):
    ready(case)
    value = demo.build(case)
    assert value["base_report"]["schema_version"] == "integrated-research-report-v1"
    assert value["schema_version"] == "issuer-expanded-research-v1"
    assert value["coverage"]["news_articles"] == 4
    assert value["news"]["as_of"] == value["base_report"]["as_of"] == value["as_of"]
    assert report.validate(value) == value


@pytest.mark.parametrize("field", ["released_score", "released_price_plan", "released_expected_target_sessions"])
def test_new_wrapper_cannot_release_recommendation_fields(case, field):
    ready(case)
    value = demo.build(case)
    value[field] = 50
    with pytest.raises(jsonschema.ValidationError):
        report.validate(value)


def test_changed_news_coverage_links_or_profile_fail_report_replay(case):
    ready(case)
    original = demo.build(case)
    for field in ("coverage", "profile", "source_catalog"):
        value = deepcopy(original)
        if field == "coverage":
            value[field]["news_articles"] = 99
        elif field == "profile":
            value[field]["symbol"] = "OTHER"
        else:
            value[field][0]["url"] = "https://example.com/unrelated"
        with pytest.raises(ValueError, match="REPLAY_MISMATCH"):
            report.validate(value)


def test_bundle_replay_detects_tampering_and_refuses_overwriting(case):
    ready(case)
    value = demo.build(case)
    folder = report.write(value, case["folder"] / "out")
    assert report.validate_file(folder / "report.json") == value
    with pytest.raises(ValueError, match="ALREADY_EXISTS"):
        report.write(value, case["folder"] / "out")
    (folder / "NEWS.ko.md").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="BUNDLE_CHANGED"):
        report.validate_file(folder / "report.json")


def test_old_context_and_pinned_source_files_are_not_modified(case):
    before = {str(p): digest(p) for p in [case["anchor"], case["old"]["context_path"]]}
    ready(case)
    value = demo.build(case)
    assert {p: digest(Path(p)) for p in before} == before
    assert not value["provenance"]["original_forecasts_modified"]


def test_whole_project_conflict_blocks_report_generation(case):
    ready(case)
    case["anchor"].write_text("new incompatible original model", encoding="utf-8")
    with pytest.raises(ValueError, match="PROJECT_CONFLICT"):
        demo.build(case)


def test_replay_cli_has_counts_only(case, capsys):
    ready(case)
    folder = report.write(demo.build(case), case["folder"] / "cli")
    assert report.main(["--validate", str(folder / "report.json")]) == 0
    assert json.loads(capsys.readouterr().out) == {"expanded_report_replay_valid": True}


def test_golden_demo(tmp_path, monkeypatch):
    case = demo.test_setup(tmp_path, monkeypatch)
    # run() owns a separate fixture folder in the same isolated private root.
    result = demo.run(tmp_path / "reports/private/golden")
    expected = json.loads((ROOT / "examples/issuer_context_golden.json").read_text(encoding="utf-8"))
    assert result["golden"] == expected
