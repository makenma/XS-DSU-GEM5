from __future__ import annotations

from dataclasses import dataclass
from mesh_ir.analysis.cost import ElementWorkDomain, Engine, MatrixAccumulationWorkDomain, MatrixEpilogueWorkDomain, MatrixWorkDomain, RowWorkDomain, estimate_work_phases
from mesh_ir.analysis.regions import flat_interval_regions, merge_spans, region_byte_spans
from mesh_ir.canonical import checked_add_u64, checked_mul_u64
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import DmaKind, INVALID_CORE_ID, Layout, MemorySpace, TensorRole
from mesh_ir.ir.graph_ir import GraphModule, OpCode, ReduceAttrs
from mesh_ir.ir.kernel_ir import AllocAttrs, BufferObject, BufferView, ControlToken, DmaAttrs, ElementRegion, GemmKernelAttrs, KernelCost, KernelModule, KernelOp, KernelOpcode, LocalCopyAttrs, LocalReduceAttrs, MatrixEpilogueAlgorithm, MatrixEpilogueKernelAttrs, MatrixPhase, MovementAlgorithm, MovementKernelAttrs, NormAlgorithm, NormKernelAttrs, OperandAccess, OperandAccessMode, RecvWaitAttrs, ReduceKind, ReductionAlgorithm, ReductionKernelAttrs, SoftmaxAlgorithm, SoftmaxKernelAttrs, StateOrigin, StateTransition, TensorState, VectorAlgorithm, VectorKernelAttrs, ViewDeclarationAttrs
from mesh_ir.passes.bufferize import PlannedBufferAccess, PlannedBufferView, VariantBuffers, project_mapped_region_pieces
from mesh_ir.passes.collectives import CollectiveState, VariantCollectives
from mesh_ir.passes.collective_geometry import CollectivePhase
from mesh_ir.passes.execution import PassExecutor, PassTask
from mesh_ir.passes.placement import LoweringDecisions, PlacementCandidate, PlacementDecision
from mesh_ir.passes.placement_geometry import DenseLogicalRegion, GeometryPhase, OperationPlacementGeometry, PlacementTransferKind, ResidentWindowRole
from mesh_ir.passes.planning_prefix import OperationIdentity, PlanningResult
from mesh_ir.passes.tiling import PlannedKernelTile


_MATRIX = frozenset((OpCode.MATMUL, OpCode.BMM, OpCode.LINEAR_BIAS))
_ROW = frozenset((OpCode.REDUCE_SUM, OpCode.REDUCE_MAX, OpCode.REDUCE_MEAN, OpCode.SOFTMAX, OpCode.LAYERNORM, OpCode.RMSNORM))
_EXTERNAL_SOURCE_ROLES = frozenset((TensorRole.INPUT, TensorRole.WEIGHT, TensorRole.CONSTANT, TensorRole.STATE, TensorRole.KV_CACHE))


@dataclass(frozen=True)
class _MovementVariantTask:
    graph: GraphModule
    geometries: tuple[OperationPlacementGeometry, ...]
    buffers: VariantBuffers
    collective: VariantCollectives
    tiles: tuple[PlannedKernelTile, ...]


def _product(values: tuple[int, ...], field: str) -> int:
    result = 1
    for value in values:
        result = checked_mul_u64(result, value, field)
    return result


def _aggregate(phases) -> tuple[int, int, int]:
    totals = {Engine.TENSOR: 0, Engine.VECTOR: 0, Engine.REDUCE: 0}
    for phase in phases:
        for work in phase.work:
            totals[work.engine] = checked_add_u64(totals[work.engine], work.operations, "tile work")
    return totals[Engine.TENSOR], totals[Engine.VECTOR], totals[Engine.REDUCE]


def _cost(input_bytes: int, output_bytes: int, phases) -> KernelCost:
    macs, vector, reduction = _aggregate(phases)
    return KernelCost(input_bytes, output_bytes, checked_add_u64(input_bytes, output_bytes, "tile local storage bytes"), macs, vector, reduction)


def _row_domain(op, source_shape: tuple[int, ...]) -> RowWorkDomain:
    axes = (op.attrs.axis,) if op.opcode is OpCode.SOFTMAX else op.attrs.axes
    axes = tuple(axis if axis >= 0 else axis + len(source_shape) for axis in axes)
    rows = _product(tuple(extent for index, extent in enumerate(source_shape) if index not in axes), "tile row count")
    fan_in = _product(tuple(source_shape[index] for index in axes), "tile row fan-in")
    return RowWorkDomain(rows, fan_in)


