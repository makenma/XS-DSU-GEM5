from __future__ import annotations

import hashlib

from axi_test_lib import (
    build_random_scenario,
    canonical_json_bytes,
    keyed_random,
    semantic_config,
    sha256_bytes,
    workload_jsonl,
)


def test_splitmix64_golden_vectors():
    expected = [
        0x31677AECE382CE62, 0x101BE0CA4DF15567,
        0xF2D743C607966570, 0x4F1E6D1DA23A7F9E,
        0xE26D4454964407E2, 0x17BF6794A34B0EB6,
        0xC108D411634279B8, 0xE8C056FD8320C8F7,
        0x6C18A344C0FEA588, 0x2022EA34C1939C72,
        0x5A8A356F81B25B01, 0x5AC8C643AF5F7F24,
        0xD71437C08B6B7F1B, 0xBD1F9ED820D2FA90,
        0x262D629767F4A61A, 0x1C9C30CE0A6F9B2C,
        0x0F057D0F930BF8C5, 0x397AAA024094B821,
        0x64587C68FAD6F940, 0xF7E2308D48CB7A0A,
        0x7783D7A3FDCC43C4, 0x4E914FE69ACD7DAE,
        0xA2F1EA35C2D96247, 0x3CA680548200FA08,
        0x5A14100B2E91579F, 0x50994DBAE62B9EC9,
        0xA98930ED3489521F, 0x3721B15C65172F40,
        0x24BBBC0FAAACA731, 0x34A0D8861075B7F2,
        0x905E1DE3A05FD653, 0xBA01B542D8861770,
        0x0FA98E5B3045A544, 0xB220025A5B0A2B3C,
        0xE7267FA0F7A251E2, 0xAB3C3798CB7A9ABE,
        0x9C4F6DE84E183565, 0xAEBA2C3799A89277,
        0xE2E559957290B6F7, 0x4D93F25E0F83859F,
    ]
    seeds = (0, 1, 7, 42, (1 << 64) - 1)
    for index, golden in enumerate(expected):
        assert keyed_random(
            seeds[index % len(seeds)],
            4660 + 17 * index * index,
            index % 5 + 1,
            (13 * index) % 257,
            index % 12 + 1,
        ) == golden
    assert keyed_random(42, 0x1234, 1, 0, 13) == 0x497F52B4D270CA5F


def test_canonical_jsonl_bytes_and_sha():
    encoded = canonical_json_bytes({"z": False, "b": "x", "a": 1})
    assert encoded == b'{"a":1,"b":"x","z":false}\n'
    assert hashlib.sha256(encoded).hexdigest() == \
        "3a5e0c6c51f317056132d1dd61824cde9b84adf26c9616eb24f6ca16a128b10c"


def test_replay_hash_independent_of_invocation():
    first = build_random_scenario(42, 64, "determinism_s42")
    second = build_random_scenario(42, 64, "determinism_s42")
    first["name"] = "determinism_a"
    second["name"] = "determinism_replay"
    first_config = semantic_config(first, {
        "seed": 42, "invocation_id": "a", "outdir": "/tmp/a",
        "result_path": "/tmp/a/result.json",
    })
    second_config = semantic_config(second, {
        "seed": 42, "invocation_id": "replay", "outdir": "/tmp/replay",
        "result_path": "/tmp/replay/result.json",
    })
    assert sha256_bytes(canonical_json_bytes(first_config)) == \
        sha256_bytes(canonical_json_bytes(second_config))
    assert workload_jsonl(first, "determinism_s42", 42) == \
        workload_jsonl(second, "determinism_s42", 42)
