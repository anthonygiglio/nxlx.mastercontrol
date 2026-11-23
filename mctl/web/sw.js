// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// The panel's service worker (D151, docs/WEBAPP.md). It does one small thing: when the panel is opened from the home
// screen and the node cannot be reached (this phone is off the venue's network, or the node is off), it shows a plain
// page that says so, with a button to try again, instead of the browser's error page.
//
// What it never does:
//   * It touches only a page being opened (a "navigate" request) and the two files of the away page, from a GET to
//     this node. Scripts, style sheets, pictures, fonts and every /api/ call go straight to the node as if there were
//     no worker: nothing of the panel is kept, so no old panel can ever be shown after an update, and nothing per
//     person (a status, a preview, a code, a list of devices) is ever stored.
//   * It never keeps a page it fetched. The only things in its cache are the two files of the "away" page, put there
//     when the worker is installed.
//   * It never decides that a slow node is away. The away page is shown only when the browser reports that the
//     request failed; a node that answers late (a loaded Pi, a phone that has just rejoined Wi-Fi) is waited for.
//   * It does not run over plain http (a browser refuses it there, and the page does not ask).
//
// The node writes the name of its release in place of the word below, so a new release is a different file: the
// browser installs it, it takes over at once, and it deletes every cache that is not its own. A node that no longer
// has this file (answers 404 or 410 for /sw.js) makes the worker remove itself and its caches.
'use strict';

var RELEASE = '__RELEASE__';
var CACHE = 'nxlx-shell-' + RELEASE;
var SHELL = ['/away.html', '/away.css'];

function cached(path) {
  return caches.open(CACHE).then(function (cache) { return cache.match(path); });
}

function away() {
  return cached('/away.html').then(function (r) {
    return r || new Response('The node cannot be reached.', { status: 503, headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
  });
}

// Does the node still serve this worker? Only a plain "gone" (404 or 410) removes it: a network failure, a 5xx or
// anything else says nothing, and the worker stays.
function gone() {
  return caches.keys().then(function (names) {
    return Promise.all(names.filter(function (n) { return n.indexOf('nxlx-shell-') === 0; }).map(function (n) { return caches.delete(n); }));
  }).then(function () { return self.registration.unregister(); });
}
function check() {
  return fetch('/sw.js', { cache: 'no-store' }).then(function (r) {
    return r && (r.status === 404 || r.status === 410) ? gone() : undefined;
  }, function () { return undefined; });
}

self.addEventListener('install', function (event) {
  event.waitUntil(caches.open(CACHE).then(function (cache) { return cache.addAll(SHELL); }).then(function () { return self.skipWaiting(); }));
});

self.addEventListener('activate', function (event) {
  event.waitUntil(caches.keys().then(function (names) {
    return Promise.all(names.filter(function (n) { return n !== CACHE; }).map(function (n) { return caches.delete(n); }));
  }).then(function () { return self.clients.claim(); }).then(check));
});

self.addEventListener('fetch', function (event) {
  var req = event.request, url = new URL(req.url);
  // only a GET to this node that is not the API (a download link opens as a navigation too)
  if (req.method !== 'GET' || url.origin !== self.location.origin || url.pathname.indexOf('/api/') === 0) return;
  if (req.mode === 'navigate') {
    // the node first, always; the away page only when the request fails. A page that comes is the real one.
    event.waitUntil(check());
    event.respondWith(fetch(req).catch(away));
  } else if (SHELL.indexOf(url.pathname) >= 0) {
    // the away page's own style sheet: from the node when it answers, from the cache when it does not
    event.respondWith(fetch(req).catch(function () {
      return cached(url.pathname).then(function (r) { return r || new Response('', { status: 503 }); });
    }));
  }
});
