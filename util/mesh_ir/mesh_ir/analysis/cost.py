from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from mesh_ir.canonical import checked_add_u64, checked_mul_u64, checked_u64
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated.semantic_enums import WorkUnit
from mesh_ir.ir.common import Const, DType, Engine, FixedStride, ShapeProductStride
from mesh_ir.ir.graph_ir import (
    ElementwiseAttrs,
    EmbeddingAttrs,
    GraphOp,
    GraphValue,
    MatmulAttrs,
    METADATA_VIEW_OPCODES,
    MovementAttrs,
    NormAttrs,
    OpAttrs,
    OpCode,
    ReduceAttrs,
    SoftmaxAttrs,
    ViewAttrs,
)
from mesh_ir.ir.graph_verify import MatrixResultForm, TensorDataContract, verify_operation_data_contract, verify_operation_type_contract


COST_MODEL_VERSION = 1


class CostAlgorithm(str, Enum):
    MATRIX = "MATRIX"
    ELEMENTWISE = "ELEMENTWISE"
    GELU_EXACT = "GELU_EXACT"
    GELU_TANH = "GELU_TANH"
    SILU = "SILU"
    METADATA_VIEW = "METADATA_VIEW"
    COPY = "COPY"
    CONCAT = "CONCAT"
    GATHER = "GATHER"
    REDUCTION = "REDUCTION"
    SOFTMAX = "SOFTMAX"
    LAYERNORM = "LAYERNORM"
    RMSNORM = "RMSNORM"


@dataclass(frozen=True)
class MatrixWorkDomain:
    batch: int
    m: int
    n: int
    k: int


@dataclass(frozen=True)
class MatrixAccumulationWorkDomain:
    batch: int
    m: int
    n: int
    k: int


@dataclass(frozen=True)
class MatrixEpilogueWorkDomain:
    batch: int
    m: int
    n: int


@dataclass(frozen=True)
class ElementWorkDomain:
    elements: int


@dataclass(frozen=True)
class RowWorkDomain:
    rows: int
    fan_in: int


@dataclass(frozen=True)
class MetadataWorkDomain:
    pass


WorkDomain = MatrixWorkDomain | MatrixAccumulationWorkDomain | MatrixEpilogueWorkDomain | ElementWorkDomain | RowWorkDomain | MetadataWorkDomain


@dataclass(frozen=True)
class WorkEstimate:
    engine: Engine
    unit: WorkUnit
    dtype: DType
    operations: int


@dataclass(frozen=True)
class ExecutionWorkPhase:
    work: tuple[WorkEstimate, ...]

    def __post_init__(self) -> None:
        if type(self.work) is not tuple or not self.work:
            raise MeshIrError("E_CONFIG", "execution work phase must contain immutable work")
        engine = None
        for item in self.work:
            if type(item) is not WorkEstimate or type(item.engine) is not Engine or type(item.unit) is not WorkUnit or type(item.dtype) is not DType:
                raise MeshIrError("E_CONFIG", "execution work phase contains an invalid work record")
            if item.engine not in (Engine.TENSOR, Engine.VECTOR, Engine.REDUCE):
                raise MeshIrError("E_CONFIG", "execution work phase requires a compute engine")
            if type(item.operations) is not int or item.operations <= 0:
                raise MeshIrError("E_CONFIG", "execution work phase requires positive work")
            checked_u64(item.operations, "execution phase work")
            if engine is None:
                engine = item.engine
            elif item.engine is not engine:
                raise MeshIrError("E_CONFIG", "execution work phase must use one engine")

    @property
    def engine(self) -> Engine:
        return self.work[0].engine


@dataclass(frozen=True)
class TensorAccessEstimate:
    value_id: int
    logical_tensor_bytes: int
    storage_span_bytes: int
    accessed_bytes: int


