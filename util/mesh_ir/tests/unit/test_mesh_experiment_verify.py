import pytest
from copy import deepcopy

from mesh_ir.experiment.verify import constant_byte_digest, core_resource_statistics, input_statistics, memory_expected_bytes, verify_memory, verify_transport, verify_full_run_windows, weighted_occupancy
from mesh_ir.experiment.verify import buffer_map
from mesh_ir.experiment.verify import verify_network, QUIESCENCE_FIELDS


@pytest.fixture
def dual_lane_network_snapshots():
    initial = {"schema": "ai_mesh_experiment_snapshot_v1", "network_cycle": 0,
               "receiver_capacity_map": [], "credit_ledger": [], "links": [],
               "input_vcs": [], "network_counters": {}}
    for lane, direction in enumerate(("West", "West_ext")):
        initial["links"].append({"link_id": lane, "flits": 0, "vc_flits": [0] * 5})
        for vnet in range(5):
            initial["receiver_capacity_map"].append({
                "link_id": lane, "vc": vnet, "vnet": vnet, "depth": 1, "initial_credit": 1,
                "sender_kind": 1, "sender_id": 1, "sender_port": lane,
                "sender_direction": direction, "receiver_kind": 1, "receiver_id": 0,
                "receiver_port": lane, "receiver_direction": direction.replace("West", "East")})
            initial["credit_ledger"].append({"link_id": lane, "vc": vnet, "depth": 1,
                "initial": 1, "current": 1, "sent": 0, "returned": 0})
            initial["input_vcs"].append({"router_id": 0, "inport_id": lane,
                "vnet": vnet, "vc": vnet, "depth": 1})
    for key in ("packets_injected", "packets_received", "flits_injected", "flits_received"):
        initial["network_counters"][key] = [0] * 5
    final = deepcopy(initial)
    final.update(network_cycle=20, drained=True, quiescence=dict.fromkeys(QUIESCENCE_FIELDS, 0))
    for lane, count in enumerate((8, 2)):
        final["links"][lane].update(flits=count, vc_flits=[0, 0, 0, 0, count])
        final["credit_ledger"][lane * 5 + 4].update(sent=count, returned=count)
    for key in final["network_counters"]:
        final["network_counters"][key] = [0, 0, 0, 0, 10]
    channels = dict(zip(("AW", "W", "B", "AR", "R"), (0, 0, 0, 0, 10)))
    oracle = {"directed_link_flits": {"router:1->router:0": channels},
              "packets": channels, "flits": channels}
    case = {"profile": {"network": {"vcs_per_vnet": 1, "ni_depths": [1] * 5, "dual_lane": True}},
            "router_input_vc_depths": [], "buffer_depths": [1] * 5}
    return initial, final, oracle, case


def test_dual_lane_oracle_sums_physical_lanes_without_losing_identity(dual_lane_network_snapshots):
    result = verify_network(*dual_lane_network_snapshots)
    assert [row["R"] for row in result["per_link"]] == [8, 2]
    assert [row["lane"] for row in result["per_link"]] == [0, 1]


@pytest.mark.parametrize("change", ("duplicate_lane", "wrong_sum", "wrong_receiver", "single_profile"))
def test_dual_lane_oracle_rejects_broken_physical_evidence(dual_lane_network_snapshots, change):
    initial, final, oracle, case = dual_lane_network_snapshots
    if change == "duplicate_lane":
        for snapshot in (initial, final):
            for row in snapshot["receiver_capacity_map"][5:]:
                row["sender_direction"] = "West"
                row["receiver_direction"] = "East"
    elif change == "wrong_sum":
        oracle["directed_link_flits"]["router:1->router:0"]["R"] = 11
    elif change == "wrong_receiver":
        for snapshot in (initial, final):
            for row in snapshot["receiver_capacity_map"][5:]:
                row["receiver_direction"] = "East"
    else:
        case["profile"]["network"]["dual_lane"] = False
    with pytest.raises(ValueError):
        verify_network(initial, final, oracle, case)


def test_buffer_cost_includes_both_local_fifo_banks():
    snapshot = {"input_vcs": [
        {"router_id": 0, "inport_id": lane, "ingress_port": 0,
         "lane": lane, "vnet": vnet, "vc": vnet * 4 + vc, "depth": 2}
        for lane in (0, 1) for vnet in range(5) for vc in range(4)]}
    assert buffer_map(snapshot).storage_bytes(32, 4) == 2 * 5 * 4 * 2 * 32


