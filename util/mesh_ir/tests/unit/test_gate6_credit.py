import pytest

from mesh_ir.runtime_reconciliation import ReconciliationError, verified_drain


def test_a_credit_counter_lie_cannot_borrow_true_flags():
    row = {"owner_kind": 0, "owner_id": 7, "port_id": 0, "link_id": 1,
           "vc": 0, "vnet": 0, "initial": 4, "depth": 4,
           "sent": 10, "returned": 9, "current": 4, "conserved": True, "restored": True}
    with pytest.raises(ReconciliationError, match="conserved|initial depth"):
        verified_drain({"quiescent": 1}, [row], {"instances_drained": 1}, [], [], [])


@pytest.mark.parametrize('endpoints, error', [
    ([dict(node=100, idle=True), dict(node=101, idle=False)], 'not idle'),
    ([dict(node=100, idle=True), dict(node=100, idle=True)], 'identity is repeated'),
    ([dict(node=100, idle=True), dict(node=101, idle=True)], None),
])
def test_drain_requires_every_distinct_memory_owner(endpoints, error):
    credit = dict(initial=4, depth=4, sent=10, returned=10, current=4,
                  conserved=True, restored=True)
    args = ({'quiescent': True}, [credit], {'instances_drained': 1}, [], [], endpoints)
    if error:
        with pytest.raises(ReconciliationError, match=error):
            verified_drain(*args)
    else:
        assert verified_drain(*args)['end_pending'] == 0
