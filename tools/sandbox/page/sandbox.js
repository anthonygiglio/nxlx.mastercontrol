// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// The switcher of the design sandbox (tools/sandbox/serve.py). It never touches the panel's page: it loads a scenario
// into the box, and opens the panel's own address for a role in a window of the chosen size. Text goes in with
// textContent only.
(function () {
  'use strict';
  var ROLES = [['owner', 'Owner'], ['operator', 'Operator'], ['guest', 'Guest'], ['nobody', 'Not paired']];
  // The frames of pvj/PANEL.md, section 2: each inside one layout of the panel, none on a breakpoint.
  var WIDTHS = [['phone', 'Phone 390', 390, 844], ['tablet', 'Tablet 820', 820, 1180], ['laptop', 'Laptop 1440', 1440, 900], ['wide', 'Wide 1728', 1728, 1117]];
  var ui = { role: 'owner', width: 'phone', data: null };
  var $ = function (id) { return document.getElementById(id); };

  function api(method, path, body) {
    var opts = { method: method, headers: {} };
    if (method === 'POST') { opts.headers['Content-Type'] = 'application/json'; opts.headers['X-Sandbox-Request'] = '1'; opts.body = JSON.stringify(body); }
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) { return { ok: r.ok, data: d }; });
    }, function () { return { ok: false, data: { error: 'the sandbox is not running' } }; });
  }
  function choices(el, list, key, after) {
    el.textContent = '';
    list.forEach(function (x) {
      var b = document.createElement('button');
      b.type = 'button';
      b.textContent = x[1];
      b.setAttribute('aria-pressed', ui[key] === x[0] ? 'true' : 'false');
      b.addEventListener('click', function () { ui[key] = x[0]; choices(el, list, key, after); after(); });
      el.appendChild(b);
    });
  }
  function address() {
    var sc = $('scenario').value || '';
    return location.origin + '/open?scenario=' + encodeURIComponent(sc) + '&role=' + ui.role;
  }
  function show(d) {
    ui.data = d;
    var sel = $('scenario'), keep = sel.value || d.scenario;
    sel.textContent = '';
    d.scenarios.forEach(function (s) {
      var o = document.createElement('option');
      o.value = s.name;
      o.textContent = s.title + (s.broken ? ' (cannot load: ' + s.broken + ')' : '');
      o.disabled = !!s.broken;
      sel.appendChild(o);
    });
    sel.value = keep;
    about();
    var st = $('status');
    st.className = d.error ? 'err' : '';
    st.textContent = d.error ? d.error : d.ready ? 'Running: ' + title(d.scenario) + '.' + (d.player === false ? ' No mpv on this computer, so nothing plays.' : '') : 'Starting...';
    var pr = $('problems');
    pr.textContent = '';
    (d.problems || []).forEach(function (p) { var li = document.createElement('li'); li.textContent = 'A step of the scenario was refused: ' + p; pr.appendChild(li); });
    link();
  }
  function title(name) {
    var s = (ui.data ? ui.data.scenarios : []).filter(function (x) { return x.name === name; })[0];
    return s ? s.title : name;
  }
  function about() {
    var s = (ui.data ? ui.data.scenarios : []).filter(function (x) { return x.name === $('scenario').value; })[0];
    $('about').textContent = s ? s.about : '';
  }
  function link() { var a = $('link'); a.href = address(); a.textContent = address(); }

  $('scenario').addEventListener('change', function () { about(); link(); });
  $('load').addEventListener('click', function () {
    var b = $('load');
    b.disabled = true; $('status').className = ''; $('status').textContent = 'Starting the box for ' + title($('scenario').value) + '...';
    api('POST', '/api/sandbox/scenario', { scenario: $('scenario').value }).then(function (r) {
      b.disabled = false;
      if (r.data && r.data.scenarios) show(r.data);
      else { $('status').className = 'err'; $('status').textContent = r.data.error || 'It did not load.'; }
    });
  });
  $('open').addEventListener('click', function () {
    var w = WIDTHS.filter(function (x) { return x[0] === ui.width; })[0];
    window.open(address(), 'nxlx-' + ui.role + '-' + ui.width, 'popup,width=' + w[2] + ',height=' + w[3]);
  });
  choices($('roles'), ROLES, 'role', link);
  choices($('widths'), WIDTHS.map(function (x) { return [x[0], x[1]]; }), 'width', function () {});
  api('GET', '/api/sandbox').then(function (r) { if (r.ok) show(r.data); else { $('status').className = 'err'; $('status').textContent = r.data.error; } });
})();
