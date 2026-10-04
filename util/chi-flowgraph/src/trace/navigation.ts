import type { Session } from './types';

/** A lesson may point at an RX, whose time must survive a view conversion. */
export function resolveCheckpoint(session:Session,raw:boolean,id:string):{id:string;tick:bigint}|undefined {
  const event=session.eventById[id];
  if(!event)return;
  const tick=BigInt(event.type==='protocol'?event.anchorTick:event.tick);
  if((raw?session.indexes.raw:session.indexes.refined)[id]!==undefined)return {id,tick};
  if(raw&&event.type==='protocol') {
    const source=event.sourceEventIds.find(ref=>session.indexes.raw[ref]!==undefined);
    if(source)return {id:source,tick};
  } else if(!raw&&event.type==='observed') {
    const message=session.protocol.find(p=>p.kind==='flit'&&p.sourceEventIds.includes(id));
    if(message)return {id:message.id,tick};
  }
}
