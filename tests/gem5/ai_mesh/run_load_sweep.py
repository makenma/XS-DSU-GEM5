import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ARCH_YAML = REPO / "configs/example/ai_mesh/arch/mesh_1x2.yaml"
CONFIG = REPO / "configs/example/ai_mesh/run_mesh_dma_garnet.py"
MESH_IR_ROOT = REPO / "util/mesh_ir"

WORKLOADS = ("contiguous", "multi_tensor", "strided")

VNET_PROFILES = {
    "V0": "2,2,2,2,2",
    "V1": "4,8,4,4,8",
    "V2": "8,4,8,8,4",
    "V3": "8,8,8,8,8",
}

BASELINE = {"outstanding": 4, "burst": 8, "vnet": "V1"}

PHASE_OUTSTANDING = {"C1": 1, "C2": 2, "C3": 8, "C4": 16}
PHASE_BURST = {"C5": 2, "C6": 4, "C7": 16}
PHASE_VNET = {"C8": "V0", "C9": "V2", "C10": "V3"}


def fatal(condition, workspace, message):
    if condition:
        raise SystemExit(f"LOAD_SWEEP_FAIL [{workspace}]: {message}")


def run_command(command, log_path, env=None):
    with open(log_path, "w") as handle:
        completed = subprocess.run(
            command, cwd=REPO, stdout=handle, stderr=subprocess.STDOUT,
            env=env, text=True)
    return completed.returncode


def patch_arch(source_text, burst_beats, read_outstanding):
    text = re.sub(r"max_burst_beats: \d+", f"max_burst_beats: {burst_beats}",
                  source_text, count=1)
    text = re.sub(r"read_outstanding: \d+",
                  f"read_outstanding: {read_outstanding}", text, count=1)
    return text


def materialize(workspace, burst_beats, read_outstanding, workload):
    (workspace / "program").mkdir(parents=True, exist_ok=True)
    arch_path = workspace / "arch.yaml"
    arch_path.write_text(
        patch_arch(ARCH_YAML.read_text(), burst_beats, read_outstanding))
    env = dict(**os.environ, PYTHONPATH=str(MESH_IR_ROOT))
    code = run_command(
        [sys.executable, "-m", "mesh_ir.cli", "build",
         "--program", f"load_saturation_{workload}",
         "--arch", str(arch_path), "--out", str(workspace / "program")],
        workspace / "build.log", env)
    fatal(code != 0, workspace, "program build failed")
    return arch_path, workspace / "program"


def measure(result, axes, latency, passed):
    timings = result.get("dma_timings", [])
    bridges = result.get("bridges", [])
    period = result.get("core_clock_period_ticks", 0)
    total = sum(b["valid_read_bytes"] for b in bridges)
    duration = (max(row["local_commit_tick"] for row in timings) -
                min(row["first_ar_tick"] for row in timings)) if timings else 0
    reasons = []
    if not passed:
        reasons.append("run_failed")
    if result.get("watchdog_fired", False):
        reasons.append("watchdog")
    if not result.get("transport") or any(
            row["error_code"] != 0 for row in result["transport"]):
        reasons.append("transport_error_or_missing")
    if not timings or any(row["first_ar_tick"] <= 0 or
                          row["local_commit_tick"] <= row["first_ar_tick"]
                          for row in timings):
        reasons.append("incomplete_timings")
    if duration <= 0:
        reasons.append("nonpositive_duration")
    if total <= 0:
        reasons.append("no_valid_bytes")
    if period <= 0:
        reasons.append("missing_clock_period")
    if not result.get("network_stalls") or not result.get(
            "target_message_buffer_blocked_cycles"):
        reasons.append("missing_backpressure_evidence")
    cycles = duration / period if duration > 0 and period > 0 else 0
    finishes = {}
    for row in timings:
        core = row.get("core_id", 0)
        finishes[core] = max(finishes.get(core, 0), row["local_commit_tick"])
    skew = max(finishes.values()) - min(finishes.values()) if finishes else 0
    stalls = result.get("network_stalls", {})
    return {
        **axes,
        "latency_cycles": latency,
        "valid": not reasons,
        "invalid_reasons": reasons,
        "bytes_per_cycle": total / cycles if not reasons else 0.0,
        "total_bytes": total,
        "duration_ticks": duration,
        "core_clock_period_ticks": period,
        "cycles": cycles,
        "completion_skew_cycles": skew / period if period > 0 else 0,
        "peak_read_outstanding": max(
            (b["peak_read_outstanding"] for b in bridges), default=0),
        "initiator_message_buffer_blocked_cycles": {
            str(b["core_id"]): b.get("message_buffer_blocked_cycles", {})
            for b in bridges if "core_id" in b},
        "target_message_buffer_blocked_cycles": result.get(
            "target_message_buffer_blocked_cycles", {}),
        "network_stalls": stalls,
        "router_credit_stall_vc_cycles": sum(
            row["router_credit_stall_vc_cycles"] for row in stalls.values()),
    }


