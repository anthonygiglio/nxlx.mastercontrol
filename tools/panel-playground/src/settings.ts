// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0

// Everything the playground can change, with the panel's real values as defaults.

export type Tokens = { bg: string; cd: string; fg: string; ln: string; mu: string; ac: string; on: string }

export const THEMES: Record<string, { name: string; tokens: Tokens }> = {
  'dark-stage': { name: 'Dark stage', tokens: { bg: '#121214', cd: '#1c1c20', fg: '#f2f1ec', ln: '#4a4a54', mu: '#a9a8a3', ac: '#f59e0b', on: '#1a1300' } },
  'light': { name: 'Light', tokens: { bg: '#f4f3ef', cd: '#ffffff', fg: '#1c1c1c', ln: '#1c1c1c', mu: '#55554f', ac: '#c2410c', on: '#ffffff' } },
  'night-red': { name: 'Night red', tokens: { bg: '#000000', cd: '#140000', fg: '#ff6a5c', ln: '#6a1a14', mu: '#c2564b', ac: '#ff3b30', on: '#000000' } },
  'high-contrast': { name: 'High contrast', tokens: { bg: '#000000', cd: '#000000', fg: '#ffffff', ln: '#ffffff', mu: '#e6e6e6', ac: '#ffff00', on: '#000000' } },
}

export const TOKEN_INFO: { key: keyof Tokens; label: string; use: string }[] = [
  { key: 'bg', label: 'Background', use: 'page behind the cards' },
  { key: 'cd', label: 'Card', use: 'cards, buttons, pads' },
  { key: 'fg', label: 'Text', use: 'main text' },
  { key: 'ln', label: 'Lines', use: 'borders and dividers' },
  { key: 'mu', label: 'Muted', use: 'labels, pad numbers' },
  { key: 'ac', label: 'Accent', use: 'playing pad, active tab' },
  { key: 'on', label: 'On accent', use: 'text on the accent' },
]

