from pathlib import Path
from dataclasses import replace

import pytest

from mesh_ir.architecture import load_arch
from mesh_ir.canonical import semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import DType, DmaKind
from mesh_ir.ir.graph_ir import OpCode
from mesh_ir.ir.kernel_ir import BarrierAttrs, DmaAttrs, KernelOpcode, MatrixPhase, RecvWaitAttrs
from mesh_ir.scheduled.model import AxiFenceAttrs, FenceScope, RepeatCommandAttrs
from mesh_ir.scheduled.addressing import derive_descriptor_execution_set
from mesh_ir.scheduled.verify import verify_program
import mesh_ir.golden_programs as golden


ROOT = Path(__file__).resolve().parents[4]


CASES = (
    golden.build_single_core_program,
    golden.build_region_edge_program,
    golden.build_compute_timing_program,
    golden.build_dual_core_program,
    golden.build_fill_program,
    golden.build_repeat_program,
    golden.build_poison_program,
    golden.build_dma_edge_program,
    golden.build_dma_error_program,
    golden.build_dma_write_error_program,
    golden.build_dma_fence_program,
    golden.build_dma_pin_program,
    golden.build_sram_parallel_program,
    golden.build_sram_conflict_program,
    golden.build_poison_elementwise_inplace_program,
    golden.build_poison_store_program,
    golden.build_poison_reduce_dst_old_program,
    golden.build_dma_shapes_program,
    golden.build_zero_dma_program,
    golden.build_fence_scopes_program,
    golden.build_cross_error_program,
    golden.build_repeat_error_program,
    golden.build_p2p_reuse_program,
    golden.build_barrier_e2e_program,
    golden.build_cross_fault_program,
    golden.build_read_window_program,
    golden.build_load_saturation_contiguous_program,
    golden.build_load_saturation_multi_tensor_program,
    golden.build_load_saturation_strided_program,
)


@pytest.mark.parametrize("factory", CASES, ids=lambda factory: factory.__name__)
def test_every_authored_golden_case_is_a_complete_intrinsically_valid_program(factory):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")

    program = factory(arch)

    assert verify_program(program, arch).program is program
    assert program.semantic_sha256
    assert program.semantics.variants[0].lineage.authoring_variant_id


@pytest.mark.parametrize(
    ("factory", "required_opcodes"),
    (
        (golden.build_single_core_program, {KernelOpcode.DMA, KernelOpcode.GEMM}),
        (golden.build_compute_timing_program, {KernelOpcode.DMA, KernelOpcode.GEMM, KernelOpcode.BMM, KernelOpcode.VECTOR, KernelOpcode.LOCAL_REDUCE}),
        (golden.build_dual_core_program, {KernelOpcode.DMA, KernelOpcode.GEMM, KernelOpcode.RECV_WAIT, KernelOpcode.LOCAL_REDUCE}),
        (golden.build_fill_program, {KernelOpcode.DMA, KernelOpcode.GEMM}),
        (golden.build_dma_shapes_program, {KernelOpcode.DMA}),
        (golden.build_p2p_reuse_program, {KernelOpcode.DMA, KernelOpcode.RECV_WAIT}),
        (golden.build_barrier_e2e_program, {KernelOpcode.BARRIER}),
    ),
)
def test_authored_golden_cases_preserve_operation_families(factory, required_opcodes):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    program = factory(arch)

    assert required_opcodes <= {item.opcode for item in program.semantics.kernel_ops}


