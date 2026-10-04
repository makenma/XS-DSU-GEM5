import { useEffect, useMemo, useRef, useState } from 'react';
import type { Instruction, Session, Step } from '../trace/types';
import { sourceView } from '../trace/source';

type Entry={instruction:Instruction;eventId:string};
interface Props {session:Session;steps:Step[];cursor:number;executionCursor:number;activeCauseIds:string[];playing:boolean;onPick:(id:string)=>void}
export default function LitmusPanel({session,steps,cursor,executionCursor,activeCauseIds,playing,onPick}:Props) {
  const [expanded,setExpanded]=useState(true),[selection,setSelection]=useState(''),[selectedInstance,setSelectedInstance]=useState(''),[assembly,setAssembly]=useState(false);
  const panel=useRef<HTMLElement>(null);
  useEffect(()=>{setSelection('');setSelectedInstance('');setAssembly(false);},[session]);
  const index=useMemo(()=>{
    const instructions=new Map<string,Entry>(),transactions=new Map<string,string[]>(),lineInstances=new Map<string,Entry[]>();
    const executions=new Map<number,{order:number;instruction:Instruction}[]>(),cancellations=new Map<string,number>();
    for(const e of session.observed)if(e.kind==='instruction'&&e.instruction)instructions.set(e.instruction.id,{instruction:{...e.instruction},eventId:e.id});
    for(const e of session.observed)if(e.kind==='instruction_context'&&e.instructionId&&e.fields?.callSite){const entry=instructions.get(e.instructionId);if(entry)entry.instruction.callSite=e.fields.callSite as Instruction['callSite'];}
    for(const entry of instructions.values())for(const loc of [entry.instruction,entry.instruction.callSite])if(loc?.sourceId&&loc.line){const key=`${loc.sourceId}:${loc.line}`,rows=lineInstances.get(key)??[];if(!rows.includes(entry))rows.push(entry);lineInstances.set(key,rows);}
    const seen=new Set<string>();
    for(const step of steps){
      const e=step.event;
      if(e.type==='observed'&&e.kind==='instruction'&&e.instruction){const rows=executions.get(e.instruction.hartId)??[];rows.push({order:step.order,instruction:e.instruction});executions.set(e.instruction.hartId,rows);}
      if(e.type==='observed'&&e.kind==='instruction_cancel'&&e.instructionId)cancellations.set(e.instructionId,step.order);
      if(e.kind!=='flit'||e.channel!=='REQ'||(e.type==='observed'&&e.observation==='RX'))continue;
      for(const id of e.causeInstructionIds??[]){
        const instruction=instructions.get(id)?.instruction;
        const node=session.manifest.litmus?.threads.find(t=>t.hartId===instruction?.hartId)?.node;
        if(!node||e.source!==node||session.manifest.topology.find(n=>n.id===e.target)?.role!=='HN-F')continue;
        const key=`${id}:${e.messageId??e.id}`;if(seen.has(key))continue;seen.add(key);
        const rows=transactions.get(id)??[];rows.push(step.id);transactions.set(id,rows);
      }
    }
    return {instructions,transactions,lineInstances,executions,cancellations};
  },[session,steps]);
  const current=steps[cursor]?.event;
  const causes=(playing?activeCauseIds:current?.causeInstructionIds??[]).map(id=>index.instructions.get(id)?.instruction).filter((i):i is Instruction=>Boolean(i));
  const producers=(current?.dataProducerInstructionIds??[]).map(id=>index.instructions.get(id)?.instruction).filter((i):i is Instruction=>Boolean(i));
  const executions:Instruction[]=[];
  for(const rows of index.executions.values()){
    let lo=0,hi=rows.length;while(lo<hi){const mid=(lo+hi)>>>1;if(rows[mid].order<=executionCursor)lo=mid+1;else hi=mid;}
    const execution=rows[lo-1]?.instruction;
    if(execution&&(index.cancellations.get(execution.id)??Infinity)>executionCursor)executions.push(execution);
  }
  const location=causes.map(c=>`${c.sourceId}:${c.line}:${c.callSite?.line}`).join(',');
  useEffect(()=>{if(!expanded)return;panel.current?.querySelectorAll<HTMLElement>('.source-code').forEach(code=>{const line=code.querySelector<HTMLElement>('.cause-call')??code.querySelector<HTMLElement>('.cause')??code.querySelector<HTMLElement>('.chosen');if(line){const top=line.offsetTop;if(top<code.scrollTop||top+line.offsetHeight>code.scrollTop+code.clientHeight)code.scrollTop=Math.max(0,top-code.clientHeight/2);}});},[location,expanded,assembly,selection]);
  const litmus=session.manifest.litmus;if(!litmus)return null;
  const instances=index.lineInstances.get(selection)??[];
  const pickInstance=(entry:Entry)=>{setSelectedInstance(entry.instruction.id);onPick(index.transactions.get(entry.instruction.id)?.[0]??entry.eventId);};
  const pickLine=(sourceId:string,lines:number[])=>{
    const choices=lines.map(line=>({key:`${sourceId}:${line}`,rows:index.lineInstances.get(`${sourceId}:${line}`)??[]}));
    const choice=choices.find(c=>c.rows.some(e=>index.transactions.has(e.instruction.id)))??choices.find(c=>c.rows.length)??choices[0];
    setSelection(choice?.key??`${sourceId}:unmapped`);setSelectedInstance('');
    const rows=choice?.rows??[],entry=rows.find(e=>causes.some(c=>c.id===e.instruction.id)&&index.transactions.has(e.instruction.id))??rows.find(e=>index.transactions.has(e.instruction.id))??rows[0];
    if(entry)pickInstance(entry);
  };
  const hasAssembly=litmus.threads.some(t=>t.language==='riscv');
  return <section className="litmus-panel" ref={panel}>
    <button className="litmus-heading" onClick={()=>setExpanded(v=>!v)} aria-expanded={expanded}><b>{expanded?'▾':'▸'} {litmus.name} · {litmus.threads.length} 线程代码</b><span><i className="cause-key"/>事务来源 <i className="execution-key"/>执行观测 <i className="producer-key"/>历史写入者</span></button>
    {expanded&&<><div className="code-view-bar"><div role="tablist" aria-label="代码视图"><button role="tab" aria-selected={!assembly} onClick={()=>setAssembly(false)}>C / 源码</button><button role="tab" aria-selected={assembly} disabled={!hasAssembly} title={hasAssembly?'实际执行的 RV64 汇编':'此会话采集的是 C 源码，未附带反汇编'} onClick={()=>setAssembly(true)}>ASM 汇编</button></div><span>{sourceView(litmus.threads[0],litmus.name,assembly).note}</span></div>
    <div className="litmus-context"><span>初始值 {JSON.stringify(litmus.initial??{})}</span><span>{session.manifest.source.kind==='educational_model'?'推演结果':'实际结果'} {JSON.stringify(litmus.results??{})}</span></div>
    <div className="thread-grid" style={{gridTemplateColumns:`repeat(${Math.min(4,litmus.threads.length)}, minmax(0, 1fr))`}}>{litmus.threads.map(thread=><article key={thread.id}><h3>{thread.title} <small>CPU{thread.hartId} → {thread.node}</small></h3><div className="source-code">{sourceView(thread,litmus.name,assembly).lines.map((line,i)=>{
      const matches=(ins:Instruction)=>(ins.sourceId===thread.id&&line.sourceLines.includes(ins.line??-1))||(ins.callSite?.sourceId===thread.id&&line.sourceLines.includes(ins.callSite.line));
      const cause=causes.some(matches),executing=executions.some(ins=>ins.sourceId===thread.id&&line.sourceLines.includes(ins.line??-1));
      return <button key={i} data-source-id={thread.id} data-line={i+1} data-source-lines={line.sourceLines.join(',')} title={cause?'当前事务来源；点击定位本核 RN→HN 请求':'点击定位执行实例及其 RN→HN 请求'} className={[cause?'cause':'',causes.some(c=>c.callSite?.sourceId===thread.id&&line.sourceLines.includes(c.callSite.line))?'cause-call':'',executing?'executing':'',producers.some(matches)?'producer':'',line.sourceLines.some(n=>selection===`${thread.id}:${n}`)?'chosen':''].join(' ')} onClick={()=>pickLine(thread.id,line.sourceLines)}><span>{i+1}</span><code>{line.text||' '}</code>{cause&&<em>事务</em>}{executing&&<em className="execution-label">执行</em>}</button>;
    })}</div></article>)}</div>
    <div className="instruction-instances">{selection?<><span>动态执行实例</span><select aria-label="代码行的动态执行实例" value={selectedInstance} onChange={e=>{const entry=index.instructions.get(e.target.value);if(entry)pickInstance(entry);}}><option value="">选择实例…</option>{instances.map(({instruction})=><option key={instruction.id} value={instruction.id}>CPU{instruction.hartId} · #{instruction.dynamicSeq} · {instruction.origin==='modeled'?'教学操作':'PC '+instruction.pc} · {index.transactions.get(instruction.id)?.length??0} 个本核请求{index.cancellations.has(instruction.id)?' · CPU 已撤销':''}</option>)}</select>{!instances.length?<small>该行没有采集到执行证据</small>:selectedInstance&&!index.transactions.has(selectedInstance)&&<small>已定位执行记录 · 无关联 CHI 请求</small>}</>:<span>{causes.length?`当前事务关联 ${causes.length} 条动态指令`:'当前事务来源未知'} · 点击代码直接定位事务，可切换动态实例</span>}</div></>}
  </section>;
}
