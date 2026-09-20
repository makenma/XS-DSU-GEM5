from __future__ import annotations

import json
import math
import os
import pickle
import subprocess
import sys
from pathlib import Path

import pytest
import torch

from mesh_ir.analysis.sram import plan_static_sram
from mesh_ir.analysis.kernel_work import kernel_local_copy_domain
from mesh_ir.architecture import load_arch
from mesh_ir.compile_config import load_compile_config_text, resolve_compile_config
from mesh_ir.compiler import compile_backend
from mesh_ir.frontend import FrontendRequest, export_graph
from mesh_ir.ir.common import DmaKind, MemorySpace
from mesh_ir.ir.graph_ir import OpCode
from mesh_ir.ir.kernel_ir import GemmKernelAttrs, KernelOpcode
from mesh_ir.ir.kernel_verify import verify_scheduled_ready_kernel
from mesh_ir.passes.bufferize import bufferize_and_alias
from mesh_ir.passes.collectives import lower_collectives
from mesh_ir.passes.execution import PassExecutor, verify_pass_chain
from mesh_ir.passes.graph_to_kernel import verify_kernel_bundle_correspondence
from mesh_ir.passes.placement import place_operations
from mesh_ir.passes.placement_geometry import ResidentWindowRole
from mesh_ir.passes.planning_prefix import plan_graphs_with_executor
from mesh_ir.passes.sharding import shard_and_pad
from mesh_ir.passes.tiling import tile_kernels
from mesh_ir.passes.movement import insert_data_movement
from mesh_ir.scheduled.verify import verify_program, verify_program_kernel_correspondence
from tests.integration.backend_compiler_fixtures import ROOT, RealBackendInputs, effective_config, real_backend_inputs
from tests.integration.kernel_pipeline_driver import KernelPipelineInputs, KernelPipelineRun, run_kernel_pipeline
from tests.integration.test_gate1_real_frontend import OpZoo
from tests.golden.support.compiler_fixtures import SMALL_MESH_COMPILE as OPERATOR_COMPILE


DRIVER = Path(__file__).with_name("kernel_pipeline_driver.py")
BACKEND_PYTHON = ROOT / ".tmp/torch-gate-2-base-env/bin/python"
KERNEL_PASSES = ("PlaceOpsAndTensors", "ShardAndPadTensors", "TileKernels", "BufferizeAndAlias", "LowerCollectives", "InsertDataMovement")
VIEW_OPS = frozenset((OpCode.RESHAPE_VIEW, OpCode.TRANSPOSE_VIEW, OpCode.PERMUTE_VIEW, OpCode.SLICE_VIEW, OpCode.EXPAND_VIEW))


def _inputs(inputs: RealBackendInputs, tensor_parallel: int) -> KernelPipelineInputs:
    return KernelPipelineInputs(inputs.variants, inputs.source_graphs, inputs.arch, effective_config(inputs, tensor_parallel))


