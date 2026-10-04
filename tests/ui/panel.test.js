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
  try {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
    const page = await ctx.newPage();
    const problems = [];
    // The 401 before pairing, the 403 for the wrong PIN, the 400 for a refused upload and the 503 for a screen
    // preview from a harness player with no window are provoked on purpose.
    const expected = /status of (400|401|403|503)/;
    page.on('console', (m) => { if (['error', 'warning'].includes(m.type()) && !expected.test(m.text())) problems.push(m.text()); });
    page.on('pageerror', (e) => problems.push('pageerror: ' + e.message));
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
    page.once('dialog', (d) => d.accept('renamed-on-phone.mp4'));
    await page.click('.item:has-text("from-phone.mp4") >> text=Rename');
    await page.waitForSelector('.item:has-text("renamed-on-phone.mp4")');
    page.once('dialog', (d) => d.accept());
    await page.click('.item:has-text("renamed-on-phone.mp4") >> text=Delete');
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
    await page.click('#netmodes >> text=Fixed address');
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
    await page.waitForFunction(() => /Listening on UDP/.test(document.getElementById('oscline').textContent));
    assert.strictEqual((await get('/api/osc')).enabled, true, 'the page switch turned OSC on');
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
    await page.selectOption('#automode', 'file');
    await page.selectOption('#autofile', { index: 0 });
    await page.fill('#autodelay', '999');
    await page.click('#autosave');
    await page.waitForFunction(() => /delay must be/.test(document.getElementById('msg').textContent));
    await page.fill('#autodelay', '3');
    await page.selectOption('#automode', 'all');
    await page.click('#autosave');
    await page.waitForFunction(() => /Play every clip.*after 3 s/.test(document.getElementById('autoline').textContent));
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
    await page.waitForSelector('#dmxline:has-text("Listening on UDP")');
    assert(await moduleIsOn('control-dmx') && (await get('/api/dmx')).enabled === true, 'the page switch turned on the module and DMX itself');
    assert.strictEqual(await page.locator('#dmxtoggle').count(), 0, 'no second switch inside the DMX card');
    await page.fill('#dmxuni', '99999');
    await page.click('#dmxsave');
    await page.waitForFunction(() => /universe/i.test(document.getElementById('msg').textContent));
    await page.fill('#dmxuni', '2');
    await page.click('#dmxsave');
    await page.waitForFunction(() => fetch('/api/dmx').then((r) => r.json()).then((d) => d.universe === 2));
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
    await page.selectOption('#midiaction', 'pad');
    await page.selectOption('#midibank', '1');
    await page.click('#midilearn');
    await page.waitForSelector('#midilearning');
    await page.click('#midicancel');
    await page.waitForSelector('#midilearn');
    await page.click('#midibuiltin');
    await page.waitForFunction(() => /Built-in map: off/.test(document.getElementById('midibuiltin').textContent));
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
    await page.fill('#streamname', 'Cam');
    await page.fill('#streamurl', 'file:///etc/passwd');
    await page.click('#streamadd');
    await page.waitForFunction(() => /must start with/.test(document.getElementById('msg').textContent));
    await page.fill('#streamurl', 'rtsp://admin:hunter2@10.0.0.5/live');
    await page.click('#streamadd');
    await page.waitForSelector('.stream-entry:has-text("rtsp://***@10.0.0.5/live")');
    assert(!(await page.textContent('body')).includes('hunter2'), 'stream password is never shown');
    await sysIndex();
    await chip('Streams', 'Ready');
    await sys('Streams');
    await page.click('.stream-entry >> button:has-text("Remove")');
    await page.waitForSelector('#streamempty');
    await onPage('Streams');
    await sysIndex();
    await chip('Streams', 'Set up');
    // Schedule: switch the module on, add an entry, turn the schedule on and off, remove the entry
    await sys('Schedule');
    await switchOn('Schedule');
    await page.waitForSelector('#schedclock');
    await page.selectOption('#schedaction', 'stop');
    await page.fill('#schedlabel', 'Close');
    await page.click('#schedadd');
    await page.waitForSelector('.sched-entry:has-text("Close: 18:00")');
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
    await page.click('.sched-entry >> button:has-text("Remove")');
    await page.waitForSelector('#schedempty');
    await page.selectOption('#schedaction', 'preset');
    await page.fill('#schedpreset', 'startlessonce01');
    await page.click('#schedadd');
    await page.waitForSelector('.sched-entry:has-text("Start script startlessonce01")');
    await page.click('.sched-entry >> button:has-text("Remove")');
    await page.waitForSelector('#schedempty');
    await onPage('Schedule');
    // Projectors: a public address is refused; the harness's fake PJLink projector (loopback, allowed there only)
    // is added, says who it is, shows its state, lamp hours and a warning, takes an input, a label and a mute
    await sys('Projectors');
    await switchOn('Projectors');
    await page.waitForSelector('#projline');
    await page.fill('#projname', 'Main');
    await page.fill('#projhost', '8.8.8.8');
    await page.click('#projadd');
    await page.waitForFunction(() => /private/.test(document.getElementById('msg').textContent));
    await page.fill('#projhost', '127.0.0.1');
    await page.fill('#projport', String(info.projector_ports[0]));
    await page.fill('#projpw', 'secret1');
    await page.click('#projadd');
    await page.waitForSelector('.proj-entry:has-text("password set")');
    // The password is gone from the form (page.content() does not show what an input holds, so ask the input),
    // and no answer of the API carries it
    if (await page.inputValue('#projpw') !== '') problems.push('the projector password is still in the form');
    const told = await page.evaluate(() => Promise.all(['/api/projectors', '/api/health', '/api/status', '/api/modules'].map((u) => fetch(u).then((r) => r.text()))));
    if (told.join(' ').includes('secret1')) problems.push('the projector password came back from the API');
    if (!told[0].includes('"has_password": true') && !told[0].includes('"has_password":true')) problems.push('the projector list did not answer: ' + told[0].slice(0, 200));
    if ((await page.content()).includes('secret1')) problems.push('the projector password came back to the page');
    await page.waitForSelector('.proj-details:has-text("NXLX Test Works FP-1")', { timeout: 15000 });
    await page.waitForSelector('.proj-status:has-text("lamp 1234 h")', { timeout: 15000 });
    if (!/^on/i.test(await page.textContent('.proj-status'))) problems.push('the projector status does not say On: ' + await page.textContent('.proj-status'));
    await page.waitForSelector('.proj-warn:has-text("Warning: filter")');
    await page.selectOption('.proj-input', '31');
    await page.waitForSelector('.proj-status:has-text("input Digital 1")', { timeout: 15000 });
    if (await pjlink(info.projector_ports[0], 'secret1', 'INPT ?') !== '31') problems.push('the fake projector is not on input 31');
    // Labels have their own chooser: naming an input that is not in use must not switch the projector to it
    await page.selectOption('.proj-labelfor', '32');
    await page.fill('.proj-label', 'Box');
    await page.click('.proj-setlabel');
    await page.waitForSelector('.proj-input option:has-text("Box (Digital 2)")', { state: 'attached', timeout: 15000 });
    await page.click('button[aria-label="Check Main"]');          // a fresh status, so a wrong switch would show
    await page.waitForFunction(() => /Power: on/.test(document.querySelector('.proj-entry').textContent), null, { timeout: 15000 });
    if (await pjlink(info.projector_ports[0], 'secret1', 'INPT ?') !== '31') problems.push('labelling an input switched the projector to it');
    if (!/input Digital 1/.test(await page.textContent('.proj-status'))) problems.push('the status moved after labelling: ' + await page.textContent('.proj-status'));
    await page.selectOption('.proj-labelfor', '31');
    await page.fill('.proj-label', 'Matrix');
    await page.click('.proj-setlabel');
    await page.waitForSelector('.proj-status:has-text("input Matrix (Digital 1)")', { timeout: 15000 });
    await page.click('button[aria-label="Mute picture Main"]');
    await page.waitForSelector('.proj-status:has-text("picture muted")', { timeout: 15000 });
    await page.click('button[aria-label="Unmute picture Main"]');
    await page.waitForFunction(() => !/muted/.test(document.querySelector('.proj-status').textContent), null, { timeout: 15000 });
    await page.click('button[aria-label="Refresh details Main"]');
    await page.waitForFunction(() => /Refresh details: done/.test(document.getElementById('msg').textContent));
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
    await page.click('.sched-entry >> button:has-text("Remove")');
    await page.waitForSelector('#schedempty');
    await page.click('nav >> text=Room');
    await page.click('button[aria-label="Remove scene Console night"]');
    await page.waitForSelector('#roomnoscenes');
    await sys('Room');
    await page.click('#sysswitch');
    await page.waitForSelector('#sysoff');
    await page.waitForFunction(() => !/Room/.test(document.querySelector('nav').textContent));
    await sys('Projectors');
    await page.click('button[aria-label="Remove Painting"]');
    await page.waitForFunction(() => document.querySelectorAll('.proj-entry').length === 1);
    await page.click('.proj-entry >> button:has-text("Remove")');
    await page.waitForFunction(() => !document.querySelector('.proj-entry'));
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
    await page.click('#syncrole-server');
    await page.waitForSelector('#syncline:has-text("Server")');
    await page.selectOption('#wallcols', '2');
    await page.selectOption('#wallcol', '1');
    await page.fill('#wallbezel', '3');
    await page.click('#wallsave');
    try {
      await page.waitForFunction(() => fetch('/api/sync').then((r) => r.json()).then((d) => d.config.wall.cols === 2 && d.config.wall.col === 1 && d.config.wall.bezel === 3), null, { timeout: 15000 });
    } catch (e) { throw new Error(e.message.split('\n')[0] + ' | the wall was not saved: ' + await syncState()); }
    // The card is rebuilt by the answer to the save above and, while it is a server, every 2 seconds. A column chosen
    // just before such a rebuild must still be there at the click. It is set without an event (as the Network step
    // does), the line at the top of the card is marked, and the step waits until that line is a new one.
    await page.evaluate(() => { document.getElementById('syncline').dataset.seen = '1'; document.getElementById('wallcol').value = '2'; });
    await page.waitForFunction(() => { const l = document.getElementById('syncline'); return l && !l.dataset.seen; }, null, { timeout: 8000 });
    if (await page.evaluate(() => document.getElementById('wallcol').value) !== '2') throw new Error('a redraw of the Sync card lost the chosen column: ' + await syncState());
    await page.click('#wallsave');
    try {
      await page.waitForFunction(() => /inside the wall/.test(document.getElementById('msg').textContent), null, { timeout: 15000 });
    } catch (e) { throw new Error(e.message.split('\n')[0] + ' | no refusal shown: ' + await syncState()); }
    await page.click('#syncrole-off');
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
    await page.click('#mapright');
    await page.waitForFunction((x) => fetch('/api/mapper').then((r) => r.json()).then((d) => Math.abs(d.surfaces[0].vertices[0][0] - (x + 50)) < 0.01), x0);
    await page.fill('#mapsetname', 'Main stage');
    await page.click('#mapsave');
    await fitsCard('#mapcard', 'Mapping card');
    await page.waitForSelector('#mapsets >> option:has-text("Main stage")', { state: 'attached' });
    await page.click('#mapon');
    await page.waitForSelector('#mapstatus:has-text("Mapping is on")', { timeout: 20000 });
    if (shots) await page.screenshot({ path: path.join(shots, '7-mapper.png'), fullPage: true });
    await page.click('#mapon');
    await page.waitForSelector('#mapstatus:has-text("Mapping is off")');
    await page.click('.map-entry >> button:has-text("Remove")');
    await page.waitForFunction(() => !document.querySelector('.map-entry'));
    await sys('Remote support');
    // Remote support: off by default; settings saved and checked; allowing it shows the start controls
    await page.waitForSelector('#supportcard #supportsave');
    assert(/Remote support is off/.test(await page.textContent('#supportcard')), 'remote support starts off');
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
    await page.waitForSelector('#supportwhy');                       // no helper in the test harness: said plainly
    await page.fill('#support-address', '10.99.0.40');
    await page.click('#supportsave');
    await page.waitForFunction(() => /inside the support network/.test(document.getElementById('msg').textContent));
    await page.click('#sysswitch');
    await page.waitForSelector('#sysswitch[aria-checked="false"]');
    await page.waitForFunction(() => /Remote support is off/.test(document.getElementById('supportcard').textContent));
    assert.strictEqual((await get('/api/support')).config.allowed, false, 'the page switch turned remote support off');
    await onPage('Remote support');
    await sys('Look');
    await page.click('button:has-text("Night red")');
    await page.waitForFunction(() => getComputedStyle(document.body).backgroundColor === 'rgb(0, 0, 0)');
    await onPage('Look');
    await sys('People and codes');
    await page.click('text=Create guest link');
    await page.waitForFunction(() => { const i = document.querySelector('input[aria-label="Guest link"]'); return i && !i.hidden && /#token=/.test(i.value); });
    const guestLink = await page.inputValue('input[aria-label="Guest link"]');
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
    page.once('dialog', (d) => d.accept());
    await page.setInputFiles('#importpick', { name: 'my-settings.json', mimeType: 'application/json', buffer: Buffer.from(exportedText) });
    await page.waitForFunction(() => /Settings imported/.test(document.getElementById('msg').textContent));
    await page.waitForFunction(() => /no passwords/.test(document.getElementById('importresult').textContent));
    const mixNow = await page.evaluate(() => fetch('/api/status').then((r) => r.json()).then((j) => j.mix.duration));
    assert.strictEqual(mixNow, exportedFile.settings.mix.duration, 'the import put the exported value back');
    assert.strictEqual(await page.evaluate(() => fetch('/api/status').then((r) => r.status)), 200, 'still paired after an import');
    page.once('dialog', (d) => d.accept());
    await page.setInputFiles('#importpick', { name: 'twice.json', mimeType: 'application/json', buffer: Buffer.from(exportedText.replace('{', '{"format": "x", ')) });
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
    let asked = '';
    page.once('dialog', (d) => { asked = d.message(); d.dismiss(); });
    await page.click('#resetbtn');
    await page.waitForFunction(() => document.getElementById('resetmedia').value === 'keep');
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

    // Scan-to-join: the owner makes a guest code; a phone opens the QR code's link and joins with one tap as view only
    await sys('People and codes');
    await page.waitForSelector('#newguest');
    await page.click('#newguest');
    await page.waitForSelector('.join-code[data-role="view"]');
    const joinCode = await page.evaluate(() => document.querySelector('.join-code[data-role="view"] .big-code').textContent);
    assert(/^[0-9]{6}$/.test(joinCode), 'a 6 digit guest code: ' + joinCode);
    await page.waitForFunction(() => { const i = document.querySelector('.join-code[data-role="view"] img.qr'); return i && i.complete && i.naturalWidth > 0; });
    await page.click('#showaccess');
    await page.waitForFunction(() => /On the display now/.test(document.getElementById('accessscreenline').textContent));
    await page.click('#hideaccess');
    await page.waitForFunction(() => /Nothing on the display/.test(document.getElementById('accessscreenline').textContent));
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
    await page.click('nav >> text=Live');
    await page.waitForSelector('.pads');
    assert.strictEqual(await page.locator('#vibes').count(), 0, 'no Vibes button while the module is off');
    assert.strictEqual(await page.locator('#shaderslink').count(), 0, 'and no Shaders link');
    await sys('Shaders and Vibes');
    assert.strictEqual(await page.locator('#shaderpage').count(), 0, 'the module off shows only the description and the big button');
    await switchOn('Shaders and Vibes');
    assert(await moduleIsOn('shaders'), 'the shaders module is on');
    await page.waitForSelector('#shadercard [data-shader="nxlx-tide.fs"]');
    assert.strictEqual(await page.locator('#shadercard [data-shader]').count(), 10, 'the ten bundled shaders are listed');
    assert.deepStrictEqual(await page.$$eval('#shaderpage .card', (cs) => cs.map((x) => x.id)), ['shadernow', 'shadercard', 'vibessettings', 'shaderremote'],
      'the page, top to bottom: now, the library, Vibes settings, other ways to control it (no sliders while nothing plays)');
    assert.strictEqual(await page.textContent('#shaderplaying'), 'No shader on screen');
    assert.strictEqual(await page.textContent('#vibesbtn'), 'Start Vibes');
    assert(/Light work/.test(await page.textContent('#shadercard [data-shader="nxlx-tide.fs"]')), 'a shader says how much work it is');
    await onPage('Shaders and Vibes');
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
    assert(!(await page.isVisible('#vibesskip')), 'no Next one while Vibes is not playing');
    await fitsPhone('Live with the Vibes button');
    await page.click('#vibes');
    await page.waitForFunction(() => /^Vibes: [A-Z]/.test((document.getElementById('np') || {}).textContent), null, { timeout: 15000 });
    await page.waitForFunction(() => /^Vibes is playing: [A-Z]/.test((document.getElementById('vibeswords') || {}).textContent));
    assert.strictEqual(await page.getAttribute('#vibes', 'aria-pressed'), 'true');
    assert.strictEqual(await page.textContent('#vibessub'), 'Tap to stop');
    // Starting, stopping, skipping and what is playing are together on Live
    await page.waitForSelector('#vibesskip:visible');
    assert((await page.evaluate(() => document.getElementById('vibesskip').getBoundingClientRect().height)) >= 44, 'Next one on Live is easy to hit');
    const skipped = page.waitForResponse((r) => r.url().endsWith('/api/vibes') && r.request().postData() === '{"next":true}');
    await page.click('#vibesskip');
    assert.strictEqual((await skipped).status(), 200, 'Next one on Live goes to the next shader');
    await fitsPhone('Live while Vibes is playing');
    if (shots) await page.screenshot({ path: path.join(shots, '8-live-vibes.png') });
    // The link lands on the same page, and Back returns to Live
    await page.click('#shaderslink');
    await page.waitForSelector('#syspage h1:text-is("Shaders and Vibes")');
    await page.waitForFunction(() => /Vibes is playing/.test((document.getElementById('shaderline') || {}).textContent));
    assert.strictEqual(await page.textContent('#vibesbtn'), 'Stop Vibes');
    assert.strictEqual(await page.textContent('#vibesnext'), 'Next one');
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
    assert.deepStrictEqual(await page.$$eval('#shaderpage .card', (cs) => cs.map((x) => x.id)), ['shadernow', 'shadercard', 'shadercontrols', 'vibessettings', 'shaderremote']);
    const speed = (await get('/api/shaders')).shaders.find((x) => x.id === 'nxlx-tide.fs').inputs.find((i) => i.name === 'speed');
    assert(new RegExp(speed.min + ' to ' + speed.max + ', normally ' + speed.default).test(await page.textContent('#shadersliders')), 'a slider says its range and its usual value');
    assert(/not saved/.test(await page.textContent('#slidernote')), 'the page says slider values are not saved');
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
    assert(mediums.length >= 1 && mediums.length < 10, 'the work filter narrows the list: ' + mediums.length);
    assert((await page.$$eval('#shadercard [data-shader]', (rs) => rs.filter((r) => !r.hidden).every((r) => /Medium work/.test(r.textContent)))), 'and shows only medium work');
    await page.selectOption('#shadercost', 'all');
    assert.strictEqual((await shownRows()).length, 10);
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
    assert(/Fewer lines is lighter work/.test(await page.textContent('#shaderadvanced')), 'picture detail says what fewer lines does');
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
    assert.strictEqual((await get('/api/shaders')).shaders.length, 10, 'the uploaded file is gone from the box');
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
    assert.strictEqual(await presenter.locator('#shadercard button[aria-label^="Play "]').count(), 10, 'a presenter can play each shader');
    assert(/in the Vibes rotation/.test(await presenter.textContent('#shadercard [data-shader="nxlx-tide.fs"]')), 'a presenter reads whether a shader is in the rotation');
    assert.strictEqual(await presenter.locator('#syspage button:disabled').count(), 0, 'nothing disabled on a presenter\'s Shaders page');
    await presenter.click('#shadercard [data-shader="nxlx-tide.fs"] button[aria-label="Play Tide"]');
    await presenter.waitForFunction(() => (document.getElementById('shaderplaying') || {}).textContent === 'Tide');
    await presenter.waitForSelector('#shin-speed');                  // a presenter gets the sliders
    await presenter.click('#vibesbtn');
    await presenter.waitForSelector('#vibesbtn:text-is("Stop Vibes")', { timeout: 15000 });
    await presenter.waitForSelector('#vibesnext');
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
      ['Health', 'Projectors', 'Shaders and Vibes', 'Sound', 'Streams', 'Boxes in step', 'About and power'], 'the rows a presenter sees');
    assert.strictEqual(await presenter.locator('#notbuilt').count(), 0, 'a presenter gets no list of modules');
    await presenter.click('.navrow:has-text("Projectors")');
    await presenter.waitForSelector('#projline');
    assert.strictEqual(await presenter.locator('.switch').count(), 0, 'a presenter gets no switch');
    assert.strictEqual(await presenter.locator('#projadd').count(), 0, 'and no form to add a projector');
    await presenter.click('#sysback');
    await presenter.waitForSelector('#sysindex');
    await liveCtx.close();

    // A laptop: the Shaders page is a workspace. The library is a column that scrolls by itself, what is playing and
    // its controls are beside it and in view, the settings and controllers are a third column; nothing sticks out at
    // any width, on this page or on Live.
    await page.setViewportSize({ width: 1366, height: 768 });
    assert.strictEqual(await post('/api/shaders/play', { id: 'nxlx-tide.fs' }), 200);
    await page.click('nav >> text=Live');
    await page.waitForSelector('#shaderslink');
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

    // Desktop width
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.click('nav >> text=Live');
    await page.waitForSelector('.pads');
    if (shots) await page.screenshot({ path: path.join(shots, '5-desktop.png') });

    const csp = problems.filter((t) => /Content Security Policy|Refused to/i.test(t));
    assert.deepStrictEqual(csp, [], 'CSP violations: ' + csp.join('; '));
    assert.deepStrictEqual(problems, [], 'console problems: ' + problems.join('; '));
    console.log('panel browser test: OK');
  } catch (e) {
    failed = true;
    console.error('FAILED:', e.message, (e.stack || '').split('\n').filter(function (l) { return /panel.test.js/.test(l); }).slice(0, 2).join(' | '));
  } finally {
    await browser.close();
    server.kill();
  }
  process.exit(failed ? 1 : 0);
})();
