from copy import deepcopy

import pytest

from mesh_ir.runtime_reconciliation import ReconciliationError, verified_dma_timing_phases


@pytest.fixture
def scoped_dma():
    frame = {'instance': 1, 'cores': {'0': {'observations': {'descriptor_executions': [], 'runtime_descriptor_executions': []}}}, 'bursts': []}
    result = {'instances': [frame], 'dma_timings': []}
    for domain in (0, 1, 2):
        identity = dict(instance=1, core_id=0, command_id=1, descriptor_id=1, generation=0,
                        domain=domain, region_group_id=int(domain != 0), region_id=int(domain == 1))
        execution = dict(identity, completed=True, completion_tick=100, submit_tick=1, status='OK')
        field = 'runtime_descriptor_executions' if domain else 'descriptor_executions'
        frame['cores']['0']['observations'][field].append(execution)
        frame['bursts'].append(dict(identity, channel='AR', ar_aw_tick=10 + domain, response_tick=90 + domain))
        result['dma_timings'].append(dict(identity, dma_kind=1, done_tick=100, first_ar_tick=10 + domain,
            last_r_tick=90 + domain, first_aw_tick=0, first_b_tick=0, first_w_tick=0, local_commit_tick=95 + domain))
    return result


def test_same_ordinals_and_completion_ticks_are_distinct_across_domains(scoped_dma):
    joined, bursts = verified_dma_timing_phases(scoped_dma)
    assert sum(map(len, joined.values())) == 3
    assert len(bursts) == 3


@pytest.mark.parametrize('mutation', ['borrow-static', 'foreign-region', 'missing-runtime-execution', 'missing-runtime-timing', 'move-instance'])
def test_dynamic_timing_cannot_borrow_static_or_foreign_execution(scoped_dma, mutation):
    result = deepcopy(scoped_dma)
    if mutation == 'borrow-static':
        result['dma_timings'][1]['first_ar_tick'] = 10
    elif mutation == 'foreign-region':
        result['instances'][0]['bursts'][1]['region_id'] = 2
    elif mutation == 'missing-runtime-execution':
        result['instances'][0]['cores']['0']['observations']['runtime_descriptor_executions'].pop()
    elif mutation == 'missing-runtime-timing':
        result['dma_timings'].pop()
    else:
        result['dma_timings'][1]['instance'] = 2
    with pytest.raises(ReconciliationError):
        verified_dma_timing_phases(result)
