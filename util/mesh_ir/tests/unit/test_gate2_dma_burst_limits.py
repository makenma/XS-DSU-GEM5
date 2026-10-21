import copy
from dataclasses import replace
from pathlib import Path

import pytest
import mesh_ir.traffic as traffic_module

from mesh_ir.analysis.sram import SramAllocation
from mesh_ir.architecture import load_arch
from mesh_ir.builder import ProgramBuilder
from mesh_ir.canonical import semantic_sha256
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Access, DmaKind
from mesh_ir.golden_programs import build_fill_program, build_load_saturation_strided_program, build_read_window_program, build_repeat_program, build_zero_dma_program
from mesh_ir.ir.kernel_ir import DmaAttrs, KernelBundle, KernelMemoryRecords, KernelModule, KernelOpcode
from mesh_ir.ir.kernel_verify import verify_kernel_memory
from mesh_ir.passes.execution import PassExecutor
from mesh_ir.passes.scheduled import lower_to_program
from mesh_ir.scheduled.addressing import _derive_descriptor_execution_set, bind_invocation, derive_descriptor_execution_set
from mesh_ir.scheduled.assemble import _expected_traffic, assemble_program
from mesh_ir.scheduled.model import AuthoredProgramOrigin, ControlCommandSource, ExternalSlotBacking, KernelCommandSource, LocalAllocationBacking
from mesh_ir.scheduled.projection import effective_dma_burst_beats
from mesh_ir.scheduled.verify import verify_pretraffic_state, verify_program
from mesh_ir.traffic import DescriptorExecution, TrafficDirection, calculate_descriptor_traffic, calculate_traffic
from tests.unit.test_gate2_kernel_ir import load_compute_store_kernel
from tests.unit.test_gate2_scheduled_dma import _state
from tests.unit.test_gate2_scheduled_pipeline import _inputs, _lowering_for_bundle


ROOT = Path(__file__).resolve().parents[4]


def _records(state):
    semantics = state.semantics
    return KernelMemoryRecords(semantics.kernel_tensors, semantics.computations, semantics.placements, semantics.logical_shards, semantics.partial_sums, semantics.objects, semantics.views, semantics.states, semantics.tokens, semantics.kernel_ops)


def _pretraffic(arch, limit):
    state = _state(arch)
    op = state.semantics.kernel_ops[4]
    attrs = replace(op.attrs, max_burst_beats=limit)
    semantics = replace(state.semantics, kernel_ops=state.semantics.kernel_ops[:4] + (replace(op, attrs=attrs),))
    descriptor = replace(state.transport.dma_descriptors[0], max_burst_beats=effective_dma_burst_beats(attrs, arch))
    transport = replace(state.transport, dma_descriptors=(descriptor,))
    executions, identity = _derive_descriptor_execution_set(transport, semantics, arch)
    semantics = replace(semantics, executions=executions, reference_binding_identity_sha256=identity)
    return replace(state, semantics=semantics, transport=transport)


def _kernel_with_limit(kernel, limit):
    op = kernel.ops[8]
    ops = kernel.ops[:8] + (replace(op, attrs=replace(op.attrs, max_burst_beats=limit)),) + kernel.ops[9:]
    return KernelModule.create(kernel.arch_digest, kernel.source_semantic_hash, kernel.entrypoint, kernel.profile_id, kernel.tensors, kernel.computations, kernel.tensor_lineage, kernel.computation_lineage, kernel.placements, kernel.shards, kernel.partial_sums, kernel.objects, kernel.views, kernel.states, kernel.tokens, ops)


def _asymmetric_store(descriptor):
    return replace(
        descriptor,
        identity=replace(descriptor.identity, direction=TrafficDirection.WRITE),
        kind=DmaKind.STORE,
        src=descriptor.dst,
        dst=replace(descriptor.src, access=Access.READ_WRITE, backing_access=Access.READ_WRITE),
        src_stride_bytes=descriptor.dst_stride_bytes,
        dst_stride_bytes=descriptor.src_stride_bytes,
    )


