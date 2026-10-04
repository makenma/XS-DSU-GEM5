"""Execution wiring/identity tests only; no guest or fake Linux PASS execution."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import native_linux_run08 as common


def load(name):
    spec = importlib.util.spec_from_file_location('test_' + name.replace('-', '_'), ROOT / 'scripts' / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DRIVER = load('prepare-native-linux-08.py')
FINAL = load('check-native-linux-complete-08.py')


class WiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def plan(self):
        source = self.root / 'source'
        for filename in ('xiangshan.py', 'Caches.py', 'FUScheduler.py'):
            path = source / 'configs/common' / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# fixture configuration\n')
        model = self.root / 'gem5'; model.write_bytes(b'fixture-only-not-executable')
        spec = {'gem5': str(model), 'gem5Sha256': common.sha(model), 'gem5Source': str(source),
                'coreFirmware': str(self.root / 'core-fw'), 'kernelFirmware': str(self.root / 'kernel-fw'),
                'suiteInputs': str(self.root / 'index.json'), 'randomBuildResult': str(self.root / 'build.json'),
                'outputs': {key: str(self.root / key) for key in
                            ('core', 'kernelBuild', 'randomMemory', 'state', 'acceptance', 'supervisorLog')}}
        inventory = common.python_inventory(source)
        plan = {'schemaVersion': 1, 'kind': 'native-linux-model08-launch', 'status': 'READY', 'ready': True,
                'spec': spec, 'project': str(common.PROJECT), 'problems': [], 'guestExecuted': False,
                'commands': common.commands(spec), 'pinnedSha256': {str(model): common.sha(model), **inventory},
                'configPythonSha256': inventory}
        for name in common.CHECKERS:
            path = ROOT / 'scripts' / name
            plan['pinnedSha256'][str(path.resolve())] = common.sha(path)
        for name in ('native_l2_fs.py', 'linux_stage_gate.py', 'BpuPhaseSpool.hh'):
            path = ROOT / 'native/gem5' / name
            plan['pinnedSha256'][str(path.resolve())] = common.sha(path)
        plan['identitySha256'] = common.identity(plan)
        return plan

    def test_missing_evidence_never_launches_or_creates_output(self):
        plan = {'status': 'INCOMPLETE', 'ready': False, 'problems': ['CBO reports missing']}
        path = self.root / 'plan.json'; path.write_text(json.dumps(plan))
        with patch.object(DRIVER.subprocess, 'Popen') as popen, patch.object(DRIVER.subprocess, 'call') as call:
            for group in ('core', 'random'):
                with self.assertRaisesRegex(ValueError, 'not ready'):
                    DRIVER.launch(path, group)
            popen.assert_not_called(); call.assert_not_called()
        self.assertEqual([p.name for p in self.root.iterdir()], ['plan.json'])

    def output_paths(self, root):
        return {key: str(root / ('unused-test-' + key)) for key in
                ('core', 'kernelBuild', 'randomMemory', 'state', 'acceptance', 'supervisorLog')}

    def test_original_output_root_still_accepted_without_creating_outputs(self):
        outputs = self.output_paths(ROOT / 'build/linux-validation')
        common.validate_output_paths(outputs)
        self.assertFalse(any(Path(value).exists() for value in outputs.values()))

    @unittest.skipUnless(common.WSL_OUTPUT_ROOT.is_absolute(), 'real WSL paths require POSIX')
    def test_real_wsl_output_root_accepted_without_creating_outputs(self):
        outputs = self.output_paths(Path('/root/chi-flowgraph-work/linux-validation/runs/model08'))
        common.validate_output_paths(outputs)
        self.assertFalse(any(Path(value).exists() for value in outputs.values()))
        outputs['core'] = str(ROOT / 'build/linux-validation/unused-test-core')
        with self.assertRaisesRegex(ValueError, 'one allowed'):
            common.validate_output_paths(outputs)

    def test_output_escape_duplicate_nested_and_other_root_rejected(self):
        root = ROOT / 'build/linux-validation'
        for bad in (root / '../elsewhere/unused-test-core', self.root / 'core',
                    root / 'unused-test-kernelBuild', root / 'unused-test-kernelBuild/core'):
            with self.subTest(path=str(bad)):
                outputs = self.output_paths(root); outputs['core'] = str(bad)
                with self.assertRaisesRegex(ValueError, 'one allowed'):
                    common.validate_output_paths(outputs)

    def test_existing_output_is_rejected_unless_revalidating_running_plan(self):
        with patch.object(common, 'PROJECT', self.root):
            root = self.root / 'build/linux-validation'
            root.mkdir(parents=True)
            outputs = self.output_paths(root)
            Path(outputs['state']).write_text('existing state')
            with self.assertRaisesRegex(ValueError, 'existing'):
                common.validate_output_paths(outputs)
            common.validate_output_paths(outputs, check_output_freshness=False)

    @unittest.skipUnless(common.WSL_OUTPUT_ROOT.is_absolute(), 'symlink test requires POSIX')
    def test_output_symlink_escape_is_rejected(self):
        with patch.object(common, 'PROJECT', self.root / 'project'):
            root = common.PROJECT / 'build/linux-validation'
            root.mkdir(parents=True)
            (root / 'unused-test-core').symlink_to(self.root / 'escaped')
            with self.assertRaisesRegex(ValueError, 'one allowed'):
                common.validate_output_paths(self.output_paths(root))

    def test_plan_hash_rejects_stale_pass_edit(self):
        plan = self.plan(); plan['spec']['gem5Sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'identity changed'):
            common.verify_plan(plan)

    def test_rehashed_ready_without_regressions_cannot_launch(self):
        plan = self.plan()
        with self.assertRaisesRegex(ValueError, 'revalidated prerequisites'):
            common.verify_plan(plan)

    def test_deleted_mandatory_pin_even_rehashed_is_rejected(self):
        plan = self.plan()
        del plan['pinnedSha256'][str((ROOT / 'scripts/check-native-linux-validation.py').resolve())]
        plan['identitySha256'] = common.identity(plan)
        with self.assertRaisesRegex(ValueError, 'mandatory'):
            common.verify_plan(plan)

    def test_missing_evidence_pin_rejected_by_full_revalidation(self):
        plan = self.plan()
        fresh = copy.deepcopy(plan)
        fresh['pinnedSha256'][str(self.root / 'required-report')] = 'a'*64
        with patch.object(common, 'prepare', return_value=fresh):
            with self.assertRaisesRegex(ValueError, 'inventory differs'):
                common.verify_plan(plan)

    def test_config_inventory_added_removed_and_modified(self):
        for change in ('added', 'removed', 'modified'):
            with self.subTest(change=change):
                plan = self.plan()
                root = Path(plan['spec']['gem5Source']) / 'configs'
                existing = root / 'common/Caches.py'
                if change == 'added': (root / 'new_import.py').write_text('changed\n')
                elif change == 'removed': existing.unlink()
                else: existing.write_text('changed\n')
                with self.assertRaises(ValueError): common.verify_plan(plan)
                if (root / 'new_import.py').exists(): (root / 'new_import.py').unlink()

    def test_pinned_report_hash_changed(self):
        plan = self.plan(); report = self.root / 'report.json'; report.write_text('{"passed":true}')
        plan['pinnedSha256'][str(report)] = common.sha(report); plan['identitySha256'] = common.identity(plan)
        report.write_text('{"passed":false}')
        with self.assertRaisesRegex(ValueError, 'pinned input changed'): common.verify_plan(plan)

    def test_core_exit_must_exist_and_be_exact_zero(self):
        receipt = self.root / 'host-exit-code.txt'
        with self.assertRaisesRegex(ValueError, 'missing'):
            common.wait_host_exit(receipt, attempts=2, sleep=lambda _: None)
        for invalid in ('1', '-6', 'false', '00', '0\n1'):
            receipt.write_text(invalid)
            with self.assertRaisesRegex(ValueError, 'nonzero/malformed'):
                common.wait_host_exit(receipt, sleep=lambda _: None)
        receipt.write_text('0\n'); common.wait_host_exit(receipt, sleep=lambda _: None)

    def test_core_exit_receipt_waits_for_real_writer(self):
        receipt = self.root / 'host-exit-code.txt'
        attempts = []
        def arrive(_):
            attempts.append(1); receipt.write_text('0\n')
        common.wait_host_exit(receipt, attempts=2, sleep=arrive)
        self.assertEqual(len(attempts), 1)

    def test_exit_receipt_is_atomically_published(self):
        receipt = self.root / 'host-exit-code.txt'
        original = Path.write_text
        observed = []
        def recording(path, text, *args, **kwargs):
            observed.append((path.name, receipt.exists()))
            return original(path, text, *args, **kwargs)
        with patch.object(Path, 'write_text', recording):
            common.write_host_exit(receipt, 0)
        self.assertEqual(observed, [('host-exit-code.txt.tmp', False)])
        self.assertEqual(receipt.read_text(), '0\n')
        self.assertFalse(receipt.with_name(receipt.name+'.tmp').exists())

    def test_fake_focused_report_for_old_model_rejected(self):
        path = self.root / 'cbo.json'
        path.write_text(json.dumps({'passed': True, 'case': 'clean', 'hostReturnCode': 0,
                                    'gem5Sha256': common.MODEL05}))
        with self.assertRaisesRegex(ValueError, 'candidate'):
            common.check_cbo(path, 'clean', self.root / 'gem5', 'a'*64)

    def test_duplicate_json_and_bool_expected_session_rejected(self):
        with self.assertRaisesRegex(ValueError, 'duplicate'): common.parse('{"passed":true,"passed":false}')
        for session in (True, 0, -1, '123'):
            with self.assertRaises(ValueError): FINAL.check('a', 'b', 'c', 'd', session, 'unused')

    def base_report(self):
        return {'status': 'PASS', 'passed': True, 'knownFailure': False, 'gem5Sha256': 'a'*64,
                'failures': [], 'incomplete': []}

    def final_fixture(self):
        dirs = [self.root / name for name in ('core', 'kernel', 'random')]
        for directory in dirs:
            directory.mkdir(); (directory / 'host-exit-code.txt').write_text('0\n')
        spec = {'gem5Sha256': 'a'*64, 'outputs': dict(zip(('core', 'kernelBuild', 'randomMemory'), map(str, dirs)))}
        return dirs, spec

    def test_final_keeps_original_checks_and_explicit_session(self):
        dirs, spec = self.final_fixture()
        calls = []
        original = types.SimpleNamespace(DRIVER_SESSION=30470)
        def check(*args):
            calls.append((args, original.DRIVER_SESSION))
            report = self.base_report(); report['incomplete'].append('original 12-opcode gate incomplete')
            return report
        original.check = check
        with patch.object(FINAL, 'module', return_value=original), patch.object(FINAL, 'read', return_value={}), \
                patch.object(FINAL, 'verify_plan', return_value=spec):
            report = FINAL.check(*dirs, 'actual-receipt', 81818, 'launch-plan')
        self.assertEqual(calls, [(tuple(dirs)+('actual-receipt',), 81818)])
        self.assertFalse(report['passed']); self.assertEqual(report['status'], 'INCOMPLETE')

    def test_final_kernel_or_random_host_failure_cannot_pass(self):
        dirs, spec = self.final_fixture()
        original = types.SimpleNamespace(check=lambda *args: self.base_report())
        for index in (1, 2):
            (dirs[index] / 'host-exit-code.txt').write_text('-6\n')
            with patch.object(FINAL, 'module', return_value=original), patch.object(FINAL, 'read', return_value={}), \
                    patch.object(FINAL, 'verify_plan', return_value=spec):
                report = FINAL.check(*dirs, 'actual-receipt', 81818, 'launch-plan')
            self.assertFalse(report['passed']); self.assertTrue(report['knownFailure'])
            (dirs[index] / 'host-exit-code.txt').write_text('0\n')

    def test_supervisor_gate_precedes_any_kernel_popen(self):
        source = (ROOT / 'scripts/continue-native-linux-08.py').read_text()
        self.assertLess(source.index("wait_host_exit(args.core / 'host-exit-code.txt')"),
                        source.index('process = subprocess.Popen(command'))
        self.assertIn("write_host_exit(args.kernel_build / 'host-exit-code.txt'", source)
        self.assertIn("verify_plan(launch)", source)


if __name__ == '__main__':
    unittest.main()
