from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from mesh_ir.canonical import JSON_SAFE_INTEGER_MAX, U64_MAX, semantic_sha256, strict_json_loads
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import (
    Access,
    Add,
    CeilDivByConst,
    Const,
    DType,
    DimExpr,
    FixedStride,
    FloorDivByConst,
    MulByConst,
    ShapeProductStride,
    StrideExpr,
    Symbol,
    TensorRole,
)
from mesh_ir.ir.graph_ir import METADATA_VIEW_OPCODES
from mesh_ir.schema import validate_schema


if TYPE_CHECKING:
    from mesh_ir.ir.graph_ir import OpAttrs, OpCode


U32_MAX = (1 << 32) - 1
_DIGEST = re.compile(r"[0-9a-f]{64}")
_VIEW_OPS = frozenset(item.value for item in METADATA_VIEW_OPCODES)
_BINARY_OPS = frozenset(("ADD", "SUB", "MUL", "DIV"))
_UNARY_OPS = frozenset(("RELU", "GELU", "SILU", "EXP", "RSQRT"))
_FLOAT_DTYPES = frozenset((DType.FP32, DType.FP16, DType.BF16))


@dataclass(frozen=True)
class TensorDataContract:
    shape: tuple[DimExpr, ...]
    dtype: DType


@dataclass(frozen=True)
class TensorLayoutContract(TensorDataContract):
    value_id: int
    strides: tuple[StrideExpr, ...]
    storage_offset: int
    alias_root: int
    access: Access


class MatrixResultForm(str, Enum):
    SEMANTIC = "SEMANTIC"
    ACCUMULATION = "ACCUMULATION"


@dataclass(frozen=True)
class _OperationDataContext:
    op_id: int | None
    opcode: OpCode
    attrs: OpAttrs


def _u32(value: object) -> bool:
    return type(value) is int and 1 <= value <= U32_MAX


def _int_tuple(value: object) -> bool:
    return type(value) is tuple and all(type(item) is int for item in value)


def _finite_number(value: object) -> bool:
    if type(value) is int:
        return -JSON_SAFE_INTEGER_MAX <= value <= JSON_SAFE_INTEGER_MAX
    return type(value) is float and math.isfinite(value)


def _verify_dimension_type(expr: object) -> None:
    if type(expr) is Const:
        if type(expr.value) is not int or not 0 <= expr.value <= U64_MAX:
            raise MeshIrError("E_SHAPE_PROFILE_MISMATCH", "constant dimension is invalid")
        return
    if type(expr) is Symbol:
        if not _u32(expr.symbol_id) or type(expr.name) is not str or not expr.name:
            raise MeshIrError("E_CONFIG", "symbol identity is invalid")
        if type(expr.minimum) is not int or type(expr.maximum) is not int or type(expr.multiple_of) is not int:
            raise MeshIrError("E_CONFIG", "symbol bounds have invalid types", symbol=expr.name)
        first_multiple = ((expr.minimum + expr.multiple_of - 1) // expr.multiple_of) * expr.multiple_of if expr.multiple_of > 0 else U64_MAX + 1
        if not 0 <= expr.minimum <= expr.maximum <= U64_MAX or expr.multiple_of < 1 or expr.multiple_of > U64_MAX or first_multiple > expr.maximum:
            raise MeshIrError("E_CONFIG", "symbol bounds are invalid", symbol=expr.name)
        return
    if type(expr) is Add:
        _verify_dimension_type(expr.lhs)
        _verify_dimension_type(expr.rhs)
        return
    if type(expr) is MulByConst:
        _verify_dimension_type(expr.value)
        if type(expr.factor) is not int or not 0 <= expr.factor <= U64_MAX:
            raise MeshIrError("E_CONFIG", "dimension multiplier is invalid")
        return
    if type(expr) in (FloorDivByConst, CeilDivByConst):
        _verify_dimension_type(expr.value)
        if type(expr.divisor) is not int or not 1 <= expr.divisor <= U64_MAX:
            raise MeshIrError("E_CONFIG", "dimension divisor is invalid")
        return
    raise MeshIrError("E_CONFIG", "dimension expression type is invalid", type=type(expr).__name__)


def _verify_stride_type(expr: object) -> None:
    if type(expr) is FixedStride:
        if type(expr.value) is not int or not 0 <= expr.value <= U64_MAX:
            raise MeshIrError("E_EXPORT_LAYOUT", "fixed stride is invalid")
        return
    if type(expr) is ShapeProductStride:
        if type(expr.dimensions) is not tuple or type(expr.factor) is not int or not 0 <= expr.factor <= U64_MAX:
            raise MeshIrError("E_EXPORT_LAYOUT", "derived stride is invalid")
        for dimension in expr.dimensions:
            _verify_dimension_type(dimension)
        return
    raise MeshIrError("E_EXPORT_LAYOUT", "stride expression type is invalid", type=type(expr).__name__)


def _verify_pytree_type(spec: object) -> None:
    from mesh_ir.ir.graph_ir import PytreeKind, PytreeSpec

    if type(spec) is not PytreeSpec or type(spec.kind) is not PytreeKind or type(spec.children) is not tuple or type(spec.keys) is not tuple:
        raise MeshIrError("E_ABI_BOUNDS", "pytree record has invalid field types")
    if any(type(key) is not str for key in spec.keys):
        raise MeshIrError("E_ABI_BOUNDS", "pytree keys must be strings")
    for child in spec.children:
        _verify_pytree_type(child)


def verify_operation_attribute_types(attrs: object) -> None:
    from mesh_ir.ir.graph_ir import ElementwiseAttrs, EmbeddingAttrs, MatmulAttrs, MovementAttrs, NormAttrs, ReduceAttrs, SoftmaxAttrs, ViewAttrs

    if type(attrs) is MatmulAttrs:
        if not _int_tuple(attrs.batch_axes) or type(attrs.lhs_contract_axis) is not int or type(attrs.rhs_contract_axis) is not int:
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "matmul attributes have invalid types")
        if type(attrs.lhs_transpose) is not bool or type(attrs.rhs_transpose) is not bool or not _finite_number(attrs.alpha) or not _finite_number(attrs.beta) or type(attrs.accum_dtype) is not DType:
            raise MeshIrError("E_EXPORT_DTYPE", "matmul attributes have invalid types")
        return
    if type(attrs) is ViewAttrs:
        if type(attrs.shape) is not tuple:
            raise MeshIrError("E_EXPORT_LAYOUT", "view shape has invalid type")
        for dimension in attrs.shape:
            _verify_dimension_type(dimension)
        if not all(_int_tuple(item) for item in (attrs.permutation, attrs.axes, attrs.starts, attrs.ends, attrs.steps, attrs.expanded_axes)):
            raise MeshIrError("E_EXPORT_LAYOUT", "view attributes have invalid types")
        return
    if type(attrs) is MovementAttrs:
        if type(attrs.axis) is not int or type(attrs.bounds_check) is not bool:
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "movement attributes have invalid types")
        return
    if type(attrs) is ElementwiseAttrs:
        if attrs.scalar is not None and type(attrs.scalar) not in (bool, int, float):
            raise MeshIrError("E_EXPORT_DTYPE", "elementwise scalar type is invalid")
        if type(attrs.scalar_side) is not str or not _finite_number(attrs.alpha) or type(attrs.approximation) is not str:
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "elementwise attributes have invalid types")
        if type(attrs.scalar) is float and not math.isfinite(attrs.scalar):
            raise MeshIrError("E_EXPORT_DTYPE", "elementwise scalar must be finite")
        if type(attrs.scalar) is int and not -JSON_SAFE_INTEGER_MAX <= attrs.scalar <= JSON_SAFE_INTEGER_MAX:
            raise MeshIrError("E_EXPORT_DTYPE", "elementwise scalar is outside the canonical numeric range")
        return
    if type(attrs) is ReduceAttrs:
        if not _int_tuple(attrs.axes) or type(attrs.keepdim) is not bool or type(attrs.output_dtype) is not DType or type(attrs.accum_dtype) is not DType or type(attrs.order) is not str:
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "reduction attributes have invalid types")
        return
    if type(attrs) is NormAttrs:
        if not _int_tuple(attrs.axes) or not _finite_number(attrs.epsilon) or type(attrs.has_weight) is not bool or type(attrs.has_bias) is not bool:
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "normalization attributes have invalid types")
        return
    if type(attrs) is SoftmaxAttrs:
        if type(attrs.axis) is not int or type(attrs.output_dtype) is not DType or type(attrs.zero_fully_masked_rows) is not bool:
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "softmax attributes have invalid types")
        return
    if type(attrs) is EmbeddingAttrs:
        if type(attrs.padding_idx) is not int or type(attrs.scale_grad_by_freq) is not bool or type(attrs.sparse) is not bool:
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "embedding attributes have invalid types")
        if attrs.max_norm is not None and not _finite_number(attrs.max_norm):
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "embedding max_norm has invalid type")
        if not _finite_number(attrs.norm_type):
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "embedding norm_type has invalid type")
        return
    raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "operation attribute record type is invalid", type=type(attrs).__name__)


