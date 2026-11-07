"""Readiness and research artifacts only; does not emit an investment report."""

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .news import TOPICS, collect_news, highlights_markdown, reviewed_event_signal, validate_collection
from .rights import as_utc, price_uses, require_entitlement
from .sec_report import ROOT, MetricEngine, SourceAdapter
from .swing_models import (attractiveness_candidate, candidate_plan, load_price_snapshot,
                           normalize_candidate_features, timing_candidate, walk_forward_cases)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Prepare licensed-input model research and free news metadata; no buy/sell report")
    parser.add_argument("--collect-news", action="store_true")
    parser.add_argument("--topics", nargs="+", choices=list(TOPICS))
    parser.add_argument("--news-snapshot", type=Path)
    parser.add_argument("--event-assessments", type=Path)
    parser.add_argument("--prices", type=Path)
    parser.add_argument("--ticker", choices=["BE"], default="BE", help="Initial news/research profile is Bloom Energy only")
    parser.add_argument("--price-entitlement", type=Path)
    parser.add_argument("--market-benchmark", type=Path, help="Explicit licensed broad-market OHLCV snapshot")
    parser.add_argument("--sector-benchmark", type=Path, help="Explicit licensed sector benchmark OHLCV snapshot")
    parser.add_argument("--sec-snapshot", type=Path, default=ROOT / "research_inputs/BE/2026-09-30.json")
    parser.add_argument("--scope", choices=["public_report", "local_research"], default="local_research")
    parser.add_argument("--as-of")
    parser.add_argument("--output-dir", type=Path, default=Path("reports/private/model_preparation"))
    args = parser.parse_args(argv)
    if args.collect_news and (args.news_snapshot or args.as_of):
        parser.error("Live collection cannot be mixed with a saved/historical news cutoff")
    policy = read(ROOT / "config/swing_candidate_policy.json")
    rights = read(ROOT / "config/source_entitlements.json")
    collection = None
    if args.collect_news:
        gdelt = next(item for item in rights["records"] if item["provider"] == "gdelt_project")
        collection = collect_news(gdelt, topics=args.topics)
    elif args.news_snapshot:
        collection = read(args.news_snapshot)
        validate_collection(collection)
    cutoff = args.as_of or now()
    as_utc(cutoff)
    if collection:
        current_rights = next((record for record in rights["records"]
                               if record["id"] == collection["entitlement_id"]
                               and record["provider"] == collection["provider"]), None)
        if current_rights is None:
            raise ValueError("News snapshot has no known metadata entitlement")
        require_entitlement(current_rights, ["store_metadata", "display_metadata_links"], cutoff)
    news_age = ((as_utc(cutoff) - as_utc(collection["checked_at"])).total_seconds()
                if collection else None)
    readiness = {
        "as_of": cutoff, "status": "not_ready_for_investment_report",
        "scope": args.scope,
        "personal_price_source": {"provider": "massive", "plan": "stocks_basic",
                                  "terms_review": "personal_local_reviewed_account_confirmation_pending",
                                  "api_key_present": bool(os.environ.get("MASSIVE_API_KEY", "").strip()),
                                  "credential_visibility_scope": "current_process_only; saved entitlements checked separately",
                                  "external_price_llm_input_enabled": False},
        "monthly_data_budget_usd": 0, "public_price_rights": "not_cleared",
        "policy_id": policy["id"],
        "policy_sha256": hashlib.sha256((ROOT / "config/swing_candidate_policy.json").read_bytes()).hexdigest(),
        "source_entitlements_sha256": hashlib.sha256((ROOT / "config/source_entitlements.json").read_bytes()).hexdigest(),
        "code_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in sorted((ROOT / "research").glob("*.py"))},
        "target_model": "candidate_implemented_not_empirically_validated",
        "attractiveness_model": "candidate_rubric_weights_and_signal_math; empirical_calibration_not_approved",
        "target_timing_model": "empirical_candidate_not_validated",
        "released_score": None, "released_price_plan": None, "released_expected_target_sessions": None,
        "news": {"status": collection["status"] if collection else "not_collected",
                 "freshness": ("not_collected" if news_age is None else
                               "not_available_at_cutoff" if news_age < 0 else
                               "fresh_metadata" if news_age <= 1800 else "stale_metadata"),
                 "age_seconds": news_age,
                 "original_article_fetch_enabled": False, "publisher_text_llm_input_enabled": False},
        "blockers": [*(["No zero-cost public-price/AI entitlement verified"] if args.scope == "public_report" else
                         ["Personal price source requires confirmed own free account/eligibility"]),
                     "Fresh licensed BE snapshot required for current price plan",
                     "No licensed point-in-time BE price history available for evaluation",
                     "Candidate normalization/weights and preregistered acceptance criteria require review",
                     "No reviewed held-out validation record for price/score/timing models"],
    }
    price_context = None
    if args.prices:
        try:
            if not args.price_entitlement:
                raise ValueError("Price calculations require an explicit source-use entitlement")
            entitlement = read(args.price_entitlement)
            require_entitlement(entitlement, price_uses(args.scope), cutoff,
                                synthetic=entitlement.get("is_illustrative") is True)
            readiness["personal_price_source"].update(provider=entitlement["provider"], plan=entitlement["plan"],
                terms_review="operator_record_passed_technical_checks_not_legal_proof")
            readiness["blockers"] = [item for item in readiness["blockers"]
                                     if item != "Personal price source requires confirmed own free account/eligibility"]
            snapshot, bars, calendar = load_price_snapshot(args.prices, entitlement, cutoff, args.scope)
            if snapshot["symbol"] != args.ticker:
                raise ValueError("Price snapshot symbol differs from the requested research target")
            plan = candidate_plan(bars, calendar, cutoff)
            if snapshot["point_in_time_verified"]:
                evaluation = walk_forward_cases(bars, calendar)
                timing = timing_candidate(evaluation["training_cases"], cutoff)
            else:
                evaluation = {"status": "blocked", "release_validation_id": None,
                              "reason": "Retrospective download is not verified point-in-time history; no historical availability invented"}
                timing = {"status": "unavailable", "released_expected_target_sessions": None,
                          "reason": "No eligible point-in-time evaluation cases"}
            readiness["price_research"] = {"status": "experimental_unvalidated",
                "symbol": snapshot["symbol"], "is_illustrative": snapshot["is_illustrative"], "snapshot_id": snapshot["snapshot_id"],
                "point_in_time_verified": snapshot["point_in_time_verified"],
                "candidate": plan, "evaluation": evaluation, "timing_candidate": timing}
            price_context = (snapshot, bars, entitlement, plan)
            readiness["personal_price_source"].update(provider=snapshot["provider"], plan=entitlement["plan"])
            readiness["personal_price_source"]["terms_review"] = "operator_record_passed_technical_checks_not_legal_proof"
            readiness["blockers"] = [item for item in readiness["blockers"]
                                     if item != "Fresh licensed BE snapshot required for current price plan"]
            if args.scope == "public_report":
                readiness["public_price_rights"] = "operator_record_passed_technical_checks_not_legal_proof"
                readiness["blockers"] = [item for item in readiness["blockers"]
                                         if item != "No zero-cost public-price/AI entitlement verified"]
        except (ValueError, OSError, KeyError) as error:
            readiness["price_research"] = {"status": "blocked", "reason": str(error)}
    if args.event_assessments:
        if not collection:
            parser.error("Event assessment requires a corresponding collected-news snapshot")
        readiness["event_signal"] = reviewed_event_signal(read(args.event_assessments), collection, cutoff)
    if price_context and args.market_benchmark and args.sector_benchmark and "event_signal" in readiness:
        try:
            stock, bars, entitlement, plan = price_context
            market, market_bars, _ = load_price_snapshot(args.market_benchmark, entitlement, cutoff, args.scope)
            sector, sector_bars, _ = load_price_snapshot(args.sector_benchmark, entitlement, cutoff, args.scope)
            sec, facts, _ = SourceAdapter.load(args.sec_snapshot, cutoff)
            metrics, _, _ = MetricEngine.calculate(facts)
            source_ids = {"prices": stock["source_ids"], "market": market["source_ids"],
                          "sector": sector["source_ids"], "fundamentals": sorted({row.source_id for row in facts.values()})}
            features = normalize_candidate_features(bars, market_bars, sector_bars, metrics, source_ids,
                plan, cutoff, max(source["published_at"] for source in sec["sources"]))
            readiness["candidate_features"] = features
            readiness["attractiveness_research"] = attractiveness_candidate(features, readiness["event_signal"])
        except (ValueError, OSError, KeyError) as error:
            readiness["attractiveness_research"] = {"status": "blocked", "score": None, "reason": str(error)}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = args.output_dir / stamp
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "model_readiness.json").write_text(json.dumps(readiness, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if collection:
        (destination / "news_metadata.json").write_text(json.dumps(collection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (destination / "news_highlights.md").write_text(highlights_markdown(collection), encoding="utf-8")
    print(json.dumps({"output_dir": str(destination.resolve()), "status": readiness["status"],
                      "news_status": readiness["news"]["status"], "released_score": None,
                      "news_items": len(collection["articles"]) if collection else 0}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
