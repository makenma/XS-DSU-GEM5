from __future__ import annotations

from mesh_ir.abi.spans import checked_span
from mesh_ir.model import Program


def stream_commands(program: Program, stream) -> list:
    span = checked_span(
        stream.command_begin, stream.command_count, len(program.commands),
        "stream range out of table",
    )
    return program.commands[span]


def command_waits(program: Program) -> dict:
    waits_by_command = {}
    for command in program.commands:
        span = checked_span(
            command.wait_begin, command.wait_count,
            len(program.command_waits), "wait range out of table",
        )
        waits_by_command[command.command_id] = [
            w.event_id for w in program.command_waits[span]
        ]
    return waits_by_command


def command_prerequisites(program: Program) -> dict:
    producers = {}
    for command in program.commands:
        if command.signal_event:
            producers.setdefault(command.signal_event, set()).add(
                command.command_id)
    for descriptor in program.dma_descriptors:
        producers.setdefault(descriptor.completion_event, set()).add(
            descriptor.command_id)

    waits_by_command = command_waits(program)
    prerequisites = {c.command_id: [] for c in program.commands}
    for stream in program.streams:
        commands = stream_commands(program, stream)
        for previous, current in zip(commands, commands[1:]):
            prerequisites[current.command_id].append(previous.command_id)
    for command in program.commands:
        for event_id in waits_by_command[command.command_id]:
            prerequisites[command.command_id].extend(
                producers.get(event_id, ()))
    return prerequisites


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
