import pytest

from mesh_ir.experiment.config import BufferMap, Topology, WorkloadSpec


def test_topologies_preserve_all_cores_and_add_memory_interfaces():
    h5, h10 = Topology("H5"), Topology("H10")
    assert h5.core_ids == h10.core_ids == tuple(range(25))
    assert h5.hbm_routers == (0, 5, 10, 15, 20)
    assert h10.hbm_routers == h5.hbm_routers + (4, 9, 14, 19, 24)
    assert h5.error_node == 30 and h10.error_node == 35
    assert h5.target_node(4) == 29


def test_east_dual_hbm_has_three_local_devices_per_attachment():
    topology = Topology("H10_EAST2")
    assert topology.hbm_routers == (4, 4, 9, 9, 14, 14, 19, 19, 24, 24)
    endpoints = topology.endpoints()
    assert len(endpoints) == len({row["node"] for row in endpoints}) == 36
    for router in (4, 9, 14, 19, 24):
        assert sorted(row["kind"] for row in endpoints if row["router"] == router) == ["core", "hbm", "hbm"]
    assert not any(row["kind"] == "hbm" and row["router"] % 5 != 4 for row in endpoints)
    assert {row["router"] for row in endpoints if row["kind"] == "error"} == {12}


def test_router_tags_keep_overlap_before_exclusive_regions():
    tags = Topology("H5").router_tags((4, 6))
    assert len(tags) == 25
    assert set(tags["0"]) == {"EDGE", "CORNER", "HBM_ATTACH"}
    assert set(tags["4"]) == {"EDGE", "CORNER", "HOT"}
    assert tags["6"] == ["HOT"]
    assert sum("EDGE" in values for values in tags.values()) == 16
    assert sum("CORNER" in values for values in tags.values()) == 4


def test_buffer_cost_uses_actual_ports_not_five_per_router():
    ports = ((0, 0), (0, 1), (1, 0))
    uniform = BufferMap.uniform(ports, (4, 8, 4, 4, 8))
    assert uniform.slots() == 3 * 4 * 28
    assert uniform.storage_bytes() == 3 * 4 * 28 * 16
    altered = uniform.replace({(0, 1, 4): 1})
    assert altered.storage_bytes() == uniform.storage_bytes() - 7 * 4 * 16
    assert altered.digest() != uniform.digest()
    assert altered.overrides()[-1] == "1:0:4:8"


@pytest.mark.parametrize("entries", [
    [(0, 0, 0, 1)] * 2,
    [(0, 0, 0, 0)],
    [(25, 0, 0, 1)],
    [(0, 0, 5, 1)],
])
def test_invalid_or_incomplete_buffer_maps_rejected(entries):
    with pytest.raises(ValueError):
        BufferMap(tuple(entries))


def test_unknown_override_and_noninteger_depth_rejected():
    baseline = BufferMap.uniform(((0, 0),), (1,) * 5)
    for changes in ({(0, 1, 4): 2}, {(0, 0, 4): True}):
        with pytest.raises(ValueError):
            baseline.replace(changes)


def test_workload_requires_exact_tiles_and_balanced_mixed():
    with pytest.raises(ValueError):
        WorkloadSpec("MIXED_1_1", bytes_per_core=65536)
    with pytest.raises(ValueError):
        WorkloadSpec("LOAD_ONLY", bytes_per_core=65537)
    assert WorkloadSpec("MIXED_1_1").tiles_per_core == 16