def _attrs(op, tile, matrix_phase, partial_sum_id, operand_dtypes, result_dtype, source_shape, input_bytes, output_bytes):
    if op.opcode in _MATRIX:
        if matrix_phase is MatrixPhase.DIRECT:
            domain = MatrixWorkDomain(tile.valid_batch, tile.valid_m, tile.valid_n, tile.valid_k)
            dtypes = operand_dtypes
        else:
            domain = MatrixAccumulationWorkDomain(tile.valid_batch, tile.valid_m, tile.valid_n, tile.valid_k)
            dtypes = operand_dtypes[:2]
        phases = estimate_work_phases(op.opcode, op.attrs, dtypes, result_dtype, domain)
        return GemmKernelAttrs(op.opcode, op.attrs, tile, _cost(input_bytes, output_bytes, phases), matrix_phase, partial_sum_id)
    if op.opcode in _ROW:
        domain = _row_domain(op, source_shape)
    else:
        domain = ElementWorkDomain(output_bytes // result_dtype.byte_width)
    phases = estimate_work_phases(op.opcode, op.attrs, operand_dtypes, result_dtype, domain)
    cost = _cost(input_bytes, output_bytes, phases)
    if op.opcode in (OpCode.ADD, OpCode.SUB, OpCode.MUL, OpCode.DIV, OpCode.RELU, OpCode.GELU, OpCode.SILU, OpCode.EXP, OpCode.RSQRT, OpCode.EMBEDDING_LOOKUP):
        algorithm = VectorAlgorithm.EMBEDDING_GATHER if op.opcode is OpCode.EMBEDDING_LOOKUP else VectorAlgorithm.ELEMENTWISE
        return VectorKernelAttrs(op.opcode, op.attrs, tile, cost, algorithm)
    if op.opcode in (OpCode.CONTIGUOUS_COPY, OpCode.CONCAT, OpCode.GATHER_ROWS):
        algorithm = MovementAlgorithm.CONCAT if op.opcode is OpCode.CONCAT else MovementAlgorithm.GATHER_ROWS if op.opcode is OpCode.GATHER_ROWS else MovementAlgorithm.STRIDED_COPY
        return MovementKernelAttrs(op.opcode, op.attrs, tile, cost, algorithm)
    if op.opcode in (OpCode.REDUCE_SUM, OpCode.REDUCE_MAX, OpCode.REDUCE_MEAN):
        return ReductionKernelAttrs(op.opcode, op.attrs, tile, cost, ReductionAlgorithm.LEFT_TO_RIGHT)
    if op.opcode is OpCode.SOFTMAX:
        return SoftmaxKernelAttrs(op.attrs, tile, cost, SoftmaxAlgorithm.STABLE_MAX_SUM)
    if op.opcode in (OpCode.LAYERNORM, OpCode.RMSNORM):
        algorithm = NormAlgorithm.LAYER_NORM if op.opcode is OpCode.LAYERNORM else NormAlgorithm.RMS_NORM
        return NormKernelAttrs(op.opcode, op.attrs, tile, cost, algorithm)
    raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "source operation has no physical Kernel family", op_id=op.op_id)


def _kernel_opcode(opcode: OpCode) -> KernelOpcode:
    if opcode in (OpCode.MATMUL, OpCode.LINEAR_BIAS):
        return KernelOpcode.GEMM
    if opcode is OpCode.BMM:
        return KernelOpcode.BMM
    if opcode in (OpCode.ADD, OpCode.SUB, OpCode.MUL, OpCode.DIV, OpCode.RELU, OpCode.GELU, OpCode.SILU, OpCode.EXP, OpCode.RSQRT, OpCode.EMBEDDING_LOOKUP):
        return KernelOpcode.VECTOR
    if opcode in (OpCode.CONTIGUOUS_COPY, OpCode.CONCAT, OpCode.GATHER_ROWS):
        return KernelOpcode.DATA_MOVEMENT
    if opcode in (OpCode.REDUCE_SUM, OpCode.REDUCE_MAX, OpCode.REDUCE_MEAN):
        return KernelOpcode.REDUCE
    if opcode is OpCode.SOFTMAX:
        return KernelOpcode.SOFTMAX
    if opcode in (OpCode.LAYERNORM, OpCode.RMSNORM):
        return KernelOpcode.NORM
    raise MeshIrError("E_EXPORT_UNSUPPORTED_OP", "source operation has no physical Kernel opcode", opcode=opcode.value)


