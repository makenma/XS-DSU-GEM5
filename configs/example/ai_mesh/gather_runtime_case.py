import json
from pathlib import Path

from mesh_ir.fault_plan import execution_keys, target_of
from mesh_ir.burst_splitter import split_segment
from mesh_ir.runtime_errors import require
from mesh_ir.runtime_gather import source_rows, verify_gather_execution, verify_gather_read_fault
from mesh_ir.runtime_reconciliation import payload_digest


def setup(ctx):
    data = json.loads(Path(ctx.options.gather_inputs).read_text())
    descriptors = ctx.schedule['sections']['DMA_DESCRIPTORS']
    commands = {row['command_id']: row for row in ctx.schedule['sections']['COMMANDS']}
    ctx.seeds, ctx.verify = [], []
    for descriptor in descriptors:
        row = ctx.expected[descriptor['descriptor_id']]
        if descriptor['kind'] == 6:
            command = commands[descriptor['command_id']]
            attr = ctx.schedule['sections']['OP_ATTRS'][command['attr_index'] - 1]
            index = ctx.schedule['sections']['COMMAND_OPERANDS'][
                command['operand_begin'] + attr['index_operand_index']]
            producer, = [item for item in descriptors if item['kind'] == 4
                         and item['dst']['shard_id'] == index['shard_id']]
            source, = [item for item in ctx.schedule['semantics']['endpoint_uses']
                       if item['descriptor_id'] == descriptor['descriptor_id'] and item['side'] == 'SRC']
            row.update(index_values=data['indices'], table_rows=source['use']['region']['shape'][0],
                       axi_data_bytes=ctx.arch['axi_data_bytes'])
            require(len(data['indices']) == row['rows'], 'runtime index count differs from admitted carrier')
            payload = b''.join(int(value).to_bytes(4, 'little', signed=True) for value in data['indices'])
            address = ctx.expected[producer['descriptor_id']]['remote_address']
            ctx.seeds.extend((address + offset, 1, byte) for offset, byte in enumerate(payload))
            for value, address in zip(data['indices'], source_rows(row)):
                ctx.seeds.append((address, row['row_bytes'], 1 + (value * 37) % 255))
            if any(not 0 <= value < row['table_rows'] for value in data['indices']):
                ctx.expected_terminal = 'MESH_PROGRAM_ERROR_DRAINED'
        elif descriptor['kind'] == 2:
            ctx.verify.extend((address, row['row_bytes']) for address in source_rows(row))
    if 'read_error_burst' in data:
        facts = execution_keys(ctx.program_dir, ctx.arch, ctx.arch['src_nodes'], expected_rows=ctx.expected)
        failed, = [fact for core in facts.values() for fact in core
                   if fact['kind'] == 6 and fact['burst_index'] == data['read_error_burst']]
        target = target_of(failed['address'], ctx.scenario['target_ranges'])
        require(target is not None, 'gather fault address has no physical target')
        ctx.scenario['mesh_planned_faults'].append(dict(target=target, uid=failed['uid'], resp='slverr'))


def check(ctx, result, expected_rows, out):
    sections = ctx.schedule['sections']
    commands = {row['command_id']: row for row in sections['COMMANDS']}
    for frame in result['instances']:
        for core_id, ledger in frame['cores'].items():
            for execution in ledger['observations']['descriptor_executions']:
                row = expected_rows[execution['descriptor_id']]
                if row['kind'] != 6:
                    continue
                bursts = sorted((burst for burst in frame['bursts']
                                 if burst['core_id'] == int(core_id) and
                                 all(burst[field] == execution[field] for field in
                                     ('command_id', 'generation', 'descriptor_id'))),
                                key=lambda burst: burst['burst_index'])
                fault = ctx.fault_models.get(execution['descriptor_id'])
                failed_occurrence = (verify_gather_read_fault(execution, row, bursts, fault['failed_burst'])
                                     if fault else None)
                verify_gather_execution(ctx.schedule, execution, row, failed_occurrence=failed_occurrence)
                ordinal = 0
                chunks = []
                for read, address in zip(execution['index_reads'], source_rows(row)):
                    for burst in split_segment(address, row['row_bytes'], ctx.arch['axi_data_bytes'], row['max_burst_beats']):
                        if ordinal == len(bursts):
                            require(fault is not None, 'gather omitted a valid occurrence burst')
                            break
                        actual = bursts[ordinal]
                        require(actual['issue_tick'] >= read['read_tick'], 'HBM request preceded SRAM index read')
                        if not actual['errored']:
                            chunks.append(bytes([1 + (read['raw_index'] * 37) % 255]) * burst.useful_bytes)
                        ordinal += 1
                require(ordinal == len(bursts), 'gather issued beyond its valid index prefix')
                payload = b''.join(chunks)
                require(execution['transfer']['payload_digest'] == payload_digest(payload),
                        'gather staging content differs from declared table occurrences')
                if execution['status'] == 'OK':
                    stores = [item for item in ledger['observations']['descriptor_executions']
                              if expected_rows[item['descriptor_id']]['kind'] == 2]
                    require(len(stores) == 1 and stores[0]['transfer']['payload_digest'] == payload_digest(payload),
                            'ordered embedding consumer or output store changed gathered bytes')
                    destination, = [item for item in frame['destinations']
                                    if item['descriptor_id'] == stores[0]['descriptor_id']]
                    require(bytes.fromhex(destination['bytes_hex']) == payload,
                            'output HBM bytes differ from runtime gathered occurrences')
                else:
                    require(not any(commands[item['command_id']]['opcode'] == 12
                                    and item['state'] == 'completed' for item in ledger['terminals']),
                            'failed gather allowed its vector consumer to complete')
    out.append('gather occurrences: PASS')
