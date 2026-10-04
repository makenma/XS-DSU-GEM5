"""Pure checker/wiring tests. Fixture bytes are never executed as a guest."""
import copy
import hashlib
import json
from pathlib import Path
import shlex
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import native_linux_run_v2 as v2


class CheckerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value) + '\n')

    def test_model_sha_absent_placeholder_wrong_actual_and_wrong_path(self):
        binary = self.root / 'gem5.opt'; binary.write_bytes(b'unit fixture, not executable')
        spec = {'profile': v2.PROFILE, 'modelId': v2.MODEL_ID, 'gem5': str(binary), 'gem5Sha256': v2.sha(binary)}
        with patch.object(v2, 'MODEL_PATH', binary):
            v2.model_check(spec, Path)
            for digest in (None, '', '0'*64, 'f'*64, 'a'*63, 'a'*63+'b', v2.legacy.MODEL05):
                with self.subTest(digest=digest), self.assertRaises(ValueError):
                    v2.model_check({**spec, 'gem5Sha256': digest}, Path)
            with self.assertRaisesRegex(ValueError, 'candidate02'):
                v2.model_check({**spec, 'gem5': str(self.root / 'other')}, Path)
            with self.assertRaisesRegex(ValueError, 'profile'):
                v2.model_check({**spec, 'profile': 'small'}, Path)

    def outputs(self, root):
        return {key: str(root / key) for key in v2.OUTPUT_NAMES}

    def test_outputs_reject_08_root_duplicate_nested_and_existing(self):
        with patch.object(v2, 'PROJECT', self.root):
            root = self.root / 'build/linux-validation/model10'; root.mkdir(parents=True)
            good = self.outputs(root); v2.outputs_check(good)
            for bad in (root.parent / 'old08', root / 'core/nested', root / 'randomMemory'):
                with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'model10 root'):
                    v2.outputs_check({**good, 'core': str(bad)})
            (root / 'state').write_text('existing')
            with self.assertRaisesRegex(ValueError, 'existing'):
                v2.outputs_check(good)
            v2.outputs_check(good, fresh=False)

    @unittest.skipUnless(sys.platform != 'win32', 'POSIX symlink fixture')
    def test_outputs_do_not_resolve_whitelist_escape(self):
        with patch.object(v2, 'PROJECT', self.root / 'project'):
            root = v2.PROJECT / 'build/linux-validation/model10'
            root.parent.mkdir(parents=True); target = self.root / 'elsewhere'; target.mkdir()
            root.symlink_to(target)
            with self.assertRaisesRegex(ValueError, 'model10 root'):
                v2.outputs_check(self.outputs(root))

    def test_real_fixed_commands_keep_all_workload_and_memory_parameters(self):
        spec = {'gem5': '/candidate/gem5', 'gem5Sha256': 'a'*64, 'gem5Source': '/source',
                'coreFirmware': '/core-fw', 'kernelFirmware': '/kernel-fw', 'suiteInputs': '/suite/index',
                'randomBuildResult': '/random/build', 'outputs': self.outputs(self.root)}
        cmd = v2.commands(spec)
        self.assertEqual(cmd, v2.legacy.commands(spec))
        self.assertIn('--suite-pipeline', cmd['core'])
        self.assertEqual(cmd['kernel'][cmd['kernel'].index('--mem-size')+1], '4GiB')
        self.assertEqual(cmd['kernel'][cmd['kernel'].index('--max-ticks')+1], '10000000000000000')
        self.assertEqual(v2.FROZEN, v2.legacy.FROZEN)

    def test_duplicate_json_and_bad_numbers_rejected(self):
        for value in ('{"ready":true,"ready":false}', '{"tick":NaN}'):
            with self.assertRaises(ValueError):
                v2.parse(value)

    def test_incomplete_plan_never_creates_output_or_starts_process(self):
        path = self.root / 'plan.json'; self.write(path, {'ready': False, 'status': 'INCOMPLETE'})
        with patch.object(v2.subprocess, 'Popen') as popen, self.assertRaisesRegex(ValueError, 'not ready'):
            v2.launch_serial(path)
        popen.assert_not_called()
        self.assertEqual([p.name for p in self.root.iterdir()], ['plan.json'])

    def test_rehashed_ready_cannot_replace_real_prerequisite_validation(self):
        spec = {'gem5Source': str(self.root), 'gem5': str(self.root / 'model'), 'gem5Sha256': 'a'*64}
        required = {str((v2.PROJECT / 'scripts' / name).resolve()): 'a'*64 for name in v2.CHECKERS}
        required.update({str((v2.PROJECT / 'native/gem5' / name).resolve()): 'a'*64 for name in
                         ('native_l2_fs.py', 'linux_stage_gate.py', 'BpuPhaseSpool.hh')})
        required[spec['gem5']] = 'a'*64
        plan = {'schemaVersion': 2, 'kind': 'native-linux-model-launch', 'profile': v2.PROFILE,
                'modelId': v2.MODEL_ID, 'ready': True, 'status': 'READY', 'problems': [], 'guestExecuted': False,
                'project': str(v2.PROJECT), 'stageOrder': list(v2.STAGES), 'spec': spec, 'commands': {},
                'configPythonSha256': {}, 'pinnedSha256': required}
        plan['identitySha256'] = v2.identity(plan)
        with patch.object(v2, 'commands', return_value={}), patch.object(v2, 'hardware_inventory', return_value={}), \
             patch.object(v2, 'sha', return_value='a'*64), \
             patch.object(v2, 'prepare', return_value={'ready': False, 'problems': ['missing fetch case']}):
            with self.assertRaisesRegex(ValueError, 'revalidated prerequisites'):
                v2.verify_plan(plan)
            del plan['pinnedSha256'][str((v2.PROJECT / 'scripts/check-native-linux-complete-10.py').resolve())]
            plan['identitySha256'] = v2.identity(plan)
            with self.assertRaisesRegex(ValueError, 'mandatory'):
                v2.verify_plan(plan)

    def test_changed_hardware_defaults_cannot_be_reauthorized_by_dynamic_pins(self):
        source = self.root / 'gem5'; root = source / 'configs/common'; root.mkdir(parents=True)
        for name in ('xiangshan.py', 'Caches.py', 'FUScheduler.py'):
            (root / name).write_text('# frozen fixture\n')
        inv = v2.legacy.python_inventory(source)
        relative = {Path(k).relative_to(source / 'configs').as_posix(): v for k, v in inv.items()}
        expected = hashlib.sha256(json.dumps(relative, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        with patch.object(v2, 'CONFIG_INVENTORY_SHA256', expected):
            self.assertEqual(v2.hardware_inventory(source), inv)
            (root / 'Caches.py').write_text('# smaller caches even before preparation\n')
            with self.assertRaisesRegex(ValueError, 'accepted full model08'):
                v2.hardware_inventory(source)

    def test_bounded_fs_cannot_reduce_budget_or_substitute_firmware(self):
        plan = v2.read(ROOT / 'build/linux-validation/fs-phase-storage-equivalence-inputs.json')
        # Preserve its actual immutable workload values; only normalize the
        # config filename for this unit test's current host (Windows or WSL).
        old_config = plan['config']; config = str(v2.PROJECT / 'native/gem5/native_l2_fs.py')
        plan['config'] = config; plan['inputSha256'][config] = plan['inputSha256'].pop(old_config)
        for row in plan['runs']:
            row['command'][2] = config
        spec = {'gem5': plan['runs'][1]['binary'], 'gem5Sha256': plan['inputSha256'][plan['runs'][1]['binary']],
                'coreFirmware': plan['firmware'], 'gem5Source': plan['environment']['CHI_GEM5_DIR'],
                'suiteInputs': plan['runs'][0]['command'][-1]}
        # The existing WSL paths are evidence strings here; no binaries run.
        with patch.object(v2, 'absolute', side_effect=lambda value: value), \
             patch.object(v2, 'sha', return_value=plan['inputSha256'][spec['suiteInputs']]):
            v2.bounded_fs_scope(spec, plan)
            for key, value in [('ticks', '1000000000'), ('progressTicks', '1'),
                               ('suitePipeline', False), ('expectedHostExitCode', 0), ('firmware', '/other')]:
                changed = copy.deepcopy(plan); changed[key] = value
                with self.subTest(field=key), self.assertRaises(ValueError):
                    v2.bounded_fs_scope(spec, changed)
            changed = copy.deepcopy(plan); changed['runs'][0]['command'] = changed['runs'][0]['command'][:-3]
            with self.assertRaisesRegex(ValueError, 'command reduced'):
                v2.bounded_fs_scope(spec, changed)

    def fixture(self, case='changed-layout'):
        """Minimal on-disk execution evidence; topology has a separate strict checker."""
        directory = self.root / case; directory.mkdir()
        firmware = directory / 'fixture.elf'; firmware.write_bytes(b'not an ELF: checker fixture only')
        binary = self.root / 'model'; binary.write_bytes(b'not an executable')
        fwsha, modelsha = v2.sha(firmware), v2.sha(binary)
        group, _, slot = v2.FETCH_CASES[case]
        checks = (['passed', 'trapPcChecked', 'causeChecked', 'tvalChecked'] if group == 'page'
                  else ['trainingChecked', 'mappedCodeChecked', 'trapPcChecked'])
        if group == 'page' and case != 'straddling-instruction-fault':
            checks += ['returnValueChecked', 'returnAddressChecked']
        variant = {'case': case, 'sha256': fwsha, 'elf': firmware.name, 'requiredGuestChecks': checks}
        manifest = {'variants': [variant], 'notLinuxSuite': True, 'hartsConfigured': 4,
                    'activeTestHarts': [0], 'otherHarts': 'WFI', 'kind': 'fixture-only'}
        config = v2.PROJECT / 'native/gem5/native_l2_fs.py'
        command = [str(binary), '--outdir=' + str(directory), '--remote-gdb-port=0',
                   '--debug-flags=Fetch,FetchFault,DecoupleBP,Decode', '--debug-file=fetch-stream-debug.log',
                   str(config), '--firmware', str(firmware), '--max-ticks', '1000000000',
                   '--progress-ticks', '100000000', '--zicbom']
        self.write(directory / 'execution-plan.json', {'gem5': str(binary), 'gem5Sha256': modelsha,
                   'firmwareSha256': fwsha, 'configSha256': v2.sha(config), 'builderManifest': manifest, 'command': command})
        final = {'gem5Sha256': modelsha, 'exitCode': 0, 'cause': 'm5_exit instruction encountered', 'tick': '41581000'}
        self.write(directory / 'native-linux-result.json', final)
        (directory / 'host-exit-code.txt').write_text('0\n')
        (directory / 'run.log').write_text('command line: ' + shlex.join(command) + '\n')
        guest = {'case': case, 'passed': True, **dict.fromkeys(checks, True)}
        serial = ''
        if group == 'page':
            cause, epc, tval = (12, 0x40000ffe, 0x40001000) if case == 'straddling-instruction-fault' else (9, 0x800000a0, 0)
            guest['expectedTrap'] = cause
            serial = f'FETCH_PAGE_TRAP cause={cause:#x} epc={epc:#x} tval={tval:#x} a0=0x2a\n'
        serial += 'FETCH_STREAM_RESULT ' + json.dumps(guest) + '\n'
        (directory / 'system.platform.terminal').write_text(serial)
        debug = '' if slot is None else f'100: system.cpu0.fetch: Predecode invalid BTB slot {slot:#x}: decoded [0x40000000,0x40000004)\n'
        if case == 'straddling-instruction-fault':
            debug += '200: system.cpu0.fetch: Demand fetch fault: pc=0x40000ffe missing=0x40001000 request=0x40001000 cause=12\n'
        (directory / 'fetch-stream-debug.log').write_text(debug)
        self.write(directory / 'flowgraph-config.json', {})
        self.write(directory / 'raw.jsonl', {'type': 'manifest', 'linux': {'payload': {'sha256': fwsha}}})
        report = {'passed': True, 'case': case, 'notLinuxSuite': True, 'kind': manifest['kind'],
                  'gem5Sha256': modelsha, 'firmwareSha256': fwsha, 'configSha256': v2.sha(config),
                  'hostReturnCode': 0, 'originalFetchRangeAssertion': False, 'simulatorExit': final,
                  'requiredGuestChecks': checks, 'guestResults': [guest], 'traceFirmwareIdentityChecked': True,
                  'artifacts': {name: {'bytes': (directory / name).stat().st_size, 'sha256': v2.sha(directory / name)}
                                for name in ('run.log', 'system.platform.terminal', 'fetch-stream-debug.log')}}
        path = directory / 'fetch-stream-result.json'; self.write(path, report)
        audit = lambda: types.SimpleNamespace(failures=[], incomplete=[])
        topology = types.SimpleNamespace(Audit=audit, topology=lambda *args: {'verified': True})
        self.enterContext(patch.dict(v2.FETCH_CASES, {case: (group, fwsha, slot)}))
        self.enterContext(patch.object(v2, 'module', return_value=topology))
        return path, case, binary, modelsha, manifest, firmware

    def test_fetch_good_evidence_and_bad_model_identity(self):
        args = self.fixture(); self.assertTrue(v2.fetch_case_check(*args)['passed'])
        row = v2.read(args[0]); row['gem5Sha256'] = '0'*64; self.write(args[0], row)
        with self.assertRaisesRegex(ValueError, 'report differs'):
            v2.fetch_case_check(*args)

    def test_fetch_pass_cache_does_not_mask_guest_failure(self):
        args = self.fixture(); p = args[0].parent / 'system.platform.terminal'
        p.write_text(p.read_text().replace('"passed": true', '"passed": false'))
        with self.assertRaisesRegex(ValueError, 'actual guest'):
            v2.fetch_case_check(*args)

    def test_fetch_failure_or_timeout_never_passes(self):
        args = self.fixture(); directory = args[0].parent
        for filename, value in [('host-exit-code.txt', '1'), ('host-exit-code.txt', '-6')]:
            (directory / filename).write_text(value)
            with self.assertRaisesRegex(ValueError, 'host exit'):
                v2.fetch_case_check(*args)
        (directory / 'host-exit-code.txt').write_text('0')
        final = v2.read(directory / 'native-linux-result.json'); final['cause'] = 'simulate() limit reached'
        self.write(directory / 'native-linux-result.json', final)
        with self.assertRaisesRegex(ValueError, 'complete normally'):
            v2.fetch_case_check(*args)

    def test_fetch_duplicate_guest_row_and_missing_check_rejected(self):
        args = self.fixture(); p = args[0].parent / 'system.platform.terminal'
        text = p.read_text(); p.write_text(text + text)
        with self.assertRaisesRegex(ValueError, 'actual guest'):
            v2.fetch_case_check(*args)
        p.write_text(text)
        row = v2.read(args[0]); row['requiredGuestChecks'] = []; self.write(args[0], row)
        with self.assertRaisesRegex(ValueError, 'weakened'):
            v2.fetch_case_check(*args)

    def test_fetch_missing_actual_recovery_fails_even_when_guest_passes(self):
        args = self.fixture(); (args[0].parent / 'fetch-stream-debug.log').write_text('no recovery\n')
        with self.assertRaisesRegex(ValueError, 'not exercised'):
            v2.fetch_case_check(*args)

    def test_fetch_preserves_valid_slot(self):
        args = self.fixture('same-layout'); self.assertTrue(v2.fetch_case_check(*args)['passed'])
        (args[0].parent / 'fetch-stream-debug.log').write_text('Predecode invalid BTB slot 0x40000002: decoded\n')
        with self.assertRaisesRegex(ValueError, 'preservation'):
            v2.fetch_case_check(*args)

    def test_fetch_corrupt_firmware_raw_manifest_and_topology_rejected(self):
        args = self.fixture(); directory = args[0].parent
        original = args[-1].read_bytes(); args[-1].write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'ELF differs'):
            v2.fetch_case_check(*args)
        args[-1].write_bytes(original)
        self.write(directory / 'raw.jsonl', {'type': 'manifest', 'linux': {'payload': {'sha256': '0'*64}}})
        with self.assertRaisesRegex(ValueError, 'raw firmware'):
            v2.fetch_case_check(*args)
        self.write(directory / 'raw.jsonl', {'type': 'manifest', 'linux': {'payload': {'sha256': v2.sha(args[-1])}}})
        checker = types.SimpleNamespace(Audit=lambda: types.SimpleNamespace(failures=[], incomplete=[]),
                                        topology=lambda *args: {'verified': False})
        with patch.object(v2, 'module', return_value=checker), self.assertRaisesRegex(ValueError, 'topology'):
            v2.fetch_case_check(*args)

    def test_fetch_actual_command_cannot_reduce_budget_or_use_other_firmware(self):
        args = self.fixture(); path = args[0].parent / 'execution-plan.json'
        plan = v2.read(path); plan['command'][plan['command'].index('--max-ticks')+1] = '1'; self.write(path, plan)
        with self.assertRaisesRegex(ValueError, 'execution identity'):
            v2.fetch_case_check(*args)

    def test_page_success_and_exact_fault_registers(self):
        for case in ('page-tail-return', 'last-halfword-return', 'straddling-instruction-fault'):
            with self.subTest(case=case):
                args = self.fixture(case); self.assertTrue(v2.fetch_case_check(*args)['passed'])
                serial = args[0].parent / 'system.platform.terminal'
                text = serial.read_text(); serial.write_text(text.replace('tval=0x', 'tval=0x1', 1))
                with self.assertRaisesRegex(ValueError, 'cause/EPC/tval'):
                    v2.fetch_case_check(*args)

    def test_correct_trap_footer_cannot_hide_missing_precise_demand_fault(self):
        args = self.fixture('straddling-instruction-fault')
        original = v2.fetch_case_check(*args)
        self.assertEqual(json.loads(json.dumps(original)), original)
        debug = args[0].parent / 'fetch-stream-debug.log'
        for value in ('', 'Demand fetch fault: pc=0x40000fc0 missing=0x40001000 request=0x40001000 cause=12\n',
                      'Demand fetch fault: pc=0x40000ffe missing=0x40001002 request=0x40001000 cause=12\n'):
            debug.write_text(value)
            with self.assertRaisesRegex(ValueError, 'precisely the demanded'):
                v2.fetch_case_check(*args)

    def test_fetch_complete_case_sets_are_not_inferred_from_supplied_passes(self):
        expected_pre = {k for k, v in v2.FETCH_CASES.items() if v[0] != 'page'}
        expected_page = {k for k, v in v2.FETCH_CASES.items() if v[0] == 'page'}
        self.assertEqual(len(expected_pre), 8); self.assertEqual(len(expected_page), 3)
        # Exercise the actual fixed manifest/source inventory before rejecting an
        # incomplete supplied run set. No fixture reports are treated as runs.
        pin = lambda p: Path(p).resolve()
        with self.assertRaisesRegex(ValueError, 'complete fetchPredecode'):
            v2.fetch_regressions({'regressions': {'fetchPredecode': {}, 'fetchPageFault': {}}}, pin, lambda p: None)

    def launch_fixture(self):
        project = self.root / 'project'; output = project / 'build/linux-validation/model10'
        plan = {'spec': {'outputs': self.outputs(output), 'gem5Sha256': 'a'*64}, 'identitySha256': 'b'*64}
        path = self.root / 'plan.json'; self.write(path, plan)
        self.enterContext(patch.object(v2, 'PROJECT', project))
        self.enterContext(patch.object(v2, 'verify_plan', return_value=plan['spec']))
        return path, plan, output

    def test_serial_wiring_runs_all_stages_in_order_without_concurrency(self):
        path, plan, output = self.launch_fixture(); calls = []
        def run(p, location, stage, log, started):
            calls.append('run:' + stage); started(100 + len(calls)); return 0
        def check(p, stage):
            calls.append('check:' + stage); Path(p['spec']['outputs'][stage]).mkdir(); return {'fixture': True}
        checker = types.SimpleNamespace(check=lambda *args: {'status': 'PASS', 'passed': True, 'knownFailure': False})
        with patch.object(v2, '_run_stage', side_effect=run), patch.object(v2, 'check_stage', side_effect=check), \
             patch.object(v2, 'module', return_value=checker), patch.object(v2.subprocess, 'Popen') as popen:
            self.assertEqual(v2.launch_serial(path), 0)
        popen.assert_not_called()
        self.assertEqual(calls, [s for stage in v2.STAGES for s in ('run:' + stage, 'check:' + stage)])
        state = v2.read(output / 'state')
        self.assertIsNone(state['driverSessionId'])
        self.assertEqual(state['status'], 'stages-passed-awaiting-external-driver-completion')

    def test_core_failure_never_launches_random_or_kernel(self):
        path, plan, output = self.launch_fixture()
        with patch.object(v2, '_run_stage', return_value=-6) as run, patch.object(v2, 'check_stage') as check:
            self.assertEqual(v2.launch_serial(path), 2)
        self.assertEqual(run.call_count, 1); check.assert_not_called()
        self.assertFalse((output / 'randomMemory').exists()); self.assertFalse((output / 'kernelBuild').exists())
        self.assertFalse(v2.read(output / 'state')['passed'])

    def test_core_checker_failure_never_launches_downstream(self):
        path, plan, output = self.launch_fixture()
        with patch.object(v2, '_run_stage', return_value=0) as run, \
             patch.object(v2, 'check_stage', side_effect=ValueError('missing real litmus outcomes')):
            self.assertEqual(v2.launch_serial(path), 2)
        self.assertEqual(run.call_count, 1)
        self.assertIn('missing real litmus', v2.read(output / 'state')['error'])

    def test_existing_state_cannot_resume_or_start_second_driver(self):
        path, plan, output = self.launch_fixture(); output.mkdir(parents=True)
        (output / 'state').write_text('{}')
        with patch.object(v2.subprocess, 'Popen') as popen, self.assertRaisesRegex(ValueError, 'existing'):
            v2.launch_serial(path)
        popen.assert_not_called()

    def test_plan_mutation_while_core_runs_stops_all_downstream(self):
        path, plan, output = self.launch_fixture()
        def run(*args):
            path.write_text('{}'); return 0
        with patch.object(v2, '_run_stage', side_effect=run) as runner, patch.object(v2, 'check_stage') as checker:
            self.assertEqual(v2.launch_serial(path), 2)
        self.assertEqual(runner.call_count, 1); checker.assert_not_called()
        self.assertIn('plan changed', v2.read(output / 'state')['error'])

    def test_final_requires_explicit_actual_session_before_reading_anything(self):
        for session in (None, True, 0, -1, '71505'):
            with self.subTest(session=session), self.assertRaisesRegex(ValueError, 'externally observed'):
                v2.final_check(None, None, None, None, session, None)

    def final_fixture(self):
        directories = {stage: self.root / stage for stage in v2.STAGES}
        for path in directories.values():
            path.mkdir(); (path / 'host-exit-code.txt').write_text('0\n')
            self.write(path / 'serial-stage-audit.json', {'passed': True, 'fixtureOnly': True})
        state_path = self.root / 'state.json'
        spec = {'outputs': {**{k: str(p) for k, p in directories.items()}, 'state': str(state_path)},
                'gem5Sha256': 'a'*64}
        plan = {'spec': spec, 'identitySha256': 'b'*64}
        path = self.root / 'plan.json'; self.write(path, plan)
        rows = [{'stage': stage, 'status': 'PASS', 'passed': True, 'hostReturnCode': 0, 'hostPid': 100+i,
                 'startedAtUtc': f'2026-10-04T00:00:{i*3:02d}+00:00',
                 'exitedAtUtc': f'2026-10-04T00:00:{i*3+1:02d}+00:00',
                 'checkedAtUtc': f'2026-10-04T00:00:{i*3+2:02d}+00:00'} for i, stage in enumerate(v2.STAGES)]
        self.write(state_path, {'launchPlanIdentitySha256': 'b'*64, 'gem5Sha256': 'a'*64,
                   'stageOrder': list(v2.STAGES), 'stages': rows, 'guestExecuted': True, 'passed': True,
                   'status': 'stages-passed-awaiting-external-driver-completion'})
        receipt = self.root / 'driver.json'
        self.write(receipt, {'launchPlanIdentitySha256': 'b'*64, 'randomMemory': str(directories['randomMemory'])})
        base = {'gem5Sha256': 'a'*64, 'passed': True, 'status': 'PASS', 'failures': [], 'incomplete': []}
        checker = types.SimpleNamespace(DRIVER_SESSION=None)
        def check(*args):
            self.assertEqual(checker.DRIVER_SESSION, 1234)
            return copy.deepcopy(base)
        checker.check = check
        self.enterContext(patch.object(v2, 'module', return_value=checker))
        self.enterContext(patch.object(v2, 'verify_plan', return_value=spec))
        args = (directories['core'], directories['kernelBuild'], directories['randomMemory'], receipt, 1234, path)
        return args, state_path

    def test_final_rechecks_each_actual_stage_even_with_cached_pass_files(self):
        args, state = self.final_fixture()
        with patch.object(v2, 'check_stage', return_value={}) as check:
            report = v2.final_check(*args)
        self.assertTrue(report['passed'])
        self.assertEqual([call.args[1] for call in check.call_args_list], list(v2.STAGES))
        with patch.object(v2, 'check_stage', side_effect=ValueError('actual serial no longer passes')):
            report = v2.final_check(*args)
        self.assertFalse(report['passed']); self.assertTrue(report['knownFailure'])
        self.assertIn('actual serial', report['failures'][0])

    def test_final_rejects_stage_overlap_or_missing_stage(self):
        args, state = self.final_fixture(); original = v2.read(state)
        for change in ('overlap', 'missing'):
            row = copy.deepcopy(original)
            if change == 'overlap': row['stages'][1]['startedAtUtc'] = row['stages'][0]['startedAtUtc']
            else: row['stages'].pop()
            self.write(state, row)
            with patch.object(v2, 'check_stage', return_value={}):
                report = v2.final_check(*args)
            self.assertFalse(report['passed'])

    def test_final_rejects_nonzero_or_missing_actual_host_exit(self):
        args, state = self.final_fixture(); receipt = args[2] / 'host-exit-code.txt'
        receipt.write_text('-6')
        with patch.object(v2, 'check_stage', return_value={}):
            self.assertFalse(v2.final_check(*args)['passed'])
            receipt.unlink()
            self.assertFalse(v2.final_check(*args)['passed'])

    def test_final_rejects_receipt_from_other_plan_or_random_run(self):
        args, state = self.final_fixture()
        for receipt in ({'launchPlanIdentitySha256': '0'*64, 'randomMemory': str(args[2])},
                        {'launchPlanIdentitySha256': 'b'*64, 'randomMemory': str(self.root / 'old-random')}):
            self.write(args[3], receipt)
            with patch.object(v2, 'check_stage', return_value={}):
                self.assertFalse(v2.final_check(*args)['passed'])


if __name__ == '__main__':
    unittest.main()
