/* Browser acceptance for the explicitly modeled Issue H special cases. */
const {createRequire} = require('node:module');
const {resolve} = require('node:path');
const {readFileSync, writeFileSync, mkdirSync} = require('node:fs');
const assert = require('node:assert/strict');

const runtimeRequire = process.env.CHI_NODE_MODULES
  ? createRequire(resolve(process.env.CHI_NODE_MODULES, '../browser-specials.cjs')) : require;
const {chromium} = runtimeRequire('playwright');
const catalog = JSON.parse(readFileSync('public/demos/catalog.json', 'utf8'));
const demos = catalog.filter(d => d.id.startsWith('teach-special-'));
assert.equal(demos.length, 15, 'generate all fifteen reviewed special cases first');

const topicLabels = {
  ordering: '消息越序与完成握手', retry: '重试与信用', snoop: 'Snoop 与逐出竞争',
  forwarding: '直接转发与后备路径', data: '数据合并与取消', advanced: '高级缓存机制',
};
const fixture = demo => {
  const records = readFileSync(`public/demos/${demo.file}`, 'utf8').trim().split('\n').map(JSON.parse);
  return {manifest: records[0], records, byId: new Map(records.filter(r => r.id).map(r => [r.id, r]))};
};

(async () => {
  const browser = await chromium.launch({channel: process.env.CHI_BROWSER || 'chrome', headless: true});
  const page = await browser.newPage({viewport: {width: 1600, height: 1100}});
  const errors = [], checked = [], checkpoints = [], codeJumps = [], screenshots = [];
  page.on('pageerror', e => errors.push(e.message));
  mkdirSync('build/qa', {recursive: true});
  const tick = () => page.locator('[data-simulation-tick]').getAttribute('data-simulation-tick');
  const setView = async raw => {
    await page.locator('.view-toggle button').nth(raw ? 1 : 0).click();
    await page.locator('.view-toggle button').nth(raw ? 1 : 0).and(page.locator('.active')).waitFor();
  };
  const assertSelection = async (expected, expectedTick, raw) => {
    await page.waitForFunction(id => document.querySelector('.mini-id')?.textContent === id, expected.id);
    assert.equal(await tick(), String(expectedTick), `${expected.id}: wrong simulation time`);
    assert.equal(await page.locator('.message-title h2').innerText(), expected.opcode || expected.kind);
    assert.equal(await page.locator('.view-toggle button.active').count(), 1);
    assert.match(await page.locator('.view-toggle button').nth(raw ? 1 : 0).getAttribute('class'), /active/,
      `${expected.id}: selecting a checkpoint or code must preserve the current view`);
  };
  const openFromMenu = async demo => {
    const group = page.locator('.scenario-menu>div>details').filter({has: page.getByText('③ Hazard 与特殊交错', {exact: false})});
    if (await group.getAttribute('open') === null) await group.locator(':scope > summary').click();
    const topic = page.locator('.scenario-topic').filter({has: page.getByText(demo.title, {exact: true})});
    assert.equal(await topic.count(), 1, `missing or duplicate topic for ${demo.id}`);
    assert.match(await topic.locator(':scope > summary').innerText(), new RegExp(topicLabels[demo.topic]));
    if (await topic.getAttribute('open') === null) await topic.locator(':scope > summary').click();
    const button = topic.locator('button').filter({has: page.getByText(demo.title, {exact: true})});
    await button.click();
    await page.getByRole('heading', {name: demo.title, exact: true}).waitFor();
    assert.equal(await button.getAttribute('aria-current'), 'true');
    assert.equal(await button.locator('small').innerText(), '推演');
  };
  const capture = async name => {
    const path = `build/qa/${name}.png`;
    await page.screenshot({path});
    screenshots.push(path);
  };

  try {
    await page.goto(process.env.CHI_URL || 'http://127.0.0.1:5173/');
    await page.locator('.scene-canvas canvas').waitFor();
    assert.equal(await page.locator('.scenario-menu button').count(), catalog.filter(d => d.group).length);
    assert.equal(await page.locator('.scenario-topic').count(), 7, 'six new topic groups plus existing hazards');

    for (const demo of demos) {
      const {manifest, records, byId} = fixture(demo);
      assert.equal(manifest.source.kind, 'educational_model');
      assert.equal(manifest.teaching.evidence, 'protocol-model');
      assert.equal(manifest.teaching.topic, demo.topic);
      await openFromMenu(demo);
      assert.match(await page.locator('.lesson-heading').innerText(), /协议推演.*非 gem5 实测/);
      assert.match(await page.locator('.code-view-bar').innerText(), /未编译运行.*协议推演/);
      assert.equal(await page.locator('.source-card>b').innerText(), '协议教学推演');
      assert.equal(await page.locator('.coverage-panel').count(), 0);
      assert.equal(await page.locator('.lesson-checkpoints button').count(), manifest.teaching.checkpoints.length);
      const sources = await page.locator('.lesson-sources a').evaluateAll(as => as.map(a => a.getAttribute('href')));
      assert.ok(sources.some(u => /^\/reference\/issue-h\.pdf#page=\d+$/.test(u)), `${demo.id}: manual reference missing`);

      const instructions = records.filter(e => e.type === 'observed' && e.kind === 'instruction');
      for (const raw of [false, true]) {
        await setView(raw);
        // Every operation that issues a local RN request must be reachable from its C line.
        for (const observed of instructions) {
          const ins = observed.instruction;
          const candidates = records.filter(e => e.type === (raw ? 'observed' : 'protocol') && e.kind === 'flit'
            && e.channel === 'REQ' && e.source === `RN${ins.hartId}` && /^HN/.test(e.target)
            && (!raw || e.observation === 'TX') && e.causeInstructionIds?.includes(ins.id));
          const expected = candidates.sort((a, b) => Number(BigInt(a.tick || a.anchorTick) - BigInt(b.tick || b.anchorTick)))[0];
          if (!expected) continue;
          const line = page.locator(`.source-code button[data-source-id="${ins.sourceId}"][data-source-lines="${ins.line}"]`);
          await line.click();
          await assertSelection(expected, expected.tick || expected.anchorTick, raw);
          assert.match(await line.getAttribute('class'), /cause/);
          assert.equal(await page.getByLabel('代码行的动态执行实例').inputValue(), ins.id);
          codeJumps.push({demo: demo.id, raw, instruction: ins.id, event: expected.id});
        }

        for (let i = 0; i < manifest.teaching.checkpoints.length; i++) {
          const checkpoint = manifest.teaching.checkpoints[i];
          const original = byId.get(checkpoint.eventId);
          assert.ok(original, `${demo.id}: unresolved checkpoint ${checkpoint.eventId}`);
          let expected = original;
          if (raw && original.type === 'protocol') {
            expected = original.sourceEventIds.map(id => byId.get(id)).find(e => e?.observation === 'TX');
          } else if (!raw && original.type === 'observed' && original.kind === 'flit') {
            expected = records.find(e => e.type === 'protocol' && e.sourceEventIds?.includes(original.id));
          }
          assert.ok(expected, `${demo.id}: no counterpart for ${checkpoint.eventId}`);
          await page.locator('.lesson-checkpoints button').nth(i).click();
          // RX checkpoints retain their arrival time even in the expanded view.
          await assertSelection(expected, original.tick || original.anchorTick, raw);
          assert.match(await page.locator('.message-title .origin').innerText(), /建模/);
          if (original.causeInstructionIds?.length) {
            assert.ok(await page.locator('.source-code button.cause').count(), `${demo.id}: checkpoint lost source highlighting`);
          }
          checkpoints.push({demo: demo.id, raw, title: checkpoint.title, selected: expected.id, tick: await tick()});
        }
      }
      checked.push({id: demo.id, topic: demo.topic, checkpoints: manifest.teaching.checkpoints.length});

      if (demo.id === 'teach-special-split-early-snoop') {
        await setView(false);
        const checkpoint = manifest.teaching.checkpoints.findIndex(c => /snoop.*数据未齐/.test(c.title));
        await page.locator('.lesson-checkpoints button').nth(checkpoint).click();
        await page.evaluate(() => {document.querySelector('.center-panel').scrollTop = 0;});
        await capture('specials-split-mesh');
      }
      if (demo.id === 'teach-special-copy-at-home') {
        await setView(false);
        await page.locator('.lesson-checkpoints button').nth(3).click();
        await page.locator('.litmus-panel').scrollIntoViewIfNeeded();
        await capture('specials-cah-code');
      }
      if (demo.id === 'teach-special-dct-double') {
        await setView(false);
        const checkpoint = manifest.teaching.checkpoints.findIndex(c => byId.get(c.eventId)?.opcode === 'SnpRespDataFwded');
        if (checkpoint >= 0) await page.locator('.lesson-checkpoints button').nth(checkpoint).click();
        await page.locator('.detail-tabs').getByRole('button', {name: '数据', exact: true}).click();
        assert.equal(await page.locator('.hex-grid>span').count(), 32);
        await page.evaluate(() => {document.querySelector('.center-panel').scrollTop = 0;});
        await capture('specials-dct-data');
        await page.locator('.detail-tabs').getByRole('button', {name: '字段', exact: true}).click();
      }
    }
    assert.deepEqual(errors, []);
    const report = {cases: checked, codeJumps, checkpoints, screenshots, topics: Object.keys(topicLabels),
      bothViews: true, modeledProvenance: true, pageErrors: errors, capturedAt: new Date().toISOString()};
    writeFileSync('build/qa/specials-browser-report.json', JSON.stringify(report, null, 2));
    console.log(JSON.stringify({cases: checked.length, topics: Object.keys(topicLabels).length,
      codeJumps: codeJumps.length, checkpoints: checkpoints.length, bothViews: true,
      modeledProvenance: true, screenshots, pageErrors: errors}, null, 2));
  } catch (error) {
    await page.screenshot({path: 'build/qa/specials-failure.png'}).catch(() => {});
    throw error;
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
