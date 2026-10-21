import dataclasses
import hashlib
import json

import pytest

from mesh_ir.kv_oracle import KvOracle
from mesh_ir.kv_persistence import load_persistent_manifest
from mesh_ir.kv_types import KvCapacity, KvGeometry
from mesh_ir.model import MeshIrError


def test_manifest_reconstructs_the_independent_oracle_and_rejects_changes(tmp_path):
    geometry = KvGeometry(0x100000, 4096, 4096, 3, 16)
    capacity = KvCapacity(4, 4, 4, 4)
    oracle = KvOracle(geometry, capacity)
    identity = {key: bytes([index] * 32).hex()
                for index, key in enumerate(('architecture', 'program', 'weights', 'layout', 'plan'))}
    manifest = dict(schema='ai_mesh_kv_persistent_v1', identity=identity,
                    geometry=dataclasses.asdict(geometry), capacity=dataclasses.asdict(capacity),
                    state_scalars=oracle.state_scalars())
    document = dict(manifest=manifest, sha256=hashlib.sha256(
        json.dumps(manifest, separators=(',', ':')).encode()).hexdigest())
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(document))
    assert load_persistent_manifest(path, identity, document['sha256']).state_scalars() == oracle.state_scalars()
    with pytest.raises(MeshIrError, match='identity mismatch'):
        load_persistent_manifest(path, dict(identity, layout='ff' * 32), document['sha256'])
    manifest['state_scalars'][1] += 1
    path.write_text(json.dumps(document))
    with pytest.raises(MeshIrError, match='digest mismatch'):
        load_persistent_manifest(path, identity, document['sha256'])

    expected = document['sha256']
    document['sha256'] = hashlib.sha256(json.dumps(manifest, separators=(',', ':')).encode()).hexdigest()
    path.write_text(json.dumps(document))
    with pytest.raises(MeshIrError, match='digest mismatch'):
        load_persistent_manifest(path, identity, expected)
