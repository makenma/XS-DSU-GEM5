from mesh_ir.architecture import ArchManifest, TargetEndpoint
from mesh_ir.effective import EffectiveArchitecture


def fabric_option_defaults(arch: ArchManifest) -> dict[str, object]:
    return {
        'axi_shared_router_endpoints': True,
        'axi_data_width_bits': arch.axi_data_bytes * 8,
        'axi_id_width_bits': arch.axi_id_bits,
        'axi_max_outstanding_reads': arch.fabric.initiator.max_outstanding_reads,
        'axi_max_outstanding_writes': arch.fabric.initiator.max_outstanding_writes,
        'garnet_dual_lane': arch.fabric.network.dual_lane,
        'link_width_bits': arch.fabric.network.flit_bytes * 8,
        'axi_data_header_sideband': arch.fabric.axi.data_header_sideband,
        'vcs_per_vnet': arch.fabric.network.vcs_per_vnet,
        'router_latency': arch.fabric.network.router_latency_cycles,
        'link_latency': arch.fabric.network.link_latency_cycles,
        'garnet_vnet_classes': ",".join(arch.fabric.network.vnet_classes),
        'garnet_buffers_per_vnet': ",".join(map(str, arch.fabric.network.router_input_depths)),
        'axi_source_fifo_depths': ",".join(map(str, arch.fabric.initiator.source_fifo_depths)),
        'axi_message_buffer_depths': ",".join(map(str, arch.fabric.initiator.message_buffer_depths)),
        'axi_local_delivery_depths': ",".join(map(str, arch.fabric.initiator.local_delivery_depths)),
        'axi_wire_header_bytes': ",".join(map(str, arch.fabric.axi.wire_header_bytes)),
        'axi_target_service_depths': ",".join(map(str, arch.fabric.target.service_queue_depths)),
        'axi_target_response_ready_depths': ",".join(map(str, arch.fabric.target.response_ready_depths)),
        'axi_source_pre_aw_bursts': arch.fabric.initiator.pre_aw_bursts,
        'axi_source_pre_aw_beats': arch.fabric.initiator.pre_aw_beats,
        'axi_b_rob_transactions': arch.fabric.initiator.b_reorder_transactions,
        'axi_r_rob_beats': arch.fabric.initiator.r_reorder_beats,
        'axi_orphan_w_transactions': arch.fabric.target.orphan_w_transactions,
        'axi_orphan_w_beats': arch.fabric.target.orphan_w_beats,
        'axi_target_read_contexts': arch.fabric.target.read_contexts,
        'axi_target_write_contexts': arch.fabric.target.write_contexts,
        'axi_target_read_response_beats': arch.fabric.target.read_response_beats,
        'axi_target_write_assembly_beats': arch.fabric.target.write_assembly_beats,
        'axi_target_base_latencies': ",".join(map(str, arch.fabric.target.base_latency_cycles)),
    }


def network_parameters(arch: ArchManifest) -> dict[str, object]:
    network = arch.fabric.network
    return dict(
        router_input_vc_depths=[
            ":".join(map(str, (row.router_id, row.input_port, row.vnet, row.depth)))
            for row in network.router_input_overrides],
        ni_buffers_per_vnet=network.ni_receive_depths,
        yx_vnets=network.yx_vnets,
    )


def target_parameters(target: TargetEndpoint) -> dict[str, object]:
    result = dict(synthetic_hbm_enabled=target.synthetic_hbm is not None)
    if target.synthetic_hbm is not None:
        result.update(synthetic_hbm_bytes_per_cycle=target.synthetic_hbm.bytes_per_cycle,
                      synthetic_hbm_queue_depth=target.synthetic_hbm.queue_depth)
    return result


def core_parameters(arch: EffectiveArchitecture) -> dict[str, object]:
    return dict(
        decode_width=arch.decode_width,
        event_visibility_cycles=arch.event_visibility_cycles,
        admit_window=arch.admit_window,
        tensor_queue_depth=arch.tensor_queue_depth,
        tensor_setup_cycles=arch.tensor_setup_cycles,
        tensor_flush_cycles=arch.tensor_pipeline_flush_cycles,
        tensor_macs_per_cycle=arch.tensor_macs_per_cycle["fp16"],
        tensor_macs_by_dtype=arch.dtype_vector(
            arch.tensor_macs_per_cycle, "tensor macs"),
        vector_queue_depth=arch.vector_queue_depth,
        vector_elements_per_cycle=(
            arch.vector_elements_per_cycle["fp16"]),
        vector_elements_by_dtype=arch.dtype_vector(
            arch.vector_elements_per_cycle, "vector elements"),
        reduce_queue_depth=arch.reduce_queue_depth,
        reduce_setup_cycles=arch.reduce_setup_cycles,
        reduce_flush_cycles=arch.reduce_flush_cycles,
        reduce_ops_per_cycle=arch.reduce_ops_per_cycle["fp16"],
        reduce_ops_by_dtype=arch.dtype_vector(
            arch.reduce_ops_per_cycle, "reduce ops"),
        sram_banks=arch.sram_banks,
        sram_bytes=arch.sram_bytes,
        sram_alignment=arch.sram_base_alignment_bytes,
        sram_line_bytes=arch.sram_bank_interleave_bytes,
        sram_read_bytes_per_cycle=(
            arch.sram_read_bytes_per_cycle_per_bank),
        sram_write_bytes_per_cycle=(
            arch.sram_write_bytes_per_cycle_per_bank),
        sram_bank_queue_depth=arch.sram_bank_queue_depth,
        sram_read_ports=arch.sram_read_ports_per_bank,
        sram_write_ports=arch.sram_write_ports_per_bank,
    )
