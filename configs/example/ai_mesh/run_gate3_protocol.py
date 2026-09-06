import argparse
import json
import os
from pathlib import Path

import m5
from m5.objects import (
    AddrRange,
    AgentAxiDriver,
    Gate3ObservationRecorder,
    NpuServingFrontend,
    Root,
    SrcClockDomain,
    System,
    VoltageDomain,
)
from m5.util import addToPath, fatal

addToPath("../../")

from common import Options
from ruby import Ruby

from gate3_profiles import profile_named, target_plans


def _scenario(profile, options):
    initiators = [
        {"src_node": 0, "src_port": 0, "router_id": 0, "default_target": 2},
        {"src_node": 1, "src_port": 0, "router_id": 1, "default_target": 3},
    ]
    targets = [
        {"dst_node": 2, "router_id": 2},
        {"dst_node": 3, "router_id": 3},
    ]
    quota = {
        "write_contexts": min(options.axi_target_write_contexts, 8),
        "write_beats": min(options.axi_target_write_assembly_beats, 128),
        "read_contexts": min(options.axi_target_read_contexts, 8),
        "read_beats": min(options.axi_target_read_response_beats, 128),
    }
    return {
        "schema_version": 1,
        "name": profile.name.lower(),
        "driver_mode": "gate3_protocol",
        "endpoint_to_router": {
            "initiators": initiators,
            "targets": targets,
        },
        "default_error_target": 2,
        "cpu_core_count": 0,
        "cpu_garnet_ports": 0,
        "ucie_links": 0,
        "traffic_shaper": "AgentAxiDriver",
        "target_ranges": [
            {"dst_node": 2, "start": 0x30000000, "end": 0x30001000},
            {"dst_node": 3, "start": 0x10000000, "end": 0x10100000},
            {"dst_node": 3, "start": 0x30100000, "end": 0x30101000},
        ],
        "quotas": [
            {"src_node": source, "src_port": 0, "dst_node": target, **quota}
            for source in (0, 1)
            for target in (2, 3)
        ],
        **target_plans(profile),
        "planned_b_ejection": target_plans(profile).get("planned_b_ejection", []),
        "channel_injection_delay_cycles": {"aw": 0, "w": 0, "ar": 0},
        "response_ejection_stall_until_cycle": {"b": 0, "r": 0},
        "consumer_stall_until_cycle": {"b": 0, "r": 0},
    }


def main():
    parser = argparse.ArgumentParser()
    Options.addNoISAOptions(parser)
    Ruby.define_options(parser)
    parser.set_defaults(
        network="garnet",
        topology="AxiMeshDie",
        routing_algorithm=1,
        mesh_rows=2,
        mem_type="DDR3_1600_8x8",
        garnet_vnet_classes="ctrl,data,ctrl,ctrl,data",
        garnet_buffers_per_vnet="4,8,4,4,8",
        axi_data_width_bits=512,
        axi_mesh_routers=4,
    )
    parser.add_argument("--profile", required=True)
    parser.add_argument("--sim-tick-limit", type=int, required=True)
    args = parser.parse_args()
    if args.sim_tick_limit <= 0:
        fatal("Gate3 simulation tick limit must be positive")
    try:
        profile = profile_named(args.profile)
    except ValueError as error:
        fatal(str(error))

    artifact_dir = Path(
        os.environ.get("AI_MESH_ARTIFACT_DIR", m5.options.outdir)
    ).resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    scenario_path = artifact_dir / "gate3_scenario.json"
    scenario_path.write_text(
        json.dumps(_scenario(profile, args), sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    args.axi_scenario = str(scenario_path)

    system = System(mem_ranges=[AddrRange(args.mem_size)])
    system.voltage_domain = VoltageDomain(voltage=args.sys_voltage)
    system.clk_domain = SrcClockDomain(
        clock="1GHz", voltage_domain=system.voltage_domain
    )
    Ruby.create_system(args, False, system, cpus=[])
    system.ruby.clk_domain = system.clk_domain

    facts_path = artifact_dir / "gate3_facts.tsv"
    system.gate3_recorder = Gate3ObservationRecorder(
        output_path=str(facts_path)
    )
    recorder = system.gate3_recorder
    driver = AgentAxiDriver(
        master=system.ruby.axi_initiator_adapter0,
        target=system.ruby.axi_target_adapter1,
        recorder=recorder,
        data_bus_bytes=args.axi_data_width_bits // 8,
        sq_depth=profile.sq_depth,
        cq_depth=profile.cq_depth,
        control_bytes=8,
        max_burst_beats=256,
        host_base=0x10000000,
        npu_control_base=0x30000000,
        agent_proxy_control_base=0x30100000,
        profile=profile.name,
        request_count=profile.request_count,
        local_visibility_delay=profile.local_visibility_delay,
        request_id_capacity=profile.request_id_capacity,
        drain_cycles=profile.drain_cycles,
        doorbell_axi_id=16,
        ack_axi_id=17,
    )
    system.gate3_driver = driver
    frontend = NpuServingFrontend(
        master=system.ruby.axi_initiator_adapter1,
        control_target=system.ruby.axi_target_adapter0,
        recorder=recorder,
        data_bus_bytes=args.axi_data_width_bits // 8,
        sq_depth=profile.sq_depth,
        cq_depth=profile.cq_depth,
        control_bytes=8,
        max_burst_beats=256,
        host_base=0x10000000,
        npu_control_base=0x30000000,
        agent_proxy_control_base=0x30100000,
        profile=profile.name,
        request_count=profile.request_count,
        sq_read_issue_delay=profile.sq_read_issue_delay,
        doorbell_axi_id=16,
        sq_head_axi_id=18,
        cq_tail_axi_id=19,
        cq_entry_axi_id=20,
        msi_axi_id=32,
        msi_axi_id_count=4,
    )
    driver.clk_domain = system.clk_domain
    frontend.clk_domain = system.clk_domain
    system.gate3_frontend = frontend

    root = Root(full_system=False, system=system)
    root.system.mem_mode = "timing"
    m5.ticks.setGlobalFrequency("1ps")
    m5.instantiate()
    event = m5.simulate(args.sim_tick_limit)
    print("Exiting @ tick", m5.curTick(), "because", event.getCause())
    if os.environ.get("AI_MESH_CHILD_REPORT"):
        from gate3_acceptance import write_gate3_artifacts

        write_gate3_artifacts(
            profile,
            event.getCode(),
            facts_path,
            artifact_dir,
            os.environ["AI_MESH_CASE_ID"],
            os.environ["AI_MESH_SUBCASE"],
        )
    raise SystemExit(event.getCode())


main()
