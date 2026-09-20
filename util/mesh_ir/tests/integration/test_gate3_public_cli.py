import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from mesh_ir.abi.decoder import decode_program
from mesh_ir.architecture import load_arch
from mesh_ir.passes.execution import PASS_REGISTRY
from mesh_ir.scheduled.verify import verify_program


ROOT = Path(__file__).resolve().parents[4]
PACKAGE = ROOT / "util/mesh_ir"
ARCH = ROOT / "configs/example/ai_mesh/arch/mesh_2x2.yaml"
EXAMPLES = PACKAGE / "examples"
COMPILER_FILES = {
    "checksums.sha256",
    "compile_report.json",
    "decomposition_manifest.json",
    "diagnostics.jsonl",
    "effective_config.json",
    "expected_traffic.json",
    "graph.mesh.json",
    "kernel.mesh.json",
    "manifest.json",
    "memory_map.json",
    "program.mshb",
    "schedule.mesh.json",
}


def invoke(module, *arguments, pythonpath=()):
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(map(str, (PACKAGE, *pythonpath)))
    environment["PYTHONHASHSEED"] = "91"
    return subprocess.run(
        [sys.executable, "-m", module, *map(str, arguments)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=600,
    )


def audit_bundle(output, export_mode):
    expected = COMPILER_FILES | ({"exported_program.pt2"} if export_mode else set())
    assert {item.name for item in output.iterdir()} == expected
    checksums = {}
    for row in (output / "checksums.sha256").read_text().splitlines():
        digest, name = row.split("  ")
        assert name not in checksums
        assert digest == hashlib.sha256((output / name).read_bytes()).hexdigest()
        checksums[name] = digest
    assert tuple(checksums) == tuple(sorted(expected - {"checksums.sha256"}))
    manifest = json.loads((output / "manifest.json").read_bytes())
    assert manifest["status"] == "ok"
    assert manifest["mshb_sha256"] == checksums["program.mshb"]
    program = decode_program((output / "program.mshb").read_bytes())
    verify_program(program, load_arch(ARCH))
    assert (output / "schedule.mesh.json").read_bytes() == program.canonical_bytes()
    assert json.loads((output / "expected_traffic.json").read_bytes()) == program.semantics.intrinsic_traffic.canonical_dict()
    report = json.loads((output / "compile_report.json").read_bytes())
    passes = report["passes"]
    assert tuple(item["name"] for item in passes) == tuple(item.name for item in PASS_REGISTRY)
    assert all(left["output_hash"] == right["input_hash"] for left, right in zip(passes, passes[1:]))
    assert passes[-1]["input_hash"] == program.semantic_sha256
    assert passes[-1]["output_hash"] == checksums["program.mshb"]
    return manifest


def test_public_export_and_saved_load_publish_the_same_tiny_mlp_program(tmp_path):
    inputs = EXAMPLES / "tiny_mlp_inputs.json"
    config = EXAMPLES / "tiny_mlp_compile.yaml"
    exported = tmp_path / "exported"
    result = invoke(
        "mesh_ir.export_and_compile",
        "--module", "examples.tiny_mlp:create_model",
        "--inputs", inputs,
        "--arch", ARCH,
        "--config", config,
        "--output", exported,
        "--workers", "1",
        "--entrypoint", "forward",
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert json.loads(result.stdout)["status"] == "ok"
    audit_bundle(exported, True)

    saved = tmp_path / "input.pt2"
    shutil.copyfile(exported / "exported_program.pt2", saved)
    loaded = tmp_path / "loaded"
    result = invoke(
        "mesh_ir.compile",
        "--exported-program", saved,
        "--arch", ARCH,
        "--config", config,
        "--output", loaded,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    audit_bundle(loaded, False)
    assert (exported / "program.mshb").read_bytes() == (loaded / "program.mshb").read_bytes()


def test_public_transformer_export_retains_two_dynamic_profiles(tmp_path):
    output = tmp_path / "transformer"
    result = invoke(
        "mesh_ir.export_and_compile",
        "--module", "examples.tiny_transformer:create_model",
        "--inputs", EXAMPLES / "tiny_transformer_inputs.json",
        "--dynamic-shapes", EXAMPLES / "tiny_transformer_shapes.json",
        "--arch", ARCH,
        "--config", EXAMPLES / "tiny_transformer_compile.yaml",
        "--output", output,
        "--gemm-m", "4",
        "--gemm-n", "8",
        "--gemm-k", "4",
        "--chunk-bytes", "32",
        "--tensor-parallel", "1",
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    manifest = audit_bundle(output, True)
    assert [(item["entrypoint"], item["profile"]) for item in manifest["variants"]] == [
        ("forward", "b2s8"),
        ("forward", "b4s8"),
    ]
    report = json.loads((output / "compile_report.json").read_bytes())
    assert {item["kind"] for item in report["input_documents"]} == {"inputs", "dynamic_shapes"}


@pytest.mark.parametrize("module,arguments", (
    ("mesh_ir.compile", ("--module", "examples.tiny_mlp:create_model")),
    ("mesh_ir.export_and_compile", ("--exported-program", "input.pt2")),
    ("mesh_ir.compile", ("--exported-program", "input.pt2", "--workers", "0")),
))
def test_public_commands_reject_wrong_family_and_invalid_workers(module, arguments, tmp_path):
    result = invoke(
        module,
        *arguments,
        "--arch", ARCH,
        "--config", EXAMPLES / "tiny_mlp_compile.yaml",
        "--output", tmp_path / "output",
    )
    assert result.returncode == 2
    assert json.loads(result.stderr)["code"] == "E_CONFIG"
    assert not (tmp_path / "output").exists()


def test_public_export_refuses_existing_output_and_protected_input_alias(tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "user-data"
    marker.write_bytes(b"preserve")
    config = EXAMPLES / "tiny_mlp_compile.yaml"
    original_config = config.read_bytes()

    for target in (output, config):
        result = invoke(
            "mesh_ir.export_and_compile",
            "--module", "examples.tiny_mlp:create_model",
            "--inputs", EXAMPLES / "tiny_mlp_inputs.json",
            "--arch", ARCH,
            "--config", config,
            "--output", target,
        )
        assert result.returncode == 1
        assert json.loads(result.stderr)["code"] == "E_CONFIG"

    assert marker.read_bytes() == b"preserve"
    assert config.read_bytes() == original_config
    assert {item.name for item in output.iterdir()} == {"user-data"}


def test_public_saved_load_rejects_invalid_archive_with_failure_only_evidence(tmp_path):
    source = tmp_path / "invalid.pt2"
    source.write_bytes(b"not a Torch export")
    output = tmp_path / "failure"
    result = invoke(
        "mesh_ir.compile",
        "--exported-program", source,
        "--arch", ARCH,
        "--config", EXAMPLES / "tiny_mlp_compile.yaml",
        "--output", output,
    )
    assert result.returncode == 1
    assert json.loads(result.stderr)["code"] == "E_EXPORT_VERSION"
    assert source.read_bytes() == b"not a Torch export"
    assert not output.exists()
    failed = output.with_name(f"{output.name}.failed")
    assert {item.name for item in failed.iterdir()} == {"diagnostics.jsonl", "manifest.json"}
    assert not tuple(failed.rglob("program.mshb"))


def test_single_source_command_rejects_multi_entrypoint_configuration(tmp_path):
    source = tmp_path / "input.pt2"
    source.write_bytes(b"protected")
    config = tmp_path / "multi.yaml"
    config.write_text(
        "schema_version: mesh-compile-v1\n"
        "entrypoints: [first, second]\n"
        "shape_profiles:\n"
        "  first: [{profile_id: first_static}]\n"
        "  second: [{profile_id: second_static}]\n"
        "symbol_bindings: {}\n"
        "parallelism: {tensor_parallel: 1, pipeline_parallel: 1, expert_parallel: 1, data_parallel: 1}\n"
        "placement: {core_order: row_major_yx, allowed_cores: [7], reserve_cores: []}\n"
        "tiling: {gemm_m: 3, gemm_n: 5, gemm_k: 7, double_buffer: true}\n"
        "collectives: {all_reduce_algorithm: tree, chunk_bytes: 28}\n"
        "runtime_model: {mode: FULL_TIMING, tensor_data: DIGEST_ONLY}\n"
    )
    output = tmp_path / "failure"
    result = invoke(
        "mesh_ir.compile",
        "--exported-program", source,
        "--arch", ARCH,
        "--config", config,
        "--output", output,
    )
    assert result.returncode == 1
    assert json.loads(result.stderr)["code"] == "E_CONFIG"
    assert not output.exists()
    assert json.loads((tmp_path / "failure.failed/manifest.json").read_bytes())["status"] == "failed"


def test_real_unsupported_op_publishes_only_failure_evidence(tmp_path):
    source = tmp_path / "unsupported_factory.py"
    source.write_text(
        "import torch\n"
        "class Model(torch.nn.Module):\n"
        "    def forward(self, value):\n"
        "        return torch.sin(value)\n"
        "def create_model():\n"
        "    return Model()\n"
    )
    output = tmp_path / "failure"
    result = invoke(
        "mesh_ir.export_and_compile",
        "--module", "unsupported_factory:create_model",
        "--inputs", EXAMPLES / "tiny_mlp_inputs.json",
        "--arch", ARCH,
        "--config", EXAMPLES / "tiny_mlp_compile.yaml",
        "--output", output,
        pythonpath=(tmp_path,),
    )
    assert result.returncode == 1
    assert json.loads(result.stderr)["code"] == "E_EXPORT_UNSUPPORTED_OP"
    assert not output.exists()
    failed = output.with_name(f"{output.name}.failed")
    assert {item.name for item in failed.iterdir()} == {"diagnostics.jsonl", "manifest.json"}
    assert json.loads((failed / "manifest.json").read_bytes())["status"] == "failed"
    assert not tuple(failed.rglob("program.mshb"))


def test_module_factory_runtime_failure_is_catalog_backed(tmp_path):
    source = tmp_path / "runtime_factory.py"
    source.write_text(
        "def create_model():\n"
        "    raise RuntimeError('factory failed')\n"
    )
    output = tmp_path / "failure"
    result = invoke(
        "mesh_ir.export_and_compile",
        "--module", "runtime_factory:create_model",
        "--inputs", EXAMPLES / "tiny_mlp_inputs.json",
        "--arch", ARCH,
        "--config", EXAMPLES / "tiny_mlp_compile.yaml",
        "--output", output,
        pythonpath=(tmp_path,),
    )
    assert result.returncode == 1
    assert json.loads(result.stderr)["code"] == "E_CONFIG"
    assert not output.exists()
    assert json.loads((tmp_path / "failure.failed/manifest.json").read_bytes())["status"] == "failed"
