"""Slice B: double-buffer reuse, its actual descriptor overlap and the serialized control.

Every observation is queried by full identity (instance, core, command, generation)
through the shared harness; buffer roles come from the published stable operation
names, never from a local command-id table.
"""

import copy

import pytest

from mesh_ir.architecture import load_arch
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.golden_runtime_programs import (
    _build_double_buffer_program,
    build_double_buffer_unsafe_program,
)
from mesh_ir.publication import publish_authored_program
from tests.integration.support.runtime_harness import (
    ARCH,
    actual_descriptor_transfer,
    admitted_descriptors,
    build_program,
    command_observation,
    compute_output,
    descriptor_executions,
    engine_execution,
    instance_frame,
    result_of,
    run_mock,
    schedule,
    semantic_command,
)

CORE = 0
COMPUTE_0 = "overlap:03:compute:0"
COMPUTE_1 = "overlap:06:compute:1"
COMPUTE_2 = "overlap:09:compute:2"
PREFETCH_0 = "overlap:02:load:input:0"
PREFETCH_1 = "overlap:04:load:input:1"
PREFETCH_2 = "overlap:07:load:input:2"
STORE_0 = "overlap:05:store:0"
STORE_1 = "overlap:08:store:1"
STORE_2 = "overlap:10:store:2"
COMPUTE_OPERATIONS = (COMPUTE_0, COMPUTE_1, COMPUTE_2)
DMA_OPERATIONS = (PREFETCH_0, PREFETCH_1, PREFETCH_2, STORE_0, STORE_1, STORE_2)


def _successful_run(tmp_path, program, extra_args=(), program_dir=None):
    program_dir = program_dir or build_program(tmp_path, program)
    run = run_mock(tmp_path, "m5out", program, program_dir, extra_args)
    assert run.returncode == 0, run.stdout + run.stderr
    return program_dir, result_of(program_dir)


def _engine_window(result, program_dir, instance_id, stable_key):
    command = semantic_command(program_dir, stable_key)
    row = engine_execution(result, instance_id, CORE, command, 0)
    assert row["ended"], row
    return {
        "command": command,
        "begin": row["begin_tick"],
        "end": row["end_tick"],
        "planned_end": row["scheduled_end_tick"],
    }


def _descriptor_window(result, program_dir, instance_id, stable_key):
    command = semantic_command(program_dir, stable_key)
    descriptors, submit, commit = actual_descriptor_transfer(
        result, program_dir, instance_id, CORE, command, 0
    )
    return {"command": command, "descriptors": descriptors, "submit": submit, "commit": commit}


def _intersects(engine, transfer):
    return max(engine["begin"], transfer["submit"]) < min(engine["end"], transfer["commit"])


def _slot_allocation(program_dir, stable_key):
    command = semantic_command(program_dir, stable_key)
    sections = schedule(program_dir)
    row = next(item for item in sections["COMMANDS"] if item["command_id"] == command)
    operands = sections["COMMAND_OPERANDS"][
        row["operand_begin"] : row["operand_begin"] + row["operand_count"]
    ]
    writes = [operand for operand in operands if operand["access"] == 2]
    assert len(writes) == 1, (stable_key, operands)
    allocation_id = writes[0]["allocation_id"]
    allocation = next(
        item for item in sections["ALLOCATIONS"] if item["allocation_id"] == allocation_id
    )
    return allocation_id, allocation["offset_bytes"], allocation["size_bytes"]


def _instance_content(result, program_dir, instance_id):
    content = {}
    for stable_key in COMPUTE_OPERATIONS:
        command = semantic_command(program_dir, stable_key)
        row = compute_output(result, instance_id, CORE, command, 0)
        content[stable_key] = ("compute", tuple(row["digest_words"]))
    for stable_key in DMA_OPERATIONS:
        command = semantic_command(program_dir, stable_key)
        rows = descriptor_executions(result, instance_id, CORE, command, 0)
        content[stable_key] = (
            "dma",
            tuple(
                sorted(
                    (
                        item["descriptor_id"],
                        item["completed"],
                        item["committed"],
                        item["status"],
                        item["transfer"]["read_bytes"] if item["transfer"] else 0,
                        item["transfer"]["write_bytes"] if item["transfer"] else 0,
                        item["transfer"]["payload_digest"] if item["transfer"] else "",
                    )
                    for item in rows
                )
            ),
        )
    return content


