import dataclasses
from pathlib import Path

import pytest

from mesh_ir.architecture import load_arch
from mesh_ir.compile_config import load_compile_config_text, resolve_compile_config
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.ir.common import Access, Const, DType, Engine, StorageClass, TensorRole, contiguous_strides
from mesh_ir.ir.graph_ir import ElementwiseAttrs, GraphFunction, GraphModule, GraphOp, GraphValue, MatmulAttrs, NormAttrs, OpCode, PytreeSpec, ReduceAttrs, SoftmaxAttrs, ViewAttrs
from mesh_ir.ir.kernel_ir import ControlToken, DistributionKind, GemmKernelAttrs, KERNEL_ATTR_TYPE_BY_OPCODE, KernelComputation, KernelCost, KernelMemoryRecords, KernelOp, KernelOpcode, KernelTensor, KernelTile, MatrixEpilogueAlgorithm, MatrixEpilogueKernelAttrs, MatrixPhase, MovementAlgorithm, MovementKernelAttrs, NormAlgorithm, NormKernelAttrs, Placement, ReductionAlgorithm, ReductionKernelAttrs, SoftmaxAlgorithm, SoftmaxKernelAttrs, TensorShard, VectorAlgorithm, VectorKernelAttrs
from mesh_ir.ir.kernel_verify import verify_kernel_memory
from mesh_ir.passes.execution import PassExecutor
from mesh_ir.passes.graph_to_kernel import lower_to_kernel
from mesh_ir.passes.planning_prefix import plan_graphs_with_executor
from mesh_ir.passes.scheduled import lower_to_program
from mesh_ir.scheduled.model import ComputeExecution, KernelCommandSource
from mesh_ir.scheduled.projection import abi_attr_for_kernel_op
from mesh_ir.scheduled.verify import verify_program
from tests.unit.test_gate2_graph_to_kernel import COMPILE


ROOT = Path(__file__).resolve().parents[4]
ZERO_COST = KernelCost(0, 0, 0, 0, 0, 0)


def test_kernel_attribute_registry_is_complete_and_immutable():
    assert set(KERNEL_ATTR_TYPE_BY_OPCODE) == set(KernelOpcode)
    with pytest.raises(TypeError):
        KERNEL_ATTR_TYPE_BY_OPCODE[KernelOpcode.NORM] = VectorKernelAttrs


def _tile(m=4, n=1, k=1):
    return KernelTile(0, 0, 0, 0, 1, m, n, k, 1, 0, n, k)


def _tensor(tensor_id, shape, dtype, producer):
    stride = 1
    strides = []
    for extent in reversed(shape):
        strides.append(stride)
        stride *= extent
    elements = 1
    for extent in shape:
        elements *= extent
    size = elements * dtype.byte_width
    return KernelTensor(tensor_id, producer, None, tensor_id, 0, f"value:{tensor_id}", TensorRole.OUTPUT if producer else TensorRole.INPUT, dtype, shape, tuple(reversed(strides)), StorageClass.CORE_SRAM if producer else StorageClass.EXTERNAL, Access.READ_WRITE if producer else Access.READ_ONLY, size, size, None)


def _records(opcode, attrs, operand_specs, result_spec):
    specs = (*operand_specs, result_spec)
    tensors = tuple(_tensor(index, shape, dtype, int(index == len(specs))) for index, (shape, dtype) in enumerate(specs, 1))
    shards = tuple(TensorShard(index, index, 1, 3, DistributionKind.PARTITIONED, (0,) * len(tensor.shape), tensor.shape, tensor.shape) for index, tensor in enumerate(tensors, 1))
    result_id = len(tensors)
    graph_opcode = OpCode.SOFTMAX if opcode is KernelOpcode.SOFTMAX else attrs.graph_opcode
    computation = KernelComputation(1, graph_opcode, tuple(range(1, result_id)), result_id, attrs.semantic_attrs)
    op = KernelOp(1, 1, f"empty:{opcode.value}", opcode, 3, result_id, (), (), attrs, (), 1)
    records = KernelMemoryRecords(tensors, (computation,), (Placement(1, (3,)),), shards, (), (), (), (), (ControlToken(1),), (op,))
    verify_kernel_memory(records)
    return records


