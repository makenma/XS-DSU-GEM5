import pytest

from mesh_ir.runtime_reconciliation import verified_dma_timing_phases


@pytest.mark.parametrize('cores', [1, 2, 4])
def test_each_frame_burst_is_owned_once_regardless_of_other_cores(cores):
    identity = {'instance': 1, 'domain': 0, 'region_group_id': 0, 'region_id': 0, 'command_id': 2, 'generation': 0}
    execution = {**identity, 'descriptor_id': 1, 'generation': 0, 'completed': True,
                 'completion_tick': 30}
    burst = {**identity, 'core_id': 0, 'descriptor_id': 1, 'generation': 0,
             'channel': 'AR', 'ar_aw_tick': 10, 'response_tick': 20}
    frame = {'instance': 1, 'cores': {str(core): {'observations': {'descriptor_executions':
             [execution] if core == 0 else []}} for core in range(cores)},
             'bursts': [burst]}
    result = {'instances': [frame], 'dma_timings': [{**identity, 'core_id': 0,
              'descriptor_id': 1, 'done_tick': 30, 'first_ar_tick': 10,
              'last_r_tick': 20, 'first_aw_tick': 0, 'first_b_tick': 0}]}
    joined, indexed = verified_dma_timing_phases(result)
    assert sum(map(len, joined.values())) == 1
    assert sum(map(len, indexed.values())) == len(frame['bursts'])
