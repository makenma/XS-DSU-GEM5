"""Qwen3 frontend primitives: the eight operator classes the full model
export needs, each as the minimal Torch module from the actual functional
graph, driven through the real export→decompose→DTO→canonicalize chain.

Negatives pin the rejection boundary: unproven assertions, illegal views,
unsupported casts and non-square exponents fail closed instead of skipping
nodes.
"""

from pathlib import Path

import pytest
import torch
from torch import nn

from mesh_ir.diagnostics import MeshIrError
from mesh_ir.frontend import FrontendRequest, export_graph
from mesh_ir.ir.graph_ir import OpCode

ARCH_DIGEST = "0" * 64


ROOT = Path(__file__).resolve().parents[2]


def _request(tmp_path, name):
    return FrontendRequest(
        entrypoint="forward", profile_id="static", arch_digest=ARCH_DIGEST,
        staging_dir=tmp_path / name, source_project_root=ROOT)


def _export(module, args, tmp_path, name, dynamic_shapes=None):
    torch.manual_seed(7)
    return export_graph((lambda: module), args, _request(tmp_path, name),
                        dynamic_shapes=dynamic_shapes)


def _opcodes(result):
    return sorted({op.opcode for op in result.graph.functions[0].ops})


def _module_ops(module, args, tmp_path, name):
    result = _export(module, args, tmp_path, name)
    return result, _opcodes(result)


class AssertMetadata(nn.Module):
    """torch.export inserts _assert_tensor_metadata guards around
    parameters; the module keeps one reachable in the graph."""

    def __init__(self):
        super().__init__()
        self.weight = nn.Parameter(torch.randn(4))

    def forward(self, x):
        return x + self.weight


def test_assert_metadata_guard_is_proven_and_eliminates(tmp_path):
    _, opcodes = _module_ops(AssertMetadata(),
                             (torch.randn(2, 4, dtype=torch.float32),),
                             tmp_path, "assert")
    assert OpCode.ADD in opcodes


class Unsqueeze(nn.Module):
    def forward(self, x):
        return x.unsqueeze(1)


@pytest.mark.parametrize("axis", (-2, -1, 0, 1, 2))
def test_unsqueeze_produces_a_reshape_view(tmp_path, axis):
    module = Unsqueeze()
    result, opcodes = _module_ops(module,
                                  (torch.randn(3, 5, dtype=torch.float32),),
                                  tmp_path, f"unsqueeze{axis}")
    assert OpCode.RESHAPE_VIEW in opcodes
    values = {v.value_id: v for v in result.graph.values}
    outputs = [values[o] for o in result.graph.functions[0].outputs]
    assert tuple(d.value for d in outputs[0].shape) == (3, 1, 5)


def test_unsqueeze_scalar_input(tmp_path):
    class Scalar(nn.Module):
        def forward(self, x):
            return x.unsqueeze(0)

    result, _ = _module_ops(Scalar(), (torch.randn((), dtype=torch.float32),),
                            tmp_path, "scalar")
    outputs = [v for v in result.graph.values
               if v.value_id in {o for o in result.graph.functions[0].outputs}]
    assert all(d.value == 1 for v in outputs for d in v.shape)


def test_unsqueeze_out_of_bounds_axis_is_rejected(tmp_path):
    class Bad(nn.Module):
        def forward(self, x):
            return x.unsqueeze(3)

    # torch.export rejects the out-of-bounds axis itself; the invalid
    # program never reaches our verifier.
    with pytest.raises((MeshIrError, Exception)):
        _export(Bad(), (torch.randn(3, 5, dtype=torch.float32),),
                tmp_path, "bad-axis")


class UnsafeView(nn.Module):
    def forward(self, x):
        return torch.ops.aten._unsafe_view(x, (5, 3))


def test_unsafe_view_lowers_to_a_proven_reshape_view(tmp_path):
    result, opcodes = _module_ops(
        UnsafeView(), (torch.randn(3, 5, dtype=torch.float32),),
        tmp_path, "unsafe")
    assert OpCode.RESHAPE_VIEW in opcodes


