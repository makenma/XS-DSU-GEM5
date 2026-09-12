#!/usr/bin/env python3

from __future__ import annotations

import argparse
import functools
import hashlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
import venv
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
MESH_IR_ROOT = REPO / "util" / "mesh_ir"
CONFIG_ROOT = REPO / "configs" / "example" / "ai_mesh"
sys.path.insert(0, str(MESH_IR_ROOT))
sys.path.insert(0, str(CONFIG_ROOT))

from dummy_core_case_registry import CASES as GEM5_CASES
from dummy_core_case_registry import GATE3_PROFILES
from dummy_core_case_registry import invariant_registry
from mesh_ir.acceptance import (
    ARTIFACT_BASENAMES,
    ARTIFACT_KINDS,
    CHILD_ENV_BASE,
    CUMULATIVE,
    ContractError,
    RUN_EXIT_CODES,
    ZERO_LEDGERS,
    artifact_file_state,
    atomic_write_bytes,
    atomic_write_json,
    canonical_digest,
    canonical_u64_text,
    ensure_output_directory,
    file_sha256,
    read_yaml_document,
    read_json_artifact,
    select_cases,
    validate_detail_code,
    validate_invariants,
    validate_junit,
    validate_manifest,
    validate_results,
    validate_run_manifest,
    validate_run_summary,
    validate_schema,
    validate_traffic,
)
from mesh_ir.abi.decoder import decode_program
from mesh_ir.agent_config import (
    build_capacity_plan,
    load_agent_runtime_config,
    release_policy_of,
)
from mesh_ir.agent_planning import (
    build_command_identity_plan,
    build_host_arena_object_plan,
    build_host_task_identity_plan,
)
from mesh_ir.agent_surrogate import load_surrogate_profiles
from mesh_ir.agent_workload import load_control_plan, load_workload_plan
from mesh_ir.builder import load_arch
from mesh_ir.effective import EffectiveArchitecture
from mesh_ir.gate3_oracle import load_observation, validate_observation
from mesh_ir.gate4_oracle import arena_regions


MANIFEST = Path(__file__).resolve().parent / "mandatory_case_manifest.yaml"
GTEST_BINARIES = (
    REPO / "build/AXI_MESH/dev/ai_mesh/mesh_binary.test.opt",
    REPO / "build/AXI_MESH/dev/ai_mesh/tensor_sram.test.opt",
    REPO / "build/AXI_MESH/dev/ai_mesh/mesh_splitter.test.opt",
    REPO / "build/AXI_MESH/dev/ai_mesh/agent_protocol.test.opt",
    REPO / "build/AXI_MESH/dev/ai_mesh/host_resource_manager.test.opt",
    REPO / "build/AXI_MESH/dev/ai_mesh/agent_object_table.test.opt",
    REPO / "build/AXI_MESH/dev/ai_mesh/agent_workload_manager.test.opt",
)


def load_manifest():
    document = read_yaml_document(MANIFEST)
    validate_manifest(document)
    return document


def prepare_golden_artifacts(workdir: Path, cases: list[dict]) -> Path:
    golden = workdir / "_inputs" / "golden"
    golden.mkdir(parents=True)
    arch_default = REPO / "configs/example/ai_mesh/arch/mesh_1x2.yaml"
    requests = {}
    for case in cases:
        for subcase in case["subcases"]:
            execution = subcase["execution"]
            if execution["runner"] != "GEM5":
                continue
            arch_text = _argument_value(
                execution["args"], "--arch", str(arch_default)
            )
            arch = Path(arch_text)
            if not arch.is_absolute():
                arch = REPO / arch
            for argument in execution["args"]:
                if not argument.startswith("$GOLDEN/"):
                    continue
                name = argument.removeprefix("$GOLDEN/")
                if not name or "/" in name:
                    raise ContractError("golden reference must name one directory")
                program = name.removesuffix("_2port")
                request = (program, arch.resolve())
                previous = requests.setdefault(name, request)
                if previous != request:
                    raise ContractError(f"conflicting golden request for {name}")
    for name, (program, arch) in requests.items():
        output = golden / name
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "mesh_ir.cli",
                "build",
                "--program",
                program,
                "--arch",
                str(arch),
                "--out",
                str(output),
            ],
            cwd=MESH_IR_ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise ContractError(result.stderr.strip() or f"failed to build {program}")
    return golden


def run_once(command, cwd, env, timeout):
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            start_new_session=True,
        )
        stdout, stderr = process.communicate(timeout=timeout)
        result = subprocess.CompletedProcess(
            command, process.returncode, stdout, stderr
        )
        return result, time.monotonic() - started, None
    except subprocess.TimeoutExpired as error:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        error.stdout, error.stderr = process.communicate()
        return error, time.monotonic() - started, "WALL_TIMEOUT"
    except OSError as error:
        return error, time.monotonic() - started, "SPAWN_ERROR"


@functools.lru_cache(maxsize=None)
def _digest(path_text: str) -> str:
    return file_sha256(Path(path_text))


