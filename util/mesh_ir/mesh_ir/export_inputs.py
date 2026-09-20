from __future__ import annotations

import hashlib
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import torch

from mesh_ir.canonical import strict_json_loads
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import DType


_TORCH_DTYPES = {
    DType.FP32: torch.float32,
    DType.FP16: torch.float16,
    DType.BF16: torch.bfloat16,
    DType.INT8: torch.int8,
    DType.INT32: torch.int32,
}
_INTEGER_RANGES = {
    DType.INT8: (-(1 << 7), (1 << 7) - 1),
    DType.INT32: (-(1 << 31), (1 << 31) - 1),
}
@dataclass(frozen=True)
class PreparedExportInputs:
    args: tuple[object, ...]
    dynamic_shapes: tuple[dict[int, object], ...] | None
    inputs_sha256: str
    dynamic_shapes_sha256: str | None

    @property
    def document_identities(self) -> tuple[tuple[str, str], ...]:
        rows = [("inputs", self.inputs_sha256)]
        if self.dynamic_shapes_sha256 is not None:
            rows.append(("dynamic_shapes", self.dynamic_shapes_sha256))
        return tuple(rows)


def _read_document(path: str | Path, label: str) -> tuple[bytes, object]:
    source = Path(path)
    try:
        payload = source.read_bytes()
    except OSError as error:
        raise MeshIrError("E_CONFIG", f"cannot read {label}", path=str(source), detail=str(error)) from error
    return payload, strict_json_loads(payload)


def _mapping(value: object, fields: frozenset[str], label: str) -> dict[str, object]:
    if type(value) is not dict:
        raise MeshIrError("E_CONFIG", f"{label} must be an object")
    unknown = set(value) - fields
    missing = fields - set(value)
    if unknown or missing:
        raise MeshIrError("E_CONFIG", f"{label} fields are invalid", missing=sorted(missing), unknown=sorted(unknown))
    return value


def _shape(value: object, argument: int) -> tuple[int, ...]:
    if type(value) is not list:
        raise MeshIrError("E_CONFIG", "tensor shape must be an array", argument=argument)
    shape = []
    elements = 1
    for axis, extent in enumerate(value):
        if type(extent) is not int or not 0 <= extent <= sys.maxsize:
            raise MeshIrError("E_CONFIG", "tensor shape extent is invalid", argument=argument, axis=axis, extent=extent)
        if extent and elements > sys.maxsize // extent:
            raise MeshIrError("E_CONFIG", "tensor shape element count overflows", argument=argument)
        elements *= extent
        shape.append(extent)
    return tuple(shape)


def _tensor(value: object, argument: int):
    row = _mapping(value, frozenset(("dtype", "shape", "data")), f"tensor argument {argument}")
    dtype_name = row["dtype"]
    try:
        dtype = DType[dtype_name] if type(dtype_name) is str else None
    except KeyError:
        dtype = None
    if dtype not in _TORCH_DTYPES:
        raise MeshIrError("E_EXPORT_DTYPE", "input tensor dtype is unsupported", argument=argument, dtype=dtype_name)
    shape = _shape(row["shape"], argument)
    data = row["data"]
    if type(data) is not list:
        raise MeshIrError("E_CONFIG", "tensor data must be a flat array", argument=argument)
    elements = math.prod(shape)
    if len(data) != elements:
        raise MeshIrError("E_CONFIG", "tensor data length differs from shape", argument=argument, expected=elements, actual=len(data))
    if dtype in _INTEGER_RANGES:
        minimum, maximum = _INTEGER_RANGES[dtype]
        if any(type(item) is not int or not minimum <= item <= maximum for item in data):
            raise MeshIrError("E_EXPORT_DTYPE", "integer tensor data is not exactly representable", argument=argument, dtype=dtype.name)
    else:
        if any(
            type(item) not in (int, float)
            or type(item) is float and not math.isfinite(item)
            for item in data
        ):
            raise MeshIrError("E_EXPORT_DTYPE", "floating tensor data must be finite numeric values", argument=argument, dtype=dtype.name)
    try:
        tensor = torch.tensor(data, dtype=_TORCH_DTYPES[dtype], device="cpu").reshape(shape)
    except (OverflowError, RuntimeError, TypeError, ValueError) as error:
        raise MeshIrError("E_EXPORT_DTYPE", "tensor data conversion failed", argument=argument, dtype=dtype.name, detail=str(error)) from error
    if tensor.is_floating_point() and not bool(torch.isfinite(tensor).all()):
        raise MeshIrError("E_EXPORT_DTYPE", "floating tensor data overflows its dtype", argument=argument, dtype=dtype.name)
    return tensor


