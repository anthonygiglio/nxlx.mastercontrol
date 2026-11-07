<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Sound reactivity (`pvj-sound`, D96)

The box listens to a capture device (a USB audio input, a line in) and turns what it hears into a few numbers: how loud, a smoothed envelope, beats and a tempo, and six band levels. Those numbers drive the picture: a shader input on the classic renderer today (slowly), OSC to other programs, and every frame in the new renderer's phase 4 (D95).

**It analyses levels only. No audio is stored, recorded, played or sent anywhere, ever.** The samples of one block (20 ms) live in memory for that block and are gone. It is off by default, with no device chosen; with no device the service idles and says so.

## The parts

| Part | Where | What it does |
| --- | --- | --- |
| The service | `pvj/sound.py`, `bin/pvj-sound`, `install/pvj-sound.service` | Runs `arecord` on the chosen device, analyses each block, writes the levels file. No network at all. |
| The levels file | `/run/pvj-sound/levels` | 128 bytes, the layout below, 50 times a second (once a second while idle). Group `audio`, 0640. |
| The service's socket | `/run/pvj-sound/sound.sock` | Answers the panel's account only: `status`, `configure`. |
| The panel's side | `pvj/soundout.py`, `Api.get_sound` and the routes | The page, the meter, OSC out, the shader mapping, telling the service what the Owner chose. |

## Sources (D97)

| Source | What is heard | Bands | Who may choose it |
| --- | --- | --- | --- |
| Capture device | a USB input or a line in: the mix, or one input of a multi-input device | yes | allowed by the Owner; listening on is the Owner's |
| The box's own sound | what the clip or the NDI stream plays, measured inside classic mpv (an `astats` audio filter; no copy of the sound) | no (0) | allowed by the Owner, switched by the Owner or an Operator |
| NDI sound (advanced) | the same measurement, only while NDI plays (D87's one sound source) | no (0) | the same |
| OSC in (advanced, Max/RNBO) | levels sent to the box under `/nxlx/sound/in/` from a render node or a listed address | as sent | the same |

OSC in, the addresses: `/nxlx/sound/in/level` (f 0 to 1, taken as -60 to 0 dB), `/db` (f), `/envelope` (f 0 to 1), `/beat` (no argument or i; one beat), `/bpm` (f), `/bands` (6 x f 0 to 1). Taken from the render nodes and the Owner's "Accept from" list only, at most 100 messages a second from one sender; nothing is sent in answer. The box's own sound is measured by an `astats` filter in classic mpv: only its RMS and peak in dB leave the player, 25 times a second.

All of them end in the one levels file below, and the privacy rule holds for each: the bands leave the box only averaged over 200 ms, at most 5 times a second, none through the support tunnel. Details and what was rejected: D97.

## Why `arecord`

`arecord` is ALSA's own tool (Debian's `alsa-utils`, in Raspberry Pi OS already; the installer adds it where the network allows). It is run as a child of the service:

```
arecord -q -D plughw:CARD=<card id>,DEV=<n> -t raw -f S16_LE -r 16000 -c 1
```

The alternatives were a binding of our own to libasound through ctypes (an ABI to keep, and a crash in it takes the service down with no word) or reading `/dev/snd` ourselves (the ALSA ioctls by hand). `/proc/asound` lists devices but carries no audio. ALSA's plug layer (`plughw:`) mixes down to one channel and converts to 16,000 samples a second in C inside arecord, so the Python side reads a sixth of what a 48 kHz stereo device gives. 16 kHz puts the top band at 5 to 8 kHz, which still carries hats and cymbals; 24 kHz was measured too and cost 60 percent more (below).

The device is never a free ALSA string. The setting holds `<card id>,<device number>` of a capture device that `/proc/asound` lists now, and the service makes it into `plughw:CARD=...,DEV=...` itself: ALSA's `file` and `tee` plugins write audio to a file, and a free string could name them.

## The analysis (per block of 20 ms, 320 samples at 16 kHz)

