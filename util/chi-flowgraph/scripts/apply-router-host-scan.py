"""Install two exact host-only router changes after the existing observer.

No stage, arbitration, credit, wakeup, or checkpoint semantics are changed.
The pinned baseline plus exact existing observer is reconstructed and hashed;
unknown edits are refused before any file is changed. --snapshot-only prepares
review artifacts without touching the gem5 checkout or observer snapshots.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT / 'native/gem5'))
from router_host_scan_patch import (
    BASELINE_SHA256, CC, COMMIT, HH, OBSERVED_CC_SHA256,
    observer_source, patches,
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def plan(gem5):
    head = subprocess.check_output(
        ['git', '-C', str(gem5), 'rev-parse', 'HEAD'], text=True).strip()
    if head != COMMIT:
        raise ValueError('unexpected gem5 commit: ' + head)
    baseline = {}
    for path, expected in BASELINE_SHA256.items():
        data = subprocess.check_output(
            ['git', '-C', str(gem5), 'show', COMMIT + ':' + path])
        if sha(data) != expected:
            raise ValueError('unexpected pinned router source: ' + path)
        baseline[path] = data
    observed = {CC: observer_source(baseline[CC].decode()).encode(), HH: baseline[HH]}
    if sha(observed[CC]) != OBSERVED_CC_SHA256:
        raise ValueError('unexpected reconstructed router observer')
    desired = {p: s.encode() for p, s in patches(
        {p: s.decode() for p, s in observed.items()}).items()}
    for path, data in desired.items():
        if (gem5 / path).read_bytes() not in (observed[path], data):
            raise ValueError('refusing independent edits or missing observer: ' + path)
    # The observer's next complete install must recognize our composed CC.
    # Never use arbitrary previous snapshots as permission to overwrite source.
    compatibility = [PROJECT / 'build/extended-installed' / CC,
                     PROJECT / 'build/gem5-installed/ChiRouterRefModel.cc']
    for path in compatibility:
        if path.exists() and path.read_bytes() not in (observed[CC], desired[CC]):
            raise ValueError('refusing independent observer snapshot: ' + str(path))
    return observed, desired, compatibility


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gem5', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--snapshot-only', action='store_true')
    args = parser.parse_args()
    gem5 = args.gem5.resolve()
    observed, desired, compatibility = plan(gem5)
    if args.check:
        print('Validated router host-scan patch; checkout unchanged.')
        return
    if not args.snapshot_only:
        for path, data in desired.items():
            target = gem5 / path
            if target.read_bytes() != data:
                target.write_bytes(data)
        for path in compatibility:
            if path.exists() and path.read_bytes() != desired[CC]:
                path.write_bytes(desired[CC])
    snapshot = PROJECT / 'build' / (
        'router-host-scan-preview' if args.snapshot_only else 'router-host-scan-installed')
    records = []
    for path, data in desired.items():
        for kind, content in [('original', observed[path]), ('patched', data)]:
            target = snapshot / kind / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        records.append({'path': path, 'upstreamSha256': BASELINE_SHA256[path],
                        'originalSha256': sha(observed[path]), 'patchedSha256': sha(data)})
    (snapshot / 'manifest.json').write_text(json.dumps({
        'schemaVersion': 1, 'gem5Commit': COMMIT,
        'checkoutModified': not args.snapshot_only, 'files': records,
        'changes': ['skip immutable disabled output-bank queues in hasWork',
                    'peek direction without copying flit payload'],
        'creditUpdateUnchanged': True,
    }, indent=2) + '\n', encoding='utf-8')
    print(('Prepared review snapshot: ' if args.snapshot_only else
           'Installed router host-scan patch: ') + str(snapshot))


if __name__ == '__main__':
    main()
