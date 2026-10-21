"""Deterministic AXI transaction plan of a mesh program.

The NPU Garnet target adapters take per-UID service plans (extra latency /
fault) and the DMA engine submits bursts FIFO per direction per core, so the
accepted transaction order is a pure function of the compiled schedule plus
the published execution counts.  This module is the single definition of that
order: it predicts the UIDs, names the admitted execution and burst each UID
belongs to, and resolves a scenario fault plan into the shared reconciliation
fault identity.

UID layout (``AxiInitiatorState::allocateMeta``):
    (src_node << 48) | (src_port << 40) | (read << 39) | local_counter
where ``local_counter`` is a per-(source, direction) accept sequence.
"""

from __future__ import annotations

import json
from pathlib import Path

from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated.abi import DMA_KIND
from mesh_ir.runtime_gather import descriptor_plan

AXI_KINDS = (DMA_KIND.LOAD, DMA_KIND.STORE, DMA_KIND.P2P_PUSH, DMA_KIND.PREFETCH, DMA_KIND.GATHER_LOAD)


def src_nodes_from_arch(arch) -> dict[int, int]:
    """{core_id: src_node} from the admitted architecture's initiators.

    The architecture manifest owns the core-to-initiator identity; every
    caller that feeds ``execution_keys``/``predict`` derives its ``src_nodes``
    from here instead of restating a per-topology literal.
    """
    return {int(initiator.core_id): int(initiator.src_node)
            for initiator in arch.fabric.core_initiators}



def transaction_arch(arch) -> dict:
    return {"core_ids": list(arch.core_ids),
            "axi_data_bytes": arch.axi_data_bytes,
            "axi_max_burst_beats": arch.axi_max_burst_beats,
            "region_bases": [region.base for region in arch.regions],
            "region_tile_strides": [region.tile_stride or 0 for region in arch.regions],
            "src_nodes": src_nodes_from_arch(arch)}


def execution_keys(
    program_dir,
    arch: dict,
    src_nodes: dict[int, int],
    src_port: int = 0,
    instances: int = 1,
    *, expected_rows=None,
):
    """{core_id: [fact, ...]} in initiator accept order.

    Each fact is ``{uid, direction, address, beats, useful_bytes, command_id,
    descriptor_id, burst_index, execution, instance}``: the admitted execution
    and burst a planned UID belongs to, so a target fault plan is resolved to
    exactly one dispatched execution instead of being trusted as an opaque
    integer.
    """
    program_dir = Path(program_dir)
    schedule = json.loads((program_dir / 'schedule.mesh.json').read_text())
    if expected_rows is None:
        traffic = json.loads((program_dir / 'expected_traffic.json').read_text())
        expected_rows = {row['identity']['descriptor_id']: row for row in traffic['descriptors']}
    width = arch["axi_data_bytes"]
    max_beats = arch["axi_max_burst_beats"]

    result = {}
    for core_id in arch["core_ids"]:
        facts: list[dict] = []
        counters = {True: 0, False: 0}
        src_node = src_nodes[core_id]
        for instance in range(1, instances + 1):
            for command in schedule["sections"]["COMMANDS"]:
                if command["core_id"] != core_id:
                    continue
                descriptors = [
                    row
                    for row in schedule["sections"].get("DMA_DESCRIPTORS", [])
                    if row["command_id"] == command["command_id"]
                ]
                for descriptor in descriptors:
                    kind = descriptor["kind"]
                    if kind not in AXI_KINDS or descriptor['descriptor_id'] not in expected_rows:
                        continue
                    admitted = expected_rows[descriptor['descriptor_id']]
                    is_read = kind in (DMA_KIND.LOAD, DMA_KIND.PREFETCH, DMA_KIND.GATHER_LOAD)
                    plan = descriptor_plan(dict(admitted, kind=kind), width, max_beats)
                    for execution in range(admitted['execution_count']):
                        for burst_index, burst in enumerate(plan.bursts):
                            counter = counters[is_read]
                            counters[is_read] = counter + 1
                            facts.append(
                                {
                                    "uid": (src_node << 48)
                                    | (src_port << 40)
                                    | ((1 if is_read else 0) << 39)
                                    | counter,
                                    "direction": "read" if is_read else "write",
                                    "kind": kind,
                                    "address": burst.beat_base,
                                    "beats": burst.beats,
                                    "useful_bytes": burst.useful_bytes,
                                    "command_id": command["command_id"],
                                    "descriptor_id": descriptor["descriptor_id"],
                                    "burst_index": burst_index,
                                    "execution": execution,
                                    "instance": instance,
                                }
                            )
        result[core_id] = facts
    return result


