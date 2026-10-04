// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// Takes the cropped screenshots of the panel that live in docs/images/ui.
// Run: SHOTS=docs/images/ui node tests/ui/screenshots.js   (needs playwright and a Chromium, like panel.test.js)
// It drives the same harness as the browser test (a real panel, a headless mpv, a fake network helper), so
// what you see is what the code produces. Devices, network addresses and clips shown are the harness's.
const { chromium } = require('playwright');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

const out = process.env.SHOTS || path.join(__dirname, '..', '..', 'docs', 'images', 'ui');
const pyBin = process.env.PYTHON || 'python3';
fs.mkdirSync(out, { recursive: true });

function startServer() {
  return new Promise((resolve, reject) => {
    const p = spawn(pyBin, [path.join(__dirname, 'harness.py')], { cwd: path.join(__dirname, '..', '..'), stdio: ['ignore', 'pipe', 'inherit'] });
    let buf = '';
    p.stdout.on('data', (d) => { buf += d; if (buf.includes('\n')) resolve({ p, info: JSON.parse(buf.split('\n')[0]) }); });
    p.on('exit', (c) => reject(new Error('harness exited ' + c)));
  });
}

(async () => {
  const { p: server, info } = await startServer();
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM || undefined });
  const base = 'http://127.0.0.1:' + info.port;
  const failures = [];
  const saved = [];
  const skipped = [];
  // One bad shot must not lose the others.
  async function shot(name, fn) {
    try { await fn(path.join(out, name + '.png')); saved.push(name); } catch (e) { failures.push(name + ': ' + e.message.split('\n')[0]); }
  }
  try {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, hasTouch: true, isMobile: true });
    const page = await ctx.newPage();
    const api = (method, url, body) => page.evaluate(async ([m, u, b]) => {
      const r = await fetch(u, { method: m, credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-PVJ-Request': '1' }, body: m === 'POST' ? JSON.stringify(b || {}) : undefined });
      return r.json().catch(() => ({}));
    }, [method, url, body]);
    const card = (title) => page.locator('.card', { has: page.locator('h2', { hasText: new RegExp('^' + title) }) }).first();
    const shell = () => page.locator('.shell').first();
    // A screen taller than the phone: the fixed tab bar would land in the middle of the picture, so for the shot
    // it sits in the page flow, at the bottom of the screen.
    async function whole(f) {
      await page.evaluate(() => { const t = document.querySelector('.tabs'); if (t) t.style.setProperty('position', 'static', 'important'); });
      try { await shell().screenshot({ path: f }); } finally {
        await page.evaluate(() => { const t = document.querySelector('.tabs'); if (t) t.style.removeProperty('position'); });
      }
    }
    const byId = (id) => page.locator('#' + id).first();
    const holding = (sel) => page.locator('.card', { has: page.locator(sel) }).first();   // the card around an element
    const tabs = (show) => page.evaluate((on) => {   // the fixed tab bar would cover the bottom of tall cards
      const t = document.querySelector('.tabs');
      if (t) { if (on) t.style.removeProperty('display'); else t.style.setProperty('display', 'none', 'important'); }
    }, show);
    // Several cards in one picture: from the top of the first to the bottom of the last, full width.
    async function span(f, first, last) {
      const r = await page.evaluate(([a, b]) => {
        const top = document.querySelector(a).getBoundingClientRect().top + window.scrollY;
        const bottom = document.querySelector(b).getBoundingClientRect().bottom + window.scrollY;
        return { y: Math.max(0, Math.floor(top - 8)), height: Math.ceil(bottom - top + 16) };
      }, [first, last]);
      await page.screenshot({ path: f, fullPage: true, clip: { x: 0, y: r.y, width: 390, height: r.height } });
    }

    await page.goto(base + '/');
    await page.waitForSelector('.pin');
    await shot('connect', (f) => shell().screenshot({ path: f }));
    await shot('screen-phone-connect', (f) => page.screenshot({ path: f, fullPage: true }));

    for (let i = 0; i < 4; i++) await page.fill(`input[aria-label="PIN digit ${i + 1}"]`, info.pin[i]);
    await page.fill('#devname', 'Anthony\'s phone');
    await page.click('#pairbtn');
    await page.waitForSelector('.pads');

    // A believable show: labelled pads across a bank, one playing.
    // Extra clips (not playable, only for the lists) so every pad has its own file.
    // Pictures too: numbered ones for Quick play and the slideshow, a PNG for the overlay.
    for (const name of ['loop-a.mkv', 'loop-b.mkv', 'sting.mkv', 'outro.mkv', '01_welcome.jpg', '02_sponsors.jpg', 'logo.png']) {
      await page.evaluate(async (n) => {
        const bytes = new Uint8Array(300000 + n.length * 40000).fill(7);
        await fetch('/api/media/upload?name=' + encodeURIComponent(n), { method: 'POST', credentials: 'same-origin',
          headers: { 'X-PVJ-Request': '1', 'Content-Type': 'application/octet-stream' }, body: bytes });
      }, name);
    }
    const labels = [['Intro', 'intro.mkv'], ['Tunnel', 'tunnel.mkv'], ['Loop A', 'loop-a.mkv'], ['Loop B', 'loop-b.mkv'], ['Sting', 'sting.mkv'], ['Outro', 'outro.mkv']];
    for (let i = 0; i < labels.length; i++) await api('POST', '/api/pads', { bank: 0, index: i, label: labels[i][0], file: labels[i][1] });
    await api('POST', '/api/play', { pad: [0, 0] });
    await page.reload();
    await page.waitForSelector('.pad.on', { timeout: 10000 }).catch(() => {});
    await page.waitForTimeout(1200);
    await shot('live', whole);

    // The transport (seek bar, prev and next, fade in, test pattern) and the snapshot card under it. The snapshot
    // is taken when it works; a headless player may have no picture to give, and then the card is shown untouched.
    await shot('live-transport', async (f) => {
      await page.waitForFunction(() => /\d/.test(document.getElementById('time').textContent));
      await page.click('#previewbtn');
      const pic = await page.waitForSelector('#preview:not([hidden])', { timeout: 8000 }).then(() => true, () => false);
      if (!pic) {
        await page.reload();
        await page.waitForSelector('#previewbtn');
        await page.waitForTimeout(1200);
      }
      await tabs(false);
      try { await span(f, '.screen > .card', '#previewcard'); } finally { await tabs(true); }
    });

    await page.click('nav >> text=Mix');
    await page.waitForSelector('#mo');
    await shot('mix', whole);

    // Projection mapping: the module on, a grid and a quad placed apart, one mapping saved.
    await api('POST', '/api/modules/mapper', { enabled: true });
    try {
      let mapState = await api('POST', '/api/mapper', { action: 'add', type: 'grid' });
      const grid = mapState.surfaces[0].id;
      await api('POST', '/api/mapper', { action: 'grid', id: grid, cols: 3, rows: 2 });
      await api('POST', '/api/mapper', { action: 'move', id: grid, target: 'screen', corner: -1, dx: -300, dy: -130 });
      await api('POST', '/api/mapper', { action: 'rename', id: grid, name: 'Back wall' });
      mapState = await api('POST', '/api/mapper', { action: 'add', type: 'quad' });
      const quad = mapState.surfaces[0].id;
      await api('POST', '/api/mapper', { action: 'move', id: quad, target: 'screen', corner: -1, dx: 420, dy: 170 });
      await api('POST', '/api/mapper', { action: 'move', id: quad, target: 'screen', corner: 1, dx: -60, dy: 40 });
      await api('POST', '/api/mapper', { action: 'rename', id: quad, name: 'Side panel' });
      await api('POST', '/api/mapper', { action: 'save', name: 'Main stage' });
    } catch (e) {
      failures.push('mapper setup: ' + e.message.split('\n')[0]);
    }
    await page.click('nav >> text=Media');   // leave and come back, so Mix is drawn with the module on
    await page.waitForSelector('#uploadbtn');
    await page.click('nav >> text=Mix');
    await page.waitForSelector('#mapbody');
    await tabs(false);
    await shot('mix-mirror', async (f) => { await page.waitForSelector('#fliph'); await holding('#fliph').screenshot({ path: f }); });
    await shot('mix-overlay', async (f) => { await page.waitForSelector('#overlayfile'); await byId('overlaycard').screenshot({ path: f }); });
    await shot('mapper', async (f) => {
      await page.waitForSelector('#mapcanvas');
      await page.waitForSelector('.map-entry:has-text("Side panel")');
      await page.waitForTimeout(600);   // the canvas is painted on the next frame
      await byId('mapcard').screenshot({ path: f });
    });
    await tabs(true);

    await page.click('nav >> text=Media');
    await page.waitForSelector('#uploadbtn');
    await page.waitForTimeout(800);
    // Crop to the content: the list is short and the screen is tall.
    await tabs(false);
    await shot('media', async (f) => {
      const bottom = await page.evaluate(() => { const c = document.querySelectorAll('.screen .card'); return Math.ceil(c[c.length - 1].getBoundingClientRect().bottom + window.scrollY); });
      await page.screenshot({ path: f, fullPage: true, clip: { x: 0, y: 0, width: 390, height: bottom + 16 } });
    });
    await shot('media-quickplay', async (f) => { await page.waitForSelector('#playnum'); await byId('quickplay').screenshot({ path: f }); });
    await shot('slideshow', async (f) => { await page.waitForSelector('#slidestart'); await byId('slideshow').screenshot({ path: f }); });
    // Live input shows only when the box has a capture device; a CI runner has none, so it is skipped, not failed.
    await page.waitForTimeout(600);
    if (await byId('inputcard').isVisible()) await shot('media-live-input', (f) => byId('inputcard').screenshot({ path: f }));
    else skipped.push('media-live-input (no capture device here)');
    await tabs(true);

    // Two join codes, so the Access card shows codes and their QR codes.
    await api('POST', '/api/access/code', { role: 'view', minutes: 60 });
    await api('POST', '/api/access/code', { role: 'live', minutes: 60 });

    // Turn the beta modules on and give them something to show.
    for (const id of ['scheduler', 'control-dmx', 'control-midi', 'inputs-srt', 'network', 'projector']) await api('POST', '/api/modules/' + id, { enabled: true });
    // Projectors at private addresses; nothing is sent to them unless a button is pressed.
    await api('POST', '/api/projectors', { add: { name: 'Main projector', host: '127.0.0.1', port: info.projector_ports[0], password: 'secret1' } });
    await api('POST', '/api/projectors', { add: { name: 'Side projector', host: '127.0.0.1', port: info.projector_ports[1], password: '' } });
    await api('POST', '/api/midi', { enabled: true });
    for (const m of [{ source: 'nanoKONTROL2', kind: 'cc', number: 0, action: 'opacity' }, { source: 'nanoKONTROL2', kind: 'cc', number: 16, action: 'volume' },
      { source: 'Mini', kind: 'note', number: 11, action: 'pad', bank: 0, index: 0 }, { source: 'Mini', kind: 'cc', number: 104, action: 'blackout' }]) await api('POST', '/api/midi/map', { add: m });
    await api('POST', '/api/autostart', { mode: 'file', file: 'intro.mkv', loop: true, delay: 5 });
    await api('POST', '/api/schedule', { enabled: true, entries: [
      { label: 'Warm up', time: '18:15', days: [4, 5], action: 'projector_on' },
      { label: 'Doors', time: '18:30', days: [4, 5], action: 'play', file: 'intro.mkv', loop: true },
      { label: 'Break', time: '20:00', days: [4, 5], action: 'preset', preset: 'startlessonce02' },
      { label: 'Close', time: '23:30', days: [0, 1, 2, 3, 4, 5, 6], action: 'blackout' },
      { label: 'Lamps off', time: '23:45', days: [0, 1, 2, 3, 4, 5, 6], action: 'projector_off' }] });
    await api('POST', '/api/streams', { action: 'add', name: 'Stage camera', url: 'rtsp://admin:secret@192.168.0.40/live' });
    await api('POST', '/api/streams', { action: 'add', name: 'Laptop (SRT)', url: 'srt://192.168.0.20:9000' });
    await page.click('nav >> text=System');
    await page.waitForSelector('#schedclock');
    await page.waitForSelector('.stream-entry');
    await page.waitForSelector('#dmxline');
    await page.waitForSelector('#midiline');
    await page.waitForSelector('#netiface');
    // The newer cards: a wait that runs out must not stop the other shots (the picture then shows what is there).
    const soft = (what, p) => p.catch(() => failures.push('wait: ' + what));
    await soft('projectors', page.waitForSelector('.proj-status:has-text("lamp")'));   // the harness's fake projectors have answered
    await soft('sound output', page.waitForSelector('#audioline, #audiomsg'));
    await soft('box', page.waitForFunction(() => !/Loading/.test(document.getElementById('boxbody').textContent)));
    await soft('access codes', page.waitForFunction(() => { const q = document.querySelectorAll('#accesscard .join-code img.qr'); return q.length >= 2 && Array.prototype.every.call(q, (i) => i.complete && i.naturalWidth > 0); }));
    await page.waitForTimeout(800);

    await page.evaluate(() => { document.querySelector('.tabs').style.setProperty('display', 'none', 'important'); });   // the fixed tab bar would cover the bottom of tall cards
    await shot('system-vitals', (f) => card('Vitals').screenshot({ path: f }));
    await shot('system-modules', (f) => card('Modules').screenshot({ path: f }));
    await shot('box', (f) => card('Box').screenshot({ path: f }));
    await shot('sound-output', (f) => card('Sound output').screenshot({ path: f }));
    await shot('projectors', (f) => card('Projectors').screenshot({ path: f }));
    await shot('autostart', (f) => card('Autostart').screenshot({ path: f }));
    await shot('schedule', (f) => card('Schedule').screenshot({ path: f }));
    await shot('streams', (f) => card('Streams').screenshot({ path: f }));
    await shot('dmx', (f) => card('DMX').screenshot({ path: f }));
    await shot('midi', (f) => card('MIDI').screenshot({ path: f }));
    await shot('network', (f) => card('Network').screenshot({ path: f }));
    await shot('control-osc', (f) => card('Control \\(OSC\\)').screenshot({ path: f }));
    await shot('appearance', (f) => card('Appearance').screenshot({ path: f }));
    await shot('access', (f) => card('Access').screenshot({ path: f }));

    await page.evaluate(() => { document.querySelector('.tabs').style.removeProperty('display'); });

    // Whole screens, top to bottom, as a starting point for mock-ups (docs/mockups/current). Every optional module
    // is switched on first, so every card is in the picture.
    for (const m of ['wall', 'projector', 'mapper', 'inputs-srt', 'scheduler', 'control-dmx', 'control-midi', 'network']) {
      await api('POST', '/api/modules/' + m, { enabled: true });
    }
    async function screens(prefix) {
      for (const tab of ['Live', 'Mix', 'Media', 'System']) {
        await page.click('nav >> text=' + tab);
        await page.waitForTimeout(1500);                      // cards that load their own data
        // The tab bar is fixed to the bottom of the window; in a whole-page picture it would stop half-way down,
        // over a card. Let it sit at the end of the page while the picture is taken.
        await page.evaluate(() => { const t = document.querySelector('.tabs'); if (t) t.style.setProperty('position', 'static', 'important'); });
        await shot(prefix + '-' + tab.toLowerCase(), (f) => page.screenshot({ path: f, fullPage: true }));
        if (process.env.MOCKUPS) {                            // editable copies: SVG and layered PSD (tests/ui/mockups.js)
          try {
            const r = await require('./mockups').exportScreen(page, process.env.MOCKUPS, prefix.replace('screen-', ''), tab.toLowerCase());
            console.log('mock-up ' + prefix + ' ' + tab + ': ' + r.sections + ' sections, ' + r.controls + ' controls');
          } catch (e) {
            failures.push('mock-up ' + prefix + ' ' + tab + ': ' + e.message.split('\n')[0]);
          }
        }
        await page.evaluate(() => { const t = document.querySelector('.tabs'); if (t) t.style.removeProperty('position'); });
      }
    }
    await screens('screen-phone');
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.reload();
    await page.waitForSelector('.pads');
    await screens('screen-laptop');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.reload();
    await page.waitForSelector('.pads');

    // Another theme, and the wide layout.
    await api('POST', '/api/theme', { name: 'night-red', accent: null });
    await page.click('nav >> text=Live');
    await page.waitForSelector('.pads');
    await page.reload();
    await page.waitForSelector('.pad.on', { timeout: 10000 }).catch(() => {});
    await page.waitForTimeout(1200);
    await shot('live-night-red', whole);
    await api('POST', '/api/theme', { name: 'dark-stage', accent: null });

    await page.setViewportSize({ width: 1180, height: 760 });
    await page.reload();
    await page.waitForSelector('.pad.on', { timeout: 10000 }).catch(() => {});
    await page.waitForTimeout(1200);
    await shot('live-desktop', (f) => page.screenshot({ path: f }));
  } catch (e) {
    failures.push('run: ' + e.message.split('\n')[0]);
  } finally {
    await browser.close();
    server.kill();
  }
  console.log('saved: ' + saved.join(', '));
  if (skipped.length) console.log('skipped: ' + skipped.join(', '));
  if (failures.length) console.error('FAILED shots:\n' + failures.join('\n'));
  process.exit(saved.length >= 10 && failures.length === 0 ? 0 : 1);
})();
