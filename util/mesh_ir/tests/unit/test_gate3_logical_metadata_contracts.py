import pytest

from mesh_ir.canonical import U64_MAX
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Access, Add, CeilDivByConst, Const, DType, FixedStride, FloorDivByConst, Symbol
from mesh_ir.ir.graph_ir import EmbeddingAttrs, OpCode, ViewAttrs
from mesh_ir.ir.graph_verify import TensorDataContract, TensorLayoutContract, verify_operation_data_contract, verify_operation_layout_contract


def _layout(value_id, shape, strides, root=1):
    return TensorLayoutContract(
        tuple(shape),
        DType.FP16,
        value_id,
        tuple(FixedStride(stride) for stride in strides),
        0,
        root,
        Access.READ_ONLY,
    )


def test_fixed_symbol_shape_is_not_interchangeable_with_a_constant():
    with pytest.raises(MeshIrError) as error:
        verify_operation_layout_contract(
            1,
            OpCode.RESHAPE_VIEW,
            ViewAttrs(shape=(Const(6),)),
            (_layout(1, (Symbol(1, "fixed", 2, 2), Const(3)), (3, 1)),),
            _layout(2, (Const(6),), (1,)),
        )
    assert error.value.code == "E_EXPORT_LAYOUT"


def test_overflow_reduced_dimension_remains_a_valid_metadata_shape():
    reduced = FloorDivByConst(Add(Const(U64_MAX), Const(U64_MAX)), U64_MAX)
    verify_operation_layout_contract(
        1,
        OpCode.RESHAPE_VIEW,
        ViewAttrs(shape=(Const(2),)),
        (_layout(1, (reduced,), (1,)),),
        _layout(2, (Const(2),), (1,)),
    )


@pytest.mark.parametrize(
    "attrs,result",
    (
        (
            ViewAttrs(axes=(0,), starts=(0,), ends=(U64_MAX,), steps=(1,)),
            _layout(
                2,
                (CeilDivByConst(Symbol(1, "extent", 0, U64_MAX), 1),),
                (1,),
            ),
        ),
        (
            ViewAttrs(
                axes=(0,), starts=(0,), ends=(U64_MAX,), steps=(U64_MAX,)
            ),
            _layout(
                2,
                (CeilDivByConst(Symbol(1, "extent", 0, U64_MAX), U64_MAX),),
                (U64_MAX,),
            ),
        ),
    ),
    ids=("u64_extent", "u64_step"),
)
def test_slice_metadata_accepts_full_u64_extent_and_step(attrs, result):
    verify_operation_layout_contract(
        1,
        OpCode.SLICE_VIEW,
        attrs,
        (_layout(1, (Symbol(1, "extent", 0, U64_MAX),), (1,)),),
        result,
    )


def test_embedding_accepts_full_u64_row_count():
    verify_operation_data_contract(
        1,
        OpCode.EMBEDDING_LOOKUP,
        EmbeddingAttrs(-1, False, False),
        (
            TensorDataContract((Const(U64_MAX), Const(1)), DType.FP16),
            TensorDataContract((), DType.INT32),
        ),
        TensorDataContract((Const(1),), DType.FP16),
    )
