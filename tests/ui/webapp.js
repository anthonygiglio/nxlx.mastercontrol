// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// The panel saved as an app (D76, docs/WEBAPP.md, pvj/web/webapp.js).
//
//   unit()      no browser: pvj/web/webapp.js is run in a bare context and asked about every device, with the
//               User-Agent texts those browsers send. `node tests/ui/webapp.js` runs it alone (tests/test_webapp.py
//               does, where there is a node); the browser test runs it first.
//   check(o)    in Chromium against the harness, on pages that say they are an iPhone, an Android phone and so on
//               (the User-Agent of the context; "runs as an app" is what matchMedia and navigator.standalone are
//               made to answer). o.browser, o.base, o.page: a page paired with full access.
//
// What is held: each device gets its own steps in its browser's own words, and one line of what does not work
// there; the card fits at 320 and 390 px in every look; a page that runs as an app says so, offers to load the
// panel again, keeps its screen over a reload, and on the connect screen says why it asks to pair again; a page in
// a browser tab starts on Live after a reload as before; the colour of the browser's bar follows the look.
//
// Chromium only, and a User-Agent text is not the device: nothing here says what a real iPhone or Android phone does.
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const UA = {
  iphoneSafari: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1',
  iphoneChrome: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/138.0.7204.156 Mobile/15E148 Safari/604.1',
  iphoneFirefox: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) FxiOS/141.0 Mobile/15E148 Safari/605.1.15',
  ipadSafari: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Safari/605.1.15',       // an iPad says it is a Mac
  ipadOld: 'Mozilla/5.0 (iPad; CPU OS 12_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/12.1 Mobile/15E148 Safari/604.1',
  androidChrome: 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Mobile Safari/537.36',
  androidSamsung: 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/28.0 Chrome/130.0.0.0 Mobile Safari/537.36',
  androidFirefox: 'Mozilla/5.0 (Android 15; Mobile; rv:141.0) Gecko/141.0 Firefox/141.0',
  androidEdge: 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Mobile Safari/537.36 EdgA/138.0.0.0',
  macSafari: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Safari/605.1.15',
  macChrome: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
  macFirefox: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:141.0) Gecko/20100101 Firefox/141.0',
  winChrome: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
  winEdge: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36 Edg/138.0.0.0',
  winFirefox: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:141.0) Gecko/20100101 Firefox/141.0',
  linuxChrome: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
  linuxFirefox: 'Mozilla/5.0 (X11; Linux x86_64; rv:141.0) Gecko/20100101 Firefox/141.0',
  chromebook: 'Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
  winOpera: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36 OPR/122.0.0.0',
};
const AWAY = 'Away from the box\'s network the app cannot open: it shows an error page until this device is back on that network.';
const HTTP = 'the box is plain HTTP on your own network, not HTTPS';
const OWN = (b) => 'Open the panel from its new icon and pair it once there. The app keeps its own sign-in, apart from ' + b + '.';
const CHROME = ['Click the three dots at the top right.', 'Choose Cast, save, and share, then Install page as app...', 'Click Install.'];
const CHROME_CANNOT = 'Chrome\'s Install button in the address bar does not appear for this box: it needs HTTPS, and ' + HTTP + '. The menu does the same. ' + AWAY;
const ANDROID = (b) => 'On Android the panel does not get a window of its own: the icon opens it in ' + b + ', with the bar of ' + b + '. An Android browser only makes a real app from a page served over HTTPS, and ' + HTTP + '.';
// [what the browser reports, device, browser, where, steps, cannot]
const CASES = [
  [{ ua: UA.iphoneSafari, touch: 5 }, 'iphone', 'safari', 'This panel is open in Safari on an iPhone.',
    ['Tap the Share button: a square with an arrow pointing up.', 'Scroll down and tap Add to Home Screen.', 'If there is a switch Open as Web App, leave it on. Tap Add.', OWN('Safari')], AWAY],
  [{ ua: UA.iphoneChrome, touch: 5 }, 'iphone', 'chrome', 'This panel is open in Chrome on an iPhone.',
    ['Open the Share menu of Chrome.', 'Tap Add to Home Screen. If there is no such entry, open the panel in Safari and do it there.', 'Tap Add.', OWN('Chrome')], AWAY],
  [{ ua: UA.iphoneFirefox, touch: 5 }, 'iphone', 'firefox', 'This panel is open in Firefox on an iPhone.',
    ['Open the Share menu of Firefox.', 'Tap Add to Home Screen. If there is no such entry, open the panel in Safari and do it there.', 'Tap Add.', OWN('Firefox')], AWAY],
  [{ ua: UA.ipadSafari, touch: 5 }, 'ipad', 'safari', 'This panel is open in Safari on an iPad.',
    ['Tap the Share button: a square with an arrow pointing up.', 'Scroll down and tap Add to Home Screen.', 'If there is a switch Open as Web App, leave it on. Tap Add.', OWN('Safari')], AWAY],
  [{ ua: UA.ipadOld, touch: 5 }, 'ipad', 'safari', 'This panel is open in Safari on an iPad.', null, AWAY],
  [{ ua: UA.androidChrome, touch: 5 }, 'android', 'chrome', 'This panel is open in Chrome on an Android device.',
    ['Tap the three dots at the top right.', 'Tap Add to Home screen.', 'Tap Add.'], ANDROID('Chrome')],
  [{ ua: UA.androidSamsung, touch: 5 }, 'android', 'samsung', 'This panel is open in Samsung Internet on an Android device.',
    ['Tap the menu: three lines at the bottom right.', 'Tap Add page to, then Home screen.', 'Tap Add.'], ANDROID('Samsung Internet')],
  [{ ua: UA.androidFirefox, touch: 5 }, 'android', 'firefox', 'This panel is open in Firefox on an Android device.',
    ['Open the menu of Firefox: three dots.', 'Tap Add to Home screen.', 'Tap Add.'],
    'On Android the panel does not get a window of its own: the icon opens it in Firefox, with the bar of Firefox. Firefox on Android does not make apps from sites.'],
  [{ ua: UA.androidEdge, touch: 5 }, 'android', 'edge', 'This panel is open in Edge on an Android device.',
    ['Open the menu of Edge: three dots.', 'Tap Add to Home screen.', 'Tap Add.'], ANDROID('Edge')],
  [{ ua: UA.macSafari, touch: 0 }, 'mac', 'safari', 'This panel is open in Safari on a Mac.',
    ['In the menu bar choose File, then Add to Dock. Or click the Share button, then Add to Dock.', 'Type a name and click Add.', OWN('Safari')], AWAY],
  [{ ua: UA.macChrome, touch: 0 }, 'mac', 'chrome', 'This panel is open in Chrome on a Mac.', CHROME, CHROME_CANNOT],
  [{ ua: UA.macFirefox, touch: 0 }, 'mac', 'firefox', 'This panel is open in Firefox on a Mac.',
    ['Bookmark this page.', 'Or open the panel in Safari, Chrome or Edge and save it there.'], 'Firefox on this computer cannot save a site as an app.'],
  [{ ua: UA.winChrome, touch: 0 }, 'windows', 'chrome', 'This panel is open in Chrome on a Windows computer.', CHROME, CHROME_CANNOT],
  [{ ua: UA.winChrome, touch: 10 }, 'windows', 'chrome', 'This panel is open in Chrome on a Windows computer.', CHROME, CHROME_CANNOT],      // a touch screen laptop is not an iPad
  [{ ua: UA.winEdge, touch: 0 }, 'windows', 'edge', 'This panel is open in Edge on a Windows computer.',
    ['Click the three dots at the top right: Settings and more.', 'Choose More tools, then Apps, then Install this site as an app.', 'Click Install.'],
    'Edge\'s App available button in the address bar does not appear for this box: it needs HTTPS, and ' + HTTP + '. The menu does the same. ' + AWAY],
  [{ ua: UA.winFirefox, touch: 0 }, 'windows', 'firefox', 'This panel is open in Firefox on a Windows computer.',
    ['If Firefox shows a web apps button at the right of the address bar, click it: the panel goes to the taskbar.', 'If there is no such button, open the panel in Edge or Chrome and save it there, or bookmark this page.'], AWAY],
  [{ ua: UA.linuxChrome, touch: 0 }, 'linux', 'chrome', 'This panel is open in Chrome on a Linux computer.', CHROME, CHROME_CANNOT],
  [{ ua: UA.linuxFirefox, touch: 0 }, 'linux', 'firefox', 'This panel is open in Firefox on a Linux computer.',
    ['Bookmark this page.', 'Or open the panel in Chrome or Edge and save it there.'], 'Firefox on this computer cannot save a site as an app.'],
  [{ ua: UA.chromebook, touch: 0 }, 'chromebook', 'chrome', 'This panel is open in Chrome on a Chromebook.', CHROME, CHROME_CANNOT],
  [{ ua: UA.winOpera, touch: 0 }, 'windows', 'other', 'This panel is open in a browser on a Windows computer.',
    ['Look in this browser\'s menu for Add to Home screen, Install, or Add to Dock.', 'If there is none, bookmark this page.'], AWAY],
  [{ ua: '', touch: 0 }, 'other', 'other', 'This panel is open in a browser on this device.',
    ['Look in this browser\'s menu for Add to Home screen, Install, or Add to Dock.', 'If there is none, bookmark this page.'], AWAY],
];

