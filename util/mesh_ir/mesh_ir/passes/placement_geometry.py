from __future__ import annotations

import itertools
from dataclasses import dataclass, replace
from enum import Enum, IntEnum

from mesh_ir.analysis.regions import ByteSpan, merge_spans, region_byte_spans
from mesh_ir.architecture import ArchManifest
from mesh_ir.canonical import checked_add_u64, checked_mul_u64
from mesh_ir.compile_config import Collectives, Tiling
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Access, DType, INVALID_CORE_ID, TensorRole
from mesh_ir.ir.graph_ir import GraphFunction, GraphOp, GraphValue, METADATA_VIEW_OPCODES, OpCode, ReduceAttrs
from mesh_ir.ir.kernel_ir import CollectiveAlgorithm, DistributionKind, KernelTile, MatrixPhase
from mesh_ir.passes.collective_geometry import CollectiveGeometry, CollectiveTransferGeometry, TiledLogicalRegion, collective_transfer_geometry


class ShardingStrategy(IntEnum):
    SINGLE = 1
    MATRIX_M = 2
    MATRIX_N = 3
    MATRIX_K_SUM = 4
    PRESERVE = 5
    OUTER_DIM = 6
    REPLICATE = 7


class ExternalBindingKind(str, Enum):
    SOURCE = "SOURCE"
    DESTINATION = "DESTINATION"


class RegionSourceKind(str, Enum):
    LOCAL = "LOCAL"
    PEER = "PEER"
    EXTERNAL = "EXTERNAL"


class TransferEndpointKind(str, Enum):
    CORE = "CORE"
    EXTERNAL_BINDING = "EXTERNAL_BINDING"


class PlacementTransferKind(str, Enum):
    EXTERNAL_LOAD = "EXTERNAL_LOAD"
    PEER_RESHARD = "PEER_RESHARD"
    OUTPUT_STORE = "OUTPUT_STORE"


class ResidentWindowRole(str, Enum):
    OPERAND = "OPERAND"
    BIAS = "BIAS"
    ACCUMULATOR = "ACCUMULATOR"
    SEMANTIC_RESULT = "SEMANTIC_RESULT"
    COLLECTIVE_RECEIVE = "COLLECTIVE_RECEIVE"


class GeometryPhase(IntEnum):
    PREFETCH = 1
    COMPUTE = 2
    COLLECTIVE = 3
    EPILOGUE = 4
    STORE = 5


@dataclass(frozen=True)
class DenseLogicalRegion:
    origin: tuple[int, ...]
    shape: tuple[int, ...]


@dataclass(frozen=True)
class RankShardGeometry:
    logical_rank: int
    owner_core: int
    global_origin: tuple[int, ...]
    padded_shape: tuple[int, ...]
    valid_shape: tuple[int, ...]


@dataclass(frozen=True)
class ValueDistribution:
    value_id: int
    distribution: DistributionKind
    partition_axis: int | None
    core_ids: tuple[int, ...]
    shards: tuple[RankShardGeometry, ...]


@dataclass(frozen=True)
class TransferEndpoint:
    kind: TransferEndpointKind
    core_id: int

    def __post_init__(self) -> None:
        valid = self.kind is TransferEndpointKind.EXTERNAL_BINDING and self.core_id == INVALID_CORE_ID
        valid = valid or self.kind is TransferEndpointKind.CORE and type(self.core_id) is int and 0 <= self.core_id < INVALID_CORE_ID
        if type(self.kind) is not TransferEndpointKind or not valid:
            raise MeshIrError("E_CONFIG", "placement transfer endpoint is invalid")


@dataclass(frozen=True)
class MappedStorageRegion:
    value_id: int
    alias_root: int
    dtype: DType
    element_offset: int
    shape: tuple[int, ...]
    strides: tuple[int, ...]

    @property
    def byte_spans(self) -> tuple[ByteSpan, ...]:
        return region_byte_spans(self.element_offset, self.shape, self.strides, self.dtype.byte_width)

    @property
    def useful_bytes(self) -> int:
        total = 0
        for span in self.byte_spans:
            total = checked_add_u64(total, span.end - span.begin, "mapped storage bytes")
        return total


@dataclass(frozen=True)
class ValueTransferRegion:
    mapped_region: MappedStorageRegion


@dataclass(frozen=True)
class WindowUseGeometry:
    operation_ordinal: int
    compute_tile_ordinal: int | None
    output_tile_index: int
    window_id: int
    phase: GeometryPhase

    def __post_init__(self) -> None:
        if type(self.operation_ordinal) is not int or self.operation_ordinal < 0:
            raise MeshIrError("E_CONFIG", "window use operation identity is invalid")
        if self.compute_tile_ordinal is not None and (type(self.compute_tile_ordinal) is not int or self.compute_tile_ordinal < 0):
            raise MeshIrError("E_CONFIG", "window use compute tile identity is invalid")
        if type(self.output_tile_index) is not int or self.output_tile_index < 0 or type(self.window_id) is not int or self.window_id < 1 or type(self.phase) is not GeometryPhase:
            raise MeshIrError("E_CONFIG", "window use fields are invalid")
        if (self.compute_tile_ordinal is None) == (self.phase is GeometryPhase.COMPUTE):
            raise MeshIrError("E_CONFIG", "window use compute identity and phase disagree")


@dataclass(frozen=True)
class ProspectiveTransfer:
    kind: PlacementTransferKind
    value_id: int
    operand_index: int | None
    request_core: int
    source: TransferEndpoint
    destination: TransferEndpoint
    region: ValueTransferRegion
    window_use: WindowUseGeometry

    def __post_init__(self) -> None:
        if type(self.kind) is not PlacementTransferKind or type(self.value_id) is not int or self.value_id < 1:
            raise MeshIrError("E_CONFIG", "placement transfer identity is invalid")
        if self.operand_index is not None and (type(self.operand_index) is not int or self.operand_index < 0):
            raise MeshIrError("E_CONFIG", "placement transfer operand identity is invalid")
        if type(self.request_core) is not int or not 0 <= self.request_core < INVALID_CORE_ID or type(self.source) is not TransferEndpoint or type(self.destination) is not TransferEndpoint or type(self.region) is not ValueTransferRegion or type(self.window_use) is not WindowUseGeometry:
            raise MeshIrError("E_CONFIG", "placement transfer fields are invalid")
        if self.kind is PlacementTransferKind.OUTPUT_STORE and (self.operand_index is not None or self.window_use.phase is not GeometryPhase.STORE):
            raise MeshIrError("E_CONFIG", "output store transfer is invalid")
        if self.kind is not PlacementTransferKind.OUTPUT_STORE and (self.operand_index is None or self.window_use.phase is GeometryPhase.STORE):
            raise MeshIrError("E_CONFIG", "operand transfer is invalid")
        expected_endpoints = {
            PlacementTransferKind.EXTERNAL_LOAD: (TransferEndpointKind.EXTERNAL_BINDING, TransferEndpointKind.CORE),
            PlacementTransferKind.PEER_RESHARD: (TransferEndpointKind.CORE, TransferEndpointKind.CORE),
            PlacementTransferKind.OUTPUT_STORE: (TransferEndpointKind.CORE, TransferEndpointKind.EXTERNAL_BINDING),
        }[self.kind]
        if (self.source.kind, self.destination.kind) != expected_endpoints:
            raise MeshIrError("E_CONFIG", "placement transfer endpoints disagree with transfer kind")
        if self.request_core != (self.source.core_id if self.source.kind is TransferEndpointKind.CORE else self.destination.core_id):
            raise MeshIrError("E_CONFIG", "placement transfer request core is invalid")

    @property
    def useful_bytes(self) -> int:
        return self.region.mapped_region.useful_bytes


@dataclass(frozen=True)
class RegionSource:
    kind: RegionSourceKind
    source: TransferEndpoint
    mapped_region: MappedStorageRegion
    transfer_indices: tuple[int, ...]

    def __post_init__(self) -> None:
        if type(self.kind) is not RegionSourceKind or type(self.source) is not TransferEndpoint or type(self.mapped_region) is not MappedStorageRegion or type(self.transfer_indices) is not tuple:
            raise MeshIrError("E_CONFIG", "operand region source fields are invalid")
        if any(type(item) is not int or item < 0 for item in self.transfer_indices) or len(set(self.transfer_indices)) != len(self.transfer_indices):
            raise MeshIrError("E_CONFIG", "operand region source transfer identities are invalid")


@dataclass(frozen=True)
class OperandRegionMapping:
    required_shard: RankShardGeometry
    required_region: DenseLogicalRegion
    sources: tuple[RegionSource, ...]
    window_uses: tuple[WindowUseGeometry, ...]

    def __post_init__(self) -> None:
        if type(self.required_shard) is not RankShardGeometry or type(self.required_region) is not DenseLogicalRegion or type(self.sources) is not tuple or type(self.window_uses) is not tuple:
            raise MeshIrError("E_CONFIG", "operand region mapping fields are invalid")
        if any(type(item) is not RegionSource for item in self.sources) or any(type(item) is not WindowUseGeometry for item in self.window_uses):
            raise MeshIrError("E_CONFIG", "operand region mapping collections are invalid")


@dataclass(frozen=True)
class OperandDistribution:
    operand_index: int
    value_id: int
    established_distribution: ValueDistribution | None
    required_distribution: ValueDistribution
    mappings: tuple[OperandRegionMapping, ...]


@dataclass(frozen=True)
class OperandWindowBinding:
    operand_index: int
    logical_rank: int
    window_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        if type(self.operand_index) is not int or self.operand_index < 0 or type(self.logical_rank) is not int or self.logical_rank < 0 or type(self.window_ids) is not tuple:
            raise MeshIrError("E_CONFIG", "operand window binding fields are invalid")
        if any(type(item) is not int or item < 1 for item in self.window_ids) or len(set(self.window_ids)) != len(self.window_ids):
            raise MeshIrError("E_CONFIG", "operand window binding identities are invalid")


