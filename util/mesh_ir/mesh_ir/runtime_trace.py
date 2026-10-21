from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class TraceFilter:
    cores: tuple[int, ...] = ()
    commands: tuple[int, ...] = ()
    tensors: tuple[int, ...] = ()
    begin: int = 0
    end: int | None = None

    def __post_init__(self):
        if self.begin < 0 or (self.end is not None and self.end < self.begin):
            raise ValueError("invalid trace time interval")

    def accepts(self, row):
        return ((not self.cores or row["core_id"] in self.cores)
                and (not self.commands or row["command_id"] in self.commands)
                and (not self.tensors or row["tensor_id"] in self.tensors)
                and row["tick"] >= self.begin
                and (self.end is None or row["tick"] <= self.end))


def write_mesh_trace(path, sections, result, filters=TraceFilter()):
    commands = {row["command_id"]: row for row in sections["COMMANDS"]}
    descriptors = {row["descriptor_id"]: row for row in sections["DMA_DESCRIPTORS"]}
    rows = []
    for frame in result["instances"]:
        for core_id, ledger in frame["cores"].items():
            observations = ledger["observations"]
            for command in observations["commands"]:
                admitted = commands[command["command_id"]]
                operands = sections["COMMAND_OPERANDS"][admitted["operand_begin"]:
                    admitted["operand_begin"] + admitted["operand_count"]]
                tensors = sorted({row["tensor_id"] for row in operands}) or [0]
                for present, tick, event in (
                    (command["issued"], command["issue_tick"], "command_issued"),
                    (command["terminal"], command["terminal_tick"], "command_terminal"),
                ):
                    if not present:
                        continue
                    for tensor_id in tensors:
                        rows.append({"tick": tick, "core_id": int(core_id),
                                     "request_id": frame["instance"],
                                     "command_id": command["command_id"],
                                     "generation": command["generation"], "event": event,
                                     "opcode": admitted["opcode"], "tensor_id": tensor_id,
                                     "transfer_id": 0, "bytes": 0, "descriptor_id": 0})
            for execution in observations["descriptor_executions"]:
                descriptor = descriptors[execution["descriptor_id"]]
                admitted = commands[execution["command_id"]]
                endpoint = descriptor["src"] if descriptor["kind"] in (1, 3, 4) else descriptor["dst"]
                for present, tick, event in (
                    (True, execution["submit_tick"], "descriptor_submitted"),
                    (execution["committed"], execution["commit_tick"], "descriptor_committed"),
                    (execution["completed"], execution["completion_tick"], "descriptor_completed"),
                ):
                    if not present:
                        continue
                    rows.append({"tick": tick, "core_id": int(core_id),
                                 "request_id": frame["instance"],
                                 "command_id": execution["command_id"],
                                 "generation": execution["generation"], "event": event,
                                 "opcode": admitted["opcode"], "tensor_id": endpoint["tensor_id"],
                                 "transfer_id": descriptor["transfer_id"],
                                 "bytes": descriptor["useful_bytes"],
                                 "descriptor_id": execution["descriptor_id"]})
    rows.sort(key=lambda row: (row["tick"], row["request_id"], row["core_id"],
                               row["command_id"], row["generation"], row["event"],
                               row["descriptor_id"], row["tensor_id"]))
    count = 0
    with Path(path).open("w", encoding="utf-8") as output:
        for row in rows:
            if filters.accepts(row):
                output.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
                count += 1
    return count
