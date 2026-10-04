"""Exercise the CBO installer in temporary trees; never modify the real gem5."""
import argparse
from contextlib import redirect_stdout
import io
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--gem5', type=Path, required=True)
args = parser.parse_args()
PROJECT = Path(__file__).resolve().parent.parent
COMMIT = '65f3241a6cd486dffddaca251536f5c691af9a1f'
BASELINES = {}
for path in [
    'src/arch/riscv/isa/decoder.isa', 'src/arch/riscv/isa/formats/mem.isa',
    'src/arch/riscv/isa/includes.isa', 'src/arch/riscv/regs/misc.hh',
    'src/arch/riscv/isa.cc', 'src/arch/riscv/tlb.hh', 'src/arch/riscv/tlb.cc',
    'src/arch/riscv/pagetable_walker.cc', 'src/arch/riscv/pmp.cc',
    'src/cpu/o3/lsq.cc', 'src/cpu/o3/lsq_unit.cc',
]:
    BASELINES[path] = subprocess.check_output(
        ['git', '-C', str(args.gem5), 'show', f'{COMMIT}:{path}'], text=True)


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='chi-cbo-installer-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.gem5 = self.root / 'gem5'
        for path, content in BASELINES.items():
            dest = self.gem5 / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
        for path in ['scripts/apply-riscv-cbo.py', 'native/gem5/riscv-cbo-format.isa',
                     'native/gem5/riscv_cbo_patch.py', 'native/gem5/RiscvCbo.hh']:
            dest = self.project / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(PROJECT / path, dest)
        for path in ['src/cpu/o3/lsq.cc', 'src/cpu/o3/lsq_unit.cc']:
            observer = PROJECT / 'build/extended-installed' / path
            dest = self.project / 'build/extended-installed' / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(observer, dest)
            shutil.copyfile(observer, self.gem5 / path)

    def run_install(self, check=False):
        def git(command, **kwargs):
            if command[-2:] == ['rev-parse', 'HEAD']:
                return COMMIT + '\n'
            self.assertEqual(command[:4], ['git', '-C', str(self.gem5), 'show'])
            return BASELINES[command[-1].split(':', 1)[1]]
        argv = ['apply-riscv-cbo.py', '--gem5', str(self.gem5)] + (['--check'] if check else [])
        old_path = sys.path[:]
        sys.modules.pop('riscv_cbo_patch', None)
        try:
            with patch.object(sys, 'argv', argv), patch('subprocess.check_output', git), redirect_stdout(io.StringIO()):
                try:
                    runpy.run_path(str(self.project / 'scripts/apply-riscv-cbo.py'), run_name='__main__')
                except SystemExit as error:
                    self.assertEqual(error.code, 0)
        finally:
            sys.path[:] = old_path
            sys.modules.pop('riscv_cbo_patch', None)

    def contents(self):
        return {str(p.relative_to(self.gem5)): p.read_bytes()
                for p in self.gem5.rglob('*') if p.is_file()}

    def test_check_is_read_only(self):
        before = self.contents()
        self.run_install(check=True)
        self.assertEqual(before, self.contents())
        self.assertFalse((self.project / 'build/riscv-cbo-installed').exists())

    def test_repeated_install_is_stable_and_composes_observer(self):
        self.run_install()
        before = self.contents()
        times = {p: p.stat().st_mtime_ns for p in self.gem5.rglob('*') if p.is_file()}
        self.run_install()
        self.assertEqual(before, self.contents())
        self.assertEqual(times, {p: p.stat().st_mtime_ns for p in times})
        for path in ['src/cpu/o3/lsq.cc', 'src/cpu/o3/lsq_unit.cc']:
            self.assertEqual((self.gem5 / path).read_bytes(),
                             (self.project / 'build/extended-installed' / path).read_bytes())

    def test_independent_edit_rejected_before_any_file_changes(self):
        self.run_install()
        p = self.gem5 / 'src/arch/riscv/pagetable_walker.cc'
        p.write_text(p.read_text() + '\n// independent user edit\n')
        before = self.contents()
        with self.assertRaisesRegex(RuntimeError, 'Refusing to overwrite independent edits'):
            self.run_install()
        self.assertEqual(before, self.contents())

    def test_overlay_revision_accepts_only_own_previous_output(self):
        self.run_install()
        p = self.project / 'native/gem5/RiscvCbo.hh'
        p.write_text(p.read_text() + '\n// next reviewed overlay revision\n')
        self.run_install()
        self.assertEqual(p.read_bytes(), (self.gem5 / 'src/arch/riscv/cbo.hh').read_bytes())


unittest.main(argv=[sys.argv[0]], verbosity=2)
