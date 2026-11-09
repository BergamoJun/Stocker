"""Bounded supplementary SEC documents in a separate point-in-time source store."""

import argparse
import json
from pathlib import Path
import time
import uuid

from . import sec_fact_review as original, sec_refresh, sec_facts
from .local_files import atomic_json, exclusive_lock
from .rights import as_utc
from .sec_report import ROOT
from .sec_transport import Client, Deferred, Unavailable, now, user_agent, endpoint
from .validation import digest


def policy():
    value = sec_refresh.read(ROOT / "config/sec_chain_policy.json")
    if value["monthly_data_budget_usd"] != 0 or value["external_llm_calls"] != 0 or any(value[k] is not False for k in ("model_training_enabled", "release_enabled", "score_enabled", "eps_subtraction_or_sum", "balance_ttm", "cross_cik_merge")):
        raise ValueError("SEC_CHAIN_BOUNDARY_CHANGED")
    return value


def namespace(root=None, illustrative=False):
    path = Path(root or ROOT / "reports/private/sec_chain_sources").resolve()
    if not illustrative and not path.is_relative_to((ROOT / "reports/private").resolve()): raise ValueError("PRIVATE_CHAIN_SOURCE_STORE_REQUIRED")
    return path / ("illustrative" if illustrative else "real")


def plan(issuer, cutoff, *, source_store=None, illustrative=False):
    state = original.state(issuer, cutoff, source_store=source_store, illustrative=illustrative)
    snap = state["snapshot"]
    if snap is None: return {"cik": sec_facts.cik(issuer), "status": "base_source_unavailable", "filings": {}, "amendments": []}
    financial = sorted((f for f in snap["filings"] if f["form"] in {"10-K", "10-Q"} and f["report_date"]), key=lambda f: f["report_date"], reverse=True)
    def missing(code): return {"cik": snap["cik"], "status": code, "filings": {}, "amendments": []}
    if snap["entity_type"] != "operating": return missing("unsupported_entity_type")
    if not financial or financial[0]["form"] != "10-Q": return missing("latest_is_not_interim_quarter")
    current = financial[0]
    older = [f for f in financial if f["report_date"] < current["report_date"]]
    annual = next((f for f in older if f["form"] == "10-K"), None)
    if annual is None: return missing("same_cik_annual_unavailable_cross_cik_merge_disabled")
    previous = older[0] if older else None
    if previous is None: return missing("previous_quarter_source_unavailable")
    earlier = next((f for f in older if f["form"] == "10-Q" and f["report_date"] < previous["report_date"]), None)
    chosen = {"current": current, "annual": annual, "previous": previous}
    if earlier: chosen["earlier"] = earlier
    fields = ("accession", "form", "filed_date", "report_date", "accepted_at", "primary_document", "source_url")
    filings = {role: {k: f[k] for k in fields} for role, f in chosen.items()}
    amendments = [{k: f[k] for k in fields} for f in snap["filings"] if f["is_amendment"] and any(f["form"] == b["form"] + "/A" and (not f["report_date"] or f["report_date"] == b["report_date"]) for b in chosen.values())]
    return {"cik": snap["cik"], "status": "planned", "filings": filings, "amendments": sorted(amendments, key=lambda f: f["accession"])}


def captures(issuer, *, root=None, illustrative=False, cutoff=None):
    folder = namespace(root, illustrative) / sec_facts.cik(issuer)
    values = []
    for receipt_path in (folder / "receipts").glob("*.json"):
        receipt = sec_refresh.read(receipt_path)
        if cutoff is not None and not original.precedes(receipt["available_at"], cutoff): continue
        path = folder / "packets" / (receipt["packet_id"] + ".json")
        if digest(path) != receipt["packet_sha256"]: raise ValueError("CHAIN_SOURCE_PACKET_CHANGED")
        value = sec_refresh.read(path)
        if value["cik"] != sec_facts.cik(issuer) or value["is_illustrative"] is not illustrative: raise ValueError("CHAIN_SOURCE_IDENTITY_CHANGED")
        for source in value["documents"].values():
            if endpoint(source["source_url"]) != "document" or f"/data/{int(value['cik'])}/" not in source["source_url"] or digest(Path(source["raw_path"])) != source["raw_sha256"] or source["is_illustrative"] is not illustrative:
                raise ValueError("CHAIN_RAW_SOURCE_CHANGED")
            if as_utc(source["available_at"]) > as_utc(receipt["available_at"]): raise ValueError("CHAIN_SOURCE_TIME_INVALID")
        values.append({**value, **receipt})
    if len({v["sequence"] for v in values}) != len(values): raise ValueError("CHAIN_CAPTURE_ORDER_CONFLICT")
    return sorted(values, key=lambda v: v["sequence"])


