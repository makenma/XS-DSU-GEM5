"""NORM-11: runtime invalid residency is a compiler-declared fact, an installed
initial-residency set and one structured, executable diagnostic."""

import json

from mesh_ir.abi.decoder import decode_program
from mesh_ir.architecture import load_arch
from mesh_ir.golden_programs import _build_pre_resident_store_program
from mesh_ir.ir.kernel_ir import StateOrigin
from mesh_ir.publication import publish_authored_program
from mesh_ir.scheduled.model import LocalAllocationBacking

from tests.integration.support.runtime_harness import (
    ARCH,
    admitted_descriptors,
    build_program,
    descriptor_executions,
    result_of,
    run_mock,
    schedule,
    semantic_command,
)

RESIDENT_PROGRAM = "pre_resident_weight"
PRODUCER_PROGRAM = "fill_offset"
FILL = "fill:input"
REPEAT_PROGRAM = "repeat"
REPEAT_CONSUMER = "repeat:load"
CANCEL_PROGRAM = "dual"
DIAGNOSTIC_FILE = "runtime_diagnostics.jsonl"
RESIDENCY_CODE = "E_TENSOR_NOT_RESIDENT"


def _initial_residency(program_dir):
    program = decode_program((program_dir / "program.mshb").read_bytes())
    semantics = program.semantics
    backings = {row.object_id: row.backing for row in semantics.object_backings}
    allocations = {row.allocation_id: row for row in program.allocations}
    initial = {}
    for state in semantics.states:
        if state.version != 0 or state.origin is not StateOrigin.PRE_RESIDENT:
            continue
        backing = backings[state.object_id]
        assert isinstance(backing, LocalAllocationBacking), backing
        allocation = allocations[backing.allocation_id]
        initial.setdefault(allocation.owner_core, set()).add(
            allocation.allocation_id
        )
    return initial


def _residency(instance, core_id):
    return instance["cores"][str(core_id)]["observations"]["residency"]


def _records(program_dir):
    path = program_dir / DIAGNOSTIC_FILE
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text().splitlines() if line.strip()
    ]


def _local_allocations(program_dir):
    return {row["allocation_id"] for row in schedule(program_dir)["ALLOCATIONS"]}


def _access_allocations(program_dir, command_id, access):
    sections = schedule(program_dir)
    command = next(
        row for row in sections["COMMANDS"] if row["command_id"] == command_id
    )
    operands = sections["COMMAND_OPERANDS"][
        command["operand_begin"] : command["operand_begin"] + command["operand_count"]
    ]
    local = _local_allocations(program_dir)
    return {
        operand["allocation_id"]
        for operand in operands
        if operand["access"] == access and operand["allocation_id"] in local
    }


def _read_allocations(program_dir, command_id):
    return _access_allocations(program_dir, command_id, 1)


def _write_allocations(program_dir, command_id):
    return _access_allocations(program_dir, command_id, 2)


def _readers_of(program_dir, allocation):
    return [
        row["command_id"]
        for row in schedule(program_dir)["COMMANDS"]
        if allocation in _read_allocations(program_dir, row["command_id"])
    ]


def _plan(result, core_id):
    core = next(row for row in result["cores"] if row["core_id"] == core_id)
    return [(row[0], row[1]) for row in core["dispatch_plan"]]


def _terminals(result, instance_index, core_id):
    return {
        (row["command_id"], row["generation"]): row["state"]
        for row in result["instances"][instance_index]["cores"][str(core_id)][
            "terminals"
        ]
    }


def _assert_zero_resources(result):
    for core in result["cores"]:
        assert core["commands_errored"] == 0, core
        assert core["commands_cancelled"] == 0, core
        assert core["live_commands"] == 0 and core["allocation_pins"] == 0, core
        assert core["dma_idle"] == 1, core


def _assert_begin_residency(result, program_dir):
    expected = _initial_residency(program_dir)
    for instance in result["instances"]:
        for core_id in instance["cores"]:
            assert set(_residency(instance, int(core_id))["at_begin"]) == (
                expected.get(int(core_id), set())
            ), (instance["instance"], core_id, expected)


