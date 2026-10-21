import pytest

from tests.integration.support.runtime_harness import build_program, result_of, run_mock


@pytest.mark.parametrize('instances', [1, 2])
def test_empty_core_plan_has_an_instance_frame(tmp_path, instances):
    directory = build_program(tmp_path, 'single')
    run = run_mock(tmp_path, 'run', 'single', directory, ('--instances', str(instances)))
    assert run.returncode == 0, run.stdout + run.stderr
    result = result_of(directory)
    plans = {row['core_id']: row['dispatch_plan'] for row in result['cores']}
    idle = {core for core, commands in plans.items() if not commands}
    assert idle
    assert len(result['instances']) == instances
    for frame in result['instances']:
        assert set(map(int, frame['cores'])) == set(plans)
        for core in idle:
            ledger = frame['cores'][str(core)]
            assert ledger['terminals'] == []
            assert ledger['observations']['descriptor_executions'] == []
            assert ledger['resources']['live_commands'] == 0
