import copy
import dataclasses
import hashlib
import json
from pathlib import Path

import jsonschema
import pytest
from referencing import Registry, Resource

from mesh_ir.architecture import load_arch
from mesh_ir.canonical import U64_MAX, semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Access, Const, DType, DmaKind, Engine, INVALID_CORE_ID, Layout, MemorySpace, StorageClass, TensorRole
from mesh_ir.ir.graph_ir import ElementwiseAttrs, EmbeddingAttrs, MatmulAttrs, MovementAttrs, NormAttrs, OpCode, ReduceAttrs, SoftmaxAttrs, ViewAttrs
from mesh_ir.ir.kernel_ir import (
    AllocAttrs,
    BarrierAttrs,
    BlockedMnkLayout,
    BufferObject,
    BufferView,
    CollectiveAlgorithm,
    CollectiveAttrs,
    CollectiveKind,
    ControlToken,
    DistributionKind,
    DmaAttrs,
    ElementRegion,
    GemmKernelAttrs,
    KernelComputation,
    KernelComputationLineage,
    KernelCost,
    KernelBundle,
    KernelModule,
    KernelOp,
    KernelOpcode,
    KernelTensor,
    KernelTensorLineage,
    KernelTile,
    LocalCopyAttrs,
    MatrixPhase,
    LocalReduceAttrs,
    MatrixEpilogueAlgorithm,
    MatrixEpilogueKernelAttrs,
    MovementAlgorithm,
    MovementKernelAttrs,
    NormAlgorithm,
    NormKernelAttrs,
    OperandAccess,
    OperandAccessMode,
    PartialSumDefinition,
    Placement,
    RecvWaitAttrs,
    ReduceKind,
    ReductionAlgorithm,
    ReductionKernelAttrs,
    SoftmaxAlgorithm,
    SoftmaxKernelAttrs,
    StateOrigin,
    StateTransition,
    TensorShard,
    TensorState,
    SynthesizedTensorPurpose,
    VectorAlgorithm,
    VectorKernelAttrs,
    ViewDeclarationAttrs,
)
from mesh_ir.ir.kernel_verify import KernelCapabilityRequirement, kernel_capability_requirements, verify_kernel_memory, verify_scheduled_ready_kernel
from mesh_ir.schema import load_schema, validate_schema


ROOT = Path(__file__).resolve().parents[4]


def tile(elements=4):
    return KernelTile(0, 0, 0, 0, 1, elements, 1, 1, 1, elements, 1, 1)


def cost(read_bytes=16, write_bytes=16):
    return KernelCost(read_bytes, write_bytes, read_bytes + write_bytes, 0, 4, 0)


def region(elements=4, offset=0, stride=1):
    return ElementRegion((offset,), (elements,), (stride,))


def tensor(tensor_id, name, role, storage_class=StorageClass.CORE_SRAM, producer=0):
    return KernelTensor(tensor_id, producer, None, tensor_id, 0, name, role, DType.FP32, (4,), (1,), storage_class, Access.READ_WRITE, 16, 16, None)


def create_kernel(arch_digest, source_hash, entrypoint, profile_id, tensors, computations, placements, shards, partial_sums, objects, views, states, tokens, ops):
    tensor_lineage = tuple(KernelTensorLineage(item.tensor_id, 0 if item.synthesized_purpose is not None else item.tensor_id) for item in tensors)
    computation_lineage = tuple(KernelComputationLineage(item.computation_id, item.computation_id, f"source:{item.computation_id}") for item in computations)
    return KernelModule.create(arch_digest, source_hash, entrypoint, profile_id, tensors, computations, tensor_lineage, computation_lineage, placements, shards, partial_sums, objects, views, states, tokens, ops)


def shard(shard_id, tensor_id, owner=3, distribution=DistributionKind.PARTITIONED, partial_sum_id=0):
    return TensorShard(shard_id, tensor_id, 1, owner, distribution, (0,), (4,), (4,), partial_sum_id)


def obj(object_id, tensor_id, shard_id, owner, memory_space, generation=0, buffer_index=0):
    return BufferObject(object_id, tensor_id, owner, memory_space, (4,), (1,), 16, 8, False, buffer_index)


def declarations(objects, shard_ids=()):
    allocs = tuple(
        KernelOp(index, 0, f"alloc:{item.object_id}", KernelOpcode.ALLOC, item.owner_core, 0, (), (), AllocAttrs(item.object_id), (), None)
        for index, item in enumerate(objects, 1)
    )
    views = tuple(BufferView(index, item.object_id, shard_ids[index - 1] if shard_ids else item.storage_tensor_id, (0,) * len(item.shape), item.shape, item.shape, 0, item.strides, Layout.CONTIGUOUS_ROW_MAJOR, None, 0) for index, item in enumerate(objects, 1))
    view_ops = tuple(
        KernelOp(len(objects) + index, 0, f"view:{item.view_id}", KernelOpcode.VIEW, objects[index - 1].owner_core, 0, (), (), ViewDeclarationAttrs(item.view_id), (), None)
        for index, item in enumerate(views, 1)
    )
    return views, allocs + view_ops


def load_compute_store_kernel():
    tensors = (
        tensor(1, "input", TensorRole.INPUT, StorageClass.EXTERNAL),
        tensor(2, "output", TensorRole.OUTPUT, StorageClass.EXTERNAL, producer=1),
    )
    shards = (shard(1, 1), shard(2, 2))
    objects = (
        obj(1, 1, 1, INVALID_CORE_ID, MemorySpace.HBM),
        obj(2, 1, 1, 3, MemorySpace.CORE_SRAM),
        obj(3, 2, 2, 3, MemorySpace.CORE_SRAM),
        obj(4, 2, 2, INVALID_CORE_ID, MemorySpace.HBM),
    )
    views, ops = declarations(objects)
    states = (
        TensorState(1, 1, 0, StateOrigin.EXTERNAL, 0),
        TensorState(2, 2, 0, StateOrigin.EMPTY, 0),
        TensorState(3, 2, 1, StateOrigin.PRODUCED, 0),
        TensorState(4, 3, 0, StateOrigin.EMPTY, 0),
        TensorState(5, 3, 1, StateOrigin.PRODUCED, 0),
        TensorState(6, 4, 0, StateOrigin.EMPTY, 0),
        TensorState(7, 4, 1, StateOrigin.PRODUCED, 0),
    )
    tokens = (ControlToken(1), ControlToken(2), ControlToken(3))
    full = region()
    effectful = (
        KernelOp(9, 0, "load", KernelOpcode.DMA, 3, 0, (OperandAccess(1, 1, full, OperandAccessMode.READ),), (StateTransition(2, 3, 2, full),), DmaAttrs(DmaKind.LOAD, 3, INVALID_CORE_ID, 3, 0, b""), (), 1),
        KernelOp(10, 1, "relu", KernelOpcode.VECTOR, 3, 2, (OperandAccess(3, 2, full, OperandAccessMode.READ),), (StateTransition(4, 5, 3, full),), VectorKernelAttrs(OpCode.RELU, ElementwiseAttrs(), tile(), cost(), VectorAlgorithm.ELEMENTWISE), (1,), 2),
        KernelOp(11, 0, "store", KernelOpcode.DMA, 3, 0, (OperandAccess(5, 3, full, OperandAccessMode.READ),), (StateTransition(6, 7, 4, full),), DmaAttrs(DmaKind.STORE, 3, 3, INVALID_CORE_ID, 0, b""), (2,), 3),
    )
    computations = (KernelComputation(1, OpCode.RELU, (1,), 2, ElementwiseAttrs()),)
    return create_kernel(
        "a" * 64,
        "b" * 64,
        "forward",
        "p4",
        tensors,
        computations,
        (Placement(1, (3,)),),
        shards,
        (),
        objects,
        views,
        states,
        tokens,
        ops + effectful,
    )


