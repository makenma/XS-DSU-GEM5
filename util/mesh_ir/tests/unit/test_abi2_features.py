from pathlib import Path
import subprocess

import pytest

from mesh_ir.abi import rules
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A


ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture(scope="module")
def envelope_probe(tmp_path_factory):
    work = tmp_path_factory.mktemp("abi2-envelope")
    source = work / "probe.cc"
    source.write_text(r'''
#include <iostream>
#include "dev/ai_mesh/mesh_binary_envelope.hh"
using namespace gem5::ai_mesh;
int main(int argc, char **argv) {
    mesh_binary_detail::MeshHeader header;
    header.abi_major = std::stoul(argv[1]);
    header.required_features = std::stoull(argv[2]);
    MeshLoadError error;
    if (!mesh_binary_detail::validateMeshHeader(header, error)) {
        std::cout << error.code;
        return 0;
    }
    std::vector<mesh_binary_detail::MeshSection> sections;
    for (int i = 3; i < argc; ++i) {
        mesh_binary_detail::MeshSection row;
        row.type = std::stoul(argv[i]);
        sections.push_back(row);
    }
    MeshBytes encoded;
    mesh_binary_detail::DecodedEnvelope decoded;
    bool accepted = mesh_binary_detail::encodeMeshEnvelope(header, sections, encoded, error) &&
                    mesh_binary_detail::decodeMeshEnvelope(encoded, decoded, error);
    std::cout << (accepted ? "ACCEPTED" : error.code);
}
''')
    executable = work / "probe"
    subprocess.run([
        "g++", "-std=c++17", "-I" + str(ROOT / "src"), str(source),
        str(ROOT / "src/dev/ai_mesh/mesh_binary_envelope.cc"),
        str(ROOT / "src/dev/ai_mesh/mesh_hash.cc"), "-o", str(executable),
    ], check=True, capture_output=True, text=True)
    return executable


@pytest.mark.parametrize("major,features", [(2, mask) for mask in range(16)] + [(1, 1), (2, 17), (2, 1 << 63)])
def test_actual_python_and_cpp_headers_share_feature_contract(envelope_probe, major, features):
    accepted = major == 2 and features in {1, 3, 9, 11, 13, 15}
    sections = sorted(A.conditional_required_sections(features))
    result = subprocess.run([str(envelope_probe), str(major), str(features), *map(str, sections)], check=True, capture_output=True, text=True)
    assert result.stdout == ("ACCEPTED" if accepted else "E_ABI_VERSION")
    if accepted:
        rules.check_abi_header(major, 0, features)
    else:
        with pytest.raises(MeshIrError, match="E_ABI_VERSION"):
            rules.check_abi_header(major, 0, features)


@pytest.mark.parametrize("damage", ("healthy_moe", "shared_content", "missing_moe", "ungated_moe", "missing_profile"))
def test_section_presence_and_ownership_are_independent(envelope_probe, damage):
    features = 3
    sections = set(A.conditional_required_sections(features))
    if damage == "shared_content":
        features = 1
        sections = {A.SECTION_TYPE.CONTENT_DIGESTS}
    elif damage == "missing_moe":
        sections.remove(A.SECTION_TYPE.MOE_KERNEL_SPECS)
    elif damage == "ungated_moe":
        features = 1
    elif damage == "missing_profile":
        features = 13
        sections = set(A.conditional_required_sections(features)) - {A.SECTION_TYPE.PROFILE_STREAM_RANGES}
    accepted = damage in {"healthy_moe", "shared_content"}
    result = subprocess.run([str(envelope_probe), "2", str(features), *map(str, sorted(sections))], check=True, capture_output=True, text=True)
    assert result.stdout == ("ACCEPTED" if accepted else "E_ABI_FEATURE")
    if accepted:
        rules.check_section_features(features, sections)
    else:
        with pytest.raises(MeshIrError, match="E_ABI_FEATURE"):
            rules.check_section_features(features, sections)
