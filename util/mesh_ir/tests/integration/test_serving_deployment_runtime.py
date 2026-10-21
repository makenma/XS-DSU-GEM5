import json
from dataclasses import replace

import pytest
import yaml

from mesh_ir.architecture import architecture_document, load_arch
from mesh_ir.gate3_oracle import read_facts_document
from mesh_ir.serving_programs import serving_program
from tests.integration.serving_runtime import REPO, assert_serving_error_quiescent, run_serving_fixture, write_serving_fixture


@pytest.mark.parametrize('separate_host', [False, True])
@pytest.mark.parametrize('fault_index', [-1, 0])
@pytest.mark.parametrize('input_parts', [1, 2])
def test_serving_driver_and_mesh_share_declared_fabric(tmp_path, separate_host, fault_index, input_parts):
    arch = load_arch(REPO / 'configs/example/ai_mesh/arch/mesh_1x2.yaml')
    if separate_host:
        memory = arch.fabric.targets[0]
        arch.fabric = replace(arch.fabric, targets=(
            replace(memory, ranges=(memory.ranges[0],)), *arch.fabric.targets[1:],
            replace(memory, name='host', dst_node=200, router_id=0,
                    ranges=(memory.ranges[1],))),
            quotas=arch.fabric.quotas + tuple(replace(row, dst_node=200)
                for row in arch.fabric.quotas if row.dst_node == 100))
    arch_path = tmp_path / 'source.arch.yaml'
    arch_path.write_text(yaml.safe_dump(architecture_document(arch)))
    work = write_serving_fixture(tmp_path, serving_program(arch, output_tokens=2, input_parts=input_parts), arch_path)
    facts, result, metrics = run_serving_fixture(work, 'deployment', fault_index)
    out = work / 'deployment'
    configuration = json.loads((out / 'config.json').read_text())['system']
    scenario = json.loads((out / 'gate6_scenario.json').read_text())
    assert len(configuration['ruby']['network']['routers']) == 2
    assert result['terminal'] == ('DONE' if fault_index < 0 else 'ERROR_DRAINED')
    assert metrics['metadata_reads_validated'] == 1
    assert metrics['frontend_drained'] == 1
    assert {row['src_node'] for row in scenario['endpoint_to_router']['initiators']} >= {0, 1}
    nodes = [100, 200] if separate_host else [100]
    assert [row['node'] for row in result['memory_endpoints']] == nodes
    assert configuration['mesh_loader']['memory_endpoints'] == [f'system.mesh_endpoint_{node}' for node in nodes]
    assert configuration['gate6_driver']['target'] == configuration[f'mesh_endpoint_{nodes[-1]}']['adapter']
    assert all(not row['dual_lane'] for row in configuration['ruby']['network']['routers'])
    assert configuration['mesh_core_0']['sram_line_bytes'] == arch.sram_bank_interleave_bytes
    if fault_index >= 0:
        document = read_facts_document(out / 'gate6_facts.tsv')
        assert_serving_error_quiescent(result, metrics, document, [1])


def test_serving_control_only_uses_the_same_deployment(tmp_path):
    arch = load_arch(REPO / 'configs/example/ai_mesh/arch/mesh_1x2.yaml')
    work = write_serving_fixture(tmp_path, serving_program(arch, output_tokens=2))
    _, result, metrics = run_serving_fixture(work, 'control', expect_execution=False,
        extra_args=('--agent-only', '--executor', 'full_context_surrogate'))
    assert result is None
    assert metrics['completed_tasks'] == metrics['frontend_drained'] == 1
    system = json.loads((work / 'control/config.json').read_text())['system']
    assert len(system['ruby']['network']['routers']) == 2
    assert not any(name.startswith('mesh_core_') for name in system)
