from mesh_ir.abi.encoder import encode_program
from mesh_ir.scheduled.model import ControlCommandSource, RepeatCommandAttrs
from tests.golden.support.stage2_control_dependency_cases import (
    build_stage2_control_dependency_cases,
)
from tests.golden.support.stage2_control_fixture import build_event_fanin_fixture
from tests.unit.test_gate3_cpp_verifier import _run


CASES = {
    case.case_id: case
    for case in build_stage2_control_dependency_cases()
}


def _write_program(tmp_path, name, program):
    path = tmp_path / f"{name}.mshb"
    path.write_bytes(encode_program(program))
    return str(path)


def _event_fanin_arguments(tmp_path):
    fixture = build_event_fanin_fixture()
    assert fixture.program == CASES["event_fanin_positive"].program
    return (
        _write_program(tmp_path, "event_fanin", fixture.program),
        str(fixture.operations["barrier_arrival_core0"]),
        str(fixture.tokens["barrier_arrival_core0"]),
        str(fixture.operations["barrier_waiter_core0"]),
        str(fixture.commands["normal_signal_first"]),
        str(fixture.commands["normal_signal_second"]),
        str(fixture.commands["normal_waiter"]),
        str(fixture.events["normal_signal_first"]),
        str(fixture.dependencies["normal_signal_to_waiter"]),
    )


def test_cpp_facts_event_fanin_separates_intrinsic_declared_and_completion_graphs(
    verifier_probe, tmp_path,
):
    assert _run(
        verifier_probe,
        "facts_event_fanin",
        *_event_fanin_arguments(tmp_path),
    ) == "FACTS:OK"


def test_cpp_facts_reject_default_and_moved_from_outputs(
    verifier_probe, tmp_path,
):
    assert _run(
        verifier_probe,
        "facts_default_moved",
        *_event_fanin_arguments(tmp_path),
    ) == "FACTS:OK"


def test_cpp_facts_reject_foreign_equal_index_handles(
    verifier_probe, tmp_path,
):
    assert _run(
        verifier_probe,
        "facts_cross_invocation",
        *_event_fanin_arguments(tmp_path),
    ) == "FACTS:OK"


def test_cpp_facts_preserve_published_output_when_dependency_admission_fails(
    verifier_probe, tmp_path,
):
    positive = CASES["event_fanin_positive"]
    negative = CASES["sram_reuse_source"]
    assert positive.expected_code is None
    assert negative.expected_code == "E_ABI_BOUNDS"
    assert _run(
        verifier_probe,
        "facts_atomic",
        _write_program(tmp_path, positive.case_id, positive.program),
        _write_program(tmp_path, negative.case_id, negative.program),
    ) == "FACTS:OK"


def test_cpp_facts_report_admitted_repeat_execution_count(verifier_probe, tmp_path):
    repeat = CASES["repeat_event_positive"]
    assert repeat.expected_code is None
    semantic = next(
        item
        for item in repeat.program.semantics.command_semantics
        if type(item.source) is ControlCommandSource
        and type(item.source.attrs) is RepeatCommandAttrs
    )
    assert _run(
        verifier_probe,
        "facts_repeat_execution_count",
        _write_program(tmp_path, repeat.case_id, repeat.program),
        str(semantic.command_id),
        str(semantic.source.attrs.repeat_count),
    ) == "FACTS:OK"
