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
    parser.add_argument("--axi-target-base-latencies", default="1,1")
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


def _nonnegative_csv(value, count, label):
    fields = value.split(",")
    if len(fields) != count or any(field == "" for field in fields):
        fatal("%s must contain exactly %d non-empty integers", label, count)
    try:
        parsed = [int(field, 10) for field in fields]
    except ValueError:
        fatal("%s contains a non-integer element", label)
    if any(item < 0 for item in parsed):
        fatal("%s entries must all be non-negative", label)
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


def _transaction_records(scenario, initiators, target_specs):
    records = scenario.get("transactions")
    generator = scenario.get("traffic_generator")
    if records is not None and generator is not None:
        fatal("AXI scenario cannot specify both transactions and traffic_generator")
    if records is not None:
        return records
    if not isinstance(generator, dict):
        fatal("AXI functional scenario requires transactions or traffic_generator")
    if generator.get("kind") != "response_progress_v1":
        fatal("unsupported AXI traffic_generator kind")
    if len(target_specs) != 1:
        fatal("response_progress_v1 requires exactly one target")
    try:
        stop_cycle = int(generator["stop_cycle"])
        id_count = int(generator.get("id_count", 8))
        write_base = _u64(generator.get("write_base", 0x10000),
                          "response-progress write_base")
        read_base = _u64(generator.get("read_base", 0x40000),
                         "response-progress read_base")
        extra_latency_modulus = int(
            generator.get("extra_latency_modulus", 4)
        )
    except (KeyError, TypeError, ValueError):
        fatal("invalid response_progress_v1 generator configuration")
    if stop_cycle != 2000:
        fatal("response_progress_v1 stop_cycle must be 2000")
    if id_count <= 0 or id_count > (1 << 16):
        fatal("response_progress_v1 id_count must be in [1,65536]")
    if extra_latency_modulus <= 0:
        fatal("response_progress_v1 extra_latency_modulus must be positive")

    dst_node = int(target_specs[0]["dst_node"])
    generated = []
    write_ordinal = 0
    read_ordinal = 0
    for cycle in range(stop_cycle + 1):
        kind = "write" if cycle % 2 == 0 else "read"
        ordinal = write_ordinal if kind == "write" else read_ordinal
        address_base = write_base if kind == "write" else read_base
        generated.append({
            "kind": kind,
            "source_index": cycle % len(initiators),
            "dst_node": dst_node,
            "axi_id": ordinal % id_count,
            "address": address_base + ordinal * 64,
            "beat_count": 1,
            "size": 6,
            "data_seed": (ordinal * 17 + cycle) & 0xFF,
            "strobe": "full",
            "arrival_cycle": cycle,
            "target_extra_latency_cycles": (
                ordinal % extra_latency_modulus
            ),
        })
        if kind == "write":
            write_ordinal += 1
        else:
            read_ordinal += 1
    return generated


