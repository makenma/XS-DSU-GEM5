from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from typing import Iterator

from mesh_ir.analysis.regions import ByteSpan, view_access_byte_spans
from mesh_ir.ir.kernel_ir import KernelOpcode, OperandAccessMode


@dataclass(frozen=True)
class AccessInterval:
    object_id: int
    op_id: int
    start_node: int
    completion_node: int
    mode: OperandAccessMode
    region_spans: tuple[ByteSpan, ...]
    dma_pinned: bool


def access_intervals(kernel, dependencies) -> tuple[AccessInterval, ...]:
    tensors = {item.tensor_id: item for item in kernel.tensors}
    shards = {item.shard_id: item for item in kernel.shards}
    objects = {item.object_id: item for item in kernel.objects}
    views = {item.view_id: item for item in kernel.views}
    return tuple(
        AccessInterval(views[access.view_id].object_id, op.op_id,
            dependencies.op_node_by_id[op.op_id - 1], dependencies.token_node_by_id[op.done_token - 1],
            access.mode, view_access_byte_spans(access, views, objects, tensors, shards), op.opcode is KernelOpcode.DMA)
        for op in kernel.ops if dependencies.op_node_by_id[op.op_id - 1]
        for access in (*op.reads, *op.writes)
    )


def conflicting_access_frontier(accesses: tuple[AccessInterval, ...]) -> Iterator[tuple[AccessInterval, AccessInterval]]:
    groups: dict[int, list[AccessInterval]] = {}
    for access in accesses:
        if access.region_spans:
            groups.setdefault(access.object_id, []).append(access)
    for records in groups.values():
        if not any(access.mode is OperandAccessMode.WRITE for access in records):
            continue
        boundaries = sorted({value for access in records for span in access.region_spans for value in (span.begin, span.end)})
        writers: list[AccessInterval | None] = [None] * (len(boundaries) - 1)
        readers: list[dict[int, AccessInterval]] = [{} for _ in writers]
        for access in records:
            preceding: dict[tuple[int, OperandAccessMode], AccessInterval] = {}
            for span in access.region_spans:
                for index in range(bisect_left(boundaries, span.begin), bisect_left(boundaries, span.end)):
                    writer = writers[index]
                    if writer is not None and writer.op_id != access.op_id:
                        preceding[writer.op_id, writer.mode] = writer
                    if access.mode is OperandAccessMode.WRITE:
                        for reader in readers[index].values():
                            if reader.op_id != access.op_id:
                                preceding[reader.op_id, reader.mode] = reader
                        readers[index].clear()
                        writers[index] = access
                    else:
                        readers[index][access.op_id] = access
            for previous in preceding.values():
                yield previous, access
