import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest
import torch
from torch import nn

from examples.tiny_mlp import create_model as create_example_mlp, example_args as example_mlp_args
from examples.tiny_transformer import create_model as create_example_transformer, dynamic_shapes as example_transformer_dynamic_shapes, example_args as example_transformer_args

from mesh_ir.diagnostics import MeshIrError
import mesh_ir.frontend as frontend_module
import mesh_ir.torch_compat as torch_compat_module
from mesh_ir.compat import operator_family
from mesh_ir.frontend import FrontendRequest, export_graph, load_graph
from mesh_ir.ir.common import DType, TensorRole
from mesh_ir.ir.graph_ir import ElementwiseAttrs, EmbeddingAttrs, MatmulAttrs, NormAttrs, OpCode
from mesh_ir.torch_compat import decompose_program, execute_program, export_program


ROOT = Path(__file__).resolve().parents[4]


class TinyMlp(nn.Module):
    def __init__(self):
        super().__init__()
        torch.manual_seed(17)
        self.linear = nn.Linear(4, 3)
        self.register_buffer("persistent", torch.ones(3))
        self.register_buffer("nonpersistent", torch.ones(3), persistent=False)
        self.constant = torch.full((3,), 2.0)

    def forward(self, x):
        return torch.relu(self.linear(x) + self.persistent + self.nonpersistent + self.constant)


class OpZoo(nn.Module):
    def forward(self, a, b, batch_a, batch_b, embedding, indices):
        matmul = torch.matmul(a, b)
        bmm = torch.bmm(batch_a, batch_b)
        reshaped = matmul.reshape(2, 2)
        transposed = reshaped.transpose(0, 1)
        permuted = transposed.permute(1, 0)
        sliced = permuted[:1, :2]
        expanded = sliced.expand(2, 2)
        moved = torch.cat((transposed.contiguous(), expanded), dim=0)
        gathered = torch.index_select(embedding, 0, indices)
        embedded = torch.nn.functional.embedding(indices, embedding)
        elem = torch.rsqrt(torch.exp(torch.nn.functional.silu(torch.nn.functional.gelu(torch.relu(moved + 1) - 1) * 2 / 2)))
        reduced = elem.sum((0,), keepdim=True) + elem.amax((0,), keepdim=True) + elem.mean((0,), keepdim=True)
        norm = torch.nn.functional.layer_norm(reduced, (2,))
        rms = torch.nn.functional.rms_norm(reduced, (2,), eps=0.001)
        softmax = torch.softmax(norm, dim=-1)
        return softmax + rms, bmm, gathered, embedded


class OneMatmul(nn.Module):
    def forward(self, lhs, rhs):
        return torch.matmul(lhs, rhs)


class OneBmm(nn.Module):
    def forward(self, lhs, rhs):
        return torch.bmm(lhs, rhs)


class OneReshape(nn.Module):
    def forward(self, value):
        return value.reshape(2, 2)


class OneTranspose(nn.Module):
    def forward(self, value):
        return value.transpose(0, 1)


class BatchedMatmul(nn.Module):
    def forward(self, lhs, rhs):
        return torch.matmul(lhs, rhs)


@pytest.mark.parametrize(
    "factory,args,opcodes",
    [
        (lambda: OneMatmul(), (torch.ones(2, 3), torch.ones(3, 2)), [OpCode.MATMUL]),
        (lambda: OneBmm(), (torch.ones(2, 2, 3), torch.ones(2, 3, 2)), [OpCode.BMM]),
        (lambda: OneReshape(), (torch.ones(4),), [OpCode.RESHAPE_VIEW]),
        (lambda: OneTranspose(), (torch.ones(2, 3),), [OpCode.TRANSPOSE_VIEW]),
    ],
)
def test_mandatory_core_ops_have_separate_exact_real_cases(factory, args, opcodes, tmp_path):
    result = export_graph(factory, args, request(tmp_path))
    assert [op.opcode for op in result.graph.functions[0].ops] == opcodes


def request(tmp_path, entrypoint="forward"):
    return FrontendRequest(entrypoint=entrypoint, profile_id="static", arch_digest="a" * 64, staging_dir=tmp_path, source_project_root=ROOT / "util/mesh_ir")


