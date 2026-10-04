"""Install passive per-instance TXT mirrors; independent of oracle/storage patch."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'native/gem5'))
from bpu_phase_mirror_patch import COMMIT, BASELINE_SHA256, prepare


def install(module_prepare, baseline, label, argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gem5', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--snapshot-only', action='store_true')
    args = parser.parse_args(argv)
    gem5 = args.gem5.resolve()
    head = subprocess.check_output(['git', '-C', str(gem5), 'rev-parse', 'HEAD'], text=True).strip()
    if head != COMMIT:
        raise ValueError('unexpected gem5 commit: ' + head)
    plans = {path: module_prepare(path, (gem5 / path).read_bytes()) for path in baseline}
    if args.check:
        print(json.dumps({'status': 'recognized', 'patch': label, 'checkoutModified': False}))
        return
    snapshot = PROJECT / 'build' / (label + ('-preview' if args.snapshot_only else '-installed'))
    records = []
    for path, (original, desired) in plans.items():
        for kind, data in [('original', original), ('patched', desired)]:
            target = snapshot / kind / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        records.append({'path': path, 'originalSha256': hashlib.sha256(original).hexdigest(),
                        'patchedSha256': hashlib.sha256(desired).hexdigest()})
    if not args.snapshot_only:
        for path, (_, desired) in plans.items():
            if (gem5 / path).read_bytes() != desired:
                (gem5 / path).write_bytes(desired)
    (snapshot / 'manifest.json').write_text(json.dumps({
        'schemaVersion': 1, 'gem5Commit': COMMIT,
        'checkoutModified': not args.snapshot_only, 'files': records,
    }, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'snapshot': str(snapshot), 'checkoutModified': not args.snapshot_only}))


if __name__ == '__main__':
    install(prepare, BASELINE_SHA256, 'bpu-phase-mirror')
