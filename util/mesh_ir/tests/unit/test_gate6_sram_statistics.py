import copy

import pytest

from mesh_ir.runtime_reconciliation import ReconciliationError
from mesh_ir.workload_statistics import verified_sram_traffic


@pytest.fixture
def core():
    return {'core_id': 7, 'sram_bank_count': 4, 'sram_read_bytes': 20,
            'sram_write_bytes': 18, 'sram_bank_conflicts': 0,
            'sram_service_cycles': 6,
            'sram_banks': [{'bank_id': i, 'read_bytes': read, 'write_bytes': write}
                           for i, (read, write) in enumerate(zip((2, 16, 2, 0), (1, 16, 1, 0)))]}


def test_bank_accounting_preserves_partial_lines_and_idle_banks(core):
    report = verified_sram_traffic([core], 4)
    assert report[0]['banks'] == core['sram_banks']
    assert report[0]['read_bytes'] == 20
    assert report[0]['write_bytes'] == 18


@pytest.mark.parametrize('mutation', ['missing_idle', 'duplicate', 'negative', 'total', 'count', 'boolean'])
def test_invalid_bank_facts_fail_closed(core, mutation):
    core = copy.deepcopy(core)
    if mutation == 'missing_idle':
        core['sram_banks'].pop()
    elif mutation == 'duplicate':
        core['sram_banks'][1] = copy.deepcopy(core['sram_banks'][0])
    elif mutation == 'negative':
        core['sram_banks'][0]['read_bytes'] = -1
    elif mutation == 'total':
        core['sram_read_bytes'] += 1
    elif mutation == 'count':
        core['sram_bank_count'] -= 1
    else:
        core['sram_banks'][0]['write_bytes'] = True
    with pytest.raises(ReconciliationError):
        verified_sram_traffic([core], 4)
