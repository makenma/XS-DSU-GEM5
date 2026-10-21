import json

import pytest

from mesh_ir.compile_service import export_and_compile_factory
from tests.integration.support.garnet_harness import run_garnet, output_of
from tests.integration.test_gate2_gather_streaming import ARCH, CONFIG, INPUTS


@pytest.fixture(scope='module', params=['create_model', 'create_unaligned_model'])
def program_dir(tmp_path_factory, request):
    directory = tmp_path_factory.mktemp('gather-runtime')
    inputs = directory / 'inputs.json'
    inputs.write_text(json.dumps(INPUTS))
    export_and_compile_factory(f'examples.gather_carrier:{request.param}', inputs, None,
                               ARCH, CONFIG, directory / 'program')
    if request.param == 'create_unaligned_model':
        traffic = json.loads((directory / 'program/expected_traffic.json').read_text())
        gather, = [row for row in traffic['descriptors'] if row['row_bytes'] == 519 * 4]
        assert gather['count_kind'] == 'INDEXED_UPPER_BOUND'
    return directory / 'program'


@pytest.mark.parametrize('indices', [[7, 2, 7], [2, 7, 2], [-1, 2, 7], [7, 32768, 2]])
def test_real_axi_gather_uses_runtime_indices_and_drains(tmp_path, program_dir, indices):
    inputs = tmp_path / 'runtime-inputs.json'
    inputs.write_text(json.dumps({'indices': indices}))
    run = run_garnet(tmp_path, 'run', 'gather_streaming', program_dir,
                     artifact_dir=tmp_path, extra_args=('--gather-inputs', str(inputs),
                     '--dump-verify-bytes', '8192', '--dump-compute-bytes', '8192',
                     '--dump-source-bytes', '8192'))
    assert run.returncode == 0, output_of(run)[-12000:]
    assert 'gather occurrences: PASS' in output_of(run)


@pytest.mark.parametrize('failed_burst', [0, 5])
@pytest.mark.parametrize('outstanding', [1, 16])
def test_real_gather_read_error_drains_issued_occurrences(tmp_path, program_dir, failed_burst, outstanding):
    inputs = tmp_path / 'runtime-inputs.json'
    inputs.write_text(json.dumps(dict(indices=[7, 2, 7], read_error_burst=failed_burst)))
    run = run_garnet(tmp_path, 'fault', 'gather_streaming', program_dir,
        artifact_dir=tmp_path, extra_args=('--gather-inputs', str(inputs),
            '--read-outstanding', str(outstanding), '--dump-compute-bytes', '8192',
            '--dump-source-bytes', '8192'))
    assert run.returncode == 0, output_of(run)[-12000:]
    result = json.loads((tmp_path / 'actual_result.json').read_text())
    assert result['error_drained']
    frame, = result['instances']
    gather, = [row for core in frame['cores'].values()
               for row in core['observations']['descriptor_executions'] if row['index_reads']]
    assert gather['status'] == 'AXI_READ_ERROR'
    bursts = sorted((row for row in frame['bursts'] if row['descriptor_id'] == gather['descriptor_id']),
                    key=lambda row: row['burst_index'])
    assert [row['burst_index'] for row in bursts if row['errored']] == [failed_burst]
    assert len(gather['index_reads']) == (1 if failed_burst == 0 else 2)
    assert all(row['state'] != 'completed' for core in frame['cores'].values()
               for row in core['terminals'] if row['command_id'] > gather['command_id'])
    assert 'gather occurrences: PASS' in output_of(run)


@pytest.mark.parametrize('axi_bytes', [32, 64])
def test_gather_table_and_output_use_different_hbm_owners(tmp_path, axi_bytes):
    import yaml

    raw = yaml.safe_load(ARCH.read_text())
    raw['axi']['data_bytes'] = axi_bytes
    raw['fabric']['network']['flit_bytes'] = axi_bytes
    raw['fabric']['network']['dual_lane'] = False
    first = next(target for target in raw['fabric']['targets'] if target['dst_node'] == 100)
    table_range = next(row for row in first['ranges'] if row['region_id'] == 0)
    split = 64 << 20
    second = dict(name='output_memory', dst_node=101, router_id=1,
                  ranges=[dict(table_range, offset_bytes=split,
                               size_bytes=table_range['size_bytes'] - split)])
    table_range['size_bytes'] = split
    raw['fabric']['targets'].append(second)
    raw['fabric']['quotas'].extend(dict(row, dst_node=101) for row in
                                  list(raw['fabric']['quotas']) if row['dst_node'] == 100)
    arch = tmp_path / 'arch.yaml'
    arch.write_text(yaml.safe_dump(raw))
    inputs = tmp_path / 'inputs.json'
    inputs.write_text(json.dumps(INPUTS))
    output = tmp_path / 'program'
    export_and_compile_factory('examples.gather_carrier:create_model', inputs, None,
                               arch, CONFIG, output)
    runtime_inputs = tmp_path / 'runtime-inputs.json'
    runtime_inputs.write_text(json.dumps(dict(indices=[7, 2, 7])))
    run = run_garnet(tmp_path, 'run', 'gather_streaming', output, arch=arch,
                     artifact_dir=tmp_path, extra_args=('--gather-inputs', str(runtime_inputs),
                     '--dump-verify-bytes', '8192', '--dump-compute-bytes', '8192',
                     '--dump-source-bytes', '8192'))
    assert run.returncode == 0, output_of(run)[-12000:]
    result = json.loads((tmp_path / 'actual_result.json').read_text())
    endpoints = {row['node']: row for row in result['memory_endpoints']}
    assert set(endpoints) == {100, 101}
    assert all(row['idle'] for row in endpoints.values())
    assert endpoints[100]['committed_valid_bytes'] == 0
    assert endpoints[101]['committed_valid_bytes'] == 3 * 512 * 4
    assert endpoints[101]['verifies'][0]['bytes_hex']
    assert 'gather occurrences: PASS' in output_of(run)
    system = json.loads((tmp_path / 'run/config.json').read_text())['system']
    assert system['mesh_bridge_0']['data_bus_bytes'] == axi_bytes
    assert system['mesh_bridge_0']['w_beats_per_cycle'] == 1
    assert system['ruby']['network']['ni_flit_size'] == axi_bytes
    assert all(not row['dual_lane'] for row in system['ruby']['network']['routers'])
