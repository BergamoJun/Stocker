"""Offline article-version -> human-review -> frozen-forecast associations."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re

import jsonschema

from . import event_review, news_archive, news_review_store as store
from .learned_research import latest_training_snapshot
from .local_files import LockBusy, atomic_json
from .rights import as_utc, price_uses, require_entitlement
from .sec_report import ROOT, SYNTHETIC_LABEL
from .validation import digest, stable_hash

CORE = ["research/news_connections.py", "research/news_review_store.py", "research/news_connection_report.py",
        "research/news_connection_demo.py", "config/news_connection_policy.json", "schemas/news_connections.schema.json"]


def _events(state, receipts, symbol, cutoff, settings, frozen=None):
    latest, equal_time = {}, 0
    for receipt in receipts:
        if receipt["symbol"] != symbol:
            continue
        if not store.precedes(receipt["available_at"], cutoff):
            equal_time += 1
            continue
        for event in receipt["manifest"]["events"]:
            latest[receipt["manifest"]["reviewer_id"], event["event_id"]] = (receipt, event)
    clusters = defaultdict(list)
    for (reviewer, event_id), (receipt, event) in latest.items():
        reasons, bindings = [], [receipt["bindings"][key] for key in event["source_ids"]]
        for binding in bindings:
            current = state["by_article_key"].get(binding["article_key"])
            if current is None:
                reasons.append("ARTICLE_NOT_ARCHIVED_AT_CUTOFF")
            elif current["content_sha256"] != binding["content_sha256"]:
                reasons.append("ARTICLE_VERSION_CHANGED")
            elif current["incarnation_id"] != binding["incarnation_id"]:
                reasons.append("ARTICLE_REVERTED_REVIEW_REQUIRED")
            elif not store.precedes(current["incarnation_available_at"], cutoff):
                reasons.append("ARCHIVE_ORDER_NOT_PRIOR_TO_CUTOFF")
            if frozen is not None and frozen.get(binding["article_key"]) != binding["content_sha256"]:
                reasons.append("ARTICLE_NOT_IN_FROZEN_CONTEXT_VERSION")
        age = (as_utc(cutoff) - as_utc(event["reviewed_at"])).total_seconds() / 3600
        check_age = max((as_utc(cutoff) - as_utc(check["checked_at"])).total_seconds() / 3600 for check in event["original_checks"])
        if age < 0 or check_age < 0:
            reasons.append("REVIEW_AFTER_CUTOFF")
        elif max(age, check_age) > settings["maximum_review_reuse_hours"]:
            reasons.append("REVIEW_EXPIRED")
        status = "review_needs_update" if reasons else "irrelevant_reviewed" if event["review_status"] == "irrelevant_reviewed" else "review_linked"
        clusters[event_id].append({"event_id": event_id, "reviewer_id": reviewer, "receipt_id": receipt["receipt_id"],
            "review_available_at": receipt["available_at"], "reviewed_at": event["reviewed_at"], "status": status,
            "reason_codes": sorted(set(reasons)), "article_keys": [binding["article_key"] for binding in bindings],
            "original_urls": [binding["url"] for binding in bindings], "rationale": event["rationale"],
            "five_session_relevance_explanation": event["five_session_relevance_explanation"],
            "counterargument": event.get("counterargument"), "monitoring_event": event.get("monitoring_event"),
            "direction": event.get("direction"), "impact": event.get("impact"), "relevance": event.get("relevance"),
            "novelty": event.get("novelty"), "reliability": event.get("reliability"),
            "published_at": event.get("published_at"), "first_public_at": event.get("first_public_at"),
            "material_update_at": event.get("material_update_at"), "material_change_reviewed": event.get("material_change_reviewed", False)})
    result = []
    for event_id, opinions in sorted(clusters.items()):
        usable = [row for row in opinions if row["status"] in {"review_linked", "irrelevant_reviewed"}]
        signatures = {tuple(row.get(key) for key in ("status", "direction", "impact", "relevance", "novelty", "reliability")) for row in usable}
        conflict = len(signatures) > 1
        representative = usable[0] if usable else opinions[0]
        status = "conflicting_reviews" if conflict else representative["status"]
        audit_article_keys = sorted({key for opinion in opinions for key in opinion["article_keys"]})
        article_keys = sorted({key for opinion in (usable or opinions) for key in opinion["article_keys"]})
        fresh = all(key in state["by_article_key"] and 0 <= (as_utc(cutoff) - as_utc(state["by_article_key"][key]["source_observed_at"])).total_seconds() <= settings["metadata_freshness_seconds"] for key in article_keys)
        origin = representative.get("material_update_at") or representative.get("first_public_at") or representative.get("published_at")
        event_age = (as_utc(cutoff) - as_utc(origin)).total_seconds() / 3600 if origin else None
        highlight = bool(status == "review_linked" and fresh and representative["direction"] in {-1, 1}
            and representative["impact"] in {"medium", "high"} and representative["reliability"] != "unverified"
            and representative["novelty"] in {"new", "material_update"} and event_age is not None and 0 <= event_age <= settings["maximum_event_age_hours"])
        result.append({"event_id": event_id, "status": status, "highlight": highlight, "article_keys": article_keys,
            "audit_article_keys": audit_article_keys,
            "opinions": opinions, "metadata_fresh_at_cutoff": fresh, "event_age_hours": event_age,
            "reason_codes": ["REVIEWER_DISAGREEMENT"] if conflict else sorted({code for opinion in opinions for code in opinion["reason_codes"]}),
            "experimental_news_score": None, "released_score": None})
    return result, equal_time


def snapshot(cutoff=None, *, symbol="BE", root=None, archive_root=None, illustrative=False, clock=store.now, frozen_news=None):
    settings, at = store.policy(), clock()
    cutoff = cutoff or at
    if symbol not in settings["allowed_symbols"] or as_utc(cutoff) > as_utc(at):
        raise ValueError("UNSUPPORTED_SYMBOL_OR_FUTURE_QUERY")
    # A frozen forecast has no shared sequence counter with either store.
    # Exclude its entire timestamp second, including audit/provenance counts,
    # so later same-second commits cannot revise a historical association.
    lookup_cutoff = (as_utc(cutoff).replace(microsecond=0) - timedelta(microseconds=1)).isoformat() if frozen_news is not None else cutoff
    state = store.archive_state(lookup_cutoff, archive_root=archive_root, illustrative=illustrative)
    receipts = store.reviews_as_of(lookup_cutoff, root=root, illustrative=illustrative)
    frozen = {store.identity(article)[0]: store.identity(article)[1] for article in frozen_news["articles"]} if frozen_news is not None else None
    events, equal_time = _events(state, receipts, symbol, cutoff, settings, frozen)
    if frozen is not None:
        events = [event for event in events if set(event["article_keys"]) & frozen.keys()]
    articles = []
    source_articles = frozen_news["articles"] if frozen_news is not None else [item["metadata"] for item in state["articles"]]
    for source in source_articles:
        key, content = store.identity(source)
        archived = state["by_article_key"].get(key)
        archive_reason = ("ARTICLE_NOT_ARCHIVED_AT_CUTOFF" if archived is None else "ARTICLE_VERSION_CHANGED" if archived["content_sha256"] != content
                          else "ARCHIVE_ORDER_NOT_PRIOR_TO_CUTOFF" if not store.precedes(archived["incarnation_available_at"], cutoff) else None)
        related = [event for event in events if key in event["audit_article_keys"]]
        statuses = {"conflicting_reviews" if event["status"] == "conflicting_reviews" else opinion["status"]
            for event in related for opinion in event["opinions"] if key in opinion["article_keys"]}
        status = "archive_unavailable_at_cutoff" if archive_reason else "conflicting_reviews" if "conflicting_reviews" in statuses else "review_linked" if "review_linked" in statuses else "irrelevant_reviewed" if statuses == {"irrelevant_reviewed"} else "review_needs_update" if related else "awaiting_review"
        articles.append({"article_key": key, "content_sha256": content, "metadata": store.metadata(source),
            "status": status, "reason_codes": [archive_reason] if archive_reason else [],
            "archive_first_available_at": archived["version_first_available_at"] if archived else None,
            "incarnation_id": archived["incarnation_id"] if archived and not archive_reason else None,
            "last_source_observed_at": archived["source_observed_at"] if archived else None,
            "metadata_fresh_at_cutoff": bool(not archive_reason and archived and 0 <= (as_utc(cutoff) - as_utc(archived["source_observed_at"])).total_seconds() <= settings["metadata_freshness_seconds"]),
            "source_ids": source.get("source_ids", archived["source_ids"] if archived else []),
            "event_ids": [event["event_id"] for event in related], "present_in_frozen_context": frozen is not None,
            "highlight": status == "review_linked" and not archive_reason and any(event["highlight"] and key in event["article_keys"] for event in related)})
    counts = dict(Counter(article["status"] for article in articles))
    return {"version": "1.0.0", "status": "news_connections_snapshot", "policy_id": settings["id"], "symbol": symbol,
        "as_of": cutoff, "generated_at": at, "is_illustrative": illustrative, "data_label": SYNTHETIC_LABEL if illustrative else None,
        "articles": articles, "events": events, "highlights": [event["event_id"] for event in events if event["highlight"]],
        "article_status_counts": counts, "equal_timestamp_receipts_withheld": equal_time,
        "archive_capture_refs": state["source_captures"],
        "review_receipt_refs": [{"receipt_id": item["receipt_id"], "receipt_sha256": item["receipt_sha256"], "available_at": item["available_at"]} for item in receipts],
        "policy_sha256": digest(ROOT / "config/news_connection_policy.json"),
        "coverage": state["coverage"], "coverage_current_at_cutoff": bool(state["coverage_checked_at"] and 0 <= (as_utc(cutoff) - as_utc(state["coverage_checked_at"])).total_seconds() <= settings["metadata_freshness_seconds"]),
        "review_reuse_scope": "explanation_only_no_automatic_legacy_score_rebinding",
        "experimental_news_score": None, "released_score": None, "calibrated_probability": None,
        "model_training_performed": False, "network_requests": 0, "external_llm_calls": 0,
        "publisher_body_fetched": False, "original_forecast_modified": False,
        "limitations": ["Human source checks and impact assertions are not independently verified truth",
            "Unchanged metadata does not prove unchanged publisher article body",
            "Archive/review timestamps are local records, not independent timestamp certification",
            "Unknown topic coverage is not neutral coverage or absence of political/commodity risk",
            "Connections and highlights do not prove improved return prediction"]}


def legacy_forecast_proof(path, record):
    """Explicit old-format adapter; never guess time from an unpinned file."""
    from .observe import check_job
    if not path.is_relative_to(ROOT / "reports/private/context_ledger"):
        raise ValueError("LEGACY_FORECAST_REQUIRES_PINNED_ORIGINAL_PROOF")
    for candidate in (ROOT / "reports/private/observation_jobs").glob("*/job.json"):
        metadata = store.read(candidate)
        matching = [target for target in metadata.get("targets", []) if target.get("kind") == "context" and Path(target["path"]).resolve() == path]
        if not matching:
            continue
        checked = check_job(candidate)
        target = matching[0]
        if checked["symbol"] != record["symbol"] or checked["is_illustrative"] is not False or target["record_id"] != record["record_id"] or target["sha256"] != digest(path):
            raise ValueError("LEGACY_FORECAST_PINNED_IDENTITY_DIFFERS")
        return {"job_path": str(candidate.resolve()), "job_sha256": digest(candidate), "forecast_sha256": target["sha256"]}
    raise ValueError("LEGACY_FORECAST_REQUIRES_PINNED_ORIGINAL_PROOF")


def forecast_summary(path, *, illustrative=False):
    path = Path(path).resolve()
    if not path.is_relative_to(ROOT / "reports/private"):
        raise ValueError("PRIVATE_FORECAST_REQUIRED")
    if not illustrative:
        # Do not parse the price-derived screen embedded in the old forecast
        # before the operator's current personal analytics rights are checked.
        _, entitlement_path = latest_training_snapshot()
        require_entitlement(store.read(entitlement_path), price_uses("local_research"), store.now())
    record = store.read(path)
    if record.get("is_illustrative") is not illustrative or record.get("status") != "prospective_context_record" or record.get("symbol") not in store.policy()["allowed_symbols"]:
        raise ValueError("FORECAST_IDENTITY_OR_CLASSIFICATION_INVALID")
    if not re.fullmatch(r"[a-f0-9]{24}", record["record_id"]):
        raise ValueError("FORECAST_RECORD_ID_INVALID")
    if record["decision_scope"] != "price_cost_screen_only":
        raise ValueError("UNSUPPORTED_ORIGINAL_DECISION_SCOPE")
    if not as_utc(record["recorded_at"]) < as_utc(record["entry_at"]) or len(record["sessions"]) != 5:
        raise ValueError("FORECAST_TIMING_INVALID")
    context_path, base_path = Path(record["context_path"]).resolve(), Path(record["base_forecast_path"]).resolve()
    if not context_path.is_relative_to(ROOT / "reports/private") or not base_path.is_relative_to(ROOT / "reports/private"):
        raise ValueError("FORECAST_SOURCE_OUTSIDE_PRIVATE_STORE")
    if digest(context_path) != record["context_sha256"] or digest(base_path) != record["base_forecast_sha256"]:
        raise ValueError("FROZEN_FORECAST_SOURCE_CHANGED")
    base = store.read(base_path)
    if base.get("symbol") != record["symbol"] or base.get("is_illustrative") is not illustrative:
        raise ValueError("BASE_FORECAST_IDENTITY_DIFFERS")
    context = store.read(context_path)
    legacy = "as_of" not in record or "is_illustrative" not in context
    proof = legacy_forecast_proof(path, record) if legacy and not illustrative else None
    if legacy and proof is None:
        raise ValueError("LEGACY_FORECAST_REQUIRES_PINNED_ORIGINAL_PROOF")
    classification = context.get("is_illustrative", record["is_illustrative"] if proof else None)
    cutoff = record.get("as_of", context["as_of"] if proof else None)
    if classification is not illustrative or (not illustrative and context.get("data_label") == SYNTHETIC_LABEL) or not as_utc(context["as_of"]) <= as_utc(cutoff) <= as_utc(record["recorded_at"]):
        raise ValueError("FROZEN_CONTEXT_CLASSIFICATION_OR_TIME_INVALID")
    if legacy and as_utc(record["screen"]["as_of"]) > as_utc(cutoff):
        raise ValueError("LEGACY_SCREEN_FOLLOWS_CONTEXT_CUTOFF")
    if len({store.identity(article)[0] for article in context["news"]["articles"]}) != len(context["news"]["articles"]):
        raise ValueError("DUPLICATE_FROZEN_ARTICLE_IDENTITY")
    # No raw price, plan, screen metrics or realized returns leave this loader.
    return {key: record[key] for key in ("record_id", "symbol", "recorded_at", "entry_at", "decision_scope")} | {
        "as_of": cutoff, "cutoff_source": "pinned_legacy_context_as_of" if "as_of" not in record else "original_forecast_as_of",
        "legacy_pinned_proof": proof,
        "forecast_path": str(path), "forecast_sha256": digest(path), "context_sha256": record["context_sha256"],
        "base_forecast_sha256": record["base_forecast_sha256"], "news": context["news"]}


def bind_forecast(path, *, root=None, archive_root=None, illustrative=False, clock=store.now):
    forecast = forecast_summary(path, illustrative=illustrative)
    at = clock()
    if as_utc(forecast["recorded_at"]) > as_utc(at):
        raise ValueError("FORECAST_NOT_YET_RECORDED")
    value = snapshot(forecast["as_of"], symbol=forecast["symbol"], root=root, archive_root=archive_root,
        illustrative=illustrative, clock=clock, frozen_news=forecast["news"])
    value.update(status="frozen_forecast_news_availability_audit", forecast={key: item for key, item in forecast.items() if key != "news"},
        association_role=store.policy()["association_role"], news_used_in_original_price_decision=False,
        original_action_changed=False)
    folder = store.namespace(root, illustrative) / "forecasts" / forecast["record_id"]
    # Creation time is excluded: future reviews cannot generate a revised
    # version of the same historical knowledge or reset its first link time.
    fingerprint = {key: item for key, item in value.items() if key != "generated_at"}
    key = stable_hash(fingerprint)
    destination = folder / (key + ".json")
    with store.exclusive_lock(folder / "link_writer.lock"):
        if destination.exists():
            previous = store.read(destination)
            if stable_hash({name: item for name, item in previous.items() if name != "generated_at"}) != key:
                raise ValueError("IMMUTABLE_FORECAST_NEWS_LINK_CHANGED")
            return previous, destination
        store.validate(value, "snapshot")
        atomic_json(destination, value)
    return value, destination


def save_snapshot(value, *, root=None):
    from .news_connection_report import markdown
    store.validate(value, "snapshot")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder = store.namespace(root, value["is_illustrative"]) / "views" / stamp
    atomic_json(folder / "connections.json", value)
    atomic_json(folder / "provenance.json", {"source_ids": sorted({source for article in value["articles"] for source in article["source_ids"]}),
        "policy": store.policy(), "code_sha256": {name: digest(ROOT / name) for name in CORE},
        "review_receipt_ids": sorted({opinion["receipt_id"] for event in value["events"] for opinion in event["opinions"]}),
        "archive_capture_refs": value["archive_capture_refs"], "review_receipt_refs": value["review_receipt_refs"],
        "forecast": value.get("forecast"), "numeric_price_context_sent_to_llm": False})
    (folder / "REPORT.ko.md").write_text(markdown(value), encoding="utf-8")
    return folder


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--capture", type=Path)
    modes.add_argument("--import-review", type=Path)
    modes.add_argument("--forecast", type=Path)
    modes.add_argument("--demo", action="store_true")
    parser.add_argument("--context", type=Path)
    parser.add_argument("--as-of")
    parser.add_argument("--illustrative", action="store_true")
    args = parser.parse_args(argv)
    if args.as_of and (args.capture or args.import_review or args.forecast or args.demo):
        parser.error("--as-of is for read-only archive views; forecast cutoff is fixed")
    try:
        if args.demo:
            from .news_connection_demo import run
            result = run()
            print(json.dumps(result))
            return 0
        receipt_result = None
        if args.capture:
            context = store.read(args.capture)
            if context.get("is_illustrative") is not args.illustrative:
                raise ValueError("CONTEXT_CLASSIFICATION_DIFFERS")
            news_archive.capture(args.capture, clock=store.now)
        if args.import_review:
            if args.context is None:
                parser.error("Review import requires the exact --context file")
            receipt_result = store.store(args.context, args.import_review, illustrative=args.illustrative)
        if args.forecast:
            value, binding_path = bind_forecast(args.forecast, illustrative=args.illustrative)
        else:
            value = snapshot(args.as_of, illustrative=args.illustrative)
        folder = save_snapshot(value)
        if args.capture:
            atomic_json(folder / "review_input.template.json", event_review.template(context["news"]))
            atomic_json(folder / "review_queue.json", event_review.queue(context["news"]))
    except LockBusy:
        print(json.dumps({"status": "deferred", "reason_code": "NEWS_CONNECTION_STORE_BUSY", "network_requests": 0}))
        return 0
    except (ValueError, OSError, KeyError, TypeError, jsonschema.ValidationError):
        print(json.dumps({"status": "withheld", "reason_code": "NEWS_CONNECTION_INPUT_RIGHTS_OR_INTEGRITY_CHECK_FAILED", "network_requests": 0}))
        return 1
    print(json.dumps({"status": value["status"], "is_illustrative": value["is_illustrative"], "article_count": len(value["articles"]),
        "article_status_counts": value["article_status_counts"], "linked_events": sum(event["status"] == "review_linked" for event in value["events"]),
        "highlight_count": len(value["highlights"]), "review_stored": receipt_result["status"] if receipt_result else None,
        "network_requests": 0, "external_llm_calls": 0, "original_forecast_modified": False, "output_dir": str(folder.resolve())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
