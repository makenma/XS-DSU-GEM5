from dataclasses import replace

from mesh_ir.abi.dependency import command_prerequisites, stream_commands
from tests.unit.test_abi2_execution_views import profiles


def test_stream_order_uses_semantic_ids_instead_of_transport_positions(profiles):
    program = replace(profiles, commands=tuple(reversed(profiles.commands)))
    for stream in program.semantics.streams:
        expected = program.semantics.stream_command_ids[stream.command_begin:stream.command_begin + stream.command_count]
        assert tuple(item.command_id for item in stream_commands(program, stream)) == expected


def test_prerequisites_are_the_admitted_typed_dependency_projection(profiles):
    expected = {item.command_id: set() for item in profiles.commands}
    for dependency in profiles.semantics.dependencies:
        expected[dependency.target_command_id].add(dependency.source_command_id)
    assert {key: set(value) for key, value in command_prerequisites(profiles).items()} == expected


def test_wire_waits_cannot_invent_an_additional_admitted_dependency(profiles):
    program = replace(profiles, commands=tuple(replace(command, signal_event=0, wait_count=0) for command in profiles.commands))
    assert command_prerequisites(program) == command_prerequisites(profiles)
