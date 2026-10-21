from dataclasses import replace
from pathlib import Path

from mesh_ir.architecture import load_arch
from mesh_ir.runtime_fabric import fabric_option_defaults, network_parameters, target_parameters
from mesh_ir.architecture import SyntheticHbmConfig


def test_fabric_projection_uses_architecture_bus_and_buffer_contracts():
    root = Path(__file__).resolve().parents[4]
    arch = load_arch(root / 'configs/example/ai_mesh/arch/mesh_1x2.yaml')
    arch.axi_data_bytes = 64
    arch.fabric = replace(arch.fabric, network=replace(
        arch.fabric.network, flit_bytes=64, dual_lane=False))
    options = fabric_option_defaults(arch)
    assert options['axi_data_width_bits'] == 512
    assert options['link_width_bits'] == 512
    assert options['garnet_dual_lane'] is False
    assert options['axi_shared_router_endpoints'] is True
    assert options['axi_max_outstanding_reads'] == arch.fabric.initiator.max_outstanding_reads
    assert options['axi_target_read_contexts'] == arch.fabric.target.read_contexts
    assert options['axi_source_fifo_depths'] == ','.join(map(str, arch.fabric.initiator.source_fifo_depths))
    assert network_parameters(arch)['yx_vnets'] == arch.fabric.network.yx_vnets
    target = arch.fabric.targets[0]
    assert target_parameters(target) == dict(synthetic_hbm_enabled=False)
    target = replace(target, synthetic_hbm=SyntheticHbmConfig(64, 8))
    assert target_parameters(target) == dict(synthetic_hbm_enabled=True,
        synthetic_hbm_bytes_per_cycle=64, synthetic_hbm_queue_depth=8)
