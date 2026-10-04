"""Strict acceptance of completed native-CHI Linux runs, never prepared or SE artifacts."""
import argparse
from collections import Counter
import configparser
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shlex

HERE = Path(__file__).resolve().parent
STAGES = ['boot', 'litmus-smoke', 'litmus-large', 'torture', 'pthreads', 'cbo', 'uncached', 'complete']
PTHREADS = ['spinlock', 'mutex', 'futex', 'amo', 'lrsc', 'release_acquire', 'false_sharing',
            'atomic_counter', 'memcpy_checksum', 'alias_cache_pressure', 'page_migration']
SCOPE = ('Finite full-system Linux tests on this recorded gem5 native-CHI model and topology. '
         'Not RTL equivalence, exhaustive CHI/RVWMO correctness, all-interleaving proof, '
         'or proof from prepared binaries, SE runs, or opcode coverage alone.')


def unique_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key: ' + key)
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=pairs,
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError('non-finite JSON: ' + x)))


def integer(value, minimum=0):
    return type(value) is int and value >= minimum


class Audit:
    def __init__(self):
        self.inputs, self.incomplete, self.failures = {}, [], []

    def require(self, condition, message, pending=False):
        if not condition:
            (self.incomplete if pending else self.failures).append(message)
        return bool(condition)

    def read(self, path, optional=False):
        path = Path(path).resolve()
        name = str(path)
        if not path.is_file():
            self.inputs[name] = {'exists': False, 'sha256': None}
            if not optional: self.incomplete.append('missing input: ' + name)
            return None
        try:
            before = path.stat()
            data = path.read_bytes()
            after = path.stat()
            stable = before.st_size == after.st_size == len(data) and before.st_mtime_ns == after.st_mtime_ns
            self.inputs[name] = {'exists': True, 'bytes': len(data),
                                 'sha256': hashlib.sha256(data).hexdigest(), 'stableDuringRead': stable}
            self.require(stable, 'input changed while reading: ' + name, pending=True)
            return data.decode('utf-8', errors='replace').replace('\r\n', '\n')
        except OSError as error:
            self.incomplete.append(f'unreadable input: {name}: {error}')
            return None

    def json(self, path, optional=False, array=False):
        text = self.read(path, optional)
        if text is None: return None
        try:
            value = unique_json(text)
            if not isinstance(value, list if array else dict): raise ValueError('wrong root type')
            return value
        except ValueError as error:
            self.failures.append(f'invalid JSON {path}: {error}')
            return None

    def digest(self, path):
        """Hash ELF/module inputs without decoding or retaining their bytes."""
        path = Path(path).resolve()
        if not path.is_file():
            self.inputs[str(path)] = {'exists': False, 'sha256': None}
            self.incomplete.append('missing archived input: ' + str(path))
            return None
        try:
            before, digest = path.stat(), hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''): digest.update(block)
            after = path.stat()
        except OSError as error:
            self.inputs[str(path)] = {'exists': path.is_file(), 'sha256': None}
            self.incomplete.append(f'unreadable archived input: {path}: {error}')
            return None
        stable = before.st_size == after.st_size and before.st_mtime_ns == after.st_mtime_ns
        self.inputs[str(path)] = {'exists': True, 'bytes': after.st_size,
                                 'sha256': digest.hexdigest(), 'stableDuringRead': stable}
        self.require(stable, 'archived input changed: ' + str(path), pending=True)
        return digest.hexdigest()

    def module(self, name):
        path = HERE / name
        self.read(path)
        spec = importlib.util.spec_from_file_location('native_validation_' + name.replace('-', '_'), path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def success(self, report, label):
        if not isinstance(report, dict): return False
        unfinished = report.get('status') in ('INCOMPLETE', 'NOT_RUN', 'TIMEOUT', 'UNSUPPORTED', 'prepared') or bool(report.get('incomplete'))
        if unfinished:
            self.incomplete.append(label + ': unfinished/unsupported/timeout')
        flags = [report[key] for key in ('pass', 'passed') if key in report]
        passed = bool(flags) and all(value is True for value in flags)
        self.require(passed, label + ': missing or false boolean pass/passed', pending=unfinished)
        self.require(not any(report.get(key) for key in ('errors', 'issues', 'error')), label + ': checker reports problems')
        self.require(report.get('status', 'PASS') == 'PASS', label + ': status is not PASS', pending=unfinished)
        return passed


def topology(audit, directory, config, label):
    text = audit.read(directory / 'config.ini')
    if text is None: return {'verified': False}
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        parser.read_string(text)
        values = {section: dict(parser[section]) for section in parser.sections()}
    except configparser.Error as error:
        audit.failures.append(f'{label}: invalid config.ini: {error}')
        return {'verified': False}
    start = len(audit.failures)
    def req(ok, message): return audit.require(ok, label + ': ' + message)
    def get(section, key): return values.get(section, {}).get(key)
    types = Counter(section.get('type') for section in values.values())
    req(get('root', 'full_system') == 'true', 'config.ini is not full-system')
    req(get('system', 'num_cpus') == '4' and get('system', 'mem_mode') == 'timing'
        and get('system', 'xiangshan_system') == 'true', 'four timing KMH CPUs required')
    for kind, count in [('BaseO3CPU', 4), ('ChiNativeL2', 4), ('ChiExtendedHome', 4),
                        ('ChiNativeMemory', 2), ('ChiRouterRefModel', 6)]:
        req(types[kind] == count, f'expected exactly {count} {kind}, got {types[kind]}')
    req(not any('Cache2ChiBridge' in str(kind) for kind in types), 'Cache2ChiBridge present')
    for hart in range(4):
        cpu, l2, bus = f'system.cpu{hart}', f'system.l2{hart}', f'system.l1bus{hart}'
        req(get(cpu, 'type') == 'BaseO3CPU' and get(cpu, 'cpu_id') == str(hart), f'CPU identity {hart}')
        req(get(l2, 'type') == 'ChiNativeL2', f'native L2 {hart} missing')
        req(str(get(l2, 'cpu_side')).startswith(bus + '.mem_side_ports['), f'L2 {hart} bypasses L1 bus')
        req(bool(re.fullmatch(r'system\.routers[0-5]\.device_ports\[0\]', str(get(l2, 'chi_side')))),
            f'L2 {hart} has no NoC timing connection')
        ports = str(get(bus, 'mem_side_ports')).split()
        req(len(ports) == 2 and ports[0] == l2 + '.cpu_side'
            and re.fullmatch(r'system\.iobus\.cpu_side_ports\[\d+\]', ports[1]), f'L1 bus {hart} has a bypass/unexpected target')
        for cache in ['icache', 'dcache', 'itb_walker_cache', 'dtb_walker_cache']:
            req(get(cpu + '.' + cache, 'type') == 'Cache'
                and str(get(cpu + '.' + cache, 'mem_side')).startswith(bus + '.cpu_side_ports['),
                f'CPU {hart} {cache} does not use native RAM path')
    coords = {(get(f'system.routers{i}', 'local_x'), get(f'system.routers{i}', 'local_y')) for i in range(6)}
    req(coords == {(str(x), str(y)) for x in range(3) for y in range(2)}, 'routers are not a 3x2 mesh')
    manifest = (config or {}).get('manifest', {})
    req(manifest.get('source', {}).get('fullSystem') is True and manifest.get('source', {}).get('nativeChi') is True,
        'manifest does not describe real full-system native CHI')
    roles = Counter(n.get('role') for n in manifest.get('topology', []) if isinstance(n, dict))
    req(roles == {'RN-F': 4, 'HN-F': 4, 'SN-F': 2}, 'manifest endpoint roles differ from 4RN/4HN/2SN')
    for role, prefix, count, field in [('RN-F', 'l2', 4, 'chi_side'), ('HN-F', 'hn', 4, 'rxport'), ('SN-F', 'sn', 2, 'chi_side')]:
        actual = {int(get(f'system.{prefix}{i}', 'node_id') or '-1') for i in range(count)}
        declared = {n.get('nodeId') for n in manifest.get('topology', []) if isinstance(n, dict) and n.get('role') == role}
        req(len(actual) == count and actual == declared, role + ' NodeIDs disagree with config.ini')
        for i in range(count):
            boundary = f'system.{prefix}{i}.{field}'
            req(manifest.get('endpointPorts', {}).get(boundary) == int(get(f'system.{prefix}{i}', 'node_id') or '-1'),
                'census boundary does not match instantiated endpoint: ' + boundary)
            if role != 'RN-F':
                req(bool(re.fullmatch(r'system\.routers[0-5]\.local_ports\[' + ('4' if role == 'HN-F' else '8') + r'\]',
                                     str(get(f'system.{prefix}{i}', field)))), boundary + ' bypasses NoC')
            if role == 'SN-F':
                req(str(get(f'system.{prefix}{i}', 'mem_side')).startswith('system.membus.cpu_side_ports['),
                    boundary + ' does not reach backend RAM')
    return {'verified': len(audit.failures) == start, 'typeCounts': dict(types),
            'cpuIds': [0, 1, 2, 3], 'mesh': [3, 2], 'classicBridge': False}


def stage_detail(audit, name, report):
    """Require the independent checker report's full inventory, not a PASS footer."""
    def req(ok, message): return audit.require(ok, f'core/{name}: {message}')
    if name.startswith('litmus-'):
        tests = report.get('tests', [])
        req(report.get('profile') == name.removeprefix('litmus-'), 'wrong litmus profile')
        req(integer(report.get('testCount'), 1) and report.get('testCount') == len(tests), 'incomplete litmus inventory')
        req(integer(report.get('uniqueSemanticTestCount'), 1000 if name == 'litmus-large' else 1), 'insufficient distinct semantic tests')
        for test in tests:
            req(isinstance(test, dict) and integer(test.get('expectedOutcomes'), 1)
                and test.get('observedOutcomes') == test.get('expectedOutcomes'), 'litmus outcome count incomplete')
    elif name == 'pthreads':
        req(report.get('fullSuiteRequested') is True and report.get('requiredTests') == PTHREADS
            and report.get('recordedTests') == PTHREADS and report.get('iterations') == 128,
            'all pthread/atomic/migration tests at 128 iterations required')
        req(report.get('testStatuses') == {'PASS': len(PTHREADS)}, 'unsupported/failed pthread test')
        records = report.get('results', [])
        req([r.get('test') for r in records if isinstance(r, dict)] == PTHREADS
            and all(r.get('status') == 'PASS' for r in records), 'missing actual per-workload result')
    elif name == 'cbo':
        req(report.get('fullCoverage') is True and report.get('phases') == ['disabled', 'enabled'], 'both real CBO phases required')
    elif name == 'uncached':
        req(report.get('samples') == 64 and isinstance(report.get('cpus'), list)
            and len(set(report['cpus'])) == 4, 'all four-hart/width/line samples required')
    elif name == 'torture':
        cases = report.get('cases', [])
        req([c.get('test') for c in cases if isinstance(c, dict)] == ['rcu', 'spin_lock', 'rw_lock', 'mutex_lock', 'rwsem_lock']
            and all(c.get('passed') is True and c.get('work') for c in cases), 'real RCU and all four lock workloads required')
        req(report.get('onlineCpus') == '0-3' and report.get('onlineNodes') == '0-1', 'torture topology mismatch')


def recheck_stages(audit, directory, logs, config):
    """Recheck real serial against content-addressed frozen inputs, never a stale PASS JSON.

    Run this command in the recorded gem5 filesystem context (normally WSL).
    Only known project checker modules execute; archived checker files are
    integrity-checked inputs, not dynamically executed code from an index.
    """
    names = [name for name in STAGES if name not in ('boot', 'complete') and logs.get(name) is not None]
    if not names: return {}
    copied = audit.json(directory / 'suite-inputs-index.json')
    launch = audit.read(directory / 'run.log')
    if copied is None or launch is None: return {}
    commands = [line[len('command line: '):] for line in launch.splitlines() if line.startswith('command line: ')]
    if not audit.require(len(commands) == 1, 'core: missing/duplicate recorded gem5 command line'): return {}
    words = shlex.split(commands[0])
    paths = [word.split('=', 1)[1] for word in words if word.startswith('--suite-inputs=')]
    paths += [words[i + 1] for i, word in enumerate(words[:-1]) if word == '--suite-inputs']
    if not audit.require(len(paths) == 1, 'core: recorded command has no unique frozen suite input index'): return {}
    index_path = Path(paths[0])
    if not index_path.is_absolute(): index_path = directory / index_path
    archived = audit.json(index_path)
    if archived is None: return {}
    start = len(audit.failures)
    audit.require(archived == copied, 'core: launch archive index differs from run-copied input index')
    identity = {key: archived.get(key) for key in ['schemaVersion', 'files', 'stages']}
    identity_bytes = (json.dumps(identity, sort_keys=True, indent=2, ensure_ascii=True) + '\n').encode()
    audit.require(hashlib.sha256(identity_bytes).hexdigest() == archived.get('inputSha256'), 'core: frozen input identity hash mismatch')
    audit.require(archived.get('schemaVersion') == 1 and isinstance(archived.get('files'), list), 'core: invalid frozen input schema')
    root = index_path.resolve().parent
    inventory = {}
    for entry in archived.get('files', []):
        if not isinstance(entry, dict): audit.require(False, 'core: invalid archived file record'); continue
        name = entry.get('path')
        if not isinstance(name, str): audit.require(False, 'core: invalid archived file path'); continue
        path = (root / name).resolve()
        if not audit.require(path.is_relative_to(root) and name not in inventory, 'core: archive path escapes root or duplicates an entry'): continue
        digest = audit.digest(path)
        if digest is not None: audit.require(digest == entry.get('sha256'), 'core: archived input changed: ' + name)
        inventory[name] = path
    if len(audit.failures) != start: return {}
    checker_names = {'litmus-smoke': 'check-linux-litmus.py', 'litmus-large': 'check-linux-litmus.py',
                     'torture': 'check-kernel-torture.py', 'pthreads': 'check-linux-suite.py',
                     'cbo': 'check-linux-cbo.py', 'uncached': 'check-linux-uncached.py'}
    results = {}
    for stage in names:
        entry = archived.get('stages', {}).get(stage, {})
        label = 'core/' + stage + '/serial-recheck'
        checker_name = checker_names[stage]
        if not audit.require(entry.get('checker') == 'checkers/' + checker_name
                             and entry['checker'] in inventory, label + ': wrong/missing frozen checker identity'): continue
        manifest = None
        if stage != 'torture':
            if not audit.require(entry.get('manifest') in inventory and entry.get('binary') in inventory,
                                 label + ': frozen manifest/binary absent'): continue
            manifest = audit.json(inventory[entry['manifest']])
            if manifest is None: continue
            binary_digest = audit.inputs[str(inventory[entry['binary']])].get('sha256')
            if binary_digest is None: continue
            audit.require(binary_digest == manifest.get('binarySha256'),
                          label + ': actual frozen ELF differs from manifest')
            if stage in ('cbo', 'uncached'):
                if not audit.require(entry.get('module') in inventory, label + ': missing frozen module record'): continue
                module_digest = audit.inputs[str(inventory[entry['module']])].get('sha256')
                if module_digest is None: continue
                audit.require(module_digest == manifest.get('module', {}).get('sha256'), label + ': actual module differs from manifest')
        checker = audit.module(checker_name)
        try:
            if stage == 'torture': report = checker.check(logs[stage])
            elif stage.startswith('litmus-'): report = checker.check(logs[stage], manifest)
            elif stage == 'pthreads': report = checker.check(logs[stage], manifest, 128)
            elif stage == 'cbo': report = checker.check(logs[stage], manifest, ['disabled', 'enabled'])
            else: report = checker.check(logs[stage], manifest, config)
            audit.success(report, label)
            stage_detail(audit, stage, report)
            results[stage] = report
        except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError) as error:
            audit.require(False, label + ': ' + str(error))
            results[stage] = {'passed': False, 'error': str(error)}
    return results


