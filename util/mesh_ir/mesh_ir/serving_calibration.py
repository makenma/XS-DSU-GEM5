from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path

from mesh_ir.experiment.metrics import latency_summary
from mesh_ir.gate3_oracle import read_facts_document


@dataclass
class ServingMeasurement:
    terminal_tick: int
    host_seconds: float
    latencies: dict[int, int]
    statuses: dict[int, str]
    outputs: dict[int, str]
    traffic: dict[str, int]
    network_messages: int
    network_wire_bytes: int

    @classmethod
    def read(cls, directory):
        directory = Path(directory)
        result = json.loads((directory / 'mesh_result.json').read_text())
        facts = read_facts_document(directory / 'gate6_facts.tsv')
        if result['terminal'] != 'DONE' or facts.fatal or result['watchdog_fired']:
            raise ValueError('E_CALIBRATION_TERMINAL: execution did not complete')
        starts = {row['request_id']: row['tick'] for row in facts.events if row['kind'] == 'SQ_CONSUME'}
        completions = {row['request_id']: row for row in facts.events if row['kind'] == 'CQ_CONSUME'}
        if starts.keys() != completions.keys():
            raise ValueError('E_CALIBRATION_COHORT: incomplete requests')
        outputs = {row['request_id']: row['status'] for row in facts.events
                   if row['kind'] == 'LOCAL_READ' and row['object'] == 'GENERATED_CODE_READ'}
        traffic = Counter()
        for row in result['burst_timings']:
            traffic[row['channel']] += row['beats'] * row['beat_bytes']
        stats = {}
        for line in (directory / 'stats.txt').read_text().splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[0] in ('hostSeconds', 'simTicks'):
                stats[fields[0]] = float(fields[1])
        garnet, aggregate = result['garnet'], result['aggregate_network']
        return cls(result['terminal_tick'], stats['hostSeconds'],
            {key: completions[key]['tick'] - tick for key, tick in starts.items()},
            {key: row['status'] for key, row in completions.items()}, outputs, dict(traffic),
            (garnet['packets_received'] if garnet is not None else 0) +
            (aggregate['endpoint_messages'] if aggregate is not None else 0),
            (garnet['wire_bytes_received'] if garnet is not None else 0) +
            (aggregate['endpoint_wire_bytes'] if aggregate is not None else 0))


def compare_serving(reference, candidate, request_ids=None):
    if (reference.latencies.keys() != candidate.latencies.keys() or
            reference.statuses != candidate.statuses or reference.outputs != candidate.outputs):
        raise ValueError('E_CALIBRATION_COHORT: request identities, status or output differ')
    if (reference.traffic != candidate.traffic or reference.network_messages != candidate.network_messages or
            reference.network_wire_bytes != candidate.network_wire_bytes):
        raise ValueError('E_CALIBRATION_TRAFFIC: executed traffic differs')
    cohort = set(reference.latencies) if request_ids is None else set(request_ids)
    if not cohort or not cohort <= reference.latencies.keys():
        raise ValueError('E_CALIBRATION_COHORT: empty or unknown window population')
    if any(reference.latencies[key] <= 0 or candidate.latencies[key] <= 0 for key in cohort):
        raise ValueError('E_CALIBRATION_LATENCY: nonpositive request latency')
    errors = {str(key): (candidate.latencies[key] - reference.latencies[key]) / reference.latencies[key]
              for key in sorted(cohort)}
    return dict(request_relative_error=errors, max_absolute_request_error=max(map(abs, errors.values())),
        reference_latency_ticks=latency_summary(reference.latencies[key] for key in cohort),
        measured_latency_ticks=latency_summary(candidate.latencies[key] for key in cohort),
        terminal_relative_error=(candidate.terminal_tick - reference.terminal_tick) / reference.terminal_tick,
        host_speedup=reference.host_seconds / candidate.host_seconds,
        host_seconds=candidate.host_seconds, traffic_equal=True,
        dma_bytes=candidate.traffic, network_messages=candidate.network_messages,
        network_wire_bytes=candidate.network_wire_bytes)


def window_dma_traffic(directory, request_ids):
    directory = Path(directory)
    facts = read_facts_document(directory / 'gate6_facts.tsv')
    starts = {row['request_id']: row['tick'] for row in facts.events if row['kind'] == 'SQ_CONSUME'}
    ends = {row['request_id']: row['tick'] for row in facts.events if row['kind'] == 'CQ_CONSUME'}
    cohort = set(request_ids)
    intervals = [(starts[key], ends[key]) for key in cohort]
    result = json.loads((directory / 'mesh_result.json').read_text())
    traffic = Counter()
    for row in result['burst_timings']:
        if any(start <= row['ar_aw_tick'] < end for start, end in intervals):
            key = ':'.join(str(row[field]) for field in ('core_id', 'channel', 'address', 'beats', 'beat_bytes'))
            traffic[key] += 1
    return dict(sorted(traffic.items()))
