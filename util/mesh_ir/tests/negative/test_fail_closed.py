from dataclasses import replace
from pathlib import Path
import re

import pytest

from mesh_ir.abi.decoder import decode_program
from mesh_ir.abi.encoder import encode_program
from mesh_ir.architecture import load_arch
from mesh_ir.canonical import semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.golden_programs import build_dual_core_program, build_single_core_program
from mesh_ir.ir.kernel_ir import KernelOpcode, RecvWaitAttrs
from mesh_ir.scheduled.verify import verify_program


ROOT = Path(__file__).resolve().parents[4]
ARCH_PATH = ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml"
FROZEN_V1_PATH = ROOT / "src/dev/ai_mesh/generated/golden_mshb.inc"


@pytest.fixture(scope="module")
def arch():
    return load_arch(ARCH_PATH)


@pytest.fixture(scope="module")
def frozen_v1_single():
    source = FROZEN_V1_PATH.read_text(encoding="utf-8")
    match = re.search(r"kGoldenMshbSingle\[\]\s*=\s*\{(.*?)\};", source, re.DOTALL)
    assert match is not None
    return bytes(int(value, 16) for value in re.findall(r"0x([0-9a-fA-F]{2})", match.group(1)))


def fresh_program(program, **changes):
    provisional = replace(program, semantic_sha256="", **changes)
    return replace(provisional, semantic_sha256=semantic_sha256(provisional.semantic_dict()))


def test_frozen_v1_reaches_the_gate2_scheduled_semantics_boundary(frozen_v1_single):
    with pytest.raises(MeshIrError) as error:
        decode_program(frozen_v1_single)
    assert error.value.code == "E_ABI_VERSION"


def test_frozen_v1_bad_magic_fails_before_payload_decode(frozen_v1_single):
    corrupt = bytearray(frozen_v1_single)
    corrupt[0] ^= 0xFF
    with pytest.raises(MeshIrError) as error:
        decode_program(bytes(corrupt))
    assert error.value.code == "E_ABI_MAGIC"


def test_frozen_v1_unknown_major_fails_before_payload_decode(frozen_v1_single):
    corrupt = bytearray(frozen_v1_single)
    corrupt[8:10] = (2).to_bytes(2, "little")
    with pytest.raises(MeshIrError) as error:
        decode_program(bytes(corrupt))
    assert error.value.code == "E_ABI_VERSION"


def test_frozen_v1_unknown_required_feature_fails_closed(frozen_v1_single):
    corrupt = bytearray(frozen_v1_single)
    corrupt[104] = 1
    with pytest.raises(MeshIrError) as error:
        decode_program(bytes(corrupt))
    assert error.value.code == "E_ABI_VERSION"


def test_frozen_v1_payload_corruption_is_detected(frozen_v1_single):
    corrupt = bytearray(frozen_v1_single)
    corrupt[200] ^= 1
    with pytest.raises(MeshIrError) as error:
        decode_program(bytes(corrupt))
    assert error.value.code == "E_ABI_CHECKSUM"


def test_frozen_v1_truncation_is_detected(frozen_v1_single):
    with pytest.raises(MeshIrError) as error:
        decode_program(frozen_v1_single[:-8])
    assert error.value.code == "E_ABI_SECTION_RANGE"


def test_gate2_encoder_rejects_a_complete_scheduled_program(arch):
    program = build_single_core_program(arch)
    assert verify_program(program, arch).program is program
    with pytest.raises(MeshIrError) as error:
        encode_program(program)
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
