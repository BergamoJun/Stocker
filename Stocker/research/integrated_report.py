"""Offline reviewed fundamentals, causal price scenarios and version-bound news."""

import argparse
from datetime import timedelta
from decimal import Decimal, localcontext
from pathlib import Path
import json
import time
import uuid

from . import integrated_inputs as inputs, news_connections, news_review_store, peak_simulation, trade_screen
from . import sec_chain_report as financial, sec_chain_review, sec_refresh
from .local_files import atomic_json
from .rights import as_utc
from .sec_report import ROOT, SYNTHETIC_LABEL
from .sec_transport import now
from .swing_models import load_price_snapshot
from .validation import digest


FILES = ["research/integrated_report.py", "research/integrated_report_render.py",
    "research/peak_simulation.py", "research/peak_model.py", "research/news_archive.py", "research/news.py", "research/event_review.py",
    "research/trade_screen.py", "config/peak_model_policy.json", "config/trade_screen_policy.json", "config/news_archive_policy.json",
    "config/news_review_policy.json", "config/source_entitlements.json", "config/official_news_entitlements.json",
    "schemas/news_archive.schema.json", "schemas/news_event_review.schema.json", "schemas/trade_screen.schema.json"]
LIMITATIONS = ["종합 연결은 과거 공시의 검토 결과·현재 확보 종가의 연구 시나리오·뉴스 검토의 확인 수준을 함께 보여줍니다. 이 자료로 예측 수익성이 검증되지는 않습니다.",
    "가격 모형은 고정 수식 통계 계산입니다. 재무·뉴스가 모형의 목표나 비용 판단을 자동 변경하지 않으며 실제 ML 학습·매매 주문은 수행하지 않습니다.",
    "공시의 회계기간, 가격 거래일, 기사 발행·검색·검토 시각은 서로 다릅니다. 모두 기준시각 전에 확인 가능한 자료만 연결합니다.",
    "현재 수집한 수정·재분류 자료를 과거 예측에 소급하지 않습니다. 종합 보고서 입력의 최초 연결 시각도 과거로 소급하지 않습니다.",
    "뉴스 제목·원문 링크와 사람이 기록한 영향 검토는 구분합니다. 미확인·오래된 주제는 위험 없음이나 중립으로 처리하지 않습니다.",
    "목표가·매력도·예상 수익실현 시기의 정식 제공 값은 보류합니다. 기업별 가격·업종·뉴스 연결 범위가 부족하면 해당 영역만 보류합니다."]


def methods():
    names = sorted(set(FILES + inputs.FILES + news_connections.CORE + news_review_store.LEGACY_FILES))
    return {**sec_chain_review.methods(), **{name: digest(ROOT / name) for name in names}}


def context(value):
    return value["base_report"]["base_report"]["context"]


def identity(value):
    c = context(value)
    if not c["snapshot_id"] or c["entity_type"] != "operating":
        raise ValueError("INTEGRATED_OPERATING_ISSUER_IDENTITY_UNAVAILABLE")
    return {"cik": c["cik"], "symbol": c["symbols"][0] if len(c["symbols"]) == 1 else None, "name": c["name"]}


def binding(packet):
    return {k: packet[k] for k in ("packet_id", "packet_sha256", "sequence", "available_at")} if packet else None


