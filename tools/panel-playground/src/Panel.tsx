// SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
// SPDX-License-Identifier: Apache-2.0

import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { frameVars, type Settings, type UpdateState } from './settings'

// A working copy of the panel's screens with the panel's own class names, so what is tuned here maps onto app.css.

const BANKS: Record<string, (string | null)[]> = {
  A: ['Intro', 'Tunnel', 'Loop A', 'Loop B', 'Sting', 'Outro', null, null, null, null, null, null],
  B: ['Crowd cam', 'Strobe', 'Rain', null, 'Credits', null, null, null, null, null, null, null],
  C: [null, null, null, null, null, null, null, null, null, null, null, null],
}
const CLIPS = [
  ['intro.mkv', '0:31', '48 MB'], ['tunnel.mp4', '2:04', '212 MB'], ['loop-a.mov', '0:16', '96 MB'],
  ['loop-b.mov', '0:16', '101 MB'], ['sting.mp4', '0:04', '6 MB'], ['outro.mkv', '1:12', '140 MB'], ['logo.png', '', '0.2 MB'],
]
const HUES = [28, 200, 280, 150, 340, 60, 0, 0, 0, 0, 0, 0]

type Props = {
  s: Settings; width: number; height: number
  setScreen: (x: Settings['screen']) => void
  setUpdate: (x: UpdateState) => void
}

