import { useState } from 'react';
import { ArrowUpRight, Braces, Database, FileText, Link2 } from 'lucide-react';
import type { FieldValue, ObservedEvent, ProtocolEvent, Session, ValidationResult } from '../trace/types';
import { CHANNEL_COLORS } from './NetworkScene';

export const ORIGIN_LABELS={observed:'实测',derived:'推导',modeled:'建模',unknown:'未知'};
const ruleNotes:Record<string,string>={
  'B2.5.1':'读请求、返回数据与完成确认使用各自适用的 ID。CompAck 的 TxnID 来自完成消息的 DBID。',
  'B2.5.3':'写数据的 TxnID 来自 DBIDResp / CompDBIDResp 的 DBID；请求 TxnID 用于匹配响应。',
  'B2.7.5':'HN-F → SN-F 的 Order=01 表示请求接收确认。ReadReceipt 不是数据返回，也不携带有效 DBID。',
  'B2.10':'重发需要 RetryAck 和适合的 P-Credit。PCrdGrant 不属于某个 TxnID，信用类型和节点对必须匹配。',
  'B2.9.4':'256-bit DAT 携带 32 字节。64 字节 cache line 使用 DataID 0、2，可乱序返回。',
  'B2.4':'协议 ID 在节点及分配生命周期内有效。原始模拟 ID 与协议 ID 分开保存。',
};
const display=(value:unknown)=>value===null||value===undefined?'—':typeof value==='object'?JSON.stringify(value):String(value);
interface Props {session:Session;event?:ProtocolEvent|ObservedEvent;onJump:(id:string)=>void}
export default function FlitDetails({session,event,onJump}:Props){
  const [tab,setTab]=useState('fields');
  if(!event)return <aside className="detail-panel panel"><div className="panel-heading"><Braces size={16}/>消息详情</div><div className="empty-detail"><Link2 size={28}/><p>选择 flit 或时序图中的消息</p><span>查看字段来源、数据与事务关联</span></div></aside>;
  const protocol=event.type==='protocol';
  const fields:Record<string,FieldValue>=protocol?event.fields:Object.fromEntries(Object.entries(event.fields??{}).map(([k,v])=>[k,{value:typeof v==='object'?JSON.stringify(v):v as FieldValue['value'],origin:event.origin??'observed' as const}]));
  const rules=protocol?[event.rule]:[];
  const checks=session.validations.filter(v=>v.eventIds.includes(event.id)||v.rootId===event.rootId);
  const raw=protocol?event.sourceEventIds.map(id=>session.eventById[id]).filter(Boolean):[];
  return <aside className="detail-panel panel">
    <div className="panel-heading"><Braces size={16}/><span>消息详情</span><span className="mini-id">{event.id}</span></div>
    <div className="message-title"><span className="channel-tag" style={{color:event.channel?CHANNEL_COLORS[event.channel]:undefined}}>{event.channel??event.kind.toUpperCase()}</span><h2>{event.opcode??event.kind}</h2><span className={`origin ${protocol?event.origin:event.origin??'observed'}`}>{protocol?ORIGIN_LABELS[event.origin]:event.origin==='modeled'?'教学建模':'原始事件'}</span></div>
    <div className="message-route"><span>{event.source??(event.type==='observed'?event.node:undefined)??'—'}</span><ArrowUpRight size={15}/><span>{event.target??'—'}</span></div>
    <div className="message-meta"><span>PA <b>{event.address??'—'}</b></span><span>{session.manifest.source.kind==='educational_model'?'教学时刻':'实际锚点'} <b>{protocol?event.anchorTick:event.tick}</b></span></div>
    {event.description&&<p className="message-description">{event.description}</p>}
    <div className="detail-tabs"><button className={tab==='fields'?'active':''} onClick={()=>setTab('fields')}><Braces size={13}/>字段</button><button className={tab==='data'?'active':''} onClick={()=>setTab('data')}><Database size={13}/>数据</button><button className={tab==='rules'?'active':''} onClick={()=>setTab('rules')}><FileText size={13}/>规则 <span>{checks.length}</span></button></div>
    <div className="detail-scroll">
      {tab==='fields'&&<><div className="field-table"><div className="field-table-head"><span>FIELD</span><span>VALUE / SOURCE</span></div>{Object.entries(fields).map(([name,field])=><div className="field-row" key={name}><span className="field-name">{name}</span><div><b>{display(field.value)}</b>{field.applicable===false&&<span className="origin-text">不适用</span>}<span className={`origin-text ${field.origin}`}>{ORIGIN_LABELS[field.origin]}</span>{field.sourceEventId&&<button className="source-link" onClick={()=>onJump(field.sourceEventId!)} title={`跳转到 ${field.sourceEventId}`}><Link2 size={11}/>{field.sourceField??field.sourceEventId}</button>}</div></div>)}</div>
      <div className="relation-card"><span className="section-label">TRANSACTION IDENTITY</span><p><span>逻辑事务</span><b>{event.rootId}</b></p><p><span>事务分支</span><b>{event.legId}</b></p>{event.parentLegId&&<p><span>父分支</span><b>{event.parentLegId}</b></p>}{event.attemptId&&<p><span>重试尝试</span><b>{event.attemptId}</b></p>}</div>
      {raw.length>0&&<details className="raw-details"><summary>{session.manifest.source.kind==='educational_model'?'教学消息':'gem5 原始消息'} · {raw.length} 个锚点</summary>{raw.map(r=><div key={r.id}><button className="source-link" onClick={()=>onJump(r.id)}>{r.id} ↗</button><pre>{JSON.stringify(r,null,2)}</pre></div>)}</details>}</>}
      {tab==='data'&&<><span className="section-label">PAYLOAD · {event.payload?event.payload.length/2:0} BYTES</span>{event.payload?<div className="hex-grid">{(event.payload.match(/../g)??[]).map((byte,i)=>{const known=Boolean(event.validMask)&&event.validMask!.slice(i*2,i*2+2).toLowerCase()==='ff';const enabled=event.opcode==='CompData'||event.opcode==='DataSepResp'||!event.byteEnable||event.byteEnable.slice(i*2,i*2+2)!=='00';return <span className={!known?'unknown-byte':!enabled?'disabled-byte':''} key={i} title={`byte ${i}${!enabled?' · BE=0':''}`}>{known?byte:'??'}</span>;})}</div>:<div className="inline-empty">此消息没有已记录的数据字节。</div>}<div className="relation-card"><p><span>数据来源</span><b>{event.payloadOrigin??'unknown'}</b></p><p><span>DataID</span><b>{display(fields.DataID?.value??event.fields?.dataid)}</b></p><p><span>有效字节</span><b>{event.validMask?'按掩码保留':'未记录掩码'}</b></p></div><p className="small-note">?? 表示未采集到的字节。写入或 snoop 中 BE=0 的字节淡显；读数据的 BE 不适用。</p></>}
      {tab==='rules'&&<>{rules.map(rule=><div className="rule-card" key={rule}><span className="section-label">ARM IHI 0050 · ISSUE H</span><h3>{rule}</h3><p>{ruleNotes[rule]??'按适用 opcode 与事务条件检查字段和消息关联。'}</p></div>)}{checks.length?checks.map(check=><Check key={check.id} check={check}/>):<div className="inline-empty">此事件尚无可执行规则结果。</div>}<p className="small-note">CHIron E.b 的检查覆盖共同子集；其通过结果不能替代完整 Issue H 验证。</p></>}
    </div>
  </aside>;
}
function Check({check}:{check:ValidationResult}){return <div className={`check-row ${check.status}`}><span className="check-dot"/><div><b>{check.engine} · {check.status==='pass'?'通过':check.status==='fail'?'失败':check.status==='unsupported'?'未支持':'证据不足'}</b><p>{check.message}</p><span>{check.rule}</span></div></div>;}
