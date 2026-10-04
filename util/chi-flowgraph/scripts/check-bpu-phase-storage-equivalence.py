"""Compare actual original -> vector oracle -> phase storage CPU runs.

The raw records retain map iteration order. CSV/TXT comparison is byte-for-byte,
including sorting ties. Missing runs/component evidence cannot produce PASS.
"""
import argparse
import configparser
import hashlib
import json
import os
from pathlib import Path
import re

PROJECT = Path(__file__).resolve().parents[1]
MODEL05 = '4b8b39640ef6f2f70fec1824f6f3254bc15a41e45aada746d290de825692ad7d'
CSV = ('topMisPredicts.csv', 'topMispredictsByBranch.csv', 'topMisrateByBranch.csv',
       'topMispredictByPhase.csv', 'topMispredictBySubPhase.csv', 'topMisPredictHist.csv',
       'misPredIndirectStream.csv', 'fsqEntryCommittedInstNumDistsByPhase.csv',
       'fsqEntryFetchedInstNumDistsByPhase.csv', 'btbEntriesByPhase.csv')
TXT = ('altuseCnt.txt', 'TableHitCnt.txt', 'missHistMap.txt', 'misPredIndirect.txt')
PHASE_FILES = {'topMispredictByPhase.csv': 'branch-phase',
               'topMispredictBySubPhase.csv': 'branch-subphase',
               'fsqEntryCommittedInstNumDistsByPhase.csv': 'fsq-committed',
               'fsqEntryFetchedInstNumDistsByPhase.csv': 'fsq-fetched',
               'btbEntriesByPhase.csv': 'btb'}
HOST_STATS = {'hostSeconds', 'hostTickRate', 'hostMemory', 'hostInstRate', 'hostOpRate'}
COMPONENT_CASES = ('replayTwice', 'replayAfterClose', 'appendAfterReplay',
                   'corruptionRejected', 'truncationRejected', 'duplicatePhaseRejected',
                   'phaseCounterAcrossInt32', 'statisticsCountersAcrossInt32',
                   'mispredRateWideIntermediate')


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def unique_object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError('duplicate JSON key: ' + key)
        out[key] = value
    return out


def parse(text):
    return json.loads(text, object_pairs_hook=unique_object,
                      parse_constant=lambda s: (_ for _ in ()).throw(ValueError(s)))


def read_json(path):
    return parse(Path(path).read_text(encoding='utf-8'))


def integer(value, minimum=0):
    return type(value) is int and minimum <= value < 2**64


def require(condition, message):
    if not condition:
        raise ValueError(message)


def local_path(value):
    if os.name == 'nt' and re.match(r'^/mnt/[a-z]/', str(value)):
        value = str(value)[5].upper() + ':/' + str(value)[7:]
    return Path(value)


def stats(path):
    lines = Path(path).read_text().splitlines()
    require(sum('Begin Simulation Statistics' in line for line in lines) == 1,
            'requires exactly one final statistics block')
    require(sum('End Simulation Statistics' in line for line in lines) == 1,
            'truncated or repeated statistics block')
    out = {}
    for line in lines:
        cells = line.split('#', 1)[0].split()
        if not cells or cells[0].startswith('-'):
            continue
        require(len(cells) >= 2 and cells[0] not in out, 'malformed/duplicate stat')
        if cells[0] not in HOST_STATS:
            out[cells[0]] = cells[1:]
    require(bool(out), 'empty statistics')
    return out


