from dataclasses import replace
import hashlib
import struct
import subprocess
from pathlib import Path
import zlib

import pytest

from mesh_ir.abi import decode_program, encode_program
from mesh_ir.architecture import load_arch
from mesh_ir.canonical import semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.golden_programs import build_single_core_program
from mesh_ir.model import ContentDigest
from tests.golden.support.optional_section import (
    DIRECTORY_OFFSETS,
    HEADER_OFFSETS,
)


ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture(scope="module")
def optional_probe(tmp_path_factory):
    directory = tmp_path_factory.mktemp("mesh_binary_optional")
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
programWithStrings(std::vector<std::string> strings, uint32_t identity)
{
    ProgramStorage program;
    program.metadata.min_reader_minor = kMinReaderMinor;
    program.metadata.semantics = {
        kSectionTypeSEMANTIC_PROGRAM_SEMANTICS, 1};
    program.semantic_strings = std::move(strings);
    AuthoredProgramOrigin origin;
    origin.namespace_ = {identity};
    origin.name = {identity};
    origin.version = 1;
    program.semantic_tables.authored_program_origin_rows.push_back(origin);
    TrafficReport traffic;
    traffic.binding_identity_sha256 = {identity};
    traffic.semantic_sha256 = {identity};
    program.semantic_tables.traffic_report_rows.push_back(traffic);
    ProgramSemantics root;
    root.origin = {kSectionTypeSEMANTIC_AUTHORED_PROGRAM_ORIGIN, 1};
    root.reference_binding_identity_sha256 = {identity};
    root.intrinsic_traffic = {kSectionTypeSEMANTIC_TRAFFIC_REPORT, 1};
    program.semantic_tables.program_semantics_rows.push_back(root);
    return program;
}

