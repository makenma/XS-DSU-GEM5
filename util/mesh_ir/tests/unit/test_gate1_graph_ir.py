import copy
import dataclasses
import os
import subprocess
import sys

import pytest

from mesh_ir.canonical import semantic_sha256, strict_json_loads
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import (
    Access,
    Add,
    CeilDivByConst,
    Const,
    DType,
    FloorDivByConst,
    MulByConst,
    ShapeProductStride,
    Symbol,
    TensorRole,
    contiguous_strides,
)
from mesh_ir.ir.graph_ir import (
    ElementwiseAttrs,
    EmbeddingAttrs,
    GraphFunction,
    GraphModule,
    GraphOp,
    GraphValue,
    MatmulAttrs,
    MovementAttrs,
    NormAttrs,
    OpCode,
    PytreeKind,
    PytreeSpec,
    ReduceAttrs,
    SoftmaxAttrs,
    ViewAttrs,
    specialize_graph,
)
from mesh_ir.schema import load_schema, validate_schema


def dims(*values):
    return tuple(value if not isinstance(value, int) else Const(value) for value in values)


def value(value_id, shape, *, dtype=DType.FP32, role=TensorRole.INPUT, strides=None, offset=0, root=None, access=Access.READ_ONLY, logical=None, storage=None):
    shape = dims(*shape)
    return GraphValue(
        value_id,
        f"v{value_id}",
        role,
        dtype,
        shape,
        contiguous_strides(shape) if strides is None else strides,
        offset,
        value_id if root is None else root,
        access,
        logical,
        storage,
    )


def graph_for(opcode, values, operands, attrs, *, symbols=(), input_pytree=None, debug=()):
    inputs = tuple(item.value_id for item in values if item.role == TensorRole.INPUT)
    output = values[-1].value_id
    pytree = input_pytree or (PytreeSpec.leaf() if len(inputs) == 1 else PytreeSpec.tuple(tuple(PytreeSpec.leaf() for _ in inputs)))
    op = GraphOp(1, opcode, tuple(operands), (output,), attrs, "node:1")
    function = GraphFunction(1, "forward", inputs, (op,), (output,), pytree, PytreeSpec.leaf())
    return GraphModule.create("a" * 64, "b" * 64, "forward", "symbolic", tuple(values), (function,), tuple(symbols), decomposition_digest="c" * 64, debug_locations=debug)


def refreshed(graph):
    return dataclasses.replace(graph, semantic_sha256=semantic_sha256(graph.semantic_dict()))


def rejects(graph, code):
    with pytest.raises(MeshIrError) as error:
        refreshed(graph).verify()
    assert error.value.code == code


def symbolic_graph():
    symbol = Symbol(1, "s1", 1, 8, 2)
    return graph_for(
        OpCode.RELU,
        (value(1, (symbol, 4), strides=(ShapeProductStride((Const(4),)), 1)), value(2, (symbol, 4), role=TensorRole.OUTPUT)),
        (1,),
        ElementwiseAttrs(),
        symbols=(symbol,),
    )


def test_closed_dimension_dsl_evaluates_all_representable_forms():
    symbol = Symbol(1, "s1", 2, 10, 2)
    exprs = (
        Add(symbol, Const(3)),
        MulByConst(symbol, 4),
        FloorDivByConst(symbol, 2),
        CeilDivByConst(Add(symbol, Const(1)), 4),
    )
    assert [expr.evaluate({1: 6}) for expr in exprs] == [9, 24, 3, 2]


def test_profile_specialization_is_immutable_distinct_and_self_verifying():
    graph = symbolic_graph()
    first = specialize_graph(graph, "p2", {1: 2})
    second = specialize_graph(graph, "p4", {1: 4})
    assert first.semantic_sha256 != second.semantic_sha256
    assert first.values[0].shape == (Const(2), Const(4))
    assert graph.values[0].shape[0] == Symbol(1, "s1", 1, 8, 2)
    assert first.profile_bindings == ((1, 2),)
    assert first.values[0].logical_extent_bytes == 32
    assert first.values[0].storage_extent_bytes == 32
    first.verify()
    second.verify()


@pytest.mark.parametrize("bindings,code", [({}, "E_SHAPE_UNBOUND"), ({1: 3}, "E_SHAPE_PROFILE_MISMATCH"), ({1: 10}, "E_SHAPE_PROFILE_MISMATCH"), ({1: 2, 2: 4}, "E_SHAPE_PROFILE_MISMATCH")])
def test_profile_specialization_rejects_missing_divisibility_range_and_extra(bindings, code):
    with pytest.raises(MeshIrError) as error:
        specialize_graph(symbolic_graph(), "bad", bindings)
    assert error.value.code == code


def test_matmul_family_accepts_contraction_transpose_batch_broadcast_and_bias():
    cases = (
        (OpCode.MATMUL, (value(1, (2, 3)), value(2, (3, 4)), value(3, (2, 4), role=TensorRole.OUTPUT)), (1, 2), MatmulAttrs()),
        (OpCode.BMM, (value(1, (2, 3, 4)), value(2, (2, 4, 5)), value(3, (2, 3, 5), role=TensorRole.OUTPUT)), (1, 2), MatmulAttrs(batch_axes=(0,))),
        (OpCode.MATMUL, (value(1, (2, 1, 3, 4)), value(2, (1, 5, 4, 6)), value(3, (2, 5, 3, 6), role=TensorRole.OUTPUT)), (1, 2), MatmulAttrs(batch_axes=(0, 1))),
        (OpCode.MATMUL, (value(1, (2, 5, 3, 4)), value(2, (5, 6, 4), strides=(24, 1, 6)), value(3, (2, 5, 3, 6), role=TensorRole.OUTPUT)), (1, 2), MatmulAttrs(batch_axes=(1,), rhs_transpose=True)),
        (OpCode.LINEAR_BIAS, (value(1, (2, 3)), value(2, (4, 3), role=TensorRole.WEIGHT), value(3, (4,), role=TensorRole.WEIGHT), value(4, (2, 4), role=TensorRole.OUTPUT)), (1, 2, 3), MatmulAttrs(rhs_transpose=True)),
    )
    for opcode, values, operands, attrs in cases:
        graph_for(opcode, values, operands, attrs).verify()


