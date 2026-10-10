// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// Browser test: drives the real panel against tests/ui/harness.py.
// Run: node tests/ui/panel.test.js   (needs playwright and a Chromium)
const { chromium } = require('playwright');
const { spawn } = require('child_process');
const path = require('path');
const assert = require('assert');
const net = require('net');
const crypto = require('crypto');

// One PJLink question to the harness's fake projector, straight over TCP (not through the panel): what it really did.
function pjlink(port, password, body) {
  return new Promise((resolve, reject) => {
    const sock = net.connect(port, '127.0.0.1');
    let buf = '', sent = false;
    sock.setTimeout(5000, () => { sock.destroy(); reject(new Error('fake projector: no answer')); });
    sock.on('error', reject);
    sock.on('data', (d) => {
      buf += d;
      if (!buf.includes('\r')) return;
      const line = buf.split('\r')[0];
      buf = buf.slice(line.length + 1);
      if (sent) { sock.destroy(); return resolve(line.split('=')[1]); }
      const m = /^PJLINK 1 ([0-9a-f]{8})$/.exec(line);
      sent = true;
      sock.write((m ? crypto.createHash('md5').update(m[1] + password).digest('hex') : '') + '%1' + body + '\r');
    });
  });
}

const shots = process.env.SHOTS || '';
const pyBin = process.env.PYTHON || 'python3';

function startServer() {
  return new Promise((resolve, reject) => {
    const p = spawn(pyBin, [path.join(__dirname, 'harness.py')], { cwd: path.join(__dirname, '..', '..'), stdio: ['ignore', 'pipe', 'inherit'] });
    let buf = '';
    p.stdout.on('data', (d) => { buf += d; const line = buf.split('\n')[0]; if (buf.includes('\n')) resolve({ p, info: JSON.parse(line) }); });
    p.on('exit', (c) => reject(new Error('harness exited ' + c)));
  });
}

