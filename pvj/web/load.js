// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// Watches the files of the page arrive (D68). A script or a style sheet that the box did not deliver (the connection
// was cut, or the box was too busy and said 503) used to leave the panel without that part and say nothing: no Room
// area without room.js, a bare page without app.css, an empty one without app.js. Now such a file is asked for
// again, twice at most, and if it still does not come the page says so in plain words, with a button that asks once
// more in place.
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
// For a file that did not arrive the notice's button does not load the page again either: it asks for the file once
// more where it is, so a code that was scanned stays in its field. Only a file that arrived broken gets a Reload
// button, and when that would cost a scanned code the notice says so first.
//
// Every script of the page except this one names what it leaves in data-gives (tests/test_page_files.py holds the
// page to that), app.js too: its last statement sets pvjApp, so a file that cannot be read as a program, or that
// stops before the panel was started, is named here and the page is not left empty and silent.
(function () {
  'use strict';

  // The waits before the first and the second new request, in ms; no third. Each is stretched or shortened by up to
  // a quarter, by chance: thirty phones that lost a file in the same moment do not all ask again in the same one.
  var AGAIN = [400, 1500];
  var asked = {};                // path -> how often it was asked for again since it last arrived
  var lost = [];                 // paths given up on
  var broken = [];               // paths that arrived and did not start
  var waits = [];                // [path, ms] for every new request since the page was opened: a record, it limits nothing
  var last = {};                 // path -> the element that failed last, for the notice's button
  var note = null, hidden = false;
  // Whether the address came with a scanned code or PIN in it (app.js takes it out at once). Only whether: what it
  // was is not kept here or anywhere else.
  var CODE = /^#(code|pin)=/;
  var hadCode = CODE.test(location.hash);
  window.pvjLoad = { asked: asked, lost: lost, broken: broken, waits: waits };

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

  function styled(el, st) { Object.keys(st).forEach(function (k) { el.style[k] = st[k]; }); return el; }

  function button(id, text, press) {
    var b = document.createElement('button');
    b.type = 'button';
    b.id = id;
    b.textContent = text;
    b.addEventListener('click', press);
    return styled(b, { font: 'inherit', minHeight: '44px', padding: '8px 20px', margin: '0', border: '1px solid #fff', borderRadius: '8px', background: '#fff', color: '#5c1616' });
  }

  // The notice's own button for files that did not arrive: each is asked for once more, where it is, and has its
  // two further requests again. Somebody pressed it, so it is no loop; and the page is not loaded again, so what
  // is on it stays (a scanned code in its field, a PIN half typed).
  function retry() {
    lost.splice(0).forEach(function (path) {
      asked[path] = 0;
      waits.push([path, 0]);
      if (last[path]) again(last[path], path);
    });
    tell();
  }

  function tell() {
    if (!document.body) return document.addEventListener('DOMContentLoaded', tell);
    if (hidden || (!lost.length && !broken.length)) {
      if (note && note.parentNode) note.parentNode.removeChild(note);
      note = null;
      return;
    }
    if (!note) {
      note = document.createElement('div');
      note.id = 'loadnote';
      note.setAttribute('role', 'alert');
      // Styled here and not in app.css, which may be the file that did not come (set through the style object,
      // which the page's policy allows; a style attribute in the page it would refuse). Fixed to the top of the
      // window and above everything, so it is seen wherever the page has been scrolled to and pushes nothing
      // down; it covers the top of the page for as long as it is there, which is what Hide is for. It never takes
      // the cursor.
      styled(note, { position: 'fixed', top: '0', left: '0', right: '0', zIndex: '2147483647', boxSizing: 'border-box', maxHeight: '60vh', overflowY: 'auto',
        background: '#5c1616', color: '#fff', font: '16px/1.4 system-ui, sans-serif', padding: '12px 16px', margin: '0', overflowWrap: 'anywhere', textAlign: 'left' });
      note.style.paddingTop = 'max(12px, env(safe-area-inset-top))';      // under a phone's notch; ignored where unknown
      document.body.insertBefore(note, document.body.firstChild);
    }
    note.textContent = '';
    var lines = [];
    if (lost.length) lines.push('Part of this page did not arrive from the box (' + lost.map(name).join(', ') + '), so some of the panel is missing. The box keeps playing. Press Try again.');
    if (broken.length) {
      lines.push('Part of this page arrived but did not start (' + broken.map(name).join(', ') + '), so some of the panel is missing. This is a fault in the panel, not in the connection; please report it.');
      // Reload is the one thing here that loses a scanned code, and only once app.js has taken it out of the address.
      if (hadCode && !CODE.test(location.hash)) lines.push('Reload forgets the code you scanned. Join first, or scan the code again afterwards.');
    }
    lines.forEach(function (t) {
      var p = document.createElement('p');
      p.textContent = t;
      p.style.margin = '0 0 8px';
      note.appendChild(p);
    });
    var row = styled(document.createElement('div'), { display: 'flex', flexWrap: 'wrap', gap: '8px' });
    if (lost.length) row.appendChild(button('loadagain', 'Try again', retry));
    if (broken.length) row.appendChild(button('loadreload', 'Reload', function () { location.reload(); }));
    // Hide takes the notice away until there is something new to say; the missing part stays missing.
    row.appendChild(styled(button('loadhide', 'Hide', function () { hidden = true; tell(); }), { background: 'transparent', color: '#fff' }));
    note.appendChild(row);
  }

  function add(list, path) {
    if (list.indexOf(path) < 0) { list.push(path); hidden = false; tell(); }
  }

  // "error" and "load" on an element do not bubble, but they pass through the document on their way down.
  document.addEventListener('error', function (e) {
    var el = e.target, path = fileOf(el);
    if (!path) return;
    var n = asked[path] || 0;
    if (n >= AGAIN.length) { last[path] = el; return add(lost, path); }
    asked[path] = n + 1;
    var ms = Math.round(AGAIN[n] * (0.75 + Math.random() * 0.5));
    waits.push([path, ms]);
    setTimeout(function () { again(el, path); }, ms);
  }, true);

  document.addEventListener('load', function (e) {
    var el = e.target, path = fileOf(el);
    if (!path) return;
    // It arrived, so the count is for one load and not for the life of the page: the theme's sheet is swapped in
    // place whenever the look is changed (/theme.css?v=..., the same path), on a panel that may be open for days,
    // and each of those loads has its own two new requests.
    delete asked[path];
    var i = lost.indexOf(path);
    if (i >= 0) { lost.splice(i, 1); tell(); }
    var gives = el.tagName === 'SCRIPT' && el.getAttribute('data-gives');
    if (gives && window[gives] === undefined) add(broken, path);
  }, true);
})();
