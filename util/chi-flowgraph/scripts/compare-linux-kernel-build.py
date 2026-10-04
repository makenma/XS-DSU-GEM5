#!/usr/bin/env python3
"""Compare completed gem5 guest evidence with an independent QEMU reference."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re

CHECKER_PATH = Path(__file__).with_name('check-linux-kernel-build.py')
spec = importlib.util.spec_from_file_location('actual_kernel_build_checker', CHECKER_PATH)
actual = importlib.util.module_from_spec(spec)
spec.loader.exec_module(actual)


def compare(text, reference):
    # Always require the existing actual-work checker before comparing hashes.
    guest = actual.check(text)
    result = {'schemaVersion': 1, 'status': 'INCOMPLETE', 'passed': False,
              'guestBuild': guest, 'differences': [], 'referenceOnly': False,
              'gem5ArtifactHashesRecomputedOnHost': False,
              'scope': 'Compare guest-reported hashes only after its four-hart make -j4 evidence passes; the independent reference cannot replace that execution. A mismatch requires diagnosis and is not attributed to CHI.'}
    if not guest['passed']:
        if 'CHI_KERNEL_BUILD_FAIL' in text or 'CHI_KERNEL_BUILD_END ' in text:
            result['status'] = 'GUEST_FAILED'
        result['reason'] = 'Actual gem5 guest kernel-build acceptance has not passed.'
        return result
    if reference is None:
        result['reason'] = 'Independent reference manifest is missing.'
        return result
    try:
        if not isinstance(reference, dict):
            raise ValueError('reference manifest must be an object')
        expected = {'schemaVersion': 1, 'referenceOnly': True, 'referenceBuildCompleted': True,
                    'referenceArtifactHashesRecomputedOnHost': True, 'gem5Executed': False,
                    'execution': 'QEMU user-mode chroot', 'profile': 'tinyconfig-smp4',
                    'sourceVersion': '6.6.52', 'target': 'Image'}
        for key, value in expected.items():
            if type(reference.get(key)) is not type(value) or reference[key] != value:
                raise ValueError(f'invalid reference {key}')
        artifacts = reference['artifacts']
        for name in ('config', 'image', 'vmlinux'):
            artifact = artifacts[name]
            if not re.fullmatch('[0-9a-f]{64}', artifact['sha256']):
                raise ValueError(f'invalid reference {name} SHA256')
            if type(artifact['bytes']) is not int or artifact['bytes'] <= 0:
                raise ValueError(f'invalid reference {name} size')
        pairs = [('configSha256', artifacts['config']['sha256']),
                 ('imageBytes', artifacts['image']['bytes']),
                 ('imageSha256', artifacts['image']['sha256']),
                 ('vmlinuxSha256', artifacts['vmlinux']['sha256'])]
        for field, expected_value in pairs:
            observed = guest['end'][field]
            if observed != expected_value:
                result['differences'].append({'field': field, 'guest': observed, 'reference': expected_value})
        result['status'] = 'MISMATCH' if result['differences'] else 'MATCH'
        result['passed'] = not result['differences']
        result['reason'] = ('Config/Image/vmlinux differences require diagnosis of inputs, build reproducibility and execution.'
                            if result['differences'] else 'Accepted gem5 guest build matches the independent reference hashes and Image size.')
        return result
    except (KeyError, TypeError, ValueError) as error:
        result['status'] = 'INVALID_REFERENCE'
        result['reason'] = str(error)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', type=Path, required=True)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = args.serial.read_bytes() if args.serial.is_file() else b''
    reference_raw = args.reference.read_bytes() if args.reference.is_file() else b''
    try:
        reference = json.loads(reference_raw) if reference_raw else None
    except (ValueError, UnicodeDecodeError):
        reference = {}
    result = compare(raw.decode(errors='replace'), reference)
    result['serialPath'] = str(args.serial)
    result['serialExists'] = args.serial.is_file()
    result['serialSha256'] = hashlib.sha256(raw).hexdigest() if raw else None
    result['referencePath'] = str(args.reference)
    result['referenceManifestSha256'] = hashlib.sha256(reference_raw).hexdigest() if reference_raw else None
    result['guestCheckerPath'] = str(CHECKER_PATH)
    result['guestCheckerSha256'] = hashlib.sha256(CHECKER_PATH.read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'passed': result['passed'],
                      'differences': result['differences'], 'output': str(args.output)}))
    raise SystemExit(0 if result['passed'] else 2 if result['status'] == 'INCOMPLETE' else 1)


if __name__ == '__main__':
    main()
