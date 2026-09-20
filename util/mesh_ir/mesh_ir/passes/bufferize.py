from __future__ import annotations

from dataclasses import dataclass

from itertools import product

from mesh_ir.analysis.regions import flat_interval_regions, region_byte_spans, spans_contain
from mesh_ir.canonical import canonical_json_bytes, checked_add_u64, checked_mul_u64, checked_u64
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Access, Const, DType, FixedStride, INVALID_CORE_ID, Layout, MemorySpace, ShapeProductStride, StorageClass, TensorRole
from mesh_ir.ir.graph_ir import GraphModule, GraphValue
from mesh_ir.ir.kernel_ir import DistributionKind, ElementRegion, KernelComputation, KernelComputationLineage, KernelTensor, KernelTensorLineage, PartialSumDefinition, Placement, SynthesizedTensorPurpose, TensorShard
from mesh_ir.passes.placement import PlacementCandidate, PlacementDecision
from mesh_ir.passes.placement_geometry import DenseLogicalRegion, GeometryPhase, MappedStorageRegion, OperationPlacementGeometry, PlacementTransferKind, RegionSourceKind, ResidentWindow, ResidentWindowRole, WindowUseGeometry, operand_window_use_regions
from mesh_ir.passes.planning_prefix import OperationIdentity, PlanningResult
from mesh_ir.passes.sharding import PlannedValueDistribution, VariantSharding
from mesh_ir.passes.tiling import TilingState


@dataclass(frozen=True)
class PlannedBufferObject:
    object_id: int
    storage_tensor_id: int
    owner_core: int
    memory_space: MemorySpace
    shape: tuple[int, ...]
    strides: tuple[int, ...]
    footprint_bytes: int
    alignment_bytes: int
    buffer_index: int
    window_id: int


@dataclass(frozen=True)
class PlannedBufferView:
    view_id: int
    object_id: int
    tensor_id: int
    shard_id: int
    shard_origin: tuple[int, ...]
    padded_shape: tuple[int, ...]
    valid_shape: tuple[int, ...]
    object_offset_elements: int
    object_strides: tuple[int, ...]
    layout: Layout | None
    generation: int


@dataclass(frozen=True)
class PlannedBufferAccess:
    view_id: int
    region: ElementRegion


@dataclass(frozen=True)
class PlannedAccessPair:
    source: PlannedBufferAccess
    destination: PlannedBufferAccess


@dataclass(frozen=True)
class PlannedWindowUse:
    identity: OperationIdentity
    operand_index: int
    logical_rank: int
    use_index: int
    compute_tile_ordinal: int | None
    output_tile_index: int
    phase: GeometryPhase
    access: PlannedBufferAccess
    local_materializations: tuple[PlannedAccessPair, ...]


@dataclass(frozen=True)
class PlannedResultView:
    identity: OperationIdentity
    output_tile_index: int
    owner_core: int
    role: ResidentWindowRole
    access: PlannedBufferAccess | None
    shard_id: int


@dataclass(frozen=True)
class PlannedTileBuffers:
    tile_id: int
    operand_accesses: tuple[PlannedBufferAccess, ...]
    result_access: PlannedBufferAccess | None
    result_shard_id: int


@dataclass(frozen=True)
class VariantBuffers:
    entrypoint: str
    profile_id: str
    tensors: tuple[KernelTensor, ...]
    computations: tuple[KernelComputation, ...]
    tensor_lineage: tuple[KernelTensorLineage, ...]
    computation_lineage: tuple[KernelComputationLineage, ...]
    placements: tuple[Placement, ...]
    shards: tuple[TensorShard, ...]
    partial_sums: tuple[PartialSumDefinition, ...]
    objects: tuple[PlannedBufferObject, ...]
    views: tuple[PlannedBufferView, ...]
    window_uses: tuple[PlannedWindowUse, ...]
    result_views: tuple[PlannedResultView, ...]
    tile_buffers: tuple[PlannedTileBuffers, ...]


@dataclass(frozen=True)
class BufferizationState:
    tiling: TilingState
    variants: tuple[VariantBuffers, ...]

    def semantic_bytes(self) -> bytes:
        return canonical_json_bytes(self)


_EXTERNAL_SOURCE_ROLES = frozenset((TensorRole.INPUT, TensorRole.WEIGHT, TensorRole.CONSTANT, TensorRole.STATE, TensorRole.KV_CACHE))


def _product(values: tuple[int, ...], field: str) -> int:
    result = 1
    for value in values:
        result = checked_mul_u64(result, value, field)
    return result


def _row_major(shape: tuple[int, ...]) -> tuple[int, ...]:
    return tuple(_product(shape[index + 1 :], "buffer stride") for index in range(len(shape)))


def _storage_elements(shape: tuple[int, ...], strides: tuple[int, ...], offset: int) -> int:
    if any(item == 0 for item in shape):
        return 0
    result = checked_add_u64(offset, 1, "buffer storage elements")
    for dimension, stride in zip(shape, strides):
        result = checked_add_u64(result, checked_mul_u64(dimension - 1, stride, "buffer storage elements"), "buffer storage elements")
    return result


