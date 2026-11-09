"""ILLUSTRATIVE — NOT LIVE MARKET DATA. Offline integrated report fixture."""

from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import uuid

from . import integrated_inputs as inputs, integrated_report as reports
from . import sec_chain_demo as chain, sec_chain_sources, sec_chain_review, sec_fact_review, sec_refresh, sec_refresh_demo
from . import news_connection_demo, news_archive, news_review_store
from .local_files import atomic_json
from .sec_report import ROOT, SYNTHETIC_LABEL
from .sec_transport import Client
from .swing_models import XnysCalendar


def price_files(folder, symbol="BE", *, end="2026-11-06", shift=0):
    folder = Path(folder)
    for name in ("prices.json", "price_entitlement.json"):
        if (folder / name).exists() and sec_refresh.read(folder / name).get("is_illustrative") is not True:
            raise ValueError("FIXTURE_CANNOT_REPLACE_REAL_INPUT")
    calendar = XnysCalendar("2026-01-01", "2026-12-31")
    sessions = calendar.sessions("2026-01-05", end)[-90:]
    bars, previous = [], 100 + shift
    for i, session in enumerate(sessions):
        close = 100 + shift + i * .04 + 1.5 * math.sin(i * .5)
        bars.append({"session": session, "open": f"{previous:.4f}", "close": f"{close:.4f}",
            "high": f"{max(previous, close) + .8:.4f}", "low": f"{min(previous, close) - .8:.4f}", "volume": 1000 + i,
            "available_at": "2026-11-07T12:00:00Z"})
        previous = close
    record = {"id": "fixture-price-rights", "provider": "fixture", "plan": "fixture", "is_illustrative": True,
        "monthly_cost_usd": 0, "reviewer": SYNTHETIC_LABEL, "reviewed_at": "2026-11-06T12:00:00Z", "review_due_at": "2027-11-01T00:00:00Z",
        "evidence_url": "https://example.com/illustrative-rights", "uses": {"ingest": "allowed", "store": "allowed", "derived_analytics": "allowed", "model_training": "unknown"}}
    snapshot = {"schema_version": "1.0.0", "snapshot_id": "illustrative-" + symbol, "is_illustrative": True, "provider": "fixture", "entitlement_id": record["id"],
        "symbol": symbol, "cik": "0000001001" if symbol == "BE" else None, "currency": "USD", "exchange": "XNYS", "quote_kind": "eod",
        "instrument_type": "equity" if symbol == "BE" else "etf", "listing_exchange": "XNYS" if symbol == "BE" else "ARCX", "leveraged_or_inverse": False,
        "adjustment": "split_adjusted_current_share_basis", "point_in_time_verified": False, "retrieved_at": "2026-11-07T12:00:00Z",
        "source_ids": ["illustrative-" + symbol], "source_url": "https://example.com/illustrative-prices/" + symbol, "bars": bars}
    raw = json.dumps({"label": SYNTHETIC_LABEL, "snapshot": snapshot}, sort_keys=True).encode()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "raw_response.json").write_bytes(raw)
    (folder / "prices.json").write_text(json.dumps({**snapshot, "raw_sha256": hashlib.sha256(raw).hexdigest()}), encoding="utf-8")
    (folder / "price_entitlement.json").write_text(json.dumps(record), encoding="utf-8")
    return folder / "prices.json", folder / "price_entitlement.json"


