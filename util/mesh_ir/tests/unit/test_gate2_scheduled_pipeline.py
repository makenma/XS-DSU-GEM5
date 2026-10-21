from dataclasses import replace
from pathlib import Path

import pytest

import mesh_ir.passes.addresses as address_pass
from mesh_ir.architecture import load_arch
from mesh_ir.canonical import semantic_sha256
from mesh_ir.compile_config import load_compile_config_text, resolve_compile_config
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.golden_programs import build_barrier_e2e_program, build_dual_core_program
from mesh_ir.analysis.cost import WorkUnit
from mesh_ir.ir.common import DType, Engine
from mesh_ir.ir.kernel_ir import KernelBundle, KernelModule
from mesh_ir.passes.execution import ExecutionDiagnostics, PassExecutor, record_pass
from mesh_ir.passes.graph_to_kernel import KernelLoweringResult
from mesh_ir.passes.placement import LoweringDecisions
from mesh_ir.passes.scheduled import lower_to_program
from mesh_ir.scheduled.model import AuthoredProgramOrigin, AuthoredVariantLineage, ComputeExecution, ControlCommandSource, DmaExecution, KernelCommandSource, ScheduledDependencyKind, StreamOrderSource
from mesh_ir.scheduled.verify import verify_program, verify_program_kernel_correspondence
from tests.unit.test_gate2_kernel_ir import load_compute_store_kernel, local_copy_kernel
from tests.unit.test_gate2_kernel_work import _linear_accumulation_and_epilogue_kernel
from tests.unit.test_gate2_scheduled_barriers import _state as barrier_state
from tests.golden.support.stage2_repeat_fixture import build_mixed_engine_repeat_program, with_repeat_range


ROOT = Path(__file__).resolve().parents[4]
COMPILE = """
schema_version: mesh-compile-v1
entrypoints: [forward]
shape_profiles:
  forward:
    - {profile_id: p4}
symbol_bindings: {}
parallelism: {tensor_parallel: 1, pipeline_parallel: 1, expert_parallel: 1, data_parallel: 1}
placement: {core_order: row_major_yx, allowed_cores: [3], reserve_cores: []}
tiling: {gemm_m: 8, gemm_n: 8, gemm_k: 8, double_buffer: true}
collectives: {all_reduce_algorithm: ring, chunk_bytes: 256}
runtime_model: {mode: FULL_TIMING, tensor_data: DIGEST_ONLY}
"""


def _inputs():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    source = load_compute_store_kernel()
    kernel = KernelModule.create(arch.digest().hex(), source.source_semantic_hash, source.entrypoint, source.profile_id, source.tensors, source.computations, source.tensor_lineage, source.computation_lineage, source.placements, source.shards, source.partial_sums, source.objects, source.views, source.states, source.tokens, source.ops)
    bundle = KernelBundle.create(arch.digest().hex(), "d" * 64, (kernel,))
    names = ("PlaceOpsAndTensors", "ShardAndPadTensors", "TileKernels", "BufferizeAndAlias", "LowerCollectives", "InsertDataMovement")
    current = "a" * 64
    records = []
    for index, name in enumerate(names):
        output = bundle.semantic_sha256 if index == len(names) - 1 else f"{index + 1:064x}"
        records.append(record_pass(name, current, output, 0))
        current = output
    lowering = KernelLoweringResult(bundle, tuple(records), LoweringDecisions((), ()), ExecutionDiagnostics(1, 0, 0, ()))
    effective = resolve_compile_config(load_compile_config_text(COMPILE, arch), arch)
    return arch, lowering, effective


def _lowering_for_bundle(bundle):
    names = ("PlaceOpsAndTensors", "ShardAndPadTensors", "TileKernels", "BufferizeAndAlias", "LowerCollectives", "InsertDataMovement")
    current = "a" * 64
    records = []
    for index, name in enumerate(names):
        output = bundle.semantic_sha256 if index == len(names) - 1 else f"{index + 1:064x}"
        records.append(record_pass(name, current, output, 0))
        current = output
    return KernelLoweringResult(bundle, tuple(records), LoweringDecisions((), ()), ExecutionDiagnostics(1, 0, 0, ()))


