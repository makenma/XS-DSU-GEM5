import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.abi.decoder import decode_program
from mesh_ir.builder import load_arch
from mesh_ir.generated import abi as A
from mesh_ir.generated import agent_abi as G
from mesh_ir.model import MeshIrError
from mesh_ir.moe_capacity import expert_capacity
from mesh_ir.moe_fill import (
    FUNCTIONAL_BYTES,
    decode_route_entry,
    drop_fill_bytes,
    install_fill,
    pad_fill_bytes,
)
from mesh_ir.moe_materializer import (
    MaterializeConfig,
    MemberSlice,
    materialize_data_plane,
)
from mesh_ir.moe_provider import FrozenToken, apply_capacity, uniform_select
from mesh_ir.moe_uid import SemanticTokenUid

REPO = Path(__file__).resolve().parents[4]
MOE_IMAGE = REPO / "tests/gem5/ai_mesh/fixtures/gate5/moe_multi.mshb"
PLAN_DIGEST = bytes.fromhex("0123456789abcdef" * 4)
MASTER_SEED = 20260901
LAYER_ID = 2
EXPERT_COUNT = 4
TOP_K = 2


class Kernel:
    def __init__(self, combine_kind=A.MOE_COMBINE_KIND.LOCAL_REDUCE):
        self.combine_kind = combine_kind


class Layer:
    def __init__(self, layer_id=LAYER_ID, expert_count=EXPERT_COUNT,
                 top_k=TOP_K, capacity_factor_q16=0x10000,
                 overflow_policy=A.MOE_OVERFLOW_POLICY.DROP,
                 token_bytes=64, output_token_bytes=128,
                 expert_opcode=A.OPCODE.GEMM, kernel_spec_index=1):
        self.layer_id = layer_id
        self.expert_count = expert_count
        self.top_k = top_k
        self.capacity_factor_q16 = capacity_factor_q16
        self.overflow_policy = overflow_policy
        self.token_bytes = token_bytes
        self.output_token_bytes = output_token_bytes
        self.expert_opcode = expert_opcode
        self.kernel_spec_index = kernel_spec_index


def token(item_id, ordinal, member, source_rank,
          phase=G.SEMANTIC_PHASE.DECODE, user_id=1):
    uid = SemanticTokenUid(
        workload_plan_item_id=item_id, user_id=user_id, task_seq=item_id,
        repair_round=0, phase=phase,
        sequence_ordinal=ordinal if phase == G.SEMANTIC_PHASE.DECODE else 0,
        token_ordinal=ordinal if phase == G.SEMANTIC_PHASE.PREFILL else 0)
    return FrozenToken(uid=uid, member_identity=member,
                       source_rank=source_rank, linear_ordinal=ordinal)


def member(layer, identity, request_id, core_id, tokens=2, input_allocation=1,
           output_allocation=3):
    return MemberSlice(
        member_identity=identity, member_request_id=request_id,
        core_id=core_id, first_semantic_token=0, valid_token_count=tokens,
        input_allocation=input_allocation, input_offset=0,
        input_token_bytes=layer.token_bytes,
        output_allocation=output_allocation, output_offset=0,
        output_token_bytes=layer.output_token_bytes)


def config(chunk_bytes=4096):
    return MaterializeConfig(
        workload_plan_digest=PLAN_DIGEST,
        program_semantic_digest=bytes.fromhex("ab" * 32),
        p2p_chunk_bytes=chunk_bytes)


@pytest.fixture(scope="module")
def program_layer():
    program = decode_program(MOE_IMAGE.read_bytes())
    return program.moe_layer_specs[1]


@pytest.fixture(scope="module")
def program_kernel():
    program = decode_program(MOE_IMAGE.read_bytes())
    return program.moe_kernel_specs[1]


def manual_plan(layer, entries):
    from mesh_ir.moe_provider import BatchMoeSelectionPlan, SelectedTokenRoute

    routes = []
    for token, experts in entries:
        for slot, expert in enumerate(experts):
            routes.append(SelectedTokenRoute(
                uid=token.uid, member_identity=token.member_identity,
                topk_slot=slot, selected_expert_id=expert,
                source_rank=token.source_rank))
    ordered = tuple(sorted(routes, key=lambda route: route.canonical_key()))
    members = tuple(sorted({route.member_identity for route in ordered}))
    return BatchMoeSelectionPlan(
        layer_id=layer.layer_id, provider_kind="route_replay",
        provider_digest="0" * 64, workload_plan_digest=PLAN_DIGEST.hex(),
        routes=ordered, source_expert_counts=(), member_identities=members)


def build(layer, tokens, members, placement, chunk_bytes=4096,
          capacity_factor_q16=0x10000,
          overflow_policy=A.MOE_OVERFLOW_POLICY.DROP):
    layer = replace(layer, capacity_factor_q16=capacity_factor_q16,
                    overflow_policy=overflow_policy)
    plan = uniform_select(layer, tokens, PLAN_DIGEST, MASTER_SEED)
    capacity = apply_capacity(plan, layer, len(tokens))
    plane = materialize_data_plane(layer, Kernel(), plan, capacity,
                                   members, placement, config(chunk_bytes))
    return plan, capacity, plane


