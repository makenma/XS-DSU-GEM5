import argparse
import json
import sys
from pathlib import Path

import m5
from m5.objects import (
    AddrRange, AxiGarnetBridge, AxiTensorDmaEngine, MeshDispatcher,
    MeshDummyCore, MeshExperimentObserver, MeshProgramLoader,
    NpuMemoryEndpoint, Root, SrcClockDomain, System, VoltageDomain,
)
from m5.util import addToPath, fatal

addToPath("../../")
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "util/mesh_ir"))

from common import Options
from ruby import Ruby
from mesh_ir.acceptance import canonical_digest
from mesh_ir.architecture import load_arch
from mesh_ir.effective import EffectiveArchitecture
from mesh_ir.experiment.config import CHANNELS
from mesh_ir.experiment.workload import experiment_hbm_targets, experiment_topology


def main():
    parser = argparse.ArgumentParser()
    Options.addNoISAOptions(parser)
    Ruby.define_options(parser)
    parser.add_argument("--experiment-config", type=Path, required=True)
    parser.set_defaults(network="garnet", topology="AxiMeshDie",
                        routing_algorithm=1, mem_type="DDR3_1600_8x8")
    args = parser.parse_args()
    if not (len(sys.argv) == 3 and sys.argv[1] == "--experiment-config" or
            len(sys.argv) == 2 and sys.argv[1].startswith("--experiment-config=")):
        parser.error("all experiment settings must come from --experiment-config")
    case = json.loads(args.experiment_config.read_text())
    if case["schema_version"] != 1:
        raise ValueError("unsupported experiment config schema")
    profile = case["profile"]
    arch = load_arch(case["arch_path"])
    effective = EffectiveArchitecture(arch)
    topology = experiment_topology(arch)
    program_dir = Path(case["program_dir"])
    workload = json.loads((program_dir / "workload.json").read_text())
    if tuple(arch.core_ids) != topology.core_ids or (arch.mesh_rows, arch.mesh_cols) != (5, 5):
        raise ValueError("experiment must instantiate the complete 5x5 core mesh")
    if arch.axi_data_bytes != 32 or arch.clock_hz != 2000000000:
        raise ValueError("experiment requires 32-byte AXI and 2 GHz clocks")
    if (arch.dma_read_outstanding, arch.dma_write_outstanding) != (case["n_read"], case["n_write"]):
        raise ValueError("case outstanding differs from effective architecture")
    if any(value not in profile["supported_n"] for value in (case["n_read"], case["n_write"])):
        raise ValueError("outstanding unsupported by the frozen resource profile")
    output = Path(case["output_dir"])
    output.mkdir(parents=True, exist_ok=True)
    hbm = next(region for region in arch.regions if region.kind == "HBM")
    hbm_targets = experiment_hbm_targets(arch)
    port_bytes = hbm_targets[0].ranges[0].size_bytes
    if workload["spec"]["hbm_port_bytes"] != port_bytes:
        raise ValueError("workload and backend address windows differ")
    error_target = next(
        target for target in arch.fabric.targets
        if target.dst_node == arch.fabric.default_error_target_node
    )
    runtime_targets = (*hbm_targets, error_target)
    runtime_target_nodes = {target.dst_node for target in runtime_targets}
    endpoint_map = [
        {"node": endpoint.src_node, "router": endpoint.router_id, "kind": "core"}
        for endpoint in arch.fabric.initiators
    ] + [
        {"node": target.dst_node, "router": target.router_id,
         "kind": "hbm" if target.synthetic_hbm is not None else "error"}
        for target in runtime_targets
    ]
    scenario = {
        "schema_version": 1, "name": "mesh_outstanding_buffer_experiment",
        "driver_mode": "mesh_program", "default_error_target": arch.fabric.default_error_target_node,
        "endpoint_to_router": {
            "initiators": [{"src_node": endpoint.src_node, "src_port": endpoint.src_port,
                            "router_id": endpoint.router_id,
                            "default_target": endpoint.default_target_node}
                           for endpoint in arch.fabric.initiators],
            "targets": [{"dst_node": target.dst_node, "router_id": target.router_id}
                        for target in runtime_targets],
        },
        "target_ranges": [{"dst_node": target.dst_node,
                           "start": hbm.base + target.ranges[0].offset_bytes,
                           "end": hbm.base + target.ranges[0].offset_bytes + target.ranges[0].size_bytes}
                          for target in hbm_targets],
        "quotas": [dict(src_node=item.src_node, src_port=item.src_port,
                        dst_node=item.dst_node, write_contexts=item.write_contexts,
                        write_beats=item.write_beats, read_contexts=item.read_contexts,
                        read_beats=item.read_beats)
                   for item in arch.fabric.quotas if item.dst_node in runtime_target_nodes],
    }
    scenario_path = output / "scenario.json"
    scenario_path.write_text(json.dumps(scenario, sort_keys=True))
    args.axi_scenario = str(scenario_path)
    args.axi_mesh_routers = 25
    args.axi_shared_router_endpoints = True
    args.mesh_rows = 5
    args.num_cpus = 25
    args.axi_data_width_bits = arch.axi_data_bytes * 8
    args.axi_id_width_bits = arch.axi_id_bits
    args.axi_max_outstanding_reads = arch.fabric.initiator.max_outstanding_reads
    args.axi_max_outstanding_writes = arch.fabric.initiator.max_outstanding_writes
    args.garnet_dual_lane = arch.fabric.network.dual_lane
    args.link_width_bits = arch.fabric.network.flit_bytes * 8
    args.axi_data_header_sideband = arch.fabric.axi.data_header_sideband
    args.vcs_per_vnet = arch.fabric.network.vcs_per_vnet
    args.router_latency = arch.fabric.network.router_latency_cycles
    args.link_latency = arch.fabric.network.link_latency_cycles
    args.garnet_vnet_classes = ",".join(arch.fabric.network.vnet_classes)
    args.garnet_buffers_per_vnet = ",".join(map(str, arch.fabric.network.router_input_depths))
    args.axi_source_fifo_depths = ",".join(map(str, arch.fabric.initiator.source_fifo_depths))
    args.axi_message_buffer_depths = ",".join(map(str, arch.fabric.initiator.message_buffer_depths))
    args.axi_local_delivery_depths = ",".join(map(str, arch.fabric.initiator.local_delivery_depths))
    args.axi_wire_header_bytes = ",".join(map(str, arch.fabric.axi.wire_header_bytes))
    args.axi_target_service_depths = ",".join(map(str, arch.fabric.target.service_queue_depths))
    args.axi_target_response_ready_depths = ",".join(map(str, arch.fabric.target.response_ready_depths))
    args.axi_source_pre_aw_bursts = arch.fabric.initiator.pre_aw_bursts
    args.axi_source_pre_aw_beats = arch.fabric.initiator.pre_aw_beats
    args.axi_b_rob_transactions = arch.fabric.initiator.b_reorder_transactions
    args.axi_r_rob_beats = arch.fabric.initiator.r_reorder_beats
    args.axi_orphan_w_transactions = arch.fabric.target.orphan_w_transactions
    args.axi_orphan_w_beats = arch.fabric.target.orphan_w_beats
    args.axi_target_read_contexts = arch.fabric.target.read_contexts
    args.axi_target_write_contexts = arch.fabric.target.write_contexts
    args.axi_target_read_response_beats = arch.fabric.target.read_response_beats
    args.axi_target_write_assembly_beats = arch.fabric.target.write_assembly_beats
    args.axi_target_base_latencies = ",".join(map(str, arch.fabric.target.base_latency_cycles))
    clock = f"{arch.clock_hz}Hz"
    args.sys_clock = clock
    args.ruby_clock = clock
    system = System(mem_ranges=[AddrRange(args.mem_size)])
    system.voltage_domain = VoltageDomain(voltage=args.sys_voltage)
    system.clk_domain = SrcClockDomain(clock=clock, voltage_domain=system.voltage_domain)
    Ruby.create_system(args, False, system, cpus=[])
    ruby = system.ruby
    ruby.clk_domain = SrcClockDomain(clock=clock, voltage_domain=system.voltage_domain)
    router_overrides = [
        ":".join(map(str, (item.router_id, item.input_port, item.vnet, item.depth)))
        for item in arch.fabric.network.router_input_overrides
    ]
    ruby.network.router_input_vc_depths = router_overrides
    ruby.network.ni_buffers_per_vnet = arch.fabric.network.ni_receive_depths
    ruby.network.yx_vnets = arch.fabric.network.yx_vnets
    targets = [getattr(ruby, f"axi_target_adapter{index}") for index in range(len(runtime_targets))]
    for target, adapter in zip(runtime_targets, targets):
        adapter.clk_domain = system.clk_domain
        adapter.synthetic_hbm_enabled = target.synthetic_hbm is not None
        if target.synthetic_hbm is not None:
            adapter.synthetic_hbm_bytes_per_cycle = target.synthetic_hbm.bytes_per_cycle
            adapter.synthetic_hbm_queue_depth = target.synthetic_hbm.queue_depth
    for index in range(len(hbm_targets)):
        seed_path = output / f"seed_{index}.txt"
        seeds = [f"{tile['address']:#x} {tile['useful_bytes']} {tile['expected_byte']}"
                 for tile in workload["tiles"]
                 if tile["direction"] == "read" and tile["target_index"] == index]
        seed_path.write_text("".join(line + "\n" for line in seeds))
        endpoint = NpuMemoryEndpoint(adapter=targets[index], seed_json=str(seed_path))
        endpoint.clk_domain = system.clk_domain
        setattr(system, f"hbm_endpoint_{index}", endpoint)
    cores = []
    for core_id in topology.core_ids:
        adapter = getattr(ruby, f"axi_initiator_adapter{core_id}")
        adapter.clk_domain = system.clk_domain
        bridge = AxiGarnetBridge(
            adapter=adapter, data_bus_bytes=arch.axi_data_bytes,
            aw_queue_depth=arch.dma_segment_queue_depth,
            ar_queue_depth=arch.dma_segment_queue_depth,
            axi_id_count=1 << arch.axi_id_bits,
            w_beats_per_cycle=arch.fabric.axi.w_beats_per_cycle,
        )
        engine = AxiTensorDmaEngine(
            bridge=bridge, data_bus_bytes=arch.axi_data_bytes,
            max_burst_beats=arch.axi_max_burst_beats,
            setup_cycles=arch.dma_setup_cycles,
            descriptor_queue_depth=arch.dma_descriptor_queue_depth,
            segment_queue_depth=arch.dma_segment_queue_depth,
            axi_id_count=1 << arch.axi_id_bits,
        )
        core = MeshDummyCore(
            core_id=core_id, dma=engine, decode_width=arch.decode_width,
            event_visibility_cycles=arch.event_visibility_cycles,
            admit_window=arch.admit_window,
            sram_bytes=arch.sram_bytes, sram_banks=arch.sram_banks,
            sram_alignment=arch.sram_base_alignment_bytes,
            sram_line_bytes=arch.sram_bank_interleave_bytes,
            sram_bank_queue_depth=arch.sram_bank_queue_depth,
            sram_read_ports=arch.sram_read_ports_per_bank,
            sram_write_ports=arch.sram_write_ports_per_bank,
            sram_read_bytes_per_cycle=arch.sram_read_bytes_per_cycle_per_bank,
            sram_write_bytes_per_cycle=arch.sram_write_bytes_per_cycle_per_bank,
            tensor_queue_depth=arch.tensor_queue_depth,
            tensor_setup_cycles=arch.tensor_setup_cycles,
            tensor_flush_cycles=arch.tensor_pipeline_flush_cycles,
            tensor_macs_per_cycle=arch.tensor_macs_per_cycle["fp16"],
            tensor_macs_by_dtype=effective.dtype_vector(arch.tensor_macs_per_cycle, "tensor"),
            vector_queue_depth=arch.vector_queue_depth,
            vector_elements_per_cycle=arch.vector_elements_per_cycle["fp16"],
            vector_elements_by_dtype=effective.dtype_vector(arch.vector_elements_per_cycle, "vector"),
            reduce_queue_depth=arch.reduce_queue_depth,
            reduce_setup_cycles=arch.reduce_setup_cycles,
            reduce_flush_cycles=arch.reduce_flush_cycles,
            reduce_ops_per_cycle=arch.reduce_ops_per_cycle["fp16"],
            reduce_ops_by_dtype=effective.dtype_vector(arch.reduce_ops_per_cycle, "reduce"),
        )
        core.clk_domain = system.clk_domain
        engine.clk_domain = system.clk_domain
        bridge.clk_domain = system.clk_domain
        setattr(system, f"mesh_core_{core_id}", core)
        cores.append(core)
    region_kinds = {"HBM": 0, "HOST_SHARED": 1, "CORE_SRAM_APERTURE": 2}
    loader = MeshProgramLoader(
        program_file=str(program_dir / "program.mshb"), cores=cores,
        arch_digest=effective.digest().hex(), effective_arch_digest=effective.digest().hex(),
        core_ids=list(arch.core_ids), sram_bytes=arch.sram_bytes,
        sram_banks=arch.sram_banks, sram_alignment=arch.sram_base_alignment_bytes,
        axi_data_bytes=arch.axi_data_bytes, axi_max_burst_beats=arch.axi_max_burst_beats,
        region_ids=list(range(len(arch.regions))),
        region_bases=[region.base for region in arch.regions],
        region_bytes=[region.bytes for region in arch.regions],
        region_tile_strides=[region.tile_stride or 0 for region in arch.regions],
        region_tile_bytes=[region.tile_bytes or 0 for region in arch.regions],
        region_kinds=[region_kinds[region.kind] for region in arch.regions],
    )
    system.mesh_loader = loader
    dispatcher = MeshDispatcher(
        loader=loader, cores=cores, network=ruby.network,
        result_json=str(output / "actual_result.json"),
        watchdog_ticks=profile["runtime"]["watchdog_ticks"],
    )
    dispatcher.clk_domain = system.clk_domain
    system.mesh_dispatcher = dispatcher
    last_read_slots = {}
    target_checks = []
    for tile in workload["tiles"]:
        if tile["direction"] == "read":
            last_read_slots[tile["core_id"], tile["slot"]] = tile
        else:
            target_checks.append(f"{tile['target_index']}:{tile['address']}:{tile['useful_bytes']}:{tile['expected_byte']}")
    core_checks = [f"{tile['core_id']}:{tile['sram_offset']}:{tile['useful_bytes']}:{tile['expected_byte']}"
                   for tile in last_read_slots.values()]
    observer = MeshExperimentObserver(
        cores=cores, targets=targets, network=ruby.network,
        core_memory_checks=core_checks, target_memory_checks=target_checks,
    )
    system.mesh_experiment_observer = observer
    root = Root(full_system=False, system=system)
    root.system.mem_mode = "timing"
    m5.ticks.setGlobalFrequency("1ps")
    m5.instantiate()
    config_record = {"schema_version": 1, "profile": profile, "topology": topology.name,
                     "arch_digest": effective.digest().hex(), "n_read": arch.dma_read_outstanding,
                     "n_write": arch.dma_write_outstanding, "channel_order": list(CHANNELS),
                     "buffer_depths": case["buffer_depths"],
                     "router_input_vc_depths": router_overrides,
                     "endpoint_map": endpoint_map, "full_timing": True,
                     "data_mode": "FUNCTIONAL_BYTES", "scenario_digest": canonical_digest(scenario),
                     "network": args.network, "routing_algorithm": args.routing_algorithm,
                     "topology_class": args.topology, "clock_hz": arch.clock_hz,
                     "ticks_per_second": 1000000000000}
    config_record["effective_digest"] = canonical_digest(config_record)
    (output / "effective_config.json").write_text(json.dumps(config_record, indent=2, sort_keys=True))
    observer.dumpSnapshot(str(output / "network_initial.json"))
    stop_tick = profile["runtime"]["max_sim_ticks"]
    sample_ticks = case["snapshot_ticks"]
    if sample_ticks != sorted(set(sample_ticks)) or any(type(tick) is not int or tick <= 0 or tick >= stop_tick for tick in sample_ticks):
        raise ValueError("snapshot ticks must be unique increasing interior ticks")
    cause = ""
    for tick in [*sample_ticks, stop_tick]:
        event = m5.simulate(tick - m5.curTick())
        cause = event.getCause()
        if cause.startswith("MESH_PROGRAM_DONE"):
            break
        if cause != "simulate() limit reached":
            fatal("experiment interrupted: %s", cause)
        if tick in sample_ticks:
            observer.dumpSnapshot(str(output / f"network_roi_{tick}.json"))
    if not cause.startswith("MESH_PROGRAM_DONE"):
        fatal("experiment did not complete: %s", cause)
    period = 1000000000000 // arch.clock_hz
    for name in ("first", "second"):
        next_edge = (m5.curTick() // period + 1) * period
        event = m5.simulate(next_edge - m5.curTick())
        if event.getCause() != "simulate() limit reached":
            fatal("unexpected drain event: %s", event.getCause())
        snapshot_name = "network_final.json" if name == "first" else "network_final2.json"
        observer.dumpSnapshot(str(output / snapshot_name))
        if not observer.drained():
            fatal("experiment not quiescent at %s drain edge", name)
    observer.dumpMemoryChecks(str(output / "memory_checks.json"))
    m5.stats.dump()
    print("MESH_EXPERIMENT_COMPLETE", m5.curTick())


main()
