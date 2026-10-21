import dataclasses

import pytest

from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Access, Const, DType, FixedStride, Symbol, contiguous_strides
from mesh_ir.ir.graph_ir import ElementwiseAttrs, EmbeddingAttrs, MatmulAttrs, MovementAttrs, NormAttrs, OpCode, ReduceAttrs, SoftmaxAttrs, ViewAttrs
import mesh_ir.ir.graph_verify as verifier


def tensor(shape, dtype=DType.FP32):
    dimensions = tuple(item if type(item) is not int else Const(item) for item in shape)
    return verifier.TensorDataContract(dimensions, dtype)


def layout(value_id, shape, *, dtype=DType.FP32, strides=None, offset=0, root=None, access=Access.READ_ONLY):
    dimensions = tuple(item if type(item) is not int else Const(item) for item in shape)
    layout_strides = contiguous_strides(dimensions) if strides is None else tuple(item if type(item) is not int else FixedStride(item) for item in strides)
    return verifier.TensorLayoutContract(
        shape=dimensions,
        dtype=dtype,
        value_id=value_id,
        strides=layout_strides,
        storage_offset=offset,
        alias_root=value_id if root is None else root,
        access=access,
    )


def check(opcode, attrs, operands, result, matrix_result_form=None):
    options = {} if matrix_result_form is None else {"matrix_result_form": matrix_result_form}
    verifier.verify_operation_data_contract(1, opcode, attrs, operands, result, **options)


def rejects(opcode, attrs, operands, result, code, matrix_result_form=None):
    with pytest.raises(MeshIrError) as error:
        check(opcode, attrs, operands, result, matrix_result_form)
    assert error.value.code == code


def check_layout(opcode, attrs, operands, result):
    verifier.verify_operation_layout_contract(7, opcode, attrs, operands, result)


def rejects_layout(opcode, attrs, operands, result, code="E_EXPORT_LAYOUT"):
    with pytest.raises(MeshIrError) as error:
        check_layout(opcode, attrs, operands, result)
    assert error.value.code == code


@pytest.mark.parametrize(
    "opcode,attrs,operands,result",
    [
        (OpCode.RESHAPE_VIEW, ViewAttrs(shape=(Const(3), Const(2))), (layout(1, (2, 3)),), layout(2, (3, 2), root=1)),
        (OpCode.TRANSPOSE_VIEW, ViewAttrs(permutation=(0, 2, 1)), (layout(1, (2, 3, 4), strides=(12, 4, 1)),), layout(2, (2, 4, 3), strides=(12, 1, 4), root=1)),
        (OpCode.SLICE_VIEW, ViewAttrs(axes=(1,), starts=(2,), ends=(8,), steps=(2,)), (layout(1, (2, 10), strides=(10, 1)),), layout(2, (2, 3), strides=(10, 2), offset=2, root=1)),
        (OpCode.EXPAND_VIEW, ViewAttrs(shape=(Const(2), Const(3)), expanded_axes=(0,)), (layout(1, (1, 3), strides=(3, 1)),), layout(2, (2, 3), strides=(0, 1), root=1, access=Access.READ_ONLY)),
        (OpCode.RESHAPE_VIEW, ViewAttrs(shape=(Const(1),)), (layout(1, ()),), layout(2, (1,), root=1)),
        (OpCode.CONTIGUOUS_COPY, ViewAttrs(shape=(Const(0), Const(3))), (layout(1, (0, 3), strides=(3, 1)),), layout(2, (0, 3), strides=(3, 1))),
        (OpCode.CONTIGUOUS_COPY, ViewAttrs(shape=(Const(2), Const(3))), (layout(1, (2, 3), strides=(1, 2)),), layout(2, (2, 3), strides=(3, 1))),
    ],
)
def test_shared_layout_contract_accepts_existing_view_and_copy_proofs(opcode, attrs, operands, result):
    check_layout(opcode, attrs, operands, result)


