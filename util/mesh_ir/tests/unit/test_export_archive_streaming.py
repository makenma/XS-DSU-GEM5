import hashlib
import zipfile
from pathlib import Path

import torch

from mesh_ir.canonical import semantic_sha256
from mesh_ir.torch_compat import _archive_content_digest, export_program, load_exported_program


def test_archive_identity_hashes_members_with_bounded_reads(tmp_path, monkeypatch):
    members = {'model/z': b'z' * (1 << 20), 'model/a': b'a' * 31}
    path = tmp_path / 'archive.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    expected = semantic_sha256([(name.partition('/')[2], hashlib.sha256(data).hexdigest())
                               for name, data in sorted(members.items())])
    original = zipfile.ZipExtFile.read

    def bounded_read(stream, size=-1):
        assert 0 <= size <= 1 << 18, 'archive identity materialized an entire member'
        return original(stream, size)

    monkeypatch.setattr(zipfile.ZipExtFile, 'read', bounded_read)
    assert _archive_content_digest(path) == expected


def test_public_export_and_load_hash_archive_without_materializing_it(tmp_path, monkeypatch):
    path = tmp_path / 'model.pt2'
    original = Path.read_bytes

    def bounded_read(file):
        assert file != path, 'archive identity materialized the complete export'
        return original(file)

    monkeypatch.setattr(Path, 'read_bytes', bounded_read)
    exported = export_program(lambda: torch.nn.Linear(3, 2), (torch.ones(1, 3),), path)
    loaded = load_exported_program(path)
    assert exported.archive_sha256 == loaded.archive_sha256 == hashlib.sha256(original(path)).hexdigest()
    assert exported.source_semantic_sha256 == loaded.source_semantic_sha256
