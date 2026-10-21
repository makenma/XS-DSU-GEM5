import hashlib
import json
from pathlib import Path

import pytest

import mesh_ir.publication as publication
from mesh_ir.architecture import load_arch
from mesh_ir.diagnostics import MeshIrError
from mesh_ir.golden_programs import build_single_core_program
from mesh_ir.publication import Artifact, ArtifactPlan, ArtifactTransaction, PublicationManifest, publish_authored_program


ROOT = Path(__file__).resolve().parents[4]
ARCH_PATH = ROOT / "configs/example/ai_mesh/arch/mesh_1x2.yaml"


def _checksums(path):
    return {
        name: digest
        for digest, name in (
            line.split("  ", 1)
            for line in (path / "checksums.sha256").read_text(encoding="utf-8").splitlines()
        )
    }


def _plan(blob=b"program"):
    digest = "0" * 64
    return ArtifactPlan(
        (Artifact("program.mshb", blob),),
        PublicationManifest(
            kind="test",
            abi_major=1,
            abi_minor=3,
            min_reader_minor=3,
            required_features=1,
            abi_schema_sha256=digest,
            variants=(),
            arch_digest=digest,
            program_semantic_sha256=digest,
            mshb_sha256=hashlib.sha256(blob).hexdigest(),
            mshb_bytes=len(blob),
        ),
        frozenset(("program.mshb",)),
    )


def test_authored_publication_uses_one_complete_atomic_artifact_set(tmp_path):
    arch = load_arch(ARCH_PATH)
    output = tmp_path / "program"

    result = publish_authored_program(
        output,
        build_single_core_program(arch),
        arch,
        kind="golden",
        identity=(("program", "single"),),
    )

    expected = {
        "program.mshb",
        "schedule.mesh.json",
        "expected_traffic.json",
        "diagnostics.jsonl",
        "manifest.json",
        "checksums.sha256",
    }
    assert {item.name for item in output.iterdir()} == expected
    assert result.output == output.resolve()
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "ok"
    assert manifest["kind"] == "golden"
    assert manifest["program_semantic_sha256"] == build_single_core_program(arch).semantic_sha256
    assert json.loads((output / "expected_traffic.json").read_text()) == build_single_core_program(arch).semantics.intrinsic_traffic.canonical_dict()
    checksums = _checksums(output)
    assert set(checksums) == expected - {"checksums.sha256"}
    for name, digest in checksums.items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == digest


def test_transaction_rejects_output_alias_existing_output_and_preserves_inputs(tmp_path):
    protected = tmp_path / "input.pt2"
    protected.write_bytes(b"source")
    nested_output = tmp_path / "nested"
    nested_output.mkdir()
    nested_input = nested_output / "config.yaml"
    nested_input.write_bytes(b"config")

    with pytest.raises(MeshIrError):
        ArtifactTransaction(protected, (protected,))
    with pytest.raises(MeshIrError):
        ArtifactTransaction(nested_output, (nested_input,))
    dangling = tmp_path / "dangling"
    dangling.symlink_to(tmp_path / "missing")
    with pytest.raises(MeshIrError):
        ArtifactTransaction(dangling, ())
    assert protected.read_bytes() == b"source"
    assert nested_input.read_bytes() == b"config"
    assert dangling.is_symlink()


def test_existing_success_is_never_replaced(tmp_path):
    output = tmp_path / "published"
    output.mkdir()
    marker = output / "marker"
    marker.write_bytes(b"original")

    with pytest.raises(MeshIrError):
        ArtifactTransaction(output, ())

    assert marker.read_bytes() == b"original"


def test_unsupported_output_filesystem_is_rejected_before_compilation(tmp_path, monkeypatch):
    previous = tmp_path / "output.failed"
    previous.mkdir()
    marker = previous / "manifest.json"
    marker.write_bytes(b"preserve")

    def unsupported(source, destination):
        raise OSError(22, "Invalid argument", str(destination))

    monkeypatch.setattr(publication, "_rename_noreplace", unsupported)

    with pytest.raises(MeshIrError) as caught:
        ArtifactTransaction(tmp_path / "output", ())

    assert caught.value.code == "E_CONFIG"
    assert "filesystem" in caught.value.message
    assert marker.read_bytes() == b"preserve"
    assert {item.name for item in tmp_path.iterdir()} == {"output.failed"}


def test_transaction_rejects_invalid_typed_manifest_before_writing(tmp_path):
    digest = "0" * 64
    plan = ArtifactPlan(
        (Artifact("payload", b"value"),),
        PublicationManifest("", 1, 3, 3, 1, digest, (), digest, digest, digest, 5),
        frozenset(("payload",)),
    )

    with ArtifactTransaction(tmp_path / "output", ()) as transaction:
        with pytest.raises(MeshIrError):
            transaction.publish(plan)

        assert not tuple(transaction.working_directory.parent.joinpath("publication").glob("*"))


@pytest.mark.parametrize("name", ("./payload", "payload/", "bad\nname", "bad\\name", "bad\0name"))
def test_transaction_rejects_noncanonical_artifact_filename(tmp_path, name):
    plan = _plan()
    changed = ArtifactPlan((Artifact(name, b"program"),), plan.manifest, frozenset((name,)))

    with ArtifactTransaction(tmp_path / "output", ()) as transaction:
        with pytest.raises(MeshIrError):
            transaction.publish(changed)
        assert not transaction.working_directory.parent.joinpath("publication").exists()

    assert not (tmp_path / "output").exists()