@dataclass(frozen=True)
class OperationCost:
    algorithm: CostAlgorithm
    operand_accesses: tuple[TensorAccessEstimate, ...]
    result_access: TensorAccessEstimate
    allocated_storage_bytes: int
    accumulation_dtype: DType | None
    work: tuple[WorkEstimate, ...]

    @property
    def logical_input_bytes(self) -> int:
        total = 0
        for item in self.operand_accesses:
            total = checked_add_u64(total, item.accessed_bytes, "logical input access bytes")
        return total

    @property
    def logical_output_bytes(self) -> int:
        return checked_u64(self.result_access.accessed_bytes, "logical output access bytes")

    def _engine_operations(self, engine: Engine) -> int:
        total = 0
        for item in self.work:
            if item.engine is engine:
                total = checked_add_u64(total, item.operations, f"{engine.name.lower()} operations")
        return total

    @property
    def macs(self) -> int:
        return self._engine_operations(Engine.TENSOR)

    @property
    def vector_ops(self) -> int:
        return self._engine_operations(Engine.VECTOR)

    @property
    def reduction_ops(self) -> int:
        return self._engine_operations(Engine.REDUCE)


_MATRIX_OPS = frozenset((OpCode.MATMUL, OpCode.BMM, OpCode.LINEAR_BIAS))
_BINARY_OPS = frozenset((OpCode.ADD, OpCode.SUB, OpCode.MUL, OpCode.DIV))
_UNARY_OPS = frozenset((OpCode.RELU, OpCode.GELU, OpCode.SILU, OpCode.EXP, OpCode.RSQRT))
_REDUCE_OPS = frozenset((OpCode.REDUCE_SUM, OpCode.REDUCE_MAX, OpCode.REDUCE_MEAN))


def _checked_domain(domain: WorkDomain) -> None:
    if type(domain) in (MatrixWorkDomain, MatrixAccumulationWorkDomain):
        fields = (domain.batch, domain.m, domain.n, domain.k)
    elif type(domain) is MatrixEpilogueWorkDomain:
        fields = (domain.batch, domain.m, domain.n)
    elif type(domain) is ElementWorkDomain:
        fields = (domain.elements,)
    elif type(domain) is RowWorkDomain:
        fields = (domain.rows, domain.fan_in)
    elif type(domain) is MetadataWorkDomain:
        fields = ()
    else:
        raise MeshIrError("E_CONFIG", "operation work domain type is invalid", type=type(domain).__name__)
    for value in fields:
        checked_u64(value, "work domain extent")


def _append_work(
    work: list[WorkEstimate],
    engine: Engine,
    unit: WorkUnit,
    dtype: DType,
    operations: int,
) -> None:
    checked_u64(operations, "operation work")
    if operations == 0:
        return
    for index, item in enumerate(work):
        if (item.engine, item.unit, item.dtype) == (engine, unit, dtype):
            work[index] = WorkEstimate(engine, unit, dtype, checked_add_u64(item.operations, operations, "operation work"))
            return
    work.append(WorkEstimate(engine, unit, dtype, operations))


def _scaled(value: int, factor: int) -> int:
    return checked_mul_u64(value, factor, "operation work")


def _append_phase(phases: list[ExecutionWorkPhase], work: tuple[WorkEstimate, ...] | list[WorkEstimate]) -> None:
    if not work:
        return
    phase = ExecutionWorkPhase(tuple(work))
    if phases and phases[-1].engine is phase.engine:
        merged = list(phases[-1].work)
        for item in phase.work:
            _append_work(merged, item.engine, item.unit, item.dtype, item.operations)
        phases[-1] = ExecutionWorkPhase(tuple(merged))
    else:
        phases.append(phase)


