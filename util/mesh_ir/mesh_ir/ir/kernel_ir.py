from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, TypeAlias

from mesh_ir.canonical import canonical_json_bytes, semantic_sha256, to_canonical
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Access, Add, CeilDivByConst, Const, DType, DmaKind, FloorDivByConst, Layout, MemorySpace, MulByConst, StorageClass, Symbol, TensorRole, dimension_data
from mesh_ir.ir.graph_ir import ElementwiseAttrs, EmbeddingAttrs, MatmulAttrs, MovementAttrs, NormAttrs, OpAttrs, OpCode, ReduceAttrs, SoftmaxAttrs, ViewAttrs, operation_attrs_data
from mesh_ir.generated.semantic_enums import CollectiveAlgorithm, CollectiveKind, DistributionKind, KernelOpcode, MatrixEpilogueAlgorithm, MatrixPhase, MovementAlgorithm, NormAlgorithm, OperandAccessMode, ReduceKind, ReductionAlgorithm, SoftmaxAlgorithm, StateOrigin, SynthesizedTensorPurpose, VectorAlgorithm

if TYPE_CHECKING:
    from mesh_ir.analysis.dependency import KernelDependencyGraph
    from mesh_ir.architecture import ArchManifest


@dataclass(frozen=True)
class BlockedMnkLayout:
    block_m: int
    block_n: int
    block_k: int
    minor_to_major: tuple[int, ...]


@dataclass(frozen=True)
class KernelTensor:
    tensor_id: int
    producer_computation_id: int
    synthesized_purpose: SynthesizedTensorPurpose | None
    alias_root_tensor_id: int
    storage_offset_elements: int
    name: str
    role: TensorRole
    dtype: DType
    shape: tuple[int, ...]
    strides: tuple[int, ...]
    storage_class: StorageClass
    access: Access
    logical_extent_bytes: int
    storage_extent_bytes: int
    content_sha256: str | None


@dataclass(frozen=True)
class KernelComputation:
    computation_id: int
    opcode: OpCode
    operand_tensor_ids: tuple[int, ...]
    result_tensor_id: int
    attrs: OpAttrs


@dataclass(frozen=True)
class KernelTensorLineage:
    tensor_id: int
    source_value_id: int


@dataclass(frozen=True)
class KernelComputationLineage:
    computation_id: int
    source_op_id: int
    source_node_id: str


@dataclass(frozen=True)
class Placement:
    placement_id: int
    core_ids: tuple[int, ...]


@dataclass(frozen=True)
class TensorShard:
    shard_id: int
    tensor_id: int
    placement_id: int
    owner_core: int
    distribution: DistributionKind
    global_origin: tuple[int, ...]
    padded_local_shape: tuple[int, ...]
    valid_shape: tuple[int, ...]
    partial_sum_id: int = 0


@dataclass(frozen=True)
class BufferObject:
    object_id: int
    storage_tensor_id: int
    owner_core: int
    memory_space: MemorySpace
    shape: tuple[int, ...]
    strides: tuple[int, ...]
    footprint_bytes: int
    alignment_bytes: int
    persistent: bool
    buffer_index: int


@dataclass(frozen=True)
class BufferView:
    view_id: int
    object_id: int
    shard_id: int
    shard_origin: tuple[int, ...]
    padded_shape: tuple[int, ...]
    valid_shape: tuple[int, ...]
    object_offset_elements: int
    object_strides: tuple[int, ...]
    layout: Layout | None
    blocked_layout: BlockedMnkLayout | None
    generation: int


@dataclass(frozen=True)
class ElementRegion:
    origin: tuple[int, ...]
    shape: tuple[int, ...]
    steps: tuple[int, ...]


@dataclass(frozen=True)
class TensorState:
    state_id: int
    object_id: int
    version: int
    origin: StateOrigin
    partial_sum_id: int = 0


@dataclass(frozen=True)
class OperandAccess:
    state_id: int
    view_id: int
    region: ElementRegion
    mode: OperandAccessMode


@dataclass(frozen=True)
class StateTransition:
    old_state_id: int
    new_state_id: int
    view_id: int
    region: ElementRegion
    mode: OperandAccessMode = OperandAccessMode.WRITE


@dataclass(frozen=True)
class ControlToken:
    token_id: int
    initial: bool = False


