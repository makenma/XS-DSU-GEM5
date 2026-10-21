import copy
import json
from pathlib import Path

import pytest

from mesh_ir.abi.decoder import decode_program
from mesh_ir.agent_workload import PlanError, load_workload_plan
from mesh_ir.serving_batch_replay import load_batch_replay


@pytest.fixture
def replay_case(tmp_path):
    fixtures = Path(__file__).resolve().parents[4] / 'tests/gem5/ai_mesh/fixtures/gate6'
    program = decode_program((fixtures / 'serving_two_tokens.mshb').read_bytes())
    workload = json.loads((fixtures / 'serving_workload_plan.json').read_text())
    workload['users'][0]['tasks'][0]['rounds'][0]['batch_replay'] = True
    workload_path = tmp_path / 'workload.json'
    workload_path.write_text(json.dumps(workload))
    plan = load_workload_plan(workload_path)
    batches = []
    for ordinal, (phase, iteration, begin, count) in enumerate([
            ('PREFILL', 0, 0, 8), ('DECODE', 0, 0, 1),
            ('DECODE', 1, 1, 1), ('PUBLISH', 2, 2, 0)]):
        batches.append(dict(batch_ordinal=ordinal, phase=phase, decode_iteration=iteration,
            concrete_profile_id=11 + ordinal, members=[dict(workload_plan_item_id=1,
                user_id=0, task_seq=0, repair_round=0, sequence_ordinal_begin=begin,
                valid_token_count=count, logical_source_rank=0)]))
    document = dict(schema='serving_batch_replay_v1', version=1,
                    workload_plan_digest=plan.digest,
                    mesh_program_digest=program.semantic_sha256, batches=batches)
    path = tmp_path / 'replay.json'
    path.write_text(json.dumps(document))
    return path, document, program, plan


def test_replay_reads_an_immutable_complete_phase_population(replay_case):
    path, _, program, workload = replay_case
    replay = load_batch_replay(path, workload, program)
    assert len(replay.batches) == 4
    assert replay.batches[2].members[0].sequence_ordinal_begin == 1
    assert replay.batches[-1].phase == 'PUBLISH'
    assert replay.batches[-1].decode_iteration == 2
    assert replay.digest == "8bbd701911769f7e9dc5eb0f3028a899b30f9eb23e69d972b1aca15b59dcd861"


@pytest.mark.parametrize('mutation', [
    'unknown_field', 'workload_digest', 'program_digest', 'ordinal_hole',
    'unknown_member', 'duplicate_member', 'rank', 'profile', 'phase',
    'decode_iteration', 'token_begin', 'token_count', 'missing_decode',
    'missing_publish', 'duplicate_publish', 'unflagged_round', 'early_publish',
])
def test_replay_rejects_schema_identity_and_phase_divergence(replay_case, mutation):
    path, document, program, workload = replay_case
    changed = copy.deepcopy(document)
    if mutation == 'unknown_field':
        changed['runtime_request_id'] = 1
    elif mutation in ('workload_digest', 'program_digest'):
        changed['workload_plan_digest' if mutation == 'workload_digest' else 'mesh_program_digest'] = '0' * 64
    elif mutation == 'ordinal_hole':
        changed['batches'][1]['batch_ordinal'] = 9
    elif mutation == 'unknown_member':
        changed['batches'][0]['members'][0]['workload_plan_item_id'] = 999
    elif mutation == 'duplicate_member':
        changed['batches'][0]['members'] *= 2
    elif mutation in ('rank', 'token_begin', 'token_count'):
        key = {'rank': 'logical_source_rank', 'token_begin': 'sequence_ordinal_begin',
               'token_count': 'valid_token_count'}[mutation]
        changed['batches'][1]['members'][0][key] += 1
    elif mutation in ('profile', 'phase', 'decode_iteration'):
        key = {'profile': 'concrete_profile_id', 'phase': 'phase',
               'decode_iteration': 'decode_iteration'}[mutation]
        changed['batches'][1][key] = 'PUBLISH' if mutation == 'phase' else 99
    elif mutation in ('missing_decode', 'missing_publish'):
        del changed['batches'][1 if mutation == 'missing_decode' else -1]
        for i, row in enumerate(changed['batches']):
            row['batch_ordinal'] = i
    elif mutation == 'duplicate_publish':
        changed['batches'].append(dict(changed['batches'][-1], batch_ordinal=4))
    elif mutation == 'early_publish':
        changed['batches'][1], changed['batches'][-1] = changed['batches'][-1], changed['batches'][1]
        for i, row in enumerate(changed['batches']):
            row['batch_ordinal'] = i
    elif mutation == 'unflagged_round':
        from dataclasses import replace
        user = workload.users[0]
        task = user.tasks[0]
        round_ = replace(task.rounds[0], batch_replay=False)
        workload = replace(workload, users=(replace(user, tasks=(replace(task, rounds=(round_,)),)),))
    path.write_text(json.dumps(changed))
    with pytest.raises(PlanError):
        load_batch_replay(path, workload, program)
