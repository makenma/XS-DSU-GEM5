import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.abi.decoder import decode_program
from mesh_ir.generated import abi as A
from mesh_ir.generated import agent_abi as G
from mesh_ir.model import MeshIrError
from mesh_ir.moe_materializer import MaterializeConfig, \
    member_slice
from mesh_ir.moe_overlay import OverlayBuilder, OverlayKey, EventRecord
from mesh_ir.moe_overlay_runtime import materialize_overlay, \
    overlay_objects_projection
from mesh_ir.moe_provider import FrozenToken, apply_capacity, uniform_select
from mesh_ir.moe_uid import SemanticTokenUid
from mesh_ir.weight_registry import weight_tag_index_map

REPO = Path(__file__).resolve().parents[4]
MOE_IMAGE = REPO / "tests/gem5/ai_mesh/fixtures/gate5/moe_multi.mshb"
PLAN_DIGEST = bytes.fromhex("01" * 32)

KINDS = (A.MESH_OBJECT_KIND.COMMAND, A.MESH_OBJECT_KIND.EVENT,
         A.MESH_OBJECT_KIND.DESCRIPTOR, A.MESH_OBJECT_KIND.TRANSFER,
         A.MESH_OBJECT_KIND.ALLOCATION, A.MESH_OBJECT_KIND.VIEW)


def token(item_id, ordinal, member, source_rank=0):
    uid = SemanticTokenUid(
        workload_plan_item_id=item_id, user_id=1, task_seq=item_id,
        repair_round=0, phase=G.SEMANTIC_PHASE.DECODE,
        sequence_ordinal=ordinal, token_ordinal=0)
    return FrozenToken(uid=uid, member_identity=member,
                       source_rank=source_rank, linear_ordinal=ordinal)


def member(program, layer, identity="m1", core_id=0, tokens=2,
           request_id=11, output_allocation=None):
    slice_ = member_slice(program, layer, identity, request_id, core_id,
                          valid_token_count=tokens)
    if output_allocation is None:
        return slice_
    return replace(slice_, output_allocation=output_allocation)


def program_parts():
    program = decode_program(MOE_IMAGE.read_bytes())
    layer = program.moe_layer_specs[1]
    kernel = program.moe_kernel_specs[1]
    regions = program.moe_dynamic_regions[
        layer.dynamic_region_first:
        layer.dynamic_region_first + layer.dynamic_region_count]
    return program, layer, kernel, regions


def config(chunk_bytes=4096):
    program, _, _, _ = program_parts()
    return MaterializeConfig(
        workload_plan_digest=PLAN_DIGEST,
        program_semantic_digest=bytes.fromhex(program.semantic_sha256()),
        p2p_chunk_bytes=chunk_bytes)


