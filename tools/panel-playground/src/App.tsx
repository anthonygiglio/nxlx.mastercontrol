// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { Drawer, Modal, Slider, Switch } from './ui'
import Panel from './Panel'
import {
  DEVICES, FONTS, KNOBS, fmt, MONOS, THEMES, TOKEN_INFO, UPDATE_STATES, defaults, exportCss, exportTheme, load, loadFont, save,
  type Layout, type Settings, type UpdateState,
} from './settings'
import './panel.css'

export default function App() {
  const [s, setS] = useState<Settings>(load)
  const [fit, setFit] = useState(true)
  const [adjust, setAdjust] = useState(false)
  useEffect(() => { save(s) }, [s])
  useEffect(() => { loadFont(s.font, FONTS); loadFont(s.mono, MONOS) }, [s.font, s.mono])

  const patch = (p: Partial<Settings>) => setS((o) => ({ ...o, ...p }))
  const setLayout = <K extends keyof Layout>(k: K, v: Layout[K]) => setS((o) => ({ ...o, layout: { ...o.layout, [k]: v } }))
  const setScreen = useCallback((screen: Settings['screen']) => setS((o) => ({ ...o, screen })), [])
  const setUpdate = useCallback((update: UpdateState) => setS((o) => ({ ...o, update })), [])

  const controls = <Controls s={s} setS={setS} patch={patch} setLayout={setLayout} />

  return (
    <div className="pg">
      <header className="pg-bar">
        <div className="pg-brand">
          <span className="pg-title">Panel Design Playground</span>
          <span className="pg-sub">nxlx.mastercontrol · app.css with live knobs</span>
        </div>
        <div className="pg-bar-tools">
          <Seg value={s.screen} onChange={(v) => patch({ screen: v as Settings['screen'] })}
            options={[['live', 'Live'], ['mix', 'Mix'], ['media', 'Media'], ['system', 'System']]} label="Screen" />
          <Seg value={s.device} onChange={(v) => patch({ device: v })}
            options={Object.entries(DEVICES).map(([k, d]) => [k, d.label])} label="Device" />
          <label className="pg-inline"><Switch checked={s.compare} onCheckedChange={(v) => patch({ compare: v })} id="compare" /> Next to a phone</label>
          <Seg value={fit ? 'fit' : 'real'} onChange={(v) => setFit(v === 'fit')} options={[['fit', 'Fit'], ['real', '100%']]} label="Zoom" />
          <ExportDialog s={s} />
          <button className="pg-btn pg-only-narrow" onClick={() => setAdjust(true)}>Adjust</button>
          <Drawer open={adjust} onClose={() => setAdjust(false)} title="Adjust the panel">{controls}</Drawer>
        </div>
      </header>
      <div className="pg-main">
        <aside className="pg-rail">{controls}</aside>
        <Stage s={s} fit={fit} setScreen={setScreen} setUpdate={setUpdate} />
      </div>
    </div>
  )
}

