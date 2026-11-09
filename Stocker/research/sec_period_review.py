"""Separate comparative-period review receipts; original review methods stay frozen."""

import argparse
from datetime import date
from decimal import Decimal
import json
from pathlib import Path

import jsonschema

from . import sec_fact_review as original, sec_filing_evidence, sec_refresh
from .local_files import atomic_json, exclusive_lock
from .rights import as_utc
from .sec_report import ROOT
from .sec_transport import now
from .validation import digest


FILES = ("research/sec_period_review.py", "research/sec_growth.py", "research/sec_growth_report.py", "config/sec_growth_policy.json", "schemas/sec_growth.schema.json")


def policy():
    value = sec_refresh.read(ROOT / "config/sec_growth_policy.json")
    if any(value[k] != 0 for k in ("monthly_data_budget_usd", "network_requests", "external_llm_calls")) or any(value[k] is not False for k in ("model_training_enabled", "release_enabled", "score_enabled", "qoq_enabled", "ttm_enabled")):
        raise ValueError("SEC_GROWTH_BOUNDARY_CHANGED")
    return value


def methods():
    return {**original.methods(), **{name: digest(ROOT / name) for name in FILES}}


def validate(value, kind):
    schema = sec_refresh.read(ROOT / "schemas/sec_growth.schema.json")
    jsonschema.Draft202012Validator({"$ref": "#/$defs/" + kind, "$defs": schema["$defs"]}, format_checker=jsonschema.FormatChecker()).validate(value)


def namespace(root=None, illustrative=False):
    path = Path(root or ROOT / "reports/private/sec_period_reviews").resolve()
    if not illustrative and not path.is_relative_to((ROOT / "reports/private").resolve()): raise ValueError("PRIVATE_PERIOD_REVIEW_STORE_REQUIRED")
    return path / ("illustrative" if illustrative else "real")


def current_state(issuer, cutoff, *, review_root=None, source_store=None, illustrative=False):
    return original.view(issuer, cutoff, root=review_root, source_store=source_store, illustrative=illustrative)


def anchor(state, accession, metric):
    record = state["reviews"].get(accession, {}).get(metric)
    if not record or record["status"] != "reviewed_source_fact": return None
    value = {"fact": record["chosen"]["fact"], "scope": record["chosen"]["finding"]["scope"],
        "definition": record["chosen"]["finding"]["definition"], "review_receipt_ids": sorted(o["receipt_id"] for o in record["opinions"])}
    return {**value, "signature": sec_refresh.canonical_hash(value)}


def is_year_pair(current, prior):
    settings = policy()
    try:
        gap = (date.fromisoformat(current["end"]) - date.fromisoformat(prior["end"])).days
        if not settings["year_gap_days"][0] <= gap <= settings["year_gap_days"][1]: return False
        if current["is_instant"] or prior["is_instant"]:
            return current["is_instant"] and prior["is_instant"] and current["start"] is None and prior["start"] is None
        start_gap = (date.fromisoformat(current["start"]) - date.fromisoformat(prior["start"])).days
        if not settings["year_gap_days"][0] <= start_gap <= settings["year_gap_days"][1]: return False
        return abs(gap - start_gap) <= settings["maximum_duration_difference_days"]
    except (TypeError, ValueError, KeyError):
        return False


def candidates(state, accession):
    if state["snapshot"] is None or accession not in state["bindings"]: raise ValueError("PERIOD_SOURCE_DOCUMENT_UNAVAILABLE")
    binding = state["bindings"][accession]
    if binding["form"].endswith("/A") or binding["amendments"]: raise ValueError("PERIOD_AMENDMENT_SCOPE_UNRESOLVED")
    filing = next(f for f in state["snapshot"]["filings"] if f["accession"] == accession)
    doc = state["snapshot"]["sources"][binding["source_id"]]
    all_facts = sec_filing_evidence.document(doc, binding["cik"])
    values = {}
    for metric, spec in original.policy()["metrics"].items():
        current = anchor(state, accession, metric)
        values[metric] = []
        if current is None: continue
        for fact in all_facts:
            # The old eligibility checks stay unchanged. Only a separate comparative
            # profile substitutes the candidate's historical period end.
            if not original.eligible(fact, {**filing, "report_date": fact["end"]}, spec): continue
            if fact["tag"] != current["fact"]["tag"] or fact["unit"] != current["fact"]["unit"]: continue
            if is_year_pair(current["fact"], fact): values[metric].append(fact)
    return values


def template(issuer, accession, *, cutoff=None, review_root=None, source_store=None, illustrative=False):
    state = current_state(issuer, cutoff or now(), review_root=review_root, source_store=source_store, illustrative=illustrative)
    evidence = candidates(state, accession)
    findings = []
    for metric in original.policy()["metrics"]:
        current = anchor(state, accession, metric)
        findings.append({"metric": metric, "decision": "unresolved", "current_signature": current["signature"] if current else None,
            "current_fact_id": current["fact"]["fact_id"] if current else None, "prior_fact_id": None, "scope": None, "row_label": "",
            "comparability_rationale": "동일 공시의 비교 표에서 전년 기간·정의·귀속 범위를 대조한 뒤 작성", "rejected_fact_ids": []})
    return {"manifest": {"version": "1.0.0", "template_only": True, "is_illustrative": illustrative, "reviewer_id": "",
        "reviewer_kind": "assistant_original_filing_review", "reviewed_at": None, "relation": "year_over_year",
        "binding": state["bindings"][accession], "method_hashes": methods(), "findings": findings}, "candidates": evidence,
        "current_anchors": {metric: anchor(state, accession, metric) for metric in original.policy()["metrics"]}}


