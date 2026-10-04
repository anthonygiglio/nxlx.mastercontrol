// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// Shaders and Vibes: the panel's part. Loaded before app.js, which hands over its helpers (h, api, act, can, say,
// poll, moduleOn, state, confirmRow, openSys, rowShown, openShaders) each time it draws; nothing here runs until
// app.js calls it. Two things are drawn here: the big Vibes button on Live, and the Shaders page (System > Shaders
// and Vibes, also opened from Live), which holds everything else.
(function () {
  'use strict';

  // Survives redraws, never saved: slider positions per shader, whether Advanced is open, the last upload's answer,
  // and which setting just said "Saved".
  var ui = { values: {}, advanced: false, upload: null, saved: '' };
  var timer = null, savedTimer = null;
  var DWELLS = [30, 60, 120, 180, 300, 600, 900, 1800, 3600];

  // "nxlx-aurora.fs" reads "Aurora"
  function nice(name) {
    var t = String(name || '').replace(/\.fs$/, '').replace(/^nxlx-/, '').replace(/[-_]+/g, ' ').trim();
    return t ? t.charAt(0).toUpperCase() + t.slice(1) : '';
  }
  function span(sec) {
    if (sec < 60) return sec + ' seconds';
    if (sec < 3600) return sec / 60 === 1 ? '1 minute' : sec / 60 + ' minutes';
    return sec / 3600 === 1 ? '1 hour' : sec / 3600 + ' hours';
  }
  function left(sec) { return Math.floor(sec / 60) + ':' + (sec % 60 < 10 ? '0' : '') + sec % 60; }
  // The cost note is the shader author's own text; the bundled ones start with low, medium or high.
  function work(cost) {
    var c = String(cost || '').toLowerCase();
    if (/^low/.test(c)) return 'Light work';
    if (/^medium to high/.test(c)) return 'Medium to heavy work';
    if (/^medium/.test(c)) return 'Medium work';
    if (/^high/.test(c)) return 'Heavy work';
    return cost ? 'Cost: ' + cost : '';
  }
  function round(v) { return String(Math.round(v * 100) / 100); }

  function player(c) { return (c.state.status && c.state.status.player) || {}; }
  function words(pl) { return pl.vibes ? 'Vibes is playing' + (pl.shader ? ': ' + nice(pl.shader) : '') : 'Start Vibes'; }
  function sub(pl) { return pl.vibes ? 'Tap to stop' : 'Moving pictures, one after another'; }

  // Live screen: one big button that starts and stops Vibes and says what is playing, and the way to the page.
  // Presenters (live access) and above use it; everyone sees whether it is on.
  function liveRow(c) {
    if (!c.moduleOn('shaders')) return null;
    var pl = player(c);
    return c.h('div', { class: 'vibesrow', id: 'vibesrow' },
      c.h('button', { class: 'btn big vibesbig' + (pl.vibes ? ' on' : ''), id: 'vibes', 'aria-pressed': pl.vibes ? 'true' : 'false', disabled: !c.can('live'),
        onclick: function () {
          c.act('POST', '/api/vibes', { on: !player(c).vibes }, function () { c.say(''); setTimeout(c.poll, 1200); });
        } },
        c.h('span', { id: 'vibeswords', text: words(pl) }), c.h('span', { class: 'vibessub', id: 'vibessub', text: sub(pl) })),
      c.h('button', { class: 'btn', id: 'shaderslink', text: 'Shaders ›', 'aria-label': 'Shaders: the list, sliders and Vibes settings', onclick: c.openShaders }));
  }
  // Called with every status poll: the Now playing line and the button follow the player.
  function patch(c, pl, np) {
    if (np && typeof pl.shader === 'string') np.textContent = (pl.vibes ? 'Vibes: ' : 'Shader: ') + (nice(pl.shader) || 'starting');
    var b = document.getElementById('vibes'), w = document.getElementById('vibeswords'), s = document.getElementById('vibessub');
    if (!b || !w || !s) return;
    w.textContent = words(pl);
    s.textContent = sub(pl);
    b.className = 'btn big vibesbig' + (pl.vibes ? ' on' : '');
    b.setAttribute('aria-pressed', pl.vibes ? 'true' : 'false');
  }

  function stateLine(d) {
    var v = d.vibes || {};
    if (v.running) return 'Vibes is playing.' + (typeof v.next_in === 'number' ? ' Next one in ' + left(v.next_in) + '.' : ' Starting.');
    return d.playing ? 'One shader, until something else plays.' : 'Nothing from here is on the screen.';
  }
  // What decides the page's layout. While it stays the same, a refresh only moves the countdown.
  function shape(d) {
    return JSON.stringify([d.playing && [d.playing.id, d.playing.checked, d.playing.values], !!(d.vibes && d.vibes.running), d.vibes && d.vibes.last, d.error, d.config,
      d.shaders.map(function (s) { return [s.id, s.vibes, s.error]; })]);
  }

  // The page: now, the library, the playing shader's sliders, Vibes settings, other ways to control it.
  function page(c) {
    var h = c.h, full = c.can('full'), live = c.can('live');
    var root = h('div', { class: 'grid2 shaderpage', id: 'shaderpage' }, h('div', { class: 'card' }, h('div', { class: 'hint', text: 'Loading...' })));
    var drawn = '';
    clearTimeout(timer);

    function send(path, payload, done) {
      return c.act('POST', path, payload, function (d) { c.say(''); if (d && d.shaders) draw(d); if (done) done(d); c.poll(); });
    }
    function refresh() {
      c.api('GET', '/api/shaders').then(function (r) {
        if (!root.isConnected) return;
        if (!r.ok) { root.textContent = ''; root.appendChild(h('div', { class: 'card' }, h('div', { class: 'msg err', id: 'shadermsg', text: r.data.error || 'Not available' }))); return; }
        draw(r.data);
      });
    }
    function busy() {             // a finger on a slider or a select, or a question waiting for its answer
      var a = document.activeElement;
      return !!(a && root.contains(a) && /^(INPUT|SELECT)$/.test(a.tagName)) || !!root.querySelector('#confirmrow');
    }
    function watch() {
      clearTimeout(timer);
      timer = setTimeout(function () {
        if (!root.isConnected) return;
        c.api('GET', '/api/shaders').then(function (r) {
          if (!root.isConnected) return;
          if (!r.ok) return watch();
          if (shape(r.data) !== drawn && !busy()) return draw(r.data);
          var line = document.getElementById('shaderline');
          if (line) line.textContent = stateLine(r.data);
          watch();
        });
      }, 5000);
    }
    function saved(key) {         // a brief "Saved" beside the control that was just applied
      ui.saved = key;
      clearTimeout(savedTimer);
      savedTimer = setTimeout(function () { ui.saved = ''; var el = document.getElementById('saved-' + key); if (el) el.textContent = ''; }, 2000);
    }
    function savedMark(key) { return h('span', { class: 'saved', id: 'saved-' + key, role: 'status', text: ui.saved === key ? 'Saved' : '' }); }
    function config(patchBody, key) {       // one setting, applied on tap
      return c.act('POST', '/api/shaders', Object.assign({ action: 'config' }, patchBody), function (d) { c.say(''); saved(key); draw(d); c.poll(); });
    }

    // 1. Now
    function nowCard(d) {
      var v = d.vibes || {}, running = !!v.running;
      var rotation = d.shaders.filter(function (s) { return s.vibes && !s.error; }).length;
      var card = h('div', { class: 'card', id: 'shadernow' }, h('h2', { text: 'On screen now' }),
        h('div', { class: 'row wrap' }, h('span', { class: 'shadername', id: 'shaderplaying', text: d.playing ? nice(d.playing.name) : 'No shader on screen' }),
          d.playing ? h('span', { class: 'chip chip-active', text: 'Active' }) : null),
        h('div', { class: 'hint', id: 'shaderline', role: 'status', text: stateLine(d) }));
      if (d.playing && (!d.playing.checked || d.playing.pass_ms)) {
        card.appendChild(h('div', { class: 'hint', text: [d.playing.checked ? '' : 'Not confirmed by the GPU yet.', d.playing.pass_ms ? d.playing.pass_ms + ' ms a frame.' : ''].filter(Boolean).join(' ') }));
      }
      if (!running && v.last && v.last.message && v.last.message !== 'stopped') {
        card.appendChild(h('div', { class: 'hint', id: 'vibeslast', text: 'Vibes ' + v.last.message + ' (' + v.last.at + ').' }));
      }
      if (d.error) card.appendChild(h('div', { class: 'msg err', id: 'shadererror', text: 'The GPU refused ' + nice(d.error.id) + ': ' + d.error.message }));
      if (!live) return card;
      card.appendChild(h('div', { class: 'row wrap' },
        h('button', { class: 'btn big grow' + (running ? '' : ' on'), id: 'vibesbtn', disabled: !running && !rotation,
          text: running ? 'Stop Vibes' : 'Start Vibes', onclick: function () { send('/api/vibes', { on: !running }, function () { setTimeout(refresh, 1500); }); } }),
        running ? h('button', { class: 'btn big', id: 'vibesnext', text: 'Next one',
          onclick: function () { send('/api/vibes', { next: true }, function () { setTimeout(refresh, 2500); }); } }) : null));
      if (!rotation) {
        card.appendChild(h('div', { class: 'hint', id: 'norotation', text: 'Nothing is in the Vibes rotation. ' +
          (full ? 'Switch on "In the Vibes rotation" for at least one shader below.' : 'Someone with full access chooses which shaders are in it.') }));
      }
      return card;
    }

    // 2. The library
    function entry(d, s) {
      var on = !!(d.playing && d.playing.id === s.id), name = nice(s.name);
      var facts = [work(s.cost), s.source === 'uploaded' ? 'your upload' : '', full ? '' : (s.vibes ? 'in the Vibes rotation' : 'not in the rotation')].filter(Boolean).join(' · ');
      var row = h('div', { class: 'item shader-entry' + (on ? ' on' : ''), 'data-shader': s.id },
        h('span', {}, h('b', { text: name }), on ? ' ' : null, on ? h('span', { class: 'chip chip-active', text: 'Active' }) : null,
          s.description ? h('br') : null, s.description ? h('span', { class: 'hint', text: s.description }) : null,
          facts ? h('br') : null, facts ? h('span', { class: 'hint shaderfacts', text: facts }) : null));
      if (s.error) row.appendChild(h('div', { class: 'msg err shaderproblem', text: 'Cannot be shown: ' + s.error }));
      else if (d.error && d.error.id === s.id) row.appendChild(h('div', { class: 'msg err shaderproblem', text: 'The GPU refused it: ' + d.error.message }));
      if (!live) return row;
      row.appendChild(h('div', { class: 'row wrap shaderacts' },
        h('button', { class: 'btn on', text: 'Play', 'aria-label': 'Play ' + name, disabled: !!s.error,
          onclick: function () { send('/api/shaders/play', { id: s.id, values: ui.values[s.id] || {} }); } }),
        full ? h('label', { class: 'rot' }, h('span', { text: 'In the Vibes rotation' }),
          h('button', { class: 'switch', role: 'switch', 'aria-checked': s.vibes ? 'true' : 'false', 'aria-label': name + ' in the Vibes rotation', disabled: !!s.error,
            onclick: function () { send('/api/shaders', { action: 'vibes', id: s.id, on: !s.vibes }); } })) : null,
        full && s.source === 'uploaded' ? h('button', { class: 'btn', text: 'Remove', 'aria-label': 'Remove ' + name, onclick: function (e) {
          c.confirmRow('Remove ' + name + '? The file is deleted from the box.', 'Remove', 'Keep it', function () { send('/api/shaders', { action: 'delete', id: s.id }); }, e.target);
        } }) : null));
      return row;
    }
    function listCard(d) {
      return h('div', { class: 'card', id: 'shadercard' }, h('h2', { text: 'Shaders' }),
        live ? h('p', { class: 'hint', text: 'Play shows one shader until something else plays. Vibes goes through the ones in its rotation.' }) : null,
        h('div', { class: 'list' }, d.shaders.map(function (s) { return entry(d, s); })));
    }

    // 3. Controls for the shader that is playing
    function controlsCard(d) {
      if (!d.playing || !live) return null;
      var s = d.shaders.filter(function (x) { return x.id === d.playing.id; })[0];
      if (!s) return null;
      var floats = s.inputs.filter(function (i) { return i.type === 'float' && i.max > i.min; });
      var card = h('div', { class: 'card', id: 'shadercontrols' }, h('h2', { text: 'Controls for ' + nice(s.name) }));
      if (!floats.length) { card.appendChild(h('div', { class: 'hint', text: 'This shader has nothing to adjust.' })); return card; }
      var vals = ui.values[s.id] = ui.values[s.id] || {};
      function current(i) {
        return typeof vals[i.name] === 'number' ? vals[i.name] : (typeof d.playing.values[i.name] === 'number' ? d.playing.values[i.name] : i['default']);
      }
      // Every slider's value goes along, so the ones not touched stay where they are on screen.
      function apply() {
        var all = {};
        floats.forEach(function (i) { all[i.name] = current(i); });
        send('/api/shaders/play', { id: s.id, values: all });
      }
      var list = h('div', { class: 'list', id: 'shadersliders' }, floats.map(function (i) {
        var now = current(i);
        var out = h('span', { class: 'k', text: round(now) });
        var input = h('input', { type: 'range', id: 'shin-' + i.name, min: i.min, max: i.max, step: (i.max - i.min) / 100, value: now });
        input.addEventListener('input', function () { out.textContent = round(+input.value); });
        // sent when the finger lifts: each change gives the GPU a new shader to take
        input.addEventListener('change', function () { vals[i.name] = +input.value; apply(); });
        return h('div', { class: 'slider shaderslider' }, h('label', { for: 'shin-' + i.name }, i.label || i.name, out), input,
          h('div', { class: 'row between' }, h('span', { class: 'hint', text: round(i.min) + ' to ' + round(i.max) + ', normally ' + round(i['default']) }),
            h('button', { class: 'btn', text: 'Reset', 'aria-label': 'Reset ' + (i.label || i.name), onclick: function () { vals[i.name] = i['default']; apply(); } })));
      }));
      card.appendChild(list);
      card.appendChild(h('div', { class: 'row' }, h('button', { class: 'btn grow', id: 'shaderresetall', text: 'Reset all', onclick: function () {
        floats.forEach(function (i) { vals[i.name] = i['default']; });
        apply();
      } })));
      card.appendChild(h('p', { class: 'hint', id: 'slidernote', text: 'The picture changes when you let go of a slider. Slider values are not saved: next time the shader starts from its own values. ' +
        'Changing one keeps this shader on the screen, so Vibes stops going to the next one.' }));
      return card;
    }

    // 4. Vibes settings (full access)
    function settingsCard(d) {
      if (!full) return null;
      var cfg = d.config, listed = DWELLS.indexOf(cfg.dwell) >= 0;
      var dwell = h('select', { class: 'text-input', id: 'vibesdwell' },
        (listed ? [] : [h('option', { value: String(cfg.dwell), text: 'Custom (' + cfg.dwell + ' s)', selected: true })]).concat(DWELLS.map(function (v) {
          return h('option', { value: String(v), text: span(v), selected: v === cfg.dwell });
        })));
      dwell.addEventListener('change', function () { dwell.blur(); config({ dwell: +dwell.value }, 'dwell'); });
      var height = h('select', { class: 'text-input', id: 'shaderheight' }, d.render.heights.map(function (v) {
        return h('option', { value: String(v), text: v + ' lines', selected: v === cfg.height });
      }));
      height.addEventListener('change', function () { height.blur(); config({ height: +height.value }, 'height'); });
      var picker = h('input', { type: 'file', id: 'shaderpick', hidden: true, accept: '.fs', 'aria-label': 'Choose an ISF shader file (.fs)' });
      function uploaded(text, err) { ui.upload = { text: text, err: err }; }
      picker.addEventListener('change', function () {
        var file = picker.files[0], note = document.getElementById('shaderuploadmsg');
        picker.value = '';
        if (!file) return;
        function refuse(why) { uploaded(file.name + ' was not added: ' + why, true); if (note) { note.textContent = ui.upload.text; note.className = 'msg err'; } }
        if (file.size > d.limits.bytes) return refuse('it is larger than ' + Math.round(d.limits.bytes / 1024) + ' KB.');
        var reader = new FileReader();
        reader.onload = function () {
          // asked directly, so a refusal lands under the button and not in the page's message line
          c.api('POST', '/api/shaders', { action: 'upload', name: file.name, source: String(reader.result) }).then(function (r) {
            var why = r.data.error || 'the box did not answer';
            if (!r.ok) return refuse(why.indexOf(file.name + ': ') === 0 ? why.slice(file.name.length + 2) : why);   // the box's own words, without the name twice
            uploaded(file.name + ' was added. It is in the list above.', false);
            if (root.isConnected) draw(r.data);
          });
        };
        reader.onerror = function () { refuse('the file could not be read.'); };
        reader.readAsText(file);
      });
      var adv = h('details', { class: 'fold', id: 'shaderadvanced', open: ui.advanced },
        h('summary', { text: 'Advanced' }),
        h('div', { class: 'list shaderadv' },
          h('label', { class: 'field', for: 'shaderheight' }, 'Picture detail ', savedMark('height')), height,
          h('p', { class: 'hint', text: 'Fewer lines is lighter work. Now drawn at ' + d.render.width + ' x ' + d.render.height + ', ' + d.render.fps +
            ' pictures a second. Speed on this board is not measured yet: choose fewer lines if the picture stutters.' }),
          h('div', { class: 'row' }, h('button', { class: 'btn grow', id: 'shaderupload', text: '+ Add a shader file (.fs)', onclick: function () { picker.click(); } }), picker),
          h('div', { class: 'msg' + (ui.upload && ui.upload.err ? ' err' : ''), id: 'shaderuploadmsg', role: 'status', text: ui.upload ? ui.upload.text : '' }),
          h('p', { class: 'hint', text: 'An ISF file that draws from nothing (a generator), up to ' + Math.round(d.limits.bytes / 1024) + ' KB. Shaders that need a picture or sound are refused, with the reason.' })));
      adv.addEventListener('toggle', function () { ui.advanced = adv.open; });
      return h('div', { class: 'card', id: 'vibessettings' }, h('h2', { text: 'Vibes settings' }),
        h('label', { class: 'field', for: 'vibesdwell' }, 'Each one stays for ', savedMark('dwell')), dwell,
        h('label', { class: 'rot between' }, h('span', {}, 'Change speed and colours a little each round ', savedMark('vary')),
          h('button', { class: 'switch', id: 'vibesvary', role: 'switch', 'aria-checked': cfg.vary ? 'true' : 'false', 'aria-label': 'Change speed and colours a little each round',
            onclick: function () { config({ vary: !cfg.vary }, 'vary'); } })),
        adv);
    }

    // 5. Other ways to control it
    function remoteCard() {
      if (!full) return null;
      var links = [['midi', 'MIDI controller'], ['dmx', 'DMX lighting desk']].filter(function (l) { return c.rowShown(l[0]); });
      if (!links.length) return null;
      return h('div', { class: 'card', id: 'shaderremote' }, h('h2', { text: 'Control from a MIDI controller or a lighting desk' }),
        h('p', { class: 'hint', text: 'A knob or pad on a MIDI controller can start and stop Vibes, go to the next one and set how long each stays. A lighting desk does the same on its ninth channel.' }),
        h('div', { class: 'row wrap' }, links.map(function (l) {
          return h('button', { class: 'btn grow', id: 'shaderto-' + l[0], text: l[1], onclick: function () { c.openSys(l[0]); } });
        })));
    }

    function draw(d) {
      drawn = shape(d);
      root.textContent = '';
      [nowCard(d), listCard(d), controlsCard(d), settingsCard(d), remoteCard()].forEach(function (card) { if (card) root.appendChild(card); });
      watch();
    }
    refresh();
    return root;
  }

  window.pvjShaders = { liveRow: liveRow, patch: patch, page: page, nice: nice };
})();
