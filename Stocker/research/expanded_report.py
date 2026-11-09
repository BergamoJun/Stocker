"""New issuer-news composition contract preserving every earlier report method."""

import argparse
import json
from pathlib import Path
import uuid

from . import integrated_report as base, issuer_profiles as profiles, issuer_news, news_refresh, project_compatibility as guard
from .integrated_report_render import markdown as base_markdown
from .local_files import atomic_json
from .news_connection_report import markdown as news_markdown, text, link
from .rights import as_utc
from .sec_refresh import read
from .sec_report import ROOT, SYNTHETIC_LABEL
from .sec_transport import now
from .validation import digest


FILES = ["research/expanded_report.py", "research/project_compatibility.py", "research/news_refresh.py", "config/news_refresh_policy.json", "schemas/issuer_context.schema.json"]
LIMITATIONS = ["기업별 뉴스 연결을 확장한 별도 계약입니다. 이전 보고서·수식·검토·예측·예약 기록은 유지합니다.",
    "새 뉴스는 원문·영향 검토 전의 메타데이터입니다. 최신 기사 수나 조회 성공을 호재 점수로 바꾸지 않습니다.",
    "MSFT·XOM 업종 참고 상품의 문서상 역할은 확인했으나 이번 단계에서 새 가격 어댑터를 활성화하지 않았습니다.",
    "캐시 사용·빈 검색·조회 실패·미검토·오래된 자료는 서로 다릅니다. 일반 검색과 제한된 공식 피드를 구분합니다."]


def methods():
    return {**base.methods(), **news_refresh.methods(), **{name: digest(ROOT / name) for name in FILES}}


def selected(issuer, cutoff, profile_root, illustrative, identity):
    profile = profiles.as_of(issuer, cutoff, root=profile_root, illustrative=illustrative)
    if profile and (profile["cik"] != identity["cik"] or profile["symbol"] != identity["symbol"]):
        raise ValueError("EXPANDED_REPORT_ISSUER_MISMATCH")
    return profile


def build(issuer, baseline, cutoff=None, *, profile_root=None, news_root=None, base_options=None, illustrative=False, clock=now):
    proof = guard.check(baseline)
    if not illustrative and Path(read(baseline)["root"]).resolve() != ROOT:
        raise ValueError("CURRENT_PROJECT_BASELINE_REQUIRED")
    cutoff = cutoff or clock()
    if as_utc(cutoff) > as_utc(clock()):
        raise ValueError("EXPANDED_REPORT_FUTURE_CUTOFF")
    original = base.build(issuer, cutoff, **(base_options or {}), illustrative=illustrative, clock=clock)
    profile = selected(issuer, cutoff, profile_root, illustrative, original["identity"])
    generated_at = clock()
    news = issuer_news.snapshot(issuer, cutoff, root=news_root, profile_root=profile_root, illustrative=illustrative, clock=lambda: generated_at)
    coverage, line = base.summarize(original["financial"], original["market"], news)
    value = {"schema_version": "issuer-expanded-research-v1", "report_id": uuid.uuid4().hex, "as_of": cutoff, "generated_at": generated_at, "is_illustrative": illustrative,
        "label": SYNTHETIC_LABEL if illustrative else "개인용 기업별 뉴스 확장 종합 연구 보고서", "base_report": original, "profile": profile,
        "news": news, "coverage": coverage, "source_catalog": base.catalog(original["financial"], original["market"], news), "one_line_ko": line,
        "limitations": list(LIMITATIONS), "released_price_plan": None, "released_score": None, "released_expected_target_sessions": None, "full_recommendation_ready": False,
        "provenance": {"method_hashes": methods(), "baseline_path": str(Path(baseline).resolve()), "baseline_sha256": proof["baseline_sha256"],
            "profile_root": str(profiles.namespace(profile_root, illustrative).parent), "news_root": str(Path(news_root or ROOT / "reports/private/issuer_news").resolve()),
            "network_requests": 0, "external_llm_calls": 0, "model_training_performed": False, "original_forecasts_modified": False}}
    validate(value)
    return value


def validate(value):
    profiles.validate(value, "report")
    p = value["provenance"]
    guard.check(p["baseline_path"], expected_sha256=p["baseline_sha256"])
    if p["method_hashes"] != methods() or p["network_requests"] != 0 or p["external_llm_calls"] != 0 or p["model_training_performed"] is not False or p["original_forecasts_modified"] is not False:
        raise ValueError("EXPANDED_REPORT_METHOD_OR_BOUNDARY_CHANGED")
    original = value["base_report"]
    base.validate(original)
    if original["as_of"] != value["as_of"] or original["is_illustrative"] is not value["is_illustrative"] or as_utc(value["as_of"]) > as_utc(value["generated_at"]):
        raise ValueError("EXPANDED_REPORT_CLASSIFICATION_OR_TIME_CHANGED")
    if not value["is_illustrative"] and Path(read(p["baseline_path"])["root"]).resolve() != ROOT:
        raise ValueError("CURRENT_PROJECT_BASELINE_REQUIRED")
    profile = selected(original["identity"]["cik"], value["as_of"], p["profile_root"], value["is_illustrative"], original["identity"])
    news = issuer_news.snapshot(original["identity"]["cik"], value["as_of"], root=p["news_root"], profile_root=p["profile_root"], illustrative=value["is_illustrative"], clock=lambda: value["generated_at"])
    coverage, line = base.summarize(original["financial"], original["market"], news)
    if any((profile != value["profile"], news != value["news"], coverage != value["coverage"], line != value["one_line_ko"], value["source_catalog"] != base.catalog(original["financial"], original["market"], news), value["limitations"] != LIMITATIONS)):
        raise ValueError("EXPANDED_REPORT_REPLAY_MISMATCH")
    if value["label"] != (SYNTHETIC_LABEL if value["is_illustrative"] else "개인용 기업별 뉴스 확장 종합 연구 보고서"):
        raise ValueError("EXPANDED_REPORT_LABEL_CHANGED")
    return value


