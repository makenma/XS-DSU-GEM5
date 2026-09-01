import argparse
import json
import os

import m5
from m5.defines import buildEnv
from m5.objects import *
from m5.util import fatal

from .Ruby import create_topology


def _strict_bool(value):
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def define_options(parser):
    parser.add_argument("--axi-mesh-routers", type=int, default=4)
    parser.add_argument("--axi-data-width-bits", type=int, default=512)
    parser.add_argument("--axi-id-width-bits", type=int, default=8)
    parser.add_argument("--axi-user-width-bits", type=int, default=0)
    parser.add_argument("--axi-max-outstanding-reads", type=int, default=64)
    parser.add_argument("--axi-max-outstanding-writes", type=int, default=32)
    parser.add_argument(
        "--axi-source-fifo-depths", default="16,64,16,32,128"
    )
    parser.add_argument(
        "--axi-message-buffer-depths", default="16,64,16,32,128"
    )
    parser.add_argument(
        "--axi-local-delivery-depths", default="16,64,16,32,128"
    )
    parser.add_argument("--axi-source-pre-aw-bursts", type=int, default=16)
    parser.add_argument("--axi-source-pre-aw-beats", type=int, default=256)
    parser.add_argument("--axi-target-write-contexts", type=int, default=64)
    parser.add_argument(
        "--axi-target-write-assembly-beats", type=int, default=4096
    )
    parser.add_argument("--axi-target-read-contexts", type=int, default=64)
    parser.add_argument(
        "--axi-target-read-response-beats", type=int, default=4096
    )
    parser.add_argument("--axi-target-service-depths", default="32,32")
    parser.add_argument(
        "--axi-target-response-ready-depths", default="16,128"
    )
    parser.add_argument("--axi-orphan-w-transactions", type=int, default=16)
    parser.add_argument("--axi-orphan-w-beats", type=int, default=256)
    parser.add_argument("--axi-b-rob-transactions", type=int, default=64)
    parser.add_argument("--axi-r-rob-beats", type=int, default=1024)
    parser.add_argument("--axi-wire-header-bytes", default="24,16,8,24,16")
    parser.add_argument(
        "--axi-strict-protocol", type=_strict_bool, default=True
    )
    parser.add_argument("--axi-seed", type=int, default=42)
    parser.add_argument("--axi-scenario", default="")
    parser.add_argument("--axi-max-sim-ticks", type=int, default=100000)
    parser.add_argument("--axi-result-json", default="")

    # Commit 1-only probe control.  This is intentionally separate from all
    # legal transaction scenarios introduced in Commit 4.
    parser.add_argument("--axi-raw-shim-probe", action="store_true")
    parser.add_argument("--axi-raw-probe-hold-cycles", type=int, default=8)


def _positive_csv(value, count, label):
    fields = value.split(",")
    if len(fields) != count or any(field == "" for field in fields):
        fatal("%s must contain exactly %d non-empty integers", label, count)
    try:
        parsed = [int(field, 10) for field in fields]
    except ValueError:
        fatal("%s contains a non-integer element", label)
    if any(item <= 0 for item in parsed):
        fatal("%s entries must all be positive", label)
    return parsed


def _message_buffer(depth):
    return MessageBuffer(
        ordered=False,
        buffer_size=depth,
        randomization="disabled",
        allow_zero_latency=False,
    )


