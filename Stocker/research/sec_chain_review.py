"""Manual, hash-bound cross-filing period components and compatibility assertions."""

import argparse
from datetime import date, timedelta
from decimal import Decimal
import json
from pathlib import Path

import jsonschema

from . import sec_chain_sources as sources, sec_filing_evidence as evidence, sec_period_review as periods, sec_refresh
from .local_files import atomic_json, exclusive_lock
from .rights import as_utc
from .sec_report import ROOT
from .sec_transport import now
from .validation import digest


FILES = ("research/sec_chain_sources.py", "research/sec_chain_review.py", "research/sec_chain_math.py", "research/sec_chain_report.py", "config/sec_chain_policy.json", "schemas/sec_chain.schema.json")
ROLES = ("current_income_quarter", "current_ytd", "prior_year_ytd", "annual", "previous_income_quarter", "previous_ytd", "earlier_ytd")


def methods(): return {**periods.methods(), **{name: digest(ROOT / name) for name in FILES}}


def validate(value, kind):
    schema = sec_refresh.read(ROOT / "schemas/sec_chain.schema.json")
    jsonschema.Draft202012Validator({"$ref": "#/$defs/" + kind, "$defs": schema["$defs"]}, format_checker=jsonschema.FormatChecker()).validate(value)


def namespace(root=None, illustrative=False):
    path = Path(root or ROOT / "reports/private/sec_chain_reviews").resolve()
    if not illustrative and not path.is_relative_to((ROOT / "reports/private").resolve()): raise ValueError("PRIVATE_CHAIN_REVIEW_REQUIRED")
    return path / ("illustrative" if illustrative else "real")


def state(issuer, cutoff, *, packet_root=None, review_root=None, source_store=None, illustrative=False):
    packet = sources.as_of(issuer, cutoff, root=packet_root, illustrative=illustrative)
    base = periods.current_state(issuer, cutoff, review_root=review_root, source_store=source_store, illustrative=illustrative)
    result = {"packet": packet, "base": base, "binding": None, "anchors": {}, "status": "supplemental_sources_unavailable"}
    if packet is None: return result
    if packet["policy_sha256"] != digest(ROOT / "config/sec_chain_policy.json"): return {**result, "status": "supplemental_source_policy_changed"}
    if packet["plan"]["status"] != "planned": return {**result, "status": packet["plan"]["status"]}
    if any(f["accession"] not in packet["documents"] for f in packet["plan"]["amendments"]): return {**result, "status": "amendment_document_unavailable"}
    current_plan = sources.plan(issuer, cutoff, source_store=source_store, illustrative=illustrative)
    if current_plan != packet["plan"]: return {**result, "status": "base_filing_plan_changed_refresh_sources"}
    current = packet["plan"]["filings"]["current"]
    anchors = {m: periods.anchor(base, current["accession"], m) for m in sources.policy()["metrics"]}
    annual_accession = packet["plan"]["filings"]["annual"]["accession"]
    annual_anchors = {m: periods.anchor(base, annual_accession, m) for m in sources.policy()["metrics"]}
    if any(base["reviews"].get(annual_accession, {}).get(m, {}).get("status") in {"review_unresolved", "conflicting_reviews"} for m in annual_anchors):
        return {**result, "status": "annual_original_review_unresolved"}
    source = packet["documents"].get(current["accession"])
    if source is None: return {**result, "status": "current_document_unavailable"}
    if any(a and a["fact"]["source_sha256"] != source["raw_sha256"] for a in anchors.values()): return {**result, "status": "current_review_document_changed"}
    annual_source = packet["documents"].get(annual_accession)
    if annual_source and any(a and a["fact"]["source_sha256"] != annual_source["raw_sha256"] for a in annual_anchors.values()):
        return {**result, "status": "annual_base_review_document_changed"}
    binding = {"cik": packet["cik"], "packet_id": packet["packet_id"], "packet_sha256": packet["packet_sha256"], "sequence": packet["sequence"],
        "current_accession": current["accession"], "anchor_bundle_sha256": sec_refresh.canonical_hash({"current": anchors, "annual": annual_anchors})}
    return {**result, "binding": binding, "anchors": anchors, "status": "ready" if any(anchors.values()) else "current_original_reviews_required"}


