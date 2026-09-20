import hashlib
import json
from dataclasses import replace

import pytest

from mesh_ir.compiler import compile_backend
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.passes.execution import PASS_REGISTRY
from mesh_ir.publication import ArtifactTransaction, publish_compilation
from tests.integration.backend_compiler_fixtures import effective_config, real_backend_inputs


@pytest.fixture(scope="module")
def compiled_bundle(real_backend_inputs):
    effective = effective_config(real_backend_inputs, 1)
    backend = compile_backend(
        real_backend_inputs.variants,
        real_backend_inputs.arch,
        effective,
        source_graphs=real_backend_inputs.source_graphs,
        workers=2,
    )
    return effective, backend


def test_multi_entrypoint_compiler_publication_retains_all_members_and_p1_through_p22(
    real_backend_inputs,
    compiled_bundle,
    tmp_path,
):
    effective, backend = compiled_bundle
    output = tmp_path / "bundle"
    with ArtifactTransaction(output, ()) as transaction:
        result = publish_compilation(
            transaction,
            real_backend_inputs.frontends,
            backend,
            real_backend_inputs.arch,
            effective,
        )

    assert len(result.artifacts) == 12
    report = json.loads((output / "compile_report.json").read_bytes())
    assert tuple(row["name"] for row in report["passes"]) == tuple(item.name for item in PASS_REGISTRY)
    assert tuple(row["entrypoint"] for row in report["member_frontend_passes"]) == ("mlp", "transformer")
    assert tuple(len(row["passes"]) for row in report["member_frontend_passes"]) == (5, 5)
    assert all(left["output_hash"] == right["input_hash"] for left, right in zip(report["passes"], report["passes"][1:]))
    assert report["passes"][-1]["output_hash"] == hashlib.sha256((output / "program.mshb").read_bytes()).hexdigest()
    assert dict(report["passes"][-1]["statistics"]) == {
        "artifact_count": 12,
        "image_bytes": len((output / "program.mshb").read_bytes()),
    }
    manifest = json.loads((output / "manifest.json").read_bytes())
    assert [(row["entrypoint"], row["profile"]) for row in manifest["variants"]] == [
        (item.entrypoint, item.profile_id)
        for item in backend.variants
    ]


def test_compiler_publication_rejects_reordered_sources_without_success_artifacts(
    real_backend_inputs,
    compiled_bundle,
    tmp_path,
):
    effective, backend = compiled_bundle
    output = tmp_path / "reordered"
    with pytest.raises(MeshIrError):
        with ArtifactTransaction(output, ()) as transaction:
            publish_compilation(
                transaction,
                tuple(reversed(real_backend_inputs.frontends)),
                backend,
                real_backend_inputs.arch,
                effective,
            )

    assert not output.exists()
    assert json.loads((tmp_path / "reordered.failed/manifest.json").read_bytes())["status"] == "failed"
    assert not (tmp_path / "reordered.failed" / "program.mshb").exists()


def test_compiler_publication_rejects_changed_frontend_pass_chain(
    real_backend_inputs,
    compiled_bundle,
    tmp_path,
):
    effective, backend = compiled_bundle
    first = real_backend_inputs.frontends[0]
    changed = replace(first, passes=first.passes[:-1])
    output = tmp_path / "changed"
    with pytest.raises(MeshIrError):
        with ArtifactTransaction(output, ()) as transaction:
            publish_compilation(
                transaction,
                (changed, *real_backend_inputs.frontends[1:]),
                backend,
                real_backend_inputs.arch,
                effective,
            )

    assert not output.exists()
    assert json.loads((tmp_path / "changed.failed/manifest.json").read_bytes())["status"] == "failed"
    assert not (tmp_path / "changed.failed" / "program.mshb").exists()


def test_compiler_publication_rejects_changed_frontend_provenance(
    real_backend_inputs,
    compiled_bundle,
    tmp_path,
):
    effective, backend = compiled_bundle
    first = real_backend_inputs.frontends[0]
    changed = replace(
        first,
        provenance=replace(first.provenance, archive_semantic_sha256="0" * 64),
    )
    output = tmp_path / "changed-provenance"
    with pytest.raises(MeshIrError):
        with ArtifactTransaction(output, ()) as transaction:
            publish_compilation(
                transaction,
                (changed, *real_backend_inputs.frontends[1:]),
                backend,
                real_backend_inputs.arch,
                effective,
            )

    assert not output.exists()
    assert json.loads((tmp_path / "changed-provenance.failed/manifest.json").read_bytes())["status"] == "failed"
    assert not (tmp_path / "changed-provenance.failed" / "program.mshb").exists()
