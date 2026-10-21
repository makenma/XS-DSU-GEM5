import json

import pytest
import yaml

from mesh_ir.gate3_oracle import read_facts_document

from .serving_runtime import PROBE, run_serving_fixture, write_shared_fixture
from .test_gate6_timing_ab import payloads
from .test_gate6_batch_replay import install_replay


@pytest.mark.parametrize('mode', ['FULL_TIMING', 'FAST_EVENT'])
@pytest.mark.parametrize('pause_tick', [0, 1000010000, 1000100000])
@pytest.mark.parametrize('shared', [False, True, None])
def test_program_boundary_drains_and_resumes_same_owners(tmp_path, mode, pause_tick, shared):
    _, work = write_shared_fixture(tmp_path, moe=True)
    if shared is None:
        workload_path = work / 'workload.json'
        workload = json.loads(workload_path.read_text())
        workload['users'] = workload['users'][:1]
        workload_path.write_text(json.dumps(workload))
    else:
        install_replay(work, shared=shared)
    config_path = work / 'config.yaml'
    config = yaml.safe_load(config_path.read_text())
    if shared is None:
        config['agent']['users'] = 1
    config['runtime']['mode'] = mode
    if mode == 'FAST_EVENT':
        config['runtime']['fast_event'] = {'queue_entries': 1}
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    _, baseline, baseline_metrics = run_serving_fixture(work, 'baseline', weight_policy='cached')
    entry = work / 'boundary_probe.py'
    entry.write_text(
        'import json, os, runpy\n'
        'from pathlib import Path\n'
        'import m5\n'
        'from m5.objects import Root\n'
        'original = m5.simulate\n'
        'def simulate(limit):\n'
        '    frontend = Root.getInstance().system.gate6_frontend\n'
        f'    original({pause_tick})\n'
        '    frontend.requestProgramBoundary()\n'
        '    initial = frontend.programBoundaryReady()\n'
        '    for attempt in range(1000):\n'
        '        if frontend.programBoundaryReady():\n'
        '            break\n'
        '        event = original(1000000)\n'
        '        assert event.getCause() == "simulate() limit reached", event.getCause()\n'
        '    else:\n'
        '        raise AssertionError("program boundary failed to drain")\n'
        '    ready = m5.curTick()\n'
        f'    event = original({1000050000 if pause_tick == 0 else 1000000})\n'
        '    assert event.getCause() == "simulate() limit reached", event.getCause()\n'
        '    assert frontend.programBoundaryReady()\n'
        '    frontend.resumeProgramBoundary()\n'
        '    Path(os.environ["AI_MESH_ARTIFACT_DIR"], "boundary.json").write_text(\n'
        '        json.dumps(dict(initial_ready=initial, ready_tick=ready, resume_tick=m5.curTick())))\n'
        '    return original(limit - m5.curTick())\n'
        'm5.simulate = simulate\n'
        f'runpy.run_path({str(PROBE)!r}, run_name="__m5_main__")\n')
    _, resumed, metrics = run_serving_fixture(work, 'resumed', entry=entry, weight_policy='cached')
    boundary = json.loads((work / 'resumed' / 'boundary.json').read_text())
    facts = read_facts_document(work / 'resumed' / 'gate6_facts.tsv')
    submissions = [event for event in facts.events if event['kind'] == 'SQ_CONSUME']
    if pause_tick == 0:
        assert submissions[0]['tick'] >= boundary['resume_tick']
    elif pause_tick == 1000010000 and shared is not None:
        if shared:
            assert submissions[1]['tick'] < boundary['ready_tick']
        else:
            assert submissions[1]['tick'] >= boundary['resume_tick']
    if pause_tick:
        assert not boundary['initial_ready']
        assert boundary['ready_tick'] > pause_tick
    assert resumed['terminal'] == baseline['terminal'] == 'DONE'
    for field in ('metadata_reads_validated', 'generated_code_reads_validated', 'core_starts'):
        assert metrics[field] == baseline_metrics[field]
    assert payloads(resumed) == payloads(baseline)
    assert [row['replay_state'] for row in resumed['moe']['cache_state']] == [
        row['replay_state'] for row in baseline['moe']['cache_state']]
    assert [row['valid_lines'] for row in resumed['moe']['cache_state']] == [
        row['valid_lines'] for row in baseline['moe']['cache_state']]


@pytest.mark.parametrize('action', ['resume_unpaused', 'resume_active', 'double_pause'])
def test_program_boundary_rejects_invalid_transitions(tmp_path, action):
    _, work = write_shared_fixture(tmp_path, moe=True)
    entry = work / 'invalid_boundary_probe.py'
    entry.write_text(
        'import runpy\n'
        'import m5\n'
        'from m5.objects import Root\n'
        'original = m5.simulate\n'
        'def simulate(limit):\n'
        '    original(1000100000)\n'
        '    frontend = Root.getInstance().system.gate6_frontend\n'
        + ('    frontend.requestProgramBoundary()\n' if action != 'resume_unpaused' else '')
        + ('    frontend.requestProgramBoundary()\n' if action == 'double_pause'
           else '    frontend.resumeProgramBoundary()\n')
        + '    raise AssertionError("invalid transition accepted")\n'
        'm5.simulate = simulate\n'
        f'runpy.run_path({str(PROBE)!r}, run_name="__m5_main__")\n')
    run_serving_fixture(work, 'rejected', entry=entry, weight_policy='cached',
                        rejection_code='serving program boundary')
