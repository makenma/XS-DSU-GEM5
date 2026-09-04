import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.builder import load_arch
from mesh_ir.effective import EffectiveArchitecture
from mesh_ir.model import MeshIrError

ARCH_PATH = Path(__file__).resolve().parents[4] / "configs/example/ai_mesh/arch/mesh_1x2.yaml"


@pytest.fixture(scope="module")
def manifest():
    return load_arch(ARCH_PATH)


def test_defaults_come_from_manifest(manifest):
    effective = EffectiveArchitecture(manifest)
    assert effective.admit_window == manifest.admit_window
    assert effective.dma_descriptor_queue_depth == manifest.dma_descriptor_queue_depth
    assert effective.digest() == manifest.digest()
    assert effective.base_digest() == manifest.digest()


def test_override_enters_object_and_digest(manifest):
    effective = EffectiveArchitecture(manifest)
    effective.override("dma_descriptor_queue_depth", 4)
    assert effective.dma_descriptor_queue_depth == 4
    assert effective.digest() != manifest.digest()
    assert effective.base_digest() == manifest.digest()


def test_non_tuning_override_rejected(manifest):
    effective = EffectiveArchitecture(manifest)
    with pytest.raises(MeshIrError) as err:
        effective.override("sram_bytes", 4096)
    assert err.value.code == "E_CAPABILITY_MISMATCH"


def test_override_does_not_mutate_base(manifest):
    snapshot = copy.deepcopy(manifest)
    effective = EffectiveArchitecture(manifest)
    effective.override("admit_window", 2)
    assert manifest == snapshot
    assert effective.admit_window == 2
    assert manifest.admit_window == snapshot.admit_window


def test_dma_outstanding_comes_from_manifest(manifest):
    effective = EffectiveArchitecture(manifest)
    assert effective.dma_read_outstanding == manifest.dma_read_outstanding
    assert effective.dma_write_outstanding == manifest.dma_write_outstanding
