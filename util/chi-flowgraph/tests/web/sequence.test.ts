import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import Sequence from '../../src/components/Sequence';
import { parseSession } from '../../src/trace/engine';
import { TransportTimeline } from '../../src/trace/playback';
import { buildSequenceLayout, buildSequenceMessages } from '../../src/trace/sequence';
import type { Manifest, ObservedEvent, ProtocolEvent, Session } from '../../src/trace/types';

const catalog = JSON.parse(readFileSync('public/demos/catalog.json', 'utf8')) as {id:string;file:string;group?:string}[];
const complexCases = catalog.filter(d => d.group === 'hazards');
const load = (id:string) => parseSession(readFileSync(`public/demos/${id}.chi.jsonl`, 'utf8'));
const split = load('teach-special-split-early-snoop');
const compact = (messages:ReturnType<typeof buildSequenceMessages>) => messages.map(m => ({
  id:m.id, source:m.source, target:m.target, start:m.start, end:m.end, timing:m.timing,
}));
const manifest:Manifest = {
  type:'manifest', schemaVersion:'1.1', title:'Sequence timing regression',
  source:{kind:'test'}, protocol:'CHI Issue H', profile:'h-extended-256', dataWidth:256, lineBytes:64,
  topology:[
    {id:'RN0',nodeId:0,role:'RN-F',label:'RN0'},
    {id:'RN1',nodeId:1,role:'RN-F',label:'RN1'},
    {id:'HN0',nodeId:2,role:'HN-F',label:'HN0'},
  ],
};
const event = (id:string, tick:bigint, observation?:'TX'|'RX', messageId=id):ObservedEvent => ({
  type:'observed', id, seq:0, tick:String(tick), kind:'flit', rootId:'root', legId:'leg',
  source:'RN0', target:'HN0', channel:'REQ', opcode:'ReadShared', observation, messageId,
});
function fixture(events:ObservedEvent[], protocol:ProtocolEvent[]=[]):Session {
  const sorted=[...events].sort((a,b) => BigInt(a.tick)<BigInt(b.tick)?-1:BigInt(a.tick)>BigInt(b.tick)?1:0)
    .map((e,seq)=>({...e,seq}));
  return parseSession([manifest,...sorted,...protocol].map(e=>JSON.stringify(e)).join('\n'));
}
function sequenceMarkup(s:Session, raw=false, currentId?:string) {
  return renderToStaticMarkup(createElement(Sequence,{
    session:s,steps:raw?s.rawSteps:s.steps,rootId:'',currentId,onPick:()=>{},
    transports:new TransportTimeline(s),
  }));
}
const attributes = (tag:string) => Object.fromEntries([...tag.matchAll(/([\w-]+)="([^"]*)"/g)].map(m=>[m[1],m[2]]));
function flight(markup:string, id:string) {
  const group=[...markup.matchAll(/<g\b([^>]*data-message-id="[^"]+"[^>]*)>([\s\S]*?)<\/g>/g)]
    .find(m=>attributes(m[1])['data-message-id']===id);
  expect(group,`SVG message ${id}`).toBeDefined();
  const line=[...group![2].matchAll(/<line\b([^>]*)>/g)]
    .map(m=>attributes(m[1])).find(a=>a.class?.split(' ').includes('sequence-flight'));
  expect(line,`SVG flight ${id}`).toBeDefined();
  return {group:attributes(group![1]),line:line!};
}

describe('sequence diagram uses the same packet endpoints as mesh playback',()=>{
  it('audits every complex teaching case, including early snoop and completion ordering',()=>{
    expect(complexCases).toHaveLength(26);
  });

  it.each(complexCases.map(d=>d.id))('%s preserves every TX/RX and deduplicates raw observations',id=>{
    const s=load(id), timeline=new TransportTimeline(s);
    const refined=buildSequenceMessages(s.steps,timeline), raw=buildSequenceMessages(s.rawSteps,timeline);
    const sends=s.observed.filter(e=>e.kind==='flit'&&e.observation==='TX');
    expect(refined).toHaveLength(sends.length);
    expect(raw).toHaveLength(sends.length);
    expect(new Set(refined.map(m=>m.id)).size).toBe(sends.length);
    expect(compact(raw)).toEqual(compact(refined));
    for(const tx of sends) {
      const rx=s.observed.find(e=>e.kind==='flit'&&e.observation==='RX'&&e.messageId===tx.messageId)!;
      const m=refined.find(m=>m.id===tx.messageId)!;
      expect(m,`${id}: ${tx.id}`).toBeDefined();
      expect(m).toMatchObject({source:tx.source,target:tx.target,start:BigInt(tx.tick),end:BigInt(rx.tick),timing:'complete'});
      expect(m.eventIds).toContain(tx.id);
      expect(m.eventIds).toContain(rx.id);
      const animated=timeline.byEvent.get(m.step.id)!;
      expect(animated.start).toBe(m.start);
      expect(animated.end).toBe(m.end);
      const midpoint=(m.start+m.end!)/2n;
      expect(timeline.sample(midpoint,Number.MAX_SAFE_INTEGER).transfers.map(t=>t.id)).toContain(m.id);
    }
    const layout=buildSequenceLayout(refined), rawLayout=buildSequenceLayout(raw);
    expect(rawLayout.ticks).toEqual(layout.ticks);
    for(let i=1;i<layout.ticks.length;i++) {
      expect(layout.ticks[i]).toBeGreaterThan(layout.ticks[i-1]);
      expect(layout.yAt(layout.ticks[i])).toBeGreaterThan(layout.yAt(layout.ticks[i-1]));
    }
    for(const m of refined) expect(layout.yAt(m.end!)).toBeGreaterThanOrEqual(layout.yAt(m.start));
  });

  it('draws the late second DataSepResp below the overtaking SnpUnique arrival',()=>{
    const timeline=new TransportTimeline(split), messages=buildSequenceMessages(split.steps,timeline);
    const data=messages.find(m=>m.step.event.opcode==='DataSepResp'&&m.step.event.type==='protocol'&&m.step.event.fields.DataID.value===2)!;
    const snoop=messages.find(m=>m.step.event.opcode==='SnpUnique')!;
    const reply=messages.find(m=>m.step.event.opcode==='SnpRespData')!;
    expect(data.start).toBeLessThan(snoop.start);
    expect(data.end).toBeGreaterThan(snoop.end!);
    expect(reply.start).toBeGreaterThanOrEqual(data.end!);
    const layout=buildSequenceLayout(messages), markup=sequenceMarkup(split);
    const dataLine=flight(markup,data.id), snoopLine=flight(markup,snoop.id);
    expect(dataLine.group['data-start-tick']).toBe(String(data.start));
    expect(dataLine.group['data-end-tick']).toBe(String(data.end));
    expect(Number(dataLine.line.y1)).toBe(layout.yAt(data.start));
    expect(Number(dataLine.line.y2)).toBe(layout.yAt(data.end!));
    expect(Number(dataLine.line.y1)).toBeLessThan(Number(dataLine.line.y2));
    expect(Number(dataLine.line.y2)).toBeGreaterThan(Number(snoopLine.line.y2));
    expect(Number(dataLine.line.x1)).toBeGreaterThan(Number(dataLine.line.x2));
    expect(markup).toContain('DataSepResp [2]');
    const rawLine=flight(sequenceMarkup(split,true),data.id);
    for(const field of ['x1','x2','y1','y2']) expect(rawLine.line[field]).toBe(dataLine.line[field]);
  });

  it('keeps a shared vertical instant for genuinely concurrent DCT branches',()=>{
    const s=load('teach-special-dct-double'), timeline=new TransportTimeline(s);
    const messages=buildSequenceMessages(s.steps,timeline), layout=buildSequenceLayout(messages);
    const direct=messages.find(m=>m.step.event.opcode==='CompData'&&m.source==='RN1'&&m.target==='RN0')!;
    const home=messages.find(m=>m.step.event.opcode==='SnpRespDataFwded')!;
    expect(direct.start).toBe(home.start);
    expect(layout.yAt(direct.start)).toBe(layout.yAt(home.start));
    expect(timeline.sample(direct.start).transfers.map(t=>t.id)).toEqual(expect.arrayContaining([direct.id,home.id]));
  });

  it('filters roots only after grouping packet observations',()=>{
    const timeline=new TransportTimeline(split), all=buildSequenceMessages(split.rawSteps,timeline);
    for(const root of new Set(all.map(m=>m.step.rootId))) {
      expect(compact(buildSequenceMessages(split.rawSteps,timeline,root)))
        .toEqual(compact(all.filter(m=>m.step.rootId===root)));
    }
  });
});

describe('sequence timing evidence and bounded rendering',()=>{
  it('keeps adjacent ticks above 2^53 distinct while compressing long quiet gaps',()=>{
    const base=9007199254740993n;
    const s=fixture([
      event('a-tx',base+1n,'TX','a'),event('b-tx',base+1n,'TX','b'),
      event('c-tx',base+2n,'TX','c'),event('b-rx',base+3n,'RX','b'),
      event('a-rx',base+4n,'RX','a'),event('c-rx',base+1000000000000000000n,'RX','c'),
    ]);
    const messages=buildSequenceMessages(s.rawSteps,new TransportTimeline(s));
    const layout=buildSequenceLayout(messages);
    expect(layout.ticks).toEqual([base+1n,base+2n,base+3n,base+4n,base+1000000000000000000n]);
    const ys=layout.ticks.map(t=>layout.yAt(t));
    for(let i=1;i<ys.length;i++) expect(ys[i]).toBeGreaterThan(ys[i-1]);
    expect(ys[4]-ys[3]).toBeCloseTo(ys[1]-ys[0]);
    expect(layout.yAt(base+5n)).toBeGreaterThanOrEqual(ys[3]);
    expect(layout.yAt(base+500000000000000000n)).toBeGreaterThan(ys[3]);
    expect(layout.yAt(base+500000000000000000n)).toBeLessThan(ys[4]);
    expect(Number.isFinite(layout.height)).toBe(true);
  });

  it('never invents a missing receive or send time from anchors or route observations',()=>{
    const unknown:ProtocolEvent={
      type:'protocol',id:'unobserved-protocol',seq:4,kind:'flit',rootId:'root',legId:'unknown-leg',
      source:'HN0',target:'RN0',channel:'RSP',opcode:'Comp',fields:{},sourceEventIds:[],dependencies:[],
      anchorTick:'40',anchorSeq:4,substep:0,origin:'modeled',rule:'logical-only',messageId:'unknown-message',
    };
    const s=fixture([
      event('tx-only',10n,'TX','tx-only-message'),
      {...event('route',20n,undefined,'tx-only-message'),kind:'route',fields:{fromRouter:'r0',toRouter:'r1'}},
      event('rx-only',30n,'RX','rx-only-message'),event('legacy',35n),
    ],[unknown]);
    const messages=buildSequenceMessages(s.steps,new TransportTimeline(s));
    expect(messages).toHaveLength(4);
    expect(messages.find(m=>m.step.id==='tx-only')).toMatchObject({start:10n,timing:'send-only'});
    expect(messages.find(m=>m.step.id==='rx-only')).toMatchObject({start:30n,timing:'receive-only'});
    expect(messages.find(m=>m.step.id==='legacy')?.timing).toBe('unknown');
    expect(messages.find(m=>m.step.id===unknown.id)?.timing).toBe('unknown');
    for(const m of messages) expect(m.end).toBeUndefined();
    const markup=sequenceMarkup(s);
    expect(markup).not.toContain('data-end-tick=');
  });

  it.each(['TX','RX'] as const)('keeps a lone %s observation classified identically after protocol expansion',observation=>{
    const observed=event('single-observation',30n,observation,'single-message');
    const protocol:ProtocolEvent={
      type:'protocol',id:'single-protocol',seq:1,kind:'flit',rootId:'root',legId:'leg',
      source:'RN0',target:'HN0',channel:'REQ',opcode:'ReadShared',fields:{},
      sourceEventIds:[observed.id],dependencies:[],anchorTick:'30',anchorSeq:0,substep:0,
      origin:'derived',rule:'expand-observed',messageId:observed.messageId,
    };
    const s=fixture([observed],[protocol]), timeline=new TransportTimeline(s);
    const raw=buildSequenceMessages(s.rawSteps,timeline), refined=buildSequenceMessages(s.steps,timeline);
    expect(compact(refined)).toEqual(compact(raw));
    expect(refined[0]).toMatchObject({start:30n,timing:observation==='TX'?'send-only':'receive-only'});
    expect(refined[0].end).toBeUndefined();
    const markup=sequenceMarkup(s);
    expect(markup).toContain(observation==='TX'?'到达未知':'发送未知');
    expect(markup).not.toContain('data-end-tick=');
  });

  it('retains logical ReadReceipt without borrowing its causal request packet identity or timing',()=>{
    // This legacy trace derives ReadReceipt from the ordered REQ. Its source
    // event IDs establish causality, not a TX/RX pair for the response itself.
    const s=load('ordered-read'), timeline=new TransportTimeline(s);
    const messages=buildSequenceMessages(s.steps,timeline);
    const receipts=s.protocol.filter(p=>p.kind==='flit'&&p.opcode==='ReadReceipt');
    expect(receipts.length).toBeGreaterThan(0);
    expect(messages).toHaveLength(s.protocol.filter(p=>p.kind==='flit').length);
    for(const receipt of receipts) {
      const m=messages.find(m=>m.step.id===receipt.id);
      expect(m,`logical response ${receipt.id}`).toBeDefined();
      expect(m).toMatchObject({source:receipt.source,target:receipt.target,timing:'unknown'});
      expect(m!.end).toBeUndefined();
      expect(timeline.byEvent.has(receipt.id)).toBe(false);
      for(const cause of receipt.sourceEventIds) {
        expect(m!.eventIds).not.toContain(cause);
        expect(m!.id).not.toBe(timeline.byEvent.get(cause)?.id);
      }
    }
  });

  it('renders at most 160 logical messages and follows a selected RX alias',()=>{
    const events:Array<ObservedEvent>=[];
    for(let i=0;i<220;i++) {
      events.push(event(`tx-${i}`,BigInt(i*10),'TX',`message-${i}`));
      events.push(event(`rx-${i}`,BigInt(i*10+5),'RX',`message-${i}`));
    }
    const s=fixture(events), markup=sequenceMarkup(s,true,'rx-219');
    expect([...markup.matchAll(/data-message-id="([^"]+)"/g)]).toHaveLength(160);
    expect(flight(markup,'message-219').group['aria-pressed']).toBe('true');
    expect(markup).not.toContain('data-message-id="message-0"');
  });
});
