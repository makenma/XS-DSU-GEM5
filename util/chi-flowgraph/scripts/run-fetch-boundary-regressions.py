"""Focused actual-CPU candidate regressions, separate from full Linux acceptance."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--gem5', type=Path, required=True)
parser.add_argument('--gem5-sha256', required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--include-page-faults', action='store_true')
args = parser.parse_args()
project = Path(__file__).resolve().parents[1]
source = Path('/root/chi-flowgraph-work/gem5')
binary = args.gem5.resolve(strict=True)
output = args.output.resolve()
if digest(binary) != args.gem5_sha256:
    raise ValueError('Candidate does not match expected immutable SHA')
output.mkdir(parents=True, exist_ok=False)
state = {'kind': 'fetch-boundary-candidate-regressions', 'linuxAcceptance': False,
         'gem5': str(binary), 'gem5Sha256': args.gem5_sha256,
         'includePageFaults': args.include_page_faults,
         'createdAtUtc': datetime.now(timezone.utc).isoformat(), 'steps': [], 'complete': False}


def save():
    (output / 'state.json').write_text(json.dumps(state, indent=2) + '\n')


def run(label, script, *arguments):
    if digest(binary) != args.gem5_sha256:
        raise ValueError('Candidate changed before ' + label)
    command = [sys.executable, str(project / 'scripts' / script), *map(str, arguments)]
    step = {'label': label, 'command': command, 'startedAtUtc': datetime.now(timezone.utc).isoformat()}
    state['steps'].append(step)
    save()
    print(json.dumps({'starting': label}), flush=True)
    with (output / (label + '.log')).open('wb') as log:
        child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        step['driverPid'] = child.pid
        save()
        step['hostExitCode'] = child.wait()
    step['finishedAtUtc'] = datetime.now(timezone.utc).isoformat()
    save()
    print(json.dumps({'finished': label, 'hostExitCode': step['hostExitCode']}), flush=True)
    if step['hostExitCode'] != 0:
        raise SystemExit(1)


save()
program_directories = [project / 'build/native-fetch-stream-fs-program',
                       project / 'build/native-fetch-boundary-cases-program']
if args.include_page_faults:
    program_directories.append(project / 'build/native-fetch-page-fault-program')
for programs in program_directories:
    manifest = json.loads((programs / 'manifest.json').read_text())
    for variant in manifest['variants']:
        case = variant['case']
        run('fetch-' + case, 'run-native-fetch-stream-fs.py', '--gem5', binary,
            '--programs', programs, '--case', case, '--output', output / ('fetch-' + case))

for case in ('clean', 'flush', 'inval', 'flush-fault'):
    run('cbo-' + case, 'run-native-cbo-tlb.py', '--gem5', binary,
        '--case', case, '--output', output / ('cbo-' + case))

for workload, program_sha, baseline_prefix in (
    ('rtl', '881aa515e8d919447f73086903eb89d0b219a26ead464b5eed024547bd9b9ed3', 'se-phase-storage-rtl-'),
    ('bpu-phase', '227efd913f4a91942dc3e117edd4e79efb34f96b4ad952c0f64f2120607070e7', 'se-bpu-phase-storage-'),
):
    for mode in ('on', 'off'):
        label = 'se-' + workload + '-' + mode
        destination = output / label
        run(label, 'run-native-bpu-phase.py', '--gem5', binary,
            '--gem5-sha256', args.gem5_sha256, '--gem5-source', source,
            '--program', project / ('build/native-' + workload + '-program'),
            '--program-sha256', program_sha, '--output', destination, '--trace', mode)
        extra = ['--without-trace'] if mode == 'off' else []
        run(label + '-equivalence', 'check-native-model-regression.py',
            '--before', project / ('build/linux-validation/' + baseline_prefix + mode),
            '--after', destination, '--output', output / (label + '-equivalence.json'), *extra)
state['complete'] = True
save()
print(json.dumps({'focusedRegressionsComplete': True, 'linuxAcceptance': False}), flush=True)