def _consumer_mapping(value, padded_shape: tuple[int, ...], required_extent_bytes: int) -> tuple[tuple[int, ...], Layout]:
    strides = tuple(item.evaluate({}) for item in value.strides)
    if len(padded_shape) >= 2:
        matrix = checked_mul_u64(padded_shape[-2], padded_shape[-1], "transposed window elements")
        expected = tuple(checked_mul_u64(_product(padded_shape[index + 1 : -2], "transposed batch stride"), matrix, "transposed batch stride") for index in range(len(padded_shape) - 2)) + (1, padded_shape[-2])
        if strides == expected and checked_mul_u64(_storage_elements(padded_shape, strides, 0), value.dtype.byte_width, "transposed window bytes") <= required_extent_bytes:
            return strides, Layout.TRANSPOSED_2D_VIEW
    return _row_major(padded_shape), Layout.CONTIGUOUS_ROW_MAJOR


def project_mapped_region_pieces(value: GraphValue, parent: DenseLogicalRegion, mapped: MappedStorageRegion) -> tuple[DenseLogicalRegion, ...]:
    if type(value) is not GraphValue or type(parent) is not DenseLogicalRegion or type(mapped) is not MappedStorageRegion:
        raise MeshIrError("E_EXPORT_LAYOUT", "mapped region projection records are invalid")
    if type(value.value_id) is not int or value.value_id < 1 or type(value.alias_root) is not int or value.alias_root < 1 or type(value.dtype) is not DType:
        raise MeshIrError("E_EXPORT_LAYOUT", "mapped region value identity is invalid")
    if type(mapped.value_id) is not int or mapped.value_id < 1 or type(mapped.alias_root) is not int or mapped.alias_root < 1 or type(mapped.dtype) is not DType:
        raise MeshIrError("E_EXPORT_LAYOUT", "mapped region identity is invalid", value_id=value.value_id)
    if mapped.value_id != value.value_id or mapped.alias_root != value.alias_root or mapped.dtype is not value.dtype:
        raise MeshIrError("E_EXPORT_LAYOUT", "mapped region identity differs from its logical value", value_id=value.value_id)
    if type(value.shape) is not tuple or any(type(item) is not Const for item in value.shape) or type(value.strides) is not tuple or any(type(item) not in (FixedStride, ShapeProductStride) or item.symbol_ids() for item in value.strides):
        raise MeshIrError("E_SHAPE_UNBOUND", "mapped region projection requires a specialized value", value_id=value.value_id)
    shape = tuple(checked_u64(item.value, "mapped value shape") for item in value.shape)
    strides = tuple(checked_u64(item.evaluate({}), "mapped value stride") for item in value.strides)
    checked_u64(value.storage_offset, "mapped value storage offset")
    if len(shape) != len(strides) or type(parent.origin) is not tuple or type(parent.shape) is not tuple or not len(parent.origin) == len(parent.shape) == len(shape):
        raise MeshIrError("E_EXPORT_LAYOUT", "mapped parent rank is invalid", value_id=value.value_id)
    if any(type(item) is not int or item < 0 for item in parent.origin + parent.shape):
        raise MeshIrError("E_EXPORT_LAYOUT", "mapped parent vectors are invalid", value_id=value.value_id)
    for origin, extent, bound in zip(parent.origin, parent.shape, shape):
        if checked_add_u64(origin, extent, "mapped parent bound") > bound:
            raise MeshIrError("E_EXPORT_LAYOUT", "mapped parent exceeds its logical value", value_id=value.value_id)
    if type(mapped.element_offset) is not int or mapped.element_offset < 0 or type(mapped.shape) is not tuple or type(mapped.strides) is not tuple or len(mapped.shape) != len(mapped.strides) or any(type(item) is not int or item < 0 for item in mapped.shape + mapped.strides):
        raise MeshIrError("E_EXPORT_LAYOUT", "mapped storage vectors are invalid", value_id=value.value_id)
    checked_u64(mapped.element_offset, "mapped storage offset")
    for item in mapped.shape:
        checked_u64(item, "mapped storage shape")
    for item in mapped.strides:
        checked_u64(item, "mapped storage stride")
    if any(not extent for extent in parent.shape) or any(not extent for extent in mapped.shape):
        return ()
    width = value.dtype.byte_width
    mapped_spans = tuple((span.begin // width, span.end // width) for span in mapped.byte_spans)
    parent_base = value.storage_offset
    for origin, stride in zip(parent.origin, strides):
        parent_base = checked_add_u64(parent_base, checked_mul_u64(origin, stride, "mapped parent offset"), "mapped parent offset")
    axes = sorted((stride, extent, index) for index, (extent, stride) in enumerate(zip(parent.shape, strides)) if extent > 1 and stride > 0)
    dense_axes = set()
    dense_elements = 1
    for stride, extent, index in axes:
        if stride == dense_elements:
            dense_axes.add(index)
            dense_elements = checked_mul_u64(dense_elements, extent, "mapped dense elements")
    dense_order = tuple(index for _, _, index in sorted(((strides[index], parent.shape[index], index) for index in dense_axes), reverse=True))
    dense_shape = tuple(parent.shape[index] for index in dense_order)
    outer_axes = tuple(index for index, (extent, stride) in enumerate(zip(parent.shape, strides)) if extent > 1 and stride > 0 and index not in dense_axes)
    pieces = []
    for outer_indices in product(*(range(parent.shape[index]) for index in outer_axes)):
        block_begin = parent_base
        for index, coordinate in zip(outer_axes, outer_indices):
            block_begin = checked_add_u64(block_begin, checked_mul_u64(coordinate, strides[index], "mapped outer offset"), "mapped outer offset")
        block_end = checked_add_u64(block_begin, dense_elements, "mapped dense block end")
        for span_begin, span_end in mapped_spans:
            begin = max(block_begin, span_begin)
            end = min(block_end, span_end)
            if begin >= end:
                continue
            for dense_region in flat_interval_regions(dense_shape, begin - block_begin, end - begin):
                origin = list(parent.origin)
                piece_shape = [1] * len(parent.shape)
                for index, extent, stride in zip(range(len(parent.shape)), parent.shape, strides):
                    if stride == 0:
                        piece_shape[index] = extent
                for index, coordinate in zip(outer_axes, outer_indices):
                    origin[index] += coordinate
                for index, coordinate, extent in zip(dense_order, dense_region.origin, dense_region.shape):
                    origin[index] += coordinate
                    piece_shape[index] = extent
                pieces.append(DenseLogicalRegion(tuple(origin), tuple(piece_shape)))
    compacted = []
    for piece in sorted(pieces, key=lambda item: (item.origin, item.shape)):
        if compacted:
            previous = compacted[-1]
            merge_axes = tuple(
                index
                for index, (left_origin, left_extent, right_origin, right_extent) in enumerate(zip(previous.origin, previous.shape, piece.origin, piece.shape))
                if left_origin != right_origin or left_extent != right_extent
            )
            if len(merge_axes) == 1:
                axis = merge_axes[0]
                if previous.shape[axis] == piece.shape[axis] and checked_add_u64(previous.origin[axis], previous.shape[axis], "mapped piece coalescing") == piece.origin[axis]:
                    merged_shape = list(previous.shape)
                    merged_shape[axis] = checked_add_u64(merged_shape[axis], piece.shape[axis], "mapped piece coalescing")
                    compacted[-1] = DenseLogicalRegion(previous.origin, tuple(merged_shape))
                    continue
        compacted.append(piece)
    return tuple(compacted)


def _selected_geometry(tiling: TilingState, assignment_index: int, identity: OperationIdentity, variant_ordinal: int) -> OperationPlacementGeometry:
    if assignment_index >= len(tiling.shards.decisions.placements):
        raise MeshIrError("E_ABI_ORDER", "bufferization placement decisions omit an operation", op_id=identity.op_id)
    decision = tiling.shards.decisions.placements[assignment_index]
    if type(decision) is not PlacementDecision or decision.identity != identity or type(decision.candidate_index) is not int or not 0 <= decision.candidate_index < len(tiling.shards.decisions.candidates):
        raise MeshIrError("E_ABI_ORDER", "bufferization placement decision is invalid", op_id=identity.op_id)
    candidate = tiling.shards.decisions.candidates[decision.candidate_index]
    if type(candidate) is not PlacementCandidate or candidate.identity != identity or candidate.variant_ordinal != variant_ordinal or candidate.rejection is not None or type(candidate.geometry) is not OperationPlacementGeometry:
        raise MeshIrError("E_ABI_ORDER", "bufferization selected geometry is invalid", op_id=identity.op_id)
    return candidate.geometry


def bufferize_and_alias(planning: PlanningResult, tiling: TilingState) -> BufferizationState:
    if type(planning) is not PlanningResult or type(tiling) is not TilingState or len(planning.state.variants) != len(tiling.shards.variants):
        raise MeshIrError("E_CONFIG", "bufferization inputs are invalid")
    variants = []
    decision_index = 0
    for variant_ordinal, (graph, sharding) in enumerate(zip(planning.state.variants, tiling.shards.variants)):
        if type(graph) is not GraphModule or type(sharding) is not VariantSharding or (graph.entrypoint, graph.profile_id) != (sharding.entrypoint, sharding.profile_id) or len(graph.functions) != 1:
            raise MeshIrError("E_ABI_ORDER", "bufferization variant identity differs from planning")
        graph.verify()
        function = graph.functions[0]
        values = {item.value_id: item for item in graph.values}
        produced = {result for op in function.ops for result in op.results}
        tensor_id_by_value = {value.value_id: index for index, value in enumerate(graph.values, 1)}
        computation_id_by_op = {op.op_id: index for index, op in enumerate(function.ops, 1)}
        producer_by_value = {result: computation_id_by_op[op.op_id] for op in function.ops for result in op.results}
        tensors = []
        tensor_lineage = []
        for tensor_id, value in enumerate(graph.values, 1):
            shape = tuple(item.value for item in value.shape)
            strides = tuple(item.evaluate({}) for item in value.strides)
            logical = checked_mul_u64(_product(shape, "logical tensor elements"), value.dtype.byte_width, "logical tensor bytes")
            storage = checked_mul_u64(_storage_elements(shape, strides, value.storage_offset), value.dtype.byte_width, "tensor storage bytes")
            storage_class = StorageClass.EXTERNAL if value.value_id in function.inputs or value.value_id in function.outputs or value.value_id not in produced and value.role in _EXTERNAL_SOURCE_ROLES else StorageClass.CORE_SRAM
            tensors.append(KernelTensor(tensor_id, producer_by_value.get(value.value_id, 0), None, tensor_id_by_value[value.alias_root], value.storage_offset, value.name, value.role, value.dtype, shape, strides, storage_class, value.access, logical, storage, value.content_sha256))
            tensor_lineage.append(KernelTensorLineage(tensor_id, value.value_id))
        computations = tuple(
            KernelComputation(computation_id_by_op[op.op_id], op.opcode, tuple(tensor_id_by_value[item] for item in op.operands), tensor_id_by_value[op.results[0]], op.attrs)
            for op in function.ops
        )
        computation_lineage = tuple(KernelComputationLineage(computation_id_by_op[op.op_id], op.op_id, op.source_node_id) for op in function.ops)
        distributions = tuple(sharding.distributions)
        if any(type(item) is not PlannedValueDistribution for item in distributions):
            raise MeshIrError("E_CONFIG", "bufferization distributions have invalid types")
        distribution_ids = {item.geometry: item for item in distributions}
        placements = tuple(Placement(item.placement_id, item.geometry.core_ids) for item in distributions)
        shards = []
        for distribution in distributions:
            tensor_id = tensor_id_by_value[distribution.geometry.value_id]
            for shard_id, shard in zip(distribution.shard_ids, distribution.geometry.shards):
                shards.append(TensorShard(shard_id, tensor_id, distribution.placement_id, shard.owner_core, distribution.geometry.distribution, shard.global_origin, shard.padded_shape, shard.valid_shape, 0))
        partial_sums = []
        objects = []
        views = []
        window_uses = []
        result_views = []
        tile_buffers = []
        external_objects = {}
        external_root_ids = {values[value_id].alias_root for value_id in function.outputs}
        external_root_ids.update(
            value.alias_root
            for value in graph.values
            if value.value_id not in produced and value.role in _EXTERNAL_SOURCE_ROLES
        )
        external_roots = tuple(
            value
            for value in graph.values
            if value.value_id == value.alias_root and value.value_id in external_root_ids
        )
        for value in external_roots:
            shape = tuple(item.value for item in value.shape)
            strides = tuple(item.evaluate({}) for item in value.strides)
            elements = _storage_elements(shape, strides, value.storage_offset)
            object_id = len(objects) + 1
            objects.append(PlannedBufferObject(object_id, tensor_id_by_value[value.value_id], INVALID_CORE_ID, MemorySpace.HBM, (elements,), (1,), checked_mul_u64(elements, value.dtype.byte_width, "external buffer bytes"), value.dtype.byte_width, 0, 0))
            external_objects[value.value_id] = object_id
        geometries = []
        for operation_ordinal, op in enumerate(function.ops):
            identity = OperationIdentity(graph.entrypoint, graph.profile_id, function.function_id, op.op_id)
            geometry = _selected_geometry(tiling, decision_index, identity, variant_ordinal)
            decision_index += 1
            if geometry.operation_ordinal != operation_ordinal:
                raise MeshIrError("E_ABI_ORDER", "bufferization operation geometry order is invalid", op_id=op.op_id)
            geometries.append((identity, op, geometry))
        accumulator_tensor_by_op = {}
        for operation, (identity, op, geometry) in zip(sharding.operations, geometries):
            if not any(item.matrix_phase is not None and item.matrix_phase.name != "DIRECT" for item in geometry.compute_tiles):
                continue
            result_value = values[op.results[0]]
            shape = tuple(item.value for item in result_value.shape)
            dtype = op.attrs.accum_dtype
            tensor_id = len(tensors) + 1
            purpose = SynthesizedTensorPurpose.PARTIAL_SUM if operation.partial_sum_id else SynthesizedTensorPurpose.ACCUMULATION
            extent = checked_mul_u64(_product(shape, "accumulator elements"), dtype.byte_width, "accumulator bytes")
            tensors.append(KernelTensor(tensor_id, computation_id_by_op[op.op_id], purpose, tensor_id, 0, f"{result_value.name}.{purpose.value.lower()}", TensorRole.ACTIVATION, dtype, shape, _row_major(shape), StorageClass.CORE_SRAM, Access.READ_WRITE, extent, extent, None))
            tensor_lineage.append(KernelTensorLineage(tensor_id, 0))
            result_distribution = distribution_ids[geometry.result_distribution]
            first_shard_id = len(shards) + 1
            for index, shard in enumerate(geometry.result_distribution.shards):
                distribution = DistributionKind.PARTIAL_SUM if operation.partial_sum_id else geometry.result_distribution.distribution
                shards.append(TensorShard(first_shard_id + index, tensor_id, result_distribution.placement_id, shard.owner_core, distribution, shard.global_origin, shard.padded_shape, shard.valid_shape, operation.partial_sum_id))
            accumulator_tensor_by_op[op.op_id] = (tensor_id, tuple(range(first_shard_id, len(shards) + 1)))
            if operation.partial_sum_id:
                partial_sums.append(PartialSumDefinition(operation.partial_sum_id, computation_id_by_op[op.op_id], tensor_id_by_value[result_value.value_id], tensor_id, result_distribution.placement_id))
        windows = tuple(window for _, _, geometry in geometries for window in geometry.resident_windows)
        window_by_id = {window.window_id: window for window in windows}
        retained_by_window = {
            backing.window.window_id: backing
            for _, _, geometry in geometries
            for backing in geometry.retained_results
        }
        operation_by_window = {window.window_id: op for _, op, geometry in geometries for window in geometry.resident_windows}
        window_ids = tuple(item.window_id for item in windows)
        if len(window_ids) != len(set(window_ids)):
            raise MeshIrError("E_ABI_DUPLICATE", "bufferization resident window identity is duplicated")
        object_by_window = {}
        for window in windows:
            if type(window) is not ResidentWindow or window.value_id not in values:
                raise MeshIrError("E_ABI_BOUNDS", "resident window storage identity is invalid", window_id=window.window_id)
            operation = operation_by_window[window.window_id]
            if window.role in (ResidentWindowRole.ACCUMULATOR, ResidentWindowRole.COLLECTIVE_RECEIVE):
                if operation.op_id not in accumulator_tensor_by_op:
                    raise MeshIrError("E_ABI_BOUNDS", "accumulator window has no synthesized tensor", window_id=window.window_id)
                storage_tensor_id = accumulator_tensor_by_op[operation.op_id][0]
            else:
                storage_tensor_id = tensor_id_by_value[values[window.value_id].alias_root]
            if tensors[storage_tensor_id - 1].dtype is not window.dtype:
                raise MeshIrError("E_EXPORT_DTYPE", "resident window dtype differs from its storage tensor", window_id=window.window_id)
            object_id = len(objects) + 1
            strides = _row_major(window.padded_shape)
            footprint = checked_mul_u64(_product(window.padded_shape, "resident window elements"), window.dtype.byte_width, "resident window bytes")
            objects.append(PlannedBufferObject(object_id, storage_tensor_id, window.owner_core, MemorySpace.CORE_SRAM, window.padded_shape, strides, footprint, window.required_alignment_bytes, window.buffer_index, window.window_id))
            object_by_window[window.window_id] = object_id
        shard_by_distribution_rank = {}
        for distribution in distributions:
            for shard_id, shard in zip(distribution.shard_ids, distribution.geometry.shards):
                shard_by_distribution_rank[(distribution.geometry, shard.logical_rank)] = (shard_id, shard)
                value = values[distribution.geometry.value_id]
                root_object = external_objects.get(value.alias_root)
                if root_object is None:
                    continue
                shape = tuple(item.value for item in value.shape)
                strides = tuple(item.evaluate({}) for item in value.strides)
                offset = value.storage_offset
                for origin, stride in zip(shard.global_origin, strides):
                    offset = checked_add_u64(offset, checked_mul_u64(origin, stride, "external shard offset"), "external shard offset")
                views.append(PlannedBufferView(len(views) + 1, root_object, tensor_id_by_value[value.value_id], shard_id, (0,) * len(shape), shard.valid_shape, shard.valid_shape, offset, strides, None, 0))
        generations = {}
        resident_generation = {}
        resident_content = {}
        physical_use_generations = {}
        logical_use_views = {}
        retained_views = {}

        def retained_access(backing, value, distribution, owner_core, region, op_id, operand_index):
            established = distribution_ids.get(distribution)
            source_shards = tuple(
                (candidate_id, candidate)
                for candidate_id, candidate in zip(established.shard_ids, distribution.shards)
                if candidate.owner_core == owner_core
            ) if established is not None and distribution is not None else ()
            logical_strides = tuple(item.evaluate({}) for item in value.strides)
            element_offset = value.storage_offset
            for origin, stride in zip(region.origin, logical_strides):
                element_offset = checked_add_u64(element_offset, checked_mul_u64(origin, stride, "retained access offset"), "retained access offset")
            required_spans = region_byte_spans(element_offset, region.shape, logical_strides, value.dtype.byte_width)
            if len(source_shards) != 1 or not spans_contain(backing.mapped_region.byte_spans, required_spans):
                raise MeshIrError("E_TENSOR_NOT_RESIDENT", "selected retained source has no unique backing shard", op_id=op_id, operand_index=operand_index, window_id=backing.window.window_id)
            source_shard_id, source_shard = source_shards[0]
            source_origin = tuple(origin - base for origin, base in zip(region.origin, source_shard.global_origin))
            if any(item < 0 for item in source_origin) or any(origin + extent > padded for origin, extent, padded in zip(source_origin, region.shape, source_shard.padded_shape)):
                raise MeshIrError("E_EXPORT_LAYOUT", "retained access exceeds its established backing shard", op_id=op_id, operand_index=operand_index)
            source_key = (backing.window.window_id, value.value_id, source_shard_id)
            if source_key not in retained_views:
                object_record = objects[object_by_window[backing.window.window_id] - 1]
                object_strides, layout = _consumer_mapping(value, source_shard.padded_shape, object_record.footprint_bytes)
                physical_extent = checked_mul_u64(_storage_elements(source_shard.padded_shape, object_strides, 0), value.dtype.byte_width, "retained backing bytes")
                if physical_extent > object_record.footprint_bytes:
                    raise MeshIrError("E_EXPORT_LAYOUT", "retained view exceeds its physical backing", op_id=op_id, operand_index=operand_index)
                view = PlannedBufferView(len(views) + 1, object_by_window[backing.window.window_id], tensor_id_by_value[value.value_id], source_shard_id, (0,) * len(source_shard.padded_shape), source_shard.padded_shape, source_shard.valid_shape, 0, object_strides, layout, 0)
                views.append(view)
                retained_views[source_key] = view.view_id
            return PlannedBufferAccess(retained_views[source_key], ElementRegion(source_origin, region.shape, (1,) * len(region.shape)))
        for identity, op, geometry in geometries:
            for operand in geometry.operand_distributions:
                value = values[operand.value_id]
                for mapping in operand.mappings:
                    selected = distribution_ids.get(operand.required_distribution)
                    if selected is None:
                        raise MeshIrError("E_ABI_BOUNDS", "operand distribution is absent from sharding projection", op_id=op.op_id, operand_index=operand.operand_index)
                    shard_id, shard = shard_by_distribution_rank[(operand.required_distribution, mapping.required_shard.logical_rank)]
                    regions = operand_window_use_regions(op, operand.operand_index, value, mapping, geometry)
                    if len(regions) != len(mapping.window_uses):
                        raise MeshIrError("E_ABI_ORDER", "operand window region projection count differs", op_id=op.op_id, operand_index=operand.operand_index)
                    for use_index, (use, region) in enumerate(zip(mapping.window_uses, regions)):
                        if type(use) is not WindowUseGeometry or use.window_id not in object_by_window:
                            raise MeshIrError("E_ABI_BOUNDS", "operand use references an absent resident window", op_id=op.op_id, operand_index=operand.operand_index)
                        window = window_by_id[use.window_id]
                        shard_origin = tuple(origin - base for origin, base in zip(region.origin, shard.global_origin))
                        retained_backing = retained_by_window.get(use.window_id)
                        if any(item < 0 for item in shard_origin) or len(region.shape) != len(shard.padded_shape):
                            raise MeshIrError("E_EXPORT_LAYOUT", "operand use does not fit its assigned shard", op_id=op.op_id, operand_index=operand.operand_index)
                        padded_shape = shard.padded_shape if retained_backing is not None else tuple(min(window_extent, shard_extent - origin) for window_extent, shard_extent, origin in zip(window.padded_shape, shard.padded_shape, shard_origin))
                        if any(item < 0 for item in padded_shape) or any(valid > padded for valid, padded in zip(region.shape, padded_shape)):
                            raise MeshIrError("E_EXPORT_LAYOUT", "operand use exceeds its assigned shard", op_id=op.op_id, operand_index=operand.operand_index)
                        logical_strides = tuple(item.evaluate({}) for item in value.strides)
                        element_offset = value.storage_offset
                        for origin, stride in zip(region.origin, logical_strides):
                            element_offset = checked_add_u64(element_offset, checked_mul_u64(origin, stride, "operand mapped offset"), "operand mapped offset")
                        physical_key = (use.window_id, use.compute_tile_ordinal, use.output_tile_index, use.phase, value.alias_root, region_byte_spans(element_offset, region.shape, logical_strides, value.dtype.byte_width))
                        logical_key = (physical_key, value.value_id, shard_id, shard_origin, region.shape)
                        has_transfer = any(transfer.operand_index == operand.operand_index and transfer.window_use == use for transfer in geometry.value_transfers)
                        content_key = (value.alias_root, value.dtype, element_offset, region.shape, logical_strides)
                        local_sources = tuple(source for source in mapping.sources if source.kind is RegionSourceKind.LOCAL)
                        source_pieces = tuple(
                            (source, piece)
                            for source in mapping.sources
                            for piece in project_mapped_region_pieces(value, region, source.mapped_region)
                        )
                        covered_elements = 0
                        covered_pieces = []
                        for _, piece in source_pieces:
                            for previous in covered_pieces:
                                if all(left < checked_add_u64(right, extent, "selected source bound") and right < checked_add_u64(left, left_extent, "selected source bound") for left, left_extent, right, extent in zip(piece.origin, piece.shape, previous.origin, previous.shape)):
                                    raise MeshIrError("E_TENSOR_NOT_RESIDENT", "selected sources overlap within one operand generation", op_id=op.op_id, operand_index=operand.operand_index, window_id=use.window_id)
                            covered_pieces.append(piece)
                            covered_elements = checked_add_u64(covered_elements, _product(piece.shape, "selected source elements"), "selected source elements")
                        if covered_elements != _product(region.shape, "selected use elements"):
                            raise MeshIrError("E_TENSOR_NOT_RESIDENT", "selected sources do not cover one operand generation", op_id=op.op_id, operand_index=operand.operand_index, window_id=use.window_id)
                        local_materializations = ()
                        if logical_key in logical_use_views:
                            access = logical_use_views[logical_key]
                        elif retained_backing is None and physical_key in physical_use_generations:
                            generation = physical_use_generations[physical_key]
                            object_strides, layout = _consumer_mapping(value, padded_shape, window.required_extent_bytes)
                            view = PlannedBufferView(len(views) + 1, object_by_window[use.window_id], tensor_id_by_value[value.value_id], shard_id, shard_origin, padded_shape, region.shape, 0, object_strides, layout, generation)
                            views.append(view)
                            access = PlannedBufferAccess(view.view_id, ElementRegion((0,) * len(region.shape), region.shape, (1,) * len(region.shape)))
                            logical_use_views[logical_key] = access
                        elif retained_backing is not None:
                            generation = 0
                            access = retained_access(retained_backing, value, operand.established_distribution, mapping.required_shard.owner_core, region, op.op_id, operand.operand_index)
                            physical_use_generations[physical_key] = generation
                            logical_use_views[logical_key] = access
                        elif not has_transfer and resident_content.get(use.window_id) == content_key:
                            generation = resident_generation[use.window_id]
                            object_strides, layout = _consumer_mapping(value, padded_shape, window.required_extent_bytes)
                            view = PlannedBufferView(len(views) + 1, object_by_window[use.window_id], tensor_id_by_value[value.value_id], shard_id, shard_origin, padded_shape, region.shape, 0, object_strides, layout, generation)
                            views.append(view)
                            access = PlannedBufferAccess(view.view_id, ElementRegion((0,) * len(region.shape), region.shape, (1,) * len(region.shape)))
                            physical_use_generations[physical_key] = generation
                            logical_use_views[logical_key] = access
                        elif has_transfer or local_sources:
                            generation = generations.get(use.window_id, -1) + 1
                            generations[use.window_id] = generation
                            object_id = object_by_window[use.window_id]
                            object_strides, layout = _consumer_mapping(value, padded_shape, window.required_extent_bytes)
                            view = PlannedBufferView(len(views) + 1, object_id, tensor_id_by_value[value.value_id], shard_id, shard_origin, padded_shape, region.shape, 0, object_strides, layout, generation)
                            views.append(view)
                            access = PlannedBufferAccess(view.view_id, ElementRegion((0,) * len(region.shape), region.shape, (1,) * len(region.shape)))
                            physical_use_generations[physical_key] = generation
                            resident_generation[use.window_id] = generation
                            resident_content[use.window_id] = content_key
                            logical_use_views[logical_key] = access
                            materializations = []
                            for source, piece in source_pieces:
                                if source.kind is not RegionSourceKind.LOCAL:
                                    continue
                                piece_strides = tuple(item.evaluate({}) for item in value.strides)
                                piece_offset = value.storage_offset
                                for origin, stride in zip(piece.origin, piece_strides):
                                    piece_offset = checked_add_u64(piece_offset, checked_mul_u64(origin, stride, "local materialization offset"), "local materialization offset")
                                piece_spans = region_byte_spans(piece_offset, piece.shape, piece_strides, value.dtype.byte_width)
                                candidates = tuple(
                                    backing
                                    for backing in retained_by_window.values()
                                    if backing.window.owner_core == source.source.core_id
                                    and backing.mapped_region.alias_root == value.alias_root
                                    and spans_contain(backing.mapped_region.byte_spans, piece_spans)
                                )
                                if len(candidates) != 1 or operand.established_distribution is None:
                                    raise MeshIrError("E_TENSOR_NOT_RESIDENT", "selected local source has no unique retained backing", op_id=op.op_id, operand_index=operand.operand_index, window_id=use.window_id)
                                source_access = retained_access(candidates[0], value, operand.established_distribution, source.source.core_id, piece, op.op_id, operand.operand_index)
                                destination_origin = tuple(base + item - parent for base, item, parent in zip(access.region.origin, piece.origin, region.origin))
                                destination_access = PlannedBufferAccess(access.view_id, ElementRegion(destination_origin, piece.shape, (1,) * len(piece.shape)))
                                materializations.append(PlannedAccessPair(source_access, destination_access))
                            local_materializations = tuple(materializations)
                        else:
                            raise MeshIrError("E_TENSOR_NOT_RESIDENT", "selected cache hit has no resident generation", op_id=op.op_id, operand_index=operand.operand_index, window_id=use.window_id)
                        window_uses.append(PlannedWindowUse(identity, operand.operand_index, mapping.required_shard.logical_rank, use_index, use.compute_tile_ordinal, use.output_tile_index, use.phase, access, local_materializations))
        tiles_by_identity = {}
        for tile in tiling.tiles:
            tiles_by_identity.setdefault(tile.identity, []).append(tile)
        for identity, op, geometry in geometries:
            result_distribution = distribution_ids[geometry.result_distribution]
            output_owners = tuple(dict.fromkeys((item.output_tile_index, item.owner_core) for item in geometry.compute_tiles))
            if not output_owners:
                for transfer in geometry.value_transfers:
                    if transfer.kind is not PlacementTransferKind.OUTPUT_STORE:
                        continue
                    window_id = transfer.window_use.window_id
                    backing = retained_by_window.get(window_id)
                    shard = next((item for item in geometry.result_distribution.shards if item.owner_core == transfer.request_core), None)
                    if backing is None or shard is None or window_id not in object_by_window:
                        raise MeshIrError("E_ABI_BOUNDS", "metadata output has no selected retained backing", op_id=op.op_id, output_tile_index=transfer.window_use.output_tile_index)
                    mapped = transfer.region.mapped_region
                    if mapped.element_offset < backing.mapped_region.element_offset:
                        raise MeshIrError("E_EXPORT_LAYOUT", "metadata output begins before its retained backing", op_id=op.op_id, output_tile_index=transfer.window_use.output_tile_index)
                    object_offset = mapped.element_offset - backing.mapped_region.element_offset
                    object_record = objects[object_by_window[window_id] - 1]
                    if checked_mul_u64(_storage_elements(mapped.shape, mapped.strides, object_offset), mapped.dtype.byte_width, "metadata output bytes") > object_record.footprint_bytes:
                        raise MeshIrError("E_EXPORT_LAYOUT", "metadata output exceeds its retained backing", op_id=op.op_id, output_tile_index=transfer.window_use.output_tile_index)
                    shard_id = result_distribution.shard_ids[shard.logical_rank]
                    view = PlannedBufferView(len(views) + 1, object_by_window[window_id], tensor_id_by_value[transfer.value_id], shard_id, (0,) * len(mapped.shape), mapped.shape, mapped.shape, object_offset, mapped.strides, None, 0)
                    views.append(view)
                    access = PlannedBufferAccess(view.view_id, ElementRegion((0,) * len(mapped.shape), mapped.shape, (1,) * len(mapped.shape)))
                    result_views.append(PlannedResultView(identity, transfer.window_use.output_tile_index, transfer.request_core, ResidentWindowRole.SEMANTIC_RESULT, access, shard_id))
            for output_tile_index, owner_core in output_owners:
                output_tile = geometry.output_tiles[output_tile_index]
                shard = next((item for item in geometry.result_distribution.shards if item.owner_core == owner_core), None)
                if shard is None:
                    raise MeshIrError("E_PLACEMENT_INFEASIBLE", "result tile owner has no selected result shard", op_id=op.op_id, output_tile_index=output_tile_index)
                semantic_shard_id = result_distribution.shard_ids[shard.logical_rank]
                accumulator = accumulator_tensor_by_op.get(op.op_id)
                for role in (ResidentWindowRole.SEMANTIC_RESULT, ResidentWindowRole.ACCUMULATOR):
                    if role is ResidentWindowRole.ACCUMULATOR and accumulator is None:
                        continue
                    windows_for_role = tuple(item for item in geometry.resident_windows if item.role is role and item.owner_core == owner_core)
                    shard_id = accumulator[1][shard.logical_rank] if role is ResidentWindowRole.ACCUMULATOR else semantic_shard_id
                    if not _product(output_tile.valid_shape, "result tile elements"):
                        result_views.append(PlannedResultView(identity, output_tile_index, owner_core, role, None, shard_id))
                        continue
                    if len(windows_for_role) != 1:
                        raise MeshIrError("E_ABI_ORDER", "result tile has no unique selected resident window", op_id=op.op_id, output_tile_index=output_tile_index, role=role.value)
                    window = windows_for_role[0]
                    tensor_id = accumulator[0] if role is ResidentWindowRole.ACCUMULATOR else tensor_id_by_value[geometry.result_distribution.value_id]
                    relative = tuple(origin - base for origin, base in zip(output_tile.origin, shard.global_origin))
                    if any(item < 0 for item in relative):
                        raise MeshIrError("E_EXPORT_LAYOUT", "result tile begins before its assigned shard", op_id=op.op_id, output_tile_index=output_tile_index)
                    padded = tuple(min(tile_extent, shard_extent - origin) for tile_extent, shard_extent, origin in zip(output_tile.padded_shape, shard.padded_shape, relative))
                    if any(valid > extent for valid, extent in zip(output_tile.valid_shape, padded)):
                        raise MeshIrError("E_EXPORT_LAYOUT", "result tile exceeds its assigned shard", op_id=op.op_id, output_tile_index=output_tile_index)
                    backing = retained_by_window.get(window.window_id)
                    if backing is not None:
                        value = values[geometry.result_distribution.value_id]
                        object_offset = 0
                        generation = 0
                        retained_key = (window.window_id, geometry.result_distribution.value_id, shard_id)
                        if retained_key not in retained_views:
                            object_record = objects[object_by_window[window.window_id] - 1]
                            mapped_strides, layout = _consumer_mapping(value, shard.padded_shape, object_record.footprint_bytes)
                            view = PlannedBufferView(len(views) + 1, object_by_window[window.window_id], tensor_id, shard_id, (0,) * len(shard.padded_shape), shard.padded_shape, shard.valid_shape, object_offset, mapped_strides, layout, generation)
                            views.append(view)
                            retained_views[retained_key] = view.view_id
                        view_id = retained_views[retained_key]
                        access = PlannedBufferAccess(view_id, ElementRegion(relative, output_tile.valid_shape, (1,) * len(relative)))
                    else:
                        object_strides = _row_major(padded)
                        object_offset = 0
                        generation = sum(1 for item in result_views if item.owner_core == owner_core and item.role is role and item.access is not None and views[item.access.view_id - 1].object_id == object_by_window[window.window_id])
                        view = PlannedBufferView(len(views) + 1, object_by_window[window.window_id], tensor_id, shard_id, relative, padded, output_tile.valid_shape, object_offset, object_strides, Layout.CONTIGUOUS_ROW_MAJOR, generation)
                        views.append(view)
                        access = PlannedBufferAccess(view.view_id, ElementRegion((0,) * len(padded), output_tile.valid_shape, (1,) * len(padded)))
                    result_views.append(PlannedResultView(identity, output_tile_index, owner_core, role, access, shard_id))
            uses_by_tile_operand = {
                (item.compute_tile_ordinal, item.operand_index): item.access
                for item in window_uses
                if item.identity == identity and item.compute_tile_ordinal is not None
            }
            result_by_key = {(item.output_tile_index, item.owner_core, item.role): item for item in result_views if item.identity == identity}
            for planned in tiles_by_identity.get(identity, ()):
                selected = planned.geometry
                empty = not checked_mul_u64(checked_mul_u64(selected.tile.valid_batch, selected.tile.valid_m, "compute tile output"), selected.tile.valid_n, "compute tile output")
                operand_count = 2 if selected.matrix_phase is not None else len(op.operands)
                operand_accesses = tuple(uses_by_tile_operand.get((selected.tile_ordinal, operand_index)) for operand_index in range(operand_count) if not empty)
                if not empty and (len(operand_accesses) != operand_count or any(item is None for item in operand_accesses)):
                    raise MeshIrError("E_ABI_BOUNDS", "compute tile operand views are incomplete", op_id=op.op_id, tile_ordinal=selected.tile_ordinal)
                role = ResidentWindowRole.ACCUMULATOR if selected.matrix_phase is not None and selected.matrix_phase.name != "DIRECT" else ResidentWindowRole.SEMANTIC_RESULT
                result_use = result_by_key.get((selected.output_tile_index, selected.owner_core, role))
                if result_use is None:
                    raise MeshIrError("E_ABI_BOUNDS", "compute tile result assignment is absent", op_id=op.op_id, tile_ordinal=selected.tile_ordinal)
                tile_buffers.append(PlannedTileBuffers(planned.tile_id, operand_accesses, result_use.access, result_use.shard_id))
        variants.append(VariantBuffers(graph.entrypoint, graph.profile_id, tuple(tensors), computations, tuple(tensor_lineage), computation_lineage, placements, tuple(shards), tuple(partial_sums), tuple(objects), tuple(views), tuple(window_uses), tuple(result_views), tuple(tile_buffers)))
    if decision_index != len(tiling.shards.decisions.placements):
        raise MeshIrError("E_ABI_ORDER", "bufferization placement decisions contain absent operations")
    return BufferizationState(tiling, tuple(variants))


__all__ = ["BufferizationState", "PlannedAccessPair", "PlannedBufferAccess", "PlannedBufferObject", "PlannedBufferView", "PlannedResultView", "PlannedTileBuffers", "PlannedWindowUse", "VariantBuffers", "bufferize_and_alias", "project_mapped_region_pieces"]