def verify_graph_types(graph, allow_empty_semantic_hash: bool = False) -> None:
    from mesh_ir.ir.graph_ir import GraphFunction, GraphOp, GraphValue, OpCode

    if type(graph.schema_major) is not int or type(graph.schema_minor) is not int:
        raise MeshIrError("E_ABI_VERSION", "Graph schema version has invalid types")
    if type(graph.required_features) is not tuple or any(type(item) is not str for item in graph.required_features):
        raise MeshIrError("E_ABI_VERSION", "Graph required features have invalid types")
    for field in (graph.arch_digest, graph.source_semantic_hash, graph.semantic_sha256, graph.entrypoint, graph.profile_id, graph.decomposition_digest):
        if type(field) is not str:
            raise MeshIrError("E_CONFIG", "Graph identity field has invalid type")
    if not allow_empty_semantic_hash and not graph.semantic_sha256:
        raise MeshIrError("E_ABI_CHECKSUM", "Graph semantic hash is absent")
    if type(graph.values) is not tuple or type(graph.functions) is not tuple or type(graph.symbols) is not tuple or type(graph.profile_bindings) is not tuple or type(graph.debug_locations) is not tuple:
        raise MeshIrError("E_CONFIG", "Graph collections must be immutable tuples")
    for symbol in graph.symbols:
        if type(symbol) is not Symbol:
            raise MeshIrError("E_CONFIG", "Graph symbol record has invalid type")
        _verify_dimension_type(symbol)
    for binding in graph.profile_bindings:
        if type(binding) is not tuple or len(binding) != 2 or not _u32(binding[0]) or type(binding[1]) is not int or not 0 <= binding[1] <= U64_MAX:
            raise MeshIrError("E_SHAPE_PROFILE_MISMATCH", "profile binding record is invalid")
    for item in graph.debug_locations:
        if type(item) is not tuple or len(item) != 2 or any(type(part) is not str for part in item):
            raise MeshIrError("E_EXPORT_LAYOUT", "debug location record has invalid types")
    for item in graph.values:
        if type(item) is not GraphValue or not _u32(item.value_id) or type(item.name) is not str or not item.name:
            raise MeshIrError("E_ABI_BOUNDS", "Graph value identity is invalid")
        if type(item.role) is not TensorRole or type(item.dtype) is not DType or type(item.access) is not Access:
            raise MeshIrError("E_ABI_ENUM", "Graph value enum is invalid", value_id=item.value_id)
        if type(item.shape) is not tuple or type(item.strides) is not tuple or len(item.shape) != len(item.strides):
            raise MeshIrError("E_EXPORT_LAYOUT", "Graph value rank metadata is invalid", value_id=item.value_id)
        for dimension in item.shape:
            _verify_dimension_type(dimension)
        for stride in item.strides:
            _verify_stride_type(stride)
        if type(item.storage_offset) is not int or not 0 <= item.storage_offset <= U64_MAX or not _u32(item.alias_root):
            raise MeshIrError("E_EXPORT_LAYOUT", "Graph value storage metadata is invalid", value_id=item.value_id)
        for extent in (item.logical_extent_bytes, item.storage_extent_bytes):
            if extent is not None and (type(extent) is not int or not 0 <= extent <= U64_MAX):
                raise MeshIrError("E_EXPORT_LAYOUT", "Graph value extent is invalid", value_id=item.value_id)
        if item.content_sha256 is not None and type(item.content_sha256) is not str:
            raise MeshIrError("E_ABI_CHECKSUM", "Graph value content digest type is invalid", value_id=item.value_id)
    for function in graph.functions:
        if type(function) is not GraphFunction or not _u32(function.function_id) or type(function.name) is not str or not function.name:
            raise MeshIrError("E_ABI_BOUNDS", "Graph function identity is invalid")
        if not _int_tuple(function.inputs) or not _int_tuple(function.outputs) or type(function.ops) is not tuple:
            raise MeshIrError("E_ABI_BOUNDS", "Graph function references have invalid types", function_id=function.function_id)
        _verify_pytree_type(function.input_pytree)
        _verify_pytree_type(function.output_pytree)
        for op in function.ops:
            if type(op) is not GraphOp or not _u32(op.op_id) or type(op.opcode) is not OpCode:
                raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "Graph operation identity is invalid")
            if not _int_tuple(op.operands) or not _int_tuple(op.results) or type(op.source_node_id) is not str or not op.source_node_id:
                raise MeshIrError("E_ABI_BOUNDS", "Graph operation references have invalid types", op_id=op.op_id)
            verify_operation_attribute_types(op.attrs)


def _checked(value: int, field: str) -> int:
    if not 0 <= value <= U64_MAX:
        raise MeshIrError("E_ABI_OVERFLOW", "Graph shape arithmetic overflows u64", field=field)
    return value


def _dim_bounds(expr) -> tuple[int, int]:
    if type(expr) is Const:
        return expr.value, expr.value
    if type(expr) is Symbol:
        return expr.minimum, expr.maximum
    if type(expr) is Add:
        lhs = _dim_bounds(expr.lhs)
        rhs = _dim_bounds(expr.rhs)
        return _checked(lhs[0] + rhs[0], "dimension minimum"), _checked(lhs[1] + rhs[1], "dimension maximum")
    if type(expr) is MulByConst:
        bounds = _dim_bounds(expr.value)
        return _checked(bounds[0] * expr.factor, "dimension minimum"), _checked(bounds[1] * expr.factor, "dimension maximum")
    bounds = _dim_bounds(expr.value)
    if type(expr) is FloorDivByConst:
        return bounds[0] // expr.divisor, bounds[1] // expr.divisor
    return (bounds[0] + expr.divisor - 1) // expr.divisor, _checked(bounds[1] + expr.divisor - 1, "ceiling dimension") // expr.divisor


def _poly_add(lhs, rhs):
    result = dict(lhs)
    for term, coefficient in rhs.items():
        result[term] = result.get(term, 0) + coefficient
        if result[term] == 0:
            del result[term]
    return result


def _poly_scale(value, factor: int):
    return {term: coefficient * factor for term, coefficient in value.items() if coefficient * factor}


def _poly_mul(lhs, rhs):
    result = {}
    for left_term, left_coefficient in lhs.items():
        for right_term, right_coefficient in rhs.items():
            term = tuple(sorted(left_term + right_term))
            result[term] = result.get(term, 0) + left_coefficient * right_coefficient
    return {term: coefficient for term, coefficient in result.items() if coefficient}


def _poly_key(value):
    return tuple(sorted((term, coefficient) for term, coefficient in value.items()))


