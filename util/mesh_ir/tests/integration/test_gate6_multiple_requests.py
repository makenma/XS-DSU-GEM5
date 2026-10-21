import copy
import json
import struct

import pytest
import yaml

from mesh_ir.abi.decoder import decode_program
from mesh_ir.gate3_oracle import read_facts_document
from .serving_runtime import FIXTURES, run_serving_fixture, write_serving_fixture, write_shared_fixture, PROBE


@pytest.mark.parametrize("fault_index", [-1, 0])
def test_two_requests_finish_before_program_drain(tmp_path, fault_index):
    program = decode_program((FIXTURES / 'serving_two_tokens.mshb').read_bytes())
    work = write_serving_fixture(tmp_path, program)
    workload_path = work / 'workload.json'
    workload = json.loads(workload_path.read_text())
    second = copy.deepcopy(workload['users'][0])
    second['user_id'] = 1
    task = second['tasks'][0]
    task['session_id'] = task['kv_handle'] = 2
    task['rounds'][0]['workload_plan_item_id'] = 2
    task['rounds'][0]['input_content_digest'] = '6d' * 32
    workload['users'].append(second)
    workload_path.write_text(json.dumps(workload))
    config_path = work / 'config.yaml'
    config = yaml.safe_load(config_path.read_text())
    config['agent']['users'] = 2
    config['serving']['max_active_sequences'] = 2
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    facts, result, metrics = run_serving_fixture(work, 'two_requests', fault_index)
    assert result['terminal'] == ('DONE' if fault_index < 0 else 'ERROR_DRAINED')
    assert metrics['metadata_reads_validated'] == 2
    assert metrics['generated_code_reads_validated'] == (2 if fault_index < 0 else 1)
    assert metrics['core_starts'] == 2
    assert result['terminal_error_core'] == (-1 if fault_index < 0 else 0)
    document = read_facts_document(work / 'two_requests' / 'gate6_facts.tsv')
    assert document.fatal is None
    completions = [event for event in document.events if event['kind'] == 'CQ_ASSIGN']
    assert [event['status'] for event in completions] == (
        ['SUCCESS', 'SUCCESS'] if fault_index < 0 else ['ERROR', 'SUCCESS'])
    assert len({event['request_id'] for event in completions}) == 2
    reads = [event for event in document.events
             if event['kind'] == 'LOCAL_READ' and event['object'] == 'GENERATED_CODE_READ']
    assert len({event['address'] for event in reads}) == (2 if fault_index < 0 else 1)
    assert len({event['status'] for event in reads}) == len(reads)
    assert result['terminal_tick'] >= max(event['tick'] for event in completions)


def run_shared_case(tmp_path, ranks, reverse, fault_index, moe=False, weight_shift=0, weight_policy="streamed"):
    program, work = write_shared_fixture(tmp_path, ranks, reverse, moe)
    entry = PROBE
    if weight_shift:
        entry = work / 'weight_binding_probe.py'
        entry.write_text(
            "import sys, runpy\n"
            "sys.path.insert(0, 'util/mesh_ir')\n"
            "from dataclasses import replace\n"
            "import mesh_ir.host_bindings as bindings\n"
            "original = bindings.build_host_binding_plans\n"
            "def shifted(program, bases):\n"
            "    return tuple(replace(plan, requirements=tuple(\n"
            f"        replace(row, platform_address=row.platform_address + {weight_shift})\n"
            "        if row.kind == bindings.WEIGHT_EXTERNAL else row\n"
            "        for row in plan.requirements)) for plan in original(program, bases))\n"
            "bindings.build_host_binding_plans = shifted\n"
            f"runpy.run_path({str(PROBE)!r}, run_name='__m5_main__')\n")
    _, result, metrics = run_serving_fixture(work, 'shared', fault_index, entry=entry, weight_policy=weight_policy)
    document = read_facts_document(work / 'shared' / 'gate6_facts.tsv')
    assert document.fatal is None
    assert result['terminal'] == ('DONE' if fault_index < 0 else 'ERROR_DRAINED')
    assert len(result['instances']) == (3 if fault_index < 0 else 1)
    selected = [row.instance_profile_id for row in program.agent_instance_profiles
                if row.member_count == 2 and tuple(
                    member.expected_logical_source_rank for member in
                    program.agent_instance_member_bindings[
                        row.member_binding_first:row.member_binding_first + row.member_count]) == ranks]
    assert [phase['instance_profile_id'] for phase in document.serving_phases] == [
        identifier for identifier in selected[:3 if fault_index < 0 else 0]
        for _ in range(2)]
    assert metrics['metadata_reads_validated'] == 2
    if fault_index < 0:
        assert metrics['generated_code_reads_validated'] == 2
    else:
        assert not any(event['kind'] == 'LOCAL_READ' and event['object'] ==
            'GENERATED_CODE_READ' for event in document.events)
    assert metrics['core_starts'] == 2
    completions = [event for event in document.events if event['kind'] == 'CQ_ASSIGN']
    assert len(completions) == 2
    assert all(event['status'] == ('SUCCESS' if fault_index < 0 else 'ERROR')
               for event in completions)


    return result


