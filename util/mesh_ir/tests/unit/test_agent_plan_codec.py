import hashlib
from pathlib import Path

from mesh_ir import agent_protocol as P
from mesh_ir.agent_workload import load_workload_plan
from mesh_ir.generated import agent_abi as A

FIXTURES = Path(__file__).resolve().parents[4] / "tests/gem5/ai_mesh/fixtures/gate4"
WORKLOAD_DIGEST = "afc9cc56e7bed3228009e27e99b42cff882cdf580430f47a4016c786c6113e5e"
ROUND0_HEX = (
    "41474e5001000000a00000000001000000000000000000000000000101000000"
    "0040000000000000000400000000000000000005010000000020000000000000"
    "0000000901000000000200008000000001000000000000000100000000000000"
    "000000000000000000000401010000000000000000000000a000000000001800"
    "a000000060000000011000000000000000000000000000002c917c7a00000000"
    "0100010020000000aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    "aaaaaaaaaaaaaaaa030001000800000000100000000000000400010020000000"
    "afc9cc56e7bed3228009e27e99b42cff882cdf580430f47a4016c786c6113e5e"
)
ROUND1_SHA256 = "2854ec712c5739031bf5256627f1d26d18c567ee5417fe095e0891b7343137c7"


def build_parameter(round_, task, user_id, addresses) -> bytes:
    def tlv(tlv_type, payload):
        return P.encode_tlv(tlv_type, payload, A.TLV_FLAGS.REQUIRED)

    tail = tlv(A.TLV_TYPE.INPUT_DIGEST, bytes.fromhex(round_.input_content_digest))
    if round_.deadline_tick is not None:
        tail += tlv(A.TLV_TYPE.DEADLINE, round_.deadline_tick.to_bytes(8, "little"))
    tail += tlv(A.TLV_TYPE.OUTPUT_CHUNK_BYTES, (4096).to_bytes(8, "little"))
    tail += tlv(A.TLV_TYPE.WORKLOAD_ID_DIGEST, bytes.fromhex(WORKLOAD_DIGEST))
    values = {
        "magic": 0x504e4741,
        "abi_major": A.ABI_MAJOR,
        "abi_minor": A.ABI_MINOR,
        "header_bytes": A.PARAMETER_HEADER_BYTES,
        "flags": 0,
        "reserved": 0,
        "input_addr": addresses[0],
        "input_bytes": round_.full_context_bytes,
        "input_tokens": round_.full_context_tokens,
        "cached_tokens": round_.expected_cached_tokens,
        "output_addr": addresses[1],
        "output_capacity_bytes": round_.output_capacity_bytes,
        "output_metadata_addr": addresses[2],
        "output_metadata_capacity_bytes": round_.output_metadata_capacity_bytes,
        "max_output_tokens": round_.output_tokens,
        "kv_handle": task.kv_handle,
        "kv_generation": task.initial_kv_generation,
        "moe_route_profile_id": 0,
        "user_id": user_id,
        "task_seq": task.task_seq,
        "repair_round": round_.repair_round,
        "qos": round_.qos,
        "request_kind": A.SQ_OPCODE.GENERATE,
        "workload_plan_item_id": round_.workload_plan_item_id,
        "target_request_id": 0,
        "binding_table_offset": A.PARAMETER_HEADER_BYTES,
        "binding_count": 0,
        "binding_record_bytes": A.BINDING_RECORD_BYTES,
        "extension_offset": A.PARAMETER_HEADER_BYTES,
        "extension_bytes": len(tail),
        "requested_profile_key": round_.requested_profile_key,
        "reserved2": 0,
        "reserved3": 0,
    }
    return P.encode_parameter(values, tail)


def task0():
    workload = load_workload_plan(FIXTURES / "agent_workload_plan_two_user.json")
    return workload.users[0].tasks[0]


def test_round0_parameter_block_matches_frozen_hex():
    task = task0()
    parameter = build_parameter(
        task.rounds[0],
        task,
        0,
        (0x0000000101000000, 0x0000000105000000, 0x0000000109000000),
    )
    assert parameter.hex() == ROUND0_HEX


