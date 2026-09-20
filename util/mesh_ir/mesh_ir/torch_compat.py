from __future__ import annotations

import hashlib
import math
import operator
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sympy
import torch

from mesh_ir.canonical import canonical_json_bytes, semantic_sha256, strict_json_loads
from mesh_ir.compat import DTypeArg, DimArg, DtoArg, DtoInput, DtoNode, DtoOutput, DtoTensor, ExportDto, NoneArg, RootSymbolBinding, ScalarArg, SequenceArg, UnsupportedOpContext, ValueRef, operator_family
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Add, Const, DType, DimExpr, FixedStride, FloorDivByConst, MulByConst, ShapeProductStride, StrideExpr, Symbol
from mesh_ir.ir.graph_ir import PytreeKind, PytreeSpec


PINNED_TORCH_VERSION = "2.8.0+cpu"
ACCEPTED_SOURCE_DIALECTS = ("TRAINING", "ATEN")
FUNCTIONAL_DIALECT = "ATEN"
PINNED_EXPORT_SCHEMA = (8, 8)
PINNED_SOURCE_OPSET = (("aten", 10),)
PRESERVED_OPERATORS = (
    "aten.linear.default", "aten.addmm.default", "aten.layer_norm.default", "aten.native_layer_norm.default", "aten.rms_norm.default", "aten.embedding.default",
)


@dataclass(frozen=True)
class ExportHandle:
    program: object
    archive_sha256: str
    source_semantic_sha256: str
    torch_version: str
    source_opset: tuple[tuple[str, int], ...]
    source_schema: tuple[int, int]
    dialect_before: str


def _decompose_sdpa(query, key, value, attn_mask=None, dropout_p=0.0, is_causal=False, *, scale=None, enable_gqa=False):
    head_dimension = query.shape[-1]
    if scale is None:
        if type(head_dimension) is not int:
            raise MeshIrError("E_SHAPE_UNBOUND", "SDPA default scale requires a static head dimension")
        factor = 1.0 / math.sqrt(head_dimension)
    else:
        factor = float(scale)
    transposed = torch.ops.aten.transpose.int(key, -2, -1)
    scores = torch.ops.aten.matmul.default(query, transposed)
    scores = torch.ops.aten.mul.Tensor(scores, factor)
    if attn_mask is not None:
        scores = torch.ops.aten.add.Tensor(scores, attn_mask)
    probabilities = torch.ops.aten._safe_softmax.default(scores, -1, None)
    return torch.ops.aten.matmul.default(probabilities, value)


PINNED_DECOMPOSITIONS: dict[Any, Any] = {
    torch.ops.aten.scaled_dot_product_attention.default: _decompose_sdpa,
}
DECOMPOSITION_DIGEST = semantic_sha256(
    {
        "torch_version": PINNED_TORCH_VERSION,
        "entries": [
            {
                "schema": str(torch.ops.aten.scaled_dot_product_attention.default._schema),
                "lowering": "transpose-matmul-scale-additive-mask-safe-softmax-matmul-v1",
            }
        ],
        "preserved": list(PRESERVED_OPERATORS),
    }
)