def _empty_case(name):
    matmul = MatmulAttrs(rhs_transpose=True, accum_dtype=DType.FP32)
    bmm = MatmulAttrs(batch_axes=(0,), rhs_transpose=True, accum_dtype=DType.FP32)
    matrix_tile = _tile(4, 4, 3)
    cases = {
        "gemm": lambda: (
            _records(KernelOpcode.GEMM, GemmKernelAttrs(OpCode.MATMUL, matmul, matrix_tile, ZERO_COST, MatrixPhase.DIRECT, 0), (((0, 3), DType.FP16), ((4, 3), DType.FP16)), ((0, 4), DType.FP16)),
            A.ATTR_KIND.GEMM_V1,
            (1, 0, 4, 3, 0, 1, int(DType.FP16), int(DType.FP32), 0, 65536),
        ),
        "bmm": lambda: (
            _records(KernelOpcode.BMM, GemmKernelAttrs(OpCode.BMM, bmm, matrix_tile, ZERO_COST, MatrixPhase.DIRECT, 0), (((1, 0, 3), DType.FP16), ((1, 4, 3), DType.FP16)), ((1, 0, 4), DType.FP16)),
            A.ATTR_KIND.BMM_V1,
            (1, 0, 4, 3, 0, 1, int(DType.FP16), int(DType.FP32), 0, 65536),
        ),
        "matrix_epilogue": lambda: (
            _records(KernelOpcode.MATRIX_EPILOGUE, MatrixEpilogueKernelAttrs(OpCode.LINEAR_BIAS, matmul, matrix_tile, ZERO_COST, MatrixEpilogueAlgorithm.VECTOR_ACCUMULATION), (((0, 3), DType.FP16), ((4, 3), DType.FP16), ((4,), DType.FP16)), ((0, 4), DType.FP16)),
            A.ATTR_KIND.ELEMENTWISE_V1,
            (0, int(DType.FP16), 0, 1, 0),
        ),
        "vector": lambda: (
            _records(KernelOpcode.VECTOR, VectorKernelAttrs(OpCode.RELU, ElementwiseAttrs(), _tile(), ZERO_COST, VectorAlgorithm.ELEMENTWISE), (((0,), DType.FP16),), ((0,), DType.FP16)),
            A.ATTR_KIND.ELEMENTWISE_V1,
            (0, int(DType.FP16), 0, 1, 0),
        ),
        "data_movement": lambda: (
            _records(KernelOpcode.DATA_MOVEMENT, MovementKernelAttrs(OpCode.CONTIGUOUS_COPY, ViewAttrs(shape=(Const(0),)), _tile(), ZERO_COST, MovementAlgorithm.STRIDED_COPY), (((0,), DType.FP16),), ((0,), DType.FP16)),
            A.ATTR_KIND.ELEMENTWISE_V1,
            (0, int(DType.FP16), 0, 1, 0),
        ),
        "reduce": lambda: (
            _records(KernelOpcode.REDUCE, ReductionKernelAttrs(OpCode.REDUCE_SUM, ReduceAttrs((1,), False, DType.FP32, DType.FP32), _tile(8), ZERO_COST, ReductionAlgorithm.LEFT_TO_RIGHT), (((0, 8), DType.FP16),), ((0,), DType.FP32)),
            A.ATTR_KIND.REDUCE_V1,
            (0, int(DType.FP16), int(DType.FP32), 0, 2),
        ),
        "softmax": lambda: (
            _records(KernelOpcode.SOFTMAX, SoftmaxKernelAttrs(SoftmaxAttrs(1, DType.FP16), _tile(8), ZERO_COST, SoftmaxAlgorithm.STABLE_MAX_SUM), (((0, 8), DType.FP16),), ((0, 8), DType.FP16)),
            A.ATTR_KIND.SOFTMAX_V1,
            (0, int(DType.FP16), A.VECTOR_ALGORITHM.STANDARD, 0),
        ),
        "norm": lambda: (
            _records(KernelOpcode.NORM, NormKernelAttrs(OpCode.LAYERNORM, NormAttrs((1,), 1e-5, False, False), _tile(8), ZERO_COST, NormAlgorithm.LAYER_NORM), (((0, 8), DType.FP16),), ((0, 8), DType.FP16)),
            A.ATTR_KIND.NORM_V1,
            (0, int(DType.FP16), A.VECTOR_ALGORITHM.STANDARD, 0),
        ),
    }
    return cases[name]()