// pvj/web/webapp.js in a context that has only what the file touches when it is read
function load(env) {
  const listeners = {};
  const meta = { content: '#121214', getAttribute() { return this.content; }, setAttribute(_k, v) { this.content = v; } };
  const ctx = {
    navigator: { userAgent: env.ua || '', maxTouchPoints: env.touch || 0, standalone: env.standalone },
    document: { addEventListener(n, f) { listeners['doc ' + n] = f; }, getElementById() { return null; }, querySelector(sel) { return /theme-color/.test(sel) ? meta : null; }, documentElement: {} },
    getComputedStyle() { return { getPropertyValue() { return ' ' + (env.bg || '') + ' '; } }; },
    location: { reload() { ctx.reloaded = true; } },
  };
  ctx.window = ctx;
  ctx.addEventListener = (n, f) => { listeners['win ' + n] = f; };
  ctx.matchMedia = (q) => ({ matches: !!env.mode && q === '(display-mode: ' + env.mode + ')' });
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '..', '..', 'pvj', 'web', 'webapp.js'), 'utf8'), ctx);
  // (what comes out of that context has its own Object and Array: taken over as plain data, so it can be compared)
  const made = ctx.pvjWebapp, plain = (x) => JSON.parse(JSON.stringify(x));
  const w = Object.assign({}, made, { where: (e) => plain(made.where(e)), here: () => plain(made.here()), guide: (x) => plain(made.guide(x)) });
  return { w, listeners, meta, ctx };
}
// a stand-in for app.js's h(): a tree of plain objects
function h(tag, attrs) {
  const el = { tag, attrs: attrs || {}, kids: [], appendChild(k) { this.kids.push(k); return k; } };
  for (let i = 2; i < arguments.length; i++) [].concat(arguments[i]).forEach((k) => { if (k) el.kids.push(k); });
  return el;
}
const find = (el, id) => (el.attrs.id === id ? el : el.kids.map((k) => (k && k.kids ? find(k, id) : null)).filter(Boolean)[0] || null);
const texts = (el) => [el.attrs.text].concat(el.kids.map((k) => (k && k.kids ? texts(k) : []))).flat().filter(Boolean);

