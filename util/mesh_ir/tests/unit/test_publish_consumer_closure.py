import dataclasses
from pathlib import Path

import pytest

from mesh_ir.abi.decoder import decode_program
from mesh_ir.generated import abi as A
from mesh_ir.execution_view import build_execution_views
from mesh_ir.model import CommandWait, MeshIrError
from mesh_ir.serving_profiles import verify_instance_io


@pytest.mark.parametrize("transitive", [False, True])
def test_publish_retirement_event_cannot_unlock_data(transitive):
    root = Path(__file__).resolve().parents[4]
    program = decode_program((root / "tests/gem5/ai_mesh/fixtures/gate6/serving_two_tokens.mshb").read_bytes())
    instance = next(i for i in program.agent_instance_profiles if i.phase == A.PHASE.PUBLISH)
    request = program.agent_request_profiles[0]
    view = build_execution_views(program).for_profile(instance.mesh_profile_id)
    commands = [program.commands[index] for index in view.command_indices]
    stores = [c for c in commands if c.opcode == A.OPCODE.DMA_STORE]
    end = next(c for c in commands if c.opcode == A.OPCODE.REQUEST_END)
    source = end.signal_event if transitive else next(d.completion_event for d in program.dma_descriptors if d.command_id == stores[0].command_id)
    target = next(c for c in commands if c.opcode == A.OPCODE.DMA_FILL)
    waits = list(program.command_waits) + [CommandWait(source)]
    mutated = dataclasses.replace(program, command_waits=tuple(waits), commands=tuple(
        dataclasses.replace(c, wait_begin=len(program.command_waits), wait_count=1)
        if c.command_id == target.command_id else c for c in program.commands))
    with pytest.raises(MeshIrError, match="suppressed PUBLISH"):
        verify_instance_io(mutated, request, instance)


def test_runtime_fill_requires_a_unique_member_store_consumer():
    root = Path(__file__).resolve().parents[4]
    program = decode_program((root / 'tests/gem5/ai_mesh/fixtures/gate6/serving_two_tokens.mshb').read_bytes())
    instance = next(row for row in program.agent_instance_profiles if row.phase == A.PHASE.PREFILL)
    view = build_execution_views(program).for_profile(instance.mesh_profile_id)
    consumer = next(command for command in program.commands if command.command_id in view.command_ids and command.opcode == A.OPCODE.DMA_STORE)
    original = program.command_operands[consumer.operand_begin]
    other = next(operand for operand in program.command_operands if operand.allocation_id and operand.allocation_id != original.allocation_id)
    program = dataclasses.replace(program, command_operands=tuple(
        dataclasses.replace(operand, allocation_id=other.allocation_id) if index == consumer.operand_begin else operand
        for index, operand in enumerate(program.command_operands)))
    with pytest.raises(MeshIrError, match='runtime fill has no unique member STORE'):
        verify_instance_io(program, program.agent_request_profiles[0], instance)
