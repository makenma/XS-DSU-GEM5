from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from examples.tiny_mlp import create_model as create_mlp, example_args as mlp_args
from examples.tiny_transformer import create_model as create_transformer, dynamic_shapes as transformer_dynamic_shapes, example_args as transformer_args
from mesh_ir.architecture import ArchManifest, load_arch
from mesh_ir.compile_config import EffectiveCompileConfig, load_compile_config_text, resolve_compile_config
from mesh_ir.frontend import FrontendRequest, FrontendResult, export_graph
from mesh_ir.ir.graph_ir import GraphModule


ROOT = Path(__file__).resolve().parents[4]
ARCH_PATH = ROOT / "configs/example/ai_mesh/arch/mesh_2x2.yaml"


@dataclass(frozen=True)
class RealBackendInputs:
    frontends: tuple[FrontendResult, ...]
    variants: tuple[GraphModule, ...]
    source_graphs: tuple[GraphModule, ...]
    arch: ArchManifest


def compile_config_text(tensor_parallel: int, all_reduce_algorithm: str = "tree") -> str:
    return f"""
schema_version: mesh-compile-v1
entrypoints: [mlp, transformer]
shape_profiles:
  mlp:
    - {{profile_id: mlp_static}}
  transformer:
    - {{profile_id: b2s4, batch: 2, sequence: 4}}
    - {{profile_id: b4s8, batch: 4, sequence: 8}}
symbol_bindings:
  transformer:
    batch: {{input: value, axis: 0}}
    sequence: {{input: value, axis: 1}}
parallelism: {{tensor_parallel: {tensor_parallel}, pipeline_parallel: 1, expert_parallel: 1, data_parallel: 1}}
placement: {{core_order: row_major_yx, allowed_cores: [7, 2, 9, 13], reserve_cores: []}}
tiling: {{gemm_m: 3, gemm_n: 5, gemm_k: 7, double_buffer: true}}
collectives: {{all_reduce_algorithm: {all_reduce_algorithm}, chunk_bytes: 28}}
runtime_model: {{mode: FULL_TIMING, tensor_data: DIGEST_ONLY}}
"""


def create_real_backend_inputs(output: Path) -> RealBackendInputs:
    arch = load_arch(ARCH_PATH)
    digest = arch.digest().hex()
    source_root = ROOT / "util/mesh_ir"
    mlp = export_graph(
        create_mlp,
        mlp_args(),
        FrontendRequest("mlp", "mlp_static", digest, output / "mlp", source_project_root=source_root),
    )
    transformer = export_graph(
        create_transformer,
        transformer_args(),
        FrontendRequest(
            "transformer",
            "symbolic",
            digest,
            output / "transformer",
            symbol_bindings=(("batch", "value", 0), ("sequence", "value", 1)),
            shape_profiles=(("b2s4", (("batch", 2), ("sequence", 4))), ("b4s8", (("batch", 4), ("sequence", 8)))),
            source_project_root=source_root,
        ),
        dynamic_shapes=transformer_dynamic_shapes(),
    )
    return RealBackendInputs((mlp, transformer), mlp.variants + transformer.variants, (mlp.graph, transformer.graph), arch)


@pytest.fixture(scope="module")
def real_backend_inputs(tmp_path_factory) -> RealBackendInputs:
    return create_real_backend_inputs(tmp_path_factory.mktemp("backend-frontend"))


def effective_config(inputs: RealBackendInputs, tensor_parallel: int, all_reduce_algorithm: str = "tree") -> EffectiveCompileConfig:
    if type(inputs) is not RealBackendInputs:
        raise TypeError("real backend inputs have an invalid type")
    return resolve_compile_config(load_compile_config_text(compile_config_text(tensor_parallel, all_reduce_algorithm), inputs.arch), inputs.arch)


__all__ = ["ARCH_PATH", "ROOT", "RealBackendInputs", "compile_config_text", "create_real_backend_inputs", "effective_config", "real_backend_inputs"]