def execute_run(gem5, workspace, arch_path, program_dir, axes, latency):
    env = dict(**os.environ,
               AI_MESH_ARTIFACT_DIR=str(workspace),
               PYTHONPATH=str(MESH_IR_ROOT))
    code = run_command(
        [str(gem5), f"--outdir={workspace / 'm5out'}", str(CONFIG),
         "--case", f"load_saturation_{axes['workload']}",
         "--mesh-program-dir", str(program_dir),
         "--arch", str(arch_path),
         "--read-outstanding", str(axes["outstanding"]),
         "--load-latency-cycles", str(latency),
         "--watchdog-ticks", "100000000",
         "--axi-max-sim-ticks", "200000000",
         "--garnet-buffers-per-vnet", VNET_PROFILES[axes["vnet"]]],
        workspace / "run.log", env)
    passed = code == 0 and "MESH_DMA_GARNET_PASS" in (
        workspace / "run.log").read_text()
    result_path = workspace / "actual_result.json"
    result = json.loads(result_path.read_text()) if result_path.exists() else {}
    row = measure(result, axes, latency, passed)
    stats_path = workspace / "m5out" / "stats.txt"
    row["message_buffer_queue_time_ticks"] = {}
    if stats_path.exists():
        for line in stats_path.read_text().splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[0].endswith(".m_stall_time"):
                row["message_buffer_queue_time_ticks"][fields[0]] = float(fields[1])
    return passed, row


def named_configs():
    configs = {"C0": dict(BASELINE)}
    configs.update({name: {**BASELINE, "outstanding": value}
                    for name, value in PHASE_OUTSTANDING.items()})
    configs.update({name: {**BASELINE, "burst": value}
                    for name, value in PHASE_BURST.items()})
    configs.update({name: {**BASELINE, "vnet": value}
                    for name, value in PHASE_VNET.items()})
    return configs


def best_of(rows, key):
    candidates = [row for row in rows if row["valid"]]
    return max(candidates, key=lambda row: row[key]) if candidates else None


def pareto_frontier(rows):
    points = [row for row in rows if row["valid"]]
    frontier = []
    for row in points:
        dominated = any(
            other is not row and
            other["bytes_per_cycle"] >= row["bytes_per_cycle"] and
            other["router_credit_stall_vc_cycles"] <= row["router_credit_stall_vc_cycles"] and
            (other["bytes_per_cycle"] > row["bytes_per_cycle"] or
             other["router_credit_stall_vc_cycles"] < row["router_credit_stall_vc_cycles"])
            for other in points)
        if not dominated:
            frontier.append(row)
    return frontier


def knee(frontier):
    if not frontier:
        return None
    x_max = max(row["bytes_per_cycle"] for row in frontier)
    x_min = min(row["bytes_per_cycle"] for row in frontier)
    y_max = max(row["router_credit_stall_vc_cycles"] for row in frontier)
    y_min = min(row["router_credit_stall_vc_cycles"] for row in frontier)

    def score(row):
        x = ((row["bytes_per_cycle"] - x_min) / (x_max - x_min)
             if x_max > x_min else 1.0)
        y = ((row["router_credit_stall_vc_cycles"] - y_min) / (y_max - y_min)
             if y_max > y_min else 1.0)
        return x + (1 - y)

    return max(frontier, key=score)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gem5", default=REPO / "build/AXI_MESH/gem5.opt")
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--workloads", nargs="+", choices=WORKLOADS,
                        default=list(WORKLOADS))
    parser.add_argument("--latency-cycles", nargs="+", type=int, default=[0])
    args = parser.parse_args()

    workdir = Path(args.workdir)
    fatal(workdir.exists() and any(workdir.iterdir()), workdir,
          "workdir must be new or empty")
    workdir.mkdir(parents=True, exist_ok=True)
    gem5 = Path(args.gem5)
    fatal(not gem5.exists(), workdir, f"missing gem5 binary {gem5}")

    configs = named_configs()
    runs = []
    phase1_names = ["C0"] + list(PHASE_OUTSTANDING)
    phase2_names = list(PHASE_BURST)
    phase3_names = list(PHASE_VNET)

    pareto = {}
    for workload in args.workloads:
        pareto[workload] = {}
        for latency in args.latency_cycles:
            group = []
            incumbent = None
            for phase, names in enumerate((phase1_names, phase2_names, phase3_names)):
                override = {}
                if incumbent:
                    override["outstanding"] = incumbent["outstanding"]
                    if phase == 2:
                        override["burst"] = incumbent["burst"]
                for name in names:
                    axes = {**configs[name], **override, "workload": workload}
                    label = f"{name}_{workload}_L{latency}"
                    workspace = workdir / label
                    arch_path, program_dir = materialize(
                        workspace, axes["burst"], axes["outstanding"], workload)
                    _, row = execute_run(
                        gem5, workspace, arch_path, program_dir, axes, latency)
                    row["config"] = name
                    runs.append(row)
                    group.append(row)
                    print(f"[{'PASS' if row['valid'] else 'INVALID'}] {label}: "
                          f"{row['bytes_per_cycle']:.3f} B/core-cycle", flush=True)
                incumbent = best_of(group, "bytes_per_cycle")
                if incumbent is None:
                    break
            frontier = pareto_frontier(group)
            chosen = knee(frontier)
            pareto[workload][str(latency)] = {
                "frontier": [r["config"] for r in frontier],
                "knee": chosen["config"] if chosen else None,
                "knee_metrics": chosen,
            }
    (workdir / "results.json").write_text(json.dumps(runs, indent=1))
    (workdir / "pareto.json").write_text(json.dumps(pareto, indent=1))
    invalid = sum(not row["valid"] for row in runs)
    print(f"LOAD_SWEEP_COMPLETE runs={len(runs)} invalid={invalid}")


if __name__ == "__main__":
    main()