def validate_run(directory, program, program_sha):
    directory = Path(directory)
    require((directory / 'host-exit-code.txt').read_text().strip() == '0', 'host exit is not zero')
    result = read_json(directory / 'native-l2-result.json')
    require(result.get('pass') is True and type(result.get('exitCode')) is int
            and result['exitCode'] == 0 and result.get('protocolOpcodeInjection') is False
            and result.get('cause') == 'exiting with last active thread context',
            'CPU run did not complete normally')
    require(all(re.fullmatch(r'[0-9]+', result.get(k, '')) for k in ('cpuExitTick', 'drainEndTick', 'tick'))
            and int(result['drainEndTick']) >= int(result['cpuExitTick'])
            and result['tick'] == result['drainEndTick'], 'invalid final ticks')
    log = (directory / 'run.log').read_text().splitlines()
    for hart in range(4):
        for prefix in ('BPU_PHASE_HART_', 'NATIVE_RTL_HART_'):
            require(log.count(f'{prefix}{hart}_PASS') == 1, 'missing/duplicate real hart PASS')
    require(not any(re.search(r'(BPU_PHASE|NATIVE_RTL)_.*FAIL', line) for line in log), 'hart FAIL')
    plan = read_json(directory / 'execution-plan.json')
    require(type(plan.get('schemaVersion')) is int and plan['schemaVersion'] == 1
            and plan.get('trace') in ('on', 'off'), 'bad execution plan')
    require(plan.get('programManifestSha256') == program_sha and plan.get('binaries') == program['binaries'],
            'run uses a different program manifest')
    for key in ('gem5Sha256', 'programManifestSha256', 'configSha256'):
        require(isinstance(plan.get(key), str) and re.fullmatch('[0-9a-f]{64}', plan[key]), 'invalid plan digest')
    command = plan.get('command')
    require(isinstance(command, list) and len(command) >= 4, 'missing actual command')
    for flag, value in [('--seed', '1'), ('--max-ticks', '2000000000'), ('--tail-ticks', '1000000')]:
        require(command.count(flag) == 1 and command[command.index(flag) + 1] == value,
                'different execution parameter: ' + flag)
    require(command.count('--elf-dir') == 1, 'missing ELF directory')
    require(sha(local_path(command[command.index('--elf-dir') + 1]) / 'manifest.json') == program_sha,
            'actual program source manifest changed')
    conf = configparser.ConfigParser(interpolation=None)
    conf.read(directory / 'config.ini')
    types = [conf.get(s, 'type', fallback='') for s in conf.sections()]
    require(types.count('ChiNativeL2') == 4 and types.count('Cache2ChiBridge') == 0
            and types.count('ChiRouterRefModel') == 6, 'actual native CPU/NoC config mismatch')
    for core in range(4):
        require(conf.get(f'system.cpu{core}.branchPred', 'type') == 'DecoupledBPUWithBTB',
                'unexpected BPU implementation')
    parsed_stats = stats(directory / 'stats.txt')
    for core in range(4):
        committed = parsed_stats[f'system.cpu{core}.committedInsts']
        require(len(committed) == 1 and committed[0].isdigit()
                and int(committed[0]) >= 200000 and int(committed[0]) % 100000 != 0,
                'CPU must cross two main phases with a partial tail')
    counts = read_json(directory / 'counts.json')
    require(counts.get('snapshotKind') == 'process-exit' and counts.get('counts'), 'missing final CHI counts')
    if plan['trace'] == 'on':
        with (directory / 'raw.jsonl').open(encoding='utf-8') as source:
            manifest = parse(next(source))
            require(manifest.get('program') == program, 'raw trace program differs from fixed manifest')
        raw_sha = sha(directory / 'raw.jsonl')
    else:
        require(not (directory / 'raw.jsonl').exists(), 'trace off unexpectedly wrote raw events')
        raw_sha = None
    return {'plan': plan, 'result': result, 'stats': parsed_stats, 'counts': counts,
            'configSha256': sha(directory / 'config.ini'), 'rawSha256': raw_sha}


def validate_records(path, kind, expected):
    content = Path(path).read_text(encoding='utf-8')
    require(content.endswith('\n'), 'truncated JSONL final line')
    lines = content.splitlines()
    require(len(lines) == expected, 'missing or duplicate phase frames')
    result = []
    bins = None
    for phase, line in enumerate(lines):
        item = parse(line)
        require(type(item.get('schemaVersion')) is int and item['schemaVersion'] == 1
                and item.get('kind') == kind and type(item.get('phaseId')) is int
                and item['phaseId'] == phase, 'phase id/kind/schema mismatch')
        rows = item.get('records')
        require(isinstance(rows, list), 'missing full phase records')
        if kind.startswith('branch-'):
            require(set(item) == {'schemaVersion', 'kind', 'phaseId', 'records', 'taken'}, 'branch frame fields')
            require(all(isinstance(r, list) and len(r) == 9 and all(integer(x) for x in r) for r in rows),
                    'invalid full branch record')
            require(len({tuple(r[:2]) for r in rows}) == len(rows), 'duplicate branch key')
            for row in rows:
                require(row[0:2] == row[2:4] and row[5] <= row[4], 'branch key/count mismatch')
            taken = item['taken']
            require(isinstance(taken, list) and all(isinstance(r, list) and len(r) == 2
                    and all(integer(x) for x in r) for r in taken), 'invalid complete taken map')
            require(len({r[0] for r in taken}) == len(taken), 'duplicate taken key')
        elif kind.startswith('fsq-'):
            require(set(item) == {'schemaVersion', 'kind', 'phaseId', 'records'}
                    and rows and all(integer(x) for x in rows), 'invalid FSQ bins')
            if bins is None:
                bins = len(rows)
            require(len(rows) == bins, 'truncated FSQ bins')
        else:
            require(set(item) == {'schemaVersion', 'kind', 'phaseId', 'records'}, 'BTB frame fields')
            for row in rows:
                require(isinstance(row, list) and len(row) == 16, 'incomplete BTB record')
                require(all(integer(row[i]) for i in (0, 1, 2, 3, 10, 14)), 'invalid BTB value')
                require(all(type(row[i]) is int and row[i] in (0, 1) for i in (4, 5, 6, 8, 9, 11, 12)),
                        'invalid BTB bool')
                require(type(row[13]) is int and type(row[15]) is int, 'invalid signed BTB fields')
                require((row[11] == 0 and row[7] is None) or
                        (row[11] == 1 and type(row[7]) is int and row[7] in (0, 1)),
                        'undefined/default isDirect must remain unknown')
            require(len({r[0] for r in rows}) == len(rows), 'duplicate BTB key')
        result.append(item)
    return result


