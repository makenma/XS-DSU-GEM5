import dataclasses
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir.builder import load_arch
from mesh_ir.generated import abi as A
from mesh_ir.model import MeshIrError
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

BASE_DIGEST_HEX = (
    "6abbcef487aebebdfe977d712ccdc911"
    "c95cb1365a2833686b0dcf1cbf079a67"
)
REQUEST_KEY = 0xD5DF2DD59659B6BA


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
        agent_request_profiles=[
            dataclasses.replace(record, requested_profile_key=0xDEADBEEF)
            for record in program.agent_request_profiles
        ],
    )
    assert program_profile_key_base_bytes(program) == \
        program_profile_key_base_bytes(other)
    assert program.semantic_sha256() != other.semantic_sha256()


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
    assert applied.semantic_sha256() != program.semantic_sha256()


def test_loader_rejects_a_zero_key(program):
    zeroed = dataclasses.replace(
        program,
        agent_request_profiles=[
            dataclasses.replace(record, requested_profile_key=0)
            for record in program.agent_request_profiles
        ],
    )
    with pytest.raises(MeshIrError) as err:
        verify_request_profile_keys(zeroed)
    assert err.value.code == "E_REQUEST_PROFILE_KEY"


def test_loader_rejects_a_tampered_key(program):
    applied = apply_request_profile_keys(program)
    tampered = dataclasses.replace(
        applied,
        agent_request_profiles=[
            dataclasses.replace(record,
                                requested_profile_key=record.
                                requested_profile_key ^ 1)
            for record in applied.agent_request_profiles
        ],
    )
    with pytest.raises(MeshIrError) as err:
        verify_request_profile_keys(tampered)
    assert err.value.code == "E_REQUEST_PROFILE_KEY"


from mesh_ir.abi.decoder import decode_program
from mesh_ir.abi.encoder import encode_program
from mesh_ir.abi.verifier import verify_program
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
        agent_instance_profiles=[
            dataclasses.replace(record, instance_profile_id=0)
            if record.instance_profile_id == 11 else record
            for record in program.agent_instance_profiles
        ],
    )
    _expect(arch, mutated, "E_REQUEST_PROFILE")


def test_verifier_rejects_a_phase_matrix_violation(arch, program):
    mutated = dataclasses.replace(
        program,
        agent_instance_profiles=[
            dataclasses.replace(record, primary_output_symbol_id=1)
            if record.phase == A.PHASE.PREFILL else record
            for record in program.agent_instance_profiles
        ],
    )
    _expect(arch, mutated, "E_BINDING_ROLE")


def test_verifier_rejects_a_decode_without_a_chunk(arch, program):
    mutated = dataclasses.replace(
        program,
        agent_instance_profiles=[
            dataclasses.replace(record, decode_chunk_tokens=0,
                                kv_write_bytes_per_member=0)
            if record.phase == A.PHASE.DECODE else record
            for record in program.agent_instance_profiles
        ],
    )
    _expect(arch, mutated, "E_REQUEST_PROFILE")


def test_verifier_rejects_a_kv_byte_mismatch(arch, program):
    mutated = dataclasses.replace(
        program,
        agent_instance_profiles=[
            dataclasses.replace(record, kv_write_bytes_per_member=1)
            if record.phase == A.PHASE.PREFILL else record
            for record in program.agent_instance_profiles
        ],
    )
    _expect(arch, mutated, "E_KV_TOKEN_MISMATCH")


def test_verifier_rejects_unsorted_requirements(arch, program):
    mutated = dataclasses.replace(
        program,
        agent_request_binding_requirements=list(reversed(
            program.agent_request_binding_requirements)),
    )
    _expect(arch, mutated, "E_ABI_ORDER")


def test_verifier_rejects_a_rank_beyond_the_source_map(arch, program):
    mutated = dataclasses.replace(
        program,
        agent_instance_member_bindings=[
            dataclasses.replace(record, expected_logical_source_rank=1)
            for record in program.agent_instance_member_bindings
        ],
    )
    _expect(arch, mutated, "E_REQUEST_PROFILE")


def test_verifier_rejects_publish_bindings_on_a_non_publish_profile(
        arch, program):
    mutated = dataclasses.replace(
        program,
        agent_publish_surrogate_bindings=[
            dataclasses.replace(record, instance_profile_id=11)
            for record in program.agent_publish_surrogate_bindings
        ],
    )
    _expect(arch, mutated, "E_BINDING_ROLE")


