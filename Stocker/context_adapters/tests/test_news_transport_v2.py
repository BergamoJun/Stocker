"""ILLUSTRATIVE — NOT LIVE MARKET DATA. Actual lock ownership regressions."""

from pathlib import Path
import io
import json
import os
import subprocess
import sys

import pytest

from context_adapters import news_refresh_v2 as v2
from research.local_files import atomic_json, exclusive_lock
from research.panel_transport import Deferred
from research.validation import digest
from research.sec_report import ROOT


def test_unlocked_persistent_file_does_not_count_as_an_active_worker(tmp_path):
    path = tmp_path / "worker.lock"
    with exclusive_lock(path):
        pass
    original = (path.read_bytes(), path.stat().st_mtime_ns)
    assert path.is_file() and not v2.lock_busy(path)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == original


def test_active_byte_lock_is_busy_and_released_file_is_reusable(tmp_path):
    path = tmp_path / "worker.lock"
    with exclusive_lock(path):
        assert v2.lock_busy(path)
    assert path.is_file() and not v2.lock_busy(path)


def fixture(tmp_path):
    root = tmp_path / "project"
    job = root / "reports/private/observation_jobs/fixture/job.json"
    atomic_json(job, {"label": "ILLUSTRATIVE"})
    baseline = tmp_path / "baseline.json"
    atomic_json(baseline, {"version": "1.0.0", "root": str(root), "files": {str(job.relative_to(root)).replace("\\", "/"): digest(job)},
        "allowed_modified": [], "allowed_new": [], "adapter_allow_new": [], "jobs": [str(job)], "reports": [], "additional_reports": []})
    record = {"id": "fixture", "provider": "massive", "plan": "stocks_basic", "is_illustrative": True, "monthly_cost_usd": 0,
        "reviewer": "ILLUSTRATIVE", "reviewed_at": "2026-11-01T00:00:00Z", "review_due_at": "2027-01-01T00:00:00Z", "evidence_url": "https://example.com/rights",
        "uses": {"ingest": "allowed", "store": "allowed", "derived_analytics": "allowed"}}
    class Opener:
        calls = 0
        def open(self, request, timeout):
            self.calls += 1
            return io.BytesIO(b'{"status":"OK","results":[]}')
    opener = Opener()
    client = v2.Client(record, "fixture-key", baseline, root=tmp_path / "budget", opener=opener, clock=lambda: "2026-11-08T13:00:00Z")
    return root, job.with_name("worker.lock"), baseline, client, opener


def test_other_process_has_priority_without_deleting_or_taking_its_lock(tmp_path):
    root, path, baseline, client, opener = fixture(tmp_path)
    code = "import sys; from research.local_files import exclusive_lock; from pathlib import Path\nwith exclusive_lock(Path(sys.argv[1])):\n print('locked', flush=True)\n sys.stdin.read(1)\n"
    process = subprocess.Popen([sys.executable, "-B", "-c", code, str(path)], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        assert process.stdout.readline().strip() == "locked"
        assert v2.lock_busy(path)
        with pytest.raises(Deferred):
            client.massive("BE", "2026-11-07T00:00:00Z", "2026-11-08T13:00:00Z")
        assert opener.calls == 0 and client.massive_requests == 0
        process.communicate(input="x", timeout=10)
        assert path.is_file() and not v2.lock_busy(path)
        assert client.massive("BE", "2026-11-07T00:00:00Z", "2026-11-08T13:00:00Z")["raw"]
        assert opener.calls == client.massive_requests == 1
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=10)


def test_new_adapter_scope_rejects_unregistered_files_before_a_request(tmp_path):
    root, path, baseline, client, opener = fixture(tmp_path)
    extra = root / "context_adapters/unregistered.py"
    extra.parent.mkdir()
    extra.write_text("unexpected", encoding="utf-8")
    with pytest.raises(ValueError, match="ADAPTER_SCOPE_CONFLICT"):
        client.massive("BE", "2026-11-07T00:00:00Z", "2026-11-08T13:00:00Z")
    assert opener.calls == 0


def test_successor_freeze_pins_adapter_code_without_modifying_parent(tmp_path):
    root, path, baseline, client, opener = fixture(tmp_path)
    new = root / "context_adapters/new.py"
    new.parent.mkdir()
    new.write_text("verified adapter", encoding="utf-8")
    record = json.loads(baseline.read_text(encoding="utf-8")); record["adapter_allow_new"] = ["context_adapters/new.py"]
    declared = baseline.with_name("declared.json"); atomic_json(declared, record)
    original = digest(declared)
    frozen = v2.freeze_scope(declared, baseline.with_name("frozen.json"))
    assert digest(declared) == original and v2.check_scope(frozen)["adapter_files"] == 1
    new.write_text("changed adapter", encoding="utf-8")
    with pytest.raises(ValueError, match="PROJECT_CONFLICT"):
        v2.check_scope(frozen)
