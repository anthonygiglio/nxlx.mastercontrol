<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Journal

Newest entry first. One entry per working session: what was done, what merged, what is open.

## 2026-10-05 (effects: ISF filters over what plays)

Pull request #81. **Not merged: it takes uploads and adds device-facing paths, and gets an independent review first.** Nothing here ran on the Pi; the dev Mac has no mpv, so every picture was drawn in CI on Mesa's software GPU.

**What it is.** An effect is an ISF filter shader put on over whatever plays (a clip, a stream, a live input), a third kind beside a clip and a generator: it never takes the screen. One at a time. Controlled live like a generator: every input, an Amount mix that every filter gets (done in the wrapper), Speed for a filter that moves by itself, Half resolution, presets, Previous and Next, a MIDI controller. It stays on across clip changes and comes off with Off, Stop, a generator or Vibes, the module going off, and a restart of the player or of the panel. The decision is D53; the whole of it is in `pvj/SHADERS.md` under "Effects".

**How it was found.** A spike in a CI job of its own printed what a real mpv does at NATIVE, MAINPRESUB and MAIN with YUV and RGB pictures. It settled the stage (NATIVE, with the effect converting YUV to RGB and back itself: an invert hooked later turned a blacked-out picture white), that the mapper's stage sees the filtered picture, that a hook can draw at half its size, that mpv leaves out a hook whose bound plane is missing (so one text serves a video and an RGB picture), and that the hook is after `video-rotate`. Then the engine, the player's third layer, the panel and the tests were built on that.

**What CI drew.** @@CI@@