def test_gate5_route_buffer_records_are_64b_and_canonical(program_layer):
    tokens = [token(1, 0, "m1", 0), token(1, 1, "m1", 0),
              token(2, 0, "m2", 1)]
    members = [member(program_layer, "m1", 11, 0),
               member(program_layer, "m2", 22, 1)]
    placement = [0, 0, 1, 1]
    _, capacity, plane = build(program_layer, tokens, members, placement,
                               capacity_factor_q16=0x20000)
    entries = plane.route_buffers[0] + plane.route_buffers[1]
    assert len(entries) == 6
    assert all(len(entry) == A.MOE_RUNTIME_ROUTE_ENTRY_BYTES
               for entry in entries)
    decoded = [decode_route_entry(entry) for entry in entries]
    assert [entry["uid"].sort_key() for entry in decoded] == \
        sorted(entry["uid"].sort_key() for entry in decoded)
    for entry in decoded:
        assert entry["disposition"] in (A.ROUTE_DISPOSITION.ACCEPT,
                                        A.ROUTE_DISPOSITION.DROP)
        if entry["disposition"] == A.ROUTE_DISPOSITION.DROP:
            assert entry["assigned_expert_id"] == 0xFFFF
            assert entry["destination_core"] == 0xFFFF
        else:
            assert entry["assigned_expert_id"] == entry["selected_expert_id"]
            assert entry["destination_core"] == placement[
                entry["selected_expert_id"]]


def test_gate5_route_buffer_keeps_every_pre_capacity_copy(program_layer):
    tokens = [token(1, 0, "m1", 0), token(1, 1, "m1", 0)]
    layer = Layer(expert_count=1, top_k=1, capacity_factor_q16=0x8000)
    members = [member(layer, "m1", 11, 0, tokens=2)]
    plan = uniform_select(layer, tokens, PLAN_DIGEST, MASTER_SEED)
    capacity = apply_capacity(plan, layer, len(tokens))
    assert capacity.expert_capacity == (1,)
    plane = materialize_data_plane(layer, Kernel(), plan, capacity, members,
                                   [0], config())
    entries = [decode_route_entry(entry)
               for entry in plane.route_buffers[0]]
    assert len(entries) == 2
    assert sum(1 for entry in entries
               if entry["disposition"] == A.ROUTE_DISPOSITION.DROP) == 1


def test_gate5_expert_rows_put_padding_after_real_rows(program_layer):
    tokens = [token(1, 0, "m1", 0)]
    layer = replace(program_layer, capacity_factor_q16=0x40000,
                    overflow_policy=A.MOE_OVERFLOW_POLICY.PAD_TO_CAPACITY)
    members = [member(layer, "m1", 11, 0, tokens=1)]
    plan = uniform_select(layer, tokens, PLAN_DIGEST, MASTER_SEED)
    capacity = apply_capacity(plan, layer, len(tokens))
    assert sum(capacity.padded_slots_by_expert) > 0
    plane = materialize_data_plane(layer, Kernel(), plan, capacity, members,
                                   [0, 0, 1, 1], config())
    for expert_id in range(EXPERT_COUNT):
        rows = [row for row in plane.expert_rows
                if row["expert_id"] == expert_id]
        kinds = [row["kind"] for row in rows]
        assert kinds == sorted(kinds, key=lambda kind: kind == "pad")
        assert [row["row_ordinal"] for row in rows] == list(range(len(rows)))
    assert len(plane.pad_fills) == sum(
        1 for padded in capacity.padded_slots_by_expert if padded)
    for fill in plane.pad_fills:
        assert fill["bytes"] == len(fill["rows"]) * layer.token_bytes


def test_gate5_dispatch_chunks_are_contiguous_and_capped(program_layer):
    tokens = [token(1, index, "m1", 0) for index in range(4)]
    members = [member(program_layer, "m1", 11, 0, tokens=4)]
    _, _, plane = build(program_layer, tokens, members, [1, 1, 1, 1],
                        chunk_bytes=128, capacity_factor_q16=0x20000)
    dispatch = [chunk for chunk in plane.chunks
                if chunk["phase"] == A.MOE_TRANSFER_PHASE.DISPATCH]
    assert dispatch
    assert all(chunk["src_core"] == 0 and chunk["dst_core"] == 1
               for chunk in dispatch)
    assert all(chunk["row_bytes"] <= 128 for chunk in dispatch)
    assert sum(chunk["rows"] for chunk in dispatch) == \
        sum(1 for route in plane.expert_rows if route["kind"] == "real")
    assert [chunk["chunk_ordinal"] for chunk in dispatch] == \
        list(range(len(dispatch)))
    for chunk in dispatch:
        assert chunk["src_offset"] % chunk["row_bytes"] == 0


