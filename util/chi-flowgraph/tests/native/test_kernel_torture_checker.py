"""Small synthetic fixtures for Linux 6.6.52 torture log rejection rules.

These are parser tests, not evidence that a guest kernel ran the torture tests.
"""
import importlib.util
from pathlib import Path
import unittest

PROJECT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('kernel_torture_checker', PROJECT / 'scripts/check-kernel-torture.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def fixture():
    lines = ['CHI_KERNEL_TORTURE_RUN_BEGIN id=kt-fixture kernel=6.6.52-chi-native cpus=0-3 nodes=0-1 rcu_seconds=3 lock_seconds=2 threads=4']
    tick = 10
    for name in ('rcu', *checker.LOCK_TYPES):
        duration = 3 if name == 'rcu' else 2
        readers = 4 if name in ('rw_lock', 'rwsem_lock') else 0
        identity = f'id=kt-fixture test={name}'
        params = ('nreaders=4 nfakewriters=2 n_barrier_cbs=4 gp_normal=1 gp_sync=1'
                  if name == 'rcu' else f'nwriters_stress=4 nreaders_stress={readers}')
        lines += [f'CHI_KERNEL_TORTURE_CASE_BEGIN {identity} seconds={duration} uptime={tick}.00',
                  f'CHI_KERNEL_TORTURE_PARAMETERS {identity} torture_type={name} {params} onoff_interval=0 shutdown_secs=0 stutter=0 shuffle_interval=0',
                  f'CHI_KERNEL_TORTURE_LOG_BEGIN {identity}',
                  '[    0.00] Earlier kernel log, outside this case window',
                  f'[  {tick}.00] CHI_KERNEL_TORTURE_KMSG_BEGIN {identity}']
        if name == 'rcu':
            body = [
                'rcu:  End-test grace-period state: g160 f0x0 total-gps=40',
                'rcu-torture: rtc: 0000000000000000 ver: 40 tfle: 0 rta: 45 rtaf: 0 rtf: 40 '
                'rtmbe: 0 rtmbkf: 0/500 rtbe: 0 rtbke: 0 rtbf: 0 rtb: 0 nt: 2 barrier: 8/8:0 read-exits: 0 nocb-toggles: 0:0',
                'rcu-torture: Reader Pipe:  999 5 0 0 0 0 0 0 0 0 0',
                'rcu-torture:--- End of test: SUCCESS: nreaders=4 nfakewriters=2 n_barrier_cbs=4']
        else:
            body = ['Writes:  Total: 400  Max/Min: 100/100   Fail: 0 ']
            if readers:
                body.append('Reads :  Total: 800  Max/Min: 200/200   Fail: 0 ')
            body.append(f'{name}-torture:--- End of test: SUCCESS: nwriters_stress=4 nreaders_stress={readers}')
        lines.extend(f'[  {tick+duration}.00] {line}' for line in body)
        lines += [f'[  {tick+duration}.01] CHI_KERNEL_TORTURE_KMSG_END {identity}',
                  f'CHI_KERNEL_TORTURE_LOG_END {identity}',
                  f'CHI_KERNEL_TORTURE_CASE_END {identity} seconds={duration} uptime={tick+duration}.05 insmod_rc=0 rmmod_rc=0']
        tick += duration + 1
    lines.append('CHI_KERNEL_TORTURE_RUN_END id=kt-fixture status=completed')
    return '\n'.join(lines) + '\n'


class TortureCheckerTests(unittest.TestCase):
    def setUp(self):
        self.text = fixture()

    def rejected(self, old, new, reason=None):
        self.assertIn(old, self.text)
        changed = self.text.replace(old, new, 1)
        with self.assertRaisesRegex(ValueError, reason or '.'):
            checker.check(changed)

    def test_valid_five_sequential_profiles(self):
        result = checker.check(self.text)
        self.assertTrue(result['passed'])
        self.assertEqual(len(result['cases']), 5)
        self.assertEqual(result['cases'][0]['work']['gracePeriods'], 40)

    def test_zero_rcu_allocations_despite_success(self):
        self.rejected('rta: 45', 'rta: 0', 'insufficient')

    def test_zero_rcu_grace_periods_despite_success(self):
        self.rejected('total-gps=40', 'total-gps=0', 'insufficient')

    def test_zero_rcu_reader_work(self):
        self.rejected('Reader Pipe:  999 5', 'Reader Pipe:  0 0', 'no real work')

    def test_premature_reclamation(self):
        self.rejected('Reader Pipe:  999 5 0', 'Reader Pipe:  999 5 1', 'prematurely')

    def test_rcu_memory_order_error(self):
        self.rejected('rtmbkf: 0/500', 'rtmbkf: 1/500', 'memory-order')

    def test_zero_barrier_work(self):
        self.rejected('barrier: 8/8:0', 'barrier: 0/0:0', 'no real work')

    def test_zero_lock_work_despite_success(self):
        self.rejected('Total: 400  Max/Min: 100/100', 'Total: 0  Max/Min: 0/0', 'insufficient')

    def test_starved_lock_worker(self):
        self.rejected('Total: 400  Max/Min: 100/100', 'Total: 400  Max/Min: 200/0', 'zero-thread')

    def test_inconsistent_lock_counts(self):
        self.rejected('Total: 400  Max/Min: 100/100', 'Total: 900  Max/Min: 100/100', 'inconsistent')

    def test_lock_exclusion_error(self):
        self.rejected('Fail: 0', 'Fail: 1', 'exclusion failed')

    def test_module_load_failure(self):
        self.rejected('insmod_rc=0', 'insmod_rc=1', 'load/unload')

    def test_module_unload_failure(self):
        self.rejected('rmmod_rc=0', 'rmmod_rc=1', 'load/unload')

    def test_too_short_actual_time(self):
        self.rejected('seconds=3 uptime=13.05', 'seconds=3 uptime=10.05', 'shorter')

    def test_false_success_suffix(self):
        self.rejected('End of test: SUCCESS:', 'End of test: SUCCESS_FAKE:', 'unique final')

    def test_wrong_actual_workers(self):
        self.rejected('SUCCESS: nreaders=4', 'SUCCESS: nreaders=0', 'actual reader')

    def test_missing_kmsg_window(self):
        self.rejected('CHI_KERNEL_TORTURE_KMSG_BEGIN id=kt-fixture test=rcu', 'LOST_KMSG_START', 'boundary')

    def test_missing_completion(self):
        self.rejected('CHI_KERNEL_TORTURE_RUN_END', 'TRUNCATED_RUN_END', 'RUN_END')

    def test_duplicate_profile(self):
        self.rejected('CASE_BEGIN id=kt-fixture test=rw_lock', 'CASE_BEGIN id=kt-fixture test=spin_lock', 'test cases')

    def test_kernel_warning(self):
        self.rejected('rcu:  End-test', 'WARNING: bad\nrcu:  End-test', 'kernel error')

    def test_only_two_online_cpus(self):
        self.rejected('cpus=0-3', 'cpus=0-1', 'four online')

    def test_same_numa_node(self):
        self.rejected('nodes=0-1', 'nodes=0', 'two NUMA')

    def test_refuse_zero_minimum_threshold(self):
        with self.assertRaisesRegex(ValueError, 'positive'):
            checker.check(self.text, min_lock_acquires=0)


if __name__ == '__main__':
    unittest.main()
