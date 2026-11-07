"""Source-backed SIC strata and a cohort frozen before any price outcome."""

from collections import defaultdict
import hashlib
import io
import json
from pathlib import Path
import re
from urllib.request import Request, build_opener
import zipfile

from .fetch_prices import NoRedirect
from .local_files import atomic_json
from .panel_transport import detail_url, symbol, validate_url
from .sec_report import ROOT
from .validation import digest, now, stable_hash

NAMES = {1: "비내구 소비재", 2: "내구 소비재", 3: "제조·기계", 4: "에너지", 5: "화학", 6: "업무장비·기술", 7: "통신", 8: "유틸리티", 9: "유통·일부 서비스", 10: "헬스케어", 11: "금융", 12: "기타 산업"}


def policy():
    value = json.loads((ROOT / "config/historical_panel_policy.json").read_text(encoding="utf-8"))
    if value["monthly_data_budget_usd"] != 0 or value["release_enabled"] is not False or value["model_training_enabled"] is not False or value["final_evaluation_locked"] is not True:
        raise ValueError("Panel must remain private fixed-formula research with the final period locked")
    return value


def parse_definitions(text):
    groups, current = {}, None
    for line in text.splitlines():
        if not line.strip():
            continue
        heading = re.fullmatch(r"\s*(\d{1,2})\s+([A-Za-z]+)\s+(.*)", line)
        interval = re.fullmatch(r"\s*(\d{4})-(\d{4})\s*", line)
        if heading:
            current = int(heading[1])
            if current in groups or current not in NAMES:
                raise ValueError("Unexpected industry definition")
            groups[current] = {"id": str(current), "short_name": heading[2], "name_ko": NAMES[current], "definition": heading[3], "ranges": []}
        elif interval and current:
            low, high = map(int, interval.groups())
            if not 0 <= low <= high <= 9999:
                raise ValueError("Invalid SIC interval")
            groups[current]["ranges"].append([low, high])
        else:
            raise ValueError("Unexpected definition line")
    if set(groups) != set(NAMES):
        raise ValueError("All twelve source industry groups are required")
    lookup = {}
    for group in groups.values():
        for low, high in group["ranges"]:
            for code in range(low, high + 1):
                if code in lookup and lookup[code] != group["id"]:
                    raise ValueError("Overlapping source classifications")
                lookup[code] = group["id"]
    return list(groups.values())


def definitions(folder, *, source_path=None):
    folder = Path(folder)
    target = folder / "industry_definitions.json"
    if target.exists():
        result = json.loads(target.read_text(encoding="utf-8"))
        if digest(folder / "Siccodes12.txt") != result["source_text_sha256"]:
            raise ValueError("Definition source changed")
        return result
    settings = policy()
    if source_path:
        raw = Path(source_path).read_bytes()
        zipped = None
    else:
        request = Request(settings["sector_definition_url"], headers={"User-Agent": "Stocker-personal-research/0.4"})
        with build_opener(NoRedirect()).open(request, timeout=20) as reply:
            zipped = reply.read(1_000_001)
        if len(zipped) > 1_000_000:
            raise ValueError("Oversized definition source")
        archive = zipfile.ZipFile(io.BytesIO(zipped))
        member = next(name for name in archive.namelist() if name.lower() == "siccodes12.txt")
        if archive.getinfo(member).file_size > 100_000:
            raise ValueError("Oversized definition text")
        raw = archive.read(member)
    groups = parse_definitions(raw.decode("utf-8-sig"))
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "Siccodes12.txt").write_bytes(raw)
    if zipped:
        (folder / "Siccodes12.zip").write_bytes(zipped)
    result = {"source_url": settings["sector_definition_url"], "retrieved_at": now(), "source_text_sha256": hashlib.sha256(raw).hexdigest(),
        "source_ids": ["fama-french-sic12-" + hashlib.sha256(raw).hexdigest()[:20]],
        "groups": groups, "scope": "SIC_range_stratification_not_the_official_Fama_French_portfolio_return_series"}
    atomic_json(target, result)
    return result


def sector_for(sic, groups):
    if isinstance(sic, bool) or not str(sic).isdigit():
        return None
    code = int(sic)
    matches = {row["id"] for row in groups for low, high in row["ranges"] if low <= code <= high}
    if len(matches) > 1:
        raise ValueError("Ambiguous SIC sector")
    return next(iter(matches)) if matches else None


def catalog_page(value):
    if value.get("status") != "OK" or not isinstance(value.get("results"), list):
        raise ValueError("Invalid historical ticker catalog")
    rows, excluded = [], []
    settings = policy()
    for raw in value["results"]:
        try:
            ticker = symbol(raw["ticker"])
            if raw["market"] != "stocks" or raw["locale"] != "us" or raw["type"] != "CS" or raw["primary_exchange"] not in settings["allowed_listing_exchanges"] or raw["currency_name"].lower() != "usd":
                raise ValueError("Unsupported market, class, exchange or currency")
            cik = str(raw.get("cik", ""))
            if not cik.isdigit() or len(cik) > 10 or not raw.get("share_class_figi"):
                raise ValueError("Stable issuer/security identity missing")
            rows.append({key: raw.get(key) for key in ("ticker", "name", "type", "market", "locale", "primary_exchange", "currency_name", "share_class_figi", "active")} | {"cik": cik.zfill(10)})
        except (ValueError, KeyError, TypeError, AttributeError):
            excluded.append({"ticker": raw.get("ticker"), "reason": "UNSUPPORTED_OR_UNIDENTIFIED_SECURITY"})
    next_url = value.get("next_url")
    if next_url:
        validate_url(next_url)
        from urllib.parse import urlparse, parse_qs
        if urlparse(next_url).path != "/v3/reference/tickers" or set(parse_qs(urlparse(next_url).query)) != {"cursor"}:
            raise ValueError("Unexpected catalog pagination URL")
    return rows, excluded, next_url