int
main(int argc, char **argv)
{
    const std::string mode = argc == 1 ? "valid" : argv[1];
    ProgramStorage program = programWithStrings({"identity"}, 1);
    if (mode == "source_unknown" || mode == "source_duplicate" ||
        mode == "source_absolute") {
        program = programWithStrings(
            {mode == "source_absolute" ? "/a.py" : "a.py", "identity"}, 2);
        program.transport.source_map.push_back({1, 1, 1, 0});
        if (mode == "source_duplicate")
            program.transport.source_map.push_back({1, 1, 2, 0});
        if (mode == "source_unknown") {
            Command command;
            command.command_id = 1;
            command.engine = kEngineCONTROL;
            command.opcode = kOpcodeHALT;
            command.debug_loc_id = 2;
            program.transport.commands.push_back(command);
        }
    } else if (mode == "hint_unknown") {
        program = programWithStrings({"identity", "name", "value"}, 1);
        program.transport.profile_hints.push_back({1, 1, 2, 3});
    } else if (mode == "hint_reordered") {
        program = programWithStrings({"a", "b", "identity", "value"}, 3);
        program.transport.entrypoints.push_back({1});
        program.transport.profiles.push_back({1, 1});
        program.transport.profile_hints.push_back({1, 1, 2, 4});
        program.transport.profile_hints.push_back({1, 1, 1, 4});
    } else if (mode == "digest_unknown" || mode == "digest_kind") {
        ContentDigest digest;
        digest.object_kind = mode == "digest_kind" ? 99 :
            kContentDigestObjectKindTENSOR;
        digest.object_id = 9;
        program.transport.content_digests.push_back(digest);
    } else if (mode == "digest_contradiction") {
        Tensor tensor;
        tensor.tensor_id = 1;
        tensor.role = kTensorRoleINPUT;
        tensor.dtype = kDtypeFP16;
        tensor.storage_class = kStorageClassEXTERNAL;
        tensor.access = kAccessKindREAD_ONLY;
        tensor.layout = kLayoutKindCONTIGUOUS_ROW_MAJOR;
        tensor.flags = kTensorFlagsHAS_CONTENT_SHA256;
        tensor.content_sha256.fill(1);
        program.transport.tensors.push_back(tensor);
        ContentDigest digest;
        digest.object_kind = kContentDigestObjectKindTENSOR;
        digest.object_id = 1;
        digest.digest.fill(2);
        program.transport.content_digests.push_back(digest);
    }
    MeshLoadError error;
    const bool accepted = validateProgramStorage(program, error);
    std::cout << accepted << ' ' << (error.code.empty() ? "-" : error.code);
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


@pytest.fixture(scope="module")
def optional_codec_probe(tmp_path_factory):
    directory = tmp_path_factory.mktemp("mesh_binary_optional_codec")
    source = directory / "probe.cc"
    executable = directory / "probe"
    source.write_text(
        r'''
#include <fstream>
#include <iostream>
#include <iterator>
#include <string>

#include "dev/ai_mesh/mesh_binary.hh"

using namespace gem5::ai_mesh;

MeshBytes
readImage(const char *path)
{
    std::ifstream file(path, std::ios::binary);
    return MeshBytes((std::istreambuf_iterator<char>(file)), {});
}

int
main(int argc, char **argv)
{
    if (argc != 4)
        return 2;
    MeshLoadError error;
    DecodedProgram output;
    if (!decodeMeshBinary(readImage(argv[1]), output, error))
        return 3;
    MeshBytes before;
    if (!encodeMeshBinary(output, before, error))
        return 4;
    const bool accepted = decodeMeshBinary(readImage(argv[2]), output, error);
    const std::string mode(argv[3]);
    if (mode == "accept") {
        if (!accepted)
            return 5;
        std::cout << "ACCEPTED";
        return 0;
    }
    if (accepted)
        return 6;
    const std::string code = error.code;
    MeshBytes after;
    if (!encodeMeshBinary(output, after, error) || after != before)
        return 7;
    std::cout << code << " full-sentinel-preserved";
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
            *(
                str(ROOT / "src/dev/ai_mesh" / name)
                for name in (
                    "mesh_binary.cc",
                    "mesh_binary_envelope.cc",
                    "mesh_binary_storage.cc",
                    "mesh_binary_validation.cc",
                    "mesh_binary_canonical.cc",
                    "mesh_canonical.cc",
                    "mesh_hash.cc",
                )
            ),
            "-o",
            str(executable),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert compiled.returncode == 0, compiled.stderr
    return executable


def _program_with_kernel_digest(program, object_id):
    changed = replace(
        program,
        content_digests=(
            ContentDigest(
                A.CONTENT_DIGEST_OBJECT_KIND["KERNEL_OBJECT"],
                0,
                object_id,
                bytes(32),
            ),
        ),
    )
    return replace(
        changed, semantic_sha256=semantic_sha256(changed.semantic_dict())
    )


def _section_directory(image):
    directory = {}
    header = dict(
        zip(
            (field["name"] for field in A.HEADER_FIELDS),
            A.HEADER_FORMAT.unpack_from(image),
        )
    )
    directory_offset = header["section_dir_offset"]
    count = header["section_count"]
    for index in range(count):
        entry = directory_offset + A.SECTION_DIR_BYTES * index
        section = dict(
            zip(
                (field["name"] for field in A.SECTION_DIR_FIELDS),
                A.SECTION_DIR_FORMAT.unpack_from(image, entry),
            )
        )
        directory[section["section_type"]] = (
            entry,
            section["offset"],
            section["size"],
        )
    return directory


def _finish_binary_mutation(image, document, directory):
    metadata = directory[A.SECTION_TYPE.PROGRAM_METADATA][1]
    semantic_digest = bytes.fromhex(semantic_sha256(document))
    semantic_digest_offset = (
        metadata + A.PROGRAM_METADATA_FIELD_OFFSETS["semantic_sha256"]
    )
    image[
        semantic_digest_offset : semantic_digest_offset + len(semantic_digest)
    ] = semantic_digest
    for entry, offset, size in directory.values():
        struct.pack_into(
            "<I",
            image,
            entry + DIRECTORY_OFFSETS["crc32"],
            zlib.crc32(image[offset : offset + size]) & 0xFFFFFFFF,
        )
    payload_digest = hashlib.sha256(image[A.HEADER_BYTES :]).digest()
    payload_digest_offset = HEADER_OFFSETS["payload_sha256"]
    image[
        payload_digest_offset : payload_digest_offset + len(payload_digest)
    ] = payload_digest
    return bytes(image)


def _with_high_first_object_id(program, object_id):
    image = bytearray(encode_program(program))
    directory = _section_directory(image)
    definition = A.SEMANTIC_RECORDS["mesh_ir.ir.kernel_ir.BufferObject"]
    field_offset = next(
        field["offset"]
        for field in definition["fields"]
        if field["name"] == "object_id"
    )
    struct.pack_into(
        "<Q",
        image,
        directory[definition["section_type"]][1] + field_offset,
        object_id,
    )
    document = program.semantic_dict()
    document["semantics"]["objects"][0]["object_id"] = object_id
    return _finish_binary_mutation(image, document, directory)


def _with_digest_object_id(program, object_id):
    image = bytearray(encode_program(program))
    directory = _section_directory(image)
    offset = directory[A.SECTION_TYPE.CONTENT_DIGESTS][1]
    struct.pack_into(
        "<I",
        image,
        offset + A.CONTENT_DIGESTS_FIELD_OFFSETS["object_id"],
        object_id,
    )
    document = program.semantic_dict()
    document["sections"]["CONTENT_DIGESTS"][0]["object_id"] = object_id
    return _finish_binary_mutation(image, document, directory)


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("valid", "1 -"),
        ("source_unknown", "0 E_ABI_BOUNDS"),
        ("source_duplicate", "0 E_ABI_ORDER"),
        ("source_absolute", "0 E_ABI_BOUNDS"),
        ("hint_unknown", "0 E_ABI_BOUNDS"),
        ("hint_reordered", "0 E_ABI_ORDER"),
        ("digest_unknown", "0 E_ABI_BOUNDS"),
        ("digest_kind", "0 E_ABI_ENUM"),
        ("digest_contradiction", "0 E_ABI_CHECKSUM"),
    ],
)
def test_optional_tables_are_freshly_validated(optional_probe, mode, expected):
    result = subprocess.run(
        [str(optional_probe), mode],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected


@pytest.mark.parametrize(
    ("case", "expected"),
    (
        ("ordinary", "ACCEPTED"),
        ("absent", "E_ABI_BOUNDS full-sentinel-preserved"),
        ("high_alias", "E_ABI_BOUNDS full-sentinel-preserved"),
        ("high_unreferenced", "ACCEPTED"),
    ),
)
def test_kernel_object_digest_uses_full_semantic_identity(
    optional_codec_probe, tmp_path, case, expected
):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    baseline_program = build_single_core_program(arch)
    ordinary = _program_with_kernel_digest(baseline_program, 1)
    baseline = tmp_path / "baseline.mshb"
    baseline.write_bytes(encode_program(ordinary))
    if case == "ordinary":
        candidate = baseline.read_bytes()
    elif case == "absent":
        candidate = _with_digest_object_id(ordinary, 9999)
    elif case == "high_alias":
        candidate = _with_high_first_object_id(ordinary, (1 << 32) + 1)
    else:
        candidate = _with_high_first_object_id(
            baseline_program, (1 << 32) + 1
        )
    path = tmp_path / "candidate.mshb"
    path.write_bytes(candidate)
    if expected == "ACCEPTED":
        decode_program(candidate)
        mode = "accept"
    else:
        with pytest.raises(MeshIrError) as caught:
            decode_program(candidate)
        assert caught.value.code == "E_ABI_BOUNDS"
        mode = "reject"
    result = subprocess.run(
        [str(optional_codec_probe), str(baseline), str(path), mode],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == expected