def test_transaction_binds_manifest_to_program_image(tmp_path):
    plan = _plan()
    changed = ArtifactPlan(
        plan.artifacts,
        PublicationManifest(**{**plan.manifest.__dict__, "mshb_bytes": 999}),
        plan.required_names,
    )

    with pytest.raises(MeshIrError):
        with ArtifactTransaction(tmp_path / "output", ()) as transaction:
            transaction.publish(changed)

    assert not (tmp_path / "output").exists()


def test_transaction_validates_written_checksum_file(tmp_path, monkeypatch):
    real_write = ArtifactTransaction._write

    def corrupt_checksum(path, data):
        real_write(path, b"corrupt\n" if path.name == "checksums.sha256" else data)

    monkeypatch.setattr(ArtifactTransaction, "_write", staticmethod(corrupt_checksum))

    with pytest.raises(MeshIrError):
        with ArtifactTransaction(tmp_path / "output", ()) as transaction:
            transaction.publish(_plan())

    assert not (tmp_path / "output").exists()


def test_staged_artifact_read_failure_is_catalog_backed(tmp_path, monkeypatch):
    real_read = Path.read_bytes

    def fail_program(path):
        if path.name == "program.mshb" and path.parent.name == "publication":
            raise OSError("injected staged read failure")
        return real_read(path)

    monkeypatch.setattr(Path, "read_bytes", fail_program)

    with pytest.raises(MeshIrError) as caught:
        with ArtifactTransaction(tmp_path / "output", ()) as transaction:
            transaction.publish(_plan())

    assert caught.value.code == "E_CONFIG"
    assert not (tmp_path / "output").exists()


def test_successful_retry_preserves_prior_failure_evidence(tmp_path):
    prior = tmp_path / "output.failed"
    prior.mkdir()
    marker = prior / "diagnostics.jsonl"
    marker.write_bytes(b"prior failure")

    with ArtifactTransaction(tmp_path / "output", ()) as transaction:
        transaction.publish(_plan())

    assert (tmp_path / "output/program.mshb").read_bytes() == b"program"
    assert marker.read_bytes() == b"prior failure"


def test_transaction_context_publishes_failure_once_and_preserves_original(tmp_path, monkeypatch):
    calls = []
    real_publish_failure = ArtifactTransaction.publish_failure

    def observe(transaction, error):
        calls.append(error)
        return real_publish_failure(transaction, error)

    monkeypatch.setattr(ArtifactTransaction, "publish_failure", observe)
    original = MeshIrError("E_CONFIG", "original")

    with pytest.raises(MeshIrError) as caught:
        with ArtifactTransaction(tmp_path / "output", ()):
            raise original

    assert caught.value is original
    assert calls == [original]
    assert json.loads((tmp_path / "output.failed/manifest.json").read_bytes())["status"] == "failed"


def test_existing_failure_evidence_does_not_replace_original_failure(tmp_path):
    prior = tmp_path / "output.failed"
    prior.mkdir()
    marker = prior / "manifest.json"
    marker.write_bytes(b"prior")
    original = MeshIrError("E_CONFIG", "original")

    with pytest.raises(MeshIrError) as caught:
        with ArtifactTransaction(tmp_path / "output", ()):
            raise original

    assert caught.value is original
    assert marker.read_bytes() == b"prior"


def test_competing_output_is_never_replaced(tmp_path, monkeypatch):
    real_rename = publication._rename_noreplace
    output = tmp_path / "output"

    def compete(source, destination):
        if Path(destination) == output:
            output.mkdir()
            (output / "user-data").write_bytes(b"preserve")
        return real_rename(source, destination)

    monkeypatch.setattr(publication, "_rename_noreplace", compete)

    with pytest.raises(MeshIrError):
        with ArtifactTransaction(output, ()) as transaction:
            transaction.publish(_plan())

    assert (output / "user-data").read_bytes() == b"preserve"
    assert not (output / "program.mshb").exists()


def test_parent_sync_failure_happens_before_visible_commit(tmp_path, monkeypatch):
    real_sync = ArtifactTransaction._sync_directory

    def fail_parent(path):
        if Path(path) == tmp_path:
            raise MeshIrError("E_CONFIG", "injected parent sync failure")
        return real_sync(path)

    monkeypatch.setattr(ArtifactTransaction, "_sync_directory", staticmethod(fail_parent))

    with pytest.raises(MeshIrError):
        with ArtifactTransaction(tmp_path / "output", ()) as transaction:
            transaction.publish(_plan())

    assert not (tmp_path / "output").exists()


def test_real_rename_failure_leaves_no_success_and_preserves_original_error(tmp_path, monkeypatch):
    arch = load_arch(ARCH_PATH)
    output = tmp_path / "program"
    real_rename = publication._rename_noreplace

    def fail_final(source, destination):
        if Path(destination) == output:
            raise OSError("injected final rename failure")
        return real_rename(source, destination)

    monkeypatch.setattr(publication, "_rename_noreplace", fail_final)

    with pytest.raises(MeshIrError) as caught:
        publish_authored_program(
            output,
            build_single_core_program(arch),
            arch,
            kind="golden",
            identity=(("program", "single"),),
        )

    assert caught.value.context["detail"] == "injected final rename failure"
    assert not output.exists()
    assert not (output.parent / f"{output.name}.failed" / "program.mshb").exists()


def test_protected_input_change_aborts_final_commit(tmp_path):
    protected = tmp_path / "config.yaml"
    protected.write_bytes(b"before")
    output = tmp_path / "program"

    with ArtifactTransaction(output, (protected,)) as transaction:
        protected.write_bytes(b"after")
        with pytest.raises(MeshIrError):
            transaction.publish_failure(MeshIrError("E_CONFIG", "compile failed"))

    assert protected.read_bytes() == b"after"
    assert not output.exists()
    assert not (tmp_path / "program.failed").exists()
