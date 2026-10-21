import dataclasses
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.architecture import load_arch
from mesh_ir.generated import abi as A
from mesh_ir.model import MeshIrError
from mesh_ir.canonical import semantic_sha256
from mesh_ir.serving_profiles import (
    apply_request_profile_keys,
    compute_request_profile_keys,
    program_profile_key_base_bytes,
    program_profile_key_base_digest,
    request_profile_key,
    verify_request_profile_keys,
)
from mesh_ir.serving_programs import serving_program

REPO = Path(__file__).resolve().parents[4]
ARCH_PATH = REPO / "configs/example/ai_mesh/arch/mesh_1x2.yaml"

GOLDEN = json.loads((REPO / 'tests/gem5/ai_mesh/fixtures/gate6/serving_min_expected.json').read_text())
BASE_DIGEST_HEX = GOLDEN['profile_key_base_digest']
REQUEST_KEY = int(GOLDEN['agent_request_profiles'][0]['requested_profile_key'], 16)


@pytest.fixture(scope="module")
def arch():
    return load_arch(ARCH_PATH)



@pytest.fixture(scope="module")
def program(arch):
    return serving_program(arch)



def test_base_projection_is_pinned_by_golden(program):
    base_bytes = program_profile_key_base_bytes(program)
    assert base_bytes.startswith(b"AGENT_PROFILE_KEY_BASE_V1\0")
    assert hashlib.sha256(base_bytes).hexdigest() == BASE_DIGEST_HEX
    assert program_profile_key_base_digest(program).hex() == BASE_DIGEST_HEX
    keys = compute_request_profile_keys(program)
    assert [hex(key) for key in keys] == [hex(REQUEST_KEY)]



def test_base_projection_ignores_the_actual_keys(program):
    other = dataclasses.replace(
        program,
        agent_request_profiles=tuple([
            dataclasses.replace(record, requested_profile_key=0xDEADBEEF)
            for record in program.agent_request_profiles
        ]),
    )
    assert program_profile_key_base_bytes(program) == \
        program_profile_key_base_bytes(other)
    assert semantic_sha256(program.semantic_dict()) != semantic_sha256(other.semantic_dict())



def test_key_depends_on_the_base_projection(program):
    base = program_profile_key_base_digest(program)
    record = program.agent_request_profiles[0]
    assert request_profile_key(record, base) == REQUEST_KEY
    shifted = dataclasses.replace(
        program, arch_digest=bytes(32))
    assert request_profile_key(
        record, program_profile_key_base_digest(shifted)) != REQUEST_KEY



def test_apply_sets_nonzero_keys_and_the_loader_accepts(program):
    applied = apply_request_profile_keys(program)
    keys = [r.requested_profile_key
            for r in applied.agent_request_profiles]
    assert keys == [REQUEST_KEY]
    verify_request_profile_keys(applied)
    assert applied.semantic_sha256 != program.semantic_sha256



def test_loader_rejects_a_zero_key(program):
    zeroed = dataclasses.replace(
        program,
        agent_request_profiles=tuple([
            dataclasses.replace(record, requested_profile_key=0)
            for record in program.agent_request_profiles
        ]),
    )
    with pytest.raises(MeshIrError) as err:
        verify_request_profile_keys(zeroed)
    assert err.value.code == "E_REQUEST_PROFILE_KEY"



def test_loader_rejects_a_tampered_key(program):
    applied = apply_request_profile_keys(program)
    tampered = dataclasses.replace(
        applied,
        agent_request_profiles=tuple([
            dataclasses.replace(record,
                                requested_profile_key=record.
                                requested_profile_key ^ 1)
            for record in applied.agent_request_profiles
        ]),
    )
    with pytest.raises(MeshIrError) as err:
        verify_request_profile_keys(tampered)
    assert err.value.code == "E_REQUEST_PROFILE_KEY"



from mesh_ir.abi.decoder import decode_program
from mesh_ir.abi.encoder import encode_program
from mesh_ir.scheduled.verify import verify_program
from mesh_ir.serving_programs import keyed_serving_program


def keyed(program):
    return apply_request_profile_keys(program)



def test_keyed_fixture_passes_full_verification(arch):
    verify_program(keyed_serving_program(arch), arch)



def _expect(arch, program, code):
    with pytest.raises(MeshIrError) as err:
        verify_program(keyed(program), arch)
    assert err.value.code == code



def test_verifier_rejects_a_zero_instance_id(arch, program):
    mutated = dataclasses.replace(
        program,
        agent_instance_profiles=tuple([
            dataclasses.replace(record, instance_profile_id=0)
            if record.instance_profile_id == 11 else record
            for record in program.agent_instance_profiles
        ]),
    )
    _expect(arch, mutated, "E_REQUEST_PROFILE")



def test_verifier_rejects_a_phase_matrix_violation(arch, program):
    mutated = dataclasses.replace(
        program,
        agent_instance_profiles=tuple([
            dataclasses.replace(record, primary_output_symbol_id=1)
            if record.phase == A.PHASE.PREFILL else record
            for record in program.agent_instance_profiles
        ]),
    )
    _expect(arch, mutated, "E_BINDING_ROLE")



def test_verifier_rejects_a_decode_without_a_chunk(arch, program):
    mutated = dataclasses.replace(
        program,
        agent_instance_profiles=tuple([
            dataclasses.replace(record, decode_chunk_tokens=0,
                                kv_write_bytes_per_member=0)
            if record.phase == A.PHASE.DECODE else record
            for record in program.agent_instance_profiles
        ]),
    )
    _expect(arch, mutated, "E_REQUEST_PROFILE")



def test_verifier_rejects_a_kv_byte_mismatch(arch, program):
    mutated = dataclasses.replace(
        program,
        agent_instance_profiles=tuple([
            dataclasses.replace(record, kv_write_bytes_per_member=1)
            if record.phase == A.PHASE.PREFILL else record
            for record in program.agent_instance_profiles
        ]),
    )
    _expect(arch, mutated, "E_KV_TOKEN_MISMATCH")



def test_verifier_rejects_unsorted_requirements(arch, program):
    mutated = dataclasses.replace(
        program,
        agent_request_binding_requirements=tuple(list(reversed(
            program.agent_request_binding_requirements))),
    )
    _expect(arch, mutated, "E_ABI_ORDER")



