import importlib.util
import json
from pathlib import Path

from mesh_ir.experiment.config import BufferMap, WorkloadSpec
from mesh_ir.experiment.execution import file_digest
from mesh_ir.experiment.verify import verified_measurement
import mesh_ir.experiment.verify as VERIFIER
import pytest
from types import SimpleNamespace


REPO = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location("mesh_experiment_runner", REPO / "tests/gem5/ai_mesh/run_mesh_experiment.py")
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


def test_materialization_derives_arch_without_mutating_frozen_profile(tmp_path):
    profile = json.loads((REPO / "configs/example/ai_mesh/experiments/fixed_profile.json").read_text())
    original = json.dumps(profile, sort_keys=True)
    work = WorkloadSpec("MIXED_1_1", bytes_per_core=4096, tile_bytes=1024,
                        hbm_port_bytes=profile["backend"]["port_bytes"])
    first, a = RUNNER.materialize(tmp_path / "a", profile, "H5", work, 1, 8, (1,) * 5)
    second, b = RUNNER.materialize(tmp_path / "b", profile, "H5", work, 4, 8, (2,) * 5)
    assert json.dumps(profile, sort_keys=True) == original
    assert first["n_read"] == 1 and second["n_read"] == 4
    assert a.plan["workload_digest"] == b.plan["workload_digest"]
    assert a.program.arch_digest != b.program.arch_digest


def test_materialization_uses_profile_width_for_packet_and_route_oracles(tmp_path):
    profile = json.loads((REPO / "configs/example/ai_mesh/experiments/fixed_profile.json").read_text())
    profile["network"]["flit_bytes"] = 32
    work = WorkloadSpec("LOAD_ONLY", bytes_per_core=65536, active_cores=(0,),
                        hbm_port_bytes=profile["backend"]["port_bytes"])
    case, bundle = RUNNER.materialize(tmp_path, profile, "H10_EAST2", work, 16, 16, (4, 8, 4, 4, 8))
    assert bundle.oracle["flits_per_packet"] == {"AW": 1, "W": 2, "B": 1, "AR": 1, "R": 2}
    assert bundle.oracle["directed_link_flits"]["endpoint:25->router:4"]["R"] == 4096
    assert bundle.oracle["directed_link_flits"]["router:4->router:3"]["R"] == 4096
    assert case["profile"]["network"]["flit_bytes"] == 32


def test_materialization_derives_write_routes_from_frozen_profile(tmp_path):
    profile = json.loads((REPO / "configs/example/ai_mesh/experiments/east_dual_hbm_32B_profile.json").read_text())
    profile["network"]["yx_vnets"] = [0, 1]
    work = WorkloadSpec("STORE_ONLY", bytes_per_core=65536, active_cores=(20,),
                        distribution="single_target", hotspot_target=0,
                        hbm_port_bytes=profile["backend"]["port_bytes"])
    case, bundle = RUNNER.materialize(tmp_path, profile, "H10_EAST2", work, 32, 32, (4, 8, 4, 4, 8))
    assert bundle.oracle["directed_link_flits"]["router:20->router:15"]["W"] == 2048
    assert "router:24->router:19" not in bundle.oracle["directed_link_flits"]
    assert case["profile"]["network"]["yx_vnets"] == [0, 1]


def test_rerun_archives_prior_result_before_launch_failure(tmp_path):
    profile = json.loads((REPO / "configs/example/ai_mesh/experiments/fixed_profile.json").read_text())
    work = WorkloadSpec("LOAD_ONLY", bytes_per_core=4096, tile_bytes=1024,
                        hbm_port_bytes=profile["backend"]["port_bytes"])
    executable = tmp_path / "not-executable"
    executable.write_text("not a runnable file")
    directory = tmp_path / "case"
    directory.mkdir()
    stale = directory / "network_final.json"
    stale.write_text('{"old": true}')
    record = {"identity": {}, "artifacts": {"network_final.json": file_digest(stale)},
              "verification": "failed", "returncode": 1, "timed_out": False}
    (directory / "execution.json").write_text(json.dumps(record))
    row = RUNNER.execute_case(directory, profile=profile, topology="H5", workload=work,
                              n_read=4, n_write=4, depths=(1,) * 5, gem5=executable,
                              source={"digest": "a" * 64, "head": "test"}, build_digest=file_digest(executable), resume=True)
    assert row["status"] == "failed"
    assert not stale.exists()
    assert (directory / "attempts/1/network_final.json").exists()
    record = json.loads((directory / "execution.json").read_text())
    assert record["launch_error"]


