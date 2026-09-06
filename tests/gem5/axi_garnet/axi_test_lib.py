"""Deterministic AXI-over-Garnet test plans and schema helpers."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from functools import lru_cache
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CONSTANTS_PATH = Path(__file__).with_name("schema_constants.json")

QUICK_CASES = (
    "endpoint_probe",
    "single_write",
    "burst_read",
    "partial_wstrb",
    "w_before_aw_at_target",
    "same_id_order",
    "cross_id_reorder",
    "buffer_depth_credit_d1",
    "buffer_depth_credit_d2",
    "buffer_depth_credit_d8",
    "target_quota_no_hol",
    "ejection_backpressure",
    "response_progress",
    "rand_quick_s1",
    "rand_quick_s7",
    "rand_quick_s42",
    "determinism_a",
    "determinism_b",
    "determinism_replay",
    "legacy_vnet0",
    "legacy_vnet1",
    "legacy_vnet2",
    "n1_static_zero_beat_count",
    "n1_static_257_beat_count",
    "n1_runtime_beat_count",
    "n2_size",
    "n3a_unaligned_decerr",
    "n3b_4k_crossing",
    "n4_early_wlast",
    "n4_late_wlast",
    "n4_missing_wlast",
    "n5_duplicate_rbeat",
    "n5_out_of_range_rbeat",
    "n6_duplicate_uid",
    "n6_unknown_response",
    "n7_strict_false",
    "n8_unmapped_decerr",
    "n9_lock_decerr",
    "n10_source_w_without_aw",
    "n11_fifo_full",
    "n11_rob_full",
    "n11_orphan_full",
    "n12_vector_length",
    "n12_zero_depth",
)

NEGATIVE_CASES = tuple(name for name in QUICK_CASES if name.startswith("n"))
RANDOM_FULL_SEEDS = (1, 7, 42, 20260831, 314159, 271828, 65537, 99, 1234, 9001)
PROFILE_NAMES = ("11111", "24224", "48448", "8168816")
VC_COUNTS = (1, 2, 4)
MATRIX_TRAFFIC = ("aw", "w", "b", "ar", "r", "all")


UNIT_BINARIES = {
    "mem/axi/axi_validation.test.opt": {
        "AxiBurstValidationTest": (
            "AcceptsIncrLengths", "AcceptsDataWidths",
            "MapsNarrowNonZeroLanes", "RejectsOutOfLaneStrobe",
            "RoutesUnalignedToDecerr", "Rejects4KiBCrossing",
            "RoutesUnsupportedFeaturesToDecerr",
            "RejectsArithmeticOverflow",
        )
    },
    "mem/axi/axi_packetization.test.opt": {
        "AxiDynamicWireBytesTest": (
            "CountsDefaultFiveChannels", "FallsBackForLegacyMessage",
            "IgnoresSemanticBytesForFlits", "RejectsInvalidWireSize",
        )
    },
    "mem/axi/axi_write_pairing.test.opt": {
        "AxiWritePairingTest": (
            "PairsAwFirst", "PairsWFirst", "BuffersMultiplePreAwBursts",
            "PairsNthWithNth", "BlocksUnboundWInjection",
            "StreamsPartialWAfterAw", "KeepsAwReadyWhenWFull",
        ),
        "AxiTargetOrphanWTest": (
            "MergesWBeforeAw", "BackpressuresNewOrphanAtLimit",
            "AllowsAwPastBlockedW", "ConvertsSharedSlotInPlace",
            "RejectsQuotaOversubscription", "RejectsDuplicatePacket",
        ),
        "AxiLastAndBeatTest": (
            "HandlesSingleAnd256BeatLast", "RejectsEarlyWlast",
            "RejectsMissingWlast", "RejectsExtraWBeat",
            "RejectsDuplicateBeat", "ReassemblesRByIndex",
        ),
    },
    "mem/axi/axi_ordering.test.opt": {
        "AxiOrderingTest": (
            "AllocatesSequencesAtAcceptance", "SameIdReadWaitsOlder",
            "DifferentIdReadCanInvert", "SameIdWriteCommitsInOrder",
            "SameIdBRetiresInOrder", "DifferentIdWriteCanInvert",
            "ReadWriteDomainsIndependent", "SameIdCrossTargetRetiresGlobally",
            "SameTickWriteUsesConfiguredTieBreak",
            "DifferentIdDelayedBDoesNotBlockReadyResponse",
        )
    },
    "mem/axi/axi_flow_control.test.opt": {
        "AxiBackpressureTest": (
            "DepthOneChannelsBackpressure", "PausesAndResumesBAndR",
            "QueueFullReturnsNoError", "RetriesOnlyOnLaterEdge",
        )
    },
    "mem/axi/axi_error_response.test.opt": {
        "AxiErrorResponseTest": (
            "DecodeMissReadFullDecerr", "DecodeMissWriteDrainsThenDecerr",
            "RangeTailRoutesWholeBurstToError", "TargetFaultReturnsSlverr",
            "ErrorWriteCommitsNoBytes", "ErrorReadReturnsZeroData",
            "NeverProducesExOkay",
        )
    },
    "mem/axi/axi_protocol_checker.test.opt": {
        "AxiProtocolCheckerTest": (
            "AcceptsValidTrace", "RejectsDuplicateOrMissingBeat",
            "RejectsBadLast", "RejectsBadResponseSequence", "RejectsBadData",
        )
    },
    "mem/ruby/network/garnet/garnet_vnet_config.test.opt": {
        "GarnetVnetConfigTest": (
            "PreservesLegacyFallback", "MapsPerVnetDepth", "MapsVnetClass",
            "RejectsInvalidVectors", "AcceptsFaultModelCompatibleDepths",
            "RejectsFaultModelIncompatibleDepths",
        )
    },
    "mem/ruby/network/garnet/garnet_vc_isolation.test.opt": {
        "GarnetVcIsolationTest": (
            "ExhaustsOnlySelectedVc", "PreservesOtherVcs",
        )
    },
    "mem/ruby/network/garnet/garnet_quiescence.test.opt": {
        "GarnetQuiescenceSnapshotTest": (
            "DetectsEveryPendingClass", "BecomesEmptyOnlyAfterRestore",
            "AccessorsDoNotScheduleEvents",
        )
    },
}


@lru_cache(maxsize=1)
def constants():
    return json.loads(CONSTANTS_PATH.read_text(encoding="utf-8"))


def full_case_names():
    matrix = tuple(
        f"buffer_matrix_{profile}_vc{vcs}_{traffic}"
        for profile in PROFILE_NAMES
        for vcs in VC_COUNTS
        for traffic in MATRIX_TRAFFIC
    )
    random_full = tuple(f"rand_full_s{seed}" for seed in RANDOM_FULL_SEEDS)
    return QUICK_CASES + matrix + random_full + ("rand_nightly_s42_10000",)


def expected_unit_test_names():
    return {
        f"{suite}.{test}"
        for suites in UNIT_BINARIES.values()
        for suite, tests in suites.items()
        for test in tests
    }


def canonical_json_bytes(value):
    return (json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ) + "\n").encode("utf-8")


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def splitmix64(value):
    mask = (1 << 64) - 1
    z = (value + 0x9E3779B97F4A7C15) & mask
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & mask
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & mask
    return (z ^ (z >> 31)) & mask


def keyed_random(master_seed, key_id, stage, beat_index, draw_kind):
    state = master_seed & ((1 << 64) - 1)
    for word in (key_id, stage, beat_index, draw_kind):
        state = splitmix64(state ^ (word & ((1 << 64) - 1)))
    return state


def _draw(seed, index, stage_name, draw_name, beat_index=0):
    table = constants()
    return keyed_random(
        seed,
        index,
        table["stage"][stage_name],
        beat_index,
        table["draw_kind"][draw_name],
    )


def random_error_count(seed, transaction_count):
    return sum(
        _draw(seed, index, "fault_injection", "fault") % 128 == 0
        for index in range(transaction_count)
    )


def parse_positive_vector(value, count, label):
    fields = value.split(",") if isinstance(value, str) else list(value)
    if len(fields) != count or any(field == "" for field in fields):
        raise ValueError(f"{label} must contain exactly {count} non-empty integers")
    try:
        if any(isinstance(field, bool) or not isinstance(field, (int, str))
               for field in fields):
            raise TypeError
        parsed = [int(field) for field in fields]
    except (OverflowError, TypeError, ValueError) as error:
        raise ValueError(f"{label} contains a non-integer element") from error
    if any(item <= 0 for item in parsed):
        raise ValueError(f"{label} entries must all be positive")
    return parsed


def default_config_contract():
    return {
        "routers": 4,
        "mesh_rows": 2,
        "link_width_bits": 128,
        "data_width_bits": 512,
        "id_width_bits": 8,
        "user_width_bits": 0,
        "cacheline_size": 64,
        "strict_protocol": True,
        "data_encoding": "full_bus",
        "source_fifo_depths": [16, 64, 16, 32, 128],
        "message_buffer_depths": [16, 64, 16, 32, 128],
        "local_delivery_depths": [16, 64, 16, 32, 128],
        "buffers_per_vnet": [4, 8, 4, 4, 8],
        "target_capacity": [64, 4096, 64, 4096],
    }


def validate_config_contract(config, scenario):
    """Pure mirror of instantiate-time structural configuration checks."""
    routers = config.get("routers")
    rows = config.get("mesh_rows")
    if not isinstance(routers, int) or routers <= 0 or \
            not isinstance(rows, int) or rows <= 0 or routers % rows:
        raise ValueError("mesh rows must be positive and divide router count")
    link_width = config.get("link_width_bits")
    if not isinstance(link_width, int) or link_width <= 0 or link_width % 8:
        raise ValueError("link width must be a positive multiple of 8")
    data_width = config.get("data_width_bits")
    if data_width not in (64, 128, 256, 512):
        raise ValueError("AXI data width is unsupported")
    if config.get("id_width_bits") not in range(1, 17):
        raise ValueError("AXI ID width is out of range")
    if config.get("user_width_bits") != 0:
        raise ValueError("AXI USER width must be zero")
    if config.get("cacheline_size", 0) < data_width // 8:
        raise ValueError("Ruby block size is below one AXI bus word")
    if config.get("strict_protocol") is not True:
        raise ValueError("strict_protocol must be true")
    if config.get("data_encoding") != "full_bus":
        raise ValueError("compact/shared-pointer data encoding is unsupported")
    for name in (
        "source_fifo_depths", "message_buffer_depths",
        "local_delivery_depths", "buffers_per_vnet",
    ):
        parse_positive_vector(config.get(name, []), 5, name)
    capacities = parse_positive_vector(
        config.get("target_capacity", []), 4, "target_capacity"
    )

    endpoint_map = scenario.get("endpoint_to_router")
    if not isinstance(endpoint_map, dict):
        raise ValueError("scenario has no endpoint_to_router map")
    initiators = endpoint_map.get("initiators")
    targets = endpoint_map.get("targets")
    if not isinstance(initiators, list) or not initiators or \
            not isinstance(targets, list) or not targets:
        raise ValueError("scenario endpoint lists must be non-empty")
    target_nodes = set()
    router_ids = []
    for target in targets:
        node = int(target["dst_node"])
        router = int(target["router_id"])
        if node < 0 or node in target_nodes:
            raise ValueError("duplicate or negative target node")
        target_nodes.add(node)
        router_ids.append(router)
    source_keys = set()
    for source in initiators:
        key = (int(source["src_node"]), int(source["src_port"]))
        if not 0 <= key[0] < 65536 or not 0 <= key[1] < 256 or \
                key in source_keys:
            raise ValueError("invalid or duplicate source endpoint")
        if int(source["default_target"]) not in target_nodes:
            raise ValueError("source default target is unknown")
        source_keys.add(key)
        router_ids.append(int(source["router_id"]))
    if len(router_ids) != len(set(router_ids)) or \
            any(router < 0 or router >= routers for router in router_ids):
        raise ValueError("endpoint router map is duplicate or out of range")
    if int(scenario.get("default_error_target", -1)) not in target_nodes:
        raise ValueError("default error target is unknown")

    ranges = []
    for record in scenario.get("target_ranges", []):
        node = int(record["dst_node"])
        start = int(record["start"], 0) if isinstance(record["start"], str) \
            else int(record["start"])
        end = int(record["end"], 0) if isinstance(record["end"], str) \
            else int(record["end"])
        if node not in target_nodes or not 0 <= start < end <= 1 << 64:
            raise ValueError("target range is empty, invalid, or unknown")
        ranges.append((start, end, node))
    if not ranges:
        raise ValueError("functional scenario requires target ranges")
    ranges.sort()
    if any(current[0] < previous[1]
           for previous, current in zip(ranges, ranges[1:])):
        raise ValueError("target ranges overlap")

    quota_records = scenario.get("quotas", [])
    quota_map = {}
    for record in quota_records:
        key = (int(record["src_node"]), int(record["src_port"]),
               int(record["dst_node"]))
        values = parse_positive_vector([
            record[field] for field in (
                "write_contexts", "write_beats", "read_contexts", "read_beats"
            )
        ], 4, "quota")
        if key[:2] not in source_keys or key[2] not in target_nodes or \
                key in quota_map:
            raise ValueError("quota endpoint is unknown or duplicated")
        quota_map[key] = values
    expected_quota_keys = {
        source + (target,) for source in source_keys for target in target_nodes
    }
    if set(quota_map) != expected_quota_keys:
        raise ValueError("quotas do not exactly cover source/target pairs")
    for target in target_nodes:
        totals = [sum(values[index] for key, values in quota_map.items()
                      if key[2] == target) for index in range(4)]
        if any(total > limit for total, limit in zip(totals, capacities)):
            raise ValueError("quota sum exceeds target capacity")
    return True


def payload_digest(data):
    digest = 1469598103934665603
    for byte in data:
        digest ^= byte
        digest = (digest * 1099511628211) & ((1 << 64) - 1)
    return digest


def _base_endpoint_scenario(name):
    return {
        "schema_version": 1,
        "name": name,
        "stable_scenario_id": name,
        "driver_mode": "concurrent",
        "endpoint_to_router": {
            "initiators": [{
                "src_node": 0,
                "src_port": 0,
                "router_id": 0,
                "default_target": 1,
            }],
            "targets": [{"dst_node": 1, "router_id": 3}],
        },
        "default_error_target": 1,
        "target_ranges": [{
            "dst_node": 1, "start": "0x0", "end": "0x2000000"
        }],
        "quotas": [{
            "src_node": 0,
            "src_port": 0,
            "dst_node": 1,
            "write_contexts": 64,
            "write_beats": 4096,
            "read_contexts": 64,
            "read_beats": 4096,
        }],
    }


def build_random_scenario(seed, transaction_count, stable_scenario_id):
    target_nodes = (100, 101, 102, 103)
    target_routers = (5, 6, 9, 10)
    source_routers = (0, 3, 12, 15)
    initiators = []
    for source, router in enumerate(source_routers):
        initiators.append({
            "src_node": source,
            "src_port": 0,
            "router_id": router,
            "default_target": target_nodes[0],
            "channel_injection_delay_cycles": {
                "aw": _draw(seed, source, "channel_skew", "channel_delay") % 4,
                "w": _draw(seed, source + 16, "channel_skew", "channel_delay") % 4,
                "ar": _draw(seed, source + 32, "channel_skew", "channel_delay") % 4,
            },
        })
    targets = [
        {"dst_node": node, "router_id": router}
        for node, router in zip(target_nodes, target_routers)
    ]
    ranges = []
    for target_index, node in enumerate(target_nodes):
        start = target_index * 0x02000000
        ranges.append({
            "dst_node": node,
            "start": hex(start),
            "end": hex(start + 0x02000000),
        })
    quotas = []
    for source in range(4):
        for node in target_nodes:
            quotas.append({
                "src_node": source,
                "src_port": 0,
                "dst_node": node,
                "write_contexts": 16,
                "write_beats": 1024,
                "read_contexts": 16,
                "read_beats": 1024,
            })

    beat_choices = (1, 2, 4, 8, 16)
    size_choices = (3, 4, 5, 6)
    domain_ordinals = defaultdict(int)
    transactions = []
    for index in range(transaction_count):
        kind = "write" if (
            _draw(seed, index, "workload", "transaction_kind") & 1
        ) == 0 else "read"
        source = _draw(seed, index, "workload", "source") % 4
        target_index = _draw(seed, index, "workload", "target") % 4
        axi_id = _draw(seed, index, "workload", "axi_id") % 8
        beat_count = beat_choices[
            _draw(seed, index, "workload", "beat_count") % len(beat_choices)
        ]
        size = size_choices[
            _draw(seed, index, "workload", "transfer_size") % len(size_choices)
        ]
        direction = 0 if kind == "write" else 1
        domain = (target_index, source, axi_id, direction)
        ordinal = domain_ordinals[domain]
        domain_ordinals[domain] += 1
        domain_index = ((source * 8 + axi_id) * 2) + direction
        address = (
            target_index * 0x02000000
            + domain_index * 0x80000
            + ordinal * 0x1000
        )
        fault = "slverr" if (
            _draw(seed, index, "fault_injection", "fault") % 128 == 0
        ) else "okay"
        transactions.append({
            "kind": kind,
            "source_index": source,
            "dst_node": target_nodes[target_index],
            "axi_id": axi_id,
            "address": hex(address),
            "beat_count": beat_count,
            "size": size,
            "w_before_aw": kind == "write" and bool(
                _draw(seed, index, "channel_skew", "channel_delay") & 1
            ),
            "data_seed": _draw(
                seed, index, "workload", "data_seed"
            ) & 0xFF,
            "qos": _draw(seed, index, "workload", "qos") & 0xF,
            "strobe": "alternating" if (
                _draw(seed, index, "workload", "strobe") & 1
            ) else "full",
            "arrival_cycle": index // 4,
            "target_extra_latency_cycles": _draw(
                seed, index, "memory_latency", "latency"
            ) % 16,
            "target_fault": fault,
        })

    return {
        "schema_version": 1,
        "name": stable_scenario_id,
        "stable_scenario_id": stable_scenario_id,
        "seed": seed,
        "driver_mode": "concurrent",
        "consumer_stall_until_cycle": {"b": 64, "r": 64},
        "endpoint_to_router": {
            "initiators": initiators,
            "targets": targets,
        },
        "default_error_target": target_nodes[-1],
        "target_ranges": ranges,
        "quotas": quotas,
        "transactions": transactions,
    }


def build_matrix_scenario(profile, vcs, traffic):
    if profile not in PROFILE_NAMES or vcs not in VC_COUNTS:
        raise ValueError("unknown buffer matrix profile or VC count")
    if traffic not in MATRIX_TRAFFIC:
        raise ValueError("unknown buffer matrix traffic selector")
    name = f"buffer_matrix_{profile}_vc{vcs}_{traffic}"
    scenario = _base_endpoint_scenario(name)
    window = constants()["buffer_matrix_window_cycles"]
    window_start = window["start"]
    window_end = window["end"]
    deferred_cycle = window_end + 500
    scenario["measurement_window_cycles"] = dict(window)
    scenario["measurement_vnet"] = (
        constants()["channel"][traffic] if traffic != "all" else -1
    )
    txns = []
    if traffic in ("aw", "w", "b"):
        for index in range(16):
            txns.append({
                "kind": "write", "source_index": 0, "dst_node": 1,
                "axi_id": index % 8, "address": hex(0x10000 + index * 0x1000),
                "beat_count": 8 if traffic == "w" else 1, "size": 6,
                "data_seed": index * 7, "strobe": "full",
                "target_extra_latency_cycles": (
                    deferred_cycle if traffic == "w" else
                    window_start if traffic == "b" else 0
                ),
            })
    elif traffic in ("ar", "r"):
        for index in range(16):
            txns.append({
                "kind": "read", "source_index": 0, "dst_node": 1,
                "axi_id": index % 8, "address": hex(0x80000 + index * 0x1000),
                "beat_count": 8 if traffic == "r" else 1, "size": 6,
                "data_seed": 0, "strobe": "full",
                "target_extra_latency_cycles": (
                    deferred_cycle if traffic == "ar" else
                    window_start if traffic == "r" else 0
                ),
            })
    else:
        for index in range(16):
            txns.extend((
                {
                    "kind": "write", "source_index": 0, "dst_node": 1,
                    "axi_id": index % 8,
                    "address": hex(0x10000 + index * 0x1000),
                    "beat_count": 4, "size": 6,
                    "data_seed": index * 11, "strobe": "full",
                    "arrival_cycle": window_start,
                },
                {
                    "kind": "read", "source_index": 0, "dst_node": 1,
                    "axi_id": index % 8,
                    "address": hex(0x80000 + index * 0x1000),
                    "beat_count": 4, "size": 6,
                    "data_seed": 0, "strobe": "full",
                    "arrival_cycle": window_start,
                },
            ))
    scenario["transactions"] = txns
    if traffic == "aw":
        scenario["channel_injection_delay_cycles"] = {
            "aw": window_start, "w": deferred_cycle, "ar": 0
        }
    elif traffic == "w":
        scenario["channel_injection_delay_cycles"] = {
            "aw": 0, "w": window_start, "ar": 0
        }
    elif traffic == "ar":
        scenario["channel_injection_delay_cycles"] = {
            "aw": 0, "w": 0, "ar": window_start
        }
    return scenario


def expand_response_progress_scenario(scenario):
    """Resolve response_progress_v1 to the same frozen plan as AXI_MESH.py."""
    generator = scenario.get("traffic_generator")
    if not isinstance(generator, dict) or \
            generator.get("kind") != "response_progress_v1":
        return scenario
    resolved = dict(scenario)
    initiators = resolved["endpoint_to_router"]["initiators"]
    targets = resolved["endpoint_to_router"]["targets"]
    if len(targets) != 1:
        raise ValueError("response_progress_v1 requires exactly one target")
    stop_cycle = int(generator["stop_cycle"])
    id_count = int(generator.get("id_count", 8))

    def address(field, default):
        value = generator.get(field, default)
        return int(value, 0) if isinstance(value, str) else int(value)

    write_base = address("write_base", 0x10000)
    read_base = address("read_base", 0x40000)
    latency_modulus = int(generator.get("extra_latency_modulus", 4))
    transactions = []
    ordinals = {"write": 0, "read": 0}
    for cycle in range(stop_cycle + 1):
        kind = "write" if cycle % 2 == 0 else "read"
        ordinal = ordinals[kind]
        base = write_base if kind == "write" else read_base
        transactions.append({
            "kind": kind,
            "source_index": cycle % len(initiators),
            "dst_node": int(targets[0]["dst_node"]),
            "axi_id": ordinal % id_count,
            "address": base + ordinal * 64,
            "beat_count": 1,
            "size": 6,
            "data_seed": (ordinal * 17 + cycle) & 0xFF,
            "strobe": "full",
            "arrival_cycle": cycle,
            "target_extra_latency_cycles": ordinal % latency_modulus,
        })
        ordinals[kind] += 1
    resolved.pop("traffic_generator")
    resolved["transactions"] = transactions
    return resolved


def build_negative_scenario(name):
    """Return the deterministic stimulus for one manifest N1--N12 case."""
    if name not in NEGATIVE_CASES:
        raise ValueError(f"unknown AXI negative case: {name}")

    scenario = _base_endpoint_scenario(name)
    valid_write = {
        "kind": "write", "source_index": 0, "dst_node": 1,
        "axi_id": 0, "address": "0x1000", "beat_count": 4, "size": 6,
        "data_seed": 0x31, "strobe": "full",
    }
    valid_read = {
        "kind": "read", "source_index": 0, "dst_node": 1,
        "axi_id": 0, "address": "0x2000", "beat_count": 4, "size": 6,
        "data_seed": 0, "strobe": "full",
    }

    runtime_faults = {
        "n1_runtime_beat_count": ("beat_count_257", valid_write),
        "n4_early_wlast": ("early_wlast", valid_write),
        "n4_late_wlast": ("late_wlast", valid_write),
        "n4_missing_wlast": ("missing_wlast", valid_write),
        "n5_duplicate_rbeat": ("duplicate_rbeat", valid_read),
        "n5_out_of_range_rbeat": ("out_of_range_rbeat", valid_read),
        "n6_duplicate_uid": ("duplicate_uid", valid_read),
        "n6_unknown_response": ("unknown_response", valid_read),
        "n10_source_w_without_aw": ("source_w_without_aw", valid_write),
    }
    if name in runtime_faults:
        fault, template = runtime_faults[name]
        scenario["runtime_fault"] = fault
        scenario["transactions"] = [dict(template)]
        return scenario

    if name in {
        "n1_static_zero_beat_count", "n1_static_257_beat_count", "n2_size",
        "n3a_unaligned_decerr", "n3b_4k_crossing",
        "n7_strict_false", "n8_unmapped_decerr", "n9_lock_decerr",
        "n12_vector_length", "n12_zero_depth",
    }:
        transaction = dict(valid_write)
        if name == "n1_static_zero_beat_count":
            transaction["beat_count"] = 0
        elif name == "n1_static_257_beat_count":
            transaction["beat_count"] = 257
        elif name == "n2_size":
            transaction["size"] = 7
        elif name == "n3a_unaligned_decerr":
            transaction.update(
                address="0x102", beat_count=1, size=2,
                expected_response="decerr",
            )
        elif name == "n3b_4k_crossing":
            transaction.update(address="0xff0", beat_count=2, size=4)
        elif name == "n8_unmapped_decerr":
            transaction.update(
                address="0x2000000", beat_count=1,
                expected_response="decerr",
            )
        elif name == "n9_lock_decerr":
            transaction.update(lock=1, expected_response="decerr")
        scenario["transactions"] = [transaction]
        return scenario

    if name == "n11_fifo_full":
        scenario["endpoint_to_router"]["initiators"][0][
            "channel_injection_delay_cycles"
        ] = {"aw": 24, "w": 0, "ar": 24}
        transactions = []
        for index in range(8):
            transactions.extend((
                {
                    "kind": "write", "source_index": 0, "dst_node": 1,
                    "axi_id": index, "address": hex(0x4000 + index * 0x100),
                    "beat_count": 1, "size": 6, "data_seed": index * 13,
                    "strobe": "full",
                },
                {
                    "kind": "read", "source_index": 0, "dst_node": 1,
                    "axi_id": index, "address": hex(0x8000 + index * 0x100),
                    "beat_count": 1, "size": 6, "data_seed": 0,
                    "strobe": "full",
                },
            ))
        scenario["transactions"] = transactions
        return scenario

    if name == "n11_rob_full":
        scenario["consumer_stall_until_cycle"] = {"b": 96, "r": 96}
        scenario["transactions"] = [
            {
                "kind": "write", "source_index": 0, "dst_node": 1,
                "axi_id": index, "address": hex(0x10000 + index * 0x100),
                "beat_count": 1, "size": 6, "data_seed": index * 17,
                "strobe": "full",
            }
            for index in range(8)
        ]
        return scenario

    if name == "n11_orphan_full":
        scenario["endpoint_to_router"]["initiators"][0][
            "channel_injection_delay_cycles"
        ] = {"aw": 24, "w": 0, "ar": 0}
        scenario["transactions"] = [
            {
                "kind": "write", "source_index": 0, "dst_node": 1,
                "axi_id": index, "address": hex(0x18000 + index * 0x100),
                "beat_count": 1, "size": 6, "w_before_aw": True,
                "data_seed": 0x40 + index, "strobe": "full",
            }
            for index in range(4)
        ]
        return scenario

    raise AssertionError(f"negative scenario not implemented: {name}")


def assign_internal_ids(scenario):
    initiators = scenario["endpoint_to_router"]["initiators"]
    uid_counter = defaultdict(int)
    target_counter = defaultdict(int)
    response_counter = defaultdict(int)
    write_ordinal = defaultdict(int)
    resolved = []
    for plan_index, original in enumerate(scenario["transactions"]):
        record = dict(original)
        source_index = int(record.get("source_index", 0))
        source = initiators[source_index]
        direction = 1 if record["kind"] == "read" else 0
        uid_key = (source_index, direction)
        local = uid_counter[uid_key]
        uid_counter[uid_key] += 1
        uid = (
            (int(source["src_node"]) << 48)
            | (int(source["src_port"]) << 40)
            | (direction << 39)
            | local
        )
        target_key = (
            int(source["src_node"]), int(source["src_port"]),
            int(record.get("axi_id", 0)), direction, int(record["dst_node"]),
        )
        response_key = target_key[:-1]
        record["plan_index"] = plan_index
        record["expected_uid"] = uid
        record["expected_target_seq"] = target_counter[target_key]
        record["expected_response_seq"] = response_counter[response_key]
        target_counter[target_key] += 1
        response_counter[response_key] += 1
        if direction == 0:
            record["expected_write_ordinal"] = write_ordinal[source_index]
            write_ordinal[source_index] += 1
        else:
            record["expected_write_ordinal"] = None
        resolved.append(record)
    return resolved


def workload_jsonl(scenario, stable_scenario_id, seed, tick_per_cycle=1000):
    resolved = assign_internal_ids(scenario)
    initiators = scenario["endpoint_to_router"]["initiators"]
    lines = []
    for record in resolved:
        source = initiators[int(record.get("source_index", 0))]
        beat_count = int(record["beat_count"])
        data_seed = int(record.get("data_seed", 0))
        first_data = bytes((data_seed + lane) & 0xFF for lane in range(64))
        entry = {
            "address": int(record["address"], 0) if isinstance(
                record["address"], str
            ) else int(record["address"]),
            "arrivalTick": int(record.get("arrival_cycle", 0)) * tick_per_cycle,
            "axiId": int(record.get("axi_id", 0)),
            "beatCount": beat_count,
            "burst": record.get("burst", "incr"),
            "cache": int(record.get("cache", 0)),
            "channelSkew": {
                "wBeforeAw": bool(record.get("w_before_aw", False))
            },
            "dataSeed": data_seed,
            "dstNode": int(record["dst_node"]),
            "expectedResponse": str(record.get(
                "expected_response",
                "slverr" if record.get("target_fault", "okay") == "slverr"
                else "okay",
            )).lower(),
            "expectedResponseSeq": record["expected_response_seq"],
            "expectedTargetSeq": record["expected_target_seq"],
            "expectedTxnUid": record["expected_uid"],
            "expectedWriteOrdinal": record["expected_write_ordinal"],
            "kind": record["kind"],
            "lock": int(record.get("lock", 0)),
            "payloadDigest": payload_digest(first_data),
            "planTxnIndex": record["plan_index"],
            "prot": int(record.get("prot", 0)),
            "qos": int(record.get("qos", 0)),
            "region": int(record.get("region", 0)),
            "schemaVersion": 1,
            "seed": seed,
            "size": int(record["size"]),
            "srcNode": int(source["src_node"]),
            "srcPort": int(source["src_port"]),
            "stableScenarioId": stable_scenario_id,
            "strobe": record.get("strobe", "full"),
            "targetExtraLatency": int(
                record.get("target_extra_latency_cycles", 0)
            ),
            "targetFault": record.get("target_fault", "okay"),
        }
        lines.append(canonical_json_bytes(entry))
    return b"".join(lines)


def semantic_config(scenario, cli):
    ignored = {"invocation_id", "outdir", "result_path"}
    stable_scenario = dict(scenario)
    # The manifest case label distinguishes determinism_a/b/replay but is not
    # semantic input.  All three share stable_scenario_id and therefore hash.
    stable_scenario.pop("name", None)
    return {
        "cli": {key: value for key, value in sorted(cli.items())
                if key not in ignored},
        "scenario": stable_scenario,
    }


TRACE_FIELDS = tuple(sorted((
    "schemaVersion", "eventSeq", "tick", "phaseEnum", "eventEnum",
    "channel", "srcNode", "srcPort", "dstNode", "txnUid", "axiId",
    "writeOrdinal", "targetSeq", "responseSeq", "beatIndex",
    "beatCount", "resp", "wireBytes", "semanticBytes", "payloadDigest",
    "occupancy",
)))


def validate_trace_events(events):
    previous = None
    for index, event in enumerate(events):
        if tuple(sorted(event)) != TRACE_FIELDS:
            raise ValueError("event trace field set mismatch")
        if event["schemaVersion"] != 1 or event["eventSeq"] != index:
            raise ValueError("event trace sequence/schema mismatch")
        key = (event["tick"], event["phaseEnum"], event["eventSeq"])
        if previous is not None and key <= previous:
            raise ValueError("event trace is not canonically ordered")
        previous = key
    return True


def load_jsonl(path):
    with Path(path).open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]
