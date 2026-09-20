import copy
import importlib.util
import json
from pathlib import Path

from mesh_ir.experiment.config import BufferMap, WorkloadSpec
from mesh_ir.experiment.execution import file_digest
import pytest
import yaml
from types import SimpleNamespace

from mesh_ir.architecture import architecture_document, load_arch, load_arch_text
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.experiment.config import Topology
from mesh_ir.experiment.verify import verify_effective
from mesh_ir.experiment.workload import build_workload, resolve_experiment_architecture

REPO = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location("mesh_experiment_runner", REPO / "tests/gem5/ai_mesh/run_mesh_experiment.py")
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


@pytest.fixture
def effective_configuration(tmp_path):
    profile = json.loads((REPO / "configs/example/ai_mesh/experiments/fixed_profile.json").read_text())
    arch = resolve_experiment_architecture(
        load_arch(REPO / "configs/example/ai_mesh/arch/mesh_5x5.yaml"),
        Topology("H5"),
        profile,
    )
    path = tmp_path / "arch.yaml"
    path.write_text(yaml.safe_dump(architecture_document(arch), sort_keys=False))
    fabric = arch.fabric
    hbm_targets = tuple(item for item in fabric.targets if item.synthetic_hbm is not None)
    error_target = next(item for item in fabric.targets if item.dst_node == fabric.default_error_target_node)
    targets = (*hbm_targets, error_target)
    clock = {"type": "SrcClockDomain", "path": "clock", "clock": [500]}
    objects = [clock, {
        "type": "GarnetNetwork", "path": "network", "clk_domain": "clock",
        "num_rows": 5, "number_of_virtual_networks": 5, "dual_lane": fabric.network.dual_lane,
        "yx_vnets": list(fabric.network.yx_vnets), "routing_algorithm": 1,
        "ni_flit_size": fabric.network.flit_bytes, "vcs_per_vnet": fabric.network.vcs_per_vnet,
        "buffers_per_vnet": list(fabric.network.router_input_depths),
        "ni_buffers_per_vnet": list(fabric.network.ni_receive_depths),
        "router_input_vc_depths": [
            ":".join(map(str, (item.router_id, item.input_port, item.vnet, item.depth)))
            for item in fabric.network.router_input_overrides
        ],
        "enable_fault_model": False,
    }]
    for core in arch.core_ids:
        objects.extend((
            {"type": "MeshDummyCore", "path": f"core.{core}", "clk_domain": "clock",
             "core_id": core, "sram_bytes": arch.sram_bytes, "sram_banks": arch.sram_banks,
             "sram_bank_queue_depth": arch.sram_bank_queue_depth,
             "sram_read_ports": arch.sram_read_ports_per_bank, "sram_write_ports": arch.sram_write_ports_per_bank,
             "sram_read_bytes_per_cycle": arch.sram_read_bytes_per_cycle_per_bank,
             "sram_write_bytes_per_cycle": arch.sram_write_bytes_per_cycle_per_bank,
             "admit_window": arch.admit_window, "decode_width": arch.decode_width},
            {"type": "AxiTensorDmaEngine", "path": f"dma.{core}", "clk_domain": "clock",
             "data_bus_bytes": arch.axi_data_bytes, "max_burst_beats": arch.axi_max_burst_beats,
             "descriptor_queue_depth": arch.dma_descriptor_queue_depth,
             "segment_queue_depth": arch.dma_segment_queue_depth, "axi_id_count": 1 << arch.axi_id_bits},
            {"type": "AxiGarnetBridge", "path": f"bridge.{core}", "clk_domain": "clock",
             "data_bus_bytes": arch.axi_data_bytes, "aw_queue_depth": arch.dma_segment_queue_depth,
             "ar_queue_depth": arch.dma_segment_queue_depth, "axi_id_count": 1 << arch.axi_id_bits,
             "w_beats_per_cycle": fabric.axi.w_beats_per_cycle},
            {"type": "GarnetRouter", "path": f"router.{core}", "clk_domain": "clock",
             "latency": fabric.network.router_latency_cycles, "width": fabric.network.flit_bytes,
             "dual_lane": fabric.network.dual_lane},
        ))
    quota = {(item.src_node, item.src_port, item.dst_node): item for item in fabric.quotas}
    for source in fabric.initiators:
        objects.append({
            "type": "AxiInitiatorAdapter", "path": f"initiator.{source.src_node}", "clk_domain": "clock",
            "src_node": source.src_node, "src_port": source.src_port,
            "max_outstanding_reads": fabric.initiator.max_outstanding_reads,
            "max_outstanding_writes": fabric.initiator.max_outstanding_writes,
            "data_bus_bytes": arch.axi_data_bytes, "source_fifo_depths": list(fabric.initiator.source_fifo_depths),
            "b_rob_transactions": fabric.initiator.b_reorder_transactions,
            "r_rob_beats": fabric.initiator.r_reorder_beats,
            "pre_aw_bursts": fabric.initiator.pre_aw_bursts, "pre_aw_beats": fabric.initiator.pre_aw_beats,
            "wire_header_bytes": list(fabric.axi.wire_header_bytes),
            "data_header_sideband": fabric.axi.data_header_sideband,
            **{
                "quota_" + dimension: [
                    getattr(quota[(source.src_node, source.src_port, target.dst_node)], dimension)
                    for target in targets
                ]
                for dimension in ("read_contexts", "write_contexts", "read_beats", "write_beats")
            },
        })
    for target in targets:
        synthetic = target.synthetic_hbm
        objects.append({
            "type": "AxiTargetAdapter", "path": f"target.{target.dst_node}", "clk_domain": "clock",
            "dst_node": target.dst_node, "data_bus_bytes": arch.axi_data_bytes,
            "service_depths": list(fabric.target.service_queue_depths),
            "wire_header_bytes": list(fabric.axi.wire_header_bytes),
            "data_header_sideband": fabric.axi.data_header_sideband,
            "response_ready_depths": list(fabric.target.response_ready_depths),
            "orphan_w_transactions": fabric.target.orphan_w_transactions,
            "orphan_w_beats": fabric.target.orphan_w_beats,
            "synthetic_hbm_enabled": synthetic is not None,
            "synthetic_hbm_bytes_per_cycle": 0 if synthetic is None else synthetic.bytes_per_cycle,
            "synthetic_hbm_queue_depth": 0 if synthetic is None else synthetic.queue_depth,
            "base_latencies": list(fabric.target.base_latency_cycles),
            **{
                "quota_" + dimension: [
                    getattr(quota[(source.src_node, source.src_port, target.dst_node)], dimension)
                    for source in fabric.initiators
                ]
                for dimension in ("read_contexts", "write_contexts", "read_beats", "write_beats")
            },
        })
    objects.extend(
        {"type": "GarnetNetworkInterface", "path": f"ni.{node}", "clk_domain": "clock",
         "vcs_per_vnet": fabric.network.vcs_per_vnet, "virt_nets": 5}
        for node in range(fabric.default_error_target_node + 1)
    )
    return {"system": {"mem_mode": "timing"}, "objects": objects}, {"arch_path": str(path)}


