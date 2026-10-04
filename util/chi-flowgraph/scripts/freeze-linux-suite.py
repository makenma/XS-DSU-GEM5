"""Freeze prepared Linux suite inputs; never build or execute guest tests.

stdout contains only {"index": "/absolute/content-hash/index.json"} on success.
Original manifests are preserved byte-for-byte alongside manifests whose file
references are made local to the snapshot. Existing snapshots are verified and
reused, never repaired or overwritten.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import sys


PROJECT = Path(__file__).resolve().parents[1]
STAGES = {
    'litmus-smoke': ('linux-litmus-smoke', 'generated/run.exe', 'check-linux-litmus.py',
                     'official-diy7-riscv-linux'),
    'litmus-large': ('linux-litmus-large', 'generated/run.exe', 'check-linux-litmus.py',
                     'official-diy7-riscv-linux'),
    'pthreads': ('linux-tests', 'linux-suite', 'check-linux-suite.py', 'native-linux-pthreads'),
    'cbo': ('linux-cbo', 'linux-cbo', 'check-linux-cbo.py', 'native-linux-cbo'),
    'uncached': ('linux-uncached', 'linux-uncached', 'check-linux-uncached.py', 'native-linux-uncached'),
}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + '\n').encode()


def read_required(path):
    if not path.is_file():
        raise ValueError(f'missing required input: {path}')
    data = path.read_bytes()
    if not data:
        raise ValueError(f'empty required input: {path}')
    return data


def check_hash(data, expected, label):
    if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected):
        raise ValueError(f'invalid declared SHA256: {label}')
    if sha256(data) != expected:
        raise ValueError(f'SHA256 mismatch: {label}')


def source_reference(parent, reference):
    if not isinstance(reference, str) or not reference:
        raise ValueError('empty or non-string manifest file reference')
    path = Path(reference)
    return (path if path.is_absolute() else parent / path).resolve()


def prepare(project):
    """Read each input once, so copying cannot race a later source rewrite."""
    project = Path(project).resolve()
    payload, stages = {}, {}
    for stage, (folder, binary_relative, checker, suite) in STAGES.items():
        source = project / 'build' / folder
        original = read_required(source / 'manifest.json')
        try:
            manifest = json.loads(original)
        except (ValueError, UnicodeDecodeError) as error:
            raise ValueError(f'{stage}: malformed manifest') from error
        if not isinstance(manifest, dict) or manifest.get('schemaVersion') != 1:
            raise ValueError(f'{stage}: wrong manifest schema')
        if manifest.get('suite') != suite:
            raise ValueError(f'{stage}: wrong manifest suite')
        if stage.startswith('litmus-') and manifest.get('profile') != stage.removeprefix('litmus-'):
            raise ValueError(f'{stage}: wrong litmus profile')
        binary_source = (source / binary_relative).resolve()
        binary = read_required(binary_source)
        check_hash(binary, manifest.get('binarySha256'), f'{stage} binary')
        references = [key for key in ('binary', 'guestBinary', 'binaryPath') if key in manifest]
        if not references or any(source_reference(source, manifest[key]) != binary_source
                                 for key in references):
            raise ValueError(f'{stage}: manifest does not reference the configured binary')
        normalized = copy.deepcopy(manifest)
        prefix = 'stages/' + stage + '/'
        for key in references:
            normalized[key] = binary_source.name
        # All snapshot checkers can use manifest.parent / manifest['binary'].
        normalized['binary'] = binary_source.name
        entry = {'manifest': prefix + 'manifest.json',
                 'originalManifest': prefix + 'manifest.original.json',
                 'checker': 'checkers/' + checker, 'binary': prefix + binary_source.name}
        if stage in ('cbo', 'uncached'):
            module = manifest.get('module')
            if not isinstance(module, dict) or module.get('built') is not True:
                raise ValueError(f'{stage}: matching kernel module has not been built')
            module_source = source_reference(source, module.get('path'))
            if not module_source.is_relative_to(source.resolve()):
                raise ValueError(f'{stage}: module path escapes its prepared artifact directory')
            module_data = read_required(module_source)
            check_hash(module_data, module.get('sha256'), f'{stage} module')
            module_relative = 'module/' + module_source.name
            normalized['module']['path'] = module_relative
            payload[prefix + module_relative] = module_data
            entry['module'] = prefix + module_relative
        if stage == 'uncached':
            entry['requiresFsConfig'] = True
        payload[entry['originalManifest']] = original
        payload[entry['manifest']] = json_bytes(normalized)
        payload[entry['binary']] = binary
        # The two litmus profiles deliberately share one identical checker.
        if entry['checker'] not in payload:
            payload[entry['checker']] = read_required(project / 'scripts' / checker)
        stages[stage] = entry
    torture = 'checkers/check-kernel-torture.py'
    payload[torture] = read_required(project / 'scripts/check-kernel-torture.py')
    stages['torture'] = {'checker': torture}
    files = [{'path': path, 'sha256': sha256(data)} for path, data in sorted(payload.items())]
    identity = {'schemaVersion': 1, 'files': files, 'stages': stages}
    snapshot_hash = sha256(json_bytes(identity))
    index = {**identity, 'inputSha256': snapshot_hash, 'execution': 'NOT_RUN',
             'manifestPolicy': 'Original bytes retained; runtime manifest file references are relative to its own directory.',
             'runtimeFsConfig': 'uncached requires the actual run output/flowgraph-config.json; it is not a prepared suite input.'}
    return payload, index


def verify_existing(directory, payload, index):
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError(f'existing snapshot is not a regular directory: {directory}')
    expected = {**payload, 'index.json': json_bytes(index)}
    actual = set()
    for path in directory.rglob('*'):
        if path.is_symlink():
            raise ValueError(f'symlink in immutable snapshot: {path}')
        if path.is_file():
            actual.add(path.relative_to(directory).as_posix())
    if actual != set(expected):
        raise ValueError(f'existing snapshot inventory differs; refusing overwrite: {directory}')
    for relative, data in expected.items():
        if read_required(directory / relative) != data:
            raise ValueError(f'existing snapshot changed; refusing overwrite: {directory / relative}')


def freeze(project, output_root):
    payload, index = prepare(project)
    output_root = Path(output_root).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    directory = output_root / index['inputSha256']
    try:
        # Exclusive creation is the no-overwrite guard, including concurrent
        # invocations. A crashed partial directory is rejected on later runs.
        directory.mkdir()
    except FileExistsError:
        verify_existing(directory, payload, index)
        return directory / 'index.json'
    for relative, data in {**payload, 'index.json': json_bytes(index)}.items():
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as destination:
            destination.write(data)
    # These permissions are advisory; every reuse/gate still checks hashes.
    for path in directory.rglob('*'):
        if path.is_file():
            path.chmod(0o444)
    for path in sorted((p for p in directory.rglob('*') if p.is_dir()), reverse=True):
        path.chmod(0o555)
    directory.chmod(0o555)
    verify_existing(directory, payload, index)
    return directory / 'index.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--project', type=Path, default=PROJECT,
                        help='Prepared project root (primarily for isolated fixture checks)')
    args = parser.parse_args()
    try:
        index = freeze(args.project, args.output_root)
    except (ValueError, OSError) as error:
        print(f'Freeze failed: {error}', file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps({'index': str(index.resolve())}))


if __name__ == '__main__':
    main()
