"""Run an isolated actual-CPU fetch diagnostic; never a Linux suite result."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--gem5', type=Path, required=True)
parser.add_argument('--case', required=True)
parser.add_argument('--programs', type=Path, help='Explicit independently built regression firmware directory')
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
programs = args.programs or root / 'build/native-fetch-stream-fs-program'
manifest = json.loads((programs / 'manifest.json').read_text())
variant = next(row for row in manifest['variants'] if row['case'] == args.case)
binary = args.gem5.resolve(strict=True)
firmware = programs / variant['elf']
assert sha256(firmware) == variant['sha256']
source = root / manifest.get('sourceRelativePath', 'native/gem5/native-fetch-stream-fs.S')
assert sha256(source) == manifest['sourceSha256']
args.output.mkdir(parents=True, exist_ok=False)
output = args.output.resolve()
config = root / 'native/gem5/native_l2_fs.py'
env = dict(os.environ)
env['CHI_GEM5_DIR'] = '/root/chi-flowgraph-work/gem5'
env['CHI_FLOWGRAPH_OUT'] = str(output / 'raw.jsonl')
command = [str(binary), '--outdir=' + str(output), '--remote-gdb-port=0',
           '--debug-flags=Fetch,FetchFault,DecoupleBP,Decode', '--debug-file=fetch-stream-debug.log',
           str(config), '--firmware', str(firmware), '--max-ticks', '1000000000',
           '--progress-ticks', '100000000', '--zicbom']
identity = {'gem5': str(binary), 'gem5Sha256': sha256(binary),
            'firmwareSha256': sha256(firmware), 'configSha256': sha256(config),
            'builderManifest': manifest, 'command': command}
(output / 'execution-plan.json').write_text(json.dumps(identity, indent=2) + '\n')
with (output / 'run.log').open('w') as log:
    child = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT)
    (output / 'host-pid.txt').write_text(str(child.pid) + '\n')
    host_code = child.wait()
(output / 'host-exit-code.txt').write_text(str(host_code) + '\n')
runlog = (output / 'run.log').read_text(errors='replace')
serial_path = output / 'system.platform.terminal'
serial = serial_path.read_text(errors='replace') if serial_path.exists() else ''
rows = [json.loads(line.removeprefix('FETCH_STREAM_RESULT ')) for line in serial.splitlines()
        if line.startswith('FETCH_STREAM_RESULT ')]
final_path = output / 'native-linux-result.json'
final = json.loads(final_path.read_text()) if final_path.exists() else {}
proper_exit = (host_code == 0 and final.get('exitCode') == 0
               and str(final.get('cause')).startswith('m5_exit instruction encountered')
               and final.get('gem5Sha256') == identity['gem5Sha256'])
trace_identity = False
trace = output / 'raw.jsonl'
if trace.exists():
    with trace.open() as stream:
        first = next((line for line in stream if line.strip()), '')
    if first:
        trace_manifest = json.loads(first)
        trace_identity = (trace_manifest.get('type') == 'manifest'
                          and trace_manifest.get('linux', {}).get('payload', {}).get('sha256')
                          == variant['sha256'])
required_checks = variant.get('requiredGuestChecks',
                              ['trainingChecked', 'mappedCodeChecked', 'trapPcChecked'])
guest_pass = (len(rows) == 1 and rows[0].get('case') == args.case
              and rows[0].get('passed') is True
              and all(rows[0].get(key) is True for key in required_checks))
assertion = ('lookupAndUpdateNextPC' in runlog
             and 'stream.startPC <= curr_pc && curr_pc < stream.predEndPC' in runlog
             and 'failed' in runlog)
result = {'kind': manifest['kind'], 'notLinuxSuite': True, 'case': args.case,
          'hostReturnCode': host_code, 'gem5Sha256': identity['gem5Sha256'],
          'firmwareSha256': variant['sha256'], 'configSha256': identity['configSha256'],
          'simulatorExit': {key: final.get(key) for key in ('tick', 'cause', 'exitCode', 'gem5Sha256')},
          'guestResults': rows, 'traceFirmwareIdentityChecked': trace_identity,
          'requiredGuestChecks': required_checks,
          'originalFetchRangeAssertion': assertion,
          'passed': proper_exit and trace_identity and guest_pass,
          'scope': 'One active hart, four KMH/native L2 on the real CHI NoC. Bare FS only. '
                   'A reproducer failure is evidence about this program, not proof of the Linux fault cause.',
          'artifacts': {path.name: {'bytes': path.stat().st_size, 'sha256': sha256(path)}
                        for path in (output / 'run.log', serial_path, output / 'fetch-stream-debug.log')
                        if path.exists()}}
(output / 'fetch-stream-result.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
raise SystemExit(0 if result['passed'] else 1)
