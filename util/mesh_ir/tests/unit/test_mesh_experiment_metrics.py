import pytest

from mesh_ir.experiment.metrics import analyze_candidates, common_hardware, event_window, measure_runtime, outstanding_window, quantile


def candidate(name, bandwidth, cost, outstanding, workload="LOAD_ONLY", **extra):
    return {"case_id": name, "topology": "H5", "profile_id": "frozen",
            "backend_id": "synthetic", "workload": workload,
            "distribution": "uniform", "burst_beats": 16,
            "workload_digest": workload + "-frozen-plan", "comparison_scope": "fixed-workload-dimensions",
            "source_digest": "source", "build_digest": "build", "profile_digest": "profile",
            "status": "valid", "stable": True, "drained": True,
            "full_timing": True, "bandwidth_Bps": bandwidth,
            "router_buffer_bytes": cost, "n_read": outstanding,
            "n_write": outstanding, "p99_ticks": 100,
            "map_digest": str(cost), "complexity": 1, **extra}


def test_f95_uses_joint_reference_not_each_map_peak():
    rows = [candidate("low", 50, 100, 1), candidate("peak", 100, 200, 8),
            candidate("cheap", 95, 150, 4)]
    result = analyze_candidates(rows)
    assert result["B_ref"] == 100
    assert result["resource_recommendation"] == "cheap"
    assert "low" not in result["F95"]
    assert result["conditional_N95"]["100"] == 1
    result = analyze_candidates(rows + [candidate("new", 110, 300, 16)])
    assert result["F95"] == ["new"]


def test_recommendations_are_independent_of_completion_order():
    rows = [candidate("z", 100, 100, 4), candidate("a", 100, 100, 4), candidate("m", 100, 200, 2)]
    assert analyze_candidates(rows, budgets=(100, 200)) == analyze_candidates(list(reversed(rows)), budgets=(100, 200))
    all_workloads = [{**row, "case_id": row["case_id"] + workload,
                      "workload": workload, "workload_digest": workload + "-frozen-plan"}
                     for workload in ("LOAD_ONLY", "STORE_ONLY", "MIXED_1_1") for row in rows]
    assert common_hardware(all_workloads) == common_hardware(list(reversed(all_workloads)))


def test_outstanding_and_buffer_recommendations_are_distinct():
    rows = [candidate("memory", 96, 100, 8), candidate("window", 95, 200, 1),
            candidate("peak", 100, 300, 16)]
    result = analyze_candidates(rows, budgets=(100, 200))
    assert result["resource_recommendation"] == "memory"
    assert result["outstanding_recommendation"] == "window"
    assert result["budgets"]["100"]["reaches_joint_f95"]


def test_mixed_conditional_n95_is_per_direction_window_not_sum():
    result = analyze_candidates([candidate("mixed", 100, 100, 4, "MIXED_1_1")])
    assert result["conditional_N95"]["100"] == 4


def test_invalid_unstable_holdout_and_profiles_cannot_share_reference():
    rows = [candidate("good", 100, 100, 4), candidate("bad", 1000, 10, 1, stable=False)]
    assert analyze_candidates(rows)["B_ref"] == 100
    with pytest.raises(ValueError):
        analyze_candidates(rows + [candidate("hot", 999, 100, 4, distribution="hotspot")])


@pytest.mark.parametrize("override", ({"workload_digest": "longer-plan"},
                                      {"comparison_scope": "single-active-core"},
                                      {"comparison_scope": "other-hotspot-position"},
                                      {"source_digest": "changed-source"},
                                      {"build_digest": "changed-build"},
                                      {"profile_digest": "changed-profile"}))
def test_different_workload_comparison_identity_is_rejected(override):
    with pytest.raises(ValueError):
        analyze_candidates([candidate("baseline", 100, 100, 4), candidate("different", 200, 100, 4, **override)])


def test_common_hardware_rejects_mixed_data_sizes_and_missing_scope():
    rows = [candidate("l", 100, 100, 4), candidate("s", 100, 100, 4, "STORE_ONLY", comparison_scope="longer")]
    with pytest.raises(ValueError):
        common_hardware(rows)
    del rows[0]["comparison_scope"]
    with pytest.raises(KeyError):
        common_hardware(rows[:1])


def test_common_hardware_requires_all_three_actual_runs():
    rows = [candidate("l", 100, 100, 4), candidate("s", 100, 100, 4, "STORE_ONLY")]
    assert common_hardware(rows)["recommendation"] is None
    rows.append(candidate("m", 96, 100, 4, "MIXED_1_1"))
    result = common_hardware(rows)
    assert result["recommendation"]["Q"] == 1


def test_roi_is_half_open_and_outstanding_is_time_weighted():
    assert event_window([(0, 1), (10, 5), (20, 7)], 10, 20) == 5
    result = outstanding_window([(0, 15), (10, 30)], 10, 20)
    assert result["average"] == 1.5
    assert result["peak"] == 2
    assert result["begin_inflight"] == 2 and result["end_inflight"] == 1


def test_percentiles_include_tail_values_and_validate_inputs():
    assert quantile([1] * 99 + [1000], .99) == 1
    assert quantile([1] * 98 + [1000] * 2, .99) == 1000
    with pytest.raises(ValueError):
        quantile([float("nan")], .99)


