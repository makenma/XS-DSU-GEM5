#!/usr/bin/env python3
"""Compare a saved, functionally corrected SLCSF with the optimized model.

Save SlcSfModel.{cc,hh} and SlcSfTypes.hh in --baseline-source before
optimization. Both executables use the identical production-model workload.
Times exclude model construction and final RAM hashing. Every result, admission
decision, SEQ observation and final array state contributes to the digest.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess


def main():
    repo = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-source", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=repo / "m5out/chi-review/performance")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--dense", type=int, default=1000000)
    parser.add_argument("--sparse", type=int, default=1000)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    source = repo / "src/mem/cache/CHI"
    files = ("SlcSfModel.cc", "SlcSfModel.hh", "SlcSfTypes.hh")
    manifest = {}
    binaries = {}
    for label, directory in (("baseline", args.baseline_source), ("candidate", source)):
        include = args.out / (label + "-source")
        saved = include / "mem/cache/CHI"
        saved.mkdir(parents=True, exist_ok=True)
        manifest[label] = {}
        for name in files:
            shutil.copyfile(directory / name, saved / name)
            manifest[label][name] = hashlib.sha256((saved / name).read_bytes()).hexdigest()
        binary = args.out / (label + ".bench")
        command = ["g++", "-O3", "-DNDEBUG", "-g", "-std=c++17", "-I" + str(include),
                   str(source / "SlcSfModel.bench.cc"), str(saved / "SlcSfModel.cc"),
                   "-o", str(binary)]
        subprocess.run(command, check=True)
        binaries[label] = binary
    # Pin both variants to the same available CPU. Alternate order to reduce
    # thermal/frequency drift; retain every sample, not only the best run.
    cpu = min(os.sched_getaffinity(0))
    os.sched_setaffinity(0, {cpu})
    report = {"compiler": subprocess.check_output(["g++", "--version"], text=True).splitlines()[0],
              "flags": "-O3 -DNDEBUG -g -std=c++17", "cpu": cpu,
              "source_sha256": manifest, "workloads": {}}
    for mode, count in (("dense", args.dense), ("sparse", args.sparse)):
        samples = {name: [] for name in binaries}
        reference = None
        for repeat in range(args.runs):
            for label in (("baseline", "candidate") if repeat % 2 == 0 else
                          ("candidate", "baseline")):
                output = subprocess.check_output([str(binaries[label]), str(count), mode], text=True)
                sample = json.loads(output)
                signature = {k: v for k, v in sample.items() if k != "seconds"}
                if reference is None:
                    reference = signature
                if signature != reference:
                    raise RuntimeError(f"Cycle/state mismatch: {label} {sample} != {reference}")
                samples[label].append(sample["seconds"])
        medians = {label: statistics.median(values) for label, values in samples.items()}
        report["workloads"][mode] = {"signature": reference, "samples_seconds": samples,
                                      "median_seconds": medians,
                                      "speedup": medians["baseline"] / medians["candidate"]}
        print(mode, report["workloads"][mode])
    (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