def shifted(value):
    if isinstance(value, dict):
        return {k: shifted(v) for k, v in value.items()}
    if isinstance(value, list):
        return [shifted(v) for v in value]
    if isinstance(value, str) and value.startswith("2026-11-08T"):
        return (datetime.fromisoformat(value.replace("Z", "+00:00")) + timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    return value


def setup(folder):
    folder = Path(folder)
    routes, watchlist = chain.fixture()
    routes[sec_refresh.REFERENCE]["data"][0][2] = "BE"
    routes[sec_refresh.REFERENCE]["data"][0][1] = "ILLUSTRATIVE BE"
    routes["https://data.sec.gov/submissions/CIK0000001001.json"]["name"] = "ILLUSTRATIVE BE"
    watchlist["entities"] = [{"symbol": "BE"}, {"symbol": "DEMOC"}]
    clock = sec_refresh_demo.Clock()
    client = Client("Stocker fixture analyst@fixture.invalid", root=folder / "http", raw_root=folder / "raw", illustrative=True, opener=sec_refresh_demo.Opener(routes), clock=clock, sleep=clock.advance)
    source_store, review_root = folder / "sec", folder / "reviews"
    sec_refresh.refresh(watchlist, client=client, store=source_store, clock=clock)
    clock.advance(2)
    roots = {"root": folder / "chain_reviews", "packet_root": folder / "sec_packets", "review_root": review_root, "source_store": source_store, "period_root": folder / "period_reviews"}
    for issuer in ("1001", "1003"):
        snapshot = sec_refresh.latest(source_store, issuer)
        for filing in sec_refresh.sec_facts.financial_filings(snapshot["filings"]):
            sheet = sec_fact_review.template(issuer, filing["accession"], cutoff=clock(), source_store=source_store, illustrative=True)
            path = folder / "reviews_in" / (issuer + filing["form"] + ".json")
            atomic_json(path, chain.original_demo.illustrative_manifest(sheet, clock()))
            sec_fact_review.store(path, root=review_root, source_store=source_store, illustrative=True, clock=clock)
    clock.advance(2)
    for issuer in ("1001", "1003"):
        sec_chain_sources.capture(issuer, root=roots["packet_root"], source_store=source_store, client=client, illustrative=True, clock=clock)
    clock.advance(2)
    sheet = sec_chain_review.template("1001", cutoff=clock(), packet_root=roots["packet_root"], review_root=review_root, source_store=source_store, illustrative=True)
    path = folder / "chain_manifest.json"
    atomic_json(path, chain.illustrative_manifest(sheet, clock()))
    sec_chain_review.store(path, root=roots["root"], packet_root=roots["packet_root"], review_root=review_root, source_store=source_store, illustrative=True, clock=clock)
    ctx = shifted(news_connection_demo.context())
    context_path, review_path = folder / "news_context.json", folder / "news_manifest.json"
    atomic_json(context_path, ctx)
    archive_root, news_root, input_root = folder / "archive", folder / "news_reviews", folder / "inputs"
    news_archive.capture(context_path, root=archive_root, clock=lambda: "2026-11-08T13:15:02Z")
    manifest = shifted(news_connection_demo.manifest(news_connection_demo.context()))
    # The metadata hash in the template must describe this shifted context.
    from .validation import stable_hash
    manifest["news_context_sha256"] = stable_hash(ctx["news"])
    atomic_json(review_path, manifest)
    news_review_store.store(context_path, review_path, root=news_root, archive_root=archive_root, illustrative=True, clock=lambda: "2026-11-08T13:15:20Z")
    selected = {role: price_files(folder / "source_prices" / symbol, symbol) for role, symbol in (("stock", "BE"), ("market", "SPY"), ("sector", "PBW"))}
    receipt = inputs.capture("1001", "BE", selected, root=input_root, illustrative=True, clock=lambda: "2026-11-08T13:15:22Z")
    clock.value = datetime.fromisoformat("2026-11-08T13:15:25+00:00")
    return {"folder": folder, "clock": clock, "financial_roots": roots, "input_root": input_root, "news_root": news_root, "archive_root": archive_root,
        "selected": selected, "receipt": receipt, "context_path": context_path, "context": ctx}


def build(case, issuer="1001", cutoff=None):
    return reports.build(issuer, cutoff or case["clock"](), **{k: case[k] for k in ("financial_roots", "input_root", "news_root", "archive_root")}, illustrative=True, clock=case["clock"])


def run(root=None):
    case = setup(root or ROOT / "reports/private/integrated_report_demo" / uuid.uuid4().hex)
    complete = build(case)
    partial = build(case, "1003")
    paths = [str(reports.write(v, case["folder"] / "reports") / "REPORT.ko.md") for v in (complete, partial)]
    golden = {"label": SYNTHETIC_LABEL, "reports": 2, "qoq": complete["coverage"]["qoq_calculated"], "ttm": complete["coverage"]["ttm_calculated"],
        "price_scenario": complete["coverage"]["price_scenario_available"], "proxies": complete["coverage"]["proxies_available"], "news_highlights": complete["coverage"]["news_highlights"],
        "partial_price_scenario": partial["coverage"]["price_scenario_available"], "released_score": complete["released_score"], "network_requests": 0}
    expected = sec_refresh.read(ROOT / "examples/integrated_report_golden.json")
    if golden != expected:
        raise ValueError("INTEGRATED_GOLDEN_CHANGED")
    atomic_json(case["folder"] / "RESULTS.json", {"golden": golden, "report_paths": paths})
    return {"golden": golden, "report_paths": paths}


if __name__ == "__main__":
    print(json.dumps(run()))
