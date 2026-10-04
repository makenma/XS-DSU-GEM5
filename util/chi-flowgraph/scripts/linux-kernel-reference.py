#!/usr/bin/env python3
"""Prepare/audit a reference build; this is not a gem5 result checker."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import time


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(data)
    return result.hexdigest()


def prepare(root, out):
    manifest = json.loads((root / 'opt/chi/rootfs-manifest.json').read_text())
    assert manifest['preparedMode'] == 'kernel-build'
    recipe = root / 'opt/chi/run-kernel-build.sh'
    assert digest(recipe) == manifest['testArtifactsSha256']['opt/chi/run-kernel-build.sh']
    source = root / 'usr/src/linux-6.6.52'
    tree = hashlib.sha256()
    for path in sorted(source.rglob('*')):
        relative = str(path.relative_to(source)).encode()
        if path.is_symlink():
            tree.update(b'L\0' + relative + b'\0' + os.readlink(path).encode() + b'\0')
        elif path.is_file():
            tree.update(b'F\0' + relative + b'\0' + digest(path).encode() + b'\0')
    assert tree.hexdigest() == manifest['guestKernelBuild']['sourceTreeSha256']

    # Copy the exact frozen guest config/build commands and environment. Do not
    # call the guest acceptance runner: its topology/time/CPU/m5 evidence would
    # be inappropriate in QEMU user mode.
    text = recipe.read_text()
    environment = text[text.index('export PATH='):text.index('fail()')]
    config = text[text.index('make -C "$source_dir" O="$build_dir" ARCH=riscv tinyconfig'):text.index('echo CHI_KERNEL_BUILD_CONFIG_READY')]
    command = 'make -C "$source_dir" O="$build_dir" ARCH=riscv -j4 --output-sync=target Image'
    assert text.count(command) == 1
    assert 'KBUILD_BUILD_VERSION' not in environment  # Both fresh builds start at #1.
    runner = '#!/bin/sh\n# QEMU reference only; no guest acceptance markers or m5ops.\nset -eu\n' + environment
    runner += '''fail() { echo "KERNEL_REFERENCE_ERROR reason=$1"; exit 1; }
[ ! -e "$build_dir" ] || fail output_not_fresh
mkdir "$build_dir"
env | sort > /build/reference-environment.txt
{
    echo '[gcc]'; gcc --version
    echo '[target]'; gcc -dumpmachine
    echo '[full-version]'; gcc -dumpfullversion
    echo '[as]'; as --version
    echo '[ld]'; ld --version
    echo '[make]'; make --version
    echo '[cc1-path]'; gcc -print-prog-name=cc1
    echo '[cc1-sha256]'; sha256sum "$(gcc -print-prog-name=cc1)"
    echo '[libgcc-path]'; gcc -print-file-name=libgcc.a
    echo '[libgcc-sha256]'; sha256sum "$(gcc -print-file-name=libgcc.a)"
} > /build/reference-toolchain.txt
gcc -dumpspecs > /build/reference-gcc-specs.txt
''' + config
    runner += command.replace('-j4', '-j2') + ' > /build/kernel.make.log 2>&1\n'
    runner += '''test -s "$build_dir/arch/riscv/boot/Image"
test -s "$build_dir/vmlinux"
sha256sum "$build_dir/arch/riscv/boot/Image" "$build_dir/vmlinux" "$build_dir/.config" > /build/reference-guest-hashes.txt
echo KERNEL_REFERENCE_COMMANDS_COMPLETED
'''
    assert 'CHI_KERNEL_BUILD_' not in runner and 'guest-control' not in runner and 'm5_exit' not in runner
    target = root / 'opt/chi/kernel-reference.sh'
    target.write_text(runner)
    target.chmod(0o755)
    shutil.copyfile(target, out / 'kernel-reference.sh')
    shutil.copyfile(recipe, out / 'frozen-guest-recipe.sh')
    shutil.copyfile(root / 'opt/chi/rootfs-manifest.json', out / 'rootfs-manifest.json')
    inputs = {'sourceTreeSha256': tree.hexdigest(), 'frozenGuestRecipeSha256': digest(recipe),
              'referenceRunnerSha256': digest(target), 'baseDpkgStatusSha256': digest(root / 'var/lib/dpkg/status'),
              'nativeToolsFromRootfs': manifest['nativeTools'],
              'sourcePathInsideRoot': '/usr/src/linux-6.6.52', 'outputPathInsideRoot': '/build/kernel',
              'outputFresh': not (root / 'build/kernel').exists(),
              'recipeDifference': 'make parallelism -j2 instead of -j4; reference metadata replaces gem5 acceptance evidence'}
    assert inputs['outputFresh']
    (out / 'reference-inputs.json').write_text(json.dumps(inputs, indent=2) + '\n')


def finish(root, out):
    started = json.loads((out / 'reference-start.json').read_text())
    inputs = json.loads((out / 'reference-inputs.json').read_text())
    assert (out / 'exit-code.txt').read_text().strip() == '0'
    build = root / 'build/kernel'
    config = build / '.config'
    lines = set(config.read_text().splitlines())
    for setting in ('CONFIG_SMP=y', 'CONFIG_NR_CPUS=4', 'CONFIG_DEBUG_INFO_NONE=y',
                    'CONFIG_LOCALVERSION="-chi-guest-small"', 'CONFIG_BUILD_SALT=""',
                    'CONFIG_RANDSTRUCT_NONE=y'):
        assert setting in lines, setting
    for name in ('MODULE_SIG', 'MODULE_SIG_ALL', 'RANDSTRUCT', 'BLK_DEV_INITRD', 'LOCALVERSION_AUTO', 'IKHEADERS'):
        assert f'CONFIG_{name}=y' not in lines, f'nondeterministic/unexpected feature: {name}'
    assert (build / '.version').read_text().strip() == '1', 'not the first fresh Image build'
    image, elf = build / 'arch/riscv/boot/Image', build / 'vmlinux'
    with image.open('rb') as stream:
        header = stream.read(64)
    assert header[48:56] == b'RISCV\0\0\0' and header[56:60] == b'RSC\x05'
    with elf.open('rb') as stream:
        header = stream.read(64)
    assert header[:6] == b'\x7fELF\x02\x01' and struct.unpack_from('<H', header, 18)[0] == 243
    assert '[full-version]\n13.2.0\n' in (root / 'build/reference-toolchain.txt').read_text()
    env = dict(line.split('=', 1) for line in (root / 'build/reference-environment.txt').read_text().splitlines())
    for name, value in {'KBUILD_BUILD_USER': 'chi', 'KBUILD_BUILD_HOST': 'gem5',
                        'KBUILD_BUILD_TIMESTAMP': 'Mon Jan 1 00:00:00 UTC 2024',
                        'SOURCE_DATE_EPOCH': '1704067200', 'LC_ALL': 'C'}.items():
        assert env.get(name) == value, name
    artifacts = {}
    for label, path in (('image', image), ('vmlinux', elf), ('config', config),
                        ('makeLog', root / 'build/kernel.make.log')):
        artifacts[label] = {'path': str(path), 'sha256': digest(path), 'bytes': path.stat().st_size}
    expected_guest = {name: sha for sha, name in (line.split(None, 1) for line in
                     (root / 'build/reference-guest-hashes.txt').read_text().splitlines())}
    for name in ('image', 'vmlinux', 'config'):
        item = artifacts[name]
        inside = '/' + str(Path(item['path']).relative_to(root))
        assert expected_guest[inside] == item['sha256'], 'reference host/guest hash disagreement'
    objects = sum(1 for _ in build.rglob('*.o'))
    assert objects > 0
    make_text = (root / 'build/kernel.make.log').read_text()
    for marker in ('  CC ', '  LD      vmlinux', '  OBJCOPY arch/riscv/boot/Image'):
        assert marker in make_text, marker
    for relative, name in (('kernel/.config', 'kernel.config'), ('kernel.config.log', 'config.log'),
                           ('kernel.make.log', 'make.log'), ('reference-environment.txt', 'environment.txt'),
                           ('reference-toolchain.txt', 'toolchain.txt'), ('reference-gcc-specs.txt', 'gcc-specs.txt'),
                           ('reference-guest-hashes.txt', 'reference-guest-hashes.txt')):
        shutil.copyfile(root / 'build' / relative, out / name)
    result = {'schemaVersion': 1, 'referenceBuildCompleted': True, 'referenceOnly': True,
              'gem5Executed': False, 'gem5KernelBuildPassed': False,
              'profile': 'tinyconfig-smp4', 'sourceVersion': '6.6.52', 'target': 'Image',
              'jobs': 2, 'guestRequiredJobs': 4, 'execution': 'QEMU user-mode chroot',
              'rootfsSha256': started['rootfsSha256'], 'inputs': inputs, 'environment': env,
              'artifacts': artifacts, 'objectFiles': objects, 'buildVersion': 1,
              'referenceArtifactHashesRecomputedOnHost': True,
              'finishedUnixSeconds': time.time(), 'startedUnixSeconds': started['startedUnixSeconds'],
              'repeatedReferenceBuilds': 1, 'comparedWithGem5': False,
              'scope': 'Independent reference Image only; not a gem5 boot, memory-model test, CPU work measurement, or guest kernel-build acceptance. Compare future gem5 config/Image/vmlinux hashes separately; do not compare log hashes or elapsed time.'}
    (out / 'reference-result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'referenceOnly': True, 'referenceBuildCompleted': True,
                      'imageSha256': artifacts['image']['sha256'], 'imageBytes': artifacts['image']['bytes'],
                      'result': str(out / 'reference-result.json')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('prepare', 'finish'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--log-dir', type=Path, required=True)
    args = parser.parse_args()
    {'prepare': prepare, 'finish': finish}[args.phase](args.root, args.log_dir)
