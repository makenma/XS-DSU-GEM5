from dataclasses import replace

import pytest

from mesh_ir.builder import ProgramBuilder
from mesh_ir.canonical import semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.execution_view import build_execution_views
from mesh_ir.generated import abi as A
from mesh_ir.model import ProfileStreamRange
from mesh_ir.scheduled.model import AuthoredProgramOrigin
from tests.unit.test_gate2_authored_facade import _arch, _declare_variant


@pytest.fixture(scope='module')
def profiles():
    arch = _arch()
    builder = ProgramBuilder(arch, AuthoredProgramOrigin('abi2', 'profile-selector', 1))
    _declare_variant(builder, arch, profile_id='small', identity='small')
    _declare_variant(builder, arch, profile_id='large', identity='large')
    return builder.build()


def profile_program(program):
    ranges = []
    for variant in program.semantics.variants:
        span = variant.membership.streams
        for stream in program.semantics.streams[span.first_id - 1:span.first_id - 1 + span.count]:
            ranges.append(ProfileStreamRange(variant.profile_id, stream.core_id, stream.physical_stream_id, stream.command_begin, stream.command_count))
    program = replace(program, required_features=9, profile_stream_ranges=tuple(ranges))
    return replace(program, semantic_sha256=semantic_sha256(program.semantic_dict()))


def test_semantic_variants_are_exact_even_without_serving_extension(profiles):
    views = build_execution_views(profiles)
    assert len(views.views) == 2
    assert not views.whole_program
    expected = [frozenset(range(item.membership.commands.first_id, item.membership.commands.first_id + item.membership.commands.count)) for item in profiles.semantics.variants]
    assert [view.command_ids for view in views.views] == expected
    assert expected[0].isdisjoint(expected[1])
    assert set.union(*(set(ids) for ids in expected)) == {item.command_id for item in profiles.commands}


def test_profile_range_projection_matches_its_semantic_variant(profiles):
    program = profile_program(profiles)
    views = build_execution_views(program)
    assert len(views.views) == 2
    for variant in program.semantics.variants:
        view = views.for_instance(variant.entrypoint_id, variant.profile_id)
        assert view.command_ids == frozenset(range(variant.membership.commands.first_id, variant.membership.commands.first_id + variant.membership.commands.count))


@pytest.mark.parametrize('damage', ['other_variant', 'duplicate', 'missing', 'ungated'])
def test_profile_range_lies_cannot_change_semantic_ownership(profiles, damage):
    program = profile_program(profiles)
    ranges = list(program.profile_stream_ranges)
    if damage == 'other_variant': ranges[0] = replace(ranges[0], command_begin=ranges[1].command_begin)
    elif damage == 'duplicate': ranges[1] = ranges[0]
    elif damage == 'missing': ranges.pop()
    else: program = replace(program, required_features=1)
    with pytest.raises(MeshIrError):
        build_execution_views(replace(program, profile_stream_ranges=tuple(ranges)))


@pytest.mark.parametrize('damage', ['profile_range', 'unknown_feature', 'missing_scheduled'])
def test_program_admission_consumes_extension_contract(profiles, damage):
    from mesh_ir.scheduled.verify import verify_program

    program = profile_program(profiles)
    if damage == 'profile_range':
        first, *rest = program.profile_stream_ranges
        program = replace(program, profile_stream_ranges=(replace(first, command_begin=first.command_begin + 1), *rest))
    elif damage == 'unknown_feature': program = replace(program, required_features=17)
    else: program = replace(program, required_features=8)
    program = replace(program, semantic_sha256=semantic_sha256(program.semantic_dict()))
    with pytest.raises(MeshIrError):
        verify_program(program, _arch())


def test_program_admission_allows_shared_content_without_moe(profiles):
    from mesh_ir.model import ContentDigest
    from mesh_ir.scheduled.verify import verify_program

    tensor = profiles.tensors[0]
    program = replace(profiles, content_digests=(ContentDigest(A.CONTENT_DIGEST_OBJECT_KIND['TENSOR'], 0, tensor.tensor_id, bytes(32)),))
    program = replace(program, semantic_sha256=semantic_sha256(program.semantic_dict()))
    assert verify_program(program, _arch()).program is program
