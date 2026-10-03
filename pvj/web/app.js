// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
(function () {
  'use strict';

  var RANK = { view: 1, live: 2, full: 3 };
  var ACCENTS = ['#f59e0b', '#c2410c', '#22d3ee', '#e879f9', '#a3e635', '#ffffff'];
  var S = {
    tab: 'live', device: null, status: null, banks: [], bank: 0, media: [], modules: [], theme: null,
    themes: [], devices: [], editing: false, sheet: null, msg: '', msgErr: false, token: null, failures: 0
  };
  var app = document.getElementById('app');
  var offlineBanner = document.getElementById('offline');

  // ---- helpers --------------------------------------------------------
  function h(tag, attrs) {
    var el = document.createElement(tag);
    attrs = attrs || {};
    Object.keys(attrs).forEach(function (k) {
      var v = attrs[k];
      if (v === false || v === null || v === undefined) return;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else if (k.slice(0, 2) === 'on') el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? '' : v);
    });
    var add = function (kid) {
      if (kid === null || kid === undefined || kid === false) return;
      if (Array.isArray(kid)) return kid.forEach(add);
      el.appendChild(typeof kid === 'string' ? document.createTextNode(kid) : kid);
    };
    for (var i = 2; i < arguments.length; i++) add(arguments[i]);
    return el;
  }
  function can(role) { return !!S.device && RANK[S.device.role] >= RANK[role]; }
  function base(path) { return (path || '').split('/').pop(); }
  function clock(sec) {
    if (typeof sec !== 'number' || sec < 0) return '--:--';
    var m = Math.floor(sec / 60), s = Math.floor(sec % 60);
    return (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;
  }
  function throttle(fn, ms) {
    var last = 0, timer = null, args;
    return function () {
      args = arguments;
      var wait = ms - (Date.now() - last);
      if (wait <= 0) { last = Date.now(); fn.apply(null, args); }
      else if (!timer) timer = setTimeout(function () { timer = null; last = Date.now(); fn.apply(null, args); }, wait);
    };
  }

  function api(method, path, body) {
    var opts = { method: method, credentials: 'same-origin', headers: {} };
    if (method === 'POST') {
      opts.headers['Content-Type'] = 'application/json';
      opts.headers['X-PVJ-Request'] = '1';
      opts.body = JSON.stringify(body || {});
    }
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        S.failures = 0;
        offlineBanner.hidden = true;
        if (r.status === 401 && S.device) { S.device = null; render(); }
        return { ok: r.ok, status: r.status, data: data };
      });
    }, function () {
      S.failures++;
      if (S.failures >= 2) offlineBanner.hidden = false;
      return { ok: false, status: 0, data: { error: 'no connection' } };
    });
  }
  function say(text, isErr) { S.msg = text || ''; S.msgErr = !!isErr; var m = document.getElementById('msg'); if (m) { m.textContent = S.msg; m.className = 'msg' + (isErr ? ' err' : ''); } }
  function act(method, path, body, after) {
    return api(method, path, body).then(function (r) {
      if (!r.ok) say(r.data.error || 'Something went wrong', true);
      else if (after) after(r.data);
      return r;
    });
  }

  // ---- data loading ---------------------------------------------------
  function loadAll() {
    return Promise.all([
      api('GET', '/api/status'), api('GET', '/api/pads'), api('GET', '/api/media'),
      api('GET', '/api/modules'), api('GET', '/api/theme'), can('full') ? api('GET', '/api/devices') : null
    ]).then(function (r) {
      if (r[0].ok) { S.status = r[0].data; S.device = r[0].data.device; }
      if (r[1].ok) S.banks = r[1].data.banks;
      if (r[2].ok) { S.media = r[2].data.files; S.mediaInfo = r[2].data; }
      if (r[3].ok) S.modules = r[3].data.modules;
      if (r[4].ok) { S.theme = r[4].data.theme; S.themes = r[4].data.available; }
      if (r[5] && r[5].ok) S.devices = r[5].data.devices;
    });
  }
  function poll() {
    if (!S.device || document.visibilityState === 'hidden') return;
    api('GET', '/api/status').then(function (r) {
      if (r.ok) {
        var was = !!(S.status && S.status.support && S.status.support.active);
        S.status = r.data;
        var now = !!(r.data.support && r.data.support.active);
        if (was !== now) return render();          // a session started or ended: show or hide the banner
        var left = document.getElementById('supportleft');
        if (left && now) left.textContent = (S.device && S.device.remote ? 'You are connected as remote support' : 'Remote support session is open') + ' · ' + mins(r.data.support.seconds_left) + ' left';
        patchLive();
      }
    });
  }

  // ---- connect --------------------------------------------------------
  function connect() {
    if (S.remote) return supportConnect();
    var pins = [0, 1, 2, 3].map(function (i) {
      return h('input', { class: 'pin', inputmode: 'numeric', autocomplete: 'one-time-code', maxlength: 1, 'aria-label': 'PIN digit ' + (i + 1), pattern: '[0-9]' });
    });
    function pinValue() { return pins.map(function (p) { return p.value; }).join(''); }
    pins.forEach(function (p, i) {
      p.addEventListener('input', function () {
        p.value = p.value.replace(/\D/g, '').slice(0, 1);
        if (p.value && pins[i + 1]) pins[i + 1].focus();
      });
      p.addEventListener('keydown', function (e) { if (e.key === 'Backspace' && !p.value && pins[i - 1]) pins[i - 1].focus(); });
      p.addEventListener('paste', function (e) {
        var text = (e.clipboardData.getData('text') || '').replace(/\D/g, '').slice(0, 4);
        if (!text) return;
        e.preventDefault();
        text.split('').forEach(function (c, j) { pins[j].value = c; });
      });
    });
    var sc = S.scanned;
    if (sc && sc.kind === 'pin') sc.value.split('').forEach(function (c, j) { if (pins[j]) pins[j].value = c; });
    var code = h('input', { class: 'text-input mono', id: 'joincode', inputmode: 'numeric', autocomplete: 'one-time-code', maxlength: 6,
      'aria-label': 'Guest or presenter code (6 digits)', placeholder: '6 digit code', value: sc && sc.kind === 'code' ? sc.value : '' });
    code.addEventListener('input', function () { code.value = code.value.replace(/\D/g, '').slice(0, 6); });
    var name = h('input', { class: 'text-input', value: sc && sc.kind === 'code' ? 'Phone' : 'My phone', 'aria-label': 'Name for this device', maxlength: 40 });
    function join(secret, button) {
      button.disabled = true;
      api('POST', '/api/pair', { pin: secret, name: name.value || 'device' }).then(function (r) {
        button.disabled = false;
        if (!r.ok) {
          var wait = r.data.retry_after ? ' Try again in ' + r.data.retry_after + ' seconds.' : '';
          var why = r.status === 403 ? (secret.length === 6 ? 'That code is wrong or has expired. Ask for a new one.' : 'Wrong PIN.') : (r.data.error || 'Could not connect.');
          return say(why + wait, true);
        }
        S.scanned = null;
        S.device = r.data.device;
        S.token = r.data.token;
        start();
      });
    }
    var pinButton = h('button', { class: 'btn' + (sc ? '' : ' on') + ' big', id: 'pairbtn', text: 'Pair with PIN' });
    pinButton.addEventListener('click', function () {
      var pin = pinValue();
      if (pin.length !== 4) return say('Enter the 4 digit PIN shown on the box.', true);
      join(pin, pinButton);
    });
    var codeButton = h('button', { class: 'btn' + (sc && sc.kind === 'code' ? ' on' : '') + ' big', id: 'joinbtn', text: 'Join with code' });
    codeButton.addEventListener('click', function () {
      if (code.value.length !== 6) return say('Enter the 6 digit code shown on the screen.', true);
      join(code.value, codeButton);
    });
    var scannedNote = sc && sc.kind === 'code' ? h('div', { class: 'msg', id: 'scannednote', text: 'Code read from the QR code. Tap Join.' }) : null;
    return h('div', { class: 'shell' },
      h('div', { class: 'screen' },
        h('h1', { text: 'Connect to your box' }),
        h('p', { text: 'Scan the QR code on the screen, or type the code shown there. The box\'s owner can use the 4 digit PIN instead. No internet needed.' }),
        scannedNote,
        h('div', { class: 'k', text: 'Guest or presenter code' }), code,
        h('label', { class: 'k', for: 'devname', text: 'Name for this device' }),
        (name.id = 'devname', name),
        codeButton,
        h('div', { class: 'k', text: 'Owner: PIN' }),
        h('div', { class: 'pin-row' }, pins),
        pinButton,
        h('div', { id: 'msg', class: 'msg', role: 'status' }),
        h('div', { class: 'grow' }),
        h('div', { class: 'k', text: 'Connection lost? The box keeps playing. Reconnect any time.' })));
  }
  // ---- remote support --------------------------------------------------
  // Support arriving through the support tunnel sees only this: the code the studio reads to them.
  function supportConnect() {
    var code = h('input', { class: 'text-input mono', id: 'supportcode', autocomplete: 'one-time-code', maxlength: 9, 'aria-label': 'Support code', placeholder: 'ABCD-2345' });
    code.addEventListener('input', function () { code.value = code.value.toUpperCase().replace(/[^A-Z0-9-]/g, '').slice(0, 9); });
    var go = h('button', { class: 'btn on big', id: 'supportlogin', text: 'Sign in' });
    go.addEventListener('click', function () {
      if (code.value.replace(/-/g, '').length !== 8) return say('Type the 8 character code the studio reads to you.', true);
      go.disabled = true;
      api('POST', '/api/support/login', { code: code.value }).then(function (r) {
        go.disabled = false;
        if (!r.ok) return say(r.data.error || 'Could not sign in.', true);
        S.device = r.data.device; start();
      });
    });
    return h('div', { class: 'shell' }, h('div', { class: 'screen' },
      h('h1', { text: 'Remote support' }),
      h('p', { text: 'You reached this box through its support tunnel. Ask the studio for the support code shown in their panel (System > Remote support) and type it here. It works only during their session.' }),
      h('label', { class: 'k', for: 'supportcode', text: 'Support code' }), code, go,
      h('div', { id: 'msg', class: 'msg', role: 'status' })));
  }
  function mins(sec) { return sec >= 120 ? Math.round(sec / 60) + ' min' : Math.max(0, sec) + ' s'; }
  function supportBanner() {
    var s = S.status && S.status.support;
    if (!s || !s.active) return null;
    var mine = S.device && S.device.remote;
    return h('div', { class: 'support-banner', id: 'supportbanner', role: 'status' },
      h('span', { id: 'supportleft', text: (mine ? 'You are connected as remote support' : 'Remote support session is open') + ' · ' + mins(s.seconds_left) + ' left' }),
      can('live') ? h('button', { class: 'btn small', id: 'supportstopbar', text: mine ? 'End session' : 'Stop',
        onclick: function () { act('POST', '/api/support/stop', {}, function () { poll(); setTimeout(render, 300); }); } }) : null);
  }
  var supportForm = null;   // survives redraws while typing the settings
  function supportCard() {
    var body = h('div', { class: 'list', id: 'supportbody' }, h('div', { class: 'k', text: 'Loading...' }));
    var card = h('div', { class: 'card', id: 'supportcard' }, h('h2', { text: 'Remote support' }), body);
    var timer = null;
    function refresh() { api('GET', '/api/support').then(function (r) { if (document.getElementById('supportcard') && r.ok) draw(r.data); }); }
    function post(path, b) { return act('POST', path, b, function (data) { say(''); draw(data); poll(); }); }
    function draw(d) {
      clearTimeout(timer);
      body.textContent = '';
      if (d.active) timer = setTimeout(refresh, 5000);
      if (!d.config) {        // support itself, or a device without full access
        body.appendChild(h('div', { class: 'k', text: d.active ? 'A support session is open, ' + mins(d.seconds_left) + ' left.' : 'No support session.' }));
        return;
      }
      var c = d.config;
      if (d.available === false) body.appendChild(h('div', { class: 'k', id: 'supportwhy', text: 'Not available on this box: ' + (d.why || '') }));
      if (d.active) {
        body.appendChild(h('div', { class: 'k', text: 'Read this code to your support contact. They open http://' + d.address + '/ through the support connection and type it.' }));
        body.appendChild(h('div', { class: 'support-code', id: 'supportcodeshow', text: d.code }));
        body.appendChild(h('div', { class: 'k', id: 'supportstate', text: (d.connected ? 'Connected to the support server' : 'Waiting for the support server...') +
          ' · ' + mins(d.seconds_left) + ' left · support signed in ' + d.logins + ' of ' + d.max_logins + ' times · ' +
          ({ full: 'full access', live: 'play and mix', view: 'watch only' })[d.role] }));
        var ext = h('select', { class: 'text-input', id: 'supportextend', 'aria-label': 'New time left' }, d.durations.map(function (m) { return h('option', { value: String(m), text: m + ' minutes from now', selected: m === 60 }); }));
        body.appendChild(h('div', { class: 'row' }, ext,
          h('button', { class: 'btn small', id: 'supportextendbtn', text: 'Set time', onclick: function () { post('/api/support/extend', { minutes: +ext.value }); } })));
        body.appendChild(h('button', { class: 'btn on', id: 'supportstop', text: 'Stop the session now', onclick: function () { post('/api/support/stop', {}); } }));
      } else if (c.allowed && d.configured) {
        var dur = h('select', { class: 'text-input', id: 'supportminutes', 'aria-label': 'How long' }, d.durations.map(function (m) { return h('option', { value: String(m), text: m < 60 ? m + ' minutes' : (m / 60) + ' hour' + (m > 60 ? 's' : ''), selected: m === 60 }); }));
        var role = h('select', { class: 'text-input', id: 'supportrole', 'aria-label': 'What support may do' },
          [['full', 'Full: support can check and change settings'], ['live', 'Play and mix only'], ['view', 'Watch only']].map(function (o) { return h('option', { value: o[0], text: o[1] }); }));
        body.appendChild(h('div', { class: 'k', text: 'Start a session when support asks for one. The box connects out to the support server; it closes by itself when the time is up, and you can stop it any time.' }));
        body.appendChild(dur); body.appendChild(role);
        body.appendChild(h('button', { class: 'btn on', id: 'supportstart', text: 'Start support session', onclick: function (e) {
          e.target.disabled = true; e.target.textContent = 'Connecting...';
          post('/api/support/start', { confirm: 'start', minutes: +dur.value, role: role.value }).then(function (r) { if (!r.ok) draw(d); });
        } }));
      }
      if (d.active) return;
      // Settings (only here, at the studio)
      var f = supportForm || { endpoint: c.endpoint, server_key: c.server_key, address: c.address, network: c.network };
      body.appendChild(h('div', { class: 'k', text: c.allowed ? 'Remote support is allowed on this box.' : 'Remote support is off. Nothing can reach this box from outside until you allow it and start a session.' }));
      body.appendChild(h('button', { class: 'btn small' + (c.allowed ? ' on' : ''), id: 'supportallow', 'aria-pressed': c.allowed ? 'true' : 'false',
        text: c.allowed ? 'Allowed. Turn off' : 'Allow remote support', onclick: function () { post('/api/support/config', { allowed: !c.allowed }); } }));
      var fields = [['endpoint', 'Support server (host:port)', 'support.example.com:51820'], ['server_key', 'Support server key', '44 characters'],
        ['address', 'This box on the support network', '10.77.0.5'], ['network', 'Support network', '10.77.0.0/24']];
      var inputs = {};
      fields.forEach(function (x) {
        inputs[x[0]] = h('input', { class: 'text-input mono', id: 'support-' + x[0], 'aria-label': x[1], placeholder: x[2], value: f[x[0]] || '', autocomplete: 'off' });
        inputs[x[0]].addEventListener('input', function () { supportForm = supportForm || Object.assign({}, f); supportForm[x[0]] = inputs[x[0]].value; });
        body.appendChild(h('label', { class: 'k', for: 'support-' + x[0], text: x[1] })); body.appendChild(inputs[x[0]]);
      });
      body.appendChild(h('button', { class: 'btn small', id: 'supportsave', text: 'Save support settings', onclick: function () {
        var b = {}; fields.forEach(function (x) { b[x[0]] = inputs[x[0]].value.trim(); });
        post('/api/support/config', b).then(function (r) { if (r.ok) supportForm = null; });
      } }));
      if (d.public_key) {
        var key = h('input', { class: 'text-input mono', id: 'supportkey', readonly: true, value: d.public_key, 'aria-label': 'This box\'s key' });
        body.appendChild(h('div', { class: 'k', text: 'This box\'s key: give it to your support team once, so their server knows this box.' }));
        body.appendChild(h('div', { class: 'row' }, key, h('button', { class: 'btn small', text: 'Copy', onclick: function () {
          key.select(); (navigator.clipboard ? navigator.clipboard.writeText(d.public_key) : Promise.reject()).then(function () { say('Key copied.'); }, function () { say('Select the key and copy it.'); });
        } })));
      }
      if (d.log && d.log.length) {
        body.appendChild(h('div', { class: 'k', text: 'Recent sessions' }));
        d.log.slice(0, 5).forEach(function (e) {
          body.appendChild(h('div', { class: 'item' }, h('span', { text: new Date(e.started * 1000).toLocaleString() + ' · ' + e.by + ' · ' + e.minutes + ' min · ' + e.role +
            (e.ended ? ' · ' + (e.reason || 'ended') + ' · ' + (e.logins || 0) + ' sign-ins' : '') })));
        });
      }
    }
    refresh();
    return card;
  }

  // ---- live -----------------------------------------------------------
  function padButton(bank, index, pad) {
    var playing = S.status && S.status.player && S.status.player.path && pad.file && base(S.status.player.path) === pad.file;
    var label = pad.label || (pad.file ? pad.file.replace(/\.[^.]+$/, '') : 'Empty');
    var b = h('button', { class: 'pad' + (playing ? ' on' : '') + (pad.file ? '' : ' empty'), 'aria-pressed': playing ? 'true' : 'false', 'data-pad': index },
      h('span', { class: 'n', text: (index + 1 < 10 ? '0' : '') + (index + 1) }), h('span', { class: 't', text: label }));
    b.addEventListener('click', function () {
      if (S.editing && can('full')) return openSheet(bank, index);
      if (!pad.file) return say(can('full') ? 'Empty pad. Tap "Edit pads" to assign a clip.' : 'Empty pad.', true);
      if (!can('live')) return say('This device is view only.', true);
      act('POST', '/api/play', { pad: [bank, index] }, function () { say(''); poll(); });
    });
    return b;
  }
  function live() {
    var st = S.status || {};
    var pl = st.player || {};
    var bank = S.banks[S.bank];
    var pads = h('div', { class: 'pads', id: 'pads' });
    (bank ? bank.pads : []).forEach(function (p, i) { pads.appendChild(padButton(S.bank, i, p)); });
    var canLive = can('live');
    return h('div', { class: 'screen' },
      h('div', { class: 'top' }, h('h1', { text: 'nxlx.mastercontrol' }), h('div', { class: 'pill k', id: 'pill' })),
      h('div', { class: 'card' },
        h('div', { class: 'k', text: 'Now playing' }),
        h('div', { id: 'np', style: false, text: '' }),
        seekBar(canLive),
        h('div', { class: 'row between' }, h('div', { class: 'k', id: 'time' }), h('div', { class: 'k', id: 'plpos' })),
        h('div', { class: 'row transport' },
          h('button', { class: 'btn small grow', id: 'prev', text: '\u23ee Prev', 'aria-label': 'Previous clip', disabled: !canLive, onclick: function () { act('POST', '/api/control', { action: 'prev' }, poll); } }),
          h('button', { class: 'btn small grow', id: 'back10', text: '\u2212 10 s', 'aria-label': 'Back 10 seconds', disabled: !canLive, onclick: function () { act('POST', '/api/control', { action: 'seek', value: -10 }, poll); } }),
          h('button', { class: 'btn small grow', id: 'fwd10', text: '+ 10 s', 'aria-label': 'Forward 10 seconds', disabled: !canLive, onclick: function () { act('POST', '/api/control', { action: 'seek', value: 10 }, poll); } }),
          h('button', { class: 'btn small grow', id: 'next', text: 'Next \u23ed', 'aria-label': 'Next clip', disabled: !canLive, onclick: function () { act('POST', '/api/control', { action: 'next' }, poll); } })),
        h('div', { class: 'row transport' },
          h('button', { class: 'btn small grow', id: 'fadein', text: 'Fade in', disabled: !canLive, onclick: function () { act('POST', '/api/fadein', { seconds: 2 }, poll); } }),
          h('button', { class: 'btn small grow', id: 'testpattern', text: 'Test pattern', disabled: !canLive, onclick: function () {
            var on = !(S.status && S.status.player && S.status.player.test_pattern);
            act('POST', '/api/testpattern', { on: on }, poll);
          } }))),
      previewBlock(),
      h('div', { class: 'banks' }, S.banks.map(function (b, i) {
        return h('button', { class: 'btn' + (i === S.bank ? ' on' : ''), text: b.name.replace('Bank ', 'Bank '), 'aria-pressed': i === S.bank ? 'true' : 'false',
          onclick: function () { S.bank = i; render(); } });
      })),
      pads,
      can('full') ? h('button', { class: 'btn small', text: S.editing ? 'Done editing' : 'Edit pads', onclick: function () { S.editing = !S.editing; render(); } }) : null,
      h('div', { id: 'msg', class: 'msg' + (S.msgErr ? ' err' : ''), role: 'status', text: S.msg }),
      h('div', { class: 'grow' }),
      h('div', { class: 'row' },
        h('button', { class: 'btn big grow', id: 'fade', text: 'Fade out', disabled: !canLive, onclick: function () { act('POST', '/api/fadeout', { seconds: 2 }); } }),
        h('button', { class: 'btn big grow', id: 'freeze', text: 'Freeze', disabled: !canLive, onclick: function () { act('POST', '/api/control', { action: 'pause' }, poll); } }),
        h('button', { class: 'btn big grow', id: 'stop', text: 'Stop', disabled: !canLive, onclick: function () { act('POST', '/api/control', { action: 'stop' }, poll); } }),
        h('button', { class: 'btn big grow', id: 'black', text: 'Blackout', disabled: !canLive, onclick: function () {
          var on = !(S.status && S.status.mix && S.status.mix.blackout);
          act('POST', '/api/blackout', { on: on }, poll);
        } })));
  }
  // ---- screen snapshot ----------------------------------------------------
  // One picture of what the box is showing, on request. Not a live view: on a Pi 4 each snapshot stalls playback
  // for about a quarter of a second (measured: a continuous preview dropped 4.7 frames a second, one every five
  // seconds still dropped 1.3), so nothing here repeats by itself.
  function previewBlock() {
    var img = h('img', { id: 'preview', alt: 'What the screen was showing', hidden: true });
    var note = h('div', { class: 'k', id: 'previewmsg', hidden: true });
    var btn = h('button', { class: 'btn small', id: 'previewbtn', text: 'Take snapshot' });
    function done() { btn.disabled = false; }
    img.addEventListener('load', function () { note.hidden = true; img.hidden = false; done(); });
    img.addEventListener('error', function () { img.hidden = true; note.hidden = false; note.textContent = 'No picture: the player may be idle or not running.'; done(); });
    btn.addEventListener('click', function () {
      btn.disabled = true; note.hidden = false; note.textContent = 'Taking a snapshot...';
      img.src = '/api/preview.jpg?t=' + Date.now();
    });
    return h('div', { class: 'card', id: 'previewcard' },
      h('div', { class: 'row between' }, h('div', { class: 'k', text: 'Screen' }), btn),
      h('div', { class: 'k', text: 'A snapshot briefly stalls playback, so it only happens when you tap.' }),
      img, note);
  }
  // Position: a slider that follows the clip, and jumps where it is released. While a finger is on it, the
  // once-a-second status update must not move it.
  var seeking = false;
  function seekBar(canLive) {
    var bar = h('input', { type: 'range', id: 'seek', class: 'seek', min: 0, max: 1000, step: 1, value: 0, 'aria-label': 'Position in the clip', disabled: !canLive });
    bar.addEventListener('pointerdown', function () { seeking = true; });
    bar.addEventListener('input', function () {
      seeking = true;
      var pl = (S.status && S.status.player) || {};
      if (pl.duration > 0) document.getElementById('time').textContent = clock(pl.duration * bar.value / 1000) + ' / ' + clock(pl.duration);
    });
    bar.addEventListener('change', function () {
      var pl = (S.status && S.status.player) || {};
      if (pl.duration > 0) act('POST', '/api/control', { action: 'seek_to', value: Math.round(pl.duration * bar.value / 10) / 100 }, function () { seeking = false; poll(); });
      else seeking = false;
    });
    bar.addEventListener('pointerup', function () { setTimeout(function () { seeking = false; }, 1500); });
    return bar;
  }
  function patchLive() {
    var st = S.status || {}, pl = st.player || {}, sys = st.system || {};
    var np = document.getElementById('np');
    if (!np) return;
    np.textContent = pl.running && pl.path ? (pl.stream || base(pl.path)) : (pl.running ? 'Player idle' : 'Player not running');
    var seekEl = document.getElementById('seek');
    var frac = pl.duration > 0 && pl.position >= 0 ? Math.min(1, pl.position / pl.duration) : 0;
    if (seekEl && !seeking) { seekEl.value = Math.round(frac * 1000); seekEl.disabled = !can('live') || !(pl.duration > 0); }
    if (!seeking) document.getElementById('time').textContent = clock(pl.position) + ' / ' + clock(pl.duration);
    var plpos = document.getElementById('plpos');
    if (plpos) plpos.textContent = pl.playlist_count > 1 && pl.playlist_pos >= 0 ? 'Clip ' + (pl.playlist_pos + 1) + ' of ' + pl.playlist_count : '';
    ['prev', 'next'].forEach(function (id) { var el = document.getElementById(id); if (el) el.disabled = !can('live') || !(pl.playlist_count > 1); });
    var tp = document.getElementById('testpattern');
    if (tp) { tp.textContent = pl.test_pattern ? 'Test pattern off' : 'Test pattern'; tp.className = 'btn small grow' + (pl.test_pattern ? ' on' : ''); }
    if (pl.test_pattern) np.textContent = 'Test pattern (colour bars)';
    if (pl.test_tone) np.textContent = 'Test tone (' + pl.test_tone + ')';
    if (pl.capture) np.textContent = 'Live input' + (pl.capture.device ? ' (' + pl.capture.device + ', ' + pl.capture.mode + ')' : '');
    var temp = typeof sys.temp_c === 'number' ? Math.round(sys.temp_c) + '°C' : '';
    document.getElementById('pill').textContent = [sys.board, temp, pl.running ? 'OK' : 'No player'].filter(Boolean).join(' · ');
    var f = document.getElementById('freeze'); if (f) f.textContent = pl.paused ? 'Resume' : 'Freeze';
    var b = document.getElementById('black'); if (b) { var on = st.mix && st.mix.blackout; b.className = 'btn big grow' + (on ? ' solid' : ''); b.textContent = on ? 'Show' : 'Blackout'; }
    var pads = document.getElementById('pads');
    if (pads && S.banks[S.bank]) {
      S.banks[S.bank].pads.forEach(function (p, i) {
        var el = pads.children[i];
        if (!el) return;
        var playing = pl.path && p.file && base(pl.path) === p.file;
        el.classList.toggle('on', !!playing);
        el.setAttribute('aria-pressed', playing ? 'true' : 'false');
      });
    }
  }
  function openSheet(bank, index) { S.sheet = { bank: bank, index: index }; render(); }
  var ENDINGS = [['loop', 'Loop'], ['stop', 'Play once, then black'], ['hold', 'Play once, hold the last frame']];
  function sheet() {
    var s = S.sheet;
    var close = function () { S.sheet = null; render(); };
    var current = (S.banks[s.bank] && S.banks[s.bank].pads[s.index]) || {};
    var ending = h('select', { class: 'text-input', id: 'padending', 'aria-label': 'When the clip ends' },
      ENDINGS.map(function (e) { return h('option', { value: e[0], text: e[1], selected: e[0] === (current.ending || 'loop') }); }));
    var pick = function (file) {
      var label = file ? file.replace(/\.[^.]+$/, '').slice(0, 40) : '';
      act('POST', '/api/pads', { bank: s.bank, index: s.index, label: label, file: file, ending: ending.value }, function (d) { S.banks = d.banks; close(); });
    };
    return h('div', { class: 'picker', onclick: function (e) { if (e.target.className === 'picker') close(); } },
      h('div', { class: 'sheet', role: 'dialog', 'aria-label': 'Choose a clip for this pad' },
        h('h2', { text: 'Pad ' + (s.index + 1) }),
        h('label', { class: 'k', for: 'padending', text: 'When the clip ends' }), ending,
        h('div', { class: 'list' }, S.media.length ? S.media.map(function (f) {
          return h('button', { class: 'btn', text: f, onclick: function () { pick(f); } });
        }) : h('div', { class: 'k', text: 'No clips in the media folder yet.' })),
        h('button', { class: 'btn', text: 'Clear pad', onclick: function () { pick(''); } }),
        h('button', { class: 'btn', text: 'Cancel', onclick: close })));
  }

  // ---- mix ------------------------------------------------------------
  function slider(id, label, min, max, step, value, fmt, send) {
    var out = h('span', { class: 'k', text: fmt(value) });
    var input = h('input', { id: id, type: 'range', min: min, max: max, step: step, value: value, disabled: !can('live') });
    var sendSoon = throttle(send, 80);
    input.addEventListener('input', function () { out.textContent = fmt(+input.value); sendSoon(+input.value); });
    input.addEventListener('change', function () { send(+input.value); });
    return h('div', { class: 'card slider' }, h('label', { for: id }, label, out), input);
  }
  function mix() {
    var m = (S.status && S.status.mix) || {}, pl = (S.status && S.status.player) || {};
    var ctl = function (action) { return function (value) { act('POST', '/api/control', { action: action, value: value }); }; };
    var choice = function (opts, current, onpick) {
      return h('div', { class: 'row' }, opts.map(function (o) {
        return h('button', { class: 'btn grow' + (o.value === current ? ' on' : ''), text: o.label, disabled: !can('live') || o.disabled,
          onclick: function () { onpick(o.value); } });
      }));
    };
    var setMix = function (patch) {
      var next = { transition: m.transition, duration: m.duration };
      Object.keys(patch).forEach(function (k) { next[k] = patch[k]; });
      act('POST', '/api/mix', next, function () { poll(); setTimeout(render, 150); });
    };
    return h('div', { class: 'screen' },
      h('div', { class: 'top' }, h('h1', { text: 'Mix' })),
      h('div', { class: 'grid2' },
        slider('mo', 'Opacity', 0, 100, 1, m.opacity === undefined ? 100 : m.opacity, function (v) { return v + '%'; }, ctl('opacity')),
        slider('ms', 'Size', 1, 200, 1, m.size === undefined ? 100 : m.size, function (v) { return v + '%'; }, ctl('size')),
        slider('mp', 'Position X', -100, 100, 1, m.position === undefined ? 0 : m.position, function (v) { return String(v); }, ctl('position')),
        slider('mpy', 'Position Y', -100, 100, 1, m.position_y === undefined ? 0 : m.position_y, function (v) { return String(v); }, ctl('position_y')),
        slider('mv', 'Speed', 25, 200, 5, Math.round((pl.speed || 1) * 100), function (v) { return (v / 100).toFixed(2) + 'x'; },
          function (v) { ctl('speed')(v / 100); }),
        slider('mvol', 'Volume', 0, 130, 1, Math.round(pl.volume === undefined || pl.volume === null ? 100 : pl.volume), function (v) { return v + '%'; }, ctl('volume'))),
      h('div', { class: 'card' },
        h('div', { class: 'k', text: 'Transition between clips' }),
        choice([{ label: 'Cut', value: 'cut' }, { label: 'Dip to black', value: 'dip' }, { label: 'Crossfade (soon)', value: 'x', disabled: true }],
          m.transition, function (v) { setMix({ transition: v }); }),
        h('div', { class: 'k', text: 'Duration' }),
        choice([0.5, 1, 2, 5].map(function (d) { return { label: d + 's', value: d }; }), m.duration, function (v) { setMix({ duration: v }); })),
      h('div', { class: 'card' },
        h('div', { class: 'k', text: 'Mirror (for rear projection or a mirror rig; costs the box some work)' }),
        h('div', { class: 'row' },
          h('button', { class: 'btn grow' + (m.flip_h ? ' on' : ''), id: 'fliph', text: 'Flip left-right', 'aria-pressed': m.flip_h ? 'true' : 'false', disabled: !can('live'),
            onclick: function () { act('POST', '/api/control', { action: 'flip_h', value: !m.flip_h }, function () { poll(); setTimeout(render, 200); }); } }),
          h('button', { class: 'btn grow' + (m.flip_v ? ' on' : ''), id: 'flipv', text: 'Flip upside down', 'aria-pressed': m.flip_v ? 'true' : 'false', disabled: !can('live'),
            onclick: function () { act('POST', '/api/control', { action: 'flip_v', value: !m.flip_v }, function () { poll(); setTimeout(render, 200); }); } }))),
      overlayCard(),
      mapperCard(),
      h('div', { class: 'card' },
        h('div', { class: 'k', text: 'Rotate' }),
        choice([0, 90, 180, 270].map(function (d) { return { label: d + '°', value: d }; }), m.rotate === undefined ? 0 : m.rotate,
          function (v) { ctl('rotate')(v); setTimeout(function () { poll(); render(); }, 200); })),
      h('div', { class: 'row' },
        h('button', { class: 'btn grow', text: 'Loop: ' + (pl.loop_file && pl.loop_file !== 'no' || pl.loop_playlist && pl.loop_playlist !== 'no' ? 'on' : 'off'), disabled: !can('live'),
          onclick: function () { var on = !(pl.loop_file && pl.loop_file !== 'no' || pl.loop_playlist && pl.loop_playlist !== 'no'); act('POST', '/api/control', { action: 'loop', value: on }, function () { poll(); setTimeout(render, 200); }); } }),
        h('button', { class: 'btn grow', text: 'Audio: ' + (pl.muted ? 'mute' : 'on'), disabled: !can('live'),
          onclick: function () { act('POST', '/api/control', { action: 'mute', value: !pl.muted }, function () { poll(); setTimeout(render, 200); }); } }),
        h('button', { class: 'btn grow', text: 'Reset mix', disabled: !can('live'),
          onclick: function () { act('POST', '/api/control', { action: 'reset' }, function () { poll(); setTimeout(render, 200); }); } })),
      h('div', { id: 'msg', class: 'msg' + (S.msgErr ? ' err' : ''), role: 'status', text: S.msg }));
  }

  // A picture over the video: a PNG from the media folder (logo, watermark, mask), fitted to the screen.
  function overlayCard() {
    var body = h('div', { class: 'list', id: 'overlaybody' }, h('div', { class: 'k', text: 'Loading...' }));
    var card = h('div', { class: 'card', id: 'overlaycard' }, h('div', { class: 'k', text: 'Overlay picture (logo or mask over the video)' }), body);
    function draw(d) {
      body.textContent = '';
      if (!d.choices.length) { body.appendChild(h('div', { class: 'k', id: 'overlaynone', text: 'Upload a PNG (transparent where the video should show) on the Media screen to use it here.' })); return; }
      var sel = h('select', { class: 'text-input', id: 'overlayfile', 'aria-label': 'Overlay picture', disabled: !can('live') },
        d.choices.map(function (n) { return h('option', { value: n, text: n, selected: n === d.file }); }));
      body.appendChild(sel);
      body.appendChild(h('button', { class: 'btn' + (d.on ? ' on' : ''), id: 'overlaytoggle', 'aria-pressed': d.on ? 'true' : 'false', disabled: !can('live'),
        text: d.on ? 'Overlay is on. Turn off' : 'Show overlay',
        onclick: function (e) {
          e.target.disabled = true; e.target.textContent = d.on ? 'Turning off...' : 'Preparing the picture...';
          act('POST', '/api/overlay', { file: sel.value, on: !d.on }, function (data) { say(''); draw(data); }).then(function (r) { if (!r.ok) draw(d); });
        } }));
    }
    api('GET', '/api/overlay').then(function (r) {
      if (!document.getElementById('overlaycard')) return;
      if (r.ok) draw(r.data); else { body.textContent = ''; body.appendChild(h('div', { class: 'k', text: r.data.error || 'Not available' })); }
    });
    return card;
  }

  // ---- projection mapping (the old Mapping tab) ------------------------
  // The screen is drawn small on a canvas; drag a corner, or pick one and nudge it. Edit on screen shows outlines on
  // the display itself. Screen corners are pixels of the display; picture corners are parts of the picture (0 to 1).
  var mapUi = { step: 10, whole: false, name: '' };  // survives redraws
  function mapOutline(s) {
    if (s.type !== 'grid') return s.vertices;
    var c = s.cols, r = s.rows, v = s.vertices, at = function (i, j) { return v[i * (c + 1) + j]; }, out = [], k;
    for (k = 0; k <= c; k++) out.push(at(0, k));
    for (k = 1; k <= r; k++) out.push(at(k, c));
    for (k = c - 1; k >= 0; k--) out.push(at(r, k));
    for (k = r - 1; k > 0; k--) out.push(at(k, 0));
    return out;
  }
  function mapperCard() {
    var body = h('div', { class: 'list', id: 'mapbody' }, h('div', { class: 'k', text: 'Loading...' }));
    var card = h('div', { class: 'card', id: 'mapcard' }, h('div', { class: 'k', text: 'Projection mapping (beta)' }), body);
    var mod = S.modules.filter(function (m) { return m.id === 'mapper'; })[0];
    if (!mod || !mod.enabled) {
      body.textContent = '';
      body.appendChild(h('div', { class: 'k', id: 'mapmsg', text: 'Off. Switch on "Projection mapper" under System > Modules (beta).' }));
      return card;
    }
    var full = can('full'), d = null, canvas = null, drag = null, lastSend = 0, waiting = null;
    // Answers can arrive out of order (the first state read can land after a change already drawn): each request is
    // numbered, and an answer older than one already drawn is not drawn.
    var asked = 0, shown = 0;
    function mapApi(method, body) {
      var n = ++asked;
      return api(method, '/api/mapper', body).then(function (r) {
        r.stale = n < shown;
        if (r.ok && !r.stale) shown = n;
        return r;
      });
    }
    function selected() { return d && d.surfaces.filter(function (s) { return s.id === d.edit.selected; })[0]; }
    function send(b) {
      return mapApi('POST', b).then(function (r) {
        if (!document.getElementById('mapcard')) return r;
        if (!r.ok) { say(r.data.error || 'Could not change the mapping', true); if (d) draw(d); return r; }
        say(''); if (!r.stale) draw(r.data); return r;
      });
    }
    function watch() {       // the show picture is built in the background: follow it until it is on
      clearTimeout(waiting);
      if (!d || d.status.state !== 'building') return;
      waiting = setTimeout(function () {
        mapApi('GET').then(function (r) { if (r.ok && !r.stale && document.getElementById('mapcard')) draw(r.data); });
      }, 1200);
    }
    function geometry() {
      var pic = d.edit.target === 'picture', cw = canvas.clientWidth || 320;
      var ch = Math.round(cw * d.screen[1] / d.screen[0]);
      return { pic: pic, cw: cw, ch: ch, sx: cw / (pic ? 1 : d.screen[0]), sy: ch / (pic ? 1 : d.screen[1]) };
    }
    function corners(s, pic) { return pic ? s.tex : s.vertices; }
    function paint() {
      if (!canvas || !d) return;
      var gm = geometry(), dpr = window.devicePixelRatio || 1;
      canvas.width = Math.round(gm.cw * dpr); canvas.height = Math.round(gm.ch * dpr); canvas.style.height = gm.ch + 'px';
      var g = canvas.getContext('2d');
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.fillStyle = '#111'; g.fillRect(0, 0, gm.cw, gm.ch);
      if (gm.pic) { g.fillStyle = '#9aa0a6'; g.font = '12px sans-serif'; g.fillText('The picture: drag the corners of the part this surface shows', 8, 16); }
      d.surfaces.slice().reverse().forEach(function (s) {
        var sel = s.id === d.edit.selected;
        if (gm.pic && !sel) return;
        var pts = gm.pic ? s.tex : mapOutline(s);
        g.strokeStyle = sel ? '#ffd800' : (s.on ? '#00ccff' : '#808080'); g.lineWidth = sel ? 2 : 1;
        g.beginPath();
        pts.forEach(function (p, i) { if (i) g.lineTo(p[0] * gm.sx, p[1] * gm.sy); else g.moveTo(p[0] * gm.sx, p[1] * gm.sy); });
        g.closePath(); g.stroke();
        if (!gm.pic) { g.fillStyle = g.strokeStyle; g.font = '11px sans-serif'; g.fillText(s.name, pts[0][0] * gm.sx + 6, pts[0][1] * gm.sy + 14); }
        if (sel) corners(s, gm.pic).forEach(function (p, i) {
          var on = !mapUi.whole && i === d.edit.corner;
          g.fillStyle = on ? '#ff2878' : '#ffd800';
          g.beginPath(); g.arc(p[0] * gm.sx, p[1] * gm.sy, on ? 7 : 4, 0, 7); g.fill();
        });
      });
    }
    function toModel(e) {
      var r = canvas.getBoundingClientRect(), gm = geometry();
      return { x: (e.clientX - r.left) / gm.sx, y: (e.clientY - r.top) / gm.sy, gm: gm };
    }
    function nearest(pt) {
      var best = null, gm = pt.gm, order = d.surfaces.slice();
      var s0 = selected();
      if (s0) order = [s0].concat(order.filter(function (s) { return s !== s0; }));
      order.forEach(function (s) {
        if (gm.pic && s !== s0) return;
        corners(s, gm.pic).forEach(function (p, i) {
          var dx = (p[0] - pt.x) * gm.sx, dy = (p[1] - pt.y) * gm.sy, dist = Math.sqrt(dx * dx + dy * dy);
          if (dist < 22 && (!best || dist < best.dist - 0.01)) best = { s: s, i: i, dist: dist };
        });
      });
      return best;
    }
    function inside(pt, poly) {
      var c = false;
      for (var i = 0, j = poly.length - 1; i < poly.length; j = i++) {
        var a = poly[i], b = poly[j];
        if ((a[1] > pt.y) !== (b[1] > pt.y) && pt.x < a[0] + (pt.y - a[1]) * (b[0] - a[0]) / (b[1] - a[1])) c = !c;
      }
      return c;
    }
    // One request at a time, the newest position next: the box never gets a flood, and an old position can never
    // land after the last one. While not editing, only the final position is sent (each change rebuilds the show).
    var inflight = false, queued = null;
    function place(final) {
      var now = Date.now();
      if (!final && (!d.edit.on || now - lastSend < 120)) return;
      lastSend = now;
      var s = drag.s, p = corners(s, d.edit.target === 'picture')[drag.i];
      var b = { action: 'place', id: s.id, target: d.edit.target, corner: drag.i, x: p[0], y: p[1] };
      queued = { b: b, final: final || !!(queued && queued.final) };
      if (!inflight) sendQueued();
    }
    function sendQueued() {
      var q = queued;
      queued = null;
      if (!q) { inflight = false; return; }
      inflight = true;
      mapApi('POST', q.b).then(function (r) {
        if (queued) return sendQueued();      // a newer position arrived meanwhile: send that, show its answer
        inflight = false;
        after(r, q.final);
      });
    }
    function after(r, final) {
      if (!document.getElementById('mapcard')) return;
      if (!r.ok) { say(r.data.error || 'Could not move that corner', true); mapApi('GET').then(function (x) { if (x.ok && !x.stale) draw(x.data); }); }
      else if (final && !r.stale) { say(''); draw(r.data); }
    }
    function draw(data) {
      d = data;
      body.textContent = '';
      var st = d.status, s = selected();
      var words = { off: 'Mapping is off', building: 'Preparing the mapped picture...', on: 'Mapping is on', editing: 'Editing on the display', error: 'Problem: ' + st.message };
      body.appendChild(h('div', { class: 'k', id: 'mapstatus', text: (words[st.state] || st.state) + ' · screen ' + d.screen[0] + 'x' + d.screen[1] + ' · ' + d.surfaces.length + ' surface' + (d.surfaces.length === 1 ? '' : 's') }));
      if (!full) { watch(); return; }
      body.appendChild(h('div', { class: 'row' },
        h('button', { class: 'btn grow' + (d.on ? ' on' : ''), id: 'mapon', 'aria-pressed': d.on ? 'true' : 'false', text: d.on ? 'Mapping on' : 'Mapping off',
          onclick: function () { send({ action: 'on', on: !d.on }); } }),
        h('button', { class: 'btn grow' + (d.edit.on ? ' on' : ''), id: 'mapedit', 'aria-pressed': d.edit.on ? 'true' : 'false', text: d.edit.on ? 'Editing on the display' : 'Edit on the display',
          onclick: function () { send({ action: 'edit', on: !d.edit.on }); } })));
      body.appendChild(h('div', { class: 'row' }, [['quad', '+ Quad'], ['triangle', '+ Triangle'], ['grid', '+ Grid']].map(function (t) {
        return h('button', { class: 'btn small grow', id: 'mapadd-' + t[0], text: t[1], disabled: d.surfaces.length >= d.limits.surfaces,
          onclick: function () { send({ action: 'add', type: t[0] }); } });
      })));
      body.appendChild(h('div', { class: 'row' }, [['screen', 'Screen corners'], ['picture', 'Picture corners']].map(function (t) {
        return h('button', { class: 'btn small grow' + (d.edit.target === t[0] ? ' on' : ''), id: 'maptarget-' + t[0], text: t[1], 'aria-pressed': d.edit.target === t[0] ? 'true' : 'false',
          onclick: function () { send({ action: 'edit', target: t[0] }); } });
      })));
      canvas = h('canvas', { class: 'mapcanvas', id: 'mapcanvas', tabindex: '0', 'aria-label': 'Mapping editor: drag a corner, or use the arrows below' });
      body.appendChild(canvas);
      canvas.addEventListener('pointerdown', function (e) {
        var pt = toModel(e), hit = nearest(pt);
        if (hit) {
          drag = { s: hit.s, i: hit.i };
          if (hit.s.id !== d.edit.selected || hit.i !== d.edit.corner) { d.edit.selected = hit.s.id; d.edit.corner = hit.i; mapUi.whole = false; paint(); }
          try { canvas.setPointerCapture(e.pointerId); } catch (x) { /* older browsers */ }
          e.preventDefault();
          return;
        }
        if (pt.gm.pic) return;
        var under = d.surfaces.filter(function (s) { return inside(pt, mapOutline(s)); })[0];
        if (under && under.id !== d.edit.selected) send({ action: 'edit', selected: under.id });
      });
      canvas.addEventListener('pointermove', function (e) {
        if (!drag) return;
        var pt = toModel(e), p = corners(drag.s, pt.gm.pic)[drag.i];
        p[0] = pt.gm.pic ? Math.min(1, Math.max(0, pt.x)) : Math.round(pt.x);
        p[1] = pt.gm.pic ? Math.min(1, Math.max(0, pt.y)) : Math.round(pt.y);
        paint(); place(false);
      });
      canvas.addEventListener('pointerup', function () { if (drag) { place(true); drag = null; } });
      canvas.addEventListener('keydown', function (e) {
        var k = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key];
        if (k && selected()) { e.preventDefault(); nudge(k[0], k[1]); }
      });
      function nudge(dx, dy) {
        var s1 = selected();
        if (!s1) return say('Add or choose a surface first.', true);
        send({ action: 'move', id: s1.id, target: d.edit.target, corner: mapUi.whole ? -1 : d.edit.corner, dx: dx * mapUi.step, dy: dy * mapUi.step });
      }
      if (s) {
        var n = corners(s, d.edit.target === 'picture').length;
        body.appendChild(h('div', { class: 'k', id: 'mapsel', text: 'Chosen: ' + s.name + (mapUi.whole ? ', the whole surface' : ', corner ' + (d.edit.corner + 1) + ' of ' + n) }));
        body.appendChild(h('div', { class: 'row nudge' },
          h('button', { class: 'btn small', id: 'mapleft', text: '←', 'aria-label': 'Move left', onclick: function () { nudge(-1, 0); } }),
          h('button', { class: 'btn small', id: 'mapup', text: '↑', 'aria-label': 'Move up', onclick: function () { nudge(0, -1); } }),
          h('button', { class: 'btn small', id: 'mapdown', text: '↓', 'aria-label': 'Move down', onclick: function () { nudge(0, 1); } }),
          h('button', { class: 'btn small', id: 'mapright', text: '→', 'aria-label': 'Move right', onclick: function () { nudge(1, 0); } }),
          h('select', { class: 'text-input', id: 'mapstep', 'aria-label': 'Step in pixels' }, [1, 10, 50].map(function (v) {
            return h('option', { value: String(v), text: v + ' px', selected: v === mapUi.step });
          }))));
        body.lastChild.lastChild.addEventListener('change', function (e) { mapUi.step = +e.target.value; });
        body.appendChild(h('div', { class: 'row' },
          h('button', { class: 'btn small grow', id: 'mapnext', text: 'Next corner', disabled: mapUi.whole,
            onclick: function () { send({ action: 'edit', corner: (d.edit.corner + 1) % n }); } }),
          h('button', { class: 'btn small grow' + (mapUi.whole ? ' on' : ''), id: 'mapwhole', 'aria-pressed': mapUi.whole ? 'true' : 'false', text: 'Move the whole surface',
            onclick: function () { mapUi.whole = !mapUi.whole; draw(d); } })));
        var nm = h('input', { class: 'text-input', id: 'mapname', 'aria-label': 'Surface name', maxlength: 40, value: s.name });
        body.appendChild(h('div', { class: 'row' }, nm, h('button', { class: 'btn small', id: 'maprename', text: 'Rename',
          onclick: function () { send({ action: 'rename', id: s.id, name: nm.value.trim() }); } })));
        if (s.type === 'grid') {
          var sizes = [1, 2, 3, 4, 5, 6, 7, 8];
          var cols = h('select', { class: 'text-input', id: 'mapcols', 'aria-label': 'Columns' }, sizes.map(function (v) { return h('option', { value: String(v), text: v + ' columns', selected: v === s.cols }); }));
          var rows = h('select', { class: 'text-input', id: 'maprows', 'aria-label': 'Rows' }, sizes.map(function (v) { return h('option', { value: String(v), text: v + ' rows', selected: v === s.rows }); }));
          body.appendChild(h('div', { class: 'row' }, cols, rows, h('button', { class: 'btn small', id: 'mapgrid', text: 'Set grid',
            onclick: function () { send({ action: 'grid', id: s.id, cols: +cols.value, rows: +rows.value }); } })));
          body.appendChild(h('div', { class: 'k', text: 'Changing the grid size spreads the points evenly again.' }));
        }
      }
      if (d.surfaces.length) body.appendChild(h('div', { class: 'k', text: 'Surfaces (the first is on top)' }));
      d.surfaces.forEach(function (x, i) {
        body.appendChild(h('div', { class: 'item map-entry' + (x.id === d.edit.selected ? ' on' : '') },
          h('button', { class: 'linkish', text: x.name + ' (' + x.type + (x.type === 'grid' ? ' ' + x.cols + 'x' + x.rows : '') + ')' + (x.on ? '' : ', hidden'), 'aria-label': 'Choose ' + x.name,
            onclick: function () { send({ action: 'edit', selected: x.id }); } }),
          h('span', { class: 'row' },
            h('button', { class: 'btn small', text: '↑', 'aria-label': 'Bring ' + x.name + ' forward', disabled: i === 0, onclick: function () { send({ action: 'order', id: x.id, dir: 'up' }); } }),
            h('button', { class: 'btn small', text: '↓', 'aria-label': 'Send ' + x.name + ' back', disabled: i === d.surfaces.length - 1, onclick: function () { send({ action: 'order', id: x.id, dir: 'down' }); } }),
            h('button', { class: 'btn small', text: x.on ? 'Hide' : 'Show', 'aria-label': (x.on ? 'Hide ' : 'Show ') + x.name, onclick: function () { send({ action: 'show', id: x.id, on: !x.on }); } }),
            h('button', { class: 'btn small', text: 'Remove', 'aria-label': 'Remove ' + x.name, onclick: function () { send({ action: 'remove', id: x.id }); } }))));
      });
      body.appendChild(h('div', { class: 'k', text: 'Saved mappings (' + d.sets.length + ' of ' + d.limits.sets + ')' }));
      if (d.sets.length) {
        var pick = h('select', { class: 'text-input', id: 'mapsets', 'aria-label': 'Saved mapping' }, d.sets.map(function (n) { return h('option', { value: n, text: n }); }));
        body.appendChild(h('div', { class: 'row' }, pick,
          h('button', { class: 'btn small', id: 'mapload', text: 'Load', onclick: function () { send({ action: 'load', name: pick.value }); } }),
          h('button', { class: 'btn small', id: 'mapdelete', text: 'Delete', onclick: function () { send({ action: 'delete', name: pick.value }); } })));
      }
      var setName = h('input', { class: 'text-input', id: 'mapsetname', 'aria-label': 'Name for this mapping', placeholder: 'Name, e.g. Main stage', maxlength: 40, value: mapUi.name });
      setName.addEventListener('input', function () { mapUi.name = setName.value; });
      body.appendChild(h('div', { class: 'row' }, setName, h('button', { class: 'btn small', id: 'mapsave', text: 'Save',
        onclick: function () { send({ action: 'save', name: mapUi.name.trim() }).then(function (r) { if (r.ok) mapUi.name = ''; }); } })));
      body.appendChild(h('div', { class: 'k', text: 'Masks: use the overlay picture above (a PNG, black where no light should fall). Map at 1920x1080 or less on a Pi 4; at 2560x1440 it drops frames.' }));
      requestAnimationFrame(paint);
      watch();
    }
    mapApi('GET').then(function (r) {
      if (!document.getElementById('mapcard') || r.stale) return;
      if (r.ok) draw(r.data); else { body.textContent = ''; body.appendChild(h('div', { class: 'k', text: r.data.error || 'Not available' })); }
    });
    return card;
  }

  // ---- media ----------------------------------------------------------
  // Live input: an HDMI capture stick or a webcam on USB, shown like a clip (the old panel's camera livefeed).
  function liveInputCard() {
    if (!can('live')) return null;
    var body = h('div', { class: 'list', id: 'inputbody' });
    var card = h('div', { class: 'card', id: 'inputcard', hidden: true }, h('div', { class: 'k', text: 'Live input (USB capture or camera)' }), body);
    api('GET', '/api/inputs').then(function (r) {
      if (!document.getElementById('inputcard') || !r.ok || !r.data.devices.length) return;
      card.hidden = false;
      var d = r.data;
      var dev = h('select', { class: 'text-input', id: 'inputdev', 'aria-label': 'Input' }, d.devices.map(function (x) { return h('option', { value: x.id, text: x.name + ' (' + x.id + ')' }); }));
      var mode = h('select', { class: 'text-input', id: 'inputmode', 'aria-label': 'Picture size' }, d.modes.map(function (m) { return h('option', { value: m, text: m }); }));
      body.appendChild(dev); body.appendChild(mode);
      body.appendChild(h('button', { class: 'btn on small', id: 'inputshow', text: d.running ? 'Show again' : 'Show live input', onclick: function () {
        say('Opening the input...');
        act('POST', '/api/play', { capture: { device: dev.value, mode: mode.value } }, function () { say('Showing the live input'); poll(); });
      } }));
      body.appendChild(h('div', { class: 'k', text: 'Playing anything else, or Stop, ends it. No sound from the input yet.' }));
    });
    return card;
  }
  function describeClip(name, i) {
    var parts = [];
    if (i.codec) parts.push(i.codec.toUpperCase() + (i.width ? ' ' + i.width + 'x' + i.height : '') + (i.fps ? ' ' + i.fps + ' fps' : ''));
    else parts.push('no picture');
    parts.push(i.audio ? 'sound: ' + i.audio : 'no sound');
    if (i.duration) parts.push(clock(i.duration));
    if (i.container) parts.push(i.container.split(',')[0]);
    var text = name + ': ' + parts.join(' \u00b7 ');
    if (i.advice && i.advice.length) text += '. Note: ' + i.advice.join(' ');
    return text;
  }
  // A copy from a USB drive runs on the box; this follows it until it is done.
  var importTimer = null;
  function watchImport(d) {
    clearTimeout(importTimer);
    var line = document.getElementById('importline');
    if (!line) return;
    line.textContent = '';
    if (d.active) {
      var pct = d.size ? Math.floor(100 * d.done / d.size) : 0;
      line.appendChild(document.createTextNode('Copying ' + d.name + ': ' + pct + '% (' + megabytes(d.done) + ' of ' + megabytes(d.size) + ') '));
      line.appendChild(h('button', { class: 'btn small', id: 'importcancel', text: 'Cancel', onclick: function () { act('POST', '/api/media/import/cancel', {}, watchImport); } }));
      importTimer = setTimeout(function () { api('GET', '/api/media/import').then(function (r) { if (r.ok) watchImport(r.data); }); }, 1000);
    } else if (d.error || d.result) {
      // Kept after the list is redrawn, until the next copy (a redraw used to wipe it at once).
      S.importNote = d.error ? 'Copy of ' + d.name + ' stopped: ' + d.error : d.name + ' is now on the box.';
      line.textContent = S.importNote;
      if (d.result && !d.seen) { d.seen = true; refreshMedia(); }
    }
  }
  function megabytes(n) { return n >= 1073741824 ? (n / 1073741824).toFixed(1) + ' GB' : (n / 1048576).toFixed(1) + ' MB'; }
  function refreshMedia() {
    return api('GET', '/api/media').then(function (r) {
      if (!r.ok) return;
      S.media = r.data.files; S.mediaInfo = r.data;
      if (!S.uploading) render();  // a redraw would wipe the progress bars of uploads still running
    });
  }
  // Results are kept so the redraw after the last upload does not wipe an error message.
  function note(text) { S.uploadNotes = (S.uploadNotes || []).concat(text).slice(-8); }
  function uploadFile(file, bar, label) {
    return new Promise(function (resolve) {
      var xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/media/upload?name=' + encodeURIComponent(file.name));
      xhr.setRequestHeader('X-PVJ-Request', '1');
      xhr.setRequestHeader('Content-Type', 'application/octet-stream');
      xhr.upload.onprogress = function (e) { if (e.lengthComputable) bar.style.width = Math.round(100 * e.loaded / e.total) + '%'; };
      xhr.onload = function () {
        var reply = {};
        try { reply = JSON.parse(xhr.responseText); } catch (e) { /* keep empty */ }
        label.textContent = file.name + (xhr.status === 200 ? ': done' : ': ' + (reply.error || 'failed (' + xhr.status + ')'));
        note(label.textContent);
        if (xhr.status === 200) bar.style.width = '100%';
        resolve(xhr.status === 200);
      };
      xhr.onerror = function () { label.textContent = file.name + ': connection lost'; note(label.textContent); resolve(false); };
      xhr.send(file);
    });
  }
  function media() {
    setTimeout(function () {
      var line = document.getElementById('importline');
      if (line && S.importNote) line.textContent = S.importNote;
      api('GET', '/api/media/import').then(function (r) { if (r.ok && r.data.active) watchImport(r.data); });
    }, 0);
    var info = S.mediaInfo || {};
    var full = can('full');
    var details = info.details || S.media.map(function (n) { return { name: n, size: 0 }; });
    var uploads = h('div', { class: 'list', id: 'uploads' });
    (S.uploadNotes || []).forEach(function (t) { uploads.appendChild(h('div', { class: 'item' }, h('div', { class: 'k', text: t }))); });
    var picker = h('input', { type: 'file', id: 'filepick', multiple: true, hidden: true, 'aria-label': 'Choose video or image files',
      accept: 'video/*,image/*,audio/*,.mkv,.mov,.mp4,.avi,.webm,.m4v,.mpg,.mpeg,.ts,.wmv,.mp3,.wav,.flac,.ogg,.m4a,.aac,.opus' });
    picker.addEventListener('change', function () {
      var files = Array.prototype.slice.call(picker.files);
      picker.value = '';
      S.uploading = (S.uploading || 0) + files.length;
      files.reduce(function (chain, file) {
        var bar = h('div', {}); var label = h('div', { class: 'k', text: file.name + ' (' + megabytes(file.size) + ')' });
        uploads.appendChild(h('div', { class: 'item' }, h('div', { class: 'grow' }, label, h('div', { class: 'progress' }, bar))));
        return chain.then(function () { return uploadFile(file, bar, label); }).then(function () { S.uploading -= 1; });
      }, Promise.resolve()).then(refreshMedia);
    });
    var items = details.map(function (d) {
      return h('div', { class: 'item' },
        h('span', {}, d.name, h('br'), h('span', { class: 'k', text: d.size ? megabytes(d.size) : '' })),
        h('span', { class: 'row' },
          h('button', { class: 'btn small', text: 'Play', disabled: !can('live'), onclick: function () { act('POST', '/api/play', { file: d.name }, function () { say('Playing ' + d.name); poll(); }); } }),
          h('button', { class: 'btn small', text: 'Info', 'aria-label': 'Details of ' + d.name, onclick: function () {
            say('Reading ' + d.name + '...');
            act('POST', '/api/media/info', { name: d.name }, function (i) { say(describeClip(d.name, i)); });
          } }),
          full ? h('button', { class: 'btn small', text: 'Rename', onclick: function () {
            var to = window.prompt('New name', d.name);
            if (to && to !== d.name) act('POST', '/api/media/rename', { name: d.name, new: to }, refreshMedia);
          } }) : null,
          full ? h('button', { class: 'btn small', text: 'Delete', onclick: function () {
            if (window.confirm('Delete ' + d.name + '?')) act('POST', '/api/media/delete', { name: d.name }, refreshMedia);
          } }) : null));
    });
    // Quick play (the old Video tab): everything in the folder, or the clips whose names start with a number
    // ("01_intro.mp4" is clip 01), looping or once. Uses the same presets as OSC and autostart.
    var numbers = [];
    S.media.forEach(function (n) { var m = /^([0-9]{2})/.exec(n); if (m && numbers.indexOf(m[1]) < 0) numbers.push(m[1]); });
    numbers.sort();
    var preset = function (name, label) { act('POST', '/api/play', { preset: name }, function (d) { say('Playing ' + label + ' (' + d.files + ' clip' + (d.files === 1 ? '' : 's') + ')'); poll(); }); };
    var numSel = numbers.length ? h('select', { class: 'text-input', id: 'numsel', 'aria-label': 'Clip number' }, numbers.map(function (n) { return h('option', { value: n, text: n + '_' }); })) : null;
    var quick = can('live') && S.media.length ? h('div', { class: 'card', id: 'quickplay' },
      h('div', { class: 'k', text: 'Play the whole folder' }),
      h('div', { class: 'row' },
        h('button', { class: 'btn small grow', id: 'playall', text: 'Play all, loop', onclick: function () { preset('startless', 'all clips'); } }),
        h('button', { class: 'btn small grow', id: 'playallonce', text: 'Play all once', onclick: function () { preset('startlessonce', 'all clips once'); } }),
        h('button', { class: 'btn small grow', id: 'shuffleall', text: 'Shuffle all', onclick: function () {
          act('POST', '/api/play', { preset: 'startless', shuffle: true }, function (d) { say('Playing all clips in a random order (' + d.files + ')'); poll(); });
        } })),
      numbers.length ? h('div', { class: 'k', text: 'Play by number (files named 01_..., 02_...)' }) : null,
      numbers.length ? h('div', { class: 'row' }, numSel,
        h('button', { class: 'btn small grow', id: 'playnum', text: 'Loop', onclick: function () { preset('startless' + numSel.value, numSel.value + '_'); } }),
        h('button', { class: 'btn small grow', id: 'playnumonce', text: 'Once', onclick: function () { preset('startlessonce' + numSel.value, numSel.value + '_ once'); } })) : null) : null;
    // Slideshow (the old Presenter tab): the pictures of the media folder or of a USB drive, one after another.
    var imagesHere = S.media.filter(function (n) { return /\.(png|jpe?g|bmp|gif)$/i.test(n); }).length;
    var drives = (info.usb || []).filter(function (d) { return d.files.some(function (f) { return /\.(png|jpe?g|bmp|gif)$/i.test(f.name); }); });
    var slideshow = null;
    if (can('live') && (imagesHere || drives.length)) {
      var src = h('select', { class: 'text-input', id: 'slidesrc', 'aria-label': 'Pictures from' },
        (imagesHere ? [h('option', { value: 'media', text: 'Media folder (' + imagesHere + ' pictures)' })] : []).concat(
          drives.map(function (d) { return h('option', { value: d.drive, text: 'USB drive ' + d.drive }); })));
      var secs = h('select', { class: 'text-input', id: 'slidesecs', 'aria-label': 'Each picture for' },
        [[0.1, 'fastest (0.1 s)'], [1, '1 second'], [2, '2 seconds'], [5, '5 seconds'], [10, '10 seconds'], [15, '15 seconds'], [30, '30 seconds'], [60, '1 minute']].map(function (o) {
          return h('option', { value: o[0], text: 'Each picture for ' + o[1], selected: o[0] === 5 });
        }));
      var end = h('select', { class: 'text-input', id: 'slideend', 'aria-label': 'After the last picture' },
        [['loop', 'Then start again'], ['hold', 'Then keep the last picture'], ['stop', 'Then black']].map(function (o) { return h('option', { value: o[0], text: o[1] }); }));
      var mix = h('input', { type: 'checkbox', id: 'slideshuffle' });
      slideshow = h('div', { class: 'card', id: 'slideshow' },
        h('div', { class: 'k', text: 'Slideshow' }), src, secs, end,
        h('label', { class: 'row', for: 'slideshuffle' }, mix, h('span', { text: 'Random order' })),
        h('button', { class: 'btn on small', id: 'slidestart', text: 'Start slideshow', onclick: function () {
          act('POST', '/api/play', { slideshow: { source: src.value, seconds: parseFloat(secs.value), ending: end.value, shuffle: mix.checked } }, function (d) {
            say('Slideshow: ' + d.images + ' pictures'); poll();
          });
        } }),
        h('div', { class: 'k', text: 'Prev and Next on the Live screen step through the pictures.' }));
    }
    return h('div', { class: 'screen' },
      h('div', { class: 'top' }, h('h1', { text: 'Media' }), h('button', { class: 'btn small', text: 'Refresh', onclick: refreshMedia })),
      quick,
      liveInputCard(),
      slideshow,
      full ? h('div', { class: 'card' },
        h('div', { class: 'k', id: 'freeline', text: (info.free !== undefined ? megabytes(info.free) + ' free' : '') + (info.max_upload ? ' \u00b7 largest file ' + megabytes(info.max_upload) : '') }),
        picker, h('button', { class: 'btn on', id: 'uploadbtn', text: 'Upload clips', onclick: function () { picker.click(); } }), uploads) : null,
      h('div', { class: 'card' }, h('div', { class: 'list' }, items.length ? items : h('div', { class: 'k', text: 'No clips yet. Upload some, or play them straight from a USB drive.' }))),
      (info.usb || []).map(function (drive) {
        return h('div', { class: 'card usb-drive', 'data-drive': drive.drive },
          h('div', { class: 'k', text: 'USB drive: ' + drive.drive + ' (read only, plays straight from the drive)' }),
          info.autostart_usb ? h('div', { class: 'k', text: 'Autostart is set to play USB sticks: plugging one in starts it, even during a show.' }) : null,
          h('div', { class: 'list' }, drive.files.length ? drive.files.map(function (f) {
            var have = S.media.indexOf(f.name) >= 0;
            return h('div', { class: 'item' },
              h('span', {}, f.name, h('br'), h('span', { class: 'k', text: megabytes(f.size) + (have ? ' \u00b7 also on the box' : '') })),
              h('span', { class: 'row' },
                h('button', { class: 'btn small', text: 'Play', 'aria-label': 'Play ' + f.name + ' from USB', disabled: !can('live'),
                  onclick: function () { act('POST', '/api/play', { usb: drive.drive + '/' + f.name }, function () { say('Playing ' + f.name); poll(); }); } }),
                can('full') ? h('button', { class: 'btn small', text: have ? 'Copy again' : 'Copy to the box', 'aria-label': 'Copy ' + f.name + ' to the box',
                  onclick: function () {
                    if (have && !window.confirm(f.name + ' is already on the box. Replace it?')) return;
                    S.importNote = '';
                    act('POST', '/api/media/import', { usb: drive.drive + '/' + f.name, replace: have }, function (d) { watchImport(d); });
                  } }) : null));
          }) : h('div', { class: 'k', text: 'No video or image files at the top of this drive.' })));
      }),
      h('div', { class: 'k', id: 'importline', role: 'status' }),
      h('div', { id: 'msg', class: 'msg' + (S.msgErr ? ' err' : ''), role: 'status', text: S.msg }));
  }

  // ---- system ---------------------------------------------------------
  function system() {
    var st = S.status || {}, sys = st.system || {}, pl = st.player || {};
    var full = can('full');
    var vitals = h('div', { class: 'card' }, h('h2', { text: 'Vitals' }),
      kv('Board', sys.model || sys.board || '?'),
      kv('Temperature', typeof sys.temp_c === 'number' ? Math.round(sys.temp_c) + '°C' : 'n/a'),
      kv('Player', pl.running ? 'Running' : 'Not running'),
      kv('This device', S.device ? S.device.name + ' (' + S.device.role + ')' : ''));
    var cards = [vitals, healthCard(), boxCard()];
    cards.push(modulesCard(full), audioCard(full), autostartCard(full), streamsCard(full), projectorsCard(full), syncCard());
    if (full || (S.device && S.device.remote)) cards.push(supportCard());
    if (full) cards.push(updateCard());
    if (full) cards.push.apply(cards, boxCareCards());
    if (full) cards.push(scheduleCard(), networkCard(), oscCard(), dmxCard(), midiCard(), appearanceCard(), accessCard(), h('div', { class: 'card' }, h('h2', { text: 'Player' }),
      h('button', { class: 'btn', text: 'Restart player now', onclick: function () { act('POST', '/api/player/restart', {}, function () { say('Player restarting. The service brings it straight back.'); }); } })));
    cards.push(h('button', { class: 'btn', text: 'Forget this device', onclick: function () {
      if (!S.device) return;
      if (full) return say('Full-access devices are removed from the list above.', true);
      S.device = null; render();
    } }));
    return h('div', { class: 'screen' }, h('div', { class: 'top' }, h('h1', { text: 'System' })),
      h('div', { id: 'msg', class: 'msg' + (S.msgErr ? ' err' : ''), role: 'status', text: S.msg }),
      h('div', { class: 'grid2' }, cards));
  }
  // ---- health (the old Powersupply, Check Services and GPU Usage buttons, in plain words) ----
  var healthTimer = null;
  function healthCard() {
    var body = h('div', { class: 'list', id: 'healthbody' }, h('div', { class: 'k', text: 'Checking...' }));
    var card = h('div', { class: 'card', id: 'healthcard' }, h('h2', { text: 'Health' }), body);
    var mark = { ok: 'OK', warn: 'Check', bad: 'Problem', unknown: '?' };
    function row(id, label, state, text) {
      return h('div', { class: 'item health-' + (state || 'unknown'), id: id },
        h('span', {}, h('b', { text: label }), h('br'), h('span', { class: 'k', text: text })),
        h('span', { class: 'badge', text: mark[state] || '?' }));
    }
    function draw(d) {
      clearTimeout(healthTimer);
      healthTimer = setTimeout(refresh, 5000);
      body.textContent = '';
      body.appendChild(row('healthpower', 'Power', d.power.state, d.power.text));
      body.appendChild(row('healthtemp', 'Temperature', d.temperature.state, d.temperature.text));
      body.appendChild(row('healthplayer', 'Player', d.player.state, d.player.text));
      var busy = d.cpu_percent !== null && d.cpu_percent >= 90;
      body.appendChild(row('healthload', 'Load', busy ? 'warn' : 'ok',
        (d.cpu_percent === null ? '?' : d.cpu_percent + '% of all cores') + (d.memory_percent === null ? '' : ' · memory ' + d.memory_percent + '% of ' + d.memory_mb + ' MB')));
      d.helpers.forEach(function (x) {
        body.appendChild(row('health-' + x.name, x.label, x.running ? 'ok' : (x.name === 'pvj-netd' ? 'unknown' : 'bad'),
          x.running ? 'Running' : (x.name === 'pvj-netd' ? 'Not running (only needed for network settings)' : 'Not running')));
      });
      body.appendChild(h('div', { class: 'k', text: 'Open the panel from another device at:' }));
      body.appendChild(h('div', { class: 'list mono', id: 'healthaddr' }, d.addresses.map(function (a) { return h('div', { class: 'item' }, h('span', { text: a })); })));
      if (can('full')) body.appendChild(h('button', { class: 'btn small', id: 'healthshowaddr', text: 'Show the address on the display (2 minutes)', onclick: function () {
        act('POST', '/api/access/screen', { show: true, items: ['address'], seconds: 120 }, function () { say('The address is on the display for 2 minutes.'); });
      } }));
    }
    function refresh() { api('GET', '/api/health').then(function (r) { if (document.getElementById('healthcard') && r.ok) draw(r.data); }); }
    refresh();
    return card;
  }

  function gb(n) { return (n / 1073741824).toFixed(1) + ' GB'; }
  // The old Settings and Display tabs' information buttons, on one card.
  function boxCard() {
    var body = h('div', { class: 'list', id: 'boxbody' }, h('div', { class: 'k', text: 'Loading...' }));
    var card = h('div', { class: 'card', id: 'boxcard' }, h('h2', { text: 'Box' }), body);
    api('GET', '/api/system').then(function (r) {
      if (!document.getElementById('boxcard')) return;
      body.textContent = '';
      if (!r.ok) return body.appendChild(h('div', { class: 'k', text: r.data.error || 'Not available' }));
      var d = r.data;
      body.appendChild(kv('nxlx.mastercontrol', d.version));
      body.appendChild(kv('Player', d.mpv || '?'));
      body.appendChild(kv('System', d.os + ' \u00b7 ' + d.kernel));
      if (d.disk) {
        body.appendChild(kv('Media storage', gb(d.disk.free) + ' free of ' + gb(d.disk.total)));
        body.appendChild(h('div', { class: 'progress', 'aria-hidden': 'true' }, (function () { var b = h('div', {}); b.style.width = Math.round(100 * d.disk.used / d.disk.total) + '%'; return b; })()));
      }
      if (d.output) body.appendChild(kv('Output now', d.output.width + ' x ' + d.output.height + (d.output.refresh ? ' at ' + d.output.refresh + ' Hz' : '')));
      d.screens.forEach(function (sc) {
        body.appendChild(kv(sc.connector, sc.connected ? 'connected' : 'nothing plugged in'));
        if (sc.connected && sc.modes.length) body.appendChild(h('div', { class: 'k mono', text: 'Modes: ' + sc.modes.join(', ') }));
      });
      var c = d.clock || {};
      var boxTime = c.now ? new Date(c.now * 1000) : null;
      body.appendChild(kv('Box clock', (boxTime ? boxTime.toLocaleString() : '?') + (c.clock_from_network === true ? ' (from the network)' : c.clock_from_network === false ? ' (NOT set from the network)' : '')));
      if (!can('full') || !d.system_actions) return;
      if (c.clock_from_network === false) body.appendChild(h('button', { class: 'btn small', id: 'setclock', text: 'Set the box clock to this phone\'s time', onclick: function () {
        act('POST', '/api/system/clock', { epoch: Math.round(Date.now() / 1000) }, function () { say('Box clock set.'); render(); });
      } }));
      body.appendChild(h('div', { class: 'row' },
        h('button', { class: 'btn small grow', id: 'rebootbtn', text: 'Restart the box', onclick: function () {
          if (!window.confirm('Restart the box now? The show stops for about a minute.')) return;
          act('POST', '/api/system/reboot', { confirm: 'reboot' }, function () { say('Restarting. Reconnect in about a minute.'); });
        } }),
        h('button', { class: 'btn small grow', id: 'poweroffbtn', text: 'Power off', onclick: function () {
          if (!window.confirm('Power the box off? Someone must unplug and replug it to start it again.')) return;
          act('POST', '/api/system/poweroff', { confirm: 'poweroff' }, function () { say('Powering off. Wait for the light to stop blinking before unplugging.'); });
        } })));
    });
    return card;
  }
  function kv(k, v) { return h('div', { class: 'row between' }, h('span', { text: k }), h('span', { class: 'k', text: String(v) })); }
  function modulesCard(full) {
    return h('div', { class: 'card' }, h('h2', { text: 'Modules' }),
      h('div', { class: 'list' }, S.modules.map(function (m) {
        var note = m.status === 'planned' ? 'Not built yet' : (!m.supported ? 'Not on this board' : m.type === 'core' ? 'Core' : m.channel);
        var b = h('button', { class: 'btn small' + (m.enabled ? ' on' : ''), text: m.enabled ? 'On' : 'Off', 'aria-pressed': m.enabled ? 'true' : 'false',
          disabled: !full || m.locked || m.status !== 'ready' || !m.supported,
          onclick: function () { act('POST', '/api/modules/' + m.id, { enabled: !m.enabled }, function (d) { S.modules = d.modules; render(); }); } });
        return h('div', { class: 'item' }, h('span', {}, m.name, h('br'), h('span', { class: 'k', text: m.version + ' · ' + note })), b);
      })));
  }
  // ---- DMX and MIDI ---------------------------------------------------
  var dmxForm = { universe: null, start: null, allow: null };  // survive redraws
  function moduleOn(id) { var m = S.modules.filter(function (x) { return x.id === id; })[0]; return !!(m && m.enabled); }
  function dmxCard() {
    var card = h('div', { class: 'card', id: 'dmxcard' }, h('h2', { text: 'DMX (Art-Net, sACN)' }));
    var body = h('div', { class: 'list', id: 'dmxbody' });
    card.appendChild(body);
    if (!moduleOn('control-dmx')) {
      body.appendChild(h('div', { class: 'k', id: 'dmxmsg', text: 'Off. Switch on "DMX over the network" under Modules above (beta).' }));
      return card;
    }
    function draw(d) {
      body.textContent = '';
      body.appendChild(h('div', { class: 'k', id: 'dmxline', text: d.error ? 'Problem: ' + d.error :
        (d.listening ? 'Listening on UDP ' + d.port + ' (' + d.received + ' frames for this universe)' : 'Off') }));
      if (d.channels) body.appendChild(h('div', { class: 'k mono', id: 'dmxlevels', text: 'Channels ' + d.start + '-' + (d.start + 7) + ': ' + d.channels.join(' ') }));
      var proto = h('select', { class: 'text-input', id: 'dmxproto', 'aria-label': 'Protocol' },
        [['artnet', 'Art-Net'], ['sacn', 'sACN (E1.31)']].map(function (p) { return h('option', { value: p[0], text: p[1], selected: p[0] === d.protocol }); }));
      var uni = h('input', { class: 'text-input mono', id: 'dmxuni', type: 'number', 'aria-label': 'Universe', value: dmxForm.universe === null ? d.universe : dmxForm.universe });
      var start = h('input', { class: 'text-input mono', id: 'dmxstart', type: 'number', min: 1, max: 505, 'aria-label': 'Start channel', value: dmxForm.start === null ? d.start : dmxForm.start });
      var allow = h('input', { class: 'text-input mono', id: 'dmxallow', 'aria-label': 'Extra allowed networks, comma separated', placeholder: 'Extra networks, e.g. 192.168.50.0/24',
        value: dmxForm.allow === null ? d.allow.join(', ') : dmxForm.allow });
      uni.addEventListener('input', function () { dmxForm.universe = uni.value; });
      start.addEventListener('input', function () { dmxForm.start = start.value; });
      allow.addEventListener('input', function () { dmxForm.allow = allow.value; });
      function send(patch) {
        act('POST', '/api/dmx', patch, function (data) { dmxForm = { universe: null, start: null, allow: null }; say(''); draw(data); });
      }
      function fields() {
        return { protocol: proto.value, universe: parseInt(uni.value, 10), start: parseInt(start.value, 10),
          allow: allow.value.split(',').map(function (x) { return x.trim(); }).filter(Boolean) };
      }
      body.appendChild(h('button', { class: 'btn' + (d.enabled ? ' on' : ''), id: 'dmxtoggle', text: d.enabled ? 'DMX is on. Turn off' : 'Turn DMX on',
        onclick: function () { var f = fields(); f.enabled = !d.enabled; send(f); } }));
      body.appendChild(proto); body.appendChild(h('label', { class: 'k', for: 'dmxuni', text: 'Universe' })); body.appendChild(uni);
      body.appendChild(h('label', { class: 'k', for: 'dmxstart', text: 'Start channel (uses 8 channels)' })); body.appendChild(start); body.appendChild(allow);
      body.appendChild(h('button', { class: 'btn small', id: 'dmxsave', text: 'Save', onclick: function () { send(fields()); } }));
      body.appendChild(h('div', { class: 'k', text: 'Off until you turn it on. Only private networks may send. The first frame only sets a starting point, and the box holds its last state if the signal stops.' }));
    }
    api('GET', '/api/dmx').then(function (r) {
      if (!document.getElementById('dmxcard')) return;
      if (!r.ok) { body.textContent = ''; body.appendChild(h('div', { class: 'k', id: 'dmxmsg', text: r.data.error || 'Not available' })); return; }
      draw(r.data);
    });
    return card;
  }
  var MIDI_ACTIONS = [['pad', 'Play a pad'], ['stop', 'Stop'], ['pause', 'Pause / resume'], ['blackout', 'Blackout on / off'], ['fadeout', 'Fade out'],
    ['reset', 'Reset mix'], ['opacity', 'Opacity (fader)'], ['size', 'Size (fader)'], ['position', 'Position X (fader)'], ['speed', 'Speed (fader)'],
    ['volume', 'Volume (fader)'], ['blackout_hold', 'Blackout while held up (fader)']];
  var midiForm = { action: 'opacity', bank: 0, index: 0 };  // survives redraws
  var midiTimer = null;
  function midiCard() {
    var card = h('div', { class: 'card', id: 'midicard' }, h('h2', { text: 'MIDI controllers' }));
    var body = h('div', { class: 'list', id: 'midibody' });
    card.appendChild(body);
    if (!moduleOn('control-midi')) {
      body.appendChild(h('div', { class: 'k', id: 'midimsg', text: 'Off. Switch on "MIDI controller (USB)" under Modules above (beta).' }));
      return card;
    }
    function describe(e) {
      var what = MIDI_ACTIONS.filter(function (a) { return a[0] === e.action; })[0];
      var ctl = (e.kind === 'note' ? 'note ' : e.kind === 'cc' ? 'CC ' : 'program ') + e.number + (e.channel ? ' ch ' + e.channel : '');
      var pad = e.action === 'pad' ? ' ' + 'ABC'[e.bank] + (e.index + 1) : '';
      return (e.source === '*' ? 'any controller' : e.source) + ' · ' + ctl + ' → ' + (what ? what[1] : e.action) + pad;
    }
    function poll() {
      clearTimeout(midiTimer);
      midiTimer = setTimeout(function () {
        if (!document.getElementById('midicard')) return;
        api('GET', '/api/midi').then(function (r) {
          if (!r.ok || !document.getElementById('midicard')) return;
          var l = r.data.learn;
          if (l.captured) return save(l.captured);
          if (l.active) {   // only the countdown changes: do not rebuild the card under the user's finger
            var el = document.getElementById('midilearning');
            if (el) el.textContent = 'Move or press a control on any controller now (' + l.seconds_left + ' s)...';
            return poll();
          }
          draw(r.data); say('Learning stopped: nothing was moved or pressed.', true);
        });
      }, 600);
    }
    function save(c) {
      var entry = { source: c.source, kind: c.kind, channel: 0, number: c.number, action: midiForm.action };
      if (midiForm.action === 'pad') { entry.bank = midiForm.bank; entry.index = midiForm.index; }
      act('POST', '/api/midi/map', { add: entry }, function (data) { say('Mapped: ' + describe(entry)); draw(data); });
    }
    function draw(d) {
      body.textContent = '';
      var names = d.devices.map(function (x) { return x.name + (x.connected ? '' : ' (not reading)'); });
      body.appendChild(h('div', { class: 'k', id: 'midiline', text: !d.enabled ? 'Off' : (d.devices.length ? d.devices.length + ' controller' + (d.devices.length > 1 ? 's' : '') + ': ' + names.join(', ') + (d.last ? '. Last: ' + d.last : '') : 'On, waiting for a controller to be plugged in') }));
      body.appendChild(h('div', { class: 'row' },
        h('button', { class: 'btn' + (d.enabled ? ' on' : ''), id: 'miditoggle', text: d.enabled ? 'MIDI is on. Turn off' : 'Turn MIDI on',
          onclick: function () { act('POST', '/api/midi', { enabled: !d.enabled }, function (data) { say(''); draw(data); }); } }),
        h('button', { class: 'btn small' + (d.builtin ? ' on' : ''), id: 'midibuiltin', 'aria-pressed': d.builtin ? 'true' : 'false',
          text: 'Built-in map: ' + (d.builtin ? 'on' : 'off'),
          onclick: function () { act('POST', '/api/midi', { builtin: !d.builtin }, function (data) { draw(data); }); } })));
      body.appendChild(h('div', { class: 'k', text: 'Your own mappings win over the built-in map (notes 36 to 71 are pads, CC 20 to 25 are levels; see MIDI.md).' }));
      body.appendChild(h('div', { class: 'k', text: 'Mappings' }));
      if (!d.map.length) body.appendChild(h('div', { class: 'k', id: 'midinomap', text: 'None yet. Choose an action below, tap Learn, then move or press a control.' }));
      d.map.forEach(function (e) {
        body.appendChild(h('div', { class: 'item midi-entry' }, h('span', { text: describe(e) }),
          h('button', { class: 'btn small', text: 'Remove', 'aria-label': 'Remove ' + describe(e), onclick: function () {
            act('POST', '/api/midi/map', { remove: e.id }, function (data) { draw(data); });
          } })));
      });
      if (d.map.length) body.appendChild(h('button', { class: 'btn small', id: 'midiclear', text: 'Remove all mappings', onclick: function () {
        if (window.confirm('Remove all mappings?')) act('POST', '/api/midi/map', { clear: true }, function (data) { draw(data); });
      } }));
      if (!d.enabled) return;
      var action = h('select', { class: 'text-input', id: 'midiaction', 'aria-label': 'Action to assign' },
        MIDI_ACTIONS.map(function (a) { return h('option', { value: a[0], text: a[1], selected: a[0] === midiForm.action }); }));
      var bank = h('select', { class: 'text-input', id: 'midibank', 'aria-label': 'Bank', hidden: midiForm.action !== 'pad' },
        ['A', 'B', 'C'].map(function (n, i) { return h('option', { value: i, text: 'Bank ' + n, selected: i === midiForm.bank }); }));
      var index = h('select', { class: 'text-input', id: 'midiindex', 'aria-label': 'Pad', hidden: midiForm.action !== 'pad' },
        Array.apply(null, Array(12)).map(function (_, i) { return h('option', { value: i, text: 'Pad ' + (i + 1), selected: i === midiForm.index }); }));
      function remember() { midiForm = { action: action.value, bank: parseInt(bank.value, 10), index: parseInt(index.value, 10) }; bank.hidden = index.hidden = action.value !== 'pad'; }
      [action, bank, index].forEach(function (el) { el.addEventListener('change', remember); });
      body.appendChild(h('div', { class: 'k', text: 'Add a mapping' }));
      body.appendChild(action); body.appendChild(bank); body.appendChild(index);
      if (d.learn.active) {
        body.appendChild(h('div', { class: 'msg', id: 'midilearning', role: 'status', text: 'Move or press a control on any controller now (' + d.learn.seconds_left + ' s)...' }));
        body.appendChild(h('button', { class: 'btn small', id: 'midicancel', text: 'Cancel', onclick: function () {
          clearTimeout(midiTimer); act('POST', '/api/midi/learn', { start: false }, function (data) { draw(data); });
        } }));
        poll();
      } else {
        body.appendChild(h('button', { class: 'btn on small', id: 'midilearn', text: 'Learn a control', onclick: function () {
          remember();
          act('POST', '/api/midi/learn', { start: true }, function (data) { draw(data); });
        } }));
      }
    }
    api('GET', '/api/midi').then(function (r) {
      if (!document.getElementById('midicard')) return;
      if (!r.ok) { body.textContent = ''; body.appendChild(h('div', { class: 'k', id: 'midimsg', text: r.data.error || 'Not available' })); return; }
      draw(r.data);
    });
    return card;
  }
  // ---- audio output -----------------------------------------------------
  function audioCard(full) {
    var body = h('div', { class: 'list', id: 'audiobody' });
    var card = h('div', { class: 'card', id: 'audiocard' }, h('h2', { text: 'Sound output' }), body);
    function label(d) { return d.description ? d.description + ' (' + d.name.replace(/^alsa\//, '') + ')' : d.name; }
    function draw(d) {
      body.textContent = '';
      var auto = d.devices.filter(function (x) { return x.name === d.automatic_is; })[0];
      var sel = h('select', { class: 'text-input', id: 'audiodev', 'aria-label': 'Sound output', disabled: !full },
        [h('option', { value: 'auto', text: 'Automatic: ' + (auto ? auto.description : 'the player\'s own choice'), selected: d.device === 'auto' })].concat(
          d.devices.filter(function (x) { return x.name !== 'auto'; }).map(function (x) { return h('option', { value: x.name, text: label(x), selected: x.name === d.device }); })));
      body.appendChild(h('div', { class: 'k', id: 'audioline', text: d.device === 'auto' ? 'Automatic: on a Pi this is the HDMI port with the screen on it.' : 'Fixed to the output below.' }));
      body.appendChild(sel);
      if (full) body.appendChild(h('button', { class: 'btn on small', id: 'audiosave', text: 'Save', onclick: function () {
        act('POST', '/api/audio', { device: sel.value }, function (data) { say(''); draw(data); });
      } }));
      if (can('live')) {
        body.appendChild(h('div', { class: 'k', text: 'Test tone (5 seconds, 440 Hz; stops what is playing)' }));
        body.appendChild(h('div', { class: 'row' }, [['left', 'Left'], ['both', 'Both'], ['right', 'Right']].map(function (c) {
          return h('button', { class: 'btn small grow', id: 'tone-' + c[0], text: c[1], onclick: function () { act('POST', '/api/testtone', { channel: c[0] }, function () { say('Playing a test tone: ' + c[1].toLowerCase()); poll(); }); } });
        })));
      }
    }
    api('GET', '/api/audio').then(function (r) {
      if (!document.getElementById('audiocard')) return;
      if (!r.ok) { body.textContent = ''; body.appendChild(h('div', { class: 'k', id: 'audiomsg', text: r.data.error || 'Not available' })); return; }
      draw(r.data);
    });
    return card;
  }
  // ---- autostart -------------------------------------------------------
  var autoForm = null;  // survives redraws: { mode, file, preset, loop, delay }
  function autostartCard(full) {
    var body = h('div', { class: 'list', id: 'autobody' });
    var card = h('div', { class: 'card', id: 'autocard' }, h('h2', { text: 'Autostart' }), body);
    var MODES = [['off', 'Off'], ['file', 'Play one clip'], ['all', 'Play every clip'], ['slideshow', 'Slideshow of the pictures'],
      ['pad', 'Play a pad'], ['usb', 'Play the USB stick (and any stick plugged in later)'], ['preset', 'Legacy start script']];
    function padName(p) {
      var b = (S.banks || [])[p[0]], pad = b && b.pads && b.pads[p[1]];
      return 'Bank ' + (p[0] + 1) + ', pad ' + (p[1] + 1) + (pad && (pad.label || pad.file) ? ': ' + (pad.label || pad.file) : '');
    }
    function draw(d) {
      body.textContent = '';
      var cfg = d.config;
      var c = autoForm || { mode: cfg.mode, file: cfg.file, preset: cfg.preset, loop: cfg.loop, delay: cfg.delay,
        shuffle: !!cfg.shuffle, seconds: cfg.seconds || 10, pad: cfg.pad || [0, 0] };
      var detail = cfg.mode === 'file' ? ' (' + cfg.file + ')' : cfg.mode === 'preset' ? ' (' + cfg.preset + ')' :
        cfg.mode === 'pad' ? ' (' + padName(cfg.pad || [0, 0]) + ')' : cfg.mode === 'slideshow' ? ' (' + (cfg.seconds || 10) + ' s a picture)' : '';
      var line = cfg.mode === 'off' ? 'Off: the box waits for you at power-up.' :
        'On: ' + MODES.filter(function (m) { return m[0] === cfg.mode; })[0][1] + detail + (cfg.shuffle ? ', shuffled' : '') + ', after ' + cfg.delay + ' s.';
      body.appendChild(h('div', { class: 'k', id: 'autoline', text: line }));
      if (d.last) body.appendChild(h('div', { class: 'k', id: 'autolast', text: 'Last run ' + d.last.at + ': ' + (d.last.ok ? 'started' : 'failed, ' + d.last.message) }));
      if (!full) return;
      var mode = h('select', { class: 'text-input', id: 'automode', 'aria-label': 'What to play at power-up' },
        MODES.map(function (m) { return h('option', { value: m[0], text: m[1], selected: m[0] === c.mode }); }));
      var file = h('select', { class: 'text-input', id: 'autofile', 'aria-label': 'Clip' },
        S.media.map(function (n) { return h('option', { value: n, text: n, selected: n === (c.file || S.media[0]) }); }));
      var preset = h('input', { class: 'text-input mono', id: 'autopreset', 'aria-label': 'Start script name', placeholder: 'startlessonce05', value: c.preset, autocomplete: 'off' });
      var pads = [];
      (S.banks || []).forEach(function (bk, bi) { (bk.pads || []).forEach(function (pd, pi) { if (pd.file) pads.push([bi, pi]); }); });
      var pad = h('select', { class: 'text-input', id: 'autopad', 'aria-label': 'Pad' }, pads.length ? pads.map(function (p) {
        return h('option', { value: p.join(','), text: padName(p), selected: p[0] === c.pad[0] && p[1] === c.pad[1] });
      }) : [h('option', { value: '', text: 'No pad has a clip yet' })]);
      var seconds = h('input', { class: 'text-input mono', id: 'autoseconds', type: 'number', min: 1, max: 3600, 'aria-label': 'Seconds a picture', value: c.seconds });
      var shuffle = h('select', { class: 'text-input', id: 'autoshuffle', 'aria-label': 'Order' },
        [[false, 'In name order'], [true, 'Shuffled']].map(function (o) { return h('option', { value: String(o[0]), text: o[1], selected: o[0] === c.shuffle }); }));
      var loop = h('select', { class: 'text-input', id: 'autoloop', 'aria-label': 'Loop' },
        [[true, 'Loop'], [false, 'Play once']].map(function (o) { return h('option', { value: String(o[0]), text: o[1], selected: o[0] === c.loop }); }));
      var delay = h('input', { class: 'text-input mono', id: 'autodelay', type: 'number', min: 0, max: 120, 'aria-label': 'Wait after power-up, seconds', value: c.delay });
      var secondsLabel = h('label', { class: 'k', for: 'autoseconds', text: 'Seconds a picture' });
      var delayLabel = h('label', { class: 'k', for: 'autodelay', text: 'Wait after power-up (seconds)' });
      function remember() {
        autoForm = { mode: mode.value, file: file.value, preset: preset.value, loop: loop.value === 'true', delay: parseFloat(delay.value || '0'),
          shuffle: shuffle.value === 'true', seconds: parseFloat(seconds.value || '10'), pad: pad.value ? pad.value.split(',').map(Number) : [0, 0] };
      }
      function show() {
        var m = mode.value;
        file.hidden = m !== 'file'; preset.hidden = m !== 'preset'; pad.hidden = m !== 'pad';
        seconds.hidden = secondsLabel.hidden = m !== 'slideshow';
        shuffle.hidden = ['all', 'slideshow', 'usb'].indexOf(m) < 0;
        loop.hidden = ['file', 'all', 'slideshow'].indexOf(m) < 0;
        delay.hidden = delayLabel.hidden = m === 'off';
      }
      [mode, file, preset, pad, seconds, shuffle, loop, delay].forEach(function (el) { el.addEventListener('input', remember); el.addEventListener('change', function () { remember(); show(); }); });
      [mode, file, preset, pad, secondsLabel, seconds, shuffle, loop, delayLabel, delay].forEach(function (el) { body.appendChild(el); });
      show();
      body.appendChild(h('div', { class: 'row' },
        h('button', { class: 'btn on small', id: 'autosave', text: 'Save', onclick: function () {
          remember();
          var b = { mode: autoForm.mode, loop: autoForm.loop, delay: autoForm.delay, shuffle: autoForm.shuffle };
          if (autoForm.mode === 'file') b.file = autoForm.file;
          if (autoForm.mode === 'preset') b.preset = autoForm.preset;
          if (autoForm.mode === 'pad') b.pad = autoForm.pad;
          if (autoForm.mode === 'slideshow') b.seconds = autoForm.seconds;
          act('POST', '/api/autostart', b, function (data) { autoForm = null; say(''); draw(data); });
        } }),
        h('button', { class: 'btn small', id: 'autotest', text: 'Run it now', disabled: !can('live'), onclick: function () {
          act('POST', '/api/autostart/test', {}, function (data) { draw(data); });
        } })));
      body.appendChild(h('div', { class: 'k', text: 'Runs when the box starts, and again if the player is restarted after a crash. A Stop from the panel is not undone. "Play the USB stick" also plays each new stick the moment it is plugged in.' }));
    }
    api('GET', '/api/autostart').then(function (r) {
      if (!document.getElementById('autocard')) return;
      if (!r.ok) { body.textContent = ''; body.appendChild(h('div', { class: 'k', id: 'automsg', text: r.data.error || 'Not available' })); return; }
      draw(r.data);
    });
    return card;
  }
  // ---- streams (SRT, RTSP, RTMP) --------------------------------------
  var streamForm = { name: '', url: '' };  // survives redraws
  function streamsCard(full) {
    var body = h('div', { class: 'list', id: 'streambody' });
    var card = h('div', { class: 'card', id: 'streamcard' }, h('h2', { text: 'Streams' }), body);
    var mod = S.modules.filter(function (m) { return m.id === 'inputs-srt'; })[0];
    if (!mod || !mod.enabled) {
      body.appendChild(h('div', { class: 'k', id: 'streammsg', text: 'Off. Switch on "Streams: SRT, RTSP, RTMP" under Modules above (beta).' }));
      return card;
    }
    function draw(d) {
      body.textContent = '';
      if (!d.streams.length) body.appendChild(h('div', { class: 'k', id: 'streamempty', text: 'No streams saved yet.' }));
      d.streams.forEach(function (st) {
        body.appendChild(h('div', { class: 'item stream-entry' },
          h('span', {}, st.name, h('br'), h('span', { class: 'addr', text: st.url })),
          h('span', { class: 'row' },
            h('button', { class: 'btn small', text: 'Play', 'aria-label': 'Play ' + st.name, disabled: !can('live'),
              onclick: function () { act('POST', '/api/play', { stream: st.id }, function () { say('Playing ' + st.name); poll(); }); } }),
            full ? h('button', { class: 'btn small', text: 'Remove', 'aria-label': 'Remove ' + st.name, onclick: function () {
              act('POST', '/api/streams', { action: 'remove', id: st.id }, draw);
            } }) : null)));
      });
      if (!full) return;
      var name = h('input', { class: 'text-input', id: 'streamname', 'aria-label': 'Stream name', placeholder: 'Name', maxlength: 40, value: streamForm.name });
      var url = h('input', { class: 'text-input mono', id: 'streamurl', 'aria-label': 'Stream address', placeholder: 'srt://192.168.1.20:9000', value: streamForm.url, autocomplete: 'off' });
      name.addEventListener('input', function () { streamForm.name = name.value; });
      url.addEventListener('input', function () { streamForm.url = url.value; });
      body.appendChild(h('div', { class: 'k', text: 'Add a stream: ' + d.schemes.join(', ') + '. A login inside the address is stored on the box and hidden here.' }));
      body.appendChild(name); body.appendChild(url);
      body.appendChild(h('button', { class: 'btn on small', id: 'streamadd', text: 'Save stream', onclick: function () {
        act('POST', '/api/streams', { action: 'add', name: streamForm.name, url: streamForm.url }, function (data) {
          streamForm.name = ''; streamForm.url = ''; say(''); draw(data);
        });
      } }));
    }
    api('GET', '/api/streams').then(function (r) {
      if (!document.getElementById('streamcard')) return;
      if (!r.ok) { body.textContent = ''; body.appendChild(h('div', { class: 'k', id: 'streammsg', text: r.data.error || 'Not available' })); return; }
      draw(r.data);
    });
    return card;
  }
  // ---- updates (signed bundles, installed by pvj-update as root; D33) ----
  var updateTimer = null;
  function updateCard() {
    var body = h('div', { class: 'list', id: 'updatebody' }, h('div', { class: 'k', text: 'Loading...' }));
    var card = h('div', { class: 'card', id: 'updatecard' }, h('h2', { text: 'Updates' }), body);
    var watchUntil = 0, startedAt = 0;  // after Install, keep asking for a while: the panel itself restarts
    function later() {
      clearTimeout(updateTimer);
      if (body.isConnected && Date.now() < watchUntil) updateTimer = setTimeout(refresh, 3000);
    }
    function refresh() {
      if (!body.isConnected) return;
      api('GET', '/api/system/update').then(function (r) {
        if (!body.isConnected) return;
        if (r.ok) draw(r.data); else later();
      }, later);
    }
    function start(source, version, where) {
      if (!window.confirm('Install version ' + version + where + '? The panel and the player restart; if the new version does not come up, the box goes back to this one by itself.')) return;
      act('POST', '/api/system/update', { source: source, version: version, confirm: 'update' }, function () {
        say('Update to ' + version + ' started.');
        startedAt = Date.now();
        watchUntil = startedAt + 10 * 60 * 1000;
        later();
      });
    }
    function row(text, id, onclick) {
      return h('div', { class: 'item' }, h('span', { text: text }), h('button', { class: 'btn small', id: id, text: 'Install', onclick: onclick }));
    }
    function draw(d) {
      clearTimeout(updateTimer);
      body.textContent = '';
      body.appendChild(h('div', { class: 'k', id: 'updateversion', text: 'Installed: version ' + d.version }));
      var last = d.last;
      if (last) {
        var words = { running: 'Updating: ', done: 'Last update: ', failed: 'Last update failed: ' };
        body.appendChild(h('div', { class: 'k', id: 'updatelast', text: (words[last.state] || '') + last.message + (last.at ? ' (' + new Date(last.at * 1000).toLocaleString() + ')' : '') }));
        if (last.state === 'running') watchUntil = Math.max(watchUntil, Date.now() + 60 * 1000);
        else if (startedAt && last.at && last.at * 1000 >= startedAt - 5000) watchUntil = 0;   // the update we started has ended
      }
      var seen = {};
      d.usb.forEach(function (b, i) {
        if (seen[b.version]) return;
        seen[b.version] = true;
        body.appendChild(row('On USB drive ' + b.drive + ': version ' + b.version + (b.signed ? '' : ' (no .sig file: it will be refused)'),
          'updateusb' + i, function () { start('usb', b.version, ' from the USB drive'); }));
      });
      d.inbox.forEach(function (b, i) {
        body.appendChild(row('Uploaded: version ' + b.version + (b.signed ? '' : ' (upload its .sig file too)'),
          'updateinbox' + i, function () { start('inbox', b.version, ''); }));
      });
      if (!d.usb.length && !d.inbox.length) body.appendChild(h('div', { class: 'k', text: 'No update waiting. Put pvj-N.N.N.tar.gz and its .sig file in a pvj-update folder on a USB stick, or upload them here.' }));
      var pick = h('input', { type: 'file', id: 'updatepick', multiple: true, accept: '.gz,.sig,.sha256', hidden: true });
      pick.addEventListener('change', function () {
        var files = Array.prototype.slice.call(pick.files);
        files.sort(function (a, b) { return a.name.length - b.name.length; });   // the bundle first, then its .sig
        var next = function () {
          var f = files.shift();
          if (!f) { refresh(); return; }
          say('Uploading ' + f.name + '...');
          fetch('/api/system/update/upload?name=' + encodeURIComponent(f.name), { method: 'POST', credentials: 'same-origin',
            headers: { 'X-PVJ-Request': '1', 'Content-Type': 'application/octet-stream' }, body: f })
            .then(function (r) {
              return r.json().catch(function () { return {}; }).then(function (j) { if (!r.ok) throw new Error(j.error || 'upload failed (HTTP ' + r.status + ')'); });
            })
            .then(function () { say(f.name + ' uploaded.'); next(); }, function (e) { say(e.message || 'upload failed', true); refresh(); });
        };
        next();
      });
      body.appendChild(pick);
      body.appendChild(h('button', { class: 'btn small', id: 'updateupload', text: 'Upload an update (.tar.gz and .sig)', onclick: function () { pick.click(); } }));
      body.appendChild(h('div', { class: 'k', text: 'Only updates signed with your key are installed; older versions are refused; a failed update goes back by itself. A .sha256 file is optional.' }));
      later();
    }
    refresh();
    return card;
  }

  // ---- box care: settings export and import, diagnostics, factory reset (pvj/boxcare.py) ----
  var careForm = { passwords: false, media: '' };   // survives redraws
  function saveFile(name, data) {
    var url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2) + '\n'], { type: 'application/json' }));
    var a = h('a', { href: url, download: name, hidden: true });
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 10000);
  }
  function boxCareCards() {
    var pw = document.getElementById('exportpw'), md = document.getElementById('resetmedia');
    if (pw) careForm.passwords = pw.checked;       // read from the page before it is rebuilt (an event can be lost)
    if (md) careForm.media = md.value;
    var remote = !!(S.device && S.device.remote);
    var cards = [];

    // settings
    var tick = h('input', { type: 'checkbox', id: 'exportpw', checked: careForm.passwords });
    tick.addEventListener('change', function () { careForm.passwords = tick.checked; });
    var result = h('div', { class: 'k', id: 'importresult', role: 'status' });
    var pick = h('input', { type: 'file', id: 'importpick', accept: '.json,application/json', hidden: true });
    pick.addEventListener('change', function () {
      var f = pick.files && pick.files[0];
      pick.value = '';
      if (!f) return;
      if (!window.confirm('Replace this box\'s settings with ' + f.name + '? The PIN, the paired devices and remote support stay as they are. A copy of the present settings is kept on the box.')) return;
      say('Importing ' + f.name + '...');
      fetch('/api/system/settings/import?confirm=import', { method: 'POST', credentials: 'same-origin',
        headers: { 'X-PVJ-Request': '1', 'Content-Type': 'application/json' }, body: f })
        .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, status: r.status, data: j }; }); },
          function () { return { ok: false, status: 0, data: { error: 'no connection' } }; })
        .then(function (r) {
          if (!r.ok) return say(r.data.error || 'The import failed (HTTP ' + r.status + ')', true);
          var lines = (r.data.problems || []).map(function (t) { return 'Check: ' + t; }).concat(r.data.notes || []);
          if (!r.data.passwords_in_file) lines.push('The file holds no passwords; ' + r.data.passwords_kept + ' already on this box were kept.');
          loadAll().then(function () {
            render();
            say('Settings imported.' + (r.data.problems && r.data.problems.length ? ' Some parts need a look (see the Settings file card).' : ''));
            var el = document.getElementById('importresult');
            if (el) el.textContent = lines.join(' · ');
          });
        });
    });
    cards.push(h('div', { class: 'card', id: 'settingscard' }, h('h2', { text: 'Settings file' }),
      h('div', { class: 'list' },
        h('div', { class: 'k', text: 'Save this box\'s settings as one file, or load them from one. The PIN, the paired devices and remote support are never in the file.' }),
        remote ? null : h('label', { class: 'row', for: 'exportpw' }, tick, h('span', { text: 'Include projector passwords and stream logins (keep that file private)' })),
        h('div', { class: 'row' },
          h('button', { class: 'btn small grow', id: 'exportbtn', text: 'Export settings', onclick: function () {
            act('POST', '/api/system/settings/export', { passwords: !remote && tick.checked }, function (d) {
              saveFile(d.name, d.file);
              say('Settings saved as ' + d.name + (d.file.passwords_included ? ' (with passwords).' : ' (no passwords in it).'));
            });
          } }),
          remote ? null : h('button', { class: 'btn small grow', id: 'importbtn', text: 'Import settings...', onclick: function () { pick.click(); } })),
        pick, result)));

    // diagnostics
    var note = h('div', { class: 'k', id: 'diagnote', role: 'status' });
    cards.push(h('div', { class: 'card', id: 'diagcard' }, h('h2', { text: 'Diagnostics' }),
      h('div', { class: 'list' },
        h('div', { class: 'k', text: 'One file to send to whoever is helping you: versions, the board, modules, health and the settings. No PIN, password, code or key is in it.' }),
        h('button', { class: 'btn small', id: 'diagbtn', text: 'Download diagnostics file', onclick: function () {
          act('GET', '/api/system/diagnostics', null, function (d) {
            saveFile(d.name, d.file);
            say('Diagnostics saved as ' + d.name + '.');
            note.textContent = d.file.log && d.file.log.note ? 'Log: ' + d.file.log.note : '';
          });
        } }),
        note)));

    // factory reset: never through remote support
    if (!remote) {
      var media = h('select', { class: 'text-input', id: 'resetmedia', 'aria-label': 'What happens to the clips' },
        [['', 'What happens to the clips?'], ['keep', 'Keep the clips on the box'], ['delete', 'Delete the clips too']].map(function (o) {
          return h('option', { value: o[0], text: o[1], selected: o[0] === careForm.media });
        }));
      media.addEventListener('change', function () { careForm.media = media.value; });
      cards.push(h('div', { class: 'card', id: 'resetcard' }, h('h2', { text: 'Factory reset' }),
        h('div', { class: 'list' },
          h('div', { class: 'k', text: 'Every setting goes back to how a new box starts, and every phone, tablet and guest is unpaired. You pair again with the new PIN on the box\'s display.' }),
          media,
          h('button', { class: 'btn small', id: 'resetbtn', text: 'Reset to factory settings', onclick: function () {
            if (!media.value) return say('Choose what happens to the clips first.', true);
            var clips = media.value === 'delete' ? 'ALL CLIPS ON THE BOX ARE DELETED.' : 'The clips stay.';
            if (!window.confirm('Reset this box to factory settings? All settings are lost and every device is unpaired, this one too. ' + clips + ' This cannot be undone.')) return;
            act('POST', '/api/system/factory-reset', { confirm: 'factory-reset', media: media.value }, function () {
              careForm = { passwords: false, media: '' };
              S.device = null;
              S.msg = '';
              render();
            });
          } }))));
    }
    return cards;
  }

  // ---- multi-box sync and video wall -----------------------------------
  var syncTimer = null;
  function syncCard() {
    var body = h('div', { class: 'list', id: 'syncbody' }, h('div', { class: 'k', text: 'Loading...' }));
    var card = h('div', { class: 'card', id: 'synccard' }, h('h2', { text: 'Sync and video wall' }), body);
    var mod = S.modules.filter(function (m) { return m.id === 'wall'; })[0];
    if (!mod || !mod.enabled) {
      body.textContent = '';
      body.appendChild(h('div', { class: 'k', id: 'syncmsg', text: 'Off. Switch on "Video wall and sync" under Modules above (beta).' }));
      return card;
    }
    var full = can('full');
    function post(b) { return act('POST', '/api/sync', b, function (data) { say(''); draw(data); }); }
    function refresh() { api('GET', '/api/sync').then(function (r) { if (document.getElementById('synccard') && r.ok) draw(r.data); }); }
    function draw(d) {
      clearTimeout(syncTimer);
      body.textContent = '';
      var c = d.config, f = d.follow || {};
      var line = c.role === 'off' ? 'Off: this box plays on its own.' :
        c.role === 'server' ? 'Server: other boxes in group "' + c.group + '" follow this one. ' + (d.sent ? d.sent + ' messages sent.' : '') :
        'Client of group "' + c.group + '": ' + (d.server ? 'following ' + d.server + '. ' : 'listening for a server. ') +
          (f.state || '') + (f.file ? ', ' + f.file : '') + (typeof f.error_ms === 'number' ? ', ' + f.error_ms + ' ms off' : '');
      body.appendChild(h('div', { class: 'k', id: 'syncline', text: line }));
      if (c.role !== 'off') syncTimer = setTimeout(refresh, 2000);
      if (!full) return;
      body.appendChild(h('div', { class: 'row wrap', id: 'syncroles' }, [['off', 'Off'], ['server', 'Server (others follow)'], ['client', 'Client (follow a server)']].map(function (r) {
        return h('button', { class: 'btn small' + (c.role === r[0] ? ' on' : ''), 'aria-pressed': c.role === r[0] ? 'true' : 'false', id: 'syncrole-' + r[0], text: r[1],
          onclick: function () { post({ role: r[0] }); } });
      })));
      var group = h('input', { class: 'text-input mono', id: 'syncgroup', 'aria-label': 'Group name', value: c.group, maxlength: 24 });
      body.appendChild(h('label', { class: 'k', for: 'syncgroup', text: 'Group name (the same on every box that plays together)' }));
      body.appendChild(h('div', { class: 'row' }, group, h('button', { class: 'btn small', id: 'syncgroupsave', text: 'Save', onclick: function () { post({ group: group.value.trim() }); } })));
      body.appendChild(h('div', { class: 'k', text: 'Every box needs the same clips with the same file names (media folder or the top of a USB drive). Clients follow the server\'s clip, position, pause and blackout.' }));
      var w = c.wall, nums = function (lo, hi) { var a = []; for (var i = lo; i <= hi; i++) a.push(i); return a; };
      function pick(id, label, values, cur, fmt) {
        return h('select', { class: 'text-input', id: id, 'aria-label': label }, values.map(function (v) { return h('option', { value: String(v), text: fmt(v), selected: v === cur }); }));
      }
      var cols = pick('wallcols', 'Columns', nums(1, 8), w.cols, function (v) { return v + (v === 1 ? ' column' : ' columns'); });
      var rows = pick('wallrows', 'Rows', nums(1, 8), w.rows, function (v) { return v + (v === 1 ? ' row' : ' rows'); });
      var col = pick('wallcol', 'This screen\'s column', nums(0, 7), w.col, function (v) { return 'column ' + (v + 1); });
      var row = pick('wallrow', 'This screen\'s row', nums(0, 7), w.row, function (v) { return 'row ' + (v + 1); });
      var bezel = h('input', { class: 'text-input mono', id: 'wallbezel', type: 'number', min: 0, max: 20, step: 0.5, value: w.bezel, 'aria-label': 'Bezel, percent of a screen' });
      body.appendChild(h('div', { class: 'k', text: 'Video wall: this screen shows one tile of the picture. 1 column and 1 row shows the whole picture.' }));
      body.appendChild(h('div', { class: 'row wrap' }, cols, rows));
      body.appendChild(h('div', { class: 'row wrap' }, col, row));
      body.appendChild(h('label', { class: 'k', for: 'wallbezel', text: 'Frame between screens (percent of a screen, hides that much picture)' }));
      body.appendChild(h('div', { class: 'row' }, bezel, h('button', { class: 'btn small', id: 'wallsave', text: 'Save wall', onclick: function () {
        post({ wall: { cols: +cols.value, rows: +rows.value, col: +col.value, row: +row.value, bezel: +bezel.value } });
      } })));
    }
    refresh();
    return card;
  }

  // ---- projectors (PJLink) ---------------------------------------------
  var projForm = { name: '', host: '', port: '4352', password: '' };  // survives redraws
  function projectorsCard(full) {
    var body = h('div', { class: 'list', id: 'projbody' });
    var card = h('div', { class: 'card', id: 'projcard' }, h('h2', { text: 'Projectors' }), body);
    var mod = S.modules.filter(function (m) { return m.id === 'projector'; })[0];
    if (!mod || !mod.enabled) {
      body.appendChild(h('div', { class: 'k', id: 'projmsg', text: 'Off. Switch on "Projector control" under Modules above (beta).' }));
      return card;
    }
    var states = {};  // id -> the last answer shown under it
    function run(pid, action, label, done) {
      api('POST', '/api/projector', { id: pid, action: action }).then(function (r) {
        if (!r.ok) return say(r.data.error || 'The projector did not answer', true);
        var failed = [];
        Object.keys(r.data.results).forEach(function (k) {
          var x = r.data.results[k];
          states[k] = x.ok ? (x.power ? 'Power: ' + x.power : label + ': done') : 'Failed: ' + x.error;
          if (!x.ok) failed.push(x.error);
        });
        if (failed.length) say(failed.length + ' projector(s) did not answer: ' + failed[0], true); else say(label + ': done');
        if (done) done();
      });
    }
    function draw(d) {
      body.textContent = '';
      body.appendChild(h('div', { class: 'k', id: 'projline', text: d.projectors.length ?
        'Controlled over the network with PJLink, like the old Beamer On and Off buttons.' :
        'No projectors added. Most network projectors speak PJLink; switch it on in the projector\'s network menu.' }));
      if (d.projectors.length > 1 && can('live')) body.appendChild(h('div', { class: 'row' },
        h('button', { class: 'btn small grow', id: 'projallon', text: 'All on', onclick: function () { run('all', 'on', 'All on', function () { draw(d); }); } }),
        h('button', { class: 'btn small grow', id: 'projalloff', text: 'All off', onclick: function () { run('all', 'off', 'All off', function () { draw(d); }); } })));
      d.projectors.forEach(function (p) {
        var ctl = can('live') ? h('div', { class: 'row wrap' }, [['on', 'On'], ['off', 'Off'], ['mute', 'Picture mute'], ['unmute', 'Unmute'], ['state', 'Check']].map(function (a) {
          return h('button', { class: 'btn small', text: a[1], 'aria-label': a[1] + ' ' + p.name, onclick: function () { run(p.id, a[0], a[1], function () { draw(d); }); } });
        })) : null;
        body.appendChild(h('div', { class: 'item proj-entry' },
          h('span', {}, p.name, h('br'), h('span', { class: 'addr', text: p.host + (p.port !== 4352 ? ':' + p.port : '') + (p.has_password ? ' · password set' : '') }),
            states[p.id] ? h('br') : null, states[p.id] ? h('span', { class: 'k', text: states[p.id] }) : null),
          full ? h('button', { class: 'btn small', text: 'Remove', 'aria-label': 'Remove ' + p.name, onclick: function () {
            act('POST', '/api/projectors', { remove: p.id }, draw);
          } }) : null));
        if (ctl) body.appendChild(ctl);
      });
      if (!full) return;
      var name = h('input', { class: 'text-input', id: 'projname', 'aria-label': 'Projector name', placeholder: 'Name', maxlength: 40, value: projForm.name });
      var host = h('input', { class: 'text-input mono', id: 'projhost', 'aria-label': 'Projector address', placeholder: '192.168.1.50', value: projForm.host, autocomplete: 'off' });
      var port = h('input', { class: 'text-input mono', id: 'projport', type: 'number', min: 1, max: 65535, 'aria-label': 'Port', value: projForm.port });
      var pw = h('input', { class: 'text-input mono', id: 'projpw', type: 'password', 'aria-label': 'PJLink password (if set on the projector)', placeholder: 'Password, if the projector has one', autocomplete: 'new-password' });
      pw.value = projForm.password;     // the property, not an attribute: a typed password never becomes page HTML
      name.addEventListener('input', function () { projForm.name = name.value; });
      host.addEventListener('input', function () { projForm.host = host.value; });
      port.addEventListener('input', function () { projForm.port = port.value; });
      pw.addEventListener('input', function () { projForm.password = pw.value; });
      body.appendChild(h('div', { class: 'k', text: 'Add a projector on this network (a private address only). The password is stored on the box and never shown again.' }));
      body.appendChild(name); body.appendChild(host); body.appendChild(port); body.appendChild(pw);
      body.appendChild(h('button', { class: 'btn on small', id: 'projadd', text: 'Add projector', onclick: function () {
        act('POST', '/api/projectors', { add: { name: projForm.name || projForm.host, host: projForm.host, port: parseInt(projForm.port || '4352', 10), password: projForm.password } }, function (data) {
          projForm = { name: '', host: '', port: '4352', password: '' }; say(''); draw(data);
        });
      } }));
    }
    api('GET', '/api/projectors').then(function (r) {
      if (!document.getElementById('projcard')) return;
      if (!r.ok) { body.textContent = ''; body.appendChild(h('div', { class: 'k', id: 'projmsg', text: r.data.error || 'Not available' })); return; }
      draw(r.data);
    });
    return card;
  }
  // ---- schedule -------------------------------------------------------
  var DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  var schedForm = { time: '18:00', days: [0, 1, 2, 3, 4, 5, 6], action: 'play', file: '', preset: '', label: '' };  // survives redraws
  function scheduleCard() {
    var body = h('div', { class: 'list', id: 'schedbody' });
    var card = h('div', { class: 'card', id: 'schedcard' }, h('h2', { text: 'Schedule' }), body);
    var mod = S.modules.filter(function (m) { return m.id === 'scheduler'; })[0];
    if (!mod || !mod.enabled) {
      body.appendChild(h('div', { class: 'k', id: 'schedmsg', text: 'Off. Switch on "Weekly schedule" under Modules above (beta).' }));
      return card;
    }
    function save(cfg, done) {
      api('POST', '/api/schedule', { enabled: cfg.enabled, entries: cfg.entries }).then(function (r) {
        if (!r.ok) return say(r.data.error || 'Could not save the schedule', true);
        say(''); draw(r.data); if (done) done();
      });
    }
    function describe(e) {
      var what = e.action === 'play' ? 'Play ' + e.file : e.action === 'preset' ? 'Start script ' + e.preset :
        ({ stop: 'Stop', blackout: 'Blackout', show: 'Show screen', projector_on: 'Projectors on', projector_off: 'Projectors off' })[e.action] || e.action;
      return e.time + ' · ' + e.days.map(function (d) { return DAYS[d]; }).join(' ') + ' · ' + what;
    }
    function draw(d) {
      body.textContent = '';
      body.appendChild(h('div', { class: 'k', id: 'schedclock', text: 'Box clock: ' + d.now + ' (' + d.timezone + '). Times use this clock; check it before a show.' }));
      body.appendChild(h('button', { class: 'btn' + (d.enabled ? ' on' : ''), id: 'schedtoggle', 'aria-pressed': d.enabled ? 'true' : 'false',
        text: d.enabled ? 'Schedule is on. Turn off' : 'Turn schedule on',
        onclick: function () { save({ enabled: !d.enabled, entries: d.entries }); } }));
      if (!d.entries.length) body.appendChild(h('div', { class: 'k', id: 'schedempty', text: 'No entries yet.' }));
      d.entries.forEach(function (e) {
        var last = d.last[e.id];
        body.appendChild(h('div', { class: 'item sched-entry' },
          h('span', {}, (e.label ? e.label + ': ' : '') + describe(e),
            last ? h('br') : null, last ? h('span', { class: 'k', text: 'Last run ' + last.at + (last.ok ? '' : ' failed: ' + last.message) }) : null),
          h('button', { class: 'btn small', text: 'Remove', 'aria-label': 'Remove ' + describe(e), onclick: function () {
            save({ enabled: d.enabled, entries: d.entries.filter(function (x) { return x.id !== e.id; }) });
          } })));
      });
      var time = h('input', { class: 'text-input mono', id: 'schedtime', type: 'time', 'aria-label': 'Time', value: schedForm.time });
      time.addEventListener('input', function () { schedForm.time = time.value; });
      var days = h('div', { class: 'row wrap', id: 'scheddays' }, DAYS.map(function (name, i) {
        var on = schedForm.days.indexOf(i) >= 0;
        return h('button', { class: 'btn small' + (on ? ' on' : ''), text: name, 'aria-pressed': on ? 'true' : 'false', onclick: function () {
          var at = schedForm.days.indexOf(i);
          if (at >= 0) schedForm.days.splice(at, 1); else schedForm.days.push(i);
          draw(d);
        } });
      }));
      var action = h('select', { class: 'text-input', id: 'schedaction', 'aria-label': 'What to do' },
        [['play', 'Play a clip'], ['preset', 'Run a legacy start script'], ['stop', 'Stop the clip'], ['blackout', 'Blackout'], ['show', 'Show screen'],
          ['projector_on', 'Projectors on'], ['projector_off', 'Projectors off']].map(function (a) {
          return h('option', { value: a[0], text: a[1], selected: a[0] === schedForm.action });
        }));
      var file = h('select', { class: 'text-input', id: 'schedfile', 'aria-label': 'Clip to play', hidden: schedForm.action !== 'play' },
        S.media.map(function (n) { return h('option', { value: n, text: n, selected: n === schedForm.file }); }));
      if (!schedForm.file && S.media.length) schedForm.file = S.media[0];
      var preset = h('input', { class: 'text-input mono', id: 'schedpreset', 'aria-label': 'Start script name', placeholder: 'startlessonce05', value: schedForm.preset, hidden: schedForm.action !== 'preset', autocomplete: 'off' });
      preset.addEventListener('input', function () { schedForm.preset = preset.value; });
      action.addEventListener('change', function () { schedForm.action = action.value; file.hidden = action.value !== 'play'; preset.hidden = action.value !== 'preset'; });
      file.addEventListener('change', function () { schedForm.file = file.value; });
      var label = h('input', { class: 'text-input', id: 'schedlabel', 'aria-label': 'Label (optional)', placeholder: 'Label (optional)', maxlength: 40, value: schedForm.label });
      label.addEventListener('input', function () { schedForm.label = label.value; });
      body.appendChild(h('div', { class: 'k', text: 'Add an entry' }));
      body.appendChild(time); body.appendChild(days); body.appendChild(action); body.appendChild(file); body.appendChild(preset); body.appendChild(label);
      body.appendChild(h('button', { class: 'btn on small', id: 'schedadd', text: 'Add entry', onclick: function () {
        if (!schedForm.days.length) return say('Choose at least one day.', true);
        var entry = { time: schedForm.time, days: schedForm.days.slice(), action: schedForm.action, label: schedForm.label };
        if (schedForm.action === 'play') { if (!schedForm.file) return say('Upload a clip first.', true); entry.file = schedForm.file; }
        if (schedForm.action === 'preset') { if (!schedForm.preset) return say('Type the start script name.', true); entry.preset = schedForm.preset.trim(); }
        save({ enabled: d.enabled, entries: d.entries.concat(entry) });
      } }));
    }
    api('GET', '/api/schedule').then(function (r) {
      if (!document.getElementById('schedcard')) return;
      if (!r.ok) { body.textContent = ''; body.appendChild(h('div', { class: 'k', id: 'schedmsg', text: r.data.error || 'Not available' })); return; }
      draw(r.data);
    });
    return card;
  }
  // ---- network (wired) ------------------------------------------------
  var netTimer = null;
  var netForm = { mode: 'dhcp', vals: {} };  // survives redraws of the System screen, so typing is never wiped
  var NET_FIELDS = ['netaddr', 'netprefix', 'netgw', 'netdns'];
  // Copy what is on screen into netForm just before anything is rebuilt. Relying on each field's input event
  // alone lost a value on a slow runner; reading the page at the moment of the redraw cannot miss one.
  function keepNetForm() {
    NET_FIELDS.forEach(function (id) {
      var el = document.getElementById(id);
      if (el && typeof el.value === 'string') { if (el.value) netForm.vals[id] = el.value; else delete netForm.vals[id]; }
    });
  }
  function clearNetForm() {
    netForm.vals = {};
    NET_FIELDS.forEach(function (id) { var el = document.getElementById(id); if (el) el.value = ''; });
  }
  var NET_MODES = [
    ['dhcp', 'Automatic (DHCP)', 'Take an address from a router.'],
    ['static', 'Fixed address', 'You choose the address. Use a range your other gear is on.'],
    ['linklocal', 'Direct cable', 'Laptop plugged straight into the box; no router. The box uses a 169.254.x.x address.'],
    ['share', 'Serve addresses', 'The box hands out addresses to whatever is plugged in.']
  ];
  function networkCard() {
    var body = h('div', { class: 'list', id: 'netbody' });
    var card = h('div', { class: 'card', id: 'netcard' }, h('h2', { text: 'Network (wired)' }), body);
    var mode = netForm.mode;
    var out = { iface: null, address: null, prefix: null, gateway: null, dns: null, secs: null, preview: null, msg: null };
    var mod = S.modules.filter(function (m) { return m.id === 'network'; })[0];
    if (!mod || !mod.enabled) {
      body.appendChild(h('div', { class: 'k', id: 'netmsg', text: 'Off. Switch on "Network settings (wired)" under Modules above (beta). Needs NetworkManager.' }));
      return card;
    }

    function refresh() {
      clearTimeout(netTimer);
      api('GET', '/api/network').then(function (r) {
        if (!document.getElementById('netcard')) return;
        if (!r.ok) { body.textContent = ''; body.appendChild(h('div', { class: 'k', id: 'netmsg', text: r.data.error || 'Not available' })); return; }
        draw(r.data);
      });
    }
    function value(el) { return el ? el.value.trim() : ''; }
    function config() {
      var c = { iface: value(out.iface), mode: mode, revert_seconds: parseInt(value(out.secs) || '60', 10) };
      if (mode === 'static' || mode === 'share') {
        if (value(out.address)) c.address = value(out.address);
        if (value(out.prefix)) c.prefix = parseInt(value(out.prefix), 10);
      }
      if (mode === 'static') {
        if (value(out.gateway)) c.gateway = value(out.gateway);
        c.dns = value(out.dns).split(/[ ,]+/).filter(Boolean);
      }
      return c;
    }
    function draw(d) {
      keepNetForm();
      body.textContent = '';
      d.interfaces.forEach(function (i) {
        body.appendChild(h('div', { class: 'item' },
          h('span', {}, i.name, h('br'), h('span', { class: 'k', text: (i.kind === 'wired' ? 'wired' : 'wi-fi') + ' \u00b7 ' + (i.carrier ? 'connected' : 'no link') + (i.speed_mbps ? ' \u00b7 ' + i.speed_mbps + ' Mbit/s' : '') })),
          h('span', { class: 'mono', text: (i.addresses || []).join(', ') || '-' })));
      });
      if (!d.helper) body.appendChild(h('div', { class: 'k', id: 'netmsg', text: 'The network helper (pvj-netd) is not running: changes cannot be applied. You can still preview them.' }));
      if (d.reverting) { body.appendChild(h('div', { class: 'msg err', id: 'netreverting', role: 'alert', text: 'Restoring the previous network. If this page stops responding, reconnect to the box at its old address.' })); netTimer = setTimeout(refresh, 2000); }
      if (d.pending) return drawPending(d.pending);
      var wired = d.interfaces.filter(function (i) { return i.kind === 'wired'; });
      if (!wired.length) return body.appendChild(h('div', { class: 'k', text: 'No wired network port found.' }));
      out.iface = h('select', { class: 'text-input', id: 'netiface', 'aria-label': 'Network port' }, wired.map(function (i) { return h('option', { value: i.name, text: i.name }); }));
      var modes = h('div', { class: 'row wrap', id: 'netmodes' });
      var help = h('div', { class: 'k', id: 'nethelp' });
      var fields = h('div', { class: 'list', id: 'netfields' });
      function remember(el) {
        if (netForm.vals[el.id]) el.value = netForm.vals[el.id];
        el.addEventListener('input', function () { netForm.vals[el.id] = el.value; });
      }
      function drawFields() {
        fields.textContent = '';
        NET_MODES.forEach(function (m) { if (m[0] === mode) help.textContent = m[2]; });
        if (mode === 'static' || mode === 'share') {
          out.address = h('input', { class: 'text-input mono', id: 'netaddr', 'aria-label': 'Address', placeholder: mode === 'share' ? '10.42.0.1' : '192.168.1.50', inputmode: 'decimal' });
          out.prefix = h('input', { class: 'text-input mono', id: 'netprefix', 'aria-label': 'Prefix length', placeholder: '24 (means 255.255.255.0)', inputmode: 'numeric' });
          remember(out.address); remember(out.prefix);
          fields.appendChild(out.address); fields.appendChild(out.prefix);
        }
        if (mode === 'static') {
          out.gateway = h('input', { class: 'text-input mono', id: 'netgw', 'aria-label': 'Gateway (optional)', placeholder: 'Gateway (optional)', inputmode: 'decimal' });
          out.dns = h('input', { class: 'text-input mono', id: 'netdns', 'aria-label': 'DNS servers (optional)', placeholder: 'DNS servers (optional)' });
          remember(out.gateway); remember(out.dns);
          fields.appendChild(out.gateway); fields.appendChild(out.dns);
        }
      }
      function drawModes() {
        modes.textContent = '';
        NET_MODES.forEach(function (m) {
          modes.appendChild(h('button', { class: 'btn small' + (m[0] === mode ? ' on' : ''), text: m[1], 'aria-pressed': m[0] === mode ? 'true' : 'false',
            onclick: function () { mode = netForm.mode = m[0]; drawModes(); drawFields(); } }));
        });
      }
      out.secs = h('select', { class: 'text-input', id: 'netsecs', 'aria-label': 'Revert automatically after' },
        [30, 60, 120, 300].map(function (n) { return h('option', { value: n, text: 'Revert after ' + n + ' s unless confirmed', selected: n === 60 }); }));
      out.preview = h('pre', { class: 'mono', id: 'netplan', hidden: true });
      out.msg = h('div', { class: 'msg', id: 'netresult', role: 'status' });
      drawModes(); drawFields();
      body.appendChild(out.iface); body.appendChild(modes); body.appendChild(help); body.appendChild(fields); body.appendChild(out.secs);
      body.appendChild(h('div', { class: 'row' },
        h('button', { class: 'btn small', id: 'netpreview', text: 'Preview commands', onclick: function () {
          api('POST', '/api/network/plan', config()).then(function (r) {
            out.preview.hidden = !r.ok; out.msg.className = 'msg' + (r.ok ? '' : ' err');
            out.msg.textContent = r.ok ? '' : (r.data.error || 'Invalid');
            if (r.ok) out.preview.textContent = r.data.commands.join('\n');
          });
        } }),
        h('button', { class: 'btn on small', id: 'netapply', text: 'Apply', onclick: function () {
          var c = config();
          api('POST', '/api/network/apply', c).then(function (r) {
            if (!r.ok) { out.msg.className = 'msg err'; out.msg.textContent = r.data.error || 'Could not apply'; return; }
            var where = (c.mode === 'static' || c.mode === 'share') ? ' If this page stops responding, open http://' + (c.address || '10.42.0.1') + ' and press Confirm before the timer runs out.'
              : ' If this page stops responding, find the box at its new address and press Confirm before the timer runs out.';
            S.netNote = 'Applied.' + where;
            clearNetForm();
            refresh();
          });
        } })));
      body.appendChild(out.preview); body.appendChild(out.msg);
      body.appendChild(h('div', { class: 'k', text: 'A change can cut this connection. It goes back by itself unless you confirm it, and also if the box restarts before you do.' }));
    }
    function drawPending(p) {
      body.appendChild(h('div', { class: 'card', id: 'netpending', role: 'alert' },
        h('div', { text: 'Waiting for your confirmation: ' + p.iface + ' \u2192 ' + p.mode }),
        h('div', { class: 'k', id: 'netleft', text: 'Reverts in ' + p.seconds_left + ' s' }),
        h('div', { class: 'k', text: S.netNote || '' }),
        h('div', { class: 'row' },
          h('button', { class: 'btn on', id: 'netconfirm', text: 'Confirm: keep this network', onclick: function () {
            api('POST', '/api/network/confirm', {}).then(function (r) { S.netNote = r.ok ? '' : (r.data.error || ''); refresh(); });
          } }),
          h('button', { class: 'btn', id: 'netrevert', text: 'Revert now', onclick: function () {
            api('POST', '/api/network/revert', {}).then(function () { S.netNote = ''; refresh(); });
          } }))));
      netTimer = setTimeout(refresh, 1000);
    }
    refresh();
    return card;
  }
  function oscCard() {
    var line = h('div', { class: 'k', id: 'oscline', text: 'Loading...' });
    var port = h('input', { class: 'text-input mono', type: 'number', min: 1024, max: 65535, 'aria-label': 'OSC port' });
    var allow = h('input', { class: 'text-input mono', 'aria-label': 'Extra allowed networks, comma separated', placeholder: 'Extra networks, e.g. 192.168.50.0/24' });
    var toggle = h('button', { class: 'btn', id: 'osctoggle', text: '...' });
    var current = null;
    function show(d) {
      current = d;
      line.textContent = d.error ? 'Problem: ' + d.error : (d.listening ? 'Listening on UDP ' + d.port + ' (' + d.received + ' messages received)' : 'Off');
      toggle.textContent = d.enabled ? 'OSC is on. Turn off' : 'Turn OSC on';
      toggle.className = 'btn' + (d.enabled ? ' on' : '');
      port.value = d.port;
      allow.value = d.allow.join(', ');
    }
    function push(patch) {
      act('POST', '/api/osc', patch, function (d) { say(''); show(d); });
    }
    api('GET', '/api/osc').then(function (r) { if (r.ok) show(r.data); else line.textContent = 'Not available'; });
    toggle.addEventListener('click', function () { if (current) push({ enabled: !current.enabled }); });
    var save = h('button', { class: 'btn small', text: 'Save port and networks', onclick: function () {
      var nets = allow.value.split(',').map(function (x) { return x.trim(); }).filter(Boolean);
      push({ port: parseInt(port.value, 10), allow: nets });
    } });
    return h('div', { class: 'card' }, h('h2', { text: 'Control (OSC)' }),
      h('div', { text: 'Off by default. Only private networks may send. Shutdown and reboot are never available over OSC.' }),
      line, toggle, h('label', { class: 'k', for: 'oscport', text: 'UDP port' }), (port.id = 'oscport', port), allow, save);
  }
  function appearanceCard() {
    var t = S.theme || {};
    var apply = function (name, accent) {
      act('POST', '/api/theme', { name: name, accent: accent }, function (d) {
        S.theme = d.theme;
        document.querySelector('link[href^="/theme.css"]').setAttribute('href', '/theme.css?v=' + Date.now());
        render();
      });
    };
    return h('div', { class: 'card' }, h('h2', { text: 'Appearance' }),
      h('div', { class: 'row wrap' }, S.themes.map(function (th) {
        return h('button', { class: 'btn small' + (th.id === t.name ? ' on' : ''), text: th.name, onclick: function () { apply(th.id, t.accent); } });
      })),
      h('div', { class: 'k', text: 'Accent' }),
      h('div', { class: 'swatches' },
        h('button', { class: 'btn small', text: 'Default', onclick: function () { apply(t.name, null); } }),
        ACCENTS.map(function (c) {
          var sw = h('button', { class: 'swatch' + (t.accent === c ? ' cur' : ''), 'aria-label': 'Accent ' + c, onclick: function () { apply(t.name, c); } });
          sw.style.background = c;
          return sw;
        })));
  }
  var accessForm = { pin: false, view: true, live: false, seconds: 300 };  // survives redraws
  var accessTimer = null;
  function accessCard() {
    var card = h('div', { class: 'card', id: 'accesscard' }, h('h2', { text: 'Access' }));
    var devices = h('div', { class: 'list' }, S.devices.map(function (d) {
      return h('div', { class: 'item' }, h('span', { text: d.name }), h('span', { class: 'row' }, h('span', { class: 'k', text: d.role }),
        h('button', { class: 'btn small', text: 'Remove', onclick: function () {
          act('POST', '/api/devices/revoke', { id: d.id }, function () { S.devices = S.devices.filter(function (x) { return x.id !== d.id; }); if (S.device && d.id === S.device.id) { S.device = null; } render(); });
        } })));
    }));
    var live = h('div', { class: 'list', id: 'accesslive' });
    function roleName(r) { return r === 'view' ? 'Guest (watch only)' : 'Presenter (play and mix)'; }
    function clock(sec) { var m = Math.floor(sec / 60), s2 = sec % 60; return m + ':' + (s2 < 10 ? '0' : '') + s2; }
    function drawLive(d) {
      live.textContent = '';
      var scr = d.screen;
      live.appendChild(h('div', { class: 'k', id: 'accessscreenline', text: scr.showing ? 'On the display now: ' + scr.items.map(function (i) { return i === 'pin' ? 'full access PIN' : roleName(i).toLowerCase(); }).join(', ') + ', hides in ' + clock(scr.seconds_left) : 'Nothing on the display.' }));
      var boxes = [['pin', 'Full access PIN (owner only)'], ['view', 'Guest code and QR (watch only)'], ['live', 'Presenter code and QR (play and mix)']].map(function (it) {
        var cb = h('input', { type: 'checkbox', id: 'show-' + it[0], checked: accessForm[it[0]] });
        cb.addEventListener('change', function () { accessForm[it[0]] = cb.checked; });
        return h('label', { class: 'row', for: 'show-' + it[0] }, cb, h('span', { text: it[1] }));
      });
      var secs = h('select', { class: 'text-input', id: 'showsecs', 'aria-label': 'Show for' },
        [[60, '1 minute'], [300, '5 minutes'], [900, '15 minutes'], [3600, '1 hour']].map(function (o) { return h('option', { value: o[0], text: 'Show for ' + o[1], selected: o[0] === accessForm.seconds }); }));
      secs.addEventListener('change', function () { accessForm.seconds = parseInt(secs.value, 10); });
      live.appendChild(h('div', { class: 'k', text: 'Show on the display' }));
      boxes.forEach(function (b) { live.appendChild(b); });
      live.appendChild(secs);
      live.appendChild(h('div', { class: 'row' },
        h('button', { class: 'btn on small', id: 'showaccess', text: scr.showing ? 'Show again' : 'Show on display', onclick: function () {
          var items = ['pin', 'view', 'live'].filter(function (i) { return accessForm[i]; });
          if (!items.length) return say('Choose what to show.', true);
          if (accessForm.pin && !window.confirm('Anyone who can see the display will see the full access PIN. Show it?')) return;
          act('POST', '/api/access/screen', { show: true, items: items, seconds: accessForm.seconds }, function (data) { say(''); drawLive(data); });
        } }),
        h('button', { class: 'btn small', id: 'hideaccess', text: 'Hide from display', disabled: !scr.showing, onclick: function () {
          act('POST', '/api/access/screen', { show: false }, function (data) { drawLive(data); });
        } })));
      live.appendChild(h('div', { class: 'k', text: 'Join codes (6 digits; they expire and work a limited number of times)' }));
      if (!d.codes.length) live.appendChild(h('div', { class: 'k', id: 'nocodes', text: 'None active.' }));
      d.codes.forEach(function (c) {
        live.appendChild(h('div', { class: 'item join-code', 'data-role': c.role },
          h('span', {}, roleName(c.role), h('br'), h('span', { class: 'mono big-code', text: c.code }), h('br'),
            h('span', { class: 'k', text: 'expires in ' + clock(c.seconds_left) + ' · ' + c.uses_left + ' uses left' })),
          h('img', { class: 'qr', alt: 'QR code for the ' + roleName(c.role).toLowerCase() + ' code', src: '/api/qr.svg?for=' + c.role + '&t=' + Date.now() }),
          h('button', { class: 'btn small', text: 'Cancel', onclick: function () { act('POST', '/api/access/cancel', { code: c.code }, drawLive); } })));
      });
      live.appendChild(h('div', { class: 'row wrap' },
        h('button', { class: 'btn small', id: 'newguest', text: 'New guest code', onclick: function () { act('POST', '/api/access/code', { role: 'view', minutes: 60 }, drawLive); } }),
        h('button', { class: 'btn small', id: 'newpresenter', text: 'New presenter code', onclick: function () { act('POST', '/api/access/code', { role: 'live', minutes: 60 }, drawLive); } }),
        h('button', { class: 'btn small', id: 'printsheet', text: 'Print access sheet', onclick: function () { printSheet(d); } })));
      clearTimeout(accessTimer);
      if (scr.showing || d.codes.length) accessTimer = setTimeout(function () { if (document.getElementById('accesscard')) refresh(); }, 5000);
    }
    function refresh() {
      // The check is after the answer arrives: on the first call the card is not on the page yet.
      api('GET', '/api/access').then(function (r) {
        if (!document.getElementById('accesscard')) return;
        if (r.ok) drawLive(r.data);
        else { live.textContent = ''; live.appendChild(h('div', { class: 'k', text: r.data.error || 'Not available' })); }
      });
    }
    var link = h('input', { class: 'text-input mono', readonly: true, 'aria-label': 'Guest link', hidden: true });
    var linkQr = h('img', { class: 'qr', id: 'linkqr', alt: 'QR code for the guest link', hidden: true });
    var role = h('select', { class: 'text-input', 'aria-label': 'Access level' }, h('option', { value: 'view', text: 'View only' }), h('option', { value: 'live', text: 'Live (play and mix)' }));
    var pinOut = h('div', { class: 'mono', id: 'pinout' });
    card.appendChild(h('div', { class: 'k', text: 'Paired devices' }));
    card.appendChild(devices);
    card.appendChild(live);
    card.appendChild(h('div', { class: 'k', text: 'Guest link that does not expire (until you remove it above)' }));
    card.appendChild(role);
    card.appendChild(h('button', { class: 'btn', text: 'Create guest link', onclick: function () {
      act('POST', '/api/devices/invite', { name: 'Guest (' + role.value + ')', role: role.value, origin: location.origin }, function (d) {
        link.value = location.origin + '/#token=' + d.token; link.hidden = false; link.select();
        if (d.qr_svg) { linkQr.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(d.qr_svg); linkQr.hidden = false; }
        api('GET', '/api/devices').then(function (r) { if (r.ok) S.devices = r.data.devices; });
      });
    } }));
    card.appendChild(link);
    card.appendChild(linkQr);
    card.appendChild(h('button', { class: 'btn', text: 'New PIN', onclick: function () { act('POST', '/api/pin/rotate', {}, function (d) { pinOut.textContent = 'New PIN: ' + d.pin; }); } }));
    card.appendChild(h('button', { class: 'btn', id: 'unlockpair', text: 'Unblock joining', onclick: function () {
      act('POST', '/api/pin/unlock', {}, function () { pinOut.textContent = 'Joining is open again (the PIN is unchanged).'; });
    } }));
    card.appendChild(pinOut);
    refresh();
    return card;
  }
  // A page to print and pin up in a studio: the panel address as a QR code (no access in it), plus the current guest
  // and presenter codes if any. Printed from the browser; nothing leaves the box.
  function printSheet(d) {
    var sheet = h('div', { id: 'printsheet' },
      h('h1', { text: 'nxlx.mastercontrol' }),
      h('p', { text: 'Scan with your phone camera to open the control panel. You need a code from the person running the show, or the PIN on the box.' }),
      h('div', { class: 'sheet-row' },
        h('figure', {}, h('img', { class: 'qr-big', alt: 'QR code for the control panel', src: '/api/qr.svg?for=panel' }), h('figcaption', { text: location.origin + '/' }))),
      d.codes.length ? h('div', { class: 'sheet-row' }, d.codes.map(function (c) {
        return h('figure', {}, h('img', { class: 'qr-big', alt: 'QR code for the ' + c.role + ' code', src: '/api/qr.svg?for=' + c.role + '&t=' + Date.now() }),
          h('figcaption', { text: (c.role === 'view' ? 'Guest (watch only)' : 'Presenter (play and mix)') + ': ' + c.code + ' (expires)' }));
      })) : null,
      h('p', { class: 'k', text: 'Codes shown here expire. The panel address above does not.' }));
    document.body.appendChild(sheet);
    document.body.classList.add('printing');
    var done = function () { document.body.classList.remove('printing'); if (sheet.parentNode) sheet.parentNode.removeChild(sheet); window.removeEventListener('afterprint', done); };
    window.addEventListener('afterprint', done);
    var imgs = sheet.querySelectorAll('img'), left = imgs.length;
    var go = function () { window.print(); setTimeout(done, 1000); };
    if (!left) return go();
    Array.prototype.forEach.call(imgs, function (im) { var fin = function () { if (--left === 0) go(); }; im.addEventListener('load', fin); im.addEventListener('error', fin); });
  }

  // ---- shell ----------------------------------------------------------
  function render() {
    clearTimeout(netTimer);
    clearTimeout(midiTimer);
    clearTimeout(accessTimer);
    clearTimeout(updateTimer);
    clearTimeout(healthTimer);
    clearTimeout(syncTimer);
    keepNetForm();
    app.textContent = '';
    if (!S.device) { app.appendChild(connect()); return; }
    var screens = { live: live, mix: mix, media: media, system: system };
    var tabs = h('nav', { class: 'tabs', 'aria-label': 'Sections' }, [['live', 'Live'], ['mix', 'Mix'], ['media', 'Media'], ['system', 'System']].map(function (t) {
      return h('button', { class: 'btn' + (S.tab === t[0] ? ' on' : ''), text: t[1], 'aria-current': S.tab === t[0] ? 'page' : false,
        onclick: function () { S.tab = t[0]; S.msg = ''; loadAll().then(render); } });
    }));
    app.appendChild(h('div', { class: 'shell' }, supportBanner(), screens[S.tab](), tabs));
    if (S.sheet) app.appendChild(sheet());
    patchLive();
  }
  function start() { loadAll().then(function () { if (S.device) render(); else render(); }); }

  // A guest link carries its token in the URL fragment, which is never sent to the server in a request line.
  function boot() {
    // A scanned QR code carries a join code or the box's PIN in the address fragment (never sent to any server);
    // keep it for the connect screen and take it out of the address bar and history at once.
    var scanned = /^#(code|pin)=([0-9]{4,6})$/.exec(location.hash);
    if (scanned) { S.scanned = { kind: scanned[1], value: scanned[2] }; history.replaceState(null, '', location.pathname); }
    var m = /^#token=([A-Za-z0-9_-]+)$/.exec(location.hash);
    var first = m ? api('POST', '/api/session', { token: m[1] }).then(function () { history.replaceState(null, '', location.pathname); }) : Promise.resolve();
    first.then(function () { return api('GET', '/api/status'); }).then(function (r) {
      if (r.ok) { S.device = r.data.device; S.status = r.data; return loadAll().then(render); }
      return api('GET', '/api/hello').then(function (h2) { S.remote = !!(h2.ok && h2.data.remote); render(); });
    });
    setInterval(poll, 1000);
  }
  boot();
})();
