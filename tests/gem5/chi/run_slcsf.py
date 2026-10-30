#!/usr/bin/env python3
"""Run directed real-HNF endpoint tests; each scenario has its own watchdog."""

import argparse
import concurrent.futures
import json
import re
from pathlib import Path
import subprocess

SCENARIOS = (
    "cold_read", "ordered_read", "sf_stale_ordered",
    "slc_clean", "slc_dirty", "sf_clean", "sf_dirty",
    "both_read", "clean_invalid_miss", "clean_invalid_self", "clean_invalid_self_miss",
    "clean_invalid_clean", "clean_invalid_dirty",
    "clean_invalid_sf_dirty", "clean_invalid_both", "ns_isolation",
    "same_line", "offset_hazard", "tx_backpressure", "sf_eviction", "seq_hazard", "seq_broadcast_hazard", "seq_pressure",
)


def main():
    repo = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser()
    parser.add_argument("--gem5", type=Path, default=repo / "build/RISCV/gem5.opt")
    parser.add_argument("--out", type=Path, default=repo / "m5out/slcsf-regression")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--response-delay", type=int, default=3)
    parser.add_argument("--scenario", action="append", choices=SCENARIOS)
    parser.add_argument("--check-event-order", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    def run(case):
        scenario, early = case
        folder = args.out / (scenario + ("-early" if early else ""))
        folder.mkdir(parents=True, exist_ok=True)
        command = [str(args.gem5.resolve()), "--outdir=" + str(folder.resolve()),
                   str(repo / "configs/example/chi_hnf_slcsf.py"),
                   "--scenario=" + scenario,
                   "--response-delay=" + str(args.response_delay)]
        if early:
            command.append("--early-schedule")
        if scenario == "seq_pressure":
            command += ["--requests=1000", "--sf-sets=4", "--sf-ways=4",
                        "--max-cycles=50000"]
        with (folder / "run.log").open("w") as log:
            try:
                result = subprocess.run(command, stdout=log,
                                        stderr=subprocess.STDOUT, timeout=120)
                code = result.returncode
            except subprocess.TimeoutExpired:
                code = 124
        output = (folder / "run.log").read_text()
        passed = code == 0 and f"CHI_HNF_PASS scenario={scenario} " in output
        match = re.search(r"CHI_HNF_PASS (.+)", output)
        return {"scenario": scenario, "passed": passed, "returncode": code,
                "early_schedule": early, "signature": match[1] if match else None,
                "response_delay": args.response_delay,
                "log": str(folder / "run.log")}

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        cases = [(s, early) for s in (args.scenario or SCENARIOS)
                 for early in ((False, True) if args.check_event_order else (False,))]
        results = list(pool.map(run, cases))
    if args.check_event_order:
        for normal, early in zip(results[::2], results[1::2]):
            match = normal["signature"] is not None and normal["signature"] == early["signature"]
            normal["event_order_match"] = early["event_order_match"] = match
            normal["passed"] &= match
            early["passed"] &= match
    (args.out / "summary.json").write_text(json.dumps(results, indent=2) + "\n")
    for result in results:
        print(("PASS" if result["passed"] else "FAIL"), result["scenario"],
              "early" if result["early_schedule"] else "normal")
    raise SystemExit(0 if all(r["passed"] for r in results) else 1)


if __name__ == "__main__":
    main()
