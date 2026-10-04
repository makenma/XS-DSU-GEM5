"""Build one frozen model10 candidate inside the D-backed WSL filesystem."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--fetch-cc-sha256', required=True)
parser.add_argument('--fetch-hh-sha256', required=True)
parser.add_argument('--candidate', type=int, choices=range(1, 100), default=1)
args = parser.parse_args()
project = Path(__file__).resolve().parents[1]
work = Path('/root/chi-flowgraph-work/linux-validation')
source = Path('/root/chi-flowgraph-work/gem5')
output = work / ('artifacts/fs-model-10-fetch-demand-fault-candidate%02d' % args.candidate)
parent = work / 'artifacts/fs-model-09-fetch-boundary-candidate01/gem5.opt'
parent_sha = '0e45dfb345200506be4940e0a940a0ada4bc82905f1f1ae22059f3a96f044597'
expected = {'src/cpu/o3/fetch.cc': args.fetch_cc_sha256,
            'src/cpu/o3/fetch.hh': args.fetch_hh_sha256}
for name, sha in expected.items():
    if digest(source / name) != sha:
        raise ValueError('Source differs from explicit frozen SHA: ' + name)
if digest(parent) != parent_sha:
    raise ValueError('Immutable parent model09 changed')
subprocess.run([sys.executable, str(project / 'scripts/apply-bpu-predecode-boundary.py'),
                '--source', str(source), '--check'], check=True, stdout=subprocess.DEVNULL)
output.mkdir(parents=True, exist_ok=False)
subprocess.run([sys.executable, str(project / 'build/linux-validation/capture-fetch-candidate-source.py'),
                str(source), str(output)], check=True)
(output / 'parent-model.json').write_text(json.dumps({
    'path': str(parent), 'sha256': parent_sha, 'frozenFetchSources': expected,
    'linuxAcceptance': False,
}, indent=2) + '\n')
swap = work / 'model10-build-link-swap'
if swap.exists() or swap.is_symlink():
    raise ValueError('Refusing existing temporary swap')
subprocess.run(['fallocate', '-l', '4G', str(swap)], check=True)
swap.chmod(0o600)
with (output / 'swap-setup.log').open('w') as log:
    subprocess.run(['mkswap', '-L', 'chi-link-10', str(swap)], check=True, stdout=log)
subprocess.run(['swapon', '-p', '-3', str(swap)], check=True)
(output / 'swaps-before-build.txt').write_text(Path('/proc/swaps').read_text())
temporary = work / 'tmp/model10-build'
temporary.mkdir(parents=True, exist_ok=True)
environment = dict(os.environ, TMPDIR=str(temporary))
command = ['scons', 'build/RISCV/gem5.opt', '-j1', '--linker=gold',
           'LINKFLAGS_EXTRA=-Wl,--no-keep-files-mapped,--no-map-whole-files']
(output / 'build-command.json').write_text(json.dumps({'command': command, 'cwd': str(source),
                                                     'TMPDIR': str(temporary)}, indent=2) + '\n')
with (output / 'build.log').open('w') as log:
    process = subprocess.Popen(command, cwd=source, env=environment, stdout=log, stderr=subprocess.STDOUT)
    (output / 'build-pid.txt').write_text(str(process.pid) + '\n')
    print(json.dumps({'buildPid': process.pid, 'output': str(output)}), flush=True)
    code = process.wait()
(output / 'build-exit-code.txt').write_text(str(code) + '\n')
if code:
    raise SystemExit(code if code > 0 else 1)
for name, sha in expected.items():
    if digest(source / name) != sha:
        raise ValueError('Source changed during build; refusing candidate publication: ' + name)
shutil.copy2(source / 'build/RISCV/gem5.opt', output / 'gem5.opt')
sha = digest(output / 'gem5.opt')
(output / 'gem5.sha256').write_text(sha + '  gem5.opt\n')
print(json.dumps({'built': True, 'gem5Sha256': sha, 'output': str(output),
                  'linuxAcceptance': False}), flush=True)
