"""ILLUSTRATIVE — NOT LIVE MARKET DATA. Calendar and fiscal-year period chains."""

from copy import deepcopy
import json
from pathlib import Path
import uuid

from . import sec_refresh_demo as transport_demo, sec_review_demo as original_demo
from . import sec_fact_review as original, sec_refresh, sec_chain_sources as sources, sec_chain_review as review, sec_chain_report as reports
from .local_files import atomic_json
from .sec_report import ROOT
from .sec_transport import Client


METRICS = ("revenue", "operating_income", "net_income", "operating_cash_flow", "capital_expenditure")
TAGS = ("us-gaap:Revenues", "us-gaap:OperatingIncomeLoss", "us-gaap:ProfitLoss", "us-gaap:NetCashProvidedByUsedInOperatingActivities", "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment")


def document(issuer, groups):
    contexts, rows, seen = [], [], set()
    for index, (role, start, end, values) in enumerate(groups):
        contexts.append(f'<xbrli:context id="p{index}"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{issuer}</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>{start}</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate></xbrli:period></xbrli:context>')
        for metric, tag, value in zip(METRICS, TAGS, values):
            if value is None or (metric, start, end, value) in seen: continue
            seen.add((metric, start, end, value))
            plain = role in {"annual", "current_income_quarter"} or (role == "current_ytd" and metric in METRICS[3:])
            dom = metric if plain else metric + "_" + role
            rows.append(f'<tr><td>{original_demo.ROW_LABELS[metric]}</td><td><ix:nonFraction id="{dom}" name="{tag}" contextRef="p{index}" unitRef="usd" format="ixt:numdotdecimal">{value}</ix:nonFraction></td></tr>')
    return ('<html><body>ILLUSTRATIVE — NOT LIVE MARKET DATA<ix:header><ix:resources>' + ''.join(contexts) + '<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit></ix:resources></ix:header><table>' + ''.join(rows) + '</table></body></html>').encode()


def fixture():
    routes, watchlist = transport_demo.fixture()
    def add(columns, issuer, suffix, end, name):
        accession = f"0000009999-26-{int(issuer) * 10 + suffix:06d}"
        for k, value in zip(columns, (accession, "10-Q", "2026-08-01" if end.endswith("06-30") else "2026-05-01", end, name, "2026-08-01T20:00:00Z" if end.endswith("06-30") else "2026-05-01T20:00:00Z")):
            columns[k].append(value)
        return f"https://www.sec.gov/Archives/edgar/data/{int(issuer)}/{accession.replace('-', '')}/{name}"
    for issuer in ("0000001001", "0000001002", "0000001003"):
        columns = routes[f"https://data.sec.gov/submissions/CIK{issuer}.json"]["filings"]["recent"]
        annual_url = f"https://www.sec.gov/Archives/edgar/data/{int(issuer)}/{columns['accessionNumber'][0].replace('-', '')}/demo-1.htm"
        current_url = f"https://www.sec.gov/Archives/edgar/data/{int(issuer)}/{columns['accessionNumber'][1].replace('-', '')}/demo-2.htm"
        if issuer == "0000001002":
            columns["reportDate"][0] = "2026-06-30"
            columns["filingDate"][0], columns["acceptanceDateTime"][0] = "2026-08-01", "2026-08-01T20:00:00Z"
            routes[annual_url] = document(issuer, [("annual", "2025-07-01", "2026-06-30", (1200, 120, 60, 100, 40))])
            routes[current_url] = document(issuer, [("current_income_quarter", "2026-07-01", "2026-09-30", (400, 40, 20, None, None)), ("current_ytd", "2026-07-01", "2026-09-30", (400, 40, 20, 40, 20)), ("prior_year_ytd", "2025-07-01", "2025-09-30", (300, 30, 15, 20, 10))])
            url = add(columns, issuer, 4, "2026-03-31", "earlier.htm")
            routes[url] = document(issuer, [("earlier_ytd", "2025-07-01", "2026-03-31", (900, 90, 45, 70, 30))])
        else:
            routes[annual_url] = document(issuer, [("annual", "2025-01-01", "2025-12-31", (1200, 120, 60, 100, 40))])
            routes[current_url] = document(issuer, [("current_income_quarter", "2026-07-01", "2026-09-30", (450, 45, 22, None, None)), ("current_ytd", "2026-01-01", "2026-09-30", (1000, 100, 50, 140, 70)), ("prior_year_ytd", "2025-01-01", "2025-09-30", (800, 80, 40, 60, 30))])
            url = add(columns, issuer, 4, "2026-06-30", "previous.htm")
            routes[url] = document(issuer, [("previous_income_quarter", "2026-04-01", "2026-06-30", (300, 30, 15, None, None)), ("previous_ytd", "2026-01-01", "2026-06-30", (550, 55, 28, 90, 45))])
            url = add(columns, issuer, 5, "2026-03-31", "earlier.htm")
            routes[url] = document(issuer, [("earlier_ytd", "2026-01-01", "2026-03-31", (250, 25, 13, 40, 20))])
        if issuer == "0000001003":
            for k in columns: columns[k] = [v for i, v in enumerate(columns[k]) if i != 0]
    return routes, watchlist


