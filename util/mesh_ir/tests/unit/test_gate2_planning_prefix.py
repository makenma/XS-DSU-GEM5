import dataclasses
import os
from pathlib import Path

import pytest

from mesh_ir.analysis.cost import (
    ElementWorkDomain,
    ExecutionWorkPhase,
    MatrixAccumulationWorkDomain,
    MatrixEpilogueWorkDomain,
    MatrixWorkDomain,
    MetadataWorkDomain,
    RowWorkDomain,
    WorkEstimate,
    WorkUnit,
    estimate_operation_cost,
    estimate_work,
    estimate_work_phases,
)
from mesh_ir.architecture import load_arch
from mesh_ir.canonical import U64_MAX, to_canonical
from mesh_ir.compile_config import (
    CompileOverrides,
    ProvenanceEntry,
    load_compile_config_text,
    resolve_compile_config,
    validate_effective_compile_config,
)
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Const, DType, Engine, FixedStride, TensorRole, contiguous_strides
from mesh_ir.ir.graph_ir import (
    ElementwiseAttrs,
    EmbeddingAttrs,
    GraphOp,
    GraphValue,
    MatmulAttrs,
    MovementAttrs,
    NormAttrs,
    OpCode,
    ReduceAttrs,
    SoftmaxAttrs,
    ViewAttrs,
    GraphFunction,
    GraphModule,
    PytreeSpec,
)
from mesh_ir.passes.execution import (
    PASS_REGISTRY,
    PassExecutor,
    PassTask,
    record_pass,
    verify_pass_chain,
)
from mesh_ir.passes.planning_prefix import (
    OperationCostRecord,
    OperationIdentity,
    PlanningState,
    graph_set_sha256,
    plan_graphs,
    plan_graphs_with_executor,
)
from mesh_ir.passes.shape_specialize import root_symbol_bindings_from_graph, specialize_profiles
from mesh_ir.ir.common import Symbol


ROOT = Path(__file__).resolve().parents[4]
ARCH_PATH = ROOT / "configs/example/ai_mesh/arch/mesh_2x2.yaml"
COMPILE = """
schema_version: mesh-compile-v1
entrypoints: [forward]
shape_profiles:
  forward:
    - {profile_id: small, logical_batch: 2, s2: 4}
symbol_bindings:
  forward:
    logical_batch: {input: x, axis: 0}
parallelism: {tensor_parallel: 2, pipeline_parallel: 1, expert_parallel: 1, data_parallel: 1}
placement: {core_order: row_major_yx, allowed_cores: [7, 2], reserve_cores: []}
tiling: {gemm_m: 8, gemm_n: 8, gemm_k: 8, double_buffer: true}
collectives: {all_reduce_algorithm: ring, chunk_bytes: 256}
runtime_model: {mode: FULL_TIMING, tensor_data: DIGEST_ONLY}
"""


DYNAMIC_COMPILE = COMPILE.replace(
    "    - {profile_id: small, logical_batch: 2, s2: 4}",
    "    - {profile_id: p2, logical_batch: 2}\n    - {profile_id: p4, logical_batch: 4}",
)
STATIC_COMPILE = COMPILE.replace(
    "    - {profile_id: small, logical_batch: 2, s2: 4}",
    "    - {profile_id: small}",
).replace(
    "symbol_bindings:\n  forward:\n    logical_batch: {input: x, axis: 0}\n",
    "symbol_bindings: {}\n",
)


def _pid_and_square(value: int) -> tuple[int, int]:
    return os.getpid(), value * value


def _reject_mesh(value: int) -> int:
    raise MeshIrError("E_CONFIG", "worker rejected task", value=value)


def _reject_runtime(value: int) -> int:
    raise RuntimeError(f"worker implementation failed: {value}")


def _mixed_failure(value: tuple[str, int]) -> int:
    if value[0] == "mesh":
        return _reject_mesh(value[1])
    return _reject_runtime(value[1])


def _value(value_id, shape, dtype=DType.FP32, *, strides=None, offset=0, root=None):
    dimensions = tuple(Const(item) for item in shape)
    return GraphValue(
        value_id,
        f"v{value_id}",
        TensorRole.ACTIVATION,
        dtype,
        dimensions,
        contiguous_strides(dimensions) if strides is None else tuple(FixedStride(item) for item in strides),
        offset,
        value_id if root is None else root,
    )


def _op(opcode, operands, result, attrs):
    return GraphOp(1, opcode, tuple(item.value_id for item in operands), (result.value_id,), attrs, "node:1")


def _work_map(work):
    return {(item.engine, item.unit, item.dtype): item.operations for item in work}


def _phase_maps(phases):
    return tuple((phase.engine, _work_map(phase.work)) for phase in phases)


def _work_totals(work):
    totals = {}
    for item in work:
        key = (item.engine, item.unit, item.dtype)
        totals[key] = totals.get(key, 0) + item.operations
    return totals


def _source_graph(arch_digest, *, entrypoint="forward", dynamic=True):
    batch = Symbol(1, "s1", 1, 4) if dynamic else Const(2)
    shape = (batch, Const(4))
    source = GraphValue(1, "x", TensorRole.INPUT, DType.FP32, shape, contiguous_strides(shape), 0, 1)
    output = GraphValue(2, "gelu", TensorRole.OUTPUT, DType.FP32, shape, contiguous_strides(shape), 0, 2)
    op = GraphOp(1, OpCode.GELU, (1,), (2,), ElementwiseAttrs(), "gelu:1")
    function = GraphFunction(1, entrypoint, (1,), (op,), (2,), PytreeSpec.leaf(), PytreeSpec.leaf())
    return GraphModule.create(arch_digest, "b" * 64, entrypoint, "symbolic" if dynamic else "small", (source, output), (function,), (batch,) if dynamic else ())


def _variants(source, config):
    profiles = tuple((profile.profile_id, profile.bindings) for profile in config.profiles_for(source.entrypoint))
    roots = tuple((binding.logical_name, binding.input_name, binding.axis) for binding in config.bindings_for(source.entrypoint))
    return specialize_profiles(source, roots, root_symbol_bindings_from_graph(source), profiles)


def _recreate(graph, *, values=None, ops=None):
    function = graph.functions[0]
    if ops is not None:
        function = dataclasses.replace(function, ops=ops)
    return GraphModule.create(
        graph.arch_digest,
        graph.source_semantic_hash,
        graph.entrypoint,
        graph.profile_id,
        graph.values if values is None else values,
        (function,),
        graph.symbols,
        graph.profile_bindings,
        graph.decomposition_digest,
        graph.debug_locations,
    )


