const {createRequire}=require('node:module');
const {resolve}=require('node:path');
const {mkdirSync,writeFileSync}=require('node:fs');
const assert=require('node:assert/strict');
const runtimeRequire=process.env.CHI_NODE_MODULES?createRequire(resolve(process.env.CHI_NODE_MODULES,'../browser-litmus.cjs')):require;
const {chromium}=runtimeRequire('playwright');
(async()=>{
 const browser=await chromium.launch({channel:process.env.CHI_BROWSER||'chrome',headless:true});
 const page=await browser.newPage({viewport:{width:1600,height:1000}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 try {
  await page.goto('http://127.0.0.1:5173/');
  await page.getByRole('heading',{name:'四核 IRIW · 3×2 mesh',exact:true}).waitFor();
  await page.locator('.scene-canvas canvas').waitFor();
  assert.equal(await page.locator('.thread-grid article').count(),4);
  const canvas=await page.locator('.scene-canvas canvas').boundingBox();assert.ok(canvas.height>=360);
  await page.locator('.coverage-panel button').filter({hasText:'ReadNotSharedDirty'}).click();
  await page.locator('.source-code .cause').first().waitFor();
  await page.locator('.source-code .cause').first().click();
  const instance=page.getByLabel('代码行的动态执行实例');
  const options=await instance.locator('option').evaluateAll(rows=>rows.map(r=>r.value).filter(Boolean));assert.ok(options.length);
  await instance.selectOption(options[0]);
  await page.getByRole('button',{name:'协议展开',exact:true}).click();
  await page.locator('.source-code .cause').first().waitFor();
  await page.getByRole('button',{name:'gem5 原始',exact:true}).click();
  await page.locator('.source-code .cause').first().waitFor();
  mkdirSync('build/qa',{recursive:true});await page.screenshot({path:'build/qa/litmus-iriw.png',fullPage:true});
  await page.getByLabel('选择示例轨迹').selectOption('litmus-protocol');
  await page.getByRole('heading',{name:'CPU 协议测试 · 19 类 REQ',exact:true}).waitFor();
  assert.equal(await page.locator('.coverage-panel button.covered').count(),19);
  for(const opcode of ['WriteUniquePtl','WriteBackFull','StashOnceShared','StashOnceUnique','ReadOnceCleanInvalid','ReadOnceMakeInvalid']) {
   await page.locator('.coverage-panel button').filter({hasText:opcode}).click();
   await page.locator('.source-code .cause').first().waitFor();
   await page.locator('.source-code .cause-call').first().waitFor();
   assert.match(await page.locator('.source-code .cause-call').first().innerText(),/chi\(/);
   assert.match(await page.locator('.instruction-instances').innerText(),/动态指令|动态执行/);
  }
  const selectedCall=await page.locator('.source-code .cause-call').first().innerText();
  await page.getByRole('button',{name:'协议展开',exact:true}).click();
  await page.locator('.source-code .cause-call').first().waitFor();
  assert.equal(await page.locator('.source-code .cause-call').first().innerText(),selectedCall);
  assert.match(await page.locator('.message-description').innerText(),/h-extended-256/);
  await page.screenshot({path:'build/qa/litmus-protocol.png',fullPage:true});
  for(const node of ['RN0','HN3']) {
   await page.getByLabel('查看节点状态').selectOption(node);
   const calculation=page.locator('.sam-calculation');await calculation.waitFor();
   const values=(await calculation.locator('p').innerText()).match(/计算索引 (\d+) · 采集索引 (\d+)/);
   assert.ok(values);assert.equal(values[1],values[2]);
  }
  const rotationTitles=await page.getByLabel('选择示例轨迹').locator('option').evaluateAll(rows=>rows.filter(r=>r.value.includes('rotation')).map(r=>r.textContent));
  assert.equal(rotationTitles.length,3);assert.equal(new Set(rotationTitles).size,3);
  await page.locator('.litmus-heading').click();assert.equal(await page.locator('.thread-grid').count(),0);
  await page.locator('.litmus-heading').click();assert.equal(await page.locator('.thread-grid article').count(),4);
  await page.getByLabel('选择示例轨迹').selectOption('litmus-stress');
  await page.getByRole('heading',{name:'四核重试与 dirty victim · 3×2 mesh',exact:true}).waitFor();
  assert.match(await page.locator('.litmus-context').innerText(),/128/);
  assert.match(await page.locator('.litmus-context').innerText(),/300/);
  assert.deepEqual(errors,[]);
  const report={fourThreads:true,meshCanvas:canvas,coverage:'19/19',codeJump:true,measuredCallSiteHighlight:true,rawRefinedHighlight:true,twoStageSAM:true,addressRotationSessions:3,stressSession:true,pageErrors:errors,capturedAt:new Date().toISOString()};
  writeFileSync('build/qa/litmus-browser-report.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
