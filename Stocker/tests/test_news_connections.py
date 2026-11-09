"""ILLUSTRATIVE — NOT LIVE MARKET DATA. Local metadata and reviewer claims only."""

from copy import deepcopy
import json
from pathlib import Path

import jsonschema
import pytest

from research import news_archive, news_connections as links, news_review_store as reviews
from research import news_connection_demo as demo
from research.local_files import atomic_json
from research.rights import RightsError
from research.sec_report import ROOT
from research.validation import digest

AFTER = "2026-11-08T11:15:30Z"


@pytest.fixture
def case(tmp_path, monkeypatch):
    names = set(links.CORE + reviews.LEGACY_FILES + ["config/news_archive_policy.json", "schemas/news_archive.schema.json"])
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / name).read_bytes())
    for module in (news_archive, links, reviews): monkeypatch.setattr(module, "ROOT", tmp_path)
    folder = tmp_path / "reports/private/fixture"
    ctx_path = folder / "context.json"
    ctx = demo.context()
    atomic_json(ctx_path, ctx)
    archive, root = folder / "archive", folder / "connections"
    news_archive.capture(ctx_path, root=archive, clock=lambda: demo.CAPTURE)
    return {"folder": folder, "context": ctx, "context_path": ctx_path, "archive": archive, "root": root, "counter": 0}


def submit(case, manifest=None, at=demo.REVIEW):
    case["counter"] += 1
    path = case["folder"] / f"review-{case['counter']}.json"
    atomic_json(path, manifest or demo.manifest(case["context"]))
    return reviews.store(case["context_path"], path, root=case["root"], archive_root=case["archive"], illustrative=True, clock=lambda: at)


def view(case, cutoff=AFTER):
    return links.snapshot(cutoff, root=case["root"], archive_root=case["archive"], illustrative=True, clock=lambda: cutoff)


def recapture(case, source_at, captured_at, title=None, add_article=False):
    ctx = deepcopy(case["context"])
    ctx["as_of"] = source_at
    for article in ctx["news"]["articles"]: article["retrieved_at"] = source_at
    ctx["news"]["providers"][0]["checked_at"] = source_at
    if title is not None: ctx["news"]["articles"][0]["title"] = title
    if add_article:
        article = deepcopy(ctx["news"]["articles"][0])
        article.update(id="third", source_ids=["third"], url="https://example.com/illustrative-third", title="ILLUSTRATIVE THIRD")
        ctx["news"]["articles"].append(article)
    case["counter"] += 1
    path = case["folder"] / f"context-{case['counter']}.json"
    atomic_json(path, ctx)
    news_archive.capture(path, root=case["archive"], clock=lambda: captured_at)
    return ctx


def test_pending_articles_are_not_neutral_or_automatically_scored(case):
    value = view(case)
    assert value["article_status_counts"] == {"awaiting_review": 2}
    assert value["events"] == value["highlights"] == []
    assert value["experimental_news_score"] is value["released_score"] is None
    assert value["coverage"][1]["discovery_status"] == "not_verified"


def test_review_storage_is_idempotent_and_never_resets_availability(case):
    source_sha = digest(case["context_path"])
    first = submit(case)
    second = submit(case, at="2026-11-08T11:16:00Z")
    assert first["receipt_id"] == second["receipt_id"] and second["status"] == "already_stored"
    assert second["available_at"] == first["available_at"] == demo.REVIEW
    assert len(list((case["root"] / "illustrative/reviews").glob("*.json"))) == 1
    assert digest(case["context_path"]) == source_sha


def test_positive_and_negative_events_are_both_highlighted_without_news_total(case):
    submit(case)
    result = view(case)
    assert len(result["highlights"]) == 2
    assert {event["opinions"][0]["direction"] for event in result["events"]} == {-1, 1}
    assert result["article_status_counts"] == {"review_linked": 2}
    assert result["experimental_news_score"] is None


def test_backdated_review_claim_is_unavailable_before_actual_commit(case):
    submit(case, at="2026-11-08T11:15:40Z")
    assert view(case, "2026-11-08T11:15:25Z")["events"] == []
    assert len(view(case, "2026-11-08T11:15:41Z")["highlights"]) == 2


