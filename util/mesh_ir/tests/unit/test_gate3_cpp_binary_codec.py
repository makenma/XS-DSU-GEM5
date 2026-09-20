import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture(scope="module")
def binary_probe(tmp_path_factory):
    directory = tmp_path_factory.mktemp("mesh_binary_codec")
    source = directory / "probe.cc"
    executable = directory / "probe"
    source.write_text(
        r'''
#include <iostream>
#include <ostream>

#include "dev/ai_mesh/mesh_binary.hh"
#include "dev/ai_mesh/mesh_binary_canonical.hh"
#include "dev/ai_mesh/mesh_hash.hh"

using namespace gem5::ai_mesh;
using namespace gem5::ai_mesh::mesh_binary_detail;
using namespace gem5::ai_mesh::mesh_abi;
using namespace gem5::ai_mesh::mesh_abi::semantic_abi;

DecodedProgram
program()
{
    DecodedProgram value;
    value.metadata.min_reader_minor = kMinReaderMinor;
    value.metadata.semantics = {
        kSectionTypeSEMANTIC_PROGRAM_SEMANTICS, 1};
    value.semantic_strings = {"identity"};
    AuthoredProgramOrigin origin;
    origin.namespace_ = {1};
    origin.name = {1};
    origin.version = 1;
    value.semantic_tables.authored_program_origin_rows.push_back(origin);
    TrafficReport traffic;
    traffic.binding_identity_sha256 = {1};
    traffic.semantic_sha256 = {1};
    value.semantic_tables.traffic_report_rows.push_back(traffic);
    ProgramSemantics root;
    root.origin = {kSectionTypeSEMANTIC_AUTHORED_PROGRAM_ORIGIN, 1};
    root.reference_binding_identity_sha256 = {1};
    root.intrinsic_traffic = {kSectionTypeSEMANTIC_TRAFFIC_REPORT, 1};
    value.semantic_tables.program_semantics_rows.push_back(root);
    mesh_hash::Sha256StreamBuffer buffer;
    std::ostream stream(&buffer);
    MeshLoadError error;
    if (!writeProgramCanonical(
            value, CanonicalProjection::Semantic, stream, error))
        std::exit(9);
    value.metadata.semantic_sha256 = buffer.digest();
    return value;
}

int
main(int argc, char **argv)
{
    const std::string mode = argc == 1 ? "roundtrip" : argv[1];
    DecodedProgram source = program();
    MeshBytes image{77};
    MeshLoadError error;
    if (mode == "stale")
        source.metadata.semantic_sha256.front() ^= 1;
    else if (mode == "major")
        source.header.abi_major = 0;
    else if (mode == "minor")
        source.header.abi_minor = kMinReaderMinor - 1;
    else if (mode == "feature")
        source.header.required_features = 0;
    else if (mode == "minimum")
        source.metadata.min_reader_minor = kMinReaderMinor - 1;
    const bool encoded = encodeMeshBinary(source, image, error);
    if (mode == "stale" || mode == "major" || mode == "minor" ||
        mode == "feature" || mode == "minimum") {
        std::cout << encoded << ' ' << error.code << ' ' << image.size()
                  << ' ' << static_cast<unsigned>(image.front());
        return 0;
    }
    if (!encoded)
        return 2;
    DecodedProgram decoded;
    decoded.metadata.min_reader_minor = 77;
    if (mode == "corrupt")
        image.back() ^= 1;
    const bool accepted = decodeMeshBinary(image, decoded, error);
    if (mode == "corrupt") {
        std::cout << accepted << ' ' << error.code << ' '
                  << decoded.metadata.min_reader_minor;
        return 0;
    }
    if (!accepted)
        return 3;
    MeshBytes reencoded;
    if (!encodeMeshBinary(decoded, reencoded, error))
        return 4;
    std::cout << (image == reencoded) << ' '
              << decoded.semantic_tables.program_semantics_rows.size() << ' '
              << decoded.semantic_strings.front();
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
            *(str(ROOT / "src/dev/ai_mesh" / name) for name in (
                "mesh_binary.cc",
                "mesh_binary_envelope.cc",
                "mesh_binary_storage.cc",
                "mesh_binary_validation.cc",
                "mesh_binary_canonical.cc",
                "mesh_canonical.cc",
                "mesh_hash.cc",
            )),
            "-o",
            str(executable),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert compiled.returncode == 0, compiled.stderr
    return executable


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("roundtrip", "1 1 identity"),
        ("stale", "0 E_ABI_CHECKSUM 1 77"),
        ("major", "0 E_ABI_VERSION 1 77"),
        ("minor", "0 E_ABI_VERSION 1 77"),
        ("feature", "0 E_ABI_VERSION 1 77"),
        ("minimum", "0 E_ABI_VERSION 1 77"),
        ("corrupt", "0 E_ABI_CHECKSUM 77"),
    ],
)
def test_public_cpp_binary_codec_is_typed_atomic_and_fresh(
    binary_probe, mode, expected
):
    result = subprocess.run(
        [str(binary_probe), mode],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == expected