def test_failed_roi_probe_is_visible_in_sweep_measurements(tmp_path):
    profile = json.loads((REPO / "configs/example/ai_mesh/experiments/fixed_profile.json").read_text())
    work = WorkloadSpec("LOAD_ONLY", bytes_per_core=4096, tile_bytes=1024,
                        hbm_port_bytes=profile["backend"]["port_bytes"])
    executable = tmp_path / "not-executable"
    executable.write_text("not a runnable file")
    directory = tmp_path / "cases" / "failed-probe"
    row = RUNNER.execute_with_roi(directory, snapshot_roi=True, profile=profile,
                                  topology="H5", workload=work, n_read=4, n_write=4,
                                  depths=(1,) * 5, gem5=executable,
                                  source={"digest": "a" * 64, "head": "test"},
                                  build_digest=file_digest(executable))
    assert row["correctness"] == "failed"
    assert "roi_probe_unavailable" in row["invalid_reasons"]
    assert json.loads((directory / "measurement.json").read_text()) == row
    summary = RUNNER.analyze_directory(tmp_path)
    assert len(summary) == 1
    assert summary[0]["status"] == "failed"


def test_analysis_recomputes_metrics_and_refuses_missing_raw_evidence(tmp_path, monkeypatch):
    profile = json.loads((REPO / "configs/example/ai_mesh/experiments/fixed_profile.json").read_text())
    work = WorkloadSpec("LOAD_ONLY", bytes_per_core=4096, tile_bytes=1024,
                        hbm_port_bytes=profile["backend"]["port_bytes"])
    executable = tmp_path / "binary"
    executable.write_text("fixed binary identity")
    monkeypatch.setattr(RUNNER, "run_process", lambda *args, **kwargs: {
        "argv": args[0], "returncode": 0, "timed_out": False, "host_seconds": .1})
    measurement = {"status": "valid", "correctness": "pass", "bandwidth_Bps": 10}
    monkeypatch.setattr(RUNNER, "verify_case", lambda directory: dict(measurement))
    monkeypatch.setattr(VERIFIER, "verify_case", lambda directory: dict(measurement))
    source = {"head": "test", "files": {"source": "a" * 64}}
    source["digest"] = RUNNER.canonical_digest(source["files"])
    directory = tmp_path / "case"
    row = RUNNER.execute_case(directory, profile=profile, topology="H5", workload=work,
                              n_read=4, n_write=4, depths=(1,) * 5, gem5=executable,
                              source=source, build_digest=file_digest(executable))
    row["bandwidth_Bps"] = 999
    (directory / "measurement.json").write_text(json.dumps(row))
    assert verified_measurement(directory)["bandwidth_Bps"] == 10
    (directory / "program/program.mshb").unlink()
    with pytest.raises(ValueError, match="artifacts"):
        verified_measurement(directory)