def test_registry_is_the_single_complete_versioned_pipeline():
    assert tuple(item.name for item in PASS_REGISTRY) == (
        "LoadAndValidateExport",
        "DecomposeToPinnedCoreAten",
        "ImportGraphIR",
        "CanonicalizeFunctionalOps",
        "SpecializeShapeProfiles",
        "FuseVerifiedPatterns",
        "AnnotateCostAndBytes",
        "ChooseParallelPlan",
        "PlaceOpsAndTensors",
        "ShardAndPadTensors",
        "TileKernels",
        "BufferizeAndAlias",
        "LowerCollectives",
        "InsertDataMovement",
        "PlanStaticSRAM",
        "InsertHazardDependencies",
        "SchedulePerCoreStreams",
        "LowerDmaToSegments",
        "BindAddressesAndRelocations",
        "VerifyScheduledIR",
        "ComputeExpectedTraffic",
        "EncodeArtifacts",
    )
    assert tuple(item.version for item in PASS_REGISTRY) == (1,) * 22


def test_pass_records_validate_registry_versions_statistics_and_chain():
    first = record_pass("FuseVerifiedPatterns", "a" * 64, "a" * 64, 1, (("fusions", 0),))
    second = record_pass("AnnotateCostAndBytes", "a" * 64, "b" * 64, 2, (("operations", 3),))
    verify_pass_chain((first, second), "a" * 64, "b" * 64, (first.name, second.name))
    with pytest.raises(MeshIrError, match="chain"):
        verify_pass_chain((first, dataclasses.replace(second, input_hash="c" * 64)), "a" * 64, "b" * 64, (first.name, second.name))
    with pytest.raises(MeshIrError):
        record_pass("UnknownPass", "a" * 64, "b" * 64, 1)
    with pytest.raises(MeshIrError):
        record_pass("FuseVerifiedPatterns", "a" * 64, "b" * 64, True)
    with pytest.raises(MeshIrError):
        record_pass("FuseVerifiedPatterns", "a" * 64, "b" * 64, 1, (("z", 1), ("z", 2)))
    for version in (True, 1.0):
        with pytest.raises(MeshIrError):
            verify_pass_chain((dataclasses.replace(first, version=version),), "a" * 64, "a" * 64, (first.name,))


@pytest.mark.parametrize("workers", [1, 2, 8])
def test_executor_uses_spawn_workers_and_returns_canonical_submission_order(workers):
    tasks = tuple(PassTask(task_id, value) for task_id, value in (("third", 3), ("first", 1), ("second", 2)))
    with PassExecutor(workers) as executor:
        completed = executor.run("AnnotateCostAndBytes", _pid_and_square, tasks)
        diagnostics = executor.diagnostics
    assert tuple(item.task_id for item in completed) == ("third", "first", "second")
    assert tuple(item.value[1] for item in completed) == (9, 1, 4)
    assert all(item.value[0] != os.getpid() for item in completed)
    assert diagnostics.workers == workers
    assert diagnostics.submitted_tasks == diagnostics.completed_tasks == 3
    assert tuple(item.task_id for item in diagnostics.tasks) == ("third", "first", "second")
    assert sorted(item.submission_index for item in diagnostics.tasks) == [0, 1, 2]
    assert sorted(item.completion_index for item in diagnostics.tasks) == [0, 1, 2]
    assert all(item.worker_pid != os.getpid() and item.elapsed_ns > 0 for item in diagnostics.tasks)


def test_executor_reuses_one_live_context_and_accumulates_unambiguous_diagnostics():
    with PassExecutor(2) as executor:
        first = executor.run("AnnotateCostAndBytes", _pid_and_square, (PassTask("cost", 2),))
        first_snapshot = executor.diagnostics
        second = executor.run("ChooseParallelPlan", _pid_and_square, (PassTask("group", 3),))
        final_snapshot = executor.diagnostics
    assert first[0].value[1] == 4
    assert second[0].value[1] == 9
    assert tuple(item.pass_name for item in first_snapshot.tasks) == ("AnnotateCostAndBytes",)
    assert tuple(item.pass_name for item in final_snapshot.tasks) == ("AnnotateCostAndBytes", "ChooseParallelPlan")
    assert (first_snapshot.submitted_tasks, final_snapshot.submitted_tasks) == (1, 2)


@pytest.mark.parametrize("workers", [True, 0, -1, 1.0])
def test_executor_rejects_malformed_worker_count_before_pool_start(workers):
    with pytest.raises(MeshIrError) as error:
        PassExecutor(workers)
    assert error.value.code == "E_CONFIG"


def test_executor_rejects_invalid_pass_and_duplicate_task_ids():
    with PassExecutor(1) as executor:
        with pytest.raises(MeshIrError):
            executor.run("UnknownPass", _pid_and_square, (PassTask("a", 1),))
        with pytest.raises(MeshIrError):
            executor.run("AnnotateCostAndBytes", _pid_and_square, (PassTask("a", 1), PassTask("a", 2)))
    assert executor.diagnostics.submitted_tasks == 0


def test_executor_preserves_structured_mesh_error_and_does_not_mask_runtime_error():
    with PassExecutor(1) as executor:
        with pytest.raises(MeshIrError) as mesh_error:
            executor.run("AnnotateCostAndBytes", _reject_mesh, (PassTask("bad", 7),))
        with pytest.raises(RuntimeError, match="implementation failed"):
            executor.run("AnnotateCostAndBytes", _reject_runtime, (PassTask("broken", 9),))
    assert mesh_error.value.code == "E_CONFIG"
    assert mesh_error.value.message == "worker rejected task"
    assert mesh_error.value.context == {"value": 7}


def test_executor_raises_first_failure_by_submission_order_and_records_runtime_completion():
    with PassExecutor(2) as executor:
        with pytest.raises(MeshIrError, match="worker rejected task"):
            executor.run(
                "AnnotateCostAndBytes",
                _mixed_failure,
                (PassTask("mesh-first", ("mesh", 7)), PassTask("runtime-second", ("runtime", 9))),
            )
        diagnostics = executor.diagnostics
    assert diagnostics.submitted_tasks == diagnostics.completed_tasks == 2
    assert tuple(item.task_id for item in diagnostics.tasks) == ("mesh-first", "runtime-second")
    assert diagnostics.tasks[1].worker_pid is None
    assert diagnostics.tasks[1].elapsed_ns is None


def test_effective_compile_config_validation_reuses_resolution_and_provenance_policy():
    arch = load_arch(ARCH_PATH)
    config = load_compile_config_text(COMPILE, arch)
    effective = resolve_compile_config(config, arch, CompileOverrides(gemm_m=16, chunk_bytes=128))
    assert validate_effective_compile_config(effective, arch) is None


def test_effective_compile_config_accepts_schema_defaulted_empty_symbol_bindings():
    arch = load_arch(ARCH_PATH)
    config = load_compile_config_text(COMPILE.replace("symbol_bindings:\n  forward:\n    logical_batch: {input: x, axis: 0}\n", ""), arch)
    assert validate_effective_compile_config(resolve_compile_config(config, arch), arch) is None


