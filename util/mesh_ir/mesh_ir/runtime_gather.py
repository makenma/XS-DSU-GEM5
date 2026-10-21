from mesh_ir.burst_splitter import SegmentPlan, plan_descriptor, split_segment
from mesh_ir.generated.abi import DMA_KIND
from mesh_ir.runtime_errors import require


def source_rows(row):
    if row['kind'] == DMA_KIND.GATHER_LOAD:
        values = row.get('index_values')
        require(values is not None and len(values) == row['rows'],
                'indexed transfer has no declared index payload')
        for value in values:
            require(type(value) is int and -(1 << 31) <= value < (1 << 31),
                    'index payload is not INT32')
            if not 0 <= value < row['table_rows']:
                break
            yield row['remote_address'] + value * row['remote_stride_bytes']
    else:
        for ordinal in range(row['rows']):
            yield row['remote_address'] + ordinal * row['remote_stride_bytes']


def descriptor_plan(row, width, max_beats):
    cap = min(row['max_burst_beats'], max_beats)
    if row['kind'] != DMA_KIND.GATHER_LOAD:
        return plan_descriptor(row['row_bytes'], row['rows'], row['remote_address'],
                               row['remote_stride_bytes'], width, cap)
    addresses = tuple(source_rows(row)) if row['row_bytes'] else ()
    bursts = tuple(burst for address in addresses
                   for burst in split_segment(address, row['row_bytes'], width, cap))
    return SegmentPlan(len(addresses), bursts, sum(b.beats * width for b in bursts),
                       len(addresses) * row['row_bytes'])


def verify_index_reads(execution, row, index_offset, index_stride, *, failed_occurrence=None):
    values = row['index_values'] if row['row_bytes'] else ()
    invalid = next((k for k, value in enumerate(values)
                    if not 0 <= value < row['table_rows']), None)
    count = (len(values) if invalid is None else invalid + 1) if failed_occurrence is None else failed_occurrence + 1
    reads = execution.get('index_reads', ())
    require(len(reads) == count, 'index read count differs from declared traversal')
    previous = execution['submit_tick']
    for ordinal, read in enumerate(reads):
        require((read['occurrence'], read['index_offset'], read['raw_index']) ==
                (ordinal, index_offset + ordinal * index_stride, values[ordinal] & 0xffffffff),
                'index read identity differs from declared payload and traversal')
        require(previous <= read['issue_tick'] < read['ready_tick'] <= read['read_tick']
                <= execution['completion_tick'], 'index read preceded SRAM service or execution')
        previous = read['read_tick']
    status = ('OK' if invalid is None else 'INDEX_OUT_OF_RANGE') if failed_occurrence is None else 'AXI_READ_ERROR'
    require(execution['status'] == status,
            'index bounds outcome differs from declared payload')
    return count


def verify_gather_execution(schedule, execution, row, *, failed_occurrence=None):
    sections = schedule['sections']
    command, = [item for item in sections['COMMANDS']
                if item['command_id'] == execution['command_id']]
    attr = sections['OP_ATTRS'][command['attr_index'] - 1]
    operand = sections['COMMAND_OPERANDS'][command['operand_begin'] + attr['index_operand_index']]
    allocation, = [item for item in sections['ALLOCATIONS']
                   if item['allocation_id'] == operand['allocation_id']]
    shard, = [item for item in sections['SHARDS'] if item['shard_id'] == operand['shard_id']]
    source, = [item for item in schedule['semantics']['endpoint_uses']
               if item['descriptor_id'] == row['descriptor_id'] and item['side'] == 'SRC']
    require(row['table_rows'] == source['use']['region']['shape'][0],
            'index table bound differs from admitted source')
    offset = allocation['offset_bytes'] + shard['allocation_offset'] + attr['index_offset_bytes']
    return verify_index_reads(execution, row, offset, attr['index_stride_bytes'],
                              failed_occurrence=failed_occurrence)


def verify_gather_read_fault(execution, row, bursts, failed_burst):
    planned = [(occurrence, burst) for occurrence, address in enumerate(source_rows(row))
               for burst in split_segment(address, row['row_bytes'], row['axi_data_bytes'], row['max_burst_beats'])]
    require(type(failed_burst) is int and 0 <= failed_burst < len(planned),
            'gather fault does not identify an admitted burst')
    failed_occurrence = planned[failed_burst][0]
    end = sum(occurrence <= failed_occurrence for occurrence, _ in planned)
    actual = sorted(bursts, key=lambda item: item['burst_index'])
    require(failed_burst < len(actual) <= end,
            'gather fault issued beyond its failing occurrence or omitted the failed burst')
    require([item['burst_index'] for item in actual] == list(range(len(actual))),
            'gather fault burst archive is not an ordered prefix')
    require([item['burst_index'] for item in actual if item['errored']] == [failed_burst],
            'gather error responses differ from the injected fault')
    reads = execution['index_reads']
    require(len(reads) == failed_occurrence + 1 and
            [read['occurrence'] for read in reads] == list(range(len(reads))),
            'gather fault index traversal differs from its failing occurrence')
    error_tick = actual[failed_burst]['response_tick']
    for item, (occurrence, expected) in zip(actual, planned):
        require((item['channel'], item['address'], item['logical_start'], item['beats'], item['useful_bytes']) ==
                ('AR', expected.beat_base, expected.logical_start, expected.beats, expected.useful_bytes),
                'gather fault burst differs from its indexed address plan')
        require(reads[occurrence]['read_tick'] <= item['issue_tick'] <= error_tick,
                'gather fault burst preceded its index read or followed the error response')
    successful = [planned[item['burst_index']][1] for item in actual if not item['errored']]
    transfer = execution['transfer']
    require(transfer['read_bytes'] == sum(burst.useful_bytes for burst in successful) and
            transfer['read_bursts'] == len(successful),
            'gather fault transfer differs from successful issued bursts')
    require(not any(transfer.get(field, 0) for field in
                    ('write_bytes', 'p2p_bytes', 'fill_bytes', 'write_bursts', 'p2p_bursts')),
            'gather fault moved traffic of the wrong direction')
    return failed_occurrence