def market(issuer, symbol, cutoff, *, input_root=None, illustrative=False):
    selected = inputs.profile(issuer, symbol, illustrative)
    packet = inputs.as_of(issuer, cutoff, root=input_root, illustrative=illustrative) if selected else None
    if packet and (packet["symbol"] != symbol or packet["method_hashes"] != inputs.methods()):
        raise ValueError("INTEGRATED_INPUT_IDENTITY_OR_METHOD_CHANGED")
    stock = {"status": "unavailable", "as_of": cutoff, "reason_codes": [], "input": None, "current_input": False, "forecast": None, "screen": None}
    proxies = []
    def read_role(role):
        if not selected:
            return None, "NO_REVIEWED_ISSUER_PRICE_ADAPTER"
        if not packet:
            return None, "NO_INPUT_COMMITTED_BEFORE_CUTOFF"
        item = packet["roles"][role]
        if item["status"] != "captured":
            return None, item["reason_code"]
        folder = Path(packet["folder"]) / role
        try:
            data = inputs.verify(folder / "prices.json", folder / "price_entitlement.json", role=role, issuer=issuer, symbol=symbol, cutoff=cutoff, illustrative=illustrative)
            record = sec_refresh.read(folder / "price_entitlement.json")
            return (data, record, folder / "prices.json"), None
        except (ValueError, OSError, KeyError, TypeError):
            return None, "CAPTURED_PRICE_RIGHTS_OR_SOURCE_UNUSABLE"
    loaded, error = read_role("stock")
    reference = None
    if error:
        stock["reason_codes"] = [error]
    else:
        data, record, path = loaded
        stock["input"] = {k: data[k] for k in ("symbol", "cik", "snapshot_id", "retrieved_at", "source_ids", "source_url", "currency", "quote_kind", "point_in_time_verified")}
        stock["input"]["reference_session"] = data["bars"][-1]["session"]
        try:
            _, bars, calendar = load_price_snapshot(path, record, cutoff)
            reference = bars[-1].session
            stock.update(current_input=True)
            stock["input"]["reference_close"] = str(bars[-1].close)
        except (ValueError, OSError, KeyError):
            stock["reason_codes"] = ["PRICE_STALE_OR_UNUSABLE"]
        if stock["current_input"]:
            try:
                forecast = peak_simulation.forecast(bars, calendar, cutoff)
                screen = trade_screen.assess(forecast, as_of=cutoff, source_ids=data["source_ids"], is_illustrative=illustrative, mode="current_research")
                stock.update(status="available_research_scenario", forecast=forecast, screen=screen)
            except (ValueError, KeyError):
                stock["reason_codes"] = ["FIXED_STATISTICAL_SCENARIO_UNAVAILABLE"]
    for role in ("market", "sector"):
        card = {"role": role, "symbol": selected[role] if selected else None, "as_of": cutoff, "status": "unavailable", "reason_codes": [], "input": None, "observation": None}
        loaded, error = read_role(role)
        if error:
            card["reason_codes"] = [error]
        elif reference is None:
            card["reason_codes"] = ["REFERENCE_STOCK_SESSION_UNAVAILABLE"]
        else:
            data, record, path = loaded
            try:
                _, bars, _ = load_price_snapshot(path, record, cutoff)
                if bars[-1].session != reference:
                    raise ValueError("MISMATCHED_REFERENCE_SESSION")
                profile = next(p for p in inputs.fetch_context.policy()["proxies"] if p["symbol"] == selected[role])
                with localcontext() as precision:
                    precision.prec = 34
                    mean = sum((b.close for b in bars[-20:]), Decimal(0)) / 20
                    change = (bars[-1].close / bars[-6].close - 1) * 100
                card.update(status="available_price_observation", input={k: data[k] for k in ("symbol", "snapshot_id", "retrieved_at", "source_ids", "source_url")},
                    observation={"reference_session": reference, "close_vs_twenty_session_mean": "above" if bars[-1].close > mean else "below" if bars[-1].close < mean else "equal",
                        "five_session_price_change_pct": str(change), "explanation_ko": profile["explanation_ko"], "limitation_ko": profile["limitation_ko"], "evidence_url": profile["evidence_url"], "return_forecast": None})
            except (ValueError, OSError, KeyError, StopIteration):
                card["reason_codes"] = ["PROXY_STALE_OR_REFERENCE_MISMATCH"]
        proxies.append(card)
    return {"as_of": cutoff, "stock": stock, "proxies": proxies}, binding(packet)


