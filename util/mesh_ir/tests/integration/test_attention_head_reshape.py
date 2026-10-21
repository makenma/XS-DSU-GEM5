from pathlib import Path
import json

import pytest
import torch
import yaml

from mesh_ir.abi import decode_program, encode_program
from mesh_ir.architecture import load_arch
from mesh_ir.compile_config import load_compile_config_text, resolve_compile_config
from mesh_ir.compiler import compile_backend
from mesh_ir.frontend import FrontendRequest, export_graph
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.graph_ir import OpCode
from mesh_ir.scheduled.verify import verify_program
from mesh_ir.publication import ArtifactTransaction, publish_compilation
from tests.integration.support.garnet_harness import output_of, run_garnet


class AttentionHeads(torch.nn.Module):
    def forward(self, probabilities, values):
        result = (probabilities @ values).transpose(1, 2).contiguous()
        return result.view(1, 16, -1).relu()


@pytest.mark.parametrize('dtype,heads,width', [(torch.float16, 32, 128), (torch.float32, 4, 16)])
def test_attention_head_reshape_has_legal_producer_ownership(tmp_path, dtype, heads, width):
    root = Path(__file__).resolve().parents[4]
    arch_path = root / 'configs/example/ai_mesh/arch/mesh_2x2.yaml'
    arch = load_arch(arch_path)
    config = yaml.safe_load(Path(__file__).with_name('gather_streaming_compile.yaml').read_text())
    config['parallelism']['tensor_parallel'] = 4
    config['placement']['allowed_cores'] = list(arch.core_ids)
    effective = resolve_compile_config(load_compile_config_text(yaml.safe_dump(config), arch), arch)
    inputs = (torch.ones(1, heads, 16, 16, dtype=dtype), torch.ones(1, heads, 16, width, dtype=dtype))
    frontend = export_graph(AttentionHeads, inputs,
        FrontendRequest('forward', 'static', arch.digest().hex(), tmp_path / 'frontend',
            source_project_root=root / 'util/mesh_ir'))
    try:
        result = compile_backend(frontend.variants, arch, effective,
            source_graphs=(frontend.graph,), workers=1)
    except MeshIrError as error:
        pytest.fail(f'{error}: {error.context}')
    view_ids = {op.op_id for op in frontend.graph.functions[0].ops if op.opcode is OpCode.RESHAPE_VIEW}
    assert view_ids
    for choice in result.kernel.decisions.placements:
        candidate = result.kernel.decisions.candidates[choice.candidate_index]
        if choice.identity.op_id in view_ids:
            assert candidate.geometry.compute_tiles == candidate.geometry.value_transfers == ()
    decoded = decode_program(encode_program(result.program))
    verify_program(decoded, arch)
    output = tmp_path / 'program'
    with ArtifactTransaction(output, ()) as transaction:
        publish_compilation(transaction, (frontend,), result, arch, effective)
    run = run_garnet(tmp_path, 'runtime', 'ring_workload', output, arch=arch_path)
    assert run.returncode == 0, output_of(run)[-12000:]
    actual = json.loads((output / 'actual_result.json').read_text())
    stats = {row.split()[0]: row.split()[1]
        for row in (tmp_path / 'runtime/stats.txt').read_text().splitlines()
        if row.startswith('system.mesh_core_')}
    for core in actual['cores']:
        for field in ('commands_issued', 'commands_completed', 'commands_errored',
                      'commands_cancelled', 'events_published', 'gemm_cycles',
                      'reduce_cycles', 'sram_bank_conflicts', 'sram_service_cycles'):
            assert type(core[field]) is int
            assert core[field] == int(stats[f'system.mesh_core_{core["core_id"]}.{field}'])
