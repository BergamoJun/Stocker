"""Append-only original-filing reviews; never rewrite the SEC collector or BE pilot."""

import argparse
from datetime import date
from decimal import Decimal
import json
from pathlib import Path

import jsonschema

from . import sec_facts, sec_refresh, sec_filing_evidence
from .local_files import atomic_json, exclusive_lock
from .rights import as_utc
from .sec_report import ROOT
from .sec_transport import now
from .validation import digest


METHOD_FILES = ("research/sec_filing_evidence.py", "research/sec_fact_review.py", "research/sec_financial_context.py",
    "research/sec_financial_report.py", "config/sec_fact_review_policy.json", "schemas/sec_fact_review.schema.json",
    "schemas/sec_financial_report.schema.json")


def policy():
    value = sec_refresh.read(ROOT / "config/sec_fact_review_policy.json")
    if any(value[k] != 0 for k in ("monthly_data_budget_usd", "network_requests", "external_llm_calls")) or any(value[k] is not False for k in ("model_training_enabled", "release_enabled", "score_enabled", "amendment_merge")):
        raise ValueError("SEC_REVIEW_LOCAL_BOUNDARY_CHANGED")
    return value


def methods():
    return {name: digest(ROOT / name) for name in METHOD_FILES}


def precedes(at, cutoff):
    return as_utc(at).replace(microsecond=0) < as_utc(cutoff).replace(microsecond=0)


def validate(value, kind):
    schema = sec_refresh.read(ROOT / "schemas/sec_fact_review.schema.json")
    jsonschema.Draft202012Validator({"$ref": "#/$defs/" + kind, "$defs": schema["$defs"]}, format_checker=jsonschema.FormatChecker()).validate(value)


def namespace(root=None, illustrative=False):
    path = Path(root or ROOT / "reports/private/sec_fact_reviews").resolve()
    if not illustrative and not path.is_relative_to((ROOT / "reports/private").resolve()):
        raise ValueError("PRIVATE_SEC_REVIEW_STORE_REQUIRED")
    return path / ("illustrative" if illustrative else "real")


def state(issuer, cutoff, *, source_store=None, illustrative=False):
    issuer = sec_facts.cik(issuer)
    store = Path(source_store or ROOT / "reports/private/sec_store" / ("illustrative" if illustrative else "real")).resolve()
    if not illustrative and not store.is_relative_to((ROOT / "reports/private").resolve()):
        raise ValueError("PRIVATE_SEC_SOURCE_STORE_REQUIRED")
    snapshot = sec_refresh.as_of(store, issuer, cutoff)
    if snapshot is None: return {"snapshot": None, "bindings": {}, "source_store": str(store)}
    if snapshot["is_illustrative"] is not illustrative:
        raise ValueError("SEC_REVIEW_SOURCE_NAMESPACE_MISMATCH")
    folder = store / "entities" / issuer
    receipts = [sec_refresh.read(p) for p in (folder / "receipts").glob("*.json")]
    receipts = sorted((r for r in receipts if as_utc(r["available_at"]) <= as_utc(cutoff)), key=lambda r: r["sequence"])
    previous, incarnations = {}, {}
    for receipt in receipts:
        path = folder / "snapshots" / (receipt["snapshot_id"] + ".json")
        if digest(path) != receipt["snapshot_sha256"]: raise ValueError("SEC_BINDING_HISTORY_CHANGED")
        value = sec_refresh.read(path)
        if value["cik"] != issuer or value["is_illustrative"] is not illustrative:
            raise ValueError("SEC_BINDING_HISTORY_IDENTITY_CHANGED")
        visible = set()
        for filing in value["filings"]:
            accession = filing["accession"]
            if filing["form"] not in {"10-K", "10-Q", "10-K/A", "10-Q/A"}: continue
            if len(filing["document_source_ids"]) != 1: continue
            visible.add(accession)
            doc_id = filing["document_source_ids"][0]
            doc = value["sources"][doc_id]
            amendments = [{"accession": r["accession"], "report_date": r["report_date"], "documents": r["document_source_ids"]} for r in value["filings"]
                if r["form"] == filing["form"].replace("/A", "") + "/A" and (not r["report_date"] or r["report_date"] == filing["report_date"])]
            binding = {"cik": issuer, "accession": accession, "form": filing["form"], "report_date": filing["report_date"],
                "filed_date": filing["filed_date"], "accepted_at": filing["accepted_at"], "primary_document": filing["primary_document"],
                "source_id": doc_id, "source_url": doc["source_url"], "raw_sha256": doc["raw_sha256"],
                "amendments": sorted(amendments, key=lambda r: r["accession"])}
            signature = sec_refresh.canonical_hash(binding)
            if previous.get(accession) != signature:
                incarnations[accession] = {**binding, "incarnation_id": sec_refresh.canonical_hash([issuer, accession, receipt["sequence"], signature]),
                    "incarnation_available_at": receipt["available_at"]}
            previous[accession] = signature
        for missing in set(previous) - visible:
            previous.pop(missing)
            incarnations.pop(missing, None)
    current = {r["accession"] for r in snapshot["filings"] if len(r["document_source_ids"]) == 1}
    return {"snapshot": snapshot, "bindings": {k: v for k, v in incarnations.items() if k in current}, "source_store": str(store)}