def news(issuer, symbol, cutoff, generated_at, *, root=None, archive_root=None, illustrative=False):
    result = {"as_of": cutoff, "status": "unavailable", "reason_codes": [], "snapshot": None}
    if not inputs.profile(issuer, symbol, illustrative) or symbol not in inputs.policy()["news_adapters"]:
        result["reason_codes"] = ["NO_REVIEWED_ISSUER_NEWS_ADAPTER"]
        return result
    # Freeze this report's article set before the entire cutoff second. The
    # existing connector then applies review/event freshness at the real cutoff.
    lookup = (as_utc(cutoff).replace(microsecond=0) - timedelta(microseconds=1)).isoformat()
    state = news_review_store.archive_state(lookup, archive_root=archive_root, illustrative=illustrative)
    frozen = {"articles": [item["metadata"] for item in state["articles"]]}
    snapshot = news_connections.snapshot(cutoff, symbol=symbol, root=root, archive_root=archive_root, illustrative=illustrative, clock=lambda: generated_at, frozen_news=frozen)
    result.update(status="linked_archive", snapshot=snapshot)
    if not snapshot["coverage_current_at_cutoff"]:
        result["reason_codes"].append("LATEST_NEWS_COVERAGE_NOT_CONFIRMED")
    return result


def summarize(finance, prices, articles):
    c, g, chain = context(finance), finance["base_report"]["growth"], finance["chain"]
    snapshot = articles["snapshot"]
    coverage = {"financial_reviewed": c["review_coverage"]["reviewed_metrics"], "financial_expected": c["review_coverage"]["expected_metrics"],
        "yoy_reviewed": g["coverage"]["reviewed_pairs"], "qoq_calculated": chain["coverage"]["qoq_calculated"], "ttm_calculated": chain["coverage"]["ttm_calculated"],
        "price_scenario_available": prices["stock"]["status"] == "available_research_scenario", "proxies_available": sum(p["status"] == "available_price_observation" for p in prices["proxies"]),
        "news_articles": len(snapshot["articles"]) if snapshot else 0, "news_highlights": len(snapshot["highlights"]) if snapshot else 0,
        "news_coverage_current": snapshot["coverage_current_at_cutoff"] if snapshot else False}
    line = f"재무 {coverage['financial_reviewed']}/{coverage['financial_expected']}개 확인, 전분기·최근 12개월 각각 {coverage['qoq_calculated']}·{coverage['ttm_calculated']}개 계산. "
    line += "가격 연구 시나리오 연결. " if coverage["price_scenario_available"] else "가격 연구 시나리오 보류. "
    line += f"검토 조건을 충족한 주요 뉴스 {coverage['news_highlights']}건; "
    line += "뉴스 검색 범위와 미검토 항목을 함께 확인해야 합니다." if coverage["news_coverage_current"] else "최신 뉴스 범위 재확인이 필요합니다."
    return coverage, line


def catalog(finance, prices, articles):
    result = {}
    for source in [*context(finance)["sources"].values(), *finance["chain"]["documents"].values()]:
        if not source.get("raw_sha256") or not source.get("source_url"):
            continue
        sid = sec_refresh.source_id(source)
        result["financial:" + sid] = {"id": "financial:" + sid, "kind": "SEC", "source_ids": [sid], "url": source["source_url"], "retrieved_at": source.get("retrieved_at"), "raw_sha256": source["raw_sha256"]}
    for role, item in [("stock", prices["stock"]), *[(p["role"], p) for p in prices["proxies"]]]:
        data = item["input"]
        if data:
            for sid in data["source_ids"]:
                key = "price:" + role + ":" + sid
                result[key] = {"id": key, "kind": "personal_price", "source_ids": [sid], "url": data["source_url"], "retrieved_at": data["retrieved_at"]}
    if articles["snapshot"]:
        for article in articles["snapshot"]["articles"]:
            key = "news:" + article["article_key"]
            result[key] = {"id": key, "kind": "news_original_link", "source_ids": article["source_ids"], "url": article["metadata"]["url"], "retrieved_at": article["last_source_observed_at"]}
    return [result[k] for k in sorted(result)]


