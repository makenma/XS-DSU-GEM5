import json
import logging

import pytest

from mesh_ir.prefill_smoke import prepare
from tests.integration.serving_runtime import FIXTURES, GEM5
from tests.integration.test_compilation_source import compiled_publication
from tests.integration.test_tensor_payload import payload_carrier


@pytest.mark.parametrize('payload_carrier', ['balanced'], indirect=True)
def test_preparation_workers_preserve_program_and_payload(compiled_publication, payload_carrier, tmp_path, caplog):
    manifest, _, _, *_, arch_path = payload_carrier
    caplog.set_level(logging.INFO, logger='mesh_ir.analysis.execution')
    outputs = []
    for workers in (1, 32):
        output = tmp_path / f'bundle-{workers}'
        prepare(compiled_publication, arch_path, manifest, FIXTURES / 'serving_runtime_config.yaml',
            FIXTURES / 'serving_workload_plan.json', output, input_tokens=2, publish_chunk_bytes=32, workers=workers)
        outputs.append(output)
    for name in ('source.mshb', 'publication.mshb', 'program.mshb', 'schedule.mesh.json',
                 'memory-image.json', 'model-invocation.json', 'input.bin', 'config.yaml', 'workload.json'):
        assert (outputs[0] / name).read_bytes() == (outputs[1] / name).read_bytes()
    document = json.loads((outputs[1] / 'bundle.json').read_text())
    assert document['prepare'] == 'PASS'
    assert document['analysis']['execution']['workers'] == 32
    assert document['analysis']['execution']['completed_tasks'] > 0
    assert all(row['status'] == 'PASS' for row in document['analysis']['stages'])
    assert 'name=ComposeProgram' in caplog.text
    assert 'name=EncodeArtifacts' in caplog.text
    from mesh_ir.prefill_smoke import run, check
    run(outputs[1], GEM5, sim_tick_limit=10000000000)
    assert check(outputs[1])['check'] == 'PASS'