def _assert_kernel_pipeline(run: KernelPipelineRun, inputs: KernelPipelineInputs, parent_pid: int) -> None:
    bundle = run.lowering.bundle
    assert tuple(item.name for item in run.lowering.passes) == KERNEL_PASSES
    verify_pass_chain(run.lowering.passes, run.planning.state.semantic_sha256(), bundle.semantic_sha256, KERNEL_PASSES)
    assert tuple((item.entrypoint, item.profile_id) for item in bundle.modules) == tuple((item.entrypoint, item.profile_id) for item in inputs.variants)
    assert tuple(item.source_semantic_hash for item in bundle.modules) == tuple(item.semantic_sha256 for item in inputs.variants)
    verify_kernel_bundle_correspondence(inputs.variants, bundle)
    assert run.lowering.execution.submitted_tasks == run.lowering.execution.completed_tasks
    assert run.lowering.execution.completed_tasks > run.prefix_execution.completed_tasks
    assert all(item.worker_pid is not None and item.worker_pid != parent_pid for item in run.lowering.execution.tasks)
    matrix_ops = []
    for graph, module in zip(inputs.variants, bundle.modules):
        module.verify(inputs.arch)
        verify_scheduled_ready_kernel(module, inputs.arch)
        sram = plan_static_sram(module, inputs.arch)
        local_objects = tuple(item for item in module.objects if item.memory_space is MemorySpace.CORE_SRAM)
        assert {item.object_id for item in sram.allocations} == {item.object_id for item in local_objects}
        assert sram.reports and all(item.peak_bytes <= item.capacity_bytes for item in sram.reports)
        assert tuple(item.source_value_id for item in module.tensor_lineage if item.source_value_id) == tuple(item.value_id for item in graph.values)
        assert all(
            lineage.source_value_id == 0
            for tensor, lineage in zip(module.tensors, module.tensor_lineage)
            if tensor.synthesized_purpose is not None
        )
        assert tuple(item.source_op_id for item in module.computation_lineage) == tuple(item.op_id for item in graph.functions[0].ops)
        assert all(type(extent) is int for item in module.tensors for extent in (*item.shape, *item.strides))
        assert all(item.opcode is not KernelOpcode.COLLECTIVE for item in module.ops)
        materialized = tuple(item for item in graph.functions[0].ops if item.opcode not in VIEW_OPS)
        lowered_computations = {item.computation_id for item in module.ops if item.computation_id}
        lowered_sources = {
            item.source_op_id
            for item in module.computation_lineage
            if item.computation_id in lowered_computations
        }
        assert {item.op_id for item in materialized} <= lowered_sources
        effect_ops = tuple(item for item in module.ops if item.opcode not in (KernelOpcode.ALLOC, KernelOpcode.VIEW))
        assert effect_ops and all(item.done_token is not None for item in effect_ops)
        assert all(transition.new_state_id <= len(module.states) for item in effect_ops for transition in item.writes)
        stores = tuple(item for item in module.ops if item.opcode is KernelOpcode.DMA and item.attrs.kind is DmaKind.STORE)
        assert stores
        matrix_ops.extend((module.entrypoint, module.profile_id, item) for item in module.ops if type(item.attrs) is GemmKernelAttrs)
    assert matrix_ops
    assert any(item.attrs.tile.valid_k < item.attrs.tile.k_extent for _, _, item in matrix_ops)
    by_computation = {}
    for entrypoint, profile_id, item in matrix_ops:
        tile = item.attrs.tile
        output_domain = (
            tile.batch_origin,
            tile.m_origin,
            tile.n_origin,
            tile.batch_extent,
            tile.m_extent,
            tile.n_extent,
            tile.valid_batch,
            tile.valid_m,
            tile.valid_n,
        )
        by_computation.setdefault((entrypoint, profile_id, item.computation_id, item.owner_core, output_domain), []).append(tile.k_origin)
    assert any(len(set(origins)) > 1 for origins in by_computation.values())


@pytest.mark.parametrize("tensor_parallel", (1, 2, 4))
def test_real_frontends_lower_to_verified_kernel_bundle(real_backend_inputs, tensor_parallel):
    inputs = _inputs(real_backend_inputs, tensor_parallel)
    run = run_kernel_pipeline(inputs, workers=2)
    _assert_kernel_pipeline(run, inputs, os.getpid())