def test_verifier_rejects_a_rank_beyond_the_source_map(arch, program):
    mutated = dataclasses.replace(
        program,
        agent_instance_member_bindings=tuple([
            dataclasses.replace(record, expected_logical_source_rank=1)
            for record in program.agent_instance_member_bindings
        ]),
    )
    _expect(arch, mutated, "E_REQUEST_PROFILE")



def test_verifier_rejects_publish_bindings_on_a_non_publish_profile(
        arch, program):
    mutated = dataclasses.replace(
        program,
        agent_publish_surrogate_bindings=tuple([
            dataclasses.replace(record, instance_profile_id=11)
            for record in program.agent_publish_surrogate_bindings
        ]),
    )
    _expect(arch, mutated, "E_BINDING_ROLE")



def test_verifier_rejects_a_missing_publish_binding(arch, program):
    mutated = dataclasses.replace(
        program, agent_publish_surrogate_bindings=tuple([]))
    _expect(arch, mutated, "E_BINDING_ROLE")



def test_verifier_rejects_an_unaligned_publish_chunk(arch, program):
    mutated = dataclasses.replace(
        program,
        agent_request_profiles=tuple([
            dataclasses.replace(record, publish_chunk_bytes=7)
            for record in program.agent_request_profiles
        ]),
    )
    _expect(arch, mutated, "E_OUTPUT_CHUNK_MISMATCH")



def test_verifier_rejects_a_double_classified_symbol(arch, program):
    symbols = {}
    for relocation in program.relocations:
        symbols[program.strings[relocation.symbol_sid - 1].value] = \
            relocation.symbol_sid
    mutated = dataclasses.replace(
        program,
        agent_instance_member_bindings=tuple([
            dataclasses.replace(record, static_input_symbol_id=program.agent_request_profiles[0].primary_input_symbol_id)
            if record.instance_profile_id == 11 else record
            for record in program.agent_instance_member_bindings
        ]),
    )
    _expect(arch, mutated, "E_BINDING_ROLE")



from mesh_ir.serving_profiles import (
    SelectorIndex,
    member_slot_base, HOST_LOAD, HOST_STORE, KV_LOAD,
    instance_descriptors,
    verify_descriptor_attribution,
    instance_io_intervals,
    member_rank_vector,
    member_source_core,
    selector_tuple,
    verify_instance_io,
    verify_member_ownership,
    verify_path_closure,
)
from mesh_ir.serving_programs import dual_member_ranks_program


@pytest.mark.parametrize("ranks", [(0, 1), (1, 0), (0, 0)])
def test_rank_vectors_are_exact_and_indexed_by_the_selector(arch, ranks):
    program = dual_member_ranks_program(arch, ranks)
    request = program.agent_request_profiles[0]
    prefill = next(i for i in program.agent_instance_profiles
                   if i.phase == A.PHASE.PREFILL and i.member_count == 2)
    assert member_rank_vector(program, prefill) == ranks
    assert [member_source_core(program, request, prefill, ordinal)
            for ordinal in range(2)] == [program.agent_source_core_map[rank].core_id for rank in ranks]
    index = SelectorIndex(program)
    assert index.select(selector_tuple(program, prefill)) is prefill



def test_selector_closure_requires_every_path_profile(arch, program):
    request = program.agent_request_profiles[0]
    verify_path_closure(program, request)
    without_publish = dataclasses.replace(
        program,
        agent_instance_profiles=tuple([
            i for i in program.agent_instance_profiles
            if i.phase != A.PHASE.PUBLISH]),
    )
    with pytest.raises(MeshIrError) as err:
        verify_path_closure(without_publish, request)
    assert err.value.code == "E_REQUEST_PROFILE"



def test_selector_closure_requires_the_decode_tail(arch, program):
    request = program.agent_request_profiles[0]
    without_decode = dataclasses.replace(
        program,
        agent_instance_profiles=tuple([
            i for i in program.agent_instance_profiles
            if i.phase != A.PHASE.DECODE]),
    )
    with pytest.raises(MeshIrError) as err:
        verify_path_closure(without_decode, request)
    assert err.value.code == "E_REQUEST_PROFILE"



def test_selector_closure_requires_a_declared_reprefill(arch, program):
    request = dataclasses.replace(program.agent_request_profiles[0],
                                  path_mask=0b011)
    with pytest.raises(MeshIrError) as err:
        verify_path_closure(program, request)
    assert err.value.code == "E_REQUEST_PROFILE"



def _with_descriptor(program, descriptor_id, **changes):
    return dataclasses.replace(
        program,
        dma_descriptors=tuple([
            dataclasses.replace(d, **changes) if d.descriptor_id ==
            descriptor_id else d
            for d in program.dma_descriptors
        ]),
    )



def _prefill(program):
    return next(i for i in program.agent_instance_profiles
                if i.phase == A.PHASE.PREFILL)



def _publish(program):
    return next(i for i in program.agent_instance_profiles
                if i.phase == A.PHASE.PUBLISH)



def _decode(program):
    return next(i for i in program.agent_instance_profiles
                if i.phase == A.PHASE.DECODE)



def _phase_descriptors(program, instance, kind):
    return [descriptor for descriptor in
            instance_descriptors(program, instance)
            if descriptor.kind == kind]



def _publish_stores(program):
    return _phase_descriptors(program, _publish(program), A.DMA_KIND.STORE)



def _publish_fill(program):
    return _phase_descriptors(program, _publish(program),
                              A.DMA_KIND.LOCAL_FILL)[0]



def test_io_oracle_accepts_the_fixture(arch, program):
    request = program.agent_request_profiles[0]
    for instance in program.agent_instance_profiles:
        verify_instance_io(program, request, instance)



def test_io_oracle_rejects_a_one_byte_short_load(arch, program):
    request = program.agent_request_profiles[0]
    mutated = _with_descriptor(program, 1, useful_bytes=127)
    with pytest.raises(MeshIrError) as err:
        verify_instance_io(mutated, request, _prefill(mutated))
    assert err.value.code == "E_HOST_IO_SIZE_MISMATCH"



