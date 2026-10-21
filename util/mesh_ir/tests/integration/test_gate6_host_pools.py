import copy
import json
import struct
import hashlib
import itertools
import dataclasses

import pytest
from collections import Counter

import yaml

from mesh_ir.architecture import load_arch
from mesh_ir.agent_workload import load_workload_plan
from mesh_ir.moe_provider import FrozenToken, uniform_select, population_digest
from mesh_ir.model import canonical_json_bytes
from mesh_ir.moe_uid import SemanticTokenUid
from mesh_ir.generated import abi as A
from mesh_ir.gate3_oracle import read_facts_document
from mesh_ir.moe_overlay_runtime import (
    OVERLAY_IMAGE_HEADER_BYTES, OVERLAY_IMAGE_RECORD_BYTES,
    OVERLAY_IMAGE_SCRATCH_BYTES, OVERLAY_IMAGE_PAYLOAD_BYTES,
)
from mesh_ir.serving_programs import moe_full_view_serving_program
from mesh_ir.serving_profiles import apply_request_profile_keys
from .serving_runtime import ARCH_YAML, run_serving_fixture, write_serving_fixture


def route_evidence(directory):
    selection, histogram, batches = {}, Counter(), []
    for path in sorted(directory.glob('moe_instance_*_layer_*.bin'), key=lambda p: int(p.name.split('_')[2])):
        blob = path.read_bytes()
        assert blob[:4] == b'MOEV'
        version, layer, records, refs, scratch, mode, payloads = struct.unpack_from('<7I', blob, 4)
        assert version == 4 and refs == 32
        descriptors = set()
        for index in range(records):
            kind, region, ordinal, owner, subtype = struct.unpack_from(
                '<H4I', blob, OVERLAY_IMAGE_HEADER_BYTES + index * OVERLAY_IMAGE_RECORD_BYTES)
            if kind == A.MESH_OBJECT_KIND.DESCRIPTOR and subtype == A.MOE_DESCRIPTOR_KIND.ROUTE_FILL:
                descriptors.add((region, ordinal))
        table = OVERLAY_IMAGE_HEADER_BYTES + records * OVERLAY_IMAGE_RECORD_BYTES + scratch * OVERLAY_IMAGE_SCRATCH_BYTES
        data = table + payloads * OVERLAY_IMAGE_PAYLOAD_BYTES
        members = set()
        for index in range(payloads):
            region, ordinal, offset, count = struct.unpack_from('<IIQQ', blob, table + index * OVERLAY_IMAGE_PAYLOAD_BYTES)
            if (region, ordinal) not in descriptors:
                continue
            assert count % A.MOE_RUNTIME_ROUTE_ENTRY_BYTES == 0 and data + offset + count <= len(blob)
            for row in A.MOE_RUNTIME_ROUTE_ENTRY_FORMAT.iter_unpack(blob[data + offset:data + offset + count]):
                uid, request, rank, token, topk, selected, assigned, core, disposition, reserved = row
                key = (uid.hex(), topk)
                assert key not in selection
                assert reserved == bytes(7)
                selection[key] = (selected, layer)
                histogram[(selected, assigned, disposition)] += 1
                members.add(request)
        batches.append(tuple(sorted(members)))
    assert selection and all(batches)
    return selection, histogram, batches


def provider_artifact(work, program, workload, kind):
    members = []
    for user in workload['users']:
        for task in user['tasks']:
            members.append([SemanticTokenUid(task['rounds'][0]['workload_plan_item_id'],
                user['user_id'], task['task_seq'], 0, 1, 0, ordinal) for ordinal in range(8)])
    document = dict(schema='moe_' + kind + '_v1', version=1,
        workload_plan_digest=load_workload_plan(work / 'workload.json').digest,
        mesh_program_digest=program.semantic_sha256,
        layers=[dict(layer_id=layer.layer_id, expert_count=layer.expert_count, top_k=layer.top_k)
                for layer in program.moe_layer_specs])
    if kind == 'route_replay':
        entries = []
        for layer in program.moe_layer_specs:
            for member in members:
                for uid in member:
                    fields = dataclasses.asdict(uid)
                    del fields['reserved0'], fields['reserved1']
                    for slot in range(layer.top_k):
                        entries.append(dict(layer_id=layer.layer_id, uid=fields, logical_source_rank=0,
                            topk_slot=slot, selected_expert_id=(uid.token_ordinal + uid.workload_plan_item_id + slot) % layer.expert_count))
        document['entries'] = entries
    else:
        records = []
        document['route_window_tokens'] = 8
        for layer, size in zip(program.moe_layer_specs, (1, 2)):
            assert layer.expert_count == layer.top_k == 2
            for group in itertools.combinations(members, size):
                population = sorted([(uid, 0) for member in group for uid in member], key=lambda row: row[0].sort_key())
                records.append(dict(layer_id=layer.layer_id, phase=1, window_ordinal=0,
                    population_digest=population_digest(layer.layer_id, 1, 0, population),
                    source_token_counts=[len(population)], source_expert_counts=[[len(population)] * 2]))
        document['records'] = records
    document['route_profile_digest'] = hashlib.sha256(canonical_json_bytes(document)).hexdigest()
    path = work / 'provider.json'
    path.write_text(json.dumps(document))
    return path


