from __future__ import annotations

from dataclasses import replace

from mesh_ir.compat import DTypeArg, DimArg, DtoArg, DtoNode, ExportDto, NoneArg, ScalarArg, SequenceArg, UnsupportedOpContext, ValueRef, operator_family
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Access, Const, DType, DimExpr, FixedStride, TensorRole
from mesh_ir.ir.graph_ir import (
    ElementwiseAttrs,
    EmbeddingAttrs,
    GraphFunction,
    GraphModule,
    GraphOp,
    GraphValue,
    MatmulAttrs,
    METADATA_VIEW_OPCODES,
    MovementAttrs,
    NormAttrs,
    OpCode,
    ReduceAttrs,
    SoftmaxAttrs,
    ViewAttrs,
)


TARGET_SCHEMAS = {
    "aten.matmul.default": "aten::matmul(Tensor self, Tensor other) -> Tensor",
    "aten.bmm.default": "aten::bmm(Tensor self, Tensor mat2) -> Tensor",
    "aten.linear.default": "aten::linear(Tensor input, Tensor weight, Tensor? bias=None) -> Tensor",
    "aten.addmm.default": "aten::addmm(Tensor self, Tensor mat1, Tensor mat2, *, Scalar beta=1, Scalar alpha=1) -> Tensor",
    "aten.native_layer_norm.default": "aten::native_layer_norm(Tensor input, SymInt[] normalized_shape, Tensor? weight, Tensor? bias, float eps) -> (Tensor, Tensor, Tensor)",
    "aten.view.default": "aten::view(Tensor(a) self, SymInt[] size) -> Tensor(a)",
    "aten.reshape.default": "aten::reshape(Tensor(a) self, SymInt[] shape) -> Tensor(a)",
    "aten.transpose.int": "aten::transpose.int(Tensor(a) self, int dim0, int dim1) -> Tensor(a)",
    "aten.permute.default": "aten::permute(Tensor(a) self, int[] dims) -> Tensor(a)",
    "aten.slice.Tensor": "aten::slice.Tensor(Tensor(a) self, int dim=0, SymInt? start=None, SymInt? end=None, SymInt step=1) -> Tensor(a)",
    "aten.expand.default": "aten::expand(Tensor(a) self, SymInt[] size, *, bool implicit=False) -> Tensor(a)",
    "aten.clone.default": "aten::clone(Tensor self, *, MemoryFormat? memory_format=None) -> Tensor",
    "aten.cat.default": "aten::cat(Tensor[] tensors, int dim=0) -> Tensor",
    "aten.index_select.default": "aten::index_select(Tensor self, int dim, Tensor index) -> Tensor",
    "aten.embedding.default": "aten::embedding(Tensor weight, Tensor indices, SymInt padding_idx=-1, bool scale_grad_by_freq=False, bool sparse=False) -> Tensor",
    "aten.add.Tensor": "aten::add.Tensor(Tensor self, Tensor other, *, Scalar alpha=1) -> Tensor",
    "aten.sub.Tensor": "aten::sub.Tensor(Tensor self, Tensor other, *, Scalar alpha=1) -> Tensor",
    "aten.mul.Tensor": "aten::mul.Tensor(Tensor self, Tensor other) -> Tensor",
    "aten.div.Tensor": "aten::div.Tensor(Tensor self, Tensor other) -> Tensor",
    "aten.relu.default": "aten::relu(Tensor self) -> Tensor",
    "aten.gelu.default": 'aten::gelu(Tensor self, *, str approximate="none") -> Tensor',
    "aten.silu.default": "aten::silu(Tensor self) -> Tensor",
    "aten.exp.default": "aten::exp(Tensor self) -> Tensor",
    "aten.rsqrt.default": "aten::rsqrt(Tensor self) -> Tensor",
    "aten.sum.dim_IntList": "aten::sum.dim_IntList(Tensor self, int[1]? dim, bool keepdim=False, *, ScalarType? dtype=None) -> Tensor",
    "aten.sum.default": "aten::sum(Tensor self, *, ScalarType? dtype=None) -> Tensor",
    "aten.amax.default": "aten::amax(Tensor self, int[1] dim=[], bool keepdim=False) -> Tensor",
    "aten.mean.dim": "aten::mean.dim(Tensor self, int[1]? dim, bool keepdim=False, *, ScalarType? dtype=None) -> Tensor",
    "aten.mean.default": "aten::mean(Tensor self, *, ScalarType? dtype=None) -> Tensor",
    "aten.layer_norm.default": "aten::layer_norm(Tensor input, SymInt[] normalized_shape, Tensor? weight=None, Tensor? bias=None, float eps=1.0000000000000001e-05, bool cudnn_enable=True) -> Tensor",
    "aten.rms_norm.default": "aten::rms_norm(Tensor input, SymInt[] normalized_shape, Tensor? weight=None, float? eps=None) -> Tensor",
    "aten.softmax.int": "aten::softmax.int(Tensor self, int dim, ScalarType? dtype=None) -> Tensor",
    "aten._safe_softmax.default": "aten::_safe_softmax(Tensor self, int dim, ScalarType? dtype=None) -> Tensor",
}