@dataclass(frozen=True)
class ComputeTileGeometry:
    tile_ordinal: int
    owner_core: int
    output_tile_index: int
    tile: KernelTile
    matrix_phase: MatrixPhase | None

    def __post_init__(self) -> None:
        if type(self.tile_ordinal) is not int or self.tile_ordinal < 0 or type(self.owner_core) is not int or not 0 <= self.owner_core < INVALID_CORE_ID:
            raise MeshIrError("E_CONFIG", "compute tile identity is invalid")
        if type(self.output_tile_index) is not int or self.output_tile_index < 0 or type(self.tile) is not KernelTile:
            raise MeshIrError("E_CONFIG", "compute tile fields are invalid")
        if self.matrix_phase is not None and type(self.matrix_phase) is not MatrixPhase:
            raise MeshIrError("E_CONFIG", "compute tile matrix phase is invalid")
        fields = tuple(getattr(self.tile, name) for name in ("batch_origin", "m_origin", "n_origin", "k_origin", "batch_extent", "m_extent", "n_extent", "k_extent", "valid_batch", "valid_m", "valid_n", "valid_k"))
        if any(type(item) is not int or item < 0 for item in fields):
            raise MeshIrError("E_CONFIG", "compute tile extents are invalid")


@dataclass(frozen=True, order=True)
class GeometryEvent:
    operation_ordinal: int
    tile_ordinal: int
    phase: GeometryPhase


@dataclass(frozen=True)
class ResidentWindow:
    window_id: int
    owner_core: int
    value_id: int
    role: ResidentWindowRole
    padded_shape: tuple[int, ...]
    dtype: DType
    dma_visible: bool
    required_extent_bytes: int
    required_alignment_bytes: int
    buffer_index: int
    first_event: GeometryEvent
    last_event: GeometryEvent


@dataclass(frozen=True)
class RetainedBacking:
    window: ResidentWindow
    mapped_region: MappedStorageRegion
    retain_through_operation: int


@dataclass(frozen=True)
class ExternalBinding:
    value_id: int
    kind: ExternalBindingKind
    access: Access


@dataclass(frozen=True)
class PlacementLedger:
    next_operation_ordinal: int
    next_window_id: int
    operation_ids: tuple[int, ...]
    external_bindings: tuple[ExternalBinding, ...]
    value_distributions: tuple[ValueDistribution, ...]
    retained_backings: tuple[RetainedBacking, ...]
    last_use_by_alias_root: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class PlacementRejection:
    code: str
    message: str


@dataclass(frozen=True)
class OperationPlacementGeometry:
    operation_ordinal: int
    operand_distributions: tuple[OperandDistribution, ...]
    operand_windows: tuple[OperandWindowBinding, ...]
    result_distribution: ValueDistribution
    output_tiles: tuple[TiledLogicalRegion, ...]
    compute_tiles: tuple[ComputeTileGeometry, ...]
    collectives: tuple[CollectiveGeometry, ...]
    value_transfers: tuple[ProspectiveTransfer, ...]
    resident_windows: tuple[ResidentWindow, ...]
    retained_results: tuple[RetainedBacking, ...]

    def __post_init__(self) -> None:
        collections = (
            (self.operand_distributions, OperandDistribution),
            (self.operand_windows, OperandWindowBinding),
            (self.output_tiles, TiledLogicalRegion),
            (self.compute_tiles, ComputeTileGeometry),
            (self.collectives, CollectiveGeometry),
            (self.value_transfers, ProspectiveTransfer),
            (self.resident_windows, ResidentWindow),
            (self.retained_results, RetainedBacking),
        )
        if type(self.operation_ordinal) is not int or self.operation_ordinal < 0 or type(self.result_distribution) is not ValueDistribution:
            raise MeshIrError("E_CONFIG", "operation placement geometry fields are invalid")
        if any(type(items) is not tuple or any(type(item) is not expected for item in items) for items, expected in collections):
            raise MeshIrError("E_CONFIG", "operation placement geometry collections are invalid")
        expected_bindings = tuple((operand.operand_index, mapping.required_shard.logical_rank) for operand in self.operand_distributions for mapping in operand.mappings)
        actual_bindings = tuple((item.operand_index, item.logical_rank) for item in self.operand_windows)
        if actual_bindings != expected_bindings:
            raise MeshIrError("E_ABI_ORDER", "operand window bindings do not match operand mappings")
        if tuple(item.tile_ordinal for item in self.compute_tiles) != tuple(range(len(self.compute_tiles))):
            raise MeshIrError("E_ABI_ORDER", "compute tile identities are not dense")
        if any(item.output_tile_index >= len(self.output_tiles) for item in self.compute_tiles):
            raise MeshIrError("E_ABI_ORDER", "compute tile references an absent output tile")
        bindings = {(item.operand_index, item.logical_rank): item.window_ids for item in self.operand_windows}
        for operand in self.operand_distributions:
            for mapping in operand.mappings:
                binding_ids = bindings[(operand.operand_index, mapping.required_shard.logical_rank)]
                for use in mapping.window_uses:
                    if use.operation_ordinal != self.operation_ordinal or use.output_tile_index >= len(self.output_tiles) or use.window_id not in binding_ids:
                        raise MeshIrError("E_ABI_ORDER", "operand window use is not selected by placement")
                    if use.compute_tile_ordinal is not None:
                        if use.compute_tile_ordinal >= len(self.compute_tiles):
                            raise MeshIrError("E_ABI_ORDER", "operand window use references an absent compute tile")
                        tile = self.compute_tiles[use.compute_tile_ordinal]
                        if tile.output_tile_index != use.output_tile_index or tile.owner_core != mapping.required_shard.owner_core:
                            raise MeshIrError("E_ABI_ORDER", "operand window use differs from its compute tile")
        for transfer in self.value_transfers:
            if transfer.window_use.operation_ordinal != self.operation_ordinal or transfer.window_use.output_tile_index >= len(self.output_tiles) or not transfer.useful_bytes:
                raise MeshIrError("E_ABI_ORDER", "placement transfer window use is invalid")
            if transfer.operand_index is not None:
                matches = tuple(
                    mapping
                    for operand in self.operand_distributions
                    if operand.operand_index == transfer.operand_index
                    for mapping in operand.mappings
                    if transfer.window_use in mapping.window_uses
                )
                if not matches or any(mapping.required_shard.owner_core != transfer.destination.core_id for mapping in matches):
                    raise MeshIrError("E_ABI_ORDER", "operand transfer is not associated with its selected window use")
            else:
                result_window = next((item for item in self.resident_windows if item.window_id == transfer.window_use.window_id), None)
                retained_alias = tuple(
                    mapping
                    for operand in self.operand_distributions
                    for mapping in operand.mappings
                    if transfer.window_use.window_id in bindings[(operand.operand_index, mapping.required_shard.logical_rank)]
                    and mapping.required_shard.owner_core == transfer.request_core
                    and any(
                        source.kind is RegionSourceKind.LOCAL
                        and source.source.core_id == transfer.request_core
                        and source.mapped_region.alias_root == transfer.region.mapped_region.alias_root
                        and not _subtract(transfer.region.mapped_region.byte_spans, source.mapped_region.byte_spans)
                        for source in mapping.sources
                    )
                )
                if (result_window is None or result_window.owner_core != transfer.request_core) and not retained_alias:
                    raise MeshIrError("E_ABI_ORDER", "output transfer is not associated with its selected result window")
        for operand in self.operand_distributions:
            for mapping in operand.mappings:
                for source in mapping.sources:
                    for index in source.transfer_indices:
                        if index >= len(self.value_transfers):
                            raise MeshIrError("E_ABI_ORDER", "operand source references an absent transfer")
                        transfer = self.value_transfers[index]
                        if transfer.kind is PlacementTransferKind.OUTPUT_STORE or transfer.source != source.source or transfer.window_use not in mapping.window_uses or not _intersections(transfer.region.mapped_region.byte_spans, source.mapped_region.byte_spans):
                            raise MeshIrError("E_ABI_ORDER", "operand source references an invalid transfer")

    @property
    def collective_transfers(self) -> tuple[CollectiveTransferGeometry, ...]:
        return tuple(transfer for collective in self.collectives for transfer in collective.transfers)


@dataclass(frozen=True)
class GeometryEstimate:
    communication_bytes: int
    peak_sram_bytes: int
    manhattan_hop_bytes_lower_bound: int


_MATRIX = frozenset((OpCode.MATMUL, OpCode.BMM, OpCode.LINEAR_BIAS))
_SOURCE_ROLES = frozenset((TensorRole.INPUT, TensorRole.WEIGHT, TensorRole.CONSTANT, TensorRole.STATE, TensorRole.KV_CACHE))


def _shape(value: GraphValue) -> tuple[int, ...]:
    result = tuple(item.value for item in value.shape)
    if len(result) != len(value.shape):
        raise MeshIrError("E_SHAPE_UNBOUND", "placement requires concrete Graph shapes", value_id=value.value_id)
    return result


def _strides(value: GraphValue) -> tuple[int, ...]:
    return tuple(item.evaluate({}) for item in value.strides)


def _product(values: tuple[int, ...], field: str) -> int:
    result = 1
    for value in values:
        result = checked_mul_u64(result, value, field)
    return result


