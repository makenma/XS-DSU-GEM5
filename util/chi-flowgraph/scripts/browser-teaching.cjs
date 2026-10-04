const {createRequire}=require('node:module');const {resolve}=require('node:path');
const {readFileSync,writeFileSync,mkdirSync}=require('node:fs');const assert=require('node:assert/strict');
const runtimeRequire=process.env.CHI_NODE_MODULES?createRequire(resolve(process.env.CHI_NODE_MODULES,'../browser-teaching.cjs')):require;
const {chromium}=runtimeRequire('playwright');
const catalog=JSON.parse(readFileSync('public/demos/catalog.json','utf8'));
(async()=>{
 const browser=await chromium.launch({channel:process.env.CHI_BROWSER||'chrome',headless:true});const page=await browser.newPage({viewport:{width:1600,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 const pick=async id=>{const demo=catalog.find(d=>d.id===id);await page.getByLabel('选择示例轨迹').selectOption(id);await page.getByRole('heading',{name:demo.title,exact:true}).waitFor();};
 try{
  await page.goto('http://127.0.0.1:5173/');await page.locator('.scenario-menu').waitFor();
  assert.equal(await page.locator('.scenario-menu>div>details').count(),3);
  assert.equal(await page.locator('.scenario-menu button').count(),catalog.filter(d=>d.group).length);
  const checked=[];
  for(const demo of catalog.filter(d=>d.group==='basics')){
   await pick(demo.id);assert.match(await page.locator('.lesson-heading').innerText(),/真实 CPU 运行节选/);assert.match(await page.locator('.lesson-heading').innerText(),/SLC miss/);
   await page.locator('.source-code .cause-call').first().waitFor();
   const source=page.locator('.source-code .cause-call').first();await source.click();
   assert.equal(await page.locator('.message-title h2').innerText(),demo.id.replace('teach-basic-',''));
   const sourceBox=await source.boundingBox(),codeBox=await source.locator('..').boundingBox();assert.ok(sourceBox.y>=codeBox.y&&sourceBox.y+sourceBox.height<=codeBox.y+codeBox.height,'highlighted C line must be in view');
   checked.push(demo.id);
  }
  for(const bits of ['00','01','10','11']){await pick('teach-hnf-'+bits);assert.match(await page.locator('.lesson-heading>strong').innerText(),new RegExp(`SLC ${bits[0]==='1'?'hit':'miss'} · SF ${bits[1]==='1'?'hit':'miss'}`));}
  // The early-snoop lesson stays on both logical transactions when a checkpoint is selected.
  await pick('teach-hazard-early-unique');assert.match(await page.locator('.lesson-heading').innerText(),/协议推演/);
  assert.match(await page.locator('.code-view-bar').innerText(),/未编译运行/);assert.equal(await page.locator('.coverage-panel').count(),0);
  await page.getByRole('button',{name:'6. 最后到底写什么 ↗',exact:true}).click();
  assert.equal(await page.locator('.message-title h2').innerText(),'CopyBackWriteData');
  assert.equal(await page.locator('.field-row').filter({has:page.getByText('CacheState',{exact:true})}).locator('b').innerText(),'I');
  assert.equal(await page.locator('.field-row').filter({has:page.getByText('BE',{exact:true})}).locator('b').innerText(),'0x00000000');
  await page.getByRole('button',{name:'数据',exact:true}).click();assert.equal(await page.locator('.hex-grid>.disabled-byte').count(),32);assert.ok((await page.locator('.hex-grid').innerText()).replace(/\s/g,'').match(/^0+$/));
  await page.locator('.timeline-panel').scrollIntoViewIfNeeded();assert.ok(await page.getByRole('button',{name:'ReadUnique RN0 到 HN0',exact:true}).count());assert.ok(await page.getByRole('button',{name:'WriteBackFull RN1 到 HN0',exact:true}).count());
  mkdirSync('build/qa',{recursive:true});await page.screenshot({path:'build/qa/teaching-early-snoop.png'});
  await page.getByRole('button',{name:'1. 两核同时发出 ↗',exact:true}).click();await page.locator('[data-flit-count="2"]').waitFor();
  const initial=await page.locator('[data-simulation-tick]').getAttribute('data-simulation-tick');assert.equal(initial,'1000');
  await page.getByRole('button',{name:'下一步',exact:true}).click();await page.getByRole('button',{name:'播放',exact:true}).waitFor();assert.ok(BigInt(await page.locator('[data-simulation-tick]').getAttribute('data-simulation-tick'))>1000n);
  await page.evaluate(()=>document.querySelector('.center-panel').scrollTop=0);await page.screenshot({path:'build/qa/teaching-mesh.png'});
  for(const id of ['teach-hazard-retry','teach-hazard-same-address','teach-hazard-stash-refuse']){await pick(id);await page.locator('.lesson-panel').waitFor();assert.equal(await page.locator('.source-card>b').innerText(),'gem5 运行采集');}
  const links=await page.locator('.lesson-sources a').evaluateAll(as=>as.map(a=>a.href));assert.ok(links.some(u=>u.includes('/reference/issue-h.pdf#page=283')));
  assert.deepEqual(errors,[]);const report={basicOpcodeCases:checked.length,hnfCombinations:4,menuCases:catalog.filter(d=>d.group).length,earlyUniqueZeroByteCopyback:true,parallelStep:true,actualVsModeledLabels:true,pageErrors:errors,capturedAt:new Date().toISOString()};writeFileSync('build/qa/teaching-browser-report.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