def test_gate5_local_rows_produce_no_transfer(program_layer):
    tokens = [token(1, 0, "m1", 0), token(1, 1, "m1", 0)]
    members = [member(program_layer, "m1", 11, 0)]
    _, _, plane = build(program_layer, tokens, members, [0, 0, 0, 0],
                        capacity_factor_q16=0x20000)
    assert plane.chunks == ()


def test_gate5_greedy_chunking_respects_allocation_breaks(program_layer):
    first = token(1, 0, "m1", 0)
    second = token(2, 0, "m2", 0)
    members = [member(program_layer, "m1", 11, 0, tokens=1,
                      input_allocation=1),
               member(program_layer, "m2", 22, 0, tokens=1,
                      input_allocation=2)]
    layer = replace(program_layer, capacity_factor_q16=0x20000)
    plan = manual_plan(layer, [(first, [0, 1]), (second, [0, 1])])
    capacity = apply_capacity(plan, layer, 2)
    assert capacity.dropped == ()
    plane = materialize_data_plane(layer, Kernel(), plan, capacity, members,
                                   [1, 1, 1, 1], config())
    dispatch = [chunk for chunk in plane.chunks
                if chunk["phase"] == A.MOE_TRANSFER_PHASE.DISPATCH and
                chunk["expert_id"] == 0]
    assert len(dispatch) == 2
    assert {chunk["src_allocation"] for chunk in dispatch} == {1, 2}
    contiguous = [token(1, 0, "m1", 0), token(1, 1, "m1", 0)]
    merged = manual_plan(layer, [(contiguous[0], [0, 1]),
                                 (contiguous[1], [0, 1])])
    merged_capacity = apply_capacity(merged, layer, 2)
    merged_plane = materialize_data_plane(
        layer, Kernel(), merged, merged_capacity,
        [member(layer, "m1", 11, 0, tokens=2, input_allocation=1)],
        [1, 1, 1, 1],
        config())
    merged_chunks = [chunk for chunk in merged_plane.chunks
                     if chunk["phase"] == A.MOE_TRANSFER_PHASE.DISPATCH and
                     chunk["expert_id"] == 0]
    assert len(merged_chunks) == 1
    assert merged_chunks[0]["rows"] == 2


def test_gate5_row_larger_than_chunk_budget_is_rejected(program_layer):
    tokens = [token(1, 0, "m1", 0)]
    members = [member(program_layer, "m1", 11, 0, tokens=1)]
    with pytest.raises(MeshIrError) as err:
        build(program_layer, tokens, members, [1, 1, 1, 1], chunk_bytes=16)
    assert err.value.code == "E_MOE_MATERIALIZATION_V"


def test_gate5_fan_in_decides_the_combine_command(program_layer):
    tokens = [token(1, 0, "m1", 0)]
    members = [member(program_layer, "m1", 11, 0, tokens=1)]
    _, _, plane = build(program_layer, tokens, members, [0, 0, 1, 1],
                        capacity_factor_q16=0x20000)
    record = plane.fan_in[0]
    assert record["fan_in"] == TOP_K
    assert record["combine_kind"] == A.MOE_COMBINE_KIND.LOCAL_REDUCE
    dropped_layer = replace(program_layer, capacity_factor_q16=0x1000,
                            overflow_policy=A.MOE_OVERFLOW_POLICY.DROP)
    plan = manual_plan(dropped_layer, [(tokens[0], [0, 0])])
    capacity = apply_capacity(plan, dropped_layer, len(tokens))
    plane = materialize_data_plane(dropped_layer, Kernel(), plan, capacity,
                                   members, [0, 0, 1, 1], config())
    assert plane.fan_in[0]["fan_in"] == 1
    assert plane.fan_in[0]["dropped_slots"]
    assert len(plane.drop_fills) == 1
    assert plane.drop_fills[0]["bytes"] == dropped_layer.output_token_bytes


def test_gate5_pad_fill_pattern_is_deterministic_and_tail_exact(program_layer):
    first = pad_fill_bytes(bytes.fromhex("ab" * 32), PLAN_DIGEST, LAYER_ID, 1,
                           0, 33)
    second = pad_fill_bytes(bytes.fromhex("ab" * 32), PLAN_DIGEST, LAYER_ID, 1,
                            0, 33)
    other_row = pad_fill_bytes(bytes.fromhex("ab" * 32), PLAN_DIGEST, LAYER_ID,
                               1, 1, 33)
    other_expert = pad_fill_bytes(bytes.fromhex("ab" * 32), PLAN_DIGEST,
                                  LAYER_ID, 2, 0, 33)
    assert first == second and len(first) == 33
    assert first != other_row and first != other_expert
    assert first != pad_fill_bytes(bytes.fromhex("ab" * 32), PLAN_DIGEST,
                                   LAYER_ID, 1, 0, 32)
    from mesh_ir.moe_fill import PAD_BLOCK_DOMAIN, pad_fill_key
    import hashlib as _hashlib
    import struct as _struct
    key = pad_fill_key(bytes.fromhex("ab" * 32), PLAN_DIGEST, LAYER_ID, 1, 0,
                       33)
    assert first[:32] == _hashlib.sha256(
        PAD_BLOCK_DOMAIN + key + _struct.pack("<Q", 0)).digest()
    assert first[32] == _hashlib.sha256(
        PAD_BLOCK_DOMAIN + key + _struct.pack("<Q", 1)).digest()[0]