def test_authored_controls_and_dma_kain_typed_intent():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    repeat = golden.build_repeat_program(arch)
    fences = golden.build_fence_scopes_program(arch)
    shapes = golden.build_dma_shapes_program(arch)

    repeat_attrs = tuple(item.source.attrs for item in repeat.semantics.command_semantics if hasattr(item.source, "attrs"))
    fence_attrs = tuple(item.source.attrs for item in fences.semantics.command_semantics if hasattr(item.source, "attrs"))
    dma_attrs = tuple(item.attrs for item in shapes.semantics.kernel_ops if type(item.attrs) is DmaAttrs)
    assert any(type(item) is RepeatCommandAttrs and item.repeat_count == golden.REPEAT_COUNT for item in repeat_attrs)
    assert {item.scope for item in fence_attrs if type(item) is AxiFenceAttrs} == {FenceScope.DMA_READ, FenceScope.DMA_WRITE, FenceScope.P2P, FenceScope.HOST_SHARED_WRITE, FenceScope.ALL_INSTANCE}
    assert {item.kind for item in dma_attrs} >= {DmaKind.PREFETCH, DmaKind.LOCAL_FILL, DmaKind.P2P_PUSH, DmaKind.STORE}
    assert any(type(item.attrs) is RecvWaitAttrs for item in shapes.semantics.kernel_ops)
    assert any(type(item.attrs) is BarrierAttrs for item in golden.build_barrier_e2e_program(arch).semantics.kernel_ops)


def test_compute_timing_and_dual_core_keep_real_dataflow_families():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    timing = golden.build_compute_timing_program(arch)
    dual = golden.build_dual_core_program(arch)

    timing_opcodes = tuple(item.opcode for item in timing.semantics.kernel_ops)
    dual_opcodes = tuple(item.opcode for item in dual.semantics.kernel_ops)
    assert KernelOpcode.BMM in timing_opcodes
    assert KernelOpcode.VECTOR in timing_opcodes
    assert KernelOpcode.LOCAL_REDUCE in timing_opcodes
    assert KernelOpcode.GEMM in dual_opcodes
    assert KernelOpcode.LOCAL_REDUCE in dual_opcodes
    assert any(type(item.attrs) is RecvWaitAttrs for item in dual.semantics.kernel_ops)

    timing_effects = tuple(
        item for item in timing.semantics.kernel_ops
        if item.opcode not in (KernelOpcode.ALLOC, KernelOpcode.VIEW)
    )
    fills = tuple(item for item in timing_effects if type(item.attrs) is DmaAttrs and item.attrs.kind is DmaKind.LOCAL_FILL)
    peer_bmm = next(item for item in timing_effects if item.opcode is KernelOpcode.BMM and item.owner_core == 0)
    selected_gemm = next(item for item in timing_effects if item.opcode is KernelOpcode.GEMM and item.owner_core == 1)
    selected_bmm = next(item for item in timing_effects if item.opcode is KernelOpcode.BMM and item.owner_core == 1)
    vector = next(item for item in timing_effects if item.opcode is KernelOpcode.VECTOR)
    p2p = next(item for item in timing_effects if type(item.attrs) is DmaAttrs and item.attrs.kind is DmaKind.P2P_PUSH)
    recv = next(item for item in timing_effects if item.opcode is KernelOpcode.RECV_WAIT)
    timing_reduce = next(item for item in timing_effects if item.opcode is KernelOpcode.LOCAL_REDUCE)
    assert tuple(item.owner_core for item in fills) == (0, 0, 1, 1)
    assert peer_bmm.attrs.phase is MatrixPhase.ACCUMULATE_ONLY
    assert selected_gemm.attrs.phase is MatrixPhase.DIRECT
    assert selected_bmm.attrs.phase is MatrixPhase.ACCUMULATE_ONLY
    assert selected_bmm.after_tokens == (selected_gemm.done_token,)
    assert vector.attrs.graph_opcode is OpCode.RELU
    assert vector.after_tokens == (selected_bmm.done_token,)
    assert p2p.after_tokens == (peer_bmm.done_token,)
    assert recv.after_tokens == (p2p.done_token,)
    assert timing_reduce.after_tokens == (recv.done_token, vector.done_token)
    partial = timing.semantics.partial_sums[0]
    placement = timing.semantics.placements[partial.placement_id - 1]
    states = {item.state_id: item for item in timing.semantics.states}
    assert placement.core_ids == (0, 1)
    assert {states[item.state_id].partial_sum_id for item in timing_reduce.reads} == {partial.partial_sum_id}

    dual_effects = tuple(
        item for item in dual.semantics.kernel_ops
        if item.opcode not in (KernelOpcode.ALLOC, KernelOpcode.VIEW)
    )
    assert tuple(item.attrs.kind for item in dual_effects if type(item.attrs) is DmaAttrs) == (
        DmaKind.LOAD,
        DmaKind.LOAD,
        DmaKind.P2P_PUSH,
        DmaKind.LOAD,
        DmaKind.LOAD,
        DmaKind.STORE,
    )
    gemms = tuple(item for item in dual_effects if item.opcode is KernelOpcode.GEMM)
    assert tuple(item.owner_core for item in gemms) == (0, 1)
    assert all(item.attrs.phase is MatrixPhase.ACCUMULATE_ONLY for item in gemms)
    p2p = next(item for item in dual_effects if type(item.attrs) is DmaAttrs and item.attrs.kind is DmaKind.P2P_PUSH)
    recv = next(item for item in dual_effects if item.opcode is KernelOpcode.RECV_WAIT)
    reduce = next(item for item in dual_effects if item.opcode is KernelOpcode.LOCAL_REDUCE)
    epilogue = next(item for item in dual_effects if item.opcode is KernelOpcode.MATRIX_EPILOGUE)
    store = next(item for item in dual_effects if type(item.attrs) is DmaAttrs and item.attrs.kind is DmaKind.STORE)
    assert (p2p.attrs.source_core, p2p.attrs.destination_core) == (0, 1)
    assert recv.attrs.transfer_id == p2p.attrs.transfer_id
    assert recv.after_tokens == (p2p.done_token,)
    assert reduce.after_tokens == (recv.done_token, gemms[1].done_token)
    assert epilogue.after_tokens == (reduce.done_token,)
    assert store.after_tokens == (epilogue.done_token,)
    assert len(dual.semantics.partial_sums) == 1