def validate_exports(directory, hart):
    directory = Path(directory)
    name = f'system.cpu{hart}.branchPred'
    prefix = name + '.'
    manifest = read_json(directory / (prefix + 'phase-manifest.json'))
    required = {'schemaVersion', 'bpuName', 'numInstCommitted', 'phaseSizeByInst', 'subPhaseSize',
                'mainPhases', 'subPhases', 'dumpId', 'completeRecords', 'partialMainInsts', 'partialSubInsts'}
    require(set(manifest) == required and manifest['bpuName'] == name
            and type(manifest['schemaVersion']) is int and manifest['schemaVersion'] == 1
            and manifest['completeRecords'] is True, 'invalid phase manifest')
    for key in required - {'bpuName', 'completeRecords'}:
        require(integer(manifest[key]), 'invalid manifest integer: ' + key)
    n = manifest['numInstCommitted']
    require(manifest['phaseSizeByInst'] == 100000 and manifest['subPhaseSize'] == 10000
            and manifest['mainPhases'] == n // 100000 >= 2
            and manifest['subPhases'] == n // 10000
            and manifest['partialMainInsts'] == n % 100000 > 0
            and manifest['partialSubInsts'] == n % 10000 > 0, 'missing phases or nonintegral tail')
    snapshots = {}
    for filename, kind in PHASE_FILES.items():
        expected = manifest['subPhases'] if kind == 'branch-subphase' else manifest['mainPhases']
        snapshots[filename] = validate_records(directory / (prefix + filename + '.records.jsonl'), kind, expected)
        csv = (directory / (prefix + filename)).read_bytes()
        require(csv.endswith(b'\n') and len(csv.splitlines()) == expected + 1, 'CSV truncated phase rows')
        require([int(line.split(b',', 1)[0]) for line in csv.splitlines()[1:]] == list(range(expected)),
                'CSV duplicate/noncontiguous phase IDs')
    artifact_hashes = {}
    for file in CSV:
        artifact_hashes[prefix + file] = sha(directory / (prefix + file))
    for file in TXT:
        owner = f'system.cpu{hart}.commit.' if file == 'misPredIndirect.txt' else prefix + 'indirectBranchPred.'
        artifact_hashes[owner + file] = sha(directory / (owner + file))
    archives = sorted(directory.glob(name + '.dump-*.phase-manifest.json'))
    ids = sorted(int(re.search(r'\.dump-([0-9]+)\.', p.name)[1]) for p in archives)
    require(ids == list(range(manifest['dumpId'] + 1)), 'missing/duplicate dump archive')
    archive_records = {}
    files = CSV + tuple(f + '.records.jsonl' for f in PHASE_FILES) + ('phase-manifest.json',)
    for dump_id in ids:
        archive_prefix = name + f'.dump-{dump_id}.'
        m = read_json(directory / (archive_prefix + 'phase-manifest.json'))
        require(m.get('dumpId') == dump_id and m.get('bpuName') == name, 'archive identity mismatch')
        archive_records[dump_id] = {}
        for file in files:
            path = directory / (archive_prefix + file)
            if file.endswith('.jsonl'):
                csv = file.removesuffix('.records.jsonl')
                expected = m['subPhases'] if PHASE_FILES[csv] == 'branch-subphase' else m['mainPhases']
                archive_records[dump_id][file] = validate_records(path, PHASE_FILES[csv], expected)
            elif file == 'phase-manifest.json':
                archive_records[dump_id][file] = m
            else:
                archive_records[dump_id][file] = sha(path)
            if dump_id == manifest['dumpId']:
                require(path.read_bytes() == (directory / (prefix + file)).read_bytes(),
                        'latest report differs from final archived dump')
    return {'manifest': manifest, 'records': snapshots, 'artifacts': artifact_hashes,
            'archives': archive_records}


