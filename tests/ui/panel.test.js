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

    // System: modules, appearance, guest link
    await page.click('nav >> text=System');
    await page.waitForSelector('h1:has-text("System")');
    assert(await page.isVisible('text=NDI'), 'NDI module listed');
    assert(await page.isVisible('text=Not built yet'), 'planned modules are labelled');
    // Network: switch the module on, preview, apply, watch the countdown, confirm
    await page.click('.item:has-text("Network settings") >> button');
    await page.waitForSelector('#netiface');
    await page.waitForSelector('#netcard >> text=192.168.1.9/24', { timeout: 8000 });  // the address arrives after the card first draws
    await page.click('#netmodes >> text=Fixed address');
    await page.fill('#netaddr', '192.168.50.20');
    await page.fill('#netprefix', '24');
    // The gateway is set without any input event (as a lost or late event would): the redraw must still keep it.
    // (waits for the field: the card may be being redrawn at this moment, which is exactly what this step is about)
    await page.waitForFunction(() => { const g = document.getElementById('netgw'); if (!g) return false; g.value = '192.168.50.1'; return true; }, null, { timeout: 8000 });
    // A redraw of the screen must not wipe what was typed
    await page.click('nav >> text=System');
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
    await page.waitForSelector('#oscline:has-text("Off")');
    await page.click('#osctoggle');
    await page.waitForFunction(() => /Listening on UDP/.test(document.getElementById('oscline').textContent));
    await page.click('#osctoggle');
    await page.waitForFunction(() => document.getElementById('oscline').textContent === 'Off');
    // Autostart: reject a missing clip, then save "play every clip" and see it summarised
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
    // DMX: switch the module on, reject a bad universe, turn it on and off
    await page.click('.item:has-text("DMX over the network") >> button');
    await page.waitForSelector('#dmxline:has-text("Off")');
    await page.fill('#dmxuni', '99999');
    await page.click('#dmxsave');
    await page.waitForFunction(() => /universe/i.test(document.getElementById('msg').textContent));
    await page.fill('#dmxuni', '2');
    await page.click('#dmxsave');
    await page.waitForFunction(() => document.getElementById('dmxuni').value === '2');
    // MIDI: switch the module on; nothing is plugged in here, so check the card and the learn flow, then turn it off
    await page.click('.item:has-text("MIDI controller") >> button');
    await page.waitForSelector('#midiline:has-text("Off")');
    await page.click('#miditoggle');
    await page.waitForFunction(() => /waiting for a controller/.test(document.getElementById('midiline').textContent));
    await page.waitForSelector('#midinomap');
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
    // Streams: switch the module on, reject a bad address, save one with a login (hidden), remove it
    await page.click('.item:has-text("Streams: SRT") >> button');
    await page.waitForSelector('#streamempty');
    await page.fill('#streamname', 'Cam');
    await page.fill('#streamurl', 'file:///etc/passwd');
    await page.click('#streamadd');
    await page.waitForFunction(() => /must start with/.test(document.getElementById('msg').textContent));
    await page.fill('#streamurl', 'rtsp://admin:hunter2@10.0.0.5/live');
    await page.click('#streamadd');
    await page.waitForSelector('.stream-entry:has-text("rtsp://***@10.0.0.5/live")');
    assert(!(await page.textContent('body')).includes('hunter2'), 'stream password is never shown');
    await page.click('.stream-entry >> button:has-text("Remove")');
    await page.waitForSelector('#streamempty');
    // Schedule: switch the module on, add an entry, turn the schedule on and off, remove the entry
    await page.click('.item:has-text("Weekly schedule") >> button');
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
    // Projectors: a public address is refused; the harness's fake PJLink projector (loopback, allowed there only)
    // is added, says who it is, shows its state, lamp hours and a warning, takes an input, a label and a mute
    await page.click('.item:has-text("Projector control") >> button');
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
    await page.waitForSelector('#healthcard .item:has-text("Projector: Main"):has-text("lamp 1234 h"):has-text("Warning: filter")', { timeout: 15000 });
    if ((await page.content()).includes('secret1')) problems.push('the projector password came back to the page');
    if ((await page.evaluate(() => fetch('/api/projectors').then((r) => r.text()))).includes('secret1')) problems.push('the projector password came back from the API');
    await page.click('.proj-entry >> button:has-text("Remove")');
    await page.waitForFunction(() => !document.querySelector('.proj-entry'));
    // Sync and video wall: switch the module on, be a server, set a wall tile, back to off
    await page.click('.item:has-text("Video wall and sync") >> button');
    await page.waitForSelector('#syncrole-server');
    await page.click('#syncrole-server');
    await page.waitForSelector('#syncline:has-text("Server")');
    await page.selectOption('#wallcols', '2');
    await page.selectOption('#wallcol', '1');
    await page.fill('#wallbezel', '3');
    await page.click('#wallsave');
    await page.waitForFunction(() => fetch('/api/sync').then((r) => r.json()).then((d) => d.config.wall.cols === 2 && d.config.wall.col === 1 && d.config.wall.bezel === 3));
    await page.selectOption('#wallcol', '2');
    await page.click('#wallsave');
    await page.waitForFunction(() => /inside the wall/.test(document.getElementById('msg').textContent));
    await page.click('#syncrole-off');
    await page.waitForSelector('#syncline:has-text("Off")');
    await fitsCard('#synccard', 'Sync card');
    // Projection mapping: switch the module on, add a quad on Mix, drag and nudge a corner, save, switch it on
    await page.click('.item:has-text("Projection mapper") >> button');
    await page.waitForFunction(() => /Projection mapper/.test(document.body.textContent));
    await page.click('nav >> text=Mix');
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
    await page.click('nav >> text=System');
    await page.waitForSelector('h1:has-text("System")');
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
    await page.click('button:has-text("Night red")');
    await page.waitForFunction(() => getComputedStyle(document.body).backgroundColor === 'rgb(0, 0, 0)');
    await page.click('text=Create guest link');
    await page.waitForFunction(() => { const i = document.querySelector('input[aria-label="Guest link"]'); return i && !i.hidden && /#token=/.test(i.value); });
    const guestLink = await page.inputValue('input[aria-label="Guest link"]');
    await fitsCard('.card', 'System');
    if (shots) await page.screenshot({ path: path.join(shots, '4-system.png'), fullPage: true });

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

    // Scan-to-join: the owner makes a guest code; a phone opens the QR code's link and joins with one tap as view only
    await page.click('nav >> text=System');
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
