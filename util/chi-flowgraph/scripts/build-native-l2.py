"""Build ordinary RV64 CPU workloads for the native CHI L2 configuration."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

project = Path(__file__).resolve().parent.parent
source = project / "native/gem5/native-l2-test.c"
out = project / "build/native-l2-program"
out.mkdir(parents=True, exist_ok=True)
cc = os.environ.get("CHI_RISCV_CC", "riscv64-linux-gnu-gcc")
toolchain = subprocess.check_output([cc, "--version"], text=True).splitlines()[0]
flags = ["-g", "-O1", "-nostdlib", "-static", "-fno-builtin", "-fno-stack-protector",
         "-fno-pie", "-no-pie", "-march=rv64imafd", "-mabi=lp64d", "-mcmodel=medany",
         "-Wl,-Ttext=0x10000", "-Wl,--build-id=none", "-Wall", "-Wextra"]
binaries = []
for hart in range(4):
    binary = out / f"native-l2-{hart}.elf"
    subprocess.run([cc, *flags, f"-DHART={hart}", str(source), "-o", str(binary)], check=True)
    binaries.append({"hart": hart, "file": binary.name,
                     "sha256": hashlib.sha256(binary.read_bytes()).hexdigest()})
(out / "manifest.json").write_text(json.dumps({
    "source": "native/gem5/native-l2-test.c",
    "sourceSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    "toolchain": toolchain, "flags": flags, "binaries": binaries,
    "traffic": "ordinary CPU load/store/fence; no injected CHI opcodes or test MMIO",
    "sharedVirtual": "0x100000000", "sharedBytes": 65536,
    "tests": ["four-core dirty owner transfer and permission upgrades",
              "capacity-driven inclusive dirty and clean victims",
              "all 320 data lines checked by all four CPUs"],
}, indent=2) + "\n")
print(f"Built four native L2 workloads using {toolchain}")
