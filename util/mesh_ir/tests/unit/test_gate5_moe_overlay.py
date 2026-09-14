import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.generated import abi as A
from mesh_ir.model import MeshIrError
from mesh_ir.moe_overlay import (
    AllocationRecord,
    CommandRecord,
    EventRecord,
    Overlay,
    OverlayKey,
    TransferRecord,
    ValidityShape,
    ViewRecord,
    align_up,
    allocate_scratch,
    assign_ordinals,
    verify_overlay,
)

LAYER = 1


def key(region_id, kind, ordinal=0):
    return OverlayKey(region_group_id=LAYER, region_id=region_id, kind=kind,
                      ordinal=ordinal)


def allocation(region_id, kind, ordinal=0, byte_count=64, alignment=64,
               expert_id=0, source_core=0xFFFF, phase=0, owner_core=0):
    return AllocationRecord(
        owner_core=owner_core, allocation_kind=kind, expert_id=expert_id,
        source_core=source_core, phase=phase, bytes=byte_count,
        alignment=alignment,
        validity=ValidityShape(A.MOE_VALIDITY_KIND.FULL_PREFIX,
                               valid_bytes=byte_count),
        key=key(region_id, A.MESH_OBJECT_KIND.ALLOCATION, ordinal))


def view(region_id, ordinal=0, owner_core=0, kind=A.MOE_VIEW_KIND.MEMBER_INPUT,
         access=A.MOE_VIEW_ACCESS.READ, byte_count=64, offset=0,
         validity=(64,), backing_kind=A.MOE_VIEW_BACKING.OVERLAY_ALLOCATION):
    return ViewRecord(
        owner_core=owner_core, view_kind=kind, access=access,
        validity_slices=((validity[0], validity[0]),),
        backing_kind=backing_kind, backing_ref=(region_id, 1),
        offset=offset, bytes=byte_count,
        semantic_owner_kind=A.MOE_SEMANTIC_OWNER.MEMBER,
        semantic_owner_ref=(1, 0), token_ref=(0,) * 32,
        key=key(region_id, A.MESH_OBJECT_KIND.VIEW, ordinal))


def event(region_id, ordinal, role, producers, signal_refs=()):
    return EventRecord(
        event_phase=A.MOE_EVENT_PHASE.EXIT, owner_core=0, phase=0,
        src_core=0xFFFF, dst_core=0xFFFF, expert_id=0xFFFF, chunk_ordinal=0,
        role=role, token_ref=(0,) * 32, producers=producers,
        key=key(region_id, A.MESH_OBJECT_KIND.EVENT, ordinal))


def command(region_id, ordinal, role, waits=(), signals=(), payload=0,
            views=(), chunk_ordinal=0, token_ref=None):
    return CommandRecord(
        phase=A.MOE_COMMAND_PHASE.EXIT_SIGNAL, opcode=A.OPCODE.EVENT_SIGNAL,
        owner_core=0, src_core=0xFFFF, dst_core=0xFFFF, expert_id=0xFFFF,
        chunk_ordinal=chunk_ordinal, role=role,
        token_ref=(0,) * 32 if token_ref is None else token_ref,
        wait_refs=waits, signal_refs=signals, payload_bytes=payload,
        view_refs=views,
        key=key(region_id, A.MESH_OBJECT_KIND.COMMAND, ordinal))


def simple_overlay() -> Overlay:
    overlay = Overlay(program_instance_id=1, layer_id=LAYER)
    overlay.add(A.MESH_OBJECT_KIND.COMMAND,
                command(1, 0, A.MOE_COMMAND_ROLE.REGION_TERMINAL))
    overlay.add(A.MESH_OBJECT_KIND.ALLOCATION,
                allocation(1, A.MOE_ALLOCATION_KIND.ROUTE_BUFFER))
    overlay.add(A.MESH_OBJECT_KIND.VIEW, view(1))
    overlay.add(A.MESH_OBJECT_KIND.EVENT,
                event(1, 0, A.MOE_EVENT_ROLE.REGION_TERMINAL, ((1, 1),)))
    return overlay


