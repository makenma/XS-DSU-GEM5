"""Deterministic Issue H illustrations. No event in this module is a CPU capture."""
import copy
import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'public/demos'
BASE = json.loads((DEST / 'litmus-iriw.chi.jsonl').read_text(encoding='utf-8').splitlines()[0])
OP = {
    'REQ': {'ReadShared': 1, 'ReadOnce': 3, 'ReadNoSnp': 4, 'PCrdReturn': 5,
            'ReadUnique': 7, 'CleanShared': 8, 'CleanInvalid': 9, 'MakeInvalid': 10,
            'MakeUnique': 12, 'Evict': 13, 'ReadNoSnpSep': 0x11,
            'WriteEvictFull': 0x15, 'WriteCleanFull': 0x17, 'WriteUniquePtl': 0x18,
            'WriteUniqueFull': 0x19, 'WriteBackFull': 0x1b,
            'WriteNoSnpPtl': 0x1c, 'WriteNoSnpFull': 0x1d,
            'StashOnceShared': 0x22, 'StashOnceUnique': 0x23,
            'ReadOnceCleanInvalid': 0x24, 'ReadOnceMakeInvalid': 0x25,
            'ReadNotSharedDirty': 0x26, 'MakeReadUnique': 0x41},
    'SNP': {'SnpShared': 1, 'SnpClean': 2, 'SnpOnce': 3, 'SnpNotSharedDirty': 4,
            'SnpUnique': 7, 'SnpCleanShared': 8, 'SnpCleanInvalid': 9,
            'SnpMakeInvalid': 10, 'SnpSharedFwd': 0x11, 'SnpCleanFwd': 0x12,
            'SnpOnceFwd': 0x13, 'SnpNotSharedDirtyFwd': 0x14, 'SnpUniqueFwd': 0x17},
    'RSP': {'SnpResp': 1, 'CompAck': 2, 'RetryAck': 3, 'Comp': 4,
            'CompDBIDResp': 5, 'DBIDResp': 6, 'PCrdGrant': 7,
            'ReadReceipt': 8, 'SnpRespFwded': 9, 'RespSepData': 0xb},
    'DAT': {'SnpRespData': 1, 'CopyBackWriteData': 2, 'NonCopyBackWriteData': 3,
            'CompData': 4, 'SnpRespDataPtl': 5, 'SnpRespDataFwded': 6,
            'WriteDataCancel': 7, 'DataSepResp': 0xb, 'NCBWrDataCompAck': 0xc},
}


def line_data(value):
    if isinstance(value, int):
        return value.to_bytes(8, 'little').hex() + '00' * 56
    assert len(value) == 128, 'a line must contain exactly 64 bytes'
    return value.lower()


@dataclass
class Message:
    start: int
    end: int
    id: str
    txid: str
    rxid: str


@dataclass
class DataGroup:
    end: int
    ids: list

    @property
    def id(self):
        return self.ids[0]


