"""Independent deterministic audits of the weighted ledger and source boundaries."""

from decimal import Decimal, ROUND_HALF_UP, localcontext
from .weighted_features import number, clip


def mapped(points):
    return int(clip(Decimal(50) + number(points) / 2, 1, 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def score(factors, settings):
    with localcontext() as context:
        context.prec = 60
        return _score(factors, settings)


def _score(factors, settings):
    if set(factors) != set(settings["features"]): raise ValueError("FACTOR_SET_MISMATCH")
    if any(row["id"] != key for key, row in factors.items()): raise ValueError("FACTOR_IDENTITY_CHANGED")
    groups = {}; known = lower = upper = Decimal(0); observed_weight = Decimal(0); missing = []
    for group, budget in settings["groups"].items():
        rows = [factors[k] for k, s in settings["features"].items() if s["group"] == group]
        if sum((number(row["weight"]) for row in rows), Decimal(0)) != number(budget): raise ValueError("GROUP_WEIGHT_SUM_CHANGED")
        group_known = group_low = group_high = Decimal(0); group_missing = Decimal(0)
        for row in rows:
            spec = settings["features"][row["id"]]
            if row["group"] != spec["group"] or row["weight"] != spec["weight"] or row["label"] != spec["label"] or row["formula"] != spec["formula"]: raise ValueError("FACTOR_COEFFICIENT_OR_DEFINITION_CHANGED")
            low, high = map(number, spec["range"]); weight = number(spec["weight"])
            if row["status"] == "available":
                if row["value"] is None or not low <= number(row["value"]) <= high or not row["source_ids"] or row["reason_code"] is not None: raise ValueError("AVAILABLE_FACTOR_INVALID")
                points = weight * number(row["value"])
                if number(row["signed_points"]) != points: raise ValueError("FACTOR_ARITHMETIC_CHANGED")
                group_known += points; group_low += points; group_high += points; observed_weight += weight
            elif row["status"] == "unavailable":
                if row["value"] is not None or row["signed_points"] is not None or not row["reason_code"]: raise ValueError("MISSING_FACTOR_CANNOT_BE_NEUTRAL")
                missing.append(row["id"]); group_missing += weight; group_low += weight * low; group_high += weight * high
            else: raise ValueError("UNKNOWN_FACTOR_STATUS")
        groups[group] = {"maximum_weight": budget, "known_signed_points": str(group_known), "missing_weight": str(group_missing), "completion_points_bounds": [str(group_low), str(group_high)]}
        known += group_known; lower += group_low; upper += group_high
    if sum(settings["groups"].values()) != 100: raise ValueError("TOTAL_COEFFICIENT_BUDGET_CHANGED")
    return {"status": "research_only_unvalidated" if not missing else "withheld_missing_factors", "experimental_score": mapped(known) if not missing else None,
        "released_score": None, "calibrated_probability": None, "known_signed_points": str(known), "observed_weight": str(observed_weight), "missing_weight": str(100 - observed_weight),
        "completion_score_bounds": [mapped(lower), mapped(upper)], "bounds_meaning": "Algebraic range over unknown factors; not a confidence or predictive interval", "missing_factors": missing, "groups": groups}


def critique(factors, assessment, numeric, events, ai, settings):
    if score(factors, settings) != assessment: raise ValueError("AGGREGATION_REPLAY_CHANGED")
    sources = {f["fact_id"] for f in ai["financial_facts"]} | {d["source_id"] for d in ai["official_documents"]}
    # Market source identifiers are passed separately by the validated caller.
    sources |= set(numeric.get("market_source_ids", []))
    for row in factors.values():
        if row["status"] == "available" and not set(row["source_ids"]) <= sources: raise ValueError("UNKNOWN_FACTOR_SOURCE")
    findings = [{"code": "MODEL_NOT_EMPIRICALLY_VALIDATED", "severity": "block_release", "message": "계수와 통계 모형은 실증 검증 전입니다. 정식 추천도·승률을 제공하지 않습니다."}]
    for key in assessment["missing_factors"]:
        findings.append({"code": "MISSING_" + key.upper(), "severity": "block_total", "message": factors[key]["label"] + ": " + factors[key]["reason_code"]})
    screen = numeric.get("cost_risk_screen")
    if screen is None or screen["screen_decision"] != "numerical_candidate":
        findings.append({"code": "COST_RISK_GATE_NOT_PASSED", "severity": "risk_gate", "message": "기존 비용·위험 조건을 통과하지 못했습니다. 다른 가점으로 이 조건을 해제하지 않습니다."})
    for key in ("path_ev_lower", "volume_confirmation", "overextension", "operating_margin", "free_cash_flow"):
        row = factors[key]
        if row["status"] == "available" and number(row["value"]) < 0:
            findings.append({"code": "NEGATIVE_" + key.upper(), "severity": "risk", "message": row["label"] + "에서 감점이 발생했습니다."})
    if events["events"]:
        findings.append({"code": "CONDITIONAL_BUSINESS_CHANNEL_NOT_PRICE_CAUSALITY", "severity": "limitation", "message": "자동 추출한 사업 경로는 조건부 해석입니다. 단기 주가 반응·시장 기대·발표 시각이 확인되지 않은 사건은 점수에 넣지 않습니다."})
    findings.append({"code": "CORRELATED_FEATURES_NOT_INDEPENDENT_ALPHA", "severity": "limitation", "message": "추세·상대 변화·모형 손익은 같은 가격을 일부 공유합니다. 변수 수를 독립 예측 증거 수로 세지 않습니다."})
    return {"method": "deterministic_ledger_source_and_risk_checks_v1", "findings": findings, "formal_recommendation_ready": False,
        "cost_risk_gate_passed": bool(screen and screen["screen_decision"] == "numerical_candidate"), "human_approved": False,
        "free_text_semantic_truth_proven": False, "released_score": None, "released_price_plan": None}
