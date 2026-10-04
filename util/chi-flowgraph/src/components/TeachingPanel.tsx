import { useEffect,useRef } from 'react';
import type { Manifest } from '../trace/types';

export const LESSON_GROUPS={basics:'① 简单教学 · REQ opcode',hnf:'② HNF · SLC / SF hit、miss',hazards:'③ Hazard 与特殊交错'};
export const LESSON_TOPICS={existing:'基础交错与 early snoop',ordering:'消息越序与完成握手',retry:'重试与信用',snoop:'Snoop 与逐出竞争',forwarding:'直接转发与后备路径',data:'数据合并与取消',advanced:'高级缓存机制'};
export interface Demo {id:string;title:string;file:string;description:string;group?:string;topic?:string;evidence?:string}

export function ScenarioMenu({demos,selected,onPick}:{demos:Demo[];selected:string;onPick:(id:string)=>void}) {
  const host=useRef<HTMLDivElement>(null);
  useEffect(()=>{const container=host.current,button=container?.querySelector<HTMLElement>('[aria-current="true"]');if(container&&button)container.scrollTop=Math.max(0,button.offsetTop-container.clientHeight/2);},[selected]);
  const lessons=demos.filter(d=>d.group);
  if(!lessons.length)return null;
  const active=demos.find(d=>d.id===selected);
  const buttons=(items:Demo[])=>items.map(d=><button key={d.id} className={d.id===selected?'selected':''} aria-current={d.id===selected?'true':undefined} onClick={()=>onPick(d.id)}><span>{d.title}</span><small className={d.evidence==='protocol-model'?'modeled':''}>{d.evidence==='protocol-model'?'推演':'实测'}</small></button>);
  return <details className="scenario-menu" open><summary>教学目录 <b>{lessons.length} 个用例</b></summary><div ref={host}>{Object.entries(LESSON_GROUPS).map(([group,label])=><details key={group} open={active?.group===group||undefined}><summary>{label}<span>{lessons.filter(d=>d.group===group).length}</span></summary><div>{group==='hazards'?Object.entries(LESSON_TOPICS).map(([topic,name])=>{
    const items=lessons.filter(d=>d.group===group&&(d.topic??'existing')===topic);
    return items.length?<details className="scenario-topic" key={topic} open={active?.group===group&&(active.topic??'existing')===topic||undefined}><summary>{name}<span>{items.length}</span></summary><div>{buttons(items)}</div></details>:null;
  }):buttons(lessons.filter(d=>d.group===group))}</div></details>)}</div></details>;
}

export default function TeachingPanel({lesson,onPick}:{lesson?:Manifest['teaching'];onPick:(id:string)=>void}) {
  if(!lesson)return null;
  return <section className={`lesson-panel ${lesson.evidence==='protocol-model'?'modeled':''}`}>
    <div className="lesson-heading"><b>观察重点</b><span>{lesson.evidence==='gem5'?'真实 CPU 运行节选':'协议推演 · 非 gem5 实测'}</span>{lesson.lookup&&<strong>SLC {lesson.lookup.slcHit?'hit':'miss'} · SF {lesson.lookup.sfHit?'hit':'miss'}</strong>}</div>
    <p>{lesson.summary}</p>
    <div className="lesson-checkpoints">{lesson.checkpoints.map((c,i)=><button key={c.eventId} onClick={()=>onPick(c.eventId)}>{i+1}. {c.title} ↗</button>)}</div>
    <details><summary>准备条件、结果与依据</summary>{!!lesson.requirements?.length&&<p className="lesson-requirements"><b>能力前提：</b>{lesson.requirements.join(' · ')}</p>}<ul>{lesson.prerequisites.map(p=><li key={p}>{p}</li>)}</ul><p>{lesson.outcome}</p><div className="lesson-sources">{lesson.sources.filter(s=>s.url).map(s=><a key={s.url} href={s.url} target="_blank" rel="noreferrer">{s.label} ↗</a>)}</div></details>
  </section>;
}
