from dataclasses import replace
from pathlib import Path
import pytest

from mesh_ir.abi.encoder import encode_program
from mesh_ir.architecture import load_arch
from mesh_ir.canonical import semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.golden_programs import (
    build_barrier_e2e_program,
    build_compute_timing_program,
    build_dual_core_program,
    build_zero_dma_program,
)
from mesh_ir.ir.kernel_ir import KernelMemoryRecords
from mesh_ir.scheduled.model import HaltAttrs, IdSpan, RequestBeginAttrs, RequestEndAttrs
from mesh_ir.scheduled.verify import verify_program
from tests.unit.test_gate3_cpp_compute_verifier import compiler_compute_program
from tests.unit.test_gate2_empty_dma_geometry import _reauthor, _records
from tests.golden.test_mutation_corpus import _cpp_verdict


ROOT = Path(__file__).resolve().parents[4]


TARGETS = A.VARIANT_MEMBERSHIP_TARGETS
KERNEL_TENSOR_TARGET = next(
    target for target in TARGETS if target[0] == "kernel_tensors"
)


def _target_population(program, target):
    _, source, program_field = target
    owner = program if source == "transport" else program.semantics
    return len(getattr(owner, program_field))


def _replace_first_membership_span(program, field, span):
    first, *remaining = program.semantics.variants
    changed = replace(
        first,
        membership=replace(first.membership, **{field: span}),
    )
    provisional = replace(
        program,
        semantics=replace(program.semantics, variants=(changed, *remaining)),
        semantic_sha256="",
    )
    return replace(
        provisional,
        semantic_sha256=semantic_sha256(provisional.semantic_dict()),
    )


def _nonempty_fixture(programs, target):
    field, _, _ = target
    for name, arch, program in programs:
        span = getattr(program.semantics.variants[0].membership, field)
        if _target_population(program, target) and span.count:
            return name, arch, program, span
    raise AssertionError(f"no complete fixture populates {field}")


def _python_verdict(program, arch):
    try:
        verify_program(program, arch)
    except MeshIrError as error:
        return f"VERIFY:{error.code}"
    return "ACCEPTED"


@pytest.fixture(scope="module")
def partition_programs(compiler_compute_program):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    compiler_arch, compiler_program = compiler_compute_program
    return (
        ("compute", arch, build_compute_timing_program(arch)),
        ("dual", arch, build_dual_core_program(arch)),
        ("barrier", arch, build_barrier_e2e_program(arch)),
        ("zero_dma", arch, build_zero_dma_program(arch)),
        ("compiler", compiler_arch, compiler_program),
    )


@pytest.fixture(scope="module")
def nonempty_then_empty_variant_program():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    source = build_compute_timing_program(arch)
    builder = _reauthor(arch, source, _records(source))
    variant = builder.variant(
        "main",
        "empty-control",
        "empty-dma-geometry:control-only",
        records=KernelMemoryRecords((), (), (), (), (), (), (), (), (), ()),
        allocations=(),
    )
    stream = variant.stream(
        arch.core_ids[0],
        0,
        A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL,
    )
    stream.control_command(RequestBeginAttrs())
    stream.control_command(RequestEndAttrs())
    stream.control_command(HaltAttrs())
    return arch, builder.build()


def test_generated_membership_target_relation_has_all_context_targets():
    assert len(TARGETS) == 24
    assert tuple(item[0] for item in TARGETS) == A.VARIANT_MEMBERSHIP_FIELDS
    assert len({item[0] for item in TARGETS}) == len(TARGETS)


@pytest.mark.parametrize("target", TARGETS, ids=lambda item: item[0])
def test_python_partition_gap_rejects_each_nonempty_generated_target(
    partition_programs, target
):
    field, _, _ = target
    _, arch, program, span = _nonempty_fixture(partition_programs, target)
    candidate = _replace_first_membership_span(
        program,
        field,
        IdSpan(span.first_id, span.count - 1),
    )

    assert encode_program(candidate) != encode_program(program)
    assert _python_verdict(candidate, arch) == "VERIFY:E_ABI_BOUNDS"


@pytest.mark.parametrize("target", TARGETS, ids=lambda item: item[0])
def test_python_partition_bounds_reject_each_generated_target(
    partition_programs, target
):
    field, _, _ = target
    _, arch, program, span = _nonempty_fixture(partition_programs, target)
    candidate = _replace_first_membership_span(
        program,
        field,
        IdSpan(span.first_id, span.count + 1),
    )

    assert encode_program(candidate) != encode_program(program)
    assert _python_verdict(candidate, arch) == "VERIFY:E_ABI_BOUNDS"


@pytest.mark.parametrize("target", TARGETS, ids=lambda item: item[0])
def test_python_partition_order_rejects_each_generated_target(
    partition_programs, target
):
    field, _, _ = target
    _, arch, program, span = _nonempty_fixture(partition_programs, target)
    candidate = _replace_first_membership_span(
        program,
        field,
        IdSpan(span.first_id + 1, span.count),
    )

    assert encode_program(candidate) != encode_program(program)
    assert _python_verdict(candidate, arch) == "VERIFY:E_ABI_ORDER"