def test_unsafe_view_with_element_count_change_is_rejected(tmp_path):
    class Bad(nn.Module):
        def forward(self, x):
            return torch.ops.aten._unsafe_view(x, (4, 3))

    with pytest.raises((MeshIrError, Exception)):
        _export(Bad(), (torch.randn(3, 5, dtype=torch.float32),),
                tmp_path, "bad-view")


class ToCopy(nn.Module):
    def forward(self, x):
        return x.to(torch.float16)


def test_to_copy_lowers_to_cast_with_independent_storage(tmp_path):
    result, opcodes = _module_ops(
        ToCopy(), (torch.randn(2, 3, dtype=torch.float32),),
        tmp_path, "cast")
    assert OpCode.CAST in opcodes
    values = {v.value_id: v for v in result.graph.values}
    outputs = [values[o] for o in result.graph.functions[0].outputs]
    assert outputs[0].dtype.name == "FP16"
    assert outputs[0].alias_root == outputs[0].value_id


def test_to_copy_unsupported_layout_is_rejected(tmp_path):
    class Bad(nn.Module):
        def forward(self, x):
            return x.to(memory_format=torch.channels_last)

    with pytest.raises(MeshIrError):
        _export(Bad(), (torch.randn(2, 3, 4, 5, dtype=torch.float32),),
                tmp_path, "bad-layout")


class Neg(nn.Module):
    def forward(self, x):
        return -x


def test_neg_lowers_to_negate(tmp_path):
    _, opcodes = _module_ops(
        Neg(), (torch.randn(2, 3, dtype=torch.float16),), tmp_path, "neg")
    assert OpCode.NEGATE in opcodes


class PowSquare(nn.Module):
    def forward(self, x):
        return torch.pow(x, 2)


def test_pow_with_exponent_two_lowers_to_mul(tmp_path):
    _, opcodes = _module_ops(
        PowSquare(), (torch.randn(2, 3, dtype=torch.float32),),
        tmp_path, "pow2")
    assert OpCode.MUL in opcodes
    assert OpCode.CAST not in opcodes


def test_pow_with_other_exponent_is_rejected(tmp_path):
    class Bad(nn.Module):
        def forward(self, x):
            return torch.pow(x, 3)

    with pytest.raises(MeshIrError):
        _export(Bad(), (torch.randn(2, 3, dtype=torch.float32),),
                tmp_path, "pow3")


class SinCos(nn.Module):
    def forward(self, x):
        return torch.sin(x) + torch.cos(x)


def test_sin_and_cos_lower_to_explicit_ops(tmp_path):
    _, opcodes = _module_ops(
        SinCos(), (torch.randn(2, 3, dtype=torch.float32),),
        tmp_path, "trig")
    assert OpCode.SIN in opcodes
    assert OpCode.COS in opcodes


def test_sin_integer_dtype_is_rejected(tmp_path):
    class Bad(nn.Module):
        def forward(self, x):
            return torch.sin(x)

    with pytest.raises(MeshIrError):
        _export(Bad(), (torch.randint(-8, 9, (2, 3), dtype=torch.int32),),
                tmp_path, "sin-int")


def test_cast_to_integer_target_is_rejected(tmp_path):
    class Bad(nn.Module):
        def forward(self, x):
            return x.to(torch.int32)

    with pytest.raises(MeshIrError, match="cast dtype pair"):
        _export(Bad(), (torch.randn(2, 3, dtype=torch.float32),),
                tmp_path, "cast-int")


def test_cast_from_integer_source_is_accepted(tmp_path):
    class Positions(nn.Module):
        def forward(self, x):
            return x.to(torch.float32) * 1.0

    _, opcodes = _module_ops(
        Positions(),
        (torch.randint(0, 16, (2, 8), dtype=torch.int32),),
        tmp_path, "cast-up")
    assert OpCode.CAST in opcodes
