"""Python mirror of the contract output-byte and payload-digest formulas.

The runtime implements the same rules in ``agent_surrogate_codec.cc``
(``surrogateOutputBytes``) and ``mesh_payload_digest.hh``
(``dualFnvDigest``); acceptance recomputes the published bytes from the
frozen semantic digest instead of trusting runtime-reported digests.
"""

from __future__ import annotations

import hashlib
import struct

OUTPUT_BYTES_DOMAIN = b"AI_MESH_OUTPUT_BYTES_V1\0"
BLOCK_BYTES = 32


def surrogate_output_bytes(semantic_digest: bytes, total_bytes: int) -> bytes:
    if len(semantic_digest) != 32:
        raise ValueError("semantic digest must be 32 bytes")
    if total_bytes < 0:
        raise ValueError("total bytes must not be negative")
    output = bytearray()
    ordinal = 0
    while len(output) < total_bytes:
        block = hashlib.sha256()
        block.update(OUTPUT_BYTES_DOMAIN)
        block.update(semantic_digest)
        block.update(struct.pack("<Q", ordinal))
        digest = block.digest()
        remaining = total_bytes - len(output)
        output.extend(digest[:min(remaining, BLOCK_BYTES)])
        ordinal += 1
    return bytes(output)


def dual_fnv_digest(data: bytes) -> str:
    h0 = 0xCBF29CE484222325
    h1 = 0x9E3779B97F4A7C15
    mask = (1 << 64) - 1
    for byte in data:
        h0 = ((h0 ^ byte) * 0x100000001B3) & mask
        h1 = ((h1 + ((h0 >> 31) ^ byte)) * 0xBF58476D1CE4E5B9) & mask
    return "%016x-%016x" % (h0, h1)
