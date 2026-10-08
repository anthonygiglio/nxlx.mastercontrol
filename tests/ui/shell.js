// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// The Workspace shell (D65), held by the browser test: four areas with screens that have one job, tabs under 600
// px and a side menu from 600 px, one transport strip on every screen, fewer screens for a presenter and a guest,
// and no control lost in the move.
//
//   reach(pg, how)        every screen of the menu is opened at 390, 768 and 1366 px, by a press ('tap') or with
//                         the keyboard ('keys'), and on each: the right screen is drawn, the title bar names its
//                         area, one item of the menus is marked as open, the strip is whole, exactly one first
//                         heading is shown, and the cursor is not lost (it is on the item pressed, or on the screen)
//   tabWalk(pg)           the Tab key alone, pressed from the top of the page at 390, 768 and 1366 px, comes to
//                         every item of the menus that is shown and to the strip's buttons, the side menu before
//                         the screen (reach() by 'keys' puts the cursor on an item and presses Enter: it shows that
//                         Enter opens the screen, this shows that the keyboard gets there)
//   menus(pg)             which menu is shown at 390, 599, 600, 768 and 1366 px
//   strip(pg)             the strip's nine buttons and the place in the clip from 600 px; on a phone four of
//                         them, and More opens the rest in place, stays open on the next screen, and closes
//   roles(pages)          what the owner, a presenter and a guest have in their menus, where each lands, and that
//                         a switched-off module's page is not in the menu while the owner's Setup index has its row
//   inventory(o)          the controls of every screen, by role, against the list of the panel before the shell
//                         (tests/ui/fixtures/controls-before.json): nothing may be missing
//
// Every function throws an Error that says what was seen. pg: a Playwright page (click, focus, isVisible,
// keyboard.press, evaluate, waitForFunction, setViewportSize and goto are all that is used).
'use strict';
const assert = require('assert');
const { go, at } = require('./signal-pages.js');
const inv = require('./inventory.js');

const AREAS = { play: 'Play', shape: 'Shape', room: 'Room', setup: 'Setup' };
const SIZES = [[390, 844], [768, 1024], [1366, 768]];
const STRIP = ['prev', 'back10', 'fwd10', 'next', 'fadein', 'fade', 'freeze', 'stop', 'black'];
const ALWAYS = ['prev', 'next', 'stop', 'black'];

/* eslint-disable no-undef */
function look(strip) {
  const shown = (el) => { if (!el) return false; const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'; };
  const id = (q) => document.getElementById(q);
  const main = document.querySelector('main.ws'), a = document.activeElement;
  const open = Array.prototype.filter.call(document.querySelectorAll('#wsside [aria-current="page"], #wstabs [aria-current="page"], #wssub [aria-current="page"]'), shown);
  const buttons = {};
  strip.concat(['seek', 'wsmore']).forEach((b) => { buttons[b] = shown(id(b)); });
  const dock = id('wsdock') ? id('wsdock').getBoundingClientRect() : null;
  return {
    screen: main ? main.getAttribute('data-screen') : null,
    title: id('wstitle') ? id('wstitle').textContent : null,
    bar: shown(id('wshead')), tabs: shown(id('wstabs')), side: shown(id('wsside')), sub: shown(id('wssub')),
    open: open.map((el) => el.id),
    heads: Array.prototype.filter.call(document.querySelectorAll('.shell h1'), shown).map((el) => el.textContent),
    buttons, more: id('wsmore') ? id('wsmore').getAttribute('aria-expanded') : null,
    dockAtFoot: !!dock && Math.abs(dock.bottom - window.innerHeight) <= 1,
    focus: !a || a === document.body ? '' : a.id || a.tagName.toLowerCase(),
    menu: Array.prototype.map.call(document.querySelectorAll('#wsside [data-go]'), (b) => b.getAttribute('data-go')),
    tabNames: Array.prototype.map.call(document.querySelectorAll('#wstabs button'), (b) => b.textContent),
    rows: Array.prototype.map.call(document.querySelectorAll('.navrow .navname'), (b) => b.textContent),
    disabled: strip.filter((b) => id(b) && id(b).disabled),
    landmarks: { nav: Array.prototype.map.call(document.querySelectorAll('.shell nav'), (n) => n.getAttribute('aria-label')), main: document.querySelectorAll('.shell main').length, header: document.querySelectorAll('.shell header').length },
  };
}
/* eslint-enable no-undef */
const see = (pg) => pg.evaluate(look, STRIP);
const ready = (pg) => pg.waitForFunction(() => !!document.querySelector('#wsside [data-go]'), null, { timeout: 15000 });
const frames = (pg) => pg.evaluate(() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done))));

