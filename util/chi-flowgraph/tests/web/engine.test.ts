import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { initialState, parseSession, reduceEvent, ReplayEngine } from '../../src/trace/engine';
const text=readFileSync('public/demos/miss-hit.chi.jsonl','utf8');

describe('trace import and deterministic replay',()=>{
  it('keeps schema 1.1 mesh, exact dynamic identities and explicit merged causes across views',()=>{
    const records=text.trim().split('\n').map(line=>JSON.parse(line)).filter(r=>r.type==='manifest'||r.type==='observed');
    const manifest=records[0];manifest.schemaVersion='1.1';
    manifest.fabric={routers:[{id:'R0',x:0,y:0},{id:'R1',x:1,y:0}],links:[{from:'R0',to:'R1'}],attachments:[{node:'RN0',router:'R0'},{node:'HN0',router:'R1'}]};
    manifest.litmus={name:'test',threads:[{id:'s0',hartId:0,node:'RN0',code:'load x\nload x',title:'P0'}]};
    const request=records.find(r=>r.kind==='flit'&&r.channel==='REQ');request.causeInstructionIds=['cpu:0:0:9007199254740993'];
    const prior=records.at(-1);
    records.push({type:'observed',kind:'cause_add',id:'merged',seq:prior.seq+1,tick:prior.tick,rootId:request.rootId,legId:request.legId,causeInstructionIds:['cpu:0:0:9007199254740993','cpu:0:0:9007199254740995']});
    const session=parseSession(records.map(record=>JSON.stringify(record)).join('\n'));
    expect(session.manifest.fabric?.routers).toHaveLength(2);
    expect(session.rawSteps.find(s=>s.id===request.id)?.event.causeInstructionIds).toEqual(['cpu:0:0:9007199254740993','cpu:0:0:9007199254740995']);
    const raw=new ReplayEngine(session,true);expect(raw.seek(1)).toEqual(new ReplayEngine(session,true).seek(1));
    manifest.fabric.links[0].to='missing';expect(()=>parseSession(records.map(record=>JSON.stringify(record)).join('\n'))).toThrow('router');
  });
  it('imports every real component demo',()=>{
    for(const file of readdirSync('public/demos').filter(s=>s.endsWith('.jsonl'))){const session=parseSession(readFileSync(`public/demos/${file}`,'utf8'));expect(session.protocol.length).toBeGreaterThan(0);expect(session.validations.filter(v=>v.status==='fail'),file).toEqual([]);}
  });
  it('random seek and stepping produce the same states without mutating snapshots',()=>{
    const session=parseSession(text), engine=new ReplayEngine(session);let state=initialState(session);const expected=[state];
    session.steps.forEach((step,i)=>{state=reduceEvent(state,step,i);expected.push(state);});
    for(const cursor of [session.steps.length-1,0,17,-1,12,3,session.steps.length-1])expect(engine.seek(cursor)).toEqual(expected[cursor+1]);
    expect(expected[0].nodes.HN0.lines).toEqual({});
  });
  it('retains addresses and ticks above 2^53 exactly',()=>{
    const records=text.trim().split('\n').map(line=>JSON.parse(line)).filter(r=>r.type==='manifest'||r.type==='observed');
    for(const r of records.slice(1)){r.tick=(9007199254740993n+BigInt(r.seq)).toString();r.address='0x20000000000001';}
    const session=parseSession(records.map(record=>JSON.stringify(record)).join('\n'));
    expect(session.observed[0].tick).toBe('9007199254740994');expect(session.observed[0].address).toBe('0x20000000000001');
  });
  it('rejects lossy numbers, malformed masks, duplicate identity and invalid cursor',()=>{
    const records=text.trim().split('\n').map(line=>JSON.parse(line)),event=records.find(r=>r.type==='observed'&&r.payload);
    event.tick=9007199254740992;expect(()=>parseSession(records.map(record=>JSON.stringify(record)).join('\n'))).toThrow('字符串');
    event.tick='1';event.validMask='ff';expect(()=>parseSession(records.map(record=>JSON.stringify(record)).join('\n'))).toThrow('掩码');
    expect(()=>parseSession(text+'\n'+text.split('\n')[1])).toThrow();
    expect(()=>new ReplayEngine(parseSession(text)).seek(NaN)).toThrow('有限');
  });
  it('does not infer cache state before a sampled state event',()=>{
    const session=parseSession(text);expect(initialState(session).nodes.HN0.lines).toEqual({});
    expect(new ReplayEngine(session).seek(-1).cursor).toBe(-1);
  });
  it('indexes and seeks a 100,000-event capture',()=>{
    const manifest=JSON.parse(text.split('\n')[0]);manifest.initialState={};
    const lines=[JSON.stringify(manifest)];
    for(let i=0;i<100_000;i++)lines.push(JSON.stringify({type:'observed',id:`o${i}`,seq:i,tick:String(9007199254740993n+BigInt(i)),kind:'state',rootId:`r${i%100}`,legId:`l${i%100}`,node:'HN0',address:`0x${(i%64*64).toString(16)}`,state:{state:i%2?'SN':'I'}}));
    const start=performance.now(),session=parseSession(lines.join('\n')),engine=new ReplayEngine(session);const end=engine.seek(99_999);
    expect(session.steps).toHaveLength(100_000);expect(end.cursor).toBe(99_999);expect(engine.seek(512)).toEqual(new ReplayEngine(session).seek(512));expect(engine.seek(99_999)).toEqual(end);
    console.info(`100k parse + seek: ${Math.round(performance.now()-start)} ms`);
  },20_000);
});
