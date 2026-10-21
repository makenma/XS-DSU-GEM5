from mesh_ir.architecture import ArchManifest
from mesh_ir.canonical import checked_add_u64
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.traffic import Binding, BindingSlot


def balanced_bindings(slots: tuple[BindingSlot, ...], arch: ArchManifest) -> tuple[Binding, ...]:
    if (type(slots) is not tuple or any(type(row) is not BindingSlot for row in slots) or
            len({row.slot_id for row in slots}) != len(slots)):
        raise MeshIrError('E_RELOCATION', 'balanced layout requires unique typed binding slots')
    ranges = tuple((target.dst_node, row) for target in arch.fabric.targets for row in target.ranges)
    cursors = [row.offset_bytes for _, row in ranges]
    loads = {node: 0 for node, _ in ranges}
    bindings = []
    for slot in sorted(slots, key=lambda row: (-row.required_allocation_bytes, row.slot_id)):
        choices = []
        for index, (node, region) in enumerate(ranges):
            if (region.region_id != slot.region_id or region.owner_core != slot.owner_core or
                    region.access < slot.access):
                continue
            alignment = slot.required_allocation_alignment_bytes
            base = arch.regions[region.region_id].base
            absolute = checked_add_u64(base, cursors[index], 'balanced binding address')
            offset = checked_add_u64(absolute, alignment - 1, 'balanced binding alignment') // alignment * alignment - base
            end = checked_add_u64(offset, slot.required_allocation_bytes, 'balanced binding extent')
            if end <= checked_add_u64(region.offset_bytes, region.size_bytes, 'balanced target extent'):
                choices.append((loads[node], node, offset, index, end))
        if not choices:
            raise MeshIrError('E_RELOCATION', 'balanced layout cannot fit the next binding slot',
                slot_id=slot.slot_id, required_bytes=slot.required_allocation_bytes)
        _, node, offset, index, end = min(choices)
        cursors[index] = end
        loads[node] = checked_add_u64(loads[node], slot.required_allocation_bytes, 'balanced owner bytes')
        bindings.append(Binding(slot.slot_id, slot.region_id, slot.owner_core, offset,
            slot.required_allocation_bytes, slot.required_allocation_alignment_bytes, slot.access))
    return tuple(sorted(bindings, key=lambda row: row.slot_id))
