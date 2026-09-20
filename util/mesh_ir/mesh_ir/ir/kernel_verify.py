from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass

from mesh_ir.analysis.regions import ByteSpan, merge_spans, region_byte_spans, spans_contain, spans_overlap, view_access_byte_spans
from mesh_ir.canonical import U64_MAX, checked_add_u64, checked_mul_u64, semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Access, Const, DType, DmaKind, Engine, FixedStride, INVALID_CORE_ID, Layout, MemorySpace, StorageClass, TensorRole
from mesh_ir.ir.graph_ir import ElementwiseAttrs, EmbeddingAttrs, MatmulAttrs, METADATA_VIEW_OPCODES, MovementAttrs, NormAttrs, OpCode, ReduceAttrs, SoftmaxAttrs, ViewAttrs
from mesh_ir.ir.graph_verify import MatrixResultForm, TensorDataContract, TensorLayoutContract, verify_operation_attribute_types, verify_operation_data_contract, verify_operation_layout_contract
from mesh_ir.schema import validate_schema


U32_MAX = (1 << 32) - 1
_DIGEST = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, order=True)
class KernelCapabilityRequirement:
    op_id: int
    engine: Engine
    dtype: DType

    def __post_init__(self):
        if not _u32(self.op_id) or type(self.engine) is not Engine or type(self.dtype) is not DType:
            raise MeshIrError("E_ABI_BOUNDS", "Kernel capability requirement has invalid field types")


def _u32(value: object, allow_zero: bool = False) -> bool:
    return type(value) is int and (0 if allow_zero else 1) <= value <= U32_MAX


def _u64(value: object) -> bool:
    return type(value) is int and 0 <= value <= U64_MAX


def _u64_tuple(value: object) -> bool:
    return type(value) is tuple and all(_u64(item) for item in value)


def _dense(records: tuple, field: str, label: str) -> None:
    actual = tuple(getattr(item, field) for item in records)
    if actual != tuple(range(1, len(records) + 1)):
        raise MeshIrError("E_ABI_ORDER", f"{label} identifiers must be dense and ordered", actual=actual)


def _product(values: tuple[int, ...], field: str) -> int:
    result = 1
    for value in values:
        result = checked_mul_u64(result, value, field)
    return result


def _storage_elements(shape: tuple[int, ...], strides: tuple[int, ...], offset: int = 0) -> int:
    if any(dimension == 0 for dimension in shape):
        return 0
    result = checked_add_u64(offset, 1, "storage extent")
    for dimension, stride in zip(shape, strides):
        result = checked_add_u64(result, checked_mul_u64(dimension - 1, stride, "storage extent"), "storage extent")
    return result


def _power_of_two(value: int) -> bool:
    return value > 0 and value & (value - 1) == 0


def _bad_type(message: str, **context) -> None:
    raise MeshIrError("E_ABI_BOUNDS", message, **context)


def _verify_region_type(region) -> None:
    from mesh_ir.ir.kernel_ir import ElementRegion

    if type(region) is not ElementRegion or not _u64_tuple(region.origin) or not _u64_tuple(region.shape) or not _u64_tuple(region.steps) or not len(region.origin) == len(region.shape) == len(region.steps) or any(step < 1 for step in region.steps):
        _bad_type("element region has invalid field types")


def _verify_tile_and_cost(tile, cost) -> None:
    from mesh_ir.ir.kernel_ir import KernelCost, KernelTile

    if type(tile) is not KernelTile or any(not _u64(getattr(tile, field.name)) for field in dataclasses.fields(tile)):
        _bad_type("kernel tile has invalid field types")
    if tile.valid_batch > tile.batch_extent or tile.valid_m > tile.m_extent or tile.valid_n > tile.n_extent or tile.valid_k > tile.k_extent:
        raise MeshIrError("E_EXPORT_LAYOUT", "valid tile extent exceeds padded extent")
    if type(cost) is not KernelCost or any(not _u64(getattr(cost, field.name)) for field in dataclasses.fields(cost)):
        _bad_type("kernel cost has invalid field types")


def _verify_attrs_type(opcode, attrs) -> None:
    from mesh_ir.ir.kernel_ir import (
        AllocAttrs,
        BarrierAttrs,
        CollectiveAlgorithm,
        CollectiveAttrs,
        CollectiveKind,
        DmaAttrs,
        GemmKernelAttrs,
        KERNEL_ATTR_TYPE_BY_OPCODE,
        KernelOpcode,
        LocalCopyAttrs,
        LocalReduceAttrs,
        MatrixEpilogueAlgorithm,
        MatrixEpilogueKernelAttrs,
        MatrixPhase,
        MovementAlgorithm,
        MovementKernelAttrs,
        NormAlgorithm,
        NormKernelAttrs,
        RecvWaitAttrs,
        ReduceKind,
        ReductionAlgorithm,
        ReductionKernelAttrs,
        SoftmaxAlgorithm,
        SoftmaxKernelAttrs,
        VectorAlgorithm,
        VectorKernelAttrs,
        ViewDeclarationAttrs,
    )

    expected = KERNEL_ATTR_TYPE_BY_OPCODE[opcode]
    if type(attrs) is not expected:
        raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "Kernel opcode has the wrong typed attributes", opcode=opcode.value)
    if type(attrs) is AllocAttrs:
        if not _u32(attrs.object_id):
            _bad_type("allocation declaration identifier is invalid")
        return
    if type(attrs) is ViewDeclarationAttrs:
        if not _u32(attrs.view_id):
            _bad_type("view declaration identifier is invalid")
        return
    if type(attrs) is DmaAttrs:
        cores = (attrs.issuing_core, attrs.source_core, attrs.destination_core)
        if type(attrs.kind) is not DmaKind or any(type(core) is not int or not 0 <= core <= INVALID_CORE_ID for core in cores) or not _u32(attrs.transfer_id, True) or type(attrs.fill_pattern) is not bytes:
            _bad_type("DMA attributes have invalid field types")
        if attrs.max_burst_beats is not None and (type(attrs.max_burst_beats) is not int or not 1 <= attrs.max_burst_beats <= 256):
            _bad_type("DMA burst limit is outside the supported range")
        if attrs.kind is DmaKind.LOCAL_FILL and attrs.max_burst_beats is not None:
            raise MeshIrError("E_DMA_RANGE", "local fill cannot declare an AXI burst limit")
        return
    if type(attrs) is RecvWaitAttrs:
        if not _u32(attrs.transfer_id) or any(type(core) is not int or not 0 <= core < INVALID_CORE_ID for core in (attrs.source_core, attrs.destination_core)) or not _u64(attrs.expected_bytes):
            _bad_type("receive-wait attributes have invalid field types")
        return
    if type(attrs) is GemmKernelAttrs:
        if type(attrs.graph_opcode) is not OpCode or attrs.graph_opcode not in (OpCode.MATMUL, OpCode.BMM, OpCode.LINEAR_BIAS) or type(attrs.semantic_attrs) is not MatmulAttrs or type(attrs.phase) is not MatrixPhase or not _u32(attrs.partial_sum_id, True):
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "matrix attributes do not preserve Graph semantics")
        if (opcode is KernelOpcode.BMM) != (attrs.graph_opcode is OpCode.BMM):
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "matrix Kernel opcode differs from its Graph semantic family")
        verify_operation_attribute_types(attrs.semantic_attrs)
        _verify_tile_and_cost(attrs.tile, attrs.cost)
        return
    if type(attrs) is MatrixEpilogueKernelAttrs:
        if type(attrs.graph_opcode) is not OpCode or attrs.graph_opcode not in (OpCode.MATMUL, OpCode.BMM, OpCode.LINEAR_BIAS) or type(attrs.semantic_attrs) is not MatmulAttrs or type(attrs.algorithm) is not MatrixEpilogueAlgorithm:
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "matrix epilogue attributes are not closed and typed")
        verify_operation_attribute_types(attrs.semantic_attrs)
        _verify_tile_and_cost(attrs.tile, attrs.cost)
        return
    if type(attrs) is VectorKernelAttrs:
        if type(attrs.graph_opcode) is not OpCode or type(attrs.semantic_attrs) not in (ElementwiseAttrs, EmbeddingAttrs) or type(attrs.algorithm) is not VectorAlgorithm:
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "vector attributes are not closed and typed")
        verify_operation_attribute_types(attrs.semantic_attrs)
        if type(attrs.semantic_attrs) is ElementwiseAttrs and attrs.algorithm is not VectorAlgorithm.ELEMENTWISE or type(attrs.semantic_attrs) is EmbeddingAttrs and attrs.algorithm is not VectorAlgorithm.EMBEDDING_GATHER:
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "vector algorithm differs from its semantics")
        if (attrs.graph_opcode is OpCode.EMBEDDING_LOOKUP) != (type(attrs.semantic_attrs) is EmbeddingAttrs):
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "vector Graph opcode differs from its semantic family")
        _verify_tile_and_cost(attrs.tile, attrs.cost)
        return
    if type(attrs) is MovementKernelAttrs:
        if type(attrs.graph_opcode) is not OpCode or type(attrs.semantic_attrs) not in (ViewAttrs, MovementAttrs) or type(attrs.algorithm) is not MovementAlgorithm:
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "movement attributes are not closed and typed")
        verify_operation_attribute_types(attrs.semantic_attrs)
        view_opcodes = (OpCode.CONTIGUOUS_COPY,)
        expected_algorithm = MovementAlgorithm.CONCAT if attrs.graph_opcode is OpCode.CONCAT else MovementAlgorithm.GATHER_ROWS if attrs.graph_opcode is OpCode.GATHER_ROWS else MovementAlgorithm.STRIDED_COPY
        if attrs.graph_opcode not in (*view_opcodes, OpCode.CONCAT, OpCode.GATHER_ROWS) or (attrs.graph_opcode in view_opcodes) != (type(attrs.semantic_attrs) is ViewAttrs) or attrs.algorithm is not expected_algorithm:
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "movement Graph opcode differs from its semantic family")
        _verify_tile_and_cost(attrs.tile, attrs.cost)
        return
    if type(attrs) is ReductionKernelAttrs:
        if type(attrs.graph_opcode) is not OpCode or attrs.graph_opcode not in (OpCode.REDUCE_SUM, OpCode.REDUCE_MAX, OpCode.REDUCE_MEAN) or type(attrs.semantic_attrs) is not ReduceAttrs or type(attrs.algorithm) is not ReductionAlgorithm:
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "reduction attributes are not closed and typed")
        verify_operation_attribute_types(attrs.semantic_attrs)
        _verify_tile_and_cost(attrs.tile, attrs.cost)
        return
    if type(attrs) is SoftmaxKernelAttrs:
        if type(attrs.semantic_attrs) is not SoftmaxAttrs or type(attrs.algorithm) is not SoftmaxAlgorithm:
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "softmax attributes are not closed and typed")
        verify_operation_attribute_types(attrs.semantic_attrs)
        _verify_tile_and_cost(attrs.tile, attrs.cost)
        return
    if type(attrs) is NormKernelAttrs:
        if type(attrs.graph_opcode) is not OpCode or type(attrs.semantic_attrs) is not NormAttrs or type(attrs.algorithm) is not NormAlgorithm:
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "normalization attributes are not closed and typed")
        verify_operation_attribute_types(attrs.semantic_attrs)
        expected_algorithm = NormAlgorithm.LAYER_NORM if attrs.graph_opcode is OpCode.LAYERNORM else NormAlgorithm.RMS_NORM if attrs.graph_opcode is OpCode.RMSNORM else None
        if attrs.algorithm is not expected_algorithm:
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "normalization algorithm differs from its Graph opcode")
        _verify_tile_and_cost(attrs.tile, attrs.cost)
        return
    if type(attrs) is CollectiveAttrs:
        if type(attrs.kind) is not CollectiveKind or type(attrs.algorithm) is not CollectiveAlgorithm or type(attrs.reduce_kind) is not ReduceKind or not _u64_tuple(attrs.participants) or not attrs.participants or not _u64(attrs.chunk_bytes) or not _u32(attrs.partial_sum_id):
            _bad_type("collective attributes have invalid field types")
        return
    if type(attrs) is LocalReduceAttrs:
        if type(attrs.reduce_kind) is not ReduceKind or not _u32(attrs.partial_sum_id):
            _bad_type("local reduction attributes have invalid field types")
        _verify_tile_and_cost(attrs.tile, attrs.cost)
        return
    if type(attrs) is LocalCopyAttrs:
        return
    if type(attrs) is BarrierAttrs and (not _u64_tuple(attrs.participants) or not attrs.participants):
        _bad_type("barrier participants are invalid")


