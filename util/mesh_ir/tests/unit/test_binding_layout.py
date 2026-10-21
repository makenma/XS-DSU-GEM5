from dataclasses import replace

import pytest

from mesh_ir.architecture import load_arch
from mesh_ir.binding_layout import balanced_bindings
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Access, MemorySpace, INVALID_CORE_ID
from mesh_ir.traffic import Binding, BindingSlot


def test_external_layout_balances_actual_owner_capacity_and_is_deterministic():
    arch = load_arch('configs/example/ai_mesh/arch/mesh_1x2.yaml')
    original = arch.fabric.targets[0]
    source = original.ranges[0]
    owners = tuple(replace(original, name=f'bank-{i}', dst_node=100 + i,
        ranges=(replace(source, offset_bytes=i * 4096, size_bytes=4096),)) for i in range(5))
    arch.fabric = replace(arch.fabric, targets=owners)
    slots = tuple(BindingSlot(i + 1, f'weight-{i}', MemorySpace.HBM, 0,
        INVALID_CORE_ID, 1024, 64, Access.READ_ONLY,
        Binding(i + 1, 0, INVALID_CORE_ID, i * 1024, 1024, 64, Access.READ_ONLY)) for i in range(10))
    bindings = balanced_bindings(slots, arch)
    assert [row.allocation_offset_bytes // 4096 for row in bindings] == list(range(5)) * 2
    assert [row.allocation_offset_bytes % 4096 for row in bindings] == [0] * 5 + [1024] * 5
    assert balanced_bindings(tuple(reversed(slots)), arch) == bindings
    arch.fabric = replace(arch.fabric, targets=tuple(reversed(owners)))
    assert balanced_bindings(slots, arch) == bindings
    assert all(row.allocation_offset_bytes % row.allocation_alignment_bytes == 0 for row in bindings)


def test_external_layout_obeys_writable_ranges_and_rejects_exhaustion():
    arch = load_arch('configs/example/ai_mesh/arch/mesh_1x2.yaml')
    original = arch.fabric.targets[0]
    source = original.ranges[0]
    arch.fabric = replace(arch.fabric, targets=(
        replace(original, ranges=(replace(source, size_bytes=128, access=Access.READ_ONLY),)),
        replace(original, name='writable', dst_node=101,
            ranges=(replace(source, offset_bytes=4096, size_bytes=128),))))
    reference = Binding(1, 0, INVALID_CORE_ID, 0, 128, 64, Access.READ_ONLY)
    readonly = BindingSlot(1, 'weight', MemorySpace.HBM, 0, INVALID_CORE_ID, 128, 64, Access.READ_ONLY, reference)
    writable = replace(readonly, slot_id=2, symbol='output', access=Access.READ_WRITE,
        reference_binding=replace(reference, slot_id=2, access=Access.READ_WRITE))
    first, second = balanced_bindings((readonly, writable), arch)
    assert (first.allocation_offset_bytes, second.allocation_offset_bytes) == (0, 4096)
    with pytest.raises(MeshIrError, match='E_RELOCATION'):
        balanced_bindings((readonly, writable, replace(readonly, slot_id=3, symbol='overflow')), arch)
