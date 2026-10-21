import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from mesh_ir.abi.generate_diagnostics import CatalogError, load_catalog
from mesh_ir.diagnostics import DIAGNOSTICS


ROOT = Path(__file__).resolve().parents[4]
GENERATOR = ROOT / "util/mesh_ir/mesh_ir/abi/generate_diagnostics.py"
CATALOG = ROOT / "util/mesh_ir/mesh_ir/diagnostics.yaml"
HEADER = ROOT / "src/dev/ai_mesh/generated/mesh_diagnostics.hh"


def _run_generator(
    *arguments: object,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(GENERATOR),
            *(str(argument) for argument in arguments),
        ],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


def _write_catalog(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_generated_catalog_matches_python_catalog_through_cpp(tmp_path):
    source = tmp_path / "diagnostics_probe.cc"
    executable = tmp_path / "diagnostics_probe"
    source.write_text(
        """
#include <iostream>
#include <string_view>

#include "dev/ai_mesh/generated/mesh_diagnostics.hh"

int
main()
{
    using namespace gem5::ai_mesh::mesh_diagnostics;
    static_assert(std::string_view(E_ABI_ENUM) == "E_ABI_ENUM");
    static_assert(findDefinition(DiagnosticCode::E_ABI_ENUM) != nullptr);
    static_assert(
        findDefinition(std::string_view("E_NOT_REGISTERED")) == nullptr);
    static_assert(
        findDefinition(static_cast<DiagnosticCode>(65535)) == nullptr);
    for (const auto &definition : kDefinitions) {
        const auto severity = definition.severity == Severity::Error
            ? "error" : "warning";
        std::cout << definition.code << '\\0' << severity << '\\0'
                  << definition.message << '\\0';
    }
    return 0;
}
""".lstrip(),
        encoding="utf-8",
    )
    compile_result = subprocess.run(
        [
            "g++",
            "-std=c++17",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-pedantic-errors",
            "-I",
            str(ROOT / "src"),
            str(source),
            "-o",
            str(executable),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert compile_result.returncode == 0, compile_result.stderr
    probe = subprocess.run(
        [str(executable)],
        check=False,
        capture_output=True,
    )
    assert probe.returncode == 0, probe.stderr.decode(
        "utf-8",
        errors="replace",
    )
    fields = probe.stdout.split(b"\0")
    assert fields[-1] == b""
    actual = [
        tuple(field.decode("utf-8") for field in fields[index : index + 3])
        for index in range(0, len(fields) - 1, 3)
    ]
    expected = [
        (
            code,
            definition.severity.value,
            definition.message,
        )
        for code, definition in DIAGNOSTICS.items()
    ]
    assert actual == expected
    header = HEADER.read_text(encoding="utf-8")
    assert hashlib.sha256(CATALOG.read_bytes()).hexdigest() in header
    assert "//" not in header
    assert "/*" not in header


def test_regeneration_is_deterministic_and_check_is_cwd_independent(tmp_path):
    first = tmp_path / "first.hh"
    second = tmp_path / "second.hh"
    for output in (first, second):
        result = _run_generator("--cpp-out", output, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
    assert first.read_bytes() == second.read_bytes() == HEADER.read_bytes()
    checked = _run_generator("--check", cwd=tmp_path)
    assert checked.returncode == 0, checked.stderr
    first.write_text("stale", encoding="utf-8")
    stale = _run_generator("--cpp-out", first, "--check", cwd=tmp_path)
    assert stale.returncode == 1
    assert str(first) in stale.stderr


def test_duplicate_yaml_mapping_key_is_rejected(tmp_path):
    catalog = _write_catalog(
        tmp_path / "duplicate.yaml",
        """schema_version: mesh-diagnostics-v1
diagnostics:
  E_DUPLICATE: {severity: error, message: first}
  E_DUPLICATE: {severity: warning, message: second}
""",
    )
    result = _run_generator(
        "--yaml",
        catalog,
        "--cpp-out",
        tmp_path / "out.hh",
    )
    assert result.returncode == 2
    assert "duplicate mapping key" in result.stderr


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[]\n", "catalog root must be a mapping"),
        (
            "schema_version: mesh-diagnostics-v2\ndiagnostics: {}\n",
            "schema_version must equal",
        ),
        (
            "schema_version: mesh-diagnostics-v1\ndiagnostics: []\n",
            "diagnostics must be a nonempty mapping",
        ),
        (
            "schema_version: mesh-diagnostics-v1\n"
            "diagnostics: {1: {severity: error, message: bad}}\n",
            "diagnostic code must be a string",
        ),
        (
            "schema_version: mesh-diagnostics-v1\n"
            "diagnostics: {E-BAD: {severity: error, message: bad}}\n",
            "diagnostic code 'E-BAD' is invalid",
        ),
        (
            "schema_version: mesh-diagnostics-v1\n"
            "diagnostics: {E_BAD: []}\n",
            "definition must be a mapping",
        ),
        (
            "schema_version: mesh-diagnostics-v1\n"
            "diagnostics: {E_BAD: {severity: fatal, message: bad}}\n",
            "severity must be error or warning",
        ),
        (
            "schema_version: mesh-diagnostics-v1\n"
            "diagnostics: {E_BAD: {severity: error, message: 7}}\n",
            "message must be a nonempty string",
        ),
        (
            "schema_version: mesh-diagnostics-v1\n"
            "diagnostics: "
            "{E_BAD: {severity: error, message: bad, extra: 1}}\n",
            "definition keys must be severity and message",
        ),
    ],
)
def test_invalid_catalog_is_rejected(tmp_path, text, message):
    catalog = _write_catalog(tmp_path / "invalid.yaml", text)
    result = _run_generator(
        "--yaml",
        catalog,
        "--cpp-out",
        tmp_path / "out.hh",
    )
    assert result.returncode == 2
    assert message in result.stderr


def test_invalid_code_is_not_normalized_to_a_cpp_identity(tmp_path):
    catalog = _write_catalog(
        tmp_path / "identity.yaml",
        """schema_version: mesh-diagnostics-v1
diagnostics:
  E_NAME-WITH-DASH: {severity: error, message: first}
  E_NAME_WITH_DASH: {severity: error, message: second}
""",
    )
    result = _run_generator(
        "--yaml",
        catalog,
        "--cpp-out",
        tmp_path / "out.hh",
    )
    assert result.returncode == 2
    assert "diagnostic code 'E_NAME-WITH-DASH' is invalid" in result.stderr


def test_surrogate_message_is_rejected_with_a_controlled_error(tmp_path):
    catalog = _write_catalog(
        tmp_path / "surrogate.yaml",
        "schema_version: mesh-diagnostics-v1\n"
        "diagnostics:\n"
        '  E_SURROGATE: {severity: error, message: "\\uD800"}\n',
    )
    result = _run_generator(
        "--yaml",
        catalog,
        "--cpp-out",
        tmp_path / "out.hh",
    )
    assert result.returncode == 2
    assert "E_SURROGATE message must be valid UTF-8" in result.stderr
    assert "Traceback" not in result.stderr


def test_diagnostic_code_count_honors_uint16_boundary():
    def catalog(count):
        entries = [
            "  E_CODE_00000: &definition "
            "{severity: error, message: valid}"
        ]
        entries.extend(
            f"  E_CODE_{index:05d}: *definition"
            for index in range(1, count)
        )
        return (
            "schema_version: mesh-diagnostics-v1\n"
            "diagnostics:\n"
            + "\n".join(entries)
            + "\n"
        ).encode("utf-8")

    assert len(load_catalog(catalog(1 << 16))) == 1 << 16
    with pytest.raises(CatalogError, match="uint16_t enum capacity"):
        load_catalog(catalog((1 << 16) + 1))


def test_special_utf8_message_roundtrips_through_typed_cpp_definition(
    tmp_path,
):
    message = '警告 "quoted" \\ path\nnext line'
    catalog = _write_catalog(
        tmp_path / "warning.yaml",
        """schema_version: mesh-diagnostics-v1
diagnostics:
  E_WARNING:
    severity: warning
    message: |-
      警告 "quoted" \\ path
      next line
""",
    )
    header = tmp_path / "warning.hh"
    generated = _run_generator(
        "--yaml",
        catalog,
        "--cpp-out",
        header,
    )
    assert generated.returncode == 0, generated.stderr
    source = tmp_path / "warning.cc"
    source.write_text(
        """
#include <iostream>
#include <string_view>

#include "warning.hh"

using namespace gem5::ai_mesh::mesh_diagnostics;
static_assert(kDefinitions.size() == 1);
static_assert(kDefinitions[0].id == DiagnosticCode::E_WARNING);
static_assert(kDefinitions[0].severity == Severity::Warning);
static_assert(kDefinitions[0].code == std::string_view(E_WARNING));

int
main()
{
    const auto message = kDefinitions[0].message;
    std::cout.write(
        message.data(), static_cast<std::streamsize>(message.size()));
    return 0;
}
""".lstrip(),
        encoding="utf-8",
    )
    compiled = subprocess.run(
        [
            "g++",
            "-std=c++17",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-pedantic-errors",
            "-I",
            str(tmp_path),
            str(source),
            "-o",
            str(tmp_path / "warning"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert compiled.returncode == 0, compiled.stderr
    probe = subprocess.run(
        [str(tmp_path / "warning")],
        check=False,
        capture_output=True,
    )
    assert probe.returncode == 0
    assert probe.stdout == message.encode("utf-8")
