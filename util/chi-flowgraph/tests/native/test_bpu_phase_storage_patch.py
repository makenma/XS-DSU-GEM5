"""Guard and scope tests using the independently archived model05 originals.

No source installation, compiler, or simulator invocation. The exact baseline
archive is required: these tests must not silently skip when it is absent.
"""
import importlib.util
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('phase_installer', ROOT / 'scripts/apply-bpu-phase-storage.py')
INSTALL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALL)
MOD = __import__('bpu_phase_storage_patch')
BASE = Path(os.environ.get('BPU_PHASE_BASELINE', str(ROOT / 'build/bpu-phase-storage-original')))


class PhasePatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = {name: (BASE / name).read_bytes() for name in MOD.BASELINE_SHA256}
        for name, data in cls.original.items():
            if hashlib.sha256(data).hexdigest() != MOD.BASELINE_SHA256[name]:
                raise AssertionError('independent original archive SHA mismatch: ' + name)
        cls.text = {name: value.decode() for name, value in cls.original.items()}
        cls.desired = MOD.patches(cls.text)

    def git_output(self, command, **kwargs):
        if command[-2:] == ['rev-parse', 'HEAD']:
            return MOD.COMMIT + '\n'
        name = command[-1].split(':', 1)[1]
        if name != MOD.HH:
            return self.original[name]
        data = self.text[name]
        scratch = '''            // Per-prediction TAGE -> MGSC communication, not predictor
            // tables or the saved training/squash metadata. Keep finalPred
            // copies bounded by the branches in the current prediction.
            stagePred.tageInfoForMgscs.clear();
'''
        original = data.replace(scratch, '').encode()
        self.assertEqual(hashlib.sha256(original).hexdigest(), MOD.UPSTREAM_HH_SHA256)
        return original

    def fixture(self, directory):
        for name, data in self.original.items():
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def test_guard_is_idempotent_and_plan_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(INSTALL.subprocess, 'check_output', self.git_output):
            directory = Path(tmp)
            self.fixture(directory)
            original, desired = INSTALL.plan(directory)
            self.assertEqual(original, self.original)
            self.assertEqual({p: (directory / p).read_bytes() for p in original}, original)
            self.assertFalse((directory / MOD.HELPER).exists())
            for name, data in desired.items():
                (directory / name).write_bytes(data)
            self.assertEqual(INSTALL.plan(directory)[1], desired)

    def test_independent_edit_and_helper_rejected_before_any_write(self):
        for name in (*self.original, MOD.HELPER):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp, patch.object(
                    INSTALL.subprocess, 'check_output', self.git_output):
                directory = Path(tmp)
                self.fixture(directory)
                changed = directory / name
                changed.write_bytes((changed.read_bytes() if changed.exists() else b'') + b'// independent\n')
                snapshot = {str(p): p.read_bytes() for p in directory.rglob('*') if p.is_file()}
                with self.assertRaisesRegex(ValueError, 'refusing independent'):
                    INSTALL.plan(directory)
                self.assertEqual(snapshot, {str(p): p.read_bytes() for p in directory.rglob('*') if p.is_file()})

    def test_wrong_commit_rejected(self):
        with patch.object(INSTALL.subprocess, 'check_output', return_value='wrong\n'):
            with self.assertRaisesRegex(ValueError, 'unexpected gem5 commit'):
                INSTALL.plan(Path('not-used'))

    def test_missing_scratch_fix_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(INSTALL.subprocess, 'check_output', self.git_output):
            directory = Path(tmp)
            self.fixture(directory)
            (directory / MOD.HH).write_bytes(self.git_output(['show', MOD.COMMIT + ':' + MOD.HH]))
            with self.assertRaisesRegex(ValueError, 'missing scratch fix'):
                INSTALL.plan(directory)

    def test_transform_requires_all_contexts(self):
        changed = dict(self.text)
        changed[MOD.STATS] = changed[MOD.STATS].replace('    numInstCommitted++;', '    numInstCommitted += 2;')
        with self.assertRaisesRegex(ValueError, 'unexpected BPU phase patch context'):
            MOD.patches(changed)

    def test_delta_algorithms_and_prediction_body_untouched(self):
        def function(text, start, end):
            return text[text.index(start):text.index(end, text.index(start))]
        for start, end in (
                ('DecoupledBPUWithBTB::processFetchDistributions(', 'DecoupledBPUWithBTB::processBTBEntries('),
                ('DecoupledBPUWithBTB::processBTBEntries(', 'DecoupledBPUWithBTB::processPhase(')):
            old = function(self.text[MOD.STATS], start, end).replace('std::vector<int>', 'std::vector<uint64_t>')
            old = old.replace('std::pair<BTBEntry, int>', 'std::pair<BTBEntry, uint64_t>')
            self.assertEqual(old,
                             function(self.desired[MOD.STATS], start, end))
        # Entire implementation after BpTrace and before processPhase is unchanged.
        start = 'DecoupledBPUWithBTB::BpTrace::BpTrace'
        end = 'DecoupledBPUWithBTB::processFetchDistributions('
        self.assertEqual(function(self.text[MOD.STATS], start, end),
                         function(self.desired[MOD.STATS], start, end))
        self.assertEqual(self.text[MOD.CC].split('void\nDecoupledBPUWithBTB::tick()', 1)[-1],
                         self.desired[MOD.CC].split('void\nDecoupledBPUWithBTB::tick()', 1)[-1])

    def test_report_only_counters_and_tail_policy(self):
        hh, stats = self.desired[MOD.HH], self.desired[MOD.STATS]
        for counter in ('phaseIdToDump', 'subPhaseIdToDump', 'numInstCommitted', 'phaseSizeByInst'):
            self.assertIn('uint64_t ' + counter + '{', hh)
        self.assertIn('stagePred.tageInfoForMgscs.clear();', hh)
        self.assertIn('uint64_t mispredCount;', hh)
        self.assertNotIn('ByPhase.push_back', stats)
        self.assertNotIn('BySubPhase.push_back', stats)
        dump = stats[stats.index('DecoupledBPUWithBTB::dumpStats()'):stats.index('DecoupledBPUWithBTB::BpTrace::BpTrace')]
        self.assertNotIn('.append(', dump)
        self.assertIn('numInstCommitted % phaseSizeByInst', dump)
        self.assertIn('phaseDumpId = phase_storage::nextCount(phaseDumpId);', dump)


if __name__ == '__main__':
    unittest.main()
