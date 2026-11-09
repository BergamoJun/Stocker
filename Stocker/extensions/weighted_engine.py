"""Automatic local multidimensional research; never rewrite old forecasts/jobs."""

import argparse
from decimal import Decimal, localcontext
import json
from pathlib import Path
import re
import uuid
import jsonschema

from research.local_files import atomic_json
from research.project_compatibility import check as historical_check
from research.rights import as_utc, require_entitlement, price_uses
from research.sec_report import ROOT
from research.sec_transport import now
from research.validation import digest, stable_hash
from . import analysis_contract, market_inputs, scope, weighted_features, weighted_events, weighted_critic

BASE = Path(__file__).resolve().parent
FILES = ["extensions/weighted_policy.json", "extensions/weighted_features.py", "extensions/weighted_events.py", "extensions/weighted_critic.py", "extensions/weighted_engine.py", "extensions/weighted_report.py", "extensions/weighted_engine.schema.json",
    "research/peak_simulation.py", "research/peak_model.py", "research/swing_models.py", "research/trade_screen.py", "config/peak_model_policy.json", "config/trade_screen_policy.json"]


def read(path): return json.loads(Path(path).read_text(encoding="utf-8"))
def methods(): return {p: digest(ROOT / p) for p in FILES}


def policy():
    value = read(BASE / "weighted_policy.json")
    if value["release_enabled"] or value["model_training_enabled"] or value["monthly_data_budget_usd"] != 0 or value["external_model_calls"] != 0 or value["horizon_sessions"] != 5:
        raise ValueError("WEIGHTED_RESEARCH_BOUNDARY_CHANGED")
    if value["groups"] != read(ROOT / "config/peak_model_policy.json")["score_weights"]: raise ValueError("GROUP_PRIOR_MAPPING_CHANGED")
    if len(value["features"]) != 23 or sum(value["groups"].values()) != 100: raise ValueError("INVALID_WEIGHT_BUDGET")
    for group, budget in value["groups"].items():
        if sum((weighted_features.number(s["weight"]) for s in value["features"].values() if s["group"] == group), Decimal(0)) != budget:
            raise ValueError("INVALID_GROUP_COEFFICIENTS")
    return value


def resolve(ticker):
    settings = policy()
    selected = next((c for c, p in settings["issuer_mechanisms"].items() if p["symbol"] == ticker), None)
    if not selected: raise ValueError("UNSUPPORTED_ENGINE_ISSUER")
    candidates = []
    for p in (ROOT / "reports/private/analysis_inputs/real" / selected).glob("*/AI_INPUT.json"):
        if p.with_name("REJECTED.json").exists(): continue
        ai = read(p)
        if ai["identity"]["cik"] == selected and ai["identity"]["symbol"] == ticker and ai["is_illustrative"] is False:
            candidates.append((as_utc(ai["as_of"]), p.parent))
    if not candidates: raise ValueError("NO_STORED_COMMON_INPUT_FOR_ISSUER")
    return max(candidates, key=lambda row: row[0])[1]


def calculate(bundle, generated_at, settings):
    ai = bundle["ai_input"]
    if as_utc(ai["as_of"]) > as_utc(generated_at) or as_utc(settings["frozen_at"]) > as_utc(generated_at): raise ValueError("FUTURE_EVIDENCE_OR_UNFROZEN_ENGINE")
    with localcontext() as context:
        context.prec = 60
        factors, numeric = weighted_features.numeric(bundle, generated_at, settings)
        analysis = weighted_events.analyze(ai, generated_at, settings)
        factors.update(analysis.pop("factors"))
        numeric["market_source_ids"] = sorted({sid for row in bundle["local_input"].get("market", {}).get("roles", {}).values() for sid in row.get("source_ids", [])})
        assessment = weighted_critic.score(factors, settings)
        critic = weighted_critic.critique(factors, assessment, numeric, analysis, ai, settings)
    if assessment["missing_factors"]:
        one_line = f"수익 기회·가격·실적·시장과 최신 사건의 세부 계산을 수행했으나 {len(assessment['missing_factors'])}개 변수의 근거가 부족해 종합 추천도는 보류합니다."
    else: one_line = "모든 변수의 연구용 계수 계산을 완료했습니다. 실증 검증 전이므로 정식 투자 추천도는 보류합니다."
    return {"analysis": analysis, "factors": [factors[key] for key in settings["features"]], "assessment": assessment, "numeric_research": numeric, "critic": critic, "one_line_ko": one_line}