@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda graph: dataclasses.replace(graph, values=(*graph.values[:1], dataclasses.replace(graph.values[1], shape=dims(5, 4)), *graph.values[2:])), "E_EXPORT_LAYOUT"),
        (lambda graph: dataclasses.replace(graph, values=(*graph.values[:-1], dataclasses.replace(graph.values[-1], shape=dims(2, 5)))), "E_EXPORT_LAYOUT"),
        (lambda graph: dataclasses.replace(graph, values=(*graph.values[:1], dataclasses.replace(graph.values[1], dtype=DType.FP16), *graph.values[2:])), "E_EXPORT_DTYPE"),
        (lambda graph: dataclasses.replace(graph, functions=(dataclasses.replace(graph.functions[0], ops=(dataclasses.replace(graph.functions[0].ops[0], attrs=MatmulAttrs(accum_dtype=DType.INT32)),)),)), "E_EXPORT_DTYPE"),
    ],
)
def test_matmul_family_rejects_contraction_result_operand_dtype_and_accumulation(mutate, code):
    base = graph_for(OpCode.MATMUL, (value(1, (2, 3)), value(2, (3, 4)), value(3, (2, 4), role=TensorRole.OUTPUT)), (1, 2), MatmulAttrs())
    rejects(mutate(base), code)


def test_elementwise_family_accepts_broadcast_scalar_and_pinned_promotions():
    graph_for(OpCode.ADD, (value(1, (2, 1, 4)), value(2, (1, 3, 1)), value(3, (2, 3, 4), role=TensorRole.OUTPUT)), (1, 2), ElementwiseAttrs()).verify()
    graph_for(OpCode.SUB, (value(1, (2, 3)), value(2, (2, 3), role=TensorRole.OUTPUT)), (1,), ElementwiseAttrs(2.0, "lhs", 2.0)).verify()
    graph_for(OpCode.DIV, (value(1, (2,), dtype=DType.INT32), value(2, (2,), dtype=DType.FP32, role=TensorRole.OUTPUT)), (1,), ElementwiseAttrs(2, "rhs")).verify()
    graph_for(OpCode.MUL, (value(1, (2,), dtype=DType.INT8), value(2, (2,), dtype=DType.INT32), value(3, (2,), dtype=DType.INT32, role=TensorRole.OUTPUT)), (1, 2), ElementwiseAttrs()).verify()
    graph_for(OpCode.EXP, (value(1, (2,), dtype=DType.INT32), value(2, (2,), dtype=DType.FP32, role=TensorRole.OUTPUT)), (1,), ElementwiseAttrs()).verify()
    graph_for(OpCode.RSQRT, (value(1, (2,), dtype=DType.INT8), value(2, (2,), dtype=DType.FP32, role=TensorRole.OUTPUT)), (1,), ElementwiseAttrs()).verify()
    for opcode in (OpCode.RELU, OpCode.GELU, OpCode.SILU):
        graph_for(opcode, (value(1, (2,), dtype=DType.FP16), value(2, (2,), dtype=DType.FP16, role=TensorRole.OUTPUT)), (1,), ElementwiseAttrs(approximation="tanh" if opcode == OpCode.GELU else "none")).verify()


def test_elementwise_family_rejects_shape_dtype_and_opcode_specific_attrs():
    binary = graph_for(OpCode.ADD, (value(1, (2, 1)), value(2, (1, 3)), value(3, (2, 3), role=TensorRole.OUTPUT)), (1, 2), ElementwiseAttrs())
    rejects(dataclasses.replace(binary, values=(binary.values[0], dataclasses.replace(binary.values[1], shape=dims(3, 1)), binary.values[2])), "E_EXPORT_LAYOUT")
    unary = graph_for(OpCode.EXP, (value(1, (2,), dtype=DType.INT32), value(2, (2,), dtype=DType.FP32, role=TensorRole.OUTPUT)), (1,), ElementwiseAttrs())
    rejects(dataclasses.replace(unary, values=(unary.values[0], dataclasses.replace(unary.values[1], dtype=DType.INT32))), "E_EXPORT_DTYPE")
    rejects(dataclasses.replace(unary, functions=(dataclasses.replace(unary.functions[0], ops=(dataclasses.replace(unary.functions[0].ops[0], attrs=ElementwiseAttrs(1, "rhs")),)),)), "E_EXPORT_UNSUPPORTED_OP")
    gelu = graph_for(OpCode.GELU, (value(1, (2,)), value(2, (2,), role=TensorRole.OUTPUT)), (1,), ElementwiseAttrs())
    rejects(dataclasses.replace(gelu, functions=(dataclasses.replace(gelu.functions[0], ops=(dataclasses.replace(gelu.functions[0].ops[0], attrs=ElementwiseAttrs(approximation="fast")),)),)), "E_EXPORT_UNSUPPORTED_OP")
    integer = graph_for(OpCode.ADD, (value(1, (2,), dtype=DType.INT32), value(2, (2,), dtype=DType.INT32, role=TensorRole.OUTPUT)), (1,), ElementwiseAttrs(2, "rhs"))
    rejects(dataclasses.replace(integer, functions=(dataclasses.replace(integer.functions[0], ops=(dataclasses.replace(integer.functions[0].ops[0], attrs=ElementwiseAttrs(2, "rhs", 0.5)),)),)), "E_EXPORT_DTYPE")


