import { useMemo } from 'react';
import type { Session } from '../trace/types';
export const TARGET_OPCODES=['ReadOnce','ReadNoSnp','ReadUnique','CleanShared','CleanInvalid','MakeInvalid','MakeUnique','Evict','WriteEvictFull','WriteUniquePtl','WriteUniqueFull','WriteBackFull','WriteNoSnpPtl','WriteNoSnpFull','StashOnceShared','StashOnceUnique','ReadOnceCleanInvalid','ReadOnceMakeInvalid','ReadNotSharedDirty'];
export default function CoveragePanel({session,onPick}:{session:Session;onPick:(id:string)=>void}) {
  const rows=useMemo(()=>{
    if(session.manifest.source.kind!=='gem5_run'||session.manifest.teaching?.evidence==='protocol-model')return [];
    const cpu=new Set(session.observed.filter(e=>e.kind==='instruction'&&(e.origin===undefined||e.origin==='observed')&&(e.instruction?.origin===undefined||e.instruction.origin==='observed'||e.instruction.origin==='cpu')).map(e=>e.instructionId));
    if(!cpu.size)return [];
    const rn=new Set(session.manifest.topology.filter(n=>n.role==='RN-F').map(n=>n.id));
    const hn=new Set(session.manifest.topology.filter(n=>n.role==='HN-F').map(n=>n.id));
    const done=new Set(session.observed.filter(e=>e.kind==='complete'&&rn.has(e.node??'')&&(e.origin===undefined||e.origin==='observed')).map(e=>e.legId));
    const reqs=new Map<string,typeof session.observed[number]>();
    for(const e of session.observed) if(e.kind==='flit'&&e.channel==='REQ'&&e.observation!=='RX'&&(e.origin===undefined||e.origin==='observed')&&rn.has(e.source??'')&&hn.has(e.target??'')&&e.causeInstructionIds?.some(id=>cpu.has(id))) reqs.set(e.legId,e);
    return TARGET_OPCODES.map(op=>{const requests=[...reqs.values()].filter(e=>e.opcode===op);return {op,count:requests.length,done:requests.filter(e=>done.has(e.legId)).length,id:requests[0]?.id};});
  },[session]);
  if(!rows.length)return null;
  return <details className="coverage-panel" open><summary>CPU 事务覆盖 <b>{rows.filter(r=>r.done>0).length} / 19</b></summary><div>{rows.map(row=><button key={row.op} disabled={!row.id} className={row.done?'covered':''} onClick={()=>row.id&&onPick(row.id)}><span>{row.op}</span><small>{row.count} 发起 / {row.done} 完成</small></button>)}</div><p>只统计有 CPU 来源证据的 RN → HN 请求；下游同名事务不计入。</p></details>;
}