export default function Panel({ s, width, height, setScreen, setUpdate }: Props) {
  const L = s.layout
  const wide = width >= 900
  const [bank, setBank] = useState('A')
  const [playing, setPlaying] = useState<string>('A0')
  const [pos, setPos] = useState(12)
  const [toast, setToast] = useState('')
  const [mix, setMix] = useState({ opacity: 100, speed: 100, size: 100, trans: 'cut', flipx: false, flipy: false })
  const [mods, setMods] = useState({ Streams: false, DMX: true, MIDI: false, Schedule: true, 'Projection mapper': false })
  const toastTimer = useRef<number | undefined>(undefined)
  const say = (t: string) => { setToast(t); clearTimeout(toastTimer.current); toastTimer.current = window.setTimeout(() => setToast(''), 1600) }

  const nav = L.nav === 'side' && !wide ? 'bottom' : L.nav
  const tabs = (
    <nav className={'tabs ' + (nav === 'bottom' ? '' : nav)}>
      {(['live', 'mix', 'media', 'system'] as const).map((t) => (
        <button key={t} className={'btn' + (s.screen === t ? ' on' : '')} onClick={() => setScreen(t)}>{t[0].toUpperCase() + t.slice(1)}</button>
      ))}
    </nav>
  )
  const emergency = (
    <div className={'emergency' + (L.emergency === 'dock' && s.screen === 'live' ? ' dock' : '')}>
      {['Fade out', 'Freeze', 'Stop', 'Blackout'].map((b) => <button key={b} className="btn big" onClick={() => say(b + '.')}>{b}</button>)}
    </div>
  )
  const top = (
    <div className="top">
      <h1>nxlx.mastercontrol</h1>
      <span className="pill k">PI 4 · OK</span>
    </div>
  )

  let screen: ReactNode
  // Called as functions, not as components, so a slider being dragged is not remounted on every change.
  if (s.screen === 'live') screen = Live()
  else if (s.screen === 'mix') screen = Mix()
  else if (s.screen === 'media') screen = Media()
  else screen = System()

  return (
    <div className="mctl-frame" style={{ ...(frameVars(s) as CSSProperties), width, height }}>
      <div className="shell">
        {nav === 'top' && tabs}
        <div className="body">
          {nav === 'side' && tabs}
          <main className="screen">{top}{screen}</main>
        </div>
        {L.emergency === 'dock' && s.screen === 'live' && emergency}
        {nav === 'bottom' && tabs}
      </div>
      {toast && <div className="toast" role="status">{toast}</div>}
    </div>
  )

  function Live() {
    const pads = BANKS[bank]
    const nowName = playing ? (BANKS[playing[0]][+playing.slice(1)] || '').toLowerCase().replace(/ /g, '-') + (playing === 'A0' ? '.mkv' : '.mp4') : ''
    const nowCard = (
      <div className="card" key="now">
        <div className="k">Now playing</div>
        <div className="now">{playing ? nowName : <span style={{ color: 'var(--mu)' }}>Nothing</span>}</div>
        <input className="seek" type="range" min={0} max={31} value={pos} onChange={(e) => setPos(+e.target.value)} aria-label="Position" />
        <div className="k" style={{ textTransform: 'none' }}>00:{String(pos).padStart(2, '0')} / 00:31</div>
        <div className="transport">
          <button className="btn" disabled>⏮ Prev</button>
          <button className="btn" onClick={() => setPos(Math.max(0, pos - 10))}>− 10 s</button>
          <button className="btn" onClick={() => setPos(Math.min(31, pos + 10))}>+ 10 s</button>
          <button className="btn" disabled>Next ⏭</button>
        </div>
        <div className="transport2">
          <button className="btn" onClick={() => say('Fade in.')}>Fade in</button>
          <button className="btn" onClick={() => say('Test pattern on.')}>Test pattern</button>
        </div>
      </div>
    )
    const screenCard = L.showScreenCard && (
      <div className="card" key="scr">
        <div className="row between"><span className="k">Screen</span><button className="btn small" onClick={() => say('Snapshot taken.')}>Take snapshot</button></div>
        <div className="k">A snapshot briefly stalls playback, so it only happens when you tap.</div>
      </div>
    )
    const padBlock = (
      <div className="live-col" key="pads">
        <div className="banks">
          {Object.keys(BANKS).map((b) => <button key={b} className={'btn' + (bank === b ? ' on' : '')} onClick={() => setBank(b)}>Bank {b}</button>)}
        </div>
        <div className="pads">
          {pads.map((name, i) => {
            const id = bank + i
            const on = playing === id
            return (
              <button key={id} className={'pad label-' + L.padLabel + (on ? ' on' : '') + (name ? '' : ' empty')}
                onClick={() => { if (name) { setPlaying(on ? '' : id); setPos(0) } else say('Empty pad. Edit pads to give it a clip.') }}>
                {L.padThumbs && name && <span className="thumb" style={{ background: `linear-gradient(135deg, hsl(${HUES[i]} 70% 45%), hsl(${HUES[i] + 40} 60% 20%))` }} />}
                {L.padNumbers && <span className="n">{String(i + 1).padStart(2, '0')}</span>}
                <span className="t">{name || 'Empty'}</span>
              </button>
            )
          })}
        </div>
        <button className="btn full" onClick={() => say('Edit pads opens here.')}>Edit pads</button>
      </div>
    )
    const flowEmergency = L.emergency === 'flow' && emergency
    if (wide && L.liveWide === 'split') {
      return (
        <div className="live-grid split">
          <div className="live-col">{nowCard}{screenCard}{flowEmergency}</div>
          {padBlock}
        </div>
      )
    }
    const first = L.liveOrder === 'pads-first' ? [padBlock, nowCard, screenCard] : [nowCard, screenCard, padBlock]
    return <>{first}{flowEmergency && <><div className="spacer" />{flowEmergency}</>}</>
  }

  function Mix() {
    const sl = (k: 'opacity' | 'speed' | 'size', label: string, min: number, max: number, fmt: (v: number) => string) => (
      <div className="slider">
        <label><span>{label}</span><span className="mono">{fmt(mix[k])}</span></label>
        <input type="range" min={min} max={max} value={mix[k]} onChange={(e) => setMix({ ...mix, [k]: +e.target.value })} />
      </div>
    )
    return (
      <div className="grid2" style={{ display: wide ? undefined : 'flex', flexDirection: 'column', gap: 'var(--sg)' }}>
        <div className="card">
          <h2>Mix</h2>
          {sl('opacity', 'Opacity', 0, 100, (v) => v + '%')}
          {sl('speed', 'Speed', 25, 200, (v) => (v / 100).toFixed(2) + 'x')}
          {sl('size', 'Size', 10, 200, (v) => v + '%')}
          <div className="k">Transition between clips</div>
          <div className="row wrap">
            {[['cut', 'Cut'], ['dip', 'Dip to black'], ['x', 'Crossfade (soon)']].map(([v, l]) => (
              <button key={v} className={'btn' + (mix.trans === v ? ' on' : '')} disabled={v === 'x'} onClick={() => setMix({ ...mix, trans: v })}>{l}</button>
            ))}
          </div>
          <button className="btn" onClick={() => setMix({ ...mix, opacity: 100, speed: 100, size: 100 })}>Reset mix</button>
        </div>
        <div className="card">
          <h2>Mirror</h2>
          <div className="k">For rear projection or a mirror rig.</div>
          <div className="row">
            <button className={'btn grow' + (mix.flipx ? ' on' : '')} onClick={() => setMix({ ...mix, flipx: !mix.flipx })}>Flip left-right</button>
            <button className={'btn grow' + (mix.flipy ? ' on' : '')} onClick={() => setMix({ ...mix, flipy: !mix.flipy })}>Upside down</button>
          </div>
        </div>
      </div>
    )
  }

  function Media() {
    return (
      <>
        <div className="card">
          <h2>Upload</h2>
          <button className="btn solid" onClick={() => say('The file picker opens here.')}>Upload clips</button>
          <div className="k">57.3 GB free on the box.</div>
        </div>
        <div className="card">
          <h2>Quick play</h2>
          <div className="row wrap">
            <button className="btn grow">Play all</button><button className="btn grow">Play once</button><button className="btn grow">Shuffle</button>
          </div>
        </div>
        <div className="card">
          <h2>Clips</h2>
          <div className="list">
            {CLIPS.map(([n, d, sz]) => (
              <div className="item" key={n}>
                <span>{n} <span className="k" style={{ textTransform: 'none' }}>{[d, sz].filter(Boolean).join(' · ')}</span></span>
                <div className="row">
                  <button className="btn small" onClick={() => say('Playing ' + n + '.')}>Play</button>
                  <button className="btn small">Rename</button>
                  <button className="btn small">Delete</button>
                </div>
              </div>
            ))}
          </div>
        </div>
      </>
    )
  }

  function System() {
    return (
      <div className="grid2" style={{ display: wide ? undefined : 'flex', flexDirection: 'column', gap: 'var(--sg)' }}>
        <div className="live-col">
          <div className="card">
            <h2>Vitals</h2>
            <div className="kv"><span>Board</span><b>Raspberry Pi 4 Model B Rev 1.5</b></div>
            <div className="kv"><span>Temperature</span><b>52°C</b></div>
            <div className="kv"><span>Player</span><b><span className="ok-dot" />Running</b></div>
            <div className="kv"><span>This device</span><b>This phone (full)</b></div>
          </div>
          <UpdateCard state={s.update} setState={setUpdate} progressBar={L.progressBar} say={say} />
        </div>
        <div className="live-col">
          <div className="card">
            <h2>Modules</h2>
            <div className="list">
              {Object.entries(mods).map(([m, on]) => (
                <div className="item" key={m}>
                  <span>{m} <span className="k">beta</span></span>
                  <button className={'btn small' + (on ? ' on' : '')} onClick={() => setMods({ ...mods, [m]: !on })}>{on ? 'On' : 'Off'}</button>
                </div>
              ))}
            </div>
          </div>
          <div className="card">
            <h2>Box</h2>
            <div className="kv"><span>Version</span><b className="mono">3.4.0</b></div>
            <div className="kv"><span>Free space</span><b>57.3 GB</b></div>
            <div className="row"><button className="btn grow">Restart the box</button><button className="btn grow">Power off</button></div>
          </div>
        </div>
      </div>
    )
  }
}