def _real_tp2_local_materialization(real_backend_inputs):
    inputs = _inputs(real_backend_inputs, 2)
    with PassExecutor(2) as executor:
        planning = plan_graphs_with_executor(inputs.variants, inputs.arch, inputs.effective, source_graphs=inputs.source_graphs, executor=executor)
        decisions = place_operations(planning, inputs.arch, inputs.effective)
        tiling = tile_kernels(planning, shard_and_pad(planning, decisions))
        buffers = bufferize_and_alias(planning, tiling)
    variant = next(item for item in buffers.variants if (item.entrypoint, item.profile_id) == ("transformer", "b2s4"))
    selected = tuple(
        decisions.candidates[item.candidate_index]
        for item in decisions.placements
        if (item.identity.entrypoint, item.identity.profile_id) == ("transformer", "b2s4")
    )
    windows = {window.window_id: window for item in selected for window in item.geometry.resident_windows}
    uses = tuple(item for item in variant.window_uses if item.identity.op_id == 2 and item.operand_index == 0)
    materialized = tuple((use, pair) for use in uses for pair in use.local_materializations)
    assert materialized
    assert all(
        pair.destination.view_id == use.access.view_id
        and all(origin >= parent for origin, parent in zip(pair.destination.region.origin, use.access.region.origin))
        and all(origin + extent <= parent + parent_extent for origin, extent, parent, parent_extent in zip(pair.destination.region.origin, pair.destination.region.shape, use.access.region.origin, use.access.region.shape))
        for use, pair in materialized
    )
    assert any(
        windows[variant.objects[variant.views[pair.source.view_id - 1].object_id - 1].window_id].role is ResidentWindowRole.SEMANTIC_RESULT
        and windows[variant.objects[variant.views[pair.destination.view_id - 1].object_id - 1].window_id].role is ResidentWindowRole.OPERAND
        and variant.objects[variant.views[pair.source.view_id - 1].object_id - 1].owner_core == variant.objects[variant.views[pair.destination.view_id - 1].object_id - 1].owner_core
        for _, pair in materialized
    )
    destination_generations = {variant.views[pair.destination.view_id - 1].generation for _, pair in materialized}
    assert any(not use.local_materializations and variant.views[use.access.view_id - 1].generation in destination_generations for use in uses)
    assert any(pair.destination.region != use.access.region for use, pair in materialized)
    return inputs, planning, buffers


def test_real_tp2_selected_local_materialization_reaches_bufferization(real_backend_inputs):
    _real_tp2_local_materialization(real_backend_inputs)


def test_real_tp2_selected_local_materialization_reaches_kernel(real_backend_inputs):
    inputs, planning, buffers = _real_tp2_local_materialization(real_backend_inputs)
    collectives = lower_collectives(planning, buffers)
    with PassExecutor(2) as executor:
        modules = insert_data_movement(planning, collectives, executor)
    module = next(item for item in modules if (item.entrypoint, item.profile_id) == ("transformer", "b2s4"))
    copies = tuple(item for item in module.ops if item.opcode is KernelOpcode.LOCAL_COPY)
    sends = tuple(item for item in module.ops if item.opcode is KernelOpcode.DMA and item.attrs.kind is DmaKind.P2P_PUSH)
    receives = tuple(item for item in module.ops if item.opcode is KernelOpcode.RECV_WAIT)
    collective_ids = {
        item.transfer_id
        for variant in collectives.variants
        if (variant.entrypoint, variant.profile_id) == ("transformer", "b2s4")
        for item in variant.transfers
    }
    send_by_id = {item.attrs.transfer_id: item for item in sends}
    receive_by_id = {item.attrs.transfer_id: item for item in receives}
    peer_ids = tuple(sorted(set(send_by_id) - collective_ids))
    assert copies
    assert all(kernel_local_copy_domain(module.memory_records(), item.op_id).elements > 0 for item in copies)
    assert sends
    assert len(send_by_id) == len(sends)
    assert set(send_by_id) == set(receive_by_id)
    assert len(peer_ids) > 1
    for transfer_id, send in send_by_id.items():
        receive = receive_by_id[transfer_id]
        assert send.done_token in receive.after_tokens
        assert receive.attrs.expected_bytes == sum(
            math.prod(access.region.shape)
            * module.tensors[module.shards[module.views[access.view_id - 1].shard_id - 1].tensor_id - 1].dtype.byte_width
            for access in send.writes
        )
        if transfer_id in collective_ids:
            continue
        target_generations = {
            (module.views[access.view_id - 1].object_id, module.views[access.view_id - 1].generation)
            for access in send.writes
        }
        consumers = tuple(
            item
            for item in module.ops
            if any((module.views[access.view_id - 1].object_id, module.views[access.view_id - 1].generation) in target_generations for access in item.reads)
        )
        assert consumers
        assert all(receive.done_token in item.after_tokens for item in consumers)
    module.verify(inputs.arch)


def _write_inputs(path: Path, inputs: KernelPipelineInputs) -> None:
    path.write_bytes(pickle.dumps((inputs.variants, inputs.source_graphs, inputs.arch, inputs.effective)))