def test_materialization_derives_arch_and_program_without_mutating_frozen_profile(tmp_path):
    profile = json.loads((REPO / "configs/example/ai_mesh/experiments/fixed_profile.json").read_text())
    original = json.dumps(profile, sort_keys=True)
    work = WorkloadSpec("MIXED_1_1", bytes_per_core=4096, tile_bytes=1024,
                        hbm_port_bytes=profile["backend"]["port_bytes"])
    case, materialized = RUNNER.materialize(tmp_path / "a", profile, "H5", work, 1, 8, (1,) * 5)
    assert json.dumps(profile, sort_keys=True) == original
    persisted = load_arch(tmp_path / "a/arch.yaml")
    document = RUNNER.merge_document(
        RUNNER.read_yaml_document(REPO / profile["arch_template"]),
        profile["architecture_overrides"],
    )
    document["core"]["dma"]["read_outstanding"] = 1
    document["core"]["dma"]["write_outstanding"] = 8
    expected = resolve_experiment_architecture(
        load_arch_text(yaml.safe_dump(document, sort_keys=False)),
        Topology("H5"), profile, router_input_depths=(1,) * 5,
    )
    bundle = build_workload(expected, work)
    assert persisted == expected
    assert bundle.arch.digest() == bundle.program.arch_digest == persisted.digest()
    assert materialized.program.semantic_sha256 == bundle.program.semantic_sha256
    assert Path(case["program_dir"]) == tmp_path / "a/program"
    assert json.loads((tmp_path / "a/program/expected_traffic.json").read_bytes()) == bundle.program.semantics.intrinsic_traffic.canonical_dict()


def test_materialization_uses_profile_width_for_packet_and_route_oracles(tmp_path):
    profile = json.loads((REPO / "configs/example/ai_mesh/experiments/fixed_profile.json").read_text())
    profile["network"]["flit_bytes"] = 32
    work = WorkloadSpec("LOAD_ONLY", bytes_per_core=65536, active_cores=(0,),
                        hbm_port_bytes=profile["backend"]["port_bytes"])
    arch = resolve_experiment_architecture(
        load_arch(REPO / "configs/example/ai_mesh/arch/mesh_5x5.yaml"),
        Topology("H10_EAST2"), profile,
    )
    bundle = build_workload(arch, work)
    assert bundle.oracle["flits_per_packet"] == {"AW": 1, "W": 2, "B": 1, "AR": 1, "R": 2}
    assert bundle.oracle["directed_link_flits"]["endpoint:25->router:4"]["R"] == 4096
    assert bundle.oracle["directed_link_flits"]["router:4->router:3"]["R"] == 4096
    assert bundle.arch.fabric.network.flit_bytes == 32


