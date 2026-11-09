"""Issuer-scoped metadata archives and explicit reviews; no automatic impact AI."""

from datetime import timedelta
from pathlib import Path

from . import issuer_profiles as profiles, news_archive, news_review_store as legacy, news_connections, event_review, sec_refresh
from .local_files import atomic_json, exclusive_lock
from .rights import as_utc
from .sec_report import ROOT, SYNTHETIC_LABEL
from .sec_transport import now
from .validation import digest, stable_hash


FILES = ["research/issuer_news.py", "research/issuer_profiles.py", "config/issuer_profiles.json", "config/news_refresh_policy.json", "schemas/issuer_context.schema.json"]


def methods():
    names = set(FILES + news_connections.CORE + legacy.LEGACY_FILES + ["research/news_archive.py", "config/news_archive_policy.json", "schemas/news_archive.schema.json"])
    return {name: digest(ROOT / name) for name in sorted(names)}


def folder(issuer, *, root=None, illustrative=False):
    base = Path(root or ROOT / "reports/private/issuer_news").resolve()
    if not illustrative and not base.is_relative_to((ROOT / "reports/private").resolve()):
        raise ValueError("PRIVATE_ISSUER_NEWS_STORE_REQUIRED")
    return base / ("illustrative" if illustrative else "real") / str(issuer).zfill(10)


def archive_root(issuer, *, root=None, illustrative=False):
    return folder(issuer, root=root, illustrative=illustrative) / "archive"


def check_context(context, at, *, profile_root=None, illustrative=False):
    if context["is_illustrative"] is not illustrative or as_utc(context["as_of"]) > as_utc(at):
        raise ValueError("ISSUER_NEWS_CONTEXT_CLASSIFICATION_OR_TIME_INVALID")
    selected = profiles.as_of(context["cik"], at, root=profile_root, illustrative=illustrative)
    if selected is None or context["symbol"] != selected["symbol"] or context["profile_binding"] != selected["binding"]:
        raise ValueError("ISSUER_NEWS_PROFILE_BINDING_CHANGED")
    if context["news"].get("issuer_binding") != {"cik": context["cik"], "symbol": context["symbol"], "profile_binding": selected["binding"]}:
        raise ValueError("ISSUER_NEWS_PACKET_FOR_ANOTHER_ISSUER_OR_PROFILE")
    return selected


def capture(context_path, *, root=None, profile_root=None, illustrative=False, clock=now):
    context = sec_refresh.read(context_path)
    check_context(context, clock(), profile_root=profile_root, illustrative=illustrative)
    return news_archive.capture(context_path, root=archive_root(context["cik"], root=root, illustrative=illustrative), clock=clock)


def template(context):
    return {"version": "1.0.0", "cik": context["cik"], "symbol": context["symbol"], "is_illustrative": context["is_illustrative"],
        "profile_binding": context["profile_binding"], "reviewer_kind": None, "manifest": event_review.template(context["news"])}


def receipts(issuer, cutoff=None, *, root=None, illustrative=False):
    values = []
    for path in (folder(issuer, root=root, illustrative=illustrative) / "reviews").glob("*.json"):
        value = sec_refresh.read(path)
        if cutoff is not None and not legacy.precedes(value["available_at"], cutoff):
            continue
        if value["receipt_sha256"] != stable_hash({k: v for k, v in value.items() if k != "receipt_sha256"}) or path.stem != value["receipt_id"]:
            raise ValueError("ISSUER_NEWS_REVIEW_RECEIPT_CHANGED")
        payload = {k: value[k] for k in ("review", "bindings", "method_hashes", "news_packet")}
        if stable_hash(payload) != value["receipt_id"] or value["cik"] != str(issuer).zfill(10) or value["is_illustrative"] is not illustrative:
            raise ValueError("ISSUER_NEWS_REVIEW_IDENTITY_CHANGED")
        profiles.validate(value["review"], "review")
        event_review.validate_manifest(value["manifest"])
        if value["manifest"] != value["review"]["manifest"] or stable_hash(value["news_packet"]) != value["manifest"]["news_context_sha256"] or value["review"]["cik"] != value["cik"] or value["review"]["symbol"] != value["symbol"] or value["review"]["is_illustrative"] is not illustrative:
            raise ValueError("ISSUER_NEWS_REVIEW_SOURCE_CHANGED")
        if as_utc(value["manifest"]["submitted_at"]) > as_utc(value["available_at"]):
            raise ValueError("ISSUER_NEWS_REVIEW_TIME_CHANGED")
        values.append(value)
    values.sort(key=lambda row: row["store_order"])
    if len({v["store_order"] for v in values}) != len(values) or any(as_utc(a["available_at"]) > as_utc(b["available_at"]) for a, b in zip(values, values[1:])):
        raise ValueError("ISSUER_NEWS_REVIEW_ORDER_CONFLICT")
    return values


