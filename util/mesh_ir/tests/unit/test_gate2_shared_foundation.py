import pytest

from mesh_ir.canonical import U64_MAX, checked_add_u64, checked_mul_u64
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.ir.common import DmaKind, Engine, INVALID_CORE_ID, Layout, MemorySpace, StorageClass


def test_shared_enums_reuse_generated_abi_values_and_names():
    assert {item.name: item.value for item in Layout} == {
        name: getattr(A.LAYOUT_KIND, name)
        for name in ("CONTIGUOUS_ROW_MAJOR", "TRANSPOSED_2D_VIEW", "BLOCKED_MNK")
    }
    assert {item.name: item.value for item in StorageClass} == {
        name: getattr(A.STORAGE_CLASS, name)
        for name in ("EXTERNAL", "HBM", "HOST_SHARED", "CORE_SRAM", "PRE_RESIDENT")
    }
    assert {item.name: item.value for item in MemorySpace} == {
        name: getattr(A.MEMORY_SPACE, name)
        for name in ("HBM", "HOST_SHARED", "CORE_SRAM", "PEER_SRAM")
    }
    assert {item.name: item.value for item in DmaKind} == {
        name: getattr(A.DMA_KIND, name)
        for name in ("LOAD", "STORE", "P2P_PUSH", "PREFETCH", "LOCAL_FILL")
    }
    assert {item.name: item.value for item in Engine} == {
        name: getattr(A.ENGINE, name)
        for name in ("CONTROL", "DMA_READ", "DMA_WRITE", "TENSOR", "VECTOR", "REDUCE")
    }
    assert INVALID_CORE_ID == 0xFFFF


@pytest.mark.parametrize(
    "function,args,expected",
    (
        (checked_add_u64, (0, U64_MAX), U64_MAX),
        (checked_add_u64, (17, 25), 42),
        (checked_mul_u64, (0, U64_MAX), 0),
        (checked_mul_u64, (6, 7), 42),
    ),
)
def test_checked_u64_arithmetic_accepts_exact_boundaries(function, args, expected):
    assert function(*args, field="extent") == expected


@pytest.mark.parametrize(
    "function,args",
    (
        (checked_add_u64, (U64_MAX, 1)),
        (checked_mul_u64, (U64_MAX, 2)),
    ),
)
def test_checked_u64_arithmetic_reports_computed_overflow(function, args):
    with pytest.raises(MeshIrError) as error:
        function(*args, field="extent")
    assert error.value.code == "E_ABI_OVERFLOW"
    assert error.value.context == {"field": "extent", "left": args[0], "right": args[1]}


@pytest.mark.parametrize(
    "function,args",
    (
        (checked_add_u64, (True, 1)),
        (checked_add_u64, (-1, 1)),
        (checked_mul_u64, (1.0, 2)),
        (checked_mul_u64, (1, U64_MAX + 1)),
    ),
)
def test_checked_u64_arithmetic_preserves_malformed_input_diagnostic(function, args):
    with pytest.raises(MeshIrError) as error:
        function(*args, field="extent")
    assert error.value.code == "E_CONFIG"
