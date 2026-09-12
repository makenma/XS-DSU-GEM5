import hashlib
from pathlib import Path

import pytest

from mesh_ir.agent_planning import (
    ArenaAllocator,
    ArenaRegion,
    build_host_arena_object_plan,
    parameter_block_bytes,
)
from mesh_ir.agent_workload import PlanError, load_control_plan, load_workload_plan
from mesh_ir.model import canonical_json_bytes

FIXTURES = Path(__file__).resolve().parents[4] / "tests/gem5/ai_mesh/fixtures/gate4"

PARAMETER_BASE = 0x0000000100100000
INPUT_BASE = 0x0000000101000000
OUTPUT_BASE = 0x0000000105000000
METADATA_BASE = 0x0000000109000000


def regions(input_bytes=67108864, parameter_bytes=8388608, output_bytes=67108864, metadata_bytes=8388608):
    return [
        ArenaRegion("INPUT", INPUT_BASE, input_bytes, 32),
        ArenaRegion("PARAMETER", PARAMETER_BASE, parameter_bytes, 8),
        ArenaRegion("OUTPUT", OUTPUT_BASE, output_bytes, 32),
        ArenaRegion("METADATA", METADATA_BASE, metadata_bytes, 8),
    ]


def build(input_bytes=67108864, parameter_bytes=8388608, output_bytes=67108864, metadata_bytes=8388608):
    workload = load_workload_plan(FIXTURES / "agent_workload_plan_two_user.json")
    control = load_control_plan(FIXTURES / "agent_control_plan_two_user.json", workload)
    document = build_host_arena_object_plan(
        workload,
        control,
        "EXPLICIT_ONLY",
        regions(input_bytes, parameter_bytes, output_bytes, metadata_bytes),
    )
    return document


def by_kind(document, kind):
    return [record for record in document["records"] if record["arena_kind"] == kind]


def test_parameter_block_bytes_formula():
    workload = load_workload_plan(FIXTURES / "agent_workload_plan_two_user.json")
    rounds = [
        round_
        for user in workload.users
        for task in user.tasks
        for round_ in task.rounds
    ]
    assert all(round_.deadline_tick is None for round_ in rounds)
    assert {parameter_block_bytes(round_) for round_ in rounds} == {256}


def test_arena_plan_covers_all_commands_with_kind_order():
    document = build()
    assert document["schema"] == "host_arena_object_plan_v1"
    assert len(document["records"]) == 5 * 4 + 2
    first_command = document["records"][:4]
    assert [record["arena_kind"] for record in first_command] == [
        "INPUT",
        "PARAMETER",
        "OUTPUT",
        "METADATA",
    ]
    cancel = [record for record in document["records"] if record["command_kind"] == "CANCEL"]
    release = [
        record for record in document["records"] if record["command_kind"] == "RELEASE_SESSION"
    ]
    assert [record["arena_kind"] for record in cancel] == ["PARAMETER"]
    assert [record["arena_kind"] for record in release] == ["PARAMETER"]
    assert cancel[0]["allocation_bytes"] == 160
    assert release[0]["allocation_bytes"] == 160


def test_arena_plan_first_command_bases_and_validity():
    document = build()
    first_input, first_parameter, first_output, first_metadata = document["records"][:4]
    assert first_input["base"] == INPUT_BASE
    assert first_input["allocation_bytes"] == 16384
    assert first_input["initial_valid_bytes"] == 16384
    assert first_input["alignment"] == 32
    assert first_parameter["base"] == PARAMETER_BASE
    assert first_parameter["allocation_bytes"] == 256
    assert first_parameter["initial_valid_bytes"] == 256
    assert first_parameter["alignment"] == 8
    assert first_output["base"] == OUTPUT_BASE
    assert first_output["allocation_bytes"] == 8192
    assert first_output["initial_valid_bytes"] == 0
    assert first_metadata["base"] == METADATA_BASE
    assert first_metadata["allocation_bytes"] == 512
    assert first_metadata["initial_valid_bytes"] == 0


def test_arena_allocations_are_monotonic_and_aligned():
    document = build()
    for kind in ("INPUT", "PARAMETER", "OUTPUT", "METADATA"):
        records = by_kind(document, kind)
        bases = [record["base"] for record in records]
        assert bases == sorted(bases)
        for record in records:
            assert record["base"] % record["alignment"] == 0
            assert record["allocation_bytes"] % record["alignment"] == 0
            assert record["initial_valid_bytes"] <= record["allocation_bytes"]


def test_arena_plan_digest_omits_self():
    document = build()
    body = {
        key: value
        for key, value in document.items()
        if key != "host_arena_object_digest"
    }
    assert document["host_arena_object_digest"] == hashlib.sha256(
        canonical_json_bytes(body)
    ).hexdigest()


def test_arena_overflow_fails_with_address_plan_code():
    with pytest.raises(PlanError, match="E_ADDRESS_PLAN"):
        build(input_bytes=16384)


def test_arena_padding_rejects_last_unaligned_allocation():
    allocator = ArenaAllocator(
        base=0x1000, size=1025, alignment=32, label="OUTPUT"
    )
    with pytest.raises(PlanError, match="E_ADDRESS_PLAN"):
        allocator.allocate(1025)


def test_arena_padding_covers_alignment_in_returned_allocation():
    allocator = ArenaAllocator(
        base=0x1000, size=2048, alignment=32, label="OUTPUT"
    )
    base, allocation = allocator.allocate(1025)
    assert base == 0x1000
    assert allocation == 1056
    assert allocator.cursor == 0x1000 + 1056


def test_arena_overlap_fails_with_address_plan_code():
    with pytest.raises(PlanError, match="E_ADDRESS_PLAN"):
        build(parameter_bytes=0x10000000)