def eligible(fact, filing, spec):
    if fact["tag"] not in spec["tags"] or fact["unit"] != spec["unit"] or not fact["same_entity"] or fact["dimensions"] or fact["hidden"] or not fact["row_text"] or fact["status"] != "parsed_numeric_evidence_only" or fact["end"] != filing["report_date"]:
        return False
    if spec["basis"] == "instant": return fact["is_instant"] and fact["start"] is None
    if fact["is_instant"] or not fact["start"]: return False
    try: days = (date.fromisoformat(fact["end"]) - date.fromisoformat(fact["start"])).days
    except (ValueError, TypeError): return False
    bounds = (330, 400) if filing["form"].startswith("10-K") else (60, 330) if spec["basis"] == "cashflow" else (60, 120)
    return bounds[0] <= days <= bounds[1]


def candidates(source_state, accession):
    snapshot, settings = source_state["snapshot"], policy()
    if snapshot is None or accession not in source_state["bindings"]:
        raise ValueError("SEC_DOCUMENT_UNAVAILABLE_FOR_REVIEW")
    if snapshot["entity_type"] != "operating": raise ValueError("OPERATING_COMPANY_REVIEW_REQUIRED")
    filing = next(r for r in snapshot["filings"] if r["accession"] == accession)
    doc = snapshot["sources"][source_state["bindings"][accession]["source_id"]]
    evidence = sec_filing_evidence.document(doc, snapshot["cik"])
    result = {name: [f for f in evidence if eligible(f, filing, spec)] for name, spec in settings["metrics"].items()}
    for name, spec in settings["metrics"].items():
        if spec["basis"] == "cashflow" and result[name]:
            first = min(f["start"] for f in result[name])
            result[name] = [f for f in result[name] if f["start"] == first]
    return result


def template(issuer, accession, *, source_store=None, illustrative=False, cutoff=None):
    at = cutoff or now()
    current = state(issuer, at, source_store=source_store, illustrative=illustrative)
    evidence = candidates(current, accession)
    manifest = {"version": "1.0.0", "template_only": True, "is_illustrative": illustrative, "reviewer_id": "",
        "reviewer_kind": "assistant_original_filing_review", "reviewed_at": None, "binding": current["bindings"][accession],
        "method_hashes": methods(), "findings": [{"metric": metric, "decision": "unresolved", "fact_id": None, "scope": None,
            "row_label": "", "definition": "", "rationale": "원문 표·기간·귀속범위를 검토한 뒤 작성", "rejected_fact_ids": []} for metric in policy()["metrics"]]}
    return {"label": "ILLUSTRATIVE — NOT LIVE MARKET DATA" if illustrative else "SEC_ORIGINAL_FILING_REVIEW_WORKSHEET",
        "manifest": manifest, "candidates": evidence}


