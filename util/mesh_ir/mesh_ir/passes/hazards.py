from __future__ import annotations

from dataclasses import dataclass

import mesh_ir.ir.kernel_verify as kernel_verify
from mesh_ir.analysis.lifetime import _analyze_verified_lifetimes
from mesh_ir.analysis.regions import spans_overlap, view_access_byte_spans
from mesh_ir.canonical import checked_add_u64, semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.kernel_ir import KernelOpcode, OperandAccessMode
from mesh_ir.passes.static_sram import _StaticSramState, verify_static_sram_state
from mesh_ir.scheduled.model import ScheduledDependencyKind


@dataclass(frozen=True, order=True)
class _HazardEdge:
    variant_ordinal: int
    source_op_id: int
    target_op_id: int
    kind: ScheduledDependencyKind
    source_identity: int


@dataclass(frozen=True)
class _HazardState:
    static: _StaticSramState
    edges: tuple[_HazardEdge, ...]

    @property
    def semantic_sha256(self) -> str:
        return semantic_sha256(self)


def insert_hazard_dependencies_stage(static: _StaticSramState) -> _HazardState:
    if type(static) is not _StaticSramState:
        raise MeshIrError("E_ABI_BOUNDS", "pass 16 requires the exact static SRAM state")
    verify_static_sram_state(static)
    edges = set()
    for ordinal, variant in enumerate(static.variants):
        verified_memory = kernel_verify.verify_kernel_memory(variant.source.records)
        records = verified_memory.records
        dependencies = verified_memory.dependencies
        producer_by_token = {op.done_token: op.op_id for op in records.ops if op.done_token is not None}
        producer_by_state = {transition.new_state_id: op.op_id for op in records.ops for transition in op.writes}
        for op in records.ops:
            if op.opcode in (KernelOpcode.ALLOC, KernelOpcode.VIEW):
                continue
            for token_id in op.after_tokens:
                source = producer_by_token.get(token_id)
                if source is not None:
                    edges.add(_HazardEdge(ordinal, source, op.op_id, ScheduledDependencyKind.KERNEL_CONTROL, token_id))
            for state_id in tuple(item.state_id for item in op.reads) + tuple(item.old_state_id for item in op.writes):
                source = producer_by_state.get(state_id)
                if source is not None:
                    edges.add(_HazardEdge(ordinal, source, op.op_id, ScheduledDependencyKind.KERNEL_STATE, state_id))
        tensors = {item.tensor_id: item for item in records.tensors}
        shards = {item.shard_id: item for item in records.shards}
        objects = {item.object_id: item for item in records.objects}
        views = {item.view_id: item for item in records.views}
        accesses = []
        for op in records.ops:
            if op.opcode in (KernelOpcode.ALLOC, KernelOpcode.VIEW):
                continue
            for access in op.reads:
                accesses.append((op.op_id, views[access.view_id].object_id, access.mode, view_access_byte_spans(access, views, objects, tensors, shards)))
            for access in op.writes:
                accesses.append((op.op_id, views[access.view_id].object_id, access.mode, view_access_byte_spans(access, views, objects, tensors, shards)))
        for index, left in enumerate(accesses):
            for right in accesses[index + 1:]:
                if left[0] == right[0] or left[1] != right[1] or left[2] is OperandAccessMode.READ and right[2] is OperandAccessMode.READ or not spans_overlap(left[3], right[3]):
                    continue
                left_node = dependencies.op_node_by_id[left[0] - 1]
                right_node = dependencies.op_node_by_id[right[0] - 1]
                if dependencies.graph.happens_before(left_node, right_node):
                    source, target, first, second = left[0], right[0], left[2], right[2]
                elif dependencies.graph.happens_before(right_node, left_node):
                    source, target, first, second = right[0], left[0], right[2], left[2]
                else:
                    raise MeshIrError("E_DEPENDENCY_CYCLE", "overlapping physical accesses are unordered", first_op_id=left[0], second_op_id=right[0])
                kind = ScheduledDependencyKind.RAW if first is OperandAccessMode.WRITE and second is OperandAccessMode.READ else ScheduledDependencyKind.WAR if first is OperandAccessMode.READ else ScheduledDependencyKind.WAW
                edges.add(_HazardEdge(ordinal, source, target, kind, left[1]))
        plan = static.variants[ordinal].plan
        lifetimes = _analyze_verified_lifetimes(records, dependencies)
        lifetime_by_object = {item.object_id: item for item in lifetimes.objects}
        allocations = plan.allocations
        for index, left in enumerate(allocations):
            left_end = checked_add_u64(left.offset_bytes, left.size_bytes, "SRAM reuse interval")
            for right in allocations[index + 1:]:
                if left.owner_core != right.owner_core or max(left.offset_bytes, right.offset_bytes) >= min(left_end, checked_add_u64(right.offset_bytes, right.size_bytes, "SRAM reuse interval")):
                    continue
                first = lifetime_by_object[left.object_id]
                second = lifetime_by_object[right.object_id]
                first_before = all(dependencies.graph.happens_before(end, start) for end in first.maximal_completion_nodes for start in second.minimal_start_nodes)
                second_before = all(dependencies.graph.happens_before(end, start) for end in second.maximal_completion_nodes for start in first.minimal_start_nodes)
                if not first_before and not second_before:
                    raise MeshIrError("E_SRAM_OOM", "shared SRAM allocation lacks complete lifetime order", first_object_id=left.object_id, second_object_id=right.object_id)
                earlier, later = (first, second) if first_before else (second, first)
                for source in {item.op_id for item in earlier.intervals if item.completion_node in earlier.maximal_completion_nodes}:
                    for target in {item.op_id for item in later.intervals if item.start_node in later.minimal_start_nodes}:
                        edges.add(_HazardEdge(ordinal, source, target, ScheduledDependencyKind.SRAM_REUSE, earlier.object_id))
    return _HazardState(static, tuple(sorted(edges)))
