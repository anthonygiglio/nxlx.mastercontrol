// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// The Workspace shell (D65) with its rail, tabs and area desks (D72), held by the browser test: four areas, tabs
// at the foot under 600 px, a rail of the areas from 600 px with the open area's screens as tabs, an area's
// screens side by side from 1200 px, one transport strip on every screen, fewer screens for a presenter and a
// guest, and no control lost in the move.
//
//   screens(pg)           every screen this device has, as keys, found as a person finds them: each area's button,
//                         its row of tabs, and the rows of the Setup index
//   reach(pg, how)        every screen is opened at 390, 768 and 1366 px, by a press ('tap') or with the keyboard
//                         ('keys'), and on each: the right screen is drawn, the title bar names its area, the
//                         area's button and the screen's tab are marked as open, the strip is whole, exactly one
//                         first heading is shown, the cursor is not lost (it is on the item pressed, or on the
//                         screen), and the columns that are shown are the ones this width has: one under 1200
//                         px, the area's whole desk from 1200 px (in Setup: the index beside the open page)
//   tabWalk(pg)           the Tab key alone, pressed from the top of the page at 390, 768 and 1366 px, comes to
//                         every item of the menus that is shown and to the strip's buttons, the rail and the tabs
//                         before the screen (reach() by 'keys' puts the cursor on an item and presses Enter: it
//                         shows that Enter opens the screen, this shows that the keyboard gets there)
//   menus(pg)             which menu is shown at 390, 599, 600, 768, 1199, 1200 and 1366 px: the tabs at the foot,
//                         the rail, the tabs of the screens, the desk with its columns as named regions and its
//                         one tab; the cursor is handed on when a tab goes with the width; a name typed in one
//                         column is still there after a tab, and after the window was made narrower and wider
//   strip(pg)             the strip is ONE line of buttons at every width from 600 px, with Freeze, Stop and
//                         Blackout at its right end: everything from 1440 px; under that it folds behind More
//                         (from 1200 px Speed and Loop; from 1000 px also back and forward 10 s and the fades;
//                         from 800 px also the place in the clip; from 600 px also Previous and Next), and More
//                         opens the rest under the line; on a phone four buttons, and More opens the rest in
//                         place, stays open on the next screen, and closes
//   narrow(pg, post, get) no text overruns at 320, 390, 600 and 768 px, on a phone held sideways (740 by 360) and
//                         at the desk's widths (1200, 1366, 1440 and 1920 px),
//                         on every screen, in the default look and in Signal, with long names (a clip, a
//                         shader, an effect, presets, scenes, walls, projectors, devices, a USB drive) put into
//                         the box's answers on their way to the page: the page does not scroll sideways, no
//                         text leaves its box or the window or is clipped, a line cut with dots has its whole
//                         text in a title, no two texts lie on one another, a title is not cut inside a word;
//                         and from 600 px the strip is one line with Freeze, Stop and Blackout on it.
//                         The failure names each element (overruns() is the measuring function, in the page)
//   roles(pages)          what the owner, a presenter and a guest have, where each lands, and that a switched-off
//                         module's screen is gone while the owner's Setup index keeps its row
//   inventory(o)          the controls of every screen, by role, against the list of the panel before the shell
//                         (tests/ui/fixtures/controls-before.json): nothing may be missing
//
// Every function throws an Error that says what was seen. pg: a Playwright page (click, focus, isVisible,
// keyboard.press, evaluate, waitForFunction, setViewportSize and goto are all that is used; narrow() also uses
// route and unroute, as tests/ui/signal-pages.js does).
'use strict';
const assert = require('assert');
const { go, at } = require('./signal-pages.js');
const inv = require('./inventory.js');

const AREAS = { play: 'Play', shape: 'Shape', room: 'Room', setup: 'Setup' };
const SIZES = [[390, 844], [768, 1024], [1366, 768]];
const DESK = 1200;                 // from this width of the panel an area's screens share the page
// the screens that are columns of one page there (the others have the whole width: Mapping, and Setup's pages)
const DESKS = { play: ['pads', 'library', 'shaders'], shape: ['effect', 'picture', 'sound'], room: ['scenes', 'walls', 'guests'] };
const HOMES = ['vibes', 'mapping', 'sound'];      // rows of the Setup index that open a screen of another area
const STRIP = ['prev', 'back10', 'fwd10', 'next', 'fadein', 'fade', 'freeze', 'stop', 'black'];
const SPEED = ['mv', 'loop'];      // Speed and Loop, on the strip since D72 (Loop has no id: it is found by its class)
const ALWAYS = ['prev', 'next', 'stop', 'black'];
// (what the closed strip shows whatever the width: under 600 px those four, from 600 px Freeze, Stop and Blackout, with Previous and Next from 800 px)
const kept = (width) => (width < 600 ? ALWAYS : ['freeze', 'stop', 'black'].concat(width >= 800 ? ['prev', 'next'] : []));

/* eslint-disable no-undef */
function look(strip) {
  const shown = (el) => { if (!el) return false; const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'; };
  const id = (q) => (q === 'loop' ? document.querySelector('#wstp .tploop') : document.getElementById(q));
  const main = document.querySelector('main.ws'), a = document.activeElement;
  const open = Array.prototype.filter.call(document.querySelectorAll('#wsside [aria-current="page"], #wstabs [aria-current="page"], #wssub [aria-current="page"], .deskcol.ix [aria-current="page"]'), shown);
  const buttons = {};
  strip.concat(['seek', 'wsmore']).forEach((b) => { buttons[b] = shown(id(b)); });
  const dock = id('wsdock') ? id('wsdock').getBoundingClientRect() : null;
  const cols = Array.prototype.filter.call(document.querySelectorAll('main.ws > .desk > .deskcol'), shown);
  return {
    screen: main ? main.getAttribute('data-screen') : null,
    title: id('wstitle') ? id('wstitle').textContent : null,
    bar: shown(id('wshead')), tabs: shown(id('wstabs')), side: shown(id('wsside')), sub: shown(id('wssub')),
    open: open.map((el) => el.id),
    heads: Array.prototype.filter.call(document.querySelectorAll('.shell h1'), shown).map((el) => el.textContent),
    buttons, more: id('wsmore') ? id('wsmore').getAttribute('aria-expanded') : null,
    dockAtFoot: !!dock && Math.abs(dock.bottom - window.innerHeight) <= 1,
    focus: !a || a === document.body ? '' : a.id || a.tagName.toLowerCase(),
    cols: cols.map((c) => c.getAttribute('data-col')),
    regions: cols.map((c) => [c.getAttribute('role') || c.tagName.toLowerCase(), c.getAttribute('aria-label')]),
    colHeads: cols.map((c) => Array.prototype.filter.call(c.querySelectorAll(':scope > .deskhead, .syshead h2'), shown).map((x) => x.textContent)[0] || ''),
    rail: Array.prototype.map.call(document.querySelectorAll('#wsside button'), (b) => [b.id, b.textContent]),
    subs: Array.prototype.filter.call(document.querySelectorAll('#wssub button'), shown).map((b) => b.id),
    job: Array.prototype.filter.call(document.querySelectorAll('.wsjob'), shown).map((x) => x.textContent).join(' | '),
    back: shown(id('sysback')),
    tabNames: Array.prototype.map.call(document.querySelectorAll('#wstabs button'), (b) => b.textContent),
    rows: Array.prototype.map.call(document.querySelectorAll('#sysindex .navrow .navname'), (b) => b.textContent),
    disabled: strip.filter((b) => id(b) && id(b).disabled),
    landmarks: { nav: Array.prototype.map.call(document.querySelectorAll('.shell nav'), (n) => n.getAttribute('aria-label')), main: document.querySelectorAll('.shell main').length, header: document.querySelectorAll('.shell header').length },
  };
}
/* eslint-enable no-undef */
const see = (pg) => pg.evaluate(look, STRIP.concat(SPEED));
const ready = (pg) => pg.waitForFunction(() => !!document.querySelector('#wsside [data-ar]'), null, { timeout: 15000 });
const frames = (pg) => pg.evaluate(() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done))));

