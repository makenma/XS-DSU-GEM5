"""Portable, foreground native-CHI Linux stage runner with fresh evidence.

This produces a new server identity, not model08/model10 certification. No stage
can shorten the fixed workload or claim completion of the combined Linux suite.
"""
import argparse
import configparser
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[1]
LIMITS = {'boot': 100_000_000_000, 'core': 100_000_000_000_000,
          'random': 130_000_000_000_000, 'kernel': 10_000_000_000_000_000}
STAGES = {'boot', 'core', 'random', 'kernel'}
CORE_STAGES = {'litmus-smoke', 'litmus-large', 'torture', 'pthreads', 'cbo', 'uncached'}
CHECKERS = ('check-native-linux-validation.py', 'check-native-linux-random-memory.py',
            'check-chi-transfer-census.py', 'check-linux-kernel-build.py',
            'check-linux-litmus.py', 'check-linux-suite.py', 'check-linux-cbo.py',
            'check-linux-uncached.py', 'check-kernel-torture.py', 'check-linux-random-memory.py')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    path = Path(path)
    before = path.stat()
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    after = path.stat()
    require((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns),
            'input changed during hashing: ' + str(path))
    return h.hexdigest()


def read(path):
    def pairs(items):
        out = {}
        for k, v in items:
            require(k not in out, 'duplicate JSON key: ' + k)
            out[k] = v
        return out
    return json.loads(Path(path).read_text(), object_pairs_hook=pairs)


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def load(name):
    spec = importlib.util.spec_from_file_location('server_' + name.replace('-', '_'), PROJECT / 'scripts' / name)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def source_root(explicit):
    candidates = [explicit, os.environ.get('CHI_GEM5_DIR'), PROJECT.parent.parent]
    for candidate in candidates:
        if candidate is None:
            continue
        path = Path(candidate).expanduser().resolve()
        if all((path / name).is_file() for name in ('SConstruct', 'configs/common/xiangshan.py', 'configs/common/Caches.py')):
            return path
        if explicit is not None or candidate == os.environ.get('CHI_GEM5_DIR'):
            raise ValueError('invalid gem5 source root: ' + str(path))
    raise ValueError('set --gem5-source or CHI_GEM5_DIR; no integrated gem5 source found')


def verify_litmus_inventory(manifest, stage):
    tests = manifest.get('tests', [])
    expected = 6 if stage == 'litmus-smoke' else 1936
    require(manifest.get('profile') == stage.removeprefix('litmus-') and len(tests) == expected,
            'fixed smoke/large test inventory differs')
    unique = len({item.get('testHash') for item in tests})
    require(manifest.get('testCount') == expected and unique == manifest.get('uniqueSemanticTestCount')
            == (5 if stage == 'litmus-smoke' else 1908), 'fixed semantic test inventory differs')
    runtime = manifest.get('runtime', {})
    require(runtime.get('availableCpus') == 4 and runtime.get('affinityCpus') == [0, 1, 2, 3]
            and runtime.get('forcedAffinity') is True and runtime.get('runs') == 1
            and runtime.get('samplesPerInstance') == (8 if stage == 'litmus-smoke' else 128),
            'fixed litmus iterations/CPU affinity differs')


