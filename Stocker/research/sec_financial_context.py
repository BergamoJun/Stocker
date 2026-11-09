"""Generic reviewed financial input, independent of frozen BE/price adapters."""

from decimal import Decimal, localcontext

from . import sec_fact_review as reviews, sec_facts


def derived(metrics, amendment_pending):
    result = {}
    def compatible(a, b):
        return all(m.get("status") == "reviewed_source_fact" and m.get("value") is not None and m.get("scope") == "consolidated" for m in (a, b)) and all(a.get(k) == b.get(k) for k in ("start", "end", "unit", "basis"))
    for label, a_name, b_name in (("operating_margin_percent", "operating_income", "revenue"), ("net_margin_percent", "net_income", "revenue"), ("free_cash_flow", "operating_cash_flow", "capital_expenditure")):
        a, b = metrics[a_name], metrics[b_name]
        valid = not amendment_pending and compatible(a, b)
        reason = "amendment_scope_not_merged" if amendment_pending else "reviewed_compatible_inputs_required"
        if not amendment_pending and all(m.get("status") == "reviewed_source_fact" for m in (a, b)) and any(m.get("scope") != "consolidated" for m in (a, b)):
            reason = "attribution_scope_mismatch"
        value = None
        if valid:
            with localcontext() as context:
                context.prec = 60
                if label == "free_cash_flow" and Decimal(b["value"]) >= 0:
                    value = format(Decimal(a["value"]) - Decimal(b["value"]), "f")
                elif label != "free_cash_flow" and Decimal(b["value"]) > 0:
                    value = format((Decimal(a["value"]) / Decimal(b["value"]) * 100).quantize(Decimal("0.000001")), "f")
                else:
                    reason = "invalid_revenue_denominator_or_capex_sign"
        result[label] = {"value": value, "unit": "USD" if label == "free_cash_flow" else "percent", "basis": a.get("basis"),
            "start": a.get("start"), "end": a.get("end"), "status": "calculated_from_reviewed_facts" if value is not None else reason,
            "formula": "operating_cash_flow-capital_expenditure" if label == "free_cash_flow" else a_name + "/revenue*100",
            "input_metrics": [a_name, b_name], "source_ids": sorted(set(a["source_ids"] + b["source_ids"])),
            "review_receipt_ids": sorted(set(a["review_receipt_ids"] + b["review_receipt_ids"]))}
    return result


def build(issuer, cutoff, *, root=None, source_store=None, illustrative=False):
    state = reviews.view(issuer, cutoff, root=root, source_store=source_store, illustrative=illustrative)
    snapshot, sections = state["snapshot"], []
    if snapshot:
        selected = sec_facts.financial_filings(snapshot["filings"]) if snapshot["entity_type"] == "operating" else []
        for filing in selected:
            accession = filing["accession"]
            binding = state["bindings"].get(accession)
            items = state["reviews"].get(accession, {})
            metrics = {}
            for name, spec in reviews.policy()["metrics"].items():
                record = items.get(name, {"status": "original_document_unavailable", "chosen": None, "opinions": []})
                chosen = record["chosen"]
                fact = chosen["fact"] if chosen else None
                basis = "instant" if spec["basis"] == "instant" else "annual" if filing["form"].startswith("10-K") else "cumulative_ytd" if spec["basis"] == "cashflow" else "quarter"
                metrics[name] = {"value": fact["value"] if fact else None, "unit": spec["unit"], "status": record["status"],
                    "basis": basis, "start": fact["start"] if fact else None, "end": fact["end"] if fact else filing["report_date"],
                    "scope": chosen["finding"]["scope"] if chosen else None, "definition": chosen["finding"]["definition"] if chosen else None,
                    "source_ids": [binding["source_id"]] if binding else [],
                    "review_receipt_ids": sorted({o["receipt_id"] for o in record["opinions"]}),
                    "review_available_at": max((o["available_at"] for o in record["opinions"]), default=None),
                    "source_locator": {k: fact[k] for k in ("fact_id", "dom_id", "tag", "context_ref", "row_number", "source_sha256")} if fact else None,
                    "review_rationale": chosen["finding"]["rationale"] if chosen else " / ".join(sorted({o["finding"]["rationale"] for o in record["opinions"]})) or None}
            amendment_pending = filing["is_amendment"] or bool(binding and binding["amendments"])
            sections.append({"accession": accession, "form": filing["form"], "report_date": filing["report_date"],
                "amendment_scope_pending": amendment_pending, "metrics": metrics, "derived": derived(metrics, amendment_pending)})
    verified = sum(m["status"] == "reviewed_source_fact" for s in sections for m in s["metrics"].values())
    expected = len(sections) * len(reviews.policy()["metrics"])
    return {"version": "1.0.0", "cik": sec_facts.cik(issuer), "as_of": cutoff, "is_illustrative": illustrative,
        "status": "unsupported_entity_type" if snapshot and snapshot["entity_type"] != "operating" else "reviewed_financial_context" if expected and verified == expected else "partial_financial_context" if verified else "awaiting_original_filing_review" if snapshot else "source_unavailable_at_cutoff",
        "name": snapshot["name"] if snapshot else None, "symbols": snapshot["identity"]["symbols"] if snapshot else [],
        "entity_type": snapshot["entity_type"] if snapshot else None, "snapshot_id": snapshot["snapshot_id"] if snapshot else None,
        "snapshot_available_at": snapshot["available_at"] if snapshot else None, "sources": snapshot["sources"] if snapshot else {},
        "sections": sections, "review_coverage": {"expected_metrics": expected, "reviewed_metrics": verified, "withheld_metrics": expected - verified,
            "stale_reviews": state["stale_reviews"], "method_outdated_reviews": state["method_outdated_reviews"]},
        "collection_coverage": snapshot["coverage"] if snapshot else {},
        "semantic_validation": "recorded_reviewer_assertion_not_machine_proof", "historical_original_vintage_verified": False,
        "source_store": state["source_store"], "score": None, "price_forecast": None}