def _reauthor_with_limit(arch, source, limit, variant_count=1):
    semantics = source.semantics
    ops = tuple(replace(op, attrs=replace(op.attrs, max_burst_beats=limit)) if op.opcode is KernelOpcode.DMA and op.attrs.kind is not DmaKind.LOCAL_FILL else op for op in semantics.kernel_ops)
    records = KernelMemoryRecords(semantics.kernel_tensors, semantics.computations, semantics.placements, semantics.logical_shards, semantics.partial_sums, semantics.objects, semantics.views, semantics.states, semantics.tokens, ops)
    local_backings = {item.backing.allocation_id: item.object_id for item in semantics.object_backings if type(item.backing) is LocalAllocationBacking}
    allocations = tuple(SramAllocation(item.allocation_id, local_backings[item.allocation_id], item.owner_core, item.offset_bytes, item.size_bytes, item.alignment_bytes) for item in source.allocations)
    external_backings = tuple(item for item in semantics.object_backings if type(item.backing) is ExternalSlotBacking)
    builder = ProgramBuilder(arch, AuthoredProgramOrigin("unit", "dma-burst-limits", 1))
    for ordinal in range(variant_count):
        variant = builder.variant("main", f"burst-{ordinal}", f"dma-burst-limits:{ordinal}", records=records, allocations=allocations, external_backings=external_backings, binding_slots=semantics.binding_slots)
        for stream in semantics.streams:
            target = variant.stream(stream.core_id, stream.physical_stream_id, stream.flags)
            command_ids = semantics.stream_command_ids[stream.command_begin:stream.command_begin + stream.command_count]
            for command_id in command_ids:
                command = semantics.command_semantics[command_id - 1]
                if type(command.source) is KernelCommandSource:
                    target.kernel_command(command.source.kernel_op_id)
                elif type(command.source) is ControlCommandSource:
                    target.control_command(command.source.attrs)
                else:
                    raise AssertionError("unexpected authored command source")
    return builder.build()


@pytest.mark.parametrize("limit", (1, 3, 8, 16))
def test_explicit_dma_burst_limit_is_verified_and_projected(limit):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    state = _pretraffic(arch, limit)

    assert verify_kernel_memory(_records(state)).records is not None
    assert verify_kernel_memory(_records(state), arch).records is not None
    program = assemble_program(verify_pretraffic_state(state, arch), arch)
    assert program.dma_descriptors[0].max_burst_beats == limit
    assert verify_program(program, arch).program is program


def test_default_dma_burst_limit_inherits_architecture_ceiling():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    state = _pretraffic(arch, None)

    assert state.semantics.kernel_ops[4].attrs.max_burst_beats is None
    assert state.transport.dma_descriptors[0].max_burst_beats == arch.axi_max_burst_beats
    assert assemble_program(verify_pretraffic_state(state, arch), arch).dma_descriptors[0].max_burst_beats == arch.axi_max_burst_beats


@pytest.mark.parametrize("limit", (True, False, 1.0, 0, -1, 257))
def test_source_verifier_rejects_invalid_dma_burst_limit(limit):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    state = _state(arch)
    op = state.semantics.kernel_ops[4]
    records = _records(replace(state, semantics=replace(state.semantics, kernel_ops=state.semantics.kernel_ops[:4] + (replace(op, attrs=replace(op.attrs, max_burst_beats=limit)),))))

    with pytest.raises(MeshIrError) as caught:
        verify_kernel_memory(records)
    assert caught.value.code == "E_ABI_BOUNDS"


@pytest.mark.parametrize("limit", (17, 256))
def test_source_only_accepts_hardware_independent_limit_but_architecture_rejects_excess(limit):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    state = _state(arch)
    op = state.semantics.kernel_ops[4]
    records = _records(replace(state, semantics=replace(state.semantics, kernel_ops=state.semantics.kernel_ops[:4] + (replace(op, attrs=replace(op.attrs, max_burst_beats=limit)),))))

    assert verify_kernel_memory(records).records is records
    with pytest.raises(MeshIrError) as caught:
        verify_kernel_memory(records, arch)
    assert caught.value.code == "E_CAPABILITY_MISMATCH"