@pytest.mark.parametrize("ranks,reverse", [((0, 0), False), ((0, 1), False),
                                           ((1, 0), False), ((1, 1), False),
                                           ((0, 1), True)])
@pytest.mark.parametrize("fault_index", [-1, 0])
def test_two_requests_share_every_concrete_phase(tmp_path, ranks, reverse, fault_index):
    run_shared_case(tmp_path, ranks, reverse, fault_index)


@pytest.mark.parametrize("weight_shift", [0, 4096])
def test_serving_materializes_shared_moe_from_its_frozen_members(tmp_path, weight_shift):
    result = run_shared_case(tmp_path, (0, 0), False, -1, moe=True, weight_shift=weight_shift)
    from mesh_ir.serving_programs import W_BASE
    from mesh_ir.architecture import load_arch
    weight_base = load_arch(tmp_path / 'arch.yaml').regions[0].base + W_BASE
    weights = [row for row in result['burst_timings'] if row['channel'] == 'AR'
               and weight_base <= row['address'] < weight_base + 8192]
    assert len(weights) == 4
    assert {row['address'] for row in weights} == {weight_base + weight_shift}
    from .serving_moe_oracle import assert_shared_moe_image
    assert_shared_moe_image(tmp_path, 'streamed')
    traffic = [row for row in result['transport'] if row['domain'] == 1]
    assert traffic
    assert {row['region_group_id'] for row in traffic} == {2}
    assert sum(row['read_bytes'] for row in traffic) == 256
    assert sum(row['fill_bytes'] for row in traffic) == 32 * 64
    assert all(row['error_code'] == 0 for row in traffic)


@pytest.mark.parametrize("active_sequences", [1, 2])
def test_finite_active_queue_drains_six_users(tmp_path, active_sequences):
    program = decode_program((FIXTURES / 'serving_two_tokens.mshb').read_bytes())
    work = write_serving_fixture(tmp_path, program)
    workload_path = work / 'workload.json'
    workload = json.loads(workload_path.read_text())
    template = workload['users'][0]
    workload['users'] = []
    for index in range(6):
        user = copy.deepcopy(template)
        user['user_id'] = index
        task = user['tasks'][0]
        task['session_id'] = task['kv_handle'] = index + 1
        task['rounds'][0]['workload_plan_item_id'] = index + 1
        task['rounds'][0]['input_content_digest'] = bytes([index + 1] * 32).hex()
        workload['users'].append(user)
    workload_path.write_text(json.dumps(workload))
    config_path = work / 'config.yaml'
    config = yaml.safe_load(config_path.read_text())
    config['agent']['users'] = 6
    config['serving'].update(max_request_contexts=6, max_active_sequences=active_sequences,
                             kv_admission_wait_entries=6)
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    _, result, metrics = run_serving_fixture(work, 'finite')
    document = read_facts_document(work / 'finite' / 'gate6_facts.tsv')
    assert document.fatal is None
    assert result['terminal'] == 'DONE'
    assert metrics['metadata_reads_validated'] == 6
    assert metrics['generated_code_reads_validated'] == 6
    completions = [event for event in document.events if event['kind'] == 'CQ_ASSIGN']
    assert len(completions) == 6
    assert len({event['request_id'] for event in completions}) == 6
    assert all(event['status'] == 'SUCCESS' for event in completions)


