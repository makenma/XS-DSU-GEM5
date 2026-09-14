import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.abi.decoder import decode_program
from mesh_ir.generated import abi as A
from mesh_ir.generated import agent_abi as G
from mesh_ir.model import MeshIrError
from mesh_ir.moe_materializer import MaterializeConfig, MemberSlice
from mesh_ir.moe_oracle import (
    TAMPER_KINDS,
    fill_traffic_id,
    MeshTopology,
    Packetization,
    command_expectations,
    compare_commands,
    compare_descriptors,
    descriptor_expectations,
    moe_traffic_lanes,
    tamper,
    verify_overlay_document,
    verify_result,
)
from mesh_ir.moe_overlay_runtime import (
    materialize_overlay,
    overlay_objects_projection,
)
from mesh_ir.moe_provider import (
    BatchMoeSelectionPlan,
    FrozenToken,
    SelectedTokenRoute,
    apply_capacity,
    uniform_select,
)
from mesh_ir.moe_uid import SemanticTokenUid

REPO = Path(__file__).resolve().parents[4]
FIXTURES = REPO / "tests/gem5/ai_mesh/fixtures/gate5"
PLAN_DIGEST = bytes.fromhex("01" * 32)
PACKETIZATION = Packetization(data_bytes=32, burst_beats=16, header_bytes=16,
                              flit_bytes=32)


class Layer:
    def __init__(self, layer_id=1, expert_count=2, top_k=1,
                 capacity_factor_q16=0x10000, token_bytes=64,
                 output_token_bytes=128):
        self.layer_id = layer_id
        self.expert_count = expert_count
        self.top_k = top_k
        self.capacity_factor_q16 = capacity_factor_q16
        self.overflow_policy = A.MOE_OVERFLOW_POLICY.DROP
        self.token_bytes = token_bytes
        self.output_token_bytes = output_token_bytes


class Kernel:
    n = 64


def token(item_id, ordinal, member, source_rank=0):
    uid = SemanticTokenUid(
        workload_plan_item_id=item_id, user_id=1, task_seq=item_id,
        repair_round=0, phase=G.SEMANTIC_PHASE.DECODE,
        sequence_ordinal=ordinal, token_ordinal=0)
    return FrozenToken(uid=uid, member_identity=member,
                       source_rank=source_rank, linear_ordinal=ordinal)


def member(identity, core_id, request_id):
    return MemberSlice(
        member_identity=identity, member_request_id=request_id,
        core_id=core_id, first_semantic_token=0, valid_token_count=1,
        input_allocation=1, input_offset=0, input_token_bytes=64,
        output_allocation=3, output_offset=0, output_token_bytes=128)


def plan_of(layer, tokens, experts):
    routes = tuple(sorted(
        [SelectedTokenRoute(uid=item.uid, member_identity=item.member_identity,
                            topk_slot=slot, selected_expert_id=expert,
                            source_rank=item.source_rank)
         for item, expert in zip(tokens, experts)
         for slot in range(layer.top_k)],
        key=lambda route: route.canonical_key()))
    return BatchMoeSelectionPlan(
        layer_id=layer.layer_id, provider_kind="uniform_smoke",
        provider_digest="00" * 32, workload_plan_digest=PLAN_DIGEST.hex(),
        routes=routes, source_expert_counts=())


