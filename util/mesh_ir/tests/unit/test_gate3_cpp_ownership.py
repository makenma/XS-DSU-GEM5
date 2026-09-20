import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture(scope="module")
def ownership_probe(tmp_path_factory):
    directory = tmp_path_factory.mktemp("mesh_binary_ownership")
    source = directory / "probe.cc"
    executable = directory / "probe"
    source.write_text(
        r'''
#include <iostream>
#include <string>

#include "dev/ai_mesh/mesh_binary_validation.hh"

using namespace gem5::ai_mesh;
using namespace gem5::ai_mesh::mesh_binary_detail;
using namespace gem5::ai_mesh::mesh_abi;
using namespace gem5::ai_mesh::mesh_abi::semantic_abi;

ProgramStorage
validStorage()
{
    ProgramStorage program;
    program.metadata.min_reader_minor = kMinReaderMinor;
    program.metadata.semantics = {
        kSectionTypeSEMANTIC_PROGRAM_SEMANTICS, 1};
    program.semantic_strings = {"identity"};
    program.semantic_u64_values = {7};
    AuthoredProgramOrigin origin;
    origin.namespace_ = {1};
    origin.name = {1};
    origin.version = 1;
    program.semantic_tables.authored_program_origin_rows.push_back(origin);
    TrafficReport traffic;
    traffic.binding_identity_sha256 = {1};
    traffic.semantic_sha256 = {1};
    program.semantic_tables.traffic_report_rows.push_back(traffic);
    ProgramSemantics root;
    root.origin = {kSectionTypeSEMANTIC_AUTHORED_PROGRAM_ORIGIN, 1};
    root.stream_command_ids = {0, 1};
    root.reference_binding_identity_sha256 = {1};
    root.intrinsic_traffic = {kSectionTypeSEMANTIC_TRAFFIC_REPORT, 1};
    program.semantic_tables.program_semantics_rows.push_back(root);
    return program;
}

int
main(int argc, char **argv)
{
    ProgramStorage program = validStorage();
    const std::string mode = argc == 1 ? "valid" : argv[1];
    if (mode == "unreachable") {
        program.semantic_tables.authored_program_origin_rows.push_back(
            program.semantic_tables.authored_program_origin_rows.front());
    } else if (mode == "unused_string") {
        program.semantic_strings.push_back("unused");
    } else if (mode == "bad_ref") {
        program.semantic_tables.program_semantics_rows.front().origin.row_id = 2;
    } else if (mode == "unsorted_strings") {
        program.semantic_strings = {"z", "a"};
    } else if (mode == "presence") {
        program.semantic_tables.authored_program_origin_rows.front().presence_mask = 1;
    } else if (mode == "utf8") {
        program.semantic_strings.front() = std::string(1, static_cast<char>(0xff));
    } else if (mode == "reordered_spans") {
        program.semantic_tables.resident_view_rows.push_back(ResidentView{});
        program.semantic_tables.scheduled_stream_rows.push_back(ScheduledStream{});
        auto &root = program.semantic_tables.program_semantics_rows.front();
        root.resident_views = {1, 1};
        root.streams = {0, 1};
        program.semantic_references = {
            {kSectionTypeSEMANTIC_SCHEDULED_STREAM, 1},
            {kSectionTypeSEMANTIC_RESIDENT_VIEW, 1},
        };
    } else if (mode == "bounds_before_overlap") {
        DescriptorGroup group;
        group.descriptor_ids = {0, 2};
        program.semantic_tables.descriptor_group_rows.push_back(group);
        auto &root = program.semantic_tables.program_semantics_rows.front();
        root.descriptor_groups = {0, 1};
        program.semantic_references = {
            {kSectionTypeSEMANTIC_DESCRIPTOR_GROUP, 1},
        };
    } else if (mode == "transport_sid_order") {
        program.transport.strings = {"z", "unused", "a"};
    } else if (mode == "missing_feature") {
        program.header.required_features = 0;
    } else if (mode == "unknown_feature") {
        program.header.required_features = kRequiredFeatures | (1ull << 63);
    } else if (mode == "absent_hidden") {
        ElementwiseAttrs attrs;
        attrs.scalar = {
            mesh_abi::semantic_abi::kScalarKindU64, 7};
        attrs.scalar_side = {1};
        attrs.approximation = {1};
        program.semantic_tables.elementwise_attrs_rows.push_back(attrs);
    }
    MeshLoadError error;
    const bool accepted = validateProgramStorage(program, error);
    std::cout << accepted << ' ' << (error.code.empty() ? "-" : error.code);
    return 0;
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
            str(ROOT / "src/dev/ai_mesh/mesh_binary_validation.cc"),
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


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("valid", "1 -"),
        ("unreachable", "0 E_ABI_BOUNDS"),
        ("unused_string", "0 E_ABI_BOUNDS"),
        ("bad_ref", "0 E_ABI_BOUNDS"),
        ("unsorted_strings", "0 E_ABI_ORDER"),
        ("presence", "0 E_ABI_RESERVED"),
        ("utf8", "0 E_ABI_CORRUPT"),
        ("reordered_spans", "0 E_ABI_ORDER"),
        ("bounds_before_overlap", "0 E_ABI_BOUNDS"),
        ("transport_sid_order", "1 -"),
        ("missing_feature", "0 E_ABI_VERSION"),
        ("unknown_feature", "0 E_ABI_VERSION"),
        ("absent_hidden", "0 E_ABI_RESERVED"),
    ],
)
def test_semantic_tree_ownership_is_exact_and_iterative(
    ownership_probe, mode, expected
):
    result = subprocess.run(
        [str(ownership_probe), mode],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected
