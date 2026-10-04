"""Synthetic positive/negative checks, not real simulator run evidence."""
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_bpu_phase_storage_equivalence as fixture

SPEC = importlib.util.spec_from_file_location('trace_checker', ROOT / 'scripts/check-bpu-phase-trace-invariance.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class TraceInvariance(unittest.TestCase):
    def setUp(self):
        self.data = fixture.Equivalence()
        self.data.setUp()
        self.on, self.off = self.data.dirs[1:]
        self.data.mutate_json('execution-plan.json', lambda x: x.update(trace='off', gem5Sha256='1' * 64))
        (self.off / 'raw.jsonl').unlink()
        for directory in (self.on, self.off):
            fixture.write_json(directory / 'flowgraph-config.json', {'actual': 'same native four-core config'})

    def tearDown(self):
        self.data.tearDown()

    def check(self):
        return MODULE.check(self.on, self.off, self.data.program / 'manifest.json')

    def test_full_pair(self):
        result = self.check()
        self.assertTrue(result['pass'], result)
        self.assertEqual(len(result['cores']), 4)
        self.assertTrue(result['traceOffRawAbsent'])
        self.assertEqual(len(result['files']), 17)

    def test_same_model_required(self):
        self.data.mutate_json('execution-plan.json', lambda x: x.update(gem5Sha256='2' * 64))
        self.assertEqual(self.check()['status'], 'FAIL')

    def test_wrong_trace_identity(self):
        self.assertEqual(MODULE.check(self.off, self.on, self.data.program / 'manifest.json')['status'], 'FAIL')

    def test_off_raw_file_forbidden(self):
        (self.off / 'raw.jsonl').write_bytes((self.on / 'raw.jsonl').read_bytes())
        self.assertEqual(self.check()['status'], 'FAIL')

    def test_percore_complete_record_difference(self):
        self.data.alter_records(lambda x: x[0]['records'][7].__setitem__(4, 21))
        self.assertEqual(self.check()['status'], 'FAIL')

    def test_percore_record_order_difference(self):
        self.data.alter_records(lambda x: x[0]['taken'].reverse())
        self.assertEqual(self.check()['status'], 'FAIL')

    def test_missing_archive_cannot_pass(self):
        (self.off / 'system.cpu3.branchPred.dump-0.topMisPredicts.csv').unlink()
        self.assertEqual(self.check()['status'], 'INCOMPLETE')

    def test_legacy_text_difference(self):
        (self.off / 'missHistMap.txt').write_text('unexpected content')
        self.assertEqual(self.check()['status'], 'FAIL')

    def test_actual_config_difference(self):
        fixture.write_json(self.off / 'flowgraph-config.json', {'actual': 'different'})
        self.assertEqual(self.check()['status'], 'FAIL')

    def test_count_bytes_not_normalized_away(self):
        path = self.off / 'counts.json'
        path.write_text(path.read_text() + '\n')
        self.assertEqual(self.check()['status'], 'FAIL')

    def test_pending_host_exit_incomplete(self):
        (self.off / 'host-exit-code.txt').unlink()
        self.assertEqual(self.check()['status'], 'INCOMPLETE')

    def test_nonzero_host_exit(self):
        (self.off / 'host-exit-code.txt').write_text('1')
        self.assertEqual(self.check()['status'], 'FAIL')


if __name__ == '__main__':
    unittest.main()
