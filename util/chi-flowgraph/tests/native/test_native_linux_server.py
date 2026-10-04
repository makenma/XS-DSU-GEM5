"""Portable launcher contract tests; no guest, compiler or gem5 is executed."""
import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('linux_server', ROOT / 'scripts/run-native-linux-server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / 'gem5'
        for name in ('SConstruct', 'configs/common/xiangshan.py', 'configs/common/Caches.py'):
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# unit fixture only\n')
        self.gem5 = self.source / 'build/RISCV/gem5.opt'
        self.gem5.parent.mkdir(parents=True)
        self.gem5.write_text('#!/bin/sh\nexit 99\n')
        self.gem5.chmod(0o755)
        self.work = self.base / 'work'
        self.firmware = self.work / 'opensbi-build/platform/generic/firmware/fw_payload.elf'
        self.firmware.parent.mkdir(parents=True)
        self.firmware.write_bytes(b'\x7fELF\x02\x01' + b'\0' * 12 + b'\xf3\0')

    def args(self, stage='boot'):
        return argparse.Namespace(stage=stage, gem5=self.gem5, gem5_source=self.source,
            work_dir=self.work, output=self.base / 'new-run', firmware=self.firmware,
            suite_inputs=None, random_build_result=None, rootfs_manifest=None, dry_run=True)

    def test_fixed_commands_all_stages(self):
        for stage in server.STAGES:
            cmd = server.stage_command(stage, self.gem5, Path('config.py'), self.firmware,
                                       self.base / stage, Path('index.json'))
            for key, value in {'--numa-nodes': '2', '--sets': '512', '--assoc': '8', '--mshrs': '32',
                               '--hn-entries': '64', '--slc-lines': '8192', '--seed': '1',
                               '--progress-ticks': '1000000000', '--max-ticks': str(server.LIMITS[stage])}.items():
                self.assertEqual(cmd[cmd.index(key) + 1], value)
            self.assertEqual(cmd[cmd.index('--mem-size') + 1], '4GiB' if stage == 'kernel' else '1GiB')
            self.assertEqual('--suite-pipeline' in cmd, stage == 'core')
            self.assertEqual('--uncached-test-ram' in cmd, stage in ('core', 'random'))
            if stage == 'kernel':
                self.assertEqual(cmd[cmd.index('--dtb-addr') + 1], '0x17fe00000')

    def test_core_cannot_skip_suite(self):
        with self.assertRaises(ValueError):
            server.stage_command('core', self.gem5, 'cfg', self.firmware, 'out')
        with self.assertRaises(ValueError):
            server.prepare(self.args('core'))

    def test_random_cannot_skip_built_bundle(self):
        with self.assertRaises(ValueError):
            server.prepare(self.args('random'))

    def test_kernel_cannot_skip_rootfs_provenance(self):
        with self.assertRaises(ValueError):
            server.prepare(self.args('kernel'))

    def test_existing_output_refused(self):
        args = self.args(); args.output.mkdir()
        with self.assertRaises(ValueError):
            server.prepare(args)

    def test_firmware_wrong_isa_refused(self):
        self.firmware.write_bytes(b'\x7fELF\x02\x01' + b'\0' * 12 + b'\x3e\0')
        with self.assertRaises(ValueError):
            server.prepare(self.args())

    def test_prepare_records_new_identity_and_no_execution(self):
        plan = server.prepare(self.args())
        self.assertEqual(plan['gem5Sha256'], server.sha(self.gem5))
        self.assertFalse(plan['guestExecuted'])
        self.assertFalse(plan['completeLinuxAcceptance'])
        self.assertFalse(plan['originalModel10Certification'])
        self.assertEqual(plan['modelCertification'], 'new-server-build-unclassified')
        self.assertEqual(plan['cwd'], str(server.PROJECT))

    def test_dry_run_never_popen_or_pass(self):
        with patch.object(server, 'parser') as parser, patch.object(server.subprocess, 'Popen') as popen:
            parser.return_value.parse_args.return_value = self.args()
            self.assertEqual(server.main(), 0)
            popen.assert_not_called()
        out = self.args().output
        self.assertTrue((out / 'execution-plan.json').is_file())
        self.assertFalse((out / 'host-pid.txt').exists())
        self.assertFalse((out / 'server-stage-result.json').exists())

    def test_source_root_explicit_and_environment(self):
        self.assertEqual(server.source_root(self.source), self.source.resolve())
        with patch.dict(os.environ, {'CHI_GEM5_DIR': str(self.source)}):
            self.assertEqual(server.source_root(None), self.source.resolve())
        with self.assertRaises(ValueError):
            server.source_root(self.base / 'absent')

    def test_changed_input_refused(self):
        plan = server.prepare(self.args())
        self.gem5.write_text('different binary')
        with self.assertRaises(ValueError):
            server.verify_pins(plan)

    def test_host_failure_cannot_reach_stage_checker(self):
        plan = server.prepare(self.args())
        with patch.object(server, 'load') as load:
            for code in (-6, 1, 2, 124, None, False):
                with self.subTest(code=code), self.assertRaises(ValueError):
                    server.check_stage(plan, code)
            load.assert_not_called()

    def test_actual_failed_wait_is_recorded(self):
        args = self.args(); args.dry_run = False
        with patch.object(server, 'parser') as parser, patch.object(server.subprocess, 'Popen') as popen:
            parser.return_value.parse_args.return_value = args
            popen.return_value.pid = 123
            popen.return_value.wait.return_value = -6
            self.assertEqual(server.main(), 1)
        receipt = server.read(args.output / 'host-completion.json')
        report = server.read(args.output / 'server-stage-result.json')
        self.assertEqual(receipt['hostExitCode'], -6)
        self.assertEqual(receipt['source'], 'python-subprocess-wait')
        self.assertFalse(report['stagePassed'])
        self.assertFalse(report['completeLinuxAcceptance'])

    def test_bundle_hash_path_and_duplicate_rejected(self):
        folder = self.base / 'bundle'; folder.mkdir()
        path = folder / 'index.json'
        source = folder / 'payload'; source.write_text('fixture')
        good = {'schemaVersion': 1, 'files': [{'path': 'payload', 'sha256': server.sha(source)}], 'stages': {}}
        for mutation in ('hash', 'path', 'duplicate'):
            item = copy.deepcopy(good)
            if mutation == 'hash': item['files'][0]['sha256'] = '0' * 64
            if mutation == 'path': item['files'][0]['path'] = '../outside'
            if mutation == 'duplicate': item['files'] *= 2
            item['inputSha256'] = __import__('hashlib').sha256(server.canonical(item)).hexdigest()
            path.write_text(json.dumps(item))
            with self.subTest(mutation=mutation), self.assertRaises((ValueError, OSError)):
                server.verify_bundle(path, 'core')

    def test_no_options_for_smaller_cpu_or_workload(self):
        options = {flag for action in server.parser()._actions for flag in action.option_strings}
        for flag in ('--max-ticks', '--num-cpus', '--clock', '--samples', '--iterations', '--sets', '--no-census'):
            self.assertNotIn(flag, options)

    def test_smoke_six_names_five_hashes_and_fixed_runtime(self):
        # The recorded official six-test smoke has one semantic duplicate.
        manifest = {'profile': 'smoke', 'testCount': 6, 'uniqueSemanticTestCount': 5,
                    'tests': [{'testHash': str(i)} for i in (0, 1, 2, 3, 4, 4)],
                    'runtime': {'availableCpus': 4, 'affinityCpus': [0, 1, 2, 3],
                                'forcedAffinity': True, 'runs': 1, 'samplesPerInstance': 8}}
        server.verify_litmus_inventory(manifest, 'litmus-smoke')
        for key, value in (('samplesPerInstance', 1), ('runs', 2), ('availableCpus', 1), ('forcedAffinity', False)):
            wrong = copy.deepcopy(manifest); wrong['runtime'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                server.verify_litmus_inventory(wrong, 'litmus-smoke')
        wrong = copy.deepcopy(manifest); wrong['uniqueSemanticTestCount'] = 6
        with self.assertRaises(ValueError):
            server.verify_litmus_inventory(wrong, 'litmus-smoke')


if __name__ == '__main__':
    unittest.main()