def build(issuer, cutoff=None, *, input_root=None, news_root=None, archive_root=None, financial_roots=None, illustrative=False, clock=now):
    inputs.policy()
    cutoff = cutoff or clock()
    if as_utc(cutoff) > as_utc(clock()):
        raise ValueError("INTEGRATED_FUTURE_CUTOFF")
    finance = financial.build(issuer, cutoff, **(financial_roots or {}), illustrative=illustrative, clock=clock)
    who = identity(finance)
    prices, bound = market(who["cik"], who["symbol"], cutoff, input_root=input_root, illustrative=illustrative)
    generated_at = clock()
    articles = news(who["cik"], who["symbol"], cutoff, generated_at, root=news_root, archive_root=archive_root, illustrative=illustrative)
    coverage, line = summarize(finance, prices, articles)
    value = {"schema_version": "integrated-research-report-v1", "report_id": uuid.uuid4().hex, "status": "research_only_unvalidated",
        "label": SYNTHETIC_LABEL if illustrative else "개인용 재무·가격·뉴스 종합 연구 보고서", "as_of": cutoff, "generated_at": generated_at,
        "is_illustrative": illustrative, "identity": who, "financial": finance, "market": prices, "news": articles, "input_binding": bound,
        "coverage": coverage, "source_catalog": catalog(finance, prices, articles), "one_line_ko": line, "limitations": list(LIMITATIONS),
        "released_price_plan": None, "released_score": None, "released_expected_target_sessions": None, "validation_id": None,
        "full_recommendation_ready": False, "original_forecasts_modified": False,
        "provenance": {"method_hashes": methods(), "input_root": str(inputs.namespace(input_root, illustrative).parent),
            "news_root": str(Path(news_root or ROOT / "reports/private/news_connections").resolve()), "archive_root": str(Path(archive_root or ROOT / "reports/private/news_archive").resolve()),
            "network_requests": 0, "external_llm_calls": 0, "llm_tokens": 0, "model_training_performed": False, "public_release": False}}
    validate(value)
    return value


def validate(value):
    inputs.validate(value, "report")
    inputs.policy()
    p = value["provenance"]
    if p["method_hashes"] != methods():
        raise ValueError("INTEGRATED_REPORT_METHOD_CHANGED")
    if as_utc(value["as_of"]) > as_utc(value["generated_at"]):
        raise ValueError("INTEGRATED_REPORT_TIME_INVALID")
    financial.validate(value["financial"])
    if value["identity"] != identity(value["financial"]) or value["financial"]["as_of"] != value["as_of"] or value["financial"]["is_illustrative"] is not value["is_illustrative"]:
        raise ValueError("INTEGRATED_FINANCIAL_IDENTITY_OR_TIME_CHANGED")
    who = value["identity"]
    expected, bound = market(who["cik"], who["symbol"], value["as_of"], input_root=p["input_root"], illustrative=value["is_illustrative"])
    articles = news(who["cik"], who["symbol"], value["as_of"], value["generated_at"], root=p["news_root"], archive_root=p["archive_root"], illustrative=value["is_illustrative"])
    coverage, line = summarize(value["financial"], expected, articles)
    if any((value["market"] != expected, value["input_binding"] != bound, value["news"] != articles, value["coverage"] != coverage, value["one_line_ko"] != line,
            value["source_catalog"] != catalog(value["financial"], expected, articles), value["limitations"] != LIMITATIONS)):
        raise ValueError("INTEGRATED_REPORT_REPLAY_MISMATCH")
    if value["label"] != (SYNTHETIC_LABEL if value["is_illustrative"] else "개인용 재무·가격·뉴스 종합 연구 보고서"):
        raise ValueError("INTEGRATED_REPORT_LABEL_CHANGED")
    return value


