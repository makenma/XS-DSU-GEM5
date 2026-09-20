import dataclasses
import subprocess
from pathlib import Path

import pytest

from mesh_ir.abi.encoder import encode_program
from mesh_ir.architecture import load_arch
from mesh_ir.canonical import U64_MAX, semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.golden_programs import (
    build_compute_timing_program,
    build_single_core_program,
    build_zero_dma_program,
)
from mesh_ir.scheduled.addressing import derive_descriptor_execution_set
from mesh_ir.scheduled.assemble import _expected_traffic, assemble_program
from mesh_ir.scheduled.verify import verify_pretraffic_state, verify_program
from tests.golden.test_mutation_corpus import _cpp_verdict
from mesh_ir.traffic import calculate_traffic
from tests.golden.support.stage3_geometry_cases import build_stage3_geometry_cases
from tests.golden.support.stage3_metadata_expression_cases import (
    build_stage3_metadata_expression_cases,
)
from tests.golden.support.stage3_intrinsic_cases import (
    Stage3IntrinsicCase,
    build_stage3_intrinsic_cases,
)
from tests.unit.test_gate2_scheduled_dma import _state, _with_descriptor_pieces


ROOT = Path(__file__).resolve().parents[4]


GEOMETRY_CASES = (
    ("compiler_empty_softmax_positive", "ACCEPTED"),
    ("intrinsic_scalar_positive", "ACCEPTED"),
    ("intrinsic_strided_store_positive", "ACCEPTED"),
    ("intrinsic_local_copy_positive", "ACCEPTED"),
    ("intrinsic_partial_hole", "ACCEPTED"),
    ("intrinsic_noninjective_write", "E_EXPORT_LAYOUT"),
    ("geometry_singleton_u64_stride_positive", "ACCEPTED"),
    ("geometry_broadcast_read_positive", "ACCEPTED"),
    ("geometry_broadcast_read_whole_program_rejection", "ACCEPTED"),
)


GEOMETRY_FACT_CASES = (
    ("zero_dma_positive", "empty"),
    ("intrinsic_scalar_positive", "scalar"),
    ("intrinsic_strided_store_positive", "strided"),
    ("geometry_broadcast_read_positive", "broadcast"),
    ("geometry_broadcast_read_whole_program_rejection", "broadcast_many_to_one"),
    ("geometry_singleton_u64_stride_positive", "singleton"),
    ("zero_dma_positive", "byte_count_empty"),
    ("intrinsic_scalar_positive", "byte_count_scalar"),
    ("intrinsic_strided_store_positive", "byte_count_strided"),
    (
        "geometry_broadcast_read_whole_program_rejection",
        "byte_count_noninjective",
    ),
    ("zero_dma_positive", "injective_empty"),
    ("intrinsic_scalar_positive", "injective"),
    ("intrinsic_strided_store_positive", "injective"),
    ("geometry_broadcast_read_whole_program_rejection", "noninjective"),
)


@pytest.fixture(scope="module")
def complete_geometry_cases():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    zero_dma = build_zero_dma_program(arch)
    return {
        case.case_id: case
        for case in (
            *build_stage3_intrinsic_cases(),
            *build_stage3_geometry_cases(),
            Stage3IntrinsicCase(
                "zero_dma_positive", arch, zero_dma, zero_dma, None
            ),
        )
    }