@pytest.mark.parametrize("weight_shift", [0, 4096])
def test_shared_moe_cache_deduplicates_expert_weights(tmp_path, weight_shift):
    result = run_shared_case(tmp_path, (0, 0), False, -1, moe=True,
                             weight_shift=weight_shift, weight_policy="cached")
    from mesh_ir.serving_programs import W_BASE
    from mesh_ir.architecture import load_arch
    weight_base = load_arch(tmp_path / 'arch.yaml').regions[0].base + W_BASE
    weights = [row for row in result['burst_timings'] if row['channel'] == 'AR'
               and weight_base <= row['address'] < weight_base + 8192]
    assert len(weights) == 3
    assert {row['address'] for row in weights} == {weight_base + weight_shift}
    assert sum(row['read_bytes'] for row in result['transport'] if row['domain'] == 2) == 128
    from .serving_moe_oracle import assert_shared_moe_image
    assert_shared_moe_image(tmp_path, 'cached')


def run_crossing_batches(tmp_path, fault=False, cancel=False):
    from mesh_ir.architecture import load_arch
    from mesh_ir.serving_programs import moe_full_view_serving_program
    from .serving_runtime import ARCH_YAML
    arch_path = tmp_path / 'bounded_arch.yaml'
    arch_doc = yaml.safe_load(ARCH_YAML.with_name('mesh_1x2_moe.yaml').read_text())
    cache = next(row for row in arch_doc['core']['sram']['partitions']
                 if row['kind'] == 'WEIGHT_CACHE')
    cache.update(bytes=4096, metadata_entries=1, max_pinned_entries=1)
    arch_path.write_text(yaml.safe_dump(arch_doc))
    program = moe_full_view_serving_program(load_arch(arch_path))
    work = write_serving_fixture(tmp_path, program, arch_path)
    workload_path = work / 'workload.json'
    workload = json.loads(workload_path.read_text())
    template = workload['users'][0]
    workload['users'] = []
    for index in range(3):
        user = copy.deepcopy(template)
        user['user_id'] = index
        task = user['tasks'][0]
        task['session_id'] = task['kv_handle'] = index + 1
        row = task['rounds'][0]
        row.update(workload_plan_item_id=index + 1, output_tokens=1, generated_code_bytes=128,
                   output_capacity_bytes=128, kv_required_tokens_after_round=9,
                   input_content_digest=bytes([index + 1] * 32).hex())
        workload['users'].append(user)
    workload_path.write_text(json.dumps(workload))
    surrogate_path = work / 'surrogate.json'
    surrogate = json.loads(surrogate_path.read_text())
    surrogate['profiles'][0].update(output_tokens=1, output_bytes=128)
    surrogate_path.write_text(json.dumps(surrogate))
    config_path = work / 'config.yaml'
    config = yaml.safe_load(config_path.read_text())
    config['agent']['users'] = 3
    if cancel:
        from mesh_ir.agent_workload import load_workload_plan
        control = {"schema": "agent_control_plan_v1", "version": 1,
            "workload_plan_digest": load_workload_plan(workload_path).digest,
            "actions": [{"control_ordinal": 1, "opcode": "CANCEL",
                "trigger": {"kind": "AFTER_SQ_ACCEPT", "event_user_id": 1,
                            "event_task_seq": 0, "event_repair_round": 0},
                "issuer_user_id": 1, "issuer_task_seq": 0,
                "target_user_id": 1, "target_task_seq": 0, "target_repair_round": 0}]}
        (work / 'control.json').write_text(json.dumps(control))
        config['agent']['control_plan'] = 'control.json'
    config['serving'].update(max_active_sequences=3, max_prefill_tokens_per_batch=8,
                             max_decode_sequences_per_batch=1, batch_wait_cycles=0)
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    _, result, metrics = run_serving_fixture(work, 'crossing', fault_index=0 if fault else -1,
        fault_kind='read', weight_policy='cached', delay_read_cycles=2000)
    if cancel:
        assert result['terminal'] == 'DONE'
        assert metrics['metadata_reads_validated'] == 3
        assert metrics['generated_code_reads_validated'] == 2
        assert result['cache_reservation']['starts'] == 7
        assert len(result['instances']) == 7
        document = read_facts_document(work / 'crossing' / 'gate6_facts.tsv')
        completions = [row for row in document.events if row['kind'] == 'CQ_ASSIGN']
        assert len(completions) == 4
        assert [row['status'] for row in document.events
                if row['kind'] == 'CQ_CONSUME' and row['request_id'] == 4294967297] == ['CANCELLED']
        attached = next(row for row in result['serving_candidate_events']
                        if row['event'] == 'ATTACHED')
        assert sum(row['event'] == 'STARTED' and row['instance'] == attached['instance']
                   for row in result['serving_candidate_events']) == 1
        cache = result['moe']['cache_state'][0]
        assert cache['live_tokens'] == cache['live_obligations'] == cache['pending_subscribers'] == 0
        assert len(result['moe']['cache_fills']) == 1
        return
    if fault:
        assert result['terminal'] == 'ERROR_DRAINED'
        assert len(result['instances']) == 3
        assert sum(bool(row['cores']) for row in result['instances']) == 1
        assert result['cache_reservation']['starts'] == 1
        assert result['cache_reservation']['candidate_attaches'] == 1
        assert result['cache_reservation']['candidate_waits'] == 1
        assert result['cache_reservation']['prestart_failures'] == 2
        document = read_facts_document(work / 'crossing' / 'gate6_facts.tsv')
        completions = [row for row in document.events if row['kind'] == 'CQ_ASSIGN']
        assert len(completions) == 3
        assert not any(row['kind'] == 'LOCAL_READ' and row['object'] ==
                       'GENERATED_CODE_READ' for row in document.events)
        assert metrics['metadata_reads_validated'] == 3
        assert [row['status'] for row in document.events
                if row['kind'] == 'CQ_CONSUME'] == ['ERROR'] * 3
        failures = [row['failure'] for row in result['instances']]
        assert {row['detail'] for row in failures} == {0x00020001}
        assert len({row['source_wire'] for row in failures}) == 1
        expected_source = struct.pack('<HHBBHIIIIHHIII',
            2, 0, 2, 5, 0, 0, 0, 0, 0, 0, 1, 0, 0, 1).hex()
        assert {row['source_wire'] for row in failures} == {expected_source}
        assert all(row['status'] == 'ERROR' for row in completions)
        assert len({row['request_id'] for row in completions}) == 3
        cache = result['moe']['cache_state'][0]
        assert cache['live_tokens'] == cache['live_obligations'] == cache['pending_subscribers'] == 0
        assert cache['tombstones'] == 1
        replay = cache['replay_state']
        assert replay is not None
        assert len(replay['failure_tombstones']) == 1
        tombstone = replay['failure_tombstones'][0]
        assert tombstone['weight_tag_index'] == 0
        assert tombstone['first_error']['error_code'] == 0x00020001
        assert tombstone['first_error']['source_key_wire'] == expected_source
        assert tombstone['first_error']['tick'] > 0
        return
    assert result['terminal'] == 'DONE'
    assert metrics['metadata_reads_validated'] == 3
    assert metrics['generated_code_reads_validated'] == 3
    assert len(result['instances']) == 9
    cache = result['cache_reservation']
    assert cache['candidate_attaches'] >= 1
    assert cache['candidate_waits'] >= 1
    assert cache['candidate_resumes'] == cache['candidate_waits']
    assert cache['starts'] == 9
    assert cache['identity_reassignments'] == 0
    assert not cache['pending']
    assert sum(row['read_bytes'] for row in result['transport'] if row['domain'] == 2) == 128
    events = result['serving_candidate_events']
    starts = [row for row in events if row['event'] == 'STARTED']
    assert len(starts) == len({row['instance'] for row in starts}) == 9
    fills = result['moe']['cache_fills']
    assert len(fills) == 1
    attached = [row for row in events if row['event'] == 'ATTACHED']
    waiting = [row for row in events if row['event'] == 'RESOURCE_WAIT']
    resumed = [row for row in events if row['event'] == 'RESUMED']
    assert attached and waiting and resumed
    assert min(row['tick'] for row in attached) < fills[0]['done_tick']
    for wait in waiting:
        resume = next(row for row in resumed if row['instance'] == wait['instance'])
        start = next(row for row in starts if row['instance'] == wait['instance'])
        assert wait['tick'] < fills[0]['done_tick'] <= resume['tick'] <= start['tick']
    state = result['moe']['cache_state'][0]
    assert state['tokens_created'] == state['token_releases'] == 3
    assert state['woken_subscribers'] == 2
    assert state['live_tokens'] == state['live_obligations'] == state['pending_subscribers'] == 0


def test_crossing_batches_attach_wait_resume_and_start_once(tmp_path):
    run_crossing_batches(tmp_path)


def test_crossing_cache_failure_drains_active_and_rejects_queued_members(tmp_path):
    run_crossing_batches(tmp_path, fault=True)


def test_cancel_frozen_attached_member_preserves_instance_and_shared_fill(tmp_path):
    run_crossing_batches(tmp_path, cancel=True)
