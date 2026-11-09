"""Reviewed multi-issuer year-over-year evidence report; no price-model changes."""

import argparse
import json
from pathlib import Path
import uuid
from urllib.parse import quote

from . import sec_financial_report as financial_report, sec_period_review as periods, sec_growth, sec_refresh
from .local_files import atomic_json
from .rights import as_utc
from .sec_report import ROOT
from .sec_refresh_report import safe
from .sec_transport import now


LIMITATIONS = ["동일 공시의 전년 비교값을 대조했습니다. 오늘 수집한 비교값이 전년 당시의 원본 값과 같았는지 검증한 역사 자료는 아닙니다.",
    "적자나 0인 이전 값은 백분율 증감률을 보류하고 증감액과 부호 전환을 표시합니다. 부채·자산 잔액 변화율과 이익 성장의 의미도 구분해야 합니다.",
    "이전 값이 작으면 백분율 변화가 크게 나타날 수 있으므로 절대 증감액을 함께 봅니다. 희석 주당순손익에는 주식수 변화도 반영됩니다.",
    "전분기 검토 자료와 일관된 전체 기간이 부족해 전분기 대비·최근 12개월 누적은 보류합니다.",
    "실적 증가는 다음 주 수익이나 목표가 달성을 보장하지 않습니다. 가격 계획·매력도·뉴스 점수와 학습 모델은 변경하지 않습니다."]


def build(issuer, cutoff, *, root=None, review_root=None, source_store=None, illustrative=False, clock=now):
    base = financial_report.build(issuer, cutoff, root=review_root, source_store=source_store, illustrative=illustrative, clock=clock)
    growth = sec_growth.build(issuer, cutoff, base["context"], root=root, review_root=review_root, source_store=source_store, illustrative=illustrative)
    value = {"schema_version": "sec-reviewed-yoy-report-v1", "report_id": uuid.uuid4().hex,
        "label": "ILLUSTRATIVE — NOT LIVE MARKET DATA" if illustrative else "개인용 전년 대비 재무 분석 보고서", "is_illustrative": illustrative,
        "as_of": cutoff, "generated_at": clock(), "base_report": base, "growth": growth, "limitations": list(LIMITATIONS),
        "provenance": {"method_hashes": periods.methods(), "source_store": base["provenance"]["source_store"],
            "review_root": base["provenance"]["review_root"], "period_root": str(periods.namespace(root, illustrative).parent),
            "network_requests": 0, "external_llm_calls": 0, "public_release": False}}
    validate(value)
    return value


def validate(value):
    periods.validate(value, "report")
    if value["provenance"]["method_hashes"] != periods.methods(): raise ValueError("GROWTH_REPORT_METHOD_CHANGED")
    if as_utc(value["as_of"]) > as_utc(value["generated_at"]): raise ValueError("GROWTH_REPORT_CUTOFF_INVALID")
    financial_report.validate(value["base_report"])
    base = value["base_report"]
    if base["as_of"] != value["as_of"] or base["is_illustrative"] is not value["is_illustrative"]: raise ValueError("GROWTH_BASE_REPORT_IDENTITY_CHANGED")
    p = value["provenance"]
    if any(p[k] != base["provenance"][k] for k in ("review_root", "source_store")): raise ValueError("GROWTH_REPORT_SOURCE_ROOT_CHANGED")
    expected = sec_growth.build(base["context"]["cik"], value["as_of"], base["context"], root=p["period_root"], review_root=p["review_root"], source_store=p["source_store"], illustrative=value["is_illustrative"])
    if expected != value["growth"] or value["limitations"] != LIMITATIONS: raise ValueError("GROWTH_REPORT_REPLAY_MISMATCH")
    label = "ILLUSTRATIVE — NOT LIVE MARKET DATA" if value["is_illustrative"] else "개인용 전년 대비 재무 분석 보고서"
    if value["label"] != label: raise ValueError("GROWTH_REPORT_LABEL_CHANGED")
    return value


TRANSITIONS = {"negative_to_positive": "음수→양수", "negative_to_zero": "음수→0", "positive_to_negative": "양수→음수", "positive_to_zero": "양수→0", "zero_to_positive": "0→양수", "zero_to_negative": "0→음수", "negative_magnitude_decreased": "음수 규모 축소", "negative_magnitude_increased": "음수 규모 확대", "unchanged_negative": "음수 유지", "unchanged_zero": "0 유지", "increased_positive": "양수 증가", "decreased_positive": "양수 감소", "unchanged_positive": "양수 유지"}


