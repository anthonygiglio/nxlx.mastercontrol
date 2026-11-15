// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// Wait in the page for a check that returns a Promise (one that fetches from the node, say), polling until it is truthy.
//
// Not page.waitForFunction with such a check: Playwright polls a function until it returns something truthy, and a
// Promise (from an async function, from fetch(...).then(...), from anything that is awaited) is truthy at once, so the
// wait is over on its first look whatever the check says (one that could never hold passed after 36 ms of a 1.5 s
// timeout). tests/test_ui_waits.py fails on every spelling of it. Here each poll is one page.evaluate, which does
// wait for the promise, and a poll that never answers is given up at the deadline too.
//
//   await pollFor(page, () => fetch('/api/x').then((r) => r.json()).then((d) => d.y === 3), 8000);
//   await pollFor(page, (want) => fetch('/api/x').then((r) => r.json()).then((d) => d.y === want), 8000, 3);
//
// `check` runs in the page, so it cannot use this file's variables; `arg` is passed to it as in page.evaluate.
// Resolves with the check's value; on timeout throws with the check's text and the last thing it gave or threw.
async function pollFor(page, check, timeout, arg) {
  const ms = timeout === undefined ? 30000 : timeout;
  const end = Date.now() + ms;
  let last = 'no answer yet';
  for (;;) {
    const left = end - Date.now();
    let timer;
    const late = new Promise((resolve) => { timer = setTimeout(() => resolve({ late: true }), Math.max(left, 1)); });
    try {
      const r = await Promise.race([page.evaluate(check, arg).then((v) => ({ v }), (e) => ({ threw: e.message })), late]);
      if (r.late) { if (last === 'no answer yet') last = 'no answer by the deadline'; }   // else keep the last real answer
      else if ('threw' in r) last = 'threw: ' + r.threw;
      else { last = r.v; if (r.v) return r.v; }
    } finally {
      clearTimeout(timer);
    }
    if (Date.now() >= end) {
      throw new Error('timed out after ' + ms + ' ms waiting for ' + String(check).replace(/\s+/g, ' ').slice(0, 300) + '; last result: ' + JSON.stringify(last));
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
}

module.exports = { pollFor };