def test_gate5_oracle_lanes_match_a_hand_computed_two_core_example():
    layer = Layer()
    tokens = [token(1, 0, "m1"), token(2, 0, "m2")]
    members = [member("m1", 0, 11), member("m2", 1, 12)]
    placement = [0, 1]
    plan = plan_of(layer, tokens, [0, 1])
    capacity = apply_capacity(plan, layer, len(tokens))
    lanes = moe_traffic_lanes(layer, Kernel(), plan, capacity, members,
                              placement, {0: 4096, 1: 4096}, PACKETIZATION,
                              MeshTopology(1, 2))
    values = lanes.lanes
    assert values["dispatch_all_route_bytes"] == 128
    assert values["dispatch_local_sram_bytes"] == 128
    assert values["dispatch_remote_dma_bytes"] == 0
    assert values["combine_all_route_bytes"] == 256
    assert values["combine_local_sram_bytes"] == 256
    assert values["combine_remote_dma_bytes"] == 0
    assert values["padding_compute_slots"] == 0
    assert values["expert_input_sram_rbytes"] == 128
    assert values["expert_output_sram_wbytes"] == 256
    assert values["combine_gather_sram_rbytes"] == 256
    assert values["combine_output_sram_wbytes"] == 256
    assert values["copy_through_commands"] == 2
    assert values["local_reduce_commands"] == 0
    assert values["combine_reduce_ops"] == 0
    assert values["weight_sram_read_bytes"] == 8192
    assert values["route_metadata_fill_sram_bytes"] == 128
    assert lanes.per_peer_logical_bytes == {}
    assert lanes.per_link_wire_bytes == {}
    assert lanes.route_entries_by_core == {0: 1, 1: 1}


def test_gate5_oracle_expands_remote_segments_hop_by_hop():
    layer = Layer(expert_count=2)
    tokens = [token(1, 0, "m1"), token(2, 0, "m2")]
    members = [member("m1", 0, 11), member("m2", 3, 12)]
    placement = [1, 0]
    plan = plan_of(layer, tokens, [0, 1])
    capacity = apply_capacity(plan, layer, len(tokens))
    lanes = moe_traffic_lanes(layer, Kernel(), plan, capacity, members,
                              placement, {0: 4096, 1: 4096}, PACKETIZATION,
                              MeshTopology(2, 2))
    assert lanes.lanes["dispatch_remote_dma_bytes"] == 128
    assert lanes.lanes["combine_remote_dma_bytes"] == 256
    peers = {(row["phase"], row["src_core"], row["dst_core"]): row["bytes"]
             for row in lanes.canonical()["per_peer_logical_bytes"]}
    assert peers[(A.MOE_TRANSFER_PHASE.DISPATCH, 0, 1)] == 64
    assert peers[(A.MOE_TRANSFER_PHASE.COMBINE, 1, 0)] == 128
    assert peers[(A.MOE_TRANSFER_PHASE.DISPATCH, 3, 0)] == 64
    assert peers[(A.MOE_TRANSFER_PHASE.COMBINE, 0, 3)] == 128
    links = {((row["src_router"][0], row["src_router"][1]),
              (row["dst_router"][0], row["dst_router"][1])): row["bytes"]
             for row in lanes.canonical()["per_link_wire_bytes"]}
    assert links == {((0, 0), (0, 1)): 256, ((0, 0), (1, 1)): 512}
    assert PACKETIZATION.wire_bytes(64) == 96
    assert PACKETIZATION.wire_bytes(128) == 160
    assert sum(links.values()) == 768


def test_gate5_oracle_counts_padded_tokens():
    layer = Layer(expert_count=2, top_k=1, capacity_factor_q16=0x20000)
    layer.overflow_policy = A.MOE_OVERFLOW_POLICY.PAD_TO_CAPACITY
    tokens = [token(1, 0, "m1")]
    members = [member("m1", 0, 11)]
    plan = plan_of(layer, tokens, [0])
    capacity = apply_capacity(plan, layer, len(tokens))
    assert capacity.padded_slots_by_expert == (0, 1)
    lanes = moe_traffic_lanes(layer, Kernel(), plan, capacity, members, [0, 1],
                              {0: 4096, 1: 4096}, PACKETIZATION,
                              MeshTopology(1, 2))
    assert lanes.lanes["padding_compute_slots"] == 1
    assert lanes.lanes["padding_fill_sram_wbytes"] == 64
    assert lanes.lanes["expert_input_sram_rbytes"] == 128
    assert lanes.lanes["expert_output_sram_wbytes"] == 256
    assert lanes.lanes["padding_discarded_out_bytes"] == 128
    assert lanes.lanes["fully_dropped_fill_sram_wbytes"] == 0
    assert lanes.lanes["copy_through_commands"] == 1


