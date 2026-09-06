import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mesh_ir import agent_protocol as P
from mesh_ir.generated import agent_abi as A


def test_crc32c_reference_vector():
    assert P.crc32c(b"123456789") == 0xE3069283
    assert P.crc32c(b"") == 0


def test_sq_round_trip_with_crc():
    values = {
        "abi_major": 1, "abi_minor": 0, "opcode": A.SQ_OPCODE.GENERATE,
        "flags": 0, "sq_seq": 7, "request_id": 0x1234, "session_id": 5,
        "parameter_block_addr": 0x800000000, "parameter_block_bytes": 160,
        "program_id": 1, "profile_id": 1, "completion_cookie": 0x99,
        "qos": 4, "flags2": 0, "reserved": 0,
    }
    blob = P.encode_sq(values)
    assert len(blob) == 64
    decoded = P.decode_sq(blob)
    assert decoded["sq_seq"] == 7
    assert decoded["opcode"] == A.SQ_OPCODE.GENERATE
    assert decoded == values


def test_sq_crc_corruption_rejected():
    values = {
        "abi_major": 1, "abi_minor": 0, "opcode": A.SQ_OPCODE.GENERATE,
        "flags": 0, "sq_seq": 1, "request_id": 1, "session_id": 0,
        "parameter_block_addr": 0x1000, "parameter_block_bytes": 160,
        "program_id": 1, "profile_id": 1, "completion_cookie": 1,
        "qos": 0, "flags2": 0, "reserved": 0,
    }
    blob = bytearray(P.encode_sq(values))
    blob[20] ^= 0x01
    with pytest.raises(P.ProtocolError) as err:
        P.decode_sq(bytes(blob))
    assert err.value.code == "E_SQ_CRC"


def test_cq_round_trip():
    values = {
        "cq_seq": 3, "request_id": 0x42, "completion_cookie": 0x77,
        "status": A.CQ_STATUS.SUCCESS,
        "flags": A.CQ_FLAGS.METADATA_VALID,
        "output_bytes_or_detail_code": 8192,
    }
    blob = P.encode_cq(values)
    assert len(blob) == 32
    assert P.decode_cq(blob) == values


def test_parameter_round_trip():
    values = {
        "magic": 0x504e4741, "abi_major": 1, "abi_minor": 0,
        "header_bytes": 160, "total_bytes": 160, "flags": 0, "reserved": 0,
        "input_addr": 0x2000, "input_bytes": 128, "input_tokens": 4,
        "cached_tokens": 0, "output_addr": 0x3000,
        "output_capacity_bytes": 4096, "output_metadata_addr": 0x4000,
        "output_metadata_capacity_bytes": 512, "max_output_tokens": 8,
        "kv_handle": 0, "kv_generation": 0, "moe_route_profile_id": 0,
        "user_id": 1, "task_seq": 1, "repair_round": 0, "qos": 2,
        "request_kind": A.SQ_OPCODE.GENERATE, "workload_plan_item_id": 3,
        "target_request_id": 0, "binding_table_offset": 160,
        "binding_count": 0, "binding_record_bytes": 24,
        "extension_offset": 0, "extension_bytes": 0,
        "requested_profile_key": 0, "reserved2": 0, "reserved3": 0,
    }
    blob = P.encode_parameter(values)
    assert len(blob) == 160
    decoded = P.decode_parameter(blob)
    decoded.pop("crc32", None)
    decoded.pop("binding_records")
    decoded.pop("tlvs")
    assert decoded == values


