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
from mesh_ir.builder import load_arch
from mesh_ir.effective import EffectiveArchitecture
from mesh_ir.experiment.config import CHANNELS, Topology, validated_yx_vnets


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
    topology = Topology(case["topology"])
    arch = load_arch(case["arch_path"])
    effective = EffectiveArchitecture(arch)
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
    if profile["axi"]["w_beats_per_cycle"] != 1:
        raise ValueError("formal experiment requires one local W beat per cycle")
    output = Path(case["output_dir"])
    output.mkdir(parents=True, exist_ok=True)
    hbm = next(region for region in arch.regions if region.kind == "HBM")
    port_bytes = profile["backend"]["port_bytes"]
    if workload["spec"]["hbm_port_bytes"] != port_bytes:
        raise ValueError("workload and backend address windows differ")
    endpoint_map = topology.endpoints()
    target_nodes = [topology.target_node(index) for index in range(len(topology.hbm_routers))]
    all_targets = target_nodes + [topology.error_node]
    quota = profile["axi"]["target_quota"]
    scenario = {
        "schema_version": 1, "name": "mesh_outstanding_buffer_experiment",
        "driver_mode": "mesh_program", "default_error_target": topology.error_node,
        "endpoint_to_router": {
            "initiators": [{"src_node": core, "src_port": 0, "router_id": core,
                            "default_target": target_nodes[core % len(target_nodes)]}
                           for core in topology.core_ids],
            "targets": [{"dst_node": entry["node"], "router_id": entry["router"]}
                        for entry in endpoint_map if entry["kind"] != "core"],
        },
        "target_ranges": [{"dst_node": node, "start": hbm.base + index * port_bytes,
                           "end": hbm.base + (index + 1) * port_bytes}
                          for index, node in enumerate(target_nodes)],
        "quotas": [{"src_node": core, "src_port": 0, "dst_node": node, **quota}
                   for core in topology.core_ids for node in all_targets],
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
    args.axi_max_outstanding_reads = arch.dma_read_outstanding
    args.axi_max_outstanding_writes = arch.dma_write_outstanding
    network_profile = profile["network"]
    args.garnet_dual_lane = network_profile.get("dual_lane", False)
    args.link_width_bits = network_profile["flit_bytes"] * 8
    args.axi_data_header_sideband = profile["axi"].get("data_header_sideband", False)
    args.vcs_per_vnet = network_profile["vcs_per_vnet"]
    args.router_latency = network_profile["router_latency"]
    args.link_latency = network_profile["link_latency"]
    args.garnet_vnet_classes = "ctrl,data,ctrl,ctrl,data"
    args.garnet_buffers_per_vnet = ",".join(map(str, case["buffer_depths"]))
    for key in ("source_fifo_depths", "message_buffer_depths", "local_delivery_depths",
                "wire_header_bytes", "target_service_depths", "target_response_ready_depths"):
        setattr(args, "axi_" + key, ",".join(map(str, profile["axi"][key])))
    for key in ("source_pre_aw_bursts", "source_pre_aw_beats", "b_rob_transactions",
                "r_rob_beats", "orphan_w_transactions", "orphan_w_beats"):
        setattr(args, "axi_" + key, profile["axi"][key])
    args.axi_target_read_contexts = 25 * quota["read_contexts"]
    args.axi_target_write_contexts = 25 * quota["write_contexts"]
    args.axi_target_read_response_beats = 25 * quota["read_beats"]
    args.axi_target_write_assembly_beats = 25 * quota["write_beats"]
    latency = profile["backend"]["base_latency_cycles"]
    args.axi_target_base_latencies = f"{latency},{latency}"
    clock = f"{arch.clock_hz}Hz"
    args.sys_clock = clock
    args.ruby_clock = clock
    system = System(mem_ranges=[AddrRange(args.mem_size)])
    system.voltage_domain = VoltageDomain(voltage=args.sys_voltage)
    system.clk_domain = SrcClockDomain(clock=clock, voltage_domain=system.voltage_domain)
    Ruby.create_system(args, False, system, cpus=[])
    ruby = system.ruby
    ruby.clk_domain = SrcClockDomain(clock=clock, voltage_domain=system.voltage_domain)
    ruby.network.router_input_vc_depths = case["router_input_vc_depths"]
    ruby.network.ni_buffers_per_vnet = network_profile["ni_depths"]
    ruby.network.yx_vnets = validated_yx_vnets(network_profile.get("yx_vnets", ()))
    targets = [getattr(ruby, f"axi_target_adapter{index}") for index in range(len(all_targets))]
    for index, adapter in enumerate(targets):
        adapter.clk_domain = system.clk_domain
        adapter.synthetic_hbm_enabled = index < len(target_nodes)
        adapter.synthetic_hbm_bytes_per_cycle = profile["backend"]["bytes_per_cycle"]
        adapter.synthetic_hbm_queue_depth = profile["backend"]["queue_depth"]
    for index in range(len(target_nodes)):
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
            w_beats_per_cycle=profile["axi"]["w_beats_per_cycle"],
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
            sram_line_bytes=arch.sram_read_bytes_per_cycle_per_bank,
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
        arch_digest=effective.base_digest().hex(), effective_arch_digest=effective.digest().hex(),
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
                     "router_input_vc_depths": case["router_input_vc_depths"],
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