def test_gate5_oracle_counts_fully_dropped_tokens():
    layer = Layer(expert_count=1, top_k=1, capacity_factor_q16=0x8000)
    tokens = [token(1, 0, "m1"), token(1, 1, "m1")]
    members = [MemberSlice(
        member_identity="m1", member_request_id=11, core_id=0,
        first_semantic_token=0, valid_token_count=2, input_allocation=1,
        input_offset=0, input_token_bytes=64, output_allocation=3,
        output_offset=0, output_token_bytes=128)]
    routes = tuple(
        SelectedTokenRoute(uid=item.uid, member_identity="m1", topk_slot=0,
                           selected_expert_id=0, source_rank=0)
        for item in tokens)
    plan = BatchMoeSelectionPlan(
        layer_id=1, provider_kind="uniform_smoke", provider_digest="00" * 32,
        workload_plan_digest=PLAN_DIGEST.hex(), routes=routes,
        source_expert_counts=())
    capacity = apply_capacity(plan, layer, len(tokens))
    assert len(capacity.accepted) == 1 and len(capacity.dropped) == 1
    lanes = moe_traffic_lanes(layer, Kernel(), plan, capacity, members, [0],
                              {0: 4096}, PACKETIZATION, MeshTopology(1, 1))
    assert lanes.lanes["fully_dropped_fill_sram_wbytes"] == 128
    assert lanes.lanes["combine_output_sram_wbytes"] == 128
    assert lanes.lanes["combine_gather_sram_rbytes"] == 128
    assert lanes.lanes["copy_through_commands"] == 1
    assert lanes.lanes["local_reduce_commands"] == 0
    assert lanes.lanes["weight_sram_read_bytes"] == 4096
    assert lanes.lanes["route_metadata_fill_sram_bytes"] == 128


def _fixture_overlay(name):
    document = json.loads((FIXTURES / (name + ".json")).read_text())
    return document


def test_gate5_oracle_accepts_every_materialized_fixture_projection():
    for name in ("moe_overlay_objects", "moe_dual_overlay_objects",
                 "moe_dual_overlay_cached", "moe_quad_overlay_objects",
                 "moe_quad_replay_overlay_objects",
                 "moe_quad_hotspot_overlay_objects"):
        document = _fixture_overlay(name)
        assert verify_overlay_document(document) == (), name


