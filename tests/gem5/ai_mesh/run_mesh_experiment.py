import argparse
import copy
import csv
import json
import os
import sys
import threading
import tempfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "util/mesh_ir"))

import yaml

from mesh_ir.acceptance import CHILD_ENV_BASE, atomic_write_bytes, canonical_digest, read_yaml_document
from mesh_ir.builder import load_arch
from mesh_ir.experiment.config import BASE_DEPTHS, BufferMap, Topology, Workload, WorkloadSpec
from mesh_ir.experiment.execution import ExecutionIdentity, artifact_hashes, file_digest, run_process, source_identity, verify_resume
from mesh_ir.experiment.metrics import COMMON_FIELDS, GROUP_FIELDS, analyze_candidates, common_hardware, eligible
from mesh_ir.experiment.plots import plot_results
from mesh_ir.experiment.search import FamilyCoverage, INITIAL_WINDOWS, STAGE_BUDGETS, active_channels, configuration_identity, coordinate_candidates, equal_capacity_exchanges, fair_candidates, long_validation_points, ranked_candidates, uniform_vectors
from mesh_ir.experiment.verify import load_json, verified_measurement, verify_case
from mesh_ir.experiment.workload import build_workload, packet_flits, validate_simulation_horizon, write_workload
from mesh_ir.model import canonical_json_bytes


DEFAULT_PROFILE = REPO / "configs/example/ai_mesh/experiments/fixed_profile.json"
CHILD_CONFIG = REPO / "configs/example/ai_mesh/run_mesh_experiment.py"


def merge_document(base, overrides):
    result = copy.deepcopy(base)
    for key, value in overrides.items():
        result[key] = merge_document(result.get(key, {}), value) if isinstance(value, dict) else copy.deepcopy(value)
    return result


def save_json(path, document):
    atomic_write_bytes(Path(path), canonical_json_bytes(document))


def invalidate_changed_source(row, observed_digest):
    directory = Path(row["directory"])
    changed = {**row, "status": "failed", "correctness": "failed",
               "invalid_reasons": [*row.get("invalid_reasons", []), "source_changed_during_execution"]}
    record = load_json(directory / "execution.json")
    record.update(verification="failed", source_changed=True, observed_source_digest=observed_digest)
    save_json(directory / "execution.json", record)
    save_json(directory / "measurement.json", changed)
    return changed


def materialize(directory, profile, topology, workload, n_read, n_write, depths, map_document=None, snapshot_ticks=(), roi=None):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if n_read not in profile["supported_n"] or n_write not in profile["supported_n"]:
        raise ValueError("outstanding unsupported by frozen resource profile")
    arch_document = merge_document(read_yaml_document(REPO / profile["arch_template"]), profile["architecture_overrides"])
    arch_document["core"]["dma"]["read_outstanding"] = n_read
    arch_document["core"]["dma"]["write_outstanding"] = n_write
    arch_path = directory / "arch.yaml"
    arch_path.write_text(yaml.safe_dump(arch_document, sort_keys=False), encoding="utf-8")
    arch = load_arch(arch_path)
    bundle = build_workload(arch, Topology(topology), workload,
                            flit_bytes=profile["network"]["flit_bytes"],
                            header_bytes=profile["axi"]["wire_header_bytes"],
                            data_header_sideband=profile["axi"].get("data_header_sideband", False),
                            yx_vnets=profile["network"].get("yx_vnets", ()))
    validate_simulation_horizon(bundle.oracle, profile["runtime"]["max_sim_ticks"], arch.clock_hz,
                                profile["measurement"]["ticks_per_second"])
    program_dir = directory / "program"
    write_workload(bundle, program_dir)
    overrides = BufferMap(tuple(tuple(row) for row in map_document["entries"])).overrides() if map_document else []
    case = {"schema_version": 1, "profile": profile, "topology": topology,
            "program_dir": str(program_dir), "arch_path": str(arch_path),
            "n_read": n_read, "n_write": n_write, "buffer_depths": list(depths),
            "router_input_vc_depths": overrides, "output_dir": str(directory),
            "snapshot_ticks": list(snapshot_ticks), "burst_beats": arch_document["axi"]["max_burst_beats"]}
    if roi is not None:
        case["roi"] = list(roi)
    save_json(directory / "case.json", case)
    return case, bundle