@pytest.mark.parametrize(
    "forge",
    [
        lambda value: dataclasses.replace(value, arch_digest="0" * 64),
        lambda value: dataclasses.replace(value, config=dataclasses.replace(value.config, entrypoints=["forward"])),
        lambda value: dataclasses.replace(value, config=dataclasses.replace(value.config, shape_profiles=(("forward", []),))),
        lambda value: dataclasses.replace(value, provenance=list(value.provenance)),
        lambda value: dataclasses.replace(value, provenance=value.provenance + (value.provenance[-1],)),
        lambda value: dataclasses.replace(value, provenance=value.provenance + (ProvenanceEntry("unknown.path", "cli", 1),)),
        lambda value: dataclasses.replace(value, provenance=value.provenance + (ProvenanceEntry("tiling.gemm_n", "environment", 1),)),
        lambda value: dataclasses.replace(value, config=dataclasses.replace(value.config, tiling=dataclasses.replace(value.config.tiling, gemm_n=17))),
    ],
)
def test_effective_compile_config_rejects_forged_structure_policy_and_values(forge):
    arch = load_arch(ARCH_PATH)
    effective = resolve_compile_config(load_compile_config_text(COMPILE, arch), arch, CompileOverrides(gemm_m=16))
    with pytest.raises(MeshIrError) as error:
        validate_effective_compile_config(forge(effective), arch)
    assert error.value.code in {"E_CONFIG", "E_ARCH_DIGEST"}


def test_matrix_work_domains_separate_accumulation_and_one_time_epilogue():
    attrs = MatmulAttrs(alpha=1.0, accum_dtype=DType.FP32)
    accumulation = estimate_work(OpCode.MATMUL, attrs, (DType.FP16, DType.FP16), DType.FP32, MatrixAccumulationWorkDomain(1, 2, 3, 4))
    cast_epilogue = estimate_work(OpCode.MATMUL, attrs, (DType.FP16, DType.FP16), DType.FP16, MatrixEpilogueWorkDomain(1, 2, 3))
    copy_epilogue = estimate_work(OpCode.MATMUL, MatmulAttrs(), (DType.FP32, DType.FP32), DType.FP32, MatrixEpilogueWorkDomain(1, 2, 3))
    assert _work_map(accumulation) == {(Engine.TENSOR, WorkUnit.MAC, DType.FP16): 24}
    assert _work_map(cast_epilogue) == {(Engine.VECTOR, WorkUnit.CAST, DType.FP32): 6}
    assert _work_map(copy_epilogue) == {(Engine.VECTOR, WorkUnit.COPY, DType.FP32): 6}


def test_matrix_full_and_epilogue_domains_count_nondefault_alpha_beta_and_bias_once():
    attrs = MatmulAttrs(alpha=0.5, beta=0.25, accum_dtype=DType.FP32)
    operand_dtypes = (DType.FP16, DType.FP16, DType.FP16)
    full = estimate_work(OpCode.LINEAR_BIAS, attrs, operand_dtypes, DType.FP16, MatrixWorkDomain(2, 3, 5, 4))
    epilogue = estimate_work(OpCode.LINEAR_BIAS, attrs, operand_dtypes, DType.FP16, MatrixEpilogueWorkDomain(2, 3, 5))
    assert _work_map(full) == {
        (Engine.TENSOR, WorkUnit.MAC, DType.FP16): 120,
        (Engine.VECTOR, WorkUnit.MUL, DType.FP32): 60,
        (Engine.VECTOR, WorkUnit.ADD, DType.FP32): 30,
    }
    assert _work_map(epilogue) == {
        (Engine.VECTOR, WorkUnit.MUL, DType.FP32): 60,
        (Engine.VECTOR, WorkUnit.ADD, DType.FP32): 30,
        (Engine.VECTOR, WorkUnit.CAST, DType.FP32): 30,
    }
    tiled = tuple(
        estimate_work(OpCode.MATMUL, MatmulAttrs(accum_dtype=DType.FP32), (DType.FP16, DType.FP16), DType.FP32, MatrixAccumulationWorkDomain(1, 2, 3, k))
        for k in (2, 2)
    )
    assert sum(item.operations for part in tiled for item in part) == 24


@pytest.mark.parametrize(
    "opcode,attrs,operand_dtypes,domain,expected",
    [
        (OpCode.ADD, ElementwiseAttrs(alpha=2.0), (DType.FP32, DType.FP32), ElementWorkDomain(6), {(Engine.VECTOR, WorkUnit.ADD, DType.FP32): 6, (Engine.VECTOR, WorkUnit.MUL, DType.FP32): 6}),
        (OpCode.SUB, ElementwiseAttrs(), (DType.FP32, DType.FP32), ElementWorkDomain(6), {(Engine.VECTOR, WorkUnit.SUB, DType.FP32): 6}),
        (OpCode.MUL, ElementwiseAttrs(), (DType.FP32, DType.FP32), ElementWorkDomain(6), {(Engine.VECTOR, WorkUnit.MUL, DType.FP32): 6}),
        (OpCode.DIV, ElementwiseAttrs(), (DType.FP32, DType.FP32), ElementWorkDomain(6), {(Engine.VECTOR, WorkUnit.DIV, DType.FP32): 6}),
        (OpCode.RELU, ElementwiseAttrs(), (DType.FP32,), ElementWorkDomain(6), {(Engine.VECTOR, WorkUnit.MAX, DType.FP32): 6}),
        (OpCode.GELU, ElementwiseAttrs(), (DType.FP32,), ElementWorkDomain(6), {(Engine.VECTOR, WorkUnit.DIV, DType.FP32): 6, (Engine.VECTOR, WorkUnit.ERF, DType.FP32): 6, (Engine.VECTOR, WorkUnit.ADD, DType.FP32): 6, (Engine.VECTOR, WorkUnit.MUL, DType.FP32): 12}),
        (OpCode.GELU, ElementwiseAttrs(approximation="tanh"), (DType.FP32,), ElementWorkDomain(6), {(Engine.VECTOR, WorkUnit.MUL, DType.FP32): 36, (Engine.VECTOR, WorkUnit.ADD, DType.FP32): 12, (Engine.VECTOR, WorkUnit.TANH, DType.FP32): 6}),
        (OpCode.SILU, ElementwiseAttrs(), (DType.FP32,), ElementWorkDomain(6), {(Engine.VECTOR, WorkUnit.NEGATE, DType.FP32): 6, (Engine.VECTOR, WorkUnit.EXP, DType.FP32): 6, (Engine.VECTOR, WorkUnit.ADD, DType.FP32): 6, (Engine.VECTOR, WorkUnit.DIV, DType.FP32): 6}),
        (OpCode.EXP, ElementwiseAttrs(), (DType.FP32,), ElementWorkDomain(6), {(Engine.VECTOR, WorkUnit.EXP, DType.FP32): 6}),
        (OpCode.RSQRT, ElementwiseAttrs(), (DType.FP32,), ElementWorkDomain(6), {(Engine.VECTOR, WorkUnit.RSQRT, DType.FP32): 6}),
        (OpCode.CONTIGUOUS_COPY, ViewAttrs(shape=(Const(2), Const(3))), (DType.FP32,), ElementWorkDomain(6), {(Engine.VECTOR, WorkUnit.COPY, DType.FP32): 6}),
        (OpCode.CONCAT, MovementAttrs(), (DType.FP32,), ElementWorkDomain(6), {(Engine.VECTOR, WorkUnit.COPY, DType.FP32): 6}),
        (OpCode.GATHER_ROWS, MovementAttrs(), (DType.FP32, DType.INT32), ElementWorkDomain(6), {(Engine.VECTOR, WorkUnit.GATHER, DType.FP32): 6}),
        (OpCode.EMBEDDING_LOOKUP, EmbeddingAttrs(-1, False, False), (DType.FP32, DType.INT32), ElementWorkDomain(6), {(Engine.VECTOR, WorkUnit.GATHER, DType.FP32): 6}),
        (OpCode.RESHAPE_VIEW, ViewAttrs(shape=(Const(6),)), (DType.FP32,), MetadataWorkDomain(), {}),
    ],
)
def test_element_movement_and_metadata_work_formulas(opcode, attrs, operand_dtypes, domain, expected):
    assert _work_map(estimate_work(opcode, attrs, operand_dtypes, DType.FP32, domain)) == expected