async function reach(pg, how) {
  await ready(pg);
  let count = 0;
  for (const [width, height] of SIZES) {
    await pg.setViewportSize({ width, height });
    await frames(pg);
    const keys = (await see(pg)).menu;
    assert(keys.length >= 4, 'the menu lists screens at ' + width + ' px: ' + keys.join(' '));
    for (const key of keys) {
      const where = key + ' at ' + width + ' px by ' + how;
      await go(pg, key, how);
      const s = await see(pg);
      const area = key.split('/')[0];
      assert.strictEqual(s.screen, key, where + ': the screen that is open');
      assert.strictEqual(s.title, AREAS[area], where + ': the title bar names the area');
      assert(s.bar, where + ': the title bar is shown');
      assert.strictEqual(s.heads.length, 1, where + ': one first heading is shown, not ' + JSON.stringify(s.heads));
      // the open item: the side menu's from 600 px; on a phone the area's tab, and the screen in the row under the title
      if (width >= 600) assert.deepStrictEqual(s.open, ['side-' + key.replace('/', '-')], where + ': the open item of the side menu');
      else assert.deepStrictEqual(s.open.filter((x) => x.indexOf('tab-') === 0), ['tab-' + area], where + ': the open tab');
      if (width < 600 && area !== 'setup') assert(s.open.indexOf('sub-' + key.replace('/', '-')) >= 0, where + ': the open screen in the row under the title, of ' + JSON.stringify(s.open));
      ALWAYS.forEach((b) => assert(s.buttons[b], where + ': the strip has ' + b));
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
      return Array.prototype.filter.call(document.querySelectorAll('#wsside button, #wssub button, #wstabs button, #wstp button'), (b) => shown(b) && !b.disabled).map((b) => b.id);
    });
    const seen = [];
    for (let i = 0; i < 200; i++) {
      await pg.keyboard.press('Tab');
      const id = await pg.evaluate(() => { const a = document.activeElement; return !a || a === document.body ? '' : a.id || (a.closest('main') ? 'main:' : '') + a.tagName.toLowerCase(); });
      if (seen.length && id === seen[0]) break;          // round again
      seen.push(id);
    }
    const missed = want.filter((id) => seen.indexOf(id) < 0);
    assert.deepStrictEqual(missed, [], 'at ' + width + ' px the Tab key never comes to: ' + missed.join(', ') + ' (it came to ' + seen.join(' ') + ')');
    ALWAYS.concat(width < 600 ? ['wsmore', 'tab-play', 'tab-setup', 'sub-shape-sound'] : ['side-play-pads', 'side-setup-index', 'side-shape-sound']).forEach((id) => {
      if (id !== 'prev' && id !== 'next') assert(seen.indexOf(id) >= 0, 'at ' + width + ' px the Tab key comes to ' + id);
    });
    const inMain = seen.findIndex((id) => id.indexOf('main:') === 0 || id === 'mvol');
    if (width >= 600) assert(inMain > seen.indexOf('side-setup-index') && seen.indexOf('side-play-pads') >= 0, 'at ' + width + ' px the side menu comes before the screen in the Tab order');
    assert(inMain >= 0 && inMain < seen.indexOf('stop'), 'at ' + width + ' px the screen comes before the strip in the Tab order');
    out[width] = seen.length;
  }
  return out;
}

async function menus(pg) {
  await ready(pg);
  for (const [width, tabs] of [[390, true], [599, true], [600, false], [768, false], [1366, false]]) {
    await pg.setViewportSize({ width, height: 900 });
    await frames(pg);
    await go(pg, 'shape/picture');
    const s = await see(pg);
    assert.strictEqual(s.tabs, tabs, 'the area tabs at ' + width + ' px');
    assert.strictEqual(s.side, !tabs, 'the side menu at ' + width + ' px');
    assert.strictEqual(s.sub, tabs, 'the row of the area\'s screens at ' + width + ' px');
    assert.deepStrictEqual(s.landmarks.nav.slice().sort(), ['Areas', 'Every screen', 'Screens of Shape'], 'the menus are named landmarks');
    assert(s.landmarks.main === 1 && s.landmarks.header === 1, 'one main part and one title bar: ' + JSON.stringify(s.landmarks));
  }
}

