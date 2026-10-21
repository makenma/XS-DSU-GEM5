import json
import re

import pytest
import yaml

from mesh_ir.agent_config import load_agent_runtime_config
from mesh_ir.agent_workload import PlanError
from mesh_ir.gate3_oracle import read_facts_document

from .serving_runtime import run_serving_fixture, write_shared_fixture
from .test_gate6_batch_replay import install_replay
from .test_gate6_timing_ab import payloads


@pytest.mark.parametrize('depth', [1, 4])
def test_finite_fast_fabric_preserves_replayed_work_and_accounts_wire_bytes(tmp_path, depth):
    _, work = write_shared_fixture(tmp_path, moe=True, output_tokens=2)
    install_replay(work, shared=False)
    config_path = work / 'config.yaml'
    config = yaml.safe_load(config_path.read_text())
    results = {}
    reports = {}
    for arm, width in [('fast', 256), ('full', 256), ('narrow', 64), ('full_narrow', 64)]:
        full_timing = arm.startswith('full')
        config['runtime']['mode'] = 'FULL_TIMING' if full_timing else 'FAST_EVENT'
        if full_timing:
            config['runtime'].pop('fast_event', None)
        else:
            config['runtime']['fast_event'] = {'queue_entries': depth}
        config_path.write_text(yaml.safe_dump(config, sort_keys=False))
        _, result, metrics = run_serving_fixture(work, arm, weight_policy='cached',
            extra_args=('--link-width-bits', str(width)))
        results[arm] = result
        assert result['terminal'] == 'DONE'
        assert metrics['metadata_reads_validated'] == 2
        assert metrics['frontend_drained'] == 1
        facts = read_facts_document(work / arm / 'gate6_facts.tsv')
        cq = [event for event in facts.events if event['kind'] == 'CQ_CONSUME']
        assert len(cq) == 2 and all(row['status'] == 'SUCCESS' for row in cq)
        assert all(bridge['idle'] for bridge in result['bridges'])
        assert all(row['live_tokens'] == row['live_obligations'] == 0
                   for row in result['moe']['cache_state'])
        system = json.loads((work / arm / 'config.json').read_text())['system']
        network = system['ruby']['network']
        mode = json.loads((work / arm / 'simulation_mode.json').read_text())
        stats = {}
        for line in (work / arm / 'stats.txt').read_text().splitlines():
            fields = line.split()
            if len(fields) > 1 and re.fullmatch(r'-?\d+(\.\d+)?', fields[1]):
                stats[fields[0]] = float(fields[1])
        starts = {event['request_id']: event['tick'] for event in facts.events
                  if event['kind'] == 'SQ_CONSUME'}
        latencies = {str(event['request_id']): event['tick'] - starts[event['request_id']]
                     for event in cq}
        assert all(value > 0 for value in latencies.values())
        reports[arm] = dict(terminal_tick=result['terminal_tick'], request_latency_ticks=latencies,
                            host_seconds=stats['hostSeconds'], mode=mode)
        if full_timing:
            assert network['type'] == 'GarnetNetwork'
            assert result['garnet']['quiescent'] == 1
            continue
        assert network['type'] == 'SimpleNetwork'
        assert mode['aggregation'] == 'AXI_MESSAGE'
        assert mode['checkpoint_restore_supported'] is True
        assert mode['drain_boundary'] == 'PROGRAM'
        assert network['buffer_size'] == depth
        assert network['endpoint_bandwidth'] == 1000 * width // 8
        for router in network['routers']:
            assert router['port_buffers']
            assert all(row['buffer_size'] == depth for row in router['port_buffers'])
        for link in network['int_links']:
            assert all(row['buffer_size'] == depth for row in link['buffers'])
        snapshot = result['aggregate_network']
        assert snapshot['quiescent'] == 1
        assert snapshot['queued_messages'] == snapshot['serialization_units'] == 0
        assert snapshot['forwarded_messages'] > 0
        if depth == 1:
            assert snapshot['blocked_cycles'] > 0
        endpoint = system['ruby']['axi_initiator_adapter0']
        headers = endpoint['wire_header_bytes']
        wire = [header + (endpoint['data_bus_bytes'] if index in (1, 4)
                          and not endpoint['data_header_sideband'] else 0)
                for index, header in enumerate(headers)]
        expected_wire = 0
        observed_wire = 0
        observed_messages = 0
        for key, value in stats.items():
            match = re.fullmatch(r'system\.ruby\.network\.routers\d+\.throttle\d+\.msg_count\.[^.]+::([0-4])', key)
            if match:
                expected_wire += int(value) * wire[int(match.group(1))]
                observed_messages += int(value)
            if re.fullmatch(r'system\.ruby\.network\.routers\d+\.throttle\d+\.total_msg_bytes', key):
                observed_wire += int(value)
        assert observed_messages == snapshot['forwarded_messages']
        assert expected_wire > 0 and expected_wire == observed_wire == snapshot['wire_bytes']
    assert all(payloads(result) == payloads(results['full']) for result in results.values())
    assert results['narrow']['terminal_tick'] > results['fast']['terminal_tick']
    for arm, reference in [('fast', 'full'), ('narrow', 'full_narrow')]:
        full = results[reference]['terminal_tick']
        reports[arm]['reference'] = reference
        reports[arm]['relative_latency_error'] = (results[arm]['terminal_tick'] - full) / full
        reports[arm]['relative_request_latency_error'] = {
            request: (ticks - reports[reference]['request_latency_ticks'][request]) /
                     reports[reference]['request_latency_ticks'][request]
            for request, ticks in reports[arm]['request_latency_ticks'].items()}
        reports[arm]['traffic_equal'] = True
    (work / 'calibration.json').write_text(json.dumps(reports, sort_keys=True, indent=2) + '\n')


