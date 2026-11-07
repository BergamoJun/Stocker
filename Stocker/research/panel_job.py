"""Bounded personal historical-panel worker. No trading, fitting or LLM calls."""

import argparse
from collections import defaultdict
from datetime import timedelta
import json
import os
from pathlib import Path
import re
import subprocess
import time
from zoneinfo import ZoneInfo

from . import historical_panel, panel_prices, panel_report, sector_universe
from .learned_research import latest_training_snapshot
from .local_files import LockBusy, atomic_json, exclusive_lock
from .observe import key_from_user
from .panel_transport import Client, Deferred, SourceUnavailable, catalog_url, detail_url, disk_name, price_url
from .rights import RightsError, as_utc, price_uses, require_entitlement
from .sec_report import ROOT
from .validation import digest, now, stable_hash

TERMINAL = {"complete", "complete_with_gaps", "withheld", "exhausted"}
JOB_FILES = sorted(set(historical_panel.ENGINE_FILES + ["research/panel_job.py", "research/panel_transport.py",
    "research/sector_universe.py", "research/panel_report.py", "research/rights.py", "research/local_files.py", "research/news_transport.py",
    "schemas/historical_panel.schema.json", "scripts/install_panel_task.ps1"]))


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def prepare(*, definition_path=None, entitlement_path=None):
    settings = sector_universe.policy()
    entitlement_path = Path(entitlement_path) if entitlement_path else latest_training_snapshot()[1]
    record = read(entitlement_path)
    require_entitlement(record, price_uses("local_research"), now())
    body = {"version": "1.0.0", "created_at": now(), "settings": settings,
        "code_sha256": {name: digest(ROOT / name) for name in JOB_FILES},
        "entitlement_source_path": str(entitlement_path.resolve()), "entitlement_sha256": digest(entitlement_path),
        "external_llm_calls": 0, "model_training_performed": False, "released_score": None}
    job_id = stable_hash(body)[:16]
    folder = ROOT / "reports/private/historical_panel" / job_id
    definitions = sector_universe.definitions(folder, source_path=definition_path)
    body.update(job_id=job_id, task_name="Stocker-HistoricalPanel-" + job_id,
        definitions_sha256=digest(folder / "industry_definitions.json"), definition_source_sha256=definitions["source_text_sha256"])
    body["integrity_sha256"] = stable_hash(body)
    path = folder / "job.json"
    atomic_json(folder / "price_entitlement.json", record)
    atomic_json(path, body)
    check(path)
    return path


def check(path):
    path = Path(path).resolve()
    if not path.is_relative_to(ROOT / "reports/private/historical_panel") or path.name != "job.json":
        raise ValueError("Private historical-panel job required")
    job = read(path)
    if not re.fullmatch(r"[a-f0-9]{16}", job["job_id"]) or path.parent.name != job["job_id"] or job["task_name"] != "Stocker-HistoricalPanel-" + job["job_id"]:
        raise ValueError("Unexpected panel job identity")
    if stable_hash({key: value for key, value in job.items() if key != "integrity_sha256"}) != job["integrity_sha256"]:
        raise ValueError("Panel job changed")
    if job["settings"] != sector_universe.policy() or job["external_llm_calls"] != 0 or job["model_training_performed"] is not False or job["released_score"] is not None:
        raise ValueError("Panel research boundaries changed")
    if set(job["code_sha256"]) != set(JOB_FILES) or any(digest(ROOT / name) != expected for name, expected in job["code_sha256"].items()):
        raise ValueError("Panel code/protocol changed; prepare a new run instead of rewriting history")
    if digest(path.parent / "price_entitlement.json") != job["entitlement_sha256"] or digest(path.parent / "industry_definitions.json") != job["definitions_sha256"] or digest(path.parent / "Siccodes12.txt") != job["definition_source_sha256"]:
        raise ValueError("Panel source evidence changed")
    return job