def _useful_bytes(program_dir):
    sections = schedule(program_dir)
    stable = {row["command_id"]: row["source_op_id"] for row in sections["COMMANDS"]}
    totals = {}
    for row in sections["EXPECTED_TRAFFIC"]:
        key = stable[row["command_id"]]
        totals[key] = totals.get(key, 0) + row["useful_bytes"]
    return totals


def _assert_overlap(result, program_dir, instance_id):
    engine = _engine_window(result, program_dir, instance_id, COMPUTE_0)
    transfer = _descriptor_window(result, program_dir, instance_id, PREFETCH_1)
    assert _intersects(engine, transfer), (engine, transfer)
    return engine, transfer


def _assert_no_overlap(result, program_dir, instance_id):
    engine = _engine_window(result, program_dir, instance_id, COMPUTE_0)
    transfer = _descriptor_window(result, program_dir, instance_id, PREFETCH_1)
    assert not _intersects(engine, transfer), (engine, transfer)
    return engine, transfer


def test_double_buffer_prefetch_overlaps_compute(tmp_path):
    program_dir, result = _successful_run(
        tmp_path, "double_buffer_overlap", ("--instances", "2")
    )
    assert len(result["instances"]) == 2
    for instance in result["instances"]:
        instance_id = instance["instance"]
        engine, transfer = _assert_overlap(result, program_dir, instance_id)

        reuse = _descriptor_window(result, program_dir, instance_id, PREFETCH_2)
        assert reuse["submit"] >= engine["end"], (reuse, engine)
        compute_row = command_observation(result, instance_id, CORE, engine["command"], 0)
        assert compute_row["terminal"] and compute_row["terminal_tick"] >= engine["end"]

        first_slot = _slot_allocation(program_dir, PREFETCH_0)
        second_slot = _slot_allocation(program_dir, PREFETCH_1)
        reused_slot = _slot_allocation(program_dir, PREFETCH_2)
        assert first_slot == reused_slot, (first_slot, reused_slot)
        assert second_slot != first_slot, (first_slot, second_slot)
        assert (
            first_slot[1] + first_slot[2] <= second_slot[1]
            or second_slot[1] + second_slot[2] <= first_slot[1]
        ), (first_slot, second_slot)

        store = _descriptor_window(result, program_dir, instance_id, STORE_0)
        following = _engine_window(result, program_dir, instance_id, COMPUTE_1)
        assert store["commit"] >= store["submit"]
        assert following["end"] >= following["begin"]

        frame, core = instance_frame(result, instance_id, CORE)
        assert frame["finalized"] is True
        assert core["resources"]["live_commands"] == 0
        assert core["resources"]["allocation_pins"] == 0
        assert core["resources"]["engine_occupancy"] == {
            "tensor": 0,
            "vector": 0,
            "reduce": 0,
        }
        assert core["completed"] == len(core["terminals"]) > 0


def test_double_buffer_matches_serialized_control(tmp_path):
    overlap_dir, overlap = _successful_run(
        tmp_path, "double_buffer_overlap", ("--instances", "2")
    )
    serialized_dir, serialized = _successful_run(
        tmp_path, "double_buffer_serialized", ("--instances", "2")
    )
    assert {row["instance"] for row in overlap["instances"]} == {
        row["instance"] for row in serialized["instances"]
    }
    for instance_id in sorted(row["instance"] for row in overlap["instances"]):
        _assert_overlap(overlap, overlap_dir, instance_id)
        _assert_no_overlap(serialized, serialized_dir, instance_id)
        assert _instance_content(overlap, overlap_dir, instance_id) == _instance_content(
            serialized, serialized_dir, instance_id
        )
    useful = _useful_bytes(overlap_dir)
    assert useful == _useful_bytes(serialized_dir)
    assert all(value > 0 for value in useful.values())


