import json

import pytest
import yaml

from mesh_ir.architecture import load_arch
from mesh_ir.gate3_oracle import read_facts_document
from mesh_ir.serving_profiles import apply_request_profile_keys
from mesh_ir.serving_programs import repair_serving_program
from .serving_runtime import ARCH_YAML, REPO, WEIGHT_DIGEST, run_serving_fixture, write_serving_fixture


def repair_fixture(work):
    program = apply_request_profile_keys(repair_serving_program(load_arch(ARCH_YAML)))
    write_serving_fixture(work, program)
    workload = json.loads((REPO / 'tests/gem5/ai_mesh/fixtures/gate4/agent_workload_plan_three_user.json').read_text())
    for user in workload['users']:
        for task in user['tasks']:
            for row in task['rounds']:
                repair = row['repair_round'] != 0
                profile = program.agent_request_profiles[int(repair)]
                row.update(logical_source_rank=0, full_context_tokens=profile.full_input_tokens,
                    full_context_bytes=profile.input_binding_bytes,
                    expected_cached_tokens=profile.expected_cached_tokens,
                    expected_cached_context_bytes=128 if repair else 0,
                    output_tokens=2, generated_code_bytes=256, output_capacity_bytes=256,
                    kv_required_tokens_after_round=profile.full_input_tokens + 2,
                    profile_id=profile.profile_id, requested_profile_key=hex(profile.requested_profile_key))
                row['delta_prompt_tokens' if repair else 'prompt_tokens'] = 8
                row['delta_prompt_bytes' if repair else 'prompt_bytes'] = 128
    (work / 'workload.json').write_text(json.dumps(workload))
    surrogate = json.loads((work / 'surrogate.json').read_text())
    source = surrogate['profiles'][0]
    surrogate['profiles'] = [dict(source, profile_id=p.profile_id,
        profile_key=hex(p.requested_profile_key), input_tokens=p.full_input_tokens,
        input_bytes=p.input_binding_bytes) for p in program.agent_request_profiles]
    (work / 'surrogate.json').write_text(json.dumps(surrogate))
    config = yaml.safe_load((work / 'config.yaml').read_text())
    config['agent'].update(users=3, max_repair_rounds=1, release_session_on_task_terminal=True)
    (work / 'config.yaml').write_text(yaml.safe_dump(config, sort_keys=False))
    return program


