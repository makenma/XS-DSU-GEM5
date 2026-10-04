"""Validate bounded, sequential Linux 6.6.52 torture module evidence.

SUCCESS is insufficient: enforce real nonzero GP/reader/reclamation/barrier
work for RCU and positive per-thread acquisitions for each configured lock.
This is finite runtime validation, not a memory-model or all-interleavings proof.
"""
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import shlex

LOCK_TYPES = ('spin_lock', 'rw_lock', 'mutex_lock', 'rwsem_lock')


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def fields(line):
    words = shlex.split(line)
    result = {}
    for word in words[1:]:
        require('=' in word, 'malformed evidence field')
        key, value = word.split('=', 1)
        require(key not in result, f'duplicate evidence field {key}')
        result[key] = value
    return result


def exactly(lines, prefix):
    matches = [line for line in lines if line.startswith(prefix + ' ')]
    require(len(matches) == 1, f'expected one {prefix}')
    return matches[0]


def last_match(pattern, body, reason):
    matches = list(re.finditer(pattern, body, re.M))
    require(matches, reason)
    return matches[-1]


def bounded(lines, begin, end):
    require(lines.count(begin) == 1 and lines.count(end) == 1, 'missing or duplicate evidence boundary')
    left, right = lines.index(begin), lines.index(end)
    require(left < right, 'reversed evidence boundaries')
    return lines[left + 1:right], left, right


def rcu_work(body, minimum):
    gp = last_match(r'^rcu:\s+End-test grace-period state:.*\btotal-gps=(\d+)\s*$', body,
                    'missing RCU completed grace-period count')
    row = last_match(r'^rcu-torture: rtc: .*\b(?:ver|VER): (\d+) .*\brta: (\d+) rtaf: (\d+) rtf: (\d+) '
                     r'rtmbe: (\d+) rtmbkf: (\d+)/(\d+) rtbe: (\d+) rtbke: (\d+) '
                     r'rtbf: (\d+).*\bbarrier: (\d+)/(\d+):(\d+) ', body, 'missing final RCU work/error counters')
    values = list(map(int, row.groups()))
    version, alloc, alloc_fail, freed, mbe, mb_fail, mb_tries, be, bke, bf, barriers, attempts, berr = values
    require(int(gp[1]) >= 1 and version >= 1 and alloc >= minimum and freed >= 1,
            'RCU made insufficient grace-period/allocation/reclamation progress')
    require(not any((mbe, mb_fail, be, bke, bf, berr)), 'RCU reported memory-order/barrier/boost errors')
    require(mb_tries >= 1 and barriers >= 1 and attempts >= barriers, 'RCU barriers/checks did no real work')
    pipe = last_match(r'^rcu-torture: Reader Pipe:\s+([0-9 ]+)\s*$', body, 'missing RCU reader counters')
    reads = list(map(int, pipe[1].split()))
    require(len(reads) >= 3 and sum(reads[:2]) >= 1, 'RCU readers did no real work')
    require(not any(reads[2:]), 'RCU reader observed a prematurely reclaimed pipeline stage')
    return {'gracePeriods': int(gp[1]), 'version': version, 'allocations': alloc,
            'allocationFailures': alloc_fail, 'freed': freed, 'readerPipe': reads,
            'memoryOrderChecks': mb_tries, 'barrierSuccesses': barriers, 'barrierAttempts': attempts}


def lock_work(body, kind, writers, readers, minimum):
    result = {}
    for label, count in [('Writes', writers), ('Reads', readers)]:
        if not count:
            continue
        row = last_match(r'^' + label + r'\s*:\s+Total: (\d+)\s+Max/Min: (\d+)/(\d+)\s*(?:\?\?\?\s*)?Fail: (\d+)(?:\s.*)?$',
                         body, f'{kind}: missing final {label} counters')
        total, maximum, least, fail = map(int, row.groups())
        require(fail == 0, f'{kind}: {label} exclusion failed')
        require(total >= minimum and least >= 1 and maximum >= least,
                f'{kind}: {label} made insufficient or zero-thread progress')
        require(least * count <= total <= maximum * count, f'{kind}: inconsistent {label} thread totals')
        result[label.lower()] = {'total': total, 'maximum': maximum, 'minimum': least, 'threads': count}
    return result


