#!/usr/bin/env python3
"""Check a live LR/SC reservation across native and legacy CHI checkpoints."""

import argparse
import configparser
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[3]
SIGNATURE = re.compile(r"CHI_LRSC_CHECKPOINT first_sc=([01]) retry_sc=([01])")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def save(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n")


def legacy_copy(checkpoint, destination):
    """Remove just the formerly absent ISA reservation fields from a copy."""
    source_file = checkpoint / "m5.cpt"
    source_hash = sha256(source_file)
    original = source_file.read_text()
    state = configparser.ConfigParser(interpolation=None)
    state.read_string(original)
    reservation = int(state["system.cpu0.isa"]["load_reservation_addr"], 0)
    if (reservation == (1 << 64) - 1 or
            state["system.rnfs0"].getboolean("reservationValid") is not True or
            int(state["system.rnfs0"]["reservationAddress"], 0) != reservation):
        raise RuntimeError("Checkpoint did not capture matching live CPU/RNF reservations")

    section, removed, lines = "", {}, []
    for line in original.splitlines(keepends=True):
        if line.startswith("["):
            section = line.strip()[1:-1]
        if section.endswith(".isa") and line.startswith("load_reservation_addr="):
            removed[section] = line.partition("=")[2].strip()
        else:
            lines.append(line)
    if set(removed) != {"system.cpu0.isa", "system.cpu1.isa"}:
        raise RuntimeError(f"Expected exactly two ISA reservation fields, found {removed}")
    shutil.copytree(checkpoint, destination)
    (destination / "m5.cpt").write_text("".join(lines))
    if sha256(source_file) != source_hash:
        raise RuntimeError("Original checkpoint was modified")
    # Every backing/cache-state artifact is identical; no RNF data or guest
    # register is patched to choose the expected result.
    for source in checkpoint.rglob("*"):
        if source.is_file() and source != source_file:
            if sha256(source) != sha256(destination / source.relative_to(checkpoint)):
                raise RuntimeError(f"Checkpoint copy differs: {source}")
    return {"source": str(checkpoint), "copy": str(destination),
            "source_m5_cpt_sha256": source_hash,
            "legacy_m5_cpt_sha256": sha256(destination / "m5.cpt"),
            "removed_fields": removed, "live_reservation": reservation}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gem5", type=Path, default=ROOT / "build/RISCV/gem5.opt")
    parser.add_argument("--out", type=Path, default=ROOT / "m5out/chi-lrsc-checkpoint")
    parser.add_argument("--compiler", default="riscv64-linux-gnu-gcc")
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    output = args.out.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Output directory must be empty")
    if args.timeout <= 0:
        parser.error("Timeout must be positive")
    output.mkdir(parents=True, exist_ok=True)
    report = {"status": "running", "runs": {}, "scope":
              "Two-hart CHI mesh with sleeping hart 1; checkpoint between LR.D and SC.D"}
    report_file = output / "report.json"
    save(report_file, report)
    try:
        binary = args.gem5.resolve()
        source = ROOT / "tests/gem5/chi/mesh_lrsc_checkpoint.S"
        linker = ROOT / "tests/gem5/chi/mesh_smoke.ld"
        config = ROOT / "configs/example/chi_mesh_linux.py"
        elf = output / "mesh-lrsc-checkpoint.elf"
        compile_command = [args.compiler, "-nostdlib", "-nostartfiles", "-static",
                           "-march=rv64imac_zicsr", "-mabi=lp64", "-Wl,--build-id=none",
                           "-T", str(linker), str(source), "-o", str(elf)]
        with (output / "build.log").open("w") as log:
            subprocess.run(compile_command, check=True, cwd=ROOT, stdout=log,
                           stderr=subprocess.STDOUT, timeout=args.timeout)
        report["compile_command"] = compile_command
        inputs = {"binary": binary, "config": config, "source": source,
                  "linker_script": linker, "elf": elf}
        report["inputs"] = {key: {"path": str(path), "sha256": sha256(path)}
                            for key, path in inputs.items()}
        checkpoint = None
        for label, expected in (("cold", 0), ("restore", 0), ("legacy", 1)):
            folder = output / label
            folder.mkdir()
            command = [str(binary), "--listener-mode=off", "-d", str(folder),
                       str(config), "--kernel", str(elf), "--bare-test",
                       "--slc-sets", "4", "--sf-sets", "4", "--rnf-lines", "64",
                       "--max-ticks", "1000000000"]
            if label == "restore":
                command += ["--restore", str(checkpoint)]
            elif label == "legacy":
                legacy = output / "legacy-cpt"
                report["legacy_copy"] = legacy_copy(checkpoint, legacy)
                command += ["--restore", str(legacy)]
            record = {"command": command, "expected_first_sc": expected,
                      "log": str(folder / "run.log")}
            report["runs"][label] = record
            save(report_file, report)
            with (folder / "run.log").open("w") as log:
                completed = subprocess.run(command, cwd=ROOT, stdout=log,
                                           stderr=subprocess.STDOUT, timeout=args.timeout)
            record["returncode"] = completed.returncode
            if completed.returncode:
                raise RuntimeError(f"{label} simulation failed; inspect {folder / 'run.log'}")
            result = json.loads((folder / "result.json").read_text())
            console = (folder / "system.platform.terminal").read_text()
            matches = SIGNATURE.findall(console)
            record["result"] = result
            record["signatures"] = matches
            if (result.get("status") != "guest_exit" or result.get("code") != 0 or
                    matches != [(str(expected), "0")] or "FAIL" in console):
                raise RuntimeError(f"{label} failed LR/SC guest assertions")
            if label == "cold":
                checkpoint = Path(result["checkpoint"])
            record["passed"] = True
            save(report_file, report)
        cold = report["runs"]["cold"]["result"]
        restored = report["runs"]["restore"]["result"]
        if cold["end_tick"] != restored["end_tick"]:
            raise RuntimeError("Native restore changed the post-checkpoint finish tick")
        if any(sha256(path) != report["inputs"][key]["sha256"]
               for key, path in inputs.items()):
            raise RuntimeError("A test input changed during execution")
        report["native_finish_tick_equal"] = True
        report["status"] = "passed"
    except (Exception, KeyboardInterrupt) as error:
        report["status"] = "failed"
        report["error"] = str(error) or type(error).__name__
    save(report_file, report)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