def _load_scenario(options):
    if not options.axi_scenario:
        return {
            "schema_version": 1,
            "name": "endpoint_probe",
            "endpoint_to_router": {
                "initiators": [
                    {"src_node": 0, "src_port": 0, "router_id": 0,
                     "default_target": 1}
                ],
                "targets": [
                    {"dst_node": 1,
                     "router_id": options.axi_mesh_routers - 1}
                ],
            },
            "default_error_target": 1,
            "target_ranges": [
                {"dst_node": 1, "start": 0, "end": 0x10000}
            ],
            "quotas": [
                {"src_node": 0, "src_port": 0, "dst_node": 1,
                 "write_contexts": options.axi_target_write_contexts,
                 "write_beats": options.axi_target_write_assembly_beats,
                 "read_contexts": options.axi_target_read_contexts,
                 "read_beats": options.axi_target_read_response_beats}
            ],
            "transactions": [
                {"kind": "write", "source_index": 0, "dst_node": 1,
                 "axi_id": 1, "address": 0x100, "beat_count": 1,
                 "size": 6, "data_seed": 17, "strobe": "full"},
                {"kind": "read", "source_index": 0, "dst_node": 1,
                 "axi_id": 2, "address": 0x100, "beat_count": 1,
                 "size": 6, "data_seed": 0, "strobe": "full"},
            ],
        }

    try:
        with open(options.axi_scenario, encoding="utf-8") as scenario_file:
            scenario = json.load(scenario_file)
    except (OSError, json.JSONDecodeError) as exc:
        fatal("cannot load AXI scenario %s: %s", options.axi_scenario, exc)

    return scenario


def _endpoint_map(scenario):
    endpoint_map = scenario.get("endpoint_to_router")
    if not isinstance(endpoint_map, dict):
        fatal("AXI scenario requires endpoint_to_router object")
    initiators = endpoint_map.get("initiators")
    targets = endpoint_map.get("targets")
    if not isinstance(initiators, list) or not initiators:
        fatal("AXI scenario requires a non-empty initiator endpoint list")
    if not isinstance(targets, list) or not targets:
        fatal("AXI scenario requires a non-empty target endpoint list")
    return initiators, targets


def _u64(value, label):
    if isinstance(value, str):
        try:
            value = int(value, 0)
        except ValueError:
            fatal("%s must be an unsigned 64-bit integer", label)
    if not isinstance(value, int) or isinstance(value, bool):
        fatal("%s must be an unsigned 64-bit integer", label)
    if value < 0 or value > 0xFFFFFFFFFFFFFFFF:
        fatal("%s must fit uint64", label)
    return value


def _normal_ranges(scenario, target_nodes, required):
    records = scenario.get("target_ranges", [])
    if not isinstance(records, list) or (required and not records):
        fatal("AXI functional scenario requires non-empty target_ranges")
    ranges = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            fatal("AXI target_ranges[%d] must be an object", index)
        try:
            dst_node = int(record["dst_node"])
            start = _u64(record["start"], "AXI target range start")
            end = _u64(record["end"], "AXI target range end")
        except (KeyError, TypeError, ValueError):
            fatal("invalid AXI target_ranges[%d] entry", index)
        if dst_node not in target_nodes:
            fatal("AXI target range references unknown dst_node %d", dst_node)
        if start >= end:
            fatal("AXI target range must be non-empty")
        ranges.append((start, end, dst_node))
    ranges.sort()
    for previous, current in zip(ranges, ranges[1:]):
        if current[0] < previous[1]:
            fatal("AXI normal target ranges must not overlap")
    return ranges


def _quota_map(scenario, initiators, target_nodes):
    records = scenario.get("quotas")
    if not isinstance(records, list) or not records:
        fatal("AXI scenario requires a non-empty quotas list")
    source_keys = {(int(src["src_node"]), int(src["src_port"]))
                   for src in initiators}
    quotas = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            fatal("AXI quotas[%d] must be an object", index)
        try:
            key = (int(record["src_node"]), int(record["src_port"]),
                   int(record["dst_node"]))
            values = tuple(int(record[field]) for field in (
                "write_contexts", "write_beats",
                "read_contexts", "read_beats"
            ))
        except (KeyError, TypeError, ValueError):
            fatal("invalid AXI quotas[%d] entry", index)
        if key[:2] not in source_keys or key[2] not in target_nodes:
            fatal("AXI quota references an unknown source or target")
        if any(value <= 0 for value in values):
            fatal("AXI quota entries must all be positive")
        if key in quotas:
            fatal("duplicate AXI source-target quota")
        quotas[key] = values
    expected = {(src_node, src_port, dst_node)
                for src_node, src_port in source_keys
                for dst_node in target_nodes}
    if set(quotas) != expected:
        fatal("AXI quotas must cover every (source,target) pair exactly once")
    return quotas


