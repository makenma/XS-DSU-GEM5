"""Semantic token identity for MoE routing (contract 7.3).

`SemanticTokenUidV1` is the only stable token identity and
`SemanticTokenUidLessV1` the only ordering over it: the wire form is
little-endian, so comparisons are numeric field order, never memcmp or
hex ordering.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from mesh_ir.generated import agent_abi as G
from mesh_ir.model import MeshIrError

UID_BYTES = 32
UID_FORMAT = struct.Struct("<QIIHBBIII")
UINT32_MAX = (1 << 32) - 1
UINT64_MAX = (1 << 64) - 1

COMPARISON_FIELDS = (
    "workload_plan_item_id",
    "user_id",
    "task_seq",
    "repair_round",
    "phase",
    "sequence_ordinal",
    "token_ordinal",
    "reserved1",
)

PHASES = (G.SEMANTIC_PHASE.PREFILL, G.SEMANTIC_PHASE.DECODE,
          G.SEMANTIC_PHASE.PUBLISH)


@dataclass(frozen=True, order=False)
class SemanticTokenUid:
    workload_plan_item_id: int
    user_id: int
    task_seq: int
    repair_round: int
    phase: int
    sequence_ordinal: int
    token_ordinal: int
    reserved0: int = 0
    reserved1: int = 0

    def validate(self) -> None:
        if not 0 <= self.workload_plan_item_id <= UINT64_MAX:
            raise MeshIrError("E_MOE_UID",
                              "workload_plan_item_id out of range")
        if self.workload_plan_item_id > UINT32_MAX:
            raise MeshIrError("E_MOE_UID",
                              "UID item id must zero-extend from u32")
        for name in ("user_id", "task_seq", "sequence_ordinal",
                     "token_ordinal"):
            value = getattr(self, name)
            if not 0 <= value <= UINT32_MAX:
                raise MeshIrError("E_MOE_UID", f"{name} out of u32 range")
        if not 0 <= self.repair_round <= 0xFFFF:
            raise MeshIrError("E_MOE_UID", "repair_round out of u16 range")
        if self.phase not in PHASES:
            raise MeshIrError("E_MOE_UID", "unknown semantic phase",
                              phase=self.phase)
        if self.reserved0 != 0 or self.reserved1 != 0:
            raise MeshIrError("E_MOE_UID", "UID reserved fields must be zero")
        if self.phase == G.SEMANTIC_PHASE.PREFILL:
            if self.sequence_ordinal != 0:
                raise MeshIrError("E_MOE_UID",
                                  "prefill UID fixes sequence_ordinal to 0")
        elif self.phase == G.SEMANTIC_PHASE.DECODE:
            if self.token_ordinal != 0:
                raise MeshIrError("E_MOE_UID",
                                  "decode UID fixes token_ordinal to 0")
        elif self.token_ordinal != 0 or self.sequence_ordinal != 0:
            raise MeshIrError("E_MOE_UID", "publish carries no MoE ordinal")

    def encode(self) -> bytes:
        self.validate()
        return UID_FORMAT.pack(
            self.workload_plan_item_id, self.user_id, self.task_seq,
            self.repair_round, self.phase, self.reserved0,
            self.sequence_ordinal, self.token_ordinal, self.reserved1)

    @classmethod
    def decode(cls, data: bytes) -> "SemanticTokenUid":
        if len(data) != UID_BYTES:
            raise MeshIrError("E_MOE_UID", "UID wire must be 32 bytes")
        values = UID_FORMAT.unpack(data)
        uid = cls(workload_plan_item_id=values[0], user_id=values[1],
                  task_seq=values[2], repair_round=values[3], phase=values[4],
                  reserved0=values[5], sequence_ordinal=values[6],
                  token_ordinal=values[7], reserved1=values[8])
        uid.validate()
        return uid

    def sort_key(self) -> tuple:
        self.validate()
        return tuple(getattr(self, name) for name in COMPARISON_FIELDS)

    def semantic_ordinal(self) -> int:
        self.validate()
        if self.phase == G.SEMANTIC_PHASE.PREFILL:
            return self.token_ordinal
        return self.sequence_ordinal

    def route_linear_ordinal(self) -> int:
        self.validate()
        if self.phase == G.SEMANTIC_PHASE.PREFILL:
            return self.token_ordinal
        if self.phase == G.SEMANTIC_PHASE.DECODE:
            return self.sequence_ordinal
        raise MeshIrError("E_MOE_UID", "publish has no route ordinal")


def uid_less(left: SemanticTokenUid, right: SemanticTokenUid) -> bool:
    return left.sort_key() < right.sort_key()