def _dim_poly(expr):
    if type(expr) is Const:
        return {(): expr.value}
    if type(expr) is Symbol:
        return {(('symbol', expr.symbol_id, expr.name, expr.minimum, expr.maximum, expr.multiple_of),): 1}
    if type(expr) is Add:
        return _poly_add(_dim_poly(expr.lhs), _dim_poly(expr.rhs))
    if type(expr) is MulByConst:
        return _poly_scale(_dim_poly(expr.value), expr.factor)
    value = _dim_poly(expr.value)
    divisor = expr.divisor
    if type(expr) is CeilDivByConst:
        value = _poly_add(value, {(): divisor - 1})
    if set(value) <= {()}:
        return {(): value.get((), 0) // divisor}
    return {(('floor', divisor, _poly_key(value)),): 1}


def _product_poly(values):
    result = {(): 1}
    for value in values:
        result = _poly_mul(result, _dim_poly(value))
    return result


def _constant_poly(value) -> int | None:
    if any(term for term in value):
        return None
    return value.get((), 0)


def _dim_equal(lhs, rhs) -> bool:
    return _dim_poly(lhs) == _dim_poly(rhs)


def _shape_equal(lhs, rhs) -> bool:
    return len(lhs) == len(rhs) and all(_dim_equal(left, right) for left, right in zip(lhs, rhs))


def _stride_poly(stride):
    if type(stride) is FixedStride:
        return {(): stride.value}
    return _poly_scale(_product_poly(stride.dimensions), stride.factor)


def _stride_bounds(stride) -> tuple[int, int]:
    if type(stride) is FixedStride:
        return stride.value, stride.value
    minimum = maximum = stride.factor
    for dimension in stride.dimensions:
        bounds = _dim_bounds(dimension)
        minimum = _checked(minimum * bounds[0], "stride minimum")
        maximum = _checked(maximum * bounds[1], "stride maximum")
    return minimum, maximum


def _shape_product_bounds(shape) -> tuple[int, int]:
    minimum = maximum = 1
    for dimension in shape:
        bounds = _dim_bounds(dimension)
        minimum = _checked(minimum * bounds[0], "logical extent minimum")
        maximum = _checked(maximum * bounds[1], "logical extent maximum")
    return minimum, maximum


def _storage_element_bounds(value) -> tuple[int, int]:
    shape_bounds = tuple(_dim_bounds(item) for item in value.shape)
    if any(maximum == 0 for _, maximum in shape_bounds):
        return 0, 0
    can_be_empty = any(minimum == 0 for minimum, _ in shape_bounds)
    minimum = 0 if can_be_empty else value.storage_offset + 1
    maximum = value.storage_offset + 1
    for dimension, stride in zip(shape_bounds, value.strides):
        stride_bounds = _stride_bounds(stride)
        if not can_be_empty:
            minimum = _checked(minimum + (dimension[0] - 1) * stride_bounds[0], "storage extent minimum")
        maximum = _checked(maximum + max(0, dimension[1] - 1) * stride_bounds[1], "storage extent maximum")
    return minimum, maximum


def _storage_poly(value):
    if any(_dim_bounds(item)[1] == 0 for item in value.shape):
        return {(): 0}
    result = {(): value.storage_offset + 1}
    for dimension, stride in zip(value.shape, value.strides):
        result = _poly_add(result, _poly_mul(_poly_add(_dim_poly(dimension), {(): -1}), _stride_poly(stride)))
    return result


def _layout_is_nonoverlapping(value) -> bool:
    axes = [index for index, dimension in enumerate(value.shape) if _dim_bounds(dimension)[1] > 1 and _stride_bounds(value.strides[index])[1] > 0]
    concrete = all(_dim_bounds(value.shape[index])[0] == _dim_bounds(value.shape[index])[1] and _stride_bounds(value.strides[index])[0] == _stride_bounds(value.strides[index])[1] for index in axes)
    if concrete:
        ordered = sorted(axes, key=lambda index: _stride_bounds(value.strides[index])[0])
        span = 1
        for index in ordered:
            dimension = _dim_bounds(value.shape[index])[0]
            stride = _stride_bounds(value.strides[index])[0]
            if stride < span:
                return False
            span += (dimension - 1) * stride
        return True
    remaining = set(axes)
    expected = {(): 1}
    while remaining:
        match = next((index for index in sorted(remaining) if _stride_poly(value.strides[index]) == expected), None)
        if match is None:
            return False
        expected = _poly_mul(expected, _dim_poly(value.shape[match]))
        remaining.remove(match)
    return True


def _verify_value_layout(value, derived_view: bool) -> None:
    logical = _shape_product_bounds(value.shape)
    logical_bytes = tuple(_checked(item * value.dtype.byte_width, "logical byte extent") for item in logical)
    storage = _storage_element_bounds(value)
    storage_bytes = tuple(_checked(item * value.dtype.byte_width, "storage byte extent") for item in storage)
    if value.logical_extent_bytes is not None and (logical_bytes[0] != logical_bytes[1] or value.logical_extent_bytes != logical_bytes[0]):
        raise MeshIrError("E_EXPORT_LAYOUT", "recorded logical extent is inconsistent", value_id=value.value_id)
    if value.storage_extent_bytes is not None and (storage_bytes[0] != storage_bytes[1] or value.storage_extent_bytes != storage_bytes[0]):
        raise MeshIrError("E_EXPORT_LAYOUT", "recorded storage extent is inconsistent", value_id=value.value_id)
    zero_stride = any(_stride_bounds(stride)[1] == 0 and _dim_bounds(dimension)[1] > 1 for dimension, stride in zip(value.shape, value.strides))
    if zero_stride and value.access is not Access.READ_ONLY:
        raise MeshIrError("E_EXPORT_LAYOUT", "broadcast overlap requires read-only access", value_id=value.value_id)
    if not derived_view and not _layout_is_nonoverlapping(value):
        raise MeshIrError("E_EXPORT_LAYOUT", "tensor layout overlap cannot be disproved", value_id=value.value_id)


def _normalize_axis(axis: int, rank: int, op_id: int) -> int:
    if not -rank <= axis < rank:
        raise MeshIrError("E_EXPORT_LAYOUT", "operation axis is out of range", op_id=op_id, axis=axis)
    return axis % rank


def _canonical_axis(axis: int, rank: int, op_id: int) -> int:
    if not 0 <= axis < rank:
        raise MeshIrError("E_EXPORT_LAYOUT", "operation axis is not canonical", op_id=op_id, axis=axis)
    return axis


def _broadcast_shape(lhs, rhs, op_id: int):
    result = []
    for index in range(1, max(len(lhs), len(rhs)) + 1):
        left = lhs[-index] if index <= len(lhs) else Const(1)
        right = rhs[-index] if index <= len(rhs) else Const(1)
        if _dim_equal(left, right):
            result.append(left)
        elif _constant_poly(_dim_poly(left)) == 1:
            result.append(right)
        elif _constant_poly(_dim_poly(right)) == 1:
            result.append(left)
        else:
            raise MeshIrError("E_EXPORT_LAYOUT", "tensor dimensions do not broadcast", op_id=op_id)
    return tuple(reversed(result))


def _require_shape(actual, expected, op_id: int, message: str) -> None:
    if not _shape_equal(actual, expected):
        raise MeshIrError("E_EXPORT_LAYOUT", message, op_id=op_id)


def _verify_matmul(op, operands, output, matrix_result_form: MatrixResultForm) -> None:
    attrs = op.attrs
    lhs, rhs = operands[:2]
    lhs_shape = list(lhs.shape)
    rhs_shape = list(rhs.shape)
    if len(lhs_shape) < 2 or len(rhs_shape) < 2:
        raise MeshIrError("E_EXPORT_LAYOUT", "matrix operands require rank at least two", op_id=op.op_id)
    if attrs.lhs_transpose:
        lhs_shape[-2], lhs_shape[-1] = lhs_shape[-1], lhs_shape[-2]
    if attrs.rhs_transpose:
        rhs_shape[-2], rhs_shape[-1] = rhs_shape[-1], rhs_shape[-2]
    if op.opcode.value == "BMM" and (len(lhs_shape) != 3 or len(rhs_shape) != 3 or len(output.shape) != 3 or attrs.batch_axes != (0,)):
        raise MeshIrError("E_EXPORT_LAYOUT", "BMM requires exact rank-three batch semantics", op_id=op.op_id)
    if op.opcode.value == "BMM" and not _dim_equal(lhs_shape[0], rhs_shape[0]):
        raise MeshIrError("E_EXPORT_LAYOUT", "BMM batch dimensions must match exactly", op_id=op.op_id)
    lhs_axis = _normalize_axis(attrs.lhs_contract_axis, len(lhs_shape), op.op_id)
    rhs_axis = _normalize_axis(attrs.rhs_contract_axis, len(rhs_shape), op.op_id)
    if lhs_axis != len(lhs_shape) - 1 or rhs_axis != len(rhs_shape) - 2:
        raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "matrix contraction axes are unsupported", op_id=op.op_id)
    if not _dim_equal(lhs_shape[lhs_axis], rhs_shape[rhs_axis]):
        raise MeshIrError("E_EXPORT_LAYOUT", "matrix contraction dimensions differ", op_id=op.op_id)
    batch = _broadcast_shape(tuple(lhs_shape[:-2]), tuple(rhs_shape[:-2]), op.op_id)
    expected = batch + (lhs_shape[-2], rhs_shape[-1])
    _require_shape(output.shape, expected, op.op_id, "matrix result shape is inconsistent")
    shared_axes = tuple(index for index in range(len(batch)) if index >= len(batch) - len(lhs_shape[:-2]) and index >= len(batch) - len(rhs_shape[:-2]))
    if attrs.batch_axes != shared_axes:
        raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "matrix batch axes are inconsistent", op_id=op.op_id)
    if len(operands) == 3:
        bias = operands[2]
        if not _shape_equal(_broadcast_shape(bias.shape, output.shape, op.op_id), output.shape):
            raise MeshIrError("E_EXPORT_LAYOUT", "matrix bias is incompatible", op_id=op.op_id)