def setup(folder, *, symbols=None):
    folder = Path(folder)
    routes, watchlist = fixture()
    if symbols: watchlist["entities"] = [e for e in watchlist["entities"] if e["symbol"] in symbols]
    clock = transport_demo.Clock()
    client = Client("Stocker fixture analyst@fixture.invalid", root=folder / "http", raw_root=folder / "raw", illustrative=True, opener=transport_demo.Opener(routes), clock=clock, sleep=clock.advance)
    source_store, review_root = folder / "store", folder / "current_reviews"
    sec_refresh.refresh(watchlist, client=client, store=source_store, clock=clock)
    clock.advance(2)
    issuers = [sec_refresh.sec_facts.cik(row[0]) for row in routes[sec_refresh.REFERENCE]["data"] if any(e["symbol"] == row[2] for e in watchlist["entities"])]
    for issuer in issuers:
        snapshot = sec_refresh.latest(source_store, issuer)
        for filing in sec_refresh.sec_facts.financial_filings(snapshot["filings"]):
            sheet = original.template(issuer, filing["accession"], cutoff=clock(), source_store=source_store, illustrative=True)
            path = folder / "current_manifests" / (issuer + filing["form"] + ".json")
            atomic_json(path, original_demo.illustrative_manifest(sheet, clock()))
            original.store(path, root=review_root, source_store=source_store, illustrative=True, clock=clock)
    clock.advance(2)
    roots = {"packet_root": folder / "packets", "review_root": review_root, "source_store": source_store}
    for issuer in issuers: sources.capture(issuer, root=roots["packet_root"], source_store=source_store, client=client, illustrative=True, clock=clock)
    clock.advance(2)
    return {"folder": folder, "clock": clock, "client": client, "routes": routes, "watchlist": watchlist, "issuers": issuers,
        **roots, "root": folder / "chain_reviews", "period_root": folder / "period_reviews", "counter": 0}


def illustrative_manifest(sheet, at):
    if sheet["manifest"]["is_illustrative"] is not True: raise ValueError("FIXTURE_HELPER_CANNOT_REVIEW_REAL_CHAINS")
    value = deepcopy(sheet["manifest"])
    value.update(template_only=False, reviewer_id="ILLUSTRATIVE_CHAIN_REVIEWER", reviewed_at=at)
    for finding in value["findings"]:
        metric = finding["metric"]
        anchor = sheet["current_anchors"][metric]
        if anchor is None: continue
        selected, labels, rejected = {}, {}, {}
        for role, options in sheet["candidates"][metric].items():
            if not options: continue
            anchor_role = "current_ytd" if metric in METRICS[3:] else "current_income_quarter"
            chosen = next((f for f in options if role == anchor_role and f["fact_id"] == anchor["fact"]["fact_id"]), options[0])
            selected[role], labels[role] = chosen["fact_id"], original_demo.ROW_LABELS[metric]
            rejected[role] = [f["fact_id"] for f in options if any(f[k] != chosen[k] for k in ("value", "start", "end"))]
        finding.update(decision="verified", scope=anchor["scope"], roles=selected, row_labels=labels, rejected_fact_ids=rejected,
            compatibility_rationale="ILLUSTRATIVE source statements inspected: identical metric, unit, attribution and fiscal definitions across each selected period; no automatic approval of real data.")
    for assessment in value["amendment_assessments"]:
        assessment.update(decision="selected_components_unchanged", rationale="ILLUSTRATIVE amendment original statement rows and selected components directly checked unchanged")
    return value


def run(root=None):
    case = setup(root or ROOT / "reports/private/sec_chain_demo" / uuid.uuid4().hex)
    before = case["clock"]()
    for issuer in case["issuers"]:
        state = review.state(issuer, case["clock"](), packet_root=case["packet_root"], review_root=case["review_root"], source_store=case["source_store"], illustrative=True)
        if state["status"] != "ready": continue
        sheet = review.template(issuer, cutoff=case["clock"](), packet_root=case["packet_root"], review_root=case["review_root"], source_store=case["source_store"], illustrative=True)
        path = case["folder"] / "chains" / (issuer + ".json")
        atomic_json(path, illustrative_manifest(sheet, case["clock"]()))
        review.store(path, root=case["root"], packet_root=case["packet_root"], review_root=case["review_root"], source_store=case["source_store"], illustrative=True, clock=case["clock"])
    case["clock"].advance(2)
    outputs, qoq, ttm = [], 0, 0
    for issuer in case["issuers"]:
        value = reports.build(issuer, case["clock"](), root=case["root"], packet_root=case["packet_root"], review_root=case["review_root"], period_root=case["period_root"], source_store=case["source_store"], illustrative=True, clock=case["clock"])
        qoq += value["chain"]["coverage"]["qoq_calculated"]
        ttm += value["chain"]["coverage"]["ttm_calculated"]
        outputs.append(str(reports.write(value, case["folder"] / "reports") / "REPORT.ko.md"))
    old = reports.build("1001", before, root=case["root"], packet_root=case["packet_root"], review_root=case["review_root"], period_root=case["period_root"], source_store=case["source_store"], illustrative=True, clock=case["clock"])
    result = {"label": "ILLUSTRATIVE — NOT LIVE MARKET DATA", "issuers": 3, "qoq_calculated": qoq, "ttm_calculated": ttm, "qoq_before_reviews": old["chain"]["coverage"]["qoq_calculated"], "price_plan": None, "attractiveness": None, "report_paths": outputs}
    atomic_json(case["folder"] / "SUMMARY.json", result)
    return result


if __name__ == "__main__": print(json.dumps(run(), ensure_ascii=True))
