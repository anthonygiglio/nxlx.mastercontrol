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
      try { await span(f, '.livecols > .card', '#previewcard'); } finally { await tabs(true); }
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
    // A feature is on only when its module and its own flag are both on (the page switch does both); OSC has only the flag.
    await api('POST', '/api/midi', { enabled: true });
    await api('POST', '/api/dmx', { enabled: true });
    await api('POST', '/api/osc', { enabled: true });
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
    // System is an index of rows and one page per row: each card is photographed on its own page.
    // A wait that runs out must not stop the other shots (the picture then shows what is there).
    const soft = (what, p) => p.catch(() => failures.push('wait: ' + what));
    async function sysIndex() {
      if (await page.locator('#sysback').count()) await page.click('#sysback');
      else if (!(await page.locator('#sysindex').count())) await page.click('nav >> text=System');
      await page.waitForSelector('#sysindex');
    }
    async function sys(name) {
      await sysIndex();
      await page.click(`.navrow:has(.navname:text-is("${name}"))`);
      await page.waitForSelector(`#syspage h1:text-is("${name}")`);
    }
    async function pageShot(name, row, title, ready) {
      await shot(name, async (f) => {
        await sys(row);
        if (ready) await soft(name, ready());
        await page.waitForTimeout(600);
        await tabs(false);             // the fixed tab bar would cover the bottom of tall cards
        try { await card(title).screenshot({ path: f }); } finally { await tabs(true); }
      });
    }
    await shot('system-index', async (f) => {
      await sysIndex();
      await soft('index states', page.waitForSelector('.navrow .chip-ready, .navrow .chip-active'));
      await page.waitForTimeout(1500);                      // every row has its answer
      await whole(f);
    });
    await shot('system-page', async (f) => { await sys('Streams'); await soft('streams page', page.waitForSelector('.stream-entry')); await page.waitForTimeout(600); await whole(f); });
    await shot('system-page-off', async (f) => { await sys('Shaders and Vibes'); await page.waitForSelector('#sysswitchon'); await whole(f); });
    await pageShot('health', 'Health', 'Health', () => page.waitForSelector('#healthpower'));
    await pageShot('box', 'About and power', 'This box', () => page.waitForFunction(() => !/Loading/.test(document.getElementById('boxbody').textContent)));
    await pageShot('sound-output', 'Sound', 'Sound output', () => page.waitForSelector('#audioline, #audiomsg'));
    await pageShot('projectors', 'Projectors', 'Projectors', () => page.waitForSelector('.proj-status:has-text("lamp")'));   // the harness's fake projectors have answered
    await pageShot('autostart', 'At power-up', 'At power-up', () => page.waitForSelector('#autoline'));
    await pageShot('schedule', 'Schedule', 'Schedule', () => page.waitForSelector('#schedclock'));
    await pageShot('streams', 'Streams', 'Streams', () => page.waitForSelector('.stream-entry'));
    await pageShot('dmx', 'DMX lighting desk', 'Lighting desk', () => page.waitForSelector('#dmxline'));
    await pageShot('midi', 'MIDI controller', 'Mappings', () => page.waitForSelector('#midiline'));
    await pageShot('network', 'Network', 'Network', () => page.waitForSelector('#netiface'));
    await pageShot('control-osc', 'OSC', 'OSC', () => page.waitForFunction(() => !/Loading/.test(document.getElementById('oscline').textContent)));
    await pageShot('appearance', 'Look', 'Appearance');
    // The System pages as a whole, as staff see them on a phone: the shared patterns (one row per thing with one
    // main button and More, the Add form, Save changes) show better on the whole page than on a card.
    async function wholePage(name, row, ready, before) {
      await shot(name, async (f) => {
        await sys(row);
        if (ready) await soft(name, ready());
        if (before) await soft(name + ' (set-up)', before());
        await page.waitForTimeout(600);
        await whole(f);
      });
    }
    await wholePage('page-projectors', 'Projectors', () => page.waitForSelector('.proj-status:has-text("lamp")'), () => page.click('.proj-entry .morebtn'));
    await wholePage('page-schedule', 'Schedule', () => page.waitForSelector('.sched-entry'), () => page.click('#schedopen'));
    await wholePage('page-dmx', 'DMX lighting desk', () => page.waitForSelector('#dmxchannels'));
    await wholePage('page-network', 'Network', () => page.waitForSelector('#netiface'));
    await wholePage('page-about', 'About and power', () => page.waitForSelector('#boxcard .kvv'));
    await wholePage('page-support', 'Remote support', () => page.waitForSelector('#supportline'));
    await wholePage('page-streams', 'Streams', () => page.waitForSelector('.stream-entry'));
    await wholePage('page-autostart', 'At power-up', () => page.waitForSelector('#autosave'));
    // The pages with a lot on them, on a laptop: the list on the left, the form beside it
    await page.setViewportSize({ width: 1366, height: 768 });
    try {
      await wholePage('page-projectors-laptop', 'Projectors', () => page.waitForSelector('.proj-status:has-text("lamp")'));
      await wholePage('page-schedule-laptop', 'Schedule', () => page.waitForSelector('.sched-entry'));
      await wholePage('page-network-laptop', 'Network', () => page.waitForSelector('#netiface'));
      await wholePage('page-people-laptop', 'People and codes', () => page.waitForSelector('#devicelist'));
    } finally {
      await page.setViewportSize({ width: 390, height: 844 });
    }
    await pageShot('access', 'People and codes', 'Let someone in', () => page.waitForFunction(() => { const q = document.querySelectorAll('#accesscard .join-code img.qr'); return q.length >= 2 && Array.prototype.every.call(q, (i) => i.complete && i.naturalWidth > 0); }));
    // Shaders and Vibes: the page with Vibes playing (opened from Live, as staff do), and Live with the big button.
    await api('POST', '/api/modules/shaders', { enabled: true });
    await api('POST', '/api/vibes', { on: true });
    await shot('shaders-page', async (f) => {
      await page.click('nav >> text=Live');
      await page.waitForSelector('#shaderslink');
      await page.click('#shaderslink');
      await soft('shaders page', page.waitForSelector('#shadercontrols', { timeout: 15000 }));
      await page.waitForTimeout(600);
      await whole(f);
    });
    await shot('live-vibes', async (f) => {
      await page.click('nav >> text=Live');
      await soft('vibes button', page.waitForFunction(() => /^Vibes is playing: /.test((document.getElementById('vibeswords') || {}).textContent), null, { timeout: 15000 }));
      await page.waitForTimeout(600);
      await whole(f);
    });
    await shot('shaders-page-laptop', async (f) => {
      await page.setViewportSize({ width: 1366, height: 768 });
      try {
        await page.click('#shaderslink');
        await soft('shaders page on a laptop', page.waitForSelector('#shadercontrols', { timeout: 15000 }));
        await page.waitForTimeout(800);
        await page.screenshot({ path: f, fullPage: true });
      } finally {
        await page.setViewportSize({ width: 390, height: 844 });
        await page.click('nav >> text=Live');
      }
    });
    // The instrument: one shader chosen by hand (a switch, a choice, two colours and numbers), with a preset saved;
    // the page on a phone, and Live on a laptop with the shader's strip beside the pads.
    await api('POST', '/api/vibes', { on: false });
    await api('POST', '/api/shaders/play', { id: 'isf-linear-gradient.fs' });
    await api('POST', '/api/shaders/presets', { action: 'save', name: 'Warm' });
    await shot('shaders-instrument', async (f) => {
      await page.click('#shaderslink');
      await soft('the instrument', page.waitForSelector('#shaderpresets [data-preset="Warm"]', { timeout: 15000 }));
      await page.waitForTimeout(600);
      await whole(f);
      await page.click('nav >> text=Live');
    });
    await shot('live-shader-laptop', async (f) => {
      await page.setViewportSize({ width: 1366, height: 768 });
      try {
        await soft('the strip on Live', page.waitForSelector('#liveshader:visible', { timeout: 15000 }));
        await page.waitForTimeout(600);
        await page.screenshot({ path: f, fullPage: true });
      } finally {
        await page.setViewportSize({ width: 390, height: 844 });
      }
    });
    await api('POST', '/api/shaders/presets', { action: 'delete', id: 'isf-linear-gradient.fs', name: 'Warm' });
    await api('POST', '/api/control', { action: 'stop' });
    await api('POST', '/api/play', { pad: [0, 0] });          // the clip is back for the pictures that follow
    await sysIndex();

    // Whole screens, top to bottom, as a starting point for mock-ups (docs/mockups/current). Every optional module
    // is switched on first, so every card is in the picture; the harness's fake nanoKONTROL2 is plugged in, so the
    // MIDI page has a drawn controller.
    for (const m of ['wall', 'projector', 'mapper', 'inputs-srt', 'scheduler', 'control-dmx', 'control-midi', 'network', 'shaders', 'room']) {
      await api('POST', '/api/modules/' + m, { enabled: true });
    }
    await api('POST', '/api/midi', { enabled: true });
    try { require('fs').writeFileSync(path.join(info.midi_dir, 'plug'), ''); } catch (e) { failures.push('mock-up: the fake controller could not be plugged in'); }
    // One editable export (SVG, layered PSD, PNG) of the page that is open: <MOCKUPS>/<device>/<name>.*
    async function mock(device, name) {
      if (!process.env.MOCKUPS) return;
      await page.evaluate(() => { window.scrollTo(0, 0); const t = document.querySelector('.tabs'); if (t) t.style.setProperty('position', 'static', 'important'); });
      try {
        const r = await require('./mockups').exportScreen(page, process.env.MOCKUPS, device, name);
        console.log('mock-up ' + device + ' ' + name + ': ' + r.sections + ' sections, ' + r.controls + ' controls');
      } catch (e) {
        failures.push('mock-up ' + device + ' ' + name + ': ' + e.message.split('\n')[0]);
      }
      await page.evaluate(() => { const t = document.querySelector('.tabs'); if (t) t.style.removeProperty('position'); });
    }
    // Every System page (the row's name, the file's name, what shows that the page has its data), the Room screen,
    // a page whose module is off, and Live with a shader playing. File names are fixed: layouts refer to them.
    const SYS_PAGES = [['Health', 'system-health', '#healthpower'], ['Projectors', 'system-projectors', '.proj-status'], ['Room', 'system-room', '#roomscenes'],
      ['Schedule', 'system-schedule', '.sched-entry'], ['Shaders and Vibes', 'system-shaders-and-vibes', '#shadercard'], ['People and codes', 'system-people-and-codes', '#devicelist'],
      ['Sound', 'system-sound', '#audioline'], ['At power-up', 'system-at-power-up', '#autosave'], ['Streams', 'system-streams', '.stream-entry'],
      ['Projection mapping', 'system-projection-mapping', '#sysbody .card'], ['Boxes in step', 'system-boxes-in-step', '#syncline'],
      ['MIDI controller', 'system-midi-controller', '.ctlgrid'], ['DMX lighting desk', 'system-dmx', '#dmxchannels'], ['OSC', 'system-osc', '#oscport'],
      ['Network', 'system-network', '#netiface'], ['Updates', 'system-updates', '#updateversion'], ['Remote support', 'system-remote-support', '#supportline'],
      ['Backup and reset', 'system-backup-and-reset', '#resetcard'], ['Look', 'system-look', '#sysbody .card'], ['About and power', 'system-about-and-power', '#boxcard .kvv']];
    async function pages(device) {
      if (!process.env.MOCKUPS) return;
      for (const [row, file, ready] of SYS_PAGES) {
        try {
          await sys(row);
          await page.waitForSelector(ready, { timeout: 8000 }).catch(() => failures.push('mock-up ' + device + ' ' + file + ': waited in vain for ' + ready));
          await page.waitForTimeout(700);
        } catch (e) { failures.push('mock-up ' + device + ' ' + file + ': ' + e.message.split('\n')[0]); continue; }
        await mock(device, file);
      }
      try {                                                   // a module that is off: its page says what it does, and one button
        await api('POST', '/api/modules/inputs-srt', { enabled: false });
        await page.reload();                                  // the panel reads which modules are on when it loads
        await page.waitForSelector('.pads');
        await page.click('nav.tabs button:text-is("System")');
        await page.waitForSelector('#sysindex');
        await page.click('#nav-streams');
        await page.waitForSelector('#sysswitchon', { timeout: 8000 });
        await mock(device, 'system-page-off');
      } catch (e) { failures.push('mock-up ' + device + ' system-page-off: ' + e.message.split('\n').slice(0, 3).join(' | ')); }
      await api('POST', '/api/modules/inputs-srt', { enabled: true });
      await page.reload();
      await page.waitForSelector('.pads');
      try {                                                   // the Room screen, as staff see it
        await page.click('nav >> text=Room');
        await page.waitForSelector('#roomscenes', { timeout: 8000 });
        await page.waitForTimeout(900);
        await mock(device, 'room');
      } catch (e) { failures.push('mock-up ' + device + ' room: ' + e.message.split('\n')[0]); }
      try {                                                   // Live while a shader plays: the Vibes row and the shader's strip
        await api('POST', '/api/shaders/play', { id: 'isf-linear-gradient.fs' });
        await page.click('nav >> text=Live');
        await page.waitForSelector('#liveshader', { timeout: 15000 }).catch(() => failures.push('mock-up ' + device + ' live-shader: no shader strip'));
        await page.waitForTimeout(900);
        await mock(device, 'live-shader');
      } catch (e) { failures.push('mock-up ' + device + ' live-shader: ' + e.message.split('\n')[0]); }
      await api('POST', '/api/control', { action: 'stop' });
      await api('POST', '/api/play', { pad: [0, 0] });
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
    await pages('phone');
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.reload();
    await page.waitForSelector('.pads');
    await screens('screen-laptop');
    await pages('laptop');
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