def test_view_and_copy_family_accepts_exact_shape_stride_offset_alias_contracts():
    cases = (
        (OpCode.RESHAPE_VIEW, (value(1, (2, 3)), value(2, (3, 2), role=TensorRole.OUTPUT, root=1)), ViewAttrs(shape=dims(3, 2))),
        (OpCode.TRANSPOSE_VIEW, (value(1, (2, 3), strides=(3, 1)), value(2, (3, 2), role=TensorRole.OUTPUT, strides=(1, 3), root=1)), ViewAttrs(permutation=(1, 0))),
        (OpCode.PERMUTE_VIEW, (value(1, (2, 3, 4), strides=(12, 4, 1)), value(2, (4, 2, 3), role=TensorRole.OUTPUT, strides=(1, 12, 4), root=1)), ViewAttrs(permutation=(2, 0, 1))),
        (OpCode.SLICE_VIEW, (value(1, (10,), strides=(1,)), value(2, (3,), role=TensorRole.OUTPUT, strides=(2,), offset=2, root=1)), ViewAttrs(axes=(0,), starts=(2,), ends=(8,), steps=(2,))),
        (OpCode.EXPAND_VIEW, (value(1, (1, 3), strides=(3, 1)), value(2, (2, 3), role=TensorRole.OUTPUT, strides=(0, 1), root=1)), ViewAttrs(shape=dims(2, 3), expanded_axes=(0,))),
    )
    for opcode, values, attrs in cases:
        graph_for(opcode, values, (1,), attrs).verify()
    graph_for(OpCode.CONTIGUOUS_COPY, (value(1, (2, 3), strides=(1, 2)), value(2, (2, 3), role=TensorRole.OUTPUT)), (1,), ViewAttrs(shape=dims(2, 3))).verify()


def test_reshape_accepts_repartitioning_within_the_same_strided_chunks():
    graph_for(
        OpCode.RESHAPE_VIEW,
        (value(1, (2, 3, 4), strides=(100, 4, 1)), value(2, (2, 12), role=TensorRole.OUTPUT, strides=(100, 1), root=1)),
        (1,),
        ViewAttrs(shape=dims(2, 12)),
    ).verify()


def test_view_and_copy_family_rejects_wrong_element_count_stride_offset_expand_and_alias():
    reshape = graph_for(OpCode.RESHAPE_VIEW, (value(1, (2, 3)), value(2, (3, 2), role=TensorRole.OUTPUT, root=1)), (1,), ViewAttrs(shape=dims(3, 2)))
    rejects(dataclasses.replace(reshape, values=(reshape.values[0], dataclasses.replace(reshape.values[1], shape=dims(4, 2)))), "E_EXPORT_LAYOUT")
    transpose = graph_for(OpCode.TRANSPOSE_VIEW, (value(1, (2, 3), strides=(3, 1)), value(2, (3, 2), role=TensorRole.OUTPUT, strides=(1, 3), root=1)), (1,), ViewAttrs(permutation=(1, 0)))
    rejects(dataclasses.replace(transpose, values=(transpose.values[0], dataclasses.replace(transpose.values[1], strides=(2, 1)))), "E_EXPORT_LAYOUT")
    permuted = graph_for(OpCode.PERMUTE_VIEW, (value(1, (2, 3, 4), strides=(12, 4, 1)), value(2, (4, 2, 3), role=TensorRole.OUTPUT, strides=(1, 12, 4), root=1)), (1,), ViewAttrs(permutation=(2, 0, 1)))
    with pytest.raises(MeshIrError) as error:
        graph_for(OpCode.TRANSPOSE_VIEW, permuted.values, (1,), ViewAttrs(permutation=(2, 0, 1)))
    assert error.value.code == "E_EXPORT_LAYOUT"
    sliced = graph_for(OpCode.SLICE_VIEW, (value(1, (10,), strides=(1,)), value(2, (3,), role=TensorRole.OUTPUT, strides=(2,), offset=2, root=1)), (1,), ViewAttrs(axes=(0,), starts=(2,), ends=(8,), steps=(2,)))
    rejects(dataclasses.replace(sliced, values=(sliced.values[0], dataclasses.replace(sliced.values[1], storage_offset=0))), "E_EXPORT_LAYOUT")
    rejects(dataclasses.replace(sliced, functions=(dataclasses.replace(sliced.functions[0], ops=(dataclasses.replace(sliced.functions[0].ops[0], attrs=ViewAttrs(axes=(0,), starts=(2,), ends=(8,), steps=(-1,))),)),)), "E_EXPORT_LAYOUT")
    expanded = graph_for(OpCode.EXPAND_VIEW, (value(1, (1, 3), strides=(3, 1)), value(2, (2, 3), role=TensorRole.OUTPUT, strides=(0, 1), root=1)), (1,), ViewAttrs(shape=dims(2, 3), expanded_axes=(0,)))
    rejects(dataclasses.replace(expanded, functions=(dataclasses.replace(expanded.functions[0], ops=(dataclasses.replace(expanded.functions[0].ops[0], attrs=ViewAttrs(shape=dims(2, 3), expanded_axes=())),)),)), "E_EXPORT_LAYOUT")
    copied = graph_for(OpCode.CONTIGUOUS_COPY, (value(1, (2, 3)), value(2, (2, 3), role=TensorRole.OUTPUT)), (1,), ViewAttrs(shape=dims(2, 3)))
    rejects(dataclasses.replace(copied, values=(copied.values[0], dataclasses.replace(copied.values[1], alias_root=1))), "E_EXPORT_LAYOUT")


