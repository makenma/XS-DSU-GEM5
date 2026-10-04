import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('freeze_suite', ROOT / 'scripts/freeze-linux-suite.py')
freeze = importlib.util.module_from_spec(spec)
spec.loader.exec_module(freeze)


class FreezeSuiteTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'project'
        self.output = Path(self.temporary.name) / 'snapshots'
        for stage, (folder, binary, checker, suite) in freeze.STAGES.items():
            source = self.root / 'build' / folder
            artifact = source / binary
            artifact.parent.mkdir(parents=True)
            data = ('fixture binary ' + stage).encode()
            artifact.write_bytes(data)
            manifest = {'schemaVersion': 1, 'suite': suite, 'binarySha256': freeze.sha256(data)}
            if stage.startswith('litmus-'):
                manifest['profile'] = stage.removeprefix('litmus-')
                manifest['guestBinary'] = str(artifact.resolve())
            else:
                manifest['binary'] = binary
            if stage in ('cbo', 'uncached'):
                module = source / 'module/test.ko'
                module.parent.mkdir()
                module.write_bytes(b'fixture module')
                manifest['module'] = {'built': True, 'path': str(module.resolve()),
                                      'sha256': freeze.sha256(module.read_bytes())}
            (source / 'manifest.json').write_text(json.dumps(manifest))
            script = self.root / 'scripts' / checker
            script.parent.mkdir(exist_ok=True)
            script.write_text('raise RuntimeError("freeze must never execute checkers")\n')
        (self.root / 'scripts/check-kernel-torture.py').write_text('# checker\n')

    def change_manifest(self, stage, update):
        path = self.root / 'build' / freeze.STAGES[stage][0] / 'manifest.json'
        manifest = json.loads(path.read_text())
        update(manifest)
        path.write_text(json.dumps(manifest))

    def test_frozen_inventory_and_local_references(self):
        index_path = freeze.freeze(self.root, self.output)
        index = json.loads(index_path.read_text())
        self.assertEqual(index['execution'], 'NOT_RUN')
        self.assertEqual(index_path.parent.name, index['inputSha256'])
        self.assertEqual(set(index['stages']), {*freeze.STAGES, 'torture'})
        for item in index['files']:
            self.assertFalse(Path(item['path']).is_absolute())
            self.assertEqual(freeze.sha256((index_path.parent / item['path']).read_bytes()), item['sha256'])
        for stage in freeze.STAGES:
            entry = index['stages'][stage]
            manifest_path = index_path.parent / entry['manifest']
            manifest = json.loads(manifest_path.read_text())
            binary = index_path.parent / entry['binary']
            self.assertEqual(manifest_path.parent / manifest['binary'], binary)
            if stage.startswith('litmus-'):
                self.assertEqual(manifest_path.parent / manifest['guestBinary'], binary)
            source = self.root / 'build' / freeze.STAGES[stage][0] / 'manifest.json'
            self.assertEqual((index_path.parent / entry['originalManifest']).read_bytes(), source.read_bytes())
        self.assertTrue(index['stages']['uncached']['requiresFsConfig'])
        self.assertEqual(freeze.freeze(self.root, self.output), index_path)

    def test_missing_input_fails_without_publishing(self):
        (self.root / 'build/linux-uncached/manifest.json').unlink()
        with self.assertRaisesRegex(ValueError, 'missing required'):
            freeze.freeze(self.root, self.output)
        self.assertFalse(self.output.exists())

    def test_binary_mismatch_rejected(self):
        (self.root / 'build/linux-tests/linux-suite').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'SHA256 mismatch'):
            freeze.freeze(self.root, self.output)

    def test_unbuilt_module_rejected(self):
        self.change_manifest('cbo', lambda m: m['module'].update(built=False))
        with self.assertRaisesRegex(ValueError, 'has not been built'):
            freeze.freeze(self.root, self.output)

    def test_module_mismatch_rejected(self):
        (self.root / 'build/linux-uncached/module/test.ko').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'SHA256 mismatch'):
            freeze.freeze(self.root, self.output)

    def test_wrong_manifest_reference_rejected(self):
        self.change_manifest('pthreads', lambda m: m.update(binary='../linux-cbo/linux-cbo'))
        with self.assertRaisesRegex(ValueError, 'configured binary'):
            freeze.freeze(self.root, self.output)

    def test_tampered_snapshot_is_not_overwritten(self):
        index_path = freeze.freeze(self.root, self.output)
        target = index_path.parent / 'checkers/check-linux-suite.py'
        target.chmod(0o644)
        target.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'refusing overwrite'):
            freeze.freeze(self.root, self.output)
        self.assertEqual(target.read_bytes(), b'tampered')

    def test_changed_checker_gets_new_snapshot(self):
        first = freeze.freeze(self.root, self.output)
        (self.root / 'scripts/check-linux-suite.py').write_bytes(b'# new checker\n')
        second = freeze.freeze(self.root, self.output)
        self.assertNotEqual(first, second)
        self.assertTrue(first.is_file())

    def test_cli_prints_only_absolute_json_index(self):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/freeze-linux-suite.py'),
                                 '--project', str(self.root), '--output-root', str(self.output)],
                                text=True, capture_output=True, check=True)
        self.assertEqual(result.stderr, '')
        self.assertEqual(len(result.stdout.splitlines()), 1)
        message = json.loads(result.stdout)
        self.assertEqual(set(message), {'index'})
        self.assertTrue(Path(message['index']).is_absolute())
        self.assertTrue(Path(message['index']).is_file())


if __name__ == '__main__':
    unittest.main()
