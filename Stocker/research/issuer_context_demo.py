"""ILLUSTRATIVE — NOT LIVE MARKET DATA. Generic issuer news and conflict guards."""

from copy import deepcopy
from datetime import timedelta
import hashlib
import io
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import uuid

from . import integrated_report_demo as old_demo, issuer_profiles as profiles, issuer_news, news_refresh, expanded_report, project_compatibility
from .context_transport import Client
from .local_files import atomic_json
from .rights import as_utc
from .sec_report import ROOT, SYNTHETIC_LABEL
from .validation import digest, stable_hash


class Response(io.BytesIO):
    status = 200


class Opener:
    def __init__(self):
        self.calls = []
        self.failure = None

    def open(self, request, timeout):
        self.calls.append(request.full_url)
        if self.failure:
            raise self.failure
        parsed = urlparse(request.full_url)
        query = parse_qs(parsed.query)
        if parsed.hostname == "api.massive.com":
            ticker = query["ticker"][0]
            payload = {"status": "OK", "results": [{"tickers": [ticker], "article_url": "https://example.com/issuer-" + ticker,
                "title": SYNTHETIC_LABEL + " " + ticker, "published_utc": "2026-11-08T13:00:00Z", "publisher": {"name": "Illustrative publisher"},
                "description": "BODY_MUST_BE_DROPPED", "insights": [{"sentiment": "positive"}], "image_url": "https://example.com/image"}]}
        else:
            key = hashlib.sha256(query["query"][0].encode()).hexdigest()[:12]
            payload = {"articles": [{"url": "https://example.com/illustrative-world-" + key, "title": SYNTHETIC_LABEL,
                "seendate": "20261108T130000Z", "domain": "example.com"}]}
        return Response(json.dumps(payload).encode())


def setup(folder):
    folder = Path(folder)
    old = old_demo.setup(folder / "legacy")
    clock = old["clock"]
    project = folder / "isolated_project"
    anchor = project / "research/anchor.py"
    anchor.parent.mkdir(parents=True, exist_ok=True)
    anchor.write_text("# ILLUSTRATIVE immutable legacy module\n", encoding="utf-8")
    baseline = folder / "baseline.json"
    atomic_json(baseline, {"version": "1.0.0", "root": str(project.resolve()), "created_at": clock(), "files": {"research/anchor.py": digest(anchor)},
        "allowed_modified": [], "allowed_new": [], "jobs": [], "reports": [], "tests_before": 0})
    registry = json.loads((ROOT / "config/issuer_profiles.json").read_text(encoding="utf-8"))
    registry.update(is_illustrative=True, reviewed_at="2026-11-08T13:10:00Z", reviewer_kind=SYNTHETIC_LABEL)
    registry["profiles"] = [registry["profiles"][0], registry["profiles"][2]]
    registry["profiles"][0].update(cik="0000001001")
    registry["profiles"][1].update(cik="0000001003", symbol="DEMOC", issuer_name="ILLUSTRATIVE DEMOC")
    registry_path = folder / "registry.json"
    atomic_json(registry_path, registry)
    record = {"id": "illustrative-metadata", "provider": "massive", "plan": "stocks_basic", "is_illustrative": True, "monthly_cost_usd": 0,
        "reviewer": SYNTHETIC_LABEL, "reviewed_at": "2026-11-06T00:00:00Z", "review_due_at": "2027-11-01T00:00:00Z", "evidence_url": "https://example.com/rights",
        "uses": {"ingest": "allowed", "store": "allowed", "derived_analytics": "allowed"}}
    opener = Opener()
    client = Client(record, "illustrative-key", baseline, root=folder / "massive_budget", gdelt_root=folder / "gdelt_budget", clock=clock, sleep=clock.advance, opener=opener)
    return {"folder": folder, "old": old, "clock": clock, "baseline": baseline, "anchor": anchor, "registry_path": registry_path, "profile_root": folder / "profiles",
        "news_root": folder / "issuer_news", "client": client, "opener": opener, "source_store": old["financial_roots"]["source_store"], "cache_root": folder / "cache"}


