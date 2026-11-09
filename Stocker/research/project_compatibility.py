"""Read-only project conflict checks against an explicit before-work baseline."""

import argparse
from pathlib import Path
import json
import uuid

from .sec_report import ROOT
from .sec_refresh import read
from .validation import digest
from .local_files import atomic_json
from .sec_transport import now


FOLDERS = ("research", "config", "schemas", "tests", "examples", "scripts", "docs")


def project_files(root):
    root = Path(root).resolve()
    result = {str(p.relative_to(root)).replace("\\", "/"): p for folder in FOLDERS for p in (root / folder).rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix not in {".pyc", ".pyo"}}
    result.update({name: root / name for name in ("README.md", "AGENTS.md", "pyproject.toml") if (root / name).is_file()})
    return result


def check(path, *, expected_sha256=None, replay=False):
    path = Path(path)
    if expected_sha256 and digest(path) != expected_sha256:
        raise ValueError("COMPATIBILITY_BASELINE_CHANGED")
    baseline = read(path)
    root = Path(baseline["root"]).resolve()
    if baseline["version"] != "1.0.0":
        raise ValueError("UNSUPPORTED_COMPATIBILITY_BASELINE")
    allowed = set(baseline["allowed_modified"])
    changed, missing = [], []
    for name, sha in baseline["files"].items():
        target = (root / name).resolve()
        if not target.is_relative_to(root):
            raise ValueError("COMPATIBILITY_PATH_ESCAPES_PROJECT")
        if not target.is_file():
            missing.append(name)
        elif digest(target) != sha and name not in allowed:
            changed.append(name)
    unexpected = sorted(set(project_files(root)) - set(baseline["files"]) - set(baseline["allowed_new"]))
    if changed or missing or unexpected:
        raise ValueError("PROJECT_CONFLICT: " + json.dumps({"changed": sorted(changed), "missing": sorted(missing), "unexpected_new": unexpected}))
    result = {"status": "compatible", "baseline_sha256": digest(path), "protected_files": len(baseline["files"]),
        "new_files": len(set(project_files(root)) - set(baseline["files"])), "jobs_verified": 0, "reports_replayed": 0}
    if replay:
        if root != ROOT:
            raise ValueError("REPLAY_REQUIRES_CURRENT_PROJECT_ROOT")
        from . import observe, panel_job, sec_refresh, sec_financial_report, sec_growth_report, sec_chain_report, integrated_report
        validators = {"financial": sec_financial_report.validate, "growth": sec_growth_report.validate,
            "chain": sec_chain_report.validate, "integrated": integrated_report.validate_file}
        for job in baseline["jobs"]:
            p = Path(job)
            if "observation_jobs" in p.parts:
                observe.check_job(p)
            elif "historical_panel" in p.parts:
                panel_job.check(p)
            elif "sec_refresh_jobs" in p.parts:
                sec_refresh.check_job(p)
            else:
                raise ValueError("UNKNOWN_PINNED_JOB_KIND")
            result["jobs_verified"] += 1
        for report in baseline["reports"]:
            validators[report["kind"]](Path(report["path"]) if report["kind"] == "integrated" else read(Path(report["path"])))
            result["reports_replayed"] += 1
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--checkpoint")
    parser.add_argument("--freeze", type=Path)
    args = parser.parse_args(argv)
    result = check(args.baseline, replay=args.replay)
    if args.checkpoint:
        target = args.baseline.parent / "checkpoints" / (uuid.uuid4().hex + ".json")
        atomic_json(target, {"label": args.checkpoint, "checked_at": now(), "result": result})
        result["checkpoint_path"] = str(target.resolve())
    if args.freeze:
        result["frozen_baseline_path"] = str(freeze(args.baseline, args.freeze).resolve())
    print(json.dumps(result))
    return 0


def freeze(path, output):
    """Close verified development scope and pin its newly introduced code."""
    check(path)
    value = read(path)
    value["created_at"] = now()
    value["parent_baseline_sha256"] = digest(path)
    value["files"] = {**value["files"], **{name: digest(p) for name, p in project_files(value["root"]).items()}}
    value["allowed_modified"] = sorted(set(value["allowed_modified"]) | {"docs/CONTEXT_EXPANSION.md"})
    value["allowed_new"] = []
    atomic_json(output, value)
    check(output)
    return Path(output)


if __name__ == "__main__":
    raise SystemExit(main())
