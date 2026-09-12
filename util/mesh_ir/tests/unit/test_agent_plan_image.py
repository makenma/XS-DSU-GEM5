import hashlib
from pathlib import Path

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
