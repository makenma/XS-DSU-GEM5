"""Synthetic mutation tests of Linux result validation, not Linux execution evidence."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('linux_suite_checker', ROOT / 'scripts/check-linux-suite.py')
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class LinuxSuiteCheckerTests(unittest.TestCase):
    def fixture(self):
        manifest = {'suite': 'native-linux-pthreads', 'threads': 4, 'sourceSha256': 'a' * 64,
                    'tests': CHECKER.TESTS, 'assemblyChecks': dict.fromkeys(
                        ('rv64Elf', 'explicitAmoAqRl', 'explicitLrAcquire', 'explicitScRelease', 'linuxEcall'), True)}
        begin = {'schemaVersion': 1, 'suite': 'native-linux-pthreads', 'buildId': 'a' * 64,
                 'threadsRequested': 4, 'iterations': 2, 'onlineCpus': 4,
                 'affinityCpus': [0, 1, 2, 3], 'selectedTests': CHECKER.TESTS}
        results = []
        for name in CHECKER.TESTS:
            results.append({'test': name, 'status': 'PASS', 'threadsRequested': 4, 'threadsStarted': 4,
                            'iterations': 2, 'operationsExpected': 8, 'operations': 8, 'observed': 8,
                            'errors': 0, 'threadOperations': [2] * 4, 'threadErrors': [0] * 4,
                            'threadCpuIds': [0, 1, 2, 3], 'threadPinErrors': [0] * 4,
                            'metrics': {'futexSlept': 4, 'futexWaitCalls': 5, 'futexWakeCalls': 8,
                                        'scFailures': 3, 'copyBytesPerOperation': 64, 'checksum': 63488,
                                        'aliasMappings': 2, 'pressureBytesPerThread': 65536,
                                        'numaMemoryNodes': 2, 'migrationPagesAttempted': 8,
                                        'migrationPagesMoved': 8, 'migrationErrno': 0, 'migrationReason': 'none'}})
        end = {'status': 'PASS', 'testsCompleted': 11, 'passed': 11, 'failed': 0, 'unsupported': 0, 'exitCode': 0}
        return {'manifest': manifest, 'begin': begin, 'results': results, 'end': end, 'extra': ''}

    def check(self, mutate=None):
        data = self.fixture()
        if mutate:
            mutate(data)
        lines = ['LINUX_SUITE_BEGIN ' + json.dumps(data['begin'])]
        lines += ['LINUX_TEST_RESULT ' + json.dumps(result) for result in data['results']]
        if data['end'] is not None:
            lines.append('LINUX_SUITE_END ' + json.dumps(data['end']))
        log = '\n'.join(lines) + '\n' + data['extra']
        return CHECKER.check(log, data['manifest'], 2)

    def rejected(self, mutate):
        report = self.check(mutate)
        self.assertFalse(report['pass'], report)
        return report

    def test_valid_complete_fixture_passes(self):
        report = self.check()
        self.assertTrue(report['pass'], report['issues'])
        self.assertTrue(report['fullSuiteRequested'])

    def test_each_missing_test_rejected(self):
        for name in CHECKER.TESTS:
            with self.subTest(test=name):
                self.rejected(lambda d: d.__setitem__('results', [r for r in d['results'] if r['test'] != name]))

    def test_duplicate_result_rejected(self):
        self.rejected(lambda d: d['results'].append(copy.deepcopy(d['results'][0])))

    def test_missing_end_rejected(self):
        self.rejected(lambda d: d.__setitem__('end', None))

    def test_timeout_after_pass_records_rejected(self):
        self.rejected(lambda d: d.__setitem__('extra', 'LINUX_SUITE_ABORT {"status":"TIMEOUT","exitCode":124}\n'))

    def test_malformed_record_rejected(self):
        self.rejected(lambda d: d.__setitem__('extra', 'LINUX_TEST_RESULT {broken\n'))

    def test_wrong_build_identity_rejected(self):
        self.rejected(lambda d: d['begin'].__setitem__('buildId', 'b' * 64))

    def test_unverified_real_instructions_rejected(self):
        self.rejected(lambda d: d['manifest']['assemblyChecks'].__setitem__('explicitScRelease', False))

    def test_wrong_iterations_rejected(self):
        self.rejected(lambda d: d['begin'].__setitem__('iterations', 1))

    def test_zero_operations_rejected(self):
        self.rejected(lambda d: d['results'][0].update(operations=0, observed=0, threadOperations=[0] * 4))

    def test_lost_counter_update_rejected(self):
        self.rejected(lambda d: d['results'][7].__setitem__('observed', 7))

    def test_missing_thread_rejected(self):
        self.rejected(lambda d: d['results'][0].__setitem__('threadsStarted', 3))

    def test_single_core_cannot_stand_for_four_threads(self):
        self.rejected(lambda d: d['begin'].__setitem__('affinityCpus', [0] * 4))

    def test_wrong_actual_thread_cpu_rejected(self):
        self.rejected(lambda d: d['results'][0].__setitem__('threadCpuIds', [0, 1, 2, 2]))

    def test_affinity_failure_rejected(self):
        self.rejected(lambda d: d['results'][0].__setitem__('threadPinErrors', [0, 0, 0, 22]))

    def test_no_actual_futex_sleep_rejected(self):
        self.rejected(lambda d: d['results'][2]['metrics'].__setitem__('futexSlept', 0))

    def test_negative_lrsc_failures_rejected(self):
        self.rejected(lambda d: d['results'][4]['metrics'].__setitem__('scFailures', -1))

    def test_zero_sc_failure_still_records_valid_successes(self):
        report = self.check(lambda d: d['results'][4]['metrics'].__setitem__('scFailures', 0))
        self.assertTrue(report['pass'], report['issues'])

    def test_wrong_memcpy_checksum_rejected(self):
        self.rejected(lambda d: d['results'][8]['metrics'].__setitem__('checksum', 0))

    def test_single_mapping_is_not_alias_coverage(self):
        self.rejected(lambda d: d['results'][9]['metrics'].__setitem__('aliasMappings', 1))

    def test_no_pressure_working_set_rejected(self):
        self.rejected(lambda d: d['results'][9]['metrics'].__setitem__('pressureBytesPerThread', 0))

    def test_single_numa_node_cannot_claim_migration(self):
        self.rejected(lambda d: d['results'][10]['metrics'].__setitem__('numaMemoryNodes', 1))

    def test_partial_migration_cannot_claim_pass(self):
        self.rejected(lambda d: d['results'][10]['metrics'].__setitem__('migrationPagesMoved', 7))

    def test_migration_syscall_error_rejected(self):
        self.rejected(lambda d: d['results'][10]['metrics'].__setitem__('migrationErrno', 38))

    def test_unsupported_migration_is_explicitly_incomplete_not_pass(self):
        def mutate(data):
            result = data['results'][10]
            result.update(status='UNSUPPORTED', operations=0, observed=0, threadOperations=[0] * 4)
            result['metrics'].update(numaMemoryNodes=1, migrationPagesAttempted=0, migrationPagesMoved=0,
                                     migrationReason='fewer_than_two_memory_numa_nodes')
            data['end'].update(status='INCOMPLETE', passed=10, unsupported=1, exitCode=2)
        report = self.rejected(mutate)
        self.assertEqual(report['status'], 'INCOMPLETE', report['issues'])
        self.assertEqual(report['incomplete'][0]['test'], 'page_migration')

    def test_failed_test_cannot_be_hidden_in_footer(self):
        self.rejected(lambda d: d['results'][0].update(status='FAIL', errors=1))

    def test_per_thread_errors_cannot_be_erased_by_total(self):
        self.rejected(lambda d: d['results'][0].__setitem__('threadErrors', [0, 0, 1, 0]))

    def test_boolean_operation_count_is_not_number(self):
        self.rejected(lambda d: d['results'][0].__setitem__('operations', True))


if __name__ == '__main__':
    unittest.main()