@dataclass(frozen=True)
class KernelCost:
    logical_input_bytes: int
    logical_output_bytes: int
    local_storage_bytes: int
    macs: int
    vector_ops: int
    reduction_ops: int


@dataclass(frozen=True)
class KernelTile:
    batch_origin: int
    m_origin: int
    n_origin: int
    k_origin: int
    batch_extent: int
    m_extent: int
    n_extent: int
    k_extent: int
    valid_batch: int
    valid_m: int
    valid_n: int
    valid_k: int


@dataclass(frozen=True)
class AllocAttrs:
    object_id: int


@dataclass(frozen=True)
class ViewDeclarationAttrs:
    view_id: int


@dataclass(frozen=True)
class DmaAttrs:
    kind: DmaKind
    issuing_core: int
    source_core: int
    destination_core: int
    transfer_id: int
    fill_pattern: bytes
    max_burst_beats: int | None = None


@dataclass(frozen=True)
class RecvWaitAttrs:
    transfer_id: int
    source_core: int
    destination_core: int
    expected_bytes: int


@dataclass(frozen=True)
class GemmKernelAttrs:
    graph_opcode: OpCode
    semantic_attrs: MatmulAttrs
    tile: KernelTile
    cost: KernelCost
    phase: MatrixPhase
    partial_sum_id: int


@dataclass(frozen=True)
class MatrixEpilogueKernelAttrs:
    graph_opcode: OpCode
    semantic_attrs: MatmulAttrs
    tile: KernelTile
    cost: KernelCost
    algorithm: MatrixEpilogueAlgorithm


@dataclass(frozen=True)
class VectorKernelAttrs:
    graph_opcode: OpCode
    semantic_attrs: ElementwiseAttrs | EmbeddingAttrs
    tile: KernelTile
    cost: KernelCost
    algorithm: VectorAlgorithm


@dataclass(frozen=True)
class MovementKernelAttrs:
    graph_opcode: OpCode
    semantic_attrs: ViewAttrs | MovementAttrs
    tile: KernelTile
    cost: KernelCost
    algorithm: MovementAlgorithm


@dataclass(frozen=True)
class ReductionKernelAttrs:
    graph_opcode: OpCode
    semantic_attrs: ReduceAttrs
    tile: KernelTile
    cost: KernelCost
    algorithm: ReductionAlgorithm


@dataclass(frozen=True)
class SoftmaxKernelAttrs:
    semantic_attrs: SoftmaxAttrs
    tile: KernelTile
    cost: KernelCost
    algorithm: SoftmaxAlgorithm


@dataclass(frozen=True)
class NormKernelAttrs:
    graph_opcode: OpCode
    semantic_attrs: NormAttrs
    tile: KernelTile
    cost: KernelCost
    algorithm: NormAlgorithm


@dataclass(frozen=True)
class CollectiveAttrs:
    kind: CollectiveKind
    participants: tuple[int, ...]
    algorithm: CollectiveAlgorithm
    chunk_bytes: int
    reduce_kind: ReduceKind
    partial_sum_id: int


@dataclass(frozen=True)
class LocalReduceAttrs:
    reduce_kind: ReduceKind
    partial_sum_id: int
    tile: KernelTile
    cost: KernelCost


@dataclass(frozen=True)
class LocalCopyAttrs:
    pass


@dataclass(frozen=True)
class BarrierAttrs:
    participants: tuple[int, ...]


@dataclass(frozen=True)
class PartialSumDefinition:
    partial_sum_id: int
    computation_id: int
    semantic_result_tensor_id: int
    accumulator_tensor_id: int
    placement_id: int


KernelAttrs: TypeAlias = AllocAttrs | ViewDeclarationAttrs | DmaAttrs | RecvWaitAttrs | GemmKernelAttrs | MatrixEpilogueKernelAttrs | VectorKernelAttrs | MovementKernelAttrs | ReductionKernelAttrs | SoftmaxKernelAttrs | NormKernelAttrs | CollectiveAttrs | LocalReduceAttrs | LocalCopyAttrs | BarrierAttrs

