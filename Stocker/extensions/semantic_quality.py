"""One frozen local-model meaning test; never a market-performance test."""

import json
import hashlib
from pathlib import Path
from decimal import Decimal
import jsonschema
from research.local_files import atomic_json
from research.sec_report import ROOT
from research.sec_transport import now
from research.validation import stable_hash, digest
from . import semantic_research as semantic, local_inference

BASE = Path(__file__).resolve().parent


def evaluate(backend, *, clock=now):
    cases = semantic.read(BASE / "semantic_quality_cases.json")["cases"]
    settings = local_inference.policy(); rows = []; accepted = exact = safety_errors = unexpected_abstentions = 0
    for case in cases:
        payload = {"source_id":"synthetic_"+case["id"], "paragraph_id":"p0001", "source":"federal_reserve_board", "published_at":None, "time_precision":"date", "text":case["text"], "source_record_sha256":"a"*64, "paragraph_sha256":hashlib.sha256(case['text'].encode()).hexdigest()}
        row = {"id":case["id"], "status":"rejected", "correct_labels":False, "response":None, "usage":None}
        try:
            value, usage = semantic.classify(payload, backend)
            row.update(status="accepted", response=value, usage=usage); accepted += 1
            correct = all(value[k] in case[k] for k in ("event_type", "variable_direction", "claim_kind", "horizon"))
            row["correct_labels"] = correct; exact += correct
            if case.get("safety_case") and not correct: safety_errors += 1
            if value["horizon"] == "within_five_sessions" and "within_five_sessions" not in case["horizon"]: safety_errors += 1
            if case["event_type"] not in (["none","unknown"], ["unknown","none"]) and value["event_type"] in {"none","unknown"}: unexpected_abstentions += 1
        except (ValueError, KeyError, jsonschema.ValidationError) as error:
            row["reason"] = "CLASSIFICATION_OR_SOURCE_GUARD_REJECTED"; unexpected_abstentions += not case.get("safety_case",False)
            if case.get("safety_case"): row["correct_labels"] = True; exact += 1
        rows.append(row)
    count = len(cases); q = settings["quality"]
    passed = Decimal(exact)/count >= Decimal(q["minimum_label_accuracy"]) and Decimal(accepted)/count >= Decimal(q["minimum_contract_acceptance"]) and safety_errors <= q["maximum_safety_errors"] and Decimal(unexpected_abstentions)/count <= Decimal(q["maximum_unexpected_abstention_fraction"])
    result = {"evaluated_at":clock(), "decision":"scoped_semantic_use_allowed" if passed else "quality_gate_failed", "cases":count, "accepted":accepted, "correct":exact, "safety_errors":safety_errors, "unexpected_abstentions":unexpected_abstentions,
        "method_hashes":semantic.methods(), "quality_code_sha256":digest(BASE / "semantic_quality.py"), "cases_sha256":digest(BASE / "semantic_quality_cases.json"), "model_sha256":settings["model_sha256"], "details":rows,
        "quality_scope":"synthetic bounded economic classification only; not causal price analysis or market performance", "training_performed":False, "external_calls":0}
    result["record_sha256"] = stable_hash(result)
    return result


def validate_receipt(value):
    if value["quality_code_sha256"] != digest(BASE / "semantic_quality.py") or value["cases_sha256"] != digest(BASE / "semantic_quality_cases.json"): raise ValueError("QUALITY_METHOD_OR_CASES_CHANGED")
    cases = {c["id"]:c for c in semantic.read(BASE / "semantic_quality_cases.json")["cases"]}
    if len(value["details"]) != len(cases) or {r["id"] for r in value["details"]} != set(cases): raise ValueError("QUALITY_CASE_SET_CHANGED")
    accepted = correct = safety = abstentions = 0
    for row in value["details"]:
        case = cases[row["id"]]
        if row["status"] == "accepted":
            response = semantic.guard(row["response"], case["text"]); accepted += 1
            match = all(response[k] in case[k] for k in ("event_type", "variable_direction", "claim_kind", "horizon")); correct += match
            safety += bool(case.get("safety_case") and not match) + bool(response["horizon"] == "within_five_sessions" and "within_five_sessions" not in case["horizon"])
            abstentions += case["event_type"] not in (["none","unknown"], ["unknown","none"]) and response["event_type"] in {"none","unknown"}
        else:
            correct += bool(case.get("safety_case")); abstentions += not case.get("safety_case",False)
    if (value["cases"],value["accepted"],value["correct"],value["safety_errors"],value["unexpected_abstentions"]) != (len(cases),accepted,correct,safety,abstentions): raise ValueError("QUALITY_COUNTS_CHANGED")
    q = local_inference.policy()["quality"]; n = len(cases)
    passed = Decimal(correct)/n >= Decimal(q["minimum_label_accuracy"]) and Decimal(accepted)/n >= Decimal(q["minimum_contract_acceptance"]) and safety <= q["maximum_safety_errors"] and Decimal(abstentions)/n <= Decimal(q["maximum_unexpected_abstention_fraction"])
    if value["decision"] != ("scoped_semantic_use_allowed" if passed else "quality_gate_failed"): raise ValueError("QUALITY_DECISION_CHANGED")
    return passed