def _matrix_epilogue_work(
    opcode: OpCode,
    attrs: MatmulAttrs,
    output_elements: int,
    output_dtype: DType,
    *,
    command_phase: bool,
) -> tuple[WorkEstimate, ...]:
    work: list[WorkEstimate] = []
    has_math = attrs.alpha != 1.0
    if attrs.alpha != 1.0:
        _append_work(work, Engine.VECTOR, WorkUnit.MUL, attrs.accum_dtype, output_elements)
    if opcode is OpCode.LINEAR_BIAS:
        if attrs.beta != 1.0:
            _append_work(work, Engine.VECTOR, WorkUnit.MUL, attrs.accum_dtype, output_elements)
        _append_work(work, Engine.VECTOR, WorkUnit.ADD, attrs.accum_dtype, output_elements)
        has_math = True
    if command_phase and output_elements:
        if output_dtype is not attrs.accum_dtype:
            _append_work(work, Engine.VECTOR, WorkUnit.CAST, attrs.accum_dtype, output_elements)
        elif not has_math:
            _append_work(work, Engine.VECTOR, WorkUnit.COPY, attrs.accum_dtype, output_elements)
    return tuple(work)


def _validate_work_signature(
    opcode: OpCode,
    attrs: OpAttrs,
    operand_dtypes: tuple[DType, ...],
    output_dtype: DType,
    domain: WorkDomain,
) -> None:
    if type(opcode) is not OpCode:
        raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "cost operation opcode type is invalid")
    if opcode in _MATRIX_OPS:
        valid_domain = type(domain) in (MatrixWorkDomain, MatrixAccumulationWorkDomain, MatrixEpilogueWorkDomain)
    elif opcode in METADATA_VIEW_OPCODES:
        valid_domain = type(domain) is MetadataWorkDomain
    elif opcode in _REDUCE_OPS or opcode in (OpCode.SOFTMAX, OpCode.LAYERNORM, OpCode.RMSNORM):
        valid_domain = type(domain) is RowWorkDomain
    else:
        valid_domain = type(domain) is ElementWorkDomain
    if not valid_domain:
        raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "cost domain does not match operation", opcode=opcode.value)
    result_form = MatrixResultForm.ACCUMULATION if type(domain) is MatrixAccumulationWorkDomain else MatrixResultForm.SEMANTIC
    verify_operation_type_contract(opcode, attrs, operand_dtypes, output_dtype, matrix_result_form=result_form)