function Stage({ s, fit, setScreen, setUpdate }: { s: Settings; fit: boolean; setScreen: (x: Settings['screen']) => void; setUpdate: (x: UpdateState) => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const [box, setBox] = useState({ w: 800, h: 600 })
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver(() => setBox({ w: el.clientWidth, h: el.clientHeight }))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  const main = DEVICES[s.device]
  const frames = s.compare && s.device !== 'phone' ? [DEVICES.phone, main] : [main]
  const gap = 40, padX = 48, padY = 88
  const totalW = frames.reduce((a, f) => a + f.w, 0) + gap * (frames.length - 1)
  const maxH = Math.max(...frames.map((f) => f.h))
  const scale = fit ? Math.min(1, (box.w - padX) / totalW, (box.h - padY) / maxH) : 1

  return (
    <div className="pg-stage" ref={ref} style={{ overflow: fit ? 'hidden' : 'auto' }}>
      <div className="pg-frames" style={{ gap: gap * scale }}>
        {frames.map((f, i) => (
          <figure key={i} className="pg-figure">
            <div className="pg-device" style={{ width: f.w * scale, height: f.h * scale }}>
              <div style={{ transform: `scale(${scale})`, transformOrigin: '0 0', width: f.w, height: f.h }}>
                <Panel s={s} width={f.w} height={f.h} setScreen={setScreen} setUpdate={setUpdate} />
              </div>
            </div>
            <figcaption>{f.label} <span>{f.w} × {f.h} · {Math.round(scale * 100)}%</span></figcaption>
          </figure>
        ))}
      </div>
    </div>
  )
}

function Controls({ s, setS, patch, setLayout }: {
  s: Settings; setS: (f: (o: Settings) => Settings) => void; patch: (p: Partial<Settings>) => void
  setLayout: <K extends keyof Layout>(k: K, v: Layout[K]) => void
}) {
  const groups = Array.from(new Set(KNOBS.map((k) => k.group)))
  const changed = (id: string) => s.knobs[id] !== KNOBS.find((k) => k.id === id)!.def
  const L = s.layout
  return (
    <div className="pg-controls">
      <Section title="Theme" note="The four themes in mctl/themes.d. Change any colour to start your own.">
        <div className="pg-themes">
          {Object.entries(THEMES).map(([id, t]) => (
            <button key={id} className={'pg-theme' + (s.theme === id ? ' on' : '')} onClick={() => patch({ theme: id, tokens: { ...t.tokens } })}>
              <span className="pg-chip" style={{ background: t.tokens.bg, borderColor: t.tokens.ln }}>
                <i style={{ background: t.tokens.ac }} /><i style={{ background: t.tokens.fg }} />
              </span>
              {t.name}
            </button>
          ))}
        </div>
        <div className="pg-tokens">
          {TOKEN_INFO.map((t) => (
            <label key={t.key} className="pg-token" title={t.use}>
              <input type="color" value={s.tokens[t.key]} id={'tok-' + t.key}
                onChange={(e) => setS((o) => ({ ...o, tokens: { ...o.tokens, [t.key]: e.target.value } }))} />
              <span><b>{t.label}</b><em>--{t.key} {s.tokens[t.key]}</em></span>
            </label>
          ))}
        </div>
      </Section>

      <Section title="Typefaces">
        <Pick label="Text" value={s.font} onChange={(v) => patch({ font: v })} options={Object.entries(FONTS).map(([k, f]) => [k, f.label])} />
        <Pick label="Labels and numbers" value={s.mono} onChange={(v) => patch({ mono: v })} options={Object.entries(MONOS).map(([k, f]) => [k, f.label])} />
        <Toggle label="Labels in capitals" checked={L.labelCase === 'uppercase'} onChange={(v) => setLayout('labelCase', v ? 'uppercase' : 'none')} />
      </Section>

      {groups.map((g) => (
        <Section key={g} title={g}>
          {KNOBS.filter((k) => k.group === g).map((k) => (
            <div key={k.id} className={'pg-knob' + (changed(k.id) ? ' changed' : '')}>
              <div className="pg-knob-head">
                <span>{k.label}</span>
                <button className="pg-val" title="Back to the panel's value" onClick={() => setS((o) => ({ ...o, knobs: { ...o.knobs, [k.id]: k.def } }))}>
                  {fmt(k, s.knobs[k.id])}
                </button>
              </div>
              <Slider min={k.min} max={k.max} step={k.step} value={[s.knobs[k.id]]} aria-label={k.label}
                onValueChange={([v]) => setS((o) => ({ ...o, knobs: { ...o.knobs, [k.id]: v } }))} />
            </div>
          ))}
          {g === 'Touch targets' && s.knobs.bh < 44 && <p className="pg-warn">Below 44px, buttons get hard to hit with a thumb at a gig.</p>}
          {g === 'Pads' && (
            <>
              <Pick label="Pad name" value={L.padLabel} onChange={(v) => setLayout('padLabel', v as Layout['padLabel'])}
                options={[['bottom', 'At the bottom (current)'], ['top', 'At the top'], ['center', 'In the middle']]} />
              <Toggle label="Pad numbers" checked={L.padNumbers} onChange={(v) => setLayout('padNumbers', v)} />
              <Toggle label="Clip thumbnails (idea)" checked={L.padThumbs} onChange={(v) => setLayout('padThumbs', v)} />
            </>
          )}
        </Section>
      ))}

      <Section title="Layout" note="These change the structure, so they need app.js work as well as CSS.">
        <Pick label="Tabs" value={L.nav} onChange={(v) => setLayout('nav', v as Layout['nav'])}
          options={[['bottom', 'Bottom (current)'], ['top', 'Top'], ['side', 'Side rail when wide']]} />
        <Pick label="Live screen, wide" value={L.liveWide} onChange={(v) => setLayout('liveWide', v as Layout['liveWide'])}
          options={[['stacked', 'One column (current)'], ['split', 'Transport left, pads right']]} />
        <Pick label="Live screen order" value={L.liveOrder} onChange={(v) => setLayout('liveOrder', v as Layout['liveOrder'])}
          options={[['transport-first', 'Now playing first (current)'], ['pads-first', 'Pads first']]} />
        <Pick label="Fade out, Freeze, Stop, Blackout" value={L.emergency} onChange={(v) => setLayout('emergency', v as Layout['emergency'])}
          options={[['flow', 'End of the page (current)'], ['dock', 'Always above the tabs']]} />
        <Toggle label="Screen snapshot card" checked={L.showScreenCard} onChange={(v) => setLayout('showScreenCard', v)} />
      </Section>

      <Section title="Updates card" note="The card from this branch. Pick a state; Install and Upload also work inside the preview.">
        <div className="pg-states">
          {UPDATE_STATES.map((u) => (
            <button key={u.id} className={'pg-state' + (s.update === u.id ? ' on' : '')} onClick={() => patch({ update: u.id, screen: 'system' })}>{u.label}</button>
          ))}
        </div>
        <Toggle label="Progress bar (idea; today it is text only)" checked={L.progressBar} onChange={(v) => setLayout('progressBar', v)} />
      </Section>

      <button className="pg-btn pg-reset" onClick={() => { const d = defaults(); setS((o) => ({ ...d, device: o.device, screen: o.screen })) }}>
        Reset to the panel as it is
      </button>
    </div>
  )
}

function ExportDialog({ s }: { s: Settings }) {
  const [open, setOpen] = useState(false)
  const css = exportCss(s), theme = exportTheme(s)
  return (
    <>
      <button className="pg-btn pg-primary" onClick={() => setOpen(true)}>Export</button>
      <Modal open={open} onClose={() => setOpen(false)} title="Take it back to the code"
        description="Only what differs from the panel today. Paste the CSS at the end of app.css so it overrides the rules above it.">
        <Code title="mctl/web/app.css" text={css} />
        <Code title="mctl/themes.d/<id>.json" text={theme} />
        <Code title="Settings (paste into a chat to recreate this look)" text={JSON.stringify({ theme: s.theme, tokens: s.tokens, font: s.font, mono: s.mono, knobs: s.knobs, layout: s.layout })} />
      </Modal>
    </>
  )
}

function Code({ title, text }: { title: string; text: string }) {
  const [done, setDone] = useState('')
  const pre = useRef<HTMLPreElement>(null)
  const copy = () => {
    navigator.clipboard.writeText(text).then(() => setDone('Copied'), () => {
      const r = document.createRange(); r.selectNodeContents(pre.current!)
      const sel = window.getSelection(); sel?.removeAllRanges(); sel?.addRange(r); setDone('Selected; press Copy')
    })
    setTimeout(() => setDone(''), 1800)
  }
  return (
    <div className="pg-code">
      <div className="pg-code-head"><span>{title}</span><button className="pg-btn" onClick={copy}>{done || 'Copy'}</button></div>
      <pre ref={pre}>{text}</pre>
    </div>
  )
}

function Section({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return (
    <section className="pg-section">
      <h3>{title}</h3>
      {note && <p className="pg-note">{note}</p>}
      {children}
    </section>
  )
}

function Seg({ value, onChange, options, label }: { value: string; onChange: (v: string) => void; options: string[][]; label: string }) {
  return (
    <div className="pg-seg" role="radiogroup" aria-label={label}>
      {options.map(([v, l]) => (
        <button key={v} role="radio" aria-checked={value === v} className={value === v ? 'on' : ''} onClick={() => onChange(v)}>{l}</button>
      ))}
    </div>
  )
}

function Pick({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: string[][] }) {
  const id = 'pick-' + label.replace(/\W+/g, '-').toLowerCase()
  return (
    <label className="pg-pick" htmlFor={id}>
      <span>{label}</span>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </label>
  )
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  const id = 'tg-' + label.replace(/\W+/g, '-').toLowerCase()
  return (
    <label className="pg-toggle" htmlFor={id}>
      <span>{label}</span>
      <Switch id={id} checked={checked} onCheckedChange={onChange} />
    </label>
  )
}
