"""Bounded SEC-only transport, conditional caching and immutable raw versions."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, build_opener
import uuid

from .fetch_prices import NoRedirect
from .local_files import LockBusy, atomic_json, exclusive_lock
from .news_transport import retry_time
from .rights import as_utc
from .sec_report import ROOT
from .validation import digest


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def policy():
    value = json.loads((ROOT / "config/sec_refresh_policy.json").read_text(encoding="utf-8"))
    if value["monthly_data_budget_usd"] != 0 or value["release_enabled"] is not False or value["external_llm_calls"] != 0 or value["model_training_enabled"] is not False:
        raise ValueError("SEC_LOCAL_RESEARCH_BOUNDARY_CHANGED")
    if value["minimum_request_spacing_seconds"] < 1 or value["request_timeout_seconds"] > 30 or value["maximum_requests_per_day"] > 50000:
        raise ValueError("SEC_TRANSPORT_LIMIT_CHANGED")
    return value


def user_agent():
    value = os.environ.get("SEC_USER_AGENT", "")
    if not value and os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as handle:
                value = winreg.QueryValueEx(handle, "SEC_USER_AGENT")[0]
        except OSError:
            value = ""
    return value if isinstance(value, str) else ""


def valid_user_agent(value, *, illustrative=False):
    if not isinstance(value, str) or not 12 <= len(value) <= 250 or value != value.strip() or any(ord(c) < 32 or ord(c) > 126 for c in value):
        return False
    found = re.search(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})", value)
    return bool(found and (illustrative or found[1].lower() not in {"example.com", "example.org", "example.net", "fixture.invalid", "test.invalid"}))


class Deferred(RuntimeError):
    def __init__(self, due_at, code="SEC_SOURCE_DEFERRED"):
        self.due_at, self.code = due_at, code
        super().__init__(code)


class Unavailable(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def endpoint(url):
    value = urlparse(url)
    if value.scheme != "https" or value.netloc not in {"www.sec.gov", "data.sec.gov"} or value.query or value.fragment or any(ord(c) < 33 for c in url):
        raise ValueError("UNREVIEWED_SEC_URL")
    path = value.path
    if value.netloc == "www.sec.gov" and path == "/files/company_tickers_exchange.json": return "reference"
    if value.netloc == "data.sec.gov" and re.fullmatch(r"/submissions/CIK\d{10}(?:-submissions-\d{3})?\.json", path): return "submissions"
    if value.netloc == "data.sec.gov" and re.fullmatch(r"/api/xbrl/companyfacts/CIK\d{10}\.json", path): return "companyfacts"
    if value.netloc == "www.sec.gov" and re.fullmatch(r"/Archives/edgar/data/[1-9]\d{0,9}/\d{18}/[A-Za-z0-9][A-Za-z0-9._-]{0,180}\.(?:htm|html|txt)", path) and ".." not in path: return "document"
    raise ValueError("UNREVIEWED_SEC_ENDPOINT")


class Client:
    def __init__(self, agent, *, root=None, raw_root=None, illustrative=False, clock=now, sleep=time.sleep, opener=None, on_attempt=lambda: None):
        if illustrative and opener is None:
            raise ValueError("ILLUSTRATIVE_SEC_REQUIRES_OFFLINE_TRANSPORT")
        self.agent, self.illustrative = agent, illustrative
        part = "illustrative" if illustrative else "real"
        self.root = Path(root or ROOT / "local_data/sec_http") / part
        self.raw_root = Path(raw_root or ROOT / "reports/private/sec_raw") / part
        self.clock, self.sleep, self.opener, self.on_attempt = clock, sleep, opener or build_opener(NoRedirect()), on_attempt
        self.network_requests = 0

    def get(self, url, *, refresh=False):
        kind, settings = endpoint(url), policy()
        key = hashlib.sha256(url.encode()).hexdigest()
        cache = self.root / "cache" / (key + ".json")
        previous = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else None
        if previous:
            raw_path = Path(previous["raw_path"]).resolve()
            if not raw_path.is_relative_to(self.raw_root.resolve()) or not raw_path.is_file() or previous["source_url"] != url or previous["is_illustrative"] is not self.illustrative or digest(raw_path) != previous["raw_sha256"]:
                raise Unavailable("SEC_CACHE_INTEGRITY_CHANGED")
            if not refresh and (as_utc(self.clock()) - as_utc(previous["checked_at"])).total_seconds() < settings["refresh_seconds"][kind]:
                return {**previous, "raw": raw_path.read_bytes(), "cache_hit": True, "network_checked": False}
        if not valid_user_agent(self.agent, illustrative=self.illustrative):
            raise Unavailable("SEC_USER_AGENT_REQUIRED")
        self.root.mkdir(parents=True, exist_ok=True)
        state_path = self.root / "request_state.json"
        try:
            with exclusive_lock(self.root / "request.lock"):
                state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
                at = self.clock()
                if state.get("blocked_until") and as_utc(at) < as_utc(state["blocked_until"]):
                    raise Deferred(state["blocked_until"])
                day = as_utc(at).date().isoformat()
                if state.get("day") != day:
                    state.update(day=day, requests_today=0, bytes_today=0)
                if state.get("requests_today", 0) >= settings["maximum_requests_per_day"] or state.get("bytes_today", 0) >= settings["maximum_download_bytes_per_day"]:
                    due = (as_utc(at).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).isoformat()
                    raise Deferred(due, "SEC_DAILY_BUDGET_REACHED")
                if state.get("stored_bytes", 0) >= settings["maximum_store_bytes"]:
                    raise Unavailable("SEC_STORAGE_BUDGET_REACHED")
                if not self.illustrative and shutil.disk_usage(self.root).free < settings["minimum_free_disk_bytes"]:
                    raise Unavailable("SEC_DISK_SPACE_LOW")
                last_request = max((as_utc(state[k]) for k in ("last_completed_at", "last_attempt_at") if state.get(k)), default=None)
                if last_request:
                    due = last_request + timedelta(seconds=settings["minimum_request_spacing_seconds"])
                    delay = (due - as_utc(self.clock())).total_seconds()
                    if delay > 0:
                        self.sleep(min(delay, settings["minimum_request_spacing_seconds"]))
                        if as_utc(self.clock()) < due: raise Deferred(due.isoformat())
                headers = {"User-Agent": self.agent, "Accept-Encoding": "identity", "Accept": "text/html,application/json,text/plain"}
                if previous:
                    if previous.get("etag"): headers["If-None-Match"] = previous["etag"]
                    if previous.get("last_modified"): headers["If-Modified-Since"] = previous["last_modified"]
                self.on_attempt()
                self.network_requests += 1
                state["requests_today"] = state.get("requests_today", 0) + 1
                state["last_attempt_at"] = self.clock()
                atomic_json(state_path, state, replace=True)
                raw, response_headers, status = None, {}, 200
                try:
                    with self.opener.open(Request(url, headers=headers), timeout=settings["request_timeout_seconds"]) as response:
                        response_headers = response.headers
                        limit = min(settings["maximum_bytes"][kind], settings["maximum_download_bytes_per_day"] - state.get("bytes_today", 0))
                        raw = response.read(limit + 1)
                        state["bytes_today"] = state.get("bytes_today", 0) + len(raw)
                        if len(raw) > limit: raise Unavailable("SEC_RESPONSE_SIZE_OR_DAILY_BYTES_LIMIT")
                        if response_headers.get("Content-Encoding", "identity").lower() not in {"", "identity"}:
                            raise Unavailable("SEC_UNEXPECTED_CONTENT_ENCODING")
                except HTTPError as error:
                    if error.code == 304 and previous:
                        status = 304
                    elif error.code in {429, 403, 503}:
                        state["blocked_until"] = retry_time(error.headers.get("Retry-After") if error.headers else None, self.clock(), settings["default_cooldown_seconds"])
                        raise Deferred(state["blocked_until"], "SEC_HTTP_" + str(error.code)) from None
                    else:
                        raise Unavailable("SEC_HTTP_" + str(error.code)) from None
                except OSError:
                    raise Unavailable("SEC_TRANSPORT_FAILURE") from None
                finally:
                    state["last_completed_at"] = self.clock()
                    atomic_json(state_path, state, replace=True)
                checked = self.clock()
                if status == 304:
                    result = {**previous, "checked_at": checked, "http_status": 304}
                    raw = Path(previous["raw_path"]).read_bytes()
                else:
                    email = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", self.agent)[0]
                    if self.agent.encode() in raw or email.encode() in raw:
                        raise Unavailable("SEC_CONTACT_ECHO_REJECTED")
                    if b"Your Request Originates from an Undeclared Automated Tool" in raw or b"Request Rate Threshold Exceeded" in raw:
                        state["blocked_until"] = (as_utc(checked) + timedelta(seconds=settings["default_cooldown_seconds"])).isoformat()
                        atomic_json(state_path, state, replace=True)
                        raise Deferred(state["blocked_until"], "SEC_ACCESS_POLICY_RESPONSE")
                    raw_hash = hashlib.sha256(raw).hexdigest()
                    folder = self.raw_root / key
                    raw_path = folder / (raw_hash + ".raw")
                    meta_path = folder / (raw_hash + ".json")
                    if not raw_path.exists() or not meta_path.exists():
                        if state.get("stored_bytes", 0) + len(raw) > settings["maximum_store_bytes"]:
                            raise Unavailable("SEC_STORAGE_BUDGET_REACHED")
                        folder.mkdir(parents=True, exist_ok=True)
                        temporary = folder / (uuid.uuid4().hex + ".tmp")
                        if not raw_path.exists():
                            with temporary.open("xb") as handle:
                                handle.write(raw)
                                handle.flush()
                                os.fsync(handle.fileno())
                            temporary.replace(raw_path)
                        elif digest(raw_path) != raw_hash:
                            raise Unavailable("SEC_RAW_INTEGRITY_CHANGED")
                        atomic_json(meta_path, {"available_at": self.clock(), "raw_sha256": raw_hash, "source_url": url,
                            "provider": "sec_edgar", "is_illustrative": self.illustrative})
                        state["stored_bytes"] = state.get("stored_bytes", 0) + len(raw)
                    source = json.loads(meta_path.read_text(encoding="utf-8"))
                    result = {**source, "raw_path": str(raw_path.resolve()), "checked_at": checked, "http_status": 200,
                        "etag": response_headers.get("ETag"), "last_modified": response_headers.get("Last-Modified"), "kind": kind}
                atomic_json(state_path, state, replace=True)
                atomic_json(cache, result, replace=True)
                observation = self.raw_root / "observations" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8] + ".json")
                atomic_json(observation, result)
                return {**result, "raw": raw, "cache_hit": False, "network_checked": True}
        except LockBusy:
            raise Deferred((as_utc(self.clock()) + timedelta(seconds=2)).isoformat(), "SEC_OTHER_REQUEST_ACTIVE") from None
