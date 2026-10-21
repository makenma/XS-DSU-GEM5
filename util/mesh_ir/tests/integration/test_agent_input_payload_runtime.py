import hashlib
import json

import pytest
import yaml

from mesh_ir.architecture import load_arch
from mesh_ir.serving_programs import serving_program
from tests.integration.serving_runtime import ARCH_YAML, PROBE, run_serving_fixture, write_serving_fixture


@pytest.mark.parametrize('input_parts', [1, 2])
def test_driver_writes_frozen_input_and_restores_identity(tmp_path, input_parts):
    arch = load_arch(ARCH_YAML)
    write_serving_fixture(tmp_path, serving_program(arch, output_tokens=2, input_parts=input_parts))
    path = tmp_path / 'workload.json'
    document = json.loads(path.read_text())
    round_ = document['users'][0]['tasks'][0]['rounds'][0]
    payload = bytes((i * 31 + 7) % 256 for i in range(round_['full_context_bytes']))
    (tmp_path / 'input.bin').write_bytes(payload)
    round_.update(input_payload_file='input.bin', input_content_digest=hashlib.sha256(payload).hexdigest())
    path.write_text(json.dumps(document))
    config = yaml.safe_load((tmp_path / 'config.yaml').read_text())
    address = config['serving']['address_map']['input_arena']['base']
    verify = tmp_path / 'verify-input.txt'
    verify.write_text(f'{address:#x} {len(payload)}\n')
    probe = tmp_path / 'probe.py'
    probe.write_text(
        'import runpy\nimport m5.objects\n'
        'endpoint = m5.objects.NpuMemoryEndpoint\n'
        'def observed_endpoint(*args, **kwargs):\n'
        f'    kwargs.update(verify_json={str(verify)!r}, verify_bytes_limit=4096)\n'
        '    return endpoint(*args, **kwargs)\n'
        'm5.objects.NpuMemoryEndpoint = observed_endpoint\n'
        f'runpy.run_path({str(PROBE)!r}, run_name="__main__")\n')
    _, result, metrics = run_serving_fixture(tmp_path, 'runtime', entry=probe)
    checks = [check for endpoint in result['memory_endpoints'] for check in endpoint['verifies']
              if check['bytes_hex']]
    assert any(bytes.fromhex(check['bytes_hex']) == payload for check in checks)
    assert result['terminal'] == 'DONE'
    assert metrics['completed_tasks'] == metrics['frontend_drained'] == 1
    checkpoint = tmp_path / 'checkpoint'
    _, continued, continued_metrics = run_serving_fixture(tmp_path, 'continued', entry=probe,
        extra_args=('--checkpoint-dir', str(checkpoint), '--checkpoint-at-tick', '0'))
    _, restored, restored_metrics = run_serving_fixture(tmp_path, 'restored', entry=probe,
        extra_args=('--restore-checkpoint', str(checkpoint)))
    assert continued['memory_endpoints'] == restored['memory_endpoints']
    assert continued['terminal_tick'] == restored['terminal_tick']
    assert continued_metrics == restored_metrics
    payload = payload[::-1]
    (tmp_path / 'input.bin').write_bytes(payload)
    round_['input_content_digest'] = hashlib.sha256(payload).hexdigest()
    path.write_text(json.dumps(document))
    run_serving_fixture(tmp_path, 'identity-rejected', entry=probe,
        rejection_code='E_CHECKPOINT_IDENTITY',
        extra_args=('--restore-checkpoint', str(checkpoint)))