def test_real_export_save_load_and_signature_classification(tmp_path):
    result = export_graph(lambda: TinyMlp(), (torch.randn(2, 4),), request(tmp_path))
    assert (tmp_path / "exported_program.pt2").is_file()
    assert {"graph_ir.json", "decomposition_manifest.json", "frontend_provenance.json", "frontend_passes.json"} <= {path.name for path in tmp_path.iterdir()}
    loaded = load_graph(tmp_path / "exported_program.pt2", request(tmp_path / "loaded"))
    roles = {value.name: value.role for value in loaded.graph.values}
    assert roles["linear.weight"] == TensorRole.WEIGHT
    assert roles["linear.bias"] == TensorRole.WEIGHT
    assert roles["persistent"] == TensorRole.STATE
    assert roles["nonpersistent"] == TensorRole.STATE
    assert roles["constant"] == TensorRole.CONSTANT
    assert roles["x"] == TensorRole.INPUT
    assert any(value.role == TensorRole.OUTPUT for value in loaded.graph.values)
    assert [op.opcode for op in result.graph.functions[0].ops] == [OpCode.LINEAR_BIAS, OpCode.ADD, OpCode.ADD, OpCode.ADD, OpCode.RELU]
    assert result.graph.semantic_sha256 == loaded.graph.semantic_sha256
    assert result.decomposition_manifest.dialect_after == "ATEN"
    assert result.decomposition_manifest.torch_version == "2.8.0+cpu"
    assert result.decomposition_manifest.source_opset == (("aten", 10),)
    assert result.decomposition_manifest.source_schema == (8, 8)
    assert [record.name for record in result.passes] == [
        "LoadAndValidateExport", "DecomposeToPinnedCoreAten", "ImportGraphIR",
        "CanonicalizeFunctionalOps", "SpecializeShapeProfiles",
    ]
    assert result.provenance.source_sha256 is not None
    assert result.provenance.compiler_mode == "source"
    expected_head = subprocess.check_output(
        ["git", "-C", str(ROOT / "util/mesh_ir"), "rev-parse", "HEAD"], text=True
    ).strip()
    assert result.provenance.compiler_git_sha == expected_head
    assert len(result.provenance.compiler_package_sha256) == 64
    assert len(result.provenance.requirements_lock_sha256) == 64
    assert result.provenance.source_schema == (8, 8)
    assert result.provenance.source_opset
    assert result.provenance.source_opset == (("aten", 10),)
    assert result.provenance.archive_semantic_sha256 == result.passes[0].input_hash


def test_first_pass_elapsed_time_covers_real_export_validation(monkeypatch, tmp_path):
    real_export = frontend_module.export_program

    def delayed_export(*args, **kwargs):
        time.sleep(0.02)
        return real_export(*args, **kwargs)

    monkeypatch.setattr(frontend_module, "export_program", delayed_export)
    result = export_graph(lambda: nn.ReLU(), (torch.ones(2),), request(tmp_path))
    assert result.passes[0].elapsed_ns >= 20_000_000


def test_real_required_operation_families_are_canonicalized(tmp_path):
    args = (
        torch.randn(2, 3), torch.randn(3, 2), torch.randn(2, 2, 3), torch.randn(2, 3, 2),
        torch.randn(8, 2), torch.tensor([1, 3], dtype=torch.int32),
    )
    result = export_graph(lambda: OpZoo(), args, request(tmp_path))
    opcodes = {op.opcode for op in result.graph.functions[0].ops}
    assert {
        OpCode.MATMUL, OpCode.BMM, OpCode.RESHAPE_VIEW, OpCode.TRANSPOSE_VIEW,
        OpCode.PERMUTE_VIEW, OpCode.SLICE_VIEW, OpCode.EXPAND_VIEW,
        OpCode.CONTIGUOUS_COPY, OpCode.CONCAT, OpCode.GATHER_ROWS,
        OpCode.ADD, OpCode.SUB, OpCode.MUL, OpCode.DIV, OpCode.RELU,
        OpCode.GELU, OpCode.SILU, OpCode.EXP, OpCode.RSQRT,
        OpCode.REDUCE_SUM, OpCode.REDUCE_MAX, OpCode.REDUCE_MEAN,
        OpCode.LAYERNORM, OpCode.RMSNORM, OpCode.SOFTMAX, OpCode.EMBEDDING_LOOKUP,
    } <= opcodes
    copied = next(op for op in result.graph.functions[0].ops if op.opcode == OpCode.CONTIGUOUS_COPY)
    transposed = result.graph.values[copied.operands[0] - 1]
    assert transposed.alias_root != result.graph.values[copied.results[0] - 1].alias_root
    rms = next(op for op in result.graph.functions[0].ops if op.opcode == OpCode.RMSNORM)
    embedding = next(op for op in result.graph.functions[0].ops if op.opcode == OpCode.EMBEDDING_LOOKUP)
    softmax = next(op for op in result.graph.functions[0].ops if op.opcode == OpCode.SOFTMAX)
    assert isinstance(rms.attrs, NormAttrs) and rms.attrs.epsilon == 0.001
    assert isinstance(embedding.attrs, EmbeddingAttrs) and embedding.attrs.padding_idx == -1
    assert softmax.attrs.axis == 1
    assert not softmax.attrs.zero_fully_masked_rows