def test_movement_and_embedding_families_accept_derived_shapes_and_int32_indices():
    graph_for(OpCode.CONCAT, (value(1, (2, 3)), value(2, (4, 3)), value(3, (6, 3), role=TensorRole.OUTPUT)), (1, 2), MovementAttrs(axis=0)).verify()
    graph_for(OpCode.GATHER_ROWS, (value(1, (4, 3)), value(2, (2,), dtype=DType.INT32), value(3, (2, 3), role=TensorRole.OUTPUT)), (1, 2), MovementAttrs(axis=0)).verify()
    graph_for(OpCode.EMBEDDING_LOOKUP, (value(1, (8, 4), role=TensorRole.WEIGHT), value(2, (2, 3), dtype=DType.INT32), value(3, (2, 3, 4), role=TensorRole.OUTPUT)), (1, 2), EmbeddingAttrs(-1, False, False)).verify()


def test_movement_and_embedding_families_reject_shape_index_dtype_rank_and_options():
    concat = graph_for(OpCode.CONCAT, (value(1, (2, 3)), value(2, (4, 3)), value(3, (6, 3), role=TensorRole.OUTPUT)), (1, 2), MovementAttrs(axis=0))
    rejects(dataclasses.replace(concat, values=(*concat.values[:-1], dataclasses.replace(concat.values[-1], shape=dims(7, 3)))), "E_EXPORT_LAYOUT")
    gather = graph_for(OpCode.GATHER_ROWS, (value(1, (4, 3)), value(2, (2,), dtype=DType.INT32), value(3, (2, 3), role=TensorRole.OUTPUT)), (1, 2), MovementAttrs(axis=0))
    rejects(dataclasses.replace(gather, values=(gather.values[0], dataclasses.replace(gather.values[1], dtype=DType.INT8), gather.values[2])), "E_EXPORT_DTYPE")
    rejects(dataclasses.replace(gather, values=(gather.values[0], dataclasses.replace(gather.values[1], shape=dims(1, 2), strides=(2, 1)), gather.values[2])), "E_EXPORT_LAYOUT")
    embedding = graph_for(OpCode.EMBEDDING_LOOKUP, (value(1, (8, 4), role=TensorRole.WEIGHT), value(2, (2, 3), dtype=DType.INT32), value(3, (2, 3, 4), role=TensorRole.OUTPUT)), (1, 2), EmbeddingAttrs(-1, False, False))
    rejects(dataclasses.replace(embedding, values=(dataclasses.replace(embedding.values[0], shape=dims(8, 2, 2), strides=(4, 2, 1)), *embedding.values[1:])), "E_EXPORT_LAYOUT")
    rejects(dataclasses.replace(embedding, functions=(dataclasses.replace(embedding.functions[0], ops=(dataclasses.replace(embedding.functions[0].ops[0], attrs=EmbeddingAttrs(8, False, False)),)),)), "E_EXPORT_LAYOUT")


def test_reduction_family_accepts_normalized_axes_rank_zero_keepdim_and_dtype_policy():
    graph_for(OpCode.REDUCE_SUM, (value(1, (2, 3, 4)), value(2, (2, 4), role=TensorRole.OUTPUT)), (1,), ReduceAttrs((1,), False, DType.FP32, DType.FP32)).verify()
    graph_for(OpCode.REDUCE_MAX, (value(1, (2, 3)), value(2, (), role=TensorRole.OUTPUT)), (1,), ReduceAttrs((0, 1), False, DType.FP32, DType.FP32)).verify()
    graph_for(OpCode.REDUCE_MEAN, (value(1, (2, 3), dtype=DType.FP16), value(2, (2, 1), dtype=DType.FP32, role=TensorRole.OUTPUT)), (1,), ReduceAttrs((1,), True, DType.FP32, DType.FP32)).verify()


def test_reduction_family_rejects_duplicate_normalized_axis_shape_dtype_accum_and_order():
    base = graph_for(OpCode.REDUCE_SUM, (value(1, (2, 3)), value(2, (2,), role=TensorRole.OUTPUT)), (1,), ReduceAttrs((1,), False, DType.FP32, DType.FP32))
    for attrs, code in (
        (ReduceAttrs((-1,), False, DType.FP32, DType.FP32), "E_EXPORT_LAYOUT"),
        (ReduceAttrs((-1, 1), False, DType.FP32, DType.FP32), "E_EXPORT_LAYOUT"),
        (ReduceAttrs((1,), False, DType.FP16, DType.FP32), "E_EXPORT_DTYPE"),
        (ReduceAttrs((1,), False, DType.FP32, DType.INT32), "E_EXPORT_DTYPE"),
        (ReduceAttrs((1,), False, DType.FP32, DType.FP32, "tree"), "E_EXPORT_UNSUPPORTED_OP"),
    ):
        rejects(dataclasses.replace(base, functions=(dataclasses.replace(base.functions[0], ops=(dataclasses.replace(base.functions[0].ops[0], attrs=attrs),)),)), code)
    rejects(dataclasses.replace(base, values=(base.values[0], dataclasses.replace(base.values[1], shape=dims(3,)))), "E_EXPORT_LAYOUT")