def check_findings(manifest, state, options):
    selected, seen = {}, set()
    accession = manifest["binding"]["accession"]
    for finding in manifest["findings"]:
        metric = finding["metric"]
        if metric in seen or metric not in original.policy()["metrics"]: raise ValueError("DUPLICATE_OR_UNKNOWN_PERIOD_METRIC")
        seen.add(metric)
        current = anchor(state, accession, metric)
        signature = current["signature"] if current else None
        if finding["current_signature"] != signature or finding["current_fact_id"] != (current["fact"]["fact_id"] if current else None):
            raise ValueError("CURRENT_REVIEW_CHANGED_SINCE_PERIOD_INSPECTION")
        if finding["decision"] == "unresolved":
            if finding["prior_fact_id"] is not None or finding["scope"] is not None: raise ValueError("UNRESOLVED_PERIOD_CANNOT_SUPPLY_FACT")
            continue
        matches = [f for f in options[metric] if f["fact_id"] == finding["prior_fact_id"]]
        if current is None or len(matches) != 1: raise ValueError("REVIEW_EXACT_COMPARATIVE_FACT_REQUIRED")
        fact = matches[0]
        if finding["scope"] != current["scope"] or not finding["row_label"].strip() or finding["row_label"].casefold() not in fact["row_text"].casefold():
            raise ValueError("COMPARATIVE_SCOPE_OR_ROW_LABEL_MISMATCH")
        valid_ids, rejected = {f["fact_id"] for f in options[metric]}, set(finding["rejected_fact_ids"])
        if not rejected <= valid_ids or fact["fact_id"] in rejected: raise ValueError("INVALID_REJECTED_COMPARATIVE_FACT")
        conflicts = {f["fact_id"] for f in options[metric] if Decimal(f["value"]) != Decimal(fact["value"]) or any(f[k] != fact[k] for k in ("start", "end"))}
        if not conflicts <= rejected: raise ValueError("EXPLAIN_COMPETING_COMPARATIVE_FACTS")
        selected[metric] = {"current": current, "prior": fact, "scope": finding["scope"]}
    return selected


def receipts(root=None, illustrative=False, cutoff=None):
    values = []
    for path in (namespace(root, illustrative) / "receipts").glob("*.json"):
        value = sec_refresh.read(path)
        if cutoff is not None and not original.precedes(value["available_at"], cutoff): continue
        validate(value, "receipt")
        if path.stem != value["receipt_id"] or sec_refresh.canonical_hash(value["manifest"]) != value["receipt_id"] or sec_refresh.canonical_hash({k: v for k, v in value.items() if k != "receipt_sha256"}) != value["receipt_sha256"]:
            raise ValueError("PERIOD_RECEIPT_INTEGRITY_CHANGED")
        manifest = value["manifest"]
        if manifest["template_only"] or manifest["is_illustrative"] is not illustrative or value["is_illustrative"] is not illustrative or not manifest["reviewer_id"].strip() or manifest["reviewed_at"] is None:
            raise ValueError("PERIOD_RECEIPT_CLASSIFICATION_CHANGED")
        if not as_utc(manifest["binding"]["incarnation_available_at"]) <= as_utc(manifest["reviewed_at"]) <= as_utc(value["available_at"]): raise ValueError("PERIOD_RECEIPT_TIME_CHANGED")
        values.append(value)
    if len({v["store_order"] for v in values}) != len(values): raise ValueError("PERIOD_RECEIPT_ORDER_CHANGED")
    return sorted(values, key=lambda v: v["store_order"])