def test_io_oracle_rejects_a_shifted_interval(arch, program):
    request = program.agent_request_profiles[0]
    descriptor = program.dma_descriptors[0]
    shifted = dataclasses.replace(
        descriptor.src, offset_bytes=descriptor.src.offset_bytes + 1)
    mutated = _with_descriptor(program, 1, src=shifted)
    with pytest.raises(MeshIrError) as err:
        verify_instance_io(mutated, request, _prefill(mutated))
    assert err.value.code == "E_BINDING_ROLE"



def test_io_oracle_rejects_an_overlapping_store(arch, program):
    request = program.agent_request_profiles[0]
    overlapped = _with_descriptor(
        program, _publish_stores(program)[0].descriptor_id,
        useful_bytes=96)
    with pytest.raises(MeshIrError) as err:
        verify_instance_io(overlapped, request, _publish(overlapped))
    assert err.value.code == "E_HOST_IO_SIZE_MISMATCH"



def test_io_oracle_rejects_a_wrong_store_role(arch, program):
    request = program.agent_request_profiles[0]
    input_tensor = next(
        r.tensor_id for r in program.relocations
        if program.strings[r.symbol_sid - 1].value == "in_slot_0")
    descriptor = _publish_stores(program)[1]
    wrong = dataclasses.replace(
        descriptor.dst, tensor_id=input_tensor)
    mutated = _with_descriptor(program, descriptor.descriptor_id, dst=wrong)
    with pytest.raises(MeshIrError) as err:
        verify_program(keyed(mutated), arch)
    assert err.value.code == "E_DMA_RANGE"



def test_admission_rejects_a_descriptor_owner_different_from_its_kernel(arch, program):
    mutated = _with_descriptor(program, 1, owner_core=1)
    with pytest.raises(MeshIrError) as err:
        verify_program(keyed(mutated), arch)
    assert err.value.code == "E_RELOCATION"



def test_io_oracle_rejects_a_publish_chunk_overflow(arch, program):
    request = dataclasses.replace(program.agent_request_profiles[0],
                                  publish_chunk_bytes=32)
    with pytest.raises(MeshIrError) as err:
        verify_instance_io(program, request, _publish(program))
    assert err.value.code == "E_OUTPUT_CHUNK_MISMATCH"



def test_io_oracle_rejects_a_kv_read_past_the_prefix(arch, program):
    request = program.agent_request_profiles[0]
    decode = dataclasses.replace(_decode(program), kv_tokens_before=4)
    with pytest.raises(MeshIrError) as err:
        verify_instance_io(program, request, decode)
    assert err.value.code == "E_BINDING_ROLE"



from mesh_ir.model import canonical_json_bytes
from mesh_ir.serving_profiles import (
    ServingCapacity,
    instance_profile_capacity,
    serving_capacity_plan,
    serving_capacity_required,
    unique_weight_symbols,
)


def test_serving_capacity_required_is_pinned_by_golden(arch, program):
    required = serving_capacity_required(program)
    assert required == ServingCapacity(
        batch_weight_binding_entries_per_context=1,
        instance_member_binding_entries_per_context=2,
        batch_interval_entries_per_context=5)
    request = program.agent_request_profiles[0]
    assert unique_weight_symbols(program, request) == 1
    rows = [instance_profile_capacity(program, instance)
            for instance in program.agent_instance_profiles]
    assert rows == [
        {"instance_member_binding_entries": 2,
         "batch_weight_binding_entries": 1, "batch_interval_entries": 5},
        {"instance_member_binding_entries": 1,
         "batch_weight_binding_entries": 1, "batch_interval_entries": 5},
        {"instance_member_binding_entries": 1,
         "batch_weight_binding_entries": 1, "batch_interval_entries": 5},
    ]



def test_serving_capacity_scales_with_the_member_count(arch):
    assigned = serving_capacity_required(serving_program(arch))
    dual = serving_capacity_required(dual_member_ranks_program(arch, (0, 1)))
    assert dual == ServingCapacity(
        batch_weight_binding_entries_per_context=1,
        instance_member_binding_entries_per_context=4,
        batch_interval_entries_per_context=9)
    assert dual.instance_member_binding_entries_per_context > \
        assigned.instance_member_binding_entries_per_context



def test_capacity_plan_projects_every_instance_profile(arch, program):
    plan = serving_capacity_plan(program, ServingCapacity(8, 16, 16))
    rows = plan["per_instance_profile"]
    assert [row["instance_profile_id"] for row in rows] == [11, 12, 13]
    assert [row["program_id"] for row in rows] == [1, 1, 1]
    assert [row["profile_id"] for row in rows] == [1, 1, 1]
    required = plan["required"]
    assert required == {
        "batch_weight_binding_entries_per_context": 1,
        "instance_member_binding_entries_per_context": 2,
        "batch_interval_entries_per_context": 5,
    }
    assert plan["headroom"] == {
        "batch_weight_binding_entries_per_context": 7,
        "instance_member_binding_entries_per_context": 14,
        "batch_interval_entries_per_context": 11,
    }
    payload = {key: value for key, value in plan.items()
               if key != "capacity_plan_digest"}
    assert plan["capacity_plan_digest"] == \
        __import__("hashlib").sha256(
            canonical_json_bytes(payload)).hexdigest()



def test_capacity_plan_rejects_config_below_required(arch, program):
    required = serving_capacity_required(program)
    for name in (
            "batch_weight_binding_entries_per_context",
            "instance_member_binding_entries_per_context",
            "batch_interval_entries_per_context"):
        values = required.fields()
        values[name] -= 1
        with pytest.raises(MeshIrError) as err:
            serving_capacity_plan(program, ServingCapacity(**values))
        assert err.value.code == "E_CAPACITY_PLAN"



def test_capacity_plan_headroom_moves_the_digest(arch, program):
    required = serving_capacity_required(program)
    exact = serving_capacity_plan(program, required)
    roomy = serving_capacity_plan(
        program, ServingCapacity(*[
            value + 1 for value in required.fields().values()]))
    assert exact["headroom"] == {
        "batch_weight_binding_entries_per_context": 0,
        "instance_member_binding_entries_per_context": 0,
        "batch_interval_entries_per_context": 0,
    }
    assert exact["capacity_plan_digest"] != roomy["capacity_plan_digest"]