def _transaction_specs(scenario, initiators, target_specs, data_bus_bytes):
    records = scenario.get("transactions")
    if not isinstance(records, list) or not records:
        fatal("AXI functional scenario requires non-empty transactions")
    target_index = {int(target["dst_node"]): index
                    for index, target in enumerate(target_specs)}
    specs = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            fatal("AXI transactions[%d] must be an object", index)
        try:
            kind = record["kind"]
            source_index = int(record.get("source_index", 0))
            dst_node = int(record["dst_node"])
            axi_id = int(record.get("axi_id", 0))
            address = _u64(record["address"], "AXI transaction address")
            beat_count = int(record["beat_count"])
            size = int(record["size"])
            w_before_aw = int(bool(record.get("w_before_aw", False)))
            data_seed = int(record.get("data_seed", 0))
            strobe = record.get("strobe", "full")
        except (KeyError, TypeError, ValueError):
            fatal("invalid AXI transactions[%d] entry", index)
        if kind not in ("write", "read"):
            fatal("AXI transaction kind must be write or read")
        if not 0 <= source_index < len(initiators):
            fatal("AXI transaction source_index is out of range")
        if dst_node not in target_index:
            fatal("AXI transaction dst_node is unknown")
        if not 1 <= beat_count <= 256:
            fatal("AXI transaction beat_count must be in [1,256]")
        if not 0 <= size < 64 or (1 << size) > data_bus_bytes:
            fatal("AXI transaction SIZE exceeds data bus")
        if not 0 <= axi_id < (1 << 16) or not 0 <= data_seed <= 255:
            fatal("AXI transaction ID or data_seed is out of range")
        if strobe not in ("full", "alternating"):
            fatal("AXI transaction strobe mode is invalid")
        specs.append("|".join(str(value) for value in (
            kind, source_index, target_index[dst_node], axi_id,
            hex(address), beat_count, size, w_before_aw, data_seed, strobe
        )))
    return specs


