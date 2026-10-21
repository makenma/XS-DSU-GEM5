import json

import pytest
import yaml

from mesh_ir.agent_workload import load_workload_plan
from mesh_ir.generated import abi as A
from mesh_ir.serving_batch_replay import load_batch_replay
from mesh_ir.serving_profiles import member_rank_vector

from .serving_runtime import run_serving_fixture, write_shared_fixture


def install_replay(work, strict=True, flagged_items=None, shared=True):
    from mesh_ir.abi.decoder import decode_program
    program = decode_program((work / "program.mshb").read_bytes())
    path = work / 'workload.json'
    document = json.loads(path.read_text())
    for user in document['users']:
        for task in user['tasks']:
            for round_ in task['rounds']:
                round_['batch_replay'] = flagged_items is None or round_['workload_plan_item_id'] in flagged_items
    path.write_text(json.dumps(document))
    workload = load_workload_plan(path)
    rounds = [(user, task, round_) for user in workload.users
              for task in user.tasks for round_ in task.rounds if round_.batch_replay]
    batches = []
    populations = (rounds,) if shared else tuple((row,) for row in rounds)
    for population in populations:
        profiles = sorted((row for row in program.agent_instance_profiles
                           if row.member_count == len(population) and row.path_kind == A.PATH_KIND.INITIAL_PREFILL
                           and member_rank_vector(program, row) == tuple(round_.logical_source_rank
                               for _, _, round_ in population)),
                          key=lambda row: (row.phase, row.kv_tokens_before))
        iteration = 0
        for profile in profiles:
            phase = {A.PHASE.PREFILL: 'PREFILL', A.PHASE.DECODE: 'DECODE',
                     A.PHASE.PUBLISH: 'PUBLISH'}[profile.phase]
            members = [dict(workload_plan_item_id=round_.workload_plan_item_id,
                            user_id=user.user_id, task_seq=task.task_seq,
                            repair_round=round_.repair_round,
                            sequence_ordinal_begin=0 if phase == 'PREFILL' else
                                profile.kv_tokens_before - round_.full_context_tokens,
                            valid_token_count=profile.valid_tokens_per_member,
                            logical_source_rank=round_.logical_source_rank)
                       for user, task, round_ in population]
            members.sort(key=lambda row: (row['workload_plan_item_id'], row['user_id'],
                                          row['task_seq'], row['repair_round']))
            batches.append(dict(batch_ordinal=len(batches), phase=phase,
                                decode_iteration=iteration,
                                concrete_profile_id=profile.instance_profile_id, members=members))
            iteration += phase == 'DECODE'
    replay = dict(schema='serving_batch_replay_v1', version=1,
                  workload_plan_digest=workload.digest,
                  mesh_program_digest=program.semantic_sha256, batches=batches)
    replay_path = work / 'replay.json'
    replay_path.write_text(json.dumps(replay))
    parsed = load_batch_replay(replay_path, workload, program)
    config_path = work / 'config.yaml'
    config = yaml.safe_load(config_path.read_text())
    config['serving'].update(batch_replay_artifact='replay.json',
                              strict_replay_serial_batches=strict, batch_wait_cycles=0)
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    return parsed


@pytest.mark.parametrize('reverse', [False, True])
def test_replay_freezes_exact_shared_population_without_batch_wait(tmp_path, reverse):
    program, work = write_shared_fixture(tmp_path, reverse=reverse, output_tokens=2)
    replay = install_replay(work)
    _, result, metrics = run_serving_fixture(work, 'replayed')
    assert result['terminal'] == 'DONE'
    assert metrics['metadata_reads_validated'] == 2
    assert (work / 'replayed/batch_replay_digest.txt').read_text().strip() == replay.digest
    assert len(result['instances']) == len(replay.batches)
    assert replay.digest in (work / 'replayed/console.log').read_text()


def test_replay_cpp_reader_rejects_artifact_when_python_reader_is_bypassed(tmp_path):
    program, work = write_shared_fixture(tmp_path)
    install_replay(work)
    replay_path = work / 'replay.json'
    replay = json.loads(replay_path.read_text())
    replay['batches'][0]['members'][0]['valid_token_count'] += 1
    replay_path.write_text(json.dumps(replay))
    from .serving_runtime import PROBE
    entry = work / 'bypass_replay_preflight.py'
    entry.write_text(
        'import runpy, sys\n'
        f'sys.path.insert(0, {str(PROBE.parents[4] / "util/mesh_ir")!r})\n'
        'from types import SimpleNamespace\n'
        'import mesh_ir.serving_batch_replay as replay\n'
        'replay.load_batch_replay = lambda *args: SimpleNamespace(digest="bypassed")\n'
        f'runpy.run_path({str(PROBE)!r}, run_name="__m5_main__")\n')
    run_serving_fixture(work, 'rejected', entry=entry,
                        rejection_code='E_BATCH_REPLAY_DIVERGENCE')


def test_strict_cached_replay_preserves_images_and_traffic_across_timing(tmp_path):
    from .test_gate6_timing_ab import payloads, commit_ticks
    program, work = write_shared_fixture(tmp_path, moe=True, output_tokens=2)
    replay = install_replay(work, shared=False)
    results = []
    images = []
    for mode, args in [('baseline', ()), ('repeat', ()),
                       ('slower', ('--sram-write-bytes-per-cycle-per-bank', '1'))]:
        _, result, metrics = run_serving_fixture(work, mode, weight_policy='cached', extra_args=args)
        assert result['terminal'] == 'DONE'
        assert metrics['metadata_reads_validated'] == 2
        assert len(result['instances']) == len(replay.batches)
        caches = result['moe']['cache_state']
        assert sum(row['tokens_created'] for row in caches) == 2
        assert len(result['moe']['cache_fills']) == 1
        results.append(result)
        images.append({path.name: path.read_bytes()
                       for path in (work / mode).glob('moe_instance_*.bin')})
    assert images[0] and images[0] == images[1] == images[2]
    assert results[0] == results[1]
    assert payloads(results[0]) == payloads(results[2])
    assert commit_ticks(results[0]) != commit_ticks(results[2])
    assert all(row['live_tokens'] == row['live_obligations'] == 0
               for result in results for row in result['moe']['cache_state'])


@pytest.mark.parametrize("record,field,value", [(0, "flags", 0), (1, "input_tokens", 9)])
def test_replay_flag_authority_rejects_wire_mutation_without_waiting_for_missing_member(tmp_path, record, field, value):
    from .test_gate6_wire_admission import wire_patch
    program, work = write_shared_fixture(tmp_path)
    install_replay(work)
    run_serving_fixture(work, 'flag_rejected', wire_patches=wire_patch(0, record, field, value),
                        rejection_code='E_BATCH_REPLAY_DIVERGENCE')


def test_artifact_path_does_not_flag_unlisted_rounds(tmp_path):
    program, work = write_shared_fixture(tmp_path, output_tokens=2)
    replay = install_replay(work, strict=False, flagged_items={1})
    _, result, metrics = run_serving_fixture(work, 'mixed_authority')
    assert result['terminal'] == 'DONE'
    assert metrics['metadata_reads_validated'] == 2
    assert len(result['instances']) == 2 * len(replay.batches)
