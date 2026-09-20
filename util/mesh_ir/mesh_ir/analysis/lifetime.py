from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from mesh_ir.analysis.dependency import build_kernel_dependency_graph
from mesh_ir.analysis.regions import ByteSpan, view_access_byte_spans
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import MemorySpace
from mesh_ir.ir.kernel_ir import KernelOpcode, OperandAccessMode, StateOrigin


class LifetimeConflictReason(str, Enum):
    PERSISTENT = "PERSISTENT"
    UNORDERED = "UNORDERED"


@dataclass(frozen=True)
class AccessInterval:
    object_id: int
    op_id: int
    start_node: int
    completion_node: int
    mode: OperandAccessMode
    region_spans: tuple[ByteSpan, ...]
    dma_pinned: bool


@dataclass(frozen=True)
class ObjectLifetime:
    object_id: int
    intervals: tuple[AccessInterval, ...]
    minimal_start_nodes: tuple[int, ...]
    maximal_completion_nodes: tuple[int, ...]


@dataclass(frozen=True)
class LifetimeConflict:
    first_object_id: int
    second_object_id: int
    reason: LifetimeConflictReason
    first_maximal_nodes: tuple[int, ...]
    second_minimal_nodes: tuple[int, ...]
    second_maximal_nodes: tuple[int, ...]
    first_minimal_nodes: tuple[int, ...]


@dataclass(frozen=True)
class LifetimeAnalysis:
    objects: tuple[ObjectLifetime, ...]
    conflicts: tuple[LifetimeConflict, ...]

    def __post_init__(self):
        if type(self.objects) is not tuple or any(type(item) is not ObjectLifetime for item in self.objects):
            raise MeshIrError("E_ABI_BOUNDS", "lifetime objects must be immutable typed records")
        if tuple(item.object_id for item in self.objects) != tuple(sorted(item.object_id for item in self.objects)) or len({item.object_id for item in self.objects}) != len(self.objects):
            raise MeshIrError("E_ABI_ORDER", "lifetime objects must be unique and canonical")
        for item in self.objects:
            if type(item.object_id) is not int or item.object_id < 1 or type(item.intervals) is not tuple or any(type(interval) is not AccessInterval for interval in item.intervals):
                raise MeshIrError("E_ABI_BOUNDS", "object lifetime has invalid field types", object_id=item.object_id)
            if type(item.minimal_start_nodes) is not tuple or type(item.maximal_completion_nodes) is not tuple or any(type(node) is not int or node < 1 for node in item.minimal_start_nodes + item.maximal_completion_nodes):
                raise MeshIrError("E_ABI_BOUNDS", "object lifetime frontier is invalid", object_id=item.object_id)
            for interval in item.intervals:
                if any(type(value) is not int or value < 1 for value in (interval.object_id, interval.op_id, interval.start_node, interval.completion_node)) or interval.object_id != item.object_id or type(interval.mode) is not OperandAccessMode or type(interval.region_spans) is not tuple or any(type(span) is not ByteSpan for span in interval.region_spans) or type(interval.dma_pinned) is not bool:
                    raise MeshIrError("E_ABI_BOUNDS", "access interval has invalid field types", object_id=item.object_id)
        if type(self.conflicts) is not tuple or any(type(item) is not LifetimeConflict for item in self.conflicts):
            raise MeshIrError("E_ABI_BOUNDS", "lifetime conflicts must be immutable typed records")
        pairs = tuple((item.first_object_id, item.second_object_id) for item in self.conflicts)
        if pairs != tuple(sorted(set(pairs))) or any(first >= second for first, second in pairs):
            raise MeshIrError("E_ABI_ORDER", "lifetime conflicts must be unique and canonical")
        object_ids = {item.object_id for item in self.objects}
        for item in self.conflicts:
            if item.first_object_id not in object_ids or item.second_object_id not in object_ids or type(item.reason) is not LifetimeConflictReason:
                raise MeshIrError("E_ABI_BOUNDS", "lifetime conflict references an invalid object")
            frontiers = (item.first_maximal_nodes, item.second_minimal_nodes, item.second_maximal_nodes, item.first_minimal_nodes)
            if any(type(frontier) is not tuple or any(type(node) is not int or node < 1 for node in frontier) for frontier in frontiers):
                raise MeshIrError("E_ABI_BOUNDS", "lifetime conflict frontier is invalid")