def run_audit(audit, directory, label, census_checker, kernel_checker):
    directory = Path(directory).resolve()
    start_fail, start_pending = len(audit.failures), len(audit.incomplete)
    result = audit.json(directory / 'native-linux-result.json')
    config = audit.json(directory / 'flowgraph-config.json')
    serial = audit.read(directory / 'system.platform.terminal')
    host_log = audit.read(directory / 'run.log')
    census = audit.json(directory / 'native-chi-counts.json')
    progress_text = audit.read(directory / 'native-linux-progress.jsonl')
    topo = topology(audit, directory, config, label)
    summary = {'directory': str(directory), 'topology': topo, 'result': result, 'census': None}
    def req(ok, message, pending=False): return audit.require(ok, label + ': ' + message, pending)
    # A host abort can prevent Python from writing its normal result footer.
    # Preserve that known failure even while the remaining suite is incomplete.
    host_failures = [line for line in (host_log or '').splitlines()
                     if re.search(r"Assertion .+ failed\.$|^Program aborted at tick [0-9]+$|"
                                  r"^(?:[^\n]*: )?(?:panic|fatal): |^terminate called ", line)]
    summary['hostFailureEvidence'] = host_failures
    req(not host_failures, 'gem5 host failure: ' + '; '.join(host_failures))
    lines = [line.strip() for line in (serial or '').replace('\r', '').splitlines()]
    for marker in ['CHI_LINUX_ONLINE_CPUS=0-3', 'CHI_LINUX_ONLINE_NODES=0-1', 'CHI_LINUX_BOOT_PASS']:
        req(lines.count(marker) == 1, 'missing/duplicate actual boot marker ' + marker, pending=lines.count(marker) == 0)
    if serial and re.search(r'(^|\n).*(?:TIMEOUT|timed out)', serial):
        req(False, 'guest timeout or unfinished stage', pending=True)
    if serial and re.search(r'CHI_LINUX_STAGE_FAIL|Kernel panic -|Oops:', serial):
        req(False, 'guest reported stage failure or kernel fault')
    if result is not None:
        req(result.get('fullSystem') is True and result.get('protocolOpcodeInjection') is False,
            'SE/prepared/injected result is not Linux evidence')
        req(type(result.get('configuredHarts')) is int and result['configuredHarts'] == 4
            and type(result.get('numaNodes')) is int and result['numaNodes'] == 2, 'requires four harts and two NUMA nodes')
        normal_exit = type(result.get('exitCode')) is int and result['exitCode'] == 0 and str(result.get('cause', '')).startswith('m5_exit instruction encountered')
        req(normal_exit, 'no normal successful m5_exit', pending=result.get('cause') in ('simulate() limit reached', 'no simulation interval'))
        req(result.get('bootPass') is True, 'boot not passed', pending=result.get('bootPass') is not True)
        req(result.get('status') == ('suite-passed' if label == 'core' else 'boot-passed'),
            'run status is not completed Linux execution', pending=result.get('status') in ('incomplete', 'not-verified'))
        req(result.get('bootEvidence') == {'onlineCpus': True, 'onlineNodes': True, 'bootMarker': True},
            'boot evidence incomplete', pending=result.get('bootPass') is not True)
        for key in ['gem5Sha256', 'firmwareSha256', 'dtbSha256']:
            req(bool(re.fullmatch('[0-9a-f]{64}', str(result.get(key, '')))), 'missing recorded ' + key)
        # Read/hash the actual DTB rather than accepting an arbitrary footer hash.
        dtb = directory / 'device.dtb'
        audit.read(dtb)
        req(audit.inputs[str(dtb.resolve())].get('sha256') == result.get('dtbSha256'), 'DTB bytes differ from executed-result hash')
    progress = []
    if progress_text is not None:
        try:
            progress = [unique_json(line) for line in progress_text.splitlines() if line.strip()]
            if not all(isinstance(item, dict) for item in progress): raise ValueError('progress row must be object')
        except ValueError as error:
            req(False, 'incomplete/invalid progress: ' + str(error), pending=True)
            progress = []
        if result is not None:
            req(bool(progress) and all(progress[-1].get(key) == result.get(key) for key in ('tick', 'cause', 'exitCode')),
                'final progress does not match result')
        exits = [p for p in progress if str(p.get('cause', '')).startswith('m5_exit instruction encountered') and type(p.get('exitCode')) is int and p['exitCode'] == 0]
        req(len(exits) == (8 if label == 'core' else 1), 'missing/extra normal guest stage exits', pending=len(exits) < (8 if label == 'core' else 1))
    if census is not None and config is not None:
        try:
            checked = census_checker.check(census, config)
            summary['census'] = checked
            req(checked.get('valid') is True, 'invalid actual transfer census: ' + '; '.join(checked.get('errors', [])))
            req(census.get('snapshotKind') == 'process-exit', 'census is not a final process-exit snapshot', pending=census.get('snapshotKind') != 'process-exit')
            if result is not None:
                req(bool(re.fullmatch(r'[1-9][0-9]*', str(result.get('tick', ''))))
                    and int(census['lastTick']) <= int(result['tick']), 'census tick exceeds/mismatches run final tick')
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            req(False, 'invalid census input: ' + str(error))
    if label == 'core':
        pipeline = (result or {}).get('suitePipeline', {})
        req(pipeline.get('requested') is True and pipeline.get('completed') is True, 'suite pipeline incomplete', pending=pipeline.get('completed') is not True)
        req(not pipeline.get('error'), 'suite pipeline checker error')
        req((result or {}).get('guestSuitePass') is True, 'suite pipeline has no completed PASS',
            pending=(result or {}).get('guestSuitePass') is None)
        entries = pipeline.get('stages', [])
        req(isinstance(entries, list) and [e.get('stage') for e in entries if isinstance(e, dict)] == STAGES, 'exact eight-stage sequence required', pending=len(entries) < len(STAGES))
        stage_results = audit.json(directory / 'stage-results.json', array=True)
        if stage_results is not None: req(stage_results == entries, 'stage-results differs from final pipeline')
        combined, checked_stages, logs = '', [], {}
        for stage in STAGES:
            log = audit.read(directory / (stage + '.serial.log'))
            logs[stage] = log
            report = audit.json(directory / (stage + '.result.json'))
            checked_stages.append({'stage': stage, 'report': report})
            if log is not None:
                combined += log
                stage_lines = [line.strip() for line in log.replace('\r', '').splitlines()]
                req([x for x in stage_lines if x.startswith('CHI_LINUX_STAGE_DONE=')] == ['CHI_LINUX_STAGE_DONE=' + stage], stage + ' lacks unique exact serial stage marker')
                if stage == 'boot':
                    for marker in ['CHI_LINUX_ONLINE_CPUS=0-3', 'CHI_LINUX_ONLINE_NODES=0-1', 'CHI_LINUX_BOOT_PASS']:
                        req(stage_lines.count(marker) == 1, 'boot segment lacks unique boot marker ' + marker)
                if stage == 'complete': req(stage_lines.count('CHI_LINUX_PIPELINE_COMPLETE') == 1, 'pipeline completion marker missing')
            if report is not None:
                audit.success(report, 'core/' + stage)
                try: stage_detail(audit, stage, report)
                except (ValueError, TypeError, KeyError, AttributeError) as error: req(False, stage + ' malformed report: ' + str(error))
        if all(item['report'] is not None for item in checked_stages) and serial is not None:
            req(combined.replace('\r', '') == serial.replace('\r', ''), 'stage serial evidence is not the actual complete guest serial')
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict): req(False, 'invalid pipeline entry'); continue
                req(entry.get('passed') is True, 'stage entry not passed')
                for key, suffix in [('serialFile', '.serial.log'), ('reportFile', '.result.json')]:
                    req(str(entry.get(key, '')).replace('\\', '/').split('/')[-1] == str(entry.get('stage')) + suffix, 'stage artifact reference mismatch')
        summary['stages'] = checked_stages
        pending_before_recheck = len(audit.incomplete)
        summary['stagesRecheckedFromSerial'] = recheck_stages(audit, directory, logs, config)
        req(list(summary['stagesRecheckedFromSerial']) == STAGES[1:-1], 'all six real workload logs must pass independent rechecks',
            pending=any(logs.get(stage) is None for stage in STAGES[1:-1]) or len(audit.incomplete) > pending_before_recheck)
    elif serial is not None:
        # The kernel checker parses actual build commands, every hart's work,
        # ordered boundaries, hashes and elapsed guest time; a PASS footer alone
        # can never satisfy this check.
        checked = kernel_checker.check(serial)
        summary['kernelBuildRechecked'] = checked
        if result is not None and result.get('bootPass') is True:
            audit.success(checked, label + '/actual-serial-build')
        else:
            req(False, 'kernel build execution has not completed', pending=True)
        req((result or {}).get('suitePipeline', {}).get('requested') is False, 'kernel build must be its separate guest run')
    summary['failures'] = audit.failures[start_fail:]
    summary['incomplete'] = audit.incomplete[start_pending:]
    summary['passed'] = not summary['failures'] and not summary['incomplete']
    return summary


