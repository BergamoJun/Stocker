"""A multi-company financial-evidence report, with a generic reviewed input adapter."""

import argparse
from decimal import Decimal
import json
from pathlib import Path
import uuid
from urllib.parse import quote

import jsonschema

from . import sec_fact_review as reviews, sec_financial_context as financial, sec_refresh
from .local_files import atomic_json
from .rights import as_utc
from .sec_report import ROOT
from .sec_refresh_report import safe
from .sec_transport import now


NAMES = {"revenue": "매출", "operating_income": "영업손익", "net_income": "순손익", "cash": "현금 및 현금성 자산", "assets": "자산", "liabilities": "부채",
    "operating_cash_flow": "영업활동 현금흐름", "capital_expenditure": "유형자산 취득 지출", "diluted_eps": "희석 주당순이익",
    "operating_margin_percent": "영업이익률", "net_margin_percent": "순이익률", "free_cash_flow": "잉여현금흐름"}
COUNTERPOINTS = ["한 보고기간의 흑자·현금 유입으로 이후 성장의 지속이나 다음 주 주가 상승을 확정할 수 없습니다.", "연간·분기·연초부터 누적한 실적은 서로 다른 기간이며 직접 같은 기준으로 비교하지 않습니다."]
MONITORING = ["다음 분기·연간 공시와 수정 공시에서 손익·현금흐름의 지속 여부를 확인합니다.", "보류 항목의 원문 대조와 변경된 원문의 재검토를 진행합니다."]
LIMITATIONS = ["수치는 사람이거나 보조 도구가 수행한 원문 검토의 기록이며 프로그램이 회계 의미의 진위를 독립적으로 증명하지는 않습니다.",
    "저장된 SEC 공시 범위의 재무 보고서입니다. 전체 시장 정보·뉴스·컨센서스·시세·목표가를 포함하지 않습니다.",
    "비교 가능한 과거 기간의 검토를 별도로 확보하기 전까지 성장률·TTM(최근 12개월 누적)은 계산하지 않습니다.",
    "현재 수집·검토한 재무 수치를 과거 가격 평가의 당시 입력으로 소급하지 않습니다."]


def narratives(context):
    observations = []
    for section in context["sections"]:
        for metric in ("operating_income", "net_income", "operating_cash_flow"):
            item = section["metrics"][metric]
            if item["value"] is None: continue
            sign = "양수" if Decimal(item["value"]) > 0 else "음수" if Decimal(item["value"]) < 0 else "0"
            name = ("지배기업 귀속 순손익" if item["scope"] == "parent_attributable" else "연결 전체 순손익") if metric == "net_income" else NAMES[metric]
            observations.append({"text": f"{section['form']}의 검토된 {name}은 {sign}입니다. 해당 보고기간의 확인값입니다.", "accession": section["accession"],
                "metric": metric, "source_ids": item["source_ids"], "review_receipt_ids": item["review_receipt_ids"]})
    return observations


def build(issuer, cutoff, *, root=None, source_store=None, illustrative=False, clock=now):
    generated = clock()
    if as_utc(cutoff) > as_utc(generated): raise ValueError("SEC_REPORT_CUTOFF_IN_FUTURE")
    context = financial.build(issuer, cutoff, root=root, source_store=source_store, illustrative=illustrative)
    result = {"schema_version": "sec-financial-research-v1", "report_id": uuid.uuid4().hex,
        "is_illustrative": illustrative, "label": "ILLUSTRATIVE — NOT LIVE MARKET DATA" if illustrative else "개인용 재무 근거 투자 참고보고서",
        "as_of": cutoff, "generated_at": generated, "context": context,
        "swing": {"horizon": "five_exchange_sessions", "status": "withheld", "price_plan": None, "attractiveness": None,
            "reason": "financial_evidence_does_not_validate_price_forecasts_or_scores"},
        "observations": narratives(context),
        "counterpoints": list(COUNTERPOINTS), "monitoring": list(MONITORING), "limitations": list(LIMITATIONS),
        "provenance": {"method_hashes": reviews.methods(), "review_root": str(reviews.namespace(root, illustrative).parent),
            "source_store": context["source_store"], "external_llm_calls": 0, "network_requests": 0, "public_release": False}}
    validate(result)
    return result