def estimate_work_phases(
    opcode: OpCode,
    attrs: OpAttrs,
    operand_dtypes: tuple[DType, ...],
    output_dtype: DType,
    domain: WorkDomain,
) -> tuple[ExecutionWorkPhase, ...]:
    _checked_domain(domain)
    _validate_work_signature(opcode, attrs, operand_dtypes, output_dtype, domain)
    input_dtype = operand_dtypes[0]
    phases: list[ExecutionWorkPhase] = []
    if opcode in _MATRIX_OPS:
        if type(domain) in (MatrixWorkDomain, MatrixAccumulationWorkDomain):
            output_elements = checked_mul_u64(checked_mul_u64(domain.batch, domain.m, "matrix output extent"), domain.n, "matrix output extent")
            macs = checked_mul_u64(output_elements, domain.k, "matrix MACs")
            work: list[WorkEstimate] = []
            _append_work(work, Engine.TENSOR, WorkUnit.MAC, input_dtype, macs)
            _append_phase(phases, work)
            if type(domain) is MatrixAccumulationWorkDomain:
                return tuple(phases)
            _append_phase(phases, _matrix_epilogue_work(opcode, attrs, output_elements, output_dtype, command_phase=False))
            return tuple(phases)
        output_elements = checked_mul_u64(checked_mul_u64(domain.batch, domain.m, "matrix output extent"), domain.n, "matrix output extent")
        _append_phase(phases, _matrix_epilogue_work(opcode, attrs, output_elements, output_dtype, command_phase=True))
        return tuple(phases)
    if type(domain) is MetadataWorkDomain:
        return ()
    if type(domain) is ElementWorkDomain:
        elements = domain.elements
        dtype = output_dtype
        work = []
        if opcode in (OpCode.ADD, OpCode.SUB, OpCode.MUL, OpCode.DIV):
            _append_work(work, Engine.VECTOR, WorkUnit(opcode.value), dtype, elements)
            if opcode in (OpCode.ADD, OpCode.SUB) and attrs.alpha != 1.0:
                _append_work(work, Engine.VECTOR, WorkUnit.MUL, dtype, elements)
        elif opcode is OpCode.RELU:
            _append_work(work, Engine.VECTOR, WorkUnit.MAX, dtype, elements)
        elif opcode is OpCode.GELU and attrs.approximation == "none":
            _append_work(work, Engine.VECTOR, WorkUnit.DIV, dtype, elements)
            _append_work(work, Engine.VECTOR, WorkUnit.ERF, dtype, elements)
            _append_work(work, Engine.VECTOR, WorkUnit.ADD, dtype, elements)
            _append_work(work, Engine.VECTOR, WorkUnit.MUL, dtype, _scaled(elements, 2))
        elif opcode is OpCode.GELU:
            _append_work(work, Engine.VECTOR, WorkUnit.MUL, dtype, _scaled(elements, 6))
            _append_work(work, Engine.VECTOR, WorkUnit.ADD, dtype, _scaled(elements, 2))
            _append_work(work, Engine.VECTOR, WorkUnit.TANH, dtype, elements)
        elif opcode is OpCode.SILU:
            for unit in (WorkUnit.NEGATE, WorkUnit.EXP, WorkUnit.ADD, WorkUnit.DIV):
                _append_work(work, Engine.VECTOR, unit, dtype, elements)
        elif opcode in (OpCode.EXP, OpCode.RSQRT):
            _append_work(work, Engine.VECTOR, WorkUnit(opcode.value), dtype, elements)
        elif opcode in (OpCode.CONTIGUOUS_COPY, OpCode.CONCAT):
            _append_work(work, Engine.VECTOR, WorkUnit.COPY, dtype, elements)
        elif opcode in (OpCode.GATHER_ROWS, OpCode.EMBEDDING_LOOKUP):
            _append_work(work, Engine.VECTOR, WorkUnit.GATHER, dtype, elements)
        _append_phase(phases, work)
        return tuple(phases)
    rows = domain.rows
    fan_in = domain.fan_in
    elements = checked_mul_u64(rows, fan_in, "row work elements")
    comparisons = checked_mul_u64(rows, max(fan_in - 1, 0), "row reduction work")
    if opcode in _REDUCE_OPS:
        dtype = attrs.accum_dtype
        reduction: list[WorkEstimate] = []
        _append_work(reduction, Engine.REDUCE, WorkUnit.MAX if opcode is OpCode.REDUCE_MAX else WorkUnit.ADD, dtype, comparisons)
        _append_phase(phases, reduction)
        if opcode is OpCode.REDUCE_MEAN:
            vector: list[WorkEstimate] = []
            _append_work(vector, Engine.VECTOR, WorkUnit.DIV, dtype, rows)
            _append_phase(phases, vector)
        return tuple(phases)
    if elements == 0:
        return ()
    dtype = input_dtype.accumulation
    if opcode is OpCode.SOFTMAX:
        if attrs.zero_fully_masked_rows:
            predicate: list[WorkEstimate] = []
            _append_work(predicate, Engine.VECTOR, WorkUnit.PREDICATE, dtype, elements)
            _append_phase(phases, predicate)
        maximum: list[WorkEstimate] = []
        if attrs.zero_fully_masked_rows:
            _append_work(maximum, Engine.REDUCE, WorkUnit.LOGICAL_AND, dtype, comparisons)
        _append_work(maximum, Engine.REDUCE, WorkUnit.MAX, dtype, comparisons)
        _append_phase(phases, maximum)
        exponent: list[WorkEstimate] = []
        _append_work(exponent, Engine.VECTOR, WorkUnit.SUB, dtype, elements)
        _append_work(exponent, Engine.VECTOR, WorkUnit.EXP, dtype, elements)
        _append_phase(phases, exponent)
        total: list[WorkEstimate] = []
        _append_work(total, Engine.REDUCE, WorkUnit.ADD, dtype, comparisons)
        _append_phase(phases, total)
        normalize: list[WorkEstimate] = []
        _append_work(normalize, Engine.VECTOR, WorkUnit.DIV, dtype, elements)
        if attrs.zero_fully_masked_rows:
            _append_work(normalize, Engine.VECTOR, WorkUnit.SELECT, dtype, elements)
        _append_phase(phases, normalize)
        return tuple(phases)
    if opcode is OpCode.LAYERNORM:
        first_reduction: list[WorkEstimate] = []
        _append_work(first_reduction, Engine.REDUCE, WorkUnit.ADD, dtype, comparisons)
        _append_phase(phases, first_reduction)
        center: list[WorkEstimate] = []
        _append_work(center, Engine.VECTOR, WorkUnit.DIV, dtype, rows)
        _append_work(center, Engine.VECTOR, WorkUnit.SUB, dtype, elements)
        _append_work(center, Engine.VECTOR, WorkUnit.MUL, dtype, elements)
        _append_phase(phases, center)
        second_reduction: list[WorkEstimate] = []
        _append_work(second_reduction, Engine.REDUCE, WorkUnit.ADD, dtype, comparisons)
        _append_phase(phases, second_reduction)
        normalize = []
        _append_work(normalize, Engine.VECTOR, WorkUnit.DIV, dtype, rows)
        _append_work(normalize, Engine.VECTOR, WorkUnit.ADD, dtype, rows)
        _append_work(normalize, Engine.VECTOR, WorkUnit.RSQRT, dtype, rows)
        _append_work(normalize, Engine.VECTOR, WorkUnit.MUL, dtype, elements)
        if attrs.has_weight:
            _append_work(normalize, Engine.VECTOR, WorkUnit.MUL, dtype, elements)
        if attrs.has_bias:
            _append_work(normalize, Engine.VECTOR, WorkUnit.ADD, dtype, elements)
        _append_phase(phases, normalize)
        return tuple(phases)
    square: list[WorkEstimate] = []
    _append_work(square, Engine.VECTOR, WorkUnit.MUL, dtype, elements)
    _append_phase(phases, square)
    reduction = []
    _append_work(reduction, Engine.REDUCE, WorkUnit.ADD, dtype, comparisons)
    _append_phase(phases, reduction)
    normalize = []
    _append_work(normalize, Engine.VECTOR, WorkUnit.DIV, dtype, rows)
    _append_work(normalize, Engine.VECTOR, WorkUnit.ADD, dtype, rows)
    _append_work(normalize, Engine.VECTOR, WorkUnit.RSQRT, dtype, rows)
    _append_work(normalize, Engine.VECTOR, WorkUnit.MUL, dtype, elements)
    if attrs.has_weight:
        _append_work(normalize, Engine.VECTOR, WorkUnit.MUL, dtype, elements)
    _append_phase(phases, normalize)
    return tuple(phases)


