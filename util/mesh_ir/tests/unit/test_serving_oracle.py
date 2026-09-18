import dataclasses
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.abi.decoder import decode_program
from mesh_ir.generated import abi as A
from mesh_ir.model import MeshIrError
from mesh_ir.serving_oracle import PhaseStep, generate_phase_plan

REPO = Path(__file__).resolve().parents[4]
FIXTURE = REPO / "tests/gem5/ai_mesh/fixtures/gate6"


def serving_min():
    return decode_program((FIXTURE / "serving_min.mshb").read_bytes())


def request_of(program, program_id=1, profile_id=1):
    for request in program.agent_request_profiles:
        if request.program_id == program_id and \
                request.profile_id == profile_id:
            return request
    raise AssertionError("request profile not found")


def test_phase_plan_matches_the_declared_instance_chain():
    program = serving_min()
    request = request_of(program)
    plan = generate_phase_plan(program, request, A.PATH_KIND.INITIAL_PREFILL)
    assert [step.phase for step in plan] == [
        A.PHASE.PREFILL, A.PHASE.DECODE, A.PHASE.PUBLISH]
    assert [step.instance_profile_id for step in plan] == [11, 12, 13]
    assert [step.kv_tokens_before for step in plan] == [0, 8, 9]
    assert [step.valid_tokens_per_member for step in plan] == [8, 1, 0]
    assert [step.cached_tokens_after for step in plan] == [8, 9, 9]
    assert plan[0].host_input_dma_bytes_per_member == 128
    assert plan[0].kv_write_bytes_per_member == 128
    assert plan[1].kv_read_bytes_per_member == 128
    assert plan[1].kv_write_bytes_per_member == 16
    assert plan[2].host_output_dma_bytes_per_member == 128
    assert plan[2].kv_read_bytes_per_member == 0
    assert plan[2].kv_write_bytes_per_member == 0
    assert plan[-1].cached_tokens_after == \
        request.full_input_tokens + request.output_tokens
    assert isinstance(plan[0], PhaseStep)


def test_phase_plan_needs_the_exact_decode_profile():
    program = serving_min()
    request = request_of(program)
    missing = dataclasses.replace(
        program,
        agent_instance_profiles=[
            instance for instance in program.agent_instance_profiles
            if instance.phase != A.PHASE.DECODE])
    with pytest.raises(MeshIrError) as error:
        generate_phase_plan(missing, request, A.PATH_KIND.INITIAL_PREFILL)
    assert "profile" in str(error.value).lower()

    drifted = dataclasses.replace(
        program,
        agent_instance_profiles=[
            dataclasses.replace(instance, kv_tokens_before=99)
            if instance.phase == A.PHASE.DECODE else instance
            for instance in program.agent_instance_profiles])
    with pytest.raises(MeshIrError) as error:
        generate_phase_plan(drifted, request, A.PATH_KIND.INITIAL_PREFILL)
    assert "cursor" in str(error.value).lower()


def test_phase_plan_rejects_byte_equation_violations():
    program = serving_min()
    request = request_of(program)
    cases = (
        ("prefill_write", A.PHASE.PREFILL, "kv_write_bytes_per_member", 112),
        ("decode_read", A.PHASE.DECODE, "kv_read_bytes_per_member", 96),
        ("decode_write", A.PHASE.DECODE, "kv_write_bytes_per_member", 8),
        ("publish_output", A.PHASE.PUBLISH,
         "host_output_dma_bytes_per_member", 64),
        ("prefill_input", A.PHASE.PREFILL,
         "host_input_dma_bytes_per_member", 64),
    )
    for name, phase, field, value in cases:
        tampered = dataclasses.replace(
            program,
            agent_instance_profiles=[
                dataclasses.replace(instance, **{field: value})
                if instance.phase == phase else instance
                for instance in program.agent_instance_profiles])
        with pytest.raises(MeshIrError) as error:
            generate_phase_plan(tampered, request,
                                A.PATH_KIND.INITIAL_PREFILL)
        assert str(error.value), name


def test_phase_plan_rejects_role_matrix_violations():
    program = serving_min()
    request = request_of(program)
    wrong_primary = dataclasses.replace(
        program,
        agent_instance_profiles=[
            dataclasses.replace(instance,
                                primary_output_symbol_id=instance.
                                primary_input_symbol_id)
            if instance.phase == A.PHASE.PREFILL else instance
            for instance in program.agent_instance_profiles])
    with pytest.raises(MeshIrError):
        generate_phase_plan(wrong_primary, request,
                            A.PATH_KIND.INITIAL_PREFILL)
    publish_kv = dataclasses.replace(
        program,
        agent_instance_profiles=[
            dataclasses.replace(instance, primary_kv_symbol_id=99)
            if instance.phase == A.PHASE.PUBLISH else instance
            for instance in program.agent_instance_profiles])
    with pytest.raises(MeshIrError):
        generate_phase_plan(publish_kv, request, A.PATH_KIND.INITIAL_PREFILL)


def two_token_program():
    return decode_program(
        (FIXTURE / "serving_two_tokens.mshb").read_bytes())


def test_two_token_fixture_phase_plan():
    program = two_token_program()
    request = request_of(program)
    assert request.output_tokens == 2
    assert request.host_output_bytes == 256
    plan = generate_phase_plan(program, request, A.PATH_KIND.INITIAL_PREFILL)
    assert [step.phase for step in plan] == [
        A.PHASE.PREFILL, A.PHASE.DECODE, A.PHASE.DECODE, A.PHASE.PUBLISH]
    assert [step.instance_profile_id for step in plan] == [11, 12, 13, 14]
    assert [step.kv_tokens_before for step in plan] == [0, 8, 9, 10]
    assert [step.cached_tokens_after for step in plan] == [8, 9, 10, 10]
    assert [step.kv_read_bytes_per_member for step in plan] == [0, 128, 144, 0]
    assert [step.kv_write_bytes_per_member for step in plan] == [128, 16, 16, 0]
    assert plan[-1].host_output_dma_bytes_per_member == 256
    assert plan[-1].cached_tokens_after == \
        request.full_input_tokens + request.output_tokens


def test_two_token_fixture_matches_the_cross_language_expected():
    import json
    from mesh_ir.model import canonical
    from mesh_ir.serving_profiles import (
        program_profile_key_base_digest,
        serving_capacity_required,
    )
    program = two_token_program()
    expected = json.loads(
        (FIXTURE / "serving_two_tokens_expected.json").read_text())
    assert program.semantic_sha256() == expected["semantic_sha256"]
    assert program_profile_key_base_digest(program).hex() == \
        expected["profile_key_base_digest"]
    assert serving_capacity_required(program).fields() == \
        expected["capacity_required"]
    for name in (
            "agent_request_profiles", "agent_instance_profiles",
            "agent_source_core_map", "agent_instance_member_bindings",
            "agent_request_binding_requirements",
            "agent_publish_surrogate_bindings"):
        actual = [canonical(record) for record in getattr(program, name)]
        assert actual == expected[name], name