def test_verifier_rejects_a_missing_publish_binding(arch, program):
    mutated = dataclasses.replace(
        program, agent_publish_surrogate_bindings=[])
    _expect(arch, mutated, "E_BINDING_ROLE")


def test_verifier_rejects_an_unaligned_publish_chunk(arch, program):
    mutated = dataclasses.replace(
        program,
        agent_request_profiles=[
            dataclasses.replace(record, publish_chunk_bytes=7)
            for record in program.agent_request_profiles
        ],
    )
    _expect(arch, mutated, "E_OUTPUT_CHUNK_MISMATCH")


def test_verifier_rejects_a_double_classified_symbol(arch, program):
    symbols = {}
    for relocation in program.relocations:
        symbols[program.strings[relocation.symbol_sid - 1].value] = \
            relocation.symbol_sid
    mutated = dataclasses.replace(
        program,
        agent_instance_member_bindings=[
            dataclasses.replace(record, static_input_symbol_id=symbols["input"])
            if record.instance_profile_id == 11 else record
            for record in program.agent_instance_member_bindings
        ],
    )
    _expect(arch, mutated, "E_BINDING_ROLE")


from mesh_ir.serving_profiles import (
    SelectorIndex,
    _relocation,
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
                   if i.phase == A.PHASE.PREFILL)
    assert member_rank_vector(program, prefill) == ranks
    assert [member_source_core(program, request, prefill, ordinal)
            for ordinal in range(2)] == list(ranks)
    index = SelectorIndex(program)
    assert index.select(selector_tuple(program, prefill)) is prefill


def test_selector_closure_requires_every_path_profile(arch, program):
    request = program.agent_request_profiles[0]
    verify_path_closure(program, request)
    without_publish = dataclasses.replace(
        program,
        agent_instance_profiles=[
            i for i in program.agent_instance_profiles
            if i.phase != A.PHASE.PUBLISH],
    )
    with pytest.raises(MeshIrError) as err:
        verify_path_closure(without_publish, request)
    assert err.value.code == "E_REQUEST_PROFILE"


def test_selector_closure_requires_the_decode_tail(arch, program):
    request = program.agent_request_profiles[0]
    without_decode = dataclasses.replace(
        program,
        agent_instance_profiles=[
            i for i in program.agent_instance_profiles
            if i.phase != A.PHASE.DECODE],
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
        dma_descriptors=[
            dataclasses.replace(d, **changes) if d.descriptor_id ==
            descriptor_id else d
            for d in program.dma_descriptors
        ],
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
    overlapped = _with_descriptor(program, 7, useful_bytes=96)
    with pytest.raises(MeshIrError) as err:
        verify_instance_io(overlapped, request, _publish(overlapped))
    assert err.value.code == "E_HOST_IO_SIZE_MISMATCH"


def test_io_oracle_rejects_a_wrong_store_role(arch, program):
    request = program.agent_request_profiles[0]
    input_tensor = next(
        r.tensor_id for r in program.relocations
        if program.strings[r.symbol_sid - 1].value == "input")
    descriptor = program.dma_descriptors[6]
    wrong = dataclasses.replace(
        descriptor.dst, tensor_id=input_tensor)
    mutated = _with_descriptor(program, 7, dst=wrong)
    with pytest.raises(MeshIrError) as err:
        verify_instance_io(mutated, request, _publish(mutated))
    assert err.value.code == "E_HOST_IO_SIZE_MISMATCH"


def test_io_oracle_rejects_a_wrong_owner(arch, program):
    request = program.agent_request_profiles[0]
    mutated = _with_descriptor(program, 1, owner_core=1)
    with pytest.raises(MeshIrError) as err:
        verify_instance_io(mutated, request, _prefill(mutated))
    assert err.value.code == "E_BINDING_ROLE"


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
        agent_request_profiles=[
            dataclasses.replace(record, profile_id=0xFFFF)
            for record in program.agent_request_profiles],
    )
    decoded = decode_program(encode_program(wide))
    assert decoded.agent_request_profiles[0].profile_id == 0xFFFF
    too_wide = dataclasses.replace(
        program,
        agent_request_profiles=[
            dataclasses.replace(record, profile_id=0x10000)
            for record in program.agent_request_profiles],
    )
    with pytest.raises(MeshIrError) as err:
        verify_program(too_wide, arch)
    assert err.value.code == "E_REQUEST_PROFILE"


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
        program, agent_request_profiles=[request],
        agent_instance_profiles=[prefill, full, tail, publish],
        agent_instance_member_bindings=bindings)
    verify_path_closure(with_tail, request)
    without_tail = dataclasses.replace(
        with_tail,
        agent_instance_profiles=[prefill, full,
                                 dataclasses.replace(publish,
                                                     member_binding_first=2)])
    with pytest.raises(MeshIrError) as err:
        verify_path_closure(without_tail, request)
    assert err.value.code == "E_REQUEST_PROFILE"


