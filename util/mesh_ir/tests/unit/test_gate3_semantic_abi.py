import dataclasses
import copy
import subprocess
import sys
import tokenize
import typing
from enum import Enum
from pathlib import Path

import pytest
import yaml

from mesh_ir.abi.generate_abi import render_semantic_cpp_enum_traits
from mesh_ir.abi.schema_loader import field_size, load_schema
from mesh_ir.generated import abi as A
from mesh_ir.scheduled.model import ProgramSemantics


ROOT = Path(__file__).resolve().parents[4]
SCHEMA_PATH = ROOT / "util/mesh_ir/mesh_ir/abi/mesh_ir_abi.yaml"
SCHEMA = load_schema(SCHEMA_PATH)


def _annotation_binding(annotation):
    args = tuple(item for item in typing.get_args(annotation) if item is not type(None))
    optional = len(args) != len(typing.get_args(annotation))
    if set(args) == {int, float, bool}:
        return {"scalar"}, (), None, optional
    if args and typing.get_origin(annotation) is not tuple:
        targets = tuple(
            f"{item.__module__}.{item.__qualname__}"
            for item in args
            if isinstance(item, type) and dataclasses.is_dataclass(item)
        )
        if len(targets) == len(args):
            return {"ref"}, targets, None, optional
        if len(args) == 1:
            return _annotation_binding(args[0])[:-1] + (optional,)
    if typing.get_origin(annotation) is tuple:
        element = typing.get_args(annotation)[0]
        elements = tuple(item for item in typing.get_args(element) if item is not type(None)) or (element,)
        targets = tuple(
            f"{item.__module__}.{item.__qualname__}"
            for item in elements
            if isinstance(item, type) and dataclasses.is_dataclass(item)
        )
        if len(targets) == len(elements):
            return {"ref_list"}, targets, None, optional
        if elements == (int,):
            return {"u64_list", "integer_list"}, (), None, optional
    if isinstance(annotation, type) and dataclasses.is_dataclass(annotation):
        return {"ref"}, (f"{annotation.__module__}.{annotation.__qualname__}",), None, optional
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return {"enum"}, (), f"{annotation.__module__}.{annotation.__qualname__}", optional
    return {
        int: {"u64", "i64"},
        bool: {"bool"},
        float: {"f64"},
        str: {"string"},
        bytes: {"bytes"},
    }[annotation], (), None, optional
def test_gate3_envelope_and_support_sections_are_schema_owned():
    schema = yaml.safe_load(SCHEMA_PATH.read_bytes())
    assert schema["abi"] == {"major": 1, "minor": 3, "min_reader_minor": 3}
    assert schema["required_features"] == {
        "SCHEDULED_SEMANTICS": {"bit": 1, "min_reader_minor": 3}
    }
    assert schema["enums"]["section_type"] == {
        **{name: value for name, value in schema["enums"]["section_type"].items() if value < 16 or value >= 101},
        "PROGRAM_METADATA": 16,
        "SEMANTIC_STRINGS": 17,
        "SEMANTIC_U64_VALUES": 18,
        "SEMANTIC_I64_VALUES": 19,
        "SEMANTIC_REFERENCES": 20,
        "SEMANTIC_BYTES": 21,
        "SEMANTIC_INTEGER_VALUES": 22,
        **{record["section_name"]: record["section_type"] for record in schema["semantic_records"].values()},
    }
    assert A.REQUIRED_FEATURE.SCHEDULED_SEMANTICS == 1
    assert A.REQUIRED_FEATURES == 1
    assert A.REQUIRED_FEATURE_MIN_READER_MINOR == {1: 3}
    assert A.JSON_SAFE_INTEGER_MAX == (1 << 53) - 1


def test_program_metadata_and_optional_record_layouts_are_exact():
    assert A.PROGRAM_METADATA_BYTES == 48
    assert A.PROGRAM_METADATA_FIELD_OFFSETS == {
        "min_reader_minor": 0,
        "flags": 2,
        "reserved": 4,
        "semantics": 8,
        "semantic_sha256": 16,
    }
    assert A.SOURCE_MAP_BYTES == 16
    assert A.PROFILE_HINTS_BYTES == 16
    assert A.CONTENT_DIGESTS_BYTES == 40


