from __future__ import annotations

from mesh_ir.abi.spans import checked_span
from mesh_ir.model import Program


def stream_commands(program: Program, stream) -> tuple:
    span = checked_span(
        stream.command_begin, stream.command_count,
        len(program.semantics.stream_command_ids),
        "stream range out of semantic command order",
    )
    commands = {item.command_id: item for item in program.commands}
    return tuple(commands[identity] for identity in program.semantics.stream_command_ids[span])


def command_prerequisites(program: Program) -> dict[int, tuple[int, ...]]:
    prerequisites = {item.command_id: set() for item in program.commands}
    for dependency in program.semantics.dependencies:
        prerequisites[dependency.target_command_id].add(dependency.source_command_id)
    return {key: tuple(sorted(value)) for key, value in prerequisites.items()}


def prerequisite_closure(prerequisites: dict, target: int) -> set:
    seen = set()
    pending = list(prerequisites.get(target, ()))
    while pending:
        node = pending.pop()
        if node in seen:
            continue
        seen.add(node)
        pending.extend(prerequisites.get(node, ()))
    return seen


def prerequisite_of(prerequisites: dict, target: int,
                    prerequisite: int) -> bool:
    return prerequisite in prerequisite_closure(prerequisites, target)
