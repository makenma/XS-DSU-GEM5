import json

import pytest

from mesh_ir import schema
from mesh_ir.diagnostics import MeshIrError


def test_identical_resource_content_is_checked_once_without_sharing_mutable_results(tmp_path, monkeypatch):
    path = tmp_path / 'cache_probe_v1.schema.json'
    path.write_text(json.dumps({'$schema': 'https://json-schema.org/draft/2020-12/schema',
                                'title': 'cache-isolation', 'type': 'object', 'properties': {'x': {'type': 'integer'}}}))
    monkeypatch.setattr(schema, 'files', lambda package: tmp_path)
    validator = schema.jsonschema.Draft202012Validator
    original = validator.check_schema
    calls = []
    def checked(document, *args, **kwargs):
        calls.append(document['title'])
        return original(document, *args, **kwargs)
    monkeypatch.setattr(validator, 'check_schema', checked)
    first = schema.load_schema(path.name)
    first['properties']['x']['type'] = 'string'
    second = schema.load_schema(path.name)
    assert second['properties']['x']['type'] == 'integer'
    assert calls == ['cache-isolation']


def test_changed_schema_content_cannot_reuse_a_checked_version(tmp_path, monkeypatch):
    path = tmp_path / 'cache_probe_v1.schema.json'
    monkeypatch.setattr(schema, 'files', lambda package: tmp_path)
    path.write_text('{"title":"cache-replacement","type":"object"}')
    assert schema.load_schema(path.name)['type'] == 'object'
    path.write_text('{"title":"cache-replacement","type":"not-a-type"}')
    with pytest.raises(MeshIrError, match='schema resource is unavailable or invalid'):
        schema.load_schema(path.name)
    path.unlink()
    with pytest.raises(MeshIrError, match='schema resource is unavailable or invalid'):
        schema.load_schema(path.name)