def build(input_folder, baseline, *, clock=now, run_id=None):
    generated_at = clock(); settings = policy()
    historical_check(baseline)
    input_folder = Path(input_folder).resolve()
    # Entitlement check precedes normalized-price use and the expensive input replay.
    envelope = read(input_folder / "bundle.json")
    if envelope["origin"].get("market_path"):
        _, source = market_inputs.record(envelope["origin"]["market_path"])
        require_entitlement(source["entitlement"], price_uses("local_research"), generated_at, synthetic=envelope["is_illustrative"])
    bundle = analysis_contract.validate(input_folder)
    result = {"contract": "weighted-swing-research-v1", "run_id": run_id or uuid.uuid4().hex, "status": "research_only_unvalidated", "identity": bundle["ai_input"]["identity"],
        "generated_at": generated_at, "evidence_as_of": bundle["as_of"], "is_illustrative": bundle["is_illustrative"],
        "origin": {"input_folder": str(input_folder), "input_bundle_sha256": digest(input_folder / "bundle.json"), "baseline_path": str(Path(baseline).resolve()), "baseline_sha256": digest(baseline)},
        "method_hashes": methods(), **calculate(bundle, generated_at, settings), "released_score": None, "released_price_plan": None,
        "released_expected_target_sessions": None, "external_model_calls": 0, "model_training_performed": False}
    result["record_sha256"] = stable_hash(result)
    schema(result)
    return result


def schema(value):
    jsonschema.Draft202012Validator(read(BASE / "weighted_engine.schema.json"), format_checker=jsonschema.FormatChecker()).validate(value)


def write(value, output=None):
    from .weighted_report import render
    schema(value)
    if not re.fullmatch(r"[a-f0-9]{32}", value["run_id"]): raise ValueError("INVALID_ENGINE_RUN_ID")
    root = Path(output or ROOT / "reports/private/weighted_engine").resolve()
    if not value["is_illustrative"] and not root.is_relative_to((ROOT / "reports/private").resolve()): raise ValueError("PRIVATE_ENGINE_STORE_REQUIRED")
    folder = root / ("illustrative" if value["is_illustrative"] else "real") / value["identity"]["cik"] / value["run_id"]
    folder.mkdir(parents=True, exist_ok=False)
    atomic_json(folder / "ENGINE.json", value)
    atomic_json(folder / "ANALYSIS.json", value["analysis"]["analysis_output"])
    (folder / "REPORT.ko.md").write_text(render(value), encoding="utf-8")
    atomic_json(folder / "FILES.json", {p.name: digest(p) for p in folder.iterdir() if p.is_file()})
    return folder


def validate(folder):
    from .weighted_report import render
    folder = Path(folder); value = read(folder / "ENGINE.json"); schema(value)
    if (folder / "REJECTED.json").exists(): raise ValueError("ENGINE_RECORD_REJECTED")
    if value["run_id"] != folder.name or value["method_hashes"] != methods() or stable_hash({k: v for k, v in value.items() if k != "record_sha256"}) != value["record_sha256"]:
        raise ValueError("ENGINE_RECORD_OR_METHOD_CHANGED")
    origin = value["origin"]
    historical_check(origin["baseline_path"], expected_sha256=origin["baseline_sha256"])
    if digest(Path(origin["input_folder"]) / "bundle.json") != origin["input_bundle_sha256"]: raise ValueError("ENGINE_INPUT_CHANGED")
    rebuilt = build(origin["input_folder"], origin["baseline_path"], clock=lambda: value["generated_at"], run_id=value["run_id"])
    if rebuilt != value: raise ValueError("ENGINE_CALCULATION_REPLAY_CHANGED")
    if read(folder / "ANALYSIS.json") != value["analysis"]["analysis_output"] or (folder / "REPORT.ko.md").read_text(encoding="utf-8") != render(value): raise ValueError("ENGINE_OUTPUT_RENDER_CHANGED")
    files = read(folder / "FILES.json")
    if set(files) != {"ENGINE.json", "ANALYSIS.json", "REPORT.ko.md"} or any(digest(folder / name) != sha for name, sha in files.items()): raise ValueError("ENGINE_OUTPUT_FILES_CHANGED")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path); parser.add_argument("--input", type=Path); parser.add_argument("--ticker"); parser.add_argument("--validate", type=Path)
    args = parser.parse_args(argv)
    if args.validate:
        value = validate(args.validate); print(json.dumps({"status": "validated", "symbol": value["identity"]["symbol"]})); return
    if not args.baseline or bool(args.input) == bool(args.ticker): parser.error("--baseline and exactly one of --input / --ticker required")
    proof = scope.check(args.baseline)
    value = build(args.input or resolve(args.ticker), args.baseline)
    scope.check(args.baseline, expected_sha256=proof["baseline_sha256"])
    folder = write(value); validate(folder)
    print(json.dumps({"status": value["status"], "symbol": value["identity"]["symbol"], "available_factors": sum(f["status"] == "available" for f in value["factors"]),
        "missing_factors": len(value["assessment"]["missing_factors"]), "folder": str(folder), "external_model_calls": 0}))


if __name__ == "__main__":
    try: main()
    except (ValueError, OSError, KeyError, TypeError, jsonschema.ValidationError) as error:
        code = str(error)
        print(json.dumps({"status": "unavailable", "reason": code if re.fullmatch(r"[A-Z][A-Z0-9_]{0,100}", code) else "ENGINE_INPUT_OR_VALIDATION_FAILED"}))
        raise SystemExit(2) from None
