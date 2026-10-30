#!/usr/bin/env python3
"""Compare complete router simulations, requiring identical traffic and stats.

Without case filters, run four two-hop cases covering throughput/congestion,
single/dual lanes, and FIFO/VOQ. Explicit filters select their Cartesian product;
unspecified dimensions then default to throughput+congestion, both lane/queue
modes, and two hops. The existing config and tester are used without changes.

``parse_router_stats(path)`` is also available to regression comparison scripts.
It returns every router./relay. statistic from the last complete stats dump,
including stall counters, with numeric spellings and NaNs normalized.
"""

import argparse
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import re
import statistics
import subprocess
import time


ROOT = Path(__file__).resolve().parents[3]
RESULT = re.compile(
    r"CHI_ROUTER_RESULT scenario=(\w+) flits=(\d+) cycles=(\d+) digest=(\d+)"
)
DEFAULT_CASES = (
    ("throughput", "single", "fifo", "two"),
    ("throughput", "dual", "voq", "two"),
    ("congestion", "single", "voq", "two"),
    ("congestion", "dual", "fifo", "two"),
)
METRICS = ("host_seconds", "wall_seconds")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_stat_value(token):
    """Canonicalize exact decimals without reducing counters to float64."""
    suffix = "%" if token.endswith("%") else ""
    number = token[:-1] if suffix else token
    try:
        value = Decimal(number)
    except InvalidOperation:
        return token
    if value.is_nan():
        return "nan" + suffix
    if value.is_infinite():
        return ("-inf" if value.is_signed() else "inf") + suffix
    if value.is_zero():
        return "0" + suffix
    with localcontext() as context:
        context.prec = max(50, len(number))
        return str(value.normalize()) + suffix


def parse_stats(path):
    """Read the last complete gem5 statistics block, preserving all columns."""
    current, completed = None, None
    for line in Path(path).read_text().splitlines():
        if "Begin Simulation Statistics" in line:
            current = {}
        elif "End Simulation Statistics" in line:
            completed, current = current, None
        elif current is not None:
            fields = line.partition("#")[0].split()
            if len(fields) >= 2:
                current[fields[0]] = [normalize_stat_value(v) for v in fields[1:]]
    if completed is None or current is not None:
        raise ValueError(f"Missing or incomplete statistics dump: {path}")
    return completed


def parse_router_stats(path):
    """Return every router./relay. key; reject an empty comparison surface."""
    result = {key: value for key, value in parse_stats(path).items()
              if key.startswith(("router.", "relay."))}
    if not any(key.startswith("router.trafficStats.") for key in result):
        raise ValueError(f"No router traffic statistics: {path}")
    return result


def stats_difference(reference, actual):
    """Include missing/extra keys as well as unequal normalized values."""
    return {key: {"reference": reference.get(key), "actual": actual.get(key)}
            for key in sorted(reference.keys() | actual.keys())
            if reference.get(key) != actual.get(key)}


