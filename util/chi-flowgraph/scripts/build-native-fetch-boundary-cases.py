"""Build six isolated real-CPU fetch regressions; never run gem5.

The FTQ geometry is read from an existing actual config.ini. Architectural
checks do not by themselves establish that a stale prediction was exercised;
the runner must inspect the corresponding Fetch/DecoupleBP trace.
"""
import argparse
import configparser
import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SOURCE_REL = 'native/gem5/native-fetch-boundary-cases.S'
DEFAULT_CONFIG = Path('/root/chi-flowgraph-work/linux-validation/runs/model08/'
                      'linux-suite-08/config.ini')
CASES = [
    ('control-interior', 0, 1, 0, 0, 2, 'jal', False),
    ('cross-stale', 1, 1, 3, 62, 64, 'addi', True),
    ('cross-clean', 2, 1, 3, 62, None, 'addi', True),
    ('prefix-interior', 3, 1, 3, 4, 6, 'addi', False),
    ('amo-cross-stale', 4, 24, 3, 62, 64, 'amoadd.d.aqrl', True),
    ('legal-taken', 5, 8, 0, 0, 2, 'c.nop;c.j', False),
]
LINKER = '''OUTPUT_ARCH(riscv)
ENTRY(_start)
SECTIONS {
 . = 0x80000000; .text : { *(.text.start) *(.text*) }
 .rodata : { *(.rodata*) }
 . = 0x80010000; .root_a : { *(.root_a) }
 . = 0x80011000; .middle_a : { *(.middle_a) }
 . = 0x80012000; .root_b : { *(.root_b) }
 . = 0x80013000; .middle_b : { *(.middle_b) }
 . = 0x80014000; .test_data : { *(.test_data) }
 . = 0x80200000; .code_a : { *(.code_a) }
 . = 0x80400000; .code_b : { *(.code_b) }
 /DISCARD/ : { *(.riscv.attributes) *(.comment) }
}
'''


def sha(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, explanation):
    if not condition:
        raise RuntimeError(explanation)


def signed(value, bits):
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


def cj_displacement(word):
    require(word & 3 == 1 and word >> 13 == 5, 'Expected c.j encoding')
    return signed(((word >> 12 & 1) << 11) | ((word >> 11 & 1) << 4) |
                  ((word >> 9 & 3) << 8) | ((word >> 8 & 1) << 10) |
                  ((word >> 7 & 1) << 6) | ((word >> 6 & 1) << 7) |
                  ((word >> 3 & 7) << 1) | ((word >> 2 & 1) << 5), 12)


