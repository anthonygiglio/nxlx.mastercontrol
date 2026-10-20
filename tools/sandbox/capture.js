// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// Pictures of the panel from a running design sandbox (tools/sandbox/serve.py), for checking a design against the real
// thing: one PNG per line of the list below, each at its frame's size, through the sandbox's /open address so the
// scenario is the named one. The pictures are references for a person, never part of a design file.
//
//   NODE_PATH=/tmp/pw/node_modules node tools/sandbox/capture.js <out folder> [switcher port, 8700]
//
// A line: name, role, scenario, width, height, and the screen to open (the area/screen key the panel's own menus use,
// pressed as a person would: an area button, then a tab).
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const SHOTS = [
  ['01-phone-owner-pads', 'owner', 'playing', 390, 844, 'play/pads'],
  ['02-phone-owner-library', 'owner', 'idle', 390, 844, 'play/library'],
  ['03-phone-owner-picture-masks', 'owner', 'masks-combining', 390, 844, 'shape/picture'],
  ['04-phone-owner-mapping', 'owner', 'mapping-surfaces', 390, 844, 'shape/mapping'],
  ['05-phone-owner-setup-index', 'owner', 'idle', 390, 844, 'setup/index'],
  ['06-phone-operator-room-scenes', 'operator', 'idle', 390, 844, 'room/scenes'],
  ['07-phone-operator-room-walls', 'operator', 'projector-warming', 390, 844, 'room/walls'],
  ['08-phone-guest-room-open', 'guest', 'playing', 390, 844, 'room/scenes'],
  ['09-phone-guest-room-locked', 'guest', 'guests-locked', 390, 844, 'room/scenes'],
  ['10-phone-pairing', 'nobody', 'idle', 390, 844, null],
  ['11-tablet-owner-pads', 'owner', 'playing', 820, 1180, 'play/pads'],
  ['12-tablet-owner-projectors', 'owner', 'projector-error', 820, 1180, 'setup/projectors'],
  ['13-tablet-owner-mapping', 'owner', 'mapping-surfaces', 820, 1180, 'shape/mapping'],
  ['14-tablet-operator-room-scenes', 'operator', 'idle', 820, 1180, 'room/scenes'],
  ['15-laptop-owner-play-desk', 'owner', 'playing', 1440, 900, 'play/pads'],
  ['16-laptop-owner-shaders', 'owner', 'vibes', 1440, 900, 'play/shaders'],
  ['17-laptop-owner-shape-desk', 'owner', 'masks-combining', 1440, 900, 'shape/picture'],
  ['18-laptop-owner-mapping', 'owner', 'mapping-surfaces', 1440, 900, 'shape/mapping'],
  ['19-laptop-owner-projectors', 'owner', 'projector-warming', 1440, 900, 'setup/projectors'],
  ['20-laptop-operator-room-desk', 'operator', 'idle', 1440, 900, 'room/scenes'],
];

// Press what a person would to reach `key` ('area/screen'), as tests/ui/signal-pages.js go() does: the area's own
// button where it is shown (the rail, or the tabs at the foot), then the screen's tab, or for a page of Setup its row.
async function reach(page, key) {
  if (!key) return;
  const [area, screen] = key.split('/');
  const areaBtn = await page.$('#side-' + area + ':visible, #tab-' + area + ':visible');
  if (areaBtn) await areaBtn.click();
  await page.waitForTimeout(500);
  if (area === 'setup' && screen !== 'index') {
    await page.click('#nav-' + screen);
  } else {
    const tab = await page.$('#sub-' + area + '-' + screen + ':visible') || await page.$('[data-desk="' + area + '"]:visible');
    if (tab) await tab.click();
  }
  await page.waitForTimeout(1500);
}

(async () => {
  const out = process.argv[2];
  const port = +(process.argv[3] || 8700);
  if (!out) throw new Error('usage: capture.js <out folder> [port]');
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch();
  const only = process.argv[4] ? new RegExp(process.argv[4]) : null;
  try {
    for (const [name, role, scenario, w, h, key] of SHOTS) {
      if (only && !only.test(name)) continue;
      // the scenario loaded afresh for each picture: nothing anybody clicked meanwhile is in it
      const r = await fetch('http://127.0.0.1:' + port + '/api/sandbox/scenario', { method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Sandbox-Request': '1' }, body: JSON.stringify({ scenario }) });
      if (!r.ok) throw new Error('scenario ' + scenario + ' did not load: ' + r.status);
      const ports = (await r.json()).ports;
      const ctx = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: 1 });
      const page = await ctx.newPage();
      await page.goto('http://127.0.0.1:' + ports[role] + '/', { timeout: 60000 });
      await page.waitForSelector(role === 'nobody' ? '#pairbtn' : '#wsmain', { timeout: 30000 });
      await page.waitForTimeout(1500);
      await reach(page, key);
      // the whole page in one picture, with the strip and the tabs where they are drawn (at the foot): the window
      // is made as tall as the page (a full-page shot repeats or cuts what is fixed to the window)
      const tall = await page.evaluate(() => Math.max(document.documentElement.scrollHeight, document.body.scrollHeight));
      if (tall > h) { await page.setViewportSize({ width: w, height: Math.min(tall, 6000) }); await page.waitForTimeout(800); }
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: path.join(out, name + '.png') });
      console.log('captured', name, w + 'x' + Math.max(h, Math.min(tall, 6000)));
      await ctx.close();
    }
  } finally {
    await browser.close();
  }
})().catch((e) => { console.error('FAILED: ' + (e && e.stack || e)); process.exit(1); });