def test_receipt_time_is_sampled_again_immediately_before_commit(case):
    path = case["folder"] / "slow-review.json"
    atomic_json(path, demo.manifest(case["context"]))
    clock = iter([demo.REVIEW, "2026-11-08T11:15:50Z"])
    result = reviews.store(case["context_path"], path, root=case["root"], archive_root=case["archive"], illustrative=True, clock=lambda: next(clock))
    assert result["available_at"] == "2026-11-08T11:15:50Z"
    assert view(case, "2026-11-08T11:15:40Z")["events"] == []


def test_same_second_review_order_is_conservatively_withheld(case):
    submit(case)
    value = view(case, "2026-11-08T11:15:20.999999Z")
    assert value["events"] == [] and value["equal_timestamp_receipts_withheld"] == 1


@pytest.mark.parametrize("problem", ["draft", "wrong_packet", "unverified_publication", "wrong_url", "future_review", "before_observation", "empty_counterargument", "boolean_direction", "unverified_update"])
def test_invalid_review_is_not_committed(case, problem):
    manifest = demo.manifest(case["context"])
    event = manifest["events"][0]
    if problem == "draft": manifest["template_only"] = True
    if problem == "wrong_packet": manifest["news_context_sha256"] = "0" * 64
    if problem == "unverified_publication": event["original_checks"][0]["publication_verified"] = False
    if problem == "wrong_url": event["original_checks"][0]["url"] = "https://example.com/other"
    if problem == "future_review": event["reviewed_at"] = "2026-11-08T11:20:00Z"
    if problem == "before_observation": event["original_checks"][0]["checked_at"] = "2026-11-08T11:14:00Z"
    if problem == "empty_counterargument": del event["counterargument"]
    if problem == "boolean_direction": event["direction"] = True
    if problem == "unverified_update": event["novelty"] = "material_update"
    with pytest.raises((ValueError, jsonschema.ValidationError)): submit(case, manifest)
    assert not list((case["root"] / "illustrative/reviews").glob("*.json"))


def test_review_import_does_not_create_archive_data_or_revert_newer_metadata(case):
    path = case["folder"] / "review.json"
    atomic_json(path, demo.manifest(case["context"]))
    with pytest.raises(ValueError, match="ARCHIVE_CONTEXT"):
        reviews.store(case["context_path"], path, root=case["root"], archive_root=case["folder"] / "empty-archive", illustrative=True, clock=lambda: demo.REVIEW)
    recapture(case, "2026-11-08T11:16:00Z", "2026-11-08T11:16:02Z", title="ILLUSTRATIVE CHANGED")
    before = len(list((case["archive"] / "illustrative/captures").glob("*.json")))
    with pytest.raises(ValueError, match="VERSION_IS_NOT_CURRENT"):
        submit(case, at="2026-11-08T11:16:05Z")
    assert len(list((case["archive"] / "illustrative/captures").glob("*.json"))) == before


def test_unchanged_metadata_refresh_reuses_review_without_rebinding_manifest(case):
    receipt = submit(case)
    original = Path(receipt["path"]).read_bytes()
    recapture(case, "2026-11-08T11:16:00Z", "2026-11-08T11:16:02Z", add_article=True)
    value = view(case, "2026-11-08T11:16:10Z")
    assert value["article_status_counts"] == {"review_linked": 2, "awaiting_review": 1}
    assert len(value["highlights"]) == 2 and value["experimental_news_score"] is None
    assert Path(receipt["path"]).read_bytes() == original


def test_changed_and_reverted_metadata_require_a_new_review(case):
    submit(case)
    recapture(case, "2026-11-08T11:16:00Z", "2026-11-08T11:16:02Z", title="ILLUSTRATIVE CHANGED")
    changed = view(case, "2026-11-08T11:16:10Z")
    assert changed["events"][0]["status"] == "review_needs_update"
    assert "ARTICLE_VERSION_CHANGED" in changed["events"][0]["reason_codes"]
    recapture(case, "2026-11-08T11:17:00Z", "2026-11-08T11:17:02Z")
    reverted = view(case, demo.LATER)
    assert "ARTICLE_REVERTED_REVIEW_REQUIRED" in reverted["events"][0]["reason_codes"]
    assert len(reverted["highlights"]) == 1


