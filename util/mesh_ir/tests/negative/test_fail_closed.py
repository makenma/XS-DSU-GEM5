from dataclasses import replace
from pathlib import Path

import pytest

from mesh_ir.abi.decoder import decode_program
from mesh_ir.abi.encoder import encode_program
from mesh_ir.architecture import load_arch
from mesh_ir.canonical import semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.golden_programs import build_dual_core_program, build_single_core_program
from mesh_ir.generated import abi as A
from mesh_ir.ir.kernel_ir import KernelOpcode, RecvWaitAttrs
from mesh_ir.scheduled.verify import verify_program


ROOT = Path(__file__).resolve().parents[4]
ARCH_PATH = ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml"


@pytest.fixture(scope="module")
def arch():
    return load_arch(ARCH_PATH)


@pytest.fixture(scope="module")
def reference_single(arch):
    return encode_program(build_single_core_program(arch))

def fresh_program(program, **changes):
    provisional = replace(program, semantic_sha256="", **changes)
    return replace(provisional, semantic_sha256=semantic_sha256(provisional.semantic_dict()))


def changed_header(image, **changes):
    names = [field["name"] for field in A.HEADER_FIELDS]
    values = dict(zip(names, A.HEADER_FORMAT.unpack_from(image)))
    values.update(changes)
    result = bytearray(image)
    A.HEADER_FORMAT.pack_into(result, 0, *(values[name] for name in names))
    return bytes(result)


def test_missing_scheduled_semantics_feature_is_rejected(reference_single):
    assert encode_program(decode_program(reference_single)) == reference_single
    corrupt = changed_header(reference_single, required_features=0)
    with pytest.raises(MeshIrError) as error:
        decode_program(corrupt)
    assert error.value.code == "E_ABI_VERSION"


def test_reference_bad_magic_fails_before_payload_decode(reference_single):
    corrupt = bytearray(reference_single)
    corrupt[0] ^= 0xFF
    with pytest.raises(MeshIrError) as error:
        decode_program(bytes(corrupt))
    assert error.value.code == "E_ABI_MAGIC"


def test_reference_unknown_major_fails_before_payload_decode(reference_single):
    corrupt = bytearray(reference_single)
    corrupt[8:10] = (A.ABI_MAJOR + 1).to_bytes(2, "little")
    with pytest.raises(MeshIrError) as error:
        decode_program(bytes(corrupt))
    assert error.value.code == "E_ABI_VERSION"


def test_reference_unknown_required_feature_fails_closed(reference_single):
    unknown = 1 << 63
    assert not A.REQUIRED_FEATURES & unknown
    corrupt = changed_header(
        reference_single, required_features=A.REQUIRED_FEATURES | unknown)
    with pytest.raises(MeshIrError) as error:
        decode_program(bytes(corrupt))
    assert error.value.code == "E_ABI_VERSION"


def test_reference_payload_corruption_is_detected(reference_single):
    corrupt = bytearray(reference_single)
    corrupt[200] ^= 1
    with pytest.raises(MeshIrError) as error:
        decode_program(bytes(corrupt))
    assert error.value.code == "E_ABI_CHECKSUM"


def test_reference_truncation_is_detected(reference_single):
    with pytest.raises(MeshIrError) as error:
        decode_program(reference_single[:-8])
    assert error.value.code == "E_ABI_SECTION_RANGE"


def test_encoder_rejects_unknown_major_for_a_complete_scheduled_program(arch):
    program = build_single_core_program(arch)
    assert verify_program(program, arch).program is program
    assert decode_program(encode_program(program)).canonical_bytes() == program.canonical_bytes()
    unsupported = fresh_program(program, abi_major=A.ABI_MAJOR + 1)
    with pytest.raises(MeshIrError) as error:
        encode_program(unsupported)
    assert error.value.code == "E_ABI_VERSION"


def test_fresh_hash_dependency_cycle_is_rejected(arch):
    baseline = build_single_core_program(arch)
    load = next(item for item in baseline.semantics.kernel_ops if item.stable_key == "load:input")
    gemm = next(item for item in baseline.semantics.kernel_ops if item.opcode is KernelOpcode.GEMM)
    operations = tuple(
        replace(item, after_tokens=(gemm.done_token,)) if item.op_id == load.op_id else item
        for item in baseline.semantics.kernel_ops
    )
    changed = fresh_program(baseline, semantics=replace(baseline.semantics, kernel_ops=operations))
    assert changed.semantic_sha256 != baseline.semantic_sha256
    with pytest.raises(MeshIrError) as error:
        verify_program(changed, arch)
    assert error.value.code == "E_DEPENDENCY_CYCLE"


def test_fresh_hash_sram_overlap_is_rejected(arch):
    baseline = build_single_core_program(arch)
    allocations = (
        baseline.allocations[0],
        replace(baseline.allocations[1], offset_bytes=baseline.allocations[0].offset_bytes),
        *baseline.allocations[2:],
    )
    changed = fresh_program(baseline, allocations=allocations)
    assert changed.semantic_sha256 != baseline.semantic_sha256
    with pytest.raises(MeshIrError) as error:
        verify_program(changed, arch)
    assert error.value.code == "E_SRAM_OOM"


def test_fresh_hash_unmatched_receive_is_rejected(arch):
    baseline = build_dual_core_program(arch)
    receive = next(item for item in baseline.semantics.kernel_ops if item.opcode is KernelOpcode.RECV_WAIT)
    operations = tuple(
        replace(item, attrs=RecvWaitAttrs(999, 0, 1, receive.attrs.expected_bytes))
        if item.op_id == receive.op_id else item
        for item in baseline.semantics.kernel_ops
    )
    changed = fresh_program(baseline, semantics=replace(baseline.semantics, kernel_ops=operations))
    assert changed.semantic_sha256 != baseline.semantic_sha256
    with pytest.raises(MeshIrError) as error:
        verify_program(changed, arch)
    assert error.value.code == "E_P2P_UNMATCHED"


@pytest.mark.parametrize('tamper', ['completion_barrier', 'operand_allocation', 'endpoint_prefix', 'stride_tail'])
def test_admitted_dma_binding_mutations_are_rejected(arch, tamper):
    program = build_dual_core_program(arch)
    if tamper == 'completion_barrier':
        descriptor = program.dma_descriptors[0]
        event = next(row for row in program.events if row.event_id == descriptor.completion_event)
        program = fresh_program(program, events=tuple(
            replace(row, kind=A.EVENT_KIND.BARRIER, expected_arrivals=1) if row.event_id == event.event_id else row
            for row in program.events))
    elif tamper == 'operand_allocation':
        operand = next(row for row in program.command_operands if row.allocation_id)
        other = next(row for row in program.allocations if row.allocation_id != operand.allocation_id)
        program = fresh_program(program, command_operands=tuple(
            replace(row, allocation_id=other.allocation_id) if row is operand else row
            for row in program.command_operands))
    else:
        descriptor = next(row for row in program.dma_descriptors if row.kind == A.DMA_KIND.P2P_PUSH)
        changed = replace(descriptor, dst=replace(descriptor.dst, offset_bytes=descriptor.dst.offset_bytes - 1)) if tamper == 'endpoint_prefix' else replace(descriptor, rows=2, dst_stride_bytes=arch.sram_bytes)
        program = fresh_program(program, dma_descriptors=tuple(changed if row is descriptor else row for row in program.dma_descriptors))
    with pytest.raises(MeshIrError):
        verify_program(program, arch)