def write(value, output=None):
    from .integrated_report_render import markdown
    from .news_connection_report import markdown as news_markdown
    validate(value)
    base = Path(output or ROOT / "reports/private/integrated_reports").resolve()
    if not value["is_illustrative"] and not base.is_relative_to((ROOT / "reports/private").resolve()):
        raise ValueError("PRIVATE_INTEGRATED_REPORT_REQUIRED")
    folder = base / ("illustrative" if value["is_illustrative"] else "real") / value["identity"]["cik"] / value["report_id"]
    if folder.exists():
        raise ValueError("INTEGRATED_REPORT_ALREADY_EXISTS")
    atomic_json(folder / "report.json", value)
    (folder / "REPORT.ko.md").write_text(markdown(value), encoding="utf-8")
    (folder / "FINANCIAL.ko.md").write_text(financial.render(value["financial"]), encoding="utf-8")
    (folder / "YOY.ko.md").write_text(financial.previous.render(value["financial"]["base_report"]), encoding="utf-8")
    (folder / "CURRENT.ko.md").write_text(financial.previous.financial_report.render(value["financial"]["base_report"]["base_report"]), encoding="utf-8")
    if value["news"]["snapshot"]:
        (folder / "NEWS.ko.md").write_text(news_markdown(value["news"]["snapshot"]), encoding="utf-8")
    files = {path.name: digest(path) for path in folder.iterdir() if path.is_file()}
    atomic_json(folder / "bundle.json", {"version": "1.0.0", "report_id": value["report_id"], "files": files})
    return folder


def validate_file(path):
    path = Path(path)
    value = sec_refresh.read(path)
    validate(value)
    bundle = sec_refresh.read(path.parent / "bundle.json")
    expected = {"report.json", "REPORT.ko.md", "FINANCIAL.ko.md", "YOY.ko.md", "CURRENT.ko.md"}
    if value["news"]["snapshot"]:
        expected.add("NEWS.ko.md")
    if bundle.get("version") != "1.0.0" or bundle.get("report_id") != value["report_id"] or set(bundle.get("files", {})) != expected:
        raise ValueError("INTEGRATED_REPORT_BUNDLE_CHANGED")
    if path.name != "report.json" or any(digest(path.parent / name) != sha for name, sha in bundle["files"].items()):
        raise ValueError("INTEGRATED_REPORT_FILE_CHANGED")
    from .integrated_report_render import markdown
    if (path.parent / "REPORT.ko.md").read_text(encoding="utf-8") != markdown(value):
        raise ValueError("INTEGRATED_REPORT_RENDER_CHANGED")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ciks", nargs="+")
    parser.add_argument("--as-of")
    parser.add_argument("--input-root", type=Path)
    parser.add_argument("--news-root", type=Path)
    parser.add_argument("--archive-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--validate", type=Path)
    args = parser.parse_args(argv)
    if args.validate:
        validate_file(args.validate)
        print(json.dumps({"integrated_report_replay_valid": True}))
        return 0
    if not args.ciks:
        parser.error("--ciks is required")
    captures = []
    if not args.as_of:
        # Only the reviewed BE mapping has a price/news adapter at this stage.
        for issuer in sorted({str(i).zfill(10) for i in args.ciks}):
            if inputs.profile(issuer, "BE"):
                captures.append(inputs.capture(issuer, "BE", inputs.discovered_inputs(), root=args.input_root))
        if captures:
            latest = max(as_utc(r["available_at"]) for r in captures)
            wait = (latest.replace(microsecond=0) + timedelta(seconds=1) - as_utc(now())).total_seconds()
            if wait > 0:
                time.sleep(min(wait + 0.01, 1.05))
    cutoff, outputs = args.as_of or now(), []
    for issuer in sorted({str(i).zfill(10) for i in args.ciks}):
        value = build(issuer, cutoff, input_root=args.input_root, news_root=args.news_root, archive_root=args.archive_root)
        folder = write(value, args.output)
        outputs.append({"cik": value["identity"]["cik"], "coverage": value["coverage"], "report_path": str(folder / "REPORT.ko.md")})
    print(json.dumps({"reports": outputs, "network_requests": 0, "external_llm_calls": 0, "released_score": None}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