def test_reimporting_old_assertions_cannot_bypass_reversion_recheck(case):
    submit(case)
    recapture(case, "2026-11-08T11:16:00Z", "2026-11-08T11:16:02Z", title="ILLUSTRATIVE CHANGED")
    recapture(case, "2026-11-08T11:17:00Z", "2026-11-08T11:17:02Z")
    with pytest.raises(ValueError, match="CHECK_PRECEDES_CURRENT_VERSION"):
        submit(case, at=demo.LATER)
    assert len(list((case["root"] / "illustrative/reviews").glob("*.json"))) == 1


def test_all_cited_article_versions_must_match_for_a_multi_source_event(case):
    manifest = demo.manifest(case["context"])
    first, second = manifest["events"]
    first["source_ids"] += second["source_ids"]
    first["original_urls"] += second["original_urls"]
    first["original_checks"] += second["original_checks"]
    manifest["events"] = [first]
    submit(case, manifest)
    assert len(view(case)["highlights"]) == 1
    recapture(case, "2026-11-08T11:16:00Z", "2026-11-08T11:16:02Z", title="ILLUSTRATIVE CHANGED")
    result = view(case, "2026-11-08T11:16:10Z")
    assert result["highlights"] == []
    assert result["article_status_counts"] == {"review_needs_update": 2}


def test_disagreeing_reviewers_are_preserved_but_not_highlighted(case):
    submit(case)
    submit(case, demo.manifest(case["context"], reviewer="another_reviewer", direction=-1), at="2026-11-08T11:15:22Z")
    result = view(case)
    assert result["article_status_counts"] == {"conflicting_reviews": 2}
    assert result["highlights"] == []
    assert all(len(event["opinions"]) == 2 for event in result["events"])


def test_latest_same_reviewer_assertion_only_applies_after_its_commit(case):
    submit(case)
    submit(case, demo.manifest(case["context"], direction=-1), at="2026-11-08T11:15:22Z")
    before, after = view(case, "2026-11-08T11:15:21Z"), view(case)
    assert before["events"][0]["opinions"][0]["direction"] == 1
    assert after["events"][0]["opinions"][0]["direction"] == -1
    assert len(after["events"][0]["opinions"]) == 1


def test_metadata_refresh_does_not_renew_expired_original_checks(case):
    submit(case)
    recapture(case, "2026-11-09T11:16:00Z", "2026-11-09T11:16:02Z")
    result = view(case, "2026-11-09T11:16:10Z")
    assert all("REVIEW_EXPIRED" in event["reason_codes"] for event in result["events"])
    assert result["highlights"] == []


def test_stale_metadata_or_recycled_event_is_not_a_fresh_highlight(case):
    manifest = demo.manifest(case["context"])
    manifest["events"][0]["novelty"] = "recycled"
    submit(case, manifest)
    assert len(view(case)["highlights"]) == 1
    stale = view(case, "2026-11-08T11:46:00Z")
    assert stale["highlights"] == [] and all(article["metadata_fresh_at_cutoff"] is False for article in stale["articles"])


def test_future_corrupted_review_does_not_change_a_valid_past_view(case):
    submit(case)
    future = submit(case, demo.manifest(case["context"], reviewer="later"), at="2026-11-08T11:16:00Z")
    value = json.loads(Path(future["path"]).read_text(encoding="utf-8"))
    value["manifest"]["events"][0]["rationale"] = "ILLUSTRATIVE TAMPERED"
    Path(future["path"]).write_text(json.dumps(value), encoding="utf-8")
    assert len(view(case)["highlights"]) == 2
    with pytest.raises(ValueError, match="INTEGRITY"):
        view(case, "2026-11-08T11:16:10Z")


