"""Current-operation inventory plus immutable prior report/job checks."""

import json
from pathlib import Path
from context_adapters.news_refresh_v2 import check_scope as parent_check
from research.local_files import atomic_json
from research.sec_transport import now
from research.validation import digest


def inventory(root):
    root = Path(root)
    return {str(p.relative_to(root)).replace("\\", "/"): p for p in (root / "extensions").rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and p.suffix not in {".pyc", ".pyo"}}


def check(baseline, *, expected_sha256=None, replay=False):
    proof = parent_check(baseline, expected_sha256=expected_sha256, replay=replay)
    value = json.loads(Path(baseline).read_text(encoding="utf-8"))
    current = inventory(value["root"])
    unexpected = set(current) - set(value["files"]) - set(value.get("allowed_extensions", []))
    if unexpected:
        raise ValueError("UNDECLARED_EXTENSION_FILES: " + json.dumps(sorted(unexpected)))
    return {**proof, "extension_files": len(current)}


def freeze(baseline, output):
    check(baseline)
    value = json.loads(Path(baseline).read_text(encoding="utf-8"))
    value["files"].update({name: digest(p) for name, p in inventory(value["root"]).items()})
    value["allowed_extensions"] = []
    value["parent_baseline_sha256"] = digest(baseline)
    value["created_at"] = now()
    atomic_json(output, value)
    return check(output)
