"""Validate endpoint traffic statistics, optionally comparing every row to raw observations.

VALID means the counts are structurally consistent, not that Linux or CHI handshakes passed.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import re

REQ = {0x26: 'ReadNotSharedDirty', 0x07: 'ReadUnique', 0x0c: 'MakeUnique',
       0x08: 'CleanShared', 0x09: 'CleanInvalid', 0x0a: 'MakeInvalid',
       0x0d: 'Evict', 0x1b: 'WriteBackFull', 0x17: 'WriteCleanFull',
       0x42: 'WriteEvictOrEvict', 0x04: 'ReadNoSnp', 0x1c: 'WriteNoSnpPtl'}
WIDTH = {'REQ': 127, 'RSP': 31, 'SNP': 31, 'DAT': 15}
MAX64 = (1 << 64) - 1


def parse_json(text):
    def unique_pairs(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError('duplicate JSON key: ' + key)
            out[key] = value
        return out
    return json.loads(text, object_pairs_hook=unique_pairs)


def load_json(path):
    return parse_json(Path(path).read_text())


def check(census, config, raw_lines=None):
    errors = []
    def require(ok, why):
        if not ok:
            errors.append(why)
        return ok
    def number(value, name, minimum=0):
        if not require(type(value) is str and bool(re.fullmatch(r'0|[1-9][0-9]*', value)), name + ': expected canonical decimal string'):
            return None
        result = int(value)
        if not require(minimum <= result <= MAX64, name + ': outside uint64 range'):
            return None
        return result
    def integer(value, maximum=65535):
        return type(value) is int and 0 <= value <= maximum
    manifest = config.get('manifest', {})
    require(manifest.get('profile') == 'h-native-256', 'configuration must declare h-native-256')
    topology = manifest.get('topology', [])
    require(isinstance(topology, list), 'topology must be an array')
    if not isinstance(topology, list):
        topology = []
    nodes, labels = {}, set()
    for node in topology:
        if not require(isinstance(node, dict), 'topology node must be an object'):
            continue
        node_id = node.get('nodeId')
        name = node.get('id')
        identity = type(name) is str and bool(name) and name not in labels
        identity &= node.get('role') in ('RN-F', 'HN-F', 'SN-F')
        if require(identity and integer(node_id) and node_id not in nodes, 'invalid or duplicate topology node identity'):
            nodes[node_id] = node
            labels.add(name)
    ports = manifest.get('endpointPorts', {})
    require(isinstance(ports, dict) and bool(ports), 'endpointPorts must be a nonempty object')
    if not isinstance(ports, dict):
        ports = {}
    for port, node_id in ports.items():
        require(type(port) is str and bool(port) and integer(node_id) and node_id in nodes,
                'endpoint port must map to an actual topology node: ' + str(port))
    rn_nodes = {node_id for node_id, node in nodes.items() if node.get('role') == 'RN-F'}
    require(bool(rn_nodes), 'topology has no RN-F endpoint')
    require(census.get('schemaVersion') == '1.0' and census.get('source') == 'accepted native endpoint transfers',
            'incorrect census schema/source')
    require(type(census.get('snapshotKind')) is str and bool(census.get('snapshotKind')), 'snapshotKind missing')
    total = number(census.get('observations'), 'observations')
    last_tick = number(census.get('lastTick'), 'lastTick')
    rows = census.get('counts', [])
    require(isinstance(rows, list), 'counts must be an array')
    if not isinstance(rows, list):
        rows = []
    def key_of(row, label):
        boundary, direction, channel = row.get('boundary'), row.get('observation'), row.get('channel')
        src, tgt, opcode = row.get('srcid'), row.get('tgtid'), row.get('opcode')
        valid = require(type(boundary) is str and boundary in ports, label + ': unknown endpoint boundary')
        valid &= require(direction in ('TX', 'RX'), label + ': direction must be TX or RX')
        valid &= require(type(channel) is str and channel in WIDTH, label + ': invalid channel')
        valid &= require(integer(src) and src in nodes and integer(tgt) and tgt in nodes, label + ': unknown or invalid source/target NodeID')
        valid &= require(integer(opcode, WIDTH.get(channel, -1) if type(channel) is str else -1), label + ': invalid channel opcode width')
        if valid:
            valid &= require(ports[boundary] == (src if direction == 'TX' else tgt), label + ': boundary node does not match flit endpoint identity')
        return (boundary, direction, channel, src, tgt, opcode) if valid else None
    index = {}
    for position, row in enumerate(rows):
        label = f'row {position}'
        if not require(isinstance(row, dict), label + ': expected object'):
            continue
        key = key_of(row, label)
        count = number(row.get('count'), label + '.count', 1)
        first = number(row.get('firstTick'), label + '.firstTick')
        last = number(row.get('lastTick'), label + '.lastTick')
        if first is not None and last is not None:
            require(first <= last, label + ': firstTick exceeds lastTick')
            require(count != 1 or first == last, label + ': one observation must have equal first/last tick')
        if key is not None and count is not None and first is not None and last is not None:
            require(key not in index, label + ': duplicate aggregation key')
            index[key] = (count, first, last)
    require(total == sum(item[0] for item in index.values()), 'observations differs from sum of rows')
    require(last_tick == max((item[2] for item in index.values()), default=0), 'global lastTick differs from row maximum')
    raw_report = {'requested': raw_lines is not None}
    if raw_lines is not None:
        errors_before_raw = len(errors)
        actual, raw_errors = {}, 0
        previous_tick = -1
        manifests = 0
        for line_number, line in enumerate(raw_lines, 1):
            if not line.strip():
                continue
            try:
                event = parse_json(line)
            except ValueError:
                errors.append(f'raw line {line_number}: invalid/truncated JSON')
                raw_errors += 1
                continue
            if not isinstance(event, dict):
                errors.append(f'raw line {line_number}: expected object')
                continue
            if event.get('type') == 'manifest':
                manifests += 1
                require(event.get('profile') == 'h-native-256', 'raw manifest has wrong native profile')
                require(event.get('endpointPorts') == ports, 'raw manifest endpointPorts differs from configuration')
            if event.get('type') != 'observed' or event.get('kind') != 'flit' or event.get('wireEncoding') != 'native-chi':
                continue
            fields = event.get('fields', {})
            if not require(isinstance(fields, dict), f'raw line {line_number}: fields must be an object'):
                continue
            row = {key: event.get(key) for key in ('boundary', 'observation', 'channel')}
            row.update({key: fields.get(key) for key in ('srcid', 'tgtid', 'opcode')})
            key = key_of(row, f'raw line {line_number}')
            tick = number(event.get('tick'), f'raw line {line_number}.tick')
            if key is None or tick is None:
                continue
            require(tick >= previous_tick, 'raw native flit ticks are not monotonic')
            previous_tick = tick
            count, first, _ = actual.get(key, (0, tick, tick))
            actual[key] = (count + 1, first, tick)
        require(manifests == 1, 'raw must contain exactly one manifest')
        for key in sorted(index.keys() | actual.keys()):
            require(index.get(key) == actual.get(key), 'raw/census count or first/last mismatch: ' + repr(key))
        raw_report.update({'nativeFlitObservations': str(sum(item[0] for item in actual.values())),
                           'aggregationKeys': len(actual), 'identical': actual == index and raw_errors == 0 and len(errors) == errors_before_raw,
                           'scope': 'All native endpoint observations, including RSP/DAT/SNP; exact count/first/last per key.'})
    req_counts = defaultdict(int)
    by_rn = {nodes[node_id]['id']: defaultdict(int) for node_id in rn_nodes}
    other = defaultdict(int)
    for (boundary, direction, channel, src, tgt, opcode), (count, first, last) in index.items():
        if direction == 'TX' and channel == 'REQ' and src in rn_nodes and ports.get(boundary) == src:
            if opcode in REQ:
                req_counts[opcode] += count
                by_rn[nodes[src]['id']][REQ[opcode]] += count
            else:
                other[f'0x{opcode:02x}'] += count
    missing = [name for opcode, name in REQ.items() if not req_counts[opcode]]
    return {'schemaVersion': 1, 'status': 'INVALID' if errors else 'VALID', 'valid': not errors,
            'observations': str(total) if total is not None else None, 'snapshotKind': census.get('snapshotKind'),
            'rtlRequestCounts': {name: str(req_counts[opcode]) for opcode, name in REQ.items()},
            'rtlRequestOpcodes': {name: f'0x{opcode:02x}' for opcode, name in REQ.items()},
            'rnRequestCounts': {name: {op: str(value) for op, value in counts.items()} for name, counts in by_rn.items()},
            'otherRnRequestOpcodes': {key: str(value) for key, value in other.items()},
            'completeRtlRequestCoverage': not missing, 'missingRtlRequestOpcodes': missing,
            'rawComparison': raw_report, 'errors': errors,
            'scope': 'Accepted transfer attempts including retries. Coverage is not Linux-test, lifecycle, handshake, data, or final-state PASS.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--census', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--raw', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    census, config = load_json(args.census), load_json(args.config)
    if args.raw:
        with args.raw.open(encoding='utf-8') as stream:
            result = check(census, config, stream)
    else:
        result = check(census, config)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['valid'] else 1)
