"""Append-only metadata versions for honest future point-in-time comparisons."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import jsonschema

from .learned_research import latest_training_snapshot
from .local_files import atomic_json, exclusive_lock
from .news import canonical_url
from .rights import as_utc, price_uses, require_entitlement
from .sec_report import ROOT, SYNTHETIC_LABEL
from .validation import digest, now, stable_hash


def validate(value, kind):
    schema = json.loads((ROOT / "schemas/news_archive.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator({"$ref": "#/$defs/" + kind, "$defs": schema["$defs"]}, format_checker=jsonschema.FormatChecker()).validate(value)


def policy():
    result = json.loads((ROOT / "config/news_archive_policy.json").read_text(encoding="utf-8"))
    if result["monthly_data_budget_usd"] != 0 or result["release_enabled"] is not False or result["model_training_permission_granted"] is not False:
        raise ValueError("Archive cannot grant source training rights or release a recommendation")
    return result


def require_metadata_rights(providers, cutoff):
    records = json.loads((ROOT / "config/source_entitlements.json").read_text(encoding="utf-8"))["records"]
    records += json.loads((ROOT / "config/official_news_entitlements.json").read_text(encoding="utf-8"))["records"]
    by_provider = {row["provider"]: row for row in records}
    for provider in providers:
        if provider == "massive":
            _, path = latest_training_snapshot()
            require_entitlement(json.loads(path.read_text(encoding="utf-8")), price_uses("local_research"), cutoff)
        else:
            if provider not in by_provider:
                raise ValueError("Unknown metadata provider")
            require_entitlement(by_provider[provider], ["ingest_metadata", "store_metadata", "derived_metadata_analysis"], cutoff)


def archive_folder(root, illustrative):
    root = Path(root).resolve()
    if not root.is_relative_to(ROOT / "reports/private"):
        raise ValueError("News metadata archive must remain private")
    return root / ("illustrative" if illustrative else "real")


def capture(path, *, root=None, clock=now):
    path = Path(path).resolve()
    context = json.loads(path.read_text(encoding="utf-8"))
    illustrative = context.get("is_illustrative")
    if type(illustrative) is not bool:
        raise ValueError("Explicit real/illustrative source classification required")
    if not illustrative and not path.is_relative_to(ROOT / "reports/private"):
        raise ValueError("Real metadata must come from a private evidence artifact")
    captured_at = clock()
    if as_utc(context["as_of"]) > as_utc(captured_at):
        raise ValueError("Cannot archive evidence from after the actual capture time")
    news, settings = context["news"], policy()
    if not illustrative:
        require_metadata_rights({provider for row in news["articles"] for provider in row["providers"]}, captured_at)
    known = set()
    for row in news["articles"]:
        url = canonical_url(row["url"])
        if url in known:
            raise ValueError("Canonical article URLs must already be deduplicated")
        known.add(url)
        if not as_utc(row["retrieved_at"]) <= as_utc(context["as_of"]):
            raise ValueError("Article observation is after the saved context cutoff")
        for field in ("provider_published_at", "first_seen_at"):
            if row.get(field) and as_utc(row[field]) > as_utc(row["retrieved_at"]):
                raise ValueError("Article timestamps are inconsistent")
        if row.get("published_at") is not None or row.get("impact_reviewed") is not False or row.get("score_eligible") is not False:
            raise ValueError("Archive accepts unreviewed metadata, not promoted event claims")
        if any(field in row for field in ("body", "description", "summary", "insights", "image_url")):
            raise ValueError("Publisher bodies, summaries, sentiment and images are not archive inputs")
    folder = archive_folder(root or ROOT / "reports/private/news_archive", illustrative)
    packet_hash = stable_hash(news)
    destination = folder / "captures" / (packet_hash + ".json")
    with exclusive_lock(folder / "writer.lock"):
        if destination.exists():
            original = json.loads(destination.read_text(encoding="utf-8"))
            validate(original, "capture")
            if stable_hash(original["news_packet"]) != packet_hash:
                raise ValueError("Previously archived packet changed")
            return {"status": "already_archived", "capture_path": str(destination), "available_at": original["available_at"], "new_versions": 0, "article_count": len(original["articles"]), "is_illustrative": illustrative}
        committed = [json.loads(item.read_text(encoding="utf-8")) for item in (folder / "captures").glob("*.json")]
        if committed and max(as_utc(item["available_at"]) for item in committed) > as_utc(captured_at):
            raise ValueError("Archive clock cannot move backwards")
        capture_order = max((item["capture_order"] for item in committed), default=0) + 1
        versions, new_versions = [], 0
        for row in news["articles"]:
            metadata = {key: row.get(key) for key in settings["content_version_fields"]}
            metadata["url"] = canonical_url(row["url"])
            article_key = stable_hash(metadata["url"])[:24]
            content_hash = stable_hash(metadata)
            version_path = folder / "versions" / article_key / (content_hash + ".json")
            if not version_path.exists():
                atomic_json(version_path, {"version": "1.0.0", "is_illustrative": illustrative, "article_key": article_key,
                    "content_sha256": content_hash, "available_at": captured_at, "first_source_observed_at": row["retrieved_at"],
                    "metadata": metadata, "new_event_verification": "not_reviewed"})
                new_versions += 1
            else:
                prior = json.loads(version_path.read_text(encoding="utf-8"))
                if stable_hash(prior["metadata"]) != content_hash or prior["is_illustrative"] != illustrative:
                    raise ValueError("Archived article version changed")
            versions.append({"article_key": article_key, "content_sha256": content_hash, "version_path": str(version_path.resolve()),
                "source_ids": row["source_ids"], "source_observed_at": row["retrieved_at"], "providers": row["providers"], "categories": row["categories"]})
        committed_capture = {"version": "1.0.0", "policy_id": settings["id"], "is_illustrative": illustrative,
            "data_label": SYNTHETIC_LABEL if illustrative else None, "available_at": captured_at,
            "capture_order": capture_order,
            "context_as_of": context["as_of"], "input_path": str(path), "input_sha256": digest(path),
            "news_packet_sha256": packet_hash, "news_packet": news, "articles": versions,
            "policy_sha256": digest(ROOT / "config/news_archive_policy.json"), "archive_code_sha256": digest(Path(__file__)),
            "released_score": None, "model_training_permission_granted": False}
        validate(committed_capture, "capture")
        atomic_json(destination, committed_capture)
    return {"status": "archived", "capture_path": str(destination), "available_at": captured_at, "new_versions": new_versions, "article_count": len(versions), "is_illustrative": illustrative}


def as_of_view(cutoff, *, root=None, illustrative=False):
    folder = archive_folder(root or ROOT / "reports/private/news_archive", illustrative)
    packets = []
    for path in (folder / "captures").glob("*.json"):
        row = json.loads(path.read_text(encoding="utf-8"))
        if as_utc(row["available_at"]) > as_utc(cutoff):
            continue
        validate(row, "capture")
        if row["is_illustrative"] != illustrative or stable_hash(row["news_packet"]) != row["news_packet_sha256"] or path.stem != row["news_packet_sha256"]:
            raise ValueError("Archive capture integrity or classification differs")
        if as_utc(row["context_as_of"]) > as_utc(row["available_at"]):
            raise ValueError("Capture knowledge precedes its source context")
        if as_utc(row["available_at"]) <= as_utc(cutoff):
            packets.append(row)
    current = {}
    if packets and not illustrative:
        require_metadata_rights({provider for packet in packets for item in packet["articles"] for provider in item["providers"]}, now())
    for packet in sorted(packets, key=lambda row: (as_utc(row["available_at"]), row["capture_order"])):
        for item in packet["articles"]:
            path = Path(item["version_path"]).resolve()
            expected = folder / "versions" / item["article_key"] / (item["content_sha256"] + ".json")
            if path != expected:
                raise ValueError("Article version path differs")
            version = json.loads(path.read_text(encoding="utf-8"))
            if version["is_illustrative"] != illustrative or stable_hash(version["metadata"]) != item["content_sha256"]:
                raise ValueError("Article version content differs")
            if not as_utc(version["available_at"]) <= as_utc(packet["available_at"]) or as_utc(item["source_observed_at"]) > as_utc(packet["available_at"]):
                raise ValueError("Version was unavailable when the capture was committed")
            current[item["article_key"]] = {**item, "metadata": version["metadata"], "version_first_available_at": version["available_at"], "last_archive_observation_at": packet["available_at"]}
    latest = max(packets, key=lambda row: (as_utc(row["available_at"]), row["capture_order"])) if packets else None
    result = {"version": "1.0.0", "status": "point_in_time_metadata_view", "as_of": cutoff, "is_illustrative": illustrative,
        "data_label": SYNTHETIC_LABEL if illustrative else None, "committed_captures": len(packets), "articles": list(current.values()),
        "coverage": latest["news_packet"]["topics"] if latest else None, "coverage_checked_at": latest["context_as_of"] if latest else None,
        "coverage_current_at_query": bool(latest and 0 <= (as_utc(cutoff) - as_utc(latest["context_as_of"])).total_seconds() <= 1800),
        "released_score": None, "model_training_permission_granted": False}
    validate(result, "view")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Archive metadata now or read only what was archived by a past cutoff")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--context", type=Path)
    group.add_argument("--as-of")
    parser.add_argument("--illustrative", action="store_true", help="Read the separate illustrative namespace")
    args = parser.parse_args(argv)
    if args.as_of:
        result = as_of_view(args.as_of, illustrative=args.illustrative)
        folder = ROOT / "reports/private/news_archive_views" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        atomic_json(folder / "metadata_view.json", result)
        summary = {"status": result["status"], "known_articles": len(result["articles"]), "committed_captures": result["committed_captures"], "output_dir": str(folder.resolve())}
    else:
        found = sorted((ROOT / "reports/private/context_report").glob("*/evidence_context.json"))
        if not args.context and not found:
            parser.error("Generate a classified context report before archiving")
        result = capture(args.context or found[-1])
        summary = {key: result[key] for key in ("status", "capture_path", "available_at", "new_versions", "article_count", "is_illustrative")}
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