@pytest.mark.parametrize(
    "opcode,attrs,operands,result",
    [
        (OpCode.RESHAPE_VIEW, ViewAttrs(shape=(Const(6),)), (layout(1, (2, 3), strides=(1, 2)),), layout(2, (6,), strides=(1,), root=1)),
        (OpCode.SLICE_VIEW, ViewAttrs(axes=(1,), starts=(2,), ends=(8,), steps=(2,)), (layout(1, (2, 10), strides=(10, 1)),), layout(2, (2, 3), strides=(10, 2), offset=3, root=1)),
        (OpCode.PERMUTE_VIEW, ViewAttrs(permutation=(1, 0)), (layout(1, (2, 3), strides=(3, 1)),), layout(2, (3, 2), strides=(3, 1), root=1)),
        (OpCode.EXPAND_VIEW, ViewAttrs(shape=(Const(2), Const(3)), expanded_axes=(0,)), (layout(1, (1, 3), strides=(3, 1)),), layout(2, (2, 3), strides=(0, 1), root=1, access=Access.READ_WRITE)),
        (OpCode.RESHAPE_VIEW, ViewAttrs(shape=(Const(3), Const(2))), (layout(1, (2, 3)),), layout(2, (3, 2))),
        (OpCode.CONTIGUOUS_COPY, ViewAttrs(shape=(Const(2), Const(3))), (layout(1, (2, 3), strides=(1, 2)),), layout(2, (2, 3), strides=(1, 2))),
    ],
)
def test_shared_layout_contract_rejects_fresh_layout_mutations(opcode, attrs, operands, result):
    rejects_layout(opcode, attrs, operands, result)


@pytest.mark.parametrize(
    "op_id,opcode,attrs,operands,result",
    [
        (True, OpCode.RESHAPE_VIEW, ViewAttrs(shape=(Const(6),)), (layout(1, (2, 3)),), layout(2, (6,), root=1)),
        (7, "RESHAPE_VIEW", ViewAttrs(shape=(Const(6),)), (layout(1, (2, 3)),), layout(2, (6,), root=1)),
        (7, OpCode.RESHAPE_VIEW, MovementAttrs(), (layout(1, (2, 3)),), layout(2, (6,), root=1)),
        (7, OpCode.RESHAPE_VIEW, ViewAttrs(shape=(Const(6),)), [], layout(2, (6,), root=1)),
        (7, OpCode.RESHAPE_VIEW, ViewAttrs(shape=(Const(6),)), (), layout(2, (6,), root=1)),
        (7, OpCode.RESHAPE_VIEW, ViewAttrs(shape=(Const(6),)), (layout(1, (2, 3)),), object()),
        (7, OpCode.RESHAPE_VIEW, ViewAttrs(shape=(Const(6),)), (dataclasses.replace(layout(1, (2, 3)), value_id=0),), layout(2, (6,), root=1)),
        (7, OpCode.RESHAPE_VIEW, ViewAttrs(shape=(Const(6),)), (dataclasses.replace(layout(1, (2, 3)), strides=[FixedStride(3), FixedStride(1)]),), layout(2, (6,), root=1)),
        (7, OpCode.RESHAPE_VIEW, ViewAttrs(shape=(Const(6),)), (dataclasses.replace(layout(1, (2, 3)), strides=(FixedStride(3),)),), layout(2, (6,), root=1)),
        (7, OpCode.RESHAPE_VIEW, ViewAttrs(shape=(Const(6),)), (dataclasses.replace(layout(1, (2, 3)), storage_offset=True),), layout(2, (6,), root=1)),
        (7, OpCode.RESHAPE_VIEW, ViewAttrs(shape=(Const(6),)), (dataclasses.replace(layout(1, (2, 3)), alias_root=0),), layout(2, (6,), root=1)),
        (7, OpCode.RESHAPE_VIEW, ViewAttrs(shape=(Const(6),)), (dataclasses.replace(layout(1, (2, 3)), access=1),), layout(2, (6,), root=1)),
    ],
)
def test_shared_layout_contract_validates_complete_nested_input_before_indexing(op_id, opcode, attrs, operands, result):
    with pytest.raises(MeshIrError):
        verifier.verify_operation_layout_contract(op_id, opcode, attrs, operands, result)