def _align_up(value: int, alignment: int) -> int:
    if value == 0:
        return 0
    return checked_mul_u64((checked_add_u64(value, alignment - 1, "placement alignment") // alignment), alignment, "placement aligned extent")


def _mapped(value: GraphValue, region: DenseLogicalRegion) -> MappedStorageRegion:
    strides = _strides(value)
    offset = value.storage_offset
    for origin, stride in zip(region.origin, strides):
        offset = checked_add_u64(offset, checked_mul_u64(origin, stride, "mapped storage offset"), "mapped storage offset")
    return MappedStorageRegion(value.value_id, value.alias_root, value.dtype, offset, region.shape, strides)


def _mapped_span(value: GraphValue, span: ByteSpan) -> MappedStorageRegion:
    width = value.dtype.byte_width
    if span.begin % width or span.end % width:
        raise MeshIrError("E_EXPORT_LAYOUT", "mapped storage span is not dtype aligned", value_id=value.value_id)
    return MappedStorageRegion(value.value_id, value.alias_root, value.dtype, span.begin // width, ((span.end - span.begin) // width,), (1,))


def _mapped_key(mapped: MappedStorageRegion) -> tuple[object, ...]:
    return mapped.alias_root, mapped.dtype, mapped.element_offset, mapped.shape, mapped.strides


def _intersections(required: tuple[ByteSpan, ...], available: tuple[ByteSpan, ...]) -> tuple[ByteSpan, ...]:
    return merge_spans(tuple(ByteSpan(max(left.begin, right.begin), min(left.end, right.end)) for left in required for right in available if max(left.begin, right.begin) < min(left.end, right.end)))


def _subtract(required: tuple[ByteSpan, ...], available: tuple[ByteSpan, ...]) -> tuple[ByteSpan, ...]:
    result = []
    for span in required:
        cursor = span.begin
        for candidate in available:
            if candidate.end <= cursor:
                continue
            if candidate.begin >= span.end:
                break
            if candidate.begin > cursor:
                result.append(ByteSpan(cursor, min(candidate.begin, span.end)))
            cursor = max(cursor, candidate.end)
            if cursor >= span.end:
                break
        if cursor < span.end:
            result.append(ByteSpan(cursor, span.end))
    return tuple(result)


def _distribution(value: GraphValue, core_ids: tuple[int, ...], distribution: DistributionKind, axis: int | None) -> ValueDistribution:
    shape = _shape(value)
    if distribution is DistributionKind.REPLICATED:
        shards = tuple(RankShardGeometry(rank, core, (0,) * len(shape), shape, shape) for rank, core in enumerate(core_ids))
        return ValueDistribution(value.value_id, distribution, None, core_ids, shards)
    if axis is None:
        if len(core_ids) != 1:
            raise MeshIrError("E_CONFIG", "partitioned placement requires an axis")
        shards = (RankShardGeometry(0, core_ids[0], (0,) * len(shape), shape, shape),)
        return ValueDistribution(value.value_id, DistributionKind.PARTITIONED, None, core_ids, shards)
    normalized = axis if axis >= 0 else axis + len(shape)
    if not 0 <= normalized < len(shape):
        raise MeshIrError("E_EXPORT_LAYOUT", "placement partition axis is invalid", value_id=value.value_id, axis=axis)
    extent = shape[normalized]
    padded = 0 if extent == 0 else (extent + len(core_ids) - 1) // len(core_ids)
    shards = []
    for rank, core in enumerate(core_ids):
        origin_value = min(checked_mul_u64(rank, padded, "partition origin"), extent)
        valid_value = min(padded, extent - origin_value)
        origin = [0] * len(shape)
        local = list(shape)
        valid = list(shape)
        origin[normalized] = origin_value
        local[normalized] = padded
        valid[normalized] = valid_value
        shards.append(RankShardGeometry(rank, core, tuple(origin), tuple(local), tuple(valid)))
    return ValueDistribution(value.value_id, distribution, normalized, core_ids, tuple(shards))


def _distribution_signature(distribution: ValueDistribution) -> tuple[object, ...]:
    return (
        distribution.distribution,
        distribution.partition_axis,
        distribution.core_ids,
        tuple((item.logical_rank, item.owner_core, item.global_origin, item.padded_shape, item.valid_shape) for item in distribution.shards),
    )


def _view_distribution(op: GraphOp, operand: GraphValue, source: ValueDistribution, result: GraphValue) -> ValueDistribution | PlacementRejection:
    if source.distribution is DistributionKind.REPLICATED:
        return _distribution(result, source.core_ids, DistributionKind.REPLICATED, None)
    if source.partition_axis is None:
        return _distribution(result, source.core_ids, DistributionKind.PARTITIONED, None)
    axis = source.partition_axis
    if op.opcode in (OpCode.TRANSPOSE_VIEW, OpCode.PERMUTE_VIEW):
        permutation = op.attrs.permutation
        result_axis = permutation.index(axis)
        shards = tuple(
            RankShardGeometry(
                item.logical_rank,
                item.owner_core,
                tuple(item.global_origin[index] for index in permutation),
                tuple(item.padded_shape[index] for index in permutation),
                tuple(item.valid_shape[index] for index in permutation),
            )
            for item in source.shards
        )
        return ValueDistribution(result.value_id, DistributionKind.PARTITIONED, result_axis, source.core_ids, shards)
    if op.opcode is OpCode.SLICE_VIEW:
        if axis in op.attrs.axes:
            return PlacementRejection("E_PLACEMENT_INFEASIBLE", "slice view splits an established partition axis")
        result_shape = _shape(result)
        shards = []
        for item in source.shards:
            origin = [0] * len(result_shape)
            padded = list(result_shape)
            valid = list(result_shape)
            origin[axis] = item.global_origin[axis]
            padded[axis] = item.padded_shape[axis]
            valid[axis] = item.valid_shape[axis]
            shards.append(RankShardGeometry(item.logical_rank, item.owner_core, tuple(origin), tuple(padded), tuple(valid)))
        return ValueDistribution(result.value_id, DistributionKind.PARTITIONED, axis, source.core_ids, tuple(shards))
    if op.opcode is OpCode.EXPAND_VIEW:
        leading = len(result.shape) - len(source.shards[0].padded_shape)
        result_axis = leading + axis
        if result_axis in op.attrs.expanded_axes:
            return PlacementRejection("E_PLACEMENT_INFEASIBLE", "expand view expands an established partition axis")
        transformed = _distribution(result, source.core_ids, DistributionKind.PARTITIONED, result_axis)
        if tuple(item.valid_shape[result_axis] for item in transformed.shards) != tuple(item.valid_shape[axis] for item in source.shards):
            return PlacementRejection("E_PLACEMENT_INFEASIBLE", "expand view changes an established partition")
        return transformed
    if op.opcode is OpCode.RESHAPE_VIEW:
        for result_axis in range(len(result.shape)):
            transformed = _distribution(result, source.core_ids, DistributionKind.PARTITIONED, result_axis)
            compatible = all(
                source_shard.owner_core == result_shard.owner_core
                and _mapped(operand, DenseLogicalRegion(source_shard.global_origin, source_shard.valid_shape)).byte_spans
                == _mapped(result, DenseLogicalRegion(result_shard.global_origin, result_shard.valid_shape)).byte_spans
                for source_shard, result_shard in zip(source.shards, transformed.shards)
            )
            if compatible:
                return transformed
    return PlacementRejection("E_PLACEMENT_INFEASIBLE", "reshape view cannot prove a rectangular partition transform")


def create_placement_ledger(function: GraphFunction, values: tuple[GraphValue, ...]) -> PlacementLedger:
    if type(function) is not GraphFunction or type(values) is not tuple or any(type(item) is not GraphValue for item in values):
        raise MeshIrError("E_CONFIG", "placement ledger inputs have invalid record types")
    by_id = {item.value_id: item for item in values}
    produced = {result for op in function.ops for result in op.results}
    used = set(function.inputs) | set(function.outputs) | {value_id for op in function.ops for value_id in (*op.operands, *op.results)}
    sources = {}
    for value_id in sorted(used):
        value = by_id[value_id]
        root = by_id[value.alias_root]
        if root.value_id not in produced and root.role in _SOURCE_ROLES:
            sources[root.value_id] = ExternalBinding(root.value_id, ExternalBindingKind.SOURCE, root.access)
    destinations = tuple(ExternalBinding(value_id, ExternalBindingKind.DESTINATION, by_id[value_id].access) for value_id in function.outputs)
    last_use = {}
    for ordinal, op in enumerate(function.ops):
        for value_id in op.operands:
            root = by_id[value_id].alias_root
            last_use[root] = ordinal
    final_ordinal = max(0, len(function.ops) - 1)
    for value_id in function.outputs:
        root = by_id[value_id].alias_root
        last_use[root] = max(last_use.get(root, 0), final_ordinal)
    return PlacementLedger(0, 1, tuple(item.op_id for item in function.ops), tuple(sources.values()) + destinations, (), (), tuple(sorted(last_use.items())))


def _outer_axis(op: GraphOp, operands: tuple[GraphValue, ...], result: GraphValue) -> int | None:
    for axis in range(len(result.shape)):
        if _partition_axis_is_legal(op, axis):
            return axis
    return None


def _partition_axis_is_legal(op: GraphOp, axis: int) -> bool:
    excluded = set()
    if op.opcode is OpCode.SOFTMAX:
        excluded.add(op.attrs.axis)
    elif op.opcode in (OpCode.LAYERNORM, OpCode.RMSNORM):
        excluded.update(op.attrs.axes)
    elif op.opcode in (OpCode.CONCAT, OpCode.GATHER_ROWS):
        excluded.add(op.attrs.axis)
    elif op.opcode in (OpCode.REDUCE_SUM, OpCode.REDUCE_MAX, OpCode.REDUCE_MEAN) and op.attrs.keepdim:
        excluded.update(op.attrs.axes)
    return axis not in excluded


def _operand_axis(op: GraphOp, operand: GraphValue, result: GraphValue, result_axis: int) -> int | None:
    if op.opcode in (OpCode.REDUCE_SUM, OpCode.REDUCE_MAX, OpCode.REDUCE_MEAN):
        attrs: ReduceAttrs = op.attrs
        kept = tuple(axis for axis in range(len(operand.shape)) if axis not in attrs.axes)
        return result_axis if attrs.keepdim else kept[result_axis]
    if op.opcode is OpCode.EMBEDDING_LOOKUP:
        return None if operand.value_id == op.operands[0] else result_axis if result_axis < len(operand.shape) else None
    if op.opcode is OpCode.GATHER_ROWS and operand.value_id == op.operands[1]:
        return None
    aligned = result_axis - (len(result.shape) - len(operand.shape))
    if aligned < 0 or operand.shape[aligned].value == 1 and result.shape[result_axis].value != 1:
        return None
    return aligned


def _preserved_distributions(
    op: GraphOp,
    operands: tuple[GraphValue, ...],
    result: GraphValue,
    core_ids: tuple[int, ...],
    established: dict[int, ValueDistribution],
) -> tuple[tuple[ValueDistribution, ...], ValueDistribution] | PlacementRejection:
    anchor_index = 1 if op.opcode is OpCode.EMBEDDING_LOOKUP else 0
    anchor = established.get(operands[anchor_index].value_id)
    if anchor is None or anchor.core_ids != core_ids:
        return PlacementRejection("E_PLACEMENT_INFEASIBLE", "operation has no compatible distribution to preserve")
    if anchor.distribution is DistributionKind.REPLICATED:
        required = tuple(_distribution(item, core_ids, DistributionKind.REPLICATED, None) for item in operands)
        return required, _distribution(result, core_ids, DistributionKind.REPLICATED, None)
    if anchor.partition_axis is None:
        return PlacementRejection("E_PLACEMENT_INFEASIBLE", "multi-core preserve source has no partition axis")
    result_axis = next(
        (
            axis
            for axis in range(len(result.shape))
            if _partition_axis_is_legal(op, axis) and _operand_axis(op, operands[anchor_index], result, axis) == anchor.partition_axis
        ),
        None,
    )
    if result_axis is None:
        return PlacementRejection("E_PLACEMENT_INFEASIBLE", "source partition cannot project to the result")
    result_distribution = _distribution(result, core_ids, DistributionKind.PARTITIONED, result_axis)
    required = []
    for operand in operands:
        operand_axis = _operand_axis(op, operand, result, result_axis)
        required.append(_distribution(operand, core_ids, DistributionKind.REPLICATED, None) if operand_axis is None else _distribution(operand, core_ids, DistributionKind.PARTITIONED, operand_axis))
    if _distribution_signature(required[anchor_index]) != _distribution_signature(anchor):
        return PlacementRejection("E_PLACEMENT_INFEASIBLE", "source partition does not match the preserved result mapping")
    return tuple(required), result_distribution


def _required_distributions(
    op: GraphOp,
    operands: tuple[GraphValue, ...],
    result: GraphValue,
    strategy: ShardingStrategy,
    core_ids: tuple[int, ...],
    established: dict[int, ValueDistribution],
) -> tuple[tuple[ValueDistribution, ...], ValueDistribution] | PlacementRejection:
    if op.opcode in METADATA_VIEW_OPCODES:
        current = established.get(operands[0].value_id)
        if strategy is ShardingStrategy.SINGLE:
            required = _distribution(operands[0], core_ids, DistributionKind.PARTITIONED, None)
            if current is not None and _distribution_signature(current) != _distribution_signature(required):
                return PlacementRejection("E_PLACEMENT_INFEASIBLE", "view source is not resident on the selected core")
        elif strategy is ShardingStrategy.REPLICATE:
            required = _distribution(operands[0], core_ids, DistributionKind.REPLICATED, None)
            if current is not None and _distribution_signature(current) != _distribution_signature(required):
                return PlacementRejection("E_PLACEMENT_INFEASIBLE", "view cannot materialize a replicated source")
        elif strategy is ShardingStrategy.PRESERVE:
            if current is None or current.core_ids != core_ids:
                return PlacementRejection("E_PLACEMENT_INFEASIBLE", "view has no compatible source distribution to preserve")
            required = current
        else:
            return PlacementRejection("E_PLACEMENT_INFEASIBLE", "view strategy would require materialization")
        transformed = _view_distribution(op, operands[0], required, result)
        if type(transformed) is PlacementRejection:
            return transformed
        return (required,), transformed
    if strategy is ShardingStrategy.SINGLE:
        return tuple(_distribution(item, core_ids, DistributionKind.PARTITIONED, None) for item in operands), _distribution(result, core_ids, DistributionKind.PARTITIONED, None)
    if op.opcode in _MATRIX:
        if strategy not in (ShardingStrategy.MATRIX_M, ShardingStrategy.MATRIX_N, ShardingStrategy.MATRIX_K_SUM):
            return PlacementRejection("E_PLACEMENT_INFEASIBLE", "matrix placement strategy is invalid")
        lhs_axis = -1 if strategy is ShardingStrategy.MATRIX_K_SUM and not op.attrs.lhs_transpose else -2 if strategy is ShardingStrategy.MATRIX_K_SUM else -1 if op.attrs.lhs_transpose else -2
        rhs_axis = -2 if strategy is ShardingStrategy.MATRIX_K_SUM and not op.attrs.rhs_transpose else -1 if strategy is ShardingStrategy.MATRIX_K_SUM else -2 if op.attrs.rhs_transpose else -1
        if strategy is ShardingStrategy.MATRIX_M:
            required = [_distribution(operands[0], core_ids, DistributionKind.PARTITIONED, lhs_axis), _distribution(operands[1], core_ids, DistributionKind.REPLICATED, None)]
            if len(operands) == 3:
                required.append(_distribution(operands[2], core_ids, DistributionKind.REPLICATED, None))
            return tuple(required), _distribution(result, core_ids, DistributionKind.PARTITIONED, -2)
        if strategy is ShardingStrategy.MATRIX_N:
            required = [_distribution(operands[0], core_ids, DistributionKind.REPLICATED, None), _distribution(operands[1], core_ids, DistributionKind.PARTITIONED, rhs_axis)]
            if len(operands) == 3:
                required.append(_distribution(operands[2], core_ids, DistributionKind.PARTITIONED, -1))
            return tuple(required), _distribution(result, core_ids, DistributionKind.PARTITIONED, -1)
        required = [_distribution(operands[0], core_ids, DistributionKind.PARTITIONED, lhs_axis), _distribution(operands[1], core_ids, DistributionKind.PARTITIONED, rhs_axis)]
        if len(operands) == 3:
            required.append(_distribution(operands[2], core_ids, DistributionKind.REPLICATED, None))
        return tuple(required), _distribution(result, core_ids, DistributionKind.REPLICATED, None)
    if strategy is ShardingStrategy.REPLICATE:
        return tuple(_distribution(item, core_ids, DistributionKind.REPLICATED, None) for item in operands), _distribution(result, core_ids, DistributionKind.REPLICATED, None)
    if strategy is ShardingStrategy.PRESERVE:
        return _preserved_distributions(op, operands, result, core_ids, established)
    if strategy is not ShardingStrategy.OUTER_DIM:
        return PlacementRejection("E_PLACEMENT_INFEASIBLE", "nonmatrix placement strategy is invalid")
    axis = _outer_axis(op, operands, result)
    if axis is None:
        return PlacementRejection("E_PLACEMENT_INFEASIBLE", "operation has no legal outer partition axis")
    result_distribution = _distribution(result, core_ids, DistributionKind.PARTITIONED, axis)
    required = []
    for operand in operands:
        operand_axis = _operand_axis(op, operand, result, axis)
        required.append(_distribution(operand, core_ids, DistributionKind.REPLICATED, None) if operand_axis is None else _distribution(operand, core_ids, DistributionKind.PARTITIONED, operand_axis))
    return tuple(required), result_distribution


def _matrix_tiles(distribution: ValueDistribution, tiling: Tiling) -> tuple[TiledLogicalRegion, ...]:
    shards = distribution.shards[:1] if distribution.distribution is DistributionKind.REPLICATED else distribution.shards
    tiles = []
    for shard in shards:
        rank = len(shard.valid_shape)
        if rank < 2:
            raise MeshIrError("E_EXPORT_LAYOUT", "matrix result rank is invalid", value_id=distribution.value_id)
        leading_shape = shard.valid_shape[:-2]
        leading_origins = tuple(itertools.product(*(range(item) for item in leading_shape))) if all(leading_shape) else ()
        if not leading_shape:
            leading_origins = ((),)
        m_valid = shard.valid_shape[-2]
        n_valid = shard.valid_shape[-1]
        if leading_origins and m_valid and n_valid:
            for leading in leading_origins:
                for local_m in range(0, m_valid, tiling.gemm_m):
                    for local_n in range(0, n_valid, tiling.gemm_n):
                        origin = tuple(base + local for base, local in zip(shard.global_origin[:-2], leading)) + (shard.global_origin[-2] + local_m, shard.global_origin[-1] + local_n)
                        padded = (1,) * len(leading) + (min(tiling.gemm_m, shard.padded_shape[-2] - local_m), min(tiling.gemm_n, shard.padded_shape[-1] - local_n))
                        valid = (1,) * len(leading) + (min(tiling.gemm_m, m_valid - local_m), min(tiling.gemm_n, n_valid - local_n))
                        tiles.append(TiledLogicalRegion(origin, padded, valid))
        else:
            tiles.append(TiledLogicalRegion(shard.global_origin, tuple(0 if extent == 0 else min(extent, tiling.gemm_m if index == rank - 2 else tiling.gemm_n if index == rank - 1 else 1) for index, extent in enumerate(shard.padded_shape)), (0,) * rank))
    return tuple(tiles)


def _output_tiles(op: GraphOp, result_distribution: ValueDistribution, tiling: Tiling) -> tuple[TiledLogicalRegion, ...]:
    if op.opcode in _MATRIX:
        return _matrix_tiles(result_distribution, tiling)
    return tuple(TiledLogicalRegion(item.global_origin, item.padded_shape, item.valid_shape) for item in result_distribution.shards)


def _output_tile_owners(op: GraphOp, result_distribution: ValueDistribution, tiling: Tiling) -> tuple[int, ...]:
    if op.opcode not in _MATRIX:
        return tuple(item.owner_core for item in result_distribution.shards)
    shards = result_distribution.shards[:1] if result_distribution.distribution is DistributionKind.REPLICATED else result_distribution.shards
    owners = []
    for shard in shards:
        single = ValueDistribution(result_distribution.value_id, DistributionKind.PARTITIONED, result_distribution.partition_axis, (shard.owner_core,), (shard,))
        owners.extend((shard.owner_core,) * len(_matrix_tiles(single, tiling)))
    return tuple(owners)


def _flatten_origin(origin: tuple[int, ...], shape: tuple[int, ...]) -> int:
    result = 0
    for coordinate, extent in zip(origin, shape):
        result = checked_add_u64(checked_mul_u64(result, extent, "matrix batch origin"), coordinate, "matrix batch origin")
    return result


def _compute_tiles(
    op: GraphOp,
    operands: tuple[GraphValue, ...],
    result: GraphValue,
    operand_distributions: tuple[ValueDistribution, ...],
    result_distribution: ValueDistribution,
    output_tiles: tuple[TiledLogicalRegion, ...],
    strategy: ShardingStrategy,
    tiling: Tiling,
) -> tuple[ComputeTileGeometry, ...]:
    if op.opcode in METADATA_VIEW_OPCODES:
        return ()
    owners = _output_tile_owners(op, result_distribution, tiling)
    if len(owners) != len(output_tiles):
        raise MeshIrError("E_ABI_ORDER", "output tile ownership is incomplete", op_id=op.op_id)
    if op.opcode not in _MATRIX:
        return tuple(
            ComputeTileGeometry(
                index,
                owner,
                index,
                KernelTile(0, 0, 0, 0, 1, _product(tile.padded_shape, "nonmatrix tile elements"), 1, 1, 1, _product(tile.valid_shape, "nonmatrix valid elements"), 1, 1),
                None,
            )
            for index, (tile, owner) in enumerate(zip(output_tiles, owners))
        )
    lhs_shape = list(_shape(operands[0]))
    if op.attrs.lhs_transpose:
        lhs_shape[-2], lhs_shape[-1] = lhs_shape[-1], lhs_shape[-2]
    full_k = lhs_shape[-1]
    leading_shape = _shape(result)[:-2]
    lhs_distribution = operand_distributions[0]
    placements = tuple((output_index, output_tile, shard.owner_core) for shard in result_distribution.shards for output_index, output_tile in enumerate(output_tiles)) if strategy is ShardingStrategy.MATRIX_K_SUM else tuple((index, tile, owner) for index, (tile, owner) in enumerate(zip(output_tiles, owners)))
    result_tiles = []
    for output_index, output_tile, owner in placements:
        lhs_shard = next(item for item in lhs_distribution.shards if item.owner_core == owner)
        if strategy is ShardingStrategy.MATRIX_K_SUM:
            k_axis = -1 if not op.attrs.lhs_transpose else -2
            k_begin = lhs_shard.global_origin[k_axis]
            k_total = lhs_shard.valid_shape[k_axis]
        else:
            k_begin = 0
            k_total = full_k
        batch_valid = _product(output_tile.valid_shape[:-2], "matrix valid batch")
        valid_m = output_tile.valid_shape[-2]
        valid_n = output_tile.valid_shape[-1]
        batch_origin = _flatten_origin(output_tile.origin[:-2], leading_shape)
        k_tiles = max(1, (k_total + tiling.gemm_k - 1) // tiling.gemm_k)
        epilogue_math = op.opcode is OpCode.LINEAR_BIAS or op.attrs.alpha != 1.0
        needs_accumulation = strategy is ShardingStrategy.MATRIX_K_SUM or k_tiles > 1 or result.dtype is not op.attrs.accum_dtype or epilogue_math
        if not batch_valid or not valid_m or not valid_n:
            phase = MatrixPhase.ACCUMULATE_ONLY if needs_accumulation else MatrixPhase.DIRECT
            tile = KernelTile(0, output_tile.origin[-2], output_tile.origin[-1], k_begin, 0, tiling.gemm_m, tiling.gemm_n, tiling.gemm_k, 0, 0, 0, 0)
            result_tiles.append(ComputeTileGeometry(len(result_tiles), owner, output_index, tile, phase))
            continue
        k_origins = tuple(range(0, k_total, tiling.gemm_k)) or (0,)
        for k_index, local_k in enumerate(k_origins):
            valid_k = min(tiling.gemm_k, k_total - local_k) if k_total else 0
            if not needs_accumulation:
                phase = MatrixPhase.DIRECT
            elif k_tiles == 1:
                phase = MatrixPhase.ACCUMULATE_ONLY
            elif k_index == 0:
                phase = MatrixPhase.ACCUMULATE_FIRST
            elif k_index + 1 == k_tiles:
                phase = MatrixPhase.ACCUMULATE_FINAL
            else:
                phase = MatrixPhase.ACCUMULATE_CONTINUE
            tile = KernelTile(batch_origin, output_tile.origin[-2], output_tile.origin[-1], k_begin + local_k, 1, tiling.gemm_m, tiling.gemm_n, tiling.gemm_k, 1, valid_m, valid_n, valid_k)
            result_tiles.append(ComputeTileGeometry(len(result_tiles), owner, output_index, tile, phase))
    return tuple(result_tiles)


def _broadcast_shape_region(shape: tuple[int, ...], output_region: DenseLogicalRegion) -> DenseLogicalRegion:
    if len(shape) > len(output_region.shape):
        raise MeshIrError("E_EXPORT_SHAPE", "operand rank exceeds result rank")
    offset = len(output_region.shape) - len(shape)
    origin = []
    extents = []
    for axis, extent in enumerate(shape):
        output_axis = offset + axis
        if extent == 1:
            origin.append(0)
            extents.append(1)
        else:
            origin.append(output_region.origin[output_axis])
            extents.append(output_region.shape[output_axis])
    return DenseLogicalRegion(tuple(origin), tuple(extents))


def _broadcast_region(value: GraphValue, output_region: DenseLogicalRegion) -> DenseLogicalRegion:
    return _broadcast_shape_region(_shape(value), output_region)


def _matrix_operand_region(
    op: GraphOp,
    operand_index: int,
    operand: GraphValue,
    compute: ComputeTileGeometry,
    output_tile: TiledLogicalRegion,
) -> DenseLogicalRegion:
    if operand_index == 2:
        return _broadcast_region(operand, DenseLogicalRegion(output_tile.origin, output_tile.valid_shape))
    shape = list(_shape(operand))
    transpose = op.attrs.lhs_transpose if operand_index == 0 else op.attrs.rhs_transpose
    if transpose:
        shape[-2], shape[-1] = shape[-1], shape[-2]
    output_batch = DenseLogicalRegion(output_tile.origin[:-2], output_tile.valid_shape[:-2])
    batch_region = _broadcast_shape_region(tuple(shape[:-2]), output_batch)
    tile = compute.tile
    if operand_index == 0:
        origin = batch_region.origin + (tile.m_origin, tile.k_origin)
        extents = batch_region.shape + (tile.valid_m, tile.valid_k)
    else:
        origin = batch_region.origin + (tile.k_origin, tile.n_origin)
        extents = batch_region.shape + (tile.valid_k, tile.valid_n)
    if transpose:
        origin = origin[:-2] + (origin[-1], origin[-2])
        extents = extents[:-2] + (extents[-1], extents[-2])
    return DenseLogicalRegion(origin, extents)


def _source_binding(ledger: PlacementLedger, value: GraphValue) -> ExternalBinding | None:
    return next((item for item in ledger.external_bindings if item.kind is ExternalBindingKind.SOURCE and item.value_id == value.alias_root and item.access in (Access.READ_ONLY, Access.READ_WRITE)), None)


def _cover_operand(
    operand_index: int,
    value: GraphValue,
    required: ValueDistribution,
    ledger: PlacementLedger,
) -> tuple[OperandRegionMapping, ...] | PlacementRejection:
    mappings = []
    for shard in required.shards:
        logical = DenseLogicalRegion(shard.global_origin, shard.valid_shape)
        mapped = _mapped(value, logical)
        remaining = mapped.byte_spans
        sources = []
        local = tuple(item for item in ledger.retained_backings if item.mapped_region.alias_root == value.alias_root and item.window.owner_core == shard.owner_core)
        local_spans = merge_spans(tuple(span for item in local for span in item.mapped_region.byte_spans))
        for span in _intersections(remaining, local_spans):
            sources.append(RegionSource(RegionSourceKind.LOCAL, TransferEndpoint(TransferEndpointKind.CORE, shard.owner_core), _mapped_span(value, span), ()))
        remaining = _subtract(remaining, local_spans)
        peers = tuple(sorted((item for item in ledger.retained_backings if item.mapped_region.alias_root == value.alias_root and item.window.owner_core != shard.owner_core), key=lambda item: (item.window.owner_core, item.window.window_id)))
        for peer in peers:
            intersections = _intersections(remaining, peer.mapped_region.byte_spans)
            for span in intersections:
                piece = _mapped_span(value, span)
                sources.append(RegionSource(RegionSourceKind.PEER, TransferEndpoint(TransferEndpointKind.CORE, peer.window.owner_core), piece, ()))
            remaining = _subtract(remaining, peer.mapped_region.byte_spans)
        binding = _source_binding(ledger, value)
        if remaining and binding is None:
            return PlacementRejection("E_TENSOR_NOT_RESIDENT", "internal operand has no retained or external source")
        for span in remaining:
            piece = _mapped_span(value, span)
            sources.append(RegionSource(RegionSourceKind.EXTERNAL, TransferEndpoint(TransferEndpointKind.EXTERNAL_BINDING, INVALID_CORE_ID), piece, ()))
        mappings.append(OperandRegionMapping(shard, logical, tuple(sources), ()))
    return tuple(mappings)


def _metadata_mappings(
    value: GraphValue,
    required: ValueDistribution,
    ledger: PlacementLedger,
) -> tuple[OperandRegionMapping, ...] | PlacementRejection:
    binding = _source_binding(ledger, value)
    mappings = []
    for shard in required.shards:
        logical = DenseLogicalRegion(shard.global_origin, shard.valid_shape)
        mapped = _mapped(value, logical)
        if binding is not None:
            sources = (RegionSource(RegionSourceKind.EXTERNAL, TransferEndpoint(TransferEndpointKind.EXTERNAL_BINDING, INVALID_CORE_ID), mapped, ()),)
        else:
            retained = tuple(item for item in ledger.retained_backings if item.mapped_region.alias_root == value.alias_root and item.window.owner_core == shard.owner_core)
            retained_spans = merge_spans(tuple(span for item in retained for span in item.mapped_region.byte_spans))
            if _subtract(mapped.byte_spans, retained_spans):
                return PlacementRejection("E_TENSOR_NOT_RESIDENT", "metadata source has no compatible retained backing")
            sources = tuple(
                RegionSource(RegionSourceKind.LOCAL, TransferEndpoint(TransferEndpointKind.CORE, shard.owner_core), _mapped_span(value, span), ())
                for span in _intersections(mapped.byte_spans, retained_spans)
            )
        mappings.append(OperandRegionMapping(shard, logical, sources, ()))
    return tuple(mappings)


def _new_window(
    window_id: int,
    owner: int,
    value_id: int,
    role: ResidentWindowRole,
    shape: tuple[int, ...],
    dtype: DType,
    dma_visible: bool,
    buffer_index: int,
    first: GeometryEvent,
    last: GeometryEvent,
    arch: ArchManifest,
) -> ResidentWindow:
    raw = checked_mul_u64(_product(shape, "resident window elements"), dtype.byte_width, "resident window bytes")
    alignment = max(arch.sram_base_alignment_bytes, arch.axi_data_bytes) if dma_visible else max(arch.sram_base_alignment_bytes, dtype.byte_width)
    extent = _align_up(raw, arch.axi_data_bytes) if dma_visible else raw
    return ResidentWindow(window_id, owner, value_id, role, shape, dtype, dma_visible, extent, alignment, buffer_index, first, last)


def _matrix_operand_window_shape(op: GraphOp, operand_index: int, shard: RankShardGeometry, tiling: Tiling) -> tuple[int, ...]:
    shape = list(shard.padded_shape)
    if operand_index < 2:
        transpose = op.attrs.lhs_transpose if operand_index == 0 else op.attrs.rhs_transpose
        if transpose:
            shape[-2], shape[-1] = shape[-1], shape[-2]
        shape[:-2] = (1,) * max(0, len(shape) - 2)
        if operand_index == 0:
            shape[-2] = min(shape[-2], tiling.gemm_m)
            shape[-1] = min(shape[-1], tiling.gemm_k)
        else:
            shape[-2] = min(shape[-2], tiling.gemm_k)
            shape[-1] = min(shape[-1], tiling.gemm_n)
        if transpose:
            shape[-2], shape[-1] = shape[-1], shape[-2]
        return tuple(shape)
    if shape:
        shape[:-1] = (1,) * max(0, len(shape) - 1)
        shape[-1] = min(shape[-1], tiling.gemm_n)
    return tuple(shape)


def _mapping_is_local(mapping: OperandRegionMapping) -> bool:
    return bool(mapping.sources) and all(item.kind is RegionSourceKind.LOCAL for item in mapping.sources)


def _retained_window_ids(ledger: PlacementLedger, value: GraphValue, owner_core: int, mapped: MappedStorageRegion) -> tuple[int, ...]:
    required = mapped.byte_spans
    return tuple(
        item.window.window_id
        for item in ledger.retained_backings
        if item.window.owner_core == owner_core and item.mapped_region.alias_root == value.alias_root and _intersections(required, item.mapped_region.byte_spans)
    )


def _matrix_k_tiles(op: GraphOp, operands: tuple[GraphValue, ...], tiling: Tiling) -> int:
    lhs_shape = list(_shape(operands[0]))
    if op.attrs.lhs_transpose:
        lhs_shape[-2], lhs_shape[-1] = lhs_shape[-1], lhs_shape[-2]
    return max(1, (lhs_shape[-1] + tiling.gemm_k - 1) // tiling.gemm_k)


def _result_window_shape(op: GraphOp, shard: RankShardGeometry, tiling: Tiling, retained: bool) -> tuple[int, ...]:
    if retained or op.opcode not in _MATRIX:
        return shard.padded_shape
    shape = list(shard.padded_shape)
    shape[:-2] = (1,) * max(0, len(shape) - 2)
    shape[-2] = min(shape[-2], tiling.gemm_m)
    shape[-1] = min(shape[-1], tiling.gemm_n)
    return tuple(shape)


def _operand_accesses(
    op: GraphOp,
    operand_index: int,
    operand: GraphValue,
    mapping: OperandRegionMapping,
    output_tiles: tuple[TiledLogicalRegion, ...],
    compute_tiles: tuple[ComputeTileGeometry, ...],
) -> tuple[tuple[int | None, int, GeometryPhase, DenseLogicalRegion], ...]:
    if op.opcode in METADATA_VIEW_OPCODES:
        if mapping.sources and mapping.sources[0].kind is RegionSourceKind.EXTERNAL and _product(mapping.required_region.shape, "metadata access elements"):
            output_index = mapping.required_shard.logical_rank
            if output_index >= len(output_tiles):
                raise MeshIrError("E_ABI_ORDER", "metadata operand rank has no selected output tile", op_id=op.op_id)
            return ((None, output_index, GeometryPhase.PREFETCH, mapping.required_region),)
        return ()
    if not _product(mapping.required_region.shape, "operand access elements"):
        return ()
    if op.opcode in _MATRIX:
        matching = tuple(item for item in compute_tiles if item.owner_core == mapping.required_shard.owner_core)
        if operand_index == 2:
            result = []
            for output_index in dict.fromkeys(item.output_tile_index for item in matching):
                region = _matrix_operand_region(op, operand_index, operand, matching[0], output_tiles[output_index])
                if _product(region.shape, "matrix bias access elements"):
                    result.append((None, output_index, GeometryPhase.EPILOGUE, region))
            return tuple(result)
        result = []
        for compute in matching:
            region = _matrix_operand_region(op, operand_index, operand, compute, output_tiles[compute.output_tile_index])
            if _product(region.shape, "matrix operand access elements"):
                result.append((compute.tile_ordinal, compute.output_tile_index, GeometryPhase.COMPUTE, region))
        return tuple(result)
    matching = tuple(item for item in compute_tiles if item.owner_core == mapping.required_shard.owner_core)
    return tuple((item.tile_ordinal, item.output_tile_index, GeometryPhase.COMPUTE, mapping.required_region) for item in matching if item.tile.valid_m)


def operand_window_use_regions(
    op: GraphOp,
    operand_index: int,
    operand: GraphValue,
    mapping: OperandRegionMapping,
    geometry: OperationPlacementGeometry,
) -> tuple[DenseLogicalRegion, ...]:
    if type(op) is not GraphOp or type(operand_index) is not int or type(operand) is not GraphValue or type(mapping) is not OperandRegionMapping or type(geometry) is not OperationPlacementGeometry:
        raise MeshIrError("E_CONFIG", "operand window region projection has invalid record types")
    if operand_index < 0 or operand_index >= len(op.operands) or op.operands[operand_index] != operand.value_id:
        raise MeshIrError("E_ABI_ORDER", "operand window region projection has an invalid operand occurrence", op_id=op.op_id)
    selected = tuple(
        candidate
        for distribution in geometry.operand_distributions
        if distribution.operand_index == operand_index and distribution.value_id == operand.value_id
        for candidate in distribution.mappings
        if candidate == mapping
    )
    if len(selected) != 1:
        raise MeshIrError("E_ABI_ORDER", "operand window region projection does not reference a selected operand mapping", op_id=op.op_id)
    binding = tuple(
        item
        for item in geometry.operand_windows
        if item.operand_index == operand_index and item.logical_rank == mapping.required_shard.logical_rank
    )
    if len(binding) != 1:
        raise MeshIrError("E_ABI_ORDER", "operand window region projection has no unique selected window binding", op_id=op.op_id)
    accesses = _operand_accesses(op, operand_index, operand, mapping, geometry.output_tiles, geometry.compute_tiles)
    if not mapping.window_uses and op.opcode in METADATA_VIEW_OPCODES:
        return ()
    by_identity: dict[tuple[int | None, int, GeometryPhase], DenseLogicalRegion] = {}
    for compute_ordinal, output_index, phase, region in accesses:
        identity = (compute_ordinal, output_index, phase)
        if identity in by_identity:
            raise MeshIrError("E_ABI_ORDER", "operand window region projection has an ambiguous access identity", op_id=op.op_id)
        by_identity[identity] = region
    projected = []
    seen: set[tuple[int | None, int, GeometryPhase]] = set()
    for use in mapping.window_uses:
        identity = (use.compute_tile_ordinal, use.output_tile_index, use.phase)
        if use.operation_ordinal != geometry.operation_ordinal or use.window_id not in binding[0].window_ids or identity in seen or identity not in by_identity:
            raise MeshIrError("E_ABI_ORDER", "operand window region projection has an invalid window use identity", op_id=op.op_id)
        seen.add(identity)
        projected.append(by_identity[identity])
    if len(seen) != len(by_identity):
        raise MeshIrError("E_ABI_ORDER", "operand window region projection omits a selected access identity", op_id=op.op_id)
    return tuple(projected)


def _retained_window_for_access(ledger: PlacementLedger, owner_core: int, alias_root: int, mapped: MappedStorageRegion, candidates: tuple[int, ...]) -> int:
    required = mapped.byte_spans
    for backing in ledger.retained_backings:
        if backing.window.window_id in candidates and backing.window.owner_core == owner_core and backing.mapped_region.alias_root == alias_root and not _subtract(required, backing.mapped_region.byte_spans):
            return backing.window.window_id
    raise MeshIrError("E_TENSOR_NOT_RESIDENT", "selected retained windows do not cover operand access")


def derive_operation_geometry(
    op: GraphOp,
    values: tuple[GraphValue, ...],
    strategy: ShardingStrategy,
    core_ids: tuple[int, ...],
    ledger: PlacementLedger,
    tiling: Tiling,
    collectives: Collectives,
    arch: ArchManifest,
) -> OperationPlacementGeometry | PlacementRejection:
    if type(op) is not GraphOp or type(values) is not tuple or any(type(item) is not GraphValue for item in values) or type(strategy) is not ShardingStrategy or type(core_ids) is not tuple or type(ledger) is not PlacementLedger or type(tiling) is not Tiling or type(collectives) is not Collectives or type(arch) is not ArchManifest:
        raise MeshIrError("E_CONFIG", "placement geometry inputs have invalid record types")
    if ledger.next_operation_ordinal >= len(ledger.operation_ids) or ledger.operation_ids[ledger.next_operation_ordinal] != op.op_id:
        raise MeshIrError("E_ABI_ORDER", "placement operation differs from ledger order", op_id=op.op_id)
    if not core_ids or len(set(core_ids)) != len(core_ids) or any(type(core) is not int or core not in arch.core_ids for core in core_ids):
        raise MeshIrError("E_PLACEMENT_INFEASIBLE", "placement candidate cores are invalid", op_id=op.op_id)
    by_id = {item.value_id: item for item in values}
    operands = tuple(by_id[item] for item in op.operands)
    result = by_id[op.results[0]]
    established = {item.value_id: item for item in ledger.value_distributions}
    required = _required_distributions(op, operands, result, strategy, core_ids, established)
    if type(required) is PlacementRejection:
        return required
    required_operands, result_distribution = required
    operand_records = []
    staged = dict(established)
    for index, (value, distribution) in enumerate(zip(operands, required_operands)):
        mappings = _metadata_mappings(value, distribution, ledger) if op.opcode in METADATA_VIEW_OPCODES else _cover_operand(index, value, distribution, ledger)
        if type(mappings) is PlacementRejection:
            return mappings
        operand_records.append(OperandDistribution(index, value.value_id, staged.get(value.value_id), distribution, mappings))
        if value.value_id not in staged and _source_binding(ledger, value) is not None:
            staged[value.value_id] = distribution
    output_tiles = _output_tiles(op, result_distribution, tiling)
    compute_tiles = _compute_tiles(op, operands, result, required_operands, result_distribution, output_tiles, strategy, tiling)
    collective_geometries = []
    if strategy is ShardingStrategy.MATRIX_K_SUM:
        algorithm = {"ring": CollectiveAlgorithm.RING, "tree": CollectiveAlgorithm.TREE}.get(collectives.all_reduce_algorithm)
        if algorithm is None:
            return PlacementRejection("E_CONFIG", "matrix K placement requires a resolved collective algorithm")
        for tile in output_tiles:
            collective_geometries.append(collective_transfer_geometry(op.op_id, result.value_id, tile, op.attrs.accum_dtype, core_ids, algorithm, collectives.chunk_bytes))
    destination = next((item for item in ledger.external_bindings if item.kind is ExternalBindingKind.DESTINATION and item.value_id == result.value_id), None)
    store_shards = result_distribution.shards[:1] if result_distribution.distribution is DistributionKind.REPLICATED else result_distribution.shards
    current = ledger.next_operation_ordinal
    first = GeometryEvent(current, 0, GeometryPhase.PREFETCH)
    compute_last = GeometryEvent(current, max(0, len(compute_tiles) - 1), GeometryPhase.COMPUTE)
    window_id = ledger.next_window_id
    windows = []
    operand_window_bindings = {}
    matrix_k_tiles = _matrix_k_tiles(op, operands, tiling) if op.opcode in _MATRIX else 1
    if op.opcode in METADATA_VIEW_OPCODES:
        for shard in store_shards if destination is not None and _source_binding(ledger, operands[0]) is not None else ():
            if not _product(shard.valid_shape, "metadata output elements"):
                continue
            windows.append(_new_window(window_id, shard.owner_core, result.value_id, ResidentWindowRole.OPERAND, shard.padded_shape, result.dtype, True, 0, first, GeometryEvent(current, 0, GeometryPhase.STORE), arch))
            operand_window_bindings[(0, shard.logical_rank)] = OperandWindowBinding(0, shard.logical_rank, (window_id,))
            window_id += 1
    operand_window_keys = {}
    for operand in operand_records:
        value = by_id[operand.value_id]
        for mapping in operand.mappings:
            shard = mapping.required_shard
            mapped = _mapped(value, mapping.required_region)
            if not mapped.useful_bytes:
                operand_window_bindings[(operand.operand_index, shard.logical_rank)] = OperandWindowBinding(operand.operand_index, shard.logical_rank, ())
                continue
            if op.opcode in METADATA_VIEW_OPCODES:
                retained_ids = _retained_window_ids(ledger, value, shard.owner_core, mapped)
                operand_window_bindings.setdefault((operand.operand_index, shard.logical_rank), OperandWindowBinding(operand.operand_index, shard.logical_rank, retained_ids))
                continue
            if _mapping_is_local(mapping):
                retained_ids = _retained_window_ids(ledger, value, shard.owner_core, mapped)
                if not retained_ids:
                    raise MeshIrError("E_TENSOR_NOT_RESIDENT", "local operand mapping has no retained window", value_id=value.value_id)
                operand_window_bindings[(operand.operand_index, shard.logical_rank)] = OperandWindowBinding(operand.operand_index, shard.logical_rank, retained_ids)
                continue
            dma = any(source.kind is not RegionSourceKind.LOCAL for source in mapping.sources)
            window_shape = _matrix_operand_window_shape(op, operand.operand_index, shard, tiling) if op.opcode in _MATRIX else shard.padded_shape
            role = ResidentWindowRole.BIAS if op.opcode is OpCode.LINEAR_BIAS and operand.operand_index == 2 else ResidentWindowRole.OPERAND
            access_sequence = tuple(
                (compute_ordinal, output_index, phase, _mapped_key(_mapped(value, region)))
                for compute_ordinal, output_index, phase, region in _operand_accesses(op, operand.operand_index, value, mapping, output_tiles, compute_tiles)
            )
            if not access_sequence:
                operand_window_bindings[(operand.operand_index, shard.logical_rank)] = OperandWindowBinding(operand.operand_index, shard.logical_rank, ())
                continue
            slots = 2 if dma and tiling.double_buffer and len({item[3] for item in access_sequence}) > 1 else 1
            key = (shard.owner_core, _mapped_key(mapped), window_shape, role, value.dtype, dma, slots, first, compute_last, access_sequence)
            if key in operand_window_keys:
                operand_window_bindings[(operand.operand_index, shard.logical_rank)] = OperandWindowBinding(operand.operand_index, shard.logical_rank, operand_window_keys[key])
                continue
            selected_window_ids = tuple(range(window_id, window_id + slots))
            operand_window_keys[key] = selected_window_ids
            for buffer_index in range(slots):
                windows.append(_new_window(window_id, shard.owner_core, value.alias_root, role, window_shape, value.dtype, dma, buffer_index, first, compute_last, arch))
                window_id += 1
            operand_window_bindings[(operand.operand_index, shard.logical_rank)] = OperandWindowBinding(operand.operand_index, shard.logical_rank, selected_window_ids)
    last_use = dict(ledger.last_use_by_alias_root).get(result.alias_root, current)
    retained = []
    for shard in (() if op.opcode in METADATA_VIEW_OPCODES else result_distribution.shards):
        if not _product(shard.valid_shape, "result shard elements"):
            continue
        end_phase = GeometryPhase.STORE if destination is not None and last_use == current else GeometryPhase.COMPUTE
        must_retain = last_use > current
        window_shape = _result_window_shape(op, shard, tiling, must_retain)
        last_tile = max(0, len(compute_tiles) - 1) if last_use == current else 0
        window = _new_window(window_id, shard.owner_core, result.value_id, ResidentWindowRole.SEMANTIC_RESULT, window_shape, result.dtype, destination is not None, 0, GeometryEvent(current, 0, GeometryPhase.COMPUTE), GeometryEvent(last_use, last_tile, end_phase), arch)
        windows.append(window)
        if must_retain:
            retained.append(RetainedBacking(window, _mapped(result, DenseLogicalRegion(shard.global_origin, shard.valid_shape)), last_use))
        window_id += 1
    if op.opcode in _MATRIX:
        needs_accumulator = strategy is ShardingStrategy.MATRIX_K_SUM or matrix_k_tiles > 1 or result.dtype is not op.attrs.accum_dtype or op.opcode is OpCode.LINEAR_BIAS or op.attrs.alpha != 1.0
        if needs_accumulator:
            owners = tuple(dict.fromkeys(item.owner_core for item in result_distribution.shards))
            tile_shape = max(output_tiles, key=lambda item: _product(item.padded_shape, "output tile elements")).padded_shape
            for owner in owners:
                windows.append(_new_window(window_id, owner, result.value_id, ResidentWindowRole.ACCUMULATOR, tile_shape, op.attrs.accum_dtype, False, 0, GeometryEvent(current, 0, GeometryPhase.COMPUTE), GeometryEvent(current, max(0, len(compute_tiles) - 1), GeometryPhase.EPILOGUE), arch))
                window_id += 1
    collective_transfers = tuple(transfer for collective in collective_geometries for transfer in collective.transfers)
    if collective_transfers:
        maximum = max(item.chunk.element_count for item in collective_transfers)
        for owner in core_ids:
            windows.append(_new_window(window_id, owner, result.value_id, ResidentWindowRole.COLLECTIVE_RECEIVE, (maximum,), op.attrs.accum_dtype, True, 0, GeometryEvent(current, 0, GeometryPhase.COLLECTIVE), GeometryEvent(current, max(0, len(output_tiles) - 1), GeometryPhase.COLLECTIVE), arch))
            window_id += 1
    operand_windows = tuple(operand_window_bindings[key] for key in sorted(operand_window_bindings))
    binding_by_key = {(item.operand_index, item.logical_rank): item for item in operand_windows}
    cache_contents = {}
    slot_cursors = {}
    transfers = []
    transfer_keys = {}
    source_transfer_indices = {}
    generation_sequences = {}
    updated_operands = []
    for operand in operand_records:
        value = by_id[operand.value_id]
        updated_mappings = []
        for mapping_index, mapping in enumerate(operand.mappings):
            binding = binding_by_key[(operand.operand_index, mapping.required_shard.logical_rank)]
            uses = []
            if op.opcode in METADATA_VIEW_OPCODES and not binding.window_ids:
                updated_mappings.append(mapping)
                continue
            accesses = _operand_accesses(op, operand.operand_index, value, mapping, output_tiles, compute_tiles)
            sequence_key = (
                binding.window_ids,
                tuple((compute_ordinal, output_index, phase, _mapped_key(_mapped(value, region))) for compute_ordinal, output_index, phase, region in accesses),
                tuple((source.kind, source.source, _mapped_key(source.mapped_region)) for source in mapping.sources),
            )
            shared_sequence = generation_sequences.get(sequence_key)
            if shared_sequence is not None:
                shared_uses, shared_transfers = shared_sequence
                for transfer_index in shared_transfers:
                    transfer_region = transfers[transfer_index].region.mapped_region
                    for source_index, source in enumerate(mapping.sources):
                        if source.kind is not RegionSourceKind.LOCAL and transfer_region.alias_root == source.mapped_region.alias_root and _intersections(transfer_region.byte_spans, source.mapped_region.byte_spans):
                            source_transfer_indices.setdefault((operand.operand_index, mapping_index, source_index), []).append(transfer_index)
                updated_sources = tuple(
                    replace(source, transfer_indices=tuple(dict.fromkeys(source_transfer_indices.get((operand.operand_index, mapping_index, source_index), ()))))
                    for source_index, source in enumerate(mapping.sources)
                )
                updated_mappings.append(replace(mapping, sources=updated_sources, window_uses=shared_uses))
                continue
            for compute_ordinal, output_index, phase, logical_region in accesses:
                mapped_access = _mapped(value, logical_region)
                if not mapped_access.useful_bytes:
                    continue
                if _mapping_is_local(mapping):
                    selected_window = _retained_window_for_access(ledger, mapping.required_shard.owner_core, value.alias_root, mapped_access, binding.window_ids)
                    uses.append(WindowUseGeometry(current, compute_ordinal, output_index, selected_window, phase))
                    continue
                if not binding.window_ids:
                    raise MeshIrError("E_ABI_ORDER", "nonlocal operand access has no selected window", value_id=value.value_id)
                content_key = _mapped_key(mapped_access)
                selected_window = next((candidate for candidate in binding.window_ids if candidate in cache_contents and cache_contents[candidate][0] == content_key), None)
                if selected_window is None:
                    cursor = slot_cursors.get(binding.window_ids, 0)
                    selected_window = binding.window_ids[cursor % len(binding.window_ids)]
                    slot_cursors[binding.window_ids] = cursor + 1
                use = WindowUseGeometry(current, compute_ordinal, output_index, selected_window, phase)
                uses.append(use)
                if selected_window in cache_contents and cache_contents[selected_window][0] == content_key:
                    for transfer_index in cache_contents[selected_window][1]:
                        transfer_region = transfers[transfer_index].region.mapped_region
                        for source_index, source in enumerate(mapping.sources):
                            if source.kind is not RegionSourceKind.LOCAL and _intersections(transfer_region.byte_spans, source.mapped_region.byte_spans):
                                source_transfer_indices.setdefault((operand.operand_index, mapping_index, source_index), []).append(transfer_index)
                    continue
                emitted = []
                remaining = mapped_access.byte_spans
                for source_index, source in enumerate(mapping.sources):
                    for span in _intersections(remaining, source.mapped_region.byte_spans):
                        piece = _mapped_span(value, span)
                        if source.kind is RegionSourceKind.LOCAL:
                            continue
                        kind = PlacementTransferKind.PEER_RESHARD if source.kind is RegionSourceKind.PEER else PlacementTransferKind.EXTERNAL_LOAD
                        request_core = source.source.core_id if kind is PlacementTransferKind.PEER_RESHARD else mapping.required_shard.owner_core
                        destination_endpoint = TransferEndpoint(TransferEndpointKind.CORE, mapping.required_shard.owner_core)
                        key = (kind, request_core, source.source, destination_endpoint, _mapped_key(piece), use)
                        transfer_index = transfer_keys.get(key)
                        if transfer_index is None:
                            transfer_index = len(transfers)
                            transfer_keys[key] = transfer_index
                            transfers.append(ProspectiveTransfer(kind, value.value_id, operand.operand_index, request_core, source.source, destination_endpoint, ValueTransferRegion(piece), use))
                        emitted.append(transfer_index)
                        source_transfer_indices.setdefault((operand.operand_index, mapping_index, source_index), []).append(transfer_index)
                    remaining = _subtract(remaining, source.mapped_region.byte_spans)
                if remaining:
                    raise MeshIrError("E_TENSOR_NOT_RESIDENT", "operand generation is not covered by selected sources", value_id=value.value_id)
                cache_contents[selected_window] = (content_key, tuple(dict.fromkeys(emitted)))
            selected_transfers = tuple(
                dict.fromkeys(
                    transfer_index
                    for source_index in range(len(mapping.sources))
                    for transfer_index in source_transfer_indices.get((operand.operand_index, mapping_index, source_index), ())
                )
            )
            generation_sequences[sequence_key] = (tuple(uses), selected_transfers)
            updated_sources = tuple(
                replace(source, transfer_indices=tuple(dict.fromkeys(source_transfer_indices.get((operand.operand_index, mapping_index, source_index), ()))))
                for source_index, source in enumerate(mapping.sources)
            )
            updated_mappings.append(replace(mapping, sources=updated_sources, window_uses=tuple(uses)))
        updated_operands.append(replace(operand, mappings=tuple(updated_mappings)))
    result_windows = tuple(item for item in windows if item.role is ResidentWindowRole.SEMANTIC_RESULT)
    metadata_windows = tuple(item for item in windows if op.opcode in METADATA_VIEW_OPCODES and item.role is ResidentWindowRole.OPERAND)
    output_owners = _output_tile_owners(op, result_distribution, tiling)
    for output_index, (tile, owner) in enumerate(zip(output_tiles, output_owners)):
        if result_distribution.distribution is DistributionKind.REPLICATED and owner != store_shards[0].owner_core:
            continue
        mapped = _mapped(result, DenseLogicalRegion(tile.origin, tile.valid_shape))
        if destination is None or not mapped.useful_bytes:
            continue
        candidates = tuple(item for item in (metadata_windows if op.opcode in METADATA_VIEW_OPCODES else result_windows) if item.owner_core == owner)
        if candidates:
            selected_window_id = candidates[0].window_id
        elif op.opcode in METADATA_VIEW_OPCODES:
            operand = updated_operands[0]
            mapping = next((item for item in operand.mappings if item.required_shard.owner_core == owner), None)
            if mapping is None:
                raise MeshIrError("E_ABI_ORDER", "output store has no selected result mapping", value_id=result.value_id)
            binding = binding_by_key[(operand.operand_index, mapping.required_shard.logical_rank)]
            selected_window_id = _retained_window_for_access(ledger, owner, result.alias_root, mapped, binding.window_ids)
        else:
            raise MeshIrError("E_ABI_ORDER", "output store has no selected result window", value_id=result.value_id)
        store_use = WindowUseGeometry(current, None, output_index, selected_window_id, GeometryPhase.STORE)
        transfers.append(ProspectiveTransfer(PlacementTransferKind.OUTPUT_STORE, result.value_id, None, owner, TransferEndpoint(TransferEndpointKind.CORE, owner), TransferEndpoint(TransferEndpointKind.EXTERNAL_BINDING, INVALID_CORE_ID), ValueTransferRegion(mapped), store_use))
    return OperationPlacementGeometry(current, tuple(updated_operands), operand_windows, result_distribution, output_tiles, compute_tiles, tuple(collective_geometries), tuple(transfers), tuple(windows), tuple(retained))


def advance_placement_ledger(ledger: PlacementLedger, geometry: OperationPlacementGeometry) -> PlacementLedger:
    if type(ledger) is not PlacementLedger or type(geometry) is not OperationPlacementGeometry or geometry.operation_ordinal != ledger.next_operation_ordinal:
        raise MeshIrError("E_ABI_ORDER", "placement geometry cannot advance this ledger")
    current = ledger.next_operation_ordinal
    distributions = {item.value_id: item for item in ledger.value_distributions}
    for operand in geometry.operand_distributions:
        distributions.setdefault(operand.value_id, operand.required_distribution)
    distributions[geometry.result_distribution.value_id] = geometry.result_distribution
    retained = tuple(item for item in ledger.retained_backings if item.retain_through_operation > current)
    retained += tuple(item for item in geometry.retained_results if item.retain_through_operation > current)
    next_window = max((item.window_id for item in geometry.resident_windows), default=ledger.next_window_id - 1) + 1
    return PlacementLedger(current + 1, next_window, ledger.operation_ids, ledger.external_bindings, tuple(distributions.values()), retained, ledger.last_use_by_alias_root)


def _minimum_target_hops(arch: ArchManifest, request_core: int, kind: PlacementTransferKind | None, target_core: int) -> int | PlacementRejection:
    initiator = next((item for item in arch.fabric.initiators if item.core_id == request_core), None)
    if initiator is None:
        return PlacementRejection("E_PLACEMENT_INFEASIBLE", "placement core has no fabric initiator")
    external = kind in (PlacementTransferKind.EXTERNAL_LOAD, PlacementTransferKind.OUTPUT_STORE)
    target_kinds = ("HBM", "HOST_SHARED") if external else ("CORE_SRAM_APERTURE",)
    access = Access.READ_ONLY if kind is PlacementTransferKind.EXTERNAL_LOAD else Access.READ_WRITE
    owner = INVALID_CORE_ID if external else target_core
    target_routers = []
    for endpoint in arch.fabric.targets:
        for target_range in endpoint.ranges:
            if not 0 <= target_range.region_id < len(arch.regions):
                continue
            region = arch.regions[target_range.region_id]
            limit = region.tile_bytes if region.kind == "CORE_SRAM_APERTURE" else region.bytes
            legal = target_range.offset_bytes >= 0 and target_range.size_bytes > 0 and target_range.offset_bytes + target_range.size_bytes <= limit
            permitted = target_range.access is Access.READ_WRITE or access is Access.READ_ONLY
            if legal and permitted and region.kind in target_kinds and target_range.owner_core == owner:
                target_routers.append(endpoint.router_id)
    if not target_routers:
        return PlacementRejection("E_PLACEMENT_INFEASIBLE", "placement transfer has no legal target range")
    cols = arch.fabric.network.router_cols
    source_y, source_x = divmod(initiator.router_id, cols)
    return min(abs(source_y - target_y) + abs(source_x - target_x) for target_y, target_x in (divmod(router, cols) for router in target_routers))


def estimate_operation_geometry(geometry: OperationPlacementGeometry, ledger: PlacementLedger, arch: ArchManifest) -> GeometryEstimate | PlacementRejection:
    communication = 0
    hop_bytes = 0
    for transfer in geometry.value_transfers:
        transfer_bytes = transfer.useful_bytes
        communication = checked_add_u64(communication, transfer_bytes, "placement communication bytes")
        target_core = transfer.destination.core_id
        hops = _minimum_target_hops(arch, transfer.request_core, transfer.kind, target_core)
        if type(hops) is PlacementRejection:
            return hops
        hop_bytes = checked_add_u64(hop_bytes, checked_mul_u64(transfer_bytes, hops, "placement hop bytes"), "placement hop bytes")
    for transfer in geometry.collective_transfers:
        transfer_bytes = checked_mul_u64(transfer.chunk.element_count, transfer.chunk.accumulation_dtype.byte_width, "collective transfer bytes")
        communication = checked_add_u64(communication, transfer_bytes, "placement communication bytes")
        hops = _minimum_target_hops(arch, transfer.source_core, None, transfer.destination_core)
        if type(hops) is PlacementRejection:
            return hops
        hop_bytes = checked_add_u64(hop_bytes, checked_mul_u64(transfer_bytes, hops, "placement hop bytes"), "placement hop bytes")
    live_windows = tuple(item.window for item in ledger.retained_backings) + geometry.resident_windows
    events = tuple(sorted({event for item in live_windows for event in (item.first_event, item.last_event)}))
    peak = 0
    for event in events:
        by_core = {}
        for window in live_windows:
            if window.first_event <= event <= window.last_event:
                by_core[window.owner_core] = checked_add_u64(by_core.get(window.owner_core, 0), window.required_extent_bytes, "placement live bytes")
        peak = max(peak, *by_core.values()) if by_core else peak
    if any(item.required_extent_bytes > arch.sram_bytes for item in live_windows) or peak > arch.sram_bytes:
        return PlacementRejection("E_SRAM_OOM", "requested resident windows have a provable SRAM capacity lower bound")
    return GeometryEstimate(communication, peak, hop_bytes)


__all__ = [
    "ComputeTileGeometry",
    "DenseLogicalRegion",
    "ExternalBinding",
    "ExternalBindingKind",
    "GeometryEstimate",
    "GeometryEvent",
    "GeometryPhase",
    "MappedStorageRegion",
    "OperandDistribution",
    "OperandRegionMapping",
    "OperandWindowBinding",
    "OperationPlacementGeometry",
    "PlacementLedger",
    "PlacementRejection",
    "PlacementTransferKind",
    "ProspectiveTransfer",
    "RankShardGeometry",
    "RegionSource",
    "RegionSourceKind",
    "ResidentWindow",
    "ResidentWindowRole",
    "RetainedBacking",
    "ShardingStrategy",
    "TransferEndpoint",
    "TransferEndpointKind",
    "ValueDistribution",
    "ValueTransferRegion",
    "WindowUseGeometry",
    "advance_placement_ledger",
    "create_placement_ledger",
    "derive_operation_geometry",
    "estimate_operation_geometry",
    "operand_window_use_regions",
]
