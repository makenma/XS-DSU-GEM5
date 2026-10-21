import dataclasses
import os
import subprocess
from pathlib import Path

import pytest

from mesh_ir.abi.decoder import decode_program
from mesh_ir.abi.encoder import encode_program
from mesh_ir.architecture import load_arch
from mesh_ir.canonical import semantic_sha256
from mesh_ir.generated import abi as A
from mesh_ir.golden_programs import (
    build_dual_core_program,
    build_repeat_program,
    build_single_core_program,
    build_zero_dma_program,
)
from mesh_ir.model import ContentDigest, ProfileHint, SourceMap
from mesh_ir.scheduled.verify import verify_program
from tests.golden.test_mutation_corpus import _write_arch_facts


ROOT = Path(__file__).resolve().parents[4]
ARCH = ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml"


@pytest.fixture(scope="module")
def arch():
    return load_arch(ARCH)


def _driver_result(driver, *paths):
    environment = os.environ.copy()
    environment["ASAN_OPTIONS"] = "detect_leaks=0"
    return subprocess.run(
        [driver, *(str(path) for path in paths)],
        capture_output=True,
        env=environment,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize("case", ("single", "dual", "repeat", "zero", "optional"))
def test_cpp_complete_authored_program_inverse_has_exact_outputs(
    case, driver, tmp_path, arch,
):
    program = {
        "single": build_single_core_program,
        "dual": build_dual_core_program,
        "repeat": build_repeat_program,
        "zero": build_zero_dma_program,
        "optional": build_single_core_program,
    }[case](arch)
    if case == "optional":
        program = dataclasses.replace(
            program,
            source_map=(SourceMap(1, "model.py", 7, 3),),
            profile_hints=(
                ProfileHint(
                    program.entrypoints[0].entrypoint_id,
                    program.profiles[0].profile_id,
                    "mode",
                    "fast",
                ),
            ),
            content_digests=(
                ContentDigest(
                    A.CONTENT_DIGEST_OBJECT_KIND["TENSOR"],
                    0,
                    program.tensors[0].tensor_id,
                    bytes.fromhex("11" * 32),
                ),
            ),
        )
        program = dataclasses.replace(
            program,
            semantic_sha256=semantic_sha256(program.semantic_dict()),
        )
    image = encode_program(program)
    verify_program(decode_program(image), arch)
    source = tmp_path / "program.mshb"
    source.write_bytes(image)
    facts = tmp_path / "arch_facts.txt"
    _write_arch_facts(facts, arch)
    reencoded = tmp_path / "reencoded.mshb"
    canonical = tmp_path / "canonical.json"
    result = _driver_result(driver, source, facts, reencoded, canonical)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "ACCEPTED\n"
    assert result.stderr == ""
    assert reencoded.read_bytes() == image
    assert canonical.read_bytes() == program.canonical_bytes()
    verify_program(decode_program(reencoded.read_bytes()), arch)
    assert source.read_bytes() == image


@pytest.mark.parametrize(
    "case",
    ("missing", "malformed_numeric", "trailing", "duplicate_region"),
)
def test_cpp_complete_inverse_rejects_malformed_independent_facts(
    case, driver, tmp_path, arch,
):
    image = encode_program(build_single_core_program(arch))
    source = tmp_path / "program.mshb"
    source.write_bytes(image)
    facts = tmp_path / "arch_facts.txt"
    _write_arch_facts(facts, arch)
    original = facts.read_text(encoding="utf-8")
    lines = original.splitlines()
    if case == "missing":
        facts.write_text("", encoding="utf-8")
    elif case == "malformed_numeric":
        lines[1] = "not-a-number"
        facts.write_text("\n".join(lines) + "\n", encoding="utf-8")
    elif case == "trailing":
        facts.write_text(original + "trailing\n", encoding="utf-8")
    else:
        facts.write_text(original + lines[8] + "\n", encoding="utf-8")
    reencoded = tmp_path / "reencoded.mshb"
    canonical = tmp_path / "canonical.json"
    result = _driver_result(driver, source, facts)
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == ""
    assert not reencoded.exists()
    assert not canonical.exists()


@pytest.mark.parametrize(
    ("case", "expected"),
    (
        ("accepted", "ACCEPTED"),
        ("decode", "DECODE:E_ABI_MAGIC"),
        ("architecture", "VERIFY:E_ARCH_DIGEST"),
    ),
)
def test_cpp_complete_inverse_preserves_legacy_driver_contract(
    case, expected, driver, tmp_path, arch,
):
    image = bytearray(encode_program(build_single_core_program(arch)))
    facts = tmp_path / "arch_facts.txt"
    _write_arch_facts(facts, arch)
    if case == "decode":
        image[0] ^= 1
    elif case == "architecture":
        original = facts.read_text(encoding="utf-8")
        first = "0" if original[0] != "0" else "1"
        facts.write_text(first + original[1:], encoding="utf-8")
    source = tmp_path / "program.mshb"
    source.write_bytes(image)
    result = _driver_result(driver, source, facts)
    assert result.returncode == 0
    assert result.stdout == expected + "\n"
    assert result.stderr == ""


def test_cpp_complete_inverse_refuses_preexisting_outputs(driver, tmp_path, arch):
    source = tmp_path / "program.mshb"
    source.write_bytes(encode_program(build_single_core_program(arch)))
    facts = tmp_path / "arch_facts.txt"
    _write_arch_facts(facts, arch)
    reencoded = tmp_path / "reencoded.mshb"
    canonical = tmp_path / "canonical.json"
    reencoded.write_bytes(b"reencoded")
    canonical.write_bytes(b"canonical")
    result = _driver_result(driver, source, facts, reencoded, canonical)
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == ""
    assert reencoded.read_bytes() == b"reencoded"
    assert canonical.read_bytes() == b"canonical"