def estimate_work(
    opcode: OpCode,
    attrs: OpAttrs,
    operand_dtypes: tuple[DType, ...],
    output_dtype: DType,
    domain: WorkDomain,
) -> tuple[WorkEstimate, ...]:
    aggregate: list[WorkEstimate] = []
    for phase in estimate_work_phases(opcode, attrs, operand_dtypes, output_dtype, domain):
        for item in phase.work:
            _append_work(aggregate, item.engine, item.unit, item.dtype, item.operations)
    return tuple(aggregate)


def _concrete_shape(value: GraphValue) -> tuple[int, ...]:
    if type(value) is not GraphValue or type(value.shape) is not tuple or type(value.strides) is not tuple:
        raise MeshIrError("E_EXPORT_LAYOUT", "cost tensor record is invalid")
    if any(type(item) is not Const for item in value.shape) or any(type(item) not in (FixedStride, ShapeProductStride) for item in value.strides):
        raise MeshIrError("E_SHAPE_UNBOUND", "cost annotation requires concrete dimensions and strides", value_id=value.value_id)
    return tuple(item.value for item in value.shape)


def _concrete_strides(value: GraphValue) -> tuple[int, ...]:
    result = []
    for stride in value.strides:
        try:
            result.append(stride.evaluate({}))
        except MeshIrError as error:
            if error.code == "E_SHAPE_UNBOUND":
                raise MeshIrError("E_SHAPE_UNBOUND", "cost annotation requires concrete dimensions and strides", value_id=value.value_id) from error
            raise
    return tuple(result)