def test_python_partition_preserves_real_empty_membership_boundaries(
    partition_programs,
):
    empty = set()
    for _, arch, program in partition_programs:
        assert _python_verdict(program, arch) == "ACCEPTED"
        membership = program.semantics.variants[0].membership
        for target in TARGETS:
            field, _, _ = target
            if _target_population(program, target) == 0:
                assert getattr(membership, field) == IdSpan(1, 0)
                empty.add(field)
    assert empty


def test_python_partition_preserves_empty_second_variant_first_ids(
    nonempty_then_empty_variant_program,
):
    arch, program = nonempty_then_empty_variant_program
    assert _python_verdict(program, arch) == "ACCEPTED"
    second = program.semantics.variants[1]
    empty_fields = {
        field
        for field, source, program_field in TARGETS
        if getattr(second.membership, field).count == 0
        and getattr(
            program if source == "transport" else program.semantics,
            program_field,
        ) is not None
    }
    assert {"kernel_tensors", "objects", "views", "kernel_ops"} <= empty_fields
    for field, source, program_field in TARGETS:
        span = getattr(second.membership, field)
        if span.count == 0:
            owner = program if source == "transport" else program.semantics
            assert span == IdSpan(len(getattr(owner, program_field)) + 1, 0)


@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        (
            lambda span: IdSpan(span.first_id, 0xFFFFFFFFFFFFFFFF),
            "E_ABI_BOUNDS",
        ),
        (
            lambda span: IdSpan(0xFFFFFFFFFFFFFFFF, span.count),
            "E_ABI_ORDER",
        ),
    ),
    ids=("count_u64_max", "first_u64_max"),
)
def test_python_partition_near_u64_span_is_rejected_before_traversal(
    partition_programs, mutation, expected,
):
    field, _, _ = KERNEL_TENSOR_TARGET
    _, arch, program, span = _nonempty_fixture(
        partition_programs, KERNEL_TENSOR_TARGET,
    )
    candidate = _replace_first_membership_span(program, field, mutation(span))

    assert encode_program(candidate) != encode_program(program)
    assert _python_verdict(candidate, arch) == f"VERIFY:{expected}"


@pytest.mark.parametrize("target", TARGETS, ids=lambda item: item[0])
@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        (
            lambda span: IdSpan(span.first_id, span.count - 1),
            "E_ABI_BOUNDS",
        ),
        (
            lambda span: IdSpan(span.first_id, span.count + 1),
            "E_ABI_BOUNDS",
        ),
        (
            lambda span: IdSpan(span.first_id + 1, span.count),
            "E_ABI_ORDER",
        ),
    ),
    ids=("gap", "bounds", "order"),
)
def test_cpp_partition_context_rejects_each_generated_target(
    driver,
    tmp_path,
    partition_programs,
    target,
    mutation,
    expected,
):
    field, _, _ = target
    _, arch, program, span = _nonempty_fixture(partition_programs, target)
    candidate = _replace_first_membership_span(program, field, mutation(span))

    assert encode_program(candidate) != encode_program(program)
    assert _python_verdict(candidate, arch) == f"VERIFY:{expected}"
    assert _cpp_verdict(
        encode_program(candidate), driver, tmp_path, arch
    ) == f"VERIFY:{expected}"


def test_cpp_partition_context_accepts_real_empty_membership_boundaries(
    driver,
    tmp_path,
    partition_programs,
):
    for _, arch, program in partition_programs:
        assert _python_verdict(program, arch) == "ACCEPTED"
        assert _cpp_verdict(
            encode_program(program), driver, tmp_path, arch
        ) == "ACCEPTED"


def test_cpp_partition_context_accepts_empty_second_variant_first_ids(
    driver,
    tmp_path,
    nonempty_then_empty_variant_program,
):
    arch, program = nonempty_then_empty_variant_program
    assert _python_verdict(program, arch) == "ACCEPTED"
    assert _cpp_verdict(
        encode_program(program), driver, tmp_path, arch
    ) == "ACCEPTED"


@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        (
            lambda span: IdSpan(span.first_id, 0xFFFFFFFFFFFFFFFF),
            "E_ABI_BOUNDS",
        ),
        (
            lambda span: IdSpan(0xFFFFFFFFFFFFFFFF, span.count),
            "E_ABI_ORDER",
        ),
    ),
    ids=("count_u64_max", "first_u64_max"),
)
def test_cpp_partition_near_u64_span_is_rejected_before_traversal(
    driver, tmp_path, partition_programs, mutation, expected,
):
    field, _, _ = KERNEL_TENSOR_TARGET
    _, arch, program, span = _nonempty_fixture(
        partition_programs, KERNEL_TENSOR_TARGET,
    )
    candidate = _replace_first_membership_span(program, field, mutation(span))

    assert encode_program(candidate) != encode_program(program)
    assert _python_verdict(candidate, arch) == f"VERIFY:{expected}"
    assert _cpp_verdict(
        encode_program(candidate), driver, tmp_path, arch
    ) == f"VERIFY:{expected}"
