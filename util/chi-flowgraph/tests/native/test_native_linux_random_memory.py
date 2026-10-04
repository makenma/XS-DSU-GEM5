"""Synthetic upper-layer acceptance tests, never actual guest execution evidence.

Reuse the existing topology/census fixture; mock only the already independently
tested workload parsers so these tests focus on bundle and execution binding.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import shlex
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


checker = load('native_random_acceptance_test', ROOT / 'scripts/check-native-linux-random-memory.py')
REAL_VERIFIED_MODULE = checker.verified_module
fixtures = load('existing_acceptance_fixtures', ROOT / 'scripts/test-native-linux-validation.py')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


class NativeRandomAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.run = self.root / 'run'
        fixtures.make_run(self.run, False)
        self.bundle = self.root / 'frozen'
        for name in checker.BUNDLE_FILES:
            path = self.bundle / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('synthetic fixture, never executable: ' + name).encode())
        random_manifest = {'binary': 'random-memory', 'binarySha256': sha(self.bundle / 'random/random-memory'),
                           'sourceSha256': sha(self.bundle / 'random/source.c')}
        put(self.bundle / 'random/manifest.json', random_manifest)
        put(self.bundle / 'random/manifest.original.json', random_manifest)
        put(self.bundle / 'smoke/manifest.json', {'binarySha256': sha(self.bundle / 'smoke/run.exe')})
        self.index = {'schemaVersion': 1, 'parameters': dict(checker.PARAMETERS), 'coreInputSha256': 'a' * 64,
                      'files': [{'path': name, 'sha256': sha(self.bundle / name)} for name in sorted(checker.BUNDLE_FILES)]}
        self.plan = {'schemaVersion': 1, 'kind': 'native-linux-random-memory',
                     'inputIndex': str(self.bundle / 'index.json')}
        for name in ('gem5', 'firmware', 'config'):
            path = self.root / name
            path.write_bytes(('synthetic ' + name).encode())
            self.plan[name], self.plan[name + 'Sha256'] = str(path), sha(path)
        self.plan['command'] = [self.plan['gem5'], '--outdir=' + str(self.run), self.plan['config'],
                                '--kernel', self.plan['firmware'], '--zicbom']
        self.build_path = self.root / 'build-result.json'
        self.refreeze()
        result_path = self.run / 'native-linux-result.json'
        result = json.loads(result_path.read_text())
        result.update(gem5Sha256=self.plan['gem5Sha256'], firmwareSha256=self.plan['firmwareSha256'])
        put(result_path, result)
        (self.run / 'host-exit-code.txt').write_text('0\n')
        (self.run / 'run.log').write_text('command line: ' + shlex.join(self.plan['command']) + '\n')
        self.serial = (fixtures.BOOT + 'LINUX_LITMUS_BEGIN {"profile":"smoke"}\n'
                       'SMOKE_VALID\nLINUX_LITMUS_END {"exitCode":0}\n'
                       'CHI_RANDOM_MEMORY_RUN_BEGIN ' + json.dumps({'binarySha256': random_manifest['binarySha256']}) + '\n'
                       'RANDOM_MEMORY_BEGIN {"schemaVersion":1}\nPAYLOAD_VALID\n'
                       'RANDOM_MEMORY_END {"status":"PASS"}\n'
                       'CHI_RANDOM_MEMORY_RUN_END {"exitCode":0}\n' + checker.DONE + '\n')
        self.set_serial(self.serial)
        self.called = []

        def verified_parser(audit, path, expected):
            self.called.append(path.name)
            if path.name == 'check-linux-litmus.py':
                def smoke(log, manifest):
                    good = 'SMOKE_VALID' in log
                    return {'pass': good, 'status': 'PASS' if good else 'FAIL', 'issues': [] if good else ['bad smoke'],
                            'profile': 'smoke', 'testCount': 1, 'uniqueSemanticTestCount': 1,
                            'tests': [{'expectedOutcomes': 1, 'observedOutcomes': 1}]}
                return types.SimpleNamespace(check=smoke)
            def random(log, manifest_path, params):
                self.assertIsInstance(manifest_path, Path)
                self.assertEqual(params, checker.PARAMETERS)
                self.assertNotIn('CHI_RANDOM_MEMORY_RUN_', log)
                self.assertNotIn('LINUX_LITMUS_', log)
                good = 'PAYLOAD_VALID' in log
                return {'pass': good, 'status': 'PASS' if good else 'FAIL', 'issues': [] if good else ['bad actual payload']}
            return types.SimpleNamespace(check=random)

        mock = patch.object(checker, 'verified_module', side_effect=verified_parser)
        self.mock = mock.start()
        self.addCleanup(mock.stop)

    def refreeze(self):
        identity = {key: self.index[key] for key in ('schemaVersion', 'parameters', 'coreInputSha256', 'files')}
        digest = hashlib.sha256((json.dumps(identity, sort_keys=True, indent=2) + '\n').encode()).hexdigest()
        self.index['inputSha256'] = self.plan['inputSha256'] = digest
        put(self.bundle / 'index.json', self.index)
        build = {key: self.plan[key] for key in ('firmware', 'firmwareSha256', 'inputIndex', 'inputSha256')}
        build.update(built=True, guestExecuted=False)
        put(self.build_path, build)
        self.plan.update(buildResult=str(self.build_path), buildResultSha256=sha(self.build_path))
        put(self.run / 'execution-plan.json', self.plan)

    def set_serial(self, value):
        (self.run / 'system.platform.terminal').write_text(value, encoding='utf-8')

    def audit(self):
        return checker.check(self.run)

    def test_complete_synthetic_fixture_runs_real_topology_and_census_gates(self):
        report = self.audit()
        self.assertEqual(report['status'], 'PASS', report)
        self.assertTrue(report['checks']['topology']['verified'])
        self.assertTrue(report['checks']['census']['valid'])
        self.assertEqual(self.called, ['check-linux-litmus.py', 'check-linux-random-memory.py'])

    def test_missing_bundle_never_loads_checker(self):
        (self.bundle / 'index.json').unlink()
        self.assertEqual(self.audit()['status'], 'INCOMPLETE')
        self.mock.assert_not_called()

    def test_missing_checker_never_loads_any_bundle_code(self):
        (self.bundle / 'checkers/check-linux-random-memory.py').unlink()
        self.assertEqual(self.audit()['status'], 'INCOMPLETE')
        self.mock.assert_not_called()

    def test_changed_input_sha_never_loads_checker(self):
        (self.bundle / 'guest/init').write_text('changed')
        report = self.audit()
        self.assertEqual(report['status'], 'FAIL', report)
        self.assertFalse(report['checks']['frozenBundleVerified'])
        self.mock.assert_not_called()

    def test_complete_inventory_required_even_with_recomputed_identity(self):
        self.index['files'] = [entry for entry in self.index['files'] if entry['path'] != 'checkers/check-linux-random-memory.py']
        self.refreeze()
        self.assertEqual(self.audit()['status'], 'FAIL')
        self.mock.assert_not_called()

    def test_duplicate_or_escaping_entry_rejected(self):
        for name in (self.index['files'][1]['path'], '../outside.py'):
            with self.subTest(name=name):
                old = self.index['files'][0]['path']
                self.index['files'][0]['path'] = name
                self.refreeze()
                self.assertEqual(self.audit()['status'], 'FAIL')
                self.index['files'][0]['path'] = old
        self.mock.assert_not_called()

    def test_stale_pass_json_does_not_replace_actual_serial(self):
        put(self.run / 'random-memory-result.json', {'pass': True, 'status': 'PASS'})
        self.set_serial(self.serial.replace('PAYLOAD_VALID', 'PAYLOAD_CORRUPTED'))
        report = self.audit()
        self.assertEqual(report['status'], 'FAIL')
        self.assertFalse(report['checks']['randomMemory']['pass'])

    def test_nonzero_host_exit_rejects_guest_pass(self):
        (self.run / 'host-exit-code.txt').write_text('139\n')
        self.assertEqual(self.audit()['status'], 'FAIL')

    def test_missing_or_partial_result_is_incomplete(self):
        path = self.run / 'native-linux-result.json'
        path.unlink()
        self.assertEqual(self.audit()['status'], 'INCOMPLETE')
        path.write_text('{"exitCode":')
        self.assertEqual(self.audit()['status'], 'INCOMPLETE')

    def test_program_must_be_inside_wrapper(self):
        record = 'RANDOM_MEMORY_BEGIN {"schemaVersion":1}\n'
        self.set_serial(record + self.serial.replace(record, ''))
        self.assertEqual(self.audit()['status'], 'FAIL')
        self.mock.assert_not_called()

    def test_records_after_wrapper_and_extra_final_marker_rejected(self):
        self.set_serial(self.serial + 'RANDOM_MEMORY_PAGE {}\n')
        self.assertEqual(self.audit()['status'], 'FAIL')
        self.set_serial(self.serial + checker.DONE + '\n')
        self.assertEqual(self.audit()['status'], 'FAIL')
        self.mock.assert_not_called()

    def test_unfinished_wrapper_is_incomplete(self):
        self.set_serial(self.serial[:self.serial.index('RANDOM_MEMORY_END ')])
        self.assertEqual(self.audit()['status'], 'INCOMPLETE')
        self.mock.assert_not_called()

    def test_failed_smoke_blocks_random_checker(self):
        self.set_serial(self.serial.replace('SMOKE_VALID', 'SMOKE_CORRUPTED'))
        self.assertEqual(self.audit()['status'], 'FAIL')
        self.assertEqual(self.called, ['check-linux-litmus.py'])

    def test_bad_schema_and_boolean_result_are_reports_not_exceptions(self):
        path = self.bundle / 'index.json'
        put(path, {'schemaVersion': 1, 'parameters': [], 'files': None})
        self.assertEqual(self.audit()['status'], 'FAIL')
        self.refreeze()
        result_path = self.run / 'native-linux-result.json'
        result = json.loads(result_path.read_text())
        result['exitCode'] = False
        put(result_path, result)
        self.assertEqual(self.audit()['status'], 'FAIL')
        self.set_serial(self.serial.replace('CHI_RANDOM_MEMORY_RUN_END {"exitCode":0}', 'CHI_RANDOM_MEMORY_RUN_END []'))
        self.assertEqual(self.audit()['status'], 'FAIL')

    def test_command_or_actual_model_differs_from_plan(self):
        (self.run / 'run.log').write_text('command line: stale-command\n')
        self.assertEqual(self.audit()['status'], 'FAIL')
        (self.run / 'run.log').write_text('command line: ' + shlex.join(self.plan['command']) + '\n')
        Path(self.plan['gem5']).write_bytes(b'different model')
        self.assertEqual(self.audit()['status'], 'FAIL')

    def test_checked_source_loader_refuses_changed_code_without_executing_it(self):
        source = self.root / 'checker.py'
        source.write_text('raise RuntimeError("unchecked source executed")\n')
        audit = fixtures.module.Audit()
        self.assertIsNone(REAL_VERIFIED_MODULE(audit, source, '0' * 64))
        self.assertTrue(audit.failures)
        source.write_text('BOUND_SOURCE = "synthetic checked bytes"\n')
        audit = fixtures.module.Audit()
        loaded = REAL_VERIFIED_MODULE(audit, source, sha(source))
        self.assertEqual(loaded.BOUND_SOURCE, 'synthetic checked bytes')
        self.assertFalse(audit.failures)


if __name__ == '__main__':
    unittest.main()
