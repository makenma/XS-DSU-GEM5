from pathlib import Path
from collections import Counter

import pytest
import torch
import yaml

from mesh_ir.analysis.sram import plan_static_sram
from mesh_ir.architecture import load_arch
from mesh_ir.compile_config import load_compile_config_text, resolve_compile_config
from mesh_ir.compiler import compile_backend
from mesh_ir.frontend import FrontendRequest, export_graph
from mesh_ir.publication import ArtifactTransaction, publish_compilation
from tests.integration.support.garnet_harness import output_of, run_garnet


class MatrixWeightChain(torch.nn.Module):
    def __init__(self, length=3):
        super().__init__()
        self.length = length
        for identity in range(length):
            self.register_buffer(f'weight{identity}', torch.eye(64, dtype=torch.float16))

    def forward(self, source):
        for identity in range(self.length):
            source = source @ getattr(self, f'weight{identity}')
        return source


@pytest.mark.parametrize('length,rom_entries', [(3, 65536), (12, 64)])
def test_streamed_weight_reuse_executes_native_with_32_compiler_workers(tmp_path, length, rom_entries):
    root = Path(__file__).resolve().parents[4]
    document = yaml.safe_load((root / 'configs/example/ai_mesh/arch/mesh_2x2.yaml').read_text())
    document['core']['sram']['bytes'] = 16384
    document['core']['command_rom_entries'] = rom_entries
    arch_path = tmp_path / 'arch.yaml'
    arch_path.write_text(yaml.safe_dump(document))
    arch = load_arch(arch_path)
    config = yaml.safe_load(Path(__file__).with_name('gather_streaming_compile.yaml').read_text())
    config['placement']['allowed_cores'] = [arch.core_ids[0]] if length == 3 else list(arch.core_ids)
    config['tiling'].update(gemm_m=16, gemm_n=32, gemm_k=32)
    effective = resolve_compile_config(load_compile_config_text(yaml.safe_dump(config), arch), arch)
    frontend = export_graph(lambda: MatrixWeightChain(length), (torch.ones(16, 64, dtype=torch.float16),),
        FrontendRequest('forward', 'static', arch.digest().hex(), tmp_path / 'frontend',
            source_project_root=root / 'util/mesh_ir'))
    result = compile_backend(frontend.variants, arch, effective, source_graphs=(frontend.graph,), workers=32)
    assert result.execution.workers == 32
    counts = Counter(command.core_id for command in result.program.commands)
    assert max(counts.values()) <= rom_entries
    if length > 3:
        assert len(counts) > 1
    assert max(report.peak_bytes for report in plan_static_sram(result.bundle.modules[0], arch).reports) <= arch.sram_bytes
    output = tmp_path / 'program'
    with ArtifactTransaction(output, ()) as transaction:
        publish_compilation(transaction, (frontend,), result, arch, effective)
    run = run_garnet(tmp_path, 'runtime', 'ring_workload', output, arch=arch_path)
    assert run.returncode == 0, output_of(run)[-12000:]