def disable_task(name):
    if os.name != "nt" or not re.fullmatch(r"Stocker-HistoricalPanel-[a-f0-9]{16}", name):
        return "not_disabled"
    try:
        result = subprocess.run([str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/schtasks.exe"),
            "/Change", "/TN", name, "/DISABLE"], capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
        return "disabled" if result.returncode == 0 else "disable_failed"
    except (OSError, subprocess.TimeoutExpired):
        return "disable_failed"


def progress(state, folder):
    cohort_path = folder / "cohort.json"
    cohort = read(cohort_path) if cohort_path.exists() else None
    stocks = state.get("stock_results", {})
    return {"schema_version": "1.0.0", "status": state["status"], "stage": state["stage"], "updated_at": now(),
        "started_at": state.get("started_at"), "next_attempt_at": state.get("next_attempt_at"),
        "network_requests": state.get("network_requests", 0), "catalog_pages": state.get("catalog_pages", 0),
        "catalog_eligible_symbols": state.get("catalog_eligible_symbols", 0), "profiles_processed": state.get("profile_index", 0),
        "profiles_verified": state.get("profiles_verified", 0), "profiles_unresolved": state.get("profiles_unresolved", 0),
        "cohort_frozen": cohort is not None, "selected_unique_issuers": len(cohort["selected"]) if cohort else 0,
        "sector_size_requirements_met": sum(row["minimum_met"] for row in cohort["sectors"]) if cohort else 0,
        "sector_count": 12, "price_snapshots_ready": sum(row["status"] == "ready" for row in stocks.values()),
        "price_snapshots_unresolved": sum(row["status"] == "unresolved" for row in stocks.values()),
        "symbols_evaluated": len(state.get("evaluated", [])), "completed_cases": state.get("completed_cases", 0),
        "unavailable_cases": state.get("unavailable_cases", 0),
        "unseen_symbols_evaluated": 0, "final_cases_executed": 0, "final_evaluation_locked": True,
        "model_training_performed": False, "external_llm_calls": 0, "monthly_data_budget_usd": 0,
        "released_score": None, "performance_validated": False, "reason_code": state.get("reason_code"),
        "sectors": cohort["sectors"] if cohort else [],
        "limitations": ["Current-vintage price and SIC metadata; not certified original historical vintages",
            "100 distinct issuers per industry is a collection target, not a completed evaluation",
            "Later delistings/missing prices remain in the frozen denominator without replacement",
            "Fixed equations only; no model fitting, news backfill or trading"]}


def write_progress(state, folder):
    value = progress(state, folder)
    import jsonschema
    schema = read(ROOT / "schemas/historical_panel.schema.json")
    jsonschema.Draft202012Validator({"$ref": "#/$defs/progress", "$defs": schema["$defs"]}).validate(value)
    atomic_json(folder / "progress.json", value, replace=True)
    labels = {"catalog": "과거 기준일의 상장 종목 명부 수집", "profiles": "기업 식별과 산업군 확인",
        "cohort": "성과를 보기 전 평가 대상 기업 고정", "prices": "과거 일별 가격과 마지막 거래일의 기업 식별 확인",
        "evaluation": "기존 수식으로 개발·중간 확인 구간 평가", "summary": "산업·기간별 결과 집계", "done": "수집·평가 작업 종료"}
    stamp = as_utc(value["updated_at"]).astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST")
    lines = ["# 여러 산업군의 과거 가격 평가 진행 상황", "", f"갱신: {stamp}", "",
        "검사 질문: 기존 계산식이 한 종목에만 맞는지, 여러 기업에서도 고점·도달 시기·비용 후 수익을 설명하는지 확인합니다.", "",
        f"현재 단계: {labels.get(value['stage'], value['stage'])}", f"실행 상태: {value['status']}", "",
        f"- 과거 명부에서 지원 범위에 해당한 종목: {value['catalog_eligible_symbols']}개",
        f"- 기업·산업군 확인 처리: {value['profiles_processed']}개 (확인 {value['profiles_verified']}, 미확인 {value['profiles_unresolved']})",
        f"- 평가 대상 고정: {'완료' if value['cohort_frozen'] else '아직 진행 전'}; 고정 기업 {value['selected_unique_issuers']}개",
        f"- 서로 다른 기업 100개 확보 조건: 12개 산업군 중 {value['sector_size_requirements_met']}개 충족",
        f"- 가격·식별 확인 완료: {value['price_snapshots_ready']}개; 미확인 {value['price_snapshots_unresolved']}개",
        f"- 평가 완료 기업: {value['symbols_evaluated']}개; 처리한 예측 기준일: {value['completed_cases']}건",
        f"- 실제 API 요청: {value['network_requests']}회; 외부 언어모델 호출: 0회", "",
        "계산 실패와 자료 부족은 0% 수익으로 바꾸지 않습니다. 아직 보지 않은 기업과 마지막 기간의 정답은 열지 않습니다.",
        "기업 100개 확보, 프로그램 검사 통과, 예측 수익성 입증은 각각 다른 조건입니다. 현재 투자 점수 공개와 모델 학습은 꺼져 있습니다.", "",
        "실행에는 PC와 해당 Windows 계정의 로그인이 필요합니다. 중간에 꺼져도 다음 실행에서 저장한 위치부터 재개합니다.",
        "무료 API 제한은 캐시·최소 15초 간격·429 응답의 대기시간으로 지킵니다. 총 요청 16,000회 또는 첫 실행 후 96시간에서 멈춥니다.", ""]
    if value["reason_code"]:
        lines += [f"중단·대기 이유: {value['reason_code']}", ""]
    if value["sectors"]:
        lines += ["| 산업군 | 확인 기업 | 고정 기업 | 마지막 시험 전용 기업 | 100개 조건 |", "|---|---:|---:|---:|---|"]
        lines += [f"| {row['name_ko']} | {row['available_unique_issuers']} | {row['selected_unique_issuers']} | {row['heldout_symbols']} | {'충족' if row['minimum_met'] else '부족'} |" for row in value["sectors"]]
    (folder / "STATUS.ko.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return value


def finish_summary(folder, cohort, state):
    by_sector = defaultdict(list)
    # Load only compact outcome fields for aggregation, never all simulated paths.
    for profile in cohort["selected"]:
        if profile["symbol"] not in state.get("evaluated", []):
            continue
        for path in (folder / "evaluation" / disk_name(profile["symbol"]) / "cases").glob("*.json"):
            row = read(path)
            row.pop("forecast", None)
            by_sector[profile["sector_id"]].append(row)
    results = []
    for sector in cohort["sectors"]:
        rows = by_sector[sector["sector_id"]]
        group = {**sector, "development_and_validation": historical_panel.summarize(rows), "breakdowns": {}}
        for key in ("phase", "reference_month", "volatility_group", "liquidity_group"):
            values = defaultdict(list)
            for row in rows:
                values[row.get(key, "unavailable")].append(row)
            group["breakdowns"][key] = {name: historical_panel.summarize(items) for name, items in sorted(values.items())}
        results.append(group)
    output = {"schema_version": "1.0.0", "status": "retrospective_panel_not_validation", "as_of": now(),
        "cohort_sha256": digest(folder / "cohort.json"), "selected_unique_issuers": len(cohort["selected"]),
        "symbols_evaluated": len(state.get("evaluated", [])), "completed_cases": state.get("completed_cases", 0),
        "sectors": results, "collection_failures": state.get("stock_results", {}),
        "final_evaluation_locked": True, "final_cases_executed": 0, "model_training_performed": False,
        "released_score": None, "performance_validated": False,
        "uncertainty": "Not yet estimated with dependent calendar blocks; no statistical significance/independence claim",
        "historical_original_vintage_verified": False}
    import jsonschema
    schema = read(ROOT / "schemas/historical_panel.schema.json")
    jsonschema.Draft202012Validator({"$ref": "#/$defs/panel_result", "$defs": schema["$defs"]}).validate(output)
    atomic_json(folder / "panel_result.json", output)
    (folder / "REPORT.ko.md").write_text(panel_report.markdown(output), encoding="utf-8")
    return output


def step(job, state, folder, client, finish_before):
    settings = job["settings"]
    definitions = read(folder / "industry_definitions.json")["groups"]
    stage = state["stage"]
    if stage == "catalog":
        if state.get("catalog_pages", 0) >= settings["maximum_pages"]:
            raise SourceUnavailable("CATALOG_PAGE_LIMIT")
        url = state.get("next_catalog_url") or catalog_url(settings["universe_date"])
        evidence = client.get(url)
        rows, excluded, following = sector_universe.catalog_page(json.loads(evidence["raw"]))
        catalog_path = folder / "catalog.json"
        saved = read(catalog_path) if catalog_path.exists() else {"rows": [], "excluded": [], "pages": []}
        if url not in [page["source_url"] for page in saved["pages"]]:
            if {row["ticker"] for row in rows} & {row["ticker"] for row in saved["rows"]}:
                raise ValueError("DUPLICATE_CATALOG_IDENTITY")
            saved["rows"].extend(rows)
            saved["excluded"].extend(excluded)
            saved["pages"].append({key: evidence[key] for key in ("source_url", "raw_sha256", "retrieved_at")})
            if len(saved["rows"]) > settings["maximum_catalog_symbols"]:
                raise SourceUnavailable("CATALOG_SYMBOL_LIMIT")
            atomic_json(catalog_path, saved, replace=True)
        state.update(catalog_pages=len(saved["pages"]), catalog_eligible_symbols=len(saved["rows"]), next_catalog_url=following)
        if not following:
            state["stage"] = "profiles"
    elif stage == "profiles":
        catalog = read(folder / "catalog.json")["rows"]
        index = state.get("profile_index", 0)
        if index >= len(catalog):
            state["stage"] = "cohort"
            return
        item = catalog[index]
        target = folder / "profiles" / (disk_name(item["ticker"]) + ".json")
        if target.exists():
            result = read(target)
        else:
            try:
                evidence = client.get(detail_url(item["ticker"], settings["universe_date"]))
                approved = sector_universe.profile(json.loads(evidence["raw"]), item, definitions, evidence)
                result = {"status": "verified", "profile": approved}
            except RightsError:
                raise
            except ValueError:
                result = {"status": "unresolved", "symbol": item["ticker"], "reason_code": "HISTORICAL_PROFILE_NOT_RESOLVED"}
            except SourceUnavailable as error:
                if error.code != "HTTP_404":
                    raise
                result = {"status": "unresolved", "symbol": item["ticker"], "reason_code": error.code}
            atomic_json(target, result)
        state["profile_index"] = index + 1
        key = "profiles_verified" if result["status"] == "verified" else "profiles_unresolved"
        state[key] = state.get(key, 0) + 1
    elif stage == "cohort":
        target = folder / "cohort.json"
        if not target.exists():
            profiles = [read(path)["profile"] for path in sorted((folder / "profiles").glob("*.json")) if read(path)["status"] == "verified"]
            cohort = sector_universe.freeze(profiles, definitions, discovery_complete=True)
            cohort.update(catalog_sha256=digest(folder / "catalog.json"), unresolved_catalog_profiles=state.get("profiles_unresolved", 0),
                sector_membership_fully_resolved=state.get("profiles_unresolved", 0) == 0)
            atomic_json(target, cohort)
        state["cohort_sha256"] = digest(target)
        state["stage"] = "prices"
    elif stage == "prices":
        selected = read(folder / "cohort.json")["selected"]
        index = state.get("price_index", 0)
        if index >= len(selected):
            state["stage"] = "evaluation"
            return
        profile = selected[index]
        price_folder = folder / "prices" / disk_name(profile["symbol"])
        path = price_folder / "prices.json"
        record = read(folder / "price_entitlement.json")
        if not path.exists():
            protocol = read(ROOT / "config/historical_evaluation_protocol.json")["initial_window"]
            evidence = client.get(price_url(profile["symbol"], protocol["start"], protocol["end"]))
            panel_prices.save(price_folder, evidence["raw"], profile, record, evidence)
        data = read(path)
        last = data["bars"][-1]["session"]
        identity_path = price_folder / "last_identity.json"
        if not identity_path.exists():
            evidence = client.get(detail_url(profile["symbol"], last))
            identity = sector_universe.verify_last_identity(json.loads(evidence["raw"]), profile, evidence, last)
            atomic_json(identity_path, identity)
        panel_prices.load(path, profile, record, now())
        state.setdefault("stock_results", {})[profile["symbol"]] = {"status": "ready", "bars": len(data["bars"]),
            "last_session": last, "symbol_partition": profile["symbol_partition"]}
        state["price_index"] = index + 1
    elif stage == "evaluation":
        cohort = read(folder / "cohort.json")
        selected = [row for row in cohort["selected"] if row["symbol_partition"] == "development_and_validation"]
        index = state.get("evaluation_index", 0)
        if index >= len(selected):
            state["stage"] = "summary"
            return
        profile = selected[index]
        if state.get("stock_results", {}).get(profile["symbol"], {}).get("status") != "ready":
            state["evaluation_index"] = index + 1
            return
        result = historical_panel.evaluate_stock(folder / "prices" / disk_name(profile["symbol"]) / "prices.json", profile,
            read(folder / "price_entitlement.json"), folder / "evaluation" / disk_name(profile["symbol"]), finish_before=finish_before)
        if result["complete"]:
            state.setdefault("evaluated", []).append(profile["symbol"])
            state["completed_cases"] = state.get("completed_cases", 0) + result["completed_cases"]
            state["unavailable_cases"] = state.get("unavailable_cases", 0) + sum(part["unavailable_cases"] for part in result["partitions"].values())
            state["evaluation_index"] = index + 1
        else:
            raise Deferred((as_utc(now()) + timedelta(seconds=60)).isoformat())
    elif stage == "summary":
        cohort = read(folder / "cohort.json")
        if not (folder / "panel_result.json").exists():
            finish_summary(folder, cohort, state)
        gaps = state.get("profiles_unresolved", 0) or state.get("unavailable_cases", 0) or not cohort["all_sector_size_requirements_met"] or any(row["status"] != "ready" for row in state.get("stock_results", {}).values())
        state.update(stage="done", status="complete_with_gaps" if gaps else "complete")
    else:
        raise ValueError("UNKNOWN_PANEL_STAGE")


def run(path, *, clock=now, client_factory=Client, disable=disable_task, invocation_seconds=None):
    path = Path(path).resolve()
    folder = path.parent
    try:
        job = check(path)
    except (ValueError, KeyError, OSError):
        # Never rewrite a previous completed result if its current code check fails.
        value = {"status": "withheld", "reason_code": "JOB_INTEGRITY_CHECK_FAILED", "external_llm_calls": 0}
        if path.is_relative_to(ROOT / "reports/private/historical_panel") and re.fullmatch(r"[a-f0-9]{16}", folder.name):
            value["task_disable_result"] = disable("Stocker-HistoricalPanel-" + folder.name)
            value["updated_at"] = clock()
            atomic_json(folder / "integrity_failure.json", value, replace=True)
        return value
    try:
        with exclusive_lock(folder / "worker.lock"):
            state_path = folder / "state.json"
            state = read(state_path) if state_path.exists() else {"status": "running", "stage": "catalog", "network_requests": 0, "started_at": clock()}
            def persist():
                atomic_json(state_path, state, replace=True)
                return write_progress(state, folder)
            if state["status"] in TERMINAL:
                state["task_disable_result"] = disable(job["task_name"])
                return persist()
            if state.get("next_attempt_at") and as_utc(clock()) < as_utc(state["next_attempt_at"]):
                return persist()
            settings = job["settings"]
            deadline = as_utc(state["started_at"]) + timedelta(hours=settings["maximum_job_elapsed_hours"])
            if invocation_seconds is not None and (type(invocation_seconds) is not int or invocation_seconds < 1):
                raise ValueError("Positive invocation limit required")
            max_seconds = min(invocation_seconds or settings["maximum_invocation_seconds"], settings["maximum_invocation_seconds"])
            finish_before = time.monotonic() + max_seconds
            symbols_before = len(state.get("evaluated", []))
            def attempt():
                if as_utc(clock()) >= deadline or state["network_requests"] >= settings["maximum_job_network_requests"]:
                    raise SourceUnavailable("JOB_BUDGET_EXHAUSTED")
                if time.monotonic() >= finish_before:
                    raise Deferred((as_utc(clock()) + timedelta(seconds=60)).isoformat())
                state["network_requests"] += 1
                atomic_json(state_path, state, replace=True)
            try:
                record = read(folder / "price_entitlement.json")
                require_entitlement(record, price_uses("local_research"), clock())
                client = client_factory(record, key_from_user(), clock=clock, on_attempt=attempt)
                state.update(status="running", next_attempt_at=None, reason_code=None)
                persist()
                while state["status"] not in TERMINAL and time.monotonic() < finish_before:
                    if as_utc(clock()) >= deadline:
                        state.update(status="exhausted", reason_code="JOB_ELAPSED_LIMIT")
                        break
                    try:
                        if state["stage"] in {"prices", "evaluation", "summary"} and digest(folder / "cohort.json") != state.get("cohort_sha256"):
                            state.update(status="withheld", reason_code="FROZEN_COHORT_CHANGED")
                            break
                        step(job, state, folder, client, finish_before)
                    except RightsError:
                        raise
                    except SourceUnavailable as error:
                        if error.code in {"HTTP_401", "HTTP_403", "LOCAL_KEY_MISSING", "CATALOG_PAGE_LIMIT", "CATALOG_SYMBOL_LIMIT", "JOB_BUDGET_EXHAUSTED", "OVERSIZED_OR_CREDENTIAL_BEARING_BODY"}:
                            state.update(status="exhausted" if error.code == "JOB_BUDGET_EXHAUSTED" else "withheld", reason_code=error.code)
                            break
                        token = state["stage"] + ":" + str(state.get("profile_index" if state["stage"] == "profiles" else "price_index", 0))
                        failures = state.setdefault("failures", {})
                        failures[token] = failures.get(token, 0) + 1
                        if failures[token] < settings["maximum_step_failures_per_symbol"] and error.code != "HTTP_404":
                            state.update(status="waiting", reason_code=error.code, next_attempt_at=(as_utc(clock()) + timedelta(minutes=5)).isoformat())
                            break
                        if state["stage"] == "profiles":
                            item = read(folder / "catalog.json")["rows"][state.get("profile_index", 0)]
                            atomic_json(folder / "profiles" / (disk_name(item["ticker"]) + ".json"), {"status": "unresolved", "symbol": item["ticker"], "reason_code": error.code})
                            state["profile_index"] = state.get("profile_index", 0) + 1
                            state["profiles_unresolved"] = state.get("profiles_unresolved", 0) + 1
                        elif state["stage"] == "prices":
                            item = read(folder / "cohort.json")["selected"][state.get("price_index", 0)]
                            state.setdefault("stock_results", {})[item["symbol"]] = {"status": "unresolved", "reason_code": error.code}
                            state["price_index"] = state.get("price_index", 0) + 1
                        else:
                            state.update(status="withheld", reason_code=error.code)
                    except ValueError:
                        if state["stage"] != "prices":
                            state.update(status="withheld", reason_code="STEP_IDENTITY_OR_INPUT_CHECK_FAILED")
                            break
                        item = read(folder / "cohort.json")["selected"][state.get("price_index", 0)]
                        state.setdefault("stock_results", {})[item["symbol"]] = {"status": "unresolved", "reason_code": "PRICE_OR_LAST_IDENTITY_CHECK_FAILED"}
                        state["price_index"] = state.get("price_index", 0) + 1
                    persist()
                    if len(state.get("evaluated", [])) - symbols_before >= settings["maximum_symbols_evaluated_per_invocation"]:
                        break
            except Deferred as deferred:
                state.update(status="waiting", reason_code="RATE_OR_INVOCATION_DEFERRED", next_attempt_at=deferred.due_at)
            except RightsError:
                state.update(status="withheld", reason_code="SOURCE_RIGHTS_NOT_CLEARED")
            except (OSError, ValueError, KeyError, TypeError, ArithmeticError):
                state.update(status="withheld", reason_code="WORKER_INPUT_OR_STORAGE_CHECK_FAILED")
            if state["status"] == "running":
                state.update(status="waiting", next_attempt_at=(as_utc(clock()) + timedelta(seconds=60)).isoformat())
            if state["status"] in TERMINAL:
                state["task_disable_result"] = disable(job["task_name"])
            return persist()
    except LockBusy:
        return {"status": "already_running", "external_llm_calls": 0}


def safe_status(value):
    keys = ("status", "stage", "network_requests", "catalog_pages", "catalog_eligible_symbols", "profiles_processed",
        "profiles_verified", "profiles_unresolved", "cohort_frozen", "selected_unique_issuers", "symbols_evaluated",
        "completed_cases", "final_cases_executed", "model_training_performed", "external_llm_calls", "reason_code")
    return {key: value[key] for key in keys if key in value}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--definition-source", type=Path)
    parser.add_argument("--job", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--invocation-seconds", type=int)
    args = parser.parse_args(argv)
    if args.prepare:
        path = prepare(definition_path=args.definition_source)
        print(json.dumps({"status": "prepared_not_yet_started", "job_file": str(path.resolve())}))
    elif args.job and args.check:
        check(args.job)
        print(json.dumps({"status": "job_integrity_valid", "final_evaluation_locked": True}))
    elif args.job and args.status:
        failure = args.job.parent / "integrity_failure.json"
        print(json.dumps(safe_status(read(failure if failure.exists() else args.job.parent / "progress.json"))))
    elif args.job:
        value = run(args.job, invocation_seconds=args.invocation_seconds)
        print(json.dumps(safe_status(value)))
        return 1 if value["status"] in {"withheld", "exhausted"} else 0
    else:
        parser.error("Use --prepare or --job")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
