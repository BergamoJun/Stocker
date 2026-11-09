"""Decimal factor calculations and a bridge to the unchanged fixed simulation."""

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from research import peak_simulation, trade_screen
from research.rights import as_utc
from research.swing_models import Bar, XnysCalendar, atr14


def number(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)): raise ValueError("INVALID_FACTOR_NUMBER")
    try: result = Decimal(str(value))
    except InvalidOperation: raise ValueError("INVALID_FACTOR_NUMBER") from None
    if not result.is_finite(): raise ValueError("NONFINITE_FACTOR_NUMBER")
    return result


def clip(value, low=-1, high=1): return max(Decimal(low), min(Decimal(high), number(value)))
def sign(value): return Decimal(1 if value > 0 else -1 if value < 0 else 0)
def decimal_text(value):
    parsed = number(value)
    return "0" if parsed == 0 else format(parsed, "f")


def factor(key, settings, value=None, *, raw=None, source_ids=None, reason=None):
    spec = settings["features"][key]
    low, high = map(number, spec["range"])
    if value is not None and (not low <= number(value) <= high or not source_ids): raise ValueError("FACTOR_VALUE_OR_PROVENANCE_INVALID")
    if value is None and not reason: raise ValueError("MISSING_FACTOR_REQUIRES_REASON")
    with localcontext() as context:
        context.prec = 60
        points = decimal_text(number(spec["weight"]) * number(value)) if value is not None else None
    return {"id": key, "group": spec["group"], "label": spec["label"], "weight": spec["weight"], "formula": spec["formula"],
        "status": "available" if value is not None else "unavailable", "value": decimal_text(value) if value is not None else None,
        "raw": raw or {}, "source_ids": sorted(set(source_ids or [])), "reason_code": reason,
        "signed_points": points}


def price_history(row, evidence_cutoff, generated_at, settings):
    if not row or row["status"] != "available": raise ValueError("CURRENT_MARKET_INPUT_UNAVAILABLE")
    data = row["history"]
    if data["currency"] != "USD" or data["quote_kind"] != "eod" or data["adjustment"] != "split_adjusted_current_share_basis" or data["dividend_adjusted"] is not False:
        raise ValueError("MARKET_UNIT_OR_ADJUSTMENT_INVALID")
    bars = [Bar(b["session"], *[number(b[k]) for k in ("open", "high", "low", "close", "volume")], b["available_at"]) for b in data["bars"]]
    if len(bars) < settings["minimum_history_sessions"] or any(as_utc(b.available_at) >= as_utc(evidence_cutoff).replace(microsecond=0) for b in bars):
        raise ValueError("INSUFFICIENT_OR_FUTURE_PRICE_HISTORY")
    calendar = XnysCalendar((date.fromisoformat(bars[0].session) - timedelta(days=10)).isoformat(), (date.fromisoformat(bars[-1].session) + timedelta(days=40)).isoformat())
    if bars[-1].session != calendar.last_completed(generated_at) or row["quote"]["session"] != bars[-1].session or number(row["quote"]["close"]) != bars[-1].close:
        raise ValueError("STALE_OR_MISMATCHED_REFERENCE_PRICE")
    recent = bars[-253:]
    if [b.session for b in recent] != calendar.sessions(recent[0].session, recent[-1].session): raise ValueError("NONCONTIGUOUS_PRICE_HISTORY")
    return bars, calendar