function unit() {
  const { w } = load({});
  assert.deepStrictEqual(Object.keys(w).sort(), ['card', 'colour', 'guide', 'here', 'pairNote', 'where']);
  for (const [env, device, browser, where, steps, cannot] of CASES) {
    const name = device + '/' + browser + ' ' + env.ua.slice(-30);
    const got = w.where(env);
    assert.deepStrictEqual(got, { device, browser, app: false }, name);
    const g = w.guide(got);
    assert.strictEqual(g.where, where, name);
    if (steps) assert.deepStrictEqual(g.steps, steps, name);
    assert.strictEqual(g.cannot, cannot, name);
    assert(g.steps.length >= 2 && g.steps.length <= 4, name + ': two to four steps');
    assert.strictEqual(g.window, !(device === 'android' || (browser === 'firefox' && (device === 'mac' || device === 'linux'))), name + ': whether it gets a window of its own');
    const words = [g.where, g.cannot].concat(g.steps).join(' ');
    assert(!/—|–/.test(words), name + ': no dashes');
    assert(!/\b(works|will work|guaranteed)\b/i.test(words.replace(/does not work/g, '')), name + ': the words promise nothing about a device nobody tried');
    // the same device, and the page already runs as an app: said either way a browser can say it
    assert.strictEqual(w.where(Object.assign({}, env, { mode: true })).app, true, name);
    assert.strictEqual(w.where(Object.assign({}, env, { standalone: true })).app, true, name);
    assert.strictEqual(w.where(Object.assign({}, env, { standalone: false })).app, false, name);
  }
  assert.deepStrictEqual(w.where(undefined), { device: 'other', browser: 'other', app: false });

  // here(): from what this browser reports. A Mac with a touch screen is an iPad; each display mode of an app counts.
  assert.deepStrictEqual(load({ ua: UA.ipadSafari, touch: 5 }).w.here(), { device: 'ipad', browser: 'safari', app: false });
  assert.deepStrictEqual(load({ ua: UA.macSafari, touch: 0 }).w.here(), { device: 'mac', browser: 'safari', app: false });
  assert.deepStrictEqual(load({ ua: UA.iphoneSafari, touch: 5, standalone: true }).w.here(), { device: 'iphone', browser: 'safari', app: true });
  for (const mode of ['standalone', 'fullscreen', 'minimal-ui']) assert.strictEqual(load({ ua: UA.winEdge, mode }).w.here().app, true, mode);
  assert.strictEqual(load({ ua: UA.winEdge, mode: 'browser' }).w.here().app, false);

  // the card, in a tab: where, the steps, what does not work; no Install button unless the browser offered one
  let p = load({ ua: UA.androidChrome, touch: 5 });
  let card = p.w.card({ h });
  assert.deepStrictEqual([card.attrs.id, card.attrs['data-device'], card.attrs['data-browser'], card.attrs['data-app']], ['appcard', 'android', 'chrome', '0']);
  assert.deepStrictEqual(texts(card), ['Save this panel as an app', 'This panel is open in Chrome on an Android device. It can have its own icon on the home screen.',
    'Tap the three dots at the top right.', 'Tap Add to Home screen.', 'Tap Add.', ANDROID('Chrome')]);
  assert.strictEqual(find(card, 'appinstall'), null);
  assert.strictEqual(p.w.pairNote(h), null, 'in a tab the connect screen says nothing more');
  p = load({ ua: UA.macSafari });
  assert.strictEqual(texts(p.w.card({ h }))[1], 'This panel is open in Safari on a Mac. Saved as an app it gets its own icon and its own window.');

  // the browser's own question (only ever over HTTPS): the button asks it, once
  p = load({ ua: UA.winChrome });
  let prevented = 0, prompted = 0;
  p.listeners['win beforeinstallprompt']({ preventDefault() { prevented += 1; }, prompt() { prompted += 1; } });
  card = p.w.card({ h });
  assert.strictEqual(prevented, 1, 'the browser shows nothing by itself');
  assert.strictEqual(find(card, 'appinstall').attrs.text, 'Install');
  assert.deepStrictEqual(texts(card).slice(2, 4), ['Install', 'Or by hand']);
  find(card, 'appinstall').attrs.onclick();
  assert.strictEqual(prompted, 1);
  assert.strictEqual(find(p.w.card({ h }), 'appinstall'), null, 'an offer is used once');
  p.listeners['win appinstalled']();
  assert.strictEqual(find(p.w.card({ h }), 'appdone').attrs.text, 'Saved. Open the panel from its new icon.');

  // as an app: says so, offers to load the panel again, and the connect screen says why it asks
  p = load({ ua: UA.iphoneSafari, touch: 5, standalone: true });
  card = p.w.card({ h });
  assert.strictEqual(card.attrs['data-app'], '1');
  assert.deepStrictEqual(texts(card), ['This is the app', 'This panel is open as an app on an iPhone. Nothing more to do here.',
    AWAY + ' That needs HTTPS, and ' + HTTP + '.', 'Load the panel again']);
  find(card, 'appreload').attrs.onclick();
  assert.strictEqual(p.ctx.reloaded, true);
  const note = p.w.pairNote(h);
  assert.deepStrictEqual([note.attrs.id, note.attrs.text], ['appnote', 'This app keeps its own sign-in, apart from the browser it was saved from, so it asks once more. ' +
    'Type the code or the PIN shown on the box\'s screen. (A QR code scanned with the camera opens the browser, not this app.)']);

  // the bar's colour: taken from the look's style sheet when it arrives, only if it is a colour, only for that sheet
  p = load({ bg: '#f4f3ef' });
  const loaded = p.listeners['doc load'];
  loaded({ target: { tagName: 'LINK', getAttribute: () => '/app.css' } });
  assert.strictEqual(p.meta.content, '#121214');
  loaded({ target: { tagName: 'SCRIPT', getAttribute: () => '/theme.css' } });
  assert.strictEqual(p.meta.content, '#121214');
  loaded({ target: { tagName: 'LINK', getAttribute: () => '/theme.css?v=7' } });
  assert.strictEqual(p.meta.content, '#f4f3ef');
  p = load({ bg: 'url(x)' });
  p.w.colour();
  assert.strictEqual(p.meta.content, '#121214');
  return CASES.length;
}