def test_norm_and_softmax_families_accept_trailing_extents_affine_flags_and_dtype():
    layer_values = (value(1, (2, 3, 4)), value(2, (3, 4), role=TensorRole.WEIGHT), value(3, (3, 4), role=TensorRole.WEIGHT), value(4, (2, 3, 4), role=TensorRole.OUTPUT))
    graph_for(OpCode.LAYERNORM, layer_values, (1, 2, 3), NormAttrs((1, 2), 1e-5, True, True)).verify()
    rms_values = (value(1, (2, 4), dtype=DType.BF16), value(2, (4,), dtype=DType.BF16, role=TensorRole.WEIGHT), value(3, (2, 4), dtype=DType.BF16, role=TensorRole.OUTPUT))
    graph_for(OpCode.RMSNORM, rms_values, (1, 2), NormAttrs((1,), DType.BF16.machine_epsilon, True, False)).verify()
    graph_for(OpCode.SOFTMAX, (value(1, (2, 4), dtype=DType.FP16), value(2, (2, 4), dtype=DType.FP32, role=TensorRole.OUTPUT)), (1,), SoftmaxAttrs(1, DType.FP32)).verify()


def test_safe_softmax_flag_is_typed_serialized_and_semantically_hashed():
    values = (value(1, (2, 4)), value(2, (2, 4), role=TensorRole.OUTPUT))
    ordinary = graph_for(OpCode.SOFTMAX, values, (1,), SoftmaxAttrs(1, DType.FP32))
    safe = graph_for(OpCode.SOFTMAX, values, (1,), SoftmaxAttrs(1, DType.FP32, True))
    assert ordinary.canonical_dict()["functions"][0]["ops"][0]["attrs"]["zero_fully_masked_rows"] is False
    assert safe.canonical_dict()["functions"][0]["ops"][0]["attrs"]["zero_fully_masked_rows"] is True
    assert ordinary.semantic_sha256 != safe.semantic_sha256
    malformed_attrs = dataclasses.replace(safe.functions[0].ops[0].attrs, zero_fully_masked_rows=1)
    rejects(dataclasses.replace(safe, functions=(dataclasses.replace(safe.functions[0], ops=(dataclasses.replace(safe.functions[0].ops[0], attrs=malformed_attrs),)),)), "E_EXPORT_UNSUPPORTED_OP")
    malformed_payload = safe.canonical_dict()
    malformed_payload["functions"][0]["ops"][0]["attrs"]["zero_fully_masked_rows"] = 1
    with pytest.raises(MeshIrError) as error:
        validate_schema("mesh_graph_v1.schema.json", malformed_payload, "Graph IR")
    assert error.value.code == "E_CONFIG"


def test_norm_and_softmax_families_reject_nontrailing_axes_affine_shape_epsilon_and_output():
    values = (value(1, (2, 3, 4)), value(2, (3, 4), role=TensorRole.WEIGHT), value(3, (2, 3, 4), role=TensorRole.OUTPUT))
    norm = graph_for(OpCode.LAYERNORM, values, (1, 2), NormAttrs((1, 2), 1e-5, True, False))
    for attrs, code in (
        (NormAttrs((-2, -1), 1e-5, True, False), "E_EXPORT_LAYOUT"),
        (NormAttrs((0, 2), 1e-5, True, False), "E_EXPORT_LAYOUT"),
        (NormAttrs((1, 2), -1.0, True, False), "E_EXPORT_DTYPE"),
        (NormAttrs((1, 2), 1e-5, False, False), "E_EXPORT_UNSUPPORTED_OP"),
    ):
        rejects(dataclasses.replace(norm, functions=(dataclasses.replace(norm.functions[0], ops=(dataclasses.replace(norm.functions[0].ops[0], attrs=attrs),)),)), code)
    rejects(dataclasses.replace(norm, values=(norm.values[0], dataclasses.replace(norm.values[1], shape=dims(4,), strides=(1,)), norm.values[2])), "E_EXPORT_LAYOUT")
    softmax = graph_for(OpCode.SOFTMAX, (value(1, (2, 4), dtype=DType.FP16), value(2, (2, 4), dtype=DType.FP32, role=TensorRole.OUTPUT)), (1,), SoftmaxAttrs(1, DType.FP32))
    rejects(dataclasses.replace(softmax, values=(softmax.values[0], dataclasses.replace(softmax.values[1], shape=dims(2, 3)))), "E_EXPORT_LAYOUT")
    rejects(dataclasses.replace(softmax, functions=(dataclasses.replace(softmax.functions[0], ops=(dataclasses.replace(softmax.functions[0].ops[0], attrs=SoftmaxAttrs(-1, DType.FP32)),)),)), "E_EXPORT_LAYOUT")


