#!/usr/bin/env python3
"""Restore the login checkpoint, log in over localhost serial, check both CPUs."""
import argparse
import json
from pathlib import Path
import re
import socket
import subprocess
import time

from run_mesh_lrsc_checkpoint import sha256

ROOT = Path(__file__).resolve().parents[3]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gem5", type=Path, default=ROOT / "build/RISCV/gem5.opt")
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=240)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        parser.error("Output directory must be empty")
    out.mkdir(parents=True, exist_ok=True)
    checkpoint = args.checkpoint.resolve()
    before = {str(p.relative_to(checkpoint)): sha256(p)
              for p in checkpoint.rglob("*") if p.is_file()}
    log_path = out / "run.log"
    command = [str(args.gem5.resolve()), "--listener-mode=on", "--remote-gdb-port=0",
               "-d", str(out), str(ROOT / "configs/example/chi_mesh_linux.py"),
               "--kernel", str(args.kernel.resolve()), "--restore", str(checkpoint),
               "--max-ticks", "500000000000", "--exit-marker", "CHI_RESTORE_DONE"]
    deadline = time.monotonic() + args.timeout
    report = {"command": command, "checkpoint": str(checkpoint), "status": "running"}
    serial = bytearray()
    connection = None
    process = None
    try:
        with log_path.open("w") as log:
            process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            while time.monotonic() < deadline:
                match = re.search(r"system\.platform\.terminal: Listening for connections on port (\d+)",
                                  log_path.read_text(errors="replace"))
                if match:
                    connection = socket.create_connection(("127.0.0.1", int(match[1])), timeout=2)
                    connection.settimeout(0.2)
                    connection.sendall(b"root\n")
                    break
                if process.poll() is not None:
                    raise RuntimeError("gem5 exited before the serial listener was ready")
                time.sleep(0.05)
            if connection is None:
                raise RuntimeError("Serial listener timeout")
            sent = False
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    data = connection.recv(4096)
                    if data:
                        serial.extend(data)
                except socket.timeout:
                    pass
                # Root's interactive shell prints a '#' prompt. Send the
                # command only after login, not to getty's username input.
                if not sent and re.search(rb"(?:^|[\r\n])[^\r\n]*# ?$", serial):
                    connection.sendall(
                        b"printf 'CHI_RESTORE_ONLINE='; cat /sys/devices/system/cpu/online; "
                        b"printf 'CHI_RESTORE_KERNEL='; uname -r; echo CHI_RESTORE_\"DONE\"\n")
                    sent = True
            if process.poll() is None:
                raise RuntimeError("Restored Linux did not finish the serial checks before timeout")
            console = (out / "system.platform.terminal").read_text(errors="replace")
            result = json.loads((out / "result.json").read_text())
            if (process.returncode != 0 or not sent or result.get("status") != "console_marker" or
                    "CHI_RESTORE_ONLINE=0-1" not in console or
                    "CHI_RESTORE_KERNEL=6.10.7" not in console or
                    "\nCHI_RESTORE_DONE" not in console):
                raise RuntimeError("Restored Linux failed login/CPU/kernel assertions")
            after = {str(p.relative_to(checkpoint)): sha256(p)
                     for p in checkpoint.rglob("*") if p.is_file()}
            if before != after:
                raise RuntimeError("Restore modified the source checkpoint")
            report.update(status="passed", result=result, checkpoint_unchanged=True,
                          checkpoint_files=before, binary_sha256=sha256(args.gem5),
                          kernel_sha256=sha256(args.kernel))
    except Exception as error:
        report.update(status="failed", error=str(error))
    finally:
        if connection is not None:
            connection.close()
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        (out / "serial-client.log").write_bytes(serial)
        (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