def test_compute_timing_bmm_partials_exactly_partition_k_and_materialize_result():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    program = golden.build_compute_timing_program(arch)
    bmm = sorted(
        (item for item in program.semantics.kernel_ops if item.opcode is KernelOpcode.BMM),
        key=lambda item: item.attrs.tile.k_origin,
    )
    shards = {item.shard_id: item for item in program.semantics.logical_shards}
    views = {item.view_id: item for item in program.semantics.views}
    tensors = {item.tensor_id: item for item in program.semantics.kernel_tensors}
    assert tuple((item.attrs.tile.k_origin, item.attrs.tile.valid_k) for item in bmm) == ((0, 1), (1, 1))
    assert tuple(
        (
            shards[views[item.reads[0].view_id].shard_id].global_origin[-1],
            item.reads[0].region.shape[-1],
            shards[views[item.reads[1].view_id].shard_id].global_origin[-2],
            item.reads[1].region.shape[-2],
        )
        for item in bmm
    ) == ((0, 1, 0, 1), (1, 1, 1, 1))
    assert tuple(
        (
            views[item.reads[0].view_id].object_offset_elements,
            views[item.reads[1].view_id].object_offset_elements,
            item.attrs.cost.logical_input_bytes,
            item.attrs.cost.macs,
        )
        for item in bmm
    ) == ((0, 0, 8, 4), (1, 2, 8, 4))
    reduce = next(item for item in program.semantics.kernel_ops if item.opcode is KernelOpcode.LOCAL_REDUCE)
    epilogue = next(item for item in program.semantics.kernel_ops if item.opcode is KernelOpcode.MATRIX_EPILOGUE)
    result_tensor_id = program.semantics.computations[1].result_tensor_id
    assert tensors[result_tensor_id].dtype is DType.FP16
    assert shards[epilogue.result_shard_id].tensor_id == result_tensor_id
    assert epilogue.after_tokens == (reduce.done_token,)