def validate(value):
    schema = sec_refresh.read(ROOT / "schemas/sec_financial_report.schema.json")
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(value)
    if value["provenance"]["method_hashes"] != reviews.methods(): raise ValueError("SEC_FINANCIAL_REPORT_METHOD_CHANGED")
    if as_utc(value["as_of"]) > as_utc(value["generated_at"]): raise ValueError("SEC_FINANCIAL_REPORT_TIME_INVALID")
    context = financial.build(value["context"]["cik"], value["as_of"], root=Path(value["provenance"]["review_root"]),
        source_store=Path(value["provenance"]["source_store"]), illustrative=value["is_illustrative"])
    if context != value["context"] or value["observations"] != narratives(context) or context["is_illustrative"] is not value["is_illustrative"]:
        raise ValueError("SEC_FINANCIAL_REPORT_REPLAY_MISMATCH")
    if value["counterpoints"] != COUNTERPOINTS or value["monitoring"] != MONITORING or value["limitations"] != LIMITATIONS:
        raise ValueError("SEC_UNSUPPORTED_REPORT_NARRATIVE")
    expected_label = "ILLUSTRATIVE — NOT LIVE MARKET DATA" if value["is_illustrative"] else "개인용 재무 근거 투자 참고보고서"
    if value["label"] != expected_label: raise ValueError("SEC_FINANCIAL_REPORT_LABEL_CHANGED")
    for section in context["sections"]:
        if financial.derived(section["metrics"], section["amendment_scope_pending"]) != section["derived"]:
            raise ValueError("SEC_FINANCIAL_REPORT_CALCULATION_CHANGED")
    return value


