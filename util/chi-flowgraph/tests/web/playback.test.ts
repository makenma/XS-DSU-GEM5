import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { parseSession } from '../../src/trace/engine';
import { cursorAtTick, SimulationClock, TransportTimeline } from '../../src/trace/playback';
import { sourceView } from '../../src/trace/source';

const session=parseSession(readFileSync('public/demos/litmus-iriw.chi.jsonl','utf8'));
describe('one clock for physical and paced concurrent replay',()=>{
  it('deduplicates TX/RX, keeps simultaneous cores and measured router anchors',()=>{
    const timeline=new TransportTimeline(session),active=timeline.sample(7000n);
    expect(new Set(active.transfers.map(t=>t.source)).size).toBeGreaterThanOrEqual(2);
    const transfer=active.transfers.find(t=>t.source==='RN3'&&t.target==='HN0')!;
    expect(transfer.start).toBe(7000n);expect(transfer.end).toBe(20000n);
    expect(transfer.hops.map(h=>h.tick)).toEqual([11000n,14000n,17000n]);
    const observations=session.observed.filter(e=>e.messageId===transfer.id);
    for(const event of observations)expect(timeline.byEvent.get(event.id)).toBe(transfer);
    expect(timeline.transfers.filter(t=>t.id===transfer.id)).toHaveLength(1);
    expect(timeline.sample(20000n).transfers).toContain(transfer);
    expect(timeline.sample(20001n).transfers).not.toContain(transfer);
  });
  it('steps across unique physical moments and reduces all equal-tick records',()=>{
    const timeline=new TransportTimeline(session),next=timeline.adjacent(7000n,1)!;
    expect(next).toBeGreaterThan(7000n);expect(timeline.adjacent(next,-1)).toBe(7000n);
    expect(new Set(timeline.moments).size).toBe(timeline.moments.length);
    for(const steps of [session.steps,session.rawSteps]){
      const at=cursorAtTick(steps,7000n);expect(BigInt(steps[at].anchorTick)).toBe(7000n);
      expect(BigInt(steps[at+1].anchorTick)).toBeGreaterThan(7000n);
    }
  });
  it('stretches the shared interval, stops exactly and preserves large ticks across pause/resume',()=>{
    const clock=new SimulationClock(),base=9007199254740993n;
    clock.reset(base,base+20000n,0);clock.advanceTo(base+4000n,1000,0);
    expect(clock.read(250)).toBe(base+1000n);expect(clock.read(1200)).toBe(base+4000n);
    clock.pause(1200);expect(clock.read(9000)).toBe(base+4000n);
    clock.advanceTo(base+7000n,1000,10000);expect(clock.read(10500)).toBe(base+5500n);
    clock.pause(10500);clock.setRate(2000,10500);clock.play(11000);
    expect(clock.read(12000)).toBe(base+7500n);
    clock.seek(base+1n,12000);clock.pause(12000);expect(clock.read(50000)).toBe(base+1n);
  });
  it('maps the explanatory C reads to the actual opposite IRIW load orders',()=>{
    const litmus=session.manifest.litmus!;
    for(const hart of [2,3]){
      const thread=litmus.threads[hart],view=sourceView(thread,litmus.name,false);
      const reads=view.lines.filter(l=>l.text.includes('READ_ONCE'));
      expect(reads.map(l=>l.sourceLines)).toEqual([[8],[9]]);
      expect(reads[0].text).toContain(hart===2?'(x)':'(y)');
      expect(view.note).toContain('非 C 编译产物');
      expect(sourceView(thread,litmus.name,true).lines[7].text).toContain('ld t1');
    }
  });
});
