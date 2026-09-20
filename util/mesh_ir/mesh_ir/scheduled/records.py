from __future__ import annotations

from dataclasses import dataclass, replace

from mesh_ir.ir.kernel_ir import AllocAttrs, CollectiveAttrs, GemmKernelAttrs, KernelMemoryRecords, LocalReduceAttrs, ViewDeclarationAttrs


@dataclass(frozen=True)
class MemoryIdBases:
    tensors: int
    computations: int
    placements: int
    shards: int
    partial_sums: int
    objects: int
    views: int
    states: int
    tokens: int
    ops: int


def globalize_memory_records(records: KernelMemoryRecords, bases: MemoryIdBases) -> KernelMemoryRecords:
    tensors = tuple(replace(item, tensor_id=item.tensor_id + bases.tensors, producer_computation_id=0 if item.producer_computation_id == 0 else item.producer_computation_id + bases.computations, alias_root_tensor_id=item.alias_root_tensor_id + bases.tensors) for item in records.tensors)
    computations = tuple(replace(item, computation_id=item.computation_id + bases.computations, operand_tensor_ids=tuple(value + bases.tensors for value in item.operand_tensor_ids), result_tensor_id=item.result_tensor_id + bases.tensors) for item in records.computations)
    placements = tuple(replace(item, placement_id=item.placement_id + bases.placements) for item in records.placements)
    shards = tuple(replace(item, shard_id=item.shard_id + bases.shards, tensor_id=item.tensor_id + bases.tensors, placement_id=item.placement_id + bases.placements, partial_sum_id=0 if item.partial_sum_id == 0 else item.partial_sum_id + bases.partial_sums) for item in records.shards)
    partial_sums = tuple(replace(item, partial_sum_id=item.partial_sum_id + bases.partial_sums, computation_id=item.computation_id + bases.computations, semantic_result_tensor_id=item.semantic_result_tensor_id + bases.tensors, accumulator_tensor_id=item.accumulator_tensor_id + bases.tensors, placement_id=item.placement_id + bases.placements) for item in records.partial_sums)
    objects = tuple(replace(item, object_id=item.object_id + bases.objects, storage_tensor_id=item.storage_tensor_id + bases.tensors) for item in records.objects)
    views = tuple(replace(item, view_id=item.view_id + bases.views, object_id=item.object_id + bases.objects, shard_id=item.shard_id + bases.shards) for item in records.views)
    states = tuple(replace(item, state_id=item.state_id + bases.states, object_id=item.object_id + bases.objects, partial_sum_id=0 if item.partial_sum_id == 0 else item.partial_sum_id + bases.partial_sums) for item in records.states)
    tokens = tuple(replace(item, token_id=item.token_id + bases.tokens) for item in records.tokens)
    ops = []
    for item in records.ops:
        attrs = item.attrs
        if type(attrs) is AllocAttrs:
            attrs = replace(attrs, object_id=attrs.object_id + bases.objects)
        elif type(attrs) is ViewDeclarationAttrs:
            attrs = replace(attrs, view_id=attrs.view_id + bases.views)
        elif type(attrs) in (GemmKernelAttrs, CollectiveAttrs, LocalReduceAttrs):
            attrs = replace(attrs, partial_sum_id=0 if attrs.partial_sum_id == 0 else attrs.partial_sum_id + bases.partial_sums)
        reads = tuple(replace(access, state_id=access.state_id + bases.states, view_id=access.view_id + bases.views) for access in item.reads)
        writes = tuple(replace(access, old_state_id=access.old_state_id + bases.states, new_state_id=access.new_state_id + bases.states, view_id=access.view_id + bases.views) for access in item.writes)
        ops.append(replace(item, op_id=item.op_id + bases.ops, computation_id=0 if item.computation_id == 0 else item.computation_id + bases.computations, result_shard_id=0 if item.result_shard_id == 0 else item.result_shard_id + bases.shards, reads=reads, writes=writes, attrs=attrs, after_tokens=tuple(value + bases.tokens for value in item.after_tokens), done_token=None if item.done_token is None else item.done_token + bases.tokens))
    return KernelMemoryRecords(tensors, computations, placements, shards, partial_sums, objects, views, states, tokens, tuple(ops))


__all__ = ["MemoryIdBases", "globalize_memory_records"]
