"""Resumable multi-issuer SEC collection; separate from frozen price research."""

import argparse
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import time
import uuid

from .local_files import LockBusy, atomic_json, exclusive_lock
from .rights import as_utc
from .sec_report import ROOT
from .sec_transport import Client, Deferred, Unavailable, endpoint, now, policy, user_agent, valid_user_agent
from . import sec_facts
from .validation import digest


REFERENCE = "https://www.sec.gov/files/company_tickers_exchange.json"
JOB_FILES = (
    "research/sec_refresh.py", "research/sec_transport.py", "research/sec_facts.py", "research/sec_refresh_report.py",
    "config/sec_refresh_policy.json", "config/sec_fact_policy.json", "schemas/sec_snapshot.schema.json",
    "scripts/install_sec_refresh_task.ps1", "research/local_files.py", "research/fetch_prices.py", "research/news_transport.py",
    "research/sec_report.py", "research/rights.py", "research/validation.py",
)


def read(path, default=None):
    return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else default


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def source(result):
    return {k: v for k, v in result.items() if k not in {"raw", "cache_hit", "network_checked"}}


def source_id(result):
    return "sec:" + canonical_hash({"url": result["source_url"], "sha256": result["raw_sha256"]})


def validate_snapshot(value):
    from jsonschema import Draft202012Validator
    Draft202012Validator(read(ROOT / "schemas/sec_snapshot.schema.json")).validate(value)
    known = set(value["sources"])
    if not known or any(s["is_illustrative"] is not value["is_illustrative"] for s in value["sources"].values()):
        raise ValueError("SEC_SNAPSHOT_SOURCE_NAMESPACE_MISMATCH")
    if set(value["financials"]["source_ids"]) - known:
        raise ValueError("SEC_FINANCIAL_SOURCE_MISSING")
    if value["identity"]["cik"] != value["cik"] or set(value["identity"].get("source_ids", [])) - known:
        raise ValueError("SEC_IDENTITY_PROVENANCE_MISMATCH")
    for key, item in value["sources"].items():
        if key != source_id(item) or endpoint(item["source_url"]) != item["kind"]:
            raise ValueError("SEC_SOURCE_ID_OR_ENDPOINT_CHANGED")
    for row in value["filings"]:
        if set(row.get("metadata_source_ids", []) + row["document_source_ids"]) - known:
            raise ValueError("SEC_FILING_SOURCE_MISSING")
    for section in value["financials"]["sections"]:
        for metric in list(section["metrics"].values()) + list(section["derived"].values()):
            if set(metric["source_ids"]) - known:
                raise ValueError("SEC_METRIC_SOURCE_MISSING")
    return value


def as_of(store, issuer, cutoff):
    """A downloaded 2024 fact is unavailable to a 2024 simulation downloaded today."""
    point = as_utc(cutoff)
    folder = Path(store) / "entities" / sec_facts.cik(issuer)
    eligible = []
    for path in (folder / "receipts").glob("*.json"):
        receipt = read(path)
        if as_utc(receipt["available_at"]) <= point:
            eligible.append(receipt)
    if not eligible:
        return None
    receipt = max(eligible, key=lambda r: (as_utc(r["available_at"]), r["sequence"]))
    path = folder / "snapshots" / (receipt["snapshot_id"] + ".json")
    if digest(path) != receipt["snapshot_sha256"]:
        raise ValueError("SEC_SNAPSHOT_INTEGRITY_CHANGED")
    value = validate_snapshot(read(path))
    for item in value["sources"].values():
        raw_path = Path(item["raw_path"])
        if not raw_path.is_file() or digest(raw_path) != item["raw_sha256"]:
            raise ValueError("SEC_SNAPSHOT_RAW_INTEGRITY_CHANGED")
        if as_utc(item["available_at"]) > as_utc(receipt["available_at"]):
            raise ValueError("SEC_SNAPSHOT_SOURCE_NOT_YET_AVAILABLE")
    return {**value, "available_at": receipt["available_at"], "sequence": receipt["sequence"]}


def latest(store, issuer):
    return as_of(store, issuer, "9999-12-31T23:59:59Z")


