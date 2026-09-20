from __future__ import annotations

import hashlib
import math
import time
from dataclasses import asdict, dataclass

from mesh_ir.architecture import ArchManifest, validate_arch
from mesh_ir.canonical import canonical_json_bytes, checked_add_u64, checked_mul_u64
from mesh_ir.compile_config import EffectiveCompileConfig, validate_effective_compile_config
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Const, DmaKind, MemorySpace, contiguous_strides
from mesh_ir.ir.graph_ir import GraphModule, METADATA_VIEW_OPCODES, OpCode
from mesh_ir.ir.kernel_ir import ElementRegion, KernelBundle, KernelModule, KernelOpcode, KernelTensor, StateOrigin
from mesh_ir.passes.execution import ExecutionDiagnostics, PassExecutor, PassRecord, record_pass, verify_pass_chain
from mesh_ir.passes.placement import LoweringDecisions, place_operations
from mesh_ir.passes.planning_prefix import PlanningResult, graph_set_sha256
from mesh_ir.passes.bufferize import BufferizationState, bufferize_and_alias
from mesh_ir.passes.collectives import CollectiveState, lower_collectives
from mesh_ir.passes.movement import insert_data_movement
from mesh_ir.passes.sharding import ShardingState, shard_and_pad
from mesh_ir.passes.tiling import TilingState, tile_kernels


@dataclass(frozen=True)
class KernelLoweringResult:
    bundle: KernelBundle
    passes: tuple[PassRecord, ...]
    decisions: LoweringDecisions
    execution: ExecutionDiagnostics


def _shape(value) -> tuple[int, ...]:
    if any(type(item) is not Const for item in value.shape):
        raise MeshIrError("E_SHAPE_UNBOUND", "Kernel lowering requires concrete Graph values", value_id=value.value_id)
    return tuple(item.value for item in value.shape)


def _strides(value) -> tuple[int, ...]:
    return tuple(item.evaluate({}) for item in value.strides)


def _product(values: tuple[int, ...], field: str) -> int:
    result = 1
    for value in values:
        result = checked_mul_u64(result, value, field)
    return result


def _extents(value) -> tuple[int, int]:
    shape = _shape(value)
    logical = checked_mul_u64(_product(shape, "logical tensor elements"), value.dtype.byte_width, "logical tensor bytes")
    if any(item == 0 for item in shape):
        return logical, 0
    elements = checked_add_u64(value.storage_offset, 1, "tensor storage elements")
    for dimension, stride in zip(shape, _strides(value)):
        elements = checked_add_u64(elements, checked_mul_u64(dimension - 1, stride, "tensor storage elements"), "tensor storage elements")
    return logical, checked_mul_u64(elements, value.dtype.byte_width, "tensor storage bytes")


def _row_major(shape: tuple[int, ...]) -> tuple[int, ...]:
    return tuple(item.evaluate({}) for item in contiguous_strides(tuple(Const(value) for value in shape)))


