import pytest

from mesh_ir.runtime_errors import ReconciliationError
from mesh_ir.runtime_peer_publish import verified_transfer_publishes


def test_empty_admitted_peer_set_requires_empty_observed_transfers():
    assert verified_transfer_publishes([{'core_id': 0, 'transfers': []}], {}) == {}
    with pytest.raises(ReconciliationError, match='without an admitted descriptor'):
        verified_transfer_publishes([{'core_id': 0, 'transfers': [{'transfer_id': 1}]}], {})
    with pytest.raises(ReconciliationError, match='differ from the admitted'):
        verified_transfer_publishes([{'core_id': 0, 'transfers': []}], {1: 1})
    with pytest.raises(ReconciliationError, match='must be a mapping'):
        verified_transfer_publishes([], [])
