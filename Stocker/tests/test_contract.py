"""Pre-development contract tests. No network access or real-market claims."""

import json
from datetime import datetime
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[1]


def read_json(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_report_schema_is_valid():
    schema = read_json("schemas/research_report.schema.json")
    jsonschema.Draft202012Validator.check_schema(schema)


def test_illustrative_report_matches_schema():
    schema = read_json("schemas/research_report.schema.json")
    report = read_json("examples/illustrative_report.json")
    jsonschema.validate(instance=report, schema=schema)
    assert report["data_status"]["is_illustrative"] is True
    assert report["summary"]["stance"] == "undetermined"
    assert {s["name"] for s in report["scenarios"]} == {"bull", "base", "bear"}


def test_illustrative_report_has_no_dangling_citations():
    report = read_json("examples/illustrative_report.json")
    known = {source["id"] for source in report["sources"]}
    referenced = set()
    for thesis in report["theses"]:
        referenced.update(thesis["evidence_ids"])
    for metric in report["metrics"]:
        referenced.update(metric["evidence_ids"])
    for item in report["risk_flags"]:
        referenced.update(item["evidence_ids"])
    for item in report["monitoring"]:
        referenced.update(item["source_ids"])
    assert referenced <= known


def test_no_future_sources_in_fixture():
    report = read_json("examples/illustrative_report.json")
    as_of = datetime.fromisoformat(report["as_of"].replace("Z", "+00:00"))
    for source in report["sources"]:
        if source["published_at"]:
            published_at = datetime.fromisoformat(source["published_at"].replace("Z", "+00:00"))
            assert published_at <= as_of


def test_golden_cases_are_well_formed():
    lines = (ROOT / "tests/golden_cases.jsonl").read_text(encoding="utf-8").splitlines()
    cases = [json.loads(line) for line in lines if line.strip()]
    assert len(cases) >= 10
    assert len({case["id"] for case in cases}) == len(cases)
    for case in cases:
        assert case["input"] and case["must"] and case["must_not"]