def _product(values: tuple[int, ...], field: str) -> int:
    result = 1
    for value in values:
        result = checked_mul_u64(result, value, field)
    return result


def _tensor_extents(value: GraphValue) -> tuple[int, int, int]:
    shape = _concrete_shape(value)
    strides = _concrete_strides(value)
    elements = _product(shape, "tensor logical elements")
    logical = checked_mul_u64(elements, value.dtype.byte_width, "tensor logical bytes")
    if any(item == 0 for item in shape):
        storage = 0
    else:
        storage_elements = checked_add_u64(value.storage_offset, 1, "tensor storage span")
        for dimension, stride in zip(shape, strides):
            contribution = checked_mul_u64(dimension - 1, stride, "tensor storage span")
            storage_elements = checked_add_u64(storage_elements, contribution, "tensor storage span")
        storage = checked_mul_u64(storage_elements, value.dtype.byte_width, "tensor storage bytes")
    if value.logical_extent_bytes is not None and value.logical_extent_bytes != logical:
        raise MeshIrError("E_EXPORT_LAYOUT", "tensor logical extent metadata is inconsistent", value_id=value.value_id)
    if value.storage_extent_bytes is not None and value.storage_extent_bytes != storage:
        raise MeshIrError("E_EXPORT_LAYOUT", "tensor storage extent metadata is inconsistent", value_id=value.value_id)
    return elements, logical, storage


def _algorithm(opcode: OpCode, attrs: object) -> CostAlgorithm:
    if opcode in _MATRIX_OPS:
        return CostAlgorithm.MATRIX
    if opcode in _BINARY_OPS or opcode in (OpCode.RELU, OpCode.EXP, OpCode.RSQRT):
        return CostAlgorithm.ELEMENTWISE
    if opcode is OpCode.GELU:
        return CostAlgorithm.GELU_TANH if attrs.approximation == "tanh" else CostAlgorithm.GELU_EXACT
    if opcode is OpCode.SILU:
        return CostAlgorithm.SILU
    if opcode in METADATA_VIEW_OPCODES:
        return CostAlgorithm.METADATA_VIEW
    if opcode is OpCode.CONTIGUOUS_COPY:
        return CostAlgorithm.COPY
    if opcode is OpCode.CONCAT:
        return CostAlgorithm.CONCAT
    if opcode in (OpCode.GATHER_ROWS, OpCode.EMBEDDING_LOOKUP):
        return CostAlgorithm.GATHER
    if opcode in _REDUCE_OPS:
        return CostAlgorithm.REDUCTION
    if opcode is OpCode.SOFTMAX:
        return CostAlgorithm.SOFTMAX
    if opcode is OpCode.LAYERNORM:
        return CostAlgorithm.LAYERNORM
    if opcode is OpCode.RMSNORM:
        return CostAlgorithm.RMSNORM
    raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "operation has no cost algorithm", opcode=opcode.value)


