const {createRequire}=require('node:module');
const {resolve}=require('node:path');
const {readFileSync,writeFileSync,mkdirSync}=require('node:fs');
const assert=require('node:assert/strict');
const runtimeRequire=process.env.CHI_NODE_MODULES?createRequire(resolve(process.env.CHI_NODE_MODULES,'../browser-replay.cjs')):require;
const {chromium}=runtimeRequire('playwright');
const records=readFileSync('public/demos/litmus-iriw.chi.jsonl','utf8').trim().split('\n').map(JSON.parse);
const observed=records.filter(r=>r.type==='observed'),protocol=records.filter(r=>r.type==='protocol');
(async()=>{
 const browser=await chromium.launch({channel:process.env.CHI_BROWSER||'chrome',headless:true});
 const page=await browser.newPage({viewport:{width:1600,height:1000}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const tick=()=>page.locator('[data-simulation-tick]').getAttribute('data-simulation-tick');
 const seek=async value=>{await page.getByLabel('定位实际 tick').fill(String(value));await page.getByRole('button',{name:'定位',exact:true}).click();await page.waitForTimeout(80);};
 try {
  await page.goto('http://127.0.0.1:5173/');await page.locator('.scene-canvas canvas').waitFor();
  assert.equal(await page.getByLabel('回放方式').inputValue(),'moments');
  // A single click, with no subsequent instance selection, must seek the RN request.
  const jumps=[];
  for(const raw of [false,true]){
   await page.getByRole('button',{name:raw?'gem5 原始':'协议展开',exact:true}).click();
   for(const [hart,line] of [[0,8],[2,9],[3,8]]){
    const ins=observed.find(e=>e.kind==='instruction'&&e.instruction.hartId===hart&&e.instruction.line===line).instruction;
    const expected=raw?observed.find(e=>e.kind==='flit'&&e.observation==='TX'&&e.source===`RN${hart}`&&e.channel==='REQ'&&e.causeInstructionIds?.includes(ins.id)):protocol.find(e=>e.kind==='flit'&&e.source===`RN${hart}`&&e.channel==='REQ'&&e.causeInstructionIds?.includes(ins.id));
    const button=page.locator(`.source-code button[data-source-id="thread-${hart}"][data-source-lines="${line}"]`);
    await button.click();assert.equal(await tick(),expected.tick??expected.anchorTick);
    assert.equal(await page.locator('.message-title h2').innerText(),expected.opcode);
    assert.equal(await page.locator('.mini-id').innerText(),expected.id);
    assert.match(await button.getAttribute('class'),/cause/);
    const style=await button.evaluate(el=>({background:getComputedStyle(el).background,weight:getComputedStyle(el.querySelector('code')).fontWeight}));
    assert.ok(Number(style.weight)>=700);assert.ok(style.background.includes('rgb(96, 64, 149)'));
    jumps.push({raw,hart,line,tick:await tick(),event:expected.id});
   }
  }
  // Equivalent C and real ASM select exactly the same dynamic instruction.
  const before=await tick();await page.getByRole('tab',{name:'ASM 汇编'}).click();
  await page.locator('.source-code button[data-source-id="thread-3"][data-line="8"]').click();assert.equal(await tick(),before);
  await page.getByRole('tab',{name:'C / 源码'}).click();assert.match(await page.locator('.code-view-bar>span').innerText(),/非 C 编译产物/);
  // Both inspection panes remain visible, with a shared selected flit.
  await page.locator('.timeline-panel').scrollIntoViewIfNeeded();
  const a=await page.locator('.sequence-pane').boundingBox(),b=await page.locator('.events-pane').boundingBox();
  assert.ok(b.x>=a.x+a.width-1);assert.ok(Math.abs(a.y-b.y)<1);
  const eventButton=page.locator('.event-list>button').filter({hasText:'CompData'}).first();await eventButton.click();
  const eventId=await eventButton.getAttribute('data-event-id');
  await page.locator(`.sequence-message[data-event-id="${eventId}"][aria-pressed="true"]`).waitFor();
  assert.equal(await page.locator('.mini-id').innerText(),eventId);
  for(const [container,selected] of [['.event-list','.event-list>[aria-pressed="true"]'],['.sequence-scroll','.sequence-message[aria-pressed="true"]']]){
   const c=await page.locator(container).boundingBox(),r=await page.locator(selected).boundingBox();assert.ok(r.y>=c.y&&r.y+r.height<=c.y+c.height,`${selected} scrolled out of view`);
  }
  mkdirSync('build/qa',{recursive:true});await page.screenshot({path:'build/qa/replay-linked-panes.png'});
  // Inspect the actual instance matrices passed to Three, not an animation flag.
  await page.getByRole('button',{name:'协议展开',exact:true}).click();
  await page.evaluate(async()=>{
   const resource=performance.getEntriesByType('resource').find(e=>e.name.includes('/three.js'));
   if(!resource)throw Error('Three module resource missing');
   const THREE=await import(resource.name),original=THREE.InstancedMesh.prototype.setMatrixAt;
   window.renderedFlits={};window.flitHistory=[];
   THREE.InstancedMesh.prototype.setMatrixAt=function(index,matrix){
    original.call(this,index,matrix);
    const id=this.userData.stepIds?.[index];
    if(id){const entry={id,position:[matrix.elements[12],matrix.elements[13],matrix.elements[14]],time:performance.now()};window.renderedFlits[id]=entry;window.flitHistory.push(entry);if(window.flitHistory.length>5000)window.flitHistory.splice(0,1000);}
   };
  });
  const txs=observed.filter(e=>e.kind==='flit'&&e.observation==='TX'&&e.tick==='7000');
  const ids=txs.map(tx=>protocol.find(p=>p.kind==='flit'&&p.sourceEventIds.includes(tx.id)).id);
  assert.ok(ids.length>=2);await seek(7000);await page.evaluate(()=>document.querySelector('.center-panel').scrollTop=0);
  const matrices=()=>page.evaluate(ids=>Object.fromEntries(ids.map(id=>[id,window.renderedFlits[id]?.position])),ids);
  const atStart=await matrices();assert.ok(ids.every(id=>atStart[id]?.length===3),JSON.stringify(atStart));await page.getByRole('button',{name:'下一步',exact:true}).click();
  await page.waitForTimeout(350);const between=await matrices();
  const moved=ids.filter(id=>JSON.stringify(atStart[id])!==JSON.stringify(between[id]));assert.ok(moved.length>=2,`only ${moved.length} concurrent flits moved`);
  await page.getByRole('button',{name:'播放',exact:true}).waitFor();
  const next=observed.filter(e=>['route','flit'].includes(e.kind)&&BigInt(e.tick)>7000n).map(e=>BigInt(e.tick)).sort((a,b)=>a<b?-1:1)[0];
  assert.equal(await tick(),String(next));const stopped=await matrices();await page.waitForTimeout(250);assert.deepEqual(await matrices(),stopped);
  // A repeated next click completes the current transition instead of freezing it.
  await seek(7000);await page.getByRole('button',{name:'下一步',exact:true}).click();await page.waitForTimeout(100);await page.getByRole('button',{name:'下一步',exact:true}).click();await page.getByRole('button',{name:'播放',exact:true}).waitFor();assert.equal(await tick(),String(next));
  // Measured route anchor: RN3 -> HN0 is at router R21 at tick 11000.
  await seek(11000);const tx=txs.find(e=>e.source==='RN3'&&e.target==='HN0'),id=protocol.find(p=>p.kind==='flit'&&p.sourceEventIds?.includes(tx.id)).id;
  const anchor=(await matrices())[id];assert.ok(Math.abs(anchor[0]-8)<1e-5&&Math.abs(anchor[1]-.75)<1e-5&&Math.abs(anchor[2]-4.5)<1e-5,JSON.stringify(anchor));
  await page.screenshot({path:'build/qa/replay-concurrent-mesh.png'});
  // Physical mode also advances all packets using one time scale; pausing freezes them.
  await page.getByLabel('回放方式').selectOption('physical');await page.getByLabel('播放速度').selectOption('5');await seek(7000);
  const physicalStart=await matrices();await page.getByRole('button',{name:'播放',exact:true}).click();await page.waitForTimeout(350);
  const physicalMid=await matrices();assert.ok(ids.filter(id=>JSON.stringify(physicalStart[id])!==JSON.stringify(physicalMid[id])).length>=2);
  await page.getByRole('button',{name:'暂停',exact:true}).click();await page.waitForTimeout(80);const paused=await matrices();await page.waitForTimeout(250);assert.deepEqual(await matrices(),paused);
  const pauseTick=await tick();await page.getByRole('button',{name:'gem5 原始',exact:true}).click();assert.equal(await tick(),pauseTick);
  // A measured C callsite jumps directly to the explicit test request too.
  await page.getByLabel('选择示例轨迹').selectOption('litmus-protocol');await page.getByRole('heading',{name:'CPU 协议测试 · 19 类 REQ',exact:true}).waitFor();
  const call=page.locator('.source-code button[data-source-id="thread-0"]').filter({hasText:'chi(0x18,7,8,8'});await call.click();
  assert.equal(await page.locator('.message-title h2').innerText(),'WriteUniquePtl');assert.match(await call.getAttribute('class'),/cause-call/);
  const callTick=await tick();assert.ok(BigInt(callTick)>0n);
  assert.deepEqual(errors,[]);
  const report={directCodeJumps:jumps,sourceAsmIdentity:true,linkedPanes:true,concurrentMovingFlits:moved.length,physicalMode:true,measuredRouterAnchor:anchor,stepStopsAt:String(next),pauseFreezesPositions:true,viewSwitchPreservesTick:true,directMeasuredCallsite:true,pageErrors:errors,capturedAt:new Date().toISOString()};
  writeFileSync('build/qa/replay-browser-report.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