def verify_kernel_types(kernel, allow_empty_semantic_hash: bool = False) -> None:
    from mesh_ir.ir.kernel_ir import (
        KernelComputationLineage,
        BlockedMnkLayout,
        BufferObject,
        BufferView,
        ControlToken,
        DistributionKind,
        KernelComputation,
        KernelMemoryRecords,
        KernelModule,
        KernelOp,
        KernelOpcode,
        KernelTensor,
        KernelTensorLineage,
        OperandAccess,
        OperandAccessMode,
        PartialSumDefinition,
        Placement,
        StateOrigin,
        StateTransition,
        SynthesizedTensorPurpose,
        TensorShard,
        TensorState,
    )

    if type(kernel) is not KernelModule or type(kernel.schema_major) is not int or type(kernel.schema_minor) is not int:
        _bad_type("Kernel module has invalid schema types")
    if type(kernel.required_features) is not tuple or any(type(item) is not str for item in kernel.required_features):
        _bad_type("Kernel required features must be immutable strings")
    for value in (kernel.arch_digest, kernel.source_semantic_hash, kernel.semantic_sha256, kernel.entrypoint, kernel.profile_id):
        if type(value) is not str:
            _bad_type("Kernel identity field has invalid type")
    if not allow_empty_semantic_hash and not kernel.semantic_sha256:
        raise MeshIrError("E_ABI_CHECKSUM", "Kernel semantic hash is absent")
    if type(kernel.tensor_lineage) is not tuple or type(kernel.computation_lineage) is not tuple:
        _bad_type("Kernel lineage tables must be immutable tuples")
    for item in kernel.tensor_lineage:
        if type(item) is not KernelTensorLineage or not _u32(item.tensor_id) or not _u32(item.source_value_id, True):
            _bad_type("Kernel tensor lineage has invalid types")
    for item in kernel.computation_lineage:
        if type(item) is not KernelComputationLineage or not _u32(item.computation_id) or not _u32(item.source_op_id) or type(item.source_node_id) is not str or not item.source_node_id:
            _bad_type("Kernel computation lineage has invalid types")
    _verify_memory_types(kernel.memory_records())


def _verify_memory_types(kernel) -> None:
    from mesh_ir.ir.kernel_ir import (
        BlockedMnkLayout,
        BufferObject,
        BufferView,
        ControlToken,
        DistributionKind,
        KernelComputation,
        KernelMemoryRecords,
        KernelOp,
        KernelOpcode,
        KernelTensor,
        OperandAccess,
        OperandAccessMode,
        PartialSumDefinition,
        Placement,
        StateOrigin,
        StateTransition,
        SynthesizedTensorPurpose,
        TensorShard,
        TensorState,
    )

    if type(kernel) is not KernelMemoryRecords:
        _bad_type("Kernel memory record has invalid type")
    collections = (kernel.tensors, kernel.computations, kernel.placements, kernel.shards, kernel.partial_sums, kernel.objects, kernel.views, kernel.states, kernel.tokens, kernel.ops)
    if any(type(value) is not tuple for value in collections):
        _bad_type("Kernel collections must be immutable tuples")
    for item in kernel.tensors:
        if type(item) is not KernelTensor or not _u32(item.tensor_id) or not _u32(item.producer_computation_id, True) or not _u32(item.alias_root_tensor_id) or not _u64(item.storage_offset_elements) or type(item.name) is not str or not item.name:
            _bad_type("Kernel tensor identity is invalid")
        if type(item.role) is not TensorRole or type(item.dtype) is not DType or type(item.storage_class) is not StorageClass or type(item.access) is not Access:
            raise MeshIrError("E_ABI_ENUM", "Kernel tensor enum has invalid type", tensor_id=item.tensor_id)
        if item.synthesized_purpose is not None and type(item.synthesized_purpose) is not SynthesizedTensorPurpose:
            _bad_type("Kernel tensor optional record has invalid type", tensor_id=item.tensor_id)
        if not _u64_tuple(item.shape) or not _u64_tuple(item.strides) or len(item.shape) != len(item.strides) or not _u64(item.logical_extent_bytes) or not _u64(item.storage_extent_bytes):
            _bad_type("Kernel tensor shape or extent has invalid type", tensor_id=item.tensor_id)
        if item.content_sha256 is not None and type(item.content_sha256) is not str:
            _bad_type("Kernel tensor content identity has invalid type", tensor_id=item.tensor_id)
    for item in kernel.computations:
        if type(item) is not KernelComputation or not _u32(item.computation_id) or type(item.opcode) is not OpCode or type(item.operand_tensor_ids) is not tuple or any(not _u32(tensor_id) for tensor_id in item.operand_tensor_ids) or not _u32(item.result_tensor_id):
            _bad_type("Kernel computation has invalid types")
        verify_operation_attribute_types(item.attrs)
    for item in kernel.placements:
        if type(item) is not Placement or not _u32(item.placement_id) or type(item.core_ids) is not tuple or any(type(core) is not int or not 0 <= core < INVALID_CORE_ID for core in item.core_ids):
            _bad_type("Kernel placement has invalid types")
    for item in kernel.shards:
        if type(item) is not TensorShard or not all(_u32(value) for value in (item.shard_id, item.tensor_id, item.placement_id)) or type(item.owner_core) is not int or not 0 <= item.owner_core < INVALID_CORE_ID or type(item.distribution) is not DistributionKind:
            _bad_type("Kernel shard has invalid identity or enum")
        if not all(_u64_tuple(value) for value in (item.global_origin, item.padded_local_shape, item.valid_shape)) or not _u32(item.partial_sum_id, True):
            _bad_type("Kernel shard region has invalid types")
    for item in kernel.partial_sums:
        if type(item) is not PartialSumDefinition or not all(_u32(value) for value in (item.partial_sum_id, item.computation_id, item.semantic_result_tensor_id, item.accumulator_tensor_id, item.placement_id)):
            _bad_type("partial-SUM definition has invalid identity")
    for item in kernel.objects:
        if type(item) is not BufferObject or not all(_u32(value) for value in (item.object_id, item.storage_tensor_id)) or type(item.owner_core) is not int or not 0 <= item.owner_core <= INVALID_CORE_ID or type(item.memory_space) is not MemorySpace:
            _bad_type("buffer object has invalid identity or enum")
        if not _u64_tuple(item.shape) or not _u64_tuple(item.strides) or len(item.shape) != len(item.strides) or any(not _u64(value) for value in (item.footprint_bytes, item.alignment_bytes, item.buffer_index)) or type(item.persistent) is not bool:
            _bad_type("buffer object has invalid extent fields", object_id=item.object_id)
    for item in kernel.views:
        if type(item) is not BufferView or not _u32(item.view_id) or not _u32(item.object_id) or not _u32(item.shard_id) or not _u64_tuple(item.shard_origin) or not _u64_tuple(item.padded_shape) or not _u64_tuple(item.valid_shape) or not _u64(item.object_offset_elements) or not _u64_tuple(item.object_strides) or not _u64(item.generation) or not len(item.shard_origin) == len(item.padded_shape) == len(item.valid_shape) == len(item.object_strides):
            _bad_type("buffer view has invalid types")
        if item.layout is not None and type(item.layout) is not Layout or item.blocked_layout is not None and type(item.blocked_layout) is not BlockedMnkLayout:
            _bad_type("buffer view layout has invalid type", view_id=item.view_id)
        if item.blocked_layout is not None and (any(not _u64(getattr(item.blocked_layout, field)) for field in ("block_m", "block_n", "block_k")) or type(item.blocked_layout.minor_to_major) is not tuple or any(type(axis) is not int for axis in item.blocked_layout.minor_to_major)):
            _bad_type("blocked layout has invalid field types", view_id=item.view_id)
    for item in kernel.states:
        if type(item) is not TensorState or not _u32(item.state_id) or not _u32(item.object_id) or not _u64(item.version) or type(item.origin) is not StateOrigin or not _u32(item.partial_sum_id, True):
            _bad_type("tensor state has invalid types")
    for item in kernel.tokens:
        if type(item) is not ControlToken or not _u32(item.token_id) or type(item.initial) is not bool:
            _bad_type("control token has invalid types")
    for item in kernel.ops:
        if type(item) is not KernelOp or not _u32(item.op_id) or not _u32(item.computation_id, True) or type(item.stable_key) is not str or not item.stable_key or type(item.opcode) is not KernelOpcode or type(item.owner_core) is not int or not 0 <= item.owner_core <= INVALID_CORE_ID or not _u32(item.result_shard_id, True):
            _bad_type("Kernel operation identity has invalid types")
        if type(item.reads) is not tuple or type(item.writes) is not tuple or type(item.after_tokens) is not tuple or any(not _u32(token_id) for token_id in item.after_tokens):
            _bad_type("Kernel operation collections have invalid types", op_id=item.op_id)
        if item.done_token is not None and not _u32(item.done_token):
            _bad_type("Kernel completion token has invalid type", op_id=item.op_id)
        for access in item.reads:
            if type(access) is not OperandAccess or not _u32(access.state_id) or not _u32(access.view_id) or type(access.mode) is not OperandAccessMode:
                _bad_type("operand access has invalid types", op_id=item.op_id)
            _verify_region_type(access.region)
        for transition in item.writes:
            if type(transition) is not StateTransition or not _u32(transition.old_state_id) or not _u32(transition.new_state_id) or not _u32(transition.view_id) or type(transition.mode) is not OperandAccessMode:
                _bad_type("state transition has invalid types", op_id=item.op_id)
            _verify_region_type(transition.region)
        _verify_attrs_type(item.opcode, item.attrs)


