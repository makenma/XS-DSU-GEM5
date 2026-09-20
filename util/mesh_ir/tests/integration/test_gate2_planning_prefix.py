import os
import pickle
import subprocess
import sys
from pathlib import Path

from examples.tiny_mlp import create_model as create_mlp, example_args as mlp_args
from examples.tiny_transformer import create_model as create_transformer, dynamic_shapes as transformer_dynamic_shapes, example_args as transformer_args

from mesh_ir.architecture import load_arch
from mesh_ir.compile_config import load_compile_config_text, resolve_compile_config
from mesh_ir.frontend import FrontendRequest, export_graph
from mesh_ir.passes.planning_prefix import graph_set_sha256, plan_graphs


ROOT = Path(__file__).resolve().parents[4]
ARCH_PATH = ROOT / "configs/example/ai_mesh/arch/mesh_2x2.yaml"
COMPILE = """
schema_version: mesh-compile-v1
entrypoints: [mlp, transformer]
shape_profiles:
  mlp:
    - {profile_id: mlp_static}
  transformer:
    - {profile_id: b2s4, batch: 2, sequence: 4}
    - {profile_id: b4s8, batch: 4, sequence: 8}
symbol_bindings:
  transformer:
    batch: {input: value, axis: 0}
    sequence: {input: value, axis: 1}
parallelism: {tensor_parallel: 2, pipeline_parallel: 1, expert_parallel: 1, data_parallel: 1}
placement: {core_order: row_major_yx, allowed_cores: [7, 9, 13], reserve_cores: [2]}
tiling: {gemm_m: 8, gemm_n: 8, gemm_k: 8, double_buffer: true}
collectives: {all_reduce_algorithm: tree, chunk_bytes: 256}
runtime_model: {mode: FULL_TIMING, tensor_data: DIGEST_ONLY}
"""


def test_real_mlp_and_complete_transformer_flow_through_three_planning_passes(tmp_path):
    arch = load_arch(ARCH_PATH)
    digest = arch.digest().hex()
    source_root = ROOT / "util/mesh_ir"
    mlp = export_graph(
        create_mlp,
        mlp_args(),
        FrontendRequest("mlp", "mlp_static", digest, tmp_path / "mlp", source_project_root=source_root),
    )
    transformer = export_graph(
        create_transformer,
        transformer_args(),
        FrontendRequest(
            "transformer",
            "symbolic",
            digest,
            tmp_path / "transformer",
            symbol_bindings=(("batch", "value", 0), ("sequence", "value", 1)),
            shape_profiles=(("b2s4", (("batch", 2), ("sequence", 4))), ("b4s8", (("batch", 4), ("sequence", 8)))),
            source_project_root=source_root,
        ),
        dynamic_shapes=transformer_dynamic_shapes(),
    )
    variants = mlp.variants + transformer.variants
    config = load_compile_config_text(COMPILE, arch)
    result = plan_graphs(variants, arch, resolve_compile_config(config, arch), source_graphs=(mlp.graph, transformer.graph), workers=2)
    assert tuple((item.entrypoint, item.profile_id) for item in result.state.variants) == (
        ("mlp", "mlp_static"),
        ("transformer", "b2s4"),
        ("transformer", "b4s8"),
    )
    assert tuple(item.semantic_sha256 for item in result.state.variants) == tuple(item.semantic_sha256 for item in variants)
    assert result.passes[0].input_hash == result.passes[0].output_hash == graph_set_sha256(variants)
    assert len(result.state.operation_costs) == sum(len(function.ops) for graph in variants for function in graph.functions)
    assert {item.identity.entrypoint for item in result.state.operation_costs} == {"mlp", "transformer"}
    assert tuple(group.admissible_core_ids for group in result.state.parallel_groups) == ((7, 9, 13),) * 3
    assert all(tuple(item.logical_rank for item in group.participants) == (0, 1) for group in result.state.parallel_groups)
    assert all(group.all_reduce_algorithm == "tree" and group.chunk_bytes == 256 for group in result.state.parallel_groups)
    assert result.execution.submitted_tasks == result.execution.completed_tasks == len(result.state.operation_costs) + 3