@pytest.mark.parametrize('slots', [3, 1])
def test_full_serving_repairs_and_releases_three_users(tmp_path, slots):
    program = repair_fixture(tmp_path)
    facts, result, metrics = run_serving_fixture(tmp_path, 'repair',
        extra_args=['--kv-max-sessions', str(slots)])
    assert result['terminal'] == 'DONE'
    document = read_facts_document(tmp_path / 'repair' / 'gate6_facts.tsv')
    assert document.fatal is None
    assert metrics['metadata_reads_validated'] == 5
    assert metrics['generated_code_reads_validated'] == 5
    from mesh_ir.agent_workload import load_workload_plan
    from mesh_ir.agent_planning import build_command_identity_plan
    from mesh_ir.serving_contract_digest import surrogate_seed, token_digests, semantic_output_digest
    from mesh_ir.serving_output_bytes import surrogate_output_bytes, dual_fnv_digest
    workload = load_workload_plan(tmp_path / 'workload.json')
    commands = build_command_identity_plan(workload, None, 'AUTO_PER_TASK')['records']
    expected_outputs = {}
    for user in workload.document['users']:
        for task in user['tasks']:
            for row in task['rounds']:
                command = next(command for command in commands if command['command_kind'] == 'GENERATE'
                    and command['user_id'] == user['user_id'] and command['task_seq'] == task['task_seq']
                    and command['repair_round_or_ffff'] == row['repair_round'])
                seed = surrogate_seed(bytes.fromhex(row['input_content_digest']), row['program_id'],
                    row['profile_id'], int(row['requested_profile_key'], 16))
                semantic = semantic_output_digest(bytes.fromhex(workload.digest), row['workload_plan_item_id'],
                    row['output_tokens'], token_digests(seed, row['output_tokens']))
                expected_outputs[command['request_id']] = dual_fnv_digest(
                    surrogate_output_bytes(semantic, row['generated_code_bytes']))
    actual_outputs = {event['request_id']: event['status'] for event in document.events
                      if event['kind'] == 'LOCAL_READ' and event['object'] == 'GENERATED_CODE_READ'}
    assert actual_outputs == expected_outputs
    profiles = {row['instance_profile_id'] for row in document.serving_phases}
    assert (210 if slots == 3 else 220) in profiles
    assert metrics['kv_session_records'] == 0
    config = yaml.safe_load((tmp_path / 'config.yaml').read_text())
    arena = config['serving']['address_map']['input_arena']
    input_base = int(arena['base'], 0) if isinstance(arena['base'], str) else arena['base']
    input_end = input_base + arena['bytes']
    prompt_bursts = [row for row in result['burst_timings']
                     if row['channel'] == 'AR' and input_base <= row['address'] < input_end]
    assert sum(row['beats'] * row['beat_bytes'] for row in prompt_bursts) == 5 * 128 + (128 if slots == 1 else 0)
    phases_by_request = {}
    for phase in document.serving_phases:
        phases_by_request.setdefault(phase['request_id'], []).append(phase)
    for event in document.events:
        if event['kind'] == 'HOST_STAGE_START':
            phases = phases_by_request[event['request_id']]
            assert event['tick'] > max(phase['commit_tick'] for phase in phases)
            finish = next(row for row in document.events if row['kind'] == 'HOST_STAGE_DONE'
                and row['request_id'] == event['request_id'] and row['object'] == event['object'])
            assert finish['tick'] > event['tick']
            assert not any(row['kind'] == 'BATCH_FREEZE'
                and row['request_id'] >> 32 == event['request_id'] >> 32
                and event['tick'] <= row['tick'] <= finish['tick'] for row in document.events)
    for request in (2, (2 << 32) | 2):
        prefill = phases_by_request[request][0]
        resident = slots == 3 or request != 2
        assert prefill['kv_tokens_before'] == (10 if resident else 0)
        assert prefill['append_tokens'] == (8 if resident else 18)

    from mesh_ir.kv_layout import kv_layout_digest
    from mesh_ir.kv_persistence import load_persistent_manifest
    identity = dict(architecture=program.arch_digest.hex(), program=program.semantic_sha256,
                    weights=WEIGHT_DIGEST, layout=kv_layout_digest(program).hex(),
                    plan=(tmp_path / 'repair' / 'plan_image.bin').read_bytes()[-32:].hex())
    snapshot = tmp_path / 'repair' / 'gate6_facts.tsv.kv.json'
    restored = load_persistent_manifest(snapshot, identity, json.loads(snapshot.read_text())['sha256'])
    assert len(restored.records) == 0
    assert len(restored.tombstones) == 3
    assert metrics['host_release_waiters'] == 0
    events = document.events
    releases = [e for e in events if e['kind'] == 'RELEASE_SUCCESS']
    finals = [e for e in events if e['kind'] == 'TASK_LIFECYCLE_FINAL']
    assert len(releases) == len(finals) == 3
    assert all(e['status'] == 'SUCCESS' for e in events if e['kind'] == 'CQ_ASSIGN')
    for final in finals:
        user = final['request_id'] >> 32
        release = next(e for e in releases if e['request_id'] >> 32 == user)
        assert release['tick'] <= final['tick']


@pytest.mark.parametrize('opcode', ['CANCEL', 'RELEASE_SESSION'])
@pytest.mark.parametrize('trigger', ['AFTER_SQ_ACCEPT', 'AFTER_GENERATE_TERMINAL'])
def test_control_cleanup_joins_the_task_lifecycle(tmp_path, opcode, trigger):
    from .test_gate6_serving_controls import _run_control
    facts, mesh, generate, control = _run_control(tmp_path, trigger, opcode, auto_release=True)
    assert facts.metrics['kv_session_records'] == 0
    assert facts.metrics['host_release_waiters'] == 0
    assert len([e for e in facts.events if e['kind'] == 'TASK_LIFECYCLE_FINAL']) == 1
    assert len([e for e in facts.events if e['kind'] == 'RELEASE_SUCCESS']) == (opcode == 'CANCEL')
    assert not any(e['kind'] == 'HOST_STAGE_START' for e in facts.events
                   if opcode == 'CANCEL' and trigger == 'AFTER_SQ_ACCEPT')


