"""Read-only explanation of frozen panel outcomes; never forecasts or trades."""

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

import jsonschema

from . import historical_panel, panel_statistics as stats
from .local_files import atomic_json
from .panel_transport import disk_name
from .rights import RightsError, as_utc, price_uses, require_entitlement
from .sec_report import ROOT
from .validation import digest, now, stable_hash

LABEL = "ILLUSTRATIVE — NOT LIVE MARKET DATA"
CORE = ["research/panel_diagnostics.py", "research/panel_statistics.py", "research/panel_diagnostic_report.py",
        "research/panel_diagnostics_demo.py", "config/panel_diagnostics_policy.json", "schemas/panel_diagnostics.schema.json"]
OUTCOMES = {"target", "stop", "timeout", "ambiguous_same_bar", "untradeable_open_gap", "stand_aside_no_positive_plan"}


class InputError(ValueError):
    """Fixed reason codes only, never exception text containing licensed values."""


def read(path, sources=None):
    path = Path(path)
    raw = path.read_bytes()
    if sources is not None:
        sources.append({"path": str(path.resolve()), "sha256": hashlib.sha256(raw).hexdigest()})
    def reject(_):
        raise InputError("NONFINITE_JSON_INPUT")
    return json.loads(raw, parse_constant=reject)


def policy():
    value = read(ROOT / "config/panel_diagnostics_policy.json")
    if value["release_enabled"] is not False or value["model_training_enabled"] is not False or value["monthly_data_budget_usd"] != 0 or value["network_requests"] != 0 or value["external_llm_calls"] != 0 or value["final_evaluation_locked"] is not True:
        raise InputError("DIAGNOSTIC_RESEARCH_BOUNDARY_CHANGED")
    if value["permitted_phases"] != ["development", "validation"] or value["cost_bps_per_side"] != ["10", "25"] or value["bootstrap"]["method"] != "non_circular_moving_calendar_blocks":
        raise InputError("UNSUPPORTED_DIAGNOSTIC_PROTOCOL")
    return value


def inside(path, root):
    path = Path(path).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise InputError("INPUT_OUTSIDE_PRIVATE_SOURCE")
    return path


def unavailable(profile, ref, reason):
    return {"symbol": profile["symbol"], "cik": profile["cik"], "sector_id": profile["sector_id"],
        **ref, "reference_month": ref["reference_session"][:7], "volatility_group": "unavailable", "liquidity_group": "unavailable",
        "status": "unavailable", "reason_code": reason, "screen_decision": "unavailable", "cost_scenarios": []}