def test_generated_envelope_offsets_cover_every_schema_field():
    cpp = (ROOT / "src/dev/ai_mesh/generated/mesh_ir_abi.hh").read_text()
    for prefix, record in (("Header", SCHEMA["header"]), ("SectionDir", SCHEMA["section_dir"])):
        for field in record["fields"]:
            name = "".join(part.capitalize() for part in field["name"].split("_"))
            assert f"k{prefix}{name}Offset = {field['offset']};" in cpp
            assert f"k{prefix}{name}Bytes = {field_size(field)};" in cpp
    assert "kStringsBlobHeaderBytes = 4;" in cpp
    assert "kStringsBlobCountOffset = 0;" in cpp
    assert "kStringsDirectoryRecordBytes = 8;" in cpp
    assert "kSemanticStringsBlobHeaderBytes = 4;" in cpp
    assert f"std::array<RequiredSectionDescriptor, {len(A.REQUIRED_SECTIONS)}> kRequiredSections" in cpp
    assert f"std::array<uint16_t, {len(A.REQUIRED_SECTIONS)}> kRequiredSectionTypes" in cpp
    assert "constexpr bool validSectionType(uint16_t value)" in cpp
    assert tuple(A.REQUIRED_SECTION_TYPES) == tuple(
        A.SECTION_TYPE.__dict__[name] for name in A.REQUIRED_SECTIONS
    )
    assert set(A.SECTION_RECORD_BYTES) == {
        value
        for name, value in A.SECTION_TYPE.__dict__.items()
        if not name.startswith("_") and name not in {"STRINGS", "SEMANTIC_STRINGS"}
    }
    for name in ("STRINGS", "SEMANTIC_STRINGS"):
        assert getattr(A, f"{name}_BLOB_HEADER_FORMAT").format == "<I"
        assert getattr(A, f"{name}_BLOB_HEADER_BYTES") == 4
        assert getattr(A, f"{name}_DIRECTORY_FORMAT").format == "<II"
        assert getattr(A, f"{name}_DIRECTORY_RECORD_BYTES") == 8
    assert A.CANONICAL_ABI_FIELDS == {
        "major": {"program_field": "abi_major", "type": "u16"},
        "min_reader_minor": {"program_field": "min_reader_minor", "type": "u16"},
        "minor": {"program_field": "abi_minor", "type": "u16"},
        "required_features": {"program_field": "required_features", "type": "u64"},
    }
    projection = (ROOT / "src/dev/ai_mesh/generated/mesh_ir_transport_projection.hh").read_text()
    assert "visitProgramAbiFieldsCanonical" in projection
    for name, binding in A.CANONICAL_ABI_FIELDS.items():
        assert f'\"{name}\"' in projection
        assert binding["program_field"] in projection
    assert A.CONTENT_DIGEST_OBJECT_KIND == {
        "TENSOR": 1,
        "SHARD": 2,
        "ALLOCATION": 3,
        "KERNEL_OBJECT": 4,
    }
    assert A.NONSEMANTIC_FIELDS == {
        "COMMANDS": ("debug_loc_id",),
        "PROFILE_HINTS": ("entrypoint_id", "profile_id", "name", "value"),
        "SOURCE_MAP": ("loc_id", "file", "line", "column"),
    }


def test_semantic_record_tables_cover_live_closure_in_declared_order():
    pending = [ProgramSemantics]
    records = {}
    enums = set()
    while pending:
        cls = pending.pop()
        qualified_name = f"{cls.__module__}.{cls.__qualname__}"
        if qualified_name in records:
            continue
        hints = typing.get_type_hints(cls)
        records[qualified_name] = tuple(field.name for field in dataclasses.fields(cls))
        for annotation in hints.values():
            annotation_parts = [annotation]
            while annotation_parts:
                part = annotation_parts.pop()
                annotation_parts.extend(
                    item for item in typing.get_args(part) if item is not Ellipsis
                )
                if isinstance(part, type) and dataclasses.is_dataclass(part):
                    pending.append(part)
                elif isinstance(part, type) and issubclass(part, Enum):
                    enums.add(f"{part.__module__}.{part.__qualname__}")
    assert tuple(A.SEMANTIC_RECORDS) == tuple(sorted(records))
    assert tuple(A.SEMANTIC_ENUMS) == tuple(sorted(enums))
    assert len(A.SEMANTIC_RECORDS) == 97
    assert tuple(record["section_type"] for record in A.SEMANTIC_RECORDS.values()) == tuple(range(256, 353))
    assert len(A.SEMANTIC_RECORD_BY_SECTION) == 97
    for qualified_name, expected in records.items():
        binding = A.SEMANTIC_RECORDS[qualified_name]
        assert tuple(field["name"] for field in binding["fields"]) == expected
        assert binding["record_bytes"] == 8 + sum(field["wire_bytes"] for field in binding["fields"])
        module_name, class_name = qualified_name.rsplit(".", 1)
        cls = getattr(__import__(module_name, fromlist=(class_name,)), class_name)
        hints = typing.get_type_hints(cls)
        for index, field in enumerate(binding["fields"]):
            kinds, targets, enum, optional = _annotation_binding(hints[field["name"]])
            assert field["kind"] in kinds
            assert set(field.get("targets", ())) == set(targets)
            assert field.get("enum") == enum
            assert (field.get("optional_bit") == index) == optional
            assert field.get("json_union_discriminator", False) == (len(targets) > 1)