def commit(store, issuer, content, clock=now):
    folder = Path(store) / "entities" / issuer
    # Source checked_at is operational metadata: unchanged source bytes do not create new data vintages.
    stable = {**content, "sources": {k: {a: b for a, b in v.items() if a not in {"checked_at", "http_status", "etag", "last_modified"}} for k, v in content["sources"].items()}}
    identity = canonical_hash(stable)
    previous = read(folder / "latest.json", {})
    # latest.json is a repairable cache. The immutable receipts are authoritative after a crash.
    receipts = [read(p) for p in (folder / "receipts").glob("*.json")]
    if receipts:
        previous = max(receipts, key=lambda r: r["sequence"])
    if previous.get("content_hash") == identity:
        atomic_json(folder / "latest.json", previous, replace=True)
        return previous, False
    sequence = previous.get("sequence", 0) + 1
    snapshot_id = f"{sequence:08d}-{identity[:24]}"
    path = folder / "snapshots" / (snapshot_id + ".json")
    value = validate_snapshot({**content, "snapshot_id": snapshot_id, "content_hash": identity})
    if not path.exists():
        atomic_json(path, value)
    elif read(path).get("content_hash") != identity:
        raise ValueError("SEC_ORPHAN_SNAPSHOT_CONFLICT")
    # Admission time is assigned AFTER immutable snapshot commit, never its filing/acceptance date.
    available = clock()
    if previous.get("available_at") and as_utc(available) <= as_utc(previous["available_at"]):
        raise Unavailable("SEC_CLOCK_NOT_ADVANCED")
    if any(as_utc(item["available_at"]) > as_utc(available) for item in value["sources"].values()):
        raise Unavailable("SEC_SOURCE_CLOCK_IN_FUTURE")
    receipt = {"snapshot_id": snapshot_id, "snapshot_sha256": digest(path), "content_hash": identity,
        "available_at": available, "sequence": sequence}
    atomic_json(folder / "receipts" / (f"{sequence:08d}-" + uuid.uuid4().hex + ".json"), receipt)
    atomic_json(folder / "latest.json", receipt, replace=True)
    return receipt, True


