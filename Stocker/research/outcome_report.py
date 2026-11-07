"""Explain the first prospective observation without promoting it to validation."""

from decimal import Decimal
import json
from pathlib import Path

import jsonschema

from .peak_model import net_return
from .report_language import percent
from .sec_report import ROOT
from .validation import save_new


def build(job, snapshot, bars, as_of):
    peak_target = next(row for row in job["targets"] if row["kind"] == "peak")
    forecast = json.loads(Path(peak_target["path"]).read_text(encoding="utf-8"))
    prediction = forecast["prediction"]
    plan = prediction["plan"]
    by_day = {row.session: row for row in bars}
    future = [by_day[day] for day in job["sessions"]]
    actual_peak = max(row.high for row in future)
    low, high = map(Decimal, prediction["peak"]["modal_price_interval"])
    context = next(row for row in job["targets"] if row["kind"] == "context")
    decision = json.loads(Path(context["path"]).read_text(encoding="utf-8"))["paper_action"]
    outcome = json.loads(Path(peak_target["path"]).with_name("outcome.json").read_text(encoding="utf-8"))
    costs = []
    for item in outcome["outcomes"]:
        row = item["result"]
        cost = item["cost_bps_per_side"]
        if row["outcome"] == "ambiguous_same_bar":
            bounds = [net_return(future[0].open, Decimal(plan[key]), cost) for key in ("invalidation_price", "first_target_price")]
        elif row["outcome"] == "untradeable_open_gap":
            bounds = [Decimal(0), Decimal(0)]
        else:
            bounds = [Decimal(row["net_return_pct"])] * 2
        comparator = net_return(future[0].open, future[-1].close, cost)
        costs.append({"cost_bps_per_side": str(cost), "hypothetical_forecast_outcome": row["outcome"],
            "hypothetical_forecast_net_return_pct_bounds": list(map(str, bounds)),
            "original_screen_action_return_pct_bounds": ["0", "0"] if decision == "cash" else list(map(str, bounds)),
            "buy_and_hold_same_period_net_return_pct": str(comparator), "target_before_stop_hit_session": row.get("hit_session")})
    return {"status": "one_prospective_observation_not_model_validation", "job_id": job["job_id"], "as_of": as_of,
        "is_illustrative": snapshot["is_illustrative"], "symbol": "BE", "sessions": job["sessions"],
        "forecast_recorded_at": forecast["recorded_at"], "original_screen_paper_action": decision,
        "observed_records": 1, "source_ids": snapshot["source_ids"],
        "outcome_snapshot_id": snapshot.get("snapshot_id"), "price_source_url": snapshot.get("source_url"), "price_retrieved_at": snapshot.get("retrieved_at"),
        "observed_peak_price": str(actual_peak), "forecast_peak_interval": list(map(str, (low, high))),
        "peak_interval_contains_observed_high": low <= actual_peak < high,
        "observed_peak_sessions": [index for index, row in enumerate(future, 1) if row.high == actual_peak],
        "forecast_peak_session": prediction["peak"]["conditional_day_mode"],
        "anytime_target_touch_session": next((index for index, row in enumerate(future, 1) if row.high >= Decimal(plan["first_target_price"])), None),
        "cost_scenarios": costs, "released_score": None, "validation_id": None, "actual_broker_execution": False}


def markdown(result):
    action = "거래하지 않음" if result["original_screen_paper_action"] == "cash" else "가격 시나리오를 가상으로 실행"
    lines = ["# BE 최초 사전 예측의 실제 관찰 결과", "", "## 무엇을 확인했나요?", "",
        f"거래 시작 전에 저장한 가격 예측을 {result['sessions'][0]}~{result['sessions'][-1]}의 실제 5거래일 가격과 비교했습니다.",
        "이 문서의 수익은 정해 둔 가격·거래 비용 규칙으로 계산한 가상 손익입니다. 실제 계좌의 체결 수익은 아닙니다.",
        "", "## 가격 예측은 어떻게 됐나요?", "",
        f"예상 고점 구간 안에 실제 최고가가 들어왔는지: {'들어왔음' if result['peak_interval_contains_observed_high'] else '벗어났음'}.",
        f"모형이 예상한 고점 형성일: 관찰 {result['forecast_peak_session']}일째. 실제 최고가가 나온 날: {result['observed_peak_sessions']}일째.",
        "기간 전체의 최고가와 목표가에 먼저 닿는 날은 서로 다른 평가 항목입니다. 목표에 닿았어도 그 전에 손실 기준에 먼저 닿았다면 이익 거래로 세지 않습니다.",
        "", "## 비용을 빼면 어떤 결과였나요?", "", f"처음 저장한 비용·위험 판단의 가상 선택: **{action}**.",
        "같은 기간에 처음부터 끝까지 보유한 경우도 같은 매수·매도 비용으로 비교합니다.", "",
        "| 매수·매도 각각의 비용 | 가격 예측을 따라간 가상 손익 | 최초 비용·위험 판단의 가상 손익 | 같은 기간 단순 보유 손익 |", "|---|---|---|---|"]
    for row in result["cost_scenarios"]:
        bounds = row["hypothetical_forecast_net_return_pct_bounds"]
        chosen = row["original_screen_action_return_pct_bounds"]
        lines.append(f"| {Decimal(row['cost_bps_per_side']) / 100:.2f}% | {percent(bounds[0])} ~ {percent(bounds[1])} | {percent(chosen[0])} ~ {percent(chosen[1])} | {percent(row['buy_and_hold_same_period_net_return_pct'])} |")
    lines += ["", "같은 날 목표와 손실 기준에 모두 닿으면 일봉만으로 순서를 알 수 없어 손익 범위를 표시합니다. 진입 시가가 허용 구간 밖이면 진입하지 않은 경우로 구분합니다.",
        "", "## 이 결과로 무엇을 판단할 수 있나요?", "",
        "확인한 실제 관찰은 1건입니다. 가격 구간·시기·비용 후 손익의 첫 비교 자료이며, 예측력이나 수익성을 승인할 표본은 아닙니다.",
        "뉴스 점수나 학습형 AI가 예측을 개선했는지는 이 한 건으로 평가하지 않습니다. 여러 시점의 사전 예측과 결과를 계속 쌓아야 합니다.",
        "정식 투자 매력도·검증 ID는 계속 비워 둡니다.", ""]
    return "\n".join(lines)


def write(folder, result):
    schema = json.loads((ROOT / "schemas/observation_job.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator({"$ref": "#/$defs/observation", "$defs": schema["$defs"]}, format_checker=jsonschema.FormatChecker()).validate(result)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "observation.json"
    if not path.exists():
        save_new(path, result)
    elif json.loads(path.read_text(encoding="utf-8")) != result:
        raise ValueError("Completed observation cannot be replaced")
    (folder / "report.md").write_text(markdown(result), encoding="utf-8")