def test_semantic_enums_are_generated_once_and_reexported_with_dtype_behavior():
    from mesh_ir.generated import semantic_enums as generated
    from mesh_ir.ir.common import Access, DType
    from mesh_ir.ir.graph_ir import OpCode
    from mesh_ir.ir.kernel_ir import KernelOpcode
    from mesh_ir.scheduled.model import FenceScope
    from mesh_ir.traffic import TrafficDirection

    assert (Access, DType, OpCode, KernelOpcode, FenceScope, TrafficDirection) == (
        generated.Access,
        generated.DType,
        generated.OpCode,
        generated.KernelOpcode,
        generated.FenceScope,
        generated.TrafficDirection,
    )
    assert DType.FP16.byte_width == 2
    assert DType.BF16.accumulation is DType.FP32
    assert DType.FP32.machine_epsilon == 1.1920928955078125e-7


def test_semantic_wire_helpers_and_special_integer_domains_are_exact():
    assert A.SEMANTIC_REF_BYTES == 8
    assert A.LIST_SPAN_BYTES == 8
    assert A.STRING_REF_BYTES == 8
    assert A.SCALAR_VALUE_BYTES == 16
    assert A.INTEGER_VALUE_BYTES == 16
    assert {
        name: value for name, value in vars(A.SCALAR_KIND).items() if not name.startswith("_")
    } == {"BOOL": 1, "I64": 2, "U64": 3, "F64": 4}
    view = A.SEMANTIC_RECORDS["mesh_ir.ir.graph_ir.ViewAttrs"]
    fields = {field["name"]: field for field in view["fields"]}
    assert fields["starts"]["kind"] == "integer_list"
    assert fields["ends"]["kind"] == "integer_list"
    assert fields["steps"]["kind"] == "u64_list"
    elementwise = A.SEMANTIC_RECORDS["mesh_ir.ir.graph_ir.ElementwiseAttrs"]
    scalar = next(field for field in elementwise["fields"] if field["name"] == "scalar")
    assert scalar["kind"] == "scalar"
    assert scalar["optional_bit"] == 0
    assert scalar["wire_bytes"] == 16


def test_generated_cpp_semantic_interface_is_split_and_typed():
    entry = (ROOT / "src/dev/ai_mesh/generated/mesh_ir_abi.hh").read_text(encoding="utf-8")
    semantic = (ROOT / "src/dev/ai_mesh/generated/mesh_ir_semantic_abi.hh").read_text(encoding="utf-8")
    records = (ROOT / "src/dev/ai_mesh/generated/mesh_ir_semantic_records.hh").read_text(encoding="utf-8")
    codec_paths = sorted((ROOT / "src/dev/ai_mesh/generated").glob("mesh_ir_semantic_codecs_*.hh"))
    assert {path.name for path in codec_paths} == {
        "mesh_ir_semantic_codecs_analysis.hh",
        "mesh_ir_semantic_codecs_common.hh",
        "mesh_ir_semantic_codecs_graph.hh",
        "mesh_ir_semantic_codecs_kernel.hh",
        "mesh_ir_semantic_codecs_scheduled.hh",
        "mesh_ir_semantic_codecs_traffic.hh",
    }
    codecs = "".join(path.read_text(encoding="utf-8") for path in codec_paths)
    assert '#include "dev/ai_mesh/generated/mesh_ir_semantic_abi.hh"' in entry
    assert "struct SemanticRef" in semantic
    assert "struct ListSpan" in semantic
    assert "struct ScalarValue" in semantic
    assert "enum class WorkUnit" in semantic
    assert "struct ProgramSemantics" in records
    assert "decodeProgramSemantics(" in codecs
    assert "encodeProgramSemantics(" in codecs
    assert len(records.splitlines()) < 2000
    assert all(
        len(path.read_text(encoding="utf-8").splitlines()) < 2000
        for path in (ROOT / "src/dev/ai_mesh/generated").glob("mesh_ir_*.hh")
        if path.name != "mesh_diagnostics.hh"
    )


