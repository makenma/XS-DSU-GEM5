#!/usr/bin/env python3
"""Save a real Linux login checkpoint and log in after native L2 restore."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

from login_native_l2_linux import ROOT, sha256
from run_native_l2_checkpoint import audit_checkpoint, checkpoint_hashes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gem5", type=Path,
                        default=ROOT / "build/RISCV/gem5.opt")
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--clock", default="100MHz")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--boot-timeout", type=float, default=3600)
    parser.add_argument("--restore-timeout", type=float, default=600)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        parser.error("Output directory must be empty")
    out.mkdir(parents=True, exist_ok=True)
    report = {"status": "running", "clock": args.clock}
    binary_digest = sha256(args.gem5)
    kernel_digest = sha256(args.kernel)
    try:
        if args.checkpoint:
            checkpoint = args.checkpoint.resolve()
        else:
            boot = out / "boot"
            boot.mkdir()
            command = [str(args.gem5.resolve()), "--listener-mode=off",
                       "--remote-gdb-port=0", "-d", str(boot),
                       str(ROOT / "configs/example/chi_mesh_native_l2_linux.py"),
                       "--kernel", str(args.kernel.resolve()),
                       "--clock", args.clock]
            report["boot_command"] = command
            (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
            with (boot / "run.log").open("w") as log:
                run = subprocess.run(command, cwd=ROOT, stdout=log,
                                     stderr=subprocess.STDOUT,
                                     timeout=args.boot_timeout)
            if run.returncode:
                raise RuntimeError("Cold boot/checkpoint failed; see boot/run.log")
            result = json.loads((boot / "result.json").read_text())
            assert result["status"] == "login_checkpoint"
            checkpoint = Path(result["checkpoint"])
            report["boot"] = result
        report["checkpoint"] = str(checkpoint)
        report["audit"] = audit_checkpoint(checkpoint)
        before = checkpoint_hashes(checkpoint)
        command = [sys.executable,
                   str(ROOT / "tests/gem5/chi/login_native_l2_linux.py"),
                   "--gem5", str(args.gem5.resolve()),
                   "--kernel", str(args.kernel.resolve()),
                   "--clock", args.clock, "--restore", str(checkpoint),
                   "--timeout", str(args.restore_timeout),
                   "--out", str(out / "restore")]
        report["restore_command"] = command
        (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        with (out / "restore-driver.log").open("w") as log:
            run = subprocess.run(command, cwd=ROOT, stdout=log,
                                 stderr=subprocess.STDOUT,
                                 timeout=args.restore_timeout + 30)
        if run.returncode:
            raise RuntimeError("Restored Linux root login failed")
        restored = json.loads((out / "restore/report.json").read_text())
        assert restored["status"] == "passed"
        assert checkpoint_hashes(checkpoint) == before
        assert sha256(args.gem5) == binary_digest
        assert sha256(args.kernel) == kernel_digest
        report.update(status="passed", restore=restored,
                      checkpoint_unchanged=True, checkpoint_files=before,
                      binary_sha256=binary_digest, kernel_sha256=kernel_digest)
    except (Exception, KeyboardInterrupt) as error:
        report.update(status="failed", error=str(error) or type(error).__name__)
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
