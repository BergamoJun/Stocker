"""ILLUSTRATIVE — NOT LIVE MARKET DATA. Honest metadata availability tests."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from research import news_archive
from research.sec_report import ROOT
from research.validation import save_new

SOURCE_AT = "2026-11-07T13:00:00Z"
FIRST = "2026-11-07T14:00:00Z"
SECOND = "2026-11-07T14:10:00Z"
LABEL = "ILLUSTRATIVE — NOT LIVE MARKET DATA"


@pytest.fixture
def case(tmp_path, monkeypatch):
    schema = tmp_path / "schemas/news_archive.schema.json"
    schema.parent.mkdir()
    schema.write_bytes((ROOT / "schemas/news_archive.schema.json").read_bytes())
    path = tmp_path / "config/news_archive_policy.json"
    path.parent.mkdir()
    path.write_bytes((ROOT / "config/news_archive_policy.json").read_bytes())
    monkeypatch.setattr(news_archive, "ROOT", tmp_path)
    context = {"is_illustrative": True, "data_label": LABEL, "as_of": SOURCE_AT,
        "news": {"articles": [{"id": "fixture", "source_ids": ["fixture"], "title": LABEL, "url": "https://example.com/story", "publisher": "Fixture",
            "retrieved_at": SOURCE_AT, "provider_published_at": "2026-11-07T12:00:00Z", "first_seen_at": None,
            "published_at": None, "impact_reviewed": False, "score_eligible": False, "providers": ["fixture"], "categories": ["company"]}],
            "topics": [{"id": "macro_politics", "discovery_status": "not_verified"}], "all_topics_discovered": False}}
    source = tmp_path / "examples/context.json"
    save_new(source, context)
    return {"source": source, "context": context, "root": tmp_path / "reports/private/news_archive"}


def persist(case, context):
    case["source"].write_text(json.dumps(context), encoding="utf-8")


def view(case, cutoff):
    return news_archive.as_of_view(cutoff, root=case["root"], illustrative=True)


def test_older_provider_or_download_time_cannot_backdate_first_availability(case):
    result = news_archive.capture(case["source"], root=case["root"], clock=lambda: FIRST)
    assert result["available_at"] == FIRST
    assert view(case, "2026-11-07T13:59:59Z")["articles"] == []
    after = view(case, FIRST)
    assert len(after["articles"]) == 1
    assert after["articles"][0]["version_first_available_at"] == FIRST
    assert after["articles"][0]["source_observed_at"] == SOURCE_AT
    assert after["coverage"][0]["discovery_status"] == "not_verified"
    assert after["coverage_current_at_query"] is False
    assert after["released_score"] is None and after["model_training_permission_granted"] is False


def test_duplicate_capture_does_not_create_versions_or_reset_time(case):
    first = news_archive.capture(case["source"], root=case["root"], clock=lambda: FIRST)
    again = news_archive.capture(case["source"], root=case["root"], clock=lambda: SECOND)
    assert again["status"] == "already_archived" and again["available_at"] == FIRST
    assert again["new_versions"] == 0 and again["capture_path"] == first["capture_path"]


def test_refreshed_observation_with_unchanged_article_reuses_immutable_version(case):
    news_archive.capture(case["source"], root=case["root"], clock=lambda: FIRST)
    context = deepcopy(case["context"])
    context["as_of"] = SECOND
    context["news"]["articles"][0]["retrieved_at"] = SECOND
    persist(case, context)
    second = news_archive.capture(case["source"], root=case["root"], clock=lambda: SECOND)
    assert second["status"] == "archived" and second["new_versions"] == 0
    result = view(case, SECOND)
    assert result["committed_captures"] == 2
    assert result["articles"][0]["version_first_available_at"] == FIRST
    assert result["articles"][0]["last_archive_observation_at"] == SECOND


def test_changed_metadata_is_only_visible_after_its_actual_archive_capture(case):
    news_archive.capture(case["source"], root=case["root"], clock=lambda: FIRST)
    changed = deepcopy(case["context"])
    changed["news"]["articles"][0]["title"] = "ILLUSTRATIVE UPDATED TITLE"
    persist(case, changed)
    second = news_archive.capture(case["source"], root=case["root"], clock=lambda: SECOND)
    assert second["new_versions"] == 1
    assert view(case, FIRST)["articles"][0]["metadata"]["title"] == LABEL
    assert view(case, SECOND)["articles"][0]["metadata"]["title"] == "ILLUSTRATIVE UPDATED TITLE"


def test_future_packet_corruption_does_not_change_a_past_valid_view(case):
    news_archive.capture(case["source"], root=case["root"], clock=lambda: FIRST)
    changed = deepcopy(case["context"])
    changed["news"]["articles"][0]["title"] = "ILLUSTRATIVE FUTURE"
    persist(case, changed)
    second = news_archive.capture(case["source"], root=case["root"], clock=lambda: SECOND)
    path = Path(second["capture_path"])
    data = json.loads(path.read_text(encoding="utf-8"))
    data["news_packet"]["articles"][0]["title"] = "tampered"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert view(case, FIRST)["articles"][0]["metadata"]["title"] == LABEL
    with pytest.raises(ValueError, match="integrity"):
        view(case, SECOND)


def test_same_second_capture_order_and_metadata_reversion_are_preserved(case):
    news_archive.capture(case["source"], root=case["root"], clock=lambda: FIRST)
    changed = deepcopy(case["context"])
    changed["news"]["articles"][0]["title"] = "ILLUSTRATIVE SECOND"
    persist(case, changed)
    news_archive.capture(case["source"], root=case["root"], clock=lambda: FIRST)
    assert view(case, FIRST)["articles"][0]["metadata"]["title"] == "ILLUSTRATIVE SECOND"
    reverted = deepcopy(case["context"])
    reverted["as_of"] = SECOND
    reverted["news"]["articles"][0]["retrieved_at"] = SECOND
    persist(case, reverted)
    result = news_archive.capture(case["source"], root=case["root"], clock=lambda: SECOND)
    assert result["new_versions"] == 0
    assert view(case, SECOND)["articles"][0]["metadata"]["title"] == LABEL


@pytest.mark.parametrize("problem", ["future_context", "future_observation", "publisher_body", "promoted_review", "missing_classification"])
def test_unsupported_input_is_rejected_without_committing_records(case, problem):
    context = deepcopy(case["context"])
    if problem == "future_context": context["as_of"] = "2026-11-08T00:00:00Z"
    elif problem == "future_observation": context["news"]["articles"][0]["retrieved_at"] = FIRST
    elif problem == "publisher_body": context["news"]["articles"][0]["body"] = "not allowed"
    elif problem == "promoted_review": context["news"]["articles"][0]["score_eligible"] = True
    else: del context["is_illustrative"]
    persist(case, context)
    with pytest.raises(ValueError):
        news_archive.capture(case["source"], root=case["root"], clock=lambda: FIRST)
    assert not list(case["root"].glob("*/captures/*.json"))


def test_real_and_illustrative_namespaces_do_not_mix(case):
    news_archive.capture(case["source"], root=case["root"], clock=lambda: FIRST)
    real = news_archive.as_of_view(FIRST, root=case["root"], illustrative=False)
    assert real["articles"] == [] and real["coverage"] is None


def test_version_tampering_and_backwards_archive_clock_are_rejected(case):
    news_archive.capture(case["source"], root=case["root"], clock=lambda: FIRST)
    context = deepcopy(case["context"])
    context["news"]["articles"][0]["title"] = "ILLUSTRATIVE changed"
    persist(case, context)
    with pytest.raises(ValueError, match="backwards"):
        news_archive.capture(case["source"], root=case["root"], clock=lambda: "2026-11-07T13:30:00Z")
    path = next(case["root"].glob("illustrative/versions/*/*.json"))
    row = json.loads(path.read_text(encoding="utf-8"))
    row["metadata"]["title"] = "tampered"
    path.write_text(json.dumps(row), encoding="utf-8")
    with pytest.raises(ValueError, match="content differs"):
        view(case, FIRST)
