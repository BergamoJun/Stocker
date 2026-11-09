"""ILLUSTRATIVE — NOT LIVE MARKET DATA. Correlated artificial outcome records."""

from decimal import Decimal
import random

from .historical_panel import references


def dataset():
    refs = references()
    profiles, sectors, cases = [], [], []
    rng = random.Random(20261108)
    for sector_id, name in (("3", "가상 산업 A"), ("6", "가상 산업 B")):
        sectors.append({"sector_id": sector_id, "name_ko": name, "selected_unique_issuers": 100})
        assets = [{"symbol": f"T{sector_id}X{i}", "cik": f"{int(sector_id) * 1000 + i:010d}", "sector_id": sector_id,
            "symbol_partition": "development_and_validation" if i < 80 else "unseen_symbol_final_only"} for i in range(100)]
        profiles.extend(assets)
        shock = 0.0
        for index, ref in enumerate(refs):
            shock = .75 * shock + rng.gauss(0, .12)
            for asset in assets[:80]:
                baseline = Decimal(str(.2 + shock + rng.gauss(0, .15)))
                excess = Decimal(str((.45 if sector_id == "3" else -.35) + shock + rng.gauss(0, .04)))
                ambiguous = index % 11 == 0
                bounds = [Decimal("-1.2"), Decimal("1.5")] if ambiguous else [baseline + excess] * 2
                scenarios = []
                for cost in ("10", "25"):
                    penalty = Decimal(cost) / 50
                    scenarios.append({"cost_bps_per_side": cost, "unfiltered_outcome": {"outcome": "ambiguous_same_bar" if ambiguous else "timeout", "hit_session": None},
                        "unfiltered_net_return_pct_bounds": [str(value - penalty) for value in bounds],
                        "screened_net_return_pct_bounds": [str(value - penalty) for value in bounds],
                        "five_session_hold_net_return_pct": str(baseline - penalty), "target_timing_evaluable": False,
                        "target_hit_inside_conditional_timing_range": None})
                cases.append({**ref, "symbol": asset["symbol"], "cik": asset["cik"], "sector_id": sector_id,
                    "reference_month": ref["reference_session"][:7], "status": "retrospective_diagnostic_not_validation",
                    "reason_code": None, "screen_decision": "numerical_candidate", "absolute_peak_error_atr": abs(shock) + .5,
                    "peak_day_absolute_error": index % 3, "modal_price_bin_hit": index % 4 == 0,
                    "volatility_group": "atr_2_to_4pct", "liquidity_group": "dollar_volume_at_least_10m", "cost_scenarios": scenarios})
    return {"status": "ready", "source_kind": "synthetic", "is_illustrative": True,
        "source_ids": ["illustrative-correlated-outcomes-v1"], "source_progress": {"status": "illustrative_not_a_provider_run"},
        "profiles": profiles, "sectors": sectors, "references": refs, "cases": cases, "sources": [],
        "fixture_label": "ILLUSTRATIVE — NOT LIVE MARKET DATA"}
