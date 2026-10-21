from pathlib import Path
from dataclasses import replace
import copy
import json

import pytest
import torch
import yaml

from mesh_ir.abi import decode_program, encode_program
from mesh_ir.architecture import load_arch
from mesh_ir.compile_config import load_compile_config_text, resolve_compile_config
from mesh_ir.compiler import compile_backend
from mesh_ir.frontend import FrontendRequest, export_graph
from mesh_ir.ir.graph_ir import OpCode
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.passes.sharding import shard_and_pad
from mesh_ir.scheduled.verify import verify_program
from mesh_ir.publication import ArtifactTransaction, publish_compilation
from tests.integration.support.garnet_harness import output_of, run_garnet


class RotateHalf(torch.nn.Module):
    def forward(self, x, y):
        value = (x + y).transpose(0, 1)
        return torch.cat((-value[:, 64:], value[:, :64]), dim=-1)


class QwenUnary(torch.nn.Module):
    def forward(self, x):
        return torch.cos(torch.sin(-x.float())).half()


@pytest.mark.parametrize('dtype', [torch.float16, torch.int32])
def test_qwen_cast_negate_and_trigonometry_reach_native_execution(tmp_path, dtype):
    root = Path(__file__).resolve().parents[4]
    arch_path = root / 'configs/example/ai_mesh/arch/mesh_1x2.yaml'
    arch = load_arch(arch_path)
    config = Path(__file__).with_name('gather_streaming_compile.yaml').read_text()
    effective = resolve_compile_config(load_compile_config_text(config, arch), arch)
    frontend = export_graph(QwenUnary, (torch.arange(16, dtype=dtype),),
        FrontendRequest('forward', 'static', arch.digest().hex(), tmp_path / 'frontend',
            source_project_root=root / 'util/mesh_ir'))
    assert {OpCode.CAST, OpCode.NEGATE, OpCode.SIN, OpCode.COS} <= {
        op.opcode for op in frontend.graph.functions[0].ops}
    result = compile_backend(frontend.variants, arch, effective,
        source_graphs=(frontend.graph,), workers=1)
    output = tmp_path / 'program'
    with ArtifactTransaction(output, ()) as transaction:
        publish_compilation(transaction, (frontend,), result, arch, effective)
    run = run_garnet(tmp_path, 'runtime', 'ring_workload', output, arch=arch_path)
    assert run.returncode == 0, output_of(run)[-12000:]
    from mesh_ir.runtime_reconciliation import ReconciliationError
    from mesh_ir.workload_statistics import summarize_workload
    statistics = json.loads((output / 'workload_statistics.json').read_text())
    idle = next(row for row in statistics['instances'][0]['cores'] if row['core_id'] == 1)
    assert idle['begin_tick'] is idle['end_tick'] is None
    assert idle['elapsed_ticks'] == idle['issued'] == idle['terminal'] == 0
    observed = json.loads((output / 'actual_result.json').read_text())
    sections = json.loads((output / 'schedule.mesh.json').read_text())['sections']
    missing = copy.deepcopy(observed)
    missing['instances'][0]['cores']['0']['observations']['commands'] = []
    with pytest.raises(ReconciliationError, match='command lifecycle facts'):
        summarize_workload(sections, missing)
    false_idle = copy.deepcopy(observed)
    false_idle['instances'][0]['cores']['1']['observations']['commands'] = [
        observed['instances'][0]['cores']['0']['observations']['commands'][0]]
    with pytest.raises(ReconciliationError, match='unassigned core'):
        summarize_workload(sections, false_idle)


@pytest.mark.parametrize('dtype', [torch.float16, torch.float32])
def test_partitioned_rotate_half_compiles_complete_backend(tmp_path, dtype):
    root = Path(__file__).resolve().parents[4]
    arch = load_arch(root / 'configs/example/ai_mesh/arch/mesh_2x2.yaml')
    config = yaml.safe_load(Path(__file__).with_name('gather_streaming_compile.yaml').read_text())
    config['parallelism']['tensor_parallel'] = 4
    config['placement']['allowed_cores'] = list(arch.core_ids)
    effective = resolve_compile_config(load_compile_config_text(yaml.safe_dump(config), arch), arch)
    inputs = (torch.ones(128, 16, dtype=dtype), torch.ones(128, 16, dtype=dtype))
    frontend = export_graph(RotateHalf, inputs,
        FrontendRequest('forward', 'static', arch.digest().hex(), tmp_path / 'frontend',
            source_project_root=root / 'util/mesh_ir'))
    result = compile_backend(frontend.variants, arch, effective,
        source_graphs=(frontend.graph,), workers=1)
    graph = result.variants[0]
    sliced = {op.op_id for op in graph.functions[0].ops
        if op.opcode is OpCode.SLICE_VIEW and op.attrs.axes == (1,)}
    assert len(sliced) == 2
    checked = 0
    for index, candidate in enumerate(result.kernel.decisions.candidates):
        if candidate.geometry is not None and candidate.identity.op_id in sliced:
            geometry = candidate.geometry
            assert geometry.compute_tiles == geometry.value_transfers == ()
            assert sorted(shard.valid_shape[1] for shard in geometry.result_distribution.shards) == [0, 0, 32, 32]
            shifted = replace(geometry.result_distribution, shards=tuple(replace(shard,
                global_origin=(0, rank * 16), valid_shape=(16, 16), padded_shape=(16, 16))
                for rank, shard in enumerate(geometry.result_distribution.shards)))
            changed = replace(candidate, geometry=replace(geometry, result_distribution=shifted))
            decisions = result.kernel.decisions
            altered = replace(decisions, candidates=decisions.candidates[:index] + (changed,) + decisions.candidates[index + 1:])
            with pytest.raises(MeshIrError, match='view distribution does not preserve source ownership'):
                shard_and_pad(result.planning, altered)
            checked += 1
    assert checked == 2
    decoded = decode_program(encode_program(result.program))
    verify_program(decoded, arch)
    assert decoded.semantic_sha256 == result.program.semantic_sha256
    output = tmp_path / 'program'
    with ArtifactTransaction(output, ()) as transaction:
        publish_compilation(transaction, (frontend,), result, arch, effective)
    run = run_garnet(tmp_path, 'runtime', 'ring_workload', output,
        arch=root / 'configs/example/ai_mesh/arch/mesh_2x2.yaml')
    assert run.returncode == 0, output_of(run)[-12000:]