@pytest.mark.parametrize("repeated", (False, True))
def test_compute_timing_rejects_invalid_local_reduction_contributors(repeated):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    baseline = golden.build_compute_timing_program(arch)
    reduce = next(item for item in baseline.semantics.kernel_ops if item.opcode is KernelOpcode.LOCAL_REDUCE)
    reads = (reduce.reads[0], reduce.reads[0]) if repeated else reduce.reads[:1]
    operations = tuple(replace(item, reads=reads) if item.op_id == reduce.op_id else item for item in baseline.semantics.kernel_ops)
    changed = replace(baseline, semantics=replace(baseline.semantics, kernel_ops=operations))
    changed = replace(changed, semantic_sha256=semantic_sha256(changed.semantic_dict()))
    assert changed.semantic_sha256 != baseline.semantic_sha256
    with pytest.raises(MeshIrError) as error:
        verify_program(changed, arch)
    assert error.value.code == "E_TENSOR_NOT_RESIDENT"


def test_dual_core_materializes_the_fp16_semantic_result_before_store():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    program = golden.build_dual_core_program(arch)
    computation = program.semantics.computations[0]
    tensors = {item.tensor_id: item for item in program.semantics.kernel_tensors}
    shards = {item.shard_id: item for item in program.semantics.logical_shards}
    views = {item.view_id: item for item in program.semantics.views}
    reduce = next(item for item in program.semantics.kernel_ops if item.opcode is KernelOpcode.LOCAL_REDUCE)
    epilogue = next(item for item in program.semantics.kernel_ops if item.opcode is KernelOpcode.MATRIX_EPILOGUE)
    store = next(item for item in program.semantics.kernel_ops if type(item.attrs) is DmaAttrs and item.attrs.kind is DmaKind.STORE)
    descriptor = next(item for item in program.dma_descriptors if item.kind == DmaKind.STORE)
    assert tensors[computation.result_tensor_id].dtype is DType.FP16
    assert shards[epilogue.result_shard_id].tensor_id == computation.result_tensor_id
    assert shards[views[store.reads[0].view_id].shard_id].tensor_id == computation.result_tensor_id
    assert epilogue.after_tokens == (reduce.done_token,)
    assert store.after_tokens == (epilogue.done_token,)
    assert descriptor.rows * descriptor.row_bytes == golden.PARTIAL_BYTES


@pytest.mark.parametrize(
    ("factory", "stable_key", "read_index", "empty_state_id", "required_opcodes"),
    (
        (golden.build_poison_program, "gemm", 0, 2, {KernelOpcode.DMA, KernelOpcode.GEMM}),
        (golden.build_poison_elementwise_inplace_program, "poison-ew:relu", 0, 1, {KernelOpcode.DMA, KernelOpcode.VECTOR}),
        (golden.build_poison_store_program, "poison-store:store", 0, 1, {KernelOpcode.DMA}),
        (golden.build_poison_reduce_dst_old_program, "timing:10:reduce:selected", 1, 13, {KernelOpcode.DMA, KernelOpcode.GEMM, KernelOpcode.BMM, KernelOpcode.VECTOR, KernelOpcode.RECV_WAIT, KernelOpcode.LOCAL_REDUCE}),
    ),
    ids=("compute", "elementwise-inplace", "store", "reduce-destination"),
)
def test_poison_cases_keep_valid_real_baselines_and_reject_fresh_hash_stale_reads(
    factory,
    stable_key,
    read_index,
    empty_state_id,
    required_opcodes,
):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    baseline = factory(arch)
    assert verify_program(baseline, arch).program is baseline
    assert required_opcodes <= {item.opcode for item in baseline.semantics.kernel_ops}
    operation = next(item for item in baseline.semantics.kernel_ops if item.stable_key == stable_key)
    reads = list(operation.reads)
    reads[read_index] = replace(reads[read_index], state_id=empty_state_id)
    operations = tuple(
        replace(item, reads=tuple(reads)) if item.op_id == operation.op_id else item
        for item in baseline.semantics.kernel_ops
    )
    changed = replace(baseline, semantics=replace(baseline.semantics, kernel_ops=operations))
    changed = replace(changed, semantic_sha256=semantic_sha256(changed.semantic_dict()))
    assert changed.semantic_sha256 != baseline.semantic_sha256
    with pytest.raises(MeshIrError) as error:
        verify_program(changed, arch)
    assert error.value.code == "E_TENSOR_NOT_RESIDENT"


