import json
import math
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from dataclasses import asdict

from mesh_ir.acceptance import CHILD_ENV_BASE, canonical_digest
from mesh_ir.builder import load_arch
from mesh_ir.burst_splitter import split_segment

from .config import BufferMap, CHANNELS, Topology, Workload, WorkloadSpec, validated_yx_vnets
from .execution import ExecutionIdentity, verify_resume
from .metrics import measure_runtime, outstanding_window
from .search import active_channels
from .workload import traffic_oracle, validate_simulation_horizon


QUIESCENCE_FIELDS = {"ni_queued_flits", "ni_queued_messages", "router_buffered_flits",
                     "non_idle_input_vcs", "non_idle_output_vcs", "data_link_pending_flits",
                     "credit_link_pending_credits", "bridge_pending_items", "credit_deficit"}


def load_json(path):
    def reject_constant(value):
        raise ValueError(f"nonfinite JSON value {value}")
    return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=reject_constant)


def verified_measurement(directory, expected_identity=None):
    directory = Path(directory).resolve()
    cached = load_json(directory / "measurement.json")
    record = load_json(directory / "execution.json")
    if record.get("verification") != "pass" or record.get("build_changed") or record.get("source_changed"):
        return {**cached, "status": "failed", "correctness": "failed"}
    identity = ExecutionIdentity(**record["identity"])
    if expected_identity is not None and identity != expected_identity:
        raise ValueError("requested execution identity differs from recorded run")
    if not verify_resume(directory, record, identity):
        raise ValueError("execution artifacts are missing or their hashes changed")
    source = load_json(directory / "source_provenance.json")
    if canonical_digest(source["files"]) != source["digest"] or source["digest"] != identity.source_digest or source["head"] != record["source_head"]:
        raise ValueError("source provenance identity is inconsistent")
    environment = record["environment"]
    if set(environment) != set(CHILD_ENV_BASE) | {"PYTHONPATH", "M5_OVERRIDE_PY_SOURCE"} or any(
            environment[key] != value for key, value in CHILD_ENV_BASE.items()) or environment["M5_OVERRIDE_PY_SOURCE"] != "false":
        raise ValueError("child execution environment differs from frozen contract")
    case = load_json(directory / "case.json")
    plan = load_json(Path(case["program_dir"]) / "workload.json")
    oracle = load_json(Path(case["program_dir"]) / "oracle.json")
    if ExecutionIdentity.from_case(case, plan, oracle, source["digest"], identity.build_digest, environment) != identity:
        raise ValueError("case/profile/workload/map/environment identity differs from execution")
    if Path(cached["directory"]).resolve() != directory or cached["identity"] != identity.document():
        raise ValueError("measurement identity is detached from its execution artifacts")
    expected_axes = {"topology": case["topology"], "profile_id": case["profile"]["profile_id"],
                     "backend_id": case["profile"]["backend"]["backend_id"], "workload": plan["spec"]["workload"],
                     "distribution": plan["spec"]["distribution"], "burst_beats": case["burst_beats"],
                     "workload_digest": plan["workload_digest"], "source_digest": identity.source_digest,
                     "build_digest": identity.build_digest, "profile_digest": identity.profile_digest,
                     "comparison_scope": canonical_digest({key: value for key, value in plan["spec"].items() if key != "workload"}),
                     "n_read": case["n_read"], "n_write": case["n_write"]}
    if any(cached[key] != value for key, value in expected_axes.items()):
        raise ValueError("measurement comparison axes differ from frozen workload/configuration")
    return {**cached, **verify_case(directory), "host_seconds": record["host_seconds"], "recomputed_from_raw": True}


def memory_expected_bytes(plan):
    reads = {}
    writes = 0
    for tile in plan["tiles"]:
        if tile["direction"] == "read":
            reads[tile["core_id"], tile["slot"]] = tile["useful_bytes"]
        else:
            writes += tile["useful_bytes"]
    return sum(reads.values()) + writes


