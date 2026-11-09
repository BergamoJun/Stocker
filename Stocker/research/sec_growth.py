"""Decimal-safe same-filing comparisons, sign transitions and percentage points."""

from decimal import Decimal, DecimalException, localcontext

from . import sec_period_review as periods


def change(current, prior):
    a, b = Decimal(current), Decimal(prior)
    if not a.is_finite() or not b.is_finite(): raise ValueError("NONFINITE_GROWTH_INPUT")
    with localcontext() as context:
        context.prec = 160
        delta = a - b
        if b < 0:
            transition = "negative_to_positive" if a > 0 else "negative_to_zero" if a == 0 else "negative_magnitude_decreased" if delta > 0 else "negative_magnitude_increased" if delta < 0 else "unchanged_negative"
        elif b == 0:
            transition = "zero_to_positive" if a > 0 else "zero_to_negative" if a < 0 else "unchanged_zero"
        else:
            transition = "positive_to_negative" if a < 0 else "positive_to_zero" if a == 0 else "increased_positive" if delta > 0 else "decreased_positive" if delta < 0 else "unchanged_positive"
        percent, status = None, "negative_or_zero_prior_percentage_withheld"
        if b > 0:
            try: percent, status = format((delta / b * 100).quantize(Decimal("0.000001")), "f"), "calculated"
            except DecimalException: status = "decimal_result_out_of_range"
    return {"current": format(a, "f"), "prior": format(b, "f"), "absolute_change": format(delta, "f"), "percent_change": percent, "percent_status": status, "transition": transition}


def _derived(pairs):
    result = {}
    for name, numerator in (("operating_margin_change", "operating_income"), ("net_margin_change", "net_income")):
        a, b = pairs.get(numerator), pairs.get("revenue")
        item = {"current": None, "prior": None, "change": None, "unit": "percentage_points", "status": "compatible_reviewed_pairs_required", "input_metrics": [numerator, "revenue"]}
        if a and b:
            compatible = all(x["scope"] == "consolidated" for x in (a, b)) and all(a[side][k] == b[side][k] for side in ("current", "prior") for k in ("start", "end", "unit"))
            if compatible and all(Decimal(b[side]["value"]) > 0 for side in ("current", "prior")):
                with localcontext() as context:
                    context.prec = 160
                    current = Decimal(a["current"]["value"]) / Decimal(b["current"]["value"]) * 100
                    prior = Decimal(a["prior"]["value"]) / Decimal(b["prior"]["value"]) * 100
                    item.update(current=format(current.quantize(Decimal("0.000001")), "f"), prior=format(prior.quantize(Decimal("0.000001")), "f"),
                        change=format((current - prior).quantize(Decimal("0.000001")), "f"), status="calculated")
            elif any(x["scope"] != "consolidated" for x in (a, b)):
                item["status"] = "attribution_scope_mismatch"
        result[name] = item
    a, b = pairs.get("operating_cash_flow"), pairs.get("capital_expenditure")
    fcf = {"status": "compatible_reviewed_pairs_required", "unit": "USD", "calculation": None, "input_metrics": ["operating_cash_flow", "capital_expenditure"]}
    if a and b and all(x["scope"] == "consolidated" for x in (a, b)) and all(a[side][k] == b[side][k] for side in ("current", "prior") for k in ("start", "end", "unit")) and all(Decimal(b[side]["value"]) >= 0 for side in ("current", "prior")):
        with localcontext() as context:
            context.prec = 160
            values = {side: format(Decimal(a[side]["value"]) - Decimal(b[side]["value"]), "f") for side in ("current", "prior")}
        fcf.update(status="calculated", calculation=change(values["current"], values["prior"]))
    result["free_cash_flow_change"] = fcf
    return result


def build(issuer, cutoff, base_context, *, root=None, review_root=None, source_store=None, illustrative=False):
    state = periods.view(issuer, cutoff, root=root, review_root=review_root, source_store=source_store, illustrative=illustrative)
    sections, paired, expected = [], 0, 0
    for section in base_context["sections"]:
        accession, metrics, valid_pairs = section["accession"], {}, {}
        for metric, current in section["metrics"].items():
            expected += 1
            record = state["comparisons"].get(accession, {}).get(metric, {"status": "comparative_source_unavailable", "chosen": None, "opinions": []})
            chosen = record["chosen"]
            pair = chosen["pair"] if chosen else None
            value = {"status": record["status"], "calculation": None, "current_value": current["value"], "unit": current["unit"], "basis": current["basis"], "scope": current["scope"],
                "pair": None, "source_ids": current["source_ids"], "current_review_receipt_ids": current["review_receipt_ids"],
                "period_review_receipt_ids": sorted({o["receipt_id"] for o in record["opinions"]}),
                "rationale": " / ".join(sorted({o["finding"]["comparability_rationale"] for o in record["opinions"]})) or None}
            if pair:
                if current["status"] != "reviewed_source_fact" or current["value"] != pair["current"]["fact"]["value"] or current["scope"] != pair["scope"]:
                    raise ValueError("GROWTH_CURRENT_CONTEXT_MISMATCH")
                simple = {"current": pair["current"]["fact"], "prior": pair["prior"], "scope": pair["scope"]}
                valid_pairs[metric] = simple
                value.update(calculation=change(simple["current"]["value"], simple["prior"]["value"]), pair=simple)
                paired += 1
            metrics[metric] = value
        sections.append({"accession": accession, "form": section["form"], "report_date": section["report_date"], "metrics": metrics, "derived": _derived(valid_pairs)})
    return {"sections": sections, "coverage": {"expected_pairs": expected, "reviewed_pairs": paired, "withheld_pairs": expected - paired, **state["withheld_review_counts"]},
        "qoq": "withheld_prior_quarter_reviews_not_collected", "ttm": "withheld_full_comparable_period_chain_not_collected", "score": None, "historical_original_vintage_verified": False}
