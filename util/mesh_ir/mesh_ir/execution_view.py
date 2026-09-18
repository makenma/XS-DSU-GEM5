"""Immutable profile execution view derived from PROFILE_STREAM_RANGES."""

from __future__ import annotations

import dataclasses

from mesh_ir.generated import abi as A
from mesh_ir.model import MeshIrError, Program


@dataclasses.dataclass(frozen=True)
class ProfileExecutionView:
    profile_id: int
    entrypoint_id: int
    ranges: tuple
    command_indices: tuple
    command_ids: frozenset
    descriptor_ids: frozenset
    event_ids: frozenset
    core_ids: frozenset

    @property
    def local_control(self) -> tuple:
        return tuple((core, stream) for core, stream, _, _ in self.ranges)


@dataclasses.dataclass(frozen=True)
class ExecutionViewSet:
    whole_program: bool
    views: tuple
    by_instance: dict

    def for_instance(self, entrypoint_id: int,
                     profile_id: int) -> ProfileExecutionView:
        view = self.by_instance.get((entrypoint_id, profile_id))
        if view is None:
            raise MeshIrError(
                "E_BINDING_ROLE",
                "mesh profile has no execution view",
                entrypoint=entrypoint_id, profile=profile_id)
        return view

    def for_profile(self, profile_id: int) -> ProfileExecutionView:
        for view in self.views:
            if view.profile_id == profile_id:
                return view
        raise MeshIrError("E_BINDING_ROLE",
                          "mesh profile has no execution view",
                          profile=profile_id)


def _fail(code: str, message: str, **context) -> None:
    raise MeshIrError(code, message, **context)


def _validate_ranges(program: Program) -> tuple:
    feature = A.PROFILE_SCOPED_EXECUTION_V1
    ranges = tuple(program.profile_stream_ranges)
    if program.required_features & feature:
        if not ranges:
            _fail("E_ABI_FEATURE",
                  "profile-scoped execution needs stream ranges")
    elif ranges:
        _fail("E_ABI_FEATURE", "profile stream ranges need the feature bit")
    if not ranges:
        return ()
    profiles = {profile.profile_id for profile in program.profiles}
    streams = {(stream.core_id, stream.stream_id): stream
               for stream in program.streams}
    keys = []
    covered = [0] * len(program.commands)
    for item in ranges:
        key = (item.profile_id, item.core_id, item.stream_id)
        if key in keys:
            _fail("E_ABI_DUPLICATE", "duplicate profile range",
                  profile=item.profile_id, core=item.core_id,
                  stream=item.stream_id)
        keys.append(key)
        if item.profile_id not in profiles:
            _fail("E_ABI_BOUNDS", "range profile does not exist",
                  profile=item.profile_id)
        stream = streams.get((item.core_id, item.stream_id))
        if stream is None:
            _fail("E_ABI_BOUNDS", "range stream does not exist",
                  core=item.core_id, stream=item.stream_id)
        if item.command_count == 0:
            _fail("E_ABI_BOUNDS", "range must not be empty",
                  profile=item.profile_id)
        begin = item.command_begin
        end = begin + item.command_count
        if end > len(program.commands):
            _fail("E_ABI_BOUNDS", "range leaves the command table",
                  profile=item.profile_id)
        if begin < stream.command_begin or \
                end > stream.command_begin + stream.command_count:
            _fail("E_ABI_BOUNDS", "range leaves its stream window",
                  profile=item.profile_id)
        for index in range(begin, end):
            command = program.commands[index]
            if command.core_id != item.core_id or \
                    command.stream_id != item.stream_id:
                _fail("E_ABI_SECTION_RANGE",
                      "command does not belong to the range stream",
                      command=command.command_id)
            covered[index] += 1
    if keys != sorted(keys):
        _fail("E_ABI_SECTION_RANGE",
              "profile ranges must be sorted by identity")
    for index, count in enumerate(covered):
        if count != 1:
            _fail("E_ABI_SECTION_RANGE",
                  "every command must belong to exactly one profile range",
                  command=program.commands[index].command_id, count=count)
    for profile_id in sorted(profiles):
        if not any(item.profile_id == profile_id for item in ranges):
            _fail("E_ABI_SECTION_RANGE", "profile has no execution range",
                  profile=profile_id)
    return ranges


def _descriptors_of(program: Program, command_ids: frozenset) -> frozenset:
    return frozenset(descriptor.descriptor_id
                     for descriptor in program.dma_descriptors
                     if descriptor.command_id in command_ids)


def _events_of(program: Program, commands, descriptor_ids) -> frozenset:
    events = set()
    waits = program.command_waits
    for command in commands:
        if command.signal_event:
            events.add(command.signal_event)
        end = command.wait_begin + command.wait_count
        events.update(wait.event_id for wait in waits[command.wait_begin:end])
    for descriptor in program.dma_descriptors:
        if descriptor.descriptor_id in descriptor_ids:
            events.add(descriptor.completion_event)
    return frozenset(events)


def _view(program: Program, profile_id: int, entrypoint_id: int,
          ranges: tuple) -> ProfileExecutionView:
    indices = []
    for _, _, begin, count in ranges:
        indices.extend(range(begin, begin + count))
    indices = tuple(sorted(indices))
    commands = tuple(program.commands[index] for index in indices)
    command_ids = frozenset(command.command_id for command in commands)
    descriptor_ids = _descriptors_of(program, command_ids)
    return ProfileExecutionView(
        profile_id=profile_id,
        entrypoint_id=entrypoint_id,
        ranges=ranges,
        command_indices=indices,
        command_ids=command_ids,
        descriptor_ids=descriptor_ids,
        event_ids=_events_of(program, commands, descriptor_ids),
        core_ids=frozenset(core for core, _, _, _ in ranges),
    )


def build_execution_views(program: Program) -> ExecutionViewSet:
    ranges = _validate_ranges(program)
    entrypoint_of = {profile.profile_id: profile.entrypoint_id
                     for profile in program.profiles}
    if not ranges:
        whole = _view(
            program, 0, 0,
            tuple((stream.core_id, stream.stream_id, stream.command_begin,
                   stream.command_count) for stream in program.streams))
        by_instance = {
            (entrypoint.entrypoint_id, profile.profile_id): whole
            for entrypoint in program.entrypoints
            for profile in program.profiles
            if profile.entrypoint_id == entrypoint.entrypoint_id}
        return ExecutionViewSet(whole_program=True, views=(whole,),
                                by_instance=by_instance)
    grouped = {}
    for item in ranges:
        grouped.setdefault(item.profile_id, []).append(
            (item.core_id, item.stream_id, item.command_begin,
             item.command_count))
    views = []
    by_instance = {}
    for profile_id in sorted(grouped):
        view = _view(program, profile_id, entrypoint_of[profile_id],
                     tuple(sorted(grouped[profile_id])))
        views.append(view)
        by_instance[(view.entrypoint_id, profile_id)] = view
    return ExecutionViewSet(whole_program=False, views=tuple(views),
                            by_instance=by_instance)
