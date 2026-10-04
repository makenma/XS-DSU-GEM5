"""Versioned, fail-closed full Linux launch preparation and serial execution.

The model08 files remain immutable. Their stateless validators are reused, not
monkey-patched. A launch plan is preparation evidence, never a Linux PASS.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import native_linux_run08 as legacy

PROJECT = Path(__file__).resolve().parents[1]
PROFILE = 'model10-full-v1'
MODEL_ID = 'model10'
MODEL_PATH = Path('/root/chi-flowgraph-work/linux-validation/artifacts/'
                  'fs-model-10-fetch-demand-fault-candidate02/gem5.opt')
WSL_OUTPUT_ROOT = Path('/root/chi-flowgraph-work/linux-validation/runs/model10')
OUTPUT_NAMES = ('core', 'randomMemory', 'kernelBuild', 'state', 'acceptance', 'supervisorLog')
STAGES = ('core', 'randomMemory', 'kernelBuild')
FROZEN = dict(legacy.FROZEN)
# Accepted model08 hardware/configuration identities, independent of a newly
# prepared plan. Relative inventory keys permit the same content on both hosts.
FS_CONFIG_SHA256 = '6ed1ea6c729b1ac53a11de6c60eb4492af8198f770c65aea242098f2a48981bf'
CONFIG_INVENTORY_SHA256 = '1459fa4f30a8d01a1895eb55f38eadb33d81dcb4a3e0dca25f5a466ed8c10af5'
CHECKERS = (*legacy.CHECKERS, 'native_linux_run_v2.py', 'prepare-native-linux-10.py',
            'check-native-linux-complete-10.py', 'run-native-fetch-stream-fs.py',
            'build-native-fetch-stream-fs.py', 'build-native-fetch-boundary-cases.py',
            'build-native-fetch-page-fault.py')
require, sha, parse, read, absolute = legacy.require, legacy.sha, legacy.parse, legacy.read, legacy.absolute
module, identity = legacy.module, legacy.identity
ERRORS = (OSError, ValueError, KeyError, TypeError, IndexError, StopIteration, subprocess.SubprocessError)

# These immutable program identities fix the test semantics; a supplied manifest
# cannot remove checks, substitute a success-only ELF, or rename a case.
FETCH_PROGRAMS = {
    'stream': ('native-fetch-stream-fs-program', 'native-fetch-stream-fs.S',
               '16d73f33c2124d8d1d9b123102ee8f2943471a61e75ca7f5c9230ede56f30a3a'),
    'boundary': ('native-fetch-boundary-cases-program', 'native-fetch-boundary-cases.S',
                 '2fd0418df329b137d6a238ee1c97de281fa27dbac9419481e6db0dccd0173072'),
    'page': ('native-fetch-page-fault-program', 'native-fetch-page-fault.S',
             '89dd4cbc5da2107b631f7a5f8a4c14c701ecb5ba200d600742cd98f795a56163'),
}
FETCH_CASES = {
    'same-layout': ('stream', 'c6cc0d14896a9af40c24d958546ffd8e4c0045bf75ce264a4b6a8ecc637a8e5f', None),
    'changed-layout': ('stream', 'b3c54beb3b6f3890d140c00b65e0ba7d6e347bbbfb49d6d284ffca476b520928', 0x40000002),
    'control-interior': ('boundary', '999187515c1778e6fc2af6b3d96c871cef7fba118112898bb2892642765f34ec', 0x40000002),
    'cross-stale': ('boundary', '0cdfd7aef41951d5baf85b7377c9c0e3c4211509ccff8ca3a521637c0319757b', 0x40000040),
    'cross-clean': ('boundary', '6d76b5205f8e2133d4d8f5b28c2c4a89e1c658277c6296dc4ec34f48560adcde', None),
    'prefix-interior': ('boundary', '2da4956583dd401b1a1fe8176d357a957474054727aaf2e1e45ca42124e938e8', 0x40000006),
    'amo-cross-stale': ('boundary', '80f38b758380412006a04c389b36008990f6f5a179efaf65a521ad056b62b0ff', 0x40000040),
    'legal-taken': ('boundary', '16fa5849192988c548cb92be76fe8547e9c279c4ecaae13c80f4f3cafb2d97ac', None),
    'page-tail-return': ('page', '5ec79e1e7fa3a9a722cee1f4be0c1f3b1545641e3115aca6855456dead356c03', None),
    'last-halfword-return': ('page', '4ece21c00c097fa07d24c364eb74bb1c23574337c35a8b11996042908c9f923a', None),
    'straddling-instruction-fault': ('page', '2a511101c14b7eec96c2d0ee30983b15d3dc5461d45bc9de60494620f16a7b01', None),
}


def outputs_check(outputs, fresh=True):
    require(isinstance(outputs, dict) and set(outputs) == set(OUTPUT_NAMES), 'exact v2 output inventory required')
    values = [absolute(outputs[key]) for key in OUTPUT_NAMES]
    allowed = {(PROJECT / 'build/linux-validation/model10').absolute()}
    if WSL_OUTPUT_ROOT.is_absolute():
        allowed.add(WSL_OUTPUT_ROOT)  # Do not resolve the whitelist through a symlink.
    require(len(set(values)) == len(values) and len({p.parent for p in values}) == 1
            and values[0].parent in allowed, 'outputs must be distinct immediate children of a model10 root')
    if fresh:
        require(not any(p.exists() or p.is_symlink() for p in values), 'refusing existing output/state/log/report')


def model_check(spec, pin):
    require(spec.get('profile') == PROFILE and spec.get('modelId') == MODEL_ID, 'fixed model10 full profile required')
    value = spec.get('gem5Sha256')
    require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value)
            and len(set(value)) > 1 and value != legacy.MODEL05, 'explicit candidate SHA is absent/malformed/placeholder')
    require(absolute(spec['gem5']) == MODEL_PATH and str(absolute(spec['gem5'])) == spec['gem5'],
            'candidate must be the immutable model10 candidate02 binary path')
    require(sha(pin(spec['gem5'])) == value, 'actual candidate binary SHA differs')


def commands(spec):
    # Existing fixed commands retain every CPU, topology, tick budget and guest
    # argument. Only candidate identity and fresh output locations vary.
    return legacy.commands(spec)


def hardware_inventory(source):
    inventory = legacy.python_inventory(source)
    root = absolute(str(source)) / 'configs'
    relative = {Path(path).relative_to(root).as_posix(): value for path, value in inventory.items()}
    digest = hashlib.sha256(json.dumps(relative, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    require(digest == CONFIG_INVENTORY_SHA256, 'hardware config inventory differs from accepted full model08 topology/defaults')
    require(sha(PROJECT / 'native/gem5/native_l2_fs.py') == FS_CONFIG_SHA256,
            'fixed native FS configuration changed (clock/cache/topology/workload defaults)')
    return inventory


def bounded_fs_scope(spec, plan):
    """Bind the strict comparator to the full agreed 5e9-tick prerequisite."""
    config = str(PROJECT / 'native/gem5/native_l2_fs.py')
    require(type(plan.get('schemaVersion')) is int and plan['schemaVersion'] == 1
            and plan.get('kind') == 'bounded-real-fs-equivalence-inputs'
            and plan.get('ticks') == '5000000000' and plan.get('progressTicks') == '1000000000'
            and plan.get('suitePipeline') is True and type(plan.get('expectedHostExitCode')) is int
            and plan['expectedHostExitCode'] == 2, 'bounded FS prerequisite budget/pipeline/exit scope changed')
    require(plan.get('config') == config and plan.get('firmware') == spec['coreFirmware'],
            'bounded FS must use the unchanged full core firmware/config')
    require(plan.get('environment') == {'CHI_GEM5_DIR': spec['gem5Source'], 'PYTHONHASHSEED': '0'}
            and plan.get('unsetEnvironment') == ['CHI_FLOWGRAPH_OUT', 'CHI_FLOWGRAPH_COUNTS', 'CHI_FLOWGRAPH_CONFIG'],
            'bounded FS environment differs')
    flags = ['--kernel', spec['coreFirmware'], '--numa-nodes', '2', '--seed', '1', '--zicbom',
             '--uncached-test-ram', '--max-ticks', '5000000000', '--progress-ticks', '1000000000',
             '--suite-pipeline', '--suite-inputs', spec['suiteInputs']]
    runs = plan.get('runs', [])
    require(len(runs) == 2 and [r.get('label') for r in runs] == ['before', 'after'], 'bounded FS exact run pair required')
    for index, row in enumerate(runs):
        binary, directory = str(absolute(row['binary'])), str(absolute(row['directory']))
        require(row.get('command') == [binary, '--outdir=' + directory, config, *flags],
                'bounded FS command reduced or changed the frozen prerequisite')
        expected = legacy.MODEL05 if index == 0 else spec['gem5Sha256']
        require(plan.get('inputSha256', {}).get(binary) == expected
                and (index == 0 or binary == spec['gem5']), 'bounded FS baseline/candidate identity differs')
    require(runs[0]['directory'] != runs[1]['directory'], 'bounded FS needs distinct before/after directories')
    require(plan['inputSha256'].get(config) == FS_CONFIG_SHA256
            and plan['inputSha256'].get(spec['coreFirmware']) == FROZEN['coreFirmwareSha256']
            and plan['inputSha256'].get(spec['suiteInputs']) == sha(spec['suiteInputs']),
            'bounded FS fixed configuration/firmware/suite pins differ')


def fetch_case_check(path, case, model, model_sha, manifest, firmware):
    """Re-read real execution evidence, independently of the saved PASS flag."""
    group, firmware_sha, invalid_slot = FETCH_CASES[case]
    rows = [row for row in manifest['variants'] if row.get('case') == case]
    require(len(rows) == 1, 'missing/duplicate fetch variant: ' + case)
    variant = rows[0]
    require(variant.get('sha256') == firmware_sha == sha(firmware), 'frozen fetch ELF differs: ' + case)
    require(manifest.get('notLinuxSuite') is True and manifest.get('hartsConfigured') == 4
            and manifest.get('activeTestHarts') == [0] and manifest.get('otherHarts') == 'WFI',
            'fetch regression execution scope differs')
    directory = absolute(str(path)).parent
    report, execution = read(path), read(directory / 'execution-plan.json')
    config = PROJECT / 'native/gem5/native_l2_fs.py'
    expected_command = [str(model), '--outdir=' + str(directory), '--remote-gdb-port=0',
                        '--debug-flags=Fetch,FetchFault,DecoupleBP,Decode', '--debug-file=fetch-stream-debug.log',
                        str(config), '--firmware', str(firmware), '--max-ticks', '1000000000',
                        '--progress-ticks', '100000000', '--zicbom']
    require(execution.get('command') == expected_command and execution.get('gem5') == str(model)
            and execution.get('gem5Sha256') == model_sha
            and execution.get('firmwareSha256') == firmware_sha
            and execution.get('configSha256') == sha(config) and execution.get('builderManifest') == manifest,
            'fetch actual execution identity/command differs: ' + case)
    legacy.actual_command(directory, expected_command)
    require((directory / 'host-exit-code.txt').read_text().strip() == '0', 'fetch host exit is nonzero/missing')
    result = read(directory / 'native-linux-result.json')
    require(result.get('gem5Sha256') == model_sha and type(result.get('exitCode')) is int
            and result['exitCode'] == 0 and result.get('cause', '').startswith('m5_exit instruction encountered')
            and type(result.get('tick')) is str and re.fullmatch('[1-9][0-9]*', result['tick']),
            'fetch actual simulator did not complete normally')
    require(report.get('passed') is True and report.get('case') == case
            and report.get('kind') == manifest['kind'] and report.get('notLinuxSuite') is True
            and report.get('gem5Sha256') == model_sha and report.get('firmwareSha256') == firmware_sha
            and report.get('configSha256') == sha(config)
            and type(report.get('hostReturnCode')) is int and report['hostReturnCode'] == 0
            and report.get('originalFetchRangeAssertion') is False
            and report.get('simulatorExit') == {k: result.get(k) for k in ('tick', 'cause', 'exitCode', 'gem5Sha256')},
            'fetch report differs from actual completion')
    serial_path = directory / 'system.platform.terminal'
    serial = serial_path.read_text(errors='replace')
    guest = [parse(line.removeprefix('FETCH_STREAM_RESULT ')) for line in serial.splitlines()
             if line.startswith('FETCH_STREAM_RESULT ')]
    checks = (['passed', 'trapPcChecked', 'causeChecked', 'tvalChecked']
              if group == 'page' else ['trainingChecked', 'mappedCodeChecked', 'trapPcChecked'])
    if group == 'page' and case != 'straddling-instruction-fault':
        checks += ['returnValueChecked', 'returnAddressChecked']
    require(report.get('requiredGuestChecks') == variant.get('requiredGuestChecks', checks)
            and set(checks) <= set(report['requiredGuestChecks']), 'fetch required checks were weakened')
    require(len(guest) == 1 and guest == report.get('guestResults') and guest[0].get('case') == case
            and guest[0].get('passed') is True and all(guest[0].get(k) is True for k in checks),
            'fetch actual guest result/checks failed')
    if group == 'page':
        traps = re.findall(r'^FETCH_PAGE_TRAP cause=(0x[0-9a-f]+) epc=(0x[0-9a-f]+) '
                           r'tval=(0x[0-9a-f]+) a0=(0x[0-9a-f]+)\s*$', serial, re.M)
        expected = ((12, 0x40000ffe, 0x40001000, 42) if case == 'straddling-instruction-fault'
                    else (9, 0x800000a0, 0, 42))
        require(len(traps) == 1 and tuple(int(v, 16) for v in traps[0]) == expected,
                'fetch page actual cause/EPC/tval/return value differs')
        require(type(guest[0].get('expectedTrap')) is int and guest[0]['expectedTrap'] == expected[0],
                'fetch page trap expectation differs')
    with (directory / 'raw.jsonl').open() as stream:
        trace = parse(next(line for line in stream if line.strip()))
    require(trace.get('type') == 'manifest' and trace.get('linux', {}).get('payload', {}).get('sha256') == firmware_sha
            and report.get('traceFirmwareIdentityChecked') is True, 'fetch raw firmware identity differs')
    topology_checker = module('check-native-linux-validation.py')
    audit = topology_checker.Audit()
    topo = topology_checker.topology(audit, directory, read(directory / 'flowgraph-config.json'), case)
    require(topo.get('verified') is True and not audit.failures and not audit.incomplete,
            'fetch actual four-KMH/native-CHI topology failed: ' + '; '.join(audit.failures + audit.incomplete))
    observed_slots, demand_faults = [], []
    with (directory / 'fetch-stream-debug.log').open(errors='replace') as stream:
        for line in stream:
            match = re.search(r'Predecode invalid BTB slot (0x[0-9a-fA-F]+):', line)
            if match and 0x40000000 <= int(match[1], 16) < 0x40001000:
                observed_slots.append(int(match[1], 16))
            fault = re.search(r'Demand fetch fault: pc=(0x[0-9a-f]+) missing=(0x[0-9a-f]+) '
                              r'request=(0x[0-9a-f]+) cause=([0-9]+)', line)
            if fault and 0x40000000 <= int(fault[1], 16) < 0x40001000:
                demand_faults.append((int(fault[1], 16), int(fault[2], 16), int(fault[3], 16), int(fault[4])))
    if group != 'page':
        require((invalid_slot in observed_slots if invalid_slot is not None else not observed_slots),
                'fetch expected stale-slot recovery/valid-slot preservation was not exercised')
    else:
        expected_faults = [(0x40000ffe, 0x40001000, 0x40001000, 12)] if case == 'straddling-instruction-fault' else []
        require(demand_faults == expected_faults, 'page fault was not issued for precisely the demanded instruction bytes')
    for name in ('run.log', 'system.platform.terminal', 'fetch-stream-debug.log'):
        artifact = directory / name
        require(report.get('artifacts', {}).get(name) == {'bytes': artifact.stat().st_size, 'sha256': sha(artifact)},
                'fetch stale/corrupt artifact: ' + name)
    return {'case': case, 'directory': str(directory), 'passed': True, 'tick': result['tick'],
            'invalidSlots': [hex(value) for value in observed_slots],
            'demandFaults': [list(value) for value in demand_faults]}


def fetch_regressions(spec, pin, pin_tree):
    reg = spec['regressions']
    groups = {'fetchPredecode': {k for k, v in FETCH_CASES.items() if v[0] != 'page'},
              'fetchPageFault': {k for k, v in FETCH_CASES.items() if v[0] == 'page'}}
    all_paths = []
    manifests = {}
    for group, (folder, source_name, source_sha) in FETCH_PROGRAMS.items():
        root = PROJECT / 'build' / folder
        manifest = read(pin(root / 'manifest.json'))
        source = PROJECT / 'native/gem5' / source_name
        require(manifest.get('sourceSha256') == source_sha == sha(pin(source)), 'frozen fetch source differs')
        require({v['case'] for v in manifest['variants']} == {k for k, v in FETCH_CASES.items() if v[0] == group}
                and len(manifest['variants']) == len({v['case'] for v in manifest['variants']}),
                'exact fetch manifest case inventory required')
        pin_tree(root)
        manifests[group] = (root, manifest)
    checked = []
    for label, cases in groups.items():
        require(isinstance(reg.get(label), dict) and set(reg[label]) == cases, 'complete ' + label + ' regression set required')
        for case in sorted(cases):
            report = pin(reg[label][case]); all_paths.append(report.parent)
            root, manifest = manifests[FETCH_CASES[case][0]]
            variant = next(v for v in manifest['variants'] if v['case'] == case)
            firmware = (root / variant['elf']).resolve()
            require(firmware.is_relative_to(root.resolve()), 'fetch firmware escapes its program bundle')
            checked.append(fetch_case_check(report, case, absolute(spec['gem5']), spec['gem5Sha256'], manifest, firmware))
            pin_tree(report.parent)
    require(len(set(all_paths)) == len(FETCH_CASES), 'each fetch case requires a distinct real run directory')
    return checked


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
    def attempt(label, fn):
        try:
            return fn()
        except ERRORS as error:
            problems.append(label + ': ' + str(error))
            return None
    attempt('model', lambda: model_check(spec, pin))
    inventory = attempt('config tree', lambda: hardware_inventory(absolute(spec['gem5Source'])))
    if inventory:
        pins.update(inventory)
    for filename in CHECKERS:
        attempt('script ' + filename, lambda filename=filename: pin(PROJECT / 'scripts' / filename))
    for filename in ('native_l2_fs.py', 'linux_stage_gate.py', 'BpuPhaseSpool.hh'):
        attempt('config/helper ' + filename, lambda filename=filename: pin(PROJECT / 'native/gem5' / filename))
    def firmware(key):
        require(spec[key + 'Sha256'] == FROZEN[key + 'Sha256'], 'changed original firmware identity')
        require(sha(pin(spec[key])) == spec[key + 'Sha256'], 'actual firmware differs')
    for key in ('coreFirmware', 'kernelFirmware'):
        attempt(key, lambda key=key: firmware(key))
    def bundle(path, keys, expected):
        path = pin(path); data = read(path)
        value = hashlib.sha256((json.dumps({k: data[k] for k in keys}, sort_keys=True, indent=2) + '\n').encode()).hexdigest()
        require(value == data.get('inputSha256') == expected, 'frozen bundle identity differs')
        seen = set()
        for row in data['files']:
            relative = row['path']; target = (path.parent / relative).resolve()
            require(relative not in seen and target.is_relative_to(path.parent), 'duplicate/escaping bundle file')
            seen.add(relative); require(sha(pin(target)) == row['sha256'], 'frozen bundle SHA mismatch')
        return data
    attempt('suite bundle', lambda: bundle(spec['suiteInputs'], ('schemaVersion', 'files', 'stages'), FROZEN['suiteInputSha256']))
    def random():
        build = read(pin(spec['randomBuildResult']))
        require(build.get('built') is True and build.get('guestExecuted') is False, 'random firmware is not a completed build-only artifact')
        require(build['firmwareSha256'] == FROZEN['randomFirmwareSha256']
                and sha(pin(build['firmware'])) == build['firmwareSha256'], 'random original firmware differs')
        data = bundle(build['inputIndex'], ('schemaVersion', 'parameters', 'coreInputSha256', 'files'), FROZEN['randomInputSha256'])
        require(data['coreInputSha256'] == FROZEN['suiteInputSha256'] and build['inputSha256'] == data['inputSha256'],
                'random/core bundle identities differ')
    attempt('random bundle', random)
    attempt('outputs', lambda: outputs_check(spec['outputs'], check_output_freshness))
    attempt('bounded FS scope', lambda: bounded_fs_scope(spec, read(pin(spec['regressions']['fs']['plan']))))
    attempt('existing strict regressions', lambda: legacy.validate_regressions(spec, pin, pin_tree))
    fetch = attempt('fetch regressions', lambda: fetch_regressions(spec, pin, pin_tree))
    resolved = attempt('commands', lambda: commands(spec))
    result = {'schemaVersion': 2, 'kind': 'native-linux-model-launch', 'profile': PROFILE, 'modelId': MODEL_ID,
              'ready': not problems, 'status': 'READY' if not problems else 'INCOMPLETE', 'spec': spec,
              'project': str(PROJECT), 'pinnedSha256': pins, 'configPythonSha256': inventory or {},
              'problems': problems, 'guestExecuted': False, 'commands': resolved, 'fetchRegressions': fetch,
              'stageOrder': list(STAGES),
              'scope': 'Preparation only; unchanged full core/random/kernel workloads. Never Linux PASS.'}
    result['identitySha256'] = identity(result)
    return result


def verify_plan(plan):
    require(type(plan.get('schemaVersion')) is int and plan['schemaVersion'] == 2
            and plan.get('kind') == 'native-linux-model-launch' and plan.get('profile') == PROFILE
            and plan.get('modelId') == MODEL_ID and plan.get('ready') is True and plan.get('status') == 'READY'
            and not plan.get('problems') and plan.get('guestExecuted') is False, 'launch plan is not ready')
    require(plan.get('identitySha256') == identity(plan), 'launch plan identity changed')
    require(plan.get('project') == str(PROJECT) and plan.get('stageOrder') == list(STAGES), 'project/stage order differs')
    spec = plan['spec']
    require(plan.get('commands') == commands(spec), 'reviewed command list changed')
    require(plan.get('configPythonSha256') == hardware_inventory(absolute(spec['gem5Source'])),
            'config Python inventory/content changed')
    required = {str((PROJECT / 'scripts' / name).resolve()) for name in CHECKERS}
    required.update(str((PROJECT / 'native/gem5' / name).resolve()) for name in
                    ('native_l2_fs.py', 'linux_stage_gate.py', 'BpuPhaseSpool.hh'))
    require(required <= set(plan.get('pinnedSha256', {})), 'missing mandatory checker/config/helper pins')
    for path, expected in plan['pinnedSha256'].items():
        require(sha(absolute(path)) == expected, 'pinned input changed: ' + path)
    require(plan['pinnedSha256'].get(str(absolute(spec['gem5']))) == spec['gem5Sha256'], 'model not pinned')
    fresh = prepare(spec, check_output_freshness=False)
    require(fresh['ready'] is True, 'revalidated prerequisites incomplete: ' + '; '.join(fresh['problems']))
    require(fresh['pinnedSha256'] == plan['pinnedSha256'] and fresh['fetchRegressions'] == plan['fetchRegressions'],
            'required evidence pin inventory/fetch findings differ')
    return spec


def _write(path, value, exclusive=False):
    with Path(path).open('x' if exclusive else 'w', encoding='utf-8') as stream:
        stream.write(json.dumps(value, indent=2) + '\n')


def _run_stage(plan, plan_path, stage, log, started):
    """Only launch_serial calls this after full validation and prior-stage PASS."""
    spec = plan['spec']; directory = absolute(spec['outputs'][stage])
    require(not directory.exists(), 'refusing existing stage directory')
    if stage == 'randomMemory':
        # This existing runner owns its directory, model/firmware verification,
        # actual child host-exit receipt and independent result recheck.
        command = plan['commands']['randomRunner']
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                   env=legacy.environment(absolute(spec['gem5Source'])))
        started(process.pid)
        return process.wait()
    directory.mkdir()
    command = plan['commands']['core' if stage == 'core' else 'kernel']
    _write(directory / 'execution-plan.json', {'kind': 'native-linux-' + stage + '-model10',
           'launchPlan': str(plan_path), 'launchPlanIdentitySha256': plan['identitySha256'],
           'gem5Sha256': spec['gem5Sha256'], 'command': command}, exclusive=True)
    with (directory / 'run.log').open('x') as stream:
        process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT,
                                   env=legacy.environment(absolute(spec['gem5Source'])))
        (directory / 'host-pid.txt').write_text(str(process.pid) + '\n')
        started(process.pid)
        code = process.wait()
    legacy.write_host_exit(directory / 'host-exit-code.txt', code)
    return code


def check_stage(plan, stage):
    spec = plan['spec']; directory = absolute(spec['outputs'][stage])
    require((directory / 'host-exit-code.txt').read_text().strip() == '0', stage + ': actual host exit not zero')
    if stage == 'randomMemory':
        report = module('check-native-linux-random-memory.py').check(directory)
        require(report.get('gem5Sha256') == spec['gem5Sha256'], 'random used a different candidate')
        execution = read(directory / 'execution-plan.json')
        build = read(spec['randomBuildResult'])
        expected = [spec['gem5'], '--outdir=' + str(directory), str(PROJECT / 'native/gem5/native_l2_fs.py'),
                    '--kernel', str(absolute(build['firmware'])), '--zicbom', '--uncached-test-ram',
                    '--max-ticks', '130000000000000']
        require(execution.get('command') == expected, 'random actual command changed')
        legacy.actual_command(directory, expected)
        require(report.get('status') == 'PASS' and report.get('passed') is True and not report.get('knownFailure')
                and not any(report.get(k) for k in ('failures', 'incomplete', 'errors', 'error')),
                'random did not complete all independent checks')
        return report
    command = plan['commands']['core' if stage == 'core' else 'kernel']
    legacy.actual_command(directory, command)
    checker = module('check-native-linux-validation.py'); audit = checker.Audit()
    row = checker.run_audit(audit, directory, 'core' if stage == 'core' else 'kernel-build',
                           audit.module('check-chi-transfer-census.py'), audit.module('check-linux-kernel-build.py'))
    require(row.get('passed') is True and not audit.failures and not audit.incomplete,
            stage + ' incomplete/failed: ' + '; '.join(audit.failures + audit.incomplete))
    require(row.get('result', {}).get('gem5Sha256') == spec['gem5Sha256'], stage + ' used a different candidate')
    return row


def launch_serial(plan_path):
    """Explicitly invoked only; no resume/retry, concurrency or shortened stage."""
    plan_path = absolute(str(plan_path)); plan = read(plan_path); spec = verify_plan(plan)
    outputs_check(spec['outputs'])
    paths = {k: absolute(v) for k, v in spec['outputs'].items()}
    paths['state'].parent.mkdir(parents=True, exist_ok=True)
    # O_EXCL claims this entire one-shot pipeline before the first guest starts.
    state = {'schemaVersion': 2, 'modelId': MODEL_ID, 'profile': PROFILE,
             'launchPlan': str(plan_path), 'launchPlanIdentitySha256': plan['identitySha256'],
             'gem5Sha256': spec['gem5Sha256'], 'stageOrder': list(STAGES), 'stages': [],
             'status': 'prepared', 'guestExecuted': False, 'passed': False,
             'driverSessionId': None, 'scope': 'Internal driver state; external tool completion still required.'}
    _write(paths['state'], state, exclusive=True)
    plan_sha = sha(plan_path)
    def update():
        state['updatedAtUtc'] = datetime.now(timezone.utc).isoformat()
        _write(paths['state'], state)
    try:
        with paths['supervisorLog'].open('x') as log:
            for stage in STAGES:
                require(sha(plan_path) == plan_sha, 'launch plan file changed during execution')
                verify_plan(plan)
                row = {'stage': stage, 'status': 'starting', 'passed': False, 'startedAtUtc': datetime.now(timezone.utc).isoformat()}
                state['stages'].append(row); state['status'] = 'running-' + stage; update()
                def started(pid):
                    row['hostPid'] = pid; state['guestExecuted'] = True; update()
                    print(json.dumps({'stage': stage, 'hostPid': pid, 'output': str(paths[stage])}), flush=True)
                code = _run_stage(plan, plan_path, stage, log, started)
                row.update(hostReturnCode=code, exitedAtUtc=datetime.now(timezone.utc).isoformat())
                update()
                require(type(code) is int and code == 0, stage + ': actual process exited ' + str(code))
                require(sha(plan_path) == plan_sha, 'launch plan changed while guest ran')
                verify_plan(plan)
                evidence = check_stage(plan, stage)
                _write(paths[stage] / 'serial-stage-audit.json', evidence, exclusive=True)
                row.update(status='PASS', passed=True, checkedAtUtc=datetime.now(timezone.utc).isoformat()); update()
            report = module('check-native-linux-validation.py').check(paths['core'], paths['kernelBuild'])
            require(report.get('status') == 'PASS' and report.get('passed') is True
                    and not report.get('knownFailure'), 'combined core/kernel coverage or validation failed')
            _write(paths['acceptance'], report, exclusive=True)
            state.update(status='stages-passed-awaiting-external-driver-completion', passed=True)
            update()
        return 0
    except ERRORS as error:
        state.update(status='failed', passed=False, knownFailure=True, error=str(error)); update()
        return 2


def final_check(core, kernel_build, random_memory, driver_completion, expected_driver_session, launch_plan):
    require(type(expected_driver_session) is int and expected_driver_session > 0,
            'explicit externally observed driver session required')
    plan = read(launch_plan); spec = verify_plan(plan)
    original = module('check-native-linux-complete.py')
    original.DRIVER_SESSION = expected_driver_session
    report = original.check(core, kernel_build, random_memory, driver_completion)
    report.update(expectedDriverSession=expected_driver_session, launchPlan=str(Path(launch_plan).resolve()),
                  launchPlanIdentitySha256=plan['identitySha256'], modelId=MODEL_ID, profile=PROFILE)
    try:
        supplied = {'core': core, 'kernelBuild': kernel_build, 'randomMemory': random_memory}
        for key, directory in supplied.items():
            require(absolute(str(directory)) == absolute(spec['outputs'][key]), 'launch run directory differs: ' + key)
            require((Path(directory) / 'host-exit-code.txt').read_text().strip() == '0', key + ': host exit not zero')
        require(report.get('gem5Sha256') == spec['gem5Sha256'], 'Linux runs differ from selected candidate')
        state = read(spec['outputs']['state'])
        require(state.get('launchPlanIdentitySha256') == plan['identitySha256']
                and state.get('gem5Sha256') == spec['gem5Sha256'] and state.get('stageOrder') == list(STAGES)
                and state.get('status') == 'stages-passed-awaiting-external-driver-completion'
                and state.get('passed') is True and state.get('guestExecuted') is True,
                'serial pipeline did not complete all stages')
        rows = state.get('stages', [])
        require([r.get('stage') for r in rows] == list(STAGES), 'serial stage inventory/order differs')
        previous = None
        for row in rows:
            require(row.get('status') == 'PASS' and row.get('passed') is True
                    and type(row.get('hostReturnCode')) is int and row['hostReturnCode'] == 0
                    and type(row.get('hostPid')) is int and row['hostPid'] > 0, 'stage actual completion missing')
            start, exit_time, checked = [datetime.fromisoformat(row[k]) for k in ('startedAtUtc', 'exitedAtUtc', 'checkedAtUtc')]
            require(all(t.utcoffset() is not None and t.utcoffset().total_seconds() == 0 for t in (start, exit_time, checked))
                    and start <= exit_time <= checked and (previous is None or previous <= start),
                    'serial stages overlapped or timestamps are invalid')
            previous = checked
            check_stage(plan, row['stage'])  # Re-read results; never trust stage audit PASS files.
        receipt = read(driver_completion)
        require(receipt.get('launchPlanIdentitySha256') == plan['identitySha256']
                and absolute(receipt['randomMemory']) == absolute(spec['outputs']['randomMemory']),
                'external driver receipt does not cover this whole serial pipeline')
    except ERRORS as error:
        report['failures'].append('model10 serial chain: ' + str(error))
    report['status'] = 'INCOMPLETE' if report['incomplete'] else 'FAIL' if report['failures'] else 'PASS'
    report['passed'] = report['status'] == 'PASS'; report['knownFailure'] = bool(report['failures'])
    return report
