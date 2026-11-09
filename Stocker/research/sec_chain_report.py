"""Quarter-on-quarter and trailing-year evidence, preserving earlier report versions."""

import argparse
import json
from pathlib import Path
import uuid

from . import sec_chain_math as math, sec_chain_review as review, sec_chain_sources as sources, sec_growth_report as previous, sec_refresh
from .local_files import atomic_json
from .rights import as_utc
from .sec_report import ROOT
from .sec_refresh_report import safe
from .sec_transport import now


LIMITATIONS = ["전분기 비교(QoQ)는 인접한 두 분기의 비교이고, 최근 12개월(TTM)은 직전 연간+올해 누적−전년 같은 누적의 계산값입니다.",
    "여러 공시의 정의·기간·회계 범위를 보조 도구 또는 사람이 대조한 기록입니다. 코드가 회계 해석의 정답이나 감사 의견을 증명하지는 않습니다.",
    "분기 현금흐름과 연말 4분기 손익은 누적값 차이로 계산할 수 있습니다. 직접 보고된 분기와 구분하며 누락·기간 공백·공시 간 반올림/수정 차이는 보류합니다.",
    "가중평균 주식수를 쓰는 EPS와 결산일 잔액을 차감·합산해 TTM으로 만들지 않습니다. EPS 누적과 이 단계의 잔액 비교는 지원하지 않습니다.",
    "현재 확보한 수정·재분류 자료를 과거에 알았던 값으로 소급하지 않습니다. TTM 증감률·목표가·매력도·학습 모델은 추가하지 않습니다."]


def chain_context(issuer, cutoff, *, root=None, packet_root=None, review_root=None, source_store=None, illustrative=False):
    view = review.view(issuer, cutoff, root=root, packet_root=packet_root, review_root=review_root, source_store=source_store, illustrative=illustrative)
    state = view["state"]
    packet = state["packet"]
    previous_annual = bool(packet and packet["plan"]["filings"].get("previous", {}).get("form") == "10-K")
    metrics, scopes, assessments = {}, {}, {}
    for metric, record in view["metrics"].items():
        chosen = record["chosen"]
        legs = chosen["components"] if chosen else {}
        scope = chosen["finding"]["scope"] if chosen else None
        scopes[metric] = scope
        calculation = math.calculate(metric, legs, previous_annual)
        for opinion in record["opinions"]:
            for assessment in opinion["amendment_assessments"]:
                assessments[sec_refresh.canonical_hash(assessment)] = assessment
        metrics[metric] = {**calculation, "review_status": record["status"], "scope": scope, "components": legs,
            "review_receipt_ids": sorted({o["receipt_id"] for o in record["opinions"]}),
            "rationale": " / ".join(sorted({o["finding"]["compatibility_rationale"] for o in record["opinions"]})) or None}
    return {"status": state["status"], "binding": state["binding"], "metrics": metrics, "derived": math.derived(metrics, scopes),
        "coverage": {"expected_metrics": 5, "qoq_calculated": sum(m["qoq_status"] == "calculated" for m in metrics.values()), "ttm_calculated": sum(m["ttm_status"] == "calculated" for m in metrics.values()), **view["rejected_review_counts"]},
        "filings": packet["plan"]["filings"] if packet else {}, "documents": packet["documents"] if packet else {},
        "packet_available_at": packet["available_at"] if packet else None, "amendment_assessments": list(assessments.values()), "historical_original_vintage_verified": False}


def build(issuer, cutoff, *, root=None, packet_root=None, review_root=None, period_root=None, source_store=None, illustrative=False, clock=now):
    base = previous.build(issuer, cutoff, root=period_root, review_root=review_root, source_store=source_store, illustrative=illustrative, clock=clock)
    chain = chain_context(issuer, cutoff, root=root, packet_root=packet_root, review_root=review_root, source_store=source_store, illustrative=illustrative)
    value = {"schema_version": "sec-reviewed-qoq-ttm-v1", "report_id": uuid.uuid4().hex, "label": "ILLUSTRATIVE — NOT LIVE MARKET DATA" if illustrative else "개인용 전분기·최근 12개월 재무 보고서",
        "is_illustrative": illustrative, "as_of": cutoff, "generated_at": clock(), "base_report": base, "chain": chain, "price_plan": None, "attractiveness": None, "limitations": list(LIMITATIONS),
        "provenance": {"method_hashes": review.methods(), "chain_root": str(review.namespace(root, illustrative).parent), "packet_root": str(sources.namespace(packet_root, illustrative).parent),
            "review_root": base["provenance"]["review_root"], "period_root": base["provenance"]["period_root"], "source_store": base["provenance"]["source_store"], "network_requests": 0, "external_llm_calls": 0, "public_release": False}}
    validate(value)
    return value


