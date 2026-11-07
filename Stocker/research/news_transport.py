"""Shared local request pacing and 429 cooldown; no retries or quota bypass."""

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.parse import urlparse

from .rights import as_utc
from .sec_report import ROOT


class NewsRequestDeferred(ValueError):
    def __init__(self, retry_at):
        super().__init__("News request deferred by shared rate budget")
        self.retry_at = retry_at


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def retry_time(header, at, default_seconds):
    fallback = as_utc(at) + timedelta(seconds=default_seconds)
    if header:
        try:
            seconds = int(header)
            parsed = as_utc(at) + timedelta(seconds=max(0, seconds))
        except (ValueError, TypeError, OverflowError):
            try:
                parsed = parsedate_to_datetime(header)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError, OverflowError):
                parsed = fallback
        fallback = max(fallback, parsed)
    return fallback.astimezone(timezone.utc).isoformat()


def request_metadata(request, opener, *, clock=now, sleep=time.sleep, state_dir=None, settings=None):
    endpoint = urlparse(request.full_url)
    if (endpoint.scheme != "https" or endpoint.hostname != "api.gdeltproject.org" or
            endpoint.path != "/api/v2/doc/doc" or endpoint.username or endpoint.password or endpoint.port not in {None, 443}):
        raise ValueError("Only the fixed GDELT metadata endpoint is supported")
    settings = settings or json.loads((ROOT / "config/service_budget_policy.json").read_text(encoding="utf-8"))["gdelt"]
    folder = Path(state_dir) if state_dir is not None else ROOT / "local_data/gdelt_budget"
    folder.mkdir(parents=True, exist_ok=True)
    lock, state_path = folder / "request.lock", folder / "request_state.json"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise NewsRequestDeferred((as_utc(clock()) + timedelta(seconds=30)).isoformat()) from None
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(str(os.getpid()))
    state = {}
    def persist():
        temporary = folder / ("state-" + str(os.getpid()) + ".tmp")
        temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        temporary.replace(state_path)
    try:
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
        at = clock()
        if state.get("blocked_until") and as_utc(at) < as_utc(state["blocked_until"]):
            raise NewsRequestDeferred(state["blocked_until"])
        if state.get("last_completed_at"):
            due = as_utc(state["last_completed_at"]) + timedelta(seconds=settings["minimum_request_spacing_seconds"])
            delay = (due - as_utc(at)).total_seconds()
            if delay > 0:
                sleep(min(delay, settings["minimum_request_spacing_seconds"]))
                if as_utc(clock()) < due:
                    raise NewsRequestDeferred(due.isoformat())
        state.update(requests_attempted=state.get("requests_attempted", 0) + 1,
                     last_started_at=clock(), last_query_sha256=hashlib.sha256(request.full_url.encode()).hexdigest())
        persist()
        try:
            with opener.open(request, timeout=15) as response:
                state["last_http_status"] = getattr(response, "status", 200)
                body = response.read(3_000_001)
        except HTTPError as error:
            if error.code == 429:
                state["blocked_until"] = retry_time(error.headers.get("Retry-After") if error.headers else None,
                                                    clock(), settings["default_429_cooldown_seconds"])
                error.retry_at = state["blocked_until"]
            state["last_http_status"] = error.code
            raise
        finally:
            state["last_completed_at"] = clock()
            persist()
        if len(body) > 3_000_000:
            raise ValueError("News response exceeded bounded size")
        if body.lstrip().lower().startswith(b"please limit requests"):
            state["blocked_until"] = retry_time(None, clock(), settings["default_429_cooldown_seconds"])
            state["throttle_response_kind"] = "provider_plaintext_limit_message"
            persist()
            raise NewsRequestDeferred(state["blocked_until"])
        return json.loads(body)
    finally:
        lock.unlink(missing_ok=True)