def test_forecast_link_is_immutable_after_later_reviews_and_same_second_captures(case):
    forecast_path = demo.forecast(case["folder"] / "early", case["context_path"])
    original_sha = digest(forecast_path)
    before, binding = links.bind_forecast(forecast_path, root=case["root"], archive_root=case["archive"], illustrative=True, clock=lambda: AFTER)
    submit(case, at=demo.DECISION)  # Same second has no cross-store ordering proof.
    recapture(case, demo.DECISION, demo.DECISION, title="ILLUSTRATIVE LATER SAME SECOND")
    after, same_binding = links.bind_forecast(forecast_path, root=case["root"], archive_root=case["archive"], illustrative=True, clock=lambda: demo.LATER)
    assert before == after and binding == same_binding and digest(forecast_path) == original_sha
    assert after["events"] == [] and after["news_used_in_original_price_decision"] is False


def test_review_and_all_sources_must_precede_and_belong_to_frozen_context(case):
    submit(case)
    forecast_path = demo.forecast(case["folder"] / "prior", case["context_path"], cutoff="2026-11-08T11:15:18Z")
    value, _ = links.bind_forecast(forecast_path, root=case["root"], archive_root=case["archive"], illustrative=True, clock=lambda: AFTER)
    assert value["events"] == [] and value["article_status_counts"] == {"awaiting_review": 2}
    frozen = {**case["context"]["news"], "articles": case["context"]["news"]["articles"][:1]}
    result = links.snapshot(AFTER, root=case["root"], archive_root=case["archive"], illustrative=True, clock=lambda: AFTER, frozen_news=frozen)
    assert len(result["articles"]) == 1 and len(result["highlights"]) == 1


def test_frozen_source_revision_or_path_traversal_cannot_be_bound(case):
    path = demo.forecast(case["folder"] / "frozen", case["context_path"])
    source = deepcopy(case["context"])
    source["news"]["articles"][0]["title"] = "ILLUSTRATIVE TAMPERED"
    case["context_path"].write_text(json.dumps(source), encoding="utf-8")
    with pytest.raises(ValueError, match="SOURCE_CHANGED"):
        links.bind_forecast(path, root=case["root"], archive_root=case["archive"], illustrative=True, clock=lambda: AFTER)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["record_id"] = "../../outside"
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="RECORD_ID_INVALID"):
        links.forecast_summary(path, illustrative=True)


def test_schema_and_console_do_not_promote_scores_or_leak_input(case, monkeypatch, capsys):
    result = view(case)
    reviews.validate(result, "snapshot")
    for key, invalid in (("released_score", 50), ("experimental_news_score", 20), ("calibrated_probability", .9), ("publisher_body_fetched", True), ("original_forecast_modified", True), ("model_training_performed", True)):
        with pytest.raises(jsonschema.ValidationError): reviews.validate({**result, key: invalid}, "snapshot")
    def invalid(*args, **kwargs): raise jsonschema.ValidationError("PRIVATE_ARTICLE_DO_NOT_PRINT")
    monkeypatch.setattr(links, "snapshot", invalid)
    assert links.main(["--illustrative"]) == 1
    output = capsys.readouterr()
    assert "PRIVATE_ARTICLE_DO_NOT_PRINT" not in output.out + output.err


def test_receipts_remain_available_after_original_input_files_change(case):
    result = submit(case)
    assert Path(result["path"]).exists()
    case["context_path"].write_text("changed local source", encoding="utf-8")
    assert len(view(case)["highlights"]) == 2  # Immutable snapshot is inside the receipt.


def test_one_invalid_opinion_does_not_approve_its_article_through_another_source(case):
    first = demo.manifest(case["context"])
    first["events"] = first["events"][:1]
    submit(case, first)
    second = demo.manifest(case["context"], reviewer="different_source_reviewer")
    second["events"] = second["events"][1:]
    second["events"][0].update(event_id="illustrative_event_1", direction=1)
    submit(case, second, at="2026-11-08T11:15:22Z")
    recapture(case, "2026-11-08T11:16:00Z", "2026-11-08T11:16:02Z", title="ILLUSTRATIVE CHANGED")
    result = view(case, "2026-11-08T11:16:10Z")
    assert result["articles"][0]["status"] == "review_needs_update" and result["articles"][0]["highlight"] is False
    assert result["articles"][1]["status"] == "review_linked" and result["articles"][1]["highlight"] is True


