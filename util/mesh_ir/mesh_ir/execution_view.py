from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.model import ProfileStreamRange, Program
from mesh_ir.scheduled.model import ProgramSemantics


@dataclass(frozen=True)
class ProfileExecutionView:
    profile_id: int
    entrypoint_id: int
    ranges: tuple[tuple[int, int, int, int], ...]
    command_indices: tuple[int, ...]
    command_ids: frozenset[int]
    descriptor_ids: frozenset[int]
    event_ids: frozenset[int]
    core_ids: frozenset[int]

    @property
    def command_counts(self) -> Mapping[int, int]:
        counts = Counter()
        for core, _, _, count in self.ranges:
            counts[core] += count
        return MappingProxyType(dict(counts))

    @property
    def local_control(self) -> tuple[tuple[int, int], ...]:
        return tuple((core, stream) for core, stream, _, _ in self.ranges)


@dataclass(frozen=True)
class ExecutionViewSet:
    whole_program: bool
    views: tuple[ProfileExecutionView, ...]
    by_instance: Mapping[tuple[int, int], ProfileExecutionView]

    def for_instance(self, entrypoint_id: int, profile_id: int) -> ProfileExecutionView:
        view = self.by_instance.get((entrypoint_id, profile_id))
        if view is None:
            raise MeshIrError('E_BINDING_ROLE', 'mesh profile has no execution view', entrypoint=entrypoint_id, profile=profile_id)
        return view

    def for_profile(self, profile_id: int) -> ProfileExecutionView:
        matches = [view for view in self.views if view.profile_id == profile_id]
        if len(matches) != 1:
            raise MeshIrError('E_BINDING_ROLE', 'mesh profile has no unique execution view', profile=profile_id)
        return matches[0]


def semantic_profile_ranges(semantics: ProgramSemantics) -> tuple[ProfileStreamRange, ...]:
    if type(semantics) is not ProgramSemantics:
        raise MeshIrError('E_ABI_BOUNDS', 'execution views require scheduled semantics')
    rows = []
    next_stream = 1
    for variant in semantics.variants:
        span = variant.membership.streams
        if span.first_id != next_stream or span.count <= 0 or span.first_id - 1 + span.count > len(semantics.streams):
            raise MeshIrError('E_ABI_BOUNDS', 'variant streams do not partition scheduled streams')
        for stream in semantics.streams[span.first_id - 1:span.first_id - 1 + span.count]:
            rows.append(ProfileStreamRange(variant.profile_id, stream.core_id, stream.physical_stream_id, stream.command_begin, stream.command_count))
        next_stream += span.count
    if next_stream != len(semantics.streams) + 1 or not rows:
        raise MeshIrError('E_ABI_BOUNDS', 'execution views require complete variant streams')
    return tuple(sorted(rows, key=lambda item: (item.profile_id, item.core_id, item.stream_id)))


def build_execution_views(program: Program) -> ExecutionViewSet:
    expected_ranges = semantic_profile_ranges(program.semantics)
    if program.required_features & A.PROFILE_SCOPED_EXECUTION_V1:
        if program.profile_stream_ranges != expected_ranges:
            raise MeshIrError('E_ABI_BOUNDS', 'profile ranges differ from scheduled variant projection')
    elif program.profile_stream_ranges:
        raise MeshIrError('E_ABI_FEATURE', 'profile ranges require the profile feature')
    commands = {item.command_id: (index, item) for index, item in enumerate(program.commands)}
    if len(commands) != len(program.commands):
        raise MeshIrError('E_ABI_DUPLICATE', 'execution view command identities are duplicated')
    views = []
    assigned = set()
    for variant in program.semantics.variants:
        ranges = tuple((row.core_id, row.stream_id, row.command_begin, row.command_count) for row in expected_ranges if row.profile_id == variant.profile_id)
        indices = []
        for core, stream, begin, count in ranges:
            if begin < 0 or count <= 0 or begin + count > len(program.semantics.stream_command_ids):
                raise MeshIrError('E_ABI_BOUNDS', 'profile stream leaves semantic command order')
            for command_id in program.semantics.stream_command_ids[begin:begin + count]:
                if command_id not in commands:
                    raise MeshIrError('E_ABI_BOUNDS', 'profile stream command is missing', command=command_id)
                index, command = commands[command_id]
                if (command.core_id, command.stream_id) != (core, stream):
                    raise MeshIrError('E_STREAM_CONTRACT', 'profile stream command owner differs', command=command_id)
                indices.append(index)
        ids = frozenset(program.commands[index].command_id for index in indices)
        span = variant.membership.commands
        if len(ids) != len(indices) or ids != frozenset(range(span.first_id, span.first_id + span.count)) or assigned & ids:
            raise MeshIrError('E_ABI_BOUNDS', 'profile commands differ from variant membership')
        assigned.update(ids)
        descriptor_ids = frozenset(item.descriptor_id for item in program.dma_descriptors if item.command_id in ids)
        event_ids = {item.completion_event for item in program.dma_descriptors if item.descriptor_id in descriptor_ids and item.completion_event}
        for index in indices:
            command = program.commands[index]
            if command.signal_event:
                event_ids.add(command.signal_event)
            if command.wait_begin + command.wait_count > len(program.command_waits):
                raise MeshIrError('E_ABI_BOUNDS', 'profile command waits leave wait table')
            event_ids.update(item.event_id for item in program.command_waits[command.wait_begin:command.wait_begin + command.wait_count])
        views.append(ProfileExecutionView(variant.profile_id, variant.entrypoint_id, ranges, tuple(indices), ids, descriptor_ids, frozenset(event_ids), frozenset(row[0] for row in ranges)))
    by_instance = {(view.entrypoint_id, view.profile_id): view for view in views}
    expected_profiles = {(item.entrypoint_id, item.profile_id) for item in program.profiles}
    if len(by_instance) != len(views) or set(by_instance) != expected_profiles or assigned != set(commands):
        raise MeshIrError('E_ABI_BOUNDS', 'execution views do not exactly cover declared profiles and commands')
    return ExecutionViewSet(len(views) == 1, tuple(views), MappingProxyType(by_instance))
