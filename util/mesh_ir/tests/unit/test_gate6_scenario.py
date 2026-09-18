import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "configs" /
                   "example" / "ai_mesh"))

from gate6_serving_scenario import (  # noqa: E402
    adapter_indices,
    combined_scenario,
    mesh_core_node,
    mesh_sram_node,
    node_ids,
)

SRAM_BASE = 0x400000000
SRAM_STRIDE = 0x400000
SRAM_TILE = 0x200000
HBM_BASE = 0x800000000
HBM_BYTES = 0x100000000
HOST_SHARED_BASE = 0x100000000
HOST_SHARED_BYTES = 0x100000


def windows(core_count):
    return dict(
        agent=[(0x30000000, 0x30000000 + 0x1000),
               (0x30100000, 0x30100000 + 0x1000),
               (0x10000000, 0x10000000 + 0x100000)],
        mesh=dict(
            sram=[(SRAM_BASE + index * SRAM_STRIDE,
                   SRAM_BASE + index * SRAM_STRIDE + SRAM_TILE)
                  for index in range(core_count)],
            hbm=(HBM_BASE, HBM_BASE + HBM_BYTES),
            host_shared=(HOST_SHARED_BASE,
                         HOST_SHARED_BASE + HOST_SHARED_BYTES),
        ),
        quotas=dict(write_contexts=4, write_beats=64, read_contexts=4,
                    read_beats=64),
    )


def build(core_count=2):
    parts = windows(core_count)
    return combined_scenario(core_count, parts["agent"], parts["mesh"],
                             parts["quotas"], routers=16)


def test_node_ids_and_adapter_indices_are_disjoint():
    for core_count in (1, 2, 4):
        nodes = node_ids(core_count)
        mesh_nodes = [mesh_core_node(nodes, index)
                      for index in range(core_count)] + \
            [mesh_sram_node(nodes, index) for index in range(core_count)]
        agent_nodes = [nodes["driver"], nodes["frontend"],
                       nodes["npu_control"], nodes["agent_proxy"]]
        assert len(set(mesh_nodes + agent_nodes)) == \
            len(mesh_nodes) + len(agent_nodes)
        assert nodes["hbm"] not in mesh_nodes + agent_nodes
        assert nodes["error"] not in mesh_nodes + agent_nodes + [nodes["hbm"]]
        adapters = adapter_indices(core_count)
        assert adapters["frontend_initiator"] == 1
        assert adapters["mesh_core_initiator_begin"] == 2
        assert adapters["mesh_aperture_target_begin"] == 2
        assert adapters["hbm_target"] == 2 + core_count
        assert adapters["initiator_count"] == 2 + core_count
        assert adapters["target_count"] == 4 + core_count


def test_scenario_lists_cover_every_endpoint_once():
    scenario = build(2)
    initiators = scenario["endpoint_to_router"]["initiators"]
    targets = scenario["endpoint_to_router"]["targets"]
    assert [entry["src_node"] for entry in initiators] == [0, 1, 4, 5]
    assert targets[0]["dst_node"] == 2
    assert targets[1]["dst_node"] == 3
    assert [entry["dst_node"] for entry in targets[2:4]] == [6, 7]
    assert targets[4]["dst_node"] == 8
    assert targets[5]["dst_node"] == 9
    assert scenario["default_error_target"] == 9
    assert scenario["driver_mode"] == "gate3_protocol"
    assert scenario["traffic_shaper"] == "AgentAxiDriver"


def test_every_quota_references_listed_nodes():
    scenario = build(3)
    sources = {entry["src_node"]
               for entry in scenario["endpoint_to_router"]["initiators"]}
    targets = {entry["dst_node"]
               for entry in scenario["endpoint_to_router"]["targets"]}
    quotas = scenario["quotas"]
    assert len(quotas) == len(sources) * len(targets)
    assert {entry["src_node"] for entry in quotas} == sources
    assert {entry["dst_node"] for entry in quotas} == targets
    for entry in quotas:
        assert entry["src_port"] == 0
        assert entry["write_contexts"] > 0 and entry["read_beats"] > 0


def test_target_ranges_are_sorted_and_do_not_overlap():
    scenario = build(2)
    ranges = scenario["target_ranges"]
    spans = sorted((entry["start"], entry["end"], entry["dst_node"])
                   for entry in ranges)
    for (start, end, _), (next_start, _, _) in zip(spans, spans[1:]):
        assert start < end
        assert end <= next_start
    hbm_endpoint = [entry for entry in ranges
                    if entry["dst_node"] == 8]
    assert hbm_endpoint
    assert hbm_endpoint[0]["start"] == HBM_BASE


def test_serving_program_addresses_land_in_the_hbm_window():
    from mesh_ir.abi.decoder import decode_program

    fixture = Path(__file__).resolve().parents[4] / \
        "tests/gem5/ai_mesh/fixtures/gate6/serving_two_tokens.mshb"
    program = decode_program(fixture.read_bytes())
    scenario = build(2)
    hbm = next(entry for entry in scenario["target_ranges"]
               if entry["dst_node"] == 8 and entry["start"] == HBM_BASE)
    relocations = {}
    for relocation in program.relocations:
        name = program.strings[relocation.symbol_sid - 1].value
        relocations[name] = HBM_BASE + relocation.offset_bytes
    for name in ("input", "weight", "kv", "output"):
        assert hbm["start"] <= relocations[name] < hbm["end"], name