class SemanticAttrs(nn.Module):
    def __init__(self):
        super().__init__()
        self.weight = nn.Parameter(torch.ones(2))
        self.bias = nn.Parameter(torch.zeros(2))
        self.table = nn.Parameter(torch.ones(4, 2))

    def forward(self, x, rhs, indices):
        fused = torch.addmm(self.bias, x, rhs, beta=0.5, alpha=2.0)
        added = torch.add(fused, 3.0, alpha=2.0)
        gelu = torch.nn.functional.gelu(added, approximate="tanh")
        norm = torch.nn.functional.layer_norm(gelu, (2,), self.weight, self.bias, eps=0.002)
        embedded = torch.nn.functional.embedding(indices, self.table, padding_idx=1)
        return norm, embedded


def test_nondefault_semantic_attributes_are_explicit(tmp_path):
    args = (torch.randn(2, 3), torch.randn(3, 2), torch.tensor([0, 1], dtype=torch.int32))
    result = export_graph(lambda: SemanticAttrs(), args, request(tmp_path))
    ops = result.graph.functions[0].ops
    addmm = next(op for op in ops if op.opcode == OpCode.LINEAR_BIAS)
    added = next(op for op in ops if op.opcode == OpCode.ADD)
    gelu = next(op for op in ops if op.opcode == OpCode.GELU)
    norm = next(op for op in ops if op.opcode == OpCode.LAYERNORM)
    embedding = next(op for op in ops if op.opcode == OpCode.EMBEDDING_LOOKUP)
    assert isinstance(addmm.attrs, MatmulAttrs) and (addmm.attrs.alpha, addmm.attrs.beta) == (2.0, 0.5)
    assert isinstance(added.attrs, ElementwiseAttrs) and (added.attrs.scalar, added.attrs.alpha) == (3.0, 2.0)
    assert isinstance(gelu.attrs, ElementwiseAttrs) and gelu.attrs.approximation == "tanh"
    assert isinstance(norm.attrs, NormAttrs) and (norm.attrs.epsilon, norm.attrs.has_weight, norm.attrs.has_bias) == (0.002, True, True)
    assert isinstance(embedding.attrs, EmbeddingAttrs) and embedding.attrs.padding_idx == 1


class DynamicAttentionScore(nn.Module):
    def forward(self, x, y):
        return torch.bmm(x, y.transpose(1, 2))


def test_real_dynamic_shared_symbols_and_derived_contiguous_stride(tmp_path):
    batch = torch.export.Dim("batch", min=1, max=4)
    sequence = torch.export.Dim("sequence", min=2, max=8)
    dynamic_shapes = ({0: batch, 1: sequence}, {0: batch, 1: sequence})
    frontend_request = FrontendRequest(
        "forward", "symbolic", "a" * 64, tmp_path,
        symbol_bindings=(("B", "x", 0), ("S", "x", 1)),
        shape_profiles=(("b1s2", (("B", 1), ("S", 2))), ("b2s8", (("B", 2), ("S", 8)))),
        source_project_root=ROOT / "util/mesh_ir",
    )
    result = export_graph(lambda: DynamicAttentionScore(), (torch.randn(2, 4, 8), torch.randn(2, 4, 8)), frontend_request, dynamic_shapes=dynamic_shapes)
    assert len(result.variants) == 2
    output = next(value for value in result.variants[1].values if value.role == TensorRole.OUTPUT)
    assert [stride.value for stride in output.strides] == [64, 8, 1]
    assert [dimension.value for dimension in output.shape] == [2, 8, 8]
    assert result.variants[0].semantic_sha256 != result.variants[1].semantic_sha256


def test_nested_dynamic_input_provenance_records_leaf_metadata_and_declaration(tmp_path):
    class NestedIdentity(nn.Module):
        def forward(self, payload):
            return torch.relu(payload["x"])

    batch = torch.export.Dim("batch", min=1, max=4)
    args = ({"x": torch.ones(2, 3), "y": torch.ones(2, 3, dtype=torch.float16)},)
    dynamic_shapes = ({"x": {0: batch}, "y": None},)
    frontend_request = FrontendRequest(
        "forward", "symbolic", "a" * 64, tmp_path,
        shape_profiles=(("b2", (("s1", 2),)),),
        source_project_root=ROOT / "util/mesh_ir",
    )
    result = export_graph(lambda: NestedIdentity(), args, frontend_request, dynamic_shapes=dynamic_shapes)
    provenance = dict(result.provenance.export_args)
    assert provenance["input[0]['x'].shape"] == (2, 3)
    assert provenance["input[0]['x'].dtype"] == "torch.float32"
    assert provenance["input[0]['y'].shape"] == (2, 3)
    assert provenance["input[0]['y'].dtype"] == "torch.float16"
    assert provenance["dynamic_shapes"] == [
        {
            "input": "input[0]['x']",
            "dimensions": [{"axis": 0, "maximum": 4, "minimum": 1, "name": "batch"}],
        },
    ]