def _scalar(arg: DtoArg, default: object = None) -> object:
    if isinstance(arg, ScalarArg):
        return arg.value
    if isinstance(arg, NoneArg):
        return default
    raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "expected a scalar operator attribute", source_node_id="context:attribute")


def _sequence(arg: DtoArg) -> tuple[DtoArg, ...]:
    if not isinstance(arg, SequenceArg):
        raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "expected a sequence operator attribute", source_node_id="context:attribute")
    return arg.items


def _dimensions(arg: DtoArg) -> tuple[DimExpr, ...]:
    result = []
    for item in _sequence(arg):
        if isinstance(item, DimArg):
            result.append(item.value)
        elif isinstance(item, ScalarArg) and type(item.value) is int:
            result.append(Const(item.value))
        else:
            raise MeshIrError("E_SHAPE_PROFILE_MISMATCH", "view shape is not an integer dimension")
    return tuple(result)


def _integers(arg: DtoArg) -> tuple[int, ...]:
    values = tuple(_scalar(item) for item in _sequence(arg))
    if any(type(value) is not int for value in values):
        raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "operator axis list must contain integers")
    return values


def _value(arg: DtoArg) -> int:
    if not isinstance(arg, ValueRef):
        raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "expected a tensor operand", source_node_id="context:operand")
    return arg.value_id


def _optional_value(arg: DtoArg) -> int | None:
    return None if isinstance(arg, NoneArg) else _value(arg)


def _arg(node: DtoNode, index: int, default: DtoArg | None = None) -> DtoArg:
    if index < len(node.args):
        return node.args[index]
    if default is None:
        raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "operator omits a required argument", target=node.target, source_node_id=node.source_node_id)
    return default


def _kw(node: DtoNode, name: str, default: DtoArg) -> DtoArg:
    return dict(node.kwargs).get(name, default)


def _unsupported_context(node: DtoNode, tensors: dict[int, GraphValue]) -> UnsupportedOpContext:
    input_ids = []

    def collect(arg: DtoArg) -> None:
        if isinstance(arg, ValueRef):
            input_ids.append(arg.value_id)
        elif isinstance(arg, SequenceArg):
            for item in arg.items:
                collect(item)

    for argument in node.args:
        collect(argument)
    for _, argument in node.kwargs:
        collect(argument)
    inputs = [tensors[value_id] for value_id in input_ids]
    outputs = [tensors[value_id] for value_id in node.result_ids]
    return UnsupportedOpContext(
        node.target,
        node.schema,
        node.source_node_id,
        tuple(tuple(str(item) for item in value.shape) for value in inputs),
        tuple(tuple(str(item) for item in value.shape) for value in outputs),
        tuple(value.dtype.name for value in inputs),
        tuple(value.dtype.name for value in outputs),
        node.location,
        operator_family(node.target),
        "use a registered functional ATen operator or add an exact verified lowering",
    )


