"""ILLUSTRATIVE - shared-lock tests use local child processes, no live API."""

from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from urllib.error import HTTPError
import pytest

from extensions import api_queue, queued_news
from research.local_files import atomic_json, exclusive_lock, LockBusy
from research.panel_transport import Deferred, SourceUnavailable
from research.rights import RightsError
from research.sec_report import ROOT
from research.validation import digest


class Clock:
    def __init__(self): self.value = datetime(2026, 11, 10, 12, tzinfo=timezone.utc); self.elapsed = 0
    def __call__(self): return self.value.isoformat()
    def pause(self, seconds): self.value += timedelta(seconds=seconds); self.elapsed += seconds


def fixture(tmp_path, *, real_pause=False):
    root = tmp_path / "project"
    job = root / "reports/private/observation_jobs/fixture/job.json"
    atomic_json(job, {"label": "ILLUSTRATIVE"})
    baseline = tmp_path / "baseline.json"
    atomic_json(baseline, {"version": "1.0.0", "root": str(root), "files": {str(job.relative_to(root)).replace("\\", "/"): digest(job)},
        "allowed_modified": [], "allowed_new": [], "allowed_extensions": [], "adapter_allow_new": [], "jobs": [str(job)], "reports": [], "additional_reports": []})
    record = {"id": "fixture", "provider": "massive", "plan": "stocks_basic", "is_illustrative": True, "monthly_cost_usd": 0,
        "reviewer": "ILLUSTRATIVE", "reviewed_at": "2026-11-01T00:00:00Z", "review_due_at": "2027-01-01T00:00:00Z", "evidence_url": "https://example.com/rights",
        "uses": {"ingest": "allowed", "store": "allowed", "derived_analytics": "allowed"}}
    clock = Clock()
    class Opener:
        def __init__(self): self.calls, self.failure = [], None
        def open(self, request, timeout):
            self.calls.append(clock())
            with pytest.raises(LockBusy):
                with exclusive_lock(tmp_path / "account/request.lock"):
                    pass
            if self.failure: raise self.failure
            return io.BytesIO(b'{"status":"OK","results":[]}')
    opener = Opener()
    client = queued_news.Client(record, "illustrative-key", baseline, root=tmp_path / "account", opener=opener, clock=clock,
        sleep=__import__("time").sleep if real_pause else clock.pause)
    if not real_pause:
        client.queue.monotonic = lambda: clock.elapsed
    return client, opener, clock, job, baseline


def call(client):
    return client.massive("BE", "2026-11-08T00:00:00Z", "2026-11-10T12:00:00Z")


def test_priority_and_fifo_are_separate_from_legacy_worker_claims(tmp_path):
    clock = Clock(); queue = api_queue.Queue(tmp_path, clock=clock, pause=clock.pause, monotonic=lambda: clock.elapsed)
    bulk = queue.enqueue("bulk"); news = queue.enqueue("news"); price = queue.enqueue("current_price")
    assert queue.head() == price.stem
    queue.update(price, "complete"); assert queue.head() == news.stem
    other = queue.enqueue("news"); assert queue.head() == news.stem
    queue.update(news, "complete"); assert queue.head() == other.stem
    assert bulk.exists()


def test_aging_and_expiry_prevent_permanent_abandoned_head(tmp_path):
    clock = Clock(); queue = api_queue.Queue(tmp_path, clock=clock, pause=clock.pause)
    bulk = queue.enqueue("bulk"); clock.pause(45); news = queue.enqueue("news")
    assert queue.head() == bulk.stem
    clock.pause(140)
    assert queue.head() == news.stem
    assert json.loads(bulk.read_text())["status"] == "expired"


def test_waiting_for_unserved_higher_priority_is_bounded(tmp_path):
    clock = Clock(); queue = api_queue.Queue(tmp_path, clock=clock, pause=clock.pause, monotonic=lambda: clock.elapsed)
    queue.settings["maximum_local_wait_seconds"] = .2
    queue.enqueue("current_price")
    with pytest.raises(Deferred):
        with queue.slot("news"):
            pytest.fail("lower-priority request should not acquire")
    statuses = [json.loads(p.read_text())["status"] for p in (queue.root / "tickets").glob("*.json")]
    assert "timed_out" in statuses


def test_account_completion_spacing_is_shared_and_http_owns_byte_lock(tmp_path):
    client, opener, clock, *_ = fixture(tmp_path)
    call(client); call(client)
    assert opener.calls == ["2026-11-10T12:00:00+00:00", "2026-11-10T12:00:15+00:00"]
    state = json.loads((client.root / "budget.json").read_text())
    assert state["requests_attempted"] == 2 and client.queue.audit["acquired"] == 2


def test_existing_cooldown_makes_zero_http_and_preserves_account_state(tmp_path):
    client, opener, clock, *_ = fixture(tmp_path)
    state = {"blocked_until": "2026-11-10T13:00:00Z", "requests_attempted": 77}
    atomic_json(client.root / "budget.json", state)
    with pytest.raises(Deferred): call(client)
    assert not opener.calls and json.loads((client.root / "budget.json").read_text()) == state


