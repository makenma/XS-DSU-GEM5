from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

import jsonschema

from mesh_ir.generated import abi as A
from mesh_ir.kv_oracle import KvOracle
from mesh_ir.kv_types import (
    SCHEMA_VERSION, KvCapacity, KvErrorCandidate, KvGeometry, KvPersistentState,
    KvRecord, KvState, kv_contract_digest,
)
from mesh_ir.model import MeshIrError
from mesh_ir.moe_weight_cache import ErrorSourceKey


def load_persistent_manifest(path, expected_identity, expected_digest):
    document = json.loads(Path(path).read_text())
    schema = Path(__file__).resolve().parents[3] / 'schemas/ai_mesh/kv_persistent_v1.schema.json'
    jsonschema.validate(document, json.loads(schema.read_text()))
    manifest = document['manifest']
    payload = json.dumps(manifest, separators=(',', ':')).encode()
    if document['sha256'] != expected_digest or hashlib.sha256(payload).hexdigest() != document['sha256']:
        raise MeshIrError('E_KV_STATE', 'persistent manifest digest mismatch')
    if manifest['identity'] != expected_identity:
        raise MeshIrError('E_KV_STATE', 'persistent manifest identity mismatch')
    geometry = KvGeometry(**manifest['geometry'])
    oracle = KvOracle(geometry, KvCapacity(**manifest['capacity']))
    values = iter(manifest['state_scalars'])
    def take(count=1):
        try:
            return tuple(next(values) for _ in range(count))
        except StopIteration as error:
            raise MeshIrError('E_KV_STATE', 'truncated persistent state') from error
    version, epoch, rollback, words = take(4)
    if version != SCHEMA_VERSION or rollback != 1 or words != (geometry.max_sessions + 63) // 64:
        raise MeshIrError('E_KV_STATE', 'persistent scalar header mismatch')
    take(words)
    owners = [None] * geometry.max_sessions
    records = []
    contract = kv_contract_digest(*(bytes.fromhex(expected_identity[key])
        for key in ('program', 'weights', 'layout')), geometry.bytes_per_token)
    for _ in range(take()[0]):
        row = take(37)
        digest = lambda start: struct.pack('<4Q', *row[start:start + 4])
        error = None
        if row[29]:
            source = A.ERROR_SOURCE_KEYS_FORMAT.unpack(struct.pack('<5Q', *row[32:37]))
            if source[4]:
                raise MeshIrError('E_KV_STATE', 'persistent error source reserved bits')
            error = KvErrorCandidate(row[30], ErrorSourceKey(*source[:4], *source[5:]), row[31])
        record = KvRecord(row[0], row[1], row[2], digest(17), KvState(row[3]),
            row[5] if row[4] else None, row[6], row[7], digest(21) if row[13] else None,
            row[8], row[9], row[10], row[11], row[12], error,
            row[14], row[15], digest(25) if row[16] else None)
        if record.contract_digest != contract:
            raise MeshIrError('E_KV_STATE', 'persistent record contract mismatch')
        if record.slot_id is not None:
            if record.slot_id >= len(owners) or owners[record.slot_id] is not None:
                raise MeshIrError('E_KV_STATE', 'persistent slot ownership mismatch')
            owners[record.slot_id] = record.tuple
        records.append(record)
    if take(5) != (0, 0, 0, 0, 0):
        raise MeshIrError('E_KV_STATE', 'persistent state contains live owners')
    tombstones = []
    for _ in range(take()[0]):
        session, handle, generation = take(3)
        tombstones.append(((session, handle), generation))
    if tuple(values):
        raise MeshIrError('E_KV_STATE', 'persistent state has trailing scalars')
    oracle.load_persistent(KvPersistentState(tuple(owners), tuple(records), tuple(tombstones), epoch))
    if oracle.state_scalars() != manifest['state_scalars']:
        raise MeshIrError('E_KV_STATE', 'persistent scalar state is not canonical')
    return oracle
