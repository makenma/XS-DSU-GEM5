from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import logging

from mesh_ir.analysis.access import AccessInterval, access_intervals
from mesh_ir.analysis.dependency import build_kernel_dependency_graph
from mesh_ir.analysis.regions import ByteSpan
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import MemorySpace
from mesh_ir.ir.kernel_ir import OperandAccessMode, StateOrigin


class LifetimeConflictReason(str, Enum):
    PERSISTENT = "PERSISTENT"
    UNORDERED = "UNORDERED"


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
    objects = {item.object_id: item for item in kernel.objects}
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
    for access in access_intervals(kernel, dependencies):
        if access.object_id in intervals:
            intervals[access.object_id].append(access)
    lifetimes = []
    for object_id in sorted(intervals):
        records = tuple(sorted(intervals[object_id], key=lambda item: (item.start_node, item.completion_node, item.op_id, item.mode.value, item.region_spans)))
        operations = tuple(sorted({(item.start_node, item.completion_node) for item in records}))
        first_accesses = []
        for start, completion in operations:
            if not any(dependencies.graph.happens_before(previous_completion, start) for _, previous_completion in first_accesses):
                first_accesses.append((start, completion))
        last_accesses = []
        for start, completion in reversed(operations):
            if not any(dependencies.graph.happens_before(completion, following_start) for following_start, _ in last_accesses):
                last_accesses.append((start, completion))
        minimal = (dependencies.invocation_begin_node,) if object_id in entry_resident else tuple(start for start, _ in first_accesses)
        maximal = tuple(sorted(completion for _, completion in last_accesses))
        if object_id in entry_resident and not maximal:
            maximal = (dependencies.invocation_begin_node,)
        lifetimes.append(ObjectLifetime(object_id, records, minimal, maximal))
    by_id = {item.object_id: item for item in lifetimes}
    conflicts = []
    by_owner = {}
    for item in kernel.objects:
        if item.object_id in by_id:
            by_owner.setdefault(item.owner_core, []).append(item)
    for local_objects in by_owner.values():
        for index, first in enumerate(local_objects):
            for second in local_objects[index + 1 :]:
                left = by_id[first.object_id]
                right = by_id[second.object_id]
                if first.persistent or second.persistent:
                    reason = LifetimeConflictReason.PERSISTENT
                else:
                    if not left.minimal_start_nodes or not right.minimal_start_nodes:
                        continue
                    ordered = all(dependencies.graph.happens_before(end, start) for end in left.maximal_completion_nodes for start in right.minimal_start_nodes) or all(dependencies.graph.happens_before(end, start) for end in right.maximal_completion_nodes for start in left.minimal_start_nodes)
                    if ordered:
                        continue
                    reason = LifetimeConflictReason.UNORDERED
                conflicts.append(LifetimeConflict(first.object_id, second.object_id, reason, left.maximal_completion_nodes, right.minimal_start_nodes, right.maximal_completion_nodes, left.minimal_start_nodes))
        logging.getLogger(__name__).info('lifetime owner complete core=%d objects=%d conflicts=%d', local_objects[0].owner_core, len(local_objects), len(conflicts))
    return LifetimeAnalysis(tuple(lifetimes), tuple(sorted(conflicts, key=lambda item: (item.first_object_id, item.second_object_id))))


def analyze_lifetimes(kernel) -> LifetimeAnalysis:
    kernel.verify()
    return kernel.memory_records()._verification.lifetimes


def analyze_memory_lifetimes(records) -> LifetimeAnalysis:
    from mesh_ir.ir.kernel_verify import verify_kernel_memory

    verified = verify_kernel_memory(records)
    return verified.lifetimes
