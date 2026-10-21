import pytest
from collections import Counter
from pathlib import Path

import yaml

from mesh_ir.diagnostics import MeshIrError
from mesh_ir.passes.hazards import insert_hazard_dependencies_stage
from mesh_ir.passes.schedule import schedule_per_core_streams_stage
from mesh_ir.passes.static_sram import plan_static_sram_stage
from tests.unit.test_gate2_scheduled_pipeline import _inputs


def test_command_capacity_rejects_before_descriptor_generation():
    arch, lowering, _ = _inputs()
    static = plan_static_sram_stage(lowering.bundle, arch)
    hazards = insert_hazard_dependencies_stage(static)
    arch.command_rom_entries = 1
    with pytest.raises(MeshIrError, match='command ROM') as error:
        schedule_per_core_streams_stage(hazards, arch)
    assert error.value.context['capacity_entries'] == 1


def test_geometry_reserves_cumulative_command_capacity():
    from mesh_ir.architecture import load_arch
    from mesh_ir.ir.common import DType, TensorRole
    from mesh_ir.ir.graph_ir import ElementwiseAttrs, GraphFunction, GraphOp, OpCode, PytreeSpec
    from mesh_ir.passes.placement_geometry import GeometryCostTemplate, PlacementRejection, PlacementValues, ShardingStrategy, create_placement_ledger, derive_operation_geometry
    from tests.unit.test_gate2_placement_geometry import ARCH_PATH, COLLECTIVES, TILING, _value

    arch = load_arch(ARCH_PATH)
    values = PlacementValues((_value(1, 'input', TensorRole.INPUT, DType.FP32, (8,)),
        _value(2, 'output', TensorRole.OUTPUT, DType.FP32, (8,))))
    op = GraphOp(1, OpCode.RELU, (1,), (2,), ElementwiseAttrs(), 'relu')
    function = GraphFunction(1, 'forward', (1,), (op,), (2,), PytreeSpec.leaf(), PytreeSpec.leaf())
    ledger = create_placement_ledger(function, values.values)
    geometry = derive_operation_geometry(op, values, ShardingStrategy.SINGLE, arch.core_ids[:1], ledger, TILING, COLLECTIVES, arch)
    template = GeometryCostTemplate.from_geometry(geometry, ledger)
    arch.command_rom_entries = 1
    assert isinstance(template.estimate(arch, {}), PlacementRejection)


@pytest.mark.parametrize('capacity', [150, 65536])
def test_backend_moves_work_to_other_cores_before_rom_fills(capacity):
    from mesh_ir.architecture import load_arch
    from mesh_ir.compiler import compile_backend
    from mesh_ir.compile_config import load_compile_config_text, resolve_compile_config
    from mesh_ir.ir.common import DType, TensorRole
    from mesh_ir.ir.graph_ir import GraphFunction, GraphModule, GraphOp, MatmulAttrs, OpCode, PytreeSpec
    from tests.unit.test_gate2_placement_geometry import ARCH_PATH, _value

    arch = load_arch(ARCH_PATH)
    arch.command_rom_entries = capacity
    length = 8
    values = (_value(1, 'input', TensorRole.INPUT, DType.FP32, (1, 16)),)
    values += tuple(_value(2 + index, f'weight{index}', TensorRole.WEIGHT, DType.FP32,
        (16, 16), content=f'{index + 1:064x}') for index in range(length))
    values += tuple(_value(2 + length + index, f'result{index}',
        TensorRole.OUTPUT if index == length - 1 else TensorRole.ACTIVATION,
        DType.FP32, (1, 16)) for index in range(length))
    ops = tuple(GraphOp(index + 1, OpCode.MATMUL,
        (1 if index == 0 else length + index + 1, index + 2),
        (length + index + 2,), MatmulAttrs(), f'matrix{index}') for index in range(length))
    function = GraphFunction(1, 'forward', (1,), ops, (values[-1].value_id,), PytreeSpec.leaf(), PytreeSpec.leaf())
    graph = GraphModule.create(arch.digest().hex(), 'a' * 64, 'forward', 'static', values, (function,))
    path = Path(__file__).parents[1] / 'integration/gather_streaming_compile.yaml'
    document = yaml.safe_load(path.read_text())
    document['parallelism']['tensor_parallel'] = 1
    document['placement']['allowed_cores'] = list(arch.core_ids)
    document['tiling'].update(gemm_m=1, gemm_n=4, gemm_k=4)
    effective = resolve_compile_config(load_compile_config_text(yaml.safe_dump(document), arch), arch)
    result = compile_backend((graph,), arch, effective, source_graphs=(graph,), workers=1)
    counts = Counter(command.core_id for command in result.program.commands)
    assert len(counts) > 1
    assert max(counts.values()) <= arch.command_rom_entries
    assert all(sum(count for _, count in candidate.geometry.command_entries) > 0
        for candidate in result.kernel.decisions.candidates if candidate.geometry is not None)
