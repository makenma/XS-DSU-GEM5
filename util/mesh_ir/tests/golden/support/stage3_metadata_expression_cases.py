from dataclasses import dataclass, replace
from math import prod

from mesh_ir.architecture import ArchManifest, load_arch
from mesh_ir.canonical import U64_MAX, semantic_sha256
from mesh_ir.ir.common import Add, Const, FloorDivByConst, MulByConst, Symbol
from mesh_ir.ir.graph_ir import ViewAttrs
from mesh_ir.model import Program
from tests.golden.support.stage2_control_fixture import ROOT
from tests.golden.support.stage3_intrinsic_cases import (
    build_metadata_contract_program,
)


@dataclass(frozen=True)
class Stage3MetadataExpressionCase:
    case_id: str
    arch: ArchManifest
    baseline: Program
    program: Program
    expected_code: str | None


def _reshape_metadata_contract(
    program: Program,
    source_shape: tuple[int, ...],
    source_strides: tuple[int, ...],
    result_shape: tuple[int, ...],
    result_strides: tuple[int, ...],
    attrs: ViewAttrs,
) -> Program:
    source_logical_extent_bytes = prod(source_shape) * 2
    source_storage_extent_bytes = (
        0
        if 0 in source_shape
        else (
            1
            + sum(
                (extent - 1) * stride
                for extent, stride in zip(source_shape, source_strides)
            )
        )
        * 2
    )
    result_logical_extent_bytes = prod(result_shape) * 2
    result_storage_extent_bytes = (
        0
        if 0 in result_shape
        else (
            1
            + sum(
                (extent - 1) * stride
                for extent, stride in zip(result_shape, result_strides)
            )
        )
        * 2
    )
    semantics = replace(
        program.semantics,
        kernel_tensors=tuple(
            replace(
                item,
                shape=source_shape,
                strides=source_strides,
                logical_extent_bytes=source_logical_extent_bytes,
                storage_extent_bytes=source_storage_extent_bytes,
            )
            if item.tensor_id == 5
            else replace(
                item,
                shape=result_shape,
                strides=result_strides,
                logical_extent_bytes=result_logical_extent_bytes,
                storage_extent_bytes=result_storage_extent_bytes,
            )
            if item.tensor_id == 6
            else item
            for item in program.semantics.kernel_tensors
        ),
        logical_shards=tuple(
            replace(
                item,
                global_origin=(0,) * len(source_shape),
                padded_local_shape=source_shape,
                valid_shape=source_shape,
            )
            if item.shard_id == 5
            else replace(
                item,
                global_origin=(0,) * len(result_shape),
                padded_local_shape=result_shape,
                valid_shape=result_shape,
            )
            if item.shard_id == 6
            else item
            for item in program.semantics.logical_shards
        ),
        computations=tuple(
            replace(item, attrs=attrs)
            if item.computation_id == 3
            else item
            for item in program.semantics.computations
        ),
    )
    provisional = replace(program, semantics=semantics, semantic_sha256="")
    return replace(
        provisional,
        semantic_sha256=semantic_sha256(provisional.semantic_dict()),
    )


def build_stage3_metadata_expression_cases() -> tuple[Stage3MetadataExpressionCase, ...]:
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    baseline = build_metadata_contract_program(arch)
    const_zero = _reshape_metadata_contract(
        baseline,
        (0,),
        (1,),
        (0,),
        (1,),
        ViewAttrs(shape=(Const(0),)),
    )
    mul_const_zero = _reshape_metadata_contract(
        baseline,
        (0,),
        (1,),
        (0,),
        (1,),
        ViewAttrs(shape=(MulByConst(Const(1), 0),)),
    )
    floor_symbolic_zero = _reshape_metadata_contract(
        baseline,
        (0,),
        (1,),
        (0,),
        (1,),
        ViewAttrs(
            shape=(
                FloorDivByConst(
                    MulByConst(Symbol(1, "zero", 0, U64_MAX), 0),
                    1,
                ),
            )
        ),
    )
    no_feasible_symbolic_zero = _reshape_metadata_contract(
        baseline,
        (0,),
        (1,),
        (0,),
        (1,),
        ViewAttrs(
            shape=(
                FloorDivByConst(
                    MulByConst(Symbol(1, "zero", 1, 1, 2), 0),
                    1,
                ),
            )
        ),
    )
    wide_symbol_id_symbolic_zero = _reshape_metadata_contract(
        baseline,
        (0,),
        (1,),
        (0,),
        (1,),
        ViewAttrs(
            shape=(
                FloorDivByConst(
                    MulByConst(Symbol(1 << 32, "zero", 0, 1), 0),
                    1,
                ),
            )
        ),
    )
    u64_reduced_two = _reshape_metadata_contract(
        baseline,
        (1, 2),
        (2, 1),
        (2,),
        (1,),
        ViewAttrs(
            shape=(
                FloorDivByConst(
                    Add(Const(U64_MAX), Const(U64_MAX)),
                    U64_MAX,
                ),
            )
        ),
    )
    return (
        Stage3MetadataExpressionCase(
            "metadata_const_zero_shape_positive",
            arch,
            baseline,
            const_zero,
            None,
        ),
        Stage3MetadataExpressionCase(
            "metadata_mul_const_zero_is_not_concrete_zero",
            arch,
            baseline,
            mul_const_zero,
            "E_EXPORT_LAYOUT",
        ),
        Stage3MetadataExpressionCase(
            "metadata_floor_symbolic_zero_is_concrete_zero",
            arch,
            baseline,
            floor_symbolic_zero,
            None,
        ),
        Stage3MetadataExpressionCase(
            "metadata_symbol_has_no_feasible_multiple",
            arch,
            baseline,
            no_feasible_symbolic_zero,
            "E_CONFIG",
        ),
        Stage3MetadataExpressionCase(
            "metadata_symbol_id_exceeds_u32",
            arch,
            baseline,
            wide_symbol_id_symbolic_zero,
            "E_CONFIG",
        ),
        Stage3MetadataExpressionCase(
            "metadata_full_u64_expression_reduces_to_two",
            arch,
            baseline,
            u64_reduced_two,
            None,
        ),
    )