def verify_workload_plan(plan, arch):
    spec = WorkloadSpec(**{**plan["spec"], "active_cores": tuple(plan["spec"]["active_cores"])})
    topology = Topology(plan["topology"])
    directions = ("read", "write") if spec.workload == Workload.MIXED_1_1 else (
        "read" if spec.workload == Workload.LOAD_ONLY else "write",)
    pairs = spec.tiles_per_core // len(directions)
    expected = {(core, direction, pair) for core in spec.active_cores for direction in directions for pair in range(pairs)}
    tiles = {(row["core_id"], row["direction"], row["pair_index"]): row for row in plan["tiles"]}
    if len(tiles) != len(plan["tiles"]) or tiles.keys() != expected:
        raise ValueError("workload spec does not exactly cover core/direction/pair tiles")
    hbm = next(region for region in arch.regions if region.kind == "HBM")
    setup = {(row["core_id"], row["sram_offset"]): row for row in plan["setup"]}
    expected_setup = {(core, (directions.index("write") * spec.ring_slots + slot) * spec.tile_bytes)
                      for core in spec.active_cores for slot in range(spec.ring_slots)} if "write" in directions else set()
    if len(setup) != len(plan["setup"]) or setup.keys() != expected_setup:
        raise ValueError("STORE setup does not exactly initialize the declared SRAM ring")
    if any(row["useful_bytes"] != spec.tile_bytes or not 1 <= row["expected_byte"] <= 255 for row in plan["setup"]):
        raise ValueError("STORE setup size or byte pattern differs from plan")
    for (core, direction, pair), row in tiles.items():
        index = directions.index(direction)
        slot = pair % spec.ring_slots
        tile_index = pair * len(directions) + index
        offset = (index * spec.ring_slots + slot) * spec.tile_bytes
        if row["slot"] != slot or row["tile_index"] != tile_index or row["sram_offset"] != offset or row["useful_bytes"] != spec.tile_bytes:
            raise ValueError("tile index/slot/size disagrees with fixed ring workload")
        if spec.distribution == "single_target" or (spec.distribution == "hotspot" and pair % 2 == 0):
            target = spec.hotspot_target
        elif spec.distribution == "hotspot":
            alternatives = [target for target in range(len(topology.hbm_routers)) if target != spec.hotspot_target]
            target = alternatives[(core + pair // 2) % len(alternatives)]
        else:
            target = (core + tile_index) % len(topology.hbm_routers)
        expected_address = hbm.base + target * spec.hbm_port_bytes + core * 2 * spec.bytes_per_core + (
            spec.bytes_per_core if direction == "write" else 0) + pair * spec.tile_bytes
        if row["target_index"] != target or row["target_node"] != topology.target_node(target) or row["address"] != expected_address:
            raise ValueError("tile target/address disagrees with frozen distribution")
        if row["bursts"] != [asdict(burst) for burst in split_segment(expected_address, spec.tile_bytes, 32, arch.axi_max_burst_beats)]:
            raise ValueError("stored tile bursts disagree with independent address splitting")
        if not 1 <= row["expected_byte"] <= 255 or (direction == "write" and row["expected_byte"] != setup[core, offset]["expected_byte"]):
            raise ValueError("tile pattern does not match initialized store ring")
    records = plan["setup"] + plan["tiles"]
    if any(len({row[field] for row in records}) != len(records) for field in ("descriptor_id", "command_id", "completion_event")):
        raise ValueError("workload command/descriptor/event identities are not unique")


def verify_memory(plan, memory):
    if memory["schema"] != "ai_mesh_experiment_memory_checks_v1" or memory["drained"] is not True:
        raise ValueError("memory checks lack drained schema contract")
    expected = memory_expected_bytes(plan)
    if memory["requested_bytes"] != expected or memory["checked_bytes"] != expected:
        raise ValueError("memory check coverage disagrees with workload")
    if memory["mismatch_count"] != 0 or memory["unreadable_bytes"] != 0 or memory["first_mismatch"] is not None:
        raise ValueError("functional byte comparison failed")


@lru_cache(maxsize=1024)
def constant_byte_digest(value, size):
    if type(value) is not int or not 0 <= value <= 255 or type(size) is not int or size < 0:
        raise ValueError("invalid constant-byte digest dimensions")
    first, second = 0xCBF29CE484222325, 0x9E3779B97F4A7C15
    mask = (1 << 64) - 1
    for index in range(size):
        first = ((first ^ value) * 0x100000001B3) & mask
        second = ((second + ((first >> 31) ^ value)) * 0xBF58476D1CE4E5B9) & mask
    return f"{first:016x}-{second:016x}"


def verify_transport(plan, actual):
    expected = {row["descriptor_id"]: {**row, "direction": "fill"} for row in plan["setup"]}
    expected.update({row["descriptor_id"]: row for row in plan["tiles"]})
    observed = {row["descriptor_id"]: row for row in actual["transport"]}
    if len(observed) != len(actual["transport"]) or set(observed) != set(expected):
        raise ValueError("descriptor transport identities do not exactly cover workload")
    for descriptor, tile in expected.items():
        row = observed[descriptor]
        if any(row[key] for key in ("error_code", "read_discarded_bytes", "write_drained_uncommitted_bytes", "p2p_bytes", "p2p_bursts")):
            raise ValueError("descriptor transport error or unexpected traffic")
        for direction in ("read", "write", "fill"):
            if row[direction + "_bytes"] != (tile["useful_bytes"] if tile["direction"] == direction else 0):
                raise ValueError("descriptor byte accounting disagrees with workload")
        for direction in ("read", "write"):
            if row[direction + "_bursts"] != (len(tile["bursts"]) if tile["direction"] == direction else 0):
                raise ValueError("descriptor burst accounting disagrees with workload")
        if row["payload_digest"] != constant_byte_digest(tile["expected_byte"], tile["useful_bytes"]):
            raise ValueError(f"descriptor {descriptor} payload digest disagrees with independent byte oracle")


def verify_effective(raw, case):
    objects = {}
    pending = [raw]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if "type" in value and "path" in value:
                objects[value["path"]] = value
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    typed = defaultdict(list)
    for obj in objects.values():
        typed[obj["type"]].append(obj)
    topology = Topology(case["topology"])
    profile = case["profile"]
    arch = load_arch(case["arch_path"])
    counts = {"MeshDummyCore": 25, "AxiTensorDmaEngine": 25, "AxiGarnetBridge": 25,
              "AxiInitiatorAdapter": 25, "AxiTargetAdapter": len(topology.hbm_routers) + 1,
              "GarnetNetwork": 1, "GarnetRouter": 25,
              "GarnetNetworkInterface": topology.error_node + 1}
    for kind, expected in counts.items():
        if len(typed[kind]) != expected:
            raise ValueError(f"effective object count {kind} != {expected}")
    if raw["system"]["mem_mode"] != "timing":
        raise ValueError("simulation is not FULL_TIMING")
    network = typed["GarnetNetwork"][0]
    wanted_network = {"num_rows": 5, "number_of_virtual_networks": 5,
                      "dual_lane": profile["network"].get("dual_lane", False),
                      "yx_vnets": list(validated_yx_vnets(profile["network"].get("yx_vnets", ()))),
                      "routing_algorithm": 1, "ni_flit_size": profile["network"]["flit_bytes"],
                      "vcs_per_vnet": profile["network"]["vcs_per_vnet"],
                      "buffers_per_vnet": case["buffer_depths"],
                      "ni_buffers_per_vnet": profile["network"]["ni_depths"],
                      "router_input_vc_depths": case["router_input_vc_depths"], "enable_fault_model": False}
    if any(network[key] != value for key, value in wanted_network.items()):
        raise ValueError("effective network differs from frozen case")
    if sorted(core["core_id"] for core in typed["MeshDummyCore"]) != list(range(25)):
        raise ValueError("effective core IDs must be exactly 0..24")
    axi = profile["axi"]
    expected_by_type = {
        "MeshDummyCore": {"sram_bytes": arch.sram_bytes, "sram_banks": arch.sram_banks,
                          "sram_bank_queue_depth": arch.sram_bank_queue_depth,
                          "sram_read_ports": arch.sram_read_ports_per_bank,
                          "sram_write_ports": arch.sram_write_ports_per_bank,
                          "sram_read_bytes_per_cycle": arch.sram_read_bytes_per_cycle_per_bank,
                          "sram_write_bytes_per_cycle": arch.sram_write_bytes_per_cycle_per_bank,
                          "admit_window": arch.admit_window, "decode_width": arch.decode_width},
        "AxiTensorDmaEngine": {"data_bus_bytes": 32, "max_burst_beats": arch.axi_max_burst_beats,
                               "descriptor_queue_depth": arch.dma_descriptor_queue_depth,
                               "segment_queue_depth": arch.dma_segment_queue_depth,
                               "axi_id_count": 1 << arch.axi_id_bits},
        "AxiGarnetBridge": {"data_bus_bytes": 32, "aw_queue_depth": arch.dma_segment_queue_depth,
                             "ar_queue_depth": arch.dma_segment_queue_depth,
                             "axi_id_count": 1 << arch.axi_id_bits, "w_beats_per_cycle": axi["w_beats_per_cycle"]},
        "AxiInitiatorAdapter": {"max_outstanding_reads": case["n_read"], "max_outstanding_writes": case["n_write"],
                                 "data_bus_bytes": 32, "source_fifo_depths": axi["source_fifo_depths"],
                                 "b_rob_transactions": axi["b_rob_transactions"], "r_rob_beats": axi["r_rob_beats"],
                                 "pre_aw_bursts": axi["source_pre_aw_bursts"], "pre_aw_beats": axi["source_pre_aw_beats"],
                                 "wire_header_bytes": axi["wire_header_bytes"],
                                 "data_header_sideband": axi.get("data_header_sideband", False)},
        "AxiTargetAdapter": {"data_bus_bytes": 32, "service_depths": axi["target_service_depths"],
                              "wire_header_bytes": axi["wire_header_bytes"],
                              "data_header_sideband": axi.get("data_header_sideband", False),
                              "response_ready_depths": axi["target_response_ready_depths"],
                              "orphan_w_transactions": axi["orphan_w_transactions"], "orphan_w_beats": axi["orphan_w_beats"],
                              "synthetic_hbm_bytes_per_cycle": profile["backend"]["bytes_per_cycle"],
                              "synthetic_hbm_queue_depth": profile["backend"]["queue_depth"]},
        "GarnetRouter": {"latency": profile["network"]["router_latency"], "width": profile["network"]["flit_bytes"],
                         "dual_lane": profile["network"].get("dual_lane", False)},
        "GarnetNetworkInterface": {"vcs_per_vnet": profile["network"]["vcs_per_vnet"], "virt_nets": 5},
        "NetworkLink": {"link_latency": profile["network"]["link_latency"], "width": profile["network"]["flit_bytes"]}}
    for kind, expected_fields in expected_by_type.items():
        for obj in typed[kind]:
            if any(obj[key] != value for key, value in expected_fields.items()):
                raise ValueError(f"effective {kind} differs from frozen resources: {obj['path']}")
            clock = objects[obj["clk_domain"]]
            if clock["type"] != "SrcClockDomain" or clock["clock"] != [500]:
                raise ValueError(f"effective {kind} clock is not 2 GHz")
    for adapter in typed["AxiInitiatorAdapter"]:
        for dimension in ("read_contexts", "write_contexts", "read_beats", "write_beats"):
            if adapter["quota_" + dimension] != [axi["target_quota"][dimension]] * (len(topology.hbm_routers) + 1):
                raise ValueError("initiator quota differs from frozen profile")
    for adapter in typed["AxiTargetAdapter"]:
        enabled = adapter["dst_node"] != topology.error_node
        if adapter["synthetic_hbm_enabled"] != enabled:
            raise ValueError("memory target does not use the declared synthetic backend")
        if enabled and adapter["base_latencies"] != [profile["backend"]["base_latency_cycles"]] * 2:
            raise ValueError("synthetic target latency differs from frozen profile")
        for dimension in ("read_contexts", "write_contexts", "read_beats", "write_beats"):
            if adapter["quota_" + dimension] != [axi["target_quota"][dimension]] * 25:
                raise ValueError("target source quotas differ from frozen profile")
    for obj in typed["MessageBuffer"]:
        label = obj["name"]
        channel = next((name for name in CHANNELS if label.lower().startswith(name.lower())), None)
        if channel is None or "axi_" not in obj["path"]:
            continue
        vector = axi["local_delivery_depths"] if label.endswith("Local") else axi["message_buffer_depths"]
        if obj["buffer_size"] != vector[CHANNELS.index(channel)]:
            raise ValueError("effective Ruby/local MessageBuffer capacity differs from profile")
    return True


def weighted_occupancy(histogram):
    if not histogram or any(type(value) is not int or value < 0 for value in histogram):
        raise ValueError("occupancy histogram must contain nonnegative integer durations")
    total = sum(histogram)
    if not total:
        return {"average": None, "p95": None, "p99": None, "full_fraction": None}
    percentiles = {}
    for name, fraction in (("p95", .95), ("p99", .99)):
        cumulative = 0
        for occupancy, duration in enumerate(histogram):
            cumulative += duration
            if cumulative >= math.ceil(total * fraction):
                percentiles[name] = occupancy
                break
    return {"average": sum(index * duration for index, duration in enumerate(histogram)) / total,
            **percentiles, "full_fraction": histogram[-1] / total}


def counter_delta(before, after):
    if isinstance(before, dict) and isinstance(after, dict) and before.keys() == after.keys():
        return {key: counter_delta(before[key], after[key]) for key in before}
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        return [counter_delta(left, right) for left, right in zip(before, after)]
    if all(type(value) in (int, float) and math.isfinite(value) and value == int(value) for value in (before, after)) and 0 <= before <= after:
        return int(after - before)
    raise ValueError("cumulative counter shape changed or moved backwards")


def core_resource_statistics(initial, final):
    previous = {row["core_id"]: row for row in initial["cores"]}
    result = []
    for row in final["cores"]:
        old = previous[row["core_id"]]
        for snapshot in (old, row):
            progress = snapshot["axi_initiator_progress"]
            if set(progress) != {"b_packets_buffered", "r_packets_buffered", "same_id_responses_blocked",
                                 "b_transactions_retired", "r_transactions_retired", "write_quota_stalls", "read_quota_stalls",
                                 "ar_rejection_attempts", "aw_rejection_attempts"}:
                raise ValueError("source progress statistics do not cover the frozen contract")
            if any(set(progress[key]) != {"fifo_full", "outstanding_full", "response_reservation_full"}
                   for key in ("ar_rejection_attempts", "aw_rejection_attempts")):
                raise ValueError("source rejection statistics omit a required resource dimension")
            occupancy = snapshot["axi_initiator_occupancy"]
            if set(occupancy) != {"read_waiting_quota", "read_granted_waiting_forward", "read_forwarded_to_message_buffer",
                                  "write_waiting_quota", "write_granted_waiting_forward", "write_forwarded_to_message_buffer",
                                  "r_rob_reserved_beats", "r_rob_buffered_beats", "b_rob_reserved_transactions", "b_rob_buffered_transactions"}:
                raise ValueError("source occupancy statistics do not cover the frozen contract")
            for direction in ("read", "write"):
                if sum(occupancy[direction + suffix] for suffix in ("_waiting_quota", "_granted_waiting_forward", "_forwarded_to_message_buffer")) != snapshot["axi_" + direction + "_outstanding"]:
                    raise ValueError("source quota/forwarded lifecycle does not close to actual outstanding")
            if occupancy["r_rob_buffered_beats"] > occupancy["r_rob_reserved_beats"] or \
                    occupancy["b_rob_buffered_transactions"] > occupancy["b_rob_reserved_transactions"] or \
                    occupancy["b_rob_reserved_transactions"] != snapshot["axi_write_outstanding"]:
                raise ValueError("source ROB occupancy/reservation does not close")
        result.append({"core_id": row["core_id"],
                       "axi_initiator_progress_delta": counter_delta(old["axi_initiator_progress"], row["axi_initiator_progress"]),
                       "sram_reservation_rejection_attempts_delta": counter_delta(
                           old["sram_reservation_rejection_attempts"], row["sram_reservation_rejection_attempts"]),
                       "sram_service_cycles_delta": counter_delta(old["sram_service_cycles"], row["sram_service_cycles"]),
                       "sram_bank_conflicts_delta": counter_delta(old["sram_bank_conflicts"], row["sram_bank_conflicts"]),
                       "occupancy_begin": old["axi_initiator_occupancy"], "occupancy_end": row["axi_initiator_occupancy"],
                       "queue_full_run_high_water": {key: row["queue_high_water"][key] for key in (
                           "local_fifo", "message_buffer", "adapter_ingress")},
                       "message_buffer_stall_cycles_delta": counter_delta(
                           old["queue_high_water"]["message_buffer_stall_cycles"], row["queue_high_water"]["message_buffer_stall_cycles"])})
    return result


def verify_full_run_windows(cores, actual, n_read, n_write):
    intervals = defaultdict(list)
    for row in actual["burst_timings"]:
        intervals[row["core_id"], row["channel"]].append((row["ar_aw_tick"], row["response_tick"]))
    result = []
    for core in cores:
        row = {"core_id": core["core_id"]}
        for direction, channel, limit in (("read", "AR", n_read), ("write", "AW", n_write)):
            samples = intervals[core["core_id"], channel]
            end = max((stop for start, stop in samples), default=0) + 1
            window = outstanding_window(samples, 0, end)
            if window["peak"] > limit:
                raise ValueError("full-run AXI outstanding exceeds frozen limit")
            if direction == "read" and not window["peak"] <= core["peak_axi_read_outstanding"] <= limit:
                raise ValueError("recorded read outstanding peak disagrees with event window or limit")
            row[direction] = window
        row["recorded_read_peak"] = core["peak_axi_read_outstanding"]
        result.append(row)
    return result


def buffer_map(snapshot):
    entries = {}
    for row in snapshot["input_vcs"]:
        key = (row["router_id"], row["inport_id"], row["vnet"])
        if key in entries and entries[key] != row["depth"]:
            raise ValueError("VCs of an input/vnet disagree about depth")
        entries[key] = row["depth"]
    return BufferMap(tuple((*key, depth) for key, depth in entries.items()))


def verify_network(initial, final, oracle, case):
    for snapshot in (initial, final):
        if snapshot["schema"] != "ai_mesh_experiment_snapshot_v1":
            raise ValueError("unknown experiment snapshot schema")
    if initial["receiver_capacity_map"] != final["receiver_capacity_map"]:
        raise ValueError("network capacity changed during a run")
    if final["drained"] is not True or set(final["quiescence"]) != QUIESCENCE_FIELDS or any(final["quiescence"].values()):
        raise ValueError("network did not fully drain")
    vcs = case["profile"]["network"]["vcs_per_vnet"]
    capacities = {(row["link_id"], row["vc"]): row for row in final["receiver_capacity_map"]}
    if len(capacities) != len(final["receiver_capacity_map"]):
        raise ValueError("duplicate capacity link/VC")
    links = {row["link_id"]: row for row in final["links"]}
    old_links = {row["link_id"]: row for row in initial["links"]}
    if set(links) != set(old_links) or len(capacities) != len(links) * 5 * vcs:
        raise ValueError("network link/VC capacity shape mismatch")
    ledger = {(row["link_id"], row["vc"]): row for row in final["credit_ledger"]}
    old_ledger = {(row["link_id"], row["vc"]): row for row in initial["credit_ledger"]}
    if set(ledger) != set(capacities) or set(old_ledger) != set(capacities) or len(ledger) != len(final["credit_ledger"]):
        raise ValueError("credit ledger does not cover each actual link/VC")
    for key, capacity in capacities.items():
        row, old = ledger[key], old_ledger[key]
        vnet, vc = capacity["vnet"], capacity["vc"]
        if not 0 <= vnet < 5 or not vnet * vcs <= vc < (vnet + 1) * vcs:
            raise ValueError("invalid capacity VC/vnet relation")
        if capacity["depth"] <= 0 or capacity["initial_credit"] != capacity["depth"] or \
                row["initial"] != capacity["depth"] or row["depth"] != capacity["depth"]:
            raise ValueError("sender credit differs from receiver capacity")
        if row["current"] != row["initial"] or row["initial"] + row["returned"] != row["sent"] + row["current"]:
            raise ValueError("credit not conserved and restored")
        if row["sent"] - old["sent"] != links[key[0]]["vc_flits"][vc] - old_links[key[0]]["vc_flits"][vc]:
            raise ValueError("credit send count differs from directed-link traffic")
    observed_routes = defaultdict(set)
    route_flits = defaultdict(lambda: dict.fromkeys(CHANNELS, 0))
    per_link = []
    elapsed_cycles = final["network_cycle"] - initial["network_cycle"]
    if elapsed_cycles <= 0:
        raise ValueError("network measurement has no duration")
    for link_id, link in links.items():
        capacity = capacities[link_id, 0]
        src_kind = "endpoint" if capacity["sender_kind"] == 0 else "router"
        dst_kind = "endpoint" if capacity["receiver_kind"] == 0 else "router"
        route = f"{src_kind}:{capacity['sender_id']}->{dst_kind}:{capacity['receiver_id']}"
        internal = src_kind == dst_kind == "router"
        lane = int(capacity["sender_direction"].endswith("_ext")) if internal else None
        if lane in observed_routes[route]:
            raise ValueError("ambiguous directed-link lane identity")
        if internal and lane != int(capacity["receiver_direction"].endswith("_ext")):
            raise ValueError("directed link crosses lanes")
        observed_routes[route].add(lane)
        actual = {channel: sum(link["vc_flits"][vnet * vcs:(vnet + 1) * vcs]) -
                  sum(old_links[link_id]["vc_flits"][vnet * vcs:(vnet + 1) * vcs])
                  for vnet, channel in enumerate(CHANNELS)}
        if sum(actual.values()) != link["flits"] - old_links[link_id]["flits"]:
            raise ValueError(f"directed-link VC counters do not sum on {route}, lane {lane}")
        for channel, value in actual.items():
            route_flits[route][channel] += value
        per_link.append({"link_id": link_id, "route": route, "lane": lane,
                         "sender_port": capacity["sender_port"], "receiver_port": capacity["receiver_port"],
                         "sender_direction": capacity["sender_direction"],
                         "receiver_direction": capacity["receiver_direction"], "flits": sum(actual.values()),
                         "utilization": sum(actual.values()) / elapsed_cycles, **actual})
    for route, lanes in observed_routes.items():
        expected_lanes = ({0, 1} if case["profile"]["network"].get("dual_lane", False) else {0}) if None not in lanes else {None}
        if lanes != expected_lanes:
            raise ValueError(f"physical lanes do not match network profile on {route}")
        expected = oracle["directed_link_flits"].get(route, dict.fromkeys(CHANNELS, 0))
        if route_flits[route] != expected:
            raise ValueError(f"directed-link flit oracle mismatch on {route}: {route_flits[route]} != {expected}")
    if not set(oracle["directed_link_flits"]) <= observed_routes.keys():
        raise ValueError("workload route is absent from actual network")
    for unit in ("packets", "flits"):
        for suffix in ("injected", "received"):
            actual = [current - old for current, old in zip(final["network_counters"][unit + "_" + suffix],
                                                          initial["network_counters"][unit + "_" + suffix])]
            if actual != [oracle[unit][channel] for channel in CHANNELS]:
                raise ValueError(f"network {unit}_{suffix} disagrees with independent oracle")
    resolved = buffer_map(final)
    if case["router_input_vc_depths"]:
        wanted = BufferMap(tuple(tuple(map(int, text.split(":"))) for text in case["router_input_vc_depths"]))
    else:
        ports = sorted({row[:2] for row in resolved.entries})
        wanted = BufferMap.uniform(ports, case["buffer_depths"])
    if resolved != wanted:
        raise ValueError("requested router capacities did not become effective")
    ni_depths = case["profile"]["network"]["ni_depths"]
    for capacity in capacities.values():
        if capacity["receiver_kind"] == 0 and capacity["depth"] != ni_depths[capacity["vnet"]]:
            raise ValueError("NI capacity changed with router depth")
    return {"buffer_map": resolved, "per_link": per_link}


def input_statistics(initial, final, ticks_per_second):
    old = {(row["router_id"], row["inport_id"], row["vc"]): row for row in initial["input_vcs"]}
    current = {(row["router_id"], row["inport_id"], row["vc"]): row for row in final["input_vcs"]}
    if len(old) != len(initial["input_vcs"]) or len(current) != len(final["input_vcs"]) or old.keys() != current.keys():
        raise ValueError("input VC coverage changed or contains duplicates")
    capacities = {(row["receiver_id"], row["receiver_port"], row["vc"]): row
                  for row in final["receiver_capacity_map"] if row["receiver_kind"] == 1}
    ingress_keys = {(row["router_id"], row["ingress_port"], row["vc"]) for row in current.values()}
    if ingress_keys != capacities.keys() or len(capacities) != sum(row["receiver_kind"] == 1 for row in final["receiver_capacity_map"]) or \
            initial["receiver_capacity_map"] != final["receiver_capacity_map"]:
        raise ValueError("input VC statistics do not cover actual receiver capacities")
    old_credit = {(row["link_id"], row["vc"]): row for row in initial["credit_ledger"]}
    new_credit = {(row["link_id"], row["vc"]): row for row in final["credit_ledger"]}
    old_links = {row["link_id"]: row for row in initial["links"]}
    new_links = {row["link_id"]: row for row in final["links"]}
    elapsed_cycles = final["network_cycle"] - initial["network_cycle"]
    seconds = (final["tick"] - initial["tick"]) / ticks_per_second
    grouped = {}
    ingress_counts = defaultdict(lambda: [0, 0])
    ingress_lanes = set()
    for row in final["input_vcs"]:
        key = (row["router_id"], row["inport_id"], row["vc"])
        start = old[key]
        ingress = (row["router_id"], row["ingress_port"], row["vc"])
        identity = ingress + (row["lane"],)
        if identity in ingress_lanes or row["lane"] not in (0, 1):
            raise ValueError("input VC lane identity is duplicated or invalid")
        ingress_lanes.add(identity)
        for field in ("ingress_port", "lane", "direction", "vnet", "depth"):
            if start[field] != row[field]:
                raise ValueError("input VC identity or capacity changed")
        capacity = capacities[ingress]
        if row["depth"] != capacity["depth"] or row["vnet"] != capacity["vnet"]:
            raise ValueError("input VC depth/vnet differs from actual receiver")
        ingress_counts[ingress][0] += row["enqueued"] - start["enqueued"]
        ingress_counts[ingress][1] += row["dequeued"] - start["dequeued"]
        if row["occupancy"] - start["occupancy"] != row["enqueued"] - start["enqueued"] - row["dequeued"] + start["dequeued"]:
            raise ValueError("input occupancy/enqueue/dequeue do not close")
        histogram = [value - previous for value, previous in zip(row["time_histogram"], start["time_histogram"])]
        if len(row["time_histogram"]) != len(start["time_histogram"]) or len(histogram) != row["depth"] + 1 or sum(histogram) != elapsed_cycles:
            raise ValueError("input occupancy histogram does not cover ROI cycles")
        group_key = key[:2] + (row["vnet"],)
        group = grouped.setdefault(group_key, {"router_id": key[0], "inport_id": key[1],
                                               "direction": row["direction"], "vnet": row["vnet"],
                                               "lane": row["lane"], "ingress_port": row["ingress_port"],
                                               "channel": CHANNELS[row["vnet"]], "depth": row["depth"],
                                               "histogram": [0] * len(histogram), "vc_count": 0,
                                               "enqueued": 0, "dequeued": 0, "credit_stalls": 0,
                                               "no_vc_stalls": 0, "sa_lost": 0, "full_run_high_water": 0})
        group["vc_count"] += 1
        group["histogram"] = [left + right for left, right in zip(group["histogram"], histogram)]
        group["full_run_high_water"] = max(group["full_run_high_water"], row["high_water"])
        for counter in ("enqueued", "dequeued", "credit_stalls", "no_vc_stalls", "sa_lost"):
            difference = row[counter] - start[counter]
            if difference < 0:
                raise ValueError("input statistics moved backwards")
            group[counter] += difference
    if (initial.get("drained") is True or (initial.get("quiescence") and not any(initial["quiescence"].values()))) and final.get("drained") is True:
        for ingress, (enqueued, dequeued) in ingress_counts.items():
            capacity = capacities[ingress]
            link_key = capacity["link_id"], capacity["vc"]
            sent = new_credit[link_key]["sent"] - old_credit[link_key]["sent"]
            returned = new_credit[link_key]["returned"] - old_credit[link_key]["returned"]
            link_flits = new_links[link_key[0]]["vc_flits"][link_key[1]] - old_links[link_key[0]]["vc_flits"][link_key[1]]
            if enqueued != link_flits or sent != link_flits:
                raise ValueError("input enqueue/link flit/credit send do not close")
            if dequeued != returned:
                raise ValueError("input dequeue/credit return do not close")
    return [{**group, **weighted_occupancy(group["histogram"]),
             "window_time_supported_peak": max((index for index, duration in enumerate(group["histogram"]) if duration), default=0),
             "enqueue_per_second": group["enqueued"] / seconds,
             "dequeue_per_second": group["dequeued"] / seconds} for key, group in sorted(grouped.items())]


def verify_case(directory):
    directory = Path(directory)
    case = load_json(directory / "case.json")
    verify_effective(load_json(directory / "raw/config.json"), case)
    plan = load_json(Path(case["program_dir"]) / "workload.json")
    arch = load_arch(case["arch_path"])
    verify_workload_plan(plan, arch)
    stored_oracle = load_json(Path(case["program_dir"]) / "oracle.json")
    workload_digest = canonical_digest({key: value for key, value in plan.items() if key != "workload_digest"})
    if plan["workload_digest"] != workload_digest:
        raise ValueError("workload digest mismatch")
    oracle = traffic_oracle(plan, Topology(case["topology"]), data_bytes=arch.axi_data_bytes,
                            max_beats=arch.axi_max_burst_beats,
                            flit_bytes=case["profile"]["network"]["flit_bytes"],
                            header_bytes=case["profile"]["axi"]["wire_header_bytes"],
                            data_header_sideband=case["profile"]["axi"].get("data_header_sideband", False),
                            yx_vnets=case["profile"]["network"].get("yx_vnets", ()))
    if stored_oracle != {**oracle, "oracle_digest": canonical_digest(oracle)}:
        raise ValueError("frozen oracle differs from workload reconstruction")
    horizon_lower_bound = validate_simulation_horizon(oracle, case["profile"]["runtime"]["max_sim_ticks"],
                                                      arch.clock_hz, case["profile"]["measurement"]["ticks_per_second"])
    initial = load_json(directory / "network_initial.json")
    final = load_json(directory / "network_final.json")
    final2 = load_json(directory / "network_final2.json")
    checked = verify_network(initial, final, oracle, case)
    verify_network(initial, final2, oracle, case)
    if final2["network_cycle"] != final["network_cycle"] + 1:
        raise ValueError("quiescence was not verified on two consecutive network edges")
    verify_memory(plan, load_json(directory / "memory_checks.json"))
    actual = load_json(directory / "actual_result.json")
    verify_transport(plan, actual)
    full_run_windows = verify_full_run_windows(final["cores"], actual, case["n_read"], case["n_write"])
    if actual.get("watchdog_fired") or actual.get("error_drained"):
        raise ValueError("runtime watchdog or error occurred")
    issues = {int(command): tick for core in final["cores"] for command, tick in core["command_issue_ticks"].items()}
    if len(final["cores"]) != 25 or sorted(core["core_id"] for core in final["cores"]) != list(range(25)):
        raise ValueError("runtime core IDs must exactly equal 0..24")
    for core in final["cores"]:
        if core["errored"] or not all(core[key] for key in ("halted", "quiescent", "dma_idle", "bridge_idle")):
            raise ValueError("core did not reach successful drain")
        if any(core[key] for key in ("live_descriptors", "pending_reads", "pending_writes",
                                     "axi_read_outstanding", "axi_write_outstanding", "error_read_bursts",
                                     "error_write_bursts", "b_errors", "r_error_beats")):
            raise ValueError("core retains incomplete/error work")
        if any(core["axi_initiator_occupancy"].values()):
            raise ValueError("source quota/ROB occupancy did not drain")
        rows = oracle["core_hbm_matrix"].get(str(core["core_id"]), {})
        for direction in ("read", "write"):
            expected_bytes = sum(row[direction + "_bytes"] for row in rows.values())
            expected_bursts = sum(row[direction + "_bursts"] for row in rows.values())
            actual_bytes = core["read_committed_bytes" if direction == "read" else "write_completed_bytes"]
            if actual_bytes != expected_bytes or core["completed_" + direction + "_bursts"] != expected_bursts or \
                    core["submitted_" + direction + "_bursts"] != expected_bursts:
                raise ValueError("DMA accounting differs from workload oracle")
            if direction == "read" and (core["ar_accepted"] != expected_bursts or core["read_rlast_consumed"] != expected_bursts or
                                        core["r_beats_consumed"] != expected_bytes // 32):
                raise ValueError("AR/RLAST/beat accounting differs from workload oracle")
            if direction == "write" and (core["aw_accepted"] != expected_bursts or core["b_consumed"] != expected_bursts or
                                         core["w_accepted"] != expected_bytes // 32):
                raise ValueError("AW/W/B accounting differs from workload oracle")
            if direction == "write" and expected_bursts > 0 and core["peak_w_accepted_per_cycle"] != 1:
                raise ValueError("STORE source did not obey one-W-beat-per-cycle contract")
            progress = core["axi_initiator_progress"]
            retired_key = "r_transactions_retired" if direction == "read" else "b_transactions_retired"
            packet_key = "r_packets_buffered" if direction == "read" else "b_packets_buffered"
            expected_packets = expected_bytes // 32 if direction == "read" else expected_bursts
            if progress[retired_key] != expected_bursts or progress[packet_key] != expected_packets:
                raise ValueError("source response buffering/retirement differs from oracle")
    topology = Topology(case["topology"])
    if sorted(target["index"] for target in final["targets"]) != list(range(len(topology.hbm_routers) + 1)):
        raise ValueError("runtime HBM/error target identity set mismatch")
    for target in final["targets"]:
        if not target["idle"] or any(target[key] for key in ("write_contexts", "write_reserved_beats", "orphan_transactions",
                                                              "orphan_reserved_beats", "read_contexts", "read_reserved_beats",
                                                              "b_ready", "r_ready", "write_services", "read_services")):
            raise ValueError("memory target retains work")
        backend = target["synthetic_backend"]
        if backend is not None and (backend["queued"] or backend["ready"] or
                                    backend["submitted"] != backend["completed"] or backend["completed"] != backend["retired"]):
            raise ValueError("synthetic backend did not drain")
        for direction in ("read", "write"):
            expected_bytes = sum(row.get(str(target["index"]), {}).get(direction + "_bytes", 0)
                                 for row in oracle["core_hbm_matrix"].values())
            if target[direction + "_committed_bytes"] != expected_bytes:
                raise ValueError("target committed bytes differ from workload oracle")
            if backend is not None and backend[direction + "_bytes_serviced"] != expected_bytes:
                raise ValueError("backend serviced bytes differ from workload oracle")
    measurement_config = case["profile"]["measurement"]
    roi = tuple(case["roi"]) if "roi" in case else None
    measured = measure_runtime(plan, actual, roi=roi, ticks_per_second=measurement_config["ticks_per_second"],
                               command_issue_ticks=issues, drain_verified=True, network_verified=True,
                               full_timing_verified=True, tolerance=measurement_config["stability_tolerance"],
                               subwindows=measurement_config["subwindows"], mixed_read_fraction=measurement_config["mixed_read_fraction"],
                               mixed_fraction_tolerance=measurement_config["mixed_fraction_tolerance"])
    measured["full_run_outstanding"] = full_run_windows
    measured["simulation_horizon_lower_bound"] = horizon_lower_bound
    input_statistics(initial, final, measurement_config["ticks_per_second"])
    if roi is not None:
        before = load_json(directory / f"network_roi_{roi[0]}.json")
        after = load_json(directory / f"network_roi_{roi[1]}.json")
    else:
        before, after = initial, final
    measured["per_router_inport_vnet"] = input_statistics(before, after, measurement_config["ticks_per_second"])
    measured["input_statistics_window"] = "roi" if roi is not None else "full_run"
    measured["per_core_resources"] = core_resource_statistics(before, after)
    measured["resource_statistics_window"] = "roi" if roi is not None else "full_run"
    measured["channel_identifiability"] = {channel: "measured_active" if index in active_channels(plan["spec"]["workload"])
                                           else "not_identifiable_in_this_workload" for index, channel in enumerate(CHANNELS)}
    seconds = (after["tick"] - before["tick"]) / measurement_config["ticks_per_second"]
    network_cycles = after["network_cycle"] - before["network_cycle"]
    old_links = {row["link_id"]: row for row in before["links"]}
    new_links = {row["link_id"]: row for row in after["links"]}
    measured["per_link_full_run"] = checked["per_link"]
    measured["per_link"] = []
    vcs = case["profile"]["network"]["vcs_per_vnet"]
    for row in checked["per_link"]:
        old_link, new_link = old_links[row["link_id"]], new_links[row["link_id"]]
        flits = new_link["flits"] - old_link["flits"]
        counts = {channel: sum(new_link["vc_flits"][vnet * vcs:(vnet + 1) * vcs]) -
                  sum(old_link["vc_flits"][vnet * vcs:(vnet + 1) * vcs]) for vnet, channel in enumerate(CHANNELS)}
        measured["per_link"].append({**row, "flits": flits,
                                     "utilization": flits / network_cycles,
                                     "window": "roi" if roi is not None else "full_run", **counts})
    old_targets = {row["index"]: row for row in before["targets"]}
    measured["per_hbm_backend"] = []
    for target in after["targets"]:
        backend = target["synthetic_backend"]
        if backend is None:
            continue
        old = old_targets[target["index"]]
        counters = {field: backend[field] - old["synthetic_backend"][field] for field in (
            "read_bytes_serviced", "write_bytes_serviced", "busy_cycles", "queue_full_cycles", "request_slot_cycles",
            "submitted", "completed", "retired")}
        counters.update({field: target[field] - old[field] for field in (
            "read_committed_bytes", "write_committed_bytes", "reads_committed", "writes_committed",
            "same_id_ready_blocked", "orphan_or_quota_stall_cycles")})
        if any(value < 0 for value in counters.values()):
            raise ValueError("target/backend statistics moved backwards")
        measured["per_hbm_backend"].append({"target_index": target["index"],
                                           "window": "roi" if roi is not None else "full_run",
                                           "read_service_Bps": counters["read_bytes_serviced"] / seconds,
                                           "write_service_Bps": counters["write_bytes_serviced"] / seconds,
                                           "target_write_commit_Bps": counters["write_committed_bytes"] / seconds,
                                           "backend_full_run_high_water": backend["high_water"],
                                           "queue_full_run_high_water": {key: target["queue_high_water"][key] for key in (
                                               "local_fifo", "message_buffer", "adapter_ingress")},
                                           "message_buffer_stall_cycles_delta": counter_delta(old["queue_high_water"]["message_buffer_stall_cycles"],
                                                                                                target["queue_high_water"]["message_buffer_stall_cycles"]), **counters})
    measured["router_buffer_bytes"] = checked["buffer_map"].storage_bytes(
        case["profile"]["network"]["flit_bytes"], case["profile"]["network"]["vcs_per_vnet"])
    measured["router_buffer_map"] = {"entries": checked["buffer_map"].entries}
    measured["map_digest"] = checked["buffer_map"].digest()
    measured["correctness"] = "pass"
    return measured