def _verify_tensor_and_shards(kernel, tensors, placements) -> None:
    from mesh_ir.ir.kernel_ir import DistributionKind, SynthesizedTensorPurpose

    for tensor in kernel.tensors:
        if tensor.producer_computation_id and tensor.producer_computation_id > len(kernel.computations):
            raise MeshIrError("E_ABI_BOUNDS", "tensor producer computation is invalid", tensor_id=tensor.tensor_id)
        if tensor.synthesized_purpose is not None and tensor.producer_computation_id < 1:
            raise MeshIrError("E_ABI_BOUNDS", "synthesized tensor requires a producer computation", tensor_id=tensor.tensor_id)
        if tensor.synthesized_purpose is None and tensor.producer_computation_id and kernel.computations[tensor.producer_computation_id - 1].result_tensor_id != tensor.tensor_id:
            raise MeshIrError("E_ABI_BOUNDS", "logical tensor producer computation differs", tensor_id=tensor.tensor_id)
        root = tensors.get(tensor.alias_root_tensor_id)
        if root is None or root.alias_root_tensor_id != root.tensor_id or root.dtype is not tensor.dtype:
            raise MeshIrError("E_EXPORT_LAYOUT", "Kernel tensor alias root is invalid", tensor_id=tensor.tensor_id)
        if tensor.synthesized_purpose is not None and tensor.alias_root_tensor_id != tensor.tensor_id:
            raise MeshIrError("E_EXPORT_LAYOUT", "synthesized tensor must own its storage root", tensor_id=tensor.tensor_id)
        logical = checked_mul_u64(_product(tensor.shape, "tensor elements"), tensor.dtype.byte_width, "tensor logical bytes")
        storage = checked_mul_u64(_storage_elements(tensor.shape, tensor.strides, tensor.storage_offset_elements), tensor.dtype.byte_width, "tensor storage bytes")
        if tensor.logical_extent_bytes != logical or tensor.storage_extent_bytes != storage:
            raise MeshIrError("E_EXPORT_LAYOUT", "Kernel tensor extent is inconsistent", tensor_id=tensor.tensor_id)
        if tensor.content_sha256 is not None and not _DIGEST.fullmatch(tensor.content_sha256):
            raise MeshIrError("E_ABI_CHECKSUM", "tensor content digest is invalid", tensor_id=tensor.tensor_id)
    for placement in kernel.placements:
        if not placement.core_ids or len(set(placement.core_ids)) != len(placement.core_ids):
            raise MeshIrError("E_PLACEMENT_INFEASIBLE", "placement participants are empty or repeated", placement_id=placement.placement_id)
    groups: dict[tuple[int, int], list] = {}
    sharded_tensor_ids: set[int] = set()
    for item in kernel.shards:
        if item.tensor_id not in tensors or item.placement_id not in placements or item.owner_core not in placements[item.placement_id].core_ids:
            raise MeshIrError("E_ABI_BOUNDS", "shard references an invalid tensor, placement, or owner", shard_id=item.shard_id)
        tensor = tensors[item.tensor_id]
        if not len(item.global_origin) == len(item.padded_local_shape) == len(item.valid_shape) == len(tensor.shape):
            raise MeshIrError("E_EXPORT_LAYOUT", "shard rank differs from tensor rank", shard_id=item.shard_id)
        for origin, padded, valid, extent in zip(item.global_origin, item.padded_local_shape, item.valid_shape, tensor.shape):
            if valid > padded or checked_add_u64(origin, valid, "shard valid end") > extent:
                raise MeshIrError("E_EXPORT_LAYOUT", "shard valid extent is out of bounds", shard_id=item.shard_id)
        if (item.distribution is DistributionKind.PARTIAL_SUM) != (item.partial_sum_id > 0):
            raise MeshIrError("E_EXPORT_LAYOUT", "partial-SUM identity does not match distribution", shard_id=item.shard_id)
        groups.setdefault((item.tensor_id, item.placement_id), []).append(item)
        sharded_tensor_ids.add(item.tensor_id)
    for tensor_id in tensors:
        if tensor_id not in sharded_tensor_ids:
            raise MeshIrError("E_ABI_BOUNDS", "tensor has no shard", tensor_id=tensor_id)
    for (tensor_id, placement_id), group in groups.items():
        owners = tuple(item.owner_core for item in group)
        if len(set(owners)) != len(owners):
            raise MeshIrError("E_PLACEMENT_INFEASIBLE", "tensor placement group has repeated shard owner", tensor_id=tensor_id, placement_id=placement_id)
        distributions = {item.distribution for item in group}
        if len(distributions) != 1:
            raise MeshIrError("E_PLACEMENT_INFEASIBLE", "tensor placement group mixes distributions", tensor_id=tensor_id, placement_id=placement_id)
        placement = placements[placement_id]
        if owners != placement.core_ids:
            raise MeshIrError("E_PLACEMENT_INFEASIBLE", "tensor shard owners do not match placement order", tensor_id=tensor_id, placement_id=placement_id)
        replicated = tuple(item for item in group if item.distribution is DistributionKind.REPLICATED)
        if replicated:
            zero_origin = (0,) * len(tensors[tensor_id].shape)
            if len(replicated) != len(group) or owners != placement.core_ids or any(item.global_origin != zero_origin or item.valid_shape != tensors[tensor_id].shape for item in group):
                raise MeshIrError("E_PLACEMENT_INFEASIBLE", "replicated shards do not cover every participant", tensor_id=tensor_id, placement_id=placement_id)
            continue
        tensor = tensors[tensor_id]
        if group[0].distribution is DistributionKind.PARTIAL_SUM:
            partial_ids = {item.partial_sum_id for item in group}
            regions = {(item.global_origin, item.valid_shape) for item in group}
            if len(partial_ids) != 1 or len(regions) != 1:
                raise MeshIrError("E_PLACEMENT_INFEASIBLE", "partial-SUM contributors must name one identical region and identity", tensor_id=tensor_id, placement_id=placement_id)
            coverage = (group[0],)
        else:
            coverage = tuple(group)
        volume = sum(_product(item.valid_shape, "shard coverage") for item in coverage)
        tensor_volume = _product(tensor.shape, "tensor coverage")
        overlap = any(
            all(left_origin < checked_add_u64(right_origin, right_extent, "shard end") and right_origin < checked_add_u64(left_origin, left_extent, "shard end") for left_origin, left_extent, right_origin, right_extent in zip(first.global_origin, first.valid_shape, second.global_origin, second.valid_shape))
            for index, first in enumerate(coverage)
            for second in coverage[index + 1 :]
        )
        if volume != tensor_volume or overlap:
            raise MeshIrError("E_EXPORT_LAYOUT", "tensor placement group does not exactly cover the logical tensor", tensor_id=tensor_id, placement_id=placement_id, covered_elements=volume, tensor_elements=tensor_volume)

    partials = {item.partial_sum_id: item for item in kernel.partial_sums}
    if tuple(partials) != tuple(range(1, len(partials) + 1)):
        raise MeshIrError("E_ABI_ORDER", "partial-SUM identifiers must be dense and ordered")
    for item in kernel.partial_sums:
        result = tensors.get(item.semantic_result_tensor_id)
        accumulator = tensors.get(item.accumulator_tensor_id)
        if result is None or accumulator is None or item.placement_id not in placements or item.computation_id > len(kernel.computations):
            raise MeshIrError("E_ABI_BOUNDS", "partial-SUM definition references an invalid record", partial_sum_id=item.partial_sum_id)
        if accumulator.synthesized_purpose is not SynthesizedTensorPurpose.PARTIAL_SUM or accumulator.producer_computation_id != item.computation_id or accumulator.shape != result.shape or accumulator.dtype is not result.dtype.accumulation:
            raise MeshIrError("E_EXPORT_DTYPE", "partial-SUM accumulator contract is inconsistent", partial_sum_id=item.partial_sum_id)
        partial_shards = tuple(shard for shard in kernel.shards if shard.partial_sum_id == item.partial_sum_id)
        if not partial_shards or any(shard.tensor_id != accumulator.tensor_id or shard.placement_id != item.placement_id for shard in partial_shards):
            raise MeshIrError("E_PLACEMENT_INFEASIBLE", "partial-SUM shards differ from their definition", partial_sum_id=item.partial_sum_id)
    unknown_partial_ids = {shard.partial_sum_id for shard in kernel.shards if shard.partial_sum_id} - set(partials)
    if unknown_partial_ids:
        raise MeshIrError("E_ABI_BOUNDS", "shard references an unknown partial-SUM definition", partial_sum_ids=tuple(sorted(unknown_partial_ids)))


def _verify_objects_and_views(kernel, tensors, shards, objects) -> None:
    for obj in kernel.objects:
        if obj.storage_tensor_id not in tensors:
            raise MeshIrError("E_ABI_BOUNDS", "buffer object references an invalid storage tensor", object_id=obj.object_id)
        tensor = tensors[obj.storage_tensor_id]
        if tensor.alias_root_tensor_id != tensor.tensor_id:
            raise MeshIrError("E_EXPORT_LAYOUT", "buffer object storage tensor is not a canonical root", object_id=obj.object_id, tensor_id=tensor.tensor_id)
        footprint = checked_mul_u64(_storage_elements(obj.shape, obj.strides), tensor.dtype.byte_width, "object footprint")
        if obj.footprint_bytes != footprint or not _power_of_two(obj.alignment_bytes):
            raise MeshIrError("E_EXPORT_LAYOUT", "buffer object extent or alignment is inconsistent", object_id=obj.object_id)
        if obj.memory_space is MemorySpace.PEER_SRAM:
            raise MeshIrError("E_ABI_ENUM", "peer SRAM is an address view, not an owning memory space", object_id=obj.object_id)
        if obj.memory_space is MemorySpace.CORE_SRAM:
            if obj.owner_core == INVALID_CORE_ID:
                raise MeshIrError("E_PLACEMENT_INFEASIBLE", "core SRAM object owner is inconsistent", object_id=obj.object_id)
        elif obj.owner_core != INVALID_CORE_ID:
            raise MeshIrError("E_PLACEMENT_INFEASIBLE", "external object must use the invalid-core sentinel", object_id=obj.object_id)
        if tensor.storage_class is StorageClass.PRE_RESIDENT and (obj.memory_space is not MemorySpace.CORE_SRAM or tensor.role is not TensorRole.WEIGHT or tensor.content_sha256 is None):
            raise MeshIrError("E_TENSOR_NOT_RESIDENT", "PRE_RESIDENT requires a content-addressed local weight", object_id=obj.object_id)
    for view in kernel.views:
        if view.object_id not in objects or view.shard_id not in shards:
            raise MeshIrError("E_ABI_BOUNDS", "view references an invalid object or shard", view_id=view.view_id)
        obj = objects[view.object_id]
        shard = shards[view.shard_id]
        tensor = tensors[shard.tensor_id]
        storage_tensor = tensors[obj.storage_tensor_id]
        if tensor.dtype is not storage_tensor.dtype or len(view.padded_shape) != len(shard.padded_local_shape):
            raise MeshIrError("E_EXPORT_DTYPE", "view and object storage contract differ", view_id=view.view_id)
        for origin, padded, valid, shard_padded, shard_valid in zip(view.shard_origin, view.padded_shape, view.valid_shape, shard.padded_local_shape, shard.valid_shape):
            if valid > padded or checked_add_u64(origin, padded, "view padded end") > shard_padded or checked_add_u64(origin, valid, "view valid end") > shard_valid:
                raise MeshIrError("E_EXPORT_LAYOUT", "view window exceeds its logical shard", view_id=view.view_id)
        spans = region_byte_spans(view.object_offset_elements, view.padded_shape, view.object_strides, tensor.dtype.byte_width)
        if any(span.end > obj.footprint_bytes for span in spans):
            raise MeshIrError("E_EXPORT_LAYOUT", "view exceeds its root object", view_id=view.view_id)
        if view.layout is Layout.CONTIGUOUS_ROW_MAJOR:
            expected = tuple(_product(view.padded_shape[index + 1 :], "row-major view stride") for index in range(len(view.padded_shape)))
            if view.object_strides != expected or view.blocked_layout is not None:
                raise MeshIrError("E_EXPORT_LAYOUT", "row-major view layout is inconsistent", view_id=view.view_id)
        elif view.layout is Layout.TRANSPOSED_2D_VIEW:
            if len(view.padded_shape) < 2:
                raise MeshIrError("E_EXPORT_LAYOUT", "transposed matrix view has insufficient rank", view_id=view.view_id)
            matrix_elements = checked_mul_u64(view.padded_shape[-2], view.padded_shape[-1], "transposed matrix footprint")
            leading = tuple(checked_mul_u64(_product(view.padded_shape[index + 1 : -2], "transposed batch stride"), matrix_elements, "transposed batch stride") for index in range(len(view.padded_shape) - 2))
            expected = leading + (1, view.padded_shape[-2])
            if view.object_strides != expected or view.blocked_layout is not None:
                raise MeshIrError("E_EXPORT_LAYOUT", "transposed matrix view layout is inconsistent", view_id=view.view_id)
        elif view.layout is Layout.BLOCKED_MNK:
            blocked = view.blocked_layout
            if blocked is None or any(value < 1 for value in (blocked.block_m, blocked.block_n, blocked.block_k)) or sorted(blocked.minor_to_major) != [0, 1, 2]:
                raise MeshIrError("E_EXPORT_LAYOUT", "blocked view layout is inconsistent", view_id=view.view_id)
        elif view.blocked_layout is not None:
            raise MeshIrError("E_EXPORT_LAYOUT", "view without blocked layout carries blocked parameters", view_id=view.view_id)
        if obj.memory_space is MemorySpace.CORE_SRAM and obj.owner_core != shard.owner_core:
            raise MeshIrError("E_PLACEMENT_INFEASIBLE", "local view owner differs from its shard", view_id=view.view_id)
    viewed = {view.object_id for view in kernel.views}
    if viewed != set(objects):
        raise MeshIrError("E_ABI_BOUNDS", "every buffer object requires at least one mapped view", object_ids=tuple(sorted(set(objects) - viewed)))