def _residency_record(program_dir, output, kind):
    assert RESIDENCY_CODE in output, output
    records = _records(program_dir)
    assert len(records) == 1, records
    record = records[0]
    assert record["code"] == RESIDENCY_CODE, record
    assert record["stage"] == "runtime", record
    assert record["severity"] == "error", record
    assert record["message"], record
    context = record["context"]
    assert context["dma_kind"] == kind, context
    assert int(context["command_id"]) > 0, context
    assert context["generation"] == "0", context
    assert int(context["operand_index"]) >= 0, context
    return context


def test_pre_resident_weights_are_resident_from_load(tmp_path):
    program_dir = build_program(tmp_path, RESIDENT_PROGRAM)
    assert _initial_residency(program_dir), "the carrier declares no weight"
    run = run_mock(tmp_path, "m5out", RESIDENT_PROGRAM, program_dir)
    output = run.stdout + run.stderr
    assert run.returncode == 0, output
    assert "MESH_PROGRAM_DONE" in output, output
    result = result_of(program_dir)
    _assert_begin_residency(result, program_dir)
    _assert_zero_resources(result)
    assert _records(program_dir) == []


def test_producer_commit_establishes_residency_for_its_consumer(tmp_path):
    program_dir = build_program(tmp_path, RESIDENT_PROGRAM)
    declared = {
        value
        for values in _initial_residency(program_dir).values()
        for value in values
    }
    run = run_mock(tmp_path, "m5out", RESIDENT_PROGRAM, program_dir)
    output = run.stdout + run.stderr
    assert run.returncode == 0, output
    result = result_of(program_dir)
    for instance in result["instances"]:
        residency = _residency(instance, 0)
        assert set(residency["at_begin"]) == declared, residency
        produced = set(residency["at_end"]) - declared
        assert produced, residency
        states = _terminals(result, 0, 0)
        for allocation in produced:
            producers = [
                row["command_id"]
                for row in schedule(program_dir)["COMMANDS"]
                if allocation in _write_allocations(program_dir, row["command_id"])
            ]
            assert producers, allocation
            for command, generation in _plan(result, 0):
                if command in producers:
                    assert states[(command, generation)] == "completed", states
            for consumer in _readers_of(program_dir, allocation):
                executions = descriptor_executions(
                    result, instance["instance"], 0, consumer, 0
                )
                assert all(
                    row["status"] == "OK" and row["committed"]
                    for row in executions
                ), (consumer, executions)
    assert _records(program_dir) == []


def test_errored_producer_cancels_its_consumer_without_a_residency_fault(tmp_path):
    program_dir = build_program(tmp_path, PRODUCER_PROGRAM)
    producer = semantic_command(program_dir, FILL)
    descriptor = admitted_descriptors(program_dir, producer)[0]
    allocation = next(iter(_write_allocations(program_dir, producer)))
    consumers = _readers_of(program_dir, allocation)
    assert consumers, allocation
    run = run_mock(
        tmp_path,
        "m5out",
        PRODUCER_PROGRAM,
        program_dir,
        ("--error-descriptors", str(descriptor)),
    )
    output = run.stdout + run.stderr
    assert "MESH_PROGRAM_ERROR_DRAINED" in output, output
    assert run.returncode != 0, output
    result = result_of(program_dir)
    states = _terminals(result, 0, 0)
    for consumer in consumers:
        planned = [key for key in _plan(result, 0) if key[0] == consumer]
        assert planned, consumer
        for key in planned:
            assert states[key] == "cancelled", (key, states)
        executions = [
            row
            for row in result["instances"][0]["cores"]["0"]["observations"][
                "descriptor_executions"
            ]
            if row["command_id"] == consumer
        ]
        assert executions == [], (consumer, executions)
    assert RESIDENCY_CODE not in output, output
    assert [row["code"] for row in _records(program_dir)] == ["E_AXI_RESPONSE"], (
        _records(program_dir)
    )


