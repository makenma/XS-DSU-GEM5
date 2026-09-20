import copy
import json
import urllib.request

import pytest

import mesh_ir.ir.graph_ir as graph_ir
import mesh_ir.ir.graph_verify as graph_verify
import mesh_ir.schema as schema_module
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.ir.common import Access, Add, CeilDivByConst, Const, DType, FloorDivByConst, MulByConst, Symbol, TensorRole, contiguous_strides, dimension_data
from mesh_ir.ir.graph_ir import ElementwiseAttrs, EmbeddingAttrs, GraphFunction, GraphModule, GraphOp, GraphValue, MatmulAttrs, MovementAttrs, NormAttrs, OpCode, PytreeSpec, ReduceAttrs, SoftmaxAttrs, ViewAttrs


@pytest.fixture
def local_schema_package(tmp_path, monkeypatch):
    graph_schema = schema_module.load_schema("mesh_graph_v1.schema.json")
    root = tmp_path / "schemas"
    root.mkdir()
    (root / "mesh_graph_v1.schema.json").write_text(json.dumps(graph_schema), encoding="utf-8")
    consumer = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "shared-attrs-consumer-v1",
        "type": "object",
        "additionalProperties": False,
        "required": ["semantic_attrs"],
        "properties": {"semantic_attrs": {"$ref": "mesh-graph-v1#/$defs/attrs"}},
    }
    (root / "shared_attrs_consumer_v1.schema.json").write_text(json.dumps(consumer), encoding="utf-8")
    monkeypatch.setattr(schema_module, "files", lambda package: root)
    return root


def test_shared_serializer_covers_all_attribute_families_and_omits_only_absent_optionals():
    assert not hasattr(graph_ir, "_attrs_data")
    assert not hasattr(graph_verify, "_verify_attrs_type")
    symbol = Symbol(1, "s1", 2, 8, 2)
    shape = (
        Const(3),
        Add(symbol, Const(1)),
        MulByConst(symbol, 2),
        FloorDivByConst(symbol, 2),
        CeilDivByConst(Add(symbol, Const(1)), 2),
    )
    attributes = (
        MatmulAttrs(batch_axes=(0,), rhs_transpose=True, accum_dtype=DType.FP16),
        ViewAttrs(shape=shape, permutation=(1, 0)),
        MovementAttrs(axis=1, bounds_check=False),
        ElementwiseAttrs(),
        ReduceAttrs((1,), False, DType.FP32, DType.FP32),
        NormAttrs((1,), 1e-5, True, False),
        SoftmaxAttrs(1, DType.FP32, True),
        EmbeddingAttrs(-1, False, False),
    )
    serialized = tuple(graph_ir.operation_attrs_data(item) for item in attributes)
    assert [item["kind"] for item in serialized] == [type(item).__name__ for item in attributes]
    assert serialized[1]["shape"] == [dimension_data(item) for item in shape]
    assert "scalar" not in serialized[3]
    assert "max_norm" not in serialized[7]
    assert graph_ir.operation_attrs_data(ElementwiseAttrs(0, "rhs"))["scalar"] == 0
    assert graph_ir.operation_attrs_data(ElementwiseAttrs(False, "rhs"))["scalar"] is False
    assert graph_ir.operation_attrs_data(EmbeddingAttrs(-1, False, False, 0.0))["max_norm"] == 0.0
    for item in attributes:
        graph_verify.verify_operation_attribute_types(item)


@pytest.mark.parametrize(
    "attrs",
    [
        MatmulAttrs(batch_axes=(True,)),
        ViewAttrs(shape=(Add(Const(1), True),)),
        ViewAttrs(permutation=[0]),
        MovementAttrs(axis=True),
        ElementwiseAttrs(DType.FP32, "rhs"),
        ReduceAttrs((True,), False, DType.FP32, DType.FP32),
        NormAttrs((1,), 1e-5, 1, False),
        SoftmaxAttrs(1, 1),
        EmbeddingAttrs(True, False, False),
    ],
)
def test_shared_type_checker_rejects_nested_coercions(attrs):
    with pytest.raises(MeshIrError):
        graph_verify.verify_operation_attribute_types(attrs)