def _validate_options(options, initiators, targets, default_error_target,
                      quotas):
    if buildEnv["PROTOCOL"] != "AXI_MESH":
        fatal("This script requires the AXI_MESH protocol")
    if not options.axi_strict_protocol:
        fatal("AXI_MESH supports strict_protocol=true only")
    if options.network != "garnet":
        fatal("AXI_MESH requires --network=garnet")
    if options.topology != "AxiMeshDie":
        fatal("AXI_MESH requires --topology=AxiMeshDie")
    if options.routing_algorithm != 1:
        fatal("AXI_MESH requires deterministic XY routing")
    if options.axi_mesh_routers <= 0:
        fatal("axi_mesh_routers must be positive")
    if options.mesh_rows <= 0 or options.axi_mesh_routers % options.mesh_rows:
        fatal("mesh_rows must be positive and divide axi_mesh_routers")
    if options.link_width_bits <= 0 or options.link_width_bits % 8:
        fatal("link_width_bits must be a positive multiple of 8")
    if options.axi_data_width_bits not in (64, 128, 256, 512):
        fatal("axi_data_width_bits must be one of 64,128,256,512")
    if not 1 <= options.axi_id_width_bits <= 16:
        fatal("axi_id_width_bits must be in [1,16]")
    if options.axi_user_width_bits != 0:
        fatal("axi_user_width_bits must be zero")
    if options.cacheline_size < options.axi_data_width_bits // 8:
        fatal("Ruby block size must cover one AXI data bus word")
    scalar_depths = (
        options.axi_max_outstanding_reads,
        options.axi_max_outstanding_writes,
        options.axi_source_pre_aw_bursts,
        options.axi_source_pre_aw_beats,
        options.axi_target_write_contexts,
        options.axi_target_write_assembly_beats,
        options.axi_target_read_contexts,
        options.axi_target_read_response_beats,
        options.axi_orphan_w_transactions,
        options.axi_orphan_w_beats,
        options.axi_b_rob_transactions,
        options.axi_r_rob_beats,
    )
    if any(depth <= 0 for depth in scalar_depths):
        fatal("all AXI outstanding/FIFO/context/beat depths must be positive")
    if options.axi_b_rob_transactions < options.axi_max_outstanding_writes:
        fatal("axi_b_rob_transactions must cover max outstanding writes")

    source_keys = []
    target_nodes = []
    router_ids = []
    target_set = set()
    for endpoint in targets:
        try:
            dst_node = int(endpoint["dst_node"])
            router_id = int(endpoint["router_id"])
        except (KeyError, TypeError, ValueError):
            fatal("invalid AXI target endpoint entry")
        if dst_node < 0:
            fatal("AXI target dst_node must be non-negative")
        target_nodes.append(dst_node)
        target_set.add(dst_node)
        router_ids.append(router_id)
    for endpoint in initiators:
        try:
            src_node = int(endpoint["src_node"])
            src_port = int(endpoint["src_port"])
            router_id = int(endpoint["router_id"])
            default_target = int(endpoint["default_target"])
        except (KeyError, TypeError, ValueError):
            fatal("invalid AXI initiator endpoint entry")
        if not 0 <= src_node < 65536 or not 0 <= src_port < 256:
            fatal("AXI source endpoint exceeds UID field width")
        if default_target not in target_set:
            fatal("AXI initiator default_target does not exist")
        source_keys.append((src_node, src_port))
        router_ids.append(router_id)

    if len(source_keys) != len(set(source_keys)):
        fatal("duplicate AXI (src_node,src_port) endpoint")
    if len(target_nodes) != len(set(target_nodes)):
        fatal("duplicate AXI dst_node endpoint")
    if default_error_target not in target_set:
        fatal("AXI default_error_target does not exist")
    if len(router_ids) != len(set(router_ids)):
        fatal("AXI endpoint router IDs must be unique")
    if any(rid < 0 or rid >= options.axi_mesh_routers for rid in router_ids):
        fatal("AXI endpoint router ID is out of range")
    if options.axi_raw_shim_probe and (
        len(initiators) != 1 or len(targets) != 1
    ):
        fatal("Commit 1 raw shim probe requires one initiator and one target")

    for dst_node in target_nodes:
        matching = [values for key, values in quotas.items()
                    if key[2] == dst_node]
        sums = tuple(sum(values[index] for values in matching)
                     for index in range(4))
        limits = (
            options.axi_target_write_contexts,
            options.axi_target_write_assembly_beats,
            options.axi_target_read_contexts,
            options.axi_target_read_response_beats,
        )
        if any(total > limit for total, limit in zip(sums, limits)):
            fatal("AXI quota sum exceeds target %d capacity", dst_node)


