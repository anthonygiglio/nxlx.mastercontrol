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
    assert.deepStrictEqual(await page.$$eval('.navname', (ns) => ns.map((x) => x.textContent)), ['Health', 'Projectors', 'Room', 'Schedule', 'Vibes', 'People and codes', 'Sound',
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
    await sys('OSC');
    await page.waitForSelector('#oscline:has-text("Off")');
    await page.click('#osctoggle');
    await page.waitForFunction(() => /Listening on UDP/.test(document.getElementById('oscline').textContent));
    await page.click('#osctoggle');
    await page.waitForFunction(() => document.getElementById('oscline').textContent === 'Off');
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
    // DMX: switch the module on, reject a bad universe, turn it on and off. The module is on but DMX itself is not
    // listening yet: the page and the index row both say so.
    await sys('DMX lighting desk');
    await switchOn('DMX lighting desk');
    await page.waitForSelector('#dmxline:has-text("Off")');
    await page.waitForSelector('#sysstate:has-text("not listening")');
    await page.fill('#dmxuni', '99999');
    await page.click('#dmxsave');
    await page.waitForFunction(() => /universe/i.test(document.getElementById('msg').textContent));
    await page.fill('#dmxuni', '2');
    await page.click('#dmxsave');
    await page.waitForFunction(() => document.getElementById('dmxuni').value === '2');
    await onPage('DMX lighting desk');
    await sysIndex();
    await chip('DMX lighting desk', 'Off');
    await page.waitForSelector(`${rowOf('DMX lighting desk')} .navstate:has-text("On, but not listening")`);
    // MIDI: switch the module on; nothing is plugged in here, so check the card and the learn flow, then turn it off
    await sys('MIDI controller');
    await switchOn('MIDI controller');
    await page.waitForSelector('#midiline:has-text("Off")');
    await page.click('#miditoggle');
    await page.waitForFunction(() => /waiting for a controller/.test(document.getElementById('midiline').textContent));
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
    await page.click('#miditoggle');
    await page.waitForFunction(() => document.getElementById('midiline').textContent === 'Off');
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
    await page.click('#schedtoggle');
    await page.waitForFunction(() => document.getElementById('schedtoggle').getAttribute('aria-pressed') === 'true');
    await page.click('#schedtoggle');
    await page.waitForFunction(() => document.getElementById('schedtoggle').getAttribute('aria-pressed') === 'false');
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
    await page.waitForSelector('#syspage .card:has-text("Where the room is run and set up")');
    await sysIndex();
    await chip('Room', 'Set up');
    await page.waitForSelector(`${rowOf('Room')} .navstate:has-text("No groups or scenes yet")`);
    await sys('Room');
    await page.click('#syspage button:has-text("Open Room")');
    await page.waitForSelector('#roomsetup #roomgname');
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
      await staff.waitForSelector('#roomscreen .room-group:has-text("Painting wall") .room-state:has-text("Warming up")', { timeout: 15000 });
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
    await page.click('#syspage >> text=Open Mix');
    await page.waitForSelector('#mapadd-quad');
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
    await page.waitForSelector('#supportcard #supportallow');
    assert(/Remote support is off/.test(await page.textContent('#supportcard')), 'remote support starts off');
    await page.fill('#support-endpoint', 'support.example.com:51820');
    await page.fill('#support-server_key', 'a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=');
    await page.fill('#support-address', '10.77.0.40');
    await page.fill('#support-network', '10.77.0.0/24');
    await page.click('#supportsave');
    await page.click('#supportallow');
    await page.waitForSelector('#supportstart');
    await page.waitForSelector('#supportwhy');                       // no helper in the test harness: said plainly
    await page.fill('#support-address', '10.99.0.40');
    await page.click('#supportsave');
    await page.waitForFunction(() => /inside the support network/.test(document.getElementById('msg').textContent));
    await page.click('#supportallow');
    await page.waitForFunction(() => /Remote support is off/.test(document.getElementById('supportcard').textContent));
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
    const post = (path, body) => page.evaluate(([p, b]) => fetch(p, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-PVJ-Request': '1' }, body: JSON.stringify(b) }).then((r) => r.status), [path, body]);
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

    // Shaders and Vibes: switch the module on, start the rotation with the big button on Live, then choose one shader
    // by hand on Mix (which ends the rotation) and stop. The harness player draws nothing (--vo=null), so this
    // checks the panel and the API, not the picture; the picture is checked in tests/test_shaders_gpu.py.
    await page.click('nav >> text=Live');
    await page.waitForSelector('.pads');
    assert.strictEqual(await page.locator('#vibes').count(), 0, 'no Vibes button while the module is off');
    await sys('Vibes');
    await switchOn('Vibes');
    await page.waitForFunction(() => fetch('/api/modules').then((r) => r.json()).then((d) => d.modules.some((m) => m.id === 'shaders' && m.enabled)));
    assert(/big Vibes button on the Live screen/.test(await page.textContent('#sysbody')), 'the Vibes page says it is started on Live');
    await onPage('Vibes');
    await page.click('#syspage >> text=Open Live');
    await page.waitForSelector('#vibes');
    await page.click('#vibes');
    await page.waitForFunction(() => /^Vibes: nxlx-/.test((document.getElementById('np') || {}).textContent), null, { timeout: 15000 });
    await page.waitForFunction(() => /Vibes is on/.test((document.getElementById('vibes') || {}).textContent));
    // Switching Vibes off while it is on the screen asks first, in place; "no" puts the switch back and changes nothing
    await sysIndex();
    await chip('Vibes', 'Active');
    await sys('Vibes');
    await page.click('#sysswitch');
    await page.waitForSelector('#confirmrow:has-text("Vibes is on the screen. Switching off stops it now.")');
    assert(!(await page.isVisible('#sysswitch')), 'the question takes the place of the switch');
    await fitsPhone('Vibes page, asking');
    await page.click('#confirmno');
    await page.waitForSelector('#sysswitch[aria-checked="true"]');
    assert.strictEqual(await page.locator('#confirmrow').count(), 0, 'the question is gone after "no"');
    assert(await page.evaluate(() => fetch('/api/modules').then((r) => r.json()).then((d) => d.modules.some((m) => m.id === 'shaders' && m.enabled))), 'Vibes is still on after "no"');
    assert(await page.evaluate(() => fetch('/api/shaders').then((r) => r.json()).then((d) => d.vibes.running)), 'and still playing');
    await page.click('nav >> text=Mix');
    await page.waitForSelector('#shadercard [data-shader="nxlx-tide.fs"]');
    assert.strictEqual(await page.locator('#shadercard [data-shader]').count(), 10, 'the ten bundled shaders are listed');
    await page.waitForFunction(() => /Vibes is on/.test((document.getElementById('shaderline') || {}).textContent));
    // By its label, not its text: when the rotation happens to be showing this very shader (1 time in 10) the button
    // reads "On screen", and a click on "Play" then waited for 30 seconds and failed.
    await page.click('#shadercard [data-shader="nxlx-tide.fs"] button[aria-label="Play nxlx-tide"]');
    await page.waitForFunction(() => /On screen: nxlx-tide/.test((document.getElementById('shaderline') || {}).textContent));
    await page.waitForSelector('#shin-speed');                       // its number inputs are sliders
    const vibesAfter = await page.evaluate(() => fetch('/api/shaders').then((r) => r.json()).then((d) => d.vibes.running));
    assert.strictEqual(vibesAfter, false, 'choosing a shader by hand ends the rotation');
    await page.fill('#vibesdwell', '45');
    await page.click('#shadersave');
    await page.waitForFunction(() => fetch('/api/shaders').then((r) => r.json()).then((d) => d.config.dwell === 45));
    await page.click('nav >> text=Live');
    await page.waitForFunction(() => /^Shader: nxlx-tide/.test((document.getElementById('np') || {}).textContent), null, { timeout: 8000 });
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
    await presenter.click('nav >> text=System');
    await presenter.waitForSelector('#sysindex');
    assert.deepStrictEqual(await presenter.$$eval('.navname', (ns) => ns.map((x) => x.textContent)),
      ['Health', 'Projectors', 'Vibes', 'Sound', 'Streams', 'Boxes in step', 'About and power'], 'the rows a presenter sees');
    assert.strictEqual(await presenter.locator('#notbuilt').count(), 0, 'a presenter gets no list of modules');
    await presenter.click('.navrow:has-text("Projectors")');
    await presenter.waitForSelector('#projline');
    assert.strictEqual(await presenter.locator('.switch').count(), 0, 'a presenter gets no switch');
    assert.strictEqual(await presenter.locator('#projadd').count(), 0, 'and no form to add a projector');
    await presenter.click('#sysback');
    await presenter.waitForSelector('#sysindex');
    await liveCtx.close();

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