def test_gate5_drop_fill_binds_the_drop_projection(program_layer):
    uid = token(1, 0, "m1", 0).uid
    entries = [(0, 3, A.ROUTE_DISPOSITION.DROP)]
    payload = drop_fill_bytes(bytes.fromhex("ab" * 32), PLAN_DIGEST, uid,
                              LAYER_ID, 128, entries)
    assert len(payload) == 128
    reordered = drop_fill_bytes(bytes.fromhex("ab" * 32), PLAN_DIGEST, uid,
                                LAYER_ID, 128,
                                [(1, 3, A.ROUTE_DISPOSITION.DROP)])
    assert payload != reordered
    assert payload == drop_fill_bytes(bytes.fromhex("ab" * 32), PLAN_DIGEST,
                                      uid, LAYER_ID, 128, list(entries))


def test_gate5_fill_modes_agree_on_shape_and_bytes(program_layer):
    row = pad_fill_bytes(bytes.fromhex("ab" * 32), PLAN_DIGEST, LAYER_ID, 0, 0,
                         31)
    functional = install_fill(FUNCTIONAL_BYTES, row)
    digest_only = install_fill(A.MOE_FILL_MODE.DIGEST_ONLY, row)
    validity_only = install_fill(A.MOE_FILL_MODE.VALIDITY_ONLY, row)
    assert functional.bytes_installed == digest_only.bytes_installed == \
        validity_only.bytes_installed == 31
    assert functional.digest == digest_only.digest == validity_only.digest
    assert functional.row_bytes() == row
    assert digest_only.row_bytes() == bytes(31)
    with pytest.raises(MeshIrError):
        install_fill(9, row)


def test_gate5_data_plane_digest_tracks_experts_not_slot_order(program_layer):
    tokens = [token(1, 0, "m1", 0)]
    members = [member(program_layer, "m1", 11, 0, tokens=1)]
    _, _, plane = build(program_layer, tokens, members, [0, 0, 1, 1],
                        capacity_factor_q16=0x20000)
    other = build(program_layer, tokens, members, [0, 1, 0, 1],
                  capacity_factor_q16=0x20000)[2]
    assert plane.digest != other.digest
    repeated = build(program_layer, tokens, members, [0, 0, 1, 1],
                     capacity_factor_q16=0x20000)[2]
    assert repeated.digest == plane.digest
    projection = plane.canonical()
    assert projection["route_buffers"][0]["entry_bytes"] == 64
    assert projection["chunks"] or projection["fan_in"]


def test_gate5_data_plane_handles_empty_population(program_layer):
    layer = Layer()
    plan = uniform_select(layer, [], PLAN_DIGEST, MASTER_SEED)
    capacity = apply_capacity(plan, layer, 0)
    plane = materialize_data_plane(layer, Kernel(), plan, capacity, [],
                                   [0, 0, 1, 1], config())
    assert plane.route_buffers == {}
    assert plane.chunks == ()
    assert plane.expert_rows == ()
    assert plane.fan_in == ()
    assert plane.pad_fills == () and plane.drop_fills == ()
    assert plane.digest


def test_gate5_capacity_geometry_matches_expert_rows(program_layer):
    tokens = [token(1, index, "m1", 0) for index in range(3)]
    layer = replace(program_layer, capacity_factor_q16=0x20000,
                    overflow_policy=A.MOE_OVERFLOW_POLICY.PAD_TO_CAPACITY)
    members = [member(layer, "m1", 11, 0, tokens=3)]
    plan = uniform_select(layer, tokens, PLAN_DIGEST, MASTER_SEED)
    capacity = apply_capacity(plan, layer, len(tokens))
    plane = materialize_data_plane(layer, Kernel(), plan, capacity, members,
                                   [0, 0, 1, 1], config())
    for expert_id in range(EXPERT_COUNT):
        rows = [row for row in plane.expert_rows
                if row["expert_id"] == expert_id]
        if not rows:
            continue
        real = sum(1 for row in rows if row["kind"] == "real")
        assert real == capacity.expert_loads[expert_id]
        assert len(rows) == capacity.expert_loads[expert_id] + \
            capacity.padded_slots_by_expert[expert_id]
        assert len(rows) == expert_capacity(len(tokens), TOP_K, 0x20000,
                                            EXPERT_COUNT)
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.abi.decoder import decode_program
from mesh_ir.builder import load_arch
from mesh_ir.generated import abi as A
from mesh_ir.generated import agent_abi as G
from mesh_ir.model import MeshIrError
from mesh_ir.moe_capacity import expert_capacity
from mesh_ir.moe_fill import (
    FUNCTIONAL_BYTES,
    decode_route_entry,
    drop_fill_bytes,
    install_fill,
    pad_fill_bytes,
)
from mesh_ir.moe_materializer import (
    MaterializeConfig,
    MemberSlice,
    materialize_data_plane,
)
from mesh_ir.moe_provider import FrozenToken, apply_capacity, uniform_select
from mesh_ir.moe_uid import SemanticTokenUid

