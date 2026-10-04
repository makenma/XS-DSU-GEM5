import type { Manifest, NodeState, ObservedEvent, ReplayState, Session, Step, TraceRecord, ValidationResult } from './types';

const emptyNode = (): NodeState => ({ lines: {}, waits: {}, inflight: {} });
const asBigInt = (s: string, label: string) => {
  try { if (typeof s !== 'string' || !/^(?:0|[1-9][0-9]*|0x[0-9a-f]+)$/i.test(s)) throw Error(); const v = BigInt(s); if (v < 0n) throw Error(); return v; }
  catch { throw new Error(`${label} 必须是非负整数字符串：${s}`); }
};
export const canonicalAddress = (s: string) => `0x${asBigInt(s, '地址').toString(16)}`;

export function parseSession(text: string): Session {
  const lines = text.replace(/^\uFEFF/, '').split(/\r?\n/).filter(l => l.trim());
  if (!lines.length) throw new Error('轨迹文件为空。');
  const records = lines.map((line, i) => {
    try { return JSON.parse(line) as TraceRecord; }
    catch { throw new Error(`第 ${i + 1} 行不是有效 JSON。`); }
  });
  const manifest = records[0] as Manifest;
  if (!manifest || typeof manifest !== 'object') throw new Error('首行需要 Manifest 对象。');
  if (manifest.type !== 'manifest' || !['1.0','1.1'].includes(manifest.schemaVersion)) throw new Error('需要 schemaVersion 1.0 或 1.1 的 Manifest 作为第一行。');
  if (!Array.isArray(manifest.topology) || !manifest.topology.length) throw new Error('Manifest 缺少 topology。');
  const nodeIds = new Set<string>();
  for (const n of manifest.topology) {
    if (!n || typeof n.id !== 'string' || !n.id || ['__proto__','constructor','prototype'].includes(n.id) || nodeIds.has(n.id) || !Number.isSafeInteger(n.nodeId) || !['RN-F', 'HN-F', 'SN-F'].includes(n.role)) throw new Error('节点 ID 必须唯一，类型需为 RN-F、HN-F 或 SN-F。');
    nodeIds.add(n.id);
  }
  if(manifest.fabric) {
    const routers=new Set<string>();
    for(const r of manifest.fabric.routers) {
      if(!r.id||routers.has(r.id)||nodeIds.has(r.id)||!Number.isFinite(r.x)||!Number.isFinite(r.y))throw new Error('mesh router 身份和坐标无效。');
      routers.add(r.id);
    }
    for(const link of manifest.fabric.links)if(!routers.has(link.from)||!routers.has(link.to)||link.from===link.to)throw new Error('mesh 链路引用无效 router。');
    const endpoints=new Set<string>();
    for(const a of manifest.fabric.attachments) {
      if(!nodeIds.has(a.node)||!routers.has(a.router)||endpoints.has(a.node))throw new Error('mesh 端点连接无效或重复。');
      endpoints.add(a.node);
    }
  }
  const sourceIds=new Set(manifest.litmus?.threads.map(t=>t.id));
  if(manifest.litmus) {
    if(sourceIds.size!==manifest.litmus.threads.length)throw new Error('源码身份重复。');
    for(const t of manifest.litmus.threads)if(!nodeIds.has(t.node)||!Number.isSafeInteger(t.hartId)||t.hartId<0||typeof t.code!=='string')throw new Error('线程与核映射无效。');
  }
  for (const record of records.slice(1)) if (!record || !['observed','protocol','validation'].includes(record.type)) throw new Error('出现未知记录类型或重复 Manifest。');
  const observed = records.filter((r): r is ObservedEvent => r.type === 'observed');
  const protocol = records.filter((r): r is Session['protocol'][number] => r.type === 'protocol');
  const validations = records.filter((r): r is ValidationResult => r.type === 'validation');
  const eventById: Session['eventById'] = Object.create(null);
  const observedSeqs = new Set<number>();
  const instructionIds=new Set<string>();
  for (const e of [...observed, ...protocol]) {
    if (!e.id || Object.hasOwn(eventById, e.id)) throw new Error(`重复或缺失事件 ID：${e.id}`);
    if (typeof e.rootId !== 'string' || !e.rootId || typeof e.legId !== 'string' || !e.legId) throw new Error(`${e.id} 缺少稳定事务身份 rootId / legId。`);
    if (!Number.isSafeInteger(e.seq) || e.seq < 0) throw new Error(`${e.id} 的 seq 必须是非负安全整数。`);
    if (e.type === 'observed') { if (observedSeqs.has(e.seq)) throw new Error('原始事件 seq 重复。'); observedSeqs.add(e.seq); }
    const tick = e.type === 'observed' ? e.tick : e.anchorTick;
    if (typeof tick !== 'string') throw new Error(`${e.id} 的 tick 必须为字符串。`);
    asBigInt(tick, 'tick');
    if (e.address !== undefined) e.address = canonicalAddress(e.address);
    if (e.payload && (!/^[0-9a-f]*$/i.test(e.payload) || e.payload.length % 2)) throw new Error(`${e.id} 的 payload 必须为完整十六进制字节。`);
    if (e.type === 'protocol' && (!e.fields || !Array.isArray(e.sourceEventIds) || !Array.isArray(e.dependencies))) throw new Error(`${e.id} 缺少协议字段或因果关系。`);
    if (e.type === 'protocol' && (!Number.isSafeInteger(e.anchorSeq) || e.anchorSeq < 0 || !Number.isSafeInteger(e.substep) || e.substep < 0)) throw new Error(`${e.id} 的锚点顺序无效。`);
    for (const mask of [e.validMask, e.byteEnable]) if (mask !== undefined && (!/^(?:00|ff)*$/i.test(mask) || (e.payload !== undefined && mask.length !== e.payload.length))) throw new Error(`${e.id} 的字节掩码无效或长度不一致。`);
    if (e.kind === 'flit' && (!e.source || !e.target || !e.channel || !['REQ','RSP','DAT','SNP'].includes(e.channel))) throw new Error(`${e.id} 缺少通道或消息端点。`);
    for (const node of [e.source,e.target,e.type === 'observed' ? e.node : undefined]) if (node && !nodeIds.has(node)) throw new Error(`${e.id} 引用了拓扑外节点 ${node}。`);
    for(const ids of [e.causeInstructionIds,e.dataProducerInstructionIds])if(ids!==undefined&&(!Array.isArray(ids)||ids.some(id=>typeof id!=='string'||!id)))throw new Error(`${e.id} 的指令归因无效。`);
    if(e.type==='observed'&&e.kind==='instruction') {
      const i=e.instruction;
      if(!i||!i.id||instructionIds.has(i.id)||!Number.isSafeInteger(i.hartId)||i.hartId<0||!Number.isSafeInteger(i.iteration)||i.iteration<0)throw new Error('动态指令身份无效或重复。');
      asBigInt(i.dynamicSeq,'动态指令序号');asBigInt(i.pc,'PC');
      if(i.sourceId&&!sourceIds.has(i.sourceId))throw new Error('动态指令引用未知源码。');
      if(i.line!==undefined&&(!Number.isSafeInteger(i.line)||i.line<1))throw new Error('动态指令源码行号无效。');
      instructionIds.add(i.id);
    }
    eventById[e.id] = e;
  }
  observed.sort((a,b) => a.seq - b.seq);
  const rootCauses=new Map<string,Set<string>>();
  for(const e of observed) { const ids=rootCauses.get(e.rootId)??new Set<string>();for(const id of e.causeInstructionIds??[])ids.add(id);rootCauses.set(e.rootId,ids); }
  for(const e of [...observed,...protocol]) if(rootCauses.get(e.rootId)?.size)e.causeInstructionIds=[...rootCauses.get(e.rootId)!];
  for (let i=1;i<observed.length;i++) {
    if (BigInt(observed[i].tick) < BigInt(observed[i-1].tick)) throw new Error('原始事件时间倒退，不能静默重排实测轨迹。');
  }
  const linked = new Set(protocol.flatMap(e => e.sourceEventIds));
  for (const e of protocol) for (const id of [...e.sourceEventIds, ...e.dependencies]) {
    if (!eventById[id]) validations.push({ type:'validation', id:`import:${e.id}:${id}`, engine:'import', status:'insufficient', rule:'causal-links', message:`关联事件 ${id} 不在采集窗口内。`, eventIds:[e.id] });
  }
  const toRawStep = (e: ObservedEvent): Step => ({id:e.id, kind:e.kind, rootId:e.rootId, address:e.address, anchorTick:e.tick, event:e, order:0});
  const rawSteps = observed.map(toRawStep);
  const steps: Step[] = [
    ...observed.filter(e => e.kind !== 'flit' || !linked.has(e.id)).map(toRawStep),
    ...protocol.map(e => ({id:e.id, kind:e.kind, rootId:e.rootId, address:e.address, anchorTick:e.anchorTick, event:e, order:0})),
  ].sort((a,b) => {
    const ea=a.event,eb=b.event;
    return (ea.type==='protocol'?ea.anchorSeq:ea.seq)-(eb.type==='protocol'?eb.anchorSeq:eb.seq)
      || (ea.type==='protocol'?ea.substep:Number.MAX_SAFE_INTEGER)-(eb.type==='protocol'?eb.substep:Number.MAX_SAFE_INTEGER)
      || a.id.localeCompare(b.id);
  });
  const refinedIndex: Record<string,number> = Object.create(null),rawIndex: Record<string,number> = Object.create(null);
  steps.forEach((step,i)=>{step.order=i;refinedIndex[step.id]=i;});
  rawSteps.forEach((step,i)=>{step.order=i;rawIndex[step.id]=i;});
  if (manifest.initialState) for (const [id,node] of Object.entries(manifest.initialState)) {
    if (!nodeIds.has(id) || !node || !node.lines) throw new Error('初始状态包含无效节点。');
    const normalized: NodeState['lines'] = Object.create(null);
    for (const [address,line] of Object.entries(node.lines)) { const key=canonicalAddress(address); if (Object.hasOwn(normalized,key)) throw new Error('初始状态包含重复地址。'); normalized[key]=line; }
    node.lines=normalized;
  }
  return {manifest, observed, protocol, validations, steps, rawSteps, eventById, indexes:{refined:refinedIndex,raw:rawIndex}};
}

