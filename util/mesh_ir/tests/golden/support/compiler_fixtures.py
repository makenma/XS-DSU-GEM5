from mesh_ir.compile_config import load_compile_config_text, resolve_compile_config
from mesh_ir.compiler import compile_backend
from mesh_ir.ir.common import Const, DType, TensorRole, contiguous_strides
from mesh_ir.ir.graph_ir import (
    ElementwiseAttrs,
    GraphFunction,
    GraphModule,
    GraphOp,
    GraphValue,
    MatmulAttrs,
    NormAttrs,
    OpCode,
    PytreeSpec,
    SoftmaxAttrs,
    ViewAttrs,
)


SMALL_MESH_COMPILE = """
schema_version: mesh-compile-v1
entrypoints: [forward]
shape_profiles:
  forward:
    - {profile_id: p1}
symbol_bindings: {}
parallelism: {tensor_parallel: 1, pipeline_parallel: 1, expert_parallel: 1, data_parallel: 1}
placement: {core_order: row_major_yx, allowed_cores: [7, 2], reserve_cores: []}
tiling: {gemm_m: 2, gemm_n: 2, gemm_k: 2, double_buffer: true}
collectives: {all_reduce_algorithm: ring, chunk_bytes: 16}
runtime_model: {mode: FULL_TIMING, tensor_data: DIGEST_ONLY}
"""


def compile_graph(arch, graph):
    config = load_compile_config_text(
        SMALL_MESH_COMPILE.replace("[7, 2]", "[0, 1]"),
        arch,
    )
    effective = resolve_compile_config(config, arch)
    return compile_backend(
        (graph,),
        arch,
        effective,
        source_graphs=(graph,),
        workers=1,
    ).program


def compile_softmax_program(arch, leading_extent):
    shape = (Const(leading_extent), Const(4))
    values = (
        GraphValue(
            1,
            "x",
            TensorRole.INPUT,
            DType.FP16,
            shape,
            contiguous_strides(shape),
            0,
            1,
            content_sha256="d" * 64,
        ),
        GraphValue(
            2,
            "softmax",
            TensorRole.OUTPUT,
            DType.FP16,
            shape,
            contiguous_strides(shape),
            0,
            2,
        ),
    )
    operation = GraphOp(
        1,
        OpCode.SOFTMAX,
        (1,),
        (2,),
        SoftmaxAttrs(1, DType.FP16, True),
        "softmax:1",
    )
    function = GraphFunction(
        1,
        "forward",
        (1,),
        (operation,),
        (2,),
        PytreeSpec.leaf(),
        PytreeSpec.leaf(),
    )
    graph = GraphModule.create(
        arch.digest().hex(),
        "e" * 64,
        "forward",
        "p1",
        values,
        (function,),
    )
    return compile_graph(arch, graph)


def compile_matrix_program(arch):
    specs = (
        (1, "lhs", TensorRole.INPUT, (Const(2), Const(3)), None),
        (2, "rhs", TensorRole.WEIGHT, (Const(4), Const(3)), "f" * 64),
        (3, "result", TensorRole.OUTPUT, (Const(2), Const(4)), None),
    )
    values = tuple(
        GraphValue(
            index,
            name,
            role,
            DType.FP16,
            shape,
            contiguous_strides(shape),
            0,
            index,
            content_sha256=content,
        )
        for index, name, role, shape, content in specs
    )
    operation = GraphOp(
        1,
        OpCode.MATMUL,
        (1, 2),
        (3,),
        MatmulAttrs(rhs_transpose=True, alpha=0.5, accum_dtype=DType.FP32),
        "matmul:1",
    )
    function = GraphFunction(
        1,
        "forward",
        (1,),
        (operation,),
        (3,),
        PytreeSpec.leaf(),
        PytreeSpec.leaf(),
    )
    graph = GraphModule.create(
        arch.digest().hex(),
        "a" * 64,
        "forward",
        "p1",
        values,
        (function,),
    )
    return compile_graph(arch, graph)


def build_transposed_bmm_graph(arch):
    source_shape = (Const(2), Const(3), Const(4))
    transposed_shape = (Const(2), Const(4), Const(3))
    rhs_shape = (Const(2), Const(4), Const(5))
    output_shape = (Const(2), Const(3), Const(5))
    source = GraphValue(
        1,
        "source",
        TensorRole.INPUT,
        DType.FP32,
        source_shape,
        contiguous_strides(source_shape),
        0,
        1,
    )
    transposed = GraphValue(
        2,
        "transposed",
        TensorRole.ACTIVATION,
        DType.FP32,
        transposed_shape,
        (12, 1, 4),
        0,
        1,
    )
    rhs = GraphValue(
        3,
        "rhs",
        TensorRole.INPUT,
        DType.FP32,
        rhs_shape,
        contiguous_strides(rhs_shape),
        0,
        3,
    )
    output = GraphValue(
        4,
        "output",
        TensorRole.OUTPUT,
        DType.FP32,
        output_shape,
        contiguous_strides(output_shape),
        0,
        4,
    )
    transpose = GraphOp(
        1,
        OpCode.TRANSPOSE_VIEW,
        (1,),
        (2,),
        ViewAttrs(permutation=(0, 2, 1)),
        "transpose:1",
    )
    bmm = GraphOp(
        2,
        OpCode.BMM,
        (2, 3),
        (4,),
        MatmulAttrs(batch_axes=(0,), lhs_transpose=True),
        "bmm:2",
    )
    function = GraphFunction(
        1,
        "forward",
        (1, 3),
        (transpose, bmm),
        (4,),
        PytreeSpec.tuple((PytreeSpec.leaf(), PytreeSpec.leaf())),
        PytreeSpec.leaf(),
    )
    return GraphModule.create(
        arch.digest().hex(),
        "b" * 64,
        "forward",
        "p1",
        (source, transposed, rhs, output),
        (function,),
    )


def compile_transposed_bmm_program(arch):
    return compile_graph(arch, build_transposed_bmm_graph(arch))


def compile_unary_chain_program(arch):
    shape = (Const(2), Const(4))
    values = tuple(
        GraphValue(
            index,
            name,
            role,
            DType.FP16,
            shape,
            contiguous_strides(shape),
            0,
            index,
            content_sha256="c" * 64 if index == 1 else None,
        )
        for index, (name, role) in enumerate(
            (
                ("x", TensorRole.INPUT),
                ("softmax", TensorRole.ACTIVATION),
                ("norm", TensorRole.ACTIVATION),
                ("gelu", TensorRole.OUTPUT),
            ),
            1,
        )
    )
    operations = (
        GraphOp(
            1,
            OpCode.SOFTMAX,
            (1,),
            (2,),
            SoftmaxAttrs(1, DType.FP16, True),
            "softmax:1",
        ),
        GraphOp(
            2,
            OpCode.LAYERNORM,
            (2,),
            (3,),
            NormAttrs((1,), 1e-5, False, False),
            "norm:2",
        ),
        GraphOp(
            3,
            OpCode.GELU,
            (3,),
            (4,),
            ElementwiseAttrs(approximation="none"),
            "gelu:3",
        ),
    )
    function = GraphFunction(
        1,
        "forward",
        (1,),
        operations,
        (4,),
        PytreeSpec.leaf(),
        PytreeSpec.leaf(),
    )
    graph = GraphModule.create(
        arch.digest().hex(),
        "b" * 64,
        "forward",
        "p1",
        values,
        (function,),
    )
    return compile_graph(arch, graph)