def _work_domain(op: GraphOp, operands: tuple[GraphValue, ...], result: GraphValue) -> WorkDomain:
    result_shape = _concrete_shape(result)
    if op.opcode in _MATRIX_OPS:
        lhs_shape = list(_concrete_shape(operands[0]))
        if op.attrs.lhs_transpose:
            lhs_shape[-2], lhs_shape[-1] = lhs_shape[-1], lhs_shape[-2]
        return MatrixWorkDomain(_product(result_shape[:-2], "matrix batch"), result_shape[-2], result_shape[-1], lhs_shape[-1])
    if op.opcode in METADATA_VIEW_OPCODES:
        return MetadataWorkDomain()
    if op.opcode in _REDUCE_OPS:
        input_shape = _concrete_shape(operands[0])
        axes = set(op.attrs.axes)
        return RowWorkDomain(_product(tuple(value for index, value in enumerate(input_shape) if index not in axes), "reduction rows"), _product(tuple(value for index, value in enumerate(input_shape) if index in axes), "reduction fan-in"))
    if op.opcode is OpCode.SOFTMAX:
        input_shape = _concrete_shape(operands[0])
        return RowWorkDomain(_product(tuple(value for index, value in enumerate(input_shape) if index != op.attrs.axis), "softmax rows"), input_shape[op.attrs.axis])
    if op.opcode in (OpCode.LAYERNORM, OpCode.RMSNORM):
        input_shape = _concrete_shape(operands[0])
        axes = set(op.attrs.axes)
        return RowWorkDomain(_product(tuple(value for index, value in enumerate(input_shape) if index not in axes), "normalization rows"), _product(tuple(value for index, value in enumerate(input_shape) if index in axes), "normalization fan-in"))
    return ElementWorkDomain(_product(result_shape, "operation result elements"))


def estimate_operation_cost(op: GraphOp, operands: tuple[GraphValue, ...], result: GraphValue) -> OperationCost:
    if type(op) is not GraphOp or type(operands) is not tuple or any(type(item) is not GraphValue for item in operands) or type(result) is not GraphValue:
        raise MeshIrError("E_CONFIG", "operation cost input records have invalid types")
    if op.operands != tuple(item.value_id for item in operands) or op.results != (result.value_id,):
        raise MeshIrError("E_ABI_BOUNDS", "operation cost values do not match operation references", op_id=op.op_id)
    operand_data = tuple(TensorDataContract(item.shape, item.dtype) for item in operands)
    result_data = TensorDataContract(result.shape, result.dtype)
    if op.opcode in METADATA_VIEW_OPCODES:
        verify_operation_type_contract(op.opcode, op.attrs, tuple(item.dtype for item in operands), result.dtype, op_id=op.op_id)
    else:
        verify_operation_data_contract(op.op_id, op.opcode, op.attrs, operand_data, result_data)
    facts = [_tensor_extents(item) for item in operands]
    result_elements, result_logical, result_storage = _tensor_extents(result)
    metadata = op.opcode in METADATA_VIEW_OPCODES
    accessed = [0 if metadata or result_elements == 0 else logical for _, logical, _ in facts]
    if op.opcode in (OpCode.GATHER_ROWS, OpCode.EMBEDDING_LOOKUP) and result_elements:
        accessed[0] = checked_mul_u64(result_elements, operands[0].dtype.byte_width, "selected source access bytes")
    operand_accesses = tuple(
        TensorAccessEstimate(value.value_id, logical, storage, access)
        for value, (_, logical, storage), access in zip(operands, facts, accessed)
    )
    result_access = TensorAccessEstimate(result.value_id, result_logical, result_storage, 0 if metadata else result_logical)
    domain = _work_domain(op, operands, result)
    work = estimate_work(op.opcode, op.attrs, tuple(item.dtype for item in operands), result.dtype, domain)
    accumulation_dtype = (
        op.attrs.accum_dtype if type(op.attrs) in (MatmulAttrs, ReduceAttrs) else
        operands[0].dtype.accumulation if type(op.attrs) in (SoftmaxAttrs, NormAttrs) else
        None
    )
    return OperationCost(
        _algorithm(op.opcode, op.attrs),
        operand_accesses,
        result_access,
        0 if metadata else result_storage,
        accumulation_dtype,
        work,
    )


__all__ = [
    "COST_MODEL_VERSION",
    "CostAlgorithm",
    "ElementWorkDomain",
    "ExecutionWorkPhase",
    "MatrixAccumulationWorkDomain",
    "MatrixEpilogueWorkDomain",
    "MatrixWorkDomain",
    "MetadataWorkDomain",
    "OperationCost",
    "RowWorkDomain",
    "TensorAccessEstimate",
    "WorkDomain",
    "WorkEstimate",
    "WorkUnit",
    "estimate_operation_cost",
    "estimate_work",
    "estimate_work_phases",
]