def compact_case(row, profile, ref, illustrative):
    if row.get("phase") != ref["phase"] or row.get("reference_session") != ref["reference_session"] or row.get("label_sessions") != ref["label_sessions"]:
        raise InputError("CASE_CALENDAR_OR_PHASE_MISMATCH")
    if row.get("is_illustrative") is not illustrative or row.get("point_in_time_verified") is not False or row.get("released_score") is not None:
        raise InputError("CASE_CLASSIFICATION_OR_RELEASE_MISMATCH")
    if not row.get("source_ids") or not isinstance(row["source_ids"], list):
        raise InputError("CASE_SOURCE_IDS_MISSING")
    if row.get("status") == "unavailable":
        if row.get("cost_scenarios") != [] or not row.get("reason_code") or row.get("screen_decision") != "unavailable":
            raise InputError("UNAVAILABLE_CASE_IS_NOT_CASH")
        return {**unavailable(profile, ref, row["reason_code"]), "source_ids": row["source_ids"],
            "original_evaluation_at": row.get("actual_evaluation_at")}
    if row.get("status") != "retrospective_diagnostic_not_validation" or row.get("screen_decision") not in {"numerical_candidate", "stand_aside"}:
        raise InputError("UNSUPPORTED_CASE_STATUS")
    result = {**unavailable(profile, ref, None), "status": row["status"], "screen_decision": row["screen_decision"],
        "volatility_group": row["volatility_group"], "liquidity_group": row["liquidity_group"],
        "source_ids": row["source_ids"], "original_evaluation_at": row.get("actual_evaluation_at"),
        "original_label_available_at": row.get("label_available_at")}
    for key in ("absolute_peak_error_atr", "peak_day_absolute_error", "modal_price_bin_hit"):
        result[key] = row[key]
    if type(result["modal_price_bin_hit"]) is not bool or not 0 <= stats.number(result["peak_day_absolute_error"]) <= 4 or stats.number(result["absolute_peak_error_atr"]) < 0:
        raise InputError("INVALID_PEAK_METRIC")
    for key in ("absolute_peak_error_atr", "peak_day_absolute_error"):
        if not math.isfinite(float(stats.number(result[key]))):
            raise InputError("INVALID_PEAK_METRIC")
    if row.get("reference_month") != ref["reference_session"][:7]:
        raise InputError("CASE_MONTH_MISMATCH")
    if row["volatility_group"] not in {"atr_below_2pct", "atr_2_to_4pct", "atr_at_least_4pct"} or row["liquidity_group"] not in {"dollar_volume_below_1m", "dollar_volume_1m_to_10m", "dollar_volume_at_least_10m"}:
        raise InputError("CASE_CONTEXT_GROUP_INVALID")
    costs = row["cost_scenarios"]
    if len(costs) != 2 or {item["cost_bps_per_side"] for item in costs} != {"10", "25"}:
        raise InputError("COST_SCENARIOS_INCOMPLETE")
    for item in sorted(costs, key=lambda value: int(value["cost_bps_per_side"])):
        outcome = item["unfiltered_outcome"]
        if outcome["outcome"] not in OUTCOMES:
            raise InputError("INVALID_EXECUTION_OUTCOME")
        clean = {"cost_bps_per_side": item["cost_bps_per_side"], "unfiltered_outcome": {"outcome": outcome["outcome"], "hit_session": outcome.get("hit_session")}}
        for name in ("unfiltered", "screened"):
            bounds = item[name + "_net_return_pct_bounds"]
            if not isinstance(bounds, list) or len(bounds) != 2:
                raise InputError("INVALID_RETURN_BOUNDS")
            low, high = map(stats.number, bounds)
            if low > high or low < -100 or any(not math.isfinite(float(x)) for x in (low, high)):
                raise InputError("INVALID_RETURN_BOUNDS")
            clean[name + "_net_return_pct_bounds"] = list(map(str, (low, high)))
        screened = list(map(stats.number, clean["screened_net_return_pct_bounds"]))
        if row["screen_decision"] == "stand_aside" and screened != [0, 0]:
            raise InputError("STAND_ASIDE_RETURN_NOT_ZERO")
        if row["screen_decision"] == "numerical_candidate" and clean["screened_net_return_pct_bounds"] != clean["unfiltered_net_return_pct_bounds"]:
            raise InputError("SCREEN_AND_EXECUTION_DIFFER")
        hold = stats.number(item["five_session_hold_net_return_pct"])
        if hold < -100 or not math.isfinite(float(hold)):
            raise InputError("INVALID_COMPARATOR_RETURN")
        clean["five_session_hold_net_return_pct"] = str(hold)
        timing = item["target_timing_evaluable"]
        coverage = item["target_hit_inside_conditional_timing_range"]
        if type(timing) is not bool or (timing and (type(coverage) is not bool or outcome["outcome"] != "target" or type(outcome.get("hit_session")) is not int or not 1 <= outcome["hit_session"] <= 5)) or (not timing and coverage is not None):
            raise InputError("INVALID_CONDITIONAL_TIMING")
        clean.update(target_timing_evaluable=timing, target_hit_inside_conditional_timing_range=coverage)
        result["cost_scenarios"].append(clean)
    return result


def cases_from_folder(folder, profile, refs, illustrative, sources):
    case_dir = inside(Path(folder) / "cases", folder)
    expected = {ref["reference_session"] + ".json" for ref in refs}
    if case_dir.exists() and any(path.name not in expected for path in case_dir.glob("*.json")):
        # Inspect names only. In particular, never open an extra final-period file.
        raise InputError("UNEXPECTED_OR_FINAL_CASE_FILE")
    result = []
    for ref in refs:
        path = inside(case_dir / (ref["reference_session"] + ".json"), folder)
        if not path.exists():
            result.append(unavailable(profile, ref, "EXPECTED_CASE_MISSING"))
        else:
            row = read(path, sources)
            result.append(compact_case(row, profile, ref, illustrative))
    return result


