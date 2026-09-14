"""Keyed deterministic RNG for synthetic MoE routing (contract 7.5).

The key layout, SHA-256 seeding, single SplitMix64 round and Q32 threshold
sampling are frozen: the same inputs must produce byte-identical draws in
Python and C++, and every substitution (library PRNG, modulo sampling,
extra draws) is a contract violation rather than an implementation choice.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

from mesh_ir.model import MeshIrError

RNG_SCHEMA_VERSION = 1
KEY_BYTES = 80
KEY_FORMAT = struct.Struct("<IIQ32sIIHBBIIIIHH")
MASK64 = (1 << 64) - 1
SPLITMIX_GAMMA = 0x9E3779B97F4A7C15
SPLITMIX_MUL1 = 0xBF58476D1CE4E5B9
SPLITMIX_MUL2 = 0x94D049BB133111EB


@dataclass(frozen=True)
class RngKey:
    master_seed: int
    workload_plan_digest: bytes
    user_id: int
    task_seq: int
    repair_round: int
    phase: int
    sequence_ordinal: int
    token_ordinal: int
    layer_id: int
    logical_source_rank: int
    topk_slot: int
    draw_id: int
    rng_schema_version: int = RNG_SCHEMA_VERSION

    def encode(self) -> bytes:
        if len(self.workload_plan_digest) != 32:
            raise MeshIrError("E_MOE_RNG", "workload plan digest must be 32 B")
        if self.rng_schema_version != RNG_SCHEMA_VERSION:
            raise MeshIrError("E_MOE_RNG", "unsupported rng schema version")
        for name in ("master_seed", "user_id", "task_seq", "repair_round",
                     "sequence_ordinal", "token_ordinal", "layer_id",
                     "logical_source_rank", "topk_slot", "draw_id"):
            value = getattr(self, name)
            if not 0 <= value <= MASK64:
                raise MeshIrError("E_MOE_RNG", f"{name} out of range")
        if not 0 <= self.phase <= 0xFF:
            raise MeshIrError("E_MOE_RNG", "phase out of range")
        return KEY_FORMAT.pack(
            self.rng_schema_version, 0, self.master_seed,
            self.workload_plan_digest, self.user_id, self.task_seq,
            self.repair_round, self.phase, 0, self.sequence_ordinal,
            self.token_ordinal, self.layer_id, self.logical_source_rank,
            self.topk_slot, self.draw_id)


def splitmix64_once(seed64: int) -> int:
    z = (seed64 + SPLITMIX_GAMMA) & MASK64
    z = ((z ^ (z >> 30)) * SPLITMIX_MUL1) & MASK64
    z = ((z ^ (z >> 27)) * SPLITMIX_MUL2) & MASK64
    return (z ^ (z >> 31)) & MASK64


def seed64_of(key: RngKey) -> int:
    digest = hashlib.sha256(key.encode()).digest()
    return int.from_bytes(digest[:8], "little")


def draw(key: RngKey) -> int:
    return splitmix64_once(seed64_of(key))


def threshold(total: int, r: int) -> int:
    if not 1 <= total <= MASK64:
        raise MeshIrError("E_MOE_RNG", "weight total out of range")
    return ((r * total) >> 64) & MASK64


def categorical(weights: list, key: RngKey) -> int:
    total = sum(weights)
    if not 1 <= total <= MASK64:
        raise MeshIrError("E_MOE_RNG", "categorical total out of range")
    limit = threshold(total, draw(key))
    cumulative = 0
    for index, weight in enumerate(weights):
        cumulative += weight
        if cumulative > limit:
            return index
    raise MeshIrError("E_MOE_RNG", "categorical draw escaped its table")


def without_replacement(expert_count: int, top_k: int, key_of) -> list:
    if top_k > expert_count:
        raise MeshIrError("E_MOE_RNG", "top-k exceeds expert count")
    remaining = list(range(expert_count))
    selected = []
    for slot in range(top_k):
        r = draw(key_of(slot))
        index = (r * len(remaining)) >> 64
        selected.append(remaining[index])
        del remaining[index]
    return selected


def checked_round_q16(value: int) -> int:
    if value < 0:
        raise MeshIrError("E_MOE_RNG",
                          "q16 rounding needs a non-negative value")
    return (value + 32768) >> 16


def uniform_profile_digest(master_seed: int, layer_id: int, expert_count: int,
                           top_k: int) -> str:
    document = {
        "schema": "uniform_smoke_v1",
        "rng_schema_version": RNG_SCHEMA_VERSION,
        "master_seed": master_seed,
        "layer_id": layer_id,
        "expert_count": expert_count,
        "top_k": top_k,
    }
    return hashlib.sha256(canonical_bytes(document)).hexdigest()


def scenario_provider_digest(layer_digests: list) -> str:
    body = bytearray()
    for layer_id, digest in sorted(layer_digests):
        body += struct.pack("<I", layer_id)
        body += bytes.fromhex(digest)
    return hashlib.sha256(bytes(body)).hexdigest()


def canonical_bytes(document) -> bytes:
    from mesh_ir.model import canonical_json_bytes

    return canonical_json_bytes(document)
