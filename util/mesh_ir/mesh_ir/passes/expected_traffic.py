from __future__ import annotations

from mesh_ir.architecture import ArchManifest
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.scheduled.assemble import _VerifiedPreTrafficState, assemble_program
from mesh_ir.model import Program


def compute_expected_traffic_stage(verified: _VerifiedPreTrafficState, arch: ArchManifest) -> Program:
    if type(verified) is not _VerifiedPreTrafficState:
        raise MeshIrError("E_ABI_BOUNDS", "pass 21 requires verified pre-traffic state")
    return assemble_program(verified, arch)


__all__ = ["compute_expected_traffic_stage"]
