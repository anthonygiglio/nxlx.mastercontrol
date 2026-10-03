// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// Browser test: drives the real panel against tests/ui/harness.py.
// Run: node tests/ui/panel.test.js   (needs playwright and a Chromium)
const { chromium } = require('playwright');
const { spawn } = require('child_process');
const path = require('path');
const assert = require('assert');

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
    // Projectors: a public address is refused; a private one is added (no projector is contacted) and removed
    await page.click('.item:has-text("Projector control") >> button');
    await page.waitForSelector('#projline');
    await page.fill('#projname', 'Main');
    await page.fill('#projhost', '8.8.8.8');
    await page.click('#projadd');
    await page.waitForFunction(() => /private/.test(document.getElementById('msg').textContent));
    await page.fill('#projhost', '192.168.0.50');
    await page.fill('#projpw', 'secret1');
    await page.click('#projadd');
    await page.waitForSelector('.proj-entry:has-text("password set")');
    if ((await page.content()).includes('secret1')) problems.push('the projector password came back to the page');
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

    // Box care: export the settings (no access data in the file), change something, import the file back; a file
    // with a repeated key is refused; diagnostics downloads; factory reset wants a choice and can be cancelled
    // (a real reset would unpair this test's own device, so it is covered by tests/test_boxcare.py)
    await page.waitForSelector('#settingscard #exportbtn');
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
