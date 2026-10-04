"""Build an isolated real-CPU instruction-boundary regression, not Linux."""
import hashlib
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[1]
out = root / 'build/native-fetch-stream-fs-program'
out.mkdir(parents=True, exist_ok=True)
source = root / 'native/gem5/native-fetch-stream-fs.S'
linker = out / 'link.ld'
linker.write_text('''OUTPUT_ARCH(riscv)
ENTRY(_start)
SECTIONS {
 . = 0x80000000;
 .text : { *(.text.start) *(.text*) }
 .rodata : { *(.rodata*) }
 . = 0x80010000; .root_a : { *(.root_a) }
 . = 0x80011000; .middle_a : { *(.middle_a) }
 . = 0x80012000; .root_b : { *(.root_b) }
 . = 0x80013000; .middle_b : { *(.middle_b) }
 . = 0x80200000; .code_a : { *(.code_a) }
 . = 0x80400000; .code_b : { *(.code_b) }
 /DISCARD/ : { *(.riscv.attributes) *(.comment) }
}
''')
flags = ['-nostdlib', '-static', '-fno-pie', '-no-pie',
         '-march=rv64imafdc_zicsr_zifencei', '-mabi=lp64d', '-mcmodel=medany',
         '-mno-relax', '-msmall-data-limit=0', '-Wl,--no-relax',
         '-Wl,--build-id=none', '-Wl,-T,' + str(linker)]
variants = []
for name, same in [('same-layout', 1), ('changed-layout', 0)]:
    elf = out / (name + '.elf')
    subprocess.run(['riscv64-linux-gnu-gcc', *flags, f'-DSAME_LAYOUT={same}',
                    '-DTRAIN_CALLS=512', str(source), '-o', str(elf)], check=True)
    asm = subprocess.check_output(['riscv64-linux-gnu-objdump', '-d', str(elf)], text=True)
    (out / (name + '.asm')).write_text(asm)
    sections = {}
    for section in ['.code_a', '.code_b', '.root_a', '.root_b', '.middle_a', '.middle_b']:
        raw = out / (name + section + '.bin')
        subprocess.run(['riscv64-linux-gnu-objcopy', '-O', 'binary',
                        '--only-section=' + section, str(elf), str(raw)], check=True)
        sections[section] = raw.read_bytes()
    a, b = sections['.code_a'], sections['.code_b']
    assert a[:2] == bytes.fromhex('0100')
    jump = int.from_bytes(a[2:4], 'little')
    assert jump & 3 == 1 and jump >> 13 == 5, 'Expected compressed unconditional jump at +2'
    assert a[16:20] == bytes.fromhex('13058500')
    assert b[:4] == (a[:4] if same else bytes.fromhex('13051500'))
    assert b[16:20] == (a[16:20] if same else bytes.fromhex('13050504'))
    assert int.from_bytes(sections['.root_a'][8:16], 'little') == ((0x80011000 >> 12) << 10) | 1
    assert int.from_bytes(sections['.root_b'][8:16], 'little') == ((0x80013000 >> 12) << 10) | 1
    assert 'sfence.vma' in asm and 'fence.i' in asm and 'mret' in asm
    variants.append({'case': name, 'elf': elf.name,
                     'sha256': hashlib.sha256(elf.read_bytes()).hexdigest(),
                     'expectedValue': 8 if same else 1,
                     'codeAHex': a.hex(), 'codeBHex': b.hex()})
manifest = {'kind': 'bare-fs-fetch-stream-instruction-boundary', 'notLinuxSuite': True,
            'sourceSha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'hartsConfigured': 4, 'activeTestHarts': [0], 'otherHarts': 'WFI',
            'virtualEntry': '0x40000000', 'oldBranchOffset': 2,
            'physicalCodeA': '0x80200000', 'physicalCodeB': '0x80400000',
            'trainingCalls': 512, 'flags': flags, 'variants': variants}
(out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(json.dumps(manifest, indent=2))