def load_job(path):
    from .panel_job import check
    path = inside(path, ROOT / "reports/private/historical_panel")
    job = check(path)
    folder, sources = path.parent, []
    record = read(folder / "price_entitlement.json", sources)
    require_entitlement(record, price_uses("local_research"), now())
    read(path, sources)
    if (folder / "integrity_failure.json").exists():
        raise InputError("SOURCE_JOB_INTEGRITY_FAILURE")
    state = read(folder / "state.json", sources) if (folder / "state.json").exists() else {"status": "not_started", "stage": "catalog"}
    progress = {key: state.get(key, 0) for key in ("network_requests", "catalog_eligible_symbols", "profile_index", "profiles_verified", "profiles_unresolved", "completed_cases")}
    progress.update(status=state["status"], stage=state["stage"])
    base = {"source_kind": "panel", "is_illustrative": False, "source_progress": progress, "sources": sources,
        "source_ids": ["historical-panel-job-" + job["job_id"]], "profiles": [], "sectors": [], "references": [], "cases": []}
    if state["status"] not in {"complete", "complete_with_gaps"} or state["stage"] != "done":
        return {**base, "status": "source_incomplete" if state["status"] in {"withheld", "exhausted"} else "waiting_for_panel_results"}
    cohort_path = folder / "cohort.json"
    if digest(cohort_path) != state.get("cohort_sha256"):
        raise InputError("FROZEN_COHORT_CHANGED")
    cohort = read(cohort_path, sources)
    refs = historical_panel.references()
    base.update(status="ready", profiles=cohort["selected"], sectors=cohort["sectors"], references=refs)
    for profile in cohort["selected"]:
        if profile["symbol_partition"] == "unseen_symbol_final_only":
            continue
        if profile["symbol_partition"] != "development_and_validation":
            raise InputError("UNKNOWN_SYMBOL_PARTITION")
        ready = state.get("stock_results", {}).get(profile["symbol"], {}).get("status") == "ready"
        if not ready or profile["symbol"] not in state.get("evaluated", []):
            base["cases"].extend(unavailable(profile, ref, "STOCK_INPUT_UNRESOLVED_OR_EVALUATION_MISSING") for ref in refs)
            continue
        evaluation = inside(folder / "evaluation" / disk_name(profile["symbol"]), folder)
        manifest = read(evaluation / "manifest.json", sources)
        price_path = inside(folder / "prices" / disk_name(profile["symbol"]) / "prices.json", folder)
        if manifest["price_sha256"] != digest(price_path) or manifest["profile_sha256"] != stable_hash(profile) or manifest["pilot"] is not False or manifest["phases"] != ["development", "validation"]:
            raise InputError("EVALUATION_INPUT_MANIFEST_CHANGED")
        if manifest["engines"] != {name: job["code_sha256"][name] for name in historical_panel.ENGINE_FILES}:
            raise InputError("EVALUATION_ENGINE_MANIFEST_CHANGED")
        sources.append({"path": str(price_path), "sha256": manifest["price_sha256"]})
        base["cases"].extend(cases_from_folder(evaluation, profile, refs, False, sources))
    return base


def load_pilot(folder):
    folder = inside(folder, ROOT / "reports/private/historical_panel_pilot")
    sources = []
    record = read(folder / "price_entitlement.json", sources)
    require_entitlement(record, price_uses("local_research"), now())
    profile = read(folder / "prices/profile.json", sources)
    manifest = read(folder / "evaluation/manifest.json", sources)
    price_path = folder / "prices/prices.json"
    if manifest["price_sha256"] != digest(price_path) or manifest["profile_sha256"] != stable_hash(profile) or manifest["pilot"] is not True or manifest["engines"] != historical_panel.engine_hashes():
        raise InputError("PILOT_INPUT_MANIFEST_CHANGED")
    if manifest["phases"] != ["development", "validation"]:
        raise InputError("PILOT_FINAL_RESULTS_NOT_PERMITTED")
    sources.append({"path": str(price_path.resolve()), "sha256": manifest["price_sha256"]})
    profile = {**profile, "symbol_partition": "development_and_validation"}
    refs = historical_panel.references()
    return {"status": "ready", "source_kind": "pilot", "is_illustrative": False, "source_progress": {"status": "engineering_pilot"},
        "sources": sources, "source_ids": ["engineering-pilot-" + digest(folder / "evaluation/manifest.json")[:20]],
        "profiles": [profile], "sectors": [{"sector_id": profile["sector_id"], "name_ko": profile["sector_name_ko"], "selected_unique_issuers": 1}],
        "references": refs, "cases": cases_from_folder(folder / "evaluation", profile, refs, False, sources)}