def test_shared_ingress_closes_credit_across_both_lane_banks():
    banks = [{"router_id": 0, "inport_id": lane, "ingress_port": 0,
              "lane": lane, "vc": 0, "vnet": 0, "direction": "Local",
              "occupancy": 0, "depth": 2, "high_water": 0,
              "time_histogram": [0, 0, 0], "enqueued": 0, "dequeued": 0,
              "credit_stalls": 0, "no_vc_stalls": 0, "sa_lost": 0}
             for lane in (0, 1)]
    before = {"tick": 0, "network_cycle": 0, "input_vcs": banks, "drained": True,
              "receiver_capacity_map": [{"receiver_kind": 1, "receiver_id": 0,
                  "receiver_port": 0, "vc": 0, "vnet": 0, "depth": 2, "link_id": 0}],
              "credit_ledger": [{"link_id": 0, "vc": 0, "sent": 0, "returned": 0}],
              "links": [{"link_id": 0, "vc_flits": [0]}]}
    after = deepcopy(before)
    after.update(tick=10, network_cycle=10)
    for bank, count in zip(after["input_vcs"], (2, 3)):
        bank.update(enqueued=count, dequeued=count, high_water=1,
                    time_histogram=[10 - count, count, 0])
    after["credit_ledger"][0].update(sent=5, returned=5)
    after["links"][0]["vc_flits"] = [5]
    rows = input_statistics(before, after, 1)
    assert [row["enqueued"] for row in rows] == [2, 3]
    assert [row["lane"] for row in rows] == [0, 1]
    after["credit_ledger"][0]["returned"] = 4
    with pytest.raises(ValueError, match="return"):
        input_statistics(before, after, 1)


def test_memory_oracle_checks_all_writes_and_last_read_per_ring_slot():
    plan = {"tiles": [
        {"core_id": 0, "direction": "read", "slot": 0, "useful_bytes": 64},
        {"core_id": 0, "direction": "read", "slot": 0, "useful_bytes": 64},
        {"core_id": 0, "direction": "write", "slot": 0, "useful_bytes": 64}]}
    assert memory_expected_bytes(plan) == 128
    memory = {"schema": "ai_mesh_experiment_memory_checks_v1", "drained": True,
              "requested_bytes": 128, "checked_bytes": 128,
              "mismatch_count": 0, "unreadable_bytes": 0, "first_mismatch": None}
    verify_memory(plan, memory)
    memory["requested_bytes"] = memory["checked_bytes"] = 64
    with pytest.raises(ValueError, match="coverage"):
        verify_memory(plan, memory)


def test_occupancy_quantiles_are_time_weighted():
    stats = weighted_occupancy([98, 1, 1])
    assert stats["average"] == .03
    assert stats["p95"] == 0
    assert stats["p99"] == 1
    assert stats["full_fraction"] == .01
    with pytest.raises(ValueError):
        weighted_occupancy([1, -1])


def test_early_overwritten_tile_digest_is_checked_independently():
    plan = {"setup": [], "tiles": [
        {"descriptor_id": 1, "direction": "read", "useful_bytes": 64, "expected_byte": 17, "bursts": [{}]},
        {"descriptor_id": 2, "direction": "read", "useful_bytes": 64, "expected_byte": 29, "bursts": [{}]}]}
    actual = {"transport": [
        {"descriptor_id": row["descriptor_id"], "read_bytes": 64, "write_bytes": 0,
         "read_bursts": 1, "write_bursts": 0, "p2p_bytes": 0, "fill_bytes": 0, "p2p_bursts": 0,
         "read_discarded_bytes": 0, "write_drained_uncommitted_bytes": 0,
         "error_code": 0, "payload_digest": constant_byte_digest(row["expected_byte"], 64)}
        for row in plan["tiles"]]}
    verify_transport(plan, actual)
    actual["transport"][0]["payload_digest"] = constant_byte_digest(29, 64)
    with pytest.raises(ValueError, match="payload digest"):
        verify_transport(plan, actual)


def test_window_histogram_peak_does_not_relabel_full_run_high_water():
    entry = {"router_id": 0, "inport_id": 0, "ingress_port": 0, "lane": 0, "vc": 0, "vnet": 0, "direction": "Local", "occupancy": 0,
             "depth": 4, "high_water": 4, "time_histogram": [0, 0, 0, 0, 2],
             "enqueued": 4, "dequeued": 4, "credit_stalls": 1, "no_vc_stalls": 2, "sa_lost": 3}
    capacity = {"receiver_kind": 1, "receiver_id": 0, "receiver_port": 0, "vc": 0,
                "vnet": 0, "depth": 4, "link_id": 0}
    begin = {"tick": 2, "network_cycle": 2, "input_vcs": [entry], "drained": True,
             "receiver_capacity_map": [capacity], "credit_ledger": [{"link_id": 0, "vc": 0, "sent": 4, "returned": 4}],
             "links": [{"link_id": 0, "vc_flits": [4]}]}
    end = {**begin, "tick": 12, "network_cycle": 12, "credit_ledger": [{"link_id": 0, "vc": 0, "sent": 6, "returned": 6}],
           "links": [{"link_id": 0, "vc_flits": [6]}], "input_vcs": [
        {**entry, "time_histogram": [8, 2, 0, 0, 2], "enqueued": 6, "dequeued": 6}]}
    row, = input_statistics(begin, end, 1)
    assert row["full_run_high_water"] == 4
    assert row["window_time_supported_peak"] == 1
    assert "high_water" not in row


