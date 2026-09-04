import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from mesh_ir import acceptance
from mesh_ir.acceptance import (
    ContractError,
    build_dma_traffic,
    validate_invariants,
    validate_traffic,
    write_success_artifacts,
)


REPO = Path(__file__).resolve().parents[4]
HARNESS_DIR = REPO / "tests/gem5/ai_mesh"
MANIFEST_PATH = HARNESS_DIR / "mandatory_case_manifest.yaml"
SELECTOR_PATH = HARNESS_DIR / "run_manifest_selector.py"
SELECTOR_SPEC = importlib.util.spec_from_file_location(
    "ai_mesh_spec_selector", SELECTOR_PATH
)
SELECTOR = importlib.util.module_from_spec(SELECTOR_SPEC)
SELECTOR_SPEC.loader.exec_module(SELECTOR)

LEDGERS = {
    "live_cq_obligations": 0,
    "fatal_cq_obligations": 0,
    "fatal_sq_intakes": 0,
    "fatal_publications": 0,
    "ambiguous_publications": 0,
    "host_ack_wait_b": 0,
    "host_ack_b_error": 0,
    "msi_rob_entries": 0,
    "fatal_records": 0,
}


def pytest_subcase(name="contract"):
    return {
        "name": name,
        "terminal_class": "QUIESCENT_SUCCESS",
        "expected_exit_reason": "QUIESCENT_SUCCESS",
        "expected_first_fatal": None,
        "expected_ledgers": dict(LEDGERS),
        "timeout": {"sim_ticks": None, "wall_seconds": 5},
        "artifacts": [
            "SUMMARY_JSON",
            "JUNIT_XML",
            "RUN_MANIFEST",
            "INVARIANTS_JSON",
        ],
        "execution": {
            "runner": "PYTEST",
            "node_id": (
                "util/mesh_ir/tests/unit/test_abi.py::"
                "test_opcode_closed_set_and_engine_mapping"
            ),
        },
    }


def test_checked_in_manifest_uses_exact_spec_shapes():
    document = yaml.safe_load(MANIFEST_PATH.read_text())
    for case in document["cases"]:
        for subcase in case["subcases"]:
            assert isinstance(subcase["execution"], dict)
            assert isinstance(subcase["timeout"], dict)
            assert set(subcase["expected_ledgers"]) == set(LEDGERS)
            assert subcase["artifacts"][:4] == [
                "SUMMARY_JSON",
                "JUNIT_XML",
                "RUN_MANIFEST",
                "INVARIANTS_JSON",
            ]


def test_manifest_schema_rejects_unknown_nested_field(tmp_path, monkeypatch):
    document = yaml.safe_load(MANIFEST_PATH.read_text())
    document = copy.deepcopy(document)
    document["cases"][0]["subcases"][0]["unexpected"] = 1
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False))
    monkeypatch.setattr(SELECTOR, "MANIFEST", path)
    with pytest.raises(Exception):
        SELECTOR.load_manifest()


def test_manifest_loader_rejects_duplicate_yaml_keys(tmp_path, monkeypatch):
    path = tmp_path / "manifest.yaml"
    path.write_text(
        "schema: ai_mesh_mandatory_manifest_v1\n"
        "schema: ai_mesh_mandatory_manifest_v1\n"
        "version: 1\n"
        "cases: []\n"
    )
    monkeypatch.setattr(SELECTOR, "MANIFEST", path)
    with pytest.raises(ContractError, match="duplicate YAML key"):
        SELECTOR.load_manifest()


def test_manifest_loader_normalizes_yaml_parser_failures(tmp_path, monkeypatch):
    path = tmp_path / "manifest.yaml"
    path.write_text("schema: [\n")
    monkeypatch.setattr(SELECTOR, "MANIFEST", path)
    with pytest.raises(ContractError, match="invalid YAML"):
        SELECTOR.load_manifest()


