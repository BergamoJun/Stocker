"""ILLUSTRATIVE — NOT LIVE MARKET DATA. No real-source automatic approval."""

from copy import deepcopy
import json
from pathlib import Path
import re
import uuid

from . import sec_review_demo as original_demo, sec_refresh_demo as transport_demo
from . import sec_fact_review as original, sec_period_review as periods, sec_growth_report as reports, sec_refresh
from .local_files import atomic_json
from .sec_report import ROOT
from .sec_transport import Client


def with_comparatives(raw, form):
    text = raw.decode()
    prior = text.replace("2025-12-31", "2024-12-31").replace("2025-01-01", "2024-01-01") if form == "10-K" else text.replace("2026-09-30", "2025-09-30").replace("2026-07-01", "2025-07-01").replace("2026-01-01", "2025-01-01")
    prior = re.sub(r'(id|contextRef|unitRef)="([^"]+)"', lambda m: m[1] + '="' + m[2] + '_prior"', prior)
    values = {"us-gaap:Revenues": "1000", "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax": "900", "us-gaap:OperatingIncomeLoss": "-50", "us-gaap:ProfitLoss": "-20", "us-gaap:CashAndCashEquivalentsAtCarryingValue": "400", "us-gaap:Assets": "4500", "us-gaap:Liabilities": "3200", "us-gaap:NetCashProvidedByUsedInOperatingActivities": "-100", "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment": "50", "us-gaap:EarningsPerShareDiluted": "-0.1"}
    def value(match):
        tag = re.search(r'name="([^"]+)"', match[1])[1]
        return match[1] + values[tag] + match[3]
    prior = re.sub(r'(<ix:nonFraction[^>]+>)([^<]+)(</ix:nonFraction>)', value, prior)
    resources = prior.split("<ix:resources>", 1)[1].split("</ix:resources>", 1)[0]
    table = "<table>" + prior.split("<table>", 1)[1].split("</table>", 1)[0] + "</table>"
    return text.replace("</ix:resources>", resources + "</ix:resources>").replace("</body>", table + "</body>").encode()


def fixture():
    routes, watchlist = original_demo.fixture()
    for url, value in list(routes.items()):
        if not isinstance(value, bytes) or "/Archives/" not in url or url.endswith("demo-3.htm"): continue
        routes[url] = with_comparatives(value, "10-K" if url.endswith("demo-1.htm") else "10-Q")
    return routes, watchlist


def illustrative_manifest(worksheet, at, *, reviewer_id="ILLUSTRATIVE_PERIOD_REVIEWER", metrics=None):
    if worksheet["manifest"]["is_illustrative"] is not True: raise ValueError("FIXTURE_HELPER_CANNOT_APPROVE_REAL_COMPARISONS")
    value = deepcopy(worksheet["manifest"])
    value.update(template_only=False, reviewer_id=reviewer_id, reviewed_at=at)
    findings = []
    for finding in value["findings"]:
        metric = finding["metric"]
        if metrics is not None and metric not in metrics: continue
        options = worksheet["candidates"][metric]
        chosen = next((f for f in options if f["dom_id"] == metric + "_prior"), None)
        if chosen:
            finding.update(decision="verified", prior_fact_id=chosen["fact_id"], scope=worksheet["current_anchors"][metric]["scope"], row_label=original_demo.ROW_LABELS[metric],
                comparability_rationale="ILLUSTRATIVE same-filing prior-year column, matching period length, tag, unit, issuer and attribution scope directly inspected.",
                rejected_fact_ids=[f["fact_id"] for f in options if any(f[k] != chosen[k] for k in ("value", "start", "end"))])
        findings.append(finding)
    value["findings"] = findings
    return value


def run(root=None):
    folder = Path(root or ROOT / "reports/private/sec_growth_demo" / uuid.uuid4().hex)
    routes, watchlist = fixture()
    clock = transport_demo.Clock()
    client = Client("Stocker fixture analyst@fixture.invalid", root=folder / "http", raw_root=folder / "raw", illustrative=True, opener=transport_demo.Opener(routes), clock=clock, sleep=clock.advance)
    source_store, review_root, period_root = folder / "store", folder / "reviews", folder / "period_reviews"
    sec_refresh.refresh(watchlist, client=client, store=source_store, clock=clock)
    clock.advance(2)
    worksheets = []
    for issuer in ("1001", "1002", "1003"):
        snapshot = sec_refresh.latest(source_store, issuer)
        for filing in snapshot["filings"]:
            if filing["form"] not in {"10-K", "10-Q"}: continue
            sheet = original.template(issuer, filing["accession"], source_store=source_store, illustrative=True, cutoff=clock())
            manifest = original_demo.illustrative_manifest(sheet, clock())
            path = folder / "current" / (issuer + filing["form"] + ".json")
            atomic_json(path, manifest)
            original.store(path, root=review_root, source_store=source_store, illustrative=True, clock=clock)
            worksheets.append((issuer, filing["accession"]))
    clock.advance(2)
    before = clock()
    for issuer, accession in worksheets:
        sheet = periods.template(issuer, accession, cutoff=clock(), review_root=review_root, source_store=source_store, illustrative=True)
        path = folder / "comparisons" / (issuer + accession + ".json")
        atomic_json(path, illustrative_manifest(sheet, clock()))
        periods.store(path, root=period_root, review_root=review_root, source_store=source_store, illustrative=True, clock=clock)
    clock.advance(2)
    outputs, pairs = [], 0
    for issuer in ("1001", "1002", "1003"):
        value = reports.build(issuer, clock(), root=period_root, review_root=review_root, source_store=source_store, illustrative=True, clock=clock)
        pairs += value["growth"]["coverage"]["reviewed_pairs"]
        outputs.append(str(reports.write(value, folder / "reports") / "REPORT.ko.md"))
    old = reports.build("1001", before, root=period_root, review_root=review_root, source_store=source_store, illustrative=True, clock=clock)
    summary = {"label": "ILLUSTRATIVE — NOT LIVE MARKET DATA", "issuers": 3, "reviewed_pairs": pairs,
        "pairs_before_review": old["growth"]["coverage"]["reviewed_pairs"], "price_plan": old["base_report"]["swing"]["price_plan"],
        "attractiveness": old["base_report"]["swing"]["attractiveness"], "report_generation_network_requests": 0, "report_paths": outputs}
    atomic_json(folder / "SUMMARY.json", summary)
    return summary


if __name__ == "__main__": print(json.dumps(run(), ensure_ascii=True))
