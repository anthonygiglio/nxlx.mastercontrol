// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// The control inventory (D65): every control a person can reach in the panel, by role, as a list of stable keys, so
// that a change of the panel's layout can be held to "nothing was lost in the move".
//
//   collect()          runs in the page: the keys of the controls that are shown right now
//   gather(pg)         collect(), then again with every fold opened, every "+ Add" form opened and each row's "More"
//                      opened in turn; returns the union
//   prepare(t)         puts the box in the state both lists are taken in (see below)
//   walk(pg)           goes through every screen of the panel as it is now and returns { key: [where it was seen] }
//   walkBefore(pg)     the same for the panel before the Workspace shell (five tabs, System and its pages); it is how
//                      tests/ui/fixtures/controls-before.json was made, and runs only on a tree from before D65
//   compare(before, after, moved)   what is in before and not in after, apart from the keys in `moved`
//
// A key is the control's id when it has one, else its kind and its label (aria-label, else its words), with digits
// replaced so that a code or a count does not make two runs differ. Navigation is not counted: the old tab bar, the
// new menus, and the Back button of a page; the rows of the Setup index are (they were rows of the System index).
//
// The state: tests/ui/signal-pages.js setUp (modules on, projectors, walls, scenes, a schedule, streams, codes, a
// mapping, two controllers), with nothing playing, Vibes off and no effect on, in a harness that has NO player
// (PVJ_HARNESS_NO_PLAYER=1), because the fixture was made on a machine without mpv and what a card offers can depend
// on the player. So the inventory holds the panel's layout, not what appears only while a clip or a shader plays;
// the steps of tests/ui/panel.test.js that play something hold those.
//
// Two more things belong to that state, and prepare() refuses to go on without them (it throws, and says what the
// box answered), because a card that is not drawn reads as "controls lost in the move":
//   - MIDI, DMX and OSC are switched on. A page of Setup whose flag is off shows "Switch on ..." in place of its
//     card. DMX and OSC each listen on a UDP port of the machine (6454 or 5568, and the OSC port of the settings),
//     so a second box of the harness cannot switch them on while a first one is listening: the box answers 409 and
//     stays off. Whoever starts the inventory's box beside another one frees those ports first.
//   - the owner's own device is called OWNER, the name it had when the fixture was made: its row on People and
//     codes has a button "Remove <name>", which is a key like any other.
//
// pg: a Playwright page, or anything with evaluate(fn, arg), goto(url) and waitForFunction(fn, arg, { timeout }).
'use strict';

/* eslint-disable no-undef */
function collect() {
  const shown = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden' && (!el.checkVisibility || el.checkVisibility()); };
  const words = (s) => String(s || '').replace(/\s+/g, ' ').trim().replace(/\d+/g, '#').slice(0, 60);
  const out = [];
  document.querySelectorAll('button, select, input, textarea, summary, a[href], canvas, .xypad').forEach((el) => {
    if (!shown(el)) return;
    if (el.closest('nav, .wssub, #wsmore') || el.id === 'sysback') return;          // navigation, and the strip's own More
    const kind = el.tagName === 'INPUT' ? 'input-' + (el.type || 'text') : el.getAttribute('role') === 'switch' ? 'switch' : el.tagName.toLowerCase();
    if (el.id) return out.push('#' + el.id.replace(/-[0-9a-f]{6,}$/, '-*').replace(/\d+/g, '#'));      // (a projector's id is made up by the box)
    out.push(kind + ': ' + words(el.getAttribute('aria-label') || el.textContent || el.getAttribute('placeholder') || el.className));
  });
  return out;
}
// How many controls are in the page and how many requests the page has made: gather() waits for both to stand still.
function pulse() { return document.querySelectorAll('button, select, input, summary, canvas').length + ':' + performance.getEntriesByType('resource').length; }
/* eslint-enable no-undef */

