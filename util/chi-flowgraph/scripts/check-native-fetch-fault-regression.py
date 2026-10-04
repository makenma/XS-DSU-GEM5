"""Verify one frozen RTL-SE fetch-fault correction; never statistics equivalence.

This deliberately narrow profile explains a reviewed 22-key delta. It does not
learn acceptable deltas from its input, authorize Linux, or alter launch gates.
Both full-span sparse fault runs and a bounded detailed episode are necessary.
"""
import argparse
import configparser
import hashlib
import json
import re
import shlex
from pathlib import Path

HOST = {'hostInstRate', 'hostMemory', 'hostOpRate', 'hostSeconds', 'hostTickRate'}
PROJECT = '/mnt/d/proj/chi_flowgraph'
ROOT = '/root/chi-flowgraph-work/linux-validation'
MODELS = (
    (ROOT + '/artifacts/fs-model-09-fetch-boundary-candidate01/gem5.opt',
     '0e45dfb345200506be4940e0a940a0ada4bc82905f1f1ae22059f3a96f044597'),
    (ROOT + '/artifacts/fs-model-10-fetch-demand-fault-candidate02/gem5.opt',
     '7aba95946686c49c763e54d1876670e4e737c90ed843fb0a4905f400c326309a'))
PROGRAM_SHA = '881aa515e8d919447f73086903eb89d0b219a26ead464b5eed024547bd9b9ed3'
CONFIG_SHA = '2253ac152559e3bfc812ad6106d8e2c594af018907e5599a086011b1b92a4ec5'
INI_SHA = 'af4e329ac6c627598ba4e501a4fcd5bd84445c790baff6431e65eba4eda1796e'
FLOW_SHA = '5409d53f0da22f2f296d43f37f16b36a9a11544ead2e0b7943df1105102522be'
RAW_SHA = '4802ef3dcd5aa2cdc80b1796657f79bc1d4cfd3830a756d5573b72e2892aa902'
ELF_SHA = [
    '78a99915bd9f0158892c8be416b082b26284102962e1e590961ccbbd4762ea8c',
    '8993a4df07cd365f9fefa68e88b64fa6cc721b7e8f52cc399f6be03acde28914',
    'a66d837cdb771ba4097dfcffa456a18c63777a4b9debf5c387168801f9bf7547',
    '9c6d146bc61f5789a8cfda26a5f29a715e3c23b34cdede0fdfac9c58708fae2a']
P = 'system.cpu0.'
# Independently reviewed primitive baselines/deltas, not an input-derived allowlist.
PRIMITIVES = {
    'fetch.cycles': (19010, 1),
    'fetch.fetchStatusDist::Running': (19064, 1),
    'fetch.fetchStatusDist::TrapPending': (6, -1),
    'decode.decodedInsts': (73473, -2),
    'decode.runCycles': (12668, -1),
    'fetch.fetchBubbles': (10943, 1),
    'iew.decodeStallReason::NoStall': (114525, 4),
    'iew.decodeStallReason::FetchFragStall': (441, -4),
    'iew.fetchStallReason::OtherFetchStall': (10332, -6),
    'iew.fetchStallReason::ITlbStall': (0, 6),
    'iew.fetchStallReason::FetchFragStall': (518, 1),
    'iew.fetchStallReason::NoStall': (103438, -1)}
FORMULAS = ('fetch.frontendBound', 'fetch.frontendBandwidthBound', 'frontendBound',
            'frontendBandwidthBound', 'backendBound', 'coreBound', 'memoryBound', 'l1Bound')