/* eslint-disable no-undef */
// What is wrong with the card's place in the window, as a list; empty when it fits.
function fit() {
  const out = [], card = document.getElementById('appcard') || document.getElementById('appnote');
  if (!card) return ['there is no card'];
  if (document.documentElement.scrollWidth > window.innerWidth + 1) out.push('the page is ' + document.documentElement.scrollWidth + ' px wide in a window of ' + window.innerWidth);
  const box = card.getBoundingClientRect();
  if (box.left < 0 || box.right > window.innerWidth + 1) out.push('the card leaves the window');
  [card].concat(Array.from(card.querySelectorAll('*'))).forEach((el) => {
    const r = el.getBoundingClientRect();
    const what = (el.id ? '#' + el.id : el.tagName.toLowerCase()) + ' "' + el.textContent.slice(0, 24) + '"';
    if (r.width === 0 || r.height === 0) out.push(what + ' has no size');
    if (r.left < box.left - 1 || r.right > box.right + 1) out.push(what + ' leaves the card');
    if (el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).display !== 'inline') out.push(what + ' has words that run out of it');
    if (el.tagName === 'BUTTON' && r.height < 44) out.push(what + ' is ' + r.height + ' px high');
  });
  return out;
}
const seen = () => ({
  device: document.getElementById('appcard').getAttribute('data-device'), browser: document.getElementById('appcard').getAttribute('data-browser'),
  app: document.getElementById('appcard').getAttribute('data-app'), head: document.querySelector('#appcard h2').textContent,
  where: document.getElementById('appwhere').textContent, steps: Array.from(document.querySelectorAll('#appsteps li')).map((x) => x.textContent),
  cannot: document.getElementById('appcannot').textContent, install: !!document.getElementById('appinstall'), reload: !!document.getElementById('appreload'),
});
// before any script of the page: this page "runs as an app", said both ways a browser says it
function asApp() {
  const real = window.matchMedia.bind(window);
  window.matchMedia = (q) => (q === '(display-mode: standalone)' ? { matches: true, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} } : real(q));
  Object.defineProperty(Navigator.prototype, 'standalone', { get() { return true; }, configurable: true });
}
/* eslint-enable no-undef */

