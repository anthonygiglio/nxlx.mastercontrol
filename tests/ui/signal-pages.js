// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// Every screen, page and state of the panel that the look "Signal" (D54, D57) is looked at and checked on, in one
// list, so the pictures (tests/ui/screenshots.js) and the checks (tests/ui/panel.test.js) are of the same things.
//
//   setUp(t)    fills the harness's box with what a room has: projectors in every power state and one that does not
//               answer, two groups with labelled inputs, scenes, a schedule, streams, codes, a mapping, a controller.
//               It adds only what is missing, so it may run after other steps and more than once.
//   pages(t)    the list. Each entry: name (the picture is signal-<name>-phone|laptop), area (what data-area must be;
//               null on a screen before pairing), open(t) (brings the screen up and returns the page it is on when
//               that is not t.page), done(t) (puts things back), room (staff press this: 56 px buttons), light (also
//               pictured and checked in Signal light), quick (holds a question that goes away by itself after 8
//               seconds: nothing may wait before it is looked at).
//
// t: { page (paired as the owner), browser, base, info (the harness's line), width, scale, notes (what went wrong
// while setting up or opening: the caller reports it), plain (true while the box is in the default look) }. Loopback only, like everything in the harness.
'use strict';
const fs = require('fs');
const path = require('path');

