from dataclasses import replace
from pathlib import Path

import pytest
import rustworkx as rx

from mesh_ir.architecture import load_arch
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.generated import abi as A
from mesh_ir.ir.common import Engine
from mesh_ir.ir.kernel_ir import AllocAttrs, BarrierAttrs, ControlToken, KernelBundle, KernelModule, KernelOp, KernelOpcode, ViewDeclarationAttrs
from mesh_ir.ir.kernel_verify import verify_kernel_memory
from mesh_ir.model import Command, CommandWait, Event, Stream
from mesh_ir.passes.hazards import insert_hazard_dependencies_stage
from mesh_ir.passes.schedule import schedule_per_core_streams_stage
from mesh_ir.passes.static_sram import plan_static_sram_stage
from mesh_ir.scheduled.model import CommandSemantics, ComputeExecution, ControlCommandSource, ControlExecution, DmaExecution, HaltAttrs, IdSpan, KernelCommandSource, KernelTokenSource, LifecycleSource, RequestBeginAttrs, RequestEndAttrs, ScheduledDependency, ScheduledDependencyKind, ScheduledStream, StateSource
from mesh_ir.scheduled.verify import _local_memory_records, _verify_dependencies_and_lifecycle, _verify_span_partition
from tests.unit.test_gate2_kernel_ir import load_compute_store_kernel
from tests.unit.test_gate2_scheduled_dma import _state


ROOT = Path(__file__).resolve().parents[4]


def _verify_variant_memories(semantics, arch):
    for variant in semantics.variants:
        verify_kernel_memory(_local_memory_records(semantics, variant), arch)


def _arch():
    return load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml")


def _two_variant_semantics():
    arch = _arch()
    first = _state(arch).semantics
    membership = first.variants[0].membership
    second_membership = replace(
        membership,
        kernel_tensors=replace(membership.kernel_tensors, first_id=2),
        placements=replace(membership.placements, first_id=2),
        logical_shards=replace(membership.logical_shards, first_id=2),
        objects=replace(membership.objects, first_id=3),
        views=replace(membership.views, first_id=3),
        states=replace(membership.states, first_id=4),
        tokens=replace(membership.tokens, first_id=2),
        kernel_ops=replace(membership.kernel_ops, first_id=6),
    )
    tensor = replace(first.kernel_tensors[0], tensor_id=2, alias_root_tensor_id=2)
    placement = replace(first.placements[0], placement_id=2)
    shard = replace(first.logical_shards[0], shard_id=2, tensor_id=2, placement_id=2)
    objects = tuple(replace(item, object_id=item.object_id + 2, storage_tensor_id=2) for item in first.objects)
    views = tuple(replace(item, view_id=item.view_id + 2, object_id=item.object_id + 2, shard_id=2) for item in first.views)
    states = tuple(replace(item, state_id=item.state_id + 3, object_id=item.object_id + 2) for item in first.states)
    token = replace(first.tokens[0], token_id=2)
    ops = []
    for item in first.kernel_ops:
        attrs = item.attrs
        if type(attrs) is AllocAttrs:
            attrs = replace(attrs, object_id=attrs.object_id + 2)
        elif type(attrs) is ViewDeclarationAttrs:
            attrs = replace(attrs, view_id=attrs.view_id + 2)
        reads = tuple(replace(access, state_id=access.state_id + 3, view_id=access.view_id + 2) for access in item.reads)
        writes = tuple(replace(access, old_state_id=access.old_state_id + 3, new_state_id=access.new_state_id + 3, view_id=access.view_id + 2) for access in item.writes)
        ops.append(replace(item, op_id=item.op_id + 5, reads=reads, writes=writes, attrs=attrs, done_token=None if item.done_token is None else item.done_token + 1))
    variants = (
        first.variants[0],
        replace(first.variants[0], variant_id=2, profile_id=2, lineage=replace(first.variants[0].lineage, authoring_variant_id="load-2"), membership=second_membership),
    )
    return arch, replace(
        first,
        variants=variants,
        kernel_tensors=first.kernel_tensors + (tensor,),
        placements=first.placements + (placement,),
        logical_shards=first.logical_shards + (shard,),
        objects=first.objects + objects,
        views=first.views + views,
        states=first.states + states,
        tokens=first.tokens + (token,),
        kernel_ops=first.kernel_ops + tuple(ops),
    )