def test_round1_parameter_block_matches_frozen_sha256():
    task = task0()
    parameter = build_parameter(
        task.rounds[1],
        task,
        0,
        (
            0x0000000101000000 + 16384,
            0x0000000105000000 + 8192,
            0x0000000109000000 + 512,
        ),
    )
    assert hashlib.sha256(parameter).hexdigest() == ROUND1_SHA256


def test_frozen_round0_hex_decodes_with_required_tlv_flags():
    decoded = P.decode_parameter(bytes.fromhex(ROUND0_HEX))
    assert decoded["total_bytes"] == len(bytes.fromhex(ROUND0_HEX))
    assert [tlv["type"] for tlv in decoded["tlvs"]] == [
        A.TLV_TYPE.INPUT_DIGEST,
        A.TLV_TYPE.OUTPUT_CHUNK_BYTES,
        A.TLV_TYPE.WORKLOAD_ID_DIGEST,
    ]
    assert all(
        tlv["flags"] == A.TLV_FLAGS.REQUIRED for tlv in decoded["tlvs"]
    )


CONTROL_CANCEL_HEX = (
    "41474e5001000000a0000000a0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000300000000010000000100000000000000000018000000000000000000000000000000000000000000000000007ffdc3cb00000000"
)
CONTROL_RELEASE_HEX = (
    "41474e5001000000a0000000a0000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000b0000000000000001000000000000000000000000000000000000020000000000000000000000000000000000001800000000000000000000000000000000000000000000000000ec41f81e00000000"
)


def build_control_parameter(request_kind, target_request_id, kv_handle, generation):
    values = {
        "magic": 0x504e4741,
        "abi_major": A.ABI_MAJOR,
        "abi_minor": A.ABI_MINOR,
        "header_bytes": A.PARAMETER_HEADER_BYTES,
        "flags": 0,
        "reserved": 0,
        "input_addr": 0,
        "input_bytes": 0,
        "input_tokens": 0,
        "cached_tokens": 0,
        "output_addr": 0,
        "output_capacity_bytes": 0,
        "output_metadata_addr": 0,
        "output_metadata_capacity_bytes": 0,
        "max_output_tokens": 0,
        "kv_handle": kv_handle,
        "kv_generation": generation,
        "moe_route_profile_id": 0,
        "user_id": 0,
        "task_seq": 0,
        "repair_round": 0,
        "qos": 0,
        "request_kind": request_kind,
        "workload_plan_item_id": 0,
        "target_request_id": target_request_id,
        "binding_table_offset": 0,
        "binding_count": 0,
        "binding_record_bytes": A.BINDING_RECORD_BYTES,
        "extension_offset": 0,
        "extension_bytes": 0,
        "requested_profile_key": 0,
        "reserved2": 0,
        "reserved3": 0,
    }
    return P.encode_parameter(values)


def test_cancel_control_parameter_is_exactly_160_bytes():
    parameter = build_control_parameter(
        A.SQ_OPCODE.CANCEL, (1 << 32) | 1, 0, 0
    )
    assert len(parameter) == 160
    assert parameter.hex() == CONTROL_CANCEL_HEX
    decoded = P.decode_parameter(parameter)
    assert decoded["request_kind"] == A.SQ_OPCODE.CANCEL
    assert decoded["target_request_id"] == (1 << 32) | 1
    assert decoded["kv_handle"] == 0
    assert decoded["binding_count"] == 0
    assert decoded["binding_record_bytes"] == A.BINDING_RECORD_BYTES
    assert decoded["binding_table_offset"] == 0
    assert decoded["extension_offset"] == 0
    assert decoded["extension_bytes"] == 0


def test_release_control_parameter_is_exactly_160_bytes():
    parameter = build_control_parameter(
        A.SQ_OPCODE.RELEASE_SESSION, 0, 11, 1
    )
    assert len(parameter) == 160
    assert parameter.hex() == CONTROL_RELEASE_HEX
    decoded = P.decode_parameter(parameter)
    assert decoded["request_kind"] == A.SQ_OPCODE.RELEASE_SESSION
    assert decoded["target_request_id"] == 0
    assert decoded["kv_handle"] == 11
    assert decoded["kv_generation"] == 1
