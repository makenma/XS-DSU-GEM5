import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { parseSession, ReplayEngine } from '../../src/trace/engine';
import { TransportTimeline } from '../../src/trace/playback';
import { resolveCheckpoint } from '../../src/trace/navigation';
import CoveragePanel from '../../src/components/CoveragePanel';
import type { ObservedEvent, ProtocolEvent, Session } from '../../src/trace/types';

const keys = [
  'split-early-snoop', 'compack-overtake', 'retry-invalidated', 'multiple-snoops',
  'data-before-response', 'evict-snoop', 'early-pcredit', 'write-cancel',
  'ordered-writes', 'copy-at-home', 'dct-double', 'dct-fallback',
  'partial-snoop-merge', 'writeclean-old', 'make-read-unique',
];
const catalog = JSON.parse(readFileSync('public/demos/catalog.json', 'utf8')) as {id:string; file:string; evidence?:string}[];
const sessions = new Map<string, Session>();
function load(key:string):Session {
  let session = sessions.get(key);
  if (!session) {
    session = parseSession(readFileSync(`public/demos/teach-special-${key}.chi.jsonl`, 'utf8'));
    sessions.set(key, session);
  }
  return session;
}
const messages = (s:Session, opcode:string, source?:string, target?:string) => s.protocol.filter(p =>
  p.opcode === opcode && (!source || p.source === source) && (!target || p.target === target));
const field = (p:ProtocolEvent, name:string) => p.fields[name]?.value;
const start = (p:ProtocolEvent) => BigInt(p.anchorTick);
const rx = (s:Session, p:ProtocolEvent) => p.sourceEventIds.map(id => s.eventById[id])
  .find(e => e.type === 'observed' && e.observation === 'RX') as ObservedEvent;
const end = (s:Session, p:ProtocolEvent) => BigInt(rx(s, p).tick);
const lastEnd = (s:Session, packets:ProtocolEvent[]) => packets.reduce((max,p) => end(s,p) > max ? end(s,p) : max, 0n);
const line = (packets:ProtocolEvent[]) => [...packets].sort((a,b) => Number(field(a,'DataID')) - Number(field(b,'DataID')))
  .map(p => p.payload).join('');
const finalState = (s:Session, raw=false) => new ReplayEngine(s,raw).seek((raw?s.rawSteps:s.steps).length-1);
const cache = (s:Session, node:string, address='0x1000') => finalState(s).nodes[node].lines[address];

