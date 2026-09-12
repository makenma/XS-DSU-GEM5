import json
import os
import sys
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
from m5.util import addToPath

addToPath("../../")

REPO = Path(__file__).resolve().parents[3]
MESH_IR_ROOT = REPO / "util" / "mesh_ir"
if str(MESH_IR_ROOT) not in sys.path:
    sys.path.insert(0, str(MESH_IR_ROOT))

from mesh_ir.agent_config import agent_proxy_control_base

from common import Options
from ruby import Ruby


GATE4_DEFAULTS = {
    "host_compute_tokens": 24,
    "host_service_queue_depth": 64,
    "host_aging_threshold_ns": 0,
    "stop_after_completed_tasks": 0,
    "stop_accepting_enabled": False,
    "stop_accepting_at_tick": 0,
    "request_id_capacity": 64,
    "host_fault_site": "",
    "host_fault_task": 0,
    "host_fault_round": 0,
    "inject_output_b_error": 0,
    "output_b_error_segment": 0,
    "control_doorbell_error_ordinal": 0,
    "mutate_cancel_command_ordinal": 0,
    "mutate_cancel_command_status": 0,
    "cancel_output_prefix_bytes": 0,
    "control_cq_first": False,
    "early_command_cq": False,
    "control_doorbell_b_hold_ns": 0,
    "cq_read_delay_ns": 0,
    "metadata_read_delay_ns": 0,
}

NPU_CONTROL_WINDOW_BYTES = 0x1000

CQ_STATUS_CODES = {
    "SUCCESS": 0,
    "CANCELLED": 1,
    "NOT_FOUND": 2,
    "ALREADY_TERMINAL": 3,
    "PARAM_ERROR": 4,
    "STALE_GENERATION": 5,
}

EARLY_COMMAND_CQ_HOLD_NS = 50000


def define_gate4_options(parser):
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


def add_gate4_arguments(parser):
    parser.add_argument("--host-compute-tokens", type=int)
    parser.add_argument("--host-service-queue-depth", type=int)
    parser.add_argument("--host-aging-threshold-ns", type=int)
    parser.add_argument("--stop-after-completed-tasks", type=int)
    parser.add_argument("--stop-accepting-at-tick", type=int)
    parser.add_argument("--host-fault-site", default="")
    parser.add_argument("--host-fault-task", type=int, default=0)
    parser.add_argument("--host-fault-round", type=int, default=0)
    parser.add_argument("--inject-output-b-error", type=int, default=0)
    parser.add_argument("--inject-output-b-error-at", default="")
    parser.add_argument("--inject-control-doorbell-b-error", type=int, default=0)
    parser.add_argument("--mutate-cancel-command-status", default="")
    parser.add_argument("--cancel-output-prefix-bytes", type=int, default=0)
    parser.add_argument("--control-cq-first", action="store_true")
    parser.add_argument("--early-command-cq", action="store_true")
    parser.add_argument("--cq-read-delay-ns", type=int, default=0)
    parser.add_argument("--metadata-read-delay-ns", type=int, default=0)


def _split_pair(value, name):
    try:
        left, right = value.split(":", 1)
        return int(left), int(right)
    except ValueError as error:
        raise ValueError(f"{name} must be NUMBER:NUMBER") from error


def normalize_gate4_arguments(args):
    args.output_b_error_segment = 0
    args.mutate_cancel_command_ordinal = 0
    args.stop_accepting_enabled = args.stop_accepting_at_tick != 0
    if args.mutate_cancel_command_status == "":
        args.mutate_cancel_command_status = 0
    if args.inject_output_b_error_at:
        request, segment = _split_pair(
            args.inject_output_b_error_at, "--inject-output-b-error-at"
        )
        if segment < 0:
            raise ValueError("output B error segment must be non-negative")
        args.inject_output_b_error = request
        args.output_b_error_segment = segment
    if isinstance(args.mutate_cancel_command_status, str):
        ordinal, status = args.mutate_cancel_command_status.split(":", 1)
        ordinal = int(ordinal)
        if status not in CQ_STATUS_CODES:
            raise ValueError(
                f"unknown CQ status {status}; expected one of "
                f"{sorted(CQ_STATUS_CODES)}"
            )
        args.mutate_cancel_command_ordinal = ordinal
        args.mutate_cancel_command_status = CQ_STATUS_CODES[status]
    if args.early_command_cq:
        args.control_doorbell_b_hold_ns = EARLY_COMMAND_CQ_HOLD_NS
    else:
        args.control_doorbell_b_hold_ns = 0