def _lower(node: DtoNode, tensors: dict[int, GraphValue]) -> tuple[OpCode, tuple[int, ...], object]:
    if node.target.startswith("aten.as_strided"):
        raise MeshIrError("E_EXPORT_LAYOUT", "as_strided has no verified V1 view proof", target=node.target, schema=node.schema, source_node_id=node.source_node_id)
    if node.target not in TARGET_SCHEMAS or TARGET_SCHEMAS[node.target] != node.schema:
        raise _unsupported_context(node, tensors).error("operator is outside the pinned canonical registry")
    if len(node.result_ids) != 1:
        raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "operator result arity is unsupported", target=node.target, source_node_id=node.source_node_id)
    target = node.target
    if target in ("aten.matmul.default", "aten.bmm.default"):
        lhs, rhs = _value(_arg(node, 0)), _value(_arg(node, 1))
        if target == "aten.bmm.default":
            batch_axes = (0,)
        else:
            lhs_batches = max(len(tensors[lhs].shape) - 2, 0)
            rhs_batches = max(len(tensors[rhs].shape) - 2, 0)
            output_batches = max(lhs_batches, rhs_batches)
            common_batches = min(lhs_batches, rhs_batches)
            batch_axes = tuple(range(output_batches - common_batches, output_batches))
        return (OpCode.BMM if target == "aten.bmm.default" else OpCode.MATMUL), (lhs, rhs), MatmulAttrs(batch_axes=batch_axes, accum_dtype=tensors[lhs].dtype.accumulation)
    if target == "aten.linear.default":
        operands = [_value(_arg(node, 0)), _value(_arg(node, 1))]
        bias = _optional_value(_arg(node, 2, NoneArg()))
        if bias is not None:
            operands.append(bias)
        return (OpCode.LINEAR_BIAS if bias is not None else OpCode.MATMUL), tuple(operands), MatmulAttrs(rhs_transpose=True, accum_dtype=tensors[operands[0]].dtype.accumulation)
    if target == "aten.addmm.default":
        bias, lhs, rhs = (_value(_arg(node, index)) for index in range(3))
        alpha = float(_scalar(_kw(node, "alpha", ScalarArg(1.0))))
        beta = float(_scalar(_kw(node, "beta", ScalarArg(1.0))))
        return OpCode.LINEAR_BIAS, (lhs, rhs, bias), MatmulAttrs(alpha=alpha, beta=beta, accum_dtype=tensors[lhs].dtype.accumulation)
    if target in ("aten.view.default", "aten.reshape.default"):
        source_shape = _sequence(_arg(node, 1))
        if sum(isinstance(item, ScalarArg) and item.value == -1 for item in source_shape) > 1:
            raise MeshIrError("E_SHAPE_PROFILE_MISMATCH", "reshape has multiple inferred dimensions", source_node_id=node.source_node_id)
        return OpCode.RESHAPE_VIEW, (_value(_arg(node, 0)),), ViewAttrs(shape=tensors[node.result_ids[0]].shape)
    if target == "aten.transpose.int":
        operand = _value(_arg(node, 0)); rank = len(tensors[operand].shape)
        first, second = int(_scalar(_arg(node, 1))), int(_scalar(_arg(node, 2)))
        first %= rank; second %= rank
        permutation = list(range(rank)); permutation[first], permutation[second] = permutation[second], permutation[first]
        return OpCode.TRANSPOSE_VIEW, (operand,), ViewAttrs(permutation=tuple(permutation))
    if target == "aten.permute.default":
        operand = _value(_arg(node, 0)); rank = len(tensors[operand].shape)
        return OpCode.PERMUTE_VIEW, (operand,), ViewAttrs(permutation=tuple(axis % rank for axis in _integers(_arg(node, 1))))
    if target == "aten.slice.Tensor":
        operand = _value(_arg(node, 0)); axis = int(_scalar(_arg(node, 1, ScalarArg(0)))) % len(tensors[operand].shape)
        start = int(_scalar(_arg(node, 2, NoneArg()), 0)); end = int(_scalar(_arg(node, 3, NoneArg()), 2**63 - 1)); step = int(_scalar(_arg(node, 4, ScalarArg(1))))
        return OpCode.SLICE_VIEW, (operand,), ViewAttrs(axes=(axis,), starts=(start,), ends=(end,), steps=(step,))
    if target == "aten.expand.default":
        operand = _value(_arg(node, 0)); result = tensors[node.result_ids[0]]
        expanded = tuple(index for index, stride in enumerate(result.strides) if isinstance(stride, FixedStride) and stride.value == 0)
        return OpCode.EXPAND_VIEW, (operand,), ViewAttrs(shape=result.shape, expanded_axes=expanded)
    if target == "aten.clone.default":
        memory_format = _scalar(_kw(node, "memory_format", NoneArg()), None)
        if memory_format not in (None, "contiguous_format"):
            raise MeshIrError("E_EXPORT_LAYOUT", "clone memory format is unsupported", source_node_id=node.source_node_id)
        return OpCode.CONTIGUOUS_COPY, (_value(_arg(node, 0)),), ViewAttrs(shape=tensors[node.result_ids[0]].shape)
    if target == "aten.cat.default":
        operands = tuple(_value(item) for item in _sequence(_arg(node, 0)))
        axis = int(_scalar(_arg(node, 1, ScalarArg(0)))) % len(tensors[operands[0]].shape)
        return OpCode.CONCAT, operands, MovementAttrs(axis)
    if target == "aten.index_select.default":
        index = _value(_arg(node, 2))
        if tensors[index].dtype != DType.INT32:
            raise MeshIrError("E_EXPORT_DTYPE", "index_select requires int32 indices", value_id=index)
        source = _value(_arg(node, 0)); axis = int(_scalar(_arg(node, 1))) % len(tensors[source].shape)
        return OpCode.GATHER_ROWS, (source, index), MovementAttrs(axis)
    if target == "aten.embedding.default":
        index = _value(_arg(node, 1))
        if tensors[index].dtype != DType.INT32:
            raise MeshIrError("E_EXPORT_DTYPE", "embedding requires int32 indices", value_id=index)
        padding = int(_scalar(_arg(node, 2, ScalarArg(-1)))); scale = bool(_scalar(_arg(node, 3, ScalarArg(False)))); sparse = bool(_scalar(_arg(node, 4, ScalarArg(False))))
        return OpCode.EMBEDDING_LOOKUP, (_value(_arg(node, 0)), index), EmbeddingAttrs(padding, scale, sparse)
    elementwise = {
        "aten.add.Tensor": OpCode.ADD, "aten.sub.Tensor": OpCode.SUB, "aten.mul.Tensor": OpCode.MUL, "aten.div.Tensor": OpCode.DIV,
        "aten.relu.default": OpCode.RELU, "aten.gelu.default": OpCode.GELU, "aten.silu.default": OpCode.SILU,
        "aten.exp.default": OpCode.EXP, "aten.rsqrt.default": OpCode.RSQRT,
    }
    if target in elementwise:
        operands = [_value(_arg(node, 0))]
        scalar = None
        if len(node.args) > 1:
            if isinstance(node.args[1], ValueRef):
                operands.append(node.args[1].value_id)
            else:
                scalar = _scalar(node.args[1])
        alpha = float(_scalar(_kw(node, "alpha", ScalarArg(1.0))))
        approximation = str(_scalar(_kw(node, "approximate", ScalarArg("none"))))
        return elementwise[target], tuple(operands), ElementwiseAttrs(scalar, "rhs" if scalar is not None else "none", alpha, approximation)
    reductions = {"aten.sum.dim_IntList": OpCode.REDUCE_SUM, "aten.sum.default": OpCode.REDUCE_SUM, "aten.amax.default": OpCode.REDUCE_MAX, "aten.mean.dim": OpCode.REDUCE_MEAN, "aten.mean.default": OpCode.REDUCE_MEAN}
    if target in reductions:
        operand = _value(_arg(node, 0)); rank = len(tensors[operand].shape)
        if target in ("aten.sum.default", "aten.mean.default"):
            axes = tuple(range(rank)); keepdim = False
        else:
            raw_axes = _integers(_arg(node, 1, SequenceArg(())))
            axes = tuple(range(rank)) if not raw_axes else tuple(axis % rank for axis in raw_axes)
            if len(set(axes)) != len(axes):
                raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "reduction axes are duplicated", source_node_id=node.source_node_id)
            keepdim = bool(_scalar(_arg(node, 2, ScalarArg(False))))
        dtype_arg = _kw(node, "dtype", NoneArg())
        if not isinstance(dtype_arg, NoneArg) and (not isinstance(dtype_arg, DTypeArg) or dtype_arg.value != tensors[node.result_ids[0]].dtype):
            raise MeshIrError("E_EXPORT_DTYPE", "reduction dtype does not match its result", source_node_id=node.source_node_id)
        return reductions[target], (operand,), ReduceAttrs(axes, keepdim, tensors[node.result_ids[0]].dtype, tensors[operand].dtype.accumulation)
    if target in ("aten.layer_norm.default", "aten.native_layer_norm.default", "aten.rms_norm.default"):
        operand = _value(_arg(node, 0)); normalized = _dimensions(_arg(node, 1)); rank = len(tensors[operand].shape); axes = tuple(range(rank - len(normalized), rank))
        weight = _optional_value(_arg(node, 2, NoneArg()))
        bias = _optional_value(_arg(node, 3, NoneArg())) if target in ("aten.layer_norm.default", "aten.native_layer_norm.default") else None
        eps_index = 4 if target in ("aten.layer_norm.default", "aten.native_layer_norm.default") else 3
        epsilon_default = 1e-5 if target in ("aten.layer_norm.default", "aten.native_layer_norm.default") else tensors[operand].dtype.machine_epsilon
        epsilon = float(_scalar(_arg(node, eps_index, NoneArg()), epsilon_default))
        operands = (operand,) + (() if weight is None else (weight,)) + (() if bias is None else (bias,))
        return (OpCode.RMSNORM if target == "aten.rms_norm.default" else OpCode.LAYERNORM), operands, NormAttrs(axes, epsilon, weight is not None, bias is not None)
    operand = _value(_arg(node, 0)); axis = int(_scalar(_arg(node, 1))) % len(tensors[operand].shape)
    return OpCode.SOFTMAX, (operand,), SoftmaxAttrs(axis, tensors[node.result_ids[0]].dtype, target == "aten._safe_softmax.default")