@pytest.mark.parametrize("name", ("gemm", "bmm", "matrix_epilogue", "vector", "data_movement", "reduce", "softmax", "norm"))
def test_access_free_empty_compute_families_project_original_zero_work_attributes(name):
    records, kind, payload = _empty_case(name)
    attr = abi_attr_for_kernel_op(records.ops[-1], records)
    assert attr.kind == kind
    assert attr.payload == payload


@pytest.mark.parametrize("corruption", ("cost", "shard_extent", "result_identity", "computation_identity", "completion", "attrs"))
def test_empty_compute_projection_rejects_each_broken_empty_proof(corruption):
    records, _, _ = _empty_case("norm")
    op = records.ops[-1]
    if corruption == "cost":
        op = dataclasses.replace(op, attrs=dataclasses.replace(op.attrs, cost=KernelCost(0, 0, 0, 0, 1, 0)))
    elif corruption == "shard_extent":
        shard = dataclasses.replace(records.shards[-1], padded_local_shape=(1, 8), valid_shape=(1, 8))
        records = dataclasses.replace(records, shards=(*records.shards[:-1], shard))
    elif corruption == "result_identity":
        op = dataclasses.replace(op, result_shard_id=1)
    elif corruption == "computation_identity":
        op = dataclasses.replace(op, computation_id=0)
    elif corruption == "completion":
        op = dataclasses.replace(op, done_token=None)
    else:
        op = dataclasses.replace(op, attrs=VectorKernelAttrs(OpCode.RELU, ElementwiseAttrs(), _tile(8), ZERO_COST, VectorAlgorithm.ELEMENTWISE))
    with pytest.raises(MeshIrError):
        abi_attr_for_kernel_op(op, dataclasses.replace(records, ops=(*records.ops[:-1], op)))


@pytest.mark.parametrize(("opcode_family", "attribute_family"), (("gemm", "norm"), ("bmm", "softmax"), ("vector", "norm"), ("norm", "gemm")))
def test_empty_compute_projection_rejects_wrong_attribute_family_before_domain_proof(opcode_family, attribute_family):
    records, _, _ = _empty_case(opcode_family)
    foreign, _, _ = _empty_case(attribute_family)
    op = dataclasses.replace(records.ops[-1], attrs=foreign.ops[-1].attrs)
    with pytest.raises(MeshIrError) as error:
        abi_attr_for_kernel_op(op, dataclasses.replace(records, ops=(op,)))
    assert error.value.code == "E_ABI_ENUM"


@pytest.mark.parametrize("opcode", ("GEMM", []), ids=("raw-string", "unhashable"))
def test_empty_compute_projection_rejects_non_enum_opcode_before_registry_lookup(opcode):
    records, _, _ = _empty_case("gemm")
    op = dataclasses.replace(records.ops[-1], opcode=opcode)
    with pytest.raises(MeshIrError) as error:
        abi_attr_for_kernel_op(op, dataclasses.replace(records, ops=(op,)))
    assert error.value.code == "E_ABI_ENUM"


