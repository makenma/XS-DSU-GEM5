"""Bind independently checked random-memory records to native-CHI Linux.

Missing/unfinished evidence never passes. Bundle checkers execute only after
the entire fixed inventory is verified, using the verified source bytes.
"""
import argparse
import configparser
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shlex
import types

HERE = Path(__file__).resolve().parent
PARAMETERS = {'iterations': 128, 'seed': 1, 'pagesPerWriter': 64,
              'pressureBytesPerWriter': 1048576, 'pressureSamplesPerEpoch': 64,
              'timeoutSeconds': 120}
BUNDLE_FILES = frozenset({
    'random/manifest.original.json', 'random/manifest.json', 'random/random-memory', 'random/source.c',
    'smoke/manifest.json', 'smoke/run.exe', 'checkers/check-linux-litmus.py',
    'checkers/check-linux-random-memory.py', 'guest/init', 'guest/busybox',
    'guest/guest-control', 'kernel.config',
})
DONE = 'CHI_LINUX_STAGE_DONE=random-memory-complete'


def module(path):
    spec = importlib.util.spec_from_file_location('check_' + Path(path).stem.replace('-', '_'), path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def sha_string(value):
    return isinstance(value, str) and bool(re.fullmatch('[0-9a-f]{64}', value))


def tick_string(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[0-9]+', value))


def read_json(audit, base, path):
    text = audit.read(path)
    if text is None:
        return None
    try:
        value = base.unique_json(text)
    except json.JSONDecodeError as error:
        unfinished = error.pos >= len(text.rstrip()) or error.msg.startswith('Unterminated string')
        audit.require(False, f'invalid or partial JSON {path}: {error}', pending=unfinished)
        return None
    except (ValueError, TypeError) as error:
        audit.require(False, f'invalid JSON {path}: {error}')
        return None
    if not audit.require(isinstance(value, dict), 'JSON root must be an object: ' + str(path)):
        return None
    return value


def verified_module(audit, path, expected_sha):
    """No path re-import or stale pyc after hashing a different source."""
    text = audit.read(path)
    info = audit.inputs.get(str(path.resolve()), {})
    if text is None or info.get('sha256') != expected_sha or not info.get('stableDuringRead'):
        audit.require(False, 'checker changed after bundle verification: ' + str(path),
                      pending=text is None or not info.get('stableDuringRead'))
        return None
    loaded = types.ModuleType('verified_' + path.stem.replace('-', '_'))
    loaded.__file__ = str(path)
    exec(compile(text, str(path), 'exec'), loaded.__dict__)
    return loaded


def verify_bundle(audit, base, plan):
    start = len(audit.failures), len(audit.incomplete)
    index_name = plan.get('inputIndex')
    if not audit.require(isinstance(index_name, str) and bool(index_name)
                         and Path(index_name).is_absolute(), 'inputIndex must be an absolute path'):
        return None
    index_path = Path(index_name).resolve()
    index = read_json(audit, base, index_path)
    if index is None:
        return None
    valid = audit.require(type(index.get('schemaVersion')) is int and index['schemaVersion'] == 1,
                          'unsupported frozen bundle schema')
    params = index.get('parameters')
    valid &= audit.require(isinstance(params, dict) and set(params) == set(PARAMETERS)
                           and all(type(params[k]) is int and params[k] == v for k, v in PARAMETERS.items()),
                           'frozen parameters differ from the fixed guest command')
    valid &= audit.require(sha_string(index.get('coreInputSha256')), 'missing frozen core input identity')
    entries = index.get('files')
    valid &= audit.require(isinstance(entries, list), 'frozen files must be an array')
    inventory = {}
    if isinstance(entries, list):
        for entry in entries:
            if not audit.require(isinstance(entry, dict) and set(entry) == {'path', 'sha256'}, 'invalid frozen file record'):
                valid = False
                continue
            name = entry['path']
            if not audit.require(isinstance(name, str) and name in BUNDLE_FILES and name not in inventory
                                 and sha_string(entry['sha256']), 'unknown, duplicate or malformed frozen file record'):
                valid = False
                continue
            inventory[name] = entry['sha256']
    valid &= audit.require(set(inventory) == BUNDLE_FILES and isinstance(entries, list)
                           and len(entries) == len(BUNDLE_FILES), 'frozen bundle lacks the complete fixed file inventory')
    if not valid:
        return None
    identity = {key: index[key] for key in ('schemaVersion', 'parameters', 'coreInputSha256', 'files')}
    identity_sha = hashlib.sha256((json.dumps(identity, sort_keys=True, indent=2) + '\n').encode()).hexdigest()
    valid &= audit.require(sha_string(plan.get('inputSha256')) and identity_sha == index.get('inputSha256')
                           == plan['inputSha256'], 'frozen input identity differs')
    root = index_path.parent
    for name, expected in inventory.items():
        path = (root / name).resolve()
        if not audit.require(path.is_relative_to(root), 'input path escapes frozen bundle: ' + name):
            valid = False
            continue
        actual = audit.digest(path)
        if actual is None:
            valid = False  # Missing evidence is INCOMPLETE, not a digest mismatch.
        elif not audit.require(actual == expected, 'frozen input changed: ' + name):
            valid = False
    if not valid or start != (len(audit.failures), len(audit.incomplete)):
        return None
    random_manifest = read_json(audit, base, root / 'random/manifest.json')
    original = read_json(audit, base, root / 'random/manifest.original.json')
    smoke_manifest = read_json(audit, base, root / 'smoke/manifest.json')
    if any(item is None for item in (random_manifest, original, smoke_manifest)):
        return None
    audit.require(random_manifest == dict(original, binary='random-memory'),
                  'normalized manifest differs beyond its binary basename')
    audit.require(random_manifest.get('binary') == 'random-memory'
                  and random_manifest.get('binarySha256') == inventory['random/random-memory']
                  and random_manifest.get('sourceSha256') == inventory['random/source.c'],
                  'random source/binary differs from its manifest')
    audit.require(smoke_manifest.get('binarySha256') == inventory['smoke/run.exe'], 'smoke binary differs from its manifest')
    if start != (len(audit.failures), len(audit.incomplete)):
        return None
    return {'root': root, 'index': index, 'inventory': inventory, 'smokeManifest': smoke_manifest}


def verify_plan(audit, base, directory, plan, result, host_log):
    audit.require(type(plan.get('schemaVersion')) is int and plan['schemaVersion'] == 1
                  and plan.get('kind') == 'native-linux-random-memory', 'wrong execution plan schema/kind')
    for name in ('gem5', 'firmware', 'config', 'buildResult'):
        value, expected = plan.get(name), plan.get(name + 'Sha256')
        if not audit.require(isinstance(value, str) and bool(value) and Path(value).is_absolute()
                             and sha_string(expected), 'missing/invalid executed input: ' + name):
            continue
        actual = audit.digest(value)
        if actual is not None:
            audit.require(actual == expected, 'executed input changed: ' + name)
    if result is not None:
        for name in ('gem5', 'firmware'):
            audit.require(sha_string(result.get(name + 'Sha256'))
                          and result[name + 'Sha256'] == plan.get(name + 'Sha256'),
                          'result input identity differs: ' + name)
    command = plan.get('command')
    if audit.require(isinstance(command, list) and bool(command) and all(isinstance(x, str) for x in command),
                     'execution plan has no command array'):
        required = [plan.get('gem5'), '--outdir=' + str(directory), plan.get('config'), '--kernel', plan.get('firmware')]
        audit.require(command[:5] == required, 'planned command does not identify this model/config/firmware/run')
        recorded = [line.removeprefix('command line: ') for line in (host_log or '').splitlines()
                    if line.startswith('command line: ')]
        if audit.require(len(recorded) == 1, 'missing/duplicate actual command line', pending=not recorded):
            try:
                audit.require(shlex.split(recorded[0]) == command, 'actual command differs from execution plan')
            except ValueError as error:
                audit.require(False, 'invalid actual command line: ' + str(error))
    name = plan.get('buildResult')
    if isinstance(name, str) and name:
        build = read_json(audit, base, name)
        if build is not None:
            audit.require(build.get('built') is True and build.get('guestExecuted') is False, 'firmware build evidence missing')
            for key in ('firmware', 'firmwareSha256', 'inputIndex', 'inputSha256'):
                audit.require(build.get(key) == plan.get(key), 'build/launch identity differs: ' + key)


def serial_slices(audit, base, serial):
    lines = [line.strip() for line in serial.replace('\r', '').splitlines()]
    start = len(audit.failures), len(audit.incomplete)
    positions = {}
    for name, prefix in [('smokeBegin', 'LINUX_LITMUS_BEGIN '), ('smokeEnd', 'LINUX_LITMUS_END '),
                         ('wrapperBegin', 'CHI_RANDOM_MEMORY_RUN_BEGIN '), ('wrapperEnd', 'CHI_RANDOM_MEMORY_RUN_END '),
                         ('programBegin', 'RANDOM_MEMORY_BEGIN '), ('programEnd', 'RANDOM_MEMORY_END ')]:
        found = [i for i, line in enumerate(lines) if line.startswith(prefix)]
        if audit.require(len(found) == 1, 'missing/duplicate ' + name, pending=not found):
            positions[name] = found[0]
    finals = [i for i, line in enumerate(lines) if line.startswith('CHI_LINUX_STAGE_DONE=')]
    if audit.require(len(finals) == 1 and lines[finals[0]] == DONE,
                     'missing/duplicate/wrong final stage marker', pending=not finals):
        positions['done'] = finals[0]
    boot_positions = []
    for marker in ('CHI_LINUX_ONLINE_CPUS=0-3', 'CHI_LINUX_ONLINE_NODES=0-1', 'CHI_LINUX_BOOT_PASS'):
        found = [i for i, line in enumerate(lines) if line == marker]
        if audit.require(len(found) == 1, 'missing/duplicate marker: ' + marker, pending=not found):
            boot_positions.append(found[0])
    if len(positions) != 7 or len(boot_positions) != 3:
        return None
    order = [positions[key] for key in ('smokeBegin', 'smokeEnd', 'wrapperBegin', 'programBegin',
                                        'programEnd', 'wrapperEnd', 'done')]
    audit.require(all(a < b for a, b in zip(order, order[1:])) and max(boot_positions) < order[0],
                  'boot, successful smoke, wrapper, program and final marker must occur in order')
    a, b = positions['wrapperBegin'], positions['wrapperEnd']
    audit.require(all(a < i < b for i, line in enumerate(lines) if line.startswith('RANDOM_MEMORY_')),
                  'random workload record appears outside its wrapper')
    wrappers = {}
    for key in ('smokeEnd', 'wrapperBegin', 'wrapperEnd'):
        payload = lines[positions[key]].split(' ', 1)[1]
        try:
            value = base.unique_json(payload)
            if not isinstance(value, dict):
                raise ValueError('wrapper must be an object')
            wrappers[key] = value
        except json.JSONDecodeError as error:
            audit.require(False, 'invalid wrapper JSON: ' + str(error), pending=error.pos >= len(payload.rstrip()))
        except (ValueError, TypeError) as error:
            audit.require(False, 'invalid wrapper JSON: ' + str(error))
    for key in ('smokeEnd', 'wrapperEnd'):
        if key in wrappers:
            audit.require(type(wrappers[key].get('exitCode')) is int and wrappers[key]['exitCode'] == 0,
                          key + ' exited nonzero or omitted a valid integer exit code')
    if start != (len(audit.failures), len(audit.incomplete)):
        return None
    return {'smoke': '\n'.join(lines[positions['smokeBegin']:positions['smokeEnd'] + 1]) + '\n',
            'random': '\n'.join(lines[a + 1:b]) + '\n', 'wrapper': wrappers['wrapperBegin']}


def checker_success(audit, report, label):
    if not isinstance(report, dict):
        audit.require(False, label + ': checker returned non-object')
        return False
    return audit.success(report, label)


def check(directory):
    directory = Path(directory).resolve()
    base = module(HERE / 'check-native-linux-validation.py')
    audit = base.Audit()
    checks, result = {}, None
    try:
        audit.read(Path(__file__))
        plan = read_json(audit, base, directory / 'execution-plan.json')
        result = read_json(audit, base, directory / 'native-linux-result.json')
        config = read_json(audit, base, directory / 'flowgraph-config.json')
        serial = audit.read(directory / 'system.platform.terminal')
        host_log = audit.read(directory / 'run.log')
        exit_text = audit.read(directory / 'host-exit-code.txt')
        failures = [line for line in (host_log or '').splitlines()
                    if re.search(r'Assertion .+ failed\.$|^Program aborted at tick [0-9]+$|'
                                 r'^(?:[^\n]*: )?(?:panic|fatal): |^terminate called ', line)]
        audit.require(not failures, 'host failure: ' + '; '.join(failures))
        if serial and re.search(r'CHI_RANDOM_MEMORY_RUN_FAIL|Kernel panic -|Oops:|RANDOM_MEMORY_ABORT ', serial):
            audit.require(False, 'guest reported workload failure, abort or kernel fault')
        if exit_text is not None:
            audit.require(exit_text.strip() == '0', 'host process did not exit zero', pending=not exit_text.strip())
        checks['topology'] = base.topology(audit, directory, config, 'random-memory')
        if result is not None:
            audit.require(result.get('fullSystem') is True and result.get('protocolOpcodeInjection') is False,
                          'actual full-system execution required')
            audit.require(type(result.get('configuredHarts')) is int and result['configuredHarts'] == 4
                          and type(result.get('numaNodes')) is int and result['numaNodes'] == 2,
                          'four harts and two NUMA nodes required')
            normal = type(result.get('exitCode')) is int and result['exitCode'] == 0 and isinstance(result.get('cause'), str) \
                and result['cause'].startswith('m5_exit instruction encountered')
            audit.require(normal, 'normal successful guest exit required', pending=result.get('cause') == 'simulate() limit reached')
            audit.require(result.get('bootPass') is True and result.get('status') == 'boot-passed',
                          'standalone Linux boot not complete', pending=result.get('bootPass') is not True)
            pipeline = result.get('suitePipeline')
            audit.require(isinstance(pipeline, dict) and pipeline.get('requested') is False, 'unexpected core pipeline')
            audit.require(tick_string(result.get('tick')) and int(result['tick']) > 0, 'invalid actual result tick')
            actual_dtb = audit.digest(directory / 'device.dtb')
            if actual_dtb is not None:
                audit.require(sha_string(result.get('dtbSha256')) and actual_dtb == result['dtbSha256'], 'actual DTB hash differs')
        progress = audit.read(directory / 'native-linux-progress.jsonl')
        if progress is not None:
            try:
                rows = [base.unique_json(line) for line in progress.splitlines() if line.strip()]
                valid = bool(rows) and all(isinstance(row, dict) and tick_string(row.get('tick'))
                                          and type(row.get('exitCode')) is int and isinstance(row.get('cause'), str)
                                          for row in rows)
                audit.require(valid, 'invalid/empty progress rows', pending=not rows)
                if valid:
                    audit.require(all(int(a['tick']) < int(b['tick']) for a, b in zip(rows, rows[1:])),
                                  'progress ticks did not increase')
                    if result is not None:
                        audit.require(all(rows[-1].get(key) == result.get(key) for key in ('tick', 'cause', 'exitCode')),
                                      'final progress does not match result')
                    exits = [row for row in rows if row['cause'].startswith('m5_exit instruction encountered')]
                    audit.require(len(exits) == 1 and exits[0]['exitCode'] == 0, 'one normal guest exit required', pending=not exits)
            except json.JSONDecodeError as error:
                audit.require(False, 'partial/invalid progress: ' + str(error), pending=True)
            except (ValueError, TypeError) as error:
                audit.require(False, 'invalid progress: ' + str(error))
        bundle = None
        if plan is not None:
            verify_plan(audit, base, directory, plan, result, host_log)
            bundle = verify_bundle(audit, base, plan)
        checks['frozenBundleVerified'] = bundle is not None
        census = read_json(audit, base, directory / 'native-chi-counts.json')
        if census is not None and config is not None:
            checks['census'] = audit.module('check-chi-transfer-census.py').check(census, config)
            audit.require(checks['census'].get('valid') is True, 'invalid native endpoint census')
            audit.require(census.get('snapshotKind') == 'process-exit', 'final census missing',
                          pending=census.get('snapshotKind') != 'process-exit')
            if result is not None and tick_string(census.get('lastTick')) and tick_string(result.get('tick')):
                audit.require(int(census['lastTick']) <= int(result['tick']), 'census extends past guest exit')
        slices = serial_slices(audit, base, serial) if serial is not None else None
        checks['serialOrderVerified'] = slices is not None
        if bundle is not None and slices is not None:
            root, inventory = bundle['root'], bundle['inventory']
            audit.require(slices['wrapper'].get('binarySha256') == inventory['random/random-memory'], 'guest binary identity differs')
            smoke_checker = verified_module(audit, root / 'checkers/check-linux-litmus.py', inventory['checkers/check-linux-litmus.py'])
            if smoke_checker is not None:
                before_smoke = len(audit.failures), len(audit.incomplete)
                checks['litmusSmoke'] = smoke_checker.check(slices['smoke'], bundle['smokeManifest'])
                smoke_ok = checker_success(audit, checks['litmusSmoke'], 'actual Linux smoke')
                base.stage_detail(audit, 'litmus-smoke', checks['litmusSmoke'])
                if smoke_ok and before_smoke == (len(audit.failures), len(audit.incomplete)):
                    random_checker = verified_module(audit, root / 'checkers/check-linux-random-memory.py',
                                                     inventory['checkers/check-linux-random-memory.py'])
                    if random_checker is not None:
                        checks['randomMemory'] = random_checker.check(slices['random'], root / 'random/manifest.json',
                                                                    bundle['index']['parameters'])
                        checker_success(audit, checks['randomMemory'], 'actual Linux random migration')
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, ArithmeticError,
            SyntaxError, ImportError, configparser.Error) as error:
        audit.require(False, 'malformed acceptance input or checker: ' + str(error))
    status = 'INCOMPLETE' if audit.incomplete else 'FAIL' if audit.failures else 'PASS'
    return {'schemaVersion': 1, 'status': status, 'passed': status == 'PASS', 'knownFailure': bool(audit.failures),
            'checkedAtUtc': datetime.now(timezone.utc).isoformat(), 'directory': str(directory),
            'gem5Sha256': (result or {}).get('gem5Sha256'), 'checks': checks,
            'incomplete': audit.incomplete, 'failures': audit.failures, 'inputs': audit.inputs,
            'scope': 'Actual supplemental Linux smoke and concurrent random migration. Not completion of the core/torture/kernel-build goal.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = check(args.run)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'failures': report['failures'], 'incomplete': report['incomplete']}, indent=2))
    raise SystemExit(0 if report['passed'] else 2 if report['status'] == 'INCOMPLETE' else 1)