class Scenario:
    def __init__(self, key, title, topic, summary, rule, pages):
        self.key, self.title, self.topic = key, title, topic
        self.summary, self.rule = summary, rule
        self.observed, self.protocol, self.threads, self.checkpoints = [], [], [], []
        self.roots, self.initial = {}, {}
        self.prerequisites = ['协议推演，非 gem5 实测；C 为示意代码，时间用于演示允许的交错。']
        self.requirements, self.results, self.outcome = [], {}, summary
        self.sources = [{'label': f'Issue H · {rule}（PDF p.{p}）',
                         'url': f'/reference/issue-h.pdf#page={p}'} for p in pages]
        self.attach = {a['node']: a['router'] for a in BASE['fabric']['attachments']}
        self.node_ids = {n['id']: n['nodeId'] for n in BASE['topology']}

    def thread(self, hart, title, code):
        self.threads.append({'id': f'thread-{hart}', 'hartId': hart, 'node': f'RN{hart}',
                             'title': title, 'language': 'c-model', 'code': code})

    def event(self, tick, kind, root, **kw):
        info = self.roots[root]
        event = {'type': 'observed', 'id': f'{self.key}:e{len(self.observed)}',
                 'seq': len(self.observed), 'tick': str(tick), 'kind': kind,
                 'rootId': root, 'legId': root + '/up', 'address': info['address'],
                 'origin': 'modeled', 'causeInstructionIds': [info['instruction']], **kw}
        self.observed.append(event)
        return event['id']

    def requester(self, name, hart, line, address='0x1000', tick=0):
        root = f'{self.key}:{name}'
        assert root not in self.roots
        identity = root + ':cpu'
        self.roots[root] = {'instruction': identity, 'address': address}
        self.event(tick, 'instruction', root, instructionId=identity,
                   instruction={'id': identity, 'hartId': hart, 'iteration': 0,
                                'dynamicSeq': str(len(self.roots)), 'pc': '0',
                                'sourceId': f'thread-{hart}', 'line': line, 'origin': 'modeled'})
        return root

    def seed(self, node, values_dict, address='0x1000'):
        self.initial.setdefault(node, {'lines': {}})['lines'][address] = values_dict

    def msg(self, tick, source, target, channel, opcode, root, fields=None, desc='',
            address=None, delay=0, leg=None, payload=None, be=None, producers=None):
        assert source in self.attach and target in self.attach
        assert tick >= 0 and delay >= 0
        leg = leg or root + '/up'
        address = address or self.roots[root]['address']
        mid = f'{self.key}:m{len(self.protocol)}'
        fields = {'opcode': OP[channel][opcode], 'SrcID': self.node_ids[source],
                  'TgtID': self.node_ids[target], **(fields or {})}
        for key in ('FwdNID', 'HomeNID', 'ReturnNID', 'StashNID'):
            if isinstance(fields.get(key), str) and fields[key] in self.node_ids:
                fields[key] = self.node_ids[fields[key]]
        common = {'messageId': mid, 'legId': leg, 'source': source, 'target': target,
                  'channel': channel, 'opcode': opcode, 'fields': fields,
                  'description': desc, 'address': address}
        if payload is not None:
            assert len(payload) % 2 == 0
            enable = be if be is not None else 'ff' * (len(payload) // 2)
            assert len(enable) == len(payload)
            common.update(payload=payload, validMask='ff' * (len(payload) // 2),
                          byteEnable=enable, payloadOrigin='modeled teaching values',
                          dataProducerInstructionIds=producers or [])
        tx = self.event(tick, 'flit', root, observation='TX', **common)
        a, b = self.attach[source], self.attach[target]
        x, y, dx, dy = int(a[1]), int(a[2]), int(b[1]), int(b[2])
        hops = []
        while x != dx or y != dy:
            nx = x + (1 if x < dx else -1) if x != dx else x
            ny = y if x != dx else y + (1 if y < dy else -1)
            hops.append((f'R{x}{y}', f'R{nx}{ny}'))
            x, y = nx, ny
        end = tick + 2000 + 2000 * len(hops) + delay
        for i, (a, b) in enumerate(hops):
            route_tick = tick + (end - tick) * (i + 1) // (len(hops) + 1)
            self.event(route_tick, 'route', root, messageId=mid, legId=leg,
                       fields={'fromRouter': a, 'toRouter': b}, address=address)
        rx = self.event(end, 'flit', root, observation='RX', **common)
        identity = f'{self.key}:p{len(self.protocol)}'
        event = {'type': 'protocol', 'id': identity, 'seq': len(self.protocol),
                 'kind': 'flit', 'rootId': root, **common,
                 'fields': {k: {'value': v, 'origin': 'modeled'} for k, v in fields.items()},
                 'sourceEventIds': [tx, rx], 'dependencies': [], 'anchorTick': str(tick),
                 'anchorSeq': 0, 'substep': 0, 'origin': 'modeled', 'rule': self.rule,
                 'causeInstructionIds': [self.roots[root]['instruction']]}
        self.protocol.append(event)
        return Message(tick, end, identity, tx, rx)

    def data(self, tick, source, target, opcode, root, resp, state, value=9, be=None,
             txnid=1, dbid=None, desc='', address=None, delay=0, leg=None, gap=1000,
             order=(0, 2), fields=None, producers=None, beat_delays=None):
        payload, mask = line_data(value), be or 'ff' * 64
        assert len(mask) == 128 and set(order) == {0, 2}
        ids, end = [], tick
        for i, data_id in enumerate(order):
            start = data_id * 32  # DataID indexes 16-byte chunks, hex is 2 chars/byte.
            beat_mask = mask[start:start + 64]
            be_bits = sum((1 << j) for j in range(32) if beat_mask[2*j:2*j+2] == 'ff')
            extra = {'Resp': resp, 'CacheState': state, 'PassDirty': bool(resp & 4),
                     'TxnID': txnid, 'DataID': data_id, 'BE': f'0x{be_bits:08x}'}
            if dbid is not None:
                extra['DBID'] = dbid
            message = self.msg(tick + i * gap, source, target, 'DAT', opcode, root,
                               fields={**extra, **(fields or {})}, desc=desc,
                               address=address, delay=(beat_delays or {}).get(data_id, delay), leg=leg,
                               payload=payload[start:start+64], be=beat_mask, producers=producers)
            ids.append(message.id)
            end = max(end, message.end)
        return DataGroup(end, ids)

    def state(self, tick, node, root, values_dict, address=None):
        return self.event(tick, 'state', root, node=node, state=values_dict,
                          address=address or self.roots[root]['address'])

    def wait(self, tick, node, root, reason, desc='', blocker=None):
        identity = f'{self.key}:wait{len(self.observed)}'
        self.event(tick, 'wait_start', root, node=node, waitId=identity,
                   reason=reason, description=desc, blocker=blocker)
        return identity

    def unwait(self, tick, node, root, waitId):
        return self.event(tick, 'wait_end', root, node=node, waitId=waitId)

    def done(self, tick, node, root):
        legs = {p['legId'] for p in self.protocol if p['rootId'] == root and
                node in (p['source'], p['target'])} or {root + '/up'}
        for leg in sorted(legs):
            self.event(tick, 'complete', root, node=node, legId=leg)

    def note(self, tick, root, text, node=None):
        return self.event(tick, 'note', root, node=node, description=text)

    def point(self, title, msg_or_id):
        self.checkpoints.append({'title': title, 'eventId': getattr(msg_or_id, 'id', msg_or_id)})

    def arrival(self, msg_or_id):
        """Checkpoint at reception; a data group means its last received beat."""
        identities = set(msg_or_id.ids if isinstance(msg_or_id, DataGroup) else
                         [getattr(msg_or_id, 'id', msg_or_id)])
        messages = [p for p in self.protocol if p['id'] in identities]
        assert len(messages) == len(identities)
        receive_ids = {p['sourceEventIds'][-1] for p in messages}
        return max((e for e in self.observed if e['id'] in receive_ids),
                   key=lambda e: int(e['tick']))['id']

    def write(self):
        self.observed.sort(key=lambda e: (int(e['tick']), e['seq']))
        for seq, event in enumerate(self.observed):
            event['seq'] = seq
        events = {e['id']: e for e in self.observed}
        for message in self.protocol:
            message['anchorSeq'] = events[message['sourceEventIds'][0]]['seq']
        # These are model integrity checks, not a claim of full CHI conformance.
        identities = {*events, *(p['id'] for p in self.protocol)}
        assert all(p['eventId'] in identities for p in self.checkpoints)
        instructions = {e['instructionId']: e for e in self.observed if e['kind'] == 'instruction'}
        threads = {t['id']: t for t in self.threads}
        for event in instructions.values():
            inst = event['instruction']
            assert 1 <= inst['line'] <= len(threads[inst['sourceId']]['code'].splitlines())
        for event in [*self.observed, *self.protocol]:
            assert all(c in instructions for c in event.get('causeInstructionIds', []))
        manifest = {k: copy.deepcopy(BASE[k]) for k in
                    ['type', 'schemaVersion', 'protocol', 'profile', 'lineBytes', 'dataWidth', 'topology', 'fabric']}
        manifest.update(title=self.title, description=self.summary, complete=True,
                        initialState=self.initial,
                        source={'kind': 'educational_model', 'generator': 'scripts/model-teaching-specials.py'},
                        clock={'tickHz': '1000000000000'},
                        sam={'description': '教学预设地址映射；本例不声明来自实测 SAM。'})
        # Keep the code panel legible; full bytes remain in initialState and DAT.
        initial_summary = {}
        for node, entry in self.initial.items():
            initial_summary[node] = {}
            for address, state in entry['lines'].items():
                summary = {k: state[k] for k in ('state', 'sfState', 'owner') if state.get(k) is not None}
                if state.get('data'):
                    summary['首8字节'] = state['data'][:16]
                if state.get('validMask'):
                    summary['有效字节'] = sum(state['validMask'][i:i+2] == 'ff' for i in range(0, len(state['validMask']), 2))
                initial_summary[node][address] = summary
        manifest['litmus'] = {'name': self.title, 'threads': self.threads,
                              'initial': initial_summary,
                              'results': {'性质': '协议推演终态，非 CPU 执行结果', **self.results}}
        identity = 'teach-special-' + self.key
        manifest['teaching'] = {'id': identity, 'group': 'hazards', 'topic': self.topic,
                               'evidence': 'protocol-model', 'summary': self.summary,
                               'prerequisites': self.prerequisites, 'requirements': self.requirements,
                               'outcome': self.outcome, 'focusRoots': list(self.roots),
                               'checkpoints': self.checkpoints, 'sources': self.sources}
        filename = identity + '.chi.jsonl'
        (DEST / filename).write_text('\n'.join(json.dumps(r, ensure_ascii=False, separators=(',', ':'))
                                   for r in [manifest, *self.observed, *self.protocol]) + '\n', encoding='utf-8')
        code_dir = ROOT / 'examples/teaching'
        code_dir.mkdir(exist_ok=True, parents=True)
        (code_dir / (self.key + '.c')).write_text(
            '// Protocol teaching pseudocode; NOT a compiled CPU test.\n\n' +
            '\n'.join('// ' + t['title'] + '\n' + t['code'] for t in self.threads), encoding='utf-8')
        return {'id': identity, 'title': self.title, 'file': filename, 'description': self.summary,
                'group': 'hazards', 'topic': self.topic, 'evidence': 'protocol-model'}