KERNEL_ATTR_TYPE_BY_OPCODE = MappingProxyType({
    KernelOpcode.ALLOC: AllocAttrs,
    KernelOpcode.VIEW: ViewDeclarationAttrs,
    KernelOpcode.DMA: DmaAttrs,
    KernelOpcode.GEMM: GemmKernelAttrs,
    KernelOpcode.BMM: GemmKernelAttrs,
    KernelOpcode.MATRIX_EPILOGUE: MatrixEpilogueKernelAttrs,
    KernelOpcode.VECTOR: VectorKernelAttrs,
    KernelOpcode.DATA_MOVEMENT: MovementKernelAttrs,
    KernelOpcode.REDUCE: ReductionKernelAttrs,
    KernelOpcode.SOFTMAX: SoftmaxKernelAttrs,
    KernelOpcode.NORM: NormKernelAttrs,
    KernelOpcode.COLLECTIVE: CollectiveAttrs,
    KernelOpcode.LOCAL_REDUCE: LocalReduceAttrs,
    KernelOpcode.LOCAL_COPY: LocalCopyAttrs,
    KernelOpcode.RECV_WAIT: RecvWaitAttrs,
    KernelOpcode.BARRIER: BarrierAttrs,
})


@dataclass(frozen=True)
class KernelOp:
    op_id: int
    computation_id: int
    stable_key: str
    opcode: KernelOpcode
    owner_core: int
    result_shard_id: int
    reads: tuple[OperandAccess, ...]
    writes: tuple[StateTransition, ...]
    attrs: KernelAttrs
    after_tokens: tuple[int, ...]
    done_token: int | None


@dataclass(frozen=True)
class KernelMemoryRecords:
    tensors: tuple[KernelTensor, ...]
    computations: tuple[KernelComputation, ...]
    placements: tuple[Placement, ...]
    shards: tuple[TensorShard, ...]
    partial_sums: tuple[PartialSumDefinition, ...]
    objects: tuple[BufferObject, ...]
    views: tuple[BufferView, ...]
    states: tuple[TensorState, ...]
    tokens: tuple[ControlToken, ...]
    ops: tuple[KernelOp, ...]


@dataclass(frozen=True)
class VerifiedKernelMemory:
    records: KernelMemoryRecords
    dependencies: "KernelDependencyGraph"


def _record_data(value: object) -> object:
    if type(value) in (Const, Symbol, Add, MulByConst, FloorDivByConst, CeilDivByConst):
        return dimension_data(value)
    if type(value) in (MatmulAttrs, ViewAttrs, MovementAttrs, ElementwiseAttrs, ReduceAttrs, NormAttrs, SoftmaxAttrs, EmbeddingAttrs):
        return operation_attrs_data(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            "kind": type(value).__name__,
            **{
                field.name: _record_data(field_value)
                for field in dataclasses.fields(value)
                if (field_value := getattr(value, field.name)) is not None
            },
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, tuple):
        return [_record_data(item) for item in value]
    return value


