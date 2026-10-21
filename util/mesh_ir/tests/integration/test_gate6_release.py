import json

import pytest
import yaml

from mesh_ir.agent_workload import load_workload_plan
from mesh_ir.gate3_oracle import read_facts_document
from .serving_runtime import PROBE, run_serving_fixture, write_shared_fixture


@pytest.mark.parametrize('case,statuses', [
    ('absent', ['NOT_FOUND']),
    ('stale', ['STALE_GENERATION']),
    ('active', ['SUCCESS']),
    ('duplicate', ['SUCCESS', 'BUSY']),
    ('full', ['SUCCESS', 'BUSY']),
    ('released_generation', ['SUCCESS', 'STALE_GENERATION', 'NOT_FOUND']),
])
def test_release_statuses_follow_the_owned_session_without_retry(tmp_path, case, statuses):
    _, work = write_shared_fixture(tmp_path, moe=True)
    workload = load_workload_plan(work / 'workload.json')
    actions = []
    for index, _ in enumerate(statuses):
        ordinal = index + 1
        trigger = ({'kind': 'AFTER_RELEASE_TERMINAL', 'after_control_ordinal': index}
                   if case == 'released_generation' and index else
                   {'kind': 'AFTER_BATCH_FREEZE', 'event_user_id': 0,
                    'event_task_seq': 0, 'event_repair_round': 0})
        actions.append({
            'control_ordinal': ordinal, 'opcode': 'RELEASE_SESSION',
            'issuer_user_id': 0, 'issuer_task_seq': 0,
            'target_session_id': 99 if case == 'absent' else 2 if case == 'full' and index else 1,
            'target_kv_handle': 99 if case == 'absent' else 2 if case == 'full' and index else 1,
            'target_generation': 2 if case == 'stale' or (case == 'released_generation' and index == 2) else 1,
            'trigger': trigger,
        })
    (work / 'control.json').write_text(json.dumps({
        'schema': 'agent_control_plan_v1', 'version': 1,
        'workload_plan_digest': workload.digest, 'actions': actions}))
    config_path = work / 'config.yaml'
    config = yaml.safe_load(config_path.read_text())
    config['agent']['control_plan'] = 'control.json'
    config['serving']['session_release_waiter_entries'] = 8
    config['serving']['max_request_contexts'] = 8
    config['agent_axi_driver']['synthetic_host_services']['release_waiter_entries'] = 8
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    entry = PROBE
    if case == 'full':
        entry = work / 'release_capacity_probe.py'
        entry.write_text(
            'import runpy\nfrom m5.objects import NpuServingFrontend\n'
            'original = NpuServingFrontend.__init__\n'
            'def override(self, *args, **kwargs):\n'
            '    kwargs["kv_release_waiter_entries"] = 1\n'
            '    original(self, *args, **kwargs)\n'
            'NpuServingFrontend.__init__ = override\n'
            f'runpy.run_path({str(PROBE)!r}, run_name="__m5_main__")\n')
    _, result, metrics = run_serving_fixture(work, case, entry=entry,
        weight_policy='cached', delay_read_cycles=10000)
    facts = read_facts_document(work / case / 'gate6_facts.tsv')
    consumed = [row for row in facts.events if row['kind'] == 'CQ_CONSUME']
    assert len(consumed) == 2 + len(statuses)
    assert len({row['request_id'] for row in consumed}) == len(consumed)
    for request_id in (1, 4294967297):
        assert next(row for row in consumed if row['request_id'] == request_id)['status'] == 'SUCCESS'
    controls = sorted((row for row in consumed if row['request_id'] not in (1, 4294967297)),
                      key=lambda row: row['request_id'])
    assert [row['status'] for row in controls] == statuses
    deferred = [row for row in facts.events if row['kind'] == 'CONTROL_DEFERRED']
    assert len(deferred) == (0 if case in ('absent', 'stale') else 1)
    assert metrics.get('kv_release_pending', 0) == 0
    assert metrics['kv_session_records'] == (2 if case in ('absent', 'stale') else 1)
    assert result['terminal'] == 'DONE'
    assert metrics['metadata_reads_validated'] == metrics['generated_code_reads_validated'] == 2
    for row in deferred:
        terminal = next(item for item in consumed if item['request_id'] == row['request_id'])
        assert terminal['tick'] > max(phase['commit_tick'] for phase in facts.serving_phases)