def profile(raw, catalog, groups, evidence):
    if evidence["source_url"] != detail_url(catalog["ticker"], policy()["universe_date"]):
        raise ValueError("Historical identity request date differs")
    value = raw.get("results")
    if raw.get("status") != "OK" or not isinstance(value, dict):
        raise ValueError("Invalid historical ticker detail")
    if value.get("ticker") != catalog["ticker"] or value.get("type") != "CS" or value.get("primary_exchange") != catalog["primary_exchange"]:
        raise ValueError("Historical security classification differs")
    if str(value.get("cik", "")).zfill(10) != catalog["cik"] or value.get("share_class_figi") != catalog["share_class_figi"]:
        raise ValueError("Issuer/security identity differs")
    sector = sector_for(value.get("sic_code"), groups)
    if sector is None:
        raise ValueError("SIC is missing or outside the published ranges")
    return {**catalog, "symbol": catalog["ticker"], "instrument_type": "equity", "currency": "USD", "sic_code": str(value["sic_code"]),
        "sector_id": sector, "sector_name_ko": next(row["name_ko"] for row in groups if row["id"] == sector),
        "requested_as_of": policy()["universe_date"], "source_url": evidence["source_url"], "retrieved_at": evidence["retrieved_at"], "raw_sha256": evidence["raw_sha256"],
        "source_ids": ["massive-reference-" + evidence["raw_sha256"][:20]],
        "historical_original_vintage_verified": False, "financial_fields_retained": False}


def verify_last_identity(raw, approved, evidence, last_session):
    value = raw.get("results", {})
    if raw.get("status") != "OK" or value.get("ticker") != approved["symbol"] or value.get("type") != "CS" or str(value.get("cik", "")).zfill(10) != approved["cik"] or value.get("share_class_figi") != approved["share_class_figi"]:
        raise ValueError("LAST_SESSION_ISSUER_OR_SHARE_CLASS_CHANGED")
    if evidence["source_url"] != detail_url(approved["symbol"], last_session):
        raise ValueError("Last-session identity request date differs")
    return {"symbol": approved["symbol"], "cik": approved["cik"], "share_class_figi": approved["share_class_figi"],
        "requested_as_of": last_session, "source_url": evidence["source_url"], "retrieved_at": evidence["retrieved_at"],
        "raw_sha256": evidence["raw_sha256"], "historical_original_vintage_verified": False, "financial_fields_retained": False}


def freeze(profiles, groups, *, discovery_complete):
    if not discovery_complete:
        raise ValueError("Do not freeze a supposedly complete cohort before catalog classification finishes")
    settings = policy()
    issuers = defaultdict(list)
    already_seen_issuers = {row["cik"] for row in profiles if row["symbol"] in settings["pilot_symbols_not_representative"]}
    for row in profiles:
        issuers[row["cik"]].append(row)
    by_sector = defaultdict(list)
    conflicting = []
    rank = lambda row: (stable_hash([settings["selection_seed"], row["cik"], row["share_class_figi"], row["symbol"]]), row["symbol"])
    for cik, rows in issuers.items():
        if len({row["sector_id"] for row in rows}) != 1:
            conflicting.append(cik)
            continue
        chosen = min(rows, key=rank)
        by_sector[chosen["sector_id"]].append(chosen)
    sectors, selected = [], []
    for group in groups:
        available = sorted(by_sector[group["id"]], key=rank)
        rows = available[:settings["selected_issuers_per_sector"]]
        unseen = sorted((row for row in rows if row["cik"] not in already_seen_issuers), key=lambda row: stable_hash(["holdout", rank(row)]))
        holdout = {row["symbol"] for row in unseen[:len(rows) // 5]}
        for row in rows:
            selected.append({**row, "symbol_partition": "unseen_symbol_final_only" if row["symbol"] in holdout else "development_and_validation"})
        enough = len(rows) >= settings["minimum_unique_issuers_per_sector"]
        sectors.append({"sector_id": group["id"], "name_ko": group["name_ko"], "available_unique_issuers": len(available), "selected_unique_issuers": len(rows),
            "heldout_symbols": len(holdout), "minimum_met": enough, "status": "size_requirement_met_not_performance_validated" if enough else "insufficient_sector_population"})
    return {"version": "1.0.0", "status": "frozen_before_price_results", "created_at": now(), "universe_as_of": settings["universe_date"],
        "sector_scheme": settings["sector_scheme"], "selected": selected, "sectors": sectors, "conflicting_issuer_ids": conflicting,
        "all_sector_size_requirements_met": all(row["minimum_met"] for row in sectors), "historical_original_vintage_verified": False,
        "released_score": None, "model_training_performed": False}