def test_request_profile_id_u16_bounds(arch, program):
    wide = dataclasses.replace(
        program,
        agent_request_profiles=tuple([
            dataclasses.replace(record, profile_id=0xFFFF)
            for record in program.agent_request_profiles]),
    )
    wide = dataclasses.replace(wide, semantic_sha256=semantic_sha256(wide.semantic_dict()))
    decoded = decode_program(encode_program(wide))
    assert decoded.agent_request_profiles[0].profile_id == 0xFFFF
    too_wide = dataclasses.replace(
        program,
        agent_request_profiles=tuple([
            dataclasses.replace(record, profile_id=0x10000)
            for record in program.agent_request_profiles]),
    )
    too_wide = dataclasses.replace(too_wide, semantic_sha256=semantic_sha256(too_wide.semantic_dict()))
    with pytest.raises(MeshIrError) as err:
        verify_program(too_wide, arch)
    assert err.value.code == "E_ABI_BOUNDS"



def test_partial_decode_tail_profile_is_required(arch, program):
    request = dataclasses.replace(program.agent_request_profiles[0],
                                  output_tokens=3)
    prefill, decode, publish = program.agent_instance_profiles
    full = dataclasses.replace(
        decode, decode_chunk_tokens=2, valid_tokens_per_member=2,
        kv_write_bytes_per_member=2 * 16)
    tail = dataclasses.replace(
        full, instance_profile_id=14, decode_chunk_tokens=1,
        valid_tokens_per_member=1, kv_write_bytes_per_member=16,
        kv_tokens_before=10, member_binding_first=2)
    publish = dataclasses.replace(publish, member_binding_first=3,
                                  kv_tokens_before=11)
    bindings = list(program.agent_instance_member_bindings)
    bindings.insert(2, dataclasses.replace(bindings[1],
                                           instance_profile_id=14))
    with_tail = dataclasses.replace(
        program, agent_request_profiles=tuple([request]),
        agent_instance_profiles=tuple([prefill, full, tail, publish]),
        agent_instance_member_bindings=tuple(bindings))
    verify_path_closure(with_tail, request)
    without_tail = dataclasses.replace(
        with_tail,
        agent_instance_profiles=tuple([prefill, full,
                                 dataclasses.replace(publish,
                                                     member_binding_first=2)]))
    with pytest.raises(MeshIrError) as err:
        verify_path_closure(without_tail, request)
    assert err.value.code == "E_REQUEST_PROFILE"



def test_cross_member_symbol_reuse_is_rejected(arch):
    program = dual_member_ranks_program(arch, (0, 1))
    prefill = next(i for i in program.agent_instance_profiles
                   if i.phase == A.PHASE.PREFILL and i.member_count == 2)
    first = program.agent_instance_member_bindings[0]
    reused = dataclasses.replace(program, agent_instance_member_bindings=tuple([
        first,
        dataclasses.replace(program.agent_instance_member_bindings[1],
                            static_input_symbol_id=first.static_input_symbol_id,
                            static_kv_symbol_id=first.static_kv_symbol_id),
        *program.agent_instance_member_bindings[2:],
    ]))
    with pytest.raises(MeshIrError) as err:
        verify_program(keyed(reused), arch)
    assert err.value.code == "E_BINDING_ROLE"



def test_cross_profile_symbol_role_change_is_rejected(arch, program):
    output_symbol = next(
        r.symbol_sid for r in program.relocations
        if program.strings[r.symbol_sid - 1].value == "out_slot_0")
    mutated = dataclasses.replace(
        program,
        agent_instance_member_bindings=tuple([
            dataclasses.replace(record,
                                static_input_symbol_id=output_symbol)
            if record.static_input_symbol_id else record
            for record in program.agent_instance_member_bindings]),
    )
    with pytest.raises(MeshIrError) as err:
        verify_program(keyed(mutated), arch)
    assert err.value.code == "E_BINDING_ROLE"



SERVING_DETAIL_DISPOSITIONS = {
    "E_REQUEST_PROFILE": ("REQUEST_RECOVERABLE", "PROFILE_ERROR", None),
    "E_REQUEST_PROFILE_KEY": ("REQUEST_RECOVERABLE", "PROFILE_ERROR", None),
    "E_BINDING_ROLE": ("REQUEST_RECOVERABLE", "PARAM_ERROR", None),
    "E_HOST_IO_SIZE_MISMATCH": ("REQUEST_RECOVERABLE", "PROFILE_ERROR", None),
    "E_OUTPUT_CHUNK_MISMATCH": ("REQUEST_RECOVERABLE", "PARAM_ERROR", None),
    "E_KV_TOKEN_MISMATCH": ("REQUEST_RECOVERABLE", "PROFILE_ERROR", None),
    "E_CAPACITY_PLAN": ("CONFIG_FATAL", None, "CONFIG_ERROR"),
}


def test_serving_detail_codes_have_fixed_dispositions():
    import re
    from pathlib import Path

    from mesh_ir.abi import serving_verifier
    from mesh_ir.generated import agent_abi as AG

    source = Path(serving_verifier.__file__).read_text()
    source += Path(__import__("mesh_ir.serving_profiles",
                              fromlist=["x"]).__file__).read_text()
    used = {code.strip('"') for code in re.findall(r'"E_[A-Z0-9_]+"', source)}
    used.discard("E_OK")
    detail_codes = {code for code in used if code in AG.DETAIL_CODE}
    assert detail_codes == set(SERVING_DETAIL_DISPOSITIONS)
    for code, disposition in SERVING_DETAIL_DISPOSITIONS.items():
        assert AG.DETAIL_CODE[code] > 0
        assert AG.DETAIL_DISPOSITION_V1[code] == disposition, code



from mesh_ir.serving_profiles import (
    ZERO_SIDE_EFFECTS,
    capture_preflight,
    preflight_stage,
)


