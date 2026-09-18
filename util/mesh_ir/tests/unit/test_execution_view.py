import dataclasses
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.builder import load_arch
from mesh_ir.execution_view import build_execution_views
from mesh_ir.generated import abi as A
from mesh_ir.golden_programs import build_single_core_program
from mesh_ir.model import MeshIrError, ProfileStreamRange

ARCH_PATH = Path(__file__).resolve().parents[4] / \
    "configs/example/ai_mesh/arch/mesh_1x2.yaml"
FEATURE = A.PROFILE_SCOPED_EXECUTION_V1


@pytest.fixture(scope="module")
def arch():
    return load_arch(ARCH_PATH)


def _split_program(base, first_count=2, second_count=5):
    entrypoint = dataclasses.replace(base.entrypoints[0], profile_count=2)
    second = dataclasses.replace(base.profiles[0], profile_id=2)
    ranges = (
        ProfileStreamRange(profile_id=1, core_id=0, stream_id=0,
                           command_begin=0, command_count=first_count),
        ProfileStreamRange(profile_id=2, core_id=0, stream_id=0,
                           command_begin=first_count,
                           command_count=second_count),
    )
    return dataclasses.replace(
        base, required_features=FEATURE, entrypoints=[entrypoint],
        profiles=[base.profiles[0], second], profile_stream_ranges=ranges)


def _reject(program, code):
    with pytest.raises(MeshIrError) as err:
        build_execution_views(program)
    assert err.value.code == code


def test_legacy_program_has_one_whole_program_view(arch):
    program = build_single_core_program(arch)
    views = build_execution_views(program)
    assert views.whole_program is True
    assert len(views.views) == 1
    whole = views.views[0]
    assert whole.command_ids == frozenset(c.command_id
                                          for c in program.commands)
    assert whole.ranges == ((0, 0, 0, len(program.commands)),)
    assert views.for_instance(1, 1) is whole


def test_each_profile_gets_its_command_closure(arch):
    program = _split_program(build_single_core_program(arch))
    views = build_execution_views(program)
    assert views.whole_program is False
    first = views.for_instance(1, 1)
    second = views.for_instance(1, 2)
    assert first.command_indices == (0, 1)
    assert second.command_indices == tuple(range(2, 7))
    assert first.command_ids.isdisjoint(second.command_ids)
    assert first.event_ids
    assert first.entrypoint_id == second.entrypoint_id == 1
    assert views.for_profile(2) is second


def test_a_profile_bound_to_another_entrypoint_is_not_resolvable(arch):
    program = _split_program(build_single_core_program(arch))
    views = build_execution_views(program)
    with pytest.raises(MeshIrError) as err:
        views.for_instance(9, 1)
    assert err.value.code == "E_BINDING_ROLE"


def test_feature_without_ranges_is_rejected(arch):
    program = dataclasses.replace(build_single_core_program(arch),
                                  required_features=FEATURE)
    _reject(program, "E_ABI_FEATURE")


def test_ranges_without_the_feature_bit_are_rejected(arch):
    program = _split_program(build_single_core_program(arch))
    program = dataclasses.replace(program, required_features=0)
    _reject(program, "E_ABI_FEATURE")


def test_ranges_must_partition_the_command_table(arch):
    program = _split_program(build_single_core_program(arch), 3, 3)
    _reject(program, "E_ABI_SECTION_RANGE")


def test_ranges_must_not_overlap(arch):
    overlap = (
        ProfileStreamRange(profile_id=1, core_id=0, stream_id=0,
                           command_begin=0, command_count=4),
        ProfileStreamRange(profile_id=2, core_id=0, stream_id=0,
                           command_begin=3, command_count=4),
    )
    program = dataclasses.replace(
        _split_program(build_single_core_program(arch)),
        profile_stream_ranges=overlap)
    _reject(program, "E_ABI_SECTION_RANGE")


def test_range_must_stay_inside_its_stream_window(arch):
    outside = (
        ProfileStreamRange(profile_id=1, core_id=0, stream_id=0,
                           command_begin=0, command_count=8),
        ProfileStreamRange(profile_id=2, core_id=0, stream_id=0,
                           command_begin=7, command_count=0),
    )
    program = dataclasses.replace(
        _split_program(build_single_core_program(arch)),
        profile_stream_ranges=outside)
    _reject(program, "E_ABI_BOUNDS")


def test_unknown_profile_stream_and_core_are_rejected(arch):
    base = _split_program(build_single_core_program(arch))
    bad_profile = dataclasses.replace(
        base, profile_stream_ranges=[
            dataclasses.replace(base.profile_stream_ranges[0], profile_id=7),
            base.profile_stream_ranges[1]])
    _reject(bad_profile, "E_ABI_BOUNDS")
    bad_stream = dataclasses.replace(
        base, profile_stream_ranges=[
            dataclasses.replace(base.profile_stream_ranges[0], stream_id=5),
            base.profile_stream_ranges[1]])
    _reject(bad_stream, "E_ABI_BOUNDS")


def test_unsorted_and_duplicate_ranges_are_rejected(arch):
    base = _split_program(build_single_core_program(arch))
    _reject(dataclasses.replace(
        base, profile_stream_ranges=tuple(
            reversed(base.profile_stream_ranges))), "E_ABI_SECTION_RANGE")
    duplicate = base.profile_stream_ranges + (
        dataclasses.replace(base.profile_stream_ranges[0],
                            command_begin=3, command_count=4),)
    _reject(dataclasses.replace(base, profile_stream_ranges=duplicate),
            "E_ABI_DUPLICATE")


def test_profile_without_a_range_is_rejected(arch):
    program = _split_program(build_single_core_program(arch))
    _reject(dataclasses.replace(
        program, profile_stream_ranges=program.profile_stream_ranges[1:]),
        "E_ABI_SECTION_RANGE")


def test_range_commands_must_match_the_stream_owner(arch):
    program = _split_program(build_single_core_program(arch))
    _reject(dataclasses.replace(
        program, profile_stream_ranges=[
            dataclasses.replace(program.profile_stream_ranges[0],
                                core_id=1),
            program.profile_stream_ranges[1]]), "E_ABI_BOUNDS")
