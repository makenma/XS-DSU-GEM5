import copy
from collections import Counter
import json
import time

import pytest
import yaml

from mesh_ir.abi.decoder import decode_program
from mesh_ir.experiment.execution import file_digest, source_identity
from mesh_ir.gate3_oracle import read_facts_document
from mesh_ir.serving_calibration import ServingMeasurement, compare_serving, window_dma_traffic
from .serving_runtime import GEM5, REPO, run_serving_fixture, write_shared_fixture
from .test_gate6_repair import repair_fixture
from .test_gate6_host_pools import provider_artifact


@pytest.mark.parametrize('scenario', ['repair_kv', 'moe_remote'])
def test_twelve_user_long_task_and_selected_windows(tmp_path, scenario):
    if scenario == 'repair_kv':
        repair_fixture(tmp_path)
    else:
        write_shared_fixture(tmp_path, moe=True)
    path = tmp_path / 'workload.json'
    workload = json.loads(path.read_text())
    templates = workload['users']
    workload['users'] = []
    item = 0
    for user_id in range(12):
        user = copy.deepcopy(templates[user_id % len(templates)])
        user['user_id'] = user_id
        template = user['tasks'][0]
        user['tasks'] = []
        for task_seq in range(2):
            task = copy.deepcopy(template)
            task.update(task_seq=task_seq, session_id=1 + user_id * 2 + task_seq,
                        kv_handle=1 + user_id * 2 + task_seq, think_time_ns=1000000)
            for row in task['rounds']:
                item += 1
                row['workload_plan_item_id'] = item
                for stage in ('compile', 'test', 'log_parse'):
                    if stage in row:
                        row[stage]['nominal_ns'] = 1000000000 if stage == 'compile' else 100000000
            user['tasks'].append(task)
        workload['users'].append(user)
    path.write_text(json.dumps(workload))
    config_path = tmp_path / 'config.yaml'
    config = yaml.safe_load(config_path.read_text())
    config['agent'].update(users=12, release_session_on_task_terminal=True)
    config['serving'].update(max_request_contexts=12, max_active_sequences=12,
        kv_session_record_entries=24, kv_session_tombstone_entries=24,
        kv_admission_wait_entries=12, session_release_waiter_entries=12,
        seen_request_id_entries=128, host_issued_request_id_entries=128,
        driver_submission_table_entries=16, early_completion_cache_entries=16,
        batch_failure_record_entries=12, cq_obligation_entries=12, terminal_result_queue_depth=12,
        batch_wait_cycles=100000 if scenario == 'moe_remote' else 0)
    services = config['agent_axi_driver']['synthetic_host_services']
    services.update(agent_object_table_entries=256, release_waiter_entries=12,
        host_admission_registry_entries=24, sq_submit_ready_queue_entries=64, service_queue_depth=12)
    config['agent_axi_driver']['remote_host_memory'].update(
        target_request_queue_depth=2, target_response_queue_depth=2)
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    extra = ['--sim-tick-limit', '30000000000000', '--kv-max-sessions',
        '1' if scenario == 'repair_kv' else '12',
        '--axi-source-fifo-depths', '2,2,2,2,2',
        '--axi-message-buffer-depths', '2,2,2,2,2', '--garnet-buffers-per-vnet', '2,2,2,2,2']
    if scenario == 'moe_remote':
        program = decode_program((tmp_path / 'program.mshb').read_bytes())
        artifact = provider_artifact(tmp_path, program, workload, 'route_replay')
        extra += ['--moe-provider', 'route_replay', '--moe-provider-artifact', str(artifact)]
    measurements, wall_seconds = {}, {}
    for mode in ('FULL_TIMING', 'FAST_EVENT', 'WINDOWED_TIMING'):
        config['runtime']['mode'] = mode
        if mode != 'FULL_TIMING':
            config['runtime']['fast_event'] = {'queue_entries': 2}
        if mode == 'WINDOWED_TIMING':
            config['runtime']['windows'] = dict(initial_mode='FULL_TIMING', switches=[
                dict(at_tick=10000000000, mode='FAST_EVENT'),
                dict(at_tick=900000000000, mode='FULL_TIMING'),
                dict(at_tick=1600000000000, mode='FAST_EVENT')])
        config_path.write_text(yaml.safe_dump(config, sort_keys=False))
        started = time.monotonic()
        _, result, metrics = run_serving_fixture(tmp_path, mode, extra_args=extra,
            weight_policy='cached' if scenario == 'moe_remote' else 'streamed')
        wall_seconds[mode] = time.monotonic() - started
        assert metrics['metadata_reads_validated'] == item
        assert metrics['completed_tasks'] == 24
        assert metrics['kv_session_records'] == metrics['host_release_waiters'] == 0
        for field in ('live_contexts', 'live_cq_obligations', 'live_sq_intakes',
                      'live_submissions', 'msi_rob_entries'):
            assert metrics[field] == 0
        facts = read_facts_document(tmp_path / mode / 'gate6_facts.tsv')
        assert len([row for row in facts.events if row['kind'] == 'RELEASE_SUCCESS']) == 24
        assert result['terminal_tick'] > 1000000000000
        measurements[mode] = ServingMeasurement.read(tmp_path / mode)
        if scenario == 'repair_kv':
            assert any(row['instance_profile_id'] == 220 for row in facts.serving_phases)
        else:
            assert max(Counter(row['instance_generation'] for row in facts.serving_phases).values()) == 2
            assert result['moe']['cache_fills']
            assert all(row['live_tokens'] == row['live_obligations'] == 0
                       for row in result['moe']['cache_state'])
    run_serving_fixture(tmp_path, 'WINDOWED_REPEAT', extra_args=extra,
        weight_policy='cached' if scenario == 'moe_remote' else 'streamed')
    repeated = ServingMeasurement.read(tmp_path / 'WINDOWED_REPEAT')
    assert repeated.latencies == measurements['WINDOWED_TIMING'].latencies
    assert repeated.terminal_tick == measurements['WINDOWED_TIMING'].terminal_tick
    compare_serving(measurements['WINDOWED_TIMING'], repeated)
    assert (tmp_path / 'WINDOWED_REPEAT' / 'gate6_facts.tsv').read_bytes() == (
        tmp_path / 'WINDOWED_TIMING' / 'gate6_facts.tsv').read_bytes()
    reference = measurements['FULL_TIMING']
    report = dict(schema='serving_calibration_v1', scenario=scenario, users=12, tasks=24,
        generates=item, deterministic_repeat=True, binary_sha256=file_digest(GEM5), workload_sha256=file_digest(path),
        source=source_identity(REPO),
        artifacts={mode: {name: file_digest(tmp_path / mode / name)
                         for name in ('config.json', 'mesh_result.json', 'gate6_facts.tsv', 'stats.txt')}
                   for mode in measurements},
        runner_wall_seconds=wall_seconds,
        actual_fast_granularity='AXI_MESSAGE', core_granularity='COMMAND_DESCRIPTOR',
        arms={mode: compare_serving(reference, measurement) for mode, measurement in measurements.items()},
        generate_cohort={mode: compare_serving(reference, measurement, reference.outputs)
                         for mode, measurement in measurements.items()})
    windows = json.loads((tmp_path / 'WINDOWED_TIMING' / 'timing_windows.json').read_text())
    for window in windows['windows']:
        if window['captured_request_ids']:
            actual_traffic = window_dma_traffic(tmp_path / 'WINDOWED_TIMING', window['captured_request_ids'])
            assert actual_traffic == window_dma_traffic(tmp_path / 'FULL_TIMING', window['captured_request_ids'])
            window['dma_traffic'] = actual_traffic
            window['approximation_error'] = compare_serving(reference, measurements['WINDOWED_TIMING'],
                window['captured_request_ids'])
        elif window['mode'] == 'FULL_TIMING':
            pytest.fail('selected FULL window captured no requests')
    report['windows'] = windows
    (tmp_path / 'calibration.json').write_text(json.dumps(report, sort_keys=True, indent=2) + '\n')
