"""ILLUSTRATIVE — NOT LIVE MARKET DATA. Synthetic review/report integration."""

from copy import deepcopy
import html
import json
from pathlib import Path
import uuid

from . import sec_refresh as collector, sec_refresh_demo as collection_demo
from . import sec_fact_review as review, sec_financial_report as reports
from .local_files import atomic_json
from .sec_report import ROOT
from .sec_transport import Client


LABEL = "ILLUSTRATIVE — NOT LIVE MARKET DATA"
ROW_LABELS = {"revenue": "Total revenue", "operating_income": "Income from operations", "net_income": "Net income including noncontrolling interests",
    "cash": "Cash and cash equivalents", "assets": "Total assets", "liabilities": "Total liabilities", "operating_cash_flow": "Operating cash flow",
    "capital_expenditure": "Purchase of property plant and equipment", "diluted_eps": "Diluted earnings per share"}


def inline_document(issuer, form):
    end = "2025-12-31" if form == "10-K" else "2026-09-30"
    start = "2025-01-01" if form == "10-K" else "2026-07-01"
    cash_start = "2025-01-01" if form == "10-K" else "2026-01-01"
    contexts = ""
    for key, period in (("income", f"<xbrli:startDate>{start}</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate>"),
            ("cashflow", f"<xbrli:startDate>{cash_start}</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate>"),
            ("instant", f"<xbrli:instant>{end}</xbrli:instant>")):
        contexts += f'<xbrli:context id="{key}"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{issuer}</xbrli:identifier></xbrli:entity><xbrli:period>{period}</xbrli:period></xbrli:context>'
    values = {"revenue": str(1000 + int(issuer)), "operating_income": "100", "net_income": "50", "cash": "500", "assets": "5000", "liabilities": "3000", "operating_cash_flow": "200", "capital_expenditure": "75", "diluted_eps": "0.125"}
    rows = []
    for metric, spec in review.policy()["metrics"].items():
        context = "instant" if spec["basis"] == "instant" else "cashflow" if spec["basis"] == "cashflow" else "income"
        tag = "us-gaap:Revenues" if metric == "revenue" else "us-gaap:ProfitLoss" if metric == "net_income" else spec["tags"][0]
        unit = "eps" if metric == "diluted_eps" else "usd"
        rows.append(f'<tr><td>{ROW_LABELS[metric]}</td><td><ix:nonFraction id="{metric}" name="{tag}" contextRef="{context}" unitRef="{unit}" decimals="3" format="ixt:numdotdecimal">{values[metric]}</ix:nonFraction></td></tr>')
    rows.append(f'<tr><td>Revenue from customer contracts only</td><td><ix:nonFraction id="contract_revenue" name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="income" unitRef="usd">{950 + int(issuer)}</ix:nonFraction></td></tr>')
    return ('<html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"><body>' + LABEL + '<ix:header><ix:resources>' + contexts +
        '<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>' +
        '<xbrli:unit id="eps"><xbrli:divide><xbrli:unitNumerator><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unitNumerator><xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator></xbrli:divide></xbrli:unit>' +
        '</ix:resources></ix:header><table><tr><th>ILLUSTRATIVE consolidated statements</th></tr>' + ''.join(rows) + '</table></body></html>').encode()


def fixture():
    routes, watchlist = collection_demo.fixture()
    for issuer in ("0000001001", "0000001002", "0000001003"):
        payload = routes[f"https://data.sec.gov/submissions/CIK{issuer}.json"]["filings"]["recent"]
        for index, form in enumerate(payload["form"]):
            if form not in {"10-K", "10-Q"}: continue
            accession, name = payload["accessionNumber"][index], payload["primaryDocument"][index]
            url = f"https://www.sec.gov/Archives/edgar/data/{int(issuer)}/{accession.replace('-', '')}/{name}"
            routes[url] = inline_document(issuer, form)
    return routes, watchlist


def illustrative_manifest(worksheet, at, *, reviewer_id="ILLUSTRATIVE_REVIEWER", metrics=None):
    """Fixture-only choices. This helper explicitly refuses real-source worksheets."""
    if worksheet["manifest"]["is_illustrative"] is not True: raise ValueError("FIXTURE_HELPER_CANNOT_REVIEW_REAL_FILINGS")
    value = deepcopy(worksheet["manifest"])
    value.update(template_only=False, reviewer_id=reviewer_id, reviewed_at=at)
    findings = []
    for finding in value["findings"]:
        metric = finding["metric"]
        if metrics is not None and metric not in metrics: continue
        options = worksheet["candidates"][metric]
        selected = next((f for f in options if f["dom_id"] == metric), None)
        if selected:
            finding.update(decision="verified", fact_id=selected["fact_id"], scope="common_diluted" if metric == "diluted_eps" else "consolidated",
                row_label=ROW_LABELS[metric], definition="ILLUSTRATIVE consolidated source definition for " + metric,
                rationale="ILLUSTRATIVE source-row review; total revenue includes the additional fixture revenue component; all other fixture choices match the stated table row.",
                rejected_fact_ids=[f["fact_id"] for f in options if any(f[k] != selected[k] for k in ("value", "start", "end"))])
        findings.append(finding)
    value["findings"] = findings
    return value


def run(root=None):
    folder = Path(root or ROOT / "reports/private/sec_review_demo" / uuid.uuid4().hex)
    routes, watchlist = fixture()
    clock = collection_demo.Clock()
    client = Client("Stocker fixture analyst@fixture.invalid", root=folder / "http", raw_root=folder / "raw", illustrative=True,
        opener=collection_demo.Opener(routes), clock=clock, sleep=clock.advance)
    source_store, review_root = folder / "store", folder / "reviews"
    collector.refresh(watchlist, client=client, store=source_store, clock=clock)
    before = clock()
    clock.advance(2)
    count = 0
    for issuer in ("1001", "1002", "1003"):
        snapshot = collector.latest(source_store, issuer)
        for filing in snapshot["filings"]:
            if filing["form"] not in {"10-K", "10-Q"}: continue
            worksheet = review.template(issuer, filing["accession"], source_store=source_store, illustrative=True, cutoff=clock())
            manifest = illustrative_manifest(worksheet, clock())
            path = folder / "manifests" / (issuer + "-" + filing["form"] + ".json")
            atomic_json(path, manifest)
            review.store(path, root=review_root, source_store=source_store, illustrative=True, clock=clock)
            count += 1
    clock.advance(2)
    outputs, verified = [], 0
    for issuer in ("1001", "1002", "1003"):
        result = reports.build(issuer, clock(), root=review_root, source_store=source_store, illustrative=True, clock=clock)
        verified += result["context"]["review_coverage"]["reviewed_metrics"]
        outputs.append(str(reports.write(result, folder / "reports") / "REPORT.ko.md"))
    old = reports.build("1001", before, root=review_root, source_store=source_store, illustrative=True, clock=clock)
    summary = {"label": LABEL, "issuers": 3, "review_receipts": count, "reviewed_metrics": verified,
        "reviewed_before_receipt": old["context"]["review_coverage"]["reviewed_metrics"], "price_plan": old["swing"]["price_plan"],
        "attractiveness": old["swing"]["attractiveness"], "report_generation_network_requests": 0, "report_paths": outputs}
    atomic_json(folder / "SUMMARY.json", summary)
    return summary


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=True))
