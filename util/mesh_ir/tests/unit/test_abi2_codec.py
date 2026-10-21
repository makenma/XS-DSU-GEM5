from dataclasses import fields, replace

import pytest

from mesh_ir.abi.decoder import decode_program
from mesh_ir.abi.encoder import encode_program
from mesh_ir.abi.json_codec import load_program_json
from mesh_ir.canonical import semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.model import Program
from mesh_ir.scheduled.model import AuthoredProgramOrigin, ProgramSemantics
from mesh_ir.traffic import TrafficReport


def wire_program(features):
    facts = {field.name: () for field in fields(ProgramSemantics)}
    facts.update(origin=AuthoredProgramOrigin("abi2", "wire-sections", 1),
                 reference_binding_identity_sha256="0" * 64,
                 intrinsic_traffic=TrafficReport("0" * 64, (), (), "0" * 64))
    transport = {binding["program_field"]: () for binding in A.TRANSPORT_CANONICAL_SECTIONS.values()}
    program = Program(A.ABI_MAJOR, A.ABI_MINOR, bytes(32), **transport,
                      semantics=ProgramSemantics(**facts), semantic_sha256="",
                      required_features=features)
    return replace(program, semantic_sha256=semantic_sha256(program.semantic_dict()))


@pytest.mark.parametrize("features", [1, 3, 9, 11, 13, 15])
def test_each_feature_combination_roundtrips_with_mandatory_semantics(features):
    program = wire_program(features)
    decoded = decode_program(encode_program(program))
    assert decoded == program
    assert load_program_json(program.canonical_bytes()) == program
    required = set(A.conditional_required_sections(features))
    present = {getattr(A.SECTION_TYPE, name) for name in program.canonical_dict()["sections"]}
    assert required <= present
    assert all(type(getattr(decoded, binding["program_field"])) is tuple for binding in A.TRANSPORT_CANONICAL_SECTIONS.values())


@pytest.mark.parametrize("damage", ["missing_semantics", "old_major", "unknown_bit", "missing_profile_dependency"])
def test_writer_rejects_incomplete_or_legacy_contract(damage):
    program = wire_program(1)
    changes = {"missing_semantics": {"semantics": None}, "old_major": {"abi_major": 1},
               "unknown_bit": {"required_features": 17}, "missing_profile_dependency": {"required_features": 5}}
    with pytest.raises(MeshIrError):
        encode_program(replace(program, **changes[damage]))


