from pathlib import Path

import pytest
import torch

from mesh_ir.compile_service import export_and_compile_factory
from mesh_ir.diagnostics import MeshIrError


ROOT = Path(__file__).resolve().parents[4]
PACKAGE = ROOT / "util/mesh_ir"
ARCH = ROOT / "configs/example/ai_mesh/arch/mesh_2x2.yaml"
EXAMPLES = PACKAGE / "examples"


def invoke(output):
    return export_and_compile_factory(
        "examples.tiny_mlp:create_model",
        EXAMPLES / "tiny_mlp_inputs.json",
        None,
        ARCH,
        EXAMPLES / "tiny_mlp_compile.yaml",
        output,
    )


def test_private_frontend_snapshot_write_failure_is_catalog_backed(tmp_path, monkeypatch):
    real_write = Path.write_bytes

    def fail_graph(path, data):
        if path.name == "graph_ir.json":
            raise OSError("injected private staging failure")
        return real_write(path, data)

    monkeypatch.setattr(Path, "write_bytes", fail_graph)
    output = tmp_path / "output"
    with pytest.raises(MeshIrError) as caught:
        invoke(output)

    assert caught.value.code == "E_CONFIG"
    assert not output.exists()
    assert (tmp_path / "output.failed/manifest.json").exists()


def test_export_archive_runtime_write_failure_is_catalog_backed(tmp_path, monkeypatch):
    def fail_save(*args, **kwargs):
        raise RuntimeError("injected archive failure")

    monkeypatch.setattr(torch.export, "save", fail_save)
    output = tmp_path / "output"
    with pytest.raises(MeshIrError) as caught:
        invoke(output)

    assert caught.value.code == "E_EXPORT_VERSION"
    assert not output.exists()
    assert (tmp_path / "output.failed/manifest.json").exists()
