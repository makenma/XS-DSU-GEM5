from dataclasses import FrozenInstanceError

import pytest

from mesh_ir.scheduled.model import (
    AuthoredProgramOrigin,
    AuthoredVariantLineage,
    CommandSemantics,
    ComputeExecution,
    ControlCommandSource,
    ControlExecution,
    IdSpan,
    ProgramVariant,
    RepeatCommandAttrs,
    ScheduledStream,
    StreamOrderSource,
    VariantMembership,
)


def _empty_membership() -> VariantMembership:
    spans = tuple(IdSpan(1, 0) for _ in range(24))
    return VariantMembership(*spans)


def test_scheduled_public_records_are_deeply_immutable() -> None:
    origin = AuthoredProgramOrigin("golden", "repeat", 1)
    variant = ProgramVariant(1, 1, 1, AuthoredVariantLineage("repeat"), 1, _empty_membership())
    stream = ScheduledStream(1, 0, 0, 0, 1, 0)
    stream_order = StreamOrderSource(1)
    command = CommandSemantics(1, ControlCommandSource(RepeatCommandAttrs(0, 1, 3)), ControlExecution())

    with pytest.raises(FrozenInstanceError):
        origin.version = 2
    with pytest.raises(FrozenInstanceError):
        variant.variant_id = 2
    with pytest.raises(FrozenInstanceError):
        stream.command_count = 2
    with pytest.raises(FrozenInstanceError):
        command.command_id = 2
    assert stream_order.stream_id == 1


def test_compute_execution_preserves_ordered_phase_tuple() -> None:
    execution = ComputeExecution(())

    assert execution.phases == ()
