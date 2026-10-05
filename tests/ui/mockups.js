// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0
// Export the panel screen that is open in a Playwright page as editable mock-up files:
//   <dir>/<device>/<screen>.svg  vector, one named group per section, card and control, real text
//   <dir>/<device>/<screen>.psd  layered: a group per card, a layer per control, the card behind them
//   <dir>/<device>/<screen>.png  the flat picture
// Used by screenshots.js when MOCKUPS is set. Needs pngjs and ag-psd (CI installs them beside playwright).
'use strict';
const fs = require('fs');
const path = require('path');

// The page side: walk the visible DOM and describe it. Runs inside the browser.
function describe() {
  // The sections of a screen: each becomes a named group at the top of the SVG and a layer group in the PSD. A System
  // page has a Back button, a header with the switch, a description, a message and a state line above its cards.
  const SECTIONS = '.top, .pads, .card, nav.tabs, .btn.back, #sysblurb, #sysstate, #msg, .danger-h, .support-banner, .banks';
  const CONTROL = 'button, input, select, textarea, canvas, img, svg, .pad';
  const used = {};
  const uniq = (s) => { s = s.replace(/\s+/g, ' ').trim().slice(0, 48) || 'item'; used[s] = (used[s] || 0) + 1; return used[s] > 1 ? s + ' ' + used[s] : s; };
  const visible = (el) => {
    const cs = getComputedStyle(el), r = el.getBoundingClientRect();
    return cs.display !== 'none' && cs.visibility !== 'hidden' && +cs.opacity > 0 && r.width > 0 && r.height > 0;
  };
  window.scrollTo(0, 0);                                  // measure from the top of the page
  const sx = window.scrollX, sy = window.scrollY;
  const box = (el) => { const r = el.getBoundingClientRect(); return { x: r.left + sx, y: r.top + sy, w: r.width, h: r.height }; };
  const ownText = (el) => Array.from(el.childNodes).filter((n) => n.nodeType === 3).map((n) => n.textContent).join(' ').trim();
  function nameOf(el) {
    if (el.matches('.card.navgroup')) { const h = el.querySelector('h2'); return 'Group: ' + (h ? h.textContent : 'top'); }
    if (el.matches('.card.ctlcard')) { const h = el.querySelector('h2'); return 'Controller: ' + (h ? h.textContent : 'unknown'); }
    if (el.matches('.card')) { const h = el.querySelector('h2'); return 'Card: ' + (h ? h.textContent : el.id || 'untitled'); }
    if (el.matches('.top.syshead')) return 'Page header';
    if (el.matches('.top')) return 'Header';
    if (el.matches('.btn.back')) return 'Back button';
    if (el.matches('#sysblurb')) return 'Description';
    if (el.matches('#sysstate')) return 'State line';
    if (el.matches('#msg')) return 'Message line';
    if (el.matches('.banks')) return 'Banks';
    if (el.matches('.navrow')) { const n = el.querySelector('.navname'); return 'Row: ' + (n ? n.textContent : el.textContent); }
    if (el.matches('.chip')) return 'Chip: ' + el.textContent;
    if (el.matches('.switch')) return 'Switch: ' + (el.getAttribute('aria-label') || el.id || '') + (el.getAttribute('aria-checked') === 'true' ? ' (on)' : ' (off)');
    if (el.matches('.ctlgrid')) return 'Controller drawing';
    if (el.matches('.ctl')) { const n = el.querySelector('.ctlname'); return 'Control: ' + (n ? n.textContent : el.textContent); }
    if (el.matches('.item.lrow')) { const n = el.querySelector('.lname'); return 'List row: ' + (n ? n.textContent : ''); }
    if (el.matches('.addform')) return 'Form: ' + ((el.querySelector('.field') || {}).textContent || el.id || '');
    if (el.matches('.confirm')) return 'Question';
    if (el.matches('details')) { const sm = el.querySelector('summary'); return 'Fold: ' + (sm ? sm.textContent : ''); }
    if (el.matches('label.field, .field')) return 'Label: ' + el.textContent;
    if (el.matches('.hint')) return 'Hint: ' + el.textContent;
    if (el.matches('.state')) return 'State: ' + el.textContent;
    if (el.matches('.pads')) return 'Pads';
    if (el.matches('nav.tabs')) return 'Tab bar';
    if (el.matches('.pad')) return 'Pad ' + el.textContent;
    if (el.matches('button')) return 'Button: ' + (el.textContent || el.getAttribute('aria-label') || '');
    if (el.matches('input[type=range]')) return 'Slider: ' + (el.getAttribute('aria-label') || el.id || '');
    if (el.matches('input[type=checkbox]')) return 'Switch: ' + (el.closest('label') ? el.closest('label').textContent : el.id);
    if (el.matches('input, textarea')) return 'Field: ' + (el.getAttribute('aria-label') || el.placeholder || el.id || el.type);
    if (el.matches('select')) return 'Menu: ' + (el.getAttribute('aria-label') || el.id || '');
    if (el.matches('h1, h2, h3')) return 'Title: ' + el.textContent;
    if (el.matches('canvas, img, svg')) return 'Picture: ' + (el.id || el.getAttribute('alt') || el.tagName.toLowerCase());
    const t = ownText(el);
    return t ? 'Text: ' + t : el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.split(' ')[0] : '');
  }
  function paint(el) {
    const cs = getComputedStyle(el), out = [];
    const b = box(el);
    const rx = parseFloat(cs.borderTopLeftRadius) || 0;
    if (!el.matches('input[type=range]') && !/rgba\(.*, 0\)|transparent/.test(cs.backgroundColor)) out.push({ k: 'rect', ...b, rx, fill: cs.backgroundColor });
    const sides = ['Top', 'Right', 'Bottom', 'Left'].map((s) => ({ s, w: parseFloat(cs['border' + s + 'Width']), c: cs['border' + s + 'Color'], st: cs['border' + s + 'Style'] }))
      .filter((d) => d.w > 0 && d.st !== 'none' && !/rgba\(.*, 0\)/.test(d.c));
    if (sides.length === 4 && sides.every((d) => d.w === sides[0].w && d.c === sides[0].c)) {
      const w = sides[0].w;
      out.push({ k: 'rect', x: b.x + w / 2, y: b.y + w / 2, w: b.w - w, h: b.h - w, rx: Math.max(0, rx - w / 2), stroke: sides[0].c, sw: w });
    } else {
      for (const d of sides) {
        const L = { Top: [b.x, b.y + d.w / 2, b.x + b.w, b.y + d.w / 2], Bottom: [b.x, b.y + b.h - d.w / 2, b.x + b.w, b.y + b.h - d.w / 2],
          Left: [b.x + d.w / 2, b.y, b.x + d.w / 2, b.y + b.h], Right: [b.x + b.w - d.w / 2, b.y, b.x + b.w - d.w / 2, b.y + b.h] }[d.s];
        out.push({ k: 'line', x1: L[0], y1: L[1], x2: L[2], y2: L[3], stroke: d.c, sw: d.w });
      }
    }
    const font = { family: cs.fontFamily, size: parseFloat(cs.fontSize), weight: cs.fontWeight, color: cs.color, spacing: cs.letterSpacing };
    const tx = (s) => cs.textTransform === 'uppercase' ? s.toUpperCase() : s;
    if (el.matches('.switch')) {
      // the track and the thumb are CSS pseudo-elements: draw them from their computed styles
      const tr = getComputedStyle(el, '::before'), th = getComputedStyle(el, '::after');
      out.push({ k: 'rect', x: b.x, y: b.y + 6, w: 52, h: 32, rx: 16, fill: tr.backgroundColor });
      out.push({ k: 'rect', x: b.x + 0.75, y: b.y + 6.75, w: 50.5, h: 30.5, rx: 15.25, stroke: tr.borderTopColor, sw: 1.5 });
      out.push({ k: 'circle', cx: b.x + (parseFloat(th.left) || 4) + 12, cy: b.y + 22, r: 12, fill: th.backgroundColor });
    } else if (el.matches('input[type=range]')) {
      const v = (+el.value - +(el.min || 0)) / ((+(el.max || 100)) - (+(el.min || 0)) || 1);
      const accent = cs.accentColor && cs.accentColor !== 'auto' ? cs.accentColor : font.color;
      out.push({ k: 'rect', x: b.x, y: b.y + b.h / 2 - 2, w: b.w, h: 4, rx: 2, fill: 'rgba(128,128,128,0.35)' });
      out.push({ k: 'rect', x: b.x, y: b.y + b.h / 2 - 2, w: b.w * v, h: 4, rx: 2, fill: accent });
      out.push({ k: 'circle', cx: b.x + 8 + (b.w - 16) * v, cy: b.y + b.h / 2, r: 8, fill: accent });
    } else if (el.matches('input[type=checkbox], input[type=radio]')) {
      out.push({ k: 'rect', x: b.x, y: b.y, w: b.w, h: b.h, rx: el.type === 'radio' ? b.w / 2 : 3, stroke: font.color, sw: 1.5, fill: el.checked ? (cs.accentColor !== 'auto' ? cs.accentColor : font.color) : 'none' });
    } else if (el.matches('input, textarea, select')) {
      const val = el.matches('select') ? (el.selectedOptions[0] ? el.selectedOptions[0].textContent : '') : (el.type === 'password' ? '•'.repeat(el.value.length) : el.value);
      const shown = val || el.placeholder || '';
      if (shown) out.push({ k: 'text', x: b.x + (parseFloat(cs.paddingLeft) || 6) + (parseFloat(cs.borderLeftWidth) || 0), y: b.y + b.h / 2 + font.size * 0.35, s: shown.split('\n')[0], ...font, color: val ? font.color : 'rgba(128,128,128,0.9)' });
      if (el.matches('select')) out.push({ k: 'text', x: b.x + b.w - 18, y: b.y + b.h / 2 + font.size * 0.35, s: '▾', ...font });
    } else if (el.matches('canvas')) {
      try { out.push({ k: 'image', ...b, href: el.toDataURL('image/png') }); } catch (e) { /* tainted: leave it out */ }
    } else if (el.matches('img')) {
      try { const c = document.createElement('canvas'); c.width = el.naturalWidth; c.height = el.naturalHeight; c.getContext('2d').drawImage(el, 0, 0); out.push({ k: 'image', ...b, href: c.toDataURL('image/png') }); } catch (e) { /* skip */ }
    } else if (el.matches('svg')) {
      out.push({ k: 'svg', ...b, xml: new XMLSerializer().serializeToString(el) });
    }
    if (!el.matches('input, textarea, select, svg')) {
      // Own text, line by line as the browser wrapped it.
      for (const n of el.childNodes) {
        if (n.nodeType !== 3 || !n.textContent.trim()) continue;
        const lines = [], re = /\S+/g, txt = n.textContent; let m;
        while ((m = re.exec(txt))) {
          const r = document.createRange(); r.setStart(n, m.index); r.setEnd(n, m.index + m[0].length);
          const rr = r.getClientRects()[0]; if (!rr) continue;
          const last = lines[lines.length - 1];
          if (last && Math.abs(last.top - rr.top) < 2) { last.words.push(m[0]); last.right = rr.right; } else lines.push({ top: rr.top, h: rr.height, left: rr.left, right: rr.right, words: [m[0]] });
        }
        for (const l of lines) out.push({ k: 'text', x: l.left + sx, y: l.top + sy + l.h / 2 + font.size * 0.35, s: tx(l.words.join(' ')), ...font });
      }
    }
    return out;
  }
  function node(el) {
    const kids = [];
    if (!el.matches('svg, select')) for (const c of el.children) if (visible(c) || c.matches('svg')) kids.push(node(c));
    return { name: nameOf(el), draw: paint(el), kids, control: el.matches(CONTROL) };
  }
  // Simplify: an unnamed wrapper that draws nothing gives its children to its parent.
  function flat(n) {
    const kids = [];
    for (const k of n.kids.map(flat)) {
      const boring = !k.draw.length && !k.control && !/^(Card|Group|Controller|Header|Page header|Back button|Description|State line|Message line|Banks|Row|Chip|Control|List row|Form|Question|Fold|Label|Hint|State|Pads|Tab bar|Title|Text|Button|Field|Slider|Switch|Menu|Picture|Pad )/.test(k.name);
      if (boring) kids.push(...k.kids); else kids.push(k);
    }
    return { ...n, kids };
  }
  const doc = document.documentElement;
  const bg = getComputedStyle(document.body).backgroundColor;
  const sections = Array.from(document.querySelectorAll(SECTIONS)).filter((el) => visible(el) && !el.parentElement.closest(SECTIONS));
  const tree = sections.map((el) => flat(node(el)));
  const walk = (n) => { n.name = uniq(n.name); n.kids.forEach(walk); };
  tree.forEach(walk);
  // For the layered picture: each section's rectangle, and the controls (top-level ones) inside it.
  const layers = sections.map((el, i) => {
    const leaves = [];
    el.querySelectorAll('*').forEach((c) => {
      if (!visible(c)) return;
      if ((c.matches(CONTROL) || (ownText(c) && !c.closest(CONTROL))) && !c.parentElement.closest(CONTROL)) {
        c.setAttribute('data-mock-leaf', '1');
        leaves.push({ name: nameOf(c), ...box(c) });
      }
    });
    el.setAttribute('data-mock-section', '1');
    return { name: tree[i].name, ...box(el), leaves };
  });
  return { width: Math.ceil(doc.scrollWidth), height: Math.ceil(doc.scrollHeight), bg, tree, layers };
}

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
function svgOf(d) {
  let id = 0;
  const shape = (p) => {
    if (p.k === 'rect') return `<rect x="${p.x.toFixed(1)}" y="${p.y.toFixed(1)}" width="${Math.max(0, p.w).toFixed(1)}" height="${Math.max(0, p.h).toFixed(1)}"${p.rx ? ` rx="${Math.min(p.rx, p.h / 2, p.w / 2).toFixed(1)}"` : ''} fill="${p.fill || 'none'}"${p.stroke ? ` stroke="${p.stroke}" stroke-width="${p.sw}"` : ''}/>`;
    if (p.k === 'line') return `<line x1="${p.x1.toFixed(1)}" y1="${p.y1.toFixed(1)}" x2="${p.x2.toFixed(1)}" y2="${p.y2.toFixed(1)}" stroke="${p.stroke}" stroke-width="${p.sw}"/>`;
    if (p.k === 'circle') return `<circle cx="${p.cx.toFixed(1)}" cy="${p.cy.toFixed(1)}" r="${p.r}" fill="${p.fill}"/>`;
    if (p.k === 'image') return `<image x="${p.x.toFixed(1)}" y="${p.y.toFixed(1)}" width="${p.w.toFixed(1)}" height="${p.h.toFixed(1)}" href="${p.href}"/>`;
    if (p.k === 'svg') return p.xml.replace(/^<svg/, `<svg x="${p.x.toFixed(1)}" y="${p.y.toFixed(1)}" width="${p.w.toFixed(1)}" height="${p.h.toFixed(1)}"`);
    if (p.k === 'text') {
      const sp = p.spacing && p.spacing !== 'normal' ? ` letter-spacing="${parseFloat(p.spacing)}"` : '';
      return `<text x="${p.x.toFixed(1)}" y="${p.y.toFixed(1)}" font-family="${esc(p.family)}" font-size="${p.size}" font-weight="${p.weight}" fill="${p.color}"${sp}>${esc(p.s)}</text>`;
    }
    return '';
  };
  const group = (n, depth) => {
    const inner = n.draw.map(shape).join('') + n.kids.map((k) => group(k, depth + 1)).join('');
    if (!inner) return '';
    return `\n${'  '.repeat(depth)}<g id="${esc(n.name)}" data-n="${id++}">${inner}</g>`;
  };
  return `<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="${d.width}" height="${d.height}" viewBox="0 0 ${d.width} ${d.height}">` +
    `\n<rect id="Background" width="100%" height="100%" fill="${d.bg}"/>` + d.tree.map((n) => group(n, 0)).join('') + '\n</svg>\n';
}