def bounds_for(regions):
    return {
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


def scratch_for(regions):
    return {region.region_id: (region.scratch_offset, region.scratch_bytes,
                               region.scratch_alignment)
            for region in regions}


def build(tokens=None, members=None, placement=None, chunk_bytes=4096,
          capacity_factor_q16=0x20000):
    program, layer, kernel, regions = program_parts()
    layer = layer.__class__(**{**layer.__dict__,
                               "capacity_factor_q16": capacity_factor_q16})
    tokens = tokens if tokens is not None else [token(1, 0, "m1"),
                                                token(1, 1, "m1")]
    members = members if members is not None else [member(program, layer)]
    placement = placement if placement is not None else [0, 0, 1, 1]
    plan = uniform_select(layer, tokens, PLAN_DIGEST, 7)
    capacity = apply_capacity(plan, layer, len(tokens))
    result = materialize_overlay(
        layer, kernel, plan, capacity, members, placement, regions,
        scratch_for(regions), config(chunk_bytes), bounds_for(regions),
        program.moe_expert_specs)
    return layer, plan, capacity, regions, result


def build_cached(tokens=None, members=None, placement=None,
                 chunk_bytes=4096, weight_tags=None):
    program, layer, kernel, regions = program_parts()
    layer = layer.__class__(**{**layer.__dict__,
                               "capacity_factor_q16": 0x20000})
    tokens = tokens if tokens is not None else [token(1, 0, "m1"),
                                                token(1, 1, "m1")]
    members = members if members is not None else [member(program, layer)]
    placement = placement if placement is not None else [0, 0, 1, 1]
    plan = uniform_select(layer, tokens, PLAN_DIGEST, 7)
    capacity = apply_capacity(plan, layer, len(tokens))
    tags = weight_tag_index_map(program) if weight_tags is None \
        else weight_tags
    result = materialize_overlay(
        layer, kernel, plan, capacity, members, placement, regions,
        scratch_for(regions),
        MaterializeConfig(
            workload_plan_digest=PLAN_DIGEST,
            program_semantic_digest=bytes.fromhex(program.semantic_sha256()),
            p2p_chunk_bytes=chunk_bytes, weight_residency="cached"),
        bounds_for(regions), program.moe_expert_specs, tags)
    return program, layer, plan, capacity, regions, result


def test_gate5_cached_weights_are_backed_by_cache_slots():
    program, _, _, _, _, result = build_cached()
    overlay = result.overlay
    tags = weight_tag_index_map(program)
    assert not [record for record in
                overlay.of_kind(A.MESH_OBJECT_KIND.ALLOCATION)
                if record.allocation_kind ==
                A.MOE_ALLOCATION_KIND.STREAMED_WEIGHT]
    assert not [record for record in
                overlay.of_kind(A.MESH_OBJECT_KIND.DESCRIPTOR)
                if record.moe_kind == A.MOE_DESCRIPTOR_KIND.STREAMED_WEIGHT]
    assert not [record for record in
                overlay.of_kind(A.MESH_OBJECT_KIND.COMMAND)
                if record.phase ==
                A.MOE_COMMAND_PHASE.STREAMED_WEIGHT_LOAD]
    assert not [record for record in
                overlay.of_kind(A.MESH_OBJECT_KIND.EVENT)
                if record.role == A.MOE_EVENT_ROLE.WEIGHT_READY]
    experts = {spec.expert_id: spec for spec in program.moe_expert_specs
               if spec.layer_id == overlay.layer_id}
    weights = {}
    for view in overlay.of_kind(A.MESH_OBJECT_KIND.VIEW):
        if view.view_kind != A.MOE_VIEW_KIND.WEIGHT:
            continue
        expert_id = view.semantic_owner_ref[0]
        expert = experts[expert_id]
        weights[expert_id] = view
        assert view.backing_kind == A.MOE_VIEW_BACKING.WEIGHT_CACHE_SLOT
        assert view.backing_ref == (tags[(expert.layer_id, expert_id)],)
        assert view.offset == 0
        assert view.bytes == expert.weight_bytes
        assert view.owner_core == expert.core_id
    assert set(weights) == {record.expert_id for record in
                            overlay.of_kind(A.MESH_OBJECT_KIND.COMMAND)
                            if record.role ==
                            A.MOE_COMMAND_ROLE.EXPERT_COMPUTE}
    assert weights
    projections = {row["ordinal"]: row for row in
                   overlay_objects_projection(overlay)["views"]
                   if row["view_kind"] == A.MOE_VIEW_KIND.WEIGHT}
    assert projections
    for view in weights.values():
        row = projections[view.key.ordinal]
        assert row["backing_kind"] == A.MOE_VIEW_BACKING.WEIGHT_CACHE_SLOT
        assert row["ref_region"] == 0
        assert row["ref_ordinal"] == view.backing_ref[0]


def test_gate5_cached_compute_waits_the_gate_instead_of_a_weight_load():
    program, _, _, _, regions, result = build_cached()
    overlay = result.overlay
    entries = {(region.region_id, A.MESH_OBJECT_KIND.EVENT, 0)
               for region in regions}
    computes = [record for record in
                overlay.of_kind(A.MESH_OBJECT_KIND.COMMAND)
                if record.role == A.MOE_COMMAND_ROLE.EXPERT_COMPUTE]
    assert computes
    roles = {record.role for record in
             overlay.of_kind(A.MESH_OBJECT_KIND.COMMAND)}
    assert A.MOE_COMMAND_ROLE.STREAMED_WEIGHT_LOAD not in roles
    for compute in computes:
        waits = {tuple(ref) for ref in compute.wait_refs}
        assert entries & waits
        assert all(ref[1] in (A.MESH_OBJECT_KIND.EVENT,
                              A.MESH_OBJECT_KIND.COMMAND) for ref in waits)
        assert compute.view_refs
    assert any(spec.layer_id == overlay.layer_id
               for spec in program.moe_expert_specs)


def test_gate5_cached_weight_needs_a_tag_binding():
    program, _, _, _, _, _ = build_cached()
    with pytest.raises(MeshIrError) as err:
        build_cached(weight_tags={})
    assert err.value.code == "E_MOE_MATERIALIZATION_V"
    assert program.moe_expert_specs


def test_gate5_padded_experts_fill_and_read_their_pad_rows():
    program, layer, kernel, regions = program_parts()
    layer = layer.__class__(**{
        **layer.__dict__, "capacity_factor_q16": 0x80000,
        "overflow_policy": A.MOE_OVERFLOW_POLICY.PAD_TO_CAPACITY})
    tokens = [token(1, 0, "m1")]
    members = [member(program, layer, tokens=1)]
    placement = [spec.core_id for spec in program.moe_expert_specs
                 if spec.layer_id == layer.layer_id]
    plan = uniform_select(layer, tokens, PLAN_DIGEST, 7)
    capacity = apply_capacity(plan, layer, len(tokens))
    result = materialize_overlay(
        layer, kernel, plan, capacity, members, placement, regions,
        scratch_for(regions),
        config(), bounds_for(regions),
        [spec for spec in program.moe_expert_specs
         if spec.layer_id == layer.layer_id])
    overlay = result.overlay
    padded = {expert_id: slots for expert_id, slots
              in enumerate(capacity.padded_slots_by_expert) if slots}
    assert padded
    allocations = {(record.key.region_id, record.key.ordinal): record
                   for record in
                   overlay.of_kind(A.MESH_OBJECT_KIND.ALLOCATION)}
    pad_buffers = {record.semantic_owner_ref[0]: record
                   for record in overlay.of_kind(A.MESH_OBJECT_KIND.VIEW)
                   if record.view_kind == A.MOE_VIEW_KIND.PAD_BUFFER}
    assert set(pad_buffers) == set(padded)
    pad_events = {record.expert_id: record for record
                  in overlay.of_kind(A.MESH_OBJECT_KIND.EVENT)
                  if record.role == A.MOE_EVENT_ROLE.PAD_FILL_DONE}
    assert set(pad_events) == set(padded)
    for expert_id, slots in padded.items():
        view = pad_buffers[expert_id]
        assert view.access == A.MOE_VIEW_ACCESS.WRITE
        assert view.bytes == slots * layer.token_bytes
        assert view.backing_kind == A.MOE_VIEW_BACKING.OVERLAY_ALLOCATION
        backing = allocations[view.backing_ref]
        assert backing.allocation_kind == A.MOE_ALLOCATION_KIND.PAD_INPUT
        assert backing.bytes == view.bytes
    pad_commands = [record for record in
                    overlay.of_kind(A.MESH_OBJECT_KIND.COMMAND)
                    if record.role == A.MOE_COMMAND_ROLE.PAD_FILL]
    assert len(pad_commands) == len(padded)
    assert all(record.opcode == A.OPCODE.DMA_FILL for record in pad_commands)
    assert {record.phase for record in pad_commands} == \
        {A.MOE_COMMAND_PHASE.PAD_FILL}
    computes = [record for record in
                overlay.of_kind(A.MESH_OBJECT_KIND.COMMAND)
                if record.role == A.MOE_COMMAND_ROLE.EXPERT_COMPUTE]
    assert len(computes) == len(capacity.expert_loads)
    for compute in computes:
        expert_id = compute.expert_id
        real = sum(1 for row in result.plane.expert_rows
                   if row["expert_id"] == expert_id and row["kind"] == "real")
        rows = real + capacity.padded_slots_by_expert[expert_id]
        assert rows > 0
        assert compute.payload_bytes == \
            rows * layer.output_token_bytes
        waits = set(compute.wait_refs)
        assert (pad_events[expert_id].key.region_id,
                A.MESH_OBJECT_KIND.EVENT,
                pad_events[expert_id].key.ordinal) in waits
        views = {(ref[0], ref[2]) for ref in compute.view_refs}
        assert (pad_buffers[expert_id].key.region_id,
                pad_buffers[expert_id].key.ordinal) in views


def test_gate5_builder_resolves_handles_into_ordinals():
    builder = OverlayBuilder(program_instance_id=1, layer_id=1)
    event = builder.add(A.MESH_OBJECT_KIND.EVENT, EventRecord(
        event_phase=A.MOE_EVENT_PHASE.EXIT, owner_core=0, phase=0,
        src_core=0xFFFF, dst_core=0xFFFF, expert_id=0xFFFF, chunk_ordinal=0,
        role=A.MOE_EVENT_ROLE.REGION_TERMINAL, token_ref=bytes(32),
        producers=(),
        key=OverlayKey(region_group_id=0, region_id=1,
                       kind=A.MESH_OBJECT_KIND.EVENT, ordinal=0)))
    builder._objects[event] = type(builder._objects[event])(
        **{**builder._objects[event].__dict__,
           "producers": ((1, A.MESH_OBJECT_KIND.COMMAND, 1),)})
    from mesh_ir.moe_overlay import CommandRecord
    builder.add(A.MESH_OBJECT_KIND.COMMAND, CommandRecord(
        phase=A.MOE_COMMAND_PHASE.EXIT_SIGNAL, opcode=A.OPCODE.EVENT_SIGNAL,
        owner_core=0, src_core=0xFFFF, dst_core=0xFFFF, expert_id=0xFFFF,
        chunk_ordinal=0, role=A.MOE_COMMAND_ROLE.REGION_TERMINAL,
        token_ref=bytes(32), signal_refs=(event,),
        key=OverlayKey(region_group_id=0, region_id=1,
                       kind=A.MESH_OBJECT_KIND.COMMAND, ordinal=0)))
    overlay = builder.finish()
    command = overlay.of_kind(A.MESH_OBJECT_KIND.COMMAND)[0]
    assert command.signal_refs == ((1, A.MESH_OBJECT_KIND.EVENT, 1),)
    assert command.key.ordinal == 1


def test_gate5_builder_rejects_unresolved_handles():
    from mesh_ir.moe_overlay import CommandRecord
    builder = OverlayBuilder(program_instance_id=1, layer_id=1)
    builder.add(A.MESH_OBJECT_KIND.COMMAND, CommandRecord(
        phase=A.MOE_COMMAND_PHASE.EXIT_SIGNAL, opcode=A.OPCODE.EVENT_SIGNAL,
        owner_core=0, src_core=0xFFFF, dst_core=0xFFFF, expert_id=0xFFFF,
        chunk_ordinal=0, role=A.MOE_COMMAND_ROLE.REGION_TERMINAL,
        token_ref=bytes(32), wait_refs=((1, A.MESH_OBJECT_KIND.EVENT, 7),),
        key=OverlayKey(region_group_id=0, region_id=1,
                       kind=A.MESH_OBJECT_KIND.COMMAND, ordinal=0)))
    with pytest.raises(MeshIrError) as err:
        builder.finish()
    assert err.value.code == "E_MOE_MATERIALIZATION_V"


def test_gate5_overlay_installs_every_object_family():
    layer, plan, capacity, regions, result = build()
    objects = result.overlay.objects
    for kind in KINDS:
        assert objects.get(kind), kind
    assert len(result.overlay.of_kind(A.MESH_OBJECT_KIND.TRANSFER)) == \
        len(result.plane.chunks)
    group_commands = [record for record in
                      result.overlay.of_kind(A.MESH_OBJECT_KIND.COMMAND)
                      if record.key.region_id == 0]
    assert len(group_commands) == 1
    assert group_commands[0].role == A.MOE_COMMAND_ROLE.GROUP_EXIT
    exits = [record for record in
             result.overlay.of_kind(A.MESH_OBJECT_KIND.EVENT)
             if record.role == A.MOE_EVENT_ROLE.OVERLAY_EXIT]
    assert len(exits) == 1
    assert exits[0].producers == ((0, A.MESH_OBJECT_KIND.COMMAND, 1),)
    terminals = [record for record in
                 result.overlay.of_kind(A.MESH_OBJECT_KIND.EVENT)
                 if record.role in (A.MOE_EVENT_ROLE.REGION_TERMINAL,
                                    A.MOE_EVENT_ROLE.EMPTY_REGION_TERMINAL)]
    assert len(terminals) == len(regions)


def test_gate5_expert_compute_waits_weight_and_gather_views():
    _, _, _, _, result = build()
    computes = [record for record in
                result.overlay.of_kind(A.MESH_OBJECT_KIND.COMMAND)
                if record.role == A.MOE_COMMAND_ROLE.EXPERT_COMPUTE]
    assert computes
    weights = {record.key.ordinal for record in
               result.overlay.of_kind(A.MESH_OBJECT_KIND.EVENT)
               if record.role == A.MOE_EVENT_ROLE.WEIGHT_READY}
    for compute in computes:
        waits = {ref[2] for ref in compute.wait_refs
                 if ref[1] == A.MESH_OBJECT_KIND.EVENT}
        assert weights & waits
        assert compute.view_refs
        assert compute.kernel_spec_index == \
            program_parts()[1].kernel_spec_index


def test_gate5_token_commands_follow_fan_in():
    from mesh_ir.moe_provider import BatchMoeSelectionPlan, SelectedTokenRoute

    tokens = [token(1, 0, "m1"), token(1, 1, "m1")]
    program, layer, kernel, regions = program_parts()
    layer = layer.__class__(**{**layer.__dict__,
                               "capacity_factor_q16": 0x1000})
    routes = tuple(sorted(
        [SelectedTokenRoute(uid=item.uid, member_identity="m1",
                            topk_slot=slot, selected_expert_id=0,
                            source_rank=0)
         for item in tokens for slot in (0, 1)],
        key=lambda route: route.canonical_key()))
    plan = BatchMoeSelectionPlan(
        layer_id=layer.layer_id, provider_kind="route_replay",
        provider_digest="0" * 64, workload_plan_digest=PLAN_DIGEST.hex(),
        routes=routes, source_expert_counts=(), member_identities=("m1",))
    capacity = apply_capacity(plan, layer, len(tokens))
    result = materialize_overlay(
        layer, kernel, plan, capacity, [member(program, layer)],
        [0, 0, 1, 1], regions,
        scratch_for(regions), config(), bounds_for(regions),
        program.moe_expert_specs)
    roles = [record.role for record in
             result.overlay.of_kind(A.MESH_OBJECT_KIND.COMMAND)]
    assert A.MOE_COMMAND_ROLE.LOCAL_REDUCE in roles or \
        A.MOE_COMMAND_ROLE.COPY_THROUGH in roles or \
        A.MOE_COMMAND_ROLE.DROPPED_TOKEN_FILL in roles
    descriptors = {record.moe_kind for record in
                   result.overlay.of_kind(A.MESH_OBJECT_KIND.DESCRIPTOR)}
    assert A.MOE_DESCRIPTOR_KIND.DROPPED_TOKEN_FILL in descriptors
    dropped = [record for record in
               result.overlay.of_kind(A.MESH_OBJECT_KIND.DESCRIPTOR)
               if record.moe_kind == A.MOE_DESCRIPTOR_KIND.DROPPED_TOKEN_FILL]
    for record in dropped:
        assert record.traffic_class() == A.MOE_TRAFFIC_CLASS.PARTIAL_RESULT
    assert capacity.dropped


def test_gate5_local_only_placement_has_no_transfers():
    _, _, _, _, result = build(placement=[0, 0, 0, 0])
    assert result.overlay.of_kind(A.MESH_OBJECT_KIND.TRANSFER) == []
    assert not result.plane.chunks
    roles = {record.role for record in
             result.overlay.of_kind(A.MESH_OBJECT_KIND.COMMAND)}
    assert A.MOE_COMMAND_ROLE.DISPATCH_SENDER not in roles
    assert A.MOE_COMMAND_ROLE.COMBINE_SENDER not in roles
    assert A.MOE_COMMAND_ROLE.EXPERT_COMPUTE in roles


def test_gate5_empty_region_gets_one_empty_terminal():
    _, _, _, _, result = build(tokens=[], members=[], placement=[0, 0, 1, 1])
    terminals = [record for record in
                 result.overlay.of_kind(A.MESH_OBJECT_KIND.COMMAND)
                 if record.role in (
                     A.MOE_COMMAND_ROLE.REGION_TERMINAL,
                     A.MOE_COMMAND_ROLE.EMPTY_REGION_TERMINAL)]
    assert len(terminals) == len(program_parts()[3])
    empty = [record for record in terminals
             if record.role == A.MOE_COMMAND_ROLE.EMPTY_REGION_TERMINAL]
    assert empty
    for record in empty:
        assert record.payload_bytes == 0
        assert record.descriptor_refs == ()
        assert record.transfer_refs == ()


def test_gate5_materialization_digest_is_stable_and_placement_sensitive():
    _, _, _, _, first = build()
    _, _, _, _, again = build()
    _, _, _, _, other = build(placement=[0, 1, 0, 1])
    assert first.digest == again.digest
    assert first.digest != other.digest
    projection = first.canonical()
    assert projection["layer_id"] == 2
    assert projection["data_plane"]["route_buffers"]
    assert projection["commands"] and projection["events"]


def test_gate5_materializer_rejects_foreign_layer_regions():
    program, layer, kernel, regions = program_parts()
    other_region = program.moe_dynamic_regions[0]
    tokens = [token(1, 0, "m1"), token(1, 1, "m1")]
    members = [member(program, layer)]
    plan = uniform_select(layer, tokens, PLAN_DIGEST, 7)
    capacity = apply_capacity(plan, layer, len(tokens))
    with pytest.raises(MeshIrError) as err:
        materialize_overlay(layer, kernel, plan, capacity, members,
                            [0, 0, 1, 1], [other_region], scratch_for(regions),
                            config(), bounds_for(regions),
                            program.moe_expert_specs)
    assert err.value.code == "E_MOE_MATERIALIZATION_V"


def test_gate5_overlay_bounds_are_enforced():
    program, layer, kernel, regions = program_parts()
    tokens = [token(1, 0, "m1"), token(1, 1, "m1")]
    members = [member(program, layer)]
    plan = uniform_select(layer, tokens, PLAN_DIGEST, 7)
    capacity = apply_capacity(plan, layer, len(tokens))
    tight = bounds_for(regions)
    tight["per_region"][regions[0].region_id][
        A.MESH_OBJECT_KIND.COMMAND] = 1
    with pytest.raises(MeshIrError) as err:
        materialize_overlay(layer, kernel, plan, capacity, members,
                            [0, 0, 1, 1], regions, scratch_for(regions),
                            config(), tight, program.moe_expert_specs)
    assert err.value.code == "E_MOE_MATERIALIZE_CAPACITY"


def test_gate5_scratch_intervals_are_inside_the_region():
    _, _, _, regions, result = build()
    for (region_id, ordinal), (offset, byte_count) in result.intervals.items():
        region = next(candidate for candidate in regions
                      if candidate.region_id == region_id)
        assert region.scratch_offset <= offset
        assert offset + byte_count <= \
            region.scratch_offset + region.scratch_bytes
    allocations = result.overlay.of_kind(A.MESH_OBJECT_KIND.ALLOCATION)
    assert len(result.intervals) == len(allocations)


def test_gate5_overlay_image_carries_resolved_scratch_addresses():
    import struct

    from mesh_ir.moe_overlay_runtime import (
        OVERLAY_IMAGE_HEADER_BYTES,
        OVERLAY_IMAGE_MAGIC,
        OVERLAY_IMAGE_RECORD_BYTES,
        OVERLAY_IMAGE_SCRATCH_BYTES,
        OVERLAY_REFS_PER_OBJECT,
        overlay_image_bytes,
        overlay_wire_bytes,
    )

    _, _, _, _, result = build()
    rows = [(region_id, ordinal, offset, byte_count)
            for (region_id, ordinal), (offset, byte_count)
            in sorted(result.intervals.items())]
    image = overlay_image_bytes([], result.overlay.layer_id, rows,
                                program_digest=bytes(range(32)))
    assert image[:4] == OVERLAY_IMAGE_MAGIC
    assert struct.unpack_from("<I", image, 8)[0] == result.overlay.layer_id
    assert struct.unpack_from("<I", image, 12)[0] == 0
    assert struct.unpack_from("<I", image, 16)[0] == OVERLAY_REFS_PER_OBJECT
    assert struct.unpack_from("<I", image, 20)[0] == len(rows)
    assert len(image) == (OVERLAY_IMAGE_HEADER_BYTES +
                          len(rows) * OVERLAY_IMAGE_SCRATCH_BYTES)
    base = OVERLAY_IMAGE_HEADER_BYTES
    assert [struct.unpack_from("<IIQQ", image,
                               base + index * OVERLAY_IMAGE_SCRATCH_BYTES)
            for index in range(len(rows))] == rows
    assert len(image) == len(overlay_image_bytes(
        [], result.overlay.layer_id, rows,
        program_digest=bytes(range(32))))
    assert (len(image) - OVERLAY_IMAGE_HEADER_BYTES -
            len(rows) * OVERLAY_IMAGE_SCRATCH_BYTES) == 0
    assert OVERLAY_IMAGE_RECORD_BYTES == 138 + OVERLAY_REFS_PER_OBJECT * 36


def test_gate5_every_view_resolves_inside_its_backing_allocation():
    _, _, _, _, result = build()
    intervals = result.intervals
    allocations = {(record.key.region_id, record.key.ordinal): record
                   for record in
                   result.overlay.of_kind(A.MESH_OBJECT_KIND.ALLOCATION)}
    overlay_backed = 0
    static_backed = 0
    for view in result.overlay.of_kind(A.MESH_OBJECT_KIND.VIEW):
        if view.backing_kind == A.MOE_VIEW_BACKING.STATIC_ALLOCATION:
            static_backed += 1
            assert len(view.backing_ref) == 1
            continue
        if view.backing_kind != A.MOE_VIEW_BACKING.OVERLAY_ALLOCATION:
            assert view.backing_ref == ()
            continue
        overlay_backed += 1
        backing = allocations[view.backing_ref]
        offset, byte_count = intervals[view.backing_ref]
        assert byte_count == backing.bytes
        assert view.offset + view.bytes <= backing.bytes
        assert offset + view.offset + view.bytes <= offset + backing.bytes
    assert overlay_backed > 0
    assert static_backed > 0
    backing_kinds = {view.backing_kind for view in
                     result.overlay.of_kind(A.MESH_OBJECT_KIND.VIEW)}
    assert backing_kinds <= {
        A.MOE_VIEW_BACKING.OVERLAY_ALLOCATION,
        A.MOE_VIEW_BACKING.STATIC_ALLOCATION,
        A.MOE_VIEW_BACKING.INSTANCE_MEMBER_BINDING,
        A.MOE_VIEW_BACKING.WEIGHT_CACHE_SLOT,
        A.MOE_VIEW_BACKING.KV_RUNTIME_VIEW,
    }


def _resolve_view(program, overlay, intervals, view_refs):
    view_ref = view_refs[0]
    view = next(record for record in overlay.of_kind(A.MESH_OBJECT_KIND.VIEW)
                if (record.key.region_id, record.key.ordinal) ==
                (view_ref[0], view_ref[2]))
    if view.backing_kind == A.MOE_VIEW_BACKING.OVERLAY_ALLOCATION:
        offset, byte_count = intervals[view.backing_ref]
    elif view.backing_kind == A.MOE_VIEW_BACKING.STATIC_ALLOCATION:
        allocation = next(candidate for candidate in program.allocations
                          if candidate.allocation_id == view.backing_ref[0])
        offset, byte_count = allocation.offset_bytes, allocation.size_bytes
    else:
        raise AssertionError(f"unresolvable backing {view.backing_kind}")
    assert view.offset + view.bytes <= byte_count
    return offset + view.offset, view.bytes, view.owner_core


def _weight_endpoint(program, expert_id):
    expert = next(candidate for candidate in program.moe_expert_specs
                  if candidate.expert_id == expert_id)
    relocation = next(candidate for candidate in program.relocations
                      if candidate.symbol_sid == expert.weight_symbol_id)
    return relocation.offset_bytes + expert.weight_region_offset, \
        expert.weight_bytes, expert.core_id


def test_gate5_every_dma_descriptor_resolves_both_endpoints():
    image = REPO / "tests/gem5/ai_mesh/fixtures/gate5/moe_dual.mshb"
    program = decode_program(image.read_bytes())
    layer = program.moe_layer_specs[0]
    kernel = program.moe_kernel_specs[layer.kernel_spec_index]
    regions = program.moe_dynamic_regions[:layer.dynamic_region_count]
    tokens = [token(1, 0, "m1")]
    plan = uniform_select(layer, tokens, PLAN_DIGEST, 7)
    capacity = apply_capacity(plan, layer, len(tokens))
    placement = [spec.core_id for spec in program.moe_expert_specs]
    result = materialize_overlay(
        layer, kernel, plan, capacity, [member(program, layer, tokens=1)],
        placement, regions,
        scratch_for(regions), config(), bounds_for(regions),
        program.moe_expert_specs)
    overlay = result.overlay
    intervals = result.intervals
    commands = {}
    for command in overlay.of_kind(A.MESH_OBJECT_KIND.COMMAND):
        for ref in command.descriptor_refs:
            commands[(ref[0], ref[2])] = command
    descriptors = overlay.of_kind(A.MESH_OBJECT_KIND.DESCRIPTOR)
    peer_destination = {}
    for record in descriptors:
        if record.destination_view_ref:
            peer_destination[(record.phase, record.expert_id,
                              record.chunk_ordinal)] = \
                record.destination_view_ref
    resolved = 0
    arrivals = 0
    for record in descriptors:
        command = commands[(record.key.region_id, record.key.ordinal)]
        if command.opcode == A.OPCODE.RECV_WAIT:
            assert record.source_view_ref == ()
            arrivals += 1
            continue
        if record.moe_kind == A.MOE_DESCRIPTOR_KIND.STREAMED_WEIGHT:
            source = _weight_endpoint(program, record.expert_id)
            target = _resolve_view(program, overlay, intervals,
                                   record.destination_view_ref)
        elif record.source_view_ref:
            source = _resolve_view(program, overlay, intervals,
                                   record.source_view_ref)
            target = _resolve_view(
                program, overlay, intervals,
                peer_destination[(record.phase, record.expert_id,
                                  record.chunk_ordinal)])
        else:
            target = _resolve_view(program, overlay, intervals,
                                   record.destination_view_ref)
            source = target
            assert command.opcode == A.OPCODE.DMA_FILL
        assert source[1] > 0 and target[1] > 0
        assert source[1] >= record.valid_bytes or target[1] >= \
            record.valid_bytes
        resolved += 1
    assert resolved + arrivals == len(descriptors)
    assert arrivals == 2 and resolved == 5


MOE_DUAL_IMAGE = REPO / "tests/gem5/ai_mesh/fixtures/gate5/moe_dual.mshb"


def build_drop(capacity_factor_q16=0x8000, unbound_output=False):
    program = decode_program(MOE_DUAL_IMAGE.read_bytes())
    layer = program.moe_layer_specs[0]
    layer = layer.__class__(**{**layer.__dict__,
                               "capacity_factor_q16": capacity_factor_q16})
    kernel = program.moe_kernel_specs[layer.kernel_spec_index]
    regions = program.moe_dynamic_regions[:layer.dynamic_region_count]
    tokens = [token(1, 0, "m1"), token(2, 0, "m2")]
    members = [member(program, layer, "m1", 0, tokens=1, request_id=11),
               member(program, layer, "m2", 1, tokens=1, request_id=12)]
    if unbound_output:
        members[1] = replace(members[1], output_allocation=0)
    placement = [0, 1]
    plan = uniform_select(layer, tokens, PLAN_DIGEST, 7)
    capacity = apply_capacity(plan, layer, len(tokens))
    result = materialize_overlay(
        layer, kernel, plan, capacity, members, placement, regions,
        scratch_for(regions), config(), bounds_for(regions),
        program.moe_expert_specs, (), (), program.allocations)
    return program, layer, capacity, result


def test_gate5_dropped_tokens_fill_a_bound_member_output():
    program, layer, capacity, result = build_drop()
    assert capacity.dropped
    fills = [record for record in
             result.overlay.of_kind(A.MESH_OBJECT_KIND.DESCRIPTOR)
             if record.moe_kind == A.MOE_DESCRIPTOR_KIND.DROPPED_TOKEN_FILL]
    assert fills
    for descriptor in fills:
        ref = descriptor.destination_view_ref[0]
        view = next(record for record in
                    result.overlay.of_kind(A.MESH_OBJECT_KIND.VIEW)
                    if (record.key.region_id, record.key.ordinal) ==
                    (ref[0], ref[2]))
        assert view.view_kind == A.MOE_VIEW_KIND.MEMBER_OUTPUT
        assert view.backing_kind == A.MOE_VIEW_BACKING.STATIC_ALLOCATION
        backing = next(allocation for allocation in program.allocations
                       if allocation.allocation_id == view.backing_ref[0])
        assert view.offset + view.bytes <= backing.size_bytes
        assert descriptor.valid_bytes == layer.output_token_bytes
    outputs = [record for record in
               result.overlay.of_kind(A.MESH_OBJECT_KIND.VIEW)
               if record.view_kind == A.MOE_VIEW_KIND.MEMBER_OUTPUT]
    assert outputs
    assert {record.backing_kind for record in outputs} == \
        {A.MOE_VIEW_BACKING.STATIC_ALLOCATION}


def test_gate5_unbound_member_output_rejects_a_dropped_fill():
    with pytest.raises(MeshIrError) as err:
        build_drop(unbound_output=True)
    assert err.value.code == "E_MOE_MATERIALIZATION_V"
    assert "bound member output" in err.value.message

def test_gate5_member_slice_binds_only_a_fitting_output_shard():
    program = decode_program(MOE_DUAL_IMAGE.read_bytes())
    layer = program.moe_layer_specs[0]
    assert member_slice(program, layer, "m1", 11, 0).output_allocation == 3
    assert member_slice(program, layer, "m2", 12, 1).output_allocation == 7
    shrunk = replace(program, shards=[
        replace(shard, span_bytes=layer.token_bytes)
        if shard.allocation_id == 7 else shard
        for shard in program.shards])
    assert member_slice(shrunk, layer, "m2", 12, 1).output_allocation == 0
    with pytest.raises(MeshIrError) as err:
        member_slice(program, layer, "m3", 13, 2)
    assert "owns no token shard" in err.value.message


def test_gate5_member_view_must_stay_on_its_own_core():
    program = decode_program(MOE_DUAL_IMAGE.read_bytes())
    layer = program.moe_layer_specs[0]
    kernel = program.moe_kernel_specs[layer.kernel_spec_index]
    regions = program.moe_dynamic_regions[:layer.dynamic_region_count]
    tokens = [token(1, 0, "m1")]
    members = [replace(member_slice(program, layer, "m1", 11, 0),
                       input_allocation=5)]
    placement = [spec.core_id for spec in program.moe_expert_specs]
    plan = uniform_select(layer, tokens, PLAN_DIGEST, 7)
    capacity = apply_capacity(plan, layer, len(tokens))
    with pytest.raises(MeshIrError) as err:
        materialize_overlay(
            layer, kernel, plan, capacity, members, placement, regions,
            scratch_for(regions), config(), bounds_for(regions),
            program.moe_expert_specs, (), (), program.allocations)
    assert "crosses the program allocation owner" in err.value.message


def test_gate5_overlay_image_carries_the_command_views():
    import json

    from mesh_ir.moe_overlay_runtime import (
        OVERLAY_IMAGE_HEADER_BYTES,
        OVERLAY_IMAGE_RECORD_BYTES,
        OVERLAY_REFS_PER_OBJECT,
        overlay_image_bytes,
    )

    path = MOE_DUAL_IMAGE.parent / "moe_dual_overlay_objects.json"
    document = json.loads(path.read_text())
    image = overlay_image_bytes(
        document["objects"], document["layer_id"],
        [(row["region_id"], row["ordinal"], row["offset"], row["bytes"])
         for row in document["scratch"]],
        program_digest=bytes.fromhex(document["program_digest_hex"]))
    records = (len(image) - OVERLAY_IMAGE_HEADER_BYTES -
               len(document["scratch"]) * 24) // OVERLAY_IMAGE_RECORD_BYTES
    assert records == len(document["objects"])
    encoded = {}
    for index in range(records):
        base = OVERLAY_IMAGE_HEADER_BYTES + index * OVERLAY_IMAGE_RECORD_BYTES
        kind = int.from_bytes(image[base:base + 2], "little")
        if kind != A.MESH_OBJECT_KIND.COMMAND:
            continue
        region = int.from_bytes(image[base + 2:base + 6], "little")
        ordinal = int.from_bytes(image[base + 6:base + 10], "little")
        ref_base = base + 138 + OVERLAY_REFS_PER_OBJECT * 12 * 2
        refs = []
        for ref in range(OVERLAY_REFS_PER_OBJECT):
            row = image[ref_base + ref * 12:ref_base + (ref + 1) * 12]
            triple = [int.from_bytes(row[at:at + 4], "little")
                      for at in (0, 4, 8)]
            if triple[0] != 0xFFFFFFFF:
                refs.append(triple)
        encoded[(region, ordinal)] = refs
    modelled = {(entry["region_id"], entry["ordinal"]):
                [list(ref) for ref in entry.get("view_refs", ())]
                for entry in document["objects"]
                if entry["kind"] == A.MESH_OBJECT_KIND.COMMAND}
    assert encoded == modelled
    assert any(refs for refs in encoded.values())


def test_gate5_overlay_image_rejects_too_many_view_refs():
    from mesh_ir.moe_overlay_runtime import (
        OVERLAY_REFS_PER_OBJECT,
        overlay_image_bytes,
    )

    entry = {
        "kind": A.MESH_OBJECT_KIND.COMMAND, "region_id": 1, "ordinal": 1,
        "owner_core": 0, "secondary_kind": A.OPCODE.GEMM, "expert_id": 0xFFFF,
        "src_core": 0xFFFF, "dst_core": 0xFFFF, "chunk_ordinal": 0,
        "phase": 0, "role": 5, "access": 0, "bytes": 64, "alignment": 0,
        "offset": 0, "ref_region": 0, "ref_ordinal": 0, "token_hex": "",
        "wait_count": 0, "signal_count": 0, "wait_refs": [], "signal_refs": [],
        "view_refs": [[1, A.MESH_OBJECT_KIND.VIEW, ordinal]
                      for ordinal in range(1, OVERLAY_REFS_PER_OBJECT + 2)],
        "backing_kind": 0, "semantic_owner_kind": 0, "semantic_owner_ref0": 0,
        "validity_extent": 0,
    }
    with pytest.raises(MeshIrError) as err:
        overlay_image_bytes([entry], 1, program_digest=bytes(range(32)))
    assert err.value.code == "E_MOE_MATERIALIZATION_V"
    assert "too many references" in err.value.message


def test_gate5_fill_modes_share_the_service_geometry():
    from mesh_ir.moe_fill import DIGEST_ONLY, FUNCTIONAL_BYTES, VALIDITY_ONLY

    projections = []
    for mode in (FUNCTIONAL_BYTES, DIGEST_ONLY, VALIDITY_ONLY):
        program, layer, kernel, regions = program_parts()
        tokens = [token(1, 0, "m1")]
        members = [member(program, layer, tokens=1)]
        plan = uniform_select(layer, tokens, PLAN_DIGEST, 7)
        capacity = apply_capacity(plan, layer, len(tokens))
        result = materialize_overlay(
            layer, kernel, plan, capacity, members, [0, 0, 1, 1], regions,
            scratch_for(regions),
            MaterializeConfig(
                workload_plan_digest=PLAN_DIGEST,
                program_semantic_digest=bytes.fromhex(
                    program.semantic_sha256()),
                p2p_chunk_bytes=4096, fill_mode=mode),
            bounds_for(regions), program.moe_expert_specs, (), (),
            program.allocations)
        projections.append((overlay_objects_projection(result.overlay),
                            result.plane.canonical(),
                            result.intervals))
    assert projections[0] == projections[1] == projections[2]
    assert projections[0][0]["commands"]


def test_gate5_dropped_token_fill_carries_the_contract_content():
    import hashlib

    program, layer, capacity, result = build_drop()
    fills = [record for record in
             result.overlay.of_kind(A.MESH_OBJECT_KIND.DESCRIPTOR)
             if record.moe_kind == A.MOE_DESCRIPTOR_KIND.DROPPED_TOKEN_FILL]
    assert fills
    zeros = hashlib.sha256(bytes(layer.output_token_bytes)).digest()
    for record in fills:
        content = bytes(record.fill_content)
        assert len(content) == record.valid_bytes == layer.output_token_bytes
        assert content != bytes(layer.output_token_bytes)
        assert hashlib.sha256(content).digest() != zeros
    repeated = build_drop()[3]
    repeated_fills = [bytes(record.fill_content) for record in
                      repeated.overlay.of_kind(A.MESH_OBJECT_KIND.DESCRIPTOR)
                      if record.moe_kind ==
                      A.MOE_DESCRIPTOR_KIND.DROPPED_TOKEN_FILL]
    assert repeated_fills == [bytes(record.fill_content)
                              for record in fills]


def test_gate5_route_fill_content_matches_its_valid_bytes():
    _, _, _, _, result = build()
    route_fills = [record for record in
                   result.overlay.of_kind(A.MESH_OBJECT_KIND.DESCRIPTOR)
                   if record.moe_kind ==
                   A.MOE_DESCRIPTOR_KIND.ROUTE_FILL]
    assert route_fills
    for record in route_fills:
        content = bytes(record.fill_content)
        assert len(content) == record.valid_bytes
        assert content != bytes(record.valid_bytes)
        assert len(content) % A.MOE_RUNTIME_ROUTE_ENTRY_BYTES == 0