def _promote_tensor(lhs: DType, rhs: DType, division: bool) -> DType:
    if lhs is rhs:
        return DType.FP32 if division and lhs in (DType.INT8, DType.INT32) else lhs
    if DType.FP32 in (lhs, rhs) or {lhs, rhs} == {DType.FP16, DType.BF16}:
        return DType.FP32
    if DType.FP16 in (lhs, rhs):
        return DType.FP16
    if DType.BF16 in (lhs, rhs):
        return DType.BF16
    return DType.FP32 if division else DType.INT32


def _promote_scalar(dtype: DType, scalar: object, division: bool) -> DType:
    if division and dtype in (DType.INT8, DType.INT32):
        return DType.FP32
    if dtype in (DType.INT8, DType.INT32) and type(scalar) is float:
        return DType.FP32
    return dtype


def _verify_elementwise(op, operands, output) -> None:
    opcode = op.opcode.value
    if opcode in _BINARY_OPS:
        if len(operands) == 2:
            expected_shape = _broadcast_shape(operands[0].shape, operands[1].shape, op.op_id)
        else:
            expected_shape = operands[0].shape
        _require_shape(output.shape, expected_shape, op.op_id, "elementwise result shape is inconsistent")
        return
    input_value = operands[0]
    _require_shape(output.shape, input_value.shape, op.op_id, "unary result shape is inconsistent")


def _view_unused(attrs, allowed: tuple[str, ...]) -> bool:
    defaults = {"shape": (), "permutation": (), "axes": (), "starts": (), "ends": (), "steps": (), "expanded_axes": ()}
    return all(field in allowed or getattr(attrs, field) == default for field, default in defaults.items())


def _stride_equal(lhs, rhs) -> bool:
    return _stride_poly(lhs) == _stride_poly(rhs)


def _reshape_chunks(value):
    chunks = []
    product = None
    unit = None
    expected = None
    for dimension, stride in reversed(tuple(zip(value.shape, value.strides))):
        dimension_poly = _dim_poly(dimension)
        if _constant_poly(dimension_poly) == 1:
            continue
        stride_poly = _stride_poly(stride)
        if product is None or stride_poly != expected:
            if product is not None:
                chunks.append((_poly_key(product), _poly_key(unit)))
            product = dimension_poly
            unit = stride_poly
        else:
            product = _poly_mul(product, dimension_poly)
        expected = _poly_mul(stride_poly, dimension_poly)
    if product is not None:
        chunks.append((_poly_key(product), _poly_key(unit)))
    return tuple(chunks)


def _verify_view(op, input_value, output) -> None:
    attrs = op.attrs
    opcode = op.opcode.value
    if output.alias_root != input_value.alias_root:
        raise MeshIrError("E_EXPORT_LAYOUT", "view operation must preserve its alias root", op_id=op.op_id)
    if opcode == "RESHAPE_VIEW":
        identical_layout = _shape_equal(input_value.shape, output.shape) and all(_stride_equal(left, right) for left, right in zip(input_value.strides, output.strides))
        preserves_order = identical_layout or _reshape_chunks(input_value) == _reshape_chunks(output)
        if not _view_unused(attrs, ("shape",)) or not _shape_equal(attrs.shape, output.shape) or _product_poly(input_value.shape) != _product_poly(output.shape) or input_value.storage_offset != output.storage_offset or not _layout_is_nonoverlapping(output) or not preserves_order:
            raise MeshIrError("E_EXPORT_LAYOUT", "reshape view metadata is inconsistent", op_id=op.op_id)
        return
    if opcode in ("TRANSPOSE_VIEW", "PERMUTE_VIEW"):
        if not _view_unused(attrs, ("permutation",)) or len(attrs.permutation) != len(input_value.shape) or sorted(attrs.permutation) != list(range(len(input_value.shape))):
            raise MeshIrError("E_EXPORT_LAYOUT", "view permutation is invalid", op_id=op.op_id)
        if opcode == "TRANSPOSE_VIEW" and sum(index != source for index, source in enumerate(attrs.permutation)) not in (0, 2):
            raise MeshIrError("E_EXPORT_LAYOUT", "transpose must exchange exactly two axes", op_id=op.op_id)
        expected_shape = tuple(input_value.shape[index] for index in attrs.permutation)
        expected_strides = tuple(input_value.strides[index] for index in attrs.permutation)
        if not _shape_equal(output.shape, expected_shape) or len(output.strides) != len(expected_strides) or any(not _stride_equal(actual, expected) for actual, expected in zip(output.strides, expected_strides)) or input_value.storage_offset != output.storage_offset:
            raise MeshIrError("E_EXPORT_LAYOUT", "permutation view metadata is inconsistent", op_id=op.op_id)
        return
    if opcode == "SLICE_VIEW":
        if not _view_unused(attrs, ("axes", "starts", "ends", "steps")) or not len(attrs.axes) == len(attrs.starts) == len(attrs.ends) == len(attrs.steps) or not attrs.axes:
            raise MeshIrError("E_EXPORT_LAYOUT", "slice attributes are incomplete", op_id=op.op_id)
        axes = tuple(_canonical_axis(axis, len(input_value.shape), op.op_id) for axis in attrs.axes)
        if len(set(axes)) != len(axes) or any(step <= 0 for step in attrs.steps):
            raise MeshIrError("E_EXPORT_LAYOUT", "slice axes or steps are invalid", op_id=op.op_id)
        expected_shape = list(input_value.shape)
        expected_strides = list(input_value.strides)
        expected_offset = input_value.storage_offset
        for axis, start, end, step in zip(axes, attrs.starts, attrs.ends, attrs.steps):
            dimension = input_value.shape[axis]
            constant = _constant_poly(_dim_poly(dimension))
            stride = _constant_poly(_stride_poly(input_value.strides[axis]))
            if constant is not None:
                normalized_start, normalized_end, normalized_step = slice(start, end, step).indices(constant)
                expected_shape[axis] = Const(len(range(normalized_start, normalized_end, normalized_step)))
                if stride is None:
                    if normalized_start != 0:
                        raise MeshIrError("E_EXPORT_LAYOUT", "symbolic slice offset cannot be proved", op_id=op.op_id)
                else:
                    expected_offset = _checked(expected_offset + normalized_start * stride, "slice storage offset")
            elif start == 0 and end >= (1 << 63) - 1:
                expected_shape[axis] = CeilDivByConst(dimension, step)
            else:
                raise MeshIrError("E_EXPORT_LAYOUT", "symbolic slice bounds cannot be proved", op_id=op.op_id)
            expected_strides[axis] = ShapeProductStride(input_value.strides[axis].dimensions, _checked(input_value.strides[axis].factor * step, "slice stride")) if type(input_value.strides[axis]) is ShapeProductStride else FixedStride(_checked(input_value.strides[axis].value * step, "slice stride"))
        if not _shape_equal(output.shape, tuple(expected_shape)) or any(not _stride_equal(actual, expected) for actual, expected in zip(output.strides, expected_strides)) or output.storage_offset != expected_offset:
            raise MeshIrError("E_EXPORT_LAYOUT", "slice result metadata is inconsistent", op_id=op.op_id)
        return
    if not _view_unused(attrs, ("shape", "expanded_axes")) or not _shape_equal(attrs.shape, output.shape) or len(output.shape) < len(input_value.shape) or output.storage_offset != input_value.storage_offset:
        raise MeshIrError("E_EXPORT_LAYOUT", "expand view metadata is inconsistent", op_id=op.op_id)
    leading = len(output.shape) - len(input_value.shape)
    expanded = []
    for axis, output_dimension in enumerate(output.shape):
        if axis < leading:
            if _constant_poly(_dim_poly(output_dimension)) == 1:
                continue
            expanded.append(axis)
            expected_stride = {(): 0}
        else:
            source_axis = axis - leading
            input_dimension = input_value.shape[source_axis]
            if _dim_equal(input_dimension, output_dimension):
                expected_stride = _stride_poly(input_value.strides[source_axis])
            elif _constant_poly(_dim_poly(input_dimension)) == 1:
                expanded.append(axis)
                expected_stride = {(): 0}
            else:
                raise MeshIrError("E_EXPORT_LAYOUT", "expand changes a non-singleton dimension", op_id=op.op_id)
        if _stride_poly(output.strides[axis]) != expected_stride:
            raise MeshIrError("E_EXPORT_LAYOUT", "expand stride is inconsistent", op_id=op.op_id)
    if attrs.expanded_axes != tuple(expanded) or output.access is not Access.READ_ONLY:
        raise MeshIrError("E_EXPORT_LAYOUT", "expand axes or access are inconsistent", op_id=op.op_id)


