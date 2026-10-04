"""Build ordinary RV64 IRIW binaries for native_l2_run.py (no injected traffic)."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=["all", "iriw", "iriw-fenced"], default="all")
    parser.add_argument("--iterations", type=int, default=128)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    if not 1 <= args.iterations <= 1_000_000:
        parser.error("--iterations must be in 1..1000000")
    if not 0 <= args.seed <= 0xffffffff:
        parser.error("--seed must fit in an unsigned 32-bit integer")

    project = Path(__file__).resolve().parent.parent
    source = project / "native/gem5/native-l2-litmus.c"
    cc = os.environ.get("CHI_RISCV_CC", "riscv64-linux-gnu-gcc")
    toolchain = subprocess.check_output([cc, "--version"], text=True).splitlines()[0]
    flags = ["-g", "-O1", "-nostdlib", "-static", "-fno-builtin", "-fno-stack-protector",
             "-fno-pie", "-no-pie", "-march=rv64imafd", "-mabi=lp64d", "-mcmodel=medany",
             "-msmall-data-limit=0", "-mno-relax", "-Wl,--no-relax",
             "-Wl,-Ttext=0x10000", "-Wl,--build-id=none", "-Wall", "-Wextra", "-Werror"]
    objdump = os.environ.get("CHI_RISCV_OBJDUMP", "riscv64-linux-gnu-objdump")
    variants = ["iriw", "iriw-fenced"] if args.variant == "all" else [args.variant]
    for variant in variants:
        out = project / "build/native-litmus-program" / variant
        out.mkdir(parents=True, exist_ok=True)
        defines = [f"-DLITMUS_ITERATIONS={args.iterations}", f"-DLITMUS_SEED={args.seed}ULL",
                   f"-DLITMUS_FENCED={int(variant == 'iriw-fenced')}"]
        binaries = []
        assembly_checks = []
        for hart in range(4):
            binary = out / f"native-l2-{hart}.elf"
            subprocess.run([cc, *flags, *defines, f"-DHART={hart}", str(source), "-o", str(binary)], check=True)
            assembly = subprocess.check_output([objdump, "-d", "--no-show-raw-insn", str(binary)], text=True)
            (out / f"native-l2-{hart}.asm").write_text(assembly, encoding="utf-8")
            # _start deliberately has no CRT startup. None of the generated
            # instructions may depend on a runtime-initialized global pointer.
            if re.search(r"^\s*[0-9a-f]+:\s+[^\n]*\bgp\b", assembly, re.M):
                raise RuntimeError(f"hart {hart}: unexpected gp-dependent instruction")
            pairs = []
            for block in re.finditer(r"^([0-9a-f]+) <native_litmus_pair_begin_(\d+)>:\n(.*?)(?=^[0-9a-f]+ <native_litmus_pair_end_\2>:)", assembly, re.M | re.S):
                instructions = re.findall(r"^\s*[0-9a-f]+:\s+(\S+)(?:\s+([^\n]*))?", block[3], re.M)
                expected = ["ld", "fence", "ld"] if variant == "iriw-fenced" else ["ld", "ld"]
                if [op for op, _ in instructions] != expected:
                    raise RuntimeError(f"hart {hart}: reader instruction pair changed: {instructions}")
                if variant == "iriw-fenced" and instructions[1][1].replace(" ", "") != "r,r":
                    raise RuntimeError(f"hart {hart}: reader fence is not fence r,r")
                pairs.append({"address": "0x" + block[1],
                              "instructions": [op + " " + operands for op, operands in instructions]})
            if args.iterations >= 4 and not pairs:
                raise RuntimeError(f"hart {hart}: no reader instruction pair found")
            assembly_checks.append({"hart": hart, "gpIndependent": True, "readerPairs": pairs})
            binaries.append({"hart": hart, "file": binary.name,
                             "sha256": hashlib.sha256(binary.read_bytes()).hexdigest()})
        if sum(len(check["readerPairs"]) for check in assembly_checks) < 2:
            raise RuntimeError("The four binaries do not contain both IRIW readers")
        (out / "disassembly-check.json").write_text(json.dumps({"variant": variant,
            "iterations": args.iterations, "seed": args.seed, "harts": assembly_checks,
            "passed": True}, indent=2) + "\n", encoding="utf-8")
        manifest = {
            "source": "native/gem5/native-l2-litmus.c",
            "sourceSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "toolchain": toolchain, "flags": flags, "defines": defines, "binaries": binaries,
            "variant": variant, "iterations": args.iterations, "seed": args.seed,
            "title": "KMH 原生 CHI L2 · " + ("IRIW + fence r,r" if variant == "iriw-fenced" else "IRIW"),
            "description": "四核普通 ld/sd；轮换 RN 角色和独立 cache line 的 HN 映射，逐轮统计 16 种读结果。",
            "traffic": "ordinary CPU ld/sd/fence; no injected CHI opcodes or test MMIO",
            "sharedVirtual": "0x100000000", "sharedBytes": 65536,
            "resultOrder": ["P2.x", "P2.y", "P3.y", "P3.x"],
            "forbiddenOutcome": "1010" if variant == "iriw-fenced" else None,
            "barrier": "Separate 64B per-hart epoch slots, fence rw,rw before/after each start/end barrier",
            "readerInstructions": ["ld", "fence r,r", "ld"] if variant == "iriw-fenced" else ["ld", "ld"],
            "assemblyVerification": "disassembly-check.json",
            "roleRotation": "(hart + iteration + seed) & 3",
            "addressRotation": {"x": "shared+0x2000+64*((iteration/4+seed)&3)",
                                "y": "shared+0x3000+64*((x_home+1+(iteration/16)%3)&3)"},
            "tests": ["Every completed reader value is 0 or 1; histogram sum equals iterations",
                      "Per-round reset/start/end synchronization; no MMIO or opcode override",
                      "1010 forbidden with both reader fences; merely observed without fences"],
        }
        (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Built {variant}: 4 harts, iterations={args.iterations}, seed={args.seed}, {toolchain}")


if __name__ == "__main__":
    main()
