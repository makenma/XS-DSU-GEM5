import hashlib
import json

import pytest
import yaml

from mesh_ir.abi.decoder import decode_program
from mesh_ir.architecture import load_arch
from mesh_ir.serving_programs import serving_program
from tests.integration.serving_runtime import PROBE, REPO, run_serving_fixture, write_serving_fixture


@pytest.mark.parametrize('failure', ['', 'sha256', 'program_identity'])
def test_binary_startup_image_reaches_real_memory_owners(tmp_path, failure):
    source = REPO / 'configs/example/ai_mesh/arch/mesh_1x2.yaml'
    document = yaml.safe_load(source.read_text())
    target = next(row for row in document['fabric']['targets'] if row['dst_node'] == 100)
    hbm = next(row for row in target['ranges'] if row['region_id'] == 0)
    boundary = 8 << 20
    document['fabric']['targets'].append(dict(name='image_hbm', dst_node=101, router_id=1,
        ranges=[dict(hbm, offset_bytes=boundary, size_bytes=hbm['size_bytes'] - boundary)]))
    hbm['size_bytes'] = boundary
    document['fabric']['quotas'].extend(dict(row, dst_node=101)
        for row in list(document['fabric']['quotas']) if row['dst_node'] == 100)
    arch_path = tmp_path / 'input.arch.yaml'
    arch_path.write_text(yaml.safe_dump(document))
    arch = load_arch(arch_path)
    write_serving_fixture(tmp_path, serving_program(arch, output_tokens=2), arch_path)
    program = decode_program((tmp_path / 'program.mshb').read_bytes())
    payload = bytes((i * 31 + i // 257) % 256 for i in range(200003))
    (tmp_path / 'payload.bin').write_bytes(b'prefix!' + payload + b'trailer')
    address = arch.regions[0].base + boundary - 333
    image = dict(schema='mesh-memory-file-image-v1',
        program_semantic_sha256=program.semantic_sha256, architecture_sha256=arch.digest().hex(),
        spans=[dict(address=address, path='payload.bin', offset=7, size=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest())])
    if failure == 'sha256':
        image['spans'][0]['sha256'] = '0' * 64
    elif failure == 'program_identity':
        image['program_semantic_sha256'] = '0' * 64
    manifest = tmp_path / 'image.json'
    manifest.write_text(json.dumps(image))
    checks = {100: (address, 333), 101: (address + 333, len(payload) - 333)}
    for node, (base, size) in checks.items():
        (tmp_path / f'verify-{node}.txt').write_text(f'{base:#x} {size}\n')
    probe = tmp_path / 'image_probe.py'
    probe.write_text(
        'import runpy\nimport m5.objects\nfrom pathlib import Path\n'
        'endpoint = m5.objects.NpuMemoryEndpoint\n'
        'def observed_endpoint(*args, **kwargs):\n'
        '    node = int(kwargs["adapter"].dst_node)\n'
        f'    path = Path({str(tmp_path)!r}) / f"verify-{{node}}.txt"\n'
        '    if path.exists():\n'
        '        kwargs.update(verify_json=str(path), verify_bytes_limit=250000)\n'
        '    return endpoint(*args, **kwargs)\n'
        'm5.objects.NpuMemoryEndpoint = observed_endpoint\n'
        f'runpy.run_path({str(PROBE)!r}, run_name="__main__")\n')
    result = run_serving_fixture(tmp_path, 'runtime', entry=probe,
        rejection_code='E_MEMORY_IMAGE' if failure else None,
        extra_args=('--memory-file-image', str(manifest)))
    if failure:
        return
    _, observation, metrics = result
    endpoints = {row['node']: row for row in observation['memory_endpoints']}
    actual = b''.join(bytes.fromhex(endpoints[node]['verifies'][0]['bytes_hex']) for node in checks)
    assert actual == payload
    assert endpoints[101]['committed_valid_bytes'] == 0
    assert observation['terminal'] == 'DONE'
    assert metrics['completed_tasks'] == metrics['frontend_drained'] == 1
    assert all(row['idle'] for row in endpoints.values())
    checkpoint = tmp_path / 'checkpoint'
    _, continued, continued_metrics = run_serving_fixture(tmp_path, 'continued', entry=probe,
        extra_args=('--memory-file-image', str(manifest), '--checkpoint-dir', str(checkpoint),
                    '--checkpoint-at-tick', '0'))
    _, restored, restored_metrics = run_serving_fixture(tmp_path, 'restored', entry=probe,
        extra_args=('--memory-file-image', str(manifest), '--restore-checkpoint', str(checkpoint)))
    assert restored['memory_endpoints'] == continued['memory_endpoints']
    assert restored['terminal_tick'] == continued['terminal_tick']
    assert restored_metrics == continued_metrics
    image['spans'][0]['offset'] = 8
    manifest.write_text(json.dumps(image))
    run_serving_fixture(tmp_path, 'wrong-image', entry=probe, rejection_code='E_CHECKPOINT_IDENTITY',
        extra_args=('--memory-file-image', str(manifest), '--restore-checkpoint', str(checkpoint)))
