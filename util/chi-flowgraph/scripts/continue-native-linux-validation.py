"""Continue an already running core suite with the real guest kernel build.

This local foreground supervisor only launches the kernel guest after every
core stage has completed and passed its independent acceptance checks. It does
not retry, truncate, replace, or count incomplete runs as successful.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def load_checker(path):
    spec = importlib.util.spec_from_file_location('linux_acceptance', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--gem5-source', type=Path, required=True)
    parser.add_argument('--gem5', type=Path, required=True)
    parser.add_argument('--gem5-sha256', required=True)
    parser.add_argument('--firmware', type=Path, required=True)
    parser.add_argument('--firmware-sha256', required=True)
    parser.add_argument('--core', type=Path, required=True)
    parser.add_argument('--core-pid', type=int, required=True)
    parser.add_argument('--kernel-build', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--max-ticks', type=int, default=10_000_000_000_000_000)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    if args.max_ticks <= 0:
        parser.error('positive kernel-build tick budget required')
    for key in ('project', 'gem5_source', 'gem5', 'firmware', 'core', 'kernel_build', 'state', 'output'):
        setattr(args, key, getattr(args, key).resolve())
    if args.core == args.kernel_build:
        parser.error('runs must use distinct directories')
    if args.kernel_build.exists():
        parser.error('refusing to overwrite an existing kernel guest run')
    config = args.project / 'native/gem5/native_l2_fs.py'
    checker_file = args.project / 'scripts/check-native-linux-validation.py'
    # Pin inputs before waiting; any subsequent edit causes a refusal to launch.
    expected = {args.gem5: args.gem5_sha256, args.firmware: args.firmware_sha256,
                config: digest(config), checker_file: digest(checker_file),
                Path(__file__).resolve(): digest(Path(__file__).resolve())}
    checker = load_checker(checker_file)
    for name in ('check-chi-transfer-census.py', 'check-linux-kernel-build.py',
                 'check-linux-litmus.py', 'check-linux-suite.py', 'check-linux-cbo.py',
                 'check-linux-uncached.py', 'check-kernel-torture.py'):
        path = args.project / 'scripts' / name
        expected[path] = digest(path)

    def verify_inputs():
        for path, sha in expected.items():
            if digest(path) != sha:
                raise ValueError('input changed or wrong SHA256: ' + str(path))

    verify_inputs()
    process_identity = Path('/proc') / str(args.core_pid) / 'stat'
    result_path = args.core / 'native-linux-result.json'
    if result_path.exists():
        raise ValueError('this continuation requires a live unfinished core; refusing a preexisting result')
    command_line = (process_identity.parent / 'cmdline').read_bytes().split(b'\0')
    if ('--outdir=' + str(args.core)).encode() not in command_line:
        raise ValueError('core PID does not run the expected output directory')
    # Linux process starttime prevents PID reuse from masquerading as the run.
    core_starttime = process_identity.read_text().rsplit(')', 1)[1].split()[19]
    started_ns = time.time_ns()
    args.state.parent.mkdir(parents=True, exist_ok=True)
    lock = args.state.with_suffix('.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    command = [str(args.gem5), '--outdir=' + str(args.kernel_build), str(config),
               '--kernel', str(args.firmware), '--mem-size', '4GiB', '--numa-nodes', '2',
               '--dtb-addr', '0x17fe00000', '--zicbom', '--max-ticks', str(args.max_ticks)]
    state = {'schemaVersion': 1, 'pid': os.getpid(), 'core': str(args.core),
             'corePid': args.core_pid, 'coreProcessStarttime': core_starttime,
             'kernelBuild': str(args.kernel_build), 'command': command,
             'pinnedSha256': {str(path): sha for path, sha in expected.items()},
             'guestExecuted': False, 'scope': 'Local continuation of authorized real Linux validation'}

    def update(status, **fields):
        state.update(status=status, updatedAtUtc=datetime.now(timezone.utc).isoformat(), **fields)
        temporary = args.state.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, indent=2) + '\n')
        temporary.replace(args.state)
        print(json.dumps({'status': status, **fields}), flush=True)

    if args.check_only:
        update('prerequisites-verified', kernelGuestStarted=False)
        return 0
    update('waiting-for-core', kernelGuestStarted=False)
    # This is an existing local test, not a scheduler or an alternate CPU model.
    while True:
        try:
            fields = process_identity.read_text().rsplit(')', 1)[1].split()
            current = fields[19] if fields[0] != 'Z' else None
        except FileNotFoundError:
            current = None
        if current != core_starttime:
            if not result_path.is_file() or result_path.stat().st_mtime_ns < started_ns:
                update('core-exited-without-new-result', passed=False)
                return 2
            break
        time.sleep(15)
    # The exit-time census can be flushed just after the Python result file.
    for attempt in range(5):
        count_path = args.core / 'native-chi-counts.json'
        if count_path.is_file() and json.loads(count_path.read_text()).get('snapshotKind') == 'process-exit':
            break
        time.sleep(2)
    audit = checker.Audit()
    census = audit.module('check-chi-transfer-census.py')
    kernel = audit.module('check-linux-kernel-build.py')
    core = checker.run_audit(audit, args.core, 'core', census, kernel)
    if not core['passed']:
        update('core-not-passed', failures=audit.failures, incomplete=audit.incomplete)
        return 2
    if core['result']['gem5Sha256'] != args.gem5_sha256:
        update('input-check-failed', error='completed core used a different model', passed=False)
        return 2
    try:
        verify_inputs()
    except ValueError as error:
        update('input-check-failed', error=str(error), passed=False)
        return 2
    args.kernel_build.mkdir()  # Refuse an existing directory even after the wait.
    (args.kernel_build / 'execution-plan.json').write_text(json.dumps(state, indent=2) + '\n')
    environment = os.environ.copy()
    environment['CHI_GEM5_DIR'] = str(args.gem5_source)
    environment.pop('CHI_FLOWGRAPH_OUT', None)
    environment.pop('CHI_FLOWGRAPH_COUNTS', None)
    with (args.kernel_build / 'run.log').open('w') as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=environment)
        update('running-kernel-guest', guestExecuted=True, kernelGuestStarted=True, guestPid=process.pid)
        exit_code = process.wait()
    # The acceptance checker re-parses real serial; a successful process alone
    # is insufficient, as is a guest SHA string without a completed build.
    try:
        verify_inputs()
    except ValueError as error:
        update('input-check-failed', error=str(error), processExitCode=exit_code, passed=False)
        return 2
    report = checker.check(args.core, args.kernel_build)
    if exit_code != 0:
        report['passed'] = False
        report['status'] = 'FAIL'
        report['knownFailure'] = True
        report['failures'].append('kernel guest process exited nonzero: ' + str(exit_code))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    update('validation-' + report['status'].lower(), processExitCode=exit_code,
           acceptanceReport=str(args.output), passed=report['passed'])
    return 0 if exit_code == 0 and report['passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