def numeric(bundle, generated_at, settings):
    ai, local = bundle["ai_input"], bundle["local_input"]
    result, histories, reasons = {}, {}, []
    market = local.get("market", {})
    for role in ("stock", "market", "sector"):
        try: histories[role] = price_history(market.get("roles", {}).get(role), ai["as_of"], generated_at, settings)
        except (ValueError, KeyError, TypeError): reasons.append("UNUSABLE_" + role.upper() + "_HISTORY")
    def put(key, value=None, raw=None, ids=None, reason=None): result[key] = factor(key, settings, value, raw=raw, source_ids=ids, reason=reason)
    def ids(role): return market["roles"][role]["source_ids"]
    stock = histories.get("stock")
    forecast = screen = None
    anchors = {k: number(v) for k, v in settings["anchors"].items()}
    if stock:
        bars, calendar = stock
        close, prior = bars[-1].close, bars[-2].close
        atr = atr14(bars); sma20 = sum((b.close for b in bars[-20:]), Decimal(0)) / 20
        sma60 = sum((b.close for b in bars[-60:]), Decimal(0)) / 60
        distance = (close - sma20) / atr
        ret5 = (close / bars[-6].close - 1) * 100
        volume_mean = sum((b.volume for b in bars[-21:-1]), Decimal(0)) / 20
        put("trend_distance", clip(distance), {"close": str(close), "sma20": str(sma20), "atr14": str(atr)}, ids("stock"))
        put("trend_structure", clip((sma20 - sma60) / atr), {"sma20": str(sma20), "sma60": str(sma60), "atr14": str(atr)}, ids("stock"))
        put("momentum_five", clip(ret5 / (atr / close * 100 * Decimal(5).sqrt())), {"return5_pct": str(ret5), "atr14": str(atr), "close": str(close)}, ids("stock"))
        if volume_mean > 0:
            put("volume_confirmation", sign(close - prior) * clip(bars[-1].volume / volume_mean - 1, 0, 1), {"volume": str(bars[-1].volume), "prior20mean": str(volume_mean)}, ids("stock"))
        else: put("volume_confirmation", reason="ZERO_PRIOR_VOLUME")
        put("overextension", -clip((distance - anchors["extension_atr_free"]) / anchors["extension_atr_span"], 0, 1), {"distance_sma20_atr": str(distance)}, ids("stock"))
        try:
            forecast = peak_simulation.forecast(bars, calendar, generated_at)
            screen = trade_screen.assess(forecast, as_of=generated_at, source_ids=ids("stock"), is_illustrative=ai["is_illustrative"], mode="current_research")
            if forecast["plan"] and screen["metrics"]:
                m = next(m for m in screen["metrics"] if m["cost_bps_per_side"] == settings["primary_cost_bps_per_side"])
                put("net_target_upside", clip(number(m["net_reference_to_target_pct"]) / anchors["net_upside_pct"]), {"net_target_pct": m["net_reference_to_target_pct"], "cost_bps_per_side": m["cost_bps_per_side"]}, ids("stock"))
                put("path_ev_lower", clip(number(m["estimated_path_ev_pct_bounds"][0]) / anchors["path_ev_pct"]), {"model_path_ev_pct_bounds": m["estimated_path_ev_pct_bounds"], "note": "daily-order bound, not statistical confidence interval"}, ids("stock"))
                put("net_reward_risk", clip((number(m["net_reward_risk"]) - anchors["reward_risk_neutral"]) / anchors["reward_risk_span"]), {"net_reward_risk": m["net_reward_risk"], "cost_bps_per_side": m["cost_bps_per_side"]}, ids("stock"))
                mass = number(forecast["peak"]["estimated_modal_bin_mass"])
                put("peak_concentration", 2 * mass - 1, {"simulated_modal_bin_mass": str(mass), "calibrated_probability": None}, ids("stock"))
        except (ValueError, KeyError, TypeError, OverflowError): reasons.append("FIXED_SIMULATION_UNAVAILABLE")
    for role in ("market", "sector"):
        if role in histories:
            bars, _ = histories[role]; average = sum((b.close for b in bars[-20:]), Decimal(0)) / 20; atr = atr14(bars)
            put(role + "_trend", clip((bars[-1].close - average) / atr), {"close": str(bars[-1].close), "sma20": str(average), "atr14": str(atr)}, ids(role))
        if stock and role in histories:
            a, b = stock[0][-6:], histories[role][0][-6:]
            if [x.session for x in a] == [x.session for x in b]:
                difference = ((a[-1].close / a[0].close - 1) - (b[-1].close / b[0].close - 1)) * 100
                put("relative_" + role, clip(difference / anchors["relative_return_pp"]), {"difference_percentage_points": str(difference), "sessions": 5, "start": a[0].session, "end": a[-1].session}, ids("stock") + ids(role))
    financial_factors(ai, generated_at, settings, put)
    for key, spec in settings["features"].items():
        if spec["group"] == "latest_events" or key in result: continue
        put(key, reason="NO_USABLE_INPUT_FOR_" + key.upper())
    return result, {"forecast": forecast, "cost_risk_screen": screen, "reason_codes": reasons, "observed_price_roles": sorted(histories)}