function crop(img, x, y, w, h) {
  x = Math.max(0, Math.round(x)); y = Math.max(0, Math.round(y));
  w = Math.min(img.width - x, Math.round(w)); h = Math.min(img.height - y, Math.round(h));
  if (w <= 0 || h <= 0) return null;
  const data = new Uint8ClampedArray(w * h * 4);
  for (let r = 0; r < h; r++) data.set(img.data.subarray(((y + r) * img.width + x) * 4, ((y + r) * img.width + x + w) * 4), r * w * 4);
  return { width: w, height: h, data };
}

async function exportScreen(page, dir, device, screen) {
  const { PNG } = require('pngjs');
  const { writePsdBuffer } = require('ag-psd');
  const out = path.join(dir, device);
  fs.mkdirSync(out, { recursive: true });
  const d = await page.evaluate(describe);
  fs.writeFileSync(path.join(out, screen + '.svg'), svgOf(d));

  // Three pictures: everything; without the controls (the cards behind them); without the sections (the page).
  const full = await page.screenshot({ fullPage: true });
  fs.writeFileSync(path.join(out, screen + '.png'), full);
  const hide = (sel) => page.evaluate((s) => document.querySelectorAll(s).forEach((e) => e.style.setProperty('visibility', 'hidden', 'important')), sel);
  await hide('[data-mock-leaf]');
  const noLeaves = await page.screenshot({ fullPage: true });
  await hide('[data-mock-section]');
  const bare = await page.screenshot({ fullPage: true });
  await page.evaluate(() => document.querySelectorAll('[data-mock-leaf], [data-mock-section]').forEach((e) => {
    e.style.removeProperty('visibility'); e.removeAttribute('data-mock-leaf'); e.removeAttribute('data-mock-section');
  }));

  const A = PNG.sync.read(full), B = PNG.sync.read(noLeaves), C = PNG.sync.read(bare);
  const k = A.width / d.width;                                   // device pixels per CSS pixel
  const layer = (img, name, b) => { const im = crop(img, b.x * k, b.y * k, b.w * k, b.h * k); return im && { name, left: Math.max(0, Math.round(b.x * k)), top: Math.max(0, Math.round(b.y * k)), imageData: im }; };
  const children = [{ name: 'Page background', left: 0, top: 0, imageData: { width: C.width, height: C.height, data: new Uint8ClampedArray(C.data) } }];
  for (const s of d.layers) {
    const kids = [layer(B, s.name.replace(/^Card: /, '') + ' (card)', s)].concat(s.leaves.map((l) => layer(A, l.name, l))).filter(Boolean);
    children.push({ name: s.name, opened: false, children: kids });
  }
  const psd = { width: A.width, height: A.height, imageData: { width: A.width, height: A.height, data: new Uint8ClampedArray(A.data) }, children };
  fs.writeFileSync(path.join(out, screen + '.psd'), writePsdBuffer(psd, { generateThumbnail: false }));
  return { svg: screen + '.svg', psd: screen + '.psd', sections: d.layers.length, controls: d.layers.reduce((n, s) => n + s.leaves.length, 0) };
}

module.exports = { exportScreen };