def test_real_static_frontend_explicit_same_id_profile_is_verified_as_specialization(tmp_path):
    arch = load_arch(ARCH_PATH)
    digest = arch.digest().hex()
    frontend = export_graph(
        create_mlp,
        mlp_args(),
        FrontendRequest(
            "mlp",
            "mlp_static",
            digest,
            tmp_path / "explicit-static",
            shape_profiles=(("mlp_static", ()),),
            source_project_root=ROOT / "util/mesh_ir",
        ),
    )
    config = load_compile_config_text(
        COMPILE.replace("entrypoints: [mlp, transformer]", "entrypoints: [mlp]")
        .replace("  transformer:\n    - {profile_id: b2s4, batch: 2, sequence: 4}\n    - {profile_id: b4s8, batch: 4, sequence: 8}\n", "")
        .replace("symbol_bindings:\n  transformer:\n    batch: {input: value, axis: 0}\n    sequence: {input: value, axis: 1}\n", "symbol_bindings: {}\n"),
        arch,
    )
    assert frontend.variants[0].semantic_sha256 != frontend.graph.semantic_sha256
    result = plan_graphs(frontend.variants, arch, resolve_compile_config(config, arch), source_graphs=(frontend.graph,))
    assert result.state.variants == frontend.variants


def test_planning_backend_imports_without_torch():
    code = (
        "import sys; "
        "import mesh_ir.analysis.cost, mesh_ir.compile_config, mesh_ir.passes.execution, "
        "mesh_ir.passes.shape_specialize, mesh_ir.passes.planning_prefix; "
        "assert 'torch' not in sys.modules"
    )
    env = dict(os.environ, PYTHONPATH=str(ROOT / "util/mesh_ir"))
    subprocess.run([sys.executable, "-c", code], check=True, env=env)


def test_planning_semantics_are_identical_across_hashseeds_and_worker_counts(tmp_path):
    arch = load_arch(ARCH_PATH)
    digest = arch.digest().hex()
    source_root = ROOT / "util/mesh_ir"
    mlp = export_graph(
        create_mlp,
        mlp_args(),
        FrontendRequest("mlp", "mlp_static", digest, tmp_path / "seed-mlp", source_project_root=source_root),
    )
    transformer = export_graph(
        create_transformer,
        transformer_args(),
        FrontendRequest(
            "transformer",
            "symbolic",
            digest,
            tmp_path / "seed-transformer",
            symbol_bindings=(("batch", "value", 0), ("sequence", "value", 1)),
            shape_profiles=(("b2s4", (("batch", 2), ("sequence", 4))), ("b4s8", (("batch", 4), ("sequence", 8)))),
            source_project_root=source_root,
        ),
        dynamic_shapes=transformer_dynamic_shapes(),
    )
    inputs = tmp_path / "planning-inputs.pickle"
    inputs.write_bytes(pickle.dumps((mlp.variants + transformer.variants, (mlp.graph, transformer.graph), arch, resolve_compile_config(load_compile_config_text(COMPILE, arch), arch))))
    driver = tmp_path / "planning_hashseed.py"
    driver.write_text(
        "import json, os, pickle, sys\n"
        "from mesh_ir.passes.planning_prefix import plan_graphs\n"
        "if __name__ == '__main__':\n"
        " variants,sources,arch,effective=pickle.loads(open(sys.argv[1],'rb').read())\n"
        " result=plan_graphs(variants,arch,effective,source_graphs=sources,workers=int(os.environ['PLANNING_WORKERS']))\n"
        " print(json.dumps({'semantic':result.state.semantic_bytes().hex(),'passes':[(p.name,p.version,p.input_hash,p.output_hash,p.statistics) for p in result.passes]},sort_keys=True,separators=(',',':')))\n",
        encoding="utf-8",
    )
    outputs = []
    for seed in ("1", "77", "31337"):
        for workers in ("1", "2", "8"):
            env = dict(os.environ, PYTHONPATH=str(ROOT / "util/mesh_ir"), PYTHONHASHSEED=seed, PLANNING_WORKERS=workers)
            outputs.append(subprocess.check_output([sys.executable, str(driver), str(inputs)], env=env, text=True))
    assert len(set(outputs)) == 1
