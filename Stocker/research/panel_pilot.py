"""Real BE engineering smoke test; explicitly not a representative sector test."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from . import historical_panel, panel_prices, sector_universe
from .learned_research import latest_training_snapshot
from .local_files import atomic_json
from .observe import key_from_user
from .panel_transport import Client, catalog_url, detail_url
from .rights import price_uses, require_entitlement
from .sec_report import ROOT
from .validation import now


def run(definition_source):
    source_path, record_path = latest_training_snapshot()
    record = json.loads(record_path.read_text(encoding="utf-8"))
    require_entitlement(record, price_uses("local_research"), now())
    client = Client(record, key_from_user())
    settings = sector_universe.policy()
    folder = ROOT / "reports/private/historical_panel_pilot" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    definitions = sector_universe.definitions(folder, source_path=definition_source)["groups"]
    evidence = client.get(catalog_url(settings["universe_date"]))
    rows, _, _ = sector_universe.catalog_page(json.loads(evidence["raw"]))
    item = next(row for row in rows if row["ticker"] == "BE")
    identity = client.get(detail_url("BE", settings["universe_date"]))
    approved = sector_universe.profile(json.loads(identity["raw"]), item, definitions, identity)
    original = json.loads(source_path.read_text(encoding="utf-8"))
    source = {key: original[key] for key in ("source_url", "retrieved_at", "raw_sha256")}
    path = panel_prices.save(folder / "prices", source_path.with_name("raw_response.json").read_bytes(), approved, record, source)
    data = json.loads(path.read_text(encoding="utf-8"))
    last = data["bars"][-1]["session"]
    final = client.get(detail_url("BE", last))
    verified = sector_universe.verify_last_identity(json.loads(final["raw"]), approved, final, last)
    atomic_json(folder / "last_identity.json", verified)
    atomic_json(folder / "price_entitlement.json", record)
    result = historical_panel.evaluate_stock(path, approved, record, folder / "evaluation", pilot=True)
    atomic_json(folder / "run.json", {"status": result["status"], "completed_cases": result["completed_cases"],
        "new_network_requests": client.network_requests, "original_price_snapshot_path": str(source_path.resolve()),
        "price_download_reused": True, "source_ids": data["source_ids"], "final_cases_executed": 0,
        "model_training_performed": False, "external_llm_calls": 0, "released_score": None})
    lines = ["# BE 과거 자료를 이용한 실행 경로 확인", "",
        "이번 실행은 새 다종목 평가기가 실제 자료를 읽고 계산·저장을 끝내는지 확인하는 작업입니다.",
        "BE는 이미 여러 번 살펴본 종목입니다. 이 결과로 다른 산업군이나 처음 보는 기업에서도 예측이 잘 된다고 판단할 수 없습니다.", "",
        "## 어떤 자료로 무엇을 비교했나요?", "",
        "기존에 내려받은 BE의 실제 일별 가격을 다시 사용했습니다. 미래 가격은 예측을 만든 다음 정답 대조에만 썼습니다.",
        "현재 시점에 수정·분할 조정된 과거 자료이므로, 당시에도 같은 자료를 구할 수 있었다고 인증한 시험은 아닙니다.",
        "과거 날짜의 기업 식별과 SIC 산업 코드를 확인했고, 마지막 가격일의 기업·주식 종류가 같은지도 대조했습니다.", "",
        f"처리한 예측 기준일: {result['completed_cases']}건. 마지막 시험 기간 16건과 처음 보는 기업의 시험은 실행하지 않았습니다.",
        "아래 수치는 개인용 로컬 연구 결과입니다. 아직 투자 점수나 검증된 수익률로 공개하지 않습니다.", "",
        "| 구간 | 처리 기준일 | 계산 가능 | 자료·계산 부족 | 고점 구간 적중 비율 | 고점 발생일 평균 오차(거래일) |", "|---|---:|---:|---:|---:|---:|"]
    for name, part in result["partitions"].items():
        label = "개발용" if name == "development" else "중간 확인용"
        hit = part["modal_price_bin_hit_fraction"]
        error = part["mean_peak_day_absolute_error_sessions"]
        lines.append(f"| {label} | {part['cases']} | {part['forecastable_cases']} | {part['unavailable_cases']} | {hit:.3f} | {error:.3f} |" if hit is not None else f"| {label} | {part['cases']} | 0 | {part['unavailable_cases']} | 미산출 | 미산출 |")
    lines += ["", "고점 구간은 향후 5거래일 중 최고가가 들어갈 것으로 계산한 가격 범위입니다. 고점 발생일과 목표가에 처음 닿는 날은 서로 다릅니다.",
        "거래 비용은 매수·매도 각각 0.10%와 0.25%를 비교합니다. 같은 날 목표가·손절가를 모두 건드리면 가능한 수익의 하한·상한을 둘 다 남깁니다.",
        "자료 부족은 거래하지 않았을 때의 0% 수익과 구별했습니다. 건별 수익의 단순 평균은 실제 계좌의 누적 수익률과 다릅니다.", "",
        "## 결과를 어떻게 사용하나요?", "",
        "실행기 점검을 마쳤습니다. 예측 성능의 개선 여부는 산업별 100개 기업을 목표로 고정한 별도 표본에서 판단해야 합니다.",
        "다음은 종목 명부·분류 수집 → 성과를 보기 전 표본 고정 → 가격과 식별 확인 → 개발·중간 구간 평가 → 부족한 산업군과 비용 후 결과 검토입니다.",
        "학습·수식 튜닝·마지막 시험 구간 개방·뉴스를 과거에 소급 적용하는 작업은 이번 실행에 포함하지 않았습니다.", ""]
    (folder / "REPORT.ko.md").write_text("\n".join(lines), encoding="utf-8")
    return {"status": result["status"], "symbol": "BE", "completed_cases": result["completed_cases"],
        "phase_case_counts": {name: part["cases"] for name, part in result["partitions"].items()},
        "unavailable_cases": sum(part["unavailable_cases"] for part in result["partitions"].values()),
        "final_cases_executed": 0, "new_network_requests": client.network_requests,
        "external_llm_calls": 0, "output_dir": str(folder.resolve())}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--definition-source", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = run(args.definition_source)
    except (ValueError, OSError, RuntimeError, KeyError, StopIteration):
        print(json.dumps({"status": "withheld", "reason_code": "PILOT_SOURCE_OR_INPUT_CHECK_FAILED", "external_llm_calls": 0}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
