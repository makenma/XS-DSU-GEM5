import subprocess

import pytest

from mesh_ir.abi.encoder import encode_program
from mesh_ir.architecture import load_arch
from mesh_ir.serving_profiles import apply_request_profile_keys
from mesh_ir.serving_programs import serving_program
from tests.unit.test_abi2_admission import admission_probe


@pytest.mark.parametrize('admission_probe', ['serving'], indirect=True)
@pytest.mark.parametrize('mode,expected', [('invoke-serving-windows', 'SERVING_WINDOWS_PASS'), ('invoke-serving-batch', 'SERVING_BATCH_PASS'), ('invoke-serving-batch-decode', 'SERVING_BATCH_PASS'), ('invoke-serving-batch-publish', 'SERVING_BATCH_PASS'), ('invoke-serving-batch-short', 'SERVING_SHORT_BACKING_REJECTED')])
def test_serving_windows_use_the_prepared_invocation(admission_probe, tmp_path, mode, expected):
    arch = load_arch('configs/example/ai_mesh/arch/mesh_1x2_moe.yaml')
    program = apply_request_profile_keys(serving_program(arch))
    path = tmp_path / 'serving.mshb'
    path.write_bytes(encode_program(program))
    result = subprocess.run([str(admission_probe), str(path), mode], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == expected