async function strip(pg) {
  await ready(pg);
  for (const [width, height] of [[768, 1024], [1366, 768], [667, 375]]) {           // from 600 px: everything, and no More
    await pg.setViewportSize({ width, height });
    await frames(pg);
    const s = await see(pg);
    STRIP.concat(['seek']).forEach((b) => assert(s.buttons[b], 'at ' + width + ' px the strip shows ' + b));
    assert(!s.buttons.wsmore, 'at ' + width + ' px there is no More');
  }
  await pg.setViewportSize({ width: 390, height: 844 });
  await frames(pg);
  await go(pg, 'play/pads');
  if ((await see(pg)).more === 'true') await pg.click('#wsmore');
  let s = await see(pg);
  const folded = STRIP.filter((b) => ALWAYS.indexOf(b) < 0).concat(['seek']);
  ALWAYS.concat(['wsmore']).forEach((b) => assert(s.buttons[b], 'a phone\'s strip shows ' + b));
  folded.forEach((b) => assert(!s.buttons[b], 'a phone\'s strip keeps ' + b + ' behind More'));
  assert.strictEqual(s.more, 'false', 'More says it is closed');
  await pg.click('#wsmore');
  s = await see(pg);
  STRIP.concat(['seek', 'wsmore']).forEach((b) => assert(s.buttons[b], 'with More open the strip shows ' + b));
  assert.strictEqual(s.more, 'true', 'More says it is open');
  assert.strictEqual(s.screen, 'play/pads', 'More opens in place');
  await go(pg, 'shape/picture');
  await go(pg, 'play/library');
  s = await see(pg);
  assert(s.more === 'true' && s.buttons.fade && s.buttons.seek, 'the strip stays open on the next screen');
  await pg.click('#wsmore');
  s = await see(pg);
  assert(s.more === 'false' && !s.buttons.fade && s.buttons.stop, 'More closes it again');
}