def test_repeat_generations_re_establish_residency(tmp_path):
    program_dir = build_program(tmp_path, REPEAT_PROGRAM)
    run = run_mock(tmp_path, "m5out", REPEAT_PROGRAM, program_dir)
    output = run.stdout + run.stderr
    assert run.returncode == 0, output
    assert "MESH_PROGRAM_DONE" in output, output
    result = result_of(program_dir)
    _assert_begin_residency(result, program_dir)
    consumer = semantic_command(program_dir, REPEAT_CONSUMER)
    planned = [key for key in _plan(result, 0) if key[0] == consumer]
    assert len(planned) >= 2, planned
    states = _terminals(result, 0, 0)
    assert all(states[key] == "completed" for key in planned), states
    produced = _write_allocations(program_dir, consumer)
    assert produced, consumer
    for instance in result["instances"]:
        assert produced <= set(_residency(instance, 0)["at_end"]), instance
    _assert_zero_resources(result)
    assert _records(program_dir) == []


def test_instance_boundary_resets_produced_residency(tmp_path):
    program_dir = build_program(tmp_path, RESIDENT_PROGRAM)
    declared = {
        value
        for values in _initial_residency(program_dir).values()
        for value in values
    }
    run = run_mock(
        tmp_path,
        "m5out",
        RESIDENT_PROGRAM,
        program_dir,
        ("--instances", "3"),
    )
    output = run.stdout + run.stderr
    assert run.returncode == 0, output
    result = result_of(program_dir)
    assert len(result["instances"]) == 3, result["instances"]
    _assert_begin_residency(result, program_dir)
    for instance in result["instances"]:
        residency = _residency(instance, 0)
        assert set(residency["at_begin"]) == declared, residency
        assert set(residency["at_end"]) - declared, residency
    assert _records(program_dir) == []


def test_compute_invalid_residency_reports_a_runtime_record(tmp_path):
    program_dir = build_program(tmp_path, RESIDENT_PROGRAM)
    declared = {
        value
        for values in _initial_residency(program_dir).values()
        for value in values
    }
    run = run_mock(
        tmp_path,
        "m5out",
        RESIDENT_PROGRAM,
        program_dir,
        ("--residency-fault", "skip_pre_resident"),
    )
    output = run.stdout + run.stderr
    assert run.returncode != 0, output
    context = _residency_record(program_dir, output, "compute")
    assert int(context["allocation_id"]) in declared, context
    assert not (program_dir / "reconciliation.json").exists()


def test_dma_invalid_residency_reports_a_runtime_record(tmp_path):
    arch = load_arch(ARCH)
    program_dir = tmp_path / "pre_resident_store"
    publish_authored_program(
        program_dir,
        _build_pre_resident_store_program(arch),
        arch,
        kind="review",
    )
    run = run_mock(
        tmp_path,
        "m5out",
        RESIDENT_PROGRAM,
        program_dir,
        ("--residency-fault", "skip_pre_resident"),
    )
    output = run.stdout + run.stderr
    assert run.returncode != 0, output
    context = _residency_record(program_dir, output, "dma")
    assert int(context["allocation_id"]) in {
        value
        for values in _initial_residency(program_dir).values()
        for value in values
    }, context


def test_cancelled_run_never_reports_a_residency_record(tmp_path):
    program_dir = build_program(tmp_path, CANCEL_PROGRAM)
    run = run_mock(
        tmp_path,
        "m5out",
        CANCEL_PROGRAM,
        program_dir,
        ("--error-descriptors", "1,4"),
    )
    output = run.stdout + run.stderr
    assert "MESH_PROGRAM_ERROR_DRAINED" in output, output
    result = result_of(program_dir)
    assert any(core["cancelled_command_ids"] for core in result["cores"]), result[
        "cores"
    ]
    assert RESIDENCY_CODE not in output, output
    assert RESIDENCY_CODE not in [row["code"] for row in _records(program_dir)]