def test_nonempty_stdout_never_synthesizes_child_report(tmp_path, monkeypatch):
    result = subprocess.CompletedProcess([], 0, "test output\n", "")
    monkeypatch.setattr(
        SELECTOR,
        "run_once",
        lambda *args, **kwargs: (result, 0.0, None),
    )
    golden = tmp_path / "golden"
    golden.mkdir()
    accepted, detail, artifact_dir = SELECTOR.run_subcase(
        "DC-1", pytest_subcase(), golden, tmp_path / "results"
    )
    assert not accepted
    assert "child report" in detail.lower()
    assert not (artifact_dir / "child_report.json").exists()


def test_incomplete_child_report_is_rejected(tmp_path, monkeypatch):
    result = subprocess.CompletedProcess([], 0, "", "")

    def run_once(command, cwd, env, *args, **kwargs):
        Path(env["AI_MESH_CHILD_REPORT"]).write_text(
            json.dumps(
                {
                    "schema": "ai_mesh_child_scenario_report_v1",
                    "version": 1,
                    "id": "DC-1",
                    "subcase": "contract",
                    "run_exit_reason": "QUIESCENT_SUCCESS",
                    "first_fatal": None,
                }
            )
        )
        return result, 0.0, None

    monkeypatch.setattr(SELECTOR, "run_once", run_once)
    golden = tmp_path / "golden"
    golden.mkdir()
    accepted, detail, _ = SELECTOR.run_subcase(
        "DC-1", pytest_subcase(), golden, tmp_path / "results"
    )
    assert not accepted
    assert "child report" in detail.lower()


def test_pytest_execution_is_direct_argv(tmp_path, monkeypatch):
    captured = {}
    result = subprocess.CompletedProcess([], 0, "", "")

    def run_once(command, cwd, env, *args, **kwargs):
        captured["command"] = command
        return result, 0.0, None

    monkeypatch.setattr(SELECTOR, "run_once", run_once)
    golden = tmp_path / "golden"
    golden.mkdir()
    SELECTOR.run_subcase(
        "DC-1", pytest_subcase(), golden, tmp_path / "results"
    )
    assert captured["command"] == [
        str(Path(SELECTOR.sys.executable).resolve()),
        "-m",
        "pytest",
        "util/mesh_ir/tests/unit/test_abi.py::test_opcode_closed_set_and_engine_mapping",
        "-q",
        "--maxfail=1",
        "--disable-warnings",
    ]


def test_pytest_resolution_uses_selected_runtime_and_fixed_environment(
    tmp_path, monkeypatch
):
    executable = tmp_path / "python"
    executable.write_bytes(b"runtime")
    node_id = "tests/example.py::test_exact"
    captured = {}

    def run(command, **kwargs):
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        return subprocess.CompletedProcess(command, 0, node_id + "\n", "")

    SELECTOR._validate_pytest_node.cache_clear()
    monkeypatch.setattr(SELECTOR.subprocess, "run", run)
    SELECTOR._validate_pytest_node(node_id, executable)
    assert captured["command"][0] == str(executable)
    assert captured["environment"] == acceptance.CHILD_ENV_BASE


def test_all_acceptance_output_schemas_are_checked_in():
    names = {
        "mandatory_manifest_v1.schema.json",
        "child_scenario_report_v1.schema.json",
        "run_summary_v1.schema.json",
        "run_manifest_v1.schema.json",
        "invariants_v1.schema.json",
        "traffic_v1.schema.json",
        "fatal_snapshot_v1.schema.json",
        "mandatory_results_v1.schema.json",
    }
    schema_dir = REPO / "schemas/ai_mesh"
    assert names <= {path.name for path in schema_dir.iterdir()}


