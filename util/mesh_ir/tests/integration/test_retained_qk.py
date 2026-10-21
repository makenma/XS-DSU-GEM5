from pathlib import Path

import pytest
import torch
import yaml

from mesh_ir.architecture import load_arch
from mesh_ir.compile_config import load_compile_config_text, resolve_compile_config
from mesh_ir.compiler import compile_backend
from mesh_ir.frontend import FrontendRequest, export_graph
from mesh_ir.ir.kernel_ir import KernelOpcode
from mesh_ir.passes.placement import ShardingStrategy
from mesh_ir.publication import ArtifactTransaction, publish_compilation
from tests.integration.support.garnet_harness import output_of, run_garnet


class RetainedQueryKey(torch.nn.Module):
    def forward(self, query, key):
        return query.relu() @ key.relu()


@pytest.mark.parametrize('dtype', [torch.float16, torch.float32])
@pytest.mark.parametrize('workers', [1, 32])
def test_retained_query_key_k_partition_executes_native(tmp_path, dtype, workers):
    root = Path(__file__).resolve().parents[4]
    arch_path = root / 'configs/example/ai_mesh/arch/mesh_2x2.yaml'
    arch = load_arch(arch_path)
    config = yaml.safe_load(Path(__file__).with_name('gather_streaming_compile.yaml').read_text())
    config['parallelism']['tensor_parallel'] = 4
    config['placement']['allowed_cores'] = list(arch.core_ids)
    config['tiling'].update(gemm_m=16, gemm_n=16, gemm_k=16)
    effective = resolve_compile_config(load_compile_config_text(yaml.safe_dump(config), arch), arch)
    inputs = (torch.ones(1, 4, 16, 128, dtype=dtype), torch.ones(1, 4, 128, 16, dtype=dtype))
    frontend = export_graph(RetainedQueryKey, inputs,
        FrontendRequest('forward', 'static', arch.digest().hex(), tmp_path / 'frontend',
            source_project_root=root / 'util/mesh_ir'))
    result = compile_backend(frontend.variants, arch, effective, source_graphs=(frontend.graph,), workers=workers)
    assert any(choice.strategy is ShardingStrategy.MATRIX_K_SUM for choice in result.kernel.decisions.placements)
    kernel = result.bundle.modules[0]
    assert any(op.opcode is KernelOpcode.LOCAL_COPY for op in kernel.ops)
    output = tmp_path / 'program'
    with ArtifactTransaction(output, ()) as transaction:
        publish_compilation(transaction, (frontend,), result, arch, effective)
    run = run_garnet(tmp_path, 'runtime', 'ring_workload', output, arch=arch_path)
    assert run.returncode == 0, output_of(run)[-12000:]
