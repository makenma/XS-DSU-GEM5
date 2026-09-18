import dataclasses
import hashlib
from pathlib import Path

import pytest

from mesh_ir.agent_planning import ArenaRegion
from mesh_ir.agent_plan_image import MAGIC, build_agent_plan_image
from mesh_ir.agent_surrogate import load_surrogate_profiles
from mesh_ir.agent_workload import (
    load_control_plan,
    load_workload_plan,
    null_control_plan_digest,
)

FIXTURES = Path(__file__).resolve().parents[4] / "tests/gem5/ai_mesh/fixtures/gate4"
IMAGE_DIGEST = "e917bd290cf4de0a0ead22424267c2d50e644dae95f64084fc76210a4580593d"
WORKLOAD_DIGEST = "afc9cc56e7bed3228009e27e99b42cff882cdf580430f47a4016c786c6113e5e"
CONTROL_DIGEST = "49fcb22b9f99dbce8e30a87871b3d738b99e49e4dde346a381004c218ad1676b"
SURROGATE_FIXTURE_DIGEST = (
    "d6aa0634939a4d745b25a1ce36b16c94685a7a8d9d58c1d608ed2d20f039d897"
)
CTRL_CANCEL_LIVE_DIGEST = (
    "ec0f8d1fc3dfb47f2a0817fab540f28e48046d8bf365eebb3329a32fdc048a09"
)
CTRL_CANCEL_LIVE_LENGTH = 3493


def regions():
    return [
        ArenaRegion("INPUT", 0x0000000101000000, 67108864, 32),
        ArenaRegion("PARAMETER", 0x0000000100100000, 8388608, 8),
        ArenaRegion("OUTPUT", 0x0000000105000000, 67108864, 32),
        ArenaRegion("METADATA", 0x0000000109000000, 8388608, 8),
    ]


def build():
    workload = load_workload_plan(FIXTURES / "agent_workload_plan_two_user.json")
    registry = load_surrogate_profiles(FIXTURES / "agent_surrogate_profiles_two_user.json")
    return build_agent_plan_image(
        workload, None, "EXPLICIT_ONLY", regions(), registry
    )


def build_control(name):
    workload = load_workload_plan(FIXTURES / "agent_workload_plan_three_user.json")
    control = load_control_plan(FIXTURES / f"agent_control_plan_{name}.json", workload)
    registry = load_surrogate_profiles(
        FIXTURES / "agent_surrogate_profiles_three_user.json"
    )
    return build_agent_plan_image(
        workload, control, "EXPLICIT_ONLY", regions(), registry
    )


def test_image_digest_is_frozen_and_self_consistent():
    image, digest = build()
    assert digest == IMAGE_DIGEST
    assert len(image) == 3306
    assert image[:4] == MAGIC
    assert image[4:8] == (1).to_bytes(4, "little")
    assert image[8:12] == (5).to_bytes(4, "little")
    assert image[12:44] == bytes.fromhex(WORKLOAD_DIGEST)
    assert image[44:76] == bytes.fromhex(CONTROL_DIGEST)
    assert image[-32:] == hashlib.sha256(image[:-32]).digest()


def test_surrogate_section_layout():
    image, _ = build()
    header_start = len(image) - 32 - 276 - 10
    assert image[header_start:header_start + 2] == (5).to_bytes(2, "little")
    assert image[header_start + 2:header_start + 10] == (276).to_bytes(8, "little")
    payload = image[header_start + 10:len(image) - 32]
    assert payload[:32] == bytes.fromhex(SURROGATE_FIXTURE_DIGEST)
    assert payload[32:36] == (5).to_bytes(4, "little")
    assert payload[36:40] == (1).to_bytes(2, "little") + (1).to_bytes(2, "little")
    assert payload[40:48] == (4097).to_bytes(8, "little")
    assert payload[48:52] == (1024).to_bytes(4, "little")
    assert payload[52:60] == (16384).to_bytes(8, "little")
    assert payload[60:64] == (128).to_bytes(4, "little")
    assert payload[64:72] == (4096).to_bytes(8, "little")
    assert payload[72:80] == (4000000).to_bytes(8, "little")
    assert payload[80:84] == (4096).to_bytes(4, "little")