def json_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def run_once(binary, config, case, args, folder, label, pair):
    scenario, lane, queue, hops = case
    folder.mkdir()
    command = [str(binary), "-d", str(folder), str(config),
               "--scenario", scenario, "--repetitions", str(args.repetitions)]
    if lane == "dual":
        command.append("--dual")
    if queue == "voq":
        command.append("--per-output")
    if hops == "two":
        command.append("--two-hop")
    sample = {"label": label, "pair": pair, "command": command,
              "log": str(folder / "run.log"), "returncode": None}
    start = time.perf_counter()
    try:
        with (folder / "run.log").open("w") as log:
            result = subprocess.run(command, cwd=ROOT, stdout=log,
                                    stderr=subprocess.STDOUT, timeout=args.timeout)
        sample["returncode"] = result.returncode
    except subprocess.TimeoutExpired:
        sample["error"] = f"Simulation exceeded {args.timeout} seconds"
    sample["wall_seconds"] = time.perf_counter() - start
    matches = list(RESULT.finditer((folder / "run.log").read_text(errors="replace")))
    if sample["returncode"] != 0 or len(matches) != 1:
        sample.setdefault("error", "Simulation failed or lacks one result signature")
    else:
        match = matches[0]
        sample["signature"] = {"scenario": match[1], "flits": int(match[2]),
                               "cycles": int(match[3]), "digest": match[4]}
        try:
            if match[1] != scenario:
                raise ValueError("Result scenario does not match the requested workload")
            stats_path = folder / "stats.txt"
            host_seconds = float(parse_stats(stats_path)["hostSeconds"][0])
            if not math.isfinite(host_seconds) or host_seconds <= 0:
                raise ValueError("hostSeconds must be positive and finite")
            sample["host_seconds"] = host_seconds
            sample["router_stats"] = parse_router_stats(stats_path)
            if hops == "two" and not any(
                    key.startswith("relay.trafficStats.") for key in sample["router_stats"]):
                raise ValueError("Two-hop run has no relay traffic statistics")
            sample["router_stats_sha256"] = json_digest(sample["router_stats"])
        except (ValueError, KeyError, OSError) as error:
            sample["error"] = str(error)
    write_json(folder / "result.json", sample)
    return sample


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, default=ROOT / "build/RISCV/gem5.opt")
    parser.add_argument("--out", type=Path, default=ROOT / "m5out/chi-router-performance")
    parser.add_argument("--runs", type=int, default=5, help="Paired runs per case")
    parser.add_argument("--repetitions", type=int, default=16384)
    parser.add_argument("--cpu", type=int, help="One allowed host CPU; default: lowest allowed")
    parser.add_argument("--timeout", type=float, default=600, help="Seconds per simulation")
    parser.add_argument("--scenario", action="append", choices=("throughput", "congestion", "idle"))
    parser.add_argument("--lane", action="append", choices=("single", "dual"))
    parser.add_argument("--queue", action="append", choices=("fifo", "voq"))
    parser.add_argument("--hops", action="append", choices=("one", "two"))
    args = parser.parse_args()
    if (args.runs <= 0 or args.repetitions <= 0 or
            not math.isfinite(args.timeout) or args.timeout <= 0):
        parser.error("runs, repetitions, and timeout must be positive")
    binaries = {label: getattr(args, label).resolve() for label in ("baseline", "candidate")}
    for path in binaries.values():
        if not path.is_file() or not os.access(path, os.X_OK):
            parser.error(f"Not an executable binary: {path}")
    args.out = args.out.resolve()
    if args.out.exists() and any(args.out.iterdir()):
        parser.error(f"Output directory must be empty: {args.out}")
    allowed_cpus = os.sched_getaffinity(0)
    cpu = min(allowed_cpus) if args.cpu is None else args.cpu
    if cpu not in allowed_cpus:
        parser.error(f"CPU {cpu} is not in the allowed affinity {sorted(allowed_cpus)}")
    if any((args.scenario, args.lane, args.queue, args.hops)):
        cases = list(dict.fromkeys(itertools.product(
            args.scenario or ("throughput", "congestion"),
            args.lane or ("single", "dual"), args.queue or ("fifo", "voq"),
            args.hops or ("two",))))
    else:
        cases = list(DEFAULT_CASES)
    config = ROOT / "configs/example/chi_router.py"
    sources = [config, ROOT / "src/mem/cache/CHI/ChiRouterTester.cc",
               ROOT / "src/mem/cache/CHI/ChiRouterTester.hh",
               ROOT / "src/mem/cache/CHI/ChiRouterTester.py", Path(__file__).resolve()]
    artifacts = {**{f"binary:{key}": path for key, path in binaries.items()},
                 **{str(path.relative_to(ROOT)): path for path in sources}}
    hashes = {key: sha256(path) for key, path in artifacts.items()}
    args.out.mkdir(parents=True, exist_ok=True)
    report_path = args.out / "report.json"
    report = {"status": "running", "scope": "Complete gem5 router port workloads; no CPU application",
              "cpu": cpu, "runs_per_binary_per_case": args.runs,
              "repetitions": args.repetitions, "artifacts": {
                  key: {"path": str(path), "sha256": hashes[key]} for key, path in artifacts.items()},
              "comparison": "Exact flits/cycles/digest and every router./relay. stats column; NaNs normalized",
              "case_order": ["-".join(case) for case in cases], "cases": {}}
    write_json(report_path, report)
    os.sched_setaffinity(0, {cpu})
    try:
        for case in cases:
            name = "-".join(case)
            case_dir = args.out / name
            case_dir.mkdir()
            record = {"status": "running", "signature": None, "router_stats": None,
                      "samples": {"baseline": [], "candidate": []}, "execution_order": []}
            report["cases"][name] = record
            for pair in range(args.runs):
                order = ("baseline", "candidate") if pair % 2 == 0 else ("candidate", "baseline")
                for label in order:
                    sample = run_once(binaries[label], config, case, args,
                                      case_dir / f"{label}-{pair}", label, pair)
                    actual_stats = sample.pop("router_stats", None)
                    record["samples"][label].append(sample)
                    record["execution_order"].append(f"{label}-{pair}")
                    write_json(report_path, report)
                    if "error" in sample:
                        raise RuntimeError(f"{name}/{label}-{pair}: {sample['error']}; see {sample['log']}")
                    if record["signature"] is None:
                        record["signature"] = sample["signature"]
                        record["router_stats"] = actual_stats
                    if sample["signature"] != record["signature"]:
                        record["mismatch"] = {"reference": record["signature"], "actual": sample["signature"]}
                        raise RuntimeError(f"Timed traffic differs: {name}/{label}-{pair}")
                    differences = stats_difference(record["router_stats"], actual_stats)
                    if differences:
                        record["stats_mismatch"] = differences
                        raise RuntimeError(f"{len(differences)} router/relay stats differ: {name}/{label}-{pair}")
                    print(f"{name} {label}-{pair}: host={sample['host_seconds']:.3f}s "
                          f"wall={sample['wall_seconds']:.3f}s, equivalent", flush=True)
                    write_json(report_path, report)
            record["medians"] = {
                label: {metric: statistics.median(sample[metric] for sample in samples)
                        for metric in METRICS} for label, samples in record["samples"].items()}
            record["speedup"] = {metric: record["medians"]["baseline"][metric] /
                                 record["medians"]["candidate"][metric] for metric in METRICS}
            record["status"] = "passed"
            write_json(report_path, report)
        changed = [key for key, path in artifacts.items() if sha256(path) != hashes[key]]
        report["artifacts_unchanged"] = not changed
        if changed:
            raise RuntimeError(f"Benchmark inputs changed during execution: {changed}")
        report["status"] = "passed"
    except (Exception, KeyboardInterrupt) as error:
        report["status"] = "failed"
        report["error"] = str(error) or type(error).__name__
        for record in report["cases"].values():
            if record["status"] == "running":
                record["status"] = "failed"
        print(f"FAIL: {report['error']}", flush=True)
    finally:
        os.sched_setaffinity(0, allowed_cpus)
        write_json(report_path, report)
    print(f"{report['status'].upper()}: {report_path}", flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
