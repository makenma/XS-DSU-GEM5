"""Synthetic mutation tests for validation only; these are not Linux execution evidence."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('linux_litmus_checker', ROOT / 'scripts/check-linux-litmus.py')
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def fixture():
    manifest = {'schemaVersion': 1, 'suite': 'official-diy7-riscv-linux', 'profile': 'smoke',
                'binarySha256': 'a' * 64, 'testCount': 1, 'uniqueSemanticTestCount': 1, 'excludedTests': [],
                'runtime': {'samplesPerInstance': 8, 'runs': 1, 'availableCpus': 4,
                            'affinityCpus': [0, 1, 2, 3], 'forcedAffinity': True},
                'tests': [{'name': 'chi_fixture', 'threads': 2, 'testHash': 'b' * 32,
                           'modelAllowedStates': ['0:x7=0; 1:x7=1;', '0:x7=1; 1:x7=0;'],
                           'modelObservation': 'Never'}]}
    log = '\n'.join([
        'boot log',
        'LINUX_LITMUS_BEGIN {"profile":"smoke","binarySha256":"' + 'a' * 64 + '","onlineCpus":4}',
        'Test chi_fixture Allowed', 'Histogram (2 states)',
        '7     :>0:x7=0; 1:x7=1;', '9     :>0:x7=1; 1:x7=0;', 'No',
        'Witnesses', 'Positive: 0, Negative: 16', 'Hash=' + 'b' * 32,
        'Observation chi_fixture Never 0 16', 'Time chi_fixture 0.1',
        'LINUX_LITMUS_END {"exitCode":0}', 'shutdown'])
    return log, manifest


class LinuxLitmusMutations(unittest.TestCase):
    def assert_bad(self, log_edit=lambda x: x, manifest_edit=lambda x: None):
        log, manifest = fixture()
        manifest_edit(manifest)
        self.assertFalse(MOD.check(log_edit(log), manifest)['pass'])

    def test_good(self):
        log, manifest = fixture()
        self.assertTrue(MOD.check(log, manifest)['pass'])

    def test_missing_begin(self):
        self.assert_bad(lambda s: '\n'.join(line for line in s.splitlines() if not line.startswith('LINUX_LITMUS_BEGIN')))

    def test_missing_end(self):
        self.assert_bad(lambda s: s.split('LINUX_LITMUS_END')[0])

    def test_timeout(self):
        self.assert_bad(lambda s: s.replace('"exitCode":0', '"exitCode":124'))

    def test_boolean_exit(self):
        self.assert_bad(lambda s: s.replace('"exitCode":0', '"exitCode":false'))

    def test_missing_cpu(self):
        self.assert_bad(lambda s: s.replace('"onlineCpus":4', '"onlineCpus":3'))

    def test_wrong_binary(self):
        self.assert_bad(lambda s: s.replace('a' * 64, 'c' * 64))

    def test_wrong_profile(self):
        self.assert_bad(lambda s: s.replace('"smoke"', '"full"'))

    def test_wrong_hash(self):
        self.assert_bad(lambda s: s.replace('b' * 32, 'c' * 32))

    def test_missing_test(self):
        self.assert_bad(lambda s: s.replace('Test chi_fixture Allowed', ''))

    def test_duplicate_test(self):
        self.assert_bad(lambda s: s.replace('Test chi_fixture Allowed', 'Test chi_fixture Allowed\nTest chi_fixture Allowed'))

    def test_missing_observation(self):
        self.assert_bad(lambda s: s.replace('Observation chi_fixture Never 0 16', ''))

    def test_duplicate_observation(self):
        self.assert_bad(lambda s: s.replace('Observation chi_fixture Never 0 16', 'Observation chi_fixture Never 0 16\nObservation chi_fixture Never 0 16'))

    def test_wrong_count(self):
        self.assert_bad(lambda s: s.replace('9     :>', '8     :>'))

    def test_wrong_state_count(self):
        self.assert_bad(lambda s: s.replace('Histogram (2 states)', 'Histogram (3 states)'))

    def test_wrong_witness(self):
        self.assert_bad(lambda s: s.replace('Positive: 0, Negative: 16', 'Positive: 0, Negative: 15'))

    def test_wrong_observation(self):
        self.assert_bad(lambda s: s.replace('Never 0 16', 'Always 16 0'))

    def test_forbidden_state(self):
        self.assert_bad(lambda s: s.replace('7=0; 1:x7=1;', '7=0; 1:x7=0;'))

    def test_forbidden_value(self):
        self.assert_bad(lambda s: s.replace('7=0; 1:x7=1;', '7=99; 1:x7=1;'))

    def test_duplicate_state(self):
        self.assert_bad(lambda s: s.replace('0:x7=1; 1:x7=0;', '0:x7=0; 1:x7=1;'))

    def test_no_histogram(self):
        self.assert_bad(lambda s: s.replace('Histogram (2 states)', ''))

    def test_fatal(self):
        self.assert_bad(lambda s: s.replace('Witnesses', 'Fatal: worker failed\nWitnesses'))

    def test_empty_log(self):
        self.assert_bad(lambda _: '')

    def test_outside_markers(self):
        self.assert_bad(lambda s: s + '\nObservation chi_extra Never 0 16')

    def test_no_affinity(self):
        self.assert_bad(manifest_edit=lambda m: m['runtime'].update(forcedAffinity=False))

    def test_wrong_samples(self):
        self.assert_bad(manifest_edit=lambda m: m['runtime'].update(samplesPerInstance=9))

    def test_manifest_exclusion(self):
        self.assert_bad(manifest_edit=lambda m: m.update(excludedTests=['skipped']))

    def test_missing_model_states(self):
        self.assert_bad(manifest_edit=lambda m: m['tests'][0].update(modelAllowedStates=[]))

    def test_positive_forbidden_even_if_state_set_wrong(self):
        self.assert_bad(lambda s: s.replace('7     :>', '7     *>').replace('Positive: 0, Negative: 16', 'Positive: 7, Negative: 9').replace('Never 0 16', 'Sometimes 7 9'))

    def test_required_condition(self):
        self.assert_bad(manifest_edit=lambda m: m['tests'][0].update(modelObservation='Always'))

    def test_nonobserved_allowed_condition_is_not_failure(self):
        log, manifest = fixture()
        manifest['tests'][0]['modelObservation'] = 'Sometimes'
        self.assertTrue(MOD.check(log, manifest)['pass'])

    def test_state_format_hex_equivalence(self):
        self.assertEqual(MOD.state_key('0:x7=0x10; y=1;'), MOD.state_key('y=1;0:x7=16;'))

    def test_inflated_unique_hash_count(self):
        self.assert_bad(manifest_edit=lambda m: m.update(uniqueSemanticTestCount=1001))

    def test_small_matrix_cannot_claim_large(self):
        self.assert_bad(lambda s: s.replace('"smoke"', '"large"'),
                        lambda m: m.update(profile='large'))


if __name__ == '__main__':
    unittest.main()
