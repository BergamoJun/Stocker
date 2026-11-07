"""Bounded approved personal ETF snapshots; exact shared price normalization."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from . import fetch_prices
from .learned_research import latest_training_snapshot
from .rights import price_uses, require_entitlement
from .sec_report import ROOT
from .swing_models import load_price_snapshot
from .validation import digest, now, save_new


def policy():
    value = json.loads((ROOT / "config/context_policy.json").read_text(encoding="utf-8"))
    if value["release_enabled"] is not False or value["monthly_data_budget_usd"] != 0:
        raise ValueError("Context must remain zero-budget private research")
    if {row["symbol"] for row in value["proxies"]} != {"SPY", "PBW"} or any(row["leveraged_or_inverse"] for row in value["proxies"]):
        raise ValueError("Only reviewed unleveraged reference ETFs are supported")
    return value


def latest(symbol):
    found = sorted((ROOT / "reports/private/context_prices").glob(f"*/{symbol}/prices.json"), key=lambda path: path.parent.parent.name)
    if not found:
        raise ValueError("No saved approved context price snapshot")
    return found[-1], found[-1].with_name("price_entitlement.json")


def verify(path, record):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    profile = next((item for item in policy()["proxies"] if item["symbol"] == data.get("symbol")), None)
    if not profile or data.get("instrument_type") != "etf" or data.get("listing_exchange") != profile["listing_exchange"]:
        raise ValueError("Unknown context identity or instrument class")
    raw = Path(path).with_name("raw_response.json")
    if not raw.exists() or digest(raw) != data["raw_sha256"]:
        raise ValueError("Context raw snapshot hash differs")
    dates = data["source_url"].split("/day/")[-1].split("?")[0].split("/")
    if len(dates) != 2 or data["source_url"] != fetch_prices.request_url(*dates, symbol=data["symbol"]):
        raise ValueError("Unrecognized context source endpoint")
    _, _, calendar = fetch_prices.basic_window(data["retrieved_at"])
    rebuilt = fetch_prices.normalize(raw.read_bytes(), record, data["retrieved_at"], *dates, calendar, symbol=data["symbol"])
    if rebuilt != data:
        raise ValueError("Context normalization differs from captured raw data")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fetch two approved personal ETF daily histories; no ML, purchases or public output")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports/private/context_prices")
    args = parser.parse_args(argv)
    if not args.output_dir.resolve().is_relative_to(ROOT / "reports/private"):
        parser.error("Private output required")
    settings, at = policy(), now()
    _, entitlement_path = latest_training_snapshot()
    record = json.loads(entitlement_path.read_text(encoding="utf-8"))
    require_entitlement(record, price_uses("local_research"), at, synthetic=False)
    if record["provider"] != "massive" or record["plan"] != "stocks_basic":
        raise ValueError("Reviewed personal Basic source required")
    key = os.environ.get("MASSIVE_API_KEY", "")
    if not key.strip():
        raise ValueError("MASSIVE_API_KEY is absent from this process")
    destination = args.output_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    start, end, calendar = fetch_prices.basic_window(at)
    results = []
    for index, profile in enumerate(settings["proxies"]):
        if index:
            time.sleep(15)
        row = {"symbol": profile["symbol"], "status": "unavailable"}
        try:
            raw = fetch_prices.download(start, end, key, symbol=profile["symbol"])
            retrieved = now()
            data = fetch_prices.normalize(raw, record, retrieved, start, end, calendar, symbol=profile["symbol"])
            target = destination / profile["symbol"]
            target.mkdir(parents=True, exist_ok=False)
            (target / "raw_response.json").write_bytes(raw)
            save_new(target / "prices.json", data)
            save_new(target / "price_entitlement.json", record)
            load_price_snapshot(target / "prices.json", record, retrieved)
            row.update(status="downloaded_current_research_only", bars=len(data["bars"]), latest_session=data["bars"][-1]["session"], output_dir=str(target.resolve()))
        except (ValueError, OSError) as error:
            row["reason"] = str(error)
        results.append(row)
    save_new(destination / "context_fetch.json", {"as_of": at, "scope": "personal_local", "results": results,
        "monthly_data_budget_usd": 0, "ml_fit_performed": False, "llm_tokens": 0})
    print(json.dumps({"results": results, "output_dir": str(destination.resolve()), "llm_tokens": 0}))
    return 0 if all(row["status"] == "downloaded_current_research_only" for row in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