def check_findings(manifest, evidence):
    settings, selected, seen = policy(), {}, set()
    for finding in manifest["findings"]:
        name = finding["metric"]
        if name not in settings["metrics"] or name in seen: raise ValueError("UNKNOWN_OR_DUPLICATE_REVIEW_METRIC")
        seen.add(name)
        if finding["decision"] == "unresolved":
            if finding["fact_id"] is not None or finding["scope"] is not None: raise ValueError("UNRESOLVED_REVIEW_CANNOT_SUPPLY_FACT")
            continue
        matches = [f for f in evidence[name] if f["fact_id"] == finding["fact_id"]]
        if len(matches) != 1: raise ValueError("REVIEW_MUST_SELECT_EXACT_ELIGIBLE_SOURCE_FACT")
        fact = matches[0]
        if finding["scope"] not in settings["metrics"][name]["scopes"] or not finding["definition"].strip() or not finding["row_label"].strip() or finding["row_label"].casefold() not in fact["row_text"].casefold():
            raise ValueError("REVIEW_SCOPE_DEFINITION_OR_ROW_LABEL_MISSING")
        tag_scopes = settings["metrics"][name].get("tag_scopes", {}).get(fact["tag"])
        if tag_scopes and finding["scope"] not in tag_scopes:
            raise ValueError("SEC_TAXONOMY_ATTRIBUTION_SCOPE_MISMATCH")
        valid_ids = {f["fact_id"] for f in evidence[name]}
        rejected = set(finding["rejected_fact_ids"])
        if not rejected <= valid_ids or fact["fact_id"] in rejected:
            raise ValueError("INVALID_REJECTED_SOURCE_FACT")
        # Different meanings/roundings cannot be silently averaged or selected by tag priority.
        conflicting = {f["fact_id"] for f in evidence[name] if Decimal(f["value"]) != Decimal(fact["value"]) or any(f[k] != fact[k] for k in ("start", "end"))}
        if not conflicting <= rejected: raise ValueError("EXPLAIN_AND_REJECT_OTHER_CONFLICTING_FACTS")
        selected[name] = fact
    return selected


def receipts(root=None, illustrative=False, cutoff=None):
    values = []
    for path in (namespace(root, illustrative) / "receipts").glob("*.json"):
        value = sec_refresh.read(path)
        if cutoff is not None and not precedes(value["available_at"], cutoff): continue
        validate(value, "receipt")
        if path.stem != value["receipt_id"] or sec_refresh.canonical_hash({k: v for k, v in value.items() if k != "receipt_sha256"}) != value["receipt_sha256"]:
            raise ValueError("SEC_REVIEW_RECEIPT_CHANGED")
        if sec_refresh.canonical_hash(value["manifest"]) != value["receipt_id"] or value["is_illustrative"] is not illustrative:
            raise ValueError("SEC_REVIEW_IDENTITY_OR_NAMESPACE_CHANGED")
        if value["manifest"]["is_illustrative"] is not illustrative or value["manifest"]["template_only"] or value["manifest"]["reviewed_at"] is None or not value["manifest"]["reviewer_id"].strip():
            raise ValueError("SEC_RECEIPT_CONTAINS_DRAFT_REVIEW")
        if not as_utc(value["manifest"]["binding"]["incarnation_available_at"]) <= as_utc(value["manifest"]["reviewed_at"]) <= as_utc(value["available_at"]):
            raise ValueError("SEC_RECEIPT_TIME_ORDER_CHANGED")
        values.append(value)
    if len({v["store_order"] for v in values}) != len(values): raise ValueError("SEC_REVIEW_RECEIPT_ORDER_CONFLICT")
    return sorted(values, key=lambda r: r["store_order"])


def store(manifest_path, *, root=None, source_store=None, illustrative=False, clock=now):
    policy()
    manifest = sec_refresh.read(manifest_path)
    validate(manifest, "manifest")
    at = clock()
    if manifest["template_only"] or not manifest["reviewer_id"].strip() or manifest["reviewed_at"] is None or manifest["is_illustrative"] is not illustrative:
        raise ValueError("DRAFT_OR_WRONG_NAMESPACE_REVIEW_REJECTED")
    if manifest["method_hashes"] != methods(): raise ValueError("SEC_REVIEW_METHOD_CHANGED")
    current = state(manifest["binding"]["cik"], at, source_store=source_store, illustrative=illustrative)
    binding = current["bindings"].get(manifest["binding"]["accession"])
    if binding != manifest["binding"]: raise ValueError("SEC_REVIEW_DOCUMENT_INCARNATION_CHANGED")
    if not as_utc(binding["incarnation_available_at"]) <= as_utc(manifest["reviewed_at"]) <= as_utc(at):
        raise ValueError("SEC_REVIEW_TIME_ORDER_INVALID")
    selected = check_findings(manifest, candidates(current, binding["accession"]))
    folder = namespace(root, illustrative)
    review_id = sec_refresh.canonical_hash(manifest)
    with exclusive_lock(folder / "write.lock"):
        existing = receipts(root, illustrative)
        same = next((r for r in existing if r["receipt_id"] == review_id), None)
        if same: return {"receipt_id": review_id, "available_at": same["available_at"], "status": "already_stored"}
        committed = clock()
        if existing and as_utc(committed) < max(as_utc(r["available_at"]) for r in existing): raise ValueError("SEC_REVIEW_CLOCK_MOVED_BACKWARDS")
        if as_utc(committed) < as_utc(manifest["reviewed_at"]): raise ValueError("SEC_REVIEW_COMMIT_PRECEDES_CLAIM")
        value = {"version": "1.0.0", "receipt_id": review_id, "manifest": manifest, "selected_evidence": selected,
            "available_at": committed, "store_order": max((r["store_order"] for r in existing), default=0) + 1,
            "is_illustrative": illustrative, "source_store": current["source_store"],
            "semantic_validation": "recorded_reviewer_assertion_not_machine_proof"}
        value["receipt_sha256"] = sec_refresh.canonical_hash(value)
        validate(value, "receipt")
        atomic_json(folder / "receipts" / (review_id + ".json"), value)
        return {"receipt_id": review_id, "available_at": committed, "status": "stored"}