def candidates(current):
    if current["status"] != "ready": raise ValueError("CHAIN_NOT_READY_" + current["status"])
    packet, anchors = current["packet"], current["anchors"]
    filings, docs = packet["plan"]["filings"], packet["documents"]
    parsed = {acc: [{**f, "source_id": sec_refresh.source_id(source)} for f in evidence.document(source, packet["cik"])] for acc, source in docs.items()}
    def clean(f, metric):
        a = anchors[metric]
        return a is not None and f["tag"] == a["fact"]["tag"] and f["unit"] == "USD" and f["same_entity"] and not f["hidden"] and not f["dimensions"] and f["row_text"] and f["status"] == "parsed_numeric_evidence_only" and not f["is_instant"] and f["start"]
    annual = filings["annual"]
    annual_facts = parsed.get(annual["accession"], [])
    starts = {f["start"] for m in anchors for f in annual_facts if clean(f, m) and f["end"] == annual["report_date"] and 330 <= (date.fromisoformat(f["end"]) - date.fromisoformat(f["start"])).days + 1 <= 400}
    if len(starts) != 1: raise ValueError("ANNUAL_PERIOD_MISSING_OR_AMBIGUOUS")
    annual_start, annual_end = next(iter(starts)), annual["report_date"]
    fy_start = (date.fromisoformat(annual_end) + timedelta(days=1)).isoformat()
    current_end, previous_end = filings["current"]["report_date"], filings["previous"]["report_date"]
    q_start = (date.fromisoformat(previous_end) + timedelta(days=1)).isoformat()
    earlier = filings.get("earlier")
    previous_q_start = (max(date.fromisoformat(annual_end), date.fromisoformat(earlier["report_date"]) if earlier else date.fromisoformat(annual_end)) + timedelta(days=1)).isoformat()
    specs = {"current_income_quarter": ("current", q_start, current_end, 60, 120), "current_ytd": ("current", fy_start, current_end, 60, 330),
        "prior_year_ytd": ("current", annual_start, None, 60, 330), "annual": ("annual", annual_start, annual_end, 330, 400)}
    if filings["previous"]["form"] == "10-Q":
        specs.update(previous_income_quarter=("previous", previous_q_start, previous_end, 60, 120), previous_ytd=("previous", fy_start, previous_end, 60, 330))
    if earlier:
        earlier_start = fy_start if earlier["report_date"] > annual_end else annual_start
        specs["earlier_ytd"] = ("earlier", earlier_start, earlier["report_date"], 60, 330)
    result = {m: {role: [] for role in ROLES} for m in anchors}
    for metric in result:
        for role, (document_role, start, end, minimum, maximum) in specs.items():
            if role in {"current_income_quarter", "previous_income_quarter"} and metric in {"operating_cash_flow", "capital_expenditure"}: continue
            for f in parsed.get(filings[document_role]["accession"], []):
                if not clean(f, metric) or f["start"] != start or (end is not None and f["end"] != end): continue
                length = (date.fromisoformat(f["end"]) - date.fromisoformat(f["start"])).days + 1
                if not minimum <= length <= maximum: continue
                if role == "prior_year_ytd":
                    gap = (date.fromisoformat(current_end) - date.fromisoformat(f["end"])).days
                    current_length = (date.fromisoformat(current_end) - date.fromisoformat(fy_start)).days + 1
                    if not 358 <= gap <= 373 or abs(current_length - length) > 7: continue
                result[metric][role].append(f)
    return result