def _verify_states_and_ops(kernel, tensors, shards, objects, views, states, tokens):
    from mesh_ir.analysis.dependency import build_kernel_dependency_graph
    from mesh_ir.ir.kernel_ir import KernelOpcode, OperandAccessMode, StateOrigin

    versions: dict[int, list[int]] = {object_id: [] for object_id in objects}
    for state in kernel.states:
        if state.object_id not in objects:
            raise MeshIrError("E_ABI_BOUNDS", "state references an invalid object", state_id=state.state_id)
        versions[state.object_id].append(state.version)
        if state.version == 0 and state.origin is StateOrigin.PRODUCED or state.version > 0 and state.origin is not StateOrigin.PRODUCED:
            raise MeshIrError("E_ABI_ORDER", "state origin does not match its version", state_id=state.state_id)
    for object_id, object_versions in versions.items():
        if sorted(object_versions) != list(range(len(object_versions))):
            raise MeshIrError("E_ABI_ORDER", "object state versions must be unique and consecutive", object_id=object_id, versions=object_versions)
    boundary = len(kernel.objects) + len(kernel.views)
    if tuple(op.opcode for op in kernel.ops[: len(kernel.objects)]) != (KernelOpcode.ALLOC,) * len(kernel.objects) or tuple(op.opcode for op in kernel.ops[len(kernel.objects) : boundary]) != (KernelOpcode.VIEW,) * len(kernel.views) or any(op.opcode in (KernelOpcode.ALLOC, KernelOpcode.VIEW) for op in kernel.ops[boundary:]):
        raise MeshIrError("E_ABI_ORDER", "Kernel declarations must form ALLOC then VIEW prefixes")
    if tuple(op.attrs.object_id for op in kernel.ops[: len(kernel.objects)]) != tuple(objects) or tuple(op.attrs.view_id for op in kernel.ops[len(kernel.objects) : boundary]) != tuple(views):
        raise MeshIrError("E_ABI_ORDER", "Kernel declarations do not match record order")
    if any(op.owner_core != obj.owner_core for op, obj in zip(kernel.ops, kernel.objects)) or any(op.owner_core != objects[view.object_id].owner_core for op, view in zip(kernel.ops[len(kernel.objects) : boundary], kernel.views)):
        raise MeshIrError("E_PLACEMENT_INFEASIBLE", "Kernel declaration owner differs from its object")
    produced: dict[int, int] = {}
    definitions = {}
    accesses = []
    for op in kernel.ops:
        pure = op.opcode in (KernelOpcode.ALLOC, KernelOpcode.VIEW)
        if pure:
            if op.done_token is not None or op.after_tokens or op.reads or op.writes:
                raise MeshIrError("E_EVENT_MULTIPLE_PRODUCERS", "pure declaration has effects", op_id=op.op_id)
            continue
        if op.done_token not in tokens:
            raise MeshIrError("E_EVENT_NO_PRODUCER", "effectful operation lacks a completion token", op_id=op.op_id)
        if len(set(op.after_tokens)) != len(op.after_tokens) or any(token_id not in tokens for token_id in op.after_tokens):
            raise MeshIrError("E_ABI_BOUNDS", "operation control dependency is invalid", op_id=op.op_id)
        for access in op.reads:
            if access.mode is not OperandAccessMode.READ or access.state_id not in states or access.view_id not in views or states[access.state_id].object_id != views[access.view_id].object_id:
                raise MeshIrError("E_ABI_BOUNDS", "operand access state/view identity is inconsistent", op_id=op.op_id)
            view = views[access.view_id]
            for origin, shape, step, limit in zip(access.region.origin, access.region.shape, access.region.steps, view.padded_shape):
                if shape and checked_add_u64(origin, checked_mul_u64(shape - 1, step, "access coordinate"), "access coordinate") >= limit:
                    raise MeshIrError("E_EXPORT_LAYOUT", "operand region exceeds its view domain", op_id=op.op_id, view_id=view.view_id)
            accesses.append((op, access.state_id, view.object_id, False, view_access_byte_spans(access, views, objects, tensors, shards)))
        write_groups = {}
        for transition in op.writes:
            if transition.mode is not OperandAccessMode.WRITE or transition.old_state_id not in states or transition.new_state_id not in states or transition.view_id not in views:
                raise MeshIrError("E_ABI_BOUNDS", "state transition reference is invalid", op_id=op.op_id)
            old, new, view = states[transition.old_state_id], states[transition.new_state_id], views[transition.view_id]
            if old.object_id != new.object_id or new.object_id != view.object_id or new.version != old.version + 1:
                raise MeshIrError("E_ABI_BOUNDS", "state transition changes object or skips version", op_id=op.op_id)
            for origin, shape, step, limit in zip(transition.region.origin, transition.region.shape, transition.region.steps, view.padded_shape):
                if shape and checked_add_u64(origin, checked_mul_u64(shape - 1, step, "access coordinate"), "access coordinate") >= limit:
                    raise MeshIrError("E_EXPORT_LAYOUT", "state transition exceeds its view domain", op_id=op.op_id, view_id=view.view_id)
            spans = view_access_byte_spans(transition, views, objects, tensors, shards)
            tensor = tensors[shards[view.shard_id].tensor_id]
            logical_bytes = checked_mul_u64(_product(transition.region.shape, "write region elements"), tensor.dtype.byte_width, "write region bytes")
            if sum(span.end - span.begin for span in spans) != logical_bytes:
                raise MeshIrError("E_EXPORT_LAYOUT", "write region maps multiple logical elements to the same storage", op_id=op.op_id, view_id=view.view_id)
            write_groups.setdefault(transition.new_state_id, []).append((transition, spans))
            accesses.append((op, transition.old_state_id, view.object_id, True, spans))
        for new_state_id, group in write_groups.items():
            if len({item.old_state_id for item, _ in group}) != 1:
                raise MeshIrError("E_ABI_BOUNDS", "grouped state definition uses multiple old states", op_id=op.op_id, state_id=new_state_id)
            if any(spans_overlap(left, right) for index, (_, left) in enumerate(group) for _, right in group[index + 1 :]):
                raise MeshIrError("E_EXPORT_LAYOUT", "grouped state definition has overlapping writes", op_id=op.op_id, state_id=new_state_id)
            if new_state_id in produced:
                raise MeshIrError("E_ABI_DUPLICATE", "state has multiple definitions", state_id=new_state_id)
            produced[new_state_id] = op.op_id
            definitions[new_state_id] = (op, merge_spans(tuple(span for _, spans in group for span in spans)))
    for state in kernel.states:
        if state.origin is StateOrigin.PRODUCED and state.state_id not in produced:
            raise MeshIrError("E_ABI_BOUNDS", "produced state has no definition", state_id=state.state_id)
        if state.origin is not StateOrigin.PRODUCED and state.state_id in produced:
            raise MeshIrError("E_ABI_DUPLICATE", "initial state is redefined", state_id=state.state_id)
    dependencies = build_kernel_dependency_graph(kernel)
    op_by_node = {node: op_id for op_id, node in enumerate(dependencies.op_node_by_id, 1) if node}
    canonical_ops = tuple(op_by_node[node] for node in dependencies.graph.canonical_topological_order() if node in op_by_node)
    declared_ops = tuple(op.op_id for op in kernel.ops if op.opcode not in (KernelOpcode.ALLOC, KernelOpcode.VIEW))
    if canonical_ops != declared_ops:
        raise MeshIrError("E_ABI_ORDER", "effectful Kernel operations are not in canonical topological order", actual=declared_ops, expected=canonical_ops)
    states_by_object: dict[int, list] = {object_id: [] for object_id in objects}
    for state in kernel.states:
        states_by_object[state.object_id].append(state)
    for op, state_id, object_id, _, spans in accesses:
        start = dependencies.op_node_by_id[op.op_id - 1]
        current = states[state_id]
        for newer in states_by_object[object_id]:
            if newer.version <= current.version or newer.state_id not in definitions:
                continue
            producer, written = definitions[newer.state_id]
            completion = dependencies.token_node_by_id[producer.done_token - 1]
            if spans_overlap(spans, written) and dependencies.graph.happens_before(completion, start):
                raise MeshIrError("E_TENSOR_NOT_RESIDENT", "operation accesses a stale physical tensor state", op_id=op.op_id, state_id=state_id, newer_state_id=newer.state_id)
    return dependencies, accesses


def _verify_initialization(kernel, dependencies, tensors, shards, objects, views, states):
    from mesh_ir.ir.kernel_ir import KernelOpcode, StateOrigin

    initialized: dict[int, tuple[ByteSpan, ...]] = {}
    initial = {state.object_id: state for state in kernel.states if state.version == 0}
    if len(initial) != len(objects):
        raise MeshIrError("E_ABI_BOUNDS", "every object requires one version-zero state")
    for object_id, state in initial.items():
        obj = objects[object_id]
        tensor = tensors[obj.storage_tensor_id]
        if state.partial_sum_id:
            raise MeshIrError("E_TENSOR_NOT_RESIDENT", "initial state cannot claim partial-SUM provenance", state_id=state.state_id)
        if state.origin in (StateOrigin.EXTERNAL, StateOrigin.PRE_RESIDENT) and any(tensors[shards[view.shard_id].tensor_id].alias_root_tensor_id != obj.storage_tensor_id for view in kernel.views if view.object_id == object_id):
            raise MeshIrError("E_TENSOR_NOT_RESIDENT", "initial backing content differs from its logical view", state_id=state.state_id)
        if state.origin is StateOrigin.EXTERNAL:
            if obj.memory_space not in (MemorySpace.HBM, MemorySpace.HOST_SHARED) or tensor.storage_class is not StorageClass.EXTERNAL:
                raise MeshIrError("E_TENSOR_NOT_RESIDENT", "EXTERNAL state does not name external storage", state_id=state.state_id)
            initialized[state.state_id] = (ByteSpan(0, obj.footprint_bytes),) if obj.footprint_bytes else ()
        elif state.origin is StateOrigin.PRE_RESIDENT:
            if tensor.storage_class is not StorageClass.PRE_RESIDENT or obj.memory_space is not MemorySpace.CORE_SRAM or tensor.role is not TensorRole.WEIGHT or tensor.content_sha256 is None:
                raise MeshIrError("E_TENSOR_NOT_RESIDENT", "PRE_RESIDENT state is invalid", state_id=state.state_id)
            initialized[state.state_id] = (ByteSpan(0, obj.footprint_bytes),) if obj.footprint_bytes else ()
        elif state.origin is StateOrigin.EMPTY:
            initialized[state.state_id] = ()
        else:
            raise MeshIrError("E_ABI_ORDER", "version-zero state has invalid origin", state_id=state.state_id)
    topo_rank = {node: index for index, node in enumerate(dependencies.graph.canonical_topological_order())}
    effects = [op for op in kernel.ops if dependencies.op_node_by_id[op.op_id - 1]]
    effects.sort(key=lambda op: topo_rank[dependencies.op_node_by_id[op.op_id - 1]])
    for op in effects:
        for access in op.reads:
            spans = view_access_byte_spans(access, views, objects, tensors, shards)
            if not spans_contain(initialized.get(access.state_id, ()), spans):
                raise MeshIrError("E_TENSOR_NOT_RESIDENT", "read is not dominated by initialization", op_id=op.op_id, state_id=access.state_id)
        if op.opcode in (KernelOpcode.COLLECTIVE, KernelOpcode.LOCAL_REDUCE) and {states[access.state_id].partial_sum_id for access in op.reads} != {op.attrs.partial_sum_id}:
            raise MeshIrError("E_TENSOR_NOT_RESIDENT", "reduction operands do not share the declared partial-SUM identity", op_id=op.op_id)
        write_groups = {}
        for transition in op.writes:
            write_groups.setdefault(transition.new_state_id, []).append(transition)
        for group in write_groups.values():
            old, new = states[group[0].old_state_id], states[group[0].new_state_id]
            previous = initialized.get(old.state_id)
            if previous is None:
                raise MeshIrError("E_TENSOR_NOT_RESIDENT", "write old state is not defined", op_id=op.op_id, state_id=old.state_id)
            written = merge_spans(tuple(span for transition in group for span in view_access_byte_spans(transition, views, objects, tensors, shards)))
            expected_partial = old.partial_sum_id
            if op.opcode in (KernelOpcode.GEMM, KernelOpcode.BMM):
                partial_ids = {shards[views[transition.view_id].shard_id].partial_sum_id for transition in group}
                if len(partial_ids) != 1:
                    raise MeshIrError("E_TENSOR_NOT_RESIDENT", "grouped matrix writes disagree on partial-SUM identity", op_id=op.op_id)
                expected_partial = partial_ids.pop()
            elif op.opcode is KernelOpcode.LOCAL_COPY or op.opcode is KernelOpcode.DMA and op.attrs.kind is not DmaKind.LOCAL_FILL:
                partial_ids = {states[access.state_id].partial_sum_id for access in op.reads}
                if len(partial_ids) != 1:
                    raise MeshIrError("E_TENSOR_NOT_RESIDENT", "DMA sources disagree on partial-SUM identity", op_id=op.op_id)
                expected_partial = partial_ids.pop()
            allowed_partial_ids = {expected_partial}
            if expected_partial and op.opcode is KernelOpcode.LOCAL_REDUCE:
                allowed_partial_ids.add(0)
            if new.partial_sum_id not in allowed_partial_ids:
                raise MeshIrError("E_TENSOR_NOT_RESIDENT", "operation does not preserve partial-SUM dataflow", op_id=op.op_id, expected_partial_sum_id=expected_partial, actual_partial_sum_id=new.partial_sum_id)
            if old.partial_sum_id != new.partial_sum_id and not spans_contain(written, previous):
                raise MeshIrError("E_TENSOR_NOT_RESIDENT", "partial-SUM identity change leaves mixed object provenance", op_id=op.op_id)
            initialized[new.state_id] = merge_spans(previous + written)
    return initialized


def _subtract_lineage_span(span, cuts):
    fragments = [span]
    for cut in cuts:
        updated = []
        for fragment in fragments:
            if cut.end <= fragment.begin or cut.begin >= fragment.end:
                updated.append(fragment)
                continue
            if fragment.begin < cut.begin:
                updated.append(ByteSpan(fragment.begin, cut.begin))
            if cut.end < fragment.end:
                updated.append(ByteSpan(cut.end, fragment.end))
        fragments = updated
    return tuple(fragments)


def _updated_lineage(previous, writes):
    cuts = tuple(span for spans, _ in writes for span in spans)
    entries = tuple((fragment, contributors) for span, contributors in previous for fragment in _subtract_lineage_span(span, cuts))
    entries += tuple((span, contributors) for spans, contributors in writes if contributors is not None for span in spans)
    ordered = sorted(entries)
    merged = []
    for span, contributors in ordered:
        if merged and merged[-1][1] == contributors and merged[-1][0].end == span.begin:
            merged[-1] = (ByteSpan(merged[-1][0].begin, span.end), contributors)
        else:
            merged.append((span, contributors))
    return tuple(merged)