def import_review(context_path, review_path, *, root=None, profile_root=None, illustrative=False, clock=now):
    context, review = sec_refresh.read(context_path), sec_refresh.read(review_path)
    at = clock()
    selected = check_context(context, at, profile_root=profile_root, illustrative=illustrative)
    profiles.validate(review, "review")
    if any(review[k] != context[k] for k in ("cik", "symbol", "is_illustrative", "profile_binding")):
        raise ValueError("ISSUER_NEWS_REVIEW_FOR_ANOTHER_ISSUER_OR_PROFILE")
    manifest = review["manifest"]
    event_review.validate_manifest(manifest)
    if review["reviewer_kind"] is None or manifest["template_only"] or not manifest["events"] or not manifest["reviewer_id"] or not manifest["reviewer_id"].strip() or manifest["submitted_at"] is None:
        raise ValueError("DRAFT_OR_EMPTY_ISSUER_REVIEW_REJECTED")
    if event_review.assess(context["news"], manifest, at)["invalid_review_count"] or len({e["event_id"] for e in manifest["events"]}) != len(manifest["events"]):
        raise ValueError("ISSUER_NEWS_REVIEW_EVIDENCE_OR_TIME_INVALID")
    arc = archive_root(context["cik"], root=root, illustrative=illustrative)
    capture_path = news_archive.archive_folder(arc, illustrative) / "captures" / (stable_hash(context["news"]) + ".json")
    if not capture_path.is_file() or not legacy.precedes(sec_refresh.read(capture_path)["available_at"], at):
        raise ValueError("ISSUER_NEWS_CONTEXT_MUST_BE_ARCHIVED_FIRST")
    if not illustrative:
        news_archive.require_metadata_rights({p for a in context["news"]["articles"] for p in a["providers"]}, at)
    state = legacy.archive_state(at, archive_root=arc, illustrative=illustrative)
    articles = {a["id"]: a for a in context["news"]["articles"]}
    bindings = {}
    for event in manifest["events"]:
        if any(not event[field].strip() for field in ("rationale", "five_session_relevance_explanation")):
            raise ValueError("ISSUER_NEWS_REVIEW_EXPLANATION_EMPTY")
        if event.get("novelty") == "material_update" and (not event.get("material_change_reviewed") or not event.get("material_update_at") or not as_utc(event["first_public_at"]) <= as_utc(event["material_update_at"]) <= as_utc(event["reviewed_at"])):
            raise ValueError("ISSUER_NEWS_MATERIAL_UPDATE_NOT_VERIFIED")
        for sid in event["source_ids"]:
            key, content = legacy.identity(articles[sid])
            current = state["by_article_key"].get(key)
            check = next(c for c in event["original_checks"] if c["source_id"] == sid)
            if current is None or current["content_sha256"] != content or not legacy.precedes(current["incarnation_available_at"], check["checked_at"]):
                raise ValueError("ISSUER_NEWS_CURRENT_VERSION_RECHECK_REQUIRED")
            bindings[sid] = {"article_key": key, "content_sha256": content, "incarnation_id": current["incarnation_id"], "incarnation_available_at": current["incarnation_available_at"], "url": articles[sid]["url"]}
    payload = {"review": review, "bindings": bindings, "method_hashes": methods(), "news_packet": context["news"]}
    rid = stable_hash(payload)
    destination = folder(context["cik"], root=root, illustrative=illustrative)
    with exclusive_lock(destination / "write.lock"):
        previous = receipts(context["cik"], root=root, illustrative=illustrative)
        same = next((v for v in previous if v["receipt_id"] == rid), None)
        if same:
            return {"receipt_id": rid, "available_at": same["available_at"], "status": "unchanged"}
        committed = clock()
        if as_utc(committed) < as_utc(at) or (previous and as_utc(committed) < as_utc(previous[-1]["available_at"])):
            raise ValueError("ISSUER_NEWS_REVIEW_CLOCK_MOVED_BACKWARDS")
        value = {"version": "1.0.0", **payload, "receipt_id": rid, "cik": review["cik"], "symbol": review["symbol"], "is_illustrative": illustrative,
            "manifest": manifest, "available_at": committed, "store_order": max((v["store_order"] for v in previous), default=0) + 1}
        value["receipt_sha256"] = stable_hash(value)
        atomic_json(destination / "reviews" / (rid + ".json"), value)
    return {"receipt_id": rid, "available_at": committed, "status": "stored"}