CDF_ONLY = ('fetch.fetchStatusDist::Squashing', 'fetch.fetchStatusDist::Blocked')
EXPECTED_CHANGED = {P + k for k in (*PRIMITIVES, *FORMULAS, *CDF_ONLY)}
STATUS = 'Running Idle Squashing Blocked TrapPending WaitingCache'.split()
STALL = ('NoStall IcacheStall ITlbStall DTlbStall BpStall IntStall TrapStall FTQBubble '
         'FetchFragStall OtherFetchStall OtherFragStall SquashStall FetchBufferInvalid '
         'InstMisPred InstSquashed SerializeStall ScalarLongExecute VectorLongExecute '
         'InstNotReady LoadL1Bound LoadL2Bound LoadL3Bound LoadMemBound StoreL1Bound '
         'StoreL2Bound StoreL3Bound StoreMemBound MemSquashed MemNotReady MemCommitRateLimit '
         'Atomic OtherMemStall MemDQBandwidth IntDQBandwidth FVDQBandwidth '
         'VectorReadyButNotIssued ScalarReadyButNotIssued ResumeUnblock CommitSquash '
         'ROBFull RegFull OtherStall').split()
PC = '0xfffffffffffcc662'
NEXT = '0xfffffffffffcc666'
SECOND = '0xfffffffffffcc680'
DEBUG_ARGS = ['--debug-flags=FetchFault,Fetch,Decode,Commit,DecoupleBP',
              '--debug-start=21335000', '--debug-end=21365000',
              '--debug-file=fetch-fault-debug.log']
SPARSE_ARGS = ['--debug-flags=FetchFault', '--debug-file=fetch-fault-debug.log']


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read_json(path):
    def unique(pairs):
        value = {}
        for k, v in pairs:
            require(k not in value, 'duplicate JSON key: ' + k)
            value[k] = v
        return value
    return json.loads(Path(path).read_text(), object_pairs_hook=unique)


def statistics(path):
    blocks, current = [], None
    for line in Path(path).read_text().splitlines():
        if 'Begin Simulation Statistics' in line:
            require(current is None, 'nested statistics')
            current = {}
        elif 'End Simulation Statistics' in line:
            require(current is not None, 'unmatched statistics end')
            blocks.append(current)
            current = None
        elif current is not None:
            words = line.split('#', 1)[0].split()
            if words and words[0] not in HOST:
                require(len(words) >= 2 and words[0] not in current, 'duplicate/malformed statistic')
                current[words[0]] = words[1:]
    require(current is None and len(blocks) == 1, 'profile requires exactly one complete statistics block')
    require(len(blocks[0]) == 8936, 'RTL-SE profile requires all 8936 non-host statistics')
    return blocks[0]


def integer(value):
    require(isinstance(value, str) and re.fullmatch(r'0|[1-9][0-9]*', value),
            'non-canonical nonnegative integer: ' + repr(value))
    return int(value)


def count(stats, key):
    return integer(stats[P + key][0])


def verify_distribution(stats, prefix, names):
    keys = [P + prefix + '::' + name for name in names]
    actual = [k for k in stats if k.startswith(P + prefix + '::') and not k.endswith('::total')]
    require(actual == keys, 'distribution members/order differ: ' + prefix)
    values = [integer(stats[k][0]) for k in keys]
    total = sum(values)
    require(total > 0 and stats[P + prefix + '::total'] == [str(total)], 'distribution denominator: ' + prefix)
    cumulative = 0.0
    for key, value in zip(keys, values):
        pdf = value / total
        cumulative += pdf
        require(stats[key] == [str(value), f'{pdf * 100:.2f}%', f'{cumulative * 100:.2f}%'],
                'distribution rendering: ' + key)
    return total