def validate(value):
    review.validate(value, "report")
    if value["provenance"]["method_hashes"] != review.methods(): raise ValueError("CHAIN_REPORT_METHOD_CHANGED")
    if as_utc(value["as_of"]) > as_utc(value["generated_at"]): raise ValueError("CHAIN_REPORT_TIME_INVALID")
    previous.validate(value["base_report"])
    base, p = value["base_report"], value["provenance"]
    if base["as_of"] != value["as_of"] or base["is_illustrative"] is not value["is_illustrative"]: raise ValueError("CHAIN_BASE_IDENTITY_CHANGED")
    if any(p[k] != base["provenance"][k] for k in ("review_root", "period_root", "source_store")): raise ValueError("CHAIN_REPORT_SOURCE_ROOT_CHANGED")
    issuer = base["base_report"]["context"]["cik"]
    expected = chain_context(issuer, value["as_of"], root=p["chain_root"], packet_root=p["packet_root"], review_root=p["review_root"], source_store=p["source_store"], illustrative=value["is_illustrative"])
    if expected != value["chain"] or value["limitations"] != LIMITATIONS: raise ValueError("CHAIN_REPORT_REPLAY_MISMATCH")
    label = "ILLUSTRATIVE — NOT LIVE MARKET DATA" if value["is_illustrative"] else "개인용 전분기·최근 12개월 재무 보고서"
    if value["label"] != label: raise ValueError("CHAIN_REPORT_LABEL_CHANGED")
    return value


