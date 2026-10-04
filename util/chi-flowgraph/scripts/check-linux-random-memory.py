"""Check the independent real-Linux random migration workload's evidence.

Synthetic unit-test logs are not execution evidence. Passing these checks does
not establish gem5 provenance, CHI correctness, or all-interleaving correctness;
the caller must bind the actual run to its immutable firmware/configuration.
"""
import argparse
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

SUITE = 'native-linux-random-memory'
PARAMS = {'iterations': 128, 'seed': 1, 'pagesPerWriter': 64,
          'pressureBytesPerWriter': 1048576, 'pressureSamplesPerEpoch': 64,
          'timeoutSeconds': 120}
MASK32 = (1 << 32) - 1
MASK64 = (1 << 64) - 1
FNV_OFFSET = 14695981039346656037
FNV_PRIME = 1099511628211
KINDS = ('BEGIN', 'MIGRATION', 'PAGE', 'WRITER', 'END', 'ABORT')


def integer(value, minimum=0, maximum=MASK64):
    return type(value) is int and minimum <= value <= maximum


def strict_json(text):
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


def next32(state):
    state = (state ^ (state << 13)) & MASK32
    state ^= state >> 17
    return (state ^ (state << 5)) & MASK32


def selections(seed, iterations, pages):
    states = [(seed ^ ((0x9e3779b9 * writer) & MASK32)) or 0x6d2b79f5
              for writer in range(1, 4)]
    permutations = [[] for _ in range(3)]
    for rnd in range(iterations):
        for slot in range(3):
            if rnd % pages == 0:
                permutation = list(range(pages))
                for index in range(pages - 1, 0, -1):
                    states[slot] = next32(states[slot])
                    other = states[slot] % (index + 1)
                    permutation[index], permutation[other] = permutation[other], permutation[index]
                permutations[slot] = permutation
            yield rnd, slot, slot + 1, permutations[slot][rnd % pages]


def fnv_bytes(data, digest=FNV_OFFSET):
    for byte in data:
        digest = ((digest ^ byte) * FNV_PRIME) & MASK64
    return digest


def selection_digest(items):
    digest = FNV_OFFSET
    for item in items:
        for value in item:
            digest = fnv_bytes(value.to_bytes(4, 'little'), digest)
    return f'{digest:016x}'