def _fresh_program(program, **changes):
    provisional = replace(program, semantic_sha256="", **changes)
    return replace(provisional, semantic_sha256=semantic_sha256(provisional.semantic_dict()))


def test_public_passes_15_through_21_execute_real_kernel_to_complete_program():
    arch, lowering, effective = _inputs()

    with PassExecutor(1) as executor:
        result = lower_to_program(lowering, arch, effective, executor)

    assert tuple(item.name for item in result.passes) == (
        "PlanStaticSRAM",
        "InsertHazardDependencies",
        "SchedulePerCoreStreams",
        "LowerDmaToSegments",
        "BindAddressesAndRelocations",
        "VerifyScheduledIR",
        "ComputeExpectedTraffic",
    )
    assert result.passes[0].input_hash == lowering.bundle.semantic_sha256
    assert result.passes[-1].output_hash == result.program.semantic_sha256
    assert result.passes[4].output_hash == result.passes[5].input_hash == result.passes[5].output_hash == result.passes[6].input_hash
    assert len(result.program.dma_descriptors) == 2
    assert len(result.program.semantics.intrinsic_traffic.descriptors) == 2
    assert len(result.program.op_attrs) == 1
    assert next(item for item in result.program.commands if item.opcode == A.OPCODE.ELEMENTWISE).attr_index == 1
    assert verify_program(result.program, arch).program is result.program
    assert verify_program_kernel_correspondence(result.program, lowering.bundle) is None


