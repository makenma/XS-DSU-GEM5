from dataclasses import replace

import pytest

from mesh_ir.execution_view import build_execution_views
from mesh_ir.generated import abi as A
from mesh_ir.model import MeshIrError
from tests.unit.test_abi2_execution_views import profiles, profile_program


def test_each_profile_gets_its_admitted_command_closure(profiles):
    program = profile_program(profiles)
    views = build_execution_views(program)
    assert not views.whole_program
    assert len(views.views) == 2
    commands = set()
    for variant in program.semantics.variants:
        view = views.for_instance(variant.entrypoint_id, variant.profile_id)
        span = variant.membership.commands
        expected = frozenset(range(span.first_id, span.first_id + span.count))
        assert view.command_ids == expected
        assert not commands & expected
        commands.update(expected)
        assert view.command_indices == tuple(row.command_id - 1 for row in program.commands
                                             if row.command_id in expected)
        assert view.event_ids
        assert views.for_profile(variant.profile_id) is view
    assert commands == {row.command_id for row in program.commands}


def test_a_profile_bound_to_another_entrypoint_is_not_resolvable(profiles):
    views = build_execution_views(profile_program(profiles))
    with pytest.raises(MeshIrError, match='E_BINDING_ROLE'):
        views.for_instance(999, 1)


@pytest.mark.parametrize('mutation', ('missing_all', 'missing_profile', 'short', 'overlap',
                                      'outside', 'profile', 'stream', 'core', 'unsorted', 'duplicate'))
def test_range_projection_cannot_change_the_admitted_variant(profiles, mutation):
    program = profile_program(profiles)
    rows = list(program.profile_stream_ranges)
    if mutation == 'missing_all': rows.clear()
    elif mutation == 'missing_profile': rows.pop()
    elif mutation == 'short': rows[0] = replace(rows[0], command_count=rows[0].command_count - 1)
    elif mutation == 'overlap': rows[1] = replace(rows[1], command_begin=rows[0].command_begin)
    elif mutation == 'outside': rows[0] = replace(rows[0], command_count=len(program.commands) + 1)
    elif mutation == 'profile': rows[0] = replace(rows[0], profile_id=999)
    elif mutation == 'stream': rows[0] = replace(rows[0], stream_id=999)
    elif mutation == 'core': rows[0] = replace(rows[0], core_id=999)
    elif mutation == 'unsorted': rows.reverse()
    elif mutation == 'duplicate': rows.append(rows[0])
    with pytest.raises(MeshIrError, match='E_ABI_BOUNDS.*scheduled variant projection'):
        build_execution_views(replace(program, profile_stream_ranges=tuple(rows)))


def test_ranges_without_the_feature_bit_are_rejected(profiles):
    program = replace(profile_program(profiles), required_features=A.SCHEDULED_SEMANTICS)
    with pytest.raises(MeshIrError, match='E_ABI_FEATURE'):
        build_execution_views(program)


def test_transport_stream_owner_drift_is_a_stream_contract_error(profiles):
    program = profile_program(profiles)
    commands = (*program.commands[:-1], replace(program.commands[-1], stream_id=999))
    with pytest.raises(MeshIrError, match='E_STREAM_CONTRACT'):
        build_execution_views(replace(program, commands=commands))
