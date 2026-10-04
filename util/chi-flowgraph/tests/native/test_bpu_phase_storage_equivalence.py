"""Synthetic checker mutations; these fixtures are not actual gem5 evidence."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('phase_checker', PROJECT / 'scripts/check-bpu-phase-storage-equivalence.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def write_json(path, value):
    path.write_text(json.dumps(value) + '\n', encoding='utf-8')


class Equivalence(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.program = self.root / 'program'
        self.program.mkdir()
        binaries = []
        for hart in range(4):
            file = f'native-l2-{hart}.elf'
            (self.program / file).write_bytes(f'SYNTHETIC TEST {hart}'.encode())
            binaries.append({'hart': hart, 'file': file, 'sha256': checker.sha(self.program / file)})
        self.manifest = {'protocolOpcodeInjection': False, 'rtlRequestWorkload': True,
                         'binaries': binaries, 'bpuPhaseWorkload': {
                             'minimumCompletedMainPhasesPerHart': 2,
                             'partialFinalPhaseMustBeObserved': True}}
        write_json(self.program / 'manifest.json', self.manifest)
        self.dirs = [self.root / x for x in ('legacy', 'oracle', 'candidate')]
        self.helper = self.root / 'helper.json'
        write_json(self.helper, {'schemaVersion': 1, 'status': 'PASS',
                   'sourceSha256': checker.sha(PROJECT / 'native/gem5/BpuPhaseSpool.hh'),
                   'cases': dict.fromkeys(checker.COMPONENT_CASES, True)})
        for i, directory in enumerate(self.dirs):
            directory.mkdir()
            self.make_run(directory, i)
            if i:
                for hart in range(4):
                    self.make_export(directory, hart)

    def tearDown(self):
        self.temp.cleanup()

    def make_run(self, d, i):
        (d / 'host-exit-code.txt').write_text('0\n')
        write_json(d / 'native-l2-result.json', {
            'pass': True, 'exitCode': 0, 'protocolOpcodeInjection': False,
            'cause': 'exiting with last active thread context', 'cpuExitTick': '9007199254740993',
            'drainEndTick': '9007199254740994', 'tick': '9007199254740994'})
        (d / 'run.log').write_text('\n'.join(f'{prefix}{h}_PASS' for h in range(4)
            for prefix in ('BPU_PHASE_HART_', 'NATIVE_RTL_HART_')) + '\n')
        write_json(d / 'execution-plan.json', {
            'schemaVersion': 1, 'trace': 'on', 'gem5Sha256': checker.MODEL05 if i == 0 else str(i) * 64,
            'configSha256': 'a' * 64, 'programManifestSha256': checker.sha(self.program / 'manifest.json'),
            'binaries': self.manifest['binaries'], 'command': ['gem5', '--outdir=x', 'config',
                '--elf-dir', str(self.program), '--seed', '1', '--max-ticks', '2000000000',
                '--tail-ticks', '1000000']})
        sections = []
        for hart in range(4):
            sections += [f'[system.l2{hart}]\ntype=ChiNativeL2\n',
                         f'[system.cpu{hart}.branchPred]\ntype=DecoupledBPUWithBTB\n']
        sections += [f'[system.routers{r}]\ntype=ChiRouterRefModel\n' for r in range(6)]
        (d / 'config.ini').write_text('\n'.join(sections))
        (d / 'stats.txt').write_text('---------- Begin Simulation Statistics ----------\n'
            + f'hostSeconds {i + 1}\n' + '\n'.join(f'system.cpu{h}.committedInsts 200003' for h in range(4))
            + '\n---------- End Simulation Statistics ----------\n')
        write_json(d / 'counts.json', {'snapshotKind': 'process-exit', 'counts': [{'count': '1'}]})
        write_json(d / 'raw.jsonl', {'program': self.manifest})
        for file in checker.CSV + checker.TXT:
            (d / file).write_bytes(b'' if file == 'missHistMap.txt' else b'identical legacy output\n')

    def make_export(self, d, hart):
        prefix = f'system.cpu{hart}.branchPred.'
        manifest = {'schemaVersion': 1, 'bpuName': prefix[:-1], 'numInstCommitted': 200003,
                    'phaseSizeByInst': 100000, 'subPhaseSize': 10000, 'mainPhases': 2,
                    'subPhases': 20, 'dumpId': 0, 'completeRecords': True,
                    'partialMainInsts': 3, 'partialSubInsts': 3}
        write_json(d / (prefix + 'phase-manifest.json'), manifest)
        for file in checker.CSV:
            (d / (prefix + file)).write_bytes(b'full CSV bytes including ties\n')
        for file in checker.TXT:
            owner = f'system.cpu{hart}.commit.' if file == 'misPredIndirect.txt' else prefix + 'indirectBranchPred.'
            (d / (owner + file)).write_bytes(b'' if file == 'missHistMap.txt' else b'per producer output\n')
        for file, kind in checker.PHASE_FILES.items():
            n = 20 if kind == 'branch-subphase' else 2
            frames = []
            for phase in range(n):
                if kind.startswith('branch-'):
                    rows = [[100 + j, 2, 100 + j, 2, 20, 1, 1, 0, 0] for j in range(8)]
                    frame = {'schemaVersion': 1, 'kind': kind, 'phaseId': phase,
                             'records': rows, 'taken': [[104, 2], [101, 3]]}
                elif kind.startswith('fsq-'):
                    frame = {'schemaVersion': 1, 'kind': kind, 'phaseId': phase, 'records': [1, 2, 3, 4]}
                else:
                    frame = {'schemaVersion': 1, 'kind': kind, 'phaseId': phase,
                             'records': [[100, 2, 100, 108, 0, 1, 0, 0, 0, 0, 4, 1, 0, -1, 100, -1],
                                         [108, 1, 0, 0, 0, 0, 0, None, 0, 0, 0, 0, 0, 0, 0, -1]]}
                frames.append(frame)
            (d / (prefix + file + '.records.jsonl')).write_text('\n'.join(json.dumps(x) for x in frames) + '\n')
            (d / (prefix + file)).write_text('header\n' + ''.join(f'{p},data\n' for p in range(n)))
        for file in checker.CSV + tuple(f + '.records.jsonl' for f in checker.PHASE_FILES) + ('phase-manifest.json',):
            (d / (prefix[:-1] + '.dump-0.' + file)).write_bytes((d / (prefix + file)).read_bytes())

    def check(self, helper=True):
        return checker.check(*self.dirs, self.program / 'manifest.json', self.helper if helper else None)

    def mutate_json(self, filename, mutate, directory=None):
        path = (directory or self.dirs[2]) / filename
        value = checker.read_json(path)
        mutate(value)
        write_json(path, value)

    def alter_records(self, modify, filename='topMispredictByPhase.csv.records.jsonl'):
        prefix = 'system.cpu0.branchPred.'
        path = self.dirs[2] / (prefix + filename)
        frames = [json.loads(x) for x in path.read_text().splitlines()]
        modify(frames)
        text = '\n'.join(json.dumps(x) for x in frames) + '\n'
        path.write_text(text)
        (self.dirs[2] / (prefix[:-1] + '.dump-0.' + filename)).write_text(text)

    def test_complete_finite_fixture(self):
        result = self.check()
        self.assertTrue(result['pass'], result)
        self.assertFalse(result['cpuRepeatedDumpTested'])
        self.assertEqual(len(result['legacyFiles']), 14)
        self.assertEqual([r['perInstanceArtifactCount'] for r in result['cores']], [14] * 4)

    def test_missing_run_incomplete(self):
        (self.dirs[2] / 'host-exit-code.txt').unlink()
        self.assertEqual(self.check()['status'], 'INCOMPLETE')

    def test_nonzero_exit_fail(self):
        (self.dirs[2] / 'host-exit-code.txt').write_text('-6')
        self.assertEqual(self.check()['status'], 'FAIL')

    def test_boolean_exit_code_rejected(self):
        self.mutate_json('native-l2-result.json', lambda x: x.update(exitCode=False))
        self.assertEqual(self.check()['status'], 'FAIL')

    def test_missing_hart_or_fake_banner(self):
        p = self.dirs[2] / 'run.log'
        p.write_text(p.read_text().replace('BPU_PHASE_HART_3_PASS', 'BPU_PHASE_HART_3_PASS_FAKE'))
        self.assertEqual(self.check()['status'], 'FAIL')

    def test_missing_legacy_file(self):
        (self.dirs[2] / 'missHistMap.txt').unlink()
        self.assertEqual(self.check()['status'], 'INCOMPLETE')

    def test_empty_legacy_file_legitimate(self):
        self.assertTrue(self.check()['pass'])

    def test_legacy_sort_tie_order_not_normalized(self):
        (self.dirs[2] / 'topMisPredicts.csv').write_text('different order\n')
        self.assertEqual(self.check()['status'], 'FAIL')

    def test_non_top_n_record_difference(self):
        self.alter_records(lambda x: x[0]['records'][7].__setitem__(4, 21))
        self.assertEqual(self.check()['status'], 'FAIL')

    def test_record_iteration_order_difference(self):
        self.alter_records(lambda x: x[0]['records'].reverse())
        self.assertEqual(self.check()['status'], 'FAIL')

    def test_taken_iteration_order_difference(self):
        self.alter_records(lambda x: x[0]['taken'].reverse())
        self.assertEqual(self.check()['status'], 'FAIL')

    def test_duplicate_phase(self):
        self.alter_records(lambda x: x.__setitem__(1, copy.deepcopy(x[0])))
        self.assertEqual(self.check()['status'], 'FAIL')

    def test_truncated_phase(self):
        self.alter_records(lambda x: x.pop())
        self.assertEqual(self.check()['status'], 'FAIL')

    def test_truncated_jsonl(self):
        p = self.dirs[2] / 'system.cpu0.branchPred.topMispredictByPhase.csv.records.jsonl'
        p.write_bytes(p.read_bytes().rstrip(b'\n'))
        self.assertEqual(self.check()['status'], 'FAIL')

    def test_uninitialized_bool_not_fabricated(self):
        self.alter_records(lambda x: x[0]['records'][1].__setitem__(7, 0), 'btbEntriesByPhase.csv.records.jsonl')
        self.assertEqual(self.check()['status'], 'FAIL')

    def test_missing_percore_txt(self):
        (self.dirs[2] / 'system.cpu2.commit.misPredIndirect.txt').unlink()
        self.assertEqual(self.check()['status'], 'INCOMPLETE')

    def test_short_tail_missing(self):
        self.mutate_json('system.cpu0.branchPred.phase-manifest.json', lambda x: x.update(numInstCommitted=200000))
        self.assertEqual(self.check()['status'], 'FAIL')

    def test_missing_dump_archive(self):
        (self.dirs[2] / 'system.cpu0.branchPred.dump-0.phase-manifest.json').unlink()
        self.assertEqual(self.check()['status'], 'FAIL')

    def test_archive_differs_from_latest(self):
        (self.dirs[2] / 'system.cpu0.branchPred.dump-0.topMisPredicts.csv').write_text('bad')
        self.assertEqual(self.check()['status'], 'FAIL')

    def test_host_stats_ignored_but_guest_stats_not(self):
        p = self.dirs[2] / 'stats.txt'
        p.write_text(p.read_text().replace('system.cpu2.committedInsts 200003', 'system.cpu2.committedInsts 200004'))
        self.assertEqual(self.check()['status'], 'FAIL')

    def test_duplicate_stats(self):
        p = self.dirs[2] / 'stats.txt'
        p.write_text(p.read_text() + 'system.cpu0.committedInsts 200003\n')
        self.assertEqual(self.check()['status'], 'FAIL')

    def test_raw_bytes_difference(self):
        p = self.dirs[2] / 'raw.jsonl'
        p.write_text(p.read_text() + '{"different":true}\n')
        self.assertEqual(self.check()['status'], 'FAIL')

    def test_missing_helper_not_full_pass(self):
        r = self.check(helper=False)
        self.assertTrue(r['cpuEquivalencePass'], r)
        self.assertEqual(r['status'], 'INCOMPLETE')

    def test_helper_rejects_missing_boundary_case(self):
        data = checker.read_json(self.helper)
        data['cases']['phaseCounterAcrossInt32'] = False
        write_json(self.helper, data)
        self.assertEqual(self.check()['status'], 'FAIL')

    def test_model05_identity_required(self):
        self.mutate_json('execution-plan.json', lambda x: x.update(gem5Sha256='9' * 64), self.dirs[0])
        self.assertEqual(self.check()['status'], 'FAIL')

    def test_same_run_not_independent(self):
        self.assertEqual(checker.check(self.dirs[0], self.dirs[1], self.dirs[1],
                         self.program / 'manifest.json', self.helper)['status'], 'FAIL')

    def test_trace_off_actual_plan_supported(self):
        for directory in self.dirs:
            self.mutate_json('execution-plan.json', lambda x: x.update(trace='off'), directory)
            (directory / 'raw.jsonl').unlink()
        self.assertTrue(self.check()['pass'])

    def test_mixed_trace_modes_rejected(self):
        self.mutate_json('execution-plan.json', lambda x: x.update(trace='off'))
        (self.dirs[2] / 'raw.jsonl').unlink()
        self.assertEqual(self.check()['status'], 'FAIL')

    def test_helper_source_mismatch(self):
        value = checker.read_json(self.helper)
        value['sourceSha256'] = '0' * 64
        write_json(self.helper, value)
        self.assertEqual(self.check()['status'], 'FAIL')

    def test_parser_preserves_wide_values(self):
        path = self.root / 'wide.jsonl'
        value = 2**53 + 17
        write_json(path, {'schemaVersion': 1, 'kind': 'branch-phase', 'phaseId': 0,
                   'records': [[value, 2, value, 2, 2**32 + 1, 2**31 + 1, 2**31, 1, 0]],
                   'taken': [[value, 2**32 + 1]]})
        records = checker.validate_records(path, 'branch-phase', 1)
        self.assertEqual(records[0]['records'][0][0], value)
        self.assertEqual(records[0]['records'][0][4], 2**32 + 1)

    def test_duplicate_json_field_rejected(self):
        with self.assertRaises(ValueError):
            checker.parse('{"schemaVersion":1,"schemaVersion":1}')


class PatchGuards(unittest.TestCase):
    def test_frozen_sources_exact_and_idempotent(self):
        sys.path.insert(0, str(PROJECT / 'native/gem5'))
        import bpu_phase_mirror_patch as mirror
        import bpu_phase_oracle_patch as oracle
        for module in (mirror, oracle):
            for path in module.BASELINE_SHA256:
                source = PROJECT / 'build/bpu-phase-storage-original' / path
                if not source.exists():
                    self.skipTest('frozen baseline source snapshot unavailable')
                original, desired = module.prepare(path, source.read_bytes())
                self.assertEqual(module.prepare(path, desired), (original, desired))
                with self.assertRaises(ValueError):
                    module.prepare(path, original + b'// independent modification\n')
                with self.assertRaises(ValueError):
                    module.prepare(path, desired + b'// independent modification\n')


if __name__ == '__main__':
    unittest.main()
