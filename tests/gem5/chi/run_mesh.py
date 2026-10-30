#!/usr/bin/env python3
"""Execute both real harts, then restore their warm CHI checkpoint."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


def main():
    repo = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gem5", type=Path, default=repo / "build/RISCV/gem5.opt")
    parser.add_argument("--out", type=Path, default=repo / "m5out/chi-mesh-regression")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    output = args.out.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Output must be empty")
    output.mkdir(parents=True, exist_ok=True)
    elf = output / "mesh-smoke.elf"
    subprocess.run([
        "riscv64-linux-gnu-gcc", "-nostdlib", "-nostartfiles", "-static",
        "-march=rv64imac_zicsr", "-mabi=lp64", "-Wl,--build-id=none",
        "-T", str(repo / "tests/gem5/chi/mesh_smoke.ld"),
        str(repo / "tests/gem5/chi/mesh_smoke.S"), "-o", str(elf),
    ], check=True)
    common = [str(args.gem5.resolve()), "--listener-mode=off"]
    config = [str(repo / "configs/example/chi_mesh_linux.py"), "--kernel", str(elf),
              "--bare-test", "--slc-sets", "4", "--sf-sets", "4",
              "--rnf-lines", "16",
              "--max-ticks", "20000000000"]
    results = {}
    for label in ("cold", "restore"):
        folder = output / label
        folder.mkdir()
        command = common + ["-d", str(folder)] + config
        if label == "restore":
            command += ["--restore", results["cold"]["checkpoint"]]
        with (folder / "run.log").open("w") as log:
            run = subprocess.run(command, cwd=repo, stdout=log,
                                 stderr=subprocess.STDOUT, timeout=args.timeout)
        if run.returncode:
            raise RuntimeError(f"{label} failed; see {folder / 'run.log'}")
        result = json.loads((folder / "result.json").read_text())
        terminal = (folder / "system.platform.terminal").read_text()
        if (result["status"] != "guest_exit" or result["code"] != 0 or
                "CHI_MESH_SMOKE_PASS" not in terminal or "FAIL" in terminal):
            raise RuntimeError(f"{label} failed the guest memory/atomic checks")
        results[label] = result
        if label == "cold":
            stats = (folder / "stats.txt").read_text()
            for node in ("rnfs0", "rnfs1"):
                for counter in ("readUnique", "writebacks", "atomicOps", "storeConditionals"):
                    match = re.search(r"^system\." + node + r"\." + counter +
                                      r"\s+(\d+)", stats, re.M)
                    if not match or int(match[1]) == 0:
                        raise RuntimeError(f"Smoke did not exercise {node}.{counter}")
    # Both runs start the post-checkpoint portion from the same drained
    # clocks, caches, directory and arbiter order. Check modeled time too.
    if results["cold"]["end_tick"] != results["restore"]["end_tick"]:
        raise RuntimeError("Restored mesh changed the post-checkpoint finish tick")
    results["passed"] = True
    results["elf_sha256"] = hashlib.sha256(elf.read_bytes()).hexdigest()
    results["scope"] = "two real harts, dirty eviction, remote reads, AMO/LRSC, warm restore"
    (output / "report.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