def predict(
    program_dir,
    arch: dict,
    src_nodes: dict[int, int],
    src_port: int = 0,
    instances: int = 1,
    *, expected_rows=None,
):
    """{core_id: {"read": [uid...], "write": [uid...]}} in accept order."""
    result = {}
    for core_id, facts in execution_keys(
        program_dir, arch, src_nodes, src_port, instances, expected_rows=expected_rows
    ).items():
        result[core_id] = {
            "read": [fact["uid"] for fact in facts if fact["direction"] == "read"],
            "write": [fact["uid"] for fact in facts if fact["direction"] == "write"],
        }
    return result


def target_of(address: int, ranges: list[dict]):
    for entry in ranges:
        if entry["start"] <= address < entry["end"]:
            return entry["dst_node"]
    return None


def resolve_fault_plan(program_dir, arch: dict, planned_faults, expected_rows,
                       instances: int = 1):
    """Resolve a scenario fault plan to one admitted execution.

    Returns ``(descriptors, occurrence, fault_models)``: the faulted descriptor
    list, the 1-based occurrence of the faulted execution in the shared
    reconciliation order, and a per-descriptor model naming the failed burst
    together with the admitted per-burst useful bytes, so the source-side byte
    accounting is verified against the admitted geometry instead of a total.
    Returns ``None`` when the plan injects no error response.
    """
    planned = [
        row
        for row in planned_faults
        if str(row.get("resp", "slverr")).lower() == "slverr"
    ]
    if not planned:
        return None
    program_dir = Path(program_dir)
    keys = execution_keys(program_dir, arch, arch["src_nodes"], instances=instances,
                          expected_rows=expected_rows)
    by_uid: dict[int, list] = {}
    for core_id, facts in keys.items():
        for fact in facts:
            by_uid.setdefault(fact["uid"], []).append((core_id, fact))
    descriptors: set[int] = set()
    occurrences: set[int] = set()
    failed_bursts: set[int] = set()
    for fault in planned:
        matches = by_uid.get(int(fault["uid"]))
        if not matches:
            raise MeshIrError(
                "E_TRAFFIC_MISMATCH",
                "planned fault uid resolves to no admitted transaction",
                uid=int(fault["uid"]),
            )
        for core_id, fact in matches:
            descriptors.add(fact["descriptor_id"])
            failed_bursts.add(fact["burst_index"])
            earlier = {
                (other["instance"], other["execution"])
                for other in keys[core_id]
                if other["descriptor_id"] == fact["descriptor_id"]
                and (other["instance"], other["execution"])
                < (fact["instance"], fact["execution"])
            }
            occurrences.add(len(earlier) + 1)
    if (len(descriptors) != 1 or len(occurrences) != 1
            or len(failed_bursts) != 1):
        raise MeshIrError(
            "E_TRAFFIC_MISMATCH",
            "planned faults do not resolve to one admitted burst",
            descriptors=sorted(descriptors),
            occurrences=sorted(occurrences),
            failed_bursts=sorted(failed_bursts),
        )
    descriptor_id = descriptors.pop()
    row = expected_rows[descriptor_id]
    burst_bytes = [
        burst.useful_bytes
        for burst in descriptor_plan(row, arch["axi_data_bytes"],
                                     arch["axi_max_burst_beats"]).bursts
    ]
    failed_burst = failed_bursts.pop()
    if not 0 <= failed_burst < len(burst_bytes):
        raise MeshIrError(
            "E_TRAFFIC_MISMATCH",
            "the failed burst index is outside the admitted burst plan",
            descriptor_id=descriptor_id,
            failed_burst=failed_burst,
            bursts=len(burst_bytes),
        )
    return (descriptor_id,), occurrences.pop(), {
        descriptor_id: {
            "burst_useful_bytes": burst_bytes,
            "failed_burst": failed_burst,
        }
    }
