import {mkdirSync,readFileSync,writeFileSync} from 'node:fs';
const manifest=JSON.parse(readFileSync('examples/coarse/miss-hit.coarse.jsonl','utf8').split('\n')[0]);
manifest.title='性能验收 · 100,000 事件';manifest.description='合成性能流量；用于检查导入与渲染，不代表 gem5 协议验证。';manifest.source={kind:'performance_fixture',generator:'generate-benchmark.mjs'};manifest.complete=false;
const records=[JSON.stringify(manifest)];
for(let row=0;row<100_000;row++){
  const i=row%50000,rx=row>=50000;
  const channel=['REQ','RSP','DAT','SNP'][i%4],source=channel==='REQ'?'RN0':'HN0',target=channel==='REQ'?'HN0':channel==='SNP'?'RN1':'RN0';
  records.push(JSON.stringify({type:'observed',id:`stress:${rx?'rx':'tx'}:${i}`,seq:row,messageId:`message:${i}`,observation:rx?'RX':'TX',tick:String(9007199254740993n+BigInt(i)+(rx?100000n:0n)),kind:'flit',rootId:`stress:${Math.floor(i/200)}`,legId:`stress:${Math.floor(i/200)}/up`,source,target,channel,opcode:{REQ:'ReadShared',RSP:'CompAck',DAT:'CompData',SNP:'SnpUnique'}[channel],address:'0x80004040',fields:{}}));
}
mkdirSync('public/benchmarks',{recursive:true});writeFileSync('public/benchmarks/100k.chi.jsonl',records.join('\n')+'\n');
console.log('Generated explicitly labeled 100,000-event performance fixture. Open /?benchmark=1 and seek tick 9007199254741192 for 200 flits.');