def markdown(value):
    # Rendering projection only: the embedded original stays untouched.
    display = {**value["base_report"], **{k: value[k] for k in ("label", "generated_at", "news", "coverage", "source_catalog", "one_line_ko", "limitations")}}
    output = base_markdown(display)
    profile = value["profile"]
    output += "\n## 기업별 뉴스·업종 연결 범위\n\n"
    if profile:
        output += "기업별로 확인한 뉴스 검색과 검토 저장소를 사용했습니다. 같은 사건도 다른 기업의 영향 검토를 자동 재사용하지 않습니다.\n\n"
        for role, label in (("market_proxy", "시장"), ("sector_proxy", "업종")):
            item = profile[role]
            output += f"- {label} 참고 상품: {text(item['symbol'])} · {link(item['source_url'], '운용사 문서')} · {text(item['limitation_ko'])}\n"
        if profile["price_adapter_status"] == "not_implemented":
            output += "\n위 상품의 문서상 역할과 실제 가격 연결은 별도입니다. 이 기업의 새 가격·ETF 시세 연결은 아직 보류입니다.\n"
    else:
        output += "기준시각까지 등록한 기업별 프로파일이 없어 뉴스 확장을 보류했습니다.\n"
    return output


def write(value, output=None):
    validate(value)
    root = Path(output or ROOT / "reports/private/expanded_reports").resolve()
    if not value["is_illustrative"] and not root.is_relative_to((ROOT / "reports/private").resolve()):
        raise ValueError("PRIVATE_EXPANDED_REPORT_REQUIRED")
    folder = root / ("illustrative" if value["is_illustrative"] else "real") / value["base_report"]["identity"]["cik"] / value["report_id"]
    if folder.exists():
        raise ValueError("EXPANDED_REPORT_ALREADY_EXISTS")
    atomic_json(folder / "report.json", value)
    atomic_json(folder / "BASE_REPORT.json", value["base_report"])
    finance = value["base_report"]["financial"]
    for name, content in (("REPORT.ko.md", markdown(value)), ("FINANCIAL.ko.md", base.financial.render(finance)),
        ("YOY.ko.md", base.financial.previous.render(finance["base_report"])), ("CURRENT.ko.md", base.financial.previous.financial_report.render(finance["base_report"]["base_report"]))):
        (folder / name).write_text(content, encoding="utf-8")
    if value["news"]["snapshot"]:
        (folder / "NEWS.ko.md").write_text(news_markdown(value["news"]["snapshot"]), encoding="utf-8")
    atomic_json(folder / "bundle.json", {"version": "1.0.0", "report_id": value["report_id"], "files": {p.name: digest(p) for p in folder.iterdir() if p.is_file()}})
    return folder


def validate_file(path):
    path = Path(path)
    value = read(path)
    validate(value)
    bundle = read(path.parent / "bundle.json")
    expected = {"report.json", "BASE_REPORT.json", "REPORT.ko.md", "FINANCIAL.ko.md", "YOY.ko.md", "CURRENT.ko.md"}
    if value["news"]["snapshot"]:
        expected.add("NEWS.ko.md")
    if bundle.get("version") != "1.0.0" or bundle.get("report_id") != value["report_id"] or set(bundle.get("files", {})) != expected or any(digest(path.parent / k) != v for k, v in bundle["files"].items()):
        raise ValueError("EXPANDED_REPORT_BUNDLE_CHANGED")
    if (path.parent / "REPORT.ko.md").read_text(encoding="utf-8") != markdown(value) or read(path.parent / "BASE_REPORT.json") != value["base_report"]:
        raise ValueError("EXPANDED_REPORT_RENDER_OR_BASE_CHANGED")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--ciks", nargs="+")
    parser.add_argument("--as-of")
    parser.add_argument("--validate", type=Path)
    args = parser.parse_args(argv)
    if args.validate:
        validate_file(args.validate)
        print(json.dumps({"expanded_report_replay_valid": True}))
        return 0
    if not args.baseline or not args.ciks:
        parser.error("--baseline and --ciks required")
    proof = guard.check(args.baseline)
    cutoff, outputs = args.as_of or now(), []
    for issuer in sorted({str(i).zfill(10) for i in args.ciks}):
        value = build(issuer, args.baseline, cutoff)
        folder = write(value)
        outputs.append({"cik": value["base_report"]["identity"]["cik"], "coverage": value["coverage"], "report_path": str(folder / "REPORT.ko.md")})
    guard.check(args.baseline, expected_sha256=proof["baseline_sha256"])
    print(json.dumps({"reports": outputs, "network_requests": 0, "external_llm_calls": 0, "released_score": None}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