@pytest.mark.parametrize("source", ("compiled", "authored"))
def test_descriptor_projection_does_not_construct_an_incomplete_program_or_dummy_traffic(source, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("pass 19 constructed an incomplete Program or dummy traffic")

    monkeypatch.setattr(address_pass, "Program", forbidden, raising=False)
    monkeypatch.setattr(address_pass, "calculate_traffic", forbidden, raising=False)
    if source == "authored":
        arch, program = build_mixed_engine_repeat_program()
    else:
        arch, lowering, effective = _inputs()
        with PassExecutor(1) as executor:
            program = lower_to_program(lowering, arch, effective, executor).program

    assert verify_program(program, arch).program is program


@pytest.fixture(scope="module")
def local_copy_program():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    source = local_copy_kernel()
    kernel = KernelModule.create(arch.digest().hex(), source.source_semantic_hash, source.entrypoint, source.profile_id, source.tensors, source.computations, source.tensor_lineage, source.computation_lineage, source.placements, source.shards, source.partial_sums, source.objects, source.views, source.states, source.tokens, source.ops)
    bundle = KernelBundle.create(arch.digest().hex(), "d" * 64, (kernel,))
    lowering = _lowering_for_bundle(bundle)
    effective = resolve_compile_config(load_compile_config_text(COMPILE, arch), arch)
    with PassExecutor(1) as executor:
        program = lower_to_program(lowering, arch, effective, executor).program
    return arch, program


def test_local_copy_projects_as_exact_vector_work_without_dma_or_network_traffic(local_copy_program):
    arch, program = local_copy_program

    semantic = next(item for item in program.semantics.command_semantics if type(item.source) is KernelCommandSource and item.source.kernel_op_id == 5)
    command = program.commands[semantic.command_id - 1]
    attr = program.op_attrs[command.attr_index - 1]
    assert command.opcode == A.OPCODE.ELEMENTWISE
    assert command.engine == int(Engine.VECTOR)
    assert attr.kind == A.ATTR_KIND.ELEMENTWISE_V1
    assert attr.payload == (4, int(DType.FP32), 0, 1, 0)
    assert type(semantic.execution) is ComputeExecution
    assert tuple((item.engine, item.unit, item.dtype, item.operations) for phase in semantic.execution.phases for item in phase.work) == ((Engine.VECTOR, WorkUnit.COPY, DType.FP32, 4),)
    assert program.dma_descriptors == ()
    assert program.semantics.descriptor_groups == ()
    assert program.semantics.endpoint_uses == ()
    assert program.semantics.intrinsic_traffic.descriptors == ()
    assert len(program.semantics.intrinsic_traffic.aggregates) == 1
    assert all(item.static_descriptors == 0 and item.descriptor_executions == 0 and item.useful_bytes == 0 and item.physical_beat_bytes == 0 and item.packets == 0 and item.flits == 0 and item.wire_bytes == 0 and item.hop_wire_bytes == 0 for item in program.semantics.intrinsic_traffic.aggregates)
    assert verify_program(program, arch).program is program


@pytest.mark.parametrize("corruption", ("opcode", "engine", "attr", "execution"))
def test_local_copy_verifier_rejects_forged_abi_or_execution_projection(local_copy_program, corruption):
    arch, program = local_copy_program
    semantic_index = next(index for index, item in enumerate(program.semantics.command_semantics) if type(item.source) is KernelCommandSource and item.source.kernel_op_id == 5)
    semantic = program.semantics.command_semantics[semantic_index]
    command_index = semantic.command_id - 1
    command = program.commands[command_index]
    if corruption == "opcode":
        commands = program.commands[:command_index] + (replace(command, opcode=A.OPCODE.DMA_LOAD),) + program.commands[command_index + 1:]
        forged = _fresh_program(program, commands=commands)
    elif corruption == "engine":
        commands = program.commands[:command_index] + (replace(command, engine=A.ENGINE.DMA_READ),) + program.commands[command_index + 1:]
        forged = _fresh_program(program, commands=commands)
    elif corruption == "attr":
        attr_index = command.attr_index - 1
        attr = replace(program.op_attrs[attr_index], payload=(4, int(DType.FP32), 0, 2, 0))
        forged = _fresh_program(program, op_attrs=program.op_attrs[:attr_index] + (attr,) + program.op_attrs[attr_index + 1:])
    else:
        semantics = replace(program.semantics, command_semantics=program.semantics.command_semantics[:semantic_index] + (replace(semantic, execution=DmaExecution(0)),) + program.semantics.command_semantics[semantic_index + 1:])
        forged = _fresh_program(program, semantics=semantics)

    with pytest.raises(MeshIrError) as caught:
        verify_program(forged, arch)
    assert caught.value.code == ("E_ENGINE_MISMATCH" if corruption == "engine" else "E_ABI_ENUM")


@pytest.mark.parametrize("passes", [(), [], None, (None,)])
def test_public_pipeline_rejects_invalid_kernel_pass_chain_without_leaking_builtin_errors(passes):
    arch, lowering, effective = _inputs()

    with PassExecutor(1) as executor, pytest.raises(MeshIrError) as caught:
        lower_to_program(replace(lowering, passes=passes), arch, effective, executor)

    assert caught.value.code == "E_CONFIG"


def test_compiled_correspondence_rejects_fresh_program_hash_with_changed_source_fact():
    arch, lowering, effective = _inputs()
    with PassExecutor(1) as executor:
        program = lower_to_program(lowering, arch, effective, executor).program
    tensor = replace(program.semantics.kernel_tensors[0], name="forged")
    semantics = replace(program.semantics, kernel_tensors=(tensor,) + program.semantics.kernel_tensors[1:])
    provisional = replace(program, semantics=semantics, semantic_sha256="")
    forged = replace(provisional, semantic_sha256=semantic_sha256(provisional.semantic_dict()))

    with pytest.raises(MeshIrError) as caught:
        verify_program_kernel_correspondence(forged, lowering.bundle)

    assert caught.value.code == "E_ABI_CHECKSUM"


def test_public_verifier_rejects_fresh_hash_with_forged_command_execution_projection():
    arch, lowering, effective = _inputs()
    with PassExecutor(1) as executor:
        program = lower_to_program(lowering, arch, effective, executor).program
    index = next(index for index, item in enumerate(program.semantics.command_semantics) if hasattr(item.execution, "phases") and item.execution.phases)
    command_semantic = program.semantics.command_semantics[index]
    command_semantic = replace(command_semantic, execution=replace(command_semantic.execution, phases=()))
    semantics = replace(program.semantics, command_semantics=program.semantics.command_semantics[:index] + (command_semantic,) + program.semantics.command_semantics[index + 1:])
    provisional = replace(program, semantics=semantics, semantic_sha256="")
    forged = replace(provisional, semantic_sha256=semantic_sha256(provisional.semantic_dict()))

    with pytest.raises(MeshIrError) as caught:
        verify_program(forged, arch)

    assert caught.value.code == "E_ABI_ENUM"


@pytest.mark.parametrize("change", ("owner", "space", "compute_operand"))
def test_standalone_verifier_rejects_forged_physical_projection(change):
    arch, lowering, effective = _inputs()
    with PassExecutor(1) as executor:
        program = lower_to_program(lowering, arch, effective, executor).program
    if change == "compute_operand":
        command = next(item for item in program.commands if item.opcode == A.OPCODE.ELEMENTWISE)
        operand_index = command.operand_begin
        operands = program.command_operands[:operand_index] + (replace(program.command_operands[operand_index], allocation_id=0),) + program.command_operands[operand_index + 1:]
        forged = _fresh_program(program, command_operands=operands)
    else:
        allocation = program.allocations[0]
        replacement = replace(allocation, owner_core=arch.core_ids[1]) if change == "owner" else replace(allocation, memory_space=A.MEMORY_SPACE.HBM)
        forged = _fresh_program(program, allocations=(replacement,) + program.allocations[1:])

    with pytest.raises(MeshIrError) as caught:
        verify_program(forged, arch)

    assert caught.value.code == "E_ABI_BOUNDS"


def test_standalone_verifier_rejects_unordered_overlapping_local_allocations():
    arch, _, effective = _inputs()
    source = _linear_accumulation_and_epilogue_kernel()
    module = KernelModule.create(arch.digest().hex(), source.source_semantic_hash, "forward", "p4", source.tensors, source.computations, source.tensor_lineage, source.computation_lineage, source.placements, source.shards, source.partial_sums, source.objects, source.views, source.states, source.tokens, source.ops)
    bundle = KernelBundle.create(arch.digest().hex(), "e" * 64, (module,))
    with PassExecutor(1) as executor:
        program = lower_to_program(_lowering_for_bundle(bundle), arch, effective, executor).program
    assert program.allocations[1].offset_bytes == program.allocations[2].offset_bytes
    allocations = program.allocations[:1] + (replace(program.allocations[1], offset_bytes=0),) + program.allocations[2:]
    forged = _fresh_program(program, allocations=allocations)

    with pytest.raises(MeshIrError) as caught:
        verify_program(forged, arch)

    assert caught.value.code == "E_SRAM_OOM"


def test_public_pipeline_globalizes_two_data_variants_without_cross_variant_aliases():
    arch, lowering, _ = _inputs()
    first = lowering.bundle.modules[0]
    second = KernelModule.create(arch.digest().hex(), first.source_semantic_hash, first.entrypoint, "p5", first.tensors, first.computations, first.tensor_lineage, first.computation_lineage, first.placements, first.shards, first.partial_sums, first.objects, first.views, first.states, first.tokens, first.ops)
    bundle = KernelBundle.create(arch.digest().hex(), lowering.bundle.source_graph_set_sha256, (first, second))
    config = COMPILE.replace("- {profile_id: p4}", "- {profile_id: p4}\n    - {profile_id: p5}")
    effective = resolve_compile_config(load_compile_config_text(config, arch), arch)

    with PassExecutor(1) as executor:
        program = lower_to_program(_lowering_for_bundle(bundle), arch, effective, executor).program

    assert len(program.semantics.variants) == 2
    assert len(program.dma_descriptors) == 4
    assert program.semantics.variants[1].membership.kernel_ops.first_id == len(first.ops) + 1
    assert program.semantics.variants[1].membership.views.first_id == len(first.views) + 1
    assert verify_program_kernel_correspondence(program, bundle) is None

    second_variant = program.semantics.variants[1]
    backing_index = next(index for index in range(second_variant.membership.object_backings.first_id - 1, second_variant.membership.object_backings.first_id - 1 + second_variant.membership.object_backings.count) if hasattr(program.semantics.object_backings[index].backing, "allocation_id"))
    backing = program.semantics.object_backings[backing_index]
    first_allocation = program.semantics.variants[0].membership.allocations.first_id
    semantics = replace(program.semantics, object_backings=program.semantics.object_backings[:backing_index] + (replace(backing, backing=replace(backing.backing, allocation_id=first_allocation)),) + program.semantics.object_backings[backing_index + 1:])
    with pytest.raises(MeshIrError) as caught:
        verify_program(_fresh_program(program, semantics=semantics), arch)
    assert caught.value.code == "E_ABI_BOUNDS"


def test_public_program_rejects_duplicate_authored_variant_lineage():
    arch, lowering, _ = _inputs()
    first = lowering.bundle.modules[0]
    second = KernelModule.create(arch.digest().hex(), first.source_semantic_hash, first.entrypoint, "p5", first.tensors, first.computations, first.tensor_lineage, first.computation_lineage, first.placements, first.shards, first.partial_sums, first.objects, first.views, first.states, first.tokens, first.ops)
    bundle = KernelBundle.create(arch.digest().hex(), lowering.bundle.source_graph_set_sha256, (first, second))
    config = COMPILE.replace("- {profile_id: p4}", "- {profile_id: p4}\n    - {profile_id: p5}")
    effective = resolve_compile_config(load_compile_config_text(config, arch), arch)
    with PassExecutor(1) as executor:
        program = lower_to_program(_lowering_for_bundle(bundle), arch, effective, executor).program
    variants = tuple(replace(item, lineage=AuthoredVariantLineage("duplicate")) for item in program.semantics.variants)
    semantics = replace(program.semantics, origin=AuthoredProgramOrigin("unit", "duplicate", 1), variants=variants)

    with pytest.raises(MeshIrError) as caught:
        verify_program(_fresh_program(program, semantics=semantics), arch)

    assert caught.value.code == "E_ABI_DUPLICATE"


def test_public_pipeline_drains_independent_compute_tail_before_end():
    arch, _, effective = _inputs()
    source = _linear_accumulation_and_epilogue_kernel()
    module = KernelModule.create(arch.digest().hex(), source.source_semantic_hash, "forward", "p4", source.tensors, source.computations, source.tensor_lineage, source.computation_lineage, source.placements, source.shards, source.partial_sums, source.objects, source.views, source.states, source.tokens, source.ops)
    bundle = KernelBundle.create(arch.digest().hex(), "e" * 64, (module,))

    with PassExecutor(1) as executor:
        program = lower_to_program(_lowering_for_bundle(bundle), arch, effective, executor).program

    end = next(item for item in program.commands if item.opcode == A.OPCODE.REQUEST_END)
    work = tuple(item for item in program.commands if item.source_op_id)
    assert not program.dma_descriptors
    assert all(any(wait.event_id == command.signal_event for wait in program.command_waits[end.wait_begin:end.wait_begin + end.wait_count]) for command in work if not any(dependency.source_command_id == command.command_id and dependency.target_command_id in {item.command_id for item in work} and dependency.kind.value != "STREAM_ORDER" for dependency in program.semantics.dependencies))
    assert verify_program(program, arch).program is program


def test_public_pipeline_keeps_successive_equal_barriers_as_distinct_joins():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    source = barrier_state(arch).semantics
    module = KernelModule.create(arch.digest().hex(), "1" * 64, "forward", "p4", source.kernel_tensors, source.computations, (), (), source.placements, source.logical_shards, source.partial_sums, source.objects, source.views, source.states, source.tokens, source.kernel_ops)
    bundle = KernelBundle.create(arch.digest().hex(), "2" * 64, (module,))
    config = COMPILE.replace("allowed_cores: [3]", "allowed_cores: [0, 1]")
    effective = resolve_compile_config(load_compile_config_text(config, arch), arch)

    with PassExecutor(1) as executor:
        program = lower_to_program(_lowering_for_bundle(bundle), arch, effective, executor).program

    assert tuple((item.barrier_group_id, item.completion_event_id) for item in program.semantics.barrier_groups) == ((1, 3), (2, 4))
    assert tuple(item.expected_arrivals for item in program.events if item.kind == A.EVENT_KIND.BARRIER) == (2, 2)
    assert verify_program(program, arch).program is program


@pytest.fixture(scope="module")
def command_diagnostic_programs():
    arch, lowering, effective = _inputs()
    with PassExecutor(1) as executor:
        compiled = lower_to_program(lowering, arch, effective, executor).program
    authored_arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")
    dual = build_dual_core_program(authored_arch)
    barrier = build_barrier_e2e_program(authored_arch)
    assert verify_program(compiled, arch).program is compiled
    assert verify_program(dual, authored_arch).program is dual
    assert verify_program(barrier, authored_arch).program is barrier
    return {
        "control": (arch, compiled),
        "dma": (arch, compiled),
        "compute": (arch, compiled),
        "recv_wait": (authored_arch, dual),
        "barrier": (authored_arch, barrier),
    }


@pytest.mark.parametrize(
    ("case", "opcode", "wrong_engine"),
    (
        ("control", A.OPCODE.REQUEST_BEGIN, A.ENGINE.DMA_READ),
        ("dma", A.OPCODE.DMA_LOAD, A.ENGINE.DMA_WRITE),
        ("compute", A.OPCODE.ELEMENTWISE, A.ENGINE.REDUCE),
        ("recv_wait", A.OPCODE.RECV_WAIT, A.ENGINE.DMA_READ),
        ("barrier", A.OPCODE.BARRIER, A.ENGINE.DMA_READ),
    ),
)
def test_valid_numeric_command_engine_mismatch_has_dedicated_diagnostic(command_diagnostic_programs, case, opcode, wrong_engine):
    arch, program = command_diagnostic_programs[case]
    command = next(item for item in program.commands if item.opcode == opcode)
    commands = (*program.commands[:command.command_id - 1], replace(command, engine=wrong_engine), *program.commands[command.command_id:])

    with pytest.raises(MeshIrError) as caught:
        verify_program(_fresh_program(program, commands=commands), arch)

    assert caught.value.code == "E_ENGINE_MISMATCH"


def test_physical_stream_order_has_exact_typed_identity_and_cannot_replace_completion(command_diagnostic_programs):
    arch, program = command_diagnostic_programs["control"]
    order = next(item for item in program.semantics.dependencies if item.kind is ScheduledDependencyKind.STREAM_ORDER)
    assert type(order.source) is StreamOrderSource
    assert order.source.stream_id == program.semantics.variants[0].lifecycle_stream_id

    end = next(item for item in program.commands if item.opcode == A.OPCODE.REQUEST_END)
    commands = program.commands[:end.command_id - 1] + (replace(end, wait_count=0),) + program.commands[end.command_id:]
    with pytest.raises(MeshIrError) as caught:
        verify_program(_fresh_program(program, commands=commands), arch)
    assert caught.value.code in ("E_EVENT_NO_PRODUCER", "E_LIFECYCLE")


def test_stream_order_boolean_is_rejected_during_semantic_hashing(command_diagnostic_programs):
    _, program = command_diagnostic_programs["control"]
    order = next(item for item in program.semantics.dependencies if item.kind is ScheduledDependencyKind.STREAM_ORDER)
    dependency_index = order.dependency_id - 1
    forged_dependency = replace(order, source=StreamOrderSource(False))
    semantics = replace(program.semantics, dependencies=(*program.semantics.dependencies[:dependency_index], forged_dependency, *program.semantics.dependencies[dependency_index + 1:]))

    with pytest.raises(MeshIrError) as caught:
        _fresh_program(program, semantics=semantics)

    assert caught.value.code == "E_ABI_BOUNDS"
    assert caught.value.message == "semantic integer is out of range"
    assert caught.value.context == {"field": "stream_id"}


def test_wrong_valid_stream_order_identity_reaches_domain_verification(command_diagnostic_programs):
    arch, program = command_diagnostic_programs["control"]
    order = next(item for item in program.semantics.dependencies if item.kind is ScheduledDependencyKind.STREAM_ORDER)
    wrong_stream_id = next(item.stream_id for item in program.semantics.streams if item.stream_id != order.source.stream_id)
    dependency_index = order.dependency_id - 1
    forged_dependency = replace(order, source=StreamOrderSource(wrong_stream_id))
    semantics = replace(program.semantics, dependencies=(*program.semantics.dependencies[:dependency_index], forged_dependency, *program.semantics.dependencies[dependency_index + 1:]))

    with pytest.raises(MeshIrError) as caught:
        verify_program(_fresh_program(program, semantics=semantics), arch)

    assert caught.value.code == "E_STREAM_CONTRACT"
    assert caught.value.message == "stream dependency contradicts stream ownership"


def test_command_engine_boolean_remains_malformed_enum_storage(command_diagnostic_programs):
    arch, program = command_diagnostic_programs["dma"]
    command = next(item for item in program.commands if item.opcode == A.OPCODE.DMA_LOAD)
    commands = (*program.commands[:command.command_id - 1], replace(command, engine=True), *program.commands[command.command_id:])

    with pytest.raises(MeshIrError) as caught:
        verify_program(_fresh_program(program, commands=commands), arch)

    assert caught.value.code == "E_ABI_ENUM"
    assert caught.value.message == "command engine or opcode is not an exact integer"


def test_stream_order_source_cannot_cross_variants():
    arch, lowering, _ = _inputs()
    first = lowering.bundle.modules[0]
    second = KernelModule.create(arch.digest().hex(), first.source_semantic_hash, first.entrypoint, "p5", first.tensors, first.computations, first.tensor_lineage, first.computation_lineage, first.placements, first.shards, first.partial_sums, first.objects, first.views, first.states, first.tokens, first.ops)
    bundle = KernelBundle.create(arch.digest().hex(), lowering.bundle.source_graph_set_sha256, (first, second))
    effective = resolve_compile_config(load_compile_config_text(COMPILE.replace("- {profile_id: p4}", "- {profile_id: p4}\n    - {profile_id: p5}"), arch), arch)
    with PassExecutor(1) as executor:
        program = lower_to_program(_lowering_for_bundle(bundle), arch, effective, executor).program
    first_variant, second_variant = program.semantics.variants
    index = next(index for index, item in enumerate(program.semantics.dependencies) if item.kind is ScheduledDependencyKind.STREAM_ORDER and first_variant.membership.dependencies.first_id <= item.dependency_id < first_variant.membership.dependencies.first_id + first_variant.membership.dependencies.count)
    dependency = replace(program.semantics.dependencies[index], source=StreamOrderSource(second_variant.lifecycle_stream_id))
    semantics = replace(program.semantics, dependencies=program.semantics.dependencies[:index] + (dependency,) + program.semantics.dependencies[index + 1:])

    with pytest.raises(MeshIrError) as caught:
        verify_program(_fresh_program(program, semantics=semantics), arch)

    assert caught.value.code == "E_STREAM_CONTRACT"


def test_genuine_mixed_engine_repeat_scales_actual_load_and_store_descriptors():
    arch, program = build_mixed_engine_repeat_program()

    verified = verify_program(program, arch)

    assert verified.program is program
    assert {item.engine for item in program.commands} >= {A.ENGINE.CONTROL, A.ENGINE.DMA_READ, A.ENGINE.DMA_WRITE, A.ENGINE.VECTOR}
    assert tuple(item.execution_count for item in program.semantics.intrinsic_traffic.descriptors) == (3, 3)
    assert tuple(item.useful_bytes for item in program.semantics.intrinsic_traffic.descriptors) == (48, 48)


def test_repeat_rejects_mutable_state_produced_before_the_body():
    arch, program = build_mixed_engine_repeat_program()
    forged = with_repeat_range(program, arch, 2, 2)

    with pytest.raises(MeshIrError) as caught:
        verify_program(forged, arch)

    assert caught.value.code == "E_ABI_BOUNDS"