def test_repair_limit_releases_the_admitted_session(tmp_path):
    repair_fixture(tmp_path)
    workload = json.loads((tmp_path / 'workload.json').read_text())
    workload['users'] = workload['users'][:1]
    task = workload['users'][0]['tasks'][0]
    task['rounds'] = task['rounds'][:1]
    task['rounds'][0].pop('log_parse')
    workload['max_repair_rounds'] = 0
    (tmp_path / 'workload.json').write_text(json.dumps(workload))
    config = yaml.safe_load((tmp_path / 'config.yaml').read_text())
    config['agent'].update(users=1, max_repair_rounds=0)
    (tmp_path / 'config.yaml').write_text(yaml.safe_dump(config, sort_keys=False))
    _, result, metrics = run_serving_fixture(tmp_path, 'limit')
    facts = read_facts_document(tmp_path / 'limit' / 'gate6_facts.tsv')
    assert result['terminal'] == 'DONE'
    assert metrics['kv_session_records'] == metrics['host_release_waiters'] == 0
    final = [e for e in facts.events if e['kind'] == 'TASK_LIFECYCLE_FINAL']
    release = [e for e in facts.events if e['kind'] == 'RELEASE_SUCCESS']
    assert len(final) == len(release) == 1
    assert metrics['failed_tasks'] == 1
    assert next(e for e in facts.events if e['kind'] == 'TASK_TERMINAL')['status'] == 'BUSINESS_FAILED'
    assert release[0]['tick'] <= final[0]['tick']
    assert not any(e['object'] == 'LOG_PARSE' for e in facts.events)


def test_quiescent_resident_manifest_restores_in_the_independent_oracle(tmp_path):
    from mesh_ir.abi.decoder import decode_program
    from mesh_ir.kv_layout import kv_layout_digest
    from mesh_ir.kv_persistence import load_persistent_manifest
    from .serving_runtime import FIXTURES
    program = decode_program((FIXTURES / 'serving_two_tokens.mshb').read_bytes())
    write_serving_fixture(tmp_path, program)
    _, result, metrics = run_serving_fixture(tmp_path, 'persistent')
    identity = dict(architecture=program.arch_digest.hex(), program=program.semantic_sha256,
                    weights=WEIGHT_DIGEST, layout=kv_layout_digest(program).hex(),
                    plan=(tmp_path / 'persistent' / 'plan_image.bin').read_bytes()[-32:].hex())
    snapshot = tmp_path / 'persistent' / 'gate6_facts.tsv.kv.json'
    restored = load_persistent_manifest(snapshot, identity, json.loads(snapshot.read_text())['sha256'])
    assert result['terminal'] == 'DONE'
    assert metrics['kv_session_records'] == len(restored.records) == 1
    record = next(iter(restored.records.values()))
    assert record.cached_tokens == 10 and record.valid_bytes == 160
    assert record.pin_count == record.admission_claim_count == record.outstanding_kv_dma == 0


def test_pre_admission_error_does_not_invent_a_release_owner(tmp_path):
    from mesh_ir.abi.decoder import decode_program
    from .serving_runtime import FIXTURES, PROBE
    program = decode_program((FIXTURES / 'serving_two_tokens.mshb').read_bytes())
    write_serving_fixture(tmp_path, program)
    config = yaml.safe_load((tmp_path / 'config.yaml').read_text())
    config['agent']['release_session_on_task_terminal'] = True
    (tmp_path / 'config.yaml').write_text(yaml.safe_dump(config, sort_keys=False))
    entry = tmp_path / 'wire_probe.py'
    entry.write_text('import os, runpy\nos.environ["GATE6_HOST_WIRE_MODE"] = "chunk_zero"\n'
                     + f'os.environ["GATE6_SERVING_WORKLOAD_PLAN"] = {str(tmp_path / 'workload.json')!r}\n'
                     + f'runpy.run_path({str(PROBE)!r}, run_name="__m5_main__")\n')
    _, result, metrics = run_serving_fixture(tmp_path, 'rejected', entry=entry, expect_execution=False)
    facts = read_facts_document(tmp_path / 'rejected' / 'gate6_facts.tsv')
    assert result is None
    assert facts.fatal is None
    assert not facts.serving_phases
    assert metrics['failed_tasks'] == 1
    assert metrics['kv_session_records'] == metrics['host_release_waiters'] == 0
    assert metrics.get('metadata_reads_validated', 0) == 0
    assert len([e for e in facts.events if e['kind'] == 'TASK_LIFECYCLE_FINAL']) == 1
    assert not any(e['kind'] in ('RELEASE_SUBMITTING', 'HOST_STAGE_START') for e in facts.events)


