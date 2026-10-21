from collections import Counter
from dataclasses import replace

import pytest

from mesh_ir.abi import encode_program
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.execution_view import build_execution_views
from mesh_ir.scheduled.verify import verify_program
from mesh_ir.serving_profiles import apply_request_profile_keys
from tests.golden.test_mutation_corpus import _cpp_verdict
from tests.integration.support.workspace_program import build_workspace_program


@pytest.fixture
def rom_program():
    program, arch = build_workspace_program(output_rows=6, publish_rows=4)
    counts = [Counter(program.commands[index].core_id for index in view.command_indices)
        for view in build_execution_views(program).views]
    peak = max(count for row in counts for count in row.values())
    assert max(Counter(row.core_id for row in program.commands).values()) > peak
    return program, arch, peak


@pytest.mark.parametrize('excess', [0, 1])
def test_python_command_rom_capacity_is_per_core_per_variant(rom_program, excess):
    program, arch, peak = rom_program
    arch.command_rom_entries = peak - excess
    program = apply_request_profile_keys(replace(program, arch_digest=arch.digest()))
    if excess:
        with pytest.raises(MeshIrError, match='command ROM'):
            verify_program(program, arch)
    else:
        verify_program(program, arch)


@pytest.mark.parametrize('excess', [0, 1])
def test_native_command_rom_capacity_matches_python(rom_program, excess, driver, tmp_path):
    program, arch, peak = rom_program
    arch.command_rom_entries = peak - excess
    program = apply_request_profile_keys(replace(program, arch_digest=arch.digest()))
    verdict = _cpp_verdict(encode_program(program), driver, tmp_path, arch)
    assert verdict == ('VERIFY:E_ABI_BOUNDS' if excess else 'ACCEPTED')


def test_capacity_is_checked_before_expensive_memory_admission(rom_program, monkeypatch):
    import mesh_ir.scheduled.verify as verifier

    program, arch, peak = rom_program
    arch.command_rom_entries = peak - 1
    program = apply_request_profile_keys(replace(program, arch_digest=arch.digest()))

    def forbidden(*args, **kwargs):
        raise AssertionError('memory admission ran before command capacity admission')

    monkeypatch.setattr(verifier, 'verify_kernel_memory', forbidden)
    with pytest.raises(MeshIrError, match='command ROM'):
        verify_program(program, arch)