def derived_values(stats, width):
    # gem5 cpu.cc sets issueWidth=params.decodeWidth. Never use fetchWidth (16).
    cycles = count(stats, 'numCycles')
    require(width == 6 and cycles == 21561, 'frozen RTL cycle/width denominator differs')
    front = count(stats, 'fetch.fetchBubbles') / (cycles * width)
    latency = count(stats, 'fetch.fetchBubbles_max') / cycles
    dispatched = count(stats, 'iew.dispatchedInsts')
    committed = count(stats, 'committedInsts')
    recovery = count(stats, 'commit.recovery_bubble')
    bad_spec = (dispatched - committed + recovery) / (cycles * width)
    retiring = committed / (cycles * width)
    backend = 1 - (front + bad_spec + retiring)
    load = count(stats, 'scheduler.memstall_any_load')
    store = count(stats, 'scheduler.memstall_any_store')
    total = count(stats, 'scheduler.exec_stall_cycle')
    l1 = count(stats, 'scheduler.memstall_l1miss')
    l2 = count(stats, 'scheduler.memstall_l2miss')
    l3 = count(stats, 'scheduler.memstall_l3miss')
    require(total > 0 and load + store > 0, 'zero performance denominator')
    memory = backend * (load + store) / total
    # Keep the actual source expression; do not round any intermediate ratio.
    memory_total = (load - l1) + (l1 - l2) + (l2 - l3) + l3 + store
    return dict(zip(FORMULAS, (front, front - latency, front, front - latency,
                              backend, backend * (total - load - store) / total,
                              memory, memory * (load - l1) / memory_total)))


def verify_statistics(before, after, width=6):
    require(before.keys() == after.keys(), 'statistic inventory differs')
    changed = {k for k in before if before[k] != after[k]}
    require(changed == EXPECTED_CHANGED, 'changed-key inventory is not the reviewed 22 keys: ' +
            repr(sorted(changed ^ EXPECTED_CHANGED)))
    for key, (base, delta) in PRIMITIVES.items():
        require(count(before, key) == base and count(after, key) == base + delta,
                'primitive baseline/delta: ' + key)
    for stats in (before, after):
        totals = [verify_distribution(stats, 'fetch.fetchStatusDist', STATUS),
                  verify_distribution(stats, 'iew.fetchStallReason', STALL),
                  verify_distribution(stats, 'iew.decodeStallReason', STALL)]
        require(totals == [21561, 129336, 129354], 'distribution totals outside frozen profile')
        for key, value in derived_values(stats, width).items():
            require(stats[P + key] == [f'{value:.6f}'], 'derived formula mismatch: ' + key)
    for key in CDF_ONLY:
        require(before[P + key][:2] == after[P + key][:2], 'CDF-only count/PDF changed')
    return {k: [before[k], after[k]] for k in sorted(changed)}


def fault_records(text):
    req = re.findall(r'(?m)^(\d+): (system\.cpu\d+\.fetch): fault, mem_req.addr=(0x[0-9a-f]+)$', text)
    dem = re.findall(r'(?m)^(\d+): (system\.cpu\d+\.fetch): \[tid:0\] Demand fetch fault: '
                     r'pc=(0x[0-9a-f]+) missing=(0x[0-9a-f]+) request=(0x[0-9a-f]+) cause=(\d+)$', text)
    # Reject malformed additional fault lines instead of silently skipping them.
    require(sum('fault, mem_req.addr=' in l for l in text.splitlines()) == len(req), 'unparsed translation fault')
    require(sum('Demand fetch fault:' in l for l in text.splitlines()) == len(dem), 'unparsed demanded fault')
    return req, dem


def verify_fault_records(text, new):
    req, dem = fault_records(text)
    require(req == [('21344000', 'system.cpu0.fetch', PC),
                    ('21344000', 'system.cpu0.fetch', SECOND)], 'unexpected full fault request census')
    expected = [('21345000', 'system.cpu0.fetch', PC, PC, PC, '0')] if new else []
    require(dem == expected, 'unexpected demanded fault census (SE cause=0 is not RISC-V cause=12)')
    return {'requests': req, 'demands': dem}


