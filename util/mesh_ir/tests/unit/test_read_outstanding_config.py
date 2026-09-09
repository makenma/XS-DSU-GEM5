import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "util" / "mesh_ir"))

from mesh_ir.builder import load_arch  # noqa: E402
from mesh_ir.effective import (  # noqa: E402
    EffectiveArchitecture,
    apply_cli_dma_overrides,
)

ARCH_PATH = REPO / "configs/example/ai_mesh/arch/mesh_1x2.yaml"


def _effective():
    return EffectiveArchitecture(load_arch(ARCH_PATH))


def test_asymmetric_outstanding_override():
    effective = _effective()
    args = type("A", (), {})()
    args.read_outstanding = 4
    args.write_outstanding = 7
    apply_cli_dma_overrides(effective, args)
    assert effective.dma_read_outstanding == 4
    assert effective.dma_write_outstanding == 7
    assert effective.digest() != effective.base_digest()


def test_unspecified_override_keeps_architecture_defaults():
    effective = _effective()
    args = type("A", (), {})()
    apply_cli_dma_overrides(effective, args)
    assert effective.dma_read_outstanding == 16
    assert effective.dma_write_outstanding == 16
    assert effective.digest() == effective.base_digest()


def test_individual_outstanding_overrides_change_digest():
    from types import SimpleNamespace
    for argument in ("read_outstanding", "write_outstanding"):
        effective = _effective()
        apply_cli_dma_overrides(effective, SimpleNamespace(**{argument: 7}))
        assert effective.digest() != effective.base_digest()


def test_queue_overrides_enter_effective_digest():
    from types import SimpleNamespace
    from mesh_ir.effective import apply_cli_dma_overrides
    for field, argument in (("dma_segment_queue_depth", "segment_queue_depth"),
                            ("dma_descriptor_queue_depth", "dma_descriptor_queue_depth")):
        effective = _effective()
        apply_cli_dma_overrides(effective, SimpleNamespace(**{argument: 1}))
        assert getattr(effective, field) == 1
        assert effective.digest() != effective.base_digest()
