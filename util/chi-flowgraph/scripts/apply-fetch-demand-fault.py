"""Install the pinned precise-demand Fetch fault fix after model09.

Both Fetch source files and all twelve unchanged BPU prerequisites are checked
before any write. Exact already-installed files are accepted; independent edits
are rejected. --check never writes. No build or simulation is started.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
PACKAGE = PROJECT / 'native/gem5'
COMMIT = '65f3241a6cd486dffddaca251536f5c691af9a1f'
PATCH_SHA256 = 'abde4494dc7369ba7fbc6e859a433649b9100dbe9aff341082d6461d1ac408ad'
MANIFEST_SHA256 = '5a91337e41c7ee01027c3126987830854736ef23937a19746c1efc26a1c716b4'
FILES = {'src/cpu/o3/fetch.cc', 'src/cpu/o3/fetch.hh'}
DEPENDENCIES = {'src/cpu/pred/btb/mbtb.cc', 'src/cpu/pred/btb/abtb.cc', 'src/cpu/pred/btb/timed_base_pred.hh', 'src/cpu/pred/btb/decoupled_bpred.hh', 'src/cpu/pred/btb/decoupled_bpred.cc', 'src/cpu/pred/btb/mbtb.hh', 'src/cpu/pred/btb/abtb.hh', 'src/cpu/pred/btb/common.hh', 'src/cpu/pred/btb/btb_ubtb.cc', 'src/cpu/pred/btb/BpuPhaseSpool.hh', 'src/cpu/pred/btb/btb_ubtb.hh', 'src/cpu/pred/btb/decoupled_bpred_stats.cc'}
HUNK = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:.*)\n$')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def parse_patch(raw):
    """Parse only exact, complete unified hunks; no fuzzy patch application."""
    lines = raw.decode('utf-8').splitlines(keepends=True)
    result = {}
    pos = 0
    while pos < len(lines):
        if not lines[pos].startswith('--- a/') or pos + 1 >= len(lines):
            raise ValueError('invalid patch file header')
        name = lines[pos][6:].rstrip('\n')
        if lines[pos + 1] != '+++ b/' + name + '\n' or name in result:
            raise ValueError('mismatched/duplicate patch path: ' + name)
        pos += 2
        hunks = []
        while pos < len(lines) and not lines[pos].startswith('--- a/'):
            match = HUNK.fullmatch(lines[pos])
            if not match:
                raise ValueError('invalid hunk header: ' + lines[pos].rstrip())
            old_start, old_count, new_start, new_count = (
                int(match[1]), int(match[2] or 1), int(match[3]), int(match[4] or 1))
            pos += 1
            body, old_seen, new_seen = [], 0, 0
            while old_seen < old_count or new_seen < new_count:
                if pos >= len(lines) or lines[pos][:1] not in (' ', '-', '+'):
                    raise ValueError('truncated or unsupported patch hunk')
                line = lines[pos]
                body.append(line)
                old_seen += line[0] != '+'
                new_seen += line[0] != '-'
                pos += 1
                if old_seen > old_count or new_seen > new_count:
                    raise ValueError('patch hunk counts do not match')
            hunks.append((old_start, old_count, new_start, new_count, body))
        if not hunks:
            raise ValueError('empty patch for ' + name)
        result[name] = hunks
    return result


def patched(raw, hunks, reverse=False):
    lines = raw.decode('utf-8').splitlines(keepends=True)
    result, cursor = [], 0
    for old_start, old_count, new_start, new_count, body in hunks:
        start, count = (new_start, new_count) if reverse else (old_start, old_count)
        index = start - 1 if count else start
        removed = '+' if reverse else '-'
        added = '-' if reverse else '+'
        expected = [line[1:] for line in body if line[0] != added]
        replacement = [line[1:] for line in body if line[0] != removed]
        if index < cursor or lines[index:index + count] != expected:
            raise ValueError('source does not match the exact patch hunk')
        result.extend(lines[cursor:index])
        result.extend(replacement)
        cursor = index + count
    result.extend(lines[cursor:])
    return ''.join(result).encode('utf-8')


def load_package():
    manifest_raw = (PACKAGE / 'fetch_demand_fault.json').read_bytes()
    if digest(manifest_raw) != MANIFEST_SHA256:
        raise ValueError('frozen Fetch manifest SHA mismatch')
    manifest = json.loads(manifest_raw)
    raw = (PACKAGE / 'fetch_demand_fault.patch').read_bytes()
    if manifest.get('schemaVersion') != 1 or manifest.get('commit') != COMMIT:
        raise ValueError('unsupported package identity')
    if digest(raw) != PATCH_SHA256 or manifest.get('patchSha256') != PATCH_SHA256:
        raise ValueError('frozen Fetch patch SHA mismatch')
    hunks = parse_patch(raw)
    records = manifest.get('files', [])
    deps = manifest.get('requiredUnchangedFiles', [])
    if len(records) != len(FILES) or {item['path'] for item in records} != FILES:
        raise ValueError('package must contain exactly the two Fetch files')
    if set(hunks) != FILES:
        raise ValueError('patch file inventory mismatch')
    if len(deps) != len(DEPENDENCIES) or {item['path'] for item in deps} != DEPENDENCIES:
        raise ValueError('BPU prerequisite inventory mismatch')
    return manifest, hunks


def inside(source, name):
    path = source / name
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(source) or resolved != path:
        raise ValueError('refusing symlink or escaped source path: ' + name)
    if not resolved.is_file():
        raise ValueError('not a source file: ' + name)
    return path


def plan(source):
    source = source.resolve(strict=True)
    head = subprocess.check_output(
        ['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    if head != COMMIT:
        raise ValueError('unexpected gem5 commit: ' + head)
    manifest, hunks = load_package()
    required = {}
    for item in manifest['requiredUnchangedFiles']:
        path = inside(source, item['path'])
        raw = path.read_bytes()
        if digest(raw) != item['sha256']:
            raise ValueError('missing/modified BPU prerequisite: ' + item['path'])
        required[path] = raw
    changes, states = {}, []
    # Prepare and verify every candidate before the caller may write any file.
    for item in manifest['files']:
        name = item['path']
        path = inside(source, name)
        raw = path.read_bytes()
        sha = digest(raw)
        if sha == item['beforeSha256']:
            desired = patched(raw, hunks[name])
            if digest(desired) != item['afterSha256']:
                raise ValueError('patched SHA mismatch: ' + name)
            state = 'pending'
        elif sha == item['afterSha256']:
            original = patched(raw, hunks[name], reverse=True)
            if digest(original) != item['beforeSha256']:
                raise ValueError('reverse patch SHA mismatch: ' + name)
            desired, state = raw, 'installed'
        else:
            raise ValueError('refusing independent edits or wrong prerequisite: ' + name)
        required[path] = raw
        changes[path] = desired
        states.append({'path': name, 'state': state,
                       'beforeSha256': item['beforeSha256'],
                       'afterSha256': item['afterSha256']})
    return required, changes, states


def install(required, changes):
    # Recheck all files, including prerequisites, before creating replacement files.
    for path, raw in required.items():
        if path.read_bytes() != raw:
            raise ValueError('source changed after validation: ' + str(path))
    staged = []
    written = 0
    try:
        for path, desired in changes.items():
            if required[path] == desired:
                continue
            with tempfile.NamedTemporaryFile(prefix=path.name + '.demand-fault-',
                                             dir=path.parent, delete=False) as handle:
                temp = Path(handle.name)
                staged.append((path, temp))
                handle.write(desired)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp, stat.S_IMODE(path.stat().st_mode))
        for path, temp in staged:
            if path.read_bytes() != required[path]:
                raise ValueError('source changed before replacement: ' + str(path))
            os.replace(temp, path)
            written += 1
    finally:
        for _, temp in staged:
            if temp.exists():
                temp.unlink()
    return written


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--check', action='store_true', help='Validate all inputs without writing')
    args = parser.parse_args()
    try:
        required, changes, states = plan(args.source)
        written = 0 if args.check else install(required, changes)
        report = {'schemaVersion': 1, 'status': 'PASS',
                  'mode': 'check' if args.check else 'install',
                  'source': str(args.source.resolve()), 'commit': COMMIT,
                  'patchSha256': PATCH_SHA256, 'sourceModified': written != 0,
                  'filesWritten': written, 'files': states,
                  'buildOrSimulationRun': False,
                  'validationScope': 'Exact source installation only, not CPU/protocol validation'}
        print(json.dumps(report, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(json.dumps({'schemaVersion': 1, 'status': 'FAIL', 'error': str(error)}, indent=2))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