// What each role has, with every module on (tests/ui/signal-pages.js setUp). A presenter has no page of Setup that
// is for the owner, and the mapping card without its page; a guest has no way to let anyone in.
const PLAY = ['play/pads', 'play/library', 'play/shaders'];
const SHAPE = ['shape/controls', 'shape/effect', 'shape/picture', 'shape/mapping', 'shape/sound'];
const MENUS = {
  owner: PLAY.concat(SHAPE, ['room/scenes', 'room/walls', 'room/guests'], ['index', 'health', 'projectors', 'room', 'schedule', 'access', 'autostart', 'streams', 'sync', 'midi', 'dmx', 'osc',
    'network', 'updates', 'support', 'backup', 'look', 'about'].map((x) => 'setup/' + x)),
  presenter: PLAY.concat(SHAPE, ['room/scenes', 'room/walls', 'room/guests'], ['index', 'health', 'projectors', 'access', 'streams', 'sync', 'about'].map((x) => 'setup/' + x)),
  guest: PLAY.concat(SHAPE, ['room/scenes', 'room/walls'], ['index', 'health', 'about'].map((x) => 'setup/' + x)),
};
// pages: { owner, presenter, guest }, each a page that has just been loaded. post(url, body): as the owner. log(text): optional.
async function roles(pages, post, log) {
  const again = {};
  for (const who of ['owner', 'presenter', 'guest']) {
    const pg = pages[who];
    await ready(pg);
    // The modules have been read once the Room screens are in the menu. If they do not come: say what the device
    // had. One cause is not the panel's: a file of the page that the box never delivered (room.js with no answer at
    // all, seen about once in ten runs on the dev Mac with three browsers on one address; the server's queue for
    // new connections is five). Then the page is loaded once more, and that is said; anything else fails here.
    const roomIn = () => pg.waitForFunction(() => document.querySelectorAll('#wsside [data-go^="room/"]').length > 0, null, { timeout: 8000 });
    try { await roomIn(); } catch (e) {
      const had = await pg.evaluate(() => fetch('/api/modules').then((r) => r.json().then((d) => ({ status: r.status, on: (d.modules || []).filter((m) => m.enabled).map((m) => m.id) })), () => ({ status: 0 }))
        .then((box) => ({ box, roomScript: !!window.pvjRoom, loaded: document.readyState,
          files: performance.getEntriesByType('resource').filter((x) => /\.(js|css)$/.test(x.name)).map((x) => x.name.replace(/^.*\//, '') + ': ' + x.responseStatus + ', ' + x.decodedBodySize + ' bytes'),
          menu: Array.prototype.map.call(document.querySelectorAll('#wsside [data-go]'), (b) => b.getAttribute('data-go')) })));
      if (had.roomScript || again[who]) throw new Error('the menu of a ' + who + ' has no Room screens: ' + JSON.stringify(had));
      again[who] = true;
      if (log) log('the Workspace shell, ' + who + ': a file of the page was not delivered (' + had.files.filter((f) => !/: 200,/.test(f)).join('; ') + '); the page is loaded once more');
      await pg.goto(await pg.evaluate(() => location.origin + '/'));
      await ready(pg);
      await roomIn();
    }
    const s = await see(pg);
    assert.deepStrictEqual(s.menu, MENUS[who], 'the screens in a ' + who + '\'s menu');
    assert.deepStrictEqual(s.tabNames, ['Play', 'Shape', 'Room', 'Setup'], 'the areas a ' + who + ' has');
    // staff and guests land on the Room; the owner on the pads
    assert.strictEqual(s.screen, who === 'owner' ? 'play/pads' : 'room/scenes', 'where a ' + who + ' lands');
    assert.deepStrictEqual(s.disabled, who === 'guest' ? STRIP : s.disabled.filter((b) => b === 'prev' || b === 'next'), 'which of the strip\'s buttons a ' + who + ' cannot press');
  }
  // a presenter's mapping is the card without the page and its switch; a guest's Sound is Volume and Audio alone
  await go(pages.presenter, 'shape/mapping');
  assert(await pages.presenter.evaluate(() => !!document.querySelector('#plainpage #mapcard') && !document.getElementById('sysswitch')), 'a presenter has the mapping card and no switch');
  await go(pages.guest, 'shape/sound');
  assert(await pages.guest.evaluate(() => !!document.querySelector('#plainpage #mvol') && !document.getElementById('audiocard') && document.getElementById('mvol').disabled), 'a guest has the Volume slider, and cannot move it');
  await go(pages.guest, 'room/scenes');
  await go(pages.presenter, 'room/scenes');
  // a module that is switched off: its page leaves the menu, and the owner's Setup index keeps its row, with the switch
  await post('/api/modules/inputs-srt', { enabled: false });
  await post('/api/modules/shaders', { enabled: false });
  try {
    const pg = pages.owner;
    await pg.goto(await pg.evaluate(() => location.origin + '/'));
    await ready(pg);
    await pg.waitForFunction(() => document.querySelectorAll('#wsside [data-go^="room/"]').length > 0, null, { timeout: 15000 });
    await go(pg, 'setup/index');
    await pg.waitForFunction(() => !!document.querySelector('.navrow'), null, { timeout: 15000 });
    const s = await see(pg);
    ['setup/streams', 'play/shaders', 'shape/effect'].forEach((k) => assert(s.menu.indexOf(k) < 0, 'with its module off, ' + k + ' is not in the menu'));
    ['Streams', 'Shaders and Vibes'].forEach((n) => assert(s.rows.indexOf(n) >= 0, 'the owner\'s Setup index still has the row ' + n));
    await pg.click('#nav-vibes');
    await pg.waitForFunction(() => !!document.getElementById('sysswitchon') && (document.querySelector('#syspage h1') || {}).textContent === 'Shaders and Vibes', null, { timeout: 15000 });
    const off = await see(pg);
    assert.strictEqual(off.screen, 'play/shaders', 'the page that is off opens where it lives');
    assert.deepStrictEqual(off.open.filter((x) => x.indexOf('side-') === 0 || x.indexOf('sub-') === 0), [], 'and no item of the menu is marked for it');
    assert.strictEqual(await pg.evaluate(() => document.getElementById('sysback').textContent), '‹ Setup', 'with a Back to the index');
  } finally {
    await post('/api/modules/inputs-srt', { enabled: true });
    await post('/api/modules/shaders', { enabled: true });
  }
  await pages.owner.goto(await pages.owner.evaluate(() => location.origin + '/'));
  await ready(pages.owner);
}

// Controls that are gone on purpose, with the reason. Everything else that was in the panel before must be there.
const MOVED = {
  presenter: { '#nav-room': 'the row "Room" of a presenter\'s System index led to the Room screen\'s own cards; they are the screens of the Room area now' },
};
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
    out[role] = { before: Object.keys(before.roles[role]).length, after: Object.keys(after).length, lost, fresh };
    if (o.log) o.log('controls of the ' + role + ': ' + out[role].before + ' before the shell, ' + out[role].after + ' now' + (fresh.length ? '; new: ' + fresh.join(', ') : ''));
    if (lost.length) failed.push('controls ' + (role === 'owner' ? 'an ' : 'a ') + role + ' could reach before the Workspace shell and cannot now:\n  ' + lost.join('\n  '));
  }
  // all three roles are compared before the check fails, so one run names everything that is missing
  assert.deepStrictEqual(failed, [], failed.join('\n') + '\n(the list before: ' + before.made_from + ')\n');
  return out;
}

module.exports = { reach, tabWalk, menus, strip, roles, inventory, MENUS, MOVED };