def verify_episode(text, new):
    records = verify_fault_records(text, new)
    lines = text.splitlines()
    evidence = []

    def exact(fragment, tick):
        matches = [(i + 1, l) for i, l in enumerate(lines) if fragment in l]
        require(len(matches) == 1 and matches[0][1].startswith(str(tick) + ': '),
                'missing/duplicate/wrong tick episode evidence: ' + fragment)
        evidence.append({'line': matches[0][0], 'text': matches[0][1]})

    prefix = f'system.cpu0.fetch: [tid:0] Instruction PC ({PC}=>{NEXT}).(0=>1) created [sn:'
    carriers = re.findall(re.escape(prefix) + r'(\d+)\]\.', text)
    expected = ['81808'] if new else ['81808', '81809']
    require(carriers == expected, 'wrong-path carrier cardinality or sequence differs')
    tick = 21345000 if new else 21344000
    for sn in expected:
        exact(prefix + sn + '].', tick)
        exact(f'system.cpu0.fetch: Set instruction {sn} with fetch id 6609', tick)
        exact(f'system.cpu0.fetch: [tid:0] [sn:{sn}] Sending instruction to decode ', tick)
    decoded = re.findall(r'system\.cpu0\.decode: \[tid:0\] Processing instruction \[sn:(\d+)\] with PC \('
                         + re.escape(PC) + r'=>', text)
    require(decoded == ([] if new else expected), 'old/new Decode carrier evidence differs')
    if not new:
        for sn in expected:
            exact(f'system.cpu0.decode: [tid:0] Processing instruction [sn:{sn}] with PC ({PC}=>', 21347000)
    exact('system.cpu0.branchPred: Control squash: ftq_id=6608, control_pc=0x105c8, '
          'real_target=' + PC + ', is_conditional=0, is_indirect=0, actually_taken=1, branch seq: 81773', 21343000)
    exact('system.cpu0.commit: [tid:0] Squashing due to branch mispred PC:0x105b0 [sn:81764]', 21347000)
    exact('system.cpu0.commit: [tid:0] Redirecting to PC (0x10000=>0x10004).(0=>1)', 21347000)
    exact('system.cpu0.decode: [tid:0] Squashing instructions due to squash from commit.', 21348000)
    exact('system.cpu0.fetch: [tid:0] Squash caused by inst at PC: (0x105b0=>0x10000).(0=>1), seqNum: 81764', 21350000)
    exact('system.cpu0.branchPred: Control squash: ftq_id=6608, control_pc=0x105b0, '
          'real_target=0x10000, is_conditional=0, is_indirect=1, actually_taken=1, branch seq: 81764', 21350000)
    require(81764 < 81773 < min(map(int, expected)), 'recovering branch must be older')
    for l in lines:
        if 'system.cpu0.commit:' in l and (PC in l or any('[sn:' + sn + ']' in l for sn in expected)):
            require('Committing' not in l and 'fault' not in l.lower(), 'wrong-path carrier committed/faulted')
    return {**records, 'carriers': expected, 'decodedCarriers': decoded,
            'recoveringBranchSequence': 81764, 'carrierFtq': 6609, 'recoveringFtq': 6608,
            'evidence': evidence}


def command(folder, model, debug=None):
    return [MODELS[model][0], '--outdir=' + str(folder), *(debug or []),
            PROJECT + '/native/gem5/native_l2_run.py', '--elf-dir',
            PROJECT + '/build/native-rtl-program', '--seed', '1',
            '--max-ticks', '2000000000', '--tail-ticks', '1000000']


def verify_plan(plan, folder, model, debug=None):
    require(plan['gem5Sha256'] == MODELS[model][1] and plan['programManifestSha256'] == PROGRAM_SHA
            and plan['configSha256'] == CONFIG_SHA and plan['trace'] == 'on', 'frozen run identity mismatch')
    require(plan['command'] == command(folder, model, debug), 'actual run plan/limits/debug scope mismatch')
    rows = [{'hart': i, 'file': f'native-l2-{i}.elf', 'sha256': sha} for i, sha in enumerate(ELF_SHA)]
    require(plan['binaries'] == rows, 'four-hart ELF identity mismatch')


