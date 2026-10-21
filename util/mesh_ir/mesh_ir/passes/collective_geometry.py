from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from mesh_ir.canonical import checked_add_u64, checked_mul_u64, checked_u64
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import DType, INVALID_CORE_ID
from mesh_ir.ir.kernel_ir import CollectiveAlgorithm


class CollectivePhase(str, Enum):
    REDUCE = "REDUCE"
    PROPAGATE = "PROPAGATE"


@dataclass(frozen=True)
class TiledLogicalRegion:
    origin: tuple[int, ...]
    padded_shape: tuple[int, ...]
    valid_shape: tuple[int, ...]

    def __post_init__(self) -> None:
        if type(self.origin) is not tuple or type(self.padded_shape) is not tuple or type(self.valid_shape) is not tuple:
            raise MeshIrError("E_CONFIG", "collective tile geometry must use immutable tuples")
        if not len(self.origin) == len(self.padded_shape) == len(self.valid_shape):
            raise MeshIrError("E_EXPORT_LAYOUT", "collective tile geometry ranks differ")
        for origin, padded, valid in zip(self.origin, self.padded_shape, self.valid_shape):
            checked_u64(origin, "collective tile origin")
            checked_u64(padded, "collective tile padded extent")
            checked_u64(valid, "collective tile valid extent")
            if valid > padded:
                raise MeshIrError("E_EXPORT_LAYOUT", "collective tile valid extent exceeds padding")
            checked_add_u64(origin, valid, "collective tile valid end")


@dataclass(frozen=True)
class CollectiveChunkGeometry:
    chunk_index: int
    computation_id: int
    semantic_result_value_id: int
    output_tile: TiledLogicalRegion
    owner_rank: int
    subchunk_index: int
    tile_flat_offset: int
    element_count: int
    accumulation_dtype: DType

    def __post_init__(self) -> None:
        if type(self.output_tile) is not TiledLogicalRegion or type(self.accumulation_dtype) is not DType:
            raise MeshIrError("E_CONFIG", "collective chunk record types are invalid")
        for value, field in (
            (self.chunk_index, "collective chunk index"),
            (self.computation_id, "collective computation id"),
            (self.semantic_result_value_id, "collective result value id"),
            (self.owner_rank, "collective owner rank"),
            (self.subchunk_index, "collective subchunk index"),
            (self.tile_flat_offset, "collective tile offset"),
            (self.element_count, "collective element count"),
        ):
            checked_u64(value, field)
        if self.chunk_index == 0 or self.computation_id == 0 or self.semantic_result_value_id == 0:
            raise MeshIrError("E_CONFIG", "collective chunk identities must be positive")


@dataclass(frozen=True)
class CollectiveTransferGeometry:
    chunk: CollectiveChunkGeometry
    source_core: int
    destination_core: int
    phase: CollectivePhase

    def __post_init__(self) -> None:
        valid_cores = type(self.source_core) is int and type(self.destination_core) is int and 0 <= self.source_core < INVALID_CORE_ID and 0 <= self.destination_core < INVALID_CORE_ID
        if type(self.chunk) is not CollectiveChunkGeometry or type(self.phase) is not CollectivePhase or not valid_cores or self.source_core == self.destination_core or self.chunk.element_count == 0:
            raise MeshIrError("E_CONFIG", "collective transfer record is invalid")


@dataclass(frozen=True)
class CollectiveGeometry:
    chunks: tuple[CollectiveChunkGeometry, ...]
    transfers: tuple[CollectiveTransferGeometry, ...]

    def __post_init__(self) -> None:
        if type(self.chunks) is not tuple or type(self.transfers) is not tuple or any(type(item) is not CollectiveChunkGeometry for item in self.chunks) or any(type(item) is not CollectiveTransferGeometry for item in self.transfers):
            raise MeshIrError("E_CONFIG", "collective geometry must use typed immutable records")
        chunk_identities = {id(item) for item in self.chunks}
        if any(id(item.chunk) not in chunk_identities for item in self.transfers):
            raise MeshIrError("E_CONFIG", "collective transfer references an absent chunk")


def _elements(shape: tuple[int, ...]) -> int:
    result = 1
    for extent in shape:
        result = checked_mul_u64(result, extent, "collective tile elements")
    return result