def verify_bundle(index_path, kind):
    index_path = Path(index_path).resolve()
    index = read(index_path)
    require(type(index.get('schemaVersion')) is int and index['schemaVersion'] == 1, 'unknown bundle schema')
    keys = ('schemaVersion', 'files', 'stages') if kind == 'core' else ('schemaVersion', 'parameters', 'coreInputSha256', 'files')
    identity = {key: index[key] for key in keys}
    require(hashlib.sha256(canonical(identity)).hexdigest() == index.get('inputSha256'), 'bundle identity mismatch')
    files = {str(index_path): sha(index_path)}
    inventory = {}
    for row in index['files']:
        require(isinstance(row, dict) and set(row) == {'path', 'sha256'}, 'invalid bundle record')
        name = row['path']
        require(isinstance(name, str) and name not in inventory and not Path(name).is_absolute(), 'duplicate/absolute bundle path')
        path = (index_path.parent / name).resolve()
        require(path.is_relative_to(index_path.parent), 'bundle path escapes root')
        require(sha(path) == row['sha256'], 'bundle bytes changed: ' + name)
        files[str(path)] = row['sha256']
        inventory[name] = path
    if kind == 'core':
        require(set(index['stages']) == CORE_STAGES, 'exact six workload stages required')
        for stage, spec in index['stages'].items():
            require(spec['checker'] in inventory, 'missing archived checker: ' + stage)
            require(sha(PROJECT / 'scripts' / Path(spec['checker']).name) == sha(inventory[spec['checker']]),
                    'frozen checker differs from current server checker: ' + stage)
            if stage == 'torture':
                continue
            require(spec['manifest'] in inventory and spec['binary'] in inventory, 'missing workload manifest/ELF')
            manifest = read(inventory[spec['manifest']])
            require(manifest.get('binarySha256') == sha(inventory[spec['binary']]), 'workload ELF SHA mismatch')
            if stage.startswith('litmus-'):
                verify_litmus_inventory(manifest, stage)
                # check-linux-litmus rechecks per-test semantic hashes/outcomes in actual serial.
            if stage in ('cbo', 'uncached'):
                require(spec.get('module') in inventory and manifest.get('module', {}).get('sha256') == sha(inventory[spec['module']]),
                        'matching kernel test module missing/changed')
    else:
        checker = load('check-native-linux-random-memory.py')
        base = load('check-native-linux-validation.py')
        audit = base.Audit()
        require(checker.verify_bundle(audit, base, {'inputIndex': str(index_path), 'inputSha256': index['inputSha256']}) is not None
                and not audit.failures and not audit.incomplete, 'random bundle failed original checks: ' + repr(audit.failures + audit.incomplete))
    return index, files


def stage_command(stage, gem5, config, firmware, output, suite_inputs=None):
    require(stage in STAGES, 'unknown stage')
    command = [str(gem5), '--outdir=' + str(output), str(config), '--kernel', str(firmware),
               '--numa-nodes', '2', '--sets', '512', '--assoc', '8', '--mshrs', '32',
               '--hn-entries', '64', '--slc-lines', '8192', '--seed', '1',
               '--progress-ticks', '1000000000', '--max-ticks', str(LIMITS[stage])]
    if stage == 'kernel':
        command += ['--mem-size', '4GiB', '--dtb-addr', '0x17fe00000', '--zicbom']
    else:
        command += ['--mem-size', '1GiB']
    if stage in ('core', 'random'):
        command += ['--zicbom', '--uncached-test-ram']
    if stage == 'core':
        require(suite_inputs is not None, 'core requires --suite-inputs')
        command += ['--suite-pipeline', '--suite-inputs', str(suite_inputs)]
    return command


