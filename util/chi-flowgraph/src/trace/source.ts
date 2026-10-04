import type { Manifest } from './types';

export type SourceLine={text:string;sourceLines:number[]};
export function sourceView(thread:NonNullable<Manifest['litmus']>['threads'][number],name:string,assembly:boolean):{lines:SourceLine[];note:string} {
  const lines=thread.code.trimEnd().split('\n').map((text,i)=>({text,sourceLines:[i+1]}));
  if(assembly||thread.language!=='riscv'||name!=='IRIW')return {lines,note:thread.language==='riscv'?'实际 RV64 汇编':thread.language==='c-model'?'C 教学示意 · 未编译运行，消息来自协议推演':'实际 C 源码'};
  // The IRIW ELF is assembled directly. Only explicitly matched instructions
  // receive a mapping; the C view is an explanation, not a fabricated DWARF source.
  const body:SourceLine[]=[{text:`// P${thread.hartId} · 初始 x = y = 0`,sourceLines:[]}];
  for(const line of lines){
    const store=line.text.match(/sd t0, (?:0|64)\(s0\) # ([xy]) = 1/);
    const read=line.text.match(/ld t[12], (?:0|64)\(s0\) # (r[01]) = ([xy])/);
    if(store)body.push({...line,text:`WRITE_ONCE(${store[1]}, 1);`});
    if(read)body.push({...line,text:`uint64_t ${read[1]} = READ_ONCE(${read[2]});`});
  }
  body.push({text:'// ↓ 结果采集（测试主体之外）',sourceLines:[]});
  for(const line of lines){
    if(/sd t[12], 10(?:24|32)\(s1\)/.test(line.text))body.push({...line,text:`record_result(${line.text.includes('t1')?'0, r0':'1, r1'});`});
    if(line.text.includes('fence rw, rw'))body.push({...line,text:'fence_rw_rw();'});
    if(line.text.includes('sd t0, 1024(t3)'))body.push({...line,text:'report_done(1);'});
  }
  return {lines:body,note:'C 等价示意 · 逐行关联实际 RV64 指令；非 C 编译产物'};
}