def _lineage_contributors(entries, spans, op_id):
    available = tuple(span for span, _ in entries)
    if not spans_contain(available, spans):
        raise MeshIrError("E_TENSOR_NOT_RESIDENT", "partial-SUM access has no complete contributor lineage", op_id=op_id)
    values = {
        contributors
        for required in spans
        for span, contributors in entries
        if required.begin < span.end and span.begin < required.end
    }
    if len(values) != 1:
        raise MeshIrError("E_TENSOR_NOT_RESIDENT", "partial-SUM access mixes contributor lineages", op_id=op_id)
    return values.pop()


def _verify_partial_contributors(kernel, dependencies, initialized, tensors, shards, objects, views, states):
    from mesh_ir.ir.kernel_ir import KernelOpcode, MatrixPhase

    partials = {item.partial_sum_id: item for item in kernel.partial_sums}
    placements = {item.placement_id: item for item in kernel.placements}
    producers = {transition.new_state_id: op for op in kernel.ops for transition in op.writes}
    topo_rank = {node: index for index, node in enumerate(dependencies.graph.canonical_topological_order())}
    effects = [op for op in kernel.ops if dependencies.op_node_by_id[op.op_id - 1]]
    effects.sort(key=lambda op: topo_rank[dependencies.op_node_by_id[op.op_id - 1]])
    lineage = {}
    completed = {}
    for op in effects:
        ordinary_reads = op.opcode not in (KernelOpcode.COLLECTIVE, KernelOpcode.LOCAL_REDUCE, KernelOpcode.LOCAL_COPY) and not (op.opcode is KernelOpcode.DMA and op.attrs.kind is DmaKind.P2P_PUSH)
        if ordinary_reads:
            for access_index, access in enumerate(op.reads):
                partial_id = states[access.state_id].partial_sum_id
                if not partial_id:
                    continue
                if op.opcode in (KernelOpcode.GEMM, KernelOpcode.BMM) and op.attrs.phase in (MatrixPhase.ACCUMULATE_CONTINUE, MatrixPhase.ACCUMULATE_FINAL) and access_index == 2:
                    continue
                definition = partials.get(partial_id)
                if definition is None:
                    raise MeshIrError("E_ABI_BOUNDS", "partial-SUM consumer references an absent definition", op_id=op.op_id, partial_sum_id=partial_id)
                expected = tuple(sorted(placements[definition.placement_id].core_ids))
                spans = view_access_byte_spans(access, views, objects, tensors, shards)
                actual = _lineage_contributors(lineage.get(access.state_id, ()), spans, op.op_id)
                if actual != expected:
                    raise MeshIrError("E_TENSOR_NOT_RESIDENT", "ordinary consumer reads an incomplete partial-SUM region", op_id=op.op_id, expected=expected, actual=actual)
                if op.opcode is KernelOpcode.MATRIX_EPILOGUE and definition.computation_id != op.computation_id:
                    raise MeshIrError("E_TENSOR_NOT_RESIDENT", "matrix epilogue consumes another computation's partial-SUM", op_id=op.op_id)
                completed[access.state_id] = partial_id
        if not op.writes:
            continue
        write_payloads = [None] * len(op.writes)
        if op.opcode in (KernelOpcode.GEMM, KernelOpcode.BMM):
            partial_id = states[op.writes[0].new_state_id].partial_sum_id
            if partial_id and op.attrs.phase in (MatrixPhase.ACCUMULATE_ONLY, MatrixPhase.ACCUMULATE_FINAL):
                region = _logical_access_region(op.writes[0], views, shards)
                _matrix_accumulation_chain(op, op.computation_id, op.owner_core, partial_id, region, producers, views, shards)
                write_payloads[0] = (op.owner_core,)
        elif op.opcode is KernelOpcode.LOCAL_COPY or op.opcode is KernelOpcode.DMA and op.attrs.kind is not DmaKind.LOCAL_FILL:
            for index, access in enumerate(op.reads):
                source_entries = lineage.get(access.state_id, ())
                if states[access.state_id].partial_sum_id or source_entries:
                    spans = view_access_byte_spans(access, views, objects, tensors, shards)
                    write_payloads[index] = _lineage_contributors(source_entries, spans, op.op_id)
        elif op.opcode is KernelOpcode.LOCAL_REDUCE:
            fan_in = len(op.reads) // len(op.writes)
            for index in range(len(op.writes)):
                contributors = []
                for access in op.reads[index * fan_in : (index + 1) * fan_in]:
                    spans = view_access_byte_spans(access, views, objects, tensors, shards)
                    contributors.extend(_lineage_contributors(lineage.get(access.state_id, ()), spans, op.op_id))
                write_payloads[index] = tuple(sorted(contributors))
        grouped = {}
        for transition, contributors in zip(op.writes, write_payloads):
            spans = view_access_byte_spans(transition, views, objects, tensors, shards)
            grouped.setdefault(transition.new_state_id, []).append((transition, spans, contributors))
        for new_state_id, group in grouped.items():
            old_state_id = group[0][0].old_state_id
            writes = tuple((spans, contributors) for _, spans, contributors in group)
            lineage[new_state_id] = _updated_lineage(lineage.get(old_state_id, ()), writes)
            old, new = states[old_state_id], states[new_state_id]
            source_partial_ids = {states[access.state_id].partial_sum_id for access in op.reads if states[access.state_id].partial_sum_id} if op.opcode in (KernelOpcode.DMA, KernelOpcode.LOCAL_REDUCE, KernelOpcode.LOCAL_COPY) else set()
            partial_ids = ({old.partial_sum_id} if old.partial_sum_id else set()) | source_partial_ids
            if op.opcode is KernelOpcode.LOCAL_REDUCE:
                partial_ids.add(op.attrs.partial_sum_id)
            if new.partial_sum_id == 0 and partial_ids:
                if len(partial_ids) != 1:
                    raise MeshIrError("E_TENSOR_NOT_RESIDENT", "partial-SUM completion mixes definitions", op_id=op.op_id)
                partial_id = partial_ids.pop()
                definition = partials.get(partial_id)
                if definition is None:
                    raise MeshIrError("E_ABI_BOUNDS", "partial-SUM completion references an absent definition", op_id=op.op_id, partial_sum_id=partial_id)
                expected = tuple(sorted(placements[definition.placement_id].core_ids))
                actual = _lineage_contributors(lineage[new_state_id], initialized[new_state_id], op.op_id)
                if actual != expected:
                    raise MeshIrError("E_TENSOR_NOT_RESIDENT", "partial-SUM completion has missing or repeated contributors", op_id=op.op_id, expected=expected, actual=actual)
                completed[new_state_id] = partial_id
    return completed


def _region_bytes(access, views, objects, tensors, shards) -> int:
    return sum(span.end - span.begin for span in view_access_byte_spans(access, views, objects, tensors, shards))


def _paired_movement_objects(op, tensors, shards, objects, views):
    if not op.reads or len(op.reads) != len(op.writes):
        raise MeshIrError("E_DMA_RANGE", "movement requires paired source and destination regions", op_id=op.op_id)
    source_objects = tuple(objects[views[access.view_id].object_id] for access in op.reads)
    destination_objects = tuple(objects[views[transition.view_id].object_id] for transition in op.writes)
    if len({item.object_id for item in source_objects}) != 1 or len({item.object_id for item in destination_objects}) != 1:
        raise MeshIrError("E_DMA_RANGE", "movement pieces must share one source and destination object", op_id=op.op_id)
    for access, transition in zip(op.reads, op.writes):
        source_view, destination_view = views[access.view_id], views[transition.view_id]
        source_tensor = tensors[shards[source_view.shard_id].tensor_id]
        destination_tensor = tensors[shards[destination_view.shard_id].tensor_id]
        source_region = _logical_access_region(access, views, shards)
        destination_region = _logical_access_region(transition, views, shards)
        if source_tensor.alias_root_tensor_id != destination_tensor.alias_root_tensor_id or source_tensor.dtype is not destination_tensor.dtype or source_region != destination_region or access.region.steps != transition.region.steps or _region_bytes(access, views, objects, tensors, shards) != _region_bytes(transition, views, objects, tensors, shards):
            raise MeshIrError("E_DMA_RANGE", "movement source and destination pieces differ", op_id=op.op_id)
    return source_objects[0], destination_objects[0]


def _operation_data_contract(op, computations, tensors, shards, objects, views, empty_target_tensor_id: int = 0) -> None:
    from mesh_ir.ir.kernel_ir import KernelOpcode, MatrixPhase, SynthesizedTensorPurpose

    if op.opcode in (KernelOpcode.GEMM, KernelOpcode.BMM, KernelOpcode.MATRIX_EPILOGUE, KernelOpcode.VECTOR, KernelOpcode.DATA_MOVEMENT, KernelOpcode.REDUCE, KernelOpcode.SOFTMAX, KernelOpcode.NORM):
        computation = computations.get(op.computation_id)
        if computation is None:
            raise MeshIrError("E_ABI_BOUNDS", "compute operation has no logical computation", op_id=op.op_id)
        graph_opcode = OpCode.SOFTMAX if op.opcode is KernelOpcode.SOFTMAX else op.attrs.graph_opcode
        if graph_opcode is not computation.opcode or op.attrs.semantic_attrs != computation.attrs:
            raise MeshIrError("E_ABI_CHECKSUM", "physical operation differs from its logical computation", op_id=op.op_id)
        if empty_target_tensor_id:
            if op.opcode in (KernelOpcode.GEMM, KernelOpcode.BMM) and op.attrs.phase is not MatrixPhase.DIRECT:
                operands = tuple(tensors[tensor_id] for tensor_id in computation.operand_tensor_ids[:2])
                result = tensors[empty_target_tensor_id]
                verify_operation_data_contract(
                    op.op_id,
                    graph_opcode,
                    op.attrs.semantic_attrs,
                    tuple(TensorDataContract(tuple(Const(dimension) for dimension in item.shape), item.dtype) for item in operands),
                    TensorDataContract(tuple(Const(dimension) for dimension in result.shape), result.dtype),
                    matrix_result_form=MatrixResultForm.ACCUMULATION,
                )
            elif empty_target_tensor_id != computation.result_tensor_id:
                raise MeshIrError("E_ABI_CHECKSUM", "empty physical result differs from its logical computation", op_id=op.op_id)
            return
        operand_ids = tuple(shards[views[item.view_id].shard_id].tensor_id for item in op.reads)
        result_id = shards[views[op.writes[0].view_id].shard_id].tensor_id
        result = tensors[result_id]
        if op.opcode is KernelOpcode.MATRIX_EPILOGUE:
            expected_reads = 2 if computation.opcode is OpCode.LINEAR_BIAS else 1
            accumulator = tensors[operand_ids[0]] if operand_ids else None
            bias_matches = computation.opcode is not OpCode.LINEAR_BIAS or len(operand_ids) == 2 and operand_ids[1] == computation.operand_tensor_ids[2]
            if len(operand_ids) != expected_reads or result_id != computation.result_tensor_id or accumulator is None or accumulator.producer_computation_id != computation.computation_id or accumulator.synthesized_purpose not in (SynthesizedTensorPurpose.ACCUMULATION, SynthesizedTensorPurpose.PARTIAL_SUM) or not bias_matches:
                raise MeshIrError("E_ABI_CHECKSUM", "matrix epilogue accesses differ from its logical computation", op_id=op.op_id)
            return
        result_form = MatrixResultForm.SEMANTIC
        if op.opcode in (KernelOpcode.GEMM, KernelOpcode.BMM) and op.attrs.phase is not MatrixPhase.DIRECT:
            expected_operands = computation.operand_tensor_ids[:2]
            continuation = op.attrs.phase in (MatrixPhase.ACCUMULATE_CONTINUE, MatrixPhase.ACCUMULATE_FINAL)
            if operand_ids[:2] != expected_operands or len(operand_ids) != 2 + int(continuation) or continuation and operand_ids[2] != result_id or result.producer_computation_id != op.computation_id or result.synthesized_purpose not in (SynthesizedTensorPurpose.ACCUMULATION, SynthesizedTensorPurpose.PARTIAL_SUM):
                raise MeshIrError("E_ABI_BOUNDS", "matrix accumulator differs from its computation", op_id=op.op_id, tensor_id=result.tensor_id)
            result_form = MatrixResultForm.ACCUMULATION
            operand_ids = operand_ids[:2]
        elif operand_ids != computation.operand_tensor_ids or result_id != computation.result_tensor_id:
            raise MeshIrError("E_ABI_CHECKSUM", "physical tensor accesses differ from logical computation", op_id=op.op_id)
        operands = tuple(tensors[tensor_id] for tensor_id in operand_ids)
        verify_operation_data_contract(
            op.op_id,
            graph_opcode,
            op.attrs.semantic_attrs,
            tuple(TensorDataContract(tuple(Const(dimension) for dimension in item.shape), item.dtype) for item in operands),
            TensorDataContract(tuple(Const(dimension) for dimension in result.shape), result.dtype),
            matrix_result_form=result_form,
        )


