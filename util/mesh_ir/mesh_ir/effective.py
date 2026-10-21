from __future__ import annotations

import copy

from mesh_ir.architecture import ArchManifest, validate_arch
from mesh_ir.diagnostics import MeshIrError

TUNING_OVERRIDE_FIELDS = (
    "dma_descriptor_queue_depth",
    "admit_window",
    "dma_read_outstanding",
    "dma_write_outstanding",
    "dma_segment_queue_depth",
    "axi_id_bits",
    "sram_write_bytes_per_cycle_per_bank",
)


def apply_cli_dma_overrides(effective, args) -> None:
    for argument, field in (
        ("read_outstanding", "dma_read_outstanding"),
        ("write_outstanding", "dma_write_outstanding"),
        ("segment_queue_depth", "dma_segment_queue_depth"),
        ("dma_descriptor_queue_depth", "dma_descriptor_queue_depth"),
    ):
        value = getattr(args, argument, None)
        if value is not None:
            effective.override(field, value)


class EffectiveArchitecture:
    def __init__(self, manifest: ArchManifest):
        self.base = manifest
        self.overrides: dict[str, int] = {}
        self._effective = copy.deepcopy(manifest)

    def override(self, field: str, value: int) -> "EffectiveArchitecture":
        if field not in TUNING_OVERRIDE_FIELDS:
            raise MeshIrError(
                "E_CAPABILITY_MISMATCH",
                "field is not a tuning override",
                field=field,
            )
        candidate_overrides = {**self.overrides, field: value}
        candidate = copy.deepcopy(self.base)
        for name, override_value in candidate_overrides.items():
            setattr(candidate, name, override_value)
        validate_arch(candidate)
        self.overrides = candidate_overrides
        self._effective = candidate
        return self

    _DTYPE_ORDER = ("fp32", "fp16", "bf16", "int8", "int32")

    def dtype_vector(self, table, what):
        # Zero marks "no capability for this dtype"; consuming it is the
        # rejection point (verifier + runtime), never a silent fallback.
        return [table.get(name, 0) for name in self._DTYPE_ORDER]

    def digest(self) -> bytes:
        return self._effective.digest()

    def base_digest(self) -> bytes:
        return self.base.digest()

    def __getattr__(self, name: str):
        return getattr(self._effective, name)
