from collections import Counter
from dataclasses import dataclass, field

from mesh_ir.architecture import ArchManifest
from mesh_ir.gate3_oracle import FactsDocument
from mesh_ir.generated.semantic_enums import AxiChannel
from mesh_ir.runtime_errors import require
from mesh_ir.runtime_memory import MemoryAddressMap, MemoryRange
from mesh_ir.serving_deployment_audit import instantiated_objects


@dataclass
class OwnerTraffic:
    read_bursts: int = 0
    write_bursts: int = 0
    read_useful_bytes: int = 0
    write_useful_bytes: int = 0
    read_beat_bytes: int = 0
    write_beat_bytes: int = 0
    channels: Counter = field(default_factory=Counter)

    def record(self, channel: str, *, count: int = 1, beat_bytes: int = 0, useful_bytes: int = 0) -> None:
        self.channels[channel] += count
        if channel == 'AR':
            self.read_bursts += count
        elif channel == 'AW':
            self.write_bursts += count
        elif channel == 'R':
            self.read_beat_bytes += count * beat_bytes
            self.read_useful_bytes += useful_bytes
        elif channel == 'W':
            self.write_beat_bytes += count * beat_bytes
            self.write_useful_bytes += useful_bytes

    def document(self) -> dict:
        return dict(read_bursts=self.read_bursts, write_bursts=self.write_bursts,
            read_useful_bytes=self.read_useful_bytes, write_useful_bytes=self.write_useful_bytes,
            read_beat_bytes=self.read_beat_bytes, write_beat_bytes=self.write_beat_bytes)


def counter_delta(before: dict, after: dict, names: tuple[str, ...]) -> dict[str, int]:
    require(all(type(before[name]) is int and type(after[name]) is int and
        0 <= before[name] <= after[name] for name in names), 'physical counters moved backwards or are invalid')
    return {name: after[name] - before[name] for name in names}


