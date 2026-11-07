"""Bounded, idempotent observation of already recorded BE forecasts."""

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
from zoneinfo import ZoneInfo

import jsonschema

from . import context_journal, fetch_prices, outcome_report, peak_research
from .advance import verify_saved_raw
from .learned_research import latest_training_snapshot
from .local_files import LockBusy, atomic_json, exclusive_lock
from .rights import as_utc, price_uses, require_entitlement
from .sec_report import ROOT
from .swing_models import load_diagnostic_snapshot, load_price_snapshot
from .validation import digest, now, save_new, settle_forecasts, stable_hash

CORE_FILES = ("observe.py", "outcome_report.py", "local_files.py", "advance.py", "fetch_prices.py", "rights.py", "swing_models.py", "peak_model.py", "peak_research.py", "context_journal.py", "validation.py")
POLICY_FILES = ("observation_job_policy.json", "peak_model_policy.json", "swing_candidate_policy.json", "validation_protocol.json", "context_policy.json", "trade_screen_policy.json")
TERMINAL = {"complete", "withheld", "exhausted"}


class RetryLater(RuntimeError):
    pass


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def policy():
    value = read(ROOT / "config/observation_job_policy.json")
    if value["monthly_data_budget_usd"] != 0 or value["release_enabled"] is not False or value["external_llm_calls"] != 0:
        raise ValueError("Observation must remain zero-budget private research")
    return value


def key_from_user():
    key = os.environ.get("MASSIVE_API_KEY", "")
    if not key and os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as handle:
                key = winreg.QueryValueEx(handle, "MASSIVE_API_KEY")[0]
        except OSError:
            key = ""
    return key if isinstance(key, str) else ""


def prepare_job():
    candidates = []
    for path in (ROOT / "reports/private/context_ledger").glob("*/forecast.json"):
        value = read(path)
        if value["symbol"] == "BE" and value["is_illustrative"] is False and not path.with_name("outcome.json").exists():
            candidates.append((value["recorded_at"], path, value))
    if not candidates:
        raise ValueError("No unresolved real pre-entry BE context forecast")
    _, context_path, context = min(candidates, key=lambda row: row[0])
    base_path = Path(context["base_forecast_path"])
    base = read(base_path)
    targets = [{"kind": "context", "record_id": context["record_id"], "path": str(context_path.resolve()), "sha256": digest(context_path)},
        {"kind": "peak", "record_id": base["record_id"], "path": str(base_path.resolve()), "sha256": digest(base_path)}]
    for path in (ROOT / "reports/private/prospective_ledger").glob("*/forecast.json"):
        value = read(path)
        if value["symbol"] == "BE" and value["is_illustrative"] is False and value["candidate"]["timing"]["sessions"] == context["sessions"]:
            targets.append({"kind": "legacy", "record_id": value["record_id"], "path": str(path.resolve()), "sha256": digest(path)})
    _, entitlement = latest_training_snapshot()
    require_entitlement(read(entitlement), price_uses("local_research"), now())
    settings = policy()
    key = stable_hash({"context_record": context["record_id"], "policy": digest(ROOT / "config/observation_job_policy.json")})[:16]
    folder = ROOT / "reports/private/observation_jobs" / key
    path = folder / "job.json"
    if path.exists():
        check_job(path)
        return path
    job = {"version": "1.0.0", "job_id": key, "task_name": "Stocker-BE-Outcome-" + key, "created_at": now(),
        "symbol": "BE", "is_illustrative": False, "due_at": context["earliest_basic_outcome_check_at"],
        "sessions": context["sessions"], "targets": targets, "settings": settings,
        "entitlement_path": str(entitlement.resolve()), "entitlement_sha256": digest(entitlement),
        "policy_sha256": {name: digest(ROOT / "config" / name) for name in POLICY_FILES},
        "code_sha256": {name: digest(ROOT / "research" / name) for name in CORE_FILES},
        "schema_sha256": digest(ROOT / "schemas/observation_job.schema.json"),
        "released_score": None, "validation_id": None, "external_llm_calls": 0}
    save_new(path, job)
    check_job(path)
    return path


