#!/usr/bin/env python3
"""Exercise live Sv39/PMP/instret state through native CHI mesh restore."""
import argparse
import json
from pathlib import Path
import subprocess

from run_mesh_lrsc_checkpoint import sha256

ROOT = Path(__file__).resolve().parents[3]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gem5", type=Path, default=ROOT / "build/RISCV/gem5.opt")
    parser.add_argument("--out", type=Path, default=ROOT / "m5out/chi-mmu-checkpoint")
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        parser.error("Output directory must be empty")
    out.mkdir(parents=True, exist_ok=True)
    source = ROOT / "tests/gem5/chi/mesh_mmu_checkpoint.S"
    linker = ROOT / "tests/gem5/chi/mesh_smoke.ld"
    config = ROOT / "configs/example/chi_mesh_linux.py"
    binary = args.gem5.resolve()
    elf = out / "mesh-mmu-checkpoint.elf"
    with (out / "build.log").open("w") as log:
        subprocess.run(["riscv64-linux-gnu-gcc", "-nostdlib", "-nostartfiles",
                        "-static", "-march=rv64imac_zicsr", "-mabi=lp64",
                        "-Wl,--build-id=none", "-T", str(linker), str(source),
                        "-o", str(elf)], stdout=log, stderr=subprocess.STDOUT,
                       check=True, timeout=120)
    inputs = {str(path): sha256(path) for path in (binary, config, source, linker, elf)}
    results = {}
    for label in ("cold", "restore"):
        folder = out / label
        folder.mkdir()
        command = [str(binary), "--listener-mode=off", "-d", str(folder),
                   str(config), "--kernel", str(elf), "--bare-test",
                   "--slc-sets", "4", "--sf-sets", "4", "--rnf-lines", "64",
                   "--max-ticks", "1000000000"]
        if label == "restore":
            command += ["--restore", results["cold"]["checkpoint"]]
        with (folder / "run.log").open("w") as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                           check=True, timeout=180)
        result = json.loads((folder / "result.json").read_text())
        console = (folder / "system.platform.terminal").read_text()
        if (result.get("status") != "guest_exit" or result.get("code") != 0 or
                "CHI_MMU_CHECKPOINT_PASS pmp_traps=2 instret_monotonic=1" not in console
                or "FAIL" in console):
            raise RuntimeError(f"{label} guest MMU/PMP/instret checks failed")
        results[label] = result
    if results["cold"]["end_tick"] != results["restore"]["end_tick"]:
        raise RuntimeError("Restoring live translation changed the finish tick")
    if any(sha256(Path(path)) != digest for path, digest in inputs.items()):
        raise RuntimeError("Test inputs changed during execution")
    results.update(passed=True, inputs=inputs, native_finish_tick_equal=True)
    (out / "report.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
