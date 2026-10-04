"""Synthetic acceptance-checker fixtures only; never evidence of a Linux run."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('acceptance', Path(__file__).with_name('check-native-linux-validation.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
BOOT = 'CHI_LINUX_ONLINE_CPUS=0-3\nCHI_LINUX_ONLINE_NODES=0-1\nCHI_LINUX_BOOT_PASS\n'
RN, HN, SN = [0, 256, 16, 272], [4, 260, 20, 276], [136, 152]
OPS = [0x26, 7, 12, 8, 9, 10, 13, 27, 23, 66, 4, 28]
REAL_RECHECK = module.recheck_stages


def put(path, value):
    path.write_text(json.dumps(value))


def edit(path, change):
    data = json.loads(path.read_text()); change(data); put(path, data)


def kernel_log():
    begin = {'profile': 'tinyconfig-smp4', 'sourceVersion': '6.6.52', 'jobs': 4,
             'onlineCpus': '0-3', 'architecture': 'riscv64', 'freshOutput': True,
             'compilerTarget': 'riscv64-linux-gnu', 'compilerVersion': '13.3.0', 'startSeconds': 1}
    end = {'profile': 'tinyconfig-smp4', 'exitCode': 0, 'target': 'Image', 'jobs': 4,
           'elfClass': 'ELF64', 'elfMachine': 'RISC-V', 'imageBytes': 4096, 'objectFiles': 3,
           'imageSha256': 'a' * 64, 'vmlinuxSha256': 'b' * 64, 'configSha256': 'c' * 64,
           'makeLogSha256': 'd' * 64, 'endSeconds': 2}
    return (BOOT + 'CHI_KERNEL_BUILD_BEGIN ' + json.dumps(begin) + '\nCHI_KERNEL_BUILD_CPUSTAT_BEFORE\n'
            + ''.join(f'cpu{i} 1 0 1 10\n' for i in range(4))
            + 'CHI_KERNEL_BUILD_CONFIG_READY\n  CC a.o\n  LD vmlinux\n  OBJCOPY arch/riscv/boot/Image\n'
            + 'CHI_KERNEL_BUILD_CPUSTAT_AFTER\n' + ''.join(f'cpu{i} 2 0 2 11\n' for i in range(4))
            + 'CHI_KERNEL_BUILD_END ' + json.dumps(end) + '\n')


def make_run(path, core):
    path.mkdir()
    (path / 'run.log').write_text('command line: synthetic-fixture-only\n')
    manifest = {'profile': 'h-native-256', 'source': {'fullSystem': True, 'nativeChi': True},
                'topology': [], 'endpointPorts': {}}
    for role, prefix, ids, field in [('RN-F', 'l2', RN, 'chi_side'), ('HN-F', 'hn', HN, 'rxport'), ('SN-F', 'sn', SN, 'chi_side')]:
        for i, node in enumerate(ids):
            manifest['topology'].append({'id': role + str(i), 'nodeId': node, 'role': role})
            manifest['endpointPorts'][f'system.{prefix}{i}.{field}'] = node
    put(path / 'flowgraph-config.json', {'manifest': manifest})
    ini = '[root]\nfull_system=true\n[system]\nnum_cpus=4\nmem_mode=timing\nxiangshan_system=true\n'
    for i in range(4):
        ini += f'[system.cpu{i}]\ntype=BaseO3CPU\ncpu_id={i}\n'
        for cache in ['icache', 'dcache', 'itb_walker_cache', 'dtb_walker_cache']:
            ini += f'[system.cpu{i}.{cache}]\ntype=Cache\nmem_side=system.l1bus{i}.cpu_side_ports[0]\n'
        ini += (f'[system.l2{i}]\ntype=ChiNativeL2\nnode_id={RN[i]}\ncpu_side=system.l1bus{i}.mem_side_ports[0]\n'
                f'chi_side=system.routers{i}.device_ports[0]\n[system.l1bus{i}]\n'
                f'mem_side_ports=system.l2{i}.cpu_side system.iobus.cpu_side_ports[{i}]\n'
                f'[system.hn{i}]\ntype=ChiExtendedHome\nnode_id={HN[i]}\nrxport=system.routers{i}.local_ports[4]\n')
    for i in range(2): ini += (f'[system.sn{i}]\ntype=ChiNativeMemory\nnode_id={SN[i]}\n'
                              f'chi_side=system.routers{i}.local_ports[8]\nmem_side=system.membus.cpu_side_ports[{i}]\n')
    for i in range(6): ini += f'[system.routers{i}]\ntype=ChiRouterRefModel\nlocal_x={i % 3}\nlocal_y={i // 3}\n'
    (path / 'config.ini').write_text(ini)
    (path / 'device.dtb').write_bytes(b'synthetic fixture DTB, not bootable')
    result = {'fullSystem': True, 'protocolOpcodeInjection': False, 'configuredHarts': 4, 'numaNodes': 2,
              'bootPass': True, 'bootEvidence': {'onlineCpus': True, 'onlineNodes': True, 'bootMarker': True},
              'tick': '100', 'exitCode': 0, 'cause': 'm5_exit instruction encountered',
              'status': 'suite-passed' if core else 'boot-passed', 'gem5Sha256': 'a' * 64,
              'firmwareSha256': 'b' * 64, 'dtbSha256': hashlib.sha256((path / 'device.dtb').read_bytes()).hexdigest(),
              'guestSuitePass': True if core else None,
              'suitePipeline': {'requested': core, 'completed': core, 'error': None, 'stages': []}}
    reports = {
        'boot': {'passed': True}, 'complete': {'passed': True},
        'cbo': {'passed': True, 'fullCoverage': True, 'phases': ['disabled', 'enabled']},
        'uncached': {'passed': True, 'samples': 64, 'cpus': [0, 1, 2, 3]},
        'torture': {'passed': True, 'onlineCpus': '0-3', 'onlineNodes': '0-1',
                    'cases': [{'test': kind, 'passed': True, 'work': {'count': 1}}
                              for kind in ['rcu', 'spin_lock', 'rw_lock', 'mutex_lock', 'rwsem_lock']]},
        'pthreads': {'pass': True, 'status': 'PASS', 'fullSuiteRequested': True, 'requiredTests': module.PTHREADS,
                     'recordedTests': module.PTHREADS, 'iterations': 128, 'testStatuses': {'PASS': 11},
                     'results': [{'test': name, 'status': 'PASS'} for name in module.PTHREADS]},
    }
    for profile, count in [('smoke', 1), ('large', 1000)]:
        reports['litmus-' + profile] = {'pass': True, 'status': 'PASS', 'profile': profile,
                                       'testCount': count, 'uniqueSemanticTestCount': count,
                                       'tests': [{'expectedOutcomes': 2, 'observedOutcomes': 2} for _ in range(count)]}
    serial = ''
    if core:
        for stage in module.STAGES:
            segment = (BOOT if stage == 'boot' else '') + ('CHI_LINUX_PIPELINE_COMPLETE\n' if stage == 'complete' else '') + 'CHI_LINUX_STAGE_DONE=' + stage + '\n'
            serial += segment
            (path / (stage + '.serial.log')).write_text(segment)
            put(path / (stage + '.result.json'), reports[stage])
            result['suitePipeline']['stages'].append({'stage': stage, 'passed': True,
                'serialFile': str(path / (stage + '.serial.log')), 'reportFile': str(path / (stage + '.result.json'))})
        put(path / 'stage-results.json', result['suitePipeline']['stages'])
    else: serial = kernel_log()
    (path / 'system.platform.terminal').write_text(serial)
    put(path / 'native-linux-result.json', result)
    (path / 'native-linux-progress.jsonl').write_text(''.join(json.dumps({'tick': str(100 if i == (7 if core else 0) else i + 1),
        'cause': result['cause'], 'exitCode': 0}) + '\n' for i in range(8 if core else 1)))
    rows = [{'boundary': 'system.l20.chi_side', 'observation': 'TX', 'channel': 'REQ', 'srcid': RN[0],
             'tgtid': HN[0], 'opcode': op, 'count': '1', 'firstTick': '1', 'lastTick': '1'} for op in OPS]
    put(path / 'native-chi-counts.json', {'schemaVersion': '1.0', 'source': 'accepted native endpoint transfers',
        'snapshotKind': 'process-exit', 'lastTick': '1', 'observations': str(len(rows)), 'counts': rows})


class AcceptanceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.core, self.kernel = Path(self.temp.name) / 'core', Path(self.temp.name) / 'kernel'
        make_run(self.core, True); make_run(self.kernel, False)
        # These tests isolate aggregate gates. The real kernel parser and
        # census parser still run. Actual core-serial rechecking is exercised
        # separately below; this mock is never used by the CLI/runtime checker.
        mock = patch.object(module, 'recheck_stages', return_value={stage: {'passed': True} for stage in module.STAGES[1:-1]})
        mock.start(); self.addCleanup(mock.stop)

    def report(self): return module.check(self.core, self.kernel)

    def test_complete_synthetic_fixture(self):
        report = self.report()
        self.assertEqual(report['status'], 'PASS', report)

    def test_missing_stage_and_missing_run_are_incomplete(self):
        (self.core / 'cbo.result.json').unlink()
        self.assertEqual(self.report()['status'], 'INCOMPLETE')
        report = module.check(self.core, Path(self.temp.name) / 'not-run')
        self.assertEqual(report['status'], 'INCOMPLETE')

    def test_periodic_or_timeout_is_incomplete(self):
        edit(self.core / 'native-chi-counts.json', lambda x: x.update(snapshotKind='periodic'))
        self.assertEqual(self.report()['status'], 'INCOMPLETE')
        edit(self.core / 'native-linux-result.json', lambda x: x.update(cause='simulate() limit reached', bootPass=False, status='incomplete'))
        self.assertEqual(self.report()['status'], 'INCOMPLETE')

    def test_host_assert_before_result_remains_known_failure(self):
        (self.core / 'native-linux-result.json').unlink()
        (self.core / 'run.log').write_text(
            "gem5.opt: build/RISCV/cpu/o3/issue_queue.cc:446: Assertion `!inst->isNonSpeculative()' failed.\n"
            'Program aborted at tick 103161925000\n')
        report = self.report()
        self.assertEqual(report['status'], 'INCOMPLETE')
        self.assertTrue(report['knownFailure'])
        self.assertEqual(len(report['runs']['core']['hostFailureEvidence']), 2)

    def test_host_fatal_cannot_accept_stale_success(self):
        (self.core / 'run.log').write_text('build/RISCV/example.cc:12: fatal: test failed\n')
        self.assertEqual(self.report()['status'], 'FAIL')
        (self.core / 'run.log').write_text('info: panic handler initialized; fatality counter=0\n')
        self.assertEqual(self.report()['status'], 'PASS')

    def test_kernel_footer_alone_cannot_pass(self):
        (self.kernel / 'system.platform.terminal').write_text(BOOT + 'CHI_KERNEL_BUILD_PASS\n')
        report = self.report()
        self.assertEqual(report['status'], 'FAIL')
        self.assertFalse(report['runs']['kernel-build']['kernelBuildRechecked']['passed'])

    def test_se_or_bridge_cannot_pass(self):
        edit(self.core / 'native-linux-result.json', lambda x: x.update(fullSystem=False))
        self.assertNotEqual(self.report()['status'], 'PASS')
        text = (self.core / 'config.ini').read_text().replace('type=ChiNativeL2', 'type=Cache2ChiBridge', 1)
        (self.core / 'config.ini').write_text(text)
        self.assertFalse(self.report()['runs']['core']['topology']['verified'])

    def test_hn_and_rx_opcodes_do_not_supply_rn_coverage(self):
        for path in [self.core, self.kernel]:
            def change(census):
                for row in census['counts']:
                    row.update(boundary='system.hn0.rxport', observation='RX')
            edit(path / 'native-chi-counts.json', change)
        report = self.report()
        self.assertEqual(report['status'], 'INCOMPLETE')
        self.assertEqual(len(report['requestCoverage']['missing']), 12)
        for path in [self.core, self.kernel]:
            def change(census):
                for row in census['counts']:
                    row.update(boundary='system.hn0.rxport', observation='TX', srcid=HN[0], tgtid=SN[0])
            edit(path / 'native-chi-counts.json', change)
        self.assertEqual(len(self.report()['requestCoverage']['missing']), 12)

    def test_mixed_model_binaries_cannot_pass(self):
        edit(self.kernel / 'native-linux-result.json', lambda x: x.update(gem5Sha256='f' * 64))
        self.assertEqual(self.report()['status'], 'FAIL')

    def test_false_report_and_wrong_order_rejected(self):
        edit(self.core / 'cbo.result.json', lambda x: x.update(passed=1))
        self.assertNotEqual(self.report()['status'], 'PASS')
        edit(self.core / 'native-linux-result.json', lambda x: x['suitePipeline']['stages'].reverse())
        self.assertNotEqual(self.report()['status'], 'PASS')


class SerialRecheckTest(unittest.TestCase):
    def test_changed_real_litmus_histogram_rejects_stale_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / 'archive'; archive.mkdir()
            (archive / 'checkers').mkdir()
            (archive / 'stages/litmus-smoke').mkdir(parents=True)
            binary = b'synthetic binary bytes; this is a checker fixture only'
            binary_hash = hashlib.sha256(binary).hexdigest()
            manifest = {'schemaVersion': 1, 'suite': 'official-diy7-riscv-linux', 'profile': 'smoke',
                        'testCount': 1, 'uniqueSemanticTestCount': 1, 'excludedTests': [],
                        'binarySha256': binary_hash,
                        'runtime': {'samplesPerInstance': 1, 'runs': 1, 'availableCpus': 4,
                                    'affinityCpus': [0, 1, 2, 3], 'forcedAffinity': True},
                        'tests': [{'name': 'chi_fixture', 'testHash': 'a' * 32, 'threads': 2,
                                   'modelObservation': 'Never', 'modelAllowedStates': ['0:x=0;']}]}
            payload = {'checkers/check-linux-litmus.py': Path(__file__).with_name('check-linux-litmus.py').read_bytes(),
                       'stages/litmus-smoke/manifest.json': json.dumps(manifest).encode(),
                       'stages/litmus-smoke/run.exe': binary}
            for name, data in payload.items(): (archive / name).write_bytes(data)
            identity = {'schemaVersion': 1,
                        'files': [{'path': name, 'sha256': hashlib.sha256(data).hexdigest()} for name, data in sorted(payload.items())],
                        'stages': {'litmus-smoke': {'checker': 'checkers/check-linux-litmus.py',
                            'manifest': 'stages/litmus-smoke/manifest.json', 'binary': 'stages/litmus-smoke/run.exe'}}}
            index = dict(identity, execution='NOT_RUN', inputSha256=hashlib.sha256(
                (json.dumps(identity, sort_keys=True, indent=2, ensure_ascii=True) + '\n').encode()).hexdigest())
            put(archive / 'index.json', index); put(root / 'suite-inputs-index.json', index)
            (root / 'run.log').write_text(f"command line: gem5 --suite-inputs '{archive / 'index.json'}'\n", encoding='utf-8')
            log = ('LINUX_LITMUS_BEGIN ' + json.dumps({'profile': 'smoke', 'binarySha256': binary_hash, 'onlineCpus': 4})
                   + '\nTest chi_fixture Forbidden\nHistogram (1 states)\n2 :> 0:x=0;\n'
                   + 'Positive: 0, Negative: 2\nObservation chi_fixture Never 0 2\nHash=' + 'a' * 32
                   + '\nLINUX_LITMUS_END {"exitCode":0}\n')
            audit = module.Audit()
            report = REAL_RECHECK(audit, root, {'litmus-smoke': log}, {})
            self.assertIn('litmus-smoke', report, (audit.failures, audit.incomplete, report))
            self.assertTrue(report['litmus-smoke']['pass'], (audit.failures, report))
            self.assertFalse(audit.failures)
            put(root / 'litmus-smoke.result.json', report['litmus-smoke'])  # now stale
            changed = log.replace('2 :> 0:x=0;', '3 :> 0:x=0;')
            audit = module.Audit()
            rejected = REAL_RECHECK(audit, root, {'litmus-smoke': changed}, {})
            self.assertFalse(rejected['litmus-smoke']['pass'])
            self.assertTrue(audit.failures)


if __name__ == '__main__':
    unittest.main()