def test_gate5_oracle_recomputes_the_dual_fixture_traffic():
    program = decode_program((FIXTURES / "moe_dual.mshb").read_bytes())
    layer = program.moe_layer_specs[0]
    kernel = program.moe_kernel_specs[layer.kernel_spec_index]
    regions = program.moe_dynamic_regions[:layer.dynamic_region_count]
    tokens = [token(1, 0, "m1")]
    members = [MemberSlice(
        member_identity="m1", member_request_id=11, core_id=0,
        first_semantic_token=0, valid_token_count=1, input_allocation=1,
        input_offset=0, input_token_bytes=64, output_allocation=3,
        output_offset=0, output_token_bytes=128)]
    placement = [spec.core_id for spec in program.moe_expert_specs]
    plan = uniform_select(layer, tokens, PLAN_DIGEST, 7)
    capacity = apply_capacity(plan, layer, len(tokens))
    bounds = {
        "per_region": {
            region.region_id: {
                A.MESH_OBJECT_KIND.COMMAND: 64, A.MESH_OBJECT_KIND.EVENT: 64,
                A.MESH_OBJECT_KIND.DESCRIPTOR: 64,
                A.MESH_OBJECT_KIND.ALLOCATION: 64,
            } for region in regions},
        "group": {
            A.MESH_OBJECT_KIND.COMMAND: 128, A.MESH_OBJECT_KIND.EVENT: 128,
            A.MESH_OBJECT_KIND.DESCRIPTOR: 128,
            A.MESH_OBJECT_KIND.TRANSFER: 32,
            A.MESH_OBJECT_KIND.ALLOCATION: 128,
        },
    }
    result = materialize_overlay(
        layer, kernel, plan, capacity, members, placement, regions,
        {region.region_id: (region.scratch_offset, region.scratch_bytes,
                            region.scratch_alignment)
         for region in regions},
        MaterializeConfig(workload_plan_digest=PLAN_DIGEST,
                          program_semantic_digest=bytes.fromhex(
                              program.semantic_sha256()),
                          p2p_chunk_bytes=4096),
        bounds, program.moe_expert_specs)
    weight_bytes = {spec.expert_id: spec.weight_bytes
                    for spec in program.moe_expert_specs}
    lanes = moe_traffic_lanes(layer, kernel, plan, capacity, members,
                              placement, weight_bytes, PACKETIZATION,
                              MeshTopology(1, 2))
    projection = overlay_objects_projection(result.overlay)
    document = {"objects": []}
    reads = fills = p2p = 0
    for row in projection["descriptors"]:
        if row["moe_kind"] == A.MOE_DESCRIPTOR_KIND.STREAMED_WEIGHT:
            reads += row["valid_bytes"]
        elif row["moe_kind"] in (A.MOE_DESCRIPTOR_KIND.ROUTE_FILL,
                                 A.MOE_DESCRIPTOR_KIND.PAD_FILL,
                                 A.MOE_DESCRIPTOR_KIND.DROPPED_TOKEN_FILL):
            fills += row["valid_bytes"]
        elif row["src_view_ordinal"] != 0:
            p2p += row["valid_bytes"]
    assert reads == lanes.lanes["weight_sram_read_bytes"]
    assert p2p == (lanes.lanes["dispatch_remote_dma_bytes"] +
                   lanes.lanes["combine_remote_dma_bytes"])
    route_fill = [row for row in projection["descriptors"]
                  if row["moe_kind"] == A.MOE_DESCRIPTOR_KIND.ROUTE_FILL]
    assert sum(row["valid_bytes"] for row in route_fill) == \
        lanes.lanes["route_metadata_fill_sram_bytes"]
    assert fills == lanes.lanes["route_metadata_fill_sram_bytes"]
    del document


def _synthetic_result(document, fills=()):
    expectations = descriptor_expectations(document, PACKETIZATION)
    transport = []
    for (region_id, ordinal), expectation in sorted(expectations.items()):
        row = {"region_id": region_id, "descriptor_id": ordinal,
               "domain": 1, "object_kind": A.MESH_OBJECT_KIND.DESCRIPTOR,
               "dma_kind": expectation["dma_kind"],
               "read_bytes": 0, "fill_bytes": 0, "p2p_bytes": 0,
               "read_bursts": 0, "write_bursts": 0, "p2p_bursts": 0,
               "error_code": 0}
        row[expectation["payload_field"]] = expectation["payload_bytes"]
        row[expectation["burst_field"]] = expectation["bursts"]
        transport.append(row)
    return {"transport": transport,
            "moe": {"regions": [], "cache_fills": list(fills)},
            "instances": []}


def test_gate5_oracle_accepts_a_matching_trace_and_rejects_tampering():
    document = _fixture_overlay("moe_dual_overlay_objects")
    expectations = descriptor_expectations(document, PACKETIZATION)
    counts = command_expectations(document)
    result = _synthetic_result(document)
    result["moe"]["regions"] = [
        {"region_id": region_id, "core_id": core_id,
         "issued": counts[core_id], "completed": counts[core_id]}
        for region_id, core_id in ((1, 0), (2, 1))]
    failures, ok = verify_result(expectations, counts, result)
    assert ok and failures == ()
    broken = json.loads(json.dumps(result))
    first = broken["transport"][0]
    field = descriptor_expectations(document, PACKETIZATION)[
        (first["region_id"], first["descriptor_id"])]["payload_field"]
    first[field] += 16
    failures, ok = verify_result(expectations, counts, broken)
    assert not ok and failures


DOCUMENT_TAMPERS = ("route_record", "dag_edge", "view_validity",
                    "terminal_record", "latch_commit")
