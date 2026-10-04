"""Compare observer-off/on real runs without wall-clock or host statistics."""
import csv
import json
import sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
scenario = sys.argv[1] if len(sys.argv)>1 else 'miss-hit'
assert scenario in ['miss-hit','retry']
runtime=root/'build/runtime' if scenario=='miss-hit' else root/'build/runtime-retry'
off=runtime/'off'
on=runtime/'on'
assert json.loads((off/'flowgraph-result.json').read_text()) == json.loads((on/'flowgraph-result.json').read_text())
assert (off/'rnf-latency.csv').read_text() == (on/'rnf-latency.csv').read_text(), 'RN transaction results/ticks changed'
with (on/'rnf-latency.csv').open() as stream:
    latency_rows=list(csv.DictReader(stream))
assert len(latency_rows)==2 and all(row['status']=='completed' for row in latency_rows), 'Expected two completed RN reads'
def stats(path):
    result={}
    for line in (path/'stats.txt').read_text().splitlines():
        parts=line.split()
        if len(parts)>=2 and (parts[0].startswith('system.') or parts[0] in ['simTicks','simInsts','simOps','finalTick']):
            result[parts[0]]=parts[1:2]
    return result
assert stats(off)==stats(on), 'Architectural/component counters changed'
records=[json.loads(line) for line in (root/f'examples/coarse/runtime-{scenario}.coarse.jsonl').read_text(encoding='utf-8').splitlines()]
observed=records[1:]
assert any(e.get('kind')=='sam' for e in observed)
lookups=[e['fields']['slcHit'] for e in observed if e.get('kind')=='lookup']
assert lookups==[False,True], lookups
requests=[e for e in observed if e.get('opcode')=='ReadShared' and e.get('observation')=='TX']
assert len(requests)==(3 if scenario=='retry' else 2), len(requests)
if scenario=='retry':
    rejected=[e for e in observed if e.get('opcode')=='RetryAck' and e.get('observation')=='TX']
    grants=[e for e in observed if e.get('opcode')=='PCrdGrant' and e.get('observation')=='TX']
    assert len(rejected)==len(grants)==1, (len(rejected),len(grants))
    retry=requests[-1]
    assert requests[-2]['legId']==retry['legId'] and requests[-2]['attemptId']!=retry['attemptId']
    assert retry['fields']['allowRetry']==0 and retry['fields']['pcrdtype']==rejected[0]['fields']['pcrdtype']
    assert sum(int(row['retry_count']) for row in latency_rows)==1
data=[e for e in observed if e.get('channel')=='DAT' and e.get('observation')=='TX']
assert len(data)==6 and all(e['validMask']=='ff'*32 for e in data), 'Expected two SN beats and four HN beats'
assert len(set(e['payload'] for e in data))==1, 'Memory and HNF returned different data'
assert {e['source'] for e in data}=={'HN0','SN0'}
report={'scenario':scenario,'observerInvariant':True,'lookupHitSequence':lookups,'requests':len(requests),'dataBeats':len(data),
        'completedRnTransactions':len(latency_rows),
        'result':json.loads((on/'flowgraph-result.json').read_text()),'checked':['ticks','RN latency CSV','system statistics','known data conservation']}
report_name='runtime-report.json' if scenario=='miss-hit' else 'runtime-retry-report.json'
(root/'build/qa'/report_name).write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