def test_serialized_identity_without_serialization_fails_the_control(tmp_path):
    arch = load_arch(ARCH)
    out = tmp_path / "double_buffer_serialized_unsynchronized"
    program = _build_double_buffer_program(arch, "serialized", False)
    published = publish_authored_program(
        out,
        program,
        arch,
        kind="golden",
        identity=(
            ("program", "double_buffer_serialized_unsynchronized"),
            ("arch_name", arch.arch_name),
        ),
        protected_inputs=(ARCH,),
    )
    assert published.canonical_dict()["status"] == "ok"
    run = run_mock(tmp_path, "m5out", "double_buffer_serialized", out)
    assert run.returncode == 0, run.stdout + run.stderr
    result = result_of(out)
    instance_id = result["instances"][0]["instance"]
    with pytest.raises(AssertionError):
        _assert_no_overlap(result, out, instance_id)
    _assert_overlap(result, out, instance_id)


def test_double_buffer_instances_are_stable(tmp_path):
    program_dir, result = _successful_run(
        tmp_path, "double_buffer_overlap", ("--instances", "2")
    )
    contents = [
        _instance_content(result, program_dir, row["instance"]) for row in result["instances"]
    ]
    assert contents[0] == contents[1]
    for instance in result["instances"]:
        instance_id = instance["instance"]
        frame, core = instance_frame(result, instance_id, CORE)
        assert frame["finalized"] is True
        assert core["errored"] == 0 and core["cancelled"] == 0
        assert core["resources"] == {
            "live_commands": 0,
            "live_dma_commands": 0,
            "live_dma_tags": 0,
            "allocation_pins": 0,
            "engine_occupancy": {"tensor": 0, "vector": 0, "reduce": 0},
        }

    projections = result["cores"][CORE]
    assert projections["command_issue_ticks"], projections
    assert projections["command_done_ticks"], projections
    per_command = {}
    for row in result["digests"]:
        per_command.setdefault((row["core_id"], row["command_id"]), []).append(row["digest"])
    assert per_command and all(len(values) == 2 for values in per_command.values()), per_command
    assert all(len(set(values)) == 1 for values in per_command.values()), per_command

    tampered = copy.deepcopy(result)
    _, core = instance_frame(tampered, 2, CORE)
    core["observations"]["compute_outputs"][-1]["digest_words"][0] ^= 0xFFFFFFFF
    assert _instance_content(tampered, program_dir, 2) != _instance_content(
        result, program_dir, 2
    )
    tampered_payload = copy.deepcopy(result)
    _, core = instance_frame(tampered_payload, 2, CORE)
    core["observations"]["descriptor_executions"][-1]["transfer"]["payload_digest"] = "dead-beef"
    assert _instance_content(tampered_payload, program_dir, 2) != _instance_content(
        result, program_dir, 2
    )


def test_engine_observations_are_instance_qualified(tmp_path):
    program_dir, result = _successful_run(
        tmp_path, "double_buffer_overlap", ("--instances", "2")
    )
    command = semantic_command(program_dir, COMPUTE_0)
    first = engine_execution(result, 1, CORE, command, 0)
    second = engine_execution(result, 2, CORE, command, 0)
    assert first["begin_tick"] != second["begin_tick"]
    assert first["end_tick"] < second["begin_tick"]
    with pytest.raises(AssertionError):
        engine_execution(result, 3, CORE, command, 0)
    with pytest.raises(AssertionError):
        engine_execution(result, 1, CORE, command + 1000, 0)
    with pytest.raises(AssertionError):
        engine_execution(result, 1, CORE + 1, command, 0)


def test_descriptor_timings_are_actual_completions(tmp_path):
    program_dir, result = _successful_run(tmp_path, "double_buffer_overlap")
    instance_id = result["instances"][0]["instance"]
    command = semantic_command(program_dir, PREFETCH_1)
    rows = descriptor_executions(result, instance_id, CORE, command, 0)
    assert sorted(row["descriptor_id"] for row in rows) == admitted_descriptors(
        program_dir, command
    )
    command_row = command_observation(result, instance_id, CORE, command, 0)
    assert command_row["terminal"]
    for row in rows:
        assert row["completed"] is True
        assert row["committed"] is True
        assert row["status"] == "OK"
        assert row["scheduled_completion_tick"] is not None
        assert row["completion_tick"] == row["commit_tick"]
        assert row["commit_tick"] >= row["submit_tick"]
        assert command_row["terminal_tick"] >= row["commit_tick"]
        assert row["transfer"]["read_bytes"] == 8192
        assert row["transfer"]["read_bursts"] > 0
        assert row["transfer"]["payload_digest"]


