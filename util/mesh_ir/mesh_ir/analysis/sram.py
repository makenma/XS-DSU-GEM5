from __future__ import annotations

from dataclasses import dataclass

from mesh_ir.analysis.lifetime import LifetimeConflict, _analyze_verified_lifetimes
from mesh_ir.analysis.regions import ByteSpan, merge_spans
from mesh_ir.architecture import validate_arch
from mesh_ir.canonical import checked_add_u64
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import MemorySpace


@dataclass(frozen=True)
class BankSpan:
    bank_id: int
    spans: tuple[ByteSpan, ...]


@dataclass(frozen=True)
class SramAllocation:
    allocation_id: int
    object_id: int
    owner_core: int
    offset_bytes: int
    size_bytes: int
    alignment_bytes: int


@dataclass(frozen=True)
class SramCoreReport:
    owner_core: int
    capacity_bytes: int
    peak_bytes: int
    padding_bytes: int
    fragmentation_bytes: int
    bank_spans: tuple[BankSpan, ...]


@dataclass(frozen=True)
class SramPlan:
    allocations: tuple[SramAllocation, ...]
    reports: tuple[SramCoreReport, ...]
    conflict_witnesses: tuple[LifetimeConflict, ...]


def _align_up(value: int, alignment: int) -> int:
    return checked_add_u64(value, alignment - 1, "SRAM alignment") // alignment * alignment


def _overlap(left_begin: int, left_end: int, right_begin: int, right_end: int) -> bool:
    return left_begin < right_end and right_begin < left_end


