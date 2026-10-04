"""Run the isolated random-memory supplement on the same native-CHI model."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess

PROJECT = Path(__file__).resolve().parents[1]


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-result', type=Path, required=True)
    parser.add_argument('--gem5', type=Path, required=True)
    parser.add_argument('--gem5-sha256', required=True)
    parser.add_argument('--gem5-source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build = json.loads(args.build_result.read_text())
    if build.get('built') is not True or build.get('guestExecuted') is not False:
        raise ValueError('expected a successful build-only firmware report')
    firmware = Path(build['firmware']).resolve()
    model = args.gem5.resolve()
    config = PROJECT / 'native/gem5/native_l2_fs.py'
    if digest(firmware) != build['firmwareSha256'] or digest(model) != args.gem5_sha256:
        raise ValueError('firmware/model SHA mismatch')
    index_path = Path(build['inputIndex']).resolve()
    index = json.loads(index_path.read_text())
    identity = {key: index[key] for key in ('schemaVersion', 'parameters', 'coreInputSha256', 'files')}
    identity_sha = hashlib.sha256((json.dumps(identity, sort_keys=True, indent=2) + '\n').encode()).hexdigest()
    if identity_sha != index['inputSha256'] or identity_sha != build['inputSha256']:
        raise ValueError('frozen input identity differs')
    for entry in index['files']:
        path = (index_path.parent / entry['path']).resolve()
        if not path.is_relative_to(index_path.parent) or digest(path) != entry['sha256']:
            raise ValueError('frozen input changed: ' + str(path))
    output = args.output.resolve()
    if output.exists():
        raise ValueError('refusing an existing run directory')
    output.mkdir(parents=True)
    command = [str(model), '--outdir=' + str(output), str(config), '--kernel', str(firmware),
               '--zicbom', '--uncached-test-ram', '--max-ticks', '130000000000000']
    plan = {'schemaVersion': 1, 'kind': 'native-linux-random-memory',
            'preparedAtUtc': datetime.now(timezone.utc).isoformat(),
            'gem5': str(model), 'gem5Sha256': args.gem5_sha256,
            'firmware': str(firmware), 'firmwareSha256': build['firmwareSha256'],
            'config': str(config), 'configSha256': digest(config),
            'inputIndex': str(index_path), 'inputSha256': identity_sha,
            'buildResult': str(args.build_result.resolve()), 'buildResultSha256': digest(args.build_result),
            'command': command}
    (output / 'execution-plan.json').write_text(json.dumps(plan, indent=2) + '\n')
    environment = os.environ.copy()
    environment['CHI_GEM5_DIR'] = str(args.gem5_source.resolve())
    for key in ('CHI_FLOWGRAPH_OUT', 'CHI_FLOWGRAPH_COUNTS', 'CHI_FLOWGRAPH_CONFIG'):
        environment.pop(key, None)
    with (output / 'run.log').open('wb') as log:
        process = subprocess.Popen(command, env=environment, stdout=log, stderr=subprocess.STDOUT)
        (output / 'host-pid.txt').write_text(str(process.pid) + '\n')
        print(json.dumps({'pid': process.pid, 'output': str(output), 'gem5Sha256': args.gem5_sha256}), flush=True)
        code = process.wait()
    (output / 'host-exit-code.txt').write_text(str(code) + '\n')
    checker_path = PROJECT / 'scripts/check-native-linux-random-memory.py'
    spec = importlib.util.spec_from_file_location('random_memory_acceptance', checker_path)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    report = checker.check(output)
    if code != 0:
        report.update(passed=False, knownFailure=True)
    (output / 'supplement-result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'hostExitCode': code, 'status': report['status'], 'passed': report['passed']}), flush=True)
    return 0 if code == 0 and report['passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