def test_setup(tmp_path, monkeypatch):
    """Isolate frozen private news stores without changing their validators."""
    from . import news_archive, news_connections, news_review_store
    names = set(news_connections.CORE + news_review_store.LEGACY_FILES + ["config/news_archive_policy.json", "schemas/news_archive.schema.json"])
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / name).read_bytes())
    for module in (news_archive, news_connections, news_review_store):
        monkeypatch.setattr(module, "ROOT", tmp_path)
    return setup(tmp_path / "reports/private/fixture")


def refresh(case):
    return news_refresh.run(["1001", "1003"], case["baseline"], **{k: case[k] for k in ("client", "registry_path", "profile_root", "news_root", "source_store", "cache_root")},
        output=case["folder"] / "refreshes", illustrative=True, clock=case["clock"], sleep=case["clock"].advance,
        official_collect=lambda: {"feeds": [], "articles": [], "status": "available", "actual_requests": 0})


def submit(case, row, *, direction=1, reviewer="ILLUSTRATIVE_REVIEWER"):
    context = json.loads(Path(row["context_path"]).read_text(encoding="utf-8"))
    if not context["is_illustrative"]:
        raise ValueError("DEMO_CANNOT_REVIEW_REAL_NEWS")
    case["clock"].advance(2)
    checked = case["clock"]()
    case["clock"].advance(2)
    at = case["clock"]()
    value = issuer_news.template(context)
    article = next(a for a in context["news"]["articles"] if "company" in a["categories"])
    value["reviewer_kind"] = "human"
    value["manifest"].update(template_only=False, reviewer_id=reviewer, submitted_at=at, events=[{
        "event_id": "fixture_company_event", "review_status": "reviewed", "review_id": uuid.uuid4().hex, "source_ids": [article["id"]], "original_urls": [article["url"]],
        "rationale": SYNTHETIC_LABEL, "five_session_relevance_explanation": SYNTHETIC_LABEL, "counterargument": SYNTHETIC_LABEL, "monitoring_event": SYNTHETIC_LABEL,
        "reviewed_at": at, "published_at": article["provider_published_at"], "first_public_at": article["provider_published_at"], "direction": direction,
        "impact": "high", "relevance": "direct_company", "reliability": "primary", "novelty": "new",
        "original_checks": [{"source_id": article["id"], "url": article["url"], "checked_at": checked, "publication_verified": True, "published_at": article["provider_published_at"]}]}])
    path = case["folder"] / "reviews" / (uuid.uuid4().hex + ".json")
    atomic_json(path, value)
    receipt = issuer_news.import_review(row["context_path"], path, root=case["news_root"], profile_root=case["profile_root"], illustrative=True, clock=case["clock"])
    case["clock"].advance(2)
    return path, receipt


def build(case, issuer="1001", cutoff=None):
    old = case["old"]
    options = {k: old[k] for k in ("input_root", "news_root", "archive_root", "financial_roots")}
    return expanded_report.build(issuer, case["baseline"], cutoff or case["clock"](), profile_root=case["profile_root"], news_root=case["news_root"], base_options=options, illustrative=True, clock=case["clock"])


def run(root=None):
    case = setup(root or ROOT / "reports/private/issuer_context_demo" / uuid.uuid4().hex)
    result, _ = refresh(case)
    case["clock"].advance(2)
    pending = build(case)
    for row in result["issuers"]:
        submit(case, row, direction=1 if row["symbol"] == "BE" else -1)
    values = [build(case, issuer) for issuer in ("1001", "1003")]
    paths = [str(expanded_report.write(v, case["folder"] / "reports") / "REPORT.ko.md") for v in values]
    golden = {"label": SYNTHETIC_LABEL, "issuers": 2, "pending_highlights": pending["coverage"]["news_highlights"],
        "reviewed_highlights": [v["coverage"]["news_highlights"] for v in values], "second_issuer_price_available": values[1]["coverage"]["price_scenario_available"], "released_score": None}
    expected = json.loads((ROOT / "examples/issuer_context_golden.json").read_text(encoding="utf-8"))
    if golden != expected:
        raise ValueError("ISSUER_CONTEXT_GOLDEN_CHANGED")
    atomic_json(case["folder"] / "RESULTS.json", {"golden": golden, "report_paths": paths})
    return {"golden": golden, "report_paths": paths}


if __name__ == "__main__":
    print(json.dumps(run()))
