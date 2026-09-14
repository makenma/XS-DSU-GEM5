"""Strict serial batch replay and its normal-mode contrast (7.5, 17.3-4).

Strict replay installs one initial cache snapshot, serializes batches so that a
batch reserves only after the previous batch is fully terminal, and decides the
whole batch in one batch-wide shadow reservation.  The projection produced here
is the A/B surface: two timing arms must agree on the outcome/fill projection
while their completion ticks differ.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from mesh_ir.generated import abi as A
from mesh_ir.model import MeshIrError, canonical_json_bytes
from mesh_ir.moe_weight_cache import (
    RESERVE_COMMITTED,
    RESERVE_FAILED,
    prove_strict_capacity,
    reserve_batch_wide,
)

STRICT_REPLAY_SCHEMA = "moe_strict_replay_v1"
RESIDENCY_NAMES = {
    A.CACHE_RESIDENCY_OUTCOME.HIT: "hit",
    A.CACHE_RESIDENCY_OUTCOME.ATTACH: "attach",
    A.CACHE_RESIDENCY_OUTCOME.NEW_FILL: "new_fill",
}


@dataclass(frozen=True)
class StrictBatch:
    batch_id: int
    occurrences: tuple

    def ordered(self) -> tuple:
        return tuple(sorted(self.occurrences,
                            key=lambda entry: (entry[0], entry[2], entry[1])))


@dataclass(frozen=True)
class StrictReplayResult:
    arm: str
    batches: tuple
    fills: tuple
    bindings: tuple
    decision_digest: str
    traffic_bytes: int

    def canonical(self) -> dict:
        return strict_replay_projection(self.batches, self.fills)


def strict_batch_barrier(coord, batch_id: int) -> None:
    live = [token for token in coord.tokens.values() if not token.released]
    if live or coord.obligations:
        raise MeshIrError(
            "E_MOE_CACHE",
            "strict replay serializes batches behind live weight work",
            batch_id=batch_id, live_tokens=len(live),
            live_obligations=len(coord.obligations))


def run_strict_replay(coord, batches, fill_ticks, tag_bytes,
                      arm="strict") -> StrictReplayResult:
    for batch in batches:
        prove_strict_capacity(coord, [(layer_id, tag)
                                      for layer_id, _expert, tag
                                      in batch.occurrences])
    outcomes = []
    fills = {}
    bindings = []
    for batch in batches:
        strict_batch_barrier(coord, batch.batch_id)
        ordered = batch.ordered()
        status, tokens = reserve_batch_wide(
            coord, batch.batch_id,
            [(layer_id, tag) for layer_id, _expert, tag in ordered],
            tick=batch.batch_id)
        if status == RESERVE_FAILED:
            raise MeshIrError("E_MOE_CACHE", "strict replay hit a tombstone",
                              batch_id=batch.batch_id)
        if status != RESERVE_COMMITTED:
            raise MeshIrError("E_MOE_CACHE",
                              "strict replay reservation was not committed",
                              batch_id=batch.batch_id)
        served = set()
        for token in tokens:
            if token.fill_key is None:
                continue
            reference = token.fill_key.wire_bytes().hex()
            if reference in served:
                continue
            served.add(reference)
            tag = token.base_key.weight_tag_index
            tick = fill_ticks[tag]
            coord.mark_filling(token.fill_key)
            coord.note_fill_success(token.fill_key, tag_bytes[tag])
            fills[reference] = (tag, tag_bytes[tag], tick)
        decisions = []
        for token, (layer_id, expert_id, _tag) in zip(tokens, ordered):
            fill_ref = (token.fill_key.wire_bytes().hex()
                        if token.fill_key is not None else None)
            decision = {
                "layer_id": layer_id,
                "expert_id": expert_id,
                "weight_tag_index": token.base_key.weight_tag_index,
                "residency": RESIDENCY_NAMES[token.outcome],
                "slot_id": token.slot_id,
                "fill_ref": fill_ref,
            }
            decisions.append(decision)
            bindings.append(decision)
        for token in tokens:
            coord.release_token(token.token_id)
        outcomes.append({"batch_id": batch.batch_id, "decisions": decisions})
    fill_rows = tuple(sorted((reference, tag, byte_count, tick)
                             for reference, (tag, byte_count, tick)
                             in fills.items()))
    return StrictReplayResult(
        arm=arm, batches=tuple(outcomes), fills=fill_rows,
        bindings=tuple(bindings),
        decision_digest=strict_replay_digest(outcomes, fill_rows),
        traffic_bytes=sum(row[2] for row in fill_rows))


def reserve_normal(coord, batch_id, occurrences, tick=0):
    status, tokens = reserve_batch_wide(
        coord, batch_id,
        [(layer_id, tag) for layer_id, _expert, tag
         in StrictBatch(batch_id, occurrences).ordered()],
        tick=tick)
    if status != RESERVE_COMMITTED:
        raise MeshIrError("E_MOE_CACHE",
                          "normal reservation was not committed",
                          batch_id=batch_id, status=status)
    return tokens


def strict_replay_projection(outcomes, fill_rows) -> dict:
    return {
        "schema": STRICT_REPLAY_SCHEMA,
        "batches": list(outcomes),
        "fills": [
            {"fill_ref": reference, "weight_tag_index": tag,
             "bytes": byte_count, "done_tick": tick}
            for reference, tag, byte_count, tick in fill_rows
        ],
    }


def strict_replay_digest(outcomes, fill_rows) -> str:
    projection = {
        "schema": STRICT_REPLAY_SCHEMA,
        "batches": list(outcomes),
        "fills": [
            {"fill_ref": reference, "weight_tag_index": tag,
             "bytes": byte_count}
            for reference, tag, byte_count, _tick in fill_rows
        ],
    }
    return hashlib.sha256(canonical_json_bytes(projection)).hexdigest()
