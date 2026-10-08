// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// Watches the files of the page arrive (D68). A script or a style sheet that the box did not deliver (the connection
// was cut, or the box was too busy and said 503) used to leave the panel without that part and say nothing: no Room
// area without room.js, a bare page without app.css, an empty one without app.js. Now such a file is asked for
// again, twice at most, and if it still does not come the page says so in plain words with a Reload button.
//
// The first script of the page, in the head, so that it is listening before any other file is asked for. It needs
// nothing from the other files and they need nothing from it, except one line in app.js: the panel draws itself
// again when a part arrives late (the event "pvjfile").
//
// Told apart by what the browser reports, since a page cannot read the status of its own files:
//   not delivered   the element gets an "error" event (no answer, a cut connection, a status that is not 2xx).
//                   Asked again; counted per file, so it ends.
//   arrived, broken the element gets "load", but the script did not leave what it is there to leave (its
//                   data-gives attribute in index.html names it, as pvjRoom for room.js). Asking again would bring
//                   the same file, so it is not asked for; the page says that the file is at fault.
// Nothing here loads the page again by itself: an address that came with a code or a token in it would lose that.
(function () {
  'use strict';

  var AGAIN = [400, 1500];       // the waits before the first and the second new request, in ms; no third
  var asked = {};                // path -> how often it was asked for again
  var lost = [];                 // paths given up on
  var broken = [];               // paths that arrived and did not start
  var note = null;
  window.pvjLoad = { asked: asked, lost: lost, broken: broken };

  // The path of a script or style sheet of this box that the element stands for; null for anything else (a picture,
  // a font, something from another address, which the page's policy refuses anyway).
  function fileOf(el) {
    var tag = el && el.tagName, url = '';
    if (tag === 'SCRIPT') url = el.getAttribute('src');
    else if (tag === 'LINK' && /(^|\s)stylesheet(\s|$)/i.test(el.getAttribute('rel') || '')) url = el.getAttribute('href');
    if (!url) return null;
    try { url = new URL(url, location.href); } catch (e) { return null; }
    return url.origin === location.origin ? url.pathname : null;
  }

  function again(old, path) {
    var script = old.tagName === 'SCRIPT', fresh = document.createElement(script ? 'script' : 'link');
    if (script) {
      if (old.getAttribute('data-gives')) fresh.setAttribute('data-gives', old.getAttribute('data-gives'));
      // (a script added by a script runs when it arrives; by then the scripts after it in the page have run, which
      // is why the panel is told below)
      fresh.addEventListener('load', function () { document.dispatchEvent(new Event('pvjfile')); });
      fresh.src = old.getAttribute('src');
    } else {
      fresh.rel = 'stylesheet';
      fresh.href = old.getAttribute('href');
    }
    // in the old one's place: style sheets keep their order (the theme's colours before the panel's rules)
    if (old.parentNode) old.parentNode.replaceChild(fresh, old);
    else document.head.appendChild(fresh);
  }

  function name(path) { return path.replace(/^\//, ''); }

  function tell() {
    if (!document.body) return document.addEventListener('DOMContentLoaded', tell);
    if (!note) {
      note = document.createElement('div');
      note.id = 'loadnote';
      note.setAttribute('role', 'alert');
      // styled here and not in app.css, which may be the file that did not come (set through the style object,
      // which the page's policy allows; a style attribute in the page it would refuse)
      var st = { background: '#5c1616', color: '#fff', font: '16px/1.4 system-ui, sans-serif', padding: '12px 16px', margin: '0', position: 'relative', zIndex: '1000' };
      Object.keys(st).forEach(function (k) { note.style[k] = st[k]; });
      document.body.insertBefore(note, document.body.firstChild);
    }
    note.textContent = '';
    var lines = [];
    if (lost.length) lines.push('Part of this page did not arrive from the box (' + lost.map(name).join(', ') + '), so some of the panel is missing. The box keeps playing. Load the page again.');
    if (broken.length) lines.push('Part of this page arrived but did not start (' + broken.map(name).join(', ') + '), so some of the panel is missing. This is a fault in the panel, not in the connection; please report it.');
    lines.forEach(function (t) {
      var p = document.createElement('p');
      p.textContent = t;
      p.style.margin = '0 0 8px';
      note.appendChild(p);
    });
    var b = document.createElement('button');
    b.type = 'button';
    b.id = 'loadagain';
    b.textContent = 'Reload';
    var bs = { font: 'inherit', minHeight: '44px', padding: '8px 20px', border: '1px solid #fff', borderRadius: '8px', background: '#fff', color: '#5c1616' };
    Object.keys(bs).forEach(function (k) { b.style[k] = bs[k]; });
    b.addEventListener('click', function () { location.reload(); });
    note.appendChild(b);
  }

  function add(list, path) {
    if (list.indexOf(path) < 0) { list.push(path); tell(); }
  }

  // "error" and "load" on an element do not bubble, but they pass through the document on their way down.
  document.addEventListener('error', function (e) {
    var el = e.target, path = fileOf(el);
    if (!path) return;
    var n = asked[path] || 0;
    if (n >= AGAIN.length) return add(lost, path);
    asked[path] = n + 1;
    setTimeout(function () { again(el, path); }, AGAIN[n]);
  }, true);

  document.addEventListener('load', function (e) {
    var el = e.target, gives = el && el.tagName === 'SCRIPT' && el.getAttribute('data-gives'), path = gives && fileOf(el);
    if (path && window[gives] === undefined) add(broken, path);
  }, true);
})();
