"""SEC identity, filing metadata and conservative standard-fact normalization.

These are unreviewed current-vintage source claims, never trading signals.
"""

from datetime import date
from decimal import Decimal, InvalidOperation
import json
import re

from .rights import as_utc
from .sec_report import ROOT
from .sec_transport import endpoint


def loads(raw):
    def reject(value):
        raise ValueError("SEC_NONFINITE_JSON")
    return json.loads(raw, parse_float=Decimal, parse_constant=reject)


def cik(value):
    text = str(value)
    if not re.fullmatch(r"\d{1,10}", text) or not 0 < int(text) < 10 ** 10:
        raise ValueError("INVALID_CIK")
    return text.zfill(10)


def symbol(value):
    text = str(value).strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,19}", text):
        raise ValueError("INVALID_SYMBOL")
    return text


def resolve(watchlist, reference):
    entries = watchlist.get("entities")
    if not isinstance(entries, list) or not entries:
        raise ValueError("EMPTY_SEC_WATCHLIST")
    fields = reference.get("fields", [])
    if not {"cik", "ticker", "name", "exchange"}.issubset(fields):
        raise ValueError("INVALID_SEC_REFERENCE")
    if len(fields) != len(set(fields)):
        raise ValueError("DUPLICATE_SEC_REFERENCE_FIELDS")
    rows = []
    for raw in reference.get("data", []):
        if len(raw) != len(fields):
            raise ValueError("INVALID_SEC_REFERENCE_ROW")
        row = dict(zip(fields, raw))
        row["cik"] = cik(row["cik"])
        row["ticker"] = symbol(row["ticker"])
        rows.append(row)
    entities, unresolved = {}, []
    for item in entries:
        if not isinstance(item, dict) or not (item.get("symbol") or item.get("cik")):
            raise ValueError("INVALID_WATCHLIST_ENTRY")
        ticker = symbol(item["symbol"]) if item.get("symbol") else None
        pinned = cik(item["cik"]) if item.get("cik") else None
        matches = [r for r in rows if ticker and r["ticker"].replace(".", "-") == ticker.replace(".", "-")]
        identities = {r["cik"] for r in matches}
        if ticker and len(identities) != 1:
            unresolved.append({"symbol": ticker, "cik": pinned, "reason": "AMBIGUOUS_SYMBOL" if identities else "UNKNOWN_CURRENT_SYMBOL"})
            continue
        if pinned and ticker and pinned not in identities:
            unresolved.append({"symbol": ticker, "cik": pinned, "reason": "CIK_SYMBOL_MISMATCH"})
            continue
        issuer = pinned or next(iter(identities))
        target = entities.setdefault(issuer, {"cik": issuer, "symbols": [], "reference_name": matches[0]["name"] if matches else None,
            "identity_basis": "pinned_cik" if pinned else "official_current_symbol_reference"})
        if ticker and ticker not in target["symbols"]:
            target["symbols"].append(ticker)
    return list(entities.values()), unresolved


def filings(payload, issuer, tracked):
    if cik(payload.get("cik", "")) != cik(issuer):
        raise ValueError("SEC_SUBMISSIONS_CIK_MISMATCH")
    columns = payload.get("filings", {}).get("recent", {})
    required = ("accessionNumber", "form", "filingDate", "reportDate", "primaryDocument")
    if not all(isinstance(columns.get(k), list) for k in required):
        raise ValueError("SEC_SUBMISSIONS_COLUMNS_MISSING")
    size = len(columns["accessionNumber"])
    if any(not isinstance(v, list) or len(v) != size for v in columns.values()):
        raise ValueError("SEC_SUBMISSIONS_COLUMN_LENGTH")
    result = []
    for i in range(size):
        form = columns["form"][i]
        if form not in tracked:
            continue
        accession = columns["accessionNumber"][i]
        if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", str(accession)):
            raise ValueError("INVALID_SEC_ACCESSION")
        filed, period = columns["filingDate"][i], columns["reportDate"][i] or None
        date.fromisoformat(filed)
        if period: date.fromisoformat(period)
        accepted = columns.get("acceptanceDateTime", [None] * size)[i] or None
        if accepted:
            if not re.search(r"(?:Z|[+-]\d\d:\d\d)$", accepted):
                accepted = None
            else:
                as_utc(accepted)
        doc = columns["primaryDocument"][i]
        url = f"https://www.sec.gov/Archives/edgar/data/{int(issuer)}/{accession.replace('-', '')}/{doc}"
        try:
            endpoint(url)
        except ValueError:
            url = None
        result.append({"accession": accession, "form": form, "filed_date": filed, "report_date": period,
            "accepted_at": accepted, "primary_document": doc, "source_url": url,
            "is_amendment": form.endswith("/A"), "document_status": "not_collected" if url else "unsupported_document_name"})
    if len({r["accession"] for r in result}) != len(result):
        raise ValueError("DUPLICATE_SEC_ACCESSION")
    return sorted(result, key=lambda r: (r["filed_date"], r["accession"]), reverse=True)


def latest_documents(rows):
    chosen = []
    for base in ("10-K", "10-Q", "8-K"):
        match = next((r for r in rows if r["form"] == base), None)
        if match: chosen.append(match["accession"])
    return chosen


