"""Check complete official litmus7 execution against the pinned herd7 state oracle."""
import argparse
import json
from pathlib import Path
import re


def state_key(text):
    """Canonicalize printed register/location values, rejecting ambiguous states."""
    pairs = []
    for item in text.strip().split(';'):
        if not item.strip():
            continue
        match = re.fullmatch(r'\s*([A-Za-z0-9_:]+)\s*=\s*(-?(?:0x[0-9a-fA-F]+|[0-9]+))\s*', item)
        if not match:
            raise ValueError('unrecognized state: ' + item)
        value = match[2]
        pairs.append((match[1], int(value, 16 if 'x' in value else 10)))
    if not pairs or len({key for key, _ in pairs}) != len(pairs):
        raise ValueError('empty state or duplicate location')
    return tuple(sorted(pairs))


def check(log, manifest):
    issues, results = [], []
    def require(ok, detail):
        if not ok:
            issues.append(detail)
    def integer(value, minimum=0):
        return type(value) is int and value >= minimum
    require(manifest.get('schemaVersion') == 1 and manifest.get('suite') == 'official-diy7-riscv-linux',
            'incorrect build manifest')
    inventory = manifest.get('tests', [])
    names = [item.get('name') for item in inventory]
    require(bool(names) and len(names) == len(set(names)) == manifest.get('testCount'),
            'empty, duplicate, or incomplete manifest test inventory')
    unique_count = len({item.get('testHash') for item in inventory})
    require(manifest.get('uniqueSemanticTestCount') == unique_count, 'incorrect unique semantic Hash count')
    if manifest.get('profile') == 'large':
        require(unique_count >= 1000, 'large profile needs at least 1000 different semantic Hash values')
    require(manifest.get('excludedTests') == [], 'build omitted tests')
    runtime = manifest.get('runtime', {})
    samples, runs = runtime.get('samplesPerInstance'), runtime.get('runs')
    require(integer(samples, 1) and integer(runs, 1), 'invalid expected sample/run count')
    require(runtime.get('availableCpus') == 4 and runtime.get('affinityCpus') == [0, 1, 2, 3]
            and runtime.get('forcedAffinity') is True, 'four-core forced affinity missing')
    markers = {'BEGIN': [], 'END': []}
    for index, line in enumerate(log.splitlines()):
        for kind in markers:
            prefix = 'LINUX_LITMUS_' + kind + ' '
            if line.startswith(prefix):
                try:
                    value = json.loads(line[len(prefix):])
                    if not isinstance(value, dict):
                        raise ValueError('not object')
                    markers[kind].append((index, value))
                except ValueError:
                    issues.append('malformed ' + kind + ' marker')
    require(len(markers['BEGIN']) == len(markers['END']) == 1, 'expected exactly one BEGIN and END; truncation cannot pass')
    begin = markers['BEGIN'][0][1] if markers['BEGIN'] else {}
    end = markers['END'][0][1] if markers['END'] else {}
    require(begin.get('profile') == manifest.get('profile'), 'runtime profile differs from manifest')
    require(begin.get('binarySha256') == manifest.get('binarySha256')
            and bool(re.fullmatch(r'[0-9a-f]{64}', str(begin.get('binarySha256', '')))), 'wrong guest binary identity')
    require(begin.get('onlineCpus') == 4, 'four actual online CPUs not confirmed')
    require(type(end.get('exitCode')) is int and end.get('exitCode') == 0, 'guest failed, timed out, or omitted exit code')
    body = log
    if len(markers['BEGIN']) == len(markers['END']) == 1:
        first, last = markers['BEGIN'][0][0], markers['END'][0][0]
        require(first < last, 'END precedes BEGIN')
        lines = log.splitlines()
        outside = '\n'.join(lines[:first] + lines[last + 1:])
        require(not re.search(r'^(Test|Observation) chi_', outside, re.M), 'litmus result outside the runtime markers')
        body = '\n'.join(lines[first + 1:last])
    require(not re.search(r'Fatal|Assertion failed|Segmentation fault|Aborted|LINUX_LITMUS_ABORT|TIMEOUT', body),
            'fatal error or timeout appeared in guest output')
    headers = list(re.finditer(r'^Test (\S+) (Allowed|Forbidden|Required)\s*$', body, re.M))
    require([match[1] for match in headers] == names, 'missing, extra, reordered, or repeated runtime tests')
    all_observations = re.findall(r'^Observation (\S+) ', body, re.M)
    require(all_observations == names, 'missing, duplicate, or wrong observation names')
    expected_by_name = {item['name']: item for item in inventory}
    for index, header in enumerate(headers):
        name = header[1]
        spec = expected_by_name.get(name)
        if spec is None:
            continue
        label = name + ': '
        part = body[header.end():headers[index + 1].start() if index + 1 < len(headers) else len(body)]
        hashes = re.findall(r'^Hash=([0-9a-f]+)\s*$', part, re.M)
        require(hashes == [spec.get('testHash')], label + 'runtime test Hash differs from official model')
        observations = re.findall(r'^Observation (\S+) (Never|Sometimes|Always) (\d+) (\d+)\s*$', part, re.M)
        witnesses = re.findall(r'^Positive: (\d+), Negative: (\d+)\s*$', part, re.M)
        histograms = re.findall(r'^Histogram \((\d+) states\)\s*$', part, re.M)
        rows = re.findall(r'^\s*(\d+)\s*([*:])>([^\n]+)$', part, re.M)
        require(len(observations) == len(witnesses) == len(histograms) == 1, label + 'missing or duplicate result records')
        count, positive, negative = 0, 0, 0
        states = set()
        allowed = set()
        try:
            allowed = {state_key(state) for state in spec.get('modelAllowedStates', [])}
            require(bool(allowed), label + 'missing model state oracle')
            for amount, marker, state in rows:
                amount = int(amount)
                key = state_key(state)
                require(amount > 0, label + 'zero-count histogram row')
                require(key not in states, label + 'duplicate histogram state')
                require(key in allowed, label + 'observed state forbidden by pinned model: ' + state)
                states.add(key)
                count += amount
                if marker == '*':
                    positive += amount
                else:
                    negative += amount
        except ValueError as error:
            issues.append(label + str(error))
        threads = spec.get('threads')
        expected = None
        if integer(threads, 1) and threads <= 4 and integer(samples, 1) and integer(runs, 1):
            expected = max(1, 4 // threads) * samples * runs
        require(expected is not None and count == expected, label + 'histogram total differs from scheduled instances × samples × runs')
        if histograms:
            require(int(histograms[0]) == len(rows) == len(states), label + 'histogram state count mismatch')
        if observations:
            obs_name, observation, pos, neg = observations[0]
            require(obs_name == name and (int(pos), int(neg)) == (positive, negative), label + 'observation differs from histogram')
            expected_observation = 'Never' if positive == 0 else 'Always' if negative == 0 else 'Sometimes'
            require(observation == expected_observation, label + 'incorrect runtime observation class')
        if witnesses:
            require(tuple(map(int, witnesses[0])) == (positive, negative), label + 'witness counts differ from histogram')
        model_observation = spec.get('modelObservation')
        require(model_observation in ('Never', 'Sometimes', 'Always'), label + 'invalid model observation')
        if model_observation == 'Never':
            require(positive == 0, label + 'forbidden condition observed')
        if model_observation == 'Always':
            require(negative == 0, label + 'required condition not observed')
        results.append({'name': name, 'threads': threads, 'instances': max(1, 4 // threads) if integer(threads, 1) else None,
                        'expectedOutcomes': expected, 'observedOutcomes': count, 'positive': positive, 'negative': negative,
                        'observedStates': len(states), 'modelObservation': model_observation})
    return {'schemaVersion': 1, 'status': 'PASS' if not issues else 'FAIL', 'pass': not issues,
            'profile': manifest.get('profile'), 'testCount': len(results),
            'uniqueSemanticTestCount': manifest.get('uniqueSemanticTestCount'), 'tests': results, 'issues': issues,
            'scope': 'Complete execution of this fixed generated matrix, forced CPU affinity, exact outcome counts, '
                     'and agreement with the pinned herd7 observable-state oracle; no exhaustive memory-model proof.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = check(args.input.read_text(errors='replace'), json.loads(args.manifest.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'testCount': report['testCount'], 'issues': report['issues']}))
    raise SystemExit(0 if report['pass'] else 1)