def check(text, lock_types=LOCK_TYPES, min_seconds=Decimal(1), min_rcu_allocations=1, min_lock_acquires=100):
    require(min_seconds > 0 and min_rcu_allocations > 0 and min_lock_acquires > 0, 'minimum work thresholds must be positive')
    require(lock_types and len(set(lock_types)) == len(lock_types) and set(lock_types) <= set(LOCK_TYPES), 'invalid expected lock profiles')
    lines = text.replace('\r', '').splitlines()
    require(not any(line.startswith('CHI_KERNEL_TORTURE_FAIL ') for line in lines), 'guest torture runner failed')
    start = exactly(lines, 'CHI_KERNEL_TORTURE_RUN_BEGIN')
    finish = exactly(lines, 'CHI_KERNEL_TORTURE_RUN_END')
    begin, end = fields(start), fields(finish)
    require(begin.get('id') == end.get('id') and end.get('status') == 'completed', 'run completion identity mismatch')
    require(begin.get('kernel', '').startswith('6.6.52-chi-native'), 'unexpected kernel version')
    require(begin.get('cpus') == '0-3' and begin.get('nodes') == '0-1', 'four online harts and two NUMA nodes required')
    run, _, _ = bounded(lines, start, finish)
    run_id = begin['id']
    threads = int(begin['threads'])
    require(2 <= threads <= 16, 'insufficient/invalid test threads')
    expected = ['rcu', *lock_types]
    case_begins = [line for line in run if line.startswith('CHI_KERNEL_TORTURE_CASE_BEGIN ')]
    require([fields(line).get('test') for line in case_begins] == expected, 'missing, duplicate, or reordered test cases')
    require(len([line for line in run if line.startswith('CHI_KERNEL_TORTURE_CASE_END ')]) == len(expected), 'missing or extra test end')
    report, previous_end = [], -1
    for kind, start_line in zip(expected, case_begins):
        details = fields(start_line)
        identity = f'id={run_id} test={kind}'
        end_line = exactly(run, 'CHI_KERNEL_TORTURE_CASE_END ' + identity)
        stop = fields(end_line)
        case, left, right = bounded(run, start_line, end_line)
        require(left > previous_end, 'test modules ran concurrently or overlapped')
        previous_end = right
        duration = Decimal(details['seconds'])
        elapsed = Decimal(stop['uptime']) - Decimal(details['uptime'])
        require(details.get('id') == run_id and stop.get('id') == run_id, 'case identity mismatch')
        require(stop.get('seconds') == details['seconds'] and duration >= min_seconds,
                'zero or insufficient requested test duration')
        require(details['seconds'] == begin['rcu_seconds' if kind == 'rcu' else 'lock_seconds'], 'case duration disagrees with run configuration')
        require(elapsed >= duration - Decimal('0.01'), 'guest elapsed time shorter than requested test')
        require(stop.get('insmod_rc') == '0' and stop.get('rmmod_rc') == '0', 'module load/unload failed')
        parameters = fields(exactly(case, 'CHI_KERNEL_TORTURE_PARAMETERS ' + identity))
        require(parameters.get('torture_type') == kind, 'wrong module torture_type')
        for parameter in ('onoff_interval', 'shutdown_secs', 'stutter', 'shuffle_interval'):
            require(parameters.get(parameter) == '0', f'unexpected {parameter} profile')
        log, _, _ = bounded(case, 'CHI_KERNEL_TORTURE_LOG_BEGIN ' + identity,
                            'CHI_KERNEL_TORTURE_LOG_END ' + identity)
        # Capture only this invocation's kmsg window. dmesg may contain earlier
        # test logs; duplicate outer/inner markers are a hard failure.
        kmsg = [re.sub(r'^\s*(?:<\d+>)?\[\s*\d+(?:\.\d+)?\]\s*', '', line) for line in log]
        evidence, _, _ = bounded(kmsg, 'CHI_KERNEL_TORTURE_KMSG_BEGIN ' + identity,
                                'CHI_KERNEL_TORTURE_KMSG_END ' + identity)
        body = '\n'.join(evidence)
        require(not re.search(r'!!!|End of test: (?:FAILURE|RCU_HOTPLUG|LOCK_HOTPLUG)|BUG:|WARNING:|Kernel panic|Oops:|Writer stall state|detected stalls', body),
                f'{kind}: kernel error/stall in test evidence')
        summary = re.findall(r'^' + re.escape(kind) + r'-torture:--- End of test: SUCCESS(?: \[debug\])?: (.+)$', body, re.M)
        require(len(summary) == 1, f'{kind}: missing unique final module SUCCESS')
        # The final module summary independently reports actual worker counts.
        actual = dict(re.findall(r'(\w+)=(-?\d+)', summary[0]))
        if kind == 'rcu':
            require(parameters.get('nreaders') == str(threads) and parameters.get('n_barrier_cbs') == str(threads)
                    and parameters.get('gp_normal') == '1' and parameters.get('gp_sync') == '1', 'RCU reader/GP/barrier profile mismatch')
            require(actual.get('nreaders') == str(threads), 'RCU actual reader count mismatch')
            work = rcu_work(body, min_rcu_allocations)
        else:
            readers = threads if kind in ('rw_lock', 'rwsem_lock') else 0
            require(parameters.get('nwriters_stress') == str(threads) and parameters.get('nreaders_stress') == str(readers), 'lock requested thread profile mismatch')
            require(actual.get('nwriters_stress') == str(threads) and actual.get('nreaders_stress') == str(readers), 'lock actual thread profile mismatch')
            work = lock_work(body, kind, threads, readers, min_lock_acquires)
        report.append({'test': kind, 'requestedSeconds': str(duration), 'elapsedSeconds': str(elapsed),
                       'parameters': parameters, 'work': work, 'passed': True})
    return {'passed': True, 'runId': run_id, 'kernel': begin['kernel'], 'onlineCpus': '0-3',
            'onlineNodes': '0-1', 'cases': report,
            'thresholds': {'minSeconds': str(min_seconds), 'minRcuAllocations': min_rcu_allocations,
                           'minLockAcquires': min_lock_acquires},
            'scope': 'Finite Linux kernel module executions with real progress; not all RCU/locking modes, memory-model proof, or CHI opcode coverage.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('serial_log', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--lock-types', nargs='+', choices=LOCK_TYPES, default=list(LOCK_TYPES))
    parser.add_argument('--min-seconds', type=Decimal, default=Decimal(1))
    parser.add_argument('--min-rcu-allocations', type=int, default=1)
    parser.add_argument('--min-lock-acquires', type=int, default=100)
    args = parser.parse_args()
    text = args.serial_log.read_text(errors='replace')
    try:
        report = check(text, args.lock_types, args.min_seconds, args.min_rcu_allocations, args.min_lock_acquires)
    except (ValueError, KeyError, ArithmeticError) as error:
        report = {'passed': False, 'error': str(error)}
    report['serialLog'] = str(args.serial_log)
    report['serialSha256'] = hashlib.sha256(args.serial_log.read_bytes()).hexdigest()
    output = args.output or args.serial_log.with_name('kernel-torture-result.json')
    output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
