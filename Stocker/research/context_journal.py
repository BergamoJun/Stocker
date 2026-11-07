"""Freeze context/screen before entry; reuse the existing immutable price ledger."""

from datetime import datetime, time, timedelta
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from . import peak_research
from .rights import as_utc
from .sec_report import ROOT
from .validation import digest, now, save_new, stable_hash


def register(prediction, screen, snapshot, bars, calendar, as_of, context_path, ledger_dir=None):
    if prediction is None or prediction.get("plan") is None or screen is None or screen["screen_decision"] == "unavailable":
        return {"status": "withheld", "reason": "유효한 가격·비용 계산이 없어 사전 기록을 보류했습니다."}
    settings_hash = digest(ROOT / "config/context_policy.json")
    screen_hash = digest(ROOT / "config/trade_screen_policy.json")
    key = stable_hash({"symbol": snapshot["symbol"], "reference": screen["reference_session"],
        "context_policy": settings_hash, "screen_policy": screen_hash, "engine": prediction["engine"]})[:24]
    folder = (Path(ledger_dir) if ledger_dir else ROOT / "reports/private/context_ledger") / key
    if (folder / "forecast.json").exists():
        return {"status": "already_recorded", "record_id": key, "path": str(folder.resolve())}
    plan = prediction["plan"]
    entry_at = calendar.calendar.session_open(plan["timing"]["sessions"][0]).isoformat()
    recorded = now()
    if not as_utc(as_of) <= as_utc(recorded) < as_utc(entry_at):
        return {"status": "withheld", "reason": "새 예측과 근거는 다음 거래일 시작 전에 저장해야 합니다."}
    original = peak_research.prospective_record(prediction, snapshot, bars, calendar, as_of)
    if original["status"] not in {"already_recorded", "prospective_research_forecast"}:
        return {"status": "withheld", "reason": "원래 가격 예측의 사전 기록을 확인하지 못했습니다."}
    base_path = Path(original["path"]) / "forecast.json"
    base_record = json.loads(base_path.read_text(encoding="utf-8"))
    old_plan = base_record["prediction"]["plan"]
    if any(old_plan[key] != plan[key] for key in ("reference_price", "first_target_price", "invalidation_price")):
        return {"status": "withheld", "reason": "기존 사전 예측과 가격 계획이 달라 새 검증 기록으로 섞지 않았습니다."}
    context_path = Path(context_path).resolve()
    if not context_path.is_relative_to(ROOT / "reports/private"):
        raise ValueError("Context journal requires a private evidence artifact")
    context = json.loads(context_path.read_text(encoding="utf-8"))
    if as_utc(context["as_of"]) > as_utc(as_of):
        raise ValueError("Future context cannot be attached to an earlier forecast")
    if screen["reference_session"] != plan["reference_session"]:
        raise ValueError("Price screen and forecast reference sessions differ")
    last = datetime.fromisoformat(plan["timing"]["sessions"][-1]).date()
    eligible = datetime.combine(last + timedelta(days=1), time(0, 2), tzinfo=ZoneInfo("America/New_York"))
    record = {"schema_version": "1.0.0", "status": "prospective_context_record", "record_id": key,
        "symbol": snapshot["symbol"], "is_illustrative": snapshot["is_illustrative"], "recorded_at": recorded,
        "as_of": as_of,
        "reference_session": screen["reference_session"], "entry_at": entry_at,
        "review_at": plan["timing"]["review_at"], "earliest_basic_outcome_check_at": eligible.isoformat(),
        "sessions": plan["timing"]["sessions"], "screen": screen, "decision_scope": "price_cost_screen_only",
        "paper_action": "scenario" if screen["screen_decision"] == "numerical_candidate" else "cash",
        "context_path": str(context_path), "context_sha256": digest(context_path),
        "context_policy_sha256": settings_hash, "screen_policy_sha256": screen_hash,
        "base_forecast_path": str(base_path), "base_forecast_sha256": digest(base_path),
        "source_ids": snapshot["source_ids"], "validation_id": None, "released_score": None}
    save_new(folder / "forecast.json", record)
    return {"status": record["status"], "record_id": key, "path": str(folder.resolve()),
        "entry_at": entry_at, "review_at": record["review_at"], "earliest_basic_outcome_check_at": record["earliest_basic_outcome_check_at"]}


