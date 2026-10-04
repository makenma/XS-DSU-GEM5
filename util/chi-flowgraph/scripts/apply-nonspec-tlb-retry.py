"""Install the checked, commit-authorized KMH delayed-TLB replay patch.

Only the exact pinned baseline or exact desired output is accepted. All three
destinations are checked before writing; repeated installs preserve mtimes.
--snapshot-only writes review artifacts without modifying the gem5 checkout.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT / 'native/gem5'))
from nonspec_tlb_retry_patch import BASELINE_SHA256, COMMIT, patches


def sha(data):
    return hashlib.sha256(data).hexdigest()


def plan(gem5):
    head = subprocess.check_output(
        ['git', '-C', str(gem5), 'rev-parse', 'HEAD'], text=True).strip()
    if head != COMMIT:
        raise ValueError('unexpected gem5 commit: ' + head)
    original = {}
    for relative, expected in BASELINE_SHA256.items():
        data = subprocess.check_output(
            ['git', '-C', str(gem5), 'show', COMMIT + ':' + relative])
        if sha(data) != expected:
            raise ValueError('unexpected pinned source hash: ' + relative)
        original[relative] = data
    desired = {p: s.encode('utf-8') for p, s in
               patches(lambda p: original[p].decode('utf-8')).items()}
    for relative, data in desired.items():
        current = (gem5 / relative).read_bytes()
        if current not in (original[relative], data):
            raise ValueError('refusing independent edits: ' + relative)
    return original, desired


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gem5', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--snapshot-only', action='store_true')
    args = parser.parse_args()
    gem5 = args.gem5.resolve()
    original, desired = plan(gem5)
    if args.check:
        print('Validated commit-authorized TLB replay patch; checkout unchanged.')
        return
    if not args.snapshot_only:
        for relative, data in desired.items():
            target = gem5 / relative
            if target.read_bytes() != data:
                target.write_bytes(data)
    snapshot = PROJECT / 'build' / (
        'nonspec-tlb-preview' if args.snapshot_only else 'nonspec-tlb-installed')
    records = []
    for relative, data in desired.items():
        for kind, content in [('original', original[relative]), ('patched', data)]:
            target = snapshot / kind / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        records.append({'path': relative, 'originalSha256': sha(original[relative]),
                        'patchedSha256': sha(data)})
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / 'manifest.json').write_text(json.dumps({
        'schemaVersion': 1, 'gem5Commit': COMMIT,
        'checkoutModified': not args.snapshot_only,
        'files': records,
    }, indent=2) + '\n', encoding='utf-8')
    print(('Prepared review snapshot: ' if args.snapshot_only else
           'Installed commit-authorized TLB replay patch: ') + str(snapshot))


if __name__ == '__main__':
    main()