def test_movement_and_slice_reject_noncanonical_negative_axes():
    concat = graph_for(OpCode.CONCAT, (value(1, (2, 3)), value(2, (2, 4)), value(3, (2, 7), role=TensorRole.OUTPUT)), (1, 2), MovementAttrs(axis=1))
    rejects(dataclasses.replace(concat, functions=(dataclasses.replace(concat.functions[0], ops=(dataclasses.replace(concat.functions[0].ops[0], attrs=MovementAttrs(axis=-1)),)),)), "E_EXPORT_LAYOUT")
    sliced = graph_for(OpCode.SLICE_VIEW, (value(1, (2, 4), strides=(4, 1)), value(2, (2, 2), role=TensorRole.OUTPUT, strides=(4, 2), root=1)), (1,), ViewAttrs(axes=(1,), starts=(0,), ends=(4,), steps=(2,)))
    rejects(dataclasses.replace(sliced, functions=(dataclasses.replace(sliced.functions[0], ops=(dataclasses.replace(sliced.functions[0].ops[0], attrs=ViewAttrs(axes=(-1,), starts=(0,), ends=(4,), steps=(2,))),)),)), "E_EXPORT_LAYOUT")


def test_symbol_occurrences_layout_extents_and_storage_alias_spans_are_verified():
    graph = symbolic_graph()
    counterfeit = Symbol(1, "s1", 1, 10, 2)
    rejects(dataclasses.replace(graph, values=(dataclasses.replace(graph.values[0], shape=(counterfeit, Const(4))), graph.values[1])), "E_SHAPE_PROFILE_MISMATCH")
    concrete = graph_for(OpCode.RELU, (value(1, (2, 3), logical=24, storage=24), value(2, (2, 3), role=TensorRole.OUTPUT, logical=24, storage=24)), (1,), ElementwiseAttrs())
    rejects(dataclasses.replace(concrete, values=(dataclasses.replace(concrete.values[0], logical_extent_bytes=20), concrete.values[1])), "E_EXPORT_LAYOUT")
    transpose = graph_for(OpCode.TRANSPOSE_VIEW, (value(1, (2, 3), strides=(3, 1)), value(2, (3, 2), role=TensorRole.OUTPUT, strides=(1, 3), root=1)), (1,), ViewAttrs(permutation=(1, 0)))
    rejects(dataclasses.replace(transpose, values=(transpose.values[0], dataclasses.replace(transpose.values[1], storage_offset=1))), "E_EXPORT_LAYOUT")
    overlapping = graph_for(OpCode.RELU, (value(1, (2, 3)), value(2, (2, 3), role=TensorRole.OUTPUT)), (1,), ElementwiseAttrs())
    rejects(dataclasses.replace(overlapping, values=(dataclasses.replace(overlapping.values[0], strides=(1, 1)), overlapping.values[1])), "E_EXPORT_LAYOUT")


def test_graph_verifier_rejects_malformed_typed_boundaries_without_raw_exceptions():
    graph = symbolic_graph()
    mutations = (
        dataclasses.replace(graph, values=(dataclasses.replace(graph.values[0], dtype=1), graph.values[1])),
        dataclasses.replace(graph, values=(dataclasses.replace(graph.values[0], value_id=True), graph.values[1])),
        dataclasses.replace(graph, functions=(dataclasses.replace(graph.functions[0], ops=(dataclasses.replace(graph.functions[0].ops[0], opcode="RELU"),)),)),
        dataclasses.replace(graph, functions=(dataclasses.replace(graph.functions[0], input_pytree=PytreeSpec("tuple", (PytreeSpec.leaf(),))),)),
    )
    for malformed in mutations:
        with pytest.raises(MeshIrError):
            malformed.verify()


def test_dense_definitions_signatures_pytree_alias_hashes_and_relative_debug_are_verified():
    graph = symbolic_graph()
    mutations = (
        (dataclasses.replace(graph, values=(dataclasses.replace(graph.values[0], value_id=2), graph.values[1])), "E_ABI_ORDER"),
        (dataclasses.replace(graph, functions=(dataclasses.replace(graph.functions[0], inputs=(99,)),)), "E_ABI_BOUNDS"),
        (dataclasses.replace(graph, values=(graph.values[0], dataclasses.replace(graph.values[1], role=TensorRole.INPUT))), "E_ABI_BOUNDS"),
        (dataclasses.replace(graph, values=(graph.values[0], dataclasses.replace(graph.values[1], alias_root=99))), "E_EXPORT_LAYOUT"),
        (dataclasses.replace(graph, functions=(dataclasses.replace(graph.functions[0], ops=(dataclasses.replace(graph.functions[0].ops[0], operands=()),)),)), "E_EXPORT_UNSUPPORTED_OP"),
        (dataclasses.replace(graph, arch_digest="z" * 64), "E_ARCH_DIGEST"),
        (dataclasses.replace(graph, debug_locations=(("node:1", "/tmp/source.py:9"),)), "E_EXPORT_LAYOUT"),
    )
    for malformed, code in mutations:
        rejects(malformed, code)
    tampered = dataclasses.replace(graph, semantic_sha256="0" * 64)
    with pytest.raises(MeshIrError) as error:
        tampered.verify()
    assert error.value.code == "E_ABI_CHECKSUM"


def test_debug_locations_are_legal_relative_sidecars_excluded_from_semantic_hash():
    graph = symbolic_graph()
    changed = dataclasses.replace(graph, debug_locations=(("node:1", "models/source.py:9"),))
    assert changed.semantic_sha256 == graph.semantic_sha256
    changed.verify()