def measurement_fixture():
    plan = {"spec": {"active_cores": [0], "workload": "LOAD_ONLY"}, "tiles": []}
    actual = {"burst_timings": [], "dma_timings": []}
    for index in range(10):
        address = 4096 + 32 * index
        plan["tiles"].append({"core_id": 0, "target_index": 0, "direction": "read", "descriptor_id": index,
                              "command_id": index, "useful_bytes": 32,
                              "bursts": [{"beat_base": address, "useful_bytes": 32, "beats": 1}]})
        actual["burst_timings"].append({"core_id": 0, "channel": "AR", "address": address,
                                       "beats": 1, "beat_bytes": 32,
                                       "ar_aw_tick": index * 10, "response_tick": index * 10 + 5,
                                       "commit_tick": index * 10 + 8})
        actual["dma_timings"].append({"core_id": 0, "descriptor_id": index,
                                     "done_tick": index * 10 + 10})
    return plan, actual


def test_runtime_measurement_verifies_cohort_units_and_tile_completion():
    plan, actual = measurement_fixture()
    measured = measure_runtime(plan, actual, roi=(20, 80), ticks_per_second=1000,
                               command_issue_ticks={index: index * 10 for index in range(10)},
                               drain_verified=True, network_verified=True, full_timing_verified=True)
    assert measured["status"] == "valid"
    assert measured["bandwidth_Bps"] == 3200
    assert measured["makespan_ticks"] == 100
    assert measured["transaction_latency_ticks"]["count"] == 6
    assert measured["tile_latency_ticks"]["p99"] == 10
    assert measured["fairness"] == 1
    assert measured["per_hbm_core_completion"][0]["read_Bps"] == 3200
    for row in (measured, measured["per_core"][0], measured["per_hbm_core_completion"][0]):
        assert row["read_bytes"] == 192
        assert row["read_bursts_per_second"] == 100
        assert row["tiles_per_second"] == 100
        assert row["transaction_latency_ticks"]["count"] == 6
        assert row["tile_latency_ticks"]["count"] == 6
    assert measured["per_core"][0]["read_pending_sram"]["average"] == .3


def test_workload_makespan_starts_at_dma_command_issue():
    plan, actual = measurement_fixture()
    for row in actual["burst_timings"]:
        for field in ("ar_aw_tick", "response_tick", "commit_tick"):
            row[field] += 10
    for row in actual["dma_timings"]:
        row["done_tick"] += 10
    measured = measure_runtime(plan, actual, roi=(30, 90),
                               command_issue_ticks={index: index * 10 for index in range(10)})
    assert measured["makespan_ticks"] == 110
    assert measured["makespan_origin"] == "first_dma_command_issue"


def test_measurement_profile_is_consumed_without_relaxing_fixed_contract():
    plan, actual = measurement_fixture()
    measured = measure_runtime(plan, actual, roi=(20, 80), subwindows=6)
    assert len(measured["subwindow_Bps"]) == 6
    with pytest.raises(ValueError):
        measure_runtime(plan, actual, roi=(20, 80), mixed_fraction_tolerance=.03)
    with pytest.raises(ValueError):
        measure_runtime(plan, actual, roi=(20, 80), tolerance=.03)


def test_missing_evidence_cannot_enter_recommendations():
    plan, actual = measurement_fixture()
    measured = measure_runtime(plan, actual, roi=(20, 80))
    assert measured["status"] != "valid"
    assert measured["full_timing"] is False
    assert "tile_issue_cohort_missing" in measured["invalid_reasons"]


def test_one_missing_tile_issue_cannot_hide_behind_other_cohort_samples():
    plan, actual = measurement_fixture()
    issues = {index: index * 10 for index in range(10) if index != 4}
    measured = measure_runtime(plan, actual, roi=(20, 80), command_issue_ticks=issues,
                               drain_verified=True, network_verified=True, full_timing_verified=True)
    assert measured["status"] != "valid"
    assert "tile_issue_evidence_incomplete" in measured["invalid_reasons"]


def test_roi_issue_cohort_keeps_late_response_tail():
    plan, actual = measurement_fixture()
    actual["burst_timings"][7]["response_tick"] = 1000
    actual["burst_timings"][7]["commit_tick"] = 1001
    actual["dma_timings"][7]["done_tick"] = 1002
    measured = measure_runtime(plan, actual, roi=(20, 80))
    assert measured["transaction_latency_ticks"]["p99"] == 930
    assert measured["makespan_ticks"] == 1002


def test_duplicate_or_missing_burst_is_rejected():
    plan, actual = measurement_fixture()
    actual["burst_timings"].append(dict(actual["burst_timings"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        measure_runtime(plan, actual)


def test_tile_done_cannot_precede_its_last_burst_commit():
    plan, actual = measurement_fixture()
    actual["dma_timings"][0]["done_tick"] = 1
    with pytest.raises(ValueError, match="tile completion"):
        measure_runtime(plan, actual)


def test_nonfinite_timing_is_rejected_explicitly():
    plan, actual = measurement_fixture()
    actual["burst_timings"][0]["response_tick"] = float("inf")
    with pytest.raises(ValueError, match="integer"):
        measure_runtime(plan, actual)
