import subprocess

import pytest

from tests.unit.test_abi2_admission import admission_probe
from mesh_ir.abi.encoder import encode_program

from mesh_ir.architecture import load_arch
from mesh_ir.generated import abi as A
from mesh_ir.scheduled.binding_catalog import ProgramBindingCatalog
from mesh_ir.scheduled.verify import verify_program
from mesh_ir.serving_profiles import apply_request_profile_keys
from mesh_ir.serving_programs import full_view_serving_program


def test_padding_expands_local_work_without_expanding_host_bindings():
    arch = load_arch('configs/example/ai_mesh/arch/mesh_1x2.yaml')
    program = apply_request_profile_keys(full_view_serving_program(arch, padding_tokens=8))
    verify_program(program, arch)
    catalog = ProgramBindingCatalog(program)
    for instance in program.agent_instance_profiles:
        if instance.phase != A.PHASE.PREFILL:
            continue
        slots = catalog.for_variant(instance.mesh_entrypoint_id, instance.mesh_profile_id)
        inputs = [binding.slot for name, binding in slots.items()
                  if name in ('in_slot_0', 'full_view_ordinal1_static_input_symbol_id')]
        assert len(inputs) == instance.member_count
        assert all(slot.required_allocation_bytes == 128 for slot in inputs)


@pytest.mark.parametrize('admission_probe', ['serving_static'], indirect=True)
def test_cpp_admits_padded_inputs_through_their_external_object(admission_probe, tmp_path):
    arch = load_arch('configs/example/ai_mesh/arch/mesh_1x2.yaml')
    program = apply_request_profile_keys(full_view_serving_program(arch, padding_tokens=8))
    binary = tmp_path / 'padded.mshb'
    binary.write_bytes(encode_program(program))
    result = subprocess.run([str(admission_probe), str(binary)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == 'ACCEPTED'