def test_control_section_layout():
    image, digest = build_control("cancel_live")
    assert digest == CTRL_CANCEL_LIVE_DIGEST
    assert len(image) == CTRL_CANCEL_LIVE_LENGTH
    assert image[8:12] == (6).to_bytes(4, "little")
    section_start = len(image) - 32 - 62 - 10
    assert image[section_start:section_start + 2] == (6).to_bytes(2, "little")
    assert image[section_start + 2:section_start + 10] == (62).to_bytes(8, "little")
    payload = image[section_start + 10:len(image) - 32]
    assert payload[0:4] == (1).to_bytes(4, "little")
    assert payload[4:8] == (1).to_bytes(4, "little")
    assert payload[8] == 2
    assert payload[9] == 1
    assert payload[10] == 1
    assert payload[11:15] == (1).to_bytes(4, "little")
    assert payload[15:19] == (0).to_bytes(4, "little")
    assert payload[19:21] == (0).to_bytes(2, "little")
    assert payload[21] == 0
    assert payload[22:26] == (0).to_bytes(4, "little")
    assert payload[26:30] == (1).to_bytes(4, "little")
    assert payload[30:34] == (0).to_bytes(4, "little")
    assert payload[34:36] == (1).to_bytes(2, "little")
    assert payload[36:40] == (0).to_bytes(4, "little")
    assert payload[40:42] == (0).to_bytes(2, "little")
    assert payload[42:50] == (0).to_bytes(8, "little")
    assert payload[50:58] == (0).to_bytes(8, "little")
    assert payload[58:62] == (0).to_bytes(4, "little")


def test_checked_in_fixture_matches_rebuild():
    image, _ = build()
    stored = (FIXTURES / "agent_plan_image_two_user.bin").read_bytes()
    assert stored == image


def test_null_control_plan_digest_matches_image_header():
    workload = load_workload_plan(FIXTURES / "agent_workload_plan_two_user.json")
    assert null_control_plan_digest(workload.digest) == CONTROL_DIGEST


SERVING_FIXTURES = (
    Path(__file__).resolve().parents[4] / "tests/gem5/ai_mesh/fixtures/gate6")


def serving_binding_plans():
    from mesh_ir.abi.decoder import decode_program
    from mesh_ir.builder import load_arch
    from mesh_ir.host_bindings import build_host_binding_plans

    program = decode_program(
        (SERVING_FIXTURES / "serving_two_tokens.mshb").read_bytes())
    manifest = load_arch(
        Path(__file__).resolve().parents[4] / "configs/example/ai_mesh"
        / "arch/mesh_1x2.yaml")
    return program, manifest, build_host_binding_plans(
        program, [region.base for region in manifest.regions])


def serving_image():
    from mesh_ir.agent_config import (load_agent_runtime_config,
                                      release_policy_of)
    from mesh_ir.agent_surrogate import load_surrogate_profiles
    from mesh_ir.agent_workload import load_workload_plan
    from mesh_ir.agent_plan_image import _SECTION_REQUEST_BINDINGS
    from mesh_ir.gate4_oracle import arena_regions

    _program, manifest, plans = serving_binding_plans()
    workload = load_workload_plan(
        SERVING_FIXTURES / "serving_workload_plan.json")
    registry = load_surrogate_profiles(
        SERVING_FIXTURES / "serving_surrogate_profiles.json")
    config = load_agent_runtime_config(
        SERVING_FIXTURES / "serving_runtime_config.yaml")
    regions = arena_regions(config.document["serving"]["address_map"])
    image, _ = build_agent_plan_image(
        workload, None, release_policy_of(config), regions, registry, plans,
        4096)
    return _SECTION_REQUEST_BINDINGS, manifest, plans, image


def _binding_payload(image, section_type):
    offset = 12 + 32 + 32
    section_count = int.from_bytes(image[8:12], "little")
    payload = None
    for _ in range(section_count):
        kind = int.from_bytes(image[offset:offset + 2], "little")
        length = int.from_bytes(image[offset + 2:offset + 10], "little")
        if kind == section_type:
            payload = image[offset + 10:offset + 10 + length]
        offset += 10 + length
    return payload


def test_request_binding_section_round_trips_the_frozen_plans():
    from mesh_ir.agent_plan_image import decode_request_binding_section

    section_type, _manifest, plans, image = serving_image()
    decoded = decode_request_binding_section(
        _binding_payload(image, section_type))
    assert decoded["kv_session_slot_bytes"] == 4096
    assert len(decoded["plans"]) == len(plans)
    for record, plan in zip(decoded["plans"], plans):
        assert record["program_id"] == plan.program_id
        assert record["profile_id"] == plan.profile_id
        assert record["instance_count"] == plan.instance_count == 4
        assert record["primary_input_symbol_id"] == plan.primary_input_symbol_id
        assert record["primary_output_symbol_id"] == plan.primary_output_symbol_id
        assert record["primary_kv_symbol_id"] == plan.primary_kv_symbol_id
        assert [(row["symbol_id"], row["kind"], row["flags"])
                for row in record["requirements"]] == [
            (row.symbol_id, row.kind, row.flags)
            for row in plan.requirements]