def test_shared_symbol_aliases_must_agree_and_canonical_names_need_no_alias(tmp_path):
    batch = torch.export.Dim("batch", min=1, max=4)
    sequence = torch.export.Dim("sequence", min=2, max=8)
    dynamic_shapes = ({0: batch, 1: sequence}, {0: batch, 1: sequence})
    args = (torch.randn(2, 4, 8), torch.randn(2, 4, 8))
    contradictory = FrontendRequest(
        "forward", "symbolic", "a" * 64, tmp_path / "bad",
        symbol_bindings=(("B_x", "x", 0), ("B_y", "y", 0), ("S", "x", 1)),
        shape_profiles=(("bad", (("B_x", 1), ("B_y", 2), ("S", 4))),),
        source_project_root=ROOT / "util/mesh_ir",
    )
    with pytest.raises(MeshIrError) as error:
        export_graph(lambda: DynamicAttentionScore(), args, contradictory, dynamic_shapes=dynamic_shapes)
    assert error.value.code == "E_SHAPE_PROFILE_MISMATCH"
    canonical = FrontendRequest(
        "forward", "symbolic", "a" * 64, tmp_path / "canonical",
        shape_profiles=(("p", (("s1", 2), ("s2", 4))),),
        source_project_root=ROOT / "util/mesh_ir",
    )
    result = export_graph(lambda: DynamicAttentionScore(), args, canonical, dynamic_shapes=dynamic_shapes)
    assert len(result.variants) == 1


class NestedInputCoreOps(nn.Module):
    def __init__(self, dtype=torch.float32):
        super().__init__()
        self.linear = nn.Linear(4, 3, bias=False, dtype=dtype)

    def forward(self, payload):
        reshaped = self.linear(payload["x"]).reshape(2, -1)
        reduced = reshaped.sum((1,), dtype=torch.float32)
        return reshaped, reduced


def test_nested_tensor_input_bias_free_linear_inferred_reshape_and_dtype_attr(tmp_path):
    result = export_graph(lambda: NestedInputCoreOps(), ({"x": torch.ones(2, 4)},), request(tmp_path))
    ops = result.graph.functions[0].ops
    linear = next(op for op in ops if op.opcode == OpCode.MATMUL)
    reshape = next(op for op in ops if op.opcode == OpCode.RESHAPE_VIEW)
    reduction = next(op for op in ops if op.opcode == OpCode.REDUCE_SUM)
    assert isinstance(linear.attrs, MatmulAttrs) and linear.attrs.rhs_transpose
    assert tuple(dimension.value for dimension in reshape.attrs.shape) == (2, 3)
    assert reduction.attrs.output_dtype.name == "FP32"


def test_batched_matmul_records_common_aligned_batch_axes(tmp_path):
    result = export_graph(
        lambda: BatchedMatmul(),
        (torch.ones(2, 3, 4), torch.ones(2, 4, 5)),
        request(tmp_path),
    )
    operation = result.graph.functions[0].ops[0]
    assert operation.opcode == OpCode.MATMUL
    assert isinstance(operation.attrs, MatmulAttrs)
    assert operation.attrs.batch_axes == (0,)


@pytest.mark.parametrize("dtype", [torch.int8, torch.int32])
def test_integer_matmul_uses_int32_accumulation(dtype, tmp_path):
    result = export_graph(
        lambda: OneMatmul(),
        (torch.ones(2, 3, dtype=dtype), torch.ones(3, 2, dtype=dtype)),
        request(tmp_path),
    )
    operation = result.graph.functions[0].ops[0]
    assert isinstance(operation.attrs, MatmulAttrs)
    assert operation.attrs.accum_dtype == DType.INT32


class DefaultRmsNorm(nn.Module):
    def forward(self, x):
        return torch.nn.functional.rms_norm(x, (4,))


@pytest.mark.parametrize(
    "dtype,epsilon",
    [
        (torch.float32, 1.1920928955078125e-7),
        (torch.float16, 0.0009765625),
        (torch.bfloat16, 0.0078125),
    ],
)
def test_rms_norm_default_epsilon_matches_input_dtype(dtype, epsilon, tmp_path):
    result = export_graph(lambda: DefaultRmsNorm(), (torch.ones(2, 4, dtype=dtype),), request(tmp_path))
    rms = next(op for op in result.graph.functions[0].ops if op.opcode == OpCode.RMSNORM)
    assert isinstance(rms.attrs, NormAttrs) and rms.attrs.epsilon == epsilon


class DynamicSlice(nn.Module):
    def forward(self, x):
        return x[:, ::2]


