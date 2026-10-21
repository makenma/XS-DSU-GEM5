from dataclasses import replace
from pathlib import Path

import pytest

from mesh_ir.architecture import load_arch
from mesh_ir.canonical import U64_MAX
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Access, DmaKind, INVALID_CORE_ID, MemorySpace, TensorRole
from mesh_ir.traffic import (
    AddressRef,
    Binding,
    BindingSlot,
    DescriptorExecution,
    DescriptorIdentity,
    DirectAddress,
    ResolvedDescriptor,
    SlotAddress,
    TrafficDirection,
    calculate_descriptor_traffic,
    resolve_addresses,
)


ROOT = Path(__file__).resolve().parents[4]
ARCH_PATH = ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml"


@pytest.fixture
def arch():
    return load_arch(ARCH_PATH)


@pytest.fixture
def input_slot():
    reference = Binding(1, 0, INVALID_CORE_ID, 0, 8192, 32, Access.READ_ONLY)
    return BindingSlot(
        1,
        "input",
        MemorySpace.HBM,
        0,
        INVALID_CORE_ID,
        64,
        32,
        Access.READ_ONLY,
        reference,
    )


@pytest.fixture
def input_ref():
    return AddressRef(
        1,
        MemorySpace.HBM,
        0,
        INVALID_CORE_ID,
        SlotAddress(1),
        0,
        64,
        64,
        1,
        Access.READ_ONLY,
    )


def test_reference_and_dispatch_use_one_resolver_and_change_page_split_residue(arch, input_slot, input_ref):
    reference = resolve_addresses(
        arch,
        (input_slot,),
        (input_slot.reference_binding,),
        (input_ref,),
        issuing_core=0,
    )
    dispatch_binding = replace(
        input_slot.reference_binding,
        allocation_offset_bytes=4064,
        allocation_size_bytes=64,
    )
    dispatch = resolve_addresses(
        arch,
        (input_slot,),
        (dispatch_binding,),
        (input_ref,),
        issuing_core=0,
    )
    assert reference.addresses[0].address == arch.regions[0].base
    assert dispatch.addresses[0].address == arch.regions[0].base + 4064
    assert reference.identity_sha256 != dispatch.identity_sha256
    local_ref = AddressRef(
        2,
        MemorySpace.CORE_SRAM,
        2,
        0,
        DirectAddress(0, 0, 64, 32, Access.READ_WRITE),
        0,
        64,
        64,
        32,
        Access.READ_WRITE,
    )
    local = resolve_addresses(arch, (), (), (local_ref,), issuing_core=0).addresses[0]
    identity = DescriptorIdentity(1, 1, 1, 1, 0, INVALID_CORE_ID, 1, TensorRole.INPUT, TrafficDirection.READ)
    plans = []
    for result in (reference, dispatch):
        descriptor = ResolvedDescriptor(identity, DmaKind.LOAD, result.addresses[0], local, 1, 64, 64, 64, 64, 64, 16)
        plans.append(calculate_descriptor_traffic(arch, DescriptorExecution(descriptor, 1)))
    assert [plan.bursts for plan in plans] == [1, 2]


def test_direct_unaligned_logical_endpoint_is_legal_with_explicit_physical_padding(arch):
    ref = AddressRef(
        1,
        MemorySpace.HBM,
        0,
        INVALID_CORE_ID,
        DirectAddress(0, 0, 32, 32, Access.READ_ONLY),
        1,
        1,
        1,
        1,
        Access.READ_ONLY,
    )
    resolved = resolve_addresses(arch, (), (), (ref,), issuing_core=0)
    assert resolved.addresses[0].address == arch.regions[0].base + 1
    assert resolved.addresses[0].physical_address == arch.regions[0].base
    assert resolved.addresses[0].physical_span_bytes == 32


def test_direct_reference_cannot_borrow_padding_from_region(arch):
    ref = AddressRef(
        1,
        MemorySpace.HBM,
        0,
        INVALID_CORE_ID,
        DirectAddress(1, 1, 1, 1, Access.READ_ONLY),
        0,
        1,
        1,
        1,
        Access.READ_ONLY,
    )
    with pytest.raises(MeshIrError) as error:
        resolve_addresses(arch, (), (), (ref,), issuing_core=0)
    assert error.value.code == "E_RELOCATION"


@pytest.mark.parametrize("bindings", [(), (Binding(2, 0, INVALID_CORE_ID, 0, 64, 32, Access.READ_ONLY),)])
def test_bindings_must_exactly_cover_slots(arch, input_slot, input_ref, bindings):
    with pytest.raises(MeshIrError) as error:
        resolve_addresses(arch, (input_slot,), bindings, (input_ref,), issuing_core=0)
    assert error.value.code == "E_RELOCATION"