def view(issuer, cutoff, *, root=None, source_store=None, illustrative=False):
    current = state(issuer, cutoff, source_store=source_store, illustrative=illustrative)
    current["reviews"], current["stale_reviews"], current["method_outdated_reviews"] = {}, 0, 0
    settings, method = policy(), methods()
    by_reviewer, evidence_cache = {}, {}
    for receipt in receipts(root, illustrative, cutoff):
        manifest, binding = receipt["manifest"], receipt["manifest"]["binding"]
        if binding["cik"] != sec_facts.cik(issuer) or not precedes(receipt["available_at"], cutoff): continue
        if manifest["method_hashes"] != method:
            current["method_outdated_reviews"] += 1
            continue
        if current["bindings"].get(binding["accession"]) != binding:
            current["stale_reviews"] += 1
            continue
        accession = binding["accession"]
        if accession not in evidence_cache: evidence_cache[accession] = candidates(current, accession)
        selected = check_findings(manifest, evidence_cache[accession])
        if selected != receipt["selected_evidence"]: raise ValueError("STORED_SEC_REVIEW_EVIDENCE_CHANGED")
        for finding in manifest["findings"]:
            key = (accession, finding["metric"], manifest["reviewer_id"])
            by_reviewer[key] = {"finding": finding, "receipt_id": receipt["receipt_id"], "available_at": receipt["available_at"],
                "fact": selected.get(finding["metric"]), "reviewer_kind": manifest["reviewer_kind"]}
    for accession in current["bindings"]:
        current["reviews"][accession] = {}
        for metric in settings["metrics"]:
            opinions = [value for key, value in by_reviewer.items() if key[:2] == (accession, metric)]
            if not opinions:
                status, chosen = "awaiting_original_review", None
            elif any(o["finding"]["decision"] == "unresolved" for o in opinions):
                status, chosen = "review_unresolved", None
            else:
                signatures = {(Decimal(o["fact"]["value"]), o["fact"]["unit"], o["fact"]["start"], o["fact"]["end"], o["finding"]["scope"]) for o in opinions}
                status, chosen = ("reviewed_source_fact", opinions[-1]) if len(signatures) == 1 else ("conflicting_reviews", None)
            current["reviews"][accession][metric] = {"status": status, "chosen": chosen, "opinions": opinions}
    return current


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cik", required=True)
    parser.add_argument("--accession")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--import-review", type=Path)
    parser.add_argument("--source-store", type=Path)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--illustrative", action="store_true")
    args = parser.parse_args()
    if args.import_review:
        manifest = sec_refresh.read(args.import_review)
        if manifest["binding"]["cik"] != sec_facts.cik(args.cik): parser.error("Review CIK differs")
        print(json.dumps(store(args.import_review, root=args.root, source_store=args.source_store, illustrative=args.illustrative)))
    else:
        if not args.accession or not args.output: parser.error("Worksheet requires --accession and --output")
        worksheet = template(args.cik, args.accession, source_store=args.source_store, illustrative=args.illustrative)
        atomic_json(args.output, worksheet)
        print(json.dumps({"worksheet_path": str(args.output.resolve()), "candidate_count": sum(len(v) for v in worksheet["candidates"].values()), "template_only": True}))


if __name__ == "__main__":
    main()