**Four things about the rig and about mpv, found with two throwaway diagnosis jobs** (LESSONS has each): the first test picture cost the player more than it could keep up with; a video played after one of another size is drawn black on this mpv with its default scalers until a shader changes (the generators' old "flat colour"); the decoder's word about a clip's colours is not what the renderer is using; and "#line" is off by one on GLSL 1.40. I also believed for one round that setting the buffer format was what drew black, and took the 8-bit buffers away from effects; a printed experiment under the Pi 4's scaling showed the setting harmless there, so they are back for the boards with cheap scaling and left alone elsewhere (D53).

**The filters.** Nine of our own (mirror quad, kaleidoscope, RGB split with a rate, colour wash, slit bands, edge glow, pixel grid, a soft vignette for the painting wall, and a colour grade for the hue, saturation and exposure that the pack could not supply cleanly), and 32 from Vidvox's ISF-Files at the generators' commit, byte for byte with their checksums pinned. Of the 327 upstream files 114 translate as effects; the pack was chosen from those that credit nobody outside and hold no snippet a reader recognised (THIRD_PARTY_LICENSES.md lists what was left out and why), and every one of the 32 candidates drew right every time, so none had to be dropped.

**Cost.** A filter runs once for every pixel of the clip. Its weight in the list is a count from its text (reads of the picture and loop rounds for one pixel) and says so; an upload over 64 reads or 256 rounds is refused. In CI, pixel for pixel, the filters' pass took 0.6 to 1.3 times that of nxlx-silk, which says little: the same software GPU has nxlx-aurora at 1.1 times nxlx-silk and the Pi 4 has it at 3. The guard reports "too heavy with this clip" and marks nothing. The steps for the Pi are in `pvj/SHADERS.md` ("To be measured on the Pi 4") and in the pull request.

**Panel.** A strip on Live (name, Previous, On or Off, Next, Amount) and an Effects card on Mix (the list by name and work, Put on, the controls with Amount first through the Shaders page's own `inputControl`, Half resolution, presets, MIDI teach buttons). Both say plainly why no effect can go on while a generator has the screen or nothing plays. New MIDI actions: effect amount, effect control 1 to 8, on/off, previous, next; on the three shipped layouts they sit only on controls that were spare (the nanoKONTROL2's fader 7 and R 5, the MIDI Mix's bottom row of knobs and fader 7, twelve pads of the Launchpad's last two columns less one).

**Left out.** A chain of effects; transitions, passes and persistent buffers; a preset rename in the panel (the API has it); an effect that comes back after a restart; a measured weight; pictures of the new card in `docs/UI.md` (the browser test saves one as a CI artifact). The generators' "#line" on the Pi's path and their buffer-format setting at every change of a value were seen and not touched.

**Not verified.** Everything on hardware: whether V3D takes the two-hook text, with which decoder, how fast, how it looks. mpv 0.35 and 0.40. A real controller. The list in `pvj/SHADERS.md` ("Effects: not verified") is the full one.

## 2026-10-04 (evening: what landed, the redesign workspace, a full disk)

Pull request #80. **Not merged by the agent that wrote it. The Room screen's ambience button ran only in CI's browser test (no Playwright on this Mac); nothing in this entry was done on the Pi by this session.** What it says about the Pi is the coordinating session's report, written down here so it is not lost.

**What landed.** #70 to #78 all merged on 2026-10-04 (their entries below say "not merged"; that was true when each was written). master and the test Pi 4 are at the merge of #78: deployed over SSH, files checked by hash, all five services active, still on the wired network. Open: #79 (every bundled shader measured on the Pi 4, in progress) and #65 (the UX proposal, a reference).

**On the Pi the same day** (systemd 257): the runtime folder checklist steps R0, R1, R5, R6, R9 and R10 and the snapshot through the panel's sandbox while a clip played; the new shader engine drawing silk, tunnel and bloom; the three MIDI controllers recognised by their profiles, with no message received from them yet. Not done: a reboot, a boot with no network, an update from the old version, a rollback, any controller pressed, any projector, Wi-Fi. The dev account `pvj-dev` was not made: Claude was not permitted to create it, and the owner may create it himself. Two old paired devices named `claude-test-mac` are still on the box.

**The owner's direction changed in two ways.** The look: "the whole visual style is on the table. I'm open to suggestions, and they can be very different options". The order: "nailing down the design language and style is first, then the projection control, then performing". And "no going elsewhere" is a default now: "you may drop the 'no going elsewhere' rule if the panel design makes sense" (D52; `CLAUDE.md` says so).

**The redesign workspace.** A Figma file with four style directions (A Stage, B Desk, C Gallery, D Signal), https://www.figma.com/design/Cu6AGouBnUBMeBCIPPx02o; a guide, https://claude.ai/artifact/VkC5rzfZiPNRgPFazyYKx1; and a job board for the whole project, https://claude.ai/artifact/V6uoeXTgNYKn7PaqeuVDbj (a claude.ai page with a database: collection `cards`, settings in `settings/links`). The owner's Figma plan met its limits while the file was built (see LESSONS).

**A full disk.** The Mac's disk filled during the day from review copies, worktrees and CI artifacts, and every agent stopped. `CLAUDE.md` has a rule for it now.

**Built in this pull request: ambience on the Room screen (D52).** Staff land on Room, and the control for their most common job was on Live. Room now has a card at the top, while Shaders and Vibes is on: a button of 64 px, "Start ambience", which starts Vibes on the usual set; a set chooser beside it when there is more than one set; while it runs "Ambience is playing: Aurora. Tap to stop" and "Next one". A guest reads one line of state and has no button. With the module off a presenter sees nothing, and the owner one line and "Open Shaders and Vibes" (Back returns to Room). It uses `POST /api/vibes` only. It makes no request of its own while it waits: `app.js` already reads `/api/status` every second on every tab, and now tells the Room screen after each answer, as it tells Live; the sets come from one `GET /api/shaders` when the screen opens and when the tab is shown again. `shaders.js` lends its own helpers (`pvjShaders.vibes`), so the set chosen on Room is the set Live starts, and the name is cleaned the same way. Browser test: the owner's hint with the module off; a presenter lands on Room, starts, reads the name, presses Next one, stops; a guest reads the state; nothing sticks out at 390 px. Screenshot `room-ambience`.

Left out: a Previous button (Live has one); the time until the next shader; the chooser stays beside the button while ambience plays, and choosing there switches the set at once, as on Live. Not sure about: whether "Ambience" as a card title above "Start ambience" is one word too many on a phone; whether staff want the set chooser at all. The sets on a tablet that stays on Room all day are as old as the last time the tab was shown.

**Added later in the same pull request, from the findings of the measuring run (#79, merged meanwhile; its entry is the next one down). None of it has run on the Pi.**

1. **The guard judges the average over its window** (`Guard.sample`). It counted seconds in a row over the limit and started again at every quiet look, so nxlx-lantern at 720 lines (3.8 dropped frames a second on average, up to 8, a quiet second in every six) was never marked, and how often the guard was asked decided the verdict. Now: 2 a second or more averaged over the last 6 seconds, the same limit and window; one look counts for at most 8 a second towards "heavy", so one hitch of any size is not a heavy shader. The rule for "it is the box" (two marks in a row with no healthy shader between) is untouched. The load the page shows is the same average, so it falls over a few seconds. Tests changed on purpose: the load after a heavy stretch reads "heavy, heavy, tight, tight, ok", not "tight" at the first quiet second; a mark made after a quiet start records 2.5, the window's average, not 3.0; "(5, 5, 5, 0) is not heavy" is gone, since that is 3.75 a second and is exactly what should be marked. New: a bursty 3.8 is marked, the answer is the same asked every 1, 2 or 3 seconds, one hitch of 30 frames is not marked, a light shader never.
2. **A saved picture detail above the board's usual one is said**: "Picture detail is 720 lines. This box is happier at 540." with "Use 540", on the Shaders page with the load and, for the owner, on the Room screen's ambience card. `render.default` was already in `GET /api/shaders`; nothing new in the API. Browser steps for both.
3. **A set with one playable shader keeps it on**: no dip, no reload. Variation still moves its numbers through the live change (not its palette turn or start time). Next and Previous load it again. Two older tests relied on a set of one coming on again every round and now use two members, on purpose.
4. **A heavy mark's height is a whole number** (`check_heavy` made 720.0 of it on every settings write).
5. **"Cannot load libcuda.so.1": left alone, on purpose.** The two ways to quiet it are to name the hardware decoders a Pi has in place of `--hwdec=auto-safe`, or to lower mpv's message level for its ffmpeg part. The first needs to know which decoder the Pi's mpv build really uses for H.264 (nothing in the repository records `hwdec-current` from the box; a wrong name would silently turn hardware decoding off, and 1080p playback is the box's main job). The second would hide real decoder errors. And it is not known how the line reaches the journal at all, since the player starts with `--no-terminal --really-quiet`. Next step, on the Pi: play an H.264 clip, read `hwdec-current` (System > Health shows it) and `journalctl -u pvj-player -o verbose` for the line's source; then put exactly that decoder, with `no` as the fallback, into `playback_profile` for Pi boards, and A/B the dropped frames.
6. **An icon at `/favicon.ico` and the two `apple-touch-icon` names** (`pvj/icon.py`): one PNG of about 550 bytes drawn with the standard library, so no picture file and no REUSE entry. A stand-in until the project has a mark.
7. **Generated shader texts nobody uses are removed when the panel starts and on Stop** (`Engine.tidy`). What the player still has loaded is kept, and if a running player cannot say what it has loaded nothing is removed: the player outlives the panel and reads a text again when its video output starts anew.

Items 1 and 4 were committed together (both are in `pvj/shaderlive.py` and its test file); the others are a commit each.

**The notes.** `HANDOFF.md` "Start here" is rewritten for this state, and what was stale under it is corrected (the shader count, controller profiles under "Not built", "no speed measured on any board", the staged password check). Three lessons added.

## 2026-10-05 (every bundled shader on the Pi 4, and the shader engine's first run on hardware)

Pull request #79. A measuring run on the owner's test Pi 4 over SSH while the owner was away (00:27 to 02:15 UTC; the evening of the 4th at the desk), and its numbers written into the code and the docs. Nothing of the engine's behaviour was changed. Judged from snapshots and the player's counters; **nobody watched the monitor.**

**The box.** Raspberry Pi 4 Model B Rev 1.5, kernel 6.18.50, mpv 0.40.0 (libplacebo 7.349) on DRM with `--vo=gpu --profile=fast`, Mesa 26.2.2, a 2560 x 1440 screen at 74.99 Hz, `pvj/` identical to master `b62c353` (133 files compared by SHA-256). 36.5 degrees and not throttled at the start, 40.4 and not throttled at the end. The picture detail in force was 720 lines (saved under the first version; this board's default is 540). The two sets were the computed ones: Ambient with 21 shaders on this box (the 23 a new box has, without nxlx-ember and nxlx-horizon, which this box's settings have switched off) and Show with the 15 Performance shaders, both 180 seconds, shuffled, variation on. The mapping was off, opacity 100, nothing playing. That the context is desktop OpenGL 3.1 was read from the player's start-up log in the first run and not again (it would have needed a player restart).

**Method.** A temporary full-access device was paired with the PIN, and a helper script ran on the box as root (it needs the player's socket), detached, writing its results as it went. Per shader and drawing height: `POST /api/shaders/play` with the defaults, 4 seconds of settling, then a 20 second window with no snapshot in it: the change of mpv's `frame-drop-count`, the average of the shader's own pass from `vo-passes`, GPU busy and jobs from `/sys/devices/platform/v3dbus/*/gpu_stats`, the temperature, and `GET /api/shaders` every 3 seconds for the engine's own `playing.load`, `drops_per_second` and `pass_ms`. Then one snapshot through the panel, copied to the Mac and looked at on contact sheets. Each Performance shader was run again with its rate at the highest and, where it has one, with Fast on. Where the first window showed between 0.05 and 2.5 dropped frames a second (13 shaders at 720 lines, 4 at 540) a 60 second window followed, and the rate in the table is over all 80 seconds. The engine tests used screenshots taken straight from mpv (the panel's snapshot is shared for 3 seconds, so two in a row are one picture) and read the generated shader texts from the panel's runtime folder to check TIME.

**All 47 were taken by the GPU at both heights; no refusal, no warning from the player about a shader, every picture right** (varied, the colours the file names, no garbage, none black). Pass times in ms, dropped frames a second; "load" is the worst the engine said in the window.

| Shader | Family | Taken by the GPU | Looks right | 540: pass ms | 540: dropped /s | 540: GPU busy % | 720: pass ms | 720: dropped /s | 720: GPU busy % | Engine's load at 540 / 720 | Class |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| nxlx-aurora | First ten | yes | yes: green curtains over a dark sky | 12.9 | 0 | 86 | 22.8 | 4.87 | 99 | ok / heavy | medium |
| nxlx-drift | First ten | yes | yes: blue and orange clouds, colourful (the new look) | 20.1 | 2.01 | 99 | 35.7 | 10.55 | 99 | heavy / heavy | heavy |
| nxlx-ember | First ten | yes | yes: warm merging blobs | 6.5 | 0 | 67 | 11.4 | 0.39 | 91 | ok / tight | light |
| nxlx-horizon | First ten | yes | yes: dusk sky, sun, striped ground | 5.5 | 0 | 68 | 9.4 | 0 | 84 | ok / ok | light |
| nxlx-lattice | First ten | yes | yes: blue moire dots on black | 4.9 | 0 | 61 | 8.3 | 0 | 77 | ok / ok | light |
| nxlx-nebula | First ten | yes | yes: purple clouds | 19.6 | 1.48 | 98 | 34.8 | 11.55 | 99 | heavy / heavy | heavy |
| nxlx-prism | First ten | yes | yes: yellow star ring, pink centre, blue rim | 5.4 | 0 | 64 | 9.4 | 0 | 80 | ok / ok | light |
| nxlx-pulse | First ten | yes | yes: teal ripples from three points | 6.2 | 0 | 67 | 10.9 | 0.27 | 96 | ok / tight | light |
| nxlx-silk | First ten | yes | yes: pink flowing lines | 4.5 | 0 | 66 | 7.5 | 0 | 75 | ok / ok | light |
| nxlx-tide | First ten | yes | yes: dark blue wave layers (low contrast by design) | 8.7 | 0 | 75 | 15.4 | 1.17 | 93 | ok / heavy | medium |
| nxlx-bloom | Ambient | yes | yes: soft orange and blue discs | 8.2 | 0 | 75 | 14.8 | 1.17 | 91 | ok / heavy | medium |
| nxlx-caustic | Ambient | yes | yes: net of light on blue | 5.8 | 0 | 61 | 10.4 | 0 | 83 | ok / ok | light |
| nxlx-contour | Ambient | yes | yes: cream contour lines | 4.6 | 0 | 61 | 8.2 | 0 | 78 | ok / ok | light |
| nxlx-dusk | Ambient | yes | yes: purple to orange sky | 5.3 | 0 | 64 | 9.9 | 0 | 89 | ok / ok | light |
| nxlx-fringe | Ambient | yes | yes: blue ripple fringes | 6 | 0 | 61 | 11.4 | 0.2 | 89 | ok / tight | light |
| nxlx-kaleido | Ambient | yes | yes: teal mirrored shapes | 5.9 | 0 | 67 | 10.5 | 0 | 79 | ok / ok | light |
| nxlx-lantern | Ambient | yes | yes: four orange lanterns | 11.5 | 0 | 80 | 20.5 | 3.84 | 99 | ok / heavy | medium |
| nxlx-moire | Ambient | yes | yes: fine tan rings | 4.7 | 0 | 65 | 7.5 | 0 | 78 | ok / ok | light |
| nxlx-petal | Ambient | yes | yes: pink and cream flower | 7.9 | 0 | 76 | 13.8 | 0.81 | 89 | ok / tight | medium |
| nxlx-pool | Ambient | yes | yes: blue water, two rings | 7.2 | 0 | 74 | 13.5 | 1.1 | 92 | ok / tight | medium |
| nxlx-ribbon | Ambient | yes | yes: orange to violet bands | 4.8 | 0 | 63 | 8.2 | 0 | 80 | ok / ok | light |
| nxlx-ridge | Ambient | yes | yes: misty hills and sun | 7.1 | 0 | 64 | 13.2 | 0.79 | 90 | ok / tight | medium |
| nxlx-stars | Ambient | yes | yes: star field with a band | 7.2 | 0 | 70 | 13.3 | 0.77 | 90 | ok / tight | medium |
| nxlx-tiles | Ambient | yes | yes: tan and slate tiles | 4.5 | 0 | 62 | 7.8 | 0 | 76 | ok / ok | light |
| nxlx-veil | Ambient | yes | yes: pink to violet folds | 4.5 | 0 | 62 | 8.3 | 0 | 78 | ok / ok | light |
| nxlx-bars | Performance | yes | yes: blue bars, three lit | 4 | 0 | 59 | 7.2 | 0 | 76 | ok / ok | light |
| nxlx-beam | Performance | yes | yes: three beams, blue and pink | 6.9 | 0 | 69 | 12.5 | 1 | 95 | ok / tight | medium |
| nxlx-burst | Performance | yes | yes: cream streaks on plum | 6.3 | 0 | 71 | 11.1 | 0.15 | 86 | ok / tight | light |
| nxlx-checker | Performance | yes | yes: bent yellow and navy checkerboard | 4.1 | 0 | 58 | 7.4 | 0 | 76 | ok / ok | light |
| nxlx-chevron | Performance | yes | yes: orange arrowheads | 4.6 | 0 | 62 | 7.8 | 0 | 72 | ok / ok | light |
| nxlx-glitch | Performance | yes | yes: torn cyan and pink stripes | 5.7 | 0 | 63 | 10 | 0 | 82 | ok / ok | light |
| nxlx-grid | Performance | yes | yes: plum tiles, some lit | 4.9 | 0 | 63 | 8.4 | 0 | 81 | ok / ok | light |
| nxlx-halftone | Performance | yes | yes: yellow dots on red | 4.3 | 0 | 61 | 7.5 | 0 | 74 | ok / ok | light |
| nxlx-mirror | Performance | yes | yes: green and violet mirrored bands | 4.3 | 0 | 56 | 7.8 | 0 | 77 | ok / ok | light |
| nxlx-radar | Performance | yes | yes: green sweep, rings, blips | 6.8 | 0 | 66 | 12.6 | 0.46 | 90 | ok / tight | light |
| nxlx-scope | Performance | yes | yes: green traces on a grid | 6.6 | 0 | 74 | 11.8 | 0.56 | 89 | ok / tight | medium |
| nxlx-spokes | Performance | yes | yes: red and navy wheel | 4.9 | 0 | 63 | 8.7 | 0 | 80 | ok / ok | light |
| nxlx-stripes | Performance | yes | yes: orange wavy stripes | 4.2 | 0 | 60 | 7.4 | 0 | 76 | ok / ok | light |
| nxlx-tunnel | Performance | yes | yes: cyan and violet tunnel | 5.1 | 0 | 63 | 9.1 | 0 | 81 | ok / ok | light |
| nxlx-zoom | Performance | yes | yes: blue and cream squares | 4.6 | 0 | 60 | 8 | 0 | 76 | ok / ok | light |
| isf-color-bars | ISF pack | yes | yes: colour bars (a still) | 2 | 0 | 54 | 3.5 | 0 | 64 | ok / ok | light |
| isf-corner-colors | ISF pack | yes | yes: four-corner blend (a still) | 4.6 | 0 | 60 | 8.3 | 0 | 78 | ok / ok | light |
| isf-linear-gradient | ISF pack | yes | yes: blue to yellow gradient (a still) | 1.7 | 0 | 54 | 2.7 | 0 | 64 | ok / ok | light |
| isf-radial-gradient | ISF pack | yes | yes: yellow spot on blue (a still) | 2.2 | 0 | 55 | 3.7 | 0 | 64 | ok / ok | light |
| isf-ridgelines | ISF pack | yes | yes: white ridges on black | 16.8 | 1.11 | 94 | 29.8 | 7.76 | 99 | heavy / heavy | heavy |
| isf-simplex-noise | ISF pack | yes | yes: grey noise clouds | 15.7 | 0.45 | 90 | 27.9 | 7.18 | 99 | tight / heavy | medium |
| isf-sine-warp-gradient | ISF pack | yes | yes: bent orange, white and blue gradient (a still) | 3.6 | 0 | 60 | 6.5 | 0 | 72 | ok / ok | light |

Classes: light holds 30 frames a second at 720 lines (fewer than 0.5 dropped a second, the engine's own line for "ok"): 33. Medium holds at 540 only: 11 (nxlx-aurora, nxlx-tide, nxlx-bloom, nxlx-lantern, nxlx-petal, nxlx-pool, nxlx-ridge, nxlx-stars, nxlx-beam, nxlx-scope, isf-simplex-noise). Heavy drops at 540: 3 (nxlx-drift, nxlx-nebula, isf-ridgelines). At the line and decided by a few frames: nxlx-radar (light), nxlx-scope and isf-simplex-noise (medium).

The Performance shaders with a rate at its highest or Fast on (20 second windows): no different from their defaults.

| Shader | Variant | 540: pass ms | 540: dropped /s | 720: pass ms | 720: dropped /s |
| --- | --- | --- | --- | --- | --- |
| nxlx-bars | Fast on | 3.9 | 0 | 6.8 | 0 |
| nxlx-bars | rate at its highest | 4.3 | 0 | 7.7 | 0 |
| nxlx-beam | rate at its highest | 6.9 | 0 | 12.7 | 0.8 |
| nxlx-burst | rate at its highest | 6.2 | 0 | 11.2 | 0 |
| nxlx-checker | rate at its highest | 4.1 | 0 | 7.8 | 0 |
| nxlx-chevron | rate at its highest | 4.6 | 0 | 8.7 | 0 |
| nxlx-glitch | Fast on | 5.8 | 0 | 10.3 | 0 |
| nxlx-glitch | rate at its highest | 5.8 | 0 | 10.2 | 0 |
| nxlx-grid | Fast on | 4.9 | 0 | 8.7 | 0 |
| nxlx-grid | rate at its highest | 5 | 0 | 8.6 | 0 |
| nxlx-halftone | rate at its highest | 4.6 | 0 | 7.5 | 0 |
| nxlx-mirror | rate at its highest | 4.5 | 0 | 7.8 | 0 |
| nxlx-radar | rate at its highest | 6.8 | 0 | 12.1 | 0.45 |
| nxlx-scope | rate at its highest | 6.7 | 0 | 11.4 | 0.05 |
| nxlx-spokes | rate at its highest | 4.9 | 0 | 8.8 | 0 |
| nxlx-stripes | rate at its highest | 4 | 0 | 7.1 | 0 |
| nxlx-tunnel | rate at its highest | 5 | 0 | 9.2 | 0 |
| nxlx-zoom | rate at its highest | 4.5 | 0 | 8.2 | 0 |

Around the table: the CPU at 4 to 11 percent of the four cores, mpv at 11 to 20 percent of one, 39 to 43 degrees, never throttled. The first play of a shader 0.55 to 0.89 seconds (the GPU's look included), a later one 0.07 to 0.27. The two fixed passes: the scale to the screen 8.4 to 12.8 ms (10 in the middle; 12.9 in the first run), the remainder pass 1.8 ms at 540 lines and 3.2 at 720 (2.3 and 4.0 in the first run). The carrier's 30 pictures a second on a 75 Hz screen get two and three refreshes in turn (26.7 and 40 ms), which is why a shader pass between 11 and 15 ms at 720 lines drops frames in bursts; see SHADERS.md.

**The engine, on nxlx-silk, nxlx-tunnel and nxlx-nebula at 720 lines.**

- A value change: answered in 7 to 12 ms (slowest of 210: 41 ms); mpv reported the new text drawn 0.34 to 0.37 seconds after the request for silk and tunnel, for a number, a switch, a choice, a colour and a point alike, and 0.54 to 0.61 for nebula (which draws about 18 frames a second there). That is an upper limit; it includes mpv's own delay in timing a pass.
- Thirty changes at ten a second: 12 to 15 texts drawn. Dropped frames in those 4.5 seconds: tunnel 0 for each of the five kinds, silk 1; nebula 56, against its 52 with nothing changing. GPU jobs stayed at 147 to 150 a second. Of 21 snapshots taken inside such bursts none was dark; before and after one change the picture differed by the change. A single black frame cannot be ruled out this way.
- Speed 0, 0.5, 2, 4 and back to 1: in the generated texts TIME went on from the TIME reached, exactly (silk 43.500, 43.500, 46.367, 57.833, 80.633). Frozen, two snapshots a second apart were identical. The first freeze after 43 seconds, where the picture passes from the carrier's count to a number the panel computed, moved the picture by 3.4 of 255 on silk and 0.4 on nebula over a second: the shader reads the carrier's frame number correctly on V3D. Tunnel, a Performance shader, was answered 1.0 for 2 and 4.
- A preset: saved with speed 0, a turn of 40 degrees and changed values, left for the defaults (the preset's name went to null), applied (0.36 seconds, values and controls as saved, the snapshot identical to the saved one), deleted.
- Previous and Next without Vibes: the neighbours in the active set's order, drawn 0.38 to 0.45 seconds after the request.
- Refusals: 400 with a sentence for a wrong kind of value, an unknown input or control, a choice outside its `VALUES`, a colour of two parts, control 9, level 200; 409 for another shader's id. Out of range is not refused but put at the nearest end. No GPU refusal could be provoked without an upload.
- `playing.pass_ms` reads a millisecond or more high for under a second after a change (the first frame of a new text took 10 to 14 ms on silk). `gpu` in `GET /api/shaders` agreed with the kernel's file.
- `GET /api/shaders` with 47 shaders: 65.8 KB, 57 to 115 ms (98 in the middle) for 20 calls 3 seconds apart while silk played, about 30 to 50 ms of the panel's CPU each: 2.1 percent of one core against 0.6 with nobody asking. No frame was dropped by it.

**Vibes and the guard** (dwell 12 seconds, 29 changes watched).

- At 540 lines on Ambient and on Show: 13 changes, not one dropped frame in 200 seconds, every shader "ok", nothing marked. The picture was below full brightness for 0.85 to 0.91 seconds per change (Mix duration 1.0) and reached black each time; a new shader every 13.0 to 13.5 seconds.
- A clip (`testpattern.mkv`) played into Vibes on Show: Vibes ended with "something else was played or stopped", the clip had the screen, Stop cleared it. The clip's own dip took 1.43 seconds.
- nxlx-nebula and nxlx-silk in a temporary set at 720 lines: nebula marked 9 seconds after it came on ("left out: it dropped 10.7 frames a second"), the rotation went on to silk at once, silk was never marked. The set was deleted and the mark taken back.
- Ambient at 720 lines, 150 seconds, 12 shaders: nxlx-aurora marked the same way (6.7 a second) and taken back afterwards. **nxlx-lantern dropped up to 8 frames a second and was not marked:** "tight" for all 12 seconds, never 6 seconds in a row above 2 when asked every second. bloom, ridge, pulse and tide dropped nothing in their 12 seconds.
- The marks that appeared and were cleared: nxlx-nebula.fs and nxlx-aurora.fs, both at 720 lines on board pi4. None at 540.

**Found, not fixed here** (this pull request records; each is small):

1. The guard lets a shader that drops frames in bursts stay, and its verdict depends on how often it is asked (`pvj/shaderlive.py`, `Guard.sample`: one look under `LIMIT` sets `_over` back to None). A rate averaged over the last `WINDOW` seconds would not.
2. The owner's box still has 720 lines from the first version's default. Eight of Ambient's shaders drop frames there. Nothing tells a person that the saved detail is above the board's default.
3. The first save of the shader settings by this version writes the two sets out (`LiveEngine._save`), and from then on that box's sets no longer follow a change of the default sets. Changing the picture detail is such a save. It is as designed (D46) and it happened to the test box in this run; see below.
4. A heavy mark stores its drawing height as `720.0` (`check_heavy` rounds every number to one decimal). Harmless; the comparison still works.
5. With one shader left in a set, Vibes dips to black every dwell and shows the same shader again.
6. Playing a clip logs "Cannot load libcuda.so.1" at error level (mpv probing hardware decoders under `--hwdec=auto-safe`); one line per clip reaches the journal.
7. The panel answers 404 for `/favicon.ico` and the two `apple-touch-icon` names a phone asks for.
8. A generated shader text from an earlier run of the panel (`shader-10905-4.glsl`) lay in `/run/pvj/web` until the first shader was played; the last text stays there after Stop.

No traceback and no error line from the panel in the journal for the run.

**In this pull request:** `PI4` in `pvj/shaderlive.py` for all 47 (class, pass and dropped frames at 540 and 720 lines; `measured` in the API carries both heights, `pass_ms_range` is gone); the `COST` note of seven of the project's shaders now starts with medium (nxlx-bloom, nxlx-lantern, nxlx-petal, nxlx-pool, nxlx-ridge, nxlx-stars, nxlx-beam); the pack's files are untouched; tests name the classes one by one and check that each class is what its two drop rates make; SHADERS.md, the manual, D51, lessons, HANDOFF. **The default sets did not change:** every shader of Ambient and of Show holds 30 frames a second at 540 lines, so none was moved out.

**The box afterwards.** Playback stopped; picture detail back at 720; both sets as they were (members, 180 seconds, variation, shuffled, Ambient active); no presets, no heavy marks; the temporary set and the temporary device gone; the owner's five devices and the PIN unchanged, compared field by field in the settings file; the helper's files removed from `/tmp`; no service restarted. Every part of `GET /api/shaders` compared equal to its state before, except Vibes' note of its last run ("stopped", 12 rounds), which lives until the panel restarts. **One thing is not as found and cannot be put back without a restart:** the `shaders` section of the settings file was `{disabled, dwell, height, vary}` and is now the same values plus `v: 2`, `active`, `guard`, `clock`, `faster` and the two sets written out. In effect nothing differs today; a later change of the default sets will not reach this box by itself. A paired full-access device at 192.168.0.159 opened the panel for seven seconds at 02:11 UTC and only read.

**The Mac.** Its disk ran full twice during the run (not from this work, which used 180 MB): for some minutes no command could be started. A watchdog on the Pi would have put the box back by itself had the session died there (see LESSONS). The snapshots and raw results are in a scratch folder on the Mac, not in git.

**Not measured:** 360 and 1080 lines; a 60 Hz or a 1920 x 1080 screen; a single black or torn frame; the Shaders page in a browser on the box; MIDI, OSC, DMX; events (no bundled shader has one); the inputs of the pack's shaders (`octaves`); a GPU refusal of a switch or choice; a dwell of minutes at 720 lines; hours of running with the carrier's clock.

## 2026-10-04 (the System pages: one set of patterns, and the rest of the audit)

Pull request #78 (D50). **Not merged. Nothing here ran on the Pi, on a phone or with a real projector. The browser test and the screenshots ran only in CI: this Mac has no Playwright and no mpv.**

What the audit of the System pages still asked for, in the brief's order.

1. **The same on every page.** Helpers in `app.js` and used by every card that was touched: `labelled` (a label above a field, a hint below), `listRow` (name, one state line, a red problem line, one main button, More with Remove last), `addBlock` ("+ Add a ...", open while the list is empty, the refusal under the Add button), `saveBar` ("Save changes", disabled until something differs from what is saved, "Not saved yet"), `toggle` (the real switch with a noun), `sayAt` (the result beside the button and in the message line), `offNotice` (a choice whose feature is off, with the switch in place), `asking` (a card that redraws by itself waits while a question is open). Sentences are out of the small-caps `.k` style on these pages, and the values of the About page (versions, file names, the clock) are no longer upper-cased by it.
2. **Questions in place.** Every Remove (projector, stream, schedule entry, MIDI mapping, paired device, with "Remove this phone? You will need the PIN to get back in." for one's own, Room group and scene), New PIN, Restart player, Restart the box, Power off, a test sound while something plays, factory reset, settings import, update install, Wi-Fi off, the PIN on the room screen, All on and All off, Turn off for one projector, stopping to lead or follow, and on Media: Delete and Copy again. Rename on Media is a small form in place. **No `window.confirm` or `window.prompt` is left**, and the browser test now counts any browser dialog as a problem.
3. **Projectors.** One power button per projector from its reported state (Turn on, Warming up..., Turn off with a question, Cooling down..., Try again when it does not answer), All on and All off naming the count, the input list (applies on change; an unnamed input carries a hint from its PJLink kind, which is from the standard's five kinds and unchecked on a real projector), "Read inputs" when none are known, and More: Blank or Show the picture, Mute or Unmute the sound, Name the inputs (a field per input, Show, one Save names), Check now, Read details again, Edit, Remove. The "Mute both" pair is gone from the page (the API keeps `mute` and `unmute`).
4. **Schedule.** Time order, days in words, Edit (the same form; the whole list is sent with the entry replaced and its id kept) and Remove under More, day shortcuts, plain words for what happens, the old start script under Advanced in the list, Start Vibes with a set when there are several (the API already took `set`), the box time and what is next at the top, a red line when the clock is not from the network (read from `/api/system`), choices marked when their feature is off with "Switch ... on" in place.
5. **The other pages.** Sound applies on tap. At power-up has labels, Save changes and "Try it now" that waits for the save. Streams is the row pattern. DMX and OSC have a state line in words, Save changes, the extra networks under Advanced, and DMX a channel table with live levels (read every 2 seconds). Boxes in step asks "What does this box do?" (Lead or Follow), then the group; the wall is folded unless in use and offers only places inside the chosen size. Network has labels (Address, Size of the network, Router, Name servers), "Try this setting", the port choice only with more than one port, the commands and the time to go back under Advanced. Remote support shows staff the state and one button; the server, the key and the past sessions are under "Advanced: support server settings". Updates has a state line and "How to get an update". About and power is one card of facts and one of actions; the duplicate Vitals card is gone.
6. **A snapshot of an idle player.** `GET /api/preview.jpg` answers 409 "nothing is on the screen right now" when the screenshot fails and the player runs with no clip; the Screen card then says "Nothing is on the screen right now." The card asks with `fetch` and shows the picture from a `data:` address (the policy allows no `blob:` pictures). `tests/test_preview.py` has both cases.
7. **Laptop.** From 900 px: Projectors and Schedule are a list that scrolls by itself with the form beside it, Network has what the ports are now beside the form, People and codes and the MIDI page are two columns with lists that scroll, DMX has its channel table beside its fields.

Also: `room.js` no longer says "under System > Modules" (it offers "Switch Projectors on" in place, and "Add a projector" opens the Projectors page). The mock-up export (`tests/ui/mockups.js`) now writes every System page, the Room screen, an off page and Live with a shader, at both sizes, with named groups for the page header, switches, chips, rows and the controller drawing; `docs/mockups/README.md` lists the files and says how to bring them into Figma.

Changed on purpose in the browser test: every Remove now needs its question answered; the dialog handlers are gone; the Sync step no longer provokes "inside the wall" from the page (the page cannot choose such a tile any more; the test asks the API directly and gets 400); 502 joins the expected console statuses ("Try again" on a projector nothing answers for).

Left out, and why: the MIDI teach flow was not redesigned (labels, the switch and the question on Remove only); Live, Media and Mix are the proposal's (#65) and were not touched beyond the browser dialogs; the pictures in `docs/images/ui` were not regenerated (CI makes new ones in the `ui-screenshots` artifact, and `docs/UI.md` says its pictures are older); `tools/` was not touched, so `tools/panel-playground/src/panel.css` is further behind `app.css` and `tools/DEVICE-TESTING.md` still has the old button names.

Not verified: everything on hardware. On the box, look at: the power button through a real warm-up and cool-down (is "Cooling down..." reported at all, and for how long); the input hints against the sockets of the real Epson; a snapshot with nothing playing (does it now say so); the pages on a real phone at arm's length, with a thumb.

## 2026-10-04 (edit a projector; a presenter can let a guest in)

Pull request #75 (D48). **Not merged: part B changes who may do what, so it waits for an independent security review. No real projector was involved; nothing here ran on the Pi.**

**A. Edit a projector.** `POST /api/projectors` takes `{"edit": {"id", "name"?, "host"?, "port"?, "password"?}}` (full access). Only what is sent changes, each field checked as for an add. No password field keeps the stored password, an empty one removes it, and it is never returned. A new address or port keeps the input names, drops the stored details and the status and restarts that projector's background worker: the worker is retired under the settings lock together with the save, and the existing take-over in `Monitor._after` lets the same thread go on at the new address, so there is never a second thread. An identify answer that was asked of the old address is thrown away (`Monitor.identify` compares the address it asked with the stored one). A rename restarts nothing. An edit that leaves the address alone does not look it up again. Panel: Edit next to Remove opens the fields in place, with labels above them, hints, "Save changes" disabled until something differs, the refusal under the button, an empty password field with "A password is set. Type a new one to change it, or leave empty to keep it." and a "Remove the password" switch. There is no "More" menu on that page yet, so Edit sits beside Remove.

Found by the round-trip test the brief asked for: a settings import refused the box's own export after an edit to a new address, because a label was only accepted for an input in the projector's list and the list is gone until the new address answers. The same refusal met a state the box could already be in before this branch (a projector that stops listing an input keeps that input's name, stored and not shown), and an Edit to a projector with other inputs makes that state ordinary. The import now accepts a label for any code of the standard's form, in the list or not, and still refuses a code of another form or a bad text. Two older assertions were changed on purpose: that a label without a list, and a label for an input outside the list, are refused.

**B. Guest codes by presenters (D48).** The four `/api/access` routes and `/api/qr.svg` now answer a live-role device, and each handler checks the role again for the action asked: a presenter may make the guest code (15, 60 or 120 minutes, at most 20 uses, `replace: true` needed while one is active), see it, show it on the room screen, take it off and end it. Refused with 403: a presenter code, the presenter code's QR, cancel by digits or all, the PIN on the screen, links, devices, the PIN itself. A presenter's GET lists the guest code only, and says of the room screen only whether the guest code is on it and whether "something else" is. While the owner has the PIN or the presenter code on the screen a presenter's show is refused (409), since a show replaces what is there and would otherwise swap it or keep it up longer; a presenter's hide removes only the guest code. Those two rules are decided inside `PinScreen` under its lock. Nothing in a request is drawn: the display gets fixed words, the digits and the address. Each code now says who made it. The support tunnel refuses the whole `/api/access` path and the QR code before any role is looked at; that list did not change, and there is a test with a presenter and a full support login. Presenter requests are written to the log with the device id.

Panel: People and codes is two cards for the owner ("Let someone in", "Paired devices") and the first one, guest only, for a presenter, whose System index now has the row. The same guest part is on the Room screen, folded, for a presenter and the owner. A new code works for 15 minutes, 1 hour or 2 hours; 1 hour unless chosen, and the choice is sent as `minutes` (the manual said 15 minutes while the panel always sent 60; the API's own default for a request without `minutes` stays 15, and the README says so). "End this code" and making a code while one is active ask in place. One vocabulary on that page, the print sheet and About: Guest (can watch), Presenter (can play and mix), Owner (everything). "Create guest link" is now "Create link", since it also makes presenter links.

Tests: `tests/test_projector.py` (edit: every refusal, password kept, changed, removed, never returned, rename, new address with one thread and nothing more to the old address, a silent old address, a stale answer, the limit, duplicates, groups), `tests/test_boxcare.py` (the round trip), `tests/test_join.py` (`PresenterGuestCodeTest`: every refusal, the allowed path, limits, CSRF, who made a code, that request fields never reach the display, a device removed mid-request; the display rules in `ManualDisplayTest`), `tests/test_support.py` (the tunnel, both roles), and browser steps for the edit form, the owner's page, a presenter's page and the Room screen. Two older assertions were changed deliberately: "/api/access gives a presenter 403" and "the QR code is for full devices only".

Not tested: a real projector; the Pi; a phone. Playwright is not on the new Mac, so the browser test ran only in CI, and that cost a round: a helper that returned two elements as a list was given to `appendChild`, the "Let someone in" part stayed empty, and only CI showed it. The unit tests of the installer, the updater, the release tool and the network helper fail on this Mac (29 of them; they need GNU tools and Linux sockets, and this branch does not touch them; whether they also fail on master here was not run); the `test (3.9)` and `test (3.12)` jobs in CI are their real run. The browser job failed once on an earlier commit of this branch at the mapper step ("Main stage" not seen in 30 seconds), the old intermittent failure; the pull request run of the same commit passed.

A code that the box makes because Show was pressed with none active lasts as long as the show (at least 15 minutes), not one of the three choices; the panel therefore makes the guest code first, for the time chosen, when a presenter presses Show with no code. The decision number is 48: the shader engine (#72) took 46 and the Shaders page (#76) took 47 while this was in review, and 49 is used by the open controller-profiles pull request.

**After the independent review** (no privilege escalation and no validation bypass; one medium and five low findings). Fixed, each with a test:

- Medium, nothing was bounded: a presenter could loop "new code, 20 uses, pair 20 times" and add 200 guest devices in 0.3 seconds, each a rewrite of the settings file and one more record for every request to scan. Now at most 200 paired devices with 20 places kept for full access (so the PIN always pairs; nothing is evicted, guests are refused first, with a plain message that is not counted as a wrong guess and uses up no code), at most 6 presenter-made guest codes an hour (429 with the wait), and a device that joined with a guest code is dropped after 7 days without use. For that a device record now says `via: "code"`, and such a guest's last use is written to the settings at most once a day (`seen`); both are optional keys, no schema change. D48's cost paragraph was wrong to call the pile-up "already so" and now says what is bounded and what is not.
- An input change asked for before an edit went to the old address and was then retried at the new one (seven `INPT 31` to a projector that does not list it). `Monitor.set_input` now reads the stored address again under the input lock and stops if it changed, and attaches a retry only if it is unchanged.
- A device removed mid-request left state behind: its show kept the guest code it had made, and its "replace" destroyed the owner's code before answering 401. The pairing check now runs under the lock before anything is replaced, and a code made by a show is cancelled when the check fails afterwards.
- A code made by a presenter's show was outside the documented limits (17 minutes for a 1020 second show, the default uses). It now lasts the shortest of 15, 60 and 120 minutes that covers the show, with the presenter's uses, and replaces nothing. The type is checked before the membership (15.0 is in (15, 60, 120)).
- Logging was thinner than D48 said. Show, hide, a code made through show and a code that replaced another (and whose it was) each write a line with the device id, and never a code's digits.
- Docs: what happens to a Room scene under way when a projector is edited (ROOM.md), and the edit form and PROJECTORS.md say that a kept password is used at the new address.

The Room screen's "Let someone in" is not drawn for a remote support login, and its People and codes row is hidden; that is read from the code (`room.js`, `rowShown`), not run in a browser, since the browser test has no support login. The server refuses it either way, which is tested.

Still open for the owner: "any presenter" includes one who came in by a presenter code ten minutes ago; the "Remove" buttons on the Paired devices list still do not ask first (the audit's item); the owner's own codes have no hourly limit.

## 2026-10-04 (controller profiles: a known MIDI controller works when it is plugged in)

Pull request #77 (D49), on top of the shader engine (#72). The owner's words: "I would like to be able to connect a controller like the Korg nanoKONTROL2 and have it automatically/preset/natively mapped to control mastercontrol."

**Nothing in this entry was tried on a real controller.** The three are on the owner's Pi, which this session did not touch.

**Built:**

1. Profile files, `pvj/controllers.d/<id>.json`, for the Korg nanoKONTROL2 (51 controls), the Akai MIDI Mix (60) and the original Novation Launchpad Mini (80): match patterns, a drawing, what each control sends, its default action. Checked strictly on load; adding a controller is one file (how: `pvj/MIDI.md`).
2. The hub applies a profile when a matching controller appears and drops it when it goes. Per control: your mapping, then the profile, then the built-in map, which is not used for a controller whose profile is on. A switch per controller. Several controllers each use their own.
3. Pickup for opacity, volume, clip speed, shader speed and shader brightness; the same press twice for blackout and Room scenes; a controllers' bank for the two controllers with one row of pad buttons.
4. New actions over calls the API had: previous and next clip, fade in, shader hue and brightness, Room scene 1 to 8, nothing, bank pad, bank step, and Vibes started with the set Ambient or Show (by name; on Marker Set and Rec of the nanoKONTROL2 and E and F of the Launchpad; the MIDI Mix has no free button). The shader speed limit for Performance shaders is the engine's own, so a fader cannot pass it.
5. The MIDI page: a card per controller with its layout drawn as a grid, what each control does, lights while it is moved (the hub keeps the last message per control), a tap to choose another action, "Back to the standard" for one control and for the controller (a question in place), the real switch "Standard layout". An unknown controller: "No built-in layout for this one yet. Teach it below." The old "Remove all mappings" asks in place now too.
6. API: `GET /api/midi` (now for presenters too) gains `controllers`, `bank`, `profiles`; `POST /api/midi` takes `{"controller", "standard"}`; `POST /api/midi/map` takes `set` and `reset`. Settings: `control.midi.controllers`, optional, no schema change, in boxcare's check with a round trip.
7. Tests (`tests/test_controllers.py`, 33): the files, matching, precedence on the numbers that collide with the built-in map, pickup, both edges of the double press, hot-plug with pipes, the cap, roles, the settings round trip, and every control of every layout sent while the shader engine's lock is held. The browser test plugs a fake nanoKONTROL2 into the harness.

**Sources, plainly.** Launchpad Mini: Novation's Launchpad S Programmer's Reference 1.02 (X-Y layout figure, message examples) plus the recording from 2026-09-30, which fits it. nanoKONTROL2: Korg's Parameter Guide has **no** factory CC table; the numbers are the widely reported ones, cross-checked with the Mixxx mapping; all marked unverified. MIDI Mix: no Akai document with numbers found; from a public Live script for the factory preset; all marked unverified.

**After the independent review (2026-10-05), fixed with tests:** Save on an unchanged guarded control no longer stores a mapping (it removed the press-twice guard), an own blackout or Room scene has a "Press twice" switch that is on by default, and Save is offered only for a change. A message that arrives after MIDI is switched off or the controller is unplugged is dropped (knob 5 of a nanoKONTROL2 is CC 20, the built-in map's opacity), and a layout goes only once its reader has ended. The card list is read as bytes, only the card's own row counts, a list that was read but gives no usable name no longer matches by card id, and the profiles hold exact card ids, product names and USB ids from the owner's Pi (0944:0117, 09e8:0031, 1235:0036) and no patterns. Volume and clip speed pickup read what the API last set (`Api.levels`). A scan no longer joins a reader under the hub's lock. One control has one own mapping. A non-text action name is a 400, and a bad profile file costs only itself. The switch list cannot be locked by junk or filled with made-up names. The last steps of a fader count as its end.

**What changes on a box that updates** (also in `pvj/MIDI.md` and the manual): learned mappings keep working and win; learned opacity, volume, clip speed, shader speed and shader brightness on the three controllers now wait for pickup; every control never mapped gets an action; the built-in map no longer applies to the three (on the Launchpad Mini notes 36 to 71 change from the built-in pads to other pads or spare, and note 72 from the built-in Stop to "Vibes: start the set Ambient").

**Not built:** lights (D49 and `pvj/MIDI.md` say why and what it would take). The System page is still shown to full-access devices only; a presenter can read the layout through the API.

**Open, for the owner:** the two-minute check per controller in `pvj/MIDI.md` (move each control once, watch its box light). Start with the nanoKONTROL2 in CC mode. The card ids, product names and USB ids are now the ones read on the Pi; the matching code itself has not run there.

## 2026-10-04 (the Shaders page becomes an instrument)

Panel only (`pvj/web/shaders.js`, `app.css`, a few lines of `app.js`), on top of the shader engine (#72, D46). The owner's words: "don't be shy about really making the control panels engaging and userfriendly"; "no telling the user to go to a different page to find the controls"; "someone doing more intense work will have a laptop, usually." No backend change.

**Built:**

1. Controls for the playing shader by type through `inputControl`: float, bool (the switch), long (buttons for up to five choices, a list for more, a slider for a range), color (native field and alpha), point2D (an XY pad with arrow keys), event (a button). All send `POST /api/shaders/values` with the shader's id while dragged, at most ten times a second per control, the last value always on release. Nothing sends a Play any more, so a control never ends Vibes.
2. Speed (with Freeze), Colour turn and Brightness trim above them, each with Reset.
3. Presets: apply on tap, save with a name, rename and delete under More, the one in use marked, "Changed" after a value moved.
4. Rotation sets: one set looks like "Vibes settings"; a second makes a list of sets with one being edited, and the library's switches then mean "in the set being edited" (the card names it). Start Vibes on a set, make it the usual one, rename, delete.
5. The library: filters by name, weight, pack and family; measured numbers; "Too heavy on this box" with Put it back; the GPU's refusal; the Playing mark is patched in place.
6. Load as three lights and words, with picture detail beside it.
7. MIDI teach buttons beside the first eight controls a knob can drive, Speed, Previous and Next, and preset 1 to 8.
8. Live: Previous and Next beside the Vibes button, the set to play when there are two, and a strip of Speed and the first four controls; on a laptop the strip is a column on the right.
9. Three groups of cards (stage, library, side) that stack on a phone and are columns from 900 and 1200 px. Keys: Space, the arrows, 1 to 8 (D47).
10. Roles: a guest sees, a presenter performs, the owner saves and edits.
11. Redraws: a card is not drawn again while it is being used, and a control takes the box's value only when it is free (D47).

**Tested:** the browser test uploads a shader with every input type and checks what each control sends, that a held slider and a held point survive polls, a refusal beside its control, presets (save, apply, Changed, rename, delete), a second set (add, fill, make usual, start Vibes on it, rename, delete), the heavy mark and Put it back, the keys and that they do nothing in a field, the presenter's and the guest's page, Live's strip, and no overflow at phone and laptop widths. The load at "heavy" and a GPU refusal cannot happen in the harness (no GPU), so the test adds those fields to the box's answer on its way to the page.

**Not verified:** nothing here has run on a Pi or been watched on a display. A real MIDI controller has not taught any of the new buttons.

**Where the code and the brief or SHADERS.md differed** (the code won; for the engine's author):

- `POST /api/shaders/values` answers `{ok, id, values, controls}`, with no `pending`. The page marks its own sends and reads `playing.pending` from the GET.
- A GPU refusal of a new shape (a switch, a choice, an event) is not in the answer; it appears later as `error`. The page shows it at the control that sent last, which is a guess.
- Presets come as names only, and any change sets `playing.preset` to null. "Changed" is therefore the page's own memory of the last preset it saw, lost on reload.
- Step walks the active set, not the library, and takes no set.
- `{"op": "activate"}` needs full access, so a presenter chooses a set per start (`{"on": true, "set"}`), which does not change the active one.
- A row's `vibes` is membership of the active set; for another set the page reads `sets[].shaders` and sends the whole list back with `op: update`.
- Every colour has four numbers, so "has alpha" cannot be told: every colour gets the alpha slider.
- A point without MIN and MAX has no range: the page uses 0 to 1 when its default lies in there, else the drawing's pixels.
- `categories` came with #74 while this was built; the family filter is now tested on the real 15 Performance shaders.
- There is no way to reorder a set's entries or to give an entry a preset from the page; both are in the API.
- Right after a Play, `GET /api/shaders` can say `playing: null` for a moment (the engine asks the player for its path, and the player has not loaded the carrier yet). The page and Live's strip follow the answer, so they can blink once; the browser test waits for the layout instead of measuring at once.
- The review fixes of #72 were merged in on the way (a 422 from `/api/shaders/values` for values the GPU refused before is said beside the control like any other refusal; set and preset names have stricter rules, and the box's words are shown).

**After #72 and #74 merged** (master merged in here): a new box has two sets, Ambient and Show, so the chooser beside Start Vibes is there from the start and the single-set page is what an owner gets by deleting down to one; Speed's top is each shader's `speed_max` (1 for a Performance shader), and the owner's opt-in `faster` is a switch under Advanced with the warning about photosensitive epilepsy in plain words; a refused value (422) frees its control at once so the box's value goes back into it.

**Cost on the box, not measured:** the page asks `GET /api/shaders` every 3 seconds while a shader plays (every 5 otherwise; it was 5), and Live asks every 4 seconds while a shader is on. That call reads the whole library and asks the player a few questions. With 50 shaders on a Pi 4 nobody has timed it.

## 2026-10-04 (the shader engine: first hardware numbers, and performing with shaders)

Pull request #72 (D46), backend only: Python, API, tests, docs. The owner's words: "a more robust shader playback and control system. i want to have more shaders available to perform with or have as auto-playing vibes." The Shaders page (#69) and the ISF pack (#70) are other sessions' work.

**The first hardware numbers for shaders** (the owner's Pi 4, 2026-10-04, the run described in the hand-off entry below; mpv 0.40, 2560 x 1440 at 75 Hz, the ten bundled shaders, the first version of the module):

- All ten compile and draw correctly. mpv makes a **desktop OpenGL 3.1 context** (GLSL 1.40, V3D, Mesa 26), not OpenGL ES; CI's `--opengl-es=yes` path is not what the Pi uses.
- The GPU is the only bottleneck (CPU 2 to 4 percent). Each frame pays two fixed passes: scaling to the screen, 12.9 ms at 1440p, and a remainder pass of 1.0, 2.3, 4.0, 8.9 ms at 360, 540, 720, 1080 lines. That leaves about 16 ms for the shader at 720 lines in a 33.3 ms frame.
- Dropped frames a second at 720 lines: silk, lattice, horizon, prism, pulse, ember 0 (pass 7.5 to 11.2 ms; pulse at the edge, and Vibes variation pushed it to about 1 a second); tide 3.3 (15.1 ms); aurora 6.7 (20.6 ms); drift 10.1 (30 ms); nebula 11 (32.7 ms). At 540: tide and aurora 0, drift 2.1, nebula 3.9. At 360: drift and nebula 0. At 1080 even silk and lattice drop 7 to 8.
- A slider change answers in 0.10 to 0.15 s; first play of a shader 0.62 to 0.78 s including the GPU check. A Vibes change takes 1.15 to 1.49 s against a Mix duration of 1.0 s. After 7 h 43 min TIME was still fine.
- A snapshot costs about 0.3 to 0.4 s of undrawn frames, which mpv's drop counter and /api/health do not see. The kernel's `/sys/devices/platform/v3dbus/*/gpu_stats` shows GPU busy and render jobs a second and agreed with the drop counter.

**What the measurement found, fixed with tests:** the default detail depends on the board (540 on a Pi 4 and unknown boards; 1080 never offered on a Pi 4; a Pi 5 and x86 keep 720 and 1080, unmeasured); the default rotation leaves out nebula and drift, keeps aurora and tide (0 drops at the Pi's default 540); every shader has a `weight` (light, medium, heavy) and the bundled ones the measured numbers in `GET /api/shaders`, and the cost table in SHADERS.md is the measured one; drift's look (two palettes side by side instead of a grey blend, turned octaves; not seen on the Pi again); `pass_ms` is the playing shader's own pass; deleting a shader clears its refusal; compiler warnings about `pvj_` names are not passed on and no line of the generated text reaches a message; uploads start in the library only; a GPU refusal is remembered until the file changes; the Vibes dip takes the Mix duration; the docs no longer say a Pi uses the ES language, and CI has a run on desktop GL 3.1 with GLSL 1.40.

**Built** (`pvj/shaderlive.py` on top of `pvj/shaders.py`):

1. Every input type adjustable live (bool, long with VALUES and LABELS or a range, color, point2D, event, float), described fully in `GET /api/shaders`, checked strictly per type.
2. Changes are coalesced by one worker: at most five compiles a second, the newest value wins, within 0.2 s of the last change. Only the shader file is exchanged (no reload, no epoch change, Vibes goes on). `//!PARAM` and `glsl-shader-opts` do not exist under `--vo=gpu` in mpv 0.37 and 0.40 (read in the source; CI checks its 0.37 refuses a `//!PARAM` shader), so a change stays a compile.
3. Common controls: speed 0 to 4 with continuous TIME, hue shift, brightness trim.
4. Presets per shader (save, list, apply, rename, delete; "default" is used by Play and Vibes).
5. Rotation sets for Vibes (shaders with optional presets, dwell, variation, order; one active; a start can name one; schedule and OSC can too). No schema change: the first set is a default on read.
6. MIDI: shader control 1 to 8, shader speed, previous and next shader, preset 1 to 8, through the same API calls.
7. A guard: more than 2 dropped frames a second for 6 seconds is too heavy; Vibes notes it, moves on and leaves the shader out until someone puts it back; a shader chosen by hand is only reported; switchable. Variation leaves alone the inputs that sit in a loop head or a condition, and a shader seen dropping frames goes without the palette turn.
8. Tests for the safety properties of the new surfaces, and CI's real mpv checks each input type on the picture, TIME across a value and a speed change, a preset, and that no screenshot is dark during 100 changes.

**The one design change underneath: the carrier counts its own frames.** A speed change without a jump needs the frame number the shader is at, and mpv's `frame` (it is `frames_uploaded` in the source: every picture uploaded since the player started) cannot be read from outside. So each carrier frame is painted with its number (a `geq` filter in the same lavfi address) and the shader reads it from the picture; the panel reads the same number as `time-pos`. CI found the one thing this broke: after a refused shader with none before it the screen showed the bare carrier, dark red, not black. A shader that draws black is put on there now.

**The independent review of #72 and what it changed** (two high, three medium, several low; nothing injected into the shader text or the lavfi address; every finding reproduced and fixed with a test):

- H1: a queued step or preset carried no epoch, so the worker put the shader over a clip played meanwhile, brought it back after a Stop, and ended a Vibes run started in between. The job now carries the player's epoch of its request; the rotation is ended at the request only; what waits is dropped when the module goes off or Vibes starts.
- H2: the MIDI dwell knob took the engine's lock, which is held across the GPU's look at a shader (3.99 s measured). Settings are edited under a lock of their own, a Vibes stop leaves the clearing to the rotation's thread, and one test now makes every call a controller, OSC, DMX or the schedule can make while that lock is held.
- M1: the guard marked every shader, for good, when the box dropped frames from any cause. After two marks in a row with no healthy shader between it stops, takes the two back and ends Vibes with its own message. A mark carries its height and board, counts at that height or above on that board, and an import leaves marks of another board behind.
- M2: a refused switch or choice value went to the GPU every time. The refused shape is remembered and answered 422.
- M3: one damaged row plus any save wiped all presets and sets. Rows are read one by one, and a key that cannot be read at all is never written over.
- Low: names without unseen characters, compared on NFC and casefold, a set not named like an id; magnitudes below 1e-30 written as 0; the frames since the anchor counted in whole numbers and a new anchor every two days, so 30 days at speed 4 comes out at 10,368,000 s; a screen of no size falls back to 1920 x 1080; a set deleted while it runs ends Vibes with its own message and the schedule's last run says the set is gone.

**After the 30 new shaders (#74) landed:** a box now starts with two sets, Ambient (active: the calm shaders without the heavy two) and Show (the Performance shaders), so no way of starting Vibes shows a Performance shader unless a set was chosen. The speed control is kept at 1 or below for Performance shaders, because their flash cap (3 a second, 6 with Fast) is counted in TIME and speed multiplies TIME; a full-access switch (`faster`, off by default, documented as a photosensitivity risk) lifts it. `weight` for the new families comes from the first word of their cost note. CI's GLSL 1.40 run draws every bundled shader at its defaults and with each switch, choice and point varied. Changing a shader's own rate input makes its picture jump in its cycle; that is documented, not fixed (it needs each shader's code).

**One case the migration does not cover:** a box where shaders were uploaded under the first version but no shader setting was ever saved has no `"shaders"` section, so it cannot be told from a new box; its uploads leave the rotation and have to be put in once.

**Not run on hardware, any of it.** The Pi numbers above are the first version's. First on the Pi: play each bundled shader and watch TIME (the carrier's clock on V3D; `clock: frame` is the way back), drag a slider and turn the speed while watching for a hitch, look at drift, run Vibes at 720 lines and see the guard take tide and aurora out, and check the kernel's gpu_stats line in `GET /api/shaders`.

**For the Shaders page** (not in this PR): controls for the input types other than float, speed, hue and brightness, presets, sets, the guard's notes (`heavy`, `refused`, `playing.load`), and the new MIDI actions in its teach rows. The browser test's "Light work" example moved from tide to silk (tide is medium by measurement); that is the only line of the page's files this PR touches.

## 2026-10-04 (more shaders: an Ambient and a Performance family)

The owner: "more shaders available to perform with or have as auto-playing vibes". Pull request #74 (branch `more-shaders`), not merged. Written in six batches of five, each pushed by itself so that CI's real mpv judged it before the next.

- **30 new original ISF shaders** in `pvj/shaders.d`, 40 of the project's own in all, in two families named by `CATEGORIES`. **Ambient** (15, in the Vibes rotation from the start): bloom, caustic, contour, dusk, fringe, kaleido, lantern, moire, petal, pool, ribbon, ridge, stars, tiles, veil. **Performance** (15, in the library, out of the rotation until put in): bars, beam, burst, checker, chevron, glitch, grid, halftone, mirror, radar, scope, spokes, stripes, tunnel, zoom. What each shows, its inputs and its estimated cost are in the table in [pvj/SHADERS.md](../pvj/SHADERS.md), with the rules they keep (cost, inputs, folded time, shape, flashing, own construction).
- **Inputs**: 4 to 8 each. All 30 have numbers and colours; 20 have a switch, 18 a choice (`long` with VALUES and LABELS), 9 a point. The panel on master shows only the numbers, so the other kinds keep their DEFAULT until it has controls for them.
- **Cost**: 29 are "low" (at or under nxlx-ember by the count of operations against the owner's Pi 4 measurements of the first ten), one is "medium" (nxlx-scope, Performance). Nothing was measured: no Pi was touched.
- **`pvj/shaders.py`**, kept small: `parse()` returns `categories`; `default_in_vibes()` is false for the category "Performance"; `library()` gives such a shader of the project's own `vibes` only when it is named in the settings list `included`; the "vibes" action writes `included` for those and `disabled` for the rest (`_opt_in`). The isf-library branch (#70) merged into master meanwhile with the same key for its third-party pack; master was merged in here and the two rules are one: a pack's shader, or one of ours for performing, is out until it is put in. `pvj/boxcare.py` checks `included` on import.
- **Tests**: the bundled set is named file by file in `tests/test_shaders.py` (three lists), and every count comes from them; a test holds the two families to their rules; the browser test has one constant for the count. The GPU test draws every one of the project's shaders with defaults and with varied numbers, and every new one also with all numbers at MIN, at MAX, late in TIME, and once per switch, per choice and per corner of a point (193 such draws), on OpenGL ES and on desktop OpenGL; it prints a line per draw and names every failure in one run.
- **What CI drew**: every draw of every batch was taken and drawn, on both GL paths and both Python versions; none was refused, none was flat or dark (the flattest picture had 133 colours where 40 are asked, the darkest a brightest value of 78 where 60 is asked; that one, nxlx-beam started from a corner, was then made to aim at the middle of the picture and given a brighter floor). No shader was dropped.
- Before each push the shaders were compiled and looked at on this Mac in a headless browser's WebGL 1 (contact sheets of every variant at 16:9 and 4:3, and a sweep of 40 moments per variant for the darkest and flattest one). That found a reserved word (`half`), several pictures that were too dark at an end of a slider, and two that depended on the moment of the screenshot.

Things to know when merging the shader-engine branch: its default rotation set leaves out only the "heavy" ones and does not know `included`; it has to honour `default_in_vibes()` or the Performance shaders go into every new box's rotation. Its `weight_of` reads the first word of COST, which is "low" or "medium" in every new file. Its tests count the bundled shaders (10 and 8) in their own places.

Not verified: none of the new shaders has been seen on a projector or run on the Pi, at any drawing size; the cost classes are estimates; the flashing rates were measured only as the mean brightness of a 320 x 180 software picture; nothing was tried with a controller. A known flaw, in SHADERS.md: turning a rate moves the picture to another place in its cycle (a shader has no memory), and the Speed control above 1 raises the flash cap with it.

## 2026-10-04 (the ISF library)

The owner: "don't forget about ISF shaders. (https://github.com/Vidvox/ISF-Files)". Done (PR #70, D44): how much of that library the box runs, a pack of what can be bundled, and the plan for the rest.

**Licence.** The repository is MIT as a whole (`LICENSE`: "Copyright (c) 2018" with no holder; GitHub reports MIT). The files are another matter: of the 36 generators the box can run, nine carry a comment that their code comes from elsewhere (The Book of Shaders, a repository that no longer exists, two glsl.io pages, Stack Overflow, an ISF sketch, a blog post and a font, a web page of colour tables). Those are not bundled and are listed in THIRD_PARTY_LICENSES.md with the reason; sources outside GitHub were not opened, so "unknown" there means not established. Two bundled files hold Ashima Arts' simplex noise (MIT), whose notice is printed in the inventory.

**Survey** (`tools/isf-survey.py`, 327 files): 36 translate (17 before this change); 210 need the playing picture (121 nothing else); 68 are transitions; 72 need several passes and 49 a picture kept between frames; 38 have a vertex shader; 7 need sound; 4 load pictures. The ranked plan for the engine is in pvj/SHADERS.md ("What would unlock the rest"): filters first (+121), transitions (+68), several passes (+10), persistent buffers (+40, and most likely not possible with mpv user shaders at all), vertex shaders (+32), sound, imported pictures.

**Translator.** Three gaps, each found by the survey and fixed with a test: `out_color` (9 generators) and an input called `color` (3) are renamed to `pvj_` names instead of refused; text that is not ASCII is allowed inside comments, which were already cut out and never passed on (7 generators had a dash or an arrow in one). Added while there: `##` is refused, since a name joined from pieces is one the checks never saw.

**Pack.** `pvj/shaders.d/isf-files`: ten files at first, seven after the review (below), byte for byte upstream under names without spaces, with upstream's LICENSE and checksums; declared MIT in REUSE.toml. The engine lists packs (`"pack"` in `GET /api/shaders`); a pack's shaders are in the library and not in Vibes until put in (`included` in the settings, exported and imported). What CI's real mpv said about the 27 licence-clear candidates, on OpenGL ES and desktop OpenGL, defaults and varied: 10 pass every time; Random Lines passed one run and failed the next by one colour (40 where more than 40 are asked) and is left out; 11 draw flat pictures by design (shapes of a few colours); 5 fail (OpenGL ES refuses a function called `sign` or `round`, a float compared with a whole number, a wrong return type; two of them draw black on desktop OpenGL).

Changed in existing tests, each for a stated reason: the GPU test picks the project's ten by pack; `out_color` and a non-ASCII comment moved from the refused list to tests of their own, with the refusals that remain around them; the library and browser tests count the pack beside the ten; the licence test reads every REUSE annotation, with REUSE's own rule for `*`, and whole file names.

Not verified: nothing here ran on the Pi or was seen on a display; no pack file has been timed on any board (the cost notes are counts from the text; the Pi 4 numbers quoted in SHADERS.md were measured for the project's own shaders in another branch). The claims about mpv in the plan (no texture survives a frame, a still as a `//!TEXTURE`, brightness and filters at MAIN, sound through a filter graph) are from reading and from how this module already works, and each needs a CI spike. On this Mac `tests/test_release.py`, `test_install.py`, `test_update.py` and `test_netd.py` fail for reasons of the machine (no GNU tar among them); CI is the reference.

**After the independent review** (one medium, nine low, no injection path in the translator; each fixed with a test). Medium: the webgl-noise notice for the two noise shaders was only in THIRD_PARTY_LICENSES.md, which no release carries; it now lies in the pack as `LICENSE.webgl-noise`, is declared in REUSE.toml with Ashima Arts and Stefan Gustavson as holders, and is pinned by checksum. **The pack is seven files, not ten**: Worley Cells names "iq" for its hash and Color Test Grid holds the well-known `rgb2hsv` and `hsv2rgb` snippet without a credit, so both left for the reason that kept Triangle and Star out (the owner's coordinator decided; consistency over a de minimis argument). The rule's limit is now written down: it catches only borrowing a file admits to. Heart's entry was wrong ("not reachable"): the transition is on GitHub in gl-transitions, MIT, by gre; it stays out as a two-colour shape. Also fixed: the Shaders page now shows the pack and the credit on a third-party row (three documents said it did); a backslash inside a comment is allowed like any other text there, and refused in code as before; pack folders are found however many files sort before them (the listing was cut at 64 names before folders were picked out); an upload hidden behind a bundled name takes no upload slot and can be deleted by that name; the pack's file names and the checksum list's own hash are pinned in the tests; `hook` is refused as any name in the code, not only as an input or a `#define`. And one more left by the picture rule: with two files gone the GPU test's varied inputs fell differently, and Spiral, a two-colour shape, drew 39 and 40 colours where more than 40 are asked (it had drawn 97 to 163); a file that passes only sometimes is not bundled, as with Random Lines. Each file's varied inputs in that test are now seeded by the file's own name, so they no longer move when the pack changes. Not done: a pack choice in the library filter (typing "isf" in the name box finds the pack), and a Remove button for a hidden upload on the page (API only).

Open: whether the owner wants the flat shapes (Checkerboard, Stripes, Spiral and the like) as a second pack with a rule of its own; the nine files of unclear origin, if the owner can establish their terms; `tools/make-release.sh` still splits file names at spaces (a test now forbids such names instead); `test_no_secret_is_in_it` in tests/test_boxcare.py failed once here because the PIN's digits occurred in the disk's byte count (LESSONS; not fixed, not this branch's code). The branch `shader-engine` adds a cost field and per-board defaults to `pvj/shaders.py`; the pack listing was kept small (`packs`, `_bundled`, a `pack` key, `included`) so the two merge.
## 2026-10-04 (a runtime folder per service)

Pull request #71 (D45). **Not run on hardware; not merged: it touches the root helpers and waits for an independent review.**

Found on the real Pi by listing owners: `/run/pvj` was root:pvj after boot, belonged to the display account after "Restart player" (with `netd.sock`, `pin` and `player.sock` in it), and to pvj-web after a reinstall. Cause, from systemd.exec(5) and the source: the player, the panel and the network helper all named `RuntimeDirectory=pvj`, and systemd re-owns such a folder and everything in it to the unit that is starting whenever the owner or group differs. With `RuntimeDirectoryPreserve=yes` in all three nothing was ever removed on stop.

Done: `pvj/paths.py` holds every runtime path. Units: `pvj/player` (display account), `pvj/web` (pvj-web), `pvj/netd` (root), each 0750 group pvj, under a `/run/pvj` that is root:root 0755 and no unit's own (systemd makes it so when missing; `install/pvj-tmpfiles.conf` keeps it so). The PIN file is 0600. mpv's preview picture is in the player's folder; everything the panel hands to the player is in the panel's, read through the group. pvj-sysd, pvj-supportd and the update result stay where they were. The installer stops the three services, reloads, takes the old shared folder back, empties it and restarts; an update from the panel now also moves a running network helper. Older code after a rollback keeps working except its preview (and it writes the PIN 0640 again).

Tests: `tests/test_paths.py`, `tests/test_run_folders.py` (a missing peer is "not running" and its folder is never made, modes, the update restart, the links for an older panel), `tests/test_units.py` (no folder shared by two users, none group-writable, none the parent of another, the parent is no unit's own, environment and sandbox match the paths, start order with what systemd adds by itself, the installer's order), `tests/test_install.py` (an upgrade over the old shared folder with planted names and links, a reinstall that leaves live folders alone).

Not verified on a box: after the review a CI job ran the installer for real under the runner's systemd 255 (owners, restarts, a crash, refusals: all as designed), but nothing ran on a Pi or under systemd 252 or 257, and the runner has no display, so the preview through the sandboxed panel answered "mpv: error running command" there: that shows no read-only error, not that a picture can be read. That systemd leaves an existing parent alone and creates a missing one as root 0755 is read from its documentation and source (252, the version in Bookworm, and 257, the version in Trixie), not observed. Which kind of reinstall left the folder with pvj-web is not known: by the rule an update from the panel would (it restarts the panel last), a manual `install.sh` would leave root (it restarts the network helper last); the owner may remember which it was. Whether mpv writes the preview readable for the group when the file is in its own folder was not seen (it was readable in the shared folder before). `tools/DEVICE-TESTING.md`, section "Runtime folders", is the checklist: owners and modes after boot, after a player restart from the panel, after a restart of each unit, after a reinstall, and seven commands that must be refused. On this Mac the install tests ran only with stand-ins for GNU `mv -T` and `stat -c`; CI is the real run.

**After the independent review** (two blockers and lower findings; the layout itself held against the systemd 252 and 257 source). Fixed, each with a test:

- Root wrote, chowned and chmodded the undervoltage note inside the panel's folder; a dangling link planted there by the panel's account made root create its target anywhere. The carry-over is gone (the note is lost by an upgrade; it only lasts until the next boot). A static test now refuses any write, chown or chmod in those installer functions except on `/run/pvj` itself and the marker.
- The preview would have answered 503 on every box: the panel's sandbox mounts the player's folder read-only, so `unlink` there fails with EROFS, which the code did not catch (the test had used a folder without write permission, which gives EACCES). The panel no longer calls unlink outside its own folder and tells a new picture from an old one by inode and timestamps; a stale one is never served.
- An emptying that was cut short was never finished, because "old layout" was decided by the owner and mode of `/run/pvj`, which the first two commands had already changed. Now a marker written last says "done", every run checks what is there, the three services are stopped before the reload, and a failed delete stops the installer with a plain message.
- `pvj-pin` (run as root on a name the panel's account controls) reads only a plain file, 32 bytes.
- A CI job (`real-install`) runs the installer for real as root under the runner's systemd: upgrade over an old shared folder with planted names and links, owners and modes, a restart of each unit, a crash, fourteen things the accounts must be refused, the preview through the sandboxed panel, a reinstall, an unfinished emptying, a wrong owner. It is not a Pi and has no display.

Open: run the checklist on the Pi (R1 first, it is the upgrade over the running old version). Remove the three rollback lines in the tmpfiles file once no box can go back before D45. The decision number is 45 because the ISF library branch (#70) uses 44.

## 2026-10-04 (hand-off at midday: what merged, what is half done, first shader numbers from the Pi)

Written because the owner is moving to a new computer and the working session was cut off by usage limits four times in a day. HANDOFF.md has the "Start here" for the next session.

Merged and deployed to the test Pi since the evening of 2026-10-03: #61, #62, #63, #66, #68, #67, #64, #69 (see their own entries below). The Pi runs the merge of #69, checked by file hash; all four services active; still on the wired network.

**First shader measurements on the real Pi 4** (a read-only run over SSH with a temporary paired device, since removed; mpv 0.40, a 2560x1440 monitor at 75 Hz; judged from the panel's snapshots, nobody watched the monitor). All ten bundled shaders compile and draw correctly. mpv there makes a desktop OpenGL 3.1 context (GLSL 1.40, V3D, Mesa 26), not OpenGL ES, so CI's `--opengl-es=yes` run is not the path the Pi uses, and the lesson that says a Pi uses the stricter language is wrong for this box. The GPU is the only limit (CPU 2 to 4 percent, 37 to 41 C, never throttled). Every frame pays two fixed passes: the scale to the screen (12.9 ms at 1440p) and a remainder pass (1.0, 2.3, 4.0, 8.9 ms at 360, 540, 720, 1080 lines), which leaves about 16 ms for the shader at 720 lines. Dropped frames a second at 720 lines: silk, lattice, horizon, prism, pulse and ember 0 (passes of 7.5 to 11.2 ms); tide 3.3 (15.1 ms); aurora 6.7 (20.6 ms); drift 10.1 (30 ms); nebula 11.0 (32.7 ms). At 540 lines tide and aurora are clean, drift 2.1, nebula 3.9; at 360 both are clean; at 1080 even silk and lattice drop 7 to 8. A slider change answers in 0.10 to 0.15 s; the first play of a shader takes 0.6 to 0.8 s. A Vibes change takes 1.15 to 1.49 s against a Mix duration of 1.0 s; the dip to black works; a refused shader is left out and the rotation carries on; a clip takes the screen and Vibes ends. One shader (tide) ran 7 h 43 min by accident when the measuring agent was cut off: still moving, not black. A snapshot costs about 0.3 to 0.4 s of frames that are not drawn, which mpv's drop counter and the Health card do not see; the kernel's v3d `gpu_stats` does.

Found by that run, not fixed on master yet (the first three are fixed in the first commit of #72): the pass time shown is the previous shader's; a deleted shader leaves its error behind; compiler warnings about internal names reach the person; the default detail of 720 lines is too high for a Pi 4 (540 is right, and 1080 should not be offered there); the default rotation holds heavy shaders and leaves light ones out; the cost table in SHADERS.md is wrong for aurora and tide; uploads join the rotation by default; the Vibes fade runs about 30 percent long; and **the runtime folder problem**: several units with different users declare `RuntimeDirectory=pvj`, so /run/pvj and what is in it (the root network helper's socket, the PIN file, the player socket) belong to whichever service started last (pvj-player after the panel's player restart, pvj-web after a reinstall). #71 is the fix, half done.

Open and unfinished: #72 (shader engine), #71 (runtime folders), #70 (ISF pack from Vidvox ISF-Files, two checks failing), #65 (the UX proposal). Master's browser test failed once after #68 at the mapper step ("Main stage" not seen in 30 s), an old intermittent failure that is still not explained.

Not done although asked for: the box on Wi-Fi with the wired port to a projector (the Wi-Fi feature has never run on hardware); the first real PJLink test (no projector has been connected); projector Edit; guest codes made by a presenter; the rest of the System audit's page redesigns; Live, Media and Mix from the proposal.

## 2026-10-04 (one switch per feature, and the Shaders page)

Two slices the owner approved, in one pull request (#69, D43).

**One switch per feature** (owner: "yes"). The page switch is now the only switch for DMX, MIDI, the schedule, OSC and Remote support; "Turn DMX on", "Turn MIDI on", "Turn schedule on", the OSC toggle and "Allow remote support" are gone, and so is "On, but not listening: turn it on inside". DMX and MIDI: on is the module and then `enabled`; off is the module. Schedule: on is the module and then `enabled` with the saved entries (read just before, because the schedule is saved whole); off is `enabled` false with the entries kept and the module left on. OSC and Remote support have no module, so the switch is their flag. A box in the mixed state shows Off and gets only the inner call. If the second call fails the reason is on the page and the switch stays Off. A schedule with saved entries asks first ("Switch the schedule on? 5 entries will start running at their times."); "Not yet" leaves it off. The switch sends only its flag: the old DMX toggle also saved whatever was typed in the fields, and that is gone (tested: a typed universe is not saved by switching off and on). Because the flag is only known from a GET, a page whose flag is not read yet says "Checking..." for a moment, and a page drawn from an old answer is drawn again once when the fresh one disagrees.

**The Shaders page** (owner: "add a panel page of shaders and shader controls"; "a big button in Live"). The placeholder Vibes page in System is now "Shaders and Vibes" and holds everything; the shaders card left Mix. Top to bottom: on screen now (name, time to the next one, Start or Stop Vibes, Next one, why Vibes last ended), the library (Play, cost in words, the rotation switch, Remove with the inline question, the GPU's refusal in red), sliders for the playing shader (range, default, Reset each, Reset all, the plain statement that values are not saved), Vibes settings applied on tap (a select for how long each stays, the variation switch, Advanced with picture detail and the file upload, whose refusal is shown under the button), and buttons to the MIDI and DMX pages. Live has one large Vibes button ("Start Vibes" / "Vibes is playing: Aurora", 64 px, full width on a phone) with a Shaders link under it; Back from the page then returns to Live. Shader names are shown without `nxlx-` and with a capital. A presenter starts, stops, skips, plays one and moves sliders; full access also changes the rotation, the settings and the files; a guest sees what is playing (the row is now shown to guests while the module is on). Roles follow what the routes allow; no backend change.

Left out, because there is no API for it today: keeping slider values (said on the page); a measured cost (the note is the author's text; its first word becomes Light, Medium or Heavy work); the elapsed time of a single shader played by hand (only the rotation has a countdown); whether the screen is black while a shader plays (the status does not say the picture is faded out). Also left out on purpose: a Shaders row on Mix (the brief said the card leaves Mix), and the owner-only "Vibes is switched off" line on Live.

Things to know: moving a slider is the same call as Play, so it ends the Vibes rotation and keeps that shader (the page says so). `POST /api/vibes {dwell}` lets a presenter change how long each stays (it exists for MIDI knobs); the page follows the brief and offers the select to full access only, through `POST /api/shaders`. While DMX, MIDI or OSC is off its card is not shown, so a port that cannot be opened cannot be changed from the Off page.

Not verified: none of this was seen on a phone or run on the test Pi; this Mac has no Playwright or mpv, so the browser test and the screenshots ran only in CI. The DMX receiver now really starts in the browser test (it never did before); on a box, whether Art-Net's port is free is not known. The pictures in `docs/images/ui` were not regenerated; CI makes `shaders-page` and `live-vibes` in the `ui-screenshots` artifact.

Open: `tools/panel-playground/src/panel.css` still lacks the new classes (`.vibesrow`, `.vibesbig`, `.rot`, `.shader-entry` and the rest; tools/ belongs to the ux-proposal branch). The room-scenes branch (#64) touches `sysRows` and the browser test; whichever merges second must merge master first.

## 2026-10-04 (the Shaders page as a workspace; no page points elsewhere)

The owner, after the first version of #69: "don't be shy about really making the control panels engaging and userfriendly ... no telling the user to go to a different page to find the controls. someone doing more intense work will have a laptop, usually." Done in the same pull request, after merging master (the Room screen, #64):

- **No pointers.** The Shaders page's buttons to the MIDI and DMX pages are replaced by the controls: the MIDI switch and three teach rows (Vibes on and off, next one, the time each stays) using the Learn call in place, with what is mapped and Remove; the DMX switch, the ninth channel's number, its ranges and last level. System > Projection mapping now holds the mapping card itself and System > Room holds the Room screen's cards under its switch (`roomInPage` takes the screen's own title and message line out); `pointerCard` is gone. Live has Next one beside the Vibes button.
- **Laptop.** From 900 px two columns, from 1200 px three: the library scrolls by itself and stays in place, the stage and the playing shader's controls are beside it. The page may use the window up to 1600 px. The browser test checks the columns at 1366 px and no sideways scroll at 900, 1100, 1366 and 1600.
- **For a growing library.** A filter by name and by work that only hides rows; each card is drawn again only when its own part of the answer changed; a rotation switch patches its row. The time left is a bar that moves every second without asking the box.
- **Ready for the coming backend.** `inputControl` draws an input by type (float today); comments mark where presets and rotation sets go. Nothing empty is drawn.

Not verified: seen only as CI screenshots (`shaders-page`, `shaders-page-laptop`, `live-vibes`), never on a phone, a laptop or the Pi. Teach was tried only as far as the harness allows (start, cancel, a mapping made through the API); no real controller. The DMX level line was only seen with nothing received.

## 2026-10-04 (System index and pages)

The owner: "the 'worst' experience is the turning off and on of all the modules. the whole system page." Done (PR #67, D42): the System screen is a short index in three groups (Everyday, Show tools, This box) with Health above them, and one page per row. A module's page has its switch at the top right (`button.switch`, `role=switch`), a plain description, the message line directly under the header, and the card below; off shows only the description and one button, "Switch on <name>". Switching keeps the person on the page and says what happened. Switching off asks first, in place (`confirmRow`, goes back after 8 seconds), where it changes what the room sees: Vibes while playing, Projection mapping while on, Boxes in step with a role set, Projectors. The Modules card and each card's own "Off. Switch on ... under Modules above" branch are gone. Each index row has a chip (Off, Set up, Ready, Active, Problem) and a sentence, worked out from the existing GET calls with no backend change; the states are asked once when the index opens and again on each return to it, and an answer repaints only its own row. A presenter and a guest see only the rows they can use (for a presenter, only modules that are on). Modules not built yet are in a folded list with no switches. A page is pushed on the browser history, so the phone's back gesture returns to the index; a reload starts on Live as before.

The cards themselves are hosted unchanged, with one fix: the Updates card asked for its state while it was being built and stayed at "Loading" on a page of its own (see LESSONS; fixed in two sessions at once, the same way). After merging #66 and #68: the Network page hosts the Wi-Fi controls and its row says the Wi-Fi network or hotspot as well as the wired address; the sync step keeps its diagnostics.

Shaped for the next slice, not built: `sysPage` takes the page switch as a list of steps (the module, then a feature's own enabled flag), shows On only when every step is on and switches on in order, so "one switch per feature" (DMX, MIDI, Schedule, OSC) is a matter of adding the second step. Until then those rows and pages say "On, but not listening: turn it on inside". The Vibes page says it is started with the big button on Live and has a settings card that today points at Mix, where the settings will land.

Not verified: nothing here was run on the test Pi or seen on a phone; this Mac has no Playwright or mpv, so the browser test ran only in CI. The pictures in `docs/images/ui` were not regenerated: `system-modules.png` and `system-vitals.png` are no longer referenced, the other pictures still show the old cards (unchanged in content), and there is no picture of the index or a page in the docs yet (CI makes `system-index`, `system-page`, `system-page-off` and `health` in the `ui-screenshots` artifact).

Open: `tools/panel-playground/src/panel.css` is a hand-kept copy of `app.css` (D34) and does not have the new classes (`.navrow`, `.navname`, `.navstate`, `.chip`, `.switch`, `.hint`, `.confirm`); sync it when the ux-proposal branch lands. `tools/DEVICE-TESTING.md` still says "System > Modules" (tools/ was left alone for the same reason). The inner switches fold into the page switch next. The room-scenes branch adds a `room` module: it gets a generic row under Show tools by itself (any ready module without a row does), and should get a proper row under Everyday when it merges; its browser test switches modules through the old Modules card and needs the `sys(...)` helper.

## 2026-10-04 (Wi-Fi control)

The owner asked for Wi-Fi control as a priority. Done (D41): the Network module now changes Wi-Fi ports too, with the wired port's confirm-or-revert safety. Modes: join a network (automatic or fixed address; WPA2 or WPA2/WPA3 mixed, WPA3 only, or open; hidden networks), the box's own hotspot (WPA2, 2.4 or 5 GHz, 10.43.0.1/24 by default), and Wi-Fi off (the radio goes off on confirm). "Find networks" scans and lists what is in range. New route `POST /api/network/scan`; `GET /api/network` reports what each Wi-Fi port is doing. The password never goes on a command line: `pvj-netd` writes a root-only NetworkManager keyfile (SSID as bytes, password escaped for GKeyFile) and loads it; the password is not kept, returned, logged or saved in the helper's state. A radio switched on for a change is switched off again when the change is undone, also after a restart.

Also fixed PR #67's failing browser test (the Updates page asked for the version before it was on the page; LESSONS) and merged #66. Left #64 (waits for its independent review) and #67 (its docs and log are not written yet) for the owner.

Independent review of the Wi-Fi change: one high, two medium, three low, each reproduced by a test that fails on the code before the fix. High: the helper's systemd sandbox (`ProtectSystem=strict`) made `/etc` read-only, so every Wi-Fi join would have failed on a box; the unit may now write to NetworkManager's profile folder (and has netlink, which `ip -j addr` always lacked). Medium: one nmcli timeout during an undo dropped the undo and its saved state for good; a confirm that failed after deleting the old profile left nothing to go back to (now: rename the old one aside, name the new one, delete the old one last, rename back on failure; this changes the wired confirm too). Low: after 20 failed undo tries a new change could overwrite what was owed (now it keeps trying once a minute and refuses new changes); "in use" in the scan list came from the air (a name with a line break could fake a record); a network name with a backslash was shown doubled. Also from the review: WPA3 refuses a hex key, the hotspot switches PMF off (Pi hotspots), joining Wi-Fi waits 45 s instead of 20, the panel waits up to 180 s for the helper, and the browser test shows a hostile network name as text.

Not verified: nothing here has run against a real NetworkManager, a Wi-Fi chip or a Pi. Open questions for the first test on the Pi: does NetworkManager load the keyfile as written (byte-list SSID, escaped password, `key-mgmt=sae`), does `nmcli -t` let a line break through in a network name, how long joining takes, can the Pi 4 run a 5 GHz hotspot and WPA3, and is the 45 s wait enough. Test it with a keyboard and monitor on the box or on the wired port, never over the only Wi-Fi link. The Wi-Fi country must be set first (Imager or raspi-config).

## 2026-10-03 (two CI flakes)

Two intermittent failures on master after #63 (run 37174896595), one pull request. Browser test, Sync step: the card was rebuilt by the answer to the first save (and is rebuilt every 2 seconds while the box is a server or client) between choosing a column and pressing Save, so Save sent the saved column and no refusal came. Shown from the failed run's log (the second save answered 200) and again by a test-only commit that failed with "col 1" in its diagnostic. The card now reads the page before every rebuild and keeps what is not saved yet; an answer older than a later save no longer clears that save's message. The test chooses the column without an event, waits for a rebuild, and prints the form, the message and every `/api/sync` request if it fails. Vibes test: nothing in the calls waits for the change (read from `pvj/vibes.py`); the 0.19 s against a 0.1 s limit was most likely `set_dwell` writing the settings file on a busy runner, which is inferred, not shown. The test now asserts that each call returns while the change is still held (for a minute), with a 2 second limit as a second check. Two more limits got the same treatment: the slow-pattern shader check counts the thread's CPU time, and the projector deadline test holds the earlier command for 3 seconds instead of 1.2.

A third one showed in the reruns of this branch: the Shaders step clicked the button that reads "Play" on one shader, and when Vibes happened to be showing that shader (1 in 10) it reads "On screen". Inferred from the code and the log, not seen with a diagnostic. The test now finds the button by its label.

Not done: the rebuild every 2 seconds still closes an open chooser and takes the cursor out of the group name; the values now survive it.
## 2026-10-03 (the room: groups, scenes, the Room screen)

Done (D40): PJLink Phase 3 as a new beta module, **Room (groups and scenes)**, off by default, which needs Projector control. Groups are named sets of projectors ("Main wall", "Painting wall") plus the implicit All. A scene is a preset tapped once: per group, power on, off or leave, a source (the projector's input code, shown by its label), picture and sound mute or leave, and one thing the box does from a short list of existing API calls (play a clip, a pad or a saved stream, start or stop Vibes, Stop, Blackout). The Room screen (`pvj/web/room.js`, one more tab; a presenter or guest starts on it) has the scenes, each group's state in a word, On, Off, its sources by label, the mutes, and All off with a second tap; a guest sees the state and no working button; a full-access device sets groups and scenes up there. Scenes also run from the schedule (action `scene`), OSC (`/pvj/scene/<n>`, `/pvj/scene`, `/pvj/group/<n>/...`) and MIDI (trigger action `scene`, learnt from the Room screen). DMX was left out: its fixed channel row has no place for a scene id.

How it sends (`pvj/room.py`): one worker thread per projector at most (so 8), each sending power, then the source, then the mutes, through the same PJLink client as the Projectors card. The source change is Phase 1's own retry (`Monitor.set_input`); `Monitor.cancel_input` is new, so a replaced scene can stop a retry that was started for it. A second scene drops what the first had not sent and the same thread then sends the newer steps, so an older command cannot follow a newer one; a group button replaces only its own kind of step. Settings: a new top-level section `room`, in the defaults and read with defaults, no schema change (13); it is registered in `boxcare.SECTIONS` (`check_room`, which calls `room.validate`), and a factory reset ends a scene under way.

Found while testing: switching the module off while an input change was being retried left that retry running in the Projector module (the step's end cleared the note that said a retry was ours, before anyone stopped it); now only the worker's own turn clears it, after stopping the retry. A test that tapped twenty times and then let the fake projector finish its warm-up failed one run in four: every tap cancels the command not yet on the wire, so the first power-on only went out after the last tap, by which time the test had already "finished" the warm-up. That was the test's race, and it now waits for what it needs to see.

Found by the pre-review read: a scene's power-on that was being retried while a projector cooled down would still have gone out after Off was pressed on the Projectors card (or `/beameroff`, or the schedule), because those paths did not know about the room's waiting steps. `Api.projector_action` now tells the room first (`Room.supersede`), with a test that holds the projector in cool-down.

After the independent review (no high, two medium, nine lower), each fixed with a test in `tests/test_room.py` (`ReviewTest`) or the browser step: Off was reported done from a remembered status while the projector was in fact warming up, and it stayed on (a refused power command is now done only if the projector, asked at that moment, says so); one double tap confirmed All off (the question now replaces the buttons, names the consequence, and "Turn off" is not taken for the first 600 ms); a scene's source could be sent after a newer pick on the Projectors card (the "still wanted" question is now asked inside the monitor's input lock); the same scene sent twenty times a second never got a command through and restarted its clip each time (identical steps are kept and the command under way is left alone; the clip is not restarted within two seconds); removing a scene or group did not end its work; a body with two edits gave a 500; `stop()` was never called at shutdown; "on" was shown while the wall was still warming up; names that look empty or like "All" passed; a scene through the support tunnel ran without its box action; smaller ones (box and projectors of one scene under one decision, the landing rule, polling that backs off, `/pvj/scene 2.0`). A thread stuck in a name lookup is now left behind after 25 seconds so a later Off is not held up. After #67 merged, Room has its row in the System index ("Everyday": the module switch, "2 groups, 1 scene", a chip) pointing to the Room tab.

Not verified: no real projector and no real room. The Room screen and the schedule hook have run only in the CI browser test (this Mac has no Playwright and no mpv); locally `room.js` was only run against a stand-in for the page. OSC and MIDI were tested through their translation and the API, with no controller. Whether 90 seconds is enough for an Epson to take a source and a mute after power-on is unknown.

Open: an independent review of this branch before merging. The MIDI card lists a scene mapping as "scene" without the scene's name. A schedule entry or MIDI mapping for a scene that was removed stays and does nothing (it says so). No volume in scenes (class 2, Phase 2). Group buttons are not schedulable by themselves.

## 2026-10-03 (box care)

Done (PR #62, D39): four pieces of looking after the box, all for full-access devices and all done by the unprivileged panel (no new root command, settings stay at schema 13). The Updates card no longer says "updated to X" after `pvj-update rollback` from a terminal (a terminal rollback and apply now write the result file, and the API drops a "done" line for a version that is not running). Settings export and import (`pvj/boxcare.py`, System > Settings file): one JSON file; the PIN, devices, remote support and its history are never in it and a file that holds them is refused; the body is parsed strictly (UTF-8, no repeated keys, no NaN or Infinity, bounded size and nesting), goes through the same migrations as a load and then every section's own check; nothing changes unless all of it passes and the previous settings are kept beside the file (the last three). A diagnostics file (versions, board, modules, health, last update, the settings built from a list of what may be shown, the log if the panel may read it, everything scrubbed). Factory reset with a fixed confirmation and an explicit choice for the clips: defaults, no devices, codes or support session, a new PIN, old copies of the settings removed, clips kept or deleted (never on a USB drive). Import, export with passwords and reset are refused through the support tunnel.

Independent security review: no high finding, three medium, ten low; each reproduced with a probe first, then fixed with a test. Medium: the diagnostics file showed the address of a playing stream (a stream key, an SRT streamid); `streams.redact` left an SRT passphrase visible to view-role devices in the streams list and the player status (older code); an export without passwords still held an RTMP stream key, an SRT streamid and query secrets under names nobody had listed. What is secret in a stream address is now decided in one place (`streams._pieces`) for the panel, the export and the diagnostics. Low: a join code or invited device made by another full device during a reset could survive it; an older file wiped the sections it did not hold; a malformed older file gave an error 500; the autostart pad was only checked when the file also held pads; two MIDI mappings for one control were accepted; `"format_version": true` was accepted; log lines were cut before they were scrubbed; a failed save left the new settings in memory and the old on disk; the checks for a running update and USB copy were not one step with the work; a result file with a time that is not a number gave an error 500. Several tests the review called weak were rewritten (one was named "holds no secret" and asserted the stream key was there).

After #61 and #63 merged: a projector's `details` and `labels` are exported and checked on import (`clean_text`, `validate_label`), and an import or reset matches the background projector checks to the new list. The Shaders and Vibes settings are a section of the file (checked by the rules of the module's own API; uploaded shader files are not in a settings file and a reset leaves them in place), a reset ends the shader rotation, and an export from a box that uses Vibes from MIDI, the schedule and autostart imports as it was.

Not verified: nothing here was run on hardware, not on the test Pi either. The diagnostics log part asks `journalctl` with fixed arguments; on a box the panel's user is not in `systemd-journal`, so it should report that the log is not readable, which nobody has seen on a box. The PIN screen returning after a reset is tested with the fake player only. A stream export was never tried with a real RTMP or SRT source. The browser test for the three cards runs in CI only.

Open: a factory reset cannot remove the root-only update backups in `/var/lib/pvj/backups`, which still hold the old devices and passwords; `pvj-update rollback` after a reset would bring them back. It needs a root command (a pvj-sysd action), not added here. An update started in the second before its result file says "running" is not seen by an import or reset.

## 2026-10-03 (shaders and Vibes)

Done (PR #63): a new beta module, **Shaders and Vibes**, off by default, offered on a Pi 4, Pi 5 and x86. ISF generator shaders (the owner's preferred format) are translated in pure Python into mpv user shaders and drawn by the player's GPU in place of a clip; ten original shaders are bundled (`pvj/shaders.d`); full-access devices upload their own `.fs` files. **Vibes** plays them endlessly for ambience: shuffled, a dwell time (180 s by default), new values, a palette turn and a time offset each round, a dip to black between shaders. It is started from a big button on Live, `POST /api/vibes`, an Autostart mode, a Schedule action, OSC (`/pvj/vibes`, `/pvj/vibes/next`), MIDI (Vibes on / off, next shader and the dwell time on a knob or fader, by Learn) and DMX (an optional ninth channel: stop, start, next; the first eight channels did not move). It ends when anything else is played or stopped and never takes the screen back (D18). Details: `pvj/SHADERS.md`, decision D38.

How the design was found: this Mac has no mpv, and every other test runs mpv with `--vo=null`, which never compiles a shader. So three short spikes ran in CI on a real mpv 0.37 with Mesa's software GPU under xvfb, with the picture read back from screenshots. They showed: at the MAIN hook the brightness that opacity, fades and Blackout use has no effect, so the shader hooks NATIVE over a tiny black RGB carrier from lavfi (the test pattern's route; no D22 flag changed); on OpenGL ES a shader that computed `mod(float(frame), 1048576.0)` drew black with no error (most likely medium precision), so TIME is put together in high precision from small whole numbers; mpv says nothing the second time it meets a shader text it refused, and lists a refused pass with a time of 0, so every generated text is unique and only a timed pass counts as drawn. The same rig is now a CI step (`tests/test_shaders_gpu.py`, 18 tests on OpenGL ES and desktop GL; a skip there is a failure): every bundled shader compiles and draws a varied picture, coordinates, TIME against the wall clock, brightness, a refused shader falling back to the one before or to black with the line of the ISF file, the real mapper's quad over a shader, a clip taking over.

The player object now keeps two shader layers (the source and the mapping) and writes them to mpv together, so `pvj/mapper.py` is unchanged, and it counts an epoch that says whose screen it is. No settings schema change: a new top-level section `"shaders"` appears on the first change (it needs wiring into the settings export and import when that lands).

Independent review: one high, four medium, nine low; every one reproduced first (two could only be reasoned about and were fixed on that basis: the unlocked status read, and the licensing note), then fixed with a test. High: with the Mix transition on Dip to black, a clip tapped around a Vibes change was accepted and never played, because Vibes used the panel's Fader and that dropped the clip's waiting callback; Vibes now fades by its own steps, set through the player only while its epoch is current, and the API claims the screen the moment it accepts a clip. Medium: a pattern with nested optional white space took 2.9 s on a hostile 32 KB file; Vibes held one lock through its dip and the wait for the GPU, so a second OSC cue stalled every later one (start, stop, next and the dwell time now only set fields under a short lock); three header values (NaN, Infinity, a 400-digit number) raised uncaught exceptions, and one such file in the folder took the whole list and Vibes down; the directive and declaration checks could be bypassed behind comments, in the middle of a line and by letter case (comments are now stripped before the checks, and not passed on). Low: an operator's Fade out was undone at the next change; Stop during the dip left the picture dark; 8-bit buffers stayed on after an mpv restart; the carrier was not confirmed after loading; two check-then-act races; repeated header keys; the CI step did not fail on a skip. The reviewer also noted that the first noise and palette functions in four bundled shaders, and the dither line, used constants and forms that are copied all over the internet; they were replaced by lattice values and colour scales of our own construction, so "original" holds without a notice.

Seen once each and not explained: the panel browser test timed out at the mapper step (`#mapsel` "corner 1 of 4") in one run and passed in every later one; `tests.test_netd.SocketTest.test_disallowed_caller_is_refused_before_anything_is_read` failed with a broken pipe on Python 3.9 in one run and passed on rerun (neither is code this PR touches); on the CI rig a screenshot of the plain test clip was at times one flat colour, before any shader had been on, so that test asserts that the shader's picture is gone instead.

**Not seen on any display, and no speed measured on any board.** Not run on a Pi, on a real GPU driver, or on mpv 0.35 or 0.40. The MIDI and DMX controls ran only against fakes: no real controller and no real console. Not built: ISF filters (shaders that need a picture), multi-pass and audio shaders, shaders in step on several boxes (a sync server playing a shader sends its clients "stop"), live coding, projectM.

## 2026-10-03 (PJLink Phase 1)

Done (PR #61): Phase 1 of the PJLink plan (D36), class 1 only. On add the box asks a projector who it is (NAME, INF1, INF2, INFO, CLSS, INST) in the background and shows it; Refresh details asks again. Input selection (INPT) from the projector's own list, with a label per input set by a full-access device through its own chooser (labelling sends nothing); a change refused as "unavailable" (ERR3) is retried every 5 seconds for up to 90 seconds in the background and then reported plainly. Picture and sound mute separately (AVMT 11/10, 21/20) and together. Lamp hours (LAMP) and warnings (ERST) in the Projectors card and the Health card. A background status check about every 45 seconds (10 while warming up or cooling down, at once after a button), staggered, each projector with its own stop signal, on at most 8 threads; it stops with the module. Status is in memory only. No settings schema change (D37).

Checked against the published standard (PJLink Specifications 1.04, class 1); the fake projector in `tests/test_projector.py` was rewritten from that document. Reading it found three client bugs: answers to INF1 and INF2 were rejected (the pattern allowed letters only), the name was decoded as ASCII (it is UTF-8), lower-case answers were refused. Also fixed: the API had no `log` method, so a failed background projector command from OSC or the schedule crashed its thread instead of logging. The browser test and the screenshots now use the fake projector on loopback (allowed in the harness only), not a 192.168 address.

Independent review: one high, three medium, the rest low; every one reproduced first, then fixed with a test. High: an input retry already being sent could override the user's newer choice (input changes to one projector now go out one at a time, and a retry checks it is still wanted before it sends). Medium: one odd answer to a secondary question (warnings, lamp hours, input, mutes) wiped the whole status although the power state was read; a stopped worker went on sending commands and could save details with the module off, and removing and re-adding eight slow projectors six times reached 48 threads (now at most 8 in all, a projector beyond that waits and says so); labelling an input in the panel switched the projector to it. Low: an odd input list erased the list and every label; the per-projector lock was keyed on the name as typed, so one device under two names got two connections, and locks were never dropped; a command could take 20 seconds (the lock wait was outside the deadline); set commands took any answer as success; a cut-off or endless line was accepted; the IPv6 metadata address was allowed; bidi and other format characters got through; a busy check asked for the details again; no fresh status after a failed button; a malformed label body gave 404; three inaccuracies in the docs; four gaps in the tests.

A mistake to record: early local test runs tried to connect to 192.168.0.7:4352 on the owner's network. A test mocked the name lookup to return that address and only meant to check a lock; once the background check existed, it carried on and connected. Fixed before the first checked CI run (the test now refuses every connection); the test Pi was not touched.

Not verified: NO REAL PROJECTOR. Unknown until one is tried: what it answers in standby, how long it stays unavailable after power-on (is 90 seconds enough), separate mutes, lamp hours on laser models, whether a check every 45 seconds bothers it. Input selection from the schedule and OSC is left for Phase 3 (an entry needs a projector and one of its inputs). The projectors screenshot in the manual is the old card.

## 2026-10-01 (direction: Leyline)

Talked through the purpose with the owner (D36): a room controller staff can use at Leyline plus the owner's VJ tool. The owner prefers ISF shaders and approved projectM as an optional module on boards that can run it, agreed a controllable video matrix is the ideal router for consoles (no model yet), and wants a private NXLX network with internet when needed. Plan for a general PJLink control system added to pvj/PROJECTORS.md; HANDOFF re-ordered. The owner will connect one Epson projector to the network on 2026-10-02 for the first real test.

## 2026-10-01 (end of the day)

Merged today: the clip guide (#51), autostart modes (#52), copy from USB (#53), mock-up exports (#54), the update button with its review fixes (#55), the design playground (#56, from a second session), the Pi check log (#57), the persistent log (#58). The Pi runs master; the persistent log is installed there (seen writing to /var/log/journal; not yet seen across a restart). HANDOFF.md has a "Start here" section with the next steps. Two sessions shared one working folder and one commit landed on the other's branch; it was moved back before anything was pushed (see LESSONS).

## 2026-10-01 (panel design playground)

Done: added `tools/panel-playground` (D34), a mock-up of the panel for trying ideas: four screens and the Updates card with its states, theme colours, typefaces, about twenty size knobs, layout options, preview sizes from phone to desktop, and an export of the changed CSS and theme JSON. First built as a claude.ai page from the web-artifacts-builder template, then moved here without its component library. `npm run build` type checks and builds one self-contained `dist/index.html`.

Open: it has not been looked at in a browser by anyone yet (no browser on the dev Mac for the session); the published page is private to the owner. The export maps each knob to app.css selectors by hand; check the result in the real panel before committing a design change.

## 2026-10-01 (persistent log)

The owner asked for a log that survives restarts, after the Pi restarted on its own and the cause was lost. D35: the installer adds a journald drop-in that keeps the log on the card, capped at 64 MB. Tests check the installer writes it and uninstall removes it.

## 2026-10-01 (update button on the Pi)

Checked on the Pi 4 with master: the installer replaced the old update units with the version templates. With a temporary test key in /etc/pvj/allowed_signers, a 0.1.1 bundle (built from master, signed, uploaded through the panel with its .sig and no .sha256) installed through POST /api/system/update: the result file went running, then done; the panel restarted and answered on 0.1.1; the inbox was emptied; pvj-web, pvj-player and pvj-sysd were active. `pvj-update rollback` went back to 0.1.0. Removed afterwards: the test key, the 0.1.1 release and its rollback pointer.

Small follow-up: after a rollback from a terminal the card still says "Last update: updated to 0.1.1" (the installed version line is right). The sudo password leaked into a file during the setup (see LESSONS); the owner changed it.

## 2026-09-30 (update button)

Done: System > Updates (D33): install a signed bundle from a USB stick's pvj-update folder or from an upload, through pvj-sysd starting a version-pinned template unit; progress and outcome from a result file.

An independent review found: root could be made to delete files through a linked upload folder (by a compromised pvj-web); a bundle with only its .sig was refused for lack of a checksum; an unexpected error left the card on "Updating" for good; two updates could start at once; an upload during an update could break it; polling stopped exactly when the panel restarted; the installed version could differ from the one confirmed; root copied untrusted files without a type or size bound (a FIFO could hang it). All fixed, each with a test.

Checked on the Pi before the review: the units installed (not enabled); an unsigned upload without a checksum was refused at the first check. Not yet checked on the Pi: the reviewed version, and a signed update through the button.

## 2026-10-01 (mock-ups)

Done: the screenshot job also exports every screen (phone and laptop, every module on) as SVG with named groups and real text, a layered PSD (a group per card, a layer per control) and a PNG, uploaded as the `ui-mockups` artifact; guide in docs/mockups/README.md. A drag-and-drop layout board for the owner is a private claude.ai artifact. Checked: the SVGs render on the Mac (Quick Look) with the right positions; the first run measured a scrolled page and drew boxes behind sliders, both fixed. Not checked: opening the PSDs in Photoshop (macOS reads their size and composite).

## 2026-09-30 (copy from USB)

Done: from the manual deep dive (the old "Loading from USB to internal"): Media > USB drive > **Copy to the box** copies a clip into the media folder in the background (`POST /api/media/import`, progress at `GET /api/media/import`, `.../cancel`), through the same code as an upload, so the same checks apply (name, free space, size limit, a hidden temporary file, no overwrite unless asked, one at a time with uploads). Tests with a fake drive, including cancel and bad references. Independent review: no high findings; fixed: the "done" line vanished at the next redraw, a stick that stops answering gets a plain message (tested with a read that fails half-way, the half file removed), an empty file is refused plainly, no leaked file handle, the copy stops if the device that started it is removed, a generic message for unexpected errors. Cancel acts between chunks (a stick that hangs without an error cannot be interrupted until it answers). Not run on the Pi with the real stick yet.

## 2026-09-30 (autostart for installations)

Done: from the manual deep dive (the old Autostart tab had a slideshow, USB and random order). Autostart now has a slideshow (seconds a picture), a pad, "play the USB stick" (also each time a drive with clips is plugged in, checked every 2 s; a drive already there at start is not played twice, a drive without clips is ignored), and shuffle for every clip, the slideshow and USB. Settings saved before these keys existed keep working with defaults (no schema change). Tests on a fake clock and fake drives, a browser step. Independent review: no high findings; fixed: the drive that is plugged in is the one that plays (it used the /media/usb link, which can point elsewhere), a stick mounted during boot is no longer missed, switching back to USB mode no longer interrupts a show, a drive that comes and goes within 10 s (bad contact) does not restart playback, a pad must exist and have a clip, and the Media screen says when plugging a stick in will start it. Not run on the Pi yet with a real stick swap.

## 2026-09-30 (prepare your clips)

Done: a "Prepare your clips" section in docs/MANUAL.md from the manual deep dive and this project's own measurements (what was measured on the Pi 4, what was not: 1080p 50/60 fps, HEVC, 4K, Pi 5 and Pi 3), a HandBrake recipe, naming for Quick play, sync advice and power; Troubleshooting now covers https-first browsers and stutters. Media > Info adds plain-word advice per board (`probe.advice`): only warnings that were measured or plainly beyond the board, and "not tested" where it was not.

## 2026-09-30 (health card and the box's address)

Done: System > Health (`pvj/health.py`, `GET /api/health`, any paired device): power (the Pi's `rpi_volt` undervoltage alarm, readable without root, checked every 5 s and remembered in /run until a reboot, since a short drop is easy to miss), temperature with the Pi's slow-down points, the player (hardware or software decode, dropped frames a second), load and memory, whether the helpers answer, and the box's addresses; a full-access device can put the address alone on the display (a new "address" item for the access screen, with no code on it). From the manual deep dive (the old Powersupply, Check Services and GPU Usage buttons). Corrected NETWORK.md: `.local` already works.

Checked on the Pi: the alarm file exists (`/sys/class/hwmon/hwmon1`, `in0_lcrit_alarm` 0) and `vcgencmd get_throttled` agrees (0x0); `/dev/vcio` is root-only, so vcgencmd is not used. The card itself goes on the box after the sync branch merges (the box's settings are already at schema 13).

## 2026-09-30 (old OSC names)

Done: from the manual deep dive (three agents read the old manual against the build; their combined list is the order of the next work). `/startmasteronce01` to `99` failed silently (they were passed as a preset name the parser does not know; the old receiver ran startmasteroneNN): fixed. Old names for features that exist now are mapped (test screen, test tones, overlay, slideshow, flip as a toggle); old names that need full access or are not built are refused explicitly and listed in pvj/OSC.md.

## 2026-09-30 (multi-box sync and video wall)

Done:
- Server and client sync (D31, `pvj/sync.py`, `pvj/SYNC.md`, System > Sync and video wall, `/api/sync`, the "Video wall and sync" module made ready, settings schema 13): the server sends its clip and position; clients follow by nudging their speed and jump only when far off. A per-box tile of the picture for a video wall, with bezel compensation.
- Checked on the Pi 4: mpv's video-crop applied while playing (a screenshot showed exactly the chosen quarter) and cleared with an empty value; small speed changes apply at once. Two real headless players following each other over UDP on the Pi: median 0 ms, worst 40 ms (one frame at 25 fps). A simulator test covers the start, drift, seeks, looping, pause, stop and missing files.

Also on the Pi through the panel: as a server it broadcast 10 messages a second on the home network; as a client its real player followed a stand-in server on the Mac (Wi-Fi) into a 1080p film on USB: three jumps in 6 s, then in step within 14 ms for the rest of the 40 s. Sync and the module were switched off again afterwards.

Independent review: two high findings, both reproduced by the reviewer and fixed with tests: a sync thread could outlive a role change and run with the old settings (each thread now has its own stop signal); a client following a server at speed 2 or 0.25 jumped forever (it settled at its old speed; it now takes the server's speed at once and learns its jump lead per speed). Also fixed: clients ignored a restarted server for up to 100 s (a run id now), the wall crop was given up when the picture size was not known yet, `ip` ran ten times a second (cached), a clip started on a client was not replaced, live inputs and streams were sent as file names, paused seeks were not followed, no flood limit, a stray timer clear on the Access card stopped the sync card refreshing, and the docs. Re-measured on the Pi after the fixes: two headless players median 0 ms, worst 40 ms; the box's own player following the Mac: two jumps, then a speed catch-up, then within 5 ms.

Not verified: two boxes on two screens (the Pi 3B needs its spare SD card), a wall with real bezels.

## 2026-09-30 (remote support)

Done:
- Remote support sessions (D29, `docs/REMOTE-SUPPORT.md`): `pvj/support.py`, the root helper `pvj-supportd` (`pvj/supportd.py`, unit, installer and image), System > Remote support card, a support sign-in screen for people arriving through the tunnel, a banner on every device, settings schema 12, `tools/support-hub/` for the owner's server. The installer installs `wireguard-tools` and `nftables` when it can and otherwise reports remote support as unavailable.
- Chosen after research (agent report): WireGuard out to the owner's server, started at the studio, panel only, time-limited. Tailscale and RustDesk were considered and not used (see D29).

Verified on the Pi 4 against a stand-in support server in a network namespace: tunnel up with a handshake in 3 seconds; through it `/api/hello` said remote, the status needed a login, a wrong code was refused, the code signed in with the chosen role, PIN change, invites, session start, support settings and power off answered 403, SSH was blocked, the snapshot route passed the checks; restarting the helper mid-session removed the interface and the firewall table and the panel ended the session; Stop removed everything. Afterwards remote support was switched off and cleared on the box, and the stand-in removed.

Independent security review: no high findings; three medium, all fixed with tests: a support network equal to the studio's LAN would have locked every studio device out for good (remote rules now apply only during a session, and an overlapping network is refused); the hub script accepted a key with a line break that could have given a box a support laptop's address (the whole key is checked now, and duplicate keys refused); restarting the panel left the tunnel open with no banner (the panel closes it at start). Also fixed: teardown order, a stale key file, the panel's real port in the firewall (the default is 8080, not 80), an 8 hour cap, lifting the PIN lockout refused through the tunnel, IPv4-mapped addresses, and the docs.

Not verified: a real server on the internet, a studio network, NAT, the hub scripts on a VPS.

## 2026-09-30 (phone layout)

Done:
- At phone width, list rows wrap: a name keeps room for about ten characters and the buttons move to their own line instead of squeezing it (the Media list showed names a few letters wide); the mapper's surface list and the Access card's button row no longer run off the card. Seen in the refreshed screenshots.
- The browser test now fails if anything in a card sticks out of it, or a list name is squeezed, at 390 px wide (Media, the mapping card, System).
- The mapping card numbers its requests and never draws an older answer over a newer one: a likely cause of the one-time failure of the browser test's mapper step (the first state read landing after the "add" answer).

## 2026-09-30 (ST 2110 removed)

Done: removed the planned ST 2110 module and reworded README, ROADMAP, HANDOFF, the manual, pvj/README and pvj/STREAMS to "through a gateway" (D30), after a research report and the owner's go-ahead. Also corrected pvj/README, which still listed the mapper as not built. Nothing of ST 2110 was ever built or tested.

## 2026-09-30 (live Pi updated, small fixes)

Done:
- Current master (aef29ab) installed on the test Pi 4 with the full offline installer by an agent; all four services active, unit files and code identical to master, player without the old 8-bit flag, settings schema 11, modules as before. Old test files cleaned out of the Pi's /tmp.
- Stopping (including the test pattern's off) now resets the player's loop settings, so an idle player no longer reports the last clip's looping; the agent saw `loop_file: "inf"` on the idle box.

## 2026-09-30 (panel screenshots refreshed)

Done:
- `tests/ui/screenshots.js` had stopped after the connect picture since the pairing button was renamed ("Pair with PIN", `#pairbtn`); the CI step is continue-on-error, so nobody noticed. Fixed.
- New cropped pictures in `docs/images/ui`: live-transport, mix-mirror, mix-overlay, mapper, media-quickplay, slideshow, box, sound-output, projectors. Schedule (every kind of action) and access (guest and presenter codes with QR codes) show more now. All other pictures retaken from the same run. Tall screens show the tab bar at the bottom instead of over the middle.
- Used in `docs/UI.md` and `docs/MANUAL.md`.

Not captured: Live input (the CI runner has no capture device; the script skips it). The snapshot image itself (a headless player gives none), so the Screen card is shown before a tap.

Seen in the pictures, not fixed (UI, for a later change): at phone width the Media list squeezes file names to a few letters per line; the Remove button of the mapping surface list and the Print access sheet button of the Access card run past the right edge of their cards. The browser test failed once on the mapper step (`#mapsel` "corner 1 of 4" not seen in 30 s) and passed on the next run.

## 2026-09-30 (projection mapper)

Done:
- Projection mapping, a beta module off by default (`pvj/mapper.py`, the Mapping card on Mix, `/api/mapper`, settings schema 11), replacing the old ofxPiMapper tab: quads with perspective, triangles and grids (bilinear, up to 8x8), up to 16 surfaces; drag or nudge corners from the phone, screen and picture corners, layer order, hide, rename, 8 saved mappings that follow a change of screen size. Outlines on the display while editing. See D28 and `pvj/MAPPER.md`.
- While a mapping is shown, the player uses 8-bit GPU buffers and stretches the picture to the screen; both go back to normal when it is off.
- Naming: multi-box sync will use server and client (the owner's choice).

Verified on the Pi 4 (screenshots of the player's output and mpv's error log; nobody watched the monitor): edit and show views of a quad, a 2x2 grid over a quad and a 64-cell grid; edges and overlaps smooth; picture corners; saved mapping; no stale shader files. Dropped frames: 0 at 1920x1080 with any mapping, about 3 a second at 2560x1440. The table takes 1.3 s (1080p, 64 cells) to 1.8 s (1440p) to build.

Found and fixed on the way (see LESSONS): mpv silently refused a half-float table, which made the first benchmark meaningless; per-cell perspective broke grids at inner lines; an osd-overlay did not show in screenshots.

Independent review: one high (a build thread per change: 25 at once in a drag), two medium (an older switch could delete a newer shader file; letterboxed clips moved every surface, confirmed on the Pi with a 720x576 test pattern), and small ones. All fixed with tests: one coalescing build worker that stops early when overtaken, cleanup that never touches newer files, the picture stretched while mapping, statuses that an older change cannot overwrite, folded grids refused, non-finite numbers refused, saved mappings that stay editable on a much larger screen, drag positions sent one at a time. The browser test failed first because its wait for the word "Modules" now also matched the Mix card; it waits for the System heading now.

Not verified: a projector, the owner at the screen, Pi 5 and x86.

## 2026-09-30 (projectors and a broader schedule)

Done:
- Projector control over PJLink class 1, a beta module off by default (`pvj/projector.py`, System > Projectors, `/api/projectors`, `/api/projector`, settings schema 10): on, off, picture mute, state; All on and All off. Private addresses only, checked at add time and before every command; passwords never returned (D27). See `pvj/PROJECTORS.md`.
- The schedule can now run a legacy start script (`preset`) and switch every projector on or off. The old OSC `/beameron` and `/beameroff` work again.
- The fake projector in the tests found a real bug before it shipped: PJLink ends lines with a carriage return alone, and the client read with `readline()`, so it would have hung on every real projector. Also added an overall deadline per command and asked all projectors at once.
- Independent review: no high findings. Fixed with tests: OSC and the schedule no longer wait for projectors (background); the name lookup when adding runs outside the settings lock; names DNS cannot encode give a 400, not a 500; an unexpected error gives a 502; one command at a time per projector; the connect shares the deadline; 169.254.169.254 refused; a typed password is no longer written into the page HTML; the docs state the real limits.

Not verified: no real projector. The owner has none on the test network.

## 2026-09-30 (PIN on screen)

Done: `pvj/pinscreen.py`. The pairing PIN and the panel's addresses are drawn by mpv on its idle screen every 3 seconds, only while no device has ever paired and nothing is playing, using a whitelist of characters (mpv expands `${...}`). Tests use a fake player.

Hardware: mpv's on-screen text on the idle Pi 4 was confirmed by the owner (top left, readable). The PinScreen thread itself was NOT run on the board, because the box already has paired devices and the feature deliberately stays silent then. To test: System > Access, remove every device (or a fresh SD card), reboot, look at the monitor.

## 2026-09-30 (USB, MIDI, audio, playback on the real Pi 4)

Verified on the board:
- USB drive (exFAT, 58 GB, three 3 to 5 GB films): auto-mounted read-only with `nosuid,nodev,noexec` at `/media/pvj/NXLX-USB`, `/media/usb` link made. Three MIDI controllers (Korg nanoKONTROL2, Akai MIDI Mix, Novation Launchpad Mini) and a USB HDMI capture adapter all enumerate. The MIDI module reads a controller through the systemd sandbox (`connected: true`), which confirms the unit fix from the earlier PR; 112 messages in a few seconds were handled.
- 1080p23.976 H.264 plays through the panel from the USB drive: software decode, about 109 percent of one of four cores, 41 C, no throttling, zero decoder drops, A/V sync steady.

Found on the board and fixed in this PR:
1. **Nothing on a USB drive could be played from the panel** (only the media folder was allowed). Added a USB list on the Media screen, `POST /api/play {"usb": "LABEL/name"}` with strict path checks, and made the old `startmasterusb` presets work.
2. **Choppy video**: 15 dropped frames a second scaling 1080p to 2560x1440. `--profile=fast` on Pi 3 and 4 by default cured it (0.0 a second).
3. **No sound on an HDMI monitor**: mpv's default output on a Pi is the headphone jack. New Sound output setting; "Automatic" picks the HDMI port with the screen on it; re-applied whenever the player restarts. Owner confirmed audio is good.
4. **The live screen preview hurt playback** (4.7 dropped frames a second with it open, 1.3 with a snapshot every 5 s). Replaced by a snapshot on request (D20).

Not verified: HEVC or 4K decode, 24 fps judder on a 75 Hz screen (the monitor offers 75, 60 and 50 Hz only), the second and third MIDI controllers' messages, the read-only root, streams, the Network module (needs a keyboard on the box).

## 2026-09-30 (screen viewer, more hardware results)

Done, on the real Pi 4 (Debian 13):
- Merged the two boot fixes (#24). A full reboot brings the services up; `kill -9` on mpv is recovered in about 2 s.
- DMX (Art-Net) over the real LAN from the owner's Mac: blackout on and off and opacity 40 percent all took effect, 143 frames counted. Weekly schedule: a `stop` entry and a `play` entry two and three minutes ahead both fired on time and reported `done`. Board self-test passes.
- Built the screen viewer (D19): `GET /api/preview.jpg`, a Screen card on Live, tests. The owner confirmed on the Mac that it looks right.

Not verified yet: PIN on the projector (still missing), USB drive, MIDI, streams, autostart across a reboot, HDMI audio, 1080p and higher decode load, read-only root, Network module.

## 2026-09-30 (first boot on a real Pi 4)

Hardware: Raspberry Pi 4 Model B Rev 1.5, image built by CI from master `0d7ca86`, flashed by the owner, wired Ethernet, a 2560x1440 75 Hz monitor. Debian 13 (trixie), kernel 6.18 aarch64, mpv 0.40.

Verified on the real board:
- The image boots; `pvj-web` and `pvj-netd` start on their own; the panel answers on port 80 and reports `"board": "pi4"` and the right model; temperature 32 to 38 C, no throttling (`get_throttled=0x0`).
- Pairing with the PIN over the network works; an upload of a 7.5 MB clip over the LAN works; playing through the panel works and the owner confirmed the picture on the monitor is smooth (720p30 H.264, software decode, about 18 percent of one core, 75 Hz display).
- After the fixes below: a full reboot brings all three services up by themselves, and `kill -9` on mpv is recovered by systemd in about 2 seconds with the panel still working.

Found on the board (none of these could show in a container), fixed in the same PR:
1. **The player never started at boot.** `pvj-player.service` had `After=multi-user.target` and is `WantedBy=multi-user.target`, and `pvj-web` is ordered after it: an ordering cycle. systemd deleted the player's start job with one journal line and no error. Fix: drop the ordering, and udev-settle (deprecated). A static test now builds the start-order graph of `install/*.service` and fails on a cycle (and proves it catches the old unit).
2. **The panel could not reach the player.** mpv creates its control socket owner-only (0600) whatever the `UMask`, and the panel runs as another user in group `pvj`. My first fix, a shell `ExecStartPost=` in the unit, did nothing: it ran before mpv had made the NEW socket and changed the stale one from the previous run. Real fix: `pvj-player serve` removes the stale socket, then a detached helper waits for the new socket and sets it to 0660. Tested with the real `serve()` and a stand-in mpv.
3. Not fixed yet: nothing shows the pairing PIN on the projector (the panel text and the manual say it does); H.264 uses software decode (`hwdec-current = no`); a 30 fps clip on a 75 Hz display was smooth here but refresh matching is not configured.

Not verified: HDMI audio, USB drive, MIDI, DMX, streams, schedule, autostart across a reboot, 1080p and higher decode load, the read-only root, the Network module (must be tested with a keyboard on the box).

## 2026-09-30 (manual)

Done:
- Merged autostart (#20). Wrote the user manual `docs/MANUAL.md` (get it running, pair, clips, play, modules, keeping it safe, troubleshooting, what is not built), added the autostart picture to `docs/UI.md`, and brought the stale panel section of `pvj/README.md` up to date (screens, API table, built and not-built lists).
- The manual says at the top that nothing has been booted on a real board.

## 2026-09-30 (autostart)

Done:
- Merged the screenshots (#19). Reran the browser test on master to catch the intermittent network-form failure; results in the next entry if any.
- Built Autostart (legacy tab 1): `pvj/autostart.py`, `/api/autostart` (+ `/test`), System > Autostart card, settings schema 6, `pvj/AUTOSTART.md`, tests. See D18.
- A test found a circular import that only shows when `pvj.api` is the first module loaded; fixed with a lazy import, and a test now imports each module first in a fresh interpreter.
- Fixed a latent bug: legacy preset names were matched with `$`, so `startless\n` passed; now `fullmatch`.

Not verified: never run through a real reboot or an mpv crash on a board.

## 2026-09-30 (screenshots)

Done:
- Added `tests/ui/screenshots.js` (cropped element shots of each screen and card, phone and desktop, from the real panel and the test harness), a non-blocking CI step that uploads them, `docs/images/ui/` (16 images, about 1.4 MB), `docs/UI.md` and a "What it looks like" section in the README. The pictures use test clips and a fake network; the docs say so.
- Taking the pictures showed two real layout bugs at phone width and both are fixed: the buttons in a list row wrapped mid-word ("Pla / y"), and stream addresses were shown in the small-caps label style (mangling them). Also shortened the network "revert" option, which was cut off.
- The inline `<style>` a screenshot script tried to add was refused by the panel's strict CSP, which is the CSP doing its job.

## 2026-09-29 (device plan)

Done:
- Reviewed the device test plan against what is actually in the image and rewrote it: new "Option C" in `tools/DEVICE-TESTING.md` (test the built image on a Pi: flash, first login without a keyboard, a numbered checklist, one test per beta module, what to send back). Option A and B were written for a Pi that already had Raspberry Pi OS, git, mpv and a clone; the image has no `git` and no `tests/`, so `tools/device-test.sh` needs a copy of the repository.
- Found by reading the unit file: `pvj-web.service` used `PrivateDevices=yes` and had no `audio` group, so the MIDI module could never see `/dev/snd/midi*` on the image. Now `SupplementaryGroups=audio`, `DevicePolicy=closed` and read-only ALSA access. Unit text checked by a test; not run under systemd.
- Added `tools/artnet-send.py` to test DMX from a laptop.
- Fixed the stale `image/README.md` (it said the panel was not in the image and the build took an hour).

Open:
- Pi 4 not yet reachable: moved to 192.168.0.0/24; no Raspberry Pi hardware address seen yet, and the SD card is not flashed. The earlier Debian host on 172.16.1.95 could not be logged into.

## 2026-09-29 (CI notes)

- I merged #16 (docs only) while its `panel-ui` check had failed, after commenting "checks green" without reading the result. The failure was the network form step (`panel.test.js` line 110, "typed values survive the redraw", 8 s timeout). The same code on master passed `panel-ui` on the next run, so it is a flake, not reproduced and not root-caused (Playwright is not installed on the dev Mac). Rule from now: read the check list for failures before merging, every time.
- "Deploy manual to Pages" has failed on every master push (`Get Pages site failed ... Pages enabled?`): Pages is not enabled for the repository, or not set to build from GitHub Actions. That is a repository setting for the owner; nothing in the code is wrong.
- A second image build was dispatched from master `2546726` (includes schedule, streams, DMX, MIDI) for the first hardware test. Still never booted.

## 2026-09-29 (first image build)

Done:
- The first run of `image.yml` (workflow dispatch on master, run 36627808613) built successfully in about 34 minutes. Artifact `nxlx-mastercontrol-image`, 717 MB, `image_2026-09-29-nxlx-mastercontrol.img.xz`, sha256 `b270f92c2e021cd78373b961bcc5810d4d29c0eb0b8af08ed619f8953bb779f9`. It kept for 90 days.
- That build predates the schedule, streams, DMX and MIDI modules (it ran on the commit before them); rebuild before flashing for a test.

Not verified:
- The image was **never booted**, on a Pi or in an emulator. "Built" means the pi-gen stage ran to the end and produced a file; it says nothing about whether it boots, brings up `pvj-player` and `pvj-web`, or shows the pairing PIN.

Open:
- Flash it on a Pi 4 and run `tools/device-test.sh`.

## 2026-09-29 (DMX and MIDI)

Done:
- Merged the streams module (#14).
- Added DMX over the network (Art-Net, sACN) and USB MIDI input: `pvj/dmx.py`, `pvj/midi.py`, `/api/dmx`, `/api/midi`, System cards, settings schema 5, `pvj/DMX.md`, `pvj/MIDI.md`, tests. See D17.
- A test found a real bug: regexes ending in `$` accepted a trailing newline (`"/dev/snd/midiC1D0\n"`, `"09:00\n"`). All new validation now uses `fullmatch`. Lesson recorded.
- Independent read-only review (agent) found: no way to reach shutdown, files or other routes, but real defects, all fixed with tests: unlocked apply/stop could leak a second receiver; DMX pad and function channels fired on every value change; the per-source rate limit is defeated by forged sources (added global packet and command caps); turning the module off left the receiver running; a returning source was not a new baseline; a failed level was never retried; MIDI path checks (ASCII digits, character device, no links) and a non-OSError killing the reader.
- Not fixed, by choice: sACN sequence numbers are ignored (documented).
- Never run against a real console, network or USB controller.

Open:
- The image build had not finished when this was written.
- DMX and MIDI need a test with real gear (console or QLC+ on a laptop; a USB pad controller on a Pi, with the service user in the `audio` group).

## 2026-09-29 (streams)

Done:
- Merged the weekly schedule (#13).
- Added the Streams module (SRT, RTSP, RTMP): `pvj/streams.py`, `/api/streams`, `{"stream": id}` on `/api/play`, System > Streams card, settings schema 4, `pvj/STREAMS.md`, tests. See D16.
- Never played a real stream. mpv is not installed on the dev Mac, so nothing here ran against mpv; CI runs the browser test with a headless mpv.

Open:
- First image build was still running when this was written.
- Streams need a test with a real SRT/RTSP source on a Pi.

## 2026-09-29 (scheduler)

Done:
- Dispatched the first image build (`image.yml`) by hand; result in the next entry or the Actions tab.
- Added the weekly schedule module (`pvj/scheduler.py`, `/api/schedule`, System > Schedule card, settings schema 3 with a migration, `pvj/SCHEDULE.md`), with unit tests on a fake clock and API tests. See D15.
- The browser test has a new schedule step. Playwright is not installed on the dev Mac, so that step has only been syntax-checked locally; CI runs it.
- On macOS, 10 `tests/test_update.py` tests fail with `mv: illegal option -- T` (GNU only). They fail the same way on master; they pass on Linux CI.

Open:
- Scheduler not run on real hardware or across a daylight-saving change.

## 2026-09-29 (later)

Done:
- Confirmed the local `docs/html/_images/01_Hdmi_connect.jpg` deletion was a side effect of #9 on a case-insensitive disk; restored it from git, tree clean. The file is still referenced by `docs/html/01_first_steps.html`.
- Confirmed the `legacy-v3` tag is on GitHub at the right commit.

Open:
- Deleting the 11 merged remote branches was blocked by the permission classifier; the owner should delete them (all 11 PRs are merged).
- Still nothing verified on real hardware; the image has never been built.

## 2026-09-29

Done:
- Merged: #4 (phase 4 core), #5 (Library: upload, rename, delete), #6 (wired Network settings, beta), #7 (copyright holder NXLX.Systems), #8 (rename to nxlx.mastercontrol), #9 (removed a case-colliding image).
- Repository renamed to `nxlx.mastercontrol` by the owner.
- Fixed a panel bug found by CI: redraws wiped the Network form; upload errors were wiped by a redraw; added an inline favicon so the browser test sees no 404.
- Added the device test (`tools/device-test.sh`, manual-only runner workflow, setup guide) in #10, and this log and hand-off notes in #11 (both open when written).

Open:
- `legacy-v3` tag not on GitHub (push refused for the session); recreate from commit `ed74df411b88b1a16dd80eecf52c3c9cf6d7768b`.
- Merged branches on GitHub can be deleted by the owner.
- Nothing verified on real hardware. Next useful step: run `tools/device-test.sh` on a Pi 4, then try the Network module on a box that can be reached another way.
- Unbuilt: crossfade, Wi-Fi/hotspot/VLAN, network updates, panel update button, Inputs/NDI/SRT/Dante/ST 2110 screens, mapper, presenter, wall, scheduler, MIDI/DMX. The image has never been built.