def local_copy_kernel():
    item = KernelTensor(1, 0, None, 1, 0, "weight", TensorRole.WEIGHT, DType.FP32, (4,), (1,), StorageClass.PRE_RESIDENT, Access.READ_ONLY, 16, 16, "c" * 64)
    shard_ = shard(1, 1)
    objects = (
        obj(1, 1, 1, 3, MemorySpace.CORE_SRAM),
        obj(2, 1, 1, 3, MemorySpace.CORE_SRAM),
    )
    views, ops = declarations(objects, (1, 1))
    states = (
        TensorState(1, 1, 0, StateOrigin.PRE_RESIDENT),
        TensorState(2, 2, 0, StateOrigin.EMPTY),
        TensorState(3, 2, 1, StateOrigin.PRODUCED),
    )
    copy = KernelOp(5, 0, "local-copy", KernelOpcode.LOCAL_COPY, 3, 0, (OperandAccess(1, 1, region(), OperandAccessMode.READ),), (StateTransition(2, 3, 2, region()),), LocalCopyAttrs(), (), 1)
    return create_kernel("a" * 64, "b" * 64, "forward", "p4", (item,), (), (Placement(1, (3,)),), (shard_,), (), objects, views, states, (ControlToken(1),), (*ops, copy))


def real_gemm_kernel(partial_output=False, ordinary_consumer=False):
    input_tensor = KernelTensor(1, 0, None, 1, 0, "lhs", TensorRole.INPUT, DType.FP32, (2, 3), (3, 1), StorageClass.EXTERNAL, Access.READ_ONLY, 24, 24, None)
    weight = KernelTensor(2, 0, None, 2, 0, "rhs", TensorRole.WEIGHT, DType.FP32, (3, 4), (4, 1), StorageClass.PRE_RESIDENT, Access.READ_ONLY, 48, 48, "c" * 64)
    output = KernelTensor(3, 1, None, 3, 0, "out", TensorRole.OUTPUT, DType.FP32, (2, 4), (4, 1), StorageClass.CORE_SRAM, Access.READ_WRITE, 32, 32, None)
    accumulator = KernelTensor(4, 1, SynthesizedTensorPurpose.PARTIAL_SUM, 4, 0, "partial", TensorRole.ACTIVATION, DType.FP32, (2, 4), (4, 1), StorageClass.CORE_SRAM, Access.READ_WRITE, 32, 32, None)
    tensors = (input_tensor, weight, output, accumulator) if partial_output else (input_tensor, weight, output)
    distribution = DistributionKind.PARTIAL_SUM if partial_output else DistributionKind.PARTITIONED
    partial_id = 1 if partial_output else 0
    shards = (
        TensorShard(1, 1, 1, 3, DistributionKind.PARTITIONED, (0, 0), (2, 3), (2, 3), 0),
        TensorShard(2, 2, 1, 3, DistributionKind.PARTITIONED, (0, 0), (3, 4), (3, 4), 0),
        TensorShard(3, 3, 1, 3, DistributionKind.PARTITIONED, (0, 0), (2, 4), (2, 4), 0),
    )
    if partial_output:
        shards += (TensorShard(4, 4, 1, 3, distribution, (0, 0), (2, 4), (2, 4), partial_id),)
    objects = (
        BufferObject(1, 1, INVALID_CORE_ID, MemorySpace.HBM, (2, 3), (3, 1), 24, 8, False, 0),
        BufferObject(2, 1, 3, MemorySpace.CORE_SRAM, (2, 3), (3, 1), 24, 8, False, 0),
        BufferObject(3, 2, 3, MemorySpace.CORE_SRAM, (3, 4), (4, 1), 48, 8, True, 0),
        BufferObject(4, 4 if partial_output else 3, 3, MemorySpace.CORE_SRAM, (2, 4), (4, 1), 32, 8, False, 0),
    )
    views, ops = declarations(objects)
    lhs_region = ElementRegion((0, 0), (2, 3), (1, 1))
    rhs_region = ElementRegion((0, 0), (3, 4), (1, 1))
    out_region = ElementRegion((0, 0), (2, 4), (1, 1))
    states = (
        TensorState(1, 1, 0, StateOrigin.EXTERNAL, 0),
        TensorState(2, 2, 0, StateOrigin.EMPTY, 0),
        TensorState(3, 2, 1, StateOrigin.PRODUCED, 0),
        TensorState(4, 3, 0, StateOrigin.PRE_RESIDENT, 0),
        TensorState(5, 4, 0, StateOrigin.EMPTY, 0),
        TensorState(6, 4, 1, StateOrigin.PRODUCED, partial_id),
    )
    tokens = (ControlToken(1), ControlToken(2))
    effects = (
        KernelOp(9, 0, "load:lhs", KernelOpcode.DMA, 3, 0, (OperandAccess(1, 1, lhs_region, OperandAccessMode.READ),), (StateTransition(2, 3, 2, lhs_region),), DmaAttrs(DmaKind.LOAD, 3, INVALID_CORE_ID, 3, 0, b""), (), 1),
        KernelOp(10, 1, "gemm:1", KernelOpcode.GEMM, 3, 4 if partial_output else 3, (OperandAccess(3, 2, lhs_region, OperandAccessMode.READ), OperandAccess(4, 3, rhs_region, OperandAccessMode.READ)), (StateTransition(5, 6, 4, out_region),), GemmKernelAttrs(OpCode.MATMUL, MatmulAttrs(), KernelTile(0, 0, 0, 0, 1, 2, 4, 3, 1, 2, 4, 3), KernelCost(72, 32, 104, 24, 0, 0), MatrixPhase.ACCUMULATE_ONLY if partial_output else MatrixPhase.DIRECT, partial_id), (1,), 2),
    )
    if ordinary_consumer:
        states += (TensorState(7, 4, 2, StateOrigin.PRODUCED, 0),)
        tokens += (ControlToken(3),)
        effects += (KernelOp(11, 1, "relu:out", KernelOpcode.VECTOR, 3, 4, (OperandAccess(6, 4, out_region, OperandAccessMode.READ),), (StateTransition(6, 7, 4, out_region),), VectorKernelAttrs(OpCode.RELU, ElementwiseAttrs(), tile(8), KernelCost(32, 32, 64, 0, 8, 0), VectorAlgorithm.ELEMENTWISE), (2,), 3),)
    computations = (KernelComputation(1, OpCode.MATMUL, (1, 2), 3, MatmulAttrs()),)
    partials = (PartialSumDefinition(1, 1, 3, 4, 1),) if partial_output else ()
    return create_kernel("a" * 64, "b" * 64, "forward", "p4", tensors, computations, (Placement(1, (3,)),), shards, partials, objects, views, states, tokens, ops + effects)


def semantic_operation_kernel(opcode, attrs, operand_specs, result_spec):
    specs = (*operand_specs, result_spec)
    tensors = []
    shards = []
    objects = []
    for index, (shape, dtype) in enumerate(specs, 1):
        stride = 1
        strides = []
        for dimension in reversed(shape):
            strides.append(stride)
            stride *= dimension
        strides = tuple(reversed(strides))
        elements = 1
        for dimension in shape:
            elements *= dimension
        size = elements * dtype.byte_width
        operand = index <= len(operand_specs)
        tensors.append(KernelTensor(index, 0 if operand else 1, None, index, 0, f"value:{index}", TensorRole.WEIGHT if operand else TensorRole.ACTIVATION, dtype, shape, strides, StorageClass.PRE_RESIDENT if operand else StorageClass.CORE_SRAM, Access.READ_ONLY if operand else Access.READ_WRITE, size, size, f"{index:064x}" if operand else None))
        shards.append(TensorShard(index, index, 1, 3, DistributionKind.PARTITIONED, (0,) * len(shape), shape, shape, 0))
        objects.append(BufferObject(index, index, 3, MemorySpace.CORE_SRAM, shape, strides, size, 8, False, 0))
    objects = tuple(objects)
    views, declarations_ = declarations(objects)
    states = tuple(TensorState(index, index, 0, StateOrigin.PRE_RESIDENT, 0) for index in range(1, len(operand_specs) + 1)) + (
        TensorState(len(operand_specs) + 1, len(objects), 0, StateOrigin.EMPTY, 0),
        TensorState(len(operand_specs) + 2, len(objects), 1, StateOrigin.PRODUCED, 0),
    )
    reads = tuple(OperandAccess(index, index, ElementRegion((0,) * len(objects[index - 1].shape), objects[index - 1].shape, (1,) * len(objects[index - 1].shape)), OperandAccessMode.READ) for index in range(1, len(operand_specs) + 1))
    result_id = len(objects)
    write = StateTransition(len(operand_specs) + 1, len(operand_specs) + 2, result_id, ElementRegion((0,) * len(objects[-1].shape), objects[-1].shape, (1,) * len(objects[-1].shape)))
    effect = KernelOp(len(declarations_) + 1, 1, f"semantic:{opcode.value}", opcode, 3, result_id, reads, (write,), attrs, (), 1)
    graph_opcode = OpCode.SOFTMAX if opcode is KernelOpcode.SOFTMAX else attrs.graph_opcode
    computation = KernelComputation(1, graph_opcode, tuple(range(1, len(operand_specs) + 1)), result_id, attrs.semantic_attrs)
    return create_kernel("a" * 64, "b" * 64, "forward", "semantic", tuple(tensors), (computation,), (Placement(1, (3,)),), tuple(shards), (), objects, views, states, (ControlToken(1),), declarations_ + (effect,))