REPO = Path(__file__).resolve().parents[4]
MOE_IMAGE = REPO / "tests/gem5/ai_mesh/fixtures/gate5/moe_multi.mshb"
PLAN_DIGEST = bytes.fromhex("0123456789abcdef" * 4)
MASTER_SEED = 20260901
LAYER_ID = 2
EXPERT_COUNT = 4
TOP_K = 2


class Kernel:
    def __init__(self, combine_kind=A.MOE_COMBINE_KIND.LOCAL_REDUCE):
        self.combine_kind = combine_kind


class Layer:
    def __init__(self, layer_id=LAYER_ID, expert_count=EXPERT_COUNT,
                 top_k=TOP_K, capacity_factor_q16=0x10000,
                 overflow_policy=A.MOE_OVERFLOW_POLICY.DROP,
                 token_bytes=64, output_token_bytes=128,
                 expert_opcode=A.OPCODE.GEMM, kernel_spec_index=1):
        self.layer_id = layer_id
        self.expert_count = expert_count
        self.top_k = top_k
        self.capacity_factor_q16 = capacity_factor_q16
        self.overflow_policy = overflow_policy
        self.token_bytes = token_bytes
        self.output_token_bytes = output_token_bytes
        self.expert_opcode = expert_opcode
        self.kernel_spec_index = kernel_spec_index


def token(item_id, ordinal, member, source_rank,
          phase=G.SEMANTIC_PHASE.DECODE, user_id=1):
    uid = SemanticTokenUid(
        workload_plan_item_id=item_id, user_id=user_id, task_seq=item_id,
        repair_round=0, phase=phase,
        sequence_ordinal=ordinal if phase == G.SEMANTIC_PHASE.DECODE else 0,
        token_ordinal=ordinal if phase == G.SEMANTIC_PHASE.PREFILL else 0)
    return FrozenToken(uid=uid, member_identity=member,
                       source_rank=source_rank, linear_ordinal=ordinal)


def member(layer, identity, request_id, core_id, tokens=2, input_allocation=1,
           output_allocation=3):
    return MemberSlice(
        member_identity=identity, member_request_id=request_id,
        core_id=core_id, first_semantic_token=0, valid_token_count=tokens,
        input_allocation=input_allocation, input_offset=0,
        input_token_bytes=layer.token_bytes,
        output_allocation=output_allocation, output_offset=0,
        output_token_bytes=layer.output_token_bytes)


def config(chunk_bytes=4096):
    return MaterializeConfig(
        workload_plan_digest=PLAN_DIGEST,
        program_semantic_digest=bytes.fromhex("ab" * 32),
        p2p_chunk_bytes=chunk_bytes)


@pytest.fixture(scope="module")
def program_layer():
    program = decode_program(MOE_IMAGE.read_bytes())
    return program.moe_layer_specs[1]


@pytest.fixture(scope="module")
def program_kernel():
    program = decode_program(MOE_IMAGE.read_bytes())
    return program.moe_kernel_specs[1]


def manual_plan(layer, entries):
    from mesh_ir.moe_provider import BatchMoeSelectionPlan, SelectedTokenRoute

    routes = []
    for token, experts in entries:
        for slot, expert in enumerate(experts):
            routes.append(SelectedTokenRoute(
                uid=token.uid, member_identity=token.member_identity,
                topk_slot=slot, selected_expert_id=expert,
                source_rank=token.source_rank))
    ordered = tuple(sorted(routes, key=lambda route: route.canonical_key()))
    members = tuple(sorted({route.member_identity for route in ordered}))
    return BatchMoeSelectionPlan(
        layer_id=layer.layer_id, provider_kind="route_replay",
        provider_digest="0" * 64, workload_plan_digest=PLAN_DIGEST.hex(),
        routes=ordered, source_expert_counts=(), member_identities=members)


def build(layer, tokens, members, placement, chunk_bytes=4096,
          capacity_factor_q16=0x10000,
          overflow_policy=A.MOE_OVERFLOW_POLICY.DROP):
    layer = replace(layer, capacity_factor_q16=capacity_factor_q16,
                    overflow_policy=overflow_policy)
    plan = uniform_select(layer, tokens, PLAN_DIGEST, MASTER_SEED)
    capacity = apply_capacity(plan, layer, len(tokens))
    plane = materialize_data_plane(layer, Kernel(), plan, capacity,
                                   members, placement, config(chunk_bytes))
    return plan, capacity, plane


