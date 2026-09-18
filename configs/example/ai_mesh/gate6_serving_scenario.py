"""Combined Gate 6 serving scenario: agent endpoints plus the mesh fabric.

Pure data mapping shared by the serving config and the unit tests: node ids
and Ruby adapter indices are allocated once here so the agent side (driver /
frontend) and the mesh side (per-core apertures and DMA bridges) never alias.
"""

from __future__ import annotations

AGENT_INITIATOR_COUNT = 2
AGENT_TARGET_COUNT = 2
DRIVER_INITIATOR_INDEX = 0
FRONTEND_INITIATOR_INDEX = 1
NPU_CONTROL_TARGET_INDEX = 0
AGENT_PROXY_TARGET_INDEX = 1

NPU_CONTROL_WINDOW_BYTES = 0x1000


def node_ids(core_count):
    if core_count <= 0:
        raise ValueError("core_count must be positive")
    base = AGENT_INITIATOR_COUNT + AGENT_TARGET_COUNT
    return {
        "driver": 0,
        "frontend": 1,
        "npu_control": 2,
        "agent_proxy": 3,
        "mesh_core_begin": base,
        "mesh_sram_begin": base + core_count,
        "hbm": base + 2 * core_count,
        "error": base + 2 * core_count + 1,
    }


def adapter_indices(core_count):
    nodes = node_ids(core_count)
    return {
        "driver_initiator": DRIVER_INITIATOR_INDEX,
        "frontend_initiator": FRONTEND_INITIATOR_INDEX,
        "npu_control_target": NPU_CONTROL_TARGET_INDEX,
        "agent_proxy_target": AGENT_PROXY_TARGET_INDEX,
        "mesh_core_initiator_begin": AGENT_INITIATOR_COUNT,
        "mesh_aperture_target_begin": AGENT_TARGET_COUNT,
        "hbm_target": AGENT_TARGET_COUNT + core_count,
        "error_target": AGENT_TARGET_COUNT + core_count + 1,
        "initiator_count": AGENT_INITIATOR_COUNT + core_count,
        "target_count": AGENT_TARGET_COUNT + core_count + 2,
    }


def mesh_core_node(nodes, index):
    return nodes["mesh_core_begin"] + index


def mesh_sram_node(nodes, index):
    return nodes["mesh_sram_begin"] + index


def agent_target_ranges(npu_control_base, agent_proxy_control_base,
                        host_base, host_bytes):
    nodes = node_ids(1)
    return [
        {"node": "npu_control", "start": npu_control_base,
         "end": npu_control_base + NPU_CONTROL_WINDOW_BYTES},
        {"node": "agent_proxy", "start": agent_proxy_control_base,
         "end": agent_proxy_control_base + NPU_CONTROL_WINDOW_BYTES},
        {"node": "agent_proxy", "start": host_base,
         "end": host_base + host_bytes},
    ], nodes


def agent_only_scenario(agent_windows, quotas):
    quota = {
        "write_contexts": quotas["write_contexts"],
        "write_beats": quotas["write_beats"],
        "read_contexts": quotas["read_contexts"],
        "read_beats": quotas["read_beats"],
    }
    return {
        "schema_version": 1,
        "name": "gate6_serving_agent",
        "driver_mode": "gate3_protocol",
        "endpoint_to_router": {
            "initiators": [
                {"src_node": 0, "src_port": 0, "router_id": 0,
                 "default_target": 2},
                {"src_node": 1, "src_port": 0, "router_id": 1,
                 "default_target": 3},
            ],
            "targets": [
                {"dst_node": 2, "router_id": 2},
                {"dst_node": 3, "router_id": 3},
            ],
        },
        "default_error_target": 2,
        "cpu_core_count": 0,
        "cpu_garnet_ports": 0,
        "ucie_links": 0,
        "traffic_shaper": "AgentAxiDriver",
        "target_ranges": [
            {"dst_node": 2, "start": agent_windows[0][0],
             "end": agent_windows[0][1]},
            {"dst_node": 3, "start": agent_windows[1][0],
             "end": agent_windows[1][1]},
            {"dst_node": 3, "start": agent_windows[2][0],
             "end": agent_windows[2][1]},
        ],
        "quotas": [
            {"src_node": source, "src_port": 0, "dst_node": target, **quota}
            for source in (0, 1)
            for target in (2, 3)
        ],
        "channel_injection_delay_cycles": {"aw": 0, "w": 0, "ar": 0},
        "response_ejection_stall_until_cycle": {"b": 0, "r": 0},
        "consumer_stall_until_cycle": {"b": 0, "r": 0},
    }