def test_empty_tp4_layernorm_projects_through_complete_scheduled_lowering():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_2x2.yaml")
    source_shape = (Const(2), Const(4), Const(8))
    parameter_shape = (Const(8),)
    source = GraphValue(1, "source", TensorRole.INPUT, DType.FP32, source_shape, contiguous_strides(source_shape), 0, 1)
    weight = GraphValue(2, "weight", TensorRole.WEIGHT, DType.FP32, parameter_shape, contiguous_strides(parameter_shape), 0, 2, content_sha256="a" * 64)
    bias = GraphValue(3, "bias", TensorRole.WEIGHT, DType.FP32, parameter_shape, contiguous_strides(parameter_shape), 0, 3, content_sha256="b" * 64)
    result = GraphValue(4, "result", TensorRole.OUTPUT, DType.FP32, source_shape, contiguous_strides(source_shape), 0, 4)
    op = GraphOp(1, OpCode.LAYERNORM, (1, 2, 3), (4,), NormAttrs((2,), 1e-5, True, True), "layernorm:empty-ranks")
    function = GraphFunction(1, "forward", (1,), (op,), (4,), PytreeSpec.leaf(), PytreeSpec.leaf())
    graph = GraphModule.create(arch.digest().hex(), "2" * 64, "forward", "p1", (source, weight, bias, result), (function,))
    text = COMPILE.replace("tensor_parallel: 1", "tensor_parallel: 4").replace("allowed_cores: [7, 2]", "allowed_cores: [7, 2, 9, 13]")
    effective = resolve_compile_config(load_compile_config_text(text, arch), arch)
    with PassExecutor(1) as executor:
        planning = plan_graphs_with_executor((graph,), arch, effective, source_graphs=(graph,), executor=executor)
        lowering = lower_to_kernel(planning, arch, effective, executor)
        scheduled = lower_to_program(lowering, arch, effective, executor)
    assert tuple(item.name for item in scheduled.passes) == (
        "PlanStaticSRAM",
        "InsertHazardDependencies",
        "SchedulePerCoreStreams",
        "LowerDmaToSegments",
        "BindAddressesAndRelocations",
        "VerifyScheduledIR",
        "ComputeExpectedTraffic",
    )
    program = scheduled.program
    norm_ops = tuple(item for item in program.semantics.kernel_ops if item.opcode is KernelOpcode.NORM)
    empty = tuple(item for item in norm_ops if item.owner_core in (9, 13))
    nonempty = tuple(item for item in norm_ops if item.owner_core in (7, 2))
    semantics = {item.source.kernel_op_id: item for item in program.semantics.command_semantics if type(item.source) is KernelCommandSource}
    assert len(empty) == len(nonempty) == 2
    for item in empty:
        semantic = semantics[item.op_id]
        command = program.commands[semantic.command_id - 1]
        attr = program.op_attrs[command.attr_index - 1]
        assert item.reads == item.writes == ()
        assert item.done_token is not None
        assert command.opcode == A.OPCODE.NORM
        assert command.engine == int(Engine.CONTROL)
        assert command.operand_count == 0
        assert command.signal_event != 0
        assert program.events[command.signal_event - 1].producer_command_id == command.command_id
        assert attr.kind == A.ATTR_KIND.NORM_V1
        assert attr.payload == (0, int(DType.FP32), A.VECTOR_ALGORITHM.STANDARD, 0)
        assert semantic.execution == ComputeExecution(())
    for item in nonempty:
        semantic = semantics[item.op_id]
        command = program.commands[semantic.command_id - 1]
        attr = program.op_attrs[command.attr_index - 1]
        assert len(item.reads) == 3
        assert len(item.writes) == 1
        assert command.operand_count == 4
        assert attr.payload == (32, int(DType.FP32), A.VECTOR_ALGORITHM.STANDARD, 0)
    assert verify_program(program, arch).program is program
