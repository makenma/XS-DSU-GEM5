"""Independent checks of saved sessions against the supplied Issue H manual.

Page numbers below are the printed/PDF page numbers in the supplied 716-page PDF.
This audits the finite demo profile, not full Issue H conformance.
"""
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

root = Path(__file__).resolve().parents[1]
rules = {
    'opcode': 'B13.10.18, tables B13.12/B13.14/B13.15/B13.16, pp. 482-487',
    'return_fields': 'B2.4.4/B2.4.10, pp. 104-107',
    'id_lifetime': 'B2.4.2/B2.4.3, pp. 102-104',
    'write_dbid': 'B2.5.3, pp. 121-127',
    'compack': 'B2.5.1.4/B2.7.3, pp. 116-118, 137-139',
    'order_receipt': 'B2.5.1.4/B2.7.5, pp. 117, 140-141',
    'memory_attributes': 'B2.8.3.5, pp. 151-152',
    'dataid_be': 'B2.9.3/B2.9.4, pp. 163-165',
    'retry': 'B2.10.2/B2.10.3, pp. 181-182',
    'provenance': 'FlowGraph evidence contract; observed fields must be preserved',
}
opcodes = {
    'REQ': {'ReadShared':1,'ReadOnce':3,'ReadNoSnp':4,'ReadUnique':7,'WriteCleanFull':0x17,
            'WriteUniqueFull':0x19,'WriteBackFull':0x1b,'WriteNoSnpPtl':0x1c,'WriteNoSnpFull':0x1d},
    'RSP': {'SnpResp':1,'CompAck':2,'RetryAck':3,'Comp':4,'CompDBIDResp':5,'DBIDResp':6,'PCrdGrant':7,'ReadReceipt':8},
    'DAT': {'SnpRespData':1,'CopyBackWriteData':2,'NonCopyBackWriteData':3,'CompData':4},
    'SNP': {'SnpShared':1,'SnpClean':2,'SnpOnce':3,'SnpNotSharedDirty':4,'SnpUnique':7,'SnpCleanInvalid':9,'SnpMakeInvalid':10},
}
counts, issues, sessions = Counter(), [], []
def check(ok, rule, file, event, message):
    counts[rule] += 1
    if not ok: issues.append({'session':file,'event':event,'rule':rule,'message':message})