def _chunks(
    computation_id: int,
    semantic_result_value_id: int,
    output_tile: TiledLogicalRegion,
    accumulation_dtype: DType,
    participants: int,
    chunk_elements: int,
) -> tuple[CollectiveChunkGeometry, ...]:
    elements = _elements(output_tile.valid_shape)
    rank_extent = 0 if elements == 0 else (elements + participants - 1) // participants
    subchunks = 1 if elements == 0 else (rank_extent + chunk_elements - 1) // chunk_elements
    records = []
    for rank in range(participants):
        rank_begin = checked_mul_u64(rank, rank_extent, "collective rank begin")
        rank_end = min(elements, checked_mul_u64(rank + 1, rank_extent, "collective rank end"))
        for subchunk in range(subchunks):
            begin = 0 if elements == 0 else checked_add_u64(rank_begin, checked_mul_u64(subchunk, chunk_elements, "collective subchunk begin"), "collective subchunk begin")
            if begin > rank_end:
                begin = rank_end
            end = 0 if elements == 0 else min(checked_add_u64(begin, chunk_elements, "collective subchunk end"), elements, rank_end)
            records.append((begin, rank, subchunk, end - begin))
    records.sort()
    return tuple(
        CollectiveChunkGeometry(index, computation_id, semantic_result_value_id, output_tile, rank, subchunk, begin, count, accumulation_dtype)
        for index, (begin, rank, subchunk, count) in enumerate(records, 1)
    )


def _ring_transfers(chunks: tuple[CollectiveChunkGeometry, ...], core_ids: tuple[int, ...]) -> tuple[CollectiveTransferGeometry, ...]:
    result = []
    participants = len(core_ids)
    for chunk in chunks:
        if chunk.element_count == 0:
            continue
        rank = chunk.owner_rank
        for phase in (CollectivePhase.REDUCE, CollectivePhase.PROPAGATE):
            for _ in range(participants - 1):
                next_rank = (rank + 1) % participants
                result.append(CollectiveTransferGeometry(chunk, core_ids[rank], core_ids[next_rank], phase))
                rank = next_rank
    return tuple(result)


def _tree_transfers(chunks: tuple[CollectiveChunkGeometry, ...], core_ids: tuple[int, ...]) -> tuple[CollectiveTransferGeometry, ...]:
    result = []
    ranks = tuple(range(len(core_ids)))
    reduce_ranks = tuple(sorted(ranks[1:], key=lambda rank: (-((rank + 1).bit_length() - 1), rank)))
    propagate_ranks = tuple(sorted(ranks[1:], key=lambda rank: (((rank + 1).bit_length() - 1), rank)))
    reduce_edges = tuple((rank, (rank - 1) // 2) for rank in reduce_ranks)
    propagate_edges = tuple(((rank - 1) // 2, rank) for rank in propagate_ranks)
    for chunk in chunks:
        if chunk.element_count == 0:
            continue
        for phase, edges in ((CollectivePhase.REDUCE, reduce_edges), (CollectivePhase.PROPAGATE, propagate_edges)):
            for source, destination in edges:
                result.append(CollectiveTransferGeometry(chunk, core_ids[source], core_ids[destination], phase))
    return tuple(result)


def collective_transfer_geometry(
    computation_id: int,
    semantic_result_value_id: int,
    output_tile: TiledLogicalRegion,
    accumulation_dtype: DType,
    core_ids: tuple[int, ...],
    algorithm: CollectiveAlgorithm,
    chunk_bytes: int,
) -> CollectiveGeometry:
    if type(computation_id) is not int or computation_id < 1 or type(semantic_result_value_id) is not int or semantic_result_value_id < 1:
        raise MeshIrError("E_CONFIG", "collective geometry identities are invalid")
    if type(output_tile) is not TiledLogicalRegion or type(accumulation_dtype) is not DType:
        raise MeshIrError("E_CONFIG", "collective geometry records have invalid types")
    if type(core_ids) is not tuple or not core_ids or any(type(core) is not int or not 0 <= core < INVALID_CORE_ID for core in core_ids) or len(set(core_ids)) != len(core_ids):
        raise MeshIrError("E_PLACEMENT_INFEASIBLE", "collective participants are invalid")
    if type(algorithm) is not CollectiveAlgorithm or algorithm not in (CollectiveAlgorithm.RING, CollectiveAlgorithm.TREE):
        raise MeshIrError("E_CONFIG", "collective geometry requires a resolved RING or TREE algorithm")
    checked_u64(chunk_bytes, "collective chunk bytes")
    if chunk_bytes == 0:
        raise MeshIrError("E_CONFIG", "collective chunk byte budget must be positive")
    elements = _elements(output_tile.valid_shape)
    chunk_elements = chunk_bytes // accumulation_dtype.byte_width
    if elements and chunk_elements == 0:
        raise MeshIrError("E_CONFIG", "collective chunk byte budget cannot hold one accumulation element")
    chunks = _chunks(computation_id, semantic_result_value_id, output_tile, accumulation_dtype, len(core_ids), max(chunk_elements, 1))
    transfers = _ring_transfers(chunks, core_ids) if algorithm is CollectiveAlgorithm.RING else _tree_transfers(chunks, core_ids)
    return CollectiveGeometry(chunks, transfers)


__all__ = [
    "CollectiveChunkGeometry",
    "CollectiveGeometry",
    "CollectivePhase",
    "CollectiveTransferGeometry",
    "TiledLogicalRegion",
    "collective_transfer_geometry",
]