def test_graph_and_local_reference_consumer_validate_the_same_payload_and_reject_null(local_schema_package):
    shape = (Const(2), Const(3))
    values = (
        GraphValue(1, "input", TensorRole.INPUT, DType.FP32, shape, contiguous_strides(shape), 0, 1, Access.READ_ONLY),
        GraphValue(2, "output", TensorRole.OUTPUT, DType.FP32, shape, contiguous_strides(shape), 0, 2, Access.READ_ONLY),
    )
    operation = GraphOp(1, OpCode.RELU, (1,), (2,), ElementwiseAttrs(), "node:1")
    function = GraphFunction(1, "forward", (1,), (operation,), (2,), PytreeSpec.leaf(), PytreeSpec.leaf())
    graph = GraphModule.create("a" * 64, "b" * 64, "forward", "static", values, (function,))
    payload = graph.canonical_dict()
    attrs = payload["functions"][0]["ops"][0]["attrs"]
    schema_module.validate_schema("mesh_graph_v1.schema.json", payload, "Graph IR")
    schema_module.validate_schema("shared_attrs_consumer_v1.schema.json", {"semantic_attrs": attrs}, "shared attributes")
    for schema_name, value in (
        ("mesh_graph_v1.schema.json", copy.deepcopy(payload)),
        ("shared_attrs_consumer_v1.schema.json", {"semantic_attrs": dict(attrs)}),
    ):
        if schema_name == "mesh_graph_v1.schema.json":
            value["functions"][0]["ops"][0]["attrs"]["scalar"] = None
        else:
            value["semantic_attrs"]["scalar"] = None
        with pytest.raises(MeshIrError) as error:
            schema_module.validate_schema(schema_name, value, "shared attributes")
        assert error.value.code == "E_CONFIG"
    embedding = graph_ir.operation_attrs_data(EmbeddingAttrs(-1, False, False))
    embedding["max_norm"] = None
    with pytest.raises(MeshIrError) as error:
        schema_module.validate_schema("shared_attrs_consumer_v1.schema.json", {"semantic_attrs": embedding}, "shared attributes")
    assert error.value.code == "E_CONFIG"


@pytest.mark.parametrize(
    "attrs",
    [
        MatmulAttrs(),
        ViewAttrs(shape=(Const(2),)),
        MovementAttrs(axis=0, bounds_check=False),
        ElementwiseAttrs(0, "rhs"),
        ElementwiseAttrs(False, "rhs"),
        ReduceAttrs((0,), False, DType.FP32, DType.FP32),
        NormAttrs((0,), 1e-5, False, False),
        SoftmaxAttrs(0, DType.FP32, False),
        EmbeddingAttrs(-1, False, False),
        EmbeddingAttrs(-1, False, False, 0.0),
    ],
)
def test_local_reference_consumer_accepts_every_attribute_family(local_schema_package, attrs):
    schema_module.validate_schema(
        "shared_attrs_consumer_v1.schema.json",
        {"semantic_attrs": graph_ir.operation_attrs_data(attrs)},
        "shared attributes",
    )


@pytest.mark.parametrize(
    "reference",
    [
        "mesh-graph-v1#/$defs/not_present",
        "unknown-schema-v1#/$defs/attrs",
        "../mesh-graph-v1#/$defs/attrs",
        "file:///etc/passwd",
        "https://example.invalid/schema.json",
    ],
)
def test_schema_references_fail_closed_without_external_retrieval(local_schema_package, reference, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: pytest.fail("external schema retrieval attempted"))
    consumer = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "invalid-consumer-v1",
        "$ref": reference,
    }
    (local_schema_package / "invalid_consumer_v1.schema.json").write_text(json.dumps(consumer), encoding="utf-8")
    with pytest.raises(MeshIrError) as error:
        schema_module.validate_schema("invalid_consumer_v1.schema.json", {}, "invalid consumer")
    assert error.value.code == "E_CONFIG"


def test_schema_resource_name_rejects_traversal():
    with pytest.raises(MeshIrError) as error:
        schema_module.load_schema("../mesh_graph_v1.schema.json")
    assert error.value.code == "E_CONFIG"
