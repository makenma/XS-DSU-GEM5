import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[4]
RUNNER = REPO / "tests/gem5/ai_mesh/run_mesh_experiment.py"


def run_smoke(outdir, extra=()):
    outdir.mkdir(parents=True, exist_ok=True)
    run = subprocess.run(
        [sys.executable, str(RUNNER), "smoke", "--topology", "H5",
         "--workload", "MIXED_1_1", "--bytes-per-core", "4096",
         "--tile-bytes", "1024", "--n", "4", "--outdir", str(outdir),
         *extra],
        cwd=REPO, capture_output=True, text=True, timeout=180,
    )
    (outdir / "observer_test.log").write_text(run.stdout + run.stderr)
    assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.parametrize("heterogeneous", [False, True])
def test_real_mixed_observer_preserves_capacity_credit_and_bytes(tmp_path, heterogeneous):
    extra = ()
    if heterogeneous:
        probe = tmp_path / "probe"
        run_smoke(probe)
        initial = json.loads((probe / "network_initial.json").read_text())
        inputs = sorted({(row["receiver_id"], row["receiver_port"], row["vnet"])
                         for row in initial["receiver_capacity_map"]
                         if row["receiver_kind"] == 1})
        entries = [[router, port, vnet, 1 if (router + port) % 2 else 8]
                   for router, port, vnet in inputs]
        path = tmp_path / "buffer_map.json"
        path.write_text(json.dumps({"entries": entries}))
        extra = ("--buffer-map", str(path))
    run_smoke(tmp_path, extra)
    initial = json.loads((tmp_path / "network_initial.json").read_text())
    final = json.loads((tmp_path / "network_final.json").read_text())
    final2 = json.loads((tmp_path / "network_final2.json").read_text())
    memory = json.loads((tmp_path / "memory_checks.json").read_text())
    assert final["schema"] == "ai_mesh_experiment_snapshot_v1"
    assert final["drained"]
    assert final2["drained"]
    assert final2["network_cycle"] == final["network_cycle"] + 1
    assert final2["credit_ledger"] == final["credit_ledger"]
    assert final2["network_counters"] == final["network_counters"]
    assert final2["links"] == final["links"]
    assert len(final["cores"]) == 25
    assert len(final["targets"]) == 6
    assert all(value == 0 for value in final["quiescence"].values())
    capacity = {(row["link_id"], row["vc"]): row
                for row in final["receiver_capacity_map"]}
    assert len(capacity) == len(final["receiver_capacity_map"])
    assert capacity
    links = {row["link_id"]: row for row in final["links"]}
    ledger = {(row["link_id"], row["vc"]): row for row in final["credit_ledger"]}
    assert capacity.keys() == ledger.keys()
    for row in links.values():
        assert row["flits"] == sum(row["vc_flits"])
    for row in final["credit_ledger"]:
        resolved = capacity[(row["link_id"], row["vc"])]
        assert row["initial"] == row["current"] == resolved["depth"]
        assert row["sent"] == row["returned"]
        assert row["sent"] == links[row["link_id"]]["vc_flits"][row["vc"]]
    input_links = {(row["receiver_id"], row["receiver_port"], row["vc"]): row
                   for row in capacity.values() if row["receiver_kind"] == 1}
    before = {(row["router_id"], row["inport_id"], row["vc"]): row
              for row in initial["input_vcs"]}
    elapsed = final["network_cycle"] - initial["network_cycle"]
    assert elapsed > 0
    for row in final["input_vcs"]:
        old = before[(row["router_id"], row["inport_id"], row["vc"])]
        assert len(row["time_histogram"]) == row["depth"] + 1
        assert sum(row["time_histogram"]) - sum(old["time_histogram"]) == elapsed
        assert row["enqueued"] == row["dequeued"]
        assert row["occupancy"] == 0
        assert row["high_water"] <= row["depth"]
        resolved = input_links[(row["router_id"], row["inport_id"], row["vc"])]
        assert row["depth"] == resolved["depth"]
        assert row["enqueued"] == ledger[(resolved["link_id"], row["vc"])]["sent"]
    assert sum(row["enqueued"] for row in final["input_vcs"]) > 0
    assert sum(row["flits"] for row in final["links"]) > 0
    for old, new in zip(final["input_vcs"], final2["input_vcs"]):
        assert new["time_histogram"][0] == old["time_histogram"][0] + 1
        assert new["time_histogram"][1:] == old["time_histogram"][1:]
        assert all(new[key] == old[key] for key in (
            "enqueued", "dequeued", "credit_stalls", "no_vc_stalls", "sa_lost"))
    counters = final["network_counters"]
    for direction, kind, counter in (("sender_kind", 0, "flits_injected"),
                                      ("receiver_kind", 0, "flits_received")):
        observed = [0] * 5
        for row in capacity.values():
            if row[direction] == kind:
                observed[row["vnet"]] += ledger[(row["link_id"], row["vc"])]["sent"]
        assert observed == counters[counter]
    assert counters["packets_injected"] == counters["packets_received"]
    assert counters["wire_bytes_injected"] == counters["wire_bytes_received"]
    for core in final["cores"]:
        initial_core = initial["cores"][core["index"]]
        idle_core = final2["cores"][core["index"]]
        progress = core["axi_initiator_progress"]
        resources = core["axi_initiator_occupancy"]
        assert all(value == 0 for value in resources.values())
        assert all(value == 0 for value in initial_core["axi_initiator_occupancy"].values())
        assert idle_core["axi_initiator_progress"] == progress
        assert idle_core["axi_initiator_occupancy"] == resources
        assert idle_core["sram_reservation_rejection_attempts"] == core["sram_reservation_rejection_attempts"]
        for name, value in progress.items():
            if isinstance(value, dict):
                assert set(value) == {"fifo_full", "outstanding_full", "response_reservation_full"}
                assert all(type(count) is int and count >= 0 for count in value.values())
                assert all(count == 0 for count in initial_core["axi_initiator_progress"][name].values())
            else:
                assert type(value) is int and value >= 0
                assert initial_core["axi_initiator_progress"][name] == 0
        assert progress["r_packets_buffered"] == core["r_beats_consumed"]
        assert progress["b_packets_buffered"] == core["b_consumed"]
        assert progress["r_transactions_retired"] == core["read_rlast_consumed"]
        assert progress["b_transactions_retired"] == core["b_consumed"]
        assert set(core["sram_reservation_rejection_attempts"]) == {"read", "write"}
        assert all(type(value) is int and value >= 0 for value in core["sram_reservation_rejection_attempts"].values())
        assert initial_core["sram_reservation_rejection_attempts"] == {"read": 0, "write": 0}
        reads = [row for row in core["burst_timings"] if row["read"]]
        writes = [row for row in core["burst_timings"] if not row["read"]]
        assert reads and writes
        assert core["peak_w_accepted_per_cycle"] == 1
        assert core["ar_accepted"] == core["read_rlast_consumed"] == len(reads)
        assert core["aw_accepted"] == core["b_consumed"] == len(writes)
        assert core["read_committed_bytes"] == sum(row["beats"] * row["beat_bytes"] for row in reads)
        assert core["write_completed_bytes"] == sum(row["beats"] * row["beat_bytes"] for row in writes)
        assert all(row["addr_accept_tick"] <= row["response_tick"] <= row["local_commit_tick"]
                   for row in reads)
        assert all(core[key] == 0 for key in ("axi_read_outstanding", "axi_write_outstanding",
                   "pending_read_reservations", "scheduled_read_commits", "error_read_bursts", "error_write_bursts"))
    assert sum(target["write_committed_bytes"] for target in final["targets"]) == sum(
        core["write_completed_bytes"] for core in final["cores"])
    for target in final["targets"]:
        backend = target["synthetic_backend"]
        if backend is not None:
            assert backend["submitted"] == backend["completed"] == backend["retired"]
            assert backend["ready"] == backend["queued"] == 0
    if heterogeneous:
        by_input = {(row["router_id"], row["inport_id"]): row["depth"]
                    for row in final["input_vcs"]}
        assert {depth for (router, _), depth in by_input.items() if router == 0} == {1, 8}
        assert any(row["depth"] == 1 and row["vnet"] == 4 and row["enqueued"] > 1
                   for row in final["input_vcs"])
        assert counters["flits_injected"][4] > counters["packets_injected"][4]
        assert counters["flits_injected"] == counters["flits_received"]
    assert memory["drained"]
    assert memory["checked_bytes"] > 0
    assert memory["checked_bytes"] == memory["requested_bytes"]
    assert memory["mismatch_count"] == memory["unreadable_bytes"] == 0
