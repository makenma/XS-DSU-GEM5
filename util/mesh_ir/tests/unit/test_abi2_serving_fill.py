from pathlib import Path
import subprocess

import pytest

from mesh_ir.abi.decoder import decode_program
from mesh_ir.abi.encoder import encode_program
from tests.unit.test_abi2_admission import admission_probe


@pytest.mark.parametrize('admission_probe', ['serving_static'], indirect=True)
@pytest.mark.parametrize('mode', ['single', 'multi', 'ordered', 'wrong_entrypoint', 'wrong_profile', 'foreign_command', 'wrong_generation', 'short_content', 'no_consumer'])
def test_runtime_fill_consumes_selected_command_ids_and_member_binding(admission_probe, tmp_path, mode):
    root = Path(__file__).resolve().parents[4]
    source = root / 'tests/gem5/ai_mesh/fixtures/gate6/serving_fullview.mshb'
    program = decode_program(source.read_bytes())
    path = tmp_path / 'program.mshb'
    path.write_bytes(encode_program(program))
    result = subprocess.run([str(admission_probe), str(path), 'invoke-fill-' + mode], capture_output=True, text=True)
    healthy = mode in ('single', 'multi', 'ordered')
    assert result.returncode == (0 if healthy else 1), result.stdout + result.stderr
    assert ('ACCEPTED' if healthy else 'E_BINDING_ROLE') in result.stdout