def _kernel_dependency_fixture():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    kernel = load_compute_store_kernel()
    state = _state(_arch())
    membership = replace(
        state.semantics.variants[0].membership,
        commands=IdSpan(1, 5),
        kernel_tensors=IdSpan(1, len(kernel.tensors)),
        computations=IdSpan(1, len(kernel.computations)),
        placements=IdSpan(1, len(kernel.placements)),
        logical_shards=IdSpan(1, len(kernel.shards)),
        partial_sums=IdSpan(1, len(kernel.partial_sums)),
        objects=IdSpan(1, len(kernel.objects)),
        views=IdSpan(1, len(kernel.views)),
        states=IdSpan(1, len(kernel.states)),
        tokens=IdSpan(1, len(kernel.tokens)),
        kernel_ops=IdSpan(1, len(kernel.ops)),
        command_semantics=IdSpan(1, 5),
        dependencies=IdSpan(1, 6),
    )
    commands = (
        Command(1, 0, 3, 0, A.ENGINE.CONTROL, A.OPCODE.REQUEST_BEGIN, signal_event=1),
        Command(2, 9, 3, 1, A.ENGINE.DMA_READ, A.OPCODE.DMA_LOAD, 0, 1, signal_event=2),
        Command(3, 10, 3, 2, A.ENGINE.VECTOR, A.OPCODE.ELEMENTWISE, 1, 1, signal_event=3),
        Command(4, 0, 3, 0, A.ENGINE.CONTROL, A.OPCODE.REQUEST_END, 2, 1, signal_event=4),
        Command(5, 0, 3, 0, A.ENGINE.CONTROL, A.OPCODE.HALT, 3, 1),
    )
    transport = replace(
        state.transport,
        streams=(Stream(3, 0, 0, 3, A.STREAM_FLAGS.IS_LIFECYCLE), Stream(3, 1, 3, 1, 0), Stream(3, 2, 4, 1, 0)),
        commands=commands,
        command_waits=(CommandWait(1), CommandWait(2), CommandWait(3), CommandWait(4)),
        events=tuple(Event(index, A.EVENT_KIND.NORMAL, producer_command_id=index) for index in range(1, 5)),
        dma_descriptors=(),
        relocations=(),
    )
    semantics = replace(
        state.semantics,
        variants=(replace(state.semantics.variants[0], membership=membership),),
        kernel_tensors=kernel.tensors,
        computations=kernel.computations,
        placements=kernel.placements,
        logical_shards=kernel.shards,
        partial_sums=kernel.partial_sums,
        objects=kernel.objects,
        views=kernel.views,
        states=kernel.states,
        tokens=kernel.tokens,
        kernel_ops=kernel.ops,
        command_semantics=(
            CommandSemantics(1, ControlCommandSource(RequestBeginAttrs()), ControlExecution()),
            CommandSemantics(2, KernelCommandSource(9), DmaExecution(1)),
            CommandSemantics(3, KernelCommandSource(10), ComputeExecution(())),
            CommandSemantics(4, ControlCommandSource(RequestEndAttrs()), ControlExecution()),
            CommandSemantics(5, ControlCommandSource(HaltAttrs()), ControlExecution()),
        ),
        dependencies=(
            ScheduledDependency(1, 1, 2, ScheduledDependencyKind.LIFECYCLE, LifecycleSource(1)),
            ScheduledDependency(2, 2, 3, ScheduledDependencyKind.KERNEL_CONTROL, KernelTokenSource(1)),
            ScheduledDependency(3, 2, 3, ScheduledDependencyKind.KERNEL_STATE, StateSource(3)),
            ScheduledDependency(4, 3, 4, ScheduledDependencyKind.KERNEL_CONTROL, KernelTokenSource(2)),
            ScheduledDependency(5, 3, 4, ScheduledDependencyKind.LIFECYCLE, LifecycleSource(1)),
            ScheduledDependency(6, 4, 5, ScheduledDependencyKind.LIFECYCLE, LifecycleSource(1)),
        ),
        streams=(
            ScheduledStream(1, 3, 0, 0, 3, A.STREAM_FLAGS.IS_LIFECYCLE),
            ScheduledStream(2, 3, 1, 3, 1, 0),
            ScheduledStream(3, 3, 2, 4, 1, 0),
        ),
        stream_command_ids=(1, 4, 5, 2, 3),
        descriptor_groups=(),
    )
    return arch, semantics, transport