def test_cross_member_symbol_reuse_is_rejected(arch):
    program = dual_member_ranks_program(arch, (0, 1))
    prefill = next(i for i in program.agent_instance_profiles
                   if i.phase == A.PHASE.PREFILL)
    first = program.agent_instance_member_bindings[0]
    reused = dataclasses.replace(program, agent_instance_member_bindings=[
        first,
        dataclasses.replace(program.agent_instance_member_bindings[1],
                            static_input_symbol_id=first.static_input_symbol_id,
                            static_kv_symbol_id=first.static_kv_symbol_id),
        *program.agent_instance_member_bindings[2:],
    ])
    with pytest.raises(MeshIrError) as err:
        verify_program(keyed(reused), arch)
    assert err.value.code == "E_BINDING_ROLE"


def test_cross_profile_symbol_role_change_is_rejected(arch, program):
    output_symbol = next(
        r.symbol_sid for r in program.relocations
        if program.strings[r.symbol_sid - 1].value == "out_slot_0")
    mutated = dataclasses.replace(
        program,
        agent_instance_member_bindings=[
            dataclasses.replace(record,
                                static_input_symbol_id=output_symbol)
            if record.static_input_symbol_id else record
            for record in program.agent_instance_member_bindings],
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
        agent_request_profiles=[
            dataclasses.replace(record, requested_profile_key=0)
            for record in keyed_program.agent_request_profiles])
    cases = [
        (zero_key, "REQUEST_PREFLIGHT", "E_REQUEST_PROFILE_KEY",
         ("REQUEST_RECOVERABLE", "PROFILE_ERROR", None)),
        (keyed(dataclasses.replace(
            program,
            agent_instance_profiles=[
                dataclasses.replace(record, primary_output_symbol_id=1)
                if record.phase == A.PHASE.PREFILL else record
                for record in program.agent_instance_profiles])),
         "REQUEST_PREFLIGHT", "E_BINDING_ROLE",
         ("REQUEST_RECOVERABLE", "PARAM_ERROR", None)),
        (keyed(dataclasses.replace(
            program,
            agent_instance_profiles=[
                dataclasses.replace(record, host_input_dma_bytes_per_member=1)
                if record.phase == A.PHASE.PREFILL else record
                for record in program.agent_instance_profiles])),
         "REQUEST_PREFLIGHT", "E_BINDING_ROLE",
         ("REQUEST_RECOVERABLE", "PARAM_ERROR", None)),
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
                                      required_features=0)
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
            agent_request_binding_requirements=[
                dataclasses.replace(record, binding_flags=flags)
                if record.binding_kind == kind else record
                for record in program.agent_request_binding_requirements],
        )
        with pytest.raises(MeshIrError) as err:
            verify_program(keyed(mutated), arch)
        assert err.value.code == "E_BINDING_ROLE", (kind, flags)


def test_member_slot_must_resolve_to_the_request_primary_tensor(arch, program):
    output = next(r for r in program.relocations
                  if program.strings[r.symbol_sid - 1].value == "output")
    mutated = dataclasses.replace(
        program,
        relocations=[
            dataclasses.replace(record, tensor_id=output.tensor_id,
                                offset_bytes=output.offset_bytes)
            if program.strings[record.symbol_sid - 1].value == "in_slot_0"
            else record
            for record in program.relocations],
    )
    with pytest.raises(MeshIrError) as err:
        verify_program(keyed(mutated), arch)
    assert err.value.code == "E_BINDING_ROLE"


def test_publish_must_not_read_kv(arch, program):
    kv_read = next(d for d in program.dma_descriptors
                   if d.kind == A.DMA_KIND.LOAD and d.src.tensor_id == 3)
    mutated = dataclasses.replace(
        program,
        expected_traffic=[
            dataclasses.replace(row, profile_id=3)
            if row.descriptor_id == kv_read.descriptor_id else row
            for row in program.expected_traffic],
        agent_instance_profiles=[
            dataclasses.replace(instance, kv_read_bytes_per_member=0)
            if instance.phase == A.PHASE.DECODE else instance
            for instance in program.agent_instance_profiles],
    )
    with pytest.raises(MeshIrError) as err:
        verify_program(keyed(mutated), arch)
    assert err.value.code == "E_BINDING_ROLE"


