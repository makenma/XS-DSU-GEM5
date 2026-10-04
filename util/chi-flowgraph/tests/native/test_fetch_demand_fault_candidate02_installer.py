"""Installer-only tests; these do not claim to validate Fetch/CPU behavior."""
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    'fetch_demand_fault_candidate02_installer', PROJECT / 'scripts/apply-fetch-demand-fault-candidate02.py')
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name).resolve()
        self.manifest = {'files': [], 'requiredUnchangedFiles': []}
        self.hunks = {}
        self.original = {}
        self.desired = {}
        for i, name in enumerate(sorted(MOD.FILES)):
            before = f'unchanged-{i}\nbefore-{i}\nend-{i}\n'.encode()
            after = f'unchanged-{i}\nafter-{i}\nend-{i}\n'.encode()
            raw_patch = (f'--- a/{name}\n+++ b/{name}\n@@ -1,3 +1,3 @@\n'
                         f' unchanged-{i}\n-before-{i}\n+after-{i}\n end-{i}\n').encode()
            self.hunks.update(MOD.parse_patch(raw_patch))
            self.manifest['files'].append({
                'path': name, 'beforeSha256': MOD.digest(before),
                'afterSha256': MOD.digest(after),
            })
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(before)
            self.original[path], self.desired[path] = before, after
        for name in sorted(MOD.DEPENDENCIES):
            raw = b'unchanged phase-storage dependency\n'
            (self.source / name).parent.mkdir(parents=True, exist_ok=True)
            (self.source / name).write_bytes(raw)
            self.manifest['requiredUnchangedFiles'].append({
                'path': name, 'sha256': MOD.digest(raw),
            })

    def plan(self, manifest=None):
        with patch.object(MOD.subprocess, 'check_output', return_value=MOD.COMMIT), \
             patch.object(MOD, 'load_package', return_value=(manifest or self.manifest, self.hunks)):
            return MOD.plan(self.source)

    def test_real_package_inventory_and_sha(self):
        manifest, hunks = MOD.load_package()
        self.assertEqual(len(manifest['files']), 2)
        self.assertEqual(set(hunks), MOD.FILES)
        self.assertEqual(len(manifest['requiredUnchangedFiles']), 12)

    def test_install_then_idempotent(self):
        required, changes, states = self.plan()
        self.assertEqual({item['state'] for item in states}, {'pending'})
        self.assertEqual(MOD.install(required, changes), 2)
        for path, data in self.desired.items():
            self.assertEqual(path.read_bytes(), data)
        required, changes, states = self.plan()
        self.assertEqual({item['state'] for item in states}, {'installed'})
        self.assertEqual(MOD.install(required, changes), 0)

    def test_mixed_known_states_recover(self):
        path = next(iter(self.desired))
        path.write_bytes(self.desired[path])
        required, changes, states = self.plan()
        self.assertEqual(sum(s['state'] == 'installed' for s in states), 1)
        self.assertEqual(MOD.install(required, changes), 1)

    def test_unknown_last_file_rejected_before_any_write(self):
        path = list(self.original)[-1]
        path.write_bytes(b'independent edits\n')
        before = {p: p.read_bytes() for p in self.original}
        with self.assertRaisesRegex(ValueError, 'independent edits'):
            self.plan()
        self.assertEqual(before, {p: p.read_bytes() for p in self.original})

    def test_wrong_phase_dependency_rejected(self):
        name = self.manifest['requiredUnchangedFiles'][-1]['path']
        (self.source / name).write_bytes(b'older prerequisite\n')
        with self.assertRaisesRegex(ValueError, 'BPU prerequisite'):
            self.plan()
        self.assertEqual(self.original, {p: p.read_bytes() for p in self.original})

    def test_wrong_pinned_commit_rejected(self):
        with patch.object(MOD.subprocess, 'check_output', return_value='wrong-head'):
            with self.assertRaisesRegex(ValueError, 'unexpected gem5 commit'):
                MOD.plan(self.source)

    def test_change_after_plan_prevents_every_write(self):
        required, changes, _ = self.plan()
        path = list(self.original)[-1]
        path.write_bytes(b'concurrent independent edit\n')
        before = {p: p.read_bytes() for p in self.original}
        with self.assertRaisesRegex(ValueError, 'source changed after validation'):
            MOD.install(required, changes)
        self.assertEqual(before, {p: p.read_bytes() for p in self.original})

    def test_wrong_expected_after_hash_rejected(self):
        manifest = copy.deepcopy(self.manifest)
        manifest['files'][-1]['afterSha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'patched SHA mismatch'):
            self.plan(manifest)

    def test_hunk_context_is_exact(self):
        name = next(iter(self.hunks))
        with self.assertRaisesRegex(ValueError, 'exact patch hunk'):
            MOD.patched(b'unrelated\n', self.hunks[name])

    def test_truncated_patch_rejected(self):
        with self.assertRaisesRegex(ValueError, 'truncated'):
            MOD.parse_patch(b'--- a/x\n+++ b/x\n@@ -1,1 +1,1 @@\n-old\n')

    def test_symlink_source_rejected(self):
        path = next(iter(self.original))
        outside = self.source / 'outside-copy'
        outside.write_bytes(path.read_bytes())
        path.unlink()
        try:
            path.symlink_to(outside)
        except OSError as error:
            self.skipTest('symlink unavailable: ' + str(error))
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.plan()


    def test_frozen_manifest_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary)
            (package / 'fetch_demand_fault_candidate02.json').write_bytes(b'{}')
            with patch.object(MOD, 'PACKAGE', package):
                with self.assertRaisesRegex(ValueError, 'manifest SHA mismatch'):
                    MOD.load_package()

    def test_frozen_patch_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary)
            (package / 'fetch_demand_fault_candidate02.json').write_bytes(
                (MOD.PACKAGE / 'fetch_demand_fault_candidate02.json').read_bytes())
            (package / 'fetch_demand_fault_candidate02.patch').write_bytes(b'corrupted patch')
            with patch.object(MOD, 'PACKAGE', package):
                with self.assertRaisesRegex(ValueError, 'patch SHA mismatch'):
                    MOD.load_package()

    def test_real_frozen_sources_round_trip(self):
        before = PROJECT / 'build/qa/fetch-demand-fault/before'
        if not before.is_dir():
            self.skipTest('source-freeze audit fixture is absent')
        manifest, hunks = MOD.load_package()
        for item in manifest['files']:
            raw = (before / Path(item['path']).name).read_bytes()
            self.assertEqual(MOD.digest(raw), item['beforeSha256'])
            candidate = MOD.patched(raw, hunks[item['path']])
            self.assertEqual(MOD.digest(candidate), item['afterSha256'])
            self.assertEqual(MOD.patched(candidate, hunks[item['path']], reverse=True), raw)


    def test_actual_candidate01_upgrade_is_guarded_and_idempotent(self):
        saved = PROJECT / 'build/qa/fetch-demand-fault-candidate02/candidate01'
        if not saved.is_dir():
            self.skipTest('candidate01 audit source fixture absent')
        manifest, hunks = MOD.load_package()
        # Real candidate01 sources with isolated dependency fixtures; no model
        # checkout is mutated and this is not a CPU behavior test.
        manifest = copy.deepcopy(manifest)
        manifest['requiredUnchangedFiles'] = self.manifest['requiredUnchangedFiles']
        for item in manifest['files']:
            (self.source / item['path']).write_bytes((saved / Path(item['path']).name).read_bytes())
        self.hunks = hunks
        required, changes, states = self.plan(manifest)
        self.assertEqual([s['state'] for s in states], ['pending-compile-correction', 'installed'])
        self.assertEqual(MOD.install(required, changes), 1)
        for item in manifest['files']:
            self.assertEqual(MOD.digest((self.source / item['path']).read_bytes()), item['afterSha256'])
        required, changes, states = self.plan(manifest)
        self.assertEqual({s['state'] for s in states}, {'installed'})
        self.assertEqual(MOD.install(required, changes), 0)


if __name__ == '__main__':
    unittest.main()
