#!/usr/bin/env python3
"""Run and analyze the dual-core 2x2 CHI public-proxy experiment.

The workloads are public-source proxies.  Nothing produced by this script is
a SPEC CPU2006 score or a SPEC-compliant run.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
DEPENDENCIES = Path(
    "/home/makenma/project/xs-gem5/workloads/dual-core-se-dependencies.json")
CONFIG = ROOT / "configs/example/kmhv2_chi_2x2_hnf_se.py"
GEM5 = ROOT / "build/RISCV/gem5.opt"
POLICIES = ("lru", "random", "srrip")
CLASSIFICATION = "public-source proxy; not SPEC CPU2006"
HNF = "system.home_node.slcsf.stats."


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")


def validate_dependencies() -> dict:
    manifest = json.loads(DEPENDENCIES.read_text(encoding="utf-8"))
    if manifest.get("workload_classification") != CLASSIFICATION:
        raise RuntimeError("dependency classification is not the proxy label")
    if len(manifest.get("processes", [])) != 2:
        raise RuntimeError("dependency manifest must describe exactly two CPUs")
    for process in manifest["processes"]:
        elf = Path(process["elf"])
        cwd = Path(process["working_directory"])
        if not elf.is_file() or not cwd.is_dir():
            raise RuntimeError(f"missing workload input for CPU{process['cpu_id']}")
        if sha256(elf) != process["elf_sha256"]:
            raise RuntimeError(f"ELF checksum mismatch: {elf}")
    ini = Path(manifest["processes"][1]["argv"][2])
    if sha256(ini) != manifest["processes"][1]["input_sha256"]:
        raise RuntimeError(f"input checksum mismatch: {ini}")
    if not GEM5.is_file() or not CONFIG.is_file():
        raise RuntimeError("gem5 binary or dedicated config is missing")
    return manifest


def common_args(dependencies: dict, memory: str) -> list[str]:
    processes = dependencies["processes"]
    return [
        str(CONFIG), "-n", "2",
        "-c", ";".join(process["elf"] for process in processes),
        "--options", ";".join(
            " ".join(process["argv"][1:]) for process in processes),
        "--workload-cwds", ";".join(
            process["working_directory"] for process in processes),
        f"--mem-size={memory}",
    ]


def run_command(command: list[str], outdir: Path) -> dict:
    outdir.mkdir(parents=True, exist_ok=True)
    stdout_path = outdir / "stdout.log"
    stderr_path = outdir / "stderr.log"
    write_json(outdir / "command.json", {
        "classification": CLASSIFICATION,
        "argv": command,
        "cwd": str(ROOT),
    })
    print(f"[run] {outdir}", flush=True)
    started = time.time()
    with stdout_path.open("w", encoding="utf-8") as stdout, \
            stderr_path.open("w", encoding="utf-8") as stderr:
        completed = subprocess.run(
            command, cwd=ROOT, stdout=stdout, stderr=stderr, check=False)
    record = {
        "return_code": completed.returncode,
        "host_elapsed_seconds": time.time() - started,
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
    }
    write_json(outdir / "run_status.json", record)
    if completed.returncode:
        raise RuntimeError(
            f"gem5 failed in {outdir} with rc={completed.returncode}")
    if not (outdir / "phase_metadata.json").is_file():
        raise RuntimeError(f"missing phase metadata in {outdir}")
    return record


def run_experiment(args) -> None:
    dependencies = validate_dependencies()
    result = args.result.resolve()
    result.mkdir(parents=True, exist_ok=True)
    shutil.copy2(DEPENDENCIES, result / "dual-core-se-dependencies.json")
    checkpoint = result / "warmup/common_full_cpt"
    common = common_args(dependencies, args.memory)
    runs = {}
    checkpoint_source = None
    if args.checkpoint_source is not None:
        if not args.skip_warmup:
            raise RuntimeError(
                "--checkpoint-source requires --skip-warmup")
        checkpoint_source = args.checkpoint_source.resolve()
        if not checkpoint_source.is_dir():
            raise RuntimeError(
                f"checkpoint source is missing: {checkpoint_source}")
        if checkpoint.exists():
            raise RuntimeError(
                f"checkpoint destination already exists: {checkpoint}")
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(checkpoint_source, checkpoint)
    if not args.skip_warmup:
        warmup = [
            str(GEM5), "-d", str(result / "warmup"), *common,
            "--experiment-mode=warmup",
            "--slc-replacement-policy=lru",
            f"--slc-replacement-seed={args.seed}",
            f"--fill-max-ticks={args.fill_max_ticks}",
            f"--settle-ticks={args.settle_ticks}",
            f"--checkpoint-full-retry-limit={args.checkpoint_full_retry_limit}",
            f"--checkpoint-refill-max-ticks={args.checkpoint_refill_max_ticks}",
            f"--common-checkpoint={checkpoint}",
        ]
        runs["warmup"] = run_command(warmup, result / "warmup")
    if not checkpoint.is_dir():
        raise RuntimeError(f"common checkpoint is missing: {checkpoint}")

    for policy in POLICIES:
        outdir = result / "runs" / policy
        command = [
            str(GEM5), "-d", str(outdir), *common,
            "--experiment-mode=roi",
            f"--slc-replacement-policy={policy}",
            f"--slc-replacement-seed={args.seed}",
            f"--roi-ticks={args.roi_ticks}",
            f"--min-roi-insts-per-core={args.min_roi_insts_per_core}",
            f"--common-checkpoint={checkpoint}",
            "--enable-rnf-transaction-latency",
            "--enable-guest-stack-profile",
            ("--guest-stack-sample-period-insts="
             f"{args.guest_stack_sample_period_insts}"),
        ]
        runs[policy] = run_command(command, outdir)

    git_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        capture_output=True, check=True).stdout.strip()
    git_status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, text=True,
        capture_output=True, check=True).stdout.splitlines()
    git_diff = subprocess.run(
        ["git", "diff", "--binary", "HEAD"], cwd=ROOT,
        capture_output=True, check=True).stdout
    checkpoint_files = {
        path.name: {"size_bytes": path.stat().st_size, "sha256": sha256(path)}
        for path in sorted(checkpoint.iterdir()) if path.is_file()
    }
    manifest = {
        "schema_version": 1,
        "classification": CLASSIFICATION,
        "warning": "These results are not SPEC CPU2006 scores.",
        "git_commit": git_commit,
        "git_dirty": bool(git_status),
        "git_status_porcelain": git_status,
        "git_diff_sha256": hashlib.sha256(git_diff).hexdigest(),
        "gem5_sha256": sha256(GEM5),
        "config": str(CONFIG),
        "config_sha256": sha256(CONFIG),
        "runner": str(Path(__file__).resolve()),
        "runner_sha256": sha256(Path(__file__).resolve()),
        "dependency_manifest": str(DEPENDENCIES),
        "dependency_manifest_sha256": sha256(DEPENDENCIES),
        "memory": args.memory,
        "policies": list(POLICIES),
        "seed": args.seed,
        "fill_max_ticks": args.fill_max_ticks,
        "settle_ticks": args.settle_ticks,
        "checkpoint_full_retry_limit": args.checkpoint_full_retry_limit,
        "checkpoint_refill_max_ticks": args.checkpoint_refill_max_ticks,
        "roi_ticks": args.roi_ticks,
        "min_roi_insts_per_core": args.min_roi_insts_per_core,
        "instrumentation": {
            "rnf_transaction_latency": {
                "enabled": True,
                "boundary": "ROI only",
                "identity": "exact CHI SrcID + TxnID",
                "semantic_timing_effect": False,
            },
            "guest_stack_profile": {
                "enabled": True,
                "boundary": "ROI only",
                "sample_period_insts": args.guest_stack_sample_period_insts,
                "semantic_timing_effect": False,
            },
        },
        "common_checkpoint": str(checkpoint),
        "common_checkpoint_source": (
            str(checkpoint_source) if checkpoint_source else None),
        "common_checkpoint_files": checkpoint_files,
        "replacement_policy_details": {
            "lru": "true LRU; hit updates recency; oldest valid line wins",
            "random": "fixed-seed uniform choice among valid ways in a full set",
            "srrip": "deterministic 2-bit SRRIP; max=3, hit=0, insert=2",
        },
        "runs": runs,
    }
    write_json(result / "manifest.json", manifest)
    analyze(result)


def parse_stat_sections(path: Path) -> list[dict[str, float]]:
    sections: list[dict[str, float]] = []
    current = None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if raw.startswith("---------- Begin Simulation Statistics"):
            current = {}
            continue
        if raw.startswith("---------- End Simulation Statistics"):
            if current is not None:
                sections.append(current)
            current = None
            continue
        if current is None or not raw or raw.startswith("#"):
            continue
        fields = raw.split("#", 1)[0].split()
        if len(fields) < 2:
            continue
        try:
            value = float(fields[1])
        except ValueError:
            continue
        current[fields[0]] = value
    if not sections:
        raise RuntimeError(f"no statistics sections in {path}")
    return sections


def metric(stats: dict[str, float], name: str, default=0.0) -> float:
    return stats.get(name, default)


def sum_matching(stats: dict[str, float], pattern: str) -> float:
    regex = re.compile(pattern)
    return sum(value for name, value in stats.items() if regex.fullmatch(name))


def vector(stats: dict[str, float], prefix: str) -> dict[str, int]:
    return {
        name[len(prefix):]: int(value)
        for name, value in stats.items() if name.startswith(prefix)
    }


def sparse_mean(stats: dict[str, float], prefix: str) -> float:
    total = 0.0
    samples = 0.0
    for name, value in stats.items():
        if not name.startswith(prefix + "::"):
            continue
        bucket = name[len(prefix) + 2:]
        if bucket.isdigit():
            total += int(bucket) * value
            samples += value
    return total / samples if samples else 0.0


def policy_metrics(policy: str, outdir: Path) -> dict:
    sections = parse_stat_sections(outdir / "stats.txt")
    stats = sections[0]
    phase = json.loads((outdir / "phase_metadata.json").read_text())
    cores = []
    for cpu in range(2):
        prefix = f"system.cpu{cpu}"
        insts = int(metric(stats, prefix + ".committedInsts"))
        cycles = int(metric(stats, prefix + ".numCycles"))
        l1_misses = int(
            metric(stats, prefix + ".icache.demandMisses::total") +
            metric(stats, prefix + ".dcache.demandMisses::total"))
        l2_misses = int(sum_matching(
            stats,
            rf"system\.l2_wrappers{cpu}\.slices[0-3]\.inner_cache\."
            r"demandMisses::total"))
        stalled_cycles = int(metric(
            stats, prefix + ".scheduler.exec_stall_cycle"))
        cores.append({
            "cpu": cpu,
            "workload": "libquantum proxy" if cpu == 0 else "OMNeT++ proxy",
            "instructions": insts,
            "cycles": cycles,
            "ipc": insts / cycles if cycles else 0.0,
            "cpi": cycles / insts if insts else math.inf,
            "l1_demand_misses": l1_misses,
            "l1_demand_mpki": l1_misses * 1000.0 / insts if insts else 0.0,
            "l2_demand_misses": l2_misses,
            "l2_demand_mpki": l2_misses * 1000.0 / insts if insts else 0.0,
            "memory_requests": l2_misses,
            "memory_requests_per_ki": l2_misses * 1000.0 / insts
            if insts else 0.0,
            "memory_request_definition": "sum of private L2 demand misses",
            "stalled_cycles": stalled_cycles,
            "memory_stall_cycles": int(metric(
                stats, prefix + ".scheduler.memstall_any_load")),
            "iew_stall_events": int(metric(
                stats, prefix + ".iew.stallEvents::total")),
        })

    slc = {
        name: metric(stats, HNF + name) for name in (
            "slcValidLines", "slcPeakValidLines", "slcCapacityLines",
            "slcOccupancyPercent", "maxValidWaysPerSet", "fullSlcCycles",
            "replacementAttempts", "cleanSlcVictims", "dirtySlcVictims",
            "totalSlcVictims", "sfVictims", "lookupOperations",
            "missMissLookups",
            "slcHitLookups", "sfHitLookups", "slcSfHitLookups",
            "directedSnoops", "broadcastSnoops", "serviceStalls",
            "staleTokenReplays", "resourceConflictReplays",
            "seqConflictReplays", "victimBufferFullReplays",
            "cancelledReplays", "requestFullCycles", "responseFullCycles",
            "noCredit", "setLockConflicts")
    }
    slc["totalReplayResponses"] = sum(
        slc[name] for name in (
            "staleTokenReplays", "resourceConflictReplays",
            "seqConflictReplays", "victimBufferFullReplays",
            "cancelledReplays"))
    completed = sum(slc[name] for name in (
        "missMissLookups", "slcHitLookups", "sfHitLookups",
        "slcSfHitLookups"))
    slc["slc_hits"] = slc["slcHitLookups"] + slc["slcSfHitLookups"]
    slc["slc_misses"] = slc["missMissLookups"] + slc["sfHitLookups"]
    slc["sf_hits"] = slc["sfHitLookups"] + slc["slcSfHitLookups"]
    slc["sf_misses"] = slc["missMissLookups"] + slc["slcHitLookups"]
    slc["completed_lookups"] = completed
    slc["slc_hit_rate"] = slc["slc_hits"] / completed if completed else 0.0
    slc["sf_hit_rate"] = slc["sf_hits"] / completed if completed else 0.0
    slc["accepted_to_visible_latency_cycles"] = sparse_mean(
        stats, HNF + "acceptedToVisibleLatency")

    routers = []
    for router in range(4):
        prefix = f"system.chi_routers{router}.trafficStats."
        row = {
            "router": router,
            "x": router % 2,
            "y": router // 2,
            "local_injected_flits": int(metric(
                stats, prefix + "localInjectedFlits::total")),
            "local_delivered_flits": int(metric(
                stats, prefix + "localDeliveredFlits::total")),
            "internal_received_flits": int(metric(
                stats, prefix + "internalReceivedFlits::total")),
            "internal_sent_flits": int(metric(
                stats, prefix + "internalSentFlits::total")),
            "internal_output_stall_cycles": int(metric(
                stats, prefix + "internalOutputStallCycles::total")),
            "local_injection_stall_cycles": int(metric(
                stats, prefix + "localInjectionStallCycles::total")),
            "local_delivery_stall_cycles": int(metric(
                stats, prefix + "localDeliveryStallCycles::total")),
        }
        row["traffic_score"] = (
            row["local_injected_flits"] + row["local_delivered_flits"] +
            row["internal_received_flits"] + row["internal_sent_flits"])
        routers.append(row)

    aggregate_insts = sum(core["instructions"] for core in cores)
    aggregate_cycles = max(core["cycles"] for core in cores)
    total_victims = slc["totalSlcVictims"]
    slc["victim_rate_per_completed_lookup"] = (
        total_victims / completed if completed else 0.0)
    slc["victims_per_ki"] = (
        total_victims * 1000.0 / aggregate_insts if aggregate_insts else 0.0)
    slc["replays_per_ki"] = (
        slc["totalReplayResponses"] * 1000.0 / aggregate_insts
        if aggregate_insts else 0.0)
    slc["replays_per_1000_completed_lookups"] = (
        slc["totalReplayResponses"] * 1000.0 / completed
        if completed else 0.0)
    slc["service_stalls_per_ki"] = (
        slc["serviceStalls"] * 1000.0 / aggregate_insts
        if aggregate_insts else 0.0)
    slc["service_stalls_per_1000_completed_lookups"] = (
        slc["serviceStalls"] * 1000.0 / completed
        if completed else 0.0)
    slc["no_credit_per_1000_completed_lookups"] = (
        slc["noCredit"] * 1000.0 / completed if completed else 0.0)
    noc = {
        "total_traffic_score": sum(row["traffic_score"] for row in routers),
        "internal_output_stall_cycles": sum(
            row["internal_output_stall_cycles"] for row in routers),
        "local_injection_stall_cycles": sum(
            row["local_injection_stall_cycles"] for row in routers),
        "local_delivery_stall_cycles": sum(
            row["local_delivery_stall_cycles"] for row in routers),
    }
    return {
        "classification": CLASSIFICATION,
        "policy": policy,
        "phase": phase,
        "cores": cores,
        "aggregate": {
            "instructions": aggregate_insts,
            "cycles": aggregate_cycles,
            "ipc": aggregate_insts / aggregate_cycles
            if aggregate_cycles else 0.0,
            "sum_core_ipc": sum(core["ipc"] for core in cores),
            "l1_demand_misses": sum(c["l1_demand_misses"] for c in cores),
            "l2_demand_misses": sum(c["l2_demand_misses"] for c in cores),
            "iew_stall_events": sum(c["iew_stall_events"] for c in cores),
            "stalled_cycles": sum(c["stalled_cycles"] for c in cores),
        },
        "slc": slc,
        "victim_by_requester": vector(stats, HNF + "victimByRequester::"),
        "victim_by_set": vector(stats, HNF + "victimBySet::"),
        "victim_by_policy": vector(stats, HNF + "victimByPolicy::"),
        "routers": routers,
        "noc": noc,
        "memory": {
            "read_requests": int(metric(stats, "system.mem_ctrls.readReqs")),
            "write_requests": int(metric(stats, "system.mem_ctrls.writeReqs")),
            "read_bursts": int(metric(stats, "system.mem_ctrls.readBursts")),
            "write_bursts": int(metric(stats, "system.mem_ctrls.writeBursts")),
        },
        "host": {
            "seconds": metric(stats, "hostSeconds"),
            "memory_bytes": int(metric(stats, "hostMemory")),
            "inst_rate": metric(stats, "hostInstRate"),
        },
    }


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def validate_results(results: dict[str, dict]) -> list[str]:
    checks = []
    restore_progress = None
    roi_ticks = None
    for policy, data in results.items():
        slc = data["slc"]
        capacity = int(slc["slcCapacityLines"])
        assert capacity == 16384
        assert int(slc["slcPeakValidLines"]) == capacity
        assert int(slc["maxValidWaysPerSet"]) == 16
        assert slc["fullSlcCycles"] > 0
        assert slc["replacementAttempts"] > 0
        assert slc["totalSlcVictims"] > 0
        assert slc["victimBufferFullReplays"] == 0
        assert slc["replacementAttempts"] == slc["totalSlcVictims"]
        assert (slc["cleanSlcVictims"] + slc["dirtySlcVictims"] ==
                slc["totalSlcVictims"])
        assert sum(data["victim_by_requester"].values()) == int(
            slc["totalSlcVictims"])
        assert sum(data["victim_by_set"].values()) == int(
            slc["totalSlcVictims"])
        assert data["victim_by_policy"].get(policy, 0) == int(
            slc["totalSlcVictims"])
        phase = data["phase"]["phases"]
        assert phase["restore"]["cpu_active_threads"] == [1, 1]
        assert phase["restore"]["slc_valid_lines"] == capacity
        assert phase["restore"]["slc_capacity_lines"] == capacity
        assert phase["roi_end"]["cpu_active_threads"] == [1, 1]
        assert all(value >= data["phase"]["min_roi_insts_per_core"]
                   for value in phase["roi_end"]["measured_cpu_insts"])
        progress = phase["restore"]["cpu_insts"]
        ticks = phase["roi_end"]["tick"] - phase["roi_start"]["tick"]
        restore_progress = restore_progress or progress
        roi_ticks = roi_ticks or ticks
        assert progress == restore_progress
        assert ticks == roi_ticks == data["phase"]["roi_ticks"]
        checks.append(
            f"{policy}: full 16384/16384, victims={int(slc['totalSlcVictims'])}, "
            f"ROI insts={phase['roi_end']['measured_cpu_insts']}")
    return checks


def analyze(result: Path) -> None:
    result = result.resolve()
    analysis = result / "analysis"
    results = {
        policy: policy_metrics(policy, result / "runs" / policy)
        for policy in POLICIES
    }
    checks = validate_results(results)
    summary = {
        "schema_version": 1,
        "classification": CLASSIFICATION,
        "warning": "Comparative proxy-workload results; not SPEC CPU2006 scores.",
        "validation": checks,
        "instruction_count_variation": {
            f"cpu{cpu}": {
                "by_policy": {
                    policy: data["cores"][cpu]["instructions"]
                    for policy, data in results.items()
                },
                "max_relative_spread_percent": (
                    (max(data["cores"][cpu]["instructions"]
                         for data in results.values()) -
                     min(data["cores"][cpu]["instructions"]
                         for data in results.values())) /
                    results["random"]["cores"][cpu]["instructions"] * 100.0
                    if results["random"]["cores"][cpu]["instructions"]
                    else 0.0),
            }
            for cpu in range(2)
        },
        "policies": results,
    }
    write_json(analysis / "summary.json", summary)

    per_core = []
    for policy, data in results.items():
        for core in data["cores"]:
            per_core.append({"policy": policy, **core})
    write_csv(
        analysis / "per_core_metrics.csv", list(per_core[0]), per_core)

    comparison_metrics = {
        "cpu0_ipc": lambda d: d["cores"][0]["ipc"],
        "cpu1_ipc": lambda d: d["cores"][1]["ipc"],
        "aggregate_ipc": lambda d: d["aggregate"]["ipc"],
        "sum_core_ipc": lambda d: d["aggregate"]["sum_core_ipc"],
        "cpu0_l1_demand_mpki": lambda d: d["cores"][0]["l1_demand_mpki"],
        "cpu1_l1_demand_mpki": lambda d: d["cores"][1]["l1_demand_mpki"],
        "cpu0_l2_demand_mpki": lambda d: d["cores"][0]["l2_demand_mpki"],
        "cpu1_l2_demand_mpki": lambda d: d["cores"][1]["l2_demand_mpki"],
        "aggregate_l1_demand_misses": lambda d: d["aggregate"]["l1_demand_misses"],
        "aggregate_l2_demand_misses": lambda d: d["aggregate"]["l2_demand_misses"],
        "aggregate_iew_stall_events": lambda d: d["aggregate"]["iew_stall_events"],
        "slc_hit_rate": lambda d: d["slc"]["slc_hit_rate"],
        "sf_hit_rate": lambda d: d["slc"]["sf_hit_rate"],
        "slc_hits": lambda d: d["slc"]["slc_hits"],
        "slc_misses": lambda d: d["slc"]["slc_misses"],
        "sf_hits": lambda d: d["slc"]["sf_hits"],
        "sf_misses": lambda d: d["slc"]["sf_misses"],
        "replacement_attempts": lambda d: d["slc"]["replacementAttempts"],
        "total_slc_victims": lambda d: d["slc"]["totalSlcVictims"],
        "clean_slc_victims": lambda d: d["slc"]["cleanSlcVictims"],
        "dirty_slc_victims": lambda d: d["slc"]["dirtySlcVictims"],
        "sf_victims": lambda d: d["slc"]["sfVictims"],
        "directed_snoops": lambda d: d["slc"]["directedSnoops"],
        "broadcast_snoops": lambda d: d["slc"]["broadcastSnoops"],
        "victim_rate_per_completed_lookup": lambda d:
            d["slc"]["victim_rate_per_completed_lookup"],
        "victims_per_ki": lambda d: d["slc"]["victims_per_ki"],
        "hnf_service_stalls": lambda d: d["slc"]["serviceStalls"],
        "hnf_stale_token_replays": lambda d: d["slc"]["staleTokenReplays"],
        "hnf_resource_conflict_replays": lambda d:
            d["slc"]["resourceConflictReplays"],
        "hnf_seq_conflict_replays": lambda d: d["slc"]["seqConflictReplays"],
        "hnf_victim_buffer_full_replays": lambda d:
            d["slc"]["victimBufferFullReplays"],
        "hnf_cancelled_replays": lambda d: d["slc"]["cancelledReplays"],
        "hnf_total_replay_responses": lambda d:
            d["slc"]["totalReplayResponses"],
        "hnf_replays_per_ki": lambda d: d["slc"]["replays_per_ki"],
        "hnf_replays_per_1000_completed_lookups": lambda d:
            d["slc"]["replays_per_1000_completed_lookups"],
        "hnf_request_full_cycles": lambda d: d["slc"]["requestFullCycles"],
        "hnf_response_full_cycles": lambda d: d["slc"]["responseFullCycles"],
        "hnf_no_credit": lambda d: d["slc"]["noCredit"],
        "hnf_set_lock_conflicts": lambda d: d["slc"]["setLockConflicts"],
        "hnf_service_stalls_per_ki": lambda d:
            d["slc"]["service_stalls_per_ki"],
        "hnf_service_stalls_per_1000_completed_lookups": lambda d:
            d["slc"]["service_stalls_per_1000_completed_lookups"],
        "hnf_no_credit_per_1000_completed_lookups": lambda d:
            d["slc"]["no_credit_per_1000_completed_lookups"],
        "accepted_to_visible_latency_cycles": lambda d:
            d["slc"]["accepted_to_visible_latency_cycles"],
        "noc_total_traffic_score": lambda d: d["noc"]["total_traffic_score"],
        "noc_total_stall_cycles": lambda d:
            d["noc"]["internal_output_stall_cycles"] +
            d["noc"]["local_injection_stall_cycles"] +
            d["noc"]["local_delivery_stall_cycles"],
        "memory_reads": lambda d: d["memory"]["read_requests"],
        "memory_writes": lambda d: d["memory"]["write_requests"],
        "host_seconds_from_stats": lambda d: d["host"]["seconds"],
        "host_simulated_inst_rate": lambda d: d["host"]["inst_rate"],
    }
    baseline = results["random"]
    comparison = []
    for policy, data in results.items():
        for name, getter in comparison_metrics.items():
            value = getter(data)
            base = getter(baseline)
            comparison.append({
                "policy": policy, "metric": name, "value": value,
                "random_value": base, "delta_vs_random": value - base,
                "percent_vs_random": ((value - base) / base * 100.0)
                if base else 0.0,
            })
    write_csv(
        analysis / "policy_comparison.csv", list(comparison[0]), comparison)

    occupancy = [{"policy": p, **{
        key: data["slc"][key] for key in (
            "slcValidLines", "slcPeakValidLines", "slcCapacityLines",
            "slcOccupancyPercent", "maxValidWaysPerSet", "fullSlcCycles")}}
        for p, data in results.items()]
    write_csv(analysis / "slc_occupancy.csv", list(occupancy[0]), occupancy)

    victims = [{"policy": p, **{
        key: data["slc"][key] for key in (
            "replacementAttempts", "cleanSlcVictims", "dirtySlcVictims",
            "totalSlcVictims", "sfVictims", "directedSnoops",
            "broadcastSnoops", "accepted_to_visible_latency_cycles")}}
        for p, data in results.items()]
    write_csv(analysis / "hnf_victims.csv", list(victims[0]), victims)

    pressure = [{"policy": p, **{
        key: data["slc"][key] for key in (
            "staleTokenReplays", "resourceConflictReplays",
            "seqConflictReplays", "victimBufferFullReplays",
            "cancelledReplays", "totalReplayResponses",
            "requestFullCycles", "responseFullCycles", "noCredit",
            "serviceStalls", "setLockConflicts", "replays_per_ki",
            "replays_per_1000_completed_lookups", "service_stalls_per_ki",
            "service_stalls_per_1000_completed_lookups",
            "no_credit_per_1000_completed_lookups")}}
        for p, data in results.items()]
    write_csv(
        analysis / "hnf_pressure_replay.csv", list(pressure[0]), pressure)

    by_rnf = []
    for policy, data in results.items():
        for requester in range(64):
            count = data["victim_by_requester"].get(str(requester), 0)
            if count or requester in (0, 4):
                by_rnf.append({
                    "policy": policy, "requester_id": requester,
                    "rnf": "RNF0/CPU0" if requester == 0 else
                           "RNF1/CPU1" if requester == 4 else "other",
                    "victims": count,
                })
    write_csv(analysis / "victim_by_rnf.csv", list(by_rnf[0]), by_rnf)

    by_set = []
    for policy, data in results.items():
        for set_index in range(1024):
            by_set.append({
                "policy": policy, "set": set_index,
                "victims": data["victim_by_set"].get(str(set_index), 0),
            })
    write_csv(analysis / "victim_by_set.csv", list(by_set[0]), by_set)

    routers = []
    for policy, data in results.items():
        ranked = sorted(
            data["routers"], key=lambda row: row["traffic_score"], reverse=True)
        for rank, row in enumerate(ranked, 1):
            routers.append({"policy": policy, "hotspot_rank": rank, **row})
    write_csv(analysis / "router_hotspots.csv", list(routers[0]), routers)

    run_summary = {
        "status": "validated",
        "classification": CLASSIFICATION,
        "validation": checks,
        "analysis": str(analysis / "summary.json"),
    }
    write_json(result / "run_summary.json", run_summary)
    print(f"[ok] validated analysis: {analysis}")


def parse_args():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    run = sub.add_parser("run")
    run.add_argument("result", type=Path)
    run.add_argument("--memory", default="1GB")
    run.add_argument("--seed", type=int, default=20260730)
    run.add_argument("--fill-max-ticks", type=int, default=5_000_000_000)
    run.add_argument("--settle-ticks", type=int, default=100_000)
    run.add_argument("--checkpoint-full-retry-limit", type=int, default=8)
    run.add_argument(
        "--checkpoint-refill-max-ticks", type=int, default=1_000_000_000)
    run.add_argument("--roi-ticks", type=int, default=2_000_000_000)
    run.add_argument("--min-roi-insts-per-core", type=int, default=100_000)
    run.add_argument(
        "--guest-stack-sample-period-insts", type=int, default=1000)
    run.add_argument("--skip-warmup", action="store_true")
    run.add_argument(
        "--checkpoint-source", type=Path,
        help="copy an existing common_full_cpt before a --skip-warmup run")
    analyze_parser = sub.add_parser("analyze")
    analyze_parser.add_argument("result", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.action == "run":
            run_experiment(args)
        else:
            analyze(args.result)
    except (AssertionError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