def store(path, *, root=None, review_root=None, source_store=None, illustrative=False, clock=now):
    policy()
    manifest = sec_refresh.read(path)
    validate(manifest, "manifest")
    if manifest["template_only"] or manifest["is_illustrative"] is not illustrative or not manifest["reviewer_id"].strip() or manifest["reviewed_at"] is None: raise ValueError("DRAFT_PERIOD_REVIEW_REJECTED")
    if manifest["method_hashes"] != methods(): raise ValueError("PERIOD_REVIEW_METHOD_CHANGED")
    state = current_state(manifest["binding"]["cik"], clock(), review_root=review_root, source_store=source_store, illustrative=illustrative)
    binding = state["bindings"].get(manifest["binding"]["accession"])
    if binding != manifest["binding"]: raise ValueError("PERIOD_REVIEW_SOURCE_EDITION_CHANGED")
    if not as_utc(binding["incarnation_available_at"]) <= as_utc(manifest["reviewed_at"]) <= as_utc(clock()): raise ValueError("PERIOD_REVIEW_TIME_INVALID")
    selected = check_findings(manifest, state, candidates(state, binding["accession"]))
    for metric, item in selected.items():
        active = state["reviews"][binding["accession"]][metric]
        if any(as_utc(o["available_at"]) > as_utc(manifest["reviewed_at"]) for o in active["opinions"]): raise ValueError("PERIOD_REVIEW_PRECEDES_CURRENT_REVIEW")
    folder, review_id = namespace(root, illustrative), sec_refresh.canonical_hash(manifest)
    with exclusive_lock(folder / "write.lock"):
        previous = receipts(root, illustrative)
        same = next((r for r in previous if r["receipt_id"] == review_id), None)
        if same: return {"receipt_id": review_id, "available_at": same["available_at"], "status": "already_stored"}
        at = clock()
        if as_utc(at) < as_utc(manifest["reviewed_at"]) or (previous and as_utc(at) < max(as_utc(r["available_at"]) for r in previous)): raise ValueError("PERIOD_REVIEW_CLOCK_MOVED_BACKWARDS")
        value = {"version": "1.0.0", "receipt_id": review_id, "manifest": manifest, "selected_pairs": selected, "is_illustrative": illustrative,
            "available_at": at, "store_order": max((r["store_order"] for r in previous), default=0) + 1}
        value["receipt_sha256"] = sec_refresh.canonical_hash(value)
        validate(value, "receipt")
        atomic_json(folder / "receipts" / (review_id + ".json"), value)
        return {"receipt_id": review_id, "available_at": at, "status": "stored"}


def view(issuer, cutoff, *, root=None, review_root=None, source_store=None, illustrative=False):
    state = current_state(issuer, cutoff, review_root=review_root, source_store=source_store, illustrative=illustrative)
    active, options_cache = {}, {}
    counts = {"source_changed": 0, "method_changed": 0, "current_review_changed": 0}
    for receipt in receipts(root, illustrative, cutoff):
        manifest, binding = receipt["manifest"], receipt["manifest"]["binding"]
        if binding["cik"] != str(issuer).zfill(10): continue
        if manifest["method_hashes"] != methods():
            counts["method_changed"] += 1
            continue
        if state["bindings"].get(binding["accession"]) != binding:
            counts["source_changed"] += 1
            continue
        accession = binding["accession"]
        for finding in manifest["findings"]:
            metric = finding["metric"]
            current = anchor(state, accession, metric)
            if finding["current_signature"] != (current["signature"] if current else None):
                counts["current_review_changed"] += 1
                continue
            if accession not in options_cache: options_cache[accession] = candidates(state, accession)
            mini = {**manifest, "findings": [finding]}
            selected = check_findings(mini, state, options_cache[accession]).get(metric)
            if selected != receipt["selected_pairs"].get(metric): raise ValueError("PERIOD_SELECTED_EVIDENCE_CHANGED")
            active[(accession, metric, manifest["reviewer_id"])] = {"finding": finding, "pair": selected, "receipt_id": receipt["receipt_id"], "available_at": receipt["available_at"]}
    result = {}
    for accession in state["bindings"]:
        result[accession] = {}
        for metric in original.policy()["metrics"]:
            opinions = [v for k, v in active.items() if k[:2] == (accession, metric)]
            if not opinions: status, chosen = "awaiting_comparative_review", None
            elif any(v["pair"] is None for v in opinions): status, chosen = "comparative_review_unresolved", None
            else:
                signatures = {(Decimal(v["pair"]["prior"]["value"]), v["pair"]["prior"]["start"], v["pair"]["prior"]["end"], v["pair"]["scope"]) for v in opinions}
                status, chosen = ("reviewed_comparative_pair", opinions[-1]) if len(signatures) == 1 else ("conflicting_comparative_reviews", None)
            result[accession][metric] = {"status": status, "chosen": chosen, "opinions": opinions}
    return {"source_state": state, "comparisons": result, "withheld_review_counts": counts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cik", required=True)
    parser.add_argument("--accession")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--import-review", type=Path)
    parser.add_argument("--review-root", type=Path)
    parser.add_argument("--period-root", type=Path)
    parser.add_argument("--source-store", type=Path)
    parser.add_argument("--illustrative", action="store_true")
    args = parser.parse_args()
    if args.import_review:
        if sec_refresh.read(args.import_review)["binding"]["cik"] != str(args.cik).zfill(10): parser.error("CIK differs")
        result = store(args.import_review, root=args.period_root, review_root=args.review_root, source_store=args.source_store, illustrative=args.illustrative)
    else:
        if not args.accession or not args.output: parser.error("--accession and --output are required")
        value = template(args.cik, args.accession, review_root=args.review_root, source_store=args.source_store, illustrative=args.illustrative)
        atomic_json(args.output, value)
        result = {"worksheet_path": str(args.output.resolve()), "template_only": True, "candidate_count": sum(len(v) for v in value["candidates"].values())}
    print(json.dumps(result))


if __name__ == "__main__": main()