def prepare(args):
    gem5 = args.gem5.expanduser().resolve()
    work = args.work_dir.expanduser().resolve()
    raw_output = args.output.expanduser()
    require(not raw_output.exists() and not raw_output.is_symlink(), 'refusing existing output: ' + str(raw_output))
    output = raw_output.resolve()
    require(gem5.is_file() and os.access(gem5, os.X_OK), 'gem5 executable missing/not executable')
    source = source_root(args.gem5_source)
    config = PROJECT / 'native/gem5/native_l2_fs.py'
    files = {str(path): sha(path) for path in [gem5, config, PROJECT / 'native/gem5/linux_stage_gate.py', Path(__file__),
             *(PROJECT / 'scripts' / name for name in CHECKERS), *sorted((source / 'configs').rglob('*.py'))]}
    suite = args.suite_inputs.expanduser().resolve() if args.suite_inputs else None
    plan = {'schemaVersion': 1, 'kind': 'native-linux-server-' + args.stage, 'stage': args.stage,
            'createdAtUtc': datetime.now(timezone.utc).isoformat(), 'gem5': str(gem5),
            'gem5Sha256': files[str(gem5)], 'gem5Source': str(source), 'workDir': str(work),
            'config': str(config), 'configSha256': files[str(config)], 'cwd': str(PROJECT),
            'modelCertification': 'new-server-build-unclassified', 'completeLinuxAcceptance': False,
            'originalModel10Certification': False, 'guestExecuted': False}
    if args.stage == 'core':
        require(suite is not None, 'core requires --suite-inputs')
        index, frozen = verify_bundle(suite, 'core')
        files.update(frozen)
        plan.update(inputIndex=str(suite), inputSha256=index['inputSha256'])
    firmware = args.firmware.expanduser().resolve() if args.firmware else None
    if args.stage == 'random':
        require(args.random_build_result is not None, 'random requires --random-build-result')
        build_path = args.random_build_result.expanduser().resolve()
        build = read(build_path)
        require(build.get('built') is True and build.get('guestExecuted') is False, 'random firmware build not verified')
        built_firmware = Path(build['firmware']).resolve()
        require(firmware is None or firmware == built_firmware, 'random firmware differs from build report')
        firmware = built_firmware
        require(sha(firmware) == build['firmwareSha256'], 'random firmware SHA mismatch')
        index_path = Path(build['inputIndex']).resolve()
        index, frozen = verify_bundle(index_path, 'random')
        require(index['inputSha256'] == build['inputSha256'], 'random build/bundle identity differs')
        files.update(frozen)
        files[str(build_path)] = sha(build_path)
        plan.update(kind='native-linux-random-memory', buildResult=str(build_path),
                    buildResultSha256=files[str(build_path)], inputIndex=str(index_path), inputSha256=index['inputSha256'])
    if firmware is None:
        folder = {'boot': 'opensbi-build', 'core': 'opensbi-suite', 'kernel': 'opensbi-rootfs'}[args.stage]
        firmware = work / folder / 'platform/generic/firmware/fw_payload.elf'
    if args.stage == 'kernel':
        require(args.rootfs_manifest is not None, 'kernel requires --rootfs-manifest from its actual prepared rootfs')
        manifest_path = args.rootfs_manifest.expanduser().resolve()
        manifest = read(manifest_path)
        require(manifest.get('architecture') == 'riscv64' and manifest.get('preparedMode') == 'kernel-build'
                and manifest.get('defaultGuestMode') == 'kernel-build'
                and manifest.get('bootMemoryBytes') == 4 * 1024**3 and manifest.get('numaNodes') == 2
                and manifest.get('guestKernelBuild', {}).get('profile') == 'tinyconfig-smp4', 'wrong rootfs profile')
        files[str(manifest_path)] = sha(manifest_path)
        plan.update(rootfsManifest=str(manifest_path), rootfsManifestSha256=files[str(manifest_path)],
                    rootfsBoundary='Manifest is provenance, not proof of embedded bytes or a completed guest build.')
    require(firmware.is_file(), 'firmware missing: ' + str(firmware))
    with firmware.open('rb') as stream:
        head = stream.read(20)
    require(head[:6] == b'\x7fELF\x02\x01' and head[18:20] == b'\xf3\x00', 'firmware must be ELF64 little-endian RISC-V')
    files[str(firmware)] = sha(firmware)
    plan.update(firmware=str(firmware), firmwareSha256=files[str(firmware)], output=str(output),
                command=stage_command(args.stage, gem5, config, firmware, output, suite), pinnedSha256=files,
                limits={'maxTicks': str(LIMITS[args.stage]), 'progressTicks': '1000000000'},
                hardware={'harts': 4, 'clock': '1GHz', 'nativeL2': 4, 'hn': 4, 'sn': 2,
                          'mesh': [3, 2], 'numaNodes': 2, 'l2Sets': 512, 'l2Associativity': 8,
                          'l2Mshrs': 32, 'hnEntries': 64, 'slcLinesPerHn': 8192})
    return plan


def verify_pins(plan):
    for path, expected in plan['pinnedSha256'].items():
        require(sha(path) == expected, 'input changed since preparation: ' + path)


def boot_check(directory, base):
    audit = base.Audit()
    result = audit.json(directory / 'native-linux-result.json') or {}
    config = audit.json(directory / 'flowgraph-config.json')
    base.topology(audit, directory, config, 'boot')
    serial = audit.read(directory / 'system.platform.terminal') or ''
    lines = serial.replace('\r', '').splitlines()
    for marker in ('CHI_LINUX_ONLINE_CPUS=0-3', 'CHI_LINUX_ONLINE_NODES=0-1', 'CHI_LINUX_BOOT_PASS'):
        audit.require(lines.count(marker) == 1, 'missing/duplicate boot marker: ' + marker)
    audit.require(not re.search(r'CHI_LINUX_.*FAIL|Kernel panic -|Oops:', serial), 'guest boot error')
    audit.require(result.get('fullSystem') is True and result.get('protocolOpcodeInjection') is False
                  and result.get('bootPass') is True and result.get('status') == 'boot-passed'
                  and result.get('configuredHarts') == 4 and result.get('numaNodes') == 2
                  and type(result.get('exitCode')) is int and result['exitCode'] == 0
                  and str(result.get('cause', '')).startswith('m5_exit instruction encountered'), 'no actual completed boot')
    return {'passed': not audit.failures and not audit.incomplete, 'failures': audit.failures,
            'incomplete': audit.incomplete, 'scope': 'Four-hart/two-NUMA boot only; no Linux workload acceptance.'}