def canonicalize_graph(dto: ExportDto, arch_digest: str, entrypoint: str, profile_id: str) -> GraphModule:
    input_kinds = {item.value_id: item.kind for item in dto.inputs}
    outputs = {item.value_id for item in dto.outputs}
    roles = {"PARAMETER": TensorRole.WEIGHT, "BUFFER": TensorRole.STATE, "CONSTANT_TENSOR": TensorRole.CONSTANT, "USER_INPUT": TensorRole.INPUT}
    aliases: dict[int, int] = {}
    graph_values = []
    for value in dto.values:
        role = roles.get(input_kinds.get(value.value_id), TensorRole.OUTPUT if value.value_id in outputs else TensorRole.ACTIVATION)
        aliases[value.value_id] = value.value_id
        graph_values.append(GraphValue(value.value_id, value.stable_name, role, value.dtype, value.shape, value.strides, value.storage_offset, value.value_id, Access.READ_ONLY, content_sha256=value.content_sha256))
    value_map = {value.value_id: value for value in graph_values}
    graph_ops = []
    for op_id, node in enumerate(dto.nodes, 1):
        try:
            opcode, operands, attrs = _lower(node, value_map)
        except MeshIrError as error:
            if error.code != "E_EXPORT_UNSUPPORTED_OP":
                raise
            raise _unsupported_context(node, value_map).error(error.message, **error.context) from error
        if opcode in METADATA_VIEW_OPCODES:
            aliases[node.result_ids[0]] = aliases[operands[0]]
            value_map[node.result_ids[0]] = replace(value_map[node.result_ids[0]], alias_root=aliases[operands[0]])
        graph_ops.append(GraphOp(op_id, opcode, operands, node.result_ids, attrs, node.source_node_id))
    graph_values = tuple(value_map[index] for index in sorted(value_map))
    user_inputs = tuple(item.value_id for item in dto.inputs if item.kind == "USER_INPUT")
    function = GraphFunction(1, entrypoint, user_inputs, tuple(graph_ops), tuple(item.value_id for item in dto.outputs), dto.input_pytree, dto.output_pytree)
    debug = tuple((node.source_node_id, node.location) for node in dto.nodes if node.location is not None)
    return GraphModule.create(arch_digest, dto.semantic_hash(), entrypoint, profile_id, graph_values, (function,), dto.symbols, decomposition_digest=dto.decomposition_digest, debug_locations=debug)
