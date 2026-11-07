"""Reproducible reports from explicitly reviewed SEC fact snapshots.

This adapter reads selected filing rows, not raw XBRL. Source-text entailment
and source capture remain reviewed steps; identifiers alone cannot prove them.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from pathlib import Path
from string import Formatter
from urllib.parse import urlparse

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
METHOD = "sec-selected-facts-v1"
SYNTHETIC_LABEL = "ILLUSTRATIVE — NOT LIVE MARKET DATA"


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def references(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"source_ids", "evidence_ids"}:
                yield from child
            else:
                yield from references(child)
    elif isinstance(value, list):
        for child in value:
            yield from references(child)


@dataclass(frozen=True)
class Fact:
    id: str
    amount: Decimal
    unit: str
    start: str | None
    end: str
    source_id: str
    basis: str


class InstrumentResolver:
    """An explicit initial cohort; never infer identity from an arbitrary ticker."""

    @staticmethod
    def resolve(snapshot: dict, ticker: str) -> dict:
        identity = snapshot["identity"]
        expected = ("BE", "0001664703", "NYSE", "equity")
        actual = tuple(identity.get(key) for key in ("symbol", "cik", "exchange", "instrument_type"))
        if ticker.upper() != "BE" or actual != expected:
            raise ValueError("Initial cohort supports verified Bloom Energy BE / CIK 0001664703 only")
        if identity["source_id"] not in {source["id"] for source in snapshot["sources"]}:
            raise ValueError("Identity requires a known SEC source")
        return identity


class SourceAdapter:
    """Offline, browser-reviewed selected facts. Does not fetch or execute sources."""

    @staticmethod
    def load(path: Path, as_of: str) -> tuple[dict, dict[str, Fact], str]:
        raw = path.read_bytes()
        snapshot = json.loads(raw)
        if snapshot.get("snapshot_version") != "1.0.0":
            raise ValueError("Unsupported selected-facts snapshot version")
        if snapshot.get("capture_method") != "browser_reviewed_selected_filing_rows":
            raise ValueError("This adapter requires reviewed selected SEC rows")
        if snapshot.get("effective_period_end") != "2026-09-30":
            raise ValueError("Initial report profile covers 2026-Q3 only")
        cutoff = timestamp(as_of)
        if not isinstance(snapshot.get("is_illustrative"), bool):
            raise ValueError("Explicit data classification is required")
        if snapshot["is_illustrative"] and SYNTHETIC_LABEL not in snapshot.get("label", ""):
            raise ValueError("Synthetic input must be visibly labelled")
        source_ids = set()
        for source in snapshot["sources"]:
            if source["id"] in source_ids:
                raise ValueError("Duplicate source ID")
            source_ids.add(source["id"])
            url = urlparse(source["url"])
            if (url.scheme != "https" or url.netloc != "www.sec.gov" or
                    not url.path.startswith("/Archives/edgar/data/1664703/")):
                raise ValueError("Source must identify the supported issuer in SEC Archives")
            if timestamp(source["published_at"]) > cutoff:
                raise ValueError("Source was published after as_of; future information rejected")
            if timestamp(source["retrieved_at"]) < timestamp(source["published_at"]):
                raise ValueError("Source retrieval precedes publication")
        if not source_ids:
            raise ValueError("At least one reviewed SEC source is required")
        facts = {}
        for row in snapshot["facts"]:
            if row["id"] in facts or row["source_id"] not in source_ids:
                raise ValueError("Duplicate fact ID or unknown fact source")
            if row["id"] not in FACT_PERIODS:
                raise ValueError("Fact is outside the initial selected-facts profile")
            if (row["period_start"], row["period_end"]) != FACT_PERIODS[row["id"]]:
                raise ValueError("Fact period does not match the labelled report profile")
            expected_basis = "issuer_guidance" if row["id"].startswith("revenue_guidance_") else "gaap"
            if row["basis"] != expected_basis:
                raise ValueError("Actual results and issuer guidance must be classified explicitly")
            if row["unit"] not in {"USD", "USD/share"} or row["scale"] not in {1, 1000}:
                raise ValueError("Unsupported currency, unit or scale")
            if isinstance(row["value"], bool) or isinstance(row["scale"], bool):
                raise ValueError("Boolean amounts/scales are not financial data")
            if not isinstance(row["value"], str):
                raise ValueError("Amounts must be decimal strings")
            try:
                amount = Decimal(row["value"]) * Decimal(row["scale"])
            except InvalidOperation as error:
                raise ValueError("Invalid decimal amount") from error
            if not amount.is_finite():
                raise ValueError("Non-finite amount")
            end = date.fromisoformat(row["period_end"])
            start = row["period_start"]
            if start is not None and date.fromisoformat(start) > end:
                raise ValueError("Invalid reporting period")
            if row["basis"] != "issuer_guidance" and end > cutoff.date():
                raise ValueError("Actual reporting period is in the future")
            if not row.get("locator"):
                raise ValueError("Fact needs a table/row/column locator")
            facts[row["id"]] = Fact(row["id"], amount, row["unit"], start,
                                    row["period_end"], row["source_id"], row["basis"])
        return snapshot, facts, digest(raw)


FACT_PERIODS = {
    **{name: ("2026-07-01", "2026-09-30") for name in (
        "revenue_q3", "gross_profit_q3", "operating_income_q3",
        "net_income_common_q3", "diluted_eps_q3")},
    **{name: ("2025-07-01", "2025-09-30") for name in (
        "revenue_q3_prior", "gross_profit_q3_prior")},
    "revenue_q2": ("2026-04-01", "2026-06-30"),
    **{name: ("2026-01-01", "2026-09-30") for name in (
        "revenue_9m", "ocf_9m", "ppe_cash_flow_9m", "customer_deposits_cash_flow_9m")},
    **{name: (None, "2026-09-30") for name in (
        "cash", "current_contract_assets", "inventories", "receivables")},
    "current_contract_assets_prior": (None, "2025-12-31"),
    **{name: ("2026-01-01", "2026-12-31") for name in (
        "revenue_guidance_low", "revenue_guidance_high")},
}


# name, input row IDs, operation, output unit, explicitly labelled period.
METRICS = [
    ("revenue_q3_musd", ["revenue_q3"], "million", "USD million", "2026-Q3 GAAP"),
    ("revenue_q3_yoy_pct", ["revenue_q3", "revenue_q3_prior"], "yoy", "%", "2026-Q3 vs 2025-Q3"),
    ("revenue_q3_qoq_pct", ["revenue_q3", "revenue_q2"], "qoq", "%", "2026-Q3 vs 2026-Q2"),
    ("gross_margin_q3_pct", ["gross_profit_q3", "revenue_q3"], "margin", "%", "2026-Q3 GAAP"),
    ("gross_margin_q3_prior_pct", ["gross_profit_q3_prior", "revenue_q3_prior"], "margin", "%", "2025-Q3 GAAP"),
    ("operating_income_q3_musd", ["operating_income_q3"], "million", "USD million", "2026-Q3 GAAP"),
    ("operating_margin_q3_pct", ["operating_income_q3", "revenue_q3"], "margin", "%", "2026-Q3 GAAP"),
    ("net_income_common_q3_musd", ["net_income_common_q3"], "million", "USD million", "2026-Q3 GAAP"),
    ("diluted_eps_q3_usd", ["diluted_eps_q3"], "identity", "USD/share", "2026-Q3 GAAP diluted"),
    ("revenue_9m_musd", ["revenue_9m"], "million", "USD million", "2026-01-01/2026-09-30 GAAP"),
    ("operating_cash_flow_9m_musd", ["ocf_9m"], "million", "USD million", "2026-01-01/2026-09-30 GAAP"),
    ("capex_cash_outflow_9m_musd", ["ppe_cash_flow_9m"], "outflow", "USD million", "2026-01-01/2026-09-30 cash purchase of PP&E"),
    ("free_cash_flow_9m_musd", ["ocf_9m", "ppe_cash_flow_9m"], "fcf", "USD million", "2026-01-01/2026-09-30 analyst-defined FCF"),
    ("cash_musd", ["cash"], "million", "USD million", "2026-09-30 instant"),
    ("current_contract_assets_musd", ["current_contract_assets"], "million", "USD million", "2026-09-30 instant"),
    ("current_contract_assets_prior_musd", ["current_contract_assets_prior"], "million", "USD million", "2025-12-31 instant"),
    ("inventories_musd", ["inventories"], "million", "USD million", "2026-09-30 instant"),
    ("receivables_musd", ["receivables"], "million", "USD million", "2026-09-30 instant"),
    ("customer_deposits_cash_flow_9m_musd", ["customer_deposits_cash_flow_9m"], "million", "USD million", "2026-01-01/2026-09-30 cash-flow adjustment"),
    ("revenue_guidance_low_musd", ["revenue_guidance_low"], "million", "USD million", "FY2026 issuer guidance, published 2026-10-22"),
    ("revenue_guidance_high_musd", ["revenue_guidance_high"], "million", "USD million", "FY2026 issuer guidance, published 2026-10-22"),
    ("implied_q4_revenue_low_musd", ["revenue_guidance_low", "revenue_9m"], "implied", "USD million", "2026-Q4 implied by issuer FY guidance; not a forecast"),
    ("implied_q4_revenue_high_musd", ["revenue_guidance_high", "revenue_9m"], "implied", "USD million", "2026-Q4 implied by issuer FY guidance; not a forecast"),
]

METRIC_LABELS = dict(zip((row[0] for row in METRICS), (
    "분기 매출", "분기 매출 전년 대비", "분기 매출 전분기 대비", "분기 매출총이익률",
    "전년 동기 매출총이익률", "분기 영업이익", "분기 영업이익률", "분기 보통주주 귀속 순이익",
    "분기 희석 EPS", "9개월 누적 매출", "9개월 누적 영업현금흐름", "9개월 현금 설비취득 지출",
    "9개월 자체 정의 FCF", "현금 및 현금성자산", "유동 계약자산", "직전 연말 유동 계약자산",
    "재고자산", "매출채권", "9개월 이연수익·고객예수금 현금흐름 조정",
    "회사 연간 매출 전망 하단", "회사 연간 매출 전망 상단", "전망 역산 4분기 필요 매출 하단",
    "전망 역산 4분기 필요 매출 상단",
)))


class MetricEngine:
    @staticmethod
    def calculate(facts: dict[str, Fact]) -> tuple[list[dict], list[dict], list[str]]:
        metrics, audit, missing = [], [], []
        for name, input_ids, operation, unit, period in METRICS:
            rows = [facts[key] for key in input_ids if key in facts]
            formula = f"{METHOD}:{operation}({','.join(input_ids)})"
            reason = None
            value = None
            try:
                if len(rows) != len(input_ids):
                    raise ValueError("missing input facts")
                expected_unit = "USD/share" if operation == "identity" else "USD"
                if any(row.unit != expected_unit for row in rows):
                    raise ValueError("incompatible units")
                a = rows[0]
                if operation in {"margin", "fcf"} and (a.start, a.end) != (rows[1].start, rows[1].end):
                    raise ValueError("incompatible reporting periods")
                if operation in {"margin", "fcf", "yoy", "qoq"} and any(row.basis != "gaap" for row in rows):
                    raise ValueError("GAAP and guidance cannot be mixed")
                if operation == "yoy":
                    b = rows[1]
                    if (not a.start or not b.start or
                        date.fromisoformat(a.start).replace(year=int(a.start[:4]) - 1).isoformat() != b.start or
                        date.fromisoformat(a.end).replace(year=int(a.end[:4]) - 1).isoformat() != b.end):
                        raise ValueError("not comparable year-over-year periods")
                if operation == "qoq" and (not a.start or not rows[1].start or
                        date.fromisoformat(rows[1].end) + timedelta(days=1) != date.fromisoformat(a.start)):
                    raise ValueError("not adjacent quarterly periods")
                with localcontext() as context:
                    context.prec = 34
                    if operation == "identity":
                        value = a.amount
                    elif operation == "million":
                        value = a.amount / Decimal(1_000_000)
                    elif operation == "outflow":
                        if a.amount > 0:
                            raise ValueError("PP&E cash purchase must retain its negative filing sign")
                        value = -a.amount / Decimal(1_000_000)
                    elif operation in {"yoy", "qoq", "margin"}:
                        if rows[1].amount <= 0:
                            raise ValueError("non-positive denominator")
                        value = a.amount / rows[1].amount * 100
                        if operation != "margin":
                            value -= 100
                    elif operation == "fcf":
                        if rows[1].amount > 0:
                            raise ValueError("PP&E cash purchase sign is invalid")
                        value = (a.amount + rows[1].amount) / Decimal(1_000_000)
                    elif operation == "implied":
                        actual = rows[1]
                        if (a.basis != "issuer_guidance" or actual.basis != "gaap" or
                                a.start != actual.start or actual.end != "2026-09-30" or a.end != "2026-12-31"):
                            raise ValueError("guidance/YTD period mismatch")
                        value = (a.amount - actual.amount) / Decimal(1_000_000)
                    else:
                        raise ValueError("unknown calculation")
                    value = value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
            except (ValueError, InvalidOperation, ZeroDivisionError) as error:
                reason = str(error)
                missing.append(f"{name}: {reason}")
            source_ids = sorted({row.source_id for row in rows})
            metrics.append({"name": name, "value": float(value) if value is not None else None,
                            "unit": unit, "period": period, "formula": formula,
                            "evidence_ids": source_ids})
            audit.append({"metric": name, "formula": formula, "input_fact_ids": input_ids,
                          "decimal_value": str(value) if value is not None else None,
                          "source_ids": source_ids, "limitation": reason})
        return metrics, audit, missing


def format_research(value, numbers: dict[str, str]):
    if isinstance(value, str):
        for _, field, spec, conversion in Formatter().parse(value):
            if field is not None and (field not in numbers or spec or conversion):
                raise ValueError("Research templates may reference metric names only")
        return value.format_map(numbers)
    if isinstance(value, list):
        return [format_research(item, numbers) for item in value]
    if isinstance(value, dict):
        return {key: format_research(item, numbers) for key, item in value.items() if key != "requires"}
    return value


def build_report(snapshot: dict, facts: dict[str, Fact], ticker: str, as_of: str,
                 snapshot_hash: str, generated_at: str | None = None) -> tuple[dict, dict]:
    identity = InstrumentResolver.resolve(snapshot, ticker)
    generated_at = generated_at or utc_now()
    if timestamp(as_of) > timestamp(generated_at):
        raise ValueError("as_of cannot be after report generation")
    metrics, calculations, missing = MetricEngine.calculate(facts)
    values = {item["name"]: item["value"] for item in metrics}
    numbers = {name: f"{Decimal(str(value)):,.2f}" if value is not None else "자료 없음"
               for name, value in values.items()}
    narrative = deepcopy(snapshot["research"])
    limitations = list(narrative["limitations"])
    for section in ("theses", "monitoring", "risk_flags"):
        selected = []
        for item in narrative[section]:
            if any(values.get(key) is None for key in item.get("requires", [])):
                limitations.append(f"{section}: 필수 수치가 부족해 해당 해석을 생략함")
            else:
                selected.append(item)
        narrative[section] = selected
    summary = narrative["summary"]
    if any(values.get(key) is None for key in summary.get("requires", [])):
        summary.update(stance="undetermined", text="필수 재무자료 부족으로 해석을 보류한다.",
                       evidence_confidence="low", confidence_rationale="검증할 필수 수치가 누락되었다.")
    narrative = format_research(narrative, numbers)
    limitations.extend(missing)
    required_missing = ["허가된 최신 시세/OHLCV", "목표가 산출 방법론", "검증된 매력도/도달기간 모델"] + missing
    report = {
        "schema_version": "0.2.0",
        "report_id": f"{ticker}-sec-{timestamp(generated_at).strftime('%Y%m%dT%H%M%SZ')}-{snapshot_hash[:10]}",
        "generated_at": generated_at, "as_of": as_of, "language": "ko",
        "subject": {"name": identity["name"], "symbol": identity["symbol"], "market": "US",
                    "instrument_type": "equity", "horizon": "swing"},
        "data_status": {"is_illustrative": snapshot["is_illustrative"], "quote_as_of": None,
                        "fundamentals_as_of": snapshot["effective_period_end"],
                        "stale": True, "missing": required_missing},
        "summary": narrative["summary"], "theses": narrative["theses"], "metrics": metrics,
        "scenarios": narrative["scenarios"], "monitoring": narrative["monitoring"],
        "risk_flags": narrative["risk_flags"], "limitations": limitations,
        "sources": [{key: source[key] for key in (
            "id", "title", "publisher", "source_type", "url", "published_at", "retrieved_at"
        )} for source in snapshot["sources"]],
        "swing": {"status": "unavailable", "headline": narrative["swing_headline"],
                  "reasons": required_missing, "price_plan": None,
                  "attractiveness": {"status": "unavailable", "score": None,
                    "validation_status": "not_validated", "methodology_id": None,
                    "methodology_version": None, "validation_id": None, "input_snapshot_id": None,
                    "components": [], "reasons": ["가격 입력과 검증된 점수 모델이 없음"]}},
    }
    if snapshot["is_illustrative"]:
        report["limitations"].insert(0, SYNTHETIC_LABEL)
    audit = {"methodology": METHOD, "snapshot_sha256": snapshot_hash,
             "capture_method": snapshot["capture_method"], "source_records": snapshot["sources"],
             "identity": identity, "normalized_facts": [
                 {**row.__dict__, "amount": str(row.amount)} for row in facts.values()],
             "calculations": calculations, "llm_api_calls": 0,
             "analysis_origin": "reviewed narrative in selected-facts snapshot; metric substitution only",
             "public_release_entitlement": "not_reviewed; local user-requested research only",
             "raw_filing_sha256": None,
             "validation_scope": "schema, IDs, publication cutoff, metric replay, balance; text entailment reviewed manually"}
    ReportValidator.validate(report, snapshot, facts)
    return report, audit


class ReportValidator:
    @staticmethod
    def validate(report: dict, snapshot: dict, facts: dict[str, Fact]) -> None:
        schema = json.loads((ROOT / "schemas/research_report.schema.json").read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(report)
        known = {source["id"] for source in report["sources"]}
        if len(known) != len(report["sources"]) or not set(references(report)) <= known:
            raise ValueError("Unknown or duplicate report citations")
        expected_sources = [{key: source[key] for key in (
            "id", "title", "publisher", "source_type", "url", "published_at", "retrieved_at"
        )} for source in snapshot["sources"]]
        if report["sources"] != expected_sources:
            raise ValueError("Report sources differ from the reviewed input snapshot")
        identity = snapshot["identity"]
        if any(report["subject"][key] != identity[key] for key in ("name", "symbol", "instrument_type")):
            raise ValueError("Report subject differs from verified identity")
        if {item["name"] for item in report["scenarios"]} != {"bull", "base", "bear"}:
            raise ValueError("Three distinct scenarios required")
        for scenario in report["scenarios"]:
            if not scenario["assumptions"] or not scenario["signals"] or not scenario["invalidated_if"]:
                raise ValueError("Scenario conditions and invalidation are required")
        for source in report["sources"]:
            if timestamp(source["published_at"]) > timestamp(report["as_of"]):
                raise ValueError("Future report citation")
        expected, _, _ = MetricEngine.calculate(facts)
        if report["metrics"] != expected:
            raise ValueError("Reported metrics do not match deterministic calculation replay")
        if report["swing"]["price_plan"] is not None or report["swing"]["attractiveness"]["score"] is not None:
            raise ValueError("SEC-only adapter cannot publish a price plan or attractiveness score")
        if not report["data_status"]["missing"] and any(item["value"] is None for item in expected):
            raise ValueError("Missing metrics must be disclosed")
        if not any(item["value"] is None for item in expected):
            if not {"bull", "bear"} <= {item["side"] for item in report["theses"]}:
                raise ValueError("Available research requires bullish and bearish arguments")


def render_markdown(report: dict) -> str:
    sources = {source["id"]: source for source in report["sources"]}

    def cite(ids):
        return " ".join(f"[{source_id}]({sources[source_id]['url']})" for source_id in ids)

    def prose(value):
        return re.sub(r"\[([a-zA-Z0-9_]+)\]",
                      lambda match: cite([match[1]]) if match[1] in sources else match[0], value)

    korea_as_of = timestamp(report["as_of"]).astimezone(timezone(timedelta(hours=9))).isoformat(timespec="seconds")
    rows = [f"# {report['subject']['name']} ({report['subject']['symbol']}) SEC 기반 리서치",
            "", f"- 분석 기준: {korea_as_of} (한국시간) / {report['as_of']} (UTC)",
            f"- 재무 기준: {report['data_status']['fundamentals_as_of']}",
            f"- 생성: {report['generated_at']} / 보고서 ID: {report['report_id']}",
            "- 범위: 검토한 공시의 재무·사업 정보. 신규 공시 전체나 실시간 시세를 포괄하지 않음.",
            "", "## 스윙 요약", "", report["swing"]["headline"], "",
            "| 항목 | 결과 |", "|---|---|",
            "| 기준가격·1차 목표가·손익비 | 산출 보류 — 허가된 가격 입력과 산출 방법 없음 |",
            "| 수익실현 시기 | 도달기간 추정 보류; 5거래일 관찰 프로필만 정의됨 |",
            "| 매력도 1~100 | 산출 보류 — 가격 입력·검증 모델 없음 |",
            f"| 근거 신뢰도 | {report['summary']['evidence_confidence']} — 방향성 적중 확률 아님 |",
            "", "## 요약", "", prose(report["summary"]["text"]), "",
            report["summary"]["confidence_rationale"], "", "## 검증한 수치", "",
            "금액은 USD million(백만 달러), 비율은 %, EPS는 USD/share. GAAP·누적기간·회사 전망을 구분한다.",
            "", "| 지표 | 값 | 기간/성격 | 출처 |", "|---|---:|---|---|"]
    for metric in report["metrics"]:
        value = "산출 보류" if metric["value"] is None else f"{metric['value']:,.2f} {metric['unit']}"
        rows.append(f"| {METRIC_LABELS[metric['name']]} | {value} | {metric['period']} | {cite(metric['evidence_ids'])} |")
    rows.extend(["", "FCF는 영업현금흐름에서 현금 PP&E 취득액을 차감한 자체 정의다. 회사 발표 FCF나 분기 FCF와 동일하다고 가정하지 않는다.",
                 "4분기 필요 매출은 회사 연간 매출 전망에서 9개월 누적 매출을 뺀 계산이며 달성 예측이 아니다.", "", "## 강세 논리와 반론", ""])
    for thesis in report["theses"]:
        rows.extend([f"- **{thesis['side']}**: {prose(thesis['claim'])} {cite(thesis['evidence_ids'])}",
                     f"  - 무효화/재검토: {thesis['invalidation']}"])
    rows.extend(["", "## 조건부 시나리오", ""])
    for scenario in report["scenarios"]:
        rows.extend([f"### {scenario['name']}", "", f"- 전제: {'; '.join(scenario['assumptions'])}",
                     f"- 해석: {prose(scenario['mechanism'])}", f"- 관찰: {'; '.join(scenario['signals'])}",
                     f"- 무효화: {scenario['invalidated_if']}", f"- 결과: {scenario['outcome']}", ""])
    rows.extend(["## 점검할 항목", ""])
    rows.extend(f"- {item['metric']}: {item['why']} {cite(item['source_ids'])}" for item in report["monitoring"])
    rows.extend(["", "## 주요 위험", ""])
    rows.extend(f"- [{item['severity']}] {item['description']} {cite(item['evidence_ids'])}" for item in report["risk_flags"])
    rows.extend(["", "## 한계·산출 방법", ""])
    rows.extend(f"- {item}" for item in report["limitations"])
    rows.extend(["", "## 원문과 시각", ""])
    for source in report["sources"]:
        rows.extend([f"- [{source['title']}]({source['url']}) [{source['id']}]",
                     f"  - 공개 {source['published_at']} / 조회 {source['retrieved_at']} / {source['publisher']}"])
    return "\n".join(rows) + "\n"