def bounds_of(region_ids=(1,), commands=8, events=8, descriptors=8,
              allocations=8, transfers=8):
    per_region = {
        region_id: {
            A.MESH_OBJECT_KIND.COMMAND: commands,
            A.MESH_OBJECT_KIND.EVENT: events,
            A.MESH_OBJECT_KIND.DESCRIPTOR: descriptors,
            A.MESH_OBJECT_KIND.ALLOCATION: allocations,
        }
        for region_id in region_ids
    }
    group = {
        A.MESH_OBJECT_KIND.COMMAND: commands * len(region_ids),
        A.MESH_OBJECT_KIND.EVENT: events * len(region_ids),
        A.MESH_OBJECT_KIND.DESCRIPTOR: descriptors * len(region_ids),
        A.MESH_OBJECT_KIND.TRANSFER: transfers,
        A.MESH_OBJECT_KIND.ALLOCATION: allocations * len(region_ids),
    }
    return {"per_region": per_region, "group": group}


def test_gate5_align_up_is_checked():
    assert align_up(0, 64) == 0
    assert align_up(1, 64) == 64
    assert align_up(64, 64) == 64
    assert align_up(65, 64) == 128
    with pytest.raises(MeshIrError):
        align_up(8, 0)
    with pytest.raises(MeshIrError):
        align_up(8, 48)


def test_gate5_ordinals_are_dense_per_region_and_kind():
    overlay = Overlay(program_instance_id=1, layer_id=LAYER)
    for phase, byte_count in enumerate((96, 32, 64)):
        overlay.add(A.MESH_OBJECT_KIND.ALLOCATION,
                    allocation(1, A.MOE_ALLOCATION_KIND.DISPATCH_REMOTE,
                               byte_count=byte_count, phase=phase))
    overlay.add(A.MESH_OBJECT_KIND.ALLOCATION,
                allocation(2, A.MOE_ALLOCATION_KIND.ROUTE_BUFFER))
    assigned = assign_ordinals(overlay)
    allocations = assigned.of_kind(A.MESH_OBJECT_KIND.ALLOCATION)
    region_one = [record for record in allocations
                  if record.key.region_id == 1]
    region_two = [record for record in allocations
                  if record.key.region_id == 2]
    assert sorted(record.key.ordinal for record in region_one) == [1, 2, 3]
    assert [record.key.ordinal for record in region_two] == [1]
    ordered = sorted(region_one, key=lambda record: record.key.ordinal)
    assert [record.canonical_key() for record in ordered] == \
        sorted(record.canonical_key() for record in region_one)


def test_gate5_ordinals_reject_identical_canonical_keys():
    overlay = Overlay(program_instance_id=1, layer_id=LAYER)
    overlay.add(A.MESH_OBJECT_KIND.ALLOCATION,
                allocation(1, A.MOE_ALLOCATION_KIND.ROUTE_BUFFER))
    overlay.add(A.MESH_OBJECT_KIND.ALLOCATION,
                allocation(1, A.MOE_ALLOCATION_KIND.ROUTE_BUFFER))
    with pytest.raises(MeshIrError) as err:
        assign_ordinals(overlay)
    assert err.value.code == "E_MOE_KEY_COLLISION"


def test_gate5_group_transfers_live_in_region_zero():
    overlay = Overlay(program_instance_id=1, layer_id=LAYER)
    overlay.add(A.MESH_OBJECT_KIND.TRANSFER, TransferRecord(
        phase=A.MOE_TRANSFER_PHASE.DISPATCH, src_core=0, dst_core=1,
        expert_id=0, chunk_ordinal=0, logical_bytes=128,
        key=key(0, A.MESH_OBJECT_KIND.TRANSFER)))
    overlay.add(A.MESH_OBJECT_KIND.EVENT, event(
        0, 0, A.MOE_EVENT_ROLE.OVERLAY_EXIT, ((0, 1),)))
    assigned = assign_ordinals(overlay)
    assert [record.key.region_id for record in
            assigned.of_kind(A.MESH_OBJECT_KIND.TRANSFER)] == [0]
    assert [record.key.ordinal for record in
            assigned.of_kind(A.MESH_OBJECT_KIND.TRANSFER)] == [1]
    assert [record.key.ordinal for record in
            assigned.of_kind(A.MESH_OBJECT_KIND.EVENT)] == [1]


