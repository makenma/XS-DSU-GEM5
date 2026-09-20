from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, TypeAlias

from mesh_ir.canonical import U64_MAX, canonical_json_bytes, semantic_sha256, to_canonical
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated.semantic_enums import OpCode
from mesh_ir.ir.common import (
    Access,
    Const,
    DType,
    DimExpr,
    FixedStride,
    ShapeProductStride,
    StrideExpr,
    Symbol,
    TensorRole,
    dimension_data,
    stride_data,
)


METADATA_VIEW_OPCODES = frozenset(
    (
        OpCode.RESHAPE_VIEW,
        OpCode.TRANSPOSE_VIEW,
        OpCode.PERMUTE_VIEW,
        OpCode.SLICE_VIEW,
        OpCode.EXPAND_VIEW,
    )
)


@dataclass(frozen=True)
class MatmulAttrs:
    batch_axes: tuple[int, ...] = ()
    lhs_contract_axis: int = -1
    rhs_contract_axis: int = -2
    lhs_transpose: bool = False
    rhs_transpose: bool = False
    alpha: float = 1.0
    beta: float = 1.0
    accum_dtype: DType = DType.FP32


@dataclass(frozen=True)
class ViewAttrs:
    shape: tuple[DimExpr, ...] = ()
    permutation: tuple[int, ...] = ()
    axes: tuple[int, ...] = ()
    starts: tuple[int, ...] = ()
    ends: tuple[int, ...] = ()
    steps: tuple[int, ...] = ()
    expanded_axes: tuple[int, ...] = ()


@dataclass(frozen=True)
class MovementAttrs:
    axis: int = 0
    bounds_check: bool = True


@dataclass(frozen=True)
class ElementwiseAttrs:
    scalar: int | float | bool | None = None
    scalar_side: str = "none"
    alpha: float = 1.0
    approximation: str = "none"


@dataclass(frozen=True)
class ReduceAttrs:
    axes: tuple[int, ...]
    keepdim: bool
    output_dtype: DType
    accum_dtype: DType
    order: str = "left_to_right"


@dataclass(frozen=True)
class NormAttrs:
    axes: tuple[int, ...]
    epsilon: float
    has_weight: bool
    has_bias: bool


@dataclass(frozen=True)
class SoftmaxAttrs:
    axis: int
    output_dtype: DType
    zero_fully_masked_rows: bool = False


@dataclass(frozen=True)
class EmbeddingAttrs:
    padding_idx: int
    scale_grad_by_freq: bool
    sparse: bool
    max_norm: float | None = None
    norm_type: float = 2.0


OpAttrs: TypeAlias = MatmulAttrs | ViewAttrs | MovementAttrs | ElementwiseAttrs | ReduceAttrs | NormAttrs | SoftmaxAttrs | EmbeddingAttrs


class PytreeKind(str, Enum):
    LEAF = "leaf"
    TUPLE = "tuple"
    LIST = "list"
    DICT = "dict"


@dataclass(frozen=True)
class PytreeSpec:
    kind: PytreeKind
    children: tuple["PytreeSpec", ...] = ()
    keys: tuple[str, ...] = ()

    @staticmethod
    def leaf() -> "PytreeSpec":
        return PytreeSpec(PytreeKind.LEAF)

    @staticmethod
    def tuple(children: tuple["PytreeSpec", ...]) -> "PytreeSpec":
        return PytreeSpec(PytreeKind.TUPLE, children)

    def leaf_count(self) -> int:
        if type(self.kind) is not PytreeKind or type(self.children) is not tuple or type(self.keys) is not tuple:
            raise MeshIrError("E_ABI_BOUNDS", "pytree record has invalid field types")
        if self.kind == PytreeKind.LEAF:
            if self.children or self.keys:
                raise MeshIrError("E_ABI_BOUNDS", "pytree leaf cannot have children or keys")
            return 1
        if self.kind == PytreeKind.DICT:
            if len(self.keys) != len(self.children) or len(set(self.keys)) != len(self.keys):
                raise MeshIrError("E_ABI_BOUNDS", "dictionary pytree keys do not match children")
        elif self.keys:
            raise MeshIrError("E_ABI_BOUNDS", "non-dictionary pytree cannot have keys")
        return sum(child.leaf_count() for child in self.children)