def template(issuer, *, cutoff=None, packet_root=None, review_root=None, source_store=None, illustrative=False):
    current = state(issuer, cutoff or now(), packet_root=packet_root, review_root=review_root, source_store=source_store, illustrative=illustrative)
    options = candidates(current)
    findings = [{"metric": m, "decision": "unresolved", "current_signature": a["signature"] if a else None, "scope": None, "roles": {}, "row_labels": {},
        "rejected_fact_ids": {}, "compatibility_rationale": "각 공시의 기간·연결/귀속범위·분류·동일 항목을 원문 대조한 뒤 작성"} for m, a in current["anchors"].items()]
    return {"manifest": {"version": "1.0.0", "template_only": True, "is_illustrative": illustrative, "reviewer_id": "", "reviewer_kind": "assistant_original_filing_review", "reviewed_at": None,
        "binding": current["binding"], "method_hashes": methods(), "findings": findings,
        "amendment_assessments": [{"accession": f["accession"], "decision": "unresolved", "rationale": "수정 사유와 선택한 기간·태그·범위·수치를 원문 대조한 뒤 작성"} for f in current["packet"]["plan"]["amendments"]]}, "candidates": options, "current_anchors": current["anchors"]}


def check(manifest, current, options):
    selected, seen = {}, set()
    for finding in manifest["findings"]:
        metric = finding["metric"]
        if metric in seen or metric not in current["anchors"]: raise ValueError("CHAIN_DUPLICATE_OR_UNKNOWN_METRIC")
        seen.add(metric)
        anchor = current["anchors"][metric]
        if finding["current_signature"] != (anchor["signature"] if anchor else None): raise ValueError("CHAIN_CURRENT_REVIEW_CHANGED")
        if finding["decision"] == "unresolved":
            if finding["roles"] or finding["scope"] is not None: raise ValueError("UNRESOLVED_CHAIN_CANNOT_SUPPLY_COMPONENTS")
            continue
        if anchor is None or finding["scope"] != anchor["scope"] or not finding["roles"]: raise ValueError("CHAIN_SCOPE_OR_CURRENT_REVIEW_REQUIRED")
        if set(finding["roles"]) != set(finding["row_labels"]) or set(finding["rejected_fact_ids"]) - set(finding["roles"]): raise ValueError("CHAIN_ROLE_LABELS_MISMATCH")
        values = {}
        for role, fact_id in finding["roles"].items():
            if role not in ROLES: raise ValueError("UNSUPPORTED_CHAIN_ROLE")
            matches = [f for f in options[metric][role] if f["fact_id"] == fact_id]
            if len(matches) != 1: raise ValueError("CHAIN_EXACT_SOURCE_COMPONENT_REQUIRED")
            fact = matches[0]
            label = finding["row_labels"][role]
            if not label.strip() or label.casefold() not in fact["row_text"].casefold(): raise ValueError("CHAIN_ROW_LABEL_MISMATCH")
            rejected = set(finding["rejected_fact_ids"].get(role, []))
            valid = {f["fact_id"] for f in options[metric][role]}
            conflicts = {f["fact_id"] for f in options[metric][role] if Decimal(f["value"]) != Decimal(fact["value"]) or any(f[k] != fact[k] for k in ("start", "end"))}
            if not rejected <= valid or fact_id in rejected or not conflicts <= rejected: raise ValueError("CHAIN_COMPETING_COMPONENTS_NOT_RESOLVED")
            anchor_role = "current_ytd" if metric in {"operating_cash_flow", "capital_expenditure"} else "current_income_quarter"
            if role == anchor_role and fact["fact_id"] != anchor["fact"]["fact_id"]: raise ValueError("CHAIN_COMPONENT_DIFFERS_FROM_CURRENT_ANCHOR")
            values[role] = {**fact, "scope": finding["scope"]}
        selected[metric] = values
    amendments = current["packet"]["plan"]["amendments"]
    assessments = manifest["amendment_assessments"]
    if {a["accession"] for a in assessments} != {a["accession"] for a in amendments} or len(assessments) != len(amendments): raise ValueError("CHAIN_AMENDMENT_ASSESSMENTS_REQUIRED")
    for amendment in amendments:
        assessment = next(a for a in assessments if a["accession"] == amendment["accession"])
        if assessment["decision"] != "selected_components_unchanged": raise ValueError("CHAIN_AMENDMENT_REVIEW_UNRESOLVED")
        document = current["packet"]["documents"][amendment["accession"]]
        facts = evidence.document(document, current["packet"]["cik"])
        affected = {f["accession"] for f in current["packet"]["plan"]["filings"].values() if f["form"] + "/A" == amendment["form"] and (not amendment["report_date"] or f["report_date"] == amendment["report_date"])}
        source_ids = {sec_refresh.source_id(current["packet"]["documents"][acc]) for acc in affected}
        for values in selected.values():
            for original_fact in values.values():
                if original_fact["source_id"] not in source_ids: continue
                comparable = [f for f in facts if f["tag"] == original_fact["tag"] and f["start"] == original_fact["start"] and f["end"] == original_fact["end"] and f["unit"] == original_fact["unit"] and f["same_entity"] and not f["hidden"] and not f["dimensions"] and f["row_text"] and f["status"] == "parsed_numeric_evidence_only"]
                if not comparable or any(Decimal(f["value"]) != Decimal(original_fact["value"]) for f in comparable): raise ValueError("CHAIN_AMENDED_COMPONENTS_DIFFER_OR_CONFLICT")
    return selected


