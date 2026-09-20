import subprocess
from pathlib import Path

import pytest

from mesh_ir.canonical import canonical_json_bytes
from mesh_ir.generated import abi as A


ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture(scope="module")
def canonical_probe(tmp_path_factory):
    directory = tmp_path_factory.mktemp("mesh_program_canonical")
    source = directory / "probe.cc"
    executable = directory / "probe"
    source.write_text(
        r'''
#include <iostream>

#include "dev/ai_mesh/mesh_binary_canonical.hh"

using namespace gem5::ai_mesh;
using namespace gem5::ai_mesh::mesh_binary_detail;
using namespace gem5::ai_mesh::mesh_abi;
using namespace gem5::ai_mesh::mesh_abi::semantic_abi;

ProgramStorage
storage(bool rich)
{
    ProgramStorage program;
    program.metadata.min_reader_minor = kMinReaderMinor;
    program.metadata.semantics = {
        kSectionTypeSEMANTIC_PROGRAM_SEMANTICS, 1};
    program.semantic_strings = rich ?
        std::vector<std::string>{"file", "identity", "name", "value"} :
        std::vector<std::string>{"identity"};
    AuthoredProgramOrigin origin;
    origin.namespace_ = {rich ? 2u : 1u};
    origin.name = {rich ? 2u : 1u};
    origin.version = 1;
    program.semantic_tables.authored_program_origin_rows.push_back(origin);
    TrafficReport traffic;
    traffic.binding_identity_sha256 = {rich ? 2u : 1u};
    traffic.semantic_sha256 = {rich ? 2u : 1u};
    program.semantic_tables.traffic_report_rows.push_back(traffic);
    ProgramSemantics root;
    root.origin = {kSectionTypeSEMANTIC_AUTHORED_PROGRAM_ORIGIN, 1};
    root.reference_binding_identity_sha256 = {rich ? 2u : 1u};
    root.intrinsic_traffic = {kSectionTypeSEMANTIC_TRAFFIC_REPORT, 1};
    program.semantic_tables.program_semantics_rows.push_back(root);
    if (rich) {
        program.transport.source_map.push_back({1, 1, 2, 3});
        program.transport.profile_hints.push_back({4, 5, 3, 4});
        ContentDigest digest;
        digest.object_kind = kContentDigestObjectKindKERNEL_OBJECT;
        digest.object_id = 7;
        digest.digest.fill(0xab);
        program.transport.content_digests.push_back(digest);
        TypedOpAttr attr;
        attr.kind = kAttrKindREPEAT_V1;
        attr.payload = RepeatV1{8, 9, 10, 0};
        program.transport.op_attrs.push_back(attr);
    }
    return program;
}

int
main()
{
    for (const bool rich : {false, true}) {
        const ProgramStorage program = storage(rich);
        MeshLoadError error;
        if (!writeProgramCanonical(
                program, CanonicalProjection::Full, std::cout, error))
            return 2;
        std::cout << '\n';
        if (!writeProgramCanonical(
                program, CanonicalProjection::Semantic, std::cout, error))
            return 3;
        std::cout << '\n';
    }
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
            str(ROOT / "src/dev/ai_mesh/mesh_binary_canonical.cc"),
            str(ROOT / "src/dev/ai_mesh/mesh_binary_validation.cc"),
            str(ROOT / "src/dev/ai_mesh/mesh_binary_storage.cc"),
            str(ROOT / "src/dev/ai_mesh/mesh_canonical.cc"),
            "-o",
            str(executable),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert compiled.returncode == 0, compiled.stderr
    return executable


@pytest.fixture(scope="module")
def semantic_value_probe(tmp_path_factory):
    directory = tmp_path_factory.mktemp("mesh_semantic_value_canonical")
    source = directory / "probe.cc"
    executable = directory / "probe"
    source.write_text(
        r'''
#include <iostream>
#include <sstream>

#include "dev/ai_mesh/mesh_binary_canonical.hh"

using namespace gem5::ai_mesh;
using namespace gem5::ai_mesh::mesh_binary_detail;
using namespace gem5::ai_mesh::mesh_abi;
using namespace gem5::ai_mesh::mesh_abi::semantic_abi;

std::string
value(const ProgramStorage &program, const SemanticRef &reference)
{
    std::ostringstream out;
    MeshLoadError error;
    if (!writeSemanticValueCanonical(program, reference, out, error))
        std::exit(2);
    return out.str();
}

int
main()
{
    ProgramStorage program;
    program.semantic_strings = {"none"};
    program.semantic_tables.const_rows = {{0, 4}, {0, 4}};
    program.semantic_references = {
        {kSectionTypeSEMANTIC_CONST, 1},
        {kSectionTypeSEMANTIC_CONST, 2},
    };
    ViewAttrs firstView;
    firstView.shape = {0, 1};
    ViewAttrs secondView;
    secondView.shape = {1, 1};
    program.semantic_tables.view_attrs_rows = {firstView, secondView};
    ElementwiseAttrs scalar;
    scalar.presence_mask =
        kElementwiseAttrsScalarField.optional_presence_mask;
    scalar.scalar = {
        mesh_abi::semantic_abi::kScalarKindU64, 7};
    scalar.scalar_side = {1};
    scalar.alpha = 1.0;
    scalar.approximation = {1};
    ElementwiseAttrs scalarDifference = scalar;
    scalarDifference.scalar.payload = 8;
    ElementwiseAttrs absentScalar = scalar;
    absentScalar.presence_mask = 0;
    absentScalar.scalar = {};
    program.semantic_tables.elementwise_attrs_rows = {
        scalar, scalarDifference, absentScalar,
    };
    const auto firstViewValue = value(
        program, {kSectionTypeSEMANTIC_VIEW_ATTRS, 1});
    const auto secondViewValue = value(
        program, {kSectionTypeSEMANTIC_VIEW_ATTRS, 2});
    const auto scalarValue = value(
        program, {kSectionTypeSEMANTIC_ELEMENTWISE_ATTRS, 1});
    const auto scalarDifferenceValue = value(
        program, {kSectionTypeSEMANTIC_ELEMENTWISE_ATTRS, 2});
    const auto absentScalarValue = value(
        program, {kSectionTypeSEMANTIC_ELEMENTWISE_ATTRS, 3});
    std::cout << (firstViewValue == secondViewValue) << ' '
              << (scalarValue == scalarDifferenceValue) << ' '
              << (scalarValue == absentScalarValue);
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
            "-ffunction-sections",
            "-I",
            str(ROOT / "src"),
            str(source),
            str(ROOT / "src/dev/ai_mesh/mesh_binary_canonical.cc"),
            str(ROOT / "src/dev/ai_mesh/mesh_binary_envelope.cc"),
            str(ROOT / "src/dev/ai_mesh/mesh_binary_storage.cc"),
            str(ROOT / "src/dev/ai_mesh/mesh_binary_validation.cc"),
            str(ROOT / "src/dev/ai_mesh/mesh_canonical.cc"),
            str(ROOT / "src/dev/ai_mesh/mesh_hash.cc"),
            "-Wl,--gc-sections",
            "-o",
            str(executable),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert compiled.returncode == 0, compiled.stderr
    return executable


def _document(semantic, rich=False):
    sections = {
        name: []
        for name, binding in A.TRANSPORT_CANONICAL_SECTIONS.items()
        if not binding["optional"]
        and (not semantic or binding["semantic"])
    }
    semantics = {
        "barrier_groups": [],
        "binding_slots": [],
        "command_semantics": [],
        "computations": [],
        "dependencies": [],
        "descriptor_groups": [],
        "endpoint_uses": [],
        "intrinsic_traffic": {
            "aggregates": [],
            "binding_identity_sha256": "identity",
            "descriptors": [],
            "semantic_sha256": "identity",
        },
        "kernel_ops": [],
        "kernel_tensors": [],
        "logical_shards": [],
        "object_backings": [],
        "objects": [],
        "origin": {
            "$type": "AuthoredProgramOrigin",
            "name": "identity",
            "namespace": "identity",
            "version": 1,
        },
        "partial_sums": [],
        "placements": [],
        "reference_binding_identity_sha256": "identity",
        "resident_views": [],
        "states": [],
        "stream_command_ids": [],
        "streams": [],
        "tokens": [],
        "variants": [],
        "views": [],
    }
    document = {
        "abi": {
            "major": A.ABI_MAJOR,
            "min_reader_minor": A.MIN_READER_MINOR,
            "minor": A.ABI_MINOR,
            "required_features": A.REQUIRED_FEATURES,
        },
        "arch_digest": "00" * 32,
        "sections": sections,
        "semantics": semantics,
    }
    if not semantic:
        document["semantic_sha256"] = "00" * 32
    if rich:
        sections["CONTENT_DIGESTS"] = [
            {
                "digest": "ab" * 32,
                "object_id": 7,
                "object_kind": A.CONTENT_DIGEST_OBJECT_KIND["KERNEL_OBJECT"],
                "reserved": 0,
            }
        ]
        sections["OP_ATTRS"] = [
            {
                "flags": 0,
                "kind": A.ATTR_KIND.REPEAT_V1,
                "repeat_count": 10,
                "subrange_begin_stream_ordinal": 8,
                "subrange_command_count": 9,
            }
        ]
        if not semantic:
            sections["PROFILE_HINTS"] = [
                {
                    "entrypoint_id": 4,
                    "name": "name",
                    "profile_id": 5,
                    "value": "value",
                }
            ]
            sections["SOURCE_MAP"] = [
                {"column": 3, "file": "file", "line": 2, "loc_id": 1}
            ]
    return document


def test_cpp_full_and_semantic_canonical_stream_match_python(canonical_probe):
    result = subprocess.run(
        [str(canonical_probe)], check=False, capture_output=True
    )
    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.splitlines() == [
        canonical_json_bytes(_document(False)),
        canonical_json_bytes(_document(True)),
        canonical_json_bytes(_document(False, True)),
        canonical_json_bytes(_document(True, True)),
    ]


def test_cpp_semantic_value_canonicalization_uses_generated_value_semantics(
    semantic_value_probe,
):
    result = subprocess.run(
        [str(semantic_value_probe)], check=False, capture_output=True
    )
    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout == b"1 0 0"