def test_checked_in_manifest_is_the_only_manifest_authority():
    validator = HARNESS_DIR / "validate_manifest.py"
    assert validator.is_file()
    assert not (HARNESS_DIR / "generate_manifest.py").exists()
    result = subprocess.run(
        [str(Path(SELECTOR.sys.executable).resolve()), str(validator)],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "150 logical IDs" in result.stdout


def test_zero_byte_traffic_retains_applicable_classes():
    expected = [
        {"descriptor_id": 1, "kind": 1, "useful_bytes": 0, "bursts": 0},
        {"descriptor_id": 2, "kind": 3, "useful_bytes": 0, "bursts": 0},
    ]
    actual = [
        {
            "descriptor_id": descriptor_id,
            "read_bytes": 0,
            "read_discarded_bytes": 0,
            "write_bytes": 0,
            "write_drained_uncommitted_bytes": 0,
            "p2p_bytes": 0,
            "fill_bytes": 0,
            "read_bursts": 0,
            "write_bursts": 0,
            "p2p_bursts": 0,
        }
        for descriptor_id in (1, 2)
    ]
    traffic = build_dma_traffic("DC-17", "zero", expected, actual, 1)
    assert [row["traffic_class"] for row in traffic["classes"]] == [
        "NPU_LOCAL_MEMORY",
        "NPU_P2P",
    ]


def test_traffic_digests_are_recomputed_from_rows():
    expected = [
        {"descriptor_id": 1, "kind": 1, "useful_bytes": 64, "bursts": 1}
    ]
    actual = [
        {
            "descriptor_id": 1,
            "read_bytes": 64,
            "read_discarded_bytes": 0,
            "read_bursts": 1,
        }
    ]
    traffic = build_dma_traffic("DC-17", "digest", expected, actual, 1)
    traffic["oracle_digest"] = "0" * 64
    traffic["actual_digest"] = "0" * 64
    with pytest.raises(ContractError):
        validate_traffic(traffic, "DC-17", "digest")


def test_invariant_registry_digest_is_recomputed():
    document = {
        "schema": "ai_mesh_invariants_v1",
        "version": 1,
        "id": "DC-1",
        "subcase": "registry",
        "status": "PASS",
        "registry_digest": "0" * 64,
        "checks": [
            {
                "name": "unit_test_passed",
                "status": "PASS",
                "expected": {"kind": "BOOL", "value": True},
                "observed": {"kind": "BOOL", "value": True},
            }
        ],
        "first_failure": None,
    }
    with pytest.raises(ContractError):
        validate_invariants(document, "DC-1", "registry")


def _write_valid_unit_artifacts(environment, monkeypatch):
    with monkeypatch.context() as context:
        for name, value in environment.items():
            context.setenv(name, value)
        write_success_artifacts(["unit_test_passed"])


def test_child_report_symlink_is_artifact_error(tmp_path, monkeypatch):
    result = subprocess.CompletedProcess([], 0, "", "")

    def run_once(command, cwd, env, timeout):
        _write_valid_unit_artifacts(env, monkeypatch)
        report = Path(env["AI_MESH_CHILD_REPORT"])
        target = report.with_name("child_report.real.json")
        report.rename(target)
        report.symlink_to(target.name)
        return result, 0.0, None

    monkeypatch.setattr(SELECTOR, "run_once", run_once)
    golden = tmp_path / "golden"
    golden.mkdir()
    accepted, detail, _ = SELECTOR.run_subcase(
        "DC-1", pytest_subcase("report_link"), golden, tmp_path / "results"
    )
    assert not accepted
    assert detail == "artifact error"


def test_symlink_artifact_is_not_reported_available(tmp_path, monkeypatch):
    result = subprocess.CompletedProcess([], 0, "", "")

    def run_once(command, cwd, env, timeout):
        _write_valid_unit_artifacts(env, monkeypatch)
        artifact_dir = Path(env["AI_MESH_ARTIFACT_DIR"])
        invariants = artifact_dir / "invariants.json"
        target = artifact_dir / "invariants.real.json"
        invariants.rename(target)
        invariants.symlink_to(target.name)
        return result, 0.0, None

    monkeypatch.setattr(SELECTOR, "run_once", run_once)
    golden = tmp_path / "golden"
    golden.mkdir()
    accepted, detail, artifact_dir = SELECTOR.run_subcase(
        "DC-1", pytest_subcase("artifact_link"), golden, tmp_path / "results"
    )
    assert not accepted
    assert "regular artifact" in detail
    summary = json.loads((artifact_dir / "summary.json").read_text())
    row = next(
        row for row in summary["artifact_paths"] if row["kind"] == "INVARIANTS_JSON"
    )
    assert row == {"kind": "INVARIANTS_JSON", "state": "MISSING", "path": None}


def test_selector_rejects_unregistered_invariant_set(tmp_path, monkeypatch):
    result = subprocess.CompletedProcess([], 0, "", "")

    def run_once(command, cwd, env, timeout):
        with monkeypatch.context() as context:
            for name, value in env.items():
                context.setenv(name, value)
            write_success_artifacts(["different_check"])
        return result, 0.0, None

    monkeypatch.setattr(SELECTOR, "run_once", run_once)
    golden = tmp_path / "golden"
    golden.mkdir()
    accepted, detail, _ = SELECTOR.run_subcase(
        "DC-1", pytest_subcase("wrong_registry"), golden, tmp_path / "results"
    )
    assert not accepted
    assert detail == "invariant registry does not match selected execution"


def test_gate12_approximation_contract_keeps_remote_proxy_explicit():
    contract = json.loads(
        (REPO / "schemas/ai_mesh/acceptance_contract_v1.schema.json").read_text()
    )
    field = contract["$defs"]["approximation"]["properties"][
        "remote_link_is_analytic_proxy"
    ]
    assert field == {"const": True}


def test_run_summary_rejects_incoherent_artifact_state():
    subcase = pytest_subcase("summary")
    summary = {
        "schema": "ai_mesh_run_summary_v1",
        "version": 1,
        "id": "DC-1",
        "subcase": "summary",
        "status": "FAIL",
        "terminal_class": "QUIESCENT_SUCCESS",
        "run_exit_reason": "HARNESS_FAILURE",
        "process_exit_code": 0,
        "child_termination": {"kind": "EXIT", "exit_code": 0, "signal": None},
        "harness_failure_kind": "MISSING_CHILD_REPORT",
        "first_fatal": None,
        "watchdog_fired": None,
        "ledger_summary": None,
        "global_quiescence": None,
        "run_manifest_digest": None,
        "normalized_execution_digest": "0" * 64,
        "artifact_paths": [
            {"kind": "SUMMARY_JSON", "state": "AVAILABLE", "path": "summary.json"},
            {"kind": "JUNIT_XML", "state": "AVAILABLE", "path": "junit.xml"},
            {
                "kind": "RUN_MANIFEST",
                "state": "AVAILABLE",
                "path": "run_manifest.json",
            },
            {
                "kind": "INVARIANTS_JSON",
                "state": "MISSING",
                "path": "invariants.json",
            },
        ],
    }
    with pytest.raises(ContractError):
        acceptance.validate_run_summary(summary, subcase)


def test_run_summary_rejects_available_artifact_that_is_not_regular(tmp_path):
    subcase = pytest_subcase("summary_files")
    summary = {
        "schema": "ai_mesh_run_summary_v1",
        "version": 1,
        "id": "DC-1",
        "subcase": "summary_files",
        "status": "FAIL",
        "terminal_class": "QUIESCENT_SUCCESS",
        "run_exit_reason": "HARNESS_FAILURE",
        "process_exit_code": 0,
        "child_termination": {"kind": "EXIT", "exit_code": 0, "signal": None},
        "harness_failure_kind": "MISSING_CHILD_REPORT",
        "first_fatal": None,
        "watchdog_fired": None,
        "ledger_summary": None,
        "global_quiescence": None,
        "run_manifest_digest": None,
        "normalized_execution_digest": "0" * 64,
        "artifact_paths": [
            {"kind": kind, "state": "AVAILABLE", "path": basename}
            for kind, basename in (
                ("SUMMARY_JSON", "summary.json"),
                ("JUNIT_XML", "junit.xml"),
                ("RUN_MANIFEST", "run_manifest.json"),
                ("INVARIANTS_JSON", "invariants.json"),
            )
        ],
    }
    with pytest.raises(ContractError, match="not a regular file"):
        acceptance.validate_run_summary(summary, subcase, tmp_path)


def test_run_summary_rejects_process_exit_code_that_disagrees_with_reason():
    subcase = pytest_subcase("exit_abi")
    summary = {
        "schema": "ai_mesh_run_summary_v1",
        "version": 1,
        "id": "DC-1",
        "subcase": "exit_abi",
        "status": "PASS",
        "terminal_class": "QUIESCENT_SUCCESS",
        "run_exit_reason": "QUIESCENT_SUCCESS",
        "process_exit_code": 20,
        "child_termination": {"kind": "EXIT", "exit_code": 20, "signal": None},
        "harness_failure_kind": None,
        "first_fatal": None,
        "watchdog_fired": False,
        "ledger_summary": dict(LEDGERS),
        "global_quiescence": True,
        "run_manifest_digest": "0" * 64,
        "normalized_execution_digest": "0" * 64,
        "artifact_paths": [
            {"kind": kind, "state": "AVAILABLE", "path": basename}
            for kind, basename in (
                ("SUMMARY_JSON", "summary.json"),
                ("JUNIT_XML", "junit.xml"),
                ("RUN_MANIFEST", "run_manifest.json"),
                ("INVARIANTS_JSON", "invariants.json"),
            )
        ],
    }
    with pytest.raises(ContractError, match="exit ABI"):
        acceptance.validate_run_summary(summary, subcase)


def test_run_summary_rejects_scenario_fields_for_harness_failure():
    subcase = pytest_subcase("harness_fields")
    summary = {
        "schema": "ai_mesh_run_summary_v1",
        "version": 1,
        "id": "DC-1",
        "subcase": "harness_fields",
        "status": "FAIL",
        "terminal_class": "QUIESCENT_SUCCESS",
        "run_exit_reason": "HARNESS_FAILURE",
        "process_exit_code": None,
        "child_termination": {"kind": "SIGNAL", "exit_code": None, "signal": 9},
        "harness_failure_kind": "SIGNAL",
        "first_fatal": None,
        "watchdog_fired": False,
        "ledger_summary": dict(LEDGERS),
        "global_quiescence": True,
        "run_manifest_digest": "0" * 64,
        "normalized_execution_digest": "0" * 64,
        "artifact_paths": [
            {"kind": kind, "state": "AVAILABLE", "path": basename}
            for kind, basename in (
                ("SUMMARY_JSON", "summary.json"),
                ("JUNIT_XML", "junit.xml"),
                ("RUN_MANIFEST", "run_manifest.json"),
                ("INVARIANTS_JSON", "invariants.json"),
            )
        ],
    }
    with pytest.raises(ContractError, match="scenario fields"):
        acceptance.validate_run_summary(summary, subcase)


def test_signal_discards_report_written_before_abnormal_termination(
    tmp_path, monkeypatch
):
    result = subprocess.CompletedProcess([], -9, "", "")

    def run_once(command, cwd, env, timeout):
        _write_valid_unit_artifacts(env, monkeypatch)
        return result, 0.0, None

    monkeypatch.setattr(SELECTOR, "run_once", run_once)
    golden = tmp_path / "golden"
    golden.mkdir()
    accepted, detail, artifact_dir = SELECTOR.run_subcase(
        "DC-1", pytest_subcase("signal"), golden, tmp_path / "results"
    )
    assert not accepted
    assert detail == "signal"
    summary = json.loads((artifact_dir / "summary.json").read_text())
    assert summary["run_exit_reason"] == "HARNESS_FAILURE"
    assert summary["watchdog_fired"] is None
    assert summary["ledger_summary"] is None
    assert summary["global_quiescence"] is None
    assert summary["run_manifest_digest"] is None


def test_run_subcase_revalidates_summary_after_outer_artifacts_exist(
    tmp_path, monkeypatch
):
    result = subprocess.CompletedProcess([], 0, "", "")

    def run_once(command, cwd, env, timeout):
        _write_valid_unit_artifacts(env, monkeypatch)
        return result, 0.0, None

    calls = []
    original = SELECTOR.validate_run_summary

    def validate(summary, subcase, root=None):
        calls.append(root)
        if root is not None:
            assert (root / "summary.json").is_file()
            assert (root / "junit.xml").is_file()
        return original(summary, subcase, root)

    monkeypatch.setattr(SELECTOR, "run_once", run_once)
    monkeypatch.setattr(SELECTOR, "validate_run_summary", validate)
    golden = tmp_path / "golden"
    golden.mkdir()
    accepted, detail, artifact_dir = SELECTOR.run_subcase(
        "DC-1", pytest_subcase("post_write"), golden, tmp_path / "results"
    )
    assert accepted, detail
    assert calls == [None, artifact_dir]


def test_wall_timeout_stops_descendant_processes(tmp_path):
    marker = tmp_path / "descendant-finished"
    descendant = (
        "import pathlib,time;"
        "time.sleep(0.5);"
        f"pathlib.Path({str(marker)!r}).write_text('finished')"
    )
    parent = (
        "import subprocess,sys,time;"
        f"subprocess.Popen([sys.executable,'-c',{descendant!r}]);"
        "time.sleep(60)"
    )
    unused_result, unused_elapsed, failure = SELECTOR.run_once(
        [sys.executable, "-c", parent], tmp_path, {}, 0.1
    )
    SELECTOR.time.sleep(0.7)
    assert failure == "WALL_TIMEOUT"
    assert not marker.exists()


def test_child_output_bytes_do_not_abort_the_outer_harness(tmp_path):
    result, unused_elapsed, failure = SELECTOR.run_once(
        [sys.executable, "-c", "import os;os.write(1,b'\\xff')"],
        tmp_path,
        {},
        1,
    )
    assert failure is None
    assert result.returncode == 0
    assert result.stdout == "\ufffd"


def test_mandatory_results_are_recounted_against_selection():
    manifest = SELECTOR.load_manifest()
    selected = [next(case for case in manifest["cases"] if case["id"] == "DC-1")]
    results = {
        "schema": "ai_mesh_mandatory_results_v1",
        "version": 1,
        "manifest_digest": acceptance.canonical_digest(manifest),
        "gate": 1,
        "selection": {"mode": "EXPLICIT_IDS", "ids": ["DC-1"]},
        "selected_logical_count": 1,
        "selected_subcase_count": 1,
        "cases": [
            {
                "id": "DC-1",
                "status": "PASS",
                "subcases": [
                    {
                        "name": subcase["name"],
                        "status": "PASS",
                        "summary_path": f"DC-1/{subcase['name']}/summary.json",
                        "junit_testcase_name": f"DC-1/{subcase['name']}",
                    }
                    for subcase in selected[0]["subcases"]
                ],
            }
        ],
        "logical_pass_count": 1,
        "logical_fail_count": 0,
        "subcase_pass_count": 1,
        "subcase_fail_count": 0,
        "junit_path": "junit_gate1.xml",
    }
    with pytest.raises(ContractError):
        acceptance.validate_results(results, manifest, selected)


def test_mandatory_results_require_each_referenced_summary(tmp_path):
    manifest = SELECTOR.load_manifest()
    selected = [next(case for case in manifest["cases"] if case["id"] == "DC-1")]
    subcases = [
        {
            "name": subcase["name"],
            "status": "PASS",
            "summary_path": f"DC-1/{subcase['name']}/summary.json",
            "junit_testcase_name": f"DC-1/{subcase['name']}",
        }
        for subcase in selected[0]["subcases"]
    ]
    results = {
        "schema": "ai_mesh_mandatory_results_v1",
        "version": 1,
        "manifest_digest": acceptance.canonical_digest(manifest),
        "gate": 1,
        "selection": {"mode": "EXPLICIT_IDS", "ids": ["DC-1"]},
        "selected_logical_count": 1,
        "selected_subcase_count": len(subcases),
        "cases": [{"id": "DC-1", "status": "PASS", "subcases": subcases}],
        "logical_pass_count": 1,
        "logical_fail_count": 0,
        "subcase_pass_count": len(subcases),
        "subcase_fail_count": 0,
        "junit_path": "junit_gate1.xml",
    }
    with pytest.raises(ContractError, match="regular artifact"):
        acceptance.validate_results(results, manifest, selected, tmp_path)


def test_failed_results_remain_aggregatable_with_missing_child_artifacts(tmp_path):
    manifest = SELECTOR.load_manifest()
    selected = [next(case for case in manifest["cases"] if case["id"] == "DC-1")]
    result_subcases = []
    for subcase in selected[0]["subcases"]:
        name = subcase["name"]
        artifact_dir = tmp_path / "DC-1" / name
        artifact_dir.mkdir(parents=True)
        summary = {
            "schema": "ai_mesh_run_summary_v1",
            "version": 1,
            "id": "DC-1",
            "subcase": name,
            "status": "FAIL",
            "terminal_class": subcase["terminal_class"],
            "run_exit_reason": "HARNESS_FAILURE",
            "process_exit_code": None,
            "child_termination": {
                "kind": "SPAWN_ERROR",
                "exit_code": None,
                "signal": None,
            },
            "harness_failure_kind": "SPAWN_ERROR",
            "first_fatal": None,
            "watchdog_fired": None,
            "ledger_summary": None,
            "global_quiescence": None,
            "run_manifest_digest": None,
            "normalized_execution_digest": "0" * 64,
            "artifact_paths": [
                {
                    "kind": kind,
                    "state": (
                        "AVAILABLE"
                        if kind in ("SUMMARY_JSON", "JUNIT_XML")
                        else "MISSING"
                    ),
                    "path": (
                        basename
                        if kind in ("SUMMARY_JSON", "JUNIT_XML")
                        else None
                    ),
                }
                for kind, basename in (
                    ("SUMMARY_JSON", "summary.json"),
                    ("JUNIT_XML", "junit.xml"),
                    ("RUN_MANIFEST", "run_manifest.json"),
                    ("INVARIANTS_JSON", "invariants.json"),
                )
            ],
        }
        acceptance.atomic_write_json(artifact_dir / "summary.json", summary)
        SELECTOR._write_single_junit("DC-1", name, "FAIL", artifact_dir / "junit.xml")
        result_subcases.append(
            {
                "name": name,
                "status": "FAIL",
                "summary_path": f"DC-1/{name}/summary.json",
                "junit_testcase_name": f"DC-1/{name}",
            }
        )
    results = {
        "schema": "ai_mesh_mandatory_results_v1",
        "version": 1,
        "manifest_digest": acceptance.canonical_digest(manifest),
        "gate": 1,
        "selection": {"mode": "EXPLICIT_IDS", "ids": ["DC-1"]},
        "selected_logical_count": 1,
        "selected_subcase_count": len(result_subcases),
        "cases": [
            {"id": "DC-1", "status": "FAIL", "subcases": result_subcases}
        ],
        "logical_pass_count": 0,
        "logical_fail_count": 1,
        "subcase_pass_count": 0,
        "subcase_fail_count": len(result_subcases),
        "junit_path": "junit_gate1.xml",
    }
    acceptance.validate_results(results, manifest, selected, tmp_path)


def test_junit_must_be_bijective_with_selected_subcases(tmp_path):
    path = tmp_path / "junit.xml"
    path.write_text(
        '<testsuites tests="1" failures="0">'
        '<testsuite name="DC-1" tests="1" failures="0">'
        '<testcase classname="DC-1" name="DC-1/wrong"/>'
        "</testsuite></testsuites>"
    )
    with pytest.raises(ContractError):
        acceptance.validate_junit(
            path,
            tmp_path,
            [("DC-1", "decode_closed_set", "PASS")],
        )


def test_run_manifest_runner_and_scenario_are_one_union(tmp_path, monkeypatch):
    result = subprocess.CompletedProcess([], 0, "", "")

    def run_once(command, cwd, env, timeout):
        _write_valid_unit_artifacts(env, monkeypatch)
        return result, 0.0, None

    monkeypatch.setattr(SELECTOR, "run_once", run_once)
    golden = tmp_path / "golden"
    golden.mkdir()
    accepted, detail, artifact_dir = SELECTOR.run_subcase(
        "DC-1", pytest_subcase("manifest_union"), golden, tmp_path / "results"
    )
    assert accepted, detail
    document = json.loads((artifact_dir / "run_manifest.json").read_text())
    document["execution"]["runner"] = "GEM5"
    with pytest.raises(ContractError):
        acceptance.validate_run_manifest(document)


def test_pytest_xpass_does_not_emit_success_report(monkeypatch):
    path = REPO / "util/mesh_ir/tests/conftest.py"
    specification = importlib.util.spec_from_file_location(
        "ai_mesh_acceptance_conftest", path
    )
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    emitted = []
    monkeypatch.setattr(
        module,
        "write_success_artifacts",
        lambda names: emitted.append(names),
    )
    monkeypatch.setenv("AI_MESH_CHILD_REPORT", "/tmp/unused-child-report.json")
    report = type(
        "Report",
        (),
        {"when": "call", "outcome": "passed", "wasxfail": "expected"},
    )()
    module.pytest_sessionstart(None)
    module.pytest_runtest_logreport(report)
    module.pytest_sessionfinish(None, 0)
    assert emitted == []


def test_unit_only_selection_does_not_build_unrelated_goldens(tmp_path):
    manifest = SELECTOR.load_manifest()
    selected = [next(case for case in manifest["cases"] if case["id"] == "DC-2")]
    golden = SELECTOR.prepare_golden_artifacts(tmp_path, selected)
    assert golden.is_dir()
    assert list(golden.iterdir()) == []


def test_run_manifest_program_identity_comes_from_loaded_binary(tmp_path):
    manifest = SELECTOR.load_manifest()
    selected = [next(case for case in manifest["cases"] if case["id"] == "DC-3")]
    selected[0]["subcases"] = [
        next(
            subcase
            for subcase in selected[0]["subcases"]
            if subcase["execution"]["runner"] == "GEM5"
        )
    ]
    golden = SELECTOR.prepare_golden_artifacts(tmp_path, selected)
    execution = selected[0]["subcases"][0]["execution"]
    program_dir = golden / "dual"
    helper_manifest = json.loads((program_dir / "manifest.json").read_text())
    helper_manifest["semantic_sha256"] = "0" * 64
    (program_dir / "manifest.json").write_text(json.dumps(helper_manifest))
    scenario = SELECTOR._gem5_scenario(execution, [], golden)
    decoded = SELECTOR.decode_program((program_dir / "program.mshb").read_bytes())
    assert scenario["mesh_programs"][0]["semantic_digest"] == decoded.semantic_sha256()


def test_traffic_builder_rejects_duplicate_descriptor_rows():
    expected = [
        {"descriptor_id": 1, "kind": 1, "useful_bytes": 1, "bursts": 1},
        {"descriptor_id": 1, "kind": 1, "useful_bytes": 1, "bursts": 1},
    ]
    with pytest.raises(ContractError):
        build_dma_traffic("DC-17", "duplicates", expected, [], 1)