def _verify_copy_data(op, input_value, output) -> None:
    attrs = op.attrs
    if not _view_unused(attrs, ("shape",)) or not _shape_equal(attrs.shape, output.shape) or not _shape_equal(input_value.shape, output.shape):
        raise MeshIrError("E_EXPORT_LAYOUT", "contiguous copy tensor contract is inconsistent", op_id=op.op_id)


def _verify_copy_layout(op, input_value, output) -> None:
    if output.alias_root == input_value.alias_root or output.alias_root != output.value_id or output.storage_offset != 0:
        raise MeshIrError("E_EXPORT_LAYOUT", "contiguous copy must create independent storage", op_id=op.op_id)
    for index, stride in enumerate(output.strides):
        if _stride_poly(stride) != _product_poly(output.shape[index + 1:]):
            raise MeshIrError("E_EXPORT_LAYOUT", "contiguous copy result is not row-major", op_id=op.op_id)


def _verify_movement(op, operands, output) -> None:
    attrs = op.attrs
    data = operands[0]
    axis = _canonical_axis(attrs.axis, len(data.shape), op.op_id)
    if op.opcode.value == "CONCAT":
        expected = list(data.shape)
        concat_dimension = data.shape[axis]
        for item in operands[1:]:
            if len(item.shape) != len(data.shape):
                raise MeshIrError("E_EXPORT_LAYOUT", "concat operand contract is inconsistent", op_id=op.op_id)
            for index, (left, right) in enumerate(zip(data.shape, item.shape)):
                if index != axis and not _dim_equal(left, right):
                    raise MeshIrError("E_EXPORT_LAYOUT", "concat non-axis dimensions differ", op_id=op.op_id)
            concat_dimension = Add(concat_dimension, item.shape[axis])
        expected[axis] = concat_dimension
        _require_shape(output.shape, tuple(expected), op.op_id, "concat result shape is inconsistent")
        return
    index = operands[1]
    if len(index.shape) != 1:
        raise MeshIrError("E_EXPORT_LAYOUT", "gather requires rank-one indices", op_id=op.op_id)
    expected = list(data.shape)
    expected[axis] = index.shape[0]
    _require_shape(output.shape, tuple(expected), op.op_id, "gather result shape is inconsistent")


def _verify_embedding(op, operands, output) -> None:
    attrs = op.attrs
    table, index = operands
    if len(table.shape) != 2:
        raise MeshIrError("E_EXPORT_LAYOUT", "embedding table must have rank two", op_id=op.op_id)
    _require_shape(output.shape, index.shape + (table.shape[1],), op.op_id, "embedding result shape is inconsistent")
    minimum_rows = _dim_bounds(table.shape[0])[0]
    if minimum_rows < 1 or not -minimum_rows <= attrs.padding_idx < minimum_rows:
        raise MeshIrError("E_EXPORT_LAYOUT", "embedding padding index is out of range", op_id=op.op_id)


def _normalized_axes(axes, rank: int, op_id: int):
    if not axes:
        raise MeshIrError("E_EXPORT_LAYOUT", "operation axes cannot be empty", op_id=op_id)
    normalized = tuple(_canonical_axis(axis, rank, op_id) for axis in axes)
    if len(set(normalized)) != len(normalized):
        raise MeshIrError("E_EXPORT_LAYOUT", "operation axes are not distinct", op_id=op_id)
    return normalized


def _verify_reduce(op, input_value, output) -> None:
    attrs = op.attrs
    axes = _normalized_axes(attrs.axes, len(input_value.shape), op.op_id)
    expected = tuple(Const(1) if attrs.keepdim and index in axes else dimension for index, dimension in enumerate(input_value.shape) if attrs.keepdim or index not in axes)
    _require_shape(output.shape, expected, op.op_id, "reduction result shape is inconsistent")


def _verify_norm(op, operands, output) -> None:
    attrs = op.attrs
    input_value = operands[0]
    _require_shape(output.shape, input_value.shape, op.op_id, "normalization result shape is inconsistent")
    axes = _normalized_axes(attrs.axes, len(input_value.shape), op.op_id)
    expected_axes = tuple(range(len(input_value.shape) - len(axes), len(input_value.shape)))
    if axes != expected_axes:
        raise MeshIrError("E_EXPORT_LAYOUT", "normalization axes must be trailing and ordered", op_id=op.op_id)
    affine_shape = tuple(input_value.shape[index] for index in axes)
    for affine in operands[1:]:
        _require_shape(affine.shape, affine_shape, op.op_id, "normalization affine shape is inconsistent")


def _verify_softmax(op, input_value, output) -> None:
    attrs = op.attrs
    _canonical_axis(attrs.axis, len(input_value.shape), op.op_id)
    _require_shape(output.shape, input_value.shape, op.op_id, "softmax result shape is inconsistent")


def _arity(op) -> tuple[int, int]:
    opcode = op.opcode.value
    if opcode == "CONCAT":
        return 1, U32_MAX
    if opcode == "LINEAR_BIAS":
        return 3, 3
    if opcode in ("MATMUL", "BMM", "GATHER_ROWS", "EMBEDDING_LOOKUP"):
        return 2, 2
    if opcode in _BINARY_OPS:
        return (1, 1) if op.attrs.scalar is not None else (2, 2)
    if opcode == "LAYERNORM":
        count = 1 + int(op.attrs.has_weight) + int(op.attrs.has_bias)
        return count, count
    if opcode == "RMSNORM":
        count = 1 + int(op.attrs.has_weight)
        return count, count
    return 1, 1