TRACE_TAMPERS = ("descriptor_bytes", "dma_kind", "typed_owner",
                 "peer_actual", "fill_id", "slot_id", "subscriber")


def test_gate5_oracle_rejects_every_tamper_class():
    document = _fixture_overlay("moe_dual_overlay_objects")
    counts = command_expectations(document)
    pristine = _synthetic_result(document)
    pristine["moe"]["regions"] = [
        {"region_id": region_id, "core_id": core_id,
         "issued": counts[core_id], "completed": counts[core_id]}
        for region_id, core_id in ((1, 0), (2, 1))]
    key_fields = {"core_id": 0, "cache_partition_id": 1,
                  "weight_tag_index": 1, "cache_generation": 0,
                  "fill_incarnation": 1, "slot_id": 0}
    fill_row = {
        "core_id": 0, "weight_tag_index": 1, "fill_incarnation": 1,
        "bytes": 4096, "committed_bytes": 4096, "slot_id": 0,
        "address": 0xC0000, "done_tick": 10, "key_fields": key_fields,
        "fill_traffic_id": fill_traffic_id(key_fields),
    }
    pristine["moe"]["cache_fills"] = [fill_row]
    pristine["transport"].append({
        "region_id": 1, "descriptor_id": 1, "domain": 2,
        "object_kind": A.MESH_OBJECT_KIND.DESCRIPTOR,
        "dma_kind": A.DMA_KIND.LOAD, "read_bytes": 4096, "fill_bytes": 0,
        "p2p_bytes": 0, "read_bursts": 8, "write_bursts": 0,
        "p2p_bursts": 0, "error_code": 0})
    rejected = []
    for kind in DOCUMENT_TAMPERS:
        candidate = tamper({"objects": document["objects"]}, kind)
        document_failures = verify_overlay_document(candidate)
        try:
            _failures, trace_ok = verify_result(
                descriptor_expectations(candidate, PACKETIZATION),
                command_expectations(candidate), pristine)
        except MeshIrError:
            trace_ok = False
        assert document_failures or not trace_ok, kind
        rejected.append(kind)
    expectations = descriptor_expectations(document, PACKETIZATION)
    geometry = (0xC0000, 4096)
    for kind in TRACE_TAMPERS:
        candidate = tamper(pristine, kind)
        try:
            _failures, trace_ok = verify_result(
                expectations, counts, candidate, cache_geometry=geometry)
        except MeshIrError:
            trace_ok = False
        assert not trace_ok, kind
        rejected.append(kind)
    assert sorted(rejected) == sorted(TAMPER_KINDS)


def test_gate5_oracle_rejects_an_unknown_worker_tamper():
    with pytest.raises(MeshIrError) as err:
        tamper({"objects": []}, "unknown")
    assert err.value.code == "E_MOE_TRAFFIC"


def test_gate5_oracle_reports_duplicate_fill_identities():
    document = _fixture_overlay("moe_dual_overlay_objects")
    counts = command_expectations(document)
    result = _synthetic_result(document)
    fill_row = {
        "core_id": 0, "weight_tag_index": 1, "fill_incarnation": 1,
        "bytes": 4096, "committed_bytes": 4096, "slot_id": 0,
        "address": 0xC0000, "done_tick": 10, "fill_traffic_id": "bb" * 32,
    }
    result["moe"]["cache_fills"] = [fill_row, dict(fill_row)]
    failures = compare_descriptors(
        descriptor_expectations(document, PACKETIZATION), result)
    assert failures == []
    with pytest.raises(MeshIrError) as err:
        verify_result(descriptor_expectations(document, PACKETIZATION),
                      counts, result)
    assert err.value.code == "E_MOE_TRAFFIC"


def test_gate5_oracle_command_comparison_flags_a_short_core():
    document = _fixture_overlay("moe_dual_overlay_objects")
    counts = command_expectations(document)
    result = {"moe": {"regions": [
        {"region_id": 1, "core_id": 0, "issued": counts[0] - 1,
         "completed": counts[0] - 1}]}}
    failures = compare_commands(counts, result)
    assert failures and "executed" in failures[0]
