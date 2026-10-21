from collections import Counter
from collections.abc import Mapping

from mesh_ir.architecture import ArchManifest
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.kernel_ir import KernelMemoryRecords, KernelOpcode


def kernel_command_counts(records: KernelMemoryRecords, arch: ArchManifest) -> dict[int, int]:
    counts = Counter(op.owner_core for op in records.ops
        if op.opcode not in (KernelOpcode.ALLOC, KernelOpcode.VIEW))
    if not counts:
        return {arch.core_ids[0]: 3}
    participants = tuple(sorted(counts, key=arch.core_ids.index))
    counts.update(participants)
    counts[participants[0]] += 2
    return dict(counts)


def verify_command_capacity(counts: Mapping[int, int], arch: ArchManifest, **context) -> None:
    for core, count in sorted(counts.items()):
        if count > arch.command_rom_entries:
            raise MeshIrError('E_ABI_BOUNDS', 'per-core command ROM capacity exceeded',
                **context, core_id=core, required_entries=count,
                capacity_entries=arch.command_rom_entries)
