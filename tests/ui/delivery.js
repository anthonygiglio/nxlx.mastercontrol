// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// A file of the page that the box does not deliver (D68, pvj/web/load.js). The harness withholds a named file on
// request (tests/ui/harness.py, the file <delivery_file>: one line a request, "drop", "busy" or "broken"), so each
// case here happens exactly once and on purpose; nothing waits for a failure that may or may not come.
//
//   check(o)    o.fresh(): a new page nobody is paired on, as { pg, said } (said: what its console printed);
//               o.page: a page paired with full access; o.base, o.info: the harness's address and its first line
//
// What is held: a script or style sheet that is cut off or answered with 503 is asked for again and the panel is
// whole without a word; after two more requests the page says which file is missing and offers Try again, which asks
// once more in place and loads nothing again (a scanned code stays in its field), and asks no further by itself; a
// script that arrives and cannot run is not asked for again and is named as a fault of the file, app.js too, which
// used to leave an empty page; the count of new requests is for one load, not for the life of the page; the waits
// are not the same for everybody; the notice stays at the top of the window, fits at 320 px and can be hidden; the
// panel draws again when a part arrives late, but never under somebody typing; and none of it breaks the page's
// Content-Security-Policy.
'use strict';

const fs = require('fs');
const assert = require('assert');

const LOST = (files) => 'Part of this page did not arrive from the box (' + files + '), so some of the panel is missing. The box keeps playing. Press Try again.';
const BROKEN = (files) => 'Part of this page arrived but did not start (' + files + '), so some of the panel is missing. This is a fault in the panel, not in the connection; please report it.';
const SCANNED = 'Reload forgets the code you scanned. Join first, or scan the code again afterwards.';
const PARTS = ['pvjShaders', 'pvjEffects', 'pvjApp', 'pvjRoom'];
const pause = (ms) => new Promise((done) => setTimeout(done, ms));

/* eslint-disable no-undef */
function look() {
  return {
    asked: Object.assign({}, window.pvjLoad.asked), lost: window.pvjLoad.lost.slice(), broken: window.pvjLoad.broken.slice(),
    // every new request this page made, by file (the waits themselves are looked at apart: they differ by chance)
    again: window.pvjLoad.waits.map((w) => w[0]).sort(),
    parts: ['pvjShaders', 'pvjEffects', 'pvjApp', 'pvjRoom'].filter((n) => !!window[n]),
    drawn: !!document.querySelector('#app > *'),
    note: Array.from(document.querySelectorAll('#loadnote p')).map((p) => p.textContent),
    buttons: Array.from(document.querySelectorAll('#loadnote button')).map((b) => b.id),
    // (a sheet that was not delivered is still in the list, and its rules cannot even be asked for)
    sheets: Array.from(document.styleSheets).map((s) => { let n = 0; try { n = s.cssRules.length; } catch (e) { n = 0; } return new URL(s.href).pathname + (n ? '' : ' (empty)'); }),
  };
}
// Where the notice is and whether it can be used, in the window as it is now.
function place() {
  const note = document.getElementById('loadnote'), r = note.getBoundingClientRect(), st = getComputedStyle(note);
  const buttons = Array.from(note.querySelectorAll('button')).map((b) => {
    const q = b.getBoundingClientRect(), hit = document.elementFromPoint(q.left + q.width / 2, q.top + q.height / 2);
    return { id: b.id, inside: q.left >= 0 && q.right <= innerWidth && q.top >= 0 && q.bottom <= innerHeight, tall: q.height >= 44, free: hit === b };
  });
  return { position: st.position, role: note.getAttribute('role'), left: r.left, top: r.top, width: r.width, low: r.bottom <= innerHeight * 0.6 + 1,
    sideways: document.documentElement.scrollWidth > innerWidth, scrolled: scrollY > 0, cursorInIt: note.contains(document.activeElement), buttons };
}
const isWhole = () => !!window.pvjLoad && !document.querySelector('#loadnote') && !!document.querySelector('#app > *') && !!window.pvjShaders && !!window.pvjEffects && !!window.pvjApp && !!window.pvjRoom &&
  document.styleSheets.length === 2 && Array.from(document.styleSheets).every((s) => { try { return s.cssRules.length > 0; } catch (e) { return false; } });
// the notice is up with that button, and the panel has drawn (both: the notice may come first)
const offersAgain = () => !!document.querySelector('#loadnote #loadagain') && !!document.querySelector('#app > *');
const offersReload = () => !!document.querySelector('#loadnote #loadreload') && !!document.querySelector('#app > *');
const code = () => [document.querySelector('#joincode').value, location.hash, location.pathname];
const typing = () => { const f = document.getElementById('latefield'); return [f ? f.value : null, !!f && document.activeElement === f, !!document.querySelector('#app > [data-was]')]; };
/* eslint-enable no-undef */