def test_partial_frozen_context_cannot_accept_a_multi_article_event(case):
    manifest = demo.manifest(case["context"])
    first, second = manifest["events"]
    for key in ("source_ids", "original_urls", "original_checks"): first[key] += second[key]
    manifest["events"] = [first]
    submit(case, manifest)
    frozen = {**case["context"]["news"], "articles": case["context"]["news"]["articles"][:1]}
    result = links.snapshot(AFTER, root=case["root"], archive_root=case["archive"], illustrative=True, clock=lambda: AFTER, frozen_news=frozen)
    assert result["highlights"] == [] and result["articles"][0]["status"] == "review_needs_update"
    assert "ARTICLE_NOT_IN_FROZEN_CONTEXT_VERSION" in result["events"][0]["reason_codes"]


def test_real_forecast_rights_are_checked_before_price_derived_record_parsing(case, monkeypatch):
    entitlement = case["folder"] / "entitlement.json"
    atomic_json(entitlement, {"is_illustrative": False, "monthly_cost_usd": 1})
    monkeypatch.setattr(links, "latest_training_snapshot", lambda: (case["folder"] / "unread-prices.json", entitlement))
    original = reviews.read
    def only_license(path):
        assert Path(path) == entitlement
        return original(path)
    monkeypatch.setattr(reviews, "read", only_license)
    with pytest.raises(RightsError): links.forecast_summary(case["folder"] / "never-parse-forecast.json")


def test_missing_legacy_cutoff_does_not_fall_back_to_recording_time(case):
    path = demo.forecast(case["folder"] / "missing-cutoff", case["context_path"])
    record = json.loads(path.read_text(encoding="utf-8"))
    del record["as_of"]
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="PINNED_ORIGINAL_PROOF"):
        links.forecast_summary(path, illustrative=True)


@pytest.mark.parametrize("problem", [None, "screen_after_context", "explicit_synthetic_context"])
def test_legacy_adapter_uses_only_hash_bound_declared_context_cutoff(case, monkeypatch, problem):
    # Operational old-format fixture, not a real price record. No prices occur.
    folder = case["folder"] / "legacy-format"
    context = {"as_of": demo.SOURCE, "news": case["context"]["news"]}
    if problem == "explicit_synthetic_context": context["is_illustrative"] = True
    context_path, base_path, forecast_path = folder / "context.json", folder / "base.json", folder / "forecast.json"
    atomic_json(context_path, context)
    atomic_json(base_path, {"symbol": "BE", "is_illustrative": False, "fixture_label": "ILLUSTRATIVE — NOT LIVE MARKET DATA"})
    record = {"record_id": "a" * 24, "status": "prospective_context_record", "symbol": "BE", "is_illustrative": False,
        "recorded_at": demo.DECISION, "entry_at": "2026-11-09T14:30:00Z", "sessions": list(range(5)),
        "decision_scope": "price_cost_screen_only", "context_path": str(context_path), "context_sha256": digest(context_path),
        "base_forecast_path": str(base_path), "base_forecast_sha256": digest(base_path),
        "screen": {"as_of": "2026-11-08T11:15:01Z" if problem == "screen_after_context" else demo.SOURCE}}
    atomic_json(forecast_path, record)
    entitlement = folder / "fixture-license.json"
    atomic_json(entitlement, {"id": "operational-fixture", "provider": "massive", "reviewer": "ILLUSTRATIVE — NOT LIVE MARKET DATA",
        "reviewed_at": "2026-11-07T00:00:00Z", "review_due_at": "2027-01-01T00:00:00Z", "evidence_url": "https://example.invalid/fixture",
        "plan": "fixture", "monthly_cost_usd": 0, "is_illustrative": False, "uses": {key: "allowed" for key in ("ingest", "store", "derived_analytics")}})
    monkeypatch.setattr(links, "latest_training_snapshot", lambda: (folder / "no-price-input", entitlement))
    monkeypatch.setattr(links, "legacy_forecast_proof", lambda path, record: {"fixture_pinned_proof": True})
    if problem:
        with pytest.raises(ValueError): links.forecast_summary(forecast_path)
    else:
        result = links.forecast_summary(forecast_path)
        assert result["as_of"] == demo.SOURCE and result["as_of"] != record["recorded_at"]
        assert result["cutoff_source"] == "pinned_legacy_context_as_of"