- **Level:** the RMS in dB (full scale is 0 dB, a full-scale sine is -3 dB), and a level from 0 to 1 for -60 to 0 dB. The peak and a clip flag (a sample at full scale).
- **Envelope:** the RMS smoothed, 10 ms up and 300 ms down, from 0 to 1 on the same scale.
- **Bands:** a 256-point FFT of the last 256 samples (16 ms) with a Hann window, its power summed into six bands and given as dB and 0 to 1 (a full-scale sine inside a band reads about -3 dB there). The bins are 62.5 Hz apart, so the sub band is one or two bins: coarse, and enough for a kick. The FFT is taken as 128 complex points and split afterwards (half the work), and the window's mean is taken out in bins 0 and 1 only (with a Hann window that is exact):

| n | Name | Hz |
| --- | --- | --- |
| 1 | sub | 40 to 120 |
| 2 | bass | 120 to 250 |
| 3 | low mid | 250 to 500 |
| 4 | mid | 500 to 2,000 |
| 5 | high mid | 2,000 to 5,000 |
| 6 | high | 5,000 to 8,000 |

- **Onset:** the spectral flux (what rose since the last block, in compressed magnitudes up to 5 kHz) against the mean of the last second.
- **Beat:** an onset at least 0.18 s after the last beat, while the level is above -60 dB. The pulse is 1 at a beat and falls to 0 in 0.15 s.
- **Tempo:** the median of the last beats' intervals, folded into 75 to 150 BPM, with a confidence (the share of intervals within 6 percent of it). 0 until there are four intervals. It is rough: a guide for a picture, not a clock.
- **Gain trim:** -24 to +24 dB, applied to the levels, never to the samples.

## What it costs (measured 2026-10-18, a bounded local benchmark)

Ten and twenty seconds of generated sound (a sine, a hum and noise, a burst every half second) through `Analyzer.block`, timed by the process's CPU clock, on the development laptop (AMD Ryzen 7 PRO 6850U, WSL, Python 3.14). `tests/test_sound.py` prints its own number at every run.

| What | Per block | CPU per second of sound |
| --- | --- | --- |
| 24 kHz, a 512-point complex FFT (the first build) | 0.548 ms | 2.7 percent of one core |
| 24 kHz, the same as a 256-point complex FFT split in two | 0.390 ms | 2.0 percent |
| **16 kHz, a 256-point real FFT (as built)** | **0.24 ms** | **1.0 to 1.3 percent** |
| writing the 128 bytes (pack and pwrite) | 0.005 ms | |
| reading them (the panel, per look) | 0.010 ms | |

