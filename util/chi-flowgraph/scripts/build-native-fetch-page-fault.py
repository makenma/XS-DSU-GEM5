"""Build real-FS instruction page-boundary cases; never runs gem5."""
import hashlib
import json
from pathlib import Path
import struct
import subprocess


def require(condition, message):
    if not condition:
        raise ValueError(message)


root = Path(__file__).resolve().parents[1]
out = root / 'build/native-fetch-page-fault-program'
out.mkdir(parents=True, exist_ok=True)
source_relative = 'native/gem5/native-fetch-page-fault.S'
source = root / source_relative
linker = out / 'link.ld'
linker.write_text('''OUTPUT_ARCH(riscv)
ENTRY(_start)
SECTIONS {
 . = 0x80000000; .text : { *(.text.start) *(.text*) }
 .rodata : { *(.rodata*) }
 . = 0x80010000; .root_table : { *(.root_table) }
 . = 0x80011000; .middle_table : { *(.middle_table) }
 . = 0x80012000; .leaf_table : { *(.leaf_table) }
 . = 0x80200000; .test_page : { *(.test_page) }
 /DISCARD/ : { *(.riscv.attributes) *(.comment) }
}
''')
flags = ['-nostdlib', '-static', '-fno-pie', '-no-pie',
         '-march=rv64imafdc_zicsr_zifencei', '-mabi=lp64d', '-mcmodel=medany',
         '-mno-relax', '-msmall-data-limit=0', '-Wl,--no-relax',
         '-Wl,--build-id=none', '-Wl,-T,' + str(linker)]
compiler = subprocess.check_output(['riscv64-linux-gnu-gcc', '--version'], text=True).splitlines()[0]
objdump = subprocess.check_output(['riscv64-linux-gnu-objdump', '--version'], text=True).splitlines()[0]
variants = []
for number, name in enumerate(('page-tail-return', 'last-halfword-return', 'straddling-instruction-fault')):
    elf = out / (name + '.elf')
    command = ['riscv64-linux-gnu-gcc', *flags, f'-DTEST_CASE={number}', str(source), '-o', str(elf)]
    subprocess.run(command, check=True)
    assembly = subprocess.check_output(['riscv64-linux-gnu-objdump', '-d', str(elf)], text=True)
    (out / (name + '.asm')).write_text(assembly)
    symbols = {}
    for line in subprocess.check_output(['riscv64-linux-gnu-nm', '-n', str(elf)], text=True).splitlines():
        fields = line.split()
        if len(fields) == 3:
            symbols[fields[2]] = int(fields[0], 16)
    sections = {}
    for section in ('.root_table', '.middle_table', '.leaf_table', '.test_page'):
        raw = out / (name + section + '.bin')
        subprocess.run(['riscv64-linux-gnu-objcopy', '-O', 'binary', '--only-section=' + section,
                        str(elf), str(raw)], check=True)
        sections[section] = raw.read_bytes()
        require(len(sections[section]) == 4096, section + ' must be exactly one page')
    tables = {name: struct.unpack('<512Q', sections[name]) for name in sections if name != '.test_page'}
    expected_root = [0] * 512
    expected_root[1] = ((0x80011000 >> 12) << 10) | 1
    expected_root[2] = 0x200000cf
    require(tables['.root_table'] == tuple(expected_root), 'root table differs from the declared mappings')
    require(tables['.middle_table'] == (((0x80012000 >> 12) << 10) | 1,) + (0,) * 511,
            'middle table must point only at the test leaf table')
    require(tables['.leaf_table'] == (((0x80200000 >> 12) << 10) | 0xcb,) + (0,) * 511,
            'only the first 4KiB test page may be present; next PTE must be zero')
    offset = 0xfc0 if number == 0 else 0xffe
    encoding = bytes.fromhex(('67800000', '8280', '1300')[number])
    require(sections['.test_page'][offset:offset + len(encoding)] == encoding, 'actual instruction bytes differ')
    require(symbols['test_entry_physical'] == 0x80200000 + offset, 'entry symbol not at the required page offset')
    entry_assembly = subprocess.check_output([
        'riscv64-linux-gnu-objdump', '-D', '--start-address=' + hex(0x80200000 + offset),
        '--stop-address=0x80201000', str(elf)], text=True)
    (out / (name + '.entry.asm')).write_text(entry_assembly)
    if number != 2:
        require('\tret' in entry_assembly, 'entry must disassemble as a real return instruction')
    require(all(name in assembly for name in ('sfence.vma', 'fence.i', 'mret')), 'missing translation/privilege setup')
    require('(gp)' not in assembly, 'bare FS code must not require an initialized gp')
    required = ['passed', 'trapPcChecked', 'causeChecked', 'tvalChecked']
    if number != 2:
        required += ['returnValueChecked', 'returnAddressChecked']
    variants.append({
        'case': name, 'elf': elf.name, 'sha256': hashlib.sha256(elf.read_bytes()).hexdigest(),
        'virtualEntry': hex(0x40000000 + offset), 'physicalEntry': hex(0x80200000 + offset),
        'entryBytesHex': encoding.hex(), 'nextPagePte': '0x0',
        'entryDisassembly': name + '.entry.asm',
        'expectedTrap': 12 if number == 2 else 9,
        'expectedTrapPc': hex(0x40000ffe if number == 2 else symbols['expected_supervisor_ecall']),
        'expectedTval': '0x40001000' if number == 2 else '0x0',
        'expectedValue': None if number == 2 else 42,
        'requiredGuestChecks': required, 'command': command,
    })
manifest = {
    'kind': 'bare-fs-fetch-page-fault', 'notLinuxSuite': True,
    'sourceRelativePath': source_relative,
    'sourceSha256': hashlib.sha256(source.read_bytes()).hexdigest(),
    'hartsConfigured': 4, 'activeTestHarts': [0], 'otherHarts': 'WFI',
    'mapping': {'virtualPage': '0x40000000', 'physicalPage': '0x80200000', 'pageBytes': 4096,
                'permissions': 'S-mode RX, A/D preset; next virtual page unmapped',
                'rootTable': '0x80010000', 'middleTable': '0x80011000', 'leafTable': '0x80012000'},
    'compiler': compiler, 'objdump': objdump, 'flags': flags, 'variants': variants,
    'guestExecuted': False,
    'scope': 'Build and static byte/page-table validation only. Real FS runner must establish actual trap/return results.',
}
(out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(json.dumps(manifest, indent=2))
