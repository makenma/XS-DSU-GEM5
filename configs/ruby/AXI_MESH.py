import argparse
import json

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


def _load_endpoint_map(options):
    if not options.axi_scenario:
        return (
            [{"src_node": 0, "src_port": 0, "router_id": 0,
              "default_target": 1}],
            [{"dst_node": 1, "router_id": options.axi_mesh_routers - 1}],
        )

    try:
        with open(options.axi_scenario, encoding="utf-8") as scenario_file:
            scenario = json.load(scenario_file)
    except (OSError, json.JSONDecodeError) as exc:
        fatal("cannot load AXI scenario %s: %s", options.axi_scenario, exc)

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


def _validate_options(options, initiators, targets):
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
    if len(router_ids) != len(set(router_ids)):
        fatal("AXI endpoint router IDs must be unique")
    if any(rid < 0 or rid >= options.axi_mesh_routers for rid in router_ids):
        fatal("AXI endpoint router ID is out of range")
    if options.axi_raw_shim_probe and (
        len(initiators) != 1 or len(targets) != 1
    ):
        fatal("Commit 1 raw shim probe requires one initiator and one target")


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
    _positive_csv(options.axi_source_fifo_depths, 5,
                  "axi_source_fifo_depths")
    _positive_csv(options.axi_target_service_depths, 2,
                  "axi_target_service_depths")
    _positive_csv(options.axi_target_response_ready_depths, 2,
                  "axi_target_response_ready_depths")
    _positive_csv(options.axi_wire_header_bytes, 5,
                  "axi_wire_header_bytes")

    initiator_specs, target_specs = _load_endpoint_map(options)
    _validate_options(options, initiator_specs, target_specs)

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
    for version, (endpoint, controller) in enumerate(
        zip(initiator_specs, initiator_controllers)
    ):
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
            raw_probe=options.axi_raw_shim_probe,
            raw_probe_hold_cycles=options.axi_raw_probe_hold_cycles,
        )
        setattr(ruby_system, "axi_initiator_adapter%d" % version, adapter)
        initiator_adapters.append(adapter)

    for version, (endpoint, controller) in enumerate(
        zip(target_specs, target_controllers)
    ):
        adapter_args = dict(
            shim=controller,
            b_out=controller.bToNetwork,
            r_out=controller.rToNetwork,
            aw_local=controller.awLocal,
            w_local=controller.wLocal,
            ar_local=controller.arLocal,
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

    ruby_system.network.number_of_virtual_networks = 5
    controllers = initiator_controllers + target_controllers
    topology = create_topology(controllers, options)
    return ([], [], topology)
