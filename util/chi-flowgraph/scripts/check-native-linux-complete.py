"""Final acceptance of all three real native-CHI Linux runs and their driver exit.

Run in the recorded filesystem context (normally WSL). Existing PASS report
files are not inputs: both underlying checkers re-read execution evidence.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
DRIVER_SESSION = 30470
REQUESTS = {'ReadNotSharedDirty', 'ReadUnique', 'MakeUnique', 'CleanShared',
            'CleanInvalid', 'MakeInvalid', 'Evict', 'WriteBackFull',
            'WriteCleanFull', 'WriteEvictOrEvict', 'ReadNoSnp', 'WriteNoSnpPtl'}


def module(name):
    path = HERE / name
    spec = importlib.util.spec_from_file_location('complete_' + path.stem.replace('-', '_'), path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def unique_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key: ' + key)
            result[key] = value
        return result
    def bad_constant(value):
        raise ValueError('non-JSON number: ' + value)
    return json.loads(text, object_pairs_hook=pairs, parse_constant=bad_constant)


def check(core, kernel_build, random_memory, driver_completion):
    core, kernel_build, random_memory = (Path(value).resolve()
                                        for value in (core, kernel_build, random_memory))
    failures, incomplete, inputs, checks = [], [], {}, {}

    def require(condition, message, pending=False):
        if not condition:
            (incomplete if pending else failures).append(message)
        return bool(condition)

    def read(path):
        path = Path(path).resolve()
        try:
            before = path.stat()
            raw = path.read_bytes()
            after = path.stat()
        except OSError as error:
            inputs[str(path)] = {'exists': path.exists()}
            incomplete.append('missing/unreadable input: ' + str(path) + ': ' + str(error))
            return None
        stable = before.st_size == after.st_size == len(raw) and before.st_mtime_ns == after.st_mtime_ns
        inputs[str(path)] = {'exists': True, 'bytes': len(raw),
                             'sha256': hashlib.sha256(raw).hexdigest(), 'stableDuringRead': stable}
        if not require(stable, 'input changed during read: ' + str(path), pending=True):
            return None
        return raw.decode('utf-8', errors='replace')

    def recheck(name, arguments, label):
        read(HERE / name)
        try:
            report = module(name).check(*arguments)
        except Exception as error:
            # A malformed input or checker exception must not erase a known
            # execution failure or turn prepared files into completion.
            failures.append(label + ': checker raised ' + type(error).__name__ + ': ' + str(error))
            return {'passed': False, 'status': 'FAIL', 'knownFailure': True, 'error': str(error)}
        if not isinstance(report, dict):
            failures.append(label + ': checker returned no object')
            return {'passed': False, 'status': 'FAIL', 'knownFailure': True}
        pending = report.get('status') in ('INCOMPLETE', 'NOT_RUN', 'TIMEOUT', 'UNSUPPORTED') or bool(report.get('incomplete'))
        if pending:
            incomplete.append(label + ': execution has not completed all acceptance checks')
        problem = report.get('knownFailure', False) is not False or any(report.get(key) for key in
                    ('failures', 'issues', 'errors', 'error'))
        require(not problem, label + ': checker reports a known failure')
        require(report.get('status') == 'PASS' and report.get('passed') is True and
                report.get('pass', True) is True and not report.get('incomplete') and not problem,
                label + ': actual complete PASS required', pending=pending)
        return report

    read(Path(__file__))
    require(len({core, kernel_build, random_memory}) == 3, 'three distinct actual Linux run directories required')
    checks['coreAndKernel'] = recheck('check-native-linux-validation.py', (core, kernel_build), 'core/kernel')
    checks['randomMemory'] = recheck('check-native-linux-random-memory.py', (random_memory,), 'random-memory')
    base, random = checks['coreAndKernel'], checks['randomMemory']

    # Coverage must already be established by the original actual Linux runs;
    # random migration or old SE traffic cannot fill an absent opcode here.
    coverage = base.get('requestCoverage')
    coverage_ok = isinstance(coverage, dict) and coverage.get('complete') is True and coverage.get('missing') == []
    counts = coverage.get('counts') if isinstance(coverage, dict) else None
    coverage_ok = coverage_ok and isinstance(counts, dict) and set(counts) == REQUESTS
    if coverage_ok:
        coverage_ok = all(type(counts[name]) is str and bool(re.fullmatch(r'[1-9][0-9]*', counts[name]))
                          for name in REQUESTS)
    require(coverage_ok, 'base actual Linux runs have not established all 12 RN-F TX REQ opcodes',
            pending=base.get('status') != 'PASS')

    identities = []
    runs = base.get('runs')
    if not isinstance(runs, dict):
        runs = {}
    for label, directory in (('core', core), ('kernel-build', kernel_build)):
        row = runs.get(label)
        row = row if isinstance(row, dict) else {}
        result = row.get('result')
        result = result if isinstance(result, dict) else {}
        identities.append(result.get('gem5Sha256'))
        if base.get('status') == 'PASS':
            require(row.get('passed') is True, label + ': base per-run check did not pass')
            require(row.get('directory') == str(directory), label + ': rechecked directory differs')
    identities.append(random.get('gem5Sha256'))
    complete_identity = all(type(value) is str and bool(re.fullmatch('[0-9a-f]{64}', value)) for value in identities)
    require(complete_identity, 'actual model identity is missing/malformed',
            pending=base.get('status') != 'PASS' or random.get('status') != 'PASS')
    if complete_identity:
        require(len(set(identities)) == 1, 'core, kernel build and random-memory used different gem5 binaries')
    if random.get('status') == 'PASS':
        require(random.get('directory') == str(random_memory), 'random-memory rechecked directory differs')

    exit_text = read(core / 'host-exit-code.txt')
    core_host_ok = exit_text is not None and exit_text.strip() == '0'
    if exit_text is not None:
        require(core_host_ok, 'core gem5 host process did not record exit code zero')
    checks['coreHostExit'] = {'passed': core_host_ok, 'path': str(core / 'host-exit-code.txt')}

    driver_text = read(driver_completion)
    driver, driver_ok = None, False
    if driver_text is not None:
        before_fail, before_pending = len(failures), len(incomplete)
        try:
            driver = unique_json(driver_text)
            if not isinstance(driver, dict):
                raise ValueError('driver completion must be an object')
            require(driver.get('source') == 'exec-session-completion', 'driver record is not an actual exec-session completion')
            require(type(driver.get('sessionId')) is int and driver.get('sessionId') == DRIVER_SESSION,
                    'driver record does not identify original exec session 30470')
            for key, expected in (('core', core), ('kernelBuild', kernel_build)):
                value = driver.get(key)
                path_ok = isinstance(value, str) and Path(value).is_absolute() and Path(value).resolve() == expected
                require(path_ok, 'driver completion ' + key + ' path differs from this acceptance run')
            observed = driver.get('observedAtUtc')
            if not isinstance(observed, str):
                raise ValueError('driver observedAtUtc is missing')
            timestamp = datetime.fromisoformat(observed.replace('Z', '+00:00'))
            require(timestamp.utcoffset() is not None and timestamp.utcoffset().total_seconds() == 0,
                    'driver observation timestamp is not UTC')
            execution = driver.get('execResult')
            if not isinstance(execution, dict):
                raise ValueError('driver execResult must contain the actual tool response object')
            code = execution.get('exit_code')
            running = execution.get('session_id') is not None
            if code is None:
                incomplete.append('original exec driver has no terminal exit_code')
            else:
                require(type(code) is int and code == 0, 'original exec driver exited nonzero or has invalid exit_code')
            require(not running, 'original exec driver still has a running session_id', pending=True)
            require(execution.get('isError') is not True and not execution.get('error'),
                    'original exec driver tool reported an error')
            driver_ok = len(failures) == before_fail and len(incomplete) == before_pending
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            failures.append('invalid driver completion: ' + str(error))
    checks['driverCompletion'] = {'passed': driver_ok, 'path': str(Path(driver_completion).resolve()),
                                  'record': driver}
    status = 'INCOMPLETE' if incomplete else 'FAIL' if failures else 'PASS'
    return {'schemaVersion': 1, 'status': status, 'passed': status == 'PASS', 'knownFailure': bool(failures),
            'checkedAtUtc': datetime.now(timezone.utc).isoformat(),
            'directories': {'core': str(core), 'kernelBuild': str(kernel_build), 'randomMemory': str(random_memory)},
            'gem5Sha256': identities[0] if complete_identity and len(set(identities)) == 1 else None,
            'checks': checks, 'inputs': inputs, 'failures': failures, 'incomplete': incomplete,
            'scope': 'Actual core Linux suite, guest kernel make -j, independent randomized concurrent '
                     'migration, 12-request coverage and terminal outer-driver completion are all required. '
                     'Finite execution evidence, not RTL equivalence or exhaustive CHI/RVWMO correctness.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('core', 'kernel-build', 'random-memory', 'driver-completion', 'output'):
        parser.add_argument('--' + flag, type=Path, required=True)
    args = parser.parse_args()
    report = check(args.core, args.kernel_build, args.random_memory, args.driver_completion)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'knownFailure': report['knownFailure'],
                      'failures': report['failures'], 'incomplete': report['incomplete']}, indent=2))
    raise SystemExit(0 if report['passed'] else 2 if report['status'] == 'INCOMPLETE' else 1)
