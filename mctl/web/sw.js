// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// The panel's service worker (D151, docs/WEBAPP.md). It does one small thing: when the panel is opened from the home
// screen and the node cannot be reached (this phone is off the venue's network, or the node is off), it shows a plain
// page that says so, with a button to try again, instead of the browser's error page.
//
// What it never does:
//   * It touches only a page being opened (a "navigate" request) from a GET. Scripts, style sheets, pictures, fonts
//     and every /api/ call go straight to the node as if there were no worker: nothing of the panel is kept, so no
//     old panel can ever be shown after an update, and nothing per person (a status, a preview, a code, a list of
//     devices) is ever stored.
//   * It never keeps a page it fetched. The only things in its cache are the two files of the "away" page, put there
//     when the worker is installed.
//   * It does not run over plain http (a browser refuses it there, and the page does not ask).
//
// The node writes the name of its release in place of the word below, so a new release is a different file: the
// browser installs it, it takes over at once, and it deletes every cache that is not its own.
'use strict';

var RELEASE = '__RELEASE__';
var CACHE = 'nxlx-shell-' + RELEASE;
var SHELL = ['/away.html', '/away.css'];
var WAIT_MS = 10000;      // a node that does not answer in this long is as good as away; the page it was opening is dropped

self.addEventListener('install', function (event) {
  event.waitUntil(caches.open(CACHE).then(function (cache) { return cache.addAll(SHELL); }).then(function () { return self.skipWaiting(); }));
});

self.addEventListener('activate', function (event) {
  event.waitUntil(caches.keys().then(function (names) {
    return Promise.all(names.filter(function (n) { return n !== CACHE; }).map(function (n) { return caches.delete(n); }));
  }).then(function () { return self.clients.claim(); }));
});

function away() {
  return caches.open(CACHE).then(function (cache) { return cache.match('/away.html'); }).then(function (r) {
    return r || new Response('The node cannot be reached.', { status: 503, headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
  });
}

self.addEventListener('fetch', function (event) {
  var req = event.request, url = new URL(req.url);
  // only a page being opened, from this node, that is not the API (a download link opens as a navigation too)
  if (req.method !== 'GET' || req.mode !== 'navigate' || url.origin !== self.location.origin || url.pathname.indexOf('/api/') === 0) return;
  event.respondWith(new Promise(function (resolve) {
    var done = false;
    var timer = setTimeout(function () { if (!done) { done = true; away().then(resolve); } }, WAIT_MS);
    fetch(req).then(function (r) { if (!done) { done = true; clearTimeout(timer); resolve(r); } },
      function () { if (!done) { done = true; clearTimeout(timer); away().then(resolve); } });
  }));
});
