import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { parseSession } from '../../src/trace/engine';
import { TransportTimeline } from '../../src/trace/playback';
import { TARGET_OPCODES } from '../../src/components/CoveragePanel';

const catalog=JSON.parse(readFileSync('public/demos/catalog.json','utf8')) as {id:string;file:string;group?:string;evidence?:string}[];
const load=(id:string)=>parseSession(readFileSync(`public/demos/${id}.chi.jsonl`,'utf8'));
describe('teaching catalog and evidence',()=>{
  it('has one completed CPU RN request with an actual SLC miss for all 20 supported teaching opcodes',()=>{
    const basics=catalog.filter(d=>d.group==='basics');expect(basics).toHaveLength(20);
    const covered=new Set<string>();
    for(const demo of basics){
      const s=load(demo.id),lesson=s.manifest.teaching!;
      expect(lesson.evidence).toBe('gem5');expect(lesson.lookup?.slcHit).toBe(false);
      const requests=s.observed.filter(e=>e.kind==='flit'&&e.observation==='TX'&&e.channel==='REQ'&&e.source?.startsWith('RN'));
      expect(requests,demo.id).toHaveLength(1);const req=requests[0];covered.add(req.opcode!);
      expect(req.causeInstructionIds?.length).toBeGreaterThan(0);
      expect(s.observed.some(e=>e.kind==='complete'&&e.node===req.source&&e.legId===req.legId)).toBe(true);
      expect(s.manifest.fabric?.routers).toHaveLength(6);
      expect(new TransportTimeline(s).sample(BigInt(req.tick)).total).toBeGreaterThan(0);
      expect(s.validations.filter(v=>v.engine==='import'),demo.id).toEqual([]);
    }
    expect([...covered].sort()).toEqual([...TARGET_OPCODES,'ReadShared'].sort());
  });
  it('preserves all four measured HNF lookup combinations and actual competing roots',()=>{
    for(const bits of ['00','01','10','11']){
      const s=load('teach-hnf-'+bits),condition={slcHit:bits[0]==='1',sfHit:bits[1]==='1'};
      expect(s.manifest.teaching?.lookup).toEqual(condition);
      expect(s.observed.some(e=>e.kind==='lookup'&&e.fields?.slcHit===condition.slcHit&&e.fields?.sfHit===condition.sfHit)).toBe(true);
    }
    const s=load('teach-hazard-same-address');expect(s.manifest.teaching?.focusRoots).toHaveLength(2);
    const wait=s.observed.find(e=>e.kind==='wait_start'&&e.reason==='same_address')!;
    expect(s.observed.some(e=>e.kind==='wait_end'&&e.waitId===wait.waitId&&BigInt(e.tick)>BigInt(wait.tick))).toBe(true);
    expect(s.observed.filter(e=>e.kind==='flit'&&e.channel==='REQ'&&e.observation==='TX'&&e.source?.startsWith('RN')).length).toBeGreaterThanOrEqual(2);
    const released=s.observed.find(e=>e.kind==='wait_end'&&e.waitId===wait.waitId)!;
    expect(s.observed.some(e=>e.kind==='complete'&&e.node===wait.node&&e.address===wait.address&&e.tick===released.tick&&e.rootId!==wait.rootId)).toBe(true);
  });
  it('selects an actual dirty-victim writeback and checkpoints the parent completion',()=>{
    const s=load('teach-hazard-victim');
    const write=s.observed.find(e=>e.kind==='flit'&&e.observation==='TX'&&e.opcode==='WriteNoSnpFull'&&e.legId.includes('/victim:'))!;
    expect(write).toBeDefined();
    expect(s.observed.some(e=>e.kind==='flit'&&e.observation==='RX'&&e.opcode==='NonCopyBackWriteData'&&e.legId===write.legId)).toBe(true);
    const done=s.manifest.teaching!.checkpoints.find(p=>p.title==='事务完成')!;
    const event=s.eventById[done.eventId];
    expect(event.kind).toBe('complete'); expect(event.legId).toBe(event.rootId+'/up');
    const observed=event as import('../../src/trace/types').ObservedEvent;
    expect(BigInt(observed.tick)).toBeGreaterThan(BigInt(write.tick));
  });
  it('models post-snoop copyback states and masks without claiming CPU evidence',()=>{
    for(const [name,state,resp] of [['early-unique','I',0],['early-shared','SC',1],['early-once','UD',6]] as const){
      const s=load('teach-hazard-'+name);
      expect(s.manifest.source.kind).toBe('educational_model');expect(s.manifest.teaching?.evidence).toBe('protocol-model');
      expect(s.observed.every(e=>e.origin==='modeled')).toBe(true);
      const copy=s.protocol.filter(e=>e.opcode==='CopyBackWriteData'),snoop=s.protocol.filter(e=>e.opcode==='SnpRespData');
      expect(copy).toHaveLength(2);expect(snoop).toHaveLength(2);
      for(const e of copy){
        expect(e.fields.CacheState.value).toBe(state);expect(e.fields.Resp.value).toBe(resp);
        expect(e.origin).toBe('modeled');expect(e.fields.TxnID.value).toBe(60);
        expect(e.byteEnable).toBe((state==='I'?'00':'ff').repeat(32));
        if(state==='I')expect(e.payload).toBe('00'.repeat(32));
      }
      expect(BigInt(copy[0].anchorTick)).toBeGreaterThan(BigInt(snoop[1].anchorTick));
      expect(snoop[0].payload?.startsWith('0900000000000000')).toBe(true);
      expect(new TransportTimeline(s).sample(1000n).total).toBe(2);
      const wb=s.observed.find(e=>e.opcode==='WriteBackFull'&&e.observation==='RX')!;
      const read=s.observed.find(e=>e.channel==='REQ'&&e.source==='RN0'&&e.observation==='RX')!;
      expect(BigInt(wb.tick)).toBeGreaterThan(BigInt(read.tick));
    }
  });
});
