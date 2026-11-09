"""Deterministic issuer-specific numeric gap resolution, separate from reviews."""

import argparse
from copy import deepcopy
from datetime import date
from decimal import Decimal, localcontext
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import uuid
import jsonschema

from research import sec_fact_review as reviews, sec_filing_evidence as evidence, sec_financial_context
from research.local_files import atomic_json
from research.project_compatibility import check as historical_check
from research.rights import as_utc
from research.sec_report import ROOT
from research.sec_transport import now
from research.validation import digest, stable_hash
from . import scope

BASE = Path(__file__).resolve().parent
FILES = ["extensions/financial_fill.py", "extensions/financial_policy.json", "extensions/financial_fill.schema.json",
    "research/sec_fact_review.py", "research/sec_filing_evidence.py", "research/sec_financial_context.py", "config/sec_fact_review_policy.json"]


def read(path): return json.loads(Path(path).read_text(encoding="utf-8"))
def methods(): return {p: digest(ROOT / p) for p in FILES}


def policy(cutoff):
    value = read(BASE / "financial_policy.json")
    if not as_utc(value["reviewed_at"]) <= as_utc(cutoff) < as_utc(value["review_due_at"]): raise ValueError("FINANCIAL_POLICY_NOT_CURRENT")
    if any(value[k] != 0 for k in ("monthly_data_budget_usd", "network_requests", "external_model_calls")) or value["model_training_enabled"] or value["score_enabled"]:
        raise ValueError("FINANCIAL_OVERLAY_BOUNDARY_CHANGED")
    return value


