"""Bounded personal workflow; optional one-time wait for the next Basic release.

No purchases, external price LLM calls, recurring daemon or model release.
"""

import argparse
from datetime import datetime, time as day_time, timedelta, timezone
import json
import os
from pathlib import Path
import time
from zoneinfo import ZoneInfo

from . import fetch_prices, prepare_models
from .news import TOPICS, collect_news, highlights_markdown, validate_collection
from .rights import as_utc, price_uses, require_entitlement
from .sec_report import ROOT
from .swing_models import load_diagnostic_snapshot, load_price_snapshot
from .validation import digest, protocol, register_forecast, retrospective_diagnostic, save_new, settle_forecasts


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def latest_snapshot(root=ROOT):
    found = []
    for path in (Path(root) / "reports/private/market_data").glob("*/prices.json"):
        try:
            data = read(path)
            rights = path.with_name("price_entitlement.json")
            if (data.get("symbol") == "BE" and data.get("provider") == "massive"
                    and data.get("is_illustrative") is False and rights.exists() and data.get("bars")):
                found.append((data["bars"][-1]["session"], as_utc(data["retrieved_at"]), path, rights))
        except (ValueError, OSError, KeyError, IndexError, TypeError):
            continue
    if not found:
        raise ValueError("No saved real BE snapshot and confirmed account record; run initial price setup")
    return max(found, key=lambda row: row[:2])[2:]


def release_state(as_of):
    _, eligible, calendar = fetch_prices.basic_window(as_of)
    latest = calendar.last_completed(as_of)
    pending = eligible < latest
    # The earliest retry follows the target session's midnight ET, plus 2 minutes.
    next_at = datetime.combine(datetime.fromisoformat(latest).date() + timedelta(days=1),
                               day_time(0, 2), tzinfo=ZoneInfo("America/New_York")) if pending else None
    return {"latest_completed_session": latest, "basic_eligible_session": eligible,
            "status": "awaiting_basic_release" if pending else "eligible_for_current_session",
            "next_action_at": next_at.astimezone(timezone.utc).isoformat() if next_at else None}


def verify_saved_raw(path, record):
    data = read(path)
    raw_path = Path(path).with_name("raw_response.json")
    if not raw_path.exists() or digest(raw_path) != data["raw_sha256"]:
        raise ValueError("Saved raw snapshot is missing or its hash differs")
    url = data["source_url"]
    dates = url.split("/day/")[-1].split("?")[0].split("/")
    if len(dates) != 2 or url != fetch_prices.request_url(*dates):
        raise ValueError("Unrecognized saved source endpoint")
    _, _, calendar = fetch_prices.basic_window(data["retrieved_at"])
    rebuilt = fetch_prices.normalize(raw_path.read_bytes(), record, data["retrieved_at"], *dates, calendar)
    if rebuilt != data:
        raise ValueError("Normalized prices differ from the verified raw response")


def fetch_with_saved_account(record, as_of):
    require_entitlement(record, price_uses("local_research"), as_of)
    if record["provider"] != "massive" or record["plan"] != "stocks_basic":
        raise ValueError("Only the reviewed free Massive Stocks Basic route is supported")
    key = os.environ.get("MASSIVE_API_KEY", "")
    if not key.strip():
        raise ValueError("MASSIVE_API_KEY is absent from this process; saved rights are independent of key visibility")
    start, end, calendar = fetch_prices.basic_window(as_of)
    raw = fetch_prices.download(start, end, key)
    retrieved = now()
    data = fetch_prices.normalize(raw, record, retrieved, start, end, calendar)
    destination = ROOT / "reports/private/market_data" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "raw_response.json").write_bytes(raw)
    save_new(destination / "prices.json", data)
    save_new(destination / "price_entitlement.json", record)
    save_new(destination / "fetch_readiness.json", {"status": "downloaded_research_only", "retrieved_at": retrieved,
        "latest_session": data["bars"][-1]["session"], "bars": len(data["bars"]), "api_key_present": True,
        "monthly_data_budget_usd": 0, "network_called": True, "account_confirmation_reused": True,
        "public_distribution_enabled": False, "external_price_llm_input_enabled": False,
        "released_score": None, "released_price_plan": None})
    return destination / "prices.json", destination / "price_entitlement.json"


