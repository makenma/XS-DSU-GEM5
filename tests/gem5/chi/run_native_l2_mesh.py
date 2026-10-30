#!/usr/bin/env python3
"""Cold two-hart coherence/eviction/AMO/LRSC regression for the merged L2."""

import argparse
import json
from pathlib import Path
import subprocess

from login_native_l2_linux import ROOT, sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gem5", type=Path,
                        default=ROOT / "build/RISCV/gem5.opt")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        parser.error("Output directory must be empty")
    out.mkdir(parents=True, exist_ok=True)
    # Reuse the real two-hart data checker without its warm checkpoint.
    source = (ROOT / "tests/gem5/chi/mesh_smoke.S").read_text()
    checkpoint = ".long 0x8600007b /* m5_checkpoint */"
    if source.count(checkpoint) != 1:
        raise RuntimeError("Unexpected checkpoint site in mesh_smoke.S")
    assembly = out / "mesh-native.S"
    assembly.write_text(source.replace(checkpoint, "nop /* cold-only */"))
    elf = out / "mesh-native.elf"
    subprocess.run([
        "riscv64-linux-gnu-gcc", "-nostdlib", "-nostartfiles", "-static",
        "-march=rv64imac_zicsr", "-mabi=lp64", "-Wl,--build-id=none",
        "-T", str(ROOT / "tests/gem5/chi/mesh_smoke.ld"),
        str(assembly), "-o", str(elf),
    ], check=True)
    command = [str(args.gem5.resolve()), "--listener-mode=off",
               "--remote-gdb-port=0", "-d", str(out),
               str(ROOT / "configs/example/chi_mesh_native_l2_linux.py"),
               "--kernel", str(elf), "--bare-test", "--l2-sets", "16",
               "--l2-assoc", "4", "--slc-sets", "4", "--sf-sets", "4",
               "--max-ticks", "20000000000"]
    report = {"command": command, "status": "running"}
    try:
        with (out / "run.log").open("w") as log:
            run = subprocess.run(command, cwd=ROOT, stdout=log,
                                 stderr=subprocess.STDOUT, timeout=args.timeout)
        result = json.loads((out / "result.json").read_text())
        console = (out / "system.platform.terminal").read_text()
        if (run.returncode or result.get("status") != "guest_exit" or
                result.get("code") != 0 or
                "CHI_MESH_SMOKE_PASS" not in console or "FAIL" in console):
            raise RuntimeError("Native L2 two-hart guest checks failed")
        report.update(status="passed", result=result,
                      binary_sha256=sha256(args.gem5), elf_sha256=sha256(elf))
    except Exception as error:
        report.update(status="failed", error=str(error))
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