def test_cost_rejects_unknown_gelu_approximation_instead_of_selecting_a_formula():
    with pytest.raises(MeshIrError) as error:
        estimate_work(OpCode.GELU, ElementwiseAttrs(approximation="unknown"), (DType.FP32,), DType.FP32, ElementWorkDomain(4))
    assert error.value.code == "E_EXPORT_UNSUPPORTED_OP"


def test_work_api_accepts_and_checks_complete_gather_operand_dtypes():
    work = estimate_work(OpCode.GATHER_ROWS, MovementAttrs(), (DType.FP16, DType.INT32), DType.FP16, ElementWorkDomain(6))
    assert _work_map(work) == {(Engine.VECTOR, WorkUnit.GATHER, DType.FP16): 6}
    with pytest.raises(MeshIrError) as error:
        estimate_work(OpCode.GATHER_ROWS, MovementAttrs(), (DType.FP16, DType.INT8), DType.FP16, ElementWorkDomain(6))
    assert error.value.code == "E_EXPORT_DTYPE"


def test_work_api_accepts_and_checks_complete_norm_affine_dtypes():
    attrs = NormAttrs((1,), 1e-5, True, True)
    estimate_work(OpCode.LAYERNORM, attrs, (DType.FP16, DType.FP16, DType.FP16), DType.FP16, RowWorkDomain(2, 4))
    with pytest.raises(MeshIrError) as error:
        estimate_work(OpCode.LAYERNORM, attrs, (DType.FP16, DType.FP32, DType.FP16), DType.FP16, RowWorkDomain(2, 4))
    assert error.value.code == "E_EXPORT_DTYPE"


def test_work_api_uses_all_tensor_operand_dtypes_for_promotion():
    work = estimate_work(OpCode.ADD, ElementwiseAttrs(), (DType.INT8, DType.FP16), DType.FP16, ElementWorkDomain(6))
    assert _work_map(work) == {(Engine.VECTOR, WorkUnit.ADD, DType.FP16): 6}
    with pytest.raises(MeshIrError) as error:
        estimate_work(OpCode.ADD, ElementwiseAttrs(), (DType.INT8, DType.FP16), DType.INT32, ElementWorkDomain(6))
    assert error.value.code == "E_EXPORT_DTYPE"


def test_reduction_softmax_and_norm_work_formulas_and_accumulation_dtypes():
    reduction = estimate_work(OpCode.REDUCE_MEAN, ReduceAttrs((1,), False, DType.FP32, DType.FP32), (DType.FP16,), DType.FP32, RowWorkDomain(2, 4))
    softmax = estimate_work(OpCode.SOFTMAX, SoftmaxAttrs(1, DType.FP16, True), (DType.FP16,), DType.FP16, RowWorkDomain(2, 4))
    layer = estimate_work(OpCode.LAYERNORM, NormAttrs((1,), 1e-5, True, True), (DType.BF16, DType.BF16, DType.BF16), DType.BF16, RowWorkDomain(2, 4))
    rms = estimate_work(OpCode.RMSNORM, NormAttrs((1,), 1e-5, True, False), (DType.FP16, DType.FP16), DType.FP16, RowWorkDomain(2, 4))
    assert _work_map(reduction) == {
        (Engine.REDUCE, WorkUnit.ADD, DType.FP32): 6,
        (Engine.VECTOR, WorkUnit.DIV, DType.FP32): 2,
    }
    assert _work_map(softmax) == {
        (Engine.REDUCE, WorkUnit.MAX, DType.FP32): 6,
        (Engine.REDUCE, WorkUnit.ADD, DType.FP32): 6,
        (Engine.VECTOR, WorkUnit.SUB, DType.FP32): 8,
        (Engine.VECTOR, WorkUnit.EXP, DType.FP32): 8,
        (Engine.VECTOR, WorkUnit.DIV, DType.FP32): 8,
        (Engine.VECTOR, WorkUnit.PREDICATE, DType.FP32): 8,
        (Engine.REDUCE, WorkUnit.LOGICAL_AND, DType.FP32): 6,
        (Engine.VECTOR, WorkUnit.SELECT, DType.FP32): 8,
    }
    assert _work_map(layer) == {
        (Engine.REDUCE, WorkUnit.ADD, DType.FP32): 12,
        (Engine.VECTOR, WorkUnit.DIV, DType.FP32): 4,
        (Engine.VECTOR, WorkUnit.SUB, DType.FP32): 8,
        (Engine.VECTOR, WorkUnit.MUL, DType.FP32): 24,
        (Engine.VECTOR, WorkUnit.ADD, DType.FP32): 10,
        (Engine.VECTOR, WorkUnit.RSQRT, DType.FP32): 2,
    }
    assert _work_map(rms) == {
        (Engine.REDUCE, WorkUnit.ADD, DType.FP32): 6,
        (Engine.VECTOR, WorkUnit.MUL, DType.FP32): 24,
        (Engine.VECTOR, WorkUnit.DIV, DType.FP32): 2,
        (Engine.VECTOR, WorkUnit.ADD, DType.FP32): 2,
        (Engine.VECTOR, WorkUnit.RSQRT, DType.FP32): 2,
    }