def cached_news(as_of):
    paths = list((ROOT / "reports/private/advancement").glob("*/news_metadata.json"))
    paths += list((ROOT / "reports/private/model_preparation").glob("*/news_metadata.json"))
    candidates = []
    for path in paths:
        try:
            collection = read(path)
            validate_collection(collection)
            age = (as_utc(as_of) - as_utc(collection["checked_at"])).total_seconds()
            complete = collection["status"] == "available" and set(collection["completed_topics"]) == set(TOPICS)
            if 0 <= age <= 900 and (complete or collection["status"] in {"partial", "unavailable"}):
                candidates.append((as_utc(collection["checked_at"]), collection,
                                   "recent_complete_metadata" if complete else "upstream_failure_15_minute_backoff"))
        except (ValueError, OSError, KeyError, TypeError):
            continue
    if candidates:
        _, collection, basis = max(candidates, key=lambda row: row[0])
        return collection, basis
    return None, None


def run_once(*, fetch_current=True, news=True):
    cutoff = now()
    policy = protocol()
    destination = ROOT / "reports/private/advancement" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination.mkdir(parents=True, exist_ok=False)
    state = {"version": "1.0.0", "as_of": cutoff, "scope": "personal_local", "status": "research_in_progress",
             "protocol_id": policy["id"], "protocol_sha256": digest(ROOT / "config/validation_protocol.json"),
             "price_release": release_state(cutoff), "monthly_data_budget_usd": 0,
             "released_price_plan": None, "released_score": None, "released_expected_target_sessions": None,
             "release_validation_id": None, "external_price_llm_input_enabled": False,
             "output_dir": str(destination.resolve()), "stages": {}}
    snapshot_path, entitlement_path = latest_snapshot()
    record = read(entitlement_path)
    require_entitlement(record, price_uses("local_research"), cutoff)
    verify_saved_raw(snapshot_path, record)
    existing = read(snapshot_path)
    if fetch_current and existing["bars"][-1]["session"] < state["price_release"]["basic_eligible_session"]:
        try:
            snapshot_path, entitlement_path = fetch_with_saved_account(record, cutoff)
            state["stages"]["price_fetch"] = "downloaded"
        except (ValueError, OSError) as error:
            state["stages"]["price_fetch"] = {"status": "failed", "reason": str(error)}
    else:
        state["stages"]["price_fetch"] = ("not_requested" if not fetch_current else
                                            "no_duplicate_request; saved snapshot covers currently eligible session")
    cutoff = now()
    data, bars, calendar = load_diagnostic_snapshot(snapshot_path, record, cutoff)
    state["prices"] = {"snapshot_id": data["snapshot_id"], "latest_session": bars[-1].session,
                       "bars": len(bars), "point_in_time_verified": data["point_in_time_verified"],
                       "path": str(snapshot_path.resolve())}
    state["stages"]["retrospective_diagnostic"] = retrospective_diagnostic(snapshot_path, record, cutoff,
                                                                          destination / "price_diagnostic")
    ledger = ROOT / "reports/private/prospective_ledger"
    state["stages"]["prospective_outcomes"] = settle_forecasts(data, bars, calendar, cutoff, ledger)
    try:
        current, current_bars, current_calendar = load_price_snapshot(snapshot_path, record, cutoff)
        state["stages"]["prospective_forecast"] = register_forecast(current, current_bars, current_calendar, cutoff, ledger)
    except ValueError as error:
        state["stages"]["prospective_forecast"] = {"status": "withheld", "reason": str(error)}
    news_path = None
    if news:
        sources = read(ROOT / "config/source_entitlements.json")
        rights = next(row for row in sources["records"] if row["provider"] == "gdelt_project")
        collection, cache_basis = cached_news(now())
        if collection is None:
            collection = collect_news(rights)
        news_path = destination / "news_metadata.json"
        save_new(news_path, collection)
        (destination / "news_highlights.md").write_text(highlights_markdown(collection), encoding="utf-8")
        state["stages"]["news"] = {"status": collection["status"], "items": len(collection["articles"]),
                                   "cache_basis": cache_basis,
                                   "completed_topics": collection["completed_topics"],
                                   "failed_topics": collection["failed_topics"], "score_component": None}
    else:
        state["stages"]["news"] = {"status": "not_requested", "score_component": None}
    args = ["--prices", str(snapshot_path), "--price-entitlement", str(entitlement_path),
            "--output-dir", str(destination / "model_preparation")]
    if news_path:
        args += ["--news-snapshot", str(news_path)]
    prepare_models.main(args)
    state["as_of"] = now()
    state["status"] = ("waiting_for_basic_release" if state["price_release"]["next_action_at"] else
                       "partial_upstream_or_input_failure" if state["stages"]["prospective_forecast"]["status"] == "withheld" else
                       "research_completed_models_unvalidated")
    save_new(destination / "workflow_state.json", state)
    lines = ["# BE 개발·평가 진행 상태", "", f"실행: {state['as_of']}",
             f"가격: {bars[-1].session}까지 {len(bars)}개 일봉", "",
             "- 가격 후보 사전 진단: price_diagnostic/retrospective_diagnostic.md",
             f"- 향후 예측 등록: {state['stages']['prospective_forecast']['status']}",
             f"- 뉴스 수집: {state['stages']['news']['status']}",
             f"- 다음 가격 제공 후 조회 시각(UTC): {state['price_release']['next_action_at']}", "",
             "실제 과거 자료의 현재 조회본 진단과 향후 예측 기록은 모델 검증 완료가 아니다.",
             "현재 추천 목표가·매력도·예상 수익실현 시기·검증 ID는 보류다.", ""]
    (destination / "progress.md").write_text("\n".join(lines), encoding="utf-8")
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description="Advance licensed BE model preparation, research-only")
    parser.add_argument("--no-fetch", action="store_true")
    parser.add_argument("--no-news", action="store_true")
    parser.add_argument("--defer-until-price", action="store_true",
                        help="After this run, wait at most four hours and perform one final refresh; no recurring daemon")
    args = parser.parse_args(argv)
    control = ROOT / "reports/private/advance_control"
    control.mkdir(parents=True, exist_ok=True)
    lock_path = control / "active.lock"
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        parser.error("An advance process may already be active; inspect reports/private/advance_control/active.lock")
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump({"pid": os.getpid(), "started_at": now()}, output)
    def checkpoint(value):
        temporary = control / ("state-" + str(os.getpid()) + ".tmp")
        temporary.write_text(json.dumps({"pid": os.getpid(), **value}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(control / "process_state.json")
    try:
        checkpoint({"status": "running", "started_at": now()})
        state = run_once(fetch_current=not args.no_fetch, news=not args.no_news)
        print(json.dumps({"status": state["status"], "output_dir": state["output_dir"]}), flush=True)
        next_at = state["price_release"]["next_action_at"]
        if args.defer_until_price and next_at:
            due = as_utc(next_at)
            wait = (due - as_utc(now())).total_seconds()
            if wait > 4 * 3600:
                raise ValueError("Deferred action must be within four hours; no indefinite wait")
            checkpoint({"status": "waiting_until_price_release", "next_action_at": next_at,
                        "output_dir": state["output_dir"], "maximum_additional_price_requests": 1,
                        "will_run_once": True})
            # Each wait is bounded; this mode is intended for a hidden local worker.
            while as_utc(now()) < due:
                time.sleep(min(30, max(0.01, (due - as_utc(now())).total_seconds())))
            state = run_once(fetch_current=not args.no_fetch, news=not args.no_news)
            print(json.dumps({"status": state["status"], "output_dir": state["output_dir"]}), flush=True)
        checkpoint({"status": "finished", "finished_at": now(), "output_dir": state["output_dir"],
                    "released_score": None})
        return 0
    except Exception as error:
        # Exceptions are generated internally or sanitized by the adapters.
        checkpoint({"status": "failed", "finished_at": now(), "error_type": type(error).__name__,
                    "reason": str(error) if isinstance(error, ValueError) else "Workflow failed; inspect local code/permissions"})
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}), flush=True)
        return 2
    finally:
        lock_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
