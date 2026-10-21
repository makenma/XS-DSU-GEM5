import hashlib
import json
import shutil

import pytest

from mesh_ir.diagnostics import MeshIrError
from mesh_ir.publication import ArtifactTransaction, publish_compilation
from tests.integration.test_tensor_payload import payload_carrier


@pytest.fixture(scope='module')
def compiled_publication(payload_carrier, tmp_path_factory):
    _, _, arch, _, frontend, backend, effective, _ = payload_carrier
    output = tmp_path_factory.mktemp('compiler-publication') / 'compiled'
    with ArtifactTransaction(output, ()) as transaction:
        publish_compilation(transaction, (frontend,), backend, arch, effective)
    return output


def test_compilation_source_closes_real_export_program_and_payload(compiled_publication, payload_carrier):
    from mesh_ir.publication import CompiledPublication
    from mesh_ir.tensor_payload import TensorPayload
    manifest, program, arch, *_ = payload_carrier
    source = CompiledPublication.load(compiled_publication, arch)
    payload = TensorPayload.load(manifest)
    identity, = source.sources
    assert source.program == program
    assert identity.archive_sha256 == payload.export_sha256
    assert identity.archive_semantic_sha256 == payload.export_semantic_sha256
    assert source.manifest_bytes == (compiled_publication / 'manifest.json').read_bytes()
    assert source.report_bytes == (compiled_publication / 'compile_report.json').read_bytes()


def test_compilation_source_embedded_export_matches_capture(payload_carrier, tmp_path):
    from mesh_ir.publication import CompiledPublication
    manifest, _, arch, _, frontend, backend, effective, _ = payload_carrier
    archive = (manifest.parent.parent / 'model.pt2').read_bytes()
    with ArtifactTransaction(tmp_path / 'embedded', ()) as transaction:
        publish_compilation(transaction, (frontend,), backend, arch, effective, exported_program_bytes=archive)
    source = CompiledPublication.load(tmp_path / 'embedded', arch)
    assert source.sources[0].archive_sha256 == hashlib.sha256(archive).hexdigest()
    with pytest.raises(MeshIrError, match='embedded export'):
        with ArtifactTransaction(tmp_path / 'wrong', ()) as transaction:
            publish_compilation(transaction, (frontend,), backend, arch, effective, exported_program_bytes=b'wrong export')
    assert not (tmp_path / 'wrong').exists()


@pytest.mark.parametrize('mutation', ['changed_bytes', 'missing', 'duplicate', 'path', 'manifest_identity',
    'pass_chain', 'source_semantic', 'graph', 'kernel', 'schedule', 'checksums', 'manifest_boolean', 'embedded_export'])
def test_compilation_source_rejects_broken_identity(compiled_publication, payload_carrier, tmp_path, mutation):
    from mesh_ir.publication import CompiledPublication
    source = tmp_path / 'compiled'
    shutil.copytree(compiled_publication, source)
    manifest = json.loads((source / 'manifest.json').read_text())
    if mutation == 'changed_bytes':
        (source / 'program.mshb').write_bytes(b'not a Program')
    elif mutation == 'missing':
        manifest['artifacts'].pop()
    elif mutation == 'duplicate':
        manifest['artifacts'].append(manifest['artifacts'][0])
    elif mutation == 'path':
        manifest['artifacts'][0]['path'] = '../compile_report.json'
    elif mutation == 'manifest_identity':
        manifest['program_semantic_sha256'] = '0' * 64
    elif mutation == 'manifest_boolean':
        manifest['abi']['min_reader_minor'] = False
    elif mutation == 'embedded_export':
        data = b'not the captured export'
        (source / 'exported_program.pt2').write_bytes(data)
        manifest['artifacts'].append(dict(path='exported_program.pt2', bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
        manifest['artifacts'].sort(key=lambda row: row['path'])
    elif mutation == 'checksums':
        (source / 'checksums.sha256').write_text('')
    else:
        name = {'pass_chain': 'compile_report.json', 'source_semantic': 'compile_report.json',
            'graph': 'graph.mesh.json', 'kernel': 'kernel.mesh.json', 'schedule': 'schedule.mesh.json'}[mutation]
        document = json.loads((source / name).read_text())
        if mutation == 'pass_chain':
            document['passes'][7]['input_hash'] = '0' * 64
        elif mutation == 'source_semantic':
            document['sources'][0]['provenance']['archive_semantic_sha256'] = '0' * 64
        else:
            document['semantic_sha256'] = '0' * 64
        data = json.dumps(document).encode()
        (source / name).write_bytes(data)
        row, = [row for row in manifest['artifacts'] if row['path'] == name]
        row.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    (source / 'manifest.json').write_text(json.dumps(manifest))
    if mutation != 'checksums':
        rows = list(manifest['artifacts']) + [dict(path='manifest.json',
            sha256=hashlib.sha256((source / 'manifest.json').read_bytes()).hexdigest())]
        (source / 'checksums.sha256').write_text(''.join(f"{row['sha256']}  {row['path']}\n"
            for row in sorted(rows, key=lambda row: row['path'])))
    with pytest.raises(MeshIrError):
        CompiledPublication.load(source, payload_carrier[2])