def _archive_content_digest(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            entries = []
            normalized = set()
            for name in sorted(archive.namelist()):
                _, separator, relative = name.partition("/")
                identity = relative if separator else name
                if identity in normalized:
                    raise MeshIrError("E_EXPORT_VERSION", "exported program archive has duplicate normalized paths", path=identity)
                normalized.add(identity)
                entries.append((identity, hashlib.sha256(archive.read(name)).hexdigest()))
    except (OSError, zipfile.BadZipFile, KeyError) as error:
        raise MeshIrError("E_EXPORT_VERSION", "exported program archive is malformed", detail=str(error)) from error
    return semantic_sha256(entries)


def summarize_export_inputs(args: tuple[object, ...], dynamic_shapes=None) -> tuple[tuple[str, object], ...]:
    summary: list[tuple[str, object]] = []
    for path, value in torch.utils._pytree.tree_flatten_with_path(args)[0]:
        if isinstance(value, torch.Tensor):
            name = "input" + torch.utils._pytree.keystr(path)
            summary.append((f"{name}.shape", tuple(int(item) for item in value.shape)))
            summary.append((f"{name}.dtype", str(value.dtype)))
    declarations: dict[str, list[dict[str, object]]] = {}
    if dynamic_shapes is not None:
        for path, declaration in torch.utils._pytree.tree_flatten_with_path(dynamic_shapes)[0]:
            if declaration is None:
                continue
            final = path[-1]
            axis = getattr(final, "key", None)
            if type(axis) is not int or not isinstance(getattr(declaration, "__name__", None), str) or not hasattr(declaration, "min") or not hasattr(declaration, "max"):
                raise MeshIrError("E_CONFIG", "dynamic shape declaration must bind an integer axis to torch.export.Dim")
            input_name = "input" + torch.utils._pytree.keystr(path[:-1])
            declarations.setdefault(input_name, []).append(
                {
                    "axis": axis,
                    "name": declaration.__name__,
                    "minimum": int(declaration.min),
                    "maximum": int(declaration.max),
                }
            )
    summary.append(
        (
            "dynamic_shapes",
            [
                {"input": input_name, "dimensions": sorted(dimensions, key=lambda item: item["axis"])}
                for input_name, dimensions in sorted(declarations.items())
            ] if dynamic_shapes is not None else None,
        )
    )
    return tuple(summary)


def _validated_metadata(metadata: object) -> tuple[str, tuple[tuple[str, int], ...], tuple[int, int]]:
    if not isinstance(metadata, dict):
        raise MeshIrError("E_EXPORT_VERSION", "exported program model metadata must be an object")
    torch_version = metadata.get("torch_version")
    opset = metadata.get("opset_version")
    schema = metadata.get("schema_version")
    if torch_version != PINNED_TORCH_VERSION:
        raise MeshIrError("E_EXPORT_VERSION", "exported program Torch version mismatch", expected=PINNED_TORCH_VERSION, actual=torch_version)
    if not isinstance(opset, dict) or any(not isinstance(key, str) or type(value) is not int for key, value in opset.items()):
        raise MeshIrError("E_EXPORT_VERSION", "exported program opset metadata is malformed")
    source_opset = tuple(sorted(opset.items()))
    if source_opset != PINNED_SOURCE_OPSET:
        raise MeshIrError("E_EXPORT_VERSION", "exported program opset version is unsupported", expected=PINNED_SOURCE_OPSET, actual=source_opset)
    if not isinstance(schema, dict) or type(schema.get("major")) is not int or type(schema.get("minor")) is not int:
        raise MeshIrError("E_EXPORT_VERSION", "exported program schema metadata is malformed")
    source_schema = (schema["major"], schema["minor"])
    if source_schema != PINNED_EXPORT_SCHEMA:
        raise MeshIrError("E_EXPORT_VERSION", "exported program schema version is unsupported", expected=PINNED_EXPORT_SCHEMA, actual=source_schema)
    return torch_version, source_opset, source_schema


def _handle(program, metadata: object, archive_sha256: str, source_semantic_sha256: str, dialect_before: str | None = None) -> ExportHandle:
    torch_version, source_opset, source_schema = _validated_metadata(metadata)
    return ExportHandle(program, archive_sha256, source_semantic_sha256, torch_version, source_opset, source_schema, program.dialect if dialect_before is None else dialect_before)


def _require_version() -> None:
    if torch.__version__ != PINNED_TORCH_VERSION:
        raise MeshIrError("E_EXPORT_VERSION", "compiler Torch version does not match the pinned frontend", expected=PINNED_TORCH_VERSION, actual=torch.__version__)


def _pt2_metadata(path: Path) -> object:
    try:
        with zipfile.ZipFile(path) as archive:
            candidates = sorted(name for name in archive.namelist() if name.endswith("/models/model.json"))
            if len(candidates) != 1:
                raise MeshIrError("E_EXPORT_VERSION", "exported program has no unique model metadata")
            return strict_json_loads(archive.read(candidates[0]))
    except MeshIrError:
        raise
    except (OSError, zipfile.BadZipFile, KeyError) as error:
        raise MeshIrError("E_EXPORT_VERSION", "exported program archive is malformed", detail=str(error)) from error


def load_exported_program(path: str | Path, expected_torch_version: str = PINNED_TORCH_VERSION):
    _require_version()
    source = Path(path)
    metadata = _pt2_metadata(source)
    _validated_metadata(metadata)
    if expected_torch_version != PINNED_TORCH_VERSION:
        raise MeshIrError("E_EXPORT_VERSION", "requested Torch version is unsupported", expected=PINNED_TORCH_VERSION, actual=expected_torch_version)
    try:
        program = torch.export.load(source)
        program.validate()
    except MeshIrError:
        raise
    except (KeyError, RuntimeError, ValueError, torch._dynamo.exc.UserError) as error:
        raise MeshIrError("E_EXPORT_VERSION", "exported program validation failed", detail=str(error)) from error
    if program.dialect not in ACCEPTED_SOURCE_DIALECTS:
        raise MeshIrError("E_EXPORT_VERSION", "exported program dialect is unsupported", dialect=program.dialect)
    return _handle(program, metadata, hashlib.sha256(source.read_bytes()).hexdigest(), _archive_content_digest(source))


def export_program(factory, args: tuple[object, ...], path: str | Path, dynamic_shapes=None):
    _require_version()
    torch.manual_seed(0)
    try:
        module = factory()
    except MeshIrError:
        raise
    except Exception as error:
        raise MeshIrError("E_CONFIG", "module factory failed", detail=str(error), exception_type=type(error).__name__) from error
    if not isinstance(module, torch.nn.Module):
        raise MeshIrError("E_CONFIG", "module factory must return torch.nn.Module")
    leaves, _ = torch.utils._pytree.tree_flatten(args)
    if any(not isinstance(arg, torch.Tensor) for arg in leaves):
        raise MeshIrError("E_EXPORT_NON_TENSOR_IO", "example user inputs must all be tensors")
    module.eval()
    module.requires_grad_(False)
    try:
        with torch.no_grad():
            program = torch.export.export(module, args, dynamic_shapes=dynamic_shapes, strict=True)
        program.validate()
    except (torch._dynamo.exc.Unsupported, torch._dynamo.exc.UserError) as error:
        raise MeshIrError("E_EXPORT_GRAPH_BREAK", "Torch export could not capture a valid single graph", detail=str(error)) from error
    destination = Path(path)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        torch.export.save(program, destination)
    except (OSError, RuntimeError) as error:
        raise MeshIrError("E_EXPORT_VERSION", "cannot save exported program archive", path=str(destination), detail=str(error)) from error
    return _handle(program, _pt2_metadata(destination), hashlib.sha256(destination.read_bytes()).hexdigest(), _archive_content_digest(destination))


def op_histogram(handle: ExportHandle) -> tuple[tuple[str, int], ...]:
    counts: dict[str, int] = {}
    for node in handle.program.graph_module.graph.nodes:
        if node.op == "call_function":
            target = _target_name(node.target)
            counts[target] = counts.get(target, 0) + 1
    return tuple(sorted(counts.items()))


def validate_export_handle(handle: ExportHandle) -> None:
    handle.program.validate()
    if handle.program.dialect not in ACCEPTED_SOURCE_DIALECTS:
        raise MeshIrError("E_EXPORT_VERSION", "exported program dialect is unsupported", dialect=handle.program.dialect)


def current_dialect(handle: ExportHandle) -> str:
    return handle.program.dialect


def execute_program(handle: ExportHandle, args: tuple[object, ...]):
    validate_export_handle(handle)
    with torch.no_grad():
        return handle.program.module()(*args)


def decompose_program(handle: ExportHandle) -> ExportHandle:
    program = handle.program
    before = program.dialect
    if before not in ACCEPTED_SOURCE_DIALECTS:
        raise MeshIrError("E_EXPORT_VERSION", "exported program dialect is unsupported", dialect=before)
    _validate_decomposition_semantics(program)
    decomposed = program.run_decompositions(PINNED_DECOMPOSITIONS)
    decomposed.validate()
    if decomposed is program or decomposed.dialect != FUNCTIONAL_DIALECT:
        raise MeshIrError("E_EXPORT_VERSION", "functionalization did not produce the pinned ATen dialect", dialect=decomposed.dialect)
    return ExportHandle(decomposed, handle.archive_sha256, handle.source_semantic_sha256, handle.torch_version, handle.source_opset, handle.source_schema, handle.dialect_before)


def _target_name(target: object) -> str:
    if target is operator.getitem:
        return "python.operator.getitem"
    return str(target)


def _dtype(value) -> DType:
    mapping = {
        torch.float32: DType.FP32,
        torch.float16: DType.FP16,
        torch.bfloat16: DType.BF16,
        torch.int8: DType.INT8,
        torch.int32: DType.INT32,
    }
    if value not in mapping:
        raise MeshIrError("E_EXPORT_DTYPE", "tensor dtype is unsupported", dtype=str(value))
    return mapping[value]


def _sym_expr(value: object):
    return value.node.expr if isinstance(value, torch.SymInt) else value


def _dimension(value: object, symbols: dict[sympy.Symbol, Symbol]) -> DimExpr:
    expr = _sym_expr(value)
    if type(expr) is int or isinstance(expr, sympy.Integer):
        return Const(int(expr))
    if isinstance(expr, sympy.Symbol):
        if expr not in symbols:
            raise MeshIrError("E_SHAPE_UNBOUND", "shape expression references an unregistered root symbol")
        return symbols[expr]
    if isinstance(expr, sympy.Add):
        parts = tuple(_dimension(item, symbols) for item in expr.args)
        result = parts[0]
        for part in parts[1:]:
            result = Add(result, part)
        return result
    if isinstance(expr, sympy.Mul):
        constants = [item for item in expr.args if isinstance(item, sympy.Integer)]
        symbolic = [item for item in expr.args if not isinstance(item, sympy.Integer)]
        if len(symbolic) == 1:
            factor = math.prod(int(item) for item in constants)
            return MulByConst(_dimension(symbolic[0], symbols), factor)
    if type(expr).__name__ == "FloorDiv" and len(expr.args) == 2 and isinstance(expr.args[1], sympy.Integer) and int(expr.args[1]) > 0:
        return FloorDivByConst(_dimension(expr.args[0], symbols), int(expr.args[1]))
    raise MeshIrError("E_SHAPE_PROFILE_MISMATCH", "shape expression is outside the closed dimension DSL", expression_type=type(expr).__name__)


def _stride(value: object, symbols: dict[sympy.Symbol, Symbol]) -> StrideExpr:
    expr = _sym_expr(value)
    if type(expr) is int or isinstance(expr, sympy.Integer):
        if int(expr) < 0:
            raise MeshIrError("E_EXPORT_LAYOUT", "negative tensor stride is unsupported", stride=int(expr))
        return FixedStride(int(expr))
    coefficient, factors = expr.as_coeff_mul()
    dimensions = []
    for factor in factors:
        if isinstance(factor, sympy.Pow) and isinstance(factor.base, sympy.Symbol) and factor.exp.is_Integer and int(factor.exp) > 0:
            dimensions.extend(_dimension(factor.base, symbols) for _ in range(int(factor.exp)))
        elif isinstance(factor, sympy.Symbol):
            dimensions.append(_dimension(factor, symbols))
        else:
            raise MeshIrError("E_EXPORT_LAYOUT", "symbolic stride is not derivable from shape symbols", expression_type=type(factor).__name__)
    if int(coefficient) < 0:
        raise MeshIrError("E_EXPORT_LAYOUT", "negative tensor stride is unsupported")
    return ShapeProductStride(tuple(dimensions), int(coefficient))


def _symbol_table(program) -> dict[sympy.Symbol, Symbol]:
    occurrences = []
    signature = program.graph_signature
    input_specs = {spec.arg.name: spec for spec in signature.input_specs if hasattr(spec.arg, "name")}
    for node in program.graph_module.graph.nodes:
        if node.op != "placeholder" or node.name not in input_specs or input_specs[node.name].kind.name != "USER_INPUT":
            continue
        value = node.meta.get("val")
        if not isinstance(value, torch.Tensor):
            continue
        for axis, dimension in enumerate(value.shape):
            expr = _sym_expr(dimension)
            for symbol in sorted(expr.free_symbols, key=str) if hasattr(expr, "free_symbols") else ():
                if symbol not in [item[0] for item in occurrences]:
                    occurrences.append((symbol, node.name, axis))
    table = {}
    for index, (symbol, input_name, axis) in enumerate(occurrences, 1):
        if symbol not in program.range_constraints:
            raise MeshIrError("E_SHAPE_UNBOUND", "root symbol has no finite range", input=input_name, axis=axis)
        value_range = program.range_constraints[symbol]
        lower, upper = value_range.lower, value_range.upper
        if not isinstance(lower, sympy.Integer) or not isinstance(upper, sympy.Integer):
            raise MeshIrError("E_SHAPE_UNBOUND", "root symbol range must be finite", input=input_name, axis=axis)
        table[symbol] = Symbol(index, f"s{index}", int(lower), int(upper), 1)
    return table


def _root_symbol_bindings(program, symbols) -> tuple[RootSymbolBinding, ...]:
    bindings = []
    signature = program.graph_signature
    input_specs = {spec.arg.name: spec for spec in signature.input_specs if hasattr(spec.arg, "name")}
    for node in program.graph_module.graph.nodes:
        if node.op != "placeholder" or node.name not in input_specs or input_specs[node.name].kind.name != "USER_INPUT":
            continue
        value = node.meta.get("val")
        if not isinstance(value, torch.Tensor):
            continue
        for axis, dimension in enumerate(value.shape):
            expr = _sym_expr(dimension)
            if isinstance(expr, sympy.Symbol) and expr in symbols:
                bindings.append(RootSymbolBinding(input_specs[node.name].arg.name, axis, symbols[expr].symbol_id))
    return tuple(bindings)


def _tensor_meta(value_id: int, stable_name: str, value, symbols, digest: str | None = None) -> DtoTensor:
    if not isinstance(value, torch.Tensor) or value.is_sparse or value.is_quantized or value.is_complex():
        raise MeshIrError("E_EXPORT_LAYOUT", "exported value is not a supported dense tensor", value=stable_name)
    shape = tuple(_dimension(item, symbols) for item in value.shape)
    strides = tuple(_stride(item, symbols) for item in value.stride())
    concrete_shape = tuple(int(item) for item in value.shape if type(item) is int)
    concrete_strides = tuple(int(item) for item in value.stride() if type(item) is int)
    if len(concrete_shape) == len(value.shape) and len(concrete_strides) == len(value.shape):
        span = 1
        for stride, dimension in sorted(zip(concrete_strides, concrete_shape)):
            if dimension > 1 and stride != 0:
                if stride < span:
                    raise MeshIrError("E_EXPORT_LAYOUT", "tensor layout has overlapping positive strides", value=stable_name)
                span += (dimension - 1) * stride
    offset = value.storage_offset()
    if type(offset) is not int or offset < 0:
        raise MeshIrError("E_EXPORT_LAYOUT", "tensor storage offset is invalid", value=stable_name)
    return DtoTensor(value_id, stable_name, _dtype(value.dtype), shape, strides, offset, digest)


def _content_digest(tensor) -> str:
    raw = tensor.detach().cpu().contiguous().view(torch.uint8).reshape(-1).tolist()
    return hashlib.sha256(bytes(raw)).hexdigest()


def _argument(value: object, value_ids: dict[object, tuple[int, ...]], symbols) -> DtoArg:
    if isinstance(value, torch.fx.Node) and value in value_ids:
        ids = value_ids[value]
        if len(ids) != 1:
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "tuple-valued operator consumption requires an explicit lowering", target=_target_name(value.target), source_node_id="context:tuple_value")
        return ValueRef(ids[0])
    if value is None:
        return NoneArg()
    if isinstance(value, torch.SymInt):
        return DimArg(_dimension(value, symbols))
    if isinstance(value, torch.dtype):
        return DTypeArg(_dtype(value))
    if type(value) in (bool, int, float, str):
        if isinstance(value, float) and not math.isfinite(value):
            raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "operator scalar attribute must be finite", source_node_id="context:attribute")
        return ScalarArg(value)
    if isinstance(value, (tuple, list)):
        return SequenceArg(tuple(_argument(item, value_ids, symbols) for item in value))
    if value is torch.contiguous_format:
        return ScalarArg("contiguous_format")
    raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "operator argument type is unsupported", argument_type=f"{type(value).__module__}.{type(value).__qualname__}", source_node_id="context:attribute")