**On a Pi 4 this is an estimate, not a measurement.** A Pi 4 core runs pure Python roughly 5 to 8 times slower than this laptop's, so the analysis should take about 6 to 10 percent of one Pi 4 core, inside the budget of about 10. arecord's own share (ALSA's rate conversion from 48 kHz) was not measured: WSL has no audio device. The unit caps the service at 25 percent of one core (`CPUQuota`) so that a fault cannot starve the player. The page shows the service's own share once it runs (its CPU time over its uptime). `tools/DEVICE-TESTING.md` SR6 measures it on the Pi.

## The levels file

`/run/pvj-sound/levels`, 128 bytes, little-endian, written in place. Version 1. Add fields only in the reserved bytes and raise the version only for a change a reader must know; never move a field.

| Offset | Type | Field |
| --- | --- | --- |
| 0 | 4 bytes | magic `NXSD` |
| 4 | u16 | version (1) |
| 6 | u16 | size of the record (128) |
| 8 | u32 | sequence number (the same at offset 124) |
| 12 | u32 | flags: 1 capturing, 2 a beat in this block, 4 an onset in this block, 8 clipped, 16 idle (no device or off) |
| 16 | f64 | when the block's last sample was heard, CLOCK_MONOTONIC seconds (the same clock in every process of the box); a block read late from a backlog in arecord's pipe carries its own, older time |
| 24 | u32 | block count since the service started |
| 28 | u32 | sample rate (16000) |
| 32 | f32 | RMS level, dB (-90 to +24) |
| 36 | f32 | level, 0 to 1 |
| 40 | f32 | envelope, 0 to 1 |
| 44 | f32 | onset strength, 0 and up (1 is the threshold) |
| 48 | f32 | beat pulse, 0 to 1 |
| 52 | f32 | tempo, BPM (0: not known) |
| 56 | f32 | tempo confidence, 0 to 1 |
| 60 | u32 | beat count since the service started |
| 64 | u16 | number of bands (6) |
| 66 | u16 | reserved (0) |
| 68 | 8 x f32 | band levels, 0 to 1 (unused ones 0) |
| 100 | f32 | peak, dB |
| 104 | 6 x u16 | slow bands: the mean of the last 200 ms of each band, changed at most 5 times a second, 0 to 65535 for 0 to 1 (what may leave the box) |
| 116 | 4 bytes | reserved (0) |
| 120 | u32 | CRC-32 (zlib's) of bytes 0 to 119 |
| 124 | u32 | sequence number again |

**How to read it:** `pread` the 128 bytes; take them only if the magic, the version and the size are right, the two sequence numbers are equal and the CRC matches; otherwise read again (a write was half done) and, after a second miss, keep the last good values. Values older than 0.5 s by the time field mean the service is not delivering; flag 16 means it is idle on purpose.

## The box clock (D116, `pvj/clock.py`)

One tempo and one beat position for the box, kept by itself between beats like a flywheel. Sound alone only has single onsets and a rough tempo; the clock runs on through a gap in what feeds it. It is plain Python with no thread of its own: the panel's once-a-second tick moves it on, and so does every look at the levels.

| Source | What feeds it | How far it is trusted | Stays in charge for |
| --- | --- | --- | --- |
| `link` | Ableton Link, through the helper `pvj-link` (below) | most: the tempo and the place in the bar at once, every time | 5 s, counted from the record's own time |
| `tap` | taps, or a tempo typed in | sets the tempo and the phase at once | until `clear`: the hand wins and sound never takes over from it |
| `osc` | `/nxlx/sound/in/bpm` and `/nxlx/sound/in/beat` | the tempo at once; a beat sets the phase at once | 5 s |
| `sound` | this service's tempo and beats | only at a confidence of 0.5 or more (`SOUND_MIN_CONFIDENCE`), and then only the tempo and a gentle push of the phase: a hit more than 0.3 beat from the grid is ignored, a nearer one moves the phase by 15 percent of its distance | 3 s |

The first of these with a word younger than its time is in charge. When a machine (OSC, Link, sound) goes quiet the next takes over, and the beat position carries on from where it was, never a jump. When all are quiet the last good tempo keeps running, and the status says which source steered it last and how old its word is (`held`). Taps: a first tap with no tempo anywhere sets nothing (a tempo needs at least two); 2 to 8 are averaged, a pause over 2 s starts a new count, a tempo outside 30 to 300 BPM is refused. A tempo can also be set by number and the phase nudged by up to half a beat. The clock is empty after a restart; the tempo is not saved.

**What follows it.** While Link, a tap or OSC is in charge, the levels record that the meter, OSC out and the shader input read has the clock's pulse (1 at a beat, 0 after 0.15 s), tempo (confidence 1) and beat count in place of the service's. This works with no sound device: the levels are then zero and nothing is heard. With no Link, tap or OSC clock, the values are the service's, exactly as before: with sound alone, when the music stops the beat stops. The clock follows sound only so that it can hand over; it reaches the consumers under tap or OSC only. A beat message over OSC is sent only for a count that went up under the same supplier; when the supplier changes (sound to clock or back) or a count goes down, it is a silent resync, and a backwards phase nudge never beats twice. The levels file and its layout are not touched, and the privacy rules below hold unchanged: the clock adds only a tempo and a beat. The Owner's switch for sound reactivity still has to be on for the shader input and OSC out to run, and OSC beats and tempos reach the clock only through the source "Levels sent in over OSC" from the addresses the Owner listed (the user chooses the audio source, h30).

Routes: `GET /api/tempo` (status), `POST /api/tempo` (one of `{"bpm": 30 to 300}`, `{"nudge": -0.5 to 0.5}`, `{"clear": true}`) and `POST /api/tempo/tap`. The Owner and an Operator may use all three; a Guest none; a support session may look and may not change. The tap is also an action, `clock_tap`, for MIDI and OSC (`/pvj/clock/tap`); DMX has none, so a neighbouring fixture on the same universe cannot tap it. Why not a new beat tracker: D116.

## OSC out (`/nxlx/sound/...`)

Sent by the panel, never by the service, and only while the Owner's switch for it is on: "Send sound to the state targets" (the D82 targets, while OSC and "Send state back" are on, each held at every send to the rule and the address locks of D82) and "Send sound to the render nodes" (D94, each held to the same rule at every send). Every number is a float32 unless said. The level, dB, the envelope, the pulse and the tempo are one bundle 20 times a second (about 170 bytes); the bands are a bundle of their own, the slow ones only, 5 times a second (about 230 bytes); a beat is a message of its own.

| Address | Arguments | When |
| --- | --- | --- |
| `/nxlx/sound/level` | f 0 to 1 | every send |
| `/nxlx/sound/db` | f dB | every send |
| `/nxlx/sound/envelope` | f 0 to 1 | every send |
| `/nxlx/sound/pulse` | f 0 to 1 | every send |
| `/nxlx/sound/bpm` | f BPM, f confidence | every send |
| `/nxlx/sound/band/<n>` | f 0 to 1, n = 1 to 6 | 5 times a second: the mean of 200 ms |
| `/nxlx/sound/bands` | 6 x f 0 to 1 | 5 times a second: the mean of 200 ms |
| `/nxlx/sound/beat` | i the beat count | at once, at each beat |

To a target: 20 values bundles, 5 bands bundles and the beats (at most 5.6 a second) each second, about 31 datagrams and 6,000 bytes. The file is looked at every 10 ms while sound goes out, so a beat leaves within about 10 ms of the block it was heard in; where the values may go is worked out again once a second, or at once after a change. Caps: per target 38 datagrams and 12,000 bytes in any second; for all targets together (4 state targets and 8 render nodes at most) 456 and 86,400 (D82's buckets: a rate and a burst). In normal work nothing drops. What does not fit is dropped, never queued. Each socket has a time to live of 1. Add addresses, never rename one: a patch or a layout made from them would break.

## The classic renderer's shaders: a slow envelope

On classic mpv a shader's value changes by writing a new shader text that mpv compiles again: at most five a second, 0.34 to 0.61 s to the screen on a Pi 4 (`pvj/SHADERS.md`). So an Operator may map one number input of the shader on the screen to the level, the beat (smoothed into how busy the beat is) or a band, with where it rests (0 to 1 of the input's range), a depth (-1 to 1) and a smoothing (0.05 to 10 s). It is sent at most twice a second, and only when it moved by 2 percent of the input's range. On the New renderer nothing is compiled (D106), so the same value goes at most twenty times a second and a beat can show; keep the smoothing under about 0.2 s for that, since a longer one flattens each beat into a slow swell. Its default stays 1.5 s.

## Ableton Link (D117, `render/link/`, `pvj/link.py`)

The box can follow a Link session on its network: it takes the tempo and the place in a four-beat bar from whoever is in the session (Ableton Live, a phone app, anything with Link) and gives them to the box clock. **It only follows.** It never sets the tempo, the beat, or play and stop, and there is nothing in the helper that could. **Off by default**, and only the Owner can switch it on: Setup > Show tools > Sound reactivity, "Follow Ableton Link" (`POST /api/tempo/link`, `{"on": true}`), kept in the setting `link`, which is the box's own and is never exported.

| Part | Where | What it does |
| --- | --- | --- |
| The helper | `render/link/pvj-link.cpp`, `install/pvj-link.service` | A small C++ program built at install from Debian's `ableton-link-dev`. GPL-3.0-or-later, its own account and group, no capabilities, a read-only system, no devices. |
| The state file | `/run/pvj-link/state` | 56 bytes, the layout at the top of `pvj-link.cpp`: the tempo, the place in the bar, the peers, and when (CLOCK_MONOTONIC). At most 20 times a second, once a second while nobody is there. Group `pvj-link`, 0640. |
| The socket | `/run/pvj-link/link.sock` | Answers the panel's account only: `status`, `on`, `off`. |
| The panel's side | `pvj/link.py` | The setting, telling the helper, reading the file where the levels are read, `link` in `GET /api/tempo` and `GET /api/sound`. |

**What it does on the network.** Off: nothing, not even listening; the Link object is disabled. On and alone: it listens for another peer on Link's multicast address (224.76.78.75, port 20808) and sends nothing; the only thing that leaves the box is the network's own join for that address. When it hears a peer it waits 0.75 s and then enables Link, because when two Link sessions meet the **older one wins** and the younger takes its tempo: a box that sat alone in a session of its own would hand Link's default tempo to any program that switched Link on (seen on a desk, two peers: the newer one went from 133 to 120 BPM). Waiting makes sure the box is always the younger. When the last peer has been gone for a second it disables Link and listens again.

**The clock.** A session with no peers says nothing, so Link's default tempo never reaches the clock. A record reaches it only with at least one peer and no older than 0.5 s. Link's clock on Linux is CLOCK_MONOTONIC_RAW, not the box's CLOCK_MONOTONIC, so the helper reads both side by side and each record says "this was the place in the bar at this time" on the box's clock. The clock takes the place in the bar and keeps its own beat count. When the peers go, or the helper stops, nothing more is heard and the clock's own rules take over after 5 s, as for any quiet source; a tap made while Link steered takes over then. A session tempo outside 30 to 300 BPM is refused like any other (Link allows 20 to 999).

**Without the package** (it could not be installed, or `--offline` on a box without it) there is no helper, the unit is skipped, the switch keeps its value, the page says Link is not available, and everything else runs as before.

Why not a beat tracker, and why not MIDI clock yet: D116 and D117.

## Who may do what (D80, D84)

| Route | Who | Remote support |
| --- | --- | --- |
| `GET /api/sound` | Operator | as the Operator; a session of the Owner's role sees what the Owner sees |
| `GET /api/sound/levels` | Operator | the same, without the bands |
| `POST /api/sound` (on, device, gain, the two OSC switches) | Owner | the Owner's role only, and never to switch capture on, choose the device while it is on, or switch OSC sending on |
| `POST /api/sound/map` (the shader input) | Operator | the Owner's role only (D80's rule for a support session) |

| `GET /api/tempo` | Operator | as the Operator; a session of the Owner's role sees it |
| `POST /api/tempo`, `POST /api/tempo/tap` (D116) | Operator | none: a helper through remote support may look and not change |
| `POST /api/tempo/link` (D117) | Owner | the Owner's role only, and only to switch it off, never on |

A Guest gets nothing of this.

## Privacy

The values are not sound. But six band levels fifty times a second are close to what a channel vocoder carries, and someone determined could make speech in the room partly understandable from them. So (the coordinator's decision after the review of #145):

- **On the box** the levels file keeps 50 a second (pvj-render's phase 4 needs it). It is readable only by group `audio`, who can already record the microphone.
- **Every reader off the box** (the page's meter on any device, OSC to the D82 targets and the D94 render nodes) gets the bands only as the slow ones: the mean of 200 ms, changed at most 5 times a second. Band levels that move at most 2.5 times a second are below the rate of syllables (about 4 to 5 a second): they carry whether someone speaks, not what. The level, the envelope and the beat may leave up to 20 times a second.
- **Through the support tunnel** the meter has the level, the envelope and the beat, and no band at all.
- The page says this in one line above the switches before the Owner switches sending on, and says plainly where the values go: to the paired Owner and Operator devices that open the page, and over OSC to the devices he lists on the box's own network.
- OSC is off by default; the diagnostics file carries the switches and never a value; no core file is ever written for the service or arecord (`LimitCORE=0`, and the service marks itself not dumpable). Every buffer is bounded; a backlog of more than 2 s in arecord's pipe is thrown away, never shown late.