export const FONTS: Record<string, { label: string; stack: string; google?: string }> = {
  system: { label: 'System UI (current)', stack: 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif' },
  inter: { label: 'Inter', stack: 'Inter, system-ui, sans-serif', google: 'Inter:wght@400;500;600;700' },
  atkinson: { label: 'Atkinson Hyperlegible', stack: '"Atkinson Hyperlegible", system-ui, sans-serif', google: 'Atkinson+Hyperlegible:wght@400;700' },
  barlow: { label: 'Barlow', stack: 'Barlow, system-ui, sans-serif', google: 'Barlow:wght@400;500;600;700' },
  plex: { label: 'IBM Plex Sans', stack: '"IBM Plex Sans", system-ui, sans-serif', google: 'IBM+Plex+Sans:wght@400;500;600;700' },
  archivo: { label: 'Archivo', stack: 'Archivo, system-ui, sans-serif', google: 'Archivo:wght@400;500;600;700' },
}
export const MONOS: Record<string, { label: string; stack: string; google?: string }> = {
  system: { label: 'System mono (current)', stack: 'ui-monospace, "SF Mono", Menlo, Consolas, monospace' },
  jetbrains: { label: 'JetBrains Mono', stack: '"JetBrains Mono", ui-monospace, monospace', google: 'JetBrains+Mono:wght@400;500' },
  plexmono: { label: 'IBM Plex Mono', stack: '"IBM Plex Mono", ui-monospace, monospace', google: 'IBM+Plex+Mono:wght@400;500' },
}

export const DEVICES: Record<string, { label: string; w: number; h: number }> = {
  phone: { label: 'Phone', w: 390, h: 844 },
  tablet: { label: 'Tablet', w: 820, h: 1180 },
  laptop: { label: 'Laptop', w: 1280, h: 800 },
  desktop: { label: 'Desktop', w: 1920, h: 1080 },
}

// Numeric knobs: [css var, label, min, max, step, default, unit, app.css selector and property it maps to]
export type Knob = { id: string; label: string; min: number; max: number; step: number; def: number; unit: string; css: (v: string) => string; group: string }
export const KNOBS: Knob[] = [
  { id: 'fs', label: 'Base text size', min: 13, max: 22, step: 1, def: 16, unit: 'px', css: (v) => `body { font-size: ${v}; }`, group: 'Type' },
  { id: 'h1', label: 'Title size', min: 14, max: 32, step: 1, def: 18, unit: 'px', css: (v) => `.top h1 { font-size: ${v}; }`, group: 'Type' },
  { id: 'h2', label: 'Card heading size', min: 12, max: 24, step: 1, def: 15, unit: 'px', css: (v) => `.card h2 { font-size: ${v}; }`, group: 'Type' },
  { id: 'kfs', label: 'Label size', min: 9, max: 16, step: 1, def: 11, unit: 'px', css: (v) => `.k { font-size: ${v}; }`, group: 'Type' },
  { id: 'ktr', label: 'Label letter spacing', min: 0, max: 0.2, step: 0.01, def: 0.08, unit: 'em', css: (v) => `.k { letter-spacing: ${v}; }`, group: 'Type' },
  { id: 'pfs', label: 'Pad name size', min: 11, max: 22, step: 1, def: 13, unit: 'px', css: (v) => `.pad { font-size: ${v}; }`, group: 'Type' },
  { id: 'bw', label: 'Border width', min: 0, max: 4, step: 0.5, def: 1.5, unit: 'px', css: (v) => `.pill, .card, .btn, .pad, .progress, .tabs, .text-input, .pin-row input { border-width: ${v}; }`, group: 'Shape' },
  { id: 'r', label: 'Corner radius', min: 0, max: 20, step: 1, def: 6, unit: 'px', css: (v) => `.card, .btn, .pad, .text-input, .pin-row input { border-radius: ${v}; }`, group: 'Shape' },
  { id: 'cp', label: 'Card padding', min: 6, max: 28, step: 1, def: 12, unit: 'px', css: (v) => `.card { padding: ${v}; }`, group: 'Spacing' },
  { id: 'g', label: 'Gap inside cards and grids', min: 2, max: 20, step: 1, def: 8, unit: 'px', css: (v) => `.card, .row, .banks, .pads { gap: ${v}; }`, group: 'Spacing' },
  { id: 'sg', label: 'Gap between cards', min: 4, max: 32, step: 1, def: 12, unit: 'px', css: (v) => `.screen { gap: ${v}; }\n@media (min-width: 900px) { .grid2 { gap: ${v}; } }`, group: 'Spacing' },
  { id: 'spad', label: 'Screen edge (narrow)', min: 8, max: 32, step: 1, def: 16, unit: 'px', css: (v) => `.screen { padding: ${v} ${v} 96px; }\n.tabs { padding-left: ${v}; padding-right: ${v}; }`, group: 'Spacing' },
  { id: 'spadw', label: 'Screen edge (wide)', min: 12, max: 64, step: 1, def: 28, unit: 'px', css: (v) => `@media (min-width: 900px) { .screen { padding: 24px ${v}; } }`, group: 'Spacing' },
  { id: 'maxw', label: 'Widest layout', min: 800, max: 1920, step: 20, def: 1100, unit: 'px', css: (v) => `.shell { max-width: ${v}; }`, group: 'Spacing' },
  { id: 'bh', label: 'Button height', min: 32, max: 64, step: 2, def: 44, unit: 'px', css: (v) => `.btn, .row.transport .btn { min-height: ${v}; }`, group: 'Touch targets' },
  { id: 'bbh', label: 'Big button height', min: 40, max: 88, step: 2, def: 56, unit: 'px', css: (v) => `.btn.big { min-height: ${v}; }`, group: 'Touch targets' },
  { id: 'th', label: 'Tab height', min: 36, max: 72, step: 2, def: 48, unit: 'px', css: (v) => `.tabs .btn { min-height: ${v}; }`, group: 'Touch targets' },
  { id: 'ph', label: 'Pad height', min: 48, max: 160, step: 4, def: 80, unit: 'px', css: (v) => `.pad { min-height: ${v}; }`, group: 'Pads' },
  { id: 'cols', label: 'Pad columns (narrow)', min: 2, max: 6, step: 1, def: 3, unit: '', css: (v) => `.pads { grid-template-columns: repeat(${v}, minmax(0, 1fr)); }`, group: 'Pads' },
  { id: 'colsw', label: 'Pad columns (wide)', min: 2, max: 8, step: 1, def: 4, unit: '', css: (v) => `@media (min-width: 900px) { .pads { grid-template-columns: repeat(${v}, minmax(0, 1fr)); } }`, group: 'Pads' },
]

export type Layout = {
  nav: 'bottom' | 'top' | 'side'
  liveWide: 'stacked' | 'split'
  liveOrder: 'transport-first' | 'pads-first'
  emergency: 'flow' | 'dock'
  padLabel: 'bottom' | 'top' | 'center'
  padNumbers: boolean
  padThumbs: boolean
  showScreenCard: boolean
  labelCase: 'uppercase' | 'none'
  progressBar: boolean
}

export type Settings = {
  theme: string
  tokens: Tokens
  font: string
  mono: string
  knobs: Record<string, number>
  layout: Layout
  device: string
  compare: boolean
  screen: 'live' | 'mix' | 'media' | 'system'
  update: UpdateState
}

export type UpdateState = 'nothing' | 'usb' | 'usb-unsigned' | 'inbox' | 'uploading' | 'confirm' | 'running' | 'done' | 'failed'
export const UPDATE_STATES: { id: UpdateState; label: string }[] = [
  { id: 'nothing', label: 'Nothing waiting' },
  { id: 'usb', label: 'On USB drive' },
  { id: 'usb-unsigned', label: 'USB, no signature' },
  { id: 'inbox', label: 'Uploaded' },
  { id: 'uploading', label: 'Uploading' },
  { id: 'confirm', label: 'Confirm step' },
  { id: 'running', label: 'Installing' },
  { id: 'done', label: 'Done' },
  { id: 'failed', label: 'Failed, rolled back' },
]

export const DEFAULT_LAYOUT: Layout = {
  nav: 'bottom', liveWide: 'stacked', liveOrder: 'transport-first', emergency: 'flow', padLabel: 'bottom',
  padNumbers: true, padThumbs: false, showScreenCard: true, labelCase: 'uppercase', progressBar: true,
}

export function defaults(): Settings {
  return {
    theme: 'dark-stage', tokens: { ...THEMES['dark-stage'].tokens }, font: 'system', mono: 'system',
    knobs: Object.fromEntries(KNOBS.map((k) => [k.id, k.def])), layout: { ...DEFAULT_LAYOUT },
    device: 'phone', compare: false, screen: 'live', update: 'usb',
  }
}

const KEY = 'mctl-playground-v1'
export function load(): Settings {
  const d = defaults()
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return d
    const s = JSON.parse(raw)
    return { ...d, ...s, tokens: { ...d.tokens, ...s.tokens }, knobs: { ...d.knobs, ...s.knobs }, layout: { ...d.layout, ...s.layout } }
  } catch { return d }
}
export function save(s: Settings) {
  try { localStorage.setItem(KEY, JSON.stringify(s)) } catch { /* private window: settings just are not remembered */ }
}

