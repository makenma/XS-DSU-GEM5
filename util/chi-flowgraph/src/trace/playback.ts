import type { Channel, ObservedEvent, Session, Step } from './types';

export function cursorAtTick(steps:Step[],tick:bigint):number {
  let lo=0,hi=steps.length;
  while(lo<hi){const mid=(lo+hi)>>>1;if(BigInt(steps[mid].anchorTick)<=tick)lo=mid+1;else hi=mid;}
  return lo-1;
}

/** One simulation clock drives every core, link, and view. Absolute ticks never
 * pass through Number, including captures starting above 2^53. */
export class SimulationClock {
  private base=0n;
  private wall=0;
  private running=false;
  private rate=20000;
  private limit:bigint|undefined;
  end=0n;
  read(now=performance.now()):bigint {
    const delta=this.running?BigInt(Math.floor(Math.max(0,now-this.wall)*this.rate/1000)):0n;
    const tick=this.base+delta,limit=this.limit??this.end;return tick>limit?limit:tick;
  }
  reset(tick:bigint,end:bigint,now=performance.now()){this.end=end;this.limit=undefined;this.running=false;this.base=tick;this.wall=now;}
  seek(tick:bigint,now=performance.now()){this.base=tick<0n?0n:tick>this.end?this.end:tick;this.limit=undefined;this.wall=now;}
  play(now=performance.now()){if(!this.running){this.limit=undefined;this.wall=now;this.running=true;}}
  /** Stretch an entire interval, never individual packets. Concurrency and all
   * measured tick positions remain shared even in presentation-paced mode. */
  advanceTo(target:bigint,durationMs:number,now=performance.now()){
    this.base=this.read(now);this.limit=target>this.end?this.end:target;
    this.rate=Number(this.limit-this.base)*1000/Math.max(1,durationMs);this.wall=now;this.running=true;
  }
  pause(now=performance.now()){this.base=this.read(now);this.wall=now;this.running=false;}
  setRate(ticksPerSecond:number,now=performance.now()){this.base=this.read(now);this.wall=now;this.rate=Math.max(1,ticksPerSecond);}
}

export interface TimedTransfer {
  id:string;rawId:string;protocolId?:string;rootId:string;
  source:string;target:string;channel:Channel;opcode:string;
  start:bigint;end:bigint;timed:boolean;
  sent:boolean;received:boolean;eventIds:string[];
  causeInstructionIds:string[];
  hops:{from:string;to:string;tick:bigint}[];
}

export class TransportTimeline {
  transfers:TimedTransfer[]=[];
  moments:bigint[]=[];
  byEvent=new Map<string,TimedTransfer>();
  private prefixEnd:bigint[]=[];
  private ends:bigint[]=[];
  constructor(session:Session) {
    const messages=new Map<string,ObservedEvent[]>();
    for(const e of session.observed)if(e.kind==='flit'||e.kind==='route'){
      const key=e.messageId??e.id,rows=messages.get(key)??[];rows.push(e);messages.set(key,rows);
    }
    for(const [id,rows] of messages){
      const flits=rows.filter(e=>e.kind==='flit');if(!flits.length)continue;
      const sent=flits.find(e=>e.observation==='TX'),tx=sent??flits[0];
      const start=BigInt(tx.tick),rx=flits.filter(e=>e.observation==='RX'&&BigInt(e.tick)>=start).at(-1);
      const transfer:TimedTransfer={id,rawId:tx.id,rootId:tx.rootId,source:tx.source!,target:tx.target!,channel:tx.channel!,opcode:tx.opcode??'',
        start,end:rx?BigInt(rx.tick):start,timed:Boolean(sent&&rx),sent:Boolean(sent),received:Boolean(rx),eventIds:rows.map(e=>e.id),causeInstructionIds:tx.causeInstructionIds??[],
        hops:rows.filter(e=>e.kind==='route').map(e=>({from:String(e.fields?.fromRouter),to:String(e.fields?.toRouter),tick:BigInt(e.tick)}))};
      this.transfers.push(transfer);for(const e of rows)this.byEvent.set(e.id,transfer);
    }
    for(const e of session.protocol)if(e.kind==='flit'){
      // sourceEventIds may describe causality, not packet identity (for example
      // a modeled ReadReceipt derived from a ReadNoSnp request). Never animate
      // that response on the request's path or silently collapse it into REQ.
      const transfer=e.sourceEventIds.map(id=>this.byEvent.get(id)).find(t=>{
        if(!t||t.source!==e.source||t.target!==e.target||t.channel!==e.channel||t.opcode!==e.opcode)return false;
        if(e.messageId&&e.messageId!==t.id)return false;
        const source=session.eventById[t.rawId];
        if(e.channel==='DAT'&&source?.type==='observed'){
          const observedId=source.fields?.DataID??source.fields?.dataId;
          if(observedId!==undefined&&e.fields.DataID?.value!==undefined&&Number(observedId)!==Number(e.fields.DataID.value))return false;
        }
        return true;
      });
      if(transfer){transfer.protocolId??=e.id;transfer.eventIds.push(e.id);this.byEvent.set(e.id,transfer);}
    }
    this.transfers.sort((a,b)=>a.start<b.start?-1:a.start>b.start?1:a.id.localeCompare(b.id));
    let max=0n;for(const t of this.transfers){if(t.end>max)max=t.end;this.prefixEnd.push(max);}
    this.ends=this.transfers.map(t=>t.end).sort((a,b)=>a<b?-1:a>b?1:0);
    this.moments=[...new Set(session.observed.filter(e=>e.kind==='flit'||e.kind==='route').map(e=>BigInt(e.tick)))].sort((a,b)=>a<b?-1:a>b?1:0);
  }
  adjacent(tick:bigint,direction:1|-1):bigint|undefined {
    let lo=0,hi=this.moments.length;
    while(lo<hi){const mid=(lo+hi)>>>1;if(direction===1?this.moments[mid]<=tick:this.moments[mid]<tick)lo=mid+1;else hi=mid;}
    return this.moments[direction===1?lo:lo-1];
  }
  sample(tick:bigint,limit=200):{transfers:TimedTransfer[];total:number} {
    let lo=0,hi=this.transfers.length;
    while(lo<hi){const mid=(lo+hi)>>>1;if(this.transfers[mid].start<=tick)lo=mid+1;else hi=mid;}
    const started=lo;lo=0;hi=this.ends.length;
    while(lo<hi){const mid=(lo+hi)>>>1;if(this.ends[mid]<tick)lo=mid+1;else hi=mid;}
    const total=started-lo,transfers:TimedTransfer[]=[];
    for(let i=started-1;i>=0&&this.prefixEnd[i]>=tick&&transfers.length<limit;i--)if(this.transfers[i].end>=tick)transfers.push(this.transfers[i]);
    return {transfers,total};
  }
}
