"""Slice E section 5: the behaviour matrix pairs the Python and C++ verdicts per input."""

import json

import pytest

from mesh_ir.architecture import load_arch
from mesh_ir.golden_programs import build_dma_pin_program
from mesh_ir.golden_runtime_programs import build_p2p_multi_descriptor_program
from mesh_ir.ir.common import Access, INVALID_CORE_ID
from mesh_ir.model import MeshIrError
from mesh_ir.scheduled.addressing import bind_invocation
from mesh_ir.scheduled.verify import verify_program
from mesh_ir.traffic import Binding

from tests.integration.support.runtime_harness import (
    ARCH,
    REPO,
    build_program,
    run_mock,
)

ARCH_2X2 = REPO / "configs/example/ai_mesh/arch/mesh_2x2.yaml"
PROGRAM = "p2p_multi_descriptor"
BINDING_PROGRAM = "dma_pin"

VALID = [
    {
        "slot_id": 1,
        "region_id": 0,
        "owner_core": INVALID_CORE_ID,
        "allocation_offset_bytes": 0x200000,
        "allocation_size_bytes": 128,
        "allocation_alignment_bytes": 64,
        "access": 2,
    },
    {
        "slot_id": 2,
        "region_id": 0,
        "owner_core": INVALID_CORE_ID,
        "allocation_offset_bytes": 0x200100,
        "allocation_size_bytes": 128,
        "allocation_alignment_bytes": 64,
        "access": 2,
    },
]
EXTRA_SLOT = VALID + [
    {
        "slot_id": 3,
        "region_id": 0,
        "owner_core": INVALID_CORE_ID,
        "allocation_offset_bytes": 0x200200,
        "allocation_size_bytes": 128,
        "allocation_alignment_bytes": 64,
        "access": 2,
    }
]
MISSING_SLOT = VALID[:1]
ALIGNMENT_METADATA = [dict(VALID[0], allocation_alignment_bytes=3), VALID[1]]
ADDRESS_MISALIGNMENT = [dict(VALID[0], allocation_offset_bytes=0x200020), VALID[1]]
OVERLAP = [
    dict(VALID[0]),
    dict(VALID[1], allocation_offset_bytes=0x200000),
]

def _records(program_dir):
    path = program_dir / "runtime_diagnostics.jsonl"
    assert path.is_file(), f"missing runtime diagnostic outlet at {path}"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _python_resolver_verdict(entries):
    arch = load_arch(ARCH)
    program = build_dma_pin_program(arch)
    variant = program.semantics.variants[0]
    bindings = tuple(
        Binding(
            entry["slot_id"],
            entry["region_id"],
            entry["owner_core"],
            entry["allocation_offset_bytes"],
            entry["allocation_size_bytes"],
            entry["allocation_alignment_bytes"],
            Access(entry["access"]),
        )
        for entry in entries
    )
    try:
        bind_invocation(
            program, arch, variant.entrypoint_id, variant.profile_id, bindings
        )
    except MeshIrError as error:
        return error.code
    return "ACCEPTED"


def test_architecture_core_count_change_is_rejected_by_both_sides(tmp_path):
    arch = load_arch(ARCH)
    other = load_arch(ARCH_2X2)
    assert len(other.core_ids) != len(arch.core_ids), other.core_ids
    program = build_p2p_multi_descriptor_program(arch)
    try:
        verify_program(program, other)
        python_verdict = "ACCEPTED"
    except MeshIrError as error:
        python_verdict = error.code
    assert python_verdict == "E_ARCH_DIGEST", python_verdict
    verified = verify_program(program, arch)
    assert verified.program is program

    program_dir = build_program(tmp_path, PROGRAM)
    manifest = json.loads((program_dir / "manifest.json").read_text())
    assert program.semantic_sha256 == manifest["program_semantic_sha256"], manifest
    assert manifest["arch_digest"] == arch.digest().hex(), manifest
    run = run_mock(
        tmp_path, "m5out", PROGRAM, program_dir, ("--arch", str(ARCH_2X2))
    )
    output = run.stdout + run.stderr
    assert run.returncode != 0, output
    rows = _records(program_dir)
    load = [row for row in rows if row["stage"] == "load"]
    assert len(load) == 1, load
    assert load[0]["code"] == python_verdict, load[0]
    assert load[0]["context"]["install_state"] == "pre_install", load[0]
    assert load[0]["context"]["installed_cores"] == "0", load[0]
    assert load[0]["context"]["cores"] == str(len(other.core_ids)), load[0]
    assert not (program_dir / "actual_result.json").exists()


@pytest.mark.parametrize(
    ("entries", "expected_detail"),
    [
        (EXTRA_SLOT, "cover every slot"),
        (MISSING_SLOT, "cover every slot"),
        (ALIGNMENT_METADATA, "alignment"),
        (ADDRESS_MISALIGNMENT, "alignment"),
        (OVERLAP, "overlap"),
    ],
    ids=(
        "unknown-extra-slot",
        "missing-slot",
        "alignment-metadata",
        "address-misalignment",
        "writable-overlap",
    ),
)
def test_invocation_binding_mutation_pairs_with_the_python_resolver(
    tmp_path, entries, expected_detail
):
    python_verdict = _python_resolver_verdict(entries)
    assert python_verdict == "E_RELOCATION", python_verdict
    program_dir = build_program(tmp_path, BINDING_PROGRAM)
    path = tmp_path / "bad.bindings.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    run = run_mock(
        tmp_path,
        "m5out",
        BINDING_PROGRAM,
        program_dir,
        ("--bindings-file", str(path)),
    )
    output = run.stdout + run.stderr
    assert run.returncode != 0, output
    rows = _records(program_dir)
    invocation = [row for row in rows if row["stage"] == "invocation"]
    assert len(invocation) == 1, rows
    record = invocation[0]
    assert record["code"] == python_verdict, record
    assert expected_detail in record["context"]["detail"], record
    assert record["context"]["install_state"] == "pre_install", record
    assert record["context"]["installed_cores"] == "0", record
    assert record["context"]["cores"] == "2", record
    assert not (program_dir / "actual_result.json").exists()


def test_reference_default_bindings_pair_through_the_adapter(tmp_path):
    arch = load_arch(ARCH)
    program = build_dma_pin_program(arch)
    variant = program.semantics.variants[0]
    reference = tuple(
        slot.reference_binding
        for slot in sorted(program.semantics.binding_slots, key=lambda s: s.slot_id)
    )
    assert len(reference) == 2, reference
    bound = bind_invocation(
        program, arch, variant.entrypoint_id, variant.profile_id, reference
    )
    assert bound is not None
    program_dir = build_program(tmp_path, BINDING_PROGRAM)
    run = run_mock(tmp_path, "m5out", BINDING_PROGRAM, program_dir)
    output = run.stdout + run.stderr
    assert run.returncode == 0, output
    assert "MESH_PROGRAM_DONE" in output, output
    result = json.loads((program_dir / "actual_result.json").read_text())
    assert all(core["commands_errored"] == 0 for core in result["cores"]), result["cores"]
