import pytest

from mesh_ir.diagnostics import MeshIrError
from mesh_ir.scheduled.model import ObjectSource, ScheduledDependencyKind
from mesh_ir.scheduled.verify import verify_program
from tests.golden.support.stage2_control_dependency_cases import build_stage2_control_dependency_cases


CASES = {
    case.case_id: case
    for case in build_stage2_control_dependency_cases()
    if case.case_id.startswith("sram_reuse_")
}


def test_sram_reuse_source_access_positive():
    case = CASES["sram_reuse_positive"]
    reuse = next(item for item in case.program.semantics.dependencies if item.kind is ScheduledDependencyKind.SRAM_REUSE)

    assert reuse.source == ObjectSource(1)
    assert verify_program(case.program, case.arch).program is case.program


def test_sram_reuse_source_must_access_declared_object():
    case = CASES["sram_reuse_source"]

    assert case.program.semantic_sha256 != case.baseline.semantic_sha256
    with pytest.raises(MeshIrError) as error:
        verify_program(case.program, case.arch)
    assert error.value.code == case.expected_code == "E_ABI_BOUNDS"