const OWNER = 'local';
const LISTENERS = [['MIDI', '/api/midi'], ['DMX', '/api/dmx'], ['OSC', '/api/osc']];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function settle(pg, least) {
  await sleep(least || 300);
  let last = '', same = 0;
  for (let i = 0; i < 40 && same < 3; i++) { const now = await pg.evaluate(pulse); same = now === last ? same + 1 : 0; last = now; await sleep(200); }
}
async function gather(pg) {
  const seen = new Set();
  const take = async () => { (await pg.evaluate(collect)).forEach((k) => seen.add(k)); };
  await settle(pg, 600);
  await take();
  // every fold, and every "+ Add" form
  const opened = await pg.evaluate(() => {
    let n = 0;
    document.querySelectorAll('main details:not([open]), .screen details:not([open])').forEach((d) => { d.open = true; n++; });
    document.querySelectorAll('.screen .addopen').forEach((b) => { b.click(); n++; });
    return n;
  });
  if (opened) { await settle(pg, 500); await take(); }
  // each row's More, one at a time (only one is open at once)
  const rows = await pg.evaluate(() => document.querySelectorAll('.screen .morebtn').length);
  for (let i = 0; i < rows; i++) {
    const ok = await pg.evaluate((n) => { const b = document.querySelectorAll('.screen .morebtn')[n]; if (b && b.getAttribute('aria-expanded') !== 'true') b.click(); return !!b; }, i);
    if (!ok) break;
    await settle(pg, 250);
    await take();
  }
  return Array.from(seen);
}