def test_real_derived_floor_div_dimension_specializes(tmp_path):
    sequence = torch.export.Dim("sequence", min=4, max=9)
    frontend_request = FrontendRequest(
        "forward", "symbolic", "a" * 64, tmp_path,
        shape_profiles=(("s5", (("s1", 5),)), ("s8", (("s1", 8),))),
        source_project_root=ROOT / "util/mesh_ir",
    )
    result = export_graph(lambda: DynamicSlice(), (torch.ones(2, 5),), frontend_request, dynamic_shapes=({1: sequence},))
    outputs = [next(value for value in variant.values if value.role == TensorRole.OUTPUT) for variant in result.variants]
    assert [[dimension.value for dimension in value.shape] for value in outputs] == [[2, 3], [2, 4]]


class Sdpa(nn.Module):
    def __init__(self, scale=None):
        super().__init__()
        self.scale = scale

    def forward(self, query, key, value):
        return torch.nn.functional.scaled_dot_product_attention(
            query, key, value, dropout_p=0.0, scale=self.scale
        )


class MaskedSdpa(nn.Module):
    def forward(self, query, key, value, mask):
        return torch.nn.functional.scaled_dot_product_attention(
            query, key, value, attn_mask=mask, dropout_p=0.0, scale=0.25
        )


@pytest.mark.parametrize("factory,masked,scale", [(lambda: Sdpa(), False, 0.5), (lambda: Sdpa(0.25), False, 0.25), (lambda: MaskedSdpa(), True, 0.25)])
def test_real_sdpa_lowers_to_expressible_canonical_attention(factory, masked, scale, tmp_path):
    args = tuple(torch.ones(2, 3, 4) for _ in range(3))
    if masked:
        args += (torch.zeros(3, 3),)
    result = export_graph(factory, args, request(tmp_path))
    operations = result.graph.functions[0].ops
    assert [op.opcode for op in operations] == [
        OpCode.TRANSPOSE_VIEW,
        OpCode.MATMUL,
        OpCode.MUL,
        *([OpCode.ADD] if masked else []),
        OpCode.SOFTMAX,
        OpCode.MATMUL,
    ]
    scale_op = next(op for op in operations if op.opcode == OpCode.MUL)
    assert isinstance(scale_op.attrs, ElementwiseAttrs) and scale_op.attrs.scalar == scale
    softmax = next(op for op in operations if op.opcode == OpCode.SOFTMAX)
    assert softmax.attrs.zero_fully_masked_rows
    assert ("aten._safe_softmax.default", 1) in result.decomposition_manifest.after_histogram


def test_sdpa_additive_all_negative_infinity_rows_preserve_safe_softmax_semantics(tmp_path):
    args = tuple(torch.ones(2, 3, 4) for _ in range(3)) + (torch.full((3, 3), -torch.inf),)
    result = export_graph(lambda: MaskedSdpa(), args, request(tmp_path))
    softmax = next(op for op in result.graph.functions[0].ops if op.opcode == OpCode.SOFTMAX)
    assert softmax.attrs.zero_fully_masked_rows
    assert ("aten._safe_softmax.default", 1) in result.decomposition_manifest.after_histogram


@pytest.mark.parametrize("mask_kind", ["finite", "partial", "fully_masked_row"])
def test_sdpa_decomposition_matches_exported_program_numerically(mask_kind, tmp_path):
    query = torch.arange(24, dtype=torch.float32).reshape(2, 3, 4) / 11
    key = torch.arange(24, dtype=torch.float32).reshape(2, 3, 4).flip(-1) / 7
    value = (torch.arange(24, dtype=torch.float32).reshape(2, 3, 4) - 9) / 5
    if mask_kind == "finite":
        mask = torch.tensor([[0.0, -0.25, 0.5], [0.75, 0.0, -0.5], [-0.125, 0.25, 0.0]])
    elif mask_kind == "partial":
        mask = torch.tensor([[0.0, -torch.inf, -torch.inf], [-torch.inf, 0.0, -torch.inf], [-torch.inf, -torch.inf, 0.0]])
    else:
        mask = torch.tensor([[0.0, -torch.inf, -torch.inf], [-torch.inf, -torch.inf, -torch.inf], [-torch.inf, 0.0, -torch.inf]])
    args = (query, key, value, mask)
    original = export_program(lambda: MaskedSdpa(), args, tmp_path / f"{mask_kind}.pt2")
    decomposed = decompose_program(original)
    expected = execute_program(original, args)
    actual = execute_program(decomposed, args)
    torch.testing.assert_close(actual, expected)
    if mask_kind == "fully_masked_row":
        torch.testing.assert_close(actual[:, 1, :], torch.zeros_like(actual[:, 1, :]))


