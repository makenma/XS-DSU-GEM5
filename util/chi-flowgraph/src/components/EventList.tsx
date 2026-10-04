import { useEffect, useRef } from 'react';
import type { Step } from '../trace/types';
import { CHANNEL_COLORS } from './NetworkScene';

export default function EventList({steps,currentId,onPick}:{steps:Step[];currentId?:string;onPick:(id:string)=>void}) {
  const host=useRef<HTMLDivElement>(null);
  const position=steps.findIndex(s=>s.id===currentId),start=Math.max(0,position-60),recent=steps.slice(start,start+200);
  useEffect(()=>{const container=host.current,line=container?.querySelector<HTMLElement>('[aria-pressed="true"]');if(container&&line){container.scrollTop=Math.max(0,line.offsetTop-container.clientHeight/2+line.offsetHeight/2);}},[currentId]);
  return <div className="event-list" ref={host}>{recent.map(s=><button data-event-id={s.id} aria-pressed={s.id===currentId} className={s.id===currentId?'selected':''} onClick={()=>onPick(s.id)} key={s.id}><span className="event-channel" style={{color:s.event.channel?CHANNEL_COLORS[s.event.channel]:undefined}}>{s.event.channel??s.kind}</span><b>{s.event.opcode??s.event.kind}</b><span>{s.event.source??(s.event.type==='observed'?s.event.node:'')}{s.event.target?` → ${s.event.target}`:''}</span><code>{s.anchorTick}</code></button>)}{steps.length>200&&<p className="small-note">显示 {start+1}–{start+recent.length} / {steps.length} 步</p>}</div>;
}
