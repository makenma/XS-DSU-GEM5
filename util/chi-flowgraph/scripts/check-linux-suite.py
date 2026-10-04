"""Validate independent Linux workload results; missing/unsupported/timeout never means PASS."""
import argparse
from collections import Counter
import json
from pathlib import Path


TESTS = ['spinlock', 'mutex', 'futex', 'amo', 'lrsc', 'release_acquire', 'false_sharing',
         'atomic_counter', 'memcpy_checksum', 'alias_cache_pressure', 'page_migration']


def check(log, manifest, iterations, selected=None):
    selected = TESTS if selected is None else selected
    issues, incomplete = [], []
    def require(condition, detail):
        if not condition:
            issues.append(detail)
    def integer(value, minimum=0):
        return type(value) is int and value >= minimum
    records = {'BEGIN': [], 'RESULT': [], 'END': [], 'ABORT': []}
    prefixes = {'LINUX_SUITE_BEGIN ': 'BEGIN', 'LINUX_TEST_RESULT ': 'RESULT',
                'LINUX_SUITE_END ': 'END', 'LINUX_SUITE_ABORT ': 'ABORT'}
    sequence = []
    for line in log.splitlines():
        for prefix, kind in prefixes.items():
            if line.startswith(prefix):
                try:
                    record = json.loads(line[len(prefix):])
                    require(isinstance(record, dict), f'{kind} must be a JSON object')
                    if isinstance(record, dict):
                        records[kind].append(record)
                        sequence.append(kind)
                except (ValueError, TypeError):
                    issues.append(f'malformed {kind} JSON')
                break
    require(integer(iterations, 1), 'expected iteration count must be positive')
    require(len(selected) == len(set(selected)) and bool(selected) and set(selected) <= set(TESTS),
            'unknown, duplicate, or empty requested test selection')
    require(manifest.get('suite') == 'native-linux-pthreads' and manifest.get('threads') == 4,
            'wrong build manifest suite/thread count')
    require(manifest.get('tests') == TESTS, 'build manifest has incomplete test inventory')
    require(all(manifest.get('assemblyChecks', {}).get(name) is True for name in
                ('rv64Elf', 'explicitAmoAqRl', 'explicitLrAcquire', 'explicitScRelease', 'linuxEcall')),
            'missing successful RV64 instruction verification')
    require(len(records['BEGIN']) == 1, 'expected exactly one suite BEGIN')
    require(len(records['END']) == 1, 'expected exactly one suite END; truncated runs cannot pass')
    require(not records['ABORT'], 'suite aborted or timed out')
    require(sequence == ['BEGIN'] + ['RESULT'] * len(selected) + ['END'], 'missing/extra/reordered suite records')
    begin = records['BEGIN'][0] if records['BEGIN'] else {}
    end = records['END'][0] if records['END'] else {}
    cpus = begin.get('affinityCpus', [])
    require(begin.get('schemaVersion') == 1 and begin.get('suite') == 'native-linux-pthreads', 'wrong runtime suite identity')
    require(begin.get('buildId') == manifest.get('sourceSha256') and
            isinstance(begin.get('buildId'), str) and len(begin['buildId']) == 64, 'runtime build identity differs from compiled source')
    require(begin.get('threadsRequested') == 4 and integer(begin.get('onlineCpus'), 4), 'fewer than four real Linux CPUs')
    require(len(cpus) == 4 and all(integer(cpu) for cpu in cpus) and len(set(cpus)) == 4,
            'four distinct CPU affinities required')
    require(begin.get('iterations') == iterations and integer(begin.get('iterations'), 1), 'runtime iteration count mismatch')
    require(begin.get('selectedTests') == selected, 'runtime test selection mismatch')
    names = [item.get('test') for item in records['RESULT']]
    require(names == selected, 'test records must each appear exactly once in requested order')
    statuses = Counter()
    for result in records['RESULT']:
        name = result.get('test')
        label = f'{name}: '
        status = result.get('status')
        statuses[status] += 1
        require(status in ('PASS', 'FAIL', 'UNSUPPORTED'), label + 'unknown status')
        require(result.get('threadsRequested') == result.get('threadsStarted') == 4, label + 'missing worker threads')
        require(result.get('threadCpuIds') == cpus, label + 'actual thread CPUs differ from assigned distinct CPUs')
        require(result.get('threadPinErrors') == [0, 0, 0, 0], label + 'CPU pinning failed')
        require(integer(result.get('iterations'), 1) and result.get('iterations') == iterations,
                label + 'wrong iteration count')
        expected = 4 * iterations
        require(result.get('operationsExpected') == expected, label + 'wrong expected operation count')
        operations = result.get('threadOperations', [])
        errors = result.get('threadErrors', [])
        require(len(operations) == 4 and all(integer(value) for value in operations), label + 'invalid per-thread operation counts')
        require(len(errors) == 4 and all(integer(value) for value in errors), label + 'invalid per-thread error counts')
        require(integer(result.get('errors')) and result.get('errors', -1) >= sum(v for v in errors if integer(v)),
                label + 'invalid or understated error count')
        require(integer(result.get('operations')) and result.get('operations') == sum(v for v in operations if integer(v)),
                label + 'operation total differs from actual workers')
        metrics = result.get('metrics', {})
        if status == 'UNSUPPORTED':
            incomplete.append({'test': name, 'reason': metrics.get('migrationReason', 'capability unavailable')})
            require(name == 'page_migration', label + 'unexpected unsupported test')
            continue
        require(status == 'PASS', label + 'test failed')
        require(result.get('errors') == 0 and errors == [0, 0, 0, 0], label + 'nonzero errors')
        require(operations == [iterations] * 4 and result.get('operations') == expected and
                result.get('observed') == expected, label + 'incomplete actual operations/result')
        if name == 'futex':
            require(integer(metrics.get('futexSlept'), 1) and integer(metrics.get('futexWaitCalls'), 1) and
                    metrics.get('futexWaitCalls', 0) >= metrics.get('futexSlept', 0) and
                    metrics.get('futexWakeCalls') == expected, label + 'no actual futex wait/wake coverage')
        if name == 'lrsc':
            require(integer(metrics.get('scFailures')), label + 'invalid SC retry count')
        if name == 'memcpy_checksum':
            size = metrics.get('copyBytesPerOperation')
            require(integer(size, 64) and size <= 1048576, label + 'invalid memcpy length')
            if integer(size, 64) and size <= 1048576:
                total = sum((owner * 37 + offset * 17 + (offset >> 8)) & 255
                            for owner in range(4) for offset in range(size)) * iterations
                require(metrics.get('checksum') == total, label + 'checksum does not match copied source bytes')
        if name == 'alias_cache_pressure':
            require(metrics.get('aliasMappings') == 2 and integer(metrics.get('pressureBytesPerThread'), 65536),
                    label + 'missing two aliases or cache pressure working set')
        if name == 'page_migration':
            require(integer(metrics.get('numaMemoryNodes'), 2), label + 'single NUMA node cannot prove migration')
            require(metrics.get('migrationPagesAttempted') == metrics.get('migrationPagesMoved') == expected,
                    label + 'page placement did not actually change on every migration')
            require(metrics.get('migrationErrno') == 0 and metrics.get('migrationReason') == 'none',
                    label + 'migration system call failed or was unsupported')
    require(end.get('testsCompleted') == len(selected), 'footer completed count mismatch')
    for status, field in (('PASS', 'passed'), ('FAIL', 'failed'), ('UNSUPPORTED', 'unsupported')):
        require(end.get(field) == statuses[status], f'footer {field} count mismatch')
    expected_end = 'FAIL' if statuses['FAIL'] else 'INCOMPLETE' if statuses['UNSUPPORTED'] else 'PASS'
    expected_exit = 1 if statuses['FAIL'] else 2 if statuses['UNSUPPORTED'] else 0
    require(end.get('status') == expected_end and end.get('exitCode') == expected_exit, 'footer status/exit mismatch')
    return {'pass': not issues and not incomplete, 'status': 'FAIL' if issues else 'INCOMPLETE' if incomplete else 'PASS',
            'fullSuiteRequested': selected == TESTS, 'requiredTests': selected,
            'recordedTests': names, 'testStatuses': dict(statuses), 'iterations': iterations,
            'issues': issues, 'incomplete': incomplete, 'results': records['RESULT'],
            'scope': 'Real Linux workload assertions and observed completion counts. '
                     'Does not replace diy7/litmus7 generated tests or establish all-interleaving correctness.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--iterations', type=int, required=True)
    parser.add_argument('--tests', default='all')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = check(args.input.read_text(encoding='utf-8', errors='replace'),
                   json.loads(args.manifest.read_text(encoding='utf-8')), args.iterations,
                   None if args.tests == 'all' else args.tests.split(','))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key != 'results'}, indent=2))
    raise SystemExit(0 if report['pass'] else 2 if report['status'] == 'INCOMPLETE' else 1)
