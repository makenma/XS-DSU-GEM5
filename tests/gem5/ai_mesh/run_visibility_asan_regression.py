#!/usr/bin/env python3

import argparse
import os
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gem5", required=True)
    parser.add_argument("--workdir", required=True)
    args = parser.parse_args()
    gem5 = Path(args.gem5).resolve()
    workdir = Path(args.workdir).resolve()
    program_dir = workdir / "single"
    program_dir.mkdir(parents=True, exist_ok=True)
    build = subprocess.run(
        [
            sys.executable,
            "-m",
            "mesh_ir.cli",
            "build",
            "--program",
            "single",
            "--arch",
            str(REPO / "configs/example/ai_mesh/arch/mesh_1x2.yaml"),
            "--out",
            str(program_dir),
        ],
        cwd=REPO / "util/mesh_ir",
        capture_output=True,
        text=True,
    )
    if build.returncode:
        sys.stdout.write(build.stdout)
        sys.stderr.write(build.stderr)
        return build.returncode
    env = dict(os.environ)
    env["ASAN_OPTIONS"] = "detect_leaks=0:halt_on_error=1"
    run = subprocess.run(
        [
            str(gem5),
            "--outdir=" + str(workdir / "m5out"),
            str(REPO / "configs/example/ai_mesh/run_mesh_program.py"),
            "--program",
            "single",
            "--mesh-program-dir",
            str(program_dir),
            "--instances",
            "16",
            "--assert-event-visibility",
            "--assert-all-done",
        ],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
    )
    sys.stdout.write(run.stdout)
    sys.stderr.write(run.stderr)
    if run.returncode or "MESH_E2E_PASS" not in run.stdout:
        return run.returncode or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