def test_scalar_and_zero_sized_tensor_extents_are_distinct_and_checked():
    scalar = value(1, (), role=TensorRole.INPUT)
    function = GraphFunction(1, "forward", (1,), (), (1,), PytreeSpec.leaf(), PytreeSpec.leaf())
    graph = GraphModule.create("a" * 64, "b" * 64, "forward", "symbolic", (scalar,), (function,))
    specialized = specialize_graph(graph, "scalar", {})
    assert specialized.values[0].logical_extent_bytes == 4
    assert specialized.values[0].storage_extent_bytes == 4
    zero = value(1, (0, 4), role=TensorRole.INPUT)
    zero_graph = GraphModule.create("a" * 64, "b" * 64, "forward", "zero", (zero,), (function,))
    specialized_zero = specialize_graph(zero_graph, "zero", {})
    assert specialized_zero.values[0].logical_extent_bytes == 0
    assert specialized_zero.values[0].storage_extent_bytes == 0


def test_alias_proof_accounts_for_a_root_that_can_be_empty():
    symbol = Symbol(1, "s1", 0, 1)
    root = value(1, (symbol,), strides=(1,))
    alias = value(2, (1,), strides=(1,), root=1)
    pytree = PytreeSpec.tuple((PytreeSpec.leaf(), PytreeSpec.leaf()))
    function = GraphFunction(1, "forward", (1, 2), (), (1, 2), pytree, pytree)
    with pytest.raises(MeshIrError) as error:
        GraphModule.create("a" * 64, "b" * 64, "forward", "symbolic", (root, alias), (function,), (symbol,))
    assert error.value.code == "E_EXPORT_LAYOUT"


def test_bmm_rank_linear_bias_and_extent_overflow_have_semantic_failures():
    bmm = graph_for(OpCode.BMM, (value(1, (2, 3, 4)), value(2, (2, 4, 5)), value(3, (2, 3, 5), role=TensorRole.OUTPUT)), (1, 2), MatmulAttrs(batch_axes=(0,)))
    rejects(dataclasses.replace(bmm, values=(dataclasses.replace(bmm.values[0], shape=dims(3, 4), strides=(4, 1)), *bmm.values[1:])), "E_EXPORT_LAYOUT")
    linear = graph_for(OpCode.LINEAR_BIAS, (value(1, (2, 3)), value(2, (4, 3), role=TensorRole.WEIGHT), value(3, (4,), role=TensorRole.WEIGHT), value(4, (2, 4), role=TensorRole.OUTPUT)), (1, 2, 3), MatmulAttrs(rhs_transpose=True))
    rejects(dataclasses.replace(linear, values=(*linear.values[:2], dataclasses.replace(linear.values[2], shape=dims(5,), strides=(1,)), linear.values[3])), "E_EXPORT_LAYOUT")
    symbol = Symbol(1, "s1", 1, 1 << 63)
    with pytest.raises(MeshIrError) as error:
        graph_for(OpCode.RELU, (value(1, (symbol,), strides=(1,)), value(2, (symbol,), role=TensorRole.OUTPUT)), (1,), ElementwiseAttrs(), symbols=(symbol,))
    assert error.value.code == "E_ABI_OVERFLOW"


def test_canonical_dict_is_actual_serialized_json_native_schema_instance():
    symbol = Symbol(1, "s1", 2, 8)
    graph = graph_for(
        OpCode.RESHAPE_VIEW,
        (value(1, (symbol, 2), strides=(2, 1)), value(2, (MulByConst(symbol, 2), 1), role=TensorRole.OUTPUT, root=1)),
        (1,),
        ViewAttrs(shape=(MulByConst(symbol, 2), Const(1))),
        symbols=(symbol,),
        input_pytree=PytreeSpec(PytreeKind.DICT, (PytreeSpec.leaf(),), ("x",)),
        debug=(("node:1", "models/reshape.py:7"),),
    )
    payload = strict_json_loads(graph.canonical_bytes())
    assert payload == graph.canonical_dict()
    assert payload["functions"][0]["ops"][0]["attrs"]["shape"][0]["kind"] == "mul_by_const"
    assert payload["functions"][0]["input_pytree"] == {"kind": "dict", "children": [{"kind": "leaf", "children": [], "keys": []}], "keys": ["x"]}
    validate_schema("mesh_graph_v1.schema.json", payload, "Graph IR")


def test_schema_accepts_canonical_large_u64_and_rejects_noncanonical_forms():
    graph = graph_for(
        OpCode.SLICE_VIEW,
        (value(1, (4,), strides=(1,)), value(2, (4,), role=TensorRole.OUTPUT, strides=(1,), root=1)),
        (1,),
        ViewAttrs(axes=(0,), starts=(0,), ends=((1 << 63) - 1,), steps=(1,)),
    )
    payload = strict_json_loads(graph.canonical_bytes())
    assert payload["functions"][0]["ops"][0]["attrs"]["ends"] == ["0x7fffffffffffffff"]
    validate_schema("mesh_graph_v1.schema.json", payload, "Graph IR")
    large_step = graph_for(
        OpCode.SLICE_VIEW,
        (value(1, (4,), strides=(1,)), value(2, (1,), role=TensorRole.OUTPUT, strides=(1 << 63,), root=1)),
        (1,),
        ViewAttrs(axes=(0,), starts=(0,), ends=((1 << 63) - 1,), steps=(1 << 63,)),
    )
    assert large_step.canonical_dict()["functions"][0]["ops"][0]["attrs"]["steps"] == ["0x8000000000000000"]
    huge = graph_for(OpCode.RELU, (value(1, (1 << 53,), dtype=DType.INT8, strides=(1,)), value(2, (1 << 53,), dtype=DType.INT8, role=TensorRole.OUTPUT, strides=(1,))), (1,), ElementwiseAttrs())
    assert huge.canonical_dict()["values"][0]["shape"][0]["value"] == "0x20000000000000"
    for invalid in ("9007199254740992", "0X20000000000000", "0x020000000000000", "0x10000000000000", "0x10000000000000000", True, 9007199254740992):
        candidate = copy.deepcopy(payload)
        candidate["functions"][0]["ops"][0]["attrs"]["ends"][0] = invalid
        with pytest.raises(MeshIrError) as error:
            validate_schema("mesh_graph_v1.schema.json", candidate, "Graph IR")
        assert error.value.code == "E_CONFIG"


