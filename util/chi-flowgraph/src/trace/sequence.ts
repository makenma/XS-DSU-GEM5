import type { Step } from './types';
import type { TransportTimeline } from './playback';

export interface SequenceMessage {
  id:string;
  step:Step;
  eventIds:string[];
  source:string;
  target:string;
  start:bigint;
  end?:bigint;
  timing:'complete'|'send-only'|'receive-only'|'unknown';
}

/** A sequence arrow and a mesh flight are the same logical transmission.
 * Raw TX/RX observations must never become two arrows. Missing arrival
 * evidence is kept unknown, rather than giving the packet an invented delay. */
export function buildSequenceMessages(steps:Step[], timeline:TransportTimeline, rootId=''):SequenceMessage[] {
  const messages=new Map<string,SequenceMessage>();
  for(const step of steps) {
    const e=step.event;
    if(step.kind!=='flit'||(rootId&&step.rootId!==rootId)||!e.source||!e.target)continue;
    const transfer=timeline.byEvent.get(step.id),id=transfer?.id??(e.type==='protocol'?step.id:e.messageId??step.id);
    const existing=messages.get(id);
    if(existing) {
      if(!existing.eventIds.includes(step.id))existing.eventIds.push(step.id);
      if(e.type==='observed'&&e.observation==='TX')existing.step=step;
      continue;
    }
    const timing=transfer?.timed?'complete':transfer?.received||(e.type==='observed'&&e.observation==='RX')?'receive-only':
      (e.type==='observed'&&e.observation==='TX')||transfer?.sent?'send-only':'unknown';
    messages.set(id,{
      id,step,eventIds:[...new Set([step.id,...(transfer?.eventIds??[])])],
      source:transfer?.source??e.source,target:transfer?.target??e.target,
      start:transfer?.start??BigInt(step.anchorTick),end:transfer?.timed?transfer.end:undefined,timing,
    });
  }
  return [...messages.values()].sort((a,b)=>a.start<b.start?-1:a.start>b.start?1:a.step.order-b.step.order);
}

/** Compress empty time by event rank, not by message list order. Equal ticks
 * share a row; TX/RX keep their order even when a later packet overtakes one.
 * BigInt keeps large capture ticks exact. */
export function buildSequenceLayout(messages:SequenceMessage[]) {
  const ticks=[...new Set(messages.flatMap(m=>m.end===undefined?[m.start]:[m.start,m.end]))]
    .sort((a,b)=>a<b?-1:a>b?1:0);
  const top=32,row=38,height=Math.max(120,top+ticks.length*row+24);
  const yAt=(tick:bigint):number=>{
    if(!ticks.length||tick<=ticks[0])return top;
    if(tick>=ticks[ticks.length-1])return top+(ticks.length-1)*row;
    let lo=0,hi=ticks.length;
    while(lo<hi){const mid=(lo+hi)>>>1;if(ticks[mid]<=tick)lo=mid+1;else hi=mid;}
    const fraction=Number((tick-ticks[lo-1])*1000000n/(ticks[lo]-ticks[lo-1]))/1000000;
    return top+(lo-1+fraction)*row;
  };
  return {ticks,height,yAt};
}