def test_softmax_work_phases_preserve_exact_resource_dependency_order():
    standard = estimate_work_phases(OpCode.SOFTMAX, SoftmaxAttrs(1, DType.FP16), (DType.FP16,), DType.FP16, RowWorkDomain(2, 4))
    safe = estimate_work_phases(OpCode.SOFTMAX, SoftmaxAttrs(1, DType.FP16, True), (DType.FP16,), DType.FP16, RowWorkDomain(2, 4))
    assert _phase_maps(standard) == (
        (Engine.REDUCE, {(Engine.REDUCE, WorkUnit.MAX, DType.FP32): 6}),
        (Engine.VECTOR, {(Engine.VECTOR, WorkUnit.SUB, DType.FP32): 8, (Engine.VECTOR, WorkUnit.EXP, DType.FP32): 8}),
        (Engine.REDUCE, {(Engine.REDUCE, WorkUnit.ADD, DType.FP32): 6}),
        (Engine.VECTOR, {(Engine.VECTOR, WorkUnit.DIV, DType.FP32): 8}),
    )
    assert _phase_maps(safe) == (
        (Engine.VECTOR, {(Engine.VECTOR, WorkUnit.PREDICATE, DType.FP32): 8}),
        (Engine.REDUCE, {(Engine.REDUCE, WorkUnit.LOGICAL_AND, DType.FP32): 6, (Engine.REDUCE, WorkUnit.MAX, DType.FP32): 6}),
        (Engine.VECTOR, {(Engine.VECTOR, WorkUnit.SUB, DType.FP32): 8, (Engine.VECTOR, WorkUnit.EXP, DType.FP32): 8}),
        (Engine.REDUCE, {(Engine.REDUCE, WorkUnit.ADD, DType.FP32): 6}),
        (Engine.VECTOR, {(Engine.VECTOR, WorkUnit.DIV, DType.FP32): 8, (Engine.VECTOR, WorkUnit.SELECT, DType.FP32): 8}),
    )


def test_execution_work_phase_requires_positive_checked_single_engine_work():
    valid = ExecutionWorkPhase((WorkEstimate(Engine.VECTOR, WorkUnit.ADD, DType.FP32, 3),))
    assert valid.engine is Engine.VECTOR
    invalid = (
        (),
        [WorkEstimate(Engine.VECTOR, WorkUnit.ADD, DType.FP32, 3)],
        (WorkEstimate(Engine.VECTOR, WorkUnit.ADD, DType.FP32, 0),),
        (WorkEstimate(Engine.VECTOR, WorkUnit.ADD, DType.FP32, True),),
        (WorkEstimate(Engine.DMA_READ, WorkUnit.ADD, DType.FP32, 3),),
        (
            WorkEstimate(Engine.VECTOR, WorkUnit.ADD, DType.FP32, 3),
            WorkEstimate(Engine.REDUCE, WorkUnit.ADD, DType.FP32, 3),
        ),
    )
    for work in invalid:
        with pytest.raises(MeshIrError):
            ExecutionWorkPhase(work)


def test_normalization_work_phases_keep_two_reductions_and_affine_order():
    layer = estimate_work_phases(
        OpCode.LAYERNORM,
        NormAttrs((1,), 1e-5, True, True),
        (DType.BF16, DType.BF16, DType.BF16),
        DType.BF16,
        RowWorkDomain(2, 4),
    )
    rms = estimate_work_phases(
        OpCode.RMSNORM,
        NormAttrs((1,), 1e-5, True, False),
        (DType.FP16, DType.FP16),
        DType.FP16,
        RowWorkDomain(2, 4),
    )
    assert _phase_maps(layer) == (
        (Engine.REDUCE, {(Engine.REDUCE, WorkUnit.ADD, DType.FP32): 6}),
        (Engine.VECTOR, {(Engine.VECTOR, WorkUnit.DIV, DType.FP32): 2, (Engine.VECTOR, WorkUnit.SUB, DType.FP32): 8, (Engine.VECTOR, WorkUnit.MUL, DType.FP32): 8}),
        (Engine.REDUCE, {(Engine.REDUCE, WorkUnit.ADD, DType.FP32): 6}),
        (Engine.VECTOR, {(Engine.VECTOR, WorkUnit.DIV, DType.FP32): 2, (Engine.VECTOR, WorkUnit.ADD, DType.FP32): 10, (Engine.VECTOR, WorkUnit.RSQRT, DType.FP32): 2, (Engine.VECTOR, WorkUnit.MUL, DType.FP32): 16}),
    )
    assert _phase_maps(rms) == (
        (Engine.VECTOR, {(Engine.VECTOR, WorkUnit.MUL, DType.FP32): 8}),
        (Engine.REDUCE, {(Engine.REDUCE, WorkUnit.ADD, DType.FP32): 6}),
        (Engine.VECTOR, {(Engine.VECTOR, WorkUnit.DIV, DType.FP32): 2, (Engine.VECTOR, WorkUnit.ADD, DType.FP32): 2, (Engine.VECTOR, WorkUnit.RSQRT, DType.FP32): 2, (Engine.VECTOR, WorkUnit.MUL, DType.FP32): 16}),
    )


def test_empty_segments_coalesce_without_losing_nonempty_row_work():
    standard = estimate_work_phases(OpCode.SOFTMAX, SoftmaxAttrs(1, DType.FP32), (DType.FP32,), DType.FP32, RowWorkDomain(2, 1))
    safe = estimate_work_phases(OpCode.SOFTMAX, SoftmaxAttrs(1, DType.FP32, True), (DType.FP32,), DType.FP32, RowWorkDomain(2, 1))
    layer = estimate_work_phases(OpCode.LAYERNORM, NormAttrs((1,), 1e-5, False, False), (DType.FP32,), DType.FP32, RowWorkDomain(2, 1))
    rms = estimate_work_phases(OpCode.RMSNORM, NormAttrs((1,), 1e-5, False, False), (DType.FP32,), DType.FP32, RowWorkDomain(2, 1))
    mean = estimate_work_phases(OpCode.REDUCE_MEAN, ReduceAttrs((1,), False, DType.FP32, DType.FP32), (DType.FP32,), DType.FP32, RowWorkDomain(2, 0))
    k_zero = estimate_work_phases(OpCode.MATMUL, MatmulAttrs(alpha=0.5), (DType.FP32, DType.FP32), DType.FP32, MatrixWorkDomain(1, 2, 3, 0))
    assert tuple(phase.engine for phase in standard) == (Engine.VECTOR,)
    assert tuple(phase.engine for phase in safe) == (Engine.VECTOR,)
    assert tuple(phase.engine for phase in layer) == (Engine.VECTOR,)
    assert tuple(phase.engine for phase in rms) == (Engine.VECTOR,)
    assert _phase_maps(mean) == ((Engine.VECTOR, {(Engine.VECTOR, WorkUnit.DIV, DType.FP32): 2}),)
    assert _phase_maps(k_zero) == ((Engine.VECTOR, {(Engine.VECTOR, WorkUnit.MUL, DType.FP32): 6}),)
    assert estimate_work_phases(OpCode.SOFTMAX, SoftmaxAttrs(1, DType.FP32), (DType.FP32,), DType.FP32, RowWorkDomain(2, 0)) == ()
    assert estimate_work_phases(OpCode.MATMUL, MatmulAttrs(alpha=0.5), (DType.FP32, DType.FP32), DType.FP32, MatrixWorkDomain(0, 2, 3, 4)) == ()


