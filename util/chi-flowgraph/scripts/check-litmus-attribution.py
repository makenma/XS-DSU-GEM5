"""Check dynamic CPU evidence and explicit propagation in real gem5 captures."""
import collections
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
reports={}
for variant in ('iriw','protocol','stress'):
    folder=ROOT/'build'/f'litmus-{variant}'
    raw=[json.loads(line) for line in (folder/'raw.jsonl').read_text(encoding='utf-8').splitlines()]
    manifest,events=raw[0],raw[1:]
    instructions={e['instructionId']:e for e in events if e['kind']=='instruction'}
    assert len(instructions)==sum(e['kind']=='instruction' for e in events), 'reused dynamic identity'
    pc_map=manifest['litmus']['pcMap']
    for identity,event in instructions.items():
        instruction=event['instruction']
        assert instruction['id']==identity
        assert identity==f'cpu:{instruction["hartId"]}:{instruction["iteration"]}:{instruction["dynamicSeq"]}'
        mapped=pc_map.get(f'{instruction["hartId"]}:{instruction["pc"]}')
        if mapped:
            assert mapped['sourceId']==instruction.get('sourceId') and mapped['line']==instruction.get('line')
    root_causes=collections.defaultdict(set)
    for event in events:
        causes=set(event.get('causeInstructionIds',[]))
        producers=set(event.get('dataProducerInstructionIds',[]))
        assert causes<=instructions.keys(), ('cause without CPU evidence',event['id'],causes-instructions.keys())
        assert producers<=instructions.keys(), ('writer without CPU evidence',event['id'],producers-instructions.keys())
        root_causes[event['rootId']].update(causes)
        if event['kind']=='instruction_cancel':assert event['instructionId'] in instructions
        if event['kind']=='instruction_context':
            instruction=instructions[event['instructionId']]['instruction']
            site=event['fields']['callSite']
            mapped=pc_map[f'{instruction["hartId"]}:{site["pc"]}']
            assert site['sourceId']==mapped['sourceId'] and site['line']==mapped['line']
            assert site['pc']!=instruction['pc'], 'caller must not replace doorbell identity'
    messages={e['messageId'] for e in events if e['kind']=='flit'}
    for event in events:
        if event['kind']=='route':assert event['messageId'] in messages
        if event['kind']=='sam' and event.get('node','').startswith('HN'):
            fields=event['fields'];address=int(event['address'],0)
            parities=[(address&int(mask)).bit_count()%2 for mask in fields['masks']]
            index=sum(parity<<bit for bit,parity in enumerate(parities))
            assert fields['parities']==parities and fields['index']==index
            assert event['target']==f'SN{index}'
        if event['kind']=='sam' and event.get('node','').startswith('RN'):
            fields=event['fields'];width=(len(fields['homeNodeIds'])-1).bit_length();address=int(event['address'],0)
            index=0
            for output in range(width):
                parity=0
                for bit in range(6+output,fields['paBits'],width):parity^=(address>>bit)&1
                index|=parity<<output
            assert fields['index']==index
            node=next(n for n in manifest['topology'] if n['id']==event['target'])
            assert node['nodeId']==fields['homeNodeIds'][index]==fields['selectedNodeId']
    expanded=[json.loads(line) for line in (folder/'session.chi.jsonl').read_text(encoding='utf-8').splitlines()]
    for event in expanded:
        if event['type']=='protocol':
            assert set(event.get('causeInstructionIds',[]))==root_causes[event['rootId']], ('lost or invented attribution',event['id'])
            assert set(event.get('dataProducerInstructionIds',[]))<=instructions.keys()
    pc_counts=collections.Counter((e['instruction']['hartId'],e['instruction']['pc']) for e in instructions.values())
    request_causes={identity for e in events if e['kind']=='flit' and e.get('channel')=='REQ' for identity in e.get('causeInstructionIds',[])}
    counts=dict(dynamicInstructions=len(instructions),repeatedPCs=sum(count>1 for count in pc_counts.values()),
                mergedCauseObservations=sum(len(e.get('causeInstructionIds',[]))>1 for e in events),
                multiBranchInstructions=sum(count>1 for count in collections.Counter(identity for e in events if e['kind']=='allocate' for identity in e.get('causeInstructionIds',[])).values()),
                instructionsWithoutCHI=len(instructions.keys()-request_causes),
                cancellations=sum(e['kind']=='instruction_cancel' for e in events),
                measuredCallSites=sum(e['kind']=='instruction_context' for e in events),
                historicalWriterObservations=sum(bool(e.get('dataProducerInstructionIds')) for e in events))
    if variant=='protocol':assert all(counts[key]>0 for key in counts), counts
    if variant=='stress':
        assert any(e.get('payloadOrigin')=='hnf_dirty_victim' and e.get('dataProducerInstructionIds') for e in events)
        assert any(e.get('opcode')=='RetryAck' and e.get('causeInstructionIds') for e in events)
    reports[variant]=counts
(ROOT/'build/qa/litmus-attribution.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(reports,ensure_ascii=False,indent=2))