def test_dma_shape_and_fill_cases_keep_physical_dma_kinds():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    shapes = golden.build_dma_shapes_program(arch)
    fill = golden.build_fill_program(arch)

    shape_kinds = {
        item.attrs.kind
        for item in shapes.semantics.kernel_ops
        if type(item.attrs) is DmaAttrs
    }
    fill_kinds = {
        item.attrs.kind
        for item in fill.semantics.kernel_ops
        if type(item.attrs) is DmaAttrs
    }
    assert shape_kinds >= {
        DmaKind.PREFETCH,
        DmaKind.LOCAL_FILL,
        DmaKind.P2P_PUSH,
        DmaKind.STORE,
    }
    assert DmaKind.LOCAL_FILL in fill_kinds


def test_repeat_case_replays_the_real_three_command_body_and_load_traffic():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    program = golden.build_repeat_program(arch)

    semantics = tuple(item.source for item in program.semantics.command_semantics)
    repeat = next(item.attrs for item in semantics if hasattr(item, "attrs") and type(item.attrs) is RepeatCommandAttrs)
    kernel_ids = tuple(item.kernel_op_id for item in semantics if hasattr(item, "kernel_op_id"))
    effect_by_id = {item.op_id: item for item in program.semantics.kernel_ops}
    assert repeat == RepeatCommandAttrs(1, 3, 3)
    assert tuple(effect_by_id[item].opcode for item in kernel_ids) == (
        KernelOpcode.DMA,
        KernelOpcode.GEMM,
        KernelOpcode.VECTOR,
    )
    assert len(program.dma_descriptors) == 1
    assert program.dma_descriptors[0].useful_bytes == 128
    local_allocations = tuple(item for item in program.allocations if item.owner_core == 0)
    assert tuple(item.offset_bytes for item in local_allocations) == (0, 0x2000)
    vector = next(item for item in program.semantics.kernel_ops if item.opcode is KernelOpcode.VECTOR)
    state_by_id = {item.state_id: item for item in program.semantics.states}
    assert state_by_id[vector.reads[0].state_id].object_id == state_by_id[vector.writes[0].old_state_id].object_id
    assert state_by_id[vector.writes[0].old_state_id].object_id == state_by_id[vector.writes[0].new_state_id].object_id
    traffic = program.semantics.intrinsic_traffic.descriptors
    assert len(traffic) == 1
    assert traffic[0].execution_count == 3
    assert traffic[0].useful_bytes == 384