def _lower_variant(task: _MovementVariantTask) -> KernelModule:
    graph = task.graph
    buffers = task.buffers
    collective = task.collective
    tiles = task.tiles
    function = graph.functions[0]
    geometries = []
    if len(task.geometries) != len(function.ops):
        raise MeshIrError("E_ABI_ORDER", "movement selected geometry count differs from source")
    for operation_ordinal, (op, geometry) in enumerate(zip(function.ops, task.geometries)):
        identity = OperationIdentity(graph.entrypoint, graph.profile_id, function.function_id, op.op_id)
        if type(geometry) is not OperationPlacementGeometry or geometry.operation_ordinal != operation_ordinal:
            raise MeshIrError("E_ABI_ORDER", "movement selected geometry is invalid", op_id=op.op_id)
        geometries.append((identity, op, geometry))
    objects = tuple(BufferObject(item.object_id, item.storage_tensor_id, item.owner_core, item.memory_space, item.shape, item.strides, item.footprint_bytes, item.alignment_bytes, item.memory_space is not MemorySpace.CORE_SRAM, item.buffer_index) for item in buffers.objects)
    views = [BufferView(item.view_id, item.object_id, item.shard_id, item.shard_origin, item.padded_shape, item.valid_shape, item.object_offset_elements, item.object_strides, item.layout, None, item.generation) for item in buffers.views]
    view_by_id = {item.view_id: item for item in buffers.views}
    object_by_id = {item.object_id: item for item in buffers.objects}
    shard_by_id = {item.shard_id: item for item in buffers.shards}
    tensor_by_id = {item.tensor_id: item for item in buffers.tensors}
    source_value_by_tensor = {item.tensor_id: item.source_value_id for item in buffers.tensor_lineage}
    value_by_id = {item.value_id: item for item in graph.values}
    result_use = {(item.identity, item.output_tile_index, item.owner_core, item.role): item for item in buffers.result_views}
    chunks = {item.chunk_id: item for item in collective.chunks}
    partials = {item.partial_sum_id: item for item in buffers.partial_sums}
    identity_by_computation = {
        item.computation_id: OperationIdentity(graph.entrypoint, graph.profile_id, function.function_id, item.source_op_id)
        for item in buffers.computation_lineage
    }
    geometry_by_identity = {identity: geometry for identity, _, geometry in geometries}
    collective_context = {}
    for transfer in collective.transfers:
        chunk = chunks.get(transfer.chunk_id)
        partial = partials.get(transfer.partial_sum_id)
        if chunk is None or partial is None or chunk.partial_sum_id != transfer.partial_sum_id or partial.computation_id not in identity_by_computation:
            raise MeshIrError("E_ABI_BOUNDS", "collective transfer identity is invalid", transfer_id=transfer.transfer_id)
        identity = identity_by_computation[partial.computation_id]
        geometry = geometry_by_identity[identity]
        output_indices = tuple(index for index, output in enumerate(geometry.output_tiles) if output == chunk.geometry.output_tile)
        if len(output_indices) != 1:
            raise MeshIrError("E_ABI_ORDER", "collective chunk output tile is ambiguous", chunk_id=chunk.chunk_id)
        output_index = output_indices[0]
        source = result_use.get((identity, output_index, transfer.geometry.source_core, ResidentWindowRole.ACCUMULATOR))
        destination = result_use.get((identity, output_index, transfer.geometry.destination_core, ResidentWindowRole.ACCUMULATOR))
        if source is None or destination is None or source.access is None or destination.access is None:
            raise MeshIrError("E_ABI_BOUNDS", "collective transfer has no accumulator views", transfer_id=transfer.transfer_id)
        relative_regions = flat_interval_regions(chunk.geometry.output_tile.valid_shape, chunk.geometry.tile_flat_offset, chunk.geometry.element_count)
        source_accesses = tuple(PlannedBufferAccess(source.access.view_id, ElementRegion(tuple(base + relative for base, relative in zip(source.access.region.origin, region.origin)), region.shape, region.steps)) for region in relative_regions)
        destination_accesses = tuple(PlannedBufferAccess(destination.access.view_id, ElementRegion(tuple(base + relative for base, relative in zip(destination.access.region.origin, region.origin)), region.shape, region.steps)) for region in relative_regions)
        receive_window = next((item for item in geometry.resident_windows if item.role is ResidentWindowRole.COLLECTIVE_RECEIVE and item.owner_core == transfer.geometry.destination_core), None)
        receive_object = None if receive_window is None else next((item for item in buffers.objects if item.window_id == receive_window.window_id), None)
        if receive_object is None:
            raise MeshIrError("E_ABI_BOUNDS", "collective transfer has no receive object", transfer_id=transfer.transfer_id)
        destination_view = view_by_id[destination.access.view_id]
        receive_accesses = []
        offset = 0
        for region in relative_regions:
            view_id = len(views) + 1
            shard_origin = tuple(base + access_origin + relative for base, access_origin, relative in zip(destination_view.shard_origin, destination.access.region.origin, region.origin))
            strides = tuple(_product(region.shape[index + 1 :], "collective receive stride") for index in range(len(region.shape)))
            views.append(BufferView(view_id, receive_object.object_id, destination.shard_id, shard_origin, region.shape, region.shape, offset, strides, Layout.CONTIGUOUS_ROW_MAJOR, None, transfer.transfer_id))
            view_by_id[view_id] = PlannedBufferView(view_id, receive_object.object_id, shard_by_id[destination.shard_id].tensor_id, destination.shard_id, shard_origin, region.shape, region.shape, offset, strides, Layout.CONTIGUOUS_ROW_MAJOR, transfer.transfer_id)
            receive_accesses.append(PlannedBufferAccess(view_id, ElementRegion((0,) * len(region.shape), region.shape, region.steps)))
            offset = checked_add_u64(offset, _product(region.shape, "collective receive elements"), "collective receive offset")
        if checked_mul_u64(offset, chunk.geometry.accumulation_dtype.byte_width, "collective receive bytes") > receive_object.footprint_bytes:
            raise MeshIrError("E_EXPORT_LAYOUT", "collective chunk exceeds its receive object", transfer_id=transfer.transfer_id)
        collective_context[transfer.transfer_id] = (identity, output_index, source_accesses, destination_accesses, tuple(receive_accesses), source.shard_id, destination.shard_id)
    states = []
    current_state = {}
    source_tensor_ids = {tensor.tensor_id for tensor in buffers.tensors if tensor.producer_computation_id == 0 and tensor.role in _EXTERNAL_SOURCE_ROLES}
    for obj in buffers.objects:
        origin = StateOrigin.EXTERNAL if obj.memory_space is not MemorySpace.CORE_SRAM and obj.storage_tensor_id in source_tensor_ids else StateOrigin.EMPTY
        state = TensorState(len(states) + 1, obj.object_id, 0, origin, 0)
        states.append(state)
        current_state[obj.object_id] = state.state_id
    tokens = []
    declarations = []
    for obj in buffers.objects:
        declarations.append(KernelOp(len(declarations) + 1, 0, f"alloc:{obj.object_id:08d}", KernelOpcode.ALLOC, obj.owner_core, 0, (), (), AllocAttrs(obj.object_id), (), None))
    for view in views:
        declarations.append(KernelOp(len(declarations) + 1, 0, f"view:{view.view_id:08d}", KernelOpcode.VIEW, object_by_id[view.object_id].owner_core, 0, (), (), ViewDeclarationAttrs(view.view_id), (), None))
    effects = []
    reader_frontier = {}

    def emit(computation_id, label, opcode, owner, result_shard_id, read_accesses, write_accesses, attrs, after_tokens=(), partial_sum_id=None):
        reads = tuple(
            OperandAccess(current_state[view_by_id[access.view_id].object_id], access.view_id, access.region, OperandAccessMode.READ)
            for access in read_accesses
        )
        after = set(after_tokens)
        for access in read_accesses:
            view = view_by_id[access.view_id]
            after.update(transfer_ready.get((view.object_id, view.generation), ()))
        writes = ()
        write_object_id = None
        if write_accesses:
            planned = tuple(view_by_id[access.view_id] for access in write_accesses)
            object_ids = {item.object_id for item in planned}
            if len(object_ids) != 1:
                raise MeshIrError("E_ABI_BOUNDS", "one physical operation cannot define multiple buffer objects", label=label)
            object_id = object_ids.pop()
            write_object_id = object_id
            after.update(reader_frontier.get(object_id, ()))
            old_state = current_state[object_id]
            partial = shard_by_id[planned[0].shard_id].partial_sum_id if partial_sum_id is None else partial_sum_id
            new_state = TensorState(len(states) + 1, object_id, states[old_state - 1].version + 1, StateOrigin.PRODUCED, partial)
            states.append(new_state)
            current_state[object_id] = new_state.state_id
            writes = tuple(StateTransition(old_state, new_state.state_id, access.view_id, access.region) for access in write_accesses)
        token = ControlToken(len(tokens) + 1)
        tokens.append(token)
        op = KernelOp(len(declarations) + len(effects) + 1, computation_id, f"effect:{len(effects):08d}:{label}", opcode, owner, result_shard_id, reads, writes, attrs, tuple(sorted(after)), token.token_id)
        effects.append(op)
        for access in read_accesses:
            object_id = view_by_id[access.view_id].object_id
            if object_id != write_object_id:
                reader_frontier.setdefault(object_id, set()).add(token.token_id)
        if write_object_id is not None:
            reader_frontier[write_object_id] = set()
        return op

    planned_use = {
        (item.identity, item.operand_index, item.compute_tile_ordinal, item.output_tile_index, item.phase, object_by_id[view_by_id[item.access.view_id].object_id].window_id): item
        for item in buffers.window_uses
    }
    retained_window_ids = {backing.window.window_id for geometry in geometry_by_identity.values() for backing in geometry.retained_results}

    def source_access(access: PlannedBufferAccess, memory_space: MemorySpace, owner_core: int) -> PlannedBufferAccess:
        target_view = view_by_id[access.view_id]
        target_shard = shard_by_id[target_view.shard_id]
        target_origin = tuple(base + local + relative for base, local, relative in zip(target_shard.global_origin, target_view.shard_origin, access.region.origin))
        projected = []
        for candidate in buffers.views:
            obj = object_by_id[candidate.object_id]
            if candidate.tensor_id != target_view.tensor_id or obj.memory_space is not memory_space or obj.owner_core != owner_core:
                continue
            if memory_space is MemorySpace.CORE_SRAM and obj.window_id not in retained_window_ids:
                continue
            shard = shard_by_id[candidate.shard_id]
            base = tuple(origin + local for origin, local in zip(shard.global_origin, candidate.shard_origin))
            relative = tuple(origin - start for origin, start in zip(target_origin, base))
            if any(item < 0 for item in relative) or any(
                extent and origin + (extent - 1) * step >= valid
                for origin, extent, step, valid in zip(relative, access.region.shape, access.region.steps, candidate.valid_shape)
            ):
                continue
            projected.append(PlannedBufferAccess(candidate.view_id, ElementRegion(relative, access.region.shape, access.region.steps)))
        identities = {
            (
                view_by_id[item.view_id].object_id,
                view_by_id[item.view_id].object_offset_elements + sum(origin * stride for origin, stride in zip(item.region.origin, view_by_id[item.view_id].object_strides)),
                tuple(step * stride for step, stride in zip(item.region.steps, view_by_id[item.view_id].object_strides)),
                item.region.shape,
            )
            for item in projected
        }
        if not projected or len(identities) != 1:
            raise MeshIrError("E_ABI_BOUNDS", "movement source view is ambiguous", tensor_id=target_view.tensor_id, shard_id=target_view.shard_id, memory_space=memory_space.value, owner_core=owner_core)
        return projected[0]

    def mapped_accesses(access: PlannedBufferAccess, mapped) -> tuple[PlannedBufferAccess, ...]:
        view = view_by_id[access.view_id]
        shard = shard_by_id[view.shard_id]
        tensor = tensor_by_id[view.tensor_id]
        global_origin = tuple(base + local + relative for base, local, relative in zip(shard.global_origin, view.shard_origin, access.region.origin))
        if any(step != 1 for step in access.region.steps):
            raise MeshIrError("E_EXPORT_LAYOUT", "selected transfer region cannot be projected into its planned access", tensor_id=tensor.tensor_id, view_id=view.view_id)
        value = value_by_id.get(source_value_by_tensor.get(tensor.tensor_id))
        if value is None:
            raise MeshIrError("E_ABI_BOUNDS", "selected transfer tensor has no source value", tensor_id=tensor.tensor_id)
        parent = DenseLogicalRegion(global_origin, access.region.shape)
        logical_pieces = project_mapped_region_pieces(value, parent, mapped)
        projected_spans = tuple(
            span
            for piece in logical_pieces
            for span in region_byte_spans(
                value.storage_offset + sum(origin * stride.evaluate({}) for origin, stride in zip(piece.origin, value.strides)),
                piece.shape,
                tuple(stride.evaluate({}) for stride in value.strides),
                value.dtype.byte_width,
            )
        )
        if merge_spans(projected_spans) != mapped.byte_spans:
            raise MeshIrError("E_EXPORT_LAYOUT", "selected transfer region exceeds its planned access", tensor_id=tensor.tensor_id, view_id=view.view_id)
        return tuple(
            PlannedBufferAccess(
                access.view_id,
                ElementRegion(
                    tuple(local + origin - base for local, origin, base in zip(access.region.origin, piece.origin, global_origin)),
                    piece.shape,
                    (1,) * len(piece.shape),
                ),
            )
            for piece in logical_pieces
        )

    def access_bytes(access: PlannedBufferAccess) -> int:
        view = view_by_id[access.view_id]
        return checked_mul_u64(_product(access.region.shape, "physical access elements"), tensor_by_id[view.tensor_id].dtype.byte_width, "physical access bytes")

    next_p2p_transfer_id = max((item.transfer_id for item in collective.transfers), default=0) + 1
    transfer_ready = {}

    def record_transfer_ready(targets, token_id):
        for target in targets:
            view = view_by_id[target.view_id]
            transfer_ready.setdefault((view.object_id, view.generation), set()).add(token_id)

    def emit_transfer(label, transfer, sources, targets):
        nonlocal next_p2p_transfer_id
        if transfer.kind is PlacementTransferKind.EXTERNAL_LOAD:
            attrs = DmaAttrs(DmaKind.PREFETCH, transfer.request_core, INVALID_CORE_ID, transfer.destination.core_id, 0, b"")
            emit(0, label, KernelOpcode.DMA, transfer.request_core, 0, sources, targets, attrs)
            return
        transfer_id = next_p2p_transfer_id
        next_p2p_transfer_id += 1
        attrs = DmaAttrs(DmaKind.P2P_PUSH, transfer.request_core, transfer.source.core_id, transfer.destination.core_id, transfer_id, b"")
        send = emit(0, label, KernelOpcode.DMA, transfer.request_core, 0, sources, targets, attrs)
        useful_bytes = 0
        for target in targets:
            useful_bytes = checked_add_u64(useful_bytes, access_bytes(target), "peer transfer bytes")
        wait_attrs = RecvWaitAttrs(transfer_id, transfer.source.core_id, transfer.destination.core_id, useful_bytes)
        wait = emit(0, f"{label}:receive", KernelOpcode.RECV_WAIT, transfer.destination.core_id, 0, (), (), wait_attrs, (send.done_token,))
        record_transfer_ready(targets, wait.done_token)

    transfers_by_use = {}
    stores_by_output = {}
    for identity, op, geometry in geometries:
        for transfer in geometry.value_transfers:
            if transfer.kind is PlacementTransferKind.OUTPUT_STORE:
                stores_by_output.setdefault((identity, transfer.window_use.output_tile_index, transfer.request_core), []).append(transfer)
            else:
                transfers_by_use.setdefault((identity, transfer.operand_index, transfer.window_use.compute_tile_ordinal, transfer.window_use.output_tile_index, transfer.window_use.phase, transfer.window_use.window_id), []).append(transfer)
    tile_plan = {item.tile_id: item for item in buffers.tile_buffers}
    tiles_by_identity = {}
    for item in tiles:
        tiles_by_identity.setdefault(item.identity, []).append(item)

    def finish_output(identity, op, computation_id, output_index, owner_core, group):
        accumulated = bool(group and group[0].geometry.matrix_phase is not None and group[0].geometry.matrix_phase is not MatrixPhase.DIRECT)
        if accumulated:
            accumulator = result_use.get((identity, output_index, owner_core, ResidentWindowRole.ACCUMULATOR))
            semantic = result_use.get((identity, output_index, owner_core, ResidentWindowRole.SEMANTIC_RESULT))
            if accumulator is None or semantic is None:
                raise MeshIrError("E_ABI_BOUNDS", "matrix epilogue result views are incomplete", op_id=op.op_id, output_tile_index=output_index)
            read_accesses = [] if accumulator.access is None else [accumulator.access]
            if op.opcode is OpCode.LINEAR_BIAS:
                bias_use = next(
                    (
                        item
                        for item in buffers.window_uses
                        if item.identity == identity
                        and item.operand_index == 2
                        and item.output_tile_index == output_index
                        and item.phase is GeometryPhase.EPILOGUE
                        and object_by_id[view_by_id[item.access.view_id].object_id].owner_core == owner_core
                    ),
                    None,
                )
                if bias_use is None:
                    raise MeshIrError("E_ABI_BOUNDS", "linear bias epilogue has no selected bias window", op_id=op.op_id, output_tile_index=output_index)
                transfer_key = (identity, 2, None, output_index, GeometryPhase.EPILOGUE, object_by_id[view_by_id[bias_use.access.view_id].object_id].window_id)
                for transfer_index, transfer in enumerate(transfers_by_use.get(transfer_key, ())):
                    targets = mapped_accesses(bias_use.access, transfer.region.mapped_region)
                    sources = tuple(source_access(target, MemorySpace.HBM, INVALID_CORE_ID) if transfer.kind is PlacementTransferKind.EXTERNAL_LOAD else source_access(target, MemorySpace.CORE_SRAM, transfer.source.core_id) for target in targets)
                    emit_transfer(f"transfer:{identity.op_id}:2:{output_index}:{owner_core}:{transfer_index}", transfer, sources, targets)
                if bias_use.access.region.shape and all(bias_use.access.region.shape):
                    read_accesses.append(bias_use.access)
            selected = group[-1].geometry
            input_bytes = sum(access_bytes(item) for item in read_accesses)
            output_bytes = 0 if semantic.access is None else access_bytes(semantic.access)
            operand_dtypes = tuple(tensor_by_id[item].dtype for item in buffers.computations[computation_id - 1].operand_tensor_ids)
            result_dtype = tensor_by_id[buffers.computations[computation_id - 1].result_tensor_id].dtype
            phases = estimate_work_phases(op.opcode, op.attrs, operand_dtypes, result_dtype, MatrixEpilogueWorkDomain(selected.tile.valid_batch, selected.tile.valid_m, selected.tile.valid_n))
            attrs = MatrixEpilogueKernelAttrs(op.opcode, op.attrs, selected.tile, _cost(input_bytes, output_bytes, phases), MatrixEpilogueAlgorithm.VECTOR_ACCUMULATION)
            emit(computation_id, f"epilogue:{op.op_id}:{output_index}:{owner_core}", KernelOpcode.MATRIX_EPILOGUE, owner_core, semantic.shard_id, tuple(read_accesses), () if semantic.access is None else (semantic.access,), attrs)
        selected_result = result_use.get((identity, output_index, owner_core, ResidentWindowRole.SEMANTIC_RESULT))
        for transfer in stores_by_output.get((identity, output_index, owner_core), ()):
            if selected_result is None or selected_result.access is None:
                raise MeshIrError("E_ABI_BOUNDS", "output store has no physical result view", op_id=op.op_id, output_tile_index=output_index)
            attrs = DmaAttrs(DmaKind.STORE, owner_core, owner_core, INVALID_CORE_ID, 0, b"")
            sources = mapped_accesses(selected_result.access, transfer.region.mapped_region)
            destinations = tuple(source_access(source, MemorySpace.HBM, INVALID_CORE_ID) for source in sources)
            emit(0, f"store:{op.op_id}:{output_index}:{owner_core}", KernelOpcode.DMA, owner_core, 0, sources, destinations, attrs)

    for identity, op, geometry in geometries:
        computation_id = next(item.computation_id for item in buffers.computation_lineage if item.source_op_id == op.op_id)
        operation_tiles = tuple(tiles_by_identity.get(identity, ()))
        output_groups = tuple(dict.fromkeys((item.geometry.output_tile_index, item.geometry.owner_core) for item in operation_tiles))
        if not output_groups:
            output_groups = tuple(
                dict.fromkeys(
                    (item.output_tile_index, item.owner_core)
                    for item in buffers.result_views
                    if item.identity == identity and item.role is ResidentWindowRole.SEMANTIC_RESULT and (identity, item.output_tile_index, item.owner_core) in stores_by_output
                )
            )
        for output_index in dict.fromkeys(item[0] for item in output_groups):
            groups = tuple(
                (
                    owner_core,
                    tuple(item for item in operation_tiles if (item.geometry.output_tile_index, item.geometry.owner_core) == (output_index, owner_core)),
                )
                for selected_output, owner_core in output_groups
                if selected_output == output_index
            )
            for owner_core, group in groups:
                for planned in group:
                    selected = planned.geometry
                    tile_buffers = tile_plan[planned.tile_id]
                    for operand_index in range(len(tile_buffers.operand_accesses)):
                        key = (identity, operand_index, selected.tile_ordinal, selected.output_tile_index, GeometryPhase.COMPUTE)
                        use = next((item for candidate_key, item in planned_use.items() if candidate_key[:5] == key), None)
                        if use is None:
                            raise MeshIrError("E_ABI_BOUNDS", "compute operand has no selected window use", op_id=op.op_id, tile_ordinal=selected.tile_ordinal, operand_index=operand_index)
                        for materialization_index, pair in enumerate(use.local_materializations):
                            destination = view_by_id[pair.destination.view_id]
                            emit(0, f"local-copy:{identity.op_id}:{operand_index}:{selected.tile_ordinal}:{materialization_index}", KernelOpcode.LOCAL_COPY, object_by_id[destination.object_id].owner_core, 0, (pair.source,), (pair.destination,), LocalCopyAttrs())
                        transfer_key = (*key, object_by_id[view_by_id[use.access.view_id].object_id].window_id)
                        for transfer_index, transfer in enumerate(transfers_by_use.get(transfer_key, ())):
                            targets = mapped_accesses(use.access, transfer.region.mapped_region)
                            sources = tuple(source_access(target, MemorySpace.HBM, INVALID_CORE_ID) if transfer.kind is PlacementTransferKind.EXTERNAL_LOAD else source_access(target, MemorySpace.CORE_SRAM, transfer.source.core_id) for target in targets)
                            emit_transfer(f"transfer:{identity.op_id}:{operand_index}:{selected.tile_ordinal}:{transfer_index}", transfer, sources, targets)
                    read_accesses = list(tile_buffers.operand_accesses)
                    if selected.matrix_phase in (MatrixPhase.ACCUMULATE_CONTINUE, MatrixPhase.ACCUMULATE_FINAL):
                        if tile_buffers.result_access is not None:
                            read_accesses.append(tile_buffers.result_access)
                    input_bytes = sum(access_bytes(item) for item in read_accesses)
                    output_bytes = 0 if tile_buffers.result_access is None else access_bytes(tile_buffers.result_access)
                    result_dtype = tensor_by_id[shard_by_id[tile_buffers.result_shard_id].tensor_id].dtype
                    operand_dtypes = tuple(tensor_by_id[item].dtype for item in buffers.computations[computation_id - 1].operand_tensor_ids)
                    if tile_buffers.operand_accesses:
                        source_shape = tile_buffers.operand_accesses[0].region.shape
                    elif op.opcode in _ROW:
                        source_shapes = tuple(
                            mapping.required_shard.valid_shape
                            for operand in geometry.operand_distributions
                            if operand.operand_index == 0
                            for mapping in operand.mappings
                            if mapping.required_shard.owner_core == selected.owner_core
                        )
                        if len(source_shapes) != 1:
                            raise MeshIrError("E_ABI_BOUNDS", "empty row tile has no unique selected source shard", op_id=op.op_id, tile_ordinal=selected.tile_ordinal)
                        source_shape = source_shapes[0]
                    else:
                        source_shape = ()
                    attrs = _attrs(op, selected.tile, selected.matrix_phase, shard_by_id[tile_buffers.result_shard_id].partial_sum_id, operand_dtypes, result_dtype, source_shape, input_bytes, output_bytes)
                    emit(computation_id, f"compute:{op.op_id}:{selected.tile_ordinal}", _kernel_opcode(op.opcode), selected.owner_core, tile_buffers.result_shard_id, tuple(read_accesses), () if tile_buffers.result_access is None else (tile_buffers.result_access,), attrs)
            for transfer in tuple(item for item in collective.transfers if collective_context[item.transfer_id][0] == identity and collective_context[item.transfer_id][1] == output_index):
                _, _, source_accesses, destination_accesses, receive_accesses, _, destination_shard_id = collective_context[transfer.transfer_id]
                useful_bytes = checked_mul_u64(transfer.geometry.chunk.element_count, transfer.geometry.chunk.accumulation_dtype.byte_width, "collective transfer bytes")
                attrs = DmaAttrs(DmaKind.P2P_PUSH, transfer.geometry.source_core, transfer.geometry.source_core, transfer.geometry.destination_core, transfer.transfer_id, b"")
                targets = receive_accesses if transfer.geometry.phase is CollectivePhase.REDUCE else destination_accesses
                send = emit(0, f"collective-send:{transfer.transfer_id}", KernelOpcode.DMA, transfer.geometry.source_core, 0, source_accesses, targets, attrs)
                wait_attrs = RecvWaitAttrs(transfer.transfer_id, transfer.geometry.source_core, transfer.geometry.destination_core, useful_bytes)
                wait = emit(0, f"collective-receive:{transfer.transfer_id}", KernelOpcode.RECV_WAIT, transfer.geometry.destination_core, 0, (), (), wait_attrs, (send.done_token,))
                record_transfer_ready(targets, wait.done_token)
                if transfer.geometry.phase is CollectivePhase.REDUCE:
                    reads = tuple(access for pair in zip(destination_accesses, receive_accesses) for access in pair)
                    row_domain = RowWorkDomain(transfer.geometry.chunk.element_count, 2)
                    dtype = transfer.geometry.chunk.accumulation_dtype
                    phases = estimate_work_phases(OpCode.REDUCE_SUM, ReduceAttrs((0,), False, dtype, dtype), (dtype,), dtype, row_domain)
                    selected = next(item.geometry for item in operation_tiles if item.geometry.output_tile_index == output_index and item.geometry.owner_core == transfer.geometry.destination_core)
                    local_attrs = LocalReduceAttrs(ReduceKind.SUM, transfer.partial_sum_id, selected.tile, _cost(checked_mul_u64(useful_bytes, 2, "collective reduction input bytes"), useful_bytes, phases))
                    emit(computation_id, f"collective-reduce:{transfer.transfer_id}", KernelOpcode.LOCAL_REDUCE, transfer.geometry.destination_core, destination_shard_id, reads, destination_accesses, local_attrs, (wait.done_token,), transfer.partial_sum_id)
            for owner_core, group in groups:
                finish_output(identity, op, computation_id, output_index, owner_core, group)
    return KernelModule.create(graph.arch_digest, graph.semantic_sha256, graph.entrypoint, graph.profile_id, buffers.tensors, buffers.computations, buffers.tensor_lineage, buffers.computation_lineage, buffers.placements, buffers.shards, buffers.partial_sums, objects, tuple(views), tuple(states), tuple(tokens), tuple(declarations + effects))