@pytest.mark.parametrize('provider', ['uniform_smoke', 'route_replay', 'histogram_replay'])
def test_finite_host_pools_preserve_business_and_token_selection(tmp_path, provider):
    run_host_pool_comparison(tmp_path, provider)


def run_host_pool_comparison(tmp_path, provider, constrained=False,
                             aggregate_queue_entries=None):
    arch = ARCH_YAML.with_name('mesh_1x2_moe.yaml')
    program = apply_request_profile_keys(moe_full_view_serving_program(load_arch(arch)))
    outcomes = []
    for arm in ('finite', 'large', 'repeat'):
        large = arm == 'large'
        work = tmp_path / arm
        write_serving_fixture(work, program, arch)
        workload = json.loads((work / 'workload.json').read_text())
        original = workload['users'][0]
        workload['users'] = []
        for user_id in range(12):
            user = copy.deepcopy(original)
            user['user_id'] = user_id
            user['tasks'] = [copy.deepcopy(original['tasks'][0]) for _ in range(2)]
            for task_seq, task in enumerate(user['tasks']):
                item = user_id * 2 + task_seq + 1
                task.update(task_seq=task_seq, session_id=item, kv_handle=item)
                task['rounds'][0].update(workload_plan_item_id=item, output_tokens=1,
                    generated_code_bytes=128, output_capacity_bytes=128, kv_required_tokens_after_round=9)
            workload['users'].append(user)
        (work / 'workload.json').write_text(json.dumps(workload))
        surrogate = json.loads((work / 'surrogate.json').read_text())
        surrogate['profiles'][0].update(output_tokens=1, output_bytes=128)
        (work / 'surrogate.json').write_text(json.dumps(surrogate))
        config = yaml.safe_load((work / 'config.yaml').read_text())
        if aggregate_queue_entries is not None:
            config['runtime'].update(mode='FAST_EVENT',
                fast_event={'queue_entries': aggregate_queue_entries})
        config['agent'].update(users=12, release_session_on_task_terminal=True)
        config['serving'].update(max_request_contexts=12, max_active_sequences=12,
            batch_wait_cycles=100000, kv_session_record_entries=24, kv_session_tombstone_entries=24,
            kv_admission_wait_entries=12, session_release_waiter_entries=12,
            seen_request_id_entries=128, host_issued_request_id_entries=128,
            driver_submission_table_entries=16, early_completion_cache_entries=16,
            batch_failure_record_entries=12, cq_obligation_entries=12, terminal_result_queue_depth=12)
        services = config['agent_axi_driver']['synthetic_host_services']
        services.update(agent_object_table_entries=256, release_waiter_entries=12,
                        host_admission_registry_entries=24, sq_submit_ready_queue_entries=64, service_queue_depth=12)
        if large:
            services.update(compile_slots=16, test_slots=16, log_parse_slots=16)
        if constrained:
            config['agent_axi_driver']['remote_host_memory'].update(
                target_request_queue_depth=2, target_response_queue_depth=2)
        (work / 'config.yaml').write_text(yaml.safe_dump(config, sort_keys=False))
        extra = ['--sim-tick-limit', '50000000000']
        if constrained:
            extra.extend(['--axi-source-fifo-depths', '2,2,2,2,2',
                          '--axi-message-buffer-depths', '2,2,2,2,2',
                          '--garnet-buffers-per-vnet', '2,2,2,2,2'])
        if provider != 'uniform_smoke':
            artifact = provider_artifact(work, program, workload, provider)
            extra.extend(['--moe-provider', provider, '--moe-provider-artifact', str(artifact)])
        _, result, metrics = run_serving_fixture(work, 'run', weight_policy='cached', extra_args=extra)
        assert result['terminal'] == 'DONE'
        assert metrics['metadata_reads_validated'] == metrics['generated_code_reads_validated'] == 24
        assert metrics['completed_tasks'] == 24
        assert metrics['kv_session_records'] == metrics['host_release_waiters'] == 0
        assert metrics['frontend_drained'] == 1
        for field in ('live_contexts', 'live_cq_obligations', 'live_sq_intakes',
                      'live_submissions', 'msi_rob_entries'):
            assert metrics[field] == 0
        assert not result['watchdog_fired']
        facts = read_facts_document(work / 'run' / 'gate6_facts.tsv')
        cq = {(e['request_id'], e['status']) for e in facts.events if e['kind'] == 'CQ_CONSUME'}
        routes, histogram, batches = route_evidence(work / 'run')
        layer = program.moe_layer_specs[1]
        tokens = [FrozenToken(SemanticTokenUid(
            task['rounds'][0]['workload_plan_item_id'], user['user_id'], task['task_seq'], 0, 1, 0, token),
            str(task['rounds'][0]['workload_plan_item_id']), 0, token)
            for user in workload['users'] for task in user['tasks'] for token in range(8)]
        selection = uniform_select(layer, tokens,
            bytes.fromhex(load_workload_plan(work / 'workload.json').digest), config['runtime']['master_seed'])
        expected = {(row.uid.encode().hex(), row.topk_slot): (row.selected_expert_id, layer.layer_id)
                    for row in selection.routes}
        if provider != 'uniform_smoke':
            expected = {(token.uid.encode().hex(), slot):
                (slot if provider == 'histogram_replay' else
                 (token.uid.token_ordinal + token.uid.workload_plan_item_id + slot) % layer.expert_count,
                 layer.layer_id) for token in tokens for slot in range(layer.top_k)}
            assert len(list((work / 'run').glob('provider_selection_*.json'))) == 12
        assert routes == expected
        for user in workload['users']:
            releases = sorted(e['tick'] for e in facts.events
                              if e['kind'] == 'RELEASE_SUCCESS' and e['request_id'] >> 32 == user['user_id'])
            thinks = sorted(e['tick'] for e in facts.events
                            if e['kind'] == 'TASK_THINK_READY' and e['request_id'] >> 32 == user['user_id'])
            assert len(releases) == len(thinks) == 2
            assert thinks[1] > releases[0]
        fills = result['moe']['cache_fills']
        assert len(fills) == 1 and fills[0]['committed_bytes'] == 128
        weight_owners = {expert.core_id for expert in program.moe_expert_specs}
        for row in result['moe']['cache_state']:
            assert row['live_tokens'] == row['live_obligations'] == row['pending_subscribers'] == 0
            assert row['tokens_created'] == row['token_releases'] == (
                12 if row['core_id'] in weight_owners else 0)
        outcomes.append((workload, cq, routes, histogram, batches, facts, result))
    left, right, repeated = outcomes
    assert left[:5] == repeated[:5]
    assert left[6]["terminal_tick"] == repeated[6]["terminal_tick"]
    assert left[:4] == right[:4]
    starts = lambda facts: [(e['request_id'], e['tick']) for e in facts.events
                            if e['kind'] == 'HOST_STAGE_START']
    assert starts(left[5]) != starts(right[5])
    (tmp_path / 'comparison.json').write_text(json.dumps({
        'selection_entries': len(left[2]),
        'provider': provider, 'repeat_identical': True,
        'finite_weight_fill_bytes': sum(row['committed_bytes'] for row in left[6]['moe']['cache_fills']),
        'large_weight_fill_bytes': sum(row['committed_bytes'] for row in right[6]['moe']['cache_fills']), 'histogram_entries': len(left[3]),
        'finite_batches': left[4], 'large_batches': right[4],
        'finite_terminal_tick': left[6]['terminal_tick'], 'large_terminal_tick': right[6]['terminal_tick'],
    }, sort_keys=True, indent=2))
    return outcomes