def test_budget_scheduler_preserves_six_groups_and_capacity_control_families(tmp_path, monkeypatch):
    calls = []
    source = {"head": "test", "files": {}, "digest": RUNNER.canonical_digest({})}
    monkeypatch.setattr(RUNNER, "source_identity", lambda repository: source)
    monkeypatch.setattr(RUNNER, "file_digest", lambda path: "a" * 64)
    monkeypatch.setattr(RUNNER, "analyze_directory", lambda directory: calls)
    ports = ((0, 0), (1, 0), (5, 0), (6, 0))
    def simulate(directory, **kwargs):
        work = kwargs["workload"]
        capacities = BufferMap(tuple(tuple(row) for row in kwargs["map_document"]["entries"])) if kwargs.get("map_document") else BufferMap.uniform(ports, kwargs["depths"])
        data_channels = (4,) if work.workload == "LOAD_ONLY" else (1,) if work.workload == "STORE_ONLY" else (1, 4)
        enough = all(row[3] >= 3 for row in capacities.entries if row[2] in data_channels)
        row = {"case_id": kwargs["case_id"], "topology": kwargs["topology"], "profile_id": "fixed", "backend_id": "fixed",
               "workload": work.workload, "distribution": work.distribution, "burst_beats": 16,
               "workload_digest": work.workload, "comparison_scope": "fixed", "source_digest": "fixed", "build_digest": "fixed", "profile_digest": "fixed",
               "status": "valid", "correctness": "pass", "stable": True, "drained": True, "full_timing": True,
               "bandwidth_Bps": 100 if enough else 50, "router_buffer_bytes": capacities.storage_bytes(),
               "n_read": kwargs["n_read"], "n_write": kwargs["n_write"], "p99_ticks": 100,
               "map_digest": capacities.digest(), "router_buffer_map": {"entries": capacities.entries}, "depths": kwargs["depths"],
               "per_router_inport_vnet": [{"router_id": router, "inport_id": port, "vnet": vnet,
                                            "credit_stalls": 1, "no_vc_stalls": 0, "sa_lost": 0,
                                            "full_fraction": 0, "histogram": [1]} for router, port in ports for vnet in range(5)]}
        calls.append(row)
        return row
    monkeypatch.setattr(RUNNER, "execute_with_roi", simulate)
    monkeypatch.setattr(RUNNER.sys, "argv", ["experiment", "sweep", "--outdir", str(tmp_path / "small"),
                                           "--max-cases", "6", "--stages", "A"])
    assert RUNNER.main() == 0
    assert len({(row["topology"], row["workload"]) for row in calls}) == 6
    assert {row["n_read"] for row in calls} == {1}
    calls.clear()
    monkeypatch.setattr(RUNNER.sys, "argv", ["experiment", "sweep", "--outdir", str(tmp_path / "search"),
                                           "--max-cases", "144", "--stages", "A", "B", "C", "D", "E"])
    assert RUNNER.main() == 0
    history = json.loads((tmp_path / "search/candidate_history.json").read_text())
    executed = [row for row in history if row["status"] == "valid"]
    for topology in ("H5", "H10"):
        for workload in ("LOAD_ONLY", "STORE_ONLY", "MIXED_1_1"):
            group = [row for row in executed if row["topology"] == topology and row["workload"] == workload]
            assert {row["capacity_relation"] for row in group if row["stage"] == "C"} == {"same_capacity", "add_capacity"}
    observed = {row["case_id"]: row for row in calls}
    for row in executed:
        if row["stage"] == "D":
            seed = observed[row["seed_case_id"]]
            assert BufferMap(tuple(tuple(entry) for entry in seed["router_buffer_map"]["entries"])).uniform_depths() is not None
            assert observed[row["case_id"]]["router_buffer_bytes"] == seed["router_buffer_bytes"]
            assert observed[row["case_id"]]["n_read"] == seed["n_read"]
    hotspot = json.loads((tmp_path / "search/hotspots_H5_LOAD_ONLY.json").read_text())
    assert set(hotspot["router_tags"]["0"]) == {"EDGE", "CORNER", "HBM_ATTACH"}
    assert set(hotspot["regions"]["HBM_ATTACH"]) == {0, 5, 10, 15, 20}
    calls.clear()
    monkeypatch.setattr(RUNNER.sys, "argv", ["experiment", "sweep", "--outdir", str(tmp_path / "final"),
                                           "--bytes-per-core", str(16 * 1048576), "--validation-bytes-per-core", str(20 * 1048576),
                                           "--max-cases", "180", "--stages", "A", "B", "C", "D", "E", "F", "--stage-budgets", "F=6"])
    assert RUNNER.main() == 0
    history = json.loads((tmp_path / "final/candidate_history.json").read_text())
    finals = [row for row in history if row["stage"] == "F"]
    assert len([row for row in finals if row["status"] == "valid"]) == 6
    assert all(row["case_id"].startswith("F_diagnostic_") for row in finals if row["status"] == "valid")
    neighbors = [row for row in finals if "neighbor" in row.get("validation_roles", [])]
    assert len(neighbors) == 6
    assert all(row["status"] == "not_run" and row["bytes_per_core"] == 20 * 1048576 for row in neighbors)


@pytest.mark.parametrize("command,stages,validation,expected_error", (
    ("sweep", ["F"], None, True),
    ("resume", ["F"], 16 * 1048576, True),
    ("sweep", ["F"], 20 * 1048576 + 1, True),
    ("sweep", ["F"], 20 * 1048576, False),
    ("sweep", ["A"], None, False),
    ("run", ["F"], None, False)))
def test_validation_size_preflight_only_applies_to_sweep_with_f(tmp_path, monkeypatch, command, stages, validation, expected_error):
    calls = []
    class ReachedSourceCollection(Exception):
        pass
    def collect_source(repository):
        calls.append(repository)
        raise ReachedSourceCollection()
    monkeypatch.setattr(RUNNER, "source_identity", collect_source)
    argv = ["experiment", command, "--outdir", str(tmp_path), "--bytes-per-core", str(16 * 1048576), "--stages", *stages]
    if validation is not None:
        argv.extend(("--validation-bytes-per-core", str(validation)))
    monkeypatch.setattr(RUNNER.sys, "argv", argv)
    with pytest.raises(SystemExit if expected_error else ReachedSourceCollection):
        RUNNER.main()
    assert bool(calls) is not expected_error


