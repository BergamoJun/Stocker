"""Metadata-only transport sharing existing account pacing and cooldowns."""

from datetime import timedelta
import hashlib
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from . import project_compatibility as guard, news_transport
from .fetch_prices import NoRedirect
from .local_files import atomic_json, exclusive_lock, LockBusy
from .panel_transport import Deferred, SourceUnavailable, symbol
from .rights import as_utc, price_uses, require_entitlement
from .sec_report import ROOT
from .sec_transport import now
from .validation import digest


class Client:
    def __init__(self, record, key, baseline, *, root=None, gdelt_root=None, clock=now, sleep=time.sleep, opener=None):
        self.record, self.key, self.baseline = record, key, Path(baseline)
        self.baseline_sha256 = digest(self.baseline)
        self.root = Path(root or ROOT / "local_data/historical_panel_api")
        self.gdelt_root = gdelt_root
        self.clock, self.sleep, self.opener = clock, sleep, opener or build_opener(NoRedirect())
        self.massive_requests = self.gdelt_requests = 0
        self.check()

    def check(self):
        if self.record["is_illustrative"] is not True and Path(json.loads(self.baseline.read_text(encoding="utf-8"))["root"]).resolve() != ROOT:
            raise ValueError("CURRENT_PROJECT_BASELINE_REQUIRED")
        return guard.check(self.baseline, expected_sha256=self.baseline_sha256)

    def massive(self, ticker, start, end):
        self.check()
        # Preserve the frozen BE observer's direct fetch path and yield while it
        # owns its worker lock instead of adding competing account requests.
        registered = json.loads(self.baseline.read_text(encoding="utf-8"))
        if any("observation_jobs" in Path(path).parts and (Path(path).parent / "worker.lock").exists() for path in registered["jobs"]):
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
                        state["blocked_until"] = news_transport.retry_time(error.headers.get("Retry-After") if error.headers else None, self.clock(), 900)
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

    def gdelt(self, query):
        self.check()
        url = "https://api.gdeltproject.org/api/v2/doc/doc?" + urlencode({"query": query, "mode": "artlist", "format": "json", "timespan": "72h", "maxrecords": 25, "sort": "datedesc"})
        request = Request(url, headers={"User-Agent": "Stocker-personal-metadata/0.5"})
        owner = self
        class Counted:
            def open(self, req, timeout):
                owner.check()
                owner.gdelt_requests += 1
                return owner.opener.open(req, timeout=timeout)
        return news_transport.request_metadata(request, Counted(), clock=self.clock, sleep=self.sleep, state_dir=self.gdelt_root)
