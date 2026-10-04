"""Build real RV64 CPU load/store tests for the reserved Normal-NC RAM page."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--output', type=Path, default=ROOT / 'build/linux-uncached')
p.add_argument('--cc', default=os.environ.get('CHI_RISCV_CC', 'riscv64-linux-gnu-gcc'))
p.add_argument('--objdump', default='riscv64-linux-gnu-objdump')
p.add_argument('--cross-compile', default='riscv64-linux-gnu-')
p.add_argument('--kernel-source', type=Path)
p.add_argument('--kernel-build', type=Path)
a = p.parse_args()
source = ROOT / 'native/linux-tests/linux-uncached.c'
module = ROOT / 'native/linux-tests/uncached-module'
sources = [source, *sorted(x for x in module.iterdir() if x.is_file())]
hashes = {str(x.relative_to(ROOT)): hashlib.sha256(x.read_bytes()).hexdigest() for x in sources}
build_id = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
a.output.mkdir(parents=True, exist_ok=True)
binary = a.output / 'linux-uncached'
flags = ['-std=gnu11', '-O2', '-g', '-static', '-march=rv64gc', '-mabi=lp64d',
         '-Wall', '-Wextra', '-Werror', f'-DLINUX_UNCACHED_BUILD_ID="{build_id}"']
subprocess.run([a.cc, *flags, str(source), '-o', str(binary)], check=True)
assembly = subprocess.check_output([a.objdump, '-d', str(binary)], text=True)
(a.output / 'linux-uncached.asm').write_text(assembly)
checks = {'rv64Elf': 'elf64-littleriscv' in assembly,
          'fixedExecutable': 'EXEC_P' in subprocess.check_output([a.objdump, '-f', str(binary)], text=True)}
for bits, load, store in [(8, 'lbu', 'sb'), (16, 'lhu', 'sh'), (32, 'lwu', 'sw'), (64, 'ld', 'sd')]:
    for direction, mnemonic in [('load', load), ('store', store)]:
        name = f'nc_{direction}{bits}'
        match = re.search(rf'<{name}>:\n(.*?)(?=\n\n)', assembly, re.S)
        checks[name] = bool(match and re.search(rf'\b{mnemonic}\s', match[1]))
if not all(checks.values()):
    raise RuntimeError(f'Actual RV64 instruction audit failed: {checks}')
module_result = {'built': False, 'reason': 'kernel paths were not supplied'}
if a.kernel_source or a.kernel_build:
    if not (a.kernel_source and a.kernel_build):
        p.error('supply both kernel paths')
    config = (a.kernel_build / '.config').read_text()
    if any(setting not in config for setting in ['CONFIG_MODULES=y', 'CONFIG_MMU=y', 'CONFIG_OF=y']):
        raise RuntimeError('module requires CONFIG_MODULES, CONFIG_MMU and CONFIG_OF')
    dest = a.output / 'module'
    dest.mkdir(exist_ok=True)
    for path in module.iterdir():
        if path.is_file(): shutil.copyfile(path, dest / path.name)
    subprocess.run(['make', '-C', str(a.kernel_source.resolve()), f'O={a.kernel_build.resolve()}',
                    'ARCH=riscv', f'CROSS_COMPILE={a.cross_compile}', f'M={dest.resolve()}',
                    '-j1', 'modules'], check=True)
    ko = dest / 'chi_uncached_test.ko'
    vermagic = subprocess.check_output(['modinfo', '-F', 'vermagic', str(ko)], text=True).strip()
    release = (a.kernel_build / 'include/config/kernel.release').read_text().strip()
    symbols = subprocess.check_output([a.cross_compile + 'nm', '-u', str(ko)], text=True)
    (dest / 'undefined-symbols.txt').write_text(symbols)
    exports = {line.split()[1] for line in (a.kernel_build / 'Module.symvers').read_text().splitlines()}
    unresolved = sorted({line.split()[-1] for line in symbols.splitlines() if line.split()} - exports)
    module_checks = {'versionMatches': vermagic.split()[0] == release, 'allSymbolsExported': not unresolved}
    if not all(module_checks.values()):
        raise RuntimeError(f'Kernel module verification failed: {module_checks}, {unresolved}')
    module_result = {'built': True, 'path': str(ko), 'sha256': hashlib.sha256(ko.read_bytes()).hexdigest(),
                     'kernelRelease': release, 'vermagic': vermagic, 'checks': module_checks,
                     'unresolvedSymbols': unresolved}
manifest = {'schemaVersion': 1, 'suite': 'native-linux-uncached', 'buildId': build_id,
            'sourceHashes': hashes, 'binary': binary.name,
            'binarySha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
            'toolchain': subprocess.check_output([a.cc, '--version'], text=True).splitlines()[0],
            'flags': flags, 'assemblyChecks': checks, 'module': module_result,
            'samples': 64, 'cpus': 4, 'lineBytes': 64, 'widths': [1, 2, 4, 8],
            'compatible': 'chi-flowgraph,uncached-test-ram',
            'boundary': 'DT no-map reserved RAM with physical PMA Normal-NC, no cacheable alias; actual CPU accesses use existing L2/NoC/HN/SN path. Pair guest data evidence with actual accepted CHI transfer counts or raw events.'}
(a.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(json.dumps({'binary': str(binary), 'buildId': build_id,
                  'assemblyChecks': checks, 'module': module_result}, indent=2))
