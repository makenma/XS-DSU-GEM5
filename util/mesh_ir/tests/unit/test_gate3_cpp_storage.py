import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture(scope="module")
def storage_probe(tmp_path_factory):
    directory = tmp_path_factory.mktemp("mesh_binary_storage")
    source = directory / "probe.cc"
    executable = directory / "probe"
    source.write_text(
        r'''
#include <algorithm>
#include <iostream>
#include <string>
#include <vector>

#include "dev/ai_mesh/mesh_binary_envelope.hh"
#include "dev/ai_mesh/mesh_binary_storage.hh"

using namespace gem5::ai_mesh;
using namespace gem5::ai_mesh::mesh_binary_detail;

int
main(int argc, char **argv)
{
    const std::string mode = argc == 1 ? "base" : argv[1];
    ProgramStorage source;
    source.header.arch_digest.fill(0x2a);
    source.metadata.min_reader_minor = mesh_abi::kMinReaderMinor;
    source.metadata.semantics = {
        mesh_abi::kSectionTypeSEMANTIC_PROGRAM_SEMANTICS, 1};
    source.metadata.semantic_sha256.fill(0x3b);
    if (mode == "rich") {
        source.semantic_strings = {"alpha"};
        source.semantic_u64_values = {11};
        source.semantic_i64_values = {-12};
        source.semantic_references = {{330, 1}};
        source.semantic_bytes = {0x13, 0x14};
        source.semantic_integer_values = {{3, 15}};
        source.transport.source_map.push_back({16, 1, 17, 18});
        source.semantic_tables.const_rows.push_back({0, 19});
    } else if (mode == "blob_gap" || mode == "blob_trailing") {
        source.semantic_strings = {"alpha"};
    }
    std::vector<MeshSection> sections;
    MeshLoadError error;
    if (mode == "minimum") {
        source.metadata.min_reader_minor = 2;
        sections.push_back({99, 0, 0, {}});
        const bool accepted = encodeProgramStorage(source, sections, error);
        std::cout << accepted << ' ' << error.code << ' ' << sections.size();
        return accepted || sections.size() != 1 ? 9 : 0;
    }
    if (!encodeProgramStorage(source, sections, error)) {
        std::cout << error.code;
        return 2;
    }
    if (mode == "base") {
        std::cout << sections.size() << ' '
                  << sections.front().type << ' '
                  << sections.back().type << ' '
                  << std::is_sorted(
                         sections.begin(), sections.end(),
                         [](const auto &left, const auto &right) {
                             return left.type < right.type;
                         }) << '\n';
        MeshBytes image;
        if (!encodeMeshEnvelope(source.header, sections, image, error))
            return 3;
        DecodedEnvelope envelope;
        if (!decodeMeshEnvelope(image, envelope, error))
            return 4;
        ProgramStorage decoded;
        if (!decodeProgramStorage(image, envelope, decoded, error)) {
            std::cout << error.code;
            return 5;
        }
        size_t semantic_tables = 0;
        mesh_abi::semantic_abi::visitSemanticTables(
            decoded.semantic_tables,
            [&](const auto &, const auto &) {
                ++semantic_tables;
                return true;
            });
        std::cout << semantic_tables << ' '
                  << decoded.metadata.min_reader_minor << ' '
                  << decoded.metadata.semantics.section_type << ' '
                  << decoded.transport.source_map.size() << '\n';
        return 0;
    }
    if (mode == "rich") {
        MeshBytes image;
        if (!encodeMeshEnvelope(source.header, sections, image, error))
            return 10;
        DecodedEnvelope envelope;
        if (!decodeMeshEnvelope(image, envelope, error))
            return 11;
        ProgramStorage decoded;
        if (!decodeProgramStorage(image, envelope, decoded, error))
            return 12;
        std::cout << decoded.semantic_strings.front() << ' '
                  << decoded.semantic_u64_values.front() << ' '
                  << decoded.semantic_i64_values.front() << ' '
                  << decoded.semantic_references.front().section_type << ' '
                  << static_cast<unsigned>(decoded.semantic_bytes.back()) << ' '
                  << decoded.semantic_integer_values.front().payload << ' '
                  << decoded.transport.source_map.size() << ' '
                  << decoded.semantic_tables.const_rows.front().value;
        return 0;
    }
    auto target = std::find_if(
        sections.begin(), sections.end(), [](const auto &section) {
            return section.type ==
                mesh_abi::kSectionTypeSEMANTIC_PROGRAM_SEMANTICS;
        });
    if (mode == "width") {
        target->record_bytes += 8;
    } else if (mode == "optional_empty") {
        sections.push_back({
            mesh_abi::kSectionTypeSOURCE_MAP,
            mesh_abi::kSourceMapBytes, 0, {}});
        std::sort(
            sections.begin(), sections.end(),
            [](const auto &left, const auto &right) {
                return left.type < right.type;
            });
    } else if (mode == "missing") {
        sections.erase(std::find_if(
            sections.begin(), sections.end(), [](const auto &section) {
                return section.type == mesh_abi::kSectionTypeSEMANTIC_BYTES;
            }));
    } else if (mode == "blob_gap" || mode == "blob_trailing") {
        target = std::find_if(
            sections.begin(), sections.end(), [](const auto &section) {
                return section.type == mesh_abi::kSectionTypeSEMANTIC_STRINGS;
            });
        if (mode == "blob_gap") {
            const size_t dataBegin = mesh_abi::kSemanticStringsBlobHeaderBytes +
                mesh_abi::kSemanticStringsDirectoryRecordBytes;
            target->payload.insert(target->payload.begin() + dataBegin, 0);
            mesh_abi::wrU32(
                target->payload.data() +
                    mesh_abi::kSemanticStringsBlobHeaderBytes +
                    mesh_abi::kSemanticStringsDirectoryOffsetOffset,
                1);
        } else {
            target->payload.push_back(0);
        }
    }
    MeshBytes image;
    if (!encodeMeshEnvelope(source.header, sections, image, error)) {
        target->count = 0;
        if (!encodeMeshEnvelope(source.header, sections, image, error))
            return 6;
    }
    DecodedEnvelope envelope;
    if (!decodeMeshEnvelope(image, envelope, error))
        return 7;
    ProgramStorage decoded;
    decoded.metadata.min_reader_minor = 77;
    decoded.header.abi_minor = 77;
    decoded.semantic_strings = {"sentinel"};
    decoded.semantic_bytes = {77};
    decoded.transport.source_map.push_back({77, 77, 77, 77});
    decoded.semantic_tables.const_rows.push_back({0, 77});
    const bool accepted = decodeProgramStorage(image, envelope, decoded, error);
    const bool preserved =
        decoded.metadata.min_reader_minor == 77 &&
        decoded.header.abi_minor == 77 &&
        decoded.semantic_strings == std::vector<std::string>{"sentinel"} &&
        decoded.semantic_bytes == std::vector<uint8_t>{77} &&
        decoded.transport.source_map.size() == 1 &&
        decoded.transport.source_map.front().loc_id == 77 &&
        decoded.semantic_tables.const_rows.size() == 1 &&
        decoded.semantic_tables.const_rows.front().value == 77;
    std::cout << accepted << ' ' << error.code << ' '
              << preserved;
    return accepted || !preserved ? 8 : 0;
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
            str(ROOT / "src/dev/ai_mesh/mesh_binary_storage.cc"),
            str(ROOT / "src/dev/ai_mesh/mesh_binary_envelope.cc"),
            str(ROOT / "src/dev/ai_mesh/mesh_canonical.cc"),
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


def test_generated_table_visitors_drive_all_required_sections(storage_probe):
    result = subprocess.run(
        [str(storage_probe)], check=False, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "119 1 352 1"
    assert lines[1] == "97 3 330 0"


def test_wrong_generated_record_width_is_rejected_atomically(storage_probe):
    result = subprocess.run(
        [str(storage_probe), "width"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "0 E_ABI_SECTION_RANGE 1"


def test_feature_implied_minimum_reader_is_enforced_atomically(storage_probe):
    result = subprocess.run(
        [str(storage_probe), "minimum"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "0 E_ABI_VERSION 1"


def test_nonempty_blob_support_optional_and_semantic_tables_round_trip(storage_probe):
    result = subprocess.run(
        [str(storage_probe), "rich"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "alpha 11 -12 330 20 15 1 19"


@pytest.mark.parametrize(
    ("mode", "diagnostic"),
    [
        ("optional_empty", "E_ABI_BOUNDS"),
        ("missing", "E_ABI_SECTION_RANGE"),
        ("blob_gap", "E_ABI_ORDER"),
        ("blob_trailing", "E_ABI_SECTION_RANGE"),
    ],
)
def test_noncanonical_storage_is_rejected_with_full_sentinel_preservation(
    storage_probe, mode, diagnostic
):
    result = subprocess.run(
        [str(storage_probe), mode],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == f"0 {diagnostic} 1"