// Every screen this device has. An area's screens are the tabs of its row (all of them are in the page, whatever
// the width shows); an area with no row has one screen. Setup's are its index and the rows of it, apart from the
// three that open a screen of another area (while their module is on; off, they open a page of Setup with the switch).
async function screens(pg) {
  await ready(pg);
  const out = [];
  const areas = await pg.evaluate(() => Array.prototype.map.call(document.querySelectorAll('#wstabs [data-ar]'), (b) => b.getAttribute('data-ar')));
  for (const area of areas) {
    if (area === 'setup') continue;
    if (((await at(pg)) || '').split('/')[0] !== area) {
      await pg.evaluate((a) => { const b = Array.prototype.filter.call(document.querySelectorAll('#side-' + a + ', #tab-' + a), (x) => x.getClientRects().length)[0]; if (b) b.click(); }, area);
      await pg.waitForFunction((a) => (document.querySelector('main.ws').getAttribute('data-screen') || '').indexOf(a + '/') === 0, area, { timeout: 15000 });
    }
    const tabs = await pg.evaluate(() => Array.prototype.map.call(document.querySelectorAll('#wssub [data-go]'), (b) => b.getAttribute('data-go')));
    (tabs.length ? tabs : [await at(pg)]).forEach((k) => out.push(k));
  }
  if (areas.indexOf('setup') >= 0) {
    await go(pg, 'setup/index');
    await pg.waitForFunction(() => !!document.querySelector('#sysindex .navrow'), null, { timeout: 15000 });
    out.push('setup/index');
    (await pg.evaluate(() => Array.prototype.map.call(document.querySelectorAll('#sysindex .navrow'), (b) => b.id.replace(/^nav-/, ''))))
      .forEach((id) => { if (HOMES.indexOf(id) < 0) out.push('setup/' + id); });
  }
  return out;
}

async function reach(pg, how) {
  await ready(pg);
  let count = 0;
  await pg.setViewportSize({ width: 390, height: 844 });
  await frames(pg);
  const keys = await screens(pg);
  assert(keys.length >= 4, 'this device has screens: ' + keys.join(' '));
  for (const [width, height] of SIZES) {
    await pg.setViewportSize({ width, height });
    await frames(pg);
    for (const key of keys) {
      const where = key + ' at ' + width + ' px by ' + how;
      await go(pg, key, how);
      const s = await see(pg);
      const area = key.split('/')[0], id = key.split('/')[1];
      const desk = (DESKS[area] || []).filter((x) => keys.indexOf(area + '/' + x) >= 0);
      const inDesk = desk.length > 1 && desk.indexOf(id) >= 0, wide = width >= DESK;
      assert.strictEqual(s.screen, key, where + ': the screen that is open');
      assert.strictEqual(s.title, AREAS[area], where + ': the title bar names the area');
      assert(s.bar, where + ': the title bar is shown');
      assert.strictEqual(s.heads.length, 1, where + ': one first heading is shown, not ' + JSON.stringify(s.heads));
      // the open items: the area's button (the rail from 600 px, the tab at the foot under that), and the screen's
      // tab. A desk's screens have one tab between them, and none where the desk is all the area has.
      const marks = [(width >= 600 ? 'side-' : 'tab-') + area];
      const own = keys.filter((k) => k.split('/')[0] === area).length;
      if (area === 'setup') { if (wide && id !== 'index') marks.push('nav-' + id); }
      else if (own > 1 && !(wide && inDesk)) marks.push('sub-' + area + '-' + id);
      else if (own > desk.length && wide && inDesk) marks.push('sub-' + area + '-desk');
      assert.deepStrictEqual(s.open.slice().sort(), marks.slice().sort(), where + ': what is marked as open');
      // the columns on the page: the desk from 1200 px, one screen under that; in Setup the index beside a page
      const cols = wide && inDesk ? desk : area === 'setup' && wide && id !== 'index' ? ['index', id] : [id];
      assert.deepStrictEqual(s.cols, cols, where + ': the columns that are shown');
      if (area === 'setup' && id !== 'index') assert.strictEqual(s.back, !wide, where + ': Back to the index is ' + (wide ? 'not shown beside the index' : 'shown'));
      kept(width).forEach((b) => assert(s.buttons[b], where + ': the strip has ' + b));
      assert(s.dockAtFoot, where + ': the strip is at the foot of the window');
      assert(s.focus, where + ': the cursor is not lost');
      count++;
    }
  }
  return count;
}

async function tabWalk(pg) {
  await ready(pg);
  const out = {};
  for (const [width, height] of SIZES) {
    await pg.setViewportSize({ width, height });
    await frames(pg);
    await go(pg, 'shape/sound');
    // what the keyboard must come to: every item of the menus that is shown, and the strip's buttons that can be pressed
    const want = await pg.evaluate(() => {
      const shown = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'; };
      if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
      window.scrollTo(0, 0);
      return Array.prototype.filter.call(document.querySelectorAll('#wsside button, #wssub button, #wstabs button, #wstp button, #wstp input'), (b) => shown(b) && !b.disabled).map((b) => b.id || 'loop');
    });
    let seen = [];
    const stops = [];
    for (let i = 0; i < 400; i++) {
      await pg.keyboard.press('Tab');
      // (each stop is told from the others by a number given to its element: many buttons of a screen have no id)
      const [id, n] = await pg.evaluate(() => { const a = document.activeElement; if (!a || a === document.body) return ['', 0]; window.pvjWalk = window.pvjWalk || 0; if (!a.pvjWalk) a.pvjWalk = ++window.pvjWalk;
        return [(a.closest('main') ? 'main:' : '') + (a.id || (a.classList.contains('tploop') ? 'loop' : a.tagName.toLowerCase())), a.pvjWalk]; });
      if (stops.length && n === stops[0]) break;          // round again
      stops.push(n);
      seen.push(id);
    }
    // (the walk begins after the item that was pressed to get here: the round is turned so that it begins where the page does)
    const top = seen.indexOf(width < 600 ? 'sub-shape-effect' : 'side-play');
    if (top > 0) seen = seen.slice(top).concat(seen.slice(0, top));
    const missed = want.filter((id) => seen.indexOf(id) < 0);
    assert.deepStrictEqual(missed, [], 'at ' + width + ' px the Tab key never comes to: ' + missed.join(', ') + ' (it came to ' + seen.join(' ') + ')');
    const menu = width < 600 ? ['wsmore', 'tab-play', 'tab-setup', 'sub-shape-sound'] : ['side-play', 'side-setup'].concat(width < DESK ? ['sub-shape-sound'] : ['sub-shape-desk', 'sub-shape-mapping']);
    kept(width).concat(menu).forEach((id) => {
      if (id !== 'prev' && id !== 'next') assert(seen.indexOf(id) >= 0, 'at ' + width + ' px the Tab key comes to ' + id);
    });
    const inMain = seen.findIndex((id) => id.indexOf('main:') === 0);
    // the order follows the eye: the rail, the tabs of the screens, the screen, the strip
    if (width >= 600) assert(seen.indexOf('side-play') >= 0 && seen.indexOf('side-setup') < seen.indexOf(menu[2]) && seen.indexOf(menu[menu.length - 1]) < inMain, 'at ' + width + ' px the rail comes before the tabs and the tabs before the screen in the Tab order: ' + seen.join(' '));
    assert(inMain >= 0 && inMain < seen.indexOf('stop'), 'at ' + width + ' px the screen comes before the strip in the Tab order');
    if (width >= DESK) {                                  // a desk: its columns from left to right
      const first = (col) => seen.findIndex((id) => id === 'main:' + col);
      assert(first('fxfilter') >= 0 && first('fxfilter') < first('mo') && first('mo') < first('mvol'), 'at ' + width + ' px the Tab key goes through Effect, Picture and Sound in that order: ' + seen.filter((id) => /^main:(fxfilter|mo|mvol)$/.test(id)).join(' '));
    }
    out[width] = seen.length;
  }
  return out;
}

async function menus(pg) {
  await ready(pg);
  for (const [width, tabs] of [[390, true], [599, true], [600, false], [768, false], [1199, false], [1200, false], [1366, false]]) {
    await pg.setViewportSize({ width, height: 900 });
    await frames(pg);
    await go(pg, 'shape/picture');
    const s = await see(pg), wide = width >= DESK, at = 'at ' + width + ' px ';
    assert.strictEqual(s.tabs, tabs, 'the area tabs at ' + width + ' px');
    assert.strictEqual(s.side, !tabs, 'the rail at ' + width + ' px');
    assert.deepStrictEqual(s.rail, [['side-play', 'Play'], ['side-shape', 'Shape'], ['side-room', 'Room'], ['side-setup', 'Setup']], 'the rail has one button per area and nothing else');
    assert.strictEqual(s.sub, true, 'the tabs of Shape\'s screens at ' + width + ' px');
    assert.deepStrictEqual(s.subs, wide ? ['sub-shape-desk', 'sub-shape-mapping'] : ['sub-shape-effect', 'sub-shape-picture', 'sub-shape-sound', 'sub-shape-mapping'], at + 'the tabs of Shape');
    assert.deepStrictEqual(s.cols, wide ? ['effect', 'picture', 'sound'] : ['picture'], at + 'the columns of Shape');
    assert.deepStrictEqual(s.regions, (wide ? ['Effect', 'Picture', 'Sound'] : ['Picture']).map((n) => ['region', n]), at + 'every column is a region with its name');
    assert.deepStrictEqual(s.colHeads, wide ? ['Effect', 'Picture', 'Sound'] : [''], at + (wide ? 'every column of the desk has its heading' : 'one screen has no column heading'));
    // where the screens share the page the title bar speaks of the area, and no column is named as the open one
    assert(wide ? !/^(Effect|Picture|Sound): /.test(s.job) && s.job.length > 0 : /^Picture: /.test(s.job), at + 'what the title bar says: ' + s.job);
    assert.deepStrictEqual(s.landmarks.nav.slice().sort(), ['Areas', 'Areas', 'Screens of Shape'], 'the menus are named landmarks');
    assert(s.landmarks.main === 1 && s.landmarks.header === 1, 'one main part and one title bar: ' + JSON.stringify(s.landmarks));
    // Play: all of its screens share the page from 1200 px, so it has no tabs there; and the link from the pads to
    // the Shaders screen is there only where Shaders is another tab
    await go(pg, 'play/pads');
    const p = await see(pg);
    assert.deepStrictEqual(p.cols, wide ? ['pads', 'library', 'shaders'] : ['pads'], at + 'the columns of Play');
    assert.strictEqual(p.sub, !wide, at + 'the tabs of Play\'s screens');
    assert.strictEqual(await pg.isVisible('#shaderslink'), !wide, at + 'the link from the pads to the Shaders screen');
  }
  // each card is on the page once, whatever is shown: one build per area
  for (const [key, ids] of [['play/pads', ['pads', 'libraryscreen', 'shaderpage', 'vibes', 'transitioncard', 'msg']], ['shape/picture', ['fxcard', 'mo', 'mvol', 'overlaycard', 'msg']], ['room/scenes', ['roomscenes', 'roomgroups', 'msg']]]) {
    for (const width of [390, 1366]) {
      await pg.setViewportSize({ width, height: 900 });
      await go(pg, key);
      const n = await pg.evaluate((list) => list.map((i) => document.querySelectorAll('[id="' + i + '"]').length), ids);
      assert.deepStrictEqual(n, ids.map(() => 1), 'on ' + key + ' at ' + width + ' px each of ' + ids.join(', ') + ' is in the page once: ' + n.join(' '));
      const twice = await pg.evaluate(() => { const seen = {}, out = []; document.querySelectorAll('.shell [id]').forEach((el) => { if (seen[el.id]) out.push(el.id); seen[el.id] = 1; }); return out; });
      assert.deepStrictEqual(twice, [], 'on ' + key + ' at ' + width + ' px no id is in the page twice');
    }
  }
  // What is typed survives a tab of the same area, and the window made narrower and wider again; the cursor stays
  // in the field. (The Effect screen's search field; then the Picture tab and back; then across 1200 and 600 px.)
  await pg.setViewportSize({ width: 768, height: 900 });
  await go(pg, 'shape/effect');
  await pg.waitForFunction(() => !!document.getElementById('fxfilter'), null, { timeout: 15000 });
  const typed = () => pg.evaluate(() => { const f = document.getElementById('fxfilter'), r = f.getBoundingClientRect(); return [f.value, r.width > 0 && r.height > 0, document.activeElement === f]; });
  await pg.evaluate(() => { const f = document.getElementById('fxfilter'); f.focus(); f.value = 'zz typed'; f.dispatchEvent(new Event('input', { bubbles: true })); });
  await go(pg, 'shape/picture');
  await go(pg, 'shape/effect');
  assert.strictEqual((await typed())[0], 'zz typed', 'a name typed on Effect is still there after the Picture tab and back');
  await pg.evaluate(() => document.getElementById('fxfilter').focus());
  for (const width of [1366, 1199, 599, 390, 1200, 768]) {
    await pg.setViewportSize({ width, height: 900 });
    await frames(pg);
    assert.deepStrictEqual(await typed(), ['zz typed', true, true], 'at ' + width + ' px, after the window changed, the field is shown with what was typed and the cursor in it');
    assert.strictEqual((await see(pg)).screen, 'shape/effect', 'at ' + width + ' px the column that was in use is the open screen');
  }
  // in a desk the column that is used last is the one a narrower window shows
  await pg.setViewportSize({ width: 1366, height: 900 });
  await frames(pg);
  await pg.evaluate(() => { document.getElementById('mvol').focus(); });
  assert.strictEqual((await see(pg)).screen, 'shape/sound', 'the column the cursor went into is the open screen');
  await pg.setViewportSize({ width: 768, height: 900 });
  await frames(pg);
  assert.deepStrictEqual((await see(pg)).cols, ['sound'], 'and it is the one a narrower window shows');
  await pg.evaluate(() => { const f = document.getElementById('fxfilter'); f.value = ''; f.dispatchEvent(new Event('input', { bubbles: true })); });
  // the cursor on a tab that goes with the width is handed on, never left nowhere
  await pg.focus('#sub-shape-sound');
  await pg.setViewportSize({ width: 1366, height: 900 });
  await frames(pg);
  assert.strictEqual((await see(pg)).focus, 'sub-shape-desk', 'the cursor on a screen\'s tab goes to the desk\'s tab when the area becomes a desk');
  await pg.setViewportSize({ width: 768, height: 900 });
  await frames(pg);
  assert.strictEqual((await see(pg)).focus, 'sub-shape-sound', 'and back to the open screen\'s tab');
  await pg.focus('#side-shape');
  await pg.setViewportSize({ width: 390, height: 844 });
  await frames(pg);
  assert.strictEqual((await see(pg)).focus, 'tab-shape', 'the cursor on the rail goes to the area\'s tab at the foot on a phone');
  await pg.setViewportSize({ width: 768, height: 900 });
  await frames(pg);
  assert.strictEqual((await see(pg)).focus, 'side-shape', 'and back to the rail');
  await pg.setViewportSize({ width: 390, height: 844 });
  await frames(pg);
}

/* eslint-disable no-undef */
// The strip's buttons as they lie: which are shown, on how many lines, and where the last one ends.
function lie(strip) {
  const shown = (el) => { if (!el) return false; const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'; };
  const vw = document.documentElement.clientWidth;
  const at = {}, lines = [];
  strip.concat(['wsmore']).forEach((b) => {
    const el = b === 'loop' ? document.querySelector('#wstp .tploop') : document.getElementById(b);
    if (!shown(el)) return;
    const r = el.getBoundingClientRect(), mid = Math.round(r.top + r.height / 2);
    at[b] = { left: Math.round(r.left), right: Math.round(r.right), mid };
    if (!lines.some((y) => Math.abs(y - mid) <= 4)) lines.push(mid);
  });
  const np = document.getElementById('np').getBoundingClientRect();
  return { vw, at, lines: lines.length, name: Math.round(np.width), page: document.documentElement.scrollWidth };
}
/* eslint-enable no-undef */

async function strip(pg) {
  await ready(pg);
  await pg.setViewportSize({ width: 1366, height: 768 });
  await frames(pg);
  if ((await see(pg)).more === 'true') await pg.evaluate(() => document.getElementById('wsmore').click());
  // From 600 px: one line, whatever is folded. [width, height, Previous and Next, the place in the clip, back and
  // forward 10 s and the fades, Speed and Loop]
  const FOLDED = ['back10', 'fwd10', 'fadein', 'fade'], ALL = STRIP.concat(SPEED);
  for (const [width, height, steps, seek, rest, speed] of [[600, 900, false, false, false, false], [667, 375, false, false, false, false], [768, 1024, false, false, false, false], [799, 900, false, false, false, false],
    [800, 900, true, false, false, false], [999, 900, true, false, false, false], [1000, 800, true, true, false, false], [1199, 800, true, true, false, false], [1200, 800, true, true, true, false], [1366, 768, true, true, true, false],
    [1439, 900, true, true, true, false], [1440, 900, true, true, true, true], [1920, 1080, true, true, true, true]]) {
    await pg.setViewportSize({ width, height });
    await frames(pg);
    const s = await see(pg), l = await pg.evaluate(lie, ALL);
    const where = 'at ' + width + ' px the strip ';
    assert.strictEqual(l.lines, 1, where + 'is one line of buttons, not ' + l.lines + ': ' + JSON.stringify(l.at));
    ['freeze', 'stop', 'black'].forEach((b) => assert(s.buttons[b], where + 'shows ' + b));
    assert(l.at.freeze.left < l.at.stop.left && l.at.stop.left < l.at.black.left && l.at.black.right <= l.vw && l.at.black.right >= l.vw - 24,
      where + 'ends with Freeze, Stop and Blackout at the right edge: ' + JSON.stringify(l.at));
    Object.keys(l.at).forEach((b) => assert(l.at[b].left >= 0 && l.at[b].right <= l.vw + 1, where + 'keeps ' + b + ' inside the window: ' + JSON.stringify(l.at[b])));
    assert(l.page <= l.vw + 1, where + 'does not make the page scroll sideways (' + l.page + ' px)');
    assert(l.name >= 60, where + 'leaves room for what plays: ' + l.name + ' px');
    assert.strictEqual(s.buttons.seek, seek, where + (seek ? 'shows' : 'folds') + ' the place in the clip');
    ['prev', 'next'].forEach((b) => assert.strictEqual(s.buttons[b], steps, where + (steps ? 'shows ' : 'folds ') + b));
    FOLDED.forEach((b) => assert.strictEqual(s.buttons[b], rest, where + (rest ? 'shows ' : 'folds ') + b));
    SPEED.forEach((b) => assert.strictEqual(s.buttons[b], speed, where + (speed ? 'shows ' : 'folds ') + (b === 'mv' ? 'Speed' : 'Loop')));
    assert.strictEqual(s.buttons.wsmore, !speed, where + (speed ? 'has no More' : 'has More'));
  }
  // Speed and Loop are on the strip and nowhere else, on a screen of every area
  for (const key of ['play/pads', 'shape/picture', 'room/scenes', 'setup/index']) {
    await go(pg, key);
    const n = await pg.evaluate(() => [document.querySelectorAll('#wstp #mv').length, document.querySelectorAll('#mv').length, document.querySelectorAll('#wstp .tploop').length,
      Array.prototype.filter.call(document.querySelectorAll('button'), (b) => /^Loop: /.test(b.textContent)).length]);
    assert.deepStrictEqual(n, [1, 1, 1, 1], 'on ' + key + ' Speed and Loop are on the strip, once: ' + n.join(' '));
  }
  // More at 768 px: what was folded comes under the line, and the three keep their place
  await pg.setViewportSize({ width: 768, height: 1024 });
  await frames(pg);
  const before = await pg.evaluate(lie, ALL);
  await pg.click('#wsmore');
  let s = await see(pg);
  const open = await pg.evaluate(lie, ALL);
  ALL.concat(['seek', 'wsmore']).forEach((b) => assert(s.buttons[b], 'at 768 px with More open the strip shows ' + b));
  assert.strictEqual(s.more, 'true', 'at 768 px More says it is open');
  ['freeze', 'stop', 'black'].forEach((b) => assert(open.at[b].left === before.at[b].left && open.at[b].mid < open.at.fade.mid, 'at 768 px ' + b + ' keeps its place when More opens: ' + JSON.stringify([before.at[b], open.at[b]])));
  await pg.click('#wsmore');
  s = await see(pg);
  assert(s.more === 'false' && !s.buttons.fade && !s.buttons.seek && !s.buttons.mv && s.buttons.freeze, 'at 768 px More closes it again');
  // More at 1366 px opens Speed and Loop, which is all that is folded there
  await pg.setViewportSize({ width: 1366, height: 768 });
  await frames(pg);
  await pg.click('#wsmore');
  s = await see(pg);
  assert(s.buttons.mv && s.buttons.loop && s.buttons.fade && s.buttons.seek, 'at 1366 px More opens Speed and Loop');
  await pg.click('#wsmore');
  await pg.setViewportSize({ width: 390, height: 844 });
  await frames(pg);
  await go(pg, 'play/pads');
  if ((await see(pg)).more === 'true') await pg.click('#wsmore');
  s = await see(pg);
  const folded = ALL.filter((b) => ALWAYS.indexOf(b) < 0).concat(['seek']);
  ALWAYS.concat(['wsmore']).forEach((b) => assert(s.buttons[b], 'a phone\'s strip shows ' + b));
  folded.forEach((b) => assert(!s.buttons[b], 'a phone\'s strip keeps ' + b + ' behind More'));
  assert.strictEqual(s.more, 'false', 'More says it is closed');
  await pg.click('#wsmore');
  s = await see(pg);
  ALL.concat(['seek', 'wsmore']).forEach((b) => assert(s.buttons[b], 'with More open the strip shows ' + b));
  assert.strictEqual(s.more, 'true', 'More says it is open');
  assert.strictEqual(s.screen, 'play/pads', 'More opens in place');
  await go(pg, 'shape/picture');
  await go(pg, 'play/library');
  s = await see(pg);
  assert(s.more === 'true' && s.buttons.fade && s.buttons.seek && s.buttons.mv, 'the strip stays open on the next screen');
  await pg.click('#wsmore');
  s = await see(pg);
  assert(s.more === 'false' && !s.buttons.fade && s.buttons.stop, 'More closes it again');
}

// ---- Text that overruns (2026-10-09). The owner saw it on the narrow layouts: a page's title cut inside a word
// beside its switch ("PROJECTOR" and "S"), the job line of the title bar squeezed to a column, what plays cut to a
// letter on the strip. The width sweep (tests/ui/sweep.js) holds the controls; this holds every text, with the long
// names a real room has.
/* eslint-disable no-undef */
// Runs in the page. Returns what overruns as [{ kind, name, detail }]; an empty list is a pass. Every text that is
// shown is looked at, by the boxes of its own lines:
//   the page scrolls sideways; a text leaves its own box, a box that holds it (up to the screen) or the window; a
//   text is clipped by a box that hides what runs over (a line cut with dots is right when a title holds the whole
//   text); two texts lie on one another; a first heading or a page's title is cut inside a plain word.
// Not looked at: what scrolls sideways inside itself on purpose, what is read out and not shown (1 px boxes), the
// options of a chooser, and a text of the page against one of a bar that stays put over it.
function overruns() {
  const out = [];
  const root = document.documentElement, shell = document.querySelector('.shell');
  if (!shell) return [{ kind: 'there is no screen', name: '', detail: '' }];
  const vw = root.clientWidth;
  const px = (n) => Math.round(n);
  const tagOf = (el) => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (typeof el.className === 'string' && el.className.trim() ? '.' + el.className.trim().split(/\s+/).join('.') : '');
  const name = (el) => { const t = (el.textContent || '').replace(/\s+/g, ' ').trim(); return tagOf(el) + ' "' + (t.length > 40 ? t.slice(0, 40) + '...' : t) + '"'; };
  const told = {};
  const add = (kind, el, detail) => { const k = kind + '|' + name(el); if (told[k]) return; told[k] = 1; out.push({ kind, name: name(el), detail: detail || '' }); };
  const shown = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden' && (!el.checkVisibility || el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })); };
  if (root.scrollWidth > vw + 1) out.push({ kind: 'the page scrolls sideways', name: '', detail: root.scrollWidth + ' px in a window of ' + vw });
  const range = document.createRange();
  const texts = [];
  const walker = document.createTreeWalker(shell, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const el = n.parentElement, text = n.nodeValue.replace(/\s+/g, ' ').trim();
    if (!text || !el || /^(SCRIPT|STYLE|OPTION|SELECT|TEXTAREA)$/.test(el.tagName) || el.closest('svg, select') || !shown(el)) continue;
    range.selectNodeContents(n);
    const lines = Array.prototype.filter.call(range.getClientRects(), (r) => r.width > 0.5 && r.height > 0.5);
    if (!lines.length) continue;
    const said = '"' + (text.length > 40 ? text.slice(0, 40) + '...' : text) + '" ';
    // the boxes that hold this text, from its own element up to the screen
    let looked = true, layer = 'page';
    const boxes = [];
    for (let e = el; e; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.position === 'sticky' || cs.position === 'fixed') layer = e.id || e.className;
      if (cs.display !== 'inline' && cs.display !== 'contents') {
        const b = e.getBoundingClientRect();
        if (b.width <= 1 || b.height <= 1) { if (/hidden|clip/.test(cs.overflowX) || cs.clipPath !== 'none' || cs.clip !== 'auto') { looked = false; break; } }       // read out, not shown
        else if (/auto|scroll/.test(cs.overflowX)) { if (e !== el || e.scrollWidth > e.clientWidth + 1) { looked = false; break; } }                                  // it scrolls sideways inside itself
        else boxes.push([e, cs, b]);
      }
      if (e === shell) break;
    }
    if (!looked) continue;
    let cut = false;
    for (const [e, cs, b] of boxes) {
      const clips = /hidden|clip/.test(cs.overflowX);
      let over = null;
      for (const r of lines) if (r.right > b.right + 1.5 || r.left < b.left - 1.5) { over = r; break; }
      const where = over ? said + px(over.left) + ' to ' + px(over.right) + ', the box is ' + px(b.left) + ' to ' + px(b.right) : '';
      if (over && clips && cs.textOverflow === 'ellipsis') {
        const holder = e.closest('[title]');
        if (!holder || holder.getAttribute('title').replace(/\s+/g, ' ').indexOf(text.slice(0, 30)) < 0) add('a text is cut with dots and no title holds the whole of it', e, said);
      } else if (over && clips) add('a text is clipped', e, where);
      else if (over) add(e === el ? 'a text runs out of its own box' : 'a text runs out of ' + tagOf(e), el, where);
      else if (/hidden|clip/.test(cs.overflowY) && (lines[lines.length - 1].bottom > b.bottom + 2 || lines[0].top < b.top - 2)) add('a text is clipped above or below', e, said + px(lines[0].top) + ' to ' + px(lines[lines.length - 1].bottom) + ', the box is ' + px(b.top) + ' to ' + px(b.bottom));
      else continue;
      cut = true;
      break;
    }
    if (cut) continue;
    const far = lines.filter((r) => r.right > vw + 1 || r.left < -1)[0];
    if (far) { add('a text leaves the window', el, said + px(far.left) + ' to ' + px(far.right) + ' of ' + vw); continue; }
    // a first heading or a page's title, cut inside a plain word (a long name with no space in it has to be cut)
    if (el.closest('h1, .wstitle') && boxes.length) {
      const room = boxes[0][2].width - parseFloat(boxes[0][1].paddingLeft) - parseFloat(boxes[0][1].paddingRight);
      for (const w of text.split(' ').filter((x) => /^[A-Za-z]{3,14}$/.test(x))) {
        const probe = document.createElement('span');
        probe.textContent = w;
        probe.style.cssText = 'position:absolute;visibility:hidden;white-space:nowrap';
        el.appendChild(probe);
        const need = probe.getBoundingClientRect().width;
        el.removeChild(probe);
        if (need > room + 1) { add('a title is cut inside a word', el, '"' + w + '" needs ' + px(need) + ' px and has ' + px(room)); break; }
      }
    }
    texts.push({ el, lines, layer });
  }
  for (let i = 0; i < texts.length; i++) {
    for (let j = i + 1; j < texts.length; j++) {
      const a = texts[i], b = texts[j];
      if (a.layer !== b.layer || a.el === b.el || a.el.contains(b.el) || b.el.contains(a.el)) continue;
      const hit = a.lines.some((r) => b.lines.some((q) => Math.min(r.right, q.right) - Math.max(r.left, q.left) > 2 && Math.min(r.bottom, q.bottom) - Math.max(r.top, q.top) > 3));
      if (hit) add('two texts lie on one another', a.el, 'the other is ' + name(b.el));
    }
  }
  return out;
}
/* eslint-enable no-undef */