def test_resource_progress_is_differenced_but_occupancy_is_not_a_stall_cycle():
    progress = {field: 0 for field in ("b_packets_buffered", "r_packets_buffered", "same_id_responses_blocked",
                                      "b_transactions_retired", "r_transactions_retired", "write_quota_stalls", "read_quota_stalls")}
    progress.update({channel: dict(fifo_full=0, outstanding_full=0, response_reservation_full=0)
                     for channel in ("ar_rejection_attempts", "aw_rejection_attempts")})
    occupancy = {field: 0 for field in ("read_waiting_quota", "read_granted_waiting_forward", "read_forwarded_to_message_buffer",
                                       "write_waiting_quota", "write_granted_waiting_forward", "write_forwarded_to_message_buffer",
                                       "r_rob_reserved_beats", "r_rob_buffered_beats", "b_rob_reserved_transactions", "b_rob_buffered_transactions")}
    queue = {field: [4] * 5 for field in ("local_fifo", "message_buffer", "adapter_ingress", "message_buffer_stall_cycles")}
    entry = {"core_id": 0, "axi_initiator_progress": progress, "axi_initiator_occupancy": occupancy,
             "sram_reservation_rejection_attempts": {"read": 0, "write": 0}, "sram_service_cycles": 5.0,
             "sram_bank_conflicts": 0, "queue_high_water": queue, "axi_read_outstanding": 0, "axi_write_outstanding": 0}
    final = {**entry, "axi_initiator_progress": {**progress, "read_quota_stalls": 3},
             "axi_initiator_occupancy": {**occupancy, "read_waiting_quota": 2}, "sram_service_cycles": 12.0, "axi_read_outstanding": 2}
    row, = core_resource_statistics({"cores": [entry]}, {"cores": [final]})
    assert row["axi_initiator_progress_delta"]["read_quota_stalls"] == 3
    assert row["occupancy_end"]["read_waiting_quota"] == 2
    assert row["sram_service_cycles_delta"] == 7
    assert row["queue_full_run_high_water"]["local_fifo"] == [4] * 5
    assert row["message_buffer_stall_cycles_delta"] == [0] * 5
    assert "stall_cycles_total" not in row


def test_outstanding_limit_is_checked_over_full_run_not_just_roi():
    cores = [{"core_id": 0, "peak_axi_read_outstanding": 2}]
    actual = {"burst_timings": [{"core_id": 0, "channel": channel, "ar_aw_tick": begin, "response_tick": end}
                                for channel, begin, end in (("AR", 1, 9), ("AR", 2, 8), ("AW", 10, 15))]}
    rows = verify_full_run_windows(cores, actual, 2, 1)
    assert rows[0]["read"]["peak"] == 2
    cores[0]["peak_axi_read_outstanding"] = 3
    with pytest.raises(ValueError, match="outstanding"):
        verify_full_run_windows(cores, actual, 2, 1)
    cores[0]["peak_axi_read_outstanding"] = 2
    with pytest.raises(ValueError, match="outstanding"):
        verify_full_run_windows(cores, actual, 1, 1)


@pytest.mark.parametrize("change", ("missing", "duplicate", "counter", "histogram"))
def test_input_statistics_requires_exact_input_vc_and_credit_cover(change):
    entry = {"router_id": 0, "inport_id": 0, "ingress_port": 0, "lane": 0, "vc": 0, "vnet": 0, "direction": "Local", "occupancy": 0,
             "depth": 2, "high_water": 0, "time_histogram": [2, 0, 0],
             "enqueued": 0, "dequeued": 0, "credit_stalls": 0, "no_vc_stalls": 0, "sa_lost": 0}
    capacity = {"receiver_kind": 1, "receiver_id": 0, "receiver_port": 0, "vc": 0,
                "vnet": 0, "depth": 2, "link_id": 0}
    before = {"tick": 2, "network_cycle": 2, "input_vcs": [entry], "drained": True,
              "receiver_capacity_map": [capacity], "credit_ledger": [{"link_id": 0, "vc": 0, "sent": 0, "returned": 0}],
              "links": [{"link_id": 0, "vc_flits": [0]}]}
    after = {**before, "tick": 12, "network_cycle": 12, "input_vcs": [{**entry, "time_histogram": [12, 0, 0]}]}
    input_statistics(before, after, 1)
    if change == "missing":
        after["input_vcs"] = []
    elif change == "duplicate":
        after["input_vcs"] *= 2
    elif change == "counter":
        after["input_vcs"][0]["enqueued"] = 1
    else:
        after["input_vcs"][0]["time_histogram"] = [11, 0, 0]
    with pytest.raises(ValueError):
        input_statistics(before, after, 1)