def validate_gate4_arguments(args):
    if args.host_fault_site not in ("", "object_produce", "object_read"):
        raise ValueError("host fault site must be object_produce or object_read")
    if args.host_fault_site and (args.host_fault_task < 0 or args.host_fault_round < 0):
        raise ValueError("host fault task/round must be non-negative")
    if args.inject_output_b_error < 0:
        raise ValueError("output B error request id must be non-negative")
    if args.output_b_error_segment < 0:
        raise ValueError("output B error segment must be non-negative")
    if args.inject_control_doorbell_b_error < 0:
        raise ValueError(
            "control doorbell error ordinal must be non-negative"
        )
    if args.mutate_cancel_command_ordinal < 0:
        raise ValueError("cancel command mutation ordinal must be non-negative")
    if args.cancel_output_prefix_bytes < 0:
        raise ValueError("cancel output prefix bytes must be non-negative")
    if args.control_doorbell_b_hold_ns < 0:
        raise ValueError("control doorbell B hold must be non-negative")
    for name in (
        "cq_read_delay_ns",
        "metadata_read_delay_ns",
    ):
        if getattr(args, name) < 0:
            raise ValueError(f"{name} must be non-negative")
    for name in (
        "host_compute_tokens",
        "host_service_queue_depth",
        "host_aging_threshold_ns",
        "stop_after_completed_tasks",
        "stop_accepting_at_tick",
    ):
        value = getattr(args, name)
        if value is not None and value < 0:
            raise ValueError(f"{name} must be non-negative")


def _scenario(options, config_document=None):
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
    if config_document is None:
        target_ranges = [
            {"dst_node": 2, "start": 0x30000000, "end": 0x30001000},
            {"dst_node": 3, "start": 0x10000000, "end": 0x10100000},
            {"dst_node": 3, "start": 0x30100000, "end": 0x30101000},
            {"dst_node": 3, "start": 0x100100000, "end": 0x111000000},
        ]
    else:
        hardware = config_hardware(config_document)
        target_ranges = [
            {
                "dst_node": 2,
                "start": hardware["npu_control_base"],
                "end": hardware["npu_control_base"] + NPU_CONTROL_WINDOW_BYTES,
            },
            {
                "dst_node": 3,
                "start": hardware["agent_proxy_control_base"],
                "end": (
                    hardware["agent_proxy_control_base"]
                    + NPU_CONTROL_WINDOW_BYTES
                ),
            },
            {
                "dst_node": 3,
                "start": hardware["host_base"],
                "end": hardware["host_base"] + hardware["host_bytes"],
            },
        ]
    return {
        "schema_version": 1,
        "name": "gate4_plan_mode",
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
        "target_ranges": target_ranges,
        "quotas": [
            {"src_node": source, "src_port": 0, "dst_node": target, **quota}
            for source in (0, 1)
            for target in (2, 3)
        ],
        "channel_injection_delay_cycles": {"aw": 0, "w": 0, "ar": 0},
        "response_ejection_stall_until_cycle": {"b": 0, "r": 0},
        "consumer_stall_until_cycle": {"b": 0, "r": 0},
    }


