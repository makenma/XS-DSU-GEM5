import pytest

from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.kernel_ir import KernelOpcode
from mesh_ir.scheduled.assemble import _TransportSections
from mesh_ir.scheduled.verify import _verify_dependencies_and_lifecycle, verify_program
from tests.golden.support.stage3_intrinsic_memory_cases import (
    build_stage3_intrinsic_memory_cases,
)


CASES = build_stage3_intrinsic_memory_cases()
BASELINES = tuple(
    next(case for case in CASES if case.baseline.semantic_sha256 == semantic_sha256)
    for semantic_sha256 in dict.fromkeys(case.baseline.semantic_sha256 for case in CASES)
)


@pytest.mark.parametrize("case", BASELINES, ids=lambda case: f"baseline_{case.case_id}")
def test_complete_intrinsic_memory_case_baselines_are_accepted(case):
    assert verify_program(case.baseline, case.arch).program is case.baseline


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_complete_intrinsic_memory_cases_have_exact_python_diagnostics(case):
    if case.expected_code is None:
        assert verify_program(case.program, case.arch).program is case.program
        return

    assert case.program.semantic_sha256 != case.baseline.semantic_sha256
    with pytest.raises(MeshIrError) as error:
        verify_program(case.program, case.arch)
    assert error.value.code == case.expected_code


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.case_id)
def test_intrinsic_memory_cases_keep_typed_dependencies_and_lifecycle_coherent(case):
    program = case.program
    _verify_dependencies_and_lifecycle(
        program.semantics,
        _TransportSections(
            program.strings,
            program.entrypoints,
            program.profiles,
            program.tensors,
            program.shards,
            program.allocations,
            program.streams,
            program.commands,
            program.command_waits,
            program.command_operands,
            program.events,
            program.dma_descriptors,
            program.op_attrs,
            program.relocations,
        ),
    )


def test_partial_sum_fragment_replacement_preserves_untouched_contributors():
    case = next(
        item
        for item in CASES
        if item.case_id == "partial_sum_fragment_replacement_positive"
    )
    reduce = next(
        item
        for item in case.program.semantics.kernel_ops
        if item.opcode is KernelOpcode.LOCAL_REDUCE
    )
    assert len(reduce.reads) == 4
    assert len(reduce.writes) == 2
    assert verify_program(case.program, case.arch).program is case.program


def test_same_object_hazard_requires_completion_not_stream_or_numeric_order():
    case = next(
        item
        for item in CASES
        if item.case_id == "same_object_stream_order_is_not_completion"
    )
    overwrite = next(
        item
        for item in case.program.semantics.kernel_ops
        if item.stable_key == "pin:z-hazard:overwrite"
    )
    assert overwrite.after_tokens == (2,)
    assert overwrite.op_id > 9
    with pytest.raises(MeshIrError) as error:
        verify_program(case.program, case.arch)
    assert error.value.code == "E_TENSOR_NOT_RESIDENT"


def test_p2p_visibility_negative_keeps_the_receive_identity_valid():
    case = next(
        item
        for item in CASES
        if item.case_id == "p2p_source_completion_missing"
    )
    receive = next(
        item
        for item in case.program.semantics.kernel_ops
        if item.opcode is KernelOpcode.RECV_WAIT
    )
    assert receive.after_tokens == (1,)
    with pytest.raises(MeshIrError) as error:
        verify_program(case.program, case.arch)
    assert error.value.code == "E_P2P_UNMATCHED"