def receipts(root=None, illustrative=False, cutoff=None):
    values = []
    for path in (namespace(root, illustrative) / "receipts").glob("*.json"):
        value = sec_refresh.read(path)
        if cutoff is not None and not periods.original.precedes(value["available_at"], cutoff): continue
        validate(value, "receipt")
        manifest = value["manifest"]
        if path.stem != value["receipt_id"] or sec_refresh.canonical_hash(manifest) != value["receipt_id"] or sec_refresh.canonical_hash({k: v for k, v in value.items() if k != "receipt_sha256"}) != value["receipt_sha256"]: raise ValueError("CHAIN_RECEIPT_INTEGRITY_CHANGED")
        if manifest["template_only"] or manifest["is_illustrative"] is not illustrative or value["is_illustrative"] is not illustrative or not manifest["reviewer_id"].strip() or manifest["reviewed_at"] is None or as_utc(manifest["reviewed_at"]) > as_utc(value["available_at"]): raise ValueError("CHAIN_RECEIPT_CLASSIFICATION_OR_TIME_CHANGED")
        values.append(value)
    if len({v["store_order"] for v in values}) != len(values): raise ValueError("CHAIN_RECEIPT_ORDER_CONFLICT")
    return sorted(values, key=lambda v: v["store_order"])


def store(path, *, root=None, packet_root=None, review_root=None, source_store=None, illustrative=False, clock=now):
    manifest = sec_refresh.read(path)
    validate(manifest, "manifest")
    if manifest["template_only"] or manifest["is_illustrative"] is not illustrative or not manifest["reviewer_id"].strip() or manifest["reviewed_at"] is None: raise ValueError("DRAFT_CHAIN_REVIEW_REJECTED")
    if manifest["method_hashes"] != methods(): raise ValueError("CHAIN_REVIEW_METHOD_CHANGED")
    current = state(manifest["binding"]["cik"], clock(), packet_root=packet_root, review_root=review_root, source_store=source_store, illustrative=illustrative)
    if current["status"] != "ready" or current["binding"] != manifest["binding"]: raise ValueError("CHAIN_SOURCE_OR_CURRENT_ANCHORS_CHANGED")
    if not as_utc(current["packet"]["available_at"]) <= as_utc(manifest["reviewed_at"]) <= as_utc(clock()): raise ValueError("CHAIN_REVIEW_TIME_INVALID")
    selected = check(manifest, current, candidates(current))
    accession = current["binding"]["current_accession"]
    annual_accession = current["packet"]["plan"]["filings"]["annual"]["accession"]
    if any(as_utc(o["available_at"]) > as_utc(manifest["reviewed_at"]) for acc in {accession, annual_accession} for metric in selected for o in current["base"]["reviews"].get(acc, {}).get(metric, {}).get("opinions", [])):
        raise ValueError("CHAIN_REVIEW_PRECEDES_CURRENT_REVIEW")
    folder, identity = namespace(root, illustrative), sec_refresh.canonical_hash(manifest)
    with exclusive_lock(folder / "write.lock"):
        previous = receipts(root, illustrative)
        same = next((r for r in previous if r["receipt_id"] == identity), None)
        if same: return {"receipt_id": identity, "available_at": same["available_at"], "status": "already_stored"}
        at = clock()
        if as_utc(at) < as_utc(manifest["reviewed_at"]) or (previous and as_utc(at) < max(as_utc(r["available_at"]) for r in previous)): raise ValueError("CHAIN_REVIEW_CLOCK_MOVED_BACKWARDS")
        value = {"version": "1.0.0", "receipt_id": identity, "manifest": manifest, "selected_components": selected, "available_at": at,
            "store_order": max((r["store_order"] for r in previous), default=0) + 1, "is_illustrative": illustrative}
        value["receipt_sha256"] = sec_refresh.canonical_hash(value)
        validate(value, "receipt")
        atomic_json(folder / "receipts" / (identity + ".json"), value)
        return {"receipt_id": identity, "available_at": at, "status": "stored"}


