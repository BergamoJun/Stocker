"""Typed additive period recipes. Missing legs never become zero."""

from datetime import date, timedelta
from decimal import Decimal, localcontext

from .sec_chain_sources import policy
from .sec_growth import change


class Withheld(ValueError):
    pass


def day(value):
    return date.fromisoformat(value)


def span(value):
    return (day(value["end"]) - day(value["start"])).days + 1


def amount(value):
    number = Decimal(value["value"])
    if not number.is_finite(): raise Withheld("nonfinite_amount")
    return number


def term(role, fact, coefficient=1):
    return {"role": role, "coefficient": coefficient, "value": fact["value"], "source_id": fact["source_id"], "fact_id": fact["fact_id"]}


def quarter(fact, role):
    if not policy()["quarter_days"][0] <= span(fact) <= policy()["quarter_days"][1]: raise Withheld("not_a_quarter_duration")
    return {"value": fact["value"], "start": fact["start"], "end": fact["end"], "kind": "direct_reported_quarter", "terms": [term(role, fact)]}


def subtract_quarter(a, b, a_role, b_role):
    if a["start"] != b["start"] or day(b["end"]) >= day(a["end"]): raise Withheld("cumulative_intervals_not_nested")
    start = (day(b["end"]) + timedelta(days=1)).isoformat()
    result = {"start": start, "end": a["end"]}
    if not policy()["quarter_days"][0] <= span(result) <= policy()["quarter_days"][1]: raise Withheld("residual_is_not_one_quarter")
    with localcontext() as context:
        context.prec = 160
        value = format(amount(a) - amount(b), "f")
    return {**result, "value": value, "kind": "derived_cumulative_difference", "terms": [term(a_role, a), term(b_role, b, -1)]}


