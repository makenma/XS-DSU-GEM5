"""Compare a bounded real-FS old/new run; equivalence is never Linux boot acceptance.

Prepare the immutable input plan BEFORE either run. Execute its two commands in
the recorded environment, save stdout/stderr as run.log and the actual process
return code as host-exit-code.txt, then invoke this file with --plan and --output.
This helper never launches gem5. Use WSL for real runs so recorded paths resolve.
"""
import argparse
from collections import Counter
import configparser
import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shlex
import time

HERE = Path(__file__).resolve().parent
HOST = {'hostInstRate', 'hostMemory', 'hostOpRate', 'hostSeconds', 'hostTickRate'}
FILES = ['config.ini', 'config.json', 'flowgraph-config.json', 'device.dtb',
         'native-linux-result.json', 'native-linux-progress.jsonl',
         'native-chi-counts.json', 'stats.txt', 'system.platform.terminal',
         'run.log', 'host-exit-code.txt']
SCOPE = ('Bounded real full-system old/new equivalence only: exact non-host statistics, '
         'accepted endpoint transfer census, serial bytes, instantiated configuration, '
         'DTB, manifest and incomplete-run result. Not Linux boot/suite PASS, '
         'full CHI event ordering/payload or hidden state equality, or RTL equivalence.')


def unique_json(text):
    # run.log permits opaque diagnostic bytes, but its JSON records do not.
    text.encode('utf-8', errors='strict')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key: ' + key)
            result[key] = value
        return result
    def bad_constant(value):
        raise ValueError('non-finite JSON: ' + value)
    return json.loads(text, object_pairs_hook=pairs, parse_constant=bad_constant)


def digest(path):
    path = Path(path)
    before = path.stat()
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('input changed while hashing: ' + str(path))
    return result.hexdigest()