@dataclass(frozen=True)
class GraphValue:
    value_id: int
    name: str
    role: TensorRole
    dtype: DType
    shape: tuple[DimExpr, ...]
    strides: tuple[StrideExpr | int, ...]
    storage_offset: int
    alias_root: int
    access: Access = Access.READ_ONLY
    logical_extent_bytes: int | None = None
    storage_extent_bytes: int | None = None
    content_sha256: str | None = None

    def __post_init__(self):
        if type(self.shape) is not tuple or type(self.strides) is not tuple:
            raise MeshIrError("E_EXPORT_LAYOUT", "shape and strides must be immutable tuples", value_id=self.value_id)
        if any(type(value) is int and value < 0 for value in self.strides):
            raise MeshIrError("E_EXPORT_LAYOUT", "negative tensor stride is unsupported", value_id=self.value_id)
        normalized = tuple(FixedStride(value) if type(value) is int else value for value in self.strides)
        object.__setattr__(self, "strides", normalized)
        if type(self.storage_offset) is not int or type(self.alias_root) is not int:
            raise MeshIrError("E_EXPORT_LAYOUT", "storage metadata is invalid", value_id=self.value_id)
        if len(self.shape) != len(normalized):
            raise MeshIrError("E_EXPORT_LAYOUT", "shape and stride rank differ", value_id=self.value_id)
        if self.storage_offset < 0 or self.alias_root < 1:
            raise MeshIrError("E_EXPORT_LAYOUT", "storage metadata is invalid", value_id=self.value_id)
        if any(isinstance(stride, FixedStride) and stride.value == 0 for stride in normalized) and self.access != Access.READ_ONLY:
            raise MeshIrError("E_EXPORT_LAYOUT", "zero stride is only legal for read-only broadcast views", value_id=self.value_id)


@dataclass(frozen=True)
class GraphOp:
    op_id: int
    opcode: OpCode
    operands: tuple[int, ...]
    results: tuple[int, ...]
    attrs: OpAttrs
    source_node_id: str


@dataclass(frozen=True)
class GraphFunction:
    function_id: int
    name: str
    inputs: tuple[int, ...]
    ops: tuple[GraphOp, ...]
    outputs: tuple[int, ...]
    input_pytree: PytreeSpec
    output_pytree: PytreeSpec


def operation_attrs_data(attrs: OpAttrs) -> dict[str, object]:
    result: dict[str, object] = {"kind": type(attrs).__name__}
    for field in dataclasses.fields(attrs):
        value = getattr(attrs, field.name)
        if value is None and (isinstance(attrs, ElementwiseAttrs) and field.name == "scalar" or isinstance(attrs, EmbeddingAttrs) and field.name == "max_norm"):
            continue
        if isinstance(attrs, ViewAttrs) and field.name == "shape":
            value = [dimension_data(item) for item in value]
        elif isinstance(value, DType):
            value = int(value)
        elif isinstance(value, tuple):
            value = list(value)
        result[field.name] = value
    return result


def _pytree_data(spec: PytreeSpec) -> dict[str, object]:
    return {"kind": spec.kind.value, "children": [_pytree_data(child) for child in spec.children], "keys": list(spec.keys)}


@dataclass(frozen=True)
class GraphModule:
    schema_major: int
    schema_minor: int
    required_features: tuple[str, ...]
    arch_digest: str
    source_semantic_hash: str
    semantic_sha256: str
    entrypoint: str
    profile_id: str
    values: tuple[GraphValue, ...]
    functions: tuple[GraphFunction, ...]
    symbols: tuple[Symbol, ...]
    profile_bindings: tuple[tuple[int, int], ...] = ()
    decomposition_digest: str = ""
    debug_locations: tuple[tuple[str, str], ...] = ()

    @classmethod
    def create(
        cls,
        arch_digest: str,
        source_semantic_hash: str,
        entrypoint: str,
        profile_id: str,
        values: tuple[GraphValue, ...],
        functions: tuple[GraphFunction, ...],
        symbols: tuple[Symbol, ...] = (),
        profile_bindings: tuple[tuple[int, int], ...] = (),
        decomposition_digest: str = "",
        debug_locations: tuple[tuple[str, str], ...] = (),
    ) -> "GraphModule":
        provisional = cls(1, 0, (), arch_digest, source_semantic_hash, "", entrypoint, profile_id, values, functions, symbols, profile_bindings, decomposition_digest, debug_locations)
        from mesh_ir.ir.graph_verify import verify_graph_types

        verify_graph_types(provisional, allow_empty_semantic_hash=True)
        graph = dataclasses.replace(provisional, semantic_sha256=semantic_sha256(provisional.semantic_dict()))
        graph.verify()
        return graph

    def semantic_dict(self) -> dict[str, object]:
        return {
            "schema": {"major": self.schema_major, "minor": self.schema_minor},
            "required_features": list(self.required_features),
            "arch_digest": self.arch_digest,
            "source_semantic_hash": self.source_semantic_hash,
            "entrypoint": self.entrypoint,
            "profile_id": self.profile_id,
            "values": [
                {
                    "value_id": value.value_id, "name": value.name, "role": int(value.role), "dtype": int(value.dtype),
                    "shape": [dimension_data(item) for item in value.shape], "strides": [stride_data(item) for item in value.strides],
                    "storage_offset": value.storage_offset, "alias_root": value.alias_root, "access": int(value.access),
                    **({"logical_extent_bytes": value.logical_extent_bytes} if value.logical_extent_bytes is not None else {}),
                    **({"storage_extent_bytes": value.storage_extent_bytes} if value.storage_extent_bytes is not None else {}),
                    **({"content_sha256": value.content_sha256} if value.content_sha256 is not None else {}),
                }
                for value in self.values
            ],
            "functions": [
                {
                    "function_id": function.function_id, "name": function.name, "inputs": list(function.inputs),
                    "ops": [{"op_id": op.op_id, "opcode": op.opcode.value, "operands": list(op.operands), "results": list(op.results), "attrs": operation_attrs_data(op.attrs), "source_node_id": op.source_node_id} for op in function.ops],
                    "outputs": list(function.outputs), "input_pytree": _pytree_data(function.input_pytree), "output_pytree": _pytree_data(function.output_pytree),
                }
                for function in self.functions
            ],
            "symbols": [dimension_data(symbol) for symbol in self.symbols],
            "profile_bindings": [{"symbol_id": symbol_id, "value": value} for symbol_id, value in self.profile_bindings],
            "decomposition_digest": self.decomposition_digest,
        }

    def canonical_dict(self) -> dict[str, object]:
        return to_canonical({**self.semantic_dict(), "semantic_sha256": self.semantic_sha256, "debug_locations": [{"source_node_id": node, "location": location} for node, location in self.debug_locations]})

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.canonical_dict())

    def verify(self) -> None:
        from mesh_ir.ir.graph_verify import verify_graph

        verify_graph(self)