def test_kernel_schema_omits_inherited_limit_and_serializes_explicit_limit():
    inherited = load_compute_store_kernel()
    explicit = _kernel_with_limit(inherited, 8)

    assert "max_burst_beats" not in inherited.canonical_dict()["ops"][8]["attrs"]
    assert explicit.canonical_dict()["ops"][8]["attrs"]["max_burst_beats"] == 8
    inherited.verify()
    explicit.verify()


def test_compiled_pass19_uses_source_limit_for_only_its_dma_operation():
    arch, lowering, effective = _inputs()
    source = lowering.bundle.modules[0]
    kernel = _kernel_with_limit(source, 8)
    bundle = KernelBundle.create(arch.digest().hex(), lowering.bundle.source_graph_set_sha256, (kernel,))
    with PassExecutor(1) as executor:
        program = lower_to_program(_lowering_for_bundle(bundle), arch, effective, executor).program

    group = next(item for item in program.semantics.descriptor_groups if item.kernel_op_id == 9)
    assert {program.dma_descriptors[item - 1].max_burst_beats for item in group.descriptor_ids} == {8}
    default_group = next(item for item in program.semantics.descriptor_groups if item.kernel_op_id == 11)
    assert {program.dma_descriptors[item - 1].max_burst_beats for item in default_group.descriptor_ids} == {arch.axi_max_burst_beats}
    assert verify_program(program, arch).program is program


def test_authored_read_window_preserves_eight_beat_limit_and_twenty_four_bursts():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    program = _reauthor_with_limit(arch, build_read_window_program(arch), 8)

    assert len(program.dma_descriptors) == 1
    assert program.dma_descriptors[0].useful_bytes == 6144
    assert program.dma_descriptors[0].max_burst_beats == 8
    assert program.semantics.intrinsic_traffic.descriptors[0].bursts == 24
    assert verify_program(program, arch).program is program


def test_authored_strided_and_multivariant_descriptors_keep_source_limit():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    strided = _reauthor_with_limit(arch, build_load_saturation_strided_program(arch), 3)
    multivariant = _reauthor_with_limit(arch, build_read_window_program(arch), 8, variant_count=2)

    assert len(strided.dma_descriptors) == 2
    assert {item.rows for item in strided.dma_descriptors} == {64}
    assert {item.src_stride_bytes for item in strided.dma_descriptors} == {1024}
    assert {item.max_burst_beats for item in strided.dma_descriptors} == {3}
    assert len(multivariant.semantics.variants) == 2
    assert tuple(item.descriptor_id for item in multivariant.dma_descriptors) == (1, 2)
    assert {item.max_burst_beats for item in multivariant.dma_descriptors} == {8}
    assert verify_program(strided, arch).program is strided
    assert verify_program(multivariant, arch).program is multivariant


def test_asymmetric_descriptor_spans_are_bounded_by_their_own_endpoints():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    program = build_load_saturation_strided_program(arch)
    executions, _ = derive_descriptor_execution_set(program, arch)

    for execution in executions:
        descriptor = execution.descriptor
        assert descriptor.physical_storage_bytes > descriptor.dst.backing_size_bytes
        assert calculate_descriptor_traffic(arch, execution).useful_bytes == descriptor.useful_bytes

        store = _asymmetric_store(descriptor)
        assert store.physical_storage_bytes > store.src.backing_size_bytes
        assert calculate_descriptor_traffic(arch, DescriptorExecution(store, execution.execution_count)).useful_bytes == store.useful_bytes


def test_traffic_rejects_physical_storage_smaller_than_one_endpoint_traversal():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    program = build_load_saturation_strided_program(arch)
    executions, _ = derive_descriptor_execution_set(program, arch)
    descriptor = executions[0].descriptor
    shortened = replace(descriptor, physical_storage_bytes=descriptor.useful_bytes)

    with pytest.raises(MeshIrError) as caught:
        calculate_descriptor_traffic(arch, DescriptorExecution(shortened, 1))
    assert caught.value.code == "E_TRAFFIC_MISMATCH"


