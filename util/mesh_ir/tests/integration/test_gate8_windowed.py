import copy
import json

import pytest
import yaml

from .serving_runtime import PROBE, run_serving_fixture, write_shared_fixture
from .test_gate6_batch_replay import install_replay
from .test_gate6_timing_ab import payloads


@pytest.mark.parametrize('initial', ['FAST_EVENT', 'FULL_TIMING'])
def test_switches_both_directions_keep_memory_cache_and_single_fabric(tmp_path, initial):
    _, work = write_shared_fixture(tmp_path, moe=True)
    path = work / 'workload.json'
    workload = json.loads(path.read_text())
    workload['users'][1]['tasks'][0]['think_time_ns'] += 1000000
    third = copy.deepcopy(workload['users'][0])
    third['user_id'] = 2
    task = third['tasks'][0]
    task['session_id'] = task['kv_handle'] = 3
    task['think_time_ns'] += 2000000
    task['rounds'][0]['workload_plan_item_id'] = 3
    workload['users'].append(third)
    path.write_text(json.dumps(workload))
    install_replay(work, shared=False)
    path = work / 'config.yaml'
    config = yaml.safe_load(path.read_text())
    config['agent']['users'] = 3
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    _, reference, reference_metrics = run_serving_fixture(work, 'full', weight_policy='cached')
    path = work / 'config.yaml'
    config = yaml.safe_load(path.read_text())
    other = 'FULL_TIMING' if initial == 'FAST_EVENT' else 'FAST_EVENT'
    config['runtime'].update(mode='WINDOWED_TIMING', fast_event={'queue_entries': 1},
        windows={'initial_mode': initial, 'switches': [
            {'at_tick': 1000010000, 'mode': other},
            {'at_tick': 2000010000, 'mode': initial}]})
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    _, result, metrics = run_serving_fixture(work, 'windowed', weight_policy='cached')
    assert result['terminal'] == 'DONE'
    assert payloads(result) == payloads(reference)
    assert metrics['metadata_reads_validated'] == reference_metrics['metadata_reads_validated'] == 3
    assert [row['replay_state'] for row in result['moe']['cache_state']] == [
        row['replay_state'] for row in reference['moe']['cache_state']]
    assert [row['valid_lines'] for row in result['moe']['cache_state']] == [
        row['valid_lines'] for row in reference['moe']['cache_state']]
    manifest = json.loads((work / 'windowed' / 'timing_windows.json').read_text())
    assert [row['mode'] for row in manifest['windows']] == [initial, other, initial]
    assert all(row['captured_request_ids'] for row in manifest['windows'])
    for previous, current in zip(manifest['windows'], manifest['windows'][1:]):
        assert previous['end_tick'] == current['start_tick']
        assert current['start_tick'] >= current['trigger_tick']
        assert current['drained'] is True
    assert result['garnet']['quiescent'] == result['aggregate_network']['quiescent'] == 1
    assert result['aggregate_network']['endpoint_messages'] > 0
    assert result['aggregate_network']['endpoint_wire_bytes'] > 0
    assert result['garnet']['packets_received'] > 0
    assert (result['garnet']['packets_received'] + result['aggregate_network']['endpoint_messages']
            == reference['garnet']['packets_received'])
    assert (result['garnet']['wire_bytes_received'] + result['aggregate_network']['endpoint_wire_bytes']
            == reference['garnet']['wire_bytes_received'])
    graph = json.loads((work / 'windowed' / 'config.json').read_text())
    def fabrics(value):
        if isinstance(value, dict):
            return int(value.get('type') in ('GarnetNetwork', 'SimpleNetwork')) + sum(
                fabrics(child) for child in value.values())
        if isinstance(value, list):
            return sum(fabrics(child) for child in value)
        return 0
    assert fabrics(graph) == 1