def _analyze_verified_lifetimes(kernel, dependencies=None) -> LifetimeAnalysis:
    dependencies = build_kernel_dependency_graph(kernel) if dependencies is None else dependencies
    tensors = {item.tensor_id: item for item in kernel.tensors}
    shards = {item.shard_id: item for item in kernel.shards}
    objects = {item.object_id: item for item in kernel.objects}
    views = {item.view_id: item for item in kernel.views}
    intervals: dict[int, list[AccessInterval]] = {
        item.object_id: []
        for item in kernel.objects
        if item.memory_space is MemorySpace.CORE_SRAM
    }
    entry_resident = {
        state.object_id
        for state in kernel.states
        if state.version == 0
        and state.origin is StateOrigin.PRE_RESIDENT
        and state.object_id in intervals
        and objects[state.object_id].footprint_bytes > 0
    }
    for op in kernel.ops:
        start_node = dependencies.op_node_by_id[op.op_id - 1]
        if not start_node:
            continue
        completion_node = dependencies.token_node_by_id[op.done_token - 1]
        for access in op.reads:
            object_id = views[access.view_id].object_id
            if object_id in intervals:
                intervals[object_id].append(AccessInterval(object_id, op.op_id, start_node, completion_node, access.mode, view_access_byte_spans(access, views, objects, tensors, shards), op.opcode is KernelOpcode.DMA))
        for transition in op.writes:
            object_id = views[transition.view_id].object_id
            if object_id in intervals:
                intervals[object_id].append(AccessInterval(object_id, op.op_id, start_node, completion_node, transition.mode, view_access_byte_spans(transition, views, objects, tensors, shards), op.opcode is KernelOpcode.DMA))
    lifetimes = []
    for object_id in sorted(intervals):
        records = tuple(sorted(intervals[object_id], key=lambda item: (item.start_node, item.completion_node, item.op_id, item.mode.value, item.region_spans)))
        starts = tuple(sorted({item.start_node for item in records}))
        completions = tuple(sorted({item.completion_node for item in records}))
        minimal = (dependencies.invocation_begin_node,) if object_id in entry_resident else tuple(start for start in starts if not any(dependencies.graph.happens_before(completion, start) for completion in completions))
        maximal = tuple(completion for completion in completions if not any(dependencies.graph.happens_before(completion, start) for start in starts))
        if object_id in entry_resident and not maximal:
            maximal = (dependencies.invocation_begin_node,)
        lifetimes.append(ObjectLifetime(object_id, records, minimal, maximal))
    by_id = {item.object_id: item for item in lifetimes}
    conflicts = []
    local_objects = tuple(item for item in kernel.objects if item.object_id in by_id)
    for index, first in enumerate(local_objects):
        for second in local_objects[index + 1 :]:
            if first.owner_core != second.owner_core:
                continue
            left = by_id[first.object_id]
            right = by_id[second.object_id]
            first_before_second = bool(left.maximal_completion_nodes and right.minimal_start_nodes) and all(dependencies.graph.happens_before(end, start) for end in left.maximal_completion_nodes for start in right.minimal_start_nodes)
            second_before_first = bool(right.maximal_completion_nodes and left.minimal_start_nodes) and all(dependencies.graph.happens_before(end, start) for end in right.maximal_completion_nodes for start in left.minimal_start_nodes)
            if not (first.persistent or second.persistent or left.minimal_start_nodes and right.minimal_start_nodes and not (first_before_second or second_before_first)):
                continue
            reason = LifetimeConflictReason.PERSISTENT if first.persistent or second.persistent else LifetimeConflictReason.UNORDERED
            conflicts.append(LifetimeConflict(first.object_id, second.object_id, reason, left.maximal_completion_nodes, right.minimal_start_nodes, right.maximal_completion_nodes, left.minimal_start_nodes))
    return LifetimeAnalysis(tuple(lifetimes), tuple(conflicts))


def analyze_lifetimes(kernel) -> LifetimeAnalysis:
    kernel.verify()
    return _analyze_verified_lifetimes(kernel.memory_records())


def analyze_memory_lifetimes(records) -> LifetimeAnalysis:
    from mesh_ir.ir.kernel_verify import verify_kernel_memory

    verified = verify_kernel_memory(records)
    return _analyze_verified_lifetimes(records, verified.dependencies)