def config_hardware(config_document: dict, accepted_queue_entries=None) -> dict:
    serving = config_document["serving"]
    address_map = serving["address_map"]
    driver = config_document["agent_axi_driver"]
    services = driver["synthetic_host_services"]
    local_io = services["local_io_model"]
    hardware = {
        "clock": driver["clock"],
        "sq_depth": serving["sq_entries"],
        "cq_depth": serving["cq_entries"],
        "host_base": driver["remote_host_memory"]["base"],
        "host_bytes": driver["remote_host_memory"]["bytes"],
        "sq_ring_base": address_map["sq_ring_base"],
        "cq_ring_base": address_map["cq_ring_base"],
        "msi_base": address_map["msi_address"],
        "npu_control_base": address_map["npu_control_page_base"],
        "agent_proxy_control_base": agent_proxy_control_base(address_map),
        "doorbell_axi_id": serving["control_axi_ids"]["sq_doorbell"],
        "ack_axi_id": serving["control_axi_ids"]["cq_head_ack"],
        "sq_head_axi_id": serving["control_axi_ids"]["sq_head_update"],
        "cq_tail_axi_id": serving["control_axi_ids"]["cq_tail_update"],
        "msi_axi_id": serving["msi_axi_id_base"],
        "msi_axi_id_count": serving["msi_axi_id_count"],
        "kv_session_record_entries": serving["kv_session_record_entries"],
        "host_available_fraction_q16": services[
            "host_available_fraction_q16"
        ],
        "host_compile_slots": services["compile_slots"],
        "host_test_slots": services["test_slots"],
        "host_log_parse_slots": services["log_parse_slots"],
        "host_weight_compile": services["scheduler_weights"]["compile"],
        "host_weight_test": services["scheduler_weights"]["test"],
        "host_weight_log_parse": services["scheduler_weights"]["log_parse"],
        "host_local_io_enabled": local_io["enabled"],
        "host_local_io_fixed_ns": local_io["fixed_latency_ns"],
        "host_local_io_bytes_per_ns": local_io["bytes_per_ns"],
        "agent_object_table_entries": services["agent_object_table_entries"],
        "cancel_join_entries": services["cancel_join_entries"],
    }
    if accepted_queue_entries is not None:
        hardware["accepted_queue_entries"] = accepted_queue_entries
    return hardware


def artifact_directory():
    directory = Path(
        os.environ.get("AI_MESH_ARTIFACT_DIR", m5.options.outdir)
    ).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _probe_driver_params():
    return {
        "sq_depth": 4,
        "cq_depth": 4,
        "host_base": 0x10000000,
        "npu_control_base": 0x30000000,
        "agent_proxy_control_base": 0x30100000,
        "doorbell_axi_id": 16,
        "ack_axi_id": 17,
    }


def _probe_frontend_params():
    return {
        "sq_depth": 4,
        "cq_depth": 4,
        "host_base": 0x10000000,
        "npu_control_base": 0x30000000,
        "agent_proxy_control_base": 0x30100000,
        "doorbell_axi_id": 16,
        "sq_head_axi_id": 18,
        "cq_tail_axi_id": 19,
        "msi_axi_id": 32,
        "msi_axi_id_count": 4,
    }


def _config_driver_params(hardware):
    return {
        "sq_depth": hardware["sq_depth"],
        "cq_depth": hardware["cq_depth"],
        "host_base": hardware["host_base"],
        "sq_ring_base": hardware["sq_ring_base"],
        "cq_ring_base": hardware["cq_ring_base"],
        "msi_base": hardware["msi_base"],
        "npu_control_base": hardware["npu_control_base"],
        "agent_proxy_control_base": hardware["agent_proxy_control_base"],
        "doorbell_axi_id": hardware["doorbell_axi_id"],
        "ack_axi_id": hardware["ack_axi_id"],
        "host_available_fraction_q16": hardware[
            "host_available_fraction_q16"
        ],
        "host_compile_slots": hardware["host_compile_slots"],
        "host_test_slots": hardware["host_test_slots"],
        "host_log_parse_slots": hardware["host_log_parse_slots"],
        "host_weight_compile": hardware["host_weight_compile"],
        "host_weight_test": hardware["host_weight_test"],
        "host_weight_log_parse": hardware["host_weight_log_parse"],
        "host_local_io_enabled": hardware["host_local_io_enabled"],
        "host_local_io_fixed_ns": hardware["host_local_io_fixed_ns"],
        "host_local_io_bytes_per_ns": hardware[
            "host_local_io_bytes_per_ns"
        ],
        "agent_object_table_entries": hardware[
            "agent_object_table_entries"
        ],
        "cancel_join_entries": hardware["cancel_join_entries"],
    }


def _config_frontend_params(hardware):
    params = {
        "sq_depth": hardware["sq_depth"],
        "cq_depth": hardware["cq_depth"],
        "host_base": hardware["host_base"],
        "sq_ring_base": hardware["sq_ring_base"],
        "cq_ring_base": hardware["cq_ring_base"],
        "msi_base": hardware["msi_base"],
        "npu_control_base": hardware["npu_control_base"],
        "agent_proxy_control_base": hardware["agent_proxy_control_base"],
        "doorbell_axi_id": hardware["doorbell_axi_id"],
        "sq_head_axi_id": hardware["sq_head_axi_id"],
        "cq_tail_axi_id": hardware["cq_tail_axi_id"],
        "msi_axi_id": hardware["msi_axi_id"],
        "msi_axi_id_count": hardware["msi_axi_id_count"],
        "kv_session_record_entries": hardware["kv_session_record_entries"],
    }
    if "accepted_queue_entries" in hardware:
        params["accepted_queue_entries"] = hardware["accepted_queue_entries"]
    return params


