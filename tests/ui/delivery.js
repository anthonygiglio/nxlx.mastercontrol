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
// whole without a word; after two more requests the page says which file is missing, offers Reload, and asks no
// further; a script that arrives and cannot run is not asked for again and is named as a fault of the file; the
// panel draws again when a part arrives late, but never under somebody typing a PIN; and none of it breaks the
// page's Content-Security-Policy.
'use strict';

const fs = require('fs');
const assert = require('assert');

const LOST = 'Part of this page did not arrive from the box (room.js), so some of the panel is missing. The box keeps playing. Load the page again.';
const BROKEN = 'Part of this page arrived but did not start (room.js), so some of the panel is missing. This is a fault in the panel, not in the connection; please report it.';

/* eslint-disable no-undef */
function look() {
  return {
    asked: Object.assign({}, window.pvjLoad.asked), lost: window.pvjLoad.lost.slice(), broken: window.pvjLoad.broken.slice(),
    parts: ['pvjShaders', 'pvjEffects', 'pvjRoom'].filter((n) => !!window[n]),
    drawn: !!document.querySelector('#app > *'),
    note: Array.from(document.querySelectorAll('#loadnote p')).map((p) => p.textContent),
    button: !!document.querySelector('#loadnote #loadagain'),
    // (a sheet that was not delivered is still in the list, and its rules cannot even be asked for)
    sheets: Array.from(document.styleSheets).map((s) => { let n = 0; try { n = s.cssRules.length; } catch (e) { n = 0; } return new URL(s.href).pathname + (n ? '' : ' (empty)'); }),
  };
}
/* eslint-enable no-undef */

async function check(o) {
  const { info, base } = o;
  const plan = (lines) => fs.writeFileSync(info.delivery_file, lines.map((l) => l + '\n').join(''));
  const left = () => fs.readFileSync(info.delivery_file, 'utf8').trim();
  const { pg, said } = await o.fresh();
  const WHOLE = { asked: {}, lost: [], broken: [], parts: ['pvjShaders', 'pvjEffects', 'pvjRoom'], drawn: true, note: [], button: false, sheets: ['/theme.css', '/app.css'] };
  const whole = (asked) => Object.assign({}, WHOLE, { asked });
  const open = async (lines, ready) => {
    plan(lines);
    await pg.goto(base + '/');
    await pg.waitForFunction(ready || (() => !!window.pvjLoad && !!document.querySelector('#app > *') && !!window.pvjShaders && !!window.pvjEffects && !!window.pvjRoom && document.styleSheets.length === 2 &&
      Array.from(document.styleSheets).every((s) => { try { return s.cssRules.length > 0; } catch (e) { return false; } })), null, { timeout: 15000 });   // eslint-disable-line no-undef
    return pg.evaluate(look);
  };

  // every file arrives: nothing is asked for twice, nothing is said
  assert.deepStrictEqual(await open([]), WHOLE, 'a page whose files all arrive');

  // cut off once (what a full queue was to a browser on the Mac): asked for again, whole, silent
  assert.deepStrictEqual(await open(['/room.js drop']), whole({ '/room.js': 1 }), 'room.js cut off once');
  assert.strictEqual(left(), '', 'the harness withheld what it was told to');
  assert.deepStrictEqual(await open(['/app.js drop']), whole({ '/app.js': 1 }), 'app.js cut off once: the panel still starts');
  assert.deepStrictEqual(await open(['/shaders.js drop', '/effects.js busy']), whole({ '/shaders.js': 1, '/effects.js': 1 }), 'two scripts at once, one cut off and one answered 503');
  // a style sheet, answered 503 as at the box's cap of connections: asked for again, and still before or after the other as the page has them
  assert.deepStrictEqual(await open(['/theme.css busy']), whole({ '/theme.css': 1 }), 'theme.css answered 503 once');
  assert.deepStrictEqual(await open(['/app.css drop', '/app.css busy']), whole({ '/app.css': 2 }), 'app.css missing twice: the second new request brings it');

  // three times: the page says so, offers Reload and asks no further (the fourth line is still there afterwards)
  const noted = () => !!document.querySelector('#loadnote #loadagain');     // eslint-disable-line no-undef
  let got = await open(['/room.js drop', '/room.js busy', '/room.js drop', '/room.js drop'], noted);
  assert.deepStrictEqual(got, Object.assign({}, WHOLE, { asked: { '/room.js': 2 }, lost: ['/room.js'], parts: ['pvjShaders', 'pvjEffects'], note: [LOST], button: true }), 'room.js missing three times');
  await new Promise((done) => setTimeout(done, 2500));
  assert.strictEqual(left(), '/room.js drop', 'after the two new requests the page asks for the file no more');
  assert.deepStrictEqual((await pg.evaluate(look)).asked, { '/room.js': 2 });
  // Reload is the visitor's own press: the page loads again (the last line cuts room.js off once more, and it is asked for again)
  await pg.click('#loadagain');
  await pg.waitForFunction(() => !!window.pvjLoad && !!window.pvjRoom && !document.querySelector('#loadnote') && !!document.querySelector('#app > *'), null, { timeout: 15000 });   // eslint-disable-line no-undef
  assert.deepStrictEqual(await pg.evaluate(look), whole({ '/room.js': 1 }), 'after Reload');
  assert.strictEqual(left(), '');

  // a script that arrives and cannot run is a fault of the file: not asked for again, and named as that
  got = await open(['/room.js broken'], noted);
  assert.deepStrictEqual(got, Object.assign({}, WHOLE, { broken: ['/room.js'], parts: ['pvjShaders', 'pvjEffects'], note: [BROKEN], button: true }), 'room.js arrives and cannot run');

  // Nobody is paired on this page: a part arriving late draws nothing again, so a PIN being typed stays.
  await open([]);
  await pg.evaluate(() => { document.querySelector('input.pin').value = '7'; document.querySelector('#app > *').setAttribute('data-was', '1'); document.dispatchEvent(new Event('pvjfile')); });   // eslint-disable-line no-undef
  assert.deepStrictEqual(await pg.evaluate(() => [document.querySelector('input.pin').value, !!document.querySelector('#app > [data-was]')]), ['7', true], 'the connect screen is left alone');   // eslint-disable-line no-undef
  // On a paired page the panel draws again with the part that came late (app.js, the listener for "pvjfile").
  assert.strictEqual(await o.page.evaluate(() => { document.querySelector('#app > *').setAttribute('data-was', '1'); document.dispatchEvent(new Event('pvjfile')); return !!document.querySelector('#app > [data-was]'); }), false,   // eslint-disable-line no-undef
    'the panel draws again when a part arrives late');

  const csp = said.filter((t) => /Content Security Policy/i.test(t));
  assert.deepStrictEqual(csp, [], 'load.js within the page\'s policy: ' + csp.join('; '));
  plan([]);
}

module.exports = { check, LOST, BROKEN };
