from mesh_ir.experiment.config import BufferMap
from mesh_ir.experiment.search import FamilyCoverage, configuration_identity, coordinate_candidates, equal_capacity_exchanges, fair_candidates, long_validation_points, ranked_candidates, uniform_vectors


def test_coordinate_budget_uses_actual_flit_width_and_vc_count():
    baseline = BufferMap.uniform(((0, 0), (1, 0)), (2,) * 5)
    budget = baseline.storage_bytes(flit_bytes=32, vcs=4)
    candidates = coordinate_candidates(baseline, {"all": {(0, 0), (1, 0)}}, budget,
                                        flit_bytes=32, vcs=4)
    assert candidates
    assert all(row["map"].storage_bytes(flit_bytes=32, vcs=4) <= budget for row in candidates)


def test_uniform_search_identifies_channels_independently():
    vectors = uniform_vectors("LOAD_ONLY", (4, 8, 4, 4, 8))
    assert (4, 8, 4, 8, 8) in vectors
    assert (4, 8, 4, 4, 16) in vectors
    assert (8, 8, 4, 4, 8) not in vectors


def test_packet_sized_integer_candidate_and_independent_r_are_prioritized():
    vectors = uniform_vectors("MIXED_1_1", (4, 8, 4, 4, 8), (2, 3, 1, 2, 3))
    assert vectors[4:6] == [(2, 3, 1, 2, 3), (4, 8, 4, 4, 3)]


def test_equal_capacity_exchange_accounts_for_different_group_sizes():
    baseline = BufferMap.uniform(((0, 0), (1, 0), (2, 0)), (8,) * 5)
    groups = {"one": {(0, 0)}, "two": {(1, 0), (2, 0)}}
    candidates = equal_capacity_exchanges(baseline, groups, channels=(4,))
    assert len(candidates) == 2
    assert all(row["map"].storage_bytes() == baseline.storage_bytes() for row in candidates)
    changed = {row[:3]: row[3] for row in candidates[0]["map"].entries}
    assert changed[(0, 0, 4)] in (6, 10)


def test_coordinate_search_respects_exact_budget_and_depth_floor():
    baseline = BufferMap.uniform(((0, 0), (1, 0)), (1,) * 5)
    assert coordinate_candidates(baseline, {"all": {(0, 0), (1, 0)}},
                                 budget_bytes=baseline.storage_bytes()) == []


def test_budget_order_preserves_groups_and_two_independent_starts():
    candidates = [{"topology": topology, "workload": workload, "seed": seed, "index": index}
                  for topology in ("H5", "H10") for workload in ("LOAD", "STORE", "MIXED")
                  for seed in (0, 1) for index in range(8)]
    first = fair_candidates(candidates, ("topology", "workload", "seed"))[:12]
    assert all(row["index"] == 0 for row in first)
    assert len({(row["topology"], row["workload"], row["seed"]) for row in first}) == 12


def test_explicit_uniform_map_is_same_hardware_not_new_design():
    candidate = {"topology": "H5", "workload": "LOAD_ONLY", "n": 4, "depths": [4, 8, 4, 4, 8],
                 "distribution": "uniform", "hotspot_target": 0, "bytes_per_core": 1048576}
    explicit = {**candidate, "depths": [1] * 5, "map": {"entries":
        BufferMap.uniform(((0, 0), (1, 0)), candidate["depths"]).entries}}
    assert configuration_identity(candidate) == configuration_identity(explicit)
    changed = BufferMap(tuple(tuple(row) for row in explicit["map"]["entries"])).replace({(0, 0, 4): 9})
    assert configuration_identity(candidate) != configuration_identity({**explicit, "map": {"entries": changed.entries}})


def test_host_completion_order_does_not_change_seed_rank():
    rows = [{"case_id": name, "bandwidth_Bps": 100, "router_buffer_bytes": 128,
             "n_read": 4, "n_write": 4, "p99_ticks": 8} for name in ("z", "a", "m")]
    assert ranked_candidates(rows) == ranked_candidates(list(reversed(rows)))
    assert [row["case_id"] for row in ranked_candidates(rows)] == ["a", "m", "z"]


def test_long_validation_keeps_distinct_neighbor_when_recommendation_differs_from_peak():
    rows = [{"case_id": name, "map_digest": capacities, "n_read": n, "n_write": n, "router_buffer_bytes": size}
            for name, capacities, n, size in (("rec", "small", 4, 100), ("peak", "large", 8, 200),
                                              ("neighbor", "small", 8, 100), ("farther", "small", 16, 100))]
    picks = {"resource_recommendation": "rec", "peak": "peak"}
    result = long_validation_points(rows, picks)
    assert [item["point"]["case_id"] for item in result] == ["rec", "peak", "neighbor"]
    assert [item["validation_roles"] for item in result] == [["recommendation"], ["peak"], ["neighbor"]]
    assert result == long_validation_points(list(reversed(rows)), picks)
    result = long_validation_points(rows, {"resource_recommendation": "rec", "peak": "rec"})
    assert result[0]["validation_roles"] == ["recommendation", "peak"]
    assert result[1]["point"]["case_id"] == "neighbor"


def test_family_coverage_resolves_real_duplicate_evidence_and_reverified_status():
    coverage = FamilyCoverage()
    family = coverage.request("F", "H5", "LOAD_ONLY", "common_hardware")
    history = [{"case_id": "actual", "status": "valid"},
               {"case_id": "requested", "status": "duplicate", "duplicate_of": "actual", "family_ids": [family]}]
    initial = coverage.report(history)
    assert initial["families"][0]["has_valid_evidence"]
    assert initial["families"][0]["evidence_case_ids"] == ["actual"]
    restored = FamilyCoverage(initial["families"])
    report = restored.report(history, [{"case_id": "actual", "status": "unconverged"}])
    assert report["status_source"] == "verified_measurements"
    assert report["families"][0]["status"] == "unconverged"
    assert not report["families"][0]["has_valid_evidence"]
    assert restored.report(history, [])["families"][0]["outcomes"] == {"missing_measurement": 1}