@functools.lru_cache(maxsize=None)
def _gtest_names(path_text: str) -> tuple[str, ...]:
    path = Path(path_text)
    if not path.is_file():
        return ()
    result = subprocess.run(
        [str(path), "--gtest_list_tests"],
        cwd=REPO,
        env={"LC_ALL": "C", "TZ": "UTC"},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ContractError(f"cannot list GTest target {path}")
    names = []
    suite = None
    for raw in result.stdout.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line:
            continue
        if not raw[:1].isspace() and line.endswith("."):
            suite = line
        elif raw[:1].isspace() and suite is not None:
            names.append(suite + line.strip())
    return tuple(names)


def _resolve_gtest(filter_name: str) -> tuple[Path, str]:
    matches = [
        path for path in GTEST_BINARIES if filter_name in _gtest_names(str(path))
    ]
    if len(matches) != 1:
        raise ContractError(
            f"GTest filter {filter_name} resolved to {len(matches)} targets"
        )
    registry = {
        str(path.relative_to(REPO)): list(_gtest_names(str(path)))
        for path in GTEST_BINARIES
        if path.is_file()
    }
    return matches[0].resolve(), canonical_digest(registry)


def _pytest_source(node_id: str) -> Path:
    source = node_id.split("::", 1)[0]
    path = (REPO / source).resolve()
    if REPO.resolve() not in path.parents or not path.is_file():
        raise ContractError(f"pytest node source does not exist: {source}")
    return path


@functools.lru_cache(maxsize=None)
def _validate_pytest_node(node_id: str, executable: Path) -> None:
    result = subprocess.run(
        [
            str(executable),
            "-m",
            "pytest",
            "--rootdir=.",
            node_id,
            "--collect-only",
            "-q",
            "--disable-warnings",
        ],
        cwd=REPO,
        env=CHILD_ENV_BASE,
        capture_output=True,
        text=True,
    )
    collected = [line.strip() for line in result.stdout.splitlines() if "::" in line]
    if result.returncode != 0 or collected != [node_id]:
        raise ContractError(
            f"pytest node {node_id} did not resolve to exactly one test"
        )


def _replace_golden(value: str, golden: Path) -> str:
    return value.replace("$GOLDEN", str(golden.resolve()))


def build_command(
    execution: dict,
    timeout: dict,
    artifact_dir: Path,
    golden: Path,
    python_executable: Path | None = None,
):
    runner = execution["runner"]
    if runner == "PYTEST":
        executable = (
            python_executable.absolute()
            if python_executable is not None
            else Path(sys.executable).resolve()
        )
        node_id = execution["node_id"]
        command = [
            str(executable),
            "-m",
            "pytest",
            node_id,
            "-q",
            "--maxfail=1",
            "--disable-warnings",
        ]
        registry_digest = _digest(str(_pytest_source(node_id)))
    elif runner == "GTEST":
        executable, registry_digest = _resolve_gtest(execution["gtest_filter"])
        command = [str(executable), f"--gtest_filter={execution['gtest_filter']}"]
    elif runner == "GEM5":
        if execution["case_name"] not in GEM5_CASES:
            raise ContractError(f"unknown selected GEM5 case {execution['case_name']}")
        executable = (REPO / "build/AXI_MESH/gem5.opt").resolve()
        if not executable.is_file():
            raise ContractError(f"missing GEM5 executable {executable}")
        script = REPO / execution["config_script"]
        if not script.is_file():
            raise ContractError(f"missing GEM5 config {script}")
        arguments = [_replace_golden(value, golden) for value in execution["args"]]
        command = [
            str(executable),
            f"--outdir={artifact_dir}",
            execution["config_script"],
        ]
        if execution["config_script"] == "configs/example/ai_mesh/run_gate3_protocol.py":
            command.extend([
                "--profile",
                GATE3_PROFILES[execution["case_name"]],
                "--sim-tick-limit",
                canonical_u64_text(timeout["sim_ticks"]),
            ])
        elif execution["config_script"] == "configs/example/ai_mesh/run_gate4_agent.py":
            command.extend([
                "--sim-tick-limit",
                canonical_u64_text(timeout["sim_ticks"]),
            ])
        else:
            command.extend([
                f"--case={execution['case_name']}",
                "--master-seed=20260901",
                f"--sim-tick-limit={canonical_u64_text(timeout['sim_ticks'])}",
            ])
        command.extend(arguments)
        registry_digest = _digest(str(CONFIG_ROOT / "dummy_core_case_registry.py"))
    else:
        raise ContractError(f"unknown runner {runner}")
    return runner, executable, command, registry_digest


def _child_environment(case_id: str, subcase: str, artifact_dir: Path) -> dict:
    return {
        **CHILD_ENV_BASE,
        "AI_MESH_CASE_ID": case_id,
        "AI_MESH_SUBCASE": subcase,
        "AI_MESH_ARTIFACT_DIR": str(artifact_dir),
        "AI_MESH_CHILD_REPORT": str(artifact_dir / "child_report.json"),
    }


def _environment_projection(environment: dict) -> list[dict]:
    return [
        {"name": name, "value": environment[name]}
        for name in sorted(environment, key=lambda value: value.encode("utf-8"))
    ]


def _normalized_execution_digest(
    runner: str, executable: Path, command: list[str], environment: dict
) -> str:
    inherited = []
    identity = {
        "runner": runner,
        "resolved_executable": str(executable),
        "final_argv": command,
        "child_environment": _environment_projection(environment),
        "inherited_environment_digest": canonical_digest(inherited),
    }
    return canonical_digest(identity)


@functools.lru_cache(maxsize=1)
def _git_identity() -> dict:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    return {"base_sha": revision, "dirty": dirty}


def _argument_value(arguments: list[str], name: str, default=None):
    for index, argument in enumerate(arguments):
        if argument == name and index + 1 < len(arguments):
            return arguments[index + 1]
        if argument.startswith(name + "="):
            return argument.split("=", 1)[1]
    return default


def _repo_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO / path


@functools.lru_cache(maxsize=None)
def _gate4_plan_documents(
    config_text: str, surrogate_text: str
) -> tuple:
    config = load_agent_runtime_config(_repo_path(config_text))
    agent = config.document["agent"]
    config_path = Path(config_text)
    workload = load_workload_plan(
        _repo_path(str(config_path.parent / agent["workload_plan"]))
    )
    control = (
        load_control_plan(
            _repo_path(str(config_path.parent / agent["control_plan"])), workload
        )
        if agent["control_plan"] is not None
        else None
    )
    surrogate = load_surrogate_profiles(_repo_path(surrogate_text))
    policy = release_policy_of(config)
    regions = arena_regions(config.document["serving"]["address_map"])
    identity = build_command_identity_plan(workload, control, policy)
    host_tasks = build_host_task_identity_plan(workload)
    arena = build_host_arena_object_plan(workload, control, policy, regions)
    capacity = build_capacity_plan(workload, control, config)
    return workload, control, identity, host_tasks, arena, capacity


def _gate4_scenario(execution: dict, arguments: list[str]) -> dict:
    config_text = _argument_value(arguments, "--runtime-config")
    surrogate_text = _argument_value(arguments, "--surrogate-profiles")
    if config_text is None or surrogate_text is None:
        raise ContractError("Gate4 execution lacks its plan fixtures")
    workload, control, identity, host_tasks, arena, capacity = _gate4_plan_documents(
        config_text, surrogate_text
    )
    configuration_digest = canonical_digest(
        {"case_name": execution["case_name"], "arguments": arguments}
    )
    protocol_digest = canonical_digest(
        {
            "protocol": "ai_mesh_gate3",
            "data_bus_bytes": 64,
            "control_bytes": 8,
        }
    )
    empty_digests = {
        "program_weight_registry": None,
        "model_weight_image": None,
        "endpoint_map": None,
    }
    period = 1_000_000_000_000 // 1_000_000_000
    return {
        "kind": "GEM5",
        "master_seed": 20260901,
        "data_mode": "FUNCTIONAL_BYTES",
        "strict_replay_serial_batches": False,
        "digests": {
            "configuration": configuration_digest,
            "base_architecture": protocol_digest,
            "effective_architecture": configuration_digest,
            "command_identity": identity["command_identity_digest"],
            "workload_plan": workload.digest,
            "control_plan": arena["control_plan_digest"],
            "host_task_identity": host_tasks["host_task_identity_digest"],
            "host_arena_object_plan": arena["host_arena_object_digest"],
            "capacity_plan": capacity["capacity_plan_digest"],
            **empty_digests,
        },
        "mesh_programs": [],
        "provider_profiles": [],
        "identity_counters": None,
        "physical_source_counters": [],
        "tick_projection": {
            "host_clock_period_ticks": period,
            "npu_clock_period_ticks": period,
            "core_clock_period_ticks": period,
            "host_tasks": [],
        },
        "endpoint_map": None,
        "host_arena_object_plan": None,
        "capacity_plan": None,
        "host_task_identity_plan": None,
        "approximation": {
            "reference_compute": False,
            "numeric_compute": False,
            "cpu_instruction_simulation": False,
            "cpu_mesh_simulation": False,
            "ucie_protocol_simulation": False,
            "remote_link_is_analytic_proxy": True,
            "synthetic_weight_bytes": True,
            "synthetic_output_bytes": True,
        },
    }


def _gem5_scenario(execution: dict, command: list[str], golden: Path) -> dict:
    arguments = [_replace_golden(value, golden) for value in execution["args"]]
    if execution["config_script"] == "configs/example/ai_mesh/run_gate4_agent.py":
        return _gate4_scenario(execution, arguments)
    if execution["config_script"] == "configs/example/ai_mesh/run_gate3_protocol.py":
        configuration_digest = canonical_digest(
            {"case_name": execution["case_name"], "arguments": arguments}
        )
        protocol_digest = canonical_digest(
            {
                "protocol": "ai_mesh_gate3",
                "data_bus_bytes": 64,
                "control_bytes": 8,
            }
        )
        empty_digests = {
            "program_weight_registry": None,
            "model_weight_image": None,
            "workload_plan": None,
            "control_plan": None,
            "host_arena_object_plan": None,
            "capacity_plan": None,
            "endpoint_map": None,
            "host_task_identity": None,
        }
        period = 1_000_000_000_000 // 1_000_000_000
        return {
            "kind": "GEM5",
            "master_seed": 20260901,
            "data_mode": "FUNCTIONAL_BYTES",
            "strict_replay_serial_batches": False,
            "digests": {
                "configuration": configuration_digest,
                "base_architecture": protocol_digest,
                "effective_architecture": configuration_digest,
                "command_identity": canonical_digest({"command": command}),
                **empty_digests,
            },
            "mesh_programs": [],
            "provider_profiles": [],
            "identity_counters": None,
            "physical_source_counters": [],
            "tick_projection": {
                "host_clock_period_ticks": period,
                "npu_clock_period_ticks": period,
                "core_clock_period_ticks": period,
                "host_tasks": [],
            },
            "endpoint_map": None,
            "host_arena_object_plan": None,
            "capacity_plan": None,
            "host_task_identity_plan": None,
            "approximation": {
                "reference_compute": False,
                "numeric_compute": False,
                "cpu_instruction_simulation": False,
                "cpu_mesh_simulation": False,
                "ucie_protocol_simulation": False,
                "remote_link_is_analytic_proxy": True,
                "synthetic_weight_bytes": True,
                "synthetic_output_bytes": True,
            },
        }
    program_dir_text = _argument_value(arguments, "--mesh-program-dir")
    if program_dir_text is None:
        raise ContractError("GEM5 execution has no mesh program directory")
    program_dir = Path(program_dir_text)
    program_image = program_dir / "program.mshb"
    decoded = decode_program(program_image.read_bytes())
    arch_text = _argument_value(
        arguments,
        "--arch",
        str(CONFIG_ROOT / "arch/mesh_1x2.yaml"),
    )
    arch_path = Path(arch_text)
    if not arch_path.is_absolute():
        arch_path = REPO / arch_path
    arch = load_arch(arch_path)
    effective = EffectiveArchitecture(arch)
    overrides = (
        ("--dma-queue-depth", "dma_descriptor_queue_depth"),
        ("--dma-descriptor-queue-depth", "dma_descriptor_queue_depth"),
        ("--admit-window", "admit_window"),
    )
    for option, field in overrides:
        value = _argument_value(arguments, option)
        if value is not None:
            effective.override(field, int(value))
    period_numerator = 1_000_000_000_000
    if period_numerator % arch.clock_hz:
        raise ContractError("architecture clock period is not integral in ticks")
    period = period_numerator // arch.clock_hz
    empty_digests = {
        "program_weight_registry": None,
        "model_weight_image": None,
        "workload_plan": None,
        "control_plan": None,
        "host_arena_object_plan": None,
        "capacity_plan": None,
        "endpoint_map": None,
        "host_task_identity": None,
    }
    digests = {
        "configuration": canonical_digest(
            {"case_name": execution["case_name"], "arguments": arguments}
        ),
        "base_architecture": arch.digest().hex(),
        "effective_architecture": effective.digest().hex(),
        "command_identity": decoded.semantic_sha256(),
        **empty_digests,
    }
    return {
        "kind": "GEM5",
        "master_seed": 20260901,
        "data_mode": "FUNCTIONAL_BYTES",
        "strict_replay_serial_batches": False,
        "digests": digests,
        "mesh_programs": [
            {
                "program_id": 1,
                "semantic_digest": decoded.semantic_sha256(),
                "file_sha256": _digest(str(program_image.resolve())),
            }
        ],
        "provider_profiles": [],
        "identity_counters": None,
        "physical_source_counters": [],
        "tick_projection": {
            "host_clock_period_ticks": period,
            "npu_clock_period_ticks": period,
            "core_clock_period_ticks": period,
            "host_tasks": [],
        },
        "endpoint_map": None,
        "host_arena_object_plan": None,
        "capacity_plan": None,
        "host_task_identity_plan": None,
        "approximation": {
            "reference_compute": False,
            "numeric_compute": False,
            "cpu_instruction_simulation": False,
            "cpu_mesh_simulation": False,
            "ucie_protocol_simulation": False,
            "remote_link_is_analytic_proxy": True,
            "synthetic_weight_bytes": True,
            "synthetic_output_bytes": True,
        },
    }


def _write_run_manifest(
    case_id: str,
    subcase: dict,
    manifest_digest: str,
    runner: str,
    executable: Path,
    command: list[str],
    registry_digest: str,
    environment: dict,
    artifact_dir: Path,
    golden: Path,
) -> tuple[dict, str]:
    execution_digest = _normalized_execution_digest(
        runner, executable, command, environment
    )
    scenario = (
        {
            "kind": "UNIT",
            "unit_registry_digest": registry_digest,
        }
        if runner in ("PYTEST", "GTEST")
        else _gem5_scenario(subcase["execution"], command, golden)
    )
    run_manifest = {
        "schema": "ai_mesh_run_manifest_v1",
        "version": 1,
        "id": case_id,
        "subcase": subcase["name"],
        "manifest_digest": manifest_digest,
        "git": _git_identity(),
        "execution": {
            "runner": runner,
            "normalized_execution_digest": execution_digest,
            "final_argv": command,
            "child_environment": _environment_projection(environment),
            "absolute_output_dir": str(artifact_dir),
        },
        "provenance": {
            "resolved_executable_sha256": _digest(str(executable)),
            "config_or_test_registry_sha256": registry_digest,
            "harness_sha256": _digest(str(Path(__file__).resolve())),
            "build_mode": "DEBUG" if executable.name.endswith(".debug") else "OPT",
            "inherited_environment": [],
        },
        "scenario": scenario,
    }
    validate_schema("run_manifest_v1.schema.json", run_manifest)
    validate_run_manifest(run_manifest)
    path = artifact_dir / ARTIFACT_BASENAMES["RUN_MANIFEST"]
    atomic_write_json(path, run_manifest)
    digest = canonical_digest(run_manifest)
    atomic_write_bytes(artifact_dir / ".run_manifest_digest", (digest + "\n").encode())
    return run_manifest, digest


def _termination(result, failure):
    if failure is not None:
        return {"kind": failure, "exit_code": None, "signal": None}
    if result.returncode < 0:
        return {"kind": "SIGNAL", "exit_code": None, "signal": -result.returncode}
    return {"kind": "EXIT", "exit_code": result.returncode, "signal": None}


def _read_child_report(path: Path, artifact_dir: Path):
    try:
        report = read_json_artifact(
            path, artifact_dir, "child_scenario_report_v1.schema.json"
        )
        validate_detail_code(report["first_fatal"])
        return report, None
    except ContractError as error:
        state = artifact_file_state(path, artifact_dir)
        if state == "MISSING":
            return None, "MISSING_CHILD_REPORT"
        if state == "INVALID":
            return None, "ARTIFACT_ERROR"
        return None, "INVALID_CHILD_REPORT"


def _judge_report(
    case_id: str,
    subcase: dict,
    termination: dict,
    child_report: dict | None,
    run_manifest: dict,
    run_manifest_digest: str,
    artifact_dir: Path,
    report_failure: str | None,
):
    failure_kind = None
    detail = "ok"
    if termination["kind"] == "SPAWN_ERROR":
        failure_kind = "SPAWN_ERROR"
    elif termination["kind"] == "WALL_TIMEOUT":
        failure_kind = "WALL_TIMEOUT"
    elif termination["kind"] == "SIGNAL":
        failure_kind = "SIGNAL"
    if failure_kind is None and child_report is None:
        failure_kind = report_failure or "INVALID_CHILD_REPORT"
    if failure_kind is None:
        expected_exit = RUN_EXIT_CODES[child_report["run_exit_reason"]]
        if termination["kind"] != "EXIT" or termination["exit_code"] != expected_exit:
            failure_kind = "EXIT_REPORT_MISMATCH"
        elif child_report["id"] != case_id or child_report["subcase"] != subcase["name"]:
            failure_kind = "EXIT_REPORT_MISMATCH"
        elif child_report["run_exit_reason"] != subcase["expected_exit_reason"]:
            failure_kind = "EXIT_REPORT_MISMATCH"
        elif child_report["first_fatal"] != subcase["expected_first_fatal"]:
            failure_kind = "EXIT_REPORT_MISMATCH"
        elif child_report["ledger_summary"] != subcase["expected_ledgers"]:
            failure_kind = "EXIT_REPORT_MISMATCH"
        elif child_report["run_manifest_digest"] != run_manifest_digest:
            failure_kind = "EXIT_REPORT_MISMATCH"
        elif subcase["terminal_class"] == "QUIESCENT_SUCCESS" and (
            child_report["watchdog_fired"] or not child_report["global_quiescence"]
        ):
            failure_kind = "EXIT_REPORT_MISMATCH"
        elif subcase["terminal_class"] == "EXPECTED_INFRA_FATAL" and (
            child_report["watchdog_fired"] or child_report["global_quiescence"]
        ):
            failure_kind = "EXIT_REPORT_MISMATCH"
    if failure_kind is None:
        try:
            stored_manifest = read_json_artifact(
                artifact_dir / ARTIFACT_BASENAMES["RUN_MANIFEST"],
                artifact_dir,
                "run_manifest_v1.schema.json",
            )
            if stored_manifest != run_manifest:
                raise ContractError("run manifest changed after child launch")
            invariants = read_json_artifact(
                artifact_dir / ARTIFACT_BASENAMES["INVARIANTS_JSON"],
                artifact_dir,
                "invariants_v1.schema.json",
            )
            expected_invariants = (
                invariant_registry(
                    subcase["execution"]["case_name"],
                    subcase["execution"]["args"],
                )
                if subcase["execution"]["runner"] == "GEM5"
                else ("unit_test_passed",)
            )
            validate_invariants(
                invariants,
                case_id,
                subcase["name"],
                expected_invariants,
            )
            if invariants["status"] != "PASS":
                raise ContractError("invariant artifact reports failure")
            if "TRAFFIC_JSON" in subcase["artifacts"]:
                traffic = read_json_artifact(
                    artifact_dir / ARTIFACT_BASENAMES["TRAFFIC_JSON"],
                    artifact_dir,
                    "traffic_v1.schema.json",
                )
                validate_traffic(traffic, case_id, subcase["name"])
                if traffic["status"] != "PASS":
                    raise ContractError("traffic artifact reports failure")
            if "FATAL_SNAPSHOT" in subcase["artifacts"]:
                snapshot = read_json_artifact(
                    artifact_dir / ARTIFACT_BASENAMES["FATAL_SNAPSHOT"],
                    artifact_dir,
                    "fatal_snapshot_v1.schema.json",
                )
                if snapshot["run_manifest_digest"] != run_manifest_digest:
                    raise ContractError("fatal snapshot run manifest mismatch")
            if "GATE3_OBSERVATION_JSON" in subcase["artifacts"]:
                observation = load_observation(
                    artifact_dir / ARTIFACT_BASENAMES["GATE3_OBSERVATION_JSON"]
                )
                validate_observation(observation)
                if observation["id"] != case_id or observation["subcase"] != subcase["name"]:
                    raise ContractError("Gate3 observation identity mismatch")
                if observation["final"]["fatal"] != (
                    subcase["terminal_class"] == "EXPECTED_INFRA_FATAL"
                ):
                    raise ContractError("Gate3 observation terminal state mismatch")
        except ContractError as error:
            failure_kind = "ARTIFACT_ERROR"
            detail = str(error)
    if failure_kind is not None and detail == "ok":
        detail = failure_kind.lower().replace("_", " ")
    return failure_kind is None, failure_kind, detail


def _artifact_paths(subcase: dict, artifact_dir: Path, status: str) -> list[dict]:
    paths = []
    for kind in subcase["artifacts"]:
        path = artifact_dir / ARTIFACT_BASENAMES[kind]
        available = kind in ("SUMMARY_JSON", "JUNIT_XML") or (
            artifact_file_state(path, artifact_dir) == "REGULAR"
        )
        paths.append(
            {
                "kind": kind,
                "state": "AVAILABLE" if available else "MISSING",
                "path": ARTIFACT_BASENAMES[kind] if available else None,
            }
        )
    if status == "PASS" and any(row["state"] != "AVAILABLE" for row in paths):
        raise ContractError("PASS summary cannot contain missing artifacts")
    return paths


def _write_single_junit(case_id: str, subcase: str, status: str, path: Path):
    suites = ET.Element(
        "testsuites",
        name="ai_mesh_mandatory",
        tests="1",
        failures="0" if status == "PASS" else "1",
    )
    suite = ET.SubElement(
        suites,
        "testsuite",
        name=case_id,
        tests="1",
        failures="0" if status == "PASS" else "1",
    )
    testcase = ET.SubElement(
        suite,
        "testcase",
        classname=case_id,
        name=f"{case_id}/{subcase}",
    )
    if status == "FAIL":
        ET.SubElement(testcase, "failure", message="acceptance contract failed")
    data = ET.tostring(suites, encoding="utf-8", xml_declaration=True)
    atomic_write_bytes(path, data)
    validate_junit(path, path.parent, [(case_id, subcase, status)])


def run_subcase(
    case_id,
    subcase,
    golden,
    workdir,
    manifest_digest=None,
    python_executable=None,
):
    manifest_digest = manifest_digest or ("0" * 64)
    artifact_dir = ensure_output_directory(Path(workdir), case_id, subcase["name"])
    environment = _child_environment(case_id, subcase["name"], artifact_dir)
    runner, executable, command, registry_digest = build_command(
        subcase["execution"],
        subcase["timeout"],
        artifact_dir,
        Path(golden),
        python_executable,
    )
    run_manifest, run_manifest_digest = _write_run_manifest(
        case_id,
        subcase,
        manifest_digest,
        runner,
        executable,
        command,
        registry_digest,
        environment,
        artifact_dir,
        Path(golden),
    )
    result, elapsed, process_failure = run_once(
        command,
        REPO,
        environment,
        subcase["timeout"]["wall_seconds"],
    )
    if isinstance(result, subprocess.CompletedProcess):
        atomic_write_bytes(
            artifact_dir / "stdout.txt", result.stdout.encode("utf-8", "replace")
        )
        atomic_write_bytes(
            artifact_dir / "stderr.txt", result.stderr.encode("utf-8", "replace")
        )
    termination = _termination(result, process_failure)
    child_report, report_failure = _read_child_report(
        artifact_dir / "child_report.json", artifact_dir
    )
    if termination["kind"] != "EXIT" or report_failure is not None:
        child_report = None
    ok, failure_kind, detail = _judge_report(
        case_id,
        subcase,
        termination,
        child_report,
        run_manifest,
        run_manifest_digest,
        artifact_dir,
        report_failure,
    )
    status = "PASS" if ok else "FAIL"
    summary = {
        "schema": "ai_mesh_run_summary_v1",
        "version": 1,
        "id": case_id,
        "subcase": subcase["name"],
        "status": status,
        "terminal_class": subcase["terminal_class"],
        "run_exit_reason": (
            child_report["run_exit_reason"]
            if child_report is not None
            else "HARNESS_FAILURE"
        ),
        "process_exit_code": termination["exit_code"],
        "child_termination": termination,
        "harness_failure_kind": failure_kind,
        "first_fatal": child_report["first_fatal"] if child_report else None,
        "watchdog_fired": child_report["watchdog_fired"] if child_report else None,
        "ledger_summary": child_report["ledger_summary"] if child_report else None,
        "global_quiescence": (
            child_report["global_quiescence"] if child_report else None
        ),
        "run_manifest_digest": (
            child_report["run_manifest_digest"] if child_report else None
        ),
        "normalized_execution_digest": run_manifest["execution"][
            "normalized_execution_digest"
        ],
        "artifact_paths": _artifact_paths(subcase, artifact_dir, status),
    }
    validate_schema("run_summary_v1.schema.json", summary)
    validate_run_summary(summary, subcase)
    atomic_write_json(artifact_dir / "summary.json", summary)
    _write_single_junit(
        case_id, subcase["name"], status, artifact_dir / "junit.xml"
    )
    validate_run_summary(summary, subcase, artifact_dir)
    return ok, detail, artifact_dir


def write_junit(report, path):
    suites = ET.Element(
        "testsuites",
        name="ai_mesh_mandatory",
        tests=str(report["selected_subcase_count"]),
        failures=str(report["subcase_fail_count"]),
    )
    for case in report["cases"]:
        suite = ET.SubElement(
            suites,
            "testsuite",
            name=case["id"],
            tests=str(len(case["subcases"])),
            failures=str(sum(row["status"] == "FAIL" for row in case["subcases"])),
        )
        for subcase in case["subcases"]:
            name = f"{case['id']}/{subcase['name']}"
            node = ET.SubElement(
                suite,
                "testcase",
                classname=case["id"],
                name=name,
            )
            if subcase["status"] == "FAIL":
                ET.SubElement(node, "failure", message="acceptance contract failed")
    atomic_write_bytes(path, ET.tostring(suites, encoding="utf-8", xml_declaration=True))


def _validate_selected_targets(cases: list[dict], python_executable: Path) -> None:
    for case in cases:
        for subcase in case["subcases"]:
            execution = subcase["execution"]
            if execution["runner"] == "PYTEST":
                _validate_pytest_node(execution["node_id"], python_executable)
            elif execution["runner"] == "GTEST":
                _resolve_gtest(execution["gtest_filter"])
            elif execution["case_name"] not in GEM5_CASES:
                raise ContractError(
                    f"unknown selected GEM5 case {execution['case_name']}"
                )


def _validate_execution_injectivity(
    cases: list[dict],
    workdir: Path,
    golden: Path,
    python_executable: Path,
) -> None:
    seen = {}
    for case in cases:
        for subcase in case["subcases"]:
            artifact_dir = (workdir / case["id"] / subcase["name"]).resolve()
            environment = _child_environment(case["id"], subcase["name"], artifact_dir)
            runner, executable, command, unused = build_command(
                subcase["execution"],
                subcase["timeout"],
                artifact_dir,
                golden,
                python_executable,
            )
            digest = _normalized_execution_digest(
                runner, executable, command, environment
            )
            identity = (case["id"], subcase["name"])
            if digest in seen:
                raise ContractError(
                    f"execution digest collision: {seen[digest]} and {identity}"
                )
            seen[digest] = identity


def _persisted_results(report: dict) -> dict:
    return {
        **report,
        "cases": [
            {
                "id": case["id"],
                "status": case["status"],
                "subcases": [
                    {
                        "name": subcase["name"],
                        "status": subcase["status"],
                        "summary_path": subcase["summary_path"],
                        "junit_testcase_name": subcase["junit_testcase_name"],
                    }
                    for subcase in case["subcases"]
                ],
            }
            for case in report["cases"]
        ],
    }


def _prepare_suite_root(path: Path) -> Path:
    root = path.absolute()
    if root.exists():
        if root.is_symlink() or not root.is_dir() or any(root.iterdir()):
            raise ContractError("suite output root must be a new or empty directory")
    else:
        root.mkdir(parents=True)
    return root.resolve()


def _python_has_pytest(executable: Path) -> bool:
    result = subprocess.run(
        [str(executable), "-c", "import pytest"],
        cwd=REPO,
        env=CHILD_ENV_BASE,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _prepare_python_runtime(workdir: Path) -> Path:
    current = Path(sys.executable).absolute()
    if _python_has_pytest(current):
        return current
    specification = importlib.util.find_spec("pytest")
    if specification is None or specification.origin is None:
        raise ContractError("pytest is unavailable to construct the unit runtime")
    package_root = Path(specification.origin).resolve().parents[1]
    runtime = workdir / "_python_runtime"
    venv.EnvBuilder(
        with_pip=False,
        symlinks=True,
        system_site_packages=True,
    ).create(runtime)
    executable = (runtime / "bin/python").absolute()
    query = subprocess.run(
        [
            str(executable),
            "-c",
            "import sysconfig; print(sysconfig.get_paths()['purelib'])",
        ],
        cwd=REPO,
        env=CHILD_ENV_BASE,
        capture_output=True,
        text=True,
    )
    if query.returncode != 0:
        raise ContractError("cannot resolve isolated Python package directory")
    package_directory = Path(query.stdout.strip())
    atomic_write_bytes(
        package_directory / "acceptance-packages.pth",
        f"{package_root}\n".encode("utf-8"),
    )
    if not _python_has_pytest(executable):
        raise ContractError("isolated Python runtime cannot import pytest")
    return executable


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate", type=int, choices=tuple(CUMULATIVE), default=1)
    parser.add_argument("--id", action="append", dest="explicit_ids")
    parser.add_argument("--workdir", default="/tmp/ai-mesh-manifest")
    arguments = parser.parse_args()
    try:
        document = load_manifest()
        manifest_digest = validate_manifest(document)
        selection_mode, cases = select_cases(
            document, arguments.gate, arguments.explicit_ids
        )
        workdir = _prepare_suite_root(Path(arguments.workdir))
        python_executable = _prepare_python_runtime(workdir)
        _validate_selected_targets(cases, python_executable)
        golden = prepare_golden_artifacts(workdir, cases)
        _validate_execution_injectivity(
            cases, workdir, golden, python_executable
        )
        report = {
            "schema": "ai_mesh_mandatory_results_v1",
            "version": 1,
            "manifest_digest": manifest_digest,
            "gate": arguments.gate,
            "selection": {
                "mode": selection_mode,
                "ids": [case["id"] for case in cases],
            },
            "selected_logical_count": len(cases),
            "selected_subcase_count": 0,
            "cases": [],
            "logical_pass_count": 0,
            "logical_fail_count": 0,
            "subcase_pass_count": 0,
            "subcase_fail_count": 0,
            "junit_path": f"junit_gate{arguments.gate}.xml",
        }
        failures = []
        for case in cases:
            result_case = {"id": case["id"], "subcases": []}
            for subcase in case["subcases"]:
                ok, detail, artifact_dir = run_subcase(
                    case["id"],
                    subcase,
                    golden,
                    workdir,
                    manifest_digest,
                    python_executable,
                )
                status = "PASS" if ok else "FAIL"
                result_case["subcases"].append(
                    {
                        "name": subcase["name"],
                        "status": status,
                        "summary_path": str(
                            (artifact_dir / "summary.json").relative_to(workdir)
                        ),
                        "junit_testcase_name": f"{case['id']}/{subcase['name']}",
                        "detail": detail,
                    }
                )
                report["selected_subcase_count"] += 1
                report[
                    "subcase_pass_count" if ok else "subcase_fail_count"
                ] += 1
                if not ok:
                    failures.append((case["id"], subcase["name"], detail))
            case_ok = all(row["status"] == "PASS" for row in result_case["subcases"])
            result_case["status"] = "PASS" if case_ok else "FAIL"
            report["logical_pass_count" if case_ok else "logical_fail_count"] += 1
            report["cases"].append(result_case)
        persisted = _persisted_results(report)
        validate_results(persisted, document, cases, workdir)
        junit_path = workdir / report["junit_path"]
        write_junit(report, junit_path)
        validate_junit(
            junit_path,
            workdir,
            [
                (case["id"], subcase["name"], subcase["status"])
                for case in report["cases"]
                for subcase in case["subcases"]
            ],
        )
        atomic_write_json(
            workdir / f"results_gate{arguments.gate}.json", persisted
        )
        print(
            f"GATE {arguments.gate}: {report['logical_pass_count']}/"
            f"{report['selected_logical_count']} logical PASS, "
            f"{report['logical_fail_count']} FAIL; "
            f"{report['subcase_pass_count']}/"
            f"{report['selected_subcase_count']} subcases, 0 skip "
            f"({workdir / report['junit_path']})"
        )
        for case_id, subcase, detail in failures:
            print(f"  FAIL {case_id}/{subcase}: {detail}")
        return 0 if not failures else 1
    except (ContractError, OSError, subprocess.SubprocessError) as error:
        print(f"HARNESS_FAILURE: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