def test_schema_rejects_unknown_missing_mistagged_and_opcode_mismatched_nested_records():
    payload = symbolic_graph().canonical_dict()
    candidates = []
    unknown = copy.deepcopy(payload)
    unknown["functions"][0]["ops"][0]["attrs"]["epsilon"] = 1e-5
    candidates.append(unknown)
    missing = copy.deepcopy(payload)
    del missing["values"][0]["shape"][0]["maximum"]
    candidates.append(missing)
    mistagged = copy.deepcopy(payload)
    mistagged["values"][0]["shape"][0]["kind"] = "unknown"
    candidates.append(mistagged)
    wrong_attrs = copy.deepcopy(payload)
    wrong_attrs["functions"][0]["ops"][0]["attrs"] = {
        "kind": "MatmulAttrs", "batch_axes": [], "lhs_contract_axis": -1, "rhs_contract_axis": -2,
        "lhs_transpose": False, "rhs_transpose": False, "alpha": 1.0, "beta": 1.0, "accum_dtype": 1,
    }
    candidates.append(wrong_attrs)
    negative_axis = copy.deepcopy(payload)
    negative_axis["functions"][0]["ops"][0]["opcode"] = "SOFTMAX"
    negative_axis["functions"][0]["ops"][0]["attrs"] = {"kind": "SoftmaxAttrs", "axis": -1, "output_dtype": 1}
    candidates.append(negative_axis)
    wrong_opcode = copy.deepcopy(payload)
    wrong_opcode["functions"][0]["ops"][0]["opcode"] = "NOT_AN_OP"
    candidates.append(wrong_opcode)
    for candidate in candidates:
        with pytest.raises(MeshIrError) as error:
            validate_schema("mesh_graph_v1.schema.json", candidate, "Graph IR")
        assert error.value.code == "E_CONFIG"


def test_schema_enum_identities_track_project_types():
    schema = load_schema("mesh_graph_v1.schema.json")
    assert set(schema["$defs"]["dtype"]["enum"]) == {int(dtype) for dtype in DType}
    assert set(schema["$defs"]["opcode"]["enum"]) == {opcode.value for opcode in OpCode}


def test_dtype_widths_exhaust_the_generated_supported_identity_set():
    assert {dtype: dtype.byte_width for dtype in DType} == {
        DType.FP32: 4,
        DType.FP16: 2,
        DType.BF16: 2,
        DType.INT8: 1,
        DType.INT32: 4,
    }


def test_importing_graph_ir_does_not_import_torch():
    environment = dict(os.environ)
    environment["PYTHONPATH"] = "util/mesh_ir"
    result = subprocess.run(
        [sys.executable, "-c", "import sys; import mesh_ir.ir.graph_ir; assert 'torch' not in sys.modules"],
        cwd=os.getcwd(),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_safety_regressions_reject_bmm_batch_broadcast_reshape_reorder_span_and_mixed_overlap():
    cases = (
        lambda: graph_for(OpCode.BMM, (value(1, (1, 3, 4)), value(2, (2, 4, 5)), value(3, (2, 3, 5), role=TensorRole.OUTPUT)), (1, 2), MatmulAttrs(batch_axes=(0,))),
        lambda: graph_for(OpCode.RESHAPE_VIEW, (value(1, (2, 3), strides=(1, 2)), value(2, (6,), role=TensorRole.OUTPUT, root=1)), (1,), ViewAttrs(shape=dims(6))),
        lambda: graph_for(OpCode.RESHAPE_VIEW, (value(1, (2, 3)), value(2, (3, 2), role=TensorRole.OUTPUT, strides=(100, 1), root=1)), (1,), ViewAttrs(shape=dims(3, 2))),
        lambda: graph_for(OpCode.RELU, (value(1, (2, 2, 2), strides=(0, 1, 1)), value(2, (2, 2, 2), role=TensorRole.OUTPUT)), (1,), ElementwiseAttrs()),
    )
    for build in cases:
        with pytest.raises(MeshIrError) as error:
            build()
        assert error.value.code == "E_EXPORT_LAYOUT"


def test_oversized_numeric_attrs_and_checksum_invalid_specialization_are_stable_errors():
    with pytest.raises(MeshIrError) as error:
        graph_for(OpCode.MATMUL, (value(1, (2, 3)), value(2, (3, 4)), value(3, (2, 4), role=TensorRole.OUTPUT)), (1, 2), MatmulAttrs(alpha=2**2048))
    assert error.value.code == "E_EXPORT_DTYPE"
    graph = graph_for(OpCode.RELU, (value(1, (2, 3)), value(2, (2, 3), role=TensorRole.OUTPUT)), (1,), ElementwiseAttrs())
    with pytest.raises(MeshIrError) as error:
        specialize_graph(dataclasses.replace(graph, semantic_sha256="0" * 64), "bad", {})
    assert error.value.code == "E_ABI_CHECKSUM"