export function frameVars(s: Settings): Record<string, string> {
  const v: Record<string, string> = {}
  for (const [k, val] of Object.entries(s.tokens)) v['--' + k] = val
  for (const k of KNOBS) v['--' + k.id] = fmt(k, s.knobs[k.id])
  v['--font'] = FONTS[s.font]?.stack || FONTS.system.stack
  v['--mono'] = MONOS[s.mono]?.stack || MONOS.system.stack
  v['--kcase'] = s.layout.labelCase
  return v
}

export function loadFont(id: string, table: Record<string, { google?: string }>) {
  const g = table[id]?.google
  if (!g || document.querySelector(`link[data-font="${id}"]`)) return
  const l = document.createElement('link')
  l.rel = 'stylesheet'; l.dataset.font = id
  l.href = `https://fonts.googleapis.com/css2?family=${g}&display=swap`
  document.head.appendChild(l)
}

export function fmt(k: Knob, v: number): string {
  const d = (String(k.step).split('.')[1] || '').length
  return (+v.toFixed(d)) + k.unit
}

// What to paste back into the codebase: only what differs from the panel today.
export function exportCss(s: Settings): string {
  const lines: string[] = []
  if (s.font !== 'system') lines.push(`body { font-family: ${FONTS[s.font].stack}; }`)
  if (s.mono !== 'system') lines.push(`.k, .mono, .pad .n { font-family: ${MONOS[s.mono].stack}; }`)
  for (const k of KNOBS) {
    const v = s.knobs[k.id]
    if (v === k.def) continue
    lines.push(`/* ${k.label}: ${fmt(k, k.def)} -> ${fmt(k, v)} */`, k.css(k.id.startsWith('cols') ? String(v) : fmt(k, v)))
  }
  if (s.layout.labelCase !== 'uppercase') lines.push('.k { text-transform: none; }')
  const lay = (Object.keys(DEFAULT_LAYOUT) as (keyof Layout)[]).filter((k) => s.layout[k] !== DEFAULT_LAYOUT[k])
  if (lay.length) {
    lines.push('', '/* Layout changes (need app.js work, not only CSS): */')
    for (const k of lay) lines.push(`/*   ${k}: ${DEFAULT_LAYOUT[k]} -> ${s.layout[k]} */`)
  }
  return lines.length ? '/* Append at the end of pvj/web/app.css: later rules win. */\n' + lines.join('\n') : '/* Nothing changed from the panel as it is. */'
}

export function exportTheme(s: Settings): string {
  const base = THEMES[s.theme]
  const same = base && (Object.keys(base.tokens) as (keyof Tokens)[]).every((k) => base.tokens[k].toLowerCase() === s.tokens[k].toLowerCase())
  const id = same ? s.theme : 'my-theme'
  return JSON.stringify({ id, name: same ? base.name : 'My theme', tokens: s.tokens }, null, 2)
}