def test_gate5_route_buffer_records_are_64b_and_canonical(program_layer):
    tokens = [token(1, 0, "m1", 0), token(1, 1, "m1", 0),
              token(2, 0, "m2", 1)]
    members = [member(program_layer, "m1", 11, 0),
               member(program_layer, "m2", 22, 1)]
    placement = [0, 0, 1, 1]
    _, capacity, plane = build(program_layer, tokens, members, placement,
                               capacity_factor_q16=0x20000)
    entries = plane.route_buffers[0] + plane.route_buffers[1]
    assert len(entries) == 6
    assert all(len(entry) == A.MOE_RUNTIME_ROUTE_ENTRY_BYTES
               for entry in entries)
    decoded = [decode_route_entry(entry) for entry in entries]
    assert [entry["uid"].sort_key() for entry in decoded] == \
        sorted(entry["uid"].sort_key() for entry in decoded)
    for entry in decoded:
        assert entry["disposition"] in (A.ROUTE_DISPOSITION.ACCEPT,
                                        A.ROUTE_DISPOSITION.DROP)
        if entry["disposition"] == A.ROUTE_DISPOSITION.DROP:
            assert entry["assigned_expert_id"] == 0xFFFF
            assert entry["destination_core"] == 0xFFFF
        else:
            assert entry["assigned_expert_id"] == entry["selected_expert_id"]
            assert entry["destination_core"] == placement[
                entry["selected_expert_id"]]


def test_gate5_route_buffer_keeps_every_pre_capacity_copy(program_layer):
    tokens = [token(1, 0, "m1", 0), token(1, 1, "m1", 0)]
    layer = Layer(expert_count=1, top_k=1, capacity_factor_q16=0x8000)
    members = [member(layer, "m1", 11, 0, tokens=2)]
    plan = uniform_select(layer, tokens, PLAN_DIGEST, MASTER_SEED)
    capacity = apply_capacity(plan, layer, len(tokens))
    assert capacity.expert_capacity == (1,)
    plane = materialize_data_plane(layer, Kernel(), plan, capacity, members,
                                   [0], config())
    entries = [decode_route_entry(entry)
               for entry in plane.route_buffers[0]]
    assert len(entries) == 2
    assert sum(1 for entry in entries
               if entry["disposition"] == A.ROUTE_DISPOSITION.DROP) == 1


def test_gate5_expert_rows_put_padding_after_real_rows(program_layer):
    tokens = [token(1, 0, "m1", 0)]
    layer = replace(program_layer, capacity_factor_q16=0x40000,
                    overflow_policy=A.MOE_OVERFLOW_POLICY.PAD_TO_CAPACITY)
    members = [member(layer, "m1", 11, 0, tokens=1)]
    plan = uniform_select(layer, tokens, PLAN_DIGEST, MASTER_SEED)
    capacity = apply_capacity(plan, layer, len(tokens))
    assert sum(capacity.padded_slots_by_expert) > 0
    plane = materialize_data_plane(layer, Kernel(), plan, capacity, members,
                                   [0, 0, 1, 1], config())
    for expert_id in range(EXPERT_COUNT):
        rows = [row for row in plane.expert_rows
                if row["expert_id"] == expert_id]
        kinds = [row["kind"] for row in rows]
        assert kinds == sorted(kinds, key=lambda kind: kind == "pad")
        assert [row["row_ordinal"] for row in rows] == list(range(len(rows)))
    assert len(plane.pad_fills) == sum(
        1 for padded in capacity.padded_slots_by_expert if padded)
    for fill in plane.pad_fills:
        assert fill["bytes"] == len(fill["rows"]) * layer.token_bytes


def test_gate5_dispatch_chunks_are_contiguous_and_capped(program_layer):
    tokens = [token(1, index, "m1", 0) for index in range(4)]
    members = [member(program_layer, "m1", 11, 0, tokens=4)]
    _, _, plane = build(program_layer, tokens, members, [1, 1, 1, 1],
                        chunk_bytes=128, capacity_factor_q16=0x20000)
    dispatch = [chunk for chunk in plane.chunks
                if chunk["phase"] == A.MOE_TRANSFER_PHASE.DISPATCH]
    assert dispatch
    assert all(chunk["src_core"] == 0 and chunk["dst_core"] == 1
               for chunk in dispatch)
    assert all(chunk["row_bytes"] <= 128 for chunk in dispatch)
    assert sum(chunk["rows"] for chunk in dispatch) == \
        sum(1 for route in plane.expert_rows if route["kind"] == "real")
    assert [chunk["chunk_ordinal"] for chunk in dispatch] == \
        list(range(len(dispatch)))
    for chunk in dispatch:
        assert chunk["src_offset"] % chunk["row_bytes"] == 0


def test_gate5_local_rows_produce_no_transfer(program_layer):
    tokens = [token(1, 0, "m1", 0), token(1, 1, "m1", 0)]
    members = [member(program_layer, "m1", 11, 0)]
    _, _, plane = build(program_layer, tokens, members, [0, 0, 0, 0],
                        capacity_factor_q16=0x20000)
    assert plane.chunks == ()


