// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
(function () {
  'use strict';

  var RANK = { view: 1, live: 2, full: 3 };
  var ACCENTS = ['#f59e0b', '#c2410c', '#22d3ee', '#e879f9', '#a3e635', '#ffffff'];
  var S = {
    tab: 'live', device: null, status: null, banks: [], bank: 0, media: [], modules: [], theme: null,
    themes: [], devices: [], editing: false, sheet: null, msg: '', msgErr: false, token: null, failures: 0,
    sys: null, sysData: {}, sysFresh: false,  // System: the page that is open (null: the index), what each row last answered
    sysFrom: null                             // the tab a page was opened from, when not from the index ('live': the Shaders link)
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
        if (method === 'POST' && r.ok) pageStateSoon();   // a System page's state line follows what was just changed
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
      if (r[4].ok) { S.theme = r[4].data.theme; S.themes = r[4].data.available; S.themeSkipped = r[4].data.skipped || []; S.accentDropped = r[4].data.accent_dropped || ''; markLook(); }
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
        if (window.pvjEffects) window.pvjEffects.patch(shaderCtx());       // the strip on Live and the card on Mix
        if (window.pvjRoom && window.pvjRoom.patch) window.pvjRoom.patch();
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
    var go = h('button', { class: 'btn on pri big', id: 'supportlogin', text: 'Sign in' });
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
  var supportAdv = false;   // whether "Advanced: support server settings" is open
  // Remote support. Staff see the state, how long, what support may do and one button. The support server, this
  // box's key and the list of past sessions are the owner's, under Advanced.
  function supportCard() {
    var body = h('div', { class: 'list sp', id: 'supportbody' }, h('div', { class: 'hint', text: 'Loading...' }));
    var card = h('div', { class: 'card', id: 'supportcard' }, h('h2', { text: 'Remote support' }), body);
    var timer = null;
    function refresh() { api('GET', '/api/support').then(function (r) { if (document.getElementById('supportcard') && r.ok) { if (asking(body)) { timer = setTimeout(refresh, 5000); return; } draw(r.data); } }); }
    function post(path, b) { return act('POST', path, b, function (data) { say(''); draw(data); poll(); }); }
    var ROLES = { full: 'check and change everything', live: 'play and mix only', view: 'watch only' };
    function draw(d) {
      clearTimeout(timer);
      body.textContent = '';
      if (d.active) timer = setTimeout(refresh, 5000);
      if (!d.config) {        // support itself, or a device without full access
        body.appendChild(h('div', { class: 'state', id: 'supportline', text: d.active ? 'A support session is open, ' + mins(d.seconds_left) + ' left.' : 'No support session is open.' }));
        return;
      }
      var c = d.config;
      body.appendChild(h('div', { class: 'state', id: 'supportline', text: d.active ? 'A support session is open, ' + mins(d.seconds_left) + ' left. Support may ' + ROLES[d.role] + '.' :
        !c.allowed ? 'Remote support is off. Nothing can reach this box from outside until you switch it on above and start a session.' :
        d.configured ? 'Ready. No session is open, and nothing can reach this box until you start one.' : 'Switched on, but not set up yet.' }));
      if (d.available === false) body.appendChild(h('div', { class: 'hint warn', id: 'supportwhy', text: 'It cannot start on this box: ' + (d.why || 'a part it needs is missing') + '. Tell whoever looks after the box.' }));
      if (d.active) {
        body.appendChild(h('div', { class: 'hint', text: 'Read this code to your support contact. They open http://' + d.address + '/ through the support connection and type it.' }));
        body.appendChild(h('div', { class: 'support-code', id: 'supportcodeshow', text: d.code }));
        body.appendChild(h('div', { class: 'state', id: 'supportstate', text: (d.connected ? 'Connected to the support server' : 'Waiting for the support server...') +
          ' · support signed in ' + d.logins + ' of ' + d.max_logins + ' times' }));
        var ext = h('select', { class: 'text-input', id: 'supportextend' }, d.durations.map(function (m) { return h('option', { value: String(m), text: m + ' minutes from now', selected: m === 60 }); }));
        body.appendChild(h('div', { class: 'fieldwrap' }, h('label', { class: 'field', for: 'supportextend', text: 'Change how long it stays open' }),
          h('div', { class: 'row' }, ext, h('button', { class: 'btn', id: 'supportextendbtn', text: 'Set time', onclick: function () { post('/api/support/extend', { minutes: +ext.value }); } }))));
        body.appendChild(h('div', { class: 'row' }, h('button', { class: 'btn on pri grow', id: 'supportstop', text: 'Stop the session now', onclick: function () { post('/api/support/stop', {}); } })));
        return;
      }
      if (c.allowed && d.configured) {
        var dur = h('select', { class: 'text-input', id: 'supportminutes' }, d.durations.map(function (m) { return h('option', { value: String(m), text: m < 60 ? m + ' minutes' : (m / 60) + ' hour' + (m > 60 ? 's' : ''), selected: m === 60 }); }));
        var role = h('select', { class: 'text-input', id: 'supportrole' },
          [['full', 'Everything: support can check and change settings'], ['live', 'Play and mix only'], ['view', 'Watch only']].map(function (o) { return h('option', { value: o[0], text: o[1] }); }));
        body.appendChild(h('div', { class: 'hint', text: 'Start a session when support asks for one. It closes by itself when the time is up, and you can stop it at any moment.' }));
        body.appendChild(labelled('How long', dur));
        body.appendChild(labelled('What support may do', role));
        body.appendChild(h('div', { class: 'row' }, h('button', { class: 'btn on pri grow', id: 'supportstart', text: 'Start support session', onclick: function (e) {
          e.target.disabled = true; e.target.textContent = 'Connecting...';
          post('/api/support/start', { confirm: 'start', minutes: +dur.value, role: role.value }).then(function (r) { if (!r.ok) draw(d); });
        } })));
      } else if (c.allowed) {
        body.appendChild(h('div', { class: 'hint warn', id: 'supportnotset', text: 'Not set up yet. The owner fills in the support server under Advanced.' }));
      }
      // Advanced: the support server (filled in once, at the studio), this box's key, past sessions
      var f = supportForm || { endpoint: c.endpoint, server_key: c.server_key, address: c.address, network: c.network };
      var fields = [['endpoint', 'Support server', 'support.example.com:51820', 'Its name or address, a colon, and its port.'],
        ['server_key', 'Support server key', '', 'The 44 characters your support team gave you.'],
        ['address', 'This box on the support network', '10.77.0.5', 'The address your support team gave this box.'],
        ['network', 'Support network', '10.77.0.0/24', 'The range support connects from. The address above must be inside it.']];
      var inputs = {}, inner = h('div', { class: 'list sp' });
      var bar = saveBar('supportsave', 'Save changes', function () {
        var b = {}; fields.forEach(function (x) { b[x[0]] = inputs[x[0]].value.trim(); });
        api('POST', '/api/support/config', b).then(function (r) {
          if (!r.ok) return bar.say((r.data.error || 'Could not save') + '. Nothing was changed.', true);
          supportForm = null; draw(r.data); poll(); say('Saved');
          var out = document.getElementById('supportsaveresult');
          if (out) out.textContent = 'Saved';
        });
      });
      fields.forEach(function (x) {
        inputs[x[0]] = h('input', { class: 'text-input mono', id: 'support-' + x[0], placeholder: x[2], value: f[x[0]] || '', autocomplete: 'off' });
        inputs[x[0]].addEventListener('input', function () { supportForm = supportForm || Object.assign({}, f); supportForm[x[0]] = inputs[x[0]].value; bar.dirty(true); });
        inner.appendChild(labelled(x[1], inputs[x[0]], x[3]));
      });
      inner.appendChild(bar.el);
      bar.dirty(!!supportForm);
      if (d.public_key) {
        var key = h('input', { class: 'text-input mono', id: 'supportkey', readonly: true, value: d.public_key });
        inner.appendChild(h('div', { class: 'fieldwrap' }, h('label', { class: 'field', for: 'supportkey', text: 'This box\'s key' }),
          h('div', { class: 'row' }, key, h('button', { class: 'btn', text: 'Copy', onclick: function () {
            key.select(); (navigator.clipboard ? navigator.clipboard.writeText(d.public_key) : Promise.reject()).then(function () { say('Key copied.'); }, function () { say('Select the key and copy it.'); });
          } })),
          h('div', { class: 'hint', text: 'Give it to your support team once, so their server knows this box.' })));
      }
      if (d.log && d.log.length) {
        inner.appendChild(h('div', { class: 'field', text: 'Recent sessions' }));
        d.log.slice(0, 5).forEach(function (e) {
          inner.appendChild(h('div', { class: 'item' }, h('span', { text: new Date(e.started * 1000).toLocaleString() + ' · ' + e.by + ' · ' + e.minutes + ' min · ' + e.role +
            (e.ended ? ' · ' + (e.reason || 'ended') + ' · ' + (e.logins || 0) + ' sign-ins' : '') })));
        });
      }
      var adv = h('details', { class: 'fold', id: 'supportadv', open: supportAdv }, h('summary', { text: 'Advanced: support server settings' }), inner);
      adv.addEventListener('toggle', function () { supportAdv = adv.open; });
      body.appendChild(adv);
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
    // On a laptop Live uses the width: the transport and the pads on the left, the playing shader's strip on the right
    // (the strip is there only while a shader is on; on a phone it sits under the Vibes row).
    return h('div', { class: 'screen' },
      h('div', { class: 'top' }, h('h1', { text: 'nxlx.mastercontrol' }), h('div', { class: 'pill k', id: 'pill' })),
      h('div', { class: 'livecols', id: 'livecols' },
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
      window.pvjShaders ? window.pvjShaders.liveRow(shaderCtx()) : null,
      window.pvjShaders ? window.pvjShaders.liveStrip(shaderCtx()) : null,
      window.pvjEffects ? window.pvjEffects.liveStrip(shaderCtx()) : null,
      previewBlock(),
      h('div', { class: 'banks' }, S.banks.map(function (b, i) {
        return h('button', { class: 'btn' + (i === S.bank ? ' on' : ''), text: b.name.replace('Bank ', 'Bank '), 'aria-pressed': i === S.bank ? 'true' : 'false',
          onclick: function () { S.bank = i; render(); } });
      })),
      pads,
      can('full') ? h('button', { class: 'btn small', text: S.editing ? 'Done editing' : 'Edit pads', onclick: function () { S.editing = !S.editing; render(); } }) : null,
      h('div', { id: 'msg', class: 'msg' + (S.msgErr ? ' err' : ''), role: 'status', text: S.msg })),
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
    var note = h('div', { class: 'hint', id: 'previewmsg', hidden: true });
    var btn = h('button', { class: 'btn small', id: 'previewbtn', text: 'Take snapshot' });
    function tell(text) { img.hidden = true; note.hidden = false; note.textContent = text; btn.disabled = false; }
    img.addEventListener('load', function () { note.hidden = true; img.hidden = false; btn.disabled = false; });
    img.addEventListener('error', function () { tell('No picture: the player gave something that is not a picture. Try again in a moment.'); });
    // Asked with fetch, so the answer's status can be read: an idle player is not an error. The picture is then
    // shown from a data: address (the page's policy allows no blob: pictures).
    btn.addEventListener('click', function () {
      btn.disabled = true; note.hidden = false; note.textContent = 'Taking a snapshot...';
      fetch('/api/preview.jpg?t=' + Date.now(), { credentials: 'same-origin' }).then(function (r) {
        if (r.status === 409) return tell('Nothing is on the screen right now.');
        if (!r.ok) return tell('No picture: the player is not running or could not make one. Try again in a moment.');
        return r.blob().then(function (blob) {
          var reader = new FileReader();
          reader.onload = function () { img.src = reader.result; };
          reader.onerror = function () { tell('No picture: it could not be read. Try again.'); };
          reader.readAsDataURL(blob);
        });
      }, function () { tell('No picture: no connection to the box.'); });
    });
    return h('div', { class: 'card', id: 'previewcard' },
      h('div', { class: 'row between' }, h('div', { class: 'k', text: 'Screen' }), btn),
      h('div', { class: 'hint', text: 'A snapshot briefly stalls playback, so it only happens when you tap.' }),
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
  // Shaders and Vibes lives in shaders.js; it borrows these helpers.
  function shaderCtx() {
    return { h: h, api: api, act: act, can: can, say: say, poll: poll, moduleOn: moduleOn, state: S, confirmRow: confirmRow,
      rowShown: function (id) { return sysRows().some(function (r) { return r.id === id && rowShown(r); }); },
      // a feature's one switch, used where its controls are shown on another page (MIDI and DMX on the Shaders page)
      switchFeature: function (id, on) {
        var row = sysRows().filter(function (r) { return r.id === id; })[0];
        S.sysFresh = false;
        return runSwitch([moduleStep(row.module)].concat(row.steps || []), on, row.offInner, null);
      },
      // An answer of the box said this module is off while its page still shows it as on (switched off on another
      // device, or the answer overtook this page's own switch): the modules are read again and the page follows.
      moduleSaidOff: function () {
        api('GET', '/api/modules').then(function (r) {
          if (!r.ok) return;
          S.modules = r.data.modules;
          if (S.tab === 'system' && S.sys && pageSwitch.steps && pageOn(pageSwitch.steps) !== pageSwitch.on) redrawSystem();
        });
      },
      openShaders: function () { openSys('vibes', S.tab); },
      openMix: function () { goTab('mix'); } };
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
    if (window.pvjShaders) window.pvjShaders.patch(shaderCtx(), pl, np);
    if (pl.effect && window.pvjEffects && pl.running && typeof pl.shader !== 'string') np.textContent += ' \u00b7 effect: ' + window.pvjEffects.nice(pl.effect);
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
      window.pvjEffects ? window.pvjEffects.mixCard(shaderCtx()) : null,
      h('div', { class: 'card' },
        h('div', { class: 'k', text: 'Transition between clips' }),
        choice([{ label: 'Cut', value: 'cut' }, { label: 'Dip to black', value: 'dip' }, { label: 'Crossfade (soon)', value: 'x', disabled: true }],
          m.transition, function (v) { setMix({ transition: v }); }),
        h('div', { class: 'k', text: 'Duration' }),
        choice([0.5, 1, 2, 5].map(function (d) { return { label: d + 's', value: d }; }), m.duration, function (v) { setMix({ duration: v }); })),
      h('div', { class: 'card' },
        h('div', { class: 'k sent', text: 'Mirror (for rear projection or a mirror rig; costs the box some work)' }),
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
    var card = h('div', { class: 'card', id: 'overlaycard' }, h('div', { class: 'k sent', text: 'Overlay picture (logo or mask over the video)' }), body);
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
    var body = h('div', { class: 'list', id: 'mapbody' }, h('div', { class: 'hint', text: 'Loading...' }));
    var card = h('div', { class: 'card', id: 'mapcard' }, h('div', { class: 'k', text: 'Projection mapping (beta)' }), body);
    var mod = S.modules.filter(function (m) { return m.id === 'mapper'; })[0];
    if (!mod || !mod.enabled) {
      body.textContent = '';
      body.appendChild(h('div', { class: 'hint', id: 'mapmsg', text: 'Off. Switch it on under System, Projection mapping (beta).' }));
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
    function send(b, done) {        // done(): the box took it; called before the card is drawn again
      return mapApi('POST', b).then(function (r) {
        if (r.ok && done) done();
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
    // Every answer rebuilds the card, and one can arrive while a name is being typed or the canvas is being moved
    // with the arrow keys: the control in use keeps the cursor, and a field what was typed into it.
    function draw(data) { keepCursor(body, function () { build(data); }); }
    function build(data) {
      d = data;
      body.textContent = '';
      var st = d.status, s = selected();
      var words = { off: 'Mapping is off', building: 'Preparing the mapped picture...', on: 'Mapping is on', editing: 'Editing on the display', error: 'Problem: ' + st.message };
      body.appendChild(h('div', { class: 'hint', id: 'mapstatus', text: (words[st.state] || st.state) + ' · screen ' + d.screen[0] + 'x' + d.screen[1] + ' · ' + d.surfaces.length + ' surface' + (d.surfaces.length === 1 ? '' : 's') }));
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
        body.appendChild(h('div', { class: 'hint', id: 'mapsel', text: 'Chosen: ' + s.name + (mapUi.whole ? ', the whole surface' : ', corner ' + (d.edit.corner + 1) + ' of ' + n) }));
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
          body.appendChild(h('div', { class: 'hint', text: 'Changing the grid size spreads the points evenly again.' }));
        }
      }
      if (d.surfaces.length) body.appendChild(h('div', { class: 'hint', text: 'Surfaces (the first is on top)' }));
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
      body.appendChild(h('div', { class: 'hint', text: 'Saved mappings (' + d.sets.length + ' of ' + d.limits.sets + ')' }));
      if (d.sets.length) {
        var pick = h('select', { class: 'text-input', id: 'mapsets', 'aria-label': 'Saved mapping' }, d.sets.map(function (n) { return h('option', { value: n, text: n }); }));
        body.appendChild(h('div', { class: 'row' }, pick,
          h('button', { class: 'btn small', id: 'mapload', text: 'Load', onclick: function () { send({ action: 'load', name: pick.value }); } }),
          h('button', { class: 'btn small', id: 'mapdelete', text: 'Delete', onclick: function () { send({ action: 'delete', name: pick.value }); } })));
      }
      // The name being typed lives in mapUi.name (every keystroke), so each rebuild starts from it and keepCursor
      // leaves the text alone (data-kept). It is emptied when the box has taken the Save and BEFORE the card is
      // drawn again: emptied afterwards, a field that still had the cursor (a tapped button does not take it on
      // Safari) showed the saved name again while a second Save would have sent an empty one.
      var setName = h('input', { class: 'text-input', id: 'mapsetname', 'aria-label': 'Name for this mapping', placeholder: 'Name, e.g. Main stage', maxlength: 40, value: mapUi.name, 'data-kept': 'card' });
      setName.addEventListener('input', function () { mapUi.name = setName.value; });
      body.appendChild(h('div', { class: 'row' }, setName, h('button', { class: 'btn small', id: 'mapsave', text: 'Save',
        onclick: function () { send({ action: 'save', name: mapUi.name.trim() }, function () { mapUi.name = ''; }); } })));
      body.appendChild(h('div', { class: 'hint', text: 'Masks: use the overlay picture above (a PNG, black where no light should fall). Map at 1920x1080 or less on a Pi 4; at 2560x1440 it drops frames.' }));
      if (body.isConnected) paint();     // at once, so the canvas has its height and nothing below it jumps for a frame under a finger
      requestAnimationFrame(paint);
      watch();
    }
    mapApi('GET').then(function (r) {
      if (!document.getElementById('mapcard') || r.stale) return;
      if (r.ok) draw(r.data); else { body.textContent = ''; body.appendChild(h('div', { class: 'hint', text: r.data.error || 'Not available' })); }
    });
    return card;
  }

  // ---- media ----------------------------------------------------------
  // Live input: an HDMI capture stick or a webcam on USB, shown like a clip (the old panel's camera livefeed).
  function liveInputCard() {
    if (!can('live')) return null;
    var body = h('div', { class: 'list', id: 'inputbody' });
    var card = h('div', { class: 'card', id: 'inputcard', hidden: true }, h('div', { class: 'k sent', text: 'Live input (USB capture or camera)' }), body);
    api('GET', '/api/inputs').then(function (r) {
      if (!document.getElementById('inputcard') || !r.ok || !r.data.devices.length) return;
      card.hidden = false;
      var d = r.data;
      var dev = h('select', { class: 'text-input', id: 'inputdev', 'aria-label': 'Input' }, d.devices.map(function (x) { return h('option', { value: x.id, text: x.name + ' (' + x.id + ')' }); }));
      var mode = h('select', { class: 'text-input', id: 'inputmode', 'aria-label': 'Picture size' }, d.modes.map(function (m) { return h('option', { value: m, text: m }); }));
      body.appendChild(dev); body.appendChild(mode);
      body.appendChild(h('button', { class: 'btn on pri small', id: 'inputshow', text: d.running ? 'Show again' : 'Show live input', onclick: function () {
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
          full ? h('button', { class: 'btn small', text: 'Rename', onclick: function (e) {      // in place: no browser dialog
            var row = e.currentTarget.closest('.row');
            var to = h('input', { class: 'text-input', 'aria-label': 'New name for ' + d.name, value: d.name, autocomplete: 'off' });
            function back() { if (box.parentNode) box.parentNode.removeChild(box); row.hidden = false; }
            var box = h('div', { class: 'confirm renamebox' }, h('span', { text: 'New name' }), to, h('div', { class: 'row' },
              h('button', { class: 'btn on pri grow', text: 'Rename', onclick: function () {
                var name = to.value.trim();
                if (!name || name === d.name) return back();
                act('POST', '/api/media/rename', { name: d.name, new: name }, refreshMedia);
              } }),
              h('button', { class: 'btn grow', text: 'Cancel', onclick: back })));
            row.hidden = true;
            row.parentNode.insertBefore(box, row.nextSibling);
            to.focus();
          } }) : null,
          full ? h('button', { class: 'btn small del', text: 'Delete', onclick: function (e) {
            confirmRow('Delete ' + d.name + '? The file is removed from the box.', 'Delete', 'Keep it', function () {
              act('POST', '/api/media/delete', { name: d.name }, refreshMedia);
            }, e.currentTarget);
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
        h('button', { class: 'btn on pri small', id: 'slidestart', text: 'Start slideshow', onclick: function () {
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
        picker, h('button', { class: 'btn on pri', id: 'uploadbtn', text: 'Upload clips', onclick: function () { picker.click(); } }), uploads) : null,
      h('div', { class: 'card' }, h('div', { class: 'list' }, items.length ? items : h('div', { class: 'k', text: 'No clips yet. Upload some, or play them straight from a USB drive.' }))),
      (info.usb || []).map(function (drive) {
        return h('div', { class: 'card usb-drive', 'data-drive': drive.drive },
          h('div', { class: 'k sent', text: 'USB drive: ' + drive.drive + ' (read only, plays straight from the drive)' }),
          info.autostart_usb ? h('div', { class: 'k', text: 'Autostart is set to play USB sticks: plugging one in starts it, even during a show.' }) : null,
          h('div', { class: 'list' }, drive.files.length ? drive.files.map(function (f) {
            var have = S.media.indexOf(f.name) >= 0;
            return h('div', { class: 'item' },
              h('span', {}, f.name, h('br'), h('span', { class: 'k', text: megabytes(f.size) + (have ? ' \u00b7 also on the box' : '') })),
              h('span', { class: 'row' },
                h('button', { class: 'btn small', text: 'Play', 'aria-label': 'Play ' + f.name + ' from USB', disabled: !can('live'),
                  onclick: function () { act('POST', '/api/play', { usb: drive.drive + '/' + f.name }, function () { say('Playing ' + f.name); poll(); }); } }),
                can('full') ? h('button', { class: 'btn small', text: have ? 'Copy again' : 'Copy to the box', 'aria-label': 'Copy ' + f.name + ' to the box',
                  onclick: function (e) {
                    function copy() {
                      S.importNote = '';
                      act('POST', '/api/media/import', { usb: drive.drive + '/' + f.name, replace: have }, function (d) { watchImport(d); });
                    }
                    if (!have) return copy();
                    confirmRow(f.name + ' is already on the box. Replace it with the one on the USB drive?', 'Replace', 'Keep it', copy, e.currentTarget);
                  } }) : null));
          }) : h('div', { class: 'k', text: 'No video or image files at the top of this drive.' })));
      }),
      h('div', { class: 'k', id: 'importline', role: 'status' }),
      h('div', { id: 'msg', class: 'msg' + (S.msgErr ? ' err' : ''), role: 'status', text: S.msg }));
  }

  // ---- system ---------------------------------------------------------
  // A short index of rows in three groups, and one page per row. S.sys is the open page (null: the index).
  // Each row can say how it is doing (a chip and a sentence), worked out from the same GET calls the cards use.
  var confirmTimer = null, pageStateTimer = null;
  var CHIPS = { off: 'Off', setup: 'Set up', ready: 'Ready', active: 'Active', problem: 'Problem', check: 'Check' };
  var SYS_GROUPS = [['everyday', 'Everyday'], ['show', 'Show tools'], ['box', 'This box']];
  function mod(id) { return S.modules.filter(function (x) { return x.id === id; })[0]; }
  function plural(n, word, many) { return n + ' ' + (n === 1 ? word : (many || word + 's')); }
  function st(chip, text) { return { chip: chip, text: text }; }
  function goTab(tab) {
    if (history.state && history.state.sys) history.replaceState(null, '');   // the page left behind is not one to come back to
    S.tab = tab; S.msg = ''; S.sys = null; S.sysFresh = false; S.sysFrom = null;
    loadAll().then(render);
  }
  // The Room screen's own cards, on the Room page under its switch: the page never sends anyone to another screen.
  // The screen's title and message line are the page's, so the copies are taken out.
  function roomInPage() {
    var el = window.pvjRoom.screen(roomCtx());
    ['.top', '#msg'].forEach(function (sel) { var x = el.querySelector(sel); if (x && x.parentNode === el) el.removeChild(x); });
    el.className = 'roominpage';
    return el;
  }
  function sysRows() {
    var full = can('full'), remote = !!(S.device && S.device.remote);
    var rows = [
      { id: 'health', group: 'top', name: 'Health', role: 'view', url: '/api/health',
        blurb: 'Whether the box is well: its power supply, temperature, the player and the helpers it needs, and each projector\'s own warnings.',
        body: function () { return [healthCard()]; } },
      { id: 'projectors', group: 'everyday', name: 'Projectors', role: 'live', module: 'projector', url: '/api/projectors',
        blurb: 'Switch the projectors on the network on and off, choose their input, mute them, and see their state, lamp hours and warnings. Not yet tried on a real projector.',
        confirmOff: function (ask) { ask('The box stops checking the projectors. They stay as they are.'); },
        body: function () { return [projectorsCard(full)]; } },
      { id: 'room', group: 'everyday', name: 'Room', role: 'live', module: 'room', url: '/api/room',
        blurb: 'For the people who run the room: scenes to tap, each wall on or off, its source and its mutes, and All off. It needs Projectors to be on. Not yet tried on a real projector.',
        body: function () { return window.pvjRoom ? [roomInPage()] : []; } },
      { id: 'schedule', group: 'everyday', name: 'Schedule', role: 'full', module: 'scheduler', url: '/api/schedule',
        blurb: 'Play a clip, stop, black out or show the screen, start Vibes or switch the projectors at set times on chosen days. It uses the box\'s clock, so check the clock first.',
        steps: [scheduleStep()], offInner: true,
        body: function () { return [scheduleCard()]; } },
      { id: 'vibes', group: 'everyday', name: 'Shaders and Vibes', role: 'view', module: 'shaders', url: '/api/shaders',
        blurb: 'Moving pictures the box draws by itself, in place of a clip. Vibes plays them one after another, for as long as you like. Play one yourself and it is an instrument: its controls, presets and a controller are all on this page.',
        confirmOff: function (ask) {
          var pl = (S.status && S.status.player) || {};
          ask(pl.vibes || typeof pl.shader === 'string' ? 'Vibes is on the screen. Switching off stops it now.' : null);
        },
        body: function () { return window.pvjShaders ? [window.pvjShaders.page(shaderCtx())] : []; } },
      // A presenter gets this row too (D48): the guest code, to make, show on the room screen and end, and nothing else.
      { id: 'access', group: 'everyday', name: 'People and codes', role: 'live', url: '/api/access', urlRole: 'live',
        blurb: full ? 'The phones and tablets paired with this box, codes for guests and presenters, and the PIN.' :
          'Let a guest watch from their own phone, with a code that stops working by itself.',
        body: accessCards },
      { id: 'sound', group: 'everyday', name: 'Sound', role: 'live', url: '/api/audio',
        blurb: 'Where the sound comes out, and a test tone to check left and right.',
        body: function () { return [audioCard(full)]; } },
      { id: 'autostart', group: 'show', name: 'At power-up', role: 'full', url: '/api/autostart',
        blurb: 'What the box plays by itself when it is powered up, with nobody at the panel.',
        body: function () { return [autostartCard(full)]; } },
      { id: 'streams', group: 'show', name: 'Streams', role: 'live', module: 'inputs-srt', url: '/api/streams',
        blurb: 'Save the addresses of network video streams (SRT, RTSP, RTMP) and play them like clips.',
        body: function () { return [streamsCard(full)]; } },
      { id: 'mapping', group: 'show', name: 'Projection mapping', role: 'full', module: 'mapper', url: '/api/mapper',
        blurb: 'Bend the picture onto walls and objects: four-cornered shapes, triangles and grids for curved screens, up to 16, drawn with outlines on the display while you place them.',
        confirmOff: function (ask) { api('GET', '/api/mapper').then(function (r) { ask(r.ok && r.data.on ? 'The mapping comes off the screen now.' : null); }); },
        body: function () { return [mapperCard()]; } },
      { id: 'sync', group: 'show', name: 'Boxes in step', role: 'live', module: 'wall', url: '/api/sync',
        blurb: 'Several boxes play together: one server leads and the clients follow its clip, position, pause and blackout. Each box can also show one tile of a video wall.',
        confirmOff: function (ask) { api('GET', '/api/sync').then(function (r) { ask(r.ok && r.data.config.role !== 'off' ? 'The other boxes stop following.' : null); }); },
        body: function () { return [syncCard()]; } },
      { id: 'midi', group: 'show', name: 'MIDI controller', role: 'full', module: 'control-midi', url: '/api/midi', urlRole: 'full',
        blurb: 'Play pads, fade and mix from a USB pad controller, keyboard or fader box. A controller the box knows works as soon as it is plugged in. The box only listens; it sends nothing back.',
        steps: [flagStep('midi', '/api/midi', function (d) { return d.enabled; }, function (v) { return { enabled: v }; })],
        body: function () { return [midiControllers(), midiCard()]; } },
      { id: 'dmx', group: 'show', name: 'DMX lighting desk', role: 'full', module: 'control-dmx', url: '/api/dmx', urlRole: 'full',
        blurb: 'Control the box from a lighting desk or lighting software over the network (Art-Net or sACN): opacity, size, position, speed, volume, blackout and pads. The box only listens.',
        steps: [flagStep('dmx', '/api/dmx', function (d) { return d.enabled; }, function (v) { return { enabled: v }; })],
        body: dmxCard },
      { id: 'osc', group: 'show', name: 'OSC', role: 'full', url: '/api/osc',
        blurb: 'Control the box from TouchOSC, QLab, Resolume and other programs that send OSC messages over the network.',
        steps: [flagStep('osc', '/api/osc', function (d) { return d.enabled; }, function (v) { return { enabled: v }; })],
        body: function () { return [oscCard()]; } },
      { id: 'network', group: 'box', name: 'Network', role: 'full', module: 'network', url: '/api/network', urlRole: 'full',
        blurb: 'Change the box\'s wired and Wi-Fi network: automatic, a fixed address, a direct cable, handing out addresses, joining a Wi-Fi network or making its own hotspot. Every change goes back by itself unless you confirm it.',
        body: function () { return [networkCard()]; } },
      { id: 'updates', group: 'box', name: 'Updates', role: 'full', url: '/api/system',
        blurb: 'Install a newer version from a USB stick or an upload. Only updates signed with your key are taken, and a failed one goes back by itself.',
        body: function () { return [updateCard()]; } },
      { id: 'support', group: 'box', name: 'Remote support', role: 'full', url: '/api/support',
        blurb: 'Let someone you trust help from far away, for a set time. Nothing can reach the box until you allow it and start a session.',
        // the settings are filled in before it is allowed, so the card shows while the switch is off; a support
        // session itself may not change this (the server refuses), so it gets no switch
        steps: full && !remote ? [flagStep('support', '/api/support', function (d) { return !!(d.config && d.config.allowed); }, function (v) { return { allowed: v }; }, '/api/support/config')] : null,
        bodyWhenOff: true,
        body: function () { return [supportCard()]; } },
      { id: 'backup', group: 'box', name: 'Backup and reset', role: 'full',
        fact: function () { return remote ? 'Settings file, diagnostics' : 'Settings file, diagnostics, factory reset'; },
        blurb: 'Save or load this box\'s settings as a file, make a file for whoever is helping you, or start over.',
        body: function () {
          var cards = boxCareCards(), last = cards[cards.length - 1];
          if (last && last.id === 'resetcard') cards.splice(cards.length - 1, 0, h('h2', { class: 'danger-h', id: 'dangerhead', text: 'Danger' }));
          return cards;
        } },
      { id: 'look', group: 'box', name: 'Look', role: 'full',
        fact: function () { var t = S.themes.filter(function (x) { return S.theme && x.id === S.theme.name; })[0]; return t ? t.name : ''; },
        blurb: 'The panel\'s colours, on every paired device.',
        body: function () { return [appearanceCard()]; } },
      { id: 'about', group: 'box', name: 'About and power', role: 'view', url: '/api/system',
        blurb: 'What this box is, its versions, storage, screens and clock, and restarting it.',
        body: aboutPage }
    ];
    // A module nobody gave a row still gets one, so it can always be switched.
    var known = rows.map(function (r) { return r.module; });
    S.modules.forEach(function (m) {
      if (m.type === 'core' || m.status !== 'ready' || known.indexOf(m.id) >= 0) return;
      rows.push({ id: 'mod-' + m.id, group: 'show', name: m.name, role: 'full', module: m.id, blurb: m.description });
    });
    return rows;
  }
  function rowShown(row) {
    if (row.id === 'access' && S.device && S.device.remote) return false;      // codes are refused through the support connection, whatever the role
    if (!can(row.role) && !(row.id === 'support' && S.device && S.device.remote)) return false;
    if (!row.module) return true;
    var m = mod(row.module);
    if (!m || m.status !== 'ready' || !m.supported) return false;
    return can('full') || m.enabled;        // nobody gets a row they cannot use
  }
  function rowFetchable(row) { return !!row.url && can(row.urlRole || 'view') && (!row.module || moduleOn(row.module)); }

  // -- what each row says about itself --
  function schedWhat(e) {
    if (e.action === 'scene') return 'Scene ' + (window.pvjRoom ? window.pvjRoom.sceneName(e.scene) : e.scene);
    return e.action === 'play' ? 'Play ' + e.file : e.action === 'preset' ? 'Old start script ' + e.preset :
      ({ stop: 'Stop playing', blackout: 'Screen to black', show: 'Screen back on', projector_on: 'Projectors on', projector_off: 'Projectors off', vibes: 'Start Vibes' })[e.action] || e.action;
  }
  // The next entry by the box's own clock (days count from Monday): { off: days from today, e: the entry }, or null.
  function schedNextEntry(d) {
    var m = /^(\d+)-(\d+)-(\d+) (\d+):(\d+)/.exec(d.now || '');
    if (!m) return null;
    var today = (new Date(+m[1], +m[2] - 1, +m[3]).getDay() + 6) % 7, nowMin = +m[4] * 60 + +m[5], best = null;
    d.entries.forEach(function (e) {
      var t = /^(\d+):(\d+)$/.exec(e.time || '');
      if (!t) return;
      var at = +t[1] * 60 + +t[2];
      (e.days || []).forEach(function (day) {
        var off = (day - today + 7) % 7;
        if (off === 0 && at <= nowMin) off = 7;
        if (!best || off * 1440 + at < best.key) best = { key: off * 1440 + at, off: off, day: DAYS[(today + off) % 7], e: e };
      });
    });
    return best;
  }
  function schedNext(d) {
    var best = schedNextEntry(d);
    return best ? (best.off === 0 ? '' : best.day + ' ') + best.e.time + ' ' + schedWhat(best.e) : '';
  }
  var SYS_STATE = {
    health: function (d) {
      var host = /^https?:\/\/([^.\/:]+)\.local/.exec((d.addresses || [])[0] || ''), name = host ? host[1] : 'This box';
      var lines = [['Power', d.power], ['Temperature', d.temperature], ['Player', d.player]];
      (d.helpers || []).forEach(function (x) { if (!x.running && x.name !== 'pvj-netd') lines.push([x.label, { state: 'bad', text: 'Not running' }]); });
      (d.projectors || []).forEach(function (x) { lines.push(['Projector ' + x.name, x]); });
      var worst = lines.filter(function (l) { return l[1] && l[1].state === 'bad'; })[0] || lines.filter(function (l) { return l[1] && l[1].state === 'warn'; })[0];
      if (!worst) return st(null, name + ' · all well');
      return st(worst[1].state === 'bad' ? 'problem' : 'check', worst[0] + ': ' + worst[1].text);
    },
    projectors: function (d) {
      var ps = d.projectors, n = ps.length;
      if (!n) return st('setup', 'No projectors added yet');
      var silent = ps.filter(function (p) { return p.status && p.status.ok === false; });
      if (silent.length) return st('problem', silent[0].name + ' does not answer');
      var warned = ps.filter(function (p) { var w = (p.status || {}).warnings || {}; return Object.keys(w).some(function (k) { return w[k] === 'error' || w[k] === 'warning'; }); });
      if (warned.length) return st('problem', warned[0].name + ' has a warning');
      var lit = ps.filter(function (p) { return /^(on|warming)/.test((p.status || {}).power || ''); });
      if (lit.length) return st('active', lit.length + ' of ' + n + ' on');
      var answered = ps.filter(function (p) { return p.status && p.status.ok; });
      return st('ready', answered.length === n ? plural(n, 'projector') + ', none on' : 'Checking...');
    },
    room: function (d) {
      if (d.job && d.job.running) return st('active', 'Working: ' + d.job.name);
      if (!d.groups.length && !d.scenes.length) return st('setup', 'No groups or scenes yet');
      return st('ready', plural(d.groups.length, 'group') + ', ' + plural(d.scenes.length, 'scene'));
    },
    schedule: function (d) {
      if (!d.enabled) return st('off', d.entries.length ? plural(d.entries.length, 'entry', 'entries') + ' saved, not running' : '');
      if (!d.entries.length) return st('setup', 'No entries yet');
      var failed = d.entries.filter(function (e) { return d.last && d.last[e.id] && d.last[e.id].ok === false; });
      if (failed.length) return st('problem', 'Last run failed: ' + (failed[0].label || schedWhat(failed[0])));
      var next = schedNext(d);
      return st('ready', next ? 'Next: ' + next : plural(d.entries.length, 'entry', 'entries'));
    },
    vibes: function (d) {
      var nice = window.pvjShaders ? window.pvjShaders.nice : String;
      if (d.error) return st('problem', 'A shader was refused: ' + nice(d.error.id));
      if (d.vibes && d.vibes.running) return st('active', 'Vibes is playing' + (d.playing ? ': ' + nice(d.playing.name) : ''));
      if (d.playing) return st('active', 'One shader is playing: ' + nice(d.playing.name));
      var n = (d.shaders || []).filter(function (s) { return s.vibes && !s.error; }).length;
      return n ? st('ready', plural(n, 'shader') + ' in the rotation') : st('setup', 'No shader is in the rotation');
    },
    access: function (d) {
      var guests = d.codes.filter(function (c) { return c.role === 'view'; }).length, presenters = d.codes.length - guests;
      if (!can('full')) return st(null, guests ? 'A guest code is active' : 'No guest code');
      return st(null, [plural(S.devices.length, 'device'), guests ? plural(guests, 'guest code') : '', presenters ? plural(presenters, 'presenter code') : ''].filter(Boolean).join(', '));
    },
    sound: function (d) {
      var name = d.device === 'auto' ? d.automatic_is : d.device, dev = d.devices.filter(function (x) { return x.name === name; })[0];
      return st(null, (d.device === 'auto' ? 'Automatic' : 'Fixed') + (dev && dev.description ? ': ' + dev.description : ''));
    },
    autostart: function (d) {
      var c = d.config;
      if (c.mode === 'off') return st('off', 'The box waits for you at power-up');
      if (d.last && d.last.ok === false) return st('problem', 'Last run failed: ' + d.last.message);
      return st('ready', ({ file: 'Plays ' + c.file, all: 'Plays every clip', slideshow: 'Shows the pictures', pad: 'Plays a pad', usb: 'Plays the USB stick',
        preset: 'Runs ' + c.preset, vibes: 'Starts Vibes' })[c.mode] || c.mode);
    },
    streams: function (d) {
      var pl = (S.status && S.status.player) || {};
      if (!d.streams.length) return st('setup', 'No streams saved yet');
      if (pl.stream) return st('active', 'Playing: ' + pl.stream);
      return st('ready', d.streams.length + ' saved');
    },
    mapping: function (d) {
      var state = (d.status || {}).state;
      if (state === 'error') return st('problem', d.status.message || 'The mapping could not be shown');
      if (!d.surfaces.length) return st('setup', 'No surfaces yet');
      if (d.edit && d.edit.on) return st('active', 'Being placed, with outlines on the display');
      if (d.on || state === 'building') return st('active', state === 'building' ? 'Being worked out' : 'On the screen');
      return st('ready', plural(d.surfaces.length, 'surface') + ', not on the screen');
    },
    sync: function (d) {
      var c = d.config;
      if (d.error) return st('problem', d.error);
      if (c.role === 'off') return st('setup', 'Not chosen yet: lead or follow');
      if (c.role === 'server') return st('active', 'Leads group "' + c.group + '"');
      return d.server ? st('active', 'Following ' + d.server) : st('problem', 'Listening for the box that leads');
    },
    midi: function (d) {
      if (!d.enabled) return st('off', '');
      if (!d.devices.length) return st('setup', 'No controller plugged in');
      var deaf = d.devices.filter(function (x) { return !x.connected; });
      if (deaf.length) return st('problem', deaf[0].name + ' is not reading');
      return st('ready', plural(d.devices.length, 'controller') + ': ' + d.devices.map(function (x) { return x.name; }).join(', '));
    },
    dmx: function (d, before) {
      if (!d.enabled) return st('off', '');
      if (d.error) return st('problem', d.error);
      if (before && before.enabled && d.received > before.received) return st('active', 'Frames arriving');
      return st('ready', d.received ? 'Listening, ' + plural(d.received, 'frame') + ' so far' : 'Listening, nothing received yet');
    },
    osc: function (d) {
      if (!d.enabled) return st('off', '');
      if (d.error) return st('problem', d.error);
      return st('ready', d.listening ? 'Listening on UDP ' + d.port : 'Starting');
    },
    network: function (d) {
      if (!d.helper) return st('problem', 'The network helper is not running');
      if (d.reverting) return st('problem', 'Going back to the previous network');
      if (d.pending) return st('active', 'A change is waiting for your confirmation');
      var addr = [];
      d.interfaces.forEach(function (i) { if (i.kind === 'wired') addr = addr.concat(i.addresses || []); });
      var ports = (d.wifi && d.wifi.ports) || {}, air = Object.keys(ports).filter(function (k) { return ports[k] && ports[k].ssid; }).map(function (k) { return (ports[k].hotspot ? 'Own hotspot: ' : 'Wi-Fi: ') + ports[k].ssid; });
      return st('ready', air.concat(addr.length ? ['wired: ' + addr.join(', ')] : []).join(', ') || 'No wired address');
    },
    updates: function (d) { return st(null, 'Version ' + d.version); },
    support: function (d) {
      var c = d.config;
      if (!c) return d.active ? st('active', 'Session open, ' + mins(d.seconds_left) + ' left') : st(null, 'No session');
      if (!c.allowed) return st('off', 'Not allowed');
      if (d.available === false) return st('problem', 'Not available on this box');
      if (d.active) return st('active', 'Session open, ' + mins(d.seconds_left) + ' left');
      return d.configured ? st('ready', 'Allowed, no session open') : st('setup', 'Allowed, but the support server is not filled in');
    },
    about: function (d) {
      var sys = (S.status && S.status.system) || {};
      return st(null, [d.board, typeof sys.temp_c === 'number' ? Math.round(sys.temp_c) + '°C' : '', d.disk ? gb(d.disk.free) + ' free' : ''].filter(Boolean).join(' · '));
    }
  };
  function sysState(row) {
    var m = row.module ? mod(row.module) : null;
    if (row.module && !(m && m.enabled)) return st('off', '');
    if (!row.url) return row.module ? st('ready', 'On') : st(null, row.fact ? row.fact() : '');
    var d = S.sysData[row.id];
    if (!d) return st(null, '');              // not asked yet, or not for this device to ask
    try { return SYS_STATE[row.id](d.now, d.before); } catch (e) { return st(null, ''); }   // an answer in a shape not foreseen: say nothing, never break the screen
  }
  function showState(chip, text, state, words) {
    chip.className = 'chip' + (state.chip ? ' chip-' + state.chip : '');
    chip.textContent = state.chip ? CHIPS[state.chip] : '';
    chip.hidden = !state.chip;
    text.textContent = words === undefined ? state.text : words;
  }
  function patchRow(row) {
    var el = document.getElementById('nav-' + row.id);
    if (el) showState(el.querySelector('.chip'), el.querySelector('.navstate'), sysState(row));
  }
  function keepAnswer(row, r) {
    if (r.ok) S.sysData[row.id] = { now: r.data, before: (S.sysData[row.id] || {}).now };
    else delete S.sysData[row.id];
  }
  // Asked once when the index opens and again on each return to it; an answer only repaints its own row.
  function loadSysStates() {
    var asked = {};
    sysRows().filter(rowShown).forEach(function (row) {
      if (!rowFetchable(row)) { delete S.sysData[row.id]; return patchRow(row); }
      (asked[row.url] = asked[row.url] || api('GET', row.url)).then(function (r) { keepAnswer(row, r); patchRow(row); });
    });
  }
  function indexHealth() {        // Health keeps its own poll while the index is open
    clearTimeout(healthTimer);
    if (!document.getElementById('nav-health')) return;
    api('GET', '/api/health').then(function (r) {
      if (!document.getElementById('nav-health')) return;
      var row = { id: 'health', url: '/api/health' };
      keepAnswer(row, r); patchRow(row);
      clearTimeout(healthTimer);
      healthTimer = setTimeout(indexHealth, 5000);
    });
  }
  // The open page's own state line: read when the page is drawn and after each change made on it.
  function pageState() {
    var line = document.getElementById('sysstate');
    var row = S.sys && sysRows().filter(function (r) { return r.id === S.sys; })[0];
    if (!line || !row || !row.url) return;
    function show() {
      var state = sysState(row);
      line.hidden = !state.chip || pageSwitch.on !== true;    // a page that is off already says Off
      showState(line.querySelector('.chip'), line.querySelector('.hint'), state, state.text.replace(/ inside$/, ' below'));
    }
    if (!rowFetchable(row)) { delete S.sysData[row.id]; return show(); }
    api('GET', row.url).then(function (r) {
      if (!line.isConnected) return;
      keepAnswer(row, r);
      // The switch is drawn from the last answer. If this one says otherwise (it was not read yet, or it was changed
      // from another phone), the page is drawn again, once: the next answer then agrees with what is drawn.
      if (pageSwitch.steps && pageOn(pageSwitch.steps) !== pageSwitch.on) return redrawSystem();
      var wait = document.getElementById('syschecking');
      if (wait && !r.ok) wait.textContent = 'Could not read its state: ' + (r.data.error || 'no answer') + '.';
      show();
    });
  }
  function pageStateSoon() {
    if (S.tab !== 'system' || !S.sys) return;
    clearTimeout(pageStateTimer);
    pageStateTimer = setTimeout(pageState, 500);
  }

  // -- moving between the index and a page --
  function redrawSystem() {         // only the System screen is rebuilt: the tabs and the rest stay as they are
    var old = app.querySelector('.shell > .screen');
    var roomTab = !!app.querySelector('nav.tabs.many');       // the Room module was just switched: the tabs change too
    if (!old || !S.device || S.tab !== 'system' || roomTab !== (!!window.pvjRoom && moduleOn('room'))) return render();
    markArea();
    stopTimers();
    keepNetForm();
    keepSyncForm();
    old.parentNode.replaceChild(system(), old);
  }
  // from: the tab the page is opened from when that is not System (Back then returns there). replace: the page takes
  // the place of the one that is open, so Back does not stop at it.
  function openSys(id, from, replace) {
    var other = S.tab !== 'system';
    if (other) { S.sysFrom = from || null; S.tab = 'system'; }
    S.sys = id; S.msg = '';
    if (replace && history.state && history.state.sys) history.replaceState({ sys: id }, '');
    else history.pushState({ sys: id }, '');
    if (other) render(); else redrawSystem();     // from another tab the tab bar changes too
    window.scrollTo(0, 0);
  }
  function leaveSysPage() {          // back at the index, or at the tab the page was opened from
    S.sys = null; S.msg = ''; S.sysFresh = false;
    if (S.sysFrom) { S.tab = S.sysFrom; S.sysFrom = null; }
  }
  function sysBack() {
    if (history.state && history.state.sys) return history.back();     // the popstate listener draws what is behind
    leaveSysPage();
    render();
    window.scrollTo(0, 0);
  }
  function system() {
    var row = S.sys && sysRows().filter(function (r) { return r.id === S.sys && rowShown(r); })[0];
    if (row) return sysPage(row.id, row.name, row.blurb, row);
    S.sys = null;
    return sysIndex();
  }
  function sysIndex() {
    var rows = sysRows().filter(rowShown);
    function nav(row) {
      var state = sysState(row);
      return h('button', { class: 'navrow', id: 'nav-' + row.id, onclick: function () { openSys(row.id); } },
        h('span', { class: 'navname', text: row.name }),
        h('span', { class: 'chip' + (state.chip ? ' chip-' + state.chip : ''), hidden: !state.chip, text: state.chip ? CHIPS[state.chip] : '' }),
        h('span', { class: 'navstate', text: state.text }));
    }
    function group(id, title) {
      var mine = rows.filter(function (r) { return r.group === id; });
      return mine.length ? h('div', { class: 'card navgroup', id: 'group-' + id }, title ? h('h2', { class: 'navhead', text: title }) : null, mine.map(nav)) : null;
    }
    function fold(id, title, list) {      // modules that cannot be switched: named and described, no switches
      return list.length ? h('details', { class: 'card fold', id: id }, h('summary', { text: title + ' (' + list.length + ')' }),
        h('div', { class: 'list' }, list.map(function (m) {
          return h('div', { class: 'item' }, h('span', {}, m.name, h('br'), h('span', { class: 'hint', text: m.description })));
        }))) : null;
    }
    var optional = can('full') ? S.modules.filter(function (m) { return m.type !== 'core'; }) : [];
    if (!S.sysFresh) { S.sysFresh = true; setTimeout(loadSysStates, 0); }
    clearTimeout(healthTimer);
    healthTimer = setTimeout(indexHealth, 5000);
    return h('div', { class: 'screen', id: 'sysindex' }, h('div', { class: 'top' }, h('h1', { text: 'System' })),
      h('div', { id: 'msg', class: 'msg' + (S.msgErr ? ' err' : ''), role: 'status', text: S.msg }),
      group('top', ''),
      SYS_GROUPS.map(function (g) { return group(g[0], g[1]); }),
      fold('notbuilt', 'Not built yet', optional.filter(function (m) { return m.status === 'planned'; })),
      fold('notonboard', 'Not on this board', optional.filter(function (m) { return m.status === 'ready' && !m.supported; })));
  }

  // -- the page shell --
  // A page has ONE switch. It is a list of steps, each { isOn(), set(value, control) -> the API's answer }: the module,
  // then, where a feature has its own "enabled" flag (DMX, MIDI, the schedule; OSC and Remote support have only the
  // flag), that flag. The switch shows On only when every step is on. Switching on runs the steps in order; switching
  // off switches the module off (or, with offInner, only the flag). A switch sends its own flag and nothing else:
  // fields being edited on the page are not saved by it.
  var pageSwitch = { steps: null, on: true };       // what the open page's switch was drawn from
  function moduleStep(id) {
    return { isOn: function () { return moduleOn(id); },
      set: function (value) { return api('POST', '/api/modules/' + id, { enabled: value }).then(function (r) { if (r.ok) S.modules = r.data.modules; return r; }); } };
  }
  // A feature's own flag. What it is now comes from the row's last GET answer (null: not read yet); its POST answers
  // in the same shape, so the answer is kept as the row's state.
  function flagStep(rowId, url, read, body, postUrl) {
    return { inner: true,
      isOn: function () { var d = S.sysData[rowId]; return d ? !!read(d.now) : null; },
      set: function (value) { return api('POST', postUrl || url, body(value)).then(function (r) { if (r.ok) keepAnswer({ id: rowId }, r); return r; }); } };
  }
  // The schedule is saved whole (the entries go with the flag), so the saved entries are read just before each
  // change and sent back as they are. Saved entries start running as soon as it is on, so that is asked first.
  function scheduleStep() {
    var step = flagStep('schedule', '/api/schedule', function (d) { return d.enabled; });
    step.set = function (value, control) {
      return api('GET', '/api/schedule').then(function (g) {
        if (!g.ok) return g;
        function post(flag) {
          return api('POST', '/api/schedule', { enabled: flag, entries: g.data.entries }).then(function (r) { if (r.ok) keepAnswer({ id: 'schedule' }, r); return r; });
        }
        var n = g.data.entries.length;
        if (!value || !n || !control || !control.isConnected) return post(value);
        return new Promise(function (resolve) {
          confirmRow('Switch the schedule on? ' + (n === 1 ? '1 entry' : n + ' entries') + ' will start running at ' + (n === 1 ? 'its time.' : 'their times.'),
            'Switch on', 'Not yet', function () { resolve(post(true)); }, control, function () {
              // "Not yet". A box whose module was off with the schedule left on is running from the moment the
              // module came on: put the flag off, so the answer is honoured.
              resolve((g.data.enabled ? post(false) : Promise.resolve(g)).then(function () { return { ok: true, cancelled: true, data: {} }; }));
            });
        });
      });
    };
    return step;
  }
  function pageOn(steps) {       // true, false, or null while a feature's own flag has not been read yet
    for (var i = 0; i < steps.length; i++) { var v = steps[i].isOn(); if (v !== true) return v; }
    return true;
  }
  function runSwitch(steps, on, offInner, control) {
    if (!on) return (offInner ? steps[steps.length - 1] : steps[0]).set(false, control);
    // The module is skipped when it is already on (a box in the mixed state gets only the feature's own call); the
    // feature's flag is always sent, because what the panel last read may be old.
    return steps.reduce(function (before, step) {
      return before.then(function (r) { return !r.ok || r.cancelled || !step.inner && step.isOn() ? r : step.set(true, control); });
    }, Promise.resolve({ ok: true, data: {} }));
  }
  // Replaces the tapped control's row with a question, a danger button and a plain one; "no", or 8 seconds, puts it
  // back (and calls onNo, when the caller is waiting for an answer).
  function confirmRow(question, yesText, noText, onYes, control, onNo) {
    var row = control.closest('.row') || control.parentNode;
    clearTimeout(confirmTimer);
    var box = h('div', { class: 'confirm', id: 'confirmrow', role: 'alert' }, h('span', { text: question }));
    function revert() { clearTimeout(confirmTimer); if (box.parentNode) box.parentNode.removeChild(box); row.hidden = false; }
    function no() { revert(); if (onNo) onNo(); }
    box.appendChild(h('div', { class: 'row' },
      h('button', { class: 'btn danger grow', id: 'confirmyes', text: yesText, onclick: function () { revert(); onYes(); } }),
      h('button', { class: 'btn grow', id: 'confirmno', text: noText, onclick: no })));
    row.hidden = true;
    row.parentNode.insertBefore(box, row.nextSibling);
    confirmTimer = setTimeout(no, 8000);
  }
  // A card that redraws by itself waits while a question is open in it, so the question is not wiped.
  function asking(el) { return !!(el && el.querySelector('#confirmrow')); }
  // A card that is rebuilt whole must not take the cursor from the person using it. rebuild() runs in between; the
  // control that had the cursor is found again by its id and gets the cursor back. (An answer from the box can
  // arrive while a name is being typed: without this the keyboard closed, and the letters typed after it went
  // nowhere.) A field also gets back what was typed into it and the place in it, but only while it is still the
  // same field: the rebuilt one must have been given the same content by the card as the old one was (its
  // defaultValue, or for a list the option marked as chosen). When that differs, the box has something else to say
  // there (another surface was chosen, the name was changed elsewhere) and the old text would be a lie under a new
  // label. A field marked data-kept holds its text in the card itself and is never written to here.
  function builtWith(el) {
    if (el.tagName !== 'SELECT') return String(el.defaultValue);
    var chosen = Array.prototype.filter.call(el.options, function (o) { return o.defaultSelected; })[0] || el.options[0];
    return chosen ? chosen.value : '';
  }
  function keepCursor(container, rebuild) {
    var a = document.activeElement, keep = null;
    if (a && a.id && container.contains(a)) {
      var field = /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName) && a.type !== 'range';
      keep = { id: a.id, field: field, tag: a.tagName, value: field ? a.value : null, built: field ? builtWith(a) : null, from: null, to: null };
      try { if (field) { keep.from = a.selectionStart; keep.to = a.selectionEnd; } } catch (e) { /* a field with no place in it */ }
    }
    rebuild();
    var el = keep && document.getElementById(keep.id);
    if (!el || !container.contains(el) || el.disabled) return;
    var same = keep.field && el.tagName === keep.tag && !el.hasAttribute('data-kept') && builtWith(el) === keep.built;
    if (same && el.tagName === 'SELECT') {
      if (Array.prototype.some.call(el.options, function (o) { return o.value === keep.value; })) el.value = keep.value;
    } else if (same) el.value = keep.value;
    try { el.focus({ preventScroll: true }); } catch (e) { el.focus(); }
    // the place in the text only where the text is the one it was a place in
    try { if (keep.field && typeof keep.from === 'number' && el.value === keep.value) el.setSelectionRange(keep.from, keep.to); } catch (e) { /* not a text field */ }
  }

  // -- the patterns every System page shares --
  // A visible label above a field and an optional hint below it. Hiding the wrapper hides all three.
  function labelled(label, input, hint) {
    return h('div', { class: 'fieldwrap', id: input.id ? input.id + 'wrap' : false },
      h('label', { class: 'field', for: input.id || false, text: label }), input, hint ? h('div', { class: 'hint', text: hint }) : null);
  }
  // One list row: a name, one state line, a red problem line when there is one, at most one primary action, and
  // "More", which opens the secondary actions in place (Remove last). Which row is open survives redraws.
  var moreOpen = null;
  function listRow(o) {
    var more = (o.more || []).filter(Boolean), open = more.length && moreOpen === o.key;
    var moreBtn = more.length ? h('button', { class: 'btn morebtn', 'aria-expanded': open ? 'true' : 'false', 'aria-label': 'More for ' + o.name, text: 'More',
      onclick: function () { moreOpen = open ? null : o.key; o.redraw(); } }) : null;
    return h('div', { class: 'item lrow' + (o.cls ? ' ' + o.cls : ''), 'data-id': o.data },
      h('div', { class: 'lhead' }, h('div', { class: 'lname', text: o.name }),
        o.sub ? h('div', { class: 'addr' + (o.subCls ? ' ' + o.subCls : ''), text: o.sub }) : null,
        !o.state ? null : typeof o.state === 'string' ? h('div', { class: 'state', text: o.state }) : o.state,
        o.problem ? h('div', { class: 'hint warn problem', text: o.problem }) : null,
        o.note ? h('div', { class: 'hint lnote', text: o.note }) : null),
      o.primary || moreBtn ? h('div', { class: 'row lacts' }, o.primary || null, moreBtn) : null,
      open ? h('div', { class: 'row wrap moreacts' }, more) : null,
      o.below || null);
  }
  // "+ Add a ...": the form opens in place, and is open by itself while the list is empty.
  var addOpen = {};
  function addBlock(key, what, empty, form, redraw) {
    if (empty || addOpen[key]) return form(empty ? null : function () { addOpen[key] = false; redraw(); });
    return h('div', { class: 'row' }, h('button', { class: 'btn grow addopen', id: key + 'open', text: '+ Add ' + what, onclick: function () { addOpen[key] = true; redraw(); } }));
  }
  // "Save changes": disabled until something changed, with "Not saved yet" while it has; the result of the save is
  // said under the button as well as in the page's message line.
  function saveBar(id, text, onSave, others) {
    var note = h('span', { class: 'hint notsaved', id: id + 'dirty', text: 'Not saved yet', hidden: true });
    var result = h('div', { class: 'msg inmsg', id: id + 'result', role: 'status' });
    var btn = h('button', { class: 'btn on pri', id: id, text: text || 'Save changes', disabled: true, onclick: function () { onSave(bar); } });
    var bar = { btn: btn, result: result, note: note, isDirty: false,
      el: h('div', { class: 'savebar' }, h('div', { class: 'row wrap' }, btn, others || null, note), result),
      dirty: function (v) { bar.isDirty = !!v; btn.disabled = !v; note.hidden = !v; if (v) bar.say(''); },
      say: function (t, isErr) { result.textContent = t || ''; result.className = 'msg inmsg' + (isErr ? ' err' : ''); if (t) say(t, isErr); } };
    return bar;
  }
  // A toggle is always the real switch, with a noun for its label and an optional hint.
  function toggle(id, label, on, set, hint) {
    var sw = h('button', { class: 'switch', id: id, role: 'switch', 'aria-checked': on ? 'true' : 'false', 'aria-label': label, onclick: function () {
      var v = sw.getAttribute('aria-checked') !== 'true';
      sw.setAttribute('aria-checked', v ? 'true' : 'false');
      if (set) set(v, sw);
    } });
    var el = h('div', { class: 'fieldwrap toggle' }, h('div', { class: 'rot between' }, h('span', { text: label }), sw), hint ? h('div', { class: 'hint', text: hint }) : null);
    el.sw = sw;
    el.isOn = function () { return sw.getAttribute('aria-checked') === 'true'; };
    return el;
  }
  // A choice that needs a feature that is switched off: said in place, with the switch itself (never "go to its page").
  function offNotice(id, rowId, name, redraw) {
    return h('div', { class: 'hint warn', id: id }, h('div', { text: name + ' is switched off, so this will do nothing.' }),
      h('div', { class: 'row' }, h('button', { class: 'btn', id: id + 'on', text: 'Switch ' + name + ' on', onclick: function () {
        shaderCtx().switchFeature(rowId, true).then(function (r) {
          if (!r.ok) return say(r.data.error || 'Could not switch ' + name + ' on.', true);
          say(name + ' is switched on.');
          redraw();
        });
      } })));
  }
  function playingNow() { var pl = (S.status && S.status.player) || {}; return !!(pl.running && (pl.path || pl.vibes || pl.stream)); }
  // A result said beside the control that caused it, as well as in the page's message line.
  function sayAt(el, text, isErr) {
    if (el) { el.textContent = text || ''; el.className = 'msg inmsg' + (isErr ? ' err' : ''); }
    say(text, isErr);
  }
  function flipSwitch(opts, steps, on, control) {
    function go() {
      runSwitch(steps, on, opts.offInner, control).then(function (r) {
        S.sysFresh = false;                 // a first step may have gone through even when a later one did not
        if (r.cancelled) return redrawSystem();
        if (!r.ok) {                        // the switch stays (or goes back to) Off, with the reason on the page
          redrawSystem();
          return say(r.data.error || 'Could not switch it ' + (on ? 'on' : 'off') + '.', true);
        }
        say(on ? 'Switched on.' : 'Switched off.');
        redrawSystem();
      });
    }
    if (on || !opts.confirmOff) return go();
    opts.confirmOff(function (question) {
      if (!question) return go();
      if (control.isConnected && !document.getElementById('confirmrow')) confirmRow(question, 'Switch off', 'Keep it on', go, control);
    });
  }
  function sysPage(id, title, blurb, opts) {
    opts = opts || {};
    var full = can('full');
    var steps = (opts.module ? [moduleStep(opts.module)] : []).concat(opts.steps || []);
    if (!steps.length) steps = null;
    var on = steps ? pageOn(steps) : true;
    pageSwitch = { steps: steps, on: on };
    var head = h('div', { class: 'top syshead' }, h('h1', { text: title }));
    if (steps && full && on !== null) {
      var sw = h('button', { class: 'switch', id: 'sysswitch', role: 'switch', 'aria-checked': on ? 'true' : 'false', 'aria-label': title,
        onclick: function () { flipSwitch(opts, steps, !on, sw); } });
      head.appendChild(h('span', { class: 'row switchwrap' }, h('span', { class: 'switchlabel', id: 'sysswitchlabel', text: on ? 'On' : 'Off' }), sw));
    }
    var body = on === null ? h('div', { class: 'card' }, h('div', { class: 'hint', id: 'syschecking', text: 'Checking...' })) :
      on || opts.bodyWhenOff ? h('div', { class: 'grid2', id: 'sysbody' }, opts.body ? opts.body() : null) :
      h('div', { class: 'card', id: 'sysoff' }, h('div', { text: 'Off. Your settings are kept while it is off.' }),
        full ? h('div', { class: 'row' }, h('button', { class: 'btn on pri big grow', id: 'sysswitchon', text: 'Switch on ' + title, onclick: function (e) { flipSwitch(opts, steps, true, e.target); } })) : null);
    setTimeout(pageState, 0);
    return h('div', { class: 'screen syspage', id: 'syspage', 'data-page': id },
      h('div', { class: 'row' }, h('button', { class: 'btn back', id: 'sysback', text: S.sysFrom === 'live' ? '‹ Live' : '‹ System', onclick: sysBack })),
      head,
      h('p', { class: 'hint', id: 'sysblurb', text: blurb || '' }),
      h('div', { id: 'msg', class: 'msg' + (S.msgErr ? ' err' : ''), role: 'status', text: S.msg }),
      h('div', { class: 'row', id: 'sysstate', hidden: true }, h('span', { class: 'chip' }), h('span', { class: 'hint' })),
      body);
  }
  // About and power: what the box is (versions, storage, screens, the clock), then restarting it. Each power action
  // asks first, in place, and says what the room will see.
  function aboutPage() {
    var full = can('full');
    var cards = [boxCard()];
    if (full) {
      var out = h('div', { class: 'msg inmsg', id: 'powerresult', role: 'status' });
      var rows = h('div', { class: 'list sp', id: 'powerbody' },
        h('div', { class: 'row' }, h('button', { class: 'btn grow', id: 'restartplayer', text: 'Restart player', onclick: function (e) {
          confirmRow('Restart the player? The picture stops for a few seconds.', 'Restart player', 'Not now', function () {
            act('POST', '/api/player/restart', {}, function () { sayAt(out, 'The player is restarting. It is back in a few seconds.'); });
          }, e.currentTarget);
        } })));
      cards.push(h('div', { class: 'card', id: 'powercard' }, h('h2', { text: 'Restart and power' }), rows, out));
    } else {
      cards.push(h('div', { class: 'card', id: 'forgetcard' }, h('h2', { text: 'This phone' }),
        h('div', { class: 'hint', text: S.device ? S.device.name + ', ' + roleName(S.device.role) : '' }),
        h('div', { class: 'row' }, h('button', { class: 'btn grow', id: 'forgetdevice', text: 'Leave this panel', onclick: function (e) {
          confirmRow('Leave this panel on this phone? You will need a code or the PIN to get back in.', 'Leave', 'Stay', function () { S.device = null; render(); }, e.currentTarget);
        } }))));
    }
    return cards;
  }
  // ---- health (the old Powersupply, Check Services and GPU Usage buttons, in plain words) ----
  var healthTimer = null;
  function healthCard() {
    var body = h('div', { class: 'list', id: 'healthbody' }, h('div', { class: 'hint', text: 'Checking...' }));
    var card = h('div', { class: 'card', id: 'healthcard' }, h('h2', { text: 'Health' }), body);
    var mark = { ok: 'OK', warn: 'Check', bad: 'Problem', unknown: '?' };
    function row(id, label, state, text) {
      return h('div', { class: 'item health-' + (state || 'unknown'), id: id },
        h('span', {}, h('b', { text: label }), h('br'), h('span', { class: 'state', text: text })),
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
      (d.projectors || []).forEach(function (x) { body.appendChild(row('healthproj-' + x.id, 'Projector: ' + x.name, x.state, x.text)); });
      body.appendChild(h('div', { class: 'field', text: 'Open the panel from another device at' }));
      body.appendChild(h('div', { class: 'list mono', id: 'healthaddr' }, d.addresses.map(function (a) { return h('div', { class: 'item' }, h('span', { text: a })); })));
      if (can('full')) body.appendChild(h('div', { class: 'row' }, h('button', { class: 'btn grow', id: 'healthshowaddr', text: 'Show the address on the display (2 minutes)', onclick: function () {
        act('POST', '/api/access/screen', { show: true, items: ['address'], seconds: 120 }, function () { say('The address is on the display for 2 minutes.'); });
      } })));
    }
    function refresh() { api('GET', '/api/health').then(function (r) { if (document.getElementById('healthcard') && r.ok) draw(r.data); }); }
    refresh();
    return card;
  }

  function gb(n) { return (n / 1073741824).toFixed(1) + ' GB'; }
  function boxCard() {
    var now = S.status || {}, sys = now.system || {};
    var body = h('div', { class: 'list sp', id: 'boxbody' }, h('div', { class: 'hint', text: 'Loading...' }));
    var card = h('div', { class: 'card', id: 'boxcard' }, h('h2', { text: 'This box' }), body);
    api('GET', '/api/system').then(function (r) {
      if (!document.getElementById('boxcard')) return;
      body.textContent = '';
      if (!r.ok) return body.appendChild(h('div', { class: 'hint', text: (r.data.error || 'Not available') + '. Open the page again in a moment.' }));
      var d = r.data;
      function head(text) { body.appendChild(h('div', { class: 'field', text: text })); }
      head('Versions');
      body.appendChild(kv('nxlx.mastercontrol', d.version));
      body.appendChild(kv('Board', d.board || sys.model || sys.board || '?'));
      body.appendChild(kv('Player', d.mpv || '?'));
      body.appendChild(kv('System', d.os + ' · ' + d.kernel));
      body.appendChild(kv('This device', S.device ? S.device.name + ', ' + roleName(S.device.role) : ''));
      if (d.disk) {
        head('Storage');
        body.appendChild(kv('Clips', gb(d.disk.free) + ' free of ' + gb(d.disk.total)));
        body.appendChild(h('div', { class: 'progress', 'aria-hidden': 'true' }, (function () { var b = h('div', {}); b.style.width = Math.round(100 * d.disk.used / d.disk.total) + '%'; return b; })()));
      }
      head('Screens');
      if (d.output) body.appendChild(kv('Picture now', d.output.width + ' x ' + d.output.height + (d.output.refresh ? ' at ' + d.output.refresh + ' Hz' : '')));
      var modes = [];
      d.screens.forEach(function (sc) {
        body.appendChild(kv(sc.connector, sc.connected ? 'connected' : 'nothing plugged in'));
        if (sc.connected && sc.modes.length) modes.push(h('div', { class: 'hint mono', text: sc.connector + ': ' + sc.modes.join(', ') }));
      });
      if (modes.length) body.appendChild(h('details', { class: 'fold', id: 'boxmodes' }, h('summary', { text: 'Advanced: what each screen can show' }), h('div', { class: 'list sp' }, modes)));
      var c = d.clock || {};
      var boxTime = c.now ? new Date(c.now * 1000) : null;
      head('Clock');
      body.appendChild(kv('Box time', boxTime ? boxTime.toLocaleString() : '?'));
      body.appendChild(h('div', { class: c.clock_from_network === false ? 'hint warn' : 'hint', id: 'boxclockline',
        text: c.clock_from_network === true ? 'Set from the network.' : c.clock_from_network === false ? 'Not set from the network, so it may be wrong. The schedule uses this clock.' : '' }));
      if (!can('full') || !d.system_actions) return;
      if (c.clock_from_network === false) body.appendChild(h('div', { class: 'row' }, h('button', { class: 'btn grow', id: 'setclock', text: 'Set the box clock to this phone\'s time', onclick: function () {
        act('POST', '/api/system/clock', { epoch: Math.round(Date.now() / 1000) }, function () { say('Box clock set.'); render(); });
      } })));
      var power = document.getElementById('powerbody'), out = document.getElementById('powerresult');
      if (!power) return;
      power.appendChild(h('div', { class: 'row' }, h('button', { class: 'btn grow', id: 'rebootbtn', text: 'Restart the box', onclick: function (e) {
        confirmRow('Restart the box? The show stops for about a minute.', 'Restart the box', 'Not now', function () {
          act('POST', '/api/system/reboot', { confirm: 'reboot' }, function () { sayAt(out, 'Restarting. Open the panel again in about a minute.'); });
        }, e.currentTarget);
      } })));
      power.appendChild(h('div', { class: 'row' }, h('button', { class: 'btn grow', id: 'poweroffbtn', text: 'Power off', onclick: function (e) {
        confirmRow('Power the box off? Someone must unplug it and plug it in again to start it.', 'Power off', 'Keep it on', function () {
          act('POST', '/api/system/poweroff', { confirm: 'poweroff' }, function () { sayAt(out, 'Powering off. Wait for the light to stop blinking before unplugging.'); });
        }, e.currentTarget);
      } })));
    });
    return card;
  }
  function kv(k, v) { return h('div', { class: 'row between' }, h('span', { text: k }), h('span', { class: 'kvv', text: String(v) })); }
  // ---- DMX and MIDI ---------------------------------------------------
  var dmxForm = { protocol: null, universe: null, start: null, allow: null };  // what was typed and not saved; survives redraws
  var dmxTimer = null, dmxAdv = false;
  // What each channel does, in the order of pvj/DMX.md (the ninth is read only if the desk sends it).
  var DMX_CHANNELS = [['Opacity', '0 to 255 is 0 to 100 percent'], ['Size', '0 to 255 is 1 to 200 percent'], ['Position X', '0 to 255 is left to right'],
    ['Speed', '0 to 255 is a quarter speed to double'], ['Volume', '0 to 255 is 0 to 100'], ['Blackout', '128 and up is on'],
    ['Pad', '6 to 11 is pad 1, 12 to 17 pad 2, and so on to pad 36'], ['Function', '50 stop, 100 pause, 150 resume, 200 fade out'],
    ['Vibes', '50 stop Vibes, 100 start Vibes, 150 next shader']];
  function moduleOn(id) { var m = S.modules.filter(function (x) { return x.id === id; })[0]; return !!(m && m.enabled); }
  function dmxCard() {
    clearTimeout(dmxTimer);
    var card = h('div', { class: 'card', id: 'dmxcard' }, h('h2', { text: 'Lighting desk' }));
    var body = h('div', { class: 'list sp', id: 'dmxbody' });
    card.appendChild(body);
    var table = h('div', { class: 'card', id: 'dmxtablecard' }, h('h2', { text: 'Channels' }));
    function count(n) { return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ','); }
    function lineText(d) {
      var what = (d.protocol === 'sacn' ? 'sACN' : 'Art-Net') + ' on universe ' + d.universe;
      if (d.error) return 'Problem: ' + d.error + '. Check that no other program on the box uses the same port, then save again.';
      if (!d.listening) return 'Not listening yet.';
      return 'Listening for ' + what + '. ' + (d.received ? count(d.received) + (d.received === 1 ? ' frame' : ' frames') + ' received.' : 'Nothing received yet.');
    }
    function drawTable(d) {
      table.textContent = '';
      table.appendChild(h('h2', { text: 'Channels' }));
      var seen = d.channels || [], n = Math.min(DMX_CHANNELS.length, 513 - d.start);
      table.appendChild(h('div', { class: 'hint', id: 'dmxtablehint', text: seen.length ? 'What the desk is sending now.' : 'Levels show here once the desk sends something. Set these channels on the desk.' }));
      var rows = [];
      for (var i = 0; i < n; i++) {
        var level = typeof seen[i] === 'number' ? seen[i] : null, bar = h('div', {});
        bar.style.width = (level === null ? 0 : Math.round(100 * level / 255)) + '%';
        rows.push(h('tr', { 'data-ch': d.start + i }, h('td', { class: 'num', text: String(d.start + i) }),
          h('td', {}, h('div', { class: 'field', text: DMX_CHANNELS[i][0] + (i === 8 ? ' (optional)' : '') }), h('div', { class: 'hint', text: DMX_CHANNELS[i][1] })),
          h('td', { class: 'lvl' }, h('div', { class: 'lvlbar' }, h('div', { class: 'progress', 'aria-hidden': 'true' }, bar), h('span', { class: 'dmxlevel', text: level === null ? '-' : String(level) })))));
      }
      table.appendChild(h('table', { class: 'chan', id: 'dmxchannels' },
        h('thead', {}, h('tr', {}, h('th', { class: 'num', text: 'Ch' }), h('th', { text: 'What it does' }), h('th', { class: 'lvl', text: 'Level now' }))),
        h('tbody', {}, rows)));
    }
    function watch() {       // the state line and the levels follow the desk; the fields are left alone
      clearTimeout(dmxTimer);
      dmxTimer = setTimeout(function () {
        if (!body.isConnected) return;
        api('GET', '/api/dmx').then(function (r) {
          if (!body.isConnected) return;
          var line = document.getElementById('dmxline');
          if (r.ok && line) { line.textContent = lineText(r.data); line.className = r.data.error ? 'hint warn' : 'state'; drawTable(r.data); }
          watch();
        });
      }, 2000);
    }
    function draw(d, saved) {
      body.textContent = '';
      body.appendChild(h('div', { class: d.error ? 'hint warn' : 'state', id: 'dmxline', text: lineText(d) }));
      var f = dmxForm;
      var proto = h('select', { class: 'text-input', id: 'dmxproto' },
        [['artnet', 'Art-Net'], ['sacn', 'sACN (E1.31)']].map(function (x) { return h('option', { value: x[0], text: x[1], selected: x[0] === (f.protocol === null ? d.protocol : f.protocol) }); }));
      var uni = h('input', { class: 'text-input mono', id: 'dmxuni', type: 'number', value: f.universe === null ? d.universe : f.universe });
      var start = h('input', { class: 'text-input mono', id: 'dmxstart', type: 'number', min: 1, max: 505, value: f.start === null ? d.start : f.start });
      var allow = h('input', { class: 'text-input mono', id: 'dmxallow', placeholder: '192.168.50.0/24', value: f.allow === null ? d.allow.join(', ') : f.allow });
      function nets() { return allow.value.split(',').map(function (x) { return x.trim(); }).filter(Boolean); }
      function changed() { return proto.value !== d.protocol || String(uni.value) !== String(d.universe) || String(start.value) !== String(d.start) || nets().join() !== d.allow.join(); }
      var bar = saveBar('dmxsave', 'Save changes', function () {
        api('POST', '/api/dmx', { protocol: proto.value, universe: parseInt(uni.value, 10), start: parseInt(start.value, 10), allow: nets() }).then(function (r) {
          if (!r.ok) return bar.say((r.data.error || 'Could not save') + '. Nothing was changed.', true);
          dmxForm = { protocol: null, universe: null, start: null, allow: null };
          draw(r.data, true); drawTable(r.data); say('Saved');
        });
      });
      function touch() {
        dmxForm = { protocol: proto.value, universe: uni.value, start: start.value, allow: allow.value };
        bar.dirty(changed());
      }
      [proto, uni, start, allow].forEach(function (el) { el.addEventListener('input', touch); el.addEventListener('change', touch); });
      var adv = h('details', { class: 'fold', id: 'dmxadv', open: dmxAdv }, h('summary', { text: 'Advanced' }), h('div', { class: 'list sp' },
        labelled('Also accept from these networks', allow, 'Only private networks may send. Add your show network here if the desk is on another one. Separate several with commas.'),
        h('div', { class: 'hint', text: 'The box listens on UDP port ' + d.port + '.' })));
      adv.addEventListener('toggle', function () { dmxAdv = adv.open; });
      body.appendChild(labelled('Protocol', proto, 'What the desk sends. Most desks and programs can send Art-Net.'));
      body.appendChild(labelled('Universe', uni, 'The same number as on the desk.'));
      body.appendChild(labelled('Start channel', start, 'The box uses 8 channels from here. A ninth, if the desk sends it, is Vibes.'));
      body.appendChild(adv);
      body.appendChild(bar.el);
      bar.dirty(changed());
      if (saved) bar.result.textContent = 'Saved';
      body.appendChild(h('div', { class: 'hint', text: 'The first frame only sets a starting point, and the box holds its last state if the signal stops. The box only listens.' }));
      watch();
    }
    api('GET', '/api/dmx').then(function (r) {
      if (!document.getElementById('dmxcard')) return;
      if (!r.ok) { body.textContent = ''; body.appendChild(h('div', { class: 'hint', id: 'dmxmsg', text: (r.data.error || 'Not available') + '. Open the page again in a moment.' })); return; }
      draw(r.data); drawTable(r.data);
    });
    return [card, table];
  }
  var MIDI_ACTIONS = [['pad', 'Play a pad'], ['stop', 'Stop'], ['pause', 'Pause / resume'], ['blackout', 'Blackout on / off'], ['fadeout', 'Fade out'],
    ['reset', 'Reset mix'], ['opacity', 'Opacity (fader)'], ['size', 'Size (fader)'], ['position', 'Position X (fader)'], ['speed', 'Speed (fader)'],
    ['volume', 'Volume (fader)'], ['blackout_hold', 'Blackout while held up (fader)'],
    ['vibes', 'Vibes on / off'], ['vibes_next', 'Vibes: next shader'], ['vibes_dwell', 'Vibes: time each shader stays (fader)'],
    ['shader_speed', 'Shader: speed (fader)'], ['shader_prev', 'Shader: the one before'], ['shader_next', 'Shader: the next one']]
    .concat([1, 2, 3, 4, 5, 6, 7, 8].map(function (n) { return ['shader_control_' + n, 'Shader: control ' + n + ' of the one playing (knob)']; }))
    .concat([1, 2, 3, 4, 5, 6, 7, 8].map(function (n) { return ['shader_preset_' + n, 'Shader: preset ' + n + ' of the one playing']; }))
    .concat([['shader_hue', 'Shader: colour turn (fader)'], ['shader_brightness', 'Shader: brightness (fader)'],
      ['vibes_ambient', 'Vibes: start the set Ambient'], ['vibes_show', 'Vibes: start the set Show'],
      ['clip_prev', 'Previous clip'], ['clip_next', 'Next clip'], ['fadein', 'Fade in'],
      ['bank_pad', 'Play a pad of the controllers\' bank'], ['bank_prev', 'Controllers\' bank: the one before'], ['bank_next', 'Controllers\' bank: the next']])
    .concat([1, 2, 3, 4, 5, 6, 7, 8].map(function (n) { return ['scene_' + n, 'Room scene ' + n]; }))
    .concat([['effect_amount', 'Effect: amount (fader)'], ['effect_toggle', 'Effect on / off'], ['effect_prev', 'Effect: the one before'], ['effect_next', 'Effect: the next one']])
    .concat([1, 2, 3, 4, 5, 6, 7, 8].map(function (n) { return ['effect_control_' + n, 'Effect: control ' + n + ' of the one that is on (knob)']; }))
    // a pairing code on the box's own display (System > People and codes switches it on): held 3 to 10 seconds, then let go
    .concat([['code_join', 'Show a one-time presenter code on the display (hold 3 seconds, let go)'],
      ['code_owner', 'Show a one-time full access code on the display (hold 3 seconds, let go)']]);
  // the actions that follow a fader or knob; a shader control does both (a knob sets it, a button steps or toggles it)
  var MIDI_LEVELS = ['opacity', 'size', 'position', 'speed', 'volume', 'blackout_hold', 'vibes_dwell', 'shader_speed', 'shader_hue', 'shader_brightness', 'effect_amount'];
  // What an action is called on the drawn layout of a controller: short, since a control is a small box.
  var MIDI_SHORT = { shader_speed: 'Shader speed', shader_prev: 'Previous shader', shader_next: 'Next shader', shader_hue: 'Shader colour turn',
    shader_brightness: 'Shader brightness', vibes_ambient: 'Vibes: Ambient', vibes_show: 'Vibes: Show', vibes_dwell: 'Vibes time', bank_prev: 'Bank before', bank_next: 'Next bank',
    effect_amount: 'Effect amount', effect_toggle: 'Effect on / off', effect_prev: 'Previous effect', effect_next: 'Next effect',
    code_join: 'Presenter code (hold)', code_owner: 'Full access code (hold)' };
  function midiWhat(a) {
    if (!a) return 'Spare';
    if (a.action === 'none') return 'Nothing';
    if (a.action === 'pad') return 'Pad ' + 'ABC'[a.bank] + (a.index + 1);
    if (a.action === 'bank_pad') return 'Pad ' + (a.index + 1);
    if (a.action === 'scene') return 'Room scene';
    if (MIDI_SHORT[a.action]) return MIDI_SHORT[a.action];
    var slot = /^shader_(control|preset)_([1-8])$/.exec(a.action);
    if (slot) return 'Shader ' + slot[1] + ' ' + slot[2];
    var fx = /^effect_control_([1-8])$/.exec(a.action);
    if (fx) return 'Effect control ' + fx[1];
    var found = MIDI_ACTIONS.filter(function (x) { return x[0] === a.action; })[0];
    return found ? found[1].replace(' (fader)', '') : a.action;
  }
  var midiForm = { action: 'opacity', bank: 0, index: 0 };  // survives redraws
  var midiTimer = null, midiLightTimer = null;
  var midiSel = null;            // { ctl, id }: the control whose chooser is open on a controller's card
  var midiDrawCard = null;       // redraws the mappings card after a change made on a controller's card
  // One card per connected controller. A recognised one gets its layout drawn from its profile: every control shows
  // what it does now, lights up while it is moved, and a tap opens its chooser. Everything is textContent.
  function midiControllers() {
    var wrap = h('div', { class: 'ctlwrap', id: 'midictls', hidden: true });
    var sig = null;
    function signature(d) {
      return JSON.stringify([d.enabled, d.bank, (d.controllers || []).map(function (c) {
        return [c.name, c.connected, c.standard, c.profile && c.profile.id, c.controls.map(function (x) { return [x.action, x.origin, x.guard]; }),
          c.lights && [c.lights.on, c.lights.state, c.lights.brightness, c.lights.testing]];
      })]);
    }
    function lights(d) {
      var cards = wrap.querySelectorAll('.ctlcard');
      (d.controllers || []).forEach(function (c, i) {
        var card = cards[i];
        if (!card || card.getAttribute('data-ctl') !== c.name) return;
        var quiet = card.querySelector('.ctlquiet');
        if (quiet) quiet.hidden = c.messages > 0;
        c.controls.forEach(function (x) {
          var el = card.querySelector('.ctl[data-id="' + x.id + '"]');
          if (!el) return;
          el.classList.toggle('lit', x.ago !== null && x.ago < 1.5);
          el.classList.toggle('wait', !!x.waiting);
          if (x.lit) el.setAttribute('data-lit', x.lit); else el.removeAttribute('data-lit');     // what its light on the controller shows now
          var val = el.querySelector('.ctlval');
          if (val) val.textContent = x.value !== null && (x.kind === 'fader' || x.kind === 'knob') ? String(x.value) : '';
        });
      });
    }
    function changed(data) { sig = signature(data); draw(data); if (midiDrawCard && document.getElementById('midicard')) midiDrawCard(data); }
    function detail(c, x) {
      var box = h('div', { class: 'ctldetail', id: 'ctldetail' });
      box.appendChild(h('div', { id: 'ctlnow', text: x.name + ': ' + midiWhat(x.action) +
        (x.origin === 'yours' ? ' (your choice' + (x.standard ? '; the standard is ' + midiWhat(x.standard) : '') + ')' :
          x.origin === 'any' ? ' (a mapping made for any controller; remove it in the list of mappings below to get the standard back)' :
          x.origin === 'standard' ? ' (standard layout)' : '') }));
      box.appendChild(h('p', { class: 'hint', text: 'Sends ' + (x.send.type === 'cc' ? 'CC ' : 'note ') + x.send.number +
        (x.unverified ? ' (from a list that the maker\'s document does not confirm)' : '') + '.' +
        (x.guard ? ' Press it twice within a second; one press does nothing.' : '') +
        (x.pickup ? ' It picks up: nothing changes until it reaches the value the box has, so nothing jumps.' : '') +
        (x.waiting ? ' It has not reached that value yet.' : '') }));
      if (!can('full')) return box;
      var level = x.kind === 'fader' || x.kind === 'knob';
      var choices = [['none', 'Nothing (switch this control off)']].concat(MIDI_ACTIONS.filter(function (a) {
        return a[0].indexOf('shader_control_') === 0 || a[0].indexOf('effect_control_') === 0 || (MIDI_LEVELS.indexOf(a[0]) >= 0) === level;
      }));
      var now = x.action ? x.action.action : (x.standard ? x.standard.action : choices[1][0]);
      var action = h('select', { class: 'text-input', id: 'ctlaction', 'aria-label': 'What ' + x.name + ' does' },
        choices.map(function (a) { return h('option', { value: a[0], text: a[1], selected: a[0] === now }); }));
      var have = x.action || {};
      var bank = h('select', { class: 'text-input', id: 'ctlbank', 'aria-label': 'Bank' },
        ['A', 'B', 'C'].map(function (n, i) { return h('option', { value: i, text: 'Bank ' + n, selected: i === (have.bank || 0) }); }));
      var index = h('select', { class: 'text-input', id: 'ctlindex', 'aria-label': 'Pad' },
        Array.apply(null, Array(12)).map(function (_, i) { return h('option', { value: i, text: 'Pad ' + (i + 1), selected: i === (have.index || 0) }); }));
      // "Press twice" for what darkens the screen or changes the room: on unless the person switches it off here
      function guardable(name) { return name === 'blackout' || name.indexOf('scene_') === 0; }
      var twiceOn = x.action && guardable(x.action.action) ? !!x.guard : true;
      var twice = h('button', { class: 'switch', id: 'ctltwice', role: 'switch', 'aria-checked': twiceOn ? 'true' : 'false', 'aria-label': 'Press twice',
        onclick: function () { twiceOn = !twiceOn; twice.setAttribute('aria-checked', twiceOn ? 'true' : 'false'); twiceLabel.textContent = twiceOn ? 'On' : 'Off'; show(); } });
      var twiceLabel = h('span', { class: 'switchlabel', text: twiceOn ? 'On' : 'Off' });
      var twiceRow = h('div', { class: 'row', id: 'ctltwicerow' }, h('span', { class: 'grow', text: 'Press twice within a second (one press does nothing)' }), twiceLabel, twice);
      function chosen() {
        var a = { action: action.value };
        if (a.action === 'pad') a.bank = parseInt(bank.value, 10);
        if (a.action === 'pad' || a.action === 'bank_pad') a.index = parseInt(index.value, 10);
        if (guardable(a.action)) a.guard = twiceOn;
        return a;
      }
      // Save only when something would change: saving what is there already must not quietly store a mapping
      function unchanged() {
        var a = chosen(), now = x.action;
        if (!now) return false;
        return a.action === now.action && a.bank === now.bank && a.index === now.index && (!guardable(a.action) || a.guard === !!x.guard);
      }
      var save = h('button', { class: 'btn on pri small', id: 'ctlsave', text: 'Save', onclick: function () {
        var a = chosen();
        act('POST', '/api/midi/map', { set: { controller: c.name, control: x.id, action: a } }, function (data) { say(x.name + ' now does: ' + midiWhat(a)); changed(data); });
      } });
      function show() {
        bank.hidden = action.value !== 'pad';
        index.hidden = action.value !== 'pad' && action.value !== 'bank_pad';
        twiceRow.hidden = !guardable(action.value);
        save.disabled = unchanged();
      }
      [action, bank, index].forEach(function (el) { el.addEventListener('change', show); });
      show();
      box.appendChild(action); box.appendChild(bank); box.appendChild(index); box.appendChild(twiceRow);
      var buttons = h('div', { class: 'row wrap' }, save);
      if (x.origin === 'yours') buttons.appendChild(h('button', { class: 'btn small', id: 'ctlback', text: 'Back to the standard', onclick: function () {
        act('POST', '/api/midi/map', { reset: { controller: c.name, control: x.id } }, function (data) { say(x.name + ' is back to the standard'); changed(data); });
      } }));
      box.appendChild(buttons);
      return box;
    }
    function draw(d) {
      wrap.textContent = '';
      var list = d.enabled ? (d.controllers || []) : [];
      wrap.hidden = !list.length;
      list.forEach(function (c) {
        var card = h('div', { class: 'card ctlcard', 'data-ctl': c.name }, h('h2', { text: c.profile ? c.profile.name : c.name }));
        wrap.appendChild(card);
        var reading = c.connected ? '' : ' (not reading)';
        if (!c.profile) {
          card.appendChild(h('p', { class: 'hint ctlline', role: 'status', text: c.name + reading + ': No built-in layout for this one yet. Teach it below.' }));
          return;
        }
        var mine = c.controls.filter(function (x) { return x.origin === 'yours'; }).length;
        card.appendChild(h('p', { class: 'hint ctlline', role: 'status', text: c.profile.name + reading + ': recognised, standard layout ' + (c.standard ? 'on' : 'off') +
          (mine ? ', ' + plural(mine, 'control') + ' changed by you' : '') + '.' }));
        // a controller that has sent nothing may be in another mode: say so before anyone thinks the layout is wrong
        card.appendChild(h('p', { class: 'hint ctlquiet', hidden: c.messages > 0, text: 'Nothing received yet. Move a control.' +
          (c.profile.id === 'korg-nanokontrol2' ? ' If this is a nanoKONTROL2, hold SET MARKER and CYCLE while plugging it in (it starts in the mode it was last used in).' : '') }));
        if (can('full')) {
          var sw = h('button', { class: 'switch ctlstd', role: 'switch', 'aria-checked': c.standard ? 'true' : 'false', 'aria-label': 'Standard layout of ' + c.profile.name,
            onclick: function () { act('POST', '/api/midi', { controller: c.name, standard: !c.standard }, changed); } });
          card.appendChild(h('div', { class: 'row' }, h('span', { class: 'grow', text: 'Standard layout' }), h('span', { class: 'switchlabel', text: c.standard ? 'On' : 'Off' }), sw));
        }
        // Lights (the box lights the controller's own buttons): one state line, the real switch (applies on tap),
        // the brightness where the controller has one, and a test sweep. The words come from the box.
        if (c.lights) {
          var L = c.lights;
          card.appendChild(h('p', { class: 'hint ctllightline', role: 'status', text: L.line + (L.testing ? ' Testing: each light comes on in turn.' : '') }));
          if (can('full')) {
            var lsw = h('button', { class: 'switch ctllights', role: 'switch', 'aria-checked': L.on ? 'true' : 'false', 'aria-label': 'Lights of ' + c.profile.name,
              onclick: function () { act('POST', '/api/midi', { controller: c.name, lights: !L.on }, changed); } });
            card.appendChild(h('div', { class: 'row' }, h('span', { class: 'grow', text: 'Lights' }), h('span', { class: 'switchlabel', text: L.on ? 'On' : 'Off' }), lsw));
            if (L.on && L.levels) {
              card.appendChild(h('div', { class: 'row wrap' }, h('span', { class: 'grow', text: 'Brightness' }),
                h('div', { class: 'seg ctlbright', role: 'group', 'aria-label': 'Brightness of the lights of ' + c.profile.name }, ['low', 'medium', 'high'].map(function (lv) {
                  return h('button', { class: 'btn small segbtn' + (L.brightness === lv ? ' on' : ''), type: 'button', 'data-level': lv, 'aria-pressed': L.brightness === lv ? 'true' : 'false',
                    text: lv.charAt(0).toUpperCase() + lv.slice(1), onclick: function () { act('POST', '/api/midi', { controller: c.name, brightness: lv }, changed); } });
                }))));
            }
            if (L.state === 'on') {
              card.appendChild(h('div', { class: 'row' }, h('button', { class: 'btn small ctltest', type: 'button', text: 'Test lights', disabled: !!L.testing, onclick: function () {
                act('POST', '/api/midi/lights', { controller: c.name, test: true }, function (data) { say('Each light of ' + c.profile.name + ' comes on in turn, then they go back.'); changed(data); });
              } })));
            }
          }
          card.appendChild(h('p', { class: 'hint ctllightnote', text: L.note }));
        }
        var grid = h('div', { class: 'ctlgrid', role: 'group', 'aria-label': 'The controls of ' + c.profile.name });
        grid.style.gridTemplateColumns = 'repeat(' + c.profile.cols + ', minmax(58px, 1fr))';
        grid.style.setProperty('--cols', String(c.profile.cols));       // for a style that gives the cells another width (D54)
        var open = null;
        c.controls.forEach(function (x) {
          var chosen = !!midiSel && midiSel.ctl === c.name && midiSel.id === x.id;
          if (chosen) open = x;
          var b = h('button', { class: 'ctl ctl-' + x.kind + (x.origin === 'yours' || x.origin === 'any' ? ' mine' : '') + (x.action ? '' : ' spare') + (chosen ? ' sel' : '') + (x.light ? ' haslight' : ''), type: 'button',
            'data-id': x.id, 'aria-pressed': chosen ? 'true' : 'false', 'aria-label': x.name + ': ' + midiWhat(x.action),
            onclick: function () { midiSel = chosen ? null : { ctl: c.name, id: x.id }; draw(d); } },
            h('span', { class: 'ctlname', text: x.name }), h('span', { class: 'ctlwhat', text: midiWhat(x.action) + (x.guard ? ' 2x' : '') }), h('span', { class: 'ctlval' }));
          b.style.gridRow = String(x.row + 1);
          b.style.gridColumn = String(x.col + 1);
          grid.appendChild(b);
        });
        card.appendChild(h('div', { class: 'ctlscroll' }, grid));
        if (open) card.appendChild(detail(c, open));
        card.appendChild(h('p', { class: 'hint', text: 'Move a control and it lights up here. Tap one to see' + (can('full') ? ' or change' : '') + ' what it does.' +
          (c.lights ? ' A small ring marks a control that has a light; it is filled while the box has that light on.' : '') +
          (c.controls.some(function (x) { return x.guard; }) ? ' 2x: press twice within a second.' : '') +
          (c.controls.some(function (x) { return x.action && x.action.action === 'bank_pad'; }) ? ' The pad buttons play bank ' + 'ABC'[d.bank || 0] + ' now.' : '') }));
        card.appendChild(h('p', { class: 'hint ctlnote', text: c.profile.note || c.profile.description }));
        if (mine && can('full')) card.appendChild(h('div', { class: 'row' }, h('button', { class: 'btn small ctlreset', text: 'Back to the standard for the whole controller', onclick: function (ev) {
          confirmRow('Put the ' + plural(mine, 'control') + ' you changed on ' + c.profile.name + ' back to the standard?', 'Back to the standard', 'Keep mine', function () {
            act('POST', '/api/midi/map', { reset: { controller: c.name } }, function (data) { say(c.profile.name + ' is back to the standard'); changed(data); });
          }, ev.currentTarget);
        } })));
      });
      lights(d);
    }
    function load() {
      api('GET', '/api/midi').then(function (r) {
        if (!document.getElementById('midictls')) return;         // the page was left: stop asking
        if (r.ok) {
          var now = signature(r.data);
          if (now !== sig && !document.getElementById('confirmrow')) { sig = now; draw(r.data); } else lights(r.data);
        } else { sig = null; wrap.textContent = ''; wrap.hidden = true; }
        clearTimeout(midiLightTimer);
        midiLightTimer = setTimeout(load, r.ok ? 700 : 3000);
      });
    }
    clearTimeout(midiLightTimer);
    midiLightTimer = setTimeout(load, 0);
    return wrap;
  }
  function midiCard() {
    var card = h('div', { class: 'card', id: 'midicard' }, h('h2', { text: 'Mappings' }));
    var body = h('div', { class: 'list sp', id: 'midibody' });
    card.appendChild(body);
    function describe(e) {
      var what = MIDI_ACTIONS.filter(function (a) { return a[0] === e.action; })[0];
      var ctl = (e.kind === 'note' ? 'note ' : e.kind === 'cc' ? 'CC ' : 'program ') + e.number + (e.channel ? ' ch ' + e.channel : '');
      var pad = e.action === 'pad' ? ' ' + 'ABC'[e.bank] + (e.index + 1) : e.action === 'bank_pad' ? ' ' + (e.index + 1) : '';
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
      if (midiForm.action === 'pad') entry.bank = midiForm.bank;
      if (midiForm.action === 'pad' || midiForm.action === 'bank_pad') entry.index = midiForm.index;
      act('POST', '/api/midi/map', { add: entry }, function (data) { say('Mapped: ' + describe(entry)); draw(data); });
    }
    function draw(d) {
      body.textContent = '';
      var names = d.devices.map(function (x) { return x.name + (x.connected ? '' : ' (not reading)'); });
      body.appendChild(h('div', { class: 'state', id: 'midiline', text: !d.enabled ? 'Off' : (d.devices.length ? d.devices.length + ' controller' + (d.devices.length > 1 ? 's' : '') + ': ' + names.join(', ') + (d.last ? '. Last: ' + d.last : '') : 'On, waiting for a controller to be plugged in') }));
      body.appendChild(toggle('midibuiltin', 'Built-in map', d.builtin, function (v) {
        act('POST', '/api/midi', { builtin: v }, function (data) { draw(data); say('Saved'); });
      }, 'For a controller the box does not know: notes 36 to 71 play pads, CC 20 to 25 are levels. Your own mappings win over it and over a known controller\'s layout.'));
      body.appendChild(h('div', { class: 'field', text: 'Your mappings' }));
      if (!d.map.length) body.appendChild(h('div', { class: 'hint', id: 'midinomap', text: 'None yet. A mapping makes one knob, fader or button do one thing. Choose what it should do below, tap Learn a control, then move or press it.' }));
      var maplist = h('div', { class: 'list sp', id: 'midimaplist' });
      if (d.map.length) body.appendChild(maplist);
      d.map.forEach(function (e) {
        maplist.appendChild(listRow({ cls: 'midi-entry', name: describe(e),
          primary: h('button', { class: 'btn plain', text: 'Remove', 'aria-label': 'Remove ' + describe(e), onclick: function (ev) {
            confirmRow('Remove this mapping? The control goes back to what it did before.', 'Remove', 'Keep it', function () {
              act('POST', '/api/midi/map', { remove: e.id }, function (data) { draw(data); say('Mapping removed.'); });
            }, ev.currentTarget);
          } }) }));
      });
      if (d.map.length > 1) body.appendChild(h('div', { class: 'row' }, h('button', { class: 'btn', id: 'midiclear', text: 'Remove all mappings', onclick: function (ev) {
        confirmRow('Remove all ' + plural(d.map.length, 'mapping') + '?', 'Remove all', 'Keep them', function () {
          act('POST', '/api/midi/map', { clear: true }, function (data) { draw(data); });
        }, ev.currentTarget);
      } })));
      if (!d.enabled) return;
      var action = h('select', { class: 'text-input', id: 'midiaction' },
        MIDI_ACTIONS.map(function (a) { return h('option', { value: a[0], text: a[1], selected: a[0] === midiForm.action }); }));
      var bank = h('select', { class: 'text-input', id: 'midibank' },
        ['A', 'B', 'C'].map(function (n, i) { return h('option', { value: i, text: 'Bank ' + n, selected: i === midiForm.bank }); }));
      var index = h('select', { class: 'text-input', id: 'midiindex' },
        Array.apply(null, Array(12)).map(function (_, i) { return h('option', { value: i, text: 'Pad ' + (i + 1), selected: i === midiForm.index }); }));
      var bankWrap = labelled('Bank', bank), indexWrap = labelled('Pad', index);
      function fit() {
        bankWrap.hidden = action.value !== 'pad';
        indexWrap.hidden = action.value !== 'pad' && action.value !== 'bank_pad';
      }
      function remember() {
        midiForm = { action: action.value, bank: parseInt(bank.value, 10), index: parseInt(index.value, 10) };
        fit();
      }
      [action, bank, index].forEach(function (el) { el.addEventListener('change', remember); });
      var form = h('div', { class: 'addform', id: 'midiaddform' }, h('div', { class: 'field', text: 'Add a mapping' }),
        labelled('What it does', action), bankWrap, indexWrap);
      fit();
      if (d.learn.active) {
        form.appendChild(h('div', { class: 'msg', id: 'midilearning', role: 'status', text: 'Move or press a control on any controller now (' + d.learn.seconds_left + ' s)...' }));
        form.appendChild(h('div', { class: 'row' }, h('button', { class: 'btn grow', id: 'midicancel', text: 'Cancel', onclick: function () {
          clearTimeout(midiTimer); act('POST', '/api/midi/learn', { start: false }, function (data) { draw(data); });
        } })));
        poll();
      } else {
        form.appendChild(h('div', { class: 'row' }, h('button', { class: 'btn on pri grow', id: 'midilearn', text: 'Learn a control', onclick: function () {
          remember();
          act('POST', '/api/midi/learn', { start: true }, function (data) { draw(data); });
        } })));
        form.appendChild(h('div', { class: 'hint', text: 'The box waits 15 seconds or so for you to move or press the control, then saves the mapping.' }));
      }
      body.appendChild(form);
    }
    midiDrawCard = draw;
    api('GET', '/api/midi').then(function (r) {
      if (!document.getElementById('midicard')) return;
      if (!r.ok) { body.textContent = ''; body.appendChild(h('div', { class: 'hint', id: 'midimsg', text: r.data.error || 'Not available' })); return; }
      draw(r.data);
    });
    return card;
  }
  // ---- audio output -----------------------------------------------------
  function audioCard(full) {
    var body = h('div', { class: 'list sp', id: 'audiobody' });
    var card = h('div', { class: 'card', id: 'audiocard' }, h('h2', { text: 'Sound output' }), body);
    function label(d) { return d.description ? d.description + ' (' + d.name.replace(/^alsa\//, '') + ')' : d.name; }
    function draw(d, saved) {
      body.textContent = '';
      var auto = d.devices.filter(function (x) { return x.name === d.automatic_is; })[0];
      var now = d.devices.filter(function (x) { return x.name === (d.device === 'auto' ? d.automatic_is : d.device); })[0];
      body.appendChild(h('div', { class: 'state', id: 'audioline', text: d.device === 'auto' ?
        'Automatic' + (now ? ': ' + label(now) : '') + '. On a Pi this is the HDMI port with the screen on it.' : 'Fixed to ' + (now ? label(now) : d.device) + '.' }));
      if (full) {
        var result = h('div', { class: 'msg inmsg', id: 'audiosaved', role: 'status', text: saved ? 'Saved' : '' });
        var sel = h('select', { class: 'text-input', id: 'audiodev', onchange: function () {
          api('POST', '/api/audio', { device: sel.value }).then(function (r) {
            if (!r.ok) return sayAt(result, (r.data.error || 'Could not change the output') + '. Choose another output, or Automatic.', true);
            say('Saved'); draw(r.data, true);
          });
        } },
          [h('option', { value: 'auto', text: 'Automatic: ' + (auto ? auto.description : 'the player\'s own choice'), selected: d.device === 'auto' })].concat(
            d.devices.filter(function (x) { return x.name !== 'auto'; }).map(function (x) { return h('option', { value: x.name, text: label(x), selected: x.name === d.device }); })));
        body.appendChild(labelled('Where the sound comes out', sel, 'Applies as soon as you choose.'));
        body.appendChild(result);
      }
      if (can('live')) {
        var toneOut = h('div', { class: 'msg inmsg', id: 'toneresult', role: 'status' });
        body.appendChild(h('div', { class: 'field', text: 'Test sound' }));
        body.appendChild(h('div', { class: 'hint', text: 'Five seconds of a steady tone, to check left and right.' }));
        body.appendChild(h('div', { class: 'row', id: 'tonerow' }, [['left', 'Left'], ['both', 'Both'], ['right', 'Right']].map(function (c) {
          return h('button', { class: 'btn grow', id: 'tone-' + c[0], text: c[1], onclick: function (e) {
            function go() { act('POST', '/api/testtone', { channel: c[0] }, function () { sayAt(toneOut, 'Playing a test sound: ' + c[1].toLowerCase() + '.'); poll(); }); }
            if (!playingNow()) return go();
            confirmRow('Play a test sound? It stops what is playing now.', 'Play the test sound', 'Not now', go, e.currentTarget);
          } });
        })));
        body.appendChild(toneOut);
      }
    }
    api('GET', '/api/audio').then(function (r) {
      if (!document.getElementById('audiocard')) return;
      if (!r.ok) { body.textContent = ''; body.appendChild(h('div', { class: 'hint', id: 'audiomsg', text: (r.data.error || 'Not available') + '. Open the page again in a moment.' })); return; }
      draw(r.data);
    });
    return card;
  }
  // ---- autostart -------------------------------------------------------
  var autoForm = null;  // survives redraws: { mode, file, preset, loop, delay }; null while nothing was changed
  var autoTried = '';   // what "Try it now" answered, said under the button by the redraw that follows
  function autostartCard(full) {
    var body = h('div', { class: 'list sp', id: 'autobody' });
    var card = h('div', { class: 'card', id: 'autocard' }, h('h2', { text: 'At power-up' }), body);
    var MODES = [['off', 'Nothing: the box waits for you'], ['file', 'Play one clip'], ['all', 'Play every clip'], ['slideshow', 'Slideshow of the pictures'],
      ['pad', 'Play a pad'], ['usb', 'Play the USB stick (and any stick plugged in later)'], ['vibes', 'Start Vibes'], ['preset', 'Old start script']];
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
      body.appendChild(h('div', { class: 'state', id: 'autoline', text: line }));
      if (d.last) body.appendChild(h('div', { class: d.last.ok ? 'hint' : 'hint warn', id: 'autolast', text: 'Last run ' + d.last.at + ': ' + (d.last.ok ? 'started' : 'failed, ' + d.last.message) }));
      if (!full) return;
      var vibesOff = !moduleOn('shaders');
      var mode = h('select', { class: 'text-input', id: 'automode' },
        MODES.map(function (m) { return h('option', { value: m[0], text: m[1] + (m[0] === 'vibes' && vibesOff ? ' (Vibes is off)' : ''), selected: m[0] === c.mode }); }));
      var file = h('select', { class: 'text-input', id: 'autofile' },
        S.media.length ? S.media.map(function (n) { return h('option', { value: n, text: n, selected: n === (c.file || S.media[0]) }); }) : [h('option', { value: '', text: 'No clips on the box yet' })]);
      var preset = h('input', { class: 'text-input mono', id: 'autopreset', placeholder: 'startlessonce05', value: c.preset, autocomplete: 'off' });
      var pads = [];
      (S.banks || []).forEach(function (bk, bi) { (bk.pads || []).forEach(function (pd, pi) { if (pd.file) pads.push([bi, pi]); }); });
      var pad = h('select', { class: 'text-input', id: 'autopad' }, pads.length ? pads.map(function (p) {
        return h('option', { value: p.join(','), text: padName(p), selected: p[0] === c.pad[0] && p[1] === c.pad[1] });
      }) : [h('option', { value: '', text: 'No pad has a clip yet' })]);
      var seconds = h('input', { class: 'text-input mono', id: 'autoseconds', type: 'number', min: 1, max: 3600, value: c.seconds });
      var shuffle = h('select', { class: 'text-input', id: 'autoshuffle' },
        [[false, 'In name order'], [true, 'Shuffled']].map(function (o) { return h('option', { value: String(o[0]), text: o[1], selected: o[0] === c.shuffle }); }));
      var loop = h('select', { class: 'text-input', id: 'autoloop' },
        [[true, 'Play it again, for ever'], [false, 'Stop after one time']].map(function (o) { return h('option', { value: String(o[0]), text: o[1], selected: o[0] === c.loop }); }));
      var delay = h('input', { class: 'text-input mono', id: 'autodelay', type: 'number', min: 0, max: 120, value: c.delay });
      var wrap = { file: labelled('Clip', file), preset: labelled('Start script name', preset, 'The name of an old PocketVJ start script, as it was called on the old box.'),
        pad: labelled('Pad', pad), seconds: labelled('Seconds a picture', seconds), shuffle: labelled('Order', shuffle),
        loop: labelled('When it ends', loop), delay: labelled('Wait after power-up (seconds)', delay, 'Gives a projector or a screen time to wake before the picture starts.') };
      var off = offNotice('autovibesoff', 'vibes', 'Vibes', function () { draw(d); });
      var tryNote = h('span', { class: 'hint', id: 'autotestnote' });
      var tryBtn = h('button', { class: 'btn', id: 'autotest', text: 'Try it now', onclick: function () {
        act('POST', '/api/autostart/test', {}, function (data) {
          autoTried = data.last ? (data.last.ok ? 'Started.' : 'It did not start: ' + data.last.message) : 'Started.';
          draw(data);
        });
      } });
      var bar = saveBar('autosave', 'Save changes', function () {
        remember();
        var b = { mode: autoForm.mode, loop: autoForm.loop, delay: autoForm.delay, shuffle: autoForm.shuffle };
        if (autoForm.mode === 'file') b.file = autoForm.file;
        if (autoForm.mode === 'preset') b.preset = autoForm.preset;
        if (autoForm.mode === 'pad') b.pad = autoForm.pad;
        if (autoForm.mode === 'slideshow') b.seconds = autoForm.seconds;
        api('POST', '/api/autostart', b).then(function (r) {
          if (!r.ok) return bar.say(r.data.error || 'Could not save. Check the fields and try again.', true);
          autoForm = null; autoTried = 'Saved'; draw(r.data); say('Saved');
        });
      }, tryBtn);
      function remember() {
        autoForm = { mode: mode.value, file: file.value, preset: preset.value, loop: loop.value === 'true', delay: parseFloat(delay.value || '0'),
          shuffle: shuffle.value === 'true', seconds: parseFloat(seconds.value || '10'), pad: pad.value ? pad.value.split(',').map(Number) : [0, 0] };
      }
      function show() {
        var m = mode.value;
        wrap.file.hidden = m !== 'file'; wrap.preset.hidden = m !== 'preset'; wrap.pad.hidden = m !== 'pad';
        wrap.seconds.hidden = m !== 'slideshow';
        wrap.shuffle.hidden = ['all', 'slideshow', 'usb'].indexOf(m) < 0;
        wrap.loop.hidden = ['file', 'all', 'slideshow'].indexOf(m) < 0;
        wrap.delay.hidden = m === 'off';
        off.hidden = !(m === 'vibes' && !moduleOn('shaders'));
        var dirty = !!autoForm;
        bar.dirty(dirty);
        tryBtn.disabled = dirty || cfg.mode === 'off' || !can('live');
        tryNote.textContent = dirty ? 'Save first to try it.' : cfg.mode === 'off' ? 'Nothing is set to happen at power-up.' : '';
      }
      [mode, file, preset, pad, seconds, shuffle, loop, delay].forEach(function (el) {
        el.addEventListener('input', function () { remember(); show(); });
        el.addEventListener('change', function () { remember(); show(); });
      });
      body.appendChild(labelled('What happens at power-up', mode));
      body.appendChild(off);
      ['file', 'preset', 'pad', 'seconds', 'shuffle', 'loop', 'delay'].forEach(function (k) { body.appendChild(wrap[k]); });
      body.appendChild(bar.el);
      body.appendChild(tryNote);
      show();
      if (autoTried) { bar.result.textContent = autoTried; bar.result.className = 'msg inmsg' + (/did not/.test(autoTried) ? ' err' : ''); autoTried = ''; }
      body.appendChild(h('div', { class: 'hint', text: 'It runs when the box starts, and again if the player is restarted after a crash. A Stop from the panel is not undone. "Play the USB stick" also plays each new stick the moment it is plugged in.' }));
    }
    api('GET', '/api/autostart').then(function (r) {
      if (!document.getElementById('autocard')) return;
      if (!r.ok) { body.textContent = ''; body.appendChild(h('div', { class: 'hint', id: 'automsg', text: (r.data.error || 'Not available') + '. Open the page again in a moment.' })); return; }
      draw(r.data);
    });
    return card;
  }
  // ---- streams (SRT, RTSP, RTMP) --------------------------------------
  var streamForm = { name: '', url: '' };  // survives redraws
  function streamsCard(full) {
    var body = h('div', { class: 'list sp', id: 'streambody' });
    var card = h('div', { class: 'card', id: 'streamcard' }, h('h2', { text: 'Streams' }), body);
    function draw(d) {
      body.textContent = '';
      var playing = ((S.status && S.status.player) || {}).stream;
      if (!d.streams.length) body.appendChild(h('div', { class: 'empty', id: 'streamempty', text: 'No streams yet. A stream is live video from a camera or another computer on the network.' +
        (full ? ' Type its address below to save it; then it plays like a clip.' : '') }));
      d.streams.forEach(function (st) {
        body.appendChild(listRow({ cls: 'stream-entry', data: st.id, name: st.name, sub: st.url, key: 'stream-' + st.id, redraw: function () { draw(d); },
          state: playing === st.name ? 'Playing now' : '',
          primary: can('live') ? h('button', { class: 'btn on pri', text: 'Play', 'aria-label': 'Play ' + st.name,
            onclick: function () { act('POST', '/api/play', { stream: st.id }, function () { say('Playing ' + st.name); poll(); }); } }) : null,
          more: full ? [h('button', { class: 'btn', text: 'Remove', 'aria-label': 'Remove ' + st.name, onclick: function (e) {
            confirmRow('Remove ' + st.name + '? Its address is forgotten.', 'Remove', 'Keep it', function () {
              act('POST', '/api/streams', { action: 'remove', id: st.id }, function (data) { moreOpen = null; draw(data); say(st.name + ' is removed.'); });
            }, e.currentTarget);
          } })] : null }));
      });
      if (!full) return;
      body.appendChild(addBlock('stream', 'a stream', !d.streams.length, function (cancel) {
        var name = h('input', { class: 'text-input', id: 'streamname', placeholder: 'Stage camera', maxlength: 40, value: streamForm.name });
        var url = h('input', { class: 'text-input mono', id: 'streamurl', placeholder: 'srt://192.168.1.20:9000', value: streamForm.url, autocomplete: 'off' });
        var err = h('div', { class: 'msg inmsg', id: 'streamerr', role: 'alert' });
        name.addEventListener('input', function () { streamForm.name = name.value; });
        url.addEventListener('input', function () { streamForm.url = url.value; });
        return h('div', { class: 'addform', id: 'streamform' }, h('div', { class: 'field', text: 'Add a stream' }),
          labelled('Name', name), labelled('Address', url, 'Starts with srt://, rtsp:// or rtmp://. A login in the address is kept on the box and hidden here.'),
          h('div', { class: 'row' },
            h('button', { class: 'btn on pri grow', id: 'streamadd', text: 'Add', onclick: function () {
              api('POST', '/api/streams', { action: 'add', name: streamForm.name, url: streamForm.url }).then(function (r) {
                if (!r.ok) return sayAt(err, r.data.error || 'Could not add the stream. Check the address.', true);
                streamForm.name = ''; streamForm.url = ''; addOpen.stream = false; draw(r.data); say('Stream added.');
              });
            } }),
            cancel ? h('button', { class: 'btn grow', id: 'streamcancel', text: 'Cancel', onclick: cancel }) : null),
          err);
      }, function () { draw(d); }));
    }
    api('GET', '/api/streams').then(function (r) {
      if (!document.getElementById('streamcard')) return;
      if (!r.ok) { body.textContent = ''; body.appendChild(h('div', { class: 'hint', id: 'streammsg', text: (r.data.error || 'Not available') + '. Open the page again in a moment.' })); return; }
      draw(r.data);
    });
    return card;
  }
  // ---- updates (signed bundles, installed by pvj-update as root; D33) ----
  var updateTimer = null;
  function updateCard() {
    var body = h('div', { class: 'list sp', id: 'updatebody' }, h('div', { class: 'hint', text: 'Loading...' }));
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
    function start(source, version, where, control) {
      confirmRow('Install version ' + version + where + '? The panel and the player restart, so the picture stops for a moment. If the new version does not come up, the box goes back to this one by itself.',
        'Install', 'Not now', function () {
          act('POST', '/api/system/update', { source: source, version: version, confirm: 'update' }, function () {
            say('Update to ' + version + ' started.');
            startedAt = Date.now();
            watchUntil = startedAt + 10 * 60 * 1000;
            later();
          });
        }, control);
    }
    function row(name, state, problem, id, go) {
      return listRow({ cls: 'update-entry', name: name, state: state, problem: problem,
        primary: h('button', { class: 'btn on pri', id: id, text: 'Install', onclick: function (e) { go(e.currentTarget); } }) });
    }
    function draw(d) {
      clearTimeout(updateTimer);
      if (asking(body)) { updateTimer = setTimeout(refresh, 3000); return; }       // a question is open: look again later
      body.textContent = '';
      var waiting = d.usb.length + d.inbox.length;
      body.appendChild(h('div', { class: 'state', id: 'updateversion', text: 'Version ' + d.version + ' is installed. ' + (waiting ? 'An update is waiting below.' : 'No update is waiting.') }));
      var last = d.last;
      if (last) {
        var words = { running: 'Updating: ', done: 'Last update: ', failed: 'Last update failed: ' };
        body.appendChild(h('div', { class: last.state === 'failed' ? 'hint warn' : 'hint', id: 'updatelast', text: (words[last.state] || '') + last.message + (last.at ? ' (' + new Date(last.at * 1000).toLocaleString() + ')' : '') }));
        if (last.state === 'running') watchUntil = Math.max(watchUntil, Date.now() + 60 * 1000);
        else if (startedAt && last.at && last.at * 1000 >= startedAt - 5000) watchUntil = 0;   // the update we started has ended
      }
      var seen = {};
      d.usb.forEach(function (b, i) {
        if (seen[b.version]) return;
        seen[b.version] = true;
        body.appendChild(row('Version ' + b.version, 'On the USB drive ' + b.drive, b.signed ? '' : 'Its .sig file is missing from the stick, so it will be refused. Copy both files to the stick.',
          'updateusb' + i, function (control) { start('usb', b.version, ' from the USB drive', control); }));
      });
      d.inbox.forEach(function (b, i) {
        body.appendChild(row('Version ' + b.version, 'Uploaded', b.signed ? '' : 'Its .sig file is missing. Upload that file too.',
          'updateinbox' + i, function (control) { start('inbox', b.version, '', control); }));
      });
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
            .then(function () { say(f.name + ' uploaded.'); next(); }, function (e) { say((e.message || 'The upload failed') + '. Choose the two update files and try again.', true); refresh(); });
        };
        next();
      });
      body.appendChild(pick);
      body.appendChild(h('div', { class: 'row' }, h('button', { class: 'btn grow', id: 'updateupload', text: 'Upload an update', onclick: function () { pick.click(); } })));
      body.appendChild(h('details', { class: 'fold', id: 'updatehow' }, h('summary', { text: 'How to get an update' }), h('div', { class: 'list sp' },
        h('div', { class: 'hint', text: 'An update is two files with the same name: pvj-N.N.N.tar.gz and pvj-N.N.N.tar.gz.sig (N.N.N is the version). A .sha256 file may come with them and is optional.' }),
        h('div', { class: 'hint', text: 'From a USB stick: put both files in a folder named pvj-update on the stick, plug it into the box, and the version shows here.' }),
        h('div', { class: 'hint', text: 'From this phone or laptop: tap Upload an update and choose both files.' }),
        h('div', { class: 'hint', text: 'Only updates signed with your key are installed. An older version is refused. A failed update goes back by itself.' }))));
      later();
    }
    setTimeout(refresh, 0);   // the card is put on the page after this returns; refresh() asks nothing until it is
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
    if (pw) careForm.passwords = pw.getAttribute('aria-checked') === 'true';       // read from the page before it is rebuilt
    if (md) careForm.media = md.value;
    var remote = !!(S.device && S.device.remote);
    var cards = [];

    // settings
    var tick = toggle('exportpw', 'Passwords in the file', careForm.passwords, function (v) { careForm.passwords = v; },
      'Projector passwords and stream logins. Keep a file that has them private.');
    var result = h('div', { class: 'msg inmsg', id: 'importresult', role: 'status' });
    var pick = h('input', { type: 'file', id: 'importpick', accept: '.json,application/json', hidden: true });
    function load(f) {
      say('Importing ' + f.name + '...');
      fetch('/api/system/settings/import?confirm=import', { method: 'POST', credentials: 'same-origin',
        headers: { 'X-PVJ-Request': '1', 'Content-Type': 'application/json' }, body: f })
        .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, status: r.status, data: j }; }); },
          function () { return { ok: false, status: 0, data: { error: 'no connection' } }; })
        .then(function (r) {
          if (!r.ok) return sayAt(document.getElementById('importresult'), (r.data.error || 'The import failed (HTTP ' + r.status + ')') + '. Nothing was changed. Choose a settings file this box or another one exported.', true);
          var lines = (r.data.problems || []).map(function (t) { return 'Check: ' + t; }).concat(r.data.notes || []);
          if (!r.data.passwords_in_file) lines.push('The file holds no passwords; ' + r.data.passwords_kept + ' already on this box were kept.');
          loadAll().then(function () {
            render();
            say('Settings imported.' + (r.data.problems && r.data.problems.length ? ' Some parts need a look (see the Settings file card).' : ''));
            var el = document.getElementById('importresult');
            if (el) el.textContent = lines.join(' · ');
          });
        });
    }
    pick.addEventListener('change', function () {
      var f = pick.files && pick.files[0];
      pick.value = '';
      var control = document.getElementById('importbtn');
      if (!f || !control) return;
      confirmRow('Replace this box\'s settings with ' + f.name + '? The PIN, the paired devices and remote support stay as they are. A copy of the present settings is kept on the box.',
        'Replace the settings', 'Keep mine', function () { load(f); }, control);
    });
    cards.push(h('div', { class: 'card', id: 'settingscard' }, h('h2', { text: 'Settings file' }),
      h('div', { class: 'list sp' },
        h('div', { class: 'hint', text: 'Save this box\'s settings as one file, or load them from one. The PIN, the paired devices and remote support are never in the file.' }),
        remote ? null : tick,
        h('div', { class: 'row' },
          h('button', { class: 'btn grow', id: 'exportbtn', text: 'Export settings', onclick: function () {
            act('POST', '/api/system/settings/export', { passwords: !remote && tick.isOn() }, function (d) {
              saveFile(d.name, d.file);
              sayAt(document.getElementById('importresult'), 'Settings saved as ' + d.name + (d.file.passwords_included ? ' (with passwords).' : ' (no passwords in it).'));
            });
          } }),
          remote ? null : h('button', { class: 'btn grow', id: 'importbtn', text: 'Import settings...', onclick: function () { pick.click(); } })),
        pick, result)));

    // diagnostics
    var note = h('div', { class: 'msg inmsg', id: 'diagnote', role: 'status' });
    cards.push(h('div', { class: 'card', id: 'diagcard' }, h('h2', { text: 'Diagnostics' }),
      h('div', { class: 'list sp' },
        h('div', { class: 'hint', text: 'One file to send to whoever is helping you: versions, the board, modules, health and the settings. No PIN, password, code or key is in it.' }),
        h('div', { class: 'row' }, h('button', { class: 'btn grow', id: 'diagbtn', text: 'Download diagnostics file', onclick: function () {
          act('GET', '/api/system/diagnostics', null, function (d) {
            saveFile(d.name, d.file);
            say('Diagnostics saved as ' + d.name + '.');
            note.textContent = d.file.log && d.file.log.note ? 'Log: ' + d.file.log.note : 'Saved as ' + d.name + '.';
          });
        } })),
        note)));

    // factory reset: never through remote support
    if (!remote) {
      var media = h('select', { class: 'text-input', id: 'resetmedia' },
        [['', 'Choose...'], ['keep', 'Keep the clips on the box'], ['delete', 'Delete the clips too']].map(function (o) {
          return h('option', { value: o[0], text: o[1], selected: o[0] === careForm.media });
        }));
      media.addEventListener('change', function () { careForm.media = media.value; });
      var resetErr = h('div', { class: 'msg inmsg', id: 'resetresult', role: 'alert' });
      cards.push(h('div', { class: 'card', id: 'resetcard' }, h('h2', { text: 'Factory reset' }),
        h('div', { class: 'list sp' },
          h('div', { class: 'hint', text: 'Every setting goes back to how a new box starts, and every phone, tablet and guest is unpaired. You pair again with the new PIN on the box\'s display.' }),
          labelled('What happens to the clips', media),
          h('div', { class: 'row' }, h('button', { class: 'btn grow', id: 'resetbtn', text: 'Reset to factory settings', onclick: function (e) {
            if (!media.value) return sayAt(resetErr, 'Choose what happens to the clips first.', true);
            var clips = media.value === 'delete' ? 'ALL CLIPS ON THE BOX ARE DELETED.' : 'The clips stay.';
            confirmRow('Reset this box to factory settings? All settings are lost and every device is unpaired, this one too. ' + clips + ' This cannot be undone.',
              'Reset this box', 'Keep everything', function () {
                act('POST', '/api/system/factory-reset', { confirm: 'factory-reset', media: media.value }, function () {
                  careForm = { passwords: false, media: '' };
                  S.device = null;
                  S.msg = '';
                  render();
                });
              }, e.currentTarget);
          } })),
          resetErr)));
    }
    return cards;
  }

  // ---- multi-box sync and video wall -----------------------------------
  var syncTimer = null;
  // What was chosen or typed in the card and is not saved yet (vals), and the saved value each field was drawn from
  // (drawn). The card is rebuilt by every answer and, while the box is a server or a client, every 2 seconds: a
  // rebuild between choosing a column and pressing Save put the saved column back, and Save then sent that.
  var syncForm = { vals: {}, drawn: {}, wallOpen: false, choosing: false };
  var SYNC_FIELDS = ['syncgroup', 'wallcols', 'wallrows', 'wallcol', 'wallrow', 'wallbezel'];
  // Read the page just before anything is rebuilt (as the Network card does): a field that differs from what it was
  // drawn from is kept.
  function keepSyncForm() {
    SYNC_FIELDS.forEach(function (id) {
      var el = document.getElementById(id);
      if (!el || typeof el.value !== 'string') return;
      if (el.value !== syncForm.drawn[id]) syncForm.vals[id] = el.value; else delete syncForm.vals[id];
    });
  }
  function syncCard() {
    var body = h('div', { class: 'list sp', id: 'syncbody' }, h('div', { class: 'hint', text: 'Loading...' }));
    var card = h('div', { class: 'card', id: 'synccard' }, h('h2', { text: 'Boxes in step' }), body);
    var full = can('full');
    // Saves are counted: an answer that is older than a later save does not clear that save's message and is not
    // drawn over it; the card asks again instead. `sent` is what the fields held at the click: once saved, a field
    // that still holds it is no longer a change, and one that was changed again meanwhile is kept.
    var posts = 0;
    function post(b, sent) {
      var n = ++posts;
      return act('POST', '/api/sync', b, function (data) {
        if (n !== posts) return refresh();
        say('Saved');
        Object.keys(sent || {}).forEach(function (id) { syncForm.drawn[id] = sent[id]; });
        draw(data);
      });
    }
    function refresh() {
      var n = posts;
      api('GET', '/api/sync').then(function (r) {
        if (!document.getElementById('synccard') || !r.ok) return;
        if (n !== posts) return refresh();
        draw(r.data);
      });
    }
    function field(id, saved) {
      syncForm.drawn[id] = String(saved);
      return id in syncForm.vals ? syncForm.vals[id] : String(saved);
    }
    function draw(d) {
      clearTimeout(syncTimer);
      if (asking(body)) { syncTimer = setTimeout(refresh, 2000); return; }
      keepSyncForm();
      body.textContent = '';
      var c = d.config, f = d.follow || {};
      var line = c.role === 'off' ? 'Off: this box plays on its own.' :
        c.role === 'server' ? 'This box leads: it is the server of group "' + c.group + '". ' + (d.sent ? d.sent + ' messages sent.' : 'Nothing sent yet.') :
        'This box follows: it is a client of group "' + c.group + '", ' + (d.server ? 'following ' + d.server + '. ' : 'listening for the box that leads. ') +
          (f.state || '') + (f.file ? ', ' + f.file : '') + (typeof f.error_ms === 'number' ? ', ' + f.error_ms + ' ms off' : '');
      body.appendChild(h('div', { class: 'state', id: 'syncline', text: line }));
      if (c.role !== 'off') syncTimer = setTimeout(refresh, 2000);
      if (!full) return;
      function choose(role) { syncForm.choosing = false; post({ role: role }); }
      if (c.role === 'off' || syncForm.choosing) {
        // the first thing asked: what this box does. Two large choices.
        body.appendChild(h('div', { class: 'field', id: 'syncask', text: 'What does this box do?' }));
        body.appendChild(h('div', { class: 'list sp', id: 'syncroles' },
          h('button', { class: 'btn bigchoice' + (c.role === 'server' ? ' on' : ''), id: 'syncrole-server', 'aria-pressed': c.role === 'server' ? 'true' : 'false', onclick: function () { choose('server'); } },
            h('b', { text: 'Lead' }), h('span', { text: 'Other boxes follow this one.' })),
          h('button', { class: 'btn bigchoice' + (c.role === 'client' ? ' on' : ''), id: 'syncrole-client', 'aria-pressed': c.role === 'client' ? 'true' : 'false', onclick: function () { choose('client'); } },
            h('b', { text: 'Follow' }), h('span', { text: 'This box follows another.' })),
          c.role !== 'off' ? h('button', { class: 'btn', id: 'syncrole-off', text: 'Neither: play on its own', onclick: function (e) {
            confirmRow(c.role === 'server' ? 'Stop leading? The other boxes stop following this one.' : 'Stop following? This box plays on its own.', 'Play on its own', 'Keep it', function () { choose('off'); }, e.currentTarget);
          } }) : null));
        if (c.role === 'off') body.appendChild(h('div', { class: 'hint', text: 'One box leads and the others follow its clip, position, pause and blackout. Every box needs the same clips with the same file names.' }));
      } else {
        body.appendChild(h('div', { class: 'row wrap', id: 'syncroles' },
          h('button', { class: 'btn grow', id: 'syncchange', text: 'Change what this box does', onclick: function () { syncForm.choosing = true; draw(d); } }),
          h('button', { class: 'btn grow', id: 'syncrole-off', text: 'Play on its own', onclick: function (e) {
            confirmRow(c.role === 'server' ? 'Stop leading? The other boxes stop following this one.' : 'Stop following? This box plays on its own.', 'Play on its own', 'Keep it', function () { choose('off'); }, e.currentTarget);
          } })));
      }
      if (c.role === 'off') return;
      var group = h('input', { class: 'text-input mono', id: 'syncgroup', value: field('syncgroup', c.group), maxlength: 24 });
      var groupOut = h('div', { class: 'msg inmsg', id: 'syncgroupresult', role: 'status' });
      body.appendChild(h('div', { class: 'fieldwrap' }, h('label', { class: 'field', for: 'syncgroup', text: 'Group name' }),
        h('div', { class: 'row' }, group, h('button', { class: 'btn', id: 'syncgroupsave', text: 'Save', onclick: function () { post({ group: group.value.trim() }, { syncgroup: group.value }); } })),
        h('div', { class: 'hint', text: 'The same on every box that plays together.' }), groupOut));
      body.appendChild(h('div', { class: 'hint', text: 'Every box needs the same clips with the same file names (in its media folder or at the top of a USB drive).' }));
      // The video wall: folded unless it is in use
      var w = c.wall, nums = function (lo, hi) { var a = []; for (var i = lo; i <= hi; i++) a.push(i); return a; };
      var inUse = w.cols > 1 || w.rows > 1;
      function pick(id, values, saved, fmt) {
        var cur = field(id, saved);
        return h('select', { class: 'text-input', id: id }, values.map(function (v) { return h('option', { value: String(v), text: fmt(v), selected: String(v) === cur }); }));
      }
      var cols = pick('wallcols', nums(1, 8), w.cols, function (v) { return v + (v === 1 ? ' column' : ' columns'); });
      var rows = pick('wallrows', nums(1, 8), w.rows, function (v) { return v + (v === 1 ? ' row' : ' rows'); });
      // this screen's place: only the columns and rows the chosen size has
      var col = pick('wallcol', nums(0, +cols.value - 1), w.col, function (v) { return 'Column ' + (v + 1); });
      var row = pick('wallrow', nums(0, +rows.value - 1), w.row, function (v) { return 'Row ' + (v + 1); });
      function limit(sel, n, word) {
        var cur = Math.min(+sel.value || 0, n - 1);
        sel.textContent = '';
        nums(0, n - 1).forEach(function (v) { sel.appendChild(h('option', { value: String(v), text: word + ' ' + (v + 1), selected: v === cur })); });
      }
      cols.addEventListener('change', function () { limit(col, +cols.value, 'Column'); });
      rows.addEventListener('change', function () { limit(row, +rows.value, 'Row'); });
      var bezel = h('input', { class: 'text-input mono', id: 'wallbezel', type: 'number', min: 0, max: 20, step: 0.5, value: field('wallbezel', w.bezel) });
      var wall = h('details', { class: 'fold', id: 'wallfold', open: inUse || syncForm.wallOpen },
        h('summary', { text: inUse ? 'Video wall: this screen is column ' + (w.col + 1) + ', row ' + (w.row + 1) + ' of ' + w.cols + ' by ' + w.rows : 'Video wall (not in use)' }),
        h('div', { class: 'list sp' },
          h('div', { class: 'hint', text: 'Several screens show one big picture, each its own part. 1 column and 1 row means no wall: this screen shows the whole picture.' }),
          h('div', { class: 'row wrap' }, labelled('Columns of the wall', cols), labelled('Rows of the wall', rows)),
          h('div', { class: 'row wrap' }, labelled('This screen\'s column', col), labelled('This screen\'s row', row)),
          labelled('Frame between screens (percent)', bezel, 'How much of a screen\'s width its frame takes. That much picture is hidden, so lines stay straight across screens.'),
          h('div', { class: 'row' }, h('button', { class: 'btn on pri grow', id: 'wallsave', text: 'Save wall', onclick: function () {
            post({ wall: { cols: +cols.value, rows: +rows.value, col: +col.value, row: +row.value, bezel: +bezel.value } },
              { wallcols: cols.value, wallrows: rows.value, wallcol: col.value, wallrow: row.value, wallbezel: bezel.value });
          } }))));
      wall.addEventListener('toggle', function () { syncForm.wallOpen = wall.open; });
      body.appendChild(wall);
    }
    refresh();
    return card;
  }

  // ---- projectors (PJLink) ---------------------------------------------
  var projForm = { name: '', host: '', port: '4352', password: '' };  // survives redraws
  // The projector whose inputs are being named, with what was typed: { id, texts: { code: text } }; null when none.
  var projNaming = null;
  // The projector being edited, in place of its row: { id, name, host, port, password, clear, error }; null when none.
  // It survives redraws (the list is read again every 5 seconds). The password is only ever what was typed here.
  var projEdit = null;
  var projTimer = null;
  // What kind of socket an input is, from the PJLink standard's five kinds (the first digit of its code). The
  // standard names the kinds, not the sockets, so this is a hint; it has not been checked against a real projector.
  var PJ_KIND_HINT = { '1': ' (computer, VGA)', '3': ' (HDMI or DVI)', '4': ' (USB)' };
  var PJ_POWER = { 'on': 'On', 'off': 'Off', 'warming up': 'Warming up', 'cooling down': 'Cooling down' };
  function projectorsCard(full) {
    clearTimeout(projTimer);
    var body = h('div', { class: 'list sp', id: 'projbody' });
    var card = h('div', { class: 'card wide', id: 'projcard' }, h('h2', { text: 'Projectors' }), body);
    var states = {};  // id -> the last answer shown under it
    var shown = null; // what is on the page, to leave it alone while nothing changed
    function plainInput(i) { return i.name + (PJ_KIND_HINT[i.code.charAt(0)] || ''); }
    function inputText(p, code) {
      var i = p.inputs.filter(function (x) { return x.code === code; })[0];
      return i ? (i.label ? i.label + ' (' + i.name + ')' : plainInput(i)) : code;
    }
    function statusText(p) {
      var st = p.status || {};
      if (st.ok === undefined) return st.waiting ? 'Waiting for an earlier check to end' : 'Checking...';
      if (!st.ok) return 'No answer';
      var t = PJ_POWER[st.power] || st.power;
      if (st.input) t += ' · input ' + inputText(p, st.input);
      if (st.mute && (st.mute.picture || st.mute.sound)) t += ' · ' + (st.mute.picture && st.mute.sound ? 'picture and sound' : st.mute.picture ? 'picture' : 'sound') + ' muted';
      if (st.lamps && st.lamps.length) t += ' · lamp ' + st.lamps.map(function (l) { return l.hours; }).join(', ') + ' h';
      return t;
    }
    function warningText(p) {
      var w = (p.status || {}).warnings || {}, out = [];
      ['error', 'warning'].forEach(function (level) {
        var names = Object.keys(w).filter(function (k) { return w[k] === level; });
        if (names.length) out.push((level === 'error' ? 'Error: ' : 'Warning: ') + names.join(', '));
      });
      return out.join('. ');
    }
    function detailsText(p) {
      var d = p.details;
      if (!d) return '';
      var who = [d.maker, d.model].filter(Boolean).join(' ');
      return who + (d.name && d.name !== who ? (who ? ' "' + d.name + '"' : d.name) : '');
    }
    function where(p) { return p.host + (p.port !== 4352 ? ':' + p.port : ''); }
    function keep() {  // what is being typed, copied from the page before it is rebuilt (an input event can be lost)
      [['projname', 'name'], ['projhost', 'host'], ['projport', 'port'], ['projpw', 'password']].forEach(function (f) {
        var el = document.getElementById(f[0]);
        if (el && body.contains(el)) projForm[f[1]] = el.value;
      });
      if (projEdit) [['projeditname', 'name'], ['projedithost', 'host'], ['projeditport', 'port'], ['projeditpw', 'password']].forEach(function (f) {
        var el = document.getElementById(f[0]);
        if (el && body.contains(el) && !el.disabled) projEdit[f[1]] = el.value;
      });
      if (projNaming) Array.prototype.forEach.call(body.querySelectorAll('.proj-nameinput'), function (el) { projNaming.texts[el.getAttribute('data-code')] = el.value; });
    }
    function load(force) {
      clearTimeout(projTimer);
      api('GET', '/api/projectors').then(function (r) {
        if (!document.body.contains(card)) return;
        clearTimeout(projTimer);
        projTimer = setTimeout(function () { if (document.body.contains(card)) load(); }, 5000);
        if (!r.ok) {
          if (shown === null) { body.textContent = ''; body.appendChild(h('div', { class: 'hint', id: 'projmsg', text: (r.data.error || 'Not available') + '. Open the page again in a moment.' })); }
          return;
        }
        var a = document.activeElement;
        var typing = a && body.contains(a) && /^(INPUT|SELECT)$/.test(a.tagName);
        // a question that is open, or a field being typed in, is never drawn over by the 5 second look
        if (force || (JSON.stringify(r.data) !== shown && !typing && !asking(body))) draw(r.data);
      });
    }
    // The add form's fields, filled in, in place of the projector's row. Only what was changed is sent: a password
    // field left empty is not sent at all, so the stored password stays; "Remove the password" sends an empty one.
    function editForm(p) {
      var e = projEdit, ids = 'projedit';
      var name = h('input', { class: 'text-input', id: ids + 'name', maxlength: 40, value: e.name });
      var host = h('input', { class: 'text-input mono', id: ids + 'host', value: e.host, autocomplete: 'off' });
      var port = h('input', { class: 'text-input mono', id: ids + 'port', type: 'number', min: 1, max: 65535, value: e.port });
      var pw = h('input', { class: 'text-input mono', id: ids + 'pw', type: 'password', autocomplete: 'new-password', disabled: e.clear });
      pw.value = e.clear ? '' : e.password;      // the property, not an attribute: a typed password never becomes page HTML
      var err = h('div', { class: 'msg err', id: ids + 'err', role: 'alert', text: e.error || '' });
      var moved = h('div', { class: 'hint', id: ids + 'moved', text: 'A new address or port: the box asks the projector at the new one who it is. The names you gave its inputs are kept.' +
        (p.has_password ? ' The stored password is then used at the new address. If the password belongs to the old projector only, remove it or type the new one.' : '') });
      function read() { e.name = name.value; e.host = host.value; e.port = port.value; if (!e.clear) e.password = pw.value; }
      function change() {           // what would be sent
        var out = { id: p.id }, n = parseInt(e.port, 10);
        if (e.name.trim() !== p.name) out.name = e.name;
        if (e.host.trim() !== p.host) out.host = e.host.trim();
        if (String(n) !== String(p.port)) out.port = n;
        if (e.clear) out.password = ''; else if (e.password) out.password = e.password;
        return out;
      }
      var clear = !p.has_password ? null : h('button', { class: 'switch', id: ids + 'clear', role: 'switch', 'aria-checked': e.clear ? 'true' : 'false', 'aria-label': 'Remove the password',
        onclick: function () { read(); e.clear = !e.clear; if (e.clear) e.password = ''; e.error = ''; draw(JSON.parse(shown), true); } });
      var save = h('button', { class: 'btn on pri grow', id: ids + 'save', text: 'Save changes', onclick: function () {
        read();
        var out = change();
        if (out.port !== undefined && !(out.port >= 1 && out.port <= 65535)) { e.error = 'The port is a number from 1 to 65535 (4352 unless it was changed on the projector).'; err.textContent = e.error; return; }
        save.disabled = true;
        api('POST', '/api/projectors', { edit: out }).then(function (r) {
          if (!projEdit || projEdit.id !== p.id) return;
          if (!r.ok) { e.error = r.data.error || 'Could not save the changes.'; err.textContent = e.error; save.disabled = false; return; }
          projEdit = null; delete states[p.id];
          say('Changes saved.'); draw(r.data, true); load();
        });
      } });
      function fresh() {
        read();
        var out = change();
        save.disabled = Object.keys(out).length < 2;
        moved.hidden = out.host === undefined && out.port === undefined;
        if (e.error) { e.error = ''; err.textContent = ''; }
      }
      [name, host, port, pw].forEach(function (el) { el.addEventListener('input', fresh); });
      var first = change();
      save.disabled = Object.keys(first).length < 2;
      moved.hidden = first.host === undefined && first.port === undefined;
      return h('div', { class: 'item proj-edit', id: 'projedit', 'data-id': p.id },
        h('b', { text: 'Edit ' + p.name }),
        h('label', { class: 'field', for: ids + 'name', text: 'Name' }), name,
        h('label', { class: 'field', for: ids + 'host', text: 'Address' }), host,
        h('div', { class: 'hint', text: 'The projector\'s IP address or name on this network. Only a private address (such as 192.168.x.x) is taken.' }),
        h('label', { class: 'field', for: ids + 'port', text: 'Port' }), port,
        h('div', { class: 'hint', text: '4352 unless it was changed on the projector.' }),
        moved,
        h('label', { class: 'field', for: ids + 'pw', text: 'PJLink password' }), pw,
        h('div', { class: 'hint', id: ids + 'pwhint', text: e.clear ? 'The password will be removed when you save.' :
          p.has_password ? 'A password is set. Type a new one to change it, or leave empty to keep it.' : 'No password is set. Type one if the projector asks for it.' }),
        clear ? h('label', { class: 'rot between' }, h('span', { text: 'Remove the password' }), clear) : null,
        h('div', { class: 'row' }, save,
          h('button', { class: 'btn grow', id: ids + 'cancel', text: 'Cancel', onclick: function () { projEdit = null; draw(JSON.parse(shown), true); } })),
        err);
    }
    function run(pid, action, label, extra) {
      var msg = { id: pid, action: action };
      Object.keys(extra || {}).forEach(function (k) { msg[k] = extra[k]; });
      api('POST', '/api/projector', msg).then(function (r) {
        if (!r.ok) { say((r.data.error || 'The projector did not answer') + '. Check that it has power and is on the network, then try again.', true); return load(true); }
        var failed = [], waiting = false;
        Object.keys(r.data.results).forEach(function (k) {
          var x = r.data.results[k];
          states[k] = x.ok ? (x.power ? 'Power: ' + x.power : (x.pending ? '' : label + ': done')) : 'It did not work: ' + x.error;
          if (!x.ok) failed.push(x.error);
          if (x.pending) waiting = true;
        });
        if (failed.length) say(plural(failed.length, 'projector') + ' did not answer: ' + failed[0] + '. Check its power and network cable, then try again.', true);
        else say(waiting ? 'The projector is not ready yet; trying again for up to 90 seconds.' : label + ': done');
        load(true);
      });
    }
    // The one power button of a projector, from what it last said.
    function powerButton(p) {
      var st = p.status || {}, id = 'projpower-' + p.id;
      function still(text) { return h('button', { class: 'btn proj-power', id: id, text: text, disabled: true }); }
      if (st.ok === undefined) return still('Checking...');
      if (!st.ok) return h('button', { class: 'btn proj-power proj-retry', id: id, text: 'Try again', 'aria-label': 'Try ' + p.name + ' again', onclick: function () { run(p.id, 'state', 'Check now'); } });
      if (st.power === 'warming up') return still('Warming up...');
      if (st.power === 'cooling down') return still('Cooling down...');
      if (st.power === 'on') return h('button', { class: 'btn proj-power', id: id, text: 'Turn off', 'aria-label': 'Turn off ' + p.name, onclick: function (e) {
        confirmRow('Turn off ' + p.name + '? It needs about a minute to cool before it can come on again.', 'Turn off', 'Keep it on', function () { run(p.id, 'off', 'Turn off'); }, e.currentTarget);
      } });
      return h('button', { class: 'btn on proj-power', id: id, text: 'Turn on', 'aria-label': 'Turn on ' + p.name, onclick: function () { run(p.id, 'on', 'Turn on'); } });
    }
    // "Name the inputs": one row per input with a text field and Show, one Save names. Its own panel, so that
    // naming an input never switches the projector to it.
    function namingPanel(p) {
      var n = projNaming, fields = {};
      function changed() { return p.inputs.filter(function (i) { return (n.texts[i.code] === undefined ? i.label : n.texts[i.code]).trim() !== i.label; }); }
      var err = h('div', { class: 'msg inmsg', id: 'projnameserr', role: 'alert' });
      var save = h('button', { class: 'btn on pri grow', id: 'projnamessave', text: 'Save names', onclick: function () {
        keep();
        var todo = changed();
        save.disabled = true;
        (function next(last) {
          var i = todo.shift();
          if (!i) { projNaming = null; say('Names saved.'); if (last) draw(last, true); return load(true); }
          api('POST', '/api/projectors', { label: { id: p.id, input: i.code, label: n.texts[i.code].trim() } }).then(function (r) {
            if (!r.ok) { save.disabled = false; return sayAt(err, (r.data.error || 'Could not save the name') + '. Use up to 24 letters, digits and spaces.', true); }
            next(r.data);
          });
        })(null);
      } });
      save.disabled = !changed().length;
      return h('div', { class: 'addform proj-names', id: 'projnames', 'data-id': p.id },
        h('div', { class: 'field', text: 'Name the inputs of ' + p.name }),
        h('div', { class: 'hint', text: 'Naming an input does not switch the projector. Give each one the name of what is plugged into it.' }),
        p.inputs.map(function (i) {
          var f = fields[i.code] = h('input', { class: 'text-input proj-nameinput', id: 'projname-' + p.id + '-' + i.code, 'data-code': i.code, maxlength: 24, placeholder: 'Matrix',
            value: n.texts[i.code] === undefined ? i.label : n.texts[i.code] });
          f.addEventListener('input', function () { n.texts[i.code] = f.value; save.disabled = !changed().length; });
          return h('div', { class: 'fieldwrap proj-namerow' }, h('label', { class: 'field', for: f.id, text: plainInput(i) }),
            h('div', { class: 'row' }, f, can('live') ? h('button', { class: 'btn', text: 'Show', 'aria-label': 'Show ' + plainInput(i) + ' on ' + p.name,
              onclick: function () { keep(); run(p.id, 'input', 'Input ' + inputText(p, i.code), { input: i.code }); } }) : null));
        }),
        h('div', { class: 'row' }, save, h('button', { class: 'btn grow', id: 'projnamescancel', text: 'Close', onclick: function () { projNaming = null; draw(JSON.parse(shown), true); } })),
        err);
    }
    function draw(d, fresh) {
      if (!fresh) keep();
      shown = JSON.stringify(d);
      if (projEdit && !d.projectors.some(function (p) { return p.id === projEdit.id; })) projEdit = null;     // removed from another device
      if (projNaming && !d.projectors.some(function (p) { return p.id === projNaming.id; })) projNaming = null;
      function redraw() { draw(JSON.parse(shown)); }
      body.textContent = '';
      var n = d.projectors.length, live = can('live');
      var lit = d.projectors.filter(function (p) { return /^(on|warming)/.test((p.status || {}).power || ''); }).length;
      if (n) body.appendChild(h('div', { class: 'state', id: 'projline', text: plural(n, 'projector') + ', ' + (lit ? lit + ' on.' : 'none on.') + ' The box asks each one how it is every few seconds.' }));
      else body.appendChild(h('div', { class: 'empty', id: 'projline', text: 'No projectors yet. On the projector, open its network menu and switch PJLink on. Then add it here.' }));
      if (n > 1 && live) body.appendChild(h('div', { class: 'row', id: 'projall' },
        h('button', { class: 'btn grow', id: 'projallon', text: 'All on', onclick: function (e) {
          confirmRow('Turn on all ' + n + ' projectors?', 'Turn all on', 'Not now', function () { run('all', 'on', 'All on'); }, e.currentTarget);
        } }),
        h('button', { class: 'btn grow', id: 'projalloff', text: 'All off', onclick: function (e) {
          confirmRow('Turn off all ' + n + ' projectors? They need about a minute to cool before they can come on again.', 'Turn all off', 'Keep them on', function () { run('all', 'off', 'All off'); }, e.currentTarget);
        } })));
      var list = h('div', { class: 'list sp splitlist', id: 'projlist' });
      d.projectors.forEach(function (p) {
        var st = p.status || {}, mute = st.mute || {}, warn = warningText(p), silent = st.ok === false;
        if (full && projEdit && projEdit.id === p.id) return list.appendChild(editForm(p));
        var below = [];
        if (live && !silent) {
          if (p.inputs.length) {
            var sel = h('select', { class: 'text-input proj-input', id: 'projinput-' + p.id, onchange: function () {
              if (sel.value) run(p.id, 'input', 'Input ' + inputText(p, sel.value), { input: sel.value });
            } }, [h('option', { value: '', text: 'Choose...', selected: !st.input && !st.pending_input })].concat(p.inputs.map(function (i) {
              return h('option', { value: i.code, text: inputText(p, i.code), selected: i.code === (st.pending_input || st.input) });
            })));
            below.push(labelled('Input', sel));
          } else {
            below.push(h('div', { class: 'hint proj-noinputs', text: 'Inputs appear once the projector is on. Turn it on, wait for On, then tap Read inputs.' }));
            below.push(h('div', { class: 'row' }, h('button', { class: 'btn grow proj-readinputs', text: 'Read inputs', 'aria-label': 'Read the inputs of ' + p.name, onclick: function () { run(p.id, 'identify', 'Read inputs'); } })));
          }
        }
        if (full && projNaming && projNaming.id === p.id && p.inputs.length) below.push(namingPanel(p));
        function act1(action, label) { return h('button', { class: 'btn', text: label, 'aria-label': label + ' on ' + p.name, onclick: function () { run(p.id, action, label); } }); }
        var more = !live ? [] : [
          silent ? null : mute.picture ? act1('unmute_picture', 'Show the picture') : act1('mute_picture', 'Blank the picture'),
          silent ? null : mute.sound ? act1('unmute_sound', 'Unmute the sound') : act1('mute_sound', 'Mute the sound'),
          full && p.inputs.length ? h('button', { class: 'btn proj-namebtn', text: 'Name the inputs', 'aria-label': 'Name the inputs of ' + p.name, onclick: function () {
            keep(); projNaming = { id: p.id, texts: {} }; moreOpen = null; draw(JSON.parse(shown), true);
          } }) : null,
          h('button', { class: 'btn', text: 'Check now', 'aria-label': 'Check ' + p.name, onclick: function () { run(p.id, 'state', 'Check now'); } }),
          h('button', { class: 'btn', text: 'Read details again', 'aria-label': 'Read the details of ' + p.name + ' again', onclick: function () { run(p.id, 'identify', 'Read details again'); } }),
          full ? h('button', { class: 'btn proj-editbtn', text: 'Edit', 'aria-label': 'Edit ' + p.name, onclick: function () {
            keep();
            projEdit = { id: p.id, name: p.name, host: p.host, port: String(p.port), password: '', clear: false, error: '' };
            moreOpen = null;
            draw(JSON.parse(shown), true);
            var first = document.getElementById('projeditname');
            if (first) first.focus();
          } }) : null,
          full ? h('button', { class: 'btn', text: 'Remove', 'aria-label': 'Remove ' + p.name, onclick: function (e) {
            confirmRow('Remove ' + p.name + '? The box forgets its address, its password and the names of its inputs. The projector itself stays as it is.', 'Remove', 'Keep it', function () {
              act('POST', '/api/projectors', { remove: p.id }, function (data) { moreOpen = null; say(p.name + ' is removed.'); draw(data, true); });
            }, e.currentTarget);
          } }) : null];
        var who = detailsText(p);
        var entry = listRow({ cls: 'proj-entry', data: p.id, name: p.name, key: 'proj-' + p.id, redraw: redraw,
          sub: (who ? who + ' · ' : '') + where(p) + (p.has_password ? ' · password set' : ''), subCls: 'proj-details',
          state: h('div', { class: 'state proj-status' }, statusText(p), warn ? h('span', { class: 'proj-warn', text: ' · ' + warn }) : null),
          problem: silent ? 'Not answering at ' + where(p) + '. Is it plugged in at the wall, and is PJLink switched on in its network menu?' + (st.error ? ' (The box says: ' + st.error + '.)' : '') : '',
          note: [st.pending_input ? 'Switching to ' + inputText(p, st.pending_input) + ' when the projector is ready (up to 90 seconds).' : (st.notice ? st.notice.text : ''), states[p.id]].filter(Boolean).join(' '),
          primary: live ? powerButton(p) : null, more: more, below: below.length ? h('div', { class: 'list sp proj-below' }, below) : null });
        entry.setAttribute('data-power', st.ok === undefined ? 'checking' : st.ok ? String(st.power) : 'no answer');      // for a style that draws the state as a chip
        list.appendChild(entry);
      });
      var side = !full ? null : addBlock('proj', 'a projector', !n, function (cancel) {
        var name = h('input', { class: 'text-input', id: 'projname', placeholder: 'Main wall', maxlength: 40, value: projForm.name });
        var host = h('input', { class: 'text-input mono', id: 'projhost', placeholder: '192.168.1.50', value: projForm.host, autocomplete: 'off' });
        var port = h('input', { class: 'text-input mono', id: 'projport', type: 'number', min: 1, max: 65535, value: projForm.port });
        var pw = h('input', { class: 'text-input mono', id: 'projpw', type: 'password', autocomplete: 'new-password' });
        pw.value = projForm.password;     // the property, not an attribute: a typed password never becomes page HTML
        var err = h('div', { class: 'msg inmsg', id: 'projerr', role: 'alert' });
        name.addEventListener('input', function () { projForm.name = name.value; });
        host.addEventListener('input', function () { projForm.host = host.value; });
        port.addEventListener('input', function () { projForm.port = port.value; });
        pw.addEventListener('input', function () { projForm.password = pw.value; });
        return h('div', { class: 'addform', id: 'projform' }, h('div', { class: 'field', text: 'Add a projector' }),
          labelled('Name', name, 'What people in the room call it.'),
          labelled('Address', host, 'The projector\'s IP address, from its network menu. Only a private address (such as 192.168.x.x) is taken.'),
          labelled('Port', port, '4352 unless it was changed on the projector.'),
          labelled('PJLink password', pw, 'Only if the projector asks for one. It is kept on the box and never shown again.'),
          h('div', { class: 'row' },
            h('button', { class: 'btn on pri grow', id: 'projadd', text: 'Add', onclick: function () {
              keep();
              api('POST', '/api/projectors', { add: { name: projForm.name || projForm.host, host: projForm.host, port: parseInt(projForm.port || '4352', 10), password: projForm.password } }).then(function (r) {
                if (!r.ok) return sayAt(err, r.data.error || 'Could not add the projector. Check the address.', true);
                projForm = { name: '', host: '', port: '4352', password: '' }; addOpen.proj = false; say('Projector added. The box is asking it who it is.'); draw(r.data, true); load();
              });
            } }),
            cancel ? h('button', { class: 'btn grow', id: 'projcancel', text: 'Cancel', onclick: function () { keep(); cancel(); } }) : null),
          err);
      }, function () { keep(); draw(JSON.parse(shown), true); });
      body.appendChild(h('div', { class: 'split' + (n ? '' : ' alone') }, n ? list : null, side ? h('div', { class: 'splitside' }, side) : null));
    }
    load();
    return card;
  }
  // ---- schedule -------------------------------------------------------
  var DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function blankSched() { return { id: '', time: '18:00', days: [0, 1, 2, 3, 4, 5, 6], action: 'play', file: '', loop: true, preset: '', label: '', scene: '', set: '' }; }
  var schedForm = blankSched();  // the entry being added, or (with an id) changed; survives redraws
  // "Every day", "Mon to Fri", "Sat, Sun", or a list
  function daysText(days) {
    var d = (days || []).slice().sort();
    if (d.length === 7) return 'Every day';
    if (d.length >= 3 && d[d.length - 1] - d[0] === d.length - 1) return DAYS[d[0]] + ' to ' + DAYS[d[d.length - 1]];
    return d.map(function (x) { return DAYS[x]; }).join(', ');
  }
  function boxTimeText(d) {
    var m = /^(\d+)-(\d+)-(\d+) (\d+):(\d+)/.exec(d.now || '');
    if (!m) return d.now || '?';
    return DAYS[(new Date(+m[1], +m[2] - 1, +m[3]).getDay() + 6) % 7] + ' ' + (+m[3]) + ' ' + MONTHS[+m[2] - 1] + ', ' + m[4] + ':' + m[5] + ' (' + d.timezone + ')';
  }
  function scheduleCard() {
    var body = h('div', { class: 'list sp', id: 'schedbody' });
    var card = h('div', { class: 'card wide', id: 'schedcard' }, h('h2', { text: 'Schedule' }), body);
    var last = null, clock = null, sets = null;      // the last answer; the box clock's own state; the Vibes sets, when there are several
    function redraw() { if (last) draw(last); }
    // What each choice needs: a feature that may be switched off. The choice is then marked, and choosing it offers
    // the switch in place.
    var NEEDS = { vibes: ['shaders', 'vibes', 'Vibes'], scene: ['room', 'room', 'Room'], projector_on: ['projector', 'projectors', 'Projectors'], projector_off: ['projector', 'projectors', 'Projectors'] };
    function isOff(action) { return !!NEEDS[action] && !moduleOn(NEEDS[action][0]); }
    function save(entries, done, err) {
      api('POST', '/api/schedule', { enabled: last.enabled, entries: entries }).then(function (r) {
        if (!r.ok) return sayAt(err || null, (r.data.error || 'Could not save the schedule') + '. Nothing was changed.', true);
        if (done) done();
        draw(r.data);
      });
    }
    function form(d, cancel) {
      var f = schedForm, editing = !!f.id;
      var err = h('div', { class: 'msg inmsg', id: 'schederr', role: 'alert' });
      var time = h('input', { class: 'text-input mono', id: 'schedtime', type: 'time', value: f.time });
      time.addEventListener('input', function () { f.time = time.value; });
      function setDays(list) { f.days = list.slice(); draw(d); }
      function same(list) { return f.days.slice().sort().join() === list.join(); }
      var shortcuts = h('div', { class: 'row wrap', id: 'schedshort' }, [['Every day', [0, 1, 2, 3, 4, 5, 6]], ['Weekdays', [0, 1, 2, 3, 4]], ['Weekend', [5, 6]]].map(function (sc) {
        return h('button', { class: 'btn small grow' + (same(sc[1]) ? ' on' : ''), text: sc[0], 'aria-pressed': same(sc[1]) ? 'true' : 'false', onclick: function () { setDays(sc[1]); } });
      }));
      var days = h('div', { class: 'row daychips', id: 'scheddays' }, DAYS.map(function (name, i) {
        var on = f.days.indexOf(i) >= 0;
        return h('button', { class: 'btn' + (on ? ' on' : ''), text: name, 'aria-pressed': on ? 'true' : 'false', onclick: function () {
          var at = f.days.indexOf(i);
          if (at >= 0) f.days.splice(at, 1); else f.days.push(i);
          draw(d);
        } });
      }));
      function opt(a) { return h('option', { value: a[0], text: a[1] + (isOff(a[0]) ? ' (' + NEEDS[a[0]][2] + ' is off)' : ''), selected: a[0] === f.action }); }
      var action = h('select', { class: 'text-input', id: 'schedaction' },
        [['play', 'Play a clip'], ['vibes', 'Start Vibes'], ['scene', 'Apply a Room scene'], ['projector_on', 'Projectors on'], ['projector_off', 'Projectors off'],
          ['blackout', 'Screen to black'], ['show', 'Screen back on'], ['stop', 'Stop playing']].filter(function (a) { return a[0] !== 'scene' || !!window.pvjRoom; }).map(opt),
        h('optgroup', { label: 'Advanced' }, opt(['preset', 'Old start script'])));
      var scene = window.pvjRoom ? window.pvjRoom.scheduleField(roomCtx(), action, f, function () { draw(d); }) : null;
      if (!f.file && S.media.length) f.file = S.media[0];
      var file = h('select', { class: 'text-input', id: 'schedfile' },
        S.media.length ? S.media.map(function (n) { return h('option', { value: n, text: n, selected: n === f.file }); }) : [h('option', { value: '', text: 'No clips on the box yet' })]);
      var preset = h('input', { class: 'text-input mono', id: 'schedpreset', placeholder: 'startlessonce05', value: f.preset, autocomplete: 'off' });
      var set = h('select', { class: 'text-input', id: 'schedset' }, [h('option', { value: '', text: 'The set that is in use then', selected: !f.set })].concat((sets || []).map(function (e) {
        return h('option', { value: e.id, text: e.name, selected: e.id === f.set });
      })));
      var label = h('input', { class: 'text-input', id: 'schedlabel', placeholder: 'Opening time', maxlength: 40, value: f.label });
      var wrap = { file: labelled('Clip', file), set: labelled('Which shaders', set), scene: scene ? labelled('Scene', scene) : null,
        preset: labelled('Start script name', preset, 'The name of an old PocketVJ start script, such as startlessonce05.') };
      var off = h('div', { id: 'schedoffwrap' });
      function fit() {
        var a = action.value;
        wrap.file.hidden = a !== 'play';
        wrap.preset.hidden = a !== 'preset';
        wrap.set.hidden = a !== 'vibes' || !sets || sets.length < 2 || isOff('vibes');
        if (wrap.scene) { wrap.scene.hidden = a !== 'scene' || isOff('scene'); scene.hidden = false; }
        off.textContent = '';
        if (isOff(a)) off.appendChild(offNotice('schedoff', NEEDS[a][1], NEEDS[a][2], function () { loadSets(); draw(d); }));
      }
      preset.addEventListener('input', function () { f.preset = preset.value; });
      action.addEventListener('change', function () { f.action = action.value; fit(); });
      file.addEventListener('change', function () { f.file = file.value; });
      set.addEventListener('change', function () { f.set = set.value; });
      label.addEventListener('input', function () { f.label = label.value; });
      fit();
      function done() { schedForm = blankSched(); addOpen.sched = false; moreOpen = null; }
      return h('div', { class: 'addform', id: 'schedform' }, h('div', { class: 'field', id: 'schedformtitle', text: editing ? 'Change the entry' : 'Add an entry' }),
        labelled('Time', time, 'By the box\'s own clock, shown above.'),
        h('div', { class: 'fieldwrap' }, h('div', { class: 'field', text: 'Days' }), shortcuts, days),
        labelled('What happens', action), off, wrap.file, wrap.set, wrap.scene, wrap.preset,
        labelled('Note (optional)', label, 'A word for yourself, shown in the list.'),
        h('div', { class: 'row' },
          h('button', { class: 'btn on pri grow', id: 'schedadd', text: editing ? 'Save changes' : 'Add', onclick: function () {
            if (!f.days.length) return sayAt(err, 'Choose at least one day.', true);
            if (!/^\d\d:\d\d/.test(f.time)) return sayAt(err, 'Choose a time.', true);
            var entry = { time: f.time.slice(0, 5), days: f.days.slice(), action: f.action, label: f.label };
            if (editing) entry.id = f.id;
            if (f.action === 'play') { if (!f.file) return sayAt(err, 'There are no clips on the box yet. Upload one on the Media screen, or choose something else to happen.', true); entry.file = f.file; entry.loop = f.loop !== false; }
            if (f.action === 'preset') { if (!f.preset) return sayAt(err, 'Type the start script name.', true); entry.preset = f.preset.trim(); }
            if (f.action === 'scene') { if (!f.scene) return sayAt(err, isOff('scene') ? 'Switch Room on first, with the button above.' : 'There is no scene yet. Make one on the Room screen, or choose something else to happen.', true); entry.scene = f.scene; }
            if (f.action === 'vibes' && f.set) entry.set = f.set;
            save(editing ? d.entries.map(function (x) { return x.id === f.id ? entry : x; }) : d.entries.concat(entry), function () { done(); say(editing ? 'Entry changed.' : 'Entry added.'); }, err);
          } }),
          cancel || editing ? h('button', { class: 'btn grow', id: 'schedcancel', text: 'Cancel', onclick: function () { done(); draw(d); } }) : null),
        err,
        h('div', { class: 'hint', text: 'An entry runs only if the box is on at that minute. A missed entry is not caught up.' }));
    }
    function draw(d) {
      last = d;
      body.textContent = '';
      body.appendChild(h('div', { class: 'state', id: 'schedclock', text: 'Box time now: ' + boxTimeText(d) }));
      var next = schedNextEntry(d);
      if (next) body.appendChild(h('div', { class: 'state', id: 'schednext', text: 'Next: ' + (next.off === 0 ? 'today' : next.off === 1 ? 'tomorrow' : next.off === 7 ? 'next ' + next.day : next.day) + ' ' + next.e.time + ', ' + schedWhat(next.e) }));
      if (clock && clock.clock_from_network === false) body.appendChild(h('div', { class: 'hint warn', id: 'schedclockwarn' },
        h('div', { text: 'The box clock was not set from the network, so it may be wrong. Compare the time above with your own before you trust the schedule.' }),
        clock.can_set ? h('div', { class: 'row' }, h('button', { class: 'btn', id: 'schedsetclock', text: 'Set the box clock to this phone\'s time', onclick: function () {
          act('POST', '/api/system/clock', { epoch: Math.round(Date.now() / 1000) }, function () { say('Box clock set.'); loadClock(); refresh(); });
        } })) : null));
      var list = h('div', { class: 'list sp splitlist', id: 'schedlist' });
      if (!d.entries.length) list.appendChild(h('div', { class: 'empty', id: 'schedempty', text: 'No entries yet. An entry makes something happen by itself at a set time on the days you choose: Vibes at opening time, the projectors off at night. Add the first one here.' }));
      d.entries.slice().sort(function (x, y) { return x.time < y.time ? -1 : x.time > y.time ? 1 : Math.min.apply(null, x.days) - Math.min.apply(null, y.days); }).forEach(function (e) {
        var ran = d.last[e.id], what = schedWhat(e) + (e.action === 'vibes' && e.set && sets ? ' (' + ((sets.filter(function (x) { return x.id === e.set; })[0] || {}).name || 'a set that was removed') + ')' : '');
        var needs = isOff(e.action) ? NEEDS[e.action][2] + ' is switched off, so this entry will do nothing.' : '';
        list.appendChild(listRow({ cls: 'sched-entry' + (schedForm.id === e.id ? ' editing' : ''), data: e.id, name: e.time + ' \u00b7 ' + what, key: 'sched-' + e.id, redraw: redraw,
          state: daysText(e.days) + (e.label ? ' \u00b7 ' + e.label : ''),
          problem: ran && !ran.ok ? 'The last run failed (' + ran.at + '): ' + ran.message : needs,
          note: ran && ran.ok ? 'Last ran ' + ran.at : '',
          more: [h('button', { class: 'btn', text: 'Edit', 'aria-label': 'Edit ' + e.time + ' ' + what, onclick: function () {
            schedForm = { id: e.id, time: e.time, days: e.days.slice(), action: e.action, file: e.file || '', loop: e.loop !== false, preset: e.preset || '', label: e.label || '', scene: e.scene || '', set: e.set || '' };
            addOpen.sched = true; moreOpen = null; draw(d);
            var first = document.getElementById('schedtime');
            if (first) first.focus();
          } }),
          h('button', { class: 'btn', text: 'Remove', 'aria-label': 'Remove ' + e.time + ' ' + what, onclick: function (ev) {
            confirmRow('Remove ' + e.time + ' ' + what + ' (' + daysText(e.days) + ')? It will no longer happen.', 'Remove', 'Keep it', function () {
              save(d.entries.filter(function (x) { return x.id !== e.id; }), function () { moreOpen = null; if (schedForm.id === e.id) schedForm = blankSched(); say('Entry removed.'); });
            }, ev.currentTarget);
          } })] }));
      });
      var side = addBlock('sched', 'an entry', !d.entries.length || !!schedForm.id, function (cancel) { return form(d, cancel); }, redraw);
      body.appendChild(h('div', { class: 'split' }, list, h('div', { class: 'splitside' }, side)));
    }
    function loadClock() {
      api('GET', '/api/system').then(function (r) {
        if (!r.ok || !body.isConnected) return;
        clock = { clock_from_network: (r.data.clock || {}).clock_from_network, can_set: !!r.data.system_actions };
        if (clock.clock_from_network === false && !asking(body)) redraw();
      });
    }
    function loadSets() {
      if (!moduleOn('shaders')) return;
      api('GET', '/api/shaders').then(function (r) {
        if (!r.ok || !body.isConnected || !r.data.sets) return;
        var had = JSON.stringify(sets);
        sets = r.data.sets.map(function (e) { return { id: e.id, name: e.name }; });
        var a = document.activeElement;
        if (JSON.stringify(sets) !== had && !asking(body) && !(a && body.contains(a) && /^(INPUT|SELECT)$/.test(a.tagName))) redraw();
      });
    }
    function refresh() {
      api('GET', '/api/schedule').then(function (r) {
        if (!document.getElementById('schedcard')) return;
        if (!r.ok) { body.textContent = ''; body.appendChild(h('div', { class: 'hint', id: 'schedmsg', text: (r.data.error || 'Not available') + '. Open the page again in a moment.' })); return; }
        draw(r.data);
      });
    }
    refresh(); loadClock(); loadSets();
    return card;
  }
  // ---- network (wired) ------------------------------------------------
  var netTimer = null;
  var netForm = { mode: 'dhcp', iface: null, vals: {}, adv: false };  // survives redraws of the System screen, so typing is never wiped
  var NET_FIELDS = ['netaddr', 'netprefix', 'netgw', 'netdns', 'netssid', 'netpass'];
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
  var WIFI_MODES = [
    ['dhcp', 'Join a network', 'Join a Wi-Fi network and take an address from its router.'],
    ['static', 'Join, fixed address', 'Join a Wi-Fi network with an address you choose.'],
    ['hotspot', 'Own hotspot', 'The box makes its own Wi-Fi network (WPA2) and hands out addresses to phones and tablets that join it.'],
    ['off', 'Wi-Fi off', 'Switch Wi-Fi off. Saved networks are kept.']
  ];
  var WIFI_SECURITY = [['wpa-psk', 'WPA2 or WPA2/WPA3 password'], ['sae', 'WPA3 only'], ['open', 'Open (no password)']];
  function wifiLine(i, w) {
    if (!w) return 'wi-fi';
    if (w.hardware === false) return 'wi-fi · blocked (a switch, or no Wi-Fi country set)';
    if (w.radio === false) return 'wi-fi · off';
    var now = w.ports && w.ports[i.name];
    if (!now) return 'wi-fi · not connected';
    return 'wi-fi · ' + (now.hotspot ? 'own hotspot “' : 'joined “') + now.ssid + '”';
  }
  function networkCard() {
    var body = h('div', { class: 'list sp', id: 'netbody' });
    var card = h('div', { class: 'card wide', id: 'netcard' }, h('h2', { text: 'Network' }), body);
    var mode = netForm.mode;
    var out = { iface: null, address: null, prefix: null, gateway: null, dns: null, ssid: null, pass: null, security: null,
      hidden: null, band: null, secs: null, preview: null, msg: null };
    function refresh() {
      clearTimeout(netTimer);
      api('GET', '/api/network').then(function (r) {
        if (!document.getElementById('netcard')) return;
        if (!r.ok) { body.textContent = ''; body.appendChild(h('div', { class: 'hint', id: 'netmsg', text: (r.data.error || 'Not available') + '. Open the page again in a moment.' })); return; }
        draw(r.data);
      });
    }
    function value(el) { return el ? el.value.trim() : ''; }
    function config(kind) {
      var c = { iface: value(out.iface), mode: mode, revert_seconds: parseInt(value(out.secs) || '60', 10) };
      if (mode === 'static' || mode === 'share' || mode === 'hotspot') {
        if (value(out.address)) c.address = value(out.address);
        if (value(out.prefix)) c.prefix = parseInt(value(out.prefix), 10);
      }
      if (mode === 'static') {
        if (value(out.gateway)) c.gateway = value(out.gateway);
        c.dns = value(out.dns).split(/[ ,]+/).filter(Boolean);
      }
      if (kind === 'wifi' && mode !== 'off') {
        c.ssid = out.ssid ? out.ssid.value : '';          // a network name may start or end with a space
        c.security = mode === 'hotspot' ? 'wpa-psk' : out.security.value;
        if (c.security !== 'open') c.password = out.pass ? out.pass.value : '';
        if (mode === 'hotspot') c.band = out.band.value; else c.hidden = !!(out.hidden && out.hidden.isOn());
      }
      return c;
    }
    function draw(d) {
      if (asking(body)) { netTimer = setTimeout(refresh, 2000); return; }
      keepNetForm();
      body.textContent = '';
      var ports = h('div', { class: 'list sp', id: 'netports' }, h('div', { class: 'field', text: 'Now' }));
      d.interfaces.forEach(function (i) {
        ports.appendChild(h('div', { class: 'item' },
          h('span', {}, i.kind === 'wired' ? 'Cable (' + i.name + ')' : 'Wi-Fi (' + i.name + ')', h('br'), h('span', { class: 'state', id: 'netline-' + i.name, text: i.kind === 'wired'
            ? 'wired \u00b7 ' + (i.carrier ? 'connected' : 'nothing plugged in') + (i.speed_mbps ? ' \u00b7 ' + i.speed_mbps + ' Mbit/s' : '')
            : wifiLine(i, d.wifi) })),
          h('span', { class: 'mono', text: (i.addresses || []).join(', ') || 'no address' })));
      });
      var form = h('div', { class: 'list sp', id: 'netform' });
      var cols = h('div', { class: 'split even' }, ports, form);
      body.appendChild(cols);
      body = form;                 // the rest of this function fills the form column
      try { fill(d); } finally { body = cols.parentNode; }
    }
    function fill(d) {
      if (!d.helper) body.appendChild(h('div', { class: 'hint warn', id: 'netmsg', text: 'The part of the box that changes the network is not running, so a change cannot be tried. Restart the box; if this stays, tell whoever looks after it.' }));
      if (d.reverting) { body.appendChild(h('div', { class: 'msg err', id: 'netreverting', role: 'alert', text: 'Restoring the previous network. If this page stops responding, reconnect to the box at its old address.' })); netTimer = setTimeout(refresh, 2000); }
      if (d.pending) return drawPending(d.pending);
      if (!d.interfaces.length) return body.appendChild(h('div', { class: 'hint', text: 'No network port found on this box.' }));
      var names = d.interfaces.map(function (i) { return i.name; });
      if (names.indexOf(netForm.iface) < 0) netForm.iface = names[0];
      function kindOf(name) { return d.interfaces.filter(function (i) { return i.name === name; })[0].kind; }
      out.iface = h('select', { class: 'text-input', id: 'netiface' }, d.interfaces.map(function (i) {
        return h('option', { value: i.name, text: (i.kind === 'wifi' ? 'Wi-Fi (' : 'Cable (') + i.name + ')', selected: i.name === netForm.iface });
      }));
      var modes = h('div', { class: 'row wrap', id: 'netmodes' });
      var help = h('div', { class: 'hint', id: 'nethelp' });
      var fields = h('div', { class: 'list sp', id: 'netfields' });
      function kind() { return kindOf(out.iface.value); }
      function modeList() { return kind() === 'wifi' ? WIFI_MODES : NET_MODES; }
      function remember(el) {
        if (netForm.vals[el.id]) el.value = netForm.vals[el.id];
        el.addEventListener('input', function () { netForm.vals[el.id] = el.value; });
      }
      function input(id, label, placeholder, extra, hint) {
        var el = h('input', Object.assign({ class: 'text-input mono', id: id, placeholder: placeholder }, extra || {}));
        remember(el);
        fields.appendChild(labelled(label, el, hint));
        return el;
      }
      function scanList(list) {
        list.textContent = '';
        list.appendChild(h('div', { class: 'hint', text: 'Looking for networks...' }));
        api('POST', '/api/network/scan', { iface: out.iface.value }).then(function (r) {
          list.textContent = '';
          if (!r.ok) return list.appendChild(h('div', { class: 'msg inmsg err', text: (r.data.error || 'Could not look for networks') + '. Try again in a moment.' }));
          if (!r.data.networks.length) return list.appendChild(h('div', { class: 'hint', text: 'No networks found. Move the box closer to the Wi-Fi, or type the name below.' }));
          r.data.networks.forEach(function (n) {
            var usable = n.security !== 'unsupported';
            list.appendChild(h('button', { class: 'btn' + (n.in_use ? ' on' : ''), disabled: !usable,
              text: n.ssid + ' · ' + n.signal + '%' + (n.security === 'open' ? ' · open' : '') + (usable ? '' : ' · not supported'),
              onclick: function () {
                out.ssid.value = netForm.vals.netssid = n.ssid;
                out.security.value = n.security;
                drawPass();
                if (out.pass) out.pass.focus();
              } }));
          });
        });
      }
      function drawPass() {
        var wrap = document.getElementById('netpasswrap');
        if (!wrap) return;
        wrap.textContent = '';
        out.pass = null;
        if (mode !== 'hotspot' && out.security && out.security.value === 'open') return;
        out.pass = h('input', { class: 'text-input mono', id: 'netpass', type: 'password', autocomplete: 'off' });
        remember(out.pass);
        wrap.appendChild(labelled(mode === 'hotspot' ? 'Password for the hotspot' : 'Wi-Fi password', out.pass, mode === 'hotspot' ? '8 to 63 characters. People type it to join the box\'s Wi-Fi.' : ''));
      }
      function drawFields() {
        fields.textContent = '';
        out.address = out.prefix = out.gateway = out.dns = out.ssid = out.pass = out.security = out.hidden = out.band = null;
        modeList().forEach(function (m) { if (m[0] === mode) help.textContent = m[2]; });
        if (kind() === 'wifi' && mode !== 'off') {
          if (mode !== 'hotspot') {
            var list = h('div', { class: 'row wrap', id: 'netscan' });
            fields.appendChild(h('div', { class: 'row' }, h('button', { class: 'btn grow', id: 'netscanbtn', text: 'Find networks', onclick: function () { scanList(list); } })));
            fields.appendChild(list);
          }
          out.ssid = input('netssid', mode === 'hotspot' ? 'Name of the box\'s Wi-Fi' : 'Network name', mode === 'hotspot' ? 'NXLX box' : 'Studio Wi-Fi', { autocomplete: 'off' });
          if (mode !== 'hotspot') {
            out.security = h('select', { class: 'text-input', id: 'netsec', onchange: drawPass },
              WIFI_SECURITY.map(function (x) { return h('option', { value: x[0], text: x[1] }); }));
            fields.appendChild(labelled('Security', out.security));
          }
          fields.appendChild(h('div', { id: 'netpasswrap' }));
          if (mode === 'hotspot') {
            out.band = h('select', { class: 'text-input', id: 'netband' },
              h('option', { value: 'bg', text: '2.4 GHz (reaches further, every device)' }), h('option', { value: 'a', text: '5 GHz (faster, less crowded)' }));
            fields.appendChild(labelled('Band', out.band));
          } else {
            out.hidden = toggle('nethidden', 'Hidden network', false, null, 'Switch on for a network that does not show in the list.');
            fields.appendChild(out.hidden);
          }
        }
        if (mode === 'static' || mode === 'share' || mode === 'hotspot') {
          out.address = input('netaddr', 'Address', mode === 'hotspot' ? '10.43.0.1' : mode === 'share' ? '10.42.0.1' : '192.168.1.50', { inputmode: 'decimal' },
            mode === 'static' ? 'The box\'s own address. Choose one in the range your other gear uses that nothing else has.' : 'The box\'s own address. Leave empty for the usual one.');
          out.prefix = input('netprefix', 'Size of the network', '24', { inputmode: 'numeric' }, '24 is the usual size (the same as 255.255.255.0). Leave empty for 24.');
        }
        if (mode === 'static') {
          out.gateway = input('netgw', 'Router (optional)', '192.168.1.1', { inputmode: 'decimal' }, 'The address of the router, for reaching the internet. Leave empty on a network with no router.');
          out.dns = input('netdns', 'Name servers (optional)', '192.168.1.1, 9.9.9.9', null, 'Usually the router\'s address. Leave empty if the box needs no internet names.');
        }
        drawPass();
      }
      function drawModes() {
        modes.textContent = '';
        modeList().forEach(function (m) {
          modes.appendChild(h('button', { class: 'btn' + (m[0] === mode ? ' on' : ''), text: m[1], 'aria-pressed': m[0] === mode ? 'true' : 'false',
            onclick: function () { mode = netForm.mode = m[0]; drawModes(); drawFields(); } }));
        });
      }
      function drawSecs() {
        var wifi = kind() === 'wifi';
        out.secs.textContent = '';
        [30, 60, 120, 300].forEach(function (n) {
          out.secs.appendChild(h('option', { value: n, text: n < 120 ? n + ' seconds' : (n / 60) + ' minutes', selected: n === (wifi ? 120 : 60) }));
        });
      }
      function fitMode() {
        var ok = modeList().some(function (m) { return m[0] === mode; });
        if (!ok) mode = netForm.mode = 'dhcp';
      }
      out.secs = h('select', { class: 'text-input', id: 'netsecs' });
      out.iface.addEventListener('change', function () { netForm.iface = out.iface.value; fitMode(); drawModes(); drawFields(); drawSecs(); });
      out.preview = h('pre', { class: 'mono', id: 'netplan', hidden: true });
      out.msg = h('div', { class: 'msg', id: 'netresult', role: 'status' });
      fitMode();
      var ifaceWrap = labelled('Which connection to change', out.iface);
      ifaceWrap.hidden = d.interfaces.length < 2;         // one port: nothing to choose
      body.appendChild(ifaceWrap);
      body.appendChild(h('div', { class: 'field', text: 'How it gets its address' }));
      body.appendChild(modes); body.appendChild(help); body.appendChild(fields);
      drawModes(); drawFields(); drawSecs();
      var wifiOffAsked = false;
      var applyBtn = h('button', { class: 'btn on pri grow', id: 'netapply', text: 'Try this setting', onclick: function () {
          var c = config(kind());
          if (c.mode === 'off' && !wifiOffAsked) {
            return confirmRow('Switch Wi-Fi off? A phone that reaches the box over Wi-Fi loses it. The change goes back by itself unless it is confirmed from a wired connection.',
              'Switch Wi-Fi off', 'Keep Wi-Fi on', function () { wifiOffAsked = true; applyBtn.click(); }, applyBtn);
          }
          wifiOffAsked = false;
          out.msg.className = 'msg'; out.msg.textContent = 'Trying it...';
          api('POST', '/api/network/apply', c).then(function (r) {
            if (!r.ok) { out.msg.className = 'msg err'; out.msg.textContent = (r.data.error || 'Could not try it') + '. Nothing was changed.'; return; }
            var where;
            if (c.mode === 'hotspot') where = ' Join the Wi-Fi “' + c.ssid + '” with this phone or tablet, then open http://' + (c.address || '10.43.0.1') + ' and press Confirm before the timer runs out.';
            else if (c.mode === 'static' || c.mode === 'share') where = ' If this page stops responding, open http://' + (c.address || '10.42.0.1') + ' and press Confirm before the timer runs out.';
            else if (c.ssid) where = ' If this page stops responding, join “' + c.ssid + '” yourself, find the box there (for example at http://' + location.hostname + ') and press Confirm before the timer runs out.';
            else where = ' If this page stops responding, find the box at its new address and press Confirm before the timer runs out.';
            S.netNote = 'Applied.' + where;
            clearNetForm();
            refresh();
          });
        } });
      body.appendChild(h('div', { class: 'row' }, applyBtn));
      body.appendChild(out.msg);
      body.appendChild(h('div', { class: 'hint', text: 'A change can cut this connection. The box tries it and goes back by itself unless you confirm it, and also if it restarts before you do.' }));
      var adv = h('details', { class: 'fold', id: 'netadv', open: netForm.adv }, h('summary', { text: 'Advanced' }), h('div', { class: 'list sp' },
        labelled('Go back by itself after', out.secs, 'How long you have to confirm before the box goes back to the network it had.'),
        h('div', { class: 'row' },
        h('button', { class: 'btn grow', id: 'netpreview', text: 'Show the commands', onclick: function () {
          api('POST', '/api/network/plan', config(kind())).then(function (r) {
            out.preview.hidden = !r.ok; out.msg.className = 'msg' + (r.ok ? '' : ' err');
            out.msg.textContent = r.ok ? '' : (r.data.error || 'Invalid');
            if (r.ok) out.preview.textContent = r.data.commands.join('\n');
          });
        } })),
        h('div', { class: 'hint', text: 'The commands the box would run for this setting. Nothing is changed by looking.' }),
        out.preview));
      adv.addEventListener('toggle', function () { netForm.adv = adv.open; });
      body.appendChild(adv);
    }
    function drawPending(p) {
      var what = p.mode === 'hotspot' ? 'own hotspot “' + p.ssid + '”' : p.ssid ? 'joining “' + p.ssid + '”' + (p.mode === 'static' ? ' (fixed address)' : '')
        : p.mode === 'off' ? 'Wi-Fi off' : p.mode;
      body.appendChild(h('div', { class: 'card', id: 'netpending', role: 'alert' },
        h('div', { class: 'field', text: 'Waiting for your confirmation: ' + p.iface + ' \u2192 ' + what }),
        h('div', { class: 'state', id: 'netleft', text: 'Reverts in ' + p.seconds_left + ' s' }),
        h('div', { class: 'hint', text: S.netNote || '' }),
        h('div', { class: 'row' },
          h('button', { class: 'btn on pri grow', id: 'netconfirm', text: 'Confirm: keep this network', onclick: function () {
            api('POST', '/api/network/confirm', {}).then(function (r) { S.netNote = r.ok ? '' : (r.data.error || ''); refresh(); });
          } }),
          h('button', { class: 'btn grow', id: 'netrevert', text: 'Go back now', onclick: function () {
            api('POST', '/api/network/revert', {}).then(function () { S.netNote = ''; refresh(); });
          } }))));
      netTimer = setTimeout(refresh, 1000);
    }
    refresh();
    return card;
  }
  var oscForm = { port: null, allow: null }, oscAdv = false, oscTimer = null;
  function oscCard() {
    clearTimeout(oscTimer);
    clearTimeout(oscKeyTimer);
    var body = h('div', { class: 'list sp', id: 'oscbody' }, h('div', { class: 'state', id: 'oscline', text: 'Loading...' }));
    var card = h('div', { class: 'card', id: 'osccard' }, h('h2', { text: 'OSC' }), body);
    function count(n) { return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ','); }
    function lineText(d) {
      if (d.error) return 'Problem: ' + d.error + '. Choose another port, or stop the other program that uses it, then save.';
      if (!d.listening) return 'Not listening yet.';
      return 'Listening on UDP port ' + d.port + '. ' + (d.received ? count(d.received) + (d.received === 1 ? ' message' : ' messages') + ' received.' : 'Nothing received yet.');
    }
    function watch() {
      clearTimeout(oscTimer);
      oscTimer = setTimeout(function () {
        if (!body.isConnected) return;
        api('GET', '/api/osc').then(function (r) {
          var line = document.getElementById('oscline');
          if (r.ok && line && body.isConnected) { line.textContent = lineText(r.data); line.className = r.data.error ? 'hint warn' : 'state'; }
          if (r.ok && body.isConnected) oscFresh(r.data, draw);
          if (body.isConnected) watch();
        });
      }, 3000);
    }
    function draw(d, saved) {
      body.textContent = '';
      body.appendChild(h('div', { class: d.error ? 'hint warn' : 'state', id: 'oscline', text: lineText(d) }));
      var port = h('input', { class: 'text-input mono', id: 'oscport', type: 'number', min: 1024, max: 65535, value: oscForm.port === null ? d.port : oscForm.port });
      var allow = h('input', { class: 'text-input mono', id: 'oscallow', placeholder: '192.168.50.0/24', value: oscForm.allow === null ? d.allow.join(', ') : oscForm.allow });
      function nets() { return allow.value.split(',').map(function (x) { return x.trim(); }).filter(Boolean); }
      function changed() { return String(port.value) !== String(d.port) || nets().join() !== d.allow.join(); }
      var bar = saveBar('oscsave', 'Save changes', function () {
        api('POST', '/api/osc', { port: parseInt(port.value, 10), allow: nets() }).then(function (r) {
          if (!r.ok) return bar.say((r.data.error || 'Could not save') + '. Nothing was changed.', true);
          oscForm = { port: null, allow: null };
          draw(r.data, true); say('Saved');
        });
      });
      function touch() { oscForm = { port: port.value, allow: allow.value }; bar.dirty(changed()); }
      [port, allow].forEach(function (el) { el.addEventListener('input', touch); });
      var adv = h('details', { class: 'fold', id: 'oscadv', open: oscAdv }, h('summary', { text: 'Advanced' }), h('div', { class: 'list sp' },
        labelled('Also accept from these networks', allow, 'Only private networks may send. Add your show network here if the sender is on another one. Separate several with commas.')));
      adv.addEventListener('toggle', function () { oscAdv = adv.open; });
      body.appendChild(labelled('UDP port', port, 'The port the other program sends to, with this box\'s address. 9876 unless it was changed here.'));
      body.appendChild(adv);
      body.appendChild(bar.el);
      bar.dirty(changed());
      if (saved) bar.result.textContent = 'Saved';
      body.appendChild(h('div', { class: 'hint', text: 'Shutdown and restart are never available over OSC.' }));
      if (d.senders) body.appendChild(oscWho(d, draw));
      watch();
    }
    api('GET', '/api/osc').then(function (r) {
      if (!body.isConnected) return;
      if (r.ok) draw(r.data); else { body.textContent = ''; body.appendChild(h('div', { class: 'hint', id: 'oscline', text: (r.data.error || 'Not available') + '. Open the page again in a moment.' })); }
    });
    return card;
  }
  // Who may send OSC (D78): three locks, each off until the owner switches it on, the senders seen lately and the
  // last messages. All of it belongs to the OSC page (oscCard draws it, and keeps the lists fresh while it is open).
  // Only a full-access device is sent the lists; the key is asked for when Show is pressed, and hides itself.
  var oscKeyTimer = null, oscLogOpen = false, OSC_KEY_SECONDS = 30;
  function oscWho(d, redraw) {
    function set(body) {
      api('POST', '/api/osc', body).then(function (r) {
        if (r.ok) { say('Saved'); return redraw(r.data); }
        say((r.data.error || 'Could not save') + '. Nothing was changed.', true);
        api('GET', '/api/osc').then(function (g) { if (g.ok) redraw(g.data); });
      });
    }
    var box = h('div', { class: 'list sp oscwho', id: 'oscwho' },
      h('div', { class: 'switchlabel', text: 'Who may send' }),
      h('div', { class: 'hint', id: 'oscwhohint', text: 'Three locks, each off until you switch it on; a message must pass every one that is on. None of them is encryption: OSC travels in the clear, and a device on this network can pretend to be another one. They keep out devices that are merely on the network.' }));
    // 1. the list of devices
    box.appendChild(toggle('osconly', 'Only these devices may send', d.only_on, function (v) { set({ only_on: v }); },
      'Only the addresses in this list, in place of every device on a private network.'));
    if (d.only_on && !d.only.length) box.appendChild(h('div', { class: 'hint warn', id: 'osconlyempty', text: 'Nobody may send yet. Send one message from your tablet, then press "Allow this one" beside it under Senders.' }));
    if (d.only.length) box.appendChild(h('div', { class: 'list', id: 'osconlylist' }, d.only.map(function (a) {
      return h('div', { class: 'item' }, h('span', { class: 'addr', text: a }),
        h('button', { class: 'btn small', text: 'Remove', 'aria-label': 'Remove ' + a, onclick: function () { set({ only: d.only.filter(function (x) { return x !== a; }) }); } }));
    })));
    // 2. a paired device
    box.appendChild(toggle('oscpaired', 'A sender must be a paired device', d.paired_on, function (v) { set({ paired_on: v }); },
      'Open the panel on the tablet once; then TouchOSC on the same tablet may send, for the hours set here. Removing the device under Access stops it at once.'));
    if (d.paired_on) {
      var roles = h('select', { class: 'text-input', id: 'oscpairedroles', onchange: function () { set({ paired_roles: roles.value }); } },
        [['full', 'Owner devices only'], ['live', 'Owner and presenter devices']].map(function (o) { return h('option', { value: o[0], text: o[1], selected: d.paired_roles === o[0] }); }));
      var hours = h('select', { class: 'text-input', id: 'oscpairedhours', onchange: function () { set({ paired_hours: parseInt(hours.value, 10) }); } },
        [1, 3, 6, 12, 24, 48, 72].concat([1, 3, 6, 12, 24, 48, 72].indexOf(d.paired_hours) < 0 ? [d.paired_hours] : []).map(function (n) { return h('option', { value: String(n), text: plural(n, 'hour'), selected: d.paired_hours === n }); }));
      box.appendChild(labelled('Which paired devices count', roles));
      box.appendChild(labelled('For how long after the panel was last used', hours,
        'Every device behind one address counts together (a phone hotspot, a router that shares one address), and an address that passes to another device still counts until the hours are over.'));
      box.appendChild(h('div', { class: 'state', id: 'oscpairednow', text: oscPairedText(d) }));
    }
    // 3. the key
    box.appendChild(toggle('osckey', 'A key in the address', d.key_on, function (v) { set({ key_on: v }); },
      'Every message must start with /k/ and the key, for example /k/<key>/pvj/stop. Anyone who can listen on this network can read the key from a single message, so it stops people who are merely on the network from poking the box, not someone who is capturing traffic.'));
    if (d.key_on) box.appendChild(oscKeyBox());
    // who sent, and what
    box.appendChild(h('div', { class: 'switchlabel', text: 'Senders in the last ten minutes' }));
    box.appendChild(h('div', { class: 'list', id: 'oscsenders' }));
    var log = h('details', { class: 'fold', id: 'osclog', open: oscLogOpen }, h('summary', { text: 'Last messages' }),
      h('div', { class: 'hint', text: 'The last 50, kept only until the panel restarts. Of a refused message only the sender and the reason are kept.' }),
      h('div', { class: 'list', id: 'oscmessages' }));
    log.addEventListener('toggle', function () { oscLogOpen = log.open; if (log.open) oscMessages(log); });
    box.appendChild(log);
    oscSenders(box.querySelector('#oscsenders'), d, set);
    if (oscLogOpen) oscMessages(log);
    box.set = set;
    return box;
  }
  function oscPairedText(d) {
    return d.paired_now.length ? 'Counts now: ' + d.paired_now.join(', ') : 'No paired device counts now. Open the panel on the tablet that sends.';
  }
  function oscSenders(el, d, set) {
    el.textContent = '';
    if (!d.senders.length) el.appendChild(h('div', { class: 'hint', id: 'oscnosenders', text: 'Nobody has sent anything in the last ten minutes. Press a button in your layout and its address appears here.' }));
    d.senders.forEach(function (x) {
      var listed = d.only.indexOf(x.address) >= 0;
      var what = x.accepted ? plural(x.messages, 'message') + ', the last one ' + x.last + (x.why ? ' (' + x.why + ')' : '') + '.'
        : 'Refused: ' + x.why + ' (' + plural(x.refused, 'time') + ').' + (x.messages ? ' Before that ' + plural(x.messages, 'message') + ' let in.' : '');
      el.appendChild(h('div', { class: 'item lrow oscsender', 'data-id': x.address },
        h('div', { class: 'lhead' }, h('div', { class: 'lname mono', text: x.address }),
          h('div', { class: x.accepted ? 'state' : 'hint warn', text: what })),
        h('div', { class: 'row lacts' }, listed ? h('span', { class: 'hint', text: 'In the list' })
          : h('button', { class: 'btn small', text: 'Allow this one', 'aria-label': 'Allow ' + x.address, disabled: d.only.length >= 16,
            onclick: function () { set({ only: d.only.concat([x.address]) }); } }))));
    });
    if (d.refused) el.appendChild(h('div', { class: 'hint', id: 'oscrefused', text: plural(d.refused, 'packet') + ' refused since the panel started.' }));
  }
  function oscMessages(log) {
    api('GET', '/api/osc/messages').then(function (r) {
      var el = log.querySelector('#oscmessages');
      if (!r.ok || !el || !log.isConnected) return;
      el.textContent = '';
      if (!r.data.messages.length) el.appendChild(h('div', { class: 'hint', text: 'Nothing yet.' }));
      r.data.messages.forEach(function (m) {
        el.appendChild(h('div', { class: 'item oscmsg' },
          h('span', { class: 'addr', text: new Date(m.at * 1000).toLocaleTimeString() + '  ' + m.from }),
          h('span', { class: 'addr', text: m.address || '(not kept)' }),
          h('span', { class: m.ok ? 'state' : 'hint', text: (m.ok ? 'Done' : m.address ? 'Let in: ' + m.why : 'Refused: ' + m.why) + (m.count > 1 ? ', ' + m.count + ' times' : '') })));
      });
    });
  }
  // Called every few seconds while the page is open: the lists change by themselves, the switches do not.
  function oscFresh(d, redraw) {
    var who = document.getElementById('oscwho'), el = document.getElementById('oscsenders'), now = document.getElementById('oscpairednow'), log = document.getElementById('osclog');
    if (!who || !el || !d.senders) return;
    oscSenders(el, d, who.set);
    if (now) now.textContent = oscPairedText(d);
    if (log && log.open) oscMessages(log);
  }
  function oscKeyBox() {
    var out = h('div', { class: 'addr osckeyout', id: 'osckeyout', hidden: true }), where = h('div', { class: 'hint', id: 'osckeywhere', hidden: true });
    var show = h('button', { class: 'btn grow', id: 'osckeyshow', text: 'Show the key', onclick: function () { if (out.hidden) ask({}); else hide(); } });
    function hide() { clearTimeout(oscKeyTimer); out.textContent = ''; where.textContent = ''; out.hidden = where.hidden = true; show.textContent = 'Show the key'; }
    function ask(body) {
      api('POST', '/api/osc/key', body).then(function (r) {
        if (!r.ok) return say((r.data.error || 'Could not show the key') + '.', true);
        out.textContent = r.data.prefix; out.hidden = where.hidden = false; show.textContent = 'Hide the key';
        where.textContent = 'In TouchOSC, put it at the front of every address in the layout: ' + r.data.example + ' in place of /pvj/stop (in each control\'s OSC message, or once with a script that adds it). It hides itself after ' + OSC_KEY_SECONDS + ' seconds.';
        if (body.new) say('A new key is made. The old one no longer works.');
        clearTimeout(oscKeyTimer);
        oscKeyTimer = setTimeout(hide, OSC_KEY_SECONDS * 1000);
      });
    }
    var fresh = h('button', { class: 'btn', id: 'osckeynew', text: 'Make a new key', onclick: function () {
      confirmRow('Make a new key? The old one stops working at once, and every address in the layout needs the new one.', 'Make a new key', 'Keep this one', function () { ask({ new: true }); }, fresh);
    } });
    return h('div', { class: 'list sp', id: 'osckeybox' }, h('div', { class: 'row wrap' }, show, fresh), out, where);
  }
  // A theme may name a style (D54): one of the few looks app.css has a block for. The name goes on the root element,
  // where the server also writes it into the page, and only a name from this list is ever put there; anything else
  // gives the default look. With a style that has a colour per area, the root also says which area is open.
  var STYLES = ['signal'];
  function chosenTheme() { return S.themes.filter(function (x) { return S.theme && x.id === S.theme.name; })[0] || null; }
  function markLook() {
    var root = document.documentElement, th = chosenTheme();
    if (!S.themes.length) return;                 // not read yet (the connect screen): what the server wrote stays
    var style = th && STYLES.indexOf(th.style) >= 0 ? th.style : null;
    if (style) root.setAttribute('data-style', style); else root.removeAttribute('data-style');
  }
  // A style that draws a slider itself needs to know how full it is: --fill on each slider, from its value. Set
  // through the script (the policy allows that, not a style attribute), on every input and four times a second,
  // because most sliders here are also moved by the box (a poll, a controller). The default look does nothing.
  function fillRanges() {
    if (!document.documentElement.hasAttribute('data-style')) return;
    var rs = document.querySelectorAll('input[type=range]');
    for (var i = 0; i < rs.length; i++) {
      var r = rs[i], min = r.min === '' ? 0 : parseFloat(r.min), max = r.max === '' ? 100 : parseFloat(r.max), v = parseFloat(r.value);
      var part = max > min && isFinite(v) ? Math.max(0, Math.min(1, (v - min) / (max - min))) : 0;
      var fill = (Math.round(part * 1000) / 10) + '%';
      if (r.getAttribute('data-fill') !== fill) { r.setAttribute('data-fill', fill); r.style.setProperty('--fill', fill); }
    }
  }
  function markArea() {
    var area = !S.device ? null : S.tab === 'room' ? 'room' : S.tab === 'mix' ? 'mix' : S.tab === 'system' ? (S.sys === 'vibes' ? 'shaders' : 'system') : 'clips';
    if (area) document.documentElement.setAttribute('data-area', area); else document.documentElement.removeAttribute('data-area');
  }
  // A small picture of a look, drawn from its own tokens (the page, a surface, the colour of each part of the panel, a
  // title in its font and case, a button and a state chip), so a look can be judged before it is tapped. Every value
  // is set through the script (the policy allows that, not a style attribute), a colour only if it is #rrggbb, a font
  // only from this list: nothing of a theme file is ever written into the page as text of a style.
  var LOOK_FONTS = { archivo: '"Archivo", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif', system: 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
    'jetbrains-mono': '"JetBrains Mono", ui-monospace, Menlo, Consolas, monospace' };
  var LOOK_AREAS = ['room', 'shaders', 'clips', 'mix', 'system'];
  function lookColour(c, fallback) { return typeof c === 'string' && /^#[0-9a-fA-F]{6}$/.test(c) ? c : fallback; }
  function lookNumber(v, low, high, fallback) { return typeof v === 'number' && isFinite(v) ? Math.max(low, Math.min(high, v)) : fallback; }
  function lookLum(c) {
    var v = [1, 3, 5].map(function (i) { var x = parseInt(c.slice(i, i + 2), 16) / 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); });
    return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2];
  }
  function lookOn(c, a, b) {       // of a and b, the one that reads better on c (as the box works out the text on an area)
    var l = lookLum(c), ra = (Math.max(l, lookLum(a)) + 0.05) / (Math.min(l, lookLum(a)) + 0.05), rb = (Math.max(l, lookLum(b)) + 0.05) / (Math.min(l, lookLum(b)) + 0.05);
    return ra >= rb ? a : b;
  }
  // An accent may be chosen only where it can be read: the box refuses one that, written on the page or on a surface
  // of the look in use, is under 4.5 to 1 (themes.accent_problems), so only the swatches that pass are offered.
  function lookRatio(a, b) { var x = lookLum(a), y = lookLum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); }
  function accentsFor(th) {
    var tk = th && th.look && th.look.tokens;
    if (!tk || !lookColour(tk.bg, null) || !lookColour(tk.cd, null)) return [];
    return ACCENTS.filter(function (c) {
      var on = lookRatio(c, '#000000') >= lookRatio(c, '#ffffff') ? '#000000' : '#ffffff';
      if (lookRatio(c, on) < 4.5) return false;
      return th.style !== 'default' || (lookRatio(c, tk.bg) >= 4.5 && lookRatio(c, tk.cd) >= 4.5);
    });
  }
  function lookTile(th, chosen, onPick) {
    var look = th.look || {}, tk = look.tokens || {}, areas = look.areas || {}, states = look.states || {}, d = look.design || null;
    var bg = lookColour(tk.bg, '#121214'), cd = lookColour(tk.cd, '#1c1c20'), fg = lookColour(tk.fg, '#f2f1ec'), ln = lookColour(tk.ln, fg);
    var ac = lookColour(areas.room, lookColour(tk.ac, fg)), on = areas.room ? lookOn(ac, bg, fg) : lookColour(tk.on, bg);
    var chipBg = lookColour(states.active, cd), chipFg = states.active ? lookOn(chipBg, bg, fg) : fg;
    var radius = d ? lookNumber(d.radius_control, 0, 24, 0) : 6, panel = d ? lookNumber(d.radius_panel, 0, 24, 0) : 6, rule = d ? lookNumber(d.border_width, 0, 4, 3) : 1.5;
    var art = h('span', { class: 'lt-art' });
    art.style.background = bg; art.style.color = fg; art.style.borderRadius = Math.min(panel, 14) + 'px';
    var strip = h('span', { class: 'lt-areas' });
    (th.areas ? LOOK_AREAS.map(function (a) { return lookColour(areas[a], null); }).filter(Boolean) : [ac]).forEach(function (c) {
      var block = h('i'); block.style.background = c; strip.appendChild(block);
    });
    var title = h('span', { class: 'lt-title', text: 'Room' });
    if (d) {
      title.style.fontFamily = Object.prototype.hasOwnProperty.call(LOOK_FONTS, d.font_title) ? LOOK_FONTS[d.font_title] : LOOK_FONTS.system;
      title.style.fontWeight = String(lookNumber(d.title_weight, 400, 900, 900));
      if (d.title_case === 'capitals') { title.style.textTransform = 'uppercase'; title.style.letterSpacing = '.04em'; }
    }
    var surface = h('span', { class: 'lt-surface' });
    surface.style.background = cd; surface.style.borderRadius = Math.min(panel, 10) + 'px';
    var btn = h('span', { class: 'lt-btn', text: 'Play' });
    var outlined = d && d.primary === 'outlined';
    btn.style.background = outlined ? 'transparent' : ac; btn.style.color = outlined ? fg : on;
    btn.style.border = Math.max(rule, outlined ? 1 : 0) + 'px solid ' + (outlined ? fg : ac); btn.style.borderRadius = Math.min(radius, 12) + 'px';
    var chip = h('span', { class: 'lt-chip', text: 'Active' });
    chip.style.background = chipBg; chip.style.color = chipFg; chip.style.borderRadius = (d ? Math.min(radius, 12) : 10) + 'px';
    if (!states.active) chip.style.border = '1px solid ' + ln;
    if (d) [btn, chip].forEach(function (el) { el.style.fontFamily = Object.prototype.hasOwnProperty.call(LOOK_FONTS, d.font_text) ? LOOK_FONTS[d.font_text] : LOOK_FONTS.system; });
    surface.appendChild(btn); surface.appendChild(chip);
    art.appendChild(strip); art.appendChild(title); art.appendChild(surface);
    return h('button', { class: 'looktile' + (chosen ? ' on' : ''), 'data-theme': th.id, 'data-mine': th.source === 'addon' ? '1' : false, 'aria-pressed': chosen ? 'true' : 'false', onclick: onPick },
      art, h('span', { class: 'lt-name' }, h('span', { class: 'lt-label', text: th.name }),
        th.source === 'addon' ? h('span', { class: 'lt-mine', text: 'yours' }) : null, chosen ? h('span', { class: 'lt-inuse', text: 'in use' }) : null));
  }
  var lookNote = { text: '', err: false };      // what adding, saving or removing a theme said; survives the redraw
  var THEME_BYTES = 16 * 1024;                  // themes.MAX_FILE: the box refuses more, and so nothing larger is read here
  function appearanceCard() {
    var t = S.theme || {};
    var fresh = function () {
      document.querySelector('link[href^="/theme.css"]').setAttribute('href', '/theme.css?v=' + Date.now());
      markLook();
      render();
      fillRanges();
    };
    var note = function (text, isErr) { lookNote = { text: text || '', err: !!isErr }; sayAt(document.getElementById('themeresult'), lookNote.text, lookNote.err); };
    var apply = function (name, accent) {
      act('POST', '/api/theme', { name: name, accent: accent }, function (d) {
        S.theme = d.theme; S.accentDropped = '';
        lookNote = { text: '', err: false };
        fresh();
      });
    };
    var now = chosenTheme();
    var pick = h('input', { type: 'file', id: 'themepick', accept: '.json,application/json', hidden: true });
    pick.addEventListener('change', function () {
      var f = pick.files && pick.files[0];
      pick.value = '';
      if (!f) return;
      if (f.size > THEME_BYTES) return note(f.name + ' is too large for a theme (at most 16 KB). Choose a theme file: a small .json file saved from this page or made with tools/figma-theme.py.', true);
      var reader = new FileReader();
      reader.onerror = function () { note('Could not read ' + f.name + '.', true); };
      reader.onload = function () {
        api('POST', '/api/theme/add', { file: String(reader.result) }).then(function (r) {
          if (!r.ok) return note((r.data.error || 'The theme was not added (HTTP ' + r.status + ')') + '. Nothing was changed.', true);
          S.themes = r.data.available; S.themeSkipped = r.data.skipped || [];
          lookNote = { text: (r.data.replaced ? 'Replaced your theme ' : 'Added ') + r.data.name + '.' + (S.theme && S.theme.name === r.data.added ? '' : ' Tap it to use it.')
            + (r.data.warnings || []).map(function (w) { return ' Note: ' + w + '.'; }).join(''), err: false };
          fresh();
          say(lookNote.text);
        });
      };
      reader.readAsText(f);
    });
    var yours = S.themes.filter(function (th) { return th.source === 'addon'; });
    return h('div', { class: 'card', id: 'lookcard' }, h('h2', { text: 'Appearance' }),
      h('div', { class: 'row wrap', id: 'lookthemes' }, S.themes.map(function (th) {
        // the accent goes with the look only where that look can carry it (the box would refuse it otherwise)
        return lookTile(th, th.id === t.name, function () { apply(th.id, t.accent && accentsFor(th).indexOf(t.accent) >= 0 ? t.accent : null); });
      })),
      now && now.areas ? h('div', { class: 'hint', id: 'lookareas', text: now.name + ' gives each part of the panel its own colour (Room, Shaders, clips, Mix, System), so there is no accent to choose.' }) : [
        h('div', { class: 'k', text: 'Accent' }),
        h('div', { class: 'swatches' },
          h('button', { class: 'btn small', text: 'Default', onclick: function () { apply(t.name, null); } }),
          accentsFor(now).map(function (c) {
            var sw = h('button', { class: 'swatch' + (t.accent === c ? ' cur' : ''), 'aria-label': 'Accent ' + c, onclick: function () { apply(t.name, c); } });
            sw.style.background = c;
            return sw;
          })),
        S.accentDropped ? h('div', { class: 'hint warn', id: 'accentdropped', text: S.accentDropped + '. Tap Default, or another accent.' }) : null],
      h('h2', { text: 'Your own themes' }),
      h('div', { class: 'hint', id: 'themehint', text: 'A theme is a small file of colours, shapes and type. Save a look as a file to start from, change it (by hand, or from Figma), and add it here. It travels in a settings file.' }),
      pick,
      h('div', { class: 'row wrap', id: 'themeacts' },
        h('button', { class: 'btn on pri grow', id: 'themeadd', text: 'Add a theme', onclick: function () { pick.click(); } }),
        h('button', { class: 'btn grow', id: 'themesave', text: 'Save this look as a file', onclick: function () {
          act('POST', '/api/theme/export', {}, function (d) { saveFile(d.name, d.file); note('Saved ' + d.name + '.' + (d.note ? ' ' + d.note : '')); });
        } })),
      h('div', { class: 'msg inmsg' + (lookNote.err ? ' err' : ''), id: 'themeresult', role: 'status', text: lookNote.text }),
      (S.themeSkipped || []).length ? h('div', { class: 'hint warn', id: 'themeskipped', text: (S.themeSkipped.length === 1 ? 'One theme file on this box could not be used: ' : S.themeSkipped.length + ' theme files on this box could not be used: ')
        + S.themeSkipped.map(function (k) { return k.file + ': ' + k.why; }).join('. ') + '.' }) : null,
      yours.length ? h('div', { class: 'list', id: 'themelist' }, yours.map(function (th) {
        return h('div', { class: 'item' }, h('span', { class: 'lname', text: th.name }),
          h('div', { class: 'row' }, h('button', { class: 'btn small del', 'data-remove': th.id, 'aria-label': 'Remove the theme ' + th.name, text: 'Remove', onclick: function (e) {
            confirmRow('Remove the theme ' + th.name + ' from this box?' + (th.id === t.name ? ' It is the look in use: the panel goes back to Dark stage.' : ''), 'Remove', 'Keep it', function () {
              api('POST', '/api/theme/remove', { id: th.id }).then(function (r) {
                if (!r.ok) return note((r.data.error || 'The theme was not removed') + '.', true);
                S.theme = r.data.theme; S.themes = r.data.available; S.themeSkipped = r.data.skipped || []; S.accentDropped = '';
                lookNote = { text: 'Removed ' + th.name + '.', err: false };
                fresh();
                say(lookNote.text);
              });
            }, e.currentTarget);
          } })));
      })) : null);
  }
  var accessForm = { pin: false, view: true, live: false, seconds: 300, minutes: 60 };  // survives redraws
  var accessTimer = null;
  // One vocabulary for the three kinds of access, wherever the panel names them.
  function roleName(r) { return r === 'view' ? 'Guest (can watch)' : r === 'live' ? 'Presenter (can play and mix)' : 'Owner (everything)'; }
  var JOIN_MINUTES = [[15, '15 minutes'], [60, '1 hour'], [120, '2 hours']];       // how long a new code works; what a presenter may choose (auth.py)
  var SHOW_SECONDS = [[60, '1 minute'], [300, '5 minutes'], [900, '15 minutes'], [3600, '1 hour']];
  // "Let someone in": codes people join with, and putting them on the room screen. `all`: the owner's page, with the
  // presenter code and the PIN too. Without it only the guest code, which is all a presenter may handle (the server
  // decides that; this only leaves out what would be refused). Also used on the Room screen.
  function letSomeoneIn(all) {
    var live = h('div', { class: 'list', id: 'accesslive' });
    var last = null;
    var qrOf = {};       // role -> { code, n }: the QR picture is asked for again only when the code changed (the code itself is never put in an address)
    function left(sec) { var m = Math.floor(sec / 60), s2 = sec % 60; return m + ':' + (s2 < 10 ? '0' : '') + s2; }
    function chooser(id, label, options, value, set) {
      var sel = h('select', { class: 'text-input', id: id }, options.map(function (o) { return h('option', { value: o[0], text: o[1], selected: o[0] === value }); }));
      sel.addEventListener('change', function () { set(parseInt(sel.value, 10)); });
      return h('div', { class: 'chooser' }, h('label', { class: 'field', for: id, text: label }), sel);
    }
    function send(path, body, said) {
      return api('POST', path, body).then(function (r) {
        if (!live.isConnected) return;
        if (!r.ok) { say(r.data.error || 'Something went wrong', true); return refresh(true); }
        say(said || '');
        drawLive(r.data);
      });
    }
    function make(role, control) {
      var existing = last && last.codes.filter(function (c) { return c.role === role; })[0];
      var time = JOIN_MINUTES.filter(function (o) { return o[0] === accessForm.minutes; })[0][1];
      function go(replace) { send('/api/access/code', { role: role, minutes: accessForm.minutes, replace: replace }, roleName(role) + ' code made. It works for ' + time + '.'); }
      if (!existing) return go(false);
      confirmRow((existing.by === 'owner' && !can('full') ? 'The owner made the code that is active. ' : '') + 'Make a new code? The one that is active stops working.',
        'New code', 'Keep it', function () { go(true); }, control);
    }
    function drawLive(d) {
      last = d;
      if (all && controllerCodeDraw) controllerCodeDraw(d.controller);
      live.textContent = '';
      var scr = d.screen, codes = d.codes.filter(function (c) { return all || c.role === 'view'; });
      live.appendChild(h('div', { class: 'hint', id: 'accesshint', text: all ?
        'A code lets someone open this panel on their own phone, by typing it or scanning its QR code. It stops working by itself.' :
        'A guest code lets someone open this panel on their own phone as ' + roleName('view') + ': they see what plays and change nothing. It stops working by itself.' }));
      live.appendChild(chooser('joinminutes', 'A new code works for', JOIN_MINUTES, accessForm.minutes, function (v) { accessForm.minutes = v; }));
      live.appendChild(h('div', { class: 'row wrap' },
        h('button', { class: 'btn on pri small', id: 'newguest', text: 'Guest code', onclick: function (e) { make('view', e.target); } }),
        all ? h('button', { class: 'btn small', id: 'newpresenter', text: 'Presenter code', onclick: function (e) { make('live', e.target); } }) : null,
        all ? h('button', { class: 'btn small', id: 'printsheet', text: 'Print access sheet', onclick: function () { printSheet(d); } }) : null));
      if (!codes.length) live.appendChild(h('div', { class: 'hint', id: 'nocodes', text: all ? 'No code is active.' : 'No guest code is active.' }));
      codes.forEach(function (c) {
        var byOwner = c.by === 'owner' && !can('full');
        if (!qrOf[c.role] || qrOf[c.role].code !== c.code) qrOf[c.role] = { code: c.code, n: Date.now() };
        live.appendChild(h('div', { class: 'item join-code', 'data-role': c.role },
          h('span', {}, roleName(c.role), h('br'), h('span', { class: 'mono big-code', text: c.code }), h('br'),
            h('span', { class: 'hint', text: 'Works for ' + left(c.seconds_left) + ' more, ' + plural(c.uses_left, 'use') + ' left' + (byOwner ? '. Made by the owner.' : '') })),
          h('img', { class: 'qr', alt: 'QR code for the ' + roleName(c.role) + ' code', src: '/api/qr.svg?for=' + c.role + '&t=' + qrOf[c.role].n }),
          h('button', { class: 'btn small endcode', text: 'End this code', 'aria-label': 'End the ' + roleName(c.role) + ' code', onclick: function (e) {
            confirmRow((byOwner ? 'The owner made this code. ' : '') + 'End this code? Nobody else can join with it. People who already joined stay.',
              'End this code', 'Keep it', function () { send('/api/access/cancel', { role: c.role }, 'The code is ended.'); }, e.target);
          } })));
      });
      if (d.screen_available) {
        var shown = scr.items.filter(function (i) { return i !== 'address'; }).map(function (i) { return i === 'pin' ? 'the ' + roleName('full') + ' PIN' : 'the ' + roleName(i) + ' code'; });
        live.appendChild(h('div', { class: 'field', text: 'On the room screen' }));
        live.appendChild(h('div', { class: 'hint', id: 'accessscreenline', text: scr.showing ? 'On the room screen now: ' + (shown.join(', ') || 'the address') + '. Hides in ' + left(scr.seconds_left) + '.' :
          scr.other ? 'Something else is on the room screen, put there by the owner. The guest code can be shown when that is gone.' : 'Nothing on the room screen.' }));
        if (all) [['pin', roleName('full') + ': the PIN, as text'], ['view', roleName('view') + ': code and QR'], ['live', roleName('live') + ': code and QR']].forEach(function (it) {
          live.appendChild(toggle('show-' + it[0], it[1], accessForm[it[0]], function (v) { accessForm[it[0]] = v; }));
        });
        var canShow = all || !scr.other;
        if (canShow) live.appendChild(chooser('showsecs', 'Show it for', SHOW_SECONDS, accessForm.seconds, function (v) { accessForm.seconds = v; }));
        live.appendChild(h('div', { class: 'row wrap' },
          canShow ? h('button', { class: 'btn on pri small', id: 'showaccess', text: scr.showing ? 'Show again' : 'Show on the room screen', onclick: function (e) {
            var items = all ? ['pin', 'view', 'live'].filter(function (i) { return accessForm[i]; }) : ['view'];
            if (!items.length) return say('Choose what to show.', true);
            function show() { send('/api/access/screen', { show: true, items: items, seconds: accessForm.seconds }, 'On the room screen.'); }
            if (all && accessForm.pin) return confirmRow('Show the ' + roleName('full') + ' PIN on the room screen? Anyone who can see the screen can then pair with everything allowed.',
              'Show the PIN', 'Do not show it', show, e.currentTarget);
            // No guest code yet: make it first, for the time chosen above (a code the box makes for a show lasts
            // as long as the show, which is not what was chosen). 409: someone made one meanwhile; show that one.
            if (all || codes.length) return show();
            api('POST', '/api/access/code', { role: 'view', minutes: accessForm.minutes, replace: false }).then(function (r) {
              if (!live.isConnected) return;
              if (!r.ok && r.status !== 409) { say(r.data.error || 'Something went wrong', true); return refresh(true); }
              show();
            });
          } }) : null,
          scr.showing ? h('button', { class: 'btn small', id: 'hideaccess', text: 'Take it off the room screen', onclick: function () {
            send('/api/access/screen', { show: false }, 'Taken off the room screen.');
          } }) : null));
      }
      clearTimeout(accessTimer);
      // also while codes from a controller are on: one can appear on the display with nobody touching this page
      if (scr.showing || scr.other || d.codes.length || (all && d.controller && d.controller.enabled)) accessTimer = setTimeout(function () { if (live.isConnected) refresh(); }, 5000);
    }
    function refresh(force) {
      // The check is after the answer arrives: on the first call the part is not on the page yet.
      api('GET', '/api/access').then(function (r) {
        if (!live.isConnected) return;
        var a = document.activeElement;
        if (!force && last && (live.querySelector('#confirmrow') || (a && live.contains(a) && a.tagName === 'SELECT'))) {     // a question is open, or a choice is being made: look again later
          clearTimeout(accessTimer);
          accessTimer = setTimeout(function () { if (live.isConnected) refresh(); }, 5000);
          return;
        }
        if (r.ok) drawLive(r.data);
        else { live.textContent = ''; live.appendChild(h('div', { class: 'hint', text: r.data.error || 'Not available' })); }
      });
    }
    refresh();
    return live;
  }
  // A code from a controller (D61): the two switches, what the box did, and End. The card never has the digits: they
  // are on the box's display only. It is drawn from the same answer as "Let someone in" (GET /api/access), which
  // carries `controller` for the owner; the function below is called with it each time that answer arrives.
  var controllerCodeDraw = null;
  function agoWords(sec) { return sec < 60 ? 'just now' : sec < 3600 ? plural(Math.floor(sec / 60), 'minute') + ' ago' : plural(Math.floor(sec / 3600), 'hour') + ' ago'; }
  function controllerCodeCard() {
    var card = h('div', { class: 'card', id: 'ctlcodecard' }, h('h2', { text: 'A code from a controller' }));
    var body = h('div', { class: 'list', id: 'ctlcodebody' });
    var KIND = { join: 'presenter', owner: 'full access' };
    var HOW = { used: 'used', expired: 'ran out unused', 'pressed again': 'hidden at the controller', cancelled: 'ended from the panel',
      replaced: 'replaced by a newer one', 'switched off': 'ended when the switch went off', 'not shown': 'the display could not show it' };
    function send(change, said, sw) {
      api('POST', '/api/access/controller', change).then(function (r) {
        if (!card.isConnected) return;
        if (!r.ok) { say(r.data.error || 'Something went wrong', true); if (sw) sw.setAttribute('aria-checked', sw.getAttribute('aria-checked') === 'true' ? 'false' : 'true'); return; }
        say(said);
        draw(r.data.controller, true);
      });
    }
    function flip(key, question, yes, onText, offText) {
      return function (v, sw) {
        var change = {}; change[key] = v;
        if (!v) return send(change, offText, sw);
        confirmRow(question, yes, 'Leave it off', function () { send(change, onText, sw); }, sw, function () { sw.setAttribute('aria-checked', 'false'); });
      };
    }
    function draw(c, force) {
      if (!c || (!force && asking(card))) return;          // a question is open: it is not wiped
      body.textContent = '';
      body.appendChild(h('div', { class: 'hint', id: 'ctlcodehint', text: 'For when you stand at the box with a MIDI controller and no paired phone. ' +
        'Hold the pad or button that has this action for 3 seconds and let go: the box draws a one-time code on its own display for ' +
        plural(Math.round(c.seconds / 60), 'minute') + ', and one new device pairs with it. Anyone who can reach the controller can do this, so it is off until you switch it on here.' }));
      body.appendChild(toggle('ctlcode-on', 'Presenter codes from a controller', c.enabled,
        flip('enabled', 'Let a controller show a presenter code? Anyone who can hold a button on a controller plugged into the box can then pair a device that plays and mixes.',
          'Switch it on', 'Codes from a controller are on.', 'Codes from a controller are off.'),
        'Pairs one device as ' + roleName('live') + '. The MIDI action is "Show a one-time presenter code".'));
      if (c.enabled) body.appendChild(toggle('ctlcode-owner', 'Full access codes too', c.owner,
        flip('owner', 'Let a controller show a full access code? Anyone who can hold a button on a controller plugged into the box can then pair a device with everything allowed.',
          'Allow full access codes', 'Full access codes from a controller are allowed.', 'Full access codes from a controller are off.'),
        'Pairs one device as ' + roleName('full') + ', like the PIN. Leave it off unless you need it. The MIDI action is "Show a one-time full access code".'));
      if (c.enabled && !c.midi_on) body.appendChild(h('div', { class: 'hint', id: 'ctlcodemidi', text: 'MIDI controllers are switched off (System, MIDI controller), so no control can ask for a code yet.' }));
      if (c.enabled) body.appendChild(h('div', { class: 'hint', id: 'ctlcodehow', text: 'Give a pad or button the action under System, MIDI controller: tap it on its controller\'s card, or use Learn. ' +
        'On a Launchpad Mini the eighth pad of the top row shows a presenter code already. A second press takes the code off the display. At most ' + c.status.per_hour + ' codes an hour.' }));
      var s = c.status;
      if (s.active) {
        body.appendChild(h('div', { class: 'codeshown', id: 'ctlcodeshown', role: 'status' },
          h('span', { text: 'A one-time ' + KIND[s.kind] + ' code is on the box\'s display now, put there from a controller ' + agoWords(s.shown_ago) + '. It works for ' +
            Math.floor(s.seconds_left / 60) + ':' + ('0' + s.seconds_left % 60).slice(-2) + ' more.' }),
          h('div', { class: 'row' }, h('button', { class: 'btn small', id: 'ctlcodeend', text: 'End this code', onclick: function () { send({ cancel: true }, 'The code is ended and off the display.'); } }))));
      } else {
        body.appendChild(h('div', { class: 'hint', id: 'ctlcodelast', text: s.last ?
          'The last one: a ' + KIND[s.last.kind] + ' code, ' + agoWords(s.last.ended_ago) + ', ' + (HOW[s.last.how] || s.last.how) +
          (s.last.device ? ' by the device "' + s.last.device + '" (it is in the list below)' : '') + '.' :
          'No code has been shown from a controller since the box started.' }));
      }
    }
    controllerCodeDraw = function (c) { if (card.isConnected) draw(c); };
    card.appendChild(body);
    return card;
  }
  // People and codes. A presenter gets the first card only, with the guest code only. The owner also gets the paired
  // devices, the links that do not expire and the PIN.
  function accessCards() {
    var full = can('full');
    var first = h('div', { class: 'card', id: 'accesscard' }, h('h2', { text: 'Let someone in' }), letSomeoneIn(full));
    if (!full) return [first];
    var card = h('div', { class: 'card', id: 'devicescard' }, h('h2', { text: 'Paired devices' }));
    var devices = h('div', { class: 'list sp', id: 'devicelist' });
    function drawDevices() {
      devices.textContent = '';
      S.devices.forEach(function (d) {
        var me = !!S.device && d.id === S.device.id;
        devices.appendChild(listRow({ cls: 'device-entry', data: d.id, name: d.name, state: roleName(d.role) + (me ? ' · this phone' : ''),
          primary: h('button', { class: 'btn plain', text: 'Remove', 'aria-label': 'Remove ' + d.name, onclick: function (e) {
            confirmRow(me ? 'Remove this phone? You will need the PIN to get back in.' : 'Remove ' + d.name + '? It needs a code, a link or the PIN to get back in.',
              'Remove', 'Keep it', function () {
                act('POST', '/api/devices/revoke', { id: d.id }, function () {
                  S.devices = S.devices.filter(function (x) { return x.id !== d.id; });
                  if (me) { S.device = null; return render(); }
                  say(d.name + ' is removed.');
                  drawDevices();
                });
              }, e.currentTarget);
          } }) }));
      });
    }
    drawDevices();
    var link = h('input', { class: 'text-input mono', readonly: true, 'aria-label': 'Link', hidden: true });
    var linkQr = h('img', { class: 'qr', id: 'linkqr', alt: 'QR code for the link', hidden: true });
    var role = h('select', { class: 'text-input', id: 'linkrole' }, h('option', { value: 'view', text: roleName('view') }), h('option', { value: 'live', text: roleName('live') }));
    var pinOut = h('div', { class: 'msg inmsg mono', id: 'pinout', role: 'status' });
    card.appendChild(devices);
    card.appendChild(labelled('A link that does not expire', role, 'For someone who is here often. It works until you remove its device from the list above.'));
    card.appendChild(h('div', { class: 'row' }, h('button', { class: 'btn grow', id: 'makelink', text: 'Create link', onclick: function () {
      act('POST', '/api/devices/invite', { name: role.value === 'view' ? 'Guest link' : 'Presenter link', role: role.value, origin: location.origin }, function (d) {
        link.value = location.origin + '/#token=' + d.token; link.hidden = false; link.select();
        if (d.qr_svg) { linkQr.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(d.qr_svg); linkQr.hidden = false; }
        api('GET', '/api/devices').then(function (r) { if (r.ok) { S.devices = r.data.devices; drawDevices(); } });
      });
    } })));
    card.appendChild(link);
    card.appendChild(linkQr);
    card.appendChild(h('div', { class: 'field', text: 'The ' + roleName('full') + ' PIN' }));
    card.appendChild(h('div', { class: 'hint', text: 'The PIN pairs a phone with everything allowed. It is on the box\'s display when nothing is paired.' }));
    card.appendChild(h('div', { class: 'row' }, h('button', { class: 'btn grow', id: 'newpin', text: 'New PIN', onclick: function (e) {
      confirmRow('Make a new PIN? The old PIN stops working. Phones that are already paired stay paired.', 'New PIN', 'Keep the old one', function () {
        act('POST', '/api/pin/rotate', {}, function (d) { pinOut.textContent = 'New PIN: ' + d.pin; say('The PIN is changed.'); });
      }, e.currentTarget);
    } })));
    card.appendChild(h('div', { class: 'row' }, h('button', { class: 'btn grow', id: 'unlockpair', text: 'Unblock joining', onclick: function () {
      act('POST', '/api/pin/unlock', {}, function () { pinOut.textContent = 'Joining is open again (the PIN is unchanged).'; say('Joining is open again.'); });
    } })));
    card.appendChild(h('div', { class: 'hint', text: 'After too many wrong PINs or codes the box stops taking them for a while. Unblock joining opens it again at once.' }));
    card.appendChild(pinOut);
    return [first, controllerCodeCard(), card];
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
        return h('figure', {}, h('img', { class: 'qr-big', alt: 'QR code for the ' + roleName(c.role) + ' code', src: '/api/qr.svg?for=' + c.role + '&t=' + Date.now() }),
          h('figcaption', { text: roleName(c.role) + ': ' + c.code + ' (expires)' }));
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
  // The Room screen lives in room.js; it borrows these helpers.
  // letIn: the guest code part of People and codes, so staff on the Room screen need not leave it to let a guest in.
  function roomCtx() {
    return { h: h, api: api, say: say, can: can, moduleOn: moduleOn, state: S, letIn: function () { return letSomeoneIn(false); },
      confirmRow: confirmRow, switchFeature: shaderCtx().switchFeature, poll: poll,
      openShaders: function () { openSys('vibes', S.tab === 'system' ? null : S.tab); },
      openProjectors: can('full') ? function () { openSys('projectors', S.tab === 'system' ? null : S.tab); } : null };
  }
  function stopTimers() {
    [netTimer, midiTimer, midiLightTimer, accessTimer, updateTimer, healthTimer, syncTimer, confirmTimer, pageStateTimer, dmxTimer, oscTimer].forEach(clearTimeout);
  }
  function render() {
    markArea();
    stopTimers();
    keepNetForm();
    keepSyncForm();
    app.textContent = '';
    if (!S.device) { S.landing = true; app.appendChild(connect()); return; }
    var screens = { live: live, mix: mix, media: media, system: system };
    var names = [['live', 'Live'], ['mix', 'Mix'], ['media', 'Media'], ['system', 'System']];
    // The Room screen (room.js): one more tab while its module is on. A presenter or a guest starts on it when the
    // page is first loaded or the device has just been paired; nobody already on another screen is ever moved.
    var landing = S.landing !== false;
    if (S.modules.length) S.landing = false;      // decided once the modules are known, not by a render that came before them
    if (window.pvjRoom && moduleOn('room')) {
      screens.room = function () { return window.pvjRoom.screen(roomCtx()); };
      names.unshift(['room', 'Room']);
      if (landing && !can('full')) S.tab = 'room';
    } else if (S.tab === 'room') S.tab = 'live';
    markArea();        // again: the tab may just have been decided
    var tabs =h('nav', { class: 'tabs' + (names.length > 4 ? ' many' : ''), 'aria-label': 'Sections' }, names.map(function (t) {
      return h('button', { class: 'btn' + (S.tab === t[0] ? ' on' : ''), text: t[1], 'aria-current': S.tab === t[0] ? 'page' : false,
        onclick: function () { goTab(t[0]); } });
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
    if (history.state && history.state.sys) history.replaceState(null, '');   // a reload starts on Live, like every other reload
    window.addEventListener('popstate', function (e) {     // the phone's back gesture: from a System page to the index
      if (!S.device) return;
      var id = (e.state && e.state.sys) || null;
      if (id) { S.tab = 'system'; S.sys = id; S.msg = ''; }
      else leaveSysPage();
      render();
      window.scrollTo(0, 0);
    });
    var m = /^#token=([A-Za-z0-9_-]+)$/.exec(location.hash);
    var first = m ? api('POST', '/api/session', { token: m[1] }).then(function () { history.replaceState(null, '', location.pathname); }) : Promise.resolve();
    first.then(function () { return api('GET', '/api/status'); }).then(function (r) {
      if (r.ok) { S.device = r.data.device; S.status = r.data; return loadAll().then(render); }
      return api('GET', '/api/hello').then(function (h2) { S.remote = !!(h2.ok && h2.data.remote); render(); });
    });
    setInterval(poll, 1000);
    document.addEventListener('input', function (e) { if (e.target && e.target.type === 'range') fillRanges(); }, true);
    setInterval(fillRanges, 250);
    // A part of the page that was not delivered at first and has arrived now (load.js): draw with it. Only on a
    // paired page that has drawn (never under a PIN being typed), and not under somebody's hands: render() empties
    // the page and keeps only the Network and the Boxes-in-step fields, so a name being typed, a list that is open,
    // a question waiting for its answer (confirmRow) or a slider being dragged would be gone. Then the drawing
    // waits: it looks again when the field is left, and a few times a second for the rest.
    var late = null, pressed = false;
    function inUse() {
      var a = document.activeElement;
      var field = a && app.contains(a) && (a.tagName === 'TEXTAREA' || a.tagName === 'SELECT' ||
        (a.tagName === 'INPUT' && !/^(range|checkbox|radio|button|submit|reset|color|file)$/.test(a.type)));
      return !!field || pressed || asking(app);
    }
    function drawLate() {
      clearTimeout(late);
      late = null;
      if (!S.device || !app.firstChild) return;
      if (inUse()) { late = setTimeout(drawLate, 400); return; }
      render();
    }
    document.addEventListener('pvjfile', drawLate);
    // (after the moment in which the cursor is nowhere: it may be on its way to the next field)
    document.addEventListener('focusout', function () { if (late) { clearTimeout(late); late = setTimeout(drawLate, 0); } }, true);
    document.addEventListener('pointerdown', function () { pressed = true; }, true);
    ['pointerup', 'pointercancel', 'blur'].forEach(function (n) { window.addEventListener(n, function () { pressed = false; }, true); });
  }
  boot();
  // The last statement of the file, read by load.js (data-gives in index.html): the file was read as a program and
  // ran to the point where the panel was started. A file the browser cannot read as a program (a syntax error after
  // a merge, a browser too old for it), or one that stops before here, leaves this unset, and the page says so
  // instead of staying empty. What goes wrong later, in an answer from the box, is not seen by this.
  window.pvjApp = true;
})();