def _logical_access_region(access, views, shards) -> tuple[tuple[int, ...], tuple[int, ...]]:
    view = views[access.view_id]
    shard = shards[view.shard_id]
    return tuple(base + local + region for base, local, region in zip(shard.global_origin, view.shard_origin, access.region.origin)), access.region.shape


def _matrix_contraction_bounds(op, views, shards) -> tuple[int, int]:
    attrs = op.attrs
    lhs_view = views[op.reads[0].view_id]
    rhs_view = views[op.reads[1].view_id]
    lhs_shard = shards[lhs_view.shard_id]
    rhs_shard = shards[rhs_view.shard_id]
    lhs_axis = len(op.reads[0].region.shape) - (2 if attrs.semantic_attrs.lhs_transpose else 1)
    rhs_axis = len(op.reads[1].region.shape) - (1 if attrs.semantic_attrs.rhs_transpose else 2)
    lhs_origin = lhs_shard.global_origin[lhs_axis] + lhs_view.shard_origin[lhs_axis] + op.reads[0].region.origin[lhs_axis]
    rhs_origin = rhs_shard.global_origin[rhs_axis] + rhs_view.shard_origin[rhs_axis] + op.reads[1].region.origin[rhs_axis]
    lhs_begin = lhs_shard.global_origin[lhs_axis]
    lhs_end = lhs_begin + lhs_shard.valid_shape[lhs_axis]
    rhs_begin = rhs_shard.global_origin[rhs_axis]
    rhs_end = rhs_begin + rhs_shard.valid_shape[rhs_axis]
    if lhs_origin != rhs_origin or attrs.tile.k_origin != lhs_origin or (lhs_begin, lhs_end) != (rhs_begin, rhs_end):
        raise MeshIrError("E_EXPORT_LAYOUT", "matrix contraction coordinates differ from physical operand regions", op_id=op.op_id)
    return lhs_begin, lhs_end


def _matrix_accumulation_chain(producer, computation_id, owner_core, partial_sum_id, region, producers, views, shards):
    from mesh_ir.ir.kernel_ir import KernelOpcode, MatrixPhase

    chain = []
    seen_states = set()
    while True:
        if producer.opcode not in (KernelOpcode.GEMM, KernelOpcode.BMM) or producer.computation_id != computation_id or producer.owner_core != owner_core or producer.attrs.partial_sum_id != partial_sum_id or len(producer.writes) != 1:
            raise MeshIrError("E_TENSOR_NOT_RESIDENT", "matrix accumulator has an invalid producer", op_id=producer.op_id)
        if _logical_access_region(producer.writes[0], views, shards) != region:
            raise MeshIrError("E_EXPORT_LAYOUT", "matrix accumulator region differs from its producer", op_id=producer.op_id)
        chain.append(producer)
        if producer.attrs.phase in (MatrixPhase.ACCUMULATE_FIRST, MatrixPhase.ACCUMULATE_ONLY):
            break
        if producer.attrs.phase not in (MatrixPhase.ACCUMULATE_CONTINUE, MatrixPhase.ACCUMULATE_FINAL) or len(producer.reads) != 3:
            raise MeshIrError("E_EXPORT_LAYOUT", "matrix accumulator phase chain is invalid", op_id=producer.op_id)
        state_id = producer.reads[2].state_id
        if state_id in seen_states or state_id not in producers:
            raise MeshIrError("E_TENSOR_NOT_RESIDENT", "matrix accumulator chain has no producer", op_id=producer.op_id)
        seen_states.add(state_id)
        producer = producers[state_id]
    chain.reverse()
    phases = tuple(item.attrs.phase for item in chain)
    expected_phases = (MatrixPhase.ACCUMULATE_ONLY,) if len(chain) == 1 else (MatrixPhase.ACCUMULATE_FIRST, *((MatrixPhase.ACCUMULATE_CONTINUE,) * (len(chain) - 2)), MatrixPhase.ACCUMULATE_FINAL)
    if phases != expected_phases:
        raise MeshIrError("E_EXPORT_LAYOUT", "matrix accumulator phase sequence is incomplete", op_id=chain[-1].op_id)
    begin, end = _matrix_contraction_bounds(chain[0], views, shards)
    cursor = begin
    for item in chain:
        item_begin, item_end = _matrix_contraction_bounds(item, views, shards)
        interval_end = item.attrs.tile.k_origin + item.attrs.tile.valid_k
        if (item_begin, item_end) != (begin, end) or item.attrs.tile.k_origin != cursor or interval_end > end:
            raise MeshIrError("E_EXPORT_LAYOUT", "matrix accumulator K regions are not an exact cover", op_id=item.op_id)
        cursor = interval_end
    if cursor != end:
        raise MeshIrError("E_EXPORT_LAYOUT", "matrix accumulator K regions do not cover the contraction domain", op_id=chain[-1].op_id)
    return tuple(chain)


def _verify_matrix_completion(kernel, computations, partials, shards, views, completed_partial_states) -> None:
    from mesh_ir.analysis.kernel_work import _empty_physical_compute_target
    from mesh_ir.ir.kernel_ir import KernelOpcode, MatrixPhase

    matrix = tuple(op for op in kernel.ops if op.opcode in (KernelOpcode.GEMM, KernelOpcode.BMM))
    empty = {op.op_id for op in (*matrix, *(item for item in kernel.ops if item.opcode is KernelOpcode.MATRIX_EPILOGUE)) if _empty_physical_compute_target(kernel, op)}
    producers = {transition.new_state_id: op for op in kernel.ops for transition in op.writes}
    published = set()
    for op in matrix:
        if op.op_id in empty:
            continue
        if op.attrs.phase is MatrixPhase.DIRECT:
            begin, end = _matrix_contraction_bounds(op, views, shards)
            if (op.attrs.tile.k_origin, op.attrs.tile.k_origin + op.attrs.tile.valid_k) != (begin, end):
                raise MeshIrError("E_EXPORT_LAYOUT", "direct matrix kernel does not cover its contraction domain", op_id=op.op_id)
    for epilogue in (op for op in kernel.ops if op.opcode is KernelOpcode.MATRIX_EPILOGUE):
        if epilogue.op_id in empty:
            continue
        state_id = epilogue.reads[0].state_id
        if state_id in completed_partial_states:
            partial = partials[completed_partial_states[state_id]]
            if partial.computation_id != epilogue.computation_id:
                raise MeshIrError("E_TENSOR_NOT_RESIDENT", "matrix epilogue consumes another computation's collective result", op_id=epilogue.op_id)
        elif state_id not in producers:
            raise MeshIrError("E_TENSOR_NOT_RESIDENT", "matrix epilogue has no complete accumulator chain", op_id=epilogue.op_id)
        else:
            _matrix_accumulation_chain(producers[state_id], epilogue.computation_id, epilogue.owner_core, 0, _logical_access_region(epilogue.reads[0], views, shards), producers, views, shards)
        output_key = (epilogue.computation_id, epilogue.owner_core, _logical_access_region(epilogue.writes[0], views, shards))
        if output_key in published:
            raise MeshIrError("E_EXPORT_LAYOUT", "matrix result region has multiple epilogues", op_id=epilogue.op_id)
        published.add(output_key)