def render(value):
    context = value["base_report"]["base_report"]["context"]
    chain = value["chain"]
    names = previous.financial_report.NAMES
    lines = ["# 전분기 비교·최근 12개월 재무 보고서", "", value["label"], "", f"기업: {safe(context['name'] or '자료 없음')} · CIK {context['cik']}", f"기준시각(UTC): {value['as_of']}", "",
        "확인한 과거 실적의 기간별 비교입니다. 주가 예측 성능이나 향후 수익을 평가한 결과는 아닙니다.", "",
        f"전분기 비교 계산 {chain['coverage']['qoq_calculated']}/5개, 최근 12개월 계산 {chain['coverage']['ttm_calculated']}/5개.", "",
        "금액은 USD 원 단위입니다. 전년 대비 근거는 같은 JSON의 기존 보고서 부분에 함께 보존합니다.", ""]
    if chain["status"] != "ready":
        lines += ["기간 자료 또는 현재 검토가 준비되지 않아 추가 계산을 보류했습니다. 기업 식별번호가 다른 전신 자료를 자동 합치지 않습니다.", f"보류 상태: `{chain['status']}`", ""]
    for assessment in chain["amendment_assessments"]:
        source = chain["documents"][assessment["accession"]]
        lines += [f"수정 공시 [원문]({source['source_url']})을 별도 대조해 선택한 재무 구성요소의 기간·태그·수치가 같음을 확인한 기록이 있습니다. 이는 수정 공시의 다른 위험·서술까지 변화가 없다는 판단은 아닙니다.", safe(assessment["rationale"]), ""]
    lines += ["| 항목 | 현재 분기 | 이전 분기 | 전분기 대비 | 최근 12개월 |", "|---|---:|---:|---:|---:|"]
    for metric, item in chain["metrics"].items():
        current, prior, ttm = item["current_quarter"], item["previous_quarter"], item["ttm"]
        qoq = item["qoq"]
        percent = qoq["percent_change"] + "%" if qoq and qoq["percent_change"] is not None else "이전 값 ≤ 0, 증감액 확인" if qoq else "보류"
        name = names[metric] + ("(지배기업 귀속)" if item["scope"] == "parent_attributable" else "")
        lines.append(f"| {name} | {current['value'] if current else '보류'} | {prior['value'] if prior else '보류'} | {percent} | {ttm['value'] if ttm else '보류'} |")
    lines += ["", "## 기간·산식과 보류 이유", ""]
    for metric, item in chain["metrics"].items():
        lines += [f"### {names[metric]}", ""]
        for field, label in (("current_quarter", "현재 분기"), ("previous_quarter", "이전 분기"), ("ttm", "최근 12개월")):
            part = item[field]
            if part:
                origin = "원문 직접 분기값" if part["kind"] == "direct_reported_quarter" else "연간+올해 누적−전년 누적" if field == "ttm" else "동일 회계연도 누적값 차이로 계산"
                lines.append(f"- {label}: {part['start']} ~ {part['end']} · {origin}.")
                for term in part["terms"]:
                    fact = item["components"][term["role"]]
                    source = next(s for s in chain["documents"].values() if sec_refresh.source_id(s) == term["source_id"])
                    lines.append(f"  - {term['coefficient']:+d} × {term['value']} USD · 역할 `{term['role']}` · [공시 원문]({source['source_url']}) · 태그 `{fact['tag']}`")
        if item["qoq"]: lines.append(f"- 전분기 증감액: {item['qoq']['absolute_change']} USD. 백분율이 보류돼도 실제 증감액은 남깁니다.")
        elif "overlap" in item["qoq_status"]: lines.append("- 전분기 비교 보류: 올해 공시에서 계산한 이전 누적값과 이전 공시의 수치가 정확히 일치하지 않습니다. 반올림·재분류·수정 여부를 원문에서 추가 확인해야 합니다.")
        else: lines.append("- 전분기 비교 보류: 필요한 기간의 검토값, 연속한 날짜 또는 적합한 범위가 부족합니다.")
        if item["ttm"] is None: lines.append("- 최근 12개월 보류: 연간·올해 누적·전년 누적의 연속한 기간과 검토가 필요합니다.")
        if item["rationale"]: lines.append("- 공시 간 비교 검토: " + safe(item["rationale"]))
        lines += [""]
    lines += ["## 보조 지표와 한계", ""]
    for horizon, label in (("current_quarter", "현재 분기"), ("previous_quarter", "이전 분기"), ("ttm", "최근 12개월")):
        for metric, number in chain["derived"][horizon].items():
            unit = "USD" if metric == "free_cash_flow" else "%"
            lines.append(f"- {label} {names[metric]}: {number + ' ' + unit if number is not None else '보류(기간·연결 범위·검토값 필요)'}")
    lines += [""] + ["- " + safe(x) for x in value["limitations"]] + ["", "목표가·매력도·가격 계획: 보류.", ""]
    return "\n".join(lines)


def write(value, output=None):
    validate(value)
    root = Path(output or ROOT / "reports/private/sec_chain_reports").resolve()
    if not value["is_illustrative"] and not root.is_relative_to((ROOT / "reports/private").resolve()): raise ValueError("PRIVATE_CHAIN_REPORT_REQUIRED")
    folder = root / ("illustrative" if value["is_illustrative"] else "real") / value["base_report"]["base_report"]["context"]["cik"] / value["report_id"]
    atomic_json(folder / "report.json", value)
    (folder / "REPORT.ko.md").write_text(render(value), encoding="utf-8")
    return folder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ciks", nargs="+")
    parser.add_argument("--as-of")
    parser.add_argument("--chain-root", type=Path)
    parser.add_argument("--packet-root", type=Path)
    parser.add_argument("--review-root", type=Path)
    parser.add_argument("--period-root", type=Path)
    parser.add_argument("--source-store", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--illustrative", action="store_true")
    parser.add_argument("--validate", type=Path)
    args = parser.parse_args()
    if args.validate:
        validate(sec_refresh.read(args.validate))
        print(json.dumps({"chain_report_replay_valid": True}))
    else:
        if not args.ciks: parser.error("--ciks is required")
        cutoff, outputs = args.as_of or now(), []
        for issuer in args.ciks:
            value = build(issuer, cutoff, root=args.chain_root, packet_root=args.packet_root, review_root=args.review_root, period_root=args.period_root, source_store=args.source_store, illustrative=args.illustrative)
            folder = write(value, args.output)
            outputs.append({"cik": value["base_report"]["base_report"]["context"]["cik"], "status": value["chain"]["status"], "coverage": value["chain"]["coverage"], "report_path": str(folder / "REPORT.ko.md")})
        print(json.dumps({"reports": outputs, "network_requests": 0}))


if __name__ == "__main__": main()
