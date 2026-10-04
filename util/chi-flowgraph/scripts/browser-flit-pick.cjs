const {createRequire}=require('node:module');
const {resolve}=require('node:path');
const {readFileSync,writeFileSync,mkdirSync}=require('node:fs');
const assert=require('node:assert/strict');
const runtimeRequire=process.env.CHI_NODE_MODULES?createRequire(resolve(process.env.CHI_NODE_MODULES,'../browser-flit-pick.cjs')):require;
const {chromium}=runtimeRequire('playwright');
const records=readFileSync('public/demos/litmus-iriw.chi.jsonl','utf8').trim().split('\n').map(JSON.parse);
const tx=records.find(e=>e.type==='observed'&&e.kind==='flit'&&e.observation==='TX'&&e.tick==='7000'&&e.source==='RN3'&&e.target==='HN0');
const expanded=records.find(e=>e.type==='protocol'&&e.kind==='flit'&&e.sourceEventIds?.includes(tx.id));
const distance=(a,b)=>Math.hypot(...a.map((value,index)=>value-b[index]));
(async()=>{
 const browser=await chromium.launch({channel:process.env.CHI_BROWSER||'chrome',headless:true});
 const page=await browser.newPage({viewport:{width:1600,height:1000}}),errors=[],checks=[];
 page.on('pageerror',e=>errors.push(e.message));
 const tick=()=>page.locator('[data-simulation-tick]').getAttribute('data-simulation-tick');
 const seek=async value=>{await page.getByLabel('定位实际 tick').fill(String(value));await page.getByRole('button',{name:'定位',exact:true}).click();await page.waitForTimeout(100);};
 const snapshot=id=>page.evaluate(id=>window.flitPickSnapshot(id),id);
 try{
  await page.goto('http://127.0.0.1:5173/');await page.locator('.scene-canvas canvas').waitFor();
  // Capture the actual scene/camera passed through Three's rendering path.
  // This only instruments the browser test; no application testing hooks exist.
  await page.evaluate(async()=>{
   const resource=performance.getEntriesByType('resource').find(e=>e.name.includes('/three.js'));
   if(!resource)throw Error('Three module resource missing');
   const THREE=await import(resource.name);
   const updateScene=THREE.Scene.prototype.updateMatrixWorld,updateCamera=THREE.PerspectiveCamera.prototype.updateMatrixWorld;
   let scene,camera;
   THREE.Scene.prototype.updateMatrixWorld=function(...args){scene=this;return updateScene.apply(this,args);};
   THREE.PerspectiveCamera.prototype.updateMatrixWorld=function(...args){camera=this;return updateCamera.apply(this,args);};
   window.flitPickSnapshot=id=>{
    if(!scene||!camera)return null;
    const canvas=document.querySelector('.scene-canvas canvas'),rect=canvas.getBoundingClientRect(),matrix=new THREE.Matrix4();
    let packet;
    scene.traverse(object=>{
     if(!object.isInstancedMesh||!object.userData.channel)return;
     const index=object.userData.stepIds.indexOf(id);if(index<0||index>=object.count)return;
     object.getMatrixAt(index,matrix);const position=new THREE.Vector3().setFromMatrixPosition(matrix),projected=position.clone().project(camera);
     packet={id,position:position.toArray(),x:rect.left+(projected.x+1)*rect.width/2,y:rect.top+(1-projected.y)*rect.height/2,scale:new THREE.Vector3().setFromMatrixScale(matrix).toArray()};
    });
    const halo=scene.getObjectByName('selected-flit-halo');
    return {packet,halo:halo?{visible:halo.visible,id:halo.userData.stepId,position:halo.position.toArray(),rings:halo.children.map(mesh=>({color:mesh.material.color.getHexString(),depthTest:mesh.material.depthTest,renderOrder:mesh.renderOrder}))}:null,rect:{x:rect.x,y:rect.y,width:rect.width,height:rect.height}};
   };
  });
  await page.getByLabel('回放方式').selectOption('physical');await page.getByLabel('播放速度').selectOption('5');
  for(const raw of [false,true]){
   const expected=raw?tx:expanded;
   await page.getByRole('button',{name:raw?'gem5 原始':'协议展开',exact:true}).click();await seek(12000);
   await page.getByRole('button',{name:'数据',exact:true}).click();
   assert.match(await page.locator('.detail-tabs button.active').innerText(),/数据/);
   const start=await snapshot(expected.id);assert.ok(start?.packet,`${expected.id} is not in flight`);
   await page.getByRole('button',{name:'播放',exact:true}).click();await page.waitForTimeout(150);
   const flying=await snapshot(expected.id);assert.ok(distance(start.packet.position,flying.packet.position)>.05,'packet did not move before clicking');
   await page.mouse.move(flying.packet.x,flying.packet.y);
   const pressed=await snapshot(expected.id);
   // Holding down deliberately lets the flit move before pointerup: selection
   // must retain the down-time identity and location, not a reused instance slot.
   await page.mouse.down();await page.waitForTimeout(150);await page.mouse.up();
   await page.getByRole('button',{name:'播放',exact:true}).waitFor();await page.waitForTimeout(100);
   assert.equal(await page.locator('.mini-id').innerText(),expected.id);
   assert.equal(await page.locator('.message-title h2').innerText(),expected.opcode);
   assert.match(await page.locator('.detail-tabs button.active').innerText(),/字段/);
   assert.ok(await page.locator('.field-row').count()>0,'selected packet has no fields');
   const selectedTick=await tick();assert.ok(BigInt(selectedTick)>12000n,`click rewound to ${selectedTick}`);assert.ok(BigInt(selectedTick)<20000n);
   const selected=await snapshot(expected.id);assert.ok(selected.packet&&selected.halo?.visible,'selected in-flight packet lacks halo');
   assert.equal(selected.halo.id,expected.id);assert.ok(distance(selected.packet.position,selected.halo.position)<1e-5);
   assert.equal(selected.halo.rings.length,2);assert.equal(selected.halo.rings[0].color,'ffffff');assert.ok(selected.halo.rings.every(ring=>!ring.depthTest&&ring.renderOrder>10));
   assert.ok(distance(selected.packet.position,pressed.packet.position)<.55,'click moved packet away from displayed press position');
   assert.ok(selected.packet.scale.every(scale=>Math.abs(scale-2)<1e-5));
   await page.locator('.scene-selection').filter({hasText:expected.opcode}).waitFor();
   await page.waitForTimeout(200);assert.equal(await tick(),selectedTick);assert.deepEqual((await snapshot(expected.id)).packet.position,selected.packet.position);
   mkdirSync('build/qa',{recursive:true});await page.screenshot({path:`build/qa/flit-picked-${raw?'raw':'expanded'}.png`});
   // Dragging from a packet rotates the view without selecting or pausing it.
   await page.getByRole('button',{name:'数据',exact:true}).click();await page.getByRole('button',{name:'播放',exact:true}).click();
   const drag=await snapshot(expected.id);await page.mouse.move(drag.packet.x,drag.packet.y);await page.mouse.down();await page.mouse.move(drag.packet.x+45,drag.packet.y+20,{steps:8});await page.mouse.up();
   assert.ok(await page.getByRole('button',{name:'暂停',exact:true}).isVisible(),'drag unexpectedly paused playback');
   assert.match(await page.locator('.detail-tabs button.active').innerText(),/数据/);
   await page.waitForTimeout(150);const continued=await tick();assert.ok(BigInt(continued)>BigInt(selectedTick),'resume did not advance the same clock');
   await page.getByRole('button',{name:'暂停',exact:true}).click();await page.getByRole('button',{name:'复位',exact:true}).click();await page.waitForTimeout(250);
   checks.push({raw,event:expected.id,opcode:expected.opcode,txTick:tx.tick,selectedTick,resumedTick:continued,fieldsVisible:true,whiteDoubleHalo:true,pausedAtPressedPosition:true,dragPreservesPlayback:true});
  }
  assert.deepEqual(errors,[]);
  const report={checks,pageErrors:errors,capturedAt:new Date().toISOString()};writeFileSync('build/qa/flit-pick-browser-report.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