def test_binding_slot_and_address_assertions_cannot_be_overridden(arch, input_slot, input_ref):
    cases = (
        replace(input_slot.reference_binding, region_id=1),
        replace(input_slot.reference_binding, owner_core=0),
        replace(input_slot.reference_binding, allocation_size_bytes=63),
        replace(input_slot.reference_binding, access=Access.READ_WRITE),
    )
    for binding in cases:
        with pytest.raises(MeshIrError) as error:
            resolve_addresses(arch, (input_slot,), (binding,), (input_ref,), issuing_core=0)
        assert error.value.code == "E_RELOCATION"


def test_overlapping_writable_bindings_reject_without_mutating_inputs(arch):
    first_binding = Binding(1, 1, INVALID_CORE_ID, 0, 64, 32, Access.READ_WRITE)
    second_binding = Binding(2, 1, INVALID_CORE_ID, 32, 64, 32, Access.READ_WRITE)
    first_slot = BindingSlot(1, "a", MemorySpace.HOST_SHARED, 1, INVALID_CORE_ID, 64, 32, Access.READ_WRITE, first_binding)
    second_slot = BindingSlot(2, "b", MemorySpace.HOST_SHARED, 1, INVALID_CORE_ID, 64, 32, Access.READ_WRITE, second_binding)
    snapshot = (first_slot, second_slot, first_binding, second_binding)
    with pytest.raises(MeshIrError) as error:
        resolve_addresses(arch, (first_slot, second_slot), (first_binding, second_binding), (), issuing_core=0)
    assert error.value.code == "E_RELOCATION"
    assert snapshot == (first_slot, second_slot, first_binding, second_binding)


@pytest.mark.parametrize(
    "value",
    [True, 1.0, -1, U64_MAX],
)
def test_binding_rejects_invalid_scalar_and_overflow(arch, input_slot, input_ref, value):
    binding = replace(input_slot.reference_binding, allocation_offset_bytes=value)
    with pytest.raises(MeshIrError) as error:
        resolve_addresses(arch, (input_slot,), (binding,), (input_ref,), issuing_core=0)
    assert error.value.code in {"E_CONFIG", "E_ABI_OVERFLOW", "E_RELOCATION"}


def test_sparse_peer_address_uses_numeric_core_aperture(arch):
    sparse = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_2x2.yaml")
    ref = AddressRef(
        1,
        MemorySpace.PEER_SRAM,
        2,
        13,
        DirectAddress(0, 0, 64, 32, Access.READ_WRITE),
        0,
        64,
        64,
        32,
        Access.READ_WRITE,
    )
    resolved = resolve_addresses(sparse, (), (), (ref,), issuing_core=7).addresses[0]
    region = sparse.regions[2]
    assert resolved.address == region.base + 13 * region.tile_stride
    assert resolved.target_router == 3


def test_unknown_owner_region_and_duplicate_refs_are_rejected(arch, input_ref):
    cases = (
        replace(input_ref, owner_core=0),
        replace(input_ref, region_id=99),
    )
    for ref in cases:
        with pytest.raises(MeshIrError):
            resolve_addresses(arch, (), (), (ref,), issuing_core=0)
    direct = replace(
        input_ref,
        origin=DirectAddress(0, 0, 64, 32, Access.READ_ONLY),
    )
    with pytest.raises(MeshIrError, match="ref"):
        resolve_addresses(arch, (), (), (direct, direct), issuing_core=0)


@pytest.mark.parametrize(
    "slot,binding",
    [
        ("region", "valid"),
        ("symbol", "valid"),
        ("reference", "valid"),
        ("valid", "slot_id"),
    ],
)
def test_malformed_nested_binding_records_fail_with_typed_diagnostic(
    arch, input_slot, input_ref, slot, binding
):
    changed_slot = {
        "region": replace(input_slot, region_id=99),
        "symbol": replace(input_slot, symbol=7),
        "reference": replace(input_slot, reference_binding=None),
        "valid": input_slot,
    }[slot]
    changed_binding = {
        "valid": input_slot.reference_binding,
        "slot_id": replace(input_slot.reference_binding, slot_id=[]),
    }[binding]
    with pytest.raises(MeshIrError) as error:
        resolve_addresses(arch, (changed_slot,), (changed_binding,), (input_ref,), issuing_core=0)
    assert error.value.code in {"E_CONFIG", "E_RELOCATION"}


def test_overlapping_writable_reference_bindings_reject_even_when_dispatch_does_not(arch):
    first_reference = Binding(1, 1, INVALID_CORE_ID, 0, 64, 32, Access.READ_WRITE)
    second_reference = Binding(2, 1, INVALID_CORE_ID, 32, 64, 32, Access.READ_WRITE)
    slots = (
        BindingSlot(1, "a", MemorySpace.HOST_SHARED, 1, INVALID_CORE_ID, 64, 32, Access.READ_WRITE, first_reference),
        BindingSlot(2, "b", MemorySpace.HOST_SHARED, 1, INVALID_CORE_ID, 64, 32, Access.READ_WRITE, second_reference),
    )
    dispatch = (
        first_reference,
        replace(second_reference, allocation_offset_bytes=64),
    )
    with pytest.raises(MeshIrError, match="reference"):
        resolve_addresses(arch, slots, dispatch, (), issuing_core=0)


