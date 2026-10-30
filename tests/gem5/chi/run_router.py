#!/usr/bin/env python3
"""Run real CHI ports against single/dual-lane router configurations."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parents[3]
RESULT = re.compile(r"CHI_ROUTER_RESULT scenario=(\w+) flits=(\d+) cycles=(\d+) digest=(\d+)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gem5", type=Path, default=ROOT / "build/RISCV/gem5.opt")
    parser.add_argument("--out", type=Path, default=ROOT / "m5out/chi-router-regression")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--scenario", action="append", choices=["timing", "alias", "congestion", "idle", "throughput"])
    parser.add_argument("--check-event-order", action="store_true", help="Repeat every case with endpoint/router same-tick order reversed")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    scenarios = args.scenario or ["timing", "alias", "congestion", "idle", "throughput"]
    cases = [(s, dual, split, False) for s in scenarios for dual in (False, True) for split in (False, True)]
    cases += [(s, dual, split, True) for s in scenarios if s in ("congestion", "throughput") for dual in (False, True) for split in (False, True)]
    cases = [(*case, reverse) for case in cases for reverse in ((False, True) if args.check_event_order else (False,))]

    def run(case):
        scenario, dual, split, two_hop, reverse = case
        name = f"{scenario}-{'dual' if dual else 'single'}-{'voq' if split else 'fifo'}-{'two' if two_hop else 'one'}"
        base_name = name
        if reverse: name += "-reversed"
        out = args.out / name
        out.mkdir(parents=True, exist_ok=True)
        command = [str(args.gem5.resolve()), "-d", str(out.resolve()), str(ROOT / "configs/example/chi_router.py"),
                   "--scenario", scenario, "--repetitions", "8" if scenario == "idle" else "128", "--trace", str((out / "trace.csv").resolve())]
        if dual: command.append("--dual")
        if split: command.append("--per-output")
        if two_hop: command.append("--two-hop")
        if reverse: command.append("--reverse-event-order")
        start = time.perf_counter()
        try:
            completed = subprocess.run(command, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
            log, code = completed.stdout, completed.returncode
        except subprocess.TimeoutExpired as error:
            log = error.stdout or b""
            if isinstance(log, bytes): log = log.decode(errors="replace")
            code = -1
        (out / "run.log").write_text(log)
        match = RESULT.search(log)
        result = {"case": name, "command": command, "returncode": code,
                  "base_case": base_name, "reverse_event_order": reverse,
                  "passed": code == 0 and match is not None, "wall_seconds": time.perf_counter() - start}
        if match:
            result.update(flits=int(match[2]), cycles=int(match[3]), digest=match[4])
        (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        print(f"{'PASS' if result['passed'] else 'FAIL'} {name}", flush=True)
        return result

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(run, cases))
    summary = {"passed": sum(r["passed"] for r in results), "total": len(results), "results": results}
    if args.check_event_order:
        reference = {r["base_case"]: r for r in results if not r["reverse_event_order"]}
        mismatches = [r["base_case"] for r in results if r["reverse_event_order"] and
                      any(r.get(field) != reference[r["base_case"]].get(field) for field in ("flits", "cycles", "digest"))]
        summary["event_order_mismatches"] = mismatches
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    raise SystemExit(0 if summary["passed"] == summary["total"] and not summary.get("event_order_mismatches") else 1)


if __name__ == "__main__":
    main()