def check(core, kernel_build):
    audit = Audit()
    audit.read(Path(__file__))
    audit.read(HERE.parent / 'native/gem5/linux_stage_gate.py')
    census_checker = audit.module('check-chi-transfer-census.py')
    kernel_checker = audit.module('check-linux-kernel-build.py')
    audit.require(Path(core).resolve() != Path(kernel_build).resolve(), 'core and kernel-build must be distinct real guest runs')
    runs = {}
    for name, path in [('core', core), ('kernel-build', kernel_build)]:
        try:
            runs[name] = run_audit(audit, Path(path), name, census_checker, kernel_checker)
        except (ValueError, TypeError, KeyError, AttributeError, configparser.Error) as error:
            audit.failures.append(f'{name}: malformed acceptance input: {error}')
            runs[name] = {'directory': str(Path(path).resolve()), 'passed': False, 'inputError': str(error)}
    counts = {name: 0 for name in census_checker.REQ.values()}
    for run in runs.values():
        census = run.get('census') or {}
        executed = run.get('result')
        linux_source = executed is None or (executed.get('fullSystem') is True and executed.get('protocolOpcodeInjection') is False)
        if census.get('valid') is True and run.get('topology', {}).get('verified') is True and linux_source:
            for name in counts:
                counts[name] += int(census['rtlRequestCounts'][name])
    missing = [name for name, value in counts.items() if value == 0]
    audit.require(not missing, 'missing actual RN-F TX REQ coverage: ' + ', '.join(missing), pending=True)
    identities = [(run.get('result') or {}).get('gem5Sha256') for run in runs.values()]
    if all(identities): audit.require(len(set(identities)) == 1, 'runs use different gem5 model binaries; cannot combine acceptance')
    status = 'INCOMPLETE' if audit.incomplete else 'FAIL' if audit.failures else 'PASS'
    return {'schemaVersion': 1, 'status': status, 'passed': status == 'PASS', 'knownFailure': bool(audit.failures),
            'checkedAtUtc': datetime.now(timezone.utc).isoformat(), 'runs': runs,
            'requestCoverage': {'selection': 'Only accepted RN-F endpoint TX REQ; HN/SN TX and all RX excluded',
                                'counts': {name: str(count) for name, count in counts.items()},
                                'complete': not missing, 'missing': missing,
                                'scope': 'Transfer-attempt coverage including retries, not a protocol/lifecycle proof.'},
            'incomplete': audit.incomplete, 'failures': audit.failures,
            'inputs': audit.inputs, 'scope': SCOPE}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core', required=True, type=Path)
    parser.add_argument('--kernel-build', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    report = check(args.core, args.kernel_build)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'incomplete': report['incomplete'],
                      'failures': report['failures'], 'missingRequests': report['requestCoverage']['missing']}, indent=2))
    raise SystemExit(0 if report['passed'] else 2 if report['status'] == 'INCOMPLETE' else 1)