def accumulation_gemm_kernel():
    kernel = real_gemm_kernel()
    semantic_tensors = (
        dataclasses.replace(kernel.tensors[0], dtype=DType.FP16, logical_extent_bytes=12, storage_extent_bytes=12),
        dataclasses.replace(kernel.tensors[1], dtype=DType.FP16, logical_extent_bytes=24, storage_extent_bytes=24),
        dataclasses.replace(kernel.tensors[2], dtype=DType.FP16, logical_extent_bytes=16, storage_extent_bytes=16),
    )
    accumulator = KernelTensor(4, 1, SynthesizedTensorPurpose.ACCUMULATION, 4, 0, "accumulator", TensorRole.ACTIVATION, DType.FP32, (2, 4), (4, 1), StorageClass.CORE_SRAM, Access.READ_WRITE, 32, 32, None)
    tensors = (*semantic_tensors, accumulator)
    shards = (*kernel.shards, TensorShard(4, 4, 1, 3, DistributionKind.PARTITIONED, (0, 0), (2, 4), (2, 4), 0))
    objects = (
        dataclasses.replace(kernel.objects[0], footprint_bytes=12),
        dataclasses.replace(kernel.objects[1], footprint_bytes=12),
        dataclasses.replace(kernel.objects[2], footprint_bytes=24),
        dataclasses.replace(kernel.objects[3], storage_tensor_id=4),
    )
    views = (*kernel.views[:3], dataclasses.replace(kernel.views[3], shard_id=4))
    gemm = kernel.ops[-1]
    attrs = dataclasses.replace(gemm.attrs, cost=KernelCost(36, 32, 68, 24, 0, 0), phase=MatrixPhase.ACCUMULATE_ONLY)
    return create_kernel(kernel.arch_digest, kernel.source_semantic_hash, kernel.entrypoint, kernel.profile_id, tensors, kernel.computations, kernel.placements, shards, kernel.partial_sums, objects, views, kernel.states, kernel.tokens, (*kernel.ops[:-1], dataclasses.replace(gemm, result_shard_id=4, attrs=attrs)))


def refreshed(kernel):
    return dataclasses.replace(kernel, semantic_sha256=semantic_sha256(kernel.semantic_dict()))


def rejects(kernel, code):
    with pytest.raises(MeshIrError) as error:
        refreshed(kernel).verify()
    assert error.value.code == code


def test_typed_load_compute_store_is_canonical_schema_valid_and_verified():
    kernel = load_compute_store_kernel()
    kernel.verify()
    validate_schema("mesh_kernel_v1.schema.json", kernel.canonical_dict(), "Kernel IR")
    assert kernel.semantic_sha256 == semantic_sha256(kernel.semantic_dict())
    assert kernel.semantic_dict()["ops"][9]["attrs"]["semantic_attrs"]["alpha"] == 1.0
    assert "scalar" not in kernel.semantic_dict()["ops"][9]["attrs"]["semantic_attrs"]