def _run(probe, mode, *arguments):
    result = subprocess.run(
        [str(probe), mode, *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def _run_program(probe, tmp_path, program):
    path = tmp_path / "program.mshb"
    path.write_bytes(encode_program(program))
    return _run(probe, "file", str(path))


@pytest.mark.parametrize("mode", ("single", "dual", "repeat"))
def test_cpp_verifier_accepts_complete_authored_programs(verifier_probe, mode):
    assert _run(verifier_probe, mode) == "ACCEPTED"


@pytest.mark.parametrize(
    "mode",
    (
        "facts_geometry_basic",
        "facts_geometry_default_moved",
        "facts_geometry_foreign_retained",
        "facts_geometry_atomic",
    ),
)
def test_cpp_geometry_facts_are_program_bound_and_exact(
    verifier_probe, tmp_path, mode
):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    program = build_single_core_program(arch)
    path = tmp_path / "geometry.mshb"
    path.write_bytes(encode_program(program))
    assert _run(verifier_probe, mode, str(path)) == "GEOMETRY:OK"


@pytest.mark.parametrize(
    "mode",
    (
        "facts_backing_basic",
        "facts_backing_default_moved",
        "facts_backing_generation",
        "facts_backing_slot_requirement",
    ),
)
def test_cpp_backing_facts_are_bound_and_atomic(verifier_probe, tmp_path, mode):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    path = tmp_path / "backing.mshb"
    path.write_bytes(encode_program(build_single_core_program(arch)))
    assert _run(verifier_probe, mode, str(path)) == "BACKING:OK"


def test_cpp_matrix_facts_bind_the_geometry_generation(verifier_probe, tmp_path):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    path = tmp_path / "matrix-facts.mshb"
    path.write_bytes(encode_program(build_compute_timing_program(arch)))
    assert _run(verifier_probe, "facts_matrix_generation", str(path)) == (
        "MATRIX:OK"
    )


def test_cpp_computation_rejects_an_invalid_gemm_attributes_reference(
    verifier_probe, tmp_path
):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    path = tmp_path / "compute-invalid-attrs.mshb"
    path.write_bytes(encode_program(build_compute_timing_program(arch)))
    assert _run(verifier_probe, "facts_computation_invalid_attrs", str(path)) == (
        "COMPUTATION:E_ABI_BOUNDS"
    )


@pytest.mark.parametrize(
    ("case_id", "expected"),
    GEOMETRY_CASES,
)
def test_cpp_geometry_directly_classifies_complete_regions(
    verifier_probe, tmp_path, complete_geometry_cases, case_id, expected
):
    case = complete_geometry_cases[case_id]
    assert case.program.semantic_sha256 == semantic_sha256(
        case.program.semantic_dict()
    )
    path = tmp_path / f"{case.case_id}.mshb"
    path.write_bytes(encode_program(case.program))
    assert _run(verifier_probe, "facts_geometry_file", str(path)) == (
        f"GEOMETRY:{expected}"
    )


@pytest.mark.parametrize(
    ("case_id", "kind"),
    GEOMETRY_FACT_CASES,
)
def test_cpp_geometry_direct_facts_preserve_region_semantics(
    verifier_probe, tmp_path, complete_geometry_cases, case_id, kind
):
    case = complete_geometry_cases[case_id]
    path = tmp_path / f"{case.case_id}.mshb"
    path.write_bytes(encode_program(case.program))
    assert _run(verifier_probe, "facts_geometry_kind", str(path), kind) == (
        "GEOMETRY:OK"
    )


def test_cpp_geometry_many_to_one_read_remains_a_dma_domain_failure(
    driver, tmp_path, complete_geometry_cases
):
    case = complete_geometry_cases[
        "geometry_broadcast_read_whole_program_rejection"
    ]
    assert case.whole_program_code == "E_DMA_RANGE"
    assert _cpp_verdict(
        encode_program(case.program), driver, tmp_path, case.arch
    ) == "VERIFY:E_DMA_RANGE"


def test_cpp_metadata_layout_replaces_a_seeded_error(verifier_probe, tmp_path):
    case = next(
        item
        for item in build_stage3_metadata_expression_cases()
        if item.case_id == "metadata_mul_const_zero_is_not_concrete_zero"
    )
    path = tmp_path / "metadata-layout.mshb"
    path.write_bytes(encode_program(case.program))
    assert _run(verifier_probe, "facts_metadata_seeded_layout", str(path)).startswith(
        "LOGICAL:E_EXPORT_LAYOUT:"
    )


@pytest.mark.parametrize(
    "mode",
    (
        "facts_geometry_piece",
        "facts_geometry_piece_invalid",
        "facts_geometry_piece_lifetime",
    ),
)
def test_cpp_geometry_piece_projection_is_bound_and_retained(
    verifier_probe, tmp_path, mode
):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    path = tmp_path / "zero-dma.mshb"
    path.write_bytes(encode_program(build_zero_dma_program(arch)))
    assert _run(verifier_probe, mode, str(path)) == "GEOMETRY:OK"


def test_cpp_geometry_piece_projection_keeps_raw_empty_stride_overflow(
    verifier_probe, tmp_path
):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    source = build_zero_dma_program(arch)
    zero_bytes = next(
        operation
        for operation in source.semantics.kernel_ops
        if operation.stable_key == "load:zero_bytes"
    )
    provisional = dataclasses.replace(
        source,
        semantics=dataclasses.replace(
            source.semantics,
            views=tuple(
                dataclasses.replace(
                    view, object_strides=(U64_MAX, 1), layout=None
                )
                if view.view_id == zero_bytes.reads[0].view_id
                else view
                for view in source.semantics.views
            ),
        ),
        semantic_sha256="",
    )
    program = dataclasses.replace(
        provisional,
        semantic_sha256=semantic_sha256(provisional.semantic_dict()),
    )
    with pytest.raises(MeshIrError) as caught:
        verify_program(program, arch)
    assert caught.value.code == "E_ABI_OVERFLOW"
    path = tmp_path / "zero-dma-raw-stride-overflow.mshb"
    path.write_bytes(encode_program(program))
    assert _run(verifier_probe, "facts_geometry_piece", str(path)) == (
        "GEOMETRY:E_ABI_OVERFLOW"
    )


@pytest.mark.parametrize(
    ("mode", "code"),
    (
        ("arch", "E_ARCH_DIGEST"),
        ("major", "E_ABI_VERSION"),
        ("minor", "E_ABI_VERSION"),
        ("feature", "E_ABI_VERSION"),
        ("minimum", "E_ABI_VERSION"),
        ("checksum", "E_ABI_CHECKSUM"),
    ),
)
def test_cpp_verifier_revalidates_mutable_program(verifier_probe, mode, code):
    assert _run(verifier_probe, mode).startswith(f"VERIFY:{code}:")


@pytest.mark.parametrize(
    ("mode", "code"),
    (
        ("group_command", "E_DMA_RANGE"),
        ("group_completion", "E_DMA_RANGE"),
        ("group_duplicate", "E_ABI_DUPLICATE"),
        ("descriptor_completion", "E_DMA_RANGE"),
    ),
)
def test_cpp_verifier_checks_descriptor_group_ownership(
    verifier_probe, mode, code
):
    assert _run(verifier_probe, mode).startswith(f"VERIFY:{code}:")


def test_cpp_context_admits_duplicate_command_identity_before_streams(
    verifier_probe,
):
    assert _run(verifier_probe, "duplicate_command").startswith(
        "VERIFY:E_ABI_DUPLICATE:"
    )


def test_cpp_context_checks_duplicate_commands_before_dense_order(
    verifier_probe,
):
    assert _run(verifier_probe, "duplicate_command_after_order").startswith(
        "VERIFY:E_ABI_DUPLICATE:"
    )


def test_cpp_context_rejects_reordered_unique_commands(verifier_probe):
    assert _run(verifier_probe, "reordered_command").startswith(
        "VERIFY:E_ABI_ORDER:"
    )


@pytest.mark.parametrize(
    ("mode", "code"),
    (
        ("descriptor_missing", "E_ABI_BOUNDS"),
        ("descriptor_append", "E_ABI_BOUNDS"),
        ("allocation_zero", "E_ABI_ORDER"),
        ("allocation_duplicate", "E_ABI_ORDER"),
        ("shard_zero", "E_ABI_BOUNDS"),
        ("shard_duplicate", "E_ABI_BOUNDS"),
        ("event_zero", "E_ABI_ORDER"),
        ("event_duplicate", "E_ABI_ORDER"),
        ("resident_runtime_shard_zero", "E_ABI_ORDER"),
        ("resident_runtime_shard_duplicate", "E_ABI_ORDER"),
    ),
)
def test_cpp_context_matches_transport_identity_admission(
    verifier_probe, mode, code
):
    assert _run(verifier_probe, mode).startswith(f"VERIFY:{code}:")


@pytest.mark.parametrize(
    "mode",
    ("variant_entrypoint_zero", "variant_profile_zero", "variant_lifecycle_zero"),
)
def test_cpp_context_rejects_variant_identity_and_lineage_bounds(
    verifier_probe, mode
):
    assert _run(verifier_probe, mode).startswith("VERIFY:E_ABI_BOUNDS:")


def test_cpp_context_rejects_empty_authored_lineage_identity(verifier_probe):
    assert _run(verifier_probe, "authored_lineage_empty").startswith(
        "CONTEXT:E_ABI_BOUNDS:"
    )


@pytest.mark.parametrize(
    "mode",
    ("object_backing_duplicate", "resident_view_duplicate", "binding_slot_duplicate"),
)
def test_cpp_context_classifies_joined_root_duplicates_as_bounds(
    verifier_probe, mode
):
    assert _run(verifier_probe, mode).startswith("VERIFY:E_ABI_BOUNDS:")


def test_cpp_context_failure_keeps_its_previous_complete_program(
    verifier_probe,
):
    assert _run(verifier_probe, "context_atomic").startswith(
        "CONTEXT:E_ABI_ORDER:"
    )


def test_cpp_verifier_accepts_multi_descriptor_group(verifier_probe, tmp_path):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    base = assemble_program(verify_pretraffic_state(_state(arch), arch), arch)
    program = _with_descriptor_pieces(base, arch, (0, 2))
    path = tmp_path / "multi-descriptor.mshb"
    path.write_bytes(encode_program(program))
    result = subprocess.run(
        [str(verifier_probe), "file", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "ACCEPTED"


def test_cpp_verifier_resolves_external_relocation_for_burst_plan(
    verifier_probe, tmp_path
):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    program = build_single_core_program(arch)
    offset = 0x300D60
    slots = tuple(
        dataclasses.replace(
            slot,
            reference_binding=dataclasses.replace(
                slot.reference_binding, allocation_offset_bytes=offset
            ),
        )
        if slot.slot_id == 3
        else slot
        for slot in program.semantics.binding_slots
    )
    relocations = tuple(
        dataclasses.replace(relocation, offset_bytes=offset)
        if relocation.tensor_id == 3
        else relocation
        for relocation in program.relocations
    )
    semantics = dataclasses.replace(program.semantics, binding_slots=slots)
    program = dataclasses.replace(
        program, relocations=relocations, semantics=semantics
    )
    executions, identity = derive_descriptor_execution_set(program, arch)
    report = calculate_traffic(arch, identity, executions)
    semantics = dataclasses.replace(
        semantics,
        reference_binding_identity_sha256=identity,
        intrinsic_traffic=report,
    )
    program = dataclasses.replace(
        program,
        semantics=semantics,
        expected_traffic=_expected_traffic(report, executions, arch),
        semantic_sha256="",
    )
    program = dataclasses.replace(
        program, semantic_sha256=semantic_sha256(program.semantic_dict())
    )
    path = tmp_path / "relocated-burst-plan.mshb"
    path.write_bytes(encode_program(program))

    assert program.expected_traffic[-1].bursts == 17
    result = subprocess.run(
        [str(verifier_probe), "file", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "ACCEPTED"


@pytest.mark.parametrize(
    ("opcode", "field"),
    (
        ("VECTOR", "unit"),
        ("VECTOR", "dtype"),
        ("LOCAL_REDUCE", "unit"),
        ("LOCAL_REDUCE", "dtype"),
        ("MATRIX_EPILOGUE", "unit"),
        ("MATRIX_EPILOGUE", "dtype"),
    ),
)
def test_cpp_verifier_checks_exact_nonmatrix_work_projection(
    verifier_probe, tmp_path, opcode, field
):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    program = build_compute_timing_program(arch)
    operation = next(
        item for item in program.semantics.kernel_ops
        if item.opcode.name == opcode
    )
    command_index = next(
        index
        for index, item in enumerate(program.semantics.command_semantics)
        if getattr(item.source, "kernel_op_id", None) == operation.op_id
    )
    commands = list(program.semantics.command_semantics)
    semantic = commands[command_index]
    phases = list(semantic.execution.phases)
    work = list(phases[0].work)
    value = work[0]
    replacement = (
        type(value.unit).MAC
        if field == "unit"
        else next(item for item in type(value.dtype) if item is not value.dtype)
    )
    work[0] = dataclasses.replace(value, **{field: replacement})
    phases[0] = dataclasses.replace(phases[0], work=tuple(work))
    commands[command_index] = dataclasses.replace(
        semantic,
        execution=dataclasses.replace(
            semantic.execution,
            phases=tuple(phases),
        ),
    )
    changed = dataclasses.replace(
        program,
        semantics=dataclasses.replace(
            program.semantics,
            command_semantics=tuple(commands),
        ),
    )
    changed = dataclasses.replace(
        changed,
        semantic_sha256=semantic_sha256(changed.semantic_dict()),
    )

    assert _run_program(verifier_probe, tmp_path, changed).startswith(
        "VERIFY:E_ABI_ENUM:"
    )


def test_cpp_verifier_accepts_complete_compute_timing_projection(
    verifier_probe, tmp_path
):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    assert _run_program(
        verifier_probe,
        tmp_path,
        build_compute_timing_program(arch),
    ) == "ACCEPTED"