def test_generated_code_uses_catalog_diagnostics_and_closed_enum_switches():
    python_source = ROOT / "util/mesh_ir/mesh_ir/generated/abi.py"
    cpp_paths = (
        ROOT / "src/dev/ai_mesh/generated/mesh_ir_abi.hh",
        *sorted((ROOT / "src/dev/ai_mesh/generated").glob("mesh_ir_semantic_*.hh")),
        *sorted((ROOT / "src/dev/ai_mesh/generated").glob("mesh_ir_transport_*.hh")),
    )
    cpp = "".join(path.read_text(encoding="utf-8") for path in cpp_paths)
    assert "ENUM_MASKS" not in python_source.read_text(encoding="utf-8")
    assert "enumMember" not in cpp
    assert "ValuesMask" not in cpp
    assert "mesh_diagnostics::E_ABI_ENUM" in cpp
    assert "mesh_diagnostics::E_ABI_RESERVED" in cpp
    assert '"E_ABI_ENUM"' not in cpp
    assert '"E_ABI_RESERVED"' not in cpp
    assert "switch (value)" in cpp
    assert "encodeCommand(" in cpp
    assert "encodeDmaDescriptor(" in cpp
    assert "encodeOpAttr(" in cpp
    assert "out = ElementwiseAttrs{}" in cpp
    assert set(A.ENUM_ALLOWED_BITS) == {"stream_flags", "tensor_flags"}
    assert next(field for field in A.PROGRAM_CANONICAL_FIELDS if field["name"] == "semantic_sha256") == {
        "name": "semantic_sha256", "kind": "digest", "semantic": False
    }
    assert A.TRANSPORT_CANONICAL_SECTIONS["SOURCE_MAP"] == {
        "program_field": "source_map",
        "projection": "records",
        "optional": True,
        "semantic": False,
    }
    assert A.TRANSPORT_CANONICAL_FIELDS["COMMANDS"][-1]["semantic"] is False
    assert "struct SemanticTables" in cpp
    assert "visitSemanticTables" in cpp
    assert "dispatchSemanticTable" in cpp
    assert "visitSemanticFieldsWire" in cpp
    assert "visitSemanticFieldsCanonical" in cpp
    assert "SemanticEnumTraits" in cpp
    assert "struct TransportTables" in cpp
    assert "struct TypedOpAttr" in cpp
    assert "visitTransportTablesWire" in cpp
    assert "visitTransportTablesCanonical" in cpp
    assert "dispatchTransportTable" in cpp
    assert "TransportRecordTraits" in cpp
    assert "decodeTypedOpAttr" in cpp
    assert "encodeTypedOpAttr" in cpp
    assert "enum class TensorFlags : uint32_t" in cpp
    assert "enum class StreamFlags : uint16_t" in cpp
    assert "kScalarValuePayloadOffset" in cpp
    assert "kProgramMetadataSemanticSha256Offset" in cpp
    assert "kScalarKindBool" in cpp
    assert "//" not in cpp and "/*" not in cpp
    with python_source.open("rb") as handle:
        assert all(token.type != tokenize.COMMENT for token in tokenize.tokenize(handle.readline))


