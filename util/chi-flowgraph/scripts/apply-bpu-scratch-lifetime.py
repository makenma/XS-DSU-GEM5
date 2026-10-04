"""Bound per-prediction TAGE-to-MGSC scratch on the pinned KMH gem5.

This does not clear predictor tables or saved training/squash metadata. Unknown
local edits are refused; repeated application preserves source mtimes.
"""
import argparse
import hashlib
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gem5', type=Path, required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    gem5 = args.gem5.resolve()
    project = Path(__file__).resolve().parent.parent
    commit = '65f3241a6cd486dffddaca251536f5c691af9a1f'
    relative = 'src/cpu/pred/btb/decoupled_bpred.hh'
    head = subprocess.check_output(['git', '-C', str(gem5), 'rev-parse', 'HEAD'], text=True).strip()
    if head != commit:
        raise ValueError('unexpected gem5 commit: ' + head)
    original = subprocess.check_output(['git', '-C', str(gem5), 'show', commit + ':' + relative])
    if hashlib.sha256(original).hexdigest() != '8a4c3955be7ad01a6672e87502931c0b6878b231df52865a29e6b789c8fd1dd1':
        raise ValueError('unexpected pinned predictor source')
    before = b'''            stagePred.indirectTargets.clear();
            stagePred.btbEntries.clear();
'''
    after = before + b'''            // Per-prediction TAGE -> MGSC communication, not predictor
            // tables or the saved training/squash metadata. Keep finalPred
            // copies bounded by the branches in the current prediction.
            stagePred.tageInfoForMgscs.clear();
'''
    if original.count(before) != 1:
        raise ValueError('ambiguous predictor reset context')
    desired = original.replace(before, after)
    target = gem5 / relative
    current = target.read_bytes()
    if current not in (original, desired):
        raise ValueError('refusing independent edits: ' + str(target))
    if args.check:
        print('Validated BPU scratch lifetime patch; checkout unchanged.')
        return
    if current != desired:
        target.write_bytes(desired)
    snapshot = project / 'build/bpu-scratch-installed' / relative
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_bytes(desired)
    print('Installed per-prediction BPU scratch cleanup: ' + str(target))


if __name__ == '__main__':
    main()
