// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// Runs mctl/web/sw.js and mctl/web/webapp.js against stand-ins for the browser, and prints what happened as JSON.
// Called by tests/test_webapp.py (node only; nothing here needs a browser, a network or a node of ours).
//   node tests/webapp_unit.js <web folder> <release name>
'use strict';
const vm = require('vm');
const fs = require('fs');
const path = require('path');

const web = process.argv[2], release = process.argv[3];
const out = {};

// ---- the service worker --------------------------------------------------
function worker(fetchImpl) {
  const handlers = {}, store = {}, puts = [], deleted = [], fetched = [];
  const caches = {
    open: (name) => Promise.resolve({
      addAll: (urls) => { store[name] = (store[name] || []).concat(urls); return Promise.resolve(); },
      put: (u) => { puts.push([name, String(u)]); return Promise.resolve(); },
      match: (u) => Promise.resolve((store[name] || []).indexOf(u) >= 0 ? new Response('AWAY PAGE ' + u, { status: 200 }) : undefined),
    }),
    keys: () => Promise.resolve(Object.keys(store)),
    delete: (n) => { deleted.push(n); delete store[n]; return Promise.resolve(true); },
  };
  class Response { constructor(body, init) { this.body = body; this.status = (init && init.status) || 200; } }
  const self = { location: { origin: 'https://node.local' }, addEventListener: (t, f) => { handlers[t] = f; },
    skipWaiting: () => { self.skipped = true; return Promise.resolve(); }, clients: { claim: () => { self.claimed = true; return Promise.resolve(); } } };
  self.registration = { unregister: () => { self.unregistered = true; return Promise.resolve(true); } };
  const g = { self, caches, Response, URL, Promise, fetch: (req) => { const u = typeof req === 'string' ? req : req.url; fetched.push(u); return fetchImpl(req, u); } };
  const src = fs.readFileSync(path.join(web, 'sw.js'), 'utf8').split('__RELEASE__').join(release);
  vm.runInNewContext(src, g, { filename: 'sw.js' });
  return { handlers, store, puts, deleted, fetched, self };
}
async function install(w) { const ev = { waitUntil(p) { this.p = p; } }; w.handlers.install(ev); await ev.p; return w; }
async function nav(w, url, extra) {
  const ev = { request: Object.assign({ method: 'GET', mode: 'navigate', url }, extra || {}), waitUntil(p) { this.kept = p; }, respondWith(p) { this.answer = p; } };
  w.handlers.fetch(ev);
  const answer = ev.answer ? await ev.answer : null;
  if (ev.kept) await ev.kept;
  return answer;
}
(async function () {
  // install keeps two files and nothing else, and takes over at once
  let w = worker(() => Promise.resolve({ status: 200, body: 'LIVE' }));
  let ev = { waitUntil(p) { this.p = p; } };
  w.handlers.install(ev); await ev.p;
  out.installed = w.store;
  out.skipped = !!w.self.skipped;
  // activate deletes every other cache, and its own stays
  w.store['nxlx-shell-0.0.1-old'] = ['/x']; w.store['something-else'] = ['/y'];
  ev = { waitUntil(p) { this.p = p; } };
  w.handlers.activate(ev); await ev.p;
  out.kept = Object.keys(w.store);
  out.deleted = w.deleted.slice().sort();
  out.claimed = !!w.self.claimed;

  // the node answers: the page is passed through as it came, and nothing is stored
  const live = { status: 200, body: 'LIVE' };
  w = worker(() => Promise.resolve(live));
  out.liveSame = (await nav(w, 'https://node.local/')) === live;
  // the node cannot be reached: the away page
  w = worker(() => Promise.reject(new TypeError('network')));
  const notYet = await nav(w, 'https://node.local/');       // not installed yet: a plain note, not an error page of the browser
  out.noCache = notYet && [notYet.status, notYet.body];
  await install(w);
  const gone = await nav(w, 'https://node.local/');
  out.awayBody = gone && gone.body;
  // never touched: an API address, a POST, a script or a picture, another site
  w = worker(() => Promise.resolve(live));
  const cases = {
    api: ['https://node.local/api/status', {}],
    apiNested: ['https://node.local/api/devices?x=1', {}],
    post: ['https://node.local/', { method: 'POST' }],
    script: ['https://node.local/app.js', { mode: 'no-cors' }],
    fetchApi: ['https://node.local/api/status', { mode: 'cors' }],
    other: ['https://example.org/', {}],
    head: ['https://node.local/', { method: 'HEAD' }],
  };
  out.untouched = {};
  for (const k of Object.keys(cases)) {
    const e = { request: Object.assign({ method: 'GET', mode: 'navigate', url: cases[k][0] }, cases[k][1]), respondWith() { this.called = true; } };
    w.handlers.fetch(e);
    out.untouched[k] = !e.called;
  }
  out.puts = w.puts.length;
  // a slow node is waited for: the real page wins, the away page is never shown for slowness
  w = await install(worker(() => new Promise((resolve) => setTimeout(() => resolve(live), 150))));
  const slow = await nav(w, 'https://node.local/');
  out.slowSame = slow === live;
  // the away page's own style sheet: from the node when it answers, from the cache when it does not
  w = await install(worker(() => Promise.resolve(live)));
  let e2 = { request: { method: 'GET', mode: 'no-cors', url: 'https://node.local/away.css' }, respondWith(p) { this.answer = p; } };
  w.handlers.fetch(e2);
  out.shellOnline = (await e2.answer) === live;
  w = await install(worker(() => Promise.reject(new TypeError('network'))));
  e2 = { request: { method: 'GET', mode: 'no-cors', url: 'https://node.local/away.css' }, respondWith(p) { this.answer = p; } };
  w.handlers.fetch(e2);
  out.shellAway = (await e2.answer).body;
  e2 = { request: { method: 'GET', mode: 'no-cors', url: 'https://node.local/away.html' }, respondWith(p) { this.answer = p; } };
  w.handlers.fetch(e2);
  out.shellAwayHtml = (await e2.answer).body;
  // a worker the node no longer has removes itself and its caches; any other answer leaves it
  const answers = { gone404: 404, gone410: 410, ok: 200, error500: 500 };
  out.selfRemoval = {};
  for (const k of Object.keys(answers)) {
    w = await install(worker((req, u) => Promise.resolve(u === '/sw.js' ? { status: answers[k] } : live)));
    w.store['nxlx-shell-0.0.1-old'] = ['/x']; w.store['something-else'] = ['/y'];
    ev = { waitUntil(p) { this.p = p; } };
    w.handlers.activate(ev); await ev.p;
    out.selfRemoval[k] = { unregistered: !!w.self.unregistered, caches: Object.keys(w.store).sort() };
  }
  w = await install(worker((req, u) => Promise.resolve(u === '/sw.js' ? { status: 404 } : live)));
  await nav(w, 'https://node.local/');                       // found out when a page is opened, too
  out.removedOnNavigation = !!w.self.unregistered;
  w = await install(worker((req, u) => u === '/sw.js' ? Promise.reject(new TypeError('network')) : Promise.resolve(live)));
  await nav(w, 'https://node.local/');
  out.networkErrorLeavesIt = !w.self.unregistered;

  // ---- webapp.js -----------------------------------------------------------
  function page(protocol, hasWorker) {
    const calls = [], listeners = {};
    const g = { navigator: { userAgent: '', maxTouchPoints: 0 }, location: { protocol }, document: { addEventListener() {}, getElementById() { return null; }, querySelector() { return null; } },
      getComputedStyle: () => ({ getPropertyValue: () => '' }), matchMedia: () => ({ matches: false }) };
    g.window = g;
    g.addEventListener = (t, f) => { listeners[t] = f; };
    if (hasWorker) g.navigator.serviceWorker = { register: (u, o) => { calls.push([u, o]); return Promise.resolve(); } };
    vm.runInNewContext(fs.readFileSync(path.join(web, 'webapp.js'), 'utf8'), g, { filename: 'webapp.js' });
    return { g, calls, listeners };
  }
  const http = page('http:', true), https = page('https:', true), none = page('https:', false);
  http.listeners.load && http.listeners.load();
  https.listeners.load && https.listeners.load();
  none.listeners.load && none.listeners.load();
  http.g.mctlWebapp.register();
  out.httpCalls = http.calls.length;
  out.httpsCalls = https.calls;
  out.noWorkerSupport = none.calls.length;

  const UA = {
    iphone: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1',
    ipad: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15',
    androidChrome: 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36',
    silk: 'Mozilla/5.0 (Linux; Android 9; KFMAWI) AppleWebKit/537.36 (KHTML, like Gecko) Silk/130.4.1 like Chrome/130.0.0.0 Safari/537.36',
    silkDesktop: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Silk/130.4.1 like Chrome/130.0.0.0 Safari/537.36',
    winChrome: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
  };
  const W = http.g.mctlWebapp;
  out.where = {};
  for (const k of Object.keys(UA)) out.where[k] = W.where({ ua: UA[k], touch: k === 'ipad' ? 5 : 0, secure: false });
  out.appMode = W.where({ ua: UA.iphone, standalone: true }).app;
  out.guide = {};
  for (const k of ['iphone', 'androidChrome', 'silk', 'winChrome']) {
    out.guide[k] = { http: W.guide(W.where({ ua: UA[k], secure: false })), https: W.guide(W.where({ ua: UA[k], secure: true })) };
  }
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
