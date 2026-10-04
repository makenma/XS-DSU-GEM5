"""Build a small FS cold-DTLB CBO reproducer; this is not a Linux suite."""
import hashlib
import json
from pathlib import Path
import re
import subprocess

root = Path(__file__).resolve().parents[1]
out = root / "build/native-cbo-tlb-program"
out.mkdir(parents=True, exist_ok=True)
source = root / "native/gem5/native-cbo-tlb.S"
linker = out / "link.ld"
linker.write_text("""OUTPUT_ARCH(riscv)
ENTRY(_start)
SECTIONS {
 . = 0x80000000;
 .text : { *(.text.start) *(.text*) }
 .rodata : { *(.rodata*) }
 . = 0x80010000;
 .page_tables : { *(.page_tables) }
 . = 0x80020000;
 .test_data : { *(.test_data) }
 /DISCARD/ : { *(.riscv.attributes) *(.comment) }
}
""")
flags = ["-nostdlib", "-static", "-fno-pie", "-no-pie", "-march=rv64imafd_zicsr_zifencei",
         "-mabi=lp64d", "-mcmodel=medany", "-mno-relax", "-msmall-data-limit=0",
         "-Wl,--no-relax", "-Wl,--build-id=none", "-Wl,-T," + str(linker)]
variants = []
for name, opcode, fault in [("inval", 0, 0), ("clean", 1, 0), ("flush", 2, 0), ("flush-fault", 2, 1)]:
    binary = out / (name + ".elf")
    subprocess.run(["riscv64-linux-gnu-gcc", *flags, f"-DCBO_OP={opcode}",
                    f"-DFAULT_MAPPING={fault}", str(source), "-o", str(binary)], check=True)
    asm = subprocess.check_output(["riscv64-linux-gnu-objdump", "-d", str(binary)], text=True)
    (out / (name + ".asm")).write_text(asm)
    cold = re.search(r"^([0-9a-f]+) <cold_cbo_instruction>:\n\s*[0-9a-f]+:\s*([0-9a-f]{8})", asm, re.M)
    expected = (opcode << 20) | (10 << 15) | (2 << 12) | 0x0f
    assert cold and int(cold[2], 16) == expected
    assert "sfence.vma" in asm and "mret" in asm
    variants.append({"case": name, "elf": binary.name,
                     "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                     "coldCboPc": "0x" + cold[1], "encoding": hex(expected),
                     "expectedTrap": 15 if fault else 9})
manifest = {"kind": "focused-bare-fs-cbo-cold-dtlb-reproducer", "notLinuxSuite": True,
            "sourceSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "hartsConfigured": 4, "activeTestHarts": [0], "otherHarts": "WFI",
            "translation": "Sv39, S-mode first data operation; virtual target 0x40020000, physical 0x80020000",
            "coldEvidence": "sfence.vma before mret; target mapping differs from instruction mapping; require runtime deferred-translation evidence",
            "flags": flags, "variants": variants}
(out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(json.dumps(manifest, indent=2))
