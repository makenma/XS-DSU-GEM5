"""Shared mock-runtime harness: build a published program, run AXI_MESH, read its artifacts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
MESH_IR_ROOT = REPO / "util" / "mesh_ir"
ARCH = REPO / "configs/example/ai_mesh/arch/mesh_1x2.yaml"
GEM5 = REPO / "build/AXI_MESH/gem5.opt"
CONFIG = REPO / "configs/example/ai_mesh/run_mesh_program.py"

TERMINAL_STATES = ("completed", "errored", "cancelled")


def build_program(tmp_path, program, extra_args=()):
    program_dir = tmp_path / program
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mesh_ir.cli",
            "build",
            "--program",
            program,
            "--arch",
            str(ARCH),
            "--out",
            str(program_dir),
            *extra_args,
        ],
        cwd=MESH_IR_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return program_dir


def build_program_with_arch(tmp_path, program, arch):
    program_dir = tmp_path / f"{program}-{arch.stem}"
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
            str(program_dir),
        ],
        cwd=MESH_IR_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return program_dir


def runtime_environment():
    """The gem5 embedded interpreter imports mesh_ir from PYTHONPATH, so the
    harness interpreter's own search path (which carries third-party modules
    such as ``referencing``) must be forwarded explicitly."""
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("AI_MESH_")
    }
    search = [entry for entry in sys.path if entry]
    if environment.get("PYTHONPATH"):
        search.append(environment["PYTHONPATH"])
    environment["PYTHONPATH"] = os.pathsep.join(search)
    return environment


def run_mock(tmp_path, output_name, program, program_dir, extra_args=()):
    environment = runtime_environment()
    return subprocess.run(
        [
            str(GEM5),
            f"--outdir={tmp_path / output_name}",
            str(CONFIG),
            "--program",
            program,
            "--mesh-program-dir",
            str(program_dir),
            *extra_args,
        ],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )


def schedule(program_dir):
    return json.loads((program_dir / "schedule.mesh.json").read_text())["sections"]


def result_of(program_dir):
    return json.loads((program_dir / "actual_result.json").read_text())


def expected_traffic(program_dir):
    return json.loads((program_dir / "expected_traffic.json").read_text())["descriptors"]


def engine_commands(program_dir, engine_id):
    return sorted(
        row["command_id"]
        for row in schedule(program_dir)["COMMANDS"]
        if row["engine"] == engine_id
    )


def command_ticks(result, core_index=0):
    core = result["cores"][core_index]
    return (
        {int(key): value for key, value in core["command_issue_ticks"].items()},
        {int(key): value for key, value in core["command_done_ticks"].items()},
    )


def terminal_partition(result, core_index=0):
    """Every instance retires exactly the dispatched command/generation pairs:
    each pair exactly once and in exactly one terminal state (spec 8.8)."""
    core = result["cores"][core_index]
    core_id = core["core_id"]
    plan = {(entry[0], entry[1]) for entry in core["dispatch_plan"]}
    assert plan, "dispatch plan is empty"
    for instance in result["instances"]:
        ledger = instance["cores"][str(core_id)]
        seen = {}
        for record in ledger["terminals"]:
            assert record["state"] in TERMINAL_STATES, record
            key = (record["command_id"], record["generation"])
            assert key not in seen, (instance["instance"], key)
            seen[key] = record["state"]
        assert set(seen) == plan, (
            instance["instance"],
            sorted(plan - set(seen)),
            sorted(set(seen) - plan),
        )
        counts = {state: 0 for state in TERMINAL_STATES}
        for state in seen.values():
            counts[state] += 1
        for state in TERMINAL_STATES:
            assert ledger[state] == counts[state], (instance["instance"], state)
    return core, plan


def stable_operations(program_dir):
    """stable_key -> op_id from the published semantics, unique by construction."""
    module = json.loads((program_dir / "schedule.mesh.json").read_text())["semantics"]
    operations = {}
    for row in module["kernel_ops"]:
        key = row["stable_key"]
        assert key not in operations, key
        operations[key] = row["op_id"]
    return operations


def semantic_command(program_dir, stable_key):
    operations = stable_operations(program_dir)
    assert stable_key in operations, (stable_key, sorted(operations))
    op_id = operations[stable_key]
    commands = [
        row["command_id"]
        for row in schedule(program_dir)["COMMANDS"]
        if row["source_op_id"] == op_id
    ]
    assert len(commands) == 1, (stable_key, commands)
    return commands[0]


def sole_instance(result):
    assert len(result["instances"]) == 1, result["instances"]
    return result["instances"][0]["instance"]


def instance_frame(result, instance_id, core_id):
    frames = [
        row for row in result["instances"] if row["instance"] == instance_id
    ]
    assert len(frames) == 1, (instance_id, [row["instance"] for row in result["instances"]])
    core = frames[0]["cores"].get(str(core_id))
    assert core is not None, (instance_id, core_id, sorted(frames[0]["cores"]))
    return frames[0], core


def instance_observations(result, instance_id, core_id):
    _, core = instance_frame(result, instance_id, core_id)
    assert "observations" in core, ("observations", instance_id, core_id, sorted(core))
    return core["observations"]


def _unique(rows, key, label):
    selected = [row for row in rows if key(row)]
    assert len(selected) == 1, (label, selected)
    return selected[0]


def command_observation(result, instance_id, core_id, command_id, generation):
    _, core = instance_frame(result, instance_id, core_id)
    return _unique(
        core["observations"]["commands"],
        lambda row: row["command_id"] == command_id and row["generation"] == generation,
        ("command", instance_id, core_id, command_id, generation),
    )


def engine_execution(result, instance_id, core_id, command_id, generation):
    _, core = instance_frame(result, instance_id, core_id)
    return _unique(
        core["observations"]["engine_executions"],
        lambda row: row["command_id"] == command_id and row["generation"] == generation,
        ("engine", instance_id, core_id, command_id, generation),
    )


def compute_output(result, instance_id, core_id, command_id, generation):
    _, core = instance_frame(result, instance_id, core_id)
    return _unique(
        core["observations"]["compute_outputs"],
        lambda row: row["command_id"] == command_id and row["generation"] == generation,
        ("output", instance_id, core_id, command_id, generation),
    )


def descriptor_executions(result, instance_id, core_id, command_id, generation):
    _, core = instance_frame(result, instance_id, core_id)
    rows = [
        row
        for row in core["observations"]["descriptor_executions"]
        if row["command_id"] == command_id and row["generation"] == generation
    ]
    assert rows, ("descriptors", instance_id, core_id, command_id, generation)
    return rows


def admitted_descriptors(program_dir, command_id):
    rows = [
        row["descriptor_id"]
        for row in schedule(program_dir)["DMA_DESCRIPTORS"]
        if row["command_id"] == command_id
    ]
    assert rows, command_id
    assert len(set(rows)) == len(rows), rows
    return sorted(rows)


def actual_descriptor_transfer(result, program_dir, instance_id, core_id, command_id, generation):
    admitted = admitted_descriptors(program_dir, command_id)
    rows = descriptor_executions(result, instance_id, core_id, command_id, generation)
    assert sorted(row["descriptor_id"] for row in rows) == admitted, (admitted, rows)
    for row in rows:
        assert row["completed"], row
        assert row["committed"], row
        assert row["status"] == "OK", row
        assert row["transfer"] is not None and row["transfer"]["payload_digest"], row
    submit = min(row["submit_tick"] for row in rows)
    commit = max(row["commit_tick"] for row in rows)
    return admitted, submit, commit