def verify_cpu(result, log):
    require(result.get('pass') is True and type(result.get('exitCode')) is int and result['exitCode'] == 0
            and result.get('cause') == 'exiting with last active thread context'
            and result.get('protocolOpcodeInjection') is False, 'CPU result failed')
    require(result.get('cpuExitTick') == '21699000' and result.get('drainEndTick') == '22699000', 'CPU/drain ticks differ')
    harts = re.findall(r'(?m)^NATIVE_RTL_HART_([0-3])_PASS$', log)
    require(sorted(harts) == list('0123') and not re.search(r'(?m)^NATIVE_RTL_.*FAIL', log), 'four actual hart PASS markers missing')
    footers = re.findall(r'(?m)^NATIVE_L2_RESULT (.+)$', log)
    require(len(footers) == 1 and json.loads(footers[0]) == result, 'actual stdout result differs from saved result')


def verify_run(folder, model, debug=None):
    folder = Path(folder).resolve()
    verify_plan(read_json(folder / 'execution-plan.json'), folder, model, debug)
    require((folder / 'host-exit-code.txt').read_text().strip() == '0', 'actual host exit is not zero')
    result = read_json(folder / 'native-l2-result.json')
    log = (folder / 'run.log').read_text()
    actual_commands = [shlex.split(line.removeprefix('command line: ')) for line in log.splitlines()
                       if line.startswith('command line: ')]
    require(actual_commands == [command(folder, model, debug)], 'actual gem5 command differs from plan')
    verify_cpu(result, log)
    hashes = {name: digest(folder / name) for name in ('config.ini', 'flowgraph-config.json', 'raw.jsonl', 'counts.json')}
    require(hashes['config.ini'] == INI_SHA and hashes['flowgraph-config.json'] == FLOW_SHA
            and hashes['raw.jsonl'] == RAW_SHA, 'frozen topology/configuration/CHI bytes mismatch')
    config = configparser.ConfigParser(interpolation=None)
    config.read(folder / 'config.ini')
    for i in range(4):
        require(config[f'system.cpu{i}.workload']['cwd'] == PROJECT, 'original Process.cwd changed')
        require(config[f'system.cpu{i}']['decodeWidth'] == '6', 'CPU issue/decode width changed')
    return {'folder': str(folder), 'planSha256': digest(folder / 'execution-plan.json'),
            'result': result, 'hashes': hashes, 'stats': statistics(folder / 'stats.txt')}


def compare_run(a, b):
    require(a['result'] == b['result'] and a['hashes'] == b['hashes'] and a['stats'] == b['stats'],
            'diagnostic run differs from its same-binary ordinary run')


def verify_strict_report(path, before, after, differences):
    report = read_json(path)
    expected_checks = {'actualCpuSuccess': True, 'sameResult': True,
                       'sameSimulationStatistics': False, 'config.ini': True,
                       'flowgraph-config.json': True, 'raw.jsonl': True}
    require(report.get('passed') is False and report.get('checks') == expected_checks, 'original strict FAIL not preserved')
    require(report.get('statisticDifferences') == [differences], 'strict report does not describe these actual statistics')
    require(report.get('statisticCounts') == [[8936], [8936]], 'strict report inventory mismatch')
    require(report.get('cpuExitTicks') == ['21699000'] * 2 and report.get('drainEndTicks') == ['22699000'] * 2,
            'strict report tick mismatch')
    expected_hashes = {k: [before['hashes'][k], after['hashes'][k]]
                       for k in ('config.ini', 'flowgraph-config.json', 'raw.jsonl')}
    require(report.get('fileSha256') == expected_hashes and report.get('ignoredHostStatistics') == sorted(HOST),
            'strict report bytes/ignored statistics mismatch')