def load_module(name):
    spec = importlib.util.spec_from_file_location('fs_equivalence_' + name.replace('-', '_'), HERE / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def statistics(text):
    blocks, current = [], None
    for line in text.splitlines():
        if line.strip() == '---------- Begin Simulation Statistics ----------':
            if current is not None:
                raise ValueError('nested statistics block')
            current = {}
        elif line.strip() == '---------- End Simulation Statistics   ----------':
            if current is None:
                raise ValueError('unmatched statistics end')
            if not current:
                raise ValueError('empty statistics block')
            blocks.append(current)
            current = None
        else:
            words = line.split('#', 1)[0].split()
            if not words:
                continue
            if current is None:
                raise ValueError('data outside statistics block: ' + line)
            if len(words) < 2 or words[0] in current:
                raise ValueError('duplicate or malformed statistic: ' + line)
            current[words[0]] = words[1:]
    if current is not None or not blocks:
        raise ValueError('truncated or missing statistics')
    return [{key: value for key, value in block.items() if key not in HOST} for block in blocks]


def prepare(before, after, before_binary, after_binary, firmware, config,
            gem5_source, ticks, progress_ticks, suite_inputs=None):
    paths = [Path(p).resolve() for p in (before, after, before_binary, after_binary,
                                       firmware, config, gem5_source)]
    before, after, before_binary, after_binary, firmware, config, gem5_source = paths
    if before == after or ticks <= 0 or progress_ticks <= 0:
        raise ValueError('distinct output directories and positive tick budgets required')
    for folder in (before, after):
        if folder.exists() and (not folder.is_dir() or any(folder.iterdir())):
            raise ValueError('refusing a nonempty/preexisting run: ' + str(folder))
    sources = [before_binary, after_binary, firmware, config, Path(__file__).resolve(),
               HERE / 'check-chi-transfer-census.py', HERE / 'check-native-linux-validation.py',
               gem5_source / 'configs/common/xiangshan.py', gem5_source / 'configs/common/Caches.py']
    if suite_inputs:
        suite_inputs = Path(suite_inputs).resolve()
        sources += [suite_inputs, config.parent / 'linux_stage_gate.py']
    hashes = {str(path): digest(path) for path in sources}
    if hashes[str(before_binary)] == hashes[str(after_binary)]:
        raise ValueError('old/new binaries have the same hash; this is not a change comparison')
    flags = ['--kernel', str(firmware), '--numa-nodes', '2', '--seed', '1', '--zicbom',
             '--uncached-test-ram', '--max-ticks', str(ticks), '--progress-ticks', str(progress_ticks)]
    if suite_inputs:
        flags += ['--suite-pipeline', '--suite-inputs', str(suite_inputs)]
    return {'schemaVersion': 1, 'kind': 'bounded-real-fs-equivalence-inputs',
            'createdNs': time.time_ns(), 'ticks': str(ticks), 'progressTicks': str(progress_ticks),
            'config': str(config), 'firmware': str(firmware), 'inputSha256': hashes,
            'suitePipeline': bool(suite_inputs), 'expectedHostExitCode': 2 if suite_inputs else 0,
            'environment': {'CHI_GEM5_DIR': str(gem5_source), 'PYTHONHASHSEED': '0'},
            'unsetEnvironment': ['CHI_FLOWGRAPH_OUT', 'CHI_FLOWGRAPH_COUNTS', 'CHI_FLOWGRAPH_CONFIG'],
            'runs': [{'label': label, 'directory': str(folder), 'binary': str(binary),
                      'command': [str(binary), '--outdir=' + str(folder), str(config), *flags]}
                     for label, folder, binary in [('before', before, before_binary), ('after', after, after_binary)]],
            'scope': SCOPE}


def differences(left, right, path='$'):
    """Exact recursive comparison; do not zip away missing blocks or ignore unknown fields."""
    if type(left) is not type(right):
        return [{'path': path, 'before': left, 'after': right}]
    if isinstance(left, dict):
        result = []
        for key in sorted(left.keys() | right.keys()):
            if key not in left or key not in right:
                result.append({'path': path + '.' + key, 'before': left.get(key), 'after': right.get(key),
                               'missing': 'before' if key not in left else 'after'})
            else:
                result += differences(left[key], right[key], path + '.' + key)
        return result
    if isinstance(left, list):
        result = []
        if len(left) != len(right):
            result.append({'path': path + '.length', 'before': len(left), 'after': len(right)})
        for index in range(min(len(left), len(right))):
            result += differences(left[index], right[index], f'{path}[{index}]')
        return result
    return [] if left == right else [{'path': path, 'before': left, 'after': right}]


def compare(plan, plan_path=None, checker_revision=None):
    report = {'schemaVersion': 1, 'status': 'INCOMPLETE', 'equivalent': False,
              'linuxBootAcceptance': False, 'linuxSuiteAcceptance': False,
              'checks': {}, 'errors': [], 'incomplete': [], 'inputSha256': {},
              'ignoredHostStatistics': sorted(HOST),
              'normalization': {'config': ['system.workload.dtb_filename (exact run/device.dtb only)'],
                                'result': ['gem5Sha256 (must match pinned binary)',
                                           'terminalFile', 'transferCensusFile (exact run paths only)'],
                                'progress': ['hostElapsedSeconds']},
              'scope': SCOPE}
    def require(ok, message):
        if not ok:
            report['errors'].append(message)
        return bool(ok)
    def read(path):
        path = Path(path)
        try:
            before = path.stat()
            data = path.read_bytes()
            after = path.stat()
            require((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns),
                    'file changed during read: ' + str(path))
            report['inputSha256'][str(path)] = hashlib.sha256(data).hexdigest()
            return data
        except FileNotFoundError:
            report['incomplete'].append('missing: ' + str(path))
            return None
    if plan_path:
        read(plan_path)
    if checker_revision:
        previous = Path(checker_revision['previousChecker']).resolve()
        old_sha = digest(previous)
        read(previous)
        own_path = str(Path(__file__).resolve())
        require(bool(checker_revision.get('reason', '').strip()), 'checker revision requires an explicit reason')
        require(old_sha == plan.get('inputSha256', {}).get(own_path),
                'previous checker snapshot does not match the immutable plan')
        report['checkerRevision'] = {'explicitlyRequested': True, 'path': own_path,
                                     'previousChecker': str(previous), 'beforeSha256': old_sha,
                                     'afterSha256': digest(own_path), 'reason': checker_revision.get('reason'),
                                     'scope': 'Only this comparison helper may differ; all other input hashes remain strict.'}
    require(plan.get('schemaVersion') == 1 and plan.get('kind') == 'bounded-real-fs-equivalence-inputs',
            'incorrect input plan format')
    require(len(plan.get('runs', [])) == 2 and plan['runs'][0]['directory'] != plan['runs'][1]['directory'],
            'plan must contain two distinct runs')
    for path, expected in plan.get('inputSha256', {}).items():
        try:
            actual = digest(path)
            report['inputSha256'][path] = actual
            own_revision = checker_revision and path == str(Path(__file__).resolve())
            require(actual == expected or own_revision, 'pinned input changed: ' + path)
        except FileNotFoundError:
            report['incomplete'].append('missing pinned input: ' + path)
    if report['errors'] or report['incomplete']:
        report['status'] = 'FAIL' if report['errors'] else 'INCOMPLETE'
        return report
    census_checker = load_module('check-chi-transfer-census.py')
    acceptance = load_module('check-native-linux-validation.py')
    normalized, evidence = [], []
    for run in plan['runs']:
        label, folder = run['label'], Path(run['directory'])
        data = {name: read(folder / name) for name in FILES}
        if any(value is None for value in data.values()):
            continue
        try:
            for name in FILES:
                require((folder / name).stat().st_mtime_ns >= plan['createdNs'], label + ': stale output: ' + name)
            # gem5's image loader can print mapped ELF bytes into diagnostics.
            # Preserve those bytes losslessly without relaxing structured files.
            text = {name: value.decode('utf-8', errors='surrogateescape' if name == 'run.log' else 'strict')
                    for name, value in data.items() if name != 'device.dtb'}
            parsed = {name: unique_json(text[name]) for name in FILES if name.endswith('.json')}
            result, config, census = [parsed[name] for name in ('native-linux-result.json', 'flowgraph-config.json',
                                                               'native-chi-counts.json')]
            manifest = config['manifest']
            linux = manifest['linux']
            require(result.get('fullSystem') is True and result.get('configuredHarts') == 4
                    and result.get('numaNodes') == 2 and result.get('protocolOpcodeInjection') is False,
                    label + ': not four-hart/two-NUMA native full-system')
            require(result.get('status') == 'incomplete' and result.get('bootPass') is False
                    and result.get('guestSuitePass') is None and result.get('cause') == 'simulate() limit reached'
                    and result.get('exitCode') == 0 and result.get('tick') == plan['ticks'],
                    label + ': did not finish exactly at the declared incomplete simulation limit')
            pipeline = result.get('suitePipeline', {})
            require(pipeline.get('requested') is plan['suitePipeline'] and pipeline.get('completed') is False
                    and pipeline.get('error') is None and pipeline.get('stages') == [],
                    label + ': bounded comparison must precede every suite exit')
            require(int(text['host-exit-code.txt'].strip()) == plan['expectedHostExitCode'],
                    label + ': unexpected gem5 host exit code')
            require(result.get('gem5Sha256') == plan['inputSha256'][run['binary']], label + ': executed binary mismatch')
            firmware_sha = plan['inputSha256'][plan['firmware']]
            dtb_sha = hashlib.sha256(data['device.dtb']).hexdigest()
            require(result.get('firmwareSha256') == linux.get('payload', {}).get('sha256') == firmware_sha,
                    label + ': firmware hash differs from pinned suite firmware')
            require(linux.get('firmware') == plan['firmware'], label + ': firmware path mismatch')
            require(result.get('dtbSha256') == linux.get('dtbSha256') == dtb_sha,
                    label + ': actual DTB hash differs from result/manifest')
            require(result.get('simulationSeed') == manifest.get('simulationSeed') == 1,
                    label + ': different simulation seed')
            numa = linux.get('numa', [])
            require(len(numa) == 2 and {n.get('nodeId') for n in numa} == {0, 1}
                    and sorted(h for n in numa for h in n.get('harts', [])) == [0, 1, 2, 3],
                    label + ': missing two-NUMA manifest placement')
            ini = configparser.ConfigParser(interpolation=None, strict=True)
            ini.optionxform = str
            ini.read_string(text['config.ini'])
            ini_values = {s: dict(ini[s]) for s in ini.sections()}
            require(not ini.defaults(), label + ': config.ini unexpected defaults')
            require(ini_values['system.workload'].get('type') == 'RiscvLinux'
                    and ini_values['system.workload'].get('object_file') == plan['firmware'],
                    label + ': workload is not the pinned Linux firmware')
            dtb_path = str(folder / 'device.dtb')
            require(ini_values['system.workload'].get('dtb_filename') == dtb_path,
                    label + ': config.ini DTB path is not this run')
            config_json = parsed['config.json']
            require(config_json.get('full_system') is True
                    and config_json['system']['workload'].get('dtb_filename') == dtb_path
                    and config_json['system']['workload'].get('object_file') == plan['firmware'],
                    label + ': config.json Linux/DTB/firmware mismatch')
            audit = acceptance.Audit()
            topology = acceptance.topology(audit, folder, config, label)
            report['errors'] += audit.failures
            report['incomplete'] += audit.incomplete
            require(topology['verified'], label + ': native topology is not verified')
            checked_census = census_checker.check(census, config)
            require(checked_census['valid'], label + ': invalid census: ' + repr(checked_census['errors']))
            require(census.get('snapshotKind') == 'process-exit' and 0 < int(census['observations'])
                    and int(census['lastTick']) <= int(plan['ticks']), label + ': census is not a finished bounded run')
            require(data['system.platform.terminal'] and (b'OpenSBI' in data['system.platform.terminal']
                    or b'Linux version ' in data['system.platform.terminal']), label + ': no actual firmware/Linux serial evidence')
            stats = statistics(text['stats.txt'])
            for block in stats:
                require(block.get('finalTick') == [plan['ticks']] and block.get('simTicks') == [plan['ticks']]
                        and int(block.get('simInsts', ['0'])[0]) > 0, label + ': empty/wrong-tick statistics block')
                for hart in range(4):
                    predictions = sum(int(v[0]) for k, v in block.items()
                                      if k.startswith(f'system.cpu{hart}.branchPred.predsOfEachStage::')
                                      and k.rsplit('::', 1)[1].isdigit())
                    require(predictions > 0, label + f': no real CPU {hart} BPU work')
            progress = [unique_json(line) for line in text['native-linux-progress.jsonl'].splitlines() if line.strip()]
            expected_ticks = list(range(int(plan['progressTicks']), int(plan['ticks']), int(plan['progressTicks']))) + [int(plan['ticks'])]
            require([p.get('tick') for p in progress] == [str(t) for t in expected_ticks], label + ': progress intervals missing/duplicated')
            last_elapsed, last_bytes = -1, -1
            for step in progress:
                elapsed, size = step.get('hostElapsedSeconds'), step.get('terminalBytes')
                require(type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= last_elapsed,
                        label + ': invalid host elapsed progress')
                require(type(size) is int and last_bytes <= size <= len(data['system.platform.terminal']),
                        label + ': invalid serial byte progress')
                require(step.get('cause') == 'simulate() limit reached' and step.get('exitCode') == 0,
                        label + ': progress shows unexpected guest exit')
                last_elapsed, last_bytes = elapsed, size
            require(progress[-1]['terminalBytes'] == len(data['system.platform.terminal']), label + ': final serial is not flushed')
            command_lines = [line[len('command line: '):] for line in text['run.log'].splitlines()
                             if line.startswith('command line: ')]
            require(len(command_lines) == 1 and shlex.split(command_lines[0]) == run['command'],
                    label + ': actual command differs from prepared command')
            log_results = [unique_json(line[len('NATIVE_LINUX_RESULT '):]) for line in text['run.log'].splitlines()
                           if line.startswith('NATIVE_LINUX_RESULT ')]
            require(log_results == [result], label + ': run.log final result missing/duplicate/different')
            log_progress = [unique_json(line[len('NATIVE_LINUX_PROGRESS '):]) for line in text['run.log'].splitlines()
                            if line.startswith('NATIVE_LINUX_PROGRESS ')]
            require(log_progress == progress, label + ': run.log progress differs from progress file')
            # The only path normalization is exact, validated output locations.
            for key, name in [('terminalFile', 'system.platform.terminal'), ('transferCensusFile', 'native-chi-counts.json')]:
                require(result.get(key) == str(folder / name), label + ': result path mismatch: ' + key)
            normalized_result = copy.deepcopy(result)
            normalized_result['gem5Sha256'] = '<pinned-old-or-new-binary>'
            normalized_result['terminalFile'] = '<run>/system.platform.terminal'
            normalized_result['transferCensusFile'] = '<run>/native-chi-counts.json'
            ini_values['system.workload']['dtb_filename'] = '<run>/device.dtb'
            config_json['system']['workload']['dtb_filename'] = '<run>/device.dtb'
            normalized_progress = [{k: v for k, v in p.items() if k != 'hostElapsedSeconds'} for p in progress]
            normalized.append({'statistics': stats, 'census': census, 'config.ini': ini_values,
                               'config.json': config_json, 'manifest': config, 'result': normalized_result,
                               'progress': normalized_progress, 'serialSha256': hashlib.sha256(data['system.platform.terminal']).hexdigest(),
                               'dtbSha256': dtb_sha})
            evidence.append({'label': label, 'tick': result['tick'], 'binarySha256': result['gem5Sha256'],
                             'statisticBlockCounts': [len(block) for block in stats],
                             'serialBytes': len(data['system.platform.terminal']), 'observations': census['observations'],
                             'topology': topology, 'censusValid': checked_census['valid']})
        except (ValueError, KeyError, TypeError, IndexError, configparser.Error) as error:
            report['errors'].append(label + ': malformed run artifact: ' + str(error))
    report['runs'] = evidence
    if len(normalized) == 2:
        for key in normalized[0]:
            found = differences(normalized[0][key], normalized[1][key])
            report['checks'][key] = {'equal': not found, 'differenceCount': len(found), 'differences': found[:100]}
            require(not found, 'old/new mismatch: ' + key)
    else:
        report['incomplete'].append('both complete bounded-run artifact sets are required')
    report['equivalent'] = not report['errors'] and not report['incomplete'] and len(normalized) == 2
    report['status'] = 'EQUIVALENT' if report['equivalent'] else ('FAIL' if report['errors'] else 'INCOMPLETE')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--recheck-current-checker', action='store_true',
                        help='Explicitly allow only this helper to differ from the original plan')
    parser.add_argument('--previous-checker', type=Path,
                        help='Archived helper source whose SHA matches the unchanged original plan')
    parser.add_argument('--revision-reason', help='Required audit explanation for a checker-only recheck')
    for name in ('before', 'after', 'before-binary', 'after-binary', 'firmware', 'config', 'gem5-source', 'suite-inputs'):
        parser.add_argument('--' + name, type=Path)
    parser.add_argument('--ticks', type=int, default=5_000_000_000)
    parser.add_argument('--progress-ticks', type=int, default=1_000_000_000)
    args = parser.parse_args()
    if args.prepare:
        if args.recheck_current_checker or args.previous_checker or args.revision_reason:
            parser.error('checker revision options apply only to rechecking an existing plan')
        names = ['before', 'after', 'before_binary', 'after_binary', 'firmware', 'config', 'gem5_source']
        if any(getattr(args, name) is None for name in names):
            parser.error('--prepare needs before/after directories, binaries, firmware, config and gem5-source')
        if args.plan.exists():
            parser.error('refusing to overwrite an existing input plan')
        try:
            plan = prepare(*(getattr(args, name) for name in names), args.ticks, args.progress_ticks, args.suite_inputs)
        except (OSError, ValueError) as error:
            parser.error(str(error))
        args.plan.parent.mkdir(parents=True, exist_ok=True)
        args.plan.write_text(json.dumps(plan, indent=2) + '\n')
        print(json.dumps(plan, indent=2))
        return 0
    if args.output is None:
        parser.error('--output is required for comparison')
    if args.recheck_current_checker != bool(args.previous_checker and args.revision_reason):
        parser.error('rechecking needs --recheck-current-checker, --previous-checker and --revision-reason together')
    if not args.recheck_current_checker and (args.previous_checker or args.revision_reason):
        parser.error('revision metadata requires explicit --recheck-current-checker')
    try:
        plan = unique_json(args.plan.read_text())
        revision = {'previousChecker': str(args.previous_checker), 'reason': args.revision_reason} if args.recheck_current_checker else None
        report = compare(plan, args.plan, revision)
    except (OSError, ValueError, KeyError, TypeError) as error:
        report = {'status': 'INCOMPLETE', 'equivalent': False, 'linuxBootAcceptance': False,
                  'linuxSuiteAcceptance': False, 'errors': [str(error)], 'scope': SCOPE}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0 if report['equivalent'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
