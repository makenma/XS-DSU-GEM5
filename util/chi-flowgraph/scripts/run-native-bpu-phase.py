"""Record an actual four-core BPU phase-storage regression run."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess

PROJECT = Path(__file__).resolve().parents[1]


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gem5', type=Path, required=True)
    parser.add_argument('--gem5-sha256', required=True)
    parser.add_argument('--gem5-source', type=Path, required=True)
    parser.add_argument('--program', type=Path, required=True)
    parser.add_argument('--program-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--trace', choices=('on', 'off'), required=True)
    args = parser.parse_args()
    binary, program, output = args.gem5.resolve(), args.program.resolve(), args.output.resolve()
    config = PROJECT / 'native/gem5/native_l2_run.py'
    if output.exists():
        raise ValueError('refusing an existing regression directory')
    if digest(binary) != args.gem5_sha256 or digest(program / 'manifest.json') != args.program_sha256:
        raise ValueError('model or program manifest differs from its expected immutable SHA')
    manifest = json.loads((program / 'manifest.json').read_text())
    rows = manifest['binaries']
    if sorted(row['hart'] for row in rows) != list(range(4)):
        raise ValueError('exactly four hart binaries required')
    for row in rows:
        if row['file'] != f"native-l2-{row['hart']}.elf" or digest(program / row['file']) != row['sha256']:
            raise ValueError('program binary differs from manifest')
    command = [str(binary), '--outdir=' + str(output), str(config), '--elf-dir', str(program),
               '--seed', '1', '--max-ticks', '2000000000', '--tail-ticks', '1000000']
    output.mkdir(parents=True)
    plan = {'schemaVersion': 1, 'createdAtUtc': datetime.now(timezone.utc).isoformat(),
            'gem5Sha256': args.gem5_sha256, 'programManifestSha256': args.program_sha256,
            'configSha256': digest(config), 'command': command, 'trace': args.trace,
            'binaries': rows, 'scope': 'Four-core host statistics regression; not Linux suite acceptance.'}
    (output / 'execution-plan.json').write_text(json.dumps(plan, indent=2) + '\n')
    environment = os.environ.copy()
    environment['CHI_GEM5_DIR'] = str(args.gem5_source.resolve())
    for name in ('CHI_FLOWGRAPH_OUT', 'CHI_FLOWGRAPH_COUNTS', 'CHI_FLOWGRAPH_CONFIG'):
        environment.pop(name, None)
    environment['CHI_FLOWGRAPH_COUNTS'] = str(output / 'counts.json')
    if args.trace == 'on':
        environment['CHI_FLOWGRAPH_OUT'] = str(output / 'raw.jsonl')
    with (output / 'run.log').open('wb') as log:
        process = subprocess.Popen(command, env=environment, stdout=log, stderr=subprocess.STDOUT)
        (output / 'host-pid.txt').write_text(str(process.pid) + '\n')
        print(json.dumps({'pid': process.pid, 'output': str(output), 'trace': args.trace}), flush=True)
        code = process.wait()
    (output / 'host-exit-code.txt').write_text(str(code) + '\n')
    print(json.dumps({'hostExitCode': code, 'output': str(output)}), flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