describe('reviewed special scenarios: usable evidence and replay', () => {
  it('publishes all fifteen reviewed cases exactly once', () => {
    const actual = catalog.filter(d => d.id.startsWith('teach-special-'));
    expect(actual.map(d=>d.id).sort()).toEqual(keys.map(k=>'teach-special-'+k).sort());
    expect(new Set(actual.map(d=>d.file)).size).toBe(keys.length);
  });

  it('continues to count all nineteen observed CPU opcodes in the real protocol run', () => {
    const s=parseSession(readFileSync('public/demos/litmus-protocol.chi.jsonl','utf8'));
    const rendered=renderToStaticMarkup(createElement(CoveragePanel,{session:s,onPick:()=>{}}));
    expect(rendered.match(/class="covered"/g) ?? []).toHaveLength(19);
    expect(rendered).toContain('19 / 19');
  });

  it.each(keys)('%s stays explicitly modeled and cannot increase CPU coverage', key => {
    const s=load(key);
    expect(s.manifest.source.kind).toBe('educational_model');
    expect(s.manifest.source.gem5Commit).toBeUndefined();
    expect(s.manifest.teaching?.evidence).toBe('protocol-model');
    expect(s.manifest.complete).toBe(true);
    expect(s.observed.length).toBeGreaterThan(0);
    expect(s.protocol.length).toBeGreaterThan(0);
    expect(s.observed.every(e=>e.origin==='modeled')).toBe(true);
    expect(s.protocol.every(e=>e.origin==='modeled' && Object.values(e.fields).every(f=>f.origin==='modeled'))).toBe(true);
    expect(s.validations.filter(v=>v.engine==='import')).toEqual([]);
    const rendered=renderToStaticMarkup(createElement(CoveragePanel,{session:s,onPick:()=>{}}));
    expect(rendered).not.toContain('class="covered"');
  });

  it.each(keys)('%s has resolvable code/checkpoints and releases waits in both views', key => {
    const s=load(key), lesson=s.manifest.teaching!;
    expect(lesson.checkpoints.length).toBeGreaterThan(0);
    expect(lesson.sources.some(source=>/issue-h\.pdf#page=\d+/.test(source.url))).toBe(true);
    for(const checkpoint of lesson.checkpoints) {
      const event=s.eventById[checkpoint.eventId];
      expect(event,checkpoint.title).toBeDefined();
      const tick=BigInt(event.type==='protocol'?event.anchorTick:event.tick);
      for(const raw of [false,true]) {
        const resolved=resolveCheckpoint(s,raw,checkpoint.eventId)!;
        expect(resolved,`${checkpoint.title}, raw=${raw}`).toBeDefined();
        expect(resolved.tick).toBe(tick);
        expect((raw?s.indexes.raw:s.indexes.refined)[resolved.id]).toBeDefined();
      }
    }
    // A receive checkpoint can select the same logical packet in refined view,
    // but its clock must remain at reception rather than jumping back to TX.
    const received=s.observed.find(e=>e.kind==='flit'&&e.observation==='RX')!;
    const atReceive=resolveCheckpoint(s,false,received.id)!;
    expect(atReceive.tick).toBe(BigInt(received.tick));
    const packet=s.eventById[atReceive.id] as ProtocolEvent;
    expect(atReceive.tick).toBeGreaterThan(BigInt(packet.anchorTick));
    const instructions=new Map(s.observed.filter(e=>e.kind==='instruction').map(e=>[e.instructionId,e.instruction!]));
    expect(instructions.size).toBeGreaterThan(0);
    for(const instruction of instructions.values()) {
      const thread=s.manifest.litmus!.threads.find(t=>t.id===instruction.sourceId)!;
      expect(thread).toBeDefined();
      expect(thread.language).toBe('c-model');
      const text=thread.code.split('\n')[instruction.line!-1];
      expect(text).toBeDefined();
      expect(text.trim()).not.toMatch(/^(\/\/|$)/);
    }
    for(const event of [...s.observed,...s.protocol]) {
      expect(event.causeInstructionIds?.length,event.id).toBeGreaterThan(0);
      for(const id of [...event.causeInstructionIds??[],...event.dataProducerInstructionIds??[]])
        expect(instructions.has(id),`${event.id} -> ${id}`).toBe(true);
    }
    for(const wait of s.observed.filter(e=>e.kind==='wait_start')) {
      const ends=s.observed.filter(e=>e.kind==='wait_end'&&e.waitId===wait.waitId);
      expect(ends,wait.waitId).toHaveLength(1);
      expect(BigInt(ends[0].tick)).toBeGreaterThanOrEqual(BigInt(wait.tick));
    }
    const refined=finalState(s), raw=finalState(s,true);
    expect(refined.nodes).toEqual(raw.nodes);
    for(const node of Object.values(refined.nodes)) expect(node.waits).toEqual({});
    expect(refined.active).toEqual({});
  });

  it.each(keys)('%s animates each TX/RX/route group as one packet', key => {
    const s=load(key), timeline=new TransportTimeline(s);
    const tx=s.observed.filter(e=>e.kind==='flit'&&e.observation==='TX');
    expect(timeline.transfers).toHaveLength(tx.length);
    expect(timeline.transfers).toHaveLength(s.protocol.length);
    expect(new Set(timeline.transfers.map(t=>t.id)).size).toBe(tx.length);
    for(const p of s.protocol) {
      const transfer=timeline.byEvent.get(p.id)!;
      expect(transfer).toBeDefined();
      expect(transfer.start).toBe(start(p));
      expect(transfer.end).toBe(end(s,p));
      expect(transfer.end).toBeGreaterThan(transfer.start);
      const route=s.observed.filter(e=>e.kind==='route'&&e.messageId===p.messageId);
      for(const e of route) {
        expect(timeline.byEvent.get(e.id)).toBe(transfer);
        expect(BigInt(e.tick)).toBeGreaterThan(transfer.start);
        expect(BigInt(e.tick)).toBeLessThan(transfer.end);
      }
      for(const id of p.sourceEventIds) expect(timeline.byEvent.get(id)).toBe(transfer);
      if(p.channel==='DAT') {
        expect(p.payload).toHaveLength(64);
        expect(p.byteEnable).toHaveLength(64);
        expect([0,2]).toContain(field(p,'DataID'));
      }
    }
  });
});

describe('special scenarios: protocol obligations', () => {
  it('allows early CompAck but holds the overtaking snoop until the entire split read arrives', () => {
    const s=load('split-early-snoop'), rsp=messages(s,'RespSepData')[0], data=messages(s,'DataSepResp');
    const ack=messages(s,'CompAck','RN0')[0], snp=messages(s,'SnpUnique')[0];
    const reply=messages(s,'SnpRespData','RN0');
    expect(start(ack)).toBeGreaterThanOrEqual(end(s,rsp));
    expect(end(s,ack)).toBeLessThan(lastEnd(s,data));
    expect(start(snp)).toBeGreaterThanOrEqual(end(s,ack));
    const firstBeat=data.find(p=>field(p,'DataID')===0)!;
    const secondBeat=data.find(p=>field(p,'DataID')===2)!;
    expect(end(s,firstBeat)).toBeLessThan(end(s,snp));
    expect(end(s,snp)).toBeLessThan(end(s,secondBeat));
    expect(start(secondBeat)).toBeLessThan(start(snp));
    expect(end(s,snp)).toBeLessThan(lastEnd(s,data));
    expect(start(reply[0])).toBeGreaterThanOrEqual(lastEnd(s,data));
    expect(field(ack,'TxnID')).toBe(field(rsp,'DBID'));
    expect(line(reply)).toBe(line(data));
    expect(cache(s,'RN0').state).toBe('I'); expect(cache(s,'RN1').state).toBe('UC');
  });

  it('arrival and completion checkpoints select RX rather than an earlier departure', () => {
    const checkpoints: [string,string,string][] = [
      ['split-early-snoop','响应先到','RespSepData'],
      ['split-early-snoop','第一拍 DataID=0 已到','DataSepResp'],
      ['split-early-snoop','最后一拍 DataID=2 到齐','DataSepResp'],
      ['compack-overtake','旧数据已返回','CompData'],
      ['retry-invalidated','信用晚到','PCrdGrant'],
      ['multiple-snoops','原写回最后获得 DBID','CompDBIDResp'],
      ['data-before-response','数据拍先返回','DataSepResp'],
      ['data-before-response','延迟的完成响应到达','RespSepData'],
      ['early-pcredit','信用先到','PCrdGrant'],
      ['write-cancel','已取得 DBID','DBIDResp'],
      ['ordered-writes','后写数据先到','NonCopyBackWriteData'],
      ['ordered-writes','前写 Comp 解锁','Comp'],
      ['dct-double','内存更新完成','NonCopyBackWriteData'],
      ['writeclean-old','内存只更新到 7','NonCopyBackWriteData'],
    ];
    for (const [key,title,opcode] of checkpoints) {
      const s=load(key), point=s.manifest.teaching!.checkpoints.find(p=>p.title===title)!;
      expect(point,`${key}: ${title}`).toBeDefined();
      const received=s.eventById[point.eventId] as ObservedEvent;
      expect(received.type).toBe('observed'); expect(received.observation).toBe('RX');
      expect(received.opcode).toBe(opcode);
      for(const raw of [false,true])
        expect(resolveCheckpoint(s,raw,point.eventId)!.tick).toBe(BigInt(received.tick));
    }
  });

  it('waits for RespSepData even when all DataSepResp packets have arrived first', () => {
    const s=load('data-before-response'), data=messages(s,'DataSepResp');
    const rsp=messages(s,'RespSepData')[0], ack=messages(s,'CompAck')[0];
    expect(lastEnd(s,data)).toBeLessThan(end(s,rsp));
    expect(start(ack)).toBeGreaterThanOrEqual(end(s,rsp));
    expect(data.every(p=>field(p,'DBID')===field(rsp,'DBID'))).toBe(true);
    expect(field(ack,'TxnID')).toBe(field(rsp,'DBID'));
    expect(cache(s,'RN0').data).toBe(line(data));
  });

  it('blocks an overtaking same-address REQ until the previous Home completion acknowledgement', () => {
    const s=load('compack-overtake'), requests=messages(s,'ReadShared').sort((a,b)=>start(a)<start(b)?-1:1);
    const ack=messages(s,'CompAck').find(p=>p.rootId===requests[0].rootId)!;
    const data=messages(s,'CompData').filter(p=>p.rootId===requests[1].rootId);
    expect(start(requests[1])).toBeGreaterThan(start(ack));
    expect(end(s,requests[1])).toBeLessThan(end(s,ack));
    expect(start(data[0])).toBeGreaterThanOrEqual(end(s,ack));
    expect(field(requests[0],'TxnID')).not.toBe(field(requests[1],'TxnID'));
    expect(cache(s,'RN0').data).toBe(line(data));
  });

  it('returns an unused credit after a retried copyback was invalidated instead of resending dirty data', () => {
    const s=load('retry-invalidated'), wb=messages(s,'WriteBackFull');
    const retry=messages(s,'RetryAck')[0], grant=messages(s,'PCrdGrant')[0], creditReturn=messages(s,'PCrdReturn')[0];
    expect(wb).toHaveLength(1); expect(messages(s,'CopyBackWriteData')).toHaveLength(0);
    const lost=s.observed.find(e=>e.kind==='state'&&e.node===wb[0].source&&e.state?.state==='I')!;
    expect(BigInt(lost.tick)).toBeGreaterThan(end(s,retry));
    expect(BigInt(lost.tick)).toBeLessThan(end(s,grant));
    expect(start(creditReturn)).toBeGreaterThanOrEqual(end(s,grant));
    expect(field(creditReturn,'PCrdType')).toBe(field(retry,'PCrdType'));
    expect(field(creditReturn,'PCrdType')).toBe(field(grant,'PCrdType'));
    expect(field(creditReturn,'AllowRetry')).toBe(0);
    expect(cache(s,'RN0').state).toBe('I'); expect(cache(s,'RN1')).toMatchObject({state:'UD',dirty:true});
  });

  it('serializes consecutive snoops to the same holder and writes back only the final invalid state', () => {
    const s=load('multiple-snoops'), first=messages(s,'SnpShared')[0];
    const firstReply=messages(s,'SnpRespData').filter(p=>field(p,'TxnID')===field(first,'TxnID'));
    const second=messages(s,'SnpUnique').find(p=>p.target===first.target)!;
    expect(start(second)).toBeGreaterThanOrEqual(lastEnd(s,firstReply));
    const states=s.observed.filter(e=>e.kind==='state'&&e.node===first.target).map(e=>e.state!.state);
    expect(states).toEqual(['SC','I']);
    const cb=messages(s,'CopyBackWriteData'); expect(cb).toHaveLength(2);
    for(const p of cb) {
      expect(field(p,'Resp')).toBe(0); expect(p.byteEnable).toBe('00'.repeat(32));
      expect(p.payload).toBe('00'.repeat(32));
    }
    expect(cache(s,'RN2')).toMatchObject({state:'UD',dirty:true});
  });

  it('answers snoops with I while an Evict notification is delayed, then preserves the new owner', () => {
    const s=load('evict-snoop'), evict=messages(s,'Evict')[0], snp=messages(s,'SnpShared')[0];
    const response=messages(s,'SnpResp').find(p=>p.source===evict.source)!;
    const invalid=s.observed.find(e=>e.kind==='state'&&e.node===evict.source&&e.state?.state==='I')!;
    expect(BigInt(invalid.tick)).toBeLessThanOrEqual(start(evict));
    expect(end(s,snp)).toBeLessThan(end(s,evict));
    expect(field(response,'Resp')).toBe(0);
    expect(field(response,'TxnID')).toBe(field(snp,'TxnID'));
    expect(s.protocol.some(p=>p.channel==='DAT'&&p.source===evict.source)).toBe(false);
    expect(messages(s,'CompAck').some(p=>p.rootId===evict.rootId)).toBe(false);
    expect(cache(s,'HN0').owner).toBe('RN1'); expect(cache(s,'RN0').state).toBe('I');
  });

  it('DCT double return keeps original TxnID, Home DBID and two independent destinations', () => {
    const s=load('dct-double');
    const req=messages(s,'ReadShared')[0], snp=messages(s,'SnpSharedFwd')[0];
    const direct=messages(s,'CompData','RN1','RN0'), returned=messages(s,'SnpRespDataFwded');
    expect(direct).toHaveLength(2); expect(returned).toHaveLength(2);
    const node=(id:string)=>s.manifest.topology.find(n=>n.id===id)!.nodeId;
    for(const p of direct) {
      expect(field(p,'TxnID')).toBe(field(req,'TxnID'));
      expect(field(p,'DBID')).toBe(field(snp,'TxnID'));
      expect(field(p,'HomeNID')).toBe(node('HN0'));
      expect(field(p,'Resp')).toBe(1);
      expect(start(p)).toBeGreaterThan(end(s,snp));
    }
    for(const p of returned) {
      expect(field(p,'TxnID')).toBe(field(snp,'TxnID'));
      expect(field(p,'Resp')).toBe(5); expect(field(p,'FwdState')).toBe(1);
    }
    expect(field(snp,'FwdNID')).toBe(node(req.source));
    expect(field(snp,'FwdTxnID')).toBe(field(req,'TxnID'));
    expect(line(direct)).toBe(line(returned));
    const timeline=new TransportTimeline(s), instant=start(direct[0]);
    expect(timeline.sample(instant).transfers.some(t=>t.id===returned[0].messageId)).toBe(true);
    const ack=messages(s,'CompAck')[0];
    expect(ack.target).toBe('HN0'); expect(field(ack,'TxnID')).toBe(field(direct[0],'DBID'));
    expect(start(ack)).toBeGreaterThanOrEqual(lastEnd(s,direct));
    expect(cache(s,'SN0').data).toBe(line(returned));
    expect(cache(s,'RN0').state).toBe('SC'); expect(cache(s,'RN1').state).toBe('SC');
  });

  it('DCT fallback uses an ordinary response and waits for its data before Home supplies the reader', () => {
    const s=load('dct-fallback'), snp=messages(s,'SnpSharedFwd')[0];
    const returned=messages(s,'SnpRespData'), result=messages(s,'CompData');
    expect(s.protocol.some(p=>p.source==='RN1'&&p.target==='RN0')).toBe(false);
    expect(messages(s,'SnpRespDataFwded')).toHaveLength(0);
    for(const p of returned) expect(field(p,'TxnID')).toBe(field(snp,'TxnID'));
    expect(start(result[0])).toBeGreaterThanOrEqual(lastEnd(s,returned));
    expect(result.every(p=>p.source==='HN0'&&p.target==='RN0')).toBe(true);
    expect(line(result)).toBe(line(returned));
    expect(cache(s,'RN0').data).toBe(line(result));
    expect(cache(s,'RN0').state).toBe('SC');
  });

  it('merges UDP snoop bytes with memory rather than treating invalid zero bytes as data', () => {
    const s=load('partial-snoop-merge'), partial=messages(s,'SnpRespDataPtl');
    const memory=messages(s,'CompData','SN0','HN0'), result=messages(s,'CompData','HN0','RN0');
    expect(partial.map(p=>field(p,'DataID')).sort()).toEqual([0,2]);
    const payload=Buffer.from(line(partial),'hex'), old=Buffer.from(line(memory),'hex');
    const mask=Buffer.from([...partial].sort((a,b)=>Number(field(a,'DataID'))-Number(field(b,'DataID'))).map(p=>p.byteEnable).join(''),'hex');
    expect(mask.some(b=>b===0)).toBe(true); expect(mask.some(b=>b===255)).toBe(true);
    const expected=Buffer.from(payload.map((v,i)=>mask[i]===255?v:old[i])).toString('hex');
    expect(line(result)).toBe(expected);
    expect(start(result[0])).toBeGreaterThanOrEqual(lastEnd(s,[...partial,...memory]));
    expect(result.every(p=>field(p,'Resp')===6)).toBe(true);
    expect(cache(s,'RN0')).toMatchObject({state:'UD',dirty:true,data:expected});
    expect(cache(s,'RN1').state).toBe('I');
    expect(cache(s,'SN0').data).toBe(line(memory));
  });

  it('keeps WriteClean snapshot and newer local store as different generations', () => {
    const s=load('writeclean-old'), cb=messages(s,'CopyBackWriteData'), snoop=messages(s,'SnpRespData');
    expect(messages(s,'WriteCleanFull')).toHaveLength(1);
    expect(messages(s,'SnpOnce')).toHaveLength(1);
    expect(start(cb[0])).toBeGreaterThan(lastEnd(s,snoop));
    expect(line(cb)).not.toBe(line(snoop));
    expect(line(cb)).toBe(s.manifest.initialState!.RN1.lines['0x1000'].data);
    expect(cache(s,'SN0').data).toBe(line(cb));
    expect(cache(s,'RN1')).toMatchObject({state:'UD',dirty:true,data:line(snoop)});
    const read=messages(s,'ReadOnce')[0], response=messages(s,'CompData','HN0','RN0');
    const store=s.observed.find(e=>e.kind==='instruction'&&e.rootId.endsWith(':new-store-generation'))!;
    expect(store.instruction?.origin).toBe('modeled');
    expect(store.instruction?.hartId).toBe(1);
    const thread=s.manifest.litmus!.threads.find(t=>t.id===store.instruction!.sourceId)!;
    expect(thread.code.split('\n')[store.instruction!.line!-1]).toContain('WRITE_ONCE');
    for(const p of [...snoop,...response]) {
      expect(p.causeInstructionIds).toEqual(read.causeInstructionIds);
      expect(p.causeInstructionIds).not.toContain(store.instructionId);
      expect(p.dataProducerInstructionIds).toEqual([store.instructionId]);
      for(const source of p.sourceEventIds.map(id=>s.eventById[id])) {
        expect(source.causeInstructionIds).toEqual(read.causeInstructionIds);
        expect(source.dataProducerInstructionIds).toEqual([store.instructionId]);
      }
    }
    // The old snapshot comes from a seeded line; no observed/model instruction
    // represents its earlier writer, so keep that historical origin unknown.
    for(const p of cb) expect(p.dataProducerInstructionIds).toEqual([]);
  });

  it('MakeReadUnique refills a line invalidated while waiting and respects previous CompAck', () => {
    const s=load('make-read-unique'), req=messages(s,'MakeReadUnique')[0];
    const result=messages(s,'CompData','HN0',req.source).filter(p=>p.rootId===req.rootId);
    const lost=s.observed.find(e=>e.kind==='state'&&e.node===req.source&&e.state?.state==='I')!;
    expect(BigInt(lost.tick)).toBeGreaterThan(start(req));
    expect(BigInt(lost.tick)).toBeLessThan(start(result[0]));
    expect(result).toHaveLength(2);
    expect(messages(s,'Comp').filter(p=>p.rootId===req.rootId)).toHaveLength(0);
    expect(messages(s,'SnpMakeInvalid')).toHaveLength(0);
    const priorAck=messages(s,'CompAck','RN1')[0], nextSnoop=messages(s,'SnpCleanInvalid')[0];
    expect(start(nextSnoop)).toBeGreaterThanOrEqual(end(s,priorAck));
    expect(result.every(p=>field(p,'TxnID')===field(req,'TxnID'))).toBe(true);
    expect(cache(s,'RN0')).toMatchObject({state:'UC',data:line(result)});
    expect(cache(s,'RN1').state).toBe('I');
  });

  it('holds early credit until RetryAck and consumes its matching type on exactly one reissue', () => {
    const s=load('early-pcredit'), grant=messages(s,'PCrdGrant')[0], retry=messages(s,'RetryAck')[0];
    const requests=messages(s,'ReadShared'); expect(requests).toHaveLength(2);
    expect(end(s,grant)).toBeLessThan(end(s,retry));
    const reissued=requests.find(p=>field(p,'AllowRetry')===0)!;
    expect(start(reissued)).toBeGreaterThanOrEqual(end(s,retry));
    expect(start(reissued)).toBeGreaterThanOrEqual(end(s,grant));
    expect(field(reissued,'PCrdType')).toBe(field(retry,'PCrdType'));
    expect(field(reissued,'PCrdType')).toBe(field(grant,'PCrdType'));
    expect(grant.source).toBe(retry.source);
    expect(field(grant,'TxnID')??0).toBe(0);
    expect(cache(s,'RN0').state).toBe('SC');
  });

  it('cancels only after DBID, sends every zero-BE beat and leaves memory unchanged', () => {
    const s=load('write-cancel'), req=messages(s,'WriteUniquePtl')[0], db=messages(s,'DBIDResp')[0];
    const packets=messages(s,'WriteDataCancel'), comp=messages(s,'Comp')[0];
    expect(packets.map(p=>field(p,'DataID')).sort()).toEqual([0,2]);
    expect(messages(s,'NonCopyBackWriteData')).toHaveLength(0);
    for(const p of packets) {
      expect(start(p)).toBeGreaterThan(end(s,db));
      expect(field(p,'TxnID')).toBe(field(db,'DBID'));
      expect(p.byteEnable).toBe('00'.repeat(32)); expect(p.payload).toBe('00'.repeat(32));
      expect(field(p,'Resp')).toBe(0);
    }
    expect(field(comp,'TxnID')).toBe(field(req,'TxnID'));
    expect(field(comp,'DBID')).toBe(field(db,'DBID'));
    expect(start(comp)).toBeGreaterThanOrEqual(lastEnd(s,packets));
    expect(cache(s,'HN0').data).toBe(s.manifest.initialState!.HN0.lines['0x1000'].data);
    expect(cache(s,'SN0').data).toBe(s.manifest.initialState!.SN0.lines['0x1000'].data);
  });

  it('orders OWO visibility through acknowledgements while requests and data overlap', () => {
    const s=load('ordered-writes'), reqs=messages(s,'WriteUniqueFull').sort((a,b)=>start(a)<start(b)?-1:1);
    expect(reqs).toHaveLength(2);
    const byRoot=(opcode:string,root:string)=>messages(s,opcode).filter(p=>p.rootId===root);
    const [a,b]=reqs, db1=byRoot('DBIDResp',a.rootId)[0], db2=byRoot('DBIDResp',b.rootId)[0];
    const comp1=byRoot('Comp',a.rootId)[0], comp2=byRoot('Comp',b.rootId)[0];
    const ack1=byRoot('CompAck',a.rootId)[0], ack2=byRoot('CompAck',b.rootId)[0];
    expect(start(b)).toBeGreaterThanOrEqual(end(s,db1)); expect(start(b)).toBeLessThan(end(s,comp1));
    expect(start(ack1)).toBeGreaterThanOrEqual(end(s,db1)); expect(start(ack1)).toBeLessThan(start(comp1));
    expect(start(ack2)).toBeGreaterThanOrEqual(end(s,comp1)); expect(start(ack2)).toBeLessThan(start(comp2));
    expect(field(ack1,'TxnID')).toBe(field(db1,'DBID')); expect(field(ack2,'TxnID')).toBe(field(db2,'DBID'));
    expect(field(comp1,'DBID')).toBe(field(db1,'DBID')); expect(field(comp2,'DBID')).toBe(field(db2,'DBID'));
    const visible=s.observed.find(e=>e.kind==='state'&&e.node===b.target&&e.rootId===b.rootId)!;
    expect(BigInt(visible.tick)).toBeGreaterThanOrEqual(end(s,ack2));
    expect(cache(s,b.target,b.address).data).toBe(line(byRoot('NonCopyBackWriteData',b.rootId)));
  });

  it('uses CAH no-data CopyBack completion but still acknowledges the Home DBID', () => {
    const s=load('copy-at-home'), req=messages(s,'WriteEvictFull')[0];
    const comp=messages(s,'Comp')[0], ack=messages(s,'CompAck')[0];
    expect(field(req,'CAH')).toBe(1); expect(field(req,'ExpCompAck')).toBe(0);
    expect(s.protocol.filter(p=>p.channel==='DAT')).toHaveLength(0);
    expect(messages(s,'CompDBIDResp')).toHaveLength(0);
    expect(start(ack)).toBeGreaterThan(end(s,comp));
    expect(field(ack,'TxnID')).toBe(field(comp,'DBID'));
    expect(field(ack,'Resp')).toBe(2);
    const exposed=s.observed.find(e=>e.kind==='state'&&e.node==='HN0')!;
    expect(BigInt(exposed.tick)).toBeGreaterThanOrEqual(end(s,ack));
    expect(cache(s,'RN0').state).toBe('I');
    expect(cache(s,'HN0').data).toBe(s.manifest.initialState!.RN0.lines['0x1000'].data);
  });
});
