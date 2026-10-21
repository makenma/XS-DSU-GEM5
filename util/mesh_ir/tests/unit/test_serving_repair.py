import pytest
from dataclasses import replace
from mesh_ir.serving_profiles import member_slot_base, HOST_LOAD

from mesh_ir.abi.decoder import decode_program
from mesh_ir.abi.encoder import encode_program
from mesh_ir.scheduled.verify import verify_program
from mesh_ir.architecture import load_arch
from mesh_ir.generated import abi as A
from mesh_ir.model import MeshIrError
from mesh_ir.serving_profiles import apply_request_profile_keys
from mesh_ir.serving_programs import repair_serving_program


@pytest.fixture
def repair():
    arch = load_arch('configs/example/ai_mesh/arch/mesh_1x2.yaml')
    return arch, apply_request_profile_keys(repair_serving_program(arch))


def test_repair_paths_have_exact_delta_and_full_dma(repair):
    arch, program = repair
    verify_program(decode_program(encode_program(program)), arch)
    request = program.agent_request_profiles[1]
    assert (request.expected_cached_tokens, request.delta_input_tokens,
            request.full_input_tokens) == (10, 8, 18)
    rows = [r for r in program.agent_instance_profiles
            if r.request_profile_id == 2 and r.phase == A.PHASE.PREFILL]
    assert {(r.path_kind, r.kv_tokens_before, r.kv_read_bytes_per_member,
             r.kv_write_bytes_per_member, r.host_input_dma_bytes_per_member)
            for r in rows} == {(1, 10, 160, 128, 128), (2, 0, 0, 288, 256)}


def test_reuse_cannot_read_full_host_context(repair):
    arch, program = repair
    reuse = next(r for r in program.agent_instance_profiles
                 if r.path_kind == A.PATH_KIND.KV_REUSE and r.phase == A.PHASE.PREFILL)
    commands = {r.command_id for r in program.expected_traffic if r.profile_id == reuse.mesh_profile_id}
    slot = member_slot_base(program, reuse, 0, HOST_LOAD)
    load = next(r for r in program.dma_descriptors if r.command_id in commands
                and r.src.tensor_id == slot.tensor.tensor_id and r.kind == A.DMA_KIND.LOAD)
    assert load.src.offset_bytes == 128
    program = replace(program, dma_descriptors=tuple(
        replace(row, src=replace(row.src, offset_bytes=0)) if row == load else row
        for row in program.dma_descriptors))
    with pytest.raises(MeshIrError):
        verify_program(apply_request_profile_keys(program), arch)
