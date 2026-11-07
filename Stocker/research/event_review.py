"""Bind analyst reviews to observed metadata and reuse the frozen news formula."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import jsonschema

from .news import canonical_url, reviewed_event_signal
from .official_news import policy
from .rights import as_utc
from .sec_report import ROOT, SYNTHETIC_LABEL
from .validation import digest, now, save_new, stable_hash


def queue(news):
    return {"version": "1.0.0", "policy_id": policy()["id"], "news_context_sha256": stable_hash(news),
        "pending": [{"article_id": row["id"], "title": row["title"], "original_url": row["url"],
            "provider_published_at": row["provider_published_at"], "observed_at": row["retrieved_at"],
            "topics": row["categories"], "fresh_metadata": row["fresh_metadata"],
            "questions_ko": ["원문이 실제로 공개된 시각을 확인했나요?", "BE의 사업이나 약 5거래일 가격에 영향을 줄 이유는 무엇인가요?",
                "좋은 영향·나쁜 영향·혼재 중 어느 쪽인가요?", "새 사실인가요, 이전 사건을 다시 쓴 기사인가요?", "반대 해석과 앞으로 확인할 사건은 무엇인가요?"]}
            for row in news["articles"] if row["fresh_metadata"]]}


def template(news):
    return {"version": "1.0.0", "policy_id": policy()["id"], "news_context_sha256": stable_hash(news),
        "template_only": True, "reviewer_id": None, "submitted_at": None, "events": []}


def validate_manifest(value):
    schema = json.loads((ROOT / "schemas/news_event_review.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(value)


def assess(news, manifest, as_of):
    cutoff = as_utc(as_of)
    rows = {row["id"]: row for row in news["articles"] if row["fresh_metadata"]}
    reasons, errors, valid, covered = [], [], [], set()
    if as_utc(policy()["frozen_at"]) > cutoff:
        reasons.append("POLICY_NOT_FROZEN_AT_CUTOFF")
    if any(as_utc(row["retrieved_at"]) > cutoff for row in rows.values()):
        reasons.append("FUTURE_METADATA")
    if any((cutoff - as_utc(row["retrieved_at"])).total_seconds() > 1800 for row in rows.values()):
        reasons.append("STALE_ARTICLE_METADATA")
    expected_topics = {"company", "sector", "macro_politics", "commodities"}
    if len(news["topics"]) != 4 or {row["id"] for row in news["topics"]} != expected_topics:
        reasons.append("INVALID_DISCOVERY_STRUCTURE")
    missing_topics = [row["id"] for row in news["topics"] if row["discovery_status"] != "query_completed_links_unreviewed"]
    if missing_topics or not news["all_topics_discovered"]:
        reasons.append("BROAD_DISCOVERY_INCOMPLETE")
    if manifest is None:
        reasons.append("NO_SUBMITTED_REVIEW")
    else:
        try:
            validate_manifest(manifest)
            if manifest["news_context_sha256"] != stable_hash(news):
                raise ValueError("Review is bound to different metadata; no silent reuse after input changes")
            if manifest["template_only"] or not manifest["reviewer_id"] or manifest["submitted_at"] is None:
                raise ValueError("Draft template is not a submitted analyst review")
            if as_utc(manifest["submitted_at"]) > cutoff:
                raise ValueError("Review submission is after decision cutoff")
            for event in manifest["events"]:
                try:
                    cited = [rows[key] for key in event["source_ids"]]
                    reviewed = as_utc(event["reviewed_at"])
                    if reviewed > as_utc(manifest["submitted_at"]):
                        raise ValueError("Individual review follows manifest submission")
                    urls = {canonical_url(value) for value in event["original_urls"]}
                    if not {canonical_url(row["url"]) for row in cited} <= urls:
                        raise ValueError("Cited article URLs are missing")
                    checks = {check["source_id"]: check for check in event["original_checks"]}
                    if len(checks) != len(event["original_checks"]) or set(checks) != set(event["source_ids"]):
                        raise ValueError("Every cited article requires exactly one original-source check")
                    for row in cited:
                        check = checks[row["id"]]
                        if canonical_url(check["url"]) != canonical_url(row["url"]):
                            raise ValueError("Original-source check URL differs")
                        if not as_utc(row["retrieved_at"]) <= as_utc(check["checked_at"]) <= reviewed <= cutoff:
                            raise ValueError("Original-source review predates observation or follows cutoff")
                        if event["review_status"] == "reviewed":
                            if check["publication_verified"] is not True or check["published_at"] is None:
                                raise ValueError("A feed or provider timestamp is not verified original publication")
                            if as_utc(check["published_at"]) > as_utc(row["retrieved_at"]):
                                raise ValueError("Original publication follows source observation")
                    if event["review_status"] == "reviewed" and as_utc(event["published_at"]) != min(as_utc(check["published_at"]) for check in checks.values()):
                        raise ValueError("Event publication differs from verified original-source checks")
                    if event["review_status"] == "reviewed" and as_utc(event["first_public_at"]) > as_utc(event["published_at"]):
                        raise ValueError("First public knowledge cannot follow this publication")
                    normalized = {**event, "retrieved_at": max((row["retrieved_at"] for row in cited), key=as_utc),
                        "first_seen_at": min((row["first_seen_at"] or row["retrieved_at"] for row in cited), key=as_utc)}
                    valid.append(normalized)
                    covered.update(event["source_ids"])
                except (ValueError, KeyError, TypeError):
                    errors.append("INVALID_EVENT_EVIDENCE_OR_TIME")
        except (ValueError, KeyError, TypeError, jsonschema.ValidationError):
            errors.append("INVALID_OR_DRAFT_REVIEW_MANIFEST")
    pending = sorted(rows.keys() - covered)
    if pending:
        reasons.append("LINKS_STILL_NEED_REVIEW")
    if errors:
        reasons.append("INVALID_REVIEW_EVIDENCE")
    current_checks = [row["checked_at"] for row in news["providers"] if row["fresh"] and 0 <= (cutoff - as_utc(row["checked_at"])).total_seconds() <= 1800]
    if not current_checks:
        reasons.append("NO_FRESH_COLLECTION")
    collection = {"status": "available" if not missing_topics and news["all_topics_discovered"] else "partial",
        "completed_topics": [row["id"] for row in news["topics"] if row["discovery_status"] == "query_completed_links_unreviewed"],
        "checked_at": min(current_checks, key=as_utc) if current_checks else as_of,
        "articles": [{"id": row["id"], "url": row["url"], "retrieved_at": row["retrieved_at"],
            "first_seen_at": row["first_seen_at"] or row["retrieved_at"]} for row in rows.values()]}
    signal = reviewed_event_signal(valid, collection, as_of) if not reasons else {"status": "unavailable", "experimental_component": None, "public_component": None}
    if signal["experimental_component"] is None and not reasons:
        reasons.append("LEGACY_SIGNAL_VALIDATION_WITHHELD")
    return {"status": signal["status"], "policy_id": policy()["id"], "as_of": as_of,
        "news_context_sha256": stable_hash(news), "current_article_count": len(rows), "reviewed_article_count": len(covered),
        "pending_article_ids": pending, "missing_discovery_topics": missing_topics, "reason_codes": reasons, "invalid_review_count": len(errors),
        "experimental_component": signal["experimental_component"], "public_component": None, "released_score": None,
        "validation_id": None, "external_llm_calls": 0, "legacy_signal": signal,
        "interpretation_ko": "원문과 사건 영향을 검토한 근거만 계산합니다. 점수는 연구용 후보이며 실제 주가 적중 확률이 아닙니다."}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline original-source review queue and candidate news signal")
    parser.add_argument("--context", type=Path)
    parser.add_argument("--reviews", type=Path)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports/private/news_review")
    args = parser.parse_args(argv)
    if not args.output_dir.resolve().is_relative_to(ROOT / "reports/private"):
        parser.error("Private review output required")
    path = args.context or sorted((ROOT / "reports/private/context_report").glob("*/evidence_context.json"))[-1]
    context = json.loads(path.read_text(encoding="utf-8"))
    if type(context.get("is_illustrative")) is not bool:
        parser.error("Explicit real/illustrative context classification required; regenerate the context report")
    manifest = json.loads(args.reviews.read_text(encoding="utf-8")) if args.reviews else None
    result = assess(context["news"], manifest, now())
    folder = args.output_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    save_new(folder / "review_queue.json", queue(context["news"]))
    save_new(folder / "review_input.template.json", template(context["news"]))
    save_new(folder / "news_signal.json", {**result, "is_illustrative": context["is_illustrative"],
        "data_label": SYNTHETIC_LABEL if context["is_illustrative"] else None, "context_path": str(path.resolve()), "context_sha256": digest(path),
        "registration": {"news_review_policy_sha256": digest(ROOT / "config/news_review_policy.json"),
            "legacy_policy_sha256": digest(ROOT / "config/swing_candidate_policy.json"), "schema_sha256": digest(ROOT / "schemas/news_event_review.schema.json"),
            "review_manifest_sha256": digest(args.reviews) if args.reviews else None,
            "code_sha256": {name: digest(ROOT / "research" / name) for name in ("event_review.py", "news.py")}}})
    print(json.dumps({"status": result["status"], "reviewed_article_count": result["reviewed_article_count"],
        "is_illustrative": context["is_illustrative"], "data_label": SYNTHETIC_LABEL if context["is_illustrative"] else None,
        "pending_article_count": len(result["pending_article_ids"]), "reason_codes": result["reason_codes"], "output_dir": str(folder.resolve())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