class DimensionParser(HTMLParser):
    """Preserve exact explicit axis/member pairs omitted by the legacy extractor."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.contexts, self.current, self.member = {}, None, None
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"xbrli:context", "context"}:
            if self.current is not None: raise ValueError("NESTED_FINANCIAL_CONTEXT")
            key = attrs.get("id")
            if not key or key in self.contexts: raise ValueError("DUPLICATE_FINANCIAL_CONTEXT")
            self.contexts[key] = []; self.current = key
        elif self.current is not None and tag.split(":")[-1] in {"explicitmember", "typedmember"}:
            if self.member is not None: raise ValueError("NESTED_FINANCIAL_MEMBER")
            self.member = {"kind": tag.split(":")[-1], "axis": attrs.get("dimension"), "text": []}
    def handle_data(self, value):
        if self.member is not None: self.member["text"].append(value)
    def handle_endtag(self, tag):
        if tag.split(":")[-1] in {"explicitmember", "typedmember"} and self.member is not None:
            self.contexts[self.current].append((self.member["kind"], self.member["axis"], "".join(self.member["text"]).strip()))
            self.member = None
        if tag in {"xbrli:context", "context"}:
            if self.member is not None: raise ValueError("UNTERMINATED_FINANCIAL_MEMBER")
            self.current = None
    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs); self.handle_endtag(tag)


def members(raw):
    if len(raw) > 30_000_000 or b"<!ENTITY" in raw.upper(): raise ValueError("UNSAFE_FINANCIAL_DOCUMENT")
    parser = DimensionParser(); parser.feed(raw.decode("utf-8-sig")); parser.close()
    if parser.current is not None or parser.member is not None: raise ValueError("UNTERMINATED_FINANCIAL_CONTEXT")
    return parser.contexts


def eligible(fact, filing, spec, *, dimensional=False):
    return reviews.eligible({**fact, "dimensions": []} if dimensional else fact, filing, spec)


def unique(facts):
    if not facts: return None, "missing_source_fact"
    signatures = {(f["value"], f["unit"], f["start"], f["end"], f["tag"]) for f in facts}
    if len(signatures) != 1: return None, "conflicting_source_values"
    return sorted(facts, key=lambda f: f["ordinal"])[0], "deterministic_source_match"


def select(metric, facts, dimensions, filing, spec, settings, issuer):
    profile = settings["issuer_profiles"][issuer]["profile"]
    if metric == "revenue" and profile == "xom_sales_member":
        rule = settings["xom_sales"]
        selected = {}
        for role in ("sales", "affiliates", "other", "total"):
            pair = [("explicitmember", rule["axis"], rule[role + "_member"])] if role != "total" else []
            candidates = [f for f in facts if f["tag"] == rule["tag"] and eligible(f, filing, spec, dimensional=True)
                and dimensions.get(f["context_ref"]) == pair and f["dimensions"] == ([rule["axis"]] if pair else [])
                and re.match(re.escape(rule[role + "_label"]) + r"\b", f["row_text"], re.I)]
            selected[role], status = unique(candidates)
            if selected[role] is None: return None, status, None
        a, b, c, total = [selected[k] for k in ("sales", "affiliates", "other", "total")]
        if any((f["start"], f["end"], f["unit"]) != (a["start"], a["end"], a["unit"]) for f in (b, c, total)):
            return None, "reconciliation_period_mismatch", None
        if not a["row_number"] < b["row_number"] < c["row_number"] < total["row_number"] or total["row_number"] - a["row_number"] > 8:
            return None, "reconciliation_statement_rows_mismatch", None
        with localcontext() as context:
            context.prec = 100
            if Decimal(a["value"]) + Decimal(b["value"]) + Decimal(c["value"]) != Decimal(total["value"]):
                return None, "reconciliation_total_mismatch", None
        return a, "deterministic_source_match", {"scope": rule["scope"], "definition": rule["definition_ko"], "reconciliation_fact_ids": [f["fact_id"] for f in (a, b, c, total)]}
    candidates = [f for f in facts if eligible(f, filing, spec) and dimensions.get(f["context_ref"]) == [] and re.match(settings["row_patterns"][metric], f["row_text"], re.I)]
    if spec["basis"] == "cashflow" and candidates:
        first = min(f["start"] for f in candidates); candidates = [f for f in candidates if f["start"] == first]
    chosen, status = unique(candidates)
    if chosen is None: return None, status, None
    scopes = spec.get("tag_scopes", {}).get(chosen["tag"], spec["scopes"])
    if len(scopes) != 1: return None, "ambiguous_attribution_scope", None
    return chosen, status, {"scope": scopes[0], "definition": "Exact standard tag, statement row, unit, entity and reporting period", "reconciliation_fact_ids": [chosen["fact_id"]]}


def compute(issuer, cutoff, *, illustrative=False, source_store=None, review_root=None):
    issuer = str(issuer).zfill(10); settings = policy(cutoff)
    if issuer not in settings["issuer_profiles"]: raise ValueError("FINANCIAL_ISSUER_PROFILE_UNAVAILABLE")
    current = reviews.state(issuer, cutoff, source_store=source_store, illustrative=illustrative)
    context = sec_financial_context.build(issuer, cutoff, source_store=source_store, root=review_root, illustrative=illustrative)
    if context["cik"] != issuer or current["snapshot"] and current["snapshot"]["cik"] != issuer:
        raise ValueError("FINANCIAL_SOURCE_IDENTITY_CHANGED")
    if current["snapshot"] and (context["entity_type"] != "operating" or settings["issuer_profiles"][issuer]["symbol"] not in context["symbols"]):
        raise ValueError("FINANCIAL_PROFILE_IDENTITY_OR_ENTITY_TYPE_CHANGED")
    facts, gaps, findings, binding_rows, source_files = [], [], [], [], {}
    bound = as_utc(cutoff).replace(microsecond=0)
    for section in context["sections"]:
        accession = section["accession"]; binding = current["bindings"].get(accession)
        filing = next(f for f in current["snapshot"]["filings"] if f["accession"] == accession)
        source = current["snapshot"]["sources"].get(binding["source_id"]) if binding else None
        items = {k: {p: v[p] for p in ("status", "value", "start", "end", "scope", "review_receipt_ids", "source_locator")} for k, v in section["metrics"].items()}
        binding_rows.append({"accession": accession, "binding": binding, "metrics": items, "amendment_pending": section["amendment_scope_pending"]})
        raw = Path(source["raw_path"]).read_bytes() if source else None
        if source:
            if digest(Path(source["raw_path"])) != source["raw_sha256"]: raise ValueError("FINANCIAL_SOURCE_CHANGED")
            source_files[source["raw_path"]] = source["raw_sha256"]
        parsed = evidence.parse(raw, source["raw_sha256"], issuer) if source else []
        dimensions = members(raw) if raw else {}
        for metric, item in section["metrics"].items():
            finding = {"accession": accession, "metric": metric, "status": item["status"], "definition": None, "source_fact_id": None}
            fact = definition = None
            if section["amendment_scope_pending"]: status = "amendment_scope_pending"
            elif item["status"] == "reviewed_source_fact": status = "existing_reviewed_fact"
            elif context["review_coverage"]["stale_reviews"] or context["review_coverage"]["method_outdated_reviews"]: status = "stale_or_outdated_review_requires_refresh"
            elif item["status"] not in {"awaiting_original_review", "review_unresolved"}: status = item["status"]
            elif item["status"] == "review_unresolved" and metric not in settings["unresolved_review_exceptions"].get(issuer, []): status = "explicit_review_unresolved"
            elif not source or not binding: status = "original_document_unavailable"
            elif any(as_utc(at) >= bound for at in (source["available_at"], binding["incarnation_available_at"], context["snapshot_available_at"])): status = "source_not_available_before_cutoff"
            elif not filing["report_date"] or date.fromisoformat(filing["report_date"]) > bound.date(): status = "future_or_missing_reporting_period"
            else: fact, status, definition = select(metric, parsed, dimensions, filing, reviews.policy()["metrics"][metric], settings, issuer)
            finding["status"] = status
            if fact:
                finding.update(definition=definition, source_fact_id=fact["fact_id"])
                spec = reviews.policy()["metrics"][metric]
                basis = "instant" if spec["basis"] == "instant" else "annual" if filing["form"].startswith("10-K") else "cumulative_ytd" if spec["basis"] == "cashflow" else "quarter"
                facts.append({"fact_id": "fin_" + issuer + "_" + accession + "_" + metric, "cik": issuer, "metric": metric, "value": fact["value"], "unit": fact["unit"], "basis": basis,
                    "start": fact["start"], "end": fact["end"], "scope": definition["scope"], "available_at": max([source["available_at"], binding["incarnation_available_at"], context["snapshot_available_at"]], key=as_utc),
                    "source_refs": [{"source_id": binding["source_id"], "url": source["source_url"], "raw_sha256": source["raw_sha256"],
                        "locator": {k: fact[k] for k in ("fact_id", "dom_id", "tag", "context_ref", "row_number", "source_sha256")}}], "validation_kind": "deterministic_source_match"})
            if status != "existing_reviewed_fact":
                reason = "정의·기간·단위가 모두 일치하는 원문 숫자가 없어 누락 상태를 유지합니다."
                if fact: reason = "원문 숫자·기간·분류·합계를 고정 규칙으로 대조했습니다. 사람 승인이나 회계감사가 아닙니다. " + definition["definition"]
                if metric == "operating_income" and issuer == "0002115436" and not fact:
                    reason = "이 공시에서 표준 영업손익 값을 확정하지 못했습니다. 세전이익·부문이익을 영업손익으로 대체하거나 0으로 채우지 않습니다."
                gaps.append({"metric": metric, "status": status, "reason": reason})
            findings.append(finding)
    annual = any(s["form"] == "10-K" for s in context["sections"])
    if not annual:
        gaps.append({"metric": "qoq_ttm", "status": "missing_same_cik_annual_history", "reason": "동일 SEC 기업 식별번호의 연간 원문 이력이 부족합니다. 다른 CIK의 과거 자료를 자동 합치지 않으며 전분기·최근 12개월은 보류합니다."})
    if not context["sections"]: gaps.append({"metric": "financial_statements", "status": "no_source_at_cutoff", "reason": "기준시각 전에 이용 가능한 해당 기업의 공시 원문이 없습니다."})
    reviewed = sum(f["status"] == "existing_reviewed_fact" for f in findings)
    return {"facts": facts, "gaps": gaps, "findings": findings, "coverage": {"expected_metrics": len(findings), "recorded_reviewed_metrics": reviewed,
        "deterministic_matched_metrics": len(facts), "withheld_metrics": len(findings) - reviewed - len(facts)},
        "binding_sha256": stable_hash(binding_rows), "source_files": source_files}


def capture(issuer, baseline, *, output=None, illustrative=False, source_store=None, review_root=None, clock=now):
    proof = scope.check(baseline); cutoff = clock(); issuer = str(issuer).zfill(10)
    payload = compute(issuer, cutoff, illustrative=illustrative, source_store=source_store, review_root=review_root)
    destination = Path(output or ROOT / "reports/private/financial_inputs").resolve()
    if not illustrative and not destination.is_relative_to((ROOT / "reports/private").resolve()): raise ValueError("PRIVATE_FINANCIAL_STORE_REQUIRED")
    folder = destination / ("illustrative" if illustrative else "real") / issuer / ("financial_" + uuid.uuid4().hex)
    committed = clock()
    if as_utc(committed) < as_utc(cutoff): raise ValueError("FINANCIAL_CLOCK_MOVED_BACKWARDS")
    value = {"contract": "issuer-financial-gap-source-v1", "record_id": folder.name, "cik": issuer, "capture_cutoff": cutoff, "available_at": committed,
        "is_illustrative": illustrative, "payload": payload, "method_hashes": methods(), "baseline_path": str(Path(baseline).resolve()), "baseline_sha256": proof["baseline_sha256"],
        "source_store": str(Path(source_store).resolve()) if source_store else None, "review_root": str(Path(review_root).resolve()) if review_root else None,
        "released_score": None, "price_plan": None, "external_model_calls": 0}
    value["record_sha256"] = stable_hash(value)
    scope.check(baseline, expected_sha256=proof["baseline_sha256"])
    folder.mkdir(parents=True, exist_ok=False); atomic_json(folder / "record.json", value)
    return value, folder


def view(path, cutoff, *, cik, illustrative=False):
    path = Path(path); path = path / "record.json" if path.is_dir() else path
    value = read(path)
    jsonschema.Draft202012Validator(read(BASE / "financial_fill.schema.json"), format_checker=jsonschema.FormatChecker()).validate(value)
    if value["method_hashes"] != methods() or value["record_id"] != path.parent.name or stable_hash({k: v for k, v in value.items() if k != "record_sha256"}) != value["record_sha256"]:
        raise ValueError("FINANCIAL_OVERLAY_RECORD_CHANGED")
    historical_check(value["baseline_path"], expected_sha256=value["baseline_sha256"])
    if value["cik"] != str(cik).zfill(10) or value["is_illustrative"] is not illustrative: raise ValueError("FINANCIAL_OVERLAY_IDENTITY_OR_NAMESPACE_CHANGED")
    if as_utc(value["available_at"]) >= as_utc(cutoff).replace(microsecond=0) or as_utc(value["capture_cutoff"]) > as_utc(value["available_at"]):
        raise ValueError("FINANCIAL_OVERLAY_NOT_AVAILABLE_AT_CUTOFF")
    kwargs = {"illustrative": illustrative, "source_store": value["source_store"], "review_root": value["review_root"]}
    rebuilt = compute(cik, value["capture_cutoff"], **kwargs)
    if rebuilt != value["payload"]: raise ValueError("FINANCIAL_OVERLAY_REPLAY_CHANGED")
    current = compute(cik, cutoff, **kwargs)
    if current["binding_sha256"] != rebuilt["binding_sha256"]: raise ValueError("FINANCIAL_SOURCE_OR_REVIEW_CHANGED_RECAPTURE_REQUIRED")
    result = deepcopy(rebuilt)
    for fact in result["facts"]: fact["available_at"] = value["available_at"]
    return {"facts": result["facts"], "gaps": result["gaps"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path); parser.add_argument("--ciks", nargs="+", required=True)
    args = parser.parse_args(argv)
    rows = []
    for cik in args.ciks:
        value, folder = capture(cik, args.baseline)
        rows.append({"cik": value["cik"], "folder": str(folder), **value["payload"]["coverage"]})
    print(json.dumps({"records": rows, "network_requests": 0, "external_model_calls": 0}))


if __name__ == "__main__": main()