def test_gate5_scratch_bump_allocates_in_kind_order():
    overlay = Overlay(program_instance_id=1, layer_id=LAYER)
    overlay.add(A.MESH_OBJECT_KIND.ALLOCATION, allocation(
        1, A.MOE_ALLOCATION_KIND.EXPERT_OUTPUT, byte_count=64, alignment=64))
    overlay.add(A.MESH_OBJECT_KIND.ALLOCATION, allocation(
        1, A.MOE_ALLOCATION_KIND.ROUTE_BUFFER, byte_count=64, alignment=64))
    overlay.add(A.MESH_OBJECT_KIND.ALLOCATION, allocation(
        1, A.MOE_ALLOCATION_KIND.DISPATCH_REMOTE, byte_count=32,
        alignment=128))
    assigned = assign_ordinals(overlay)
    intervals = allocate_scratch(assigned, {1: (0x80000, 0x40000, 64)})
    by_kind = {record.allocation_kind: intervals[(1, record.key.ordinal)]
               for record in assigned.of_kind(A.MESH_OBJECT_KIND.ALLOCATION)}
    assert by_kind[A.MOE_ALLOCATION_KIND.ROUTE_BUFFER] == (0x80000, 64)
    assert by_kind[A.MOE_ALLOCATION_KIND.DISPATCH_REMOTE] == (0x80080, 32)
    assert by_kind[A.MOE_ALLOCATION_KIND.EXPERT_OUTPUT] == (0x800C0, 64)


def test_gate5_scratch_rejects_exhaustion_and_unknown_region():
    overlay = Overlay(program_instance_id=1, layer_id=LAYER)
    overlay.add(A.MESH_OBJECT_KIND.ALLOCATION, allocation(
        1, A.MOE_ALLOCATION_KIND.ROUTE_BUFFER, byte_count=256))
    assigned = assign_ordinals(overlay)
    with pytest.raises(MeshIrError) as err:
        allocate_scratch(assigned, {1: (0x80000, 128, 64)})
    assert err.value.code == "E_MOE_MATERIALIZE_CAPACITY"
    with pytest.raises(MeshIrError) as err:
        allocate_scratch(assigned, {2: (0x80000, 128, 64)})
    assert err.value.code == "E_MOE_MATERIALIZE_CAPACITY"


def test_gate5_row_bitmap_validity_is_exact():
    shape = ValidityShape(A.MOE_VALIDITY_KIND.ROW_BITMAP, row_bytes=32,
                          row_count=9, bitmap_lowerhex="ff01")
    shape.validate(288)
    with pytest.raises(MeshIrError):
        ValidityShape(A.MOE_VALIDITY_KIND.ROW_BITMAP, row_bytes=32,
                      row_count=8, bitmap_lowerhex="ff01").validate(256)
    with pytest.raises(MeshIrError):
        ValidityShape(A.MOE_VALIDITY_KIND.ROW_BITMAP, row_bytes=32,
                      row_count=9, bitmap_lowerhex="ff03").validate(288)
    with pytest.raises(MeshIrError):
        ValidityShape(A.MOE_VALIDITY_KIND.FULL_PREFIX, valid_bytes=64,
                      row_bytes=32).validate(64)
    with pytest.raises(MeshIrError):
        ValidityShape(A.MOE_VALIDITY_KIND.FULL_PREFIX,
                      valid_bytes=65).validate(64)


def test_gate5_structural_verifier_accepts_a_minimal_overlay():
    overlay = assign_ordinals(simple_overlay())
    verify_overlay(overlay, bounds_of())


def test_gate5_structural_verifier_rejects_duplicate_producers():
    overlay = simple_overlay()
    overlay.objects[A.MESH_OBJECT_KIND.EVENT] = [event(
        1, 0, A.MOE_EVENT_ROLE.REGION_TERMINAL, ((1, 1), (1, 2)))]
    assigned = assign_ordinals(overlay)
    with pytest.raises(MeshIrError) as err:
        verify_overlay(assigned, bounds_of())
    assert err.value.code == "E_MOE_MATERIALIZATION_V"


