import struct
import json

from mesh_ir.kv_persistence import load_persistent_manifest

from mesh_ir.gate3_oracle import read_facts_document
from mesh_ir.generated import agent_abi as A
from .serving_runtime import PROBE, run_serving_fixture, write_shared_fixture


def test_bad_provider_population_reduces_one_shared_prestart_failure(tmp_path):
    _, work = write_shared_fixture(tmp_path, moe=True)
    entry = work / 'provider_fault.py'
    entry.write_text('import m5, runpy\n'
        'from m5.objects import Root\n'
        'original = m5.instantiate\n'
        'def instantiate(*args, **kwargs):\n'
        '    original(*args, **kwargs)\n'
        '    for obj in Root.getInstance().descendants():\n'
        '        if obj.__class__.__name__ == "MeshProgramLoader":\n'
        '            obj.setServingMoeProvider(lambda layer, tokens: [])\n'
        'm5.instantiate = instantiate\n'
        f'runpy.run_path({str(PROBE)!r}, run_name="__m5_main__")\n')
    _, result, metrics = run_serving_fixture(work, 'fault', entry=entry, weight_policy='cached')
    facts = read_facts_document(work / 'fault/gate6_facts.tsv')
    assert result['terminal'] == 'ERROR_DRAINED'
    assert not any(row['kind'] == 'CORE_START' for row in facts.events)
    failures = [row['failure'] for row in result['instances'] if row.get('failure')]
    assert len(failures) == 1
    source = struct.unpack('<HHBBHIIII16s', bytes.fromhex(failures[0]['source_wire']))
    assert source[:5] == (6, 65535, 3, 7, 0)
    assert source[5] != 0
    assert source[6:] == (0, 0, 0, bytes(16))
    assert failures[0]['detail'] == A.DETAIL_CODE['E_MOE_PROFILE']
    cq = [row for row in facts.events if row['kind'] == 'CQ_CONSUME']
    assert len(cq) == 2
    assert {row['request_id'] for row in cq} == {1, 4294967297}
    assert all(row['status'] == 'ERROR' for row in cq)
    assert metrics['metadata_reads_validated'] == 2
    state_path = work / 'fault/gate6_facts.tsv.kv.json'
    state = json.loads(state_path.read_text())
    load_persistent_manifest(state_path, state['manifest']['identity'], state['sha256'])
    assert metrics['live_cq_obligations'] == 0
