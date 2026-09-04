"""Effective runtime architecture: the single configuration object that
runners, SimObject parameters and architecture digests must share.

The base manifest comes from the arch yaml.  Only tuning fields may be
overridden at run time; every override enters the effective digest so two
runs with different behaviour parameters never share one digest.  The base
digest stays bound to the .mshb program (compatibility contract v1:
structure-relevant fields are never overridable).
"""

from __future__ import annotations

import copy

from mesh_ir.model import ArchManifest, MeshIrError

TUNING_OVERRIDE_FIELDS = (
    "dma_descriptor_queue_depth",
    "admit_window",
    "dma_read_outstanding",
    "dma_write_outstanding",
    "dma_segment_queue_depth",
)


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
        self.overrides[field] = value
        self._effective = copy.deepcopy(self.base)
        for name, override_value in self.overrides.items():
            setattr(self._effective, name, override_value)
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