def render(value):
    context = value["context"]
    coverage = context["review_coverage"]
    lines = ["# 재무 근거 투자 참고보고서", "", value["label"], "", f"기업: {safe(context['name'] or '자료 없음')} · CIK {context['cik']}",
        f"보고서 기준시각(UTC): {value['as_of']}", "", "이 보고서는 공시 원문을 대조한 재무 근거와 보류 사유를 보여줍니다. 향후 수익 예측 성능을 검증한 보고서가 아닙니다.", "",
        f"원문 검토 수치: {coverage['reviewed_metrics']}개 / 대상 {coverage['expected_metrics']}개. 보류: {coverage['withheld_metrics']}개.", "",
        "약 5거래일 스윙 항목: 가격 계획·목표가·매력도 점수는 보류합니다. 재무 검토와 가격 예측 검증은 각각 필요합니다.", ""]
    states = {"reviewed_source_fact": "원문 대조 기록 있음", "awaiting_original_review": "원문 검토 대기", "original_document_unavailable": "원문 미수집", "review_unresolved": "검토 후 미해결", "conflicting_reviews": "검토자 판정 충돌"}
    bases = {"quarter": "해당 분기", "annual": "연간", "instant": "결산일 현재", "cumulative_ytd": "회계연도 시작부터 누적"}
    scopes = {"consolidated": "연결 전체", "parent_attributable": "지배기업 귀속", "common_diluted": "보통주 희석 기준"}
    for section in context["sections"]:
        lines += [f"## {section['form']} · 결산일 {section['report_date']}", "", "| 항목 | 값 | 단위 | 기간·범위 | 확인 상태 |", "|---|---:|---|---|---|"]
        for name, item in section["metrics"].items():
            period = f"{item['start'] or ''} ~ {item['end']} · {bases[item['basis']]} · {scopes.get(item['scope'], '범위 검토 대기')}"
            refs = " ".join(f"[원문]({context['sources'][sid]['source_url']}#{quote(item['source_locator']['dom_id'], safe='')})" if item["source_locator"] and item["source_locator"]["dom_id"] else f"[원문]({context['sources'][sid]['source_url']})" for sid in item["source_ids"])
            lines.append(f"| {NAMES[name]} | {item['value'] if item['value'] is not None else '보류'} | {item['unit']} | {period} | {states[item['status']]} {refs} |")
        lines += ["", "파생 지표(확인된 입력으로 계산):", ""]
        for name, item in section["derived"].items():
            reason = "지배기업 귀속과 연결 전체의 범위를 구분하기 위해 계산을 보류합니다." if item["status"] == "attribution_scope_mismatch" else "같은 기간·범위의 검토 입력 또는 수정 공시 검토가 필요합니다."
            lines.append(f"- {NAMES[name]} ({bases.get(item['basis'], '기간 확인 필요')} · {item['start'] or ''} ~ {item['end']}): {item['value'] + ' ' + item['unit'] if item['value'] is not None else '보류 — ' + reason}")
        if section["amendment_scope_pending"]: lines += ["- 수정 공시가 연결돼 파생 지표의 자동 병합은 보류했습니다."]
        lines += [""]
    lines += ["## 확인된 근거와 반론", ""]
    lines += ["- " + safe(x["text"]) for x in value["observations"]] or ["- 확정해 서술할 재무 입력이 아직 없습니다."]
    lines += ["- " + safe(x) for x in value["counterpoints"]]
    lines += ["", "## 다음 확인과 자료 부족", ""] + ["- " + safe(x) for x in value["monitoring"] + value["limitations"]]
    missing = context["collection_coverage"].get("missing_base_forms", [])
    if missing: lines.append("- 보관 목록에서 확인하지 못한 기본 공시: " + ", ".join(missing))
    lines += [f"- 현재 원문과 맞지 않는 과거 검토 {coverage['stale_reviews']}건, 검토 방법 변경으로 보류한 기록 {coverage['method_outdated_reviews']}건.", "",
        "USD는 미국 달러 원 단위, USD/shares는 주당 달러, percent는 백분율입니다. 보류는 0이나 중립 평가를 뜻하지 않습니다.", "",
        "## 검토 추적", ""]
    for section in context["sections"]:
        for name, item in section["metrics"].items():
            if item["review_receipt_ids"]:
                row = item["source_locator"]["row_number"] if item["source_locator"] else "확정하지 않음"
                lines += [f"- {section['accession']} · {NAMES[name]} · 원문 행 {row} · 검토 기록 {', '.join(item['review_receipt_ids'])}",
                    "  - 선택 이유: " + safe(item["review_rationale"])]
    return "\n".join(lines) + "\n"


def write(value, output=None):
    validate(value)
    root = Path(output or ROOT / "reports/private/sec_financial_reports").resolve()
    if not value["is_illustrative"] and not root.is_relative_to((ROOT / "reports/private").resolve()): raise ValueError("PRIVATE_FINANCIAL_REPORT_REQUIRED")
    folder = root / ("illustrative" if value["is_illustrative"] else "real") / value["context"]["cik"] / value["report_id"]
    atomic_json(folder / "report.json", value)
    (folder / "REPORT.ko.md").write_text(render(value), encoding="utf-8")
    return folder


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ciks", nargs="+")
    parser.add_argument("--as-of")
    parser.add_argument("--review-root", type=Path)
    parser.add_argument("--source-store", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--illustrative", action="store_true")
    parser.add_argument("--validate", type=Path)
    args = parser.parse_args()
    if args.validate:
        validate(sec_refresh.read(args.validate))
        print(json.dumps({"report_replay_valid": True}))
    else:
        if not args.ciks: parser.error("--ciks is required")
        cutoff = args.as_of or now()
        outputs = []
        for issuer in args.ciks:
            value = build(issuer, cutoff, root=args.review_root, source_store=args.source_store, illustrative=args.illustrative)
            folder = write(value, args.output)
            outputs.append({"cik": value["context"]["cik"], "status": value["context"]["status"], "review_coverage": value["context"]["review_coverage"], "report_path": str(folder / "REPORT.ko.md")})
        print(json.dumps({"reports": outputs, "network_requests": 0, "external_llm_calls": 0}))


if __name__ == "__main__":
    main()