def _two_compute_variant_semantics():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    kernel = load_compute_store_kernel()
    first = _state(_arch()).semantics
    counts = {
        "kernel_tensors": len(kernel.tensors),
        "computations": len(kernel.computations),
        "placements": len(kernel.placements),
        "logical_shards": len(kernel.shards),
        "partial_sums": len(kernel.partial_sums),
        "objects": len(kernel.objects),
        "views": len(kernel.views),
        "states": len(kernel.states),
        "tokens": len(kernel.tokens),
        "kernel_ops": len(kernel.ops),
    }
    first_membership = replace(first.variants[0].membership, **{field: IdSpan(1, count) for field, count in counts.items()})
    second_membership = replace(first_membership, **{field: IdSpan(count + 1, count) for field, count in counts.items()})
    tensors = tuple(replace(item, tensor_id=item.tensor_id + counts["kernel_tensors"], producer_computation_id=0 if item.producer_computation_id == 0 else item.producer_computation_id + counts["computations"], alias_root_tensor_id=item.alias_root_tensor_id + counts["kernel_tensors"]) for item in kernel.tensors)
    computations = tuple(replace(item, computation_id=item.computation_id + counts["computations"], operand_tensor_ids=tuple(value + counts["kernel_tensors"] for value in item.operand_tensor_ids), result_tensor_id=item.result_tensor_id + counts["kernel_tensors"]) for item in kernel.computations)
    placements = tuple(replace(item, placement_id=item.placement_id + counts["placements"]) for item in kernel.placements)
    shards = tuple(replace(item, shard_id=item.shard_id + counts["logical_shards"], tensor_id=item.tensor_id + counts["kernel_tensors"], placement_id=item.placement_id + counts["placements"]) for item in kernel.shards)
    objects = tuple(replace(item, object_id=item.object_id + counts["objects"], storage_tensor_id=item.storage_tensor_id + counts["kernel_tensors"]) for item in kernel.objects)
    views = tuple(replace(item, view_id=item.view_id + counts["views"], object_id=item.object_id + counts["objects"], shard_id=item.shard_id + counts["logical_shards"]) for item in kernel.views)
    states = tuple(replace(item, state_id=item.state_id + counts["states"], object_id=item.object_id + counts["objects"]) for item in kernel.states)
    tokens = tuple(replace(item, token_id=item.token_id + counts["tokens"]) for item in kernel.tokens)
    ops = []
    for item in kernel.ops:
        attrs = item.attrs
        if type(attrs) is AllocAttrs:
            attrs = replace(attrs, object_id=attrs.object_id + counts["objects"])
        elif type(attrs) is ViewDeclarationAttrs:
            attrs = replace(attrs, view_id=attrs.view_id + counts["views"])
        reads = tuple(replace(access, state_id=access.state_id + counts["states"], view_id=access.view_id + counts["views"]) for access in item.reads)
        writes = tuple(replace(access, old_state_id=access.old_state_id + counts["states"], new_state_id=access.new_state_id + counts["states"], view_id=access.view_id + counts["views"]) for access in item.writes)
        ops.append(replace(item, op_id=item.op_id + counts["kernel_ops"], computation_id=0 if item.computation_id == 0 else item.computation_id + counts["computations"], result_shard_id=0 if item.result_shard_id == 0 else item.result_shard_id + counts["logical_shards"], reads=reads, writes=writes, attrs=attrs, after_tokens=tuple(value + counts["tokens"] for value in item.after_tokens), done_token=None if item.done_token is None else item.done_token + counts["tokens"]))
    variants = (
        replace(first.variants[0], membership=first_membership),
        replace(first.variants[0], variant_id=2, profile_id=2, lineage=replace(first.variants[0].lineage, authoring_variant_id="compute-2"), membership=second_membership),
    )
    semantics = replace(
        first,
        variants=variants,
        kernel_tensors=kernel.tensors + tensors,
        computations=kernel.computations + computations,
        placements=kernel.placements + placements,
        logical_shards=kernel.shards + shards,
        partial_sums=kernel.partial_sums,
        objects=kernel.objects + objects,
        views=kernel.views + views,
        states=kernel.states + states,
        tokens=kernel.tokens + tokens,
        kernel_ops=kernel.ops + tuple(ops),
    )
    return arch, semantics, kernel.memory_records()