def _axis_progressions_intersect(left: int, left_count: int, left_step: int, right: int, right_count: int, right_step: int) -> bool:
    if not left_count or not right_count:
        return False
    left_end = left + (left_count - 1) * left_step
    right_end = right + (right_count - 1) * right_step
    if left_count == 1:
        return right <= left <= right_end and (left - right) % right_step == 0
    if right_count == 1:
        return left <= right <= left_end and (right - left) % left_step == 0
    lower = max(left, right)
    upper = min(left_end, right_end)
    if lower > upper:
        return False
    divisor = math.gcd(left_step, right_step)
    difference = right - left
    if difference % divisor:
        return False
    left_reduced = left_step // divisor
    right_reduced = right_step // divisor
    index = 0 if right_reduced == 1 else (difference // divisor * pow(left_reduced, -1, right_reduced)) % right_reduced
    candidate = left + left_step * index
    period = left_step * right_reduced
    if candidate < lower:
        candidate += ((lower - candidate + period - 1) // period) * period
    return candidate <= upper


def _logical_regions_overlap(left: ElementRegion, right: ElementRegion) -> bool:
    if any(not extent for extent in left.shape) or any(not extent for extent in right.shape):
        return False
    return all(
        _axis_progressions_intersect(left_origin, left_extent, left_step, right_origin, right_extent, right_step)
        for left_origin, left_extent, left_step, right_origin, right_extent, right_step in zip(left.origin, left.shape, left.steps, right.origin, right.shape, right.steps)
    )


def _regions_exactly_cover(shape: tuple[int, ...], regions: tuple[ElementRegion, ...]) -> bool:
    covered = 0
    nonempty = []
    for region in regions:
        if len(region.origin) != len(shape):
            return False
        for coordinate, extent, step, limit in zip(region.origin, region.shape, region.steps, shape):
            if extent:
                last = checked_add_u64(coordinate, checked_mul_u64(extent - 1, step, "output coordinate"), "output coordinate")
                if last >= limit:
                    return False
            elif coordinate > limit:
                return False
        elements = _product(region.shape, "output stored elements")
        if elements:
            if any(_logical_regions_overlap(region, previous) for previous in nonempty):
                return False
            nonempty.append(region)
        covered = checked_add_u64(covered, elements, "output stored elements")
    return covered == _product(shape, "Graph output elements")


def _verify_graph_output_coverage(graph: GraphModule, kernel: KernelModule, source_tensors: dict[int, KernelTensor]) -> None:
    output_values = {value_id: next(value for value in graph.values if value.value_id == value_id) for function in graph.functions for value_id in function.outputs}
    output_tensors = {source_tensors[value_id].tensor_id: value for value_id, value in output_values.items()}
    pieces: dict[int, list[ElementRegion]] = {tensor_id: [] for tensor_id in output_tensors}
    for op in kernel.ops:
        if op.opcode is not KernelOpcode.DMA or op.attrs.kind is not DmaKind.STORE:
            continue
        for source, destination in zip(op.reads, op.writes):
            source_view = kernel.views[source.view_id - 1]
            destination_view = kernel.views[destination.view_id - 1]
            source_tensor = kernel.tensors[kernel.shards[source_view.shard_id - 1].tensor_id - 1]
            destination_shard = kernel.shards[destination_view.shard_id - 1]
            destination_tensor = kernel.tensors[destination_shard.tensor_id - 1]
            output = output_tensors.get(destination_tensor.tensor_id)
            if output is None:
                raise MeshIrError("E_TENSOR_NOT_RESIDENT", "Kernel STORE targets a non-output logical tensor", op_id=op.op_id, tensor_id=destination_tensor.tensor_id)
            source_state = kernel.states[source.state_id - 1]
            if source_state.origin is not StateOrigin.PRODUCED or source_tensor.alias_root_tensor_id != destination_tensor.alias_root_tensor_id:
                raise MeshIrError("E_TENSOR_NOT_RESIDENT", "Kernel output STORE does not read its produced logical content", op_id=op.op_id, tensor_id=destination_tensor.tensor_id)
            shape = _shape(output)
            rank = len(shape)
            region = destination.region
            if len(destination_shard.global_origin) != rank or len(destination_view.shard_origin) != rank or len(region.origin) != rank:
                raise MeshIrError("E_EXPORT_LAYOUT", "Kernel output STORE region rank differs from Graph output", op_id=op.op_id, tensor_id=destination_tensor.tensor_id)
            origin = tuple(checked_add_u64(checked_add_u64(shard_origin, view_origin, "output coordinate"), access_origin, "output coordinate") for shard_origin, view_origin, access_origin in zip(destination_shard.global_origin, destination_view.shard_origin, region.origin))
            pieces[destination_tensor.tensor_id].append(ElementRegion(origin, region.shape, region.steps))
    for tensor_id, output in output_tensors.items():
        if not _regions_exactly_cover(_shape(output), tuple(pieces[tensor_id])):
            raise MeshIrError("E_TENSOR_NOT_RESIDENT", "Kernel output store regions do not exactly cover Graph output", tensor_id=tensor_id)


def verify_kernel_correspondence(graph: GraphModule, kernel: KernelModule) -> None:
    graph.verify()
    kernel.verify()
    if (kernel.arch_digest, kernel.source_semantic_hash, kernel.entrypoint, kernel.profile_id) != (graph.arch_digest, graph.semantic_sha256, graph.entrypoint, graph.profile_id):
        raise MeshIrError("E_ABI_CHECKSUM", "Kernel source Graph identity differs")
    source_tensors = {lineage.source_value_id: kernel.tensors[lineage.tensor_id - 1] for lineage in kernel.tensor_lineage if lineage.source_value_id}
    if set(source_tensors) != {item.value_id for item in graph.values}:
        raise MeshIrError("E_ABI_BOUNDS", "Kernel source value membership differs from Graph")
    physical_value_ids = {
        value_id
        for function in graph.functions
        for value_id in (*function.inputs, *function.outputs)
    }
    for function in graph.functions:
        for op in function.ops:
            if op.opcode not in METADATA_VIEW_OPCODES:
                physical_value_ids.update(op.operands)
                physical_value_ids.update(op.results)
    produced_views = {transition.view_id for op in kernel.ops for transition in op.writes}
    produced_generations = {(kernel.views[view_id - 1].object_id, kernel.views[view_id - 1].generation) for view_id in produced_views}
    for value in graph.values:
        tensor = source_tensors[value.value_id]
        logical_extent, storage_extent = _extents(value)
        if (tensor.name, tensor.role, tensor.dtype, tensor.shape, tensor.strides, tensor.storage_offset_elements, tensor.alias_root_tensor_id, tensor.access, tensor.logical_extent_bytes, tensor.storage_extent_bytes, tensor.content_sha256) != (value.name, value.role, value.dtype, _shape(value), _strides(value), value.storage_offset, value.alias_root, value.access, logical_extent, storage_extent, value.content_sha256):
            raise MeshIrError("E_EXPORT_LAYOUT", "Kernel logical tensor differs from Graph value", value_id=value.value_id)
        mapped = tuple(item for item in kernel.views if kernel.shards[item.shard_id - 1].tensor_id == tensor.tensor_id)
        local = tuple(item for item in mapped if kernel.objects[item.object_id - 1].memory_space is MemorySpace.CORE_SRAM)
        valid_local = all(
            kernel.objects[item.object_id - 1].storage_tensor_id == tensor.alias_root_tensor_id
            and (
                item.object_offset_elements == sum(origin * stride for origin, stride in zip(item.shard_origin, tensor.strides)) and item.object_strides == tensor.strides
                or item.object_offset_elements == 0 and item.object_strides == _row_major(item.padded_shape) and (item.object_id, item.generation) in produced_generations
            )
            for item in local
        )
        if not valid_local or (value.value_id in physical_value_ids and not mapped):
            raise MeshIrError("E_EXPORT_LAYOUT", f"Kernel physical view differs from Graph affine mapping: value={value.value_id} views={tuple((item.view_id, item.object_id, item.generation, item.shard_origin, item.padded_shape, item.object_offset_elements, item.object_strides, (item.object_id, item.generation) in produced_generations) for item in local)}", value_id=value.value_id)
    graph_ops = {item.op_id: item for function in graph.functions for item in function.ops}
    source_op_by_computation = {item.computation_id: item.source_op_id for item in kernel.computation_lineage}
    if set(source_op_by_computation.values()) != set(graph_ops):
        raise MeshIrError("E_ABI_BOUNDS", "Kernel computation lineage membership differs from Graph")
    lineage_by_computation = {item.computation_id: item for item in kernel.computation_lineage}
    for computation in kernel.computations:
        lineage = lineage_by_computation[computation.computation_id]
        source = graph_ops[lineage.source_op_id]
        if lineage.source_node_id != source.source_node_id or (computation.opcode, computation.operand_tensor_ids, computation.result_tensor_id, computation.attrs) != (source.opcode, source.operands, source.results[0], source.attrs):
            raise MeshIrError("E_ABI_CHECKSUM", "Kernel computation differs from Graph operation", computation_id=computation.computation_id)
    for op in kernel.ops:
        source_op_id = source_op_by_computation.get(op.computation_id, 0)
        if op.computation_id and source_op_id not in graph_ops:
            raise MeshIrError("E_ABI_BOUNDS", "Kernel operation references an unknown Graph operation", op_id=op.op_id)
        if source_op_id and hasattr(op.attrs, "semantic_attrs"):
            source = graph_ops[source_op_id]
            recorded_opcode = OpCode.SOFTMAX if op.opcode is KernelOpcode.SOFTMAX else op.attrs.graph_opcode
            if recorded_opcode is not source.opcode or op.attrs.semantic_attrs != source.attrs:
                raise MeshIrError("E_ABI_CHECKSUM", "Kernel operation semantics differ from Graph operation", op_id=op.op_id, source_op_id=source_op_id)
    lowered = {source_op_by_computation[item.computation_id] for item in kernel.ops if item.computation_id}
    missing = tuple(item.op_id for item in graph_ops.values() if item.opcode not in METADATA_VIEW_OPCODES and item.op_id not in lowered)
    if missing:
        raise MeshIrError("E_ABI_BOUNDS", "Graph operations are absent from Kernel lowering", op_ids=missing)
    _verify_graph_output_coverage(graph, kernel, source_tensors)


def verify_kernel_bundle_correspondence(graphs: tuple[GraphModule, ...], bundle: KernelBundle) -> None:
    if bundle.source_graph_set_sha256 != graph_set_sha256(graphs) or len(bundle.modules) != len(graphs):
        raise MeshIrError("E_ABI_CHECKSUM", "Kernel bundle Graph set identity differs")
    for graph, kernel in zip(graphs, bundle.modules):
        verify_kernel_correspondence(graph, kernel)


def _state_hash(payload: LoweringDecisions | ShardingState | TilingState | BufferizationState | CollectiveState) -> str:
    return hashlib.sha256(canonical_json_bytes(asdict(payload))).hexdigest()


def lower_to_kernel(planning: PlanningResult, arch: ArchManifest, effective: EffectiveCompileConfig, executor: PassExecutor) -> KernelLoweringResult:
    if type(planning) is not PlanningResult or type(executor) is not PassExecutor:
        raise MeshIrError("E_CONFIG", "Kernel lowering inputs have invalid record types")
    validate_arch(arch)
    validate_effective_compile_config(effective, arch)
    initial_hash = planning.state.semantic_sha256()
    records = []
    started = time.perf_counter_ns()
    decisions = place_operations(planning, arch, effective)
    current = _state_hash(decisions)
    records.append(record_pass("PlaceOpsAndTensors", initial_hash, current, time.perf_counter_ns() - started, (("candidates", len(decisions.candidates)), ("placements", len(decisions.placements)))))
    started = time.perf_counter_ns()
    sharding = shard_and_pad(planning, decisions)
    output = _state_hash(sharding)
    records.append(record_pass("ShardAndPadTensors", current, output, time.perf_counter_ns() - started, (("placements", sum(len(item.distributions) for item in sharding.variants)), ("shards", sum(len(distribution.shard_ids) for item in sharding.variants for distribution in item.distributions)))))
    current = output
    started = time.perf_counter_ns()
    tiling = tile_kernels(planning, sharding)
    output = _state_hash(tiling)
    records.append(record_pass("TileKernels", current, output, time.perf_counter_ns() - started, (("tiles", len(tiling.tiles)),)))
    current = output
    started = time.perf_counter_ns()
    buffers = bufferize_and_alias(planning, tiling)
    output = _state_hash(buffers)
    records.append(record_pass("BufferizeAndAlias", current, output, time.perf_counter_ns() - started, (("objects", sum(len(item.objects) for item in buffers.variants)), ("views", sum(len(item.views) for item in buffers.variants)))))
    current = output
    started = time.perf_counter_ns()
    collectives = lower_collectives(planning, buffers)
    output = _state_hash(collectives)
    records.append(record_pass("LowerCollectives", current, output, time.perf_counter_ns() - started, (("transfers", sum(len(item.transfers) for item in collectives.variants)),)))
    current = output
    started = time.perf_counter_ns()
    modules = insert_data_movement(planning, collectives, executor)
    bundle = KernelBundle.create(arch.digest().hex(), graph_set_sha256(planning.state.variants), modules)
    output = bundle.semantic_sha256
    records.append(record_pass("InsertDataMovement", current, output, time.perf_counter_ns() - started, (("modules", len(modules)),)))
    passes = tuple(records)
    verify_pass_chain(passes, initial_hash, output, ("PlaceOpsAndTensors", "ShardAndPadTensors", "TileKernels", "BufferizeAndAlias", "LowerCollectives", "InsertDataMovement"))
    verify_kernel_bundle_correspondence(planning.state.variants, bundle)
    return KernelLoweringResult(bundle, passes, decisions, executor.diagnostics)


__all__ = [
    "KernelLoweringResult",
    "lower_to_kernel",
    "verify_kernel_bundle_correspondence",
    "verify_kernel_correspondence",
]