def test_gate5_structural_verifier_rejects_missing_producer_and_cycles():
    producer = command(1, 0, A.MOE_COMMAND_ROLE.REGION_TERMINAL,
                       signals=((1, A.MESH_OBJECT_KIND.EVENT, 1),), chunk_ordinal=0)
    consumer = command(2, 0, A.MOE_COMMAND_ROLE.REGION_TERMINAL,
                       waits=((3, A.MESH_OBJECT_KIND.EVENT, 1),), chunk_ordinal=1)
    overlay = Overlay(program_instance_id=1, layer_id=LAYER)
    overlay.add(A.MESH_OBJECT_KIND.COMMAND, producer)
    overlay.add(A.MESH_OBJECT_KIND.COMMAND, consumer)
    assigned = assign_ordinals(overlay)
    with pytest.raises(MeshIrError) as err:
        verify_overlay(assigned, bounds_of(region_ids=(1, 2)))
    assert err.value.code == "E_MOE_MATERIALIZATION_V"
    first = command(1, 0, A.MOE_COMMAND_ROLE.REGION_TERMINAL,
                    waits=((1, A.MESH_OBJECT_KIND.EVENT, 2),), signals=((1, A.MESH_OBJECT_KIND.EVENT, 1),), chunk_ordinal=0)
    second = command(1, 0, A.MOE_COMMAND_ROLE.REGION_TERMINAL,
                     waits=((1, A.MESH_OBJECT_KIND.EVENT, 1),), signals=((1, A.MESH_OBJECT_KIND.EVENT, 2),), chunk_ordinal=1)
    cyclic = Overlay(program_instance_id=1, layer_id=LAYER)
    cyclic.add(A.MESH_OBJECT_KIND.COMMAND, first)
    cyclic.add(A.MESH_OBJECT_KIND.COMMAND, second)
    assigned = assign_ordinals(cyclic)
    with pytest.raises(MeshIrError) as err:
        verify_overlay(assigned, bounds_of())
    assert err.value.code == "E_MOE_MATERIALIZATION_V"


def test_gate5_structural_verifier_enforces_region_and_group_bounds():
    overlay = Overlay(program_instance_id=1, layer_id=LAYER)
    for chunk in range(3):
        overlay.add(A.MESH_OBJECT_KIND.COMMAND,
                    command(1, 0, A.MOE_COMMAND_ROLE.REGION_TERMINAL,
                            chunk_ordinal=chunk))
    assigned = assign_ordinals(overlay)
    with pytest.raises(MeshIrError) as err:
        verify_overlay(assigned, bounds_of(commands=2))
    assert err.value.code == "E_MOE_MATERIALIZE_CAPACITY"
    tight_group = bounds_of(commands=3)
    tight_group["group"][A.MESH_OBJECT_KIND.COMMAND] = 2
    with pytest.raises(MeshIrError) as err:
        verify_overlay(assigned, tight_group)
    assert err.value.code == "E_MOE_MATERIALIZE_CAPACITY"


def test_gate5_empty_region_terminal_carries_no_payload():
    overlay = Overlay(program_instance_id=1, layer_id=LAYER)
    overlay.add(A.MESH_OBJECT_KIND.COMMAND, command(
        1, 0, A.MOE_COMMAND_ROLE.EMPTY_REGION_TERMINAL, payload=64))
    assigned = assign_ordinals(overlay)
    with pytest.raises(MeshIrError):
        verify_overlay(assigned, bounds_of())


def test_gate5_combine_commands_need_an_explicit_view():
    overlay = Overlay(program_instance_id=1, layer_id=LAYER)
    overlay.add(A.MESH_OBJECT_KIND.COMMAND,
                command(1, 0, A.MOE_COMMAND_ROLE.LOCAL_REDUCE))
    assigned = assign_ordinals(overlay)
    with pytest.raises(MeshIrError):
        verify_overlay(assigned, bounds_of())
    with_view = Overlay(program_instance_id=1, layer_id=LAYER)
    with_view.add(A.MESH_OBJECT_KIND.COMMAND,
                  command(1, 0, A.MOE_COMMAND_ROLE.LOCAL_REDUCE,
                          views=((1, A.MESH_OBJECT_KIND.VIEW, 1),)))
    assigned = assign_ordinals(with_view)
    verify_overlay(assigned, bounds_of())


def test_gate5_views_reject_unknown_backing_and_escape():
    overlay = Overlay(program_instance_id=1, layer_id=LAYER)
    overlay.add(A.MESH_OBJECT_KIND.VIEW, replace(
        view(1), backing_kind=9))
    assigned = assign_ordinals(overlay)
    with pytest.raises(MeshIrError):
        verify_overlay(assigned, bounds_of())
    escaping = Overlay(program_instance_id=1, layer_id=LAYER)
    escaping.add(A.MESH_OBJECT_KIND.ALLOCATION,
                 allocation(1, A.MOE_ALLOCATION_KIND.ROUTE_BUFFER,
                            byte_count=64))
    escaping.add(A.MESH_OBJECT_KIND.VIEW, replace(
        view(1, byte_count=128), backing_ref=(1, 1)))
    assigned = assign_ordinals(escaping)
    with pytest.raises(MeshIrError):
        verify_overlay(assigned, bounds_of())
    missing_backing = Overlay(program_instance_id=1, layer_id=LAYER)
    missing_backing.add(A.MESH_OBJECT_KIND.VIEW, replace(
        view(1, byte_count=32), backing_ref=(1, 7)))
    assigned = assign_ordinals(missing_backing)
    with pytest.raises(MeshIrError):
        verify_overlay(assigned, bounds_of())