def _transaction_specs(scenario, initiators, target_specs, data_bus_bytes):
    records = _transaction_records(scenario, initiators, target_specs)
    if not isinstance(records, list) or not records:
        fatal("AXI functional scenario requires non-empty transactions")
    target_index = {int(target["dst_node"]): index
                    for index, target in enumerate(target_specs)}
    specs = []
    plans_by_target = [[] for _ in target_specs]
    uid_counters = {}
    target_seq_counters = {}
    response_seq_counters = {}
    write_ordinal_counters = {}
    max_extra_latency = [0, 0]
    max_arrival_cycle = 0
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
            arrival_cycle = int(record.get("arrival_cycle", 0))
            extra_latency = int(
                record.get("target_extra_latency_cycles", 0)
            )
            fault = str(record.get("target_fault", "okay")).lower()
            burst = str(record.get("burst", "incr")).lower()
            lock = int(record.get("lock", 0))
            cache = int(record.get("cache", 0))
            prot = int(record.get("prot", 0))
            region = int(record.get("region", 0))
            qos = int(record.get("qos", 0))
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
        beat_bytes = 1 << size
        if burst == "incr":
            span_bytes = beat_count * beat_bytes
            if span_bytes > 0xFFFFFFFFFFFFFFFF - address:
                fatal("AXI INCR last address overflows uint64")
            last_byte_exclusive = address + span_bytes
            if (address >> 12) != ((last_byte_exclusive - 1) >> 12):
                fatal("AXI INCR burst crosses a 4 KiB boundary")
        if not 0 <= axi_id < (1 << 16) or not 0 <= data_seed <= 255:
            fatal("AXI transaction ID or data_seed is out of range")
        if strobe not in ("full", "alternating"):
            fatal("AXI transaction strobe mode is invalid")
        if arrival_cycle < 0 or extra_latency < 0:
            fatal("AXI transaction arrival/latency must be non-negative")
        if extra_latency > 0xFFFFFFFF:
            fatal("AXI target extra latency must fit uint32")
        if fault not in ("okay", "slverr"):
            fatal("AXI target_fault must be okay or slverr")
        if burst not in ("fixed", "incr", "wrap"):
            fatal("AXI transaction burst must be fixed, incr, or wrap")
        if not 0 <= lock <= 1 or not 0 <= cache <= 15 or \
                not 0 <= prot <= 7 or not 0 <= region <= 15 or \
                not 0 <= qos <= 15:
            fatal("AXI transaction sideband field is out of range")
        direction = 1 if kind == "read" else 0
        max_extra_latency[direction] = max(
            max_extra_latency[direction], extra_latency
        )
        max_arrival_cycle = max(max_arrival_cycle, arrival_cycle)
        counter_key = (source_index, direction)
        local_counter = uid_counters.get(counter_key, 0)
        if local_counter >= (1 << 39):
            fatal("AXI transaction UID local counter overflow")
        uid_counters[counter_key] = local_counter + 1
        source = initiators[source_index]
        expected_uid = (
            (int(source["src_node"]) << 48)
            | (int(source["src_port"]) << 40)
            | (direction << 39)
            | local_counter
        )
        expected_resp = str(record.get(
            "expected_response",
            "slverr" if fault == "slverr" else "okay"
        )).lower()
        if expected_resp not in ("okay", "slverr", "decerr"):
            fatal("AXI expected_response must be okay, slverr, or decerr")
        target_key = (
            int(source["src_node"]), int(source["src_port"]), axi_id,
            bool(direction), dst_node,
        )
        response_key = target_key[:-1]
        target_seq = target_seq_counters.get(target_key, 0)
        response_seq = response_seq_counters.get(response_key, 0)
        target_seq_counters[target_key] = target_seq + 1
        response_seq_counters[response_key] = response_seq + 1
        if direction:
            write_ordinal = "none"
        else:
            write_ordinal = write_ordinal_counters.get(source_index, 0)
            write_ordinal_counters[source_index] = write_ordinal + 1
        specs.append("|".join(str(value) for value in (
            kind, source_index, target_index[dst_node], axi_id,
            hex(address), beat_count, size, w_before_aw, data_seed, strobe,
            expected_uid, arrival_cycle, expected_resp, index,
            target_seq, response_seq, write_ordinal,
            burst, lock, cache, prot, region, qos
        )))
        plans_by_target[target_index[dst_node]].append(
            (expected_uid, extra_latency, fault)
        )
    return specs, plans_by_target, {
        "max_extra_latency": max_extra_latency,
        "max_arrival_cycle": max_arrival_cycle,
        "arrival_cycles": [int(record.get("arrival_cycle", 0))
                           for record in records],
    }