def value(event, field): return event.get('fields',{}).get(field,{}).get('value')
for path in sorted((root/'public/demos').glob('*.chi.jsonl')):
    records=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    manifest=records[0]
    # This independent audit checks the original full-line profile only.
    # CPU-generated 1.1 sessions have separate checks in check-litmus.py.
    if manifest['profile']!='h-core-256': continue
    nodes={n['id']:n for n in manifest['topology']}
    by_id={e['id']:e for e in records[1:]}; requests={}; latest={}; beats=defaultdict(list); live={}
    flits=[e for e in records if e['type']=='protocol' and e['kind']=='flit']
    for event in (e for e in records if e['type']=='protocol'):
        eid=event['id']; key=(event['legId'],event.get('attemptId','0')); name=event['opcode']
        def verify(ok, rule, message): check(ok,rule,path.name,eid,message)
        if event['kind']=='binding':
            field='DBID' if 'DBID' in event['fields'] else 'TxnID'
            slot=(field,value(event,'Namespace'),value(event,field))
            if value(event,'Action')=='allocate':
                verify(slot not in live,'id_lifetime','ID allocated twice in a live namespace'); live[slot]=key
            else:
                verify(live.get(slot)==key,'id_lifetime','Release does not match its lifetime'); live.pop(slot,None)
            continue
        if event['kind']!='flit': continue
        verify(value(event,'Opcode')==opcodes[event['channel']].get(name),'opcode','Issue H opcode mismatch')
        verify('tick' not in event and any(by_id[s]['tick']==event['anchorTick'] for s in event['sourceEventIds']),
               'provenance','Protocol time must be anchored to an observed event')
        for wire, raw in [('AllowRetry','allowRetry'),('MemAttr','memattr'),('SnpAttr','snpattr'),('ExpCompAck','expCompAck')]:
            field=event['fields'].get(wire,{})
            if field.get('origin')=='observed':
                verify(field['value']==by_id[field['sourceEventId']]['fields'][raw],'provenance',wire+' differs from observation')
        if event['channel']=='REQ':
            requests[key]=event;latest[event['legId']]=event
            downstream=nodes[event['source']]['role']=='HN-F' and nodes[event['target']]['role']=='SN-F'
            verify(value(event,'ReturnNID')==(nodes[event['source']]['nodeId'] if downstream else 0), 'return_fields','ReturnNID applicability')
            verify(value(event,'ReturnTxnID')==(value(event,'TxnID') if downstream and name=='ReadNoSnp' else 0),'return_fields','ReturnTxnID mapping')
            verify(not downstream or value(event,'Order') in [0,1],'order_receipt','Reserved HN-F/SN-F Order')
            verify(not downstream or value(event,'ExpCompAck')==0,'compack','Subordinate must not require CompAck')
            verify(name not in ['ReadShared','ReadUnique'] or value(event,'ExpCompAck')==1,'compack','Mandatory read CompAck missing')
            verify(value(event,'AllowRetry')!=1 or value(event,'PCrdType')==0,'retry','First-attempt PCrdType is not zero')
            if value(event,'AllowRetry')==0:
                grants=[by_id[d] for d in event['dependencies'] if by_id[d].get('opcode')=='PCrdGrant']
                verify(any(g['source']==event['target'] and g['target']==event['source'] and value(g,'PCrdType')==value(event,'PCrdType') for g in grants),'retry','Missing node-pair/type credit')
            parent=latest.get(event.get('parentLegId'))
            if downstream and parent:
                expected=13 if name.startswith('Write') else value(parent,'MemAttr')
                verify(value(event,'MemAttr')==expected,'memory_attributes','Downstream attributes are not preserved / eviction attributes invalid')
        elif event['channel']=='DAT':
            beats[key].append(value(event,'DataID'))
            verify(value(event,'DataID') in [0,2],'dataid_be','Invalid 256-bit DataID')
            if name in ['CopyBackWriteData','NonCopyBackWriteData']:
                source=by_id[event['fields']['TxnID']['sourceEventId']]
                verify(source['opcode'] in ['DBIDResp','CompDBIDResp'] and value(event,'TxnID')==value(source,'DBID'),'write_dbid','Write DAT does not use granted DBID')
            if name=='CompData': verify(event['fields']['BE'].get('applicable') is False,'dataid_be','Read BE must be inapplicable')
        elif name=='CompAck':
            source=by_id[event['fields']['TxnID']['sourceEventId']]
            verify(value(event,'TxnID')==value(source,'DBID'),'compack','Ack does not use completion DBID')
        elif name=='ReadReceipt':
            verify(value(event,'DBID')==0 and value(event,'TxnID')==value(requests[key],'TxnID'),'order_receipt','ReadReceipt ID/DBID mapping')
    for key, ids in beats.items(): check(sorted(ids)==[0,2],'dataid_be',path.name,str(key),'Each demonstrated transfer needs exactly two unique beats')
    check(not live,'id_lifetime',path.name,'end','Demonstrated complete transactions retained protocol IDs')
    failures=[e for e in records if e['type']=='validation' and e['status']=='fail']
    sessions.append({'file':path.name,'source':manifest['source']['kind'],'flits':len(flits),'reportedFailures':len(failures)})
    if failures: issues.append({'session':path.name,'message':'Refiner validation failures','count':len(failures)})
pdf=root/'IHI0050H_amba_chi_architecture_spec.pdf'
report={'manual':pdf.name,'manualSha256':hashlib.sha256(pdf.read_bytes()).hexdigest(),
        'scope':'Finite h-core-256 examples; not a complete Issue H conformance certification',
        'sessions':sessions,'rules':{name:{'reference':ref,'checks':counts[name]} for name,ref in rules.items()},
        'issues':issues,'passed':not issues}
(root/'build/qa').mkdir(parents=True,exist_ok=True)
(root/'build/qa/protocol-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'sessions':len(sessions),'checks':sum(counts.values()),'issues':issues},ensure_ascii=False))
raise SystemExit(bool(issues))