// Long names, put into the box's answers on their way to the page (nothing is stored on the box, so no later step
// meets them). The lengths are the longest each answer may hold: a file's name is free, a preset's and a shader's
// 40 characters, a scene's and a wall's 32, a projector's, a pad's, a device's and a stream's 40.
const LONG = { clip: 'A_very_long_clip_name_without_any_spaces_2026-10-09_final_v3.mp4', shader: 'isf-a-very-long-shader-name-kaleidoscope-tunnel-v3', effect: 'fx-a-very-long-effect-name-chromatic-aberration-v2',
  preset: 'A_very_long_preset_name_for_the_opening', scene: 'Movie_night_with_surround_and_di', wall: 'Main_wall_with_the_three_blended', projector: 'Main_projector_above_the_door_EB-L1075U',
  pad: 'A_very_long_pad_label_without_spaces_26', device: 'Presenters_tablet_in_the_staff_room_iPad', stream: 'Stage_camera_on_the_balcony_PTZ_number_2', drive: 'A_LONG_USB_STICK_NAME_KINGSTON_DATATRAVELER' };
function longNames(url, d) {
  const first = (list, key, value) => { if (Array.isArray(list) && list[0] && typeof list[0][key] === 'string') list[0][key] = value; };
  if (/\/api\/status$/.test(url)) {         // a clip of a playlist plays, an hour in, with the long effect over it
    // (the whole answer about the player is this one: a stream, a live input, a test pattern or a shader that an
    // earlier step left on would be named on the strip in place of the clip)
    d.player = { running: true, path: '/media/' + LONG.clip, duration: 5400, position: 3723, playlist_count: 12, playlist_pos: 10, effect: LONG.effect + '.fs', volume: (d.player || {}).volume, paused: false };
    d.system = Object.assign({}, d.system, { board: 'Raspberry Pi 4 Model B', temp_c: 61.4 });
  } else if (/\/api\/shaders$/.test(url)) {
    (d.shaders || []).slice(0, 2).forEach((x, i) => { if (!i) x.name = LONG.shader; x.presets = [LONG.preset, 'Short']; });       // (the name is what is shown; the id stays)
  } else if (/\/api\/effects$/.test(url)) {
    (d.effects || []).slice(0, 1).forEach((x) => { x.name = LONG.effect; x.presets = [LONG.preset, 'Short']; });
  } else if (/\/api\/media$/.test(url)) {
    d.usb = [{ drive: LONG.drive, files: [{ name: 'festival_reel_2026_with_a_very_long_file_name_final_master_copy.mp4', size: 734003200 }, { name: 'poster.png', size: 2097152 }] }];
  } else if (/\/api\/room$/.test(url)) { first(d.scenes, 'name', LONG.scene); first(d.groups, 'name', LONG.wall); first(d.projectors, 'name', LONG.projector); }
  else if (/\/api\/projectors$/.test(url)) first(d.projectors, 'name', LONG.projector);
  else if (/\/api\/devices$/.test(url)) first(d.devices, 'name', LONG.device);
  else if (/\/api\/streams$/.test(url)) first(d.streams, 'name', LONG.stream);
  else if (/\/api\/pads$/.test(url)) { if (d.banks && d.banks[0] && d.banks[0].pads && d.banks[0].pads[0] && d.banks[0].pads[0].file) d.banks[0].pads[0].label = LONG.pad; }
  return d;
}
const NARROW = [[320, 568], [390, 844], [600, 800], [768, 1024], [740, 360], [1200, 800], [1366, 768], [1440, 900], [1920, 1080]];      // (the last four: an area is a desk)
const FAKED = ['status', 'shaders', 'effects', 'media', 'room', 'projectors', 'devices', 'streams', 'pads'];
// pg: the owner's page, with every module on. post(url, body), get(url): calls of the box as the owner, each
// resolving with the answer's data. Both looks are gone through, and the look the box had is put back.
async function narrow(pg, post, get) {
  const was = ((await get('/api/theme')) || {}).theme || { name: 'dark-stage', accent: null };
  const home = await pg.evaluate(() => location.origin + '/');
  // (a request that is under way when the page moves on ends in an error of its own: it is let go, not reported)
  const handler = async (route) => {
    try {
      if (route.request().method() !== 'GET') return await route.continue();
      const r = await route.fetch();
      let d = null;
      try { d = await r.json(); } catch (e) { d = null; }
      if (!d || r.status() !== 200) return await route.fulfill({ response: r });
      return await route.fulfill({ response: r, json: longNames(route.request().url().split('?')[0], d) });
    } catch (e) { return route.continue().catch(() => {}); }
  };
  const found = [];
  let looked = 0;
  for (const u of FAKED) await pg.route('**/api/' + u, handler);
  try {
    for (const lookName of ['dark-stage', 'signal']) {
      await post('/api/theme', { name: lookName, accent: null });
      await pg.setViewportSize({ width: 390, height: 844 });
      await pg.goto(home);
      await ready(pg);
      await pg.waitForFunction(() => !!document.querySelector('#wsside [data-ar="room"]'), null, { timeout: 15000 });
      const keys = await screens(pg);
      assert(keys.length >= 20, 'the screens for the narrow widths: ' + keys.join(' '));
      for (const key of keys) {
        await pg.setViewportSize({ width: 390, height: 844 });
        await frames(pg);
        await go(pg, key);
        // its cards have their data (the same wait for every screen: a poll of the status is one second), the folds
        // are opened, and on a phone the strip is open too: More shows the most there is
        await pg.waitForFunction(() => !!document.querySelector('main.ws .screen') && !/Checking\.\.\./.test(document.querySelector('main.ws').textContent) && /A_very_long_clip_name/.test(document.getElementById('np').textContent), null, { timeout: 15000 });
        await pg.evaluate(() => { document.querySelectorAll('main.ws details').forEach((x) => { x.open = true; }); });
        for (const [width, height] of NARROW) {
          await pg.setViewportSize({ width, height });
          for (const more of width < 600 || width === 1366 ? [false, true] : [false]) {        // (at 1366 px More holds Speed and Loop)
            if ((await pg.evaluate(() => document.getElementById('wsmore').getAttribute('aria-expanded') === 'true')) !== more) await pg.evaluate(() => document.getElementById('wsmore').click());
            await frames(pg);
            const got = await pg.evaluate(overruns);
            looked++;
            // from 600 px the strip is one line with Freeze, Stop and Blackout on it, in this look and with this name too
            if (width >= 600 && !more) {
              const l = await pg.evaluate(lie, STRIP.concat(SPEED));
              if (l.lines !== 1 || !l.at.freeze || !l.at.stop || !l.at.black || l.at.black.right > l.vw) got.push({ kind: 'the strip is not one line with Freeze, Stop and Blackout on it', name: '', detail: l.lines + ' lines: ' + JSON.stringify(l.at) });
            }
            got.forEach((f) => found.push(lookName + ', ' + key + ' at ' + width + ' by ' + height + (more ? ' with More open' : '') + ': ' + f.kind + (f.name ? ': ' + f.name : '') + (f.detail ? ' (' + f.detail + ')' : '')));
          }
        }
      }
    }
  } finally {
    for (const u of FAKED) await pg.unroute('**/api/' + u, handler).catch(() => {});
    await post('/api/theme', { name: was.name, accent: was.accent === undefined ? null : was.accent });
    await pg.setViewportSize({ width: 390, height: 844 });
    await pg.goto(home);
    await ready(pg);
    if ((await see(pg)).more === 'true') await pg.click('#wsmore');
  }
  assert.deepStrictEqual(found, [], 'text overruns at narrow widths (' + found.length + '):\n' + found.slice(0, 60).join('\n') + (found.length > 60 ? '\n... and ' + (found.length - 60) + ' more' : '') + '\n');
  return looked;
}

