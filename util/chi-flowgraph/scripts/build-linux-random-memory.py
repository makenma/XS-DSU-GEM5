"""Build the independent RV64 Linux random migration/alias pressure test; never execute it."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def build(output, cc, objdump, readelf):
    output.mkdir(parents=True, exist_ok=True)
    source = ROOT / 'native/linux-tests/random-memory.c'
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    binary = output / 'random-memory'
    flags = ['-O2', '-g', '-static', '-pthread', '-std=gnu11', '-march=rv64gc', '-mabi=lp64d',
             '-Wall', '-Wextra', '-Werror', f'-DRANDOM_MEMORY_BUILD_ID="{source_sha}"']
    version = subprocess.check_output([cc, '--version'], text=True).splitlines()[0]
    command = [cc, *flags, str(source), '-o', str(binary)]
    subprocess.run(command, check=True)
    assembly = subprocess.check_output([objdump, '-d', str(binary)], text=True)
    attributes = subprocess.check_output([readelf, '-A', str(binary)], text=True)
    (output / 'random-memory.asm').write_text(assembly, encoding='utf-8')
    (output / 'random-memory.attributes.txt').write_text(attributes, encoding='utf-8')
    architectures = re.findall(r'Tag_RISCV_arch:\s*"([^"]+)"', attributes)
    # A static libc can contain optional IFUNC implementations for other ISA
    # extensions; record ELF architecture as well as the exact compiler target.
    checks = {'rv64Elf': 'elf64-littleriscv' in assembly, 'linuxEcall': 'ecall' in assembly,
              'sourceTargetRv64gc': '-march=rv64gc' in flags,
              'noAdvertisedVectorOrZbb': bool(architectures) and all(
                  not any(token.startswith(('v', 'zbb')) for token in arch.split('_')[1:])
                  for arch in architectures)}
    if not all(checks.values()):
        raise RuntimeError('RV64 instruction/ELF checks failed: ' + str(checks))
    defaults = {'iterations': 128, 'seed': 1, 'pagesPerWriter': 64,
                'pressureBytesPerWriter': 1048576, 'pressureSamplesPerEpoch': 64, 'timeoutSeconds': 120}
    manifest = {
        'schemaVersion': 1, 'outputSchemaVersion': 1, 'suite': 'native-linux-random-memory',
        'source': 'native/linux-tests/random-memory.c', 'sourceSha256': source_sha,
        'binary': binary.name, 'binarySha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'toolchain': version, 'flags': flags, 'buildCommand': command,
        'assembly': 'random-memory.asm', 'elfAttributes': architectures, 'assemblyChecks': checks,
        'defaultParameters': defaults,
        'defaultArguments': ['--iterations', '128', '--seed', '1', '--pages-per-writer', '64',
                             '--pressure-bytes', '1048576', '--pressure-samples', '64', '--timeout', '120'],
        'requirements': {
            'threads': 4, 'writers': 3, 'writerIds': [1, 2, 3], 'minimumIterations': 128,
            'minimumPagesPerWriter': 64, 'minimumMemoryNumaNodes': 2,
            'movesPerRound': 3, 'allSelectedMovesMustChangeNode': True,
            'allPoolPagesMustBeMigrated': True, 'minimumOverlappingAttempts': 1,
            'minimumWindowWritesPerWriter': 1, 'minimumRuntimeWritesPerWriter': 1,
            'minimumPressureBytesPerWriter': 1048576, 'minimumPressureSamplesPerEpoch': 64,
            'minimumDistinctRuntimePressureLinesPerWriter': 64,
            'pressureLineBytes': 64, 'pressureMappingsPerWriter': 2,
            'fullPressureInitializationAndFinalByteCheck': True,
            'finalPoolCheckAfterAllWritersJoined': True, 'allByteErrorsMustBeZero': True,
            'guestTimeoutRequired': True, 'unsupportedIsPass': False,
        },
        'prng': {'name': 'xorshift32', 'shifts': [13, -17, 5], 'zeroReplacement': 0x6d2b79f5,
                 'migrationStreamMultiplier': 0x9e3779b9, 'writerStreamMultiplier': 0x85ebca6b,
                 'seedDerivation': 'uint32(seed ^ uint32(multiplier * writerId)); replace zero',
                 'selector': 'per-writer-fisher-yates-v1',
                 'shuffle': 'reset 0..N-1, descending i=N-1..1, j=next32()%(i+1), swap',
                 'migrationChoice': 'reshuffle on round%pagesPerWriter==0; choose order[round%pagesPerWriter]',
                 'targetChoice': 'before==nodes[0] ? nodes[1] : nodes[0]',
                 'selectionDigest': 'FNV1a64 of (round,slot,writer,page), each little-endian uint32, in output order'},
        'payload': {'pattern': 'xor-u64-word-v1', 'seedShift': 32,
                    'ownerMultiplier': '0x9e3779b97f4a7c15', 'pageMultiplier': '0xd1b54a32d192ed03',
                    'epochMultiplier': '0x94d049bb133111eb', 'wordMultiplier': '0xbf58476d1ce4e5b9',
                    'word': '(seed<<32) ^ owner*Mowner ^ page*Mpage ^ epoch*Mepoch ^ (offset/8)*Mword; uint64 wrap',
                    'byte': '(word>>(8*(offset%8)))&255', 'initialEpoch': 1,
                    'fnvOffset': '14695981039346656037', 'fnvPrime': '1099511628211'},
        'metricUnits': {'pressureRuntimeWrites': 'complete 64-byte lines',
                        'pressureRuntimeAliasChecks': 'bytes', 'pressureInitializationChecks': 'bytes',
                        'pressureFinalChecks': 'bytes', 'completedBeforeAfter': 'runtime complete pages, excludes initialization'},
        'pressureScope': 'Every allocated byte is written through alias A and verified through alias B before migration. '
                         'Runtime sampling visits a PRNG-shuffled line permutation; actual distinct lines are reported separately. '
                         '3 MiB initialized capacity exceeds suite05 total 2 MiB SLC; runtime touched capacity is not assumed from allocation.',
        'concurrencyScope': 'Three sole-owner writers continuously update versioned whole pages while one thread invokes real '
                            'Linux query/MOVE/query. Counter snapshots prove overlap of completed writes with syscall windows, '
                            'not execution at an exact internal kernel copy instruction.',
        'mappingScope': 'Migration pools are private anonymous pages with THP disabled. Independent pressure buffers use '
                        'two MAP_SHARED aliases of each memfd; only their owner writes, avoiding application data races.',
        'execution': 'NOT_RUN', 'guestPass': False,
    }
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'binary': str(binary), 'sourceSha256': source_sha,
                      'binarySha256': manifest['binarySha256'], 'assemblyChecks': checks,
                      'execution': 'NOT_RUN'}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'build/linux-random-memory')
    parser.add_argument('--cc', default=os.environ.get('CHI_RISCV_CC', 'riscv64-linux-gnu-gcc'))
    parser.add_argument('--objdump', default=os.environ.get('CHI_RISCV_OBJDUMP', 'riscv64-linux-gnu-objdump'))
    parser.add_argument('--readelf', default=os.environ.get('CHI_RISCV_READELF', 'riscv64-linux-gnu-readelf'))
    args = parser.parse_args()
    build(args.output, args.cc, args.objdump, args.readelf)
