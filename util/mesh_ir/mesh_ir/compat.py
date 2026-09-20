from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TypeAlias

from mesh_ir.canonical import semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import DType, DimExpr, StrideExpr, dimension_data, stride_data
from mesh_ir.ir.graph_ir import PytreeSpec


def operator_family(target: str) -> str:
    parts = target.split(".")
    if len(parts) < 2 or parts[0] != "aten":
        return "unknown"
    operation = parts[1]
    families = {
        "matmul": frozenset(("matmul", "bmm", "mm", "linear", "addmm")),
        "normalization": frozenset(("layer_norm", "native_layer_norm", "rms_norm")),
        "attention": frozenset(("softmax", "_safe_softmax", "scaled_dot_product_attention")),
        "reduction": frozenset(("sum", "mean", "amax", "max")),
        "view": frozenset(("reshape", "view", "transpose", "permute", "slice", "expand", "as_strided", "clone")),
        "data_movement": frozenset(("cat", "index_select", "gather")),
        "embedding": frozenset(("embedding",)),
        "elementwise": frozenset(("add", "sub", "mul", "div", "relu", "gelu", "silu", "exp", "rsqrt", "sin")),
    }
    return next((family for family, operations in families.items() if operation in operations), "unknown")


@dataclass(frozen=True)
class UnsupportedOpContext:
    target: str
    schema: str
    source_node_id: str
    input_shapes: tuple[tuple[str, ...], ...]
    output_shapes: tuple[tuple[str, ...], ...]
    input_dtypes: tuple[str, ...]
    output_dtypes: tuple[str, ...]
    source_location: str | None
    nearest_family: str
    guidance: str

    def error(self, message: str, **details: object) -> MeshIrError:
        context = {
            **details,
            "target": self.target,
            "schema": self.schema,
            "source_node_id": self.source_node_id,
            "input_shapes": [list(shape) for shape in self.input_shapes],
            "output_shapes": [list(shape) for shape in self.output_shapes],
            "input_dtypes": list(self.input_dtypes),
            "output_dtypes": list(self.output_dtypes),
            "source_location": self.source_location,
            "nearest_family": self.nearest_family,
            "guidance": self.guidance,
        }
        return MeshIrError("E_EXPORT_UNSUPPORTED_OP", message, **context)


@dataclass(frozen=True)
class ValueRef:
    value_id: int


@dataclass(frozen=True)
class ScalarArg:
    value: int | float | bool | str


@dataclass(frozen=True)
class DimArg:
    value: DimExpr


@dataclass(frozen=True)
class DTypeArg:
    value: DType


@dataclass(frozen=True)
class NoneArg:
    pass


@dataclass(frozen=True)
class SequenceArg:
    items: tuple["DtoArg", ...]


DtoArg: TypeAlias = ValueRef | ScalarArg | DimArg | DTypeArg | NoneArg | SequenceArg


@dataclass(frozen=True)
class DtoTensor:
    value_id: int
    stable_name: str
    dtype: DType
    shape: tuple[DimExpr, ...]
    strides: tuple[StrideExpr, ...]
    storage_offset: int
    content_sha256: str | None = None


@dataclass(frozen=True)
class DtoInput:
    position: int
    value_id: int
    kind: str
    argument_name: str
    target: str | None
    persistent: bool | None


@dataclass(frozen=True)
class DtoOutput:
    position: int
    value_id: int
    kind: str


@dataclass(frozen=True)
class RootSymbolBinding:
    input_name: str
    axis: int
    symbol_id: int


@dataclass(frozen=True)
class DtoNode:
    source_node_id: str
    target: str
    schema: str
    args: tuple[DtoArg, ...]
    kwargs: tuple[tuple[str, DtoArg], ...]
    result_ids: tuple[int, ...]
    location: str | None


def _arg_data(arg: DtoArg) -> dict[str, object]:
    if isinstance(arg, ValueRef):
        return {"kind": "value", "value_id": arg.value_id}
    if isinstance(arg, ScalarArg):
        return {"kind": "scalar", "value": arg.value}
    if isinstance(arg, DimArg):
        return {"kind": "dimension", "value": dimension_data(arg.value)}
    if isinstance(arg, DTypeArg):
        return {"kind": "dtype", "value": int(arg.value)}
    if isinstance(arg, NoneArg):
        return {"kind": "none"}
    return {"kind": "sequence", "items": [_arg_data(item) for item in arg.items]}


@dataclass(frozen=True)
class ExportDto:
    torch_version: str
    dialect_before: str
    dialect_after: str
    source_opset: tuple[tuple[str, int], ...]
    source_schema: tuple[int, int]
    decomposition_digest: str
    inputs: tuple[DtoInput, ...]
    outputs: tuple[DtoOutput, ...]
    values: tuple[DtoTensor, ...]
    nodes: tuple[DtoNode, ...]
    symbols: tuple
    root_symbol_bindings: tuple[RootSymbolBinding, ...]
    input_pytree: PytreeSpec
    output_pytree: PytreeSpec

    def semantic_data(self) -> dict[str, object]:
        return {
            "torch_version": self.torch_version,
            "dialect_before": self.dialect_before,
            "dialect_after": self.dialect_after,
            "source_opset": [{"namespace": key, "version": value} for key, value in self.source_opset],
            "source_schema": {"major": self.source_schema[0], "minor": self.source_schema[1]},
            "decomposition_digest": self.decomposition_digest,
            "inputs": [asdict(item) for item in self.inputs],
            "outputs": [asdict(item) for item in self.outputs],
            "values": [
                {
                    "value_id": value.value_id, "stable_name": value.stable_name, "dtype": int(value.dtype),
                    "shape": [dimension_data(item) for item in value.shape], "strides": [stride_data(item) for item in value.strides],
                    "storage_offset": value.storage_offset,
                    **({"content_sha256": value.content_sha256} if value.content_sha256 is not None else {}),
                }
                for value in self.values
            ],
            "nodes": [
                {
                    "source_node_id": node.source_node_id, "target": node.target, "schema": node.schema,
                    "args": [_arg_data(item) for item in node.args],
                    "kwargs": [{"name": name, "value": _arg_data(value)} for name, value in node.kwargs],
                    "result_ids": list(node.result_ids),
                }
                for node in self.nodes
            ],
            "symbols": [dimension_data(symbol) for symbol in self.symbols],
            "root_symbol_bindings": [asdict(item) for item in self.root_symbol_bindings],
            "input_pytree": asdict(self.input_pytree),
            "output_pytree": asdict(self.output_pytree),
        }

    def semantic_hash(self) -> str:
        return semantic_sha256(self.semantic_data())