def test_gate5_external_events_are_allowed_as_entry_gate():
    overlay = Overlay(program_instance_id=1, layer_id=LAYER)
    overlay.add(A.MESH_OBJECT_KIND.COMMAND, command(
        1, 0, A.MOE_COMMAND_ROLE.REGION_TERMINAL,
        waits=((1, A.MESH_OBJECT_KIND.EVENT, 99),)))
    assigned = assign_ordinals(overlay)
    with pytest.raises(MeshIrError):
        verify_overlay(assigned, bounds_of())
    verify_overlay(assigned, bounds_of(),
                   external_events=frozenset(
                       {(1, A.MESH_OBJECT_KIND.EVENT, 99)}))


def test_gate5_overlay_enums_are_frozen_by_the_contract():
    assert [vars(A.MOE_ALLOCATION_KIND)[name] for name in
            ("ROUTE_BUFFER", "STREAMED_WEIGHT", "DISPATCH_REMOTE", "PAD_INPUT",
             "EXPERT_OUTPUT", "COMBINE_REMOTE", "REDUCE_OUTPUT")] == \
        list(range(7))
    assert [vars(A.MOE_VIEW_KIND)[name] for name in
            ("ROUTE_METADATA", "MEMBER_INPUT", "DISPATCH_BUFFER", "PAD_BUFFER",
             "WEIGHT", "EXPERT_OUTPUT", "COMBINE_BUFFER", "REDUCE_ACCUMULATOR",
             "MEMBER_OUTPUT")] == list(range(9))
    assert [vars(A.MOE_COMMAND_PHASE)[name] for name in
            ("ROUTE_FILL", "STREAMED_WEIGHT_LOAD", "DISPATCH_PUSH",
             "DISPATCH_WAIT", "PAD_FILL", "EXPERT_COMPUTE", "COMBINE_PUSH",
             "COMBINE_WAIT", "DROPPED_TOKEN_FILL", "COPY_THROUGH",
             "LOCAL_REDUCE", "EXIT_SIGNAL")] == list(range(12))
    assert A.MOE_COMMAND_PHASE.DROPPED_TOKEN_FILL == 8
    assert A.MOE_COMMAND_PHASE.COPY_THROUGH == 9
    assert A.MOE_COMMAND_PHASE.LOCAL_REDUCE == 10
    assert A.MOE_COMMAND_PHASE.EXIT_SIGNAL == 11
    assert vars(A.MOE_EVENT_PHASE)["EXIT"] == 7
    assert vars(A.MOE_TRANSFER_PHASE)["DISPATCH"] == 0
    assert vars(A.MOE_TRANSFER_PHASE)["COMBINE"] == 1
    assert [vars(A.MOE_DESCRIPTOR_KIND)[name] for name in
            ("ROUTE_FILL", "STREAMED_WEIGHT", "PAD_FILL", "DISPATCH",
             "COMBINE", "DROPPED_TOKEN_FILL")] == list(range(6))
    assert list(vars(A.MOE_VIEW_BACKING)[name] for name in
                ("OVERLAY_ALLOCATION", "STATIC_ALLOCATION",
                 "INSTANCE_MEMBER_BINDING", "WEIGHT_CACHE_SLOT",
                 "KV_RUNTIME_VIEW")) == list(range(5))
    assert list(vars(A.MOE_SEMANTIC_OWNER)[name] for name in
                ("MEMBER", "EXPERT", "TOKEN", "REGION")) == list(range(4))
    assert vars(A.MESH_OBJECT_DOMAIN)["STATIC"] == 0
    assert vars(A.MESH_OBJECT_DOMAIN)["MOE_OVERLAY"] == 1
    assert list(vars(A.MESH_OBJECT_KIND)[name] for name in
                ("COMMAND", "EVENT", "DESCRIPTOR", "TRANSFER", "ALLOCATION",
                 "VIEW")) == list(range(6))