def insert_data_movement(planning: PlanningResult, collectives: CollectiveState, executor: PassExecutor) -> tuple[KernelModule, ...]:
    if type(planning) is not PlanningResult or type(collectives) is not CollectiveState or type(executor) is not PassExecutor or len(planning.state.variants) != len(collectives.buffers.variants) or len(planning.state.variants) != len(collectives.variants):
        raise MeshIrError("E_CONFIG", "movement lowering inputs are invalid")
    tasks = []
    decisions = collectives.buffers.tiling.shards.decisions
    if type(decisions) is not LoweringDecisions or type(decisions.candidates) is not tuple or type(decisions.placements) is not tuple:
        raise MeshIrError("E_CONFIG", "movement placement records are invalid")
    decision_index = 0
    for index, (graph, buffers, collective) in enumerate(zip(planning.state.variants, collectives.buffers.variants, collectives.variants)):
        selected_geometries = []
        function = graph.functions[0]
        for operation_ordinal, op in enumerate(function.ops):
            if decision_index >= len(decisions.placements):
                raise MeshIrError("E_ABI_ORDER", "movement placement decisions omit an operation", op_id=op.op_id)
            decision = decisions.placements[decision_index]
            decision_index += 1
            if type(decision) is not PlacementDecision or type(decision.candidate_index) is not int or not 0 <= decision.candidate_index < len(decisions.candidates):
                raise MeshIrError("E_ABI_ORDER", "movement placement decision is invalid", op_id=op.op_id)
            candidate = decisions.candidates[decision.candidate_index]
            identity = OperationIdentity(graph.entrypoint, graph.profile_id, function.function_id, op.op_id)
            if type(candidate) is not PlacementCandidate or decision.identity != identity or candidate.identity != identity or candidate.variant_ordinal != index or decision.strategy is not candidate.strategy or decision.core_ids != candidate.core_ids or candidate.rejection is not None or type(candidate.geometry) is not OperationPlacementGeometry or candidate.geometry.operation_ordinal != operation_ordinal:
                raise MeshIrError("E_ABI_ORDER", "movement selected geometry is invalid", op_id=op.op_id)
            selected_geometries.append(candidate.geometry)
        tiles = tuple(item for item in collectives.buffers.tiling.tiles if (item.identity.entrypoint, item.identity.profile_id) == (graph.entrypoint, graph.profile_id))
        payload = _MovementVariantTask(graph, tuple(selected_geometries), buffers, collective, tiles)
        tasks.append(PassTask(f"{index}:{graph.entrypoint}:{graph.profile_id}", payload))
    if decision_index != len(decisions.placements):
        raise MeshIrError("E_ABI_ORDER", "movement placement decisions contain absent operations")
    return tuple(item.value for item in executor.run("InsertDataMovement", _lower_variant, tuple(tasks)))


__all__ = ["insert_data_movement"]