def financial_filings(rows):
    selected = []
    for base in ("10-K", "10-Q"):
        match = next((r for r in rows if r["form"] == base), None)
        if match:
            selected.append(match)
            # Amendments are separate source claims, even if their period is absent.
            selected.extend(r for r in rows if r["form"] == base + "/A" and (r["report_date"] == match["report_date"] or not r["report_date"]))
        else:
            amended = next((r for r in rows if r["form"] == base + "/A"), None)
            if amended: selected.append(amended)
    return selected


def normalize(payload, issuer, rows, source_id):
    if cik(payload.get("cik", "")) != cik(issuer):
        raise ValueError("SEC_COMPANYFACTS_CIK_MISMATCH")
    settings = loads((ROOT / "config/sec_fact_policy.json").read_bytes())
    taxonomy = payload.get("facts", {}).get(settings["taxonomy"], {})
    sections = []
    for filing in financial_filings(rows):
        metrics = {}
        for name, spec in settings["metrics"].items():
            candidates = []
            for tag in spec["tags"]:
                for raw in taxonomy.get(tag, {}).get("units", {}).get(spec["unit"], []):
                    if raw.get("accn") != filing["accession"] or raw.get("end") != filing["report_date"] or raw.get("form") != filing["form"]:
                        continue
                    if spec["basis"] == "instant":
                        if raw.get("start"): continue
                        basis = "instant"
                    else:
                        try: days = (date.fromisoformat(raw["end"]) - date.fromisoformat(raw["start"])).days
                        except (KeyError, TypeError, ValueError): continue
                        annual = filing["form"].startswith("10-K")
                        bounds = settings["annual_duration_days"] if annual else settings["quarterly_cashflow_ytd_days"] if spec["basis"] == "cashflow" else settings["quarter_duration_days"]
                        if not bounds[0] <= days <= bounds[1]: continue
                        basis = "annual" if annual else "cumulative_ytd" if spec["basis"] == "cashflow" else "quarter"
                    try:
                        if isinstance(raw["val"], bool): continue
                        value = Decimal(str(raw["val"]))
                        if not value.is_finite(): continue
                    except (KeyError, InvalidOperation): continue
                    candidates.append({"value": format(value, "f"), "tag": tag, "unit": spec["unit"], "basis": basis,
                        "start": raw.get("start"), "end": raw["end"], "accession": raw["accn"], "filed_date": raw.get("filed"),
                        "fiscal_year": raw.get("fy"), "fiscal_period": raw.get("fp"), "frame": raw.get("frame"), "source_ids": [source_id]})
            if candidates and spec["basis"] == "cashflow":
                earliest = min(c["start"] for c in candidates)
                candidates = [c for c in candidates if c["start"] == earliest]
            signatures = {(c["start"], c["end"], Decimal(c["value"])) for c in candidates}
            if len(signatures) == 1:
                metrics[name] = {**candidates[0], "evidence": candidates, "status": "unreviewed_standard_fact"}
            else:
                metrics[name] = {"value": None, "unit": spec["unit"], "source_ids": [source_id],
                    "status": "conflicting_standard_facts" if signatures else "missing_exact_filing_period_unit", "evidence": candidates}
        amendment_pending = filing["is_amendment"] or any(r["form"] == filing["form"] + "/A" and (r["report_date"] == filing["report_date"] or not r["report_date"]) for r in rows)
        derived = {}
        def compatible(a, b):
            return a.get("value") is not None and b.get("value") is not None and all(a.get(k) == b.get(k) for k in ("start", "end", "accession", "unit"))
        for label, numerator in (("operating_margin_percent", "operating_income"), ("net_margin_percent", "net_income")):
            a, b = metrics[numerator], metrics["revenue"]
            good = not amendment_pending and compatible(a, b) and Decimal(b["value"]) > 0
            derived[label] = {"value": format(Decimal(a["value"]) / Decimal(b["value"]) * 100, "f") if good else None,
                "unit": "percent", "status": "calculated_unreviewed" if good else "amendment_review_pending" if amendment_pending else "incompatible_or_missing_inputs", "source_ids": [source_id]}
        a, b = metrics["operating_cash_flow"], metrics["capital_expenditure"]
        good = not amendment_pending and compatible(a, b) and Decimal(b["value"]) >= 0
        derived["free_cash_flow"] = {"value": format(Decimal(a["value"]) - Decimal(b["value"]), "f") if good else None,
            "unit": "USD", "basis": a.get("basis"), "status": "calculated_unreviewed" if good else "amendment_review_pending" if amendment_pending else "incompatible_or_missing_inputs", "source_ids": [source_id]}
        sections.append({"accession": filing["accession"], "form": filing["form"], "report_date": filing["report_date"],
            "amendment_review_pending": amendment_pending, "metrics": metrics, "derived": derived})
    return {"sections": sections, "semantic_review": "not_reviewed", "source_vintage": "current_api_snapshot_not_original_historical_vintage",
        "source_ids": [source_id], "unsupported": ["custom_taxonomy", "non_USD", "IFRS", "automatic_8K_event_interpretation"],
        "score": None, "investment_recommendation": None}