def combined_scenario(core_count, agent_windows, mesh_windows, quotas,
                      routers):
    nodes = node_ids(core_count)
    endpoint_count = AGENT_INITIATOR_COUNT + core_count + \
        AGENT_TARGET_COUNT + core_count + 2
    if routers < endpoint_count:
        raise ValueError(
            "routers %d cannot host %d unique AXI endpoints"
            % (routers, endpoint_count))
    router = 0
    initiators = []
    for src_node, default_target in (
            (nodes["driver"], nodes["npu_control"]),
            (nodes["frontend"], nodes["agent_proxy"])):
        initiators.append({"src_node": src_node, "src_port": 0,
                           "router_id": router,
                           "default_target": default_target})
        router += 1
    for index in range(core_count):
        initiators.append({"src_node": mesh_core_node(nodes, index),
                           "src_port": 0, "router_id": router,
                           "default_target": nodes["error"]})
        router += 1
    targets = []
    for dst_node in (nodes["npu_control"], nodes["agent_proxy"]):
        targets.append({"dst_node": dst_node, "router_id": router})
        router += 1
    for index in range(core_count):
        targets.append({"dst_node": mesh_sram_node(nodes, index),
                        "router_id": router})
        router += 1
    for dst_node in (nodes["hbm"], nodes["error"]):
        targets.append({"dst_node": dst_node, "router_id": router})
        router += 1
    target_ranges = [
        {"dst_node": nodes["npu_control"], "start": agent_windows[0][0],
         "end": agent_windows[0][1]},
        {"dst_node": nodes["agent_proxy"], "start": agent_windows[1][0],
         "end": agent_windows[1][1]},
        {"dst_node": nodes["agent_proxy"], "start": agent_windows[2][0],
         "end": agent_windows[2][1]},
    ]
    target_ranges.extend(
        {"dst_node": mesh_sram_node(nodes, index), "start": window[0],
         "end": window[1]}
        for index, window in enumerate(mesh_windows["sram"]))
    target_ranges.append({"dst_node": nodes["hbm"],
                          "start": mesh_windows["hbm"][0],
                          "end": mesh_windows["hbm"][1]})
    if mesh_windows.get("host_shared") is not None:
        target_ranges.append({"dst_node": nodes["hbm"],
                              "start": mesh_windows["host_shared"][0],
                              "end": mesh_windows["host_shared"][1]})
    quota = {
        "write_contexts": quotas["write_contexts"],
        "write_beats": quotas["write_beats"],
        "read_contexts": quotas["read_contexts"],
        "read_beats": quotas["read_beats"],
    }
    source_nodes = [entry["src_node"] for entry in initiators]
    target_nodes = [entry["dst_node"] for entry in targets]
    return {
        "schema_version": 1,
        "name": "gate6_serving",
        "driver_mode": "gate3_protocol",
        "endpoint_to_router": {"initiators": initiators, "targets": targets},
        "default_error_target": nodes["error"],
        "cpu_core_count": 0,
        "cpu_garnet_ports": 0,
        "ucie_links": 0,
        "traffic_shaper": "AgentAxiDriver",
        "target_ranges": target_ranges,
        "quotas": [
            {"src_node": source, "src_port": 0, "dst_node": target, **quota}
            for source in source_nodes
            for target in target_nodes
        ],
        "channel_injection_delay_cycles": {"aw": 0, "w": 0, "ar": 0},
        "response_ejection_stall_until_cycle": {"b": 0, "r": 0},
        "consumer_stall_until_cycle": {"b": 0, "r": 0},
        "mesh_planned_extra_latency": [],
        "mesh_planned_faults": [],
    }
