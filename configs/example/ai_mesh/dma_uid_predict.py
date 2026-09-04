"""Predict the initiator-accepted AXI transaction UIDs of a mesh program.

The NPU Garnet target adapters take deterministic per-UID service plans
(extra latency / fault), and the real DMA engine submits bursts FIFO per
direction per core, so the accepted transaction order is a pure function of
the program schedule.  This helper re-derives that order from the compiled
schedule + traffic oracle: for each core, walk its streams in program
order and enumerate read (LOAD/PREFETCH) and write (STORE/P2P) bursts.

UID layout (AxiInitiatorState::allocateMeta):
    (src_node << 48) | (src_port << 40) | (read << 39) | local_counter
where local_counter is a per-(source, direction) accept sequence.
"""

from __future__ import annotations

import json
from pathlib import Path


def _iter_core_commands(schedule: dict, core_id: int):
    for command in schedule["sections"]["COMMANDS"]:
        if command["core_id"] == core_id:
            yield command


def _descriptors_of(schedule: dict, command_id: int):
    return [
        d
        for d in schedule["sections"].get("DMA_DESCRIPTORS", [])
        if d["command_id"] == command_id
    ]


def _bursts(address: int, useful: int, width: int, max_beats: int):
    a, remaining, bursts = address, useful, []
    while remaining > 0:
        beat_base = a & ~(width - 1)
        head = a - beat_base
        page_cap = 4096 - (beat_base & 0xFFF)
        burst_cap = max_beats * width
        take = min(remaining, page_cap - head, burst_cap - head)
        bursts.append({"address": beat_base, "beats": (head + take + width - 1) // width})
        a += take
        remaining -= take
    return bursts


def predict(program_dir: Path, arch: dict, src_nodes: dict[int, int],
            src_port: int = 0, instances: int = 1):
    """Return {core_id: {"read": [uid...], "write": [uid...]}}.

    src_nodes maps architecture core id -> logical initiator src_node.
    """
    schedule = json.loads((program_dir / "schedule.mesh.json").read_text())
    width = arch["axi_data_bytes"]
    max_beats = arch["axi_max_burst_beats"]

    result = {}
    for core_id in arch["core_ids"]:
        reads: list[int] = []
        writes: list[int] = []
        src_node = src_nodes[core_id]
        for _ in range(instances):
            for command in _iter_core_commands(schedule, core_id):
                for desc in _descriptors_of(schedule, command["command_id"]):
                    kind = desc["kind"]
                    if kind not in (1, 2, 3, 4):  # LOAD/STORE/P2P/PREFETCH
                        continue
                    is_read = kind in (1, 4)
                    region_base = arch["region_bases"][desc["src"]["region_id"]]
                    region_stride = arch["region_tile_strides"][desc["src"]["region_id"]] or 0
                    owner = desc["src"]["owner_core"] or 0
                    for row in range(desc["rows"]):
                        a = (region_base + owner * region_stride +
                             desc["src"]["offset_bytes"] + row * desc["src_stride_bytes"])
                        for _burst in _bursts(a, desc["row_bytes"], width, max_beats):
                            counter = len(reads) if is_read else len(writes)
                            uid = (src_node << 48) | (src_port << 40) | (
                                1 if is_read else 0) << 39 | counter
                            (reads if is_read else writes).append(uid)
        result[core_id] = {"read": reads, "write": writes}
    return result


def target_of(address: int, ranges: list[dict]) -> int | None:
    for entry in ranges:
        if entry["start"] <= address < entry["end"]:
            return entry["dst_node"]
    return None