def test_matrix_phases_and_every_aggregate_remain_derived_from_ordered_work():
    cases = (
        (OpCode.MATMUL, MatmulAttrs(alpha=0.5), (DType.FP32, DType.FP32), DType.FP32, MatrixWorkDomain(1, 2, 3, 4)),
        (OpCode.BMM, MatmulAttrs(batch_axes=(0,)), (DType.FP32, DType.FP32), DType.FP32, MatrixWorkDomain(2, 2, 3, 4)),
        (OpCode.LINEAR_BIAS, MatmulAttrs(alpha=0.5, beta=0.25), (DType.FP32, DType.FP32, DType.FP32), DType.FP32, MatrixWorkDomain(1, 2, 3, 4)),
        (OpCode.MATMUL, MatmulAttrs(accum_dtype=DType.FP32), (DType.FP16, DType.FP16), DType.FP32, MatrixAccumulationWorkDomain(1, 2, 3, 4)),
        (OpCode.MATMUL, MatmulAttrs(accum_dtype=DType.FP32), (DType.FP16, DType.FP16), DType.FP16, MatrixEpilogueWorkDomain(1, 2, 3)),
        (OpCode.ADD, ElementwiseAttrs(alpha=2.0), (DType.FP32, DType.FP32), DType.FP32, ElementWorkDomain(6)),
        (OpCode.SUB, ElementwiseAttrs(), (DType.FP32, DType.FP32), DType.FP32, ElementWorkDomain(6)),
        (OpCode.MUL, ElementwiseAttrs(), (DType.FP32, DType.FP32), DType.FP32, ElementWorkDomain(6)),
        (OpCode.DIV, ElementwiseAttrs(), (DType.FP32, DType.FP32), DType.FP32, ElementWorkDomain(6)),
        (OpCode.RELU, ElementwiseAttrs(), (DType.FP32,), DType.FP32, ElementWorkDomain(6)),
        (OpCode.GELU, ElementwiseAttrs(), (DType.FP32,), DType.FP32, ElementWorkDomain(6)),
        (OpCode.GELU, ElementwiseAttrs(approximation="tanh"), (DType.FP32,), DType.FP32, ElementWorkDomain(6)),
        (OpCode.SILU, ElementwiseAttrs(), (DType.FP32,), DType.FP32, ElementWorkDomain(6)),
        (OpCode.EXP, ElementwiseAttrs(), (DType.FP32,), DType.FP32, ElementWorkDomain(6)),
        (OpCode.RSQRT, ElementwiseAttrs(), (DType.FP32,), DType.FP32, ElementWorkDomain(6)),
        (OpCode.CONTIGUOUS_COPY, ViewAttrs(shape=(Const(6),)), (DType.FP32,), DType.FP32, ElementWorkDomain(6)),
        (OpCode.CONCAT, MovementAttrs(), (DType.FP32,), DType.FP32, ElementWorkDomain(6)),
        (OpCode.GATHER_ROWS, MovementAttrs(), (DType.FP32, DType.INT32), DType.FP32, ElementWorkDomain(6)),
        (OpCode.EMBEDDING_LOOKUP, EmbeddingAttrs(-1, False, False), (DType.FP32, DType.INT32), DType.FP32, ElementWorkDomain(6)),
        (OpCode.RESHAPE_VIEW, ViewAttrs(shape=(Const(6),)), (DType.FP32,), DType.FP32, MetadataWorkDomain()),
        (OpCode.REDUCE_SUM, ReduceAttrs((1,), False, DType.FP32, DType.FP32), (DType.FP32,), DType.FP32, RowWorkDomain(2, 4)),
        (OpCode.REDUCE_MAX, ReduceAttrs((1,), False, DType.FP32, DType.FP32), (DType.FP32,), DType.FP32, RowWorkDomain(2, 4)),
        (OpCode.REDUCE_MEAN, ReduceAttrs((1,), False, DType.FP32, DType.FP32), (DType.FP32,), DType.FP32, RowWorkDomain(2, 4)),
        (OpCode.SOFTMAX, SoftmaxAttrs(1, DType.FP32, True), (DType.FP32,), DType.FP32, RowWorkDomain(2, 4)),
        (OpCode.LAYERNORM, NormAttrs((1,), 1e-5, False, False), (DType.FP32,), DType.FP32, RowWorkDomain(2, 4)),
        (OpCode.RMSNORM, NormAttrs((1,), 1e-5, False, False), (DType.FP32,), DType.FP32, RowWorkDomain(2, 4)),
    )
    for opcode, attrs, operand_dtypes, output_dtype, domain in cases:
        phases = estimate_work_phases(opcode, attrs, operand_dtypes, output_dtype, domain)
        flattened = tuple(item for phase in phases for item in phase.work)
        aggregate = estimate_work(opcode, attrs, operand_dtypes, output_dtype, domain)
        assert _work_map(aggregate) == _work_totals(flattened)
        assert tuple((item.engine, item.unit, item.dtype) for item in aggregate) == tuple(dict.fromkeys((item.engine, item.unit, item.dtype) for item in flattened))


def test_empty_result_and_zero_fan_in_have_distinct_work_semantics():
    empty_softmax = estimate_work(OpCode.SOFTMAX, SoftmaxAttrs(1, DType.FP32), (DType.FP32,), DType.FP32, RowWorkDomain(2, 0))
    empty_mean = estimate_work(OpCode.REDUCE_MEAN, ReduceAttrs((1,), False, DType.FP32, DType.FP32), (DType.FP32,), DType.FP32, RowWorkDomain(2, 0))
    empty_matrix = estimate_work(OpCode.MATMUL, MatmulAttrs(), (DType.FP32, DType.FP32), DType.FP32, MatrixWorkDomain(1, 2, 3, 0))
    assert empty_softmax == ()
    assert _work_map(empty_mean) == {(Engine.VECTOR, WorkUnit.DIV, DType.FP32): 2}
    assert empty_matrix == ()


def test_operation_cost_preserves_strided_storage_access_and_view_alias_facts():
    source = _value(1, (2, 3), strides=(1, 4), offset=2)
    copied = _value(2, (2, 3))
    copy_cost = estimate_operation_cost(_op(OpCode.CONTIGUOUS_COPY, (source,), copied, ViewAttrs(shape=copied.shape)), (source,), copied)
    viewed = _value(3, (3, 2), strides=(4, 1), offset=2, root=1)
    view_cost = estimate_operation_cost(_op(OpCode.RESHAPE_VIEW, (source,), viewed, ViewAttrs(shape=viewed.shape)), (source,), viewed)
    assert (copy_cost.operand_accesses[0].logical_tensor_bytes, copy_cost.operand_accesses[0].storage_span_bytes, copy_cost.operand_accesses[0].accessed_bytes) == (24, 48, 24)
    assert (copy_cost.logical_output_bytes, copy_cost.allocated_storage_bytes, copy_cost.vector_ops) == (24, 24, 6)
    assert view_cost.operand_accesses[0].accessed_bytes == view_cost.result_access.accessed_bytes == 0
    assert view_cost.allocated_storage_bytes == 0
    assert (view_cost.result_access.logical_tensor_bytes, view_cost.result_access.storage_span_bytes) == (24, 48)