def test_preflight_outcome_records_stage_detail_and_zero_side_effects(arch,
                                                                     program):
    keyed_program = keyed_serving_program(arch)
    zero_key = dataclasses.replace(
        keyed_program,
        agent_request_profiles=tuple([
            dataclasses.replace(record, requested_profile_key=0)
            for record in keyed_program.agent_request_profiles]))
    zero_key = dataclasses.replace(zero_key, semantic_sha256=semantic_sha256(zero_key.semantic_dict()))
    cases = [
        (zero_key, "REQUEST_PREFLIGHT", "E_REQUEST_PROFILE_KEY",
         ("REQUEST_RECOVERABLE", "PROFILE_ERROR", None)),
        (keyed(dataclasses.replace(
            program,
            agent_instance_profiles=tuple([
                dataclasses.replace(record, primary_output_symbol_id=1)
                if record.phase == A.PHASE.PREFILL else record
                for record in program.agent_instance_profiles]))),
         "REQUEST_PREFLIGHT", "E_BINDING_ROLE",
         ("REQUEST_RECOVERABLE", "PARAM_ERROR", None)),
        (keyed(dataclasses.replace(
            program,
            agent_instance_profiles=tuple([
                dataclasses.replace(record, host_input_dma_bytes_per_member=1)
                if record.phase == A.PHASE.PREFILL else record
                for record in program.agent_instance_profiles]))),
         "REQUEST_PREFLIGHT", "E_HOST_IO_SIZE_MISMATCH",
         ("REQUEST_RECOVERABLE", "PROFILE_ERROR", None)),
    ]
    for candidate, stage, code, disposition in cases:
        outcome = capture_preflight(verify_program, candidate, arch)
        assert outcome.stage == stage
        assert outcome.code == code
        assert outcome.disposition == disposition
        assert outcome.admitted is False
        assert outcome.side_effects == ZERO_SIDE_EFFECTS
    assert capture_preflight(verify_program, keyed_program, arch).code == \
        "E_OK"



def test_abi_and_capacity_negatives_report_their_stage(arch):
    missing_bit = dataclasses.replace(serving_program(arch),
                                      required_features=A.SCHEDULED_SEMANTICS)
    outcome = capture_preflight(encode_program, missing_bit)
    assert outcome.code == "E_ABI_FEATURE"
    assert outcome.stage == "ABI_DECODE"
    assert outcome.admitted is False
    assert outcome.side_effects == ZERO_SIDE_EFFECTS
    required = serving_capacity_required(serving_program(arch))
    values = required.fields()
    values["batch_interval_entries_per_context"] -= 1
    outcome = capture_preflight(serving_capacity_plan, serving_program(arch),
                                ServingCapacity(**values))
    assert outcome.code == "E_CAPACITY_PLAN"
    assert outcome.stage == "CAPACITY_PLAN"
    assert outcome.disposition == ("CONFIG_FATAL", None, "CONFIG_ERROR")
    assert outcome.admitted is False
    assert outcome.side_effects == ZERO_SIDE_EFFECTS



from mesh_ir.burst_splitter import plan_descriptor


def _requirement(program, kind):
    return next(r for r in program.agent_request_binding_requirements
                if r.binding_kind == kind)



def test_binding_flags_must_equal_the_exact_combination(arch, program):
    for kind, flags in ((3, 1), (4, 4), (1, 0), (2, 0)):
        mutated = dataclasses.replace(
            program,
            agent_request_binding_requirements=tuple([
                dataclasses.replace(record, binding_flags=flags)
                if record.binding_kind == kind else record
                for record in program.agent_request_binding_requirements]),
        )
        with pytest.raises(MeshIrError) as err:
            verify_program(keyed(mutated), arch)
        assert err.value.code == "E_BINDING_ROLE", (kind, flags)



def test_member_slot_must_resolve_to_the_request_primary_tensor(arch, program):
    output = next(r for r in program.relocations
                  if program.strings[r.symbol_sid - 1].value == "out_slot_0")
    mutated = dataclasses.replace(
        program,
        relocations=tuple([
            dataclasses.replace(record, tensor_id=output.tensor_id,
                                offset_bytes=output.offset_bytes)
            if program.strings[record.symbol_sid - 1].value == "in_slot_0"
            else record
            for record in program.relocations]),
    )
    with pytest.raises(MeshIrError) as err:
        verify_program(keyed(mutated), arch)
    assert err.value.code == "E_RELOCATION"



def test_publish_must_not_read_kv(arch, program):
    kv_read = _phase_descriptors(program, _decode(program),
                                 A.DMA_KIND.LOAD)[0]
    mutated = dataclasses.replace(
        program,
        expected_traffic=tuple([
            dataclasses.replace(row, profile_id=3)
            if row.descriptor_id == kv_read.descriptor_id else row
            for row in program.expected_traffic]),
        agent_instance_profiles=tuple([
            dataclasses.replace(instance, kv_read_bytes_per_member=0)
            if instance.phase == A.PHASE.DECODE else instance
            for instance in program.agent_instance_profiles]),
    )
    with pytest.raises(MeshIrError) as err:
        verify_program(keyed(mutated), arch)
    assert err.value.code == "E_TRAFFIC_MISMATCH"