def snapshot(issuer, cutoff, *, root=None, profile_root=None, illustrative=False, clock=now):
    at, settings = clock(), profiles.policy()
    if as_utc(cutoff) > as_utc(at):
        raise ValueError("ISSUER_NEWS_FUTURE_CUTOFF")
    selected = profiles.as_of(issuer, cutoff, root=profile_root, illustrative=illustrative)
    if selected is None:
        return {"as_of": cutoff, "status": "unavailable", "reason_codes": ["NO_PROFILE_AVAILABLE_AT_CUTOFF"], "snapshot": None}
    lookup = (as_utc(cutoff).replace(microsecond=0) - timedelta(microseconds=1)).isoformat()
    state = legacy.archive_state(lookup, archive_root=archive_root(issuer, root=root, illustrative=illustrative), illustrative=illustrative)
    packets = [sec_refresh.read(Path(ref["path"])) for ref in state["source_captures"]]
    latest = max(packets, key=lambda p: (as_utc(p["available_at"]), p["capture_order"])) if packets else None
    binding_current = bool(latest and latest["news_packet"].get("issuer_binding") == {"cik": selected["cik"], "symbol": selected["symbol"], "profile_binding": selected["binding"]})
    stored = receipts(issuer, lookup, root=root, illustrative=illustrative)
    valid = [r for r in stored if binding_current and r["method_hashes"] == methods() and r["review"]["profile_binding"] == selected["binding"] and r["symbol"] == selected["symbol"]]
    frozen = {a["article_key"]: a["content_sha256"] for a in state["articles"]}
    events, equal = news_connections._events(state, valid, selected["symbol"], cutoff, settings, frozen)
    articles = []
    for a in state["by_article_key"].values():
        related = [event for event in events if a["article_key"] in event["article_keys"]]
        statuses = {e["status"] for e in related}
        status = next((s for s in ("conflicting_reviews", "review_linked", "irrelevant_reviewed", "review_needs_update") if s in statuses), "awaiting_review")
        articles.append({"article_key": a["article_key"], "metadata": a["metadata"], "content_sha256": a["content_sha256"], "incarnation_id": a["incarnation_id"],
            "last_source_observed_at": a["source_observed_at"], "metadata_fresh_at_cutoff": binding_current and 0 <= (as_utc(cutoff) - as_utc(a["source_observed_at"])).total_seconds() <= settings["metadata_freshness_seconds"],
            "source_ids": a["source_ids"], "status": status, "event_ids": [e["event_id"] for e in related], "reason_codes": sorted({code for e in related for code in e["reason_codes"]}),
            "highlight": status == "review_linked" and any(e["highlight"] for e in related)})
    providers = latest["news_packet"]["providers"] if latest else []
    current = binding_current and any((p["status"] in {"links_available", "bounded_no_links", "available", "no_recent_entries_in_bounded_feed"}
        or p["status"] == "partial" and any(p["provider"] in a["providers"] for a in latest["news_packet"]["articles"]))
        and 0 <= (as_utc(cutoff) - as_utc(p["checked_at"])).total_seconds() <= settings["metadata_freshness_seconds"] for p in providers)
    coverage = [] if latest else None
    for topic in state["coverage"] or []:
        fresh = bool(binding_current and topic.get("checked_at") and 0 <= (as_utc(cutoff) - as_utc(topic["checked_at"])).total_seconds() <= settings["metadata_freshness_seconds"])
        coverage.append({**topic, "discovery_status": topic["discovery_status"] if fresh else "not_verified", "fresh_at_cutoff": fresh})
    value = {"version": "1.0.0", "status": "issuer_news_connections_snapshot", "symbol": selected["symbol"], "cik": selected["cik"], "as_of": cutoff, "generated_at": at,
        "is_illustrative": illustrative, "data_label": SYNTHETIC_LABEL if illustrative else None, "articles": articles, "events": events, "highlights": [e["event_id"] for e in events if e["highlight"]],
        "article_status_counts": {s: sum(a["status"] == s for a in articles) for s in sorted({a["status"] for a in articles})},
        "coverage": coverage, "coverage_current_at_cutoff": current, "archive_capture_refs": state["source_captures"],
        "review_receipt_refs": [{k: r[k] for k in ("receipt_id", "receipt_sha256", "available_at")} for r in valid], "outdated_profile_or_method_reviews": len(stored) - len(valid),
        "experimental_news_score": None, "released_score": None, "calibrated_probability": None, "network_requests": 0, "external_llm_calls": 0, "model_training_performed": False,
        "publisher_body_fetched": False, "original_forecast_modified": False, "limitations": ["Issuer-bound recorded reviews are assertions; source truth is not independently certified", "Unknown coverage is not neutral risk", "Current metadata does not prove unchanged publisher body"]}
    return {"as_of": cutoff, "status": "linked_issuer_archive", "reason_codes": [] if current else ["LATEST_NEWS_COVERAGE_NOT_CONFIRMED"], "snapshot": value}


def main(argv=None):
    import argparse
    import json
    from . import project_compatibility as guard
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--context", required=True, type=Path)
    parser.add_argument("--import-review", required=True, type=Path)
    args = parser.parse_args(argv)
    proof = guard.check(args.baseline)
    result = import_review(args.context, args.import_review)
    guard.check(args.baseline, expected_sha256=proof["baseline_sha256"])
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
