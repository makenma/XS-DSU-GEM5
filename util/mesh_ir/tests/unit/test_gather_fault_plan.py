import json

import pytest

from mesh_ir.fault_plan import execution_keys, resolve_fault_plan
from mesh_ir.runtime_errors import ReconciliationError


@pytest.fixture
def invocation(tmp_path):
    (tmp_path / 'schedule.mesh.json').write_text(json.dumps({'sections': {
        'COMMANDS': [{'core_id': 3, 'command_id': 1}, {'core_id': 3, 'command_id': 2}],
        'DMA_DESCRIPTORS': [{'command_id': 1, 'descriptor_id': 1, 'kind': 4},
                            {'command_id': 2, 'descriptor_id': 2, 'kind': 6}]}}))
    arch = dict(core_ids=[3], src_nodes={3: 8}, axi_data_bytes=32, axi_max_burst_beats=16)
    rows = {1: dict(kind=4, execution_count=1, rows=1, row_bytes=12,
                    remote_address=8192, remote_stride_bytes=12, max_burst_beats=16),
            2: dict(kind=6, execution_count=1, rows=3, row_bytes=24,
                    remote_address=4080, remote_stride_bytes=24, max_burst_beats=16,
                    table_rows=32, index_values=[0, 2, 0])}
    return tmp_path, arch, rows


def test_fault_uids_include_indexed_occurrences_and_page_splits(invocation):
    directory, arch, rows = invocation
    facts = execution_keys(directory, arch, arch['src_nodes'], expected_rows=rows)[3]
    assert [row['useful_bytes'] for row in facts] == [12, 16, 8, 24, 16, 8]
    assert [row['address'] for row in facts] == [8192, 4064, 4096, 4128, 4064, 4096]
    assert [row['uid'] for row in facts] == [(8 << 48) | (1 << 39) | i for i in range(6)]
    assert [row['burst_index'] for row in facts[1:]] == list(range(5))
    target = facts[3]
    descriptors, occurrence, models = resolve_fault_plan(directory, arch,
        [dict(uid=target['uid'], resp='slverr')], rows)
    assert descriptors == (2,) and occurrence == 1
    assert models[2] == dict(burst_useful_bytes=[16, 8, 24, 16, 8], failed_burst=2)


def test_gather_fault_plan_requires_declared_indices(invocation):
    directory, arch, rows = invocation
    del rows[2]['index_values']
    with pytest.raises(ReconciliationError, match='index payload'):
        execution_keys(directory, arch, arch['src_nodes'], expected_rows=rows)


def test_invalid_index_excludes_unissued_rows_from_fault_uids(invocation):
    directory, arch, rows = invocation
    rows[2]['index_values'] = [0, -1, 2]
    facts = execution_keys(directory, arch, arch['src_nodes'], expected_rows=rows)[3]
    assert [row['useful_bytes'] for row in facts] == [12, 16, 8]