def execute_case(directory, *, profile, topology, workload, n_read, n_write, depths,
                 gem5, source, build_digest, map_document=None, timeout=None, resume=False,
                 snapshot_ticks=(), roi=None, case_id=None, stage="single", reason="requested", host_parallelism=1):
    directory = Path(directory).resolve()
    existing_record = load_json(directory / "execution.json") if (directory / "execution.json").exists() else None
    if existing_record is not None and not resume:
        raise ValueError(f"existing experiment requires --resume: {directory}")
    with tempfile.TemporaryDirectory(prefix="mesh-experiment-prepare-") as temporary:
        case, bundle = materialize(temporary, profile, topology, workload, n_read, n_write, depths,
                                    map_document, snapshot_ticks, roi)
        arch_bytes = Path(case["arch_path"]).read_bytes()
    case.update(program_dir=str(directory / "program"), arch_path=str(directory / "arch.yaml"),
                output_dir=str(directory))
    environment = {**CHILD_ENV_BASE, "PYTHONPATH": str(REPO / "util/mesh_ir"), "M5_OVERRIDE_PY_SOURCE": "false"}
    case["host_execution"] = {"timeout_seconds": timeout or profile["runtime"]["host_timeout_seconds"],
                               "parallelism": host_parallelism}
    identity = ExecutionIdentity.from_case(case, bundle.plan, bundle.oracle, source["digest"], build_digest, environment)
    case_id = case_id or identity.digest()[:16]
    axes = {"case_id": case_id, "stage": stage, "selection_reason": reason,
            "topology": topology, "profile_id": profile["profile_id"],
            "backend_id": profile["backend"]["backend_id"], "workload": workload.workload,
            "distribution": workload.distribution, "burst_beats": case["burst_beats"],
            "workload_digest": bundle.plan["workload_digest"],
            "source_digest": identity.source_digest, "build_digest": identity.build_digest,
            "profile_digest": identity.profile_digest,
            "comparison_scope": canonical_digest({key: value for key, value in workload.document().items() if key != "workload"}),
            "n_read": n_read, "n_write": n_write, "depths": list(depths),
            "map_digest": identity.map_digest, "directory": str(directory),
            "complexity": len(case["router_input_vc_depths"]), "identity": identity.document()}
    if resume and existing_record is not None and verify_resume(directory, existing_record, identity):
        verified = verified_measurement(directory, expected_identity=identity)
        measured = {**axes, **verified, "reused": True}
        save_json(directory / "measurement.json", measured)
        return measured
    if existing_record is not None:
        attempts = directory / "attempts"
        attempts.mkdir(exist_ok=True)
        archive = attempts / str(len(list(attempts.iterdir())) + 1)
        archive.mkdir()
        owned = {Path(name).parts[0] for name in existing_record["artifacts"]}
        owned.update(("execution.json", "measurement.json"))
        for name in sorted(owned - {"attempts", "roi_probe"}):
            path = directory / name
            if name in (".", "..") or not path.resolve().is_relative_to(directory):
                raise ValueError("invalid archived artifact path")
            if path.exists():
                path.rename(archive / name)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "arch.yaml").write_bytes(arch_bytes)
    write_workload(bundle, directory / "program")
    save_json(directory / "case.json", case)
    save_json(directory / "source_provenance.json", source)
    env = {**os.environ, **environment,
           "AI_MESH_ARTIFACT_DIR": str(directory)}
    argv = [str(Path(gem5).resolve()), f"--outdir={directory / 'raw'}", str(CHILD_CONFIG),
            "--experiment-config", str(directory / "case.json")]
    result = run_process(argv, cwd=REPO, log_path=directory / "run.log",
                         timeout=case["host_execution"]["timeout_seconds"], env=env)
    try:
        result["build_changed"] = file_digest(gem5) != build_digest
    except OSError as error:
        result.update(build_changed=True, build_error=str(error))
    result.update(identity=identity.document(), environment=environment, host_execution=case["host_execution"], source_changed=False,
                  source_head=source["head"], verification="failed", artifacts={})
    measured = {**axes, "status": "failed", "correctness": "failed", "invalid_reasons": [],
                "host_seconds": result["host_seconds"]}
    if result["returncode"] == 0 and not result["timed_out"] and not result.get("build_changed"):
        try:
            measured.update(verify_case(directory))
            result["verification"] = "pass"
        except (ValueError, KeyError, OSError, TypeError) as error:
            measured["invalid_reasons"] = [str(error)]
    else:
        measured["invalid_reasons"] = (["build_changed_during_execution"] if result.get("build_changed") else
                                       [result["launch_error"]] if result.get("launch_error") else
                                       ["host_timeout" if result["timed_out"] else f"exit_code:{result['returncode']}"])
    artifact_paths = [path for path in directory.rglob("*") if path.is_file() and "attempts" not in path.relative_to(directory).parts
                      and path.name not in ("execution.json", "measurement.json")]
    result["artifacts"] = artifact_hashes(directory, artifact_paths)
    save_json(directory / "execution.json", result)
    save_json(directory / "measurement.json", measured)
    return measured


