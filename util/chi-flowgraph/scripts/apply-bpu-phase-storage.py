"""Install/review the standalone bounded BPU phase storage candidate.

Requires the exact pinned source plus the model05 scratch-lifetime fix. Refuses
independent edits and does not edit the default observer installer or snapshots.
Apply the independent BPU text mirror separately. Do not re-run the older
scratch installer afterward: its strict guard intentionally rejects this HH.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'native/gem5'))
from bpu_phase_storage_patch import (
    BASELINE_SHA256, CC, COMMIT, HELPER, HH, STATS,
    UPSTREAM_HH_SHA256, patches, scratch_source,
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def plan(source):
    head = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    if head != COMMIT:
        raise ValueError('unexpected gem5 commit: ' + head)
    original = {}
    for path in (HH, CC, STATS):
        raw = subprocess.check_output(['git', '-C', str(source), 'show', COMMIT + ':' + path])
        if path == HH:
            if digest(raw) != UPSTREAM_HH_SHA256:
                raise ValueError('unexpected pinned HH')
            raw = scratch_source(raw.decode('utf-8')).encode('utf-8')
        if digest(raw) != BASELINE_SHA256[path]:
            raise ValueError('unexpected pinned baseline: ' + path)
        original[path] = raw
    desired = {name: text.encode('utf-8') for name, text in
               patches({name: data.decode('utf-8') for name, data in original.items()}).items()}
    desired[HELPER] = (PROJECT / 'native/gem5/BpuPhaseSpool.hh').read_bytes()
    # Check ALL inputs before any mutation. An already installed exact patch is safe.
    for name, data in desired.items():
        target = source / name
        current = target.read_bytes() if target.exists() else None
        if name == HELPER:
            if current not in (None, data):
                raise ValueError('refusing independent helper: ' + str(target))
        elif current not in (original[name], data):
            raise ValueError('refusing independent edits/missing scratch fix: ' + str(target))
    return original, desired


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--preview', type=Path, help='Write review copies only; source stays untouched')
    args = parser.parse_args()
    source = args.source.resolve()
    original, desired = plan(source)
    report = {'schemaVersion': 1, 'source': str(source), 'commit': COMMIT,
              'sourceModified': not args.check and args.preview is None,
              'files': [{'path': name, 'originalSha256': digest(original[name]) if name in original else None,
                         'desiredSha256': digest(data)} for name, data in desired.items()],
              'defaultInstallerIntegrated': False, 'simulationsRun': False}
    if args.preview is not None:
        for group, files in (('original', original), ('patched', desired)):
            for name, data in files.items():
                path = args.preview / group / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        (args.preview / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    elif not args.check:
        for name, data in desired.items():
            path = source / name
            if not path.exists() or path.read_bytes() != data:
                path.write_bytes(data)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
