// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// Shaders and Vibes: the panel's part. Loaded before app.js, which hands over its helpers (h, api, act, can, say,
// poll, moduleOn, state, confirmRow, rowShown, switchFeature, openShaders) each time it draws; nothing here runs until
// app.js calls it. Three things are drawn here: the Vibes row on Live, the strip of the playing shader's controls on
// Live, and the Shaders page (System > Shaders and Vibes, also opened from Live), which is the instrument: what is
// playing and its load, its controls by type, presets, the library, the rotation sets and the controllers.
(function () {
  'use strict';

  // Survives redraws, never saved: the library's filters, which set is being edited and which one Start uses, the
  // preset last applied per page (to say "Changed"), the speed Freeze goes back to, the MIDI action being taught.
  var ui = { advanced: false, upload: null, saved: '', filter: '', cost: 'all', pack: 'all', family: 'all', editSet: null, startSet: null,
    lastPreset: null, lastSpeed: 1, teach: '', taught: '', open: '', more: false };
  var timer = null, savedTimer = null, ticker = null, learnTimer = null, soonTimer = null;
  var keys = null;              // the open Shaders page's shortcuts (see the listener at the end)
  var DWELLS = [30, 60, 120, 180, 300, 600, 900, 1800, 3600];
  var SEND_GAP = 100;           // a dragged control sends at most ten times a second; the box compiles five a second
  var SETTLE = 1200;            // after a send, the box's answer may still be the value before: leave the control alone
  var ORDINAL = ['first', 'second', 'third', 'fourth', 'fifth', 'sixth', 'seventh', 'eighth'];
  var BOARDS = { pi4: 'a Pi 4', pi5: 'a Pi 5', x86: 'a PC' };
  var WORK = { light: 'Light work', medium: 'Medium work', heavy: 'Heavy work' };
  var FAMILIES = ['Ambient', 'Performance'];
  var LOAD = { ok: 'Running smoothly', tight: 'Close to the limit', heavy: 'Dropping frames: try a lower picture detail' };

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
  function round(v) { return String(Math.round(v * 100) / 100); }
  function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }
  function hex(rgb) {
    return '#' + [0, 1, 2].map(function (n) { var x = Math.round(clamp(+rgb[n] || 0, 0, 1) * 255).toString(16); return x.length < 2 ? '0' + x : x; }).join('');
  }
  function unhex(text) { return [1, 3, 5].map(function (n) { return parseInt(text.slice(n, n + 2), 16) / 255; }); }
  function same(a, b) { return JSON.stringify(a) === JSON.stringify(b); }
  // How much work a shader is, with the numbers measured on a real board where there are any.
  function work(s) {
    var words = WORK[s.weight] || (s.cost ? 'Cost: ' + s.cost : ''), m = s.measured;
    if (!m) return words;
    var ms = typeof m.pass_ms === 'number' ? round(m.pass_ms) : (m.pass_ms_range ? round(m.pass_ms_range[0]) + ' to ' + round(m.pass_ms_range[1]) : '');
    if (!ms) return words;
    return words + '. ' + ms + ' ms on ' + (BOARDS[m.board] || m.board) + (m.lines ? ' at ' + m.lines + ' lines' : '') + (m.stale ? ' (before its look was changed)' : '');
  }
  function family(s) { return FAMILIES.filter(function (f) { return (s.categories || []).some(function (x) { return String(x).toLowerCase() === f.toLowerCase(); }); })[0] || ''; }

  function player(c) { return (c.state.status && c.state.status.player) || {}; }
  function words(pl) { return pl.vibes ? 'Vibes is playing' + (pl.shader ? ': ' + nice(pl.shader) : '') : 'Start Vibes'; }
  function sub(pl) { return pl.vibes ? 'Tap to stop' : 'Moving pictures, one after another'; }
  function activeSet(d) { return d.sets.filter(function (e) { return e.id === d.active; })[0] || d.sets[0] || null; }
  function setById(d, id) { return d.sets.filter(function (e) { return e.id === id; })[0] || null; }
  // The set a Start goes through: the one chosen here, else the usual (active) one. Null means "say nothing about
  // sets": there is only one, or the usual one is meant.
  function startSet(d) {
    var e = ui.startSet && d.sets.length > 1 ? setById(d, ui.startSet) : null;
    return e && e.id !== d.active ? e : null;
  }
  function vibesBody(d, on) { var e = on && d ? startSet(d) : null; return e ? { on: true, set: e.id } : { on: on }; }
  // The inputs a controller's knobs 1 to 8 drive, in the box's own count: numbers, switches, choices and events.
  function knobOf(inputs) {
    var out = {}, n = 0;
    inputs.forEach(function (i) {
      if ((i.type === 'float' && i.max > i.min) || i.type === 'bool' || i.type === 'long' || i.type === 'event') { n++; if (n <= 8) out[i.name] = n; }
    });
    return out;
  }

  // ---- sending values -------------------------------------------------------------------------------------------
  // One rig per place that shows controls (the page, the strip on Live). A control gets a channel: push() while it is
  // dragged (at most ten sends a second, the newest value wins), send() for a tap, touch(false) when the finger lifts
  // (the last value always goes out). free() says whether the box's own value may be put into the control: not while
  // it is touched, not while a send waits or is under way, and not for a moment after one.
  function makeRig(c, shaderId, after) {
    var rig = { last: null, sentAt: 0 };
    rig.channel = function (key, body, note) {
      var st = { touch: false, has: false, value: null, timer: null, last: 0, flying: 0, done: 0 };
      function fire() {
        clearTimeout(st.timer); st.timer = null;
        if (!st.has) return;
        var b = body(st.value);
        st.has = false; st.last = Date.now(); st.flying++;
        b.id = shaderId();
        rig.last = ch; rig.sentAt = st.last;
        note('', false, true);
        c.api('POST', '/api/shaders/values', b).then(function (r) {
          st.flying--; st.done = Date.now();
          if (!r.ok) note(r.status === 409 ? 'Not sent: another shader is on the screen now.' : (r.data.error || 'The box did not take it.'), true);
          else if (!st.flying && !st.has) note('', false, false);
          if (after) after(r);
        });
      }
      var ch = {
        push: function (v) {
          st.value = v; st.has = true;
          var wait = SEND_GAP - (Date.now() - st.last);
          if (wait <= 0) fire(); else if (!st.timer) st.timer = setTimeout(fire, wait);
        },
        send: function (v) { st.value = v; st.has = true; fire(); },
        flush: function () { if (st.has) fire(); },
        touch: function (on) { st.touch = !!on; if (!on) ch.flush(); },
        free: function () { return !st.touch && !st.has && !st.flying && Date.now() - Math.max(st.last, st.done) > SETTLE; },
        note: note
      };
      return ch;
    };
    return rig;
  }
  // The small line under a control: a dot while a value is on its way, the box's words if it refused.
  function noteLine() {
    var el = document.createElement('span');
    el.className = 'ctlnote';
    el.setAttribute('role', 'status');
    return { el: el, say: function (text, err, pending) { el.textContent = pending ? '●' : (text || ''); el.className = 'ctlnote' + (err ? ' err' : '') + (pending ? ' pending' : ''); } };
  }
  // A range input that tells its channel when it is held.
  function slider(attrs, ch, onInput) {
    var el = document.createElement('input');
    el.type = 'range';
    Object.keys(attrs).forEach(function (k) { el.setAttribute(k, attrs[k]); });
    el.addEventListener('pointerdown', function () { ch.touch(true); });
    ['pointerup', 'pointercancel', 'blur'].forEach(function (ev) { el.addEventListener(ev, function () { ch.touch(false); }); });
    el.addEventListener('input', function () { onInput(+el.value, false); });
    el.addEventListener('change', function () { onInput(+el.value, true); ch.touch(false); });
    return el;
  }

  // One input of a shader, drawn by its type. Returns {el, apply(value), reset()}: apply puts the box's value into
  // the control (the caller asks the channel first whether it may), reset sends the file's own value.
  // opt: {h, rig, compact (the strip on Live: no Reset button, no range words), extra (a node for the buttons row,
  // the MIDI teach button), size ({width, height} of the drawing, for a point without a range)}
  function inputControl(i, now, opt) {
    var h = opt.h, label = i.label || i.name, id = (opt.prefix || 'shin-') + i.name;
    var note = noteLine();
    var ch = opt.rig.channel('v:' + i.name, function (v) { var b = { values: {} }; b.values[i.name] = v; return b; }, note.say);
    var out = h('span', { class: 'slidervalue' });
    function head(forId) { return h('div', { class: 'ctlhead' }, h(forId ? 'label' : 'span', { class: 'ctllabel', for: forId || false, text: label }), out, note.el); }
    function resetBtn(fn) { return opt.compact ? null : h('button', { class: 'btn ctlreset', text: 'Reset', 'aria-label': 'Reset ' + label, onclick: fn }); }
    function acts(fn) { var r = resetBtn(fn); return r || opt.extra ? h('div', { class: 'ctlacts' }, opt.extra || null, r) : null; }
    function wrap(kind) { return h('div', { class: 'ctl ctl-' + kind + (opt.compact ? ' compact' : ''), 'data-input': i.name, 'data-type': i.type }); }
    function numberSlider(lo, hi, step, def, text) {
      var cur = now;
      var input = slider({ id: id, min: lo, max: hi, step: step, value: now }, ch, function (v, done) {
        cur = v; out.textContent = text(v);
        if (done) ch.send(v); else ch.push(v);
      });
      out.textContent = text(now);
      function reset() { cur = def; input.value = def; out.textContent = text(def); ch.send(def); }
      input.addEventListener('dblclick', reset);                    // a double tap puts it back
      var box = wrap('slider');
      box.appendChild(head(id));
      box.appendChild(h('div', { class: 'ctlrow' }, input, acts(reset)));
      if (!opt.compact) box.appendChild(h('span', { class: 'hint ctlrange', text: text(lo) + ' to ' + text(hi) + ', normally ' + text(def) }));
      return { el: box, ch: ch, reset: reset, apply: function (v) { if (typeof v === 'number' && v !== cur) { cur = v; input.value = v; out.textContent = text(v); } } };
    }
    switch (i.type) {
      case 'float':
        if (!(i.max > i.min)) return null;
        return numberSlider(i.min, i.max, (i.max - i.min) / 200, i['default'], round);
      case 'bool': {
        var sw = h('button', { class: 'switch', id: id, role: 'switch', 'aria-checked': now ? 'true' : 'false', 'aria-label': label, onclick: function () {
          var want = sw.getAttribute('aria-checked') !== 'true';
          sw.setAttribute('aria-checked', want ? 'true' : 'false');
          ch.send(want);
        } });
        var bb = wrap('bool');
        bb.appendChild(h('div', { class: 'ctlrow' }, h('span', { class: 'ctllabel grow', text: label }), note.el, opt.extra || null, sw));
        return { el: bb, ch: ch, reset: function () { sw.setAttribute('aria-checked', i['default'] ? 'true' : 'false'); ch.send(!!i['default']); },
          apply: function (v) { sw.setAttribute('aria-checked', v ? 'true' : 'false'); } };
      }
      case 'long': {
        if (!i.values) {            // a whole number in a range: a slider in whole steps, or a field when the range is huge
          if (i.max - i.min <= 200) return numberSlider(i.min, i.max, 1, i['default'], function (v) { return String(Math.round(v)); });
          var num = h('input', { class: 'text-input', id: id, type: 'number', min: i.min, max: i.max, step: 1, value: now, 'aria-label': label });
          num.addEventListener('change', function () { if (num.value !== '' && isFinite(+num.value)) ch.send(Math.round(+num.value)); });
          var nb = wrap('number');
          nb.appendChild(head(id));
          nb.appendChild(h('div', { class: 'ctlrow' }, num, acts(function () { num.value = i['default']; ch.send(i['default']); })));
          return { el: nb, ch: ch, reset: function () { num.value = i['default']; ch.send(i['default']); }, apply: function (v) { if (document.activeElement !== num) num.value = v; } };
        }
        var labels = i.labels || i.values.map(String), lb = wrap('choice'), pickEl, show;
        if (i.values.length <= 5) {         // a few choices: all of them in view, one tap each
          var btns = i.values.map(function (v, n) {
            return h('button', { class: 'btn segbtn', role: 'radio', 'data-value': String(v), text: labels[n], onclick: function () { show(v); ch.send(v); } });
          });
          pickEl = h('div', { class: 'seg', id: id, role: 'radiogroup', 'aria-label': label }, btns);
          show = function (v) {
            btns.forEach(function (b, n) { var on = i.values[n] === v; b.setAttribute('aria-checked', on ? 'true' : 'false'); b.className = 'btn segbtn' + (on ? ' on' : ''); });
          };
        } else {
          pickEl = h('select', { class: 'text-input', id: id, 'aria-label': label }, i.values.map(function (v, n) { return h('option', { value: String(v), text: labels[n] }); }));
          pickEl.addEventListener('change', function () { ch.send(+pickEl.value); });
          show = function (v) { if (document.activeElement !== pickEl) pickEl.value = String(v); };
        }
        show(now);
        lb.appendChild(h('div', { class: 'ctlhead' }, h('span', { class: 'ctllabel', text: label }), note.el));
        lb.appendChild(h('div', { class: 'ctlrow' }, pickEl, opt.extra ? h('div', { class: 'ctlacts' }, opt.extra) : null));
        return { el: lb, ch: ch, reset: function () { show(i['default']); ch.send(i['default']); }, apply: show };
      }
      case 'color': {
        var col = (Array.isArray(now) ? now : i['default']).slice();
        var pick = h('input', { type: 'color', class: 'swatchpick', id: id, value: hex(col), 'aria-label': label });
        var alpha = slider({ id: id + '-alpha', min: 0, max: 1, step: 0.01, value: col[3], 'aria-label': label + ': alpha (how solid it is)' }, ch, function (v, done) {
          col[3] = v; paint();
          if (done) ch.send(col.slice()); else ch.push(col.slice());
        });
        var paint = function () { out.textContent = hex(col) + ' · ' + Math.round(col[3] * 100) + '%'; };
        pick.addEventListener('input', function () { col = unhex(pick.value).concat(col[3]); paint(); ch.push(col.slice()); });
        pick.addEventListener('change', function () { col = unhex(pick.value).concat(col[3]); paint(); ch.send(col.slice()); });
        paint();
        var resetCol = function () { col = i['default'].slice(); pick.value = hex(col); alpha.value = col[3]; paint(); ch.send(col.slice()); };
        var cb = wrap('color');
        cb.appendChild(head(id));
        cb.appendChild(h('div', { class: 'ctlrow' }, pick, h('label', { class: 'alphalabel', for: id + '-alpha', text: 'Alpha' }), alpha, acts(resetCol)));
        return { el: cb, ch: ch, reset: resetCol, apply: function (v) {
          if (!Array.isArray(v) || document.activeElement === pick || same(v, col)) return;
          col = v.slice(); pick.value = hex(col); alpha.value = col[3]; paint();
        } };
      }
      case 'point2D': {
        // A point without a range in the file: 0 to 1 when its own value sits in there, else the drawing's pixels.
        var unit = i['default'][0] >= 0 && i['default'][0] <= 1 && i['default'][1] >= 0 && i['default'][1] <= 1;
        var size = opt.size || { width: 1, height: 1 };
        var lo = i.min || [0, 0], hi = i.max || (unit ? [1, 1] : [size.width, size.height]);
        var pt = (Array.isArray(now) ? now : i['default']).slice(), drag = false;
        var dot = h('span', { class: 'xydot' });
        var pad = h('div', { class: 'xypad', id: id, tabindex: '0', role: 'group', 'aria-label': label + ': drag in the square, or use the arrow keys' }, dot);
        var place = function () {
          var fx = hi[0] > lo[0] ? (pt[0] - lo[0]) / (hi[0] - lo[0]) : 0.5, fy = hi[1] > lo[1] ? (pt[1] - lo[1]) / (hi[1] - lo[1]) : 0.5;
          dot.style.left = clamp(fx, 0, 1) * 100 + '%';
          dot.style.top = (1 - clamp(fy, 0, 1)) * 100 + '%';         // up is more, as in the shader
          out.textContent = 'x ' + round(pt[0]) + '  y ' + round(pt[1]);
        };
        var at = function (e) {
          var r = pad.getBoundingClientRect();
          pt = [lo[0] + clamp((e.clientX - r.left) / r.width, 0, 1) * (hi[0] - lo[0]), lo[1] + clamp(1 - (e.clientY - r.top) / r.height, 0, 1) * (hi[1] - lo[1])];
          place(); ch.push(pt.slice());
        };
        pad.addEventListener('pointerdown', function (e) {
          drag = true; ch.touch(true);
          try { pad.setPointerCapture(e.pointerId); } catch (x) { /* a pointer made by a test has nothing to capture */ }
          at(e); e.preventDefault(); pad.focus();
        });
        pad.addEventListener('pointermove', function (e) { if (drag) at(e); });
        ['pointerup', 'pointercancel'].forEach(function (ev) { pad.addEventListener(ev, function () { if (drag) { drag = false; ch.touch(false); } }); });
        pad.addEventListener('keydown', function (e) {
          var d = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1] }[e.key];
          if (!d) return;
          e.preventDefault(); e.stopPropagation();
          var part = e.shiftKey ? 10 : 50;
          pt = [clamp(pt[0] + d[0] * (hi[0] - lo[0]) / part, lo[0], hi[0]), clamp(pt[1] + d[1] * (hi[1] - lo[1]) / part, lo[1], hi[1])];
          place(); ch.push(pt.slice());
        });
        place();
        var resetPt = function () { pt = i['default'].slice(); place(); ch.send(pt.slice()); };
        var pb = wrap('point');
        pb.appendChild(head(null));
        pb.appendChild(h('div', { class: 'ctlrow xyrow' }, pad, acts(resetPt)));
        return { el: pb, ch: ch, reset: resetPt, apply: function (v) { if (Array.isArray(v) && !drag && document.activeElement !== pad && !same(v, pt)) { pt = v.slice(); place(); } } };
      }
      case 'event': {
        var eb = wrap('event');
        eb.appendChild(h('div', { class: 'ctlrow' },
          h('button', { class: 'btn big grow eventbtn', id: id, text: label, 'aria-label': label + ' (a short press)', onclick: function () { ch.send(true); } }), note.el,
          opt.extra ? h('div', { class: 'ctlacts' }, opt.extra) : null));
        return { el: eb, ch: ch, reset: null, apply: function () {} };
      }
      default:
        return null;
    }
  }
  // The three controls every shader has. Speed carries the Freeze switch: it sets 0 and goes back to the speed before.
  function commonControl(which, spec, now, opt) {
    var h = opt.h, id = (opt.prefix || 'shc-') + which, note = noteLine();
    var label = { speed: 'Speed', hue: 'Colour turn', brightness: 'Brightness trim' }[which];
    var text = { speed: function (v) { return round(v) + '×'; }, hue: function (v) { return (v > 0 ? '+' : '') + Math.round(v) + '°'; },
      brightness: function (v) { return Math.round(v * 100) + '%'; } }[which];
    var ch = opt.rig.channel('c:' + which, function (v) { var b = { controls: {} }; b.controls[which] = v; return b; }, note.say);
    var out = h('span', { class: 'slidervalue', text: text(now) }), cur = now, freeze = null;
    function shown(v) {
      cur = v; out.textContent = text(v);
      if (freeze) freeze.setAttribute('aria-checked', v === 0 ? 'true' : 'false');
    }
    var input = slider({ id: id, min: spec.min, max: spec.max, step: which === 'hue' ? 1 : (spec.max - spec.min) / 200, value: now }, ch, function (v, done) {
      shown(v);
      if (which === 'speed' && v > 0) ui.lastSpeed = v;
      if (done) ch.send(v); else ch.push(v);
    });
    function set(v) { input.value = v; shown(v); ch.send(v); }
    function reset() { if (which === 'speed') ui.lastSpeed = spec['default']; set(spec['default']); }
    input.addEventListener('dblclick', reset);
    var box = h('div', { class: 'ctl ctl-slider ctl-common' + (opt.compact ? ' compact' : ''), 'data-common': which });
    box.appendChild(h('div', { class: 'ctlhead' }, h('label', { class: 'ctllabel', for: id, text: label }), out, note.el));
    var track = h('div', { class: 'track' }, input);
    if (which === 'speed') {              // where 1 is: the shader's own pace
      var mark = h('span', { class: 'trackmark', 'aria-hidden': 'true', text: '1×' });
      mark.style.left = (spec['default'] - spec.min) / (spec.max - spec.min) * 100 + '%';
      track.appendChild(mark);
      freeze = h('button', { class: 'switch', id: id + '-freeze', role: 'switch', 'aria-checked': now === 0 ? 'true' : 'false', 'aria-label': 'Freeze the shader', onclick: function () {
        if (cur > 0) { ui.lastSpeed = cur; set(0); } else set(ui.lastSpeed > 0 ? ui.lastSpeed : spec['default']);
      } });
    }
    box.appendChild(h('div', { class: 'ctlrow' }, track,
      freeze ? h('label', { class: 'rot freezelabel' }, h('span', { text: 'Freeze' }), freeze) : null,
      h('div', { class: 'ctlacts' }, opt.extra || null,
        opt.compact ? null : h('button', { class: 'btn ctlreset', text: 'Reset', 'aria-label': 'Reset ' + label, onclick: reset }))));
    return { el: box, ch: ch, reset: reset, apply: function (v) { if (typeof v === 'number' && v !== cur) { input.value = v; shown(v); if (which === 'speed' && v > 0) ui.lastSpeed = v; } } };
  }
  // Put the box's values into the controls that are free to take them.
  function syncControls(ctls, playing) {
    if (!playing || playing.pending) return;
    ctls.forEach(function (x) {
      if (!x.ctl.ch.free()) return;
      var v = x.common ? (playing.controls || {})[x.common] : (playing.values || {})[x.name];
      if (v !== undefined) x.ctl.apply(v);
    });
  }
  function inputShape(s) { return s.inputs.map(function (i) { return [i.name, i.type, i.min, i.max, i.values, i.labels, i['default']]; }); }

  // ---- Live -----------------------------------------------------------------------------------------------------
  // The big button starts and stops Vibes and says what is playing; beside it the shader before and the next one
  // while a shader is on, the set to play when there is more than one, and the way to the page. Presenters (live
  // access) and above use it; everyone sees whether it is on.
  var L = { data: null, shader: null, at: 0, busy: false, shape: '', ctls: [], rig: null };
  function liveRow(c) {
    if (!c.moduleOn('shaders')) return null;
    var pl = player(c), on = typeof pl.shader === 'string', can = c.can('live');
    function step(dir) {
      return function () { c.act('POST', '/api/shaders/step', { dir: dir }, function () { c.say(''); L.at = 0; setTimeout(c.poll, 900); }); };
    }
    var pick = can ? c.h('select', { class: 'text-input', id: 'liveset', hidden: true, 'aria-label': 'The set of shaders Vibes plays' }) : null;
    if (pick) pick.addEventListener('change', function () {
      ui.startSet = pick.value; pick.blur();
      if (player(c).vibes && L.data) c.act('POST', '/api/vibes', vibesBody(L.data, true), function () { c.say(''); setTimeout(c.poll, 1200); });
    });
    L.shader = null; L.at = 0; L.shape = '';
    return c.h('div', { class: 'vibesrow', id: 'vibesrow' },
      c.h('button', { class: 'btn big vibesbig' + (pl.vibes ? ' on' : ''), id: 'vibes', 'aria-pressed': pl.vibes ? 'true' : 'false', disabled: !can,
        onclick: function () {
          c.act('POST', '/api/vibes', vibesBody(L.data, !player(c).vibes), function () { c.say(''); setTimeout(c.poll, 1200); });
        } },
        c.h('span', { id: 'vibeswords', text: words(pl) }), c.h('span', { class: 'vibessub', id: 'vibessub', text: sub(pl) })),
      can ? c.h('button', { class: 'btn big', id: 'liveprev', text: '‹ Previous', 'aria-label': 'The shader before', hidden: !on, onclick: step(-1) }) : null,
      can ? c.h('button', { class: 'btn big', id: 'vibesskip', text: 'Next ›', 'aria-label': 'The next shader', hidden: !on, onclick: step(1) }) : null,
      pick,
      c.h('button', { class: 'btn big', id: 'shaderslink', text: 'Shaders ›', 'aria-label': 'Shaders: the list, controls, presets and Vibes settings', onclick: c.openShaders }));
  }
  // The strip: Speed and the first four controls of the shader that is on, so a performer need not leave Live.
  function liveStrip(c) {
    if (!c.moduleOn('shaders') || !c.can('live')) return null;
    return c.h('div', { class: 'card liveside', id: 'liveshader', hidden: true });
  }
  function drawLive(c, d, box) {
    var pick = document.getElementById('liveset');
    if (pick) {
      var shape = JSON.stringify(d.sets.map(function (e) { return [e.id, e.name]; }));
      if (pick.getAttribute('data-shape') !== shape && document.activeElement !== pick) {
        pick.textContent = '';
        d.sets.forEach(function (e) { pick.appendChild(c.h('option', { value: e.id, text: 'Set: ' + e.name })); });
        pick.setAttribute('data-shape', shape);
      }
      pick.hidden = d.sets.length < 2;
      if (document.activeElement !== pick) pick.value = (d.vibes.running && d.vibes.set && d.vibes.set.id) || (startSet(d) || activeSet(d) || {}).id || '';
    }
    var s = d.playing && d.shaders.filter(function (x) { return x.id === d.playing.id; })[0];
    box.hidden = !s;
    if (box.parentNode) box.parentNode.classList.toggle('has-side', !!s);
    if (!s) { L.shape = ''; L.ctls = []; return; }
    var shape2 = JSON.stringify([s.id, inputShape(s)]);
    if (shape2 !== L.shape) {
      L.shape = shape2;
      L.rig = makeRig(c, function () { return L.data && L.data.playing ? L.data.playing.id : null; }, function () { L.at = Date.now() - 3000; });
      L.ctls = [];
      box.textContent = '';
      box.appendChild(c.h('div', { class: 'row between' }, c.h('h2', { id: 'livename', text: nice(s.name) }),
        c.h('button', { class: 'btn', id: 'livemore', text: 'All controls ›', onclick: c.openShaders })));
      var list = c.h('div', { class: 'ctls', id: 'livectls' });
      var speed = commonControl('speed', d.controls.speed, d.playing.controls.speed, { h: c.h, rig: L.rig, compact: true, prefix: 'live-' });
      L.ctls.push({ common: 'speed', ctl: speed });
      list.appendChild(speed.el);
      s.inputs.slice(0, 4).forEach(function (i) {
        var v = d.playing.values[i.name];
        var ctl = inputControl(i, v === undefined ? i['default'] : v, { h: c.h, rig: L.rig, compact: true, prefix: 'live-', size: d.render });
        if (ctl) { L.ctls.push({ name: i.name, ctl: ctl }); list.appendChild(ctl.el); }
      });
      box.appendChild(list);
      return;
    }
    syncControls(L.ctls, d.playing);
  }
  // Called with every status poll: the Now playing line, the button and the strip follow the player.
  function patch(c, pl, np) {
    if (np && typeof pl.shader === 'string') np.textContent = (pl.vibes ? 'Vibes: ' : 'Shader: ') + (nice(pl.shader) || 'starting');
    var b = document.getElementById('vibes'), w = document.getElementById('vibeswords'), s = document.getElementById('vibessub');
    if (!b || !w || !s) return;
    w.textContent = words(pl);
    s.textContent = sub(pl);
    b.className = 'btn big vibesbig' + (pl.vibes ? ' on' : '');
    b.setAttribute('aria-pressed', pl.vibes ? 'true' : 'false');
    var on = typeof pl.shader === 'string';
    ['liveprev', 'vibesskip'].forEach(function (id) { var el = document.getElementById(id); if (el) el.hidden = !on; });
    var box = document.getElementById('liveshader');
    if (!box || L.busy) return;
    // The strip and the set chooser need the shader list: asked for when the shader changes, and every few seconds
    // while one is on (its values may be moved from elsewhere).
    var name = on ? pl.shader : null;
    if (name === L.shader && (Date.now() - L.at < (on ? 4000 : 20000))) return;
    L.busy = true;
    c.api('GET', '/api/shaders').then(function (r) {
      L.busy = false; L.at = Date.now(); L.shader = name;
      if (!r.ok || !box.isConnected) return;
      L.data = r.data;
      drawLive(c, r.data, box);
    });
  }

  function stateLine(d) {
    var v = d.vibes || {};
    if (v.running) {
      var of = v.set && d.sets.length > 1 ? ' the set ' + v.set.name : '';
      return 'Vibes is playing' + of + '.' + (typeof v.next_in === 'number' && d.playing ? ' ' + left(v.next_in) + ' left, then the next one.' : ' Starting.');
    }
    return d.playing ? 'One shader, until something else plays.' : 'Nothing from here is on the screen.';
  }

  // ---- the page ---------------------------------------------------------------------------------------------------
  // On a phone, top to bottom: now (with its load), controls, presets, the library, Vibes and its sets, controllers.
  // From 900 px the library is a column of its own that scrolls by itself, so what is playing and its controls stay
  // in view; from 1200 px the sets and the controllers are a third column. Each card is drawn by itself, and only
  // when its own shape changed; values are put into controls that nobody is holding.
  function page(c) {
    var h = c.h, full = c.can('full'), live = c.can('live');
    var slots = {}, cols = {};
    ['now', 'ctl', 'pre', 'lib', 'set', 'remote'].forEach(function (k) { slots[k] = h('div', { class: 'shaderslot slot-' + k, hidden: true }); });
    cols.stage = h('div', { class: 'shadercol col-stage' }, slots.now, slots.ctl, slots.pre);
    cols.lib = h('div', { class: 'shadercol col-lib' }, slots.lib);
    cols.side = h('div', { class: 'shadercol col-side' }, slots.set, slots.remote);
    var root = h('div', { class: 'shaderpage', id: 'shaderpage' }, h('div', { class: 'card', id: 'shaderloading' }, h('div', { class: 'hint', text: 'Loading...' })),
      cols.stage, cols.lib, cols.side);
    var data = null, drawn = {}, ctls = [], seenError = undefined;
    var midi = { asked: false, data: null }, teachers = [];
    clearTimeout(timer); clearInterval(ticker); clearTimeout(learnTimer); clearTimeout(soonTimer);
    var rig = makeRig(c, function () { return data && data.playing ? data.playing.id : null; }, function () { soon(700); });

    // A card is not drawn again under someone's hands: while a field in it has the cursor, a question waits for its
    // answer, or one of its controls is held. It is drawn at the next answer instead.
    function held(slot) {
      var a = document.activeElement;
      if (a && slot.contains(a) && (a.tagName === 'SELECT' || a.tagName === 'TEXTAREA' || (a.tagName === 'INPUT' && a.type !== 'range') || a.classList.contains('xypad'))) return true;
      if (slot.querySelector('#confirmrow')) return true;
      return slot === slots.ctl && ctls.some(function (x) { return !x.ctl.ch.free(); });
    }
    function put(k, card) {
      slots[k].textContent = '';
      slots[k].hidden = !card;
      if (card) slots[k].appendChild(card);
    }
    function fail(r, msgId) {
      var el = msgId && document.getElementById(msgId), text = r.data.error || 'Something went wrong';
      if (el) { el.textContent = text; el.className = 'msg err'; } else c.say(text, true);
    }
    // One change: the answer is the whole state, or a short one (then the state is asked for a moment later). A
    // refusal is said in the card it belongs to.
    function send(path, payload, done, msgId) {
      return c.api('POST', path, payload).then(function (r) {
        if (!root.isConnected) return r;
        if (!r.ok) { fail(r, msgId); return r; }
        c.say('');
        if (r.data && r.data.shaders) draw(r.data); else soon(600);
        if (done) done(r.data);
        c.poll();
        return r;
      });
    }
    function refresh() {
      return c.api('GET', '/api/shaders').then(function (r) {
        if (!root.isConnected) return;
        if (!r.ok) { var l = document.getElementById('shaderloading'); if (l) { l.textContent = ''; l.appendChild(h('div', { class: 'msg err', id: 'shadermsg', text: r.data.error || 'Not available' })); } return; }
        draw(r.data);
      });
    }
    function soon(ms) { clearTimeout(soonTimer); soonTimer = setTimeout(function () { if (root.isConnected) refresh(); }, ms); }
    function watch() {
      clearTimeout(timer);
      timer = setTimeout(function () { if (root.isConnected) refresh().then(watch); }, data && data.playing ? 3000 : 5000);
    }
    // Once a second, between two answers from the box: the countdown moves, and a shader that changed on the screen
    // (the status poll knows first) is asked about at once.
    function tick() {
      if (!root.isConnected) return clearInterval(ticker);
      var pl = player(c), mine = data && data.playing ? data.playing.name : null;
      if (data && (typeof pl.shader === 'string' ? pl.shader : null) !== mine && !tick.asked) { tick.asked = true; soon(150); }
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
    function members(d, e) {      // the shaders of a set that can be shown
      var ok = {};
      d.shaders.forEach(function (s) { if (!s.error) ok[s.id] = true; });
      return e ? e.shaders.filter(function (r) { return ok[r.id]; }).length : 0;
    }
    function editing(d) { return (ui.editSet && setById(d, ui.editSet)) || activeSet(d); }
    function toggleVibes() {
      var running = !!(data.vibes && data.vibes.running);
      send('/api/vibes', vibesBody(data, !running), function () { soon(1200); }, 'shadernowmsg');
    }
    function step(dir) { send('/api/shaders/step', { dir: dir }, function () { soon(900); }, 'shadernowmsg'); }
    function applyPreset(name) {
      if (!data.playing) return;
      ui.lastPreset = { id: data.playing.id, name: name };
      send('/api/shaders/preset', { id: data.playing.id, name: name }, function () { soon(500); }, 'presetmsg');
    }

    // ---- MIDI: taught beside the thing it controls --------------------------------------------------------------
    // A small "MIDI" button opens a box under its control: what is on it now (with Remove), Teach, and the switch if
    // MIDI is off. The Vibes rows in the controllers card are the same box, always open.
    var midiOk = full && c.rowShown('midi');
    function what(e) { return (e.source === '*' ? 'any controller' : e.source) + ', ' + (e.kind === 'note' ? 'note ' : e.kind === 'cc' ? 'control ' : 'program ') + e.number; }
    function loadMidi() {
      if (!midiOk) return;
      if (!c.moduleOn('control-midi')) { midi.data = null; midi.asked = true; return drawTeachers(); }    // the box refuses the question while the module is off
      c.api('GET', '/api/midi').then(function (r) { if (!root.isConnected) return; midi.data = r.ok ? r.data : null; midi.asked = true; drawTeachers(); });
    }
    function midiOn() { return !!(midi.data && midi.data.enabled); }
    function drawTeacher(t) {
      var d = midi.data, on = midiOn(), mine = on ? d.map.filter(function (e) { return e.action === t.action; }) : [];
      if (t.btn) {
        t.btn.className = 'btn midibtn' + (mine.length ? ' mapped' : '');
        t.btn.setAttribute('aria-expanded', ui.open === t.action ? 'true' : 'false');
        t.box.hidden = ui.open !== t.action;
        if (t.box.hidden) return;
      }
      var box = t.box;
      box.textContent = '';
      box.appendChild(h('span', {}, h('b', { text: t.title }), h('br'),
        h('span', { class: 'hint', text: (t.hint ? t.hint + ' ' : '') + (!on ? '' : mine.length ? 'Now on: ' + mine.map(what).join('; ') + '.' : 'Not on any control yet (' + t.kind + ').') })));
      if (!on) {
        if (t.btn) box.appendChild(h('div', { class: 'row wrap' }, h('span', { class: 'hint', text: 'MIDI is off.' }),
          h('button', { class: 'btn', text: 'Switch MIDI on', onclick: function () { switchMidi(true); } })));
        return;
      }
      var learning = d.learn.active && ui.teach === t.action;
      if (learning) {
        box.appendChild(h('div', { class: 'msg', id: 'shaderlearning', role: 'status', text: 'Move or press the control now (' + d.learn.seconds_left + ' s)...' }));
        box.appendChild(h('div', { class: 'row' }, h('button', { class: 'btn', id: 'shaderteachcancel', text: 'Cancel', onclick: function () {
          clearTimeout(learnTimer); ui.teach = '';
          c.act('POST', '/api/midi/learn', { start: false }, gotMidi);
        } })));
      } else {
        box.appendChild(h('div', { class: 'row wrap' },
          h('button', { class: 'btn', text: 'Teach a control', 'aria-label': 'Teach a control: ' + t.title, disabled: d.learn.active, onclick: function () {
            ui.teach = t.action; ui.taught = '';
            c.act('POST', '/api/midi/learn', { start: true }, gotMidi);
          } }),
          mine.map(function (e) {
            return h('button', { class: 'btn', text: 'Remove', 'aria-label': 'Remove ' + what(e) + ' from ' + t.title, onclick: function () { c.act('POST', '/api/midi/map', { remove: e.id }, gotMidi); } });
          })));
      }
      if (ui.taught && ui.teach === t.action && !learning) box.appendChild(h('div', { class: 'msg', id: 'shadertaught', role: 'status', text: ui.taught }));
    }
    function drawTeachers() {
      teachers = teachers.filter(function (t) { return t.box.isConnected; });
      teachers.forEach(drawTeacher);
      if (remoteMidi) remoteMidi();
      clearTimeout(learnTimer);
      if (midiOn() && midi.data.learn.active && ui.teach) pollLearn();
    }
    function gotMidi(d) { midi.data = d; drawTeachers(); }
    function pollLearn() {
      clearTimeout(learnTimer);
      learnTimer = setTimeout(function () {
        if (!root.isConnected) return;
        c.api('GET', '/api/midi').then(function (r) {
          if (!r.ok || !root.isConnected) return;
          var l = r.data.learn;
          if (l.captured) {
            var entry = { source: l.captured.source, kind: l.captured.kind, channel: 0, number: l.captured.number, action: ui.teach };
            return c.act('POST', '/api/midi/map', { add: entry }, function (d) { ui.taught = 'Learned: ' + what(entry) + '.'; gotMidi(d); });
          }
          if (l.active) {     // only the countdown changes
            var el = document.getElementById('shaderlearning');
            if (el) el.textContent = 'Move or press the control now (' + l.seconds_left + ' s)...';
            return pollLearn();
          }
          ui.taught = 'Nothing was moved or pressed. Try again.';
          gotMidi(r.data);
        });
      }, 600);
    }
    function switchMidi(on) {
      return c.switchFeature('midi', on).then(function (r) {
        if (!r.ok) c.say(r.data.error || 'Could not switch it ' + (on ? 'on' : 'off') + '.', true); else c.say('');
        if (root.isConnected) loadMidi();
      });
    }
    // {btn, box} for one action: the button goes beside the control, the box under it.
    function teacher(action, title, hint, kind, always) {
      if (!midiOk) return null;
      var t = { action: action, title: title, hint: hint, kind: kind || 'a knob or a fader' };
      t.box = h('div', { class: (always ? 'item ' : '') + 'teach' + (always ? '' : ' teachbox'), 'data-teach': action, hidden: !always });
      if (!always) t.btn = h('button', { class: 'btn midibtn', text: 'MIDI', 'data-midi': action, 'aria-label': 'MIDI controller: ' + title, 'aria-expanded': 'false', onclick: function () {
        ui.open = ui.open === action ? '' : action;
        if (!midi.asked) loadMidi(); else drawTeachers();
      } });
      teachers.push(t);
      if (midi.asked) setTimeout(function () { if (t.box.isConnected) drawTeacher(t); }, 0);
      return t;
    }

    // ---- 1. Now: what is on, its load, and the transport --------------------------------------------------------
    function loadWords(p) {
      var parts = [LOAD[p.load] || ''];
      if (typeof p.drops_per_second === 'number' && p.drops_per_second > 0) parts.push(round(p.drops_per_second) + ' dropped frames a second.');
      if (p.pass_ms) parts.push(p.pass_ms + ' ms a frame.');
      if (p.checked === null || p.checked === undefined) parts.push('Not confirmed by the GPU yet.');
      return parts.filter(Boolean).join(' ');
    }
    function patchLoad(d) {
      var box = document.getElementById('shaderload'), p = d.playing;
      if (!box) return;
      box.hidden = !p;
      if (!p) return;
      var state = LOAD[p.load] ? p.load : 'unknown';
      box.className = 'loadbox load-' + state;
      box.setAttribute('data-load', state);
      var w = document.getElementById('shaderloadwords');
      var text = loadWords(p) || (d.config.guard === false ? 'The load is not being watched (the guard is off).' : 'Watching the load...');
      if (w && w.textContent !== text) w.textContent = text;
      var pend = document.getElementById('shaderpending');
      if (pend) pend.hidden = !p.pending;
    }
    function nowCard(d) {
      var v = d.vibes || {}, running = !!v.running, many = d.sets.length > 1;
      var chosen = startSet(d) || activeSet(d), n = members(d, chosen);
      var card = h('div', { class: 'card shaderstage', id: 'shadernow' }, h('h2', { text: 'On screen now' }),
        h('div', { class: 'row wrap' }, h('span', { class: 'shadername', id: 'shaderplaying', text: d.playing ? nice(d.playing.name) : 'No shader on screen' }),
          d.playing ? h('span', { class: 'chip chip-active', id: 'shadermode', text: running ? 'Vibes' : 'Playing' }) : null,
          h('span', { class: 'chip chip-check', id: 'shaderpending', hidden: true, text: 'Changing' })),
        h('div', { class: 'stageline', id: 'shaderline', role: 'status', text: stateLine(d) }));
      if (running) card.appendChild(h('div', { class: 'progress', id: 'vibesprogress', 'aria-hidden': 'true' }, h('div', { id: 'vibesbar' })));
      card.appendChild(h('div', { class: 'loadbox', id: 'shaderload', hidden: true },
        h('span', { class: 'loadmeter', 'aria-hidden': 'true' }, h('i'), h('i'), h('i')), h('span', { id: 'shaderloadwords', role: 'status' })));
      if (full) {       // picture detail sits with the load it changes
        var height = h('select', { class: 'text-input', id: 'shaderheight' }, d.render.heights.map(function (x) {
          return h('option', { value: String(x), text: x + ' lines' + (x === d.render['default'] ? ' (usual for this box)' : ''), selected: x === d.config.height });
        }));
        height.addEventListener('change', function () {
          height.blur();
          send('/api/shaders', { action: 'config', height: +height.value }, function () { saved('height'); var m = document.getElementById('saved-height'); if (m) m.textContent = 'Saved'; }, 'shadernowmsg');
        });
        card.appendChild(h('div', { class: 'detailrow' }, h('label', { class: 'field', for: 'shaderheight' }, 'Picture detail ', savedMark('height')), height));
        card.appendChild(h('p', { class: 'hint', id: 'detailhint', text: 'Fewer lines is lighter work. Now drawn at ' + d.render.width + ' x ' + d.render.height + ', ' + d.render.fps + ' pictures a second' +
          (d.render.measured ? '.' : '; speed on this board is not measured, so choose fewer lines if the picture stutters.') }));
      }
      if (!running && v.last && v.last.message && v.last.message !== 'stopped') {
        card.appendChild(h('div', { class: 'hint', id: 'vibeslast', text: 'Vibes ' + v.last.message + ' (' + v.last.at + ').' }));
      }
      if (d.error) card.appendChild(h('div', { class: 'msg err', id: 'shadererror', text: 'The GPU refused ' + nice(d.error.id) + ': ' + d.error.message }));
      if (!live) return card;
      if (many) {       // which set Vibes goes through; with one set nobody has to think about sets
        var pick = h('select', { class: 'text-input', id: 'vibesset', 'aria-label': 'The set of shaders Vibes plays' }, d.sets.map(function (e) {
          return h('option', { value: e.id, text: e.name + (e.id === d.active ? ' (the usual one)' : ''), selected: e.id === (running && v.set ? v.set.id : chosen.id) });
        }));
        pick.addEventListener('change', function () {
          ui.startSet = pick.value; pick.blur();
          if (running) send('/api/vibes', vibesBody(data, true), function () { soon(1200); }, 'shadernowmsg'); else { drawn.now = ''; draw(data); }
        });
        card.appendChild(h('div', { class: 'detailrow' }, h('label', { class: 'field', for: 'vibesset', text: 'Vibes plays the set' }), pick));
      }
      var prev = teacher('shader_prev', 'The shader before', '', 'a pad or a button'), next = teacher('shader_next', 'The next shader', '', 'a pad or a button');
      card.appendChild(h('div', { class: 'transport3' },
        h('button', { class: 'btn big', id: 'shaderprev', text: '‹ Previous', 'aria-label': 'The shader before', onclick: function () { step(-1); } }),
        h('button', { class: 'btn big' + (running ? '' : ' on'), id: 'vibesbtn', disabled: !running && !n, text: running ? 'Stop Vibes' : 'Start Vibes', onclick: toggleVibes }),
        h('button', { class: 'btn big', id: 'shadernext', text: 'Next ›', 'aria-label': 'The next shader', onclick: function () { step(1); } })));
      card.appendChild(h('div', { class: 'msg', id: 'shadernowmsg', role: 'status' }));
      if (prev) {
        card.appendChild(h('div', { class: 'row wrap midirow' }, h('span', { class: 'hint grow', text: 'Previous and Next from a controller:' }), prev.btn, next.btn));
        prev.btn.textContent = 'MIDI: Previous'; next.btn.textContent = 'MIDI: Next';
        card.appendChild(prev.box); card.appendChild(next.box);
      }
      card.appendChild(h('p', { class: 'hint', id: 'stephint', text: 'Previous and Next go through ' + (many ? 'the set' : 'the shaders that are in Vibes') + ', with Vibes running or not.' }));
      if (!n) {
        card.appendChild(h('div', { class: 'hint', id: 'norotation', text: (many ? 'The set ' + chosen.name + ' has no shader in it. ' : 'Nothing is in Vibes. ') +
          (full ? 'Switch at least one shader in, in the list.' : 'Someone with full access chooses which shaders are in it.') }));
      }
      card.appendChild(h('p', { class: 'hint keyshint', id: 'shaderkeys', text: 'Keys: Space starts and stops Vibes · ← → the shader before and the next · 1 to 8 a preset' }));
      return card;
    }

    // ---- 2. Controls for the shader that is on: the three every shader has, then its own, by type ---------------
    function controlsCard(d) {
      ctls = [];
      if (!d.playing || !live) return null;
      var s = d.shaders.filter(function (x) { return x.id === d.playing.id; })[0];
      if (!s) return null;
      var card = h('div', { class: 'card', id: 'shadercontrols' }, h('h2', { text: 'Controls for ' + nice(s.name) }));
      var common = h('div', { class: 'ctls', id: 'shadercommon' });
      ['speed', 'hue', 'brightness'].forEach(function (k) {
        var t = k === 'speed' ? teacher('shader_speed', 'Speed', 'A knob or a fader sets the speed of whichever shader is playing.') : null;
        var ctl = commonControl(k, d.controls[k], d.playing.controls[k], { h: h, rig: rig, extra: t ? t.btn : null });
        ctls.push({ common: k, ctl: ctl });
        common.appendChild(ctl.el);
        if (t) common.appendChild(t.box);
      });
      card.appendChild(common);
      var own = h('div', { class: 'ctls', id: 'shadersliders' }), knob = knobOf(s.inputs), shown = [];
      s.inputs.forEach(function (i) {
        var n = knob[i.name];
        var t = n ? teacher('shader_control_' + n, 'Knob ' + n + ': ' + (i.label || i.name),
          'Knob ' + n + ' follows the ' + ORDINAL[n - 1] + ' control of whichever shader is playing' + (i.type === 'event' || i.type === 'bool' ? ' (a pad or a button works too).' : '.')) : null;
        var v = d.playing.values[i.name];
        var ctl = inputControl(i, v === undefined ? i['default'] : v, { h: h, rig: rig, extra: t ? t.btn : null, size: d.render });
        if (!ctl) return;
        ctls.push({ name: i.name, ctl: ctl });
        shown.push(i);
        own.appendChild(ctl.el);
        if (t) own.appendChild(t.box);
      });
      if (!shown.length) { card.appendChild(h('div', { class: 'hint', id: 'nocontrols', text: 'This shader has no controls of its own.' })); return card; }
      card.appendChild(h('h3', { class: 'ctltitle', text: 'This shader\'s own' }));
      card.appendChild(own);
      card.appendChild(h('div', { class: 'msg', id: 'shaderctlmsg', role: 'status' }));
      card.appendChild(h('div', { class: 'row' }, h('button', { class: 'btn grow', id: 'shaderresetall', text: 'Reset all to the shader\'s own values', onclick: function () {
        var all = {}, speed = {};
        shown.forEach(function (i) { if (i.type !== 'event') all[i.name] = i['default']; });
        Object.keys(d.controls).forEach(function (k) { speed[k] = d.controls[k]['default']; });
        send('/api/shaders/values', { id: s.id, values: all, controls: speed }, function (r) {
          ctls.forEach(function (x) { var v = x.common ? r.controls[x.common] : r.values[x.name]; if (v !== undefined) x.ctl.apply(v); });
        }, 'shaderctlmsg');
      } })));
      card.appendChild(h('p', { class: 'hint', id: 'slidernote', text: 'The picture follows while you drag; a double tap on a slider puts it back. Moving a control does not stop Vibes. ' +
        'The values last until the shader changes: save a preset to keep them.' }));
      return card;
    }

    // ---- 3. Presets of the shader that is on ---------------------------------------------------------------------
    function presetState(d) {
      var p = d.playing, s = p && d.shaders.filter(function (x) { return x.id === p.id; })[0];
      if (!s) return null;
      var last = ui.lastPreset && ui.lastPreset.id === p.id && s.presets.indexOf(ui.lastPreset.name) >= 0 ? ui.lastPreset.name : null;
      return { s: s, names: s.presets, used: p.preset || null, changed: !p.preset && last ? last : null };
    }
    function inlineName(row, value, label, onSave) {       // a name typed in place of a row, with Save and Cancel
      var input = h('input', { class: 'text-input', type: 'text', maxlength: String((data.limits && data.limits.name) || 40), value: value, 'aria-label': label, autocomplete: 'off' });
      var form = h('div', { class: 'row wrap renamerow' }, input,
        h('button', { class: 'btn on', text: 'Save', onclick: function () { var t = input.value.trim(); if (t) onSave(t); } }),
        h('button', { class: 'btn', text: 'Cancel', onclick: function () { form.parentNode.removeChild(form); row.hidden = false; } }));
      input.addEventListener('keydown', function (e) { if (e.key === 'Enter') { var t = input.value.trim(); if (t) onSave(t); } });
      row.hidden = true;
      row.parentNode.insertBefore(form, row.nextSibling);
      input.focus(); input.select();
    }
    function presetsCard(d) {
      var st = live ? presetState(d) : null;
      if (!st) return null;
      var s = st.s, card = h('div', { class: 'card', id: 'shaderpresets' },
        h('div', { class: 'row between' }, h('h2', { text: 'Presets' }), st.changed ? h('span', { class: 'chip chip-check', id: 'presetchanged', text: 'Changed' }) : null),
        h('p', { class: 'hint', text: 'Presets: your saved versions of this shader. A tap puts one on.' }));
      if (st.names.length) {
        card.appendChild(h('div', { class: 'presetrow', id: 'presetrow' }, st.names.map(function (name, n) {
          var on = st.used === name, was = st.changed === name;
          return h('button', { class: 'btn preset' + (on ? ' on' : '') + (was ? ' was' : ''), 'data-preset': name, 'aria-pressed': on ? 'true' : 'false',
            'aria-label': 'Preset ' + name + (on ? ' (in use)' : was ? ' (changed since)' : ''), onclick: function () { applyPreset(name); } },
            n < 8 ? h('span', { class: 'slot', text: String(n + 1) }) : null, h('span', { text: name }));
        })));
      } else card.appendChild(h('div', { class: 'hint', id: 'nopresets', text: full ? 'None yet. Set the controls as you like them, then save.' : 'None yet. Someone with full access saves them.' }));
      card.appendChild(h('div', { class: 'msg', id: 'presetmsg', role: 'status' }));
      if (!full) return card;
      var name = h('input', { class: 'text-input', id: 'presetname', type: 'text', maxlength: String(d.limits.name || 40), placeholder: 'A name, for example Bright', 'aria-label': 'Name of the new preset', autocomplete: 'off' });
      var save = h('button', { class: 'btn', id: 'presetsave', text: 'Save as preset', onclick: function () {
        var t = name.value.trim();
        if (!t) { name.focus(); return fail({ data: { error: 'Give the preset a name first.' } }, 'presetmsg'); }
        ui.lastPreset = { id: s.id, name: t };
        name.blur();
        send('/api/shaders/presets', { action: 'save', id: s.id, name: t }, null, 'presetmsg');
      } });
      name.addEventListener('input', function () {
        var t = name.value.trim().toLowerCase();
        save.textContent = st.names.some(function (x) { return x.toLowerCase() === t; }) ? 'Save over it' : 'Save as preset';
      });
      name.addEventListener('keydown', function (e) { if (e.key === 'Enter') save.click(); });
      card.appendChild(h('div', { class: 'row wrap saverow' }, name, save));
      var more = h('details', { class: 'fold', id: 'presetmore', open: ui.more }, h('summary', { text: 'More' }));
      more.addEventListener('toggle', function () { ui.more = more.open; });
      var list = h('div', { class: 'list' });
      st.names.forEach(function (p) {
        var row = h('div', { class: 'row wrap presetedit', 'data-preset-edit': p }, h('span', { class: 'grow', text: p + (p.toLowerCase() === 'default' ? ' (what a plain Play and Vibes use)' : '') }),
          h('button', { class: 'btn', text: 'Rename', 'aria-label': 'Rename the preset ' + p, onclick: function () {
            inlineName(row, p, 'New name for the preset ' + p, function (to) {
              if (ui.lastPreset && ui.lastPreset.id === s.id && ui.lastPreset.name === p) ui.lastPreset.name = to;
              document.activeElement.blur();
              send('/api/shaders/presets', { action: 'rename', id: s.id, name: p, to: to }, null, 'presetmsg');
            });
          } }),
          h('button', { class: 'btn', text: 'Delete', 'aria-label': 'Delete the preset ' + p, onclick: function (e) {
            c.confirmRow('Delete the preset ' + p + '?', 'Delete', 'Keep it', function () { send('/api/shaders/presets', { action: 'delete', id: s.id, name: p }, null, 'presetmsg'); }, e.target);
          } }));
        list.appendChild(h('div', { class: 'item presetitem' }, row));
      });
      if (!st.names.length) list.appendChild(h('div', { class: 'hint', text: 'Nothing to rename or delete yet.' }));
      if (midiOk) {
        list.appendChild(h('p', { class: 'hint', text: 'A pad on a controller can put on preset 1 to 8 of whichever shader is playing:' }));
        var slots8 = h('div', { class: 'slotgrid', id: 'presetslots' });
        for (var n = 1; n <= 8; n++) {
          var t = teacher('shader_preset_' + n, 'Preset ' + n, 'A pad puts on preset ' + n + ' of whichever shader is playing' + (st.names[n - 1] ? ' (here: ' + st.names[n - 1] + ').' : '.'), 'a pad or a button');
          t.btn.textContent = 'MIDI: ' + n;
          slots8.appendChild(t.btn);
          list.appendChild(t.box);
        }
        list.insertBefore(slots8, list.querySelector('.teachbox'));
      }
      more.appendChild(list);
      card.appendChild(more);
      return card;
    }

    // ---- 4. The library: filters, and one compact row per shader ------------------------------------------------
    // It will hold dozens, so a row that changes (its switch, the Playing mark) is patched in place, and a filter
    // only hides rows.
    function applyFilter() {
      var q = ui.filter.trim().toLowerCase(), shown = 0;
      Array.prototype.forEach.call(root.querySelectorAll('.shader-entry'), function (row) {
        var hit = (!q || row.getAttribute('data-name').indexOf(q) >= 0) && (ui.cost === 'all' || row.getAttribute('data-work') === ui.cost) &&
          (ui.pack === 'all' || row.getAttribute('data-pack') === ui.pack) && (ui.family === 'all' || row.getAttribute('data-family') === ui.family);
        row.hidden = !hit;
        if (hit) shown++;
      });
      var none = document.getElementById('shadernone');
      if (none) { none.hidden = shown > 0; none.textContent = q ? 'No shader has "' + ui.filter.trim() + '" in its name' + (ui.cost === 'all' && ui.pack === 'all' && ui.family === 'all' ? '.' : ' with these choices.') : 'No shader fits these choices.'; }
    }
    function markPlaying(d) {
      var on = d.playing ? d.playing.id : null;
      Array.prototype.forEach.call(root.querySelectorAll('.shader-entry'), function (row) {
        var is = row.getAttribute('data-shader') === on, chip = row.querySelector('.playchip');
        row.classList.toggle('on', is);
        if (chip) chip.hidden = !is;
      });
    }
    function packName(p) { return p === 'nxlx' ? 'The project\'s own' : p === 'uploads' ? 'Your uploads' : 'The ' + p + ' pack'; }
    function entry(d, s, e, many) {
      var name = nice(s.name), inSet = e.shaders.some(function (r) { return r.id === s.id; });
      var facts = [work(s), family(s), s.source === 'uploaded' ? 'your upload' : '', full ? '' : (inSet ? 'in Vibes' : 'not in Vibes')].filter(Boolean).join(' · ');
      // Somebody else's work says so: the pack it came with and the author's own credit line (plain text, as written
      // in the file). The project's own need neither.
      var third = s.pack && s.pack !== 'nxlx' && s.pack !== 'uploads';
      var from = third ? 'From the ' + s.pack + ' pack. Credit: ' + (s.credit || 'none given in the file')
        : (s.source === 'uploaded' && s.credit ? 'Credit: ' + s.credit : '');
      var row = h('div', { class: 'item shader-entry', 'data-shader': s.id, 'data-pack': s.pack || '', 'data-name': name.toLowerCase(), 'data-work': s.weight || 'other', 'data-family': family(s) },
        h('span', {}, h('b', { text: name }), ' ', h('span', { class: 'chip chip-active playchip', hidden: true, text: 'Playing' }),
          s.description ? h('br') : null, s.description ? h('span', { class: 'hint', text: s.description }) : null,
          facts ? h('br') : null, facts ? h('span', { class: 'hint shaderfacts', text: facts }) : null,
          from ? h('br') : null, from ? h('span', { class: 'hint shadercredit', text: from }) : null));
      if (s.error) row.appendChild(h('div', { class: 'msg err shaderproblem', text: 'Cannot be shown: ' + s.error }));
      else if (s.refused) row.appendChild(h('div', { class: 'msg err shaderproblem shaderrefused', text: 'This box\'s GPU refused it: ' + s.refused }));
      else if (d.error && d.error.id === s.id) row.appendChild(h('div', { class: 'msg err shaderproblem', text: 'The GPU refused it: ' + d.error.message }));
      if (s.heavy) {
        row.appendChild(h('div', { class: 'row wrap shaderheavy' },
          h('span', { class: 'msg err grow', text: 'Too heavy on this box. Left out of Vibes.' + (s.heavy.drops ? ' (' + round(s.heavy.drops) + ' dropped frames a second at ' + s.heavy.height + ' lines)' : '') }),
          full ? h('button', { class: 'btn', text: 'Put it back', 'aria-label': 'Put ' + name + ' back into Vibes', onclick: function () { send('/api/shaders', { action: 'heavy', id: s.id, on: false }, null, 'shaderlibmsg'); } }) : null));
      }
      if (!live) return row;
      var label = many ? 'In this set' : 'In Vibes';
      var sw = full ? h('button', { class: 'switch', role: 'switch', 'aria-checked': inSet ? 'true' : 'false', 'aria-label': name + (many ? ' in the set ' + e.name : ' in Vibes'), disabled: !!s.error,
        onclick: function () {
          var want = sw.getAttribute('aria-checked') !== 'true', now = editing(data);
          var list = now.shaders.filter(function (r) { return r.id !== s.id; }).map(function (r) { return r.preset ? { id: r.id, preset: r.preset } : r.id; });
          if (want) list.push(s.id);
          c.api('POST', '/api/shaders', { action: 'set', op: 'update', id: now.id, shaders: list }).then(function (r) {
            if (!r.ok) return fail(r, 'shaderlibmsg');
            c.say('');
            sw.setAttribute('aria-checked', want ? 'true' : 'false');       // only this row and the cards that count change
            data = r.data; drawn.lib = libShape(data);
            draw(data);
          });
        } }) : null;
      row.appendChild(h('div', { class: 'row wrap shaderacts' },
        h('button', { class: 'btn on', text: 'Play', 'aria-label': 'Play ' + name, disabled: !!s.error,
          onclick: function () { send('/api/shaders/play', { id: s.id }, null, 'shaderlibmsg'); } }),
        full ? h('label', { class: 'rot' }, h('span', { text: label }), sw) : null,
        full && s.source === 'uploaded' ? h('button', { class: 'btn', text: 'Remove', 'aria-label': 'Remove ' + name, onclick: function (ev) {
          c.confirmRow('Remove ' + name + '? The file is deleted from the box.', 'Remove', 'Keep it', function () { send('/api/shaders', { action: 'delete', id: s.id }, null, 'shaderlibmsg'); }, ev.target);
        } }) : null));
      return row;
    }
    function libShape(d) {
      var e = editing(d);
      return JSON.stringify([d.error, e && [e.id, e.name, e.shaders], d.sets.length > 1, d.shaders.map(function (s) { return [s.id, s.error, s.refused, s.heavy, s.weight, s.categories, s.description]; })]);
    }
    function listCard(d) {
      var e = editing(d), many = d.sets.length > 1;
      var find = h('input', { class: 'text-input', id: 'shaderfilter', type: 'search', placeholder: 'Find a shader', 'aria-label': 'Find a shader by name', value: ui.filter, autocomplete: 'off' });
      find.addEventListener('input', function () { ui.filter = find.value; applyFilter(); });
      function chooser(id, key, label, options) {
        if (options.every(function (o) { return o[0] !== ui[key]; })) ui[key] = 'all';
        var el = h('select', { class: 'text-input', id: id, 'aria-label': label }, options.map(function (o) { return h('option', { value: o[0], text: o[1], selected: o[0] === ui[key] }); }));
        el.addEventListener('change', function () { ui[key] = el.value; applyFilter(); });
        return el;
      }
      var packs = [], fams = [];
      d.shaders.forEach(function (s) { if (s.pack && packs.indexOf(s.pack) < 0) packs.push(s.pack); var f = family(s); if (f && fams.indexOf(f) < 0) fams.push(f); });
      var filters = h('div', { class: 'shaderfind', id: 'shaderfind' }, find,
        chooser('shadercost', 'cost', 'Show by how much work it is', [['all', 'Any work'], ['light', 'Light work'], ['medium', 'Medium work'], ['heavy', 'Heavy work']]),
        packs.length > 1 ? chooser('shaderpack', 'pack', 'Show by where it came from', [['all', 'From anywhere']].concat(packs.map(function (p) { return [p, packName(p)]; }))) : null,
        fams.length ? chooser('shaderfamily', 'family', 'Show by what it is made for', [['all', 'Any kind']].concat(FAMILIES.filter(function (f) { return fams.indexOf(f) >= 0; }).map(function (f) { return [f, f]; }))) : null);
      return h('div', { class: 'card', id: 'shadercard' }, h('h2', { text: 'Shaders (' + d.shaders.length + ')' }),
        live ? h('p', { class: 'hint', text: 'Play shows one shader until something else plays.' + (full ? '' : ' Vibes goes through the ones that are in it.') }) : null,
        full ? h('p', { class: 'hint', id: 'libset' }, many ? 'The switches put a shader in or out of the set ' : 'The switch puts a shader in or out of Vibes.', many ? h('b', { text: e.name }) : null, many ? '.' : null) : null,
        filters,
        h('div', { class: 'msg', id: 'shaderlibmsg', role: 'status' }),
        h('div', { class: 'list shaderlist', id: 'shaderlist' }, d.shaders.map(function (s) { return entry(d, s, e, many); }),
          h('div', { class: 'hint', id: 'shadernone', hidden: true })));
    }

    // ---- 5. Vibes and its sets (full access) --------------------------------------------------------------------
    // With one set this is "Vibes settings" and says nothing about sets but the way to make a second one. With more,
    // each set is a row; one is being edited (its time, variation, order, name, and its shaders in the library).
    function settingsCard(d) {
      if (!full) return null;
      var e = editing(d), many = d.sets.length > 1;
      function update(body, key, msg) {
        return send('/api/shaders', Object.assign({ action: 'set', op: 'update', id: e.id }, body), function () { if (key) { saved(key); var m = document.getElementById('saved-' + key); if (m) m.textContent = 'Saved'; } }, msg || 'setmsg');
      }
      var listed = DWELLS.indexOf(e.dwell) >= 0;
      var dwell = h('select', { class: 'text-input', id: 'vibesdwell' },
        (listed ? [] : [h('option', { value: String(e.dwell), text: 'Custom (' + e.dwell + ' s)', selected: true })]).concat(DWELLS.map(function (x) {
          return h('option', { value: String(x), text: span(x), selected: x === e.dwell });
        })));
      dwell.addEventListener('change', function () { dwell.blur(); update({ dwell: +dwell.value }, 'dwell'); });
      var order = h('select', { class: 'text-input', id: 'vibesorder' }, [['shuffle', 'Shuffled'], ['listed', 'In the order they were put in']].map(function (o) {
        return h('option', { value: o[0], text: o[1], selected: o[0] === e.order });
      }));
      order.addEventListener('change', function () { order.blur(); update({ order: order.value }, 'order'); });
      var card = h('div', { class: 'card', id: 'vibessettings' }, h('h2', { text: many ? 'Vibes sets' : 'Vibes settings' }));
      if (many) {
        card.appendChild(h('p', { class: 'hint', text: 'A set is a list of shaders for Vibes, with its own pace. The usual set is the one the Vibes button, the schedule and controllers start.' }));
        card.appendChild(h('div', { class: 'list', id: 'setlist' }, d.sets.map(function (x) {
          var on = x.id === e.id;
          return h('button', { class: 'setrow' + (on ? ' on' : ''), 'data-set': x.id, 'aria-pressed': on ? 'true' : 'false', 'aria-label': 'Edit the set ' + x.name, onclick: function () {
            ui.editSet = x.id; drawn.set = ''; draw(data);
          } }, h('span', { class: 'navname', text: x.name }), h('span', { class: 'navstate', text: members(d, x) + (members(d, x) === 1 ? ' shader' : ' shaders') + ', ' + span(x.dwell) + ' each' }),
            h('span', { class: 'setchips' }, x.id === d.active ? h('span', { class: 'chip chip-ready', text: 'Usual' }) : null, on ? h('span', { class: 'chip chip-active', text: 'Editing' }) : null));
        })));
        card.appendChild(h('h3', { class: 'ctltitle', id: 'setediting', text: 'Editing: ' + e.name }));
        card.appendChild(h('p', { class: 'hint', text: 'Its shaders are chosen with the switches in the list of shaders.' }));
      }
      card.appendChild(h('label', { class: 'field', for: 'vibesdwell' }, 'Each one stays for ', savedMark('dwell')));
      card.appendChild(dwell);
      card.appendChild(h('label', { class: 'field', for: 'vibesorder' }, 'They play ', savedMark('order')));
      card.appendChild(order);
      card.appendChild(h('label', { class: 'rot between' }, h('span', {}, 'Change speed and colours a little each round ', savedMark('vary')),
        h('button', { class: 'switch', id: 'vibesvary', role: 'switch', 'aria-checked': e.vary ? 'true' : 'false', 'aria-label': 'Change speed and colours a little each round',
          onclick: function () { update({ vary: !e.vary }, 'vary'); } })));
      card.appendChild(h('div', { class: 'msg', id: 'setmsg', role: 'status' }));
      if (many) {
        var acts = h('div', { class: 'row wrap', id: 'setacts' },
          h('button', { class: 'btn on', id: 'setstart', text: 'Start Vibes on this set', disabled: !members(d, e), onclick: function () {
            ui.startSet = e.id;
            send('/api/vibes', { on: true, set: e.id }, function () { drawn.now = ''; soon(1200); }, 'setmsg');
          } }),
          e.id !== d.active ? h('button', { class: 'btn', id: 'setactivate', text: 'Make it the usual set', onclick: function () {
            ui.startSet = null;
            send('/api/shaders', { action: 'set', op: 'activate', id: e.id }, null, 'setmsg');
          } }) : null,
          h('button', { class: 'btn', id: 'setrename', text: 'Rename', onclick: function () {
            inlineName(acts, e.name, 'New name for the set ' + e.name, function (to) { document.activeElement.blur(); update({ name: to }, null); });
          } }),
          h('button', { class: 'btn', id: 'setdelete', text: 'Delete', 'aria-label': 'Delete the set ' + e.name, onclick: function (ev) {
            c.confirmRow('Delete the set ' + e.name + '? Its shaders stay in the library.', 'Delete', 'Keep it', function () {
              ui.editSet = null; if (ui.startSet === e.id) ui.startSet = null;
              send('/api/shaders', { action: 'set', op: 'delete', id: e.id }, null, 'setmsg');
            }, ev.target);
          } }));
        card.appendChild(acts);
      }
      // A new set: a name is all it needs; it starts empty and is the one being edited
      var name = h('input', { class: 'text-input', id: 'setname', type: 'text', maxlength: String(d.limits.name || 40), placeholder: 'A name, for example Show', 'aria-label': 'Name of the new set', autocomplete: 'off' });
      function add() {
        var t = name.value.trim(), before = d.sets.map(function (x) { return x.id; });
        if (!t) { name.focus(); return fail({ data: { error: 'Give the set a name first.' } }, 'setaddmsg'); }
        name.blur();
        c.api('POST', '/api/shaders', { action: 'set', op: 'add', name: t, shaders: [] }).then(function (r) {
          if (!r.ok) return fail(r, 'setaddmsg');
          var fresh = r.data.sets.filter(function (x) { return before.indexOf(x.id) < 0; })[0];
          if (fresh) ui.editSet = fresh.id;
          ui.addOpen = false;
          if (root.isConnected) draw(r.data);
        });
      }
      name.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') add(); });
      var addFold = h('details', { class: 'fold', id: 'setaddfold', open: !!ui.addOpen }, h('summary', { text: many ? '+ Add a set' : 'More than one set of shaders' }),
        h('div', { class: 'list shaderadv' },
          many ? null : h('p', { class: 'hint', text: 'A second list of shaders for Vibes, for example quiet ones for opening hours and strong ones for a show. Until you add one there is nothing to choose.' }),
          h('div', { class: 'row wrap saverow' }, name, h('button', { class: 'btn', id: 'setadd', text: 'Add the set', onclick: add })),
          h('div', { class: 'msg', id: 'setaddmsg', role: 'status' })));
      addFold.addEventListener('toggle', function () { ui.addOpen = addFold.open; });
      card.appendChild(addFold);

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
            uploaded(file.name + ' was added. It is in the list of shaders; switch it into Vibes there.', false);
            drawn.set = '';
            if (root.isConnected) draw(r.data);
          });
        };
        reader.onerror = function () { refuse('the file could not be read.'); };
        reader.readAsText(file);
      });
      var guard = d.config.guard !== false;
      var adv = h('details', { class: 'fold', id: 'shaderadvanced', open: ui.advanced },
        h('summary', { text: 'Advanced' }),
        h('div', { class: 'list shaderadv' },
          h('label', { class: 'rot between' }, h('span', {}, 'Leave out shaders that are too heavy for this box ', savedMark('guard')),
            h('button', { class: 'switch', id: 'shaderguard', role: 'switch', 'aria-checked': guard ? 'true' : 'false', 'aria-label': 'Leave out shaders that are too heavy for this box',
              onclick: function () { send('/api/shaders', { action: 'config', guard: !guard }, function () { saved('guard'); var m = document.getElementById('saved-guard'); if (m) m.textContent = 'Saved'; }, 'setmsg'); } })),
          h('p', { class: 'hint', text: 'While Vibes runs, a shader that keeps dropping frames is passed over and marked in the list until you put it back.' }),
          h('div', { class: 'row' }, h('button', { class: 'btn grow', id: 'shaderupload', text: '+ Add a shader file (.fs)', onclick: function () { picker.click(); } }), picker),
          h('div', { class: 'msg' + (ui.upload && ui.upload.err ? ' err' : ''), id: 'shaderuploadmsg', role: 'status', text: ui.upload ? ui.upload.text : '' }),
          h('p', { class: 'hint', text: 'An ISF file that draws from nothing (a generator), up to ' + Math.round(d.limits.bytes / 1024) + ' KB. Shaders that need a picture or sound are refused, with the reason.' })));
      adv.addEventListener('toggle', function () { ui.advanced = adv.open; });
      card.appendChild(adv);
      return card;
    }

    // ---- 6. Controllers: the MIDI switch and the Vibes rows, and the lighting desk's channel --------------------
    var TEACH = [['vibes', 'Start and stop Vibes', 'a pad or a button'], ['vibes_next', 'Next one', 'a pad or a button'], ['vibes_dwell', 'How long each one stays', 'a knob or a fader']];
    var remoteMidi = null;
    function remoteCard() {
      if (!full || !(c.rowShown('midi') || c.rowShown('dmx'))) return null;
      var head = h('div', { id: 'shadermidihead' }), rows = h('div', { class: 'list', id: 'shadermidirows' });
      var midiBox = h('div', { class: 'list', id: 'shadermidi' }, head, rows), dmx = h('div', { class: 'list', id: 'shaderdmx' });
      function featureSwitch(id, label, on, flip) {
        var sw = h('button', { class: 'switch', id: 'shader' + id + 'sw', role: 'switch', 'aria-checked': on ? 'true' : 'false', 'aria-label': label, onclick: function () { flip(!on); } });
        return h('label', { class: 'rot between' }, h('b', { text: label }), h('span', { class: 'row' }, h('span', { class: 'switchlabel', text: on ? 'On' : 'Off' }), sw));
      }
      var vibesRows = TEACH.map(function (t) { var x = teacher(t[0], t[1], '', t[2], true); if (x) rows.appendChild(x.box); return x; });
      remoteMidi = function () {
        if (!head.isConnected) return;
        var on = midiOn();
        head.textContent = '';
        head.appendChild(featureSwitch('midi', 'MIDI controller', on, switchMidi));
        head.appendChild(h('p', { class: 'hint', id: 'shadermidiline', text: !on ? 'Off. Switch it on to teach a knob or a pad to run Vibes and the shader\'s controls.'
          : (midi.data.devices.length ? 'Plugged in: ' + midi.data.devices.map(function (x) { return x.name; }).join(', ') + '.' : 'No controller is plugged in yet.') +
            ' The shader\'s own controls, Speed, Previous, Next and the presets are taught beside them: the small MIDI buttons above.' }));
        rows.hidden = !on;
        vibesRows.forEach(function (x) { if (x) x.box.hidden = !on; });
      };
      function drawDmx(d) {
        dmx.textContent = '';
        var on = !!(d && d.enabled);
        dmx.appendChild(featureSwitch('dmx', 'DMX lighting desk', on, function (want) {
          c.switchFeature('dmx', want).then(function (r) {
            if (!r.ok) c.say(r.data.error || 'Could not switch it ' + (want ? 'on' : 'off') + '.', true); else c.say('');
            if (root.isConnected) loadDmx();
          });
        }));
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
      loadDmx();
      return h('div', { class: 'card', id: 'shaderremote' }, h('h2', { text: 'Controllers' }),
        c.rowShown('midi') ? midiBox : null, c.rowShown('dmx') ? dmx : null);
    }

    // Each card has its own shape; a card is drawn again only when its shape changed, and never under someone's hands.
    function draw(d) {
      data = d;
      tick.asked = false;
      var first = document.getElementById('shaderloading');
      if (first) first.parentNode.removeChild(first);
      if (ui.editSet && !setById(d, ui.editSet)) ui.editSet = null;
      if (ui.startSet && !setById(d, ui.startSet)) ui.startSet = null;
      if (d.playing && d.playing.preset) ui.lastPreset = { id: d.playing.id, name: d.playing.preset };
      else if (ui.lastPreset && (!d.playing || d.playing.id !== ui.lastPreset.id)) ui.lastPreset = null;
      var s = d.playing && d.shaders.filter(function (x) { return x.id === d.playing.id; })[0];
      var e = editing(d), st = presetState(d);
      var sets = d.sets.map(function (x) { return [x.id, x.name, x.dwell, x.vary, x.order, members(d, x)]; });
      var shapes = {
        now: JSON.stringify([d.playing && d.playing.id, !!(d.vibes && d.vibes.running), d.vibes && d.vibes.set, d.vibes && d.vibes.last, d.error, sets, d.active, ui.startSet,
          d.render, d.config.height]),
        ctl: JSON.stringify([s && [s.id, inputShape(s)], d.controls]),
        pre: JSON.stringify([st && [st.s.id, st.names, st.used, st.changed]]),
        lib: libShape(d),
        set: JSON.stringify([sets, d.active, e && e.id, d.config.guard, ui.upload, d.limits])
      };
      var late = false;
      ['now', 'ctl', 'pre', 'lib', 'set'].forEach(function (k) {
        if (shapes[k] === drawn[k]) return;
        if (drawn[k] !== undefined && held(slots[k])) { shapes[k] = drawn[k]; late = true; return; }
        if (k === 'now') put(k, nowCard(d));
        else if (k === 'ctl') put(k, controlsCard(d));
        else if (k === 'pre') put(k, presetsCard(d));
        else if (k === 'lib') { var list = document.getElementById('shaderlist'), at = list ? list.scrollTop : 0; put(k, listCard(d)); applyFilter(); list = document.getElementById('shaderlist'); if (list) list.scrollTop = at; }
        else put(k, settingsCard(d));
      });
      if (!drawn.remote) { put('remote', remoteCard()); if (midiOk) loadMidi(); }
      shapes.remote = '1';
      drawn = shapes;
      syncControls(ctls, d.playing);
      // A change the GPU refused shows up a moment after it was sent: said at the control that sent it.
      var err = d.error && d.playing && d.error.id === d.playing.id ? d.error : null, stamp = err ? err.at + err.message : '';
      if (seenError !== undefined && stamp && stamp !== seenError && rig.last && Date.now() - rig.sentAt < 10000) rig.last.note('The GPU refused that: ' + err.message + ' It is as it was before.', true);
      seenError = stamp;
      markPlaying(d);
      patchLoad(d);
      countdown();
      watch();
      if (late) soon(1500);
    }
    keys = { root: root, live: live, space: function () { if (data) toggleVibes(); }, step: function (dir) { if (data) step(dir); },
      preset: function (n) { var st = data && presetState(data); if (st && st.names[n - 1]) applyPreset(st.names[n - 1]); } };
    ticker = setInterval(tick, 1000);
    refresh();
    return root;
  }

  // A laptop's keyboard on the Shaders page: space starts and stops Vibes, the arrows step, 1 to 8 put on a preset.
  // Never while typing or choosing: not in a field, a list or the XY pad, not with a modifier, not while a question
  // waits. Space on a button stays that button's own press.
  document.addEventListener('keydown', function (e) {
    if (!keys || !keys.root.isConnected || !keys.live) return;
    if (e.ctrlKey || e.metaKey || e.altKey || e.repeat || e.defaultPrevented || document.getElementById('confirmrow')) return;
    var a = document.activeElement, tag = a && a.tagName;
    if (a && (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA' || a.isContentEditable || (a.closest && a.closest('.xypad')))) return;
    if (e.key === ' ' || e.code === 'Space') {
      if (tag === 'BUTTON' || tag === 'SUMMARY' || tag === 'A') return;
      e.preventDefault();
      keys.space();
    } else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      e.preventDefault();
      keys.step(e.key === 'ArrowRight' ? 1 : -1);
    } else if (/^[1-8]$/.test(e.key)) keys.preset(+e.key);
  });

  window.pvjShaders = { liveRow: liveRow, liveStrip: liveStrip, patch: patch, page: page, nice: nice };
})();