def test_operation_cost_counts_matrix_broadcast_gather_embedding_and_scalar_bytes():
    lhs = _value(1, (2, 3), DType.FP16)
    rhs = _value(2, (3, 4), DType.FP16)
    result = _value(3, (2, 4), DType.FP16)
    matrix = estimate_operation_cost(_op(OpCode.MATMUL, (lhs, rhs), result, MatmulAttrs(accum_dtype=DType.FP32)), (lhs, rhs), result)
    assert (matrix.logical_input_bytes, matrix.logical_output_bytes, matrix.allocated_storage_bytes, matrix.macs) == (36, 16, 16, 24)
    broadcast = estimate_operation_cost(_op(OpCode.ADD, (_value(4, (2, 1)), _value(5, (1, 3))), _value(6, (2, 3)), ElementwiseAttrs()), (_value(4, (2, 1)), _value(5, (1, 3))), _value(6, (2, 3)))
    scalar = estimate_operation_cost(_op(OpCode.RELU, (_value(7, ()),), _value(8, ()), ElementwiseAttrs()), (_value(7, ()),), _value(8, ()))
    table = _value(9, (10, 4))
    indices = _value(10, (2, 3), DType.INT32)
    gathered = _value(11, (2, 3, 4))
    embedding = estimate_operation_cost(_op(OpCode.EMBEDDING_LOOKUP, (table, indices), gathered, EmbeddingAttrs(-1, False, False)), (table, indices), gathered)
    assert (broadcast.logical_input_bytes, broadcast.logical_output_bytes, broadcast.vector_ops) == (20, 24, 6)
    assert (scalar.logical_input_bytes, scalar.logical_output_bytes, scalar.vector_ops) == (4, 4, 1)
    assert tuple(item.accessed_bytes for item in embedding.operand_accesses) == (96, 24)
    assert (embedding.logical_output_bytes, embedding.vector_ops) == (96, 24)


def test_planning_canonical_boundary_preserves_complete_cost_and_group_records():
    arch = load_arch(ARCH_PATH)
    config = load_compile_config_text(DYNAMIC_COMPILE, arch)
    source = _source_graph(arch.digest().hex())
    result = plan_graphs(_variants(source, config), arch, resolve_compile_config(config, arch), source_graphs=(source,))
    lhs = _value(10, (2, 3), DType.FP16)
    rhs = _value(11, (3, 4), DType.FP16)
    output = _value(12, (2, 4), DType.FP16)
    present = OperationCostRecord(
        OperationIdentity("forward", "p2", 1, 2),
        estimate_operation_cost(
            _op(OpCode.MATMUL, (lhs, rhs), output, MatmulAttrs(accum_dtype=DType.FP32)),
            (lhs, rhs),
            output,
        ),
    )
    state = PlanningState(
        result.state.variants,
        (result.state.operation_costs[0], present),
        result.state.parallel_groups,
    )
    document = state.semantic_dict()
    assert document["operation_costs"] == tuple(dataclasses.asdict(item) for item in state.operation_costs)
    assert document["parallel_groups"] == tuple(dataclasses.asdict(item) for item in state.parallel_groups)
    canonical = to_canonical(document)
    assert canonical["operation_costs"][0]["cost"]["accumulation_dtype"] is None
    assert canonical["operation_costs"][1]["cost"]["accumulation_dtype"] == DType.FP32.value


def test_shared_canonical_still_omits_absent_dataclass_fields():
    arch = load_arch(ARCH_PATH)
    config = load_compile_config_text(DYNAMIC_COMPILE, arch)
    source = _source_graph(arch.digest().hex())
    result = plan_graphs(_variants(source, config), arch, resolve_compile_config(config, arch), source_graphs=(source,))
    assert result.state.operation_costs[0].cost.accumulation_dtype is None
    assert "accumulation_dtype" not in to_canonical(result.state.operation_costs[0].cost)


def test_planning_pass_hashes_are_exactly_derived_from_adjacent_states():
    arch = load_arch(ARCH_PATH)
    config = load_compile_config_text(DYNAMIC_COMPILE, arch)
    source = _source_graph(arch.digest().hex())
    variants = _variants(source, config)
    result = plan_graphs(variants, arch, resolve_compile_config(config, arch), source_graphs=(source,))
    graph_hash = graph_set_sha256(variants)
    cost_hash = PlanningState(variants, result.state.operation_costs, ()).semantic_sha256()
    final_hash = result.state.semantic_sha256()
    assert tuple((item.input_hash, item.output_hash) for item in result.passes) == (
        (graph_hash, graph_hash),
        (graph_hash, cost_hash),
        (cost_hash, final_hash),
    )


def test_work_and_extent_arithmetic_rejects_u64_overflow():
    with pytest.raises(MeshIrError) as work_error:
        estimate_work(OpCode.GELU, ElementwiseAttrs(), (DType.FP32,), DType.FP32, ElementWorkDomain(U64_MAX))
    assert work_error.value.code == "E_ABI_OVERFLOW"
    huge = _value(1, (1 << 63, 3), strides=(3, 1))
    result = _value(2, (1 << 63, 3), strides=(3, 1))
    with pytest.raises(MeshIrError) as extent_error:
        estimate_operation_cost(_op(OpCode.RELU, (huge,), result, ElementwiseAttrs()), (huge,), result)
    assert extent_error.value.code == "E_ABI_OVERFLOW"


def test_planning_prefix_runs_real_passes_in_config_profile_order_and_preserves_graphs():
    arch = load_arch(ARCH_PATH)
    config = load_compile_config_text(DYNAMIC_COMPILE, arch)
    effective = resolve_compile_config(config, arch)
    source = _source_graph(arch.digest().hex())
    variants = _variants(source, config)
    result = plan_graphs(variants, arch, effective, source_graphs=(source,), workers=2)
    assert result.state.variants == variants
    assert result.source_graphs == (source,)
    assert tuple(item.name for item in result.passes) == ("FuseVerifiedPatterns", "AnnotateCostAndBytes", "ChooseParallelPlan")
    assert result.passes[0].input_hash == result.passes[0].output_hash == graph_set_sha256(variants)
    verify_pass_chain(result.passes, graph_set_sha256(variants), result.state.semantic_sha256(), tuple(item.name for item in result.passes))
    assert tuple((item.identity.entrypoint, item.identity.profile_id, item.identity.function_id, item.identity.op_id) for item in result.state.operation_costs) == (
        ("forward", "p2", 1, 1),
        ("forward", "p4", 1, 1),
    )
    assert tuple(group.group_id for group in result.state.parallel_groups) == (1, 2)
    assert all(tuple(item.logical_rank for item in group.participants) == (0, 1) for group in result.state.parallel_groups)
    assert all(group.admissible_core_ids == (7, 2) for group in result.state.parallel_groups)
    assert result.execution.submitted_tasks == result.execution.completed_tasks == 4
    assert tuple(item.pass_name for item in result.execution.tasks) == (
        "AnnotateCostAndBytes",
        "AnnotateCostAndBytes",
        "ChooseParallelPlan",
        "ChooseParallelPlan",
    )
    assert all(item.worker_pid != os.getpid() for item in result.execution.tasks)