const call = (pg, method, url, body) => pg.evaluate(async ([m, u, b]) => {
  const r = await fetch(u, { method: m, credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-PVJ-Request': '1' }, body: m === 'POST' ? JSON.stringify(b || {}) : undefined });
  return r.json().catch(() => ({}));
}, [method, url, body]);
const post = (t, url, body) => call(t.page, 'POST', url, body);
const get = (t, url) => call(t.page, 'GET', url);
const first = (e) => e.message.split('\n')[0];

async function soft(t, what, promise) { try { return await promise; } catch (e) { t.notes.push(what + ': ' + first(e)); return null; } }
async function has(t, pg, sel, ms) { return soft(t, 'waited in vain for ' + sel, pg.waitForSelector(sel, { timeout: ms || 8000 })); }
// ---- moving about in the Workspace shell (D65, D72) ----
// A screen has a key, 'area/screen': play/pads, play/library, play/shaders, shape/effect, shape/picture,
// shape/sound, shape/mapping, room/scenes, room/walls, room/guests, setup/index and setup/<the id of a page>. The
// open one is on the workspace as data-screen. go() presses what a person would at this width: the area's button
// of the rail from 600 px, or its tab at the foot under that; then the screen's tab in the row of the area's
// screens (in Setup: the row of the index, which from 1200 px also stands beside an open page). Where an area is
// a desk (from 1200 px) its screens are columns of one page and have no tab: go() then uses the column, as a
// person does (a press on its heading; with how = 'keys' the cursor goes into it), and the panel takes that
// column for the open one.
// It waits until the screen has been drawn: a press that leads to another build reads the box's state and only
// then draws (so "the screen is there" can be true of the one that is about to be replaced). The screens of one
// area that are columns of one build (ONE_BUILD) only change what is shown; a press on one of their tabs still
// reads the box's lists again, and go() waits for that to end (the workspace is aria-busy meanwhile), so a step
// that follows sees what the box has now. Only plain selectors, and click, isVisible, evaluate and
// waitForFunction, are used. With how = 'keys' nothing is clicked: the item is given the cursor and Enter is pressed.
// What the five tabs of the panel before D65 are now: Live is play/pads (its transport is the strip on every
// screen), Media is play/library, Mix is shape/picture (with shape/effect, shape/sound and shape/mapping; Speed
// and Loop are on the strip, the Transition on play/pads), System is setup/index, Room is room/scenes and room/walls.
const at = (pg) => pg.evaluate(() => { const m = document.querySelector('main.ws'); return m ? m.getAttribute('data-screen') : null; });
const ONE_BUILD = [['play/pads', 'play/library', 'play/shaders'], ['shape/effect', 'shape/picture', 'shape/sound'], ['room/scenes', 'room/walls', 'room/guests']];
async function go(pg, key, how) {
  await pg.waitForFunction(() => !!document.querySelector('main.ws'), null, { timeout: 15000 });
  const press = async (sel, want, into) => {              // want: the key to arrive at, or an area ('room/': any of its screens); into: a column, not a button
    const from = await at(pg);
    const kept = !!from && ONE_BUILD.some((g) => g.indexOf(from) >= 0 && (g.indexOf(want) >= 0 || (want.slice(-1) === '/' && from.indexOf(want) === 0)));
    if (!kept) await pg.evaluate(() => { const old = document.querySelector('main.ws > .desk'); if (old) old.setAttribute('data-before-go', ''); });      // (the build is what is replaced, not the workspace)
    if (how === 'keys') { await pg.focus(sel); if (!into) await pg.keyboard.press('Enter'); } else await pg.click(sel);
    await pg.waitForFunction(([k, fresh]) => {
      const m = document.querySelector('main.ws'), now = m && m.getAttribute('data-screen');
      return !!now && (k.slice(-1) === '/' ? now.indexOf(k) === 0 : now === k) && !m.hasAttribute('aria-busy') && !(fresh && m.querySelector(':scope > .desk[data-before-go]'));
    }, [want, !kept], { timeout: 15000 });
  };
  const area = key.split('/')[0], id = key.split('/')[1];
  const here = async () => ((await at(pg)) || '').split('/')[0];
  const areaButton = async () => ((await pg.isVisible('#side-' + area)) ? '#side-' + area : '#tab-' + area);
  if (area === 'setup') {
    const row = '#nav-' + id;
    if (key !== 'setup/index' && (await here()) === 'setup' && await pg.isVisible(row)) return press(row, key);
    await press(await areaButton(), 'setup/index');       // the button of Setup is always its index
    if (key !== 'setup/index') await press(row, key);
    return;
  }
  if ((await here()) !== area) await press(await areaButton(), area + '/');      // the area comes back on the screen it was left on
  const tab = '#sub-' + area + '-' + id, col = 'main.ws > .desk > .deskcol[data-col="' + id + '"]';
  if (await pg.isVisible(tab)) return press(tab, key);
  if (await pg.isVisible(col)) {                           // a column of the desk that is on the page
    if ((await at(pg)) !== key) await press(how === 'keys' ? col : col + ' > .deskhead, ' + col + ' .syshead h2', key, true);
    return;
  }
  if (await pg.isVisible('#sub-' + area + '-desk')) await press('#sub-' + area + '-desk', area + '/');      // from a tool that has the whole width, back to the desk
  if ((await at(pg)) !== key) await press(how === 'keys' ? col : col + ' > .deskhead, ' + col + ' .syshead h2', key, true);
}
// The strip shows four of its buttons on a phone: More opens the rest (the place in the clip, back and forward 10
// seconds, Fade in, Fade out, Freeze, Loop and Speed). It stays open across screens, so a step that needs them opens it once.
async function stripOpen(pg) {
  if (await pg.isVisible('#wsmore') && (await pg.evaluate(() => document.getElementById('wsmore').getAttribute('aria-expanded'))) !== 'true') await pg.click('#wsmore');
}
async function sysIndex(pg) {
  if ((await at(pg)) !== 'setup/index') await go(pg, 'setup/index');
  await pg.waitForSelector('#sysindex');
}
// A page by the name of its row of the Setup index, as the owner finds it there. (Shaders and Vibes, Projection
// mapping and Sound open as screens of Play and Shape, with a Back to the index; the others are pages of Setup.
// A page that is a column of an area has its title as a second heading.)
async function sys(pg, name) {
  await sysIndex(pg);
  await pg.click('#sysindex .navrow:has(.navname:text-is("' + name + '"))');
  // (the page's own title is in the page and not shown where the page is a column of a desk: the column's heading names it there)
  await pg.waitForSelector('#syspage .syshead :is(h1, h2):text-is("' + name + '")', { state: 'attached' });
  await pg.waitForSelector('#syspage');
}
// Back from a page to where it was opened from, by the page's own Back. Where the index stands beside a page of
// Setup (from 1200 px) the page has no Back to it, and there is nothing to do: the rows are there.
async function back(pg) { if (await pg.isVisible('#sysback')) await pg.click('#sysback'); }
async function home(t) {                                // the panel reads which modules are on when it loads
  await t.page.goto(t.base + '/');
  await t.page.waitForSelector((t.plain ? '' : 'html[data-style="signal"] ') + 'main.ws');      // t.plain: the caller is in the default look (the width sweep)
}
const projectors = async (t) => (await get(t, '/api/projectors')).projectors || [];
const powerOf = (p) => (p.status && p.status.ok ? p.status.power : p.status && p.status.ok === false ? 'no answer' : '');

const MODULES = ['wall', 'projector', 'mapper', 'inputs-srt', 'scheduler', 'control-dmx', 'control-midi', 'network', 'shaders', 'room'];
// name, which of the harness's fake projectors (-1: an address nothing listens on), password, the action that brings
// it to the state the picture wants, and that state
const PROJECTORS = [['Main projector', 0, 'secret1', null, 'on'], ['Side projector', 1, '', null, 'off'], ['Bar projector', 2, '', 'off', 'cooling down'],
  ['Stairs projector', 3, '', 'on', 'warming up'], ['Hall projector', 4, '', null, 'off'], ['Garden projector', -1, '', null, 'no answer']];
const WALLS = ['Main wall', 'Painting wall'];
const CLIPS = ['loop-a.mkv', 'leyline_opening_night_final_v2.mkv', '01_welcome.jpg', '02_sponsors.jpg', 'logo.png'];

async function setUp(t) {
  const page = t.page;
  for (const id of MODULES) await post(t, '/api/modules/' + id, { enabled: true });
  for (const f of ['midi', 'dmx', 'osc']) await post(t, '/api/' + f, { enabled: true });
  try { for (const f of ['plug', 'plug-launchpad']) fs.writeFileSync(path.join(t.info.midi_dir, f), ''); } catch (e) { t.notes.push('the fake controllers could not be plugged in'); }

  // clips: a short name, a long one with no space in it (it must wrap, never widen the page), pictures
  const haveClips = (await get(t, '/api/media')).files || [];
  for (const name of CLIPS) {
    if (haveClips.indexOf(name) >= 0) continue;
    await page.evaluate(async (n) => {
      await fetch('/api/media/upload?name=' + encodeURIComponent(n), { method: 'POST', credentials: 'same-origin',
        headers: { 'X-PVJ-Request': '1', 'Content-Type': 'application/octet-stream' }, body: new Uint8Array(300000 + n.length * 40000).fill(7) });
    }, name);
  }
  const pads = [['Intro', 'intro.mkv'], ['Tunnel', 'tunnel.mkv'], ['Loop A', 'loop-a.mkv'], ['Opening night', 'leyline_opening_night_final_v2.mkv']];
  for (let i = 0; i < pads.length; i++) await post(t, '/api/pads', { bank: 0, index: i, label: pads[i][0], file: pads[i][1] });

  // projectors: on, in standby, cooling down, warming up, and one that does not answer
  const ports = t.info.projector_ports || [];
  let ps = await projectors(t);
  for (const [name, fake, password] of PROJECTORS) {
    if (ps.some((p) => p.name === name) || fake >= ports.length) continue;
    await post(t, '/api/projectors', { add: { name, host: '127.0.0.1', port: fake < 0 ? 9 : ports[fake], password } });
  }
  await post(t, '/api/projector', { id: 'all', action: 'identify' });
  await soft(t, 'the projectors did not say which inputs they have', page.waitForFunction((names) => fetch('/api/projectors').then((r) => r.json())
    .then((d) => names.every((n) => d.projectors.some((p) => p.name === n && p.inputs.length > 0))), PROJECTORS.slice(0, 2).map((x) => x[0]), { timeout: 25000, polling: 1000 }));
  ps = await projectors(t);
  for (const [name, , , action, want] of PROJECTORS) {
    const p = ps.filter((x) => x.name === name)[0];
    if (p && action && powerOf(p) !== want) await post(t, '/api/projector', { id: p.id, action });
  }
  // two walls, each with its inputs named, and scenes
  let room = await get(t, '/api/room');
  for (let i = 0; i < WALLS.length; i++) {
    const p = ps.filter((x) => x.name === PROJECTORS[i][0])[0];
    if (!p) continue;
    const codes = (p.inputs || []).map((x) => x.code).slice(0, 2);
    for (let k = 0; k < codes.length; k++) await post(t, '/api/projectors', { label: { id: p.id, input: codes[k], label: ['Laptop', 'Console'][k] } });
    const old = (room.groups || []).filter((g) => g.name === WALLS[i])[0];      // a group an earlier step left is given this projector
    await post(t, '/api/room', { group: old ? { id: old.id, name: WALLS[i], projectors: [p.id] } : { name: WALLS[i], projectors: [p.id] } });
  }
  room = await get(t, '/api/room');
  const wall = (room.groups || []).filter((g) => g.name === WALLS[0])[0];
  const scenes = [['Movie night', { action: 'leave' }, 'unmute'], ['Workshop', { action: 'leave' }, 'leave'], ['Ambient evening', { action: 'vibes' }, 'leave'], ['Closing', { action: 'blackout' }, 'mute']];
  for (const [name, box, picture] of scenes) {
    if ((room.scenes || []).some((s) => s.name === name) || !wall) continue;
    await post(t, '/api/room', { scene: { name, groups: [{ group: wall.id, power: 'leave', input: '', picture: picture === 'leave' ? 'unmute' : picture, sound: 'leave' }], box } });
  }

  if (!((await get(t, '/api/schedule')).entries || []).length) {
    await post(t, '/api/schedule', { enabled: true, entries: [
      { label: 'Warm up', time: '18:15', days: [4, 5], action: 'projector_on' },
      { label: 'Doors', time: '18:30', days: [4, 5], action: 'play', file: 'intro.mkv', loop: true },
      { label: 'Close', time: '23:30', days: [0, 1, 2, 3, 4, 5, 6], action: 'blackout' },
      { label: 'Lamps off', time: '23:45', days: [0, 1, 2, 3, 4, 5, 6], action: 'projector_off' }] });
  }
  if (!((await get(t, '/api/streams')).streams || []).length) {
    await post(t, '/api/streams', { action: 'add', name: 'Stage camera', url: 'rtsp://admin:secret@192.168.0.40/live' });
    await post(t, '/api/streams', { action: 'add', name: 'Laptop (SRT)', url: 'srt://192.168.0.20:9000' });
  }
  await post(t, '/api/access/code', { role: 'view', minutes: 60 });
  await post(t, '/api/access/code', { role: 'live', minutes: 60 });
  // People and codes with "A code from a controller" switched on: both switches and the lines under them. The state
  // "a code is on the display" needs a hold on a real controller, so it is not in the pictures.
  await post(t, '/api/access/controller', { enabled: true });
  for (const m of [{ source: 'nanoKONTROL2', kind: 'cc', number: 0, action: 'opacity' }, { source: 'nanoKONTROL2', kind: 'cc', number: 16, action: 'volume' },
    { source: 'Mini', kind: 'note', number: 11, action: 'pad', bank: 0, index: 0 }]) await post(t, '/api/midi/map', { add: m });
  await post(t, '/api/autostart', { mode: 'file', file: 'intro.mkv', loop: true, delay: 5 });
  const map = await get(t, '/api/mapper');
  if (!(map.surfaces || []).length) {
    const made = await post(t, '/api/mapper', { action: 'add', type: 'grid' });
    const grid = made.surfaces && made.surfaces[0] && made.surfaces[0].id;
    if (grid) {
      await post(t, '/api/mapper', { action: 'move', id: grid, target: 'screen', corner: -1, dx: -300, dy: -130 });
      await post(t, '/api/mapper', { action: 'rename', id: grid, name: 'Back wall' });
      await post(t, '/api/mapper', { action: 'save', name: 'Main stage' });
    }
  }
  t.presenterToken = (await post(t, '/api/devices/invite', { name: 'Presenter', role: 'live' })).token;
  await home(t);
}

// A second device: its own context at the width and scale of the pictures. `route` may answer for the box.
async function other(t, hash, route) {
  const ctx = await t.browser.newContext({ viewport: { width: t.width, height: 844 }, deviceScaleFactor: t.scale || 1 });
  t.contexts.push(ctx);
  const pg = await ctx.newPage();
  if (route) await route(pg);
  await pg.goto(t.base + '/' + (hash || ''));
  return pg;
}
async function closeOthers(t) { while (t.contexts.length) await t.contexts.pop().close().catch(() => {}); }

async function playClip(t) {                             // a clip on the display, no shader: an effect can be put on
  await post(t, '/api/vibes', { on: false });
  await post(t, '/api/control', { action: 'stop' });
  await post(t, '/api/play', { pad: [0, 0] });
}
async function putEffectOn(t) {                          // on Shape > Effect, with the card's own button, as someone would
  await go(t.page, 'shape/effect');
  await has(t, t.page, '#fxlist [data-effect]', 20000);
  if (!(await t.page.locator('#fx-amount').count())) {
    await soft(t, 'the effect could not be put on', t.page.click('#fxlist [data-put="fx-vignette.fs"]:not([disabled])', { timeout: 20000 }));
    await has(t, t.page, '#fx-amount', 15000);
  }
}
async function projectorsPage(t) {
  await sys(t.page, 'Projectors');
  await has(t, t.page, '.proj-entry');
  await soft(t, 'the projectors are not in their states', t.page.waitForFunction((n) => document.querySelectorAll('.proj-entry').length >= n
    && !/Checking/.test(document.getElementById('projlist').textContent), Math.min(PROJECTORS.length, (t.info.projector_ports || []).length + 1), { timeout: 20000 }));
}
// Open the secondary actions of the first projector's row
async function projectorMore(t) {
  await projectorsPage(t);
  if (!(await t.page.locator('.proj-entry .moreacts').count())) await t.page.click('.proj-entry >> nth=0 >> .morebtn');
  await has(t, t.page, '.proj-entry .moreacts');
}
function sysPages() {
  // the row's name, the picture's name, what shows that the page has its data
  return [['Health', 'health', '#healthpower'], ['Room', 'room-settings', '#roomsetup'], ['Schedule', 'schedule', '.sched-entry'],
    ['People and codes', 'people', '#devicelist'], ['At power-up', 'power-up', '#autosave'],
    ['Streams', 'streams', '.stream-entry'], ['Boxes in step', 'boxes-in-step', '#syncline'],
    ['DMX lighting desk', 'dmx', '#dmxchannels'], ['OSC', 'osc', '#oscport'], ['Network', 'network', '#netiface'], ['Updates', 'updates', '#updateversion'],
    ['Remote support', 'support', '#supportline'], ['Backup and reset', 'backup', '#resetcard'], ['Look', 'look', '#lookthemes'], ['About and power', 'about', '#boxcard .kvv']];
}

function pages() {
  const list = [];
  const add = (name, area, open, more) => list.push(Object.assign({ name, area, open }, more || {}));

  // ---- before pairing ----
  add('pairing', null, async (t) => { const pg = await other(t); await pg.waitForSelector('.pin'); return pg; }, { light: true, done: closeOthers });
  add('support-sign-in', null, async (t) => {
    const pg = await other(t, '', (p) => p.route('**/api/hello', async (route) => {      // as the box answers through its support tunnel
      const r = await route.fetch(); const d = await r.json().catch(() => ({}));
      await route.fulfill({ response: r, json: Object.assign(d, { remote: true }) });
    }));
    await pg.waitForSelector('#supportcode');
    await pg.click('#supportlogin');                    // the message line, with an error in it
    return pg;
  }, { done: closeOthers });

  // ---- Room: Scenes, Walls, Guests ----
  // (go() waits for the screen that the press draws, not the one that was there: a presenter is on Room already
  // when the page opens, and a fold opened on the screen about to be replaced was closed again by the redraw.)
  const wall = '.room-group:has-text("' + WALLS[0] + '")';
  add('scenes', 'room', async (t) => {                  // ambience playing, and the scenes
    await post(t, '/api/vibes', { on: true });
    await go(t.page, 'room/scenes');
    await has(t, t.page, '#roomscenes .btn');
    await soft(t, 'ambience did not start', t.page.waitForFunction(() => /^Ambience is playing: /.test((document.getElementById('roomambwords') || {}).textContent), null, { timeout: 15000 }));
  }, { room: true, light: true });
  add('walls', 'room', async (t) => {                   // each wall, one of them just pressed
    await go(t.page, 'room/walls');
    await has(t, t.page, wall);
    await soft(t, 'no button on the first wall', t.page.locator(wall + ' .btn').first().click({ timeout: 5000 }));
    await has(t, t.page, wall + ' .room-result', 15000);
  }, { room: true, light: true });
  add('walls-all-off', 'room', async (t) => {
    await go(t.page, 'room/walls');
    await has(t, t.page, wall);
    await t.page.click('#roomalloff');
    await t.page.waitForSelector('#roomallask');
  }, { room: true, quick: true, light: true });
  add('scenes-presenter', 'room', async (t) => {        // where staff land: no set-up, the scenes and ambience
    const pg = await other(t, '#token=' + t.presenterToken);
    await pg.waitForSelector('main.ws[data-screen="room/scenes"]');
    await has(t, pg, '#roomscenes .btn');
    return pg;
  }, { room: true, done: closeOthers });
  add('guests-presenter', 'room', async (t) => {        // the fold to let someone in, open
    const pg = await other(t, '#token=' + t.presenterToken);
    await pg.waitForSelector('main.ws');
    await go(pg, 'room/guests');
    await soft(t, 'the fold to let someone in', pg.click('#roomletin > summary', { timeout: 5000 }));
    await has(t, pg, '#roomletin #accesslive');
    return pg;
  }, { room: true, done: closeOthers });

  // ---- Play: Pads, Library (Shaders is further down, with its own colour) ----
  add('pads', 'clips', async (t) => {                   // a clip playing: its pad lit, the strip saying what plays
    await playClip(t);
    await go(t.page, 'play/pads');
    await has(t, t.page, '.pads');
  }, { light: true });
  add('pads-strip-open', 'clips', async (t) => {        // a phone's strip with More open: the place in the clip and the other five
    await playClip(t);
    await go(t.page, 'play/pads');
    await has(t, t.page, '.pads');
    await stripOpen(t.page);
  }, { light: true, done: async (t) => { if (await t.page.isVisible('#wsmore') && (await t.page.getAttribute('#wsmore', 'aria-expanded')) === 'true') await t.page.click('#wsmore'); } });
  add('library', 'clips', async (t) => {                // the library, an upload's line and progress bar, what Info says
    await playClip(t);
    await go(t.page, 'play/library');
    await has(t, t.page, '#uploadbtn');
    await soft(t, 'the upload', t.page.setInputFiles('#filepick', { name: 'New loop (take 2).mkv', mimeType: 'video/x-matroska', buffer: Buffer.alloc(200000, 7) }));
    await soft(t, 'Info', t.page.locator('.item:has-text("intro.mkv") button:text-is("Info")').first().click({ timeout: 8000 }));      // a real clip: the box reads it
    await soft(t, 'what Info says', t.page.waitForFunction(() => { const m = document.getElementById('msg'); return m && m.textContent && !/^Reading/.test(m.textContent); }, null, { timeout: 8000 }));
  }, { light: true });
  add('library-usb', 'clips', async (t) => {            // a USB stick, as the box lists one, and the question before a file is deleted
    await t.page.route('**/api/media', async (route) => {
      if (route.request().method() !== 'GET') return route.continue();
      const r = await route.fetch(); const d = await r.json().catch(() => ({}));
      d.usb = [{ drive: 'STICK', files: [{ name: 'festival_reel_2026.mp4', size: 734003200 }, { name: 'loop-a.mkv', size: 52428800 }, { name: 'poster.png', size: 2097152 }] }];
      await route.fulfill({ response: r, json: d });
    });
    await go(t.page, 'play/pads');
    await go(t.page, 'play/library');
    await t.page.click('.libhead button:text-is("Refresh")');
    await has(t, t.page, '.usb-drive');
    await soft(t, 'Delete', t.page.locator('.item:has-text("logo.png") button:text-is("Delete")').first().click({ timeout: 8000 }));
  }, { quick: true, done: async (t) => { await t.page.unroute('**/api/media'); } });

  // ---- Shape: Effect, Picture, Sound, and Mapping ----
  add('effect-idle', 'mix', async (t) => {              // nothing playing: empty states, the Effects card saying why
    await post(t, '/api/vibes', { on: false });
    await post(t, '/api/control', { action: 'stop' });
    await go(t.page, 'play/pads');
    await go(t.page, 'shape/effect');
    await has(t, t.page, '#fxwhy', 20000);
  });
  add('effect', 'mix', async (t) => {                   // an effect on in the Effects card
    await playClip(t);
    await putEffectOn(t);
  }, { light: true });
  add('picture', 'mix', async (t) => {                  // the sliders, mirror, overlay, rotate
    await playClip(t);
    await go(t.page, 'shape/picture');
    await has(t, t.page, '#mo');
    await has(t, t.page, '#overlaybody > *');
  }, { light: true });
  add('mapping', 'mix', async (t) => {                  // the mapping page with its switch, the card with its pointer, and Test pattern
    await go(t.page, 'shape/mapping');
    await has(t, t.page, '#mapcanvas');
    await has(t, t.page, '#testpattern');
  });
  add('sound', 'mix', async (t) => {                    // Volume and Audio above the Sound page's card
    await go(t.page, 'shape/sound');
    await has(t, t.page, '#mvol');
    await has(t, t.page, '#audioline, #audiomsg');
  });

  // ---- Setup ----
  add('setup-index', 'system', async (t) => {
    await sysIndex(t.page);
    await has(t, t.page, '.navrow .chip-ready, .navrow .chip-active');
    await t.page.waitForTimeout(1500);                  // every row has its answer
  }, { light: true });
  add('projectors', 'system', projectorsPage, { light: true });
  add('projectors-more', 'system', projectorMore);
  add('projectors-edit', 'system', async (t) => { await projectorMore(t); await t.page.click('.proj-entry .proj-editbtn'); await has(t, t.page, '#projedit'); },
    { done: async (t) => { await soft(t, 'the edit form stayed', t.page.click('#projeditcancel', { timeout: 3000 })); } });
  add('projectors-inputs', 'system', async (t) => { await projectorMore(t); await t.page.click('.proj-entry .proj-namebtn'); await has(t, t.page, '#projnames'); },
    { done: async (t) => { await soft(t, 'the naming panel stayed', t.page.click('#projnamescancel', { timeout: 3000 })); } });
  add('projectors-add', 'system', async (t) => { await projectorsPage(t); await t.page.click('#projopen'); await has(t, t.page, '#projform'); await t.page.click('#projadd'); },
    { done: async (t) => { await soft(t, 'the add form stayed', t.page.click('#projcancel', { timeout: 3000 })); } });
  add('projectors-turn-off', 'system', async (t) => {   // the question asked in place
    await projectorsPage(t);
    await t.page.click('.proj-entry .proj-power:text-is("Turn off")');
    await t.page.waitForSelector('#confirmrow');
  }, { quick: true });
  for (const [row, name, ready] of sysPages()) {
    add(name, 'system', async (t) => { await sys(t.page, row); await has(t, t.page, ready); },
      { light: ['schedule', 'people', 'network', 'backup'].indexOf(name) >= 0 });
  }
  add('schedule-add', 'system', async (t) => { await sys(t.page, 'Schedule'); await has(t, t.page, '.sched-entry'); await t.page.click('#schedopen'); await has(t, t.page, '.daychips'); },
    { done: async (t) => { await soft(t, 'the schedule form stayed', t.page.click('#schedcancel', { timeout: 3000 })); } });
  add('people-presenter', 'system', async (t) => {      // a presenter's People and codes: guest codes only
    const pg = await other(t, '#token=' + t.presenterToken);
    await pg.waitForSelector('main.ws');
    await sys(pg, 'People and codes');
    await has(t, pg, '#accesslive');
    return pg;
  }, { done: closeOthers });
  // (desk: where Play's screens share the page, the area keeps Play's colour and the Shaders column wears its own)
  add('shaders', 'shaders', async (t) => {              // Play > Shaders: the one screen that wears the Shaders colour
    await post(t, '/api/vibes', { on: true });
    await go(t.page, 'play/shaders');
    await has(t, t.page, '#shadercontrols', 15000);
  }, { light: true, desk: 'clips' });
  add('shaders-from-index', 'shaders', async (t) => {   // the same page as the owner opens it from the Setup index: with a Back to it
    await sys(t.page, 'Shaders and Vibes');
    await has(t, t.page, '#sysback');
    await has(t, t.page, '#shadercontrols', 15000);
  }, { desk: 'clips' });
  add('midi', 'system', async (t) => {                  // the drawn controllers with their lights on: the switch, the brightness, Test lights
    await sys(t.page, 'MIDI controller');
    await has(t, t.page, '.ctlgrid', 15000);
    for (let i = 0; i < 4; i++) {                       // each card that has lights: switched on (the page redraws after each)
      const off = t.page.locator('.ctlcard .ctllights[aria-checked="false"]').first();
      if (!(await off.count())) break;
      await soft(t, 'Lights', off.click({ timeout: 5000 }));
      await t.page.waitForTimeout(700);
    }
    await has(t, t.page, '.ctlcard .ctllights[aria-checked="true"]');
    await has(t, t.page, '.ctlcard .ctlbright');
  }, { light: true });
  add('midi-control', 'system', async (t) => {          // one control chosen: what it does, and the choice of another action
    await sys(t.page, 'MIDI controller');
    await has(t, t.page, '.ctlgrid', 15000);
    await t.page.locator('.ctlgrid .ctl').nth(3).click();
    await has(t, t.page, '#ctldetail');
  });
  add('midi-teach', 'system', async (t) => {            // teaching: the box waits for a control to be moved
    await sys(t.page, 'MIDI controller');
    await has(t, t.page, '#midilearn', 15000);
    await t.page.click('#midilearn');
    await has(t, t.page, '#midilearning');
  }, { done: async (t) => { await soft(t, 'the box went on waiting for a control', t.page.click('#midicancel', { timeout: 3000 })); } });
  add('network-wifi', 'system', async (t) => {          // Wi-Fi: the networks the box can see (names come from strangers, and keep their letters)
    await sys(t.page, 'Network');
    await has(t, t.page, '#netiface');
    await t.page.selectOption('#netiface', 'wlan0');
    await has(t, t.page, '#netscanbtn');
    await t.page.click('#netscanbtn');
    await has(t, t.page, '#netscan .btn');
  }, { done: async (t) => { await soft(t, 'back to the cable', t.page.selectOption('#netiface', 'eth0', { timeout: 3000 })); } });
  add('network-pending', 'system', async (t) => {       // a change applied: confirm it or it goes back
    await sys(t.page, 'Network');
    await has(t, t.page, '#netiface');
    await t.page.selectOption('#netiface', 'eth0');
    await t.page.click('#netmodes >> text=Direct cable');
    await t.page.click('#netapply');
    await has(t, t.page, '#netpending', 15000);
    await soft(t, 'the countdown', t.page.waitForFunction(() => /Reverts in \d+ s/.test((document.getElementById('netleft') || {}).textContent), null, { timeout: 8000 }));
  }, { done: async (t) => { await soft(t, 'the network change stayed pending', t.page.click('#netrevert', { timeout: 5000 }));
    await soft(t, 'the network did not go back', t.page.waitForFunction(() => !document.getElementById('netreverting') && !document.getElementById('netpending'), null, { timeout: 20000 })); } });
  add('backup-reset', 'system', async (t) => {          // the danger area with its question open
    await sys(t.page, 'Backup and reset');
    await has(t, t.page, '#resetcard');
    await t.page.selectOption('#resetmedia', 'keep');
    await t.page.click('#resetbtn');
    await t.page.waitForSelector('#confirmrow');
  }, { quick: true });
  add('page-off', 'system', async (t) => {              // a page whose module is off: what it does, and one button
    await post(t, '/api/modules/inputs-srt', { enabled: false });
    await home(t);
    await sys(t.page, 'Streams');
    await has(t, t.page, '#sysswitchon');
  }, { light: true, done: async (t) => { await post(t, '/api/modules/inputs-srt', { enabled: true }); await home(t); } });
  return list;
}

module.exports = { setUp, pages, closeOthers, sys, sysIndex, go, at, back, stripOpen, WALLS };

// What one screen must hold in Signal (the rules are in pvj/THEMES.md). Returns what is wrong, as sentences; an
// empty list is a pass. o: { area (null before pairing), desk (the area where the screen is a column of a desk,
// when that is another), light (Signal light) }.
async function check(pg, o) {
  return pg.evaluate(async ([area, deskArea, light]) => {
    const out = [];
    const root = document.documentElement, shell = document.querySelector('.shell');
    if (!shell) return ['there is no screen'];
    if (root.getAttribute('data-style') !== 'signal') out.push('the style is gone');
    const beside = Array.prototype.filter.call(document.querySelectorAll('main.ws > .desk > .deskcol'), (c) => c.getClientRects().length > 0).length > 1;
    const wantArea = beside && deskArea ? deskArea : area;
    if ((root.getAttribute('data-area') || null) !== wantArea) out.push('area is ' + root.getAttribute('data-area') + ', not ' + wantArea);
    const shown = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'; };
    const name = (el) => (el.id ? '#' + el.id + ' ' : '') + (el.getAttribute('aria-label') || el.textContent || el.tagName).trim().slice(0, 28);
    // nothing sticks out: not of the window, and not of its card
    if (root.scrollWidth > window.innerWidth + 1) out.push('the page is wider than the window');
    document.querySelectorAll('.card').forEach((card) => {
      const box = card.getBoundingClientRect();
      card.querySelectorAll('button, input, select, span, img').forEach((el) => {
        if (el.closest('.ctlscroll')) return;               // a controller's drawing scrolls sideways inside its card, on purpose
        const r = el.getBoundingClientRect();
        if (r.width && (r.right > box.right + 1 || r.left < box.left - 1)) out.push('sticks out of its card: ' + name(el));
      });
    });
    // touch targets: 44 px, and 56 px for what staff press on Room and Live
    shell.querySelectorAll('button, select, summary, input, .xypad').forEach((el) => {
      if (!shown(el)) return;
      const r = el.getBoundingClientRect();
      // (the place in the clip as a bar along the strip's edge, D72: its target is 24 px high, on purpose, and as wide as the strip)
      if (el.id === 'seek' && getComputedStyle(el.parentElement).position === 'absolute') { if (r.height < 23.5) out.push('the bar of the place in the clip is under 24 px high: ' + Math.round(r.height)); return; }
      if (r.height < 43.5 || r.width < 43.5) out.push('small (' + Math.round(r.width) + 'x' + Math.round(r.height) + '): ' + name(el));
      else if (el.tagName === 'BUTTON' && el.closest('#roomscreen, .livecols, .banks, .tp .keep') && r.height < 55.5) out.push('under 56 px where staff press: ' + name(el));
    });
    // text: size, and contrast against what is behind it
    const lum = (c) => { const v = c.map((x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); }); return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; };
    const rgb = (s) => { const m = /rgba?\(([^)]+)\)/.exec(s); if (!m) return null; const p = m[1].split(/[ ,/]+/).map(Number); return { c: p.slice(0, 3), a: p.length > 3 ? p[3] : 1 }; };
    const behind = (el) => { for (let e = el; e; e = e.parentElement) { const b = rgb(getComputedStyle(e).backgroundColor); if (b && b.a > 0.99) return b.c; } return null; };
    const fits = (el, word, room) => {                    // 0 when this word fits the room it has, in the element's own type; else its width
      const probe = document.createElement('span');
      probe.textContent = word;
      probe.style.cssText = 'position:absolute;visibility:hidden;white-space:nowrap';
      el.appendChild(probe);
      const need = probe.getBoundingClientRect().width;
      el.removeChild(probe);
      return need <= room + 1 ? 0 : Math.round(need);
    };
    const walker = document.createTreeWalker(shell, NodeFilter.SHOW_TEXT);
    const seen = new Set();
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const el = n.parentElement, text = n.nodeValue.trim();
      if (!text || seen.has(el) || el.tagName === 'OPTION' || !shown(el)) continue;
      seen.add(el);
      const cs = getComputedStyle(el);
      if (parseFloat(cs.fontSize) < 13) out.push('text of ' + cs.fontSize + ': ' + name(el));
      // a word that does not fit (in capitals, or on a button) is cut by the browser in the middle, which a width check does not see
      const caps = cs.textTransform === 'uppercase';
      if ((caps || el.closest('button.btn')) && !el.closest('.ctlscroll')) {
        const word = text.split(/\s+/).sort((x, y) => y.length - x.length)[0] || '';
        const room = el.clientWidth ? el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) : el.getBoundingClientRect().width;
        const need = word.length > 1 ? fits(el, word, room) : 0;
        if (need) out.push('a word is cut (' + need + ' px in ' + Math.round(room) + '): ' + name(el));
        // capitals are for titles, actions and short labels: never a sentence
        if (caps && (/[.!?]\s+\S/.test(text) || text.split(/\s+/).length > 8)) out.push('a sentence in capitals: ' + name(el));
      }
      let faded = false;
      for (let e = el; e; e = e.parentElement) if (e.disabled || parseFloat(getComputedStyle(e).opacity) < 1) faded = true;
      const fg = rgb(cs.color), bg = behind(el);
      if (faded || !fg || !bg || fg.a < 0.99) continue;
      const a = lum(fg.c), b = lum(bg);
      const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
      if (ratio < 4.5) out.push('contrast ' + ratio.toFixed(2) + ' (' + cs.color + ' on rgb(' + bg.join(', ') + ')): ' + name(el));
    }
    // what a cell of a drawn controller says it does is whole
    shell.querySelectorAll('.ctlgrid .ctl .ctlwhat').forEach((el) => {
      if (!shown(el)) return;
      const word = el.textContent.trim().split(/\s+/).sort((x, y) => y.length - x.length)[0] || '';
      const need = word.length > 1 ? fits(el, word, el.clientWidth) : 0;
      if (need) out.push('a word is cut in a controller cell (' + need + ' px in ' + Math.round(el.clientWidth) + '): ' + el.textContent);
    });
    // names of clips and files keep their case; numbers are in the number face
    shell.querySelectorAll('#np, .pad .t, .sched-entry .lname, #uploads .k').forEach((el) => {
      if (shown(el) && getComputedStyle(el).textTransform !== 'none') out.push('a clip or file name in capitals: ' + name(el));
    });
    shell.querySelectorAll('.slidervalue, #time, .big-code, .kvv, .addr, input[type=number], input[type=time]').forEach((el) => {
      if (shown(el) && !/JetBrains Mono/.test(getComputedStyle(el).fontFamily)) out.push('a number not in the number face: ' + name(el));
    });
    // a state chip is never the area's colour (Active has a colour of its own), whatever area is open
    const probeC = document.createElement('span');
    probeC.style.cssText = 'position:absolute;visibility:hidden;background:' + getComputedStyle(root).getPropertyValue('--ac').trim();
    document.body.appendChild(probeC);
    const areaRgb = getComputedStyle(probeC).backgroundColor;
    document.body.removeChild(probeC);
    shell.querySelectorAll('.chip, .badge').forEach((el) => {
      if (shown(el) && getComputedStyle(el).backgroundColor === areaRgb) out.push('a state chip has the area colour: ' + name(el));
    });
    // in the light an area's colour is a filled block only: no line, ring or stripe in it (it is too faint on off-white)
    if (light) shell.querySelectorAll('*').forEach((el) => {
      if (!shown(el)) return;
      const cs = getComputedStyle(el);
      if (cs.backgroundColor === areaRgb) return;
      const line = ['Top', 'Right', 'Bottom', 'Left'].some((s) => parseFloat(cs['border' + s + 'Width']) > 0 && cs['border' + s + 'Style'] !== 'none' && cs['border' + s + 'Color'] === areaRgb);
      if (line || (cs.boxShadow !== 'none' && cs.boxShadow.indexOf(areaRgb) >= 0) || (cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0 && cs.outlineColor === areaRgb)) out.push('a thin line in the area colour, in the light: ' + name(el));
    });
    // a slider's fill is where its value is, at any moment: the panel writes the fill in the same turn as the value
    // (the box moves sliders too: the position in a clip jumps once a second, on every screen), so there is no
    // second look. A second look 300 ms later once hid a fill that trailed its value by up to 250 ms, until the
    // value moved again between the two looks (LESSONS).
    const fillOff = (el) => {
      const min = el.min === '' ? 0 : parseFloat(el.min), max = el.max === '' ? 100 : parseFloat(el.max);
      const want = max > min ? Math.round(1000 * (parseFloat(el.value) - min) / (max - min)) / 10 : 0;
      const got = parseFloat(getComputedStyle(el).getPropertyValue('--fill'));
      return Math.abs(got - want) <= 0.2 ? '' : 'slider fill ' + got + '% for a value at ' + want + '%: ' + name(el);
    };
    Array.prototype.forEach.call(shell.querySelectorAll('input[type=range]'), (el) => { const off = shown(el) && fillOff(el); if (off) out.push(off); });
    // the title: whole, inside the window, in the area's colour. Before pairing it is the screen's own heading; in
    // the Workspace shell (D65, D72) it is the title bar, and the open area's button (its tab under 600 px, its
    // button of the rail from 600 px) is that colour too. Exactly one heading of the first rank is shown, and the
    // transport strip is on every screen with the four buttons that never fold away.
    const bg = (el) => (el ? getComputedStyle(el).backgroundColor : '');
    const bar = document.getElementById('wshead');
    const h1 = bar ? document.getElementById('wstitle') : document.querySelector('.screen h1');
    if (h1) { const r = h1.getBoundingClientRect(); if (r.right > window.innerWidth + 1 || r.left < -1 || h1.scrollWidth > h1.clientWidth + 1) out.push('the title sticks out: ' + h1.textContent); }
    if (bar) {
      const open = Array.prototype.filter.call(document.querySelectorAll('#wsside [aria-current="page"], #wstabs [aria-current="page"]'), shown);
      const inMenu = !!document.querySelector('#wsside [aria-current="page"]');         // (a page whose module is off is not in the menu: only its area's tab can be open)
      if (!h1 || !shown(h1) || bg(bar) !== areaRgb) out.push('the title bar is not the area colour ' + areaRgb + ': ' + bg(bar));
      if (open.length > 1 || (!open.length && (inMenu || window.innerWidth < 600))) out.push(open.length + ' open items are shown in the menus');
      else if (open.length && bg(open[0]) !== areaRgb) out.push('the open item of the menu is not the area colour ' + areaRgb + ': ' + bg(open[0]));
      const heads = Array.prototype.filter.call(shell.querySelectorAll('h1'), shown);
      if (heads.length !== 1) out.push(heads.length + ' first headings are shown: ' + heads.map((x) => x.textContent).join(', '));
      const strip = document.getElementById('wstp');
      if (!strip || !shown(strip) || !['prev', 'next', 'stop', 'black'].every((id) => { const b = document.getElementById(id); return b && shown(b); })) out.push('the transport strip is not whole on this screen');
    } else {
      const top = h1 && h1.closest('.top');
      if (!h1 || [bg(h1), bg(top)].indexOf(areaRgb) < 0) out.push('the title block is not the area colour ' + areaRgb + ': ' + JSON.stringify([bg(h1), bg(top)]));
    }
    return out;
  }, [o.area, o.desk || null, !!o.light]);
}
module.exports.check = check;

// The place in the clip, on the transport strip, while the box moves it: 30 looks over three seconds (three polls
// or more), and at every one the fill is where the value is. Returns { shown, values (how many places were seen),
// bad (sentences) }. A clip must be playing for this to prove anything: `values` under 2 means the place stood still.
async function seekFill(pg) {
  return pg.evaluate(async () => {
    const el = document.getElementById('seek');
    if (!el) return { shown: false, values: 0, bad: ['there is no place in the clip on the strip'] };
    const r = el.getBoundingClientRect(), seen = {}, bad = {};
    for (let i = 0; i < 30; i++) {
      const want = Math.round(1000 * parseFloat(el.value) / parseFloat(el.max)) / 10, got = parseFloat(getComputedStyle(el).getPropertyValue('--fill'));
      seen[el.value] = 1;
      if (!(Math.abs(got - want) <= 0.2)) bad['slider fill ' + got + '% for a value at ' + want + '%: #seek Position in the clip'] = 1;
      await new Promise((done) => setTimeout(done, 100));
    }
    return { shown: r.width > 0 && r.height > 0, values: Object.keys(seen).length, bad: Object.keys(bad) };
  });
}
module.exports.seekFill = seekFill;