def _expected_attr_type(opcode):
    from mesh_ir.ir.graph_ir import ElementwiseAttrs, EmbeddingAttrs, MatmulAttrs, MovementAttrs, NormAttrs, ReduceAttrs, SoftmaxAttrs, ViewAttrs

    name = opcode.value
    if name in ("MATMUL", "BMM", "LINEAR_BIAS"):
        return MatmulAttrs
    if name in _VIEW_OPS or name == "CONTIGUOUS_COPY":
        return ViewAttrs
    if name in ("CONCAT", "GATHER_ROWS"):
        return MovementAttrs
    if name in _BINARY_OPS or name in _UNARY_OPS:
        return ElementwiseAttrs
    if name.startswith("REDUCE_"):
        return ReduceAttrs
    if name in ("LAYERNORM", "RMSNORM"):
        return NormAttrs
    if name == "SOFTMAX":
        return SoftmaxAttrs
    if name == "EMBEDDING_LOOKUP":
        return EmbeddingAttrs
    raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "operation opcode is unsupported", opcode=name)


def verify_operation_type_contract(
    opcode: OpCode,
    attrs: OpAttrs,
    operand_dtypes: tuple[DType, ...],
    result_dtype: DType,
    *,
    matrix_result_form: MatrixResultForm = MatrixResultForm.SEMANTIC,
    op_id: int | None = None,
) -> None:
    from mesh_ir.ir.graph_ir import OpCode

    if type(opcode) is not OpCode:
        raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "operation opcode type is invalid", op_id=op_id)
    if op_id is not None and not _u32(op_id):
        raise MeshIrError("E_ABI_BOUNDS", "operation identity is invalid", op_id=op_id)
    if type(matrix_result_form) is not MatrixResultForm:
        raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "matrix result form is invalid", op_id=op_id)
    if type(operand_dtypes) is not tuple or any(type(item) is not DType for item in operand_dtypes) or type(result_dtype) is not DType:
        raise MeshIrError("E_EXPORT_DTYPE", "operation dtype contract is invalid", op_id=op_id)
    verify_operation_attribute_types(attrs)
    if type(attrs) is not _expected_attr_type(opcode):
        raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "operation attribute contract is invalid", op_id=op_id, opcode=opcode.value)
    op = _OperationDataContext(op_id, opcode, attrs)
    minimum, maximum = _arity(op)
    matrix = opcode.value in ("MATMUL", "BMM", "LINEAR_BIAS")
    if matrix_result_form is MatrixResultForm.ACCUMULATION:
        if not matrix:
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "accumulation result form is unsupported for this opcode", op_id=op_id, opcode=opcode.value)
        if opcode.value == "LINEAR_BIAS":
            minimum = maximum = 2
    if not minimum <= len(operand_dtypes) <= maximum:
        raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "operation operand arity is invalid", op_id=op_id, opcode=opcode.value)
    if matrix:
        lhs, rhs = operand_dtypes[:2]
        if lhs is not rhs or attrs.accum_dtype is not lhs.accumulation:
            raise MeshIrError("E_EXPORT_DTYPE", "matrix operand or accumulation dtype is inconsistent", op_id=op_id)
        expected_result = attrs.accum_dtype if matrix_result_form is MatrixResultForm.ACCUMULATION else lhs
        if result_dtype is not expected_result:
            raise MeshIrError("E_EXPORT_DTYPE", "matrix result dtype is inconsistent", op_id=op_id)
        if opcode.value == "LINEAR_BIAS" and matrix_result_form is MatrixResultForm.SEMANTIC and operand_dtypes[2] is not result_dtype:
            raise MeshIrError("E_EXPORT_DTYPE", "matrix bias dtype is inconsistent", op_id=op_id)
        if opcode.value != "LINEAR_BIAS" and attrs.beta != 1.0:
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "beta is only meaningful for biased matrix operations", op_id=op_id)
        return
    if opcode.value in _BINARY_OPS:
        if attrs.scalar_side not in ("none", "lhs", "rhs") or attrs.approximation != "none" or opcode.value in ("MUL", "DIV") and attrs.alpha != 1.0:
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "elementwise attributes do not apply to this opcode", op_id=op_id)
        if len(operand_dtypes) == 2:
            if attrs.scalar is not None or attrs.scalar_side != "none":
                raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "tensor elementwise operation carries scalar attributes", op_id=op_id)
            expected_result = _promote_tensor(operand_dtypes[0], operand_dtypes[1], opcode.value == "DIV")
        else:
            if attrs.scalar is None or attrs.scalar_side not in ("lhs", "rhs"):
                raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "scalar elementwise operation omits scalar semantics", op_id=op_id)
            if opcode.value == "SUB" and type(attrs.scalar) is bool:
                raise MeshIrError("E_EXPORT_DTYPE", "boolean subtraction is unsupported", op_id=op_id)
            expected_result = _promote_scalar(operand_dtypes[0], attrs.scalar, opcode.value == "DIV")
        if result_dtype is not expected_result:
            raise MeshIrError("E_EXPORT_DTYPE", "elementwise result dtype is inconsistent", op_id=op_id)
        if expected_result in (DType.INT8, DType.INT32) and type(attrs.alpha) is float and not attrs.alpha.is_integer():
            raise MeshIrError("E_EXPORT_DTYPE", "integer elementwise alpha is incompatible", op_id=op_id)
        return
    if opcode.value in _UNARY_OPS:
        if attrs.scalar is not None or attrs.scalar_side != "none" or attrs.alpha != 1.0:
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "unary operation carries scalar semantics", op_id=op_id)
        if opcode.value == "GELU":
            if attrs.approximation not in ("none", "tanh"):
                raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "GELU approximation is unsupported", op_id=op_id)
        elif attrs.approximation != "none":
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "unary approximation attribute is invalid", op_id=op_id)
        input_dtype = operand_dtypes[0]
        if opcode.value in ("GELU", "SILU") and input_dtype not in _FLOAT_DTYPES:
            raise MeshIrError("E_EXPORT_DTYPE", "unary operation requires floating-point input", op_id=op_id)
        expected_result = DType.FP32 if opcode.value in ("EXP", "RSQRT") and input_dtype in (DType.INT8, DType.INT32) else input_dtype
        if result_dtype is not expected_result:
            raise MeshIrError("E_EXPORT_DTYPE", "unary result dtype is inconsistent", op_id=op_id)
        return
    if opcode.value in _VIEW_OPS or opcode.value == "CONTIGUOUS_COPY":
        if result_dtype is not operand_dtypes[0]:
            raise MeshIrError("E_EXPORT_DTYPE", "view or copy operation must preserve dtype", op_id=op_id)
        return
    if opcode.value in ("CONCAT", "GATHER_ROWS"):
        if not attrs.bounds_check:
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "unchecked movement operation is unsupported", op_id=op_id)
        if opcode.value == "CONCAT":
            if any(item is not operand_dtypes[0] for item in operand_dtypes[1:]) or result_dtype is not operand_dtypes[0]:
                raise MeshIrError("E_EXPORT_DTYPE", "concat dtype contract is inconsistent", op_id=op_id)
        elif operand_dtypes[1] is not DType.INT32 or result_dtype is not operand_dtypes[0]:
            raise MeshIrError("E_EXPORT_DTYPE", "gather dtype contract is inconsistent", op_id=op_id)
        return
    if opcode.value == "EMBEDDING_LOOKUP":
        if operand_dtypes[1] is not DType.INT32 or result_dtype is not operand_dtypes[0]:
            raise MeshIrError("E_EXPORT_DTYPE", "embedding dtype contract is inconsistent", op_id=op_id)
        if attrs.max_norm is not None or attrs.scale_grad_by_freq or attrs.sparse or attrs.norm_type <= 0:
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "embedding options are unsupported", op_id=op_id)
        return
    if opcode.value.startswith("REDUCE_"):
        input_dtype = operand_dtypes[0]
        if attrs.order != "left_to_right":
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "reduction order is unsupported", op_id=op_id)
        if attrs.output_dtype is not result_dtype or attrs.accum_dtype is not input_dtype.accumulation:
            raise MeshIrError("E_EXPORT_DTYPE", "reduction dtype policy is inconsistent", op_id=op_id)
        if opcode.value == "REDUCE_MAX" and result_dtype is not input_dtype:
            raise MeshIrError("E_EXPORT_DTYPE", "max reduction must preserve dtype", op_id=op_id)
        if opcode.value == "REDUCE_MEAN" and input_dtype not in _FLOAT_DTYPES:
            raise MeshIrError("E_EXPORT_DTYPE", "mean reduction requires floating-point input", op_id=op_id)
        if result_dtype not in (input_dtype, input_dtype.accumulation):
            raise MeshIrError("E_EXPORT_DTYPE", "reduction output dtype is unsupported", op_id=op_id)
        return
    if opcode.value in ("LAYERNORM", "RMSNORM"):
        input_dtype = operand_dtypes[0]
        if input_dtype not in _FLOAT_DTYPES or result_dtype is not input_dtype or any(item is not input_dtype for item in operand_dtypes[1:]):
            raise MeshIrError("E_EXPORT_DTYPE", "normalization dtype is unsupported", op_id=op_id)
        if attrs.epsilon < 0:
            raise MeshIrError("E_EXPORT_DTYPE", "normalization epsilon is unsupported", op_id=op_id)
        if opcode.value == "RMSNORM" and attrs.has_bias:
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "RMSNorm cannot carry bias", op_id=op_id)
        return
    if opcode.value == "SOFTMAX":
        input_dtype = operand_dtypes[0]
        if input_dtype not in _FLOAT_DTYPES or attrs.output_dtype is not result_dtype or result_dtype not in (input_dtype, DType.FP32):
            raise MeshIrError("E_EXPORT_DTYPE", "softmax dtype policy is inconsistent", op_id=op_id)
        return
    raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "operation opcode has no type contract", op_id=op_id, opcode=opcode.value)


