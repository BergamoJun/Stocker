"""Metadata adapter using bounded account-lock waiting instead of hash polling."""

import argparse
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request

from context_adapters.news_refresh_v2 import Client as PreviousClient, lock_busy
from research.local_files import atomic_json
from research.news_transport import retry_time
from research.panel_transport import Deferred, SourceUnavailable, symbol
from research.rights import as_utc, price_uses, require_entitlement
from research.sec_report import ROOT
from research.sec_transport import now
from research.validation import digest
from . import api_queue, scope


class Client(PreviousClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.queue = api_queue.Queue(self.root, clock=self.clock, pause=self.sleep, observer_busy=self.observer_busy)

    def check(self):
        super().check()
        return scope.check(self.baseline, expected_sha256=self.baseline_sha256)

    def observer_busy(self):
        value = json.loads(self.baseline.read_text(encoding="utf-8"))
        return any("observation_jobs" in Path(p).parts and lock_busy(Path(p).parent / "worker.lock") for p in value["jobs"])

    def massive(self, ticker, start, end):
        self.check()
        require_entitlement(self.record, price_uses("local_research"), self.clock(), synthetic=self.record["is_illustrative"])
        if self.record["provider"] != "massive" or self.record["plan"] != "stocks_basic":
            raise ValueError("ONLY_PERSONAL_BASIC_METADATA_ROUTE_SUPPORTED")
        if not self.key or self.key != self.key.strip() or any(ord(c) < 33 or ord(c) > 126 for c in self.key):
            raise SourceUnavailable("LOCAL_KEY_MISSING")
        if not as_utc(start) <= as_utc(end) <= as_utc(self.clock()):
            raise ValueError("INVALID_METADATA_QUERY_WINDOW")
        settings = api_queue.policy()
        if self.massive_requests >= settings["maximum_company_http_requests"]:
            raise SourceUnavailable("RUN_REQUEST_CAP")
        url = "https://api.massive.com/v2/reference/news?" + urlencode({"ticker": symbol(ticker), "published_utc.gte": start,
            "published_utc.lte": end, "order": "desc", "sort": "published_utc", "limit": 25})
        state_path = self.root / "budget.json"
        # No HTTP retries. Only acquisition of the shared local byte lock waits.
        with self.queue.slot("news"):
            self.check()
            state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
            if state.get("blocked_until") and as_utc(self.clock()) < as_utc(state["blocked_until"]):
                raise Deferred(state["blocked_until"])
            if state.get("last_completed_at"):
                due = as_utc(state["last_completed_at"]) + timedelta(seconds=settings["minimum_seconds_after_completion"])
                delay = (due - as_utc(self.clock())).total_seconds()
                if delay > settings["minimum_seconds_after_completion"]:
                    raise Deferred(due.isoformat())
                if delay > 0:
                    self.sleep(delay)
                    if as_utc(self.clock()) < due:
                        raise Deferred(due.isoformat())
            if self.observer_busy():
                raise Deferred((as_utc(self.clock()) + timedelta(seconds=15)).isoformat())
            require_entitlement(self.record, price_uses("local_research"), self.clock(), synthetic=self.record["is_illustrative"])
            self.check()
            if self.observer_busy():
                raise Deferred((as_utc(self.clock()) + timedelta(seconds=15)).isoformat())
            self.massive_requests += 1
            state["requests_attempted"] = state.get("requests_attempted", 0) + 1
            atomic_json(state_path, state, replace=True)
            request = Request(url, headers={"Authorization": "Bearer " + self.key, "Accept": "application/json", "User-Agent": "Stocker-personal-queued-metadata/0.1"})
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
        if len(raw) > 2_000_000 or self.key.encode("ascii") in raw:
            raise SourceUnavailable("OVERSIZED_OR_CREDENTIAL_BEARING_BODY")
        return {"raw": raw, "retrieved_at": self.clock(), "source_url": url, "raw_sha256": hashlib.sha256(raw).hexdigest()}


def run(issuers, baseline, *, client, **kwargs):
    from context_adapters import news_refresh_v2
    proof = scope.check(baseline)
    value, folder = news_refresh_v2.run(issuers, baseline, client=client, **kwargs)
    scope.check(baseline, expected_sha256=proof["baseline_sha256"])
    transport = {"id": "shared-stock-request-queue-v1", "method_hashes": {str(p.relative_to(ROOT)).replace("\\", "/"): digest(p)
        for p in [Path(__file__), api_queue.BASE / "api_queue.py", api_queue.BASE / "api_queue_policy.json"]},
        "queue_audit": dict(client.queue.audit), "maximum_http_requests": 3, "http_retries": 0,
        "legacy_worker_methods_changed": False, "legacy_workers_participate_in_priority_order": False}
    atomic_json(folder / "QUEUED_TRANSPORT.json", transport)
    return {**value, "queued_transport": transport}, folder


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--ciks", nargs="+")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--freeze", type=Path)
    args = parser.parse_args(argv)
    if args.check:
        value = scope.check(args.baseline, replay=args.replay)
        if args.freeze:
            value["frozen"] = scope.freeze(args.baseline, args.freeze)
        print(json.dumps(value)); return
    if not args.ciks:
        parser.error("--ciks required")
    from research.learned_research import latest_training_snapshot
    from research.sec_refresh import read
    _, entitlement = latest_training_snapshot()
    client = Client(read(entitlement), os.environ.get("MASSIVE_API_KEY", ""), args.baseline)
    result, folder = run(args.ciks, args.baseline, client=client)
    print(json.dumps({"output": str(folder), "issuers": [{k: r[k] for k in ("symbol", "company_status", "article_count")} for r in result["issuers"]],
        "requests": {k: result[k] for k in ("massive_requests", "gdelt_requests", "official_requests")}, "queue_audit": result["queued_transport"]["queue_audit"]}))


if __name__ == "__main__":
    main()