@pytest.mark.parametrize('mode,fast', [('FAST_EVENT', None), ('FAST_EVENT', {'queue_entries': 0}),
                                     ('FULL_TIMING', {'queue_entries': 1}), ('WINDOWED_TIMING', None)])
def test_incomplete_or_unbounded_modes_fail_closed(tmp_path, mode, fast):
    _, work = write_shared_fixture(tmp_path)
    path = work / 'config.yaml'
    config = yaml.safe_load(path.read_text())
    config['runtime']['mode'] = mode
    if fast is not None:
        config['runtime']['fast_event'] = fast
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    with pytest.raises(PlanError):
        load_agent_runtime_config(path)


@pytest.mark.parametrize('owner', ['router', 'link'])
def test_cpp_rejects_an_unbounded_fabric_queue_after_python_setup(tmp_path, owner):
    from .serving_runtime import PROBE
    _, work = write_shared_fixture(tmp_path)
    config_path = work / 'config.yaml'
    config = yaml.safe_load(config_path.read_text())
    config['runtime'].update(mode='FAST_EVENT', fast_event={'queue_entries': 1})
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    entry = work / 'unbounded_probe.py'
    queue = 'self.routers[0].port_buffers[0]' if owner == 'router' else 'self.int_links[0].buffers[0]'
    entry.write_text(
        'import runpy\n'
        'from m5.objects import SimpleNetwork\n'
        'original = SimpleNetwork.setup_buffers\n'
        'def inject(self):\n'
        '    original(self)\n'
        f'    {queue}.buffer_size = 0\n'
        'SimpleNetwork.setup_buffers = inject\n'
        f'runpy.run_path({str(PROBE)!r}, run_name="__m5_main__")\n')
    run_serving_fixture(work, 'rejected', entry=entry, rejection_code='finite SimpleNetwork')


@pytest.mark.parametrize('mode', ['FULL_TIMING', 'FAST_EVENT', 'WINDOWED_TIMING'])
def test_input_failure_drains_cache_and_fabric(tmp_path, mode):
    from mesh_ir.architecture import load_arch
    from .serving_runtime import assert_serving_error_quiescent
    program, work = write_shared_fixture(tmp_path, moe=True)
    expert = program.moe_expert_specs[0]
    relocation = next(row for row in program.relocations if row.symbol_sid == expert.weight_symbol_id)
    weight_address = load_arch(work / 'arch.yaml').regions[relocation.region_id].base + relocation.offset_bytes
    path = work / 'config.yaml'
    config = yaml.safe_load(path.read_text())
    config['runtime']['mode'] = mode
    if mode in ('FAST_EVENT', 'WINDOWED_TIMING'):
        config['runtime']['fast_event'] = {'queue_entries': 1}
    if mode == 'WINDOWED_TIMING':
        config['runtime']['windows'] = dict(initial_mode='FAST_EVENT', switches=[
            dict(at_tick=1000300000, mode='FULL_TIMING')])
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    _, result, metrics = run_serving_fixture(work, 'background', weight_policy='cached',
        extra_latencies=[dict(core=0, kind='read', index=0, address=weight_address, cycles=10000)],
        fault_kind='read', fault_index=1,
        fault_target_address=config['serving']['address_map']['input_arena']['base'])
    facts = read_facts_document(work / 'background/gate6_facts.tsv')
    assert_serving_error_quiescent(result, metrics, facts, [1, 4294967297])
    assert result['moe']['cache_fills']
    for cache in result['moe']['cache_state']:
        assert cache['live_tokens'] == cache['live_obligations'] == cache['pending_subscribers'] == 0
    last_fill = max(fill['done_tick'] for fill in result['moe']['cache_fills'])
    assert result['terminal_tick'] >= last_fill
    fabric = result['garnet'] if mode == 'FULL_TIMING' else result['aggregate_network']
    assert fabric['quiescent'] == 1
    if mode == 'WINDOWED_TIMING':
        assert result['garnet']['quiescent'] == 1
        manifest = json.loads((work / 'background' / 'timing_windows.json').read_text())
        assert len(manifest['windows']) == 2
        assert manifest['windows'][1]['start_tick'] >= last_fill


def test_twelve_users_complete_with_finite_fast_fabric(tmp_path):
    from .test_gate6_host_pools import run_host_pool_comparison
    outcomes = run_host_pool_comparison(tmp_path, 'route_replay', constrained=True,
                                        aggregate_queue_entries=2)
    reports = []
    for workload, cq, routes, histogram, batches, facts, result in outcomes:
        assert len(workload['users']) == 12
        assert len(cq) == 48 and all(status == 'SUCCESS' for _, status in cq)
        assert len([row for row in facts.events if row['kind'] == 'RELEASE_SUCCESS']) == 24
        assert facts.fatal is None
        assert any(len(batch) > 1 for batch in batches)
        assert all(bridge['idle'] for bridge in result['bridges'])
        fabric = result['aggregate_network']
        assert fabric['quiescent'] == 1
        assert fabric['queued_messages'] == fabric['serialization_units'] == 0
        assert fabric['forwarded_messages'] > 0 and fabric['blocked_cycles'] > 0
        reports.append(dict(terminal_tick=result['terminal_tick'],
                            completions=len(cq), tasks=24, fabric=fabric))
    assert reports[0] == reports[2]
    (tmp_path / 'pressure.json').write_text(json.dumps(reports, sort_keys=True, indent=2) + '\n')
