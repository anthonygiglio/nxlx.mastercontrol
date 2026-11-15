// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// Wait in the page for an async check (one that fetches from the node, say), polling until it is truthy.
//
// Not an async function given to the page's waitForFunction: Playwright polls a function until it returns something truthy, and the
// Promise an async function returns is truthy at once, so such a wait is over on its first look whatever the check
// says (a check that could never hold passed after 36 ms of a 1.5 s timeout). tests/test_ui_waits.py fails on the
// spelling. Here each poll is one page.evaluate, which does wait for the promise.
//
//   await until(page, async () => (await fetch('/api/x').then((r) => r.json())).y === 3, 8000);
//
// `check` runs in the page, so it cannot use this file's variables; `arg` is passed to it as in page.evaluate.
// Resolves with the check's value; on timeout throws with the check's text and the last value it gave.
async function until(page, check, timeout, arg) {
  const end = Date.now() + (timeout === undefined ? 30000 : timeout);
  let last;
  for (;;) {
    try { last = await page.evaluate(check, arg); } catch (e) { last = 'threw: ' + e.message; }
    if (last && !(typeof last === 'string' && last.startsWith('threw: '))) return last;
    if (Date.now() > end) throw new Error('timed out after ' + (timeout === undefined ? 30000 : timeout) + ' ms waiting for ' + String(check).replace(/\s+/g, ' ').slice(0, 300) + '; last result: ' + JSON.stringify(last));
    await page.waitForTimeout(100);
  }
}

module.exports = { until };