def test_gate5_greedy_chunking_respects_allocation_breaks(program_layer):
    first = token(1, 0, "m1", 0)
    second = token(2, 0, "m2", 0)
    members = [member(program_layer, "m1", 11, 0, tokens=1,
                      input_allocation=1),
               member(program_layer, "m2", 22, 0, tokens=1,
                      input_allocation=2)]
    layer = replace(program_layer, capacity_factor_q16=0x20000)
    plan = manual_plan(layer, [(first, [0, 1]), (second, [0, 1])])
    capacity = apply_capacity(plan, layer, 2)
    assert capacity.dropped == ()
    plane = materialize_data_plane(layer, Kernel(), plan, capacity, members,
                                   [1, 1, 1, 1], config())
    dispatch = [chunk for chunk in plane.chunks
                if chunk["phase"] == A.MOE_TRANSFER_PHASE.DISPATCH and
                chunk["expert_id"] == 0]
    assert len(dispatch) == 2
    assert {chunk["src_allocation"] for chunk in dispatch} == {1, 2}
    contiguous = [token(1, 0, "m1", 0), token(1, 1, "m1", 0)]
    merged = manual_plan(layer, [(contiguous[0], [0, 1]),
                                 (contiguous[1], [0, 1])])
    merged_capacity = apply_capacity(merged, layer, 2)
    merged_plane = materialize_data_plane(
        layer, Kernel(), merged, merged_capacity,
        [member(layer, "m1", 11, 0, tokens=2, input_allocation=1)],
        [1, 1, 1, 1],
        config())
    merged_chunks = [chunk for chunk in merged_plane.chunks
                     if chunk["phase"] == A.MOE_TRANSFER_PHASE.DISPATCH and
                     chunk["expert_id"] == 0]
    assert len(merged_chunks) == 1
    assert merged_chunks[0]["rows"] == 2


def test_gate5_row_larger_than_chunk_budget_is_rejected(program_layer):
    tokens = [token(1, 0, "m1", 0)]
    members = [member(program_layer, "m1", 11, 0, tokens=1)]
    with pytest.raises(MeshIrError) as err:
        build(program_layer, tokens, members, [1, 1, 1, 1], chunk_bytes=16)
    assert err.value.code == "E_MOE_MATERIALIZATION_V"


def test_gate5_fan_in_decides_the_combine_command(program_layer):
    tokens = [token(1, 0, "m1", 0)]
    members = [member(program_layer, "m1", 11, 0, tokens=1)]
    _, _, plane = build(program_layer, tokens, members, [0, 0, 1, 1],
                        capacity_factor_q16=0x20000)
    record = plane.fan_in[0]
    assert record["fan_in"] == TOP_K
    assert record["combine_kind"] == A.MOE_COMBINE_KIND.LOCAL_REDUCE
    dropped_layer = replace(program_layer, capacity_factor_q16=0x1000,
                            overflow_policy=A.MOE_OVERFLOW_POLICY.DROP)
    plan = manual_plan(dropped_layer, [(tokens[0], [0, 0])])
    capacity = apply_capacity(plan, dropped_layer, len(tokens))
    plane = materialize_data_plane(dropped_layer, Kernel(), plan, capacity,
                                   members, [0, 0, 1, 1], config())
    assert plane.fan_in[0]["fan_in"] == 1
    assert plane.fan_in[0]["dropped_slots"]
    assert len(plane.drop_fills) == 1
    assert plane.drop_fills[0]["bytes"] == dropped_layer.output_token_bytes


def test_gate5_pad_fill_pattern_is_deterministic_and_tail_exact(program_layer):
    first = pad_fill_bytes(bytes.fromhex("ab" * 32), PLAN_DIGEST, LAYER_ID, 1,
                           0, 33)
    second = pad_fill_bytes(bytes.fromhex("ab" * 32), PLAN_DIGEST, LAYER_ID, 1,
                            0, 33)
    other_row = pad_fill_bytes(bytes.fromhex("ab" * 32), PLAN_DIGEST, LAYER_ID,
                               1, 1, 33)
    other_expert = pad_fill_bytes(bytes.fromhex("ab" * 32), PLAN_DIGEST,
                                  LAYER_ID, 2, 0, 33)
    assert first == second and len(first) == 33
    assert first != other_row and first != other_expert
    assert first != pad_fill_bytes(bytes.fromhex("ab" * 32), PLAN_DIGEST,
                                   LAYER_ID, 1, 0, 32)
    from mesh_ir.moe_fill import PAD_BLOCK_DOMAIN, pad_fill_key
    import hashlib as _hashlib
    import struct as _struct
    key = pad_fill_key(bytes.fromhex("ab" * 32), PLAN_DIGEST, LAYER_ID, 1, 0,
                       33)
    assert first[:32] == _hashlib.sha256(
        PAD_BLOCK_DOMAIN + key + _struct.pack("<Q", 0)).digest()
    assert first[32] == _hashlib.sha256(
        PAD_BLOCK_DOMAIN + key + _struct.pack("<Q", 1)).digest()[0]


