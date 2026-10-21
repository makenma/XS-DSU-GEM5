from pathlib import Path
from dataclasses import replace

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
from mesh_ir.passes.graph_to_kernel import verify_kernel_correspondence
from mesh_ir.publication import ArtifactTransaction, publish_compilation
from tests.integration.support.garnet_harness import output_of, run_garnet


class RetainedBroadcast(torch.nn.Module):
    def forward(self, source):
        heads = source.transpose(1, 2)
        produced = heads + heads
        expanded = produced.unsqueeze(2).expand(-1, -1, 4, -1, -1)
        return expanded.contiguous().flatten(1, 2).relu()


@pytest.mark.parametrize('parallel', [1, 4])
@pytest.mark.parametrize('tokens,width,dtype', [(4, 4, torch.float32), (16, 128, torch.float16)])
def test_retained_gqa_broadcast_compiles(tmp_path, parallel, tokens, width, dtype):
    root = Path(__file__).resolve().parents[4]
    arch = load_arch(root / 'configs/example/ai_mesh/arch/mesh_2x2.yaml')
    config = yaml.safe_load(Path(__file__).with_name('gather_streaming_compile.yaml').read_text())
    config['parallelism']['tensor_parallel'] = parallel
    config['placement']['allowed_cores'] = list(arch.core_ids)
    effective = resolve_compile_config(load_compile_config_text(yaml.safe_dump(config), arch), arch)
    frontend = export_graph(RetainedBroadcast, (torch.ones(1, tokens, 8, width, dtype=dtype),),
        FrontendRequest('forward', 'static', arch.digest().hex(), tmp_path / 'frontend',
            source_project_root=root / 'util/mesh_ir'))
    expanded = next(op for op in frontend.graph.functions[0].ops if op.opcode is OpCode.EXPAND_VIEW)
    value = next(value for value in frontend.graph.values if value.value_id == expanded.results[0])
    assert value.strides[2].evaluate({}) == 0
    result = compile_backend(frontend.variants, arch, effective, source_graphs=(frontend.graph,), workers=1)
    kernel = result.bundle.modules[0]
    copy = next(op for op in kernel.ops if op.opcode is KernelOpcode.DATA_MOVEMENT and op.reads)
    source = kernel.views[copy.reads[0].view_id - 1]
    assert source.object_strides[2] == 0
    assert source.layout is None
    corrupted = replace(source, object_strides=source.object_strides[:1] + (source.object_strides[1] - 1,) + source.object_strides[2:])
    invalid = replace(kernel, views=tuple(corrupted if view.view_id == source.view_id else view for view in kernel.views))
    invalid = replace(invalid, semantic_sha256=semantic_sha256(invalid.semantic_dict()))
    with pytest.raises(MeshIrError) as failure:
        verify_kernel_correspondence(result.variants[0], invalid)
    assert failure.value.code != 'E_ABI_CHECKSUM'
    destination = kernel.views[copy.writes[0].view_id - 1]
    invalid = replace(kernel, views=tuple(replace(view, layout=None) if view.view_id == destination.view_id else view for view in kernel.views))
    invalid = replace(invalid, semantic_sha256=semantic_sha256(invalid.semantic_dict()))
    with pytest.raises(MeshIrError, match='without a consumer layout'):
        invalid.verify()
    output = tmp_path / 'program'
    with ArtifactTransaction(output, ()) as transaction:
        publish_compilation(transaction, (frontend,), result, arch, effective)
    run = run_garnet(tmp_path, 'runtime', 'ring_workload', output,
        arch=root / 'configs/example/ai_mesh/arch/mesh_2x2.yaml')
    assert run.returncode == 0, output_of(run)[-12000:]