def test_schema_admission_rejects_ambiguous_and_unrepresentable_inputs(tmp_path):
    duplicate = tmp_path / "duplicate.yaml"
    duplicate.write_bytes(SCHEMA_PATH.read_bytes() + b"\nabi: {major: 1}\n")
    with pytest.raises(ValueError, match="duplicate YAML key"):
        load_schema(duplicate)

    schema = yaml.safe_load(SCHEMA_PATH.read_bytes())
    mutations = []
    feature = copy.deepcopy(schema)
    feature["required_features"]["SCHEDULED_SEMANTICS"]["bit"] = 1 << 64
    mutations.append(feature)
    enum = copy.deepcopy(schema)
    enum["semantic_enums"]["mesh_ir.analysis.cost.WorkUnit"]["members"][0]["wire"] = 1 << 32
    mutations.append(enum)
    identifier = copy.deepcopy(schema)
    identifier["semantic_records"]["mesh_ir.analysis.cost.WorkEstimate"]["json_tag"] = "bad-tag"
    mutations.append(identifier)
    width = copy.deepcopy(schema)
    width["semantic_records"]["mesh_ir.analysis.cost.WorkEstimate"]["fields"][0]["wire_bytes"] = 16
    mutations.append(width)
    duplicate_field = copy.deepcopy(schema)
    fields = duplicate_field["semantic_records"]["mesh_ir.analysis.cost.WorkEstimate"]["fields"]
    fields[1]["name"] = fields[0]["name"]
    mutations.append(duplicate_field)
    optional = copy.deepcopy(schema)
    optional["semantic_records"]["mesh_ir.ir.graph_ir.ElementwiseAttrs"]["fields"][1]["optional_bit"] = 64
    mutations.append(optional)
    section = copy.deepcopy(schema)
    section["semantic_records"]["mesh_ir.analysis.cost.WorkEstimate"]["section_type"] = 1 << 16
    mutations.append(section)
    abi_bool = copy.deepcopy(schema)
    abi_bool["abi"]["minor"] = True
    mutations.append(abi_bool)
    abi_width = copy.deepcopy(schema)
    abi_width["abi"]["min_reader_minor"] = 1 << 16
    mutations.append(abi_width)
    feature_minor = copy.deepcopy(schema)
    feature_minor["required_features"]["SCHEDULED_SEMANTICS"]["min_reader_minor"] = "3"
    mutations.append(feature_minor)
    record_bytes = copy.deepcopy(schema)
    record_bytes["records"]["ENTRYPOINTS"]["bytes"] = True
    mutations.append(record_bytes)
    field_offset = copy.deepcopy(schema)
    field_offset["records"]["ENTRYPOINTS"]["fields"][0]["offset"] = "0"
    mutations.append(field_offset)
    enum_width = copy.deepcopy(schema)
    enum_width["enums"]["stream_flags"]["IS_LIFECYCLE"] = 1 << 16
    mutations.append(enum_width)
    cpp_collision = copy.deepcopy(schema)
    cpp_collision["records"]["ENTRYPOINTS"]["fields"][0]["name"] = "namespace"
    cpp_collision["records"]["ENTRYPOINTS"]["fields"][1]["name"] = "namespace_"
    mutations.append(cpp_collision)
    python_base = copy.deepcopy(schema)
    python_base["semantic_enums"]["mesh_ir.analysis.cost.WorkUnit"]["python_base"] = "object"
    mutations.append(python_base)
    python_type = copy.deepcopy(schema)
    python_type["semantic_enums"]["mesh_ir.analysis.cost.WorkUnit"]["members"][0]["python"] = 1
    mutations.append(python_type)
    python_duplicate = copy.deepcopy(schema)
    python_duplicate["semantic_enums"]["mesh_ir.analysis.cost.WorkUnit"]["members"][1]["python"] = "MAC"
    mutations.append(python_duplicate)
    for index, candidate in enumerate(mutations):
        path = tmp_path / f"invalid-{index}.yaml"
        path.write_text(yaml.safe_dump(candidate, sort_keys=False), encoding="utf-8")
        with pytest.raises(ValueError):
            load_schema(path)


def test_semantic_enum_python_strings_are_emitted_as_bounded_utf8_literals(tmp_path):
    schema = yaml.safe_load(SCHEMA_PATH.read_bytes())
    schema["semantic_enums"]["mesh_ir.analysis.cost.WorkUnit"]["members"][0]["python"] = "A\n雪\0F"
    path = tmp_path / "unicode.yaml"
    path.write_text(yaml.safe_dump(schema, sort_keys=False), encoding="utf-8")
    admitted = load_schema(path)
    source = render_semantic_cpp_enum_traits(admitted)
    assert "A\n雪\0F" not in source
    assert "std::string_view{" in source
    assert "\\x00" in source and "\\xe9" in source