def test_read_only_address_use_may_narrow_a_writable_slot(arch):
    binding = Binding(1, 1, INVALID_CORE_ID, 0, 64, 32, Access.READ_WRITE)
    slot = BindingSlot(1, "state", MemorySpace.HOST_SHARED, 1, INVALID_CORE_ID, 64, 32, Access.READ_WRITE, binding)
    ref = AddressRef(1, MemorySpace.HOST_SHARED, 1, INVALID_CORE_ID, SlotAddress(1), 0, 64, 64, 32, Access.READ_ONLY)
    resolved = resolve_addresses(arch, (slot,), (binding,), (ref,), issuing_core=0)
    assert resolved.addresses[0].access == Access.READ_ONLY
    assert resolved.addresses[0].backing_access == Access.READ_WRITE


def test_binding_and_address_namespaces_are_positive_u32(arch, input_slot, input_ref):
    large_id = 1 << 40
    large_binding = replace(input_slot.reference_binding, slot_id=large_id)
    large_slot = replace(
        input_slot,
        slot_id=large_id,
        reference_binding=large_binding,
    )
    large_ref = replace(
        input_ref,
        ref_id=large_id,
        origin=SlotAddress(large_id),
    )
    with pytest.raises(MeshIrError) as error:
        resolve_addresses(arch, (large_slot,), (large_binding,), (large_ref,), issuing_core=0)
    assert error.value.code == "E_RELOCATION"


def test_reference_and_dispatch_bindings_validate_the_same_physical_interval(arch):
    reference = Binding(1, 0, INVALID_CORE_ID, 0, 32, 32, Access.READ_ONLY)
    dispatch = replace(reference, allocation_size_bytes=64)
    slot = BindingSlot(1, "input", MemorySpace.HBM, 0, INVALID_CORE_ID, 32, 32, Access.READ_ONLY, reference)
    ref = AddressRef(1, MemorySpace.HBM, 0, INVALID_CORE_ID, SlotAddress(1), 1, 32, 32, 1, Access.READ_ONLY)
    with pytest.raises(MeshIrError) as error:
        resolve_addresses(arch, (slot,), (dispatch,), (ref,), issuing_core=0)
    assert error.value.code == "E_RELOCATION"


def test_axi_address_width_rejects_unrepresentable_address_and_accepts_last_beat(arch):
    arch.axi_address_bits = 32
    ref = AddressRef(1, MemorySpace.HBM, 0, INVALID_CORE_ID, DirectAddress(0, 0, 32, 32, Access.READ_ONLY), 0, 32, 32, 32, Access.READ_ONLY)
    with pytest.raises(MeshIrError) as error:
        resolve_addresses(arch, (), (), (ref,), issuing_core=0)
    assert error.value.code == "E_RELOCATION"

    arch.axi_address_bits = 36
    hbm = arch.regions[0]
    arch.regions = (replace(hbm, base=(1 << 36) - hbm.bytes),) + arch.regions[1:]
    boundary_offset = hbm.bytes - 32
    boundary = replace(
        ref,
        origin=DirectAddress(boundary_offset, boundary_offset, 32, 32, Access.READ_ONLY),
    )
    resolved = resolve_addresses(arch, (), (), (boundary,), issuing_core=0).addresses[0]
    assert resolved.physical_address == (1 << 36) - 32
    assert resolved.physical_address + resolved.physical_span_bytes == 1 << 36


def test_slot_backing_must_reserve_full_axi_beats_for_small_dma_region(arch):
    short_binding = Binding(1, 2, 0, 0, 24, 8, Access.READ_WRITE)
    short_slot = BindingSlot(1, "scalar", MemorySpace.CORE_SRAM, 2, 0, 24, 8, Access.READ_WRITE, short_binding)
    ref = AddressRef(1, MemorySpace.CORE_SRAM, 2, 0, SlotAddress(1), 0, 24, 24, 8, Access.READ_WRITE)
    with pytest.raises(MeshIrError, match="physical AXI beats"):
        resolve_addresses(arch, (short_slot,), (short_binding,), (ref,), issuing_core=0)

    full_binding = replace(short_binding, allocation_size_bytes=32, allocation_alignment_bytes=32)
    full_slot = replace(short_slot, reference_binding=full_binding)
    resolved = resolve_addresses(arch, (full_slot,), (full_binding,), (ref,), issuing_core=0).addresses[0]
    assert (resolved.physical_span_bytes, resolved.backing_size_bytes) == (32, 32)