def test_parameter_full_block_crc_covers_tail():
    """A semantic parameter block (header + mandatory GENERATE TLVs) must
    have its CRC cover every byte of total_bytes with the CRC field zeroed:
    flipping a tail byte must be rejected (spec 8.3)."""
    from mesh_ir import agent_protocol as P2
    values = {
        "magic": 0x504e4741, "abi_major": 1, "abi_minor": 0,
        "header_bytes": 160, "flags": 0, "reserved": 0,
        "input_addr": 0x20000, "input_bytes": 32, "input_tokens": 4,
        "cached_tokens": 0, "output_addr": 0x30000,
        "output_capacity_bytes": 32, "output_metadata_addr": 0x40000,
        "output_metadata_capacity_bytes": 128, "max_output_tokens": 8,
        "kv_handle": 0, "kv_generation": 0, "moe_route_profile_id": 0,
        "user_id": 1, "task_seq": 1, "repair_round": 0, "qos": 2,
        "request_kind": A.SQ_OPCODE.GENERATE, "workload_plan_item_id": 3,
        "target_request_id": 0, "binding_table_offset": 160,
        "binding_count": 0, "binding_record_bytes": 24,
        "extension_offset": 160, "extension_bytes": 96,
        "requested_profile_key": 1, "reserved2": 0, "reserved3": 0,
    }
    tlvs = (
        P.encode_tlv(A.TLV_TYPE.INPUT_DIGEST, bytes(32), A.TLV_FLAGS.REQUIRED)
        + P.encode_tlv(A.TLV_TYPE.OUTPUT_CHUNK_BYTES, bytes(8),
                       A.TLV_FLAGS.REQUIRED)
        + P.encode_tlv(A.TLV_TYPE.WORKLOAD_ID_DIGEST, bytes(32),
                       A.TLV_FLAGS.REQUIRED)
    )
    blob = P.encode_parameter(values, tail=tlvs)
    assert len(blob) == 160 + len(tlvs)
    decoded = P.decode_parameter(blob)
    assert len(decoded["tlvs"]) == 3
    corrupt = bytearray(blob)
    corrupt[-1] ^= 0x01
    with pytest.raises(P.ProtocolError) as err:
        P.decode_parameter(bytes(corrupt))
    assert err.value.code == "E_SQ_CRC"


@pytest.mark.parametrize(
    ("binding_offset", "binding_count", "record_bytes", "extension_offset",
     "extension_bytes"),
    (
        (4096, 1, 24, 160, 96),
        (152, 1, 24, 160, 96),
        (160, 1, 24, 160, 96),
        (256, 1, 16, 160, 96),
    ),
)
def test_parameter_rejects_invalid_binding_layout(
    binding_offset, binding_count, record_bytes, extension_offset,
    extension_bytes
):
    values = {
        "magic": 0x504e4741, "abi_major": 1, "abi_minor": 0,
        "header_bytes": 160, "flags": 0, "reserved": 0,
        "input_addr": 0x2000, "input_bytes": 64, "input_tokens": 4,
        "cached_tokens": 0, "output_addr": 0x3000,
        "output_capacity_bytes": 64, "output_metadata_addr": 0x4000,
        "output_metadata_capacity_bytes": 128, "max_output_tokens": 8,
        "kv_handle": 0, "kv_generation": 0, "moe_route_profile_id": 0,
        "user_id": 1, "task_seq": 1, "repair_round": 0, "qos": 2,
        "request_kind": A.SQ_OPCODE.GENERATE, "workload_plan_item_id": 3,
        "target_request_id": 0, "binding_table_offset": binding_offset,
        "binding_count": binding_count, "binding_record_bytes": record_bytes,
        "extension_offset": extension_offset,
        "extension_bytes": extension_bytes,
        "requested_profile_key": 0, "reserved2": 0, "reserved3": 0,
    }
    tlvs = (
        P.encode_tlv(A.TLV_TYPE.INPUT_DIGEST, bytes(32), A.TLV_FLAGS.REQUIRED)
        + P.encode_tlv(A.TLV_TYPE.OUTPUT_CHUNK_BYTES, bytes(8),
                       A.TLV_FLAGS.REQUIRED)
        + P.encode_tlv(A.TLV_TYPE.WORKLOAD_ID_DIGEST, bytes(32),
                       A.TLV_FLAGS.REQUIRED)
    )
    binding = P.encode_binding({
        "symbol_id": 1,
        "kind": A.BINDING_KIND.HOST_INPUT,
        "flags": A.BINDING_FLAGS.READ,
        "address": 0x2000,
        "bytes": 64,
    })
    blob = P.encode_parameter(values, tail=tlvs + binding)
    with pytest.raises(P.ProtocolError) as error:
        P.decode_parameter(blob)
    assert error.value.code == "E_REQUEST_BINDING"


