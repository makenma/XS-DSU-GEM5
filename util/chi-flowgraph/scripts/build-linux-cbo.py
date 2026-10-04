"""Build the RV64 Linux CBO test and optionally its matching Linux test module."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, default=ROOT / 'build/linux-cbo')
parser.add_argument('--cc', default=os.environ.get('CHI_RISCV_CC', 'riscv64-linux-gnu-gcc'))
parser.add_argument('--objdump', default=os.environ.get('CHI_RISCV_OBJDUMP', 'riscv64-linux-gnu-objdump'))
parser.add_argument('--kernel-source', type=Path)
parser.add_argument('--kernel-build', type=Path)
parser.add_argument('--cross-compile', default='riscv64-linux-gnu-')
args = parser.parse_args()
source = ROOT / 'native/linux-tests/linux-cbo.c'
module = ROOT / 'native/linux-tests/cbo-module'
sources = [source, *sorted(p for p in module.iterdir() if p.is_file())]
hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
build_id = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
args.output.mkdir(parents=True, exist_ok=True)
binary = args.output / 'linux-cbo'
flags = ['-std=gnu11', '-O2', '-g', '-static', '-march=rv64gc', '-mabi=lp64d',
         '-Wall', '-Wextra', '-Werror', f'-DLINUX_CBO_BUILD_ID="{build_id}"']
subprocess.run([args.cc, *flags, str(source), '-o', str(binary)], check=True)
assembly = subprocess.check_output([args.objdump, '-d', str(binary)], text=True)
(args.output / 'linux-cbo.asm').write_text(assembly)
checks = {'rv64Elf': 'elf64-littleriscv' in assembly,
          'fixedExecutable': 'EXEC_P' in subprocess.check_output([args.objdump, '-f', str(binary)], text=True)}
instruction_addresses = {}
for name, immediate in [('inval', 0), ('clean', 1), ('flush', 2)]:
    match = re.search(rf'<cbo_{name}>:\n(.*?)(?=\n\n)', assembly, re.S)
    body = match[1] if match else ''
    # a0 is the single pointer argument; raw instruction encodings also work
    # with objdump versions predating the Zicbom mnemonic printer.
    encoding = f'{(immediate << 20) | (10 << 15) | (2 << 12) | 0x0f:08x}'
    checks[name] = encoding in body and body.count('fence') == 2
    instruction = re.search(rf'^\s*([0-9a-f]+):\s+{encoding}\b', body, re.M)
    if instruction:
        instruction_addresses[name] = hex(int(instruction[1], 16))
    else:
        checks[name] = False
if not all(checks.values()):
    raise RuntimeError(f'Compiled CBO instruction audit failed: {checks}')
module_result = {'built': False, 'reason': 'kernel paths were not supplied'}
if args.kernel_source or args.kernel_build:
    if not (args.kernel_source and args.kernel_build):
        parser.error('--kernel-source and --kernel-build must be supplied together')
    config = (args.kernel_build / '.config').read_text()
    if 'CONFIG_MODULES=y' not in config or 'CONFIG_MMU=y' not in config:
        raise RuntimeError('CBO module requires CONFIG_MODULES=y and CONFIG_MMU=y')
    # Keep all generated module files out of the source directory.
    dest = args.output / 'module'
    dest.mkdir(exist_ok=True)
    for p in module.iterdir():
        if p.is_file(): shutil.copyfile(p, dest / p.name)
    command = ['make', '-C', str(args.kernel_source.resolve()), f'O={args.kernel_build.resolve()}',
               'ARCH=riscv', f'CROSS_COMPILE={args.cross_compile}', f'M={dest.resolve()}', '-j1', 'modules']
    subprocess.run(command, check=True)
    ko = dest / 'chi_cbo_test.ko'
    module_assembly = subprocess.check_output([args.objdump, '-d', str(ko)], text=True)
    (dest / 'chi_cbo_test.asm').write_text(module_assembly)
    words = [int(word, 16) for word in re.findall(r'^\s*[0-9a-f]+:\s+([0-9a-f]{8})\s', module_assembly, re.M)]
    operations = {name: sum((word & ~(31 << 15)) == (0x200f | (code << 20)) for word in words)
                  for code, name in enumerate(['inval', 'clean', 'flush'])}
    vermagic = subprocess.check_output(['modinfo', '-F', 'vermagic', str(ko)], text=True).strip()
    release = (args.kernel_build / 'include/config/kernel.release').read_text().strip()
    symbols = subprocess.check_output([args.cross_compile + 'nm', '-u', str(ko)], text=True)
    (dest / 'undefined-symbols.txt').write_text(symbols)
    exports = {line.split()[1] for line in (args.kernel_build / 'Module.symvers').read_text().splitlines()}
    undefined = [line.split()[-1] for line in symbols.splitlines() if line.split()]
    unresolved = sorted(set(undefined) - exports)
    module_checks = {'versionMatches': vermagic.split()[0] == release,
                     'allSymbolsExported': not unresolved,
                     'actualCboInstructions': all(count >= 1 for count in operations.values()),
                     'sfencePresent': 'sfence.vma' in module_assembly,
                     'senvcfgPresent': 'senvcfg' in module_assembly}
    if not all(module_checks.values()):
        raise RuntimeError(f'Kernel module verification failed: {module_checks}, unresolved={unresolved}')
    module_result = {'built': True, 'path': str(ko), 'sha256': hashlib.sha256(ko.read_bytes()).hexdigest(),
                     'kernelRelease': release, 'vermagic': vermagic, 'checks': module_checks,
                     'opcodeCounts': operations, 'unresolvedSymbols': unresolved}
manifest = {'schemaVersion': 1, 'suite': 'native-linux-cbo', 'buildId': build_id,
            'sourceHashes': hashes, 'binary': binary.name,
            'binarySha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
            'toolchain': subprocess.check_output([args.cc, '--version'], text=True).splitlines()[0],
            'assemblyChecks': checks, 'flags': flags, 'module': module_result,
            'instructionAddresses': instruction_addresses,
            'phases': ['disabled', 'enabled'], 'cpus': 4, 'lineBytes': 64,
            'operations': ['inval', 'clean', 'flush'],
            'cases': ['aligned-rw', 'unaligned-rw', 'readonly', 'noaccess', 'unmapped'],
            'pteCases': ['pte-rw', 'pte-ro'],
            'boundary': 'Disabled phase verifies stock U-mode SIGILL. Enabled phase uses a temporary CAP_SYS_ADMIN module; D=0 evidence comes from controlled helper PFN mappings and real S-mode CBO instructions.'}
(args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(json.dumps({'binary': str(binary), 'buildId': build_id, 'assemblyChecks': checks, 'module': module_result}, indent=2))
