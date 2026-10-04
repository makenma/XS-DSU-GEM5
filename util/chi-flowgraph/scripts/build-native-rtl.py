"""Build four real CPU workloads for the RTL-aligned native CHI request profile."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

project = Path(__file__).resolve().parent.parent
source = project / 'native/gem5/native-l2-rtl-test.c'
out = project / 'build/native-rtl-program'
out.mkdir(parents=True, exist_ok=True)
cc = os.environ.get('CHI_RISCV_CC', 'riscv64-linux-gnu-gcc')
objdump = os.environ.get('CHI_RISCV_OBJDUMP', 'riscv64-linux-gnu-objdump')
toolchain = subprocess.check_output([cc, '--version'], text=True).splitlines()[0]
flags = ['-g', '-O1', '-nostdlib', '-static', '-fno-builtin', '-fno-stack-protector',
         '-fno-pie', '-no-pie', '-march=rv64imafd_zicbom', '-mabi=lp64d', '-mcmodel=medany',
         '-msmall-data-limit=0', '-mno-relax', '-Wl,--no-relax',
         '-Wl,-Ttext=0x10000', '-Wl,--build-id=none', '-Wall', '-Wextra', '-Werror']
binaries = []
checks = []
for hart in range(4):
    binary = out / f'native-l2-{hart}.elf'
    subprocess.run([cc, *flags, f'-DHART={hart}', str(source), '-o', str(binary)], check=True)
    assembly = subprocess.check_output([objdump, '-d', str(binary)], text=True)
    (out / f'native-l2-{hart}.asm').write_text(assembly)
    if re.search(r'^\s*[0-9a-f]+:\s+[0-9a-f]+\s+[^\n]*\bgp\b', assembly, re.M):
        raise RuntimeError(f'hart {hart}: uninitialized gp dependence')
    cbos = []
    for match in re.finditer(r'^\s*([0-9a-f]+):\s+([0-9a-f]{8})\s+([^\n]+)', assembly, re.M):
        word = int(match[2], 16)
        if word & 0x00007fff == 0x0000200f:
            immediate = word >> 20
            if immediate not in (0, 1, 2):
                raise RuntimeError(f'Unexpected CBO immediate {immediate}')
            cbos.append({'pc': '0x' + match[1], 'word': '0x' + match[2],
                         'instruction': ['cbo.inval', 'cbo.clean', 'cbo.flush'][immediate]})
    if hart == 0 and {item['instruction'] for item in cbos} != {'cbo.inval', 'cbo.clean', 'cbo.flush'}:
        raise RuntimeError('hart 0 must contain all three architectural CBO encodings')
    block = re.search(r'<write_line>:\n(.*?)(?=\n\n)', assembly, re.S)
    if not block or len(re.findall(r'\bsd\b', block[1])) != 8:
        raise RuntimeError(f'hart {hart}: expected eight actual sd instructions for a full line')
    checks.append({'hart': hart, 'gpIndependent': True, 'cbos': cbos, 'fullLineStores': 8})
    binaries.append({'hart': hart, 'file': binary.name,
                     'sha256': hashlib.sha256(binary.read_bytes()).hexdigest()})
(out / 'disassembly-check.json').write_text(json.dumps({'passed': True, 'harts': checks}, indent=2) + '\n')
manifest = {
    'title': 'KMH 原生 L2 · RTL 12 类请求 CPU 场景',
    'description': '真实 ld/sd/fence 和 Zicbom 指令，正常不可缓存 RAM，按实际 RN 发包检查覆盖。',
    'source': 'native/gem5/native-l2-rtl-test.c',
    'sourceSha256': hashlib.sha256(source.read_bytes()).hexdigest(),
    'toolchain': toolchain, 'flags': flags, 'binaries': binaries,
    'rtlRequestWorkload': True, 'protocolOpcodeInjection': False,
    'traffic': 'real CPU load/store/fence/cbo.clean/cbo.flush/cbo.inval',
    'sharedVirtual': '0x100000000', 'sharedBytes': 65536,
    'uncachedVirtual': '0x200000000', 'uncachedBytes': 4096,
    'uncachedKind': 'normal-noncacheable-ram',
    'uncachedPolicy': {'Device': False, 'MemAttr': 0, 'Order': 2, 'ExpCompAck': False,
                       'cacheableAlias': False, 'cpuStrictOrder': True},
    'cboSupport': 'Zicbom 1.0 CPU decode; this workload exercises SE, while FS permissions and translation have separate Linux tests',
    'isaSource': 'https://docs.riscv.org/reference/isa/v20240411/_attachments/riscv-unprivileged.pdf',
    'assemblyVerification': 'disassembly-check.json',
    'tests': ['partial writes and dirty owner sharing preserve untouched bytes',
              'dirty clean / dirty flush / clean invalidate through real CPU CBOs',
              '320 complete-line writes and all bytes checked by four CPUs',
              'private dirty clean retained unique, then capacity eviction and all bytes rechecked',
              'capacity-driven dirty and clean replacement',
              'normal noncacheable RAM 64-bit plus byte writes checked across CPUs'],
}
(out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
print(f'Built and disassembled four native RTL workloads using {toolchain}')
