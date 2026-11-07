"""Calendar/capacity planning only; this never claims a real panel was evaluated."""

from datetime import datetime, timezone
from decimal import Decimal
import json

import jsonschema

from .sec_report import ROOT
from .swing_models import XnysCalendar
from .validation import digest, now, save_new


def partitions(settings):
    window = settings["initial_window"]
    calendar = XnysCalendar(window["start"], window["end"])
    days = calendar.sessions(window["start"], window["end"])
    minimum, horizon, stride = window["minimum_history_sessions"], window["label_sessions"], window["reference_stride_sessions"]
    eligible = days[minimum - 1:]
    first = int(Decimal(len(eligible)) * Decimal(settings["partitions"]["development_fraction"]))
    second = int(Decimal(len(eligible)) * (Decimal(settings["partitions"]["development_fraction"]) + Decimal(settings["partitions"]["validation_fraction"])))
    boundary = [eligible[0], eligible[first], eligible[second]]
    names = ["development", "validation", "final_locked"]
    output = []
    for number, name in enumerate(names):
        start = boundary[number]
        stop = boundary[number + 1] if number < 2 else None
        refs, purged, unresolved_tail = [], [], []
        for index in range(minimum - 1, len(days), stride):
            if days[index] < start or (stop and days[index] >= stop):
                continue
            future = days[index + 1:index + 1 + horizon]
            if len(future) != horizon:
                unresolved_tail.append(days[index])
            elif stop and future[-1] >= stop:
                purged.append(days[index])
            else:
                refs.append({"reference_session": days[index], "label_sessions": future})
        output.append({"phase": name, "start": start, "end_exclusive": stop, "eligible_references": len(refs),
            "purged_references": purged, "unresolved_tail_references": unresolved_tail, "references": refs,
            "performance_results_permitted_now": name != "final_locked"})
    return {"calendar_sessions": len(days), "partitions": output}


def capacity(settings, budget):
    names = settings["minimum_symbols_per_sector"] * settings["sector_count_for_capacity_example"]
    rate = budget["massive_basic"]["requests_per_minute"]
    return {"is_capacity_example_not_a_selected_universe": True, "sectors_assumed": settings["sector_count_for_capacity_example"],
        "symbols_assumed": names, "price_requests_if_one_per_symbol": names,
        "api_rate_floor_minutes_excluding_metadata_and_retries": str(Decimal(names) / Decimal(rate)),
        "additional_reference_requests": "not_yet_known", "free_history_years": budget["massive_basic"]["history_years"],
        "external_llm_calls": 0, "llm_tokens": 0}


def main():
    policy_path = ROOT / "config/historical_evaluation_protocol.json"
    settings = json.loads(policy_path.read_text(encoding="utf-8"))
    budget = json.loads((ROOT / "config/service_budget_policy.json").read_text(encoding="utf-8"))
    result = {"status": "planning_only_no_real_panel_run", "as_of": now(), "protocol_id": settings["id"],
        "policy_sha256": digest(policy_path), "calendar_plan": partitions(settings), "capacity": capacity(settings, budget),
        "real_symbols_evaluated": 0, "new_models_fitted": 0, "network_requests": 0, "released_score": None,
        "next_required": ["verified_historical_sector_universe", "separate_generic_stock_history_adapter", "cached_resumable_panel_runner", "unseen_final_period_gate"]}
    schema = json.loads((ROOT / "schemas/historical_preflight.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(result)
    folder = ROOT / "reports/private/historical_evaluation_preparation" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    save_new(folder / "preflight.json", result)
    print(json.dumps({"status": result["status"], "real_symbols_evaluated": 0, "capacity_example": result["capacity"],
        "calendar_case_counts_per_complete_symbol": [{"phase": row["phase"], "eligible_references": row["eligible_references"]} for row in result["calendar_plan"]["partitions"]],
        "output_dir": str(folder.resolve())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
