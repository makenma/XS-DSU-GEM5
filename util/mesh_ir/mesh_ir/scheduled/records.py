from __future__ import annotations

from dataclasses import dataclass, replace

from mesh_ir.analysis.derivation import same_records

from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.scheduled.model import IdSpan, ProgramVariant, _ProgramFacts
from mesh_ir.ir.kernel_ir import AllocAttrs, DmaAttrs, RecvWaitAttrs, CollectiveAttrs, GemmKernelAttrs, KernelOp, KernelMemoryRecords, LocalReduceAttrs, ViewDeclarationAttrs


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
    transfers: int


def verify_variant_partitions(semantics, transport) -> None:
    for field, source, program_field in A.VARIANT_MEMBERSHIP_TARGETS:
        target = transport if source == 'transport' else semantics
        total = len(getattr(target, program_field))
        expected = 1
        for variant in semantics.variants:
            span = getattr(variant.membership, field)
            if type(span) is not IdSpan or type(span.first_id) is not int or type(span.count) is not int or span.first_id != expected or span.count < 0:
                raise MeshIrError('E_ABI_ORDER', 'variant membership span is not canonical', field=field, variant_id=variant.variant_id)
            expected += span.count
        if expected != total + 1:
            raise MeshIrError('E_ABI_BOUNDS', 'variant membership does not cover its collection', field=field, expected=total, actual=expected - 1)


def globalize_memory_records(records: KernelMemoryRecords, bases: MemoryIdBases) -> KernelMemoryRecords:
    if all(getattr(bases, field) == 0 for field in bases.__dataclass_fields__):
        return records
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
        elif type(attrs) in (DmaAttrs, RecvWaitAttrs):
            attrs = replace(attrs, transfer_id=attrs.transfer_id + bases.transfers if attrs.transfer_id else 0)
        elif type(attrs) is ViewDeclarationAttrs:
            attrs = replace(attrs, view_id=attrs.view_id + bases.views)
        elif type(attrs) in (GemmKernelAttrs, CollectiveAttrs, LocalReduceAttrs):
            attrs = replace(attrs, partial_sum_id=0 if attrs.partial_sum_id == 0 else attrs.partial_sum_id + bases.partial_sums)
        reads = tuple(replace(access, state_id=access.state_id + bases.states, view_id=access.view_id + bases.views) for access in item.reads)
        writes = tuple(replace(access, old_state_id=access.old_state_id + bases.states, new_state_id=access.new_state_id + bases.states, view_id=access.view_id + bases.views) for access in item.writes)
        ops.append(replace(item, op_id=item.op_id + bases.ops, computation_id=0 if item.computation_id == 0 else item.computation_id + bases.computations, result_shard_id=0 if item.result_shard_id == 0 else item.result_shard_id + bases.shards, reads=reads, writes=writes, attrs=attrs, after_tokens=tuple(value + bases.tokens for value in item.after_tokens), done_token=None if item.done_token is None else item.done_token + bases.tokens))
    return KernelMemoryRecords(tensors, computations, placements, shards, partial_sums, objects, views, states, tokens, tuple(ops))


def transfer_id_extent(ops: tuple[KernelOp, ...]) -> int:
    return max((op.attrs.transfer_id for op in ops
                if type(op.attrs) in (DmaAttrs, RecvWaitAttrs)), default=0)


def _local_id(value: int, span: IdSpan, field: str, optional: bool = False) -> int:
    if type(value) is not int:
        raise MeshIrError("E_ABI_BOUNDS", "cross-variant or unknown semantic identity", field=field, value=value)
    if optional and value == 0:
        return 0
    if not span.first_id <= value < span.first_id + span.count:
        raise MeshIrError("E_ABI_BOUNDS", "cross-variant or unknown semantic identity", field=field, value=value)
    return value - span.first_id + 1


def _slice(items: tuple, span: IdSpan) -> tuple:
    return items[span.first_id - 1:span.first_id - 1 + span.count]