@pytest.fixture(scope="module")
def binary_probe(tmp_path_factory):
    from pathlib import Path
    import subprocess

    root = Path(__file__).resolve().parents[4]
    work = tmp_path_factory.mktemp("abi2-binary")
    source = work / "probe.cc"
    source.write_text(r'''
#include <fstream>
#include <iostream>
#include <iterator>
#include "dev/ai_mesh/mesh_binary.hh"
#include "dev/ai_mesh/mesh_binary_canonical.hh"
#include "dev/ai_mesh/mesh_serving_projection.hh"
#include "dev/ai_mesh/mesh_hash.hh"
#include "dev/ai_mesh/mesh_kv_layout.hh"
using namespace gem5::ai_mesh;
int main(int argc, char **argv) {
    std::ifstream input(argv[1], std::ios::binary);
    MeshBytes image((std::istreambuf_iterator<char>(input)), {});
    DecodedProgram program;
    MeshLoadError error;
    MeshBytes encoded;
    if (!decodeMeshBinary(image, program, error) ||
        !encodeMeshBinary(program, encoded, error)) {
        std::cout << error.code << ": " << error.message;
        return 1;
    }
    std::ofstream output(argv[2], std::ios::binary);
    output.write(reinterpret_cast<const char *>(encoded.data()), encoded.size());
    std::ofstream json(argv[3]);
    if (!mesh_binary_detail::writeProgramCanonical(program,
            mesh_abi::CanonicalProjection::Full, json, error)) {
        std::cout << error.code << ": " << error.message;
        return 1;
    }
    if (argc > 4 && std::string(argv[4]) == "kv-layout") {
        std::array<uint8_t, 32> digest;
        ProgramSemanticContext context;
        if (!buildProgramSemanticContext(program, context, error) ||
            !kvLayoutDigest(program, context, digest, error)) {
            std::cout << error.code << ": " << error.message;
            return 1;
        }
        std::cout << mesh_hash::digestHex(digest);
        return 0;
    }
    if (argc > 4) {
        std::array<uint8_t, 32> base;
        if (!programProfileKeyBaseDigest(program, base, error)) {
            std::cout << error.code << ": " << error.message;
            return 1;
        }
        std::cout << mesh_hash::digestHex(base) << "\n";
        std::cout << requestProfileKey(program.transport.agent_request_profiles.at(0), base) << "\n";
        std::cout << (verifyRequestProfileKeys(program, error) ? "ACCEPTED" : error.code);
    }
}
''')
    sources = ["mesh_binary", "mesh_binary_envelope", "mesh_binary_storage", "mesh_binary_validation", "mesh_binary_canonical", "mesh_canonical", "mesh_hash", "mesh_serving_projection", "mesh_kv_layout", "mesh_ir_semantic_context"]
    executable = work / "probe"
    subprocess.run(["g++", "-std=c++17", "-I" + str(root / "src"), str(source),
                    *[str(root / "src/dev/ai_mesh" / (name + ".cc")) for name in sources],
                    "-o", str(executable)], check=True, capture_output=True, text=True)
    return executable


@pytest.mark.parametrize("features", [1, 3, 9, 11, 13, 15])
def test_cpp_binary_roundtrip_matches_python_bytes_and_canonical_json(binary_probe, tmp_path, features):
    import subprocess

    program = wire_program(features)
    image = encode_program(program)
    original = tmp_path / "input.mshb"
    binary = tmp_path / "output.mshb"
    json = tmp_path / "output.json"
    original.write_bytes(image)
    result = subprocess.run([str(binary_probe), str(original), str(binary), str(json)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert binary.read_bytes() == image
    assert json.read_bytes() == program.canonical_bytes()


def test_cpp_roundtrip_preserves_all_nonempty_domain_extension_records(binary_probe, tmp_path):
    import subprocess

    from mesh_ir.model import RECORD_CLASSES

    program = wire_program(15)
    replacements = {}
    for section in A.SECTION_REQUIRED_FEATURES:
        name = next(name for name in A.TRANSPORT_CANONICAL_SECTIONS if getattr(A.SECTION_TYPE, name) == section)
        values = {}
        for field in getattr(A, name + "_FIELDS"):
            kind = field["type"]
            if "const" in field:
                value = field["const"]
            elif field.get("const_zero"):
                value = bytes(int(kind[5:])) if kind.startswith("bytes") else 0
            elif "enum" in field:
                value = min(A.ENUM_CLOSED_SETS[field["enum"]])
            elif kind.startswith("bytes"):
                value = bytes(range(int(kind[5:])))
            else:
                value = 0x100000001 if kind == "u64" else 3
            values[field["name"]] = value
        replacements[A.TRANSPORT_CANONICAL_SECTIONS[name]["program_field"]] = (RECORD_CLASSES[name](**values),)
    program = replace(program, **replacements)
    program = replace(program, semantic_sha256=semantic_sha256(program.semantic_dict()))
    image = encode_program(program)
    assert decode_program(image) == program
    assert load_program_json(program.canonical_bytes()) == program
    source, binary, json = (tmp_path / name for name in ("input.mshb", "output.mshb", "output.json"))
    source.write_bytes(image)
    result = subprocess.run([str(binary_probe), str(source), str(binary), str(json)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert binary.read_bytes() == image
    assert json.read_bytes() == program.canonical_bytes()