def check_job(path):
    path = Path(path).resolve()
    if not path.is_relative_to(ROOT / "reports/private/observation_jobs"):
        raise ValueError("Job must stay in the private observation directory")
    job = read(path)
    schema = read(ROOT / "schemas/observation_job.schema.json")
    jsonschema.Draft202012Validator({"$ref": "#/$defs/job", "$defs": schema["$defs"]}, format_checker=jsonschema.FormatChecker()).validate(job)
    if job["schema_sha256"] != digest(ROOT / "schemas/observation_job.schema.json"):
        raise ValueError("Observation contract changed")
    if not re.fullmatch(r"[a-f0-9]{16}", job["job_id"]) or job["task_name"] != "Stocker-BE-Outcome-" + job["job_id"]:
        raise ValueError("Unknown job/task identity")
    if path.name != "job.json" or path.parent.name != job["job_id"]:
        raise ValueError("Job path differs from its identity")
    if job["symbol"] != "BE" or job["is_illustrative"] is not False or len(job["sessions"]) != 5:
        raise ValueError("Real BE five-session job required")
    if job["settings"] != policy():
        raise ValueError("Observation settings changed")
    if set(job["code_sha256"]) != set(CORE_FILES) or set(job["policy_sha256"]) != set(POLICY_FILES):
        raise ValueError("Registered calculation/policy files are incomplete")
    if [row["kind"] for row in job["targets"]].count("context") != 1 or [row["kind"] for row in job["targets"]].count("peak") != 1:
        raise ValueError("Exactly one original context and peak forecast are required")
    for group, folder in (("policy_sha256", "config"), ("code_sha256", "research")):
        for name, value in job[group].items():
            if digest(ROOT / folder / name) != value:
                raise ValueError("Registered policy or calculation code changed")
    for target in job["targets"]:
        target_path = Path(target["path"]).resolve()
        if not target_path.is_relative_to(ROOT / "reports/private") or digest(target_path) != target["sha256"]:
            raise ValueError("Original forecast differs")
        record = read(target_path)
        if record["record_id"] != target["record_id"] or record["symbol"] != "BE" or record["is_illustrative"] is not False:
            raise ValueError("Forecast identity differs")
        expected_ledger = {"context": "context_ledger", "peak": "peak_ledger", "legacy": "prospective_ledger"}[target["kind"]]
        if target_path != ROOT / "reports/private" / expected_ledger / target["record_id"] / "forecast.json":
            raise ValueError("Forecast ledger path differs")
        plan = record["prediction"]["plan"] if target["kind"] == "peak" else record.get("candidate")
        sessions = plan["timing"]["sessions"] if plan else record["sessions"]
        if sessions != job["sessions"]:
            raise ValueError("Forecast observation sessions differ")
        if target["kind"] == "context" and job["due_at"] != record["earliest_basic_outcome_check_at"]:
            raise ValueError("Observation release time differs from the recorded plan")
    if digest(job["entitlement_path"]) != job["entitlement_sha256"]:
        raise ValueError("Saved source entitlement changed")
    return job


def state_markdown(state):
    labels = {"planned": "예약 준비", "not_due": "예약 시각 전 대기", "retry_wait": "자료 제공 또는 다음 재시도 대기",
        "complete": "관찰 결과 저장 완료", "withheld": "입력·사용권·규칙 검사를 통과하지 못해 보류", "exhausted": "정해 둔 재시도 한도 도달"}
    due = state.get("due_at")
    due_ko = as_utc(due).astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M") if due else "등록 파일 확인 필요"
    lines = ["# BE 자동 관찰 작업 상태", "", labels.get(state["status"], "실행 상태 확인 필요"), "",
        f"첫 조회 예정: {due_ko} (한국 시간)", f"이 작업에서 시도한 가격 API 요청: {state['network_attempts']}회.",
        "자료가 없으면 이익·손실을 0으로 채우지 않습니다. 실제 5거래일 가격이 모여야 결과를 비교합니다.",
        "오류 시 15분 간격으로 최대 24시간·97회까지 재시도합니다. 원본·사용권·규칙 오류는 자동 판정을 멈춥니다."]
    if state.get("result_dir"):
        lines += ["", "[관찰 결과 보고서](result/report.md)", "첫 한 건의 관찰 결과이며 실제 계좌 수익이나 모델 검증 완료를 뜻하지 않습니다."]
    return "\n".join(lines) + "\n"


