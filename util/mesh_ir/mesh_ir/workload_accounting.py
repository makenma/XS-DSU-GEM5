from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass

from mesh_ir.runtime_reconciliation import _require


@dataclass(frozen=True, order=True)
class TrafficClass:
    instance: int
    entrypoint: int
    profile: int
    core: int
    peer: int
    memory_space: int
    tensor_class: int
    direction: str


def verified_tensor_class_traffic(expected_rows, frames, tensor_roles, *, instances):
    _require(isinstance(expected_rows, dict) and expected_rows,
             "no admitted descriptor to classify its traffic")
    _require(isinstance(tensor_roles, dict) and tensor_roles,
             "the admitted tensor table carries no class")
    _require(type(instances) is int and instances > 0,
             "instance count must be positive")
    _require(isinstance(frames, list) and
             [frame.get("instance") for frame in frames] == list(range(1, instances + 1)),
             "tensor traffic instance frames do not equal the requested instance set")
    classes = {}
    for descriptor_id, row in expected_rows.items():
        role = tensor_roles.get(row.get("tensor_id"))
        _require(role is not None and row.get("tensor_role") == role,
                 "admitted descriptor %s claims class %s for tensor %s, the admitted tensor table classifies it %s"
                 % (descriptor_id, row.get("tensor_role"), row.get("tensor_id"), role))
        _require(row["kind"] in (1, 2, 3, 4),
                 "workload network accounting requires an AXI descriptor")
        space = row["src_memory_space"] if row["direction"] == "READ" else row["dst_memory_space"]
        classes[descriptor_id] = (row["entrypoint_id"], row["profile_id"],
                                  row["issuing_core"], row["peer_core"], space,
                                  role, row["direction"])
    reports = []
    for frame in frames:
        moved_bytes, moved_bursts = Counter(), Counter()
        seen = set()
        for burst in frame["bursts"]:
            descriptor_id = burst["descriptor_id"]
            row = expected_rows.get(descriptor_id)
            _require(row is not None, "archived burst has no admitted descriptor")
            _require(burst["instance"] == frame["instance"] and
                     burst["core_id"] == row["issuing_core"] and
                     burst["command_id"] == row["command_id"],
                     "tensor traffic burst differs from its admitted execution identity")
            identity = (burst["core_id"], burst["command_id"], burst["generation"],
                        descriptor_id, burst["burst_index"])
            _require(identity not in seen, "tensor traffic duplicates an execution burst")
            seen.add(identity)
            _require(burst["channel"] == ("AR" if row["direction"] == "READ" else "AW"),
                     "tensor traffic burst channel differs from its admitted direction")
            moved_bytes[descriptor_id] += burst["useful_bytes"]
            moved_bursts[descriptor_id] += 1
        grouped = {}
        for descriptor_id, row in expected_rows.items():
            _require(moved_bytes[descriptor_id] == row["useful_bytes"] and
                     moved_bursts[descriptor_id] == row["bursts"],
                     "instance %s descriptor %s moved %s bytes/%s transactions, the admitted plan declares %s/%s"
                     % (frame["instance"], descriptor_id, moved_bytes[descriptor_id],
                        moved_bursts[descriptor_id], row["useful_bytes"], row["bursts"]))
            key = TrafficClass(frame["instance"], *classes[descriptor_id])
            entry = grouped.setdefault(key, {"expected_bytes": 0, "actual_bytes": 0,
                                             "expected_transactions": 0, "actual_transactions": 0})
            entry["expected_bytes"] += row["useful_bytes"]
            entry["actual_bytes"] += moved_bytes[descriptor_id]
            entry["expected_transactions"] += row["bursts"]
            entry["actual_transactions"] += moved_bursts[descriptor_id]
        reports.extend({**asdict(key), **entry} for key, entry in sorted(grouped.items()))
    return {"classes": len({(key[-2], key[-1]) for key in classes.values()}),
            "bytes": sum(row["actual_bytes"] for row in reports), "rows": reports}


def verified_collective_landings(program_dir, sections, result, descriptors, *, arch, instances):
    from mesh_ir.generated import abi as A
    from mesh_ir.runtime_reconciliation import admitted_write_identities, verified_landing_events

    identities = admitted_write_identities(program_dir, descriptors, arch=arch,
                                           src_nodes=arch["src_nodes"], instances=instances)
    transfers = {}
    for row in sections["DMA_DESCRIPTORS"]:
        if row["kind"] == 3:
            key = (row["dst"]["owner_core"], row["transfer_id"])
            transfers.setdefault(key, []).append(row["descriptor_id"])
    receivers = {}
    for command in sections["COMMANDS"]:
        if command["opcode"] == A.OPCODE.RECV_WAIT:
            transfer_id = sections["OP_ATTRS"][command["attr_index"] - 1]["transfer_id"]
            key = (command["core_id"], transfer_id)
            _require(key not in receivers, "collective transfer has duplicate RECV_WAIT")
            receivers[key] = command["command_id"]
    _require(set(transfers) == set(receivers), "collective transfers and receivers differ")
    checked = 0
    for frame in result["instances"]:
        executions = {}
        for ledger in frame["cores"].values():
            for row in ledger["observations"]["descriptor_executions"]:
                executions.setdefault(row["descriptor_id"], []).append(row)
        seen = set()
        for aperture in frame["apertures"]:
            for row in aperture["transfers"]:
                key = (aperture["core_id"], row["transfer_id"])
                _require(key in transfers and key not in seen, "collective target/transfer identity differs from admission")
                seen.add(key)
                verified_landing_events(row, admitted_ids=transfers[key], instance=frame["instance"],
                                        identities=identities, executions=executions,
                                        archived_bursts=frame["bursts"])
                receiver = [item for item in frame["cores"][str(key[0])]["observations"]["commands"]
                            if item["command_id"] == receivers[key]]
                _require(len(receiver) == 1 and receiver[0]["issued"] and receiver[0]["terminal"],
                         "collective RECV_WAIT has no unique completed execution")
                _require(row["notified"] and receiver[0]["terminal_tick"] > row["commit_tick"],
                         "collective RECV_WAIT completed before target commit became visible")
                checked += 1
        _require(seen == set(transfers), "collective transfer coverage is incomplete")
    return checked
