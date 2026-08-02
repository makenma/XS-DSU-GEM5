#!/usr/bin/env python3
"""Run a short, timing-neutrality and host-overhead A/B for instrumentation."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "util/xs_scripts/chi_2x2_dual_proxy_experiment.py"
CONFIG = ROOT / "configs/example/kmhv2_chi_2x2_hnf_se.py"
GEM5 = ROOT / "build/RISCV/gem5.opt"
PRIOR = ROOT / "results/chi-2x2-dual-public-proxy-direct-victim-v1"
POLICIES = ("lru", "random", "srrip")


def load_runner():
    spec = importlib.util.spec_from_file_location("chi_dual_runner_ab", RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {RUNNER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")


def numeric_stats(runner, path: Path) -> dict[str, float]:
    return runner.parse_stat_sections(path)[0]


def non_host_differences(left: dict[str, float], right: dict[str, float]):
    differences = []
    for name in sorted(left.keys() | right.keys()):
        if "host" in name.lower() or "wallclock" in name.lower():
            continue
        a = left.get(name)
        b = right.get(name)
        if a == b or (
                a is not None and b is not None and
                math.isnan(a) and math.isnan(b)):
            continue
        differences.append({"stat": name, "off": a, "instrumented": b})
    return differences


def command_for(runner, dependencies, checkpoint: Path, outdir: Path,
                ticks: int, latency: bool, profile: bool) -> list[str]:
    command = [
        str(GEM5), "-d", str(outdir),
        *runner.common_args(dependencies, "1GB"),
        "--experiment-mode=roi",
        "--slc-replacement-policy=lru",
        "--slc-replacement-seed=20260730",
        f"--roi-ticks={ticks}",
        "--min-roi-insts-per-core=1000",
        f"--common-checkpoint={checkpoint}",
    ]
    if latency:
        command.append("--enable-rnf-transaction-latency")
    if profile:
        command.extend([
            "--enable-guest-stack-profile",
            "--guest-stack-sample-period-insts=1000",
        ])
    return command


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    parser.add_argument("--ticks", type=int, default=20_000_000)
    args = parser.parse_args()
    result = args.result.resolve()
    checkpoint = result / "warmup/common_full_cpt"
    output_root = result / "validation/instrumentation_ab"
    try:
        if output_root.exists():
            raise RuntimeError(
                f"refusing to overwrite existing A/B directory: {output_root}")
        if not checkpoint.is_dir():
            raise RuntimeError(f"checkpoint is missing: {checkpoint}")
        runner = load_runner()
        dependencies = runner.validate_dependencies()
        modes = {
            "off": (False, False),
            "latency_only": (True, False),
            "profile_only": (False, True),
            "both": (True, True),
        }
        records = {}
        for mode, (latency, profile) in modes.items():
            outdir = output_root / mode
            command = command_for(
                runner, dependencies, checkpoint, outdir,
                args.ticks, latency, profile)
            run = runner.run_command(command, outdir)
            phase = json.loads((outdir / "phase_metadata.json").read_text())
            stats = numeric_stats(runner, outdir / "stats.txt")
            records[mode] = {
                "latency_enabled": latency,
                "profile_enabled": profile,
                "host_elapsed_seconds": run["host_elapsed_seconds"],
                "host_seconds_from_stats": stats["hostSeconds"],
                "sim_ticks": int(stats["simTicks"]),
                "sim_insts": int(stats["simInsts"]),
                "roi_insts": phase["phases"]["roi_end"][
                    "measured_cpu_insts"],
                "active_at_end": phase["phases"]["roi_end"][
                    "cpu_active_threads"],
            }
        off_stats = numeric_stats(runner, output_root / "off/stats.txt")
        comparisons = {}
        all_differences = []
        for mode in ("latency_only", "profile_only", "both"):
            stats = numeric_stats(runner, output_root / mode / "stats.txt")
            differences = non_host_differences(off_stats, stats)
            all_differences.extend({"mode": mode, **item} for item in differences)
            comparisons[mode] = {
                "non_host_numeric_stat_differences": len(differences),
                "differences": differences,
                "host_overhead_percent_from_stats": (
                    (stats["hostSeconds"] / off_stats["hostSeconds"] - 1) * 100),
                "host_elapsed_overhead_percent": (
                    (records[mode]["host_elapsed_seconds"] /
                     records["off"]["host_elapsed_seconds"] - 1) * 100),
            }

        formal = {}
        formal_all_differences = []
        for policy in POLICIES:
            current = result / "runs" / policy
            prior = PRIOR / "runs" / policy
            current_stats = numeric_stats(runner, current / "stats.txt")
            prior_stats = numeric_stats(runner, prior / "stats.txt")
            differences = non_host_differences(prior_stats, current_stats)
            formal_all_differences.extend(
                {"policy": policy, **item} for item in differences)
            formal[policy] = {
                "non_host_numeric_stat_differences": len(differences),
                "prior_host_seconds": prior_stats["hostSeconds"],
                "instrumented_host_seconds": current_stats["hostSeconds"],
                "combined_instrumentation_host_overhead_percent": (
                    (current_stats["hostSeconds"] /
                     prior_stats["hostSeconds"] - 1) * 100),
            }
        summary = {
            "schema_version": 1,
            "purpose": (
                "host-overhead estimate and proof that instrumentation does "
                "not change simulated numeric statistics"),
            "checkpoint": str(checkpoint),
            "short_roi_ticks": args.ticks,
            "sample_period_retired_macro_insts": 1000,
            "records": records,
            "comparisons_vs_off": comparisons,
            "non_host_numeric_stat_differences": len(all_differences),
            "combined_host_overhead_percent": comparisons["both"][
                "host_overhead_percent_from_stats"],
            "latency_only_host_overhead_percent": comparisons["latency_only"][
                "host_overhead_percent_from_stats"],
            "profile_only_host_overhead_percent": comparisons["profile_only"][
                "host_overhead_percent_from_stats"],
            "formal_2b_comparison_to_uninstrumented_prior_direct_pocq": formal,
            "formal_non_host_numeric_stat_differences": len(
                formal_all_differences),
            "host_overhead_warning": (
                "Host wall-clock overhead is machine/load dependent. Short "
                "A/B modes isolate recorder costs; the formal comparison "
                "captures scaling at the full 2B-tick ROI. Neither is guest "
                "simulated latency."),
            "semantic_timing_effect": False,
        }
        write_json(result / "validation/instrumentation_ab_summary.json", summary)
        if all_differences or formal_all_differences:
            raise AssertionError(
                f"instrumentation changed simulated stats: short="
                f"{len(all_differences)}, formal={len(formal_all_differences)}")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    except (AssertionError, OSError, RuntimeError, KeyError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