def check(args):
    # Hash real immutable binaries once each, never trust their filenames alone.
    for path, sha in MODELS:
        require(digest(path) == sha, 'immutable binary SHA mismatch: ' + path)
    program = Path(PROJECT) / 'build/native-rtl-program'
    require(digest(program / 'manifest.json') == PROGRAM_SHA, 'frozen RTL manifest changed')
    for i, sha in enumerate(ELF_SHA):
        require(digest(program / f'native-l2-{i}.elf') == sha, 'actual ELF changed')
    require(digest(Path(PROJECT) / 'native/gem5/native_l2_run.py') == CONFIG_SHA, 'frozen SE config source changed')
    normals = [verify_run(args.before, 0), verify_run(args.after, 1)]
    require(normals[0]['result'] == normals[1]['result'] and normals[0]['hashes'] == normals[1]['hashes'],
            'architectural result/config/CHI observation differs')
    differences = verify_statistics(normals[0]['stats'], normals[1]['stats'])
    verify_strict_report(args.strict_report, *normals, differences)
    # Preserve, rather than reinterpret, the original historical comparison chain.
    history = []
    for path, expected, passed in (
            (args.prior_equivalence, '8a4e0c64a4435e58185d14cc64906cf8a868016f5bac73229488515ccb9cb226', True),
            (args.historical_strict_report, '0c39d5acb5e79af922086ec31490d1304a58cd2989bd69a30817e66a6eb88614', False)):
        require(digest(path) == expected and read_json(path).get('passed') is passed, 'historical evidence changed')
        history.append({'path': str(path), 'sha256': expected, 'passed': passed})
    episodes, sparsities = [], []
    for index, (detailed, sparse) in enumerate(zip((args.before_diagnostic, args.after_diagnostic),
                                                 (args.before_sparse, args.after_sparse))):
        diagnostic = verify_run(detailed, index, DEBUG_ARGS)
        compare_run(normals[index], diagnostic)
        episode_path = Path(detailed) / 'fetch-fault-debug.log'
        episodes.append({'path': str(episode_path), 'sha256': digest(episode_path),
                         **verify_episode(episode_path.read_text(), bool(index))})
        complete = verify_run(sparse, index, SPARSE_ARGS)
        compare_run(normals[index], complete)
        sparse_path = Path(sparse) / 'fetch-fault-debug.log'
        sparse_text = sparse_path.read_text()
        require(len([line for line in sparse_text.splitlines() if line.strip()]) == 2 + index,
                'unrecognized/extra full-span FetchFault log record')
        sparsities.append({'path': str(sparse_path), 'sha256': digest(sparse_path),
                           **verify_fault_records(sparse_text, bool(index))})
    return {'passed': True, 'verdict': 'corrected-fault-behavior-verified',
            'legacyStrictPassed': False, 'statisticsEquivalent': False,
            'linuxAcceptance': False, 'launchAuthorized': False,
            'profile': 'rtl-se-model09-to-model10-candidate02-trace-on-v1',
            'strictReport': {'path': str(args.strict_report), 'sha256': digest(args.strict_report)},
            'preservedHistoricalReports': history,
            'normalRuns': [{k: v for k, v in run.items() if k != 'stats'} for run in normals],
            'primitiveDeltaCount': 12, 'cdfOnlyChangedKeys': 2, 'derivedFormulaCount': 8,
            'statisticCountPerRun': 8936, 'statisticDifferences': differences,
            'boundedEpisodes': episodes, 'completeFaultCensus': sparsities,
            'formulaSource': 'fetch.cc:254-265; cpu.cc:131,486-529; base/stats/text.cc:269-292,338-383',
            'scope': 'One frozen four-hart RTL-SE trace-on workload. All other non-host statistics, '
                     'CPU results/ticks and complete CHI bytes agree. Does not authorize BPU, FS, '
                     'trace-off semantic coverage or Linux acceptance; historical strict FAIL remains.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('before', 'after', 'strict-report', 'before-diagnostic', 'after-diagnostic',
                 'before-sparse', 'after-sparse', 'prior-equivalence', 'historical-strict-report', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    args = p.parse_args()
    require(not args.output.exists(), 'refusing to overwrite an evidence report')
    try:
        report = check(args)
    except (ValueError, KeyError, OSError, TypeError, configparser.Error) as error:
        report = {'passed': False, 'verdict': 'FAIL', 'legacyStrictPassed': False,
                  'statisticsEquivalent': False, 'linuxAcceptance': False, 'launchAuthorized': False,
                  'error': str(error)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ('passed', 'verdict', 'legacyStrictPassed', 'linuxAcceptance')}, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