@pytest.mark.parametrize("dtype", [DType.FP16, DType.BF16])
def test_matrix_semantic_and_accumulation_results_are_distinct(dtype):
    lhs = tensor((2, 3), dtype)
    rhs = tensor((4, 3), dtype)
    attrs = MatmulAttrs(rhs_transpose=True, alpha=0.5, accum_dtype=DType.FP32)
    check(OpCode.MATMUL, attrs, (lhs, rhs), tensor((2, 4), dtype))
    check(
        OpCode.MATMUL,
        attrs,
        (lhs, rhs),
        tensor((2, 4), DType.FP32),
        verifier.MatrixResultForm.ACCUMULATION,
    )
    rejects(OpCode.MATMUL, attrs, (lhs, rhs), tensor((2, 4), DType.FP32), "E_EXPORT_DTYPE")
    rejects(
        OpCode.MATMUL,
        attrs,
        (lhs, rhs),
        tensor((2, 4), dtype),
        "E_EXPORT_DTYPE",
        verifier.MatrixResultForm.ACCUMULATION,
    )


def test_transposed_bmm_and_linear_bias_preserve_full_matrix_semantics():
    bmm_attrs = MatmulAttrs(batch_axes=(0,), lhs_transpose=True, rhs_transpose=True)
    check(
        OpCode.BMM,
        bmm_attrs,
        (tensor((2, 3, 5)), tensor((2, 4, 3))),
        tensor((2, 5, 4)),
    )
    linear_attrs = MatmulAttrs(rhs_transpose=True, alpha=0.5, beta=0.25)
    check(
        OpCode.LINEAR_BIAS,
        linear_attrs,
        (tensor((2, 3)), tensor((4, 3)), tensor((4,))),
        tensor((2, 4)),
    )
    rejects(
        OpCode.LINEAR_BIAS,
        linear_attrs,
        (tensor((2, 3)), tensor((4, 3)), tensor((4,))),
        tensor((2, 4)),
        "E_EXPORT_UNSUPPORTED_OP",
        verifier.MatrixResultForm.ACCUMULATION,
    )


def test_linear_bias_accumulation_form_validates_only_the_matrix_phase():
    attrs = MatmulAttrs(rhs_transpose=True, alpha=0.5, beta=0.25, accum_dtype=DType.FP32)
    lhs = tensor((2, 3), DType.FP16)
    rhs = tensor((4, 3), DType.FP16)
    bias = tensor((4,), DType.FP16)
    check(
        OpCode.LINEAR_BIAS,
        attrs,
        (lhs, rhs),
        tensor((2, 4), DType.FP32),
        verifier.MatrixResultForm.ACCUMULATION,
    )
    rejects(
        OpCode.LINEAR_BIAS,
        attrs,
        (lhs, rhs, bias),
        tensor((2, 4), DType.FP32),
        "E_EXPORT_UNSUPPORTED_OP",
        verifier.MatrixResultForm.ACCUMULATION,
    )
    rejects(
        OpCode.LINEAR_BIAS,
        attrs,
        (lhs, rhs),
        tensor((2, 4), DType.FP16),
        "E_EXPORT_DTYPE",
        verifier.MatrixResultForm.ACCUMULATION,
    )
    rejects(OpCode.LINEAR_BIAS, attrs, (lhs, rhs), tensor((2, 4), DType.FP16), "E_EXPORT_UNSUPPORTED_OP")
    rejects(
        OpCode.MATMUL,
        MatmulAttrs(beta=0.25, accum_dtype=DType.FP32),
        (lhs, tensor((3, 4), DType.FP16)),
        tensor((2, 4), DType.FP32),
        "E_EXPORT_UNSUPPORTED_OP",
        verifier.MatrixResultForm.ACCUMULATION,
    )


def test_elementwise_broadcast_scalar_unary_and_zero_extent_contracts():
    check(
        OpCode.ADD,
        ElementwiseAttrs(),
        (tensor((2, 1, 4)), tensor((1, 3, 4))),
        tensor((2, 3, 4)),
    )
    check(OpCode.ADD, ElementwiseAttrs(0, "rhs", 2.0), (tensor((0, 4)),), tensor((0, 4)))
    check(OpCode.GELU, ElementwiseAttrs(approximation="tanh"), (tensor(()),), tensor(()))