def _pytree(spec) -> PytreeSpec:
    if spec is None or spec.is_leaf():
        return PytreeSpec.leaf()
    children = tuple(_pytree(child) for child in spec.children_specs)
    if spec.type is tuple:
        return PytreeSpec(PytreeKind.TUPLE, children)
    if spec.type is list:
        return PytreeSpec(PytreeKind.LIST, children)
    if spec.type is dict and all(isinstance(key, str) for key in spec.context):
        return PytreeSpec(PytreeKind.DICT, children, tuple(spec.context))
    raise MeshIrError("E_EXPORT_NON_TENSOR_IO", "input/output pytree contains an unsupported structural node", pytree_type=str(spec.type))


def _source_location(node) -> str | None:
    stack = node.meta.get("stack_trace")
    if not stack:
        return None
    repository = Path.cwd().resolve()
    for match in re.finditer(r'File "([^"]+)", line (\d+)', stack):
        try:
            relative = Path(match.group(1)).resolve().relative_to(repository)
        except ValueError:
            continue
        return f"{relative.as_posix()}:{match.group(2)}"
    return None


def _fx_unsupported_context(node, source_node_id: str, guidance: str) -> UnsupportedOpContext:
    inputs = []

    def collect(value: object) -> None:
        if isinstance(value, torch.fx.Node):
            metadata = value.meta.get("val")
            if isinstance(metadata, torch.Tensor):
                inputs.append(metadata)
        elif isinstance(value, (tuple, list)):
            for item in value:
                collect(item)
        elif isinstance(value, dict):
            for item in value.values():
                collect(item)

    collect(node.args)
    collect(node.kwargs)
    outputs = []
    collect_output = outputs.append

    def collect_result(value: object) -> None:
        if isinstance(value, torch.Tensor):
            collect_output(value)
        elif isinstance(value, (tuple, list)):
            for item in value:
                collect_result(item)
        elif isinstance(value, dict):
            for item in value.values():
                collect_result(item)

    collect_result(node.meta.get("val"))

    def dtype_name(value) -> str:
        try:
            return _dtype(value.dtype).name
        except MeshIrError:
            return str(value.dtype).removeprefix("torch.").upper()

    target = _target_name(node.target)
    return UnsupportedOpContext(
        target=_target_name(node.target),
        schema=str(getattr(node.target, "_schema", "")),
        source_node_id=source_node_id,
        input_shapes=tuple(tuple(str(item) for item in value.shape) for value in inputs),
        output_shapes=tuple(tuple(str(item) for item in value.shape) for value in outputs),
        input_dtypes=tuple(dtype_name(value) for value in inputs),
        output_dtypes=tuple(dtype_name(value) for value in outputs),
        source_location=_source_location(node),
        nearest_family=operator_family(target),
        guidance=guidance,
    )


