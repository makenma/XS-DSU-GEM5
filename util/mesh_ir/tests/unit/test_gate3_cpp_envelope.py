import hashlib
import struct
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture(scope="module")
def envelope_probe(tmp_path_factory):
    directory = tmp_path_factory.mktemp("mesh_binary_envelope")
    source = directory / "probe.cc"
    executable = directory / "probe"
    source.write_text(
        r'''
#include <cstdint>
#include <iostream>
#include <iterator>
#include <string>
#include <vector>

#include "dev/ai_mesh/mesh_binary_envelope.hh"

using namespace gem5::ai_mesh;
using namespace gem5::ai_mesh::mesh_binary_detail;

int
main(int argc, char **)
{
    if (argc != 1) {
        MeshBytes image(
            std::istreambuf_iterator<char>(std::cin),
            std::istreambuf_iterator<char>());
        DecodedEnvelope out;
        out.header.abi_minor = 77;
        out.sections.push_back({99, 0, 0, 0, 0});
        MeshLoadError error;
        if (decodeMeshEnvelope(image, out, error)) {
            std::cout << "OK";
            return 0;
        }
        std::cout << error.code << ' ' << out.header.abi_minor << ' '
                  << out.sections.size();
        return 1;
    }
    MeshHeader header;
    header.arch_digest.fill(0x5a);
    std::vector<MeshSection> sections{
        {1, 0, 1, MeshBytes{0x41}},
        {16, 4, 1, MeshBytes{1, 2, 3, 4}},
    };
    MeshBytes image;
    MeshLoadError error;
    if (!encodeMeshEnvelope(header, sections, image, error))
        return 2;
    for (const auto byte : image) {
        constexpr char hex[] = "0123456789abcdef";
        std::cout << hex[byte >> 4] << hex[byte & 0xf];
    }
    std::cout << '\n';
    DecodedEnvelope decoded;
    if (!decodeMeshEnvelope(image, decoded, error))
        return 3;
    std::cout << decoded.header.abi_minor << ' '
              << decoded.header.required_features << ' '
              << decoded.sections.size() << ' '
              << decoded.sections[0].offset << ' '
              << decoded.sections[1].offset << '\n';
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
            str(ROOT / "src/dev/ai_mesh/mesh_binary_envelope.cc"),
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


def _refresh_payload_sha(image):
    image[72:104] = hashlib.sha256(image[128:]).digest()


def _run_decoder(executable, image):
    result = subprocess.run(
        [str(executable), "decode"],
        input=bytes(image),
        check=False,
        capture_output=True,
    )
    return result.returncode, result.stdout.decode()


def test_envelope_encode_decode_is_dense_and_deterministic(envelope_probe):
    result = subprocess.run(
        [str(envelope_probe)], check=False, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    encoded, facts = result.stdout.splitlines()
    image = bytes.fromhex(encoded)
    assert len(image) == struct.unpack_from("<Q", image, 16)[0]
    assert image[72:104] == hashlib.sha256(image[128:]).digest()
    assert facts == "3 1 2 208 216"
    assert image[209:216] == bytes(7)


def test_envelope_rejects_integrity_and_layout_faults_atomically(
    envelope_probe,
):
    valid = subprocess.run(
        [str(envelope_probe)], check=True, capture_output=True, text=True
    ).stdout.splitlines()[0]
    cases = {}
    image = bytearray.fromhex(valid)
    image[0] ^= 1
    cases["magic"] = (image, "E_ABI_MAGIC 77 1")
    image = bytearray.fromhex(valid)
    struct.pack_into("<H", image, 8, 2)
    cases["major"] = (image, "E_ABI_VERSION 77 1")
    image = bytearray.fromhex(valid)
    struct.pack_into("<Q", image, 104, 0)
    cases["feature"] = (image, "E_ABI_VERSION 77 1")
    image = bytearray.fromhex(valid)
    image[112] = 1
    cases["reserved"] = (image, "E_ABI_RESERVED 77 1")
    image = bytearray.fromhex(valid)
    image[209] = 1
    _refresh_payload_sha(image)
    cases["padding"] = (image, "E_ABI_CORRUPT 77 1")
    image = bytearray.fromhex(valid)
    image[208] ^= 1
    _refresh_payload_sha(image)
    cases["crc"] = (image, "E_ABI_CHECKSUM 77 1")
    image = bytearray.fromhex(valid)
    struct.pack_into("<H", image, 128 + 40, 1)
    _refresh_payload_sha(image)
    cases["order"] = (image, "E_ABI_ORDER 77 1")
    image = bytearray.fromhex(valid)
    image[216:216] = bytes(8)
    struct.pack_into("<Q", image, 16, len(image))
    struct.pack_into("<Q", image, 128 + 40 + 8, 224)
    _refresh_payload_sha(image)
    cases["zero_hole"] = (image, "E_ABI_SECTION_RANGE 77 1")
    image = bytearray.fromhex(valid)
    struct.pack_into("<Q", image, 16, len(image) + 1)
    image.append(0)
    _refresh_payload_sha(image)
    cases["trailing"] = (image, "E_ABI_SECTION_RANGE 77 1")
    for image, expected in cases.values():
        code, output = _run_decoder(envelope_probe, image)
        assert code == 1
        assert output == expected