def view(issuer, cutoff, *, root=None, packet_root=None, review_root=None, source_store=None, illustrative=False):
    current = state(issuer, cutoff, packet_root=packet_root, review_root=review_root, source_store=source_store, illustrative=illustrative)
    active, options = {}, None
    rejected = {"source_or_anchor_changed": 0, "method_changed": 0}
    for receipt in receipts(root, illustrative, cutoff):
        manifest = receipt["manifest"]
        if manifest["binding"]["cik"] != str(issuer).zfill(10): continue
        if manifest["method_hashes"] != methods():
            rejected["method_changed"] += 1
            continue
        if current["status"] != "ready" or current["binding"] != manifest["binding"]:
            rejected["source_or_anchor_changed"] += 1
            continue
        if options is None: options = candidates(current)
        selected = check(manifest, current, options)
        if selected != receipt["selected_components"]: raise ValueError("CHAIN_STORED_COMPONENTS_CHANGED")
        for finding in manifest["findings"]:
            active[(finding["metric"], manifest["reviewer_id"])] = {"finding": finding, "components": selected.get(finding["metric"]), "receipt_id": receipt["receipt_id"], "amendment_assessments": manifest["amendment_assessments"]}
    result = {}
    for metric in sources.policy()["metrics"]:
        opinions = [v for k, v in active.items() if k[0] == metric]
        if not opinions: status, chosen = "awaiting_chain_review", None
        elif any(v["components"] is None for v in opinions): status, chosen = "chain_review_unresolved", None
        else:
            signatures = {tuple(sorted((r, Decimal(f["value"]), f["start"], f["end"], f["scope"], f["tag"]) for r, f in v["components"].items())) for v in opinions}
            status, chosen = ("reviewed_chain_components", opinions[-1]) if len(signatures) == 1 else ("conflicting_chain_reviews", None)
        result[metric] = {"status": status, "chosen": chosen, "opinions": opinions}
    return {"state": current, "metrics": result, "rejected_review_counts": rejected}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cik", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--import-review", type=Path)
    parser.add_argument("--packet-root", type=Path)
    parser.add_argument("--review-root", type=Path)
    parser.add_argument("--chain-root", type=Path)
    args = parser.parse_args()
    if args.import_review:
        if sec_refresh.read(args.import_review)["binding"]["cik"] != str(args.cik).zfill(10): parser.error("CIK differs")
        result = store(args.import_review, root=args.chain_root, packet_root=args.packet_root, review_root=args.review_root)
    else:
        if not args.output: parser.error("--output is required")
        sheet = template(args.cik, packet_root=args.packet_root, review_root=args.review_root)
        atomic_json(args.output, sheet)
        result = {"worksheet_path": str(args.output.resolve()), "template_only": True, "candidate_count": sum(len(items) for roles in sheet["candidates"].values() for items in roles.values())}
    print(json.dumps(result))


if __name__ == "__main__": main()
