from dataclasses import replace
from pathlib import Path

import pytest

from mesh_ir.architecture import ControlInitiatorRole, load_arch
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.runtime_deployment import FabricDeployment, ServingDeployment

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def arch():
    return load_arch(ROOT / 'configs/example/ai_mesh/arch/mesh_1x2.yaml')


@pytest.mark.parametrize('host_base', (0x10000000, 0x100000000))
def test_serving_composes_existing_memory_and_shared_routers(arch, host_base):
    base = FabricDeployment.from_arch(arch)
    result = ServingDeployment.from_arch(arch, 0x30000000, 0x30100000,
                                         host_base, 0x100000)
    deployment = result.fabric
    assert deployment.network == base.network
    assert deployment.peer_nodes == base.peer_nodes
    assert deployment.initiators[:len(base.initiators)] == base.initiators
    assert deployment.targets[:len(base.targets)] == base.targets
    assert deployment.quotas[:len(base.quotas)] == base.quotas
    assert set(base.address_ranges) <= set(deployment.address_ranges)
    assert deployment.initiators[result.driver_index].role is ControlInitiatorRole.HOST_DRIVER
    assert deployment.initiators[result.frontend_index].role is ControlInitiatorRole.SERVING_FRONTEND
    assert deployment.initiators[result.driver_index].router_id == 0
    assert deployment.core_initiator_index(1) == base.core_initiator_index(1)
    host_node = deployment.targets[result.host_target_index].dst_node
    host = deployment.memory_map.partition([(host_base, 0x100000)])
    assert host[host_node] == [(host_base, 0x100000)]
    if host_base == 0x100000000:
        assert host_node == 100
        assert deployment.memory_nodes == base.memory_nodes
    else:
        assert host_node not in base.memory_nodes
    npu_node = deployment.targets[result.npu_control_target_index].dst_node
    assert npu_node not in deployment.memory_nodes
    scenario = result.scenario()
    assert scenario['default_error_target'] == base.default_error_target_node
    assert len(scenario['quotas']) == len(deployment.initiators) * len(deployment.targets)
    assert scenario['traffic_shaper'] == 'AgentAxiDriver'
    assert base == FabricDeployment.from_arch(arch)


@pytest.mark.parametrize('npu, proxy, host, size', (
    (0x30000000, 0x30000000, 0x10000000, 0x100000),
    (0x800000000, 0x30100000, 0x10000000, 0x100000),
    (0x30000000, 0x30100000, 0x0fffffff0, 0x100000),
    (0x30000000, 0x30100000, 0x400000000, 0x100000),
    (0x30000000, 0x30100000, 0x10000000, 0),
))
def test_serving_rejects_overlaps_partial_owners_and_peer_memory(arch, npu, proxy, host, size):
    with pytest.raises(MeshIrError):
        ServingDeployment.from_arch(arch, npu, proxy, host, size)


def test_serving_rejects_control_quota_overcommit(arch):
    arch.fabric = replace(arch.fabric, target=replace(arch.fabric.target,
                           write_contexts=32, read_contexts=32))
    with pytest.raises(MeshIrError, match='quota'):
        ServingDeployment.from_arch(arch, 0x30000000, 0x30100000,
                                    0x100000000, 0x100000)


def test_serving_allocates_protocol_sources_below_u16_even_with_large_target_nodes(arch):
    arch.fabric = replace(arch.fabric, targets=tuple(
        replace(row, dst_node=100000) if row.dst_node == 100 else row
        for row in arch.fabric.targets),
        initiators=tuple(replace(row, default_target_node=100000)
                         for row in arch.fabric.initiators),
        default_error_target_node=100000,
        quotas=tuple(replace(row, dst_node=100000) if row.dst_node == 100 else row
                     for row in arch.fabric.quotas))
    result = ServingDeployment.from_arch(arch, 0x30000000, 0x30100000,
                                         0x100000000, 0x100000)
    assert all(row.src_node <= 65535 for row in result.fabric.initiators)
    assert result.fabric.targets[result.host_target_index].dst_node == 100000