def _run_driver(python: Path, payload: Path, workers: int, seed: str, *, torch_free: bool = False):
    env = dict(os.environ, PYTHONPATH=str(ROOT / "util/mesh_ir"), PYTHONHASHSEED=seed)
    command = [str(python), str(DRIVER), str(payload), str(workers)]
    if torch_free:
        command.append("--require-no-torch")
    return subprocess.run(command, env=env, text=True, capture_output=True, timeout=180)


def test_kernel_bundle_is_deterministic_across_hashseeds_and_workers(real_backend_inputs, tmp_path):
    inputs = _inputs(real_backend_inputs, 2)
    payload = tmp_path / "kernel-inputs.pickle"
    _write_inputs(payload, inputs)
    results = tuple(_run_driver(Path(sys.executable), payload, workers, seed) for seed in ("1", "77", "31337") for workers in (1, 2, 8))
    assert all(item.returncode == 0 for item in results), tuple((item.returncode, item.stderr) for item in results)
    documents = tuple(json.loads(item.stdout) for item in results)
    assert len(documents) == 9
    assert len({json.dumps(item["semantic"], sort_keys=True, separators=(",", ":")) for item in documents}) == 1
    assert tuple(item["observed"]["workers"] for item in documents) == (1, 2, 8) * 3
    assert all(item["observed"]["submitted"] == item["observed"]["completed"] == item["observed"]["task_count"] for item in documents)
    assert all(item["observed"]["pipeline_worker_pids"] and item["observed"]["driver_pid"] not in item["observed"]["pipeline_worker_pids"] for item in documents)


def test_real_kernel_backend_runs_without_torch(real_backend_inputs, tmp_path):
    inputs = _inputs(real_backend_inputs, 2)
    payload = tmp_path / "torch-free-kernel-inputs.pickle"
    _write_inputs(payload, inputs)
    result = _run_driver(BACKEND_PYTHON, payload, 2, "31337", torch_free=True)
    assert result.returncode == 0, result.stderr
    document = json.loads(result.stdout)
    assert document["observed"]["workers"] == 2
    assert document["observed"]["environment_probe_pids"]
    assert document["observed"]["pipeline_worker_pids"]
    assert document["observed"]["driver_pid"] not in document["observed"]["environment_probe_pids"]
    assert document["observed"]["driver_pid"] not in document["observed"]["pipeline_worker_pids"]
    assert document["observed"]["submitted"] == document["observed"]["completed"] == document["observed"]["task_count"]


def test_actual_accepted_operator_frontend_reaches_verified_program(tmp_path):
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_2x2.yaml")
    torch.manual_seed(71)
    args = (
        torch.randn(2, 3),
        torch.randn(3, 2),
        torch.randn(2, 2, 3),
        torch.randn(2, 3, 2),
        torch.randn(8, 2),
        torch.tensor([1, 3], dtype=torch.int32),
    )
    frontend = export_graph(
        OpZoo,
        args,
        FrontendRequest("forward", "p1", arch.digest().hex(), tmp_path / "frontend", source_project_root=ROOT / "util/mesh_ir"),
    )
    opcodes = {item.opcode for function in frontend.graph.functions for item in function.ops}
    assert {
        OpCode.EMBEDDING_LOOKUP,
        OpCode.GATHER_ROWS,
        OpCode.CONCAT,
        OpCode.EXPAND_VIEW,
        OpCode.SLICE_VIEW,
        OpCode.RMSNORM,
        OpCode.REDUCE_SUM,
        OpCode.REDUCE_MAX,
        OpCode.REDUCE_MEAN,
    } <= opcodes
    effective = resolve_compile_config(load_compile_config_text(OPERATOR_COMPILE, arch), arch)
    result = compile_backend(frontend.variants, arch, effective, source_graphs=(frontend.graph,), workers=2)
    verify_kernel_bundle_correspondence(frontend.variants, result.bundle)
    verify_program_kernel_correspondence(result.program, result.bundle)
    assert verify_program(result.program, arch).program is result.program
    assert len(result.passes) == 16
    assert result.program.semantics.intrinsic_traffic.descriptors