def _entity(client, store, entity, settings, clock, reference=None):
    issuer = entity["cik"]
    folder = Path(store) / "entities" / issuer
    state_path = folder / "state.json"
    with exclusive_lock(folder / "refresh.lock"):
        state = read(state_path, {"known_filings": {}, "documents": {}, "pending_documents": [], "facts_lag_attempts": 0})
        submissions = client.get(f"https://data.sec.gov/submissions/CIK{issuer}.json")
        payload = sec_facts.loads(submissions["raw"])
        rows = sec_facts.filings(payload, issuer, settings["tracked_forms"])
        state.setdefault("filing_sources", {})[source_id(submissions)] = source(submissions)
        new = [r["accession"] for r in rows if r["accession"] not in state["known_filings"]]
        changed = [r["accession"] for r in rows if r["accession"] in state["known_filings"] and any(r.get(k) != state["known_filings"][r["accession"]].get(k) for k in ("form", "filed_date", "report_date", "primary_document", "accepted_at"))]
        first = not state.get("baseline_complete", False)
        # Only a filing later than the prior watermark is a newly observed arrival.
        # Older unseen rows remain archive additions, not fresh market catalysts.
        watermark = state.get("last_filing_watermark")
        arrivals = [r["accession"] for r in rows if r["accession"] in new and not first and watermark and (r["filed_date"], r["accession"]) > tuple(watermark)]
        desired = sec_facts.latest_documents(rows) if first else new + changed
        for accession in desired:
            if accession not in state["pending_documents"]:
                state["pending_documents"].append(accession)
        for row in rows:
            row["metadata_source_ids"] = [source_id(submissions)]
            state["known_filings"][row["accession"]] = row
        if rows:
            candidate = max((r["filed_date"], r["accession"]) for r in rows)
            state["last_filing_watermark"] = list(max(candidate, tuple(watermark) if watermark else candidate))
        changed_financial = any(r["accession"] in new + changed and r["form"].startswith(("10-K", "10-Q")) for r in rows)
        retry_facts = state.get("facts_next_retry") and as_utc(clock()) >= as_utc(state["facts_next_retry"])
        # Persist queues BEFORE I/O so a budget interruption cannot lose an accession.
        state["baseline_complete"] = True
        atomic_json(state_path, state, replace=True)
        sources = {source_id(submissions): source(submissions)}
        if reference:
            sources[source_id(reference)] = source(reference)
            entity = {**entity, "source_ids": [source_id(reference)]}
        else:
            entity = {**entity, "source_ids": []}
        all_rows = sorted(state["known_filings"].values(), key=lambda r: (r["filed_date"], r["accession"]), reverse=True)
        facts = None
        try:
            facts = client.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{issuer}.json", refresh=bool(changed_financial or retry_facts))
            normalized = sec_facts.normalize(sec_facts.loads(facts["raw"]), issuer, all_rows, source_id(facts))
            sources[source_id(facts)] = source(facts)
            # Operating-company playbooks must not be applied to funds/other entities.
            if payload.get("entityType") != "operating":
                normalized["sections"] = []
                normalized["unsupported"].append("non_operating_or_unknown_entity_type")
        except Unavailable as error:
            if error.code not in {"SEC_HTTP_404"}:
                raise
            normalized = {"sections": [], "semantic_review": "not_reviewed", "source_vintage": "current_api_snapshot_not_original_historical_vintage",
                "source_ids": [], "unsupported": ["companyfacts_unavailable"], "score": None, "investment_recommendation": None}
        selected = sec_facts.financial_filings(all_rows)
        awaiting = [r["accession"] for r in selected if not any(s["accession"] == r["accession"] and any(m["value"] is not None for m in s["metrics"].values()) for s in normalized["sections"])]
        if awaiting:
            state["facts_lag_attempts"] += 1
            seconds = settings["facts_retry_seconds"] if state["facts_lag_attempts"] <= settings["facts_initial_lag_retries"] else settings["refresh_seconds"]["companyfacts"]
            state["facts_next_retry"] = (as_utc(clock()) + timedelta(seconds=seconds)).isoformat()
        else:
            state.update(facts_lag_attempts=0, facts_next_retry=None)
        # Refresh baseline documents conditionally, keeping earlier raw versions on any failure.
        for accession in sec_facts.latest_documents(rows):
            if accession not in state["pending_documents"]:
                state["pending_documents"].append(accession)
        errors, attempted = [], 0
        for accession in list(state["pending_documents"]):
            if attempted >= settings["maximum_documents_per_entity_per_invocation"]:
                break
            row = state["known_filings"][accession]
            if not row["source_url"]:
                state["pending_documents"].remove(accession)
                continue
            attempted += 1
            try:
                result = client.get(row["source_url"])
                state["documents"][accession] = source(result)
                state["pending_documents"].remove(accession)
            except Unavailable as error:
                errors.append({"accession": accession, "reason": error.code})
                # A vanished/404 file is NOT proof of an officially withdrawn filing.
                state["pending_documents"].remove(accession)
                state["pending_documents"].append(accession)
            finally:
                atomic_json(state_path, state, replace=True)
        for accession, doc in state["documents"].items():
            sources[source_id(doc)] = doc
        for row in all_rows:
            for sid in row["metadata_source_ids"]:
                sources[sid] = state["filing_sources"][sid]
        catalog = sorted(state["known_filings"].values(), key=lambda r: (r["filed_date"], r["accession"]), reverse=True)
        for row in catalog:
            doc = state["documents"].get(row["accession"])
            row["document_source_ids"] = [source_id(doc)] if doc else []
            row["document_status"] = "collected_unreviewed" if doc else row["document_status"]
            if doc and any(e["accession"] == row["accession"] for e in errors):
                row["document_status"] = "refresh_failed_previous_document_preserved"
        content = {"schema_version": "1.0.0", "is_illustrative": client.illustrative,
            "label": "ILLUSTRATIVE — NOT LIVE MARKET DATA" if client.illustrative else "SEC_PUBLIC_SOURCE_UNREVIEWED_LOCAL_RESEARCH",
            "cik": issuer, "identity": entity, "name": payload.get("name"), "entity_type": payload.get("entityType"),
            "sic": payload.get("sic"), "sic_description": payload.get("sicDescription"),
            "filings": catalog, "financials": normalized, "sources": sources,
            "coverage": {"recent_submissions_only": True, "historical_files_not_collected": len(payload.get("filings", {}).get("files", [])),
                "missing_base_forms": [form for form in ("10-K", "10-Q", "8-K") if not any(r["form"] == form for r in catalog)],
                "predecessor_successor_cik_linkage_verified": False,
                "awaiting_exact_filing_facts": awaiting, "pending_documents": len(state["pending_documents"]), "document_errors": errors,
                "missing_recent_rows_are_not_deletions": True, "automatic_semantic_review": False,
                "original_historical_vintage_verified": False},
            "policy_hashes": {name: digest(ROOT / "config" / name) for name in ("sec_refresh_policy.json", "sec_fact_policy.json")}}
        content["code_hashes"] = {name: digest(ROOT / name) for name in ("research/sec_refresh.py", "research/sec_facts.py", "research/sec_transport.py", "schemas/sec_snapshot.schema.json")}
        receipt, created = commit(store, issuer, content, clock)
        state["last_success_at"] = clock()
        atomic_json(state_path, state, replace=True)
        return {"cik": issuer, "symbols": entity["symbols"], "name": payload.get("name"), "status": "updated" if created else "unchanged", "snapshot_id": receipt["snapshot_id"],
            "newly_observed_arrivals": arrivals, "baseline_filings": len(rows) if first else 0,
            "changed_known_filing_metadata": len(changed),
            "older_unseen_archive_additions": len(new) - len(arrivals) if not first else 0,
            "known_filings": len(catalog), "awaiting_facts": len(awaiting), "pending_documents": len(state["pending_documents"]), "document_errors": len(errors)}


