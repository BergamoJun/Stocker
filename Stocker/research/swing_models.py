"""Candidate swing mathematics and chronological evaluation, not released models."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, localcontext
from importlib.metadata import version
from pathlib import Path

import exchange_calendars as xcals

from .rights import as_utc, price_uses, require_entitlement
from .policy import candidate_policy


@dataclass(frozen=True)
class Bar:
    session: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    available_at: str


class XnysCalendar:
    def __init__(self, start, end):
        self.calendar = xcals.get_calendar("XNYS", start=start, end=end)
        self.id = "XNYS"
        self.version = "exchange_calendars-" + version("exchange_calendars")

    def close(self, session):
        return self.calendar.session_close(session).isoformat().replace("+00:00", "Z")

    def sessions(self, start, end):
        return [item.date().isoformat() for item in self.calendar.sessions_in_range(start, end)]

    def last_completed(self, as_of):
        cutoff = as_utc(as_of)
        days = self.sessions((cutoff.date() - timedelta(days=20)).isoformat(), cutoff.date().isoformat())
        completed = [day for day in days if as_utc(self.close(day)) <= cutoff]
        if not completed:
            raise ValueError("No completed exchange session in covered calendar range")
        return completed[-1]

    def review_window(self, reference):
        start = date.fromisoformat(reference) + timedelta(days=1)
        following = self.sessions(start.isoformat(), (start + timedelta(days=25)).isoformat())[:5]
        if len(following) != 5:
            raise ValueError("Calendar does not cover five future sessions")
        return {"holding_sessions": 5, "calendar_id": self.id, "calendar_version": self.version,
                "sessions": following, "expires_at": self.close(following[0]),
                "review_at": self.close(following[-1])}


def load_price_snapshot(path: Path, entitlement, as_of, scope="local_research"):
    return _load_price_snapshot(path, entitlement, as_of, scope, require_current=True)


def load_diagnostic_snapshot(path: Path, entitlement, as_of):
    """Historical input inspection only; never authorizes a current price plan."""
    return _load_price_snapshot(path, entitlement, as_of, "local_research", require_current=False)


def _load_price_snapshot(path, entitlement, as_of, scope, *, require_current):
    data = json.loads(path.read_text(encoding="utf-8"))
    snapshot_version = data.get("schema_version", "1.0.0")
    if snapshot_version not in {"1.0.0", "1.1.0"}:
        raise ValueError("Unsupported price snapshot contract version")
    if snapshot_version == "1.1.0" and data.get("volume_representation") != "exact_decimal_string_shares":
        raise ValueError("Decimal-volume snapshot requires an explicit representation")
    if not isinstance(data.get("is_illustrative"), bool) or not data.get("symbol") or not data.get("snapshot_id"):
        raise ValueError("Explicit symbol, snapshot identity and data classification required")
    synthetic = data.get("is_illustrative") is True
    require_entitlement(entitlement, price_uses(scope), as_of, synthetic=synthetic)
    if data.get("provider") != entitlement["provider"] or data.get("entitlement_id") != entitlement["id"]:
        raise ValueError("Price provider/entitlement identity mismatch")
    if data.get("currency") != "USD" or data.get("adjustment") != "split_adjusted_current_share_basis":
        raise ValueError("Require consistent USD split-adjusted current-share-basis OHLCV")
    if data.get("exchange") != "XNYS" or not data.get("source_ids") or not data.get("snapshot_id"):
        raise ValueError("Require verified exchange, snapshot and source IDs")
    if not isinstance(data.get("point_in_time_verified"), bool):
        raise ValueError("Explicit point-in-time classification required")
    bars = []
    for item in data["bars"]:
        if any(not isinstance(item.get(key), str) for key in ("open", "high", "low", "close")):
            raise ValueError("OHLC prices must be exact decimal strings")
        prices = [Decimal(item[key]) for key in ("open", "high", "low", "close")]
        if any(not value.is_finite() or value <= 0 for value in prices):
            raise ValueError("Prices must be positive finite decimals")
        opening, high, low, closing = prices
        if not low <= min(opening, closing) <= max(opening, closing) <= high:
            raise ValueError("Invalid OHLC envelope")
        raw_volume = item["volume"]
        if ((snapshot_version == "1.0.0" and type(raw_volume) is not int) or
                (snapshot_version == "1.1.0" and not isinstance(raw_volume, str))):
            raise ValueError("Invalid volume representation for price snapshot version")
        try:
            volume = Decimal(raw_volume)
        except InvalidOperation:
            raise ValueError("Invalid volume") from None
        if not volume.is_finite() or volume < 0:
            raise ValueError("Invalid volume")
        if as_utc(item["available_at"]) > as_utc(as_of):
            raise ValueError("Future/unavailable price observation")
        bars.append(Bar(item["session"], opening, high, low, closing,
                        volume, item["available_at"]))
    if len(bars) < 60 or [bar.session for bar in bars] != sorted({bar.session for bar in bars}):
        raise ValueError("Require at least 60 unique chronological daily bars")
    start = (date.fromisoformat(bars[0].session) - timedelta(days=30)).isoformat()
    end = (as_utc(as_of).date() + timedelta(days=40)).isoformat()
    calendar = XnysCalendar(start, end)
    if [bar.session for bar in bars] != calendar.sessions(bars[0].session, bars[-1].session):
        raise ValueError("Missing/non-trading sessions; calendar gaps cannot be silently ignored")
    if any(as_utc(bar.available_at) < as_utc(calendar.close(bar.session)) for bar in bars):
        raise ValueError("A completed daily bar cannot be known before its regular-session close")
    if require_current and bars[-1].session != calendar.last_completed(as_of):
        raise ValueError("Latest completed session is missing; price plan stale")
    return data, bars, calendar


def atr14(bars):
    if len(bars) < 15:
        raise ValueError("ATR14 requires a prior close and fourteen true ranges")
    ranges = [max(row.high - row.low, abs(row.high - previous.close), abs(row.low - previous.close))
              for previous, row in zip(bars, bars[1:])]
    with localcontext() as context:
        context.prec = 34
        result = sum(ranges[:14], Decimal(0)) / 14
        for value in ranges[14:]:
            result = (result * 13 + value) / 14
    if result <= 0:
        raise ValueError("No usable positive volatility estimate")
    return result


def candidate_plan(bars, calendar, as_of):
    cutoff = as_utc(as_of)
    settings = candidate_policy()["target"]
    eligible = [bar for bar in bars if as_utc(bar.available_at) <= cutoff]
    if len(eligible) < settings["minimum_completed_sessions"] or eligible[-1].session != calendar.last_completed(as_of):
        raise ValueError("Insufficient current point-in-time daily history")
    return _reference_baseline(eligible, calendar, as_of)


def _reference_baseline(eligible, calendar, as_of):
    """Arithmetic only. Caller must enforce current or diagnostic use explicitly."""
    settings = candidate_policy()["target"]
    if len(eligible) < settings["minimum_completed_sessions"]:
        raise ValueError("Insufficient baseline history")
    with localcontext() as context:
        context.prec = 34
        reference, atr = eligible[-1].close, atr14(eligible)
        target = (reference + Decimal(settings["target_atr_multiple"]) * atr).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
        invalidation = (reference - Decimal(settings["invalidation_atr_multiple"]) * atr).quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
        if not target > reference > invalidation > 0:
            raise ValueError("Invalid long reference-price scenario")
        ratio = (target - reference) / (reference - invalidation)
        return {"status": "research_only_unvalidated", "released_price_plan": None,
                "methodology_id": "atr14-reference-baseline-v1", "methodology_version": "0.1.0",
                "reference_session": eligible[-1].session, "as_of": as_of,
                "reference_price": str(reference), "atr14": str(atr),
                "first_target_price": str(target), "invalidation_price": str(invalidation),
                "reward_risk_ratio": str(ratio),
                "upside_pct": str((target / reference - 1) * 100),
                "downside_pct": str((1 - invalidation / reference) * 100),
                "timing": {**calendar.review_window(eligible[-1].session), "expected_target_sessions": None},
                "limitation": "Fixed ATR benchmark; chosen multipliers are not calibrated evidence of profitability"}


def replay_five_sessions(plan, future, calendar, cost_bps_per_side=Decimal("10")):
    if [bar.session for bar in future] != plan["timing"]["sessions"]:
        raise ValueError("Evaluation requires exactly the five scheduled future sessions")
    target, stop = Decimal(plan["first_target_price"]), Decimal(plan["invalidation_price"])
    entry = future[0].open
    base = {"reference_session": plan["reference_session"], "label_available_at": calendar.close(future[-1].session),
            "entry": str(entry), "target": str(target), "stop": str(stop), "hit_session": None,
            "net_return_pct": None}
    if not stop < entry < target:
        return {**base, "outcome": "untradeable_open_gap"}
    fee = Decimal(cost_bps_per_side) / 10000
    if not Decimal(0) <= fee < Decimal("0.1"):
        raise ValueError("Explicit bounded per-side cost required")
    outcome, exit_price, hit_session = "timeout", future[-1].close, None
    for index, bar in enumerate(future, 1):
        if bar.open <= stop:
            outcome, exit_price = "stop", bar.open
        elif bar.open >= target:
            outcome, exit_price = "target", target
        elif bar.low <= stop and bar.high >= target:
            return {**base, "outcome": "ambiguous_same_bar"}
        elif bar.low <= stop:
            outcome, exit_price = "stop", stop
        elif bar.high >= target:
            outcome, exit_price = "target", target
        else:
            continue
        hit_session = index if outcome == "target" else None
        break
    with localcontext() as context:
        context.prec = 34
        net = ((exit_price * (1 - fee)) / (entry * (1 + fee)) - 1) * 100
    return {**base, "outcome": outcome, "hit_session": hit_session, "exit_price": str(exit_price),
            "net_return_pct": str(net), "cost_bps_per_side": str(cost_bps_per_side)}


def walk_forward_cases(bars, calendar):
    cases = []
    # Non-overlapping label windows. Each plan sees only history available then.
    for index in range(59, len(bars) - 5, 5):
        cutoff = bars[index].available_at
        plan = candidate_plan(bars[:index + 1], calendar, cutoff)
        cases.append(replay_five_sessions(plan, bars[index + 1:index + 6], calendar))
    split = int(len(cases) * 0.8)
    return {"status": "experimental_unvalidated", "release_validation_id": None,
            "method": "chronological expanding history; five-session non-overlapping labels",
            "training_cases": cases[:split], "held_out_cases": cases[split:],
            "acceptance_criteria": "Must be pre-registered before real-data evaluation; no automatic pass"}


def timing_candidate(cases, as_of):
    settings = candidate_policy()["timing"]
    eligible = [case for case in cases if as_utc(case["label_available_at"]) <= as_utc(as_of)]
    resolved = [case for case in eligible if case["outcome"] in {"target", "stop", "timeout"}]
    hits = sorted(case["hit_session"] for case in resolved if case["outcome"] == "target")
    result = {"status": "research_only_unvalidated", "released_expected_target_sessions": None,
              "prior_resolved_cases": len(resolved), "prior_target_hits": len(hits),
              "ambiguous_or_untradeable": len(eligible) - len(resolved),
              "experimental_target_hit_sessions": None,
              "conditioning": "Among past target-hit cases only; not an unconditional sale-date forecast"}
    if len(resolved) >= settings["minimum_prior_resolved_cases"] and len(hits) >= settings["minimum_prior_target_hits"]:
        result["experimental_target_hit_sessions"] = {
            "min": hits[max(0, math.ceil(len(hits) * settings["quantiles"][0]) - 1)],
            "max": hits[max(0, math.ceil(len(hits) * settings["quantiles"][1]) - 1)]}
    return result


def attractiveness_candidate(features, event_signal):
    weights = {key: value for key, value in candidate_policy()["attractiveness"]["weights"].items()
               if key != "latest_events"}
    if event_signal.get("status") != "research_only_unvalidated":
        return {"status": "unavailable", "score": None, "experimental_score": None, "reason": "Latest-event component unavailable"}
    contributions = {}
    for key, weight in weights.items():
        feature = features.get(key)
        if (not isinstance(feature, dict) or not feature.get("source_ids") or
                not feature.get("methodology_id") or feature.get("value") is None):
            return {"status": "unavailable", "score": None, "experimental_score": None,
                    "reason": f"Missing versioned normalized feature: {key}; weights not redistributed"}
        value = Decimal(str(feature["value"]))
        if not value.is_finite() or not 0 <= value <= 1:
            raise ValueError("Normalized features must be finite in [0,1]")
        contributions[key] = Decimal(weight) * value
    contributions["latest_events"] = Decimal(event_signal["experimental_component"])
    total = max(Decimal(1), min(Decimal(100), sum(contributions.values())))
    return {"status": "research_only_unvalidated", "score": None,
            "experimental_score": int(total.quantize(Decimal(1), rounding=ROUND_HALF_UP)),
            "experimental_raw_score": str(total),
            "contributions": {key: str(value) for key, value in contributions.items()},
            "reason": "Candidate weights/normalization require held-out validation; never interpret as win probability"}


def normalize_candidate_features(bars, market_bars, sector_bars, financial_metrics,
                                 source_ids, plan, as_of, filing_published_at):
    """Explicit experimental rubric. Inputs must already pass their source gates."""
    cutoff = as_utc(as_of)
    filing_age = (cutoff - as_utc(filing_published_at)).total_seconds() / 86400
    maximum_age = candidate_policy()["attractiveness"]["normalization_rules"]["maximum_filing_age_days_proposed"]
    if not 0 <= filing_age <= maximum_age:
        raise ValueError("Financial feature publication is future-dated or too old for this candidate")
    for history in (bars, market_bars, sector_bars):
        if len(history) < 60 or any(as_utc(bar.available_at) > cutoff for bar in history):
            raise ValueError("Features require point-in-time history without future bars")
        if history[-1].session != bars[-1].session:
            raise ValueError("Benchmark and stock reference sessions differ")
    if not source_ids or set(source_ids) != {"prices", "fundamentals", "market", "sector"}:
        raise ValueError("Explicit provenance required for all feature families")
    if any(not ids for ids in source_ids.values()):
        raise ValueError("Empty feature source IDs")
    metrics = {item["name"]: item["value"] for item in financial_metrics}
    financial_keys = ("revenue_q3_yoy_pct", "operating_margin_q3_pct", "free_cash_flow_9m_musd")
    if any(metrics.get(key) is None for key in financial_keys):
        raise ValueError("Missing required verified financial feature")
    with localcontext() as context:
        context.prec = 34
        close = bars[-1].close
        average20 = sum((bar.close for bar in bars[-20:]), Decimal(0)) / 20
        average60 = sum((bar.close for bar in bars[-60:]), Decimal(0)) / 60
        prior_volume20 = sum(bar.volume for bar in bars[-21:-1]) / Decimal(20)
        votes = [close > average20 > average60, close > bars[-6].close,
                 close > bars[-2].close and bars[-1].volume >= prior_volume20]
        price_value = Decimal(sum(votes)) / 3
        fundamental_value = Decimal(sum(Decimal(str(metrics[key])) > 0 for key in financial_keys)) / 3
        sector_votes = [history[-1].close > sum((row.close for row in history[-20:]), Decimal(0)) / 20
                        for history in (market_bars, sector_bars)]
        market_value = Decimal(sum(sector_votes)) / 2
        rr = Decimal(plan["reward_risk_ratio"])
        risk_value = max(Decimal(0), min(Decimal(1), (rr - 1) / 2))
    rubric = candidate_policy()["attractiveness"]["normalization_registry"]
    values = {"price_volume": (price_value, "prices"), "fundamentals": (fundamental_value, "fundamentals"),
              "market_sector": (market_value, "market"), "risk_reward": (risk_value, "prices")}
    return {key: {"value": str(value), "methodology_id": rubric,
                  "source_ids": (source_ids[family] + source_ids["sector"] if key == "market_sector" else source_ids[family])}
            for key, (value, family) in values.items()}
