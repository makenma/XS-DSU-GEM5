// Exercise the same parser/state engine used by the import Worker.
import {createServer} from 'vite';
import {readFileSync, mkdirSync, writeFileSync} from 'node:fs';
import assert from 'node:assert/strict';
const input = process.argv[2] || 'examples/native-l2/run.chi.jsonl';
const output = process.argv[3] || 'build/qa/native-replay.json';
const server = await createServer({server:{middlewareMode:true},appType:'custom'});
try {
  const {parseSession, ReplayEngine} = await server.ssrLoadModule('/src/trace/engine.ts');
  const session = parseSession(readFileSync(input,'utf8'));
  assert.equal(session.manifest.profile,'h-native-256');
  assert.equal(session.validations.filter(v=>v.status==='fail').length,0);
  const checks=[];
  for(const raw of [false,true]) {
    const total=(raw?session.rawSteps:session.steps).length;
    const sequential=new ReplayEngine(session,raw), random=new ReplayEngine(session,raw);
    const checkpoints=new Set([0,1,255,256,Math.floor(total/3),Math.floor(total/2),total-1]);
    const snapshots=new Map();
    for(let i=0;i<total;++i) {
      const state=sequential.seek(i);
      if(checkpoints.has(i))snapshots.set(i,JSON.stringify(state));
    }
    for(const cursor of [...snapshots.keys()].reverse()) {
      assert.equal(JSON.stringify(random.seek(cursor)),snapshots.get(cursor),`seek mismatch: raw=${raw}, cursor=${cursor}`);
    }
    const final=random.seek(total-1);
    assert.equal(Object.keys(final.active).length,0,`unclosed active legs: raw=${raw}`);
    for(const [id,node] of Object.entries(final.nodes)) {
      assert.equal(Object.keys(node.inflight).length,0,`unclosed inflight at ${id}: raw=${raw}`);
      assert.equal(Object.keys(node.waits).length,0,`unclosed waits at ${id}: raw=${raw}`);
    }
    checks.push({raw,steps:total,randomSeeks:snapshots.size,finalInflight:0,finalWaits:0});
  }
  const report={input,profile:session.manifest.profile,observed:session.observed.length,
    protocol:session.protocol.length,checks,passed:true};
  mkdirSync('build/qa',{recursive:true});
  writeFileSync(output,JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify(report,null,2));
} finally {await server.close();}
