"""Collect checked end-to-end evidence; fail if a required result is absent."""
import json
import re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
def read(relative):return json.loads((ROOT/relative).read_text(encoding='utf-8'))
def log(relative):return re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',(ROOT/relative).read_text(encoding='utf-8'))
protocol=read('build/litmus-protocol/acceptance.json')
stress=read('build/litmus-stress/acceptance.json')
assert protocol['coverage']=='19/19'
assert stress['crossCoreDataChecks']==128 and stress['retryAcks']>0 and stress['victims']>0
invariance=read('build/qa/litmus-invariance.json')
assert all(result['observerInvariant'] for result in invariance.values())
browser=read('build/qa/litmus-browser-report.json')
assert browser['coverage']=='19/19' and browser['twoStageSAM'] and browser['measuredCallSiteHighlight'] and not browser['pageErrors']
performance=read('build/qa/browser-report.json')
assert performance['events']==100000 and performance['visibleFlits']==200
assert min(performance['fps'])>=30 and not performance['pageErrors']
native={}
for platform,file in [('windows','build/qa/native-extended.log'),('linux','build/qa/native-extended-linux.log')]:
    output=log(file);count=int(re.search(r'Ran (\d+) tests',output)[1]);assert count==47 and output.rstrip().endswith('OK')
    native[platform]=count
frontend=int(re.search(r'Tests\s+(\d+) passed',log('build/qa/frontend-extended.log'))[1]);assert frontend>=14
gem5={}
for name,file,expected in [('FlowGraph','build/qa/components.log',3),('HNF','build/qa/hnf-regression.log',62),('RNF','build/qa/rnf-regression.log',25)]:
    count=int(re.search(r'\[\s*PASSED\s*\]\s+(\d+) tests',log(file))[1]);assert count==expected;gem5[name]=count
legacy=read('build/qa/protocol-audit.json');assert legacy['passed'] and len(legacy['sessions'])==9
rotations=[];homes=set();memories=set()
for rotation in range(4):
    name='litmus-iriw'+(f'-rotation-{rotation}' if rotation else '')
    records=[json.loads(line) for line in (ROOT/f'public/demos/{name}.chi.jsonl').read_text(encoding='utf-8').splitlines()]
    manifest=records[0];physical=int(manifest['litmus']['run']['sharedPhysical'],0)
    rotations.append({'session':name,'sharedPhysical':hex(physical)})
    for event in records[1:]:
        if event['type']=='observed' and event['kind']=='sam' and int(event['address'],0) in (physical,physical+64):
            (homes if event.get('node','').startswith('RN') else memories).add(event['target'])
assert homes=={'HN0','HN1','HN2','HN3'} and memories=={'SN0','SN1'}
report={'status':'passed','cpuTransactions':protocol,'fourCoreStress':stress,
        'observerInvariance':invariance,'attribution':read('build/qa/litmus-attribution.json'),
        'browser':browser,'teaching':{'captures':read('build/qa/teaching-captures.json'),'browser':read('build/qa/teaching-browser-report.json'),'modeledCases':3},'replay':read('build/qa/replay-browser-report.json'),'performance':performance,'regressions':{'native':native,'frontend':frontend,'gem5':gem5},
        'legacySessions':9,'independentLegacyChecks':sum(rule['checks'] for rule in legacy['rules'].values()),
        'addressRotations':rotations,'sharedDataHomes':sorted(homes),'sharedDataMemories':sorted(memories),
        'scope':'Finite gem5 behavior model and actual CPU programs; not RTL co-simulation or full Issue H certification.'}
path=ROOT/'build/qa/extended-acceptance.json'
path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'status':report['status'],'transactions':'19/19','meanFps':performance['meanFps'],
                  'regressions':report['regressions'],'report':str(path)},ensure_ascii=False,indent=2))