def test_movement_embedding_and_copy_contracts():
    check(
        OpCode.CONCAT,
        MovementAttrs(1, True),
        (tensor((2, 3)), tensor((2, 4))),
        tensor((2, 7)),
    )
    check(
        OpCode.GATHER_ROWS,
        MovementAttrs(1, True),
        (tensor((2, 4)), tensor((3,), DType.INT32)),
        tensor((2, 3)),
    )
    check(
        OpCode.EMBEDDING_LOOKUP,
        EmbeddingAttrs(-1, False, False),
        (tensor((10, 8)), tensor((2, 3), DType.INT32)),
        tensor((2, 3, 8)),
    )
    check(OpCode.CONTIGUOUS_COPY, ViewAttrs(shape=(Const(2), Const(3))), (tensor((2, 3)),), tensor((2, 3)))


def test_reduction_softmax_and_normalization_contracts():
    check(
        OpCode.REDUCE_SUM,
        ReduceAttrs((1,), True, DType.FP32, DType.FP32),
        (tensor((2, 3), DType.FP16),),
        tensor((2, 1), DType.FP32),
    )
    check(
        OpCode.REDUCE_MAX,
        ReduceAttrs((0,), False, DType.BF16, DType.FP32),
        (tensor((2, 3), DType.BF16),),
        tensor((3,), DType.BF16),
    )
    check(
        OpCode.REDUCE_MEAN,
        ReduceAttrs((0, 1), False, DType.FP32, DType.FP32),
        (tensor((2, 3)),),
        tensor(()),
    )
    check(OpCode.SOFTMAX, SoftmaxAttrs(1, DType.FP32, True), (tensor((2, 3), DType.FP16),), tensor((2, 3)))
    check(
        OpCode.LAYERNORM,
        NormAttrs((1, 2), 1e-5, True, True),
        (tensor((2, 3, 4)), tensor((3, 4)), tensor((3, 4))),
        tensor((2, 3, 4)),
    )
    check(
        OpCode.RMSNORM,
        NormAttrs((2,), 1e-5, True, False),
        (tensor((2, 3, 4)), tensor((4,))),
        tensor((2, 3, 4)),
    )


def test_symbolic_shapes_use_the_existing_dimension_equivalence():
    batch = Symbol(1, "batch", 1, 8)
    check(
        OpCode.MATMUL,
        MatmulAttrs(batch_axes=(0,)),
        (tensor((batch, 2, 3)), tensor((batch, 3, 4))),
        tensor((batch, 2, 4)),
    )


def test_matrix_rejects_bad_contraction_batch_shape_dtype_and_accumulation():
    attrs = MatmulAttrs(accum_dtype=DType.FP32)
    rejects(OpCode.MATMUL, attrs, (tensor((2, 3)), tensor((4, 5))), tensor((2, 5)), "E_EXPORT_LAYOUT")
    rejects(
        OpCode.BMM,
        MatmulAttrs(batch_axes=(0,)),
        (tensor((2, 3, 4)), tensor((3, 4, 5))),
        tensor((2, 3, 5)),
        "E_EXPORT_LAYOUT",
    )
    rejects(OpCode.MATMUL, attrs, (tensor((2, 3)), tensor((3, 4))), tensor((2, 5)), "E_EXPORT_LAYOUT")
    rejects(OpCode.MATMUL, attrs, (tensor((2, 3)), tensor((3, 4), DType.FP16)), tensor((2, 4)), "E_EXPORT_DTYPE")
    rejects(
        OpCode.MATMUL,
        MatmulAttrs(accum_dtype=DType.FP16),
        (tensor((2, 3), DType.FP16), tensor((3, 4), DType.FP16)),
        tensor((2, 4), DType.FP16),
        "E_EXPORT_DTYPE",
    )


