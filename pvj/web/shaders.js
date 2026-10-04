// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// Shaders and Vibes: the panel's part. Loaded before app.js, which hands over its helpers (h, api, act, can, say,
// poll, moduleOn, state) each time it draws; nothing here runs until app.js calls it.
(function () {
  'use strict';

  var ui = { values: {}, dwell: null };      // survives redraws: slider positions per shader, a typed dwell time
  var timer = null;

  function vibesOn(c) { var pl = (c.state.status && c.state.status.player) || {}; return !!pl.vibes; }
  function toggleVibes(c, after) {
    c.act('POST', '/api/vibes', { on: !vibesOn(c) }, function (d) { c.say(''); if (after) after(d); setTimeout(c.poll, 1200); });
  }

  // Live screen: one big button. Presenters (live access) and above; everyone sees whether it is on.
  function liveRow(c) {
    if (!c.moduleOn('shaders')) return null;
    return c.h('div', { class: 'row' },
      c.h('button', { class: 'btn big grow' + (vibesOn(c) ? ' on' : ''), id: 'vibes', 'aria-pressed': vibesOn(c) ? 'true' : 'false',
        text: vibesOn(c) ? 'Vibes is on. Stop' : 'Vibes', disabled: !c.can('live'), onclick: function () { toggleVibes(c); } }));
  }
  // Called with every status poll: the Now playing line and the button follow the player.
  function patch(c, pl, np) {
    if (np && typeof pl.shader === 'string') np.textContent = (pl.vibes ? 'Vibes: ' : 'Shader: ') + (pl.shader || 'starting');
    var b = document.getElementById('vibes');
    if (b) {
      b.textContent = pl.vibes ? 'Vibes is on. Stop' : 'Vibes';
      b.className = 'btn big grow' + (pl.vibes ? ' on' : '');
      b.setAttribute('aria-pressed', pl.vibes ? 'true' : 'false');
    }
  }

  function statusLine(d) {
    var parts = [];
    if (d.playing) {
      parts.push('On screen: ' + d.playing.name + (d.playing.checked ? '' : ' (not confirmed by the GPU)') +
        (d.playing.pass_ms ? ', ' + d.playing.pass_ms + ' ms a frame' : ''));
    } else parts.push('No shader on screen.');
    if (d.vibes && d.vibes.running) parts.push('Vibes is on' + (typeof d.vibes.next_in === 'number' ? ', next in ' + d.vibes.next_in + ' s' : ', starting'));
    else if (d.vibes && d.vibes.last) parts.push('Vibes ' + d.vibes.last.message + ' (' + d.vibes.last.at + ')');
    return parts.join(' · ');
  }

  // Mix screen: the list, one shader's sliders, Vibes settings and upload.
  function card(c) {
    var h = c.h, full = c.can('full'), live = c.can('live');
    var body = h('div', { class: 'list', id: 'shaderbody' });
    var box = h('div', { class: 'card', id: 'shadercard' }, h('div', { class: 'k', text: 'Shaders and Vibes (beta)' }), body);
    clearTimeout(timer);
    if (!c.moduleOn('shaders')) {
      body.appendChild(h('div', { class: 'k', id: 'shadermsg', text: 'Off. Switch it on under System, Vibes (beta).' }));
      return box;
    }
    function send(path, payload, done) {
      return c.act('POST', path, payload, function (d) { c.say(''); if (d && d.shaders) draw(d); if (done) done(d); c.poll(); });
    }
    function refresh() {
      c.api('GET', '/api/shaders').then(function (r) {
        if (!box.isConnected) return;
        if (!r.ok) { body.textContent = ''; body.appendChild(h('div', { class: 'k', id: 'shadermsg', text: r.data.error || 'Not available' })); return; }
        draw(r.data);
      });
    }
    function watch() {                         // only the status line is refreshed by itself, so typing is never wiped
      clearTimeout(timer);
      timer = setTimeout(function () {
        if (!box.isConnected) return;
        c.api('GET', '/api/shaders').then(function (r) {
          var line = document.getElementById('shaderline');
          if (r.ok && line && box.isConnected) { line.textContent = statusLine(r.data); watch(); }
        });
      }, 5000);
    }
    function sliders(d, s) {
      var floats = s.inputs.filter(function (i) { return i.type === 'float' && i.max > i.min; });
      if (!floats.length || !live) return null;
      var vals = ui.values[s.id] = ui.values[s.id] || {};
      return h('div', { class: 'list', id: 'shadersliders' }, floats.map(function (i) {
        var now = typeof vals[i.name] === 'number' ? vals[i.name] : (d.playing.values[i.name] !== undefined ? d.playing.values[i.name] : i['default']);
        var out = h('span', { class: 'k', text: String(Math.round(now * 100) / 100) });
        var input = h('input', { type: 'range', id: 'shin-' + i.name, 'aria-label': i.label, min: i.min, max: i.max, step: (i.max - i.min) / 100, value: now });
        input.addEventListener('input', function () { out.textContent = String(Math.round(+input.value * 100) / 100); });
        // sent when the finger lifts: each change gives the GPU a new shader to take
        input.addEventListener('change', function () { vals[i.name] = +input.value; send('/api/shaders/play', { id: s.id, values: vals }); });
        return h('div', { class: 'slider' }, h('label', { for: 'shin-' + i.name }, i.label + ' ', out), input);
      }));
    }
    function draw(d) {
      body.textContent = '';
      body.appendChild(h('div', { class: 'k', id: 'shaderline', text: statusLine(d) }));
      if (d.error) body.appendChild(h('div', { class: 'msg err', id: 'shadererror', text: 'Refused: ' + d.error.id + ': ' + d.error.message }));
      var running = !!(d.vibes && d.vibes.running);
      body.appendChild(h('div', { class: 'row' },
        h('button', { class: 'btn grow' + (running ? ' on' : ''), id: 'vibesbtn', 'aria-pressed': running ? 'true' : 'false', disabled: !live,
          text: running ? 'Vibes is on. Stop' : 'Start Vibes', onclick: function () { send('/api/vibes', { on: !running }, function () { setTimeout(refresh, 1500); }); } }),
        running ? h('button', { class: 'btn', id: 'vibesnext', text: 'Next shader', disabled: !live,
          onclick: function () { send('/api/vibes', { next: true }, function () { setTimeout(refresh, 2500); }); } }) : null));
      d.shaders.forEach(function (s) {
        var on = !!(d.playing && d.playing.id === s.id);
        var about = s.error ? 'Cannot be shown: ' + s.error : [s.description, s.cost ? 'Cost: ' + s.cost : '', s.source === 'uploaded' ? 'uploaded' : ''].filter(Boolean).join(' · ');
        body.appendChild(h('div', { class: 'item shader-entry' + (on ? ' on' : ''), 'data-shader': s.id },
          h('span', {}, s.name, h('br'), h('span', { class: 'k', text: about })),
          h('div', { class: 'row' },
            h('button', { class: 'btn small' + (on ? ' on' : ''), text: on ? 'On screen' : 'Play', 'aria-label': 'Play ' + s.name, disabled: !live || !!s.error,
              onclick: function () { send('/api/shaders/play', { id: s.id, values: ui.values[s.id] || {} }); } }),
            full ? h('button', { class: 'btn small' + (s.vibes ? ' on' : ''), text: s.vibes ? 'In Vibes' : 'Not in Vibes', 'aria-pressed': s.vibes ? 'true' : 'false',
              'aria-label': (s.vibes ? 'Take ' : 'Put ') + s.name + (s.vibes ? ' out of Vibes' : ' in Vibes'), disabled: !!s.error,
              onclick: function () { send('/api/shaders', { action: 'vibes', id: s.id, on: !s.vibes }); } }) : null,
            full && s.source === 'uploaded' ? h('button', { class: 'btn small', text: 'Delete', 'aria-label': 'Delete ' + s.name, onclick: function () {
              if (window.confirm('Delete ' + s.name + '?')) send('/api/shaders', { action: 'delete', id: s.id });
            } }) : null)));
        if (on) { var sl = sliders(d, s); if (sl) body.appendChild(sl); }
      });
      if (full) {
        var dwell = h('input', { class: 'text-input mono', id: 'vibesdwell', type: 'number', min: d.limits.dwell[0], max: d.limits.dwell[1],
          'aria-label': 'Seconds each shader stays', value: ui.dwell === null ? d.config.dwell : ui.dwell });
        dwell.addEventListener('input', function () { ui.dwell = dwell.value; });
        var height = h('select', { class: 'text-input', id: 'shaderheight', 'aria-label': 'Drawing size' }, d.render.heights.map(function (v) {
          return h('option', { value: String(v), text: 'Draw ' + v + ' lines high', selected: v === d.config.height });
        }));
        var vary = h('select', { class: 'text-input', id: 'vibesvary', 'aria-label': 'Variation' },
          [[true, 'Vary values and colours each round'], [false, 'Always the shader’s own values']].map(function (o) {
            return h('option', { value: String(o[0]), text: o[1], selected: o[0] === d.config.vary });
          }));
        body.appendChild(h('label', { class: 'k', for: 'vibesdwell', text: 'Vibes: seconds each shader stays (' + d.limits.dwell[0] + ' to ' + d.limits.dwell[1] + ')' }));
        body.appendChild(dwell); body.appendChild(vary); body.appendChild(height);
        var picker = h('input', { type: 'file', id: 'shaderpick', hidden: true, accept: '.fs', 'aria-label': 'Choose an ISF shader file (.fs)' });
        picker.addEventListener('change', function () {
          var file = picker.files[0];
          picker.value = '';
          if (!file) return;
          if (file.size > d.limits.bytes) return c.say(file.name + ': larger than ' + Math.round(d.limits.bytes / 1024) + ' KB', true);
          var reader = new FileReader();
          reader.onload = function () { send('/api/shaders', { action: 'upload', name: file.name, source: String(reader.result) }); };
          reader.onerror = function () { c.say(file.name + ': could not be read', true); };
          reader.readAsText(file);
        });
        body.appendChild(h('div', { class: 'row' },
          h('button', { class: 'btn on small', id: 'shadersave', text: 'Save', onclick: function () {
            var payload = { action: 'config', dwell: parseFloat(dwell.value || '0'), vary: vary.value === 'true', height: parseInt(height.value, 10) };
            send('/api/shaders', payload, function () { ui.dwell = null; });
          } }),
          h('button', { class: 'btn small', id: 'shaderupload', text: 'Upload an ISF shader (.fs)', onclick: function () { picker.click(); } }), picker));
      }
      body.appendChild(h('div', { class: 'k', text: 'Drawn at ' + d.render.width + ' x ' + d.render.height + ', ' + d.render.fps +
        ' pictures a second. Speed on this board is not measured yet: watch the screen, and choose fewer lines if it stutters.' }));
      watch();
    }
    refresh();
    return box;
  }

  window.pvjShaders = { liveRow: liveRow, patch: patch, card: card };
})();