def test_admitted_reuse_policy_error_still_releases_the_session(tmp_path):
    repair_fixture(tmp_path)
    workload = json.loads((tmp_path / 'workload.json').read_text())
    for user in workload['users']:
        for row in user['tasks'][0]['rounds'][1:]:
            row['kv_policy'] = 'REQUIRE_REUSE'
    (tmp_path / 'workload.json').write_text(json.dumps(workload))
    _, result, metrics = run_serving_fixture(tmp_path, 'policy_error',
        extra_args=['--kv-max-sessions', '1'])
    facts = read_facts_document(tmp_path / 'policy_error' / 'gate6_facts.tsv')
    assert result['terminal'] == 'DONE'
    assert metrics['failed_tasks'] == 1 and metrics['completed_tasks'] == 2
    assert metrics['metadata_reads_validated'] == 5
    assert metrics['generated_code_reads_validated'] == 4
    assert metrics['kv_session_records'] == metrics['host_release_waiters'] == 0
    assert len([e for e in facts.events if e['kind'] == 'RELEASE_SUCCESS']) == 3
    assert len([e for e in facts.events if e['kind'] == 'TASK_LIFECYCLE_FINAL']) == 3


def test_checkpoint_without_program_boundary_is_rejected(tmp_path):
    from mesh_ir.abi.decoder import decode_program
    from .serving_runtime import FIXTURES, PROBE
    program = decode_program((FIXTURES / 'serving_two_tokens.mshb').read_bytes())
    write_serving_fixture(tmp_path, program)
    entry = tmp_path / 'checkpoint_probe.py'
    entry.write_text('import m5, runpy\n'
        'original = m5.simulate\n'
        'def checkpoint_after_drain(*args):\n'
        '    event = original(*args)\n'
        '    m5.simulate = original\n'
        + f'    m5.checkpoint({str(tmp_path / 'checkpoint')!r})\n'
        '    return event\n'
        'm5.simulate = checkpoint_after_drain\n'
        + f'runpy.run_path({str(PROBE)!r}, run_name="__m5_main__")\n')
    with pytest.raises(AssertionError, match='E_CHECKPOINT_BOUNDARY'):
        run_serving_fixture(tmp_path, 'checkpoint_rejected', entry=entry)


def test_surrogate_output_size_is_checked_against_installed_mesh_profile(tmp_path):
    from mesh_ir.abi.decoder import decode_program
    from .serving_runtime import FIXTURES
    program = decode_program((FIXTURES / 'serving_two_tokens.mshb').read_bytes())
    write_serving_fixture(tmp_path, program)
    path = tmp_path / 'workload.json'
    workload = json.loads(path.read_text())
    round_ = workload['users'][0]['tasks'][0]['rounds'][0]
    round_['generated_code_bytes'] += 128
    round_['output_capacity_bytes'] = round_['generated_code_bytes']
    for kind in ('compile', 'test'):
        if round_[kind] is not None:
            round_[kind]['local_io']['read_bytes'] = round_['generated_code_bytes']
    path.write_text(json.dumps(workload))
    path = tmp_path / 'surrogate.json'
    surrogate = json.loads(path.read_text())
    surrogate['profiles'][0]['output_bytes'] = round_['generated_code_bytes']
    path.write_text(json.dumps(surrogate))
    run_serving_fixture(tmp_path, 'rejected',
        rejection_code='surrogate output size differs from the immutable mesh request profile')
