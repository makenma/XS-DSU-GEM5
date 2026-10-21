import json

import pytest
import yaml

from mesh_ir.architecture import load_arch
from mesh_ir.ir.common import TensorRole
from tests.integration.serving_runtime import ARCH_YAML, run_serving_fixture, write_serving_fixture, write_serving_observation_probe
from tests.integration.support.workspace_program import build_workspace_program


@pytest.mark.parametrize('output_rows,publish_rows,model_role', [
    (4, 4, TensorRole.WEIGHT), (4, 2, TensorRole.WEIGHT), (6, 4, TensorRole.WEIGHT),
    (6, 4, TensorRole.STATE), (6, 4, TensorRole.CONSTANT)])
def test_prefill_driver_publishes_computed_workspace_and_drains(tmp_path, output_rows, publish_rows,
        model_role, arch_path=ARCH_YAML):
    program, arch = build_workspace_program(load_arch(arch_path), output_rows=output_rows,
        publish_rows=publish_rows, model_role=model_role)
    output_bytes = output_rows * 16
    write_serving_fixture(tmp_path, program, arch_path)
    config = yaml.safe_load((tmp_path / 'config.yaml').read_text())
    address_map = config['serving']['address_map']
    addresses = {name: address_map[name + '_arena']['base'] for name in ('output', 'workspace')}
    probe = write_serving_observation_probe(tmp_path, arch, [(value, output_bytes) for value in addresses.values()])
    facts, result, metrics = run_serving_fixture(tmp_path, 'runtime', entry=probe)
    assert result['terminal'] == 'DONE'
    assert metrics['completed_tasks'] == metrics['frontend_drained'] == 1
    assert metrics['metadata_reads_validated'] == 1
    assert metrics['kv_session_records'] == metrics['kv_session_tombstones'] == 0
    assert all(metrics[key] == 0 for key in ('live_contexts', 'live_cq_obligations', 'live_sq_intakes', 'live_submissions'))
    checks = [check for endpoint in result['memory_endpoints'] for check in endpoint['verifies'] if check['bytes_hex']]
    (tmp_path / 'observed-output.json').write_text(json.dumps(checks, indent=2))
    assert len(checks) == 2
    assert checks[0]['bytes_hex'] == checks[1]['bytes_hex']
    assert len(bytes.fromhex(checks[0]['bytes_hex'])) == output_bytes
    assert bytes.fromhex(checks[0]['bytes_hex']) != bytes(output_bytes)
    computed = [output for instance in result['instances'] for core in instance['cores'].values()
                for output in core['observations']['compute_outputs']]
    assert len(computed) == 1
    assert ''.join(row['bytes_hex'] for row in computed[0]['rows']) == checks[0]['bytes_hex']
    assert len(result['instances']) == 2
    assert len(result['dma_timings']) == 3 + 2 * ((output_rows + publish_rows - 1) // publish_rows)
    assert all(row['done_tick'] > 0 for row in result['dma_timings'])
    assert all(bridge['idle'] == 1 for bridge in result['bridges'])


def test_prefill_driver_drains_workspace_store_error_without_kv_terminal(tmp_path):
    from mesh_ir.gate3_oracle import read_facts_document
    from tests.integration.serving_runtime import assert_serving_error_quiescent

    program, _ = build_workspace_program()
    write_serving_fixture(tmp_path, program)
    _, result, metrics = run_serving_fixture(tmp_path, 'fault', fault_index=0)
    facts = read_facts_document(tmp_path / 'fault/gate6_facts.tsv')
    assert_serving_error_quiescent(result, metrics, facts, [1])
    assert metrics['serving_instance_count'] == 0
