from __future__ import annotations

import argparse
import sys

from mesh_ir.canonical import canonical_json_bytes
from mesh_ir.compile_config import CompileOverrides
from mesh_ir.compile_service import compile_exported_program, export_and_compile_factory
from mesh_ir.diagnostics import MeshIrError


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise MeshIrError("E_CONFIG", "invalid command line", detail=message)


def _positive(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected a positive integer") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return parsed


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--arch", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--entrypoint")
    parser.add_argument("--workers", type=_positive, default=1)
    parser.add_argument("--gemm-m", type=_positive)
    parser.add_argument("--gemm-n", type=_positive)
    parser.add_argument("--gemm-k", type=_positive)
    parser.add_argument("--chunk-bytes", type=_positive)
    parser.add_argument("--tensor-parallel", type=_positive)


def _overrides(args) -> CompileOverrides:
    return CompileOverrides(args.gemm_m, args.gemm_n, args.gemm_k, args.chunk_bytes, args.tensor_parallel)


def _run(function, args) -> int:
    try:
        result = function(args)
    except MeshIrError as error:
        sys.stderr.write(error.to_jsonl() + "\n")
        return 1
    sys.stdout.buffer.write(canonical_json_bytes(result.canonical_dict()) + b"\n")
    return 0


def compile_main(argv=None) -> int:
    parser = _Parser(prog="python -m mesh_ir.compile")
    parser.add_argument("--exported-program", required=True)
    _common(parser)
    try:
        args = parser.parse_args(argv)
    except MeshIrError as error:
        sys.stderr.write(error.to_jsonl() + "\n")
        return 2
    return _run(
        lambda values: compile_exported_program(
            values.exported_program,
            values.arch,
            values.config,
            values.output,
            workers=values.workers,
            entrypoint=values.entrypoint,
            overrides=_overrides(values),
        ),
        args,
    )


def export_and_compile_main(argv=None) -> int:
    parser = _Parser(prog="python -m mesh_ir.export_and_compile")
    parser.add_argument("--module", required=True)
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--dynamic-shapes")
    _common(parser)
    try:
        args = parser.parse_args(argv)
    except MeshIrError as error:
        sys.stderr.write(error.to_jsonl() + "\n")
        return 2
    return _run(
        lambda values: export_and_compile_factory(
            values.module,
            values.inputs,
            values.dynamic_shapes,
            values.arch,
            values.config,
            values.output,
            workers=values.workers,
            entrypoint=values.entrypoint,
            overrides=_overrides(values),
        ),
        args,
    )


__all__ = ["compile_main", "export_and_compile_main"]
