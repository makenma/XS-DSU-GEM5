import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.builder import load_arch
from mesh_ir.effective import EffectiveArchitecture
from mesh_ir.model import MeshIrError


ARCH_PATH = Path(__file__).resolve().parents[4] / "configs/example/ai_mesh/arch/mesh_1x2.yaml"


def _load_variant(tmp_path, old, new):
    path = tmp_path / "arch.yaml"
    source = ARCH_PATH.read_text()
    assert source.count(old) == 1
    path.write_text(source.replace(old, new))
    return load_arch(path)


@pytest.mark.parametrize(
    "old,new",
    (
        ("data_bytes: 32", "data_bytes: 128"),
        ("max_burst_beats: 16", "max_burst_beats: 257"),
        ("cols: 2", "cols: 3"),
        ("tile_stride: 0x400000", "tile_stride: 0x100000"),
        ("tile_stride: 0x400000", "tile_stride: 0x300000"),
        ("base: 0x800000000", "base: 0xfffffffffffff000"),
    ),
)
def test_architecture_rejects_values_outside_runtime_contract(
    tmp_path, old, new
):
    with pytest.raises(MeshIrError) as error:
        _load_variant(tmp_path, old, new)
    assert error.value.code == "E_CAPABILITY_MISMATCH"


@pytest.mark.parametrize(
    "field,value",
    (
        ("dma_read_outstanding", 7),
        ("dma_write_outstanding", 9),
        ("dma_segment_queue_depth", 5),
    ),
)
def test_runtime_override_enters_effective_architecture_digest(
    field, value
):
    manifest = load_arch(ARCH_PATH)
    effective = EffectiveArchitecture(manifest)
    before = effective.digest()
    effective.override(field, value)
    assert getattr(effective, field) == value
    assert effective.digest() != before
    assert effective.base_digest() == manifest.digest()
