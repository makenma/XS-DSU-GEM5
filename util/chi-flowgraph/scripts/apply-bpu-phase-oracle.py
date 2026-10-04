"""Install baseline-only independent vector exports; no collection/counter changes."""
import importlib.util
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'native/gem5'))
from bpu_phase_oracle_patch import BASELINE_SHA256, prepare

spec = importlib.util.spec_from_file_location('phase_installer', Path(__file__).with_name('apply-bpu-phase-mirror.py'))
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

if __name__ == '__main__':
    installer.install(prepare, BASELINE_SHA256, 'bpu-phase-oracle')
