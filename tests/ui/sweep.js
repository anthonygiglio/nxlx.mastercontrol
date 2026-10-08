// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// The width sweep (D64): one screen of the panel, as it is open, looked at in every size class of the resizing
// study and on a phone held sideways, so the layout stays repaired where it used to break (320 px, a short window,
// 600 to 899 px). tests/ui/panel.test.js runs it on every screen in the list of tests/ui/signal-pages.js.
//
//   SIZES            the windows: every width where a class begins or ends, and three sideways phones
//   measure(o)       runs in the page; returns what is wrong as [{ kind, name, detail }], an empty list is a pass
//   sweep(pg, o)     resizes the page through SIZES, measures each, returns the faults with the size on each
//   lines(found)     the faults of one screen as sentences, one per fault, with every size it was seen at
//
// What is held, at every size: the page does not scroll sideways; no control is wider than the window or sticks
// out of it or of its card; a control is 44 px tall and wide where its token says 44 (in Signal every control; 56
// px for what staff press on Room and Live); no words are clipped inside a button, a chip or a pad, run out of it,
// or are broken in the middle of a word on a button; there is a way to the other screens that is shown, whole and
// inside the window (the area tabs under 600 px, the side menu from 600 px: the Workspace shell, D65); and the
// transport strip's buttons and every item of the menus can be brought into view and pressed. In a short window
// (under 480 px) that last rule is held for every control, and the bars that stay put leave at least half of the
// window's height to the page. A screen on which no control at all was measured is a fault of the sweep itself.
//
// Measured in headless Chromium only. Nothing here says how the panel looks on a real phone.
'use strict';

// Compact is under 600, medium 600 to 899, expanded 900 to 1199, large 1200 to 1599, extra large from 1600; a
// window under 480 px tall is short. The heights are those of common devices of each width; the last three are
// phones held sideways.
const SIZES = [[320, 568], [360, 740], [390, 844], [600, 960], [768, 1024], [899, 900], [900, 900], [1200, 800], [1366, 768], [1600, 900],
  [667, 375], [844, 390], [932, 430]];      // the last: a large phone sideways, wide enough for the laptop's columns