def _validate_decomposition_semantics(program) -> None:
    position = 0
    for node in program.graph_module.graph.nodes:
        if node.op != "call_function":
            continue
        position += 1
        if _target_name(node.target) != "aten.scaled_dot_product_attention.default":
            continue
        source_node_id = f"node:{position}"
        arguments = node.args
        keywords = dict(node.kwargs)
        mask = arguments[3] if len(arguments) > 3 else None
        dropout = arguments[4] if len(arguments) > 4 else 0.0
        causal = arguments[5] if len(arguments) > 5 else False
        enable_gqa = keywords.get("enable_gqa", False)
        query = arguments[0].meta.get("val") if isinstance(arguments[0], torch.fx.Node) else None
        if not isinstance(query, torch.Tensor) or query.dtype != torch.float32:
            raise _fx_unsupported_context(node, source_node_id, "export FP32 attention or add an explicit canonical cast contract").error("SDPA requires FP32 inputs in the cast-free canonical surface")
        if dropout != 0.0:
            raise _fx_unsupported_context(node, source_node_id, "set dropout_p=0.0 for inference").error("SDPA dropout is unsupported for deterministic inference", dropout_p=dropout)
        if causal:
            raise _fx_unsupported_context(node, source_node_id, "provide an explicit additive mask with safe-row semantics").error("SDPA causal mask construction is outside the canonical operator surface")
        if enable_gqa:
            raise _fx_unsupported_context(node, source_node_id, "expand key/value explicitly with supported view operations").error("SDPA grouped-query expansion is outside the canonical operator surface")
        if isinstance(mask, torch.fx.Node):
            mask_value = mask.meta.get("val")
            if isinstance(mask_value, torch.Tensor) and mask_value.dtype == torch.bool:
                raise _fx_unsupported_context(node, source_node_id, "use an additive FP32 attention mask").error("boolean SDPA masks are outside the canonical operator surface")