def as_of(issuer, cutoff, *, root=None, illustrative=False):
    values = captures(issuer, root=root, illustrative=illustrative, cutoff=cutoff)
    return values[-1] if values else None


def capture(issuer, *, root=None, source_store=None, client=None, illustrative=False, clock=now):
    settings = policy()
    client = client or Client(user_agent())
    if client.illustrative is not illustrative: raise ValueError("CHAIN_TRANSPORT_NAMESPACE_MISMATCH")
    started, began = clock(), time.monotonic()
    proposal = plan(issuer, started, source_store=source_store, illustrative=illustrative)
    folder = namespace(root, illustrative) / sec_facts.cik(issuer)
    initial, old_callback = client.network_requests, client.on_attempt
    def budget():
        if client.network_requests - initial >= settings["maximum_document_requests_per_issuer"] or time.monotonic() - began >= settings["maximum_issuer_seconds"]:
            raise Unavailable("CHAIN_CAPTURE_INVOCATION_BUDGET")
        old_callback()
    client.on_attempt = budget
    try:
        with exclusive_lock(folder / "write.lock"):
            documents, missing = {}, []
            targets = list(proposal["filings"].values()) + proposal["amendments"]
            for accession, filing in {f["accession"]: f for f in targets}.items():
                if not filing["source_url"]:
                    missing.append({"accession": accession, "reason": "document_url_unavailable"})
                    continue
                try:
                    response = client.get(filing["source_url"])
                    documents[accession] = sec_refresh.source(response)
                except (Unavailable, Deferred) as error:
                    missing.append({"accession": accession, "reason": error.code})
                    if isinstance(error, Deferred): break
            content = {"version": "1.0.0", "cik": sec_facts.cik(issuer), "is_illustrative": illustrative, "plan": proposal,
                "documents": {k: {a: b for a, b in v.items() if a not in {"checked_at", "http_status", "etag", "last_modified"}} for k, v in documents.items()},
                "missing": missing, "policy_sha256": digest(ROOT / "config/sec_chain_policy.json")}
            content_hash = sec_refresh.canonical_hash(content)
            previous = captures(issuer, root=root, illustrative=illustrative)
            if previous and previous[-1]["content_hash"] == content_hash:
                receipt, created = {k: previous[-1][k] for k in ("packet_id", "packet_sha256", "available_at", "sequence", "content_hash")}, False
            else:
                sequence = previous[-1]["sequence"] + 1 if previous else 1
                packet_id = f"{sequence:08d}-" + content_hash[:24]
                path = folder / "packets" / (packet_id + ".json")
                if not path.exists(): atomic_json(path, content)
                elif sec_refresh.read(path) != content: raise ValueError("CHAIN_ORPHAN_PACKET_CONFLICT")
                at = clock()
                if previous and as_utc(at) <= as_utc(previous[-1]["available_at"]): raise ValueError("CHAIN_SOURCE_CLOCK_NOT_ADVANCED")
                receipt = {"packet_id": packet_id, "packet_sha256": digest(path), "available_at": at, "sequence": sequence, "content_hash": content_hash}
                atomic_json(folder / "receipts" / (f"{sequence:08d}-" + uuid.uuid4().hex + ".json"), receipt)
                created = True
            return {"cik": sec_facts.cik(issuer), "status": "captured" if created else "unchanged", "plan_status": proposal["status"],
                **receipt, "document_count": len(documents), "missing_count": len(missing), "network_requests": client.network_requests - initial}
    finally:
        client.on_attempt = old_callback


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ciks", nargs="+", required=True)
    parser.add_argument("--source-store", type=Path)
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    results = [capture(cik, root=args.root, source_store=args.source_store) for cik in args.ciks]
    print(json.dumps({"captures": results}))


if __name__ == "__main__": main()
