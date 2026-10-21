import hashlib
import random
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture(scope="module")
def hash_probe(tmp_path_factory):
    directory = tmp_path_factory.mktemp("mesh_hash")
    source = directory / "probe.cc"
    executable = directory / "probe"
    source.write_text(
        r'''
#include <algorithm>
#include <cstdint>
#include <iostream>
#include <ostream>
#include <string>
#include <string_view>
#include <vector>

#include "dev/ai_mesh/mesh_hash.hh"

using namespace gem5::ai_mesh::mesh_hash;

uint8_t
nibble(char value)
{
    if (value >= '0' && value <= '9')
        return value - '0';
    if (value >= 'a' && value <= 'f')
        return value - 'a' + 10;
    return value - 'A' + 10;
}

std::vector<uint8_t>
decode(std::string_view text)
{
    std::vector<uint8_t> bytes;
    if (text == "-")
        return bytes;
    for (size_t index = 0; index < text.size(); index += 2) {
        bytes.push_back(
            (nibble(text[index]) << 4) | nibble(text[index + 1]));
    }
    return bytes;
}

void
print(const Sha256Digest &digest)
{
    constexpr char hex[] = "0123456789abcdef";
    for (const auto byte : digest)
        std::cout << hex[byte >> 4] << hex[byte & 0xf];
    std::cout << '\n';
}

int
main()
{
    char mode;
    size_t chunk;
    std::string encoded;
    while (std::cin >> mode >> chunk >> encoded) {
        const auto bytes = decode(encoded);
        if (mode == 'd') {
            Sha256 hash;
            for (size_t offset = 0; offset < bytes.size();) {
                const size_t count = std::min(chunk, bytes.size() - offset);
                hash.update(bytes.data() + offset, count);
                offset += count;
            }
            print(hash.digest());
        } else if (mode == 'c') {
            Sha256 hash;
            const size_t split = bytes.size() / 2;
            hash.update(bytes.data(), split);
            print(hash.digest());
            hash.update(bytes.data() + split, bytes.size() - split);
            print(hash.digest());
        } else if (mode == 's') {
            Sha256StreamBuffer buffer;
            std::ostream stream(&buffer);
            for (size_t offset = 0; offset < bytes.size();) {
                const size_t count = std::min(chunk, bytes.size() - offset);
                stream.write(
                    reinterpret_cast<const char *>(bytes.data() + offset),
                    static_cast<std::streamsize>(count));
                offset += count;
            }
            if (!stream)
                return 2;
            print(buffer.digest());
        } else if (mode == 'p') {
            Sha256StreamBuffer buffer;
            std::ostream stream(&buffer);
            for (const auto byte : bytes)
                stream.put(static_cast<char>(byte));
            if (!stream)
                return 2;
            print(buffer.digest());
        } else {
            return 3;
        }
    }
    return std::cin.eof() ? 0 : 4;
}
'''.lstrip(),
        encoding="utf-8",
    )
    compiled = subprocess.run(
        [
            "g++",
            "-std=c++17",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-pedantic-errors",
            "-I",
            str(ROOT / "src"),
            str(source),
            str(ROOT / "src/dev/ai_mesh/mesh_hash.cc"),
            "-o",
            str(executable),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert compiled.returncode == 0, compiled.stderr
    return executable


def test_incremental_and_stream_hash_match_hashlib(hash_probe):
    generator = random.Random(77031337)
    values = [
        b"",
        b"abc",
        bytes(range(256)),
        bytes(generator.getrandbits(8) for _ in range(55)),
        bytes(generator.getrandbits(8) for _ in range(56)),
        bytes(generator.getrandbits(8) for _ in range(63)),
        bytes(generator.getrandbits(8) for _ in range(64)),
        bytes(generator.getrandbits(8) for _ in range(65)),
        bytes(generator.getrandbits(8) for _ in range(4097)),
    ]
    rows = [
        (mode, chunk, value)
        for value in values
        for mode in ("d", "s", "p")
        for chunk in (1, 7, 64, 257)
    ]
    payload = "".join(
        f"{mode} {chunk} {value.hex() or '-'}\n"
        for mode, chunk, value in rows
    )
    result = subprocess.run(
        [str(hash_probe)],
        input=payload,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        hashlib.sha256(value).hexdigest()
        for _, _, value in rows
    ]


def test_digest_snapshot_does_not_finalize_incremental_state(hash_probe):
    values = [b"", b"a", bytes(range(256)), b"boundary" * 257]
    payload = "".join(f"c 1 {value.hex() or '-'}\n" for value in values)
    result = subprocess.run(
        [str(hash_probe)],
        input=payload,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    expected = []
    for value in values:
        expected.extend(
            [
                hashlib.sha256(value[: len(value) // 2]).hexdigest(),
                hashlib.sha256(value).hexdigest(),
            ]
        )
    assert result.stdout.splitlines() == expected