async function check(o) {
  const { info, base } = o;
  const plan = (lines) => fs.writeFileSync(info.delivery_file, lines.map((l) => l + '\n').join(''));
  const left = () => fs.readFileSync(info.delivery_file, 'utf8').trim();
  const { pg, said } = await o.fresh();
  const WHOLE = { asked: {}, lost: [], broken: [], again: [], parts: PARTS, drawn: true, note: [], buttons: [], sheets: ['/theme.css', '/app.css'] };
  const whole = (again) => Object.assign({}, WHOLE, { again });
  const waits = [];          // every wait before a new request that any of these pages chose: [path, ms]
  const seen = async () => { const got = await pg.evaluate(look); waits.push(...await pg.evaluate(() => window.pvjLoad.waits)); return got; };   // eslint-disable-line no-undef
  // address: what follows the box's own, as a scanned code does ("#code=..."). Through an empty page first, since
  // going from the panel to the panel with only that part different loads nothing.
  const open = async (lines, ready, address) => {
    plan(lines);
    if (address) await pg.goto('about:blank');
    await pg.goto(base + '/' + (address || ''));
    await pg.waitForFunction(ready || isWhole, null, { timeout: 15000 });
    return seen();
  };

  // every file arrives: nothing is asked for twice, nothing is said
  assert.deepStrictEqual(await open([]), WHOLE, 'a page whose files all arrive');

  // cut off once (what a full queue was to a browser on the Mac): asked for again, whole, silent
  assert.deepStrictEqual(await open(['/room.js drop']), whole(['/room.js']), 'room.js cut off once');
  assert.strictEqual(left(), '', 'the harness withheld what it was told to');
  assert.deepStrictEqual(await open(['/app.js drop']), whole(['/app.js']), 'app.js cut off once: the panel still starts');
  assert.deepStrictEqual(await open(['/shaders.js drop', '/effects.js busy']), whole(['/effects.js', '/shaders.js']), 'two scripts at once, one cut off and one answered 503');
  // a style sheet, answered 503 as at the box's cap of connections: asked for again, and still before or after the other as the page has them
  assert.deepStrictEqual(await open(['/theme.css busy']), whole(['/theme.css']), 'theme.css answered 503 once');
  assert.deepStrictEqual(await open(['/app.css drop', '/app.css busy']), whole(['/app.css', '/app.css']), 'app.css missing twice: the second new request brings it');
  {
    // the first wait is the short one and the second the long one, each within a quarter of its length
    const [[, first], [, second]] = await pg.evaluate(() => window.pvjLoad.waits);   // eslint-disable-line no-undef
    assert(first >= 300 && first <= 500 && second >= 1125 && second <= 1875, 'the two waits: ' + first + ' ms, ' + second + ' ms');
  }

  // The two new requests are for one load, not for the life of the page. The theme's sheet is missing twice and
  // comes; later the look is changed, which asks for the same path again in the same place (app.js, the Appearance
  // card: the href of the link gets a new ?v=), and that request is cut off once. It is asked for again and comes.
  assert.deepStrictEqual(await open(['/theme.css drop', '/theme.css busy']), whole(['/theme.css', '/theme.css']), 'theme.css missing twice');
  plan(['/theme.css drop']);
  await pg.evaluate(() => document.querySelector('link[href^="/theme.css"]').setAttribute('href', '/theme.css?v=' + Date.now()));   // eslint-disable-line no-undef
  await pg.waitForFunction(() => !!document.querySelector('#loadnote') || (window.pvjLoad.waits.length === 3 && !window.pvjLoad.asked['/theme.css'] &&   // eslint-disable-line no-undef
    Array.from(document.styleSheets).every((s) => { try { return s.cssRules.length > 0; } catch (e) { return false; } })), null, { timeout: 15000 });   // eslint-disable-line no-undef
  assert.deepStrictEqual(await seen(), whole(['/theme.css', '/theme.css', '/theme.css']), 'the look changed on a page that had used both new requests once: the sheet is asked for again');
  assert.strictEqual(left(), '');

  // three times: the page says so, offers Try again and asks no further (the fourth line is still there afterwards)
  let got = await open(['/room.js drop', '/room.js busy', '/room.js drop', '/room.js drop'], offersAgain);
  const lostRoom = Object.assign({}, WHOLE, { asked: { '/room.js': 2 }, again: ['/room.js', '/room.js'], lost: ['/room.js'], parts: PARTS.filter((p) => p !== 'pvjRoom'), note: [LOST('room.js')], buttons: ['loadagain', 'loadhide'] });
  assert.deepStrictEqual(got, lostRoom, 'room.js missing three times');
  await pause(2500);
  assert.strictEqual(left(), '/room.js drop', 'after the two new requests the page asks for the file no more');
  assert.deepStrictEqual(await pg.evaluate(look), lostRoom);

  // The notice is at the top of the window wherever the page has been scrolled to, above everything, pushes nothing
  // sideways, and its buttons can be pressed; on the narrowest phone too. It says it is an alert and does not take
  // the cursor.
  for (const [width, height] of [[390, 844], [320, 568]]) {
    await pg.setViewportSize({ width, height });
    await pg.evaluate(() => { document.body.style.minHeight = '3000px'; window.scrollTo(0, 1800); });   // eslint-disable-line no-undef
    const at = await pg.evaluate(place);
    assert.deepStrictEqual(at, { position: 'fixed', role: 'alert', left: 0, top: 0, width, low: true, sideways: false, scrolled: true, cursorInIt: false,
      buttons: ['loadagain', 'loadhide'].map((id) => ({ id, inside: true, tall: true, free: true })) }, 'the notice at ' + width + ' px: ' + JSON.stringify(at));
    await pg.evaluate(() => { document.body.style.minHeight = ''; window.scrollTo(0, 0); });   // eslint-disable-line no-undef
  }
  await pg.setViewportSize({ width: 390, height: 844 });

  // Try again is the visitor's own press. It asks once more where the file is and loads nothing again: what the
  // page held is still there. (The last line cuts room.js off once more, and it is asked for again by itself.)
  await pg.evaluate(() => { window.stillHere = true; });   // eslint-disable-line no-undef
  await pg.click('#loadagain');
  await pg.waitForFunction(isWhole, null, { timeout: 15000 });
  assert.deepStrictEqual(await seen(), whole(['/room.js', '/room.js', '/room.js', '/room.js']), 'after Try again');
  assert.strictEqual(await pg.evaluate(() => window.stillHere), true, 'Try again did not load the page again');   // eslint-disable-line no-undef
  assert.strictEqual(left(), '');

  // A code that was scanned (it comes in the address, and app.js takes it out at once) is still in its field after
  // a file was missing and Try again brought it: the page was not loaded again, so nothing had to keep the code.
  got = await open(['/app.css drop', '/app.css busy', '/app.css drop'], offersAgain, '#code=123456');
  assert.deepStrictEqual(got, Object.assign({}, WHOLE, { asked: { '/app.css': 2 }, again: ['/app.css', '/app.css'], lost: ['/app.css'], note: [LOST('app.css')], buttons: ['loadagain', 'loadhide'], sheets: ['/theme.css', '/app.css (empty)'] }),
    'app.css missing three times on a page opened from a QR code');
  assert.deepStrictEqual(await pg.evaluate(code), ['123456', '', '/'], 'the scanned code is in its field and out of the address');
  await pg.click('#loadagain');
  await pg.waitForFunction(isWhole, null, { timeout: 15000 });
  assert.deepStrictEqual(await seen(), whole(['/app.css', '/app.css', '/app.css']), 'after Try again on the page with the code');
  assert.deepStrictEqual(await pg.evaluate(code), ['123456', '', '/'], 'the scanned code is still in its field after Try again');

  // a script that arrives and cannot run is a fault of the file: not asked for again, and named as that
  const brokenRoom = Object.assign({}, WHOLE, { broken: ['/room.js'], parts: PARTS.filter((p) => p !== 'pvjRoom'), note: [BROKEN('room.js')], buttons: ['loadreload', 'loadhide'] });
  assert.deepStrictEqual(await open(['/room.js broken'], offersReload), brokenRoom, 'room.js arrives and cannot run');
  // Hide takes the notice away and nothing else
  await pg.click('#loadhide');
  assert.deepStrictEqual(await pg.evaluate(look), Object.assign({}, brokenRoom, { note: [], buttons: [] }), 'after Hide');
  // On a page opened from a QR code the notice says what Reload costs before anybody presses it; and it does cost that.
  got = await open(['/room.js broken'], offersReload, '#code=123456');
  assert.deepStrictEqual(got, Object.assign({}, brokenRoom, { note: [BROKEN('room.js'), SCANNED] }), 'room.js cannot run on a page opened from a QR code');
  assert.deepStrictEqual(await pg.evaluate(code), ['123456', '', '/']);
  await pg.click('#loadreload');
  await pg.waitForFunction(() => !!window.pvjRoom && !document.querySelector('#loadnote') && !!document.querySelector('#joincode'), null, { timeout: 15000 });   // eslint-disable-line no-undef
  assert.deepStrictEqual(await pg.evaluate(code), ['', '', '/'], 'Reload loads the page again, and the code is gone as the notice said');

  // app.js itself arrives and cannot be read as a program (a merge gone wrong, a browser too old for it): the page
  // used to stay empty and silent. Now it is named like any other, since app.js says when it has started (pvjApp).
  got = await open(['/app.js broken'], () => !!document.querySelector('#loadnote #loadreload') && !!window.pvjRoom);   // eslint-disable-line no-undef
  assert.deepStrictEqual(got, Object.assign({}, WHOLE, { broken: ['/app.js'], parts: PARTS.filter((p) => p !== 'pvjApp'), drawn: false, note: [BROKEN('app.js')], buttons: ['loadreload', 'loadhide'] }),
    'app.js arrives and cannot run: the page says so');

  // The waits before the new requests above: each within a quarter of 0.4 s or of 1.5 s, and not everybody's the same.
  const chosen = waits.map((w) => w[1]).filter((ms) => ms > 0);
  assert(chosen.length >= 10 && chosen.every((ms) => (ms >= 300 && ms <= 500) || (ms >= 1125 && ms <= 1875)), 'the waits: ' + chosen.join(' '));
  assert(chosen.some((ms) => ms !== 400 && ms !== 1500), 'the waits differ by chance, they are not all 400 and 1500: ' + chosen.join(' '));

  // Nobody is paired on this page: a part arriving late draws nothing again, so a PIN being typed stays.
  await open([]);
  await pg.evaluate(() => { document.querySelector('input.pin').value = '7'; document.querySelector('#app > *').setAttribute('data-was', '1'); document.dispatchEvent(new Event('pvjfile')); });   // eslint-disable-line no-undef
  assert.deepStrictEqual(await pg.evaluate(() => [document.querySelector('input.pin').value, !!document.querySelector('#app > [data-was]')]), ['7', true], 'the connect screen is left alone');   // eslint-disable-line no-undef

  // On a paired page the panel draws again with the part that came late (app.js, the listener for "pvjfile").
  const late = o.page;
  assert.strictEqual(await late.evaluate(() => { if (document.activeElement) document.activeElement.blur(); document.querySelector('#app > *').setAttribute('data-was', '1'); document.dispatchEvent(new Event('pvjfile')); return !!document.querySelector('#app > [data-was]'); }), false,   // eslint-disable-line no-undef
    'the panel draws again when a part arrives late');
  // But not under somebody typing: drawing again empties the page, and what was typed would be gone. (The field is
  // put there by this check, in the drawn panel: which fields a paired page has depends on the screen it is on and
  // on what is switched on, and the rule is about any field in it.)
  await late.evaluate(() => {
    const shell = document.querySelector('#app > *'), f = document.createElement('input');   // eslint-disable-line no-undef
    f.id = 'latefield'; f.className = 'text-input'; f.setAttribute('aria-label', 'A field of the test');
    shell.setAttribute('data-was', '1'); shell.appendChild(f); f.focus(); f.value = 'half a na';
    document.dispatchEvent(new Event('pvjfile'));   // eslint-disable-line no-undef
  });
  assert.deepStrictEqual(await late.evaluate(typing), ['half a na', true, true], 'a part arrived late while somebody was typing: nothing is drawn over the field');
  await pause(1000);
  assert.deepStrictEqual(await late.evaluate(typing), ['half a na', true, true], 'and a second later it is still left alone');
  await late.evaluate(() => document.getElementById('latefield').blur());   // eslint-disable-line no-undef
  await late.waitForFunction(() => !document.querySelector('#app > [data-was]') && !document.getElementById('latefield') && !!document.querySelector('#app > *'), null, { timeout: 5000 });   // eslint-disable-line no-undef
  // Nor under a question that waits for its answer (confirmRow): it is drawn once the question is gone.
  await late.evaluate(() => {
    const shell = document.querySelector('#app > *'), q = document.createElement('div');   // eslint-disable-line no-undef
    q.id = 'confirmrow'; shell.setAttribute('data-was', '1'); shell.appendChild(q);
    document.dispatchEvent(new Event('pvjfile'));   // eslint-disable-line no-undef
  });
  await pause(600);
  assert.strictEqual(await late.evaluate(() => !!document.querySelector('#app > [data-was] #confirmrow')), true, 'a question that is open is not drawn over');   // eslint-disable-line no-undef
  await late.evaluate(() => { const q = document.getElementById('confirmrow'); q.parentNode.removeChild(q); });   // eslint-disable-line no-undef
  await late.waitForFunction(() => !document.querySelector('#app > [data-was]') && !!document.querySelector('#app > *'), null, { timeout: 5000 });   // eslint-disable-line no-undef

  const csp = said.filter((t) => /Content Security Policy/i.test(t));
  assert.deepStrictEqual(csp, [], 'load.js within the page\'s policy: ' + csp.join('; '));
  plan([]);
}

module.exports = { check, LOST, BROKEN, SCANNED };