@pytest.mark.parametrize('mode', ['FAST_EVENT', 'FULL_TIMING'])
@pytest.mark.parametrize('switch_at_zero', [False, True])
def test_startup_window_reuses_existing_transport_timing(tmp_path, mode, switch_at_zero):
    _, work = write_shared_fixture(tmp_path, moe=True)
    install_replay(work, shared=False)
    path = work / 'config.yaml'
    config = yaml.safe_load(path.read_text())
    config['runtime']['mode'] = mode
    if mode == 'FAST_EVENT':
        config['runtime']['fast_event'] = {'queue_entries': 1}
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    _, reference, _ = run_serving_fixture(work, 'reference', weight_policy='cached')
    other = 'FULL_TIMING' if mode == 'FAST_EVENT' else 'FAST_EVENT'
    switches = [{'at_tick': 9000000000, 'mode': other}]
    if switch_at_zero:
        switches.insert(0, {'at_tick': 0, 'mode': mode})
    config['runtime'].update(mode='WINDOWED_TIMING', fast_event={'queue_entries': 1},
        windows={'initial_mode': other if switch_at_zero else mode, 'switches': switches})
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    _, result, _ = run_serving_fixture(work, 'windowed', weight_policy='cached')
    assert result['terminal_tick'] == reference['terminal_tick']
    assert payloads(result) == payloads(reference)
    assert result['burst_timings'] == reference['burst_timings']
    manifest = json.loads((work / 'windowed' / 'timing_windows.json').read_text())
    assert len(manifest['windows']) == 1 + switch_at_zero
    assert manifest['windows'][-1]['start_tick'] == 0
    assert manifest['windows'][-1]['mode'] == mode


@pytest.mark.parametrize('pause', [False, True])
def test_switch_requires_a_drained_program_boundary(tmp_path, pause):
    _, work = write_shared_fixture(tmp_path, moe=True)
    entry = work / 'invalid_switch.py'
    entry.write_text(
        'import runpy\n'
        'import m5\n'
        'from m5.objects import Root\n'
        'original = m5.simulate\n'
        'def simulate(limit):\n'
        '    original(1000010000)\n'
        '    frontend = Root.getInstance().system.gate6_frontend\n'
        + ('    frontend.requestProgramBoundary()\n' if pause else '')
        + '    frontend.switchProgramTiming(True)\n'
        '    raise AssertionError("unsafe timing switch accepted")\n'
        'm5.simulate = simulate\n'
        f'runpy.run_path({str(PROBE)!r}, run_name="__m5_main__")\n')
    run_serving_fixture(work, 'rejected', entry=entry, weight_policy='cached',
                        rejection_code='network timing handoff requires a drained program boundary')


@pytest.mark.parametrize('queue', ['link', 'router'])
def test_window_fabric_rejects_unbounded_internal_queues(tmp_path, queue):
    _, work = write_shared_fixture(tmp_path, moe=True)
    path = work / 'config.yaml'
    config = yaml.safe_load(path.read_text())
    config['runtime'].update(mode='WINDOWED_TIMING', fast_event={'queue_entries': 1},
        windows={'initial_mode': 'FULL_TIMING', 'switches': [
            {'at_tick': 1000010000, 'mode': 'FAST_EVENT'}]})
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    entry = work / 'unbounded_switch.py'
    target = ('self.aggregate_link_buffers[0]' if queue == 'link' else
              'self.aggregate_routers[0].port_buffers[0]')
    entry.write_text(
        'import runpy\n'
        'from m5.objects import GarnetNetwork\n'
        'original = GarnetNetwork.setup_aggregate\n'
        'def setup(self, *args):\n'
        '    original(self, *args)\n'
        f'    {target}.buffer_size = 0\n'
        'GarnetNetwork.setup_aggregate = setup\n'
        f'runpy.run_path({str(PROBE)!r}, run_name="__m5_main__")\n')
    run_serving_fixture(work, 'rejected', entry=entry, weight_policy='cached',
                        rejection_code=f'aggregate {queue} queue capacity mismatch')
