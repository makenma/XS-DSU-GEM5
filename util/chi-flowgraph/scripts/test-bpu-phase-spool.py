"""Execute already-built helper/boundary tests and produce checked JSON evidence.

Does not compile or start gem5. Compilation commands/artifact provenance remain
the responsibility of the build driver; their hashes are included here.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise RuntimeError('helper failed: ' + result.stderr)
    data = json.loads(result.stdout)
    if data.get('status') != 'PASS' or not data.get('cases') or any(value is not True for value in data['cases'].values()):
        raise ValueError('helper did not report strict success')
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--boundary-binary', type=Path, required=True)
    parser.add_argument('--boundary-source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    evidence = {'schemaVersion': 1, 'status': 'FAIL', 'cases': {},
                'sourceSha256': sha(ROOT / 'native/gem5/BpuPhaseSpool.hh'),
                'testSourceSha256': sha(ROOT / 'tests/native/bpu_phase_spool_test.cc'),
                'binarySha256': sha(args.binary), 'boundaryBinarySha256': sha(args.boundary_binary),
                'boundarySourceSha256': sha(args.boundary_source), 'simulationsRun': False}
    try:
        with tempfile.TemporaryDirectory(prefix='bpu-spool-test-') as tmp:
            spool = run([str(args.binary.resolve()), tmp])
        boundary = run([str(args.boundary_binary.resolve())])
        failure = subprocess.run([str(args.binary.resolve()), '--write-error'], capture_output=True,
                                 text=True, timeout=30)
        if failure.returncode == 0 or 'BPU phase storage' not in failure.stderr:
            raise ValueError('write failure was not explicitly rejected')
        evidence['cases'] = {**spool['cases'], **boundary['cases'], 'writeErrorRejected': True}
        evidence['writeErrorReturnCode'] = failure.returncode
        evidence['status'] = 'PASS'
    except (RuntimeError, ValueError, subprocess.SubprocessError) as error:
        evidence['error'] = str(error)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(evidence, indent=2))
    return 0 if evidence['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