def test_gate5_drop_fill_binds_the_drop_projection(program_layer):
    uid = token(1, 0, "m1", 0).uid
    entries = [(0, 3, A.ROUTE_DISPOSITION.DROP)]
    payload = drop_fill_bytes(bytes.fromhex("ab" * 32), PLAN_DIGEST, uid,
                              LAYER_ID, 128, entries)
    assert len(payload) == 128
    reordered = drop_fill_bytes(bytes.fromhex("ab" * 32), PLAN_DIGEST, uid,
                                LAYER_ID, 128,
                                [(1, 3, A.ROUTE_DISPOSITION.DROP)])
    assert payload != reordered
    assert payload == drop_fill_bytes(bytes.fromhex("ab" * 32), PLAN_DIGEST,
                                      uid, LAYER_ID, 128, list(entries))


def test_gate5_fill_modes_agree_on_shape_and_bytes(program_layer):
    row = pad_fill_bytes(bytes.fromhex("ab" * 32), PLAN_DIGEST, LAYER_ID, 0, 0,
                         31)
    functional = install_fill(FUNCTIONAL_BYTES, row)
    digest_only = install_fill(A.MOE_FILL_MODE.DIGEST_ONLY, row)
    validity_only = install_fill(A.MOE_FILL_MODE.VALIDITY_ONLY, row)
    assert functional.bytes_installed == digest_only.bytes_installed == \
        validity_only.bytes_installed == 31
    assert functional.digest == digest_only.digest == validity_only.digest
    assert functional.row_bytes() == row
    assert digest_only.row_bytes() == bytes(31)
    with pytest.raises(MeshIrError):
        install_fill(9, row)


def test_gate5_data_plane_digest_tracks_experts_not_slot_order(program_layer):
    tokens = [token(1, 0, "m1", 0)]
    members = [member(program_layer, "m1", 11, 0, tokens=1)]
    _, _, plane = build(program_layer, tokens, members, [0, 0, 1, 1],
                        capacity_factor_q16=0x20000)
    other = build(program_layer, tokens, members, [0, 1, 0, 1],
                  capacity_factor_q16=0x20000)[2]
    assert plane.digest != other.digest
    repeated = build(program_layer, tokens, members, [0, 0, 1, 1],
                     capacity_factor_q16=0x20000)[2]
    assert repeated.digest == plane.digest
    projection = plane.canonical()
    assert projection["route_buffers"][0]["entry_bytes"] == 64
    assert projection["chunks"] or projection["fan_in"]


def test_gate5_data_plane_handles_empty_population(program_layer):
    layer = Layer()
    plan = uniform_select(layer, [], PLAN_DIGEST, MASTER_SEED)
    capacity = apply_capacity(plan, layer, 0)
    plane = materialize_data_plane(layer, Kernel(), plan, capacity, [],
                                   [0, 0, 1, 1], config())
    assert plane.route_buffers == {}
    assert plane.chunks == ()
    assert plane.expert_rows == ()
    assert plane.fan_in == ()
    assert plane.pad_fills == () and plane.drop_fills == ()
    assert plane.digest


def test_gate5_capacity_geometry_matches_expert_rows(program_layer):
    tokens = [token(1, index, "m1", 0) for index in range(3)]
    layer = replace(program_layer, capacity_factor_q16=0x20000,
                    overflow_policy=A.MOE_OVERFLOW_POLICY.PAD_TO_CAPACITY)
    members = [member(layer, "m1", 11, 0, tokens=3)]
    plan = uniform_select(layer, tokens, PLAN_DIGEST, MASTER_SEED)
    capacity = apply_capacity(plan, layer, len(tokens))
    plane = materialize_data_plane(layer, Kernel(), plan, capacity, members,
                                   [0, 0, 1, 1], config())
    for expert_id in range(EXPERT_COUNT):
        rows = [row for row in plane.expert_rows
                if row["expert_id"] == expert_id]
        if not rows:
            continue
        real = sum(1 for row in rows if row["kind"] == "real")
        assert real == capacity.expert_loads[expert_id]
        assert len(rows) == capacity.expert_loads[expert_id] + \
            capacity.padded_slots_by_expert[expert_id]
        assert len(rows) == expert_capacity(len(tokens), TOP_K, 0x20000,
                                            EXPERT_COUNT)


def test_gate5_member_rows_must_match_the_layer_token_rows(program_layer):
    tokens = [token(1, 0, "m1", 0)]
    members = [replace(member(program_layer, "m1", 11, 0, tokens=1),
                       input_token_bytes=program_layer.token_bytes * 2)]
    with pytest.raises(MeshIrError) as err:
        build(program_layer, tokens, members, [1, 1, 1, 1])
    assert err.value.code == "E_MOE_MATERIALIZATION_V"
    assert "member rows must match" in err.value.message