@pytest.mark.parametrize("side", ("src", "dst"))
def test_traffic_rejects_each_endpoint_traversal_beyond_its_own_backing(side):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    program = build_load_saturation_strided_program(arch)
    execution, _ = derive_descriptor_execution_set(program, arch)
    descriptor = execution[0].descriptor
    if side == "src":
        descriptor = replace(_asymmetric_store(descriptor), src_stride_bytes=descriptor.src_stride_bytes)
    else:
        descriptor = replace(descriptor, dst_stride_bytes=descriptor.src_stride_bytes)

    with pytest.raises(MeshIrError) as caught:
        calculate_descriptor_traffic(arch, DescriptorExecution(descriptor, 1))
    assert caught.value.code == "E_TRAFFIC_MISMATCH"


def test_repeat_and_rebinding_preserve_source_limit_while_changing_counts_independently():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    repeated = _reauthor_with_limit(arch, build_repeat_program(arch), 8)
    read_window = _reauthor_with_limit(arch, build_read_window_program(arch), 8)

    assert len(repeated.dma_descriptors) == 1
    assert repeated.dma_descriptors[0].max_burst_beats == 8
    assert repeated.semantics.intrinsic_traffic.descriptors[0].execution_count == 3
    slot = read_window.semantics.binding_slots[0]
    rebound = replace(slot.reference_binding, allocation_offset_bytes=slot.reference_binding.allocation_offset_bytes + arch.axi_data_bytes)
    invocation = bind_invocation(read_window, arch, 1, 1, (rebound,))
    executions, _ = derive_descriptor_execution_set(read_window, arch, (rebound,), 1)
    assert executions[0].descriptor.max_burst_beats == 8
    assert read_window.semantics.intrinsic_traffic.descriptors[0].bursts == 24
    assert invocation.traffic.descriptors[0].bursts == 25


def test_local_fill_rejects_explicit_dma_burst_limit():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    program = build_fill_program(arch)
    records = _records(program)
    fill_index, fill = next((index, op) for index, op in enumerate(records.ops) if op.opcode is KernelOpcode.DMA and op.attrs.kind is DmaKind.LOCAL_FILL)

    assert fill.attrs.max_burst_beats is None
    assert verify_kernel_memory(records, arch).records is records
    ops = records.ops[:fill_index] + (replace(fill, attrs=replace(fill.attrs, max_burst_beats=1)),) + records.ops[fill_index + 1:]

    with pytest.raises(MeshIrError) as caught:
        verify_kernel_memory(replace(records, ops=ops))
    assert caught.value.code == "E_DMA_RANGE"


def test_zero_length_authored_dma_preserves_cap_completion_and_zero_traffic():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    inherited = build_zero_dma_program(arch)
    explicit = _reauthor_with_limit(arch, inherited, 8)

    assert all(op.attrs.max_burst_beats is None for op in inherited.semantics.kernel_ops if op.opcode is KernelOpcode.DMA)
    for program, limit in ((inherited, arch.axi_max_burst_beats), (explicit, 8)):
        assert verify_program(program, arch).program is program
        assert all(descriptor.max_burst_beats == (arch.axi_max_burst_beats if descriptor.kind == DmaKind.LOCAL_FILL else limit) for descriptor in program.dma_descriptors)
        assert all(descriptor.useful_bytes == 0 for descriptor in program.dma_descriptors)
        assert all(program.commands[group.command_id - 1].signal_event == group.completion_event_id for group in program.semantics.descriptor_groups)
        assert all(item.bursts == 0 and item.segments == 0 and item.useful_bytes == 0 for item in program.semantics.intrinsic_traffic.descriptors)
        assert all(channel.messages == 0 and channel.packets == 0 and channel.flits == 0 and channel.wire_bytes == 0 for item in program.semantics.intrinsic_traffic.descriptors for channel in item.channels)