def jal_displacement(word):
    require(word & 0xfff == 0x06f, 'Expected jal zero, target')
    return signed(((word >> 31) << 20) | ((word >> 21 & 0x3ff) << 1) |
                  ((word >> 20 & 1) << 11) | (word & 0xff000), 21)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG,
                        help='Actual four-KMH config.ini that supplies predictWidth')
    parser.add_argument('--training-calls', type=int, default=512)
    args = parser.parse_args()
    require(args.training_calls >= 512, 'At least 512 training calls required')
    require(args.training_calls <= 1000000, 'Training count exceeds bounded test scope')
    config_path = args.config.resolve(strict=True)
    config_bytes = config_path.read_bytes()
    config = configparser.ConfigParser()
    config.read_string(config_bytes.decode('utf-8'))
    widths = {f'system.cpu{i}.branchPred': int(config[f'system.cpu{i}.branchPred']['predictWidth'])
              for i in range(4)}
    require(set(widths.values()) == {64}, 'These layouts require actual predictWidth=64 on all 4 CPUs')
    require(all(config[s]['type'] == 'DecoupledBPUWithBTB' for s in widths),
            'The observed BPU must be DecoupledBPUWithBTB')
    out = ROOT / 'build/native-fetch-boundary-cases-program'
    out.mkdir(parents=True, exist_ok=True)
    source = ROOT / SOURCE_REL
    linker = out / 'link.ld'
    linker.write_text(LINKER)
    (out / 'geometry-config.ini').write_bytes(config_bytes)
    flags = ['-nostdlib', '-static', '-fno-pie', '-no-pie',
             '-march=rv64imafdc_zicsr_zifencei', '-mabi=lp64d', '-mcmodel=medany',
             '-mno-relax', '-msmall-data-limit=0', '-Wl,--no-relax',
             '-Wl,--build-id=none', '-Wl,-T,' + str(linker)]
    commands, variants = [], []

    def run(command):
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        commands.append({'argv': command, 'exitCode': result.returncode,
                         'stdout': result.stdout, 'stderr': result.stderr})
        return result.stdout

    toolchain = run(['riscv64-linux-gnu-gcc', '--version']).splitlines()[0]
    sections = ['.code_a', '.code_b', '.root_a', '.root_b', '.middle_a', '.middle_b', '.test_data']
    for name, case_id, expected, prefix, offset, stale_slot, instruction, cross in CASES:
        elf = out / (name + '.elf')
        run(['riscv64-linux-gnu-gcc', *flags, f'-DCASE_ID={case_id}',
             f'-DTRAIN_CALLS={args.training_calls}', '-DPREDICT_WIDTH=64', str(source), '-o', str(elf)])
        asm = run(['riscv64-linux-gnu-objdump', '-d', str(elf)])
        (out / (name + '.asm')).write_text(asm)
        symbols = {}
        for line in run(['riscv64-linux-gnu-nm', '-n', str(elf)]).splitlines():
            fields = line.split()
            if len(fields) == 3:
                symbols[fields[2]] = int(fields[0], 16)
        header = run(['riscv64-linux-gnu-readelf', '-h', '-lW', str(elf)])
        (out / (name + '.elf-layout.txt')).write_text(header)
        require('0x80000000' in header and symbols['_start'] == 0x80000000, 'Wrong ELF entry')
        raw = {}
        for section in sections:
            path = out / (name + section + '.bin')
            run(['riscv64-linux-gnu-objcopy', '-O', 'binary', '--only-section=' + section,
                 str(elf), str(path)])
            raw[section] = path.read_bytes()
        a, b = raw['.code_a'], raw['.code_b']
        require(symbols['a_instruction'] == 0x80200000 + offset, name + ': A instruction offset')
        require(symbols['b_instruction'] == 0x80400000 + offset, name + ': B instruction offset')
        require(symbols['code_a_target'] == 0x80200080, name + ': A target offset')
        require(symbols['code_b_old_target'] == 0x80400080, name + ': B old target offset')
        require(symbols['code_b_real_target'] == 0x804000a0, name + ': B new target offset')
        require(symbols['atomic_word'] == 0x80014008, 'Wrong data location/alignment')
        if prefix:
            require(a[:4] == b[:4] == bytes.fromhex('93853500'), name + ': prefix addi a1,3')
        if cross:
            require(a[4:62] == b[4:62] == bytes.fromhex('0100') * 29, name + ': prefix padding')
            require(offset == 62 and offset < 64 < offset + 4, name + ': missing cross-boundary instruction')
        if stale_slot is not None:
            require(symbols['trained_branch'] == 0x80200000 + stale_slot, name + ': old branch slot')
            require(a[stale_slot - 2:stale_slot] == bytes.fromhex('0100'), name + ': A c.nop')
            require(stale_slot + cj_displacement(int.from_bytes(a[stale_slot:stale_slot+2], 'little')) == 128,
                    name + ': A c.j destination')
        else:
            require('trained_branch' not in symbols, 'Clean case has unexpected training branch')
            require(a[offset:offset+8] == bytes.fromhex('1305850067800000'), 'Clean A addi/ret layout')
        if case_id == 0:
            require(jal_displacement(int.from_bytes(b[:4], 'little')) == 160, 'B control destination')
            require(b[4:12] == bytes.fromhex('1305050267800000'), 'Control fallthrough must be poison')
        elif case_id == 4:
            amo = ((3 << 25) | (21 << 20) | (20 << 15) | (3 << 12) | (12 << 7) | 0x2f)
            require(int.from_bytes(b[offset:offset+4], 'little') == amo, 'Wrong AMO register/AQ/RL encoding')
            require(b[offset+4:offset+8] == bytes.fromhex('33055601'), 'AMO return must depend on old-value result')
            require('amoadd.d.aqrl' in asm, 'Missing real AMO instruction')
        elif case_id == 5:
            require(a[:12] == b[:12], 'Legal taken layout changed')
        else:
            require(b[offset:offset+8] == bytes.fromhex('1305150067800000'), name + ': B addi/ret layout')
        require(a[128:136] == bytes.fromhex('1305850067800000'), 'Training target value')
        require(b[128:136] == bytes.fromhex('1305850067800000' if case_id == 5 else '1305050467800000'),
                'Old-target poison value')
        require(b[160:168] == bytes.fromhex('1305150067800000'), 'New control target value')
        for suffix, middle, code in [('a', 0x80011000, 0x80200000), ('b', 0x80013000, 0x80400000)]:
            expected_root = [0xcf, ((middle >> 12) << 10) | 1, 0x200000cf]
            expected_middle = ((code >> 12) << 10) | 0xcf
            require(raw['.root_' + suffix] == b''.join(v.to_bytes(8, 'little') for v in expected_root) + bytes(4096-24),
                    'Sv39 root map mismatch')
            require(raw['.middle_' + suffix] == expected_middle.to_bytes(8, 'little') + bytes(4096-8),
                    'Sv39 2MiB leaf mismatch')
        require(raw['.test_data'] == b''.join(v.to_bytes(8, 'little') for v in [0x123456789abcdef, 17, 0xfedcba987654321]),
                'AMO initial data/guards mismatch')
        require(all(text in asm for text in ['sfence.vma', 'fence.i', 'mret', 'pmpaddr0', 'pmpcfg0']),
                'Missing startup/translation/fence instructions')
        require(symbols['expected_supervisor_ecall'] != symbols['supervisor_fail'], 'Fail must not share accepted trap PC')
        variants.append({'case': name, 'elf': elf.name, 'sha256': sha(elf.read_bytes()),
                         'expectedValue': expected, 'expectedPrefixValue': prefix,
                         'expectedTrainingValue': args.training_calls * 8,
                         'expectedTrainingPrefixValue': args.training_calls * prefix,
                         'instruction': instruction, 'instructionOffset': offset,
                         'instructionVirtualAddress': hex(0x40000000 + offset),
                         'oldBranchOffset': stale_slot,
                         'oldBranchVirtualAddress': None if stale_slot is None else hex(0x40000000 + stale_slot),
                         'crossesFtqBoundary': cross, 'expectedFtqBoundary': '0x40000040' if cross else None,
                         'invalidOldSlotExpected': case_id not in (2, 5),
                         'oldTargetVirtualAddress': '0x40000080',
                         'controlTargetVirtualAddress': '0x400000a0' if case_id == 0 else None,
                         'amo': {'address': '0x80014008', 'initialValue': 17, 'increment': 7,
                                 'expectedOldValue': 17, 'expectedFinalValue': 24,
                                 'expectedRmwCount': 1} if case_id == 4 else None,
                         'expectedTrapPc': hex(symbols['expected_supervisor_ecall']),
                         'symbolAddresses': {k: hex(v) for k, v in symbols.items()},
                         'codeAHex': a.hex(), 'codeBHex': b.hex(),
                         'assemblyChecksPassed': True})
    manifest = {'kind': 'bare-fs-fetch-stream-instruction-boundary', 'notLinuxSuite': True,
                'sourceRelativePath': SOURCE_REL, 'sourceSha256': sha(source.read_bytes()),
                'builderSha256': sha(Path(__file__).read_bytes()), 'linkerSha256': sha(linker.read_bytes()),
                'hartsConfigured': 4, 'activeTestHarts': [0], 'otherHarts': 'WFI',
                'virtualEntry': '0x40000000', 'physicalCodeA': '0x80200000', 'physicalCodeB': '0x80400000',
                'trainingCalls': args.training_calls, 'flags': flags, 'toolchain': toolchain,
                'predictWidth': 64, 'geometryEvidence': {'config': str(config_path),
                    'configSha256': sha(config_bytes), 'snapshot': 'geometry-config.ini',
                    'predictWidths': widths,
                    'fallThroughFormula': '(bbStart + predictWidth) & ~(predictWidth / 2 - 1)',
                    'alignedEntryFallThrough': '0x40000040'},
                'scope': 'Architectural result checks and build-time layout verification; actual stale BTB/FTQ exercise requires runtime trace evidence. No guest run performed by builder.',
                'variants': variants}
    (out / 'build-commands.json').write_text(json.dumps(commands, indent=2) + '\n')
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'manifest': str(out / 'manifest.json'), 'cases': [v['case'] for v in variants],
                      'assemblyChecksPassed': True, 'guestExecuted': False}, indent=2))


if __name__ == '__main__':
    main()
