import { useEffect, useMemo, useRef, useState } from 'react';
import type { Session, Step } from '../trace/types';
import { TransportTimeline } from '../trace/playback';
import { buildSequenceLayout, buildSequenceMessages } from '../trace/sequence';
import { CHANNEL_COLORS } from './NetworkScene';

export default function Sequence({session,steps,rootId,currentId,onPick,transports,clockTick}:{
  session:Session;steps:Step[];rootId:string;currentId?:string;
  onPick:(id:string,tick?:bigint)=>void;transports?:TransportTimeline;clockTick?:string;
}){
  const host=useRef<HTMLDivElement>(null);
  const [viewportWidth,setViewportWidth]=useState(560);
  useEffect(()=>{
    if(!host.current)return;
    const element=host.current,observer=new ResizeObserver(()=>setViewportWidth(element.clientWidth));
    observer.observe(element);setViewportWidth(element.clientWidth);
    return()=>observer.disconnect();
  },[]);
  const timeline=useMemo(()=>transports??new TransportTimeline(session),[session,transports]);
  const messages=useMemo(()=>buildSequenceMessages(steps,timeline,rootId),[steps,timeline,rootId]);
  const selectedIndex=messages.findIndex(m=>currentId!==undefined&&m.eventIds.includes(currentId));
  const start=Math.max(0,Math.min(messages.length-160,selectedIndex-40));
  const visible=useMemo(()=>messages.slice(start,start+160),[messages,start]);
  const layout=useMemo(()=>buildSequenceLayout(visible),[visible]);
  const nodeSet=new Set(visible.flatMap(m=>[m.source,m.target]));
  const nodes=session.manifest.topology.filter(n=>nodeSet.has(n.id));
  const axisWidth=Math.max(82,...layout.ticks.map(t=>String(t).length*6+18));
  const width=Math.max(viewportWidth,axisWidth+nodes.length*88+24);
  const x=(id:string)=>axisWidth+(nodes.findIndex(n=>n.id===id)+.5)*(width-axisWidth-24)/Math.max(1,nodes.length);
  const selected=messages[selectedIndex];
  useEffect(()=>{
    if(!host.current||!selected)return;
    const tick=clockTick===undefined?selected.start:BigInt(clockTick);
    const focus=tick>=selected.start&&tick<=(selected.end??selected.start)?tick:selected.start;
    host.current.scrollTop=Math.max(0,layout.yAt(focus)-host.current.clientHeight/2+36);
  },[currentId,selected,layout,clockTick]);
  const playhead=clockTick===undefined?undefined:BigInt(clockTick);
  const showPlayhead=playhead!==undefined&&layout.ticks.length>0&&playhead>=layout.ticks[0]&&playhead<=layout.ticks.at(-1)!;
  const drawn=useMemo(()=>[...visible].sort((a,b)=>Number(a===selected)-Number(b===selected)),[visible,selected]);
  return <div className="sequence-view">
    <p className="sequence-key">时间向下 · ○ 发送 → 到达 <span>时刻等距，非延迟比例</span></p>
    <div className="sequence-scroll" ref={host}>
      {messages.length>160&&<p className="small-note">显示 {start+1}–{start+visible.length} / {messages.length} 条消息；拖动时间轴定位其他消息。</p>}
      <svg className="sequence-header" width={width} height={32} aria-hidden="true">
        <text x={10} y={21} fill="#9fb1cb" fontSize="10">tick ↓</text>
        {nodes.map(node=><text key={node.id} x={x(node.id)} y={21} textAnchor="middle" fill="#c6d5e9" fontSize="11">{node.label}</text>)}
      </svg>
      <svg width={width} height={layout.height} role="img" aria-label="当前事务协议时序图">
        {layout.ticks.map(tick=><g key={String(tick)} className="sequence-tick" data-tick={String(tick)}>
          <line x1={axisWidth-6} x2={width} y1={layout.yAt(tick)} y2={layout.yAt(tick)} stroke="#162337"/>
          <text x={axisWidth-12} y={layout.yAt(tick)+3} textAnchor="end" fill="#7f96b4" fontSize="9">{String(tick)}</text>
        </g>)}
        {nodes.map(node=><line key={node.id} x1={x(node.id)} x2={x(node.id)} y1={0} y2={layout.height} stroke="#354865" strokeDasharray="3 5"/>)}
        {drawn.map(message=>{
          const {step}=message,e=step.event,a=x(message.source),b=x(message.target);
          const y1=layout.yAt(message.start),y2=layout.yAt(message.end??message.start);
          const color=CHANNEL_COLORS[e.channel??'REQ'],active=message===selected;
          const dataId=e.type==='protocol'?e.fields.DataID?.value:e.fields?.DataID;
          const label=`${e.opcode}${dataId!==undefined?` [${dataId}]`:''}`;
          const unknown=message.timing==='complete'?'':message.timing==='receive-only'?'发送未知':message.timing==='send-only'?'到达未知':'传输时刻未知';
          const description=`${label} ${message.source} 到 ${message.target}，${message.timing==='receive-only'?'观测到达':'发送'} tick ${message.start}${message.end!==undefined?`，到达 tick ${message.end}`:`，${unknown}`}`;
          const angle=Math.atan2(y2-y1,b-a),dx=Math.cos(angle),dy=Math.sin(angle);
          const arrowLength=active?18:15,arrowHalfWidth=active?8:7;
          const arrow=`M ${b} ${y2} L ${b-arrowLength*dx+arrowHalfWidth*dy} ${y2-arrowLength*dy-arrowHalfWidth*dx} L ${b-arrowLength*dx-arrowHalfWidth*dy} ${y2-arrowLength*dy+arrowHalfWidth*dx} Z`;
          const pick=(atEnd=false)=>{
            const tick=atEnd?message.end??message.start:message.start;
            const id=atEnd&&e.type==='observed'?message.eventIds.find(id=>{
              const candidate=session.eventById[id];return candidate?.type==='observed'&&candidate.observation==='RX';
            })??step.id:step.id;
            onPick(id,tick);
          };
          return <g key={message.id} role="button" tabIndex={0} className={`sequence-message ${active?'selected':''}`}
            data-event-id={step.id} data-message-id={message.id} data-start-tick={String(message.start)} data-end-tick={message.end===undefined?undefined:String(message.end)}
            aria-pressed={active} aria-label={description} onClick={()=>pick()} onKeyDown={k=>{if(k.key==='Enter'||k.key===' '){k.preventDefault();pick();}}}>
            <title>{`${description}${message.end!==undefined?`；在途 ${message.end-message.start} tick`:''}。点击线段定位发送，点击箭头定位到达。`}</title>
            <line x1={a} x2={b} y1={y1} y2={y2} stroke="transparent" strokeWidth={14} className="sequence-hit-area"/>
            {active&&<line x1={a} x2={b} y1={y1} y2={y2} stroke={color} strokeOpacity={.22} strokeWidth={10} pointerEvents="none"/>}
            <line className="sequence-flight" x1={a} x2={b} y1={y1} y2={y2} stroke={color} strokeWidth={active?3:2} strokeDasharray={unknown?'4 4':undefined} pointerEvents="none"/>
            <path className="sequence-arrowhead" d={arrow} fill={color} stroke={color} strokeWidth={1.2} strokeLinejoin="round" pointerEvents="none"/>
            <circle cx={a} cy={y1} r={3.5} stroke={color} fill="#0a1321"/>
            <text className="sequence-label" x={(a+b)/2} y={(y1+y2)/2-8} textAnchor="middle" fill={active?'#f4f8ff':color} fontSize="10">{label}{unknown?` · ${unknown}`:''}</text>
            {y2-y1>114&&<text className="sequence-label" x={b+(b<a?10:-10)} y={y2+17} textAnchor={b<a?'start':'end'} fill={color} fontSize="9">{label} 到达</text>}
            {message.end!==undefined&&<circle role="button" tabIndex={0} aria-label={`${label} 到达 ${message.target} tick ${message.end}`} className="sequence-arrival"
              cx={b-6*dx} cy={y2-6*dy} r={16} fill="transparent" stroke="none"
              onClick={k=>{k.stopPropagation();pick(true);}} onKeyDown={k=>{if(k.key==='Enter'||k.key===' '){k.stopPropagation();k.preventDefault();pick(true);}}}><title>{`定位到达 tick ${message.end}`}</title></circle>}
          </g>;
        })}
        {showPlayhead&&<g className="sequence-playhead" pointerEvents="none" data-clock-tick={clockTick}>
          <line x1={axisWidth-6} x2={width} y1={layout.yAt(playhead)} y2={layout.yAt(playhead)} stroke="#d4faff" strokeOpacity={.8} strokeDasharray="5 5"/>
          <text x={axisWidth-10} y={layout.yAt(playhead)-9} textAnchor="end" fill="#ddfcff" fontSize="9">当前</text>
        </g>}
      </svg>
      {!visible.length&&<div className="inline-empty">选择一个事务查看消息时序。</div>}
    </div>
  </div>;
}