@pytest.mark.parametrize("forged_limit", (4, 16))
def test_public_verifier_rejects_fresh_traffic_with_descriptor_limit_different_from_source(forged_limit):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    program = assemble_program(verify_pretraffic_state(_pretraffic(arch, 8), arch), arch)
    descriptor = replace(program.dma_descriptors[0], max_burst_beats=forged_limit)
    provisional = replace(program, dma_descriptors=(descriptor,), expected_traffic=(), semantic_sha256="")
    executions, identity = derive_descriptor_execution_set(provisional, arch)
    report = calculate_traffic(arch, identity, executions)
    semantics = replace(program.semantics, reference_binding_identity_sha256=identity, intrinsic_traffic=report)
    provisional = replace(provisional, semantics=semantics, expected_traffic=_expected_traffic(report, executions, arch))
    forged = replace(provisional, semantic_sha256=semantic_sha256(provisional.semantic_dict()))

    with pytest.raises(MeshIrError) as caught:
        verify_program(forged, arch)
    assert caught.value.code == "E_DMA_RANGE"


def test_public_verifier_rejects_fresh_nonempty_descriptor_completion_different_from_command():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    program = build_read_window_program(arch)
    descriptor = program.dma_descriptors[0]
    alternate_event = next(item.event_id for item in program.events if item.event_id != descriptor.completion_event)
    descriptor = replace(descriptor, completion_event=alternate_event)
    groups = tuple(replace(item, completion_event_id=alternate_event) if descriptor.descriptor_id in item.descriptor_ids else item for item in program.semantics.descriptor_groups)
    semantics = replace(program.semantics, descriptor_groups=groups)
    provisional = replace(program, dma_descriptors=(descriptor,), expected_traffic=(), semantics=semantics, semantic_sha256="")
    executions, identity = derive_descriptor_execution_set(provisional, arch)
    report = calculate_traffic(arch, identity, executions)
    semantics = replace(semantics, reference_binding_identity_sha256=identity, intrinsic_traffic=report)
    provisional = replace(provisional, semantics=semantics, expected_traffic=_expected_traffic(report, executions, arch))
    forged = replace(provisional, semantic_sha256=semantic_sha256(provisional.semantic_dict()))

    with pytest.raises(MeshIrError) as caught:
        verify_program(forged, arch)
    assert caught.value.code == "E_DMA_RANGE"


def test_batch_traffic_validates_architecture_once_and_preserves_report(monkeypatch):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    program = build_read_window_program(arch)
    executions, identity = derive_descriptor_execution_set(program, arch)
    second_descriptor = replace(executions[0].descriptor, identity=replace(executions[0].descriptor.identity, command_id=executions[0].descriptor.identity.command_id + 1, descriptor_id=executions[0].descriptor.identity.descriptor_id + 1))
    pair = executions + (DescriptorExecution(second_descriptor, 1),)
    expected = calculate_traffic(arch, identity, pair)
    original = traffic_module.validate_arch
    calls = []

    def counted(manifest):
        calls.append(manifest)
        return original(manifest)

    monkeypatch.setattr(traffic_module, "validate_arch", counted)
    actual = calculate_traffic(arch, identity, pair)

    assert calls == [arch]
    assert actual == expected


def test_batch_traffic_validates_architecture_once_before_rejecting_second_descriptor(monkeypatch):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    program = build_read_window_program(arch)
    executions, identity = derive_descriptor_execution_set(program, arch)
    malformed = replace(executions[0].descriptor, identity=replace(executions[0].descriptor.identity, command_id=executions[0].descriptor.identity.command_id + 1, descriptor_id=executions[0].descriptor.identity.descriptor_id + 1), useful_bytes=executions[0].descriptor.useful_bytes - 1)
    original = traffic_module.validate_arch
    calls = []

    def counted(manifest):
        calls.append(manifest)
        return original(manifest)

    monkeypatch.setattr(traffic_module, "validate_arch", counted)
    with pytest.raises(MeshIrError) as caught:
        calculate_traffic(arch, identity, executions + (DescriptorExecution(malformed, 1),))

    assert caught.value.code == "E_TRAFFIC_MISMATCH"
    assert calls == [arch]


def test_direct_descriptor_traffic_still_rejects_invalid_architecture():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    program = build_read_window_program(arch)
    executions, _ = derive_descriptor_execution_set(program, arch)
    invalid = copy.deepcopy(arch)
    invalid.fabric = replace(invalid.fabric, initiator=replace(invalid.fabric.initiator, b_reorder_transactions=1))

    with pytest.raises(MeshIrError):
        calculate_descriptor_traffic(invalid, executions[0])
