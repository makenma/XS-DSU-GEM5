from pathlib import Path

import pytest
import torch
import yaml

from mesh_ir.architecture import load_arch
from mesh_ir.compile_config import load_compile_config_text, resolve_compile_config
from mesh_ir.compiler import compile_backend
from mesh_ir.frontend import FrontendRequest, export_graph
from mesh_ir.ir.kernel_ir import KernelOpcode
from mesh_ir.publication import ArtifactTransaction, publish_compilation
from tests.integration.support.garnet_harness import output_of, run_garnet


class RetainedRotaryMultiply(torch.nn.Module):
    def forward(self, query, scale):
        return query.relu().transpose(1, 2) * scale


@pytest.mark.parametrize('dtype', [torch.float16, torch.float32])
def test_retained_transpose_materializes_elementwise_layout(tmp_path, dtype):
    root = Path(__file__).resolve().parents[4]
    arch_path = root / 'configs/example/ai_mesh/arch/mesh_2x2.yaml'
    arch = load_arch(arch_path)
    config = yaml.safe_load(Path(__file__).with_name('gather_streaming_compile.yaml').read_text())
    config['parallelism']['tensor_parallel'] = 1
    config['placement']['allowed_cores'] = [arch.core_ids[0]]
    effective = resolve_compile_config(load_compile_config_text(yaml.safe_dump(config), arch), arch)
    inputs = (torch.ones(1, 3, 4, 8, dtype=dtype), torch.ones(1, 1, 3, 8, dtype=dtype))
    frontend = export_graph(RetainedRotaryMultiply, inputs,
        FrontendRequest('forward', 'static', arch.digest().hex(), tmp_path / 'frontend',
            source_project_root=root / 'util/mesh_ir'))
    result = compile_backend(frontend.variants, arch, effective, source_graphs=(frontend.graph,), workers=1)
    kernel = result.bundle.modules[0]
    assert any(op.opcode is KernelOpcode.LOCAL_COPY for op in kernel.ops)
    output = tmp_path / 'program'
    with ArtifactTransaction(output, ()) as transaction:
        publish_compilation(transaction, (frontend,), result, arch, effective)
    run = run_garnet(tmp_path, 'runtime', 'ring_workload', output, arch=arch_path)
    assert run.returncode == 0, output_of(run)[-12000:]
