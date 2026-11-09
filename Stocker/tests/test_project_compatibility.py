"""ILLUSTRATIVE — NOT LIVE MARKET DATA. Before-work conflict checks."""

from pathlib import Path
import json

import pytest

from research import project_compatibility as guard
from research.local_files import atomic_json
from research.validation import digest


@pytest.fixture
def case(tmp_path):
    code = tmp_path / "research/engine.py"
    code.parent.mkdir()
    code.write_text("original", encoding="utf-8")
    doc = tmp_path / "README.md"
    doc.write_text("original documentation", encoding="utf-8")
    path = tmp_path / "baseline.json"
    atomic_json(path, {"version": "1.0.0", "root": str(tmp_path), "files": {"research/engine.py": digest(code), "README.md": digest(doc)},
        "allowed_modified": ["README.md"], "allowed_new": ["research/new.py"], "jobs": [], "reports": []})
    return path, code, doc


def test_original_files_and_owned_additions_are_compatible(case):
    path, code, _ = case
    code.with_name("new.py").write_text("new module", encoding="utf-8")
    result = guard.check(path)
    assert result["status"] == "compatible" and result["protected_files"] == 2 and result["new_files"] == 1


def test_changed_calculation_is_a_conflict(case):
    path, code, _ = case
    code.write_text("changed formula", encoding="utf-8")
    with pytest.raises(ValueError, match="PROJECT_CONFLICT.*engine.py"):
        guard.check(path)


def test_missing_dependency_is_a_conflict(case):
    path, code, _ = case
    code.unlink()
    with pytest.raises(ValueError, match="PROJECT_CONFLICT.*missing"):
        guard.check(path)


def test_new_file_outside_declared_scope_is_a_conflict(case):
    path, code, _ = case
    code.with_name("unplanned.py").write_text("new", encoding="utf-8")
    with pytest.raises(ValueError, match="unexpected_new.*unplanned.py"):
        guard.check(path)


def test_allowed_documentation_change_preserves_calculation_guard(case):
    path, _, doc = case
    doc.write_text("updated documentation", encoding="utf-8")
    assert guard.check(path)["status"] == "compatible"


def test_baseline_change_is_detected_even_if_file_hashes_still_match(case):
    path, _, _ = case
    original = digest(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["allowed_new"].append("research/anything.py")
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="BASELINE_CHANGED"):
        guard.check(path, expected_sha256=original)


def test_baseline_paths_cannot_escape_workspace(case):
    path, _, _ = case
    value = json.loads(path.read_text(encoding="utf-8"))
    value["files"]["../escape.py"] = "0" * 64
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="ESCAPES_PROJECT"):
        guard.check(path)


def test_checkpoint_records_check_and_does_not_modify_originals(case, capsys):
    path, code, doc = case
    before = [digest(p) for p in (path, code, doc)]
    assert guard.main(["--baseline", str(path), "--checkpoint", "before_refresh"]) == 0
    result = json.loads(capsys.readouterr().out)
    checkpoint = json.loads(Path(result["checkpoint_path"]).read_text(encoding="utf-8"))
    assert checkpoint["label"] == "before_refresh" and checkpoint["result"]["status"] == "compatible"
    assert [digest(p) for p in (path, code, doc)] == before


def test_freezing_pins_new_code_and_preserves_original_baseline(case):
    path, code, _ = case
    code.with_name("new.py").write_text("verified new code", encoding="utf-8")
    original = digest(path)
    frozen = guard.freeze(path, path.with_name("runtime_baseline.json"))
    assert digest(path) == original
    assert guard.check(frozen)["new_files"] == 0
    code.with_name("new.py").write_text("changed after release", encoding="utf-8")
    with pytest.raises(ValueError, match="PROJECT_CONFLICT.*new.py"):
        guard.check(frozen)
