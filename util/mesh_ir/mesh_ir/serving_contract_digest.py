"""Independent derivation of the frozen serving request expectation.

Mirrors the NPU semantic-digest codec (``agent_surrogate_codec.cc``:
``surrogateSeed``/``surrogateTokenDigest``/``surrogateOutputPrefixDigest``)
so acceptance can derive the expected semantic digest and published bytes
from the frozen workload inputs instead of trusting runtime-reported values.
"""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

from mesh_ir.serving_output_bytes import dual_fnv_digest, surrogate_output_bytes

SURROGATE_DOMAIN = b"AI_MESH_AGENT_SURROGATE_V1\0"
PREFIX_DOMAIN = b"AGENT_OUTPUT_PREFIX_V1\0"


def surrogate_seed(input_content_digest: bytes, program_id: int, profile_id: int,
                   requested_profile_key: int) -> bytes:
    if len(input_content_digest) != 32:
        raise ValueError("input content digest must be 32 bytes")
    return hashlib.sha256(
        SURROGATE_DOMAIN + input_content_digest +
        struct.pack("<HHQ", program_id, profile_id,
                    requested_profile_key)).digest()


def token_digests(seed: bytes, output_tokens: int) -> list[bytes]:
    return [hashlib.sha256(seed + struct.pack("<I", ordinal)).digest()
            for ordinal in range(output_tokens)]


def semantic_output_digest(workload_digest: bytes, workload_plan_item_id: int,
                           output_tokens: int,
                           tokens: list[bytes]) -> bytes:
    if len(tokens) != output_tokens:
        raise ValueError("token digest count differs from the output tokens")
    payload = PREFIX_DOMAIN + workload_digest + \
        struct.pack("<II", workload_plan_item_id, output_tokens)
    for token in tokens:
        payload += token
    return hashlib.sha256(payload).digest()


def frozen_expectation(workload_path: Path, program, profile_id: int,
                       output_bytes: int) -> dict:
    """Derive the expectation for one frozen round of the served request."""
    from mesh_ir.agent_workload import load_workload_plan

    workload = load_workload_plan(workload_path)
    item = json.loads(Path(workload_path).read_text("utf-8"))["users"][0][
        "tasks"][0]["rounds"][0]
    profile = next(record for record in program.agent_request_profiles
                   if record.profile_id == profile_id)
    seed = surrogate_seed(bytes.fromhex(item["input_content_digest"]),
                          item["program_id"], item["profile_id"],
                          int(item["requested_profile_key"], 16))
    tokens = token_digests(seed, item["output_tokens"])
    semantic = semantic_output_digest(bytes.fromhex(workload.digest),
                                      item["workload_plan_item_id"],
                                      item["output_tokens"], tokens)
    content = surrogate_output_bytes(semantic, output_bytes)
    input_bytes = profile.full_input_tokens * profile.kv_bytes_per_token
    kv_content = b"".join(
        hashlib.sha256(b"AI_MESH_INPUT_V1\0" +
                       bytes.fromhex(item["input_content_digest"]) +
                       struct.pack("<Q", ordinal)).digest()
        for ordinal in range((input_bytes + 31) // 32))[:input_bytes]
    kv_content += b"".join(surrogate_output_bytes(token, profile.kv_bytes_per_token)
                           for token in tokens)
    return {
        "semantic_digest": semantic.hex(),
        "output_bytes": output_bytes,
        "host_digest": dual_fnv_digest(content),
        "content": content,
        "kv_content": kv_content,
        "profile_id": profile.profile_id,
        "output_tokens": item["output_tokens"],
    }
