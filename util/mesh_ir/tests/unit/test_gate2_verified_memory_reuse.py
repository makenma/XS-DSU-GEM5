from dataclasses import replace

import pytest

import mesh_ir.ir.kernel_verify as kernel_verify
import mesh_ir.passes.schedule as schedule
import mesh_ir.scheduled.verify as scheduled_verify
from mesh_ir.analysis.kernel_work import kernel_work_phases
from mesh_ir.canonical import semantic_sha256
from mesh_ir.compile_config import load_compile_config_text, resolve_compile_config
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.kernel_ir import KernelBundle, KernelModule, StateOrigin
from mesh_ir.passes.execution import PassExecutor
from mesh_ir.passes.hazards import insert_hazard_dependencies_stage
from mesh_ir.passes.scheduled import lower_to_program
from mesh_ir.passes.static_sram import plan_static_sram_stage
from tests.unit.test_gate2_kernel_ir import local_copy_kernel
from tests.unit.test_gate2_scheduled_pipeline import COMPILE, _inputs, _lowering_for_bundle


@pytest.fixture(scope="module")
def multi_variant_context():
    arch, lowering, _ = _inputs()
    first = lowering.bundle.modules[0]
    source = local_copy_kernel()
    second = KernelModule.create(arch.digest().hex(), source.source_semantic_hash, source.entrypoint, "p5", source.tensors, source.computations, source.tensor_lineage, source.computation_lineage, source.placements, source.shards, source.partial_sums, source.objects, source.views, source.states, source.tokens, source.ops)
    bundle = KernelBundle.create(arch.digest().hex(), lowering.bundle.source_graph_set_sha256, (first, second))
    effective = resolve_compile_config(load_compile_config_text(COMPILE.replace("- {profile_id: p4}", "- {profile_id: p4}\n    - {profile_id: p5}"), arch), arch)
    lowered = _lowering_for_bundle(bundle)
    static = plan_static_sram_stage(bundle, arch)
    hazards = insert_hazard_dependencies_stage(static)
    with PassExecutor(1) as executor:
        program = lower_to_program(lowered, arch, effective, executor).program
    records = tuple(scheduled_verify._local_memory_records(program.semantics, variant) for variant in program.semantics.variants)
    assert records[0] != records[1]
    assert kernel_work_phases(records[0]) != kernel_work_phases(records[1])
    return arch, static, hazards, program


def test_program_reuses_one_verified_memory_per_variant_and_reverifies_each_call(multi_variant_context, monkeypatch):
    arch, _, _, program = multi_variant_context
    original = kernel_verify.verify_kernel_memory
    records_seen = []

    def counted(records, arch=None):
        result = original(records, arch)
        records_seen.append(result.records)
        return result

    monkeypatch.setattr(kernel_verify, "verify_kernel_memory", counted)
    monkeypatch.setattr(scheduled_verify, "verify_kernel_memory", counted)
    for invocation in (1, 2):
        assert scheduled_verify.verify_program(program, arch).program is program
        assert len(records_seen) == invocation * len(program.semantics.variants)


def test_hazard_pass_reuses_verified_dependencies_for_each_variant_lifetime(multi_variant_context, monkeypatch):
    _, static, expected, _ = multi_variant_context
    original = kernel_verify.verify_kernel_memory
    records_seen = []

    def counted(records, arch=None):
        result = original(records, arch)
        records_seen.append(result.records)
        return result

    monkeypatch.setattr(kernel_verify, "verify_kernel_memory", counted)
    actual = insert_hazard_dependencies_stage(static)
    assert actual.semantic_sha256 == expected.semantic_sha256
    assert actual.edges == expected.edges
    assert len(records_seen) == len(static.variants)


def test_schedule_reuses_one_verified_memory_per_variant_and_reverifies_each_call(multi_variant_context, monkeypatch):
    arch, _, hazards, _ = multi_variant_context
    expected = schedule.schedule_per_core_streams_stage(hazards, arch)
    original = kernel_verify.verify_kernel_memory
    records_seen = []

    def counted(records, arch=None):
        result = original(records, arch)
        records_seen.append(result.records)
        return result

    monkeypatch.setattr(kernel_verify, "verify_kernel_memory", counted)
    monkeypatch.setattr(schedule, "verify_kernel_memory", counted)
    for invocation in (1, 2):
        actual = schedule.schedule_per_core_streams_stage(hazards, arch)
        assert actual == expected
        assert actual.semantic_sha256 == expected.semantic_sha256
        assert len(records_seen) == invocation * len(hazards.static.variants)


def test_schedule_success_does_not_authorize_a_later_stale_read(multi_variant_context):
    arch, _, hazards, _ = multi_variant_context
    schedule.schedule_per_core_streams_stage(hazards, arch)
    variant = hazards.static.variants[0]
    records = variant.source.records
    operation = next(op for op in records.ops if op.computation_id and op.reads)
    read = operation.reads[0]
    producer = next(write for op in records.ops for write in op.writes if write.new_state_id == read.state_id)
    assert records.states[producer.old_state_id - 1].origin is StateOrigin.EMPTY
    changed_op = replace(operation, reads=(replace(read, state_id=producer.old_state_id), *operation.reads[1:]))
    changed_records = replace(records, ops=tuple(changed_op if op.op_id == operation.op_id else op for op in records.ops))
    changed_variant = replace(variant, source=replace(variant.source, records=changed_records))
    changed_static = replace(hazards.static, variants=(changed_variant, *hazards.static.variants[1:]))
    changed_hazards = replace(hazards, static=changed_static)

    with pytest.raises(MeshIrError) as caught:
        schedule.schedule_per_core_streams_stage(changed_hazards, arch)

    assert caught.value.code == "E_TENSOR_NOT_RESIDENT"


def test_successful_program_verification_does_not_authorize_fresh_stale_state(multi_variant_context):
    arch, _, _, program = multi_variant_context
    assert scheduled_verify.verify_program(program, arch).program is program
    operation = next(op for op in program.semantics.kernel_ops if op.computation_id and op.reads)
    read = operation.reads[0]
    transition = next(write for op in program.semantics.kernel_ops for write in op.writes if write.new_state_id == read.state_id)
    assert program.semantics.states[transition.old_state_id - 1].origin is StateOrigin.EMPTY
    corrupt_op = replace(operation, reads=(replace(read, state_id=transition.old_state_id), *operation.reads[1:]))
    semantics = replace(program.semantics, kernel_ops=tuple(corrupt_op if op.op_id == operation.op_id else op for op in program.semantics.kernel_ops))
    provisional = replace(program, semantics=semantics, semantic_sha256="")
    corrupt = replace(provisional, semantic_sha256=semantic_sha256(provisional.semantic_dict()))

    with pytest.raises(MeshIrError) as caught:
        scheduled_verify.verify_program(corrupt, arch)

    assert caught.value.code == "E_TENSOR_NOT_RESIDENT"


def test_successful_program_verification_does_not_authorize_different_architecture(multi_variant_context):
    arch, _, _, program = multi_variant_context
    assert scheduled_verify.verify_program(program, arch).program is program
    changed_arch = replace(arch, clock_hz=arch.clock_hz * 2)

    with pytest.raises(MeshIrError) as caught:
        scheduled_verify.verify_program(program, changed_arch)

    assert caught.value.code == "E_ARCH_DIGEST"