def specialize_graph(graph: GraphModule, profile_id: str, bindings: Mapping[int, int]) -> GraphModule:
    graph.verify()
    if type(profile_id) is not str or not profile_id or not isinstance(bindings, Mapping) or any(type(symbol_id) is not int or symbol_id < 1 or type(value) is not int for symbol_id, value in bindings.items()):
        raise MeshIrError("E_SHAPE_PROFILE_MISMATCH", "shape profile identity or bindings are invalid")
    required = {symbol.symbol_id for symbol in graph.symbols}
    supplied = set(bindings)
    if required - supplied:
        raise MeshIrError("E_SHAPE_UNBOUND", "profile omits shape symbols", missing=sorted(required - supplied))
    if supplied - required:
        raise MeshIrError("E_SHAPE_PROFILE_MISMATCH", "profile has unknown shape symbols", extra=sorted(supplied - required))
    for symbol in graph.symbols:
        symbol.evaluate(bindings)
    values = []
    for value in graph.values:
        shape = tuple(Const(dimension.evaluate(bindings)) for dimension in value.shape)
        strides = tuple(FixedStride(stride.evaluate(bindings)) for stride in value.strides)
        elements = 1
        for dimension in shape:
            elements *= dimension.value
            if elements > U64_MAX:
                raise MeshIrError("E_ABI_OVERFLOW", "specialized logical extent overflows u64", value_id=value.value_id)
        logical_bytes = elements * value.dtype.byte_width
        if logical_bytes > U64_MAX:
            raise MeshIrError("E_ABI_OVERFLOW", "specialized logical byte extent overflows u64", value_id=value.value_id)
        storage_elements = 0 if any(dimension.value == 0 for dimension in shape) else value.storage_offset + 1 + sum((dimension.value - 1) * stride.value for dimension, stride in zip(shape, strides))
        if storage_elements > U64_MAX or storage_elements * value.dtype.byte_width > U64_MAX:
            raise MeshIrError("E_ABI_OVERFLOW", "specialized storage extent overflows u64", value_id=value.value_id)
        values.append(dataclasses.replace(value, shape=shape, strides=strides, logical_extent_bytes=logical_bytes, storage_extent_bytes=storage_elements * value.dtype.byte_width))
    functions = tuple(
        dataclasses.replace(function, ops=tuple(
            dataclasses.replace(op, attrs=dataclasses.replace(op.attrs, shape=tuple(Const(item.evaluate(bindings)) for item in op.attrs.shape)) if isinstance(op.attrs, ViewAttrs) else op.attrs)
            for op in function.ops
        ))
        for function in graph.functions
    )
    return GraphModule.create(graph.arch_digest, graph.source_semantic_hash, graph.entrypoint, profile_id, tuple(values), functions, (), tuple(sorted(bindings.items())), graph.decomposition_digest, graph.debug_locations)