@pytest.mark.parametrize("mode", ["causal", "boolean_mask", "dropout", "low_precision"])
def test_unexpressible_sdpa_semantics_are_precisely_rejected(mode, tmp_path):
    class UnsupportedSdpa(nn.Module):
        def forward(self, query, key, value, mask):
            return torch.nn.functional.scaled_dot_product_attention(
                query,
                key,
                value,
                attn_mask=mask if mode == "boolean_mask" else None,
                dropout_p=0.1 if mode == "dropout" else 0.0,
                is_causal=mode == "causal",
            )

    dtype = torch.float16 if mode == "low_precision" else torch.float32
    args = tuple(torch.full((2, 3, 4), 65504, dtype=dtype) for _ in range(3)) + (torch.ones(3, 3, dtype=torch.bool),)
    with pytest.raises(MeshIrError) as error:
        export_graph(lambda: UnsupportedSdpa(), args, request(tmp_path))
    assert error.value.code == "E_EXPORT_UNSUPPORTED_OP"
    for field in ("target", "schema", "source_node_id", "input_shapes", "output_shapes", "input_dtypes", "output_dtypes", "source_location", "nearest_family", "guidance"):
        assert error.value.context[field]
    assert error.value.context["target"] == "aten.scaled_dot_product_attention.default"
    assert error.value.context["nearest_family"] == "attention"


class NativeLayerNorm(nn.Module):
    def __init__(self, output_index):
        super().__init__()
        self.output_index = output_index

    def forward(self, value):
        return torch.native_layer_norm(value, (4,), None, None, 1e-5)[self.output_index]


def test_native_layer_norm_primary_projection_is_structurally_lowered(tmp_path):
    result = export_graph(lambda: NativeLayerNorm(0), (torch.ones(2, 4),), request(tmp_path))
    assert [op.opcode for op in result.graph.functions[0].ops] == [OpCode.LAYERNORM]


def test_native_layer_norm_auxiliary_projection_is_precisely_rejected(tmp_path):
    with pytest.raises(MeshIrError) as error:
        export_graph(lambda: NativeLayerNorm(1), (torch.ones(2, 4),), request(tmp_path))
    assert error.value.code == "E_EXPORT_UNSUPPORTED_OP"
    assert error.value.context["projection_index"] == 1
    for field in ("target", "schema", "source_node_id", "input_shapes", "output_shapes", "input_dtypes", "output_dtypes", "source_location", "nearest_family", "guidance"):
        assert error.value.context[field]
    assert error.value.context["nearest_family"] == "normalization"


@pytest.mark.parametrize("operation,opcode", [("sum", OpCode.REDUCE_SUM), ("mean", OpCode.REDUCE_MEAN), ("amax", OpCode.REDUCE_MAX)])
def test_full_tensor_reductions_use_all_axes(operation, opcode, tmp_path):
    class FullReduction(nn.Module):
        def forward(self, value):
            return getattr(value, operation)()

    result = export_graph(lambda: FullReduction(), (torch.ones(2, 4),), request(tmp_path))
    reduction = result.graph.functions[0].ops[0]
    assert reduction.opcode == opcode
    assert reduction.attrs.axes == (0, 1)


def test_expand_retained_dimension_uses_verified_result_shape(tmp_path):
    class ExpandRetained(nn.Module):
        def forward(self, value):
            return value.expand(-1, 3)

    result = export_graph(lambda: ExpandRetained(), (torch.ones(2, 1),), request(tmp_path))
    operation = result.graph.functions[0].ops[0]
    assert operation.opcode == OpCode.EXPAND_VIEW
    assert tuple(dimension.value for dimension in operation.attrs.shape) == (2, 3)


def test_int64_index_dtype_is_rejected(tmp_path):
    args = (torch.randn(4, 2), torch.tensor([1], dtype=torch.int64))

    class Index(nn.Module):
        def forward(self, weight, index):
            return torch.nn.functional.embedding(index, weight)

    with pytest.raises(MeshIrError) as error:
        export_graph(lambda: Index(), args, request(tmp_path))
    assert error.value.code == "E_EXPORT_DTYPE"


class Unsupported(nn.Module):
    def forward(self, x):
        return torch.sin(x)


def test_unsupported_op_has_complete_stable_context(tmp_path):
    with pytest.raises(MeshIrError) as error:
        export_graph(lambda: Unsupported(), (torch.randn(2, 2),), request(tmp_path))
    assert error.value.code == "E_EXPORT_UNSUPPORTED_OP"
    assert error.value.context["target"] == "aten.sin.default"
    assert "aten::sin" in error.value.context["schema"]
    assert error.value.context["source_node_id"].startswith("node:")
    assert error.value.context["input_shapes"]
    assert error.value.context["output_shapes"]
    assert error.value.context["input_dtypes"] == ["FP32"]
    assert error.value.context["output_dtypes"] == ["FP32"]
    assert "test_gate1_real_frontend.py:" in error.value.context["source_location"]
    assert error.value.context["nearest_family"] == "elementwise"
    assert error.value.context["guidance"]


