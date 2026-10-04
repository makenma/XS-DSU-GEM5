"""Validate and package actual gem5 captures, never synthesize protocol traffic."""
import argparse
import collections
import json
from pathlib import Path
import subprocess

TARGETS = dict(ReadOnce=3, ReadNoSnp=4, ReadUnique=7, CleanShared=8,
               CleanInvalid=9, MakeInvalid=10, MakeUnique=12, Evict=13,
               WriteEvictFull=0x15, WriteUniquePtl=0x18, WriteUniqueFull=0x19,
               WriteBackFull=0x1b, WriteNoSnpPtl=0x1c, WriteNoSnpFull=0x1d,
               StashOnceShared=0x22, StashOnceUnique=0x23,
               ReadOnceCleanInvalid=0x24, ReadOnceMakeInvalid=0x25,
               ReadNotSharedDirty=0x26)
ROOT = Path(__file__).resolve().parents[1]

def check(folder, variant, binary, publish, session_id=None):
    folder = Path(folder)
    result = json.loads((folder/'flowgraph-result.json').read_text(encoding='utf-8'))
    assert result['done'] == [1]*4 and not any(result['errors']), result
    records = [json.loads(line) for line in (folder/'raw.jsonl').read_text(encoding='utf-8').splitlines()]
    manifest, events = records[0], records[1:]
    assert manifest['schemaVersion'] == '1.1'
    roles = collections.Counter(n['role'] for n in manifest['topology'])
    assert roles == {'RN-F':4, 'HN-F':4, 'SN-F':2}, roles
    assert len(manifest['fabric']['routers']) == 6 and len(manifest['fabric']['links']) == 7
    instructions = {e['instructionId']:e for e in events if e['kind']=='instruction'}
    requests = {}
    for e in events:
        if e['kind']=='flit' and e.get('channel')=='REQ' and e['source'].startswith('RN') and e['target'].startswith('HN'):
            requests[e['legId']] = e
    completions = {e['legId'] for e in events if e['kind']=='complete' and e.get('node','').startswith('RN')}
    counts = collections.Counter(e['opcode'] for leg,e in requests.items() if leg in completions and e.get('causeInstructionIds'))
    for request in requests.values():
        for identity in request.get('causeInstructionIds',[]):
            assert identity in instructions, ('missing CPU execution evidence', identity)
    report = {'variant':variant,'done':result['done'],'tick':result['tick'],
              'cpuInitiatedCompleted':dict(counts),'instructions':len(instructions),
              'routes':sum(e['kind']=='route' for e in events)}
    assert report['routes'] > 0
    if variant=='protocol':
        assert set(TARGETS) <= set(counts), ('missing completed CPU types',set(TARGETS)-set(counts))
        expected = [0,0,0,0,0,0xaaaaaaaaaaaaaaaa,0xaa22aa44aa66aa88,
                    0x123456789abcdef0,0x987654321,0xaa22aa44aa66aa88,
                    0x1111,0x1111,0x2222,0,0x3333]
        assert result['registers'][0][:len(expected)] == expected, result['registers'][0]
        assert result['registers'][1][:2] == [42,43], result['registers'][1]
        assert result['registers'][0][16:20] == [0,0x4444,0x4444,0x5555], result['registers'][0][16:20]
        targets = {e['target'] for e in requests.values()}
        sn_targets = {e['target'] for e in events if e['kind']=='flit' and e.get('channel')=='REQ' and e.get('source','').startswith('HN')}
        assert targets == {'HN0','HN1','HN2','HN3'} and sn_targets == {'SN0','SN1'}, (targets,sn_targets)
        assert sum(e['kind']=='allocate' and e.get('implicitDataPull',False) for e in events)==2
        assert any(e['kind']=='stash_ignored' for e in events)
        for pull in [e for e in events if e['kind']=='allocate' and e.get('implicitDataPull',False)]:
            installed=[e for e in events if e['kind']=='state' and e.get('node')=='RN1' and e['legId']==pull['legId']]
            assert installed and installed[-1]['state']['state']==('UC' if pull['opcode']=='ReadUnique' else 'SC')
        wb=next(e for e in requests.values() if e['opcode']=='WriteBackFull')
        copies=[e for e in events if e['kind']=='flit' and e.get('opcode')=='CopyBackWriteData' and e['legId']==wb['legId']]
        assert copies and all(e.get('dataProducerInstructionIds') for e in copies)
        # A later CPU read hits the actual installed target L2; the pull has no REQ.
        for line in (11,12):
            addr=hex(int(result['sharedPhysical'],16)+line*64)
            assert not any(e['source']=='RN1' and e['address']==addr for e in requests.values()), ('stash target missed',line)
        for e in events:
            if e['kind']=='state' and e.get('node')=='RN0' and e.get('state',{}).get('state')=='SD':
                assert not any(r['opcode']=='ReadNotSharedDirty' and r['rootId']==e['rootId'] for r in requests.values())
        report['coverage']='19/19'
        # JSON numbers would round these RV64 results in the browser.
        manifest['litmus']['results']={'P0':[hex(value) for value in expected],'P1 stash reads':[42,43],
                                     'discard / dirty clean':[hex(value) for value in result['registers'][0][16:20]],
                                     'checks':'partial BE / dirty latest / clean invalidation / discard / stash hit / refusal passed'}
    elif variant=='stress':
        assert all(r[0]==0 for r in result['registers']), result
        retry=sum(e.get('opcode')=='RetryAck' and e.get('observation')=='TX' for e in events)
        victims=[e for e in events if e['kind']=='allocate' and e.get('opcode')=='SLC/SF victim']
        assert retry and victims, (retry,len(victims))
        report.update(retryAcks=retry,victims=len(victims),crossCoreDataChecks=128)
        manifest['litmus']['results']={'四核错误数':[r[0] for r in result['registers']],'交叉读取':128,'RetryAck':retry,'victim':len(victims)}
    else:
        manifest['litmus']['results']={f'P{i}':result['registers'][i][:2] for i in (2,3)}
        assert len({e['instruction']['hartId'] for e in instructions.values()}) == 4
    manifest['litmus']['run']={'tick':result['tick'],'done':result['done'],'sharedPhysical':result['sharedPhysical']}
    if variant=='iriw' and manifest['litmus'].get('rotation'):
        rotation=manifest['litmus']['rotation']
        manifest['title']=f'四核 IRIW · 地址轮换 {rotation}'
        manifest['description']+=f' 共享物理页 {result["sharedPhysical"]}，分配器轮换 {rotation} 页。'
    enriched=folder/'input.jsonl'
    enriched.write_text('\n'.join(json.dumps(e,ensure_ascii=False) for e in records)+'\n',encoding='utf-8')
    target=folder/'session.chi.jsonl'
    subprocess.run([str(binary),'--input',str(enriched),'--output',str(target),'--profile','h-extended-256'],check=True)
    expanded=[json.loads(line) for line in target.read_text(encoding='utf-8').splitlines()]
    failed=[e for e in expanded if e['type']=='validation' and e['status']=='fail']
    assert not failed, failed[:10]
    report['protocolFlits']=sum(e['type']=='protocol' and e['kind']=='flit' for e in expanded)
    report['validationStatus']=dict(collections.Counter(e['status'] for e in expanded if e['type']=='validation'))
    (folder/'acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if publish:
        demos=ROOT/'public/demos';name=session_id or f'litmus-{variant}';filename=f'{name}.chi.jsonl'
        assert name.startswith('litmus-') and all(c.isalnum() or c=='-' for c in name), 'invalid session id'
        (demos/filename).write_text(target.read_text(encoding='utf-8'),encoding='utf-8')
        catalog=json.loads((demos/'catalog.json').read_text(encoding='utf-8'))
        catalog=[e for e in catalog if e['id']!=name]
        entry={'id':name,'title':manifest['title'],'description':manifest['description'],'file':filename}
        if name=='litmus-iriw':catalog.insert(0,entry)
        else:catalog.insert(1,entry)
        (demos/'catalog.json').write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('folder');p.add_argument('--variant',choices=['iriw','protocol','stress'],required=True)
    p.add_argument('--binary',type=Path,default=ROOT/'build/chi-refine.exe');p.add_argument('--publish',action='store_true')
    p.add_argument('--session-id',help='Distinct litmus-* catalog identity for an address-rotation run')
    a=p.parse_args();check(a.folder,a.variant,a.binary.resolve(),a.publish,a.session_id)