const LOOKS = ['dark-stage', 'light', 'high-contrast', 'night-red', 'signal', 'signal-light'];

async function check(o) {
  const { browser, base, page } = o;
  const cookies = await page.context().cookies();
  const post = (pg, p, b) => pg.evaluate(([u, body]) => fetch(u, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-PVJ-Request': '1' }, body: JSON.stringify(body) }).then((r) => r.status), [p, b]);
  const opened = [];
  const open = async (ua, width, app, paired) => {
    const c = await browser.newContext({ viewport: { width, height: 700 }, userAgent: ua, hasTouch: /Mobile|Android|iPhone/.test(ua) });
    opened.push(c);
    if (app) await c.addInitScript(asApp);
    if (paired !== false) await c.addCookies(cookies);
    const pg = await c.newPage();
    pg.on('console', (m) => { if (m.type() === 'error' && !/status of 401/.test(m.text())) o.problems.push('saved as an app: ' + m.text()); });
    pg.on('pageerror', (e) => o.problems.push('saved as an app, pageerror: ' + e.message));
    await pg.goto(base + '/');
    return pg;
  };
  const toCard = async (pg) => {
    await pg.click('nav >> text=System');
    await pg.click('.navrow:has(.navname:text-is("Save as an app"))');
    await pg.waitForSelector('#appcard');
  };
  try {
    // each device its own steps, at 320 and at 390 px
    const devices = [[UA.iphoneSafari, 'iphone', 'safari'], [UA.androidChrome, 'android', 'chrome'], [UA.androidSamsung, 'android', 'samsung'], [UA.macSafari, 'mac', 'safari'],
      [UA.winEdge, 'windows', 'edge'], [UA.winFirefox, 'windows', 'firefox'], [UA.chromebook, 'chromebook', 'chrome']];
    for (const [ua, device, name] of devices) {
      const want = CASES.filter((c) => c[0].ua === ua && c[1] === device)[0];
      for (const width of [320, 390]) {
        const pg = await open(ua, width);
        await toCard(pg);
        const got = await pg.evaluate(seen);
        assert.deepStrictEqual([got.device, got.browser, got.app, got.head], [device, name, '0', 'Save this panel as an app'], device + ' ' + name);
        assert.deepStrictEqual(got.steps, want[4], device + ' ' + name + ': its steps');
        assert.strictEqual(got.cannot, want[5], device + ' ' + name + ': what does not work there');
        assert(got.where.startsWith(want[3] + ' '), device + ' ' + name + ': ' + got.where);
        assert.deepStrictEqual([got.install, got.reload], [false, false], 'over plain HTTP the browser offers nothing itself, and a tab has its own reload');
        assert.deepStrictEqual(await pg.evaluate(fit), [], device + ' ' + name + ' at ' + width + ' px');
        assert.strictEqual(await pg.textContent('#sysblurb'), 'Put this panel on the home screen, the Dock or the taskbar of this device, so it opens like an app: its own icon, its own window. ' +
          'The steps are the ones each browser\'s maker gives; not yet tried on real phones and tablets.');
        await pg.context().close();
      }
    }

    // the longest card (an Android phone) at 320 px in every look, then the look the test had
    const before = (await page.evaluate(() => fetch('/api/theme').then((r) => r.json()))).theme;      // eslint-disable-line no-undef
    const narrow = await open(UA.androidChrome, 320);
    try {
      for (const look of LOOKS) {
        assert.strictEqual(await post(page, '/api/theme', { name: look, accent: null }), 200, look);
        await narrow.goto(base + '/');
        await toCard(narrow);
        assert.deepStrictEqual(await narrow.evaluate(fit), [], 'the card at 320 px in ' + look);
        // the colour of the browser's bar is the page colour of that look, written by the box
        const colours = await narrow.evaluate(() => [document.querySelector('meta[name="theme-color"]').content, getComputedStyle(document.documentElement).getPropertyValue('--bg').trim()]);   // eslint-disable-line no-undef
        assert.strictEqual(colours[0], colours[1], 'theme-color in ' + look);
      }
      // and when the look changes under an open page (its style sheet arrives again), the page sets it itself
      const was = await narrow.evaluate(() => document.querySelector('meta[name="theme-color"]').content);      // eslint-disable-line no-undef
      assert.strictEqual(await post(page, '/api/theme', { name: 'light', accent: null }), 200);
      await narrow.evaluate(() => document.querySelector('link[href^="/theme.css"]').setAttribute('href', '/theme.css?v=1'));      // eslint-disable-line no-undef
      await narrow.waitForFunction((old) => document.querySelector('meta[name="theme-color"]').content !== old, was, { timeout: 10000 });      // eslint-disable-line no-undef
      assert.strictEqual(await narrow.evaluate(() => document.querySelector('meta[name="theme-color"]').content), '#f4f3ef');      // eslint-disable-line no-undef
    } finally {
      await post(page, '/api/theme', { name: before.name, accent: before.accent || null });
    }

    // a page that runs as an app
    const app = await open(UA.iphoneSafari, 320, true);
    await toCard(app);
    let got = await app.evaluate(seen);
    assert.deepStrictEqual([got.app, got.head, got.where, got.reload, got.steps], ['1', 'This is the app', 'This panel is open as an app on an iPhone. Nothing more to do here.', true, []]);
    assert.deepStrictEqual(await app.evaluate(fit), [], 'the app\'s own card at 320 px');
    // a reload keeps the screen: the System page, then a tab
    await app.reload();
    await app.waitForSelector('#syspage[data-page="app"] #appcard');
    await Promise.all([app.waitForEvent('load'), app.click('#appreload')]);      // the card's own button does the same
    await app.waitForSelector('#syspage[data-page="app"] #appcard');
    await app.click('#sysback');                                     // and Back still leads to the index, from the page itself
    await app.waitForSelector('#sysindex');
    await app.reload();
    await app.waitForSelector('#sysindex');
    await app.click('nav >> text=Media');
    await app.waitForSelector('nav button[aria-current="page"]:text-is("Media")');
    await app.reload();
    await app.waitForSelector('nav button[aria-current="page"]:text-is("Media")');
    // the back gesture from a page of System leads to the index, never out of the app
    await toCard(app);
    await app.evaluate(() => history.back());      // eslint-disable-line no-undef
    await app.waitForSelector('#sysindex');

    // a browser tab is as it was: a reload starts on Live
    const tab = await open(UA.iphoneSafari, 390);
    await toCard(tab);
    await tab.reload();
    await tab.waitForSelector('nav button[aria-current="page"]:text-is("Live")');
    assert.strictEqual(await tab.locator('#appnote').count(), 0);

    // an app nobody has paired in yet: the connect screen says why it asks, and fits
    const unpaired = await open(UA.iphoneSafari, 320, true, false);
    await unpaired.waitForSelector('.pin');
    assert.strictEqual(await unpaired.textContent('#appnote'), 'This app keeps its own sign-in, apart from the browser it was saved from, so it asks once more. ' +
      'Type the code or the PIN shown on the box\'s screen. (A QR code scanned with the camera opens the browser, not this app.)');
    assert.deepStrictEqual(await unpaired.evaluate(fit), [], 'the note on the connect screen at 320 px');
    const plain = await open(UA.iphoneSafari, 320, false, false);
    await plain.waitForSelector('.pin');
    assert.strictEqual(await plain.locator('#appnote').count(), 0, 'in a browser tab the connect screen is as it was');

    // what the page's head asks the box for is answered, with the type a browser wants
    const answers = await page.evaluate(() => Promise.all(['/manifest.webmanifest', '/icon-192.png', '/icon-512.png', '/icon-maskable-512.png', '/icon-mono.svg', '/apple-touch-icon.png']      // eslint-disable-line no-undef
      .map((u) => fetch(u, { credentials: 'omit' }).then((r) => [u, r.status, r.headers.get('Content-Type')]))));      // eslint-disable-line no-undef
    assert.deepStrictEqual(answers, [['/manifest.webmanifest', 200, 'application/manifest+json'], ['/icon-192.png', 200, 'image/png'], ['/icon-512.png', 200, 'image/png'],
      ['/icon-maskable-512.png', 200, 'image/png'], ['/icon-mono.svg', 200, 'image/svg+xml'], ['/apple-touch-icon.png', 200, 'image/png']]);
    assert.strictEqual(await page.evaluate(() => document.querySelector('link[rel="manifest"]').href), base + '/manifest.webmanifest');      // eslint-disable-line no-undef
    assert.strictEqual(await page.evaluate(() => (navigator.serviceWorker ? navigator.serviceWorker.getRegistrations().then((r) => r.length) : 0)), 0, 'no service worker');      // eslint-disable-line no-undef
  } finally {
    for (const c of opened) await c.close().catch(() => {});
  }
}

module.exports = { unit, check, UA, CASES };
if (require.main === module) console.log('saved as an app: the words for ' + unit() + ' devices and browsers');
