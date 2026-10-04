import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('stage_gate', PROJECT / 'native/gem5/linux_stage_gate.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
BOOT = 'CHI_LINUX_ONLINE_CPUS=0-3\nCHI_LINUX_ONLINE_NODES=0-1\nCHI_LINUX_BOOT_PASS\nCHI_LINUX_STAGE_DONE=boot\n'


class StageGateTest(unittest.TestCase):
    def test_requires_actual_topology_and_unique_next_stage(self):
        with tempfile.TemporaryDirectory() as folder:
            for text in (BOOT.replace('0-3', '0'), BOOT + 'CHI_LINUX_STAGE_DONE=boot\n',
                         BOOT.replace('DONE=boot', 'DONE=complete')):
                gate = module.LinuxStageGate(PROJECT, folder)
                with self.assertRaises(RuntimeError):
                    gate.accept(text)
                self.assertEqual(gate.results, [])

    def test_checker_failure_does_not_advance_or_claim_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            gate = module.LinuxStageGate(PROJECT, folder)
            self.assertFalse(gate.accept(BOOT))
            with patch.object(module.subprocess, 'run') as run:
                run.return_value.returncode = 1
                run.return_value.stdout = 'forbidden outcome'
                run.return_value.stderr = ''
                with self.assertRaises(RuntimeError):
                    gate.accept(BOOT + 'CHI_LINUX_STAGE_DONE=litmus-smoke\n')
            self.assertEqual([r['stage'] for r in gate.results], ['boot'])

    def test_only_complete_after_all_checked_stages(self):
        with tempfile.TemporaryDirectory() as folder:
            gate = module.LinuxStageGate(PROJECT, folder)
            text = BOOT
            self.assertFalse(gate.accept(text))
            with patch.object(module.subprocess, 'run') as run:
                run.return_value.returncode = 0
                run.return_value.stdout = run.return_value.stderr = ''
                for stage in module.STAGES[1:-1]:
                    text += f'output for {stage}\nCHI_LINUX_STAGE_DONE={stage}\n'
                    self.assertFalse(gate.accept(text))
                self.assertEqual(run.call_count, 6)
            text += 'CHI_LINUX_PIPELINE_COMPLETE\nCHI_LINUX_STAGE_DONE=complete\n'
            self.assertTrue(gate.accept(text))
            with self.assertRaises(RuntimeError):
                gate.accept(text)


if __name__ == '__main__':
    unittest.main()