def financial_factors(ai, generated_at, settings, put):
    facts = ai["financial_facts"]
    def latest(metric, bases, scopes):
        eligible = [f for f in facts if f["metric"] == metric and f["basis"] in bases and f["scope"] in scopes and f["unit"] == "USD" and as_utc(f["available_at"]) < as_utc(ai["as_of"]).replace(microsecond=0)]
        eligible = [f for f in eligible if 0 <= (as_utc(generated_at).date() - date.fromisoformat(f["end"])).days <= (settings["max_annual_age_days"] if f["basis"] == "annual" else settings["max_quarter_age_days"])]
        if not eligible: return None
        end = max(f["end"] for f in eligible); recent = [f for f in eligible if f["end"] == end]
        if len({(f["value"], f["start"], f["end"], f["scope"], f["basis"]) for f in recent}) != 1: return None
        return recent[0]
    def same(a, b): return a and b and all(a[k] == b[k] for k in ("basis", "start", "end", "unit", "scope"))
    def provenance(*values): return [f["fact_id"] for f in values]
    consolidated = {"consolidated"}
    revenue = latest("revenue", {"quarter", "annual"}, consolidated); operating = latest("operating_income", {"quarter", "annual"}, consolidated)
    if same(revenue, operating) and number(revenue["value"]) > 0:
        margin = number(operating["value"]) / number(revenue["value"]) * 100
        put("operating_margin", clip(margin / number(settings["anchors"]["operating_margin_pct"])), {"margin_percent": str(margin), "basis": revenue["basis"], "start": revenue["start"], "end": revenue["end"]}, provenance(revenue, operating))
    ocf = latest("operating_cash_flow", {"cumulative_ytd", "annual"}, consolidated); capex = latest("capital_expenditure", {"cumulative_ytd", "annual"}, consolidated)
    if same(ocf, capex) and number(capex["value"]) >= 0:
        value = number(ocf["value"]) - number(capex["value"])
        put("free_cash_flow", sign(value), {"value": str(value), "unit": "USD", "basis": ocf["basis"], "start": ocf["start"], "end": ocf["end"]}, provenance(ocf, capex))
    cash = latest("cash", {"instant"}, consolidated); liabilities = latest("liabilities", {"instant"}, consolidated); assets = latest("assets", {"instant"}, consolidated)
    if same(cash, liabilities) and number(cash["value"]) >= 0 and number(liabilities["value"]) > 0:
        ratio = number(cash["value"]) / number(liabilities["value"])
        put("cash_coverage", clip((ratio - number(settings["anchors"]["cash_coverage_neutral"])) / number(settings["anchors"]["cash_coverage_span"])), {"cash_to_total_liabilities": str(ratio), "end": cash["end"]}, provenance(cash, liabilities))
    if same(liabilities, assets) and number(liabilities["value"]) >= 0 and number(assets["value"]) > 0:
        ratio = number(liabilities["value"]) / number(assets["value"]); anchor = number(settings["anchors"]["liability_ratio_neutral"])
        put("liability_burden", clip((anchor - ratio) / anchor), {"total_liabilities_to_assets": str(ratio), "end": assets["end"]}, provenance(liabilities, assets))
    earnings = latest("net_income", {"quarter", "annual"}, {"consolidated", "parent_attributable"})
    if earnings:
        put("earnings_sign", sign(number(earnings["value"])), {"value": earnings["value"], "unit": "USD", "scope": earnings["scope"], "basis": earnings["basis"], "start": earnings["start"], "end": earnings["end"]}, provenance(earnings))
