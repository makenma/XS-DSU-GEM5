"""Build an isolated Linux supplement; never alter the running core or kernel build."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

PROJECT = Path(__file__).resolve().parents[1]
PARAMETERS = {'iterations': 128, 'seed': 1, 'pagesPerWriter': 64,
              'pressureBytesPerWriter': 1048576, 'pressureSamplesPerEpoch': 64, 'timeoutSeconds': 120}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def required(path):
    data = Path(path).read_bytes()
    if not data:
        raise ValueError('empty input: ' + str(path))
    return data


def prepare(work, suite_index):
    suite = json.loads(required(suite_index))
    root = suite_index.parent
    smoke = suite['stages']['litmus-smoke']
    for entry in suite['files']:
        path = (root / entry['path']).resolve()
        if not path.is_relative_to(root) or digest(required(path)) != entry['sha256']:
            raise ValueError('frozen core input changed: ' + str(path))
    random_dir = PROJECT / 'build/linux-random-memory'
    original = required(random_dir / 'manifest.json')
    manifest = json.loads(original)
    if manifest.get('defaultParameters') != PARAMETERS:
        raise ValueError('workload defaults differ from the fixed guest command')
    binary = (random_dir / manifest['binary']).resolve()
    if not binary.is_relative_to(random_dir.resolve()):
        raise ValueError('random binary escapes prepared directory')
    binary_data = required(binary)
    source_data = required(PROJECT / 'native/linux-tests/random-memory.c')
    if digest(binary_data) != manifest['binarySha256'] or digest(source_data) != manifest['sourceSha256']:
        raise ValueError('random workload source/binary identity mismatch')
    normalized = dict(manifest, binary='random-memory')
    payload = {
        'random/manifest.original.json': original,
        'random/manifest.json': canonical(normalized),
        'random/random-memory': binary_data,
        'random/source.c': source_data,
        'smoke/manifest.json': required(root / smoke['manifest']),
        'smoke/run.exe': required(root / smoke['binary']),
        'checkers/check-linux-litmus.py': required(root / smoke['checker']),
        'checkers/check-linux-random-memory.py': required(PROJECT / 'scripts/check-linux-random-memory.py'),
        'guest/init': required(PROJECT / 'native/linux-platform/random-memory-init.sh'),
        'guest/busybox': required(work / 'boot-rootfs/bin/busybox'),
        'guest/guest-control': required(work / 'boot-rootfs/bin/guest-control'),
        'kernel.config': required(PROJECT / 'build/linux-validation/suite-build/.config'),
    }
    if digest(payload['smoke/run.exe']) != json.loads(payload['smoke/manifest.json'])['binarySha256']:
        raise ValueError('smoke binary differs from its frozen manifest')
    identity = {'schemaVersion': 1, 'parameters': PARAMETERS, 'coreInputSha256': suite['inputSha256'],
                'files': [{'path': name, 'sha256': digest(data)} for name, data in sorted(payload.items())]}
    sha = digest(canonical(identity))
    index = dict(identity, inputSha256=sha, guestExecuted=False,
                 scope='Prepared Linux smoke plus concurrent random migration inputs; not guest PASS.')
    destination = work / 'artifacts/linux-random-memory-inputs' / sha
    destination.parent.mkdir(parents=True, exist_ok=True)
    contents = dict(payload, **{'index.json': canonical(index)})
    if destination.exists():
        actual = {p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_file()}
        if actual != set(contents) or any(p.is_symlink() for p in destination.rglob('*')):
            raise ValueError('existing frozen input inventory changed')
        for name, data in contents.items():
            if required(destination / name) != data:
                raise ValueError('existing frozen input changed: ' + name)
    else:
        destination.mkdir()
        for name, data in contents.items():
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream:
                stream.write(data)
    return destination, index


def config_values(path):
    return {line.split('=', 1)[0]: line for line in path.read_text().splitlines()
            if line.startswith('CONFIG_') or line.startswith('# CONFIG_')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--suite-inputs', type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.jobs <= 4:
        parser.error('jobs must be between 1 and 4')
    work = args.work.resolve()
    bundle, index = prepare(work, args.suite_inputs.resolve())
    short = index['inputSha256'][:12]
    output = work / ('random-memory-kernel-build-' + short)
    opensbi_out = work / ('opensbi-random-memory-' + short)
    logs = PROJECT / 'build/linux-validation/random-memory-build' / short
    logs.mkdir(parents=True, exist_ok=True)
    lock = (logs / 'build.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if (output.exists() or opensbi_out.exists()) and not args.resume:
        raise ValueError('isolated build already exists; inspect it before --resume')
    kernel = work / 'sources/linux-6.6.52'
    seed = work / 'kernel-build'
    if not output.exists():
        # Copy objects, never hard-link them to an input of other builds.
        # Large generated images and embedded rootfs data will be regenerated.
        def ignore(directory, names):
            relative = Path(directory).relative_to(seed)
            if relative == Path('.'):
                return [n for n in names if n.startswith(('vmlinux', '.tmp_vmlinux'))]
            if relative == Path('usr'):
                return [n for n in names if n.startswith('initramfs')]
            if relative == Path('arch/riscv/boot'):
                return [n for n in names if n.startswith('Image')]
            return []
        shutil.copytree(seed, output, symlinks=True, ignore=ignore)
    shutil.copy2(bundle / 'kernel.config', output / '.config')
    entries = [f'dir /{name} {mode} 0 0' for name, mode in
               [('dev', '755'), ('proc', '755'), ('sys', '755'), ('tmp', '1777'),
                ('run', '755'), ('bin', '755'), ('sbin', '755'), ('usr', '755'),
                ('usr/bin', '755'), ('usr/sbin', '755')]]
    entries += ['nod /dev/console 600 0 0 c 5 1', 'nod /dev/null 666 0 0 c 1 3']
    for target, source in [('init', 'guest/init'), ('bin/busybox', 'guest/busybox'),
                           ('bin/guest-control', 'guest/guest-control'),
                           ('bin/litmus-smoke', 'smoke/run.exe'), ('bin/random-memory', 'random/random-memory')]:
        entries.append(f'file /{target} {bundle / source} 755 0 0')
    entries += [f'slink /bin/{name} busybox 777 0 0' for name in 'sh mount echo cat uname sleep sync'.split()]
    initramfs = output / 'random-memory-initramfs.list'
    initramfs.write_text('\n'.join(entries) + '\n')
    environment = os.environ.copy()
    environment.update(KBUILD_BUILD_USER='chi', KBUILD_BUILD_HOST='native-validation')
    def run(command, name):
        with (logs / name).open('wb') as stream:
            subprocess.run(list(map(str, command)), env=environment, stdout=stream, stderr=subprocess.STDOUT, check=True)
    run([kernel / 'scripts/config', '--file', output / '.config', '--set-str', 'INITRAMFS_SOURCE', initramfs], 'config-edit.log')
    base = ['make', '-C', kernel, 'O=' + str(output), 'ARCH=riscv', 'CROSS_COMPILE=riscv64-linux-gnu-']
    run([*base, 'olddefconfig'], 'config.log')
    before, after = config_values(bundle / 'kernel.config'), config_values(output / '.config')
    changes = [key for key in sorted(before.keys() | after.keys()) if before.get(key) != after.get(key)]
    if changes != ['CONFIG_INITRAMFS_SOURCE']:
        raise ValueError('unexpected kernel configuration changes: ' + repr(changes))
    run([*base, '-j' + str(args.jobs), 'Image'], 'kernel-build.log')
    image = output / 'arch/riscv/boot/Image'
    run(['make', '-C', work / 'sources/opensbi-1.3.1', 'O=' + str(opensbi_out),
         'PLATFORM=generic', 'CROSS_COMPILE=riscv64-linux-gnu-', 'FW_PAYLOAD=y',
         'FW_PAYLOAD_PATH=' + str(image), 'FW_PAYLOAD_OFFSET=0x200000', '-j' + str(args.jobs)], 'opensbi-build.log')
    firmware = opensbi_out / 'platform/generic/firmware/fw_payload.elf'
    result = {'schemaVersion': 1, 'built': True, 'guestExecuted': False,
              'inputIndex': str(bundle / 'index.json'), 'inputSha256': index['inputSha256'],
              'firmware': str(firmware), 'firmwareSha256': digest(required(firmware)),
              'image': str(image), 'imageSha256': digest(required(image)),
              'kernelConfig': str(output / '.config'), 'kernelConfigSha256': digest(required(output / '.config')),
              'kernelConfigChanges': changes, 'parameters': PARAMETERS,
              'scope': 'Build-only supplemental firmware. Actual Linux execution and independent checkers remain required.'}
    (logs / 'build-result.json').write_bytes(canonical(result))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
