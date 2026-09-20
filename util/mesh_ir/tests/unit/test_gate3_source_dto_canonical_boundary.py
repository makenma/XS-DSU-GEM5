from dataclasses import replace

from mesh_ir.canonical import semantic_sha256, to_canonical
from mesh_ir.compat import DtoInput, DtoNode, DtoOutput, ExportDto, NoneArg, ValueRef
from mesh_ir.ir.common import Const, DType, FixedStride, TensorRole
from mesh_ir.ir.graph_ir import GraphFunction, GraphModule, GraphValue, PytreeSpec


def _dto(inputs: tuple[DtoInput, ...] | None = None, location: str | None = None) -> ExportDto:
    return ExportDto(
        "2.8.0+cpu",
        "ATEN",
        "ATEN",
        (("aten", 10),),
        (8, 8),
        "d" * 64,
        inputs
        if inputs is not None
        else (
            DtoInput(0, 1, "USER_INPUT", "input", None, None),
            DtoInput(1, 2, "PARAMETER", "weight", "module.weight", None),
            DtoInput(2, 3, "BUFFER", "running", "module.running", True),
            DtoInput(3, 4, "BUFFER", "scratch", "module.scratch", False),
        ),
        (DtoOutput(0, 5, "USER_OUTPUT"),),
        (),
        (DtoNode("node:1", "aten.add.Tensor", "aten::add.Tensor", (ValueRef(1), NoneArg()), (), (5,), location),),
        (),
        (),
        PytreeSpec.leaf(),
        PytreeSpec.leaf(),
    )


def test_source_dto_semantic_mapping_keeps_complete_input_signature_fields():
    dto = _dto()
    expected = {
        "torch_version": "2.8.0+cpu",
        "dialect_before": "ATEN",
        "dialect_after": "ATEN",
        "source_opset": [{"namespace": "aten", "version": 10}],
        "source_schema": {"major": 8, "minor": 8},
        "decomposition_digest": "d" * 64,
        "inputs": [
            {"position": 0, "value_id": 1, "kind": "USER_INPUT", "argument_name": "input", "target": None, "persistent": None},
            {"position": 1, "value_id": 2, "kind": "PARAMETER", "argument_name": "weight", "target": "module.weight", "persistent": None},
            {"position": 2, "value_id": 3, "kind": "BUFFER", "argument_name": "running", "target": "module.running", "persistent": True},
            {"position": 3, "value_id": 4, "kind": "BUFFER", "argument_name": "scratch", "target": "module.scratch", "persistent": False},
        ],
        "outputs": [{"position": 0, "value_id": 5, "kind": "USER_OUTPUT"}],
        "values": [],
        "nodes": [
            {
                "source_node_id": "node:1",
                "target": "aten.add.Tensor",
                "schema": "aten::add.Tensor",
                "args": [{"kind": "value", "value_id": 1}, {"kind": "none"}],
                "kwargs": [],
                "result_ids": [5],
            }
        ],
        "symbols": [],
        "root_symbol_bindings": [],
        "input_pytree": {"kind": "leaf", "children": [], "keys": []},
        "output_pytree": {"kind": "leaf", "children": [], "keys": []},
    }

    assert to_canonical(dto.semantic_data()) == expected
    assert dto.semantic_hash() == semantic_sha256(expected)


def test_source_dto_target_and_persistence_each_change_hash_but_debug_location_does_not():
    dto = _dto()
    changed_target = _dto((dto.inputs[0], replace(dto.inputs[1], target="module.alt_weight"), *dto.inputs[2:]))
    changed_persistence = _dto((*dto.inputs[:2], replace(dto.inputs[2], persistent=False), dto.inputs[3]))
    changed_debug = _dto(location="example.py:7")

    assert changed_target.semantic_hash() != dto.semantic_hash()
    assert changed_persistence.semantic_hash() != dto.semantic_hash()
    assert changed_debug.semantic_hash() == dto.semantic_hash()


def test_common_canonical_and_public_graph_optional_contracts_remain_omitting():
    input_record = DtoInput(0, 1, "USER_INPUT", "input", None, None)
    assert to_canonical(input_record) == {"position": 0, "value_id": 1, "kind": "USER_INPUT", "argument_name": "input"}

    value = GraphValue(1, "input", TensorRole.INPUT, DType.FP32, (Const(1),), (FixedStride(1),), 0, 1)
    function = GraphFunction(1, "entry", (1,), (), (1,), PytreeSpec.leaf(), PytreeSpec.leaf())
    graph = GraphModule.create("a" * 64, "b" * 64, "entry", "profile", (value,), (function,))

    assert "content_sha256" not in graph.canonical_dict()["values"][0]
