"""Bounded priority/FIFO acquisition of the existing account byte lock."""

from contextlib import contextmanager
from datetime import timedelta
import json
import os
from pathlib import Path
import time
import uuid

from research.local_files import atomic_json, exclusive_lock, LockBusy
from research.panel_transport import Deferred
from research.rights import as_utc
from research.sec_transport import now

BASE = Path(__file__).resolve().parent


def policy():
    value = json.loads((BASE / "api_queue_policy.json").read_text(encoding="utf-8"))
    if value["monthly_data_budget_usd"] != 0 or value["source_http_retries"] != 0 or value["minimum_seconds_after_completion"] < 15:
        raise ValueError("QUEUE_SCOPE_CHANGED")
    return value


class Queue:
    def __init__(self, account_root, *, clock=now, pause=time.sleep, monotonic=time.monotonic, observer_busy=lambda: False):
        self.account = Path(account_root)
        self.root = self.account / "request_queue"
        self.clock, self.pause, self.monotonic, self.observer_busy = clock, pause, monotonic, observer_busy
        self.settings = policy()
        self.audit = {"tickets": 0, "shared_lock_waits": 0, "queue_waits": 0, "acquired": 0, "expired": 0, "observer_yields": 0}

    @contextmanager
    def coordinator(self):
        deadline = self.monotonic() + 2
        while True:
            lock = exclusive_lock(self.root / "coordinator.lock")
            try:
                lock.__enter__()
                break
            except LockBusy:
                if self.monotonic() >= deadline:
                    raise Deferred((as_utc(self.clock()) + timedelta(seconds=15)).isoformat()) from None
                self.pause(.005)
        try:
            yield
        finally:
            lock.__exit__(None, None, None)

    def update(self, ticket, status):
        with self.coordinator():
            value = json.loads(ticket.read_text(encoding="utf-8"))
            value.update(status=status, updated_at=self.clock())
            atomic_json(ticket, value, replace=True)
            atomic_json(self.root / "events" / (uuid.uuid4().hex + ".json"), value)

    def enqueue(self, lane):
        if lane not in self.settings["priorities"]:
            raise ValueError("UNSUPPORTED_QUEUE_LANE")
        if self.observer_busy():
            self.audit["observer_yields"] += 1
            raise Deferred((as_utc(self.clock()) + timedelta(seconds=15)).isoformat())
        with self.coordinator():
            state_path = self.root / "sequence.json"
            number = json.loads(state_path.read_text(encoding="utf-8"))["sequence"] + 1 if state_path.exists() else 1
            atomic_json(state_path, {"sequence": number}, replace=True)
            ticket = self.root / "tickets" / (uuid.uuid4().hex + ".json")
            value = {"ticket_id": ticket.stem, "sequence": number, "lane": lane, "pid": os.getpid(), "status": "waiting", "created_at": self.clock(),
                "updated_at": self.clock(), "expires_at": (as_utc(self.clock()) + timedelta(seconds=self.settings["lease_seconds"])).isoformat()}
            atomic_json(ticket, value)
            atomic_json(self.root / "events" / (uuid.uuid4().hex + ".json"), value)
        self.audit["tickets"] += 1
        return ticket

    def head(self):
        with self.coordinator():
            rows = []
            for path in (self.root / "tickets").glob("*.json"):
                value = json.loads(path.read_text(encoding="utf-8"))
                if value["status"] != "waiting":
                    continue
                if as_utc(value["expires_at"]) <= as_utc(self.clock()):
                    value.update(status="expired", updated_at=self.clock())
                    atomic_json(path, value, replace=True)
                    atomic_json(self.root / "events" / (uuid.uuid4().hex + ".json"), value)
                    self.audit["expired"] += 1
                    continue
                age = max(0, (as_utc(self.clock()) - as_utc(value["created_at"])).total_seconds())
                priority = max(0, self.settings["priorities"][value["lane"]] - int(age / self.settings["aging_seconds"]) * 10)
                rows.append((priority, value["sequence"], path.stem))
        return min(rows)[2] if rows else None

    @contextmanager
    def slot(self, lane="news"):
        ticket = self.enqueue(lane)
        deadline = self.monotonic() + self.settings["maximum_local_wait_seconds"]
        outcome = "failed"
        try:
            while True:
                if self.observer_busy():
                    self.audit["observer_yields"] += 1
                    raise Deferred((as_utc(self.clock()) + timedelta(seconds=15)).isoformat())
                if self.monotonic() >= deadline:
                    outcome = "timed_out"
                    raise Deferred((as_utc(self.clock()) + timedelta(seconds=15)).isoformat())
                if self.head() != ticket.stem:
                    self.audit["queue_waits"] += 1
                    self.pause(self.settings["queue_poll_seconds"])
                    continue
                lock = exclusive_lock(self.account / "request.lock")
                try:
                    lock.__enter__()
                except LockBusy:
                    self.audit["shared_lock_waits"] += 1
                    self.pause(self.settings["shared_lock_poll_seconds"])
                    continue
                try:
                    if self.observer_busy():
                        self.audit["observer_yields"] += 1
                        raise Deferred((as_utc(self.clock()) + timedelta(seconds=15)).isoformat())
                    if self.head() != ticket.stem:
                        continue
                    self.update(ticket, "running")
                    self.audit["acquired"] += 1
                    yield {"ticket_id": ticket.stem, "lane": lane}
                    outcome = "complete"
                    return
                finally:
                    lock.__exit__(None, None, None)
        except Deferred:
            if outcome != "timed_out":
                outcome = "deferred"
            raise
        finally:
            self.update(ticket, outcome)