def _without_dependency(semantics, dependency_id):
    retained = tuple(item for item in semantics.dependencies if item.dependency_id != dependency_id)
    return replace(semantics, dependencies=tuple(replace(item, dependency_id=index) for index, item in enumerate(retained, 1)))


def test_real_data_variants_deglobalize_every_populated_memory_namespace():
    arch, semantics = _two_variant_semantics()

    for field, collection in (
        ("kernel_tensors", semantics.kernel_tensors),
        ("computations", semantics.computations),
        ("placements", semantics.placements),
        ("logical_shards", semantics.logical_shards),
        ("partial_sums", semantics.partial_sums),
        ("objects", semantics.objects),
        ("views", semantics.views),
        ("states", semantics.states),
        ("tokens", semantics.tokens),
        ("kernel_ops", semantics.kernel_ops),
    ):
        _verify_span_partition(semantics.variants, field, len(collection))
    _verify_variant_memories(semantics, arch)
    second = _local_memory_records(semantics, semantics.variants[1])

    assert second == _local_memory_records(_state(arch).semantics, _state(arch).semantics.variants[0])


def test_compute_variant_deglobalizes_computation_and_required_result_shard():
    arch, semantics, expected = _two_compute_variant_semantics()

    _verify_variant_memories(semantics, arch)
    second = _local_memory_records(semantics, semantics.variants[1])

    assert second == expected
    assert second.ops[9].result_shard_id == 2


@pytest.mark.parametrize(
    ("collection", "index", "field"),
    (
        ("kernel_tensors", 1, "producer_computation_id"),
        ("logical_shards", 1, "partial_sum_id"),
        ("states", 3, "partial_sum_id"),
        ("kernel_ops", 5, "computation_id"),
        ("kernel_ops", 5, "result_shard_id"),
        ("kernel_ops", 9, "done_token"),
    ),
)
def test_optional_global_identity_rejects_bool_instead_of_normalizing_zero(collection, index, field):
    arch, semantics = _two_variant_semantics()
    records = getattr(semantics, collection)
    forged = replace(records[index], **{field: False})
    semantics = replace(semantics, **{collection: records[:index] + (forged,) + records[index + 1:]})

    with pytest.raises(MeshIrError) as caught:
        _verify_variant_memories(semantics, arch)

    assert caught.value.code == "E_ABI_BOUNDS"


def test_real_data_variant_rejects_cross_variant_nested_identity():
    arch, semantics = _two_variant_semantics()
    forged = replace(semantics.kernel_tensors[1], alias_root_tensor_id=1)
    semantics = replace(semantics, kernel_tensors=(semantics.kernel_tensors[0], forged))

    with pytest.raises(MeshIrError) as caught:
        _verify_variant_memories(semantics, arch)

    assert caught.value.code == "E_ABI_BOUNDS"


