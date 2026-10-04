"""Synthetic parser/mutation tests only; these fixtures never boot Linux."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shlex
import tempfile
import unittest


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), Path(__file__).with_name(name))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


subject = load('check-native-fs-equivalence.py')
fixture = load('test-native-linux-validation.py')


def put(path, value):
    path.write_text(json.dumps(value))


def edit(path, change):
    value = json.loads(path.read_text())
    change(value)
    put(path, value)


class FsEquivalenceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.old, self.new = self.base / 'before', self.base / 'after'
        files = [self.base / name for name in ('old.elf', 'new.elf', 'firmware.elf', 'native_l2_fs.py')]
        for path in files:
            path.write_text('synthetic, not executable: ' + path.name)
        source = self.base / 'source'
        (source / 'configs/common').mkdir(parents=True)
        for name in ('xiangshan.py', 'Caches.py'):
            (source / 'configs/common' / name).write_text('# synthetic dependency')
        self.plan = subject.prepare(self.old, self.new, *files, source, 100, 50)
        for run in self.plan['runs']:
            self.make_run(run)

    def make_run(self, run):
        path = Path(run['directory'])
        fixture.make_run(path, False)  # Reuse topology/census fixtures; no checker mocking.
        dtb_hash = subject.digest(path / 'device.dtb')
        firmware_hash = self.plan['inputSha256'][self.plan['firmware']]
        edit(path / 'flowgraph-config.json', lambda c: c['manifest'].update(
            simulationSeed=1, linux={'firmware': self.plan['firmware'], 'payload': {'sha256': firmware_hash},
                                   'dtbSha256': dtb_hash, 'numa': [{'nodeId': 0, 'harts': [0, 1]},
                                                               {'nodeId': 1, 'harts': [2, 3]}]}))
        with (path / 'config.ini').open('a') as stream:
            stream.write('[system.workload]\ntype=RiscvLinux\nobject_file=' + self.plan['firmware']
                         + '\ndtb_filename=' + str(path / 'device.dtb') + '\n')
        put(path / 'config.json', {'full_system': True, 'system': {'workload': {
            'object_file': self.plan['firmware'], 'dtb_filename': str(path / 'device.dtb')}}})
        edit(path / 'native-linux-result.json', lambda r: r.update(
            bootPass=False, bootEvidence={'onlineCpus': False, 'onlineNodes': False, 'bootMarker': False},
            cause='simulate() limit reached', status='incomplete', guestSuitePass=None,
            gem5Sha256=self.plan['inputSha256'][run['binary']], firmwareSha256=firmware_hash,
            dtbSha256=dtb_hash, simulationSeed=1, terminalFile=str(path / 'system.platform.terminal'),
            transferCensusFile=str(path / 'native-chi-counts.json')))
        (path / 'system.platform.terminal').write_bytes(b'OpenSBI synthetic test fixture\n')
        size = (path / 'system.platform.terminal').stat().st_size
        progress = [{'tick': str(t), 'cause': 'simulate() limit reached', 'exitCode': 0,
                     'hostElapsedSeconds': t / 2, 'terminalBytes': size} for t in [50, 100]]
        (path / 'native-linux-progress.jsonl').write_text(''.join(json.dumps(p) + '\n' for p in progress))
        result = json.loads((path / 'native-linux-result.json').read_text())
        (path / 'run.log').write_text('command line: ' + shlex.join(run['command']) + '\n'
                                    + ''.join('NATIVE_LINUX_PROGRESS ' + json.dumps(p) + '\n' for p in progress)
                                    + 'NATIVE_LINUX_RESULT ' + json.dumps(result) + '\n')
        (path / 'host-exit-code.txt').write_text('0\n')
        stats = '---------- Begin Simulation Statistics ----------\nsimTicks 100\nfinalTick 100\nsimInsts 17\nhostSeconds 1\n'
        for hart in range(4):
            stats += f'system.cpu{hart}.branchPred.predsOfEachStage::0 3\nsystem.cpu{hart}.branchPred.misses 1\n'
        stats += '---------- End Simulation Statistics   ----------\n'
        (path / 'stats.txt').write_text(stats * 2)

    def report(self):
        return subject.compare(self.plan)

    def assert_rejected(self):
        result = self.report()
        self.assertFalse(result['equivalent'], result)
        self.assertNotEqual(result['status'], 'EQUIVALENT')
        self.assertFalse(result['linuxBootAcceptance'])

    def test_exact_fixture_and_only_declared_host_variation(self):
        path = self.new / 'stats.txt'
        path.write_text(path.read_text().replace('hostSeconds 1', 'hostSeconds 999'))
        report = self.report()
        self.assertTrue(report['equivalent'], report)
        self.assertEqual(report['status'], 'EQUIVALENT')
        self.assertFalse(report['linuxBootAcceptance'])
        self.assertFalse(report['linuxSuiteAcceptance'])

    def test_non_host_bpu_statistic_change(self):
        path = self.new / 'stats.txt'
        path.write_text(path.read_text().replace('branchPred.misses 1', 'branchPred.misses 2'))
        self.assert_rejected()

    def test_extra_statistic_block_is_not_hidden_by_zip(self):
        path = self.new / 'stats.txt'
        path.write_text(path.read_text() + path.read_text())
        self.assert_rejected()

    def test_equal_but_truncated_statistics_rejected(self):
        for folder in (self.old, self.new):
            path = folder / 'stats.txt'
            path.write_text(path.read_text().rsplit('---------- End Simulation Statistics', 1)[0])
        self.assert_rejected()

    def test_self_consistent_census_mutation(self):
        def change(census):
            census['observations'] = str(int(census['observations']) + 1)
            census['counts'][0]['count'] = '2'
        edit(self.new / 'native-chi-counts.json', change)
        self.assert_rejected()

    def test_identical_periodic_census_cannot_pass(self):
        for folder in (self.old, self.new):
            edit(folder / 'native-chi-counts.json', lambda c: c.update(snapshotKind='periodic'))
        self.assert_rejected()

    def test_config_changes_not_hidden_by_path_replacement(self):
        edit(self.new / 'config.json', lambda c: c['system'].update(unexpectedOptimization=True))
        self.assert_rejected()

    def test_dtb_tamper_despite_same_recorded_hash(self):
        (self.new / 'device.dtb').write_bytes(b'changed DTB')
        self.assert_rejected()

    def test_serial_byte_change_is_not_normalized(self):
        with (self.new / 'system.platform.terminal').open('ab') as stream:
            stream.write(b'\r')
        self.assert_rejected()

    def test_wrong_binary_identity(self):
        edit(self.new / 'native-linux-result.json', lambda r: r.update(gem5Sha256='0' * 64))
        self.assert_rejected()

    def test_input_config_changed_after_prepare(self):
        Path(self.plan['config']).write_text('# mutated after prepare')
        self.assert_rejected()

    def test_stale_outputs_and_missing_outputs(self):
        os.utime(self.new / 'stats.txt', ns=(1, 1))
        self.assert_rejected()
        (self.new / 'stats.txt').unlink()
        report = self.report()
        self.assertEqual(report['status'], 'INCOMPLETE', report)

    def test_guest_fault_host_abort_and_command_change(self):
        (self.new / 'host-exit-code.txt').write_text('-6\n')
        self.assert_rejected()
        (self.new / 'host-exit-code.txt').write_text('0\n')
        path = self.new / 'run.log'
        path.write_text(path.read_text().replace('--max-ticks 100', '--max-ticks 101'))
        self.assert_rejected()

    def test_undeclared_result_field_difference_rejected(self):
        edit(self.new / 'native-linux-result.json', lambda r: r.update(unexpectedFutureField=1))
        self.assert_rejected()

    def test_binary_diagnostics_preserve_valid_ascii_structured_records(self):
        for folder in (self.old, self.new):
            path = folder / 'run.log'
            path.write_bytes(b'image_file_data: mapped ELF bytes \xff\x80\xfe\n' + path.read_bytes())
        report = self.report()
        self.assertTrue(report['equivalent'], report)
        self.assertIn(str(self.old / 'run.log'), report['inputSha256'])
        # Invalid bytes within a machine-readable record are still rejected.
        path = self.new / 'run.log'
        path.write_bytes(path.read_bytes().replace(b'"status": "incomplete"', b'"status": "incomplete\xff"'))
        self.assert_rejected()

    def test_structured_input_remains_strict_utf8(self):
        path = self.new / 'native-linux-result.json'
        path.write_bytes(path.read_bytes().replace(b'"status": "incomplete"', b'"status": "incomplete\xff"'))
        self.assert_rejected()

    def test_checker_revision_is_explicit_and_does_not_repin_inputs(self):
        previous = self.base / 'previous-checker.py'
        previous.write_bytes(Path(subject.__file__).read_bytes() + b'\n# earlier synthetic revision\n')
        own_path = str(Path(subject.__file__).resolve())
        self.plan['inputSha256'][own_path] = subject.digest(previous)
        before_plan = copy.deepcopy(self.plan)
        self.assert_rejected()  # There is no implicit fallback when self-pin changes.
        revision = {'previousChecker': str(previous), 'reason': 'Test only: reviewed decoding fix'}
        report = subject.compare(self.plan, checker_revision=revision)
        self.assertTrue(report['equivalent'], report)
        self.assertEqual(self.plan, before_plan)
        self.assertEqual(report['checkerRevision']['beforeSha256'], subject.digest(previous))
        self.assertEqual(report['checkerRevision']['afterSha256'], subject.digest(own_path))
        Path(self.plan['config']).write_text('# changed model configuration')
        self.assertFalse(subject.compare(self.plan, checker_revision=revision)['equivalent'])

    def test_checker_revision_requires_matching_snapshot_and_reason(self):
        previous = self.base / 'wrong-checker.py'
        previous.write_bytes(b'not the pinned checker')
        report = subject.compare(self.plan, checker_revision={'previousChecker': str(previous), 'reason': 'reviewed'})
        self.assertFalse(report['equivalent'])
        previous.write_bytes(Path(subject.__file__).read_bytes())
        report = subject.compare(self.plan, checker_revision={'previousChecker': str(previous), 'reason': ' '})
        self.assertFalse(report['equivalent'])


if __name__ == '__main__':
    unittest.main()
