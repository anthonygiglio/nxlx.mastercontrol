// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// Shaders and Vibes: the panel's part. Loaded before app.js, which hands over its helpers (h, api, act, can, say,
// poll, moduleOn, state, confirmRow, rowShown, switchFeature, openShaders) each time it draws; nothing here runs until
// app.js calls it. Two things are drawn here: the big Vibes button on Live, and the Shaders page (System > Shaders
// and Vibes, also opened from Live), which holds everything else.
(function () {
  'use strict';

  // Survives redraws, never saved: slider positions per shader, whether Advanced is open, the last upload's answer,
  // and which setting just said "Saved".
  // the library's filter, and the MIDI action being taught
  var ui = { values: {}, advanced: false, upload: null, saved: '', filter: '', cost: 'all', teach: '', taught: '' };
  var timer = null, savedTimer = null, ticker = null, learnTimer = null;
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
      c.h('button', { class: 'btn big', id: 'vibesskip', text: 'Next one', hidden: !pl.vibes, disabled: !c.can('live'),
        onclick: function () { c.act('POST', '/api/vibes', { next: true }, function () { c.say(''); setTimeout(c.poll, 1500); }); } }),
      c.h('button', { class: 'btn big', id: 'shaderslink', text: 'Shaders ›', 'aria-label': 'Shaders: the list, sliders and Vibes settings', onclick: c.openShaders }));
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
    var n = document.getElementById('vibesskip');
    if (n) n.hidden = !pl.vibes;
  }

  function stateLine(d) {
    var v = d.vibes || {};
    if (v.running) return 'Vibes is playing.' + (typeof v.next_in === 'number' && d.playing ? ' ' + left(v.next_in) + ' left, then the next one.' : ' Starting.');
    return d.playing ? 'One shader, until something else plays.' : 'Nothing from here is on the screen.';
  }
  // The page. On a phone: now, the library, the playing shader's controls, Vibes settings, controllers. From 900 px
  // it is a workspace: the library is a column of its own that scrolls by itself, so what is playing and its
  // controls stay in view. Each card is drawn by itself; a change redraws only the cards it touches.
  function page(c) {
    var h = c.h, full = c.can('full'), live = c.can('live');
    var slots = {};
    ['now', 'lib', 'ctl', 'set', 'remote'].forEach(function (k) { slots[k] = h('div', { class: 'shaderslot slot-' + k, hidden: true }); });
    var root = h('div', { class: 'shaderpage', id: 'shaderpage' }, h('div', { class: 'card', id: 'shaderloading' }, h('div', { class: 'hint', text: 'Loading...' })),
      slots.now, slots.lib, slots.ctl, slots.set, slots.remote);
    var data = null, drawn = {};
    clearTimeout(timer); clearInterval(ticker); clearTimeout(learnTimer);

    function put(k, card) {
      slots[k].textContent = '';
      slots[k].hidden = !card;
      if (card) slots[k].appendChild(card);
    }
    function send(path, payload, done) {
      return c.act('POST', path, payload, function (d) { c.say(''); if (d && d.shaders) draw(d); if (done) done(d); c.poll(); });
    }
    function refresh() {
      c.api('GET', '/api/shaders').then(function (r) {
        if (!root.isConnected) return;
        if (!r.ok) { var l = document.getElementById('shaderloading'); if (l) { l.textContent = ''; l.appendChild(h('div', { class: 'msg err', id: 'shadermsg', text: r.data.error || 'Not available' })); } return; }
        draw(r.data);
      });
    }
    function busy() {             // a finger on a slider or a select, or a question waiting for its answer
      var a = document.activeElement;
      return !!(a && root.contains(a) && (a.type === 'range' || a.tagName === 'SELECT')) || !!root.querySelector('#confirmrow');
    }
    function watch() {
      clearTimeout(timer);
      timer = setTimeout(function () {
        if (!root.isConnected) return;
        c.api('GET', '/api/shaders').then(function (r) {
          if (!root.isConnected) return;
          if (r.ok && !busy()) draw(r.data);
          watch();
        });
      }, 5000);
    }
    // The countdown runs here between two answers from the box, so the bar moves every second without asking.
    function tick() {
      if (!root.isConnected) return clearInterval(ticker);
      var v = data && data.vibes;
      if (!v || !v.running || typeof v.next_in !== 'number') return;
      v.next_in = Math.max(0, v.next_in - 1);
      countdown();
    }
    function countdown() {
      var line = document.getElementById('shaderline'), bar = document.getElementById('vibesbar');
      if (line) line.textContent = stateLine(data);
      var v = data.vibes || {};
      if (bar && typeof v.next_in === 'number' && data.config.dwell > 0) bar.style.width = Math.max(0, Math.min(100, 100 - 100 * v.next_in / data.config.dwell)) + '%';
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
    function rotation(d) { return d.shaders.filter(function (s) { return s.vibes && !s.error; }).length; }

    // 1. Now: large and plain, with the time left as a bar
    function nowCard(d) {
      var v = d.vibes || {}, running = !!v.running, n = rotation(d);
      var card = h('div', { class: 'card shaderstage', id: 'shadernow' }, h('h2', { text: 'On screen now' }),
        h('div', { class: 'row wrap' }, h('span', { class: 'shadername', id: 'shaderplaying', text: d.playing ? nice(d.playing.name) : 'No shader on screen' }),
          d.playing ? h('span', { class: 'chip chip-active', text: running ? 'Vibes' : 'Playing' }) : null),
        h('div', { class: 'stageline', id: 'shaderline', role: 'status', text: stateLine(d) }));
      if (running) card.appendChild(h('div', { class: 'progress', id: 'vibesprogress', 'aria-hidden': 'true' }, h('div', { id: 'vibesbar' })));
      if (d.playing && (!d.playing.checked || d.playing.pass_ms)) {
        card.appendChild(h('div', { class: 'hint', text: [d.playing.checked ? '' : 'Not confirmed by the GPU yet.', d.playing.pass_ms ? d.playing.pass_ms + ' ms a frame.' : ''].filter(Boolean).join(' ') }));
      }
      if (!running && v.last && v.last.message && v.last.message !== 'stopped') {
        card.appendChild(h('div', { class: 'hint', id: 'vibeslast', text: 'Vibes ' + v.last.message + ' (' + v.last.at + ').' }));
      }
      if (d.error) card.appendChild(h('div', { class: 'msg err', id: 'shadererror', text: 'The GPU refused ' + nice(d.error.id) + ': ' + d.error.message }));
      if (!live) return card;
      card.appendChild(h('div', { class: 'row wrap' },
        h('button', { class: 'btn big grow' + (running ? '' : ' on'), id: 'vibesbtn', disabled: !running && !n,
          text: running ? 'Stop Vibes' : 'Start Vibes', onclick: function () { send('/api/vibes', { on: !running }, function () { setTimeout(refresh, 1500); }); } }),
        running ? h('button', { class: 'btn big', id: 'vibesnext', text: 'Next one',
          onclick: function () { send('/api/vibes', { next: true }, function () { setTimeout(refresh, 2500); }); } }) : null));
      if (!n) {
        card.appendChild(h('div', { class: 'hint', id: 'norotation', text: 'Nothing is in the Vibes rotation. ' +
          (full ? 'Switch on "In the Vibes rotation" for at least one shader in the list.' : 'Someone with full access chooses which shaders are in it.') }));
      }
      return card;
    }

    // 2. The library: a filter by name and by work, and one compact row per shader. It will hold dozens, so a row
    // that changes (its rotation switch) is patched in place, and the filter only hides rows.
    function weight(s) { var w = work(s.cost); return /^Light/.test(w) ? 'light' : /^Medium work/.test(w) ? 'medium' : /heavy/i.test(w) ? 'heavy' : 'other'; }
    function applyFilter() {
      var q = ui.filter.trim().toLowerCase(), shown = 0;
      Array.prototype.forEach.call(root.querySelectorAll('.shader-entry'), function (row) {
        var hit = (!q || row.getAttribute('data-name').indexOf(q) >= 0) && (ui.cost === 'all' || row.getAttribute('data-work') === ui.cost);
        row.hidden = !hit;
        if (hit) shown++;
      });
      var none = document.getElementById('shadernone');
      if (none) { none.hidden = shown > 0; none.textContent = q ? 'No shader has "' + ui.filter.trim() + '" in its name' + (ui.cost === 'all' ? '.' : ' at that amount of work.') : 'No shader with that amount of work.'; }
    }
    function entry(d, s) {
      var on = !!(d.playing && d.playing.id === s.id), name = nice(s.name);
      var facts = [work(s.cost), s.source === 'uploaded' ? 'your upload' : '', full ? '' : (s.vibes ? 'in the Vibes rotation' : 'not in the rotation')].filter(Boolean).join(' · ');
      // Somebody else's work says so: the pack it came with and the author's own credit line (plain text, as written
      // in the file). The project's own ten need neither.
      var third = s.pack && s.pack !== 'nxlx' && s.pack !== 'uploads';
      var from = third ? 'From the ' + s.pack + ' pack. Credit: ' + (s.credit || 'none given in the file')
        : (s.source === 'uploaded' && s.credit ? 'Credit: ' + s.credit : '');
      var row = h('div', { class: 'item shader-entry' + (on ? ' on' : ''), 'data-shader': s.id, 'data-pack': s.pack || '', 'data-name': name.toLowerCase(), 'data-work': weight(s) },
        h('span', {}, h('b', { text: name }), on ? ' ' : null, on ? h('span', { class: 'chip chip-active', text: 'Playing' }) : null,
          s.description ? h('br') : null, s.description ? h('span', { class: 'hint', text: s.description }) : null,
          facts ? h('br') : null, facts ? h('span', { class: 'hint shaderfacts', text: facts }) : null,
          from ? h('br') : null, from ? h('span', { class: 'hint shadercredit', text: from }) : null));
      if (s.error) row.appendChild(h('div', { class: 'msg err shaderproblem', text: 'Cannot be shown: ' + s.error }));
      else if (d.error && d.error.id === s.id) row.appendChild(h('div', { class: 'msg err shaderproblem', text: 'The GPU refused it: ' + d.error.message }));
      if (!live) return row;
      var sw = full ? h('button', { class: 'switch', role: 'switch', 'aria-checked': s.vibes ? 'true' : 'false', 'aria-label': name + ' in the Vibes rotation', disabled: !!s.error,
        onclick: function () {
          var want = sw.getAttribute('aria-checked') !== 'true';
          c.api('POST', '/api/shaders', { action: 'vibes', id: s.id, on: want }).then(function (r) {
            if (!r.ok) return c.say(r.data.error || 'Something went wrong', true);
            c.say('');
            sw.setAttribute('aria-checked', want ? 'true' : 'false');       // only this row and the Now card change
            data = r.data; drawn.lib = libShape(data); drawn.now = '';
            draw(data);
          });
        } }) : null;
      row.appendChild(h('div', { class: 'row wrap shaderacts' },
        h('button', { class: 'btn on', text: 'Play', 'aria-label': 'Play ' + name, disabled: !!s.error,
          onclick: function () { send('/api/shaders/play', { id: s.id, values: ui.values[s.id] || {} }); } }),
        full ? h('label', { class: 'rot' }, h('span', { text: 'In the Vibes rotation' }), sw) : null,
        full && s.source === 'uploaded' ? h('button', { class: 'btn', text: 'Remove', 'aria-label': 'Remove ' + name, onclick: function (e) {
          c.confirmRow('Remove ' + name + '? The file is deleted from the box.', 'Remove', 'Keep it', function () { send('/api/shaders', { action: 'delete', id: s.id }); }, e.target);
        } }) : null));
      return row;
    }
    function libShape(d) { return JSON.stringify([d.playing && d.playing.id, d.error, d.shaders.map(function (s) { return [s.id, s.vibes, s.error, s.cost]; })]); }
    function listCard(d) {
      var find = h('input', { class: 'text-input', id: 'shaderfilter', type: 'search', placeholder: 'Find a shader', 'aria-label': 'Find a shader by name', value: ui.filter, autocomplete: 'off' });
      find.addEventListener('input', function () { ui.filter = find.value; applyFilter(); });
      var cost = h('select', { class: 'text-input', id: 'shadercost', 'aria-label': 'Show by how much work it is' },
        [['all', 'Any work'], ['light', 'Light work'], ['medium', 'Medium work'], ['heavy', 'Heavy work']].map(function (o) { return h('option', { value: o[0], text: o[1], selected: o[0] === ui.cost }); }));
      cost.addEventListener('change', function () { ui.cost = cost.value; applyFilter(); });
      return h('div', { class: 'card', id: 'shadercard' }, h('h2', { text: 'Shaders (' + d.shaders.length + ')' }),
        live ? h('p', { class: 'hint', text: 'Play shows one shader until something else plays. Vibes goes through the ones in its rotation.' }) : null,
        h('div', { class: 'shaderfind' }, find, cost),
        h('div', { class: 'list shaderlist', id: 'shaderlist' }, d.shaders.map(function (s) { return entry(d, s); }),
          h('div', { class: 'hint', id: 'shadernone', hidden: true })));
    }

    // 3. Controls for the shader that is playing. One function draws an input by its type: today the box takes number
    // inputs (float) as sliders; a switch for bool, a choice for long, a colour and an XY pad get their case here when
    // the box can take them. Presets for the playing shader belong at the top of this card.
    function inputControl(i, now, set) {
      var label = i.label || i.name;
      switch (i.type) {
        case 'float': {
          if (!(i.max > i.min)) return null;
          var out = h('span', { class: 'slidervalue', text: round(now) });
          var input = h('input', { type: 'range', id: 'shin-' + i.name, min: i.min, max: i.max, step: (i.max - i.min) / 100, value: now });
          input.addEventListener('input', function () { out.textContent = round(+input.value); });
          // sent when the finger lifts: each change gives the GPU a new shader to take
          input.addEventListener('change', function () { set(+input.value); });
          input.addEventListener('dblclick', function () { set(i['default']); });         // a double tap puts it back
          return h('div', { class: 'slider shaderslider' }, h('label', { for: 'shin-' + i.name }, label, out), input,
            h('div', { class: 'row between' }, h('span', { class: 'hint', text: round(i.min) + ' to ' + round(i.max) + ', normally ' + round(i['default']) }),
              h('button', { class: 'btn', text: 'Reset', 'aria-label': 'Reset ' + label, onclick: function () { set(i['default']); } })));
        }
        default:
          return null;
      }
    }
    function controlsCard(d) {
      if (!d.playing || !live) return null;
      var s = d.shaders.filter(function (x) { return x.id === d.playing.id; })[0];
      if (!s) return null;
      var card = h('div', { class: 'card', id: 'shadercontrols' }, h('h2', { text: 'Controls for ' + nice(s.name) }));
      var vals = ui.values[s.id] = ui.values[s.id] || {};
      function current(i) {
        return typeof vals[i.name] === 'number' ? vals[i.name] : (typeof d.playing.values[i.name] === 'number' ? d.playing.values[i.name] : i['default']);
      }
      var shown = [];
      // Every shown control's value goes along, so the ones not touched stay where they are on screen.
      function apply() {
        var all = {};
        shown.forEach(function (i) { all[i.name] = current(i); });
        send('/api/shaders/play', { id: s.id, values: all });
      }
      var kids = [];
      s.inputs.forEach(function (i) {
        var el = inputControl(i, current(i), function (v) { vals[i.name] = v; apply(); });
        if (el) { shown.push(i); kids.push(el); }
      });
      if (!kids.length) { card.appendChild(h('div', { class: 'hint', text: 'This shader has nothing to adjust.' })); return card; }
      card.appendChild(h('div', { class: 'list', id: 'shadersliders' }, kids));
      card.appendChild(h('div', { class: 'row' }, h('button', { class: 'btn grow', id: 'shaderresetall', text: 'Reset all', onclick: function () {
        shown.forEach(function (i) { vals[i.name] = i['default']; });
        apply();
      } })));
      card.appendChild(h('p', { class: 'hint', id: 'slidernote', text: 'The picture changes when you let go of a slider; a double tap on a slider puts it back. Slider values are not saved: next time the shader starts from its own values. ' +
        'Changing one keeps this shader on the screen, so Vibes stops going to the next one.' }));
      return card;
    }

    // 4. Vibes settings (full access). Named rotation sets belong at the top of this card when the box has them.
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
            uploaded(file.name + ' was added. It is in the list of shaders.', false);
            drawn.set = '';
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

    // 5. A MIDI controller and a lighting desk, set up here: nobody is sent to another page for it. Drawn by itself
    // and kept apart from the shader cards, so a refresh of those never interrupts teaching a control.
    var TEACH = [['vibes', 'Start and stop Vibes', 'a pad or a button'], ['vibes_next', 'Next one', 'a pad or a button'], ['vibes_dwell', 'How long each one stays', 'a knob or a fader']];
    function remoteCard() {
      if (!full || !(c.rowShown('midi') || c.rowShown('dmx'))) return null;
      var midi = h('div', { class: 'list', id: 'shadermidi' }), dmx = h('div', { class: 'list', id: 'shaderdmx' });
      function featureSwitch(id, label, on, redraw) {
        var sw = h('button', { class: 'switch', id: 'shader' + id + 'sw', role: 'switch', 'aria-checked': on ? 'true' : 'false', 'aria-label': label,
          onclick: function () {
            c.switchFeature(id, !on).then(function (r) {
              if (!r.ok) c.say(r.data.error || 'Could not switch it ' + (on ? 'off' : 'on') + '.', true); else c.say('');
              if (root.isConnected) redraw();
            });
          } });
        return h('label', { class: 'rot between' }, h('b', { text: label }), h('span', { class: 'row' }, h('span', { class: 'switchlabel', text: on ? 'On' : 'Off' }), sw));
      }
      function what(e) { return (e.source === '*' ? 'any controller' : e.source) + ', ' + (e.kind === 'note' ? 'note ' : e.kind === 'cc' ? 'control ' : 'program ') + e.number; }
      function drawMidi(d) {
        clearTimeout(learnTimer);
        midi.textContent = '';
        var on = !!(d && d.enabled);
        midi.appendChild(featureSwitch('midi', 'MIDI controller', on, loadMidi));
        if (!on) { midi.appendChild(h('p', { class: 'hint', text: 'Off. Switch it on to teach a knob or a pad to run Vibes.' })); return; }
        midi.appendChild(h('p', { class: 'hint', id: 'shadermidiline', text: d.devices.length ? 'Plugged in: ' + d.devices.map(function (x) { return x.name; }).join(', ') + '.' : 'No controller is plugged in yet.' }));
        TEACH.forEach(function (t) {
          var mine = d.map.filter(function (e) { return e.action === t[0]; });
          var learning = d.learn.active && ui.teach === t[0];
          var box = h('div', { class: 'item teach', 'data-teach': t[0] }, h('span', {}, h('b', { text: t[1] }), h('br'), h('span', { class: 'hint', text: mine.length ? 'Now on: ' + mine.map(what).join('; ') : 'Not on any control yet (' + t[2] + ').' })));
          if (learning) {
            box.appendChild(h('div', { class: 'msg', id: 'shaderlearning', role: 'status', text: 'Move or press the control now (' + d.learn.seconds_left + ' s)...' }));
            box.appendChild(h('div', { class: 'row' }, h('button', { class: 'btn', id: 'shaderteachcancel', text: 'Cancel', onclick: function () {
              clearTimeout(learnTimer); ui.teach = '';
              c.act('POST', '/api/midi/learn', { start: false }, drawMidi);
            } })));
          } else {
            box.appendChild(h('div', { class: 'row wrap' },
              h('button', { class: 'btn', text: 'Teach a control', 'aria-label': 'Teach a control: ' + t[1], disabled: d.learn.active, onclick: function () {
                ui.teach = t[0]; ui.taught = '';
                c.act('POST', '/api/midi/learn', { start: true }, drawMidi);
              } }),
              mine.map(function (e) {
                return h('button', { class: 'btn', text: 'Remove', 'aria-label': 'Remove ' + what(e) + ' from ' + t[1], onclick: function () { c.act('POST', '/api/midi/map', { remove: e.id }, drawMidi); } });
              })));
          }
          if (ui.taught && ui.teach === t[0] && !learning) box.appendChild(h('div', { class: 'msg', id: 'shadertaught', role: 'status', text: ui.taught }));
          midi.appendChild(box);
        });
        if (d.learn.active && ui.teach) pollLearn();
      }
      function pollLearn() {
        clearTimeout(learnTimer);
        learnTimer = setTimeout(function () {
          if (!root.isConnected) return;
          c.api('GET', '/api/midi').then(function (r) {
            if (!r.ok || !root.isConnected) return;
            var l = r.data.learn;
            if (l.captured) {
              var entry = { source: l.captured.source, kind: l.captured.kind, channel: 0, number: l.captured.number, action: ui.teach };
              return c.act('POST', '/api/midi/map', { add: entry }, function (d) { ui.taught = 'Learned: ' + what(entry) + '.'; drawMidi(d); });
            }
            if (l.active) {     // only the countdown changes
              var el = document.getElementById('shaderlearning');
              if (el) el.textContent = 'Move or press the control now (' + l.seconds_left + ' s)...';
              return pollLearn();
            }
            ui.taught = 'Nothing was moved or pressed. Try again.';
            drawMidi(r.data);
          });
        }, 600);
      }
      function loadMidi() {
        if (!c.rowShown('midi')) return;
        if (!c.moduleOn('control-midi')) return drawMidi(null);       // the box refuses the question while the module is off
        c.api('GET', '/api/midi').then(function (r) { if (root.isConnected) drawMidi(r.ok ? r.data : null); });
      }
      function drawDmx(d) {
        dmx.textContent = '';
        var on = !!(d && d.enabled);
        dmx.appendChild(featureSwitch('dmx', 'DMX lighting desk', on, loadDmx));
        if (!on) { dmx.appendChild(h('p', { class: 'hint', text: 'Off. Switch it on to run Vibes from a lighting desk.' })); return; }
        var ch = d.start + 8, level = d.channels && typeof d.channels[8] === 'number' ? d.channels[8] : null;
        dmx.appendChild(h('p', { class: 'hint', id: 'shaderdmxline', text: (d.error ? 'Problem: ' + d.error + '. ' : d.listening ? 'Listening, universe ' + d.universe + '. ' : 'Not listening. ') +
          'Vibes is on channel ' + ch + ' (the ninth from start channel ' + d.start + ').' }));
        dmx.appendChild(h('div', { class: 'dmxzones', id: 'shaderdmxzones' }, [['50 to 99', 'Stop'], ['100 to 149', 'Start'], ['150 to 199', 'Next one']].map(function (z) {
          return h('span', { class: 'chip' }, z[1] + ': ' + z[0]);
        })));
        dmx.appendChild(h('p', { class: 'hint', id: 'shaderdmxlevel', text: level === null ? 'Level now: nothing received on that channel yet.' : 'Level now: ' + level + '.' }));
      }
      function loadDmx() {
        if (!c.rowShown('dmx')) return;
        if (!c.moduleOn('control-dmx')) return drawDmx(null);
        c.api('GET', '/api/dmx').then(function (r) { if (root.isConnected) drawDmx(r.ok ? r.data : null); });
      }
      loadMidi(); loadDmx();
      return h('div', { class: 'card', id: 'shaderremote' }, h('h2', { text: 'Run Vibes from a controller' }),
        c.rowShown('midi') ? midi : null, c.rowShown('dmx') ? dmx : null);
    }

    // Each card has its own shape; a card is drawn again only when its shape changed.
    function draw(d) {
      data = d;
      var first = document.getElementById('shaderloading');
      if (first) first.parentNode.removeChild(first);
      var shapes = {
        now: JSON.stringify([d.playing && [d.playing.id, d.playing.checked, d.playing.pass_ms], !!(d.vibes && d.vibes.running), d.vibes && d.vibes.last, d.error, rotation(d)]),
        lib: libShape(d),
        ctl: JSON.stringify([d.playing && [d.playing.id, d.playing.values]]),
        set: JSON.stringify([d.config, d.render, ui.saved])
      };
      if (shapes.now !== drawn.now) put('now', nowCard(d));
      if (shapes.lib !== drawn.lib) { put('lib', listCard(d)); applyFilter(); }
      if (shapes.ctl !== drawn.ctl) put('ctl', controlsCard(d));
      if (shapes.set !== drawn.set) put('set', settingsCard(d));
      if (!drawn.remote) { put('remote', remoteCard()); shapes.remote = '1'; } else shapes.remote = '1';
      drawn = shapes;
      countdown();
      watch();
    }
    ticker = setInterval(tick, 1000);
    refresh();
    return root;
  }

  window.pvjShaders = { liveRow: liveRow, patch: patch, page: page, nice: nice };
})();