@lru_cache(maxsize=4096)
def page_digest(seed, writer, page, epoch, size):
    constant = (((seed << 32) ^ (writer * 0x9e3779b97f4a7c15) ^
                 (page * 0xd1b54a32d192ed03) ^ (epoch * 0x94d049bb133111eb)) & MASK64)
    digest = FNV_OFFSET
    for offset in range(0, size, 8):
        word = constant ^ (((offset // 8) * 0xbf58476d1ce4e5b9) & MASK64)
        digest = fnv_bytes(word.to_bytes(8, 'little')[:min(8, size-offset)], digest)
    return f'{digest:016x}'


def check(log_text, manifest, expected_params=None):
    issues, incomplete = [], []
    artifact_checked = False
    def require(condition, detail):
        if not condition:
            issues.append(detail)
    def report():
        return {'pass': not issues and not incomplete,
                'status': 'FAIL' if issues else 'INCOMPLETE' if incomplete else 'PASS',
                'suite': SUITE, 'issues': issues, 'incomplete': incomplete,
                'binaryArtifactChecked': artifact_checked,
                'scope': 'Checks completed Linux workload records, deterministic selection, '
                         'observed placement changes, concurrent progress and final data. '
                         'Actual gem5/Linux run provenance is the caller\'s responsibility.'}
    if isinstance(manifest, (str, Path)):
        manifest_path = Path(manifest)
        try:
            manifest = strict_json(manifest_path.read_text(encoding='utf-8'))
            binary = manifest.get('binary')
            require(isinstance(binary, str) and bool(binary) and Path(binary).name == binary,
                    'manifest binary must be a filename relative to the manifest')
            if not issues:
                path = (manifest_path.parent / binary).resolve()
                require(path.parent == manifest_path.parent.resolve(), 'binary escapes manifest directory')
                require(hashlib.sha256(path.read_bytes()).hexdigest() == manifest.get('binarySha256'),
                        'binary artifact SHA256 differs from the manifest')
                artifact_checked = not issues
        except (OSError, ValueError, TypeError, AttributeError) as error:
            issues.append('cannot validate manifest/binary: ' + str(error))
            return report()
    require(isinstance(manifest, dict), 'manifest must be an object')
    if not isinstance(manifest, dict):
        return report()
    require(type(manifest.get('schemaVersion')) is int and manifest.get('schemaVersion') == 1
            and type(manifest.get('outputSchemaVersion')) is int
            and manifest.get('outputSchemaVersion') == 1 and manifest.get('suite') == SUITE,
            'wrong manifest identity')
    assembly = manifest.get('assemblyChecks')
    require(isinstance(assembly, dict) and all(assembly.get(key) is True for key in
            ('rv64Elf', 'linuxEcall', 'sourceTargetRv64gc', 'noAdvertisedVectorOrZbb')),
            'manifest has not verified the expected RV64 Linux build')
    requirements = manifest.get('requirements')
    required = {'threads': 4, 'writers': 3, 'writerIds': [1, 2, 3],
                'minimumIterations': 128, 'minimumPagesPerWriter': 64,
                'minimumMemoryNumaNodes': 2, 'movesPerRound': 3,
                'allSelectedMovesMustChangeNode': True, 'allPoolPagesMustBeMigrated': True,
                'minimumOverlappingAttempts': 1, 'minimumWindowWritesPerWriter': 1,
                'minimumRuntimeWritesPerWriter': 1, 'minimumPressureBytesPerWriter': 1048576,
                'minimumPressureSamplesPerEpoch': 64, 'minimumDistinctRuntimePressureLinesPerWriter': 64,
                'pressureLineBytes': 64, 'pressureMappingsPerWriter': 2,
                'fullPressureInitializationAndFinalByteCheck': True,
                'finalPoolCheckAfterAllWritersJoined': True, 'allByteErrorsMustBeZero': True,
                'guestTimeoutRequired': True, 'unsupportedIsPass': False}
    require(isinstance(requirements, dict) and all(
        type(requirements.get(key)) is type(value) and requirements.get(key) == value
        for key, value in required.items()), 'manifest requirements differ from this validation profile')
    source_sha = manifest.get('sourceSha256')
    require(isinstance(source_sha, str) and bool(re.fullmatch('[0-9a-f]{64}', source_sha)),
            'manifest source SHA256 is missing or malformed')
    require(isinstance(manifest.get('binarySha256'), str) and
            bool(re.fullmatch('[0-9a-f]{64}', manifest['binarySha256'])),
            'manifest binary SHA256 is missing or malformed')
    expected = dict(PARAMS)
    defaults = manifest.get('defaultParameters')
    require(isinstance(defaults, dict) and all(key in defaults for key in PARAMS),
            'manifest default parameters are incomplete')
    if isinstance(defaults, dict):
        expected.update({key: defaults[key] for key in PARAMS if key in defaults})
    if expected_params is not None:
        require(isinstance(expected_params, dict) and set(expected_params) <= set(PARAMS),
                'unknown expected parameter')
        if isinstance(expected_params, dict):
            expected.update(expected_params)
    bounds = {'iterations': (128, 100000), 'seed': (1, MASK32), 'pagesPerWriter': (64, 4096),
              'pressureBytesPerWriter': (1048576, 16777216),
              'pressureSamplesPerEpoch': (64, 16384), 'timeoutSeconds': (1, 86400)}
    for key, (low, high) in bounds.items():
        require(integer(expected.get(key), low, high), 'invalid expected ' + key)
    if issues:
        return report()
    require(expected['iterations'] >= expected['pagesPerWriter'], 'not enough rounds to cover every page')
    require(expected['pressureBytesPerWriter'] % 64 == 0, 'pressure region is not whole cache lines')
    records = {kind: [] for kind in KINDS}
    sequence = []
    for line in log_text.splitlines():
        if not line.startswith('RANDOM_MEMORY_'):
            continue
        prefix, separator, payload = line.partition(' ')
        kind = prefix.removeprefix('RANDOM_MEMORY_')
        if kind not in records or not separator:
            issues.append('unknown or malformed random-memory record prefix')
            continue
        try:
            value = strict_json(payload)
            if not isinstance(value, dict):
                raise ValueError('record is not an object')
            records[kind].append(value)
            sequence.append(kind)
        except (ValueError, TypeError):
            issues.append('malformed ' + kind + ' JSON')
    require(not records['ABORT'], 'workload aborted or timed out')
    if not records['BEGIN'] or not records['END']:
        incomplete.append('missing BEGIN or END; no complete execution evidence')
        return report()
    require(len(records['BEGIN']) == len(records['END']) == 1, 'expected exactly one BEGIN and END')
    begin, end = records['BEGIN'][0], records['END'][0]
    require(type(begin.get('schemaVersion')) is int and begin.get('schemaVersion') == 1
            and begin.get('suite') == SUITE,
            'wrong runtime suite identity')
    require(begin.get('buildId') == source_sha, 'runtime buildId differs from the frozen source')
    for key in PARAMS:
        require(type(begin.get(key)) is int and begin.get(key) == expected[key],
                'runtime parameter differs: ' + key)
    require(begin.get('prng') == 'xorshift32' and begin.get('selector') == 'per-writer-fisher-yates-v1',
            'wrong deterministic selection algorithm')
    if end.get('status') == 'UNSUPPORTED':
        incomplete.append('Linux reported unsupported migration/capability')
        return report()
    cpus = begin.get('affinityCpus')
    require(isinstance(cpus, list) and len(cpus) == 4 and all(integer(x, 0, 65535) for x in cpus)
            and len(set(cpus)) == 4, 'four distinct actual CPU affinities required')
    require(begin.get('threadsRequested') == end.get('threadsStarted') == 4,
            'four actual Linux threads required')
    require(end.get('threadCpuIds') == cpus and end.get('threadPinErrors') == [0]*4,
            'actual CPU placement or pinning failed')
    require(isinstance(cpus,list) and bool(cpus) and type(end.get('verifierCpu')) is int
            and end.get('verifierCpu') == cpus[0], 'final verifier did not execute on coordinator CPU')
    nodes = begin.get('numaMemoryNodes')
    require(isinstance(nodes, list) and len(nodes) >= 2 and all(integer(x, 0, 65535) for x in nodes)
            and len(set(nodes)) == len(nodes), 'at least two distinct memory NUMA nodes required')
    require(begin.get('pageBytes') == 4096, 'this fixed Linux profile requires 4096-byte pages')
    if issues:
        return report()
    iterations, pages, seed = expected['iterations'], expected['pagesPerWriter'], expected['seed']
    pressure = expected['pressureBytesPerWriter']
    expected_selection = list(selections(seed, iterations, pages))
    require(sequence == ['BEGIN'] + ['MIGRATION']*(iterations*3) +
            (['PAGE']*pages + ['WRITER'])*3 + ['END'],
            'missing, duplicate, extra or reordered workload records')
    require(len(records['MIGRATION']) == iterations*3, 'wrong actual migration record count')
    previous_after = [0]*3
    window_writes = [0]*3
    overlapping = 0
    distinct = [set() for _ in range(3)]
    for index, (item, selection) in enumerate(zip(records['MIGRATION'], expected_selection)):
        label = f'migration {index}: '
        actual = tuple(item.get(key) for key in ('round', 'slot', 'writer', 'page'))
        require(all(type(x) is int for x in actual) and actual == selection, label+'wrong seeded page selection')
        rnd, slot, writer, page = selection
        distinct[slot].add(page)
        before_node, target, after_node = (item.get(key) for key in ('beforeNode', 'targetNode', 'afterNode'))
        require(integer(before_node) and before_node in nodes, label+'invalid queried source node')
        expected_target = nodes[1] if before_node == nodes[0] else nodes[0]
        require(type(target) is int and target == expected_target and target != before_node,
                label+'target does not represent a cross-node move')
        require(type(after_node) is int and after_node == target, label+'post-query placement did not change')
        require(all(type(item.get(key)) is int and item.get(key) == 0 for key in
                    ('queryBeforeRc','moveRc','queryAfterRc','errorNumber')),
                label+'query/MOVE/query syscall error or partial move')
        require(type(item.get('pageStatus')) is int and item.get('pageStatus') == target,
                label+'MOVE per-page status does not match target')
        require(item.get('status') == 'PASS', label+'migration was not successful')
        a, b = item.get('completedBefore'), item.get('completedAfter')
        valid = (isinstance(a,list) and isinstance(b,list) and len(a)==len(b)==3 and
                 all(integer(x) for x in a+b))
        require(valid, label+'invalid per-writer completed-page snapshots')
        if valid:
            require(all(previous_after[j] <= a[j] <= b[j] for j in range(3)),
                    label+'writer epochs went backwards across migration windows')
            deltas = [b[j]-a[j] for j in range(3)]
            overlapping += sum(deltas)>0
            window_writes = [window_writes[j]+deltas[j] for j in range(3)]
            previous_after = b
    require(overlapping > 0 and all(value > 0 for value in window_writes),
            'each writer must make measured progress inside actual migration windows')
    require(end.get('overlapAttempts') == overlapping and end.get('writerWindowWrites') == window_writes,
            'footer concurrency totals differ from observed windows')
    require(end.get('migrationAttempts') == end.get('migrationsPassed') == iterations*3,
            'not every required query/MOVE/query migration completed')
    require(end.get('distinctMigratedPages') == [len(x) for x in distinct] == [pages]*3,
            'random selection did not cover the full per-writer page pools')
    require(end.get('selectionDigest') == selection_digest(expected_selection),
            'selection digest differs from the independently generated seeded sequence')
    page_rows = records['PAGE']
    require([(row.get('writer'),row.get('page')) for row in page_rows] ==
            [(writer,page) for writer in range(1,4) for page in range(pages)],
            'final versioned page records are missing, duplicate or reordered')
    epochs = [0]*3
    updated = [0]*3
    for row in page_rows:
        writer, page, epoch = row.get('writer'),row.get('page'),row.get('epoch')
        valid = integer(writer,1,3) and integer(page,0,pages-1) and integer(epoch,1)
        require(valid, 'invalid final page identity/version')
        require(row.get('bytes') == 4096 and type(row.get('bytes')) is int,
                'final page verification did not cover all bytes')
        require(type(row.get('byteErrors')) is int and row.get('byteErrors') == 0,
                'final page byte mismatch')
        if valid:
            epochs[writer-1] += epoch
            updated[writer-1] += epoch>1
            require(row.get('fnv64') == page_digest(seed,writer,page,epoch,4096),
                    f'writer {writer} page {page}: bytes/hash do not match final version')
    require([row.get('writer') for row in records['WRITER']] == [1,2,3],
            'missing, duplicate or reordered writer summaries')
    pressure_report = []
    for index, row in enumerate(records['WRITER'][:3]):
        label = f'writer {index+1}: '
        for key in ('pagesWritten','runtimePagesWritten','distinctRuntimePages','epochSum',
                    'pressureRuntimeWrites','pressureRuntimeAliasChecks','pressureDistinctRuntimeLines',
                    'migrationWindowWrites'):
            require(integer(row.get(key)), label+'invalid integer '+key)
        require(type(row.get('cpu')) is int and row.get('cpu') == cpus[index+1] and
                type(row.get('pinError')) is int and row.get('pinError') == 0,
                label+'actual affinity failed')
        runtime = row.get('runtimePagesWritten')
        require(integer(runtime,1) and row.get('pagesWritten') == row.get('epochSum') == epochs[index]
                and epochs[index] == pages + runtime, label+'page versions and completed writes differ')
        require(row.get('distinctRuntimePages') == updated[index] and updated[index] > 0,
                label+'runtime page coverage differs from final versions')
        require(integer(runtime,1) and runtime >= previous_after[index], label+'final epoch regressed')
        require(row.get('migrationWindowWrites') == window_writes[index], label+'window progress differs')
        for key in ('finalPageByteErrors','mainPageByteErrors','pressureByteErrors'):
            require(type(row.get(key)) is int and row.get(key) == 0, label+key+' is nonzero/invalid')
        for key in ('pressureAllocatedBytes','pressureInitializedBytes','pressureInitializationChecks','pressureFinalChecks'):
            require(type(row.get(key)) is int and row.get(key) == pressure, label+key+' is incomplete')
        writes, lines = row.get('pressureRuntimeWrites'),row.get('pressureDistinctRuntimeLines')
        require(integer(writes, expected['pressureSamplesPerEpoch']) and integer(runtime,1)
                and writes == runtime*expected['pressureSamplesPerEpoch'], label+'runtime pressure writes incomplete')
        require(integer(writes) and row.get('pressureRuntimeAliasChecks') == writes*64,
                label+'runtime alias byte checks incomplete')
        require(integer(lines,1,pressure//64) and integer(writes) and lines == min(writes,pressure//64),
                label+'pressure permutation coverage is inconsistent')
        if integer(lines):
            pressure_report.append({'writer':index+1, 'initializedBytes':pressure,
                                    'runtimeDistinctLines':lines, 'runtimeDistinctBytes':lines*64,
                                    'runtimeWorkingSetExceedsL2':lines*64>262144})
    require(end.get('status') == 'PASS' and type(end.get('exitCode')) is int and end.get('exitCode') == 0,
            'workload did not complete successfully')
    for key in ('migrationAttempts','migrationsPassed','overlapAttempts'):
        require(integer(end.get(key)), 'footer invalid integer '+key)
    for key in ('writerWindowWrites','distinctMigratedPages','threadPinErrors','threadCpuIds'):
        value=end.get(key)
        require(isinstance(value,list) and all(integer(x) for x in value), 'footer invalid integer array '+key)
    for key in ('totalPageByteErrors','totalPressureByteErrors','errors'):
        require(type(end.get(key)) is int and end.get(key) == 0, 'footer '+key+' is nonzero/invalid')
    result = report()
    result.update({'parameters':expected,'migrationRecords':len(records['MIGRATION']),
                   'distinctMigratedPages':[len(x) for x in distinct], 'overlapAttempts':overlapping,
                   'writerWindowWrites':window_writes,'finalPageRecords':len(page_rows),
                   'pressureWorkingSets':pressure_report})
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    for flag in ('iterations','seed','pages-per-writer','pressure-bytes','pressure-samples','timeout'):
        parser.add_argument('--'+flag,type=lambda text:int(text,0))
    args=parser.parse_args()
    names={'iterations':'iterations','seed':'seed','pages_per_writer':'pagesPerWriter',
           'pressure_bytes':'pressureBytesPerWriter','pressure_samples':'pressureSamplesPerEpoch',
           'timeout':'timeoutSeconds'}
    expected={target:getattr(args,source) for source,target in names.items() if getattr(args,source) is not None}
    try:
        result=check(args.input.read_text(encoding='utf-8',errors='replace'),args.manifest,expected)
    except (OSError,ValueError,TypeError,KeyError) as error:
        result={'pass':False,'status':'FAIL','issues':['checker input error: '+str(error)]}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2))
    return 0 if result['pass'] else 2 if result['status']=='INCOMPLETE' else 1


if __name__=='__main__':
    raise SystemExit(main())