(async () => {
  const { p: server, info } = await startServer();
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM || undefined });
  let failed = false;
  // For a failure that comes and goes: every page the test opens is kept, with the box's last answers to it, and
  // when a step fails report() prints what each open page showed at that moment (the screen, the message line, the
  // field in use, the cards the failed step named) and those answers. Nothing here changes what the test does.
  const opened = [];
  const noteAnswer = (who) => (res) => {
    const req = res.request(), url = res.url(), at = url.indexOf('/api/');
    if (at < 0) return;
    const entry = { t: Date.now(), line: req.method() + ' ' + url.slice(at) + ' ' + res.status() };
    const quiet = req.method() === 'GET' && res.status() < 400 && /\/api\/status$/.test(url);     // asked every second
    if (quiet) { who.polls = (who.polls || 0) + 1; return; }
    if (req.method() !== 'GET') entry.line += ' sent ' + String(req.postData() || '').slice(0, 300);
    who.answers.push(entry);
    if (who.answers.length > 30) who.answers.shift();
    if (res.status() >= 400) res.text().then((t) => { entry.line += ' answered ' + t.slice(0, 300); }, () => {});
  };
  const newContext = browser.newContext.bind(browser);
  browser.newContext = async (options) => {
    const c = await newContext(options);
    const who = { n: opened.length + 1, answers: [], page: null };
    opened.push(who);
    c.on('page', (pg) => { who.page = who.page || pg; });
    c.on('response', noteAnswer(who));
    return c;
  };
  const within = (promise, ms) => Promise.race([promise, new Promise((resolve) => setTimeout(() => resolve(null), ms))]);
  async function report(error) {
    const ids = Array.from(new Set((String(error && error.message).match(/#[A-Za-z][\w-]*/g) || []).map((x) => x.slice(1)))).slice(0, 8);
    const now = Date.now();
    for (const who of opened) {
      const pg = who.page;
      if (!pg || pg.isClosed()) continue;
      let seen = null;
      try {
        seen = await within(pg.evaluate((names) => {
          const cut = (t, n) => { t = String(t || '').replace(/\s+/g, ' ').trim(); return t.length > n ? t.slice(0, n) + ' [...]' : t; };
          const text = (el) => (el ? cut(el.innerText || el.textContent, 1500) : null);
          const one = (q) => document.querySelector(q);
          const screen = one('.shell > .screen') || one('#app > *');
          const active = document.activeElement;
          const tab = one('nav.tabs [aria-current="page"]');
          return {
            screen: screen ? (screen.id || screen.className) + (screen.dataset && screen.dataset.page ? ' (' + screen.dataset.page + ')' : '') : 'none',
            tab: tab ? tab.textContent : null,
            title: text(one('h1')),
            msg: one('#msg') ? one('#msg').className + ': ' + cut(one('#msg').textContent, 300) : null,
            asking: text(one('#confirmrow')),
            offline: Array.from(document.querySelectorAll('.offline, #offline')).some((el) => !el.hidden),
            focus: active && active !== document.body ? (active.id ? '#' + active.id : active.tagName.toLowerCase()) + ('value' in active ? ' = ' + JSON.stringify(cut(active.value, 80)) : '') : 'nothing',
            named: names.map((id) => {
              const el = document.getElementById(id);
              if (!el) return '#' + id + ': not in the page';
              const box = el.getBoundingClientRect(), card = el.closest('.card');
              return '#' + id + ': ' + (box.width && box.height ? 'shown' : 'hidden or empty') + (el.disabled ? ', disabled' : '') +
                ('value' in el && el.tagName !== 'BUTTON' ? ', value ' + JSON.stringify(cut(el.value, 80)) : '') + ', text ' + JSON.stringify(cut(el.innerText || el.textContent, 300)) +
                (card && card !== el ? ' | its card' + (card.id ? ' #' + card.id : '') + ': ' + JSON.stringify(cut(card.innerText, 900)) : '');
            }),
            all: text(screen),
          };
        }, ids), 5000);
      } catch (e) { seen = null; }
      console.error('--- page ' + who.n + (who.n === 1 ? ' (the full-access phone)' : '') + ' at ' + pg.url().replace(/#.*/, '#...') + ' ---');
      if (!seen) console.error('  the page did not answer');
      else {
        console.error('  screen: ' + seen.screen + ' | tab: ' + seen.tab + ' | title: ' + seen.title + ' | focus: ' + seen.focus + (seen.offline ? ' | OFFLINE banner shown' : ''));
        console.error('  message line: ' + seen.msg + (seen.asking ? ' | a question is open: ' + seen.asking : ''));
        seen.named.forEach((line) => console.error('  ' + line));
        console.error('  the whole screen: ' + seen.all);
      }
      console.error('  the last answers of the box (seconds before the failure; ' + (who.polls || 0) + ' status polls left out):');
      who.answers.slice(-25).forEach((a) => console.error('    -' + ((now - a.t) / 1000).toFixed(2) + ' ' + a.line));
    }
  }
  try {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
    const page = await ctx.newPage();
    const problems = [];
    // The 401 before pairing, the 403 for the wrong PIN, the 400 for a refused upload, the 409 for a value sent to a
    // shader that has left the screen and the 503 for a screen preview from a harness player with no window are
    // provoked on purpose; so is the 502 for "Try again" on a projector that nothing answers for.
    // And the 422 for a theme file the box refuses (its text cannot be read, or it holds CSS).
    const expected = /status of (400|401|403|409|422|502|503)/;
    page.on('console', (m) => { if (['error', 'warning'].includes(m.type()) && !expected.test(m.text())) problems.push(m.text()); });
    page.on('pageerror', (e) => problems.push('pageerror: ' + e.message));
    const fontRequests = [];             // the default look must never ask for a font file; Signal asks the box itself
    page.on('request', (r) => { if (/\.woff2?(\?|$)/.test(r.url())) fontRequests.push(r.url()); });
    const base = 'http://127.0.0.1:' + info.port;
    await page.goto(base + '/');

    // Connect screen
    await page.waitForSelector('.pin');
    if (shots) await page.screenshot({ path: path.join(shots, '1-connect.png') });
    // wrong PIN first
    const wrong = info.pin === '0000' ? '1111' : '0000';
    for (let i = 0; i < 4; i++) await page.fill(`input[aria-label="PIN digit ${i + 1}"]`, wrong[i]);
    await page.click('#pairbtn');
    await page.waitForFunction(() => /Wrong PIN/.test(document.getElementById('msg').textContent));
    // right PIN
    for (let i = 0; i < 4; i++) await page.fill(`input[aria-label="PIN digit ${i + 1}"]`, info.pin[i]);
    await page.click('#pairbtn');
    await page.waitForSelector('.pads');

    // Live: assign a pad, then play it
    await page.click('text=Edit pads');
    await page.click('.pad >> nth=0');
    await page.click('.sheet >> text=intro.mkv');
    await page.click('text=Done editing');
    await page.waitForSelector('.pad:not(.empty)');
    await page.click('.pad >> nth=0');
    await page.waitForFunction(() => /intro/.test(document.getElementById('np').textContent), null, { timeout: 8000 });
    await page.waitForSelector('.pad.on');
    if (shots) await page.screenshot({ path: path.join(shots, '2-live.png') });
    // Screen snapshot: one picture on request. The harness player has no real window (--vo=null), so no picture can be
    // made here; the panel must say so instead of showing a broken image, and must not keep asking by itself.
    let previewRequests = 0;
    page.on('request', (r) => { if (r.url().includes('/api/preview.jpg')) previewRequests++; });
    await page.click('#previewbtn');
    await page.waitForFunction(() => { const m = document.getElementById('previewmsg'); return m && !m.hidden && /No picture/.test(m.textContent); }, null, { timeout: 8000 });
    await page.waitForTimeout(1500);
    assert.strictEqual(previewRequests, 1, 'a snapshot is taken once per tap, never repeated by itself (' + previewRequests + ' requests)');
    await page.click('#black');
    await page.waitForFunction(() => document.getElementById('black').textContent === 'Show');
    await page.click('#black');
    await page.waitForFunction(() => document.getElementById('black').textContent === 'Blackout');
    // Transport: the position slider is there, and the test pattern switches on and off
    await page.waitForSelector('#seek');
    await page.click('#testpattern');
    await page.waitForFunction(() => /Test pattern off/.test(document.getElementById('testpattern').textContent), null, { timeout: 8000 });
    await page.click('#testpattern');
    await page.waitForFunction(() => document.getElementById('testpattern').textContent === 'Test pattern', null, { timeout: 8000 });
    // Stop ends the clip and leaves the player running (a view-only guest gets a disabled button, checked below)
    await page.click('#stop');
    await page.waitForFunction(() => /Player idle/.test(document.getElementById('np').textContent), null, { timeout: 8000 });

    // A snapshot with nothing playing is not an error: the Screen card says so in plain words. (An earlier answer
    // is remembered by the box for a few seconds, so this asks until the box has looked again.)
    for (let tries = 0; ; tries++) {
      await page.waitForFunction(() => !document.getElementById('previewbtn').disabled);
      await page.click('#previewbtn');
      try {
        await page.waitForFunction(() => { const m = document.getElementById('previewmsg'); return m && !m.hidden && m.textContent === 'Nothing is on the screen right now.'; }, null, { timeout: 2500 });
        break;
      } catch (e) { if (tries >= 4) throw new Error('an idle snapshot does not say that nothing is on the screen: ' + await page.textContent('#previewmsg')); }
    }
    assert(!(await page.isVisible('#preview')), 'no broken picture for an idle snapshot');
    // Pad endings: the pad editor offers loop, once, and hold
    await page.click('text=Edit pads');
    await page.click('.pad >> nth=1');
    await page.waitForSelector('#padending');
    assert.deepStrictEqual(await page.$$eval('#padending option', (os) => os.map((o) => o.value)), ['loop', 'stop', 'hold']);
    await page.selectOption('#padending', 'hold');
    await page.click('.sheet >> text=tunnel.mkv');
    await page.click('text=Done editing');
    // Media: upload a file, see it listed, rename it, delete it
    await page.click('nav >> text=Media');
    await page.waitForSelector('#uploadbtn');
    await page.setInputFiles('#filepick', { name: 'from-phone.mp4', mimeType: 'video/mp4', buffer: Buffer.alloc(300000, 7) });
    await page.waitForSelector('.item:has-text("from-phone.mp4") >> text=Rename', { timeout: 8000 });
    // No browser dialog anywhere in the panel: one would be a problem at the end of the test
    page.on('dialog', (d) => { problems.push('a browser dialog opened: ' + d.message()); d.dismiss(); });
    await page.click('.item:has-text("from-phone.mp4") >> text=Rename');
    await page.fill('.renamebox input', 'renamed-on-phone.mp4');
    await page.click('.renamebox button:text-is("Rename")');
    await page.waitForSelector('.item:has-text("renamed-on-phone.mp4")');
    // Delete asks first, in place, naming the file; "Keep it" changes nothing
    await page.click('.item:has-text("renamed-on-phone.mp4") >> text=Delete');
    await page.waitForSelector('#confirmrow:has-text("Delete renamed-on-phone.mp4? The file is removed from the box.")');
    await page.click('#confirmno');
    await page.waitForSelector('.item:has-text("renamed-on-phone.mp4") >> text=Delete');
    await page.click('.item:has-text("renamed-on-phone.mp4") >> text=Delete');
    await page.click('#confirmyes');
    await page.waitForFunction(() => !/renamed-on-phone/.test(document.body.textContent));
    await page.setInputFiles('#filepick', { name: 'virus.exe', mimeType: 'application/octet-stream', buffer: Buffer.alloc(100, 1) });
    await page.waitForFunction(() => /only video, image and audio files/.test(document.getElementById('uploads').textContent), null, { timeout: 8000 });

    // Nothing in a card may be wider than the card at phone width (names squeezed, buttons off the edge).
    async function fitsCard(selector, what) {
      const bad = await page.evaluate((sel) => {
        const out = [];
        document.querySelectorAll(sel).forEach((card) => {
          const box = card.getBoundingClientRect();
          card.querySelectorAll('button, input, select, span, img').forEach((el) => {
            const r = el.getBoundingClientRect();
            if (r.width && (r.right > box.right + 1 || r.left < box.left - 1)) out.push((el.textContent || el.id || el.tagName).trim().slice(0, 30));
          });
          card.querySelectorAll('.item > span:first-child').forEach((el) => { if (el.getBoundingClientRect().width < 60 && el.textContent.length > 8) out.push('squeezed: ' + el.textContent.slice(0, 30)); });
        });
        return out;
      }, selector);
      assert(bad.length === 0, what + ': outside the card or squeezed: ' + bad.join(', '));
    }
    await fitsCard('.card', 'Media');
    // Nothing may make the page wider than the phone.
    async function fitsPhone(what) {
      const wide = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      assert(wide <= 1, what + ': ' + wide + ' px wider than the phone');
    }
    // The same two checks for any page of any device (the owner's, a presenter's, a guest's), at any width.
    const fitsOn = async (pg, what) => {
      const bad = await pg.evaluate(() => {
        const out = [];
        if (document.documentElement.scrollWidth > window.innerWidth + 1) out.push('the page is wider than the window');
        document.querySelectorAll('.card').forEach((card) => {
          const box = card.getBoundingClientRect();
          card.querySelectorAll('button, input, select, span, img').forEach((el) => {
            if (el.closest('.ctlscroll')) return;               // a controller's drawing scrolls sideways inside its card, on purpose
            const r = el.getBoundingClientRect();
            if (r.width && (r.right > box.right + 1 || r.left < box.left - 1)) out.push((el.textContent || el.id || el.tagName).trim().slice(0, 30));
          });
        });
        return out;
      });
      assert.deepStrictEqual(bad, [], what + ': ' + bad.join('; '));
    };
    // System is an index of rows and one page per row. sysIndex() goes to the index, sys(name) opens a row's page,
    // onPage() checks the open page fits, chip() waits for what a row's chip says.
    const rowOf = (name) => `.navrow:has(.navname:text-is("${name}"))`;
    async function sysIndex() {
      if (await page.locator('#sysback').count()) await page.click('#sysback');
      else if (!(await page.locator('#sysindex').count())) await page.click('nav >> text=System');
      await page.waitForSelector('#sysindex');
    }
    async function sys(name) {
      await sysIndex();
      await page.click(rowOf(name));
      await page.waitForSelector(`#syspage h1:text-is("${name}")`);
    }
    async function chip(name, word) { await page.waitForSelector(`${rowOf(name)} .chip:text-is("${word}")`, { timeout: 8000 }); }
    async function onPage(name) {
      assert.strictEqual(await page.textContent('#syspage h1'), name, 'still on the ' + name + ' page');
      await fitsCard('.card, #syspage', name + ' page');
      await fitsPhone(name + ' page');
    }
    const post = (path, body) => page.evaluate(([p, b]) => fetch(p, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-PVJ-Request': '1' }, body: JSON.stringify(b) }).then((r) => r.status), [path, body]);
    const get = (path) => page.evaluate((p) => fetch(p).then((r) => r.json()), path);
    const moduleIsOn = async (id) => (await get('/api/modules')).modules.some((m) => m.id === id && m.enabled);
    // Switching off with the page's switch: the page then shows only the description and the big button
    async function switchOff(name) {
      await page.click('#sysswitch');
      await page.waitForSelector('#sysoff');
      await page.waitForFunction(() => /Switched off/.test(document.getElementById('msg').textContent));
      assert.strictEqual(await page.getAttribute('#sysswitch', 'aria-checked'), 'false', name + ' is off');
      assert.strictEqual(await page.textContent('#syspage h1'), name, 'switching off keeps the ' + name + ' page open');
    }
    // The page's switch: off shows only the description and one big button; switching on keeps the person there
    async function switchOn(name) {
      assert.strictEqual(await page.getAttribute('#sysswitch', 'aria-checked'), 'false', name + ' starts off');
      assert.strictEqual(await page.textContent('#sysswitchlabel'), 'Off');
      assert(/Your settings are kept/.test(await page.textContent('#sysoff')), name + ': the off page says the settings are kept');
      assert.strictEqual(await page.textContent('#sysswitchon'), 'Switch on ' + name);
      await page.click('#sysswitchon');
      await page.waitForSelector('#sysswitch[aria-checked="true"]');
      await page.waitForFunction(() => /Switched on/.test(document.getElementById('msg').textContent));
      assert.strictEqual(await page.textContent('#sysswitchlabel'), 'On');
      assert.strictEqual(await page.textContent('#syspage h1'), name, 'switching on keeps the ' + name + ' page open');
    }

    // Mix: drag a slider and check the throttle keeps request count sane
    await page.click('nav >> text=Mix');
    await page.waitForSelector('#mo');
    // Mix: Crossfade can be chosen (it used to be greyed out as "soon"); the box keeps it and the picker shows it
    await page.click('button:has-text("Crossfade")');
    await page.waitForFunction(() => fetch('/api/status').then((r) => r.json()).then((j) => j.mix.transition === 'crossfade'));
    await page.waitForSelector('button.on:has-text("Crossfade")');
    for (const [label, value] of [['Wipe from left', 'wipe-from-left'], ['Wipe from bottom', 'wipe-from-bottom'], ['Slide off left', 'slide-left'], ['Slide off down', 'slide-down']]) {
      await page.click('button:has-text("' + label + '")');
      await page.waitForFunction((v) => fetch('/api/status').then((r) => r.json()).then((j) => j.mix.transition === v), value);
      await page.waitForSelector('button.on:has-text("' + label + '")');
    }
    // A box that gave up on the transition says why under the picker, in words that stay inside a phone's width, and
    // Try again does what choosing the transition again does. (The box's answer is given a reason here: nothing in
    // the harness is slow enough to make one.)
    {
      const why = '2 stills in a row took over a second (1.2, 1.4); dipping to black instead. The box tries again in 5 minutes, or at once when the transition is chosen again';
      assert.strictEqual(await page.locator('#mixfallback').count(), 0, 'nothing is said while the box does the transition');
      let giveUp = true;
      await page.route('**/api/status', async (route) => {
        const response = await route.fetch();
        const body = await response.json();
        if (giveUp) body.mix.fallback = why;
        await route.fulfill({ response, json: body });
      });
      const before = page.viewportSize();
      await page.setViewportSize({ width: 320, height: before.height });
      await page.click('nav >> text=Live');
      await page.click('nav >> text=Mix');
      await page.waitForSelector('#mixfallbackwhy');
      assert.strictEqual(await page.textContent('#mixfallbackwhy'), 'This box is not doing that transition now: ' + why + '.');
      const fits = await page.evaluate(() => {
        const box = document.getElementById('mixfallback').getBoundingClientRect(), text = document.getElementById('mixfallbackwhy').getBoundingClientRect();
        return { right: Math.max(box.right, text.right), wide: window.innerWidth, page: document.documentElement.scrollWidth };
      });
      assert(fits.right <= fits.wide && fits.page <= fits.wide, 'the reason sticks out at 320 px: ' + JSON.stringify(fits));
      const sent = page.waitForRequest((r) => r.url().endsWith('/api/mix') && r.method() === 'POST');
      giveUp = false;
      await page.click('#mixretry');
      assert.deepStrictEqual(JSON.parse((await sent).postData()), { transition: 'slide-down', duration: (await get('/api/status')).mix.duration });
      await page.waitForFunction(() => !document.getElementById('mixfallback'));
      await page.unroute('**/api/status');
      await page.setViewportSize(before);
    }
    await page.click('button:has-text("Dip to black")');
    await page.waitForFunction(() => fetch('/api/status').then((r) => r.json()).then((j) => j.mix.transition === 'dip'));
    await page.waitForSelector('button.on:has-text("Dip to black")');
    let controlCalls = 0;
    page.on('request', (r) => { if (r.url().endsWith('/api/control') && r.method() === 'POST') controlCalls++; });
    await page.evaluate(() => {
      const el = document.getElementById('mo');
      for (let v = 100; v >= 20; v--) { el.value = v; el.dispatchEvent(new Event('input')); }
      el.dispatchEvent(new Event('change'));
    });
    await page.waitForTimeout(400);
    assert(controlCalls > 0 && controlCalls < 10, 'expected throttled slider requests, got ' + controlCalls);
    if (shots) await page.screenshot({ path: path.join(shots, '3-mix.png') });
    await page.click('text=90°');
    await page.click('text=Reset mix');

    // System: the index (three groups, a row each), the folded list of what is not built, then each page
    await sysIndex();
    await page.waitForSelector('h1:has-text("System")');
    assert.deepStrictEqual(await page.$$eval('.navhead', (hs) => hs.map((x) => x.textContent)), ['Everyday', 'Show tools', 'This box']);
    assert.deepStrictEqual(await page.$$eval('.navname', (ns) => ns.map((x) => x.textContent)), ['Health', 'Projectors', 'Room', 'Schedule', 'Shaders and Vibes', 'People and codes', 'Sound',
      'At power-up', 'Streams', 'Projection mapping', 'Boxes in step', 'MIDI controller', 'DMX lighting desk', 'OSC',
      'Network', 'Updates', 'Remote support', 'Backup and reset', 'Look', 'About and power'], 'the rows a full-access device sees');
    const low = await page.$$eval('.navrow', (rs) => rs.filter((r) => r.getBoundingClientRect().height < 56).map((r) => r.textContent));
    assert.deepStrictEqual(low, [], 'every row is at least 56 px high');
    assert.strictEqual(await page.textContent('#notbuilt summary'), 'Not built yet (5)');
    assert(!(await page.isVisible('#notbuilt >> text=NDI')), 'the list of what is not built starts folded');
    await page.click('#notbuilt summary');
    assert(await page.isVisible('#notbuilt >> text=NDI'), 'NDI is listed as not built yet');
    assert(await page.isVisible('#notbuilt >> text=Receive NDI from Resolume'), 'with its description');
    assert.strictEqual(await page.locator('#notbuilt button').count(), 0, 'what is not built has no switch');
    await page.click('#notbuilt summary');
    await page.waitForFunction(() => document.querySelector('#nav-health .navstate').textContent.length > 8, null, { timeout: 8000 });   // the box's name and "all well", or the worst problem
    await chip('Network', 'Off');
    await chip('At power-up', 'Off');
    await fitsCard('.card', 'System index');
    await fitsPhone('System index');
    // Network: switch the module on, preview, apply, watch the countdown, confirm
    await sys('Network');
    assert.strictEqual(await page.locator('#netcard').count(), 0, 'a module that is off shows no card');
    await switchOn('Network');
    await page.waitForSelector('#netiface');
    await page.waitForSelector('#netcard >> text=192.168.1.9/24', { timeout: 8000 });  // the address arrives after the card first draws
    // Every field has a visible label; the box tries a setting, it does not just "apply" it; the rest is under Advanced
    await page.click('#netmodes >> text=Fixed address');
    await page.waitForSelector('#netgw');
    for (const [id, text] of [['netaddr', 'Address'], ['netprefix', 'Size of the network'], ['netgw', 'Router (optional)'], ['netdns', 'Name servers (optional)']]) {
      assert.strictEqual(await page.textContent(`label[for="${id}"]`), text, 'the label of #' + id);
    }
    assert.strictEqual(await page.textContent('#netapply'), 'Try this setting');
    assert(!(await page.isVisible('#netpreview')) && !(await page.isVisible('#netsecs')), 'the commands and the time to go back are under Advanced');
    assert(await page.isVisible('#netiface'), 'two ports: the choice of port is shown');
    await page.fill('#netaddr', '192.168.50.20');
    await page.fill('#netprefix', '24');
    // The gateway is set without any input event (as a lost or late event would): the redraw must still keep it.
    // (waits for the field: the card may be being redrawn at this moment, which is exactly what this step is about)
    await page.waitForFunction(() => { const g = document.getElementById('netgw'); if (!g) return false; g.value = '192.168.50.1'; return true; }, null, { timeout: 8000 });
    // A redraw of the screen must not wipe what was typed: leave for another screen and come back to the page
    await page.click('nav >> text=Mix');
    await page.waitForSelector('#mo');
    await sys('Network');
    try {
      await page.waitForFunction(() => {
        const a = document.getElementById('netaddr'), g = document.getElementById('netgw');
        return a && g && a.value === '192.168.50.20' && g.value === '192.168.50.1';
      }, null, { timeout: 8000 });  // typed values survive the redraw (polls until the card is rebuilt)
    } catch (e) {
      // Say what the card looked like, so a failure that only happens on a slow runner can be understood.
      const seen = await page.evaluate(() => {
        const card = document.getElementById('netcard');
        const v = (id) => { const el = document.getElementById(id); return el ? el.value : '(missing)'; };
        return JSON.stringify({ cards: document.querySelectorAll('#netcard').length, addr: v('netaddr'), prefix: v('netprefix'), gw: v('netgw'),
          mode: document.querySelector('#netmodes .on') && document.querySelector('#netmodes .on').textContent,
          text: card ? card.textContent.slice(0, 300) : '(no card)' });
      }).catch((x) => 'evaluate failed: ' + x.message);
      throw new Error(e.message.split('\n')[0] + ' | card state: ' + seen);
    }
    // The card can still be redrawn once more after loading (which clears the preview), so press again until it sticks
    for (let tries = 0; ; tries++) {
      if (!(await page.isVisible('#netpreview'))) await page.click('#netadv > summary');      // the commands are under Advanced
      await page.click('#netpreview');
      try {
        await page.waitForFunction(() => { const e = document.getElementById('netplan'); return e && /ipv4\.addresses 192\.168\.50\.20\/24/.test(e.textContent); }, null, { timeout: 2500 });
        break;
      } catch (e) { if (tries >= 4) throw e; }
    }
    await page.fill('#netaddr', '8.8.8.8; reboot');
    if (shots) await page.screenshot({ path: path.join(shots, '6-network.png'), fullPage: true });
    await page.click('#netapply');
    await page.waitForFunction(() => /address/i.test(document.getElementById('netresult').textContent) && document.getElementById('netresult').className.includes('err'));
    await page.fill('#netaddr', '192.168.50.20');
    await page.click('#netapply');
    await page.waitForSelector('#netpending');
    await page.waitForFunction(() => /Reverts in \d+ s/.test(document.getElementById('netleft').textContent));
    await page.click('#netconfirm');
    await page.waitForSelector('#netiface');
    await page.click('#netmodes >> text=Direct cable');
    await page.click('#netapply');
    await page.waitForSelector('#netpending');
    await page.click('#netrevert');
    await page.waitForSelector('#netiface');
    // Wi-Fi: pick the Wi-Fi port, find networks, choose one, type its password, apply, confirm; the password never comes back
    await page.selectOption('#netiface', 'wlan0');
    await page.waitForSelector('#netmodes >> text=Own hotspot');
    assert(!(await page.isVisible('#netmodes >> text=Direct cable')), 'wired modes are not offered for Wi-Fi');
    await page.click('#netscanbtn');
    await page.waitForSelector('#netscan >> text=Leyline Staff');
    assert(await page.isDisabled('#netscan button:has-text("Corp")'), 'a company (802.1X) network cannot be chosen');
    assert(await page.isVisible('#netscan button:has-text("<img src=x")'), 'a hostile name is shown as text');
    assert.strictEqual(await page.$$eval('#netcard img', (els) => els.length), 0, 'and never becomes markup');
    assert(!(await page.evaluate(() => window.pwned)), 'nothing from a network name ran');
    await page.click('#netscan >> text=Leyline Staff');
    assert.strictEqual(await page.inputValue('#netssid'), 'Leyline Staff');
    await page.fill('#netpass', 'short');
    await page.click('#netapply');
    await page.waitForFunction(() => /password/i.test(document.getElementById('netresult').textContent) && document.getElementById('netresult').className.includes('err'));
    await page.fill('#netpass', 'staff only 2026');
    const applied = page.waitForResponse((r) => r.url().endsWith('/api/network/apply'));
    await page.click('#netapply');
    assert(!(await (await applied).text()).includes('staff only'), 'the password is not sent back');
    await page.waitForFunction(() => /joining \u201cLeyline Staff\u201d/.test((document.getElementById('netpending') || {}).textContent || ''));
    await page.click('#netconfirm');
    await page.waitForFunction(() => /joined \u201cLeyline Staff\u201d/.test((document.getElementById('netline-wlan0') || {}).textContent || ''));
    assert(!(await page.content()).includes('staff only'), 'the password is not left on the page');
    await page.selectOption('#netiface', 'wlan0');
    await page.click('#netmodes >> text=Own hotspot');
    await page.waitForSelector('#netband');
    await page.selectOption('#netiface', 'eth0');
    await page.waitForSelector('#netmodes >> text=Direct cable');
    await page.waitForFunction(() => !document.getElementById('netreverting') && !document.getElementById('netpending'));
    await onPage('Network');
    // Back returns to the index (the button, and the phone's own back), and the row now tells the truth
    await page.click('#sysback');
    await page.waitForSelector('#sysindex');
    await chip('Network', 'Ready');
    await page.waitForSelector(`${rowOf('Network')} .navstate:has-text("192.168.1.9/24")`);
    await page.click(rowOf('Network'));
    await page.waitForSelector('#syspage h1:text-is("Network")');
    await page.evaluate(() => history.back());
    await page.waitForSelector('#sysindex');
    // OSC has no module: the page's switch is OSC itself, and there is no second switch inside the card
    await sysIndex();
    await chip('OSC', 'Off');
    await sys('OSC');
    assert.strictEqual(await page.locator('#osccard, #oscline').count(), 0, 'OSC off shows no card');
    await switchOn('OSC');
    await page.waitForFunction(() => /^Listening on UDP port \d+\. Nothing received yet\.$/.test(document.getElementById('oscline').textContent));
    assert.strictEqual((await get('/api/osc')).enabled, true, 'the page switch turned OSC on');
    // Fields have labels; Save changes waits for a change; the extra networks are under Advanced
    assert.strictEqual(await page.textContent('label[for="oscport"]'), 'UDP port');
    assert(await page.isDisabled('#oscsave') && !(await page.isVisible('#oscallow')), 'nothing to save yet, and the networks are folded');
    await page.click('#oscadv > summary');
    assert.strictEqual(await page.textContent('label[for="oscallow"]'), 'Also accept from these networks');
    const oscPort = await page.inputValue('#oscport');
    await page.fill('#oscport', '80');
    assert(await page.isVisible('#oscsavedirty'), 'a changed port says Not saved yet');
    await page.click('#oscsave');
    await page.waitForFunction(() => /Nothing was changed/.test(document.getElementById('oscsaveresult').textContent));
    await page.fill('#oscport', oscPort);
    assert(await page.isDisabled('#oscsave'), 'back to the saved port: nothing to save');
    assert.strictEqual(await page.locator('#osctoggle').count(), 0, 'no second switch inside the OSC card');
    await onPage('OSC');
    await sysIndex();
    await chip('OSC', 'Ready');
    await sys('OSC');
    await switchOff('OSC');
    assert.strictEqual((await get('/api/osc')).enabled, false, 'the page switch turned OSC off');
    await onPage('OSC');
    // Autostart: reject a missing clip, then save "play every clip" and see it summarised
    await sys('At power-up');
    await page.waitForSelector('#autoline:has-text("Off")');
    assert(await page.isDisabled('#autosave'), 'nothing to save until something changed');
    assert.strictEqual(await page.textContent('#autosave'), 'Save changes');
    assert.strictEqual(await page.textContent('label[for="automode"]'), 'What happens at power-up', 'a visible label above the choice');
    await page.selectOption('#automode', 'file');
    assert(await page.isVisible('#autosavedirty') && /Not saved yet/.test(await page.textContent('#autosavedirty')), 'a change says Not saved yet');
    assert(await page.isDisabled('#autotest') && /Save first to try it/.test(await page.textContent('#autotestnote')), 'Try it now waits for the save');
    for (const id of ['autofile', 'autoloop', 'autodelay']) assert(await page.isVisible('label[for="' + id + '"]'), 'a visible label for #' + id);
    await page.selectOption('#autofile', { index: 0 });
    await page.fill('#autodelay', '999');
    await page.click('#autosave');
    await page.waitForFunction(() => /delay must be/.test(document.getElementById('msg').textContent));
    await page.fill('#autodelay', '3');
    await page.selectOption('#automode', 'all');
    await page.click('#autosave');
    await page.waitForFunction(() => /Play every clip.*after 3 s/.test(document.getElementById('autoline').textContent));
    assert.strictEqual(await page.textContent('#autosaveresult'), 'Saved', 'the result is said under the button');
    assert(await page.isDisabled('#autosave') && !(await page.isDisabled('#autotest')), 'saved: nothing more to save, and it can be tried');
    // Vibes needs its module, which is off here: the choice is marked, and choosing it offers the switch in place
    assert(/Start Vibes \(Vibes is off\)/.test(await page.textContent('#automode option[value="vibes"]')), 'the Vibes choice is marked off');
    await page.selectOption('#automode', 'vibes');
    assert(/Vibes is switched off, so this will do nothing/.test(await page.textContent('#autovibesoff')), 'choosing it says so');
    assert.strictEqual(await page.textContent('#autovibesoffon'), 'Switch Vibes on');
    await page.selectOption('#automode', 'slideshow');
    assert(await page.isVisible('#autoseconds') && await page.isVisible('#autoshuffle') && !(await page.isVisible('#autofile')), 'slideshow fields');
    await page.fill('#autoseconds', '8');
    await page.selectOption('#autoshuffle', 'true');
    await page.click('#autosave');
    await page.waitForFunction(() => /Slideshow.*8 s a picture.*shuffled/.test(document.getElementById('autoline').textContent));
    await page.selectOption('#automode', 'usb');
    await page.click('#autosave');
    await page.waitForFunction(() => /USB stick/.test(document.getElementById('autoline').textContent));
    await page.selectOption('#automode', 'off');
    await page.click('#autosave');
    await page.waitForFunction(() => /^Off/.test(document.getElementById('autoline').textContent));
    await onPage('At power-up');
    // DMX: ONE switch. It switches the module on and then DMX itself, so the box is listening when it says On.
    await sys('DMX lighting desk');
    await switchOn('DMX lighting desk');
    await page.waitForSelector('#dmxline:text-is("Listening for Art-Net on universe 0. Nothing received yet.")');
    assert(await moduleIsOn('control-dmx') && (await get('/api/dmx')).enabled === true, 'the page switch turned on the module and DMX itself');
    assert.strictEqual(await page.locator('#dmxtoggle').count(), 0, 'no second switch inside the DMX card');
    for (const id of ['dmxproto', 'dmxuni', 'dmxstart']) assert(await page.isVisible(`label[for="${id}"]`), `a visible label above #${id}`);
    assert(await page.isDisabled('#dmxsave') && !(await page.isVisible('#dmxallow')), 'nothing to save yet, and the networks are folded under Advanced');
    // The channel table: number, what it does, the live level; eight channels from the start channel, and the ninth
    assert.deepStrictEqual(await page.$$eval('#dmxchannels tbody tr', (rs) => rs.map((r) => r.children[0].textContent + ' ' + r.querySelector('.field').textContent)),
      ['1 Opacity', '2 Size', '3 Position X', '4 Speed', '5 Volume', '6 Blackout', '7 Pad', '8 Function', '9 Vibes (optional)'], 'the channels, by name');
    await page.fill('#dmxuni', '99999');
    assert(await page.isVisible('#dmxsavedirty'), 'a change says Not saved yet');
    await page.click('#dmxsave');
    await page.waitForFunction(() => /universe/i.test(document.getElementById('msg').textContent));
    assert(/universe/i.test(await page.textContent('#dmxsaveresult')), 'the refusal is under the button too');
    await page.fill('#dmxuni', '2');
    await page.fill('#dmxstart', '101');
    await page.click('#dmxsave');
    await page.waitForFunction(() => fetch('/api/dmx').then((r) => r.json()).then((d) => d.universe === 2 && d.start === 101));
    await page.waitForSelector('#dmxline:has-text("Listening for Art-Net on universe 2.")');
    await page.waitForSelector('#dmxchannels tr[data-ch="101"]');
    assert.strictEqual(await page.textContent('#dmxchannels tbody tr >> nth=0 >> td >> nth=0'), '101', 'the table follows the start channel');
    assert.strictEqual(await page.textContent('#dmxsaveresult'), 'Saved');
    await page.fill('#dmxstart', '1');                                // back to channel 1, where the rest of this test expects it
    await page.click('#dmxsave');
    await page.waitForSelector('#dmxchannels tr[data-ch="1"]');
    await onPage('DMX lighting desk');
    await sysIndex();
    await chip('DMX lighting desk', 'Ready');
    await page.waitForSelector(`${rowOf('DMX lighting desk')} .navstate:has-text("Listening")`);
    // A box in the mixed state (the module on, DMX itself off, as boxes set up before this were): the row and the
    // page say Off, and switching on sends only DMX's own call. A field being edited is never saved by the switch.
    assert.strictEqual(await post('/api/dmx', { enabled: false }), 200);
    await sys('DMX lighting desk');
    await page.waitForSelector('#sysoff');
    assert.strictEqual(await page.getAttribute('#sysswitch', 'aria-checked'), 'false', 'module on but DMX off shows Off');
    await sysIndex();
    await chip('DMX lighting desk', 'Off');
    assert(!/not listening|inside/.test(await page.textContent(rowOf('DMX lighting desk'))), 'the row no longer points at a second switch');
    await sys('DMX lighting desk');
    await page.waitForSelector('#sysswitchon');
    const dmxCalls = [];
    const dmxSeen = (r) => { if (r.method() === 'POST' && /\/api\/(dmx|modules\/control-dmx)$/.test(r.url())) dmxCalls.push(r.url().replace(/^.*\/api\//, '') + ' ' + r.postData()); };
    page.on('request', dmxSeen);
    await page.click('#sysswitchon');
    await page.waitForSelector('#sysswitch[aria-checked="true"]');
    await page.waitForSelector('#dmxuni');
    assert.deepStrictEqual(dmxCalls, ['dmx {"enabled":true}'], 'switching on from the mixed state sends only the inner call, with nothing but the flag');
    await page.fill('#dmxuni', '7');                                 // typed, not saved
    dmxCalls.length = 0;
    await page.click('#sysswitch');
    await page.waitForSelector('#sysoff');
    assert.deepStrictEqual(dmxCalls, ['modules/control-dmx {"enabled":false}'], 'switching off switches the module off and saves nothing else');
    await page.click('#sysswitchon');
    await page.waitForSelector('#sysswitch[aria-checked="true"]');
    page.off('request', dmxSeen);
    assert.strictEqual((await get('/api/dmx')).universe, 2, 'the switch did not save the universe that was only typed');
    await onPage('DMX lighting desk');
    // MIDI: switch the module on; nothing is plugged in here, so check the card and the learn flow, then turn it off
    await sys('MIDI controller');
    await switchOn('MIDI controller');
    await page.waitForFunction(() => /waiting for a controller/.test(document.getElementById('midiline').textContent));
    assert(await moduleIsOn('control-midi') && (await get('/api/midi')).enabled === true, 'the page switch turned on the module and MIDI itself');
    assert.strictEqual(await page.locator('#miditoggle').count(), 0, 'no second switch inside the MIDI card');
    await page.waitForSelector('#midinomap');
    for (const a of ['vibes', 'vibes_next', 'vibes_dwell']) assert.strictEqual(await page.locator('#midiaction option[value="' + a + '"]').count(), 1, 'the MIDI action list offers ' + a);
    for (const a of ['code_join', 'code_owner']) assert(/hold 3 seconds/.test(await page.textContent('#midiaction option[value="' + a + '"]')), 'the action list offers ' + a + ' and says it is a hold');
    await page.selectOption('#midiaction', 'pad');
    await page.selectOption('#midibank', '1');
    await page.click('#midilearn');
    await page.waitForSelector('#midilearning');
    await page.click('#midicancel');
    await page.waitForSelector('#midilearn');
    assert.strictEqual(await page.getAttribute('#midibuiltin', 'role'), 'switch', 'the built-in map is a real switch');
    await page.click('#midibuiltin');
    await page.waitForFunction(() => { const b = document.getElementById('midibuiltin'); return b && b.getAttribute('aria-checked') === 'false'; });
    assert.strictEqual((await get('/api/midi')).builtin, false, 'the switch applied on tap');
    // Controller profiles: the harness's fake Korg nanoKONTROL2 (pipes, no hardware) is plugged in while the page is
    // open. It is recognised and its layout is drawn; a moved control lights up; a tap changes what a control does.
    const fs = require('fs');
    const midiPlug = path.join(info.midi_dir, 'plug'), midiIn = path.join(info.midi_dir, 'in');
    const nano = '.ctlcard[data-ctl="nanoKONTROL2"]';
    assert.strictEqual(await page.locator('.ctlcard').count(), 0, 'no controller card before one is plugged in');
    fs.writeFileSync(midiPlug, '');
    await page.waitForSelector(nano + ' .ctlline:has-text("Korg nanoKONTROL2: recognised, standard layout on.")', { timeout: 15000 });
    await page.waitForSelector('.ctlcard[data-ctl="keys"] .ctlline:has-text("No built-in layout for this one yet. Teach it below.")');
    assert.strictEqual(await page.locator(nano + ' .ctl').count(), 51, 'every control of the nanoKONTROL2 is drawn');
    assert.strictEqual(await page.locator('.ctlcard[data-ctl="keys"] .ctl').count(), 0, 'an unknown controller has no drawn layout');
    assert.strictEqual(await page.textContent(nano + ' .ctl[data-id="fader1"] .ctlwhat'), 'Opacity');
    assert.strictEqual(await page.textContent(nano + ' .ctl[data-id="knob3"] .ctlwhat'), 'Shader control 3');
    assert.strictEqual(await page.textContent(nano + ' .ctl[data-id="r8"] .ctlwhat'), 'Blackout on / off 2x');
    assert.strictEqual(await page.textContent(nano + ' .ctl[data-id="fader8"] .ctlwhat'), 'Spare');
    assert.strictEqual(await page.textContent(nano + ' .ctl[data-id="fader7"] .ctlwhat'), 'Effect amount');
    assert.strictEqual(await page.locator(nano + ' .ctl.lit').count(), 0, 'nothing is lit before a control is moved');
    await page.waitForSelector(nano + ' .ctlquiet:has-text("Nothing received yet. Move a control. If this is a nanoKONTROL2, hold SET MARKER and CYCLE while plugging it in")');
    fs.appendFileSync(midiIn, 'B0 10 40\n');                                    // knob 1 is turned
    await page.waitForSelector(nano + ' .ctl[data-id="knob1"].lit');
    assert.strictEqual(await page.textContent(nano + ' .ctl[data-id="knob1"] .ctlval'), '64');
    assert.strictEqual(await page.locator(nano + ' .ctlquiet').isHidden(), true, 'the "nothing received" line goes with the first message');
    await page.waitForSelector(nano + ' .ctl[data-id="knob1"]:not(.lit)', { timeout: 8000 });      // and goes dark again
    fs.appendFileSync(midiIn, 'B0 00 05\n');                                    // fader 1, far below the box's 100 percent: it waits (pickup)
    await page.waitForSelector(nano + ' .ctl[data-id="fader1"].wait');
    assert.strictEqual((await get('/api/midi')).controllers[0].controls.filter((x) => x.id === 'fader1')[0].pickup, true);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1), 'the drawn layout made the page scroll sideways');
    // a tap opens the chooser; the choice is saved as this person's mapping, and "Back to the standard" undoes it
    await page.click(nano + ' .ctl[data-id="r5"]');
    await page.waitForSelector('#ctldetail #ctlnow:has-text("R 5: Effect on / off (standard layout)")');     // it was spare before effects came
    assert.strictEqual(await page.locator('#ctlaction option[value="opacity"]').count(), 0, 'a button is not offered a fader\'s action');
    await page.selectOption('#ctlaction', 'pad');
    await page.selectOption('#ctlbank', '1');
    await page.selectOption('#ctlindex', '2');
    await page.click('#ctlsave');
    await page.waitForSelector(nano + ' .ctl[data-id="r5"].mine:has-text("Pad B3")');
    await page.waitForSelector(nano + ' .ctlline:has-text("1 control changed by you")');
    let midiMap = (await get('/api/midi')).map;
    assert.deepStrictEqual(midiMap.map((e) => [e.source, e.kind, e.number, e.action, e.bank, e.index]), [['nanoKONTROL2', 'cc', 68, 'pad', 1, 2]]);
    await page.waitForSelector('#midicard .midi-entry:has-text("nanoKONTROL2")');       // and it is listed with the other mappings
    await page.click('#ctlback');
    await page.waitForSelector(nano + ' .ctl[data-id="r5"]:not(.mine):has-text("Effect on / off")');
    assert.strictEqual((await get('/api/midi')).map.length, 0, 'Back to the standard removed the mapping');
    // Save on a guarded control that was not changed is not offered, so the press-twice guard cannot be saved away
    await page.click(nano + ' .ctl[data-id="r8"]');
    await page.waitForSelector('#ctldetail #ctlnow:has-text("R 8: Blackout on / off (standard layout)")');
    assert.strictEqual(await page.isDisabled('#ctlsave'), true, 'Save is off until the choice differs');
    assert.strictEqual(await page.getAttribute('#ctltwice', 'aria-checked'), 'true', 'press twice is on');
    assert.strictEqual(await post('/api/midi/map', { set: { controller: 'nanoKONTROL2', control: 'r8', action: { action: 'blackout' } } }), 200);
    assert.strictEqual((await get('/api/midi')).map.length, 0, 'saving the standard through the API stores nothing either');
    await page.click('#ctltwice');
    assert.strictEqual(await page.isDisabled('#ctlsave'), false, 'switching press twice off is a change');
    await page.click('#ctltwice');
    assert.strictEqual(await page.isDisabled('#ctlsave'), true);
    await page.selectOption('#ctlaction', 'stop');
    assert.strictEqual(await page.locator('#ctltwicerow').isHidden(), true, 'press twice is only for blackout and Room scenes');
    assert.strictEqual(await page.isDisabled('#ctlsave'), false);
    // the whole controller, with the question in place
    await page.click(nano + ' .ctl[data-id="stop"]');
    await page.selectOption('#ctlaction', 'none');
    await page.click('#ctlsave');
    await page.waitForSelector(nano + ' .ctl[data-id="stop"].mine:has-text("Nothing")');
    await page.click(nano + ' .ctlreset');
    await page.waitForSelector('#confirmrow:has-text("back to the standard?")');
    await page.click('#confirmno');
    assert.strictEqual((await get('/api/midi')).map.length, 1, 'answering no keeps the mapping');
    await page.click(nano + ' .ctlreset');
    await page.click('#confirmyes');
    await page.waitForSelector(nano + ' .ctl[data-id="stop"]:not(.mine):has-text("Stop")');
    assert.strictEqual((await get('/api/midi')).map.length, 0);
    // Lights: off for a nanoKONTROL2 until someone switches them on (its LED mode has to be set in Korg's editor first);
    // the real switch applies on tap, the drawn layout marks the controls that have a light, and Test lights runs the
    // sweep. What the box writes to the (fake) controller is read back: fixed three-byte messages, nothing else.
    const midiOut = path.join(info.midi_dir, 'out');
    const wrote = async (re) => {
      for (let i = 0; i < 150; i++) {
        const text = fs.existsSync(midiOut) ? fs.readFileSync(midiOut, 'utf8') : '';
        if (re.test(text)) return text;
        await new Promise((r) => setTimeout(r, 100));
      }
      throw new Error('the controller was never sent ' + re);
    };
    await page.waitForSelector(nano + ' .ctllightline:has-text("Lights off.")');
    await page.waitForSelector(nano + ' .ctllightnote:has-text("Set LED mode to External in Korg\'s editor first")');
    assert.strictEqual(await page.getAttribute(nano + ' .ctllights', 'role'), 'switch', 'Lights is a real switch');
    assert.strictEqual(await page.getAttribute(nano + ' .ctllights', 'aria-checked'), 'false', 'off until switched on');
    assert.strictEqual(await page.locator(nano + ' .ctltest').count(), 0, 'no Test lights while the lights are off');
    assert.strictEqual(await page.locator(nano + ' .ctl.haslight').count(), 30, 'the drawn layout marks the thirty controls that have a light');
    assert.strictEqual(await page.locator(nano + ' .ctl[data-id="fader1"].haslight, ' + nano + ' .ctl[data-id="track_prev"].haslight').count(), 0);
    assert(!fs.existsSync(midiOut), 'nothing is written to a controller whose lights are off');
    await page.click(nano + ' .ctllights');
    await page.waitForSelector(nano + ' .ctllights[aria-checked="true"]');
    await page.waitForSelector(nano + ' .ctllightline:has-text("Lights on.")');
    assert.strictEqual((await get('/api/midi')).controllers[0].lights.on, true, 'the switch applied on tap');
    await wrote(/^b0 20 (00|7f)$/m);                                             // S 1 was told what to show (CC 32)
    assert.strictEqual(await page.locator(nano + ' .ctlbright').count(), 0, 'lights of one colour: no brightness choice');
    await page.click(nano + ' .ctltest');
    await page.waitForSelector(nano + ' .ctllightline:has-text("Testing")');
    await wrote(/^b0 47 7f$/m);                                                  // the sweep reached R 8 (CC 71), which is dark otherwise
    await page.waitForSelector(nano + ' .ctltest:not([disabled])', { timeout: 15000 });
    await page.waitForSelector(nano + ' .ctllightline:not(:has-text("Testing"))');
    const sentToNano = (await wrote(/^b0 47 00$/m)).trim().split('\n');            // and back to what the box says
    assert(sentToNano.length >= 60 && sentToNano.every((l) => /^b0 [0-9a-f]{2} (00|7f)$/.test(l)), 'only the profile\'s own messages reach the controller');
    assert.strictEqual(await post('/api/midi/lights', { controller: 'nanoKONTROL2', test: true, bytes: [240, 1, 247] }), 400, 'a request cannot carry bytes');
    assert.strictEqual(await post('/api/midi/lights', { controller: 'keys', test: true }), 409, 'no lights for a controller without a layout');
    assert.strictEqual(await page.locator('.ctlcard[data-ctl="keys"] .ctllights').count(), 0);
    await page.click(nano + ' .ctllights');
    await page.waitForSelector(nano + ' .ctllightline:has-text("Lights off.")');
    assert.strictEqual(await page.locator(nano + ' .ctltest').count(), 0);
    // the switch per controller: a real switch, and the layout is off without unplugging
    assert.strictEqual(await page.getAttribute(nano + ' .ctlstd', 'role'), 'switch');
    await page.click(nano + ' .ctlstd');
    await page.waitForSelector(nano + ' .ctlline:has-text("standard layout off")');
    await page.waitForSelector(nano + ' .ctlstd[aria-checked="false"]');
    assert.strictEqual(await page.textContent(nano + ' .ctl[data-id="fader1"] .ctlwhat'), 'Spare');
    assert.deepStrictEqual((await get('/api/midi')).controllers.map((c) => [c.name, c.standard]), [['nanoKONTROL2', false], ['keys', true]]);
    await page.click(nano + ' .ctlstd');
    await page.waitForSelector(nano + ' .ctlstd[aria-checked="true"]');
    fs.unlinkSync(midiPlug);                                                    // unplugged: the cards go
    await page.waitForFunction(() => document.querySelectorAll('.ctlcard').length === 0, null, { timeout: 15000 });
    await onPage('MIDI controller');
    await switchOff('MIDI controller');
    assert(!(await moduleIsOn('control-midi')), 'switching off switches the MIDI module off');
    await onPage('MIDI controller');
    // Streams: the index says Off before; switch the module on, reject a bad address, save one with a login (hidden),
    // remove it; the index says Set up after (on, but nothing saved)
    await sysIndex();
    await chip('Streams', 'Off');
    await sys('Streams');
    await switchOn('Streams');
    await page.waitForSelector('#streamempty');
    assert(/No streams yet\. A stream is live video/.test(await page.textContent('#streamempty')), 'the empty state says what a stream is');
    assert.strictEqual(await page.textContent('label[for="streamurl"]'), 'Address');
    assert.strictEqual(await page.textContent('#streamadd'), 'Add');
    await page.fill('#streamname', 'Cam');
    await page.fill('#streamurl', 'file:///etc/passwd');
    await page.click('#streamadd');
    await page.waitForFunction(() => /must start with/.test(document.getElementById('msg').textContent));
    assert(/must start with/.test(await page.textContent('#streamerr')), 'the refusal is under the Add button too');
    await page.fill('#streamurl', 'rtsp://admin:hunter2@10.0.0.5/live');
    await page.click('#streamadd');
    await page.waitForSelector('.stream-entry:has-text("rtsp://***@10.0.0.5/live")');
    assert(!(await page.textContent('body')).includes('hunter2'), 'stream password is never shown');
    await sysIndex();
    await chip('Streams', 'Ready');
    await sys('Streams');
    // The row: one primary action (Play) and More; Remove is under More and asks first, naming the stream
    await page.waitForSelector('.stream-entry .lacts');
    assert.deepStrictEqual(await page.$$eval('.stream-entry .lacts button', (bs) => bs.map((b) => b.textContent)), ['Play', 'More'], 'a stream row has Play and More');
    assert.strictEqual(await page.locator('#streamopen').count(), 1, 'with a stream saved the Add form is folded behind "+ Add a stream"');
    await page.click('.stream-entry .morebtn');
    await page.click('.stream-entry .moreacts button:has-text("Remove")');
    await page.waitForSelector('#confirmrow:has-text("Remove Cam? Its address is forgotten.")');
    await page.click('#confirmno');
    assert.strictEqual((await get('/api/streams')).streams.length, 1, '"Keep it" removes nothing');
    await page.click('.stream-entry .moreacts button:has-text("Remove")');
    await page.click('#confirmyes');
    await page.waitForSelector('#streamempty');
    assert(await page.isVisible('#streamname'), 'an empty list has the Add form open');
    await onPage('Streams');
    await sysIndex();
    await chip('Streams', 'Set up');
    // Schedule: switch the module on, add an entry, turn the schedule on and off, remove the entry
    await sys('Schedule');
    await switchOn('Schedule');
    await page.waitForSelector('#schedclock');
    const schedRow = (n) => `.sched-entry >> nth=${n}`;
    const schedNames = () => page.$$eval('.sched-entry .lname', (ns) => ns.map((x) => x.textContent));
    const schedDays = () => page.$$eval('#scheddays button[aria-pressed="true"]', (bs) => bs.map((b) => b.textContent));
    async function removeEntry(n) {
      await page.click(`${schedRow(n)} >> .morebtn`);
      await page.click(`${schedRow(n)} >> button:text-is("Remove")`);
      await page.waitForSelector('#confirmrow');
      await page.click('#confirmyes');
    }
    assert(/^Box time now: (Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d+ [A-Z][a-z]{2}, \d\d:\d\d \(.+\)$/.test(await page.textContent('#schedclock')), 'the box time, readable, with its zone: ' + await page.textContent('#schedclock'));
    assert(/^No entries yet\. An entry makes something happen by itself at a set time/.test(await page.textContent('#schedempty')), 'the empty state says what an entry is for');
    for (const id of ['schedtime', 'schedaction', 'schedlabel']) assert(await page.isVisible(`label[for="${id}"]`), `a visible label above #${id}`);
    assert(/An entry runs only if the box is on at that minute\. A missed entry is not caught up\./.test(await page.textContent('#schedform')), 'the form says what happens to a missed entry');
    // What happens, in plain words; a choice whose feature is off is marked; the old start script is under Advanced
    assert.deepStrictEqual(await page.$$eval('#schedaction option', (os) => os.map((o) => o.textContent)),
      ['Play a clip', 'Start Vibes (Vibes is off)', 'Apply a Room scene (Room is off)', 'Projectors on (Projectors is off)', 'Projectors off (Projectors is off)',
        'Screen to black', 'Screen back on', 'Stop playing', 'Old start script'], 'the choices of the schedule');
    assert.strictEqual(await page.locator('#schedaction optgroup[label="Advanced"] option[value="preset"]').count(), 1, 'the old start script is under Advanced');
    // Choosing one that is off says so and has the switch right there; it is switched on without leaving the page
    await page.selectOption('#schedaction', 'vibes');
    assert(/Vibes is switched off, so this will do nothing\./.test(await page.textContent('#schedoff')), 'choosing Vibes while it is off says so');
    assert.strictEqual(await page.textContent('#schedoffon'), 'Switch Vibes on');
    await page.click('#schedoffon');
    await page.waitForFunction(() => !document.getElementById('schedoff') && /Vibes is switched on/.test(document.getElementById('msg').textContent));
    assert(await moduleIsOn('shaders'), 'the button switched Vibes on in place');
    assert.strictEqual(await page.textContent('#schedaction option[value="vibes"]'), 'Start Vibes', 'the mark is gone once it is on');
    assert.strictEqual(await page.textContent('#syspage h1'), 'Schedule', 'still on the Schedule page');
    // Days: three shortcuts above seven chips
    await page.click('#schedshort button:text-is("Weekdays")');
    assert.deepStrictEqual(await schedDays(), ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'], 'Weekdays');
    await page.click('#schedshort button:text-is("Weekend")');
    assert.deepStrictEqual(await schedDays(), ['Sat', 'Sun'], 'Weekend');
    await page.click('#schedshort button:text-is("Every day")');
    assert.strictEqual((await schedDays()).length, 7, 'Every day');
    await page.selectOption('#schedaction', 'stop');
    await page.fill('#schedlabel', 'Close');
    await page.click('#schedadd');
    await page.waitForSelector('.sched-entry:has-text("18:00 \u00b7 Stop playing")');
    assert.strictEqual(await page.textContent('.sched-entry .state'), 'Every day \u00b7 Close', 'the days in words, then the note');
    await page.waitForSelector('#schednext');
    assert(/^Next: (today|tomorrow) 18:00, Stop playing$/.test(await page.textContent('#schednext')), 'what happens next: ' + await page.textContent('#schednext'));
    assert.strictEqual(await page.locator('#schedtoggle').count(), 0, 'no second switch inside the Schedule card');
    let sched = await get('/api/schedule');
    assert(sched.enabled === true && sched.entries.length === 1, 'with no entries the switch turned the schedule on without asking, and the entry was added');
    // Off: only the schedule's own flag; the module stays on and the entries are kept
    await switchOff('Schedule');
    sched = await get('/api/schedule');
    assert(sched.enabled === false && sched.entries.length === 1 && await moduleIsOn('scheduler'), 'switching off keeps the module on and the entries saved');
    await sysIndex();
    await chip('Schedule', 'Off');
    await page.waitForSelector(`${rowOf('Schedule')} .navstate:has-text("1 entry saved, not running")`);
    // On with a saved entry: it would start running at once, so the switch asks first, in place
    await sys('Schedule');
    await page.click('#sysswitchon');
    await page.waitForSelector('#confirmrow:has-text("Switch the schedule on? 1 entry will start running at its time.")');
    await fitsPhone('Schedule page, asking');
    await page.click('#confirmno');
    await page.waitForSelector('#sysswitchon');
    assert.strictEqual(await page.locator('#confirmrow').count(), 0, 'the question is gone after "Not yet"');
    assert.strictEqual((await get('/api/schedule')).enabled, false, '"Not yet" leaves the schedule off');
    await page.click('#sysswitchon');
    await page.click('#confirmyes');
    await page.waitForSelector('#sysswitch[aria-checked="true"]');
    sched = await get('/api/schedule');
    assert(sched.enabled === true && sched.entries.length === 1 && sched.entries[0].label === 'Close', 'switching on kept the saved entry');
    await sysIndex();
    await chip('Schedule', 'Ready');
    await page.waitForSelector(`${rowOf('Schedule')} .navstate:has-text("18:00 Stop")`);    // the row shows the next one
    await sys('Schedule');
    // A second entry, earlier in the day: the list is in time order, and its days read "Mon to Fri"
    await page.waitForSelector('.sched-entry');
    await page.click('#schedopen');
    await page.fill('#schedtime', '09:30');
    await page.click('#schedshort button:text-is("Weekdays")');
    await page.selectOption('#schedaction', 'blackout');
    await page.click('#schedadd');
    await page.waitForFunction(() => document.querySelectorAll('.sched-entry').length === 2);
    assert.deepStrictEqual(await schedNames(), ['09:30 \u00b7 Screen to black', '18:00 \u00b7 Stop playing'], 'entries are sorted by time');
    assert.strictEqual(await page.textContent(`${schedRow(0)} >> .state`), 'Mon to Fri');
    // Edit: the same form, filled in. Saving changes that entry, keeps its id, and leaves the other one alone.
    const schedBefore = await get('/api/schedule');
    await page.click(`${schedRow(0)} >> .morebtn`);
    assert.deepStrictEqual(await page.$$eval('.sched-entry .moreacts button', (bs) => bs.map((b) => b.textContent)), ['Edit', 'Remove'], 'Edit and Remove are under More');
    await page.click(`${schedRow(0)} >> button:text-is("Edit")`);
    await page.waitForSelector('#schedformtitle:text-is("Change the entry")');
    assert.strictEqual(await page.inputValue('#schedtime'), '09:30', 'the form holds the entry\'s time');
    assert.strictEqual(await page.inputValue('#schedaction'), 'blackout', 'and what it does');
    assert.deepStrictEqual(await schedDays(), ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'], 'and its days');
    assert.strictEqual(await page.textContent('#schedadd'), 'Save changes');
    await page.fill('#schedtime', '19:15');
    await page.click('#scheddays button:text-is("Sat")');
    await page.selectOption('#schedaction', 'show');
    await page.click('#schedadd');
    await page.waitForSelector('.sched-entry:has-text("19:15 \u00b7 Screen back on")');
    const schedAfter = await get('/api/schedule');
    const wasEntry = schedBefore.entries.find((e) => e.action === 'blackout'), isEntry = schedAfter.entries.find((e) => e.action === 'show');
    assert(schedAfter.entries.length === 2 && isEntry && isEntry.id === wasEntry.id && isEntry.time === '19:15' && isEntry.days.join() === '0,1,2,3,4,5',
      'the edit changed that entry and kept its id: ' + JSON.stringify(schedAfter.entries));
    assert(schedAfter.entries.some((e) => e.label === 'Close' && e.action === 'stop' && e.time === '18:00'), 'the other entry is untouched by the edit');
    assert.deepStrictEqual(await schedNames(), ['18:00 \u00b7 Stop playing', '19:15 \u00b7 Screen back on'], 'the edited entry moved to its place in time order');
    assert.strictEqual(await page.textContent(`${schedRow(1)} >> .state`), 'Mon to Sat');
    assert.strictEqual(await page.textContent('#schedformtitle, #schedopen'), '+ Add an entry', 'the form is folded again after the edit');
    await onPage('Schedule');
    // A laptop: the list on the left, the form beside it, nothing sticking out
    await page.setViewportSize({ width: 1366, height: 800 });
    await page.waitForFunction(() => { const l = document.getElementById('schedlist'), f = document.getElementById('schedopen'); return l && f && f.getBoundingClientRect().left > l.getBoundingClientRect().right - 2; });
    await fitsCard('.card, #syspage', 'Schedule page on a laptop');
    assert.strictEqual(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, 'Schedule on a laptop is not wider than the window');
    await page.setViewportSize({ width: 390, height: 844 });
    // Remove is under More, asks first and names the entry; "Keep it" removes nothing
    await page.click(`${schedRow(1)} >> .morebtn`);
    await page.click(`${schedRow(1)} >> button:text-is("Remove")`);
    await page.waitForSelector('#confirmrow:has-text("Remove 19:15 Screen back on (Mon to Sat)? It will no longer happen.")');
    await page.click('#confirmno');
    assert.strictEqual((await get('/api/schedule')).entries.length, 2, '"Keep it" removes nothing');
    await page.click(`${schedRow(1)} >> button:text-is("Remove")`);
    await page.click('#confirmyes');
    await page.waitForFunction(() => document.querySelectorAll('.sched-entry').length === 1);
    await removeEntry(0);
    await page.waitForSelector('#schedempty');
    await page.selectOption('#schedaction', 'preset');
    await page.fill('#schedpreset', 'startlessonce01');
    await page.click('#schedadd');
    await page.waitForSelector('.sched-entry:has-text("Old start script startlessonce01")');
    await removeEntry(0);
    await page.waitForSelector('#schedempty');
    // Vibes was switched on from the schedule's form: off again, on its own page, as the rest of this test expects
    await sys('Shaders and Vibes');
    await switchOff('Shaders and Vibes');
    await sys('Schedule');
    await page.waitForSelector('#schedempty');
    await onPage('Schedule');
    // Projectors: a public address is refused; the harness's fake PJLink projector (loopback, allowed there only)
    // is added, says who it is, shows its state, lamp hours and a warning in its one state line, has ONE power button
    // that follows its state, takes an input, names for its inputs and a blanked picture
    await sys('Projectors');
    await switchOn('Projectors');
    await page.waitForSelector('#projline');
    assert.strictEqual(await page.textContent('#projline'), 'No projectors yet. On the projector, open its network menu and switch PJLink on. Then add it here.', 'the empty state says the first step');
    assert(!/Beamer/.test(await page.textContent('#sysbody')), 'no word about the old Beamer buttons');
    for (const id of ['projname', 'projhost', 'projport', 'projpw']) assert(await page.isVisible(`label[for="${id}"]`), `a visible label above #${id}`);
    assert.strictEqual(await page.textContent('#projadd'), 'Add');
    await page.fill('#projname', 'Main');
    await page.fill('#projhost', '8.8.8.8');
    await page.click('#projadd');
    await page.waitForFunction(() => /private/.test(document.getElementById('msg').textContent));
    assert(/private/.test(await page.textContent('#projerr')), 'the refusal is under the Add button too');
    await page.fill('#projhost', '127.0.0.1');
    await page.fill('#projport', String(info.projector_ports[0]));
    await page.fill('#projpw', 'secret1');
    await page.click('#projadd');
    await page.waitForSelector('.proj-entry:has-text("password set")');
    // The password is gone from the form (page.content() does not show what an input holds, so ask the input),
    // and no answer of the API carries it. With a projector in the list the form is behind "+ Add a projector".
    await page.click('#projopen');
    if (await page.inputValue('#projpw') !== '') problems.push('the projector password is still in the form');
    await page.click('#projcancel');
    await page.waitForSelector('#projopen');
    const told = await page.evaluate(() => Promise.all(['/api/projectors', '/api/health', '/api/status', '/api/modules'].map((u) => fetch(u).then((r) => r.text()))));
    if (told.join(' ').includes('secret1')) problems.push('the projector password came back from the API');
    if (!told[0].includes('"has_password": true') && !told[0].includes('"has_password":true')) problems.push('the projector list did not answer: ' + told[0].slice(0, 200));
    if ((await page.content()).includes('secret1')) problems.push('the projector password came back to the page');
    await page.waitForSelector('.proj-details:has-text("NXLX Test Works FP-1")', { timeout: 15000 });
    await page.waitForSelector('.proj-status:has-text("lamp 1234 h")', { timeout: 15000 });
    if (!/^On/.test(await page.textContent('.proj-status'))) problems.push('the projector status does not say On: ' + await page.textContent('.proj-status'));
    await page.waitForSelector('.proj-status .proj-warn:has-text("Warning: filter")');       // the warning is in the state line
    // One power button, from the state: it is on, so the button turns it off; the row has that and More, nothing else
    await page.waitForSelector('.proj-power:text-is("Turn off")');
    assert.deepStrictEqual(await page.$$eval('.proj-entry .lacts button', (bs) => bs.map((b) => b.textContent)), ['Turn off', 'More'], 'a projector row has one power button and More');
    // Inputs: a plain hint from the PJLink kind until somebody names them; the choice applies when made
    assert.deepStrictEqual(await page.$$eval('.proj-input option', (os) => os.map((o) => o.textContent)),
      ['Choose...', 'RGB 1 (computer, VGA)', 'Digital 1 (HDMI or DVI)', 'Digital 2 (HDMI or DVI)'], 'unnamed inputs say what kind of socket they are');
    assert(await page.isVisible('label[for^="projinput-"]'), 'the input choice has a visible label');
    await page.selectOption('.proj-input', '31');
    await page.waitForSelector('.proj-status:has-text("input Digital 1 (HDMI or DVI)")', { timeout: 15000 });
    if (await pjlink(info.projector_ports[0], 'secret1', 'INPT ?') !== '31') problems.push('the fake projector is not on input 31');
    // More: the secondary actions in plain words, Remove last
    await page.click('.proj-entry .morebtn');
    assert.deepStrictEqual(await page.$$eval('.proj-entry .moreacts button', (bs) => bs.map((b) => b.textContent)),
      ['Blank the picture', 'Mute the sound', 'Name the inputs', 'Check now', 'Read details again', 'Edit', 'Remove'], 'what is under More, in order');
    // Name the inputs: one row per input, one Save names; naming an input that is not in use does not switch to it
    await page.click('.proj-namebtn');
    await page.waitForSelector('#projnames');
    assert(/Naming an input does not switch the projector\./.test(await page.textContent('#projnames')), 'the naming panel says it does not switch');
    assert.strictEqual(await page.locator('.proj-nameinput').count(), 3, 'one field per input');
    assert(await page.isDisabled('#projnamessave'), 'no names to save yet');
    await page.fill('.proj-nameinput[data-code="32"]', 'Box');
    await page.fill('.proj-nameinput[data-code="31"]', 'Matrix');
    await page.click('#projnamessave');
    await page.waitForSelector('.proj-input option:has-text("Box (Digital 2)")', { state: 'attached', timeout: 15000 });
    await page.waitForSelector('.proj-status:has-text("input Matrix (Digital 1)")', { timeout: 15000 });
    if (await pjlink(info.projector_ports[0], 'secret1', 'INPT ?') !== '31') problems.push('naming an input switched the projector to it');
    assert.strictEqual(await page.locator('#projnames').count(), 0, 'the naming panel closes after Save names');
    // "Show" in the naming panel does switch, so the person can see which socket is which
    await page.click('.proj-entry .morebtn');
    await page.click('.proj-namebtn');
    await page.click('button[aria-label="Show Digital 2 (HDMI or DVI) on Main"]');
    await page.waitForSelector('.proj-status:has-text("input Box (Digital 2)")', { timeout: 15000 });
    if (await pjlink(info.projector_ports[0], 'secret1', 'INPT ?') !== '32') problems.push('Show did not switch the projector to that input');
    await page.click('button[aria-label="Show Digital 1 (HDMI or DVI) on Main"]');
    await page.waitForSelector('.proj-status:has-text("input Matrix (Digital 1)")', { timeout: 15000 });
    await page.click('#projnamescancel');
    await page.click('.proj-entry .morebtn');
    await page.click('button[aria-label="Check Main"]');          // a fresh status, so a wrong switch would show
    await page.waitForFunction(() => /Power: on/.test(document.querySelector('.proj-entry').textContent), null, { timeout: 15000 });
    await page.click('button[aria-label="Blank the picture on Main"]');
    await page.waitForSelector('.proj-status:has-text("picture muted")', { timeout: 15000 });
    await page.click('button[aria-label="Show the picture on Main"]');
    await page.waitForFunction(() => !/muted/.test(document.querySelector('.proj-status').textContent), null, { timeout: 15000 });
    await page.click('button[aria-label="Read the details of Main again"]');
    await page.waitForFunction(() => /Read details again: done/.test(document.getElementById('msg').textContent));
    // Turn off asks first, in place, naming the projector and what it costs; "Keep it on" sends nothing
    await page.click('.proj-power');
    await page.waitForSelector('#confirmrow:has-text("Turn off Main? It needs about a minute to cool before it can come on again.")');
    await page.click('#confirmno');
    if (await pjlink(info.projector_ports[0], 'secret1', 'POWR ?') !== '1') problems.push('"Keep it on" switched the projector off');
    await page.click('.proj-power');
    await page.click('#confirmyes');
    await page.waitForSelector('.proj-power:text-is("Turn on")', { timeout: 15000 });
    if (await pjlink(info.projector_ports[0], 'secret1', 'POWR ?') !== '0') problems.push('Turn off did not switch the projector off');
    assert(/\bon\b/.test(await page.getAttribute('.proj-power', 'class')), 'Turn on is the accent button');
    assert(/^Off/.test(await page.textContent('.proj-status')), 'the state line says Off');
    // Cooling down: this fake goes straight to standby, so the box's answer is given that state on its way to the
    // page. The button waits, disabled, and says so. (Warming up is the second fake projector, further down.)
    const cooling = async (route) => {
      if (route.request().method() !== 'GET') return route.continue();
      const res = await route.fetch(), d = await res.json();
      d.projectors.forEach((x) => { if (x.status && x.status.ok) x.status.power = 'cooling down'; });
      await route.fulfill({ response: res, json: d });
    };
    await page.route('**/api/projectors', cooling);
    await page.waitForSelector('.proj-power:text-is("Cooling down...")', { timeout: 15000 });
    assert(await page.isDisabled('.proj-power'), 'no power button to press while it cools');
    assert(/^Cooling down/.test(await page.textContent('.proj-status')));
    await page.unroute('**/api/projectors', cooling);
    await page.waitForSelector('.proj-power:text-is("Turn on")', { timeout: 15000 });
    await page.click('.proj-power');                                 // on needs no question
    await page.waitForSelector('.proj-power:text-is("Turn off")', { timeout: 15000 });
    if (await pjlink(info.projector_ports[0], 'secret1', 'POWR ?') !== '1') problems.push('Turn on did not switch the projector on');
    await page.waitForSelector('.proj-status:has-text("input Matrix (Digital 1)")', { timeout: 15000 });
    // Edit, in place: the add form filled in. Nothing to save until something changed; a refusal is said under the
    // button; the password is never in the form; a rename keeps the password and the input names.
    await page.click('button[aria-label="Edit Main"]');                 // More is still open
    await page.waitForSelector('#projedit');
    if (!(await page.isDisabled('#projeditsave'))) problems.push('Save changes can be pressed with nothing changed');
    if (await page.inputValue('#projeditpw') !== '') problems.push('the edit form has something in its password field');
    if (!/A password is set\. Type a new one to change it, or leave empty to keep it\./.test(await page.textContent('#projeditpwhint'))) problems.push('the edit form does not say that a password is set');
    for (const id of ['projeditname', 'projedithost', 'projeditport', 'projeditpw']) {
      if (!(await page.locator(`label[for="${id}"]`).isVisible())) problems.push(`no visible label above #${id}`);
    }
    if (await page.inputValue('#projedithost') !== '127.0.0.1' || await page.inputValue('#projeditport') !== String(info.projector_ports[0])) problems.push('the edit form is not filled in with the projector\'s address and port');
    if (await page.isVisible('#projeditmoved')) problems.push('the new-address hint shows before the address was changed');
    await page.fill('#projedithost', '8.8.8.8');
    if (await page.isDisabled('#projeditsave')) problems.push('Save changes stays disabled after a change');
    if (!/If the password belongs to the old projector only, remove it or type the new one\./.test(await page.textContent('#projeditmoved')) || !(await page.isVisible('#projeditmoved')))
      problems.push('a new address does not say that the stored password is used there');
    await page.click('#projeditsave');
    await page.waitForSelector('#projediterr:has-text("private network")');
    await page.fill('#projedithost', '127.0.0.1');
    if (!(await page.isDisabled('#projeditsave'))) problems.push('Save changes is enabled again although the address is back as it was');
    await page.fill('#projeditname', 'Main wall');
    await page.click('#projeditclear');                              // "Remove the password": the field is closed, and what was typed stays
    await page.waitForSelector('#projeditpwhint:has-text("will be removed")');
    if (!(await page.isDisabled('#projeditpw'))) problems.push('the password field is still open with Remove the password on');
    if (await page.inputValue('#projeditname') !== 'Main wall') problems.push('the name being typed was lost when the form was drawn again');
    await page.click('#projeditclear');
    await page.waitForSelector('#projeditpwhint:has-text("A password is set")');
    await page.click('#projeditsave');
    await page.waitForSelector('.proj-entry:has-text("Main wall")');
    if (await page.$('#projedit')) problems.push('the edit form is still open after saving');
    if (!/password set/.test(await page.textContent('.proj-entry'))) problems.push('a rename lost the projector password');
    if (await pjlink(info.projector_ports[0], 'secret1', 'INPT ?') !== '31') problems.push('the fake projector moved after a rename');
    await page.waitForSelector('.proj-status:has-text("input Matrix (Digital 1)")', { timeout: 15000 });      // the label is kept, and it still answers with the kept password
    await page.click('.proj-entry .morebtn');
    await page.click('button[aria-label="Check Main wall"]');
    await page.waitForFunction(() => /Power: on/.test(document.querySelector('.proj-entry').textContent), null, { timeout: 15000 });
    await page.click('button[aria-label="Edit Main wall"]');
    await page.fill('#projeditname', 'Main');
    await page.click('#projeditsave');
    await page.waitForSelector('.proj-entry .lname:text-is("Main")');
    if ((await page.content()).includes('secret1')) problems.push('the projector password came back to the page after an edit');
    await onPage('Projectors');
    await sysIndex();
    await chip('Projectors', 'Problem');                             // the projector's own warning reaches the index row
    await page.waitForSelector(`${rowOf('Projectors')} .navstate:has-text("Main has a warning")`);
    await sys('Health');
    await page.waitForSelector('#healthcard .item:has-text("Projector: Main"):has-text("lamp 1234 h"):has-text("Warning: filter")', { timeout: 15000 });
    await onPage('Health');
    await sys('Projectors');
    await page.waitForSelector('.proj-entry');
    if ((await page.content()).includes('secret1')) problems.push('the projector password came back to the page');
    if ((await page.evaluate(() => fetch('/api/projectors').then((r) => r.text()))).includes('secret1')) problems.push('the projector password came back from the API');
    // The room: the harness's second fake projector (in standby, slow to warm up), two groups, a scene tapped on the
    // Room screen, a wall's own buttons, All off with a second tap, what a guest and a presenter get, the schedule
    await page.click('#projopen');
    await page.fill('#projname', 'Painting');
    await page.fill('#projhost', '127.0.0.1');
    await page.fill('#projport', String(info.projector_ports[1]));
    await page.click('#projadd');
    await page.waitForFunction(() => document.querySelectorAll('.proj-entry').length === 2);
    await page.waitForFunction(() => document.querySelectorAll('.proj-input').length === 2, null, { timeout: 15000 });   // both input lists are read
    await sys('Room');
    await switchOn('Room');
    await page.waitForFunction(() => /^Room/.test(document.querySelector('nav').textContent));       // the tab is there at once
    await page.waitForSelector('#syspage #roomsetup #roomgname');    // the Room controls are on the page itself, under its switch
    assert.strictEqual(await page.locator('#syspage button:has-text("Open Room")').count(), 0, 'the Room page does not send anyone to another screen');
    assert.strictEqual(await page.locator('#msg').count(), 1, 'one message line on the Room page');
    await onPage('Room');
    await sysIndex();
    await chip('Room', 'Set up');
    await page.waitForSelector(`${rowOf('Room')} .navstate:has-text("No groups or scenes yet")`);
    await sys('Room');
    await page.click('nav >> text=Room');
    // The System page holds the same set-up card, so wait for the Room tab's own screen: typing into the page that is
    // about to be replaced lost the group's name (one CI run in three).
    await page.waitForSelector('#roomscreen.screen #roomsetup #roomgname');
    for (const [wall, member] of [['Main wall', 'Main'], ['Painting wall', 'Painting']]) {
      await page.fill('#roomgname', wall);
      await page.click(`#roomgmembers >> button:has-text("${member}")`);
      await page.click('#roomgsave');
      await page.waitForSelector(`.room-group:has-text("${wall}")`);
    }
    await page.waitForSelector('.room-group:has-text("Main wall") .room-state:has-text("On")', { timeout: 15000 });
    await page.waitForSelector('.room-group:has-text("Painting wall") .room-state:has-text("Off")', { timeout: 15000 });
    await page.fill('#roomsname', 'Console night');
    await page.selectOption('select[aria-label="Power of Main wall"]', 'on');
    await page.selectOption('select[aria-label="Source of Main wall"]', '32');
    await page.selectOption('select[aria-label="Sound of Main wall"]', 'mute');
    await page.selectOption('select[aria-label="Power of Painting wall"]', 'on');
    await page.selectOption('#roomsbox', 'file');
    await page.selectOption('#roomsfile', 'tunnel.mkv');
    await page.click('#roomssave');
    await page.waitForSelector('.room-scene:has-text("Console night")');
    await page.waitForSelector('.room-sitem:has-text("Main wall: on, Box, sound muted"):has-text("Box: play tunnel.mkv")');
    await fitsCard('#roomscreen .card', 'Room');
    const roomWide = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    if (roomWide > 1) problems.push('the Room screen is ' + roomWide + ' px wider than the phone');
    // Shaders and Vibes is off: the owner gets one line and the way to its page (Back returns to Room), no ambience button
    assert.strictEqual(await page.locator('#roomambience, #roomamb').count(), 0, 'no ambience control while Shaders and Vibes is off');
    assert.strictEqual(await page.textContent('#roomamboff .hint'), 'Ambience (Vibes) is switched off.');
    await page.click('#roomambopen');
    await page.waitForSelector('#syspage h1:text-is("Shaders and Vibes")');
    await page.click('#sysback');
    await page.waitForSelector('#roomscreen.screen #roomamboff');
    await page.click('.room-scene:has-text("Console night")');
    await page.waitForFunction(() => /Main wall: on, input Box, sound muted\. Painting wall: switching on \(warming up\)\. Box: playing tunnel\.mkv\./.test((document.getElementById('roomjob') || {}).textContent || ''), null, { timeout: 20000 });
    if (await pjlink(info.projector_ports[0], 'secret1', 'INPT ?') !== '32') problems.push('the scene did not switch the main wall to input 32');
    if (await pjlink(info.projector_ports[0], 'secret1', 'AVMT ?') !== '21') problems.push('the scene did not mute the sound of the main wall only');
    if (await pjlink(info.projector_ports[1], '', 'POWR ?') !== '3') problems.push('the scene did not switch the painting wall on');
    await page.waitForFunction(() => fetch('/api/status').then((r) => r.json()).then((d) => /tunnel/.test((d.player || {}).path || '')), null, { timeout: 8000 });
    await page.waitForSelector('.room-group:has-text("Painting wall") .room-state:has-text("Warming up")', { timeout: 15000 });
    await page.waitForSelector('.room-group:has-text("Main wall") .room-text:has-text("On · Box · sound muted")', { timeout: 15000 });
    await page.click('button[aria-label="Main wall: Matrix"]');
    await page.waitForSelector('.room-group:has-text("Main wall") .room-text:has-text("On · Matrix")', { timeout: 15000 });
    if (await pjlink(info.projector_ports[0], 'secret1', 'INPT ?') !== '31') problems.push('the source button did not switch the main wall to input 31');
    await page.click('button[aria-label="Main wall: Sound on"]');
    await page.waitForFunction(() => !/muted/.test(document.querySelector('.room-group .room-text').textContent), null, { timeout: 15000 });
    // A guest sees the state and no button that does anything; a presenter starts on the Room screen with the buttons
    const roomTokens = await page.evaluate(() => Promise.all(['view', 'live'].map((role) => fetch('/api/devices/invite', { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-PVJ-Request': '1' }, body: JSON.stringify({ name: 'room ' + role, role: role }) }).then((r) => r.json()).then((d) => d.token))));
    for (const [n, role] of [[0, 'view'], [1, 'live']]) {
      const roomCtx = await browser.newContext({ viewport: { width: 390, height: 844 } });
      const staff = await roomCtx.newPage();
      staff.on('console', (m) => { if (['error'].includes(m.type()) && !expected.test(m.text())) problems.push('room ' + role + ': ' + m.text()); });
      staff.on('pageerror', (e) => problems.push('room ' + role + ' pageerror: ' + e.message));
      await staff.goto(base + '/#token=' + roomTokens[n]);
      try {
        await staff.waitForSelector('#roomscreen .room-group:has-text("Painting wall") .room-state:has-text("Warming up")', { timeout: 15000 });
      } catch (e) {     // say what the device had on its screen, what the box answered, and any error of the page
        const seen = await staff.evaluate(() => fetch('/api/room').then((r) => r.text()).then((t) => ({
          nav: (document.querySelector('nav') || {}).textContent, screen: (document.getElementById('app') || {}).textContent.slice(0, 400), room: t.slice(0, 700) })));
        throw new Error('room ' + role + ': not on the Room screen with the painting wall warming up: ' + JSON.stringify(seen) + ' problems: ' + problems.join('; '));
      }
      assert(await staff.isDisabled('.room-scene') === (role === 'view'), role + ': the scene button is ' + (role === 'view' ? 'not ' : '') + 'for tapping');
      assert.strictEqual(await staff.locator('#roomalloff').count(), role === 'view' ? 0 : 1, role + ': All off');
      assert.strictEqual(await staff.locator('#roomviewonly').count(), role === 'view' ? 1 : 0, role + ': the view only note');
      assert.strictEqual(await staff.locator('#roomsetup').count(), 0, role + ': no set-up');
      assert.strictEqual(await staff.locator('#roomambience, #roomamboff').count(), 0, role + ': nothing about ambience while Shaders and Vibes is off');
      // Letting a guest in is on the Room screen itself for staff (a presenter), and not there for a guest
      assert.strictEqual(await staff.locator('#roomletin').count(), role === 'view' ? 0 : 1, role + ': Let someone in');
      if (role === 'live') {
        await staff.click('#roomletin summary');
        await staff.waitForSelector('#roomletin #newguest');
        assert.strictEqual(await staff.locator('#newpresenter, #show-pin, #printsheet').count(), 0, 'staff on the Room screen get the guest code only');
        await staff.waitForSelector('#roomletin #nocodes');
      }
      await roomCtx.close();
    }
    // All off: a double tap sends nothing (its second tap lands on the question, which is not taken yet); the
    // question names what happens; "Keep them on" puts the buttons back; the deliberate two steps switch off
    let groupPosts = 0;
    const countPosts = (r) => { if (r.url().endsWith('/api/room/group') && r.method() === 'POST') groupPosts++; };
    page.on('request', countPosts);
    await page.dblclick('#roomalloff');
    await page.waitForTimeout(700);
    assert.strictEqual(groupPosts, 0, 'a double tap on All off sent ' + groupPosts + ' request(s)');
    if (await pjlink(info.projector_ports[0], 'secret1', 'POWR ?') !== '1') problems.push('a double tap on All off switched a projector off');
    if (await page.locator('#roomallask').count()) await page.click('#roomallkeep');
    await page.waitForSelector('#roomalloff');
    // the same with the second tap exactly on "Turn off", at once: not taken
    await page.evaluate(() => { document.getElementById('roomalloff').click(); document.getElementById('roomalloffyes').click(); });
    await page.waitForSelector('#roomallask:has-text("Turn off all 2 projectors? They need about a minute to cool before they can come on again.")');
    await page.waitForTimeout(700);
    assert.strictEqual(groupPosts, 0, 'a tap on Turn off in the same moment as the question sent ' + groupPosts + ' request(s)');
    if (await pjlink(info.projector_ports[0], 'secret1', 'POWR ?') !== '1') problems.push('Turn off was taken in the same moment as the question');
    await fitsCard('#roomscreen .card', 'Room, All off asking');
    await page.click('#roomallkeep');
    await page.waitForSelector('#roomalloff');
    assert.strictEqual(await page.locator('#roomallask').count(), 0, 'Keep them on puts the buttons back');
    await page.click('#roomalloff');
    await page.waitForSelector('#roomalloffyes');
    await page.waitForTimeout(700);
    await page.click('#roomalloffyes');
    page.off('request', countPosts);
    assert.strictEqual(groupPosts, 1, 'the two deliberate steps send All off once');
    await page.waitForSelector('.room-group:has-text("Main wall") .room-state:has-text("Off")', { timeout: 15000 });
    if (await pjlink(info.projector_ports[0], 'secret1', 'POWR ?') !== '0') problems.push('All off did not switch the main wall off');
    if (shots) await page.screenshot({ path: path.join(shots, '8-room.png'), fullPage: true });
    await sysIndex();
    await chip('Room', 'Ready');
    await page.waitForSelector(`${rowOf('Room')} .navstate:has-text("2 groups, 1 scene")`);
    await sys('Schedule');
    await page.waitForSelector('#schedscene', { state: 'attached' });
    await page.selectOption('#schedaction', 'scene');
    await page.click('#schedadd');
    await page.waitForSelector('.sched-entry:has-text("Scene Console night")');
    await removeEntry(0);
    await page.waitForSelector('#schedempty');
    // A box whose clock was not set from the network says so on this page (this harness has no clock helper, so the
    // box's answer is given that on its way to the page)
    const noClock = async (route) => {
      const res = await route.fetch(), d = await res.json();
      d.clock = Object.assign({}, d.clock, { clock_from_network: false });
      await route.fulfill({ response: res, json: d });
    };
    await page.route('**/api/system', noClock);
    await sys('Schedule');
    await page.waitForSelector('#schedclockwarn:has-text("The box clock was not set from the network, so it may be wrong.")');
    await onPage('Schedule');
    await page.unroute('**/api/system', noClock);
    await page.click('nav >> text=Room');
    await page.click('button[aria-label="Remove scene Console night"]');
    await page.waitForSelector('#confirmrow:has-text("Remove the scene Console night?")');
    await page.click('#confirmyes');
    await page.waitForSelector('#roomnoscenes');
    await sys('Room');
    await page.click('#sysswitch');
    await page.waitForSelector('#sysoff');
    await page.waitForFunction(() => !/Room/.test(document.querySelector('nav').textContent));
    await sys('Projectors');
    // The second fake projector was switched on by the scene and is slow: it is still warming up, and says so on
    // a button that cannot be pressed. The first one was switched off by All off.
    const rowP = (name) => `.proj-entry:has(.lname:text-is("${name}"))`;
    await page.waitForSelector(`${rowP('Painting')} .proj-power:text-is("Warming up...")`, { timeout: 15000 });
    assert(await page.isDisabled(`${rowP('Painting')} .proj-power`), 'no power button to press while it warms up');
    await page.waitForSelector(`${rowP('Main')} .proj-power:text-is("Turn on")`, { timeout: 15000 });
    // All on and All off ask first and name the count
    await page.click('#projalloff');
    await page.waitForSelector('#confirmrow:has-text("Turn off all 2 projectors? They need about a minute to cool before they can come on again.")');
    await page.click('#confirmno');
    await page.click('#projallon');
    await page.waitForSelector('#confirmrow:has-text("Turn on all 2 projectors?")');
    await page.click('#confirmno');
    if (await pjlink(info.projector_ports[0], 'secret1', 'POWR ?') !== '0') problems.push('a question that was answered no switched a projector');
    // Laptop: the list and the Add form side by side, nothing sticking out
    await page.setViewportSize({ width: 1366, height: 800 });
    await page.waitForFunction(() => { const l = document.getElementById('projlist'), f = document.getElementById('projopen'); return l && f && f.getBoundingClientRect().left > l.getBoundingClientRect().right - 2; });
    await fitsCard('.card, #syspage', 'Projectors page on a laptop');
    assert.strictEqual(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, 'Projectors on a laptop is not wider than the window');
    await page.setViewportSize({ width: 390, height: 844 });
    // Remove is under More and asks first, naming the projector
    await page.click(`${rowP('Painting')} .morebtn`);
    await page.click('button[aria-label="Remove Painting"]');
    await page.waitForSelector('#confirmrow:has-text("Remove Painting? The box forgets its address, its password and the names of its inputs.")');
    await page.click('#confirmno');
    assert.strictEqual((await get('/api/projectors')).projectors.length, 2, '"Keep it" removes nothing');
    await page.click('button[aria-label="Remove Painting"]');
    await page.click('#confirmyes');
    await page.waitForFunction(() => document.querySelectorAll('.proj-entry').length === 1);
    // A projector that does not answer (nothing listens at this port): the row says what to check, and the one
    // button is Try again
    await page.click('#projopen');
    await page.fill('#projname', 'Ghost');
    await page.fill('#projhost', '127.0.0.1');
    await page.fill('#projport', '1');
    await page.click('#projadd');
    await page.waitForSelector(`${rowP('Ghost')} .problem`, { timeout: 20000 });
    assert(/^Not answering at 127\.0\.0\.1:1\. Is it plugged in at the wall, and is PJLink switched on in its network menu\?/.test(await page.textContent(`${rowP('Ghost')} .problem`)),
      'a silent projector says what to check: ' + await page.textContent(`${rowP('Ghost')} .problem`));
    assert.strictEqual(await page.textContent(`${rowP('Ghost')} .proj-power`), 'Try again');
    await page.click(`${rowP('Ghost')} .proj-power`);
    await page.waitForSelector(`${rowP('Ghost')} .problem`, { timeout: 20000 });
    await onPage('Projectors');
    await page.click(`${rowP('Ghost')} .morebtn`);
    await page.click('button[aria-label="Remove Ghost"]');
    await page.click('#confirmyes');
    await page.waitForFunction(() => document.querySelectorAll('.proj-entry').length === 1);
    await page.click('.proj-entry .morebtn');
    await page.click('button[aria-label="Remove Main"]');
    await page.click('#confirmyes');
    await page.waitForFunction(() => !document.querySelector('.proj-entry'));
    await page.waitForSelector('#projline:has-text("No projectors yet")');
    assert(await page.isVisible('#projname'), 'an empty list has the Add form open');
    // Sync and video wall: switch the module on, be a server, set a wall tile, back to off
    // This step failed twice in CI only. If it fails again it says what the form held, what the message was, what is
    // saved, and every /api/sync request the page made with its answer.
    const syncSeen = [];
    page.on('response', (r) => {
      if (!r.url().endsWith('/api/sync')) return;
      const q = r.request();
      r.text().catch(() => '(no body)').then((t) => {
        syncSeen.push(q.method() + ' ' + (q.postData() || '') + ' -> ' + r.status() + ' ' + t.slice(0, 160));
        if (syncSeen.length > 10) syncSeen.shift();
      });
    });
    const syncState = async () => {
      const form = await page.evaluate(() => {
        const v = (id) => { const el = document.getElementById(id); return el ? el.value : '(missing)'; };
        const m = document.getElementById('msg');
        return fetch('/api/sync').then((r) => r.json()).catch((x) => ({ config: 'fetch failed: ' + x.message })).then((d) => ({
          cols: v('wallcols'), rows: v('wallrows'), col: v('wallcol'), row: v('wallrow'), bezel: v('wallbezel'),
          msg: m ? m.textContent : '(missing)', saved: d.config }));
      }).catch((x) => 'evaluate failed: ' + x.message);
      return JSON.stringify({ form: form, requests: syncSeen });
    };
    await sys('Boxes in step');
    await switchOn('Boxes in step');
    await page.waitForSelector('#syncrole-server');
    // The first thing asked is what this box does: two large choices, and nothing else to fill in yet
    assert.strictEqual(await page.textContent('#syncask'), 'What does this box do?');
    assert.deepStrictEqual(await page.$$eval('#syncroles button', (bs) => bs.map((b) => b.textContent)), ['LeadOther boxes follow this one.', 'FollowThis box follows another.'], 'lead or follow');
    assert.strictEqual(await page.locator('#syncgroup, #wallfold').count(), 0, 'the group and the wall come after the choice');
    await page.click('#syncrole-server');
    await page.waitForSelector('#syncline:has-text("server of group")');
    await page.waitForSelector('label[for="syncgroup"]:text-is("Group name")');
    // The video wall is folded until it is used; a screen's place is offered only inside the chosen size
    assert(!(await page.isVisible('#wallcols')), 'the wall is folded while it is not in use');
    await page.click('#wallfold > summary');
    assert.strictEqual(await page.locator('#wallcol option').count(), 1, 'one column: one place to choose');
    await page.selectOption('#wallcols', '2');
    assert.strictEqual(await page.locator('#wallcol option').count(), 2, 'two columns: two places');
    await page.selectOption('#wallcol', '1');
    await page.fill('#wallbezel', '3');
    await page.click('#wallsave');
    try {
      await page.waitForFunction(() => fetch('/api/sync').then((r) => r.json()).then((d) => d.config.wall.cols === 2 && d.config.wall.col === 1 && d.config.wall.bezel === 3), null, { timeout: 15000 });
    } catch (e) { throw new Error(e.message.split('\n')[0] + ' | the wall was not saved: ' + await syncState()); }
    // The card is rebuilt by the answer to the save above and, while it is a server, every 2 seconds. A column chosen
    // just before such a rebuild must still be there at the click. It is set without an event (as the Network step
    // does), the line at the top of the card is marked, and the step waits until that line is a new one.
    await page.evaluate(() => { document.getElementById('syncline').dataset.seen = '1'; document.getElementById('wallcol').value = '0'; });
    await page.waitForFunction(() => { const l = document.getElementById('syncline'); return l && !l.dataset.seen; }, null, { timeout: 8000 });
    if (await page.evaluate(() => document.getElementById('wallcol').value) !== '0') throw new Error('a redraw of the Sync card lost the chosen column: ' + await syncState());
    assert(await page.isVisible('#wallcols'), 'a wall in use stays unfolded through a redraw');
    await page.click('#wallsave');
    try {
      await page.waitForFunction(() => fetch('/api/sync').then((r) => r.json()).then((d) => d.config.wall.col === 0), null, { timeout: 15000 });
    } catch (e) { throw new Error(e.message.split('\n')[0] + ' | the column was not saved: ' + await syncState()); }
    // a tile outside the wall can no longer be chosen in the panel; the box still refuses one
    assert.strictEqual(await post('/api/sync', { wall: { cols: 2, rows: 1, col: 2, row: 0, bezel: 3 } }), 400, 'the box refuses a tile outside the wall');
    // Stopping asks first: the other boxes stop following
    await page.click('#syncrole-off');
    await page.waitForSelector('#confirmrow:has-text("Stop leading? The other boxes stop following this one.")');
    await page.click('#confirmyes');
    await page.waitForSelector('#syncline:has-text("Off")');
    await fitsCard('#synccard', 'Sync card');
    await onPage('Boxes in step');
    // Projection mapping: switch the module on (its page says where the controls are and takes you there), add a
    // quad on Mix, drag and nudge a corner, save, switch it on
    await sys('Projection mapping');
    await switchOn('Projection mapping');
    await onPage('Projection mapping');
    assert.strictEqual(await page.locator('#syspage button:has-text("Open Mix")').count(), 0, 'the mapping page does not send anyone to Mix');
    await page.waitForSelector('#syspage #mapadd-quad');             // the controls are on the page itself
    await page.click('#mapadd-quad');
    await page.waitForSelector('.map-entry:has-text("Quad")');
    await page.waitForSelector('#mapsel:has-text("corner 1 of 4")');
    const box = await page.locator('#mapcanvas').boundingBox();
    assert(box && box.width > 200 && box.height > 100, 'the mapping canvas is drawn');
    const before = await page.evaluate(() => fetch('/api/mapper').then((r) => r.json()).then((d) => d.surfaces[0].vertices[0]));
    // the new quad's first corner is at a quarter of the screen: drag it towards the top left
    await page.mouse.move(box.x + box.width / 4, box.y + box.height / 4);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width / 8, box.y + box.height / 8, { steps: 5 });
    await page.mouse.up();
    await page.waitForFunction((b) => fetch('/api/mapper').then((r) => r.json()).then((d) => d.surfaces[0].vertices[0][0] < b[0] - 100), before);
    await page.selectOption('#mapstep', '50');
    const x0 = await page.evaluate(() => fetch('/api/mapper').then((r) => r.json()).then((d) => d.surfaces[0].vertices[0][0]));
    // The card is drawn again by the answer to every change. drawnAgain() marks the line at its top and waits until
    // that line is a new one (as the Sync step does): the step goes on from what the panel shows, not from what
    // the box already knows. (The box knows a change a few milliseconds before its answer is back.)
    const markMap = () => page.evaluate(() => { document.getElementById('mapstatus').dataset.seen = '1'; });
    const drawnAgain = () => page.waitForFunction(() => { const l = document.getElementById('mapstatus'); return l && !l.dataset.seen; }, null, { timeout: 8000 });
    await markMap();
    await page.click('#mapright');
    await page.waitForFunction((x) => fetch('/api/mapper').then((r) => r.json()).then((d) => Math.abs(d.surfaces[0].vertices[0][0] - (x + 50)) < 0.01), x0);
    await drawnAgain();
    // A name is typed while the card is drawn again. This is the failure that came and went here ("Main stage" not
    // seen in 30 s): the answer to the nudge arrived between the moment the name field was chosen and the typing,
    // the card was rebuilt, the letters went nowhere and Save sent an empty name, which the box refused. Here the
    // answer is held back for a moment, as on a slow link, so it happens every time: the field must keep the
    // cursor, the letters typed so far and the place in them, and take the rest.
    let holdBack = true;
    const slowAnswer = async (route) => {
      if (holdBack && route.request().method() === 'POST') await new Promise((r) => setTimeout(r, 700));
      await route.continue();
    };
    await page.route('**/api/mapper', slowAnswer);
    await markMap();
    await page.click('#mapleft');                 // back to where it was; its answer comes 700 ms later
    await page.focus('#mapsetname');
    await page.keyboard.type('Min');
    await page.keyboard.press('ArrowLeft');
    await page.keyboard.press('ArrowLeft');       // the cursor is after the M
    await drawnAgain();
    const typing = await page.evaluate(() => { const a = document.activeElement, f = document.getElementById('mapsetname'); return { cursorIn: a ? a.id : '', text: f ? f.value : null, at: f ? f.selectionStart : null }; });
    assert.deepStrictEqual(typing, { cursorIn: 'mapsetname', text: 'Min', at: 1 }, 'a redraw of the mapping card took #mapsetname away from the person typing in it: ' + JSON.stringify(typing));
    await page.keyboard.type('a');
    await page.keyboard.press('End');
    await page.keyboard.type(' stage');
    holdBack = false;
    await page.unroute('**/api/mapper', slowAnswer);
    assert.strictEqual(await page.inputValue('#mapsetname'), 'Main stage', 'what was typed across the redraw is all there');
    await page.waitForFunction((x) => fetch('/api/mapper').then((r) => r.json()).then((d) => Math.abs(d.surfaces[0].vertices[0][0] - x) < 0.01), x0);     // the nudge back was taken
    // Save with the cursor left in the name field, as on Safari and iOS, where a tapped button does not take it
    // (Chromium's click would): the button's own click() presses it and moves nothing.
    const press = (id) => page.evaluate((x) => document.getElementById(x).click(), id);
    const nameField = () => page.evaluate(() => { const a = document.activeElement, f = document.getElementById('mapsetname'); return { cursorIn: a ? a.id : '', text: f ? f.value : null }; });
    await press('mapsave');
    await fitsCard('#mapcard', 'Mapping card');
    await page.waitForSelector('#mapsets >> option:has-text("Main stage")', { state: 'attached' });
    // Review of #86, finding 2b. The saved name is gone from the field, and stays gone through later redraws: put
    // back by a redraw it would be shown while a second Save sent an empty name, which the box refuses.
    assert.deepStrictEqual(await nameField(), { cursorIn: 'mapsetname', text: '' }, 'after Save the name field #mapsetname is empty and still has the cursor: ' + JSON.stringify(await nameField()));
    for (const arrow of ['mapright', 'mapleft']) {
      await markMap();
      await press(arrow);
      await drawnAgain();
      assert.deepStrictEqual(await nameField(), { cursorIn: 'mapsetname', text: '' }, 'a redraw after Save does not put the saved name back into #mapsetname: ' + JSON.stringify(await nameField()));
    }
    // Finding 2a. The surface's name field belongs to the chosen surface. Another surface is chosen by an answer
    // that arrives while a new name is being typed (held back here; it could as well come from another phone):
    // the field then holds the new surface's name, never the text typed for the old one, or Rename would give
    // that text to the wrong surface. The cursor stays.
    holdBack = true;
    await page.route('**/api/mapper', slowAnswer);
    await markMap();
    await press('mapadd-triangle');               // its answer, 700 ms later, chooses the new triangle
    await page.focus('#mapname');
    await page.keyboard.press('End');
    await page.keyboard.type(' on the left');
    assert.strictEqual(await page.inputValue('#mapname'), 'Quad on the left');
    await drawnAgain();
    holdBack = false;
    await page.unroute('**/api/mapper', slowAnswer);
    const chosen = await page.evaluate(() => fetch('/api/mapper').then((r) => r.json()).then((d) => d.surfaces.filter((x) => x.id === d.edit.selected)[0]));
    assert.strictEqual(chosen.type, 'triangle', 'the new triangle is the chosen surface');
    const surfaceName = await page.evaluate(() => { const a = document.activeElement, f = document.getElementById('mapname'); return { cursorIn: a ? a.id : '', text: f ? f.value : null }; });
    assert.deepStrictEqual(surfaceName, { cursorIn: 'mapname', text: chosen.name }, 'the name field #mapname shows the surface that is chosen now, not what was typed for the one before: ' + JSON.stringify(surfaceName));
    assert.deepStrictEqual((await get('/api/mapper')).surfaces.map((x) => x.name).sort(), ['Quad', chosen.name].sort(), 'nothing was renamed');
    await page.click(`.map-entry:has-text("${chosen.name}") >> button:has-text("Remove")`);
    await page.waitForFunction(() => document.querySelectorAll('.map-entry').length === 1);
    await page.waitForSelector('.map-entry:has-text("Quad")');
    await page.click('#mapon');
    await page.waitForSelector('#mapstatus:has-text("Mapping is on")', { timeout: 20000 });
    if (shots) await page.screenshot({ path: path.join(shots, '7-mapper.png'), fullPage: true });
    await page.click('#mapon');
    await page.waitForSelector('#mapstatus:has-text("Mapping is off")');
    await page.click('.map-entry >> button:has-text("Remove")');
    await page.waitForFunction(() => !document.querySelector('.map-entry'));
    await sys('Remote support');
    // Remote support: off by default; settings saved and checked; allowing it shows the start controls
    await page.waitForSelector('#supportcard #supportline');
    assert(/Remote support is off/.test(await page.textContent('#supportcard')), 'remote support starts off');
    // What staff see first is the state; the support server is under Advanced
    assert(!(await page.isVisible('#support-endpoint')), 'the support server fields are folded');
    await page.click('#supportadv > summary');
    for (const id of ['support-endpoint', 'support-server_key', 'support-address', 'support-network']) assert(await page.isVisible(`label[for="${id}"]`), `a visible label above #${id}`);
    assert(await page.isDisabled('#supportsave'), 'nothing to save until something is typed');
    assert.strictEqual(await page.getAttribute('#sysswitch', 'aria-checked'), 'false', 'and its switch is the page switch');
    assert.strictEqual(await page.locator('#supportallow').count(), 0, 'no second switch inside the Remote support card');
    await page.fill('#support-endpoint', 'support.example.com:51820');
    await page.fill('#support-server_key', 'a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=');
    await page.fill('#support-address', '10.77.0.40');
    await page.fill('#support-network', '10.77.0.0/24');
    await page.click('#supportsave');
    await page.waitForFunction(() => fetch('/api/support').then((r) => r.json()).then((d) => d.config.address === '10.77.0.40'));
    await page.click('#sysswitch');
    await page.waitForSelector('#sysswitch[aria-checked="true"]');
    assert.strictEqual((await get('/api/support')).config.allowed, true, 'the page switch allowed remote support');
    await page.waitForSelector('#supportstart');
    assert(await page.isVisible('label[for="supportminutes"]') && await page.isVisible('label[for="supportrole"]'), 'how long and what support may do have labels');
    assert(await page.isVisible('#support-address'), 'Advanced stays open through a redraw');
    await page.waitForSelector('#supportwhy');                       // no helper in the test harness: said plainly
    await page.fill('#support-address', '10.99.0.40');
    await page.click('#supportsave');
    await page.waitForFunction(() => /inside the support network/.test(document.getElementById('msg').textContent));
    await page.click('#sysswitch');
    await page.waitForSelector('#sysswitch[aria-checked="false"]');
    await page.waitForFunction(() => /Remote support is off/.test(document.getElementById('supportcard').textContent));
    assert.strictEqual((await get('/api/support')).config.allowed, false, 'the page switch turned remote support off');
    await onPage('Remote support');
    // The Look page draws each look as a small picture in that look's own type, so it is the one place where the
    // default look asks the box for the two shipped fonts (D60). Nowhere before it, and never from anywhere else.
    assert.deepStrictEqual(fontRequests, [], 'a font file was fetched before the Look page was opened: ' + fontRequests.join(', '));
    await sys('Look');
    await page.waitForSelector('#lookthemes .looktile[data-theme="signal"] .lt-title');
    assert.deepStrictEqual(await page.$$eval('#lookthemes .looktile', (ts) => ts.map((t) => t.getAttribute('data-theme')).sort()),
      ['dark-stage', 'high-contrast', 'light', 'night-red', 'signal', 'signal-light'], 'every look has its picture');
    await page.click('button:has-text("Night red")');
    await page.waitForFunction(() => getComputedStyle(document.body).backgroundColor === 'rgb(0, 0, 0)');
    await onPage('Look');
    await sys('People and codes');
    assert.deepStrictEqual(await page.$$eval('#linkrole option', (os) => os.map((o) => o.textContent)), ['Guest (can watch)', 'Presenter (can play and mix)'], 'the link roles, in the one vocabulary');
    assert(/Owner \(everything\)/.test(await page.textContent('#devicescard')), 'this device is named Owner (everything) in the list');
    assert(!/watch only|View only|\(play and mix\)/.test(await page.textContent('#sysbody')), 'no other names for the roles on People and codes: ' + await page.textContent('#sysbody'));
    // A code from a controller (D61): off on a new box; a question in place before it goes on; the full access kind is
    // a second switch that is only there once the first is on; off applies at once and takes the second with it. The
    // card never holds a code: only a hold on a controller makes one, and this test has no controller on this page.
    await page.waitForSelector('#ctlcodecard #ctlcode-on');
    assert.strictEqual(await page.getAttribute('#ctlcode-on', 'aria-checked'), 'false', 'codes from a controller are off on a new box');
    assert.strictEqual(await page.locator('#ctlcode-owner, #ctlcodeshown, #ctlcodeend').count(), 0, 'no second switch and no code while it is off');
    await page.click('#ctlcode-on');
    await page.waitForSelector('#ctlcodecard #confirmrow');
    assert.strictEqual((await get('/api/access')).controller.enabled, false, 'nothing is switched on before the question is answered');
    await page.click('#confirmno');
    await page.waitForFunction(() => document.getElementById('ctlcode-on').getAttribute('aria-checked') === 'false' && !document.getElementById('confirmrow'));
    await page.click('#ctlcode-on');
    await page.click('#confirmyes');
    await page.waitForSelector('#ctlcode-owner');
    assert.deepStrictEqual(await get('/api/access').then((a) => [a.controller.enabled, a.controller.owner, a.controller.status.active]), [true, false, false], 'on, without the full access kind');
    assert(/No code has been shown from a controller/.test(await page.textContent('#ctlcodelast')), 'the card says nothing was shown yet');
    await page.click('#ctlcode-owner');
    await page.waitForSelector('#ctlcodecard #confirmrow');
    assert(/everything allowed/.test(await page.textContent('#confirmrow')), 'the question says what a full access code gives');
    await page.click('#confirmyes');
    await page.waitForFunction(() => { const s = document.getElementById('ctlcode-owner'); return s && s.getAttribute('aria-checked') === 'true'; });
    assert.strictEqual((await get('/api/access')).controller.owner, true, 'the full access kind is allowed');
    assert.strictEqual(await post('/api/access/controller', { kind: 'owner' }), 400, 'no request makes a code');
    assert.strictEqual(await post('/api/access/controller', { cancel: true }), 409, 'and there is none to end');
    assert(!/[0-9]{6}/.test(await page.textContent('#ctlcodecard')), 'no digits on the card');
    await page.click('#ctlcode-on');                                  // off: at once, no question
    await page.waitForFunction(() => !document.getElementById('ctlcode-owner') && document.getElementById('ctlcode-on').getAttribute('aria-checked') === 'false');
    assert.deepStrictEqual(await get('/api/access').then((a) => [a.controller.enabled, a.controller.owner]), [false, false], 'off takes the full access kind off with it');
    await page.click('#makelink');
    await page.waitForFunction(() => { const i = document.querySelector('input[aria-label="Link"]'); return i && !i.hidden && /#token=/.test(i.value); });
    const guestLink = await page.inputValue('input[aria-label="Link"]');
    await onPage('People and codes');
    await sys('Sound');
    await page.waitForSelector('#audioline, #audiomsg');
    await onPage('Sound');
    await sys('Updates');
    await page.waitForSelector('#updateversion');
    await onPage('Updates');
    await sys('About and power');
    await page.waitForFunction(() => !/Loading/.test(document.getElementById('boxbody').textContent));
    await onPage('About and power');
    await sysIndex();
    await fitsCard('.card', 'System index, modules on');
    if (shots) await page.screenshot({ path: path.join(shots, '4-system.png'), fullPage: true });

    // Box care: export the settings (no access data in the file), change something, import the file back; a file
    // with a repeated key is refused; diagnostics downloads; factory reset wants a choice and can be cancelled
    // (a real reset would unpair this test's own device, so it is covered by tests/test_boxcare.py)
    await sys('Backup and reset');
    await page.waitForSelector('#settingscard #exportbtn');
    assert.deepStrictEqual(await page.$$eval('#sysbody > *', (els) => els.map((e) => e.id)), ['settingscard', 'diagcard', 'dangerhead', 'resetcard'], 'reset is last, under Danger');
    const [exported] = await Promise.all([page.waitForEvent('download'), page.click('#exportbtn')]);
    assert(/^nxlx-settings-.+\.json$/.test(exported.suggestedFilename()), 'export file name: ' + exported.suggestedFilename());
    const exportedText = require('fs').readFileSync(await exported.path(), 'utf8');
    const exportedFile = JSON.parse(exportedText);
    assert.strictEqual(exportedFile.passwords_included, false, 'no passwords unless ticked');
    for (const k of ['auth', 'devices', 'support', 'support_log']) assert(!(k in exportedFile.settings), k + ' must not be exported');
    assert(!/token|pin_hash|a2tra2tr/.test(exportedText), 'no access data or support key in the export');
    const otherDuration = exportedFile.settings.mix.duration === 3 ? 4 : 3;
    assert.strictEqual(await post('/api/mix', { transition: exportedFile.settings.mix.transition, duration: otherDuration }), 200);
    await page.setInputFiles('#importpick', { name: 'my-settings.json', mimeType: 'application/json', buffer: Buffer.from(exportedText) });
    await page.waitForSelector('#confirmrow:has-text("Replace this box\'s settings with my-settings.json?")');
    await page.click('#confirmyes');
    await page.waitForFunction(() => /Settings imported/.test(document.getElementById('msg').textContent));
    await page.waitForFunction(() => /no passwords/.test(document.getElementById('importresult').textContent));
    const mixNow = await page.evaluate(() => fetch('/api/status').then((r) => r.json()).then((j) => j.mix.duration));
    assert.strictEqual(mixNow, exportedFile.settings.mix.duration, 'the import put the exported value back');
    assert.strictEqual(await page.evaluate(() => fetch('/api/status').then((r) => r.status)), 200, 'still paired after an import');
    await page.waitForSelector('#importbtn');
    await page.setInputFiles('#importpick', { name: 'twice.json', mimeType: 'application/json', buffer: Buffer.from(exportedText.replace('{', '{"format": "x", ')) });
    await page.click('#confirmyes');
    await page.waitForFunction(() => /appears twice/.test(document.getElementById('msg').textContent));
    const [diag] = await Promise.all([page.waitForEvent('download'), page.click('#diagbtn')]);
    assert(/^nxlx-diagnostics-.+\.json$/.test(diag.suggestedFilename()), 'diagnostics file name');
    const diagText = require('fs').readFileSync(await diag.path(), 'utf8');
    const diagFile = JSON.parse(diagText);
    assert(diagFile.version && diagFile.health && diagFile.settings && diagFile.log, 'diagnostics has its parts');
    assert(!/token_hash|pin_hash|pin_salt|server_key|a2tra2tr/.test(diagText) && !diagText.includes(info.pin + ' ('), 'no secret in the diagnostics file');
    await page.waitForFunction(() => /^Log: /.test(document.getElementById('diagnote').textContent));
    let resets = 0;
    page.on('request', (r) => { if (r.url().endsWith('/api/system/factory-reset')) resets++; });
    await page.click('#resetbtn');
    await page.waitForFunction(() => /Choose what happens to the clips/.test(document.getElementById('msg').textContent));
    await page.selectOption('#resetmedia', 'keep');
    await page.click('#resetbtn');
    await page.waitForSelector('#confirmrow');
    const asked = await page.textContent('#confirmrow > span');
    await page.click('#confirmno');
    await page.waitForSelector('#resetbtn');
    assert.strictEqual(await page.inputValue('#resetmedia'), 'keep');
    assert(/every device is unpaired/.test(asked) && /The clips stay/.test(asked), 'the reset question says what it does: ' + asked);
    assert.strictEqual(resets, 0, 'a cancelled reset sends nothing');
    await fitsCard('#settingscard, #diagcard, #resetcard', 'Box care');
    await onPage('Backup and reset');

    // Guest (view only) via the link in a fresh context
    const guestCtx = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const guest = await guestCtx.newPage();
    guest.on('console', (m) => { if (['error'].includes(m.type()) && !expected.test(m.text())) problems.push('guest: ' + m.text()); });
    await guest.goto(guestLink.replace(/^https?:\/\/[^/]+/, base));
    await guest.waitForSelector('.pads');
    assert(await guest.isDisabled('#black'), 'view-only guest cannot blackout');
    assert(await guest.isDisabled('#stop'), 'view-only guest cannot stop');
    assert(!(await guest.isVisible('text=Edit pads')), 'view-only guest cannot edit pads');
    assert.strictEqual(await guest.evaluate(() => location.hash), '', 'token removed from the URL');
    // A guest's System has only what a guest can use: no disabled lists
    await guest.click('nav >> text=System');
    await guest.waitForSelector('#sysindex');
    assert.deepStrictEqual(await guest.$$eval('.navname', (ns) => ns.map((x) => x.textContent)), ['Health', 'About and power'], 'the rows a guest sees');
    assert.strictEqual(await guest.locator('#notbuilt').count(), 0, 'a guest gets no list of modules');
    await guest.click('.navrow:has-text("About and power")');
    await guest.waitForSelector('#boxcard');
    assert.strictEqual(await guest.locator('#syspage button:disabled').count(), 0, 'nothing disabled on a guest page');
    // A guest's two pages, on a phone and on a laptop: they fit, there is no power action, and the one button asks first
    for (const width of [390, 1366]) {
      await guest.setViewportSize({ width, height: 844 });
      await guest.waitForSelector('#boxcard .kvv');
      await fitsOn(guest, "a guest's About and power page at " + width);
      await guest.click('#sysback');
      await guest.click('.navrow:has-text("Health")');
      await guest.waitForSelector('#healthpower');
      await fitsOn(guest, "a guest's Health page at " + width);
      assert.strictEqual(await guest.locator('#healthshowaddr, #sysswitch').count(), 0, 'nothing to press on a guest\'s Health page');
      await guest.click('#sysback');
      await guest.click('.navrow:has-text("About and power")');
    }
    await guest.setViewportSize({ width: 390, height: 844 });
    await guest.waitForSelector('#boxcard .kvv');
    assert.strictEqual(await guest.locator('#powercard, #sysswitch, #setclock, #rebootbtn, #poweroffbtn, #restartplayer').count(), 0, 'a guest gets no power action');
    assert(!/Vitals/.test(await guest.textContent('#sysbody')), 'no second card repeating the board and the player');
    await guest.click('#forgetdevice');
    await guest.waitForSelector('#confirmrow:has-text("Leave this panel on this phone? You will need a code or the PIN to get back in.")');
    await guest.click('#confirmno');
    await guest.waitForSelector('#forgetdevice');

    // Scan-to-join: the owner makes a guest code; a phone opens the QR code's link and joins with one tap as view only
    await sys('People and codes');
    await page.waitForSelector('#newguest');
    assert.deepStrictEqual(await page.$$eval('#joinminutes option', (os) => os.map((o) => o.textContent)), ['15 minutes', '1 hour', '2 hours'], 'how long a new code works');
    assert.strictEqual(await page.inputValue('#joinminutes'), '60', 'one hour unless chosen otherwise');
    await page.selectOption('#joinminutes', '15');
    const [codeReq] = await Promise.all([page.waitForRequest((r) => r.url().endsWith('/api/access/code')), page.click('#newguest')]);
    assert.strictEqual(JSON.parse(codeReq.postData()).minutes, 15, 'the chosen time is what is sent');
    await page.waitForSelector('.join-code[data-role="view"]');
    assert(/Works for 1[45]:[0-9]{2} more/.test(await page.textContent('.join-code[data-role="view"]')), 'the code says how long it works: ' + await page.textContent('.join-code[data-role="view"]'));
    await page.selectOption('#joinminutes', '60');
    const joinCode = await page.evaluate(() => document.querySelector('.join-code[data-role="view"] .big-code').textContent);
    assert(/^[0-9]{6}$/.test(joinCode), 'a 6 digit guest code: ' + joinCode);
    await page.waitForFunction(() => { const i = document.querySelector('.join-code[data-role="view"] img.qr'); return i && i.complete && i.naturalWidth > 0; });
    await page.click('#showaccess');
    await page.waitForFunction(() => /On the room screen now: the Guest \(can watch\) code/.test(document.getElementById('accessscreenline').textContent));
    await page.click('#hideaccess');
    await page.waitForFunction(() => /Nothing on the room screen/.test(document.getElementById('accessscreenline').textContent));
    const scanCtx = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const scanner = await scanCtx.newPage();
    scanner.on('console', (m) => { if (['error'].includes(m.type()) && !expected.test(m.text())) problems.push('scanner: ' + m.text()); });
    await scanner.goto(base + '/#code=' + joinCode);
    await scanner.waitForSelector('#scannednote');
    assert.strictEqual(await scanner.evaluate(() => location.hash), '', 'the code is removed from the address bar');
    assert.strictEqual(await scanner.inputValue('#joincode'), joinCode, 'the scanned code is filled in');
    await scanner.click('#joinbtn');
    await scanner.waitForSelector('.pads');
    assert(await scanner.isDisabled('#black'), 'a guest code gives view-only access');
    await scanCtx.close();

    // Shaders and Vibes: one page holds everything about shaders. Switch the module on there, start the rotation with
    // the big button on Live, open the page from the link next to it, choose one shader by hand (which ends the
    // rotation), move a slider, change the settings, add and remove a file. The harness player draws nothing
    // (--vo=null), so this checks the panel and the API, not the picture; that is tests/test_shaders_gpu.py.
    const BUNDLED_SHADERS = 40;         // the files in pvj/shaders.d (named one by one in tests/test_shaders.py)
    await page.click('nav >> text=Live');
    await page.waitForSelector('.pads');
    assert.strictEqual(await page.locator('#vibes').count(), 0, 'no Vibes button while the module is off');
    assert.strictEqual(await page.locator('#shaderslink').count(), 0, 'and no Shaders link');
    await sys('Shaders and Vibes');
    assert.strictEqual(await page.locator('#shaderpage').count(), 0, 'the module off shows only the description and the big button');
    await switchOn('Shaders and Vibes');
    assert(await moduleIsOn('shaders'), 'the shaders module is on');
    await page.waitForSelector('#shadercard [data-shader="nxlx-tide.fs"]');
    assert.strictEqual(await page.locator('#shadercard [data-shader^="nxlx-"]').count(), BUNDLED_SHADERS, 'the project\'s own shaders are listed');
    // and the third-party pack (Vidvox ISF-Files): every one the API lists, and none of them in the Vibes rotation
    const packed = (await get('/api/shaders')).shaders.filter((s) => s.pack === 'isf-files');
    assert(packed.length === 7 && packed.every((s) => s.source === 'bundled' && !s.vibes && !s.error), 'the ISF-Files pack is listed, out of Vibes');
    assert.strictEqual(await page.locator('#shadercard [data-shader]').count(), BUNDLED_SHADERS + packed.length, 'the pack is listed with the project\'s own');
    // somebody else's work says so on its row: the pack and the author's own credit; the project's own rows do not
    assert.strictEqual(await page.locator('#shadercard [data-pack="isf-files"]').count(), packed.length, 'each pack row is marked with its pack');
    assert.strictEqual(await page.textContent('#shadercard [data-shader="isf-simplex-noise.fs"] .shadercredit'),
      'From the isf-files pack. Credit: by VIDVOX (simplex by Ashima Arts / Stefan Gustavson)', 'a pack shader shows its pack and credit');
    assert.strictEqual(await page.locator('#shadercard [data-pack="isf-files"] .shadercredit').count(), packed.length, 'every pack row has the line');
    assert.strictEqual(await page.locator('#shadercard [data-pack="nxlx"] .shadercredit').count(), 0, 'the project\'s own rows carry no credit line');
    assert.deepStrictEqual(await page.$$eval('#shaderpage .card', (cs) => cs.map((x) => x.id)), ['shadernow', 'shadercard', 'vibessettings', 'shaderremote'],
      'the page, top to bottom: now, the library, Vibes settings, other ways to control it (no sliders while nothing plays)');
    assert.strictEqual(await page.textContent('#shaderplaying'), 'No shader on screen');
    assert.strictEqual(await page.textContent('#vibesbtn'), 'Start Vibes');
    assert(/Light work/.test(await page.textContent('#shadercard [data-shader="nxlx-silk.fs"]')), 'a shader says how much work it is');
    await onPage('Shaders and Vibes');
    // The module goes off under the open page. The box is "off" from the first moment of the switch's request, and
    // it still answers GET /api/shaders then (200), with no shader and no set; the page asks that every few seconds.
    // It drew the answer and read the time of a set that was not there: "Cannot read properties of null (reading
    // 'dwell')", seen once in CI when the question's answer overtook the switch's. Both orders are forced here: the
    // switch's own answer is held back until the page has asked again, and then the module is switched off from
    // elsewhere. Either way the page must follow the box (the Off page with its one button), with no page error.
    {
      const followsOrBreaks = async (what) => {
        let hear;
        const broke = new Promise((res) => { hear = (e) => res(e.message); page.on('pageerror', hear); });
        const how = await Promise.race([page.waitForSelector('#sysoff', { timeout: 15000 }).then(() => ''), broke]);
        page.off('pageerror', hear);
        assert.strictEqual(how, '', what + ': the Shaders page drew an answer that had no set: ' + how);
        assert.strictEqual(await page.getAttribute('#sysswitch', 'aria-checked'), 'false', what + ': the switch says Off');
        assert.strictEqual(await page.locator('#shaderpage').count(), 0, what + ': none of the cards is left to tap');
        assert.strictEqual(await page.textContent('#syspage h1'), 'Shaders and Vibes', what + ': still on its page');
      };
      const backOn = async () => {
        await page.click('#sysswitchon');
        await page.waitForSelector('#vibessettings');
        assert(await moduleIsOn('shaders'), 'the shaders module is on again');
      };
      let letGo;
      const held = new Promise((res) => { letGo = res; });
      await page.route('**/api/modules/shaders', async (route) => {
        const answer = await route.fetch();           // the box has switched it off; the panel does not know yet
        await held;
        await route.fulfill({ response: answer });
      });
      try {
        await page.click('#sysswitch');               // nothing plays, so it asks nothing first
        await followsOrBreaks('the answer to the switch held back');
        assert.strictEqual(await moduleIsOn('shaders'), false, 'the box had switched it off');
      } finally {
        letGo();
      }
      await page.waitForFunction(() => /Switched off/.test(document.getElementById('msg').textContent));
      await page.unroute('**/api/modules/shaders');
      await page.waitForSelector('#sysoff');
      await backOn();
      assert.strictEqual(await post('/api/modules/shaders', { enabled: false }), 200);      // as another device would
      await followsOrBreaks('switched off from elsewhere');
      await backOn();
      await page.waitForSelector('#shadercard [data-shader="nxlx-tide.fs"]');
      assert.strictEqual(await page.textContent('#vibesbtn'), 'Start Vibes');
    }
    // The Mix screen no longer has a shaders card
    await page.click('nav >> text=Mix');
    await page.waitForSelector('#mo');
    assert.strictEqual(await page.locator('#shadercard, #shaderpage').count(), 0, 'the shaders card left Mix');
    // Live: one large Vibes button, with its state in words, and the Shaders link next to it
    await page.click('nav >> text=Live');
    await page.waitForSelector('#vibes');
    const big = await page.evaluate(() => { const r = document.getElementById('vibes').getBoundingClientRect(); return { h: r.height, w: r.width, vw: window.innerWidth }; });
    assert(big.h >= 56 && big.w >= big.vw - 34, 'the Vibes button is big and as wide as the phone screen allows: ' + JSON.stringify(big));
    assert.strictEqual(await page.textContent('#vibeswords'), 'Start Vibes');
    assert(!(await page.isVisible('#vibesskip')) && !(await page.isVisible('#liveprev')), 'no Previous and Next while no shader is on');
    await page.waitForSelector('#liveset:visible', { timeout: 20000 });
    assert.deepStrictEqual(await page.$$eval('#liveset option', (os) => os.map((o) => o.textContent)), ['Set: Ambient', 'Set: Show'], 'a new box has two sets, and Live has the simple chooser beside the Vibes button');
    await fitsPhone('Live with the Vibes button');
    await page.click('#vibes');
    await page.waitForFunction(() => /^Vibes: [A-Z]/.test((document.getElementById('np') || {}).textContent), null, { timeout: 15000 });
    await page.waitForFunction(() => /^Vibes is playing: [A-Z]/.test((document.getElementById('vibeswords') || {}).textContent));
    assert.strictEqual(await page.getAttribute('#vibes', 'aria-pressed'), 'true');
    assert.strictEqual(await page.textContent('#vibessub'), 'Tap to stop');
    // Starting, stopping, skipping and what is playing are together on Live
    await page.waitForSelector('#vibesskip:visible');
    assert((await page.evaluate(() => document.getElementById('vibesskip').getBoundingClientRect().height)) >= 44, 'Next one on Live is easy to hit');
    const skipped = page.waitForResponse((r) => r.url().endsWith('/api/shaders/step') && r.request().postData() === '{"dir":1}');
    await page.click('#vibesskip');
    assert.strictEqual((await skipped).status(), 200, 'Next on Live goes to the next shader');
    assert(await page.isVisible('#liveprev'), 'and Previous is beside it');
    await fitsPhone('Live while Vibes is playing');
    if (shots) await page.screenshot({ path: path.join(shots, '8-live-vibes.png') });
    // The link lands on the same page, and Back returns to Live
    await page.click('#shaderslink');
    await page.waitForSelector('#syspage h1:text-is("Shaders and Vibes")');
    await page.waitForFunction(() => /Vibes is playing/.test((document.getElementById('shaderline') || {}).textContent));
    assert.strictEqual(await page.textContent('#vibesbtn'), 'Stop Vibes');
    assert.strictEqual(await page.textContent('#shadernext'), 'Next \u203a');
    assert.strictEqual(await page.textContent('#sysback'), '‹ Live');
    if (shots) await page.screenshot({ path: path.join(shots, '9-shaders.png'), fullPage: true });
    // A guest sees what is playing, and nothing to press
    await guest.click('nav >> text=Live');
    await guest.waitForSelector('#vibes');
    assert(await guest.isDisabled('#vibes'), 'a guest cannot start or stop Vibes');
    await guest.click('#shaderslink');
    await guest.waitForSelector('#shadercard [data-shader="nxlx-tide.fs"]');
    assert(/Vibes is playing/.test(await guest.textContent('#shaderline')), 'a guest sees that Vibes is playing');
    assert.strictEqual(await guest.locator('#shaderpage button, #shaderpage input[type=range], #vibesdwell, #shaderheight, #sysswitch').count(), 0, 'a guest gets nothing to press on the Shaders page (only the filter of the list)');
    await page.click('#sysback');
    await page.waitForSelector('.pads');
    assert.strictEqual(await page.textContent('nav button[aria-current="page"]'), 'Live', 'Back from the Shaders page returns to Live');
    // Switching the module off while Vibes is on the screen asks first, in place; "no" puts the switch back and changes nothing
    await sysIndex();
    await chip('Shaders and Vibes', 'Active');
    await page.waitForSelector(`${rowOf('Shaders and Vibes')} .navstate:has-text("Vibes is playing")`);
    await sys('Shaders and Vibes');
    await page.click('#sysswitch');
    await page.waitForSelector('#confirmrow:has-text("Vibes is on the screen. Switching off stops it now.")');
    assert(!(await page.isVisible('#sysswitch')), 'the question takes the place of the switch');
    await fitsPhone('Shaders page, asking');
    await page.click('#confirmno');
    await page.waitForSelector('#sysswitch[aria-checked="true"]');
    assert.strictEqual(await page.locator('#confirmrow').count(), 0, 'the question is gone after "no"');
    assert(await moduleIsOn('shaders'), 'the module is still on after "no"');
    assert((await get('/api/shaders')).vibes.running, 'and Vibes is still playing');
    // Play just one. By its label, not its text, so it is the same button whether or not it is the one on screen.
    await page.waitForSelector('#shadercard [data-shader="nxlx-tide.fs"]');
    await page.click('#shadercard [data-shader="nxlx-tide.fs"] button[aria-label="Play Tide"]');
    await page.waitForFunction(() => (document.getElementById('shaderplaying') || {}).textContent === 'Tide');
    await page.waitForFunction(() => /One shader, until something else plays/.test((document.getElementById('shaderline') || {}).textContent));
    assert.strictEqual((await get('/api/shaders')).vibes.running, false, 'choosing a shader by hand ends the rotation');
    await page.waitForSelector('#vibeslast:has-text("a shader was chosen by hand")');      // why Vibes ended, in words
    await page.waitForSelector('#shadercard [data-shader="nxlx-tide.fs"] .chip:text-is("Playing")');
    // Its number inputs are sliders, with their range and usual value, a Reset each and Reset all; applied when let go
    await page.waitForSelector('#shin-speed');
    assert.deepStrictEqual(await page.$$eval('#shaderpage .card', (cs) => cs.map((x) => x.id)), ['shadernow', 'shadercontrols', 'shaderpresets', 'shadercard', 'vibessettings', 'shaderremote']);
    const speed = (await get('/api/shaders')).shaders.find((x) => x.id === 'nxlx-tide.fs').inputs.find((i) => i.name === 'speed');
    assert(new RegExp(speed.min + ' to ' + speed.max + ', normally ' + speed.default).test(await page.textContent('#shadersliders')), 'a slider says its range and its usual value');
    assert(/does not stop Vibes/.test(await page.textContent('#slidernote')) && /save a preset/.test(await page.textContent('#slidernote')), 'the page says what a control does to Vibes and how to keep its value');
    const speedNow = () => get('/api/shaders').then((d) => d.playing && d.playing.values.speed);
    await page.evaluate((v) => { const el = document.getElementById('shin-speed'); el.value = v; el.dispatchEvent(new Event('input')); el.dispatchEvent(new Event('change')); }, speed.max);
    await page.waitForFunction((v) => fetch('/api/shaders').then((r) => r.json()).then((d) => d.playing && Math.abs(d.playing.values.speed - v) < 1e-6), speed.max);
    await page.click('#shadersliders button[aria-label="Reset ' + speed.label + '"]');
    await page.waitForFunction((v) => fetch('/api/shaders').then((r) => r.json()).then((d) => d.playing && Math.abs(d.playing.values.speed - v) < 1e-6), speed.default);
    await page.waitForSelector('#shin-speed');
    await page.evaluate((v) => { const el = document.getElementById('shin-speed'); el.value = v; el.dispatchEvent(new Event('change')); }, speed.min);
    await page.waitForFunction((v) => fetch('/api/shaders').then((r) => r.json()).then((d) => d.playing && Math.abs(d.playing.values.speed - v) < 1e-6), speed.min);
    await page.click('#shaderresetall');
    await page.waitForFunction((v) => fetch('/api/shaders').then((r) => r.json()).then((d) => d.playing && Math.abs(d.playing.values.speed - v) < 1e-6), speed.default);
    assert.strictEqual(typeof await speedNow(), 'number');
    // Vibes settings apply on tap, with a brief "Saved"; there is no Save button
    assert.strictEqual(await page.locator('#shadersave').count(), 0, 'no Save button for the Vibes settings');
    assert.deepStrictEqual(await page.$$eval('#vibesdwell option', (os) => os.map((o) => o.textContent)),
      ['30 seconds', '1 minute', '2 minutes', '3 minutes', '5 minutes', '10 minutes', '15 minutes', '30 minutes', '1 hour']);
    await page.selectOption('#vibesdwell', '60');
    await page.waitForFunction(() => fetch('/api/shaders').then((r) => r.json()).then((d) => d.config.dwell === 60));
    await page.waitForSelector('#saved-dwell:text-is("Saved")');
    const vary = (await get('/api/shaders')).config.vary;
    await page.click('#vibesvary');
    await page.waitForFunction((was) => fetch('/api/shaders').then((r) => r.json()).then((d) => d.config.vary === !was), vary);
    await page.waitForSelector('#vibesvary[aria-checked="' + String(!vary) + '"]');
    // A time that is not in the list (set from a MIDI knob or the API) shows as it is, never as a wrong one
    assert.strictEqual(await post('/api/shaders', { action: 'config', dwell: 45 }), 200);
    await sys('Shaders and Vibes');
    await page.waitForSelector('#vibesdwell');
    assert.strictEqual(await page.$eval('#vibesdwell', (el) => el.options[el.selectedIndex].textContent), 'Custom (45 s)');
    // In or out of the Vibes rotation: a switch per shader, applied on tap
    const tideSwitch = '#shadercard [data-shader="nxlx-tide.fs"] .switch';
    assert.strictEqual(await page.getAttribute(tideSwitch, 'aria-checked'), 'true');
    await page.evaluate(() => { document.querySelector('#shadercard [data-shader="nxlx-tide.fs"]').dataset.seen = '1'; });
    await page.click(tideSwitch);
    await page.waitForSelector(tideSwitch + '[aria-checked="false"]');
    assert.strictEqual(await page.getAttribute('#shadercard [data-shader="nxlx-tide.fs"]', 'data-seen'), '1', 'a rotation switch changes its own row; the list is not drawn again');
    assert.strictEqual((await get('/api/shaders')).shaders.find((x) => x.id === 'nxlx-tide.fs').vibes, false, 'the switch took the shader out of the rotation');
    await page.click(tideSwitch);
    await page.waitForSelector(tideSwitch + '[aria-checked="true"]');
    // The library will grow: find by name, show by how much work it is; filtering only hides rows
    const shownRows = () => page.$$eval('#shadercard [data-shader]', (rs) => rs.filter((r) => !r.hidden).map((r) => r.dataset.shader));
    await page.fill('#shaderfilter', 'tid');
    assert.deepStrictEqual(await shownRows(), ['nxlx-tide.fs'], 'the filter finds a shader by name');
    await page.fill('#shaderfilter', 'zzz');
    await page.waitForSelector('#shadernone:has-text("No shader has")');
    await page.fill('#shaderfilter', '');
    await page.selectOption('#shadercost', 'medium');
    const mediums = await shownRows();
    assert(mediums.length >= 1 && mediums.length < BUNDLED_SHADERS, 'the work filter narrows the list: ' + mediums.length);
    assert((await page.$$eval('#shadercard [data-shader]', (rs) => rs.filter((r) => !r.hidden).every((r) => /Medium work/.test(r.textContent)))), 'and shows only medium work');
    await page.selectOption('#shadercost', 'all');
    assert.strictEqual((await shownRows()).length, BUNDLED_SHADERS + packed.length);
    // A MIDI controller and a lighting desk are set up on this page: no button that leads to another page
    assert.strictEqual(await page.locator('#shaderto-dmx, #shaderto-midi').count(), 0, 'no buttons that open the MIDI and DMX pages');
    await page.waitForSelector('#shaderdmxline:has-text("Vibes is on channel 9")');
    assert(/Stop: 50 to 99/.test(await page.textContent('#shaderdmxzones')) && /Start: 100 to 149/.test(await page.textContent('#shaderdmxzones')) && /Next one: 150 to 199/.test(await page.textContent('#shaderdmxzones')), 'the ninth channel\'s ranges are shown');
    assert(/Level now/.test(await page.textContent('#shaderdmxlevel')), 'and its level');
    assert.strictEqual(await page.getAttribute('#shaderdmxsw', 'aria-checked'), 'true', 'DMX shows as on');
    assert.strictEqual(await page.getAttribute('#shadermidisw', 'aria-checked'), 'false', 'MIDI shows as off (switched off above)');
    await page.click('#shadermidisw');
    await page.waitForSelector('#shadermidisw[aria-checked="true"]');
    assert(await moduleIsOn('control-midi') && (await get('/api/midi')).enabled === true, 'the MIDI switch here is the same one switch');
    assert.strictEqual(await page.locator('#shadermidi .teach').count(), 3, 'teach rows for Vibes on and off, next one, and the time each stays');
    await page.click('#shadermidi [data-teach="vibes_next"] button:has-text("Teach a control")');
    await page.waitForSelector('#shadermidi [data-teach="vibes_next"] #shaderlearning:has-text("Move or press the control now")');
    assert.strictEqual((await get('/api/midi')).learn.active, true, 'Teach starts the box listening for a control');
    await page.click('#shaderteachcancel');
    await page.waitForFunction(() => !document.getElementById('shaderlearning'));
    assert.strictEqual(await post('/api/midi/map', { add: { source: '*', kind: 'cc', channel: 0, number: 30, action: 'vibes_dwell' } }), 200);
    await sys('Shaders and Vibes');
    await page.waitForSelector('#shadermidi [data-teach="vibes_dwell"]:has-text("any controller, control 30")');     // what is mapped already, in place
    await page.click('#shadermidi [data-teach="vibes_dwell"] button:has-text("Remove")');
    await page.waitForSelector('#shadermidi [data-teach="vibes_dwell"]:has-text("Not on any control yet")');
    assert.strictEqual((await get('/api/midi')).map.filter((e) => e.action === 'vibes_dwell').length, 0, 'Remove took the mapping off');
    // Advanced is folded; a refused file says why under the button, not in the page's message line
    assert(!(await page.isVisible('#shaderupload')), 'Advanced starts folded');
    await page.click('#shaderadvanced summary');
    await page.waitForSelector('#shaderupload');
    assert(/too heavy for this box/.test(await page.textContent('#shaderadvanced')), 'the guard\'s switch is under Advanced');
    assert(/lightest/.test(await page.textContent('#shaderheight')), 'picture detail (beside the load, not under Advanced) says which choice is the lightest');
    await page.setInputFiles('#shaderpick', { name: 'bad$name.fs', mimeType: 'text/plain', buffer: Buffer.from('void main() {}') });
    await page.waitForFunction(() => /bad\$name\.fs was not added: a shader file is named/.test(document.getElementById('shaderuploadmsg').textContent));
    assert(/err/.test(await page.getAttribute('#shaderuploadmsg', 'class')), 'the refusal is shown as a problem');
    assert(!/not added|named with/.test(await page.textContent('#msg')), 'and not in the message line at the top');
    const flat = '/*{"ISFVSN":"2","DESCRIPTION":"A flat grey for the browser test.","CATEGORIES":["Generator"],"INPUTS":[{"NAME":"level","LABEL":"Level","TYPE":"float","MIN":0.0,"MAX":1.0,"DEFAULT":0.5}]}*/\nvoid main() { gl_FragColor = vec4(vec3(level), 1.0); }\n';
    await page.setInputFiles('#shaderpick', { name: 'flat-grey.fs', mimeType: 'text/plain', buffer: Buffer.from(flat) });
    const mine = '#shadercard [data-shader="flat-grey.fs"]';
    await page.waitForSelector(mine);
    assert(/Flat grey/.test(await page.textContent(mine)) && /your upload/.test(await page.textContent(mine)), 'an uploaded shader is listed as an upload');
    assert(await page.isVisible('#shaderupload'), 'Advanced stays open after an upload');
    assert.strictEqual(await page.locator('#shadercard [data-shader="nxlx-tide.fs"] button:has-text("Remove")').count(), 0, 'a bundled shader has no Remove');
    assert.strictEqual(await page.$eval(mine + ' .shaderacts', (row) => row.lastElementChild.textContent), 'Remove', 'Remove is the last control of an uploaded shader');
    await onPage('Shaders and Vibes');                               // sliders, settings and Advanced all open
    const small = await page.$$eval('#syspage button, #syspage select, #syspage summary, #syspage input[type=range]', (els) => els.filter((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height < 44; })
      .map((e) => (e.getAttribute('aria-label') || e.textContent || e.id).slice(0, 30)));
    assert.deepStrictEqual(small, [], 'every control on the Shaders page is at least 44 px high');
    await page.click(mine + ' button:has-text("Remove")');
    await page.waitForSelector('#confirmrow:has-text("Remove Flat grey? The file is deleted from the box.")');
    await fitsPhone('Shaders page, asking about a file');
    await page.click('#confirmno');
    await page.waitForFunction(() => !document.getElementById('confirmrow'));
    assert.strictEqual(await page.locator(mine).count(), 1, '"Keep it" keeps the file');
    await page.click(mine + ' button:has-text("Remove")');
    await page.click('#confirmyes');
    await page.waitForFunction(() => !document.querySelector('#shadercard [data-shader="flat-grey.fs"]'));
    assert.strictEqual((await get('/api/shaders')).shaders.filter((s) => s.id === 'flat-grey.fs').length, 0, 'the uploaded file is gone from the box');
    // ---- The instrument: controls by type, presets, sets, the load, a controller beside each control, the keys ----
    // A shader with every kind of input, uploaded as a file would be. The harness player draws nothing, so what is
    // checked is what the page sends and shows; the picture is tests/test_shaderlive_gpu.py.
    const ALL = '/*{"DESCRIPTION": "Every kind of control, for the browser test.", "COST": "low: no loop", "INPUTS": [' +
      '{"NAME": "level", "TYPE": "float", "MIN": 0.0, "MAX": 2.0, "DEFAULT": 0.5, "LABEL": "Level"},' +
      '{"NAME": "lit", "TYPE": "bool", "DEFAULT": false, "LABEL": "Lit"},' +
      '{"NAME": "mode", "TYPE": "long", "VALUES": [0, 2, 5], "LABELS": ["None", "Two", "Five"], "DEFAULT": 2, "LABEL": "Mode"},' +
      '{"NAME": "shape", "TYPE": "long", "VALUES": [0, 1, 2, 3, 4, 5, 6], "LABELS": ["Dot", "Line", "Ring", "Star", "Wave", "Grid", "Cross"], "DEFAULT": 1, "LABEL": "Shape"},' +
      '{"NAME": "count", "TYPE": "long", "MIN": 1, "MAX": 6, "DEFAULT": 3, "LABEL": "Count"},' +
      '{"NAME": "tint", "TYPE": "color", "DEFAULT": [1.0, 0.5, 0.25, 1.0], "LABEL": "Tint"},' +
      '{"NAME": "spot", "TYPE": "point2D", "DEFAULT": [0.5, 0.5], "MIN": [0.0, 0.0], "MAX": [1.0, 1.0], "LABEL": "Spot"},' +
      '{"NAME": "bang", "TYPE": "event", "LABEL": "Flash"},' +
      '{"NAME": "steps", "TYPE": "float", "MIN": 1.0, "MAX": 6.0, "DEFAULT": 3.0, "LABEL": "Steps"}]}*/\n' +
      'void main() {\n    float v = 0.0;\n    for (int i = 0; i < 6; i++) { if (float(i) < steps) { v += level; } }\n' +
      '    gl_FragColor = vec4(tint.rgb * v + vec3(spot, float(mode + count + shape)), (lit || bang) ? 1.0 : 0.5);\n}\n';
    const AID = 'all-inputs.fs';
    assert.strictEqual(await post('/api/shaders', { action: 'upload', name: AID, source: ALL }), 200);
    assert.strictEqual(await post('/api/shaders/play', { id: AID }), 200);
    await page.waitForSelector('#shin-level');
    assert.deepStrictEqual(await page.$$eval('#shaderpage .card', (cs) => cs.map((x) => x.id)), ['shadernow', 'shadercontrols', 'shaderpresets', 'shadercard', 'vibessettings', 'shaderremote'],
      'a phone, top to bottom: now, controls, presets, the library, Vibes, controllers');
    const sent = [];
    page.on('request', (r) => {
      if (r.method() === 'POST' && /\/api\/(shaders\/(values|play|step|preset)|vibes)$/.test(r.url())) sent.push({ path: new URL(r.url()).pathname, body: JSON.parse(r.postData() || '{}') });
    });
    const sentTo = (p) => sent.filter((x) => x.path === p).map((x) => x.body);
    async function wasSent(p, test, what) {
      const hit = typeof test === 'function' ? test : (b) => JSON.stringify(b) === JSON.stringify(test);
      for (let n = 0; n < 80; n++) { if (sentTo(p).some(hit)) return; await page.waitForTimeout(50); }
      assert.fail(what + ': not sent to ' + p + (typeof test === 'function' ? '' : ' ' + JSON.stringify(test)) + '; the last were ' + JSON.stringify(sentTo(p).slice(-4)));
    }
    const V = (values) => ({ values, id: AID });
    const C = (controls) => ({ controls, id: AID });
    const VALUES = '/api/shaders/values';
    const boxHas = (name, v) => page.waitForFunction(([n, x]) => fetch('/api/shaders').then((r) => r.json()).then((d) => d.playing && JSON.stringify(d.playing.values[n]) === JSON.stringify(x)), [name, v]);
    const aPoll = () => page.waitForResponse((r) => r.url().endsWith('/api/shaders') && r.request().method() === 'GET', { timeout: 20000 });
    const setRange = (id, v, events) => page.evaluate(([i, x, evs]) => { const el = document.getElementById(i); el.value = x; evs.forEach((e) => el.dispatchEvent(new Event(e))); }, [id, v, events]);
    // a number: a slider that sends while it is dragged (a few times a second, not once per step), keeps its place
    // through a poll while it is held, and always sends the last value when it is let go
    await page.evaluate(() => {
      const el = document.getElementById('shin-level');
      el.dataset.seen = '1';
      el.dispatchEvent(new Event('pointerdown'));
      for (let n = 50; n <= 125; n++) { el.value = n / 100; el.dispatchEvent(new Event('input')); }
    });
    await page.waitForTimeout(400);
    const dragged = sentTo(VALUES).length;
    assert(dragged >= 1 && dragged <= 4, '76 steps of a drag are a few sends, not 76: ' + dragged);
    assert.strictEqual(await post(VALUES, { values: { level: 0.2 } }), 200);            // moved from somewhere else meanwhile
    await aPoll(); await aPoll();
    await page.waitForTimeout(200);
    {
      const held = await page.$eval('#shin-level', (el) => [+el.value, el.dataset.seen]);
      assert(Math.abs(held[0] - 1.25) < 0.006 && held[1] === '1', 'a slider that is held is neither moved nor drawn again by a poll: ' + JSON.stringify(held));
    }
    await setRange('shin-level', 1.5, ['input', 'change', 'pointerup']);
    await wasSent(VALUES, V({ level: 1.5 }), 'a slider, let go');
    await boxHas('level', 1.5);
    assert.strictEqual(await post(VALUES, { values: { level: 0.3 } }), 200);            // and once nobody holds it, it follows the box
    await page.waitForFunction(() => Math.abs(+document.getElementById('shin-level').value - 0.3) < 0.006, null, { timeout: 20000 });
    assert.strictEqual(await page.$eval('#shin-level', (el) => el.dataset.seen), '1', 'the box\'s value is put into the slider that is there; the card is not drawn again');
    // a switch
    await page.click('#shin-lit');
    await wasSent(VALUES, V({ lit: true }), 'a switch');
    assert.strictEqual(await page.getAttribute('#shin-lit', 'aria-checked'), 'true');
    assert.strictEqual(await page.getAttribute('#shin-lit', 'role'), 'switch');
    // a choice of three: all in view; of seven: a list
    assert.deepStrictEqual(await page.$$eval('#shin-mode [role=radio]', (bs) => bs.map((b) => b.textContent + ':' + b.getAttribute('aria-checked'))), ['None:false', 'Two:true', 'Five:false']);
    await page.click('#shin-mode [data-value="5"]');
    await wasSent(VALUES, V({ mode: 5 }), 'a choice, as buttons');
    assert.strictEqual(await page.getAttribute('#shin-mode [data-value="5"]', 'aria-checked'), 'true');
    assert.strictEqual(await page.$eval('#shin-shape', (el) => el.tagName + ':' + el.options.length), 'SELECT:7');
    await page.selectOption('#shin-shape', '4');
    await wasSent(VALUES, V({ shape: 4 }), 'a choice, as a list');
    await setRange('shin-count', 5, ['input', 'change']);
    await wasSent(VALUES, V({ count: 5 }), 'a whole number in a range');
    // a colour: a swatch large enough to hit, and alpha beside it
    {
      const sw = await page.$eval('#shin-tint', (el) => { const r = el.getBoundingClientRect(); return [el.type, r.width, r.height]; });
      assert(sw[0] === 'color' && sw[1] >= 44 && sw[2] >= 44, 'the colour swatch is a native colour field of at least 44 px: ' + JSON.stringify(sw));
    }
    await setRange('shin-tint', '#00ff00', ['input', 'change']);
    await wasSent(VALUES, V({ tint: [0, 1, 0, 1] }), 'a colour');
    await setRange('shin-tint-alpha', 0.5, ['input', 'change']);
    await wasSent(VALUES, V({ tint: [0, 1, 0, 0.5] }), 'a colour\'s alpha');
    // a point: a square to drag in, which keeps its place through a poll while held; the arrows nudge it (and do not step)
    const xy = (type, fx, fy) => page.evaluate(([t, x, y]) => {
      const el = document.getElementById('shin-spot'), r = el.getBoundingClientRect();
      el.dispatchEvent(new PointerEvent(t, { bubbles: true, cancelable: true, pointerId: 7, clientX: r.left + r.width * x, clientY: r.top + r.height * y }));
    }, [type, fx, fy]);
    const spotNear = (x, y) => (b) => b.values && b.values.spot && Math.abs(b.values.spot[0] - x) < 0.011 && Math.abs(b.values.spot[1] - y) < 0.011;
    await page.locator('#shin-spot').scrollIntoViewIfNeeded();
    {
      const pad = await page.$eval('#shin-spot', (el) => { const r = el.getBoundingClientRect(); return [r.width, r.height]; });
      assert(pad[0] >= 120 && Math.abs(pad[0] - pad[1]) < 2, 'the XY pad is a square: ' + JSON.stringify(pad));
    }
    await xy('pointerdown', 0.25, 0.25);
    await wasSent(VALUES, spotNear(0.25, 0.75), 'a point, pressed (up is more)');
    assert.strictEqual(await page.textContent('[data-input="spot"] .slidervalue'), 'x 0.25  y 0.75', 'the two numbers are shown');
    assert.strictEqual(await post(VALUES, { values: { spot: [0.9, 0.9] } }), 200);
    await aPoll(); await aPoll();
    assert.strictEqual(await page.textContent('[data-input="spot"] .slidervalue'), 'x 0.25  y 0.75', 'a held point is not moved by a poll');
    await xy('pointermove', 0.75, 0.25);
    await xy('pointerup', 0.75, 0.25);
    await wasSent(VALUES, spotNear(0.75, 0.75), 'a point, dragged');
    const stepsBefore = sentTo('/api/shaders/step').length;
    await page.focus('#shin-spot');
    await page.keyboard.press('ArrowLeft');
    await wasSent(VALUES, spotNear(0.73, 0.75), 'a point, nudged with an arrow key');
    assert.strictEqual(sentTo('/api/shaders/step').length, stepsBefore, 'an arrow key on the XY pad moves the point, not the shader');
    await page.evaluate(() => document.activeElement.blur());
    // an event: a button
    await page.click('#shin-bang');
    await wasSent(VALUES, V({ bang: true }), 'an event');
    // the three every shader has, above its own: Speed with Freeze, Colour turn, Brightness trim, each with Reset
    assert.deepStrictEqual(await page.$$eval('#shadercommon .ctl', (cs) => cs.map((x) => x.dataset.common)), ['speed', 'hue', 'brightness']);
    // (a shader's control and a drawn controller's cell share the class ctl; the cell's box, centred small type and
    // pointer once leaked onto every shader control)
    assert.deepStrictEqual(await page.$eval('#shadercommon .ctl', (c) => { const cs = getComputedStyle(c); return [cs.borderLeftWidth, cs.borderRadius, cs.textAlign, cs.cursor, cs.overflow]; }),
      ['0px', '0px', 'start', 'auto', 'visible'], 'a shader control is not drawn as a controller cell');
    assert(await page.evaluate(() => document.getElementById('shadercommon').compareDocumentPosition(document.getElementById('shadersliders')) & Node.DOCUMENT_POSITION_FOLLOWING), 'the common controls are above the shader\'s own');
    {
      const d = await get('/api/shaders'), limits = d.controls.speed, mine = d.shaders.find((x) => x.id === AID);
      assert(mine.speed_max === limits.max, 'a shader that is not of the Performance family may run at the box\'s top speed');
      assert.deepStrictEqual(await page.$eval('#shc-speed', (el) => [+el.min, +el.max]), [limits.min, mine.speed_max], 'Speed runs from 0 to what the box allows this shader');
      assert.strictEqual(await page.locator('#shadercommon [data-common="speed"] .trackmark').count(), 1, 'and 1 is marked');
    }
    await setRange('shc-speed', 2, ['input', 'change']);
    await wasSent(VALUES, C({ speed: 2 }), 'Speed');
    await page.click('#shc-speed-freeze');
    await wasSent(VALUES, C({ speed: 0 }), 'Freeze');
    assert.strictEqual(await page.getAttribute('#shc-speed-freeze', 'aria-checked'), 'true');
    const twos = sentTo(VALUES).filter((b) => b.controls && b.controls.speed === 2).length;
    await page.click('#shc-speed-freeze');
    await page.waitForFunction(() => document.getElementById('shc-speed-freeze').getAttribute('aria-checked') === 'false');
    for (let n = 0; n < 60 && sentTo(VALUES).filter((b) => b.controls && b.controls.speed === 2).length === twos; n++) await page.waitForTimeout(50);
    assert.strictEqual(sentTo(VALUES).filter((b) => b.controls && b.controls.speed === 2).length, twos + 1, 'Freeze off goes back to the speed before');
    await setRange('shc-hue', 90, ['input', 'change']);
    await wasSent(VALUES, C({ hue: 90 }), 'Colour turn');
    await page.click('#shadercommon button[aria-label="Reset Colour turn"]');
    await wasSent(VALUES, C({ hue: 0 }), 'Reset of Colour turn');
    await setRange('shc-brightness', 1.5, ['input', 'change']);
    await wasSent(VALUES, C({ brightness: 1.5 }), 'Brightness trim');
    assert.strictEqual(sentTo('/api/shaders/play').length, 0, 'no control sends a Play');
    await page.waitForFunction(() => fetch('/api/shaders').then((r) => r.json()).then((d) => d.playing && d.playing.values.lit === true && d.playing.values.mode === 5 && d.playing.controls.brightness === 1.5 && d.playing.controls.speed === 2));
    // a refusal is said at the control: here a value for a shader that has just left the screen
    {
      const said = await page.evaluate(() => fetch('/api/shaders/play', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-PVJ-Request': '1' }, body: JSON.stringify({ id: 'nxlx-tide.fs' }) }).then(() => {
        document.getElementById('shin-lit').click();
        return new Promise((done) => {
          let n = 0;
          const t = setInterval(() => { const e = document.querySelector('[data-input="lit"] .ctlnote.err'); if ((e && e.textContent) || ++n > 200) { clearInterval(t); done(e ? e.textContent : ''); } }, 20);
        });
      }));
      assert(/another shader is on the screen/.test(said), 'a refused value is said beside its control: ' + said);
    }
    assert.strictEqual(await post('/api/shaders/play', { id: AID }), 200);
    await page.waitForFunction(() => document.getElementById('shin-level') && !document.querySelector('.ctlnote.err') && document.getElementById('shaderplaying').textContent === 'All inputs', null, { timeout: 20000 });
    // Presets. The keys do nothing while a name is being typed.
    assert(/Presets: your saved versions of this shader/.test(await page.textContent('#shaderpresets')));
    await page.fill('#presetname', 'x');
    {
      const before = sent.length;
      for (const k of ['Space', '1', 'ArrowRight', 'ArrowLeft']) await page.keyboard.press(k);
      await page.waitForTimeout(400);
      assert.strictEqual(sent.length, before, 'no key does anything while a field has the cursor: ' + JSON.stringify(sent.slice(before)));
    }
    await page.fill('#presetname', 'Bright');
    await page.click('#presetsave');
    await page.waitForSelector('#presetrow [data-preset="Bright"][aria-pressed="true"]');           // saved, and marked as the one in use
    assert.deepStrictEqual((await get('/api/shaders')).shaders.find((s) => s.id === AID).presets, ['Bright']);
    await page.click('#shin-lit');
    await page.waitForSelector('#presetchanged');                                                     // the values differ from it now
    await page.waitForSelector('#presetrow [data-preset="Bright"].was');
    await page.click('#presetrow [data-preset="Bright"]');
    await wasSent('/api/shaders/preset', { id: AID, name: 'Bright' }, 'a tap on a preset');
    await page.waitForSelector('#presetrow [data-preset="Bright"][aria-pressed="true"]');
    await page.waitForFunction(() => !document.getElementById('presetchanged'));
    await page.click('#presetmore summary');
    await page.click('[data-preset-edit="Bright"] button:has-text("Rename")');
    await page.fill('.renamerow input', 'Night');
    await page.click('.renamerow button:has-text("Save")');
    await page.waitForSelector('#presetrow [data-preset="Night"]');
    assert.deepStrictEqual((await get('/api/shaders')).shaders.find((s) => s.id === AID).presets, ['Night'], 'renamed on the box');
    await page.click('[data-preset-edit="Night"] button:has-text("Delete")');
    await page.waitForSelector('#confirmrow:has-text("Delete the preset Night?")');
    await page.click('#confirmno');
    await page.waitForFunction(() => !document.getElementById('confirmrow'));
    assert.strictEqual(await page.locator('#presetrow [data-preset="Night"]').count(), 1, '"Keep it" keeps the preset');
    await page.click('[data-preset-edit="Night"] button:has-text("Delete")');
    await page.click('#confirmyes');
    await page.waitForSelector('#nopresets');
    assert.deepStrictEqual((await get('/api/shaders')).shaders.find((s) => s.id === AID).presets, [], 'deleted on the box');
    for (const name of ['One', 'Two']) {
      await page.fill('#presetname', name);
      await page.click('#presetsave');
      await page.waitForSelector('#presetrow [data-preset="' + name + '"][aria-pressed="true"]');
    }
    assert.deepStrictEqual(await page.$$eval('#presetrow .slot', (xs) => xs.map((x) => x.textContent)), ['1', '2'], 'each preset shows its number, which is its key and its pad');
    await page.evaluate(() => document.activeElement.blur());
    await page.keyboard.press('1');
    await wasSent('/api/shaders/preset', { id: AID, name: 'One' }, 'the key 1');
    await page.waitForSelector('#presetrow [data-preset="One"][aria-pressed="true"]');
    // A controller is taught beside the thing it controls: knob N is the N-th input a knob can drive (a colour and a
    // point are not), and Speed, Previous, Next and the eight preset places have their own
    assert.deepStrictEqual(await page.$$eval('#shadersliders [data-midi]', (bs) => bs.map((b) => b.dataset.midi)), [1, 2, 3, 4, 5, 6, 7].map((n) => 'shader_control_' + n));
    assert.strictEqual(await page.locator('#shadersliders [data-input="tint"] [data-midi], #shadersliders [data-input="spot"] [data-midi]').count(), 0);
    assert.strictEqual(await page.getAttribute('[data-input="bang"] [data-midi]', 'data-midi'), 'shader_control_6');
    for (const a of ['shader_speed', 'shader_prev', 'shader_next']) assert.strictEqual(await page.locator('#shaderpage [data-midi="' + a + '"]').count(), 1, 'a MIDI button for ' + a);
    assert.deepStrictEqual(await page.$$eval('#presetslots [data-midi]', (bs) => bs.map((b) => b.dataset.midi)), [1, 2, 3, 4, 5, 6, 7, 8].map((n) => 'shader_preset_' + n));
    await page.click('[data-midi="shader_control_1"]');
    await page.waitForSelector('[data-teach="shader_control_1"]:has-text("Knob 1 follows the first control of whichever shader is playing")');
    await page.click('[data-teach="shader_control_1"] button:has-text("Teach a control")');
    await page.waitForSelector('[data-teach="shader_control_1"] #shaderlearning:has-text("Move or press the control now")');
    await page.click('#shaderteachcancel');
    await page.waitForFunction(() => !document.getElementById('shaderlearning'));
    assert.strictEqual(await post('/api/midi/map', { add: { source: '*', kind: 'cc', channel: 0, number: 41, action: 'shader_control_1' } }), 200);
    await page.click('[data-midi="shader_control_1"]');                                             // closed, and opened again: it asks the box
    await page.click('[data-midi="shader_control_1"]');
    await page.waitForSelector('[data-teach="shader_control_1"]:has-text("Now on: any controller, control 41")');
    await page.waitForSelector('[data-midi="shader_control_1"].mapped');
    await onPage('Shaders and Vibes');                               // every kind of control, presets and a teach box open, at phone width
    {
      const small = await page.$$eval('#syspage button, #syspage select, #syspage summary, #syspage input[type=range], #syspage input[type=color], #syspage .xypad', (els) => els.filter((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && (r.height < 44 || r.width < 44); })
        .map((e) => (e.getAttribute('aria-label') || e.textContent || e.id).slice(0, 30)));
      assert.deepStrictEqual(small, [], 'every control of the instrument is at least 44 px');
    }
    await page.click('[data-teach="shader_control_1"] button:has-text("Remove")');
    await page.waitForSelector('[data-teach="shader_control_1"]:has-text("Not on any control yet")');
    assert.strictEqual((await get('/api/midi')).map.filter((e) => e.action === 'shader_control_1').length, 0);
    await page.click('[data-midi="shader_control_1"]');
    // The library: measured numbers, the pack filter, picture detail from this board's own list
    assert(/Light work\. 7\.5 ms on a Pi 4 at 720 lines/.test(await page.textContent('#shadercard [data-shader="nxlx-silk.fs"]')), 'a measured shader says its numbers');
    await page.selectOption('#shaderpack', 'isf-files');
    assert.strictEqual((await shownRows()).length, packed.length, 'the pack filter shows the pack');
    await page.selectOption('#shaderpack', 'all');
    {
      const rows = (await get('/api/shaders')).shaders, of = (f) => rows.filter((x) => (x.categories || []).includes(f)).length;
      assert(of('Performance') === 15 && of('Ambient') >= 15, 'the box has both families: ' + of('Ambient') + ' and ' + of('Performance'));
      for (const f of ['Performance', 'Ambient']) {
        await page.selectOption('#shaderfamily', f);
        assert.strictEqual((await shownRows()).length, of(f), 'the family filter shows the ' + f + ' shaders');
      }
      await page.selectOption('#shaderfamily', 'all');
      assert(/Performance/.test(await page.textContent('#shadercard [data-shader="nxlx-bars.fs"] .shaderfacts')), 'a row says its family');
    }
    {
      const render = (await get('/api/shaders')).render;
      assert.deepStrictEqual(await page.$$eval('#shaderheight option', (os) => os.map((o) => +o.value)), render.heights, 'picture detail offers this board\'s own heights');
      assert(/usual here/.test(await page.$eval('#shaderheight option[value="' + render.default + '"]', (o) => o.textContent)), 'and marks the usual one');
      const other = render.heights.find((x) => x !== render.height);
      await page.selectOption('#shaderheight', String(other));
      await page.waitForFunction((x) => fetch('/api/shaders').then((r) => r.json()).then((d) => d.config.height === x), other);
      await page.waitForSelector('#shaderheight');
      await page.selectOption('#shaderheight', String(render.height));
      await page.waitForFunction((x) => fetch('/api/shaders').then((r) => r.json()).then((d) => d.config.height === x), render.height);
      // A saved picture detail above this board's usual one (a box set up by the first version): a plain line beside
      // the load says so, and one tap puts it back
      const saved = (await get('/api/shaders')).config.height, above = render.heights.find((x) => x > render.default);
      assert(above, 'this board offers a height above its usual one');
      if (saved <= render.default) assert.strictEqual(await page.locator('#detailhigh').count(), 0, 'nothing is said while the detail is the usual one or lower');
      assert.strictEqual(await post('/api/shaders', { action: 'config', height: above }), 200);
      try {
        await page.waitForSelector('#shadernow #detailhigh', { timeout: 15000 });
      } catch (e) {
        // Timed out here once (2026-10-05) with the box holding the new height and the page asking every 3 seconds.
        // The page does not draw a card again while a field in it has the cursor or a question waits in it: say
        // which of those it was, or that it was neither.
        const why = await page.evaluate(() => fetch('/api/shaders').then((r) => r.json()).then((d) => {
          const a = document.activeElement, slot = document.querySelector('.slot-now'), q = document.getElementById('confirmrow'), pick = document.getElementById('shaderheight');
          return { boxHeight: d.config.height, usual: d.render.default, chooserShows: pick ? pick.value : null, pageThere: !!document.getElementById('shaderpage'),
            cursorIn: a && a !== document.body ? a.tagName.toLowerCase() + '#' + a.id : 'nothing', cursorInTheNowCard: !!(a && slot && slot.contains(a)),
            question: q ? q.textContent.slice(0, 80) : null, questionInTheNowCard: !!(q && slot && slot.contains(q)), line: (document.getElementById('shaderline') || {}).textContent };
        }));
        throw new Error(e.message.split('\n')[0] + ' | the line about a high picture detail did not come (#shadernow #detailhigh): ' + JSON.stringify(why));
      }
      assert.strictEqual(await page.textContent('#detailhighwords'), 'Picture detail is ' + above + ' lines. This box is happier at ' + render.default + '.');
      assert.strictEqual(await page.textContent('#detailuse'), 'Use ' + render.default);
      await onPage('Shaders and Vibes');
      const lowered = page.waitForResponse((r) => r.url().endsWith('/api/shaders') && r.request().method() === 'POST' && r.request().postData() === JSON.stringify({ action: 'config', height: render.default }));
      await page.click('#detailuse');
      assert.strictEqual((await lowered).status(), 200, 'Use ' + render.default + ' applies on tap');
      await page.waitForFunction(() => !document.getElementById('detailhigh'), null, { timeout: 15000 });
      assert.strictEqual((await get('/api/shaders')).config.height, render.default);
      assert.strictEqual(await page.inputValue('#shaderheight'), String(render.default), 'and the chooser shows it');
      assert.strictEqual(await post('/api/shaders', { action: 'config', height: saved }), 200);       // as it was, for the steps that follow
      await page.waitForFunction((x) => document.getElementById('shaderheight') && document.getElementById('shaderheight').value === String(x), saved, { timeout: 15000 });
    }
    // What this harness cannot make happen by itself (no GPU): a load that is too high and a GPU refusal. The box's
    // answer is given those fields on its way to the page.
    let faked = true;
    await page.route('**/api/shaders', async (route) => {
      if (!faked || route.request().method() !== 'GET') return route.continue();
      const res = await route.fetch(), d = await res.json();
      d.shaders.find((s) => s.id === 'nxlx-ember.fs').refused = 'line 6: no such thing';
      if (d.playing) { d.playing.load = 'heavy'; d.playing.drops_per_second = 4.2; }
      await route.fulfill({ response: res, json: d });
    });
    await page.waitForSelector('#shaderload[data-load="heavy"]', { timeout: 20000 });
    assert(/Dropping frames: try a lower picture detail/.test(await page.textContent('#shaderloadwords')) && /4\.2 dropped frames a second/.test(await page.textContent('#shaderloadwords')), 'the load is said in words');
    assert(await page.evaluate(() => document.getElementById('shadernow').contains(document.getElementById('shaderheight'))), 'picture detail is right there with the load');
    await page.waitForSelector('#shadercard [data-shader="nxlx-ember.fs"] .shaderrefused:has-text("This box\'s GPU refused it: line 6: no such thing")');
    faked = false;
    await page.unroute('**/api/shaders');
    await page.waitForFunction(() => document.getElementById('shaderload').getAttribute('data-load') !== 'heavy' && !document.querySelector('.shaderrefused'), null, { timeout: 20000 });
    // Too heavy on this box: said on its row, with the way back
    assert.strictEqual(await post('/api/shaders', { action: 'heavy', id: 'nxlx-silk.fs', on: true }), 200);
    await page.waitForSelector('#shadercard [data-shader="nxlx-silk.fs"] .shaderheavy:has-text("Too heavy on this box. Left out of Vibes.")', { timeout: 20000 });
    await page.click('#shadercard [data-shader="nxlx-silk.fs"] .shaderheavy button:has-text("Put it back")');
    await page.waitForFunction(() => !document.querySelector('#shadercard [data-shader="nxlx-silk.fs"] .shaderheavy'));
    assert.strictEqual((await get('/api/shaders')).shaders.find((s) => s.id === 'nxlx-silk.fs').heavy, null, '"Put it back" took the note off');
    // Sets. A new box has two (Ambient, the usual one, and Show with the Performance shaders), so there is a simple
    // chooser beside Start Vibes. With one set, nothing on the page asks anyone to think about sets.
    assert.deepStrictEqual(await page.$$eval('#vibesset option', (os) => os.map((o) => o.textContent)), ['Ambient (usual)', 'Show'], 'the chooser: Ambient or Show');
    assert.strictEqual(await page.textContent('#vibessettings h2'), 'Vibes sets');
    assert.strictEqual(await page.textContent('#libset'), 'The switches put a shader in or out of the set Ambient.');
    {
      const first = (await get('/api/shaders')).sets.find((e) => e.name === 'Show');
      assert.strictEqual(await post('/api/shaders', { action: 'set', op: 'delete', id: first.id }), 200);
      await page.waitForFunction(() => !document.getElementById('vibesset') && !document.getElementById('setlist'), null, { timeout: 20000 });
    }
    assert.strictEqual(await page.locator('#vibesset, #setlist, #setstart').count(), 0, 'one set: no chooser and no list of sets');
    assert.strictEqual(await page.textContent('#vibessettings h2'), 'Vibes settings');
    assert.strictEqual(await page.textContent('#libset'), 'The switch puts a shader in or out of Vibes.');
    await page.click('#setaddfold summary');
    await page.fill('#setname', 'Show');
    await page.click('#setadd');
    await page.waitForSelector('#setediting:text-is("Editing: Show")');
    assert.strictEqual(await page.textContent('#vibessettings h2'), 'Vibes sets');
    assert.strictEqual(await page.textContent('#libset'), 'The switches put a shader in or out of the set Show.', 'the library says which set its switches edit');
    const inSet = (id) => '#shadercard [data-shader="' + id + '"] .switch';
    assert.strictEqual(await page.getAttribute(inSet('nxlx-prism.fs'), 'aria-checked'), 'false', 'the new set is empty');
    assert(await page.isDisabled('#setstart'), 'an empty set cannot be started');
    for (const id of ['nxlx-prism.fs', AID]) {
      await page.click(inSet(id));
      await page.waitForSelector(inSet(id) + '[aria-checked="true"]');
    }
    {
      const d = await get('/api/shaders'), show = d.sets.find((e) => e.name === 'Show');
      assert.deepStrictEqual(show.shaders.map((r) => r.id), ['nxlx-prism.fs', AID], 'the switches filled the set being edited');
      assert(d.active !== show.id && d.sets.find((e) => e.id === d.active).shaders.every((r) => r.id !== AID), 'and left the usual set alone');
      assert.strictEqual(await page.locator('#vibesset option').count(), 2, 'with two sets there is a chooser beside the Vibes button');
      await page.click('#setactivate');
      await page.waitForFunction(() => !document.getElementById('setactivate'));
      assert.strictEqual((await get('/api/shaders')).active, show.id, 'made the usual set');
    }
    await page.click('#setstart');
    await page.waitForFunction(() => fetch('/api/shaders').then((r) => r.json()).then((d) => d.vibes.running && d.vibes.set && d.vibes.set.name === 'Show' && d.playing), null, { timeout: 20000 });
    await page.waitForFunction(() => /Vibes is playing the set Show/.test(document.getElementById('shaderline').textContent), null, { timeout: 20000 });
    // moving a control while Vibes runs does not end it
    await page.waitForSelector('#shc-hue');
    await setRange('shc-hue', 45, ['input', 'change']);
    await wasSent(VALUES, (b) => b.controls && b.controls.hue === 45, 'Colour turn during Vibes');
    await page.waitForFunction(() => fetch('/api/shaders').then((r) => r.json()).then((d) => d.vibes.running && d.playing && d.playing.controls.hue === 45), null, { timeout: 20000 });
    // The keys: the arrows step, space stops and starts Vibes
    await page.evaluate(() => document.activeElement.blur());
    assert(/Space/.test(await page.textContent('#shaderkeys')), 'the keys are said on the page');
    await page.keyboard.press('ArrowRight');
    await wasSent('/api/shaders/step', { dir: 1 }, 'the right arrow');
    await page.keyboard.press('ArrowLeft');
    await wasSent('/api/shaders/step', { dir: -1 }, 'the left arrow');
    await page.keyboard.press('Space');
    await wasSent('/api/vibes', { on: false }, 'space, while Vibes runs');
    await page.waitForFunction(() => fetch('/api/shaders').then((r) => r.json()).then((d) => !d.vibes.running));
    await page.waitForSelector('#vibesbtn:text-is("Start Vibes")');
    await page.keyboard.press('Space');
    await page.waitForFunction(() => fetch('/api/shaders').then((r) => r.json()).then((d) => d.vibes.running && d.vibes.set.name === 'Show'), null, { timeout: 20000 });
    await page.waitForSelector('#vibesbtn:text-is("Stop Vibes")', { timeout: 20000 });
    // Previous and Next are buttons too, and work without Vibes
    await page.click('#vibesbtn');
    await page.waitForFunction(() => fetch('/api/shaders').then((r) => r.json()).then((d) => !d.vibes.running));
    {
      const n = sentTo('/api/shaders/step').length;
      await page.click('#shadernext');
      await page.waitForFunction(() => fetch('/api/shaders').then((r) => r.json()).then((d) => !d.vibes.running && d.playing), null, { timeout: 20000 });
      assert.deepStrictEqual(sentTo('/api/shaders/step').slice(n), [{ dir: 1 }], 'Next steps without Vibes');
      assert.strictEqual(await page.textContent('#shaderprev'), '‹ Previous');
    }
    // A Performance shader flashes, so its Speed stops at 1 until the owner allows faster, under Advanced, with a
    // plain warning
    assert.strictEqual(await post('/api/shaders/play', { id: 'nxlx-bars.fs' }), 200);
    await page.waitForFunction(() => document.getElementById('speedlimit') && document.getElementById('shc-speed').max === '1', null, { timeout: 20000 });
    assert.strictEqual(await page.textContent('#fasterwarning'), 'Lets performance shaders flash faster than 3 times a second. This can trigger seizures in people with photosensitive epilepsy.');
    assert(await page.evaluate(() => document.getElementById('shaderadvanced').contains(document.getElementById('shaderfaster'))), 'the switch for faster is under Advanced');
    assert.strictEqual(await page.getAttribute('#shaderfaster', 'aria-checked'), 'false', 'and it is off until someone switches it on');
    if (!(await page.isVisible('#shaderfaster'))) await page.click('#shaderadvanced summary');
    await page.click('#shaderfaster');
    await page.waitForFunction(() => fetch('/api/shaders').then((r) => r.json()).then((d) => d.config.faster === true));
    await page.waitForFunction(() => document.getElementById('shc-speed').max === '4' && !document.getElementById('speedlimit'), null, { timeout: 20000 });
    await page.waitForSelector('#shaderfaster[aria-checked="true"]');
    await page.click('#shaderfaster');
    await page.waitForFunction(() => fetch('/api/shaders').then((r) => r.json()).then((d) => d.config.faster === false));
    await page.waitForFunction(() => document.getElementById('shc-speed').max === '1', null, { timeout: 20000 });
    // A set is renamed and deleted in place
    assert.strictEqual(await post('/api/shaders', { action: 'set', op: 'add', name: 'Temp', shaders: ['nxlx-silk.fs'] }), 200);
    await page.waitForSelector('#setlist .setrow:has-text("Temp")', { timeout: 20000 });
    await page.click('#setlist .setrow:has-text("Temp")');
    await page.waitForSelector('#setediting:text-is("Editing: Temp")');
    await page.click('#setrename');
    await page.fill('.renamerow input', 'Late');
    await page.click('.renamerow button:has-text("Save")');
    await page.waitForSelector('#setediting:text-is("Editing: Late")');
    await page.selectOption('#vibesorder', 'listed');
    await page.waitForFunction(() => fetch('/api/shaders').then((r) => r.json()).then((d) => d.sets.some((e) => e.name === 'Late' && e.order === 'listed')));
    await page.waitForSelector('#setdelete');
    await page.click('#setdelete');
    await page.waitForSelector('#confirmrow:has-text("Delete the set Late?")');
    await fitsPhone('Shaders page, asking about a set');
    await page.click('#confirmyes');
    await page.waitForFunction(() => document.querySelectorAll('#setlist .setrow').length === 2);
    assert.deepStrictEqual((await get('/api/shaders')).sets.map((e) => e.name), ['Ambient', 'Show']);
    // Live: the shader before and the next one beside the Vibes button, the set to play, and a strip of Speed and
    // the shader's first four controls
    assert.strictEqual(await post('/api/shaders/play', { id: AID }), 200);
    await page.click('nav >> text=Live');
    await page.waitForSelector('#liveshader:visible', { timeout: 20000 });
    await page.waitForFunction(() => document.getElementById('livename').textContent === 'All inputs', null, { timeout: 20000 });
    assert.deepStrictEqual(await page.$$eval('#livectls .ctl', (cs) => cs.map((x) => x.dataset.common || x.dataset.input)), ['speed', 'level', 'lit', 'mode', 'shape'], 'the strip: Speed and the first four controls');
    assert(await page.isVisible('#liveprev') && await page.isVisible('#vibesskip'), 'Previous and Next are beside the Vibes button while a shader is on');
    assert.strictEqual(await page.locator('#liveset option').count(), 2, 'and the set to play, since there are two');
    await setRange('live-level', 0.8, ['input', 'change']);
    await wasSent(VALUES, V({ level: 0.8 }), 'a slider on Live');
    await fitsPhone('Live with the shader strip');
    await fitsCard('#liveshader', 'the shader strip on Live');
    if (shots) await page.screenshot({ path: path.join(shots, '10-live-shader.png'), fullPage: true });
    assert.strictEqual(await post('/api/shaders/play', { id: 'nxlx-tide.fs' }), 200);
    await page.click('nav >> text=Live');
    await page.waitForFunction(() => /^Shader: Tide/.test((document.getElementById('np') || {}).textContent), null, { timeout: 8000 });
    assert.strictEqual(await page.textContent('#vibeswords'), 'Start Vibes');
    await page.click('#stop');
    await page.waitForFunction(() => /Player idle/.test((document.getElementById('np') || {}).textContent), null, { timeout: 8000 });

    // A presenter's System: the rows a presenter can use (the modules that are on), no switches, nothing disabled
    const presenterToken = await page.evaluate(() => fetch('/api/devices/invite', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-PVJ-Request': '1' },
      body: JSON.stringify({ name: 'Presenter', role: 'live' }) }).then((r) => r.json()).then((d) => d.token));
    const liveCtx = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const presenter = await liveCtx.newPage();
    presenter.on('console', (m) => { if (['error'].includes(m.type()) && !expected.test(m.text())) problems.push('presenter: ' + m.text()); });
    presenter.on('pageerror', (e) => problems.push('presenter pageerror: ' + e.message));
    await presenter.goto(base + '/#token=' + presenterToken);
    await presenter.waitForSelector('.pads');
    // A presenter's Shaders page: start, stop, skip, play one and move sliders; no switches, no settings, no upload
    await presenter.waitForSelector('#vibes');
    assert(!(await presenter.isDisabled('#vibes')), 'a presenter can use the Vibes button');
    await presenter.click('#shaderslink');
    await presenter.waitForSelector('#shadercard [data-shader="nxlx-tide.fs"]');
    assert.deepStrictEqual(await presenter.$$eval('#shaderpage .card', (cs) => cs.map((x) => x.id)), ['shadernow', 'shadercard'], 'a presenter gets no settings and no links to MIDI and DMX');
    assert.strictEqual(await presenter.locator('#syspage .switch').count(), 0, 'no module switch and no rotation switches for a presenter');
    assert.strictEqual(await presenter.locator('#shaderupload, #vibesdwell, #vibesvary, #shaderpage button:has-text("Remove")').count(), 0, 'no upload, settings or Remove for a presenter');
    assert.strictEqual(await presenter.locator('#shaderheight, #shaderguard, #setadd, #setlist, #shaderpage [data-midi], #shaderpage button:has-text("Put it back")').count(), 0, 'no picture detail, guard, sets to edit or MIDI teaching for a presenter');
    assert.strictEqual(await presenter.locator('#shadercard button[aria-label^="Play "]').count(), (await get('/api/shaders')).shaders.length, 'a presenter can play each shader');
    assert(/in Vibes/.test(await presenter.textContent('#shadercard [data-shader="nxlx-tide.fs"]')), 'a presenter reads whether a shader is in Vibes');
    assert.strictEqual(await presenter.locator('#syspage button:disabled').count(), 0, 'nothing disabled on a presenter\'s Shaders page');
    await presenter.click('#shadercard [data-shader="nxlx-tide.fs"] button[aria-label="Play Tide"]');
    await presenter.waitForFunction(() => (document.getElementById('shaderplaying') || {}).textContent === 'Tide');
    await presenter.waitForSelector('#shin-speed');                  // a presenter gets the controls
    // a presenter moves controls and applies presets, and saves none
    await presenter.waitForSelector('#nopresets:has-text("Someone with full access saves them")');
    assert.strictEqual(await presenter.locator('#presetsave, #presetname, #presetmore').count(), 0, 'a presenter cannot save, rename or delete a preset');
    assert.strictEqual(await post('/api/shaders/presets', { action: 'save', id: 'nxlx-tide.fs', name: 'Calm' }), 200);       // the owner saves one
    await presenter.waitForSelector('#presetrow [data-preset="Calm"]', { timeout: 20000 });
    {
      const applied = presenter.waitForResponse((r) => r.url().endsWith('/api/shaders/preset') && r.request().method() === 'POST');
      await presenter.click('#presetrow [data-preset="Calm"]');
      assert.strictEqual((await applied).status(), 200, 'a presenter applies a preset');
      const moved = presenter.waitForResponse((r) => r.url().endsWith('/api/shaders/values') && r.request().method() === 'POST');
      await presenter.evaluate(() => { const el = document.getElementById('shc-hue'); el.value = 30; el.dispatchEvent(new Event('input')); el.dispatchEvent(new Event('change')); });
      assert.strictEqual((await moved).status(), 200, 'a presenter moves a control');
    }
    assert.strictEqual(await post('/api/shaders/presets', { action: 'delete', id: 'nxlx-tide.fs', name: 'Calm' }), 200);
    // and chooses the set Vibes plays (there are two), without changing which one is the usual one
    await presenter.selectOption('#vibesset', { label: 'Ambient' });
    await presenter.waitForSelector('#vibesbtn:text-is("Start Vibes")');
    await presenter.click('#vibesbtn');
    await presenter.waitForSelector('#vibesbtn:text-is("Stop Vibes")', { timeout: 15000 });
    await presenter.waitForFunction(() => fetch('/api/shaders').then((r) => r.json()).then((d) => d.vibes.running && d.vibes.set.name === 'Ambient' && d.sets.find((e) => e.id === d.active).name === 'Show'), null, { timeout: 20000 });
    await presenter.waitForSelector('#shadernext');
    await presenter.click('#vibesbtn');
    await presenter.waitForSelector('#vibesbtn:text-is("Start Vibes")', { timeout: 15000 });
    {
      const wide = await presenter.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      assert(wide <= 1, 'a presenter\'s Shaders page: ' + wide + ' px wider than the phone');
    }
    await presenter.click('#sysback');
    await presenter.waitForSelector('.pads');
    await presenter.click('nav >> text=System');
    await presenter.waitForSelector('#sysindex');
    assert.deepStrictEqual(await presenter.$$eval('.navname', (ns) => ns.map((x) => x.textContent)),
      ['Health', 'Projectors', 'Shaders and Vibes', 'People and codes', 'Sound', 'Streams', 'Boxes in step', 'About and power'], 'the rows a presenter sees');
    assert.strictEqual(await presenter.locator('#notbuilt').count(), 0, 'a presenter gets no list of modules');
    // A presenter's People and codes (D48): the guest code and nothing else. The owner has a guest code and a
    // presenter code active; the presenter sees the first, is asked before replacing or ending it, and never sees
    // the second.
    assert.strictEqual(await post('/api/access/code', { role: 'view', minutes: 60 }), 200);
    assert.strictEqual(await post('/api/access/code', { role: 'live', minutes: 60 }), 200);
    const ownersCodes = (await get('/api/access')).codes;
    const ownersGuest = ownersCodes.filter((c) => c.role === 'view')[0].code, ownersPresenter = ownersCodes.filter((c) => c.role === 'live')[0].code;
    await presenter.click('.navrow:has-text("People and codes")');
    await presenter.waitForSelector('#accesscard .join-code[data-role="view"]');
    assert.strictEqual(await presenter.textContent('#accesscard h2'), 'Let someone in');
    assert.strictEqual(await presenter.locator('#devicescard, #newpresenter, #printsheet, #show-pin, #show-live, #makelink, #unlockpair, .join-code[data-role="live"]').count(), 0,
      'a presenter gets no devices, presenter code, PIN, link or print sheet');
    assert.strictEqual(await presenter.locator('#syspage .switch').count(), 0, 'and no switch');
    assert.strictEqual(await presenter.textContent('.join-code .big-code'), ownersGuest, 'a presenter sees the owner\'s guest code');
    assert(/Made by the owner/.test(await presenter.textContent('.join-code')), 'and is told who made it');
    assert(!(await presenter.content()).includes(ownersPresenter), 'the presenter code is nowhere on a presenter\'s page');
    assert(/Guest \(can watch\)/.test(await presenter.textContent('#accesscard')) && !/watch only|View only/.test(await presenter.textContent('#accesscard')), 'the one vocabulary for roles');
    assert.deepStrictEqual(await presenter.$$eval('#joinminutes option', (os) => os.map((o) => o.textContent)), ['15 minutes', '1 hour', '2 hours']);
    assert.strictEqual(await presenter.inputValue('#joinminutes'), '60', 'one hour unless chosen otherwise');
    await presenter.waitForFunction(() => { const i = document.querySelector('.join-code img.qr'); return i && i.complete && i.naturalWidth > 0; });
    assert(!(await presenter.getAttribute('.join-code img.qr', 'src')).includes(ownersGuest), 'the code is not in the QR picture\'s address');
    await presenter.click('.endcode');                               // ending the owner's code asks first
    await presenter.waitForSelector('#confirmrow:has-text("The owner made this code")');
    await presenter.click('#confirmno');
    await presenter.waitForSelector('.join-code .big-code');
    assert.strictEqual((await get('/api/access')).codes.filter((c) => c.role === 'view')[0].code, ownersGuest, '"Keep it" ended nothing');
    await presenter.selectOption('#joinminutes', '120');
    await presenter.click('#newguest');                              // so does replacing it
    await presenter.waitForSelector('#confirmrow:has-text("The owner made the code that is active")');
    const [guestReq] = await Promise.all([presenter.waitForRequest((r) => r.url().endsWith('/api/access/code')), presenter.click('#confirmyes')]);
    assert.deepStrictEqual(JSON.parse(guestReq.postData()), { role: 'view', minutes: 120, replace: true }, 'the chosen two hours are sent');
    await presenter.waitForFunction((old) => { const c = document.querySelector('.join-code .big-code'); return c && /^[0-9]{6}$/.test(c.textContent) && c.textContent !== old; }, ownersGuest);
    const staffCode = await presenter.textContent('.join-code .big-code');
    assert(/Works for 1(19|20):[0-9]{2} more/.test(await presenter.textContent('.join-code')), 'the new code works for two hours: ' + await presenter.textContent('.join-code'));
    assert(!/Made by the owner/.test(await presenter.textContent('.join-code')), 'the presenter\'s own code is not called the owner\'s');
    const after = (await get('/api/access')).codes;
    assert.deepStrictEqual(after.map((c) => [c.role, c.by]).sort(), [['live', 'owner'], ['view', 'presenter']], 'one guest code, the presenter\'s; the presenter code untouched');
    assert.strictEqual(after.filter((c) => c.role === 'live')[0].code, ownersPresenter);
    await presenter.selectOption('#showsecs', '60');
    await presenter.click('#showaccess');
    await presenter.waitForFunction(() => /On the room screen now: the Guest \(can watch\) code/.test(document.getElementById('accessscreenline').textContent));
    assert.deepStrictEqual((await get('/api/access')).screen.items, ['view'], 'only the guest code went on the room screen');
    assert.strictEqual(await presenter.locator('#syspage button:disabled').count(), 0, 'nothing disabled on a presenter\'s People and codes');
    {
      const wide = await presenter.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      assert(wide <= 1, 'a presenter\'s People and codes: ' + wide + ' px wider than the phone');
    }
    await presenter.click('#hideaccess');
    await presenter.waitForFunction(() => /Nothing on the room screen/.test(document.getElementById('accessscreenline').textContent));
    // what the page does not offer, the box refuses: asked straight from the presenter's browser
    const refused = await presenter.evaluate((digits) => Promise.all([
      ['/api/access/code', { role: 'live' }], ['/api/access/code', { role: 'view', minutes: 121, replace: true }], ['/api/access/code', { role: 'view', uses: 21, replace: true }],
      ['/api/access/cancel', { all: true }], ['/api/access/cancel', { role: 'live' }], ['/api/access/cancel', { code: digits }],
      ['/api/access/screen', { show: true, items: ['pin'] }], ['/api/access/screen', { show: true, items: ['view', 'live'] }],
      ['/api/devices/invite', { name: 'x', role: 'view' }], ['/api/devices/revoke', { id: 'x' }], ['/api/pin/rotate', {}], ['/api/pin/unlock', {}]
    ].map((x) => fetch(x[0], { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-PVJ-Request': '1' }, body: JSON.stringify(x[1]) }).then((r) => r.status))), ownersPresenter);
    assert.deepStrictEqual(refused, [403, 400, 400, 403, 403, 403, 403, 403, 403, 403, 403, 403], 'what a presenter is refused');
    assert.strictEqual(await presenter.evaluate(() => fetch('/api/qr.svg?for=live').then((r) => r.status)), 403, 'no QR code of the presenter code for a presenter');
    assert.strictEqual(await presenter.evaluate(() => fetch('/api/access/code', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{"role":"view","replace":true}' }).then((r) => r.status)), 403,
      'and nothing without the request header (CSRF)');
    assert.strictEqual((await get('/api/access')).codes.filter((c) => c.role === 'view')[0].code, staffCode, 'none of that changed the guest code');
    await presenter.click('.endcode');
    await presenter.waitForSelector('#confirmrow:has-text("End this code?")');
    assert(!/The owner made/.test(await presenter.textContent('#confirmrow')), 'ending the presenter\'s own code does not mention the owner');
    await presenter.click('#confirmyes');
    await presenter.waitForSelector('#nocodes');
    assert.deepStrictEqual((await get('/api/access')).codes.map((c) => c.role), ['live'], 'the guest code is ended, the presenter code is still the owner\'s');
    assert.strictEqual(await post('/api/access/cancel', { all: true }), 200);
    await presenter.click('#sysback');
    await presenter.waitForSelector('#sysindex');
    await presenter.waitForSelector(`${rowOf('People and codes')} .navstate:has-text("No guest code")`);
    await presenter.click('.navrow:has-text("Projectors")');
    await presenter.waitForSelector('#projline');
    assert.strictEqual(await presenter.locator('.switch').count(), 0, 'a presenter gets no switch');
    assert.strictEqual(await presenter.locator('#projadd, #projopen').count(), 0, 'and no form to add a projector');
    await presenter.click('#sysback');
    await presenter.waitForSelector('#sysindex');
    // A presenter's view of every page this pass changed, on a phone and on a laptop: it fits, and nothing on it sets
    // the box up (no switch, no Add form, no Save, no Edit or Remove, no power action)
    for (const width of [390, 1366]) {
      await presenter.setViewportSize({ width, height: 844 });
      for (const [name, marker] of [['Health', '#healthpower'], ['Projectors', '#projline'], ['People and codes', '#accesshint'], ['Sound', '#audioline'],
        ['Streams', '#streamempty, .stream-entry'], ['Boxes in step', '#syncline'], ['About and power', '#boxcard .kvv']]) {
        await presenter.click(`.navrow:has(.navname:text-is("${name}"))`);
        await presenter.waitForSelector(marker);
        await fitsOn(presenter, `a presenter's ${name} page at ${width}`);
        assert.strictEqual(await presenter.locator('#sysswitch, .addform, .addopen, .savebar, #powercard, #audiodev, #syncroles, #devicescard, #syspage button:text-is("Remove"), #syspage button:text-is("Edit")').count(), 0,
          `nothing to set the box up with on a presenter's ${name} page`);
        await presenter.click('#sysback');
        await presenter.waitForSelector('#sysindex');
      }
    }
    await presenter.click(`.navrow:has(.navname:text-is("Sound"))`);
    await presenter.waitForSelector('#tone-both');
    assert.strictEqual(await presenter.locator('#tonerow button:disabled').count(), 0, 'a presenter can play the test sound');
    await presenter.click('#sysback');
    await presenter.waitForSelector('#sysindex');
    await liveCtx.close();

    // Ambience on the Room screen (the Vibes rotation under the name staff use): a presenter lands on Room, starts it
    // there, reads the shader's name, goes to the next one and stops it. A guest reads the state and has nothing to
    // press. Nothing sticks out on a phone. Room is switched on for this step only.
    assert.strictEqual(await post('/api/modules/room', { enabled: true }), 200);
    {
      const tokens = await page.evaluate(() => Promise.all(['live', 'view'].map((role) => fetch('/api/devices/invite', { method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-PVJ-Request': '1' }, body: JSON.stringify({ name: 'ambience ' + role, role: role }) }).then((r) => r.json()).then((d) => d.token))));
      const open = async (token, who) => {
        const c = await browser.newContext({ viewport: { width: 390, height: 844 } });
        const pg = await c.newPage();
        pg.on('console', (m) => { if (['error'].includes(m.type()) && !expected.test(m.text())) problems.push(who + ': ' + m.text()); });
        pg.on('pageerror', (e) => problems.push(who + ' pageerror: ' + e.message));
        await pg.goto(base + '/#token=' + token);
        await pg.waitForSelector('#roomscreen #roomambience', { timeout: 15000 });
        return pg;
      };
      const vibesPost = (pg, body) => pg.waitForResponse((r) => r.url().endsWith('/api/vibes') && r.request().method() === 'POST' && r.request().postData() === body);
      // The owner, and only the owner, also reads on Room that the saved picture detail is above this board's usual
      // one, with the one tap that puts it back
      const shaderState = await get('/api/shaders');
      const usual = shaderState.render.default, above = shaderState.render.heights.find((x) => x > usual);
      assert.strictEqual(await post('/api/shaders', { action: 'config', height: above }), 200);
      await page.setViewportSize({ width: 390, height: 844 });
      await page.reload();
      await page.waitForSelector('nav >> text=Room');
      await page.click('nav >> text=Room');
      await page.waitForSelector('#roomscreen #roomambdetail:visible', { timeout: 15000 });
      assert.strictEqual(await page.textContent('#roomambdetailwords'), 'Picture detail ' + above + ' is high here.');
      assert.strictEqual(await page.textContent('#roomambuse'), 'Use ' + usual);
      {
        const one = await page.evaluate(() => { const w = document.getElementById('roomambdetailwords'), b = document.getElementById('roomambuse').getBoundingClientRect(), r = w.getBoundingClientRect();
          return { lines: r.height / parseFloat(getComputedStyle(w).lineHeight), beside: b.left >= r.right - 1 && b.top < r.bottom && b.bottom > r.top }; });
        assert(one.lines < 1.5 && one.beside, 'the picture detail line on Room is one line with its button beside it: ' + JSON.stringify(one));
      }
      await fitsOn(page, "the owner's Room screen with the picture detail line");
      const staff = await open(tokens[0], 'ambience presenter');
      await staff.waitForSelector('#roomambset:visible');
      assert.strictEqual(await staff.locator('#roomambdetail, #roomambuse').count(), 0, 'a presenter is not told about the picture detail');
      await page.click('#roomambuse');
      await page.waitForFunction(() => document.getElementById('roomambdetail').hidden, null, { timeout: 15000 });
      assert.strictEqual((await get('/api/shaders')).config.height, usual, 'Use ' + usual + ' on Room applies on tap');
      assert.strictEqual(await post('/api/shaders', { action: 'config', height: shaderState.config.height }), 200);
      assert.strictEqual(await staff.textContent('#roomambwords'), 'Start ambience');
      assert.strictEqual(await staff.getAttribute('#roomamb', 'aria-pressed'), 'false');
      assert((await staff.evaluate(() => document.getElementById('roomamb').getBoundingClientRect().height)) >= 56, 'the ambience button is at least 56 px high');
      assert(!(await staff.isVisible('#roomambnext')), 'no Next one while nothing plays');
      await staff.waitForSelector('#roomambset:visible');           // two sets: a chooser beside the button
      assert.deepStrictEqual(await staff.$$eval('#roomambset option', (os) => os.map((o) => o.textContent)), ['Set: Ambient', 'Set: Show']);
      const started = vibesPost(staff, '{"on":true}');             // the usual set: nothing is said about sets
      await staff.click('#roomamb');
      assert.strictEqual((await started).status(), 200, 'a presenter starts ambience on the Room screen');
      await staff.waitForFunction(() => /^Ambience is playing: [A-Z].*\.$/.test((document.getElementById('roomambwords') || {}).textContent), null, { timeout: 20000 });
      assert.strictEqual(await staff.textContent('#roomambsub'), 'Tap to stop');
      assert.strictEqual(await staff.getAttribute('#roomamb', 'aria-pressed'), 'true');
      assert((await get('/api/shaders')).vibes.running, 'the rotation is running on the box');
      const guest2 = await open(tokens[1], 'ambience guest');
      await guest2.waitForFunction(() => /^Ambience is playing: [A-Z].*\.$/.test((document.getElementById('roomambstate') || {}).textContent), null, { timeout: 20000 });
      assert.strictEqual(await guest2.locator('#roomamb, #roomambnext, #roomambset, #roomambience button, #roomambience select').count(), 0, 'a guest reads the state and has nothing to press');
      await fitsOn(guest2, "a guest's Room screen while ambience plays");
      await staff.waitForSelector('#roomambnext:visible');
      assert((await staff.evaluate(() => document.getElementById('roomambnext').getBoundingClientRect().height)) >= 56, 'Next one is easy to hit');
      const nexted = vibesPost(staff, '{"next":true}');
      await staff.click('#roomambnext');
      assert.strictEqual((await nexted).status(), 200, 'Next one goes to the next shader');
      await staff.waitForFunction(() => /^Ambience is playing: [A-Z]/.test((document.getElementById('roomambwords') || {}).textContent), null, { timeout: 20000 });
      await fitsOn(staff, "a presenter's Room screen while ambience plays");
      if (shots) await staff.screenshot({ path: path.join(shots, '9-room-ambience.png') });
      const stopped = vibesPost(staff, '{"on":false}');
      await staff.click('#roomamb');
      assert.strictEqual((await stopped).status(), 200, 'a tap stops it');
      await staff.waitForFunction(() => (document.getElementById('roomambwords') || {}).textContent === 'Start ambience');
      await page.waitForFunction(() => fetch('/api/shaders').then((r) => r.json()).then((d) => !d.vibes.running), null, { timeout: 20000 });
      await guest2.waitForFunction(() => (document.getElementById('roomambstate') || {}).textContent === 'Ambience is not playing.', null, { timeout: 20000 });
      await staff.waitForFunction(() => document.getElementById('roomambnext').hidden && document.getElementById('roomamb').getAttribute('aria-pressed') === 'false', null, { timeout: 20000 });
      await fitsOn(staff, "a presenter's Room screen with ambience stopped");
      await staff.context().close();
      await guest2.context().close();
    }
    await page.click('nav >> text=Live');                // off the Room screen before its module goes, so it asks nothing more
    await page.waitForSelector('.pads');
    assert.strictEqual(await post('/api/modules/room', { enabled: false }), 200);

    // Effects: a filter over what plays. Live has a compact strip (the effect's name, Previous, On or Off, Next, and
    // Amount while one is on); Mix has the card with the list, the controls of the one that is on and its presets.
    // The harness player draws nothing (--vo=null), so this checks the panel and the API, not the picture; that is
    // tests/test_effects_gpu.py.
    {
      const fxName = (id, want) => page.waitForFunction(([i, w]) => (document.getElementById(i) || {}).textContent === w, [id, want], { timeout: 20000 });
      assert.strictEqual(await post('/api/control', { action: 'stop' }), 200);
      await page.click('nav >> text=Live');
      await page.waitForSelector('#livefxwhy', { timeout: 20000 });
      assert(/Nothing with a picture is playing/.test(await page.textContent('#livefxwhy')), 'with nothing playing the strip says why no effect can go on');
      assert((await page.isDisabled('#livefxon')) && (await page.isDisabled('#livefxnext')) && (await page.isDisabled('#livefxprev')), 'and its buttons are not to be pressed');
      assert.strictEqual(await post('/api/play', { file: 'intro.mkv' }), 200);
      await page.waitForSelector('#livefxon:not([disabled])', { timeout: 20000 });
      assert.strictEqual(await page.textContent('#livefxname'), 'None');
      assert.strictEqual(await page.locator('#live-fx-amount').count(), 0, 'no Amount while no effect is on');
      const low = await page.$$eval('#livefx button', (els) => els.filter((e) => e.getBoundingClientRect().height < 44).map((e) => e.id));
      assert.deepStrictEqual(low, [], 'every button of the strip is at least 44 px high');
      await fitsPhone('Live with the effects strip');
      // the way to the card on Mix
      await page.click('#livefxmore');
      await page.waitForSelector('#fxlist [data-effect="fx-vignette.fs"]', { timeout: 20000 });
      assert.strictEqual(await page.textContent('nav button[aria-current="page"]'), 'Mix', 'the Effects link on Live opens Mix');
      const fxs = (await get('/api/effects')).effects;
      assert(fxs.length >= 29 && fxs.filter((s) => s.pack === 'nxlx').length === 12 && fxs.every((s) => !s.error), 'the project\'s twelve filters and the pack are listed');
      assert.strictEqual(await page.locator('#fxlist [data-effect]').count(), fxs.length, 'every filter has a row');
      assert.strictEqual(await page.textContent('#fxname'), 'No effect is on');
      assert.strictEqual(await page.textContent('#fxchip'), 'Off');
      assert(/Medium work on a Pi 4: holds a 720p clip at full size, a 1080p clip at (720|540) lines/.test(await page.textContent('#fxlist [data-effect="fx-vignette.fs"]')), 'a filter says how much work it is, as a Pi 4 measured it');
      assert(/Light work on a Pi 4: holds a 1080p clip/.test(await page.textContent('#fxlist [data-effect="isf-duotone.fs"]')), 'the one filter that held a 1080p clip at full size says so');
      assert(/measured on a Raspberry Pi 4/.test(await page.textContent('#fxworknote')), 'the list says where its words about work come from');
      assert(/from the isf-files pack, by VIDVOX/.test(await page.textContent('#fxlist [data-effect="isf-mirror.fs"]')), 'somebody else\'s filter says whose it is');
      assert(/moves by itself/.test(await page.textContent('#fxlist [data-effect="fx-kaleido.fs"]')), 'a filter that moves by itself says so');
      // the list by name and by weight
      await page.fill('#fxfilter', 'vign');
      await page.waitForFunction(() => document.querySelectorAll('#fxlist [data-effect]').length === 1);
      await page.fill('#fxfilter', 'no such effect');
      await page.waitForSelector('#fxnone');
      await page.fill('#fxfilter', '');
      await page.click('#fxweight [data-weight="medium"]');
      const medium = await page.locator('#fxlist [data-effect]').count();
      assert(medium >= 1 && medium < fxs.length, 'the work filter narrows the list: ' + medium);
      await page.click('#fxweight [data-weight="all"]');
      await page.waitForFunction((n) => document.querySelectorAll('#fxlist [data-effect]').length === n, fxs.length);
      // Put on: the clip plays on, the card shows the effect with Amount first, then its own controls by type
      const put = page.waitForResponse((r) => r.url().endsWith('/api/effects') && r.request().method() === 'POST');
      await page.click('#fxlist [data-put="fx-vignette.fs"]');
      assert.strictEqual((await put).status(), 200, 'Put on is taken');
      await fxName('fxname', 'Vignette');
      assert.strictEqual(await page.textContent('#fxchip'), 'On');
      assert((await get('/api/status')).player.path, 'the clip is still what plays');
      assert.deepStrictEqual(await page.$$eval('#fxctls > .ctl', (els) => els.slice(0, 2).map((e) => e.getAttribute('data-common') || 'input')), ['amount', 'input'],
        'Amount is the first control, then the filter\'s own (a filter that does not move has no Speed)');
      assert.strictEqual(await page.locator('#fx-half').count(), 0, 'the Half resolution switch is gone: Effect detail took its place');
      // Effect detail: one setting for the box, the size an effect works at. It applies on tap and is kept.
      {
        const det = (await get('/api/effects')).detail;
        assert.deepStrictEqual(det.choices, ['auto', 540, 720, 'full']);
        assert.strictEqual(det.value, det.default, 'nobody chose yet: the board\'s own default');
        assert.strictEqual(await page.inputValue('#fxdetailpick'), String(det.value), 'the picker shows what is in force');
        assert.deepStrictEqual(await page.$$eval('#fxdetailpick option', (els) => els.map((e) => e.textContent)),
          ['Automatic' + (det.default === 'auto' ? ' (recommended on this box)' : ''), '540 lines', '720 lines', 'Full']);
        assert(/fine patterns \(dots, lines, tiles\) look larger/.test(await page.textContent('#fxdetailnote')), 'the price in look is said');
        const lineIs = (re) => page.waitForFunction((src) => new RegExp(src).test((document.getElementById('fxworking') || {}).textContent || ''), re.source, { timeout: 20000 });
        const clip = (await get('/api/effects')).on.working.clip;
        assert(clip && clip.lines > 0 && clip.lines <= 540, 'the test clip is a small one: ' + JSON.stringify(clip));
        const other = det.value === 540 ? 720 : 540;
        const saved = page.waitForResponse((r) => r.url().endsWith('/api/effects/config') && r.request().method() === 'POST');
        await page.selectOption('#fxdetailpick', String(other));
        assert.strictEqual((await saved).status(), 200, 'a choice is sent as it is made');
        await lineIs(new RegExp('^Working at full size for this ' + clip.lines + 'p clip \\(it is within ' + other + ' lines\\)\\.$'));
        assert.strictEqual((await get('/api/effects')).detail.value, other);
        await page.reload();
        await page.click('nav >> text=Mix');
        await page.waitForSelector('#fxdetailpick', { timeout: 20000 });
        assert.strictEqual(await page.inputValue('#fxdetailpick'), String(other), 'the choice is kept');
        const full = page.waitForResponse((r) => r.url().endsWith('/api/effects/config') && r.request().method() === 'POST');
        await page.selectOption('#fxdetailpick', 'full');
        assert.strictEqual((await full).status(), 200);
        await lineIs(new RegExp('^Working at full size for this ' + clip.lines + 'p clip\\.$'));
        const back = page.waitForResponse((r) => r.url().endsWith('/api/effects/config') && r.request().method() === 'POST');
        await page.selectOption('#fxdetailpick', String(det.default));
        assert.strictEqual((await back).status(), 200);
        await page.waitForFunction((want) => fetch('/api/effects').then((r) => r.json()).then((d) => d.detail.value === want), det.default, { timeout: 15000 });
        if (det.default === 'auto') await lineIs(/^Automatic: working at /);
        await fxName('fxname', 'Vignette');
      }
      assert.deepStrictEqual(await page.$$eval('#fxctls > .ctl[data-input]', (els) => els.map((e) => e.getAttribute('data-type'))),
        ['float', 'float', 'float', 'long', 'point2D', 'color'], 'its own inputs are drawn by type');
      assert.strictEqual(await page.locator('#fxlist [data-effect="fx-vignette.fs"] [data-off]').count(), 1, 'its row offers Off');
      const sent = page.waitForResponse((r) => r.url().endsWith('/api/effects/values'));
      await page.$eval('#fx-amount', (el) => { el.value = 0.4; el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true })); });
      assert.strictEqual((await sent).status(), 200, 'a move of Amount is sent');
      await page.waitForFunction(() => fetch('/api/effects').then((r) => r.json()).then((d) => d.on && d.on.controls.amount === 0.4), null, { timeout: 15000 });
      // the MIDI buttons are beside what they drive (the owner's)
      for (const a of ['effect_amount', 'effect_prev', 'effect_toggle', 'effect_next', 'effect_control_1', 'effect_control_4']) {
        assert.strictEqual(await page.locator('#fxcard [data-midi="' + a + '"]').count(), 1, 'a MIDI button for ' + a);
      }
      assert.strictEqual(await page.locator('#fxctls [data-input="centre"] [data-midi], #fxctls [data-input="edge"] [data-midi]').count(), 0, 'a point and a colour have no knob');
      const small = await page.$$eval('#fxcard button, #fxcard select, #fxcard input[type=range], #fxcard input[type=search], #fxcard input[type=text]',
        (els) => els.filter((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height < 44; }).map((e) => (e.getAttribute('aria-label') || e.textContent || e.id).slice(0, 30)));
      assert.deepStrictEqual(small, [], 'every control of the Effects card is at least 44 px high');
      await fitsPhone('Mix with an effect on');
      if (shots) await page.screenshot({ path: path.join(shots, '10-mix-effects.png'), fullPage: true });
      // a preset of this filter
      await page.fill('#fxpresetname', 'Soft');
      await page.click('#fxpresetsave');
      await page.waitForSelector('#fxpresets [data-preset="Soft"]', { timeout: 15000 });
      // Live: the strip has the name, Amount and Off, and Now playing says an effect is on
      await page.click('nav >> text=Live');
      await fxName('livefxname', 'Vignette');
      await page.waitForSelector('#live-fx-amount');
      await page.waitForFunction(() => /effect: Vignette/.test((document.getElementById('np') || {}).textContent), null, { timeout: 15000 });
      assert.strictEqual(await page.locator('#livefxoff').count(), 1, 'Off is on the strip while an effect is on');
      await fitsPhone('Live with an effect on');
      const stepped = page.waitForResponse((r) => r.url().endsWith('/api/effects/step') && r.request().postData() === '{"dir":1}');
      await page.click('#livefxnext');
      assert.strictEqual((await stepped).status(), 200, 'Next on Live goes to the next effect');
      await fxName('livefxname', 'Wash');
      // a generator shader takes the screen: the effect stays on, over it (D74), and both places go on offering everything
      assert.strictEqual(await post('/api/shaders/play', { id: 'nxlx-tide.fs' }), 200);
      await page.waitForFunction(() => /Shader: Tide.*effect: Wash/.test((document.getElementById('np') || {}).textContent), null, { timeout: 20000 });
      await fxName('livefxname', 'Wash');
      assert.strictEqual(await page.locator('#livefxwhy').count(), 0, 'the strip has no reason why none can go on: one is on, over the shader');
      assert.strictEqual(await page.locator('#livefxoff').count(), 1, 'Off is on the strip while an effect is on over a shader');
      assert(!(await page.isDisabled('#livefxnext')), 'Next is offered over a shader');
      let over = null;                                                 // the worker's look at the picture under the effect comes once a second
      for (let i = 0; i < 40; i++) {
        over = await get('/api/effects');
        if (over.on && over.on.working.under === 'shader') break;
        await page.waitForTimeout(250);
      }
      assert.deepStrictEqual([over.available, over.on && over.on.id, over.on && over.on.working.under], [true, 'fx-wash.fs', 'shader']);
      await fitsPhone('Live with an effect on over a shader');
      await page.setViewportSize({ width: 320, height: 844 });         // the narrowest phone: no text runs out of its card
      await fitsOn(page, 'Live at 320 with an effect on over a shader');
      await page.click('#livefxmore');
      await page.waitForSelector('#fxrule', { timeout: 20000 });
      await fxName('fxname', 'Wash');
      assert.strictEqual(await page.locator('#fxwhy').count(), 0, 'the card has no reason why none can go on');
      assert(/over a shader and Vibes; Stop takes it off/.test(await page.textContent('#fxrule')), 'the card says the effect stays over a shader');
      await page.waitForFunction(() => /line shader/.test((document.getElementById('fxworking') || {}).textContent || ''), null, { timeout: 20000 });
      await fitsOn(page, 'the effect card at 320 with an effect on over a shader');
      await page.setViewportSize({ width: 390, height: 844 });
      // another effect is put on over the shader from the list, and the shader plays on under it
      assert(!(await page.isDisabled('#fxlist [data-put="fx-vignette.fs"]')), 'Put on is offered over a shader');
      await page.click('#fxlist [data-put="fx-vignette.fs"]');
      await fxName('fxname', 'Vignette');
      const st = (await get('/api/status')).player;
      assert.deepStrictEqual([st.shader, st.effect], ['nxlx-tide', 'fx-vignette'], 'the shader plays on under the new effect');
      // a clip again: an effect goes on and comes off with Off
      assert.strictEqual(await post('/api/play', { file: 'intro.mkv' }), 200);
      await page.waitForSelector('#fxlist [data-put="fx-wash.fs"]:not([disabled])', { timeout: 20000 });
      await page.click('#fxlist [data-put="fx-wash.fs"]');
      await page.waitForSelector('#fxoff', { timeout: 15000 });
      await page.click('#fxoff');
      await fxName('fxname', 'No effect is on');
      assert.strictEqual((await get('/api/effects')).on, null);
      assert.strictEqual(await post('/api/effects/presets', { action: 'delete', id: 'fx-vignette.fs', name: 'Soft' }), 200);
      assert.strictEqual(await post('/api/control', { action: 'stop' }), 200);
      await page.click('nav >> text=Live');
      await page.waitForSelector('.pads');
    }

    // A pad can hold a generator shader instead of a clip (D73): the pad editor offers Clip or Shader and then the
    // list; the pad says Shader and which; a tap shows it as choosing it on the Shaders page does; and a name of any
    // length stays inside the pad, at a phone's narrowest width too.
    {
      assert(await moduleIsOn('shaders'), 'the shaders module is on for the shader pad');
      await page.click('nav >> text=Live');
      await page.waitForSelector('.pads');
      await page.click('text=Edit pads');
      await page.click('.pad >> nth=5');
      await page.waitForSelector('#padkind');
      assert(await page.isVisible('#padending'), 'the editor of an empty pad opens on Clip');
      assert.strictEqual(await page.getAttribute('#padkindclip', 'aria-pressed'), 'true');
      await page.click('#padkindshader');
      await page.waitForSelector('#padshaders .padpick');
      assert(!(await page.isVisible('#padending')), 'a shader has no ending to choose');
      assert(!(await page.isVisible('.sheet >> text=intro.mkv')), 'the clips are put away while the shaders show');
      await page.click('#padkindclip');
      assert(await page.isVisible('.sheet >> text=intro.mkv'), 'and come back with Clip');
      await page.click('#padkindshader');
      await page.click('#padshaders .padpick:has-text("Aurora")');
      await page.waitForSelector('.pad.shader');
      await page.click('text=Done editing');
      const shaderPad = page.locator('.pad >> nth=5');
      assert.strictEqual(await shaderPad.locator('.padkind').textContent(), 'Shader');
      assert.strictEqual(await shaderPad.locator('.t').textContent(), 'Aurora');
      assert.strictEqual(await shaderPad.getAttribute('title'), 'Shader: Aurora');
      assert.deepStrictEqual((await get('/api/pads')).banks[0].pads[5], { label: 'Aurora', file: '', shader: 'nxlx-aurora.fs' });
      await shaderPad.click();
      await page.waitForFunction(() => /Shader: Aurora/.test(document.getElementById('np').textContent), null, { timeout: 20000 });
      await page.waitForSelector('.pad.shader.on');
      assert.strictEqual((await get('/api/shaders')).playing.id, 'nxlx-aurora.fs');
      // its editor opens on Shader, with its own row marked
      await page.click('text=Edit pads');
      await shaderPad.click();
      await page.waitForSelector('#padshaders .padpick.on:has-text("Aurora")');
      assert.strictEqual(await page.getAttribute('#padkindshader', 'aria-pressed'), 'true');
      await page.click('.sheet >> text=Cancel');
      await page.click('text=Done editing');
      // a clip pad after it takes the screen, and the shader pad is no longer the one that is on
      await page.click('.pad >> nth=0');
      await page.waitForFunction(() => /intro/.test(document.getElementById('np').textContent), null, { timeout: 15000 });
      await page.waitForFunction(() => !document.querySelector('.pad.shader.on'));
      // The two together (D73 and D74): with an effect on over the clip, the shader pad's tap brings its shader up
      // under the effect. The pad is the one playing, Now playing names both, and the effect's strip still has Off.
      {
        assert.strictEqual(await post('/api/effects', { id: 'fx-wash.fs' }), 200);
        await page.waitForFunction(() => /effect: Wash/.test(document.getElementById('np').textContent), null, { timeout: 15000 });
        await shaderPad.click();
        await page.waitForFunction(() => /Shader: Aurora.*effect: Wash/.test(document.getElementById('np').textContent), null, { timeout: 20000 });
        await page.waitForSelector('.pad.shader.on');
        const both = (await get('/api/status')).player;
        assert.deepStrictEqual([both.shader, both.effect, both.shader_refused], ['nxlx-aurora', 'fx-wash', undefined]);
        assert.strictEqual(await page.locator('#livefxoff').count(), 1, 'Off is on the strip while the effect is on over the pad\'s shader');
        assert.strictEqual(await page.locator('#padrefused').count(), 0, 'nothing is said under the pads: nothing was refused');
        await fitsPhone('Live with a shader pad playing under an effect');
        assert.strictEqual(await post('/api/effects', { off: true }), 200);
        await page.waitForSelector('.pad.shader.on');                    // the effect off: the pad's shader plays on, and is still the one playing
        await page.click('.pad >> nth=0');
        await page.waitForFunction(() => /intro/.test(document.getElementById('np').textContent), null, { timeout: 15000 });
        await page.waitForFunction(() => !document.querySelector('.pad.shader.on'));
      }
      // A pad's shader that the box's graphics chip refused is said where the pads are: one line under them, and
      // the pad marked. (The box's answer is given a refusal here: the harness's player refuses nothing.)
      {
        const message = "the player refused nxlx-aurora.fs: line 12: `oops' undeclared. The screen is black.";
        assert.strictEqual(await page.locator('#padrefused').count(), 0, 'nothing is said while nothing was refused');
        let refused = true;
        await page.route('**/api/status', async (route) => {
          const response = await route.fetch();
          const body = await response.json();
          if (refused) body.player.shader_refused = { id: 'nxlx-aurora.fs', message, at: '2026-10-09 12:00:00' };
          await route.fulfill({ response, json: body });
        });
        await page.waitForSelector('#padrefused');
        assert.strictEqual(await page.textContent('#padrefused'), "A pad's shader was not shown. The box refused nxlx-aurora.fs: line 12: `oops' undeclared. The screen is black.");
        assert.deepStrictEqual(await page.$$eval('.pad', (els) => els.map((el, i) => (el.classList.contains('refused') ? i : -1)).filter((i) => i >= 0)), [5]);
        const fits = await page.evaluate(() => {
          const line = document.getElementById('padrefused').getBoundingClientRect(), pads = document.getElementById('pads').getBoundingClientRect();
          return { right: line.right, wide: window.innerWidth, page: document.documentElement.scrollWidth, under: line.top >= pads.bottom - 1 };
        });
        assert(fits.right <= fits.wide && fits.page <= fits.wide && fits.under, 'the refusal sticks out, or is not under the pads: ' + JSON.stringify(fits));
        refused = false;
        await page.waitForFunction(() => !document.getElementById('padrefused') && !document.querySelector('.pad.refused'));
        await page.unroute('**/api/status');
      }
      // Which pad is "the one playing": two pads with the same shader and different presets, only the one whose
      // preset is on is marked; and none while Vibes is the one showing that shader.
      {
        assert.strictEqual(await post('/api/shaders/play', { id: 'nxlx-aurora.fs' }), 200);
        assert.strictEqual(await post('/api/shaders/presets', { action: 'save', name: 'Slow', id: 'nxlx-aurora.fs' }), 200);
        assert.strictEqual(await post('/api/pads', { bank: 0, index: 6, label: 'Aurora slow', shader: 'nxlx-aurora.fs', preset: 'Slow' }), 200);
        assert.strictEqual(await post('/api/control', { action: 'stop' }), 200);
        await page.reload();
        await page.waitForSelector('.pad.shader >> nth=1');
        const marked = () => page.$$eval('.pad', (els) => els.map((el, i) => (el.classList.contains('on') && el.classList.contains('shader') ? i : -1)).filter((i) => i >= 0));
        const until = async (want, what) => {
          try { await page.waitForFunction((w) => JSON.stringify(Array.from(document.querySelectorAll('.pad')).map((el, i) => (el.classList.contains('on') && el.classList.contains('shader') ? i : -1)).filter((i) => i >= 0)) === w, JSON.stringify(want), { timeout: 20000 }); }
          catch (e) { throw new Error(what + ': the shader pads marked as playing are ' + JSON.stringify(await marked()) + ', expected ' + JSON.stringify(want)); }
        };
        await page.click('.pad >> nth=5');
        await until([5], 'the pad without a preset was tapped');
        await page.click('.pad >> nth=6');
        await until([6], 'the pad with the preset Slow was tapped');
        assert.strictEqual((await get('/api/status')).player.shader_preset, 'Slow');
        assert.strictEqual(await post('/api/vibes', { on: true }), 200);
        await page.waitForFunction(() => fetch('/api/status').then((r) => r.json()).then((s) => s.player.vibes === true && typeof s.player.shader === 'string'), null, { timeout: 20000 });
        await until([], 'Vibes is the one showing a shader');
        assert.strictEqual(await post('/api/vibes', { on: false }), 200);
        assert.strictEqual(await post('/api/control', { action: 'stop' }), 200);
        assert.strictEqual(await post('/api/shaders/presets', { action: 'delete', id: 'nxlx-aurora.fs', name: 'Slow' }), 200);
      }
      // a long label and a long shader name: nothing sticks out of the pad or of the page, at 320 and as it was
      assert.strictEqual(await post('/api/pads', { bank: 0, index: 6, label: 'Averyveryverylongshadernamewithnospaces', shader: 'nxlx-tide.fs' }), 200);
      const before = page.viewportSize();
      for (const width of [320, before.width]) {
        await page.setViewportSize({ width, height: before.height });
        await page.reload();
        await page.waitForSelector('.pad.shader >> nth=1');
        const out = await page.evaluate(() => Array.from(document.querySelectorAll('.pad')).map((el) => {
          const b = el.getBoundingClientRect(), t = el.querySelector('.t').getBoundingClientRect(), n = el.querySelector('.n').getBoundingClientRect();
          return { text: el.textContent, inside: t.right <= b.right + 0.5 && n.right <= b.right + 0.5 && t.bottom <= b.bottom + 0.5, page: document.documentElement.scrollWidth <= window.innerWidth };
        }).filter((x) => !x.inside || !x.page));
        assert.deepStrictEqual(out, [], 'a pad whose text sticks out at ' + width + ' px: ' + JSON.stringify(out));
      }
      assert.strictEqual(await post('/api/pads', { bank: 0, index: 5, label: '', file: '' }), 200);
      assert.strictEqual(await post('/api/pads', { bank: 0, index: 6, label: '', file: '' }), 200);
      assert.strictEqual(await post('/api/control', { action: 'stop' }), 200);
      await page.reload();
      await page.waitForSelector('.pads');
    }

    // A laptop: the Shaders page is a workspace. The library is a column that scrolls by itself, what is playing and
    // its controls are beside it and in view, the settings and controllers are a third column; nothing sticks out at
    // any width, on this page or on Live.
    await page.setViewportSize({ width: 1366, height: 768 });
    assert.strictEqual(await post('/api/shaders/play', { id: 'nxlx-tide.fs' }), 200);
    await page.click('nav >> text=Live');
    await page.waitForSelector('#shaderslink');
    {
      // The strip follows the box's answer, which may say "nothing on" for a moment right after a Play: wait for the
      // layout to hold, and say what it was if it never does.
      const measure = () => {
        const r = (id) => { const b = document.getElementById(id).getBoundingClientRect(); return { left: b.left, right: b.right, top: b.top }; };
        return { pads: r('pads'), strip: r('liveshader'), vibes: r('vibes'), name: (document.getElementById('livename') || {}).textContent, vw: window.innerWidth };
      };
      const ok = await page.waitForFunction(() => {
        const g = (id) => document.getElementById(id).getBoundingClientRect(), strip = g('liveshader');
        return strip.width > 0 && (document.getElementById('livename') || {}).textContent === 'Tide' && g('pads').right <= strip.left && g('vibes').right <= strip.left && strip.right <= window.innerWidth;
      }, null, { timeout: 20000 }).then(() => true, () => false);
      assert(ok, 'on a laptop Live has the pads and the transport on the left and the shader strip on the right: ' + JSON.stringify(await page.evaluate(measure)) +
        ' box: ' + JSON.stringify(await get('/api/shaders').then((d) => [d.playing && d.playing.id, d.vibes])));
    }
    await fitsPhone('Live at 1366 px');
    await page.click('#shaderslink');
    await page.waitForSelector('#shadercontrols #shin-speed');
    await page.waitForSelector('#shaderdmxline');
    const lay = await page.evaluate(() => {
      const r = (id) => { const b = document.getElementById(id).getBoundingClientRect(); return { left: b.left, right: b.right, top: b.top, bottom: b.bottom }; };
      const list = document.getElementById('shaderlist');
      return { lib: r('shadercard'), now: r('shadernow'), ctl: r('shadercontrols'), set: r('vibessettings'), remote: r('shaderremote'), vh: window.innerHeight,
        overflow: getComputedStyle(list).overflowY, listShown: list.clientHeight, listAll: list.scrollHeight };
    });
    assert(lay.lib.right <= lay.now.left && lay.now.right <= lay.set.left, 'three columns at 1366 px: library, stage, settings: ' + JSON.stringify(lay));
    assert(Math.abs(lay.ctl.left - lay.now.left) < 1 && lay.ctl.top >= lay.now.bottom, 'the controls are under what is playing, in the middle column');
    assert(Math.abs(lay.remote.left - lay.set.left) < 1, 'the controllers are under the settings');
    assert(lay.overflow === 'auto' && lay.listAll > lay.listShown, 'the library scrolls by itself: ' + JSON.stringify(lay));
    assert(lay.ctl.top < lay.vh && lay.lib.top < lay.vh, 'the playing shader\'s controls are in view without scrolling the library');
    for (const w of [900, 1100, 1366, 1600]) {
      await page.setViewportSize({ width: w, height: 768 });
      await page.waitForTimeout(150);
      await fitsPhone('Shaders page at ' + w + ' px');
    }
    await fitsCard('#shaderpage .card', 'Shaders page on a laptop');
    assert.strictEqual(await post('/api/control', { action: 'stop' }), 200);

    // A laptop: every System page this pass changed, as the box is at the end of this test, fits its cards and the
    // window (the Shaders page, Projection mapping and Room have their own steps)
    await page.setViewportSize({ width: 1366, height: 800 });
    await page.click('nav >> text=System');                    // from wherever the last step left the panel (a page opened from Live goes back to Live)
    await page.waitForSelector('#sysindex');
    const laptopRows = (await page.$$eval('.navname', (ns) => ns.map((x) => x.textContent))).filter((n) => !['Shaders and Vibes', 'Projection mapping', 'Room'].includes(n));
    for (const name of laptopRows) {
      await sys(name);
      await page.waitForTimeout(700);          // the page's own cards arrive after it opens
      await fitsOn(page, name + ' page at 1366');
    }
    // People and codes and the MIDI page are two columns on a laptop; a list that grows scrolls by itself
    await sys('People and codes');
    await page.waitForSelector('#devicelist');
    assert(await page.evaluate(() => { const a = document.getElementById('accesscard').getBoundingClientRect(), b = document.getElementById('devicescard').getBoundingClientRect(); return b.left > a.right - 2; }),
      'People and codes: the two cards are side by side on a laptop');
    // "A code from a controller" (D61) is in the left column, under "Let someone in", and the devices card is beside both
    await page.waitForSelector('#ctlcodecard #ctlcode-on');
    const cols = await page.evaluate(() => { const r = (id) => document.getElementById(id).getBoundingClientRect(); return { a: r('accesscard'), c: r('ctlcodecard'), d: r('devicescard') }; });
    assert(Math.abs(cols.c.left - cols.a.left) <= 1 && Math.abs(cols.c.right - cols.a.right) <= 1, 'the controller code card is in the left column: ' + JSON.stringify(cols));
    assert(cols.c.top >= cols.a.bottom - 1, 'it is under Let someone in: ' + JSON.stringify(cols));
    assert(cols.d.left > cols.c.right - 2 && Math.abs(cols.d.top - cols.a.top) <= 1, 'the devices card is beside both, from the top: ' + JSON.stringify(cols));
    await fitsCard('#ctlcodecard', 'A code from a controller at 1366');
    await fitsOn(page, 'People and codes at 1366 with the controller code card');
    assert.strictEqual(await page.evaluate(() => getComputedStyle(document.getElementById('devicelist')).overflowY), 'auto', 'the device list scrolls by itself on a laptop');
    // Desktop width
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.click('nav >> text=Live');
    await page.waitForSelector('.pads');
    if (shots) await page.screenshot({ path: path.join(shots, '5-desktop.png') });

    // ---- The look "Signal" (D54): chosen under System > Look, it restyles the whole panel; the default look, which
    // every step above ran in, never loads its fonts. The layout checks run again with it on: nothing sticks out at
    // 390 px, everything that can be tapped is 44 px at least (56 px on the Room screen), no text is under 13 px, every
    // text reads at 4.5 to 1 on what is behind it, and the title, the open tab and the focus ring take the area's
    // colour. This step comes last and puts the default look back.
    {
      assert(fontRequests.every((u) => u.indexOf(base + '/fonts/') === 0), 'a font was asked for somewhere other than the box: ' + fontRequests.join(', '));
      assert.strictEqual(await page.evaluate(() => document.documentElement.hasAttribute('data-style')), false, 'the default look has no style on the root element');
      const AREA = { room: 'rgb(255, 214, 10)', shaders: 'rgb(255, 79, 163)', clips: 'rgb(61, 220, 151)', mix: 'rgb(183, 140, 255)', system: 'rgb(122, 162, 255)' };
      await page.setViewportSize({ width: 390, height: 844 });
      for (const id of ['room', 'shaders', 'control-midi', 'projector']) assert.strictEqual(await post('/api/modules/' + id, { enabled: true }), 200, 'module ' + id);
      await post('/api/midi', { enabled: true });
      try { fs.writeFileSync(path.join(info.midi_dir, 'plug'), ''); } catch (e) { /* the page is checked without a controller drawn */ }
      await page.goto(base + '/');
      await page.waitForSelector('nav.tabs');
      await sys('Look');
      await page.waitForSelector('#lookthemes');
      assert((await page.locator('.swatch').count()) > 0, 'the default look offers accents');
      await page.click('#lookthemes button[data-theme="signal"]');
      await page.waitForSelector('html[data-style="signal"]');           // applies on tap, with no reload
      await page.waitForSelector('#lookareas');
      assert.strictEqual(await page.locator('.swatch').count(), 0, 'Signal has a colour per area, so no accent is offered');
      assert.strictEqual(await page.getAttribute('#lookthemes button[data-theme="signal"]', 'aria-pressed'), 'true');
      assert.deepStrictEqual((await get('/api/theme')).theme.name, 'signal', 'the box remembers the look');
      // the fonts come from the box itself, with the right type
      // (a face is fetched when something on the page first uses it; the page asks for both here, as a number would)
      const faces = await page.evaluate(() => Promise.all([document.fonts.load('900 44px Archivo'), document.fonts.load('500 16px "JetBrains Mono"')]).then((r) => r.map((x) => x.length)));
      assert.deepStrictEqual(faces, [1, 1], 'both typefaces load');
      assert(await page.evaluate(() => document.fonts.check('900 44px Archivo') && document.fonts.check('500 16px "JetBrains Mono"')), 'both typefaces are ready');
      const fontNames = fontRequests.map((u) => u.replace(base, '')).sort();
      assert(fontNames.includes('/fonts/archivo-latin.06fa7831.woff2') && fontNames.includes('/fonts/jetbrains-mono-500-latin.6c95bc2f.woff2'), 'both fonts are fetched from the box: ' + fontNames.join(', '));
      assert(fontRequests.every((u) => u.indexOf(base + '/fonts/') === 0), 'a font was asked for somewhere else: ' + fontRequests.join(', '));
      for (const f of ['/fonts/archivo-latin.06fa7831.woff2', '/fonts/jetbrains-mono-500-latin.6c95bc2f.woff2']) {
        const got = await page.evaluate((u) => fetch(u).then(async (r) => [r.status, r.headers.get('content-type'), (await r.arrayBuffer()).byteLength]), f);
        assert(got[0] === 200 && got[1] === 'font/woff2' && got[2] > 4000, f + ' is served by the box as a font: ' + JSON.stringify(got));
      }
      assert(/^"?Archivo/.test(await page.evaluate(() => getComputedStyle(document.querySelector('#syspage h1')).fontFamily)), 'titles are set in Archivo');

      // Every screen, page and state in the list of tests/ui/signal-pages.js (the same list the pictures are taken
      // from), at 390 and at 1366 px in the dark and at 390 px in the light, each held to the rules of the look by
      // that file's check(): nothing sticks out, no word in capitals is cut and no sentence is in capitals, touch
      // targets (44 px; 56 px for what staff press on Room and Live), no text under 13 px, contrast 4.5 to 1, names
      // of clips keep their case, numbers in the number face, a state chip never in the area's colour, in the light
      // no line in an area's colour, slider fills, and the title block and the open tab in the area's colour.
      const signal = require('./signal-pages');
      const signalBad = [];              // every screen is looked at before the step fails, so one run names everything
      const st = { page, browser, base, info, width: 390, scale: 1, notes: [], contexts: [] };
      await signal.setUp(st);
      // The width sweep (D64, tests/ui/sweep.js): each of those screens, while it is open, is also resized through
      // every size class of the resizing study (320 to 1600 px wide, and three phones held sideways) and held to
      // the rules that keep a layout whole: no sideways scroll, nothing wider than the window or out of its card,
      // touch targets, no clipped or broken words on a button, the tabs and the transport within reach. In Signal
      // it rides on the first round below; the default look has a round of its own once that look is back. Every
      // fault of every screen is collected, so one run names them all. What it costs is printed at the end.
      const sweep = require('./sweep');
      const sweepBad = [];
      const sweepCost = { screens: 0, measuring: 0, plainRound: 0 };
      const sweepHere = async (on, name, look) => {
        const began = Date.now();
        try { sweep.lines(await sweep.sweep(on, { signal: look === 'Signal' })).forEach((f) => sweepBad.push(look + ', ' + name + ': ' + f)); }
        catch (e) { sweepBad.push(look + ', ' + name + ' could not be swept: ' + e.message.split('\n')[0]); }
        await on.setViewportSize({ width: st.width, height: 844 }).catch(() => {});
        sweepCost.screens += 1;
        sweepCost.measuring += Date.now() - began;
      };
      const signalRound = async (label, light, swept) => {
        for (const p of signal.pages()) {
          try {
            const on = (await p.open(st)) || page;
            if (!p.quick) await on.waitForTimeout(1000);          // a page's own cards arrive after it opens; a slider's fill is set within a quarter of a second
            const found = await signal.check(on, { area: p.area, light });
            if (found.length) signalBad.push('Signal, ' + p.name + label + ': ' + found.join('; '));
            if (swept) await sweepHere(on, p.name, 'Signal');
          } catch (e) { signalBad.push('Signal, ' + p.name + label + ' could not be looked at: ' + e.message.split('\n')[0]); }
          if (p.done) await p.done(st).catch((e) => signalBad.push('Signal, ' + p.name + label + ', putting back: ' + e.message.split('\n')[0]));
        }
      };
      await signalRound('', false, true);
      // The Room screen as staff use it: named group cards, source buttons with their labels and a result line were
      // on the screen that was checked (the built-in "Everything" card alone is not what staff see).
      await page.click('nav >> text=Room');
      await page.waitForSelector('#roomscreen');
      await page.waitForSelector('.room-group:has-text("Main wall")', { timeout: 8000 }).catch(() => {});
      const onRoom = await page.evaluate(() => ({ named: Array.prototype.filter.call(document.querySelectorAll('.room-group'), (g) => /wall/.test(g.textContent)).length,
        sources: document.querySelectorAll('.room-source').length, results: document.querySelectorAll('.room-result').length,
        labelled: Array.prototype.some.call(document.querySelectorAll('.room-source'), (b) => /Laptop|Console/.test(b.textContent)) }));
      if (onRoom.named < 2 || !onRoom.sources || !onRoom.results || !onRoom.labelled) signalBad.push('Signal, Room: the screen that was checked did not have two named groups, labelled source buttons and a result line: ' + JSON.stringify(onRoom)
        + ' projectors: ' + JSON.stringify(((await get('/api/projectors')).projectors || []).map((x) => [x.name, x.inputs.length, x.status && x.status.power]))
        + ' groups: ' + JSON.stringify(((await get('/api/room')).groups || []).map((g) => [g.name, g.projectors.length, g.inputs && g.inputs.length, g.state])));
      // On and Off are the two halves of one switch: the half in force is filled (On with the screen's colour)
      const halves = await page.evaluate(() => { const g = Array.prototype.filter.call(document.querySelectorAll('.room-group'), (x) => x.querySelector('.room-state.state-on') && x.getAttribute('data-id') !== 'all')[0];
        return g ? [getComputedStyle(g.querySelector('[data-action="on"]')).backgroundColor, getComputedStyle(g.querySelector('[data-action="off"]')).backgroundColor] : null; });
      if (halves) assert(halves[0] === AREA.room && halves[1] !== AREA.room, 'a group that is on has its On half in the Room colour: ' + JSON.stringify(halves));
      // All off is the danger red, never the screen's colour
      const allOff = await page.evaluate(() => { const b = document.getElementById('roomalloff'); return b ? getComputedStyle(b).backgroundColor : null; });
      if (allOff) assert.strictEqual(allOff, 'rgb(255, 59, 48)', 'All off is the danger red');
      await page.keyboard.press('Tab');
      const ring = await page.evaluate(() => { const cs = getComputedStyle(document.activeElement); return [document.activeElement.tagName, cs.outlineStyle, cs.outlineWidth, cs.outlineColor]; });
      assert(ring[1] === 'solid' && ring[2] === '3px' && ring[3] === AREA.room, 'the focus ring on Room is the area colour, 3 px: ' + JSON.stringify(ring));
      await page.click('nav >> text=Live');
      await page.waitForSelector('.pads');
      const liveTitle = await page.evaluate(() => { const h1 = document.querySelector('.screen > .top h1'), b = getComputedStyle(h1, '::before');
        return [b.content, b.fontSize, b.textTransform, h1.textContent, getComputedStyle(h1).fontSize]; });
      assert.deepStrictEqual(liveTitle, ['"Live"', '44px', 'uppercase', 'nxlx.mastercontrol', '15px'], 'in Signal the Live screen is titled LIVE, with the panel\'s name small under it');
      await page.click('nav >> text=Mix');
      await page.waitForSelector('#fliph');
      const moved = await page.evaluate(() => { const r = document.querySelector('.shell input[type=range]:not(:disabled)'); if (!r) return null;
        const min = r.min === '' ? 0 : parseFloat(r.min), max = r.max === '' ? 100 : parseFloat(r.max);
        const was = r.value; r.value = String(min + (max - min) * 0.75); const v = parseFloat(r.value); r.dispatchEvent(new Event('input', { bubbles: true }));
        const out = [Math.round(1000 * (v - min) / (max - min)) / 10, parseFloat(getComputedStyle(r).getPropertyValue('--fill'))];
        r.value = was; r.dispatchEvent(new Event('input', { bubbles: true })); r.dispatchEvent(new Event('change', { bubbles: true })); return out; });
      assert(moved && Math.abs(moved[0] - moved[1]) <= 0.2, 'a slider that is moved is filled up to where it is, at once: ' + JSON.stringify(moved));
      await sysIndex();
      await page.waitForSelector('.navrow .chip-ready, .navrow .chip-active, .navrow .chip-off');
      const subTitle = await page.evaluate(() => getComputedStyle(document.querySelector('#sysindex .top h1'), '::after').content);
      assert.strictEqual(subTitle, '"nxlx.mastercontrol"', 'the other screens carry the panel\'s name as the small line under the title');
      const active = await page.evaluate(() => { const c = document.querySelector('.navrow .chip-active'); return c ? [getComputedStyle(c).backgroundColor, getComputedStyle(c).color, c.textContent] : null; });
      if (active) assert.deepStrictEqual(active, ['rgb(0, 224, 255)', 'rgb(11, 11, 13)', 'Active'], 'Active is its own fixed colour, with the word');
      const setUpChip = await page.evaluate(() => { const c = document.querySelector('.navrow .chip-setup'); return c ? [getComputedStyle(c).backgroundColor, c.textContent] : null; });
      if (setUpChip) assert.deepStrictEqual(setUpChip, ['rgb(255, 149, 0)', 'Set up'], 'Set up is the orange that is not Room yellow (D57), with the word');
      const longNames = await page.$$eval('.navname', (ns) => ns.filter((x) => x.scrollWidth > x.clientWidth + 1).map((x) => x.textContent));
      assert.deepStrictEqual(longNames, [], 'a row name is cut off in capitals');
      // Projectors: every power state is on the page that was checked, each with its own chip and its own button
      await sys('Projectors');
      await page.waitForSelector('.proj-entry');
      const powers = await page.$$eval('.proj-entry', (rows) => rows.map((r) => { const b = r.querySelector('.proj-power');
        return [r.getAttribute('data-power'), getComputedStyle(r.querySelector('.lname'), '::after').content, b ? getComputedStyle(b).backgroundColor : '']; }));
      for (const want of ['on', 'off', 'warming up', 'cooling down', 'no answer']) {
        if (!powers.some((x) => x[0] === want)) signalBad.push('Signal, Projectors: no projector in the state "' + want + '" on the page that was checked: ' + JSON.stringify(powers));
      }
      const look = {};
      powers.forEach((x) => { look[x[0]] = x[1] + ' ' + x[2]; });
      assert.strictEqual(new Set(Object.values(look)).size, Object.keys(look).length, 'each power state has a chip and a button of its own: ' + JSON.stringify(look));
      // a reload: the server writes the style into the page, so it is there before any script runs
      const html = await page.evaluate(() => fetch('/').then((r) => r.text()));
      assert(/<html lang="en" data-style="signal">/.test(html), 'the page as served carries the style');
      // a laptop
      st.width = 1366;
      await page.setViewportSize({ width: 1366, height: 800 });
      await signalRound(' at 1366', false);
      // the light room
      st.width = 390;
      await page.setViewportSize({ width: 390, height: 844 });
      assert.strictEqual(await post('/api/theme', { name: 'signal-light', accent: null }), 200);
      await page.goto(base + '/');
      await page.waitForSelector('nav.tabs');
      assert.strictEqual(await page.evaluate(() => getComputedStyle(document.body).backgroundColor), 'rgb(242, 240, 234)', 'Signal light has the off-white page');
      await signalRound(' in the light', true);
      await page.click('nav >> text=Room');
      await page.waitForSelector('#roomscreen');
      await page.keyboard.press('Tab');
      const ringLight = await page.evaluate(() => getComputedStyle(document.activeElement).outlineColor);
      assert.strictEqual(ringLight, 'rgb(11, 11, 13)', 'in the light the focus ring is the text colour (yellow on off-white would be lost)');
      await signal.closeOthers(st);
      if (st.notes.length) console.log('Signal, while setting screens up:\n  ' + st.notes.join('\n  '));
      if (signalBad.length && sweepBad.length) console.log('the width sweep, in Signal (the step fails on the rules of the look first):\n' + sweepBad.join('\n'));
      assert.deepStrictEqual(signalBad, [], 'in Signal:\n' + signalBad.join('\n'));
      // back to the default look, as the box was
      await sys('Look');
      await page.click('#lookthemes button[data-theme="dark-stage"]');
      await page.waitForFunction(() => !document.documentElement.hasAttribute('data-style'));
      assert((await page.locator('.swatch').count()) > 0, 'the default look offers accents again');
      // The width sweep in the default look, the one a box comes with: the same screens, opened once more. (Its
      // words are in the system's typeface, so widths here are those of the machine the test runs on.)
      {
        const began = Date.now(), notesBefore = st.notes.length;
        st.plain = true;
        for (const p of signal.pages()) {
          try {
            const on = (await p.open(st)) || page;
            if (!p.quick) await on.waitForTimeout(500);
            await sweepHere(on, p.name, 'the default look');
          } catch (e) { sweepBad.push('the default look, ' + p.name + ' could not be opened: ' + e.message.split('\n')[0]); }
          if (p.done) await p.done(st).catch((e) => sweepBad.push('the default look, ' + p.name + ', putting back: ' + e.message.split('\n')[0]));
        }
        await signal.closeOthers(st);
        st.plain = false;
        sweepCost.plainRound = Date.now() - began;
        if (st.notes.length > notesBefore) console.log('the default look, while setting screens up:\n  ' + st.notes.slice(notesBefore).join('\n  '));
      }
      console.log('width sweep: ' + sweepCost.screens + ' screens at ' + sweep.SIZES.length + ' sizes each; resizing and measuring took ' + (sweepCost.measuring / 1000).toFixed(1)
        + ' s, and the round in the default look ' + (sweepCost.plainRound / 1000).toFixed(1) + ' s in all (opening its screens included)');
      assert.deepStrictEqual(sweepBad, [], 'the width sweep (' + sweepBad.length + '):\n' + sweepBad.join('\n'));
      assert.strictEqual(await post('/api/modules/room', { enabled: false }), 200);
      await page.setViewportSize({ width: 1280, height: 800 });
    }

    // ---- A theme of the owner's own (D60): a small file added on the Look page. One that cannot be read is refused in
    // plain words; a good one shows among the looks marked "yours", applies on tap (its radius, its sentence-case
    // titles and its two colours are on Room and on Live), can be saved as a file again, and is removed with a
    // question asked in place, the panel going back to the look it came with.
    {
      await page.setViewportSize({ width: 390, height: 844 });
      assert.strictEqual(await post('/api/modules/room', { enabled: true }), 200);
      await page.goto(base + '/');
      await page.waitForSelector('nav.tabs');
      await sys('Look');
      await page.waitForSelector('#lookthemes .looktile');
      // the pictures: each look in its own colours, type and case
      const tiles = await page.$$eval('#lookthemes .looktile', (ts) => ts.map((t) => { const art = t.querySelector('.lt-art'), ti = t.querySelector('.lt-title'), cs = getComputedStyle(ti);
        return [t.getAttribute('data-theme'), getComputedStyle(art).backgroundColor, cs.textTransform, cs.fontFamily.split(',')[0].replace(/"/g, ''), t.querySelectorAll('.lt-areas i').length, t.querySelector('.lt-btn').textContent, t.querySelector('.lt-chip').textContent]; }));
      const tile = {};
      tiles.forEach((x) => { tile[x[0]] = x.slice(1); });
      assert.deepStrictEqual(tile.signal, ['rgb(11, 11, 13)', 'uppercase', 'Archivo', 5, 'Play', 'Active'], 'Signal\'s picture: ' + JSON.stringify(tile.signal));
      assert.deepStrictEqual(tile['signal-light'].slice(0, 2), ['rgb(242, 240, 234)', 'uppercase']);
      assert.deepStrictEqual([tile['dark-stage'][0], tile['dark-stage'][1], tile['dark-stage'][3]], ['rgb(18, 18, 20)', 'none', 1], 'Dark stage\'s picture: ' + JSON.stringify(tile['dark-stage']));
      assert.strictEqual(await page.locator('#lookthemes .looktile.on[data-theme="dark-stage"] .lt-inuse').count(), 1, 'the look in use says so in words');
      assert.strictEqual(await page.locator('.lt-mine').count(), 0, 'nothing is marked "yours" before a theme is added');
      const wide = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
      assert(wide[0] <= wide[1] + 1, 'the Look page is wider than a phone: ' + JSON.stringify(wide));
      const small = await page.$$eval('#lookcard .lt-title, #lookcard .lt-btn, #lookcard .lt-chip, #lookcard .lt-name span', (els) => els.filter((e) => parseFloat(getComputedStyle(e).fontSize) < 13).length);
      assert.strictEqual(small, 0, 'text under 13 px in a look\'s picture');
      // An accent is offered only where it can be read on the look in use (the box refuses the others): on Dark stage
      // every swatch but the dark orange, on Light the dark orange alone; and each one offered is taken.
      const swatches = () => page.$$eval('.swatch', (ss) => ss.map((x) => x.getAttribute('aria-label').replace('Accent ', '')));
      assert.deepStrictEqual(await swatches(), ['#f59e0b', '#22d3ee', '#e879f9', '#a3e635', '#ffffff'], 'the accents offered on Dark stage');
      assert.strictEqual(await post('/api/theme', { name: 'dark-stage', accent: '#c2410c' }), 400, 'the box refuses an accent that cannot be read');
      await page.click('.looktile[data-theme="light"]');
      await page.waitForFunction(() => getComputedStyle(document.body).backgroundColor === 'rgb(244, 243, 239)');
      assert.deepStrictEqual(await swatches(), ['#c2410c'], 'the accents offered on Light');
      assert.strictEqual(await post('/api/theme', { name: 'light', accent: '#ffffff' }), 400);
      await page.click('.swatch');
      await page.waitForSelector('.swatch.cur');
      assert.deepStrictEqual((await get('/api/theme')).theme, { name: 'light', accent: '#c2410c' }, 'a swatch that is offered is taken');
      assert.strictEqual(await page.locator('#accentdropped').count(), 0);
      await page.click('.looktile[data-theme="dark-stage"]');          // the accent chosen for Light cannot be read on Dark stage: tapping the look must not be refused
      await page.waitForFunction(() => getComputedStyle(document.body).backgroundColor === 'rgb(18, 18, 20)', null, { timeout: 8000 });
      assert.deepStrictEqual((await get('/api/theme')).theme, { name: 'dark-stage', accent: null }, 'an accent that the next look cannot carry is left behind');
      const signalTheme = (await get('/api/theme')).available.filter((t) => t.id === 'signal')[0].look;
      const own = { id: 'browser-test', name: 'Browser test', style: 'signal', tokens: signalTheme.tokens, areas: Object.assign({}, signalTheme.areas, { room: '#ff8a65', clips: '#4dd0e1' }),
        states: signalTheme.states, design: { radius_control: 14, title_case: 'sentence' } };
      // one whose text cannot be read: refused, with the pair named, and nothing added
      const dim = Object.assign({}, own, { id: 'dim', name: 'Dim', tokens: Object.assign({}, own.tokens, { fg: '#555555' }) });
      await page.setInputFiles('#themepick', { name: 'dim.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(dim)) });
      await page.waitForFunction(() => { const m = document.getElementById('themeresult'); return m && /err/.test(m.className) && /Text on the page is 2\.6 to 1; it needs 4\.5/.test(m.textContent); }, null, { timeout: 8000 });
      assert(/^This theme cannot be used: /.test(await page.textContent('#themeresult')), 'the refusal is said plainly: ' + await page.textContent('#themeresult'));
      assert.strictEqual(await page.locator('.looktile[data-theme="dim"]').count(), 0);
      // CSS in a theme never gets as far as the page
      await page.setInputFiles('#themepick', { name: 'css.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(Object.assign({}, own, { css: 'body{display:none}' }))) });
      await page.waitForFunction(() => /unknown keys: css/.test(document.getElementById('themeresult').textContent), null, { timeout: 8000 });
      await page.setInputFiles('#themepick', { name: 'huge.json', mimeType: 'application/json', buffer: Buffer.alloc(40000, 32) });
      await page.waitForFunction(() => /too large for a theme/.test(document.getElementById('themeresult').textContent), null, { timeout: 8000 });
      // a good one
      await page.setInputFiles('#themepick', { name: 'browser-test.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(own, null, 2)) });
      await page.waitForSelector('.looktile[data-theme="browser-test"] .lt-mine:text-is("yours")', { timeout: 8000 });
      assert.strictEqual(await page.textContent('#themeresult'), 'Added Browser test. Tap it to use it.');
      assert.strictEqual(await page.evaluate(() => document.documentElement.hasAttribute('data-style')), false, 'adding a theme does not put it on');
      const mineTile = await page.$eval('.looktile[data-theme="browser-test"]', (t) => [getComputedStyle(t.querySelector('.lt-title')).textTransform, getComputedStyle(t.querySelector('.lt-btn')).backgroundColor, getComputedStyle(t.querySelector('.lt-btn')).borderTopLeftRadius, getComputedStyle(t.querySelector('.lt-areas i:nth-child(3)')).backgroundColor]);
      assert.deepStrictEqual(mineTile, ['none', 'rgb(255, 138, 101)', '12px', 'rgb(77, 208, 225)'], 'its picture shows its case, its radius and its two colours before it is tapped: ' + JSON.stringify(mineTile));
      // tap: it is the look, at once
      await page.click('.looktile[data-theme="browser-test"]');
      await page.waitForSelector('html[data-style="signal"]');
      await page.waitForFunction(() => getComputedStyle(document.documentElement).getPropertyValue('--tk-r').trim() === '14px', null, { timeout: 8000 });
      assert.strictEqual((await get('/api/theme')).theme.name, 'browser-test');
      const seen = async () => page.evaluate(() => { const top = document.querySelector('.screen > .top:first-child'), h1 = top.querySelector('h1'), tab = document.querySelector('nav.tabs .btn.on'), plain = document.querySelector('nav.tabs .btn:not(.on)');
        return [getComputedStyle(top).backgroundColor, getComputedStyle(h1).textTransform, getComputedStyle(h1, '::before').textTransform, getComputedStyle(tab).backgroundColor, getComputedStyle(plain).borderTopLeftRadius, getComputedStyle(plain).minHeight]; });
      await page.click('nav >> text=Room');
      await page.waitForSelector('#roomscreen');
      await page.waitForFunction(() => document.documentElement.getAttribute('data-area') === 'room');
      const onRoom = await seen();
      assert.deepStrictEqual([onRoom[0], onRoom[1], onRoom[3], onRoom[4], onRoom[5]], ['rgb(255, 138, 101)', 'none', 'rgb(255, 138, 101)', '14px', '56px'], 'Room in the owner\'s theme: its colour, sentence case, rounded: ' + JSON.stringify(onRoom));
      const roomBtn = await page.evaluate(() => { const b = document.querySelector('#roomscreen .btn'); return b ? [getComputedStyle(b).borderTopLeftRadius, b.getBoundingClientRect().height >= 55.5] : null; });
      if (roomBtn) assert.deepStrictEqual(roomBtn, ['14px', true], 'a button on Room is rounded and still 56 px: ' + JSON.stringify(roomBtn));
      await page.click('nav >> text=Live');
      await page.waitForSelector('.pads');
      await page.waitForFunction(() => document.documentElement.getAttribute('data-area') === 'clips');
      const onLive = await seen();
      assert.deepStrictEqual([onLive[0], onLive[2], onLive[3], onLive[4]], ['rgb(77, 208, 225)', 'none', 'rgb(77, 208, 225)', '14px'], 'Live in the owner\'s theme: ' + JSON.stringify(onLive));
      assert.strictEqual(await page.evaluate(() => getComputedStyle(document.querySelector('.pad')).borderTopLeftRadius), '14px', 'a pad is rounded');
      const html2 = await page.evaluate(() => fetch('/').then((r) => r.text()));
      assert(/<html lang="en" data-style="signal">/.test(html2), 'the page as served carries the style of the owner\'s theme');
      const css2 = await page.evaluate(() => fetch('/theme.css').then((r) => r.text()));
      assert(/^:root\{[^{}<>]*--tk-r:14px;[^{}<>]*\}$/.test(css2) && !/display|url\(/.test(css2), 'what the box serves for the theme is one block of variables: ' + css2.slice(0, 200));
      // a laptop: the pictures fit, and the theme is saved as a file that holds what was added
      await page.setViewportSize({ width: 1366, height: 800 });
      await sys('Look');
      await page.waitForSelector('.looktile[data-theme="browser-test"].on .lt-inuse');
      const wide2 = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth, document.querySelectorAll('#lookthemes .looktile').length]);
      assert(wide2[0] <= wide2[1] + 1 && wide2[2] === 7, 'the Look page on a laptop: ' + JSON.stringify(wide2));
      assert.strictEqual(await page.locator('.swatch').count(), 0, 'a theme with a colour per area offers no accent');
      const [download] = await Promise.all([page.waitForEvent('download'), page.click('#themesave')]);
      assert.strictEqual(download.suggestedFilename(), 'nxlx-theme-browser-test.json');
      const saved = JSON.parse(fs.readFileSync(await download.path(), 'utf8'));
      assert.deepStrictEqual([saved.id, saved.name, saved.areas.room, saved.design.radius_control, saved.design.title_case, saved.design.border_width], ['browser-test', 'Browser test', '#ff8a65', 14, 'sentence', 3], 'the saved file: ' + JSON.stringify(saved));
      // removing the look in use asks first, in place, and says what will happen
      await page.setViewportSize({ width: 390, height: 844 });
      await page.click('#themelist button[data-remove="browser-test"]');
      await page.waitForSelector('#confirmrow:has-text("Remove the theme Browser test from this box? It is the look in use: the panel goes back to Dark stage.")');
      await page.click('#confirmno');
      assert.strictEqual((await get('/api/theme')).theme.name, 'browser-test', '"Keep it" changes nothing');
      await page.click('#themelist button[data-remove="browser-test"]');
      await page.click('#confirmyes');
      await page.waitForFunction(() => !document.documentElement.hasAttribute('data-style') && !document.querySelector('.looktile[data-theme="browser-test"]'), null, { timeout: 8000 });
      assert.deepStrictEqual((await get('/api/theme')).theme, { name: 'dark-stage', accent: null }, 'the box is back on the look it came with');
      assert.strictEqual(await page.evaluate(() => getComputedStyle(document.body).backgroundColor), 'rgb(18, 18, 20)');
      assert.strictEqual(await page.locator('#themelist').count(), 0);
      assert.strictEqual(await page.textContent('#themeresult'), 'Removed Browser test.');
      assert.strictEqual(await post('/api/modules/room', { enabled: false }), 200);
      await page.setViewportSize({ width: 1280, height: 800 });
    }

    {
      // A file of the page that the box does not deliver (D68, tests/ui/delivery.js): asked for again, and said when it
      // still does not come. On a page of its own, whose console (the withheld files, by design) is not this test's.
      const fresh = async () => {
        const pg = await (await browser.newContext({ viewport: { width: 390, height: 844 } })).newPage();
        const said = [];
        pg.on('console', (m) => said.push(m.text()));
        return { pg, said };
      };
      await require('./delivery').check({ info, base, page, fresh });
      console.log('files not delivered: asked for again, said when missing');
    }

    const csp = problems.filter((t) => /Content Security Policy|Refused to/i.test(t));
    assert.deepStrictEqual(csp, [], 'CSP violations: ' + csp.join('; '));
    assert.deepStrictEqual(problems, [], 'console problems: ' + problems.join('; '));
    console.log('panel browser test: OK');
  } catch (e) {
    failed = true;
    console.error('FAILED:', e.message, (e.stack || '').split('\n').filter(function (l) { return /panel.test.js/.test(l); }).slice(0, 2).join(' | '));
    try { await report(e); } catch (x) { console.error('(no report of the pages: ' + x.message + ')'); }
  } finally {
    await browser.close();
    server.kill();
  }
  process.exit(failed ? 1 : 0);
})();