def test_host_input_rows_expand_by_stride(arch, program):
    read = next(d for d in program.dma_descriptors
                if d.kind == A.DMA_KIND.LOAD and d.src.tensor_id == 1)
    gapped = dataclasses.replace(read, rows=2, row_bytes=64,
                                 src_stride_bytes=96, dst_stride_bytes=64)
    plan = plan_descriptor(64, 2, read.src.offset_bytes, 96,
                           arch.axi_data_bytes, arch.axi_max_burst_beats)
    mutated = dataclasses.replace(
        program,
        dma_descriptors=[gapped if d == read else d
                         for d in program.dma_descriptors],
        expected_traffic=[
            dataclasses.replace(
                row, bursts=len(plan.bursts), ar_count=len(plan.bursts),
                segments=plan.segments, physical_beat_bytes=plan.beat_bytes,
                r_beats=plan.beat_bytes // arch.axi_data_bytes)
            if row.descriptor_id == read.descriptor_id else row
            for row in program.expected_traffic],
    )
    with pytest.raises(MeshIrError) as err:
        verify_program(keyed(mutated), arch)
    assert err.value.code == "E_BINDING_ROLE"


def test_kv_append_window_is_exact(arch, program):
    kv_store = next(d for d in program.dma_descriptors
                    if d.kind == A.DMA_KIND.STORE and d.row_bytes == 16)
    shifted = dataclasses.replace(
        kv_store, dst=dataclasses.replace(kv_store.dst, offset_bytes=KV_BASE))
    mutated = dataclasses.replace(
        program,
        dma_descriptors=[shifted if d == kv_store else d
                         for d in program.dma_descriptors])
    with pytest.raises(MeshIrError) as err:
        verify_program(keyed(mutated), arch)
    assert err.value.code == "E_BINDING_ROLE"


KV_BASE = 0x400000


def test_reprefill_closure_uses_full_tokens(arch, program):
    request = dataclasses.replace(program.agent_request_profiles[0],
                                  delta_input_tokens=4,
                                  expected_cached_tokens=4,
                                  delta_input_dma_bytes=64, path_mask=4)
    instances = [dataclasses.replace(i, path_kind=A.PATH_KIND.REPREFILL)
                 for i in program.agent_instance_profiles]
    reprefill = dataclasses.replace(program, agent_request_profiles=[request],
                                    agent_instance_profiles=instances)
    verify_program(keyed(reprefill), arch)
    wrong = dataclasses.replace(
        reprefill,
        agent_instance_profiles=[
            dataclasses.replace(i, valid_tokens_per_member=4,
                                kv_write_bytes_per_member=64)
            if i.phase == A.PHASE.PREFILL else i for i in instances])
    with pytest.raises(MeshIrError) as err:
        verify_program(keyed(wrong), arch)
    assert err.value.code == "E_REQUEST_PROFILE"


def test_reuse_closure_requires_its_own_prefill(arch, program):
    request = dataclasses.replace(program.agent_request_profiles[0],
                                  path_mask=2)
    instances = [dataclasses.replace(i, path_kind=A.PATH_KIND.KV_REUSE)
                 for i in program.agent_instance_profiles
                 if i.phase != A.PHASE.PREFILL]
    without = dataclasses.replace(program, agent_request_profiles=[request],
                                  agent_instance_profiles=instances)
    with pytest.raises(MeshIrError) as err:
        verify_path_closure(without, request)
    assert err.value.code == "E_REQUEST_PROFILE"


def test_publish_dag_requires_the_store_wait(arch, program):
    mutated = dataclasses.replace(
        program,
        commands=[dataclasses.replace(c, wait_count=0)
                  if c.command_id in (9, 10) else c
                  for c in program.commands])
    with pytest.raises(MeshIrError) as err:
        verify_program(keyed(mutated), arch)
    assert err.value.code == "E_BINDING_ROLE"


def test_publish_dag_requires_the_producer_allocation(arch, program):
    fill = next(d for d in program.dma_descriptors
                if d.kind == A.DMA_KIND.LOCAL_FILL)
    command = next(c for c in program.commands
                   if c.command_id == fill.command_id)
    kv_shard = next(s for s in program.shards
                    if s.tensor_id == 3)
    mutated = dataclasses.replace(
        program,
        dma_descriptors=[
            dataclasses.replace(
                d, src=dataclasses.replace(d.src, tensor_id=3,
                                           shard_id=kv_shard.shard_id,
                                           offset_bytes=0x3000),
                dst=dataclasses.replace(d.dst, tensor_id=3,
                                        shard_id=kv_shard.shard_id,
                                        offset_bytes=0x3000))
            if d == fill else d for d in program.dma_descriptors],
        command_operands=[
            dataclasses.replace(o, tensor_id=3, shard_id=kv_shard.shard_id,
                                allocation_id=kv_shard.allocation_id)
            if command.operand_begin <= index <
            command.operand_begin + command.operand_count else o
            for index, o in enumerate(program.command_operands)],
    )
    with pytest.raises(MeshIrError) as err:
        verify_program(keyed(mutated), arch)
    assert err.value.code == "E_BINDING_ROLE"


def test_publish_binding_ordinal_set_is_exact(arch, program):
    record = program.agent_publish_surrogate_bindings[0]
    mutated = dataclasses.replace(
        program,
        agent_publish_surrogate_bindings=[
            record, dataclasses.replace(record, member_ordinal=1)])
    with pytest.raises(MeshIrError) as err:
        verify_program(keyed(mutated), arch)
    assert err.value.code == "E_BINDING_ROLE"


def _merged_rank_program(arch, second_ranks):
    program = dual_member_ranks_program(arch, (0, 1))
    instances = list(program.agent_instance_profiles)
    bindings = list(program.agent_instance_member_bindings)
    for instance in program.agent_instance_profiles:
        new_id = instance.instance_profile_id + 100
        instances.append(dataclasses.replace(
            instance, instance_profile_id=new_id,
            member_binding_first=len(bindings)))
        for offset in range(instance.member_binding_count):
            source = program.agent_instance_member_bindings[
                instance.member_binding_first + offset]
            bindings.append(dataclasses.replace(
                source, instance_profile_id=new_id, member_ordinal=offset,
                expected_logical_source_rank=second_ranks[offset]))
    return dataclasses.replace(program, agent_instance_profiles=instances,
                               agent_instance_member_bindings=bindings)


def test_distinct_rank_vectors_coexist_and_are_selectable(arch):
    merged = _merged_rank_program(arch, (1, 0))
    index = SelectorIndex(merged)
    first = next(i for i in merged.agent_instance_profiles
                 if i.instance_profile_id == 11)
    second = next(i for i in merged.agent_instance_profiles
                  if i.instance_profile_id == 111)
    assert member_rank_vector(merged, first) == (0, 1)
    assert member_rank_vector(merged, second) == (1, 0)
    assert index.select(selector_tuple(merged, first)) is first
    assert index.select(selector_tuple(merged, second)) is second
    duplicate = dataclasses.replace(
        merged,
        agent_instance_profiles=merged.agent_instance_profiles + [
            dataclasses.replace(second, instance_profile_id=222)])
    with pytest.raises(MeshIrError) as err:
        SelectorIndex(duplicate)
    assert err.value.code == "E_ABI_DUPLICATE"


def test_multi_member_profiles_need_unique_ownership(arch):
    program = dual_member_ranks_program(arch, (0, 0))
    request = program.agent_request_profiles[0]
    prefill = next(i for i in program.agent_instance_profiles
                   if i.phase == A.PHASE.PREFILL)
    with pytest.raises(MeshIrError) as err:
        verify_instance_io(program, request, prefill)
    assert err.value.code == "E_BINDING_ROLE"
    distinct = dual_member_ranks_program(arch, (0, 1))
    distinct_request = distinct.agent_request_profiles[0]
    distinct_prefill = next(i for i in distinct.agent_instance_profiles
                            if i.phase == A.PHASE.PREFILL)
    verify_member_ownership(distinct, distinct_request, distinct_prefill)


def _symbol_id(program, name):
    return next(r.symbol_sid for r in program.relocations
                if program.strings[r.symbol_sid - 1].value == name)


def test_member_slot_base_must_match_the_primary_view(arch, program):
    slot = _symbol_id(program, "in_slot_0")
    for amount in (1, 64):
        mutated = dataclasses.replace(
            program,
            relocations=[
                dataclasses.replace(record,
                                    offset_bytes=record.offset_bytes + amount)
                if record.symbol_sid == slot else record
                for record in program.relocations],
        )
        with pytest.raises(MeshIrError) as err:
            verify_program(keyed(mutated), arch)
        assert err.value.code == "E_BINDING_ROLE", amount


def test_same_core_overlapping_slot_ranges_are_rejected(arch):
    dual = dual_member_ranks_program(arch, (0, 0))
    slots = {_symbol_id(dual, name)
             for name in ("in_slot_1", "kv_slot_1", "out_slot_1")}
    overlap = dataclasses.replace(
        dual,
        relocations=[
            dataclasses.replace(record,
                                offset_bytes=record.offset_bytes + 1)
            if record.symbol_sid in slots else record
            for record in dual.relocations],
    )
    prefill = next(i for i in overlap.agent_instance_profiles
                   if i.phase == A.PHASE.PREFILL)
    with pytest.raises(MeshIrError) as err:
        verify_member_ownership(overlap, overlap.agent_request_profiles[0],
                                prefill)
    assert err.value.code == "E_BINDING_ROLE"


def test_closure_requires_singleton_for_every_rank(arch, program):
    mutated = dataclasses.replace(
        program,
        agent_request_profiles=[
            dataclasses.replace(program.agent_request_profiles[0],
                                source_rank_count=2)],
        agent_source_core_map=[
            dataclasses.replace(program.agent_source_core_map[0]),
            dataclasses.replace(program.agent_source_core_map[0],
                                core_id=1)],
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
        program, agent_request_profiles=[request],
        agent_source_core_map=[
            dataclasses.replace(program.agent_source_core_map[0]),
            dataclasses.replace(program.agent_source_core_map[0],
                                core_id=0)],
        agent_instance_profiles=instances,
        agent_instance_member_bindings=bindings,
        agent_publish_surrogate_bindings=(
            list(program.agent_publish_surrogate_bindings) +
            [dataclasses.replace(
                program.agent_publish_surrogate_bindings[0],
                instance_profile_id=23)]),
    )
    with pytest.raises(MeshIrError) as err:
        verify_path_closure(mutated, request)
    assert err.value.code == "E_REQUEST_PROFILE"


def test_publish_producer_must_be_in_the_publish_closure(arch, program):
    fill = next(d for d in program.dma_descriptors
                if d.kind == A.DMA_KIND.LOCAL_FILL)
    mutated = dataclasses.replace(
        program,
        expected_traffic=[
            dataclasses.replace(row, profile_id=1)
            if row.descriptor_id == fill.descriptor_id else row
            for row in program.expected_traffic],
    )
    with pytest.raises(MeshIrError) as err:
        verify_program(keyed(mutated), arch)
    assert err.value.code == "E_BINDING_ROLE"


def test_publish_producer_must_fill_the_whole_allocation(arch, program):
    fill = next(d for d in program.dma_descriptors
                if d.kind == A.DMA_KIND.LOCAL_FILL)
    short = dataclasses.replace(fill, row_bytes=64, useful_bytes=64,
                                physical_storage_bytes=64,
                                src_stride_bytes=64, dst_stride_bytes=64)
    mutated = dataclasses.replace(
        program,
        dma_descriptors=[short if d == fill else d
                         for d in program.dma_descriptors],
        expected_traffic=[
            dataclasses.replace(row, useful_bytes=64)
            if row.descriptor_id == fill.descriptor_id else row
            for row in program.expected_traffic],
    )
    with pytest.raises(MeshIrError) as err:
        verify_program(keyed(mutated), arch)
    assert err.value.code == "E_BINDING_ROLE"


def test_publish_stores_must_read_their_own_slice(arch, program):
    output_tensor = _relocation(
        program, program.agent_request_profiles[0].
        primary_output_symbol_id).tensor_id
    stores = [d.descriptor_id for d in program.dma_descriptors
              if d.kind == A.DMA_KIND.STORE and
              d.dst.tensor_id == output_tensor]
    source = next(d.src.offset_bytes for d in program.dma_descriptors
                  if d.descriptor_id in stores)
    mutated = dataclasses.replace(
        program,
        dma_descriptors=[
            dataclasses.replace(
                d, src=dataclasses.replace(d.src, offset_bytes=source))
            if d.descriptor_id in stores else d
            for d in program.dma_descriptors],
    )
    with pytest.raises(MeshIrError) as err:
        verify_program(keyed(mutated), arch)
    assert err.value.code == "E_BINDING_ROLE"


def _traffic_for(arch, row, descriptor):
    read = descriptor.kind == A.DMA_KIND.LOAD
    endpoint = descriptor.src if read else descriptor.dst
    stride = descriptor.src_stride_bytes if read else descriptor.dst_stride_bytes
    plan = plan_descriptor(descriptor.row_bytes, descriptor.rows,
                           endpoint.offset_bytes, stride, arch.axi_data_bytes,
                           descriptor.max_burst_beats)
    beats = plan.beat_bytes // arch.axi_data_bytes
    return dataclasses.replace(
        row, useful_bytes=descriptor.useful_bytes,
        physical_beat_bytes=plan.beat_bytes, segments=plan.segments,
        bursts=len(plan.bursts), ar_count=len(plan.bursts) if read else 0,
        aw_count=0 if read else len(plan.bursts),
        b_count=0 if read else len(plan.bursts),
        r_beats=beats if read else 0, w_beats=0 if read else beats)


def _apply_descriptors(program, replacements):
    return dataclasses.replace(
        program,
        dma_descriptors=[replacements.get(d.descriptor_id, d)
                         for d in program.dma_descriptors],
        expected_traffic=[
            dataclasses.replace(row,
                                useful_bytes=replacements[
                                    row.descriptor_id].useful_bytes,
                                physical_beat_bytes=row.physical_beat_bytes,
                                segments=row.segments, bursts=row.bursts,
                                ar_count=row.ar_count, aw_count=row.aw_count,
                                b_count=row.b_count, r_beats=row.r_beats,
                                w_beats=row.w_beats)
            if row.descriptor_id in replacements else row
            for row in program.expected_traffic])


def test_legal_reuse_delta_suffix_is_accepted(arch, program):
    request = program.agent_request_profiles[0]
    input_base = _relocation(program, request.primary_input_symbol_id).offset_bytes
    kv_base = _relocation(program, request.primary_kv_symbol_id).offset_bytes
    input_load = next(d for d in program.dma_descriptors
                      if d.kind == A.DMA_KIND.LOAD and
                      d.src.tensor_id == _relocation(
                          program, request.primary_input_symbol_id).tensor_id)
    prefill = next(i for i in program.agent_instance_profiles
                   if i.phase == A.PHASE.PREFILL)
    prefill_ids = {r.descriptor_id for r in program.expected_traffic
                   if r.profile_id == prefill.mesh_profile_id}
    kv_store = next(d for d in program.dma_descriptors
                    if d.kind == A.DMA_KIND.STORE and
                    d.dst.tensor_id == _relocation(
                        program, request.primary_kv_symbol_id).tensor_id and
                    d.descriptor_id in prefill_ids)
    replacements = {
        input_load.descriptor_id: dataclasses.replace(
            input_load, src=dataclasses.replace(input_load.src,
                                                offset_bytes=input_base + 64),
            row_bytes=64, src_stride_bytes=64, dst_stride_bytes=64,
            useful_bytes=64, physical_storage_bytes=64),
        kv_store.descriptor_id: dataclasses.replace(
            kv_store, dst=dataclasses.replace(kv_store.dst,
                                              offset_bytes=kv_base + 64),
            row_bytes=64, src_stride_bytes=64, dst_stride_bytes=64,
            useful_bytes=64, physical_storage_bytes=64),
    }
    reuse = dataclasses.replace(
        program,
        agent_request_profiles=[dataclasses.replace(
            request, path_mask=2, delta_input_tokens=4,
            expected_cached_tokens=4, delta_input_dma_bytes=64)],
        agent_instance_profiles=[
            dataclasses.replace(
                instance, path_kind=A.PATH_KIND.KV_REUSE,
                **({"valid_tokens_per_member": 4, "kv_tokens_before": 4,
                    "host_input_dma_bytes_per_member": 64,
                    "kv_write_bytes_per_member": 64}
                   if instance.phase == A.PHASE.PREFILL else {}))
            for instance in program.agent_instance_profiles],
        dma_descriptors=[replacements.get(d.descriptor_id, d)
                         for d in program.dma_descriptors],
        expected_traffic=[
            _traffic_for(arch, row, replacements[row.descriptor_id])
            if row.descriptor_id in replacements else row
            for row in program.expected_traffic])
    verify_program(keyed(reuse), arch)


def test_independent_slot_and_descriptor_relocate_together(arch, program):
    request = program.agent_request_profiles[0]
    input_base = _relocation(program, request.primary_input_symbol_id).offset_bytes
    input_load = next(d for d in program.dma_descriptors
                      if d.kind == A.DMA_KIND.LOAD and
                      d.src.tensor_id == _relocation(
                          program, request.primary_input_symbol_id).tensor_id)
    slot = _symbol_id(program, "in_slot_0")
    moved_load = dataclasses.replace(
        input_load, src=dataclasses.replace(input_load.src,
                                            offset_bytes=input_base + 256))
    relocated = dataclasses.replace(
        program,
        relocations=[
            dataclasses.replace(record, offset_bytes=input_base + 256)
            if record.symbol_sid == slot else record
            for record in program.relocations],
        dma_descriptors=[moved_load if d == input_load else d
                         for d in program.dma_descriptors],
        expected_traffic=[
            _traffic_for(arch, row, moved_load)
            if row.descriptor_id == input_load.descriptor_id else row
            for row in program.expected_traffic])
    verify_program(keyed(relocated), arch)


def _duplicate_member_descriptors(program, shift=0, cores=(1, 1)):
    prefill = next(i for i in program.agent_instance_profiles
                   if i.phase == A.PHASE.PREFILL)
    prefill_ids = {r.descriptor_id for r in program.expected_traffic
                   if r.profile_id == prefill.mesh_profile_id}
    request = program.agent_request_profiles[0]
    input_tensor = _relocation(
        program, request.primary_input_symbol_id).tensor_id
    kv_tensor = _relocation(program, request.primary_kv_symbol_id).tensor_id
    wanted = [d for d in program.dma_descriptors
              if d.descriptor_id in prefill_ids and
              ((d.kind == A.DMA_KIND.LOAD and d.src.tensor_id == input_tensor)
               or (d.kind == A.DMA_KIND.STORE and
                   d.dst.tensor_id == kv_tensor))]
    descriptors = list(program.dma_descriptors)
    traffic = list(program.expected_traffic)
    next_id = max(d.descriptor_id for d in descriptors) + 1
    for index, descriptor in enumerate(wanted):
        core = cores[index % len(cores)]
        new = dataclasses.replace(
            descriptor, descriptor_id=next_id, owner_core=core,
            src=dataclasses.replace(descriptor.src, owner_core=core,
                                    offset_bytes=descriptor.src.offset_bytes +
                                    shift),
            dst=dataclasses.replace(descriptor.dst, owner_core=core,
                                    offset_bytes=descriptor.dst.offset_bytes +
                                    shift))
        descriptors.append(new)
        row = next(r for r in program.expected_traffic
                   if r.descriptor_id == descriptor.descriptor_id)
        traffic.append(dataclasses.replace(row, descriptor_id=next_id))
        next_id += 1
    return dataclasses.replace(program, dma_descriptors=descriptors,
                               expected_traffic=traffic)


def test_two_core_member_descriptors_attribute_uniquely(arch):
    program = dual_member_ranks_program(arch, (0, 1))
    program = _duplicate_member_descriptors(program, shift=0)
    prefill = next(i for i in program.agent_instance_profiles
                   if i.phase == A.PHASE.PREFILL)
    verify_descriptor_attribution(program, program.agent_request_profiles[0],
                                  prefill)


def test_same_core_independent_views_are_attributable(arch):
    program = dual_member_ranks_program(arch, (0, 0))
    request = program.agent_request_profiles[0]
    prefill = next(i for i in program.agent_instance_profiles
                   if i.phase == A.PHASE.PREFILL)
    for name, amount in (("in_slot_1", 256), ("kv_slot_1", 256)):
        slot = _symbol_id(program, name)
        program = dataclasses.replace(
            program,
            relocations=[
                dataclasses.replace(record,
                                    offset_bytes=record.offset_bytes + amount)
                if record.symbol_sid == slot else record
                for record in program.relocations])
    program = _duplicate_member_descriptors(program, shift=256, cores=(0,))
    verify_member_ownership(program, request, prefill)
    verify_descriptor_attribution(program, request, prefill)
    aliased = _duplicate_member_descriptors(
        dual_member_ranks_program(arch, (0, 0)), shift=0)
    with pytest.raises(MeshIrError) as err:
        verify_member_ownership(aliased, aliased.agent_request_profiles[0],
                                next(i for i in aliased.agent_instance_profiles
                                     if i.phase == A.PHASE.PREFILL))
    assert err.value.code == "E_BINDING_ROLE"


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