@pytest.mark.parametrize("workers", [1, 2, 8])
def test_planning_prefix_is_deterministic_across_real_executor_sizes(workers):
    arch = load_arch(ARCH_PATH)
    config = load_compile_config_text(DYNAMIC_COMPILE, arch)
    source = _source_graph(arch.digest().hex())
    variants = _variants(source, config)
    baseline = plan_graphs(variants, arch, resolve_compile_config(config, arch), source_graphs=(source,), workers=1)
    candidate = plan_graphs(variants, arch, resolve_compile_config(config, arch), source_graphs=(source,), workers=workers)
    assert candidate.state.semantic_bytes() == baseline.state.semantic_bytes()
    assert tuple((item.input_hash, item.output_hash, item.statistics) for item in candidate.passes) == tuple((item.input_hash, item.output_hash, item.statistics) for item in baseline.passes)


def test_planning_with_live_executor_reports_only_the_current_diagnostic_slice():
    arch = load_arch(ARCH_PATH)
    config = load_compile_config_text(DYNAMIC_COMPILE, arch)
    source = _source_graph(arch.digest().hex())
    variants = _variants(source, config)
    with PassExecutor(2) as executor:
        executor.run("AnnotateCostAndBytes", _pid_and_square, (PassTask("prior", 2),))
        result = plan_graphs_with_executor(variants, arch, resolve_compile_config(config, arch), source_graphs=(source,), executor=executor)
        full = executor.diagnostics
    assert result.execution.submitted_tasks == 4
    assert all(item.task_id != "prior" for item in result.execution.tasks)
    assert full.submitted_tasks == 5


def test_static_identity_frontend_shape_path_is_not_respecialized():
    arch = load_arch(ARCH_PATH)
    config = load_compile_config_text(STATIC_COMPILE, arch)
    source = _source_graph(arch.digest().hex(), dynamic=False)
    result = plan_graphs((source,), arch, resolve_compile_config(config, arch), source_graphs=(source,))
    assert result.state.variants[0] is source
    assert result.passes[0].input_hash == graph_set_sha256((source,))


def test_explicit_static_specialization_with_same_profile_id_is_also_verified():
    arch = load_arch(ARCH_PATH)
    config = load_compile_config_text(STATIC_COMPILE, arch)
    source = _source_graph(arch.digest().hex(), dynamic=False)
    variant = specialize_profiles(source, (), (), (("small", ()),))[0]
    assert variant.semantic_sha256 != source.semantic_sha256
    result = plan_graphs((variant,), arch, resolve_compile_config(config, arch), source_graphs=(source,))
    assert result.state.variants == (variant,)


def test_static_identity_still_validates_configured_root_bindings():
    arch = load_arch(ARCH_PATH)
    text = STATIC_COMPILE.replace("symbol_bindings: {}", "symbol_bindings:\n  forward:\n    unbound: {input: missing, axis: 0}")
    config = load_compile_config_text(text, arch)
    source = _source_graph(arch.digest().hex(), dynamic=False)
    with pytest.raises(MeshIrError) as error:
        plan_graphs((source,), arch, resolve_compile_config(config, arch), source_graphs=(source,))
    assert error.value.code in {"E_SHAPE_UNBOUND", "E_SHAPE_PROFILE_MISMATCH"}


@pytest.mark.parametrize("mutation", ["missing", "extra", "arch", "layout", "attrs", "source"])
def test_planning_rejects_invalid_membership_architecture_and_fresh_semantic_mutations(mutation):
    arch = load_arch(ARCH_PATH)
    config = load_compile_config_text(DYNAMIC_COMPILE, arch)
    source = _source_graph(arch.digest().hex())
    variants = _variants(source, config)
    sources = (source,)
    if mutation == "missing":
        variants = variants[:1]
    elif mutation == "extra":
        variants = variants + (variants[-1],)
    elif mutation == "arch":
        variants = (_recreate(dataclasses.replace(variants[0], arch_digest="c" * 64)), variants[1])
    elif mutation == "layout":
        value = dataclasses.replace(variants[0].values[-1], strides=(FixedStride(5), FixedStride(1)), storage_extent_bytes=36)
        variants = (_recreate(variants[0], values=(*variants[0].values[:-1], value)), variants[1])
    elif mutation == "attrs":
        op = dataclasses.replace(variants[0].functions[0].ops[0], attrs=ElementwiseAttrs(approximation="tanh"))
        variants = (_recreate(variants[0], ops=(op,)), variants[1])
    else:
        sources = (_source_graph(arch.digest().hex(), entrypoint="decode"),)
    with pytest.raises(MeshIrError) as error:
        plan_graphs(variants, arch, resolve_compile_config(config, arch), source_graphs=sources)
    assert error.value.code in {"E_ARCH_DIGEST", "E_SHAPE_PROFILE_MISMATCH", "E_CONFIG"}


def test_planning_rejects_insufficient_admissible_cores_without_changing_logical_degree():
    arch = load_arch(ARCH_PATH)
    config = load_compile_config_text(DYNAMIC_COMPILE, arch)
    source = _source_graph(arch.digest().hex())
    variants = _variants(source, config)
    placement = dataclasses.replace(config.placement, allowed_cores=(7,))
    forged_config = dataclasses.replace(config, placement=placement)
    effective = dataclasses.replace(resolve_compile_config(config, arch), config=forged_config)
    with pytest.raises(MeshIrError) as error:
        plan_graphs(variants, arch, effective, source_graphs=(source,))
    assert error.value.code in {"E_CONFIG", "E_PLACEMENT_INFEASIBLE"}


@pytest.mark.parametrize("degree", [1, 2, 4])
def test_parallel_groups_keep_exact_logical_degree_separate_from_sparse_physical_ids(degree):
    arch = load_arch(ARCH_PATH)
    text = DYNAMIC_COMPILE.replace("allowed_cores: [7, 2]", "allowed_cores: [7, 2, 9, 13]").replace("tensor_parallel: 2", f"tensor_parallel: {degree}")
    config = load_compile_config_text(text, arch)
    source = _source_graph(arch.digest().hex())
    result = plan_graphs(_variants(source, config), arch, resolve_compile_config(config, arch), source_graphs=(source,))
    assert all(tuple(item.logical_rank for item in group.participants) == tuple(range(degree)) for group in result.state.parallel_groups)
    assert all(group.admissible_core_ids == (7, 2, 9, 13) for group in result.state.parallel_groups)
