"""Gate 5 archive manifest validation: source maps and real environments.

The manifest checker's case-level validations run against a synthetic matrix
tree: each case builds the smallest archive the checker reads, applies one
defect and asserts the checker reports it, so the rejection reasons stay
repeatable without rerunning gem5.
"""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

WORKTREE = Path(__file__).resolve().parents[4]
CHECKER = WORKTREE / ".tmp/docs/torch-gate-5-implementation/check_manifest.py"

_spec = importlib.util.spec_from_file_location("gate5_check_manifest", CHECKER)
check_manifest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_manifest)

BINARY = {"path": "build/AXI_MESH/gem5.opt", "sha256": "b" * 64}
CONFIG = {
    "path": "configs/example/ai_mesh/run_mesh_dma_garnet.py",
    "sha256": "c" * 64,
}


def write_source_map(tmp_path, mapping):
    path = tmp_path / "changed-sources.json"
    path.write_text(json.dumps(mapping, indent=1, sort_keys=True) + "\n")
    return {
        "path": str(path),
        "count": len(mapping),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def write_case(matrix, name, identity, environment):
    case = matrix / name
    case.mkdir(parents=True)
    (case / "identity.json").write_text(json.dumps(identity, indent=1))
    (case / "command.json").write_text(
        json.dumps({"environment": environment}, indent=1))


def problems(tmp_path, identity, environment):
    matrix = tmp_path / "matrix"
    write_case(matrix, "carrier", identity, environment)
    state = {"checked": 0, "missing": [], "mismatch": []}
    check_manifest.source_map_problems(matrix, state)
    check_manifest.environment_problems(matrix, state)
    return state


def test_a_readable_source_map_and_real_environment_pass(tmp_path):
    mapping = {BINARY["path"]: BINARY["sha256"],
               CONFIG["path"]: CONFIG["sha256"]}
    reference = write_source_map(tmp_path, mapping)
    state = problems(
        tmp_path,
        {"binary": BINARY, "config": CONFIG, "changed_sources": reference},
        {"AI_MESH_ARTIFACT_DIR": str(tmp_path / "work")},
    )
    assert state["missing"] == [], state
    assert state["mismatch"] == [], state
    assert state["checked"] == 2, state


def test_a_case_without_a_source_map_reference_is_missing(tmp_path):
    state = problems(
        tmp_path, {"binary": BINARY, "config": CONFIG},
        {"AI_MESH_ARTIFACT_DIR": str(tmp_path / "work")},
    )
    assert ("case source map reference", "carrier") in state["missing"], state


@pytest.mark.parametrize(
    "defect, label",
    (
        ("digest", "case source map"),
        ("count", "case source map count"),
        ("identity", "case identity vs source map"),
    ),
)
def test_a_tampered_source_map_is_a_mismatch(tmp_path, defect, label):
    mapping = {BINARY["path"]: BINARY["sha256"],
               CONFIG["path"]: CONFIG["sha256"]}
    reference = write_source_map(tmp_path, dict(mapping))
    identity = {"binary": dict(BINARY), "config": dict(CONFIG),
                "changed_sources": dict(reference)}
    if defect == "digest":
        identity["changed_sources"]["sha256"] = "0" * 64
    elif defect == "count":
        identity["changed_sources"]["count"] = len(mapping) + 1
    else:
        identity["binary"]["sha256"] = "d" * 64
    state = problems(
        tmp_path, identity,
        {"AI_MESH_ARTIFACT_DIR": str(tmp_path / "work")},
    )
    assert [entry[:2] for entry in state["mismatch"]
            if entry[0] == label], state


@pytest.mark.parametrize(
    "environment, label",
    (
        ({"AI_MESH_ARTIFACT_DIR": "<per-case artifact dir>"},
         "case environment placeholder"),
        ({"PYTHONPATH": "util/mesh_ir:<locked interpreter site-packages>"},
         "case environment placeholder"),
        ({}, "case environment"),
        (None, "case environment"),
    ),
)
def test_an_undefined_environment_is_a_missing_measurement(
        tmp_path, environment, label):
    mapping = {BINARY["path"]: BINARY["sha256"]}
    reference = write_source_map(tmp_path, mapping)
    state = problems(
        tmp_path,
        {"binary": BINARY, "config": CONFIG, "changed_sources": reference},
        environment,
    )
    assert [entry for entry in state["missing"]
            if entry[0] == label], state
