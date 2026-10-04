"""Cross-compile the actual RV64 Linux pthread suite; never install dependencies or run it."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
TESTS = ['spinlock', 'mutex', 'futex', 'amo', 'lrsc', 'release_acquire', 'false_sharing',
         'atomic_counter', 'memcpy_checksum', 'alias_cache_pressure', 'page_migration']


def build(out, cc, objdump):
    out.mkdir(parents=True, exist_ok=True)
    source = ROOT / 'native/linux-tests/suite.c'
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    binary = out / 'linux-suite'
    flags = ['-O2', '-g', '-static', '-pthread', '-std=gnu11', '-march=rv64gc', '-mabi=lp64d',
             '-fno-builtin-memcpy', '-Wall', '-Wextra', '-Werror',
             f'-DLINUX_SUITE_BUILD_ID="{source_hash}"']
    version = subprocess.check_output([cc, '--version'], text=True).splitlines()[0]
    subprocess.run([cc, *flags, str(source), '-o', str(binary)], check=True)
    assembly = subprocess.check_output([objdump, '-d', str(binary)], text=True)
    (out / 'linux-suite.asm').write_text(assembly, encoding='utf-8')
    def body(name):
        match = re.search(rf'<{name}(?:\.[^>]*)?>:\n(.*?)(?=\n\n)', assembly, re.S)
        if not match:
            raise RuntimeError(f'missing compiled {name}')
        return match[1]
    checks = {'rv64Elf': 'elf64-littleriscv' in assembly,
              'explicitAmoAqRl': 'amoadd.d.aqrl' in body('explicit_amo'),
              'explicitLrAcquire': 'lr.d.aq' in body('explicit_lrsc'),
              'explicitScRelease': 'sc.d.rl' in body('explicit_lrsc'),
              'linuxEcall': 'ecall' in assembly}
    if not all(checks.values()):
        raise RuntimeError(f'actual RV64 assembly checks failed: {checks}')
    manifest = {'schemaVersion': 1, 'suite': 'native-linux-pthreads',
                'source': 'native/linux-tests/suite.c', 'sourceSha256': source_hash,
                'binary': binary.name, 'binarySha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
                'toolchain': version, 'flags': flags, 'tests': TESTS, 'threads': 4,
                'assemblyChecks': checks, 'assembly': 'linux-suite.asm',
                'defaultArguments': ['--iterations', '128', '--timeout', '120', '--test', 'all'],
                'execution': 'Real RV64 Linux pthreads, pinned to four allowed online CPUs.',
                'generator': None,
                'generatorBoundary': 'This hand-written systems suite does not replace diy7/litmus7 generated tests.',
                'pageMigration': 'Requires actual move_pages node changes; single-node/unsupported capability is NOT PASS.'}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'binary': str(binary), 'sourceSha256': source_hash, 'assemblyChecks': checks}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'build/linux-tests')
    parser.add_argument('--cc', default=os.environ.get('CHI_RISCV_CC', 'riscv64-linux-gnu-gcc'))
    parser.add_argument('--objdump', default=os.environ.get('CHI_RISCV_OBJDUMP', 'riscv64-linux-gnu-objdump'))
    args = parser.parse_args()
    build(args.output, args.cc, args.objdump)
