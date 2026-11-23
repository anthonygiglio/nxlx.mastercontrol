// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// The page the QR sticker opens (J-179, D147 addendum): three faces, optional words, Send, "Thanks, sent". No login, no
// cookie, nothing read from the node. It sends one request: POST /api/feedback/visitor with the face and the words. The
// node adds its name, the time and what plays; nothing of that is typed or sent from here.
(function () {
  'use strict';
  var MAX = 1000;
  var form = document.getElementById('visitorform');
  if (!form || document.body.getAttribute('data-open') !== '1') return;     // switched off: the page already says so
  var faces = Array.prototype.slice.call(form.querySelectorAll('.feedback-face'));
  var words = document.getElementById('visitortext');
  var send = document.getElementById('visitorsend');
  var status = document.getElementById('visitorstatus');
  var face = null, busy = false, done = false;

  function say(text, cls) { status.className = 'feedback-status' + (cls ? ' ' + cls : ''); status.textContent = text; }
  function cap(t) { t = String(t || ''); return t.charAt(0).toUpperCase() + t.slice(1); }

  faces.forEach(function (b) {
    b.addEventListener('click', function () {
      if (busy || done) return;
      face = b.getAttribute('data-face');
      faces.forEach(function (x) { x.setAttribute('aria-pressed', x === b ? 'true' : 'false'); x.classList.toggle('on', x === b); });
      send.disabled = false;
      say('');
    });
  });

  send.addEventListener('click', function () {
    if (!face || busy || done) return;
    busy = true; send.disabled = true;
    say('Sending...');
    fetch('/api/feedback/visitor', {
      method: 'POST', credentials: 'omit',
      headers: { 'Content-Type': 'application/json', 'X-MCTL-Request': '1' },
      body: JSON.stringify({ face: face, text: words.value.slice(0, MAX) })
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) { return { ok: r.ok, status: r.status, data: data || {} }; });
    }, function () {
      return { ok: false, status: 0, data: { error: 'no connection to the node' } };
    }).then(function (r) {
      busy = false;
      if (r.ok) {
        done = true;
        faces.forEach(function (b) { b.disabled = true; });
        words.disabled = true;
        say('Thanks, sent', 'thanks');
        return;
      }
      send.disabled = false;
      var wait = r.data.retry_after;
      say(r.data.error ? cap(r.data.error) + (wait && r.status === 429 && wait < 120 ? ' (about ' + wait + ' seconds)' : '') : 'Could not send. Try again.', 'err');
    });
  });
})();
