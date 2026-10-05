// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// Effects: the panel's part. An effect is a filter over whatever plays (a clip, a stream, a live input); see
// pvj/effects.py. Loaded after shaders.js, whose controls it draws with (window.pvjShaders.kit), and before app.js,
// which hands over its helpers each time it draws. Two things are drawn here: a compact strip on Live (the
// effect's name, Amount, Off, Previous and Next) and the Effects card on Mix (the list of filters, the controls of
// the one that is on with Amount first, presets, and the MIDI teach buttons beside what they drive).
(function () {
  'use strict';

  // Survives redraws, never saved: the list's filters, the MIDI action being taught, what the last upload said.
  var ui = { filter: '', weight: 'all', open: '', teach: '', taught: '', upload: null, more: false };
  var X = { data: null, at: 0, busy: false, key: '' };          // the box's last answer, shared by the strip and the card
  var strip = { shape: '', amount: null };
  var card = { head: '', ctl: '', pre: '', list: '', add: '', ctls: [], half: null };
  var midi = { data: null, asked: false };
  var teachers = [], learnTimer = null, soonTimer = null;
  var UPPER = { rgb: 'RGB', lgg: 'LGG', eq: 'EQ', h: 'H', v: 'V' };
  var WORK = { light: 'Light work', medium: 'Medium work', heavy: 'Heavy work' };
  var LOAD = { ok: 'Running smoothly', tight: 'Close to the limit', heavy: 'Too heavy with this clip: try Half resolution, or another effect' };
  var WHERE = { path: '/api/effects/values', gone: 'Not sent: that effect is not on any more.' };

  function kit() { return window.pvjShaders && window.pvjShaders.kit; }
  // "fx-mirror-quad.fs" reads "Mirror quad", "isf-rgb-invert" reads "RGB invert"
  function nice(name) {
    var words = String(name || '').replace(/\.fs$/, '').replace(/^(fx|isf)-/, '').split(/[-_ ]+/).filter(Boolean)
      .map(function (w) { return UPPER[w.toLowerCase()] || w; });
    var t = words.join(' ');
    return t ? t.charAt(0).toUpperCase() + t.slice(1) : '';
  }
  function player(c) { return (c.state.status && c.state.status.player) || {}; }
  function rowOf(d, id) { return d.effects.filter(function (s) { return s.id === id; })[0] || null; }
  function percent(v) { return Math.round(v * 100) + '%'; }
  function times(v) { return kit().round(v) + '×'; }
  function busyIn(el) {             // a card part is not drawn again while a question waits in it or a field in it has the cursor
    var a = document.activeElement;
    return !!(el && (el.querySelector('#confirmrow') || (a && el.contains(a) && /^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName) && a.type !== 'range')));
  }

  // ---- asking the box -------------------------------------------------------------------------------------------
  function drawAll(c) {
    if (!X.data) return;
    var s = document.getElementById('livefx'), m = document.getElementById('fxcard');
    if (s) drawStrip(c, X.data, s);
    if (m) drawCard(c, X.data, m);
  }
  function load(c) {
    if (X.busy) return false;
    X.busy = true;
    c.api('GET', '/api/effects').then(function (r) {
      X.busy = false; X.at = Date.now();
      if (r.ok) { X.data = r.data; drawAll(c); }
    });
    return true;
  }
  function soon(c, ms) { clearTimeout(soonTimer); soonTimer = setTimeout(function () { load(c); c.poll(); }, ms); }
  // One change. A call that answers with the whole state is drawn at once; the others (a step, a preset, on from a
  // controller) are done by the box's worker a moment later, so the state is asked for again then.
  function send(c, path, body, after) {
    return c.api('POST', path, body).then(function (r) {
      if (!r.ok) c.say(r.data.error || 'The box did not take it.', true);
      else {
        c.say('');
        if (r.data && r.data.effects) { X.data = r.data; X.at = Date.now(); drawAll(c); c.poll(); } else soon(c, 700);
        if (after) after(r.data);
      }
      return r;
    });
  }
  // Called with every status poll. The state is asked for again when what plays has changed hands, every few
  // seconds while an effect is on (its values may be moved from a controller), and now and then otherwise.
  function patch(c) {
    if (!document.getElementById('livefx') && !document.getElementById('fxcard')) return;
    if (!c.moduleOn('shaders')) return;
    var pl = player(c);
    var key = [pl.effect || '', typeof pl.shader === 'string', !!pl.path, !!pl.running, !!pl.capture, !!pl.test_pattern].join('|');
    if (!X.data || key !== X.key || Date.now() - X.at > (pl.effect ? 4000 : 15000)) { if (load(c)) X.key = key; }
  }

  // ---- one control of the common kind: a number with its value beside it --------------------------------------------
  function numberControl(c, rig, o) {
    var k = kit(), h = c.h, note = k.noteLine();
    var ch = rig.channel('c:' + o.key, function (v) { var b = { controls: {} }; b.controls[o.key] = v; return b; }, note.say);
    var out = h('span', { class: 'slidervalue', text: o.text(o.value) }), cur = o.value;
    var input = k.slider({ id: o.id, min: o.min, max: o.max, step: (o.max - o.min) / 100, value: o.value }, ch, function (v, done) {
      cur = v; out.textContent = o.text(v);
      if (done) ch.settle(v); else ch.push(v);
    });
    function reset() { cur = o.reset; input.value = o.reset; out.textContent = o.text(o.reset); ch.send(o.reset); }
    input.addEventListener('dblclick', reset);
    var box = h('div', { class: 'ctl ctl-slider ctl-common' + (o.compact ? ' compact' : ''), 'data-common': o.key });
    box.appendChild(h('div', { class: 'ctlhead' }, h('label', { class: 'ctllabel', for: o.id, text: o.label }), out, note.el));
    box.appendChild(h('div', { class: 'ctlrow' }, input, h('div', { class: 'ctlacts' }, o.extra || null,
      o.compact ? null : h('button', { class: 'btn ctlreset', text: 'Reset', 'aria-label': 'Reset ' + o.label, onclick: reset }))));
    if (o.hint) box.appendChild(h('span', { class: 'hint ctlrange', text: o.hint }));
    return { el: box, ch: ch, apply: function (v) { if (typeof v === 'number' && v !== cur) { cur = v; input.value = v; out.textContent = o.text(v); } } };
  }
  function rigFor(c) {
    return kit().makeRig(c, function () { return X.data && X.data.on ? X.data.on.id : null; }, function () { X.at = Date.now() - 3000; }, WHERE);
  }

  // ---- Live: the strip ----------------------------------------------------------------------------------------------
  function liveStrip(c) {
    if (!c.moduleOn('shaders') || !kit()) return null;
    strip.shape = '';
    var box = c.h('div', { class: 'card fxstrip', id: 'livefx' });
    if (X.data) drawStrip(c, X.data, box);
    else box.appendChild(c.h('div', { class: 'k', text: 'Effect' }));
    return box;
  }
  function drawStrip(c, d, box) {
    var h = c.h, can = c.can('live'), on = d.on;
    var shape = JSON.stringify([d.enabled, d.available, d.unavailable, on && on.id, can]);
    if (shape === strip.shape) {
      if (on && strip.amount && strip.amount.ch.free() && !on.pending) strip.amount.apply(on.controls.amount);
      return;
    }
    strip.shape = shape; strip.amount = null;
    box.textContent = '';
    box.appendChild(h('div', { class: 'row between wrap' },
      h('div', { class: 'fxtitle' }, h('span', { class: 'k', text: 'Effect' }), h('span', { class: 'fxname', id: 'livefxname', text: on ? nice(on.name) : 'None' })),
      h('button', { class: 'btn', id: 'livefxmore', text: 'Effects ›', 'aria-label': 'Effects: the list, all controls and presets', onclick: c.openMix })));
    if (!on && !d.available) box.appendChild(h('div', { class: 'hint', id: 'livefxwhy', text: d.unavailable || 'Not available now.' }));
    if (!can) return;
    var off = !on && !d.available;
    function step(dir) { return function () { send(c, '/api/effects/step', { dir: dir }); }; }
    box.appendChild(h('div', { class: 'row fxbuttons' },
      h('button', { class: 'btn grow', id: 'livefxprev', text: '‹ Previous', 'aria-label': 'The effect before', disabled: off, onclick: step(-1) }),
      on ? h('button', { class: 'btn grow', id: 'livefxoff', text: 'Off', 'aria-label': 'Take the effect off', onclick: function () { send(c, '/api/effects', { off: true }); } })
        : h('button', { class: 'btn grow', id: 'livefxon', text: 'On', 'aria-label': 'Put the last effect back on', disabled: off, onclick: function () { send(c, '/api/effects', { toggle: true }); } }),
      h('button', { class: 'btn grow', id: 'livefxnext', text: 'Next ›', 'aria-label': 'The next effect', disabled: off, onclick: step(1) })));
    if (on) {
      strip.amount = numberControl(c, rigFor(c), { key: 'amount', id: 'live-fx-amount', label: 'Amount', min: 0, max: 1, value: on.controls.amount, reset: 1, text: percent, compact: true });
      box.appendChild(h('div', { class: 'ctls' }, strip.amount.el));
    }
  }

  // ---- MIDI teach buttons (full access, with the MIDI row in view) -----------------------------------------------
  function midiOk(c) { return c.can('full') && c.rowShown('midi'); }
  function what(e) { return (e.source === '*' ? 'any controller' : e.source) + ', ' + (e.kind === 'note' ? 'note ' : e.kind === 'cc' ? 'control ' : 'program ') + e.number; }
  function loadMidi(c) {
    if (!midiOk(c)) return;
    if (!c.moduleOn('control-midi')) { midi.data = null; midi.asked = true; return drawTeachers(c); }
    c.api('GET', '/api/midi').then(function (r) { if (!document.getElementById('fxcard')) return; midi.data = r.ok ? r.data : null; midi.asked = true; drawTeachers(c); });
  }
  function drawTeacher(c, t) {
    var h = c.h, d = midi.data, on = !!(d && d.enabled), mine = on ? d.map.filter(function (e) { return e.action === t.action; }) : [];
    t.btn.className = 'btn midibtn' + (mine.length ? ' mapped' : '');
    t.btn.setAttribute('aria-expanded', ui.open === t.action ? 'true' : 'false');
    t.box.hidden = ui.open !== t.action;
    if (t.box.hidden) return;
    var box = t.box;
    box.textContent = '';
    box.appendChild(h('span', {}, h('b', { text: t.title }), h('br'),
      h('span', { class: 'hint', text: !on ? '' : mine.length ? 'Now on: ' + mine.map(what).join('; ') + '.' : 'Not on any control of your own yet (' + t.kind + ').' })));
    if (!on) {
      box.appendChild(h('div', { class: 'row wrap' }, h('span', { class: 'hint', text: 'MIDI is off.' }),
        h('button', { class: 'btn', text: 'Switch MIDI on', onclick: function () {
          c.switchFeature('midi', true).then(function (r) { if (!r.ok) c.say(r.data.error || 'Could not switch it on.', true); else c.say(''); loadMidi(c); });
        } })));
      return;
    }
    function got(data) { midi.data = data; drawTeachers(c); }
    var learning = d.learn.active && ui.teach === t.action;
    if (learning) {
      box.appendChild(h('div', { class: 'msg', id: 'fxlearning', role: 'status', text: 'Move or press the control now (' + d.learn.seconds_left + ' s)...' }));
      box.appendChild(h('div', { class: 'row' }, h('button', { class: 'btn', text: 'Cancel', onclick: function () {
        clearTimeout(learnTimer); ui.teach = '';
        c.act('POST', '/api/midi/learn', { start: false }, got);
      } })));
    } else {
      box.appendChild(h('div', { class: 'row wrap' },
        h('button', { class: 'btn', text: 'Teach a control', 'aria-label': 'Teach a control: ' + t.title, disabled: d.learn.active, onclick: function () {
          ui.teach = t.action; ui.taught = '';
          c.act('POST', '/api/midi/learn', { start: true }, got);
        } }),
        mine.map(function (e) {
          return h('button', { class: 'btn', text: 'Remove', 'aria-label': 'Remove ' + what(e) + ' from ' + t.title, onclick: function () { c.act('POST', '/api/midi/map', { remove: e.id }, got); } });
        })));
    }
    if (ui.taught && ui.teach === t.action && !learning) box.appendChild(h('div', { class: 'msg', id: 'fxtaught', role: 'status', text: ui.taught }));
  }
  function drawTeachers(c) {
    teachers = teachers.filter(function (t) { return t.box.isConnected; });
    teachers.forEach(function (t) { drawTeacher(c, t); });
    clearTimeout(learnTimer);
    if (midi.data && midi.data.enabled && midi.data.learn.active && ui.teach) pollLearn(c);
  }
  function pollLearn(c) {
    clearTimeout(learnTimer);
    learnTimer = setTimeout(function () {
      if (!document.getElementById('fxcard')) return;
      c.api('GET', '/api/midi').then(function (r) {
        if (!r.ok || !document.getElementById('fxcard')) return;
        var l = r.data.learn;
        if (l.captured) {
          var entry = { source: l.captured.source, kind: l.captured.kind, channel: 0, number: l.captured.number, action: ui.teach };
          return c.act('POST', '/api/midi/map', { add: entry }, function (d) { ui.taught = 'Learned: ' + what(entry) + '.'; midi.data = d; drawTeachers(c); });
        }
        if (l.active) {
          var el = document.getElementById('fxlearning');
          if (el) el.textContent = 'Move or press the control now (' + l.seconds_left + ' s)...';
          return pollLearn(c);
        }
        ui.taught = 'Nothing was moved or pressed. Try again.';
        midi.data = r.data; drawTeachers(c);
      });
    }, 600);
  }
  // {btn, box} for one action: the button goes beside the control, the box under it. Null without full access.
  function teacher(c, action, title, kind) {
    if (!midiOk(c)) return null;
    var t = { action: action, title: title, kind: kind || 'a knob or a fader' };
    t.box = c.h('div', { class: 'teach teachbox', 'data-teach': action, hidden: true });
    t.btn = c.h('button', { class: 'btn midibtn', text: 'MIDI', 'data-midi': action, 'aria-label': 'MIDI controller: ' + title, 'aria-expanded': 'false', onclick: function () {
      ui.open = ui.open === action ? '' : action;
      if (ui.open || !midi.asked) loadMidi(c); else drawTeachers(c);
    } });
    teachers.push(t);
    if (midi.asked) setTimeout(function () { if (t.box.isConnected) drawTeacher(c, t); }, 0);
    return t;
  }

  // ---- Mix: the card ------------------------------------------------------------------------------------------------
  function mixCard(c) {
    var h = c.h;
    var box = h('div', { class: 'card fxcard', id: 'fxcard' }, h('div', { class: 'k', text: 'Effects (beta): a filter over what plays' }));
    card.head = card.ctl = card.pre = card.list = card.add = '';
    card.ctls = []; card.half = null; teachers = [];
    if (!c.moduleOn('shaders') || !kit()) {
      box.appendChild(h('div', { class: 'hint', id: 'fxmsg', text: 'Off. Switch it on under System, Shaders and Vibes (beta).' }));
      return box;
    }
    var stage = h('div', { class: 'fxstage' }, h('div', { id: 'fxhead' }), h('div', { id: 'fxctl' }), h('div', { id: 'fxpre' }));
    var filter = h('input', { class: 'text-input', id: 'fxfilter', type: 'search', placeholder: 'Find an effect by name', 'aria-label': 'Find an effect by name', value: ui.filter });
    filter.addEventListener('input', function () { ui.filter = filter.value; if (X.data) drawList(c, X.data); });
    var weights = h('div', { class: 'seg', id: 'fxweight', role: 'radiogroup', 'aria-label': 'Show effects by how much work they are' },
      [['all', 'All'], ['light', 'Light'], ['medium', 'Medium'], ['heavy', 'Heavy']].map(function (w) {
        return h('button', { class: 'btn segbtn' + (ui.weight === w[0] ? ' on' : ''), role: 'radio', 'aria-checked': ui.weight === w[0] ? 'true' : 'false', 'data-weight': w[0], text: w[1],
          onclick: function () {
            ui.weight = w[0];
            Array.prototype.forEach.call(weights.children, function (b) { var on = b.getAttribute('data-weight') === ui.weight; b.className = 'btn segbtn' + (on ? ' on' : ''); b.setAttribute('aria-checked', on ? 'true' : 'false'); });
            if (X.data) drawList(c, X.data);
          } });
      }));
    var lib = h('div', { class: 'fxlib' }, h('div', { class: 'field', text: 'Effects' }), filter, weights,
      h('div', { class: 'hint', id: 'fxworknote', text: 'How much work an effect is, is counted from its text. Nothing here has been measured on a board yet.' }),
      h('div', { class: 'list', id: 'fxlist' }), h('div', { id: 'fxadd' }));
    box.appendChild(h('div', { class: 'fxgrid' }, stage, lib));
    if (X.data) setTimeout(function () { if (box.isConnected && X.data) drawCard(c, X.data, box); }, 0);
    load(c);
    if (midiOk(c) && !midi.asked) loadMidi(c);
    return box;
  }
  function drawCard(c, d, box) {
    if (!document.getElementById('fxhead')) return;
    drawHead(c, d);
    drawControls(c, d);
    drawPresets(c, d);
    drawList(c, d);
    drawAdd(c, d);
  }
  function loadWords(on) {
    var parts = [LOAD[on.load] || 'Watching the load...'];
    if (typeof on.drops_per_second === 'number' && on.drops_per_second > 0) parts.push(kit().round(on.drops_per_second) + ' dropped frames a second.');
    if (on.pass_ms) parts.push(on.pass_ms + ' ms a frame.');
    return parts.join(' ');
  }
  function drawHead(c, d) {
    var h = c.h, el = document.getElementById('fxhead'), on = d.on, live = c.can('live');
    var shape = JSON.stringify([d.available, d.unavailable, on && on.id, d.last, d.error, live, midiOk(c)]);
    if (shape !== card.head && !busyIn(el)) {
      card.head = shape;
      el.textContent = '';
      el.appendChild(h('div', { class: 'row wrap' }, h('span', { class: 'fxname', id: 'fxname', text: on ? nice(on.name) : 'No effect is on' }),
        h('span', { class: 'chip ' + (on ? 'chip-active' : 'chip-off'), id: 'fxchip', text: on ? 'On' : 'Off' }),
        h('span', { class: 'chip chip-check', id: 'fxpending', hidden: true, text: 'Changing' })));
      if (on) el.appendChild(h('div', { class: 'loadbox', id: 'fxload' }, h('span', { class: 'loadmeter', 'aria-hidden': 'true' }, h('i'), h('i'), h('i')), h('span', { id: 'fxloadwords', role: 'status' })));
      if (!d.available) el.appendChild(h('div', { class: 'msg', id: 'fxwhy', role: 'status', text: d.unavailable || 'Not available now.' }));
      if (!on && d.last) el.appendChild(h('div', { class: 'hint', id: 'fxlast', text: 'The last effect came off: ' + d.last + '.' }));
      if (d.error) el.appendChild(h('div', { class: 'msg err', id: 'fxerror', text: 'The GPU refused ' + nice(d.error.id) + ': ' + d.error.message }));
      if (live) {
        var blocked = !on && !d.available;
        var prev = teacher(c, 'effect_prev', 'The effect before', 'a pad or a button'), tog = teacher(c, 'effect_toggle', 'Effect on / off', 'a pad or a button'),
          next = teacher(c, 'effect_next', 'The next effect', 'a pad or a button');
        el.appendChild(h('div', { class: 'row fxbuttons' },
          h('button', { class: 'btn big grow', id: 'fxprev', text: '‹ Previous', 'aria-label': 'The effect before', disabled: blocked, onclick: function () { send(c, '/api/effects/step', { dir: -1 }); } }),
          on ? h('button', { class: 'btn big grow', id: 'fxoff', text: 'Off', 'aria-label': 'Take the effect off', onclick: function () { send(c, '/api/effects', { off: true }); } })
            : h('button', { class: 'btn big grow', id: 'fxon', text: 'On', 'aria-label': 'Put the last effect back on', disabled: blocked, onclick: function () { send(c, '/api/effects', { toggle: true }); } }),
          h('button', { class: 'btn big grow', id: 'fxnext', text: 'Next ›', 'aria-label': 'The next effect', disabled: blocked, onclick: function () { send(c, '/api/effects/step', { dir: 1 }); } })));
        if (prev) {
          el.appendChild(h('div', { class: 'row fxbuttons fxteachrow' }, [prev, tog, next].map(function (t) { return h('span', { class: 'grow fxteachcell' }, t.btn); })));
          [prev, tog, next].forEach(function (t) { el.appendChild(t.box); });
        }
      }
      el.appendChild(h('div', { class: 'hint', id: 'fxrule', text: 'One effect at a time. It stays on when the clip changes; Stop, a generator shader and Vibes take it off.' }));
    }
    if (on) {
      var lb = document.getElementById('fxload'), w = document.getElementById('fxloadwords'), p = document.getElementById('fxpending');
      var state = LOAD[on.load] ? on.load : 'unknown';
      if (lb) { lb.className = 'loadbox load-' + state; lb.setAttribute('data-load', state); }
      if (w && w.textContent !== loadWords(on)) w.textContent = loadWords(on);
      if (p) p.hidden = !on.pending;
    }
  }
  function drawControls(c, d) {
    var h = c.h, k = kit(), el = document.getElementById('fxctl'), on = d.on, s = on ? rowOf(d, on.id) : null, live = c.can('live');
    var shape = JSON.stringify(s && live ? [s.id, s.speed_max, d.faster, midiOk(c), s.inputs.map(function (i) { return [i.name, i.type, i.min, i.max, i.values, i.labels, i['default']]; })] : null);
    if (shape === card.ctl) {
      if (on && !on.pending) {
        k.syncControls(card.ctls, on);
        if (card.half && card.half.ch.free()) card.half.sw.setAttribute('aria-checked', on.controls.half ? 'true' : 'false');
      }
      return;
    }
    if (busyIn(el)) return;
    card.ctl = shape; card.ctls = []; card.half = null;
    el.textContent = '';
    if (!s || !live) return;
    var rig = rigFor(c), list = h('div', { class: 'ctls', id: 'fxctls' });
    function add(entry, t) { card.ctls.push(entry); list.appendChild(entry.ctl.el); if (t) list.appendChild(t.box); }
    el.appendChild(h('div', { class: 'row between' }, h('div', { class: 'field', text: 'Controls' }),
      h('button', { class: 'btn', id: 'fxresetall', text: 'Reset all', onclick: function () {
        var values = {};
        s.inputs.forEach(function (i) { if (i.type !== 'event') values[i.name] = i['default']; });
        send(c, '/api/effects/values', { id: s.id, values: values, controls: { amount: 1, speed: 1, half: false } });
        card.ctl = '';
      } })));
    // Amount first: the mix between the picture as it is and the filtered one, which every effect has
    var ta = teacher(c, 'effect_amount', 'Amount', 'a knob or a fader');
    add({ common: 'amount', ctl: numberControl(c, rig, { key: 'amount', id: 'fx-amount', label: 'Amount', min: 0, max: 1, value: on.controls.amount, reset: 1, text: percent,
      extra: ta && ta.btn, hint: '0% is the picture as it is, 100% the effect in full' }) }, ta);
    if (typeof s.speed_max === 'number') {
      add({ common: 'speed', ctl: numberControl(c, rig, { key: 'speed', id: 'fx-speed', label: 'Speed', min: 0, max: s.speed_max, value: Math.min(on.controls.speed, s.speed_max), reset: 1, text: times,
        hint: s.speed_max <= 1 ? 'This effect moves by itself' + (s.flashes ? ' and may flash' : '') + ': it is kept at its own pace or slower. (Faster is the owner\'s switch on the Shaders page.)'
          : 'Faster than 1× is allowed on this box: mind people who are sensitive to flashing light.' }) });
    }
    var hnote = k.noteLine(), hch = rig.channel('c:half', function (v) { return { controls: { half: v } }; }, hnote.say);
    var sw = h('button', { class: 'switch', id: 'fx-half', role: 'switch', 'aria-checked': on.controls.half ? 'true' : 'false', 'aria-label': 'Half resolution', onclick: function () {
      var want = sw.getAttribute('aria-checked') !== 'true';
      sw.setAttribute('aria-checked', want ? 'true' : 'false');
      hch.send(want);
    } });
    card.half = { ch: hch, sw: sw };
    list.appendChild(h('div', { class: 'ctl ctl-bool', 'data-common': 'half' }, h('div', { class: 'ctlrow' },
      h('span', { class: 'ctllabel grow', text: 'Half resolution (lighter for the box, a softer picture)' }), hnote.el, sw)));
    var knob = k.knobOf(s.inputs);
    s.inputs.forEach(function (i) {
      var n = knob[i.name], v = on.values[i.name];
      var t = n ? teacher(c, 'effect_control_' + n, 'Knob ' + n + ': ' + (i.label || i.name), i.type === 'float' || (i.type === 'long' && !i.values) ? 'a knob or a fader' : 'a pad, a button or a knob') : null;
      var shown = String(i.label || i.name).toLowerCase() === 'amount' ? Object.assign({}, i, { label: 'Amount (the filter\'s own)' }) : i;
      var ctl = k.inputControl(shown, v === undefined ? i['default'] : v, { h: h, rig: rig, prefix: 'fxin-', extra: t && t.btn, size: { width: 1, height: 1 } });
      if (ctl) add({ name: i.name, ctl: ctl }, t);
    });
    el.appendChild(list);
  }
  function drawPresets(c, d) {
    var h = c.h, el = document.getElementById('fxpre'), on = d.on, s = on ? rowOf(d, on.id) : null, live = c.can('live'), full = c.can('full');
    var shape = JSON.stringify(s && live ? [s.id, s.presets, on.preset, full, ui.more] : null);
    if (shape === card.pre || busyIn(el)) return;
    card.pre = shape;
    el.textContent = '';
    if (!s || !live) return;
    el.appendChild(h('div', { class: 'field', text: 'Presets of ' + nice(s.name) }));
    if (!s.presets.length) el.appendChild(h('div', { class: 'hint', id: 'fxnopresets', text: full ? 'None yet. Set the controls as you like them, give them a name and save.' : 'None yet.' }));
    else el.appendChild(h('div', { class: 'row wrap', id: 'fxpresets' }, s.presets.map(function (name) {
      return h('button', { class: 'btn' + (on.preset === name ? ' on' : ''), 'data-preset': name, 'aria-pressed': on.preset === name ? 'true' : 'false', text: name,
        onclick: function () { send(c, '/api/effects/preset', { name: name }); } });
    })));
    if (!full) return;
    var name = h('input', { class: 'text-input', id: 'fxpresetname', maxlength: 40, placeholder: 'Name, for example Warm', 'aria-label': 'Name of the new preset' });
    function save() {
      if (!name.value.trim()) return c.say('Give the preset a name first.', true);
      send(c, '/api/effects/presets', { action: 'save', name: name.value.trim(), id: s.id }, function () { name.value = ''; name.blur(); card.pre = ''; });
    }
    name.addEventListener('keydown', function (e) { if (e.key === 'Enter') save(); });
    el.appendChild(h('div', { class: 'row' }, name, h('button', { class: 'btn', id: 'fxpresetsave', text: 'Save as preset', onclick: save })));
    el.appendChild(h('div', { class: 'hint', text: 'A preset called default is what Put on uses for this effect.' }));
    if (!s.presets.length) return;
    el.appendChild(h('button', { class: 'btn', id: 'fxpresetmore', 'aria-expanded': ui.more ? 'true' : 'false', text: ui.more ? 'Done' : 'Delete a preset', onclick: function () { ui.more = !ui.more; card.pre = ''; drawPresets(c, X.data); } }));
    if (ui.more) s.presets.forEach(function (p) {
      el.appendChild(h('div', { class: 'row between' }, h('span', { text: p }),
        h('button', { class: 'btn', text: 'Delete', 'aria-label': 'Delete the preset ' + p, onclick: function (ev) {
          c.confirmRow('Delete the preset ' + p + '?', 'Delete', 'Keep it', function () { send(c, '/api/effects/presets', { action: 'delete', id: s.id, name: p }); }, ev.target);
        } })));
    });
  }
  function shownRows(d) {
    var q = ui.filter.trim().toLowerCase();
    return d.effects.filter(function (s) {
      if (ui.weight !== 'all' && s.weight !== ui.weight) return false;
      return !q || nice(s.name).toLowerCase().indexOf(q) >= 0 || s.name.toLowerCase().indexOf(q) >= 0;
    });
  }
  function drawList(c, d) {
    var h = c.h, el = document.getElementById('fxlist'), live = c.can('live'), full = c.can('full');
    if (!el) return;
    var rows = shownRows(d), onId = d.on && d.on.id;
    var shape = JSON.stringify([rows.map(function (s) { return [s.id, s.weight, s.refused, s.error]; }), onId, d.available, live, full]);
    if (shape === card.list || busyIn(el)) return;
    card.list = shape;
    el.textContent = '';
    if (!rows.length) { el.appendChild(h('div', { class: 'hint', id: 'fxnone', text: d.effects.length ? 'No effect matches. Clear the name or choose All.' : 'No effects on this box.' })); return; }
    rows.forEach(function (s) {
      var name = nice(s.name), isOn = s.id === onId;
      var facts = [WORK[s.weight] || '', s.moves ? 'moves by itself' : '', s.flashes ? 'may flash' : '',
        s.source === 'uploaded' ? 'your upload' : (s.pack === 'nxlx' ? '' : 'from the ' + s.pack + ' pack' + (s.credit ? ', ' + s.credit.replace(/^by /i, 'by ') : ''))].filter(Boolean).join(' · ');
      var item = h('div', { class: 'item fxrow' + (isOn ? ' on' : ''), 'data-effect': s.id },
        h('span', { class: 'fxwords' }, h('b', { text: name }), isOn ? h('span', { class: 'chip chip-active', text: 'On' }) : null, h('br'),
          h('span', { class: 'hint', text: facts }),
          s.description ? h('span', { class: 'hint fxdesc', text: s.description }) : null,
          s.error ? h('span', { class: 'msg err', text: 'This file cannot be used: ' + s.error }) : null,
          s.refused ? h('span', { class: 'msg err', text: 'The GPU of this box refused it: ' + s.refused }) : null),
        h('div', { class: 'row' },
          live && !s.error ? (isOn ? h('button', { class: 'btn', 'data-off': s.id, text: 'Off', 'aria-label': 'Take ' + name + ' off', onclick: function () { send(c, '/api/effects', { off: true }); } })
            : h('button', { class: 'btn', 'data-put': s.id, text: 'Put on', 'aria-label': 'Put ' + name + ' on', disabled: !d.available, onclick: function (ev) {
              ev.target.disabled = true; ev.target.textContent = 'Putting on...';
              send(c, '/api/effects', { id: s.id }).then(function (r) { if (!r.ok) { card.list = ''; drawList(c, X.data); } });
            } })) : null,
          full && s.source === 'uploaded' ? h('button', { class: 'btn', text: 'Remove', 'aria-label': 'Remove ' + name, onclick: function (ev) {
            c.confirmRow('Remove ' + name + ' and its presets from the box?', 'Remove', 'Keep it', function () { send(c, '/api/effects/library', { action: 'delete', id: s.id }); }, ev.target);
          } }) : null));
      el.appendChild(item);
    });
  }
  function drawAdd(c, d) {
    var h = c.h, el = document.getElementById('fxadd');
    var shape = JSON.stringify([c.can('full'), ui.upload, d.limits.bytes]);
    if (!el || shape === card.add) return;
    card.add = shape;
    el.textContent = '';
    if (!c.can('full')) return;
    var picker = h('input', { type: 'file', id: 'fxfile', accept: '.fs', hidden: true });
    function told(text, err) { ui.upload = { text: text, err: err }; card.add = ''; if (X.data) drawAdd(c, X.data); }
    picker.addEventListener('change', function () {
      var file = picker.files[0];
      if (!file) return;
      if (file.size > d.limits.bytes) return told(file.name + ' was not added: it is larger than ' + Math.round(d.limits.bytes / 1024) + ' KB.', true);
      var reader = new FileReader();
      reader.onerror = function () { told(file.name + ' was not added: it could not be read.', true); };
      reader.onload = function () {
        c.api('POST', '/api/effects/library', { action: 'upload', name: file.name, source: String(reader.result) }).then(function (r) {
          if (!r.ok) return told(file.name + ' was not added: ' + (r.data.error || 'the box did not take it') + '.', true);
          X.data = r.data;
          told(file.name + ' was added. It is in the list of effects.', false);
          drawAll(c);
        });
      };
      reader.readAsText(file);
    });
    el.appendChild(h('div', { class: 'row' }, h('button', { class: 'btn grow', id: 'fxupload', text: '+ Add an effect file (.fs)', onclick: function () { picker.click(); } }), picker));
    el.appendChild(h('div', { class: 'hint', text: 'An ISF filter: one file that reads the playing picture (inputImage) in one pass. A file that is refused says why.' }));
    el.appendChild(h('div', { class: 'msg' + (ui.upload && ui.upload.err ? ' err' : ''), id: 'fxuploadmsg', role: 'status', text: ui.upload ? ui.upload.text : '' }));
  }

  window.pvjEffects = { liveStrip: liveStrip, mixCard: mixCard, patch: patch, nice: nice };
})();
