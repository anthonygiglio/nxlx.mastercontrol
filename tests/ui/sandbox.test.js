// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// Smoke test of the design sandbox (tools/sandbox): the switcher's page, and the real panel in each role at a phone's
// and a laptop's width, each with no script error and no refusal of the page's policy.
// Run: node tests/ui/sandbox.test.js   (needs playwright and a Chromium, as tests/ui/panel.test.js)
const { chromium } = require('playwright');
const { spawn } = require('child_process');
const path = require('path');
const assert = require('assert');

const ROOT = path.join(__dirname, '..', '..');
const PORT = 20000 + Math.floor(Math.random() * 30000);

function startSandbox() {
  return new Promise((resolve, reject) => {
    const proc = spawn('python3', [path.join(ROOT, 'tools', 'sandbox', 'serve.py'), '--port', String(PORT), '--scenario', 'playing'],
      { cwd: ROOT, stdio: ['ignore', 'pipe', 'inherit'] });
    let out = '';
    const timer = setTimeout(() => reject(new Error('the sandbox did not start in 180 s: ' + out)), 180000);
    proc.stdout.on('data', (d) => {
      out += d;
      if (out.includes('Stop with Ctrl+C.')) { clearTimeout(timer); resolve(proc); }
    });
    proc.on('exit', (code) => { clearTimeout(timer); reject(new Error('the sandbox ended (' + code + '): ' + out)); });
  });
}

// What a page must not do: throw, or have the policy refuse something of it.
function watch(page, problems, where) {
  page.on('pageerror', (e) => problems.push(where + ': ' + e.message));
  page.on('console', (m) => { if (m.type() === 'error' && /Content Security Policy|Refused to/.test(m.text())) problems.push(where + ': ' + m.text()); });
}

(async () => {
  const sandbox = await startSandbox();
  const browser = await chromium.launch();
  const problems = [];
  try {
    // The switcher: its own page, its scenarios, and an address that loads the chosen one.
    const sw = await browser.newPage();
    watch(sw, problems, 'switcher');
    await sw.goto('http://127.0.0.1:' + PORT + '/');
    await sw.waitForFunction(() => document.getElementById('status').textContent.indexOf('Running') === 0, null, { timeout: 30000 });
    const options = await sw.$$eval('#scenario option', (os) => os.map((o) => o.value));
    assert.ok(options.includes('playing') && options.includes('empty'), 'scenarios listed: ' + options);
    assert.ok((await sw.textContent('#link')).endsWith('/open?scenario=playing&role=owner'));
    await sw.click('#roles button:nth-child(3)');
    assert.ok((await sw.textContent('#link')).endsWith('&role=guest'));

    // The panel, each role at two widths. What each must show is the panel's own: the area tabs or the rail, and
    // the pairing screen for a device that is not paired. A Guest and an Operator land on Room (the Room module is on).
    const want = {
      owner: async (p) => p.waitForSelector('#padsscreen', { timeout: 20000 }),
      operator: async (p) => p.waitForSelector('#roomscreen, [data-area="room"] .deskcol', { timeout: 20000 }),
      guest: async (p) => p.waitForSelector('#guestcard', { timeout: 20000 }),
      nobody: async (p) => p.waitForSelector('#pairbtn', { timeout: 20000 }),
    };
    for (const [w, h] of [[390, 844], [1440, 900]]) {
      for (const role of Object.keys(want)) {
        const ctx = await browser.newContext({ viewport: { width: w, height: h } });
        const page = await ctx.newPage();
        watch(page, problems, role + ' at ' + w);
        await page.goto('http://127.0.0.1:' + PORT + '/open?scenario=playing&role=' + role);
        await want[role](page);
        if (role !== 'nobody') {
          const nav = await page.evaluate(() => [!!document.getElementById('wstabs') && getComputedStyle(document.getElementById('wstabs')).display !== 'none',
            !!document.getElementById('wsside') && getComputedStyle(document.getElementById('wsside')).display !== 'none']);
          assert.deepStrictEqual(nav, w < 600 ? [true, false] : [false, true], role + ' at ' + w + ': the area tabs on a phone, the rail from 600 px');
          const reach = await page.evaluate(() => fetch('/api/status').then((r) => r.json()).then((d) => d.reach));
          assert.strictEqual(reach, role, 'the port of ' + role + ' is ' + reach);
          // the switcher is never part of the panel's page
          assert.strictEqual(await page.$('#scenario'), null);
        }
        await ctx.close();
      }
    }
    assert.deepStrictEqual(problems, []);
    console.log('sandbox: ok (the switcher, and 4 roles at 390 and 1440)');
  } finally {
    await browser.close();
    sandbox.kill('SIGINT');
  }
})().catch((e) => { console.error('FAILED: ' + (e && e.stack || e)); process.exit(1); });
