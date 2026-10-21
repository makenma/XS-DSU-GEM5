import copy
import json

import pytest
import yaml

from mesh_ir.gate3_oracle import read_facts_document
from mesh_ir.agent_workload import load_workload_plan
from .serving_runtime import run_serving_fixture, write_shared_fixture


@pytest.mark.parametrize('fault_kind', ['read', 'write'])
@pytest.mark.parametrize('cancel_member', [False, True])
def test_shared_failure_has_one_record_per_batch_and_one_terminal_per_member(tmp_path, fault_kind, cancel_member):
    _, work = write_shared_fixture(tmp_path, moe=True)
    workload = json.loads((work / 'workload.json').read_text())
    third = copy.deepcopy(workload['users'][1])
    third['user_id'] = 2
    task = third['tasks'][0]
    task['session_id'] = task['kv_handle'] = 3
    task['rounds'][0]['workload_plan_item_id'] = 3
    task['rounds'][0]['input_content_digest'] = '7f' * 32
    workload['users'].append(third)
    (work / 'workload.json').write_text(json.dumps(workload))
    config_path = work / 'config.yaml'
    config = yaml.safe_load(config_path.read_text())
    config['agent']['users'] = 3
    config['serving'].update(max_active_sequences=3, batch_wait_cycles=1000,
                             max_prefill_tokens_per_batch=16)
    if cancel_member:
        (work / 'control.json').write_text(json.dumps({
            'schema': 'agent_control_plan_v1', 'version': 1,
            'workload_plan_digest': load_workload_plan(work / 'workload.json').digest,
            'actions': [{'control_ordinal': 1, 'opcode': 'CANCEL',
                         'issuer_user_id': 0, 'issuer_task_seq': 0,
                         'target_user_id': 0, 'target_task_seq': 0, 'target_repair_round': 0,
                         'trigger': {'kind': 'AFTER_BATCH_FREEZE', 'event_user_id': 0,
                                     'event_task_seq': 0, 'event_repair_round': 0}}]}))
        config['agent']['control_plan'] = 'control.json'
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    _, result, metrics = run_serving_fixture(work, 'fault', fault_index=0,
        fault_kind=fault_kind, weight_policy='cached', delay_read_cycles=10000)
    facts = read_facts_document(work / 'fault/gate6_facts.tsv')
    assert result['terminal'] == 'ERROR_DRAINED'
    assert metrics['metadata_reads_validated'] == 3
    assert result['cache_reservation']['candidate_attaches'] == 1
    freezes = [row for row in facts.events if row['kind'] == 'BATCH_FREEZE']
    assert {row['request_id'] for row in freezes if row['tick'] == freezes[0]['tick']} == {1, 4294967297}
    failures = [row['failure'] for row in result['instances'] if row.get('failure')]
    assert len(failures) == (2 if fault_kind == 'read' else 1)
    assert len({row['source_wire'] for row in failures}) == 1
    cq = [row for row in facts.events if row['kind'] == 'CQ_CONSUME']
    assert len(cq) == len({row['request_id'] for row in cq}) == 3 + int(cancel_member)
    statuses = {row['request_id']: row['status'] for row in cq}
    assert statuses[1] == ('CANCELLED' if cancel_member else 'ERROR')
    assert statuses[4294967297] == 'ERROR'
    if cancel_member:
        assert statuses[2] == 'SUCCESS'
        assert next(row for row in failures if row['instance'] == 1)['cancel_tombstones'] == [1]
    assert statuses[8589934593] == ('ERROR' if fault_kind == 'read' else 'SUCCESS')
    fills = [row for row in result['dma_timings'] if row['domain'] == 2]
    assert len(fills) == 1
    bursts = [row for row in result['burst_timings'] if row['core_id'] == fills[0]['core_id']
              and row['channel'] == 'AR' and row['ar_aw_tick'] == fills[0]['first_ar_tick']]
    assert len(bursts) == 1
    assert bursts[0]['response_tick'] == fills[0]['last_r_tick']
    assert sum(row['read_bursts'] for row in result['transport'] if row['domain'] == 2) == int(fault_kind != 'read')
    assert sum(row['read_discarded_bytes'] for row in result['transport'] if row['domain'] == 2) == (128 if fault_kind == 'read' else 0)
    for cache in result['moe']['cache_state']:
        assert cache['live_tokens'] == cache['live_obligations'] == cache['pending_subscribers'] == 0
    assert sum(cache['tombstones'] for cache in result['moe']['cache_state']) == int(fault_kind == 'read')
