const {createRequire}=require('node:module');
const {resolve}=require('node:path');
const {mkdirSync,writeFileSync,createReadStream}=require('node:fs');
const {createInterface}=require('node:readline');
const assert=require('node:assert/strict');
const runtimeRequire=process.env.CHI_NODE_MODULES?createRequire(resolve(process.env.CHI_NODE_MODULES,'../browser-native.cjs')):require;
const {chromium}=runtimeRequire('playwright');
(async()=>{
  // Event lists also contain allocate/SAM/router rows with an opcode. Select an
  // actual imported protocol REQ by its stable identity, not its display text.
  const input=resolve(process.env.CHI_NATIVE_TRACE||'examples/native-l2/run.chi.jsonl');
  const lines=createInterface({input:createReadStream(input),crlfDelay:Infinity});
  let request;
  for await(const line of lines) {
    const record=JSON.parse(line);
    if(record.type==='protocol'&&record.kind==='flit'&&record.channel==='REQ'&&record.opcode==='ReadNotSharedDirty') {
      request=record;break;
    }
  }
  assert.ok(request,'Native trace has no ReadNotSharedDirty protocol REQ');
  assert.equal(request.fields.Size.value,6);
  const browser=await chromium.launch({channel:process.env.CHI_BROWSER||'chrome',headless:true});
  const page=await browser.newPage({viewport:{width:1600,height:1000}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  page.setDefaultTimeout(90000);
  try {
    await page.goto('http://127.0.0.1:5173/');
    await page.locator('input[type=file]').setInputFiles(input);
    await page.getByRole('heading',{name:'KMH CPU · 原生 CHI L2',exact:true}).waitFor();
    await page.locator('.scene-canvas canvas').waitFor();
    assert.equal(await page.locator('.coverage-panel').count(),0,'Trace without instruction records must not display CPU opcode coverage as zero');
    await page.getByLabel('按事务身份筛选').fill(request.rootId);
    const selectRequest=async()=>{
      // Tick seek follows concurrent traffic and can focus another root.
      await page.locator('.transaction-item').filter({has:page.getByText(request.rootId,{exact:true})}).click();
      await page.locator(`.event-list>button[data-event-id="${request.id}"]`).click();
    };
    await selectRequest();
    assert.equal(await page.locator('.mini-id').innerText(),request.id);
    await page.getByRole('button',{name:'字段',exact:true}).click();
    const size=page.locator('.field-row').filter({has:page.getByText('Size',{exact:true})});
    assert.equal(await size.locator('b').innerText(),'6');
    assert.match(await size.innerText(),/实测/);
    const readTick=()=>page.locator('[data-simulation-tick]').getAttribute('data-simulation-tick');
    const startTick=await readTick();
    await page.getByRole('button',{name:'下一步',exact:true}).click();
    await page.waitForFunction(tick=>BigInt(document.querySelector('[data-simulation-tick]').dataset.simulationTick)>BigInt(tick),startTick);
    await page.getByRole('button',{name:'播放',exact:true}).waitFor();
    const forwardTick=await readTick();
    await page.getByRole('button',{name:'上一步',exact:true}).click();
    assert.equal(await readTick(),startTick);
    const seekTick=String(BigInt(request.anchorTick)+1000n);
    await page.getByLabel('定位实际 tick').fill(seekTick);
    await page.getByRole('button',{name:'定位',exact:true}).click();
    assert.equal(await readTick(),seekTick);
    await selectRequest();
    await page.getByRole('button',{name:'gem5 原始',exact:true}).click();
    assert.equal(await page.locator('.mini-id').innerText(),request.sourceEventIds[0]);
    assert.equal(await page.locator('.field-row').filter({has:page.getByText('size',{exact:true})}).locator('b').innerText(),'6');
    await page.getByRole('button',{name:'协议展开',exact:true}).click();
    assert.equal(await page.locator('.mini-id').innerText(),request.id);
    assert.equal(await size.locator('b').innerText(),'6');
    mkdirSync('build/qa',{recursive:true});
    await page.screenshot({path:'build/qa/native-l2-browser.png',fullPage:true});
    assert.deepEqual(errors,[]);
    const report={profile:'h-native-256',imported:true,observedRequestSize:6,
      requestId:request.id,sourceEventId:request.sourceEventIds[0],startTick,forwardTick,seekTick,
      forwardAndBackward:true,tickSeek:true,rawAndProtocolViews:true,unknownCpuCoverageHidden:true,pageErrors:errors,passed:true};
    writeFileSync('build/qa/native-l2-browser.json',JSON.stringify(report,null,2)+'\n');
    console.log(JSON.stringify(report,null,2));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
