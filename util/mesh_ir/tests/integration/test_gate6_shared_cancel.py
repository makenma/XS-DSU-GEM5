import json

import pytest
import yaml

from mesh_ir.agent_workload import load_workload_plan
from mesh_ir.gate3_oracle import read_facts_document
from .serving_runtime import run_serving_fixture, write_shared_fixture


@pytest.mark.parametrize('cancelled', [(0,), (0, 1)])
def test_frozen_shared_members_cancel_without_changing_the_physical_fill(tmp_path, cancelled):
    _, work = write_shared_fixture(tmp_path, moe=True)
    _, baseline, baseline_metrics = run_serving_fixture(work, 'baseline', weight_policy='cached',
                                                       delay_read_cycles=10000)
    workload = load_workload_plan(work / 'workload.json')
    actions = [{
        'control_ordinal': ordinal + 1, 'opcode': 'CANCEL',
        'issuer_user_id': user, 'issuer_task_seq': 0,
        'target_user_id': user, 'target_task_seq': 0, 'target_repair_round': 0,
        'trigger': {'kind': 'AFTER_BATCH_FREEZE', 'event_user_id': user,
                    'event_task_seq': 0, 'event_repair_round': 0},
    } for ordinal, user in enumerate(cancelled)]
    (work / 'control.json').write_text(json.dumps({
        'schema': 'agent_control_plan_v1', 'version': 1,
        'workload_plan_digest': workload.digest, 'actions': actions}))
    config_path = work / 'config.yaml'
    config = yaml.safe_load(config_path.read_text())
    config['agent']['control_plan'] = 'control.json'
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    _, result, metrics = run_serving_fixture(work, 'cancel', weight_policy='cached', delay_read_cycles=10000)
    facts = read_facts_document(work / 'cancel/gate6_facts.tsv')
    assert result['terminal'] == 'DONE'
    assert metrics['metadata_reads_validated'] == 2
    assert metrics.get('generated_code_reads_validated', 0) == 2 - len(cancelled)
    assert not any(row.get('failure') for row in result['instances'])
    cq = [row for row in facts.events if row['kind'] == 'CQ_CONSUME']
    assert len(cq) == 2 + len(cancelled)
    assert len({row['request_id'] for row in cq}) == len(cq)
    for user in range(2):
        request = (user << 32) + 1
        terminal = next(row for row in cq if row['request_id'] == request)
        assert terminal['status'] == ('CANCELLED' if user in cancelled else 'SUCCESS')
    assert len(result['moe']['cache_fills']) == 1
    assert sum(row['read_bytes'] for row in result['transport'] if row['domain'] == 2) == 128
    for cache in result['moe']['cache_state']:
        assert cache['live_tokens'] == cache['live_obligations'] == cache['pending_subscribers'] == 0
        assert cache['tombstones'] == 0
    assert metrics['kv_cached_tokens'] == (8 if len(cancelled) == 2 else 9)
    shared_prefill = facts.serving_phases[:2]
    assert {row['request_id'] for row in shared_prefill} == {1, 4294967297}
    assert len({row['instance_profile_id'] for row in shared_prefill}) == 1
    assert (work / 'cancel/moe_instance_1_layer_2.bin').read_bytes() == (
        work / 'baseline/moe_instance_1_layer_2.bin').read_bytes()
    assert baseline['terminal'] == 'DONE'
    if len(cancelled) == 1:
        for field in ('generated_code_digest_hi', 'generated_code_digest_lo'):
            assert metrics[field] == baseline_metrics[field]


def test_shared_publish_cancel_keeps_other_member_output_and_frozen_route(tmp_path):
    from mesh_ir.generated import abi as A
    from mesh_ir.serving_profiles import apply_request_profile_keys
    from mesh_ir.serving_publish_oracle import validate_publish_segments

    from mesh_ir.architecture import load_arch
    from mesh_ir.serving_programs import moe_full_view_serving_program
    from .serving_runtime import ARCH_YAML

    arch = load_arch(ARCH_YAML.with_name('mesh_1x2_moe.yaml'))
    program = apply_request_profile_keys(moe_full_view_serving_program(
        arch, output_tokens=2, publish_max_burst_beats=1))
    program, work = write_shared_fixture(tmp_path, moe=True, output_tokens=2, program=program)
    publish = next(row for row in program.agent_instance_profiles
                   if row.phase == A.PHASE.PUBLISH and row.member_count == 2)
    config_path = work / 'config.yaml'
    config = yaml.safe_load(config_path.read_text())
    output = config['serving']['address_map']['output_arena']['base']
    delays = [{'core': 0, 'kind': 'write', 'index': index, 'address': output, 'cycles': 10000}
              for index in range(6, 22)]
    _, baseline, baseline_metrics = run_serving_fixture(work, 'baseline', weight_policy='cached',
                                                       extra_latencies=delays)
    workload_plan = load_workload_plan(work / 'workload.json')
    (work / 'control.json').write_text(json.dumps({
        'schema': 'agent_control_plan_v1', 'version': 1,
        'workload_plan_digest': workload_plan.digest,
        'actions': [{'control_ordinal': 1, 'opcode': 'CANCEL',
                     'issuer_user_id': 0, 'issuer_task_seq': 0,
                     'target_user_id': 0, 'target_task_seq': 0, 'target_repair_round': 0,
                     'trigger': {'kind': 'AFTER_FIRST_OUTPUT_CHUNK', 'event_user_id': 0,
                                 'event_task_seq': 0, 'event_repair_round': 0}}]}))
    config['agent']['control_plan'] = 'control.json'
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    _, result, metrics = run_serving_fixture(work, 'cancel', weight_policy='cached',
                                            extra_latencies=delays)
    facts = read_facts_document(work / 'cancel/gate6_facts.tsv')
    assert baseline['terminal'] == result['terminal'] == 'DONE'
    assert len(result['instances']) == 4
    cq = [row for row in facts.events if row['kind'] == 'CQ_CONSUME']
    assert len(cq) == len({row['request_id'] for row in cq}) == 3
    assert {row['request_id']: row['status'] for row in cq} == {
        1: 'CANCELLED', 2: 'SUCCESS', 4294967297: 'SUCCESS'}
    segments = facts.publish_segments
    members = {ordinal: (request, output + ordinal * 256)
               for ordinal, request in enumerate((1, 4294967297))}
    assert validate_publish_segments(program, publish.instance_profile_id, 4, members, 32, segments) == {
        1: 128, 4294967297: 256}
    assert facts.cancel_sources[1] == dict(instance=4, phase=3)
    assert any(row['kind'] == 3 and row['request'] == 1 for row in segments)
    assert not any(row['kind'] == 3 and row['request'] == 4294967297 for row in segments)
    assert (work / 'cancel/moe_instance_1_layer_2.bin').read_bytes() == (
        work / 'baseline/moe_instance_1_layer_2.bin').read_bytes()
    assert metrics['metadata_reads_validated'] == 2
    for field in ('generated_code_digest_hi', 'generated_code_digest_lo'):
        assert metrics[field] == baseline_metrics[field]