def assemble(
    args,
    plan_image: str,
    facts_name: str = "gate4_facts.tsv",
    config_document: dict | None = None,
    accepted_queue_entries: int | None = None,
):
    hardware = (
        config_hardware(config_document, accepted_queue_entries)
        if config_document is not None
        else None
    )
    artifact_dir = artifact_directory()
    scenario_path = artifact_dir / "gate4_scenario.json"
    scenario_path.write_text(
        json.dumps(
            _scenario(args, config_document), sort_keys=True,
            separators=(",", ":")
        ) + "\n",
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

    facts_path = artifact_dir / facts_name
    system.gate3_recorder = Gate3ObservationRecorder(
        output_path=str(facts_path)
    )
    recorder = system.gate3_recorder
    driver_layout = (
        _config_driver_params(hardware)
        if hardware is not None
        else _probe_driver_params()
    )
    frontend_layout = (
        _config_frontend_params(hardware)
        if hardware is not None
        else _probe_frontend_params()
    )
    driver = AgentAxiDriver(
        master=system.ruby.axi_initiator_adapter0,
        target=system.ruby.axi_target_adapter1,
        recorder=recorder,
        data_bus_bytes=args.axi_data_width_bits // 8,
        control_bytes=8,
        max_burst_beats=256,
        profile="",
        request_count=1,
        local_visibility_delay=0,
        request_id_capacity=args.request_id_capacity,
        drain_cycles=8,
        request_source="replay_plan",
        plan_image=plan_image,
        host_compute_tokens=args.host_compute_tokens,
        host_service_queue_depth=args.host_service_queue_depth,
        host_aging_threshold_ns=args.host_aging_threshold_ns,
        stop_after_completed_tasks=args.stop_after_completed_tasks,
        stop_accepting_enabled=args.stop_accepting_enabled,
        stop_accepting_at_tick=args.stop_accepting_at_tick,
        host_fault_site=args.host_fault_site,
        host_fault_task=args.host_fault_task,
        host_fault_round=args.host_fault_round,
        control_doorbell_error_ordinal=args.inject_control_doorbell_b_error,
        mutate_cancel_command_ordinal=args.mutate_cancel_command_ordinal,
        mutate_cancel_command_status=args.mutate_cancel_command_status,
        control_doorbell_b_hold_ns=args.control_doorbell_b_hold_ns,
        cq_read_delay_ns=args.cq_read_delay_ns,
        metadata_read_delay_ns=args.metadata_read_delay_ns,
        **driver_layout,
    )
    system.gate4_driver = driver
    frontend = NpuServingFrontend(
        master=system.ruby.axi_initiator_adapter1,
        control_target=system.ruby.axi_target_adapter0,
        recorder=recorder,
        driver=driver,
        data_bus_bytes=args.axi_data_width_bits // 8,
        control_bytes=8,
        max_burst_beats=256,
        profile="",
        request_count=1,
        sq_read_issue_delay=0,
        cq_entry_axi_id=20,
        executor="full_context_surrogate",
        plan_image=plan_image,
        output_b_error_request=args.inject_output_b_error,
        output_b_error_segment=args.output_b_error_segment,
        control_cq_first=args.control_cq_first,
        **frontend_layout,
    )
    if hardware is not None:
        agent_clock = SrcClockDomain(
            clock=hardware["clock"], voltage_domain=system.voltage_domain
        )
        driver.clk_domain = agent_clock
        frontend.clk_domain = agent_clock
    else:
        driver.clk_domain = system.clk_domain
        frontend.clk_domain = system.clk_domain
    system.gate4_frontend = frontend

    root = Root(full_system=False, system=system)
    root.system.mem_mode = "timing"
    m5.ticks.setGlobalFrequency("1ps")
    m5.instantiate()
    return artifact_dir, facts_path


def run_simulation(sim_tick_limit: int):
    event = m5.simulate(sim_tick_limit)
    print("Exiting @ tick", m5.curTick(), "because", event.getCause())
    return event
