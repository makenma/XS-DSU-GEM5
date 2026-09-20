"""Slice C: asymmetric barrier arrival, release, error cancel and instance reset."""

import pytest

from mesh_ir.architecture import load_arch

from tests.integration.support.runtime_harness import (
    ARCH,
    admitted_descriptors,
    build_program,
    command_observation,
    instance_frame,
    result_of,
    run_mock,
    semantic_command,
)

BARRIER_0 = "asym:01:barrier:core0"
LOAD_1 = "asym:02:load:core1"
BARRIER_1 = "asym:03:barrier:core1"
AFTER_0 = "asym:04:after:core0"
AFTER_1 = "asym:05:after:core1"
PROGRAM = "barrier_asymmetric"


def _run(tmp_path, extra_args=()):
    program_dir = build_program(tmp_path, PROGRAM)
    run = run_mock(tmp_path, "m5out", PROGRAM, program_dir, extra_args)
    return program_dir, run


def _barriers(result, instance_id, core_id):
    frame, _ = instance_frame(result, instance_id, core_id)
    groups = frame["barriers"]
    assert len(groups) == 1, groups
    return groups[0]


def _commands(program_dir):
    return {
        key: semantic_command(program_dir, key)
        for key in (BARRIER_0, LOAD_1, BARRIER_1, AFTER_0, AFTER_1)
    }


def test_barrier_waits_for_missing_participant(tmp_path):
    program_dir, run = _run(tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    result = result_of(program_dir)
    commands = _commands(program_dir)
    instance_id = result["instances"][0]["instance"]
    group = _barriers(result, instance_id, 0)
    assert group["expected"] == 2
    assert group["phase"] == "released"
    arrivals = {row["participant"]: row["tick"] for row in group["arrivals"]}
    assert set(arrivals) == {commands[BARRIER_0], commands[BARRIER_1]}, arrivals
    assert arrivals[commands[BARRIER_0]] < arrivals[commands[BARRIER_1]]
    assert group["release_tick"] == arrivals[commands[BARRIER_1]]
    assert group["cancel_tick"] is None

    store = {
        commands[AFTER_0]: command_observation(result, instance_id, 0, commands[AFTER_0], 0),
        commands[AFTER_1]: command_observation(result, instance_id, 1, commands[AFTER_1], 0),
    }
    for row in store.values():
        assert row["issued"] and row["terminal"]
        assert row["issue_tick"] >= group["release_tick"]
    load = command_observation(result, instance_id, 1, commands[LOAD_1], 0)
    assert load["terminal"] and load["terminal_tick"] <= arrivals[commands[BARRIER_1]]
    barrier1 = command_observation(result, instance_id, 1, commands[BARRIER_1], 0)
    assert barrier1["issue_tick"] >= load["terminal_tick"]


def test_barrier_watchdog_names_missing_dependency(tmp_path):
    program_dir = build_program(tmp_path, PROGRAM)
    commands = _commands(program_dir)
    descriptor = admitted_descriptors(program_dir, commands[LOAD_1])[0]
    run = run_mock(
        tmp_path,
        "m5out",
        PROGRAM,
        program_dir,
        ("--drop-descriptors", str(descriptor), "--watchdog-ticks", "200000"),
    )
    output = run.stdout + run.stderr
    assert "E_RUNTIME_DEADLOCK" in output, output
    assert "MESH_PROGRAM_DONE" not in output, output
    assert "barrier event=" in output, output
    lines = [row for row in output.splitlines() if "barrier event=" in row]
    assert lines, output
    for line in lines:
        assert "generation=0" in line, line
        assert "arrived=1[" in line and "expected=2" in line, line
        assert f"cmd{commands[BARRIER_0]}(core0)" in line, line
        assert f"cmd{commands[BARRIER_1]}(core1)" in line, line
    chained = [
        line
        for line in lines
        if f"<-cmd{commands[LOAD_1]}(core1)" in line and "dma_cmd" in line
    ]
    assert chained, lines
    assert "submitted=" in chained[0], chained[0]
    assert "observation_frame" in output, output


def test_barrier_error_cancels_open_group(tmp_path):
    program_dir = build_program(tmp_path, PROGRAM)
    commands = _commands(program_dir)
    descriptor = admitted_descriptors(program_dir, commands[LOAD_1])[0]
    run = run_mock(
        tmp_path,
        "m5out",
        PROGRAM,
        program_dir,
        ("--error-descriptors", str(descriptor)),
    )
    output = run.stdout + run.stderr
    assert "MESH_PROGRAM_ERROR_DRAINED" in output, output
    result = result_of(program_dir)
    instance_id = result["instances"][0]["instance"]
    group = _barriers(result, instance_id, 0)
    assert group["phase"] == "cancelled"
    assert group["release_tick"] is None
    assert group["cancel_tick"] is not None
    assert [row["participant"] for row in group["arrivals"]] == [commands[BARRIER_0]]
    for core_id, key in ((0, AFTER_0), (1, AFTER_1)):
        row = command_observation(result, instance_id, core_id, commands[key], 0)
        assert row["terminal"], row
    core = result["cores"][0]
    assert core["dma_idle"] == 1 and core["live_commands"] == 0 and core["allocation_pins"] == 0
    assert result["cores"][1]["live_commands"] == 0


def test_barrier_state_resets_between_instances(tmp_path):
    program_dir, run = _run(tmp_path, ("--instances", "2"))
    assert run.returncode == 0, run.stdout + run.stderr
    result = result_of(program_dir)
    commands = _commands(program_dir)
    releases = []
    for instance in result["instances"]:
        instance_id = instance["instance"]
        group = _barriers(result, instance_id, 0)
        assert group["phase"] == "released"
        assert group["expected"] == 2
        assert sorted(row["participant"] for row in group["arrivals"]) == sorted(
            [commands[BARRIER_0], commands[BARRIER_1]]
        )
        releases.append(group["release_tick"])
    assert releases[0] is not None and releases[1] is not None
    assert releases[0] < releases[1]
    for instance in result["instances"]:
        instance_id = instance["instance"]
        for core_id, key in ((0, AFTER_0), (1, AFTER_1)):
            row = command_observation(result, instance_id, core_id, commands[key], 0)
            assert row["issued"] and row["terminal"]
        _, core = instance_frame(result, instance_id, 0)
        assert core["resources"]["live_commands"] == 0
        assert core["resources"]["engine_occupancy"] == {
            "tensor": 0,
            "vector": 0,
            "reduce": 0,
        }