@pytest.mark.parametrize("retry_after,expected", [("600", "2026-11-10T12:15:00"), ("1200", "2026-11-10T12:20:00")])
def test_429_is_one_attempt_and_shared_retry_after_blocks_next_request(tmp_path, retry_after, expected):
    client, opener, clock, *_ = fixture(tmp_path)
    opener.failure = HTTPError("https://api.massive.com/", 429, "ILLUSTRATIVE", {"Retry-After": retry_after}, None)
    with pytest.raises(Deferred): call(client)
    with pytest.raises(Deferred): call(client)
    assert len(opener.calls) == client.massive_requests == 1
    assert json.loads((client.root / "budget.json").read_text())["blocked_until"].startswith(expected)


@pytest.mark.parametrize("error", [HTTPError("https://api.massive.com/", 403, "ILLUSTRATIVE", {}, None), OSError("ILLUSTRATIVE")])
def test_source_errors_are_not_retried_and_lock_is_released(tmp_path, error):
    client, opener, clock, *_ = fixture(tmp_path); opener.failure = error
    with pytest.raises(SourceUnavailable): call(client)
    assert len(opener.calls) == 1
    with exclusive_lock(client.root / "request.lock"):
        pass


def test_missing_source_rights_prevent_queue_or_http(tmp_path):
    client, opener, *_ = fixture(tmp_path)
    client.record["uses"]["derived_analytics"] = "denied"
    with pytest.raises(RightsError): call(client)
    assert client.queue.audit["tickets"] == 0 and not opener.calls


def test_active_observer_yields_without_taking_or_deleting_its_lock(tmp_path):
    client, opener, clock, job, _ = fixture(tmp_path)
    path = job.with_name("worker.lock")
    with exclusive_lock(path):
        with pytest.raises(Deferred): call(client)
    assert path.exists() and not opener.calls and client.queue.audit["tickets"] == 0


def test_observer_start_after_slot_acquisition_still_prevents_http(tmp_path):
    client, opener, *_ = fixture(tmp_path)
    client.observer_busy = lambda: client.queue.audit["acquired"] > 0
    with pytest.raises(Deferred): call(client)
    assert not opener.calls


def test_run_cap_and_key_not_in_url_or_budget_receipts(tmp_path):
    client, opener, *_ = fixture(tmp_path)
    for _ in range(3):
        value = call(client)
        assert "illustrative-key" not in value["source_url"]
    with pytest.raises(SourceUnavailable, match="RUN_REQUEST_CAP"): call(client)
    assert len(opener.calls) == 3
    assert all("illustrative-key" not in p.read_text() for p in client.root.rglob("*.json"))


def test_undeclared_extension_change_stops_before_http(tmp_path):
    client, opener, clock, job, _ = fixture(tmp_path)
    path = job.parents[4] / "extensions/unregistered.py"
    path.parent.mkdir(); path.write_text("ILLUSTRATIVE")
    with pytest.raises(ValueError, match="UNDECLARED_EXTENSION"): call(client)
    assert not opener.calls


def child_lock(path, release):
    code = "import sys,time; from pathlib import Path; from research.local_files import exclusive_lock\nwith exclusive_lock(Path(sys.argv[1])):\n print('locked',flush=True)\n while not Path(sys.argv[2]).exists(): time.sleep(.01)\n"
    proc = subprocess.Popen([sys.executable, "-B", "-c", code, str(path), str(release)], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert proc.stdout.readline().strip() == "locked"
    return proc


def test_waits_for_actual_legacy_process_lock_then_uses_same_file(tmp_path):
    path, release = tmp_path / "request.lock", tmp_path / "release"
    proc = child_lock(path, release)
    timer = threading.Timer(.3, lambda: release.write_text("release")); timer.start()
    queue = api_queue.Queue(tmp_path)
    try:
        with queue.slot("news"):
            with pytest.raises(LockBusy):
                with exclusive_lock(path): pass
        assert queue.audit["shared_lock_waits"] > 0 and queue.audit["acquired"] == 1
        assert path.exists()
        proc.wait(timeout=5)
    finally:
        timer.cancel(); timer.join(timeout=2)
        if proc.poll() is None: proc.terminate(); proc.communicate(timeout=5)


def test_method_conflict_during_queue_wait_prevents_http(tmp_path):
    client, opener, clock, job, baseline = fixture(tmp_path, real_pause=True)
    release = tmp_path / "release"
    proc = child_lock(client.root / "request.lock", release)
    def changed():
        job.write_text('{"label":"ILLUSTRATIVE CHANGED"}')
        release.write_text("release")
    timer = threading.Timer(.3, changed); timer.start()
    try:
        with pytest.raises(ValueError, match="PROJECT_CONFLICT"): call(client)
        assert not opener.calls and client.massive_requests == 0
        proc.wait(timeout=5)
    finally:
        timer.cancel(); timer.join(timeout=2)
        if proc.poll() is None: proc.terminate(); proc.communicate(timeout=5)