def test_error_completion_drains_without_a_successful_commit(tmp_path):
    program_dir = build_program(tmp_path, "double_buffer_overlap")
    command = semantic_command(program_dir, PREFETCH_1)
    descriptor = admitted_descriptors(program_dir, command)[0]
    run = run_mock(
        tmp_path,
        "m5out",
        "double_buffer_overlap",
        program_dir,
        ("--error-descriptors", str(descriptor)),
    )
    output = run.stdout + run.stderr
    assert "MESH_PROGRAM_ERROR_DRAINED" in output, output
    result = result_of(program_dir)
    instance_id = result["instances"][0]["instance"]
    rows = descriptor_executions(result, instance_id, CORE, command, 0)
    errored = [row for row in rows if row["descriptor_id"] == descriptor]
    assert len(errored) == 1, rows
    row = errored[0]
    assert row["completed"] is True
    assert row["committed"] is False
    assert row["status"] == "AXI_READ_ERROR"
    assert row["commit_tick"] is None
    assert row["transfer"]["read_bytes"] == 0
    assert row["transfer"]["read_bursts"] == 0
    assert not row["transfer"]["payload_digest"]
    core = result["cores"][CORE]
    assert core["dma_idle"] == 1 and core["live_commands"] == 0
    assert core["allocation_pins"] == 0


def test_lost_descriptor_does_not_publish_completion(tmp_path):
    program_dir = build_program(tmp_path, "double_buffer_overlap")
    command = semantic_command(program_dir, PREFETCH_1)
    descriptor = admitted_descriptors(program_dir, command)[0]
    run = run_mock(
        tmp_path,
        "m5out",
        "double_buffer_overlap",
        program_dir,
        ("--drop-descriptors", str(descriptor), "--watchdog-ticks", "200000"),
    )
    output = run.stdout + run.stderr
    assert "E_RUNTIME_DEADLOCK" in output, output
    assert "MESH_PROGRAM_DONE" not in output, output
    assert "observation_frame" in output, output
    submitted = int(output.split("descriptors_submitted=")[1].split()[0])
    completed = int(output.split("descriptors_completed=")[1].split()[0])
    committed = int(output.split("descriptors_committed=")[1].split()[0])
    assert submitted > completed, (submitted, completed)
    assert completed == committed, (completed, committed)


def test_incomplete_or_duplicate_observations_are_rejected(tmp_path):
    program_dir, result = _successful_run(tmp_path, "double_buffer_overlap")
    instance_id = result["instances"][0]["instance"]
    command = semantic_command(program_dir, COMPUTE_0)
    assert engine_execution(result, instance_id, CORE, command, 0)["ended"] is True

    dropped = copy.deepcopy(result)
    _, core = instance_frame(dropped, instance_id, CORE)
    core["observations"]["engine_executions"] = [
        row
        for row in core["observations"]["engine_executions"]
        if row["command_id"] != command
    ]
    with pytest.raises(AssertionError):
        engine_execution(dropped, instance_id, CORE, command, 0)

    duplicated = copy.deepcopy(result)
    _, core = instance_frame(duplicated, instance_id, CORE)
    row = next(
        item
        for item in core["observations"]["engine_executions"]
        if item["command_id"] == command
    )
    core["observations"]["engine_executions"].append(copy.deepcopy(row))
    with pytest.raises(AssertionError):
        engine_execution(duplicated, instance_id, CORE, command, 0)

    missing_descriptor = copy.deepcopy(result)
    _, core = instance_frame(missing_descriptor, instance_id, CORE)
    prefetch = semantic_command(program_dir, PREFETCH_1)
    admitted = admitted_descriptors(program_dir, prefetch)
    core["observations"]["descriptor_executions"] = [
        item
        for item in core["observations"]["descriptor_executions"]
        if item["descriptor_id"] != admitted[0]
    ]
    with pytest.raises(AssertionError):
        actual_descriptor_transfer(
            missing_descriptor, program_dir, instance_id, CORE, prefetch, 0
        )


def test_double_buffer_reuse_requires_retirement():
    arch = load_arch(ARCH)
    with pytest.raises(MeshIrError) as failure:
        build_double_buffer_unsafe_program(arch)
    assert failure.value.code == "E_TENSOR_NOT_RESIDENT"
    assert failure.value.context == {
        "first_op_id": 29,
        "second_op_id": 33,
        "object_id": 1,
    }