def variant_memory_records(semantics: _ProgramFacts, variant: ProgramVariant, *, source_records: KernelMemoryRecords | None = None) -> KernelMemoryRecords:
    if type(variant) is not ProgramVariant:
        raise MeshIrError('E_ABI_BOUNDS', 'memory projection requires an exact Program variant')
    if source_records is not None and type(source_records) is not KernelMemoryRecords:
        raise MeshIrError('E_ABI_BOUNDS', 'memory source must use exact Kernel records')
    collections = tuple(getattr(semantics, name) for name in (
        'kernel_tensors', 'computations', 'placements', 'logical_shards', 'partial_sums',
        'objects', 'views', 'states', 'tokens', 'kernel_ops'))
    if any(type(items) is not tuple for items in collections):
        raise MeshIrError('E_ABI_BOUNDS', 'memory collections must be immutable tuples')
    cached = variant.__dict__.get('_localized_memory')
    if source_records is None and cached is not None and all(left is right for left, right in zip(collections, cached[0])):
        return cached[1]
    membership = variant.membership
    transfer_base = transfer_id_extent(semantics.kernel_ops[:membership.kernel_ops.first_id - 1])
    tensors = tuple(replace(item, tensor_id=_local_id(item.tensor_id, membership.kernel_tensors, "tensor_id"), producer_computation_id=_local_id(item.producer_computation_id, membership.computations, "producer_computation_id", True), alias_root_tensor_id=_local_id(item.alias_root_tensor_id, membership.kernel_tensors, "alias_root_tensor_id")) for item in _slice(semantics.kernel_tensors, membership.kernel_tensors))
    computations = tuple(replace(item, computation_id=_local_id(item.computation_id, membership.computations, "computation_id"), operand_tensor_ids=tuple(_local_id(value, membership.kernel_tensors, "operand_tensor_id") for value in item.operand_tensor_ids), result_tensor_id=_local_id(item.result_tensor_id, membership.kernel_tensors, "result_tensor_id")) for item in _slice(semantics.computations, membership.computations))
    placements = tuple(replace(item, placement_id=_local_id(item.placement_id, membership.placements, "placement_id")) for item in _slice(semantics.placements, membership.placements))
    shards = tuple(replace(item, shard_id=_local_id(item.shard_id, membership.logical_shards, "logical_shard_id"), tensor_id=_local_id(item.tensor_id, membership.kernel_tensors, "shard_tensor_id"), placement_id=_local_id(item.placement_id, membership.placements, "shard_placement_id"), partial_sum_id=_local_id(item.partial_sum_id, membership.partial_sums, "shard_partial_sum_id", True)) for item in _slice(semantics.logical_shards, membership.logical_shards))
    partials = tuple(replace(item, partial_sum_id=_local_id(item.partial_sum_id, membership.partial_sums, "partial_sum_id"), computation_id=_local_id(item.computation_id, membership.computations, "partial_computation_id"), semantic_result_tensor_id=_local_id(item.semantic_result_tensor_id, membership.kernel_tensors, "partial_result_tensor_id"), accumulator_tensor_id=_local_id(item.accumulator_tensor_id, membership.kernel_tensors, "partial_accumulator_tensor_id"), placement_id=_local_id(item.placement_id, membership.placements, "partial_placement_id")) for item in _slice(semantics.partial_sums, membership.partial_sums))
    objects = tuple(replace(item, object_id=_local_id(item.object_id, membership.objects, "object_id"), storage_tensor_id=_local_id(item.storage_tensor_id, membership.kernel_tensors, "storage_tensor_id")) for item in _slice(semantics.objects, membership.objects))
    views = tuple(replace(item, view_id=_local_id(item.view_id, membership.views, "view_id"), object_id=_local_id(item.object_id, membership.objects, "view_object_id"), shard_id=_local_id(item.shard_id, membership.logical_shards, "view_shard_id")) for item in _slice(semantics.views, membership.views))
    states = tuple(replace(item, state_id=_local_id(item.state_id, membership.states, "state_id"), object_id=_local_id(item.object_id, membership.objects, "state_object_id"), partial_sum_id=_local_id(item.partial_sum_id, membership.partial_sums, "state_partial_sum_id", True)) for item in _slice(semantics.states, membership.states))
    tokens = tuple(replace(item, token_id=_local_id(item.token_id, membership.tokens, "token_id")) for item in _slice(semantics.tokens, membership.tokens))
    ops = []
    for item in _slice(semantics.kernel_ops, membership.kernel_ops):
        attrs = item.attrs
        if type(attrs) is AllocAttrs:
            attrs = replace(attrs, object_id=_local_id(attrs.object_id, membership.objects, "allocation_object_id"))
        elif type(attrs) in (DmaAttrs, RecvWaitAttrs):
            attrs = replace(attrs, transfer_id=attrs.transfer_id - transfer_base if attrs.transfer_id else 0)
        elif type(attrs) is ViewDeclarationAttrs:
            attrs = replace(attrs, view_id=_local_id(attrs.view_id, membership.views, "declaration_view_id"))
        elif type(attrs) in (GemmKernelAttrs, CollectiveAttrs, LocalReduceAttrs):
            attrs = replace(attrs, partial_sum_id=_local_id(attrs.partial_sum_id, membership.partial_sums, "operation_partial_sum_id", True))
        reads = tuple(replace(access, state_id=_local_id(access.state_id, membership.states, "read_state_id"), view_id=_local_id(access.view_id, membership.views, "read_view_id")) for access in item.reads)
        writes = tuple(replace(access, old_state_id=_local_id(access.old_state_id, membership.states, "old_state_id"), new_state_id=_local_id(access.new_state_id, membership.states, "new_state_id"), view_id=_local_id(access.view_id, membership.views, "write_view_id")) for access in item.writes)
        ops.append(replace(item, op_id=_local_id(item.op_id, membership.kernel_ops, "kernel_op_id"), computation_id=_local_id(item.computation_id, membership.computations, "operation_computation_id", True), result_shard_id=_local_id(item.result_shard_id, membership.logical_shards, "result_shard_id", True), reads=reads, writes=writes, attrs=attrs, after_tokens=tuple(_local_id(value, membership.tokens, "after_token_id") for value in item.after_tokens), done_token=None if item.done_token is None else _local_id(item.done_token, membership.tokens, "done_token_id")))
    result = KernelMemoryRecords(tensors, computations, placements, shards, partials, objects, views, states, tokens, tuple(ops))
    if source_records is not None and same_records(result, source_records):
        source_records._verification
        result = source_records
    variant.__dict__['_localized_memory'] = (collections, result)
    return result


__all__ = ["MemoryIdBases", "globalize_memory_records", "transfer_id_extent", "variant_memory_records", "verify_variant_partitions"]
