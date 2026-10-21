from dataclasses import replace
from pathlib import Path

import pytest
import torch
import yaml

from mesh_ir.architecture import load_arch
from mesh_ir.canonical import semantic_sha256
from mesh_ir.compile_config import load_compile_config_text, resolve_compile_config
from mesh_ir.compiler import compile_backend
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.frontend import FrontendRequest, export_graph
from mesh_ir.ir.graph_ir import OpCode
from mesh_ir.ir.kernel_ir import KernelOpcode
from mesh_ir.publication import ArtifactTransaction, publish_compilation
from tests.integration.support.garnet_harness import output_of, run_garnet


class BroadcastRms(torch.nn.Module):
    def forward(self, source, weight):
        value = source.float()
        inverse = ((value * value).mean(dim=-1, keepdim=True) + 1e-6).rsqrt()
        return (value * inverse).to(source.dtype) * weight


@pytest.mark.parametrize('dtype', [torch.float16, torch.float32])
def test_broadcast_rms_regions_compile_and_execute_native(tmp_path, dtype):
    root = Path(__file__).resolve().parents[4]
    arch_path = root / 'configs/example/ai_mesh/arch/mesh_2x2.yaml'
    arch = load_arch(arch_path)
    config = yaml.safe_load(Path(__file__).with_name('gather_streaming_compile.yaml').read_text())
    config['parallelism']['tensor_parallel'] = 4
    config['placement']['allowed_cores'] = list(arch.core_ids)
    effective = resolve_compile_config(load_compile_config_text(yaml.safe_dump(config), arch), arch)
    frontend = export_graph(BroadcastRms,
        (torch.ones(4, 3, 8, dtype=dtype), torch.ones(8, dtype=dtype)),
        FrontendRequest('forward', 'static', arch.digest().hex(), tmp_path / 'frontend',
            source_project_root=root / 'util/mesh_ir'))
    result = compile_backend(frontend.variants, arch, effective, source_graphs=(frontend.graph,), workers=1)
    kernel = result.bundle.modules[0]
    multiply = next(op for op in kernel.ops if op.opcode is KernelOpcode.VECTOR
        and op.attrs.graph_opcode is OpCode.MUL and any(read.region.shape != op.writes[0].region.shape for read in op.reads))
    read = next(read for read in multiply.reads if read.region.shape == multiply.writes[0].region.shape)
    shape = read.region.shape[:1] + (1,) + read.region.shape[2:]
    invalid_op = replace(multiply, reads=tuple(replace(item, region=replace(item.region, shape=shape)) if item is read else item for item in multiply.reads))
    invalid = replace(kernel, ops=tuple(invalid_op if op.op_id == multiply.op_id else op for op in kernel.ops))
    invalid = replace(invalid, semantic_sha256=semantic_sha256(invalid.semantic_dict()))
    with pytest.raises(MeshIrError, match='elementwise operand regions do not match output shape'):
        invalid.verify()
    output = tmp_path / 'program'
    with ArtifactTransaction(output, ()) as transaction:
        publish_compilation(transaction, (frontend,), result, arch, effective)
    run = run_garnet(tmp_path, 'runtime', 'ring_workload', output, arch=arch_path)
    assert run.returncode == 0, output_of(run)[-12000:]