function UpdateCard({ state, setState, progressBar, say }: { state: UpdateState; setState: (x: UpdateState) => void; progressBar: boolean; say: (t: string) => void }) {
  const [pct, setPct] = useState(35)
  useEffect(() => {
    if (state !== 'running' && state !== 'uploading') return
    setPct(state === 'uploading' ? 10 : 0)
    const t = window.setInterval(() => setPct((p) => {
      if (p >= 100) { window.clearInterval(t); if (state === 'running') setState('done'); else setState('inbox'); return 100 }
      return p + (state === 'uploading' ? 6 : 3)
    }), 220)
    return () => window.clearInterval(t)
  }, [state, setState])

  const steps = ['checking the signature', 'unpacking', 'switching to the new version', 'waiting for the panel and the player to come back']
  const step = Math.min(3, Math.floor(pct / 25))
  const at = '1 Oct 2026, 21:14'
  const [src, setSrc] = useState<'usb' | 'inbox'>('usb')
  const install = (from: 'usb' | 'inbox') => { setSrc(from); setState('confirm') }

  return (
    <div className="card" id="updatecard">
      <h2>Updates</h2>
      <div className="list">
        <div className="k">Installed: version 3.4.0</div>
        {state === 'done' && <div className="k">Last update: installed version 3.5.0 ({at})</div>}
        {state === 'failed' && <div className="k err">Last update failed: version 3.5.0 did not start, so the box went back to 3.4.0 ({at})</div>}
        {state === 'running' && (
          <>
            <div className="k">Updating: {steps[step]} (step {step + 1} of 4)</div>
            {progressBar && <div className="progress"><div style={{ width: pct + '%' }} /></div>}
            <div className="k">The panel goes away for about a minute and comes back by itself.</div>
          </>
        )}
        {state === 'uploading' && (
          <>
            <div className="k">Uploading pvj-3.5.0.tar.gz ({Math.round(pct * 0.84)} of 84 MB)</div>
            {progressBar && <div className="progress"><div style={{ width: pct + '%' }} /></div>}
          </>
        )}
        {(state === 'usb' || state === 'usb-unsigned') && (
          <div className="item">
            <span>On USB drive sda1: version 3.5.0{state === 'usb-unsigned' ? ' (no signature: it will be refused)' : ''}</span>
            <button className="btn small" onClick={() => install('usb')}>Install</button>
          </div>
        )}
        {state === 'inbox' && (
          <div className="item">
            <span>Uploaded: version 3.5.0</span>
            <button className="btn small" onClick={() => install('inbox')}>Install</button>
          </div>
        )}
        {state === 'confirm' && (
          <div className="confirm">
            <b>Install version 3.5.0{src === 'usb' ? ' from the USB drive' : ''}?</b>
            <span>The panel and the player restart. If the new version does not come up, the box goes back to this one by itself.</span>
            <div className="row">
              <button className="btn small on" onClick={() => { setState('running'); say('Update started.') }}>Install now</button>
              <button className="btn small" onClick={() => setState(src)}>Cancel</button>
            </div>
          </div>
        )}
        {state === 'nothing' && <div className="k">No update waiting. Put pvj-N.N.N.tar.gz with its .sig file in a pvj-update folder on a USB stick, or upload them here.</div>}
        {state !== 'running' && state !== 'uploading' && (
          <button className="btn small" style={{ marginTop: 8 }} onClick={() => setState('uploading')}>Upload an update (.tar.gz and .sig)</button>
        )}
        <div className="k" style={{ marginTop: 8 }}>Only updates signed with your key are installed; older versions are refused; a failed update goes back by itself.</div>
      </div>
    </div>
  )
}