def test_generation_is_idempotent_from_an_unrelated_directory(tmp_path):
    generated = tmp_path / "generated"
    generated.mkdir()
    command = [
        sys.executable,
        str(ROOT / "util/mesh_ir/mesh_ir/abi/generate_abi.py"),
        "--yaml",
        str(SCHEMA_PATH),
        "--python-out",
        str(tmp_path / "abi.py"),
        "--cpp-out",
        str(generated / "mesh_ir_abi.hh"),
        "--docs-out",
        str(tmp_path / "mesh_ir_abi.md"),
    ]
    subprocess.run(command, cwd=tmp_path, check=True, capture_output=True, text=True)
    assert (tmp_path / "abi.py").read_bytes() == (
        ROOT / "util/mesh_ir/mesh_ir/generated/abi.py"
    ).read_bytes()
    assert (tmp_path / "mesh_ir_abi.md").read_bytes() == (
        ROOT / "docs/generated/mesh_ir_abi.md"
    ).read_bytes()
    for actual in generated.iterdir():
        assert actual.read_bytes() == (
            ROOT / "src/dev/ai_mesh/generated" / actual.name
        ).read_bytes()
    subprocess.run([*command, "--check"], cwd=tmp_path, check=True, capture_output=True, text=True)
    obsolete = generated / "mesh_ir_semantic_codecs_obsolete.hh"
    obsolete.write_text("stale", encoding="utf-8")
    assert subprocess.run([*command, "--check"], cwd=tmp_path, capture_output=True, text=True).returncode == 1
    subprocess.run(command, cwd=tmp_path, check=True, capture_output=True, text=True)
    assert not obsolete.exists()