def test_insufficient_horizon_is_rejected_before_any_gem5_launch(tmp_path, monkeypatch):
    profile = json.loads((REPO / "configs/example/ai_mesh/experiments/fixed_profile.json").read_text())
    profile["runtime"]["max_sim_ticks"] = 10_000_000_000
    work = WorkloadSpec("STORE_ONLY", bytes_per_core=16 * 1048576, distribution="hotspot",
                        hbm_port_bytes=profile["backend"]["port_bytes"])
    oracle = {"directed_link_flits": {"router:0->endpoint:25": {"AW": 819200, "W": 19660800, "B": 0, "AR": 0, "R": 0}}}
    monkeypatch.setattr(RUNNER, "build_workload", lambda *args, **kwargs: SimpleNamespace(oracle=oracle))
    launches = []
    monkeypatch.setattr(RUNNER, "run_process", lambda *args, **kwargs: launches.append(args))
    with pytest.raises(ValueError, match="directed-link.*lower bound"):
        RUNNER.execute_case(tmp_path / "case", profile=profile, topology="H10", workload=work,
                            n_read=16, n_write=16, depths=(4, 8, 4, 4, 8), gem5=tmp_path / "unused",
                            source={"digest": "a" * 64, "head": "test"}, build_digest="b" * 64)
    assert launches == []


@pytest.mark.parametrize("observed_status,stages,budgets", (
    ("unconverged", ["A", "C", "D", "E", "F"], []),
    ("valid", ["A", "F"], ["F=0"])))
def test_requested_families_remain_visible_without_eligible_seeds_or_budget(
        tmp_path, monkeypatch, observed_status, stages, budgets):
    calls = []
    source = {"head": "test", "files": {}, "digest": RUNNER.canonical_digest({})}
    monkeypatch.setattr(RUNNER, "source_identity", lambda repository: source)
    monkeypatch.setattr(RUNNER, "file_digest", lambda path: "a" * 64)
    monkeypatch.setattr(RUNNER, "analyze_directory", lambda directory: calls)
    capacities = BufferMap.uniform(((0, 0), (1, 0)), (4, 8, 4, 4, 8))
    def simulate(directory, **kwargs):
        row = {"case_id": kwargs["case_id"], "topology": kwargs["topology"], "workload": kwargs["workload"].workload,
               "distribution": "uniform", "profile_id": "fixed", "backend_id": "fixed", "burst_beats": 16,
               "workload_digest": "fixed", "comparison_scope": "fixed", "source_digest": "fixed", "build_digest": "fixed",
               "profile_digest": "fixed", "status": observed_status, "correctness": "pass", "stable": observed_status == "valid",
               "drained": True, "full_timing": True, "bandwidth_Bps": 100, "p99_ticks": 100,
               "router_buffer_bytes": capacities.storage_bytes(), "n_read": kwargs["n_read"], "n_write": kwargs["n_write"],
               "depths": kwargs["depths"], "map_digest": capacities.digest(), "router_buffer_map": {"entries": capacities.entries},
               "per_router_inport_vnet": []}
        calls.append(row)
        return row
    monkeypatch.setattr(RUNNER, "execute_with_roi", simulate)
    argv = ["experiment", "sweep", "--outdir", str(tmp_path), "--windows", "1", "--stages", *stages]
    if budgets:
        argv.extend(("--stage-budgets", *budgets))
    monkeypatch.setattr(RUNNER.sys, "argv", argv)
    assert RUNNER.main() == 0
    report = json.loads((tmp_path / "family_coverage.json").read_text())
    families = report["families"]
    history = json.loads((tmp_path / "candidate_history.json").read_text())
    if observed_status == "unconverged":
        for topology in ("H5", "H10"):
            for workload in ("LOAD_ONLY", "STORE_ONLY", "MIXED_1_1"):
                missing = [row for row in families if row["topology"] == topology and row["workload"] == workload and row["stage"] != "A"]
                assert {row["stage"] for row in missing} == {"C", "D", "E", "F"}
                assert all(row["status"] == "unavailable" and row["candidate_ids"] == [] for row in missing)
                assert all(row["unavailable_reason"] in ("no_eligible_seed", "missing_uniform_control") for row in missing)
        assert {row["stage"] for row in history} == {"A"}
        assert all(not row["has_valid_evidence"] for row in families)
    else:
        neighbors = [row for row in families if row["family"] == "long_neighbor"]
        assert len(neighbors) == 6
        assert all(row["status"] == "unavailable" and row["unavailable_reason"] == "no_neighbor" for row in neighbors)
        long = [row for row in families if row["family"] in ("long_recommendation", "long_peak")]
        assert len(long) == 12
        assert all(row["status"] == "not_run" and row["outcomes"] == {"not_run": 1} for row in long)
        observed = {row["case_id"]: row for row in history}
        assert all(observed[row["candidate_ids"][0]]["budget_reason"] == "reserved_stage_budget" for row in long)
