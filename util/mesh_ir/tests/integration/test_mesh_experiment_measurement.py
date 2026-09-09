import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[4]


@pytest.mark.parametrize("workload", ("LOAD_ONLY", "STORE_ONLY", "MIXED_1_1"))
def test_east_dual_hbm_full_data_survives_single_flit_transport(tmp_path, workload):
    result = subprocess.run(
        [sys.executable, str(REPO / "tests/gem5/ai_mesh/run_mesh_experiment.py"),
         "smoke", "--topology", "H10_EAST2", "--workload", workload,
         "--profile", str(REPO / "configs/example/ai_mesh/experiments/east_dual_hbm_32B_profile.json"),
         "--bytes-per-core", "131072", "--tile-bytes", "65536", "--n", "4",
         "--outdir", str(tmp_path)],
        cwd=REPO, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    measured = json.loads((tmp_path / "measurement.json").read_text())
    assert measured["correctness"] == "pass" and measured["drained"]
    assert measured["router_buffer_bytes"] == 116 * 4 * 28 * 32
    config = json.loads((tmp_path / "raw/config.json").read_text())
    network = config["system"]["ruby"]["network"]
    router_paths = {router["router_id"]: router["path"] for router in network["routers"]}
    for router in (4, 9, 14, 19, 24):
        attached = [link for link in network["ext_links"]
                    if link["int_node"] == router_paths[router]]
        assert len(attached) == 3
        assert len({link["ext_node"] for link in attached}) == 3
    final = json.loads((tmp_path / "network_final.json").read_text())
    initial = json.loads((tmp_path / "network_initial.json").read_text())
    for suffix in ("injected", "received"):
        packets = [a - b for a, b in zip(final["network_counters"]["packets_" + suffix],
                                         initial["network_counters"]["packets_" + suffix])]
        flits = [a - b for a, b in zip(final["network_counters"]["flits_" + suffix],
                                       initial["network_counters"]["flits_" + suffix])]
        assert flits == packets


@pytest.mark.parametrize("dual_lane", (False, True))
def test_write_yx_routes_match_physical_links_and_drain(tmp_path, dual_lane):
    profile = json.loads((REPO / "configs/example/ai_mesh/experiments/east_dual_hbm_32B_profile.json").read_text())
    profile["network"].update(yx_vnets=[0, 1], dual_lane=dual_lane)
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(profile))
    directory = tmp_path / "case"
    result = subprocess.run(
        [sys.executable, str(REPO / "tests/gem5/ai_mesh/run_mesh_experiment.py"),
         "smoke", "--topology", "H10_EAST2", "--workload", "MIXED_1_1",
         "--profile", str(profile_path), "--bytes-per-core", "1310720",
         "--tile-bytes", "65536", "--n", "4", "--outdir", str(directory)],
        cwd=REPO, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    measured = json.loads((directory / "measurement.json").read_text())
    assert measured["correctness"] == "pass" and measured["drained"]
    config = json.loads((directory / "raw/config.json").read_text())
    network = config["system"]["ruby"]["network"]
    assert network["yx_vnets"] == [0, 1]
    assert network["dual_lane"] == dual_lane
    oracle = json.loads((directory / "program/oracle.json").read_text())
    assert oracle["directed_link_flits"]["router:20->router:15"]["W"] > 0
    assert oracle["directed_link_flits"]["router:4->router:3"]["R"] > 0


def test_real_dma_tile_issue_evidence_covers_every_submitted_command(tmp_path):
    result = subprocess.run(
        [sys.executable, str(REPO / "tests/gem5/ai_mesh/run_mesh_experiment.py"),
         "smoke", "--topology", "H5", "--workload", "MIXED_1_1",
         "--bytes-per-core", "4096", "--tile-bytes", "1024", "--n", "4",
         "--outdir", str(tmp_path)],
        cwd=REPO, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "already has parent" not in (tmp_path / "run.log").read_text()
    plan = json.loads((tmp_path / "program/workload.json").read_text())
    snapshot = json.loads((tmp_path / "network_final.json").read_text())
    actual = json.loads((tmp_path / "actual_result.json").read_text())
    issues = {core["core_id"]: {int(key): value for key, value in
              core["command_issue_ticks"].items()} for core in snapshot["cores"]}
    done = {(row["core_id"], row["descriptor_id"]): row["done_tick"]
            for row in actual["dma_timings"]}
    for tile in plan["tiles"]:
        assert tile["command_id"] in issues[tile["core_id"]]
        issue = issues[tile["core_id"]][tile["command_id"]]
        assert 0 < issue <= done[tile["core_id"], tile["descriptor_id"]]