def calculate(metric, legs, previous_is_annual):
    result = {"current_quarter": None, "previous_quarter": None, "qoq": None, "qoq_status": "missing_reviewed_legs",
        "ttm": None, "ttm_status": "missing_reviewed_legs", "unit": "USD"}
    if metric not in policy()["metrics"]: return {**result, "qoq_status": "non_additive_metric_not_supported", "ttm_status": "non_additive_metric_not_supported"}
    if not legs: return result
    if any(v["unit"] != "USD" or v["scope"] != next(iter(legs.values()))["scope"] or v["tag"] != next(iter(legs.values()))["tag"] for v in legs.values()):
        return {**result, "qoq_status": "incompatible_unit_scope_or_tag", "ttm_status": "incompatible_unit_scope_or_tag"}
    def get(role):
        if role not in legs: raise Withheld("missing_" + role)
        return legs[role]
    income = metric in {"revenue", "operating_income", "net_income"}
    try:
        if income:
            current = quarter(get("current_income_quarter"), "current_income_quarter")
        else:
            ytd = get("current_ytd")
            current = quarter(ytd, "current_ytd") if span(ytd) <= policy()["quarter_days"][1] else subtract_quarter(ytd, get("previous_ytd"), "current_ytd", "previous_ytd")
        if metric == "capital_expenditure" and Decimal(current["value"]) < 0: raise Withheld("negative_derived_spending_requires_review")
        result["current_quarter"] = current
    except Withheld as error: result["qoq_status"] = str(error)
    try:
        if previous_is_annual:
            previous = subtract_quarter(get("annual"), get("earlier_ytd"), "annual", "earlier_ytd")
        elif income:
            previous = quarter(get("previous_income_quarter"), "previous_income_quarter")
        else:
            ytd = get("previous_ytd")
            previous = quarter(ytd, "previous_ytd") if span(ytd) <= policy()["quarter_days"][1] else subtract_quarter(ytd, get("earlier_ytd"), "previous_ytd", "earlier_ytd")
        if metric == "capital_expenditure" and Decimal(previous["value"]) < 0: raise Withheld("negative_derived_spending_requires_review")
        result["previous_quarter"] = previous
    except Withheld as error: result["qoq_status"] = str(error)
    if result["current_quarter"] and result["previous_quarter"]:
        a, b = result["current_quarter"], result["previous_quarter"]
        if day(a["start"]) != day(b["end"]) + timedelta(days=1) or abs(span(a) - span(b)) > policy()["maximum_quarter_length_difference_days"]:
            result["qoq_status"] = "quarter_gap_or_noncomparable_duration"
        else:
            reconciled = True
            if income and not previous_is_annual and "current_ytd" in legs and "previous_ytd" in legs:
                ca, pr = legs["current_ytd"], legs["previous_ytd"]
                with localcontext() as context:
                    context.prec = 160
                    reconciled = ca["start"] == pr["start"] and day(a["start"]) == day(pr["end"]) + timedelta(days=1) and amount(ca) - Decimal(a["value"]) == amount(pr)
            if income and not previous_is_annual and "previous_ytd" in legs and "earlier_ytd" in legs:
                pr, earlier = legs["previous_ytd"], legs["earlier_ytd"]
                if pr["start"] == earlier["start"] and day(b["start"]) == day(earlier["end"]) + timedelta(days=1):
                    with localcontext() as context:
                        context.prec = 160
                        reconciled = reconciled and amount(pr) - Decimal(b["value"]) == amount(earlier)
            if reconciled:
                result.update(qoq=change(a["value"], b["value"]), qoq_status="calculated")
            else: result["qoq_status"] = "income_overlap_rounding_or_vintage_difference_requires_review"
    try:
        annual, current, prior = get("annual"), get("current_ytd"), get("prior_year_ytd")
        if not policy()["annual_days"][0] <= span(annual) <= policy()["annual_days"][1]: raise Withheld("annual_period_invalid")
        if prior["start"] != annual["start"] or day(current["start"]) != day(annual["end"]) + timedelta(days=1) or not day(prior["end"]) < day(annual["end"]):
            raise Withheld("ttm_intervals_do_not_cover_one_contiguous_year")
        if abs(span(current) - span(prior)) > 7 or not 358 <= (day(current["end"]) - day(prior["end"])).days <= 373: raise Withheld("ytd_periods_not_comparable")
        period = {"start": (day(prior["end"]) + timedelta(days=1)).isoformat(), "end": current["end"]}
        if not policy()["annual_days"][0] <= span(period) <= policy()["annual_days"][1]: raise Withheld("ttm_period_is_not_one_year")
        with localcontext() as context:
            context.prec = 160
            value = amount(annual) + amount(current) - amount(prior)
        if metric == "capital_expenditure" and value < 0: raise Withheld("negative_ttm_spending_requires_review")
        result.update(ttm={**period, "value": format(value, "f"), "kind": "annual_plus_ytd_minus_prior_ytd", "terms": [term("annual", annual), term("current_ytd", current), term("prior_year_ytd", prior, -1)]}, ttm_status="calculated")
    except Withheld as error: result["ttm_status"] = str(error)
    return result


def derived(metrics, scope):
    result = {}
    for horizon in ("current_quarter", "previous_quarter", "ttm"):
        values = {name: item[horizon] for name, item in metrics.items()}
        out = {"operating_margin_percent": None, "net_margin_percent": None, "free_cash_flow": None}
        def same(a, b): return a is not None and b is not None and all(a[k] == b[k] for k in ("start", "end"))
        revenue = values.get("revenue")
        with localcontext() as context:
            context.prec = 160
            for name, metric in (("operating_margin_percent", "operating_income"), ("net_margin_percent", "net_income")):
                income = values.get(metric)
                if same(revenue, income) and Decimal(revenue["value"]) > 0 and scope.get(metric) == scope.get("revenue") == "consolidated":
                    out[name] = format((Decimal(income["value"]) / Decimal(revenue["value"]) * 100).quantize(Decimal("0.000001")), "f")
            cash, capex = values.get("operating_cash_flow"), values.get("capital_expenditure")
            if same(cash, capex) and scope.get("operating_cash_flow") == scope.get("capital_expenditure") == "consolidated" and Decimal(capex["value"]) >= 0:
                out["free_cash_flow"] = format(Decimal(cash["value"]) - Decimal(capex["value"]), "f")
        result[horizon] = out
    return result
