import { readdirSync, readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { resolve } from 'node:path';
const binary=resolve(`build/chi-refine${process.platform==='win32'?'.exe':''}`);
mkdirSync('public/demos',{recursive:true});
const catalog=[];
for(const file of readdirSync('examples/coarse').filter(f=>f.endsWith('.coarse.jsonl')).sort()){
  const id=file.replace('.coarse.jsonl',''),target=`${id}.chi.jsonl`;
  const result=spawnSync(binary,['--input',`examples/coarse/${file}`,'--output',`public/demos/${target}`,'--profile','h-core-256'],{stdio:'inherit'});
  if(result.error)throw result.error;if(result.status!==0)process.exit(result.status??1);
  const manifest=JSON.parse(readFileSync(`public/demos/${target}`,'utf8').split('\n')[0]);
  catalog.push({id,title:manifest.title,file:target,description:manifest.description});
}
const result=spawnSync(binary,['--input','examples/coarse/miss-hit.coarse.jsonl','--output','public/demos/ordered-read.chi.jsonl','--profile','h-core-256','--ordered-read'],{stdio:'inherit'});
if(result.status!==0)process.exit(result.status??1);
const ordered=readFileSync('public/demos/ordered-read.chi.jsonl','utf8').trim().split('\n').map(JSON.parse);
ordered[0].title='有序读取 · Order=01 与 ReadReceipt';
ordered[0].description='复用实际 miss/hit 观测；显式协议配置补出 ReadReceipt，只有逻辑顺序。';
writeFileSync('public/demos/ordered-read.chi.jsonl',ordered.map(JSON.stringify).join('\n')+'\n');
catalog.push({id:'ordered-read',title:ordered[0].title,file:'ordered-read.chi.jsonl',description:ordered[0].description});
for(const file of readdirSync('public/demos').filter(f=>(f.startsWith('litmus-')||f.startsWith('teach-'))&&f.endsWith('.chi.jsonl'))){
  const manifest=JSON.parse(readFileSync(`public/demos/${file}`,'utf8').split('\n')[0]);
  catalog.push({id:file.replace('.chi.jsonl',''),title:manifest.title,file,description:manifest.description,...(manifest.teaching?{group:manifest.teaching.group,topic:manifest.teaching.topic,evidence:manifest.teaching.evidence}:{})});
}
const priority=id=>id==='litmus-iriw'?0:id==='litmus-protocol'?1:id==='miss-hit'?3:id.startsWith('litmus-')?2:4;
catalog.sort((a,b)=>priority(a.id)-priority(b.id)||a.id.localeCompare(b.id));
writeFileSync('public/demos/catalog.json',JSON.stringify(catalog,null,2)+'\n');
