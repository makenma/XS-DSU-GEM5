from dataclasses import fields, replace

import pytest

from mesh_ir.abi.decoder import decode_program
from mesh_ir.abi.encoder import encode_program
from mesh_ir.canonical import semantic_sha256
from mesh_ir.model import AgentRequestProfile
from mesh_ir.serving_profiles import apply_request_profile_keys, program_profile_key_base_digest, verify_request_profile_keys
from tests.unit.test_abi2_codec import wire_program


@pytest.fixture
def program():
    values = {field.name: 0 for field in fields(AgentRequestProfile)}
    values.update(program_id=1, profile_id=1, requested_profile_key=0, delta_input_tokens=16)
    result = replace(wire_program(13), agent_request_profiles=(AgentRequestProfile(**values),))
    return replace(result, semantic_sha256=semantic_sha256(result.semantic_dict()))


def test_profile_identity_is_independent_of_derived_program_checksum(program):
    assert program_profile_key_base_digest(program) == program_profile_key_base_digest(replace(program, semantic_sha256='f' * 64))


def test_binding_profile_keys_returns_immutable_wire_ready_program(program):
    keyed = apply_request_profile_keys(program)
    assert type(keyed.agent_request_profiles) is tuple
    assert keyed.semantic_sha256 == semantic_sha256(keyed.semantic_dict())
    assert keyed.agent_request_profiles[0].requested_profile_key != 0
    verify_request_profile_keys(keyed)
    assert apply_request_profile_keys(keyed) == keyed
    assert decode_program(encode_program(keyed)) == keyed
    assert program.agent_request_profiles[0].requested_profile_key == 0


def test_profile_semantic_change_changes_key_even_with_stale_checksum(program):
    first = apply_request_profile_keys(program)
    other = replace(first, agent_request_profiles=(replace(first.agent_request_profiles[0], delta_input_tokens=17),))
    second = apply_request_profile_keys(other)
    assert second.agent_request_profiles[0].requested_profile_key != first.agent_request_profiles[0].requested_profile_key


def test_cpp_profile_identity_matches_immutable_python_projection(binary_probe, tmp_path, program):
    import subprocess

    keyed = apply_request_profile_keys(program)
    source = tmp_path / 'input.mshb'
    binary = tmp_path / 'output.mshb'
    json = tmp_path / 'output.json'
    source.write_bytes(encode_program(keyed))
    result = subprocess.run([str(binary_probe), str(source), str(binary), str(json), 'profile-key'], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == [program_profile_key_base_digest(keyed).hex(), str(keyed.agent_request_profiles[0].requested_profile_key), 'ACCEPTED']
    assert binary.read_bytes() == source.read_bytes()
    assert json.read_bytes() == keyed.canonical_bytes()


from tests.unit.test_abi2_codec import binary_probe
