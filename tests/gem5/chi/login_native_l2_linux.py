#!/usr/bin/env python3
"""Boot or restore the merged 2x2 mesh and verify a root shell over serial."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import socket
import subprocess
import time

ROOT = Path(__file__).resolve().parents[3]


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gem5", type=Path,
                        default=ROOT / "build/RISCV/gem5.opt")
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--clock", default="100MHz")
    parser.add_argument("--restore", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=3600)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        parser.error("Output directory must be empty")
    out.mkdir(parents=True, exist_ok=True)
    command = [str(args.gem5.resolve()), "--listener-mode=on",
               "--remote-gdb-port=0", "-d", str(out),
               str(ROOT / "configs/example/chi_mesh_native_l2_linux.py"),
               "--kernel", str(args.kernel.resolve()),
               "--clock", args.clock,
               "--exit-marker", "CHI_MERGE_LOGIN_DONE"]
    if args.restore:
        command += ["--restore", str(args.restore.resolve())]
    report = {"command": command, "status": "running",
              "requested_clock": args.clock}
    binary_digest = sha256(args.gem5)
    kernel_digest = sha256(args.kernel)
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    log_path = out / "run.log"
    terminal = out / "system.platform.terminal"
    serial = bytearray()
    connection = None
    process = None
    started = time.monotonic()
    deadline = started + args.timeout
    try:
        with log_path.open("w") as log:
            process = subprocess.Popen(command, cwd=ROOT, stdout=log,
                                       stderr=subprocess.STDOUT)
            while time.monotonic() < deadline:
                match = re.search(
                    r"system\.platform\.terminal: Listening for "
                    r"connections on port (\d+)",
                    log_path.read_text(errors="replace"))
                if match:
                    connection = socket.create_connection(
                        ("127.0.0.1", int(match[1])), timeout=5)
                    connection.settimeout(0.5)
                    break
                if process.poll() is not None:
                    raise RuntimeError("gem5 exited before serial was ready")
                time.sleep(0.1)
            if connection is None:
                raise RuntimeError("Serial listener timeout")
            logged_in = sent = False
            if args.restore:
                # The login prompt was printed before the snapshot. Resume
                # the waiting getty rather than waiting for it to print again.
                connection.sendall(b"root\n")
                logged_in = True
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    data = connection.recv(16384)
                    if data:
                        serial.extend(data)
                except socket.timeout:
                    pass
                if not logged_in and b"gem5-chi login:" in serial:
                    connection.sendall(b"root\n")
                    logged_in = True
                if logged_in and not sent and re.search(
                        rb"(?:^|[\r\n])[^\r\n]*# ?$", serial):
                    connection.sendall(
                        b"printf 'CHI_MERGE_ONLINE='; "
                        b"cat /sys/devices/system/cpu/online; "
                        b"printf 'CHI_MERGE_KERNEL='; uname -r; "
                        b"printf 'CHI_MERGE_UID='; id -u; "
                        b"echo CHI_MERGE_LOGIN_\"DONE\"\n")
                    sent = True
                if terminal.exists() and "Kernel panic" in terminal.read_text(
                        errors="replace"):
                    raise RuntimeError("Linux kernel panic")
            if process.poll() is None:
                raise RuntimeError("Linux boot/login exceeded wall timeout")
            console = terminal.read_text(errors="replace")
            result = json.loads((out / "result.json").read_text())
            checks = {
                "gem5_exit_zero": process.returncode == 0,
                "root_login_sent": logged_in,
                "shell_command_sent": sent,
                "console_marker": result.get("status") == "console_marker",
                "two_online_cpus": "CHI_MERGE_ONLINE=0-1" in console,
                "linux_kernel": "CHI_MERGE_KERNEL=6.10.7" in console,
                "root_uid": "CHI_MERGE_UID=0" in console,
                "shell_completed": "\nCHI_MERGE_LOGIN_DONE" in console,
            }
            report["checks"] = checks
            if not all(checks.values()):
                raise RuntimeError("Linux login/CPU/kernel checks failed")
            config = json.loads((out / "config.json").read_text())
            system = config["system"]
            topology = {
                "routers": len(system["routers"]),
                "hnfs": len(system["hnfs"]),
                "cpus": len(system["cpu"]),
                "l2_types": [l2["type"] for l2 in system["l2s"]],
                "l2_mshrs": [l2["mshrs"] for l2 in system["l2s"]],
            }
            if topology != {"routers": 4, "hnfs": 2, "cpus": 2,
                            "l2_types": ["ChiNativeL2", "ChiNativeL2"],
                            "l2_mshrs": [32, 32]}:
                raise RuntimeError("Unexpected merged topology")
            if (sha256(args.gem5) != binary_digest or
                    sha256(args.kernel) != kernel_digest):
                raise RuntimeError("Binary or firmware changed during validation")
            report.update(status="passed", result=result, topology=topology,
                          clock_period_ticks=system["clk_domain"]["clock"],
                          binary_sha256=binary_digest,
                          kernel_sha256=kernel_digest)
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
        report["wall_seconds"] = time.monotonic() - started
        (out / "serial-client.log").write_bytes(serial)
        (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
