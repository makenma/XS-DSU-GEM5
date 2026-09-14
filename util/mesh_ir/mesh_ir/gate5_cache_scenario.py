"""Cache coordinator scenario shared by the Python oracle and the C++ runtime.

Both languages replay this exact transition list; the projected snapshot and
the fill identity set must match byte for byte.
"""

from __future__ import annotations

import json

from mesh_ir.generated import abi as A
from mesh_ir.moe_weight_cache import (
    RESERVE_COMMITTED,
    RESERVE_FAILED,
    RESERVE_RESOURCE_WAIT,
    CacheReservationCoordinator,
    weight_fill_source,
)

SCENARIO_NAME = "cache_coordinator_v1"
SLOT_COUNT = 3
SLOT_BYTES = 4096
CACHEABLE_TAGS = (1, 2, 3, 4)
CAPACITY = {"mshr_slots": 3, "eviction_slots": 3, "obligation_slots": 3,
            "subscriber_slots": 6}


def replay() -> dict:
    coordinator = CacheReservationCoordinator(
        core_id=0, slot_count=SLOT_COUNT, slot_bytes=SLOT_BYTES,
        cacheable_tags=CACHEABLE_TAGS, **CAPACITY)
    events = []

    def record(name, status, tokens=()):
        events.append({
            "event": name,
            "status": status,
            "blocked_tags": [key.weight_tag_index for key in (tokens or ())
                             if isinstance(key, object) and
                             hasattr(key, "weight_tag_index") and
                             not hasattr(key, "outcome")],
            "tokens": [
                {"tag": token.base_key.weight_tag_index,
                 "outcome": token.outcome,
                 "slot_id": token.slot_id,
                 "fill_incarnation": (token.fill_key.fill_incarnation
                                      if token.fill_key else None)}
                for token in tokens if hasattr(token, "outcome")],
        })

    status, tokens = coordinator.reserve(1, 1, [1, 2], tick=10)
    record("cold_fill", status, tokens)
    for token in tokens:
        coordinator.mark_filling(token.fill_key)
        coordinator.note_fill_success(token.fill_key, SLOT_BYTES)
    status, warm = coordinator.reserve(2, 1, [1], tick=20)
    record("warm_hit", status, warm)
    status, attach = coordinator.reserve(3, 1, [2, 3], tick=30)
    record("hit_and_new_fill", status, attach)
    in_flight = [token for token in attach if token.fill_key is not None]
    coordinator.mark_filling(in_flight[0].fill_key)
    coordinator.note_fill_success(in_flight[0].fill_key, SLOT_BYTES)
    for token in tokens + warm + attach:
        coordinator.release_token(token.token_id)
    status, evict = coordinator.reserve(4, 1, [4], tick=40)
    record("evict_new_fill", status, evict)
    coordinator.mark_filling(evict[0].fill_key)
    source = weight_fill_source(evict[0].fill_key)
    coordinator.note_fill_failure(
        evict[0].fill_key,
        A.WEIGHT_FILL_FAILURE_SITE.CACHE_FILL_AXI_R, tick=50, source=source)
    coordinator.release_token(evict[0].token_id)
    status, blocked = coordinator.reserve(5, 1, [4], tick=60)
    record("tombstone_blocks", status, blocked)
    slots = coordinator.snapshot()
    return {
        "scenario": SCENARIO_NAME,
        "events": events,
        "slots": slots["slots"],
        "next_fill_incarnation": slots["next_fill_incarnation"],
        "next_use_epoch": coordinator.next_use_epoch,
        "failure_tombstones": slots["failure_tombstones"],
    }


def main() -> int:
    print(json.dumps(replay(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
