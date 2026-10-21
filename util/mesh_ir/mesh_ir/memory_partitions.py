from __future__ import annotations

from dataclasses import dataclass

from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A


@dataclass(frozen=True)
class ArchPartition:
    kind: int
    base: int
    bytes: int
    alignment: int
    metadata_entries: int
    max_pinned_entries: int = 0
    replacement: str = "LRU"

    def document(self) -> dict:
        return {"kind": next(name for name, value in vars(A.SRAM_PARTITION_KIND).items() if value == self.kind),
                "base": self.base, "bytes": self.bytes, "alignment": self.alignment,
                "metadata_entries": self.metadata_entries,
                "max_pinned_entries": self.max_pinned_entries, "replacement": self.replacement}


def parse_partitions(entries: list[dict]) -> tuple[ArchPartition, ...]:
    result = []
    for entry in entries:
        kind = vars(A.SRAM_PARTITION_KIND).get(entry["kind"])
        if type(kind) is not int:
            raise MeshIrError("E_ARCH_PARTITION", "unknown SRAM partition kind", kind=entry["kind"])
        result.append(ArchPartition(**{**entry, "kind": kind}))
    return tuple(result)


def validate_partitions(partitions: tuple[ArchPartition, ...], sram_bytes: int, slot_bytes: int) -> None:
    if type(partitions) is not tuple or any(type(item) is not ArchPartition for item in partitions):
        raise MeshIrError("E_ARCH_PARTITION", "SRAM partitions must be immutable typed records")
    if type(slot_bytes) is not int or not 0 <= slot_bytes < 1 << 32:
        raise MeshIrError("E_ARCH_PARTITION", "weight cache slot size must be u32")
    kinds = [item.kind for item in partitions]
    if len(set(kinds)) != len(kinds) or any(type(kind) is not int or kind not in A.ENUM_CLOSED_SETS["sram_partition_kind"] for kind in kinds):
        raise MeshIrError("E_ARCH_PARTITION", "SRAM partition kinds must be known and unique")
    for item in partitions:
        if any(type(value) is not int or not 0 <= value < 1 << 64 for value in (item.base, item.bytes, item.alignment, item.metadata_entries, item.max_pinned_entries)):
            raise MeshIrError("E_ARCH_PARTITION", "SRAM partition dimensions must be unsigned integers")
        if any(value >= 1 << 32 for value in (item.alignment, item.metadata_entries, item.max_pinned_entries)):
            raise MeshIrError("E_ARCH_PARTITION", "SRAM partition runtime counters and alignment must fit u32")
        if not item.bytes or not item.alignment or item.alignment & (item.alignment - 1) or item.base % item.alignment:
            raise MeshIrError("E_ARCH_PARTITION", "SRAM partition range must be nonempty and aligned")
        if item.base > sram_bytes or item.bytes > sram_bytes - item.base:
            raise MeshIrError("E_ARCH_PARTITION", "SRAM partition escapes SRAM")
        if not item.metadata_entries or item.max_pinned_entries > item.metadata_entries or item.replacement != "LRU":
            raise MeshIrError("E_ARCH_PARTITION", "SRAM partition metadata or replacement is invalid")
    ordered = sorted(partitions, key=lambda item: item.base)
    if any(left.base + left.bytes > right.base for left, right in zip(ordered, ordered[1:])):
        raise MeshIrError("E_ARCH_PARTITION", "SRAM partitions overlap")
    cache = next((item for item in partitions if item.kind == A.SRAM_PARTITION_KIND.WEIGHT_CACHE), None)
    if cache is None:
        if slot_bytes:
            raise MeshIrError("E_ARCH_PARTITION", "weight cache slot size requires its partition")
    elif not slot_bytes or slot_bytes % cache.alignment or cache.bytes % slot_bytes or cache.metadata_entries != cache.bytes // slot_bytes:
        raise MeshIrError("E_ARCH_PARTITION", "weight cache slots must exactly cover its partition and metadata")