def verify_serving_memory_traffic(arch: ArchManifest, configuration: dict, result: dict, facts: FactsDocument,
                                  before: dict, after: dict, *, ticks_per_second: int) -> dict:
    require(ticks_per_second > 0 and 0 <= before['tick'] < after['tick'], 'fabric measurement window is invalid')
    require(before['schema'] == after['schema'] == 'ai_mesh_experiment_snapshot_v1', 'fabric snapshot schema differs')
    require(before['tick'] == 0, 'Serving traffic audit requires the initial snapshot before execution')
    seconds = (after['tick'] - before['tick']) / ticks_per_second
    targets = {row['path']: row for row in instantiated_objects(configuration).values() if row['type'] == 'AxiTargetAdapter'}
    old = {row['name']: row for row in before['targets']}
    current = {row['name']: row for row in after['targets']}
    require(len(old) == len(before['targets']) and len(current) == len(after['targets']) and
        old.keys() == current.keys() == targets.keys(), 'physical target snapshot coverage differs')
    address_map = MemoryAddressMap(tuple(MemoryRange(row['dst_node'], begin, end) for row in targets.values()
        for begin, end in zip(row['memory_range_starts'], row['memory_range_ends'], strict=True)))
    owners = {row['dst_node']: OwnerTraffic() for row in targets.values()}
    hbm_ranges = tuple(MemoryRange(target.dst_node, arch.regions[span.region_id].base + span.offset_bytes,
        arch.regions[span.region_id].base + span.offset_bytes + span.size_bytes)
        for target in arch.fabric.targets for span in target.ranges if arch.regions[span.region_id].kind == 'HBM')
    hbm = {span.node: OwnerTraffic() for span in hbm_ranges}
    require(hbm, 'architecture contains no physical HBM owner')
    seen_bursts = set()
    for frame in result['instances']:
        for burst in frame['bursts']:
            identity = (frame['instance'], burst['core_id'], burst['ordinal'])
            require(identity not in seen_bursts and burst['instance'] == frame['instance'], 'DMA burst identity is duplicated or misplaced')
            seen_bursts.add(identity)
            require(not burst['errored'] and burst['channel'] in ('AR', 'AW') and
                0 < burst['beats'] and 0 < burst['beat_bytes'] <= arch.axi_data_bytes and
                before['tick'] <= burst['issue_tick'] <= burst['done_tick'] <= after['tick'], 'DMA burst is not a healthy measured transfer')
            size = burst['beats'] * burst['beat_bytes']
            address = burst['address']
            require(address <= burst['logical_start'] and 0 < burst['useful_bytes'] and
                burst['logical_start'] + burst['useful_bytes'] <= address + size, 'DMA useful bytes exceed its physical beat span')
            mapped = [(node, spans) for node, spans in address_map.partition([(address, size)]).items() if spans]
            require(len(mapped) == 1 and len(mapped[0][1]) == 1, 'one AXI burst crosses physical owner ranges')
            node = mapped[0][0]
            records = [owners[node]]
            matching = [span for span in hbm_ranges if span.begin <= address and address + size <= span.end]
            if matching:
                require(len(matching) == 1 and matching[0].node == node, 'HBM address owner differs from deployment')
                records.append(hbm[node])
            for record in records:
                record.record(burst['channel'])
                record.record('R' if burst['channel'] == 'AR' else 'W', count=burst['beats'],
                    beat_bytes=burst['beat_bytes'], useful_bytes=burst['useful_bytes'])
                if burst['channel'] == 'AW':
                    record.record('B')
    for event in facts.events:
        if event['kind'] != 'AXI_ACCEPT':
            continue
        channel = event['channel']
        require(channel in ('AW', 'W', 'B', 'AR', 'R') and
            before['tick'] <= event['tick'] <= after['tick'], 'control AXI event is outside the measurement')
        require(channel not in ('R', 'B') or event['response'] == 'OKAY', 'control AXI response failed')
        beat = 1 << event['size']
        require(0 < beat <= arch.axi_data_bytes, 'control AXI beat width exceeds the bus')
        mapped = [(node, spans) for node, spans in address_map.partition([(event['address'], beat)]).items() if spans]
        require(len(mapped) == 1 and len(mapped[0][1]) == 1, 'control AXI beat crosses physical owners')
        useful = event['bytes']
        require(0 <= useful <= beat, 'control AXI useful bytes exceed its beat')
        if channel == 'W':
            require(event['wstrb'] is not None and int(event['wstrb'], 16).bit_count() == useful,
                'control AXI write strobe differs from useful bytes')
        owners[mapped[0][0]].record(channel, beat_bytes=beat, useful_bytes=useful)
    physical = []
    pending_fields = ('write_contexts', 'write_reserved_beats', 'orphan_transactions', 'orphan_reserved_beats',
        'read_contexts', 'read_reserved_beats', 'b_ready', 'r_ready', 'write_services', 'read_services')
    for name, target in targets.items():
        initial, final = old[name], current[name]
        require(initial['idle'] and final['idle'] and all(initial[key] == final[key] == 0 for key in pending_fields),
            'physical target has pending transactions or services')
        counts = counter_delta(initial, final, ('read_committed_bytes', 'write_committed_bytes',
            'reads_committed', 'writes_committed', 'same_id_ready_blocked', 'orphan_or_quota_stall_cycles'))
        expected = owners[target['dst_node']]
        require(counts['read_committed_bytes'] == expected.read_beat_bytes and
            counts['write_committed_bytes'] == expected.write_useful_bytes and
            counts['reads_committed'] == expected.read_bursts and counts['writes_committed'] == expected.write_bursts,
            f"physical target {target['dst_node']} counters differ from CPU and core AXI transfers")
        backend = final['synthetic_backend']
        require((backend is not None) == target['synthetic_hbm_enabled'] and
            (initial['synthetic_backend'] is not None) == (backend is not None), 'physical HBM backend coverage differs')
        service = None
        if backend is not None:
            require(backend['queued'] == backend['ready'] == 0 and
                0 <= backend['high_water'] <= target['synthetic_hbm_queue_depth'], 'physical HBM backend did not drain or exceeded capacity')
            service = counter_delta(initial['synthetic_backend'], backend, ('submitted', 'completed', 'retired',
                'read_bytes_serviced', 'write_bytes_serviced', 'busy_cycles', 'queue_full_cycles', 'request_slot_cycles'))
            require(service['submitted'] == service['completed'] == service['retired'] == expected.read_bursts + expected.write_bursts and
                service['read_bytes_serviced'] == expected.read_beat_bytes and service['write_bytes_serviced'] == expected.write_beat_bytes,
                'physical HBM backend service differs from actual AXI beat bytes')
            service.update(high_water=backend['high_water'], read_service_Bps=service['read_bytes_serviced'] / seconds,
                write_service_Bps=service['write_bytes_serviced'] / seconds)
        physical.append(dict(node=target['dst_node'], target=name, counters=counts, backend=service,
            queue_high_water=final['queue_high_water']))
    channels = tuple(channel.name for channel in AxiChannel)
    packets = Counter()
    for traffic in owners.values():
        packets.update(traffic.channels)
    network = []
    for index, channel in enumerate(channels):
        size = arch.fabric.axi.wire_header_bytes[index]
        if channel in ('W', 'R'):
            size = arch.axi_data_bytes + (0 if arch.fabric.axi.data_header_sideband else size)
        expected = dict(packets=packets[channel], wire_bytes=packets[channel] * size,
            flits=packets[channel] * ((size + arch.fabric.network.flit_bytes - 1) // arch.fabric.network.flit_bytes))
        for unit, count in expected.items():
            for direction in ('injected', 'received'):
                key = unit + '_' + direction
                initial, final = before['network_counters'][key], after['network_counters'][key]
                require(len(initial) == len(final) == len(channels) and initial[index] == 0 and final[index] == count,
                    f'physical network {key} differs from CPU and core {channel} packets')
                if unit != 'wire_bytes':
                    require(result['garnet_traffic'][direction][unit][index] == count, 'archived network traffic differs from physical counters')
        network.append(dict(channel=channel, **expected))
    require(sum(row['wire_bytes'] for row in network) == result['garnet']['wire_bytes_received'] and
        sum(row['packets'] for row in network) == result['garnet']['packets_received'], 'archived network totals differ')
    return dict(window=dict(begin_tick=before['tick'], end_tick=after['tick'], seconds=seconds,
        ticks_per_second=ticks_per_second, scope='whole_simulation'),
        hbm=[dict(node=node, **traffic.document()) for node, traffic in sorted(hbm.items())],
        targets=sorted(physical, key=lambda row: row['node']), network=network)
