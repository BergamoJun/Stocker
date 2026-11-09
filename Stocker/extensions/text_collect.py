"""Bounded official authored-text collection with policy and robots checks."""

import argparse
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import Request, build_opener
from urllib.robotparser import RobotFileParser
import uuid

from research.fetch_prices import NoRedirect
from research.local_files import atomic_json, exclusive_lock, LockBusy
from research.news_transport import retry_time
from research.rights import as_utc
from research.sec_report import ROOT
from research.sec_transport import now
from research.validation import stable_hash
from . import scope, text_sources as source, text_store as store

AGENT = "Stocker-personal-source-text/0.1"


class Collector:
    def __init__(self, baseline, *, root=None, clock=now, pause=time.sleep, opener=None, illustrative=False):
        self.baseline, self.clock, self.pause = Path(baseline), clock, pause
        self.root = Path(root or ROOT / "reports/private/source_text_transport") / ("illustrative" if illustrative else "real")
        if not illustrative and not self.root.resolve().is_relative_to((ROOT / "reports/private").resolve()):
            raise ValueError("PRIVATE_TEXT_TRANSPORT_REQUIRED")
        self.settings = source.policy(clock)
        self.proof = scope.check(self.baseline)
        self.opener, self.requests, self.illustrative = opener or build_opener(NoRedirect()), 0, illustrative
        self.intervals = {key: self.settings["minimum_request_interval_seconds"] for key in self.settings["sources"]}

    def gate(self):
        source.policy(self.clock)
        return scope.check(self.baseline, expected_sha256=self.proof["baseline_sha256"])

    def fetch(self, url, provider):
        self.gate()
        source.approved_url(url, provider, auxiliary=True)
        if self.requests >= self.settings["maximum_http_requests"]:
            raise ValueError("OFFICIAL_TEXT_RUN_REQUEST_CAP")
        state_path = self.root / "budget.json"
        with exclusive_lock(self.root / "request.lock"):
            state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
            if state.get("blocked_until") and as_utc(self.clock()) < as_utc(state["blocked_until"]):
                raise ValueError("OFFICIAL_TEXT_COOLDOWN")
            if state.get("last_completed_at"):
                remaining = (as_utc(state["last_completed_at"]) + timedelta(seconds=self.intervals[provider]) - as_utc(self.clock())).total_seconds()
                if remaining > 0:
                    self.pause(remaining)
            self.gate()
            self.requests += 1
            try:
                with self.opener.open(Request(url, headers={"User-Agent": AGENT, "Accept": "text/html,application/xml,text/xml,text/plain"}), timeout=20) as response:
                    raw = response.read(self.settings["maximum_response_bytes"] + 1)
            except HTTPError as error:
                if error.code == 429:
                    state["blocked_until"] = retry_time(error.headers.get("Retry-After") if error.headers else None, self.clock(), 900)
                raise ValueError("OFFICIAL_HTTP_" + str(error.code)) from None
            except OSError:
                raise ValueError("OFFICIAL_TEXT_TRANSPORT_FAILURE") from None
            finally:
                state["last_completed_at"] = self.clock()
                atomic_json(state_path, state, replace=True)
        if len(raw) > self.settings["maximum_response_bytes"]:
            raise ValueError("OVERSIZED_OFFICIAL_TEXT")
        return raw, self.clock()

    def cached(self, url, provider, kind, normalize):
        self.gate()
        path = self.root / "cache" / (stable_hash([url, provider, kind, store.methods()]) + ".json")
        if path.exists():
            saved = json.loads(path.read_text(encoding="utf-8"))
            value = saved["value"]
            age = (as_utc(self.clock()) - as_utc(value["checked_at"])).total_seconds()
            if saved["sha256"] != stable_hash(value):
                raise ValueError("OFFICIAL_TEXT_CACHE_CHANGED")
            if 0 <= age < self.settings["cache_seconds"]:
                return {**value, "cache_reused": True}
        try:
            raw, observed = self.fetch(url, provider)
            value = {"status": "available", "checked_at": observed, "payload": normalize(raw, observed), "response_sha256": hashlib.sha256(raw).hexdigest()}
        except (ValueError, OSError, LockBusy) as error:
            code = str(error) if isinstance(error, ValueError) else "SOURCE_UNAVAILABLE"
            # Only internal reason codes reach receipts, never source response text.
            value = {"status": "unavailable", "checked_at": self.clock(), "reason_code": code if code.isascii() and code.replace("_", "").isalnum() else "SOURCE_FORMAT_FAILURE"}
            if kind == "robots" and code == "OFFICIAL_HTTP_404":
                value = {"status": "available", "checked_at": self.clock(), "payload": "User-agent: *\nDisallow:\n", "robots_http_404_no_rules": True}
        self.gate()
        atomic_json(path, {"value": value, "sha256": stable_hash(value)}, replace=True)
        return {**value, "cache_reused": False}

    def collect(self, *, store_root=None, output=None):
        initial, rows, providers = self.requests, [], {}
        for provider, profile in self.settings["sources"].items():
            origin = "https://" + profile["host"]
            robots = self.cached(origin + "/robots.txt", provider, "robots", lambda raw, _: raw.decode("utf-8-sig"))
            rights = self.cached(profile["policy_url"], provider, "rights", lambda raw, _: {"matches_reviewed_policy": source.policy_hash(raw) == profile["policy_text_sha256"]})
            if robots["status"] != "available" or rights["status"] != "available" or rights["payload"]["matches_reviewed_policy"] is not True:
                providers[provider] = "robots_or_policy_unavailable_or_changed"
                continue
            rules = RobotFileParser(); rules.parse(robots["payload"].splitlines())
            rate = rules.request_rate(AGENT)
            delay = max(self.settings["minimum_request_interval_seconds"], rules.crawl_delay(AGENT) or 0, rate.seconds / rate.requests if rate and rate.requests > 0 else 0)
            if delay > 30:
                providers[provider] = "robots_delay_exceeds_bounded_run"; continue
            self.intervals[provider] = delay
            if not rules.can_fetch(AGENT, profile["feed_url"]):
                providers[provider] = "robots_disallowed"; continue
            feed = self.cached(profile["feed_url"], provider, "feed", lambda raw, observed: source.feed_links(raw, provider, observed, self.settings))
            if feed["status"] != "available":
                providers[provider] = "feed_unavailable"; continue
            providers[provider] = "bounded_no_links" if not feed["payload"] else "processed"
            for item in feed["payload"]:
                url = item["url"]
                if not rules.can_fetch(AGENT, url):
                    rows.append({"source": provider, "url": url, "status": "robots_disallowed"}); continue
                value = self.cached(url, provider, "article", lambda raw, observed: {**source.article(raw, url, provider, observed, self.settings), "retrieved_at": observed})
                if value["status"] != "available":
                    rows.append({"source": provider, "url": url, "status": "withheld", "reason_code": value["reason_code"]}); continue
                parsed = value["payload"]
                if parsed["published_date"] != as_utc(item["provider_published_at"]).date().isoformat():
                    rows.append({"source": provider, "url": url, "status": "publication_date_conflict"}); continue
                self.gate()
                folder, record = store.capture(parsed, url, provider, self.baseline, root=store_root, illustrative=self.illustrative, clock=self.clock)
                rows.append({"source": provider, "url": url, "status": "stored", "source_id": record["source_id"], "folder": str(folder), "paragraph_count": len(record["paragraphs"]),
                    "record_sha256": record["record_sha256"], "source_retrieved_at": record["retrieved_at"], "available_at": record["available_at"], "cache_reused": value["cache_reused"]})
        self.gate()
        folder = Path(output or ROOT / "reports/private/source_text_runs") / ("illustrative" if self.illustrative else "real") / uuid.uuid4().hex
        if not self.illustrative and not folder.resolve().is_relative_to((ROOT / "reports/private").resolve()):
            raise ValueError("PRIVATE_TEXT_RUN_REQUIRED")
        # The packet is generated separately after a full second so that source
        # receipts written in this run cannot be treated as earlier same-second data.
        result = {"contract": "official-text-collection-run-v1", "finished_at": self.clock(), "is_illustrative": self.illustrative, "providers": providers, "documents": rows,
            "actual_requests": self.requests - initial, "stored_documents": sum(r["status"] == "stored" for r in rows),
            "baseline_sha256": self.proof["baseline_sha256"], "method_hashes": store.methods(), "external_model_calls": 0, "model_training_performed": False,
            "company_news_coverage_complete": False, "world_news_coverage_complete": False}
        atomic_json(folder / "RESULTS.json", result)
        return result, folder


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--collect", action="store_true")
    parser.add_argument("--packet", action="store_true")
    parser.add_argument("--as-of")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--freeze", type=Path)
    args = parser.parse_args(argv)
    if args.check:
        value = scope.check(args.baseline, replay=args.replay)
        if args.freeze:
            value["frozen"] = scope.freeze(args.baseline, args.freeze)
        print(json.dumps(value)); return
    if args.collect:
        result, folder = Collector(args.baseline).collect()
        print(json.dumps({"output": str(folder), "actual_requests": result["actual_requests"], "stored_documents": result["stored_documents"], "provider_statuses": result["providers"]}))
    if args.packet:
        scope.check(args.baseline)
        value = store.packet(args.as_of or now())
        path = ROOT / "reports/private/source_text_packets" / (uuid.uuid4().hex + ".json")
        atomic_json(path, value)
        print(json.dumps({"packet_path": str(path), "documents": len(value["documents"]), "external_model_calls": 0}))
    if not any((args.check, args.collect, args.packet)):
        parser.error("choose --collect, --packet or --check")


if __name__ == "__main__":
    main()
