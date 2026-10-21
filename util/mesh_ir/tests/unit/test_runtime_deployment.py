from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from mesh_ir.architecture import load_arch
from mesh_ir.runtime_deployment import FabricDeployment

ROOT = Path(__file__).resolve().parents[4]


def test_deployment_preserves_shared_routers_and_complete_address_ranges():
    arch = load_arch(ROOT / 'configs/example/ai_mesh/arch/mesh_1x2.yaml')
    deployment = FabricDeployment.from_arch(arch)
    scenario = deployment.scenario('probe', 'mesh_program')
    assert deployment.routers == 2
    assert [row['router_id'] for row in scenario['endpoint_to_router']['initiators']] == [0, 1]
    assert [row['router_id'] for row in scenario['endpoint_to_router']['targets']] == [0, 0, 1]
    hbm = arch.regions[0]
    assert any(row['start'] == hbm.base and row['end'] == hbm.base + hbm.bytes
               for row in scenario['target_ranges'])
    assert scenario['default_error_target'] == arch.fabric.default_error_target_node
    assert len(scenario['quotas']) == len(arch.fabric.quotas)
    assert deployment.peer_target_index(0) == 1
    assert deployment.target_index(100) == 0
    with pytest.raises(FrozenInstanceError):
        deployment.default_error_target_node = 42
    scenario['target_ranges'].clear()
    assert deployment.scenario('probe', 'mesh_program')['target_ranges']


def test_control_initiators_do_not_shift_core_adapter_identity():
    import yaml
    from mesh_ir.architecture import load_arch_text
    from tests.unit.test_control_initiators import control_document

    raw = control_document()
    raw['fabric']['initiators'].insert(0, raw['fabric']['initiators'].pop())
    deployment = FabricDeployment.from_arch(load_arch_text(yaml.safe_dump(raw)))
    assert deployment.core_initiator_index(0) == 1
    assert deployment.core_initiator_index(1) == 2
    assert deployment.peer_node(1) == 1001
    assert len(deployment.memory_targets) == 1