def execute_with_roi(directory, *, snapshot_roi=False, **kwargs):
    if not snapshot_roi or kwargs.get("roi") is not None:
        return execute_case(directory, **kwargs)
    probe = execute_case(Path(directory) / "roi_probe", **kwargs)
    if probe["correctness"] != "pass" or "roi_begin_tick" not in probe:
        measured = {**probe, "invalid_reasons": [*probe.get("invalid_reasons", []), "roi_probe_unavailable"]}
        save_json(Path(directory) / "measurement.json", measured)
        return measured
    begin, end = probe["roi_begin_tick"], probe["roi_end_tick"]
    windows = kwargs["profile"]["measurement"]["subwindows"]
    boundaries = [begin + (end - begin) * index // windows for index in range(windows + 1)]
    return execute_case(directory, **{**kwargs, "roi": (begin, end), "snapshot_ticks": boundaries})


def analyze_directory(directory):
    directory = Path(directory)
    paths = sorted(directory.glob("cases/*/measurement.json"))
    if (directory / "measurement.json").exists():
        paths.append(directory / "measurement.json")
    rows = []
    for path in paths:
        cached = load_json(path)
        try:
            actual_directory = Path(cached["directory"]).resolve()
            if actual_directory not in (path.parent.resolve(), (path.parent / "roi_probe").resolve()):
                raise ValueError("measurement index refers to a different candidate directory")
            row = verified_measurement(Path(cached["directory"]))
        except (ValueError, KeyError, OSError, TypeError) as error:
            row = {**cached, "status": "failed", "correctness": "failed", "invalid_reasons": ["artifact_verification: " + str(error)]}
        rows.append(row)
    groups = {}
    for row in rows:
        key = tuple(row[field] for field in GROUP_FIELDS)
        groups.setdefault(key, []).append(row)
    recommendations = {}
    equal_capacity_controls = []
    for key, group in sorted(groups.items()):
        measured = [row for row in group if "router_buffer_map" in row]
        c0 = None
        if measured:
            baseline_case = load_json(Path(measured[0]["directory"]) / "case.json")
            ports = sorted({tuple(row[:2]) for row in measured[0]["router_buffer_map"]["entries"]})
            network = baseline_case["profile"]["network"]
            c0 = BufferMap.uniform(ports, network["baseline_depths"]).storage_bytes(
                network["flit_bytes"], network["vcs_per_vnet"])
        result = analyze_candidates(group, budgets=(() if c0 is None else (c0 // 2, c0, 2 * c0)))
        result["baseline_C0_bytes"] = c0
        result["channel_identifiability"] = {channel: "measured_active" if index in active_channels(group[0]["workload"])
                                             else "not_identifiable_in_this_workload"
                                             for index, channel in enumerate(("AW", "W", "B", "AR", "R"))}
        recommendations["/".join(map(str, key))] = result
        uniform = []
        heterogeneous = []
        for row in measured:
            if not eligible(row):
                continue
            entries = row["router_buffer_map"]["entries"]
            is_uniform = BufferMap(tuple(tuple(entry) for entry in entries)).uniform_depths() is not None
            (uniform if is_uniform else heterogeneous).append(row)
        for changed in heterogeneous:
            controls = [row for row in uniform if row["router_buffer_bytes"] == changed["router_buffer_bytes"]
                        and (row["n_read"], row["n_write"]) == (changed["n_read"], changed["n_write"])]
            if controls:
                control = ranked_candidates(controls)[0]
                equal_capacity_controls.append({"uniform_case": control["case_id"], "heterogeneous_case": changed["case_id"],
                                                 "storage_bytes": changed["router_buffer_bytes"],
                                                 "n_read": changed["n_read"], "n_write": changed["n_write"],
                                                 "bandwidth_ratio": changed["bandwidth_Bps"] / control["bandwidth_Bps"]})
    save_json(directory / "equal_capacity_controls.json", equal_capacity_controls)
    common = {}
    for key in sorted({tuple(row[field] for field in COMMON_FIELDS) for row in rows}):
        group = [row for row in rows if tuple(row[field] for field in COMMON_FIELDS) == key]
        common["/".join(map(str, key))] = common_hardware(group)
    selected_names = {value[name] for value in recommendations.values() for name in (
        "peak", "resource_recommendation", "outstanding_recommendation") if value[name] is not None}
    selected_names.update(case_id for group in common.values() for name in ("recommendation", "compromise")
                          if group[name] is not None for case_id in group[name]["cases"].values())
    instantiate = {}
    for row in rows:
        if row["case_id"] not in selected_names:
            continue
        target = directory / "recommended" / row["case_id"]
        target.mkdir(parents=True, exist_ok=True)
        original = load_json(Path(row["directory"]) / "case.json")
        workload = load_json(Path(original["program_dir"]) / "workload.json")["spec"]
        save_json(target / "buffer_map.json", row["router_buffer_map"])
        save_json(target / "profile.json", original["profile"])
        argv = [sys.executable, str(Path(__file__).resolve()), "run", "--topology", row["topology"],
                "--workload", row["workload"], "--bytes-per-core", str(workload["bytes_per_core"]),
                "--tile-bytes", str(workload["tile_bytes"]), "--n-read", str(row["n_read"]),
                "--n-write", str(row["n_write"]), "--buffer-map", str((target / "buffer_map.json").resolve()),
                "--profile", str((target / "profile.json").resolve()), "--distribution", workload["distribution"],
                "--hotspot-target", str(workload["hotspot_target"]), "--active-cores", *map(str, workload["active_cores"]),
                "--outdir", "<NEW_OUTPUT_DIRECTORY>", "--gem5", "<MATCHING_GEM5_BINARY>", "--snapshot-roi"]
        instantiate[row["case_id"]] = {"argv": argv, "map": str(target / "buffer_map.json"),
                                       "profile": str(target / "profile.json"), "identity": row["identity"]}
    save_json(directory / "sweep_summary.json", rows)
    coverage_path = directory / "family_coverage.json"
    if coverage_path.exists():
        coverage = FamilyCoverage(load_json(coverage_path)["families"])
        save_json(coverage_path, coverage.report(load_json(directory / "candidate_history.json"), rows))
    save_json(directory / "best_configs.json", {"groups": recommendations, "common_hardware": common,
                                               "instantiate": instantiate,
                                               "family_coverage": coverage_path.name if coverage_path.exists() else None})
    columns = ("case_id", "stage", "topology", "workload", "distribution", "status", "n_read", "n_write",
               "map_digest", "router_buffer_bytes", "read_Bps", "write_Bps", "bandwidth_Bps", "p99_ticks", "fairness")
    for name, selected in (("sweep_summary.csv", rows), ("pareto_frontier.csv", [row for row in rows if any(
            row["case_id"] in result["pareto"] for result in recommendations.values())])):
        with (directory / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(selected)
    for label in ("per_core", "per_core_resources", "per_hbm_core_completion", "per_hbm_backend", "per_link", "per_router_inport_vnet"):
        save_json(directory / (label + ".json"), {row["case_id"]: row.get(label, []) for row in rows})
    plot_results(directory / "plots", rows, recommendations)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("smoke", "run", "sweep", "resume", "verify", "analyze"))
    parser.add_argument("--outdir", required=True, type=Path)
    parser.add_argument("--gem5", type=Path, default=REPO / "build/AXI_MESH/gem5.opt")
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--topology", choices=tuple(Topology.layouts), default="H5")
    parser.add_argument("--topologies", nargs="+", choices=tuple(Topology.layouts), default=("H5", "H10"))
    parser.add_argument("--workload", choices=[value.value for value in Workload], default="LOAD_ONLY")
    parser.add_argument("--workloads", nargs="+", choices=[value.value for value in Workload], default=[value.value for value in Workload])
    parser.add_argument("--bytes-per-core", type=int, default=1048576)
    parser.add_argument("--validation-bytes-per-core", type=int)
    parser.add_argument("--tile-bytes", type=int, default=65536)
    parser.add_argument("--n", type=int, default=4)
    parser.add_argument("--n-read", type=int)
    parser.add_argument("--n-write", type=int)
    parser.add_argument("--windows", nargs="+", type=int, default=INITIAL_WINDOWS)
    parser.add_argument("--depths", nargs=5, type=int, default=BASE_DEPTHS, metavar=("AW", "W", "B", "AR", "R"))
    parser.add_argument("--buffer-map", type=Path)
    parser.add_argument("--distribution", choices=("uniform", "hotspot", "single_target"), default="uniform")
    parser.add_argument("--hotspot-target", type=int, default=0)
    parser.add_argument("--active-cores", nargs="+", type=int, default=list(range(25)))
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--parallelism", type=int, default=1)
    parser.add_argument("--max-cases", type=int, default=48)
    parser.add_argument("--stage-budgets", nargs="+", default=[f"{key}={value}" for key, value in STAGE_BUDGETS.items()])
    parser.add_argument("--stages", nargs="+", choices=("A", "B", "C", "D", "E", "F"), default=("A",))
    parser.add_argument("--snapshot-roi", action="store_true")
    parser.add_argument("--snapshot-ticks", nargs="*", type=int, default=[])
    parser.add_argument("--roi", nargs=2, type=int)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.command == "verify":
        measured = verify_case(args.outdir)
        print(json.dumps({"correctness": measured["correctness"], "status": measured["status"]}, sort_keys=True))
        return 0
    if args.command == "analyze":
        rows = analyze_directory(args.outdir)
        print(f"MESH_EXPERIMENT_ANALYZED cases={len(rows)}")
        return 0
    if args.parallelism < 1 or args.max_cases < 1:
        parser.error("parallelism and max-cases must be positive")
    profile = load_json(args.profile)
    flit_bytes, vcs = profile["network"]["flit_bytes"], profile["network"]["vcs_per_vnet"]
    if args.parallelism > profile["runtime"]["host_parallelism"]:
        parser.error("host parallelism exceeds the frozen profile cap")
    stage_budgets = dict(STAGE_BUDGETS)
    for field in args.stage_budgets:
        name, value = field.split("=")
        if name not in stage_budgets or int(value) < 0:
            parser.error("stage budgets require A..F=nonnegative integer")
        stage_budgets[name] = int(value)
    validation_bytes = args.validation_bytes_per_core if args.validation_bytes_per_core is not None else 2 * args.bytes_per_core
    if args.command in ("sweep", "resume") and "F" in args.stages:
        if validation_bytes <= args.bytes_per_core:
            parser.error("--validation-bytes-per-core must exceed --bytes-per-core")
        try:
            for kind in args.workloads:
                WorkloadSpec(kind, bytes_per_core=validation_bytes, tile_bytes=args.tile_bytes,
                             active_cores=tuple(args.active_cores), distribution=args.distribution,
                             hotspot_target=args.hotspot_target, hbm_port_bytes=profile["backend"]["port_bytes"])
        except ValueError as error:
            parser.error(f"invalid --validation-bytes-per-core: {error}")
    source = source_identity(REPO)
    build_digest = file_digest(args.gem5)
    args.outdir.mkdir(parents=True, exist_ok=True)
    if args.command not in ("smoke", "run"):
        save_json(args.outdir / "source_provenance.json", source)
    workload = WorkloadSpec(args.workload, bytes_per_core=args.bytes_per_core, tile_bytes=args.tile_bytes,
                            active_cores=tuple(args.active_cores), distribution=args.distribution,
                            hotspot_target=args.hotspot_target, hbm_port_bytes=profile["backend"]["port_bytes"])
    shared = {"profile": profile, "gem5": args.gem5, "source": source, "build_digest": build_digest,
              "timeout": args.timeout, "resume": args.resume or args.command == "resume"}
    shared["host_parallelism"] = args.parallelism
    if args.command in ("smoke", "run"):
        row = execute_with_roi(args.outdir, topology=args.topology, workload=workload,
                           n_read=args.n_read or args.n, n_write=args.n_write or args.n, depths=args.depths,
                           map_document=load_json(args.buffer_map) if args.buffer_map else None,
                           snapshot_ticks=args.snapshot_ticks, roi=args.roi, snapshot_roi=args.snapshot_roi, **shared)
        observed_source = source_identity(REPO)["digest"]
        if observed_source != source["digest"]:
            row = invalidate_changed_source(row, observed_source)
            save_json(args.outdir / "measurement.json", row)
        print(json.dumps({"case_id": row["case_id"], "status": row["status"],
                          "correctness": row["correctness"], "reasons": row.get("invalid_reasons", [])}, sort_keys=True))
        return 0 if row["correctness"] == "pass" else 1
    candidates, completed_rows = [], []
    coverage = FamilyCoverage()
    seen = {}
    scheduled = 0
    source_changed = False
    stage_scheduled = defaultdict(int)
    history_lock = threading.Lock()
    def save_progress():
        save_json(args.outdir / "candidate_history.json", candidates)
        save_json(args.outdir / "family_coverage.json", coverage.report(candidates))
    def run_candidate(candidate):
        candidate_profile = candidate.get("profile", profile)
        work = WorkloadSpec(candidate["workload"], bytes_per_core=candidate.get("bytes_per_core", args.bytes_per_core),
                            tile_bytes=args.tile_bytes, hbm_port_bytes=candidate_profile["backend"]["port_bytes"],
                            distribution=candidate.get("distribution", args.distribution),
                            hotspot_target=candidate.get("hotspot_target", args.hotspot_target),
                            active_cores=tuple(candidate.get("active_cores", args.active_cores)))
        with history_lock:
            candidate["status"] = "running"
            save_progress()
        print(json.dumps({"event": "submit", "case_id": candidate["case_id"],
                          "configuration_identity": configuration_identity(candidate),
                          "topology": candidate["topology"], "endpoints": Topology(candidate["topology"]).endpoints(),
                          "n_read": candidate["n"], "n_write": candidate["n"], "workload": work.document(),
                          "depths": candidate["depths"], "map": candidate.get("map"),
                          "profile_digest": canonical_digest(candidate_profile),
                          "source_digest": source["digest"], "build_digest": build_digest,
                          "snapshot_roi": args.snapshot_roi}, sort_keys=True), flush=True)
        row = execute_with_roi(args.outdir / "cases" / candidate["case_id"], topology=candidate["topology"],
                           workload=work, n_read=candidate["n"], n_write=candidate["n"], depths=candidate["depths"],
                           map_document=candidate.get("map"), snapshot_roi=args.snapshot_roi,
                           case_id=candidate["case_id"], stage=candidate["stage"], reason=candidate["reason"],
                           **{**shared, "profile": candidate_profile})
        with history_lock:
            candidate["status"] = row["status"]
            completed_rows.append(row)
            save_progress()
        print(f"{candidate['case_id']}: {row['status']} {row.get('bandwidth_Bps')}", flush=True)
        return row
    def execute_batch(batch, stage_limit=None, fair_fields=None):
        nonlocal scheduled, source_changed
        pending = []
        if fair_fields:
            batch = fair_candidates(batch, fair_fields)
        batch_scheduled = 0
        for candidate in batch:
            candidate.setdefault("distribution", args.distribution)
            candidate.setdefault("hotspot_target", args.hotspot_target)
            candidate.setdefault("bytes_per_core", args.bytes_per_core)
            identity = configuration_identity(candidate)
            if identity in seen:
                candidate.update(status="duplicate", candidate_kind="replication", duplicate_of=seen[identity])
            elif source_changed:
                candidate.update(status="not_run", budget_reason="frozen_source_changed")
            elif candidate["n"] not in profile["supported_n"]:
                candidate["status"] = "unsupported"
            elif stage_scheduled[candidate["stage"]] >= stage_budgets[candidate["stage"]] or (
                    stage_limit is not None and batch_scheduled >= stage_limit):
                candidate.update(status="not_run", budget_reason="reserved_stage_budget")
            elif scheduled >= args.max_cases:
                candidate.update(status="not_run", budget_reason="total_case_budget")
            else:
                candidate["status"] = "pending"
                candidate["candidate_kind"] = "variant"
                seen[identity] = candidate["case_id"]
                scheduled += 1
                stage_scheduled[candidate["stage"]] += 1
                batch_scheduled += 1
                pending.append(candidate)
            candidates.append(candidate)
        save_progress()
        with ThreadPoolExecutor(max_workers=args.parallelism) as pool:
            batch_rows = list(pool.map(run_candidate, pending))
        observed_source = source_identity(REPO)["digest"]
        if pending and observed_source != source["digest"]:
            source_changed = True
            affected = {row["case_id"]: invalidate_changed_source(row, observed_source) for row in batch_rows}
            completed_rows[:] = [affected.get(row["case_id"], row) for row in completed_rows]
            for candidate in pending:
                candidate["status"] = "failed"
            save_progress()
        completed_rows.sort(key=lambda row: row["case_id"])
    def group_rows(topology, kind):
        return [row for row in completed_rows if row["topology"] == topology and row["workload"] == kind
                and row["distribution"] == args.distribution and eligible(row)]
    if "A" in args.stages:
        execute_batch([{"case_id": f"A_{topology}_{kind}_N{n}", "stage": "A", "topology": topology,
                        "workload": kind, "n": n, "depths": list(args.depths), "reason": "baseline_outstanding",
                        "family_ids": [coverage.request("A", topology, kind, f"baseline_N{n}")]}
                       for kind in args.workloads for topology in args.topologies for n in args.windows],
                      fair_fields=("topology", "workload"))
    if "B" in args.stages:
        batch = []
        for kind in args.workloads:
            for topology in args.topologies:
                rows = group_rows(topology, kind)
                n = args.n
                if rows:
                    chosen = analyze_candidates(rows)["resource_recommendation"]
                    n = next(row["n_read"] for row in rows if row["case_id"] == chosen)
                packing = packet_flits(profile["axi"]["wire_header_bytes"], 32, profile["network"]["flit_bytes"],
                                       profile["axi"].get("data_header_sideband", False))
                packet_depths = tuple(packing[channel] for channel in ("AW", "W", "B", "AR", "R"))
                for index, depths in enumerate(uniform_vectors(kind, args.depths, packet_depths)):
                    windows = sorted({n, min(max(profile["supported_n"]), n * 2)}) if index < 4 else [n]
                    for window in windows:
                        batch.append({"case_id": f"B_{topology}_{kind}_N{window}_V{index}", "stage": "B",
                                      "topology": topology, "workload": kind, "n": window, "depths": list(depths),
                                      "reason": "baseline_knee_and_higher_independent_channel_vector",
                                      "family_ids": [coverage.request("B", topology, kind, f"uniform_V{index}_N{window}")]})
        execute_batch(batch, fair_fields=("topology", "workload"))
    hotspot_groups = {}
    for kind in args.workloads:
        for topology in args.topologies:
            rows = group_rows(topology, kind)
            if not rows:
                continue
            pilot = min(rows, key=lambda row: (-row["n_read"], -row["bandwidth_Bps"], row["case_id"]))
            pressure = defaultdict(float)
            for port in pilot["per_router_inport_vnet"]:
                pressure[port["router_id"]] += (port["credit_stalls"] + port["no_vc_stalls"] + port["sa_lost"] +
                                               (port["full_fraction"] or 0) * sum(port["histogram"]))
            attachment = set(Topology(topology).hbm_routers)
            ranked = sorted((router for router in range(25) if router not in attachment and pressure[router] > 0),
                            key=lambda router: (-pressure[router], router))
            hot = set(ranked[:max(1, len(ranked) // 5)])
            edge = {router for router in range(25) if router % 5 in (0, 4) or router // 5 in (0, 4)}
            regions = {"HBM_ATTACH": attachment, "HOT_NON_HBM": hot,
                       "OTHER_EDGE": edge - attachment - hot,
                       "OTHER_INTERIOR": set(range(25)) - edge - attachment - hot}
            ports = {tuple(row[:2]) for row in pilot["router_buffer_map"]["entries"]}
            groups = {name: {port for port in ports if port[0] in routers} for name, routers in regions.items()}
            document = {"pilot_case_id": pilot["case_id"], "rule": "rank_actual_allocator_probe_failures_and_full_vc_time_top_fifth_non_hbm",
                        "pressure": dict(pressure), "regions": {name: sorted(routers) for name, routers in regions.items()},
                        "ports": {name: sorted(ports) for name, ports in groups.items()},
                        "router_tags": Topology(topology).router_tags(hot), "hot_label_scope": "ranked_non_hbm"}
            document["map_digest"] = canonical_digest(document)
            save_json(args.outdir / f"hotspots_{topology}_{kind}.json", document)
            hotspot_groups[topology, kind] = groups
    for round_index in range(2):
        if "C" in args.stages:
            batch = []
            relation = "same_capacity" if round_index == 0 else "add_capacity"
            families = {(topology, kind, seed_index): coverage.request("C", topology, kind, f"{relation}_seed{seed_index}")
                        for kind in args.workloads for topology in args.topologies for seed_index in range(2)}
            for (topology, kind, seed_index), family in families.items():
                if (topology, kind) not in hotspot_groups:
                    coverage.unavailable(family, "no_eligible_seed")
            for (topology, kind), groups in hotspot_groups.items():
                rows = group_rows(topology, kind)
                seeds = []
                for row in ranked_candidates(rows):
                    ports = sorted({tuple(entry[:2]) for entry in row["router_buffer_map"]["entries"]})
                    c0 = BufferMap.uniform(ports, profile["network"]["baseline_depths"]).storage_bytes(flit_bytes, vcs)
                    if round_index == 1 and row["router_buffer_bytes"] >= 2 * c0:
                        continue
                    if row["map_digest"] not in {seed["map_digest"] for seed in seeds}:
                        seeds.append(row)
                    if len(seeds) == 2:
                        break
                for seed_index in range(len(seeds), 2):
                    coverage.unavailable(families[topology, kind, seed_index], "no_eligible_capacity_seed")
                for seed_index, seed in enumerate(seeds):
                    family = families[topology, kind, seed_index]
                    baseline = BufferMap(tuple(tuple(row) for row in seed["router_buffer_map"]["entries"]))
                    ports = sorted({row[:2] for row in baseline.entries})
                    c0 = BufferMap.uniform(ports, profile["network"]["baseline_depths"]).storage_bytes(flit_bytes, vcs)
                    variants = equal_capacity_exchanges(baseline, groups, channels=active_channels(kind)) if round_index == 0 else [
                        candidate for candidate in coordinate_candidates(baseline, groups, 2 * c0, channels=active_channels(kind),
                                                                           flit_bytes=flit_bytes, vcs=vcs)
                        if candidate["map"].slots() > baseline.slots()]
                    if not variants:
                        coverage.unavailable(family, "no_legal_variant")
                    for index, variant in enumerate(variants):
                        batch.append({"case_id": f"C{round_index}_{topology}_{kind}_S{seed_index}_{index}", "stage": "C",
                                      "topology": topology, "workload": kind, "n": seed["n_read"], "depths": seed["depths"],
                                      "map": {"entries": variant["map"].entries}, "reason": variant["reason"],
                                      "seed_case_id": seed["case_id"], "capacity_relation": relation, "family_ids": [family]})
            execute_batch(batch, stage_limit=(stage_budgets["C"] + (1 - round_index)) // 2,
                          fair_fields=("topology", "workload", "seed_case_id"))
        if "D" in args.stages and round_index == 0:
            batch = []
            for topology in args.topologies:
                for kind in args.workloads:
                    family = coverage.request("D", topology, kind, "equal_capacity_uniform_control")
                    rows = [row for row in group_rows(topology, kind) if BufferMap(
                        tuple(tuple(entry) for entry in row["router_buffer_map"]["entries"])).uniform_depths() is not None]
                    if not rows:
                        coverage.unavailable(family, "missing_uniform_control")
                        continue
                    seed = ranked_candidates(rows)[0]
                    baseline = BufferMap(tuple(tuple(row) for row in seed["router_buffer_map"]["entries"]))
                    ranked = sorted(seed["per_router_inport_vnet"], key=lambda row: (
                        -row["credit_stalls"], -row["sa_lost"], row["router_id"], row["inport_id"], row["vnet"]))
                    ports = list(dict.fromkeys((row["router_id"], row["inport_id"]) for row in ranked))[:4]
                    groups = {f"port_{router}_{port}": {(router, port)} for router, port in ports}
                    variants = equal_capacity_exchanges(baseline, groups, channels=active_channels(kind))
                    if not variants:
                        coverage.unavailable(family, "no_legal_variant")
                    for index, variant in enumerate(variants):
                        batch.append({"case_id": f"D_{topology}_{kind}_{index}", "stage": "D", "topology": topology,
                                      "workload": kind, "n": seed["n_read"], "depths": seed["depths"],
                                      "map": {"entries": variant["map"].entries}, "reason": variant["reason"],
                                      "seed_case_id": seed["case_id"], "capacity_relation": "same_capacity_uniform_control",
                                      "family_ids": [family]})
            execute_batch(batch, fair_fields=("topology", "workload"))
        if "E" in args.stages:
            batch = []
            for topology in args.topologies:
                for kind in args.workloads:
                    family = coverage.request("E", topology, kind, f"outstanding_resweep_round{round_index}")
                    rows = group_rows(topology, kind)
                    if not rows:
                        coverage.unavailable(family, "no_eligible_seed")
                        continue
                    frontier = set(analyze_candidates(rows)["pareto"])
                    seeds = ranked_candidates(row for row in rows if row["case_id"] in frontier)[:4]
                    for index, seed in enumerate(seeds):
                        for n in sorted({max(1, seed["n_read"] // 2), seed["n_read"], min(128, seed["n_read"] * 2)}):
                            batch.append({"case_id": f"E{round_index}_{topology}_{kind}_S{index}_N{n}", "stage": "E",
                                          "topology": topology, "workload": kind, "n": n, "depths": seed["depths"],
                                          "map": seed["router_buffer_map"], "reason": "outstanding_resweep_after_buffer_search",
                                          "family_ids": [family]})
            execute_batch(batch, stage_limit=(stage_budgets["E"] + (1 - round_index)) // 2,
                          fair_fields=("topology", "workload"))
    if "F" in args.stages:
        common_batch, hotspot_batch, long_batch, diagnostic_batch = [], [], [], []
        for topology in args.topologies:
            families = {(kind, name): coverage.request("F", topology, kind, name)
                        for kind in args.workloads for name in ("common_hardware", "hotspot_0",
                            f"hotspot_{len(Topology(topology).hbm_routers) - 1}", "long_recommendation", "long_peak", "long_neighbor")}
            for name in ("near", "far"):
                families["LOAD_ONLY", name] = coverage.request("F", topology, "LOAD_ONLY", name)
            if topology == "H10":
                for kind in ("LOAD_ONLY", "MIXED_1_1"):
                    families[kind, "half_backend"] = coverage.request("F", topology, kind, "half_backend")
            rows = [row for row in completed_rows if row["topology"] == topology and eligible(row)]
            if not rows:
                for family in families.values():
                    coverage.unavailable(family, "no_eligible_seed")
                continue
            common = common_hardware(rows)
            selection = common["recommendation"] or common["compromise"]
            if selection:
                seed = next(row for row in rows if row["case_id"] in selection["cases"].values())
            else:
                seed = ranked_candidates(rows)[0]
            for kind in args.workloads:
                for location in (None, 0, len(Topology(topology).hbm_routers) - 1):
                    candidate = {"case_id": f"F_{topology}_{kind}_H{location}", "stage": "F",
                                 "topology": topology, "workload": kind, "n": seed["n_read"], "depths": seed["depths"],
                                 "map": seed["router_buffer_map"], "distribution": "uniform" if location is None else "hotspot",
                                 "hotspot_target": location or 0,
                                 "reason": "common_static_hardware_and_unretrained_hotspot_holdout",
                                 "family_ids": [families[kind, "common_hardware" if location is None else f"hotspot_{location}"]]}
                    (common_batch if location is None else hotspot_batch).append(candidate)
                group = [row for row in rows if row["workload"] == kind]
                if group:
                    picks = analyze_candidates(group)
                    validations = long_validation_points(group, picks)
                    if not any("neighbor" in row["validation_roles"] for row in validations):
                        coverage.unavailable(families[kind, "long_neighbor"], "no_neighbor")
                    for index, validation in enumerate(validations):
                        point = validation["point"]
                        long_batch.append({"case_id": f"F_long_{topology}_{kind}_{index}", "stage": "F",
                                           "topology": topology, "workload": kind, "n": point["n_read"], "depths": point["depths"],
                                           "map": point["router_buffer_map"], "bytes_per_core": validation_bytes,
                                           "reason": "longer_workload_recommendation_peak_or_neighbor_validation",
                                           "validation_of": point["case_id"], "validation_roles": validation["validation_roles"],
                                           "neighbor_of": validation.get("neighbor_of"),
                                           "family_ids": [families[kind, "long_" + role] for role in validation["validation_roles"]]})
                else:
                    for role in ("recommendation", "peak", "neighbor"):
                        coverage.unavailable(families[kind, "long_" + role], "no_eligible_seed")
            for label, core in zip(("near", "far"), Topology(topology).diagnostic_cores()):
                diagnostic_batch.append({"case_id": f"F_diagnostic_{topology}_{label}", "stage": "F",
                                          "topology": topology, "workload": "LOAD_ONLY", "n": seed["n_read"],
                                          "depths": seed["depths"], "map": seed["router_buffer_map"], "active_cores": [core],
                                          "distribution": "single_target", "hotspot_target": 0,
                                          "reason": "single_active_dma_fixed_target_near_far_static_hardware",
                                          "family_ids": [families["LOAD_ONLY", label]]})
            if topology == "H10":
                half_profile = copy.deepcopy(profile)
                half_profile["profile_id"] += "_half_backend_rate"
                half_profile["backend"]["backend_id"] += "_half_rate"
                half_profile["backend"]["bytes_per_cycle"] //= 2
                for kind in ("LOAD_ONLY", "MIXED_1_1"):
                    diagnostic_batch.append({"case_id": f"F_diagnostic_H10_half_backend_{kind}", "stage": "F",
                                              "topology": topology, "workload": kind, "n": seed["n_read"], "depths": seed["depths"],
                                              "map": seed["router_buffer_map"], "profile": half_profile,
                                              "reason": "H10_half_per_port_backend_rate_matches_H5_total_service",
                                              "family_ids": [families[kind, "half_backend"]]})
        execute_batch(fair_candidates(diagnostic_batch) + fair_candidates(common_batch) +
                      fair_candidates(hotspot_batch) + fair_candidates(long_batch))
    save_progress()
    rows = analyze_directory(args.outdir)
    print(f"MESH_EXPERIMENT_SWEEP cases={len(rows)} not_run={sum(row['status'] == 'not_run' for row in candidates)}")
    return 1 if any(row["correctness"] != "pass" for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