class UnsupportedCloneAttribute(nn.Module):
    def forward(self, x):
        return torch.clone(x, memory_format=torch.preserve_format)


def test_unsupported_argument_rejection_uses_complete_operator_context(tmp_path):
    with pytest.raises(MeshIrError) as error:
        export_graph(lambda: UnsupportedCloneAttribute(), (torch.ones(2, 3),), request(tmp_path))
    assert error.value.code == "E_EXPORT_UNSUPPORTED_OP"
    for field in ("target", "schema", "source_node_id", "input_shapes", "output_shapes", "input_dtypes", "output_dtypes", "source_location", "nearest_family", "guidance"):
        assert error.value.context[field]
    assert error.value.context["target"] == "aten.clone.default"
    assert error.value.context["nearest_family"] == "view"


def test_diagnostic_operator_family_uses_exact_aten_operation_names():
    assert operator_family("aten.sin.default") == "elementwise"
    assert operator_family("aten.max.dim") == "reduction"
    assert operator_family("aten.sort.default") == "unknown"
    assert operator_family("custom.matmul_fake.default") == "unknown"


def test_source_location_selects_a_repository_frame_after_external_frames():
    class Node:
        meta = {
            "stack_trace": (
                'File "/opt/framework/internal.py", line 7, in capture\n'
                f'File "{Path(__file__).resolve()}", line 11, in forward\n'
            )
        }

    assert torch_compat_module._source_location(Node()) == "util/mesh_ir/tests/integration/test_gate1_real_frontend.py:11"


class TupleResult(nn.Module):
    def forward(self, x):
        return torch.max(x, dim=1).values


class UnsafeView(nn.Module):
    def forward(self, x):
        return torch.as_strided(x, (2, 2), (1, 1))


def test_tuple_result_and_unproved_as_strided_are_rejected(tmp_path):
    with pytest.raises(MeshIrError) as tuple_error:
        export_graph(lambda: TupleResult(), (torch.ones(2, 3),), request(tmp_path / "tuple"))
    assert tuple_error.value.code == "E_EXPORT_UNSUPPORTED_OP"
    for field in ("target", "schema", "source_node_id", "input_shapes", "output_shapes", "input_dtypes", "output_dtypes", "source_location", "nearest_family", "guidance"):
        assert tuple_error.value.context[field]
    assert tuple_error.value.context["target"] == "aten.max.dim"
    assert tuple_error.value.context["nearest_family"] == "reduction"
    with pytest.raises(MeshIrError) as layout_error:
        export_graph(lambda: UnsafeView(), (torch.ones(4),), request(tmp_path / "layout"))
    assert layout_error.value.code == "E_EXPORT_LAYOUT"


def test_noncontiguous_input_stride_survives_and_overlapping_input_rejects(tmp_path):
    noncontiguous = torch.arange(6, dtype=torch.float32).reshape(2, 3).transpose(0, 1)
    result = export_graph(lambda: nn.ReLU(), (noncontiguous,), request(tmp_path / "strided"))
    input_value = next(value for value in result.graph.values if value.role == TensorRole.INPUT)
    assert tuple(stride.value for stride in input_value.strides) == (1, 3)
    overlapping = torch.as_strided(torch.arange(4, dtype=torch.float32), (2, 2), (1, 1))
    with pytest.raises(MeshIrError) as error:
        export_graph(lambda: nn.ReLU(), (overlapping,), request(tmp_path / "overlap"))
    assert error.value.code == "E_EXPORT_LAYOUT"


class Mutating(nn.Module):
    def forward(self, x):
        x.add_(1)
        return x


def test_mutated_user_input_is_rejected(tmp_path):
    with pytest.raises(MeshIrError) as error:
        export_graph(lambda: Mutating(), (torch.zeros(2),), request(tmp_path))
    assert error.value.code == "E_EXPORT_STATE_MUTATION"


class DataDependentGraphBreak(nn.Module):
    def forward(self, x):
        if x.sum().item() > 0:
            return x + 1
        return x - 1


def test_data_dependent_graph_break_is_rejected(tmp_path):
    with pytest.raises(MeshIrError) as error:
        export_graph(lambda: DataDependentGraphBreak(), (torch.ones(2),), request(tmp_path))
    assert error.value.code == "E_EXPORT_GRAPH_BREAK"


@pytest.mark.parametrize(
    "factory,args,code",
    [
        (lambda: object(), (torch.ones(1),), "E_CONFIG"),
        (lambda: TinyMlp(), ("not-a-tensor",), "E_EXPORT_NON_TENSOR_IO"),
    ],
)
def test_factory_and_input_contracts_are_rejected(factory, args, code, tmp_path):
    with pytest.raises(MeshIrError) as error:
        export_graph(factory, args, request(tmp_path))
    assert error.value.code == code


class ScalarOutput(nn.Module):
    def forward(self, x):
        return 7


