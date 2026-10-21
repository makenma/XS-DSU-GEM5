import ast
import hashlib
from pathlib import Path

import pytest
import torch


@pytest.fixture(scope="module")
def content_digest():
    path = Path(__file__).resolve().parents[2] / "mesh_ir/torch_compat.py"
    module = ast.parse(path.read_text())
    function = next(node for node in module.body
                    if isinstance(node, ast.FunctionDef) and node.name == "_content_digest")
    scope = {"torch": torch, "hashlib": hashlib}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), scope)
    return scope["_content_digest"]


@pytest.mark.parametrize("dtype", [torch.bfloat16, torch.float16, torch.float32, torch.int8, torch.int32])
@pytest.mark.parametrize("layout", ["contiguous", "transpose", "offset", "empty", "scalar"])
def test_constant_digest_uses_exact_contiguous_bytes_without_python_byte_list(content_digest, monkeypatch, dtype, layout):
    tensor = torch.arange(30, dtype=dtype).reshape(5, 6)
    if layout == "transpose":
        tensor = tensor.T
    elif layout == "offset":
        tensor = tensor[1:4, 2:5]
    elif layout == "empty":
        tensor = tensor[:0]
    elif layout == "scalar":
        tensor = tensor[1, 2]
    expected = hashlib.sha256(bytes(tensor.contiguous().reshape(-1).view(torch.uint8).tolist())).hexdigest()

    def reject_list(*args, **kwargs):
        raise AssertionError("parameter bytes must not be expanded into Python objects")

    monkeypatch.setattr(torch.Tensor, "tolist", reject_list)
    assert content_digest(tensor) == expected
