"""Personal free-plan cache and shared throttling for the historical panel."""

from datetime import timedelta
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, build_opener

from .fetch_prices import NoRedirect
from .local_files import LockBusy, atomic_json, exclusive_lock
from .news_transport import retry_time
from .rights import as_utc, price_uses, require_entitlement
from .sec_report import ROOT
from .validation import now


class Deferred(RuntimeError):
    def __init__(self, due_at):
        self.due_at = due_at
        super().__init__("Provider or shared budget deferred")


class SourceUnavailable(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def symbol(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", value):
        raise ValueError("Unsupported ticker syntax")
    return value


def disk_name(ticker):
    # CON/PRN/AUX and similar valid tickers are reserved Windows device names.
    return "ticker-" + symbol(ticker)


def validate_url(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "api.massive.com" or parsed.username or parsed.password:
        raise ValueError("Only the reviewed Massive host is supported")
    if any(ord(c) < 33 for c in url) or any(key.lower() in {"apikey", "api_key", "token"} for key in parse_qs(parsed.query)):
        raise ValueError("Credential-bearing URL rejected")
    if parsed.path != "/v3/reference/tickers" and not re.fullmatch(r"/v3/reference/tickers/[A-Z][A-Z0-9.-]{0,14}", parsed.path) and not re.fullmatch(r"/v2/aggs/ticker/[A-Z][A-Z0-9.-]{0,14}/range/1/day/\d{4}-\d{2}-\d{2}/\d{4}-\d{2}-\d{2}", parsed.path):
        raise ValueError("Unsupported source endpoint")
    return url


def catalog_url(date):
    return "https://api.massive.com/v3/reference/tickers?" + urlencode({"market": "stocks", "type": "CS", "locale": "us", "date": date, "active": "true", "sort": "ticker", "order": "asc", "limit": 1000})


def detail_url(ticker, date):
    return "https://api.massive.com/v3/reference/tickers/" + symbol(ticker) + "?" + urlencode({"date": date})


def price_url(ticker, start, end):
    return f"https://api.massive.com/v2/aggs/ticker/{symbol(ticker)}/range/1/day/{start}/{end}?adjusted=true&sort=asc&limit=50000"


class Client:
    def __init__(self, record, key, *, root=None, clock=now, sleep=time.sleep, opener=None, on_attempt=lambda: None):
        self.record, self.key = record, key
        self.root = Path(root) if root else ROOT / "local_data/historical_panel_api"
        self.clock, self.sleep, self.opener, self.on_attempt = clock, sleep, opener or build_opener(NoRedirect()), on_attempt
        self.network_requests = 0

    def get(self, url):
        validate_url(url)
        require_entitlement(self.record, price_uses("local_research"), self.clock(), synthetic=self.record["is_illustrative"])
        cache = self.root / "cache" / hashlib.sha256(url.encode()).hexdigest()
        if cache.with_suffix(".json").exists():
            meta = json.loads(cache.with_suffix(".json").read_text(encoding="utf-8"))
            raw = cache.with_suffix(".raw").read_bytes()
            if meta["source_url"] != url or hashlib.sha256(raw).hexdigest() != meta["raw_sha256"] or meta["is_illustrative"] != self.record["is_illustrative"]:
                raise ValueError("Cached source provenance changed")
            return {**meta, "raw": raw, "cache_hit": True}
        if not self.key or self.key != self.key.strip() or any(ord(c) < 33 or ord(c) > 126 for c in self.key):
            raise SourceUnavailable("LOCAL_KEY_MISSING")
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
                self.on_attempt()
                self.network_requests += 1
                state["requests_attempted"] = state.get("requests_attempted", 0) + 1
                atomic_json(state_path, state, replace=True)
                request = Request(url, headers={"Authorization": "Bearer " + self.key, "Accept": "application/json", "User-Agent": "Stocker-personal-panel/0.4"})
                try:
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
                    atomic_json(state_path, state, replace=True)
                if len(raw) > 2_000_000 or self.key.encode("ascii") in raw:
                    raise SourceUnavailable("OVERSIZED_OR_CREDENTIAL_BEARING_BODY")
                # Validate transport format before retaining the private source bytes.
                try:
                    value = json.loads(raw)
                    if not isinstance(value, dict) or value.get("status") not in {"OK", "DELAYED"}:
                        raise ValueError("Invalid provider envelope")
                except (ValueError, TypeError):
                    raise SourceUnavailable("INVALID_JSON_ENVELOPE") from None
                meta = {"source_url": url, "retrieved_at": self.clock(), "raw_sha256": hashlib.sha256(raw).hexdigest(), "provider": "massive", "is_illustrative": self.record["is_illustrative"]}
                cache.parent.mkdir(parents=True, exist_ok=True)
                temporary = cache.with_suffix(".tmp")
                temporary.write_bytes(raw)
                temporary.replace(cache.with_suffix(".raw"))
                atomic_json(cache.with_suffix(".json"), meta)
                return {**meta, "raw": raw, "cache_hit": False}
        except LockBusy:
            raise Deferred((as_utc(self.clock()) + timedelta(seconds=15)).isoformat()) from None