def _verify_operation_semantics(kernel, computations, tensors, shards, objects, views) -> None:
    from mesh_ir.ir.kernel_ir import KernelOpcode, MatrixPhase
    from mesh_ir.analysis.kernel_work import _empty_physical_compute_target, _kernel_op_work_phases_verified

    vector_ops = frozenset((OpCode.ADD, OpCode.SUB, OpCode.MUL, OpCode.DIV, OpCode.RELU, OpCode.GELU, OpCode.SILU, OpCode.EXP, OpCode.RSQRT, OpCode.EMBEDDING_LOOKUP))
    compute_ops = frozenset((KernelOpcode.GEMM, KernelOpcode.BMM, KernelOpcode.MATRIX_EPILOGUE, KernelOpcode.VECTOR, KernelOpcode.DATA_MOVEMENT, KernelOpcode.REDUCE, KernelOpcode.SOFTMAX, KernelOpcode.NORM, KernelOpcode.COLLECTIVE, KernelOpcode.LOCAL_REDUCE))
    result_groups: dict[tuple[int, int], tuple[int, set[int]]] = {}
    for op in kernel.ops:
        if op.opcode in compute_ops:
            result_shard = shards[op.result_shard_id]
            if result_shard.owner_core != op.owner_core:
                raise MeshIrError("E_TENSOR_NOT_RESIDENT", "physical result shard owner differs from operation owner", op_id=op.op_id, shard_id=result_shard.shard_id)
            if op.writes and any(views[item.view_id].shard_id != result_shard.shard_id for item in op.writes):
                raise MeshIrError("E_EXPORT_LAYOUT", "physical operation write differs from its assigned result shard", op_id=op.op_id, shard_id=result_shard.shard_id)
            if op.opcode in (KernelOpcode.COLLECTIVE, KernelOpcode.LOCAL_REDUCE):
                partial = next((item for item in kernel.partial_sums if item.partial_sum_id == op.attrs.partial_sum_id), None)
                if partial is None:
                    raise MeshIrError("E_ABI_BOUNDS", "partial operation references an absent definition", op_id=op.op_id, partial_sum_id=op.attrs.partial_sum_id)
                if result_shard.partial_sum_id != partial.partial_sum_id or result_shard.tensor_id != partial.accumulator_tensor_id or result_shard.placement_id != partial.placement_id or partial.computation_id != op.computation_id:
                    raise MeshIrError("E_PLACEMENT_INFEASIBLE", "partial result assignment differs from its definition", op_id=op.op_id, shard_id=result_shard.shard_id)
            key = (op.computation_id, result_shard.tensor_id)
            assigned = result_groups.get(key)
            if assigned is None:
                result_groups[key] = (result_shard.placement_id, {op.owner_core})
            elif assigned[0] != result_shard.placement_id:
                raise MeshIrError("E_PLACEMENT_INFEASIBLE", "one physical computation result uses multiple placement groups", op_id=op.op_id, tensor_id=result_shard.tensor_id)
            else:
                assigned[1].add(op.owner_core)
        empty_target_tensor_id = _empty_physical_compute_target(kernel, op)
        if empty_target_tensor_id:
            _operation_data_contract(op, computations, tensors, shards, objects, views, empty_target_tensor_id)
            _kernel_op_work_phases_verified(kernel, op.op_id)
            continue
        if op.opcode in compute_ops:
            used = tuple(objects[views[item.view_id].object_id] for item in (*op.reads, *op.writes))
            if any(item.memory_space is not MemorySpace.CORE_SRAM or item.owner_core != op.owner_core for item in used):
                raise MeshIrError("E_TENSOR_NOT_RESIDENT", "compute operation does not use owner-local SRAM", op_id=op.op_id)
            if any(views[item.view_id].layout is None for item in (*op.reads, *op.writes)):
                raise MeshIrError("E_EXPORT_LAYOUT", "compute operation uses a view without a consumer layout", op_id=op.op_id)
        if op.opcode is KernelOpcode.LOCAL_COPY:
            source_object, destination_object = _paired_movement_objects(op, tensors, shards, objects, views)
            if op.computation_id or op.result_shard_id or op.done_token is None or source_object.memory_space is not MemorySpace.CORE_SRAM or destination_object.memory_space is not MemorySpace.CORE_SRAM or source_object.owner_core != op.owner_core or destination_object.owner_core != op.owner_core:
                raise MeshIrError("E_TENSOR_NOT_RESIDENT", "local copy endpoints or identities are invalid", op_id=op.op_id)
            _kernel_op_work_phases_verified(kernel, op.op_id)
            continue
        if op.opcode is KernelOpcode.DMA:
            attrs = op.attrs
            if op.owner_core != attrs.issuing_core:
                raise MeshIrError("E_DMA_RANGE", "DMA issuing owner is inconsistent", op_id=op.op_id)
            if attrs.kind is DmaKind.LOCAL_FILL:
                dst_obj = objects[views[op.writes[0].view_id].object_id] if len(op.writes) == 1 else None
                if op.reads or len(op.writes) != 1 or not 1 <= len(attrs.fill_pattern) <= 16 or attrs.transfer_id or attrs.issuing_core != attrs.source_core or attrs.issuing_core != attrs.destination_core or dst_obj.memory_space is not MemorySpace.CORE_SRAM or dst_obj.owner_core != attrs.issuing_core:
                    raise MeshIrError("E_DMA_RANGE", "local fill attributes or operands are invalid", op_id=op.op_id)
            else:
                if attrs.fill_pattern:
                    raise MeshIrError("E_DMA_RANGE", "DMA requires paired source and destination regions", op_id=op.op_id)
                src_obj, dst_obj = _paired_movement_objects(op, tensors, shards, objects, views)
                if attrs.kind in (DmaKind.LOAD, DmaKind.PREFETCH) and (src_obj.memory_space not in (MemorySpace.HBM, MemorySpace.HOST_SHARED) or dst_obj.memory_space is not MemorySpace.CORE_SRAM or dst_obj.owner_core != attrs.issuing_core):
                    raise MeshIrError("E_DMA_RANGE", "load endpoints are invalid", op_id=op.op_id)
                if attrs.kind in (DmaKind.LOAD, DmaKind.PREFETCH) and (attrs.source_core != INVALID_CORE_ID or attrs.destination_core != attrs.issuing_core):
                    raise MeshIrError("E_DMA_RANGE", "load core identities are invalid", op_id=op.op_id)
                if attrs.kind is DmaKind.STORE and (src_obj.memory_space is not MemorySpace.CORE_SRAM or dst_obj.memory_space not in (MemorySpace.HBM, MemorySpace.HOST_SHARED) or src_obj.owner_core != attrs.issuing_core):
                    raise MeshIrError("E_DMA_RANGE", "store endpoints are invalid", op_id=op.op_id)
                if attrs.kind is DmaKind.STORE and (attrs.source_core != attrs.issuing_core or attrs.destination_core != INVALID_CORE_ID):
                    raise MeshIrError("E_DMA_RANGE", "store core identities are invalid", op_id=op.op_id)
                if attrs.kind is DmaKind.P2P_PUSH and (src_obj.memory_space is not MemorySpace.CORE_SRAM or dst_obj.memory_space is not MemorySpace.CORE_SRAM or src_obj.owner_core != attrs.source_core or dst_obj.owner_core != attrs.destination_core or attrs.issuing_core != attrs.source_core or attrs.transfer_id < 1):
                    raise MeshIrError("E_P2P_UNMATCHED", "P2P endpoints are invalid", op_id=op.op_id)
            continue
        if op.opcode in (KernelOpcode.GEMM, KernelOpcode.BMM):
            if op.attrs.phase is MatrixPhase.DIRECT:
                expected_reads = 3 if op.attrs.graph_opcode is OpCode.LINEAR_BIAS else 2
            elif op.attrs.phase in (MatrixPhase.ACCUMULATE_CONTINUE, MatrixPhase.ACCUMULATE_FINAL):
                expected_reads = 3
            else:
                expected_reads = 2
            if len(op.reads) != expected_reads or len(op.writes) != 1:
                raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "matrix kernel has invalid arity", op_id=op.op_id)
            attrs = op.attrs
            lhs, rhs, out = op.reads[0].region.shape, op.reads[1].region.shape, op.writes[0].region.shape
            if min(len(lhs), len(rhs), len(out)) < 2:
                raise MeshIrError("E_EXPORT_LAYOUT", "matrix operands must have matrix rank", op_id=op.op_id)
            lhs_m, lhs_k = (lhs[-1], lhs[-2]) if attrs.semantic_attrs.lhs_transpose else (lhs[-2], lhs[-1])
            rhs_k, rhs_n = (rhs[-1], rhs[-2]) if attrs.semantic_attrs.rhs_transpose else (rhs[-2], rhs[-1])
            if attrs.graph_opcode is OpCode.BMM:
                if len(lhs) != 3 or len(rhs) != 3 or len(out) != 3 or lhs[:-2] != rhs[:-2] or lhs[:-2] != out[:-2]:
                    raise MeshIrError("E_EXPORT_LAYOUT", "batched matrix operands have inconsistent batch shapes", op_id=op.op_id)
            else:
                leading = []
                for index in range(1, max(len(lhs), len(rhs)) - 1):
                    left = lhs[-2 - index] if index <= len(lhs) - 2 else 1
                    right = rhs[-2 - index] if index <= len(rhs) - 2 else 1
                    if left == right:
                        leading.append(left)
                    elif left == 1:
                        leading.append(right)
                    elif right == 1:
                        leading.append(left)
                    else:
                        raise MeshIrError("E_EXPORT_LAYOUT", "matrix physical batch regions do not broadcast", op_id=op.op_id)
                if tuple(reversed(leading)) != out[:-2]:
                    raise MeshIrError("E_EXPORT_LAYOUT", "matrix physical batch region differs from its result", op_id=op.op_id)
            if attrs.phase is MatrixPhase.DIRECT and attrs.graph_opcode is OpCode.LINEAR_BIAS and op.reads[2].region.shape != (rhs_n,):
                raise MeshIrError("E_EXPORT_LAYOUT", "linear bias region does not match output columns", op_id=op.op_id)
            batch = _product(out[:-2], "matrix batch")
            tile = attrs.tile
            if lhs_k != rhs_k or out[-2:] != (lhs_m, rhs_n) or (tile.valid_batch, tile.valid_m, tile.valid_n, tile.valid_k) != (batch, lhs_m, rhs_n, lhs_k):
                raise MeshIrError("E_EXPORT_LAYOUT", "matrix tile does not match operand regions", op_id=op.op_id)
        if op.opcode is KernelOpcode.MATRIX_EPILOGUE:
            expected_reads = 2 if op.attrs.graph_opcode is OpCode.LINEAR_BIAS else 1
            if len(op.reads) != expected_reads or len(op.writes) != 1:
                raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "matrix epilogue has invalid physical operands", op_id=op.op_id)
            output = op.writes[0].region.shape
            if op.reads[0].region.shape != output or len(output) < 2:
                raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "matrix epilogue has invalid physical operands", op_id=op.op_id)
            if op.attrs.graph_opcode is OpCode.LINEAR_BIAS and op.reads[1].region.shape != (output[-1],):
                raise MeshIrError("E_EXPORT_LAYOUT", "matrix epilogue bias region does not match output columns", op_id=op.op_id)
            batch = _product(output[:-2], "matrix epilogue batch")
            if (op.attrs.tile.valid_batch, op.attrs.tile.valid_m, op.attrs.tile.valid_n) != (batch, output[-2], output[-1]):
                raise MeshIrError("E_EXPORT_LAYOUT", "matrix epilogue tile differs from its result region", op_id=op.op_id)
        if op.opcode is KernelOpcode.VECTOR:
            attrs = op.attrs
            if attrs.graph_opcode in (OpCode.ADD, OpCode.SUB, OpCode.MUL, OpCode.DIV):
                expected_reads = 1 if attrs.semantic_attrs.scalar is not None else 2
            elif attrs.graph_opcode is OpCode.EMBEDDING_LOOKUP:
                expected_reads = 2
            else:
                expected_reads = 1
            if attrs.graph_opcode not in vector_ops or len(op.reads) != expected_reads or len(op.writes) != 1:
                raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "vector kernel has invalid semantic opcode or arity", op_id=op.op_id)
            if type(attrs.semantic_attrs) is ElementwiseAttrs and any(access.region.shape != op.writes[0].region.shape for access in op.reads):
                raise MeshIrError("E_EXPORT_LAYOUT", "elementwise operand regions do not match output shape", op_id=op.op_id)
        if op.opcode is KernelOpcode.DATA_MOVEMENT:
            expected_reads = 2 if op.attrs.graph_opcode is OpCode.GATHER_ROWS else 1
            if op.attrs.graph_opcode is OpCode.CONCAT:
                expected_reads = len(op.reads)
            if not op.reads or len(op.reads) != expected_reads or len(op.writes) != 1:
                raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "movement kernel has invalid arity", op_id=op.op_id)
        if op.opcode in (KernelOpcode.REDUCE, KernelOpcode.SOFTMAX) and (len(op.reads) != 1 or len(op.writes) != 1):
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "reduction kernel has invalid arity", op_id=op.op_id)
        if op.opcode is KernelOpcode.SOFTMAX and op.reads and op.reads[0].region.shape != op.writes[0].region.shape:
            raise MeshIrError("E_EXPORT_LAYOUT", "softmax input and output regions differ", op_id=op.op_id)
        if op.opcode is KernelOpcode.NORM:
            expected_reads = 1 + int(op.attrs.semantic_attrs.has_weight) + int(op.attrs.semantic_attrs.has_bias)
            if len(op.reads) != expected_reads or len(op.writes) != 1:
                raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "normalization kernel has invalid arity", op_id=op.op_id)
            if op.reads and op.reads[0].region.shape != op.writes[0].region.shape:
                raise MeshIrError("E_EXPORT_LAYOUT", "normalization input and output regions differ", op_id=op.op_id)
        if op.opcode is KernelOpcode.LOCAL_REDUCE:
            if not op.writes or len(op.reads) % len(op.writes) or len(op.reads) // len(op.writes) < 2:
                raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "local reduction requires one fixed fan-in group per output region", op_id=op.op_id)
            fan_in = len(op.reads) // len(op.writes)
            dtypes = {tensors[shards[views[item.view_id].shard_id].tensor_id].dtype for item in (*op.reads, *op.writes)}
            if len(dtypes) != 1:
                raise MeshIrError("E_EXPORT_DTYPE", "local reduction input and output dtypes differ", op_id=op.op_id)
            for index, transition in enumerate(op.writes):
                output = _logical_access_region(transition, views, shards)
                if any(_logical_access_region(access, views, shards) != output or access.region.steps != transition.region.steps for access in op.reads[index * fan_in : (index + 1) * fan_in]):
                    raise MeshIrError("E_EXPORT_LAYOUT", "local reduction input group differs from its output region", op_id=op.op_id)
        if op.opcode is KernelOpcode.COLLECTIVE and (len(op.reads) != 1 or len(op.writes) != 1):
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "collective has invalid arity", op_id=op.op_id)
        if op.opcode in (KernelOpcode.RECV_WAIT, KernelOpcode.BARRIER) and (op.reads or op.writes):
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "synchronization operation carries data operands", op_id=op.op_id)
        if hasattr(op.attrs, "cost"):
            input_bytes = sum(_region_bytes(access, views, objects, tensors, shards) for access in op.reads)
            output_bytes = sum(_region_bytes(access, views, objects, tensors, shards) for access in op.writes)
            if op.attrs.cost.logical_input_bytes != input_bytes or op.attrs.cost.logical_output_bytes != output_bytes or op.attrs.cost.local_storage_bytes != checked_add_u64(input_bytes, output_bytes, "local storage work"):
                raise MeshIrError("E_EXPORT_LAYOUT", "kernel byte cost is inconsistent", op_id=op.op_id)
        _operation_data_contract(op, computations, tensors, shards, objects, views)
        if hasattr(op.attrs, "cost"):
            _kernel_op_work_phases_verified(kernel, op.op_id)
    placements = {item.placement_id: item for item in kernel.placements}
    for (computation_id, tensor_id), (placement_id, owners) in result_groups.items():
        if owners != set(placements[placement_id].core_ids):
            raise MeshIrError("E_PLACEMENT_INFEASIBLE", "physical computation result does not cover every assigned owner", computation_id=computation_id, tensor_id=tensor_id, placement_id=placement_id)


def _verify_p2p(kernel, objects, views, tensors, shards) -> None:
    from mesh_ir.ir.kernel_ir import KernelOpcode

    sends = {}
    receives = {}
    for op in kernel.ops:
        if op.opcode is KernelOpcode.DMA and op.attrs.kind is DmaKind.P2P_PUSH:
            if op.attrs.transfer_id in sends:
                raise MeshIrError("E_P2P_UNMATCHED", "P2P transfer has duplicate send", transfer_id=op.attrs.transfer_id)
            sends[op.attrs.transfer_id] = op
        if op.opcode is KernelOpcode.RECV_WAIT:
            if op.attrs.transfer_id in receives:
                raise MeshIrError("E_P2P_UNMATCHED", "P2P transfer has duplicate receive", transfer_id=op.attrs.transfer_id)
            receives[op.attrs.transfer_id] = op
    if set(sends) != set(receives):
        raise MeshIrError("E_P2P_UNMATCHED", "P2P sends and receives are not bijective", sends=sorted(sends), receives=sorted(receives))
    for transfer_id, send in sends.items():
        receive = receives[transfer_id]
        attrs, wait = send.attrs, receive.attrs
        size = sum(_region_bytes(transition, views, objects, tensors, shards) for transition in send.writes)
        if (attrs.source_core, attrs.destination_core, size) != (wait.source_core, wait.destination_core, wait.expected_bytes) or receive.owner_core != wait.destination_core or send.done_token not in receive.after_tokens:
            raise MeshIrError("E_P2P_UNMATCHED", "P2P send and receive identities differ", transfer_id=transfer_id)


def _verify_physical_hazards(kernel, dependencies, accesses) -> None:
    records = []
    for op, state_id, object_id, write, spans in accesses:
        start = dependencies.op_node_by_id[op.op_id - 1]
        completion = dependencies.token_node_by_id[op.done_token - 1]
        records.append((op.op_id, state_id, object_id, write, spans, start, completion))
    for index, first in enumerate(records):
        for second in records[index + 1 :]:
            if first[0] == second[0] or first[2] != second[2] or not (first[3] or second[3]) or not spans_overlap(first[4], second[4]):
                continue
            ordered = dependencies.graph.happens_before(first[6], second[5]) or dependencies.graph.happens_before(second[6], first[5])
            if not ordered:
                raise MeshIrError("E_TENSOR_NOT_RESIDENT", "overlapping physical accesses are unordered", first_op_id=first[0], second_op_id=second[0], object_id=first[2])


