// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// The Room screen (see ROOM.md): ambience to start and stop (the Vibes rotation, with the helpers of shaders.js),
// scenes to tap, each group of projectors on or off, its source and its mutes, All off with a second tap, and the
// state of each group at a glance. A view-only device sees the state and no buttons. A
// full-access device also gets the set-up of groups and scenes. Loaded after app.js, which hands its own helpers
// over in `c` (h, api, say, can, moduleOn, state); text only ever goes into the page as textContent.
(function () {
  'use strict';

  var timer = null;        // the next look at /api/room
  var armed = 0;           // when "All off" asked its question (Date.now()); 0: not asking
  var armTimer = null;
  var resume = null;       // looks again when the tab is shown again (nothing is asked while it is hidden)
  document.addEventListener('visibilitychange', function () { if (document.visibilityState !== 'hidden' && resume) resume(); });
  var names = {};          // scene id -> name, for the schedule card
  var streams = null;      // saved streams, read once when a scene may play one
  var follow = null;       // the open screen's ambience card, redrawn from each status poll of app.js (no request of its own)
  var draft = { group: blankGroup(), scene: blankScene() };     // what is being typed; survives redraws

  function blankGroup() { return { id: '', name: '', projectors: [] }; }
  function blankScene() { return { id: '', name: '', noGuests: false, rows: {}, box: { action: 'leave', file: '', loop: true, bank: 0, index: 0, stream: '' } }; }
  function row(gid) {
    if (!draft.scene.rows[gid]) draft.scene.rows[gid] = { power: 'leave', input: '', picture: 'leave', sound: 'leave' };
    return draft.scene.rows[gid];
  }
  function cap(t) { return t.charAt(0).toUpperCase() + t.slice(1); }
  function sourceName(i) { return i.label || i.name; }
  function remember(scenes) { names = {}; scenes.forEach(function (s) { names[s.id] = s.name; }); }

  function screen(c) {
    var h = c.h, live = c.can('live'), full = c.can('full');
    clearTimeout(timer);
    var scenes = h('div', { class: 'card', id: 'roomscenes' }, h('h2', { text: 'Scenes' }), h('div', { class: 'hint', text: 'Loading...' }));
    var groups = h('div', { class: 'room-groups', id: 'roomgroups' });
    var setup = full ? h('div', { class: 'card', id: 'roomsetup' }) : null;
    // Let a guest in without leaving the room's controls: the guest code part of People and codes, opened on request
    // (nothing is asked of the box until then). For whoever may use the buttons here.
    // Not for a support login: the box refuses codes through the support connection, whatever the role.
    var letin = live && c.letIn && !(c.state.device && c.state.device.remote) ? h('details', { class: 'card fold', id: 'roomletin' }, h('summary', { text: 'Let someone in' })) : null;
    if (letin) letin.addEventListener('toggle', function () {
      var old = letin.querySelector('#accesslive');
      if (old) letin.removeChild(old);
      if (letin.open) letin.appendChild(c.letIn());
    });
    var amb = ambience();
    var root = h('div', { class: 'screen', id: 'roomscreen' },
      h('div', { class: 'top' }, h('h1', { text: 'Room' }), live ? null : h('div', { class: 'pill k', id: 'roomviewonly', text: c.guestOpen && c.guestOpen() ? 'Guest' : 'Watching' })),
      h('div', { id: 'msg', class: 'msg' + (c.state.msgErr ? ' err' : ''), role: 'status', text: c.state.msg }),
      amb, scenes, groups, c.guestLock ? c.guestLock() : null, letin, setup);
    var shownLive = null, shownSetup = null, last = null;

    // ---- ambience: the Vibes rotation, under the name staff use for it ----
    // What is playing comes from the status app.js already reads every second, so this asks nothing while it waits.
    // The sets are read once when the screen opens and again when the tab is shown again. A presenter (live access)
    // gets the buttons, a guest one line of state. With the module off: nothing, and for the owner the way to it.
    function ambience() {
      var V = window.pvjShaders && window.pvjShaders.vibes;
      follow = null;
      if (!V) return null;
      if (!c.moduleOn('shaders')) {
        if (!c.owner() || !c.openShaders) return null;      // the way to the module's switch, which is the Owner's
        return h('div', { class: 'card room-amb', id: 'roomamboff' }, h('div', { class: 'row wrap' },
          h('span', { class: 'hint grow', text: 'Ambience (Vibes) is switched off.' }),
          h('button', { class: 'btn', id: 'roomambopen', text: 'Open Shaders and Vibes', onclick: c.openShaders })));
      }
      var nice = window.pvjShaders.nice, data = null, held = null, shown = '';
      var words = h('span', { id: 'roomambwords' }), under = h('span', { class: 'vibessub', id: 'roomambsub' });
      function post(body, after) {
        c.api('POST', '/api/vibes', body).then(function (r) {
          if (!r.ok) { held = null; c.say(r.data.error || 'Something went wrong', true); return draw(); }
          c.say('');
          if (after) after();
          setTimeout(function () { if (document.body.contains(card)) c.poll(); }, 1200);     // the first shader is on within a second
        });
      }
      var big = live ? h('button', { class: 'btn big vibesbig', id: 'roomamb', onclick: function () {
        var on = !state().on;
        held = { on: on, until: Date.now() + 5000 };      // say so at once; the box's own word takes over when it agrees
        draw();
        post(V.body(data, on));
      } }, words, under) : null;
      var next = live ? h('button', { class: 'btn big', id: 'roomambnext', text: 'Next one', 'aria-label': 'Ambience: the next one', hidden: true,
        onclick: function () { post({ next: true }); } }) : null;
      var pick = live ? h('select', { class: 'text-input', id: 'roomambset', hidden: true, 'aria-label': 'The set of shaders ambience plays' }) : null;
      if (pick) pick.addEventListener('change', function () {
        V.choose(pick.value); pick.blur();
        if (state().on && data) post(V.body(data, true));          // while it plays, the chosen set takes over
      });
      var line = live ? null : h('div', { class: 'room-text', id: 'roomambstate', role: 'status' });
      // For the owner only, and only when it applies: the saved picture detail is above this board's usual one.
      var detail = full ? h('div', { class: 'row', id: 'roomambdetail', hidden: true }) : null;
      var card = h('div', { class: 'card room-amb', id: 'roomambience' }, h('h2', { text: 'Ambience' }),
        live ? h('div', { class: 'vibesrow' }, big, next, pick) : line, detail);
      function state() {
        var pl = V.player(c), on = !!pl.vibes;
        if (held && (held.on === on || Date.now() > held.until)) held = null;
        return { on: held ? held.on : on, name: on && !(held && !held.on) ? nice(pl.shader) : '' };
      }
      function sets() {
        if (!pick) return;
        if (!data) {              // not read yet, or the module was switched off meanwhile: no set to choose, no advice
          pick.hidden = true;
          if (detail) detail.hidden = true;
          return;
        }
        if (detail) {
          var high = V.detailHigh(data);
          detail.hidden = !high;
          detail.textContent = '';
          if (high) {
            // one line on a phone: the short form of what the Shaders page says in full
            detail.appendChild(h('span', { class: 'hint grow', id: 'roomambdetailwords', text: 'Picture detail ' + data.config.height + ' is high here.' }));
            detail.appendChild(h('button', { class: 'btn', id: 'roomambuse', text: high.button, onclick: function () {
              c.api('POST', '/api/shaders', high.body).then(function (r) {
                if (!r.ok) return c.say(r.data.error || 'Could not save', true);
                c.say('Picture detail is ' + high.body.height + ' lines now.');
                read();
              });
            } }));
          }
        }
        var shape = JSON.stringify(data.sets.map(function (e) { return [e.id, e.name]; }));
        if (pick.getAttribute('data-shape') !== shape && document.activeElement !== pick) {
          pick.textContent = '';
          data.sets.forEach(function (e) { pick.appendChild(h('option', { value: e.id, text: 'Set: ' + e.name })); });
          pick.setAttribute('data-shape', shape);
        }
        pick.hidden = data.sets.length < 2;
        if (document.activeElement !== pick) pick.value = (data.vibes.running && data.vibes.set && data.vibes.set.id) || (V.startSet(data) || V.activeSet(data) || {}).id || '';
      }
      function draw() {
        var s = state(), key = JSON.stringify(s);
        if (key === shown) return;
        shown = key;
        var playing = 'Ambience is playing' + (s.name ? ': ' + s.name : '');
        if (!live) { line.textContent = s.on ? playing + '.' : 'Ambience is not playing.'; return; }
        words.textContent = s.on ? playing + '.' : 'Start ambience';
        under.textContent = s.on ? 'Tap to stop' : 'Moving pictures, one after another';
        big.className = 'btn big vibesbig' + (s.on ? ' on' : '');
        big.setAttribute('aria-pressed', s.on ? 'true' : 'false');
        next.hidden = !s.on;
      }
      function read() {
        if (!pick) return;
        c.api('GET', '/api/shaders').then(function (r) { if (r.ok && document.body.contains(card)) { data = V.loaded(r.data) ? r.data : null; sets(); } });
      }
      follow = { draw: function () { if (document.body.contains(card)) draw(); else if (follow && follow.card === card) follow = null; }, read: read, card: card };
      draw();
      read();
      return card;
    }

    function send(path, body, said) {
      c.api('POST', path, body).then(function (r) {
        if (!r.ok) c.say(r.data.error || 'Something went wrong', true);
        else c.say(said);
        load(true);
      });
    }
    function edit(body, said, done) {
      c.api('POST', '/api/room', body).then(function (r) {
        if (!r.ok) return c.say(r.data.error || 'Could not save', true);
        keep();
        if (done) done();
        c.say(said);
        show(r.data, true);
      });
    }

    // ---- what everyone sees ----
    function result(job, id, lead) {
      return h('div', { class: 'room-result' + (job.ok ? '' : ' err'), id: id || false, role: 'status', text: (lead || '') + job.text });
    }
    function groupCard(g) {
      var all = g.id === 'all', title = all ? 'Everything' : g.name;
      var kids = [h('div', { class: 'row between' }, h('h2', { text: title }),
        h('span', { class: 'badge room-state state-' + g.state.replace(/ /g, '-'), text: cap(g.state) })),
        h('div', { class: 'room-text', text: g.projectors.length ? g.text : (all ? 'No projectors added yet (System > Projectors).' : 'No projectors in this group.') })];
      if (live && g.projectors.length) {
        var button = function (text, action, extra, cls) {
          var body = { group: g.id, action: action };
          if (extra) body.input = extra;
          return h('button', { class: 'btn grow ' + (cls || 'big'), text: text, 'aria-label': title + ': ' + text, 'data-action': action,
            onclick: function () { send('/api/room/group', body, title + ': ' + text + ', started'); } });
        };
        if (all && armed) {
          // The question takes the place of the two buttons, so the second tap of a double tap lands on it: a
          // "yes" in the first 600 ms after asking is not taken. "No", or 8 seconds, puts the buttons back.
          var n = g.projectors.length, calm = function () { armed = 0; clearTimeout(armTimer); if (last && document.body.contains(root)) drawLive(last); };
          kids.push(h('div', { class: 'confirm', id: 'roomallask', role: 'alert' },
            h('span', { text: n === 1 ? 'Turn off the projector? It needs about a minute to cool before it can come on again.' :
              'Turn off all ' + n + ' projectors? They need about a minute to cool before they can come on again.' }),
            h('div', { class: 'row' },
              h('button', { class: 'btn danger grow big', id: 'roomalloffyes', text: 'Turn off', onclick: function () {
                if (Date.now() - armed < 600) return;
                armed = 0; clearTimeout(armTimer);
                send('/api/room/group', { group: 'all', action: 'off' }, 'Everything: off, started');
              } }),
              h('button', { class: 'btn grow big', id: 'roomallkeep', text: n === 1 ? 'Keep it on' : 'Keep them on', onclick: calm }))));
        } else if (all) {
          kids.push(h('div', { class: 'row wrap' }, button('All on', 'on'),
            h('button', { class: 'btn big grow', id: 'roomalloff', text: 'All off', onclick: function () {
              clearTimeout(armTimer);
              armed = Date.now();
              armTimer = setTimeout(function () { armed = 0; if (last && document.body.contains(root)) drawLive(last); }, 8000);
              drawLive(last);
            } })));
        } else {
          kids.push(h('div', { class: 'row' }, button('On', 'on'), button('Off', 'off')));
          if (g.inputs.length) {
            kids.push(h('div', { class: 'field', text: 'Source' }));
            kids.push(h('div', { class: 'row wrap room-sources' }, g.inputs.map(function (i) {
              var b = button(sourceName(i), 'input', i.input, 'room-source' + (g.input === i.input ? ' on' : ''));
              b.setAttribute('aria-pressed', g.input === i.input ? 'true' : 'false');
              return b;
            })));
          }
          kids.push(h('div', { class: 'row wrap' },
            g.mute.picture ? button('Show picture', 'unmute_picture', null, 'big on') : button('Mute picture', 'mute_picture'),
            g.mute.sound ? button('Sound on', 'unmute_sound', null, 'big on') : button('Mute sound', 'mute_sound')));
        }
      }
      if (g.last) kids.push(result(g.last));
      return h('div', { class: 'card room-group', 'data-id': g.id }, kids);
    }
    function drawLive(d) {
      scenes.textContent = '';
      scenes.appendChild(h('h2', { text: 'Scenes' }));
      if (!d.scenes.length) scenes.appendChild(h('div', { class: 'hint', id: 'roomnoscenes', text: full ? 'No scenes yet. Add one under "Set up the room" below.' : 'No scenes yet.' }));
      else scenes.appendChild(h('div', { class: 'room-scenes' }, d.scenes.map(function (s) {
        var on = !!d.job && d.job.scene === s.id;
        return h('button', { class: 'btn big room-scene' + (on ? ' on' : ''), 'data-id': s.id, text: s.name, disabled: !live, 'aria-pressed': on ? 'true' : 'false',
          onclick: function () { send('/api/room/scene', { scene: s.id }, s.name + ': started'); } });
      })));
      if (d.job) scenes.appendChild(result(d.job, 'roomjob', d.job.name + (d.job.running ? ' (working): ' : ': ')));
      groups.textContent = '';
      d.groups.forEach(function (g) { groups.appendChild(groupCard(g)); });
      if (d.all) groups.appendChild(groupCard(d.all));
    }

    // ---- set-up, full access only ----
    function keep() {  // what is being typed, copied from the page before it is rebuilt (an input event can be lost)
      [['roomgname', draft.group], ['roomsname', draft.scene]].forEach(function (f) {
        var el = document.getElementById(f[0]);
        if (el && setup.contains(el)) f[1].name = el.value;
      });
    }
    function choose(label, options, value, set, id) {
      var el = h('select', { class: 'text-input', 'aria-label': label, id: id || false }, options.map(function (o) {
        return h('option', { value: o[0], text: o[1], selected: String(o[0]) === String(value) });
      }));
      el.addEventListener('change', function () { set(el.value); });
      return el;
    }
    function describe(s, d) {
      var parts = s.groups.map(function (r) {
        var g = r.group === 'all' ? d.all : d.groups.filter(function (x) { return x.id === r.group; })[0];
        var words = [];
        if (r.power !== 'leave') words.push(r.power);
        if (r.input) {
          var i = g ? g.inputs.filter(function (x) { return x.input === r.input; })[0] : null;
          words.push(i ? sourceName(i) : 'input ' + r.input);
        }
        if (r.picture !== 'leave') words.push(r.picture === 'mute' ? 'picture muted' : 'picture shown');
        if (r.sound !== 'leave') words.push(r.sound === 'mute' ? 'sound muted' : 'sound on');
        return (g ? g.name : 'A removed group') + ': ' + words.join(', ');
      });
      var b = s.box;
      if (b.action === 'file') parts.push('Box: play ' + b.file);
      if (b.action === 'pad') parts.push('Box: play pad ' + 'ABCDEFGHIJ'.charAt(b.pad[0]) + (b.pad[1] + 1));
      if (b.action === 'stream') parts.push('Box: play a saved stream');
      if (b.action === 'stop') parts.push('Box: stop');
      if (b.action === 'blackout') parts.push('Box: blackout');
      if (b.action === 'vibes') parts.push('Box: start Vibes');
      if (b.action === 'vibes_stop') parts.push('Box: stop Vibes');
      return parts.join(' · ') || 'Does nothing yet';
    }
    function learn(s) {  // give a MIDI control this scene: the same Learn as System > MIDI controllers
      c.api('POST', '/api/midi/learn', { start: true }).then(function (r) {
        if (!r.ok) return c.say(r.data.error || 'MIDI is not available', true);
        c.say('Move or press a control on a MIDI controller now, for "' + s.name + '"...');
        var tries = 0;
        (function wait() {
          setTimeout(function () {
            if (!document.body.contains(root)) return;
            c.api('GET', '/api/midi').then(function (m) {
              if (!m.ok) return c.say(m.data.error || 'MIDI is not available', true);
              var got = m.data.learn.captured;
              if (got) {
                return c.api('POST', '/api/midi/map', { add: { source: got.source, kind: got.kind, channel: 0, number: got.number, action: 'scene', scene: s.id } }).then(function (a) {
                  c.say(a.ok ? 'That control now applies "' + s.name + '". It is listed under System > MIDI controllers.' : (a.data.error || 'Could not save the mapping'), !a.ok);
                });
              }
              if (m.data.learn.active && ++tries < 40) return wait();
              c.say('Nothing was moved or pressed.', true);
            });
          }, 600);
        })();
      });
    }
    function drawSetup(d, fresh) {
      if (!fresh) keep();      // fresh: the draft was just set or emptied on purpose, the page holds the older text
      setup.textContent = '';
      setup.appendChild(h('h2', { text: 'Set up the room' }));
      if (!d.projectors.length) setup.appendChild(h('div', { class: 'empty', id: 'roomnoproj' },
        h('div', { text: 'No projectors yet. A room is made of projectors, so the first step is to add one.' }),
        c.openProjectors ? h('div', { class: 'row' }, h('button', { class: 'btn on pri grow', id: 'roomaddproj', text: 'Add a projector', onclick: c.openProjectors })) : null));
      // groups
      setup.appendChild(h('div', { class: 'field', text: 'Groups' }));
      setup.appendChild(h('div', { class: 'hint', text: '"All" is always there.' }));
      var glist = h('div', { class: 'list', id: 'roomglist' });
      d.groups.forEach(function (g) {
        var members = d.projectors.filter(function (p) { return g.projectors.indexOf(p.id) >= 0; }).map(function (p) { return p.name; });
        glist.appendChild(h('div', { class: 'item room-gitem' }, h('span', {}, g.name, h('br'), h('span', { class: 'addr', text: members.join(', ') || 'no projectors left' })),
          h('div', { class: 'row' },
            h('button', { class: 'btn small', text: 'Edit', 'aria-label': 'Edit group ' + g.name, onclick: function () {
              keep(); draft.group = { id: g.id, name: g.name, projectors: g.projectors.slice() }; drawSetup(last, true);
            } }),
            h('button', { class: 'btn small', text: 'Remove', 'aria-label': 'Remove group ' + g.name, onclick: function (e) {
              c.confirmRow('Remove the group ' + g.name + '? Scenes that use it lose that part. The projectors stay.', 'Remove', 'Keep it', function () {
                edit({ remove_group: g.id }, 'Group removed');
              }, e.currentTarget);
            } }))));
      });
      setup.appendChild(glist);
      var gname = h('input', { class: 'text-input', id: 'roomgname', 'aria-label': 'Group name', placeholder: 'Group name (Main wall)', maxlength: d.limits.name, value: draft.group.name });
      gname.addEventListener('input', function () { draft.group.name = gname.value; });
      setup.appendChild(gname);
      setup.appendChild(h('div', { class: 'row wrap', id: 'roomgmembers' }, d.projectors.map(function (p) {
        var on = draft.group.projectors.indexOf(p.id) >= 0;
        return h('button', { class: 'btn small room-member' + (on ? ' on' : ''), text: p.name, 'aria-pressed': on ? 'true' : 'false', 'aria-label': p.name + ' in this group', onclick: function () {
          var at = draft.group.projectors.indexOf(p.id);
          if (at >= 0) draft.group.projectors.splice(at, 1); else draft.group.projectors.push(p.id);
          drawSetup(last);
        } });
      })));
      setup.appendChild(h('div', { class: 'row' },
        h('button', { class: 'btn on pri small', id: 'roomgsave', text: draft.group.id ? 'Save group' : 'Add group', onclick: function () {
          keep();
          var known = d.projectors.map(function (p) { return p.id; });
          var g = { name: draft.group.name, projectors: draft.group.projectors.filter(function (p) { return known.indexOf(p) >= 0; }) };
          if (draft.group.id) g.id = draft.group.id;
          if (!g.projectors.length) return c.say('Choose at least one projector for the group.', true);
          edit({ group: g }, 'Group saved', function () { draft.group = blankGroup(); });
        } }),
        draft.group.id ? h('button', { class: 'btn small', id: 'roomgcancel', text: 'Cancel', onclick: function () { keep(); draft.group = blankGroup(); drawSetup(last, true); } }) : null));
      // scenes
      setup.appendChild(h('div', { class: 'field', text: 'Scenes' }));
      var slist = h('div', { class: 'list', id: 'roomslist' });
      d.scenes.forEach(function (s) {
        slist.appendChild(h('div', { class: 'item room-sitem' }, h('span', {}, s.name, h('br'), h('span', { class: 'addr', text: describe(s, d) + (s.no_guests === true ? ' · Not for guests' : '') })),
          h('div', { class: 'row' },
            c.moduleOn('control-midi') && c.owner() ? h('button', { class: 'btn small', text: 'MIDI', 'aria-label': 'Learn a MIDI control for ' + s.name, onclick: function () { learn(s); } }) : null,
            h('button', { class: 'btn small', text: 'Edit', 'aria-label': 'Edit scene ' + s.name, onclick: function () {
              keep();
              var rows = {};
              s.groups.forEach(function (r) { rows[r.group] = { power: r.power, input: r.input, picture: r.picture, sound: r.sound }; });
              var b = s.box;
              draft.scene = { id: s.id, name: s.name, noGuests: s.no_guests === true, rows: rows, box: { action: b.action, file: b.file || '', loop: b.loop !== false,
                bank: b.pad ? b.pad[0] : 0, index: b.pad ? b.pad[1] : 0, stream: b.stream || '' } };
              drawSetup(last, true);
            } }),
            h('button', { class: 'btn small', text: 'Remove', 'aria-label': 'Remove scene ' + s.name, onclick: function (e) {
              c.confirmRow('Remove the scene ' + s.name + '? A schedule entry or a controller that starts it will do nothing.', 'Remove', 'Keep it', function () {
                edit({ remove_scene: s.id }, 'Scene removed', function () { if (draft.scene.id === s.id) draft.scene = blankScene(); });
              }, e.currentTarget);
            } }))));
      });
      setup.appendChild(slist);
      setup.appendChild(h('div', { class: 'field', id: 'roomsformtitle', text: draft.scene.id ? 'Change the scene' : 'Add a scene: what each group gets when it is tapped' }));
      var sname = h('input', { class: 'text-input', id: 'roomsname', 'aria-label': 'Scene name', placeholder: 'Scene name (Movie night)', maxlength: d.limits.name, value: draft.scene.name });
      sname.addEventListener('input', function () { draft.scene.name = sname.value; });
      setup.appendChild(sname);
      var targets = d.groups.concat(d.all ? [d.all] : []);
      targets.forEach(function (g) {
        var r = row(g.id), title = g.id === 'all' ? 'All projectors' : g.name;
        var sources = [['', 'Source: leave']].concat(g.inputs.map(function (i) { return [i.input, sourceName(i)]; }));
        if (r.input && !g.inputs.some(function (i) { return i.input === r.input; })) sources.push([r.input, 'Input ' + r.input]);
        setup.appendChild(h('div', { class: 'field', text: title }));
        setup.appendChild(h('div', { class: 'room-row', 'data-group': g.id },
          choose('Power of ' + title, [['leave', 'Power: leave'], ['on', 'Switch on'], ['off', 'Switch off']], r.power, function (v) { r.power = v; }),
          choose('Source of ' + title, sources, r.input, function (v) { r.input = v; }),
          choose('Picture of ' + title, [['leave', 'Picture: leave'], ['mute', 'Picture muted'], ['unmute', 'Picture shown']], r.picture, function (v) { r.picture = v; }),
          choose('Sound of ' + title, [['leave', 'Sound: leave'], ['mute', 'Sound muted'], ['unmute', 'Sound on']], r.sound, function (v) { r.sound = v; })));
      });
      var b = draft.scene.box, media = c.state.media || [];
      var kinds = [['leave', 'The box: leave alone'], ['file', 'The box plays a clip'], ['pad', 'The box plays a pad']];
      if (c.moduleOn('inputs-srt')) kinds.push(['stream', 'The box plays a saved stream']);
      if (c.moduleOn('shaders')) kinds.push(['vibes', 'The box starts Vibes (shaders)'], ['vibes_stop', 'The box stops Vibes']);
      kinds.push(['stop', 'The box stops its clip'], ['blackout', 'The box goes black']);
      if (!b.file && media.length) b.file = media[0];
      var file = choose('Clip to play', media.map(function (n) { return [n, n]; }), b.file, function (v) { b.file = v; }, 'roomsfile');
      var loop = choose('At the end of the clip', [['1', 'Loop the clip'], ['0', 'Play it once']], b.loop ? '1' : '0', function (v) { b.loop = v === '1'; }, 'roomsloop');
      var bank = choose('Bank', (c.state.banks || []).map(function (x, i) { return [i, x.name]; }), b.bank, function (v) { b.bank = parseInt(v, 10); }, 'roomsbank');
      var index = choose('Pad', Array.apply(null, Array(12)).map(function (_, i) { return [i, 'Pad ' + (i + 1)]; }), b.index, function (v) { b.index = parseInt(v, 10); }, 'roomspad');
      var stream = choose('Stream to play', (streams || []).map(function (x) { return [x.id, x.name]; }), b.stream, function (v) { b.stream = v; }, 'roomsstream');
      function params() {
        file.hidden = loop.hidden = b.action !== 'file';
        bank.hidden = index.hidden = b.action !== 'pad';
        stream.hidden = b.action !== 'stream';
      }
      var kind = choose('What the box does', kinds, b.action, function (v) {
        b.action = v; params();
        if (v === 'stream' && streams === null) {
          c.api('GET', '/api/streams').then(function (r) { streams = r.ok ? r.data.streams : []; if (document.body.contains(root)) drawSetup(last); });
        }
      }, 'roomsbox');
      params();
      setup.appendChild(kind); setup.appendChild(file); setup.appendChild(loop); setup.appendChild(bank); setup.appendChild(index); setup.appendChild(stream);
      // "Not for guests" (D80): guests may apply every scene while guest controls are open, except one marked here
      var noGuests = h('input', { type: 'checkbox', id: 'roomsnoguests', checked: draft.scene.noGuests });
      noGuests.addEventListener('change', function () { draft.scene.noGuests = noGuests.checked; });
      setup.appendChild(h('label', { class: 'row', id: 'roomsnoguestsrow' }, noGuests, h('span', { text: 'Not for guests' })));
      setup.appendChild(h('div', { class: 'hint', text: 'While guest controls are open a guest can apply every scene, with what it switches and mutes. Tick this for a scene only staff should start. A scene that plays a stream is never a guest\'s.' }));
      setup.appendChild(h('div', { class: 'row' },
        h('button', { class: 'btn on pri small', id: 'roomssave', text: draft.scene.id ? 'Save scene' : 'Add scene', onclick: function () {
          keep();
          var out = { name: draft.scene.name, groups: [], box: { action: b.action } };
          if (draft.scene.id) out.id = draft.scene.id;
          if (draft.scene.noGuests) out.no_guests = true;
          targets.forEach(function (g) {
            var r = row(g.id);
            if (r.power === 'leave' && !r.input && r.picture === 'leave' && r.sound === 'leave') return;
            out.groups.push({ group: g.id, power: r.power, input: r.input, picture: r.picture, sound: r.sound });
          });
          if (b.action === 'file') { if (!b.file) return c.say('Upload a clip first.', true); out.box.file = b.file; out.box.loop = b.loop; }
          if (b.action === 'pad') out.box.pad = [b.bank, b.index];
          if (b.action === 'stream') { if (!b.stream && streams && streams.length) b.stream = streams[0].id; if (!b.stream) return c.say('Save a stream under System > Streams first.', true); out.box.stream = b.stream; }
          edit({ scene: out }, 'Scene saved', function () { draft.scene = blankScene(); });
        } }),
        draft.scene.id ? h('button', { class: 'btn small', id: 'roomscancel', text: 'Cancel', onclick: function () { keep(); draft.scene = blankScene(); drawSetup(last, true); } }) : null));
      setup.appendChild(h('div', { class: 'hint', text: 'A scene switches on first, then chooses the source once the projector is ready, then mutes. Tapping another scene replaces the one still under way.' }));
    }

    // ---- loading ----
    function show(d, force) {
      last = d;
      remember(d.scenes);
      if (!d.enabled) {
        scenes.textContent = '';
        scenes.appendChild(h('h2', { text: 'Scenes' }));
        scenes.appendChild(h('div', { class: 'hint warn', id: 'roommsg' },
          h('div', { text: 'Projectors is switched off, so the room can do nothing.' + (c.owner() ? '' : ' Ask the owner to switch it on.') }),
          c.owner() && c.switchFeature ? h('div', { class: 'row' }, h('button', { class: 'btn', id: 'roomprojon', text: 'Switch Projectors on', onclick: function () {
            c.switchFeature('projectors', true).then(function (r) {
              if (!r.ok) return c.say(r.data.error || 'Could not switch Projectors on.', true);
              c.say('Projectors is switched on.');
              load(true);
            });
          } })) : null));
        groups.textContent = '';
        if (setup) setup.textContent = '';
        shownLive = shownSetup = null;
        return;
      }
      var a = JSON.stringify([d.scenes.map(function (s) { return [s.id, s.name]; }), d.groups, d.all, d.job, armed]);
      if (a !== shownLive) { shownLive = a; drawLive(d); }
      if (!setup) return;
      var plain = function (g) { return [g.id, g.name, g.projectors, g.inputs]; };
      var b = JSON.stringify([d.scenes, d.groups.map(plain), d.all ? d.all.inputs : null, d.projectors]);
      var at = document.activeElement;
      var typing = at && setup.contains(at) && /^(INPUT|SELECT)$/.test(at.tagName);
      if (force || (b !== shownSetup && !typing && !setup.querySelector('#confirmrow'))) { shownSetup = b; drawSetup(d, force); }
    }
    var wait = 2000;       // between two looks; longer after each one that fails, back to 2 seconds when one works
    function load(now) {
      clearTimeout(timer);
      resume = function () { if (document.body.contains(root)) { load(true); if (follow) follow.read(); } };
      if (!c.state.device) return;                                       // no longer paired: nothing more is asked
      if (!now && document.visibilityState === 'hidden') return;         // a hidden tab asks nothing; `resume` looks again
      c.api('GET', '/api/room').then(function (r) {
        if (!document.body.contains(root)) return;
        clearTimeout(timer);
        wait = r.ok ? 2000 : Math.min(wait * 2, 30000);
        if (!c.state.device) return;
        if (follow) follow.draw();
        timer = setTimeout(function () { if (document.body.contains(root)) load(); }, wait);
        if (!r.ok) {
          if (last === null) { scenes.textContent = ''; scenes.appendChild(h('h2', { text: 'Scenes' })); scenes.appendChild(h('div', { class: 'hint', id: 'roommsg', text: r.data.error || 'Not available' })); }
          return;
        }
        show(r.data);
      });
    }
    load(true);
    return root;
  }

  // The schedule card's "Apply a Room scene": one more action and a chooser for the scene. `redraw` is called
  // once the scene names are known, so entries show a name and not an id.
  function scheduleField(c, action, form, redraw) {
    // The schedule's own list has the choice (marked when Room is off); this adds only the scene to choose.
    if (!c.moduleOn('room')) return null;
    if (!action.querySelector('option[value="scene"]')) action.appendChild(c.h('option', { value: 'scene', text: 'Apply a Room scene', selected: form.action === 'scene' }));
    var ids = Object.keys(names);
    if (!names[form.scene]) form.scene = ids.length ? ids[0] : '';
    var pick = c.h('select', { class: 'text-input', id: 'schedscene', 'aria-label': 'Scene to apply', hidden: form.action !== 'scene' },
      ids.map(function (id) { return c.h('option', { value: id, text: names[id], selected: id === form.scene }); }));
    pick.addEventListener('change', function () { form.scene = pick.value; });
    action.addEventListener('change', function () { pick.hidden = action.value !== 'scene'; });
    var before = JSON.stringify(names);
    c.api('GET', '/api/room').then(function (r) {
      if (!r.ok) return;
      remember(r.data.scenes);
      if (JSON.stringify(names) !== before && document.body.contains(pick)) redraw();
    });
    return pick;
  }
  function sceneName(id) { return names[id] || '(a scene that was removed, or the Room module is off)'; }

  window.pvjRoom = { screen: screen, scheduleField: scheduleField, sceneName: sceneName, patch: function () { if (follow) follow.draw(); } };
})();
