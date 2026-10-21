from dataclasses import replace

import pytest

from mesh_ir.canonical import canonical_json_bytes
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.passes import addresses, scheduled
from mesh_ir.passes.execution import PassExecutor
from tests.unit.test_gate2_scheduled_pipeline import _inputs


def test_compiled_verification_derives_physical_records_once(monkeypatch):
    arch, lowering, effective = _inputs()
    calls = []
    original = addresses.globalize_memory_records

    def observed(*args):
        calls.append(args[0])
        return original(*args)

    monkeypatch.setattr(addresses, 'globalize_memory_records', observed)
    with PassExecutor(1) as executor:
        result = scheduled.lower_to_program(lowering, arch, effective, executor)
    assert result.program.commands
    assert len(calls) == len(lowering.bundle.modules)


def test_derivation_cache_preserves_fresh_architecture_admission():
    from mesh_ir.passes.static_sram import plan_static_sram_stage

    arch, lowering, _ = _inputs()
    first = plan_static_sram_stage(lowering.bundle, arch)
    assert plan_static_sram_stage(lowering.bundle, arch) is first
    arch.sram_bytes = 1
    with pytest.raises(MeshIrError):
        plan_static_sram_stage(lowering.bundle, arch)


def test_changed_stage_records_do_not_reuse_the_previous_derivation():
    from mesh_ir.passes.static_sram import plan_static_sram_stage
    from mesh_ir.passes.hazards import insert_hazard_dependencies_stage

    arch, lowering, _ = _inputs()
    state = plan_static_sram_stage(lowering.bundle, arch)
    original = insert_hazard_dependencies_stage(state)
    changed = replace(state, variants=())
    with pytest.raises(MeshIrError):
        insert_hazard_dependencies_stage(changed)
    assert canonical_json_bytes(original.static) == canonical_json_bytes(state)


def test_transport_and_admission_share_one_digest_for_an_immutable_program(monkeypatch):
    from mesh_ir.abi import encode_program
    from mesh_ir.model import Program
    from mesh_ir.scheduled.verify import verify_program, verify_program_kernel_correspondence

    arch, lowering, effective = _inputs()
    with PassExecutor(1) as executor:
        result = scheduled.lower_to_program(lowering, arch, effective, executor)
    program = replace(result.program)
    original = Program.semantic_dict
    calls = []

    def observed(self):
        calls.append(self)
        return original(self)

    monkeypatch.setattr(Program, 'semantic_dict', observed)
    first = encode_program(program)
    verify_program(program, arch)
    verify_program_kernel_correspondence(program, lowering.bundle)
    assert encode_program(program) == first
    assert len(calls) == 1
    with pytest.raises(MeshIrError, match='checksum'):
        verify_program(replace(program, semantic_sha256='0' * 64), arch)


def test_derived_proofs_preserve_program_serialization():
    import pickle
    from mesh_ir.abi import encode_program
    from mesh_ir.scheduled.verify import verify_program

    arch, lowering, effective = _inputs()
    with PassExecutor(1) as executor:
        program = scheduled.lower_to_program(lowering, arch, effective, executor).program
    restored = pickle.loads(pickle.dumps(program))
    verify_program(restored, arch)
    assert encode_program(restored) == encode_program(program)
