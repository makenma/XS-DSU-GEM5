from dataclasses import asdict, replace

import pytest

from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir import kernel_verify
from tests.unit.test_gate2_kernel_ir import load_compute_store_kernel, refreshed


def test_immutable_kernel_reuses_its_complete_verification_across_memory_passes(monkeypatch):
    kernel = replace(load_compute_store_kernel())
    before = asdict(kernel)
    verify = kernel_verify._verify_intrinsic
    calls = []

    def observed(records):
        calls.append(records)
        return verify(records)

    monkeypatch.setattr(kernel_verify, '_verify_intrinsic', observed)
    kernel.verify()
    kernel.verify()
    first = kernel_verify.verify_kernel_memory(kernel.memory_records())
    second = kernel_verify.verify_kernel_memory(kernel.memory_records())
    assert len(calls) == 1
    assert first.dependencies is second.dependencies
    assert kernel.memory_records() is kernel.memory_records()
    assert asdict(kernel) == before


def test_replaced_kernel_is_verified_again_and_rejects_invalid_content():
    kernel = load_compute_store_kernel()
    kernel.verify()
    changed = refreshed(replace(kernel, ops=kernel.ops[:-1]))
    with pytest.raises(MeshIrError):
        changed.verify()


def test_architecture_checks_are_not_cached_with_intrinsic_verification():
    from tests.unit.test_gate2_dependency_sram import local_arch

    kernel = load_compute_store_kernel()
    kernel.verify()
    with pytest.raises(MeshIrError):
        kernel.verify(local_arch())