def test_binding_round_trip():
    values = {
        "symbol_id": 1, "kind": A.BINDING_KIND.HOST_INPUT,
        "flags": A.BINDING_FLAGS.READ, "address": 0x2000, "bytes": 128,
    }
    blob = P.encode_binding(values)
    assert len(blob) == 24
    assert P.decode_binding(blob) == values


def test_tlv_encode_decode_and_ordering():
    a = P.encode_tlv(A.TLV_TYPE.INPUT_DIGEST, bytes(32), A.TLV_FLAGS.REQUIRED)
    b = P.encode_tlv(A.TLV_TYPE.OUTPUT_CHUNK_BYTES, bytes(8), A.TLV_FLAGS.REQUIRED)
    tlvs = P.decode_tlvs(a + b)
    assert [t["type"] for t in tlvs] == [
        A.TLV_TYPE.INPUT_DIGEST, A.TLV_TYPE.OUTPUT_CHUNK_BYTES]
    with pytest.raises(P.ProtocolError):
        P.decode_tlvs(b + a)  # descending order rejected
    with pytest.raises(P.ProtocolError):
        P.decode_tlvs(a[:-1])  # truncated payload


def test_metadata_round_trip_with_tlv_tail():
    values = {
        "magic": 0x4f4e4741, "abi_major": 1, "abi_minor": 0,
        "header_bytes": 128, "flags": 0,
        "terminal_status": A.CQ_STATUS.SUCCESS,
        "request_id": 9, "session_id": 2, "user_id": 1, "task_seq": 1,
        "repair_round": 0, "reserved": 0, "output_tokens": 4,
        "output_bytes": 64, "semantic_content_digest": bytes(32),
        "completed_instance_count": 1, "moe_invocation_count": 0,
        "request_start_tick": 100, "terminal_ready_tick": 200,
        "reserved2": 0,
    }
    timing = P.encode_tlv(A.OUTPUT_TLV_TYPE.TIMING_BREAKDOWN, bytes(64),
                          A.TLV_FLAGS.REQUIRED)
    blob = P.encode_metadata(values, tail=timing)
    decoded = P.decode_metadata(blob)
    assert decoded["total_bytes"] == 128 + 8 + 64
    assert len(decoded["tlvs"]) == 1
    assert decoded["tlvs"][0]["type"] == A.OUTPUT_TLV_TYPE.TIMING_BREAKDOWN
    assert decoded["request_id"] == 9
    blob2 = bytearray(blob)
    blob2[60] ^= 0xFF
    with pytest.raises(P.ProtocolError) as err:
        P.decode_metadata(bytes(blob2))
    assert err.value.code == "E_SQ_CRC"


def test_ring_helpers():
    assert P.slot_index(5, 4) == 1
    assert P.ring_occupancy(6, 2, 4) == 4
    with pytest.raises(P.ProtocolError):
        P.ring_occupancy(2, 6, 4)  # underflow
    with pytest.raises(P.ProtocolError):
        P.ring_occupancy(7, 2, 4)  # overflow


def test_detail_code_registry_closed():
    assert A.DETAIL_CODE["E_OK"] == 0
    assert A.DETAIL_CODE_BY_VALUE[0x00020002] == "E_AGENT_PROTOCOL_FATAL"
    assert set(A.DETAIL_DISPOSITION_V1) == set(A.DETAIL_CODE)
    assert A.FAULT_SITE_PROJECTION_V1["MSI_TARGET_OR_B"] == {
        "source_class": "MSI",
        "component_kind": "NPU_FRONTEND",
        "object_kind": "MSI",
        "detail_code": "E_INTERRUPT",
    }
