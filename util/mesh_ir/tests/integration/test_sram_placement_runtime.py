import json

import pytest

from tests.integration.support.runtime_harness import ARCH, build_program_with_arch, run_mock


@pytest.fixture(scope="module")
def placements(tmp_path_factory):
    root = tmp_path_factory.mktemp("sram-placement")
    results = {}
    for name, program, arch in (
        ("parallel", "sram_parallel", ARCH),
        ("conflict", "sram_conflict", ARCH),
        ("two_ports", "sram_conflict", ARCH.with_name("mesh_1x2_2port.yaml")),
    ):
        directory = build_program_with_arch(root, program, arch)
        run = run_mock(root, name, program, directory, ("--arch", str(arch)))
        assert run.returncode == 0, run.stdout + run.stderr
        results[name] = json.loads((directory / "actual_result.json").read_text())
    return results


def test_conflict_completes_later_than_parallel(placements):
    assert placements["conflict"]["terminal_tick"] > placements["parallel"]["terminal_tick"]


def test_two_ports_shorten_conflict(placements):
    assert placements["parallel"]["terminal_tick"] <= placements["two_ports"]["terminal_tick"]
    assert placements["two_ports"]["terminal_tick"] < placements["conflict"]["terminal_tick"]