def settle(snapshot, bars, calendar, as_of, ledger_dir=None, *, record_ids=None, base_record_ids=None):
    base_results = (peak_research.settle_records(snapshot, bars, calendar, as_of) if base_record_ids is None else
        peak_research.settle_records(snapshot, bars, calendar, as_of, record_ids=base_record_ids))
    ledger = Path(ledger_dir) if ledger_dir else ROOT / "reports/private/context_ledger"
    results = []
    for path in sorted(ledger.glob("*/forecast.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record_ids is not None and record["record_id"] not in record_ids:
            continue
        if record["symbol"] != snapshot["symbol"] or record["is_illustrative"] != snapshot["is_illustrative"]:
            continue
        outcome = path.with_name("outcome.json")
        if outcome.exists():
            results.append({"record_id": record["record_id"], "status": "already_evaluated"})
            continue
        base_path = Path(record["base_forecast_path"])
        try:
            sources_match = (record["context_policy_sha256"] == digest(ROOT / "config/context_policy.json") and
                record["screen_policy_sha256"] == digest(ROOT / "config/trade_screen_policy.json") and
                digest(record["context_path"]) == record["context_sha256"] and digest(base_path) == record["base_forecast_sha256"])
        except OSError:
            sources_match = False
        if not sources_match:
            results.append({"record_id": record["record_id"], "status": "revision_withheld"})
            continue
        base_outcome = base_path.with_name("outcome.json")
        by_day = {bar.session: bar for bar in bars}
        if any(session not in by_day for session in record["sessions"]) or as_utc(calendar.close(record["sessions"][-1])) > as_utc(as_of):
            results.append({"record_id": record["record_id"], "status": "awaiting_five_session_outcome"})
            continue
        if any(as_utc(by_day[session].available_at) > as_utc(as_of) for session in record["sessions"]):
            results.append({"record_id": record["record_id"], "status": "future_observation_withheld"})
            continue
        if not base_outcome.exists():
            results.append({"record_id": record["record_id"], "status": "awaiting_five_session_outcome"})
            continue
        original = json.loads(base_outcome.read_text(encoding="utf-8"))
        base_record = json.loads(base_path.read_text(encoding="utf-8"))
        if original.get("record_id") != base_record["record_id"]:
            results.append({"record_id": record["record_id"], "status": "outcome_identity_withheld"})
            continue
        if as_utc(original["as_of"]) > as_utc(as_of):
            results.append({"record_id": record["record_id"], "status": "future_outcome_withheld"})
            continue
        if any(not as_utc(record["recorded_at"]) <= as_utc(row["result"]["label_available_at"]) <= as_utc(as_of) for row in original["outcomes"]):
            results.append({"record_id": record["record_id"], "status": "future_observation_withheld"})
            continue
        values = [{"cost_bps_per_side": row["cost_bps_per_side"],
            "result": row["result"] if record["paper_action"] == "scenario" else {"outcome": "cash", "net_return_pct": "0",
                "label_available_at": row["result"]["label_available_at"]}} for row in original["outcomes"]]
        save_new(outcome, {"schema_version": "1.0.0", "status": "observed_context_outcome", "record_id": record["record_id"],
            "as_of": as_of, "evaluated_at": now(), "source_ids": original["source_ids"],
            "base_outcome_sha256": digest(base_outcome), "paper_action": record["paper_action"],
            "outcomes": values, "validation_id": None, "released_score": None})
        results.append({"record_id": record["record_id"], "status": "observed_context_outcome"})
    return {"base_forecasts": base_results, "context_records": results}
