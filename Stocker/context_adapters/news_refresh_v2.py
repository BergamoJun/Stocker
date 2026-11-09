"""Metadata transport v2: distinguish a persistent lock file from an active lock."""

import argparse
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request

from research import project_compatibility as legacy_guard, news_refresh, expanded_report
from research.context_transport import Client as OriginalClient
from research.local_files import atomic_json, exclusive_lock, LockBusy
from research.news_transport import retry_time
from research.panel_transport import Deferred, SourceUnavailable, symbol
from research.rights import as_utc, price_uses, require_entitlement
from research.sec_report import ROOT
from research.sec_transport import now
from research.validation import digest


def check_scope(baseline, *, expected_sha256=None, replay=False):
    proof = legacy_guard.check(baseline, expected_sha256=expected_sha256, replay=replay)
    value = json.loads(Path(baseline).read_text(encoding="utf-8"))
    root = Path(value["root"])
    present = {str(p.relative_to(root)).replace("\\", "/") for p in (root / "context_adapters").rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix not in {".pyc", ".pyo"}}
    allowed = set(value.get("adapter_allow_new", []))
    unexpected = present - set(value["files"]) - allowed
    if unexpected:
        raise ValueError("ADAPTER_SCOPE_CONFLICT: " + json.dumps(sorted(unexpected)))
    if replay:
        for path in value.get("additional_reports", []):
            expanded_report.validate_file(path)
            proof["reports_replayed"] += 1
    return {**proof, "adapter_files": len(present)}


def freeze_scope(baseline, output):
    check_scope(baseline)
    value = json.loads(Path(baseline).read_text(encoding="utf-8"))
    root = Path(value["root"])
    for path in (root / "context_adapters").rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}:
            value["files"][str(path.relative_to(root)).replace("\\", "/")] = digest(path)
    value["adapter_allow_new"] = []
    value["parent_baseline_sha256"] = digest(baseline)
    value["created_at"] = now()
    atomic_json(output, value)
    check_scope(output)
    return Path(output)


def lock_busy(path):
    """Never delete a worker's persistent lock file or take a Windows lock."""
    path = Path(path)
    try:
        with path.open("rb", buffering=0) as handle:
            if os.name == "nt":
                # Windows byte locks deny reads through any other file handle.
                # A successful unbuffered read proves byte zero is unlocked at
                # this instant; the mere existence of the file proves nothing.
                handle.read(1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return False
    except FileNotFoundError:
        return False
    except OSError:
        return True


class Client(OriginalClient):
    def check(self):
        super().check()
        return check_scope(self.baseline, expected_sha256=self.baseline_sha256)

    def massive(self, ticker, start, end):
        self.check()
        registered = json.loads(self.baseline.read_text(encoding="utf-8"))
        if any("observation_jobs" in Path(path).parts and lock_busy(Path(path).parent / "worker.lock") for path in registered["jobs"]):
            raise Deferred((as_utc(self.clock()) + timedelta(seconds=15)).isoformat())
        require_entitlement(self.record, price_uses("local_research"), self.clock(), synthetic=self.record["is_illustrative"])
        if self.record["provider"] != "massive" or self.record["plan"] != "stocks_basic":
            raise ValueError("ONLY_PERSONAL_BASIC_METADATA_ROUTE_SUPPORTED")
        if not self.key or self.key != self.key.strip() or any(ord(c) < 33 or ord(c) > 126 for c in self.key):
            raise SourceUnavailable("LOCAL_KEY_MISSING")
        if not as_utc(start) <= as_utc(end) <= as_utc(self.clock()):
            raise ValueError("INVALID_METADATA_QUERY_WINDOW")
        url = "https://api.massive.com/v2/reference/news?" + urlencode({"ticker": symbol(ticker), "published_utc.gte": start,
            "published_utc.lte": end, "order": "desc", "sort": "published_utc", "limit": 25})
        state_path = self.root / "budget.json"
        try:
            with exclusive_lock(self.root / "request.lock"):
                state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
                if state.get("blocked_until") and as_utc(self.clock()) < as_utc(state["blocked_until"]):
                    raise Deferred(state["blocked_until"])
                if state.get("last_completed_at"):
                    due = as_utc(state["last_completed_at"]) + timedelta(seconds=15)
                    delay = (due - as_utc(self.clock())).total_seconds()
                    if delay > 0:
                        self.sleep(min(delay, 15))
                        if as_utc(self.clock()) < due:
                            raise Deferred(due.isoformat())
                self.check()
                self.massive_requests += 1
                state["requests_attempted"] = state.get("requests_attempted", 0) + 1
                atomic_json(state_path, state, replace=True)
                request = Request(url, headers={"Authorization": "Bearer " + self.key, "Accept": "application/json", "User-Agent": "Stocker-personal-metadata/0.5"})
                try:
                    with self.opener.open(request, timeout=15) as response:
                        raw = response.read(2_000_001)
                except HTTPError as error:
                    if error.code == 429:
                        state["blocked_until"] = retry_time(error.headers.get("Retry-After") if error.headers else None, self.clock(), 900)
                        raise Deferred(state["blocked_until"]) from None
                    raise SourceUnavailable("HTTP_" + str(error.code)) from None
                except OSError:
                    raise SourceUnavailable("TRANSPORT_FAILURE") from None
                finally:
                    state["last_completed_at"] = self.clock()
                    atomic_json(state_path, state, replace=True)
        except LockBusy:
            raise Deferred((as_utc(self.clock()) + timedelta(seconds=15)).isoformat()) from None
        if len(raw) > 2_000_000 or self.key.encode("ascii") in raw:
            raise SourceUnavailable("OVERSIZED_OR_CREDENTIAL_BEARING_BODY")
        return {"raw": raw, "retrieved_at": self.clock(), "source_url": url, "raw_sha256": hashlib.sha256(raw).hexdigest()}


def run(issuers, baseline, *, client, **kwargs):
    proof = check_scope(baseline)
    result, folder = news_refresh.run(issuers, baseline, client=client, cache_root=kwargs.pop("cache_root", ROOT / "reports/private/issuer_news_cache_v2"), **kwargs)
    check_scope(baseline, expected_sha256=proof["baseline_sha256"])
    transport = {"id": "windows-lock-aware-metadata-transport-v2", "source_sha256": digest(Path(__file__)), "baseline_sha256": proof["baseline_sha256"],
        "legacy_normalization_method_hashes": result["method_hashes"], "legacy_reports_modified": False}
    atomic_json(folder / "TRANSPORT.json", transport)
    return {**result, "transport_provenance_path": str(folder / "TRANSPORT.json"), "transport": transport}, folder


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--ciks", nargs="+")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--freeze", type=Path)
    args = parser.parse_args(argv)
    if args.check:
        result = check_scope(args.baseline, replay=args.replay)
        if args.freeze:
            result["frozen_baseline_path"] = str(freeze_scope(args.baseline, args.freeze))
        print(json.dumps(result))
        return 0
    if not args.ciks:
        parser.error("--ciks required")
    from research.learned_research import latest_training_snapshot
    from research.sec_refresh import read
    _, path = latest_training_snapshot()
    client = Client(read(path), os.environ.get("MASSIVE_API_KEY", ""), args.baseline)
    result, folder = run(args.ciks, args.baseline, client=client)
    print(json.dumps({"output_dir": str(folder), "issuers": [{k: r[k] for k in ("cik", "symbol", "article_count", "company_status", "sector_status")} for r in result["issuers"]],
        "massive_requests": result["massive_requests"], "gdelt_requests": result["gdelt_requests"], "official_requests": result["official_requests"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
