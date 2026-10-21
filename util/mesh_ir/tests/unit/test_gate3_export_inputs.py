import json

import pytest

from mesh_ir.diagnostics import MeshIrError
from mesh_ir.export_inputs import load_export_input_documents


def _write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def test_input_documents_create_exact_cpu_tensors_and_shared_dims(tmp_path):
    inputs = tmp_path / "inputs.json"
    shapes = tmp_path / "shapes.json"
    _write(inputs, {
        "schema_version": "mesh-export-inputs-v1",
        "args": [
            {"dtype": "FP32", "shape": [2, 2], "data": [0.5, 1, -2, 3]},
            {"dtype": "INT8", "shape": [2], "data": [-128, 127]},
        ],
    })
    _write(shapes, {
        "schema_version": "mesh-export-shapes-v1",
        "symbols": {"batch": {"min": 1, "max": 4}},
        "args": [{"0": "batch"}, {"0": "batch"}],
    })

    prepared = load_export_input_documents(inputs, shapes)

    assert tuple(str(item.dtype) for item in prepared.args) == ("torch.float32", "torch.int8")
    assert tuple(tuple(item.shape) for item in prepared.args) == ((2, 2), (2,))
    assert prepared.args[0].tolist() == [[0.5, 1.0], [-2.0, 3.0]]
    assert prepared.args[1].tolist() == [-128, 127]
    assert prepared.dynamic_shapes[0][0] is prepared.dynamic_shapes[1][0]
    assert prepared.document_identities == (
        ("inputs", prepared.inputs_sha256),
        ("dynamic_shapes", prepared.dynamic_shapes_sha256),
    )


@pytest.mark.parametrize(
    "tensor",
    (
        {"dtype": "INT8", "shape": [1], "data": [1.5]},
        {"dtype": "INT8", "shape": [1], "data": [True]},
        {"dtype": "INT8", "shape": [1], "data": [128]},
        {"dtype": "FP32", "shape": [1], "data": [1e39]},
        {"dtype": "FP32", "shape": [1], "data": [10**1000]},
        {"dtype": "FP32", "shape": [2], "data": [1]},
        {"dtype": "UNKNOWN", "shape": [1], "data": [1]},
        {"dtype": "FP32", "shape": [-1], "data": []},
    ),
)
def test_input_documents_reject_invalid_tensor_declarations(tmp_path, tensor):
    path = tmp_path / "inputs.json"
    _write(path, {"schema_version": "mesh-export-inputs-v1", "args": [tensor]})

    with pytest.raises(MeshIrError):
        load_export_input_documents(path)


@pytest.mark.parametrize(
    "shapes",
    (
        {"schema_version": "mesh-export-shapes-v1", "symbols": {"batch": {"min": 1, "max": 4}}, "args": [{}]},
        {"schema_version": "mesh-export-shapes-v1", "symbols": {}, "args": [{"0": "missing"}]},
        {"schema_version": "mesh-export-shapes-v1", "symbols": {"batch": {"min": 3, "max": 4}}, "args": [{"0": "batch"}]},
        {"schema_version": "mesh-export-shapes-v1", "symbols": {"batch": {"min": 1, "max": 4}}, "args": [{"1": "batch"}]},
        {"schema_version": "mesh-export-shapes-v1", "symbols": {"batch": {"min": 1, "max": 1}}, "args": [{"0": "batch"}]},
        {"schema_version": "mesh-export-shapes-v1", "symbols": {"batch": {"min": 1, "max": 4}}, "args": [{"00": "batch"}]},
        {"schema_version": "mesh-export-shapes-v1", "symbols": {"batch": {"min": 1, "max": 4}}, "args": [{"9" * 5000: "batch"}]},
    ),
)
def test_dynamic_shape_documents_reject_inconsistent_contracts(tmp_path, shapes):
    inputs = tmp_path / "inputs.json"
    dynamic = tmp_path / "shapes.json"
    _write(inputs, {
        "schema_version": "mesh-export-inputs-v1",
        "args": [{"dtype": "FP32", "shape": [2], "data": [1, 2]}],
    })
    _write(dynamic, shapes)

    with pytest.raises(MeshIrError):
        load_export_input_documents(inputs, dynamic)


def test_input_document_loader_rejects_duplicate_and_unknown_fields(tmp_path):
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        '{"schema_version":"mesh-export-inputs-v1","args":[],"args":[]}',
        encoding="utf-8",
    )
    unknown = tmp_path / "unknown.json"
    _write(unknown, {"schema_version": "mesh-export-inputs-v1", "args": [], "extra": 1})

    with pytest.raises(MeshIrError):
        load_export_input_documents(duplicate)
    with pytest.raises(MeshIrError):
        load_export_input_documents(unknown)