def refresh(watchlist, *, client=None, store=None, clock=now, monotonic=time.monotonic, max_entities=None):
    settings = policy()
    if not isinstance(watchlist, dict) or not isinstance(watchlist.get("entities"), list) or not 0 < len(watchlist["entities"]) <= settings["maximum_watchlist_entities"]:
        raise ValueError("SEC_WATCHLIST_SIZE_OUT_OF_BOUNDS")
    client = client or Client(user_agent())
    namespace = "illustrative" if client.illustrative else "real"
    store = Path(store or ROOT / "reports/private/sec_store" / namespace)
    started, began = clock(), monotonic()
    state_path = store / "progress.json"
    result = {"started_at": started, "finished_at": None, "is_illustrative": client.illustrative,
        "watchlist_hash": canonical_hash(watchlist), "watchlist_entries": len(watchlist["entities"]), "status": "running", "entities": [], "unresolved": []}
    initial_requests = client.network_requests
    old_callback = client.on_attempt
    def budget():
        if client.network_requests - initial_requests >= settings["maximum_requests_per_invocation"] or monotonic() - began >= settings["maximum_invocation_seconds"]:
            raise Deferred((as_utc(clock()) + timedelta(minutes=15)).isoformat(), "SEC_INVOCATION_BUDGET_REACHED")
        old_callback()
    client.on_attempt = budget
    try:
        with exclusive_lock(store / "run.lock"):
            progress = read(state_path, {"cursor": 0})
            try:
                # A CIK-only list needs no current ticker table (supports delisted issuers).
                needs_reference = any(entry.get("symbol") for entry in watchlist["entities"])
                reference_result = client.get(REFERENCE) if needs_reference else None
                reference = sec_facts.loads(reference_result["raw"]) if reference_result else {"fields": ["cik", "name", "ticker", "exchange"], "data": []}
                entities, unresolved = sec_facts.resolve(watchlist, reference)
                result["unresolved"] = unresolved
                result["resolved_issuers"] = len(entities)
                if not entities:
                    result["status"] = "unresolved_watchlist"
                else:
                    cursor = progress.get("cursor", 0) % len(entities)
                    limit = min(max_entities or settings["maximum_entities_per_invocation"], settings["maximum_entities_per_invocation"], len(entities))
                    for offset in range(limit):
                        if monotonic() - began >= settings["maximum_invocation_seconds"]:
                            result["status"] = "budget_deferred"
                            break
                        index = (cursor + offset) % len(entities)
                        try:
                            result["entities"].append(_entity(client, store, entities[index], settings, clock, reference_result))
                        except (ValueError, KeyError, TypeError, Unavailable) as error:
                            if isinstance(error, Unavailable) and error.code == "SEC_USER_AGENT_REQUIRED": raise
                            result["entities"].append({"cik": entities[index]["cik"], "status": "unavailable", "reason": error.code if isinstance(error, Unavailable) else "SEC_SOURCE_SHAPE_OR_IDENTITY_REJECTED"})
                        progress["cursor"] = (index + 1) % len(entities)
                        atomic_json(state_path, progress, replace=True)
                    if result["status"] == "running":
                        result["status"] = "partial" if unresolved or any(r["status"] == "unavailable" or r.get("document_errors") for r in result["entities"]) else "completed_batch"
            except Deferred as error:
                result.update(status="deferred", reason=error.code, next_attempt_at=error.due_at)
            except Unavailable as error:
                result.update(status="awaiting_user_agent" if error.code == "SEC_USER_AGENT_REQUIRED" else "source_unavailable", reason=error.code)
            result["finished_at"] = clock()
            result["network_requests"] = client.network_requests - initial_requests
            run_id = started.replace(":", "").replace("-", "").replace(".", "") + "-" + uuid.uuid4().hex[:8]
            run_dir = store / "runs" / run_id
            atomic_json(run_dir / "result.json", result)
            atomic_json(run_dir / "watchlist.json", watchlist)
            from .sec_refresh_report import render_run
            (run_dir / "REPORT.ko.md").write_text(render_run(result), encoding="utf-8")
            atomic_json(store / "latest_run.json", {"result_path": str((run_dir / "result.json").resolve()), "status": result["status"], "at": result["finished_at"]}, replace=True)
            result["report_path"] = str((run_dir / "REPORT.ko.md").resolve())
    except LockBusy:
        result.update(status="another_refresh_running", finished_at=clock(), network_requests=client.network_requests - initial_requests)
    finally:
        client.on_attempt = old_callback
    return result


