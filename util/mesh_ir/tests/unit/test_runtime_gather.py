import pytest

from mesh_ir.runtime_errors import ReconciliationError
from mesh_ir.runtime_gather import descriptor_plan, verify_index_reads


def row(indices):
    return dict(kind=6, rows=len(indices), row_bytes=12, remote_address=4090,
                remote_stride_bytes=20, max_burst_beats=16, table_rows=32,
                index_values=indices)


def test_duplicate_occurrences_preserve_their_burst_order():
    plan = descriptor_plan(row([7, 2, 7]), 32, 16)
    assert [b.logical_start for b in plan.bursts] == [4230, 4130, 4230]
    assert plan.useful_bytes == 36


def test_invalid_index_stops_before_issuing_its_row():
    plan = descriptor_plan(row([7, 32, 2]), 32, 16)
    assert [b.logical_start for b in plan.bursts] == [4230]
    assert plan.useful_bytes == 12
    assert not descriptor_plan(row([-1, 2, 7]), 32, 16).bursts


def test_index_read_evidence_is_timed_and_occurrence_specific():
    reads = [dict(occurrence=k, index_offset=64 + 8*k, raw_index=value,
                  issue_tick=10 + 3*k, ready_tick=11 + 3*k, read_tick=12 + 3*k)
             for k, value in enumerate([7, 2, 7])]
    execution = dict(index_reads=reads, submit_tick=9, completion_tick=30, status='OK')
    assert verify_index_reads(execution, row([7, 2, 7]), 64, 8) == 3
    reads[2]['index_offset'] = 64
    with pytest.raises(ReconciliationError, match='index'):
        verify_index_reads(execution, row([7, 2, 7]), 64, 8)


def test_a_functional_peek_cannot_substitute_for_sram_service():
    execution = dict(index_reads=[dict(occurrence=0, index_offset=0, raw_index=7,
                     issue_tick=10, ready_tick=15, read_tick=10)],
                     submit_tick=9, completion_tick=30, status='OK')
    with pytest.raises(ReconciliationError, match='index'):
        verify_index_reads(execution, row([7]), 0, 4)


def test_index_traversal_includes_the_bound_view_offset():
    from mesh_ir.runtime_gather import verify_gather_execution
    schedule = dict(sections=dict(
        COMMANDS=[dict(command_id=2, attr_index=1, operand_begin=0)],
        OP_ATTRS=[dict(index_operand_index=0, index_offset_bytes=4, index_stride_bytes=8)],
        COMMAND_OPERANDS=[dict(allocation_id=3, shard_id=4)],
        ALLOCATIONS=[dict(allocation_id=3, offset_bytes=64)],
        SHARDS=[dict(shard_id=4, allocation_offset=16)]),
        semantics=dict(endpoint_uses=[dict(descriptor_id=1, side='SRC',
                       use=dict(region=dict(shape=[32, 3])))]))
    execution = dict(command_id=2, index_reads=[dict(occurrence=0, index_offset=84,
                     raw_index=7, issue_tick=10, ready_tick=11, read_tick=12)],
                     submit_tick=9, completion_tick=30, status='OK')
    assert verify_gather_execution(schedule, execution, dict(row([7]), descriptor_id=1)) == 1


@pytest.mark.parametrize('damage', [None, 'future_row', 'wrong_error', 'late_issue', 'bytes', 'occurrence'])
def test_gather_fault_preserves_only_issued_successful_segments(damage):
    from mesh_ir.runtime_gather import verify_gather_read_fault
    expected = dict(row([0, 2, 0]), row_bytes=24, remote_address=4080,
                    remote_stride_bytes=24, axi_data_bytes=32)
    execution = dict(index_reads=[dict(occurrence=0, index_offset=64, raw_index=0,
        issue_tick=10, ready_tick=11, read_tick=12)], submit_tick=9,
        completion_tick=40, status='AXI_READ_ERROR', transfer=dict(read_bytes=8, read_bursts=1))
    bursts = [dict(burst_index=i, address=address, logical_start=logical, beats=1,
        useful_bytes=size, issue_tick=13+i, response_tick=30+i, errored=i==0,
        channel='AR') for i, (address, logical, size) in enumerate([(4064,4080,16),(4096,4096,8)])]
    if damage == 'future_row':
        bursts.append(dict(bursts[-1], burst_index=2, address=4128, logical_start=4128, useful_bytes=24))
    elif damage == 'wrong_error':
        bursts[1]['errored'] = True
    elif damage == 'late_issue':
        bursts[1]['issue_tick'] = 31
    elif damage == 'bytes':
        execution['transfer']['read_bytes'] = 24
    elif damage == 'occurrence':
        execution['index_reads'][0]['occurrence'] = 2
    if damage is None:
        assert verify_gather_read_fault(execution, expected, bursts, 0) == 0
    else:
        with pytest.raises(ReconciliationError):
            verify_gather_read_fault(execution, expected, bursts, 0)
