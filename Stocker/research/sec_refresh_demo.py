"""ILLUSTRATIVE — NOT LIVE MARKET DATA. No network calls or real issuer values."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
from urllib.error import HTTPError
import uuid

from . import sec_facts
from .sec_report import ROOT
from .sec_transport import Client
from .sec_refresh import REFERENCE, as_of, latest, refresh
from .sec_refresh_report import write_context
from .local_files import atomic_json


LABEL = "ILLUSTRATIVE — NOT LIVE MARKET DATA"


class Clock:
    def __init__(self, value="2026-11-08T13:00:00Z"):
        self.value = datetime.fromisoformat(value.replace("Z", "+00:00"))

    def __call__(self):
        return self.value.isoformat().replace("+00:00", "Z")

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


class Response(io.BytesIO):
    def __init__(self, raw, headers):
        super().__init__(raw)
        self.headers = headers


class Opener:
    """Offline SEC-shaped responses, including real HTTP conditional-cache semantics."""
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def open(self, request, timeout):
        url = request.full_url
        self.calls.append({"url": url, "conditional": bool(request.get_header("If-none-match"))})
        value = self.routes.get(url)
        if isinstance(value, Exception): raise value
        if value is None: raise HTTPError(url, 404, "fixture", {}, None)
        if not isinstance(value, bytes): value = json.dumps(value, sort_keys=True).encode()
        tag = '"' + hashlib.sha256(value).hexdigest() + '"'
        if request.get_header("If-none-match") == tag:
            raise HTTPError(url, 304, "fixture", {"ETag": tag}, None)
        return Response(value, {"ETag": tag})


def fixture():
    reference = {"fields": ["cik", "name", "ticker", "exchange"], "data": []}
    routes = {REFERENCE: reference}
    entities = []
    for number, ticker in ((1001, "DEMOA"), (1002, "DEMOB"), (1003, "DEMOC")):
        issuer = sec_facts.cik(number)
        reference["data"].append([number, "ILLUSTRATIVE " + ticker, ticker, "FIXTURE"])
        entities.append({"symbol": ticker})
        columns = {k: [] for k in ("accessionNumber", "form", "filingDate", "reportDate", "primaryDocument", "acceptanceDateTime")}
        facts = {"cik": number, "facts": {"us-gaap": {}}}
        for counter, form, filed, end, start in ((1, "10-K", "2026-02-15", "2025-12-31", "2025-01-01"), (2, "10-Q", "2026-11-01", "2026-09-30", "2026-07-01"), (3, "8-K", "2026-11-06", "2026-11-06", None)):
            accession = f"0000009999-26-{number * 10 + counter:06d}"
            values = [accession, form, filed, end, f"demo-{counter}.htm", filed + "T20:00:00Z"]
            for k, value in zip(columns, values): columns[k].append(value)
            url = f"https://www.sec.gov/Archives/edgar/data/{number}/{accession.replace('-', '')}/demo-{counter}.htm"
            routes[url] = ("<html>" + LABEL + " " + form + "</html>").encode()
            if form == "8-K": continue
            for tag, value, basis in (("Revenues", 1000 + number, "income"), ("OperatingIncomeLoss", 100, "income"), ("NetIncomeLoss", 50, "income"),
                    ("CashAndCashEquivalentsAtCarryingValue", 500, "instant"), ("Assets", 5000, "instant"), ("Liabilities", 3000, "instant"),
                    ("NetCashProvidedByUsedInOperatingActivities", 200, "cashflow"), ("PaymentsToAcquirePropertyPlantAndEquipment", 75, "cashflow"), ("EarningsPerShareDiluted", 0.125, "income")):
                row = {"accn": accession, "form": form, "filed": filed, "end": end, "val": value, "fy": 2025 if form == "10-K" else 2026, "fp": "FY" if form == "10-K" else "Q3"}
                if basis != "instant": row["start"] = "2026-01-01" if form == "10-Q" and basis == "cashflow" else start
                facts["facts"]["us-gaap"].setdefault(tag, {"units": {}})["units"].setdefault("USD/shares" if tag == "EarningsPerShareDiluted" else "USD", []).append(row)
        routes[f"https://data.sec.gov/submissions/CIK{issuer}.json"] = {"cik": str(number), "name": "ILLUSTRATIVE " + ticker, "entityType": "operating", "sic": "9999", "sicDescription": "ILLUSTRATIVE",
            "filings": {"recent": columns, "files": [{"name": f"CIK{issuer}-submissions-001.json"}]}}
        routes[f"https://data.sec.gov/api/xbrl/companyfacts/CIK{issuer}.json"] = facts
    return routes, {"version": "1.0.0", "label": LABEL, "entities": entities}


def amendment(routes, issuer="0000001001"):
    data = routes[f"https://data.sec.gov/submissions/CIK{issuer}.json"]
    columns = data["filings"]["recent"]
    accession = "0000009999-26-099999"
    for key, value in zip(columns, (accession, "10-Q/A", "2026-11-08", "2026-09-30", "amended.htm", "2026-11-08T13:00:00Z")):
        columns[key].append(value)
    url = f"https://www.sec.gov/Archives/edgar/data/{int(issuer)}/{accession.replace('-', '')}/amended.htm"
    routes[url] = ("<html>" + LABEL + " amendment</html>").encode()
    fact_rows = routes[f"https://data.sec.gov/api/xbrl/companyfacts/CIK{issuer}.json"]["facts"]["us-gaap"]
    for tag in fact_rows.values():
        for unit in tag["units"].values():
            original = next(r for r in unit if r["form"] == "10-Q")
            value = deepcopy(original)
            value.update(form="10-Q/A", accn=accession, filed="2026-11-08")
            unit.append(value)


def run(root=None):
    folder = Path(root or ROOT / "reports/private/sec_refresh_demo" / uuid.uuid4().hex)
    routes, watchlist = fixture()
    clock = Clock()
    opener = Opener(routes)
    client = Client("Stocker fixture analyst@fixture.invalid", root=folder / "http", raw_root=folder / "raw", illustrative=True, opener=opener, clock=clock, sleep=clock.advance)
    store = folder / "store"
    initial = refresh(watchlist, client=client, store=store, clock=clock)
    cutoff = clock()
    repeat = refresh(watchlist, client=client, store=store, clock=clock)
    clock.advance(3601)
    amendment(routes)
    amended = refresh(watchlist, client=client, store=store, clock=clock)
    old = as_of(store, "1001", cutoff)
    current = latest(store, "1001")
    summary = {"label": LABEL, "initial_issuers": len(initial["entities"]), "initial_status": initial["status"],
        "cached_repeat_requests": repeat["network_requests"], "amendment_status": amended["status"],
        "old_asof_has_amendment": any(r["is_amendment"] for r in old["filings"]),
        "current_has_amendment": any(r["is_amendment"] for r in current["filings"]),
        "asof_before_capture": as_of(store, "1001", "2025-01-01T00:00:00Z") is None,
        "semantic_review": current["financials"]["semantic_review"], "released_score": current["financials"]["score"],
        "report_path": str(write_context(current, folder / "contexts"))}
    atomic_json(folder / "SUMMARY.json", summary)
    return summary


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=True))