def analyze(dataset, settings=None):
    settings = settings or policy()
    if type(dataset["is_illustrative"]) is not bool or dataset["source_kind"] not in {"panel", "pilot", "synthetic"} or dataset["is_illustrative"] != (dataset["source_kind"] == "synthetic"):
        raise InputError("REAL_AND_ILLUSTRATIVE_DATA_MIXED")
    result = {"schema_version": "1.0.0", "status": dataset["status"], "as_of": now(), "methodology_id": settings["id"],
        "source_kind": dataset["source_kind"], "is_illustrative": dataset["is_illustrative"],
        "label": LABEL if dataset["is_illustrative"] else "PERSONAL LOCAL RESEARCH — NOT MODEL VALIDATION",
        "source_ids": dataset["source_ids"], "source_progress": dataset["source_progress"], "groups": [],
        "reference_periods": {},
        "case_slots": len(dataset["cases"]), "final_evaluation_locked": True, "final_cases_read": 0,
        "heldout_symbols_read": 0, "model_training_performed": False, "network_requests": 0, "external_llm_calls": 0,
        "released_score": None, "performance_validated": False, "calibrated_win_probability": None,
        "estimand": settings["estimand"], "assumptions": settings["assumptions"],
        "limitations": ["Exploratory pointwise percentile intervals; no multiplicity-adjusted discovery claim",
            "Repeated resamples are computations, not new independent market observations",
            "Nominal block count is not an estimated independent sample count",
            "Conditional on fixed cohort and matched data; missing results remain in coverage denominator",
            "No portfolio compounding, dividend, cash-interest or market-impact model",
            "Peak-day timing and conditional target-first timing measure different events"]}
    if dataset["status"] != "ready":
        return result
    for phase in settings["permitted_phases"]:
        phase_refs = [row for row in dataset["references"] if row["phase"] == phase]
        if phase_refs:
            result["reference_periods"][phase] = {"first_reference": min(row["reference_session"] for row in phase_refs),
                "last_reference": max(row["reference_session"] for row in phase_refs),
                "last_label_session": max(row["label_sessions"][-1] for row in phase_refs)}
    profiles = {row["symbol"]: row for row in dataset["profiles"]}
    if len(profiles) != len(dataset["profiles"]) or len({row["cik"] for row in profiles.values()}) != len(profiles):
        raise InputError("DUPLICATE_ISSUER_OR_SYMBOL")
    sectors = {row["sector_id"]: row for row in dataset["sectors"]}
    if len(sectors) != len(dataset["sectors"]) or any(row["sector_id"] not in sectors or row["symbol_partition"] not in {"development_and_validation", "unseen_symbol_final_only"} for row in profiles.values()):
        raise InputError("SECTOR_OR_SYMBOL_PARTITION_MISMATCH")
    if any(sector["selected_unique_issuers"] != sum(row["sector_id"] == sector["sector_id"] for row in profiles.values()) for sector in sectors.values()):
        raise InputError("SECTOR_POPULATION_COUNT_MISMATCH")
    refs = {(row["phase"], row["reference_session"]): row for row in dataset["references"]}
    if len(refs) != len(dataset["references"]) or any(phase not in settings["permitted_phases"] for phase, _ in refs):
        raise InputError("FINAL_OR_DUPLICATE_REFERENCE_NOT_PERMITTED")
    if len({day for _, day in refs}) != len(refs):
        raise InputError("DATE_SHARED_ACROSS_PHASES")
    for row in refs.values():
        labels = row["label_sessions"]
        if len(labels) != 5 or labels != sorted(set(labels)) or labels[0] <= row["reference_session"]:
            raise InputError("INVALID_FIVE_SESSION_LABEL_WINDOW")
    development = [row for row in refs.values() if row["phase"] == "development"]
    validation = [row for row in refs.values() if row["phase"] == "validation"]
    if development and validation and max(row["label_sessions"][-1] for row in development) >= min(row["reference_session"] for row in validation):
        raise InputError("LABEL_WINDOW_CROSSES_PHASE_BOUNDARY")
    if any([row["reference_session"] for row in dataset["references"] if row["phase"] == phase] != sorted(row["reference_session"] for row in dataset["references"] if row["phase"] == phase) for phase in settings["permitted_phases"]):
        raise InputError("REFERENCES_NOT_CHRONOLOGICAL")
    keys = set()
    for row in dataset["cases"]:
        key = (row["symbol"], row["phase"], row["reference_session"])
        if key in keys or key[0] not in profiles or profiles[key[0]]["symbol_partition"] != "development_and_validation" or (key[1], key[2]) not in refs:
            raise InputError("DUPLICATE_UNSEEN_OR_FINAL_CASE")
        if row["cik"] != profiles[key[0]]["cik"] or row["sector_id"] != profiles[key[0]]["sector_id"]:
            raise InputError("CASE_ISSUER_OR_SECTOR_MISMATCH")
        keys.add(key)
    expected = {(profile["symbol"], phase, day) for profile in profiles.values() if profile["symbol_partition"] == "development_and_validation" for phase, day in refs}
    if keys != expected:
        raise InputError("EXPECTED_DENOMINATOR_INCOMPLETE")
    grouped = defaultdict(list)
    for row in dataset["cases"]:
        grouped[row["sector_id"], row["phase"]].append(row)
    for sector in dataset["sectors"]:
        for phase in settings["permitted_phases"]:
            result["groups"].append(stats.group_diagnostics(grouped[sector["sector_id"], phase], dataset["references"], phase,
                sector, settings, pilot=dataset["source_kind"] == "pilot"))
    result["status"] = "illustrative_diagnostics" if dataset["is_illustrative"] else "pilot_descriptive_only" if dataset["source_kind"] == "pilot" else "retrospective_diagnostics_not_validation"
    return result