def test_sram_bank_cases_preserve_only_the_selected_bank_pattern():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    parallel = golden.build_sram_parallel_program(arch)
    conflict = golden.build_sram_conflict_program(arch)
    interleave = arch.sram_bank_interleave_bytes
    banks = arch.sram_banks

    parallel_banks = tuple(
        (item.offset_bytes // interleave) % banks for item in parallel.allocations
    )
    conflict_banks = tuple(
        (item.offset_bytes // interleave) % banks for item in conflict.allocations
    )
    assert len(set(parallel_banks)) == len(parallel_banks)
    assert len(set(conflict_banks)) == 1
    assert tuple(item.size_bytes for item in parallel.allocations) == tuple(
        item.size_bytes for item in conflict.allocations
    )


def test_fill_and_zero_dma_cases_preserve_zero_length_completion_semantics():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    fill = golden.build_fill_program(arch)
    zero = golden.build_zero_dma_program(arch)

    assert tuple(
        (item.kind, item.rows, item.row_bytes, item.useful_bytes)
        for item in fill.dma_descriptors
    ) == (
        (DmaKind.LOCAL_FILL, 1, 128, 128),
        (DmaKind.LOAD, 1, 0, 0),
        (DmaKind.STORE, 1, 128, 128),
    )
    gemm = next(item for item in fill.semantics.kernel_ops if item.opcode is KernelOpcode.GEMM)
    assert (gemm.attrs.tile.valid_m, gemm.attrs.tile.valid_n, gemm.attrs.tile.valid_k) == (8, 8, 8)
    assert tuple(item.size_bytes for item in fill.allocations) == (128, 128)
    zero_ops = tuple(
        item
        for item in zero.semantics.kernel_ops
        if item.opcode is KernelOpcode.DMA
    )
    assert tuple(item.attrs.kind for item in zero_ops) == (
        DmaKind.LOAD,
        DmaKind.LOAD,
        DmaKind.LOCAL_FILL,
        DmaKind.LOCAL_FILL,
        DmaKind.P2P_PUSH,
    )
    assert tuple(
        (item.reads or (item.writes[0],))[0].region.shape
        for item in zero_ops
    ) == ((0, 32), (1, 0), (0, 32), (1, 0), (0, 32))
    assert tuple((item.rows, item.row_bytes) for item in zero.dma_descriptors) == (
        (0, 64),
        (1, 0),
        (0, 64),
        (1, 0),
        (0, 64),
    )
    executions, _ = derive_descriptor_execution_set(zero, arch)
    assert (executions[1].descriptor.src.region_offset_bytes, executions[1].descriptor.dst.region_offset_bytes) == (0x100040, 0)
    assert any(type(item.attrs) is RecvWaitAttrs for item in zero.semantics.kernel_ops)
    assert all(item.useful_bytes == 0 for item in zero.dma_descriptors)
    assert all(item.useful_bytes == 0 for item in zero.semantics.intrinsic_traffic.descriptors)


def test_read_window_and_load_saturation_keep_exact_descriptor_shapes():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    read_window = golden.build_read_window_program(arch)
    contiguous = golden.build_load_saturation_contiguous_program(arch)
    multi = golden.build_load_saturation_multi_tensor_program(arch)
    strided = golden.build_load_saturation_strided_program(arch)

    assert len(read_window.dma_descriptors) == 1
    assert read_window.dma_descriptors[0].useful_bytes == 6 * 1024
    assert read_window.dma_descriptors[0].max_burst_beats == 8
    assert len(contiguous.dma_descriptors) == 2
    assert len(multi.dma_descriptors) == 8
    assert len(strided.dma_descriptors) == 2
    assert all(item.useful_bytes == 64 * 1024 for item in contiguous.dma_descriptors)
    assert all(item.useful_bytes == 16 * 1024 for item in multi.dma_descriptors)
    assert all((item.rows, item.row_bytes, item.src_stride_bytes) == (64, 512, 1024) for item in strided.dma_descriptors)
    assert tuple(
        (item.owner_core, item.offset_bytes, item.size_bytes)
        for item in multi.allocations
    ) == tuple(
        (core_id, ordinal * 0x20000, 16 * 1024)
        for core_id in (0, 1)
        for ordinal in range(4)
    )
    assert tuple(
        (item.owner_core, item.offset_bytes, item.size_bytes)
        for item in strided.allocations
    ) == ((0, 0, 32 * 1024), (1, 0, 32 * 1024))


def test_dma_edge_retains_load_store_and_boundary_sizes():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    program = golden.build_dma_edge_program(arch)

    cap = arch.axi_data_bytes * arch.axi_max_burst_beats
    expected_sizes = (1, 1, arch.axi_data_bytes - 1, arch.axi_data_bytes + 1, 64, cap - 1, cap + 1, 700)
    assert tuple(item.kind for item in program.dma_descriptors) == tuple(
        kind for _ in expected_sizes for kind in (DmaKind.LOAD, DmaKind.STORE)
    )
    assert tuple(item.useful_bytes for item in program.dma_descriptors) == tuple(
        size for size in expected_sizes for _ in range(2)
    )
    executions, _ = derive_descriptor_execution_set(program, arch)
    assert tuple(item.descriptor.src.region_offset_bytes for item in executions[::2]) == (
        0x100000,
        0x100041,
        0x100080,
        0x1000C0,
        0x100FF0,
        0x101100,
        0x101400,
        0x101800,
    )
    assert tuple(item.descriptor.dst.region_offset_bytes for item in executions[1::2]) == (
        0x200000,
        0x200040,
        0x200080,
        0x2000C0,
        0x201000,
        0x201100,
        0x201400,
        0x201800,
    )
    assert any(item.bursts > 1 for item in program.semantics.intrinsic_traffic.descriptors)