/* eslint-disable no-undef */
function measure(o) {
  const out = [];
  const root = document.documentElement, shell = document.querySelector('.shell');
  if (!shell) return [{ kind: 'there is no screen', name: '', detail: '' }];
  const vw = root.clientWidth, vh = window.innerHeight;
  const short = vh < 480;
  const add = (kind, el, detail) => out.push({ kind, name: el ? name(el) : '', detail: detail || '' });
  const name = (el) => ((el.id ? '#' + el.id + ' ' : '') + (el.getAttribute('aria-label') || el.textContent || el.tagName).replace(/\s+/g, ' ').trim().slice(0, 28)) || el.tagName.toLowerCase() + '.' + el.className;
  // (what is inside a closed fold still has a size in Chromium, which only skips drawing it: checkVisibility knows)
  const shown = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden' && (!el.checkVisibility || el.checkVisibility()); };
  // something that scrolls sideways inside itself on purpose (a drawn controller): what is in it may be wider
  const scrolls = (el) => { for (let e = el.parentElement; e && e !== document.body; e = e.parentElement) { if (/auto|scroll/.test(getComputedStyle(e).overflowX)) return true; } return false; };
  const px = (n) => Math.round(n);

  // 1. the page does not scroll sideways
  //    (what makes it do so is named: the outermost things that reach past the window's edge, three at most)
  if (root.scrollWidth > vw) {
    const past = [];
    shell.querySelectorAll('*').forEach((el) => {
      if (past.length > 2 || !shown(el) || scrolls(el)) return;
      const r = el.getBoundingClientRect(), up = el.parentElement.getBoundingClientRect();
      if (r.right > vw + 1 && !(up.right > vw + 1)) past.push(name(el) + ' reaches ' + px(r.right));
    });
    add('the page scrolls sideways', null, root.scrollWidth + ' px in a window of ' + vw + (past.length ? ': ' + past.join('; ') : ''));
  }

  // 2. no control is wider than the window, or sticks out of it or of its card
  const controls = Array.prototype.filter.call(shell.querySelectorAll('button, select, summary, input, textarea, a[href], canvas, .xypad'), (el) => shown(el));
  if (!controls.length) add('no control was measured', null, 'the screen is empty, or the sweep does not see into it');
  controls.concat(Array.prototype.filter.call(shell.querySelectorAll('.card'), shown)).forEach((el) => {
    if (scrolls(el)) return;
    const r = el.getBoundingClientRect();
    if (r.width > vw + 1) add('wider than the window', el, px(r.width) + ' px');
    else if (r.right > vw + 1 || r.left < -1) add('sticks out of the window', el, px(r.left) + ' to ' + px(r.right));
    const card = el.parentElement && el.parentElement.closest('.card');
    if (card) { const b = card.getBoundingClientRect(); if (r.right > b.right + 1 || r.left < b.left - 1) add('sticks out of its card', el, px(r.left) + ' to ' + px(r.right) + ', the card is ' + px(b.left) + ' to ' + px(b.right)); }
  });

  // 3. touch targets: 44 px where the token says 44 (in Signal every control), 56 px for what staff press
  controls.forEach((el) => {
    if (el.tagName === 'CANVAS' || el.tagName === 'A' || el.tagName === 'TEXTAREA') return;
    const r = el.getBoundingClientRect();
    const token = parseFloat(getComputedStyle(el).minHeight) || 0;
    if (o.signal || token >= 44) {
      if (r.height < 43.5 || r.width < 43.5) add('small', el, px(r.width) + 'x' + px(r.height));
      else if (o.signal && el.tagName === 'BUTTON' && el.closest('#roomscreen, .livecols, .banks, .tp .keep') && r.height < 55.5) add('under 56 px where staff press', el, px(r.height) + ' px');
    }
  });

  // 4. words: not clipped inside a button, a chip or a pad, not running out of it, not broken in the middle
  const fits = (el, word, room) => {
    const probe = document.createElement('span');
    probe.textContent = word;
    probe.style.cssText = 'position:absolute;visibility:hidden;white-space:nowrap';
    el.appendChild(probe);
    const need = probe.getBoundingClientRect().width;
    el.removeChild(probe);
    return need <= room + 1 ? 0 : Math.round(need);
  };
  const range = document.createRange();
  shell.querySelectorAll('button, summary, .chip, .badge, .pill').forEach((ctl) => {
    if (!shown(ctl) || scrolls(ctl)) return;
    const box = ctl.getBoundingClientRect();
    const told = {};
    [ctl].concat(Array.prototype.slice.call(ctl.querySelectorAll('*'))).forEach((el) => {
      if (!shown(el) || !el.textContent.trim()) return;
      const cs = getComputedStyle(el);
      if (/hidden|clip/.test(cs.overflowX) && el.scrollWidth > el.clientWidth + 1 && !told.c) { told.c = 1; add('words are clipped', ctl, el.scrollWidth + ' px in ' + el.clientWidth); }
      else if (/hidden|clip/.test(cs.overflowY) && el.scrollHeight > el.clientHeight + 1 && !told.c) { told.c = 1; add('words are clipped', ctl, el.scrollHeight + ' px high in ' + el.clientHeight); }
    });
    const walker = document.createTreeWalker(ctl, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const el = n.parentElement, text = n.nodeValue.trim();
      if (!text || !shown(el)) continue;
      range.selectNodeContents(n);
      const rects = range.getClientRects();
      for (let i = 0; i < rects.length && !told.o; i++) {
        const r = rects[i];
        if (r.width && (r.right > box.right + 1 || r.left < box.left - 1)) { told.o = 1; add('words run out of it', ctl, px(r.left) + ' to ' + px(r.right) + ', it is ' + px(box.left) + ' to ' + px(box.right)); }
      }
      if (ctl.tagName === 'BUTTON' && !told.w) {
        const cs = getComputedStyle(el);
        const word = text.split(/\s+/).sort((x, y) => y.length - x.length)[0] || '';
        const room = el.clientWidth ? el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) : el.getBoundingClientRect().width;
        const need = word.length > 1 && (ctl.classList.contains('btn') || cs.textTransform === 'uppercase') ? fits(el, word, room) : 0;
        if (need) { told.w = 1; add('a word is broken', ctl, need + ' px in ' + px(room)); }
      }
    }
  });

  // 5. the way to the other screens is shown: the tab bar under 600 px, whole and in the window, or the side menu;
  //    the transport and the menus can be brought into view and pressed (in a short window: every control), and
  //    the bars that stay put leave half the window to the page
  const tabs = document.querySelector('nav.tabs'), side = document.getElementById('wsside');
  const was = [window.scrollX, window.scrollY];
  const paired = !!document.getElementById('wstp');
  if (paired && !(tabs && shown(tabs)) && !(side && shown(side))) add('there is no way to another screen', null, 'neither the tabs nor the side menu is shown');
  if (paired && tabs && shown(tabs) && side && shown(side)) add('the tabs and the side menu are both shown', null, vw + ' px');
  if (paired && (vw < 600) !== !!(tabs && shown(tabs))) add(vw < 600 ? 'under 600 px there are no tabs' : 'from 600 px the tabs are still there', null, vw + ' px');
  if (side && shown(side)) { const r = side.getBoundingClientRect(); if (r.right > vw / 2 || r.left < -1) add('the side menu is cut off or takes half the window', null, px(r.left) + ' to ' + px(r.right)); }
  if (tabs && shown(tabs)) {
    const r = tabs.getBoundingClientRect();
    if (r.bottom > vh + 1 || r.top < -1 || r.right > vw + 1 || r.left < -1) add('the tab bar is cut off', null, px(r.top) + ' to ' + px(r.bottom) + ' in a window ' + vh + ' px high');
    let pinned = 0;
    shell.querySelectorAll('*').forEach((el) => { if (/fixed|sticky/.test(getComputedStyle(el).position) && shown(el) && !el.parentElement.closest('.picker')) { const b = el.getBoundingClientRect(); if (b.width > vw / 2 && b.bottom > 0 && b.top < vh) pinned += Math.min(b.bottom, vh) - Math.max(b.top, 0); } });
    if (pinned > vh / 2) add('the bars that stay put take most of the window', null, px(pinned) + ' px of ' + vh);
  }
  if (paired && !(tabs && shown(tabs))) {        // from 600 px: the strip alone stays put
    const d = document.getElementById('wsdock'), b = d ? d.getBoundingClientRect() : null;
    if (b && Math.min(b.bottom, vh) - Math.max(b.top, 0) > vh / 2) add('the bars that stay put take most of the window', null, px(b.height) + ' px of ' + vh);
  }
  const reach = (el) => {
    if (scrolls(el) || getComputedStyle(el).pointerEvents === 'none') return;
    el.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' });
    const r = el.getBoundingClientRect();
    if (r.height <= vh && (r.bottom > vh + 1 || r.top < -1)) return add('cannot be brought into view', el, px(r.top) + ' to ' + px(r.bottom) + ' in a window ' + vh + ' px high');
    const x = Math.min(vw - 1, Math.max(0, r.left + r.width / 2)), y = Math.min(vh - 1, Math.max(0, r.top + r.height / 2));
    const hit = document.elementFromPoint(x, y);
    // (the message line of Signal lies over the page for some seconds and goes by itself: it is not a cover)
    if (hit && !(el.contains(hit) || hit.contains(el) || hit.closest('#msg') || (hit.closest('label') && hit.closest('label').contains(el)))) add('covered, cannot be pressed', el, 'by ' + name(hit));
  };
  const must = short ? controls : controls.filter((el) => el.closest('nav, .tp'));
  if (!document.querySelector('.picker')) must.forEach(reach);
  window.scrollTo(was[0], was[1]);
  return out;
}
/* eslint-enable no-undef */

// pg: a Playwright page (or anything with setViewportSize and evaluate). o: { signal } (the look in use).
async function sweep(pg, o) {
  const found = [];
  for (const [width, height] of (o && o.sizes) || SIZES) {
    await pg.setViewportSize({ width, height });
    await pg.evaluate(() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done))));
    const got = await pg.evaluate(measure, { signal: !!(o && o.signal) });
    got.forEach((f) => found.push(Object.assign(f, { size: width + 'x' + height })));
  }
  return found;
}

// One sentence per fault, with every size it shows at: "words are clipped: Stop at 320x568 (61 px in 48), 360x740"
function lines(found) {
  const by = new Map();
  found.forEach((f) => {
    const key = f.kind + (f.name ? ': ' + f.name : '');
    if (!by.has(key)) by.set(key, []);
    by.get(key).push(f.size + (f.detail ? ' (' + f.detail + ')' : ''));
  });
  return Array.from(by, ([key, at]) => key + ' at ' + at.join(', '));
}

module.exports = { SIZES, measure, sweep, lines };
