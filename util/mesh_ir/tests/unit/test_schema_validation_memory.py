import pytest

from mesh_ir import schema
from mesh_ir.diagnostics import MeshIrError
from tests.unit.test_gate2_kernel_ir import load_compute_store_kernel


@pytest.mark.parametrize('invalid', [False, True])
def test_schema_validation_without_defaults_avoids_materializing_all_leaf_paths(monkeypatch, invalid):
    document = load_compute_store_kernel().canonical_dict()
    if invalid:
        document['unexpected'] = 1

    def reject_leaf_path_materialization(*args, **kwargs):
        pytest.fail('Schema validation without defaults must not allocate a set of every leaf path')

    monkeypatch.setattr(schema, '_value_paths', reject_leaf_path_materialization)
    if invalid:
        with pytest.raises(MeshIrError, match='schema validation failed'):
            schema.validate_schema('mesh_kernel_v1.schema.json', document, 'Kernel IR')
    else:
        assert schema.validate_schema('mesh_kernel_v1.schema.json', document, 'Kernel IR') == ()
