#!/usr/bin/env python3
"""Reproducible CPU and timing-port regression; fail on missing path evidence."""
import argparse
import csv
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def stats(path):
    out = {}
    for line in path.read_text().splitlines():
        fields = line.split()
        if len(fields) > 1:
            try:
                out[fields[0]] = float(fields[1])
            except ValueError:
                pass
    return out


def run_case(
    root,
    name,
    arguments,
    expected_failure=False,
    directed=False,
    trace=False,
    timeout=3600,
):
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    cmd = [str(REPO / "build/RISCV/gem5.opt"), "--outdir=" + str(directory)]
    if trace:
        cmd += ["--debug-flags=DmaPath", "--debug-file=path.trace"]
    cmd += [
        "tests/gem5/dma/directed.py"
        if directed
        else "configs/example/chi_dma.py"
    ]
    cmd += list(map(str, arguments))
    print("RUN", name, flush=True)
    with (directory / "command.json").open("w") as f:
        json.dump(cmd, f, indent=2)
    with (directory / "run.log").open("w") as f:
        process = subprocess.run(
            cmd, cwd=REPO, stdout=f, stderr=subprocess.STDOUT, timeout=timeout
        )
    log = (directory / "run.log").read_text()
    if expected_failure:
        assert process.returncode != 0 and "PASS" not in log, (
            name,
            log[-2000:],
        )
        assert (
            "FAIL timeout" in log
            or "simulate() limit reached" in log
            or "NotImplementedError" in log
        )
        if name.startswith("cut-"):
            raw = stats(directory / "stats.txt")
            assert raw["system.rnf_uncached.chiTransactions"] > 0
            if "hni" in arguments:
                assert (
                    raw["system.hni.packets"]
                    == raw["system.dma.accesses"]
                    == 0
                )
            else:
                assert raw["system.hni.packets"] > 0
                assert raw["system.rni.packets"] > 0
                assert raw["system.rni.chiTransactions"] > 0
        return [], {
            "name": name,
            "expected_failure": True,
            "returncode": process.returncode,
        }
    assert process.returncode == 0 and "PASS" in log, (name, log[-4000:])
    result = json.loads((directory / "result.json").read_text())
    raw = stats(directory / "stats.txt")
    if directed:
        prefix = "system.test_adapter."
        assert raw[prefix + "entries"] == raw[prefix + "outstanding"] == 0
        assert raw[prefix + "respRetry"] > 0
        if "--credits" in arguments:
            assert raw["system.hni.creditStalls"] > 0
            assert raw["system.dma.jobs"] == 0
            assert (
                raw["system.dma.accesses"]
                == raw["system.hni.pioRequests"]
                == 256
            )
        elif "--io" in arguments:
            assert raw["system.hni.reqRetry"] > 0
            assert raw["system.dma.jobs"] == 1
            assert (
                raw["system.dma.accesses"]
                == raw["system.hni.pioRequests"]
                == 10
            )
        else:
            assert raw[prefix + "reqRetry"] > 0
            assert raw[prefix + "reorderedData"] > 0
            assert int(re.search(r"reordered=(\d+)", log).group(1)) > 0
    else:
        assert all(
            result[k]
            for k in (
                "rni_idle",
                "hni_idle",
                "rnf_uncached_idle",
                "dma_idle",
                "hnf_idle",
            )
        )
        assert (
            raw["system.rnf_uncached.pioRequests"]
            == raw["system.hni.packets"]
            == raw["system.hni.pioRequests"]
            == raw["system.dma.accesses"]
            > 0
        )
        if result["mode"] != "mmio":
            assert (
                raw["system.rni.packets"] > 0
                and raw["system.rni.chiTransactions"] > 0
            )
            if result["mode"] != "dma_error":
                assert (
                    raw["system.rni.readBytes"]
                    == raw["system.rni.writeBytes"]
                    == raw["system.dma.bytesWritten"]
                    > 0
                )
        for prefix in ("system.rni.", "system.hni.", "system.rnf_uncached."):
            assert raw[prefix + "entries"] == raw[prefix + "outstanding"] == 0
        assert raw["system.dma.activeChunks"] == 0
        if "--stalls" in arguments:
            assert (
                raw["system.hni.reqRetry"] > 0
                and raw["system.rni.respRetry"] > 0
            )
        if "--reverse-data" in arguments:
            assert raw["system.rni.reorderedData"] > 0
    rows = []
    for mode, length, tasks, cycles in re.findall(
        r"BENCH,([^,\n]+),(\d+),(\d+),(\d+)", log
    ):
        length, tasks, cycles = int(length), int(tasks), int(cycles)
        assert cycles > 0, "rdcycle must measure actual simulated cycles"
        seconds = cycles / 1e9  # Factory's CPU/system clock is 1 GHz.
        effective = length * tasks if mode in ("dma_copy", "mixed") else 0
        rows.append(
            {
                "case": name,
                "mode": mode,
                "length": length,
                "chunk_bytes": result.get("chunk_bytes", 0),
                "parallel_chunks": result.get("parallel_chunks", 0),
                "tasks": tasks,
                "cycles": cycles,
                "mean_latency_ns": cycles / tasks,
                "tasks_per_s": tasks / seconds,
                "effective_bytes_per_s": effective / seconds,
                "register_accesses_per_s": tasks
                * (2 if mode == "mmio_write_read" else 1)
                / seconds
                if mode.startswith("mmio")
                else 0,
            }
        )
    evidence = {
        k: v
        for k, v in raw.items()
        if k.startswith(
            (
                "system.dma.",
                "system.rni.",
                "system.hni.",
                "system.test_adapter.",
            )
        )
        and "power_state" not in k
    }
    return rows, {"name": name, "result": result, "stats": evidence}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--smoke", action="store_true")
    p.add_argument("--skip-build", action="store_true")
    p.add_argument("--output", default="results/dma-baseline/regression")
    p.add_argument("--jobs", type=int, default=2)
    p.add_argument(
        "--timeout",
        type=int,
        default=3600,
        help="Per-case host wall-clock limit; never used for bandwidth",
    )
    p.add_argument(
        "--cases", help="Optional regular expression selecting case names"
    )
    a = p.parse_args()
    assert a.jobs > 0 and a.timeout > 0
    root = REPO / a.output
    root.mkdir(parents=True, exist_ok=True)
    if not a.skip_build:
        targets = ["build/RISCV/gem5.opt"]
        if not a.smoke:
            targets.append(
                "build/RISCV/mem/cache/CHI/hnf_coherency_controller.test.opt"
            )
        with (root / "build.log").open("w") as f:
            subprocess.run(
                [
                    "scons",
                    *targets,
                    "--ignore-style",
                    "--linker=gold",
                    "-j32",
                ],
                cwd=REPO,
                stdout=f,
                stderr=subprocess.STDOUT,
                check=True,
            )
        subprocess.run(
            ["make", "-C", "tests/test-progs/simple_dma"], cwd=REPO, check=True
        )
    if not a.smoke:
        with (root / "hnf-unit.log").open("w") as f:
            subprocess.run(
                [
                    str(
                        REPO
                        / "build/RISCV/mem/cache/CHI/hnf_coherency_controller.test.opt"
                    )
                ],
                cwd=REPO,
                stdout=f,
                stderr=subprocess.STDOUT,
                check=True,
            )
    cases = [("smoke64", [], False, False, True)]
    if not a.smoke:
        cases += [
            ("directed-rni", [], False, True, True),
            ("directed-io", ["--io"], False, True, True),
            ("directed-credits", ["--credits"], False, True, True),
            ("o3-64", ["--cpu", "o3"], False, False, False),
        ]
        for parallel in [1, 4]:
            for length in [64, 4096, 1048576]:
                cases.append(
                    (
                        f"copy-{length}-p{parallel}",
                        [
                            "--length",
                            length,
                            "--parallel-chunks",
                            parallel,
                            "--chunk-bytes",
                            256,
                        ],
                        False,
                        False,
                        False,
                    )
                )
            for length in [1, 3, 31, 33, 63, 65, 127, 129]:
                cases.append(
                    (
                        f"boundary-{length}-p{parallel}",
                        [
                            "--length",
                            length,
                            "--parallel-chunks",
                            parallel,
                            "--chunk-bytes",
                            33,
                        ],
                        False,
                        False,
                        False,
                    )
                )
            cases.append(
                (
                    f"errors-p{parallel}",
                    ["--mode", "errors", "--parallel-chunks", parallel],
                    False,
                    False,
                    False,
                )
            )
            cases.append(
                (
                    f"mixed-p{parallel}",
                    [
                        "--mode",
                        "mixed",
                        "--length",
                        64,
                        "--iterations",
                        16,
                        "--parallel-chunks",
                        parallel,
                    ],
                    False,
                    False,
                    False,
                )
            )
        cases += [
            (
                "repeat-stalls",
                [
                    "--length",
                    129,
                    "--iterations",
                    3,
                    "--parallel-chunks",
                    4,
                    "--chunk-bytes",
                    31,
                    "--rni-entries",
                    2,
                    "--hnf-entries",
                    8,
                    "--stalls",
                    "--reverse-data",
                ],
                False,
                False,
                True,
            ),
            (
                "dma-error",
                [
                    "--mode",
                    "dma_error",
                    "--dma-error",
                    "--parallel-chunks",
                    4,
                    "--chunk-bytes",
                    33,
                ],
                False,
                False,
                False,
            ),
            (
                "mmio",
                ["--mode", "mmio", "--iterations", 64],
                False,
                False,
                False,
            ),
            (
                "cut-hni",
                ["--cut-path", "hni", "--max-ticks", 2000000000],
                True,
                False,
                False,
            ),
            (
                "cut-rni-timeout",
                ["--cut-path", "rni", "--poll-limit", 4],
                True,
                False,
                False,
            ),
            ("rtl-unimplemented", ["--backend", "rtl"], True, False, False),
        ]
    if a.cases:
        cases = [c for c in cases if re.search(a.cases, c[0])]
    # Short cases produce useful evidence first; the two 1 MiB CPU runs can
    # then run independently in parallel without sharing any simulated state.
    cases.sort(key=lambda c: 1048576 in c[1])
    assert cases, "No matching cases"
    rows, results, failures = [], [], []
    if a.cases and (root / "summary.json").exists():
        # A focused repair preserves the other cases' actual run evidence.
        # A full invocation always starts a fresh matrix.
        selected = {c[0] for c in cases}
        results = [r for r in json.loads((root / "summary.json").read_text())
                   if r["name"] not in selected]
        if (root / "benchmark.csv").exists():
            with (root / "benchmark.csv").open(newline="") as f:
                rows = [r for r in csv.DictReader(f) if r["case"] not in selected]
    with ThreadPoolExecutor(max_workers=a.jobs) as pool:
        futures = {
            pool.submit(run_case, root, *c, timeout=a.timeout): c[0]
            for c in cases
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                new_rows, result = future.result()
                rows += new_rows
                results.append(result)
                print("PASS", name, flush=True)
            except Exception as exc:
                failures.append(name)
                results.append(
                    {"name": name, "failed": True, "error": str(exc)}
                )
                print("FAIL", name, str(exc), flush=True)
            results.sort(key=lambda r: r["name"])
            (root / "summary.json").write_text(
                json.dumps(results, indent=2) + "\n"
            )
            if rows:
                with (root / "benchmark.csv").open("w", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                    writer.writeheader()
                    writer.writerows(
                        sorted(rows, key=lambda r: (r["case"], r["mode"]))
                    )
    if failures:
        raise SystemExit(f"FAIL {failures}; evidence: {root}")
    print(f"PASS {len(cases)} cases; evidence: {root}")


if __name__ == "__main__":
    main()