def _verify_tensor_contract_fields(value: TensorDataContract, label: str) -> None:
    if type(value.shape) is not tuple:
        raise MeshIrError("E_EXPORT_LAYOUT", "tensor data contract shape must be an immutable tuple", field=label)
    if type(value.dtype) is not DType:
        raise MeshIrError("E_EXPORT_DTYPE", "tensor data contract dtype is invalid", field=label)
    for dimension in value.shape:
        _verify_dimension_type(dimension)


def _verify_tensor_data_contract(value: object, label: str) -> None:
    if type(value) is not TensorDataContract:
        raise MeshIrError("E_EXPORT_LAYOUT", "tensor data contract has invalid type", field=label)
    _verify_tensor_contract_fields(value, label)


def _verify_tensor_layout_contract(value: object, label: str) -> None:
    if type(value) is not TensorLayoutContract:
        raise MeshIrError("E_EXPORT_LAYOUT", "tensor layout contract has invalid type", field=label)
    _verify_tensor_contract_fields(value, label)
    if not _u32(value.value_id) or not _u32(value.alias_root):
        raise MeshIrError("E_ABI_BOUNDS", "tensor layout identity is invalid", field=label)
    if type(value.strides) is not tuple or len(value.strides) != len(value.shape):
        raise MeshIrError("E_EXPORT_LAYOUT", "tensor layout strides do not match rank", field=label)
    for stride in value.strides:
        _verify_stride_type(stride)
    if type(value.storage_offset) is not int or not 0 <= value.storage_offset <= U64_MAX:
        raise MeshIrError("E_EXPORT_LAYOUT", "tensor layout storage offset is invalid", field=label)
    if type(value.access) is not Access:
        raise MeshIrError("E_EXPORT_LAYOUT", "tensor layout access is invalid", field=label)


def verify_operation_layout_contract(
    op_id: int,
    opcode: OpCode,
    attrs: OpAttrs,
    operands: tuple[TensorLayoutContract, ...],
    result: TensorLayoutContract,
) -> None:
    from mesh_ir.ir.graph_ir import OpCode

    if not _u32(op_id):
        raise MeshIrError("E_ABI_BOUNDS", "operation identity is invalid", op_id=op_id)
    if type(opcode) is not OpCode:
        raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "operation opcode type is invalid", op_id=op_id)
    if type(operands) is not tuple:
        raise MeshIrError("E_EXPORT_LAYOUT", "operation layout operands must be an immutable tuple", op_id=op_id)
    for index, operand in enumerate(operands):
        _verify_tensor_layout_contract(operand, f"operand[{index}]")
    _verify_tensor_layout_contract(result, "result")
    verify_operation_type_contract(opcode, attrs, tuple(item.dtype for item in operands), result.dtype, op_id=op_id)
    if opcode.value not in _VIEW_OPS and opcode.value != "CONTIGUOUS_COPY":
        raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "operation has no layout contract", op_id=op_id, opcode=opcode.value)
    op = _OperationDataContext(op_id, opcode, attrs)
    if opcode.value in _VIEW_OPS:
        _verify_view(op, operands[0], result)
    else:
        _verify_copy_data(op, operands[0], result)
        _verify_copy_layout(op, operands[0], result)


def verify_operation_data_contract(
    op_id: int,
    opcode: OpCode,
    attrs: OpAttrs,
    operands: tuple[TensorDataContract, ...],
    result: TensorDataContract,
    *,
    matrix_result_form: MatrixResultForm = MatrixResultForm.SEMANTIC,
) -> None:
    from mesh_ir.ir.graph_ir import OpCode

    if type(operands) is not tuple:
        raise MeshIrError("E_EXPORT_LAYOUT", "operation operands must be an immutable tuple", op_id=op_id)
    for index, operand in enumerate(operands):
        _verify_tensor_data_contract(operand, f"operand[{index}]")
    _verify_tensor_data_contract(result, "result")
    verify_operation_type_contract(
        opcode,
        attrs,
        tuple(item.dtype for item in operands),
        result.dtype,
        matrix_result_form=matrix_result_form,
        op_id=op_id,
    )
    if opcode.value in _VIEW_OPS:
        raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "metadata-only view opcode has no tensor data contract", op_id=op_id, opcode=opcode.value)
    op = _OperationDataContext(op_id, opcode, attrs)
    matrix = opcode.value in ("MATMUL", "BMM", "LINEAR_BIAS")
    if matrix:
        _verify_matmul(op, operands, result, matrix_result_form)
    elif opcode.value in _BINARY_OPS or opcode.value in _UNARY_OPS:
        _verify_elementwise(op, operands, result)
    elif opcode.value == "CONTIGUOUS_COPY":
        _verify_copy_data(op, operands[0], result)
    elif opcode.value in ("CONCAT", "GATHER_ROWS"):
        _verify_movement(op, operands, result)
    elif opcode.value == "EMBEDDING_LOOKUP":
        _verify_embedding(op, operands, result)
    elif opcode.value.startswith("REDUCE_"):
        _verify_reduce(op, operands[0], result)
    elif opcode.value in ("LAYERNORM", "RMSNORM"):
        _verify_norm(op, operands, result)
    elif opcode.value == "SOFTMAX":
        _verify_softmax(op, operands[0], result)
    else:
        raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "operation opcode has no tensor data contract", op_id=op_id, opcode=opcode.value)


def _verify_op(op, values) -> None:
    if len(op.results) != 1:
        raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "operation result or attribute contract is invalid", op_id=op.op_id, opcode=op.opcode.value)
    operands = tuple(values[item] for item in op.operands)
    output = values[op.results[0]]
    opcode = op.opcode.value
    if opcode in _VIEW_OPS or opcode == "CONTIGUOUS_COPY":
        verify_operation_layout_contract(
            op.op_id,
            op.opcode,
            op.attrs,
            tuple(TensorLayoutContract(item.shape, item.dtype, item.value_id, item.strides, item.storage_offset, item.alias_root, item.access) for item in operands),
            TensorLayoutContract(output.shape, output.dtype, output.value_id, output.strides, output.storage_offset, output.alias_root, output.access),
        )
    else:
        verify_operation_data_contract(
            op.op_id,
            op.opcode,
            op.attrs,
            tuple(TensorDataContract(item.shape, item.dtype) for item in operands),
            TensorDataContract(output.shape, output.dtype),
        )
    if opcode not in _VIEW_OPS and output.alias_root != output.value_id:
        raise MeshIrError("E_EXPORT_LAYOUT", "materializing operation result must own storage", op_id=op.op_id)