def disable_task(name):
    if os.name != "nt" or not re.fullmatch(r"Stocker-BE-Outcome-[a-f0-9]{16}", name):
        return "not_disabled"
    try:
        result = subprocess.run([str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/schtasks.exe"), "/Change", "/TN", name, "/DISABLE"],
            capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
        return "disabled" if result.returncode == 0 else "disable_failed"
    except (OSError, subprocess.TimeoutExpired):
        return "disable_failed"


def snapshot_for_job(record, cutoff, state, persist, key):
    path, _ = latest_training_snapshot()
    verify_saved_raw(path, record)
    data, bars, calendar = load_diagnostic_snapshot(path, record, cutoff)
    if bars[-1].session == calendar.last_completed(cutoff):
        state.update(price_snapshot_path=str(path.resolve()), price_snapshot_sha256=digest(path))
        return data, bars, calendar
    if not key:
        raise ValueError("Missing local account key")
    state["network_attempts"] += 1
    state["last_network_at"] = cutoff
    persist()
    start, end, calendar = fetch_prices.basic_window(cutoff)
    try:
        raw = fetch_prices.download(start, end, key)
    except fetch_prices.PriceFetchError as error:
        if any(code in str(error) for code in ("HTTP 401", "HTTP 403")):
            raise ValueError("Account authorization unavailable") from None
        raise RetryLater("PRICE_TRANSPORT_UNAVAILABLE") from None
    retrieved = now()
    data = fetch_prices.normalize(raw, record, retrieved, start, end, calendar)
    folder = ROOT / "reports/private/market_data" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "raw_response.json").write_bytes(raw)
    save_new(folder / "prices.json", data)
    save_new(folder / "price_entitlement.json", record)
    data, bars, calendar = load_diagnostic_snapshot(folder / "prices.json", record, retrieved)
    state.update(price_snapshot_path=str((folder / "prices.json").resolve()), price_snapshot_sha256=digest(folder / "prices.json"))
    if bars[-1].session != calendar.last_completed(retrieved):
        raise RetryLater("PRICE_SESSION_NOT_COMPLETE")
    return data, bars, calendar


def settle_targets(job, data, bars, calendar, cutoff):
    by_kind = {kind: {row["record_id"] for row in job["targets"] if row["kind"] == kind} for kind in ("legacy", "peak", "context")}
    legacy = settle_forecasts(data, bars, calendar, cutoff, ROOT / "reports/private/prospective_ledger", record_ids=by_kind["legacy"])
    result = context_journal.settle(data, bars, calendar, cutoff, record_ids=by_kind["context"], base_record_ids=by_kind["peak"])
    rows = legacy + result["base_forecasts"] + result["context_records"]
    if any("awaiting" in row["status"] for row in rows):
        raise RetryLater("FIVE_SESSION_OUTCOMES_NOT_AVAILABLE")
    if any("withheld" in row["status"] or "mismatch" in row["status"] or "inconsistent" in row["status"] for row in rows):
        raise ValueError("Original policy, price or observation consistency check failed")
    for target in job["targets"]:
        outcome = read(Path(target["path"]).with_name("outcome.json"))
        if outcome["record_id"] != target["record_id"] or as_utc(outcome["as_of"]) > as_utc(cutoff):
            raise ValueError("Outcome identity or observation time is invalid")
    return rows


def run(path, *, clock=now, disable=disable_task):
    path = Path(path).resolve()
    if not path.is_relative_to(ROOT / "reports/private/observation_jobs"):
        raise ValueError("Private job path required")
    folder = path.parent
    try:
        with exclusive_lock(folder / "worker.lock"):
            cutoff = clock()
            state_path = folder / "state.json"
            state = read(state_path) if state_path.exists() else {"status": "planned", "network_attempts": 0, "first_due_execution_at": None, "last_network_at": None}
            state["checked_at"] = cutoff
            def persist():
                atomic_json(state_path, state, replace=True)
                (folder / "status.md").write_text(state_markdown(state), encoding="utf-8")
            try:
                if state["status"] in TERMINAL:
                    if state["status"] == "complete" and (not state.get("result_sha256") or digest(Path(state["result_dir"]) / "observation.json") != state["result_sha256"]):
                        raise ValueError("Completed result is missing or changed")
                    state["task_control"] = disable("Stocker-BE-Outcome-" + folder.name)
                    persist()
                    return state
                job = check_job(path)
                state["due_at"] = job["due_at"]
                if as_utc(cutoff) < as_utc(job["due_at"]):
                    state.update(status="not_due", due_at=job["due_at"], api_key_present=bool(key_from_user()))
                    persist()
                    return state
                settings = job["settings"]
                if state["first_due_execution_at"] is None:
                    state["first_due_execution_at"] = cutoff
                elapsed = as_utc(cutoff) - as_utc(state["first_due_execution_at"])
                if state["network_attempts"] >= settings["maximum_network_attempts"] or elapsed >= timedelta(hours=settings["retry_window_hours_from_first_due_execution"]):
                    state.update(status="exhausted", reason_code="BOUNDED_RETRY_WINDOW_EXHAUSTED", task_control=disable(job["task_name"]))
                    persist()
                    return state
                if state["last_network_at"] and as_utc(cutoff) < as_utc(state["last_network_at"]) + timedelta(minutes=settings["retry_interval_minutes"]):
                    state["status"] = "retry_wait"
                    persist()
                    return state
                record = read(job["entitlement_path"])
                require_entitlement(record, price_uses("local_research"), cutoff)
                result_dir = folder / "result"
                completed = result_dir / "observation.json"
                if completed.exists():
                    result = read(completed)
                    if result["job_id"] != job["job_id"] or result["is_illustrative"] is not False:
                        raise ValueError("Completed report identity differs")
                    outcome_report.write(result_dir, result)
                    state.update(status="complete", completed_at=cutoff, result_dir=str(result_dir.resolve()), result_sha256=digest(completed), task_control=disable(job["task_name"]))
                    persist()
                    return state
                data, bars, calendar = snapshot_for_job(record, cutoff, state, persist, key_from_user())
                cutoff = clock()
                if any(as_utc(row.available_at) > as_utc(cutoff) for row in bars):
                    raise ValueError("Outcome data was not observed by this cutoff")
                rows = settle_targets(job, data, bars, calendar, cutoff)
                if not (result_dir / "observation.json").exists():
                    outcome_report.write(result_dir, outcome_report.build(job, data, bars, cutoff))
                state.update(status="complete", completed_at=clock(), result_dir=str(result_dir.resolve()),
                    result_sha256=digest(result_dir / "observation.json"),
                    settlement_states=rows, task_control=disable(job["task_name"]))
            except RetryLater as error:
                state.update(status="retry_wait", reason_code=str(error))
            except (ValueError, OSError, KeyError, TypeError, IndexError, jsonschema.ValidationError):
                state.update(status="withheld", reason_code="INPUT_POLICY_LICENSE_OR_RESULT_CHECK_FAILED")
                name = "Stocker-BE-Outcome-" + folder.name
                state["task_control"] = disable(name)
            persist()
            atomic_json(folder / "runs" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json"), state)
            return state
    except LockBusy:
        return {"status": "already_running", "network_attempts": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Prepare or run one bounded observation of existing BE forecasts")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--job", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.prepare:
        path = prepare_job()
        job = read(path)
        result = {"status": "prepared", "job_path": str(path.resolve()), "task_name": job["task_name"], "due_at": job["due_at"]}
    elif args.job and args.check:
        job = check_job(args.job)
        result = {"status": "validated", "job_id": job["job_id"], "due_at": job["due_at"], "api_key_present": bool(key_from_user())}
    elif args.job:
        result = run(args.job)
        result = {key: result[key] for key in ("status", "network_attempts", "due_at", "reason_code", "result_dir") if key in result}
    else:
        parser.error("Use --prepare, or --job with optional --check")
    if __import__("sys").stdout is not None:
        print(json.dumps(result))
    return 0 if result["status"] not in {"withheld", "exhausted"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