def prepare_job(watchlist_path):
    path = Path(watchlist_path).resolve()
    if not path.is_relative_to(ROOT.resolve()):
        raise ValueError("SEC_WATCHLIST_MUST_BE_LOCAL_PROJECT_FILE")
    watchlist = read(path)
    if not watchlist or not watchlist.get("entities"):
        raise ValueError("EMPTY_SEC_WATCHLIST")
    job_id = uuid.uuid4().hex[:16]
    job = {"version": "1.0.0", "created_at": now(), "task_name": "Stocker-SEC-Refresh-" + job_id,
        "watchlist_path": str(path), "watchlist_content_is_dynamic": True, "monthly_data_budget_usd": 0,
        "is_illustrative": False, "source_hashes": {name: digest(ROOT / name) for name in JOB_FILES}}
    folder = ROOT / "reports/private/sec_refresh_jobs" / job_id
    for name in ("sec_refresh_policy.json", "sec_fact_policy.json"):
        atomic_json(folder / "policies" / name, read(ROOT / "config" / name))
    atomic_json(folder / "job.json", job)
    return folder / "job.json"


def import_cohort(cohort_path, output):
    """Copy issuer IDs only; never read price outcomes, alter partitions or fit a model."""
    path, output = Path(cohort_path).resolve(), Path(output).resolve()
    if not path.is_relative_to(ROOT.resolve()) or not output.is_relative_to(ROOT.resolve()):
        raise ValueError("SEC_COHORT_IMPORT_MUST_STAY_LOCAL")
    cohort = read(path)
    rows = cohort.get("selected") if isinstance(cohort, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("SEC_COHORT_NOT_READY")
    entities = {}
    for row in rows:
        issuer = sec_facts.cik(row["cik"])
        entities.setdefault(issuer, {"cik": issuer, "label": row.get("symbol", issuer)})
    if len(entities) > policy()["maximum_watchlist_entities"]:
        raise ValueError("SEC_WATCHLIST_SIZE_OUT_OF_BOUNDS")
    watchlist = {"version": "1.0.0", "purpose": "SEC collection only; no historical evaluation inputs or partition changes",
        "source_cohort_sha256": digest(path), "entities": list(entities.values())}
    atomic_json(output, watchlist)
    return {"watchlist_path": str(output), "unique_issuers": len(entities), "source_cohort_sha256": watchlist["source_cohort_sha256"]}


def check_job(path):
    path = Path(path).resolve()
    if not path.is_relative_to((ROOT / "reports/private/sec_refresh_jobs").resolve()):
        raise ValueError("SEC_JOB_PATH_REJECTED")
    job = read(path)
    if job.get("monthly_data_budget_usd") != 0 or job.get("is_illustrative") is not False or set(job.get("source_hashes", {})) != set(JOB_FILES):
        raise ValueError("SEC_JOB_CONTRACT_CHANGED")
    if not job["task_name"].startswith("Stocker-SEC-Refresh-") or job["task_name"].split("-")[-1] != path.parent.name:
        raise ValueError("SEC_JOB_IDENTITY_CHANGED")
    if any(digest(ROOT / name) != value for name, value in job["source_hashes"].items()):
        raise ValueError("SEC_JOB_SOURCE_CHANGED")
    if not Path(job["watchlist_path"]).resolve().is_relative_to(ROOT.resolve()):
        raise ValueError("SEC_JOB_WATCHLIST_PATH_CHANGED")
    return job


def run_job(path):
    path = Path(path).resolve()
    if not path.is_relative_to((ROOT / "reports/private/sec_refresh_jobs").resolve()):
        raise ValueError("SEC_JOB_PATH_REJECTED")
    try:
        job = check_job(path)
        result = refresh(read(job["watchlist_path"]))
        status = {k: v for k, v in result.items() if k not in {"entities", "unresolved"}}
        status["processed_issuers"] = len(result["entities"])
    except (ValueError, OSError, KeyError, TypeError):
        status = {"status": "job_contract_or_local_state_rejected", "at": now()}
    atomic_json(path.parent / "status.json", status, replace=True)
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watchlist", type=Path, default=ROOT / "config/sec_watchlist.json")
    parser.add_argument("--symbols", nargs="+")
    parser.add_argument("--ciks", nargs="+")
    parser.add_argument("--prepare-job", action="store_true")
    parser.add_argument("--job", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--check-config", action="store_true")
    parser.add_argument("--as-of")
    parser.add_argument("--max-entities", type=int)
    parser.add_argument("--import-cohort", type=Path)
    parser.add_argument("--output-watchlist", type=Path)
    args = parser.parse_args()
    if args.import_cohort:
        if not args.output_watchlist:
            parser.error("--import-cohort requires --output-watchlist")
        print(json.dumps(import_cohort(args.import_cohort, args.output_watchlist)))
    elif args.check_config:
        print(json.dumps({"user_agent_configured": valid_user_agent(user_agent()), "policy_valid": bool(policy()), "data_budget_usd": 0}))
    elif args.prepare_job:
        print(json.dumps({"job_path": str(prepare_job(args.watchlist))}))
    elif args.job:
        if args.check:
            check_job(args.job)
            print(json.dumps({"job_valid": True}))
        else:
            print(json.dumps(read(args.job.parent / "status.json", {"status": "not_run"}) if args.status else run_job(args.job), ensure_ascii=True))
    elif args.as_of:
        if not args.ciks or len(args.ciks) != 1:
            parser.error("--as-of requires exactly one --ciks value")
        value = as_of(ROOT / "reports/private/sec_store/real", args.ciks[0], args.as_of)
        if value:
            from .sec_refresh_report import write_context
            output = write_context(value)
            print(json.dumps({"status": "available", "report_path": str(output)}))
        else:
            print(json.dumps({"status": "unavailable_at_cutoff"}))
    else:
        watchlist = {"entities": [{"symbol": s} for s in args.symbols]} if args.symbols else {"entities": [{"cik": s} for s in args.ciks]} if args.ciks else read(args.watchlist)
        result = refresh(watchlist, max_entities=args.max_entities)
        print(json.dumps({k: v for k, v in result.items() if k not in {"entities", "unresolved"}}, ensure_ascii=True))


if __name__ == "__main__":
    main()