def test_axes_result_and_semantic_option_failures_are_closed():
    rejects(OpCode.SOFTMAX, SoftmaxAttrs(2, DType.FP32), (tensor((2, 3)),), tensor((2, 3)), "E_EXPORT_LAYOUT")
    rejects(
        OpCode.LAYERNORM,
        NormAttrs((0,), 1e-5, False, False),
        (tensor((2, 3)),),
        tensor((2, 3)),
        "E_EXPORT_LAYOUT",
    )
    rejects(
        OpCode.REDUCE_SUM,
        ReduceAttrs((1,), False, DType.FP32, DType.FP32),
        (tensor((2, 3)),),
        tensor((2, 1)),
        "E_EXPORT_LAYOUT",
    )
    rejects(OpCode.RELU, ElementwiseAttrs(1, "rhs"), (tensor((2, 3)),), tensor((2, 3)), "E_EXPORT_UNSUPPORTED_OP")
    rejects(
        OpCode.RMSNORM,
        NormAttrs((1,), 1e-5, True, False),
        (tensor((2, 3)), tensor((2,))),
        tensor((2, 3)),
        "E_EXPORT_LAYOUT",
    )
    rejects(
        OpCode.GATHER_ROWS,
        MovementAttrs(1, True),
        (tensor((2, 4)), tensor((3,), DType.INT8)),
        tensor((2, 3)),
        "E_EXPORT_DTYPE",
    )
    rejects(
        OpCode.EMBEDDING_LOOKUP,
        EmbeddingAttrs(-1, False, False, 1.0),
        (tensor((10, 8)), tensor((2,), DType.INT32)),
        tensor((2, 8)),
        "E_EXPORT_UNSUPPORTED_OP",
    )
    rejects(
        OpCode.CONCAT,
        MovementAttrs(1, False),
        (tensor((2, 3)), tensor((2, 4))),
        tensor((2, 7)),
        "E_EXPORT_UNSUPPORTED_OP",
    )


def test_nonmatrix_accumulation_and_unsupported_views_reject():
    rejects(
        OpCode.RELU,
        ElementwiseAttrs(),
        (tensor((2, 3)),),
        tensor((2, 3)),
        "E_EXPORT_UNSUPPORTED_OP",
        verifier.MatrixResultForm.ACCUMULATION,
    )
    rejects(OpCode.RESHAPE_VIEW, ViewAttrs(shape=(Const(6),)), (tensor((2, 3)),), tensor((6,)), "E_EXPORT_UNSUPPORTED_OP")


@pytest.mark.parametrize(
    "op_id,opcode,attrs,operands,result,result_form",
    [
        (True, OpCode.RELU, ElementwiseAttrs(), (object(),), object(), None),
        (1, "RELU", ElementwiseAttrs(), (object(),), object(), None),
        (1, OpCode.RELU, ElementwiseAttrs(), [], object(), None),
        (1, OpCode.RELU, ElementwiseAttrs(), (object(),), object(), None),
        (1, OpCode.RELU, ElementwiseAttrs(), (None,), object(), None),
        (1, OpCode.RELU, ElementwiseAttrs(), (), verifier.TensorDataContract([Const(2)], DType.FP32), None),
        (1, OpCode.RELU, ElementwiseAttrs(), (), verifier.TensorDataContract((True,), DType.FP32), None),
        (1, OpCode.RELU, ElementwiseAttrs(), (), verifier.TensorDataContract((), 1), None),
        (1, OpCode.MATMUL, MatmulAttrs(batch_axes=[0]), (tensor((2, 3)), tensor((3, 4))), tensor((2, 4)), None),
        (1, OpCode.REDUCE_SUM, ReduceAttrs([0], False, DType.FP32, DType.FP32), (tensor((2,)),), tensor(()), None),
        (1, OpCode.RELU, dataclasses.replace(ElementwiseAttrs(), scalar_side=1), (), tensor(()), None),
        (1, OpCode.RELU, ElementwiseAttrs(), (tensor((2, 3)),), tensor((2, 3)), 1),
        (1, OpCode.RELU, ElementwiseAttrs(), (), tensor(()), "SEMANTIC"),
    ],
)
def test_public_boundary_rejects_wrong_nested_types_before_semantics(op_id, opcode, attrs, operands, result, result_form):
    options = {} if result_form is None else {"matrix_result_form": result_form}
    with pytest.raises(MeshIrError):
        verifier.verify_operation_data_contract(op_id, opcode, attrs, operands, result, **options)
