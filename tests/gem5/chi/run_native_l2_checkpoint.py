#!/usr/bin/env python3
"""Verify warm native L2/L1 checkpoints by executing real guest programs."""

import argparse
import configparser
import json
from pathlib import Path
import shutil
import subprocess

from login_native_l2_linux import ROOT, sha256


def checkpoint_hashes(checkpoint):
    return {str(p.relative_to(checkpoint)): sha256(p)
            for p in checkpoint.rglob("*") if p.is_file()}


def audit_checkpoint(checkpoint, require_reservation=False, require_l2_guard=False):
    state = configparser.ConfigParser(interpolation=None)
    state.optionxform = str
    state.read(checkpoint / "m5.cpt")
    audit = {"l2": {}, "l1_valid": 0, "l1_dirty": 0, "l1_locks": 0}
    for node in ("system.l2s0", "system.l2s1"):
        assert state[node].getint("checkpointVersion") == 1
        lines = [state[f"{node}.line{i}"]
                 for i in range(state[node].getint("count"))]
        audit["l2"][node] = {
            field: sum(line.getboolean(field) for line in lines)
            for field in ("valid", "dirty", "upperWritable")
        }
    for cpu in range(2):
        for cache in ("icache", "dcache", "itb_walker_cache",
                      "dtb_walker_cache"):
            node = f"system.cpu{cpu}.{cache}"
            assert state[node].getint("warmCheckpointVersion") == 1
            tags = f"{node}.warmTags"
            for i in range(state[tags].getint("numBlocks")):
                block = state[f"{tags}.block{i}"]
                audit["l1_valid"] += block.getboolean("valid")
                audit["l1_dirty"] += bool(block.getint("coherence") & 8)
                audit["l1_locks"] += len(block["lockContexts"].split())
    assert sum(node["valid"] for node in audit["l2"].values()) > 0
    assert audit["l1_valid"] > 0
    assert audit["l1_dirty"] + sum(
        node["dirty"] for node in audit["l2"].values()) > 0
    if require_reservation:
        reservation = int(state["system.cpu0.isa"]["load_reservation_addr"])
        assert reservation != (1 << 64) - 1
        assert audit["l1_locks"] > 0
        audit["live_reservation"] = reservation
    if require_l2_guard:
        guards = dict(zip(
            map(int, state["system.l2s0"]["reservationAddresses"].split()),
            map(int, state["system.l2s0"]["reservationDeadlines"].split())))
        assert guards[reservation & ~63] > state["root.globals"].getint("curTick")
        audit["l2_reservation_guard"] = True
    return audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gem5", type=Path,
                        default=ROOT / "build/RISCV/gem5.opt")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--case", action="append",
                        choices=("mesh", "lrsc", "lrsc_miss", "mmu"))
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        parser.error("Output directory must be empty")
    out.mkdir(parents=True, exist_ok=True)
    cases = {
        "mesh": ("mesh_smoke.S", "CHI_MESH_SMOKE_PASS"),
        "lrsc": ("mesh_lrsc_checkpoint.S",
                 "CHI_LRSC_CHECKPOINT first_sc=0 retry_sc=0"),
        "lrsc_miss": ("mesh_lrsc_checkpoint.S",
                      "CHI_LRSC_CHECKPOINT first_sc=0 retry_sc=0"),
        "mmu": ("mesh_mmu_checkpoint.S",
                "CHI_MMU_CHECKPOINT_PASS pmp_traps=2 instret_monotonic=1"),
    }
    report = {"status": "running", "cases": {}}
    config = ROOT / "configs/example/chi_mesh_native_l2_linux.py"
    binary_digest = sha256(args.gem5)
    try:
        for label in args.case or cases:
            folder = out / label
            folder.mkdir()
            source, marker = cases[label]
            elf = folder / "guest.elf"
            subprocess.run([
                "riscv64-linux-gnu-gcc", "-nostdlib", "-nostartfiles", "-static",
                "-march=rv64imac_zicsr", "-mabi=lp64", "-Wl,--build-id=none",
                *(["-DCHI_LRSC_COLD_DATA"] if label == "lrsc_miss" else []),
                "-T", str(ROOT / "tests/gem5/chi/mesh_smoke.ld"),
                str(ROOT / "tests/gem5/chi" / source), "-o", str(elf),
            ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            runs = {}
            for run_label in ("cold", "restore"):
                run_out = folder / run_label
                run_out.mkdir()
                command = [str(args.gem5.resolve()), "--listener-mode=off",
                           "--remote-gdb-port=0", "-d", str(run_out), str(config),
                           "--kernel", str(elf), "--bare-test", "--l2-sets", "16",
                           "--l2-assoc", "4", "--slc-sets", "4", "--sf-sets", "4",
                           "--max-ticks", "20000000000"]
                if run_label == "restore":
                    command += ["--restore", str(checkpoint)]
                with (run_out / "run.log").open("w") as log:
                    result = subprocess.run(command, cwd=ROOT, stdout=log,
                                            stderr=subprocess.STDOUT,
                                            timeout=args.timeout)
                if result.returncode:
                    raise RuntimeError(f"{label}/{run_label} failed: {run_out}")
                data = json.loads((run_out / "result.json").read_text())
                console = (run_out / "system.platform.terminal").read_text()
                assert data.get("status") == "guest_exit" and data.get("code") == 0
                assert marker in console and "FAIL" not in console
                runs[run_label] = data
                if run_label == "cold":
                    checkpoint = Path(data["checkpoint"])
                    before = checkpoint_hashes(checkpoint)
                    audit = audit_checkpoint(checkpoint,
                                             label.startswith("lrsc"),
                                             label == "lrsc_miss")
            assert runs["restore"]["start_tick"] == runs["cold"]["checkpoint_tick"]
            assert runs["cold"]["end_tick"] == runs["restore"]["end_tick"]
            assert checkpoint_hashes(checkpoint) == before
            record = {"status": "passed", "runs": runs, "audit": audit,
                      "finish_tick_equal": True, "checkpoint_unchanged": True}
            report["cases"][label] = record
            (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
            # Geometry mismatch and absent cache state must fail explicitly.
            if label == "lrsc":
                invalid = folder / "missing-l2-version"
                shutil.copytree(checkpoint, invalid)
                cpt = invalid / "m5.cpt"
                state = configparser.ConfigParser(interpolation=None)
                state.optionxform = str
                state.read(cpt)
                del state["system.l2s0"]["checkpointVersion"]
                with cpt.open("w") as stream:
                    state.write(stream, space_around_delimiters=False)
                for name, source_cpt, sets, message in (
                    ("wrong-geometry", checkpoint, "8", "configuration changed"),
                    ("missing-state", invalid, "16", "checkpointVersion"),
                ):
                    bad_out = folder / name
                    bad_out.mkdir()
                    command = [str(args.gem5.resolve()), "--listener-mode=off",
                               "--remote-gdb-port=0", "-d", str(bad_out), str(config),
                               "--kernel", str(elf), "--bare-test", "--l2-sets", sets,
                               "--l2-assoc", "4", "--slc-sets", "4", "--sf-sets", "4",
                               "--restore", str(source_cpt)]
                    with (bad_out / "run.log").open("w") as log:
                        bad = subprocess.run(command, cwd=ROOT, stdout=log,
                                             stderr=subprocess.STDOUT,
                                             timeout=args.timeout)
                    assert bad.returncode != 0
                    assert message in (bad_out / "run.log").read_text()
                record["invalid_snapshots_rejected"] = True
        assert sha256(args.gem5) == binary_digest
        report.update(status="passed", binary_sha256=binary_digest)
    except (Exception, KeyboardInterrupt) as error:
        report.update(status="failed", error=str(error) or type(error).__name__)
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