def check_stage(plan, host_code):
    directory = Path(plan['output'])
    require(type(host_code) is int and host_code == 0, 'gem5 host process exited nonzero: ' + str(host_code))
    verify_pins(plan)
    log = (directory / 'run.log').read_text(errors='replace')
    commands = [shlex.split(l.removeprefix('command line: ')) for l in log.splitlines() if l.startswith('command line: ')]
    require(commands == [plan['command']], 'actual gem5 command differs from saved command')
    result = read(directory / 'native-linux-result.json')
    require(result.get('gem5Sha256') == plan['gem5Sha256'] and result.get('firmwareSha256') == plan['firmwareSha256'],
            'actual model/firmware identity differs')
    config = configparser.ConfigParser(interpolation=None)
    config.read(directory / 'config.ini')
    require(config['system.clk_domain']['clock'] == '1000', 'actual CPU/system clock must be 1GHz')
    if plan['stage'] == 'random':
        return load('check-native-linux-random-memory.py').check(directory)
    base = load('check-native-linux-validation.py')
    if plan['stage'] == 'boot':
        return boot_check(directory, base)
    audit = base.Audit()
    return base.run_audit(audit, directory, 'core' if plan['stage'] == 'core' else 'kernel-build',
                          audit.module('check-chi-transfer-census.py'), audit.module('check-linux-kernel-build.py'))


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage', choices=sorted(STAGES), required=True)
    for name in ('gem5', 'work-dir', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    for name in ('gem5-source', 'firmware', 'suite-inputs', 'random-build-result', 'rootfs-manifest'):
        p.add_argument('--' + name, type=Path)
    p.add_argument('--dry-run', action='store_true', help='Hash/check inputs and save PREPARED plan only; consumes a fresh output directory')
    return p


def main():
    args = parser().parse_args()
    plan = prepare(args)
    out = Path(plan['output'])
    out.mkdir(parents=True, exist_ok=False)
    (out / 'execution-plan.json').write_text(json.dumps(plan, indent=2) + '\n')
    if args.dry_run:
        print(json.dumps({'status': 'PREPARED', 'guestExecuted': False, 'stagePassed': False,
                          'completeLinuxAcceptance': False, 'plan': str(out / 'execution-plan.json')}))
        return 0
    verify_pins(plan)
    environment = os.environ.copy()
    environment.update(CHI_GEM5_DIR=plan['gem5Source'], PYTHONHASHSEED='0')
    for key in ('CHI_FLOWGRAPH_OUT', 'CHI_FLOWGRAPH_COUNTS', 'CHI_FLOWGRAPH_CONFIG'):
        environment.pop(key, None)
    # Census is produced by native_l2_fs.py; detailed trace stays disabled as in the original Linux runs.
    with (out / 'run.log').open('wb') as log:
        child = subprocess.Popen(plan['command'], cwd=PROJECT, env=environment, stdout=log, stderr=subprocess.STDOUT)
        (out / 'host-pid.txt').write_text(str(child.pid) + '\n')
        print(json.dumps({'status': 'RUNNING', 'pid': child.pid, 'output': str(out)}), flush=True)
        code = child.wait()
    (out / 'host-exit-code.txt').write_text(str(code) + '\n')
    receipt = {'source': 'python-subprocess-wait', 'command': plan['command'], 'cwd': str(PROJECT),
               'pid': child.pid, 'hostExitCode': code, 'finishedAtUtc': datetime.now(timezone.utc).isoformat(),
               'gem5Sha256': plan['gem5Sha256'], 'firmwareSha256': plan['firmwareSha256']}
    (out / 'host-completion.json').write_text(json.dumps(receipt, indent=2) + '\n')
    try:
        checked = check_stage(plan, code)
    except (ValueError, KeyError, OSError, TypeError, configparser.Error) as error:
        checked = {'passed': False, 'error': str(error)}
    report = {'stage': args.stage, 'stagePassed': checked.get('passed') is True,
              'completeLinuxAcceptance': False, 'originalModel10Certification': False,
              'modelCertification': plan['modelCertification'], 'hostExitCode': code,
              'guestExecuted': True, 'check': checked}
    (out / 'server-stage-result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('stage', 'stagePassed', 'hostExitCode', 'completeLinuxAcceptance')}))
    return 0 if report['stagePassed'] else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, OSError, TypeError) as error:
        print('Server launch refused: ' + str(error), file=sys.stderr)
        raise SystemExit(2)