def check(legacy, oracle, candidate, program_manifest, helper_report=None):
    report = {'schemaVersion': 1, 'status': 'INCOMPLETE', 'pass': False,
              'cpuEquivalencePass': False, 'issues': [], 'missing': [],
              'scope': 'Finite actual four-core CPU regression plus host-storage component tests; not Linux acceptance.',
              'excludedStats': sorted(HOST_STATS), 'rawRecordOrderCompared': True,
              'unknownFields': ['BTBEntry.isDirect when valid==false (original default is uninitialized)'],
              'integerScope': 'Actual CPU comparison covers the defined old signed-int range. Wide-counter component tests check specified nonnegative integer semantics; they do not claim equivalence to old signed overflow.',
              'cpuRepeatedDumpTested': False}
    try:
        dirs = [Path(x).resolve() for x in (legacy, oracle, candidate)]
        require(len(set(dirs)) == 3, 'original, independent oracle and candidate must be distinct runs')
        program_manifest = Path(program_manifest)
        program = read_json(program_manifest)
        require(program.get('protocolOpcodeInjection') is False and program.get('rtlRequestWorkload') is True,
                'program is not actual CPU workload')
        workload = program['bpuPhaseWorkload']
        require(workload['minimumCompletedMainPhasesPerHart'] >= 2
                and workload['partialFinalPhaseMustBeObserved'] is True, 'weak phase workload')
        require([row['hart'] for row in program['binaries']] == list(range(4)), 'program must contain four harts')
        for row in program['binaries']:
            require(sha(program_manifest.parent / row['file']) == row['sha256'], 'ELF artifact hash mismatch')
        program_sha = sha(program_manifest)
        runs = [validate_run(d, program, program_sha) for d in dirs]
        require(runs[0]['plan']['gem5Sha256'] == MODEL05, 'legacy run is not immutable original model05')
        require(len({r['plan']['gem5Sha256'] for r in runs}) == 3, 'three distinct model variants required')
        for run in runs[1:]:
            for key in ('configSha256', 'trace', 'binaries', 'programManifestSha256'):
                require(run['plan'][key] == runs[0]['plan'][key], 'different execution plan ' + key)
            for key in ('result', 'stats', 'counts', 'configSha256', 'rawSha256'):
                require(run[key] == runs[0][key], 'actual behavior differs: ' + key)
        report['trace'] = runs[0]['plan']['trace']
        report['modelSha256'] = [r['plan']['gem5Sha256'] for r in runs]
        report['nonHostStatsCompared'] = len(runs[0]['stats'])
        report['cpuExitTick'] = runs[0]['result']['cpuExitTick']
        report['drainEndTick'] = runs[0]['result']['drainEndTick']
        report['legacyFiles'] = {}
        for file in CSV + TXT:
            hashes = [sha(d / file) for d in dirs]
            require(len(set(hashes)) == 1, 'legacy output bytes differ: ' + file)
            report['legacyFiles'][file] = hashes[0]
        report['cores'] = []
        for hart in range(4):
            left = validate_exports(dirs[1], hart)
            right = validate_exports(dirs[2], hart)
            require(left == right, f'core {hart}: complete records, order, archive or report bytes differ')
            report['cores'].append({'hart': hart, **left['manifest'],
                                    'perInstanceArtifactCount': len(left['artifacts'])})
        report['cpuRepeatedDumpTested'] = all(c['dumpId'] >= 1 for c in report['cores'])
        report['cpuEquivalencePass'] = True
    except FileNotFoundError as error:
        report['missing'].append(str(error))
    except (ValueError, KeyError, IndexError, TypeError, StopIteration, configparser.Error) as error:
        report['issues'].append(str(error))
    try:
        if helper_report is None:
            report['missing'].append('actual storage component report required')
        else:
            helper = read_json(helper_report)
            require(type(helper.get('schemaVersion')) is int and helper['schemaVersion'] == 1
                    and helper.get('status') == 'PASS', 'component test did not pass')
            require(helper.get('sourceSha256') == sha(PROJECT / 'native/gem5/BpuPhaseSpool.hh'),
                    'component tests used different storage helper source')
            require(all(helper.get('cases', {}).get(key) is True for key in COMPONENT_CASES),
                    'component tests missing replay/corruption/Int32 boundary cases')
            report['componentReportSha256'] = sha(helper_report)
            report['componentCases'] = helper['cases']
    except FileNotFoundError as error:
        report['missing'].append(str(error))
    except (ValueError, KeyError, TypeError) as error:
        report['issues'].append(str(error))
    report['status'] = 'FAIL' if report['issues'] else ('INCOMPLETE' if report['missing'] else 'PASS')
    report['pass'] = report['status'] == 'PASS' and report['cpuEquivalencePass']
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('legacy', 'oracle', 'candidate', 'program-manifest', 'output'):
        parser.add_argument('--' + key, type=Path, required=True)
    parser.add_argument('--helper-report', type=Path)
    args = parser.parse_args()
    result = check(args.legacy, args.oracle, args.candidate, args.program_manifest, args.helper_report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': result['status'], 'cpuEquivalencePass': result['cpuEquivalencePass'],
                      'issues': result['issues'], 'missing': result['missing']}))
    return 0 if result['pass'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