def _inputs(document: object) -> tuple[object, ...]:
    root = _mapping(document, frozenset(("schema_version", "args")), "export inputs")
    if root["schema_version"] != "mesh-export-inputs-v1":
        raise MeshIrError("E_CONFIG", "export input schema version is unsupported", schema_version=root["schema_version"])
    args = root["args"]
    if type(args) is not list:
        raise MeshIrError("E_EXPORT_NON_TENSOR_IO", "export arguments must be an ordered tensor array")
    return tuple(_tensor(item, index) for index, item in enumerate(args))


def _dynamic_shapes(document: object, args: tuple[object, ...]) -> tuple[dict[int, object], ...]:
    root = _mapping(document, frozenset(("schema_version", "symbols", "args")), "dynamic shapes")
    if root["schema_version"] != "mesh-export-shapes-v1":
        raise MeshIrError("E_CONFIG", "dynamic shape schema version is unsupported", schema_version=root["schema_version"])
    declarations = root["symbols"]
    rows = root["args"]
    if type(declarations) is not dict or type(rows) is not list or len(rows) != len(args):
        raise MeshIrError("E_CONFIG", "dynamic shape arguments do not match export inputs")
    symbols = {}
    bounds = {}
    for name, value in declarations.items():
        if type(name) is not str or not name:
            raise MeshIrError("E_CONFIG", "dynamic shape symbol name is invalid")
        bound = _mapping(value, frozenset(("min", "max")), f"dynamic shape symbol {name}")
        minimum = bound["min"]
        maximum = bound["max"]
        if type(minimum) is not int or type(maximum) is not int or not 0 <= minimum < maximum <= sys.maxsize:
            raise MeshIrError("E_CONFIG", "dynamic shape symbol bounds are invalid", symbol=name)
        bounds[name] = (minimum, maximum)
        try:
            symbols[name] = torch.export.Dim(name, min=minimum, max=maximum)
        except (TypeError, ValueError) as error:
            raise MeshIrError("E_CONFIG", "dynamic shape symbol bounds are unsupported", symbol=name, detail=str(error)) from error
    used = set()
    result = []
    for argument, (row, tensor) in enumerate(zip(rows, args)):
        if type(row) is not dict:
            raise MeshIrError("E_CONFIG", "dynamic shape argument must be an axis object", argument=argument)
        axes = {}
        tensor_axes = {str(axis): axis for axis in range(tensor.dim())}
        for raw_axis, name in row.items():
            if type(raw_axis) is not str or raw_axis not in tensor_axes or type(name) is not str or name not in symbols:
                raise MeshIrError("E_CONFIG", "dynamic shape axis binding is invalid", argument=argument, axis=raw_axis, symbol=name)
            axis = tensor_axes[raw_axis]
            minimum, maximum = bounds[name]
            extent = tensor.shape[axis]
            if not minimum <= extent <= maximum:
                raise MeshIrError("E_SHAPE_PROFILE_MISMATCH", "example extent violates dynamic shape bounds", argument=argument, axis=axis, symbol=name, extent=extent)
            axes[axis] = symbols[name]
            used.add(name)
        result.append(axes)
    if used != set(symbols):
        raise MeshIrError("E_CONFIG", "dynamic shape declarations contain unused symbols", unused=sorted(set(symbols) - used))
    return tuple(result)


def load_export_input_documents(
    inputs_path: str | Path,
    dynamic_shapes_path: str | Path | None = None,
) -> PreparedExportInputs:
    input_bytes, input_document = _read_document(inputs_path, "export inputs")
    args = _inputs(input_document)
    dynamic_shapes = None
    dynamic_sha = None
    if dynamic_shapes_path is not None:
        dynamic_bytes, dynamic_document = _read_document(dynamic_shapes_path, "dynamic shapes")
        dynamic_shapes = _dynamic_shapes(dynamic_document, args)
        dynamic_sha = hashlib.sha256(dynamic_bytes).hexdigest()
    return PreparedExportInputs(
        args,
        dynamic_shapes,
        hashlib.sha256(input_bytes).hexdigest(),
        dynamic_sha,
    )


__all__ = ["PreparedExportInputs", "load_export_input_documents"]
