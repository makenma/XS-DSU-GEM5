#!/usr/bin/env python3
"""Measure complete gem5 HNF runs and require identical timed port traffic."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import time


def main():
    repo = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, default=repo / "build/RISCV/gem5.opt")
    parser.add_argument("--out", type=Path, default=repo / "m5out/chi-review/hnf-performance")
    parser.add_argument("--requests", type=int, default=100000)
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()
    if args.requests <= 0 or args.runs <= 0:
        parser.error("requests and runs must be positive")
    args.out.mkdir(parents=True, exist_ok=True)
    cpu = min(os.sched_getaffinity(0))
    os.sched_setaffinity(0, {cpu})
    reference = None
    samples = {"baseline": [], "candidate": []}
    for repeat in range(args.runs):
        order = ("baseline", "candidate") if repeat % 2 == 0 else ("candidate", "baseline")
        for label in order:
            folder = args.out / f"{label}-{repeat}"
            folder.mkdir(exist_ok=True)
            command = [str(getattr(args, label).resolve()), "-d", str(folder.resolve()),
                       str(repo / "configs/example/chi_hnf_slcsf.py"), "--scenario=throughput",
                       f"--requests={args.requests}", "--max-cycles=8000000"]
            start = time.perf_counter()
            with (folder / "run.log").open("w") as log:
                result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=180)
            elapsed = time.perf_counter() - start
            output = (folder / "run.log").read_text()
            match = re.search(r"CHI_HNF_PASS (.+)", output)
            if result.returncode or not match:
                raise RuntimeError(f"Failed simulation: {folder / 'run.log'}")
            signature = dict(re.findall(r"(\w+)=([^ ]+)", match[1]))
            if reference is None:
                reference = signature
            if signature != reference:
                raise RuntimeError(f"Port-cycle/data mismatch: {signature} != {reference}")
            stats = (folder / "stats.txt").read_text()
            host = float(re.search(r"^hostSeconds\s+(\S+)", stats, re.M)[1])
            samples[label].append({"wall_seconds": elapsed, "host_seconds": host})
            print(label, repeat, samples[label][-1], flush=True)
    medians = {label: {metric: statistics.median(s[metric] for s in values)
                       for metric in ("wall_seconds", "host_seconds")}
               for label, values in samples.items()}
    def sha256(path):
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    report = {"scope": "complete gem5 HNF endpoint workload, not a CPU application",
              "cpu": cpu, "requests": args.requests, "runs": args.runs,
              "binary_sha256": {label: sha256(getattr(args, label))
                                for label in ("baseline", "candidate")},
              "config_sha256": sha256(repo / "configs/example/chi_hnf_slcsf.py"),
              "signature": reference, "samples": samples, "medians": medians,
              "speedup": {metric: medians["baseline"][metric] / medians["candidate"][metric]
                          for metric in ("wall_seconds", "host_seconds")}}
    (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