def _verify_computations(kernel, computations, tensors, shards) -> None:
    from mesh_ir.ir.kernel_ir import KernelOpcode

    layout_opcodes = METADATA_VIEW_OPCODES | {OpCode.CONTIGUOUS_COPY}

    def data_contract(tensor):
        return TensorDataContract(tuple(Const(dimension) for dimension in tensor.shape), tensor.dtype)

    def layout_contract(tensor):
        return TensorLayoutContract(tuple(Const(dimension) for dimension in tensor.shape), tensor.dtype, tensor.tensor_id, tuple(FixedStride(stride) for stride in tensor.strides), tensor.storage_offset_elements, tensor.alias_root_tensor_id, tensor.access)

    for computation in kernel.computations:
        if computation.result_tensor_id not in tensors or any(tensor_id not in tensors for tensor_id in computation.operand_tensor_ids):
            raise MeshIrError("E_ABI_BOUNDS", "computation references an invalid tensor", computation_id=computation.computation_id)
        result = tensors[computation.result_tensor_id]
        if result.producer_computation_id != computation.computation_id:
            raise MeshIrError("E_ABI_BOUNDS", "computation result producer identity differs", computation_id=computation.computation_id, tensor_id=result.tensor_id)
        operands = tuple(tensors[tensor_id] for tensor_id in computation.operand_tensor_ids)
        if computation.opcode in layout_opcodes:
            verify_operation_layout_contract(computation.computation_id, computation.opcode, computation.attrs, tuple(layout_contract(item) for item in operands), layout_contract(result))
        else:
            verify_operation_data_contract(computation.computation_id, computation.opcode, computation.attrs, tuple(data_contract(item) for item in operands), data_contract(result))
    stable_keys = tuple(op.stable_key for op in kernel.ops)
    if len(set(stable_keys)) != len(stable_keys):
        raise MeshIrError("E_ABI_ORDER", "physical operation stable keys must be unique")
    for op in kernel.ops:
        if op.computation_id and op.computation_id not in computations:
            raise MeshIrError("E_ABI_BOUNDS", "physical operation references an invalid computation", op_id=op.op_id)
    for tensor in kernel.tensors:
        if tensor.producer_computation_id and tensor.producer_computation_id not in computations:
            raise MeshIrError("E_ABI_BOUNDS", "tensor producer computation is absent", tensor_id=tensor.tensor_id)
    compute_opcodes = frozenset((
        KernelOpcode.GEMM,
        KernelOpcode.BMM,
        KernelOpcode.MATRIX_EPILOGUE,
        KernelOpcode.VECTOR,
        KernelOpcode.DATA_MOVEMENT,
        KernelOpcode.REDUCE,
        KernelOpcode.SOFTMAX,
        KernelOpcode.NORM,
        KernelOpcode.COLLECTIVE,
        KernelOpcode.LOCAL_REDUCE,
    ))
    for op in kernel.ops:
        if op.opcode in compute_opcodes and op.computation_id not in computations:
            raise MeshIrError("E_ABI_BOUNDS", "physical compute operation has no computation", op_id=op.op_id)
        if op.opcode in compute_opcodes:
            if op.result_shard_id not in shards:
                raise MeshIrError("E_ABI_BOUNDS", "physical compute operation has no valid result shard", op_id=op.op_id)
        elif op.result_shard_id:
            raise MeshIrError("E_ABI_BOUNDS", "noncompute operation has a result shard", op_id=op.op_id)


def _verify_intrinsic(kernel):
    _dense(kernel.tensors, "tensor_id", "tensor")
    _dense(kernel.computations, "computation_id", "computation")
    _dense(kernel.placements, "placement_id", "placement")
    _dense(kernel.shards, "shard_id", "shard")
    _dense(kernel.partial_sums, "partial_sum_id", "partial-SUM")
    _dense(kernel.objects, "object_id", "object")
    _dense(kernel.views, "view_id", "view")
    _dense(kernel.states, "state_id", "state")
    _dense(kernel.tokens, "token_id", "token")
    _dense(kernel.ops, "op_id", "operation")
    tensors = {item.tensor_id: item for item in kernel.tensors}
    computations = {item.computation_id: item for item in kernel.computations}
    partials = {item.partial_sum_id: item for item in kernel.partial_sums}
    placements = {item.placement_id: item for item in kernel.placements}
    shards = {item.shard_id: item for item in kernel.shards}
    objects = {item.object_id: item for item in kernel.objects}
    views = {item.view_id: item for item in kernel.views}
    states = {item.state_id: item for item in kernel.states}
    tokens = {item.token_id: item for item in kernel.tokens}
    _verify_computations(kernel, computations, tensors, shards)
    _verify_tensor_and_shards(kernel, tensors, placements)
    _verify_objects_and_views(kernel, tensors, shards, objects)
    dependencies, accesses = _verify_states_and_ops(kernel, tensors, shards, objects, views, states, tokens)
    initialized = _verify_initialization(kernel, dependencies, tensors, shards, objects, views, states)
    completed_partial_states = _verify_partial_contributors(kernel, dependencies, initialized, tensors, shards, objects, views, states)
    _verify_operation_semantics(kernel, computations, tensors, shards, objects, views)
    _verify_matrix_completion(kernel, computations, partials, shards, views, completed_partial_states)
    _verify_p2p(kernel, objects, views, tensors, shards)
    _verify_physical_hazards(kernel, dependencies, accesses)
    return dependencies, completed_partial_states


def _verify_arch_memory(kernel, arch) -> None:
    from mesh_ir.architecture import validate_arch
    from mesh_ir.ir.kernel_ir import KernelOpcode

    validate_arch(arch)
    ranks = {core_id: index for index, core_id in enumerate(arch.core_ids)}
    for placement in kernel.placements:
        if any(core_id not in ranks for core_id in placement.core_ids) or placement.core_ids != tuple(sorted(placement.core_ids, key=ranks.__getitem__)):
            raise MeshIrError("E_PLACEMENT_INFEASIBLE", "placement is not in architecture row-major order", placement_id=placement.placement_id)
    for obj in kernel.objects:
        if obj.memory_space is MemorySpace.CORE_SRAM and obj.owner_core not in ranks:
            raise MeshIrError("E_PLACEMENT_INFEASIBLE", "buffer owner is not a hardware core", object_id=obj.object_id)
    for op in kernel.ops:
        if op.opcode not in (KernelOpcode.ALLOC, KernelOpcode.VIEW) and op.owner_core not in ranks:
            raise MeshIrError("E_PLACEMENT_INFEASIBLE", "effectful operation owner is not a hardware core", op_id=op.op_id)
        if op.opcode is KernelOpcode.DMA and op.attrs.max_burst_beats is not None and op.attrs.max_burst_beats > arch.axi_max_burst_beats:
            raise MeshIrError("E_CAPABILITY_MISMATCH", "DMA burst limit exceeds the architecture ceiling", op_id=op.op_id)
        if op.opcode in (KernelOpcode.BARRIER, KernelOpcode.COLLECTIVE):
            participants = op.attrs.participants
            if len(set(participants)) != len(participants) or any(core not in ranks for core in participants) or participants != tuple(sorted(participants, key=ranks.__getitem__)) or op.owner_core not in participants:
                raise MeshIrError("E_PLACEMENT_INFEASIBLE", "synchronization participants are not unique row-major hardware owners", op_id=op.op_id)


def verify_kernel_memory(records, arch=None):
    from mesh_ir.ir.kernel_ir import VerifiedKernelMemory

    _verify_memory_types(records)
    dependencies, _ = _verify_intrinsic(records)
    if arch is not None:
        _verify_arch_memory(records, arch)
    return VerifiedKernelMemory(records, dependencies)


def _kernel_capability_requirements_verified(kernel) -> tuple[KernelCapabilityRequirement, ...]:
    from mesh_ir.analysis.kernel_work import _kernel_op_work_phases_verified

    requirements = set()
    for op in kernel.ops:
        for phase in _kernel_op_work_phases_verified(kernel, op.op_id):
            for work in phase.work:
                requirements.add(KernelCapabilityRequirement(op.op_id, work.engine, work.dtype))
    return tuple(sorted(requirements))


def kernel_capability_requirements(kernel) -> tuple[KernelCapabilityRequirement, ...]:
    verify_kernel(kernel)
    return _kernel_capability_requirements_verified(kernel)


def _verify_kernel(kernel, arch=None):
    verify_kernel_types(kernel)
    validate_schema("mesh_kernel_v1.schema.json", kernel.canonical_dict(), "Kernel IR")
    if kernel.schema_major != 1 or kernel.schema_minor != 0 or kernel.required_features:
        raise MeshIrError("E_ABI_VERSION", "Kernel schema version or feature set is unsupported")
    if not _DIGEST.fullmatch(kernel.arch_digest) or not _DIGEST.fullmatch(kernel.source_semantic_hash):
        raise MeshIrError("E_ABI_CHECKSUM", "Kernel lineage digest is invalid")
    if not _DIGEST.fullmatch(kernel.semantic_sha256) or semantic_sha256(kernel.semantic_dict()) != kernel.semantic_sha256:
        raise MeshIrError("E_ABI_CHECKSUM", "Kernel semantic checksum does not match")
    if not kernel.entrypoint or not kernel.profile_id:
        raise MeshIrError("E_CONFIG", "Kernel entrypoint and profile identity are required")
    if tuple(item.tensor_id for item in kernel.tensor_lineage) != tuple(item.tensor_id for item in kernel.tensors) or tuple(item.computation_id for item in kernel.computation_lineage) != tuple(item.computation_id for item in kernel.computations):
        raise MeshIrError("E_ABI_ORDER", "Kernel lineage tables must exactly follow their intrinsic records")
    for tensor, lineage in zip(kernel.tensors, kernel.tensor_lineage):
        if (tensor.synthesized_purpose is None) != (lineage.source_value_id > 0):
            raise MeshIrError("E_ABI_BOUNDS", "Kernel tensor lineage differs from synthesized identity", tensor_id=tensor.tensor_id)
    records = kernel.memory_records()
    _verify_memory_types(records)
    dependencies, completed_partial_states = _verify_intrinsic(records)
    if arch is not None:
        _verify_arch_memory(records, arch)
    if arch is not None:
        from mesh_ir.architecture import validate_arch

        validate_arch(arch)
        expected = arch.digest().hex()
        if kernel.arch_digest != expected:
            raise MeshIrError("E_ARCH_DIGEST", "Kernel architecture digest does not match", expected=expected, actual=kernel.arch_digest)
    return dependencies, completed_partial_states


def verify_kernel(kernel, arch=None) -> None:
    _verify_kernel(kernel, arch)


def verify_scheduled_ready_kernel(kernel, arch) -> None:
    from mesh_ir.ir.kernel_ir import KernelOpcode

    if arch is None:
        raise MeshIrError("E_CONFIG", "Scheduled-ready Kernel verification requires architecture")
    _, completed_partial_states = _verify_kernel(kernel, arch)
    throughput = {
        Engine.TENSOR: arch.tensor_macs_per_cycle,
        Engine.VECTOR: arch.vector_elements_per_cycle,
        Engine.REDUCE: arch.reduce_ops_per_cycle,
    }
    for requirement in _kernel_capability_requirements_verified(kernel):
        if requirement.dtype.name.lower() not in throughput[requirement.engine]:
            raise MeshIrError("E_CAPABILITY_MISMATCH", "architecture lacks required compute dtype throughput", op_id=requirement.op_id, engine=requirement.engine.name, dtype=requirement.dtype.name)
    for op in kernel.ops:
        if op.opcode is KernelOpcode.COLLECTIVE:
            raise MeshIrError("E_CAPABILITY_MISMATCH", "abstract collective is not Scheduled-ready", op_id=op.op_id)
    latest = {}
    for state in kernel.states:
        if state.object_id not in latest or state.version > latest[state.object_id].version:
            latest[state.object_id] = state
    views = {item.view_id: item for item in kernel.views}
    accumulator_objects = {
        views[transition.view_id].object_id
        for op in kernel.ops
        if op.opcode in (KernelOpcode.GEMM, KernelOpcode.BMM)
        for transition in op.writes
        if kernel.states[transition.new_state_id - 1].partial_sum_id
    }
    unresolved = tuple(state.state_id for object_id, state in latest.items() if object_id in accumulator_objects and state.partial_sum_id and state.state_id not in completed_partial_states)
    if unresolved:
        raise MeshIrError("E_TENSOR_NOT_RESIDENT", "partial-SUM states require a concrete completion proof", state_ids=unresolved)
