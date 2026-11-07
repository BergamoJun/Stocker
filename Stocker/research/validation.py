"""Frozen research diagnostics and append-only prospective records.

Numeric artifacts remain private. Retrospective diagnostics cannot certify a
model, change an input's observed timestamp, or emit a current price plan.
"""

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from pathlib import Path

import jsonschema

from .rights import as_utc
from .sec_report import ROOT
from .swing_models import _reference_baseline, candidate_plan, load_diagnostic_snapshot, replay_five_sessions


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stable_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def protocol():
    path = ROOT / "config/validation_protocol.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if data["release_allowed"] is not False or data["diagnostic"]["label_sessions"] != 5:
        raise ValueError("Research protocol cannot authorize release or change supported horizon")
    if data["candidate_policy_sha256"] != digest(ROOT / "config/swing_candidate_policy.json"):
        raise ValueError("Candidate policy changed after protocol freeze; register a new protocol")
    return data


def save_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write("\n")


def validate_artifact(value):
    schema = json.loads((ROOT / "schemas/validation_artifact.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(value)


def value_fingerprint(bar):
    # Decimal normalization avoids treating 1000 and 1000.0 as a data revision.
    def canonical(value):
        number = Decimal(value)
        if number == 0:
            return "0"
        return format(number, "f").rstrip("0").rstrip(".") if "." in format(number, "f") else format(number, "f")
    return stable_hash({"session": bar.session, **{key: canonical(getattr(bar, key))
        for key in ("open", "high", "low", "close", "volume")}})


def partition_summary(cases, screening):
    outcomes = Counter(case["outcome"] for case in cases)
    resolved = [case for case in cases if case["net_return_pct"] is not None]
    with localcontext() as context:
        context.prec = 34
        average = sum((Decimal(case["net_return_pct"]) for case in resolved), Decimal(0)) / len(resolved) if resolved else None
        excess = sum((Decimal(case["net_return_pct"]) - Decimal(case["comparator_net_return_pct"])
                      for case in resolved), Decimal(0)) / len(resolved) if resolved else None
        unresolved = Decimal(len(cases) - len(resolved)) / len(cases) if cases else Decimal(1)
    flags = {"sample_count": len(resolved) >= screening["minimum_resolved_cases"],
             "unresolved_fraction": unresolved <= Decimal(screening["maximum_ambiguous_or_untradeable_fraction"]),
             "positive_mean_net_return": average is not None and average > Decimal(screening["minimum_mean_net_return_pct_exclusive"]),
             "positive_mean_excess": excess is not None and excess > Decimal(screening["minimum_mean_excess_to_comparator_pct_exclusive"])}
    return {"cases": len(cases), "resolved": len(resolved), "outcomes": dict(sorted(outcomes.items())),
            "mean_net_return_pct": str(average) if average is not None else None,
            "mean_excess_to_comparator_pct": str(excess) if excess is not None else None,
            "unresolved_fraction": str(unresolved), "screening_flags": flags,
            "screening_passed": all(flags.values()), "release_validation_id": None}


def retrospective_diagnostic(snapshot_path, entitlement, as_of, output_dir):
    settings = protocol()
    if as_utc(as_of) < as_utc(settings["frozen_at"]):
        raise ValueError("Diagnostic cannot precede protocol registration")
    data, bars, calendar = load_diagnostic_snapshot(Path(snapshot_path), entitlement, as_of)
    if data["symbol"] != settings["symbol"]:
        raise ValueError("Protocol population is BE only")
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    # Bind protocol/input/code before calculating any outcomes. Never overwrite.
    registration = {"registered_at": now(), "protocol_id": settings["id"],
        "protocol_sha256": digest(ROOT / "config/validation_protocol.json"),
        "price_snapshot_sha256": digest(snapshot_path), "snapshot_id": data["snapshot_id"],
        "entitlement_sha256": stable_hash(entitlement),
        "code_sha256": {p.name: digest(p) for p in (Path(__file__), ROOT / "research/swing_models.py")},
        "data_regime": "retrospective_current_vintage_not_point_in_time",
        "price_input_observed_at": max((bar.available_at for bar in bars), key=as_utc),
        "is_illustrative": data["is_illustrative"], "no_current_plan": True}
    save_new(destination / "diagnostic_registration.json", registration)
    diagnostic = settings["diagnostic"]
    scenarios = []
    for cost in diagnostic["cost_bps_per_side"]:
        cases = []
        for index in range(diagnostic["minimum_history_sessions"] - 1, len(bars) - 5,
                           diagnostic["reference_stride_sessions"]):
            history, future = bars[:index + 1], bars[index + 1:index + 6]
            try:
                # Actual observations stay intact. This explicitly uses the current
                # vintage to inspect historical arithmetic, not availability then.
                plan = _reference_baseline(history, calendar, as_of)
                case = replay_five_sessions(plan, future, calendar, Decimal(cost))
            except ValueError:
                case = {"outcome": "invalid_target_configuration", "reference_session": bars[index].session,
                        "net_return_pct": None, "hit_session": None}
            fee = Decimal(cost) / 10000
            with localcontext() as context:
                context.prec = 34
                comparator = (future[-1].close * (1 - fee) / (future[0].open * (1 + fee)) - 1) * 100
            case.update(comparator_net_return_pct=str(comparator),
                        hypothetical_decision_at=calendar.close(history[-1].session),
                        label_period_ended_at=calendar.close(future[-1].session),
                        label_available_at=max((bar.available_at for bar in future), key=as_utc),
                        point_in_time_verified=False)
            cases.append(case)
        split = int(Decimal(len(cases)) * Decimal(diagnostic["training_fraction"]))
        train, held = cases[:split], cases[split:]
        summary = {name: partition_summary(rows, diagnostic["screening_only"])
                   for name, rows in (("training", train), ("held_out", held))}
        scenarios.append({"cost_bps_per_side": cost, "training_cases": train, "held_out_cases": held,
                          "summary": summary})
    result = {"schema_version": "1.0.0", "status": "retrospective_diagnostic_not_validation",
        "as_of": as_of, "registration": registration, "protocol_id": settings["id"],
        "price_bars": len(bars), "first_session": bars[0].session, "last_session": bars[-1].session,
        "scenarios": scenarios, "released_price_plan": None, "released_score": None,
        "released_expected_target_sessions": None, "release_validation_id": None,
        "limitations": ["Current split-adjusted/revised vintage, not historical point-in-time observations",
            "Conditional comparator uses the same resolved cohort; unresolved cases remain visible",
            "Single selected stock; no population/survivorship claim; no dividends or taxes",
            "News/financial score and timing calibration are outside this price-only diagnostic",
            "No parameter selection or production release is authorized by screening results"]}
    validate_artifact(result)
    save_new(destination / "retrospective_diagnostic.json", result)
    lines = ["# BE 가격 후보 사전 진단", "", "**실제 과거 가격의 현재 조회본을 사용한 진단이며 모델 검증 완료가 아니다.**", "",
             f"입력 세션: {bars[0].session} ~ {bars[-1].session}, {len(bars)}개", f"고정 평가 규칙: {settings['id']}", "",
             "| 편도 비용(bp) | 훈련 사례/해결 | 평가 사례/해결 | 평가 평균 순수익률(%) | 같은 사례 대비 평균 초과(%) |", "|---:|---:|---:|---:|---:|"]
    for row in scenarios:
        train, held = row["summary"]["training"], row["summary"]["held_out"]
        lines.append(f"| {row['cost_bps_per_side']} | {train['cases']}/{train['resolved']} | {held['cases']}/{held['resolved']} | {held['mean_net_return_pct']} | {held['mean_excess_to_comparator_pct']} |")
    lines.extend(["", "갭/동일 일봉 양방향 도달/불가 사례는 JSON에 별도 집계한다. 미해결 사례를 성공 거래로 계산하지 않는다.",
                  "수익률은 가정에 따른 비교값이며 실제 체결/투자 성과가 아니다. 원본 가용시각을 소급하지 않았다.",
                  "현재 목표가·매력도·도달기간·검증 ID는 모두 보류다. 뉴스 40% 모델의 성능 검증으로 사용하지 않는다.", ""])
    with (destination / "retrospective_diagnostic.md").open("x", encoding="utf-8") as output:
        output.write("\n".join(lines))
    return {"status": result["status"], "price_bars": len(bars),
            "case_count": len(scenarios[0]["training_cases"]) + len(scenarios[0]["held_out_cases"]),
            "output_dir": str(destination.resolve()), "release_validation_id": None}


def register_forecast(data, bars, calendar, as_of, ledger_dir):
    settings = protocol()
    if as_utc(as_of) < as_utc(settings["frozen_at"]):
        raise ValueError("Forecast precedes protocol registration")
    if data["symbol"] != settings["symbol"]:
        raise ValueError("Protocol population is BE only")
    plan = candidate_plan(bars, calendar, as_of)
    entry_session = plan["timing"]["sessions"][0]
    entry_at = calendar.calendar.session_open(entry_session).isoformat()
    recorded_at = now()
    if as_utc(as_of) >= as_utc(entry_at) or as_utc(recorded_at) >= as_utc(entry_at):
        raise ValueError("Forecast must be recorded before the next regular open")
    if as_utc(as_of) > as_utc(recorded_at):
        raise ValueError("Future-dated forecast cutoff")
    key = stable_hash({"symbol": data["symbol"], "reference_session": plan["reference_session"], "protocol_id": settings["id"]})[:24]
    destination = Path(ledger_dir) / key
    if (destination / "forecast.json").exists():
        return {"status": "already_recorded", "record_id": key, "output_dir": str(destination.resolve())}
    record = {"schema_version": "1.0.0", "record_id": key, "status": "prospective_research_forecast",
        "symbol": data["symbol"], "recorded_at": recorded_at, "as_of": as_of,
        "is_illustrative": data["is_illustrative"], "snapshot_id": data["snapshot_id"],
        "source_ids": data["source_ids"], "protocol_id": settings["id"],
        "protocol_sha256": digest(ROOT / "config/validation_protocol.json"),
        "candidate_policy_sha256": settings["candidate_policy_sha256"],
        "code_sha256": {p.name: digest(p) for p in (Path(__file__), ROOT / "research/swing_models.py")},
        "entry_at": entry_at, "candidate": plan,
        "history_fingerprints": {bar.session: value_fingerprint(bar) for bar in bars},
        "point_in_time_history_verified": data["point_in_time_verified"],
        "prediction_recorded_before_entry": True, "release_validation_id": None,
        "released_score": None, "released_price_plan": None,
        "limitation": "Prediction logged now using the observed vintage; does not certify historical vintages or model performance"}
    validate_artifact(record)
    save_new(destination / "forecast.json", record)
    return {"status": record["status"], "record_id": key, "output_dir": str(destination.resolve())}


def settle_forecasts(data, bars, calendar, as_of, ledger_dir, *, record_ids=None):
    settings = protocol()
    by_day = {bar.session: bar for bar in bars}
    results = []
    for path in sorted(Path(ledger_dir).glob("*/forecast.json")):
        forecast = json.loads(path.read_text(encoding="utf-8"))
        if record_ids is not None and forecast["record_id"] not in record_ids:
            continue
        if forecast["symbol"] != data["symbol"] or forecast["is_illustrative"] != data["is_illustrative"]:
            continue
        outcome_path = path.with_name("outcome.json")
        if outcome_path.exists():
            results.append({"record_id": forecast["record_id"], "status": "already_settled"})
            continue
        sessions = forecast["candidate"]["timing"]["sessions"]
        if any(day not in by_day for day in sessions) or as_utc(calendar.close(sessions[-1])) > as_utc(as_of):
            results.append({"record_id": forecast["record_id"], "status": "awaiting_five_session_outcome"})
            continue
        same_protocol = (forecast["protocol_sha256"] == digest(ROOT / "config/validation_protocol.json")
                         and forecast["protocol_id"] == settings["id"])
        overlap = set(forecast["history_fingerprints"]) & by_day.keys()
        if not same_protocol or forecast["candidate"]["reference_session"] not in overlap:
            results.append({"record_id": forecast["record_id"], "status": "protocol_or_reference_mismatch"})
            continue
        if any(forecast["history_fingerprints"][day] != value_fingerprint(by_day[day]) for day in overlap):
            results.append({"record_id": forecast["record_id"], "status": "input_revision_detected_outcome_withheld"})
            continue
        future = [by_day[day] for day in sessions]
        observed = max(as_utc(bar.available_at) for bar in future)
        if observed > as_utc(as_of) or observed < as_utc(forecast["recorded_at"]):
            results.append({"record_id": forecast["record_id"], "status": "unavailable_or_pre_forecast_outcome_snapshot"})
            continue
        scenario_results = []
        for cost in settings["diagnostic"]["cost_bps_per_side"]:
            result = replay_five_sessions(forecast["candidate"], future, calendar, Decimal(cost))
            result["label_period_ended_at"] = result["label_available_at"]
            result["label_available_at"] = observed.isoformat()
            scenario_results.append({"cost_bps_per_side": cost, "result": result})
        outcome = {"schema_version": "1.0.0", "record_id": forecast["record_id"], "status": "prospective_research_outcome",
            "evaluated_at": now(), "as_of": as_of, "outcome_snapshot_id": data["snapshot_id"],
            "source_ids": data["source_ids"], "scenarios": scenario_results, "release_validation_id": None}
        validate_artifact(outcome)
        save_new(outcome_path, outcome)
        results.append({"record_id": forecast["record_id"], "status": outcome["status"]})
    return results
