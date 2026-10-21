import json

import pytest

from .test_gate6_host_pools import run_host_pool_comparison


@pytest.mark.parametrize('provider', ['route_replay', 'histogram_replay'])
def test_twelve_users_with_constrained_transport(tmp_path, provider):
    outcomes = run_host_pool_comparison(tmp_path, provider, constrained=True)
    reports = []
    for arm in ('finite', 'large', 'repeat'):
        system = json.loads((tmp_path / arm / 'run/config.json').read_text())['system']
        driver = system['gate6_driver']
        target = system['ruby'][driver['target'].split('.')[-1]]
        assert target['service_depths'] == [2, 2]
        assert target['response_ready_depths'] == [2, 2]
        assert system['ruby']['network']['buffers_per_vnet'] == [2] * 5
        assert [driver['host_compile_slots'], driver['host_test_slots'],
                driver['host_log_parse_slots']] == ([16] * 3 if arm == 'large' else [4, 6, 4])
    for workload, cq, routes, histogram, batches, facts, result in outcomes:
        assert facts.fatal is None
        completions = [e for e in facts.events if e['kind'] == 'CQ_CONSUME']
        assert len(completions) == len(cq) == 48
        assert len({e['request_id'] for e in completions}) == 48
        assert any(len(batch) > 1 for batch in batches)
        assert sum(channel['ni_credit_stall_vc_cycles']
                   for channel in result['network_stalls'].values()) > 0
        starts = {e['request_id']: e['tick'] for e in facts.events
                  if e['kind'] == 'TASK_THINK_READY'}
        releases = {e['request_id']: e['tick'] for e in facts.events
                    if e['kind'] == 'RELEASE_SUCCESS'}
        assert len(starts) == len(releases) == 24
        latencies = []
        for user in workload['users']:
            user_starts = sorted(tick for request, tick in starts.items()
                                 if request >> 32 == user['user_id'])
            user_releases = sorted(tick for request, tick in releases.items()
                                   if request >> 32 == user['user_id'])
            assert len(user_starts) == len(user_releases) == 2
            latencies.extend(end - begin for begin, end in zip(user_starts, user_releases))
        latencies.sort()
        assert latencies[0] > 0
        assert all(row['response_tick'] >= row['ar_aw_tick'] > 0
                   for row in result['burst_timings'])
        reports.append(dict(task_count=len(latencies), max_task_ticks=latencies[-1],
                            p95_task_ticks=latencies[22], terminal_tick=result['terminal_tick']))
    assert reports[0] == reports[2]
    (tmp_path / 'pressure.json').write_text(json.dumps(reports, sort_keys=True, indent=2))
