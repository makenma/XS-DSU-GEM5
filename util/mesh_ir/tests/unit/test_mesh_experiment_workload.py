from dataclasses import replace
from pathlib import Path

import pytest

from mesh_ir.abi.decoder import decode_program
from mesh_ir.abi.encoder import encode_program
from mesh_ir.abi.verifier import verify_program
from mesh_ir.builder import load_arch
from mesh_ir.experiment.config import Topology, WorkloadSpec
from mesh_ir.experiment.workload import build_workload, packet_flits, traffic_oracle, validate_simulation_horizon
from mesh_ir.experiment.verify import verify_workload_plan
from mesh_ir.generated import abi as A


@pytest.fixture
def arch():
    original = load_arch(Path(__file__).resolve().parents[4] /
                         "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    return replace(original, core_ids=tuple(range(25)), mesh_rows=5, mesh_cols=5)


@pytest.mark.parametrize("kind", ("LOAD_ONLY", "STORE_ONLY", "MIXED_1_1"))
@pytest.mark.parametrize("topology", ("H5", "H10"))
def test_generated_program_is_abi_valid_and_exact(arch, kind, topology):
    spec = WorkloadSpec(kind, bytes_per_core=262144)
    bundle = build_workload(arch, Topology(topology), spec)
    verify_program(bundle.program, arch)
    verify_program(decode_program(encode_program(bundle.program)), arch)
    oracle = bundle.oracle
    assert oracle["useful_bytes"] == 25 * 262144
    assert oracle["burst_counts"]["read"] + oracle["burst_counts"]["write"] == 25 * 512
    assert all(row["beats"] == 16 for tile in bundle.plan["tiles"]
               for row in tile["bursts"])
    assert len(bundle.program.allocations) <= 25 * 4
    assert not any(c.opcode in (A.OPCODE.REPEAT, A.OPCODE.BARRIER)
                   for c in bundle.program.commands)
    if kind == "MIXED_1_1":
        assert oracle["useful_read_bytes"] == oracle["useful_write_bytes"]
        tiles = [tile for tile in bundle.plan["tiles"] if tile["core_id"] == 0]
        read, write = tiles[:2]
        command = next(c for c in bundle.program.commands
                       if c.command_id == write["command_id"])
        waits = bundle.program.command_waits[command.wait_begin:
                                           command.wait_begin + command.wait_count]
        assert read["completion_event"] not in [wait.event_id for wait in waits]
    assert all(1 <= tile["expected_byte"] <= 255 for tile in bundle.plan["tiles"])
    assert all(1 <= row["expected_byte"] <= 255 for row in bundle.plan["setup"])


def test_packet_and_link_oracle_is_independent(arch):
    bundle = build_workload(arch, Topology("H5"), WorkloadSpec(
        "LOAD_ONLY", bytes_per_core=65536, active_cores=(0,)))
    oracle = bundle.oracle
    assert oracle["packets"] == {"AW": 0, "W": 0, "B": 0, "AR": 128, "R": 2048}
    assert oracle["flits"]["AR"] == 256
    assert oracle["flits"]["R"] == 6144
    assert oracle["directed_link_flits"]["endpoint:0->router:0"]["AR"] == 256
    assert oracle["directed_link_flits"]["endpoint:25->router:0"]["R"] == 6144
    bundle.program.expected_traffic[0].bursts = 99999
    assert oracle["burst_counts"]["read"] == 128


@pytest.mark.parametrize("kind", ("LOAD_ONLY", "STORE_ONLY", "MIXED_1_1"))
def test_yx_write_routes_preserve_workload_and_return_paths(arch, kind):
    spec = WorkloadSpec(kind, bytes_per_core=131072, active_cores=(20,),
                        distribution="single_target", hotspot_target=0)
    xy = build_workload(arch, Topology("H10_EAST2"), spec,
                        flit_bytes=32, data_header_sideband=True)
    yx = build_workload(arch, Topology("H10_EAST2"), spec,
                        flit_bytes=32, data_header_sideband=True, yx_vnets=(0, 1))
    assert encode_program(xy.program) == encode_program(yx.program)
    assert xy.plan == yx.plan
    assert xy.oracle["flits"] == yx.oracle["flits"]
    for channel in ("B", "AR", "R"):
        routes = [{key: value[channel] for key, value in bundle.oracle["directed_link_flits"].items()
                   if value[channel]} for bundle in (xy, yx)]
        assert routes[0] == routes[1]
    for channel in ("AW", "W"):
        if not yx.oracle["flits"][channel]:
            continue
        routes = {key for key, value in yx.oracle["directed_link_flits"].items() if value[channel]}
        path = (20, 15, 10, 5, 0, 1, 2, 3, 4)
        assert routes == {"endpoint:20->router:20", "router:4->endpoint:25"} | {
            f"router:{a}->router:{b}" for a, b in zip(path, path[1:])}


@pytest.mark.parametrize("vnets", ((0, 0), (-1,), (5,), (True,), ("W",)))
def test_routing_rejects_invalid_yx_vnets(arch, vnets):
    with pytest.raises(ValueError, match="yx_vnets"):
        build_workload(arch, Topology("H10_EAST2"),
                       WorkloadSpec("STORE_ONLY", bytes_per_core=65536), yx_vnets=vnets)


def test_sideband_metadata_changes_serialization_without_changing_data(arch):
    spec = WorkloadSpec("MIXED_1_1", bytes_per_core=131072)
    plain = build_workload(arch, Topology("H10_EAST2"), spec, flit_bytes=32)
    sideband = build_workload(arch, Topology("H10_EAST2"), spec,
                              flit_bytes=32, data_header_sideband=True)
    assert sideband.plan == plain.plan
    assert sideband.oracle["useful_bytes"] == 25 * 131072
    assert sideband.oracle["flits"] == sideband.oracle["packets"]
    assert sideband.oracle["packets"] == plain.oracle["packets"]
    assert sideband.oracle["flits"]["R"] * 2 == plain.oracle["flits"]["R"]
    assert packet_flits((24, 16, 8, 24, 16), 32, 16, True)["R"] == 2


def test_hotspot_plan_exact_half_and_different_holdout(arch):
    spec = WorkloadSpec("MIXED_1_1", bytes_per_core=524288,
                        distribution="hotspot", hotspot_target=9)
    bundle = build_workload(arch, Topology("H10"), spec)
    hot = [tile for tile in bundle.plan["tiles"] if tile["target_index"] == 9]
    assert sum(tile["useful_bytes"] for tile in hot) == 25 * 524288 // 2
    assert bundle.plan["workload_digest"] != build_workload(
        arch, Topology("H10"), replace(spec, hotspot_target=0)).plan["workload_digest"]


def test_near_far_diagnostic_keeps_fixed_target_and_other_hardware(arch):
    spec = WorkloadSpec("LOAD_ONLY", bytes_per_core=131072,
                        distribution="single_target", active_cores=(0,))
    near = build_workload(arch, Topology("H10"), spec)
    far = build_workload(arch, Topology("H10"), replace(spec, active_cores=(24,)))
    assert {tile["target_index"] for tile in near.plan["tiles"] + far.plan["tiles"]} == {0}
    assert sum(near.oracle["directed_link_flits"][key]["AR"] for key in near.oracle["directed_link_flits"]
               if key.startswith("router:") and "->router:" in key) == 0
    assert sum(far.oracle["directed_link_flits"][key]["AR"] for key in far.oracle["directed_link_flits"]
               if key.startswith("router:") and "->router:" in key) == 8 * far.oracle["flits"]["AR"]
    assert {stream.core_id for stream in near.program.streams} == set(range(25))


@pytest.mark.parametrize("change", ("data_size", "pair", "slot", "setup"))
def test_workload_declaration_cannot_disagree_with_tiles_and_setup(arch, change):
    bundle = build_workload(arch, Topology("H5"), WorkloadSpec("MIXED_1_1", bytes_per_core=262144))
    verify_workload_plan(bundle.plan, arch)
    if change == "data_size":
        bundle.plan["spec"]["bytes_per_core"] *= 2
    elif change == "pair":
        bundle.plan["tiles"][0]["pair_index"] += 1
    elif change == "slot":
        bundle.plan["tiles"][0]["slot"] += 1
    else:
        bundle.plan["setup"].pop()
    with pytest.raises(ValueError):
        verify_workload_plan(bundle.plan, arch)


def test_hot_store_serialization_lower_bound_rejects_ten_ms_not_one_hundred_ms(arch):
    plan = {"tiles": [{"core_id": core, "direction": "write", "target_index": 0,
                       "address": 4096 + core * 512, "useful_bytes": 512} for core in range(25)]}
    one_burst_per_core = traffic_oracle(plan, Topology("H10"))
    hot_bursts_per_core = (16 * 1048576 // 2) // 512
    oracle = {"directed_link_flits": {route: {channel: count * hot_bursts_per_core for channel, count in counts.items()}
                                       for route, counts in one_burst_per_core["directed_link_flits"].items()}}
    with pytest.raises(ValueError, match="directed-link.*lower bound"):
        validate_simulation_horizon(oracle, 10_000_000_000, arch.clock_hz, 10**12)
    result = validate_simulation_horizon(oracle, 100_000_000_000, arch.clock_hz, 10**12)
    assert result["route"] == "router:0->endpoint:25"
    assert result["flits"] == 20_480_000
    assert result["minimum_ticks"] == 10_240_000_000
    assert result["bound_kind"] == "directed_link_serialization_necessary_only"
    with pytest.raises(ValueError, match="directed-link.*lower bound"):
        validate_simulation_horizon(oracle, result["minimum_ticks"], arch.clock_hz, 10**12)


def test_serialization_tick_conversion_rounds_up():
    oracle = {"directed_link_flits": {"link": {"AW": 1, "W": 1, "B": 0, "AR": 0, "R": 0}}}
    assert validate_simulation_horizon(oracle, 10, 3, 10)["minimum_ticks"] == 7
