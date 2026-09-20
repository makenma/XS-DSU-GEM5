import pytest

from mesh_ir.abi.encoder import encode_program
from mesh_ir.architecture import load_arch
from mesh_ir.generated import abi as A
from tests.golden.test_mutation_corpus import (
    ARCH_PATH,
    _build_dma_barrier_completion_program,
    _cpp_verdict,
    _python_verdict,
    _replace_transport,
    _with_fresh_semantic_sha,
)


@pytest.fixture(scope="module")
def arch():
    _python_verdict.arch = load_arch(ARCH_PATH)
    return _python_verdict.arch


def _dma_completion_event_retyped_barrier_blob(arch):
    program = _build_dma_barrier_completion_program(arch)
    descriptor = program.dma_descriptors[0]
    command = program.commands[descriptor.command_id - 1]
    event = program.events[descriptor.completion_event - 1]
    assert command.signal_event == descriptor.completion_event
    assert event.kind == A.EVENT_KIND.NORMAL
    changed = _replace_transport(
        program,
        "events",
        descriptor.completion_event - 1,
        kind=A.EVENT_KIND.BARRIER,
    )
    blob = encode_program(_with_fresh_semantic_sha(changed))
    assert blob != encode_program(program)
    return blob


def test_dma_normal_completion_event_retyped_to_barrier_is_rejected(arch):
    verdict = _python_verdict(_dma_completion_event_retyped_barrier_blob(arch))
    assert verdict == "VERIFY:E_EVENT_MULTIPLE_PRODUCERS"


def test_dma_normal_completion_event_retyped_to_barrier_languages_agree(
    arch, driver, tmp_path
):
    blob = _dma_completion_event_retyped_barrier_blob(arch)
    python = _python_verdict(blob)
    cpp = _cpp_verdict(blob, driver, tmp_path, arch)
    assert python == cpp == "VERIFY:E_EVENT_MULTIPLE_PRODUCERS"