def _mesh_program_plans(scenario, target_specs):
    """Deterministic per-UID service plans for mesh-program traffic.

    The Dummy Core runtime owns transaction generation, so plans cannot be
    derived from a tester transaction list; the scenario states them
    directly per target node (uid -> extra service cycles / fault).
    """
    nodes = [int(spec["dst_node"]) for spec in target_specs]
    plans = [[] for _ in target_specs]
    for kind, fields, fault_default in (
        ("mesh_planned_extra_latency", ("uid", "cycles"), None),
        ("mesh_planned_faults", ("uid",), "slverr"),
    ):
        records = scenario.get(kind, [])
        if not isinstance(records, list):
            fatal("AXI scenario %s must be a list", kind)
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                fatal("AXI %s[%d] must be an object", kind, index)
            try:
                target = int(record["target"])
                uid = int(record[fields[0]])
                value = (
                    int(record[fields[1]])
                    if len(fields) > 1
                    else int(str(record.get("resp", fault_default)) == "slverr")
                )
            except (KeyError, TypeError, ValueError):
                fatal("invalid AXI %s[%d] entry", kind, index)
            if target not in nodes:
                fatal("AXI %s references unknown target %d", kind, target)
            if uid < 0 or (len(fields) > 1 and value < 0):
                fatal("AXI %s uid/cycles must be non-negative", kind)
            entry_fault = str(record.get("resp", fault_default)).lower()
            if len(fields) == 1 and entry_fault not in ("okay", "slverr"):
                fatal("AXI mesh_planned_faults resp must be okay or slverr")
            if len(fields) > 1:
                plans[nodes.index(target)].append((uid, value, "okay"))
            else:
                plans[nodes.index(target)].append((uid, 0, entry_fault))
    return plans


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

    # Fail malformed per-vnet vectors in Python before any SimObject is
    # instantiated, so invalid configuration is an ordinary exit-1 fatal.
    _positive_csv(
        options.garnet_buffers_per_vnet, 5, "garnet_buffers_per_vnet"
    )

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
    target_service_depths = _positive_csv(
        options.axi_target_service_depths, 2, "axi_target_service_depths"
    )
    target_base_latencies = _nonnegative_csv(
        options.axi_target_base_latencies, 2, "axi_target_base_latencies"
    )
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
    transaction_specs = []
    plans_by_target = [[] for _ in target_specs]
    transaction_metadata = {
        "max_extra_latency": [0, 0],
        "max_arrival_cycle": 0,
        "arrival_cycles": [],
    }
    driver_mode = str(scenario.get("driver_mode", "sequential"))
    if driver_mode not in ("sequential", "concurrent", "mesh_program"):
        fatal("AXI driver_mode must be sequential, concurrent, or mesh_program")
    if driver_mode == "mesh_program":
        plans_by_target = _mesh_program_plans(scenario, target_specs)
    if not options.axi_raw_shim_probe and driver_mode != "mesh_program":
        transaction_specs, plans_by_target, transaction_metadata = \
            _transaction_specs(
                scenario, initiator_specs, target_specs, data_bus_bytes
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
    response_stall = scenario.get("response_ejection_stall_until_cycle", {})
    if not isinstance(response_stall, dict):
        fatal("response_ejection_stall_until_cycle must be an object")
    try:
        response_ejection_stalls = [
            int(response_stall.get(channel, 0)) for channel in ("b", "r")
        ]
    except (TypeError, ValueError):
        fatal("AXI response ejection stall cycles must be integers")
    if any(cycle < 0 for cycle in response_ejection_stalls):
        fatal("AXI response ejection stall cycles must be non-negative")
    consumer_stall = scenario.get("consumer_stall_until_cycle", {})
    if not isinstance(consumer_stall, dict):
        fatal("consumer_stall_until_cycle must be an object")
    try:
        consumer_stalls = [
            int(consumer_stall.get(channel, 0)) for channel in ("b", "r")
        ]
    except (TypeError, ValueError):
        fatal("AXI consumer stall cycles must be integers")
    if any(cycle < 0 for cycle in consumer_stalls):
        fatal("AXI consumer stall cycles must be non-negative")
    runtime_fault = str(scenario.get("runtime_fault", ""))
    allowed_runtime_faults = {
        "", "beat_count_257", "early_wlast", "late_wlast",
        "missing_wlast", "duplicate_rbeat", "out_of_range_rbeat",
        "duplicate_uid", "unknown_response", "source_w_without_aw",
    }
    if runtime_fault not in allowed_runtime_faults:
        fatal("unsupported AXI runtime_fault mode")
    measurement = scenario.get("measurement_window_cycles")
    measurement_cycles = [0, 0]
    measurement_vnet = -2
    if measurement is not None:
        if not isinstance(measurement, dict):
            fatal("measurement_window_cycles must be an object")
        try:
            measurement_cycles = [
                int(measurement["start"]), int(measurement["end"])
            ]
            measurement_vnet = int(scenario["measurement_vnet"])
        except (KeyError, TypeError, ValueError):
            fatal("invalid AXI measurement window/vnet")
        if measurement_cycles[0] < 0 or \
                measurement_cycles[1] <= measurement_cycles[0]:
            fatal("AXI measurement window must have 0 <= start < end")
        if measurement_vnet < -1 or measurement_vnet >= 5:
            fatal("AXI measurement_vnet must be -1 or in [0,4]")
    try:
        expected_router_vnet = int(
            scenario.get("expected_router_vnet", -1)
        )
        expected_router_depth = int(
            scenario.get("expected_router_depth", 0)
        )
        progress_watchdog_cycles = int(
            scenario.get("progress_watchdog_cycles", 256)
        )
        issue_stop_cycle = int(scenario.get("issue_stop_cycle", 0))
    except (TypeError, ValueError):
        fatal("AXI scenario expectation cycles/depths must be integers")
    if expected_router_vnet < -1 or expected_router_vnet >= 5:
        fatal("expected_router_vnet must be -1 or in [0,4]")
    if expected_router_depth < 0 or progress_watchdog_cycles <= 0 or \
            issue_stop_cycle < 0:
        fatal("AXI scenario expectation values are out of range")
    if expected_router_vnet >= 0 and expected_router_depth == 0:
        configured_depths = _positive_csv(
            options.garnet_buffers_per_vnet, 5,
            "garnet_buffers_per_vnet"
        )
        expected_router_depth = configured_depths[expected_router_vnet]

    liveness_bound_components = [0, 0, 0]
    if str(scenario.get("name", "")) == "response_progress":
        proof = scenario.get("liveness_bound_cycles")
        if not isinstance(proof, dict):
            fatal("response_progress requires liveness_bound_cycles proof")
        try:
            liveness_bound_components = [int(proof[field]) for field in (
                "max_target_latency",
                "max_forced_stall",
                "packet_serialization_and_path_slack",
            )]
        except (KeyError, TypeError, ValueError):
            fatal("response_progress liveness proof fields must be integers")
        if any(value < 0 for value in liveness_bound_components):
            fatal("response_progress liveness proof must be non-negative")

        actual_target_latency = max(
            target_base_latencies[index] +
            transaction_metadata["max_extra_latency"][index]
            for index in range(2)
        )
        actual_forced_stall = max(
            response_ejection_stalls + consumer_stalls
        )
        link_bytes = options.link_width_bits // 8
        max_packet_bytes = max(
            header + (data_bus_bytes if channel in (1, 4) else 0)
            for channel, header in enumerate(wire_headers)
        )
        max_packet_flits = (max_packet_bytes + link_bytes - 1) // link_bytes
        columns = options.axi_mesh_routers // options.mesh_rows
        mesh_diameter = options.mesh_rows + columns - 2
        minimum_path_slack = (
            2 * max_packet_flits + 4 * (mesh_diameter + 1)
        )
        if liveness_bound_components[0] < actual_target_latency:
            fatal("response_progress max_target_latency proof is too small")
        if liveness_bound_components[1] < actual_forced_stall:
            fatal("response_progress max_forced_stall proof is too small")
        if liveness_bound_components[2] < minimum_path_slack:
            fatal("response_progress packet/path slack proof is too small")
        if sum(liveness_bound_components) >= progress_watchdog_cycles:
            fatal("response_progress liveness proof must be below watchdog")
        if transaction_metadata["max_arrival_cycle"] != issue_stop_cycle:
            fatal("response_progress last request must arrive at issue_stop_cycle")
        if set(transaction_metadata["arrival_cycles"]) != \
                set(range(issue_stop_cycle + 1)):
            fatal("response_progress requires request creation every flood cycle")

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
        endpoint_delay_config = endpoint.get(
            "channel_injection_delay_cycles", delay_config
        )
        if not isinstance(endpoint_delay_config, dict):
            fatal("initiator channel_injection_delay_cycles must be an object")
        try:
            endpoint_injection_delays = [
                int(endpoint_delay_config.get(channel, 0))
                for channel in ("aw", "w", "ar")
            ]
        except (TypeError, ValueError):
            fatal("initiator channel injection delays must be integers")
        if any(delay < 0 for delay in endpoint_injection_delays):
            fatal("initiator channel injection delays must be non-negative")
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
            injection_delays=endpoint_injection_delays,
            response_ejection_stall_until=response_ejection_stalls,
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
        target_plans = plans_by_target[version]
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
            service_depths=target_service_depths,
            base_latencies=target_base_latencies,
            planned_uids=[entry[0] for entry in target_plans],
            planned_extra_latency_cycles=[entry[1] for entry in target_plans],
            planned_fault_responses=[entry[2] for entry in target_plans],
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

    driver_mode = str(scenario.get("driver_mode", "sequential"))
    if driver_mode not in ("sequential", "concurrent", "mesh_program"):
        fatal("AXI driver_mode must be sequential, concurrent, or mesh_program")
    if driver_mode == "mesh_program":
        plans_by_target = _mesh_program_plans(scenario, target_specs)
    if not options.axi_raw_shim_probe and driver_mode != "mesh_program":
        result_json = options.axi_result_json or os.path.join(
            m5.options.outdir, "axi_result.json"
        )
        tester = AxiTraceTester(
            initiators=initiator_adapters,
            targets=target_adapters,
            network=ruby_system.network,
            target_nodes=ordered_target_nodes,
            transaction_specs=transaction_specs,
            case_name=str(scenario.get("name", "unnamed")),
            result_json=result_json,
            event_trace_jsonl=os.path.join(
                m5.options.outdir, "event_trace.jsonl"
            ),
            credit_ledger_json=os.path.join(
                m5.options.outdir, "credit_ledger.json"
            ),
            residual_state_json=os.path.join(
                m5.options.outdir, "residual_state.json"
            ),
            runtime_fault=runtime_fault,
            seed=options.axi_seed,
            wire_header_bytes=wire_headers,
            data_bus_bytes=data_bus_bytes,
            concurrent=(driver_mode == "concurrent"),
            consumer_stall_until=consumer_stalls,
            expected_router_vnet=expected_router_vnet,
            expected_router_depth=expected_router_depth,
            progress_watchdog_cycles=progress_watchdog_cycles,
            issue_stop_cycle=issue_stop_cycle,
            liveness_bound_components=liveness_bound_components,
            local_delivery_depths=local_depths,
            measurement_window_cycles=measurement_cycles,
            measurement_vnet=measurement_vnet,
        )
        ruby_system.axi_trace_tester = tester

    ruby_system.network.number_of_virtual_networks = 5
    controllers = initiator_controllers + target_controllers
    topology = create_topology(controllers, options)
    return ([], [], topology)
