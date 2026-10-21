import subprocess

import pytest

from mesh_ir.abi.encoder import encode_program
from mesh_ir.architecture import load_arch
from mesh_ir.generated import abi as A
from mesh_ir.golden_programs import build_zero_dma_program
from tests.unit.test_abi2_admission import admission_probe


@pytest.mark.parametrize('admission_probe', ['serving_static'], indirect=True)
def test_zero_byte_peer_retains_its_admitted_control_plan(admission_probe, tmp_path):
    program = build_zero_dma_program(load_arch('configs/example/ai_mesh/arch/mesh_1x2.yaml'))
    image = tmp_path / 'zero.mshb'
    image.write_bytes(encode_program(program))
    run = subprocess.run([admission_probe, image, 'invoke-peer'], capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    expected = {(row.transfer_id, row.dst.owner_core, 0) for row in program.dma_descriptors
                if row.kind == A.DMA_KIND.P2P_PUSH}
    assert expected
    assert {tuple(map(int, line.split())) for line in run.stdout.splitlines()} == expected
