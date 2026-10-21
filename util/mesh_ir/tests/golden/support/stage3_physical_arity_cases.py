from dataclasses import replace

from mesh_ir.architecture import load_arch
from mesh_ir.ir.common import Access, Const, DType
from mesh_ir.ir.graph_ir import MovementAttrs, NormAttrs, OpCode, ReduceAttrs, SoftmaxAttrs, ViewAttrs
from mesh_ir.ir.kernel_ir import (
    KernelCost,
    KernelOpcode,
    KernelTile,
    MovementAlgorithm,
    MovementKernelAttrs,
    NormAlgorithm,
    NormKernelAttrs,
    ReductionAlgorithm,
    ReductionKernelAttrs,
    SoftmaxAlgorithm,
    SoftmaxKernelAttrs,
)
from mesh_ir.model import Program
from tests.golden.support.stage3_intrinsic_memory_cases import (
    _with_projected_operands,
)
from tests.golden.support.stage2_control_fixture import ROOT
from tests.golden.support.stage3_intrinsic_cases import build_complete_program
from tests.golden.support.stage3_physical_operation_cases import (
    Stage3PhysicalOperationCase,
)
from tests.unit.test_gate2_kernel_ir import semantic_operation_kernel


def _drop_reads(program: Program, opcode: KernelOpcode, count: int) -> Program:
    operation = next(
        item for item in program.semantics.kernel_ops if item.opcode is opcode
    )
    kernel_ops = tuple(
        replace(item, reads=item.reads[:count]) if item.op_id == operation.op_id else item
        for item in program.semantics.kernel_ops
    )
    return _with_projected_operands(
        program, replace(program.semantics, kernel_ops=kernel_ops)
    )


def _complete(arch, name, opcode, attrs, operands, result):
    kernel = semantic_operation_kernel(opcode, attrs, operands, result)
    return build_complete_program(arch, kernel.memory_records(), name)


def build_stage3_physical_arity_cases() -> tuple[Stage3PhysicalOperationCase, ...]:
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    tile = KernelTile(0, 0, 0, 0, 1, 4, 1, 1, 1, 4, 1, 1)
    movement_cost = KernelCost(16, 16, 32, 0, 4, 0)
    contiguous = _complete(
        arch,
        "stage3-arity-contiguous",
        KernelOpcode.DATA_MOVEMENT,
        MovementKernelAttrs(
            OpCode.CONTIGUOUS_COPY,
            ViewAttrs(shape=(Const(4),)),
            tile,
            movement_cost,
            MovementAlgorithm.STRIDED_COPY,
        ),
        (((4,), DType.FP32),),
        ((4,), DType.FP32),
    )
    gather = _complete(
        arch,
        "stage3-arity-gather",
        KernelOpcode.DATA_MOVEMENT,
        MovementKernelAttrs(
            OpCode.GATHER_ROWS,
            MovementAttrs(1, True),
            tile,
            KernelCost(44, 24, 68, 0, 6, 0),
            MovementAlgorithm.GATHER_ROWS,
        ),
        (((2, 4), DType.FP32), ((3,), DType.INT32)),
        ((2, 3), DType.FP32),
    )
    concat = _complete(
        arch,
        "stage3-arity-concat",
        KernelOpcode.DATA_MOVEMENT,
        MovementKernelAttrs(
            OpCode.CONCAT,
            MovementAttrs(1, True),
            tile,
            KernelCost(56, 56, 112, 0, 14, 0),
            MovementAlgorithm.CONCAT,
        ),
        (((2, 3), DType.FP32), ((2, 4), DType.FP32)),
        ((2, 7), DType.FP32),
    )
    reduce = _complete(
        arch,
        "stage3-arity-reduce",
        KernelOpcode.REDUCE,
        ReductionKernelAttrs(
            OpCode.REDUCE_SUM,
            ReduceAttrs((0,), False, DType.FP32, DType.FP32),
            tile,
            KernelCost(16, 4, 20, 0, 0, 3),
            ReductionAlgorithm.LEFT_TO_RIGHT,
        ),
        (((4,), DType.FP32),),
        ((), DType.FP32),
    )
    softmax = _complete(
        arch,
        "stage3-arity-softmax",
        KernelOpcode.SOFTMAX,
        SoftmaxKernelAttrs(
            SoftmaxAttrs(0, DType.FP32, True),
            tile,
            KernelCost(16, 16, 32, 0, 20, 9),
            SoftmaxAlgorithm.STABLE_MAX_SUM,
        ),
        (((4,), DType.FP32),),
        ((4,), DType.FP32),
    )
    norm = _complete(
        arch,
        "stage3-arity-norm",
        KernelOpcode.NORM,
        NormKernelAttrs(
            OpCode.LAYERNORM,
            NormAttrs((0,), 1e-5, True, True),
            tile,
            KernelCost(48, 16, 64, 0, 24, 6),
            NormAlgorithm.LAYER_NORM,
        ),
        (((4,), DType.FP32), ((4,), DType.FP32), ((4,), DType.FP32)),
        ((4,), DType.FP32),
    )
    invalid = (
        ("physical_contiguous_copy_arity", contiguous, KernelOpcode.DATA_MOVEMENT, 0),
        ("physical_gather_rows_arity", gather, KernelOpcode.DATA_MOVEMENT, 1),
        ("physical_concat_arity", concat, KernelOpcode.DATA_MOVEMENT, 0),
        ("physical_reduce_arity", reduce, KernelOpcode.REDUCE, 0),
        ("physical_softmax_arity", softmax, KernelOpcode.SOFTMAX, 0),
        ("physical_norm_affine_arity", norm, KernelOpcode.NORM, 2),
    )
    return tuple(
        Stage3PhysicalOperationCase(
            case_id,
            arch,
            baseline,
            _drop_reads(baseline, opcode, reads),
            "E_EXPORT_UNSUPPORTED_OP",
            "movement kernel has invalid arity"
            if opcode is KernelOpcode.DATA_MOVEMENT
            else "reduction kernel has invalid arity"
            if opcode in (KernelOpcode.REDUCE, KernelOpcode.SOFTMAX)
            else "normalization kernel has invalid arity",
        )
        for case_id, baseline, opcode, reads in invalid
    )