def test_host_input_rows_expand_by_stride(arch, program):
    read = next(d for d in program.dma_descriptors
                if d.kind == A.DMA_KIND.LOAD and d.src.tensor_id == 1)
    gapped = dataclasses.replace(read, rows=2, row_bytes=64,
                                 src_stride_bytes=96, dst_stride_bytes=64)
    plan = plan_descriptor(64, 2, read.src.offset_bytes, 96,
                           arch.axi_data_bytes, arch.axi_max_burst_beats)
    mutated = dataclasses.replace(
        program,
        dma_descriptors=tuple([gapped if d == read else d
                         for d in program.dma_descriptors]),
        expected_traffic=tuple([
            dataclasses.replace(
                row, bursts=len(plan.bursts), ar_count=len(plan.bursts),
                segments=plan.segments, physical_beat_bytes=plan.beat_bytes,
                r_beats=plan.beat_bytes // arch.axi_data_bytes)
            if row.descriptor_id == read.descriptor_id else row
            for row in program.expected_traffic]),
    )
    with pytest.raises(MeshIrError) as err:
        verify_program(keyed(mutated), arch)
    assert err.value.code == "E_DMA_RANGE"



def test_kv_append_window_is_exact(arch, program):
    kv_store = next(d for d in program.dma_descriptors
                    if d.kind == A.DMA_KIND.STORE and d.row_bytes == 16)
    shifted = dataclasses.replace(
        kv_store, dst=dataclasses.replace(kv_store.dst, offset_bytes=KV_BASE))
    mutated = dataclasses.replace(
        program,
        dma_descriptors=tuple([shifted if d == kv_store else d
                         for d in program.dma_descriptors]))
    with pytest.raises(MeshIrError) as err:
        verify_program(keyed(mutated), arch)
    assert err.value.code == "E_DMA_RANGE"



KV_BASE = 0x400000


def test_reprefill_closure_uses_full_tokens(arch, program):
    request = dataclasses.replace(program.agent_request_profiles[0],
                                  delta_input_tokens=4,
                                  expected_cached_tokens=4,
                                  delta_input_dma_bytes=64, path_mask=4)
    instances = [dataclasses.replace(i, path_kind=A.PATH_KIND.REPREFILL)
                 for i in program.agent_instance_profiles]
    reprefill = dataclasses.replace(program, agent_request_profiles=tuple([request]),
                                    agent_instance_profiles=tuple(instances))
    verify_program(keyed(reprefill), arch)
    wrong = dataclasses.replace(
        reprefill,
        agent_instance_profiles=tuple([
            dataclasses.replace(i, valid_tokens_per_member=4,
                                kv_write_bytes_per_member=64)
            if i.phase == A.PHASE.PREFILL else i for i in instances]))
    with pytest.raises(MeshIrError) as err:
        verify_program(keyed(wrong), arch)
    assert err.value.code == "E_REQUEST_PROFILE"



def test_reuse_closure_requires_its_own_prefill(arch, program):
    request = dataclasses.replace(program.agent_request_profiles[0],
                                  path_mask=2)
    instances = [dataclasses.replace(i, path_kind=A.PATH_KIND.KV_REUSE)
                 for i in program.agent_instance_profiles
                 if i.phase != A.PHASE.PREFILL]
    without = dataclasses.replace(program, agent_request_profiles=tuple([request]),
                                  agent_instance_profiles=tuple(instances))
    with pytest.raises(MeshIrError) as err:
        verify_path_closure(without, request)
    assert err.value.code == "E_REQUEST_PROFILE"



def test_publish_dag_requires_the_store_wait(arch, program):
    store_commands = {descriptor.command_id
                      for descriptor in _publish_stores(program)}
    mutated = dataclasses.replace(
        program,
        commands=tuple([dataclasses.replace(c, wait_count=0)
                  if c.command_id in store_commands else c
                  for c in program.commands]))
    with pytest.raises(MeshIrError) as err:
        verify_program(keyed(mutated), arch)
    assert err.value.code == "E_EVENT_NO_PRODUCER"



def test_publish_dag_requires_the_producer_allocation(arch, program):
    fill = _publish_fill(program)
    command = next(c for c in program.commands
                   if c.command_id == fill.command_id)
    kv_shard = next(s for s in program.shards
                    if s.tensor_id == 3)
    mutated = dataclasses.replace(
        program,
        dma_descriptors=tuple([
            dataclasses.replace(
                d, src=dataclasses.replace(d.src, tensor_id=3,
                                           shard_id=kv_shard.shard_id,
                                           offset_bytes=0x3000),
                dst=dataclasses.replace(d.dst, tensor_id=3,
                                        shard_id=kv_shard.shard_id,
                                        offset_bytes=0x3000))
            if d == fill else d for d in program.dma_descriptors]),
        command_operands=tuple([
            dataclasses.replace(o, tensor_id=3, shard_id=kv_shard.shard_id,
                                allocation_id=kv_shard.allocation_id)
            if command.operand_begin <= index <
            command.operand_begin + command.operand_count else o
            for index, o in enumerate(program.command_operands)]),
    )
    with pytest.raises(MeshIrError) as err:
        verify_program(keyed(mutated), arch)
    assert err.value.code == "E_ABI_BOUNDS"



def test_publish_binding_ordinal_set_is_exact(arch, program):
    record = program.agent_publish_surrogate_bindings[0]
    mutated = dataclasses.replace(
        program,
        agent_publish_surrogate_bindings=tuple([
            record, dataclasses.replace(record, member_ordinal=1)]))
    with pytest.raises(MeshIrError) as err:
        verify_program(keyed(mutated), arch)
    assert err.value.code == "E_BINDING_ROLE"



def test_distinct_rank_vectors_coexist_and_are_selectable(arch):
    from mesh_ir.serving_programs import ranked_full_view_serving_program

    program = ranked_full_view_serving_program(arch)
    index = SelectorIndex(program)
    selected = []
    for ranks in ((0, 1), (1, 0)):
        instance = next(row for row in program.agent_instance_profiles
            if row.phase == A.PHASE.PREFILL and member_rank_vector(program, row) == ranks)
        assert index.select(selector_tuple(program, instance)) is instance
        selected.append(instance)
    assert selected[0] is not selected[1]
    duplicate = dataclasses.replace(program, agent_instance_profiles=(
        *program.agent_instance_profiles, dataclasses.replace(selected[1], instance_profile_id=999999)))
    with pytest.raises(MeshIrError, match="E_ABI_DUPLICATE"):
        SelectorIndex(duplicate)



def test_multi_member_profiles_need_unique_ownership(arch):
    from mesh_ir.serving_programs import full_view_alias_program

    program = dual_member_ranks_program(arch, (0, 0))
    prefill = next(row for row in program.agent_instance_profiles
                   if row.phase == A.PHASE.PREFILL and row.member_count == 2)
    request = program.agent_request_profiles[0]
    verify_instance_io(program, request, prefill)
    aliased = full_view_alias_program(program)
    with pytest.raises(MeshIrError, match="E_BINDING_ROLE"):
        verify_instance_io(aliased, request, prefill)



def _symbol_id(program, name):
    return next(r.symbol_sid for r in program.relocations
                if program.strings[r.symbol_sid - 1].value == name)



def test_member_slot_base_must_match_the_primary_view(arch, program):
    slot = _symbol_id(program, "in_slot_0")
    for amount in (1, 64):
        mutated = dataclasses.replace(
            program,
            relocations=tuple([
                dataclasses.replace(record,
                                    offset_bytes=record.offset_bytes + amount)
                if record.symbol_sid == slot else record
                for record in program.relocations]),
        )
        with pytest.raises(MeshIrError) as err:
            verify_program(keyed(mutated), arch)
        assert err.value.code == "E_RELOCATION", amount



def test_same_core_overlapping_slot_ranges_are_rejected(arch):
    program = dual_member_ranks_program(arch, (0, 0))
    prefill = next(row for row in program.agent_instance_profiles
                   if row.phase == A.PHASE.PREFILL and row.member_count == 2)
    left = member_slot_base(program, prefill, 0, HOST_LOAD).slot
    right = member_slot_base(program, prefill, 1, HOST_LOAD).slot
    shifted = dataclasses.replace(right, reference_binding=dataclasses.replace(
        right.reference_binding, allocation_offset_bytes=left.reference_binding.allocation_offset_bytes + 1))
    overlap = dataclasses.replace(program, semantics=dataclasses.replace(program.semantics,
        binding_slots=tuple(shifted if row.slot_id == right.slot_id else row
                            for row in program.semantics.binding_slots)))
    with pytest.raises(MeshIrError, match="E_BINDING_ROLE"):
        verify_member_ownership(overlap, overlap.agent_request_profiles[0], prefill)



def test_closure_requires_singleton_for_every_rank(arch, program):
    mutated = dataclasses.replace(
        program,
        agent_request_profiles=tuple([
            dataclasses.replace(program.agent_request_profiles[0],
                                source_rank_count=2)]),
        agent_source_core_map=tuple([
            dataclasses.replace(program.agent_source_core_map[0]),
            dataclasses.replace(program.agent_source_core_map[0],
                                core_id=1)]),
    )
    with pytest.raises(MeshIrError) as err:
        verify_path_closure(mutated, mutated.agent_request_profiles[0])
    assert err.value.code == "E_REQUEST_PROFILE"



def test_closure_requires_prefill_for_an_explicit_rank(arch, program):
    instances = list(program.agent_instance_profiles)
    bindings = list(program.agent_instance_member_bindings)
    for instance in program.agent_instance_profiles[1:]:
        new_id = instance.instance_profile_id + 10
        instances.append(dataclasses.replace(
            instance, instance_profile_id=new_id,
            member_binding_first=len(bindings)))
        bindings.append(dataclasses.replace(
            program.agent_instance_member_bindings[
                instance.member_binding_first],
            instance_profile_id=new_id, expected_logical_source_rank=1))
    request = dataclasses.replace(program.agent_request_profiles[0],
                                  source_rank_count=2)
    mutated = dataclasses.replace(
        program, agent_request_profiles=tuple([request]),
        agent_source_core_map=tuple([
            dataclasses.replace(program.agent_source_core_map[0]),
            dataclasses.replace(program.agent_source_core_map[0],
                                core_id=0)]),
        agent_instance_profiles=tuple(instances),
        agent_instance_member_bindings=tuple(bindings),
        agent_publish_surrogate_bindings=(
            tuple(list(program.agent_publish_surrogate_bindings) +
            [dataclasses.replace(
                program.agent_publish_surrogate_bindings[0],
                instance_profile_id=23)])),
    )
    with pytest.raises(MeshIrError) as err:
        verify_path_closure(mutated, request)
    assert err.value.code == "E_REQUEST_PROFILE"



def test_publish_producer_must_be_in_the_publish_closure(arch, program):
    fill = _publish_fill(program)
    mutated = dataclasses.replace(
        program,
        expected_traffic=tuple([
            dataclasses.replace(row, profile_id=1)
            if row.descriptor_id == fill.descriptor_id else row
            for row in program.expected_traffic]),
    )
    with pytest.raises(MeshIrError) as err:
        verify_program(keyed(mutated), arch)
    assert err.value.code == "E_TRAFFIC_MISMATCH"



def test_publish_producer_must_fill_the_whole_allocation(arch, program):
    fill = _publish_fill(program)
    short = dataclasses.replace(fill, row_bytes=64, useful_bytes=64,
                                physical_storage_bytes=64,
                                src_stride_bytes=64, dst_stride_bytes=64)
    mutated = dataclasses.replace(
        program,
        dma_descriptors=tuple([short if d == fill else d
                         for d in program.dma_descriptors]),
        expected_traffic=tuple([
            dataclasses.replace(row, useful_bytes=64)
            if row.descriptor_id == fill.descriptor_id else row
            for row in program.expected_traffic]),
    )
    with pytest.raises(MeshIrError) as err:
        verify_program(keyed(mutated), arch)
    assert err.value.code == "E_DMA_RANGE"



def test_publish_stores_must_read_their_own_slice(arch, program):
    output_tensor = member_slot_base(program, _publish(program), 0, HOST_STORE).tensor.tensor_id
    stores = [d.descriptor_id for d in program.dma_descriptors
              if d.kind == A.DMA_KIND.STORE and
              d.dst.tensor_id == output_tensor]
    source = next(d.src.offset_bytes for d in program.dma_descriptors
                  if d.descriptor_id in stores)
    mutated = dataclasses.replace(
        program,
        dma_descriptors=tuple([
            dataclasses.replace(
                d, src=dataclasses.replace(d.src, offset_bytes=source))
            if d.descriptor_id in stores else d
            for d in program.dma_descriptors]),
    )
    with pytest.raises(MeshIrError) as err:
        verify_program(keyed(mutated), arch)
    assert err.value.code == "E_DMA_RANGE"



def _apply_descriptors(program, replacements):
    return dataclasses.replace(
        program,
        dma_descriptors=tuple([replacements.get(d.descriptor_id, d)
                         for d in program.dma_descriptors]),
        expected_traffic=tuple([
            dataclasses.replace(row,
                                useful_bytes=replacements[
                                    row.descriptor_id].useful_bytes,
                                physical_beat_bytes=row.physical_beat_bytes,
                                segments=row.segments, bursts=row.bursts,
                                ar_count=row.ar_count, aw_count=row.aw_count,
                                b_count=row.b_count, r_beats=row.r_beats,
                                w_beats=row.w_beats)
            if row.descriptor_id in replacements else row
            for row in program.expected_traffic]))



def test_legal_reuse_delta_suffix_is_accepted(arch):
    from mesh_ir.serving_programs import repair_serving_program

    program = keyed(repair_serving_program(arch))
    verify_program(program, arch)
    request = program.agent_request_profiles[1]
    prefill = next(row for row in program.agent_instance_profiles
        if row.path_kind == A.PATH_KIND.KV_REUSE and row.phase == A.PHASE.PREFILL)
    source = member_slot_base(program, prefill, 0, HOST_LOAD)
    loads = [row for row in instance_descriptors(program, prefill)
             if row.kind == A.DMA_KIND.LOAD and row.src.tensor_id == source.tensor.tensor_id]
    assert len(loads) == 1
    assert loads[0].src.offset_bytes == request.input_binding_bytes - request.delta_input_dma_bytes
    assert loads[0].useful_bytes == request.delta_input_dma_bytes
    verify_instance_io(program, request, prefill)



def test_independent_slot_relocation_preserves_object_relative_descriptor(arch, program):
    from mesh_ir.scheduled.addressing import bind_invocation, derive_descriptor_execution_set
    from mesh_ir.scheduled.binding_catalog import ProgramBindingCatalog

    program = keyed(program)
    prefill = _prefill(program)
    binding = member_slot_base(program, prefill, 0, HOST_LOAD)
    originals = tuple(row.slot.reference_binding for row in ProgramBindingCatalog(program).for_variant(
        prefill.mesh_entrypoint_id, prefill.mesh_profile_id).values())
    shifted = dataclasses.replace(binding.slot.reference_binding,
        allocation_offset_bytes=binding.slot.reference_binding.allocation_offset_bytes + 256)
    changed = tuple(shifted if row.slot_id == shifted.slot_id else row for row in originals)
    before = bind_invocation(program, arch, prefill.mesh_entrypoint_id, prefill.mesh_profile_id, originals)
    after = bind_invocation(program, arch, prefill.mesh_entrypoint_id, prefill.mesh_profile_id, changed)
    assert after.program_semantic_sha256 == before.program_semantic_sha256
    assert after.bound_identity_sha256 != before.bound_identity_sha256
    assert next(row for row in after.bindings if row.slot_id == shifted.slot_id) == shifted
    baseline, _ = derive_descriptor_execution_set(program, arch, originals, before.variant_id)
    relocated, _ = derive_descriptor_execution_set(program, arch, changed, after.variant_id)
    assert len(baseline) == len(relocated)
    moved = 0
    for original, updated in zip(baseline, relocated):
        assert original.descriptor.identity == updated.descriptor.identity
        for side in ('src', 'dst'):
            before_address = getattr(original.descriptor, side)
            after_address = getattr(updated.descriptor, side)
            delta = after_address.address - before_address.address
            assert delta in (0, 256)
            moved += delta == 256
    assert moved == 1
    assert program == keyed(program)



def test_member_slots_remain_attributable_when_request_source_core_changes(arch):
    program = dual_member_ranks_program(arch, (0, 1))
    prefill = next(row for row in program.agent_instance_profiles
                   if row.phase == A.PHASE.PREFILL and row.member_count == 2)
    verify_descriptor_attribution(program, program.agent_request_profiles[0], prefill)
    changed = dataclasses.replace(program, agent_source_core_map=(program.agent_source_core_map[0],
        dataclasses.replace(program.agent_source_core_map[1], core_id=1)))
    verify_descriptor_attribution(changed, changed.agent_request_profiles[0], prefill)
    verify_program(keyed(changed), arch)



def test_same_core_independent_views_are_attributable(arch):
    program = dual_member_ranks_program(arch, (0, 0))
    prefill = next(row for row in program.agent_instance_profiles
                   if row.phase == A.PHASE.PREFILL and row.member_count == 2)
    request = program.agent_request_profiles[0]
    verify_member_ownership(program, request, prefill)
    verify_descriptor_attribution(program, request, prefill)
    left = member_slot_base(program, prefill, 0, HOST_LOAD)
    right = member_slot_base(program, prefill, 1, HOST_LOAD)
    assert left.object.object_id != right.object.object_id
    assert left.slot.reference_binding.allocation_offset_bytes != right.slot.reference_binding.allocation_offset_bytes



from mesh_ir.serving_programs import (
    full_view_alias_program,
    full_view_crossed_publish_program,
    full_view_serving_program,
)


def test_full_view_duplicate_rank_independent_views_are_accepted(arch):
    program = keyed(full_view_serving_program(arch))
    verify_program(program, arch)
    dual = next(i for i in program.agent_instance_profiles
                if i.instance_profile_id == 111)
    assert dual.member_count == 2
    assert member_rank_vector(program, dual) == (0, 0)
    assert [member_source_core(program, program.agent_request_profiles[0],
                               dual, ordinal) for ordinal in range(2)] == [0, 0]
    assert [record.member_ordinal for record in
            program.agent_instance_member_bindings[
                dual.member_binding_first:
                dual.member_binding_first + dual.member_count]] == [0, 1]
    publishes = [r for r in program.agent_publish_surrogate_bindings
                 if r.instance_profile_id == 113]
    assert sorted(r.member_ordinal for r in publishes) == [0, 1]
    assert len({r.allocation_id for r in publishes}) == 2



def test_full_view_duplicate_rank_alias_is_rejected(arch):
    program = keyed(full_view_alias_program(full_view_serving_program(arch)))
    with pytest.raises(MeshIrError) as err:
        verify_program(program, arch)
    assert err.value.code == "E_BINDING_ROLE"



def test_full_view_crossed_publish_binding_is_rejected(arch):
    program = keyed(full_view_crossed_publish_program(
        full_view_serving_program(arch)))
    with pytest.raises(MeshIrError) as err:
        verify_program(program, arch)
    assert err.value.code == "E_BINDING_ROLE"



def test_shared_plan_sequence_uses_the_exact_rank_vector(arch):
    from mesh_ir.serving_programs import ranked_full_view_serving_program
    from mesh_ir.serving_profiles import planned_instance_sequence, member_rank_vector
    program = keyed(ranked_full_view_serving_program(arch))
    for ranks in ((0,), (1,), (0, 0), (0, 1), (1, 0), (1, 1)):
        sequence = planned_instance_sequence(program, program.agent_request_profiles[0], 0, ranks)
        assert len(sequence) == 3
        assert all(member_rank_vector(program, row) == ranks for row in sequence)