def test_kernel_completion_canonical_boundary_omits_only_declaration_absence():
    kernel = load_compute_store_kernel()
    semantic = kernel.semantic_dict()
    declarations = tuple(item for item in semantic["ops"] if item["opcode"] in ("ALLOC", "VIEW"))
    effects = tuple(item for item in semantic["ops"] if item["opcode"] not in ("ALLOC", "VIEW"))
    assert declarations and all("done_token" not in item for item in declarations)
    assert tuple(item["done_token"] for item in effects) == (1, 2, 3)
    independent_module_hash = hashlib.sha256(
        json.dumps(
            semantic,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    assert kernel.semantic_sha256 == independent_module_hash == semantic_sha256(semantic)
    bundle = KernelBundle.create(kernel.arch_digest, "d" * 64, (kernel,))
    independent_bundle_hash = hashlib.sha256(
        json.dumps(
            bundle.semantic_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    assert bundle.semantic_sha256 == independent_bundle_hash == semantic_sha256(bundle.semantic_dict())
    historical = copy.deepcopy(semantic)
    for item in historical["ops"]:
        if item["opcode"] in ("ALLOC", "VIEW"):
            item["done_token"] = None
    assert independent_module_hash != hashlib.sha256(
        json.dumps(
            historical,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    explicit_null = copy.deepcopy(kernel.canonical_dict())
    explicit_null["ops"][0]["done_token"] = None
    with pytest.raises(MeshIrError) as schema_error:
        validate_schema("mesh_kernel_v1.schema.json", explicit_null, "Kernel IR")
    assert schema_error.value.code == "E_CONFIG"
    assert schema_error.value.context["path"] == "ops.0.done_token"
    effect_index = next(index for index, item in enumerate(kernel.ops) if item.opcode not in (KernelOpcode.ALLOC, KernelOpcode.VIEW))
    missing = dataclasses.replace(kernel.ops[effect_index], done_token=None)
    changed = refreshed(dataclasses.replace(kernel, ops=(*kernel.ops[:effect_index], missing, *kernel.ops[effect_index + 1 :])))
    with pytest.raises(MeshIrError) as completion_error:
        changed.verify()
    assert completion_error.value.code == "E_EVENT_NO_PRODUCER"
    assert completion_error.value.message == "effectful operation lacks a completion token"


def test_local_copy_is_source_neutral_schema_valid_and_verified():
    kernel = local_copy_kernel()
    validate_schema("mesh_kernel_v1.schema.json", kernel.canonical_dict(), "Kernel IR")
    assert kernel.ops[-1].computation_id == 0
    assert kernel.ops[-1].result_shard_id == 0
    assert kernel.semantic_dict()["ops"][-1]["attrs"] == {"kind": "LocalCopyAttrs"}


def test_source_neutral_memory_projection_reuses_the_exact_intrinsic_tuples():
    kernel = load_compute_store_kernel()
    records = kernel.memory_records()
    verified = verify_kernel_memory(records)
    assert records.tensors is kernel.tensors
    assert records.computations is kernel.computations
    assert records.ops is kernel.ops
    assert verified.records is records
    assert verified.dependencies.op_node_by_id


def test_physical_compute_operands_preserve_logical_computation_identity():
    kernel = semantic_operation_kernel(
        KernelOpcode.VECTOR,
        VectorKernelAttrs(OpCode.ADD, ElementwiseAttrs(), tile(), KernelCost(32, 16, 48, 0, 4, 0), VectorAlgorithm.ELEMENTWISE),
        (((4,), DType.FP32), ((4,), DType.FP32)),
        ((4,), DType.FP32),
    )
    compute = kernel.ops[-1]
    swapped = dataclasses.replace(compute, reads=tuple(reversed(compute.reads)))
    rejects(dataclasses.replace(kernel, ops=(*kernel.ops[:-1], swapped)), "E_ABI_CHECKSUM")


def test_initial_backing_storage_identity_matches_every_logical_view_root():
    resident = semantic_operation_kernel(
        KernelOpcode.VECTOR,
        VectorKernelAttrs(OpCode.ADD, ElementwiseAttrs(), tile(), KernelCost(32, 16, 48, 0, 4, 0), VectorAlgorithm.ELEMENTWISE),
        (((4,), DType.FP32), ((4,), DType.FP32)),
        ((4,), DType.FP32),
    )
    wrong_resident = dataclasses.replace(resident.objects[0], storage_tensor_id=2)
    rejects(dataclasses.replace(resident, objects=(wrong_resident, *resident.objects[1:])), "E_TENSOR_NOT_RESIDENT")
    external = load_compute_store_kernel()
    wrong_external = dataclasses.replace(external.objects[0], storage_tensor_id=2)
    rejects(dataclasses.replace(external, objects=(wrong_external, *external.objects[1:])), "E_TENSOR_NOT_RESIDENT")


def test_kernel_schema_reuses_the_graph_attribute_definitions():
    schema = json.loads((ROOT / "util/mesh_ir/mesh_ir/schemas/mesh_kernel_v1.schema.json").read_text(encoding="utf-8"))
    assert not ({"matmul_semantics", "elementwise_semantics", "embedding_semantics", "view_semantics", "movement_semantics", "reduce_semantics", "softmax_semantics", "norm_semantics"} & schema["$defs"].keys())
    assert schema["$defs"]["gemm_attrs"]["properties"]["semantic_attrs"]["$ref"] == "mesh-graph-v1#/$defs/matmul_attrs"
    assert schema["$defs"]["movement_attrs"]["properties"]["semantic_attrs"]["oneOf"][0]["$ref"] == "mesh-graph-v1#/$defs/view_attrs"


def test_kernel_schema_dispatches_each_attribute_tag_to_one_complete_definition():
    gemm = real_gemm_kernel().ops[-1].attrs
    attrs = (
        AllocAttrs(1),
        ViewDeclarationAttrs(1),
        DmaAttrs(DmaKind.PREFETCH, 3, INVALID_CORE_ID, 3, 0, b""),
        RecvWaitAttrs(1, 3, 7, 16),
        gemm,
        MatrixEpilogueKernelAttrs(OpCode.LINEAR_BIAS, gemm.semantic_attrs, gemm.tile, gemm.cost, MatrixEpilogueAlgorithm.VECTOR_ACCUMULATION),
        VectorKernelAttrs(OpCode.RELU, ElementwiseAttrs(), tile(), cost(), VectorAlgorithm.ELEMENTWISE),
        MovementKernelAttrs(OpCode.CONTIGUOUS_COPY, ViewAttrs(shape=(Const(4),)), tile(), cost(), MovementAlgorithm.STRIDED_COPY),
        ReductionKernelAttrs(OpCode.REDUCE_SUM, ReduceAttrs((0,), False, DType.FP32, DType.FP32), tile(1), KernelCost(16, 4, 20, 0, 0, 3), ReductionAlgorithm.LEFT_TO_RIGHT),
        SoftmaxKernelAttrs(SoftmaxAttrs(0, DType.FP32, True), tile(), KernelCost(16, 16, 32, 0, 20, 9), SoftmaxAlgorithm.STABLE_MAX_SUM),
        NormKernelAttrs(OpCode.LAYERNORM, NormAttrs((0,), 1e-5, False, False), tile(), KernelCost(16, 16, 32, 0, 16, 6), NormAlgorithm.LAYER_NORM),
        CollectiveAttrs(CollectiveKind.ALL_REDUCE, (3, 7), CollectiveAlgorithm.RING, 16, ReduceKind.SUM, 1),
        LocalReduceAttrs(ReduceKind.SUM, 1, tile(), KernelCost(32, 16, 48, 0, 0, 4)),
        LocalCopyAttrs(),
        BarrierAttrs((3, 7)),
    )
    kernel = load_compute_store_kernel()
    payloads = tuple(
        dataclasses.replace(kernel, ops=(dataclasses.replace(kernel.ops[-1], attrs=item),)).canonical_dict()["ops"][0]["attrs"]
        for item in attrs
    )
    expected = tuple(f"#/$defs/{name}" for name in ("alloc_attrs", "view_attrs", "dma_attrs", "recv_attrs", "gemm_attrs", "matrix_epilogue_attrs", "vector_attrs", "movement_attrs", "reduction_attrs", "softmax_attrs", "norm_attrs", "collective_attrs", "local_reduce_attrs", "local_copy_attrs", "barrier_attrs"))
    schema = load_schema("mesh_kernel_v1.schema.json")
    graph_schema = load_schema("mesh_graph_v1.schema.json")
    probe = {"$schema": schema["$schema"], "$id": "mesh-kernel-attrs-probe", "$defs": schema["$defs"], "$ref": "#/$defs/attrs"}
    base = jsonschema.validators.validator_for(probe)
    reference = base.VALIDATORS["$ref"]
    visited = []

    def count_reference(validator, ref, instance, current_schema):
        if ref in expected:
            visited.append(ref)
        yield from reference(validator, ref, instance, current_schema)

    validator_type = jsonschema.validators.extend(base, {"$ref": count_reference})
    validator = validator_type(probe, registry=Registry().with_resource("mesh-graph-v1", Resource.from_contents(graph_schema)))
    for payload, selected in zip(payloads, expected):
        visited.clear()
        assert not tuple(validator.iter_errors(payload))
        assert tuple(visited) == (selected,)
    for payload in payloads:
        missing = dict(payload)
        del missing[next((key for key in payload if key != "kind"), "kind")]
        unexpected = dict(payload, unexpected=1)
        assert tuple(validator.iter_errors(missing))
        assert tuple(validator.iter_errors(unexpected))
    assert tuple(validator.iter_errors({"kind": "UnknownAttrs"}))
    assert tuple(validator.iter_errors({"object_id": 1}))
    assert tuple(validator.iter_errors({"kind": "AllocAttrs", "object_id": 1, "view_id": 1}))
    malformed = dict(payloads[4])
    malformed["semantic_attrs"] = dict(malformed["semantic_attrs"], alpha=None)
    assert tuple(validator.iter_errors(malformed))
    malformed = dict(payloads[2], transfer_id=-1)
    assert tuple(validator.iter_errors(malformed))
    before = kernel.semantic_sha256
    validate_schema("mesh_kernel_v1.schema.json", kernel.canonical_dict(), "Kernel IR")
    assert kernel.semantic_sha256 == before == semantic_sha256(kernel.semantic_dict())


def test_real_gemm_has_shape_consistent_operands_tile_and_cost():
    kernel = real_gemm_kernel()
    kernel.verify()
    attrs = kernel.ops[9].attrs
    assert attrs.tile == KernelTile(0, 0, 0, 0, 1, 2, 4, 3, 1, 2, 4, 3)
    assert attrs.cost.macs == 24


def test_stale_checksum_is_not_repaired_by_verification():
    kernel = load_compute_store_kernel()
    stale = dataclasses.replace(kernel, profile_id="p8")
    with pytest.raises(MeshIrError) as error:
        stale.verify()
    assert error.value.code == "E_ABI_CHECKSUM"
    assert stale.semantic_sha256 == kernel.semantic_sha256


def test_missing_load_fails_with_fresh_hash_on_empty_state_read():
    kernel = load_compute_store_kernel()
    compute = dataclasses.replace(kernel.ops[9], reads=(OperandAccess(2, 2, region(), OperandAccessMode.READ),), after_tokens=())
    rejects(dataclasses.replace(kernel, ops=(*kernel.ops[:9], compute, *kernel.ops[10:])), "E_TENSOR_NOT_RESIDENT")


def test_state_transition_rejects_skipped_version_and_different_object():
    kernel = load_compute_store_kernel()
    skipped = dataclasses.replace(kernel.states[2], version=2)
    rejects(dataclasses.replace(kernel, states=(*kernel.states[:2], skipped, *kernel.states[3:])), "E_ABI_ORDER")
    foreign = dataclasses.replace(kernel.ops[8].writes[0], new_state_id=5)
    rejects(dataclasses.replace(kernel, ops=(*kernel.ops[:8], dataclasses.replace(kernel.ops[8], writes=(foreign,)), *kernel.ops[9:])), "E_ABI_BOUNDS")


def test_safe_view_shares_root_without_creating_an_object():
    kernel = load_compute_store_kernel()
    alias = BufferView(5, 2, 1, (0,), (4,), (4,), 0, (1,), Layout.CONTIGUOUS_ROW_MAJOR, None, 1)
    view_op = KernelOp(9, 0, "view:5", KernelOpcode.VIEW, 3, 0, (), (), ViewDeclarationAttrs(5), (), None)
    effects = tuple(dataclasses.replace(op, op_id=op.op_id + 1) for op in kernel.ops[8:])
    extended = create_kernel(
        kernel.arch_digest,
        kernel.source_semantic_hash,
        kernel.entrypoint,
        kernel.profile_id,
        kernel.tensors,
        kernel.computations,
        kernel.placements,
        kernel.shards,
        kernel.partial_sums,
        kernel.objects,
        (*kernel.views, alias),
        kernel.states,
        kernel.tokens,
        (*kernel.ops[:8], view_op, *effects),
    )
    extended.verify()
    assert len(extended.objects) == 4
    assert extended.views[-1].object_id == 2


def partial_write_kernel(second_elements=2):
    tensors = (tensor(1, "partial", TensorRole.ACTIVATION, producer=1),)
    shards = (shard(1, 1),)
    objects = (obj(1, 1, 1, 3, MemorySpace.CORE_SRAM),)
    views, ops = declarations(objects, (1, 2))
    states = (
        TensorState(1, 1, 0, StateOrigin.EMPTY, 0),
        TensorState(2, 1, 1, StateOrigin.PRODUCED, 0),
        TensorState(3, 1, 2, StateOrigin.PRODUCED, 0),
        TensorState(4, 1, 3, StateOrigin.PRODUCED, 0),
    )
    tokens = tuple(ControlToken(index) for index in range(1, 4))
    first = region(2, 0)
    second = region(second_elements, 2)
    full = region(4, 0)
    effectful = (
        KernelOp(3, 0, "fill:0", KernelOpcode.DMA, 3, 0, (), (StateTransition(1, 2, 1, first),), DmaAttrs(DmaKind.LOCAL_FILL, 3, 3, 3, 0, b"\x00"), (), 1),
        KernelOp(4, 0, "fill:1", KernelOpcode.DMA, 3, 0, (), (StateTransition(2, 3, 1, second),), DmaAttrs(DmaKind.LOCAL_FILL, 3, 3, 3, 0, b"\x01"), (1,), 2),
        KernelOp(5, 1, "read", KernelOpcode.VECTOR, 3, 1, (OperandAccess(3, 1, full, OperandAccessMode.READ),), (StateTransition(3, 4, 1, full),), VectorKernelAttrs(OpCode.RELU, ElementwiseAttrs(), tile(), cost(), VectorAlgorithm.ELEMENTWISE), (2,), 3),
    )
    computations = (KernelComputation(1, OpCode.RELU, (1,), 1, ElementwiseAttrs()),)
    return create_kernel("a" * 64, "b" * 64, "forward", "p4", tensors, computations, (Placement(1, (3,)),), shards, (), objects, views, states, tokens, ops + effectful)


def test_disjoint_partial_writes_union_to_a_fully_initialized_read():
    partial_write_kernel().verify()


def test_incomplete_partial_initialization_rejects_full_read():
    with pytest.raises(MeshIrError) as error:
        partial_write_kernel(1)
    assert error.value.code == "E_TENSOR_NOT_RESIDENT"


def test_out_of_bounds_view_and_blocked_layout_without_parameters_reject():
    kernel = load_compute_store_kernel()
    view = dataclasses.replace(kernel.views[1], object_offset_elements=4)
    rejects(dataclasses.replace(kernel, views=(kernel.views[0], view, *kernel.views[2:])), "E_EXPORT_LAYOUT")
    blocked = dataclasses.replace(kernel.views[1], layout=Layout.BLOCKED_MNK, blocked_layout=None)
    rejects(dataclasses.replace(kernel, views=(kernel.views[0], blocked, *kernel.views[2:])), "E_EXPORT_LAYOUT")
    BlockedMnkLayout(4, 8, 16, (2, 1, 0))


def test_partial_sum_cannot_feed_ordinary_vector_consumer():
    with pytest.raises(MeshIrError) as error:
        real_gemm_kernel(partial_output=True, ordinary_consumer=True)
    assert error.value.code == "E_TENSOR_NOT_RESIDENT"


def test_p2p_transfer_and_receive_wait_require_a_unique_bijection():
    tensor_record = KernelTensor(1, 0, None, 1, 0, "replica", TensorRole.WEIGHT, DType.FP32, (4,), (1,), StorageClass.PRE_RESIDENT, Access.READ_ONLY, 16, 16, "c" * 64)
    shards = (shard(1, 1, 3, DistributionKind.REPLICATED), shard(2, 1, 7, DistributionKind.REPLICATED))
    objects = (obj(1, 1, 1, 3, MemorySpace.CORE_SRAM), obj(2, 1, 2, 7, MemorySpace.CORE_SRAM))
    views, declarations_ = declarations(objects, (1, 2))
    states = (TensorState(1, 1, 0, StateOrigin.PRE_RESIDENT, 0), TensorState(2, 2, 0, StateOrigin.EMPTY, 0), TensorState(3, 2, 1, StateOrigin.PRODUCED, 0))
    p2p = KernelOp(5, 0, "p2p:11", KernelOpcode.DMA, 3, 0, (OperandAccess(1, 1, region(), OperandAccessMode.READ),), (StateTransition(2, 3, 2, region()),), DmaAttrs(DmaKind.P2P_PUSH, 3, 3, 7, 11, b""), (), 1)
    receive = KernelOp(6, 0, "recv:11", KernelOpcode.RECV_WAIT, 7, 0, (), (), RecvWaitAttrs(11, 3, 7, 16), (1,), 2)
    matched = create_kernel("a" * 64, "b" * 64, "forward", "p4", (tensor_record,), (), (Placement(1, (3, 7)),), shards, (), objects, views, states, (ControlToken(1), ControlToken(2)), declarations_ + (p2p, receive))
    matched.verify()
    missing = refreshed(dataclasses.replace(matched, tokens=matched.tokens[:-1], ops=matched.ops[:-1]))
    assert missing.semantic_sha256 != matched.semantic_sha256
    rejects(missing, "E_P2P_UNMATCHED")
    duplicate = dataclasses.replace(receive, op_id=7, stable_key="recv:11:duplicate", done_token=3)
    rejects(dataclasses.replace(matched, tokens=(*matched.tokens, ControlToken(3)), ops=(*matched.ops, duplicate)), "E_P2P_UNMATCHED")


def test_scalar_and_zero_extent_remain_distinct_and_typed_boundary_rejects_coercion():
    assert ElementRegion((), (), ()).shape == ()
    assert ElementRegion((0,), (0,), (1,)).shape == (0,)
    kernel = load_compute_store_kernel()
    malformed = dataclasses.replace(kernel.tensors[0], dtype=int(DType.FP32))
    with pytest.raises(MeshIrError):
        refreshed(dataclasses.replace(kernel, tensors=(malformed, *kernel.tensors[1:]))).verify()


def test_missing_duplicate_producer_and_cycle_have_stable_codes():
    kernel = load_compute_store_kernel()
    missing = dataclasses.replace(kernel.tokens[0], token_id=4)
    no_producer = dataclasses.replace(kernel.ops[8], done_token=4)
    rejects(dataclasses.replace(kernel, tokens=(missing, *kernel.tokens[1:]), ops=(*kernel.ops[:8], no_producer, *kernel.ops[9:])), "E_ABI_ORDER")
    orphan = dataclasses.replace(kernel, tokens=(*kernel.tokens, ControlToken(4)))
    rejects(orphan, "E_EVENT_NO_PRODUCER")
    duplicate = dataclasses.replace(kernel.ops[9], done_token=1)
    rejects(dataclasses.replace(kernel, ops=(*kernel.ops[:9], duplicate, *kernel.ops[10:])), "E_EVENT_MULTIPLE_PRODUCERS")
    cycle = dataclasses.replace(kernel.ops[8], after_tokens=(3,))
    rejects(dataclasses.replace(kernel, ops=(*kernel.ops[:8], cycle, *kernel.ops[9:])), "E_DEPENDENCY_CYCLE")


def test_scheduled_ready_is_separate_and_architecture_required():
    kernel = load_compute_store_kernel()
    with pytest.raises(MeshIrError) as error:
        verify_scheduled_ready_kernel(kernel, None)
    assert error.value.code == "E_CONFIG"


def test_local_reduce_cannot_claim_partial_sum_completion_without_dataflow_proof():
    kernel = real_gemm_kernel(partial_output=True)
    states = (*kernel.states, TensorState(7, 4, 2, StateOrigin.PRODUCED, 0))
    tokens = (*kernel.tokens, ControlToken(3))
    output = ElementRegion((0, 0), (2, 4), (1, 1))
    reduce = KernelOp(
        11,
        1,
        "local-sum:1",
        KernelOpcode.LOCAL_REDUCE,
        3,
        4,
        (OperandAccess(6, 4, output, OperandAccessMode.READ), OperandAccess(6, 4, output, OperandAccessMode.READ)),
        (StateTransition(6, 7, 4, output),),
        LocalReduceAttrs(ReduceKind.SUM, 1, tile(8), KernelCost(64, 32, 96, 0, 0, 8)),
        (2,),
        3,
    )
    rejects(dataclasses.replace(kernel, states=states, tokens=tokens, ops=(*kernel.ops, reduce)), "E_TENSOR_NOT_RESIDENT")


def test_p2p_propagates_partial_sum_identity_to_the_actual_peer_state():
    source = real_gemm_kernel(partial_output=True)
    placements = (Placement(1, (3,)), Placement(2, (7,)))
    shards = (*source.shards, TensorShard(5, 4, 2, 7, DistributionKind.PARTITIONED, (0, 0), (2, 4), (2, 4), 0))
    peer = BufferObject(5, 4, 7, MemorySpace.CORE_SRAM, (2, 4), (4, 1), 32, 8, False, 0)
    objects = (*source.objects, peer)
    views, declarations_ = declarations(objects, (1, 1, 2, 4, 5))
    states = (*source.states, TensorState(7, 5, 0, StateOrigin.EMPTY, 0), TensorState(8, 5, 1, StateOrigin.PRODUCED, 1))
    tokens = (*source.tokens, ControlToken(3), ControlToken(4))
    original_effects = tuple(dataclasses.replace(op, op_id=op.op_id + 2) for op in source.ops[8:])
    output = ElementRegion((0, 0), (2, 4), (1, 1))
    send = KernelOp(13, 0, "p2p:31", KernelOpcode.DMA, 3, 0, (OperandAccess(6, 4, output, OperandAccessMode.READ),), (StateTransition(7, 8, 5, output),), DmaAttrs(DmaKind.P2P_PUSH, 3, 3, 7, 31, b""), (2,), 3)
    receive = KernelOp(14, 0, "recv:31", KernelOpcode.RECV_WAIT, 7, 0, (), (), RecvWaitAttrs(31, 3, 7, 32), (3,), 4)
    kernel = create_kernel(source.arch_digest, source.source_semantic_hash, source.entrypoint, source.profile_id, source.tensors, source.computations, placements, shards, source.partial_sums, objects, views, states, tokens, declarations_ + original_effects + (send, receive))
    kernel.verify()
    lost = dataclasses.replace(kernel.states[7], partial_sum_id=0)
    rejects(dataclasses.replace(kernel, states=(*kernel.states[:7], lost)), "E_TENSOR_NOT_RESIDENT")


def test_scheduled_ready_rejects_every_unresolved_partial_sum_state():
    kernel = real_gemm_kernel(partial_output=True)
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    kernel = refreshed(dataclasses.replace(kernel, arch_digest=arch.digest().hex()))
    with pytest.raises(MeshIrError) as error:
        verify_scheduled_ready_kernel(kernel, arch)
    assert error.value.code == "E_TENSOR_NOT_RESIDENT"


def test_architecture_row_major_order_is_not_numeric_core_order():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_2x2.yaml")
    tensor_record = KernelTensor(1, 0, None, 1, 0, "resident", TensorRole.WEIGHT, DType.FP32, (4,), (1,), StorageClass.PRE_RESIDENT, Access.READ_ONLY, 16, 16, "c" * 64)
    shards = (shard(1, 1, 7, DistributionKind.REPLICATED), shard(2, 1, 2, DistributionKind.REPLICATED))
    objects = (obj(1, 1, 1, 7, MemorySpace.CORE_SRAM), obj(2, 1, 2, 2, MemorySpace.CORE_SRAM))
    views, ops = declarations(objects, (1, 2))
    states = (TensorState(1, 1, 0, StateOrigin.PRE_RESIDENT, 0), TensorState(2, 2, 0, StateOrigin.PRE_RESIDENT, 0))
    kernel = create_kernel(arch.digest().hex(), "b" * 64, "forward", "p4", (tensor_record,), (), (Placement(1, (7, 2)),), shards, (), objects, views, states, (), ops)
    kernel.verify(arch)
    reordered = dataclasses.replace(kernel.placements[0], core_ids=(2, 7))
    with pytest.raises(MeshIrError) as error:
        refreshed(dataclasses.replace(kernel, placements=(reordered,))).verify(arch)
    assert error.value.code == "E_PLACEMENT_INFEASIBLE"


def test_compute_cannot_read_external_memory_or_use_another_cores_sram():
    gemm_kernel = real_gemm_kernel()
    gemm = gemm_kernel.ops[-1]
    remote_lhs = dataclasses.replace(gemm.reads[0], state_id=1, view_id=1)
    rejects(dataclasses.replace(gemm_kernel, ops=(*gemm_kernel.ops[:-1], dataclasses.replace(gemm, reads=(remote_lhs, *gemm.reads[1:])))), "E_TENSOR_NOT_RESIDENT")
    vector_kernel = load_compute_store_kernel()
    compute = vector_kernel.ops[-2]
    rejects(dataclasses.replace(vector_kernel, ops=(*vector_kernel.ops[:-2], dataclasses.replace(compute, owner_core=7), vector_kernel.ops[-1])), "E_TENSOR_NOT_RESIDENT")


def test_partitioned_shards_exactly_cover_the_logical_tensor():
    kernel = load_compute_store_kernel()
    enlarged = dataclasses.replace(kernel.tensors[0], shape=(8,), logical_extent_bytes=32, storage_extent_bytes=32)
    rejects(dataclasses.replace(kernel, tensors=(enlarged, *kernel.tensors[1:])), "E_EXPORT_LAYOUT")


def test_ordering_an_old_state_after_its_overwrite_does_not_make_it_current():
    kernel = load_compute_store_kernel()
    load, compute, store = kernel.ops[-3:]
    overwritten = TensorState(8, 2, 2, StateOrigin.PRODUCED, 0)
    fill = KernelOp(10, 0, "overwrite", KernelOpcode.DMA, 3, 0, (), (StateTransition(3, 8, 2, load.writes[0].region),), DmaAttrs(DmaKind.LOCAL_FILL, 3, 3, 3, 0, b"\x00"), (1,), 4)
    stale_compute = dataclasses.replace(compute, op_id=11, after_tokens=(4,))
    final_store = dataclasses.replace(store, op_id=12)
    altered = dataclasses.replace(kernel, states=(*kernel.states, overwritten), tokens=(*kernel.tokens, ControlToken(4)), ops=(*kernel.ops[:-3], load, fill, stale_compute, final_store))
    rejects(altered, "E_TENSOR_NOT_RESIDENT")


def test_vector_arity_follows_the_exact_graph_semantic_family():
    kernel = load_compute_store_kernel()
    compute = kernel.ops[-2]
    attrs = dataclasses.replace(compute.attrs, cost=dataclasses.replace(compute.attrs.cost, logical_input_bytes=32, local_storage_bytes=48))
    duplicated = dataclasses.replace(compute, reads=(compute.reads[0], compute.reads[0]), attrs=attrs)
    rejects(dataclasses.replace(kernel, ops=(*kernel.ops[:-2], duplicated, kernel.ops[-1])), "E_EXPORT_UNSUPPORTED_OP")


def test_write_regions_must_map_each_logical_element_to_distinct_storage():
    kernel = load_compute_store_kernel()
    overlapping = dataclasses.replace(kernel.views[3], object_strides=(0,), layout=None)
    rejects(dataclasses.replace(kernel, views=(*kernel.views[:3], overlapping)), "E_EXPORT_LAYOUT")


def test_transposed_and_disjoint_writes_and_broadcast_reads_remain_valid():
    kernel = load_compute_store_kernel()
    store = kernel.ops[-1]
    transposed = ElementRegion((0,), (2,), (2,))
    kernel = create_kernel(
        kernel.arch_digest,
        kernel.source_semantic_hash,
        kernel.entrypoint,
        kernel.profile_id,
        kernel.tensors,
        kernel.computations,
        kernel.placements,
        kernel.shards,
        kernel.partial_sums,
        kernel.objects,
        kernel.views,
        kernel.states,
        kernel.tokens,
        (*kernel.ops[:-1], dataclasses.replace(store, reads=(dataclasses.replace(store.reads[0], region=transposed),), writes=(dataclasses.replace(store.writes[0], region=transposed),))),
    )
    kernel.verify()
    partial_write_kernel().verify()
    compute = kernel.ops[-2]


def test_shared_logical_contract_accepts_each_materialized_kernel_family():
    cases = (
        semantic_operation_kernel(KernelOpcode.VECTOR, VectorKernelAttrs(OpCode.RELU, ElementwiseAttrs(), tile(), cost(), VectorAlgorithm.ELEMENTWISE), (((4,), DType.FP32),), ((4,), DType.FP32)),
        semantic_operation_kernel(KernelOpcode.VECTOR, VectorKernelAttrs(OpCode.EMBEDDING_LOOKUP, EmbeddingAttrs(-1, False, False), tile(48), KernelCost(344, 192, 536, 0, 48, 0), VectorAlgorithm.EMBEDDING_GATHER), (((10, 8), DType.FP32), ((2, 3), DType.INT32)), ((2, 3, 8), DType.FP32)),
        semantic_operation_kernel(KernelOpcode.DATA_MOVEMENT, MovementKernelAttrs(OpCode.CONTIGUOUS_COPY, ViewAttrs(shape=(Const(4),)), tile(), cost(), MovementAlgorithm.STRIDED_COPY), (((4,), DType.FP32),), ((4,), DType.FP32)),
        semantic_operation_kernel(KernelOpcode.DATA_MOVEMENT, MovementKernelAttrs(OpCode.CONCAT, MovementAttrs(1, True), tile(14), KernelCost(56, 56, 112, 0, 14, 0), MovementAlgorithm.CONCAT), (((2, 3), DType.FP32), ((2, 4), DType.FP32)), ((2, 7), DType.FP32)),
        semantic_operation_kernel(KernelOpcode.DATA_MOVEMENT, MovementKernelAttrs(OpCode.GATHER_ROWS, MovementAttrs(1, True), tile(6), KernelCost(44, 24, 68, 0, 6, 0), MovementAlgorithm.GATHER_ROWS), (((2, 4), DType.FP32), ((3,), DType.INT32)), ((2, 3), DType.FP32)),
        semantic_operation_kernel(KernelOpcode.REDUCE, ReductionKernelAttrs(OpCode.REDUCE_SUM, ReduceAttrs((0,), False, DType.FP32, DType.FP32), tile(1), KernelCost(16, 4, 20, 0, 0, 3), ReductionAlgorithm.LEFT_TO_RIGHT), (((4,), DType.FP32),), ((), DType.FP32)),
        semantic_operation_kernel(KernelOpcode.SOFTMAX, SoftmaxKernelAttrs(SoftmaxAttrs(0, DType.FP32, True), tile(), KernelCost(16, 16, 32, 0, 20, 9), SoftmaxAlgorithm.STABLE_MAX_SUM), (((4,), DType.FP32),), ((4,), DType.FP32)),
        semantic_operation_kernel(KernelOpcode.NORM, NormKernelAttrs(OpCode.LAYERNORM, NormAttrs((0,), 1e-5, False, False), tile(), KernelCost(16, 16, 32, 0, 16, 6), NormAlgorithm.LAYER_NORM), (((4,), DType.FP32),), ((4,), DType.FP32)),
    )
    for kernel in cases:
        kernel.verify()


def test_shared_logical_contract_rejects_invalid_axes_dtypes_shapes_and_unary_options():
    invalid = (
        lambda: semantic_operation_kernel(KernelOpcode.SOFTMAX, SoftmaxKernelAttrs(SoftmaxAttrs(99, DType.FP32), tile(), cost(), SoftmaxAlgorithm.STABLE_MAX_SUM), (((4,), DType.FP32),), ((4,), DType.FP32)),
        lambda: semantic_operation_kernel(KernelOpcode.REDUCE, ReductionKernelAttrs(OpCode.REDUCE_SUM, ReduceAttrs((0,), False, DType.FP32, DType.FP32), tile(), cost(), ReductionAlgorithm.LEFT_TO_RIGHT), (((4,), DType.FP32),), ((4,), DType.FP32)),
        lambda: semantic_operation_kernel(KernelOpcode.NORM, NormKernelAttrs(OpCode.LAYERNORM, NormAttrs((99,), 1e-5, False, False), tile(), cost(), NormAlgorithm.LAYER_NORM), (((4,), DType.FP32),), ((4,), DType.FP32)),
        lambda: semantic_operation_kernel(KernelOpcode.VECTOR, VectorKernelAttrs(OpCode.RELU, ElementwiseAttrs(1, "rhs"), tile(), cost(), VectorAlgorithm.ELEMENTWISE), (((4,), DType.FP32),), ((4,), DType.FP32)),
        lambda: semantic_operation_kernel(KernelOpcode.DATA_MOVEMENT, MovementKernelAttrs(OpCode.RESHAPE_VIEW, ViewAttrs(shape=(Const(4),)), tile(), cost(), MovementAlgorithm.STRIDED_COPY), (((4,), DType.FP32),), ((4,), DType.FP32)),
    )
    for factory in invalid:
        with pytest.raises(MeshIrError):
            factory()
    kernel = real_gemm_kernel()
    gemm = kernel.ops[-1]
    semantic_attrs = dataclasses.replace(gemm.attrs.semantic_attrs, accum_dtype=DType.INT32)
    attrs = dataclasses.replace(gemm.attrs, semantic_attrs=semantic_attrs)
    computation = dataclasses.replace(kernel.computations[0], attrs=semantic_attrs)
    rejects(dataclasses.replace(kernel, computations=(computation,), ops=(*kernel.ops[:-1], dataclasses.replace(gemm, attrs=attrs))), "E_EXPORT_DTYPE")


def test_accumulation_matrix_result_requires_typed_synthesized_source_provenance():
    kernel = accumulation_gemm_kernel()
    kernel.verify()
    assert kernel_capability_requirements(kernel) == (KernelCapabilityRequirement(kernel.ops[-1].op_id, Engine.TENSOR, DType.FP16),)
    wrong_source = dataclasses.replace(kernel.tensors[3], producer_computation_id=2)
    rejects(dataclasses.replace(kernel, tensors=(*kernel.tensors[:3], wrong_source)), "E_ABI_BOUNDS")
    semantic = dataclasses.replace(kernel.tensors[3], synthesized_purpose=None)
    rejects(dataclasses.replace(kernel, tensors=(*kernel.tensors[:3], semantic)), "E_ABI_BOUNDS")


def test_capability_requirements_are_typed_canonical_and_use_actual_work_dtypes():
    matrix = real_gemm_kernel()
    reduction = semantic_operation_kernel(KernelOpcode.REDUCE, ReductionKernelAttrs(OpCode.REDUCE_SUM, ReduceAttrs((0,), False, DType.FP32, DType.FP32), tile(1), KernelCost(16, 4, 20, 0, 0, 3), ReductionAlgorithm.LEFT_TO_RIGHT), (((4,), DType.FP32),), ((), DType.FP32))
    mixed = semantic_operation_kernel(KernelOpcode.SOFTMAX, SoftmaxKernelAttrs(SoftmaxAttrs(0, DType.FP16, True), tile(), KernelCost(8, 8, 16, 0, 20, 9), SoftmaxAlgorithm.STABLE_MAX_SUM), (((4,), DType.FP16),), ((4,), DType.FP16))
    assert kernel_capability_requirements(matrix) == (KernelCapabilityRequirement(matrix.ops[-1].op_id, Engine.TENSOR, DType.FP32),)
    assert kernel_capability_requirements(reduction) == (KernelCapabilityRequirement(reduction.ops[-1].op_id, Engine.REDUCE, DType.FP32),)
    assert kernel_capability_requirements(mixed) == (
        KernelCapabilityRequirement(mixed.ops[-1].op_id, Engine.VECTOR, DType.FP32),
        KernelCapabilityRequirement(mixed.ops[-1].op_id, Engine.REDUCE, DType.FP32),
    )
    with pytest.raises(MeshIrError):
        KernelCapabilityRequirement(True, Engine.VECTOR, DType.FP32)


def test_scheduled_ready_requires_each_exact_engine_dtype_from_a_valid_architecture():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    vector = semantic_operation_kernel(KernelOpcode.VECTOR, VectorKernelAttrs(OpCode.RELU, ElementwiseAttrs(), tile(), KernelCost(4, 4, 8, 0, 4, 0), VectorAlgorithm.ELEMENTWISE), (((4,), DType.INT8),), ((4,), DType.INT8))
    matrix = real_gemm_kernel()
    reduction = semantic_operation_kernel(KernelOpcode.REDUCE, ReductionKernelAttrs(OpCode.REDUCE_SUM, ReduceAttrs((0,), False, DType.FP32, DType.FP32), tile(1), KernelCost(16, 4, 20, 0, 0, 3), ReductionAlgorithm.LEFT_TO_RIGHT), (((4,), DType.FP32),), ((), DType.FP32))
    mixed = semantic_operation_kernel(KernelOpcode.SOFTMAX, SoftmaxKernelAttrs(SoftmaxAttrs(0, DType.FP16), tile(), KernelCost(8, 8, 16, 0, 12, 6), SoftmaxAlgorithm.STABLE_MAX_SUM), (((4,), DType.FP16),), ((4,), DType.FP16))
    kernels = tuple(refreshed(dataclasses.replace(kernel, arch_digest=arch.digest().hex())) for kernel in (vector, matrix, reduction, mixed))
    for kernel in kernels:
        verify_scheduled_ready_kernel(kernel, arch)
    missing = (
        dataclasses.replace(arch, vector_elements_per_cycle={key: value for key, value in arch.vector_elements_per_cycle.items() if key != "int8"}),
        dataclasses.replace(arch, tensor_macs_per_cycle={key: value for key, value in arch.tensor_macs_per_cycle.items() if key != "fp32"}),
        dataclasses.replace(arch, reduce_ops_per_cycle={key: value for key, value in arch.reduce_ops_per_cycle.items() if key != "fp32"}),
        dataclasses.replace(arch, vector_elements_per_cycle={key: value for key, value in arch.vector_elements_per_cycle.items() if key != "fp32"}),
    )
    for kernel, unsupported in zip(kernels, missing):
        rebound = refreshed(dataclasses.replace(kernel, arch_digest=unsupported.digest().hex()))
        with pytest.raises(MeshIrError) as error:
            verify_scheduled_ready_kernel(rebound, unsupported)
        assert error.value.code == "E_CAPABILITY_MISMATCH"


def test_scheduled_ready_rejects_nonhardware_owner_and_noncanonical_barrier_participants():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    kernel = load_compute_store_kernel()
    barrier = KernelOp(12, 0, "barrier", KernelOpcode.BARRIER, 3, 0, (), (), BarrierAttrs((4, 3)), (3,), 4)
    kernel = refreshed(dataclasses.replace(kernel, arch_digest=arch.digest().hex(), tokens=(*kernel.tokens, ControlToken(4)), ops=(*kernel.ops, barrier)))
    with pytest.raises(MeshIrError) as error:
        verify_scheduled_ready_kernel(kernel, arch)
    assert error.value.code == "E_PLACEMENT_INFEASIBLE"
    bad_owner = dataclasses.replace(barrier, owner_core=42, attrs=BarrierAttrs((42,)))
    kernel = refreshed(dataclasses.replace(kernel, ops=(*kernel.ops[:-1], bad_owner)))
    with pytest.raises(MeshIrError) as error:
        verify_scheduled_ready_kernel(kernel, arch)
    assert error.value.code == "E_PLACEMENT_INFEASIBLE"


def test_kernel_schema_is_closed_omits_optional_nulls_and_accepts_canonical_u64():
    kernel = load_compute_store_kernel()
    assert kernel.canonical_dict()["ops"][9]["result_shard_id"] == 2
    missing_result = kernel.canonical_dict()
    del missing_result["ops"][9]["result_shard_id"]
    with pytest.raises(MeshIrError):
        validate_schema("mesh_kernel_v1.schema.json", missing_result, "Kernel IR")
    large = dataclasses.replace(kernel.objects[1], buffer_index=U64_MAX)
    refreshed(dataclasses.replace(kernel, objects=(kernel.objects[0], large, *kernel.objects[2:]))).verify()
    payload = kernel.canonical_dict()
    payload["ops"][9]["attrs"]["semantic_attrs"]["scalar"] = None
    with pytest.raises(MeshIrError):
        validate_schema("mesh_kernel_v1.schema.json", payload, "Kernel IR")
    payload = kernel.canonical_dict()
    payload["ops"][9]["attrs"]["unexpected"] = 1
    with pytest.raises(MeshIrError):
        validate_schema("mesh_kernel_v1.schema.json", payload, "Kernel IR")
    overflow = dataclasses.replace(kernel.objects[1], buffer_index=U64_MAX + 1)
    with pytest.raises(MeshIrError) as error:
        dataclasses.replace(kernel, objects=(kernel.objects[0], overflow, *kernel.objects[2:])).verify()
    assert error.value.code == "E_ABI_BOUNDS"


def test_local_fill_and_declarations_preserve_their_physical_owner():
    kernel = partial_write_kernel()
    fill = dataclasses.replace(kernel.ops[-3], owner_core=7)
    rejects(dataclasses.replace(kernel, ops=(*kernel.ops[:-3], fill, *kernel.ops[-2:])), "E_DMA_RANGE")
    declaration = dataclasses.replace(kernel.ops[0], owner_core=7)
    rejects(dataclasses.replace(kernel, ops=(declaration, *kernel.ops[1:])), "E_PLACEMENT_INFEASIBLE")


def test_blocked_layout_minor_order_rejects_boolean_integer_coercion():
    kernel = load_compute_store_kernel()
    malformed = dataclasses.replace(kernel.views[1], layout=Layout.BLOCKED_MNK, blocked_layout=BlockedMnkLayout(1, 1, 1, (True, 1, 2)))
    with pytest.raises(MeshIrError) as error:
        dataclasses.replace(kernel, views=(kernel.views[0], malformed, *kernel.views[2:])).verify()
    assert error.value.code == "E_ABI_BOUNDS"


def test_one_logical_tensor_supports_multiple_complete_placement_groups():
    kernel = load_compute_store_kernel()
    extra = (
        TensorShard(3, 1, 2, 3, DistributionKind.PARTITIONED, (0,), (2,), (2,)),
        TensorShard(4, 1, 2, 7, DistributionKind.PARTITIONED, (2,), (2,), (2,)),
    )
    multi = refreshed(dataclasses.replace(kernel, placements=(*kernel.placements, Placement(2, (3, 7))), shards=(*kernel.shards, *extra)))
    multi.verify()
    mixed = dataclasses.replace(extra[1], distribution=DistributionKind.REPLICATED)
    rejects(dataclasses.replace(multi, shards=(*kernel.shards, extra[0], mixed)), "E_PLACEMENT_INFEASIBLE")


def test_compute_result_shard_is_required_typed_and_matches_the_actual_write():
    kernel = load_compute_store_kernel()
    compute = kernel.ops[-2]
    for value in (0, True, len(kernel.shards) + 1):
        rejects(dataclasses.replace(kernel, ops=(*kernel.ops[:-2], dataclasses.replace(compute, result_shard_id=value), kernel.ops[-1])), "E_ABI_BOUNDS")
    rejects(dataclasses.replace(kernel, ops=(*kernel.ops[:-2], dataclasses.replace(compute, result_shard_id=1), kernel.ops[-1])), "E_EXPORT_LAYOUT")
    load = kernel.ops[-3]
    rejects(dataclasses.replace(kernel, ops=(*kernel.ops[:-3], dataclasses.replace(load, result_shard_id=1), *kernel.ops[-2:])), "E_ABI_BOUNDS")


def test_equal_core_tuples_can_hold_distinct_complete_partition_axes():
    kernel = real_gemm_kernel()
    extra = (
        TensorShard(4, 1, 2, 3, DistributionKind.PARTITIONED, (0, 0), (1, 3), (1, 3)),
        TensorShard(5, 1, 2, 7, DistributionKind.PARTITIONED, (1, 0), (1, 3), (1, 3)),
        TensorShard(6, 1, 3, 3, DistributionKind.PARTITIONED, (0, 0), (2, 2), (2, 2)),
        TensorShard(7, 1, 3, 7, DistributionKind.PARTITIONED, (0, 2), (2, 2), (2, 1)),
    )
    multi = refreshed(dataclasses.replace(kernel, placements=(*kernel.placements, Placement(2, (3, 7)), Placement(3, (3, 7))), shards=(*kernel.shards, *extra)))
    multi.verify()
    rejects(dataclasses.replace(multi, shards=multi.shards[:-1]), "E_PLACEMENT_INFEASIBLE")
    duplicate = dataclasses.replace(extra[1], owner_core=3)
    rejects(dataclasses.replace(multi, shards=(*kernel.shards, extra[0], duplicate, *extra[2:])), "E_PLACEMENT_INFEASIBLE")


def test_partial_sum_identity_cannot_cross_placement_groups():
    kernel = real_gemm_kernel(partial_output=True)
    extra = TensorShard(5, 4, 2, 7, DistributionKind.PARTIAL_SUM, (0, 0), (2, 4), (2, 4), 1)
    rejects(dataclasses.replace(kernel, placements=(*kernel.placements, Placement(2, (7,))), shards=(*kernel.shards, extra)), "E_PLACEMENT_INFEASIBLE")
