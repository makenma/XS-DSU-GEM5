"""Independent model08 launch evidence; never edits model05 inputs or outputs."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
WSL_OUTPUT_ROOT = Path('/root/chi-flowgraph-work/linux-validation/runs/model08')
MODEL05 = '4b8b39640ef6f2f70fec1824f6f3254bc15a41e45aada746d290de825692ad7d'
FROZEN = {
    'coreFirmwareSha256': '4acde7c2a69d1a0bcea39ec78371510594a558cdce7f7fb367eae2af694eb36e',
    'kernelFirmwareSha256': 'e53cb2394430e92f2b9763effff88f246b19d90cfa2306b50077c3f9eb63922f',
    'suiteInputSha256': '76aa90c405b1a84843542040de750d9bcb9380205267bd87ffb1e0eb20a1baac',
    'randomInputSha256': '29334d7e5240758eba884c86d70ad0a4c65ec5407cef100e54313b14f1e0eb4a',
    'randomFirmwareSha256': 'b261fcf1513f9a4277a319446605d5362bf71d625f42b00d9cbaa6712d7a63d3',
}
CHECKERS = (
    'check-native-linux-validation.py', 'check-native-linux-random-memory.py',
    'check-native-linux-complete.py', 'check-chi-transfer-census.py',
    'check-linux-kernel-build.py', 'check-linux-litmus.py', 'check-linux-suite.py',
    'check-linux-cbo.py', 'check-linux-uncached.py', 'check-kernel-torture.py',
    'check-native-model-regression.py', 'check-native-fs-equivalence.py',
    'check-bpu-phase-storage-equivalence.py', 'check-bpu-phase-trace-invariance.py',
    'run-native-linux-random-memory.py',
    'native_linux_run08.py', 'prepare-native-linux-08.py',
    'continue-native-linux-08.py', 'check-native-linux-complete-08.py',
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def parse(text):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate JSON key: ' + key)
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def read(path):
    value = parse(Path(path).read_text(encoding='utf-8'))
    require(isinstance(value, dict), 'JSON object required: ' + str(path))
    return value


def absolute(value):
    require(isinstance(value, str) and Path(value).is_absolute(), 'absolute path required: ' + str(value))
    return Path(value).resolve()


def validate_output_paths(outputs, check_output_freshness=True):
    names = ('core', 'kernelBuild', 'randomMemory', 'state', 'acceptance', 'supervisorLog')
    require(set(outputs) == set(names), 'exact output inventory required')
    values = [absolute(outputs[key]) for key in names]
    allowed = {(PROJECT / 'build/linux-validation').resolve()}
    if WSL_OUTPUT_ROOT.is_absolute():
        # Keep this literal root: resolving the whitelist itself could accept a
        # symlink that redirects the entire output tree outside the named root.
        allowed.add(WSL_OUTPUT_ROOT)
    parents = {path.parent for path in values}
    require(len(set(values)) == len(values) and len(parents) == 1 and parents <= allowed,
            'outputs must be distinct immediate children of one allowed model08 output root')
    if check_output_freshness:
        require(not any(path.exists() for path in values), 'refusing existing output/state/log/report')


def module(name):
    path = PROJECT / 'scripts' / name
    spec = importlib.util.spec_from_file_location('run08_' + path.stem.replace('-', '_'), path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def python_inventory(source):
    root = Path(source).resolve() / 'configs'
    require(root.is_dir(), 'missing gem5 configs tree')
    files = sorted(root.rglob('*.py'))
    require(files and all((root / name).is_file() for name in
            ('common/xiangshan.py', 'common/Caches.py', 'common/FUScheduler.py')), 'incomplete config imports')
    require(all(path.resolve().is_relative_to(root) for path in files), 'config symlink escapes pinned tree')
    return {str(path.resolve()): sha(path) for path in files}


def environment(source):
    env = dict(os.environ)
    env['CHI_GEM5_DIR'] = str(source)
    for name in ('CHI_FLOWGRAPH_OUT', 'CHI_FLOWGRAPH_COUNTS', 'CHI_FLOWGRAPH_CONFIG'):
        env.pop(name, None)
    return env


def commands(spec):
    output = spec['outputs']
    config = str(PROJECT / 'native/gem5/native_l2_fs.py')
    return {
        'core': [spec['gem5'], '--outdir=' + output['core'], config,
                 '--kernel', spec['coreFirmware'], '--zicbom', '--uncached-test-ram',
                 '--suite-pipeline', '--suite-inputs', spec['suiteInputs']],
        'kernel': [spec['gem5'], '--outdir=' + output['kernelBuild'], config,
                   '--kernel', spec['kernelFirmware'], '--mem-size', '4GiB', '--numa-nodes', '2',
                   '--dtb-addr', '0x17fe00000', '--zicbom', '--max-ticks', '10000000000000000'],
        'randomRunner': [sys.executable, str(PROJECT / 'scripts/run-native-linux-random-memory.py'),
                         '--build-result', spec['randomBuildResult'], '--gem5', spec['gem5'],
                         '--gem5-sha256', spec['gem5Sha256'], '--gem5-source', spec['gem5Source'],
                         '--output', output['randomMemory']],
    }


def actual_command(directory, expected):
    text = (Path(directory) / 'run.log').read_text(encoding='utf-8', errors='surrogateescape')
    commands = [shlex.split(line[len('command line: '):]) for line in text.splitlines()
                if line.startswith('command line: ')]
    require(commands == [expected], 'actual run command differs: ' + str(directory))


def check_cbo(path, case, model, model_sha):
    row = read(path)
    require(row.get('passed') is True and row.get('gem5Sha256') == model_sha
            and row.get('case') == case and type(row.get('hostReturnCode')) is int
            and row['hostReturnCode'] == 0, 'focused CBO report not passed for candidate: ' + case)
    programs = PROJECT / 'build/native-cbo-tlb-program'
    variants = read(programs / 'manifest.json')['variants']
    choices = [value for value in variants if value.get('case') == case]
    require(len(choices) == 1, 'missing/duplicate focused CBO program case')
    variant = choices[0]
    firmware = (programs / variant['elf']).resolve()
    require(firmware.is_relative_to(programs.resolve()) and sha(firmware) == variant['sha256']
            == row.get('firmwareSha256') and row.get('coldCboPc') == variant['coldCboPc'],
            'focused CBO program/cold PC identity differs')
    require(row.get('expectedOriginalAssertion') is False and row.get('originalRetryAssertion') is False,
            'CBO old assertion result cannot authorize launch')
    directory = Path(path).parent
    command = row['command']
    require(isinstance(command, list) and command[0] == str(model)
            and '--outdir=' + str(directory.resolve()) in command, 'CBO execution identity mismatch')
    require(command.count('--firmware') == 1 and command[command.index('--firmware') + 1] == str(firmware),
            'CBO command did not execute its frozen firmware')
    actual_command(directory, command)
    result = read(directory / 'native-linux-result.json')
    require(result.get('gem5Sha256') == model_sha and type(result.get('exitCode')) is int
            and result['exitCode'] == 0 and result.get('cause', '').startswith('m5_exit instruction encountered'),
            'actual focused CBO exit differs')
    require(row['simulatorExit'] == {key: result.get(key) for key in ('tick', 'cause', 'exitCode', 'gem5Sha256')},
            'stale focused CBO footer')
    serial = (directory / 'system.platform.terminal').read_text(errors='replace')
    rows = [parse(line[len('CBO_TLB_RESULT '):]) for line in serial.splitlines() if line.startswith('CBO_TLB_RESULT ')]
    require(rows == row['guestResults'] and len(rows) == 1 and rows[0].get('passed') is True
            and rows[0].get('case') == case and rows[0].get('pcChecked') is True
            and type(rows[0].get('expectedTrap')) is int and rows[0]['expectedTrap'] == variant['expectedTrap']
            and (case != 'flush-fault' or rows[0].get('tvalChecked') is True), 'actual CBO guest evidence differs')
    debug = (directory / 'cbo-tlb-debug.log').read_text(errors='replace')
    cold_pc = hex(int(variant['coldCboPc'], 16))[2:]
    deferred = [line for line in debug.splitlines() if 'Deferred mem inst' in line
                and 'ready to execute' in line and cold_pc in line.lower()]
    require(row.get('runtimeDeferredTranslation') is True and row.get('deferredEvidence')
            and row['deferredEvidence'] == deferred[-3:], 'CBO missing actual cold-PC deferred retry')
    requests = []
    with (directory / 'raw.jsonl').open() as trace:
        manifest = parse(next(trace))
        require(manifest.get('type') == 'manifest' and manifest.get('linux', {}).get('payload', {}).get('sha256')
                == row['firmwareSha256'], 'focused CBO raw firmware mismatch')
        for line in trace:
            event = parse(line)
            if (event.get('kind') == 'flit' and event.get('observation') == 'TX'
                    and event.get('channel') == 'REQ' and event.get('boundary') == 'system.l20.chi_side'
                    and event.get('fields', {}).get('opcode') in (8, 9, 10)):
                requests.append(event)
    opcode = {'clean': 8, 'flush': 9, 'inval': 10}.get(case)
    require((not requests if case == 'flush-fault' else len(requests) == 1
             and requests[0]['fields']['opcode'] == opcode and int(requests[0]['address'], 0) == 0x80020000)
            and row.get('nativeMaintenanceTraceChecked') is True, 'focused CBO actual native traffic failed')


def validate_regressions(spec, pin, pin_tree):
    model, model_sha = absolute(spec['gem5']), spec['gem5Sha256']
    reg = spec['regressions']
    pin_tree(PROJECT / 'build/native-cbo-tlb-program')
    pin(PROJECT / 'native/gem5/BpuPhaseSpool.hh')
    require(set(reg['cbo']) == {'clean', 'flush', 'inval', 'flush-fault'}, 'all four focused CBO cases required')
    for case, path in reg['cbo'].items():
        pin(path); check_cbo(absolute(path), case, model, model_sha); pin_tree(absolute(path).parent)
    require(set(reg['se']) == {'on', 'off'}, 'both real SE trace modes required')
    for mode, evidence in reg['se'].items():
        report = read(pin(evidence['report']))
        after = absolute(evidence['after'])
        executed = read(after / 'execution-plan.json')
        require(executed.get('gem5Sha256') == model_sha and executed.get('trace') == mode
                and executed['command'][0] == str(model), 'SE candidate identity/trace differs')
        require((after / 'host-exit-code.txt').read_text().strip() == '0', 'SE actual host exit nonzero')
        actual_command(after, executed['command'])
        with tempfile.TemporaryDirectory(prefix='run08-se-check-') as temp:
            output = Path(temp) / 'report.json'
            command = [sys.executable, str(PROJECT / 'scripts/check-native-model-regression.py'),
                       '--before', str(absolute(evidence['before'])), '--after', str(after), '--output', str(output)]
            if mode == 'off': command.append('--without-trace')
            result = subprocess.run(command, capture_output=True, text=True, timeout=300)
            require(result.returncode == 0 and read(output) == report and report.get('passed') is True,
                    'SE comparison failed or supplied report stale: ' + mode)
        pin_tree(absolute(evidence['before'])); pin_tree(after)
    evidence = reg['fs']
    stored = read(pin(evidence['report'])); plan_path = pin(evidence['plan']); plan = read(plan_path)
    require(plan['runs'][1]['binary'] == str(model) and plan['inputSha256'][str(model)] == model_sha,
            'FS comparison did not use selected candidate')
    require(plan['inputSha256'][plan['runs'][0]['binary']] == MODEL05,
            'FS comparison baseline is not immutable model05')
    fresh = module('check-native-fs-equivalence.py').compare(plan, plan_path)
    require(fresh.get('equivalent') is True and fresh == stored, 'FS comparison not equivalent or stale report')
    for path in plan['inputSha256']: pin(path)
    for run in plan['runs']: pin_tree(absolute(run['directory']))
    require(set(reg['bpu']) == {'on', 'off'}, 'both complete BPU oracle trace modes required')
    for mode, evidence in reg['bpu'].items():
        stored = read(pin(evidence['report']))
        arguments = [absolute(evidence[key]) for key in
                     ('legacy', 'oracle', 'candidate', 'programManifest', 'helperReport')]
        fresh = module('check-bpu-phase-storage-equivalence.py').check(*arguments)
        require(fresh.get('pass') is True and fresh == stored and fresh.get('trace') == mode
                and fresh.get('modelSha256', [None])[-1] == model_sha, 'BPU complete oracle failed/stale: ' + mode)
        for path in arguments[:3]: pin_tree(path)
        pin_tree(arguments[3].parent); pin(arguments[4])
    evidence = reg['bpuTrace']
    stored = read(pin(evidence['report']))
    arguments = [absolute(evidence[key]) for key in ('on', 'off', 'programManifest')]
    fresh = module('check-bpu-phase-trace-invariance.py').check(*arguments)
    require(fresh.get('pass') is True and fresh == stored and fresh.get('modelSha256') == model_sha,
            'BPU trace invariance not passed/stale')
    require(str(arguments[0]) == reg['bpu']['on']['candidate']
            and str(arguments[1]) == reg['bpu']['off']['candidate'], 'BPU trace comparison uses different runs')


def prepare(spec, check_output_freshness=True):
    pins, problems = {}, []
    def pin(value):
        path = absolute(str(value)); pins[str(path)] = sha(path); return path
    def pin_tree(value):
        root = absolute(str(value))
        require(root.is_dir(), 'missing evidence directory: ' + str(root))
        for path in sorted(root.rglob('*')):
            if path.is_file():
                require(path.resolve().is_relative_to(root), 'evidence symlink escapes directory')
                pin(path)
    def attempt(label, function):
        try: return function()
        except (OSError, ValueError, KeyError, TypeError, IndexError, StopIteration, subprocess.SubprocessError) as error:
            problems.append(label + ': ' + str(error)); return None
    def model():
        require(isinstance(spec.get('gem5Sha256'), str) and re.fullmatch('[0-9a-f]{64}', spec['gem5Sha256'])
                and spec['gem5Sha256'] != MODEL05, 'candidate SHA is absent/malformed/original model05')
        require(sha(pin(spec['gem5'])) == spec['gem5Sha256'], 'candidate binary SHA differs')
    attempt('model', model)
    inventory = attempt('config tree', lambda: python_inventory(absolute(spec['gem5Source'])))
    if inventory: pins.update(inventory)
    for filename in CHECKERS:
        attempt('script ' + filename, lambda filename=filename: pin(PROJECT / 'scripts' / filename))
    for filename in ('native_l2_fs.py', 'linux_stage_gate.py'):
        attempt('config ' + filename, lambda filename=filename: pin(PROJECT / 'native/gem5' / filename))
    def firmware(key):
        require(spec[key + 'Sha256'] == FROZEN[key + 'Sha256'], 'changed original firmware identity')
        require(sha(pin(spec[key])) == spec[key + 'Sha256'], 'actual firmware differs')
    for key in ('coreFirmware', 'kernelFirmware'):
        attempt(key, lambda key=key: firmware(key))
    def bundle(path, keys, identity):
        path = pin(path); data = read(path)
        require(hashlib.sha256((json.dumps({key: data[key] for key in keys}, sort_keys=True, indent=2)+'\n').encode()).hexdigest()
                == data.get('inputSha256') == identity, 'frozen bundle identity differs')
        seen = set()
        for row in data['files']:
            relative = row['path']; target = (path.parent / relative).resolve()
            require(relative not in seen and target.is_relative_to(path.parent), 'duplicate/escaping bundle file')
            seen.add(relative); require(sha(pin(target)) == row['sha256'], 'frozen bundle SHA mismatch')
        return data
    attempt('suite bundle', lambda: bundle(spec['suiteInputs'], ('schemaVersion', 'files', 'stages'), FROZEN['suiteInputSha256']))
    def random():
        build = read(pin(spec['randomBuildResult']))
        require(build.get('built') is True and build.get('guestExecuted') is False, 'random firmware build is not completed build-only')
        require(build['firmwareSha256'] == FROZEN['randomFirmwareSha256']
                and sha(pin(build['firmware'])) == build['firmwareSha256'], 'random original firmware differs')
        data = bundle(build['inputIndex'], ('schemaVersion', 'parameters', 'coreInputSha256', 'files'), FROZEN['randomInputSha256'])
        require(data['coreInputSha256'] == FROZEN['suiteInputSha256'] and build['inputSha256'] == data['inputSha256'],
                'random/core bundle identities differ')
    attempt('random bundle', random)
    attempt('outputs', lambda: validate_output_paths(spec['outputs'], check_output_freshness))
    attempt('regressions', lambda: validate_regressions(spec, pin, pin_tree))
    resolved_commands = attempt('commands', lambda: commands(spec))
    result = {'schemaVersion': 1, 'kind': 'native-linux-model08-launch', 'ready': not problems,
              'status': 'READY' if not problems else 'INCOMPLETE', 'spec': spec,
              'project': str(PROJECT), 'pinnedSha256': pins, 'configPythonSha256': inventory or {},
              'problems': problems, 'guestExecuted': False, 'commands': resolved_commands,
              'scope': 'Launch preparation only, never Linux PASS. Unchanged full core/random/kernel workloads.'}
    result['identitySha256'] = identity(result)
    return result


def identity(plan):
    return hashlib.sha256(json.dumps({k: v for k, v in plan.items() if k != 'identitySha256'},
                                    sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def verify_plan(plan):
    require(type(plan.get('schemaVersion')) is int and plan['schemaVersion'] == 1 and plan.get('kind') == 'native-linux-model08-launch'
            and plan.get('ready') is True and plan.get('status') == 'READY' and not plan.get('problems')
            and plan.get('guestExecuted') is False, 'launch plan is not ready; no guest may start')
    require(plan.get('identitySha256') == identity(plan), 'launch plan identity changed')
    require(plan.get('project') == str(PROJECT), 'launch project differs')
    spec = plan['spec']
    require(plan.get('commands') == commands(spec), 'reviewed command list changed')
    require(plan.get('configPythonSha256') == python_inventory(absolute(spec['gem5Source'])),
            'config Python inventory/content changed (including added/removed imports)')
    require(bool(plan.get('pinnedSha256')), 'missing input pins')
    required_scripts = {str((PROJECT / 'scripts' / name).resolve()) for name in CHECKERS}
    required_scripts.update(str((PROJECT / 'native/gem5' / name).resolve()) for name in
                            ('native_l2_fs.py', 'linux_stage_gate.py', 'BpuPhaseSpool.hh'))
    require(required_scripts <= set(plan['pinnedSha256']), 'missing mandatory checker/config/helper pins')
    for path, expected in plan['pinnedSha256'].items():
        require(sha(absolute(path)) == expected, 'pinned input changed: ' + path)
    require(plan['pinnedSha256'].get(str(absolute(spec['gem5']))) == spec['gem5Sha256'], 'model not pinned')
    # A self-hash detects accidental edits; it is not authorization to assert
    # READY. Re-run all evidence predicates, even for a rehashed supplied plan.
    # Output freshness is separately checked immediately before each Popen.
    fresh = prepare(spec, check_output_freshness=False)
    require(fresh['ready'] is True, 'revalidated prerequisites are incomplete: ' + '; '.join(fresh['problems']))
    require(fresh['pinnedSha256'] == plan['pinnedSha256'], 'required evidence pin inventory differs')
    return spec


def wait_host_exit(path, attempts=30, sleep=None):
    import time
    sleep = sleep or time.sleep
    for _ in range(attempts):
        if Path(path).is_file():
            text = Path(path).read_text().strip()
            require(text == '0', 'actual core host exit is nonzero/malformed: ' + text)
            return
        sleep(1)
    raise ValueError('core host exit receipt is missing; kernel guest cannot start')


def write_host_exit(path, code):
    require(type(code) is int, 'host exit code must be an integer')
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(str(code) + '\n')
    temporary.replace(path)