def _bank_spans(allocations: tuple[SramAllocation, ...], banks: int, interleave: int) -> tuple[BankSpan, ...]:
    spans: list[list[ByteSpan]] = [[] for _ in range(banks)]
    for allocation in allocations:
        cursor = allocation.offset_bytes
        end = checked_add_u64(cursor, allocation.size_bytes, "SRAM bank interval")
        while cursor < end:
            boundary = checked_add_u64((cursor // interleave) * interleave, interleave, "SRAM bank boundary")
            piece_end = min(boundary, end)
            spans[(cursor // interleave) % banks].append(ByteSpan(cursor, piece_end))
            cursor = piece_end
    return tuple(BankSpan(bank_id, merge_spans(tuple(items))) for bank_id, items in enumerate(spans))


def _plan_verified_sram(kernel, arch, lifetimes, allocations: tuple[SramAllocation, ...] | None = None) -> SramPlan:
    validate_arch(arch)
    local = tuple(item for item in kernel.objects if item.memory_space is MemorySpace.CORE_SRAM)
    lifetime_ids = tuple(item.object_id for item in lifetimes.objects)
    if set(lifetime_ids) != {item.object_id for item in local}:
        raise MeshIrError("E_ABI_BOUNDS", "lifetime analysis does not cover every core SRAM object")
    core_ids = set(arch.core_ids)
    for item in local:
        scalars = (item.object_id, item.storage_tensor_id, item.buffer_index, item.footprint_bytes, item.alignment_bytes)
        if any(type(value) is not int or value < 0 for value in scalars) or item.object_id < 1 or item.storage_tensor_id < 1 or item.alignment_bytes < 1 or item.alignment_bytes & (item.alignment_bytes - 1) or item.owner_core not in core_ids:
            raise MeshIrError("E_ABI_BOUNDS", "core SRAM object has invalid allocation requirements", object_id=item.object_id)
    conflicts = {tuple(sorted((item.first_object_id, item.second_object_id))) for item in lifetimes.conflicts}
    lifetime_by_id = {item.object_id: item for item in lifetimes.objects}
    requirements = {}
    for item in local:
        dma_visible = any(interval.dma_pinned for interval in lifetime_by_id[item.object_id].intervals)
        alignment = max(item.alignment_bytes, arch.sram_base_alignment_bytes, arch.axi_data_bytes if dma_visible else 1)
        size = _align_up(item.footprint_bytes, arch.axi_data_bytes) if dma_visible else item.footprint_bytes
        requirements[item.object_id] = (alignment, size)
    if allocations is None:
        priority = sorted(local, key=lambda item: (-int(item.persistent), -requirements[item.object_id][0], -requirements[item.object_id][1], item.storage_tensor_id, item.buffer_index, item.object_id))
        placed: dict[int, SramAllocation] = {}
        for allocation_id, item in enumerate(priority, 1):
            alignment, size = requirements[item.object_id]
            candidate = 0
            while True:
                candidate = _align_up(candidate, alignment)
                end = checked_add_u64(candidate, size, "SRAM allocation end")
                blockers = tuple(
                    allocation
                    for other_id, allocation in placed.items()
                    if allocation.owner_core == item.owner_core
                    and tuple(sorted((item.object_id, other_id))) in conflicts
                    and _overlap(candidate, end, allocation.offset_bytes, checked_add_u64(allocation.offset_bytes, allocation.size_bytes, "SRAM existing allocation end"))
                )
                if not blockers:
                    break
                candidate = max(checked_add_u64(blocker.offset_bytes, blocker.size_bytes, "SRAM first-fit advance") for blocker in blockers)
            if end > arch.sram_bytes:
                limiting = tuple(sorted((item.object_id, *(blocker.object_id for blocker in placed.values() if blocker.owner_core == item.owner_core and tuple(sorted((item.object_id, blocker.object_id))) in conflicts and blocker.offset_bytes < end))))
                raise MeshIrError("E_SRAM_OOM", "static SRAM allocation exceeds core capacity", owner_core=item.owner_core, capacity_bytes=arch.sram_bytes, required_end=end, limiting_object_ids=limiting)
            placed[item.object_id] = SramAllocation(allocation_id, item.object_id, item.owner_core, candidate, size, alignment)
        allocations = tuple(sorted(placed.values(), key=lambda item: item.allocation_id))
    else:
        if type(allocations) is not tuple or any(type(item) is not SramAllocation for item in allocations):
            raise MeshIrError("E_ABI_BOUNDS", "pinned SRAM allocations must be an exact tuple")
        for allocation in allocations:
            scalars = (allocation.allocation_id, allocation.object_id, allocation.owner_core, allocation.offset_bytes, allocation.size_bytes, allocation.alignment_bytes)
            if any(type(value) is not int or value < 0 for value in scalars) or allocation.allocation_id < 1 or allocation.object_id < 1 or allocation.alignment_bytes < 1 or allocation.alignment_bytes & (allocation.alignment_bytes - 1):
                raise MeshIrError("E_ABI_BOUNDS", "pinned SRAM allocation has invalid fields", allocation_id=allocation.allocation_id)
        if tuple(item.allocation_id for item in allocations) != tuple(range(1, len(allocations) + 1)):
            raise MeshIrError("E_ABI_BOUNDS", "pinned SRAM allocation IDs must be dense and ordered")
        object_by_id = {item.object_id: item for item in local}
        if len(allocations) != len(local) or {item.object_id for item in allocations} != set(object_by_id):
            raise MeshIrError("E_ABI_BOUNDS", "pinned SRAM allocations must cover every local object exactly once")
        for allocation in allocations:
            obj = object_by_id[allocation.object_id]
            required_alignment, required_size = requirements[allocation.object_id]
            if allocation.owner_core != obj.owner_core or allocation.owner_core not in core_ids:
                raise MeshIrError("E_ABI_BOUNDS", "pinned SRAM allocation owner differs from its object", allocation_id=allocation.allocation_id)
            if allocation.alignment_bytes < required_alignment or allocation.offset_bytes % allocation.alignment_bytes or allocation.size_bytes < required_size:
                raise MeshIrError("E_ABI_BOUNDS", "pinned SRAM allocation does not satisfy its storage requirements", allocation_id=allocation.allocation_id, required_alignment=required_alignment, required_size=required_size)
            end = checked_add_u64(allocation.offset_bytes, allocation.size_bytes, "pinned SRAM allocation end")
            if end > arch.sram_bytes:
                raise MeshIrError("E_SRAM_OOM", "pinned SRAM allocation exceeds core capacity", allocation_id=allocation.allocation_id, owner_core=allocation.owner_core, capacity_bytes=arch.sram_bytes, required_end=end)
        for index, first in enumerate(allocations):
            first_end = checked_add_u64(first.offset_bytes, first.size_bytes, "pinned SRAM allocation end")
            for second in allocations[index + 1 :]:
                second_end = checked_add_u64(second.offset_bytes, second.size_bytes, "pinned SRAM allocation end")
                if first.owner_core == second.owner_core and tuple(sorted((first.object_id, second.object_id))) in conflicts and _overlap(first.offset_bytes, first_end, second.offset_bytes, second_end):
                    raise MeshIrError("E_SRAM_OOM", "conflicting pinned SRAM lifetimes overlap", owner_core=first.owner_core, first_object_id=first.object_id, second_object_id=second.object_id)
    reports = []
    for owner in sorted({item.owner_core for item in allocations}, key=arch.core_ids.index):
        owned = tuple(item for item in allocations if item.owner_core == owner)
        peak = max((checked_add_u64(item.offset_bytes, item.size_bytes, "SRAM peak") for item in owned), default=0)
        occupied = merge_spans(tuple(ByteSpan(item.offset_bytes, checked_add_u64(item.offset_bytes, item.size_bytes, "SRAM occupied interval")) for item in owned))
        occupied_bytes = sum(span.end - span.begin for span in occupied)
        object_by_id = {item.object_id: item for item in local}
        padding = sum(item.size_bytes - object_by_id[item.object_id].footprint_bytes for item in owned)
        reports.append(SramCoreReport(owner, arch.sram_bytes, peak, padding, peak - occupied_bytes, _bank_spans(owned, arch.sram_banks, arch.sram_bank_interleave_bytes)))
    relevant = tuple(item for item in lifetimes.conflicts if any(allocation.object_id == item.first_object_id for allocation in allocations) and any(allocation.object_id == item.second_object_id for allocation in allocations))
    return SramPlan(allocations, tuple(reports), relevant)


def plan_memory_sram(records, arch, *, allocations: tuple[SramAllocation, ...] | None = None) -> SramPlan:
    from mesh_ir.ir.kernel_verify import verify_kernel_memory

    verified = verify_kernel_memory(records, arch)
    lifetimes = _analyze_verified_lifetimes(records, verified.dependencies)
    return _plan_verified_sram(records, arch, lifetimes, allocations)


def plan_static_sram(kernel, arch) -> SramPlan:
    kernel.verify(arch)
    records = kernel.memory_records()
    return _plan_verified_sram(records, arch, _analyze_verified_lifetimes(records))
