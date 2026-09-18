"""Gate 6 prestart-cancel acceptance on the real reservation-wait entry.

Runs the real dummy-core stack (MeshDispatcher + MeshDummyCore + weight cache
+ event queue) on the tight cached scenario.  The cancel branch observes a live
reservation wait, applies the dispatcher's existing prestart cancel entry and
then proves the simulation advances past the original watchdog horizon with no
residue and no late start / command issue.  The recovery branch uses a
recoverable temporary occupation so the same wait resolves and the batch starts
exactly once.

Dispatcher evidence only: KV rollback authority is produced by
ServingMeshExecutor tests and is reported separately.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
GEM5 = REPO / "build/AXI_MESH/gem5.opt"
PROBE = REPO / "tests/gem5/ai_mesh/gate6/cancel_probe.py"
TIGHT = REPO / "tests/gem5/ai_mesh/fixtures/gate6/moe_multi_tight"
ARCH = REPO / "configs/example/ai_mesh/arch/mesh_1x2_moe_tight.yaml"
OVERLAY = ",".join(str(REPO / "tests/gem5/ai_mesh/fixtures/gate5" / name)
                   for name in ("moe_multi_l1_overlay_cached.bin",
                                "moe_multi_l2_overlay_cached.bin"))
WATCHDOG_TICKS = 4000000
TIGHT_SHA256 = "6444385c03c7c45c978a5988b97ecaba5022a04e4c00849e863dac1528684573"


REUSE = REPO / "tests/gem5/ai_mesh/fixtures/gate6/moe_dual_cached"
REUSE_ARCH = REPO / "configs/example/ai_mesh/arch/mesh_1x2_moe.yaml"
REUSE_OVERLAY = str(REPO / "tests/gem5/ai_mesh/fixtures/gate5"
                    / "moe_dual_overlay_cached.bin")


def base_argv(case: str, outdir: Path, tick_limit: int,
              program_dir: Path = TIGHT, arch: Path = ARCH,
              overlay: str = OVERLAY) -> list[str]:
    return [
        str(GEM5), "--outdir=" + str(outdir), str(PROBE),
        "--case=" + case, "--master-seed=20260901",
        "--sim-tick-limit=" + str(tick_limit),
        "--mesh-program-dir", str(program_dir), "--arch", str(arch),
        "--overlay-image", overlay, "--weight-policy", "cached",
        "--watchdog-ticks", str(WATCHDOG_TICKS),
    ]


def run(case: str, outdir: Path, env_extra: dict[str, str],
        tick_limit: int, program_dir: Path = TIGHT, arch: Path = ARCH,
        overlay: str = OVERLAY) -> tuple[int, Path, Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    evidence = outdir / "cancel_evidence.json"
    env = dict(os.environ)
    for name in ("AI_MESH_CHILD_REPORT", "AI_MESH_CASE_ID", "AI_MESH_SUBCASE"):
        env.pop(name, None)
    env["AI_MESH_ARTIFACT_DIR"] = str(outdir)
    env["GATE6_CANCEL_EVIDENCE"] = str(evidence)
    env.update(env_extra)
    with (outdir / "console.log").open("w") as handle:
        process = subprocess.run(
            base_argv(case, outdir, tick_limit, program_dir, arch, overlay),
            env=env,
                                 stdout=handle, stderr=subprocess.STDOUT,
                                 timeout=1800)
    return process.returncode, evidence, outdir / "actual_result.json"


def cancel_branch(outdir: Path) -> dict:
    failures: list[str] = []
    wait_timeout = 2 * WATCHDOG_TICKS
    observe = 2 * WATCHDOG_TICKS
    code, evidence_path, result_path = run(
        "moe_multi_cached", outdir,
        {"GATE6_CANCEL_WAIT_TIMEOUT": str(wait_timeout),
         "GATE6_CANCEL_OBSERVE": str(observe)},
        tick_limit=wait_timeout + observe + WATCHDOG_TICKS)
    console = (outdir / "console.log").read_text()
    if "MESH_WATCHDOG" in console:
        failures.append("progress watchdog fired after the cancel")
    if not evidence_path.exists():
        cause = next((line for line in console.splitlines()
                      if line.startswith("Exiting @")), "no exit cause")
        return {"branch": "cancel", "pass": False,
                "failures": failures +
                ["cancel driver wrote no evidence (" + cause + ")"]}
    evidence = json.loads(evidence_path.read_text())
    for key in ("wait_observed", "cancel_issued", "advanced_past_cancel"):
        if not evidence[key]:
            failures.append(f"{key} is false")
    if evidence["observe_tick"] - evidence["cancel_tick"] < WATCHDOG_TICKS:
        failures.append("observation horizon did not pass the watchdog bound")
    if evidence["wait_attempts"] < 1:
        failures.append("wait window was never retried against the real cache")
    if not evidence["wait_window_clean"]:
        failures.append("reservation window left residue after the cancel")
    if not evidence["no_late_start"]:
        failures.append("cancelled instance started after the cancel")
    if not evidence["no_late_issue"]:
        failures.append("cancelled instance issued commands after the cancel")
    if evidence["failure"]:
        failures.append("driver failure: " + evidence["failure"])
    if code != 0:
        failures.append(f"gem5 exit code {code}")
    if not result_path.exists():
        failures.append("dispatcher wrote no snapshot")
    else:
        result = json.loads(result_path.read_text())
        reservation = result.get("cache_reservation") or {}
        if reservation.get("pending"):
            failures.append("dispatcher snapshot kept pending reservations")
        if reservation.get("waiting"):
            failures.append("dispatcher snapshot still reports a live wait")
        if reservation.get("starts"):
            failures.append("dispatcher snapshot reports a start")
        if result.get("watchdog_fired"):
            failures.append("dispatcher snapshot counts a watchdog firing")
        issued = [row["commands_issued"] for row in result.get("cores", [])]
        if any(issued):
            failures.append(f"cores issued commands: {issued}")
    return {"branch": "cancel", "pass": not failures,
            "failures": failures, "evidence": evidence}


def recovery_branch(outdir: Path) -> dict:
    failures: list[str] = []
    probe_env = {"GATE6_CANCEL_WAIT_TIMEOUT": str(WATCHDOG_TICKS),
                 "GATE6_CANCEL_OBSERVE": str(2 * WATCHDOG_TICKS),
                 "GATE6_CANCEL_ON_WAIT": "0",
                 "GATE6_CANCEL_EXIT_CAUSE": "PRESTART_WAIT_OBSERVED"}
    code, evidence_path, result_path = run(
        "moe_dual_cached_reuse", outdir, probe_env,
        tick_limit=6 * WATCHDOG_TICKS, program_dir=REUSE, arch=REUSE_ARCH,
        overlay=REUSE_OVERLAY)
    if not evidence_path.exists():
        console = (outdir / "console.log").read_text()
        cause = next((line for line in console.splitlines()
                      if line.startswith("Exiting @")), "no exit cause")
        return {"branch": "recovery", "pass": False,
                "failures": ["recovery driver wrote no evidence; the "
                             "simulation ended without a reservation wait "
                             "(" + cause + ")"]}
    evidence = json.loads(evidence_path.read_text())
    if not evidence["wait_observed"]:
        failures.append("recovery never observed a reservation wait")
    if code != 0:
        failures.append(f"gem5 exit code {code}")
    if evidence["reservation_commits"] < 1:
        failures.append("recovery never committed the reservation")
    if evidence["reservation_waiting"]:
        failures.append("recovery wait had not resolved at the horizon")
    delta = evidence["starts_after_cancel"] - evidence["starts_before_cancel"]
    if evidence["generation_after_cancel"] == evidence["cancelled_generation"]:
        if delta != 1:
            failures.append(
                f"waited instance started {delta} times instead of once")
    result = json.loads(result_path.read_text()) if result_path.exists() else {}
    reservation = result.get("cache_reservation") or {}
    if not reservation.get("commits"):
        failures.append("dispatcher snapshot reports no committed reservation")
    if reservation.get("pending"):
        failures.append("recovery left pending reservations")
    if reservation.get("waiting"):
        failures.append("dispatcher snapshot still reports a live wait")
    return {"branch": "recovery", "pass": not failures,
            "failures": failures, "evidence": evidence,
            "cache_reservation": reservation}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--outdir", required=True)
    parser.add_argument("--branch", default="cancel")
    arguments = parser.parse_args()
    digest = hashlib.sha256((TIGHT / "program.mshb").read_bytes()).hexdigest()
    if digest != TIGHT_SHA256:
        print(json.dumps({"pass": False,
                          "failures": ["tight fixture digest drifted"]},
                         indent=2))
        return 1
    outdir = Path(arguments.outdir)
    if arguments.branch == "cancel":
        summary = cancel_branch(outdir)
    else:
        summary = recovery_branch(outdir)
    (outdir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "evidence"},
                     indent=2))
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