def create_system(
    options, full_system, system, dma_ports, bootmem, ruby_system, cpus
):
    if full_system or dma_ports or cpus:
        fatal("AXI_MESH requires full_system=false, dma_ports=[], cpus=[]")

    network_depths = _positive_csv(
        options.axi_message_buffer_depths, 5,
        "axi_message_buffer_depths"
    )
    local_depths = _positive_csv(
        options.axi_local_delivery_depths, 5,
        "axi_local_delivery_depths"
    )
    source_depths = _positive_csv(
        options.axi_source_fifo_depths, 5, "axi_source_fifo_depths"
    )
    _positive_csv(options.axi_target_service_depths, 2,
                  "axi_target_service_depths")
    target_response_depths = _positive_csv(
        options.axi_target_response_ready_depths, 2,
        "axi_target_response_ready_depths"
    )
    wire_headers = _positive_csv(
        options.axi_wire_header_bytes, 5, "axi_wire_header_bytes"
    )
    data_bus_bytes = options.axi_data_width_bits // 8
    for channel, header_bytes in enumerate(wire_headers):
        wire_bytes = header_bytes
        if channel in (1, 4):
            wire_bytes += data_bus_bytes
        if wire_bytes > 0x7FFFFFFF:
            fatal("AXI wire bytes for channel index %d must fit positive int",
                  channel)

    scenario = _load_scenario(options)
    initiator_specs, target_specs = _endpoint_map(scenario)
    target_nodes = {int(target["dst_node"]) for target in target_specs}
    try:
        default_error_target = int(scenario["default_error_target"])
    except (KeyError, TypeError, ValueError):
        fatal("AXI scenario requires integer default_error_target")
    normal_ranges = _normal_ranges(
        scenario, target_nodes, not options.axi_raw_shim_probe
    )
    quotas = _quota_map(scenario, initiator_specs, target_nodes)
    _validate_options(
        options, initiator_specs, target_specs, default_error_target, quotas
    )
    delay_config = scenario.get("channel_injection_delay_cycles", {})
    if not isinstance(delay_config, dict):
        fatal("channel_injection_delay_cycles must be an object")
    try:
        injection_delays = [int(delay_config.get(channel, 0))
                            for channel in ("aw", "w", "ar")]
    except (TypeError, ValueError):
        fatal("AXI channel injection delays must be integers")
    if any(delay < 0 for delay in injection_delays):
        fatal("AXI channel injection delays must be non-negative")

    initiator_controllers = []
    target_controllers = []

    for version, endpoint in enumerate(initiator_specs):
        controller = AxiInitiator_Controller(
            version=version,
            router_id=int(endpoint["router_id"]),
            ruby_system=ruby_system,
        )
        controller.awToNetwork = _message_buffer(network_depths[0])
        controller.wToNetwork = _message_buffer(network_depths[1])
        controller.bFromNetwork = _message_buffer(network_depths[2])
        controller.arToNetwork = _message_buffer(network_depths[3])
        controller.rFromNetwork = _message_buffer(network_depths[4])
        controller.bLocal = _message_buffer(local_depths[2])
        controller.rLocal = _message_buffer(local_depths[4])
        setattr(ruby_system, "axi_initiator_controller%d" % version,
                controller)
        initiator_controllers.append(controller)

    for version, endpoint in enumerate(target_specs):
        controller = AxiTarget_Controller(
            version=version,
            router_id=int(endpoint["router_id"]),
            ruby_system=ruby_system,
        )
        controller.awFromNetwork = _message_buffer(network_depths[0])
        controller.wFromNetwork = _message_buffer(network_depths[1])
        controller.bToNetwork = _message_buffer(network_depths[2])
        controller.arFromNetwork = _message_buffer(network_depths[3])
        controller.rToNetwork = _message_buffer(network_depths[4])
        controller.awLocal = _message_buffer(local_depths[0])
        controller.wLocal = _message_buffer(local_depths[1])
        controller.arLocal = _message_buffer(local_depths[3])
        setattr(ruby_system, "axi_target_controller%d" % version,
                controller)
        target_controllers.append(controller)

    targets_by_node = {
        int(spec["dst_node"]): controller
        for spec, controller in zip(target_specs, target_controllers)
    }

    initiator_adapters = []
    ordered_target_nodes = [int(spec["dst_node"]) for spec in target_specs]
    for version, (endpoint, controller) in enumerate(
        zip(initiator_specs, initiator_controllers)
    ):
        source_key = (int(endpoint["src_node"]), int(endpoint["src_port"]))
        source_quotas = [quotas[source_key + (target,)]
                         for target in ordered_target_nodes]
        adapter = AxiInitiatorAdapter(
            shim=controller,
            peer=targets_by_node[int(endpoint["default_target"])],
            aw_out=controller.awToNetwork,
            w_out=controller.wToNetwork,
            ar_out=controller.arToNetwork,
            b_local=controller.bLocal,
            r_local=controller.rLocal,
            src_node=int(endpoint["src_node"]),
            src_port=int(endpoint["src_port"]),
            dst_node=int(endpoint["default_target"]),
            targets=target_controllers,
            target_nodes=ordered_target_nodes,
            range_starts=[entry[0] for entry in normal_ranges],
            range_ends=[entry[1] for entry in normal_ranges],
            range_targets=[entry[2] for entry in normal_ranges],
            default_error_target=default_error_target,
            quota_target_nodes=ordered_target_nodes,
            quota_write_contexts=[entry[0] for entry in source_quotas],
            quota_write_beats=[entry[1] for entry in source_quotas],
            quota_read_contexts=[entry[2] for entry in source_quotas],
            quota_read_beats=[entry[3] for entry in source_quotas],
            id_width=options.axi_id_width_bits,
            max_outstanding_reads=options.axi_max_outstanding_reads,
            max_outstanding_writes=options.axi_max_outstanding_writes,
            source_fifo_depths=source_depths,
            pre_aw_bursts=options.axi_source_pre_aw_bursts,
            pre_aw_beats=options.axi_source_pre_aw_beats,
            b_rob_transactions=options.axi_b_rob_transactions,
            r_rob_beats=options.axi_r_rob_beats,
            injection_delays=injection_delays,
            wire_header_bytes=wire_headers,
            data_bus_bytes=data_bus_bytes,
            raw_probe=options.axi_raw_shim_probe,
            raw_probe_hold_cycles=options.axi_raw_probe_hold_cycles,
        )
        setattr(ruby_system, "axi_initiator_adapter%d" % version, adapter)
        initiator_adapters.append(adapter)

    target_adapters = []
    for version, (endpoint, controller) in enumerate(
        zip(target_specs, target_controllers)
    ):
        target_node = int(endpoint["dst_node"])
        target_quota_keys = [
            (int(source["src_node"]), int(source["src_port"]), target_node)
            for source in initiator_specs
        ]
        target_quotas = [quotas[key] for key in target_quota_keys]
        target_ranges = [entry for entry in normal_ranges
                         if entry[2] == target_node]
        adapter_args = dict(
            shim=controller,
            b_out=controller.bToNetwork,
            r_out=controller.rToNetwork,
            aw_local=controller.awLocal,
            w_local=controller.wLocal,
            ar_local=controller.arLocal,
            dst_node=target_node,
            source_nodes=[key[0] for key in target_quota_keys],
            source_ports=[key[1] for key in target_quota_keys],
            quota_write_contexts=[entry[0] for entry in target_quotas],
            quota_write_beats=[entry[1] for entry in target_quotas],
            quota_read_contexts=[entry[2] for entry in target_quotas],
            quota_read_beats=[entry[3] for entry in target_quotas],
            memory_range_starts=[entry[0] for entry in target_ranges],
            memory_range_ends=[entry[1] for entry in target_ranges],
            target_write_contexts=options.axi_target_write_contexts,
            target_write_assembly_beats=(
                options.axi_target_write_assembly_beats
            ),
            target_read_contexts=options.axi_target_read_contexts,
            target_read_response_beats=(
                options.axi_target_read_response_beats
            ),
            orphan_w_transactions=options.axi_orphan_w_transactions,
            orphan_w_beats=options.axi_orphan_w_beats,
            response_ready_depths=target_response_depths,
            ingress_depths=[local_depths[0], local_depths[1],
                            local_depths[3]],
            wire_header_bytes=wire_headers,
            data_bus_bytes=data_bus_bytes,
            raw_probe=options.axi_raw_shim_probe,
            raw_probe_hold_cycles=options.axi_raw_probe_hold_cycles,
        )
        if options.axi_raw_shim_probe:
            initiator = initiator_specs[0]
            adapter_args.update(
                peer=initiator_controllers[0],
                probe_observer=initiator_adapters[0],
                src_node=int(initiator["src_node"]),
                src_port=int(initiator["src_port"]),
                dst_node=int(endpoint["dst_node"]),
            )
        adapter = AxiTargetAdapter(**adapter_args)
        setattr(ruby_system, "axi_target_adapter%d" % version, adapter)
        target_adapters.append(adapter)

    if not options.axi_raw_shim_probe:
        transaction_specs = _transaction_specs(
            scenario, initiator_specs, target_specs, data_bus_bytes
        )
        result_json = options.axi_result_json or os.path.join(
            m5.options.outdir, "axi_result.json"
        )
        tester = AxiTraceTester(
            initiators=initiator_adapters,
            targets=target_adapters,
            target_nodes=ordered_target_nodes,
            transaction_specs=transaction_specs,
            case_name=str(scenario.get("name", "unnamed")),
            result_json=result_json,
            data_bus_bytes=data_bus_bytes,
        )
        ruby_system.axi_trace_tester = tester

    ruby_system.network.number_of_virtual_networks = 5
    controllers = initiator_controllers + target_controllers
    topology = create_topology(controllers, options)
    return ([], [], topology)