def test_request_binding_section_record_is_twenty_four_bytes():
    from mesh_ir.agent_plan_image import decode_request_binding_section

    section_type, _manifest, plans, image = serving_image()
    payload = _binding_payload(image, section_type)
    assert payload[:12] == (4096).to_bytes(8, "little") + \
        (len(plans)).to_bytes(4, "little")
    requirement_total = sum(len(plan.requirements) for plan in plans)
    assert len(payload) == 12 + 24 * len(plans) + 24 * requirement_total
    decoded = decode_request_binding_section(payload)
    assert decoded["plans"][0]["requirements"][0]["kind"] == 1


def test_checked_in_serving_image_matches_the_rebuild():
    _type, _manifest, _plans, image = serving_image()
    stored = (SERVING_FIXTURES
              / "agent_plan_image_serving_bindings.bin").read_bytes()
    assert stored == image


def test_missing_profile_is_not_silently_zero():
    from mesh_ir.host_bindings import build_host_binding_plans
    from mesh_ir.model import MeshIrError

    import dataclasses

    program, _manifest, plans = serving_binding_plans()
    stripped = dataclasses.replace(
        program, agent_request_binding_requirements=[
            requirement
            for requirement in program.agent_request_binding_requirements
            if requirement.request_profile_id != plans[0].profile_id])
    try:
        build_host_binding_plans(stripped, [0] * 8)
    except MeshIrError as error:
        assert error.code == "E_BINDING_ROLE"
    else:
        raise AssertionError("dropped profile produced a plan")


def test_plan_projection_must_match_the_frozen_workload(monkeypatch):
    import struct

    import pytest

    import mesh_ir.agent_plan_image as plan_image
    from mesh_ir.agent_workload import PlanError

    original = plan_image._round

    def tampered_item(round_):
        data = bytearray(original(round_))
        struct.pack_into("<I", data, 0, round_.workload_plan_item_id + 998)
        return bytes(data)

    monkeypatch.setattr(plan_image, "_round", tampered_item)
    with pytest.raises(PlanError):
        build()


def test_plan_projection_rejects_a_tampered_round_cached_tokens(monkeypatch):
    import struct

    import pytest

    import mesh_ir.agent_plan_image as plan_image
    from mesh_ir.agent_workload import PlanError

    original = plan_image._round

    def tampered_cached(round_):
        data = bytearray(original(round_))
        struct.pack_into("<I", data, 28, round_.expected_cached_tokens + 1)
        return bytes(data)

    monkeypatch.setattr(plan_image, "_round", tampered_cached)
    with pytest.raises(PlanError):
        build()


def _assert_projection_rejects(monkeypatch, mutate):
    import mesh_ir.agent_plan_image as plan_image
    from mesh_ir.agent_workload import PlanError

    original = plan_image._round

    def encode(round_):
        changed = mutate(round_)
        return original(changed) if changed is not None else original(round_)

    monkeypatch.setattr(plan_image, "_round", encode)
    with pytest.raises(PlanError):
        build()


@pytest.mark.parametrize("field", ["prompt_bytes", "prompt_tokens",
                                   "delta_prompt_bytes", "delta_prompt_tokens"])
def test_plan_projection_checks_prompt_fields(monkeypatch, field):
    def mutate(round_):
        value = getattr(round_, field)
        if value is None:
            return None
        return dataclasses.replace(round_, **{field: value + 1})

    _assert_projection_rejects(monkeypatch, mutate)


@pytest.mark.parametrize("field", ["nominal_ns", "host_tokens_required",
                                   "raw_log_bytes", "excerpt_bytes",
                                   "excerpt_tokens"])
def test_plan_projection_checks_compile_stage_fields(monkeypatch, field):
    def mutate(round_):
        stage = round_.compile
        return dataclasses.replace(
            round_, compile=dataclasses.replace(
                stage, **{field: (getattr(stage, field) or 0) + 1}))

    _assert_projection_rejects(monkeypatch, mutate)


@pytest.mark.parametrize("field", ["read_bytes", "write_bytes"])
def test_plan_projection_checks_compile_local_io(monkeypatch, field):
    def mutate(round_):
        stage = round_.compile
        return dataclasses.replace(
            round_, compile=dataclasses.replace(
                stage, local_io=dataclasses.replace(
                    stage.local_io,
                    **{field: getattr(stage.local_io, field) + 1})))

    _assert_projection_rejects(monkeypatch, mutate)


def test_plan_projection_checks_compile_outcome(monkeypatch):
    def mutate(round_):
        stage = round_.compile
        outcome = "FAIL" if stage.outcome == "SUCCESS" else "SUCCESS"
        return dataclasses.replace(
            round_, compile=dataclasses.replace(stage, outcome=outcome))

    _assert_projection_rejects(monkeypatch, mutate)


def test_plan_projection_checks_optional_stage_presence(monkeypatch):
    def mutate(round_):
        if round_.test is not None:
            return dataclasses.replace(round_, test=None)
        if round_.log_parse is not None:
            return dataclasses.replace(round_, log_parse=None)
        return None

    _assert_projection_rejects(monkeypatch, mutate)