@dataclass(frozen=True)
class KernelModule:
    schema_major: int
    schema_minor: int
    required_features: tuple[str, ...]
    arch_digest: str
    source_semantic_hash: str
    semantic_sha256: str
    entrypoint: str
    profile_id: str
    tensors: tuple[KernelTensor, ...]
    computations: tuple[KernelComputation, ...]
    tensor_lineage: tuple[KernelTensorLineage, ...]
    computation_lineage: tuple[KernelComputationLineage, ...]
    placements: tuple[Placement, ...]
    shards: tuple[TensorShard, ...]
    partial_sums: tuple[PartialSumDefinition, ...]
    objects: tuple[BufferObject, ...]
    views: tuple[BufferView, ...]
    states: tuple[TensorState, ...]
    tokens: tuple[ControlToken, ...]
    ops: tuple[KernelOp, ...]

    @classmethod
    def create(
        cls,
        arch_digest: str,
        source_semantic_hash: str,
        entrypoint: str,
        profile_id: str,
        tensors: tuple[KernelTensor, ...],
        computations: tuple[KernelComputation, ...],
        tensor_lineage: tuple[KernelTensorLineage, ...],
        computation_lineage: tuple[KernelComputationLineage, ...],
        placements: tuple[Placement, ...],
        shards: tuple[TensorShard, ...],
        partial_sums: tuple[PartialSumDefinition, ...],
        objects: tuple[BufferObject, ...],
        views: tuple[BufferView, ...],
        states: tuple[TensorState, ...],
        tokens: tuple[ControlToken, ...],
        ops: tuple[KernelOp, ...],
    ) -> "KernelModule":
        provisional = cls(1, 0, (), arch_digest, source_semantic_hash, "", entrypoint, profile_id, tensors, computations, tensor_lineage, computation_lineage, placements, shards, partial_sums, objects, views, states, tokens, ops)
        from mesh_ir.ir.kernel_verify import verify_kernel_types

        verify_kernel_types(provisional, allow_empty_semantic_hash=True)
        kernel = dataclasses.replace(provisional, semantic_sha256=semantic_sha256(provisional.semantic_dict()))
        kernel.verify()
        return kernel

    def semantic_dict(self) -> dict[str, object]:
        return {
            "schema": {"major": self.schema_major, "minor": self.schema_minor},
            "required_features": list(self.required_features),
            "arch_digest": self.arch_digest,
            "source_semantic_hash": self.source_semantic_hash,
            "entrypoint": self.entrypoint,
            "profile_id": self.profile_id,
            "tensors": [_record_data(item) for item in self.tensors],
            "computations": [_record_data(item) for item in self.computations],
            "tensor_lineage": [_record_data(item) for item in self.tensor_lineage],
            "computation_lineage": [_record_data(item) for item in self.computation_lineage],
            "placements": [_record_data(item) for item in self.placements],
            "shards": [_record_data(item) for item in self.shards],
            "partial_sums": [_record_data(item) for item in self.partial_sums],
            "objects": [_record_data(item) for item in self.objects],
            "views": [_record_data(item) for item in self.views],
            "states": [_record_data(item) for item in self.states],
            "tokens": [_record_data(item) for item in self.tokens],
            "ops": [_record_data(item) for item in self.ops],
        }

    def canonical_dict(self) -> dict[str, object]:
        return to_canonical({**self.semantic_dict(), "semantic_sha256": self.semantic_sha256})

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.canonical_dict())

    def verify(self, arch: "ArchManifest | None" = None) -> None:
        from mesh_ir.ir.kernel_verify import verify_kernel

        verify_kernel(self, arch)

    def memory_records(self) -> KernelMemoryRecords:
        return KernelMemoryRecords(self.tensors, self.computations, self.placements, self.shards, self.partial_sums, self.objects, self.views, self.states, self.tokens, self.ops)


@dataclass(frozen=True)
class KernelBundle:
    schema_major: int
    schema_minor: int
    required_features: tuple[str, ...]
    arch_digest: str
    source_graph_set_sha256: str
    semantic_sha256: str
    modules: tuple[KernelModule, ...]

    @classmethod
    def create(
        cls,
        arch_digest: str,
        source_graph_set_sha256: str,
        modules: tuple[KernelModule, ...],
    ) -> "KernelBundle":
        provisional = cls(1, 0, (), arch_digest, source_graph_set_sha256, "", modules)
        bundle = dataclasses.replace(provisional, semantic_sha256=semantic_sha256(provisional.semantic_dict()))
        bundle.verify()
        return bundle

    def semantic_dict(self) -> dict[str, object]:
        return {
            "schema": {"major": self.schema_major, "minor": self.schema_minor},
            "required_features": list(self.required_features),
            "arch_digest": self.arch_digest,
            "source_graph_set_sha256": self.source_graph_set_sha256,
            "modules": [item.canonical_dict() for item in self.modules],
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes({**self.semantic_dict(), "semantic_sha256": self.semantic_sha256})

    def verify(self) -> None:
        if type(self.schema_major) is not int or type(self.schema_minor) is not int or type(self.required_features) is not tuple or any(type(item) is not str for item in self.required_features):
            raise MeshIrError("E_ABI_BOUNDS", "Kernel bundle version fields are invalid")
        if type(self.arch_digest) is not str or type(self.source_graph_set_sha256) is not str or type(self.semantic_sha256) is not str or type(self.modules) is not tuple:
            raise MeshIrError("E_ABI_BOUNDS", "Kernel bundle identity fields are invalid")
        if any(type(item) is not KernelModule or item.arch_digest != self.arch_digest for item in self.modules):
            raise MeshIrError("E_ARCH_DIGEST", "Kernel bundle modules are invalid")
        if self.semantic_sha256 != semantic_sha256(self.semantic_dict()):
            raise MeshIrError("E_ABI_CHECKSUM", "Kernel bundle semantic checksum is stale")
        for module in self.modules:
            module.verify()
