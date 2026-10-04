import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

comparator = load('reference_comparator', ROOT / 'scripts/compare-linux-kernel-build.py')
fixtures = load('kernel_fixtures', Path(__file__).with_name('test_kernel_build_checker.py'))


def reference():
    return {'schemaVersion': 1, 'referenceOnly': True, 'referenceBuildCompleted': True,
            'referenceArtifactHashesRecomputedOnHost': True, 'gem5Executed': False,
            'execution': 'QEMU user-mode chroot', 'profile': 'tinyconfig-smp4',
            'sourceVersion': '6.6.52', 'target': 'Image',
            'artifacts': {'image': {'bytes': 123456, 'sha256': 'a' * 64},
                          'vmlinux': {'bytes': 130000, 'sha256': 'b' * 64},
                          'config': {'bytes': 10000, 'sha256': 'c' * 64}}}


class ReferenceCompareTest(unittest.TestCase):
    def test_match(self):
        result = comparator.compare(fixtures.fixture(), reference())
        self.assertEqual(result['status'], 'MATCH')
        self.assertTrue(result['passed'])
        self.assertFalse(result['gem5ArtifactHashesRecomputedOnHost'])

    def test_absent_and_partial_guest_are_incomplete(self):
        for text in ('', 'Linux booted', fixtures.fixture().split('CHI_KERNEL_BUILD_END ')[0]):
            with self.subTest(text=text[:20]):
                result = comparator.compare(text, reference())
                self.assertEqual(result['status'], 'INCOMPLETE')
                self.assertFalse(result['passed'])

    def test_failed_guest_cannot_match(self):
        for text in (fixtures.fixture() + '\nCHI_KERNEL_BUILD_FAIL reason=test',
                     fixtures.fixture().replace('  CC      init/main.o', ''),
                     fixtures.fixture().replace('cpu3 11 0 22', 'cpu3 1 0 2')):
            with self.subTest(text=text[-30:]):
                result = comparator.compare(text, reference())
                self.assertEqual(result['status'], 'GUEST_FAILED')
                self.assertFalse(result['passed'])

    def test_each_hash_or_size_difference_is_listed(self):
        for artifact, field, value, output_field in (
                ('config', 'sha256', 'e' * 64, 'configSha256'),
                ('image', 'bytes', 123457, 'imageBytes'),
                ('image', 'sha256', 'e' * 64, 'imageSha256'),
                ('vmlinux', 'sha256', 'e' * 64, 'vmlinuxSha256')):
            with self.subTest(artifact=artifact, field=field):
                ref = reference()
                ref['artifacts'][artifact][field] = value
                result = comparator.compare(fixtures.fixture(), ref)
                self.assertEqual(result['status'], 'MISMATCH')
                self.assertEqual([entry['field'] for entry in result['differences']], [output_field])

    def test_missing_and_untrusted_reference(self):
        self.assertEqual(comparator.compare(fixtures.fixture(), None)['status'], 'INCOMPLETE')
        self.assertEqual(comparator.compare(fixtures.fixture(), [])['status'], 'INVALID_REFERENCE')
        for field, value in (('referenceOnly', False), ('gem5Executed', True),
                             ('referenceBuildCompleted', False),
                             ('referenceArtifactHashesRecomputedOnHost', False)):
            with self.subTest(field=field):
                ref = reference()
                ref[field] = value
                self.assertEqual(comparator.compare(fixtures.fixture(), ref)['status'], 'INVALID_REFERENCE')
        for mutation in ({}, {'image': {'bytes': True, 'sha256': 'a' * 64}},
                         {'image': {'bytes': 123456, 'sha256': 'garbage'}}):
            ref = reference()
            ref['artifacts'].update(mutation)
            if not mutation:
                del ref['artifacts']['config']
            self.assertEqual(comparator.compare(fixtures.fixture(), ref)['status'], 'INVALID_REFERENCE')


if __name__ == '__main__':
    unittest.main()
