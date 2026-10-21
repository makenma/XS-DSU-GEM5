from dataclasses import replace
from pathlib import Path

import pytest

from mesh_ir.architecture import load_arch
from mesh_ir.compile_config import load_compile_config_text, resolve_compile_config
from mesh_ir.compiler import BackendCompilationResult, compile_backend
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Const, DType, TensorRole, contiguous_strides
from mesh_ir.ir.graph_ir import ElementwiseAttrs, GraphFunction, GraphModule, GraphOp, GraphValue, OpCode, PytreeSpec
from mesh_ir.passes.execution import PASS_REGISTRY, verify_pass_chain
from mesh_ir.passes.planning_prefix import graph_set_sha256
from mesh_ir.scheduled.verify import verify_program, verify_program_kernel_correspondence
from tests.golden.support.compiler_fixtures import SMALL_MESH_COMPILE as COMPILE


ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture(scope="module")
def direct_compilation():
    arch = load_arch(ROOT / "configs/example/ai_mesh/arch/mesh_2x2.yaml")
    shape = (Const(2), Const(4))
    source = GraphValue(1, "x", TensorRole.INPUT, DType.FP32, shape, contiguous_strides(shape), 0, 1, content_sha256="c" * 64)
    output = GraphValue(2, "relu", TensorRole.OUTPUT, DType.FP32, shape, contiguous_strides(shape), 0, 2)
    op = GraphOp(1, OpCode.RELU, (1,), (2,), ElementwiseAttrs(), "relu:1")
    function = GraphFunction(1, "forward", (1,), (op,), (2,), PytreeSpec.leaf(), PytreeSpec.leaf())
    graph = GraphModule.create(arch.digest().hex(), "b" * 64, "forward", "p1", (source, output), (function,))
    effective = resolve_compile_config(load_compile_config_text(COMPILE, arch), arch)
    return arch, graph, effective, compile_backend((graph,), arch, effective, source_graphs=(graph,), workers=2)


def test_compile_backend_composes_real_stages_and_derived_result_views(direct_compilation):
    arch, graph, _, result = direct_compilation
    names = tuple(item.name for item in PASS_REGISTRY)
    expected = names[names.index("FuseVerifiedPatterns"):names.index("ComputeExpectedTraffic") + 1]
    assert result.source_graphs is result.planning.source_graphs
    assert result.variants is result.planning.state.variants
    assert result.bundle is result.kernel.bundle
    assert result.program is result.scheduled.program
    assert result.execution is result.scheduled.execution
    assert result.intrinsic_traffic is result.program.semantics.intrinsic_traffic
    assert tuple(item.name for item in result.passes) == expected
    assert len(result.passes) == 16
    assert result.passes == result.planning.passes + result.kernel.passes + result.scheduled.passes
    assert verify_pass_chain(result.planning.passes, graph_set_sha256((graph,)), result.planning.state.semantic_sha256(), expected[:3]) is None
    assert verify_pass_chain(result.kernel.passes, result.planning.state.semantic_sha256(), result.bundle.semantic_sha256, expected[3:9]) is None
    assert verify_pass_chain(result.scheduled.passes, result.bundle.semantic_sha256, result.program.semantic_sha256, expected[9:]) is None
    assert verify_pass_chain(result.passes, graph_set_sha256((graph,)), result.program.semantic_sha256, expected) is None
    assert verify_program(result.program, arch).program is result.program
    assert verify_program_kernel_correspondence(result.program, result.bundle) is None
    assert result.execution.submitted_tasks == result.execution.completed_tasks == len(result.execution.tasks)
    assert result.execution.completed_tasks > 0


@pytest.mark.parametrize("boundary", ("planning", "kernel", "program"))
def test_backend_result_rejects_a_connected_chain_detached_from_stage_semantics(direct_compilation, boundary):
    _, _, _, result = direct_compilation
    detached = "f" * 64
    planning = result.planning
    kernel = result.kernel
    scheduled = result.scheduled
    if boundary == "planning":
        planning = replace(planning, passes=planning.passes[:-1] + (replace(planning.passes[-1], output_hash=detached),))
        kernel = replace(kernel, passes=(replace(kernel.passes[0], input_hash=detached),) + kernel.passes[1:])
    elif boundary == "kernel":
        kernel = replace(kernel, passes=kernel.passes[:-1] + (replace(kernel.passes[-1], output_hash=detached),))
        scheduled = replace(scheduled, passes=(replace(scheduled.passes[0], input_hash=detached),) + scheduled.passes[1:])
    else:
        scheduled = replace(scheduled, passes=scheduled.passes[:-1] + (replace(scheduled.passes[-1], output_hash=detached),))
    with pytest.raises(MeshIrError):
        BackendCompilationResult(planning, kernel, scheduled)


def test_compile_backend_rejects_non_integer_worker_count_before_dispatch(direct_compilation):
    arch, graph, effective, _ = direct_compilation
    with pytest.raises(MeshIrError) as caught:
        compile_backend((graph,), arch, effective, source_graphs=(graph,), workers=True)
    assert caught.value.code == "E_CONFIG"
