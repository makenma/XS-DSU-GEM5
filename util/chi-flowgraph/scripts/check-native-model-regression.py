"""Compare two completed CPU runs after a host-only implementation change."""
import argparse
import hashlib
import json
from pathlib import Path

HOST = {'hostInstRate', 'hostMemory', 'hostOpRate', 'hostSeconds', 'hostTickRate'}


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def statistics(path):
    blocks, current = [], None
    for line in path.read_text().splitlines():
        if 'Begin Simulation Statistics' in line:
            if current is not None:
                raise ValueError('nested statistic block')
            current = {}
        elif 'End Simulation Statistics' in line:
            if current is None:
                raise ValueError('unmatched statistic end')
            blocks.append(current)
            current = None
        elif current is not None:
            words = line.split('#', 1)[0].split()
            if words and words[0] not in HOST:
                if len(words) < 2 or words[0] in current:
                    raise ValueError('invalid statistic: ' + line)
                current[words[0]] = words[1:]
    if current is not None or not blocks:
        raise ValueError('truncated or absent statistics')
    return blocks


p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--before', type=Path, required=True)
p.add_argument('--after', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--without-trace', action='store_true', help='Compare compact counts when both runs disabled detailed tracing')
a = p.parse_args()
results = [json.loads((folder / 'native-l2-result.json').read_text()) for folder in (a.before, a.after)]
stats = [statistics(folder / 'stats.txt') for folder in (a.before, a.after)]
observation = 'counts.json' if a.without_trace else 'raw.jsonl'
hashes = {name: [digest(folder / name) for folder in (a.before, a.after)]
          for name in ('config.ini', 'flowgraph-config.json', observation)}
checks = {'actualCpuSuccess': all(r.get('pass') is True and r.get('exitCode') == 0 and
                                 r.get('cause') == 'exiting with last active thread context' for r in results),
          'sameResult': results[0] == results[1], 'sameSimulationStatistics': stats[0] == stats[1],
          **{name: values[0] == values[1] for name, values in hashes.items()}}
report = {'passed': all(checks.values()), 'checks': checks, 'fileSha256': hashes,
          'statisticDifferences': [{key: [left.get(key), right.get(key)]
                                   for key in left.keys() | right.keys() if left.get(key) != right.get(key)}
                                  for left, right in zip(stats[0], stats[1])],
          'cpuExitTicks': [r['cpuExitTick'] for r in results],
          'drainEndTicks': [r['drainEndTick'] for r in results],
          'statisticCounts': [[len(block) for block in group] for group in stats],
          'ignoredHostStatistics': sorted(HOST),
          'scope': f'Completed real CPU self-checks, configuration, every non-host statistic and every {observation} byte. '
                   'No unobserved cache-state equality or exhaustive RTL equivalence claim.'}
a.output.write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
raise SystemExit(0 if report['passed'] else 1)