// The state both lists are taken in. t: as in tests/ui/signal-pages.js ({ page, base, info, notes, contexts, plain }).
async function prepare(t) {
  const signal = require('./signal-pages.js');
  const call = (url, body) => t.page.evaluate(async ([u, b]) => {
    const r = await fetch(u, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-PVJ-Request': '1' }, body: JSON.stringify(b || {}) });
    return r.json().catch(() => ({}));
  }, [url, body]);
  // the two clips the harness makes with a real encoder, where the machine has one: the same names without it
  await t.page.evaluate(async (names) => {
    const have = ((await (await fetch('/api/media')).json()).files) || [];
    for (const n of names) {
      if (have.indexOf(n) >= 0) continue;
      await fetch('/api/media/upload?name=' + encodeURIComponent(n), { method: 'POST', credentials: 'same-origin', headers: { 'X-PVJ-Request': '1', 'Content-Type': 'application/octet-stream' }, body: new Uint8Array(200000).fill(7) });
    }
  }, ['intro.mkv', 'tunnel.mkv']);
  await signal.setUp(t);
  // what setUp asked for is looked at, not assumed (a refused request is silent there)
  const off = [];
  for (const [name, url] of LISTENERS) {
    const asked = await call(url, { enabled: true });
    const now = await t.page.evaluate((u) => fetch(u, { credentials: 'same-origin' }).then((r) => r.json()).catch(() => ({})), url);
    if (now.enabled !== true || now.error) off.push(name + ' is not on (' + (asked.error || now.error || 'the box gave no reason') + ')');
  }
  if (off.length) throw new Error('the inventory cannot be taken, the box is not in the state the list was made in: ' + off.join('; ') + '. Its page would show "Switch on" in place of its card. If another box of the harness is running, switch DMX and OSC off there first: they listen on the same UDP ports.');
  const me = ((await t.page.evaluate(() => fetch('/api/status', { credentials: 'same-origin' }).then((r) => r.json()).catch(() => ({})))).device || {}).name;
  if (me !== OWNER) throw new Error('the inventory cannot be taken: the owner\'s device is called "' + me + '" and the list was made with one called "' + OWNER + '" (pair with inventory.OWNER); its row on People and codes would read as a control lost.');
  await call('/api/vibes', { on: false });
  await call('/api/effects', { off: true });
  await call('/api/control', { action: 'stop' });
  return {
    presenter: (await call('/api/devices/invite', { name: 'Inventory presenter', role: 'live' })).token,
    guest: (await call('/api/devices/invite', { name: 'Inventory guest', role: 'view' })).token,
  };
}

function note(found, where, keys) { keys.forEach((k) => { (found[k] = found[k] || []); if (found[k].indexOf(where) < 0) found[k].push(where); }); }

// ---- the panel as it is now: the Workspace shell ----
// Every screen is in the side menu (which is in the page at every width, shown or not): each item is pressed in turn.
// The Setup index lists pages that the menu does not (a page whose module is off): those rows are opened as well.
async function walk(pg) {
  const found = {};
  await pg.waitForFunction(() => !!document.querySelector('#wsside [data-go]'), null, { timeout: 15000 });
  const items = await pg.evaluate(() => Array.prototype.map.call(document.querySelectorAll('#wsside [data-go]'), (b) => [b.getAttribute('data-go'), b.textContent.trim()]));
  const at = async (go) => pg.waitForFunction((g) => { const m = document.querySelector('main.ws'); return m && m.getAttribute('data-screen') === g; }, go, { timeout: 15000 });
  for (const [go, name] of items) {
    await pg.evaluate((g) => { document.querySelector('#wsside [data-go="' + g + '"]').click(); }, go);
    await at(go);
    note(found, go + ' (' + name + ')', await gather(pg));
  }
  // the strip is on every screen: taken once more with its More open, as a phone has it
  await pg.evaluate(() => { const m = document.getElementById('wsmore'); if (m && m.getAttribute('aria-expanded') !== 'true') m.click(); });
  note(found, 'the transport strip', await pg.evaluate(collect));
  const index = async () => { await pg.evaluate(() => { document.querySelector('#wsside [data-go="setup/index"]').click(); }); await at('setup/index'); await pg.waitForFunction(() => !!document.querySelector('.navrow'), null, { timeout: 15000 }); };
  await index();
  const rows = await pg.evaluate(() => Array.prototype.map.call(document.querySelectorAll('.navrow'), (b) => [b.id, b.querySelector('.navname').textContent]));
  const menu = items.map((x) => x[1]);
  for (const [id, name] of rows) {
    if (menu.indexOf(name) >= 0) continue;
    await index();
    await pg.evaluate((i) => { document.getElementById(i).click(); }, id);
    await pg.waitForFunction((n) => { const h1 = document.querySelector('#syspage h1'); return h1 && h1.textContent === n; }, name, { timeout: 15000 });
    note(found, 'Setup index > ' + name, await gather(pg));
  }
  return found;
}

// ---- the panel before D65: five tabs, and System with a page per row ----
async function walkBefore(pg) {
  const found = {};
  await pg.waitForFunction(() => !!document.querySelector('nav.tabs button'), null, { timeout: 15000 });
  const tabs = await pg.evaluate(() => Array.prototype.map.call(document.querySelectorAll('nav.tabs button'), (b) => b.textContent.trim()));
  const tab = async (name) => {
    await pg.evaluate(() => { const old = document.querySelector('.shell > .screen'); if (old) old.setAttribute('data-before-tap', ''); });
    await pg.evaluate((n) => { Array.prototype.filter.call(document.querySelectorAll('nav.tabs button'), (b) => b.textContent.trim() === n)[0].click(); }, name);
    await pg.waitForFunction(() => { const s = document.querySelector('.shell > .screen'); return s && !s.hasAttribute('data-before-tap'); }, null, { timeout: 15000 });
  };
  for (const name of tabs) {
    await tab(name);
    note(found, name, await gather(pg));
  }
  await tab('System');
  await pg.waitForFunction(() => !!document.querySelector('.navrow'), null, { timeout: 15000 });
  const rows = await pg.evaluate(() => Array.prototype.map.call(document.querySelectorAll('.navrow'), (b) => [b.id, b.querySelector('.navname').textContent]));
  for (const [id, name] of rows) {
    await tab('System');
    await pg.waitForFunction((i) => !!document.getElementById(i), id, { timeout: 15000 });
    await pg.evaluate((i) => { document.getElementById(i).click(); }, id);
    await pg.waitForFunction((n) => { const h1 = document.querySelector('#syspage h1'); return h1 && h1.textContent === n; }, name, { timeout: 15000 });
    note(found, 'System > ' + name, await gather(pg));
  }
  return found;
}

// What was there before and is not now. `moved` is { key: reason }: keys that are gone on purpose.
function compare(before, after, moved) {
  const lost = [];
  Object.keys(before).sort().forEach((k) => { if (!after[k] && !(moved && moved[k])) lost.push(k + '  (was on: ' + before[k].join(', ') + ')'); });
  return lost;
}

module.exports = { collect, gather, prepare, walk, walkBefore, compare, OWNER, LISTENERS };
