import hashlib
from pathlib import Path

import pytest

from mesh_ir.agent_planning import (
    ArenaAllocator,
    ArenaRegion,
    build_host_arena_object_plan,
    parameter_block_bytes,
)
from mesh_ir.abi.decoder import decode_program
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


def test_parameter_block_bytes_tracks_the_binding_count():
    workload = load_workload_plan(FIXTURES / "agent_workload_plan_two_user.json")
    rounds = [
        round_
        for user in workload.users
        for task in user.tasks
        for round_ in task.rounds
    ]
    assert {parameter_block_bytes(round_, 0) for round_ in rounds} == {256}
    assert {parameter_block_bytes(round_, 1) for round_ in rounds} == {280}
    assert {parameter_block_bytes(round_, 2) for round_ in rounds} == {304}
    with pytest.raises(PlanError):
        parameter_block_bytes(rounds[0], 0x10000)


def test_arena_plan_allocates_the_full_binding_table_capacity():
    workload = load_workload_plan(FIXTURES / "agent_workload_plan_two_user.json")
    control = load_control_plan(
        FIXTURES / "agent_control_plan_two_user.json", workload
    )
    keyed = {
        (round_.program_id, round_.profile_id): 2
        for user in workload.users
        for task in user.tasks
        for round_ in task.rounds
    }
    document = build_host_arena_object_plan(
        workload,
        control,
        "EXPLICIT_ONLY",
        regions(),
        keyed,
    )
    parameters = [
        record
        for record in by_kind(document, "PARAMETER")
        if record["command_kind"] == "GENERATE"
    ]
    assert parameters
    for record in parameters:
        assert record["allocation_bytes"] >= 304
        assert record["initial_valid_bytes"] >= 304
    spans = sorted(
        (record["base"], record["base"] + record["allocation_bytes"])
        for record in document["records"]
    )
    for (_, end), (next_start, _) in zip(spans, spans[1:]):
        assert end <= next_start


def test_host_binding_plan_projects_the_four_roles_without_npu_slots():
    from mesh_ir.host_bindings import (build_host_binding_plans,
                                       resolve_host_binding_records)

    program = decode_program(
        (Path(__file__).resolve().parents[4] / "tests/gem5/ai_mesh/fixtures"
         / "gate6" / "serving_two_tokens.mshb").read_bytes()
    )
    plans = build_host_binding_plans(
        program, [0, 0x100200000, 0x100300000, 0x100400000])
    assert len(plans) == 1
    plan = plans[0]
    assert plan.primary_input_symbol_id == 1
    assert plan.primary_output_symbol_id == 6
    assert plan.primary_kv_symbol_id == 3
    assert plan.instance_count == 4
    assert [row.symbol_id for row in plan.requirements] == [1, 2, 3, 6]
    assert [row.kind for row in plan.requirements] == [1, 4, 3, 2]
    assert plan.requirements[0].platform_address == 0
    assert plan.requirements[2].platform_address == 0
    records = resolve_host_binding_records(
        plan, 0x100200000, 128, 0x100300000, 256, 4096)
    assert [record[0] for record in records] == [1, 2, 3, 6]
    assert records[0][3:] == (0x100200000, 128)
    assert records[2][3:] == (0, 4096)
    assert records[3][3:] == (0x100300000, 256)