def test_materialization_derives_write_routes_from_frozen_profile(tmp_path):
    profile = json.loads((REPO / "configs/example/ai_mesh/experiments/east_dual_hbm_32B_profile.json").read_text())
    profile["network"]["yx_vnets"] = [0, 1]
    work = WorkloadSpec("STORE_ONLY", bytes_per_core=65536, active_cores=(20,),
                        distribution="single_target", hotspot_target=0,
                        hbm_port_bytes=profile["backend"]["port_bytes"])
    arch = resolve_experiment_architecture(
        load_arch(REPO / "configs/example/ai_mesh/arch/mesh_5x5.yaml"),
        Topology("H10_EAST2"), profile,
    )
    bundle = build_workload(arch, work)
    assert bundle.oracle["directed_link_flits"]["router:20->router:15"]["W"] == 2048
    assert "router:24->router:19" not in bundle.oracle["directed_link_flits"]
    assert bundle.arch.fabric.network.yx_vnets == (0, 1)


def test_effective_configuration_matches_resolved_adapter_identities(effective_configuration):
    raw, case = effective_configuration
    assert verify_effective(raw, case) is True


@pytest.mark.parametrize("adapter_type,identity_fields", (
    ("AxiInitiatorAdapter", ("src_node", "src_port")),
    ("AxiTargetAdapter", ("dst_node",)),
))
def test_effective_configuration_rejects_duplicate_and_missing_adapter_identity(
        effective_configuration, adapter_type, identity_fields):
    raw, case = effective_configuration
    changed = copy.deepcopy(raw)
    adapters = [item for item in changed["objects"] if item["type"] == adapter_type]
    for field in identity_fields:
        adapters[1][field] = adapters[0][field]
    with pytest.raises(ValueError, match="identities"):
        verify_effective(changed, case)


def test_rerun_archives_prior_result_before_new_launch(tmp_path, monkeypatch):
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
    launches = []
    monkeypatch.setattr(RUNNER, "run_process", lambda *args, **kwargs: (
        launches.append(args) or {"argv": args[0], "returncode": 1, "timed_out": False, "host_seconds": .1}
    ))
    measured = RUNNER.execute_case(directory, profile=profile, topology="H5", workload=work,
                                   n_read=4, n_write=4, depths=(1,) * 5, gem5=executable,
                                   source={"digest": "a" * 64, "head": "test"}, build_digest=file_digest(executable), resume=True)
    assert measured["correctness"] == "failed"
    assert not stale.exists()
    assert (directory / "attempts/1/network_final.json").read_text() == '{"old": true}'
    assert len(launches) == 1
    assert (directory / "program/manifest.json").exists()


def test_failed_roi_probe_does_not_launch_measurement(tmp_path, monkeypatch):
    profile = json.loads((REPO / "configs/example/ai_mesh/experiments/fixed_profile.json").read_text())
    work = WorkloadSpec("LOAD_ONLY", bytes_per_core=4096, tile_bytes=1024,
                        hbm_port_bytes=profile["backend"]["port_bytes"])
    executable = tmp_path / "not-executable"
    executable.write_text("not a runnable file")
    directory = tmp_path / "cases" / "failed-probe"
    launches = []
    monkeypatch.setattr(RUNNER, "run_process", lambda *args, **kwargs: (
        launches.append(args) or {"argv": args[0], "returncode": 1, "timed_out": False, "host_seconds": .1}
    ))
    measured = RUNNER.execute_with_roi(directory, snapshot_roi=True, profile=profile,
                                       topology="H5", workload=work, n_read=4, n_write=4,
                                       depths=(1,) * 5, gem5=executable,
                                       source={"digest": "a" * 64, "head": "test"},
                                       build_digest=file_digest(executable))
    assert measured["correctness"] == "failed"
    assert measured["invalid_reasons"][-1] == "roi_probe_unavailable"
    assert len(launches) == 1
    assert (directory / "roi_probe/program/manifest.json").exists()
    assert not (directory / "program").exists()


def test_published_program_reaches_runtime_and_verification(tmp_path, monkeypatch):
    profile = json.loads((REPO / "configs/example/ai_mesh/experiments/fixed_profile.json").read_text())
    work = WorkloadSpec("LOAD_ONLY", bytes_per_core=4096, tile_bytes=1024,
                        hbm_port_bytes=profile["backend"]["port_bytes"])
    executable = tmp_path / "binary"
    executable.write_text("fixed binary identity")
    monkeypatch.setattr(RUNNER, "run_process", lambda *args, **kwargs: {
        "argv": args[0], "returncode": 0, "timed_out": False, "host_seconds": .1})
    verification = []
    monkeypatch.setattr(RUNNER, "verify_case", lambda directory: (
        verification.append(directory) or {"status": "valid", "correctness": "pass"}
    ))
    source = {"head": "test", "files": {"source": "a" * 64}}
    source["digest"] = RUNNER.canonical_digest(source["files"])
    directory = tmp_path / "case"
    measured = RUNNER.execute_case(directory, profile=profile, topology="H5", workload=work,
                                   n_read=4, n_write=4, depths=(1,) * 5, gem5=executable,
                                   source=source, build_digest=file_digest(executable))
    assert measured["correctness"] == "pass"
    assert verification == [directory.resolve()]
    assert json.loads((directory / "program/manifest.json").read_bytes())["status"] == "ok"


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