// What each role has, with every module on (tests/ui/signal-pages.js setUp). A presenter has no page of Setup that
// is for the owner, and the mapping card without its page; a guest has no way to let anyone in.
const PLAY = ['play/pads', 'play/library', 'play/shaders'];
const SHAPE = ['shape/effect', 'shape/picture', 'shape/sound', 'shape/mapping'];
const MENUS = {
  owner: PLAY.concat(SHAPE, ['room/scenes', 'room/walls', 'room/guests'], ['index', 'health', 'projectors', 'room', 'schedule', 'access', 'autostart', 'streams', 'sync', 'midi', 'dmx', 'osc',
    'network', 'updates', 'support', 'backup', 'look', 'about'].map((x) => 'setup/' + x)),
  presenter: PLAY.concat(SHAPE, ['room/scenes', 'room/walls', 'room/guests'], ['index', 'health', 'projectors', 'access', 'streams', 'sync', 'about'].map((x) => 'setup/' + x)),
  guest: PLAY.concat(SHAPE, ['room/scenes', 'room/walls'], ['index', 'health', 'about'].map((x) => 'setup/' + x)),
};
// pages: { owner, presenter, guest }, each a page that has just been loaded. post(url, body): as the owner.
async function roles(pages, post) {
  for (const who of ['owner', 'presenter', 'guest']) {
    const pg = pages[who];
    await ready(pg);
    // The modules have been read once the Room area is on the rail. If it does not come: say what the device
    // had, and fail. A file of the page that did not arrive is asked for again by the page itself (load.js, D68),
    // so a panel without the Room area is a failure, of the panel or of that loader; "files" says which file it was.
    const roomIn = () => pg.waitForFunction(() => !!document.querySelector('#wsside [data-ar="room"]'), null, { timeout: 8000 });
    try { await roomIn(); } catch (e) {
      const had = await pg.evaluate(() => fetch('/api/modules').then((r) => r.json().then((d) => ({ status: r.status, on: (d.modules || []).filter((m) => m.enabled).map((m) => m.id) })), () => ({ status: 0 }))
        .then((box) => ({ box, roomScript: !!window.pvjRoom, loaded: document.readyState,
          files: performance.getEntriesByType('resource').filter((x) => /\.(js|css)$/.test(x.name)).map((x) => x.name.replace(/^.*\//, '') + ': ' + x.responseStatus + ', ' + x.decodedBodySize + ' bytes'),
          areas: Array.prototype.map.call(document.querySelectorAll('#wsside [data-ar]'), (b) => b.getAttribute('data-ar')) })));
      throw new Error('the panel of a ' + who + ' has no Room area: ' + JSON.stringify(had));
    }
    const s = await see(pg);
    // staff and guests land on the Room; the owner on the pads
    assert.strictEqual(s.screen, who === 'owner' ? 'play/pads' : 'room/scenes', 'where a ' + who + ' lands');
    assert.deepStrictEqual(s.tabNames, ['Play', 'Shape', 'Room', 'Setup'], 'the areas a ' + who + ' has');
    assert.deepStrictEqual(s.disabled, who === 'guest' ? STRIP.concat(SPEED) : s.disabled.filter((b) => b === 'prev' || b === 'next'), 'which of the strip\'s controls a ' + who + ' cannot use');
    assert.deepStrictEqual(await screens(pg), MENUS[who], 'the screens a ' + who + ' has');
    // a desk has the columns the role has: at 1366 px the Room of a guest is two columns, of the others three
    await pg.setViewportSize({ width: 1366, height: 800 });
    await go(pg, 'room/scenes');
    assert.deepStrictEqual((await see(pg)).cols, who === 'guest' ? ['scenes', 'walls'] : ['scenes', 'walls', 'guests'], 'the columns of a ' + who + '\'s Room at 1366 px');
    await go(pg, 'play/pads');
    assert.deepStrictEqual((await see(pg)).cols, ['pads', 'library', 'shaders'], 'the columns of a ' + who + '\'s Play at 1366 px');
    await go(pg, 'shape/picture');
    assert.deepStrictEqual((await see(pg)).cols, ['effect', 'picture', 'sound'], 'the columns of a ' + who + '\'s Shape at 1366 px');
    await pg.setViewportSize({ width: 390, height: 844 });
  }
  // a presenter's mapping is the card without the page and its switch; a guest's Sound is Volume and Audio alone;
  // both have Test pattern on Mapping, which a guest cannot press
  await go(pages.presenter, 'shape/mapping');
  assert(await pages.presenter.evaluate(() => !!document.querySelector('#plainpage #mapcard') && !document.getElementById('sysswitch') && !!document.querySelector('#plainpage #testpattern')), 'a presenter has the mapping card, Test pattern and no switch');
  await go(pages.guest, 'shape/mapping');
  assert(await pages.guest.evaluate(() => { const t = document.querySelector('#plainpage #testpattern'); return !!t && t.disabled; }), 'a guest sees Test pattern on Mapping, and cannot press it');
  await go(pages.guest, 'shape/sound');
  assert(await pages.guest.evaluate(() => !!document.querySelector('#plainpage #mvol') && !document.getElementById('audiocard') && document.getElementById('mvol').disabled), 'a guest has the Volume slider, and cannot move it');
  await go(pages.guest, 'room/scenes');
  await go(pages.presenter, 'room/scenes');
  // a module that is switched off: its screens are gone (no tab, no column), and the owner's Setup index keeps its
  // row, which opens a page of Setup with the switch; a desk with one column left is just that screen; and with
  // no Mapping screen, Test pattern is on Picture
  await post('/api/modules/inputs-srt', { enabled: false });
  await post('/api/modules/shaders', { enabled: false });
  await post('/api/modules/mapper', { enabled: false });
  try {
    const pg = pages.owner;
    await pg.goto(await pg.evaluate(() => location.origin + '/'));
    await ready(pg);
    await pg.waitForFunction(() => !!document.querySelector('#wsside [data-ar="room"]'), null, { timeout: 15000 });
    const keys = await screens(pg);
    ['play/shaders', 'shape/effect', 'shape/mapping'].forEach((k) => assert(keys.indexOf(k) < 0, 'with its module off, ' + k + ' is not a screen'));
    await pg.setViewportSize({ width: 1366, height: 800 });
    await go(pg, 'play/pads');
    assert.deepStrictEqual((await see(pg)).cols, ['pads', 'library'], 'with Shaders off, Play\'s desk is the pads and the library');
    await go(pg, 'shape/picture');
    let s = await see(pg);
    assert.deepStrictEqual([s.cols, s.sub], [['picture', 'sound'], false], 'with Shaders and Mapping off, Shape\'s desk is Picture and Sound, with no tabs');
    assert(await pg.isVisible('#picturescreen #testpattern'), 'with no Mapping screen, Test pattern is on Picture');
    await pg.setViewportSize({ width: 390, height: 844 });
    await go(pg, 'setup/index');
    await pg.waitForFunction(() => !!document.querySelector('.navrow'), null, { timeout: 15000 });
    s = await see(pg);
    ['Streams', 'Shaders and Vibes', 'Projection mapping'].forEach((n) => assert(s.rows.indexOf(n) >= 0, 'the owner\'s Setup index still has the row ' + n));
    assert.deepStrictEqual(await pg.evaluate(() => ['streams', 'vibes', 'mapping'].map((id) => !!document.querySelector('#nav-' + id + ' .chip-off:not([hidden])'))), [true, true, true], 'each of those rows says Off');
    await pg.click('#nav-vibes');
    await pg.waitForFunction(() => !!document.getElementById('sysswitchon') && (document.querySelector('#syspage h1') || {}).textContent === 'Shaders and Vibes', null, { timeout: 15000 });
    const off = await see(pg);
    assert.strictEqual(off.screen, 'setup/vibes', 'the page that is off is a page of Setup');
    assert.deepStrictEqual(off.open, ['tab-setup'], 'and Setup is the area that is open');
    assert.strictEqual(await pg.evaluate(() => document.getElementById('sysback').textContent), '‹ Setup', 'with a Back to the index');
  } finally {
    await post('/api/modules/inputs-srt', { enabled: true });
    await post('/api/modules/shaders', { enabled: true });
    await post('/api/modules/mapper', { enabled: true });
  }
  await pages.owner.goto(await pages.owner.evaluate(() => location.origin + '/'));
  await ready(pages.owner);
}

// Controls that are gone on purpose, with the reason and the place where the same thing is now. Everything else
// that was in the panel before must be there. (D72: the effect strip that stood on Live, then on Shape >
// Controls, was a second copy of the head of the Effects card. The playing shader's strip was the other copy that
// went; it has no entry here because nothing plays while the list is taken, so none of its controls is in it:
// panel.test.js holds each of them on the Shaders screen while a shader plays.)
const FX_STRIP = {
  '#livefxprev': 'the effect strip was a copy: the same control is #fxprev on Shape > Effect',
  '#livefxon': 'the effect strip was a copy: the same control is #fxon on Shape > Effect (#fxoff while an effect is on)',
  '#livefxnext': 'the effect strip was a copy: the same control is #fxnext on Shape > Effect',
  '#livefxmore': 'the effect strip\'s link to the Effects card: the card is the Effect screen, a tab of Shape (a column of its desk from 1200 px)',
};
const MOVED = {
  owner: FX_STRIP,
  presenter: Object.assign({ '#nav-room': 'the row "Room" of a presenter\'s System index led to the Room screen\'s own cards; they are the screens of the Room area now' }, FX_STRIP),
  guest: { '#livefxmore': FX_STRIP['#livefxmore'] },
};
// What stands for each of them: the list fails if a control that replaced a removed copy is not there.
const IN_PLACE = { owner: ['#fxprev', '#fxon', '#fxnext'], presenter: ['#fxprev', '#fxon', '#fxnext'], guest: [] };
// o: { open(token) -> a page loaded with that token (null: the owner's, already paired), prepare: the t for
// inventory.prepare (its page is the owner's), log(text) }. The box behind it has no player (see inventory.js).
async function inventory(o) {
  const before = require('./fixtures/controls-before.json');
  const tokens = await inv.prepare(o.t);
  const out = {}, failed = [];
  for (const [role, token] of [['owner', null], ['presenter', tokens.presenter], ['guest', tokens.guest]]) {
    const pg = await o.open(token);
    await pg.setViewportSize({ width: 1366, height: 900 });
    const after = await inv.walk(pg);
    let lost = inv.compare(before.roles[role], after, MOVED[role]);
    if (lost.length) {          // a card whose answer came late on a slow machine reads as lost: every screen is taken once more
      if (o.log) o.log('controls of the ' + role + ': ' + lost.length + ' not seen at first, walking once more');
      const again = await inv.walk(pg);
      Object.keys(again).forEach((k) => { after[k] = after[k] || again[k]; });
      lost = inv.compare(before.roles[role], after, MOVED[role]);
    }
    const fresh = Object.keys(after).filter((k) => !before.roles[role][k]);
    IN_PLACE[role].forEach((k) => { if (!after[k]) lost.push(k + '  (it stands for a copy that was removed on purpose, and is not there)'); });
    // an exception is for what is gone: one that names a control which is still there is out of date
    Object.keys(MOVED[role] || {}).forEach((k) => { if (after[k]) lost.push(k + '  (listed as removed on purpose, and it is there: take it off the list)'); });
    out[role] = { before: Object.keys(before.roles[role]).length, after: Object.keys(after).length, lost, fresh };
    if (o.log) o.log('controls of the ' + role + ': ' + out[role].before + ' before the shell, ' + out[role].after + ' now' + (fresh.length ? '; new: ' + fresh.join(', ') : ''));
    if (lost.length) failed.push('controls ' + (role === 'owner' ? 'an ' : 'a ') + role + ' could reach before the Workspace shell and cannot now:\n  ' + lost.join('\n  '));
  }
  // all three roles are compared before the check fails, so one run names everything that is missing
  assert.deepStrictEqual(failed, [], failed.join('\n') + '\n(the list before: ' + before.made_from + ')\n');
  return out;
}

module.exports = { screens, reach, tabWalk, menus, strip, narrow, overruns, longNames, roles, inventory, MENUS, MOVED };