def extract_dto(handle: ExportHandle) -> ExportDto:
    program = handle.program
    if program.dialect != FUNCTIONAL_DIALECT:
        raise MeshIrError("E_EXPORT_VERSION", "DTO extraction requires functional ATen dialect", dialect=program.dialect)
    symbols = _symbol_table(program)
    signature = program.graph_signature
    input_specs = {spec.arg.name: spec for spec in signature.input_specs if hasattr(spec.arg, "name")}
    output_specs = {spec.arg.name: spec for spec in signature.output_specs if hasattr(spec.arg, "name")}
    values = []
    inputs = []
    nodes = []
    value_ids: dict[object, tuple[int, ...]] = {}
    next_value = 1
    input_position = 0
    for node in program.graph_module.graph.nodes:
        if node.op == "placeholder":
            spec = input_specs.get(node.name)
            if spec is None or not isinstance(node.meta.get("val"), torch.Tensor):
                raise MeshIrError("E_EXPORT_NON_TENSOR_IO", "graph signature input is not a tensor", input=node.name)
            kind = spec.kind.name
            if kind not in ("PARAMETER", "BUFFER", "CONSTANT_TENSOR", "USER_INPUT"):
                raise MeshIrError("E_EXPORT_NON_TENSOR_IO", "graph signature input kind is unsupported", kind=kind)
            target = spec.target
            stable_name = target if target is not None else spec.arg.name
            payload = None
            if kind == "PARAMETER" or kind == "BUFFER" and spec.persistent:
                payload = program.state_dict[target]
            elif kind == "BUFFER" or kind == "CONSTANT_TENSOR":
                payload = program.constants[target]
            digest = _content_digest(payload) if payload is not None else None
            values.append(_tensor_meta(next_value, stable_name, node.meta["val"], symbols, digest))
            value_ids[node] = (next_value,)
            inputs.append(DtoInput(input_position, next_value, kind, spec.arg.name, target, spec.persistent))
            input_position += 1
            next_value += 1
        elif node.op == "call_function":
            target = _target_name(node.target)
            schema = str(getattr(node.target, "_schema", ""))
            meta = node.meta.get("val")
            if target == "python.operator.getitem" and isinstance(node.args[0], torch.fx.Node) and _target_name(node.args[0].target) == "aten.native_layer_norm.default":
                index = node.args[1]
                if type(index) is not int or index != 0:
                    raise _fx_unsupported_context(node.args[0], f"node:{len(nodes)}", "consume only projection 0 or use canonical layer_norm").error("native layer norm auxiliary output is unsupported", projection_index=index)
                value_ids[node] = value_ids[node.args[0]]
                continue
            if target in ("aten.sym_size.int", "aten._assert_scalar.default") or node.target is operator.eq:
                continue
            if target == "aten.native_layer_norm.default" and isinstance(meta, (tuple, list)) and len(meta) == 3:
                projections = []
                for user in node.users:
                    if user.op != "call_function" or user.target is not operator.getitem or len(user.args) != 2 or user.args[0] is not node or type(user.args[1]) is not int:
                        raise _fx_unsupported_context(node, f"node:{len(nodes) + 1}", "consume only tensor projection 0 or use canonical layer_norm").error("native layer norm result has an unsupported consumer")
                    projections.append(user.args[1])
                unsupported = sorted(index for index in set(projections) if index != 0)
                if unsupported:
                    raise _fx_unsupported_context(node, f"node:{len(nodes) + 1}", "consume only projection 0 or use canonical layer_norm").error("native layer norm auxiliary output is unsupported", projection_index=unsupported[0])
                if 0 not in projections:
                    raise _fx_unsupported_context(node, f"node:{len(nodes) + 1}", "consume projection 0 or use canonical layer_norm").error("native layer norm primary output is not consumed")
                result_ids = (next_value,)
                values.append(_tensor_meta(next_value, f"v{next_value}", meta[0], symbols))
                next_value += 1
            elif isinstance(meta, (tuple, list)):
                raise _fx_unsupported_context(node, f"node:{len(nodes) + 1}", "add an exact tuple-result lowering and projection contract").error("tuple-valued operator requires an explicit result lowering")
            elif isinstance(meta, torch.Tensor):
                result_ids = (next_value,)
                values.append(_tensor_meta(next_value, f"v{next_value}", meta, symbols))
                next_value += 1
            else:
                raise MeshIrError("E_EXPORT_NON_TENSOR_IO", "operator result is not a tensor", target=target)
            value_ids[node] = result_ids
            source_node_id = f"node:{len(nodes) + 1}"
            try:
                args = tuple(_argument(item, value_ids, symbols) for item in node.args)
                kwargs = tuple((name, _argument(value, value_ids, symbols)) for name, value in sorted(node.kwargs.items()))
            except MeshIrError as error:
                if error.code != "E_EXPORT_UNSUPPORTED_OP":
                    raise
                raise _fx_unsupported_context(node, source_node_id, "use a supported tensor, dimension, dtype, scalar, or sequence attribute").error(error.message, **error.context) from error
            nodes.append(DtoNode(source_node_id, target, schema, args, kwargs, result_ids, _source_location(node)))
        elif node.op == "output":
            continue
        else:
            raise MeshIrError("E_EXPORT_GRAPH_BREAK", "export graph contains an unsupported node kind", node_kind=node.op)
    outputs = []
    for position, spec in enumerate(signature.output_specs):
        kind = spec.kind.name
        if kind in ("BUFFER_MUTATION", "USER_INPUT_MUTATION"):
            raise MeshIrError("E_EXPORT_STATE_MUTATION", "exported graph mutates an input or buffer", kind=kind)
        if kind != "USER_OUTPUT" or not hasattr(spec.arg, "name") or spec.arg.name not in output_specs:
            raise MeshIrError("E_EXPORT_NON_TENSOR_IO", "graph signature output kind is unsupported", kind=kind)
        matching = next((ids for node, ids in value_ids.items() if node.name == spec.arg.name), None)
        if matching is None or len(matching) != 1:
            raise MeshIrError("E_EXPORT_NON_TENSOR_IO", "graph signature output does not identify one tensor", output=spec.arg.name)
        outputs.append(DtoOutput(position, matching[0], kind))
    return ExportDto(
        handle.torch_version, handle.dialect_before, program.dialect,
        handle.source_opset, handle.source_schema, DECOMPOSITION_DIGEST,
        tuple(inputs), tuple(outputs), tuple(values), tuple(nodes), tuple(symbols[key] for key in sorted(symbols, key=lambda item: symbols[item].symbol_id)), _root_symbol_bindings(program, symbols),
        _pytree(program.call_spec.in_spec), _pytree(program.call_spec.out_spec),
    )