def test_non_tensor_output_is_rejected(tmp_path):
    with pytest.raises(MeshIrError) as error:
        export_graph(lambda: ScalarOutput(), (torch.ones(1),), request(tmp_path))
    assert error.value.code == "E_EXPORT_NON_TENSOR_IO"


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("torch_version", "0.0.0"),
        ("schema_version", {"major": 99, "minor": 0}),
        ("opset_version", {"aten": 999}),
        ("opset_version", {}),
        (None, []),
    ],
)
def test_archive_version_mismatch_is_rejected(field, replacement, tmp_path):
    export_graph(lambda: TinyMlp(), (torch.randn(2, 4),), request(tmp_path / "source"))
    source = tmp_path / "source/exported_program.pt2"
    altered = tmp_path / "altered.pt2"
    with zipfile.ZipFile(source) as incoming, zipfile.ZipFile(altered, "w") as outgoing:
        for name in incoming.namelist():
            payload = incoming.read(name)
            if name.endswith("/models/model.json"):
                model = json.loads(payload)
                if field is None:
                    model = replacement
                else:
                    model[field] = replacement
                payload = json.dumps(model).encode()
            outgoing.writestr(name, payload)
    with pytest.raises(MeshIrError) as error:
        load_graph(altered, request(tmp_path / "loaded"))
    assert error.value.code == "E_EXPORT_VERSION"


def test_backend_imports_do_not_import_torch():
    code = (
        "import sys; "
        "import mesh_ir.model, mesh_ir.abi.encoder, mesh_ir.ir.graph_ir, "
        "mesh_ir.passes.canonicalize, mesh_ir.passes.shape_specialize; "
        "assert 'torch' not in sys.modules"
    )
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    subprocess.run([sys.executable, "-c", code], check=True, env=env)


def test_real_frontend_is_hashseed_deterministic(tmp_path):
    script = tmp_path / "run.py"
    script.write_text(
        "from pathlib import Path\n"
        "import torch\n"
        "from torch import nn\n"
        "from mesh_ir.frontend import FrontendRequest, export_graph\n"
        "class M(nn.Module):\n"
        " def __init__(self):\n"
        "  super().__init__(); torch.manual_seed(5); self.l=nn.Linear(4,3)\n"
        " def forward(self,x): return torch.relu(self.l(x))\n"
        f"r=export_graph(lambda:M(),(torch.ones(2,4),),FrontendRequest('forward','static','a'*64,Path('stage'),source_project_root=Path({str(ROOT / 'util/mesh_ir')!r})))\n"
        "print(r.graph.canonical_bytes().hex())\n"
    )
    outputs = []
    for seed in ("1", "77", "31337"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
        outputs.append(subprocess.check_output([sys.executable, str(script)], cwd=tmp_path / seed, env=env, text=True) if (tmp_path / seed).mkdir() is None else "")
    assert len(set(outputs)) == 1


def test_checked_in_examples_export_in_real_torch(tmp_path):
    mlp = export_graph(create_example_mlp, example_mlp_args(), request(tmp_path / "mlp"))
    transformer_request = FrontendRequest(
        "forward", "symbolic", "a" * 64, tmp_path / "transformer",
        shape_profiles=(("b2s8", (("s1", 2), ("s2", 8))),),
        source_project_root=ROOT / "util/mesh_ir",
    )
    transformer = export_graph(create_example_transformer, example_transformer_args(), transformer_request, dynamic_shapes=example_transformer_dynamic_shapes())
    assert mlp.graph.functions[0].ops
    assert transformer.graph.functions[0].ops
    assert len(transformer.variants) == 1


def test_pass_input_identity_changes_with_exported_semantics(tmp_path):
    class Add(nn.Module):
        def __init__(self, amount):
            super().__init__()
            self.amount = amount

        def forward(self, x):
            return x + self.amount

    first = export_graph(lambda: Add(1), (torch.ones(2),), request(tmp_path / "one"))
    second = export_graph(lambda: Add(2), (torch.ones(2),), request(tmp_path / "two"))
    repeated = export_graph(lambda: Add(1), (torch.ones(2),), request(tmp_path / "repeated"))
    assert first.passes[0].output_hash != second.passes[0].output_hash
    assert first.passes[1].output_hash != second.passes[1].output_hash
    assert first.graph.semantic_sha256 != second.graph.semantic_sha256
    assert first.passes[0].output_hash == repeated.passes[0].output_hash
    assert first.passes[1].output_hash == repeated.passes[1].output_hash
    assert first.passes[0].output_hash == first.passes[1].input_hash
    assert first.passes[1].output_hash == first.passes[2].input_hash == first.passes[2].output_hash
    assert first.passes[2].output_hash == first.passes[3].input_hash
    assert first.passes[3].output_hash == first.passes[4].input_hash