def test_result_shard_and_span_reject_cross_variant_or_coercible_identity():
    arch, semantics = _two_variant_semantics()
    forged_op = replace(semantics.kernel_ops[5], result_shard_id=1)
    forged_semantics = replace(semantics, kernel_ops=semantics.kernel_ops[:5] + (forged_op,) + semantics.kernel_ops[6:])

    with pytest.raises(MeshIrError) as caught:
        _verify_variant_memories(forged_semantics, arch)
    assert caught.value.code == "E_ABI_BOUNDS"

    membership = replace(semantics.variants[1].membership, objects=replace(semantics.variants[1].membership.objects, first_id=False))
    variant = replace(semantics.variants[1], membership=membership)
    with pytest.raises(MeshIrError):
        _verify_span_partition((semantics.variants[0], variant), "objects", len(semantics.objects))


def test_typed_dependency_proof_keeps_distinct_reasons_for_one_edge_pair():
    arch = _arch()
    state = _state(arch)
    dependency = ScheduledDependency(4, 2, 3, ScheduledDependencyKind.LIFECYCLE, LifecycleSource(1))
    semantics = replace(state.semantics, dependencies=state.semantics.dependencies + (dependency,))

    _verify_dependencies_and_lifecycle(semantics, state.transport)

    missing = replace(semantics, dependencies=(semantics.dependencies[0], replace(semantics.dependencies[2], dependency_id=2), replace(dependency, dependency_id=3)))
    with pytest.raises(MeshIrError):
        _verify_dependencies_and_lifecycle(missing, state.transport)


@pytest.mark.parametrize("dependency_id", (2, 3, 4))
def test_actual_kernel_token_and_state_dependencies_are_independently_required(dependency_id):
    _, semantics, transport = _kernel_dependency_fixture()

    _verify_dependencies_and_lifecycle(semantics, transport)

    with pytest.raises(MeshIrError) as caught:
        _verify_dependencies_and_lifecycle(_without_dependency(semantics, dependency_id), transport)

    assert caught.value.code == "E_ABI_BOUNDS"


def test_dependency_kind_rejects_forged_source_type_and_variant():
    state = _state(_arch())
    forged_type = replace(state.semantics.dependencies[1], source=StateSource(1))
    forged_variant = replace(state.semantics.dependencies[0], source=LifecycleSource(2))

    for forged in (forged_type, forged_variant):
        semantics = replace(state.semantics, dependencies=tuple(forged if item.dependency_id == forged.dependency_id else item for item in state.semantics.dependencies))
        with pytest.raises(MeshIrError):
            _verify_dependencies_and_lifecycle(semantics, state.transport)

    semantics = replace(state.semantics, command_semantics=(state.semantics.command_semantics[0], replace(state.semantics.command_semantics[1], source=KernelCommandSource(False)), *state.semantics.command_semantics[2:]))
    with pytest.raises(MeshIrError):
        _verify_dependencies_and_lifecycle(semantics, state.transport)


def test_wait_event_must_match_typed_dependency_producer():
    state = _state(_arch())
    waits = (state.transport.command_waits[0], replace(state.transport.command_waits[1], event_id=1), state.transport.command_waits[2])
    transport = replace(state.transport, command_waits=waits)

    with pytest.raises(MeshIrError):
        _verify_dependencies_and_lifecycle(state.semantics, transport)


def test_same_stream_dma_completion_is_not_inferred_from_admission_order():
    state = _state(_arch())
    commands = tuple(replace(item, wait_count=0) if item.command_id == 3 else item for item in state.transport.commands)

    with pytest.raises(MeshIrError) as caught:
        _verify_dependencies_and_lifecycle(state.semantics, replace(state.transport, commands=commands))

    assert caught.value.code == "E_EVENT_NO_PRODUCER"


