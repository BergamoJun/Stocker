"""Queued generic current EOD transport; no modification of historical workers."""

from datetime import timedelta
import hashlib
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

from research.local_files import atomic_json
from research.news_transport import retry_time
from research.panel_transport import Deferred, SourceUnavailable, validate_url
from research.rights import as_utc, price_uses, require_entitlement
from research.validation import stable_hash
from .queued_news import Client as QueueClient


class Client(QueueClient):
    def __init__(self, *args, cache_root=None, maximum_requests=16, **kwargs):
        super().__init__(*args, **kwargs)
        if not 1 <= maximum_requests <= 16:
            raise ValueError("BOUNDED_MARKET_REQUESTS_REQUIRED")
        self.cache_root = Path(cache_root or self.root / "market_cache_v1")
        self.network_requests = 0
        self.maximum_requests = maximum_requests

    def get(self, url):
        self.check()
        validate_url(url)
        if url.split("?")[0].endswith("/v3/reference/tickers"):
            raise ValueError("CATALOG_NOT_PART_OF_CURRENT_MARKET_SCOPE")
        require_entitlement(self.record, price_uses("local_research"), self.clock(), synthetic=self.record["is_illustrative"])
        key = hashlib.sha256(url.encode()).hexdigest()
        meta_path, raw_path = self.cache_root / (key + ".json"), self.cache_root / (key + ".raw")
        if meta_path.exists():
            value = json.loads(meta_path.read_text(encoding="utf-8"))
            if value["metadata_sha256"] != stable_hash({k: v for k, v in value.items() if k != "metadata_sha256"}) or value["source_url"] != url or value["is_illustrative"] is not self.record["is_illustrative"]:
                raise ValueError("MARKET_TRANSPORT_CACHE_CHANGED")
            age = (as_utc(self.clock()) - as_utc(value["checked_at"])).total_seconds()
            if 0 <= age < 900:
                if value["status"] != "available":
                    if value.get("retry_at"): raise Deferred(value["retry_at"])
                    raise SourceUnavailable(value["reason_code"])
                raw = raw_path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != value["raw_sha256"]:
                    raise ValueError("MARKET_CACHED_RAW_CHANGED")
                return {**value, "raw": raw, "cache_reused": True}
        if not self.key or self.key != self.key.strip() or any(ord(c) < 33 or ord(c) > 126 for c in self.key):
            raise SourceUnavailable("LOCAL_KEY_MISSING")
        if self.network_requests >= self.maximum_requests:
            raise SourceUnavailable("MARKET_RUN_REQUEST_CAP")
        budget = self.root / "budget.json"
        try:
            with self.queue.slot("current_price"):
                self.check()
                state = json.loads(budget.read_text(encoding="utf-8")) if budget.exists() else {}
                if state.get("blocked_until") and as_utc(self.clock()) < as_utc(state["blocked_until"]):
                    raise Deferred(state["blocked_until"])
                if state.get("last_completed_at"):
                    due = as_utc(state["last_completed_at"]) + timedelta(seconds=15)
                    delay = (due - as_utc(self.clock())).total_seconds()
                    if delay > 15: raise Deferred(due.isoformat())
                    if delay > 0:
                        self.sleep(delay)
                        if as_utc(self.clock()) < due: raise Deferred(due.isoformat())
                self.check()
                require_entitlement(self.record, price_uses("local_research"), self.clock(), synthetic=self.record["is_illustrative"])
                if self.observer_busy(): raise Deferred((as_utc(self.clock()) + timedelta(seconds=15)).isoformat())
                self.network_requests += 1
                state["requests_attempted"] = state.get("requests_attempted", 0) + 1
                atomic_json(budget, state, replace=True)
                try:
                    request = Request(url, headers={"Authorization": "Bearer " + self.key, "Accept": "application/json", "User-Agent": "Stocker-personal-queued-eod/0.1"})
                    with self.opener.open(request, timeout=20) as response:
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
                    atomic_json(budget, state, replace=True)
            if len(raw) > 2_000_000 or self.key.encode("ascii") in raw:
                raise SourceUnavailable("OVERSIZED_OR_CREDENTIAL_BEARING_RESPONSE")
            try:
                parsed = json.loads(raw)
                if not isinstance(parsed, dict) or parsed.get("status") not in {"OK", "DELAYED"}:
                    raise ValueError("bad envelope")
            except (ValueError, TypeError):
                raise SourceUnavailable("INVALID_JSON_ENVELOPE") from None
            value = {"source_url": url, "retrieved_at": self.clock(), "checked_at": self.clock(), "raw_sha256": hashlib.sha256(raw).hexdigest(),
                "is_illustrative": self.record["is_illustrative"], "provider": "massive", "status": "available"}
            self.cache_root.mkdir(parents=True, exist_ok=True)
            temporary = raw_path.with_suffix(".tmp")
            temporary.write_bytes(raw); temporary.replace(raw_path)
        except (Deferred, SourceUnavailable) as error:
            value = {"source_url": url, "checked_at": self.clock(), "is_illustrative": self.record["is_illustrative"], "provider": "massive", "status": "unavailable",
                "reason_code": error.code if isinstance(error, SourceUnavailable) else "REQUEST_DEFERRED", "retry_at": getattr(error, "due_at", None)}
            value["metadata_sha256"] = stable_hash(value)
            atomic_json(meta_path, value, replace=True)
            raise
        value["metadata_sha256"] = stable_hash(value)
        atomic_json(meta_path, value, replace=True)
        return {**value, "raw": raw, "cache_reused": False}