def _verify_symbol_occurrences(expr, symbols) -> None:
    if type(expr) is Symbol:
        declared = symbols.get(expr.symbol_id)
        if declared is None:
            raise MeshIrError("E_SHAPE_UNBOUND", "tensor metadata references an undeclared symbol", symbol_id=expr.symbol_id)
        if expr != declared:
            raise MeshIrError("E_SHAPE_PROFILE_MISMATCH", "symbol occurrence contradicts its declaration", symbol_id=expr.symbol_id)
    elif type(expr) is Add:
        _verify_symbol_occurrences(expr.lhs, symbols)
        _verify_symbol_occurrences(expr.rhs, symbols)
    elif type(expr) is MulByConst:
        _verify_symbol_occurrences(expr.value, symbols)
    elif type(expr) in (FloorDivByConst, CeilDivByConst):
        _verify_symbol_occurrences(expr.value, symbols)


def _verify_debug_locations(graph, source_nodes) -> None:
    seen = set()
    for source_node_id, location in graph.debug_locations:
        path = location.rsplit(":", 1)[0]
        if source_node_id not in source_nodes or source_node_id in seen or not path or path.startswith("/") or "\\" in path or any(part in ("", ".", "..") for part in path.split("/")):
            raise MeshIrError("E_EXPORT_LAYOUT", "debug location is not a unique repository-relative sidecar", source_node_id=source_node_id)
        seen.add(source_node_id)


def verify_graph(graph) -> None:
    verify_graph_types(graph)
    if graph.schema_major != 1 or graph.schema_minor != 0 or graph.required_features:
        raise MeshIrError("E_ABI_VERSION", "Graph IR schema or required features are unsupported")
    if not graph.entrypoint or not graph.profile_id:
        raise MeshIrError("E_ABI_BOUNDS", "Graph entrypoint and profile identities are required")
    if not _DIGEST.fullmatch(graph.arch_digest) or not _DIGEST.fullmatch(graph.source_semantic_hash):
        raise MeshIrError("E_ARCH_DIGEST", "Graph lineage digest is malformed")
    if not _DIGEST.fullmatch(graph.semantic_sha256) or graph.decomposition_digest and not _DIGEST.fullmatch(graph.decomposition_digest):
        raise MeshIrError("E_ABI_CHECKSUM", "Graph semantic or decomposition digest is malformed")
    symbol_ids = [symbol.symbol_id for symbol in graph.symbols]
    if symbol_ids != list(range(1, len(symbol_ids) + 1)) or len({symbol.name for symbol in graph.symbols}) != len(graph.symbols):
        raise MeshIrError("E_ABI_ORDER", "shape symbols must use dense ids and unique names")
    symbols = {symbol.symbol_id: symbol for symbol in graph.symbols}
    binding_ids = [item[0] for item in graph.profile_bindings]
    if binding_ids != sorted(set(binding_ids)) or graph.symbols and not set(binding_ids) <= set(symbols):
        raise MeshIrError("E_SHAPE_PROFILE_MISMATCH", "profile bindings are not ordered unique symbol identities")
    if graph.symbols:
        for symbol_id, value in graph.profile_bindings:
            symbols[symbol_id].evaluate({symbol_id: value})
    value_ids = [value.value_id for value in graph.values]
    function_ids = [function.function_id for function in graph.functions]
    op_ids = [op.op_id for function in graph.functions for op in function.ops]
    if value_ids != list(range(1, len(value_ids) + 1)) or function_ids != list(range(1, len(function_ids) + 1)) or op_ids != list(range(1, len(op_ids) + 1)):
        raise MeshIrError("E_ABI_ORDER", "Graph namespaces must use dense ids starting at one")
    values = {value.value_id: value for value in graph.values}
    view_results = {
        result
        for function in graph.functions
        for op in function.ops
        if op.opcode.value in _VIEW_OPS
        for result in op.results
    }
    for value in graph.values:
        for dimension in value.shape:
            _verify_symbol_occurrences(dimension, symbols)
        for stride in value.strides:
            if type(stride) is ShapeProductStride:
                for dimension in stride.dimensions:
                    _verify_symbol_occurrences(dimension, symbols)
        if value.content_sha256 is not None and not _DIGEST.fullmatch(value.content_sha256):
            raise MeshIrError("E_ABI_CHECKSUM", "tensor content digest is malformed", value_id=value.value_id)
        _verify_value_layout(value, value.value_id in view_results)
    for value in graph.values:
        root = values.get(value.alias_root)
        if root is None or root.alias_root != root.value_id:
            raise MeshIrError("E_EXPORT_LAYOUT", "tensor alias root is absent or not a root", value_id=value.value_id, alias_root=value.alias_root)
        if value.alias_root != value.value_id:
            if value.dtype is not root.dtype:
                raise MeshIrError("E_EXPORT_LAYOUT", "tensor alias cannot reinterpret its root dtype", value_id=value.value_id, alias_root=value.alias_root)
            value_span = _poly_scale(_storage_poly(value), value.dtype.byte_width)
            root_span = _poly_scale(_storage_poly(root), root.dtype.byte_width)
            root_minimum = _checked(_storage_element_bounds(root)[0] * root.dtype.byte_width, "root storage byte extent")
            value_maximum = _checked(_storage_element_bounds(value)[1] * value.dtype.byte_width, "alias storage byte extent")
            if value.value_id not in view_results and value_span != root_span and value_maximum > root_minimum:
                raise MeshIrError("E_EXPORT_LAYOUT", "tensor alias span exceeds its root storage", value_id=value.value_id, alias_root=value.alias_root)
    if len(graph.functions) != 1 or graph.functions[0].name != graph.entrypoint:
        raise MeshIrError("E_ABI_BOUNDS", "Graph must contain exactly its named entrypoint")
    source_nodes = set()
    for function in graph.functions:
        known_values = set(values)
        if not set(function.inputs + function.outputs) <= known_values or len(set(function.inputs)) != len(function.inputs):
            raise MeshIrError("E_ABI_BOUNDS", "function references unknown or duplicate inputs", function_id=function.function_id)
        if any(values[item].role is not TensorRole.INPUT for item in function.inputs) or set(function.inputs) != {item.value_id for item in graph.values if item.role is TensorRole.INPUT}:
            raise MeshIrError("E_ABI_BOUNDS", "function input role is inconsistent", function_id=function.function_id)
        if function.input_pytree.leaf_count() != len(function.inputs) or function.output_pytree.leaf_count() != len(function.outputs):
            raise MeshIrError("E_ABI_BOUNDS", "pytree leaves do not match function signature", function_id=function.function_id)
        defined = set(function.inputs)
        defined.update(value.value_id for value in graph.values if value.role in (TensorRole.WEIGHT, TensorRole.CONSTANT, TensorRole.STATE, TensorRole.KV_CACHE))
        for op in function.ops:
            if op.source_node_id in source_nodes:
                raise MeshIrError("E_ABI_DUPLICATE", "operation source identity is duplicated", op_id=op.op_id)
            source_nodes.add(op.source_node_id)
            if not set(op.operands) <= defined:
                raise MeshIrError("E_ABI_BOUNDS", "operation references an undefined or non-topological value", op_id=op.op_id)
            if not op.results or any(result in defined or result not in known_values for result in op.results):
                raise MeshIrError("E_ABI_DUPLICATE", "operation result is invalid or multiply defined", op_id=op.op_id)
            _verify_op(op, values)
            defined.update(op.results)
        if not set(function.outputs) <= defined or defined != known_values:
            raise MeshIrError("E_ABI_BOUNDS", "function output or value definition is incomplete", function_id=function.function_id)
    _verify_debug_locations(graph, source_nodes)
    payload = strict_json_loads(graph.canonical_bytes())
    if payload != graph.canonical_dict():
        raise MeshIrError("E_ABI_CORRUPT", "Graph canonical dictionary differs from serialized JSON")
    validate_schema("mesh_graph_v1.schema.json", payload, "Graph IR")
    if semantic_sha256(graph.semantic_dict()) != graph.semantic_sha256:
        raise MeshIrError("E_ABI_CHECKSUM", "Graph semantic hash mismatch")