def test_worker_stream_must_drain_into_request_end_with_actual_event_sync():
    state = _state(_arch())
    flags = A.STREAM_FLAGS.IS_LIFECYCLE | A.STREAM_FLAGS.IS_LOCAL_CONTROL
    streams = (
        ScheduledStream(1, 0, 0, 0, 3, flags),
        ScheduledStream(2, 0, 1, 3, 1, 0),
    )
    semantics = replace(state.semantics, streams=streams, stream_command_ids=(1, 3, 4, 2))
    commands = tuple(replace(item, stream_id=1) if item.command_id == 2 else item for item in state.transport.commands)
    transport = replace(state.transport, streams=(Stream(0, 0, 0, 3, flags), Stream(0, 1, 3, 1, 0)), commands=commands)

    _verify_dependencies_and_lifecycle(semantics, transport)

    undrained = tuple(replace(item, wait_count=0) if item.command_id == 3 else item for item in commands)
    with pytest.raises(MeshIrError) as caught:
        _verify_dependencies_and_lifecycle(semantics, replace(transport, commands=undrained))

    assert caught.value.code in ("E_EVENT_NO_PRODUCER", "E_LIFECYCLE")


def test_lifecycle_end_uses_one_reverse_query_without_work_descendants(monkeypatch):
    _, semantics, transport = _kernel_dependency_fixture()
    boundaries = {item.command_id for item in transport.commands if item.opcode in (A.OPCODE.REQUEST_BEGIN, A.OPCODE.REQUEST_END)}
    native_ancestors = rx.ancestors
    native_descendants = rx.descendants
    ancestor_calls = []
    descendant_calls = []

    def observed_ancestors(graph, target):
        ancestor_calls.append(graph[target])
        return native_ancestors(graph, target)

    def observed_descendants(graph, source):
        descendant_calls.append(graph[source])
        return native_descendants(graph, source)

    monkeypatch.setattr(rx, "ancestors", observed_ancestors)
    monkeypatch.setattr(rx, "descendants", observed_descendants)
    _verify_dependencies_and_lifecycle(semantics, transport)
    assert ancestor_calls == [4]
    assert descendant_calls
    assert set(descendant_calls) <= boundaries


def test_hazard_and_queue_reasons_are_not_suppressed_by_existing_edge_pair():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_5x5.yaml")
    kernel = load_compute_store_kernel()
    kernel = KernelModule.create(
        arch.digest().hex(),
        kernel.source_semantic_hash,
        kernel.entrypoint,
        kernel.profile_id,
        kernel.tensors,
        kernel.computations,
        kernel.tensor_lineage,
        kernel.computation_lineage,
        kernel.placements,
        kernel.shards,
        kernel.partial_sums,
        kernel.objects,
        kernel.views,
        kernel.states,
        kernel.tokens,
        kernel.ops,
    )
    static = plan_static_sram_stage(KernelBundle.create(arch.digest().hex(), "d" * 64, (kernel,)), arch)

    hazards = insert_hazard_dependencies_stage(static)
    pair = tuple(item for item in hazards.edges if (item.source_op_id, item.target_op_id) == (9, 10))
    assert {item.kind for item in pair} >= {ScheduledDependencyKind.KERNEL_CONTROL, ScheduledDependencyKind.KERNEL_STATE, ScheduledDependencyKind.RAW}

    queue_arch = _arch()
    queue_ops = (
        KernelOp(1, 0, "barrier:1", KernelOpcode.BARRIER, 0, 0, (), (), BarrierAttrs((0,)), (), 1),
        KernelOp(2, 0, "barrier:2", KernelOpcode.BARRIER, 0, 0, (), (), BarrierAttrs((0,)), (1,), 2),
    )
    queue_kernel = KernelModule.create(queue_arch.digest().hex(), "f" * 64, "main", "queue", (), (), (), (), (), (), (), (), (), (), (ControlToken(1), ControlToken(2)), queue_ops)
    queue_static = plan_static_sram_stage(KernelBundle.create(queue_arch.digest().hex(), "e" * 64, (queue_kernel,)), queue_arch)
    queue_hazards = insert_hazard_dependencies_stage(queue_static)
    scheduled = schedule_per_core_streams_stage(queue_hazards, queue_arch)
    variant = scheduled.variants[0]
    work = {item.source.kernel_op_id: item.command_ordinal for item in variant.commands if hasattr(item.source, "kernel_op_id")}
    stream = next(item for item in variant.streams if work[1] in item.command_ordinals)
    assert stream.command_ordinals == (work[1], work[2])