def render(value):
    context, growth = value["base_report"]["context"], value["growth"]
    coverage = growth["coverage"]
    lines = ["# 전년 대비 재무 분석 보고서", "", value["label"], "", f"기업: {safe(context['name'] or '자료 없음')} · CIK {context['cik']}", f"기준시각(UTC): {value['as_of']}", "",
        "같은 공시의 현재 값과 전년 같은 기간 값을 원문에서 대조해 비교했습니다. 확인한 실적 변화이며 향후 수익 예측의 검증 결과는 아닙니다.", "",
        f"현재 재무 검토 {context['review_coverage']['reviewed_metrics']}/{context['review_coverage']['expected_metrics']}개, 전년 비교 검토 {coverage['reviewed_pairs']}/{coverage['expected_pairs']}쌍.", "",
        "연간·분기·회계연도 시작부터의 누적과 결산일 잔액을 구분합니다. 금액은 USD 원 단위, 주당순손익은 USD/shares입니다. 성장률은 이전 값이 양수일 때 (현재−이전)÷이전×100입니다.", ""]
    for section in growth["sections"]:
        lines += [f"## {section['form']} · 결산일 {section['report_date']}", "", "| 항목 | 현재 | 전년 | 증감액 | 전년 대비 | 해석 |", "|---|---:|---:|---:|---:|---|"]
        for metric, item in section["metrics"].items():
            name = financial_report.NAMES[metric]
            if item["scope"] == "parent_attributable": name += "(지배기업 귀속)"
            if item["basis"] == "instant": name += "(결산일 잔액)"
            elif item["basis"] == "cumulative_ytd": name += "(회계연도 누적)"
            calc = item["calculation"]
            if calc:
                percent = calc["percent_change"] + "%" if calc["percent_change"] is not None else "보류(이전 값 ≤ 0)" if calc["percent_status"] == "negative_or_zero_prior_percentage_withheld" else "수치 범위 초과로 보류"
                interpretation = TRANSITIONS[calc["transition"]]
                if metric in {"operating_income", "net_income"}:
                    interpretation = {"negative_to_positive": "적자→흑자", "positive_to_negative": "흑자→적자", "negative_magnitude_decreased": "적자 규모 축소", "negative_magnitude_increased": "적자 규모 확대"}.get(calc["transition"], interpretation)
                lines.append(f"| {name} | {calc['current']} | {calc['prior']} | {calc['absolute_change']} | {percent} | {interpretation} |")
            else:
                lines.append(f"| {name} | {item['current_value'] if item['current_value'] is not None else '보류'} | 보류 | 보류 | 보류 | 비교 원문 검토 대기 또는 미해결 |")
        lines += ["", "기간·출처와 판단 이유:", ""]
        for metric, item in section["metrics"].items():
            pair = item["pair"]
            if pair:
                source = context["sources"][item["source_ids"][0]]
                labels = []
                for side, title in (("current", "현재"), ("prior", "전년")):
                    fact = pair[side]
                    link = source["source_url"] + ("#" + quote(fact["dom_id"], safe="") if fact["dom_id"] else "")
                    labels.append(f"{title} {fact['start'] or ''} ~ {fact['end']} [원문]({link})")
                lines.append(f"- {financial_report.NAMES[metric]}: " + "; ".join(labels) + ". " + safe(item["rationale"]))
            elif item["rationale"]:
                lines.append(f"- {financial_report.NAMES[metric]} 보류 이유: {safe(item['rationale'])}")
        lines += ["", "이익률 변화:", ""]
        for metric in ("operating_margin_change", "net_margin_change"):
            item = section["derived"][metric]
            label = "영업이익률" if metric.startswith("operating") else "순이익률"
            lines.append(f"- {label}: 현재 {item['current']}%, 전년 {item['prior']}%, 차이 {item['change']}%p(퍼센트포인트)." if item["status"] == "calculated" else f"- {label} 변화 보류: 동일 기간·단위·연결 전체 범위의 검토값이 필요합니다.")
        fcf = section["derived"]["free_cash_flow_change"]
        calc = fcf["calculation"]
        if calc:
            lines.append(f"- 잉여현금흐름(해당 연간 또는 회계연도 누적): 현재 {calc['current']} USD, 전년 {calc['prior']} USD, 증감 {calc['absolute_change']} USD. 영업 현금흐름−유형자산 취득 지출의 자체 정의입니다.")
        else: lines.append("- 잉여현금흐름 비교 보류: 동일 기간의 현금흐름·유형자산 지출 검토가 필요합니다.")
        lines += [""]
    lines += ["## 반론·자료 부족과 다음 확인", ""] + ["- " + safe(x) for x in value["limitations"] + value["base_report"]["counterpoints"] + value["base_report"]["monitoring"]]
    lines += ["", "목표가·매력도·가격 계획: 보류. 위 실적 비교를 기존 예측·과거 평가에 소급 반영하지 않습니다.", ""]
    return "\n".join(lines)


def write(value, output=None):
    validate(value)
    root = Path(output or ROOT / "reports/private/sec_growth_reports").resolve()
    if not value["is_illustrative"] and not root.is_relative_to((ROOT / "reports/private").resolve()): raise ValueError("PRIVATE_GROWTH_REPORT_REQUIRED")
    folder = root / ("illustrative" if value["is_illustrative"] else "real") / value["base_report"]["context"]["cik"] / value["report_id"]
    atomic_json(folder / "report.json", value)
    (folder / "REPORT.ko.md").write_text(render(value), encoding="utf-8")
    return folder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ciks", nargs="+")
    parser.add_argument("--as-of")
    parser.add_argument("--period-root", type=Path)
    parser.add_argument("--review-root", type=Path)
    parser.add_argument("--source-store", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--illustrative", action="store_true")
    parser.add_argument("--validate", type=Path)
    args = parser.parse_args()
    if args.validate:
        validate(sec_refresh.read(args.validate))
        print(json.dumps({"growth_report_replay_valid": True}))
    else:
        if not args.ciks: parser.error("--ciks is required")
        outputs = []
        cutoff = args.as_of or now()
        for issuer in args.ciks:
            value = build(issuer, cutoff, root=args.period_root, review_root=args.review_root, source_store=args.source_store, illustrative=args.illustrative)
            folder = write(value, args.output)
            outputs.append({"cik": value["base_report"]["context"]["cik"], "coverage": value["growth"]["coverage"], "report_path": str(folder / "REPORT.ko.md")})
        print(json.dumps({"reports": outputs, "network_requests": 0}))


if __name__ == "__main__": main()