export function initialState(session: Session): ReplayState {
  const nodes: ReplayState['nodes'] = Object.create(null);
  for (const node of session.manifest.topology) nodes[node.id] = { ...emptyNode(), ...structuredClone(session.manifest.initialState?.[node.id] ?? {}) };
  return { nodes, active: {}, cursor: -1 };
}

export function reduceEvent(prev: ReplayState, step: Step, cursor: number): ReplayState {
  const event = step.event;
  let active = prev.active;
  let nodes = prev.nodes;
  if (event.type === 'protocol' && event.kind === 'flit') active = {...active, [event.legId]:event.opcode};
  if (event.kind === 'complete') {
    active = {...active}; delete active[event.legId];
  }
  if (event.type === 'observed' && event.node) {
    const old = nodes[event.node] ?? emptyNode();
    let next = old;
    if (event.kind === 'state' && event.address && event.state) next = {...old, lines:{...old.lines, [event.address]:{...event.state}}};
    if (event.kind === 'sam') next = {...old, sam:{ ...event.fields, address:event.address, target:event.target }};
    if (event.kind === 'wait_start') next = {...old, waits:{...old.waits, [event.waitId ?? event.legId]:{reason:event.reason ?? 'unknown', rootId:event.rootId, since:event.tick, blocker:event.blocker, occupancy:event.occupancy, capacity:event.capacity}}};
    if (event.kind === 'wait_end') { const waits={...old.waits}; delete waits[event.waitId ?? event.legId]; next={...old,waits}; }
    if (event.kind === 'allocate') next={...old,inflight:{...old.inflight,[event.legId]:event.opcode ?? 'active'}};
    if (event.kind === 'complete') {const inflight={...old.inflight}; delete inflight[event.legId]; next={...old,inflight};}
    if (next!==old) nodes={...nodes,[event.node]:next};
  }
  return {nodes,active,cursor};
}

export class ReplayEngine {
  private checkpoints = new Map<number, ReplayState>();
  private steps: Step[];
  private lastState: ReplayState;
  constructor(public session: Session, raw = false) {
    this.steps=raw?session.rawSteps:session.steps;
    this.lastState=initialState(session);
    this.checkpoints.set(-1, this.lastState);
  }
  seek(cursor: number): ReplayState {
    if (!Number.isFinite(cursor)) throw new Error('回放位置必须为有限数值。');
    cursor=Math.max(-1, Math.min(Math.trunc(cursor), this.steps.length-1));
    const block=Math.floor((cursor+1)/256)*256-1;
    let start=block;
    while (!this.checkpoints.has(start)) start-=256;
    let state=this.checkpoints.get(start)!;
    if (this.lastState.cursor >= start && this.lastState.cursor <= cursor) {state=this.lastState;start=state.cursor;}
    for (let i=start+1;i<=cursor;i++) {
      state=reduceEvent(state,this.steps[i],i);
      if ((i+1)%256===0) this.checkpoints.set(i,state);
    }
    this.lastState=state;
    return state;
  }
}
