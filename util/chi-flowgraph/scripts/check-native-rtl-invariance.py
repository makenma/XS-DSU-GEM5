"""Compare existing native RTL trace-on/off artifacts without running gem5."""
import argparse
from collections import Counter
import configparser
import hashlib
import json
from pathlib import Path

HOST = {'hostInstRate', 'hostMemory', 'hostOpRate', 'hostSeconds', 'hostTickRate'}
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--on', type=Path, required=True)
parser.add_argument('--off', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def stats(path):
    blocks, block, ignored = [], None, []
    for line in path.read_text().splitlines():
        if 'Begin Simulation Statistics' in line:
            if block is not None:
                raise ValueError('nested statistics blocks')
            block = {}
        elif 'End Simulation Statistics' in line:
            if block is None:
                raise ValueError('unmatched statistics end')
            blocks.append(block)
            block = None
        elif block is not None:
            words = line.split('#', 1)[0].split()
            if not words:
                continue
            if len(words) < 2 or words[0] in block:
                raise ValueError(f'malformed or duplicated statistic: {line}')
            if words[0] in HOST:
                ignored.append(words[0])
            else:
                # Preserve integer precision, NaN spelling, vector values and
                # every non-host counter; do not convert to float or sample.
                block[words[0]] = ' '.join(words[1:])
    if block is not None or not blocks:
        raise ValueError('missing or incomplete statistics block')
    return blocks, sorted(set(ignored))

def load(folder):
    result = json.loads((folder / 'native-l2-result.json').read_text())
    log = (folder / 'run.log').read_text()
    lines = log.splitlines()
    banners = [line for line in lines if line.startswith('NATIVE_RTL_HART_')]
    hart_pass = [lines.count(f'NATIVE_RTL_HART_{h}_PASS') == 1 for h in range(4)]
    cpu_pass = (result.get('pass') is True and result.get('exitCode') == 0
                and result.get('cause') == 'exiting with last active thread context'
                and result.get('protocolOpcodeInjection') is False
                and all(hart_pass) and 'NATIVE_RTL_FAIL' not in log)
    configuration = configparser.ConfigParser(interpolation=None)
    configuration.read(folder / 'config.ini')
    types = Counter(configuration.get(section, 'type', fallback='')
                    for section in configuration.sections())
    sim_stats, ignored = stats(folder / 'stats.txt')
    return {'result': result, 'cpuPass': cpu_pass, 'hartPass': hart_pass,
            'bannerOrder': banners, 'statistics': sim_stats, 'ignored': ignored,
            'nativeConfiguration': types['ChiNativeL2'] == 4 and types['Cache2ChiBridge'] == 0,
            'hashes': {name: digest(folder / name) for name in
                       ('native-l2-result.json', 'config.ini', 'flowgraph-config.json', 'stats.txt', 'run.log')}}

on, off = load(args.on), load(args.off)
result_fields = {key: on['result'].get(key) == off['result'].get(key)
                 and (key in on['result']) == (key in off['result'])
                 for key in sorted(set(on['result']) | set(off['result']))}
differences = {}
if len(on['statistics']) != len(off['statistics']):
    differences['blockCount'] = [len(on['statistics']), len(off['statistics'])]
for n, (left, right) in enumerate(zip(on['statistics'], off['statistics'])):
    delta = {key: {'on': left.get(key), 'off': right.get(key)}
             for key in sorted(set(left) | set(right)) if left.get(key) != right.get(key)}
    if delta:
        differences[str(n)] = delta
config_equal = {name: on['hashes'][name] == off['hashes'][name]
                for name in ('config.ini', 'flowgraph-config.json')}
trace_on_present = (args.on / 'raw.jsonl').is_file() and (args.on / 'raw.jsonl').stat().st_size > 0
trace_off_absent = not (args.off / 'raw.jsonl').exists()
passed = (on['cpuPass'] and off['cpuPass'] and on['nativeConfiguration'] and off['nativeConfiguration']
          and all(result_fields.values()) and all(config_equal.values()) and not differences
          and on['bannerOrder'] == off['bannerOrder'] and trace_on_present and trace_off_absent)
report = {'passed': passed,
    'purpose': 'Compare completed native RTL workload with exporter enabled and disabled',
    'runs': {label: {'directory': str(folder), 'cpuSelfCheckPassed': data['cpuPass'],
                    'hartPass': data['hartPass'], 'bannerOrder': data['bannerOrder'],
                    'cpuExitTick': data['result']['cpuExitTick'],
                    'drainEndTick': data['result']['drainEndTick'], 'fileSha256': data['hashes']}
             for label, folder, data in [('traceOn', args.on, on), ('traceOff', args.off, off)]},
    'traceOnPresent': trace_on_present, 'traceOffAbsent': trace_off_absent,
    'resultFieldsEqual': result_fields, 'configurationFilesIdentical': config_equal,
    'comparedStatisticsBlocks': len(on['statistics']),
    'comparedSimulationStatsPerBlock': [len(block) for block in on['statistics']],
    'ignoredHostStats': sorted(HOST),
    'hostStatsActuallyPresent': {'on': on['ignored'], 'off': off['ignored']},
    'statDifferences': differences,
    'boundary': 'CPU self-checks, every result field, identical recorded configuration, and every non-host statistic match. Complete final cache tags/data/coherence state were not directly dumped; no equality of an unobserved cache-state dump is claimed.'}
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps({'passed': passed, 'cpuExitTick': on['result']['cpuExitTick'],
                  'drainEndTick': on['result']['drainEndTick'],
                  'comparedSimulationStatsPerBlock': report['comparedSimulationStatsPerBlock'],
                  'statDifferences': differences, 'report': str(args.output)}, indent=2))
if not passed:
    raise SystemExit('Native RTL exporter invariance failed')