def test_generated_cpp_semantic_records_compile_and_round_trip(tmp_path):
    source = tmp_path / "semantic.cc"
    binary = tmp_path / "semantic"
    source.write_text(
        '#include "dev/ai_mesh/generated/mesh_ir_abi.hh"\n'
        "int main() {\n"
        "using namespace gem5::ai_mesh::mesh_abi::semantic_abi;\n"
        "ControlExecution marker{};\n"
        "auto bytes = encodeControlExecution(marker);\n"
        "gem5::ai_mesh::mesh_abi::AbiError error{};\n"
        "ControlExecution decoded{};\n"
        "if (!decodeControlExecution(bytes.data(), decoded, error)) return 1;\n"
        "SemanticIntegerValue integer{2, ~uint64_t(0)};\n"
        "auto integer_bytes = encodeSemanticIntegerValue(integer);\n"
        "SemanticIntegerValue decoded_integer{};\n"
        "if (!decodeSemanticIntegerValue(integer_bytes.data(), decoded_integer, error)) return 2;\n"
        "std::array<uint8_t, 8> dirty{}; dirty.fill(0xff);\n"
        "encodeSemanticRef(dirty.data(), SemanticRef{330, 1});\n"
        "if (dirty[2] != 0 || dirty[3] != 0) return 3;\n"
        "dirty.fill(0xff); encodeStringRef(dirty.data(), StringRef{1});\n"
        "if (dirty[4] != 0 || dirty[5] != 0 || dirty[6] != 0 || dirty[7] != 0) return 4;\n"
        "auto bad_string = dirty; bad_string[4] = 1; StringRef string_ref{}; error = {};\n"
        "if (decodeStringRef(bad_string.data(), string_ref, error) || error.code != gem5::ai_mesh::mesh_diagnostics::E_ABI_RESERVED) return 5;\n"
        "bad_string.fill(0); error = {}; if (decodeStringRef(bad_string.data(), string_ref, error) || error.code != gem5::ai_mesh::mesh_diagnostics::E_ABI_BOUNDS) return 6;\n"
        "auto bad_integer = integer_bytes; bad_integer[2] = 1; error = {};\n"
        "if (decodeSemanticIntegerValue(bad_integer.data(), decoded_integer, error) || error.code != gem5::ai_mesh::mesh_diagnostics::E_ABI_RESERVED) return 7;\n"
        "bad_integer = integer_bytes; bad_integer[0] = 9; error = {};\n"
        "if (decodeSemanticIntegerValue(bad_integer.data(), decoded_integer, error) || error.code != gem5::ai_mesh::mesh_diagnostics::E_ABI_ENUM) return 8;\n"
        "ElementwiseAttrs elementwise{}; elementwise.approximation.string_id = 1; elementwise.scalar_side.string_id = 1;\n"
        "auto elementwise_bytes = encodeElementwiseAttrs(elementwise);\n"
        "ElementwiseAttrs reused{}; reused.scalar.kind = 4; reused.scalar.payload = 7;\n"
        "if (!decodeElementwiseAttrs(elementwise_bytes.data(), reused, error) || reused.scalar.kind != 0) return 9;\n"
        "SemanticTables tables{}; tables.control_execution_rows.emplace_back();\n"
        "size_t table_count = 0; if (!visitSemanticTables(tables, [&](auto, auto &) { ++table_count; return true; })) return 10;\n"
        "const auto &const_tables = tables; size_t selected = 0;\n"
        "if (!dispatchSemanticTable(314, const_tables, [&](auto, const auto &rows) { selected = rows.size(); return true; }) || selected != 1) return 11;\n"
        "size_t field_count = 0; if (!visitSemanticFieldsCanonical(reused, [&](auto, bool, const auto &) { ++field_count; return true; }) || field_count != 4) return 12;\n"
        "auto python_value = SemanticEnumTraits<WorkUnit>::pythonValue(WorkUnit::MAC);\n"
        "if (python_value.kind != SemanticEnumPythonKind::String || python_value.string_value != \"MAC\") return 13;\n"
        "gem5::ai_mesh::mesh_abi::Command command{}; command.engine = static_cast<uint16_t>(gem5::ai_mesh::mesh_abi::Engine::CONTROL); command.opcode = static_cast<uint16_t>(gem5::ai_mesh::mesh_abi::Opcode::REQUEST_BEGIN);\n"
        "auto command_bytes = gem5::ai_mesh::mesh_abi::encodeCommand(command); gem5::ai_mesh::mesh_abi::Command decoded_command{};\n"
        "if (!gem5::ai_mesh::mesh_abi::decodeCommand(command_bytes.data(), decoded_command, error)) return 14;\n"
        "using namespace gem5::ai_mesh::mesh_abi;\n"
        "if (kProgramCanonicalFields.front().name != \"abi\" || kProgramCanonicalFields.back().name != \"semantics\") return 15;\n"
        "if (kTransportCanonicalSections.front().name != \"ALLOCATIONS\" || kTransportCanonicalSections.back().name != \"TENSORS\") return 16;\n"
        "AttrPayload wrong = GemmV1{}; size_t wrong_fields = 0;\n"
        "if (visitAttrPayloadCanonical(kAttrKindBMM_V1, wrong, CanonicalProjection::Full, [&](auto, const auto &) { ++wrong_fields; return true; }) || wrong_fields != 0) return 17;\n"
        "TypedOpAttr typed{kAttrKindREPEAT_V1, RepeatV1{}}; std::array<uint8_t, kOpAttrsBytes> typed_bytes{};\n"
        "if (typed.as<RepeatV1>() == nullptr || !encodeTypedOpAttr(typed, typed_bytes, error)) return 18;\n"
        "TypedOpAttr decoded_typed{}; if (!decodeTypedOpAttr(typed_bytes.data(), decoded_typed, error) || decoded_typed.as<RepeatV1>() == nullptr) return 19;\n"
        "typed.kind = kAttrKindBMM_V1; if (encodeTypedOpAttr(typed, typed_bytes, error)) return 20;\n"
        "TransportTables transport{}; transport.op_attrs.push_back(decoded_typed); size_t wire_tables = 0;\n"
        "if (!visitTransportTablesWire(transport, [&](auto, auto &) { ++wire_tables; return true; }) || wire_tables != kTransportCanonicalSections.size()) return 21;\n"
        "size_t canonical_tables = 0; if (!visitTransportTablesCanonical(transport, CanonicalProjection::Full, [&](auto, auto &) { ++canonical_tables; return true; }) || canonical_tables + 3 != kTransportCanonicalSections.size()) return 22;\n"
        "const auto &const_transport = transport; size_t dispatched = 0;\n"
        "if (!dispatchTransportTable(kSectionTypeOP_ATTRS, const_transport, [&](auto, const auto &rows) { dispatched = rows.size(); return true; }) || dispatched != 1) return 23;\n"
        "if (!validSectionType(kSectionTypeSTRINGS) || !validSectionType(kSectionTypeSEMANTIC_TRAFFIC_REPORT) || validSectionType(0) || validSectionType(0xffff)) return 24;\n"
        "return integer.kind == 2 && integer.payload == ~uint64_t(0) ? 0 : 25;\n"
        "}\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-pedantic-errors", "-I", str(ROOT / "src"), str(source), "-o", str(binary)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run([str(binary)], check=True, capture_output=True, text=True)