def save(dataset):
    from .panel_diagnostic_report import markdown
    settings = policy()
    result = analyze(dataset, settings)
    schema = read(ROOT / "schemas/panel_diagnostics.schema.json")
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(result)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    folder = ROOT / "reports/private/panel_diagnostics" / ("illustrative" if dataset["is_illustrative"] else "real") / stamp
    provenance = {"captured_at": result["as_of"], "source_ids": dataset["source_ids"], "source_files": dataset["sources"],
        "policy": settings, "code_sha256": {name: digest(ROOT / name) for name in CORE},
        "compact_input_sha256": stable_hash({key: value for key, value in dataset.items() if key != "sources"}),
        "numeric_context_sent_to_llm": False, "source_files_modified": False}
    atomic_json(folder / "inputs.json", provenance)
    if dataset["is_illustrative"]:
        atomic_json(folder / "illustrative_input.json", dataset)
    atomic_json(folder / "diagnostics.json", result)
    (folder / "REPORT.ko.md").write_text(markdown(result, settings), encoding="utf-8")
    return result, folder


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--job", type=Path)
    source.add_argument("--pilot", type=Path)
    source.add_argument("--demo", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.demo:
            from .panel_diagnostics_demo import dataset as demo
            dataset = demo()
        else:
            dataset = load_job(args.job) if args.job else load_pilot(args.pilot)
        result, folder = save(dataset)
    except RightsError:
        print(json.dumps({"status": "withheld", "reason_code": "LOCAL_ANALYTICS_RIGHTS_NOT_CLEARED", "network_requests": 0}))
        return 1
    except (InputError, ValueError, OSError, KeyError, TypeError, ArithmeticError, jsonschema.ValidationError):
        print(json.dumps({"status": "withheld", "reason_code": "DIAGNOSTIC_INPUT_OR_INTEGRITY_CHECK_FAILED", "network_requests": 0}))
        return 1
    print(json.dumps({"status": result["status"], "is_illustrative": result["is_illustrative"], "case_slots": result["case_slots"],
        "groups": len(result["groups"]), "computed_exploratory_intervals": sum(block["status"] == "computed_exploratory" for group in result["groups"] for cost in group["uncertainty"] for block in cost["blocks"]),
        "final_cases_read": 0, "heldout_symbols_read": 0, "model_training_performed": False, "network_requests": 0,
        "external_llm_calls": 0, "output_dir": str(folder.resolve())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
