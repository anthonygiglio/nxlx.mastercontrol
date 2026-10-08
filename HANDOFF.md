<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Hand-off notes

For a new person or a new claude session picking this up cold. Read this, then the [project log](project-log/README.md) (decisions, lessons, journal), [README.md](README.md), [ROADMAP.md](ROADMAP.md) and [pvj/README.md](pvj/README.md).

## What this is

**nxlx.mastercontrol** by NXLX.Systems (Anthony Giglio, video artist; "NXLX" is his VJ name, now the whole practice). It is a fork of PocketVJ CP v3 by Marc-André Gasser (magdesign), whose upstream is unmaintained and unreachable. Goal: run on Raspberry Pi 3B/4/5, x86 mini PCs and old computers, on Pi OS Bookworm/Trixie and Debian/Ubuntu, and be rugged at gigs (direct Ethernet on a private network preferred).

The repository was renamed from `PocketVJ-CP-v3` to `nxlx.mastercontrol`.

## Decisions already made (do not reopen without a reason)

- Python 3, standard library only, replaces PHP. The legacy PHP and docs stay in the tree until feature parity.
- New code is Apache-2.0 (SPDX headers, `REUSE.toml`). Legacy code stays under the upstream `LICENSE.md`. `LICENSE.md` and `AUTHORS.md` are never edited. Copyright holder in headers: "NXLX.Systems and contributors".
- One long-lived mpv controlled over JSON IPC, supervised by systemd (`pvj-player.service`). The panel (`pvj-web.service`) is unprivileged; anything needing root goes through small helpers (`pvj-netd`, `pvj-sysd`, `pvj-supportd`) that answer only pvj-web over a socket.
- Token auth (PIN pairing, roles view/live/full), CSRF header, strict CSP. Signed updates with rollback.
- NDI and AES67/Dante are separate optional modules, not built yet. ST 2110 is not planned natively; it comes in through a gateway (D30). SRT/RTSP/RTMP streams, DMX, MIDI, the schedule and autostart are built, off by default.
- Naming: the `pvj` package, `pvj-*` services and commands and install paths keep their names.
- Style: no em dashes in written text (commas, semicolons, new sentences). Default document font Inter.

## Read this first (state at the end of 2026-10-05)

This section is the newest and wins over the older "Start here" below where they differ. The table of open pull requests further down is out of date: #83, #84, #85, #86, #87 and #88 are all merged.

**State.** Master and the test Pi 4 are both at the merge of #88 (`fe6b7fd`), deployed over SSH through the `pvj-dev` account and confirmed by file hash, all five services active. The only open pull request is #65 (the UX proposal, a reference, not to merge).

**Merged on 2026-10-05.** The Signal look (#83, D54) and Signal on every screen (#85, D57); effects measured on the Pi 4 (#84, D56); effect detail, which picks the working size on a small board (#87, D58); the test failures that came and went (#86, D59); themes the owner can add and save (#88, D60). Each had an independent read-only review where it touched the network, uploads or root helpers, and every finding has a test.

**The development machine is moving.** The owner is moving this work from a Mac (disk nearly full) to an HP laptop running Windows with WSL (Ubuntu), on the same home network as the Pi. On a new machine: clone the repository inside the Linux home folder, `gh auth login`, make a new SSH key and add its public half to `/home/pvj-dev/.ssh/authorized_keys` on the Pi, and reach the Pi by address (192.168.0.169 at the time of writing; `nxlx-mastercontrol.local` often does not resolve inside WSL). Deploy as before: `git archive origin/master`, unpack in `/tmp` on the Pi, `sudo -n ./install/install.sh --offline`, then compare a file's hash. The `pvj-dev` account is for the test box only and is removed before the box goes to the venue (`sudo deluser --remove-home pvj-dev && sudo rm /etc/sudoers.d/pvj-dev`).

**Before the final shipment** the owner wants to run a deep review himself (`/code-review ultra`); remind him. It is his to start.

**Waiting for the owner.**

- Which layout: the three options (A "On the wall", B "Front doors", C "Workspace") are a clickable page at https://claude.ai/artifact/7WzDiCQaXwnS2RJX3KBq57. He wants to redo "the tab and how screens are divided. and the laptop view. each tab does look like its doing too much." Nothing in the panel's layout has been rebuilt yet.
- Two Signal choices in D57 to confirm: ambience on Room in Room yellow, and Set up in orange.
- Whether edge glow and corner colour tint (GPU at 94 to 96 percent at 720 lines on the Pi 4) should step down to 540 lines in Automatic.
- His eyes on a real screen: nobody has watched the monitor, a phone or Safari for Signal, effects at Automatic, the added themes (outlined tabs and the sample `tools/theme-samples/soft-room.json` have never been seen), or the mapping card fix.

**The Figma UI kit.** A second file, "nxlx.mastercontrol Signal UI kit" (https://www.figma.com/design/AqZ4LsKRMvxuwC5ciksWdY): 48 variables in one collection, 19 text styles and 43 components were built through Figma's connector before it refused further calls ("You've reached the Figma MCP tool call limit on the Starter plan"; reads and writes both; no reset time given). The plan also allows only 3 pages and 1 variable mode, so a second theme is a duplicate of the file. The owner then chose a local bridge, the open source `figma-console-mcp` with its Desktop Bridge plugin running in Figma Desktop, which has no call limit and needs no token; through it the file was finished on 2026-10-05: page 1 has a "Start here" block, the tokens shown as swatches and the 43 parts; page 2 has thirteen screens at 390 and 1366 built from component instances with auto layout (Room, Live, Shaders and Vibes, System index, Projectors, Mix, Media, Schedule, People and codes, MIDI controller, Network, a page that is off, Pairing); page 3 is a playground with empty frames and a starter shell for each layout option at both sizes. The screens are a likeness, not a copy: some words inside parts are the part's defaults (field values, the confirm's buttons, a group's result line), and the laptop layouts are three columns of the phone's sections. In Claude Code's auto mode the bridge needed an Allow rule added by the owner (`Bash(python3 mcp.py *)`), and it ran only from the main session, not from a subagent. Variable names and how they map to theme files are in [pvj/THEMES.md](pvj/THEMES.md); `tools/figma-theme.py` converts them.

**After this section was first written (late on 2026-10-05).** #90 (the projector test that depended on timing, tests only) and #91 (the Shaders page while its module goes off) are merged. A read-only study of a properly responsive panel is at https://claude.ai/artifact/SM8DS63b4R2KymXMS1zFad: the answer is yes, by a mix of size classes, auto-fit grids, `clamp()` and container queries, with control heights left to the tokens. Seven decisions in it wait for the owner, the first being whether to start step 1 (repairs at 320, a sideways phone and 600 to 899 in today's layout, plus a width sweep in the browser test); it recommends layout A growing into C. Nothing has been built for it. The Figma bridge needs Figma Desktop on the same machine as Claude Code; whether a bridge inside WSL can reach Figma running in Windows is untested.

**Known and open.**

- "no reply from mpv" to a screenshot in the GPU tests has now been seen twice in CI (once on OpenGL ES in `test_a_capped_effect_draws_the_right_picture...`), never reproduced. Worth chasing, not only rerunning.
- GitHub sometimes never gives a job a runner ("The job was not acquired by Runner of type hosted"); rerun the failed jobs.
- An effect put on a paused clip is not drawn until the next frame; the projection mapping alone is heavy on a Pi 4. (Fades ran about 30 percent long; `Fader.ramp` now paces its steps against the clock, tested with a clock of its own and not run on hardware.)
- Never run on hardware: a real projector, the Wi-Fi feature, any controller control pressed by a person, a Pi 3, a Pi 5, x86.
- The panel makes a new pairing PIN at every start, so each deploy changes it (`sudo -n pvj-pin` shows it); paired devices are unaffected.

## Start here (state at the end of 2026-10-04)

**State.** At the end of 2026-10-04 master and the test Pi 4 were both at the merge of #78; master has since taken #79 (the shader measurements, which change no behaviour of the engine beyond the classes it reports). The Pi was deployed over SSH and checked by file hash; all five services are active (`pvj-player`, `pvj-web`, `pvj-netd`, `pvj-sysd`, `pvj-supportd`). It is still on the wired network (192.168.0.169); Wi-Fi is not joined.

**Merged on 2026-10-03 and 04.** PJLink Phase 1 (#61, D37); box care: settings export and import, diagnostics, factory reset (#62, D39); Shaders and Vibes, the first version (#63, D38); the Room screen with groups and scenes (#64, D40); two CI flakes (#66); the System index with a page per item (#67, D42); Wi-Fi control (#68, D41); one switch per feature and the Shaders page (#69, D43); the ISF library with a pack of seven Vidvox generators (#70, D44); a runtime folder per service under a root-owned `/run/pvj` (#71, D45); the shader engine: live values, speed, presets, sets, a guard for heavy shaders (#72, D46); the midday hand-off (#73); 30 new shaders in an Ambient and a Performance family (#74); edit a projector, and guest codes a presenter can make (#75, D48); the Shaders page as an instrument (#76, D47); controller profiles for the nanoKONTROL2, MIDI Mix and Launchpad Mini (#77, D49); every System page on one set of patterns, with questions in place (#78, D50). Each risky one had an independent read-only review with every finding fixed. The journal has an entry for each; several of those entries say "not merged", which was true when they were written.

**Open pull requests.** #79 (the shader measurements on the Pi 4, D51) was in progress at the end of 2026-10-04 and merged on 2026-10-05; see "The shader measurements" below.

| PR | State |
| --- | --- |
| #65 | The UX proposal (a click-through prototype and a written spec for Live, Media, Mix and the Shaders page). A reference, not to merge as is. |
| #87 | **Effect detail (D58):** effects choose their own working size on a small board. The on/off Half resolution became a box setting (Automatic, 540 lines, 720 lines, Full); Automatic on a Pi 4 is 720 lines (540 for isf-edge-blowout and for an upload), from a run on the test Pi 4 the same day. Three fixes from the first run ride along (the guard over a looping clip, the shown frame rate, a lingering `error`). **Not merged by its author.** See "Effects" below. |
| #84 | Effects on the Pi 4: the first run on hardware and every filter measured (D56), with the table in the code, the steps as run and one small fix. Records and one fix; **not merged by its author**. See "Effects" below. (#81, effects themselves, merged on 2026-10-05 and is on the box.) |
| #86 | The test failures that come and go (D59): five causes shown and fixed in the product (the mapping card, the helper clients, a projector edit, a held MIDI button, a lost update of the shader settings), a report of every page when a browser step fails, and the test matrix without fail-fast. Green in CI, **not merged by its author**. Before merging: an independent read of the helper clients' change (`pvj/netd.py`, `pvj/sysd.py`, `pvj/supportd.py`). See "Test failures that come and go" below. |
| #83 | The Signal look (D54): a second style for the whole panel, chosen under System > Look, not the default. Needs an independent review of the `pvj/server.py` change before merging, and the owner's eye on a real phone. |
| #85 | Signal carried through every screen, page and state (D57), with most care on Room and Projectors. Panel only. **Not merged: the owner looks at the pictures first** (the `signal-` pictures in the `ui-screenshots` artifact) **and confirms two choices: ambience on Room is Room yellow, and Set up is orange `#ff9500`, not Figma's amber.** A page added to the panel is added to `tests/ui/signal-pages.js`, which pictures and checks it in the look. |

**Themes the owner can make (2026-10-05, #88, D60). Not merged: it adds a route that takes a file, so it needs an independent security review first.** A theme may now set a bounded list of design tokens (radius, border, density, control heights, title case and weights, fonts on the box, filled or outlined) besides its colours; the Signal block reads them through `--tk-*` variables with its own values behind them, so the built-in Signal is as it was. The box refuses a theme whose text is under 4.5 to 1 and names the pair. System > Look has a picture of each look, "Add a theme", "Save this look as a file" and Remove; added themes live in `<state>/addons/themes`, travel in a settings file, and go at a factory reset. `tools/figma-theme.py` turns the variables of the Figma file "nxlx.mastercontrol Signal UI kit" into a theme file. [pvj/THEMES.md](pvj/THEMES.md) ("Make your own theme") has every token, its range and its name in Figma. For whoever reviews it: `pvj/themes.py` (`parse`, `checked`, `Store`), `api.add_theme` and `remove_theme`, `boxcare._check_themes`, and the tests in `tests/test_themes.py` and `tests/test_theme_api.py`. Still to do: look at outlined tabs and the sample theme on a real screen; add a font family if the owner wants one.

**The look (2026-10-05).** The owner picked direction D, Signal, in the Figma file, to try. It is in #83 as two themes (Signal, Signal light) of a new style; [pvj/THEMES.md](pvj/THEMES.md) holds what Figma says, what was decided here, the contrast figures and how a style is added. A new part of the panel must be given its Signal rules in the marked block at the end of `app.css` and looked at in the `signal-` screenshots; the browser test's Signal pass checks sizes, overflow and contrast, not whether it looks finished. Not tried on a phone or the Pi.

**The owner's direction, in their words.** From 2026-10-03 and 04, still standing: "the way users interact with each module doesn't seem intuitive or easy to use"; "don't be shy about really making the control panels engaging and userfriendly, the panel design and layout and pages should be organized in a logical fashion"; "someone doing more intense work will have a laptop, usually"; "a more robust shader playback and control system. i want to have more shaders available to perform with or have as auto-playing vibes"; "don't forget about ISF shaders (https://github.com/Vidvox/ISF-Files)"; every screen should get "the same audit and design treatment". New on 2026-10-04:

- On the look: "the whole visual style is on the table. I'm open to suggestions, and they can be very different options".
- On the order of the redesign: "nailing down the design language and style is first, then the projection control, then performing".
- "No telling the user to go to a different page to find the controls" is now a default, not a hard rule: "you may drop the 'no going elsewhere' rule if the panel design makes sense" (D52). A page still holds its own controls unless a link is clearer.

**The redesign workspace.**

- The Figma file: https://www.figma.com/design/Cu6AGouBnUBMeBCIPPx02o. Four style directions: A Stage, B Desk, C Gallery, D Signal. The owner's Figma plan allows 3 pages and 1 variable mode, and possibly 20 connector calls a month (see LESSONS), so plan each call.
- The guide: https://claude.ai/artifact/VkC5rzfZiPNRgPFazyYKx1.
- The job board for the whole project: https://claude.ai/artifact/V6uoeXTgNYKn7PaqeuVDbj. It is a claude.ai page with a database: the cards are in the collection `cards`, its settings in `settings/links`.
- Older, still there: the layout board (https://claude.ai/artifact/Bc24QHaMhyeS3eZfFyamLJ; press Save, then ask Claude to read it back), the design playground (D34, `tools/panel-playground`, preview at https://claude.ai/artifact/DYUr95vVi1jJyGquxhqZTu; its `panel.css` is well behind `app.css`), the proposal's prototype (https://claude.ai/artifact/NwPErwtYzJb6WTaAj9UqKj), and the editable mock-ups of every page for Figma (`docs/mockups/README.md`; the files are the CI artifact `ui-mockups`, not in git).

**Verified on the Pi on 2026-10-04** (systemd 257, Debian 13):

- The runtime folders (D45): steps R0, R1, R5, R6, R9 and R10 of the "Runtime folders" checklist in `tools/DEVICE-TESTING.md` (the installer over the running older version, a restart of each unit, a crash of each service, a reinstall, the accounts refused each other's things), and the snapshot through the panel's sandbox while a clip played.
- The new shader engine drawing silk, tunnel and bloom. How it was judged is not written down; the earlier shader runs were judged from snapshots and the player's counters, with nobody at the monitor.
- The three MIDI controllers are recognised by their profiles when plugged in. No message has been received from any of them yet.

**Not verified there:** a reboot with the new folders (R2); a boot with no network (R8); an update from the old version through the panel (R1b); a rollback; any controller pressed, turned or taught; any projector; Wi-Fi (the feature has never run on hardware).

**The shader measurements (2026-10-05, #79, D51).** All 47 bundled shaders were measured on the Pi 4 at 540 and 720 lines through the engine, judged from snapshots and the player's counters; nobody watched the monitor. The GPU took every one and every picture was right. 33 are light, 11 medium (they hold 30 frames a second at 540 lines, the Pi 4's default, and not at 720), 3 heavy (nxlx-drift, nxlx-nebula, isf-ridgelines). At 540 lines nothing in the default sets drops frames; at 720 eight members of Ambient do. The table is in `pvj/SHADERS.md`, the method in the journal. Still open from it:

- Every number is from a 75 Hz monitor at 2560x1440, where 30 pictures a second fit badly. A 60 Hz or a 1080p projector may behave differently: measure there before trusting the classes.
- Three shaders sit at the line between two classes: nxlx-radar, nxlx-scope and isf-simplex-noise. Their class may flip on another screen or another run.
- The test box still has 720 lines saved from the first version; the Shaders page (and, for the owner, the Room screen) now says so and offers 540 (#80). Nobody has pressed it on the box.
- "Cannot load libcuda.so.1" is still logged at each clip start. It was left alone on purpose: read `hwdec-current` on the Pi while an H.264 clip plays and find how the line reaches the journal, then name that decoder in `playback_profile` (the journal entry for #80, item 5).
- Changed after the run and not yet on the box (#80): the guard judges the average drop rate over its window (lantern at 720 lines should now be marked), a set of one shader stays on without a dip, a heavy mark keeps whole lines, old shader texts are removed at start and on Stop, and the panel has an icon. Each is tested with fakes only: run Vibes on Ambient at 720 lines and watch for lantern, and a set of one for a dwell.
- Its shader settings are saved in the engine's form with both sets written out, so a later change of the default sets will not reach it by itself.
- The raw data of the second run is in `/tmp/nxlx-measure.FCbYZw` on the Mac (about 35 MB), which macOS will empty in time: move it if it is wanted. The first run's is in `~/nxlx-shader-measure-2026-10-04`.

**Effects (2026-10-05, #81 merged, D55; measured in #84, D56).** An effect is an ISF filter put on over whatever plays, one at a time, with an Amount mix, live values, presets, Previous and Next and MIDI actions; a strip on Live and a card on Mix. 37 filters come with it: twelve of our own and 25 from ISF-Files. **On 2026-10-05 all 37 ran on the test Pi 4** through the engine, over the owner's clips, judged from snapshots and the player's counters (nobody watched the monitor): every one was taken by V3D and drew the right picture, colours and geometry right, also over the hardware decoder's `drm_prime` pictures; Blackout stays black under an invert. **Over a 720p clip every filter holds 30 frames a second; over a 1080p clip every one drops frames at full size (0.2 to 11 a second) and none does with Half resolution.** So 36 are "medium", isf-duotone is "light", none is "heavy". The table is in `pvj/SHADERS.md` ("Effects on a Raspberry Pi 4") and in `PI4` of `pvj/effects.py`; the method and every number are in the journal. Open from the run:

- **Decided and built in #87 (D58): Effect detail.** The owner: "If running on a Pi4 or 3, let's auto-scale clips to 720p or 1080, whichever performs better overall." The Half resolution switch is gone from the card; in its place is one setting for the box, **Effect detail**: Automatic, 540 lines, 720 lines, Full. The filter works on a copy of the picture whose shorter side is at most that many lines; a clip at or below it is not scaled. **Automatic is what a Pi 4 has when nobody chose, and there it is 720 lines, 540 for isf-edge-blowout and for an upload**, measured on the test Pi 4 on 2026-10-05: at full size every filter drops frames over a 1080p clip, at 720 lines 36 of 37 hold with nothing dropped, at 540 all do. The table is `AUTO` and `PI4_720` in `pvj/effects.py`; the numbers are in `pvj/SHADERS.md` ("Effect detail on a Raspberry Pi 4") and the journal. A Pi 5 and x86 start at Full and Automatic scales nothing there (not measured); a Pi 3 would get 540 lines (not measured, and effects are not offered there). What to know before touching it:
  - **It costs look.** At 720 lines over a 1080p clip the picture is softer and a halftone's dots are one and a half times as large; at 540, twice. Judged from six snapshots, by nobody's eye on a display.
  - **Two filters have little room at 720 lines** (fx-edge-glow and isf-corner-color-tint: the GPU 94 to 96 percent busy, nothing dropped in three windows, 135 seconds in all). The mapping on top will tip them; the guard then says "lower Effect detail to 540 lines".
  - **Amount 0 is the clip at its own size** (the player is told to leave the filter's pass out), but an effect that is on at 0 still costs the player its extra passes (22.6 ms a frame against 13.1 over a 1080p clip, nothing dropped). Between 0 and 1 the whole picture is the capped one: mixing with the clip at full size was tried on the board and drops frames.
  - **`"half": true` still works** (that one effect at 540 lines at most); `false`, which every saved preset holds, follows the box. Do not make `false` mean full size: every preset would then override Automatic.
  - **The player turns a picture before the effect's hook** (CI asserts it). The size lines in the text do the arithmetic for a lying and a standing picture alike and are thirty words long each; mpv reads at most thirty-two.
  - **Automatic is a table, not a controller:** it does not follow the guard. An effect that proves too heavy stays on until someone lowers the detail.
- **Over a frozen clip an effect is not drawn until the next frame** (the player redraws its cached picture), the request waits 4 seconds, and the panel's snapshot shows the effect anyway. Somebody has to look at the real screen with a clip paused, then decide on a cure (the journal, item 2 of "Found").
- **The mapping with three surfaces drops 6 to 10 frames a second by itself** on the 2560 x 1440 screen; an effect adds 4 more. Measure the mapping on the venue's projector before planning a mapped show with effects.
- **Three of the owner's ten clips cannot be played smoothly on the Pi 4** (two HEVC, one of 1206 x 2622 at 60): 17 to 28 frames dropped a second with no effect.
- **A fade took about a third longer than asked** (`Fader.ramp` slept a full step on top of what each step cost). Fixed on 2026-10-07: the steps are paced against the clock, as Vibes' own fade already was. Not run on hardware. The other half of this line, a clip shorter than 6 seconds that could never be called "heavy" by the guard, was already fixed in #87 (`Guard.sample` adds the rises up across a loop; `test_a_clip_shorter_than_the_guards_window_can_be_called_heavy`).
- **Not run:** the buffer comparison for more than one filter, a restart of the player with an effect on, an upload and a GPU refusal on V3D, the panel in a browser, a controller. A person pressed Stop on the panel at 13:07 UTC and the run ended there.
- The contact sheets of the run (24 pictures, 7.4 MB) are in a temporary folder on the Mac that macOS will empty in time; the pull request names it.
- **8-bit buffers under an effect** are right on the Pi 4 for the one filter measured (fx-wash over 1080p: 0.9 dropped a second against 8.5 in the player's own buffers), and left alone on other boards after a black screenshot in CI (D55).
- **Two things seen in the generators:** the line a refused generator names is one too high on the Pi's path (GLSL 1.40 counts `#line` differently), and `fbo-format` was set again at every change of a generator's value, which sets mpv's renderer up anew each time. The second is fixed (2026-10-07): `Player.swap_source` sets the format only when a source comes or goes, as an effect's layer does. Tested with a fake player; not run on hardware, so whether a value change is now smoother on the Pi 4 is not known. `Player.play_source` (another shader, Vibes' next one) still sets it every time.
- **On mpv 0.37 with its default scalers a video after one of another size was black in CI until a shader changed** (LESSONS). It is not about effects. A Pi 4 runs with cheap scaling, where it did not happen; an x86 box or a Pi 5 would run with the default scalers: look for it there.
- **Seven pack files were taken out after the review** (two matched a public snippet their credit does not name; five more as a precaution, without a comparison). If the owner wants a bulge, a ball or a splash back, they are to be written, not restored.
- **With nothing playing, Next and the one effect button answered "ok" and then did nothing.** Since 2026-10-07 a paired device (the panel) gets 409 with the reason, also for Previous and for a preset of another effect: its request asks the player first. A controller's call (MIDI, OSC, DMX, a Room scene) still asks the player nothing and still hears "ok", with the reason in `error` once the worker has looked; that is D55's rule, kept, and a test counts the questions. Tested with a fake player; not run on hardware and not pressed in a browser.
- **A race in the MIDI hub, found through a test that failed now and then:** when MIDI was switched off between a button's press and its release, the hub kept the button as pressed, and its first press after MIDI was back did nothing. Fixed in pull request #86 (the hub forgets what was held when it stops listening); see "Test failures that come and go" below.
- **Left out on purpose:** a chain of effects, transitions, passes, persistent buffers, an effect that comes back after a restart (the vignette for the painting wall has to be put on again), a preset rename in the panel, pictures of the card in `docs/UI.md`.

**What needs the owner.**

- A projector on the network: nothing of PJLink, the Room scenes or the Projectors page has met a real one.
- Pressing the controllers: the two-minute check in `pvj/MIDI.md` (does each control do what its drawing says).
- Controller lights, once #82 is reviewed and on the Pi: the check list "Controller lights" in `tools/DEVICE-TESTING.md` (Test lights, play a pad, start Vibes, black out). For the nanoKONTROL2, LED Mode must be set to External in Korg's editor first.
- The dev account `pvj-dev` on the Pi: the owner created it on 2026-10-05 (see "Working on the test Pi"). It is for the test box only and has to go before the box goes to the venue.
- The Mac's disk. It filled on 2026-10-04 and stopped every agent (see LESSONS). Free space before starting several agents.
- Two old paired devices named `claude-test-mac` on the Pi: remove them under System > People and codes.

**What is next, in order.**

1. The owner picks a style direction in the Figma file (A, B, C or D, or a mix).
2. In that style: the room and the projector screens (Room, Projectors).
3. Then performing (the Shaders page, controllers). Live, Media and Mix are not redesigned yet; the proposal (#65) is the reference for them.
4. Controller lights are built and reviewed (pull request #82, D53; the security review's findings are fixed, the sandbox line `DeviceAllow=char-alsa rw` was judged acceptable with its documented cost) and wait for the owner to merge and for the check on the hardware. Follow-up from the review, to try on the Pi: a udev group for MIDI nodes so the panel can drop the `audio` group (D53).
5. Effects: the working size is decided and built (#87, D58). Still to do with somebody at the screen: look at 720 lines against 540 and Full with a halftone and an edge filter on the real display, and at a frozen clip with an effect on (above); then measure Effect detail on a Pi 5 and on the venue's projector, and fill their rows of `AUTO`. A chain of effects, and transitions, are the next steps of the survey in `pvj/SHADERS.md`.
6. PJLink Phase 2 (class 2 volume, freeze, input names, signal resolution, "Find projectors", status notices), after the first real projector has answered what Phase 1 could not: what it says in standby, how long it is unavailable after power-on, whether 90 seconds is enough for a scene's source and mutes, whether "Cooling down" is reported.

Further out, unchanged: live coding with a last-good fallback and projectM as an optional module; painting-wall masks on the second projector (likely the Pi 3B as a second synced box, which waits for a spare SD card; never touch its current card); network notes for the private NXLX network; a video matrix driver once the owner picks a model; display mode; mapper workflow; wall improvements; a schedule with dates; follow-on actions.

**First checks still owed on the box, from earlier work.**

- Wi-Fi (D41), from the wired port or a keyboard, never over the only link: set the Wi-Fi country, switch Network on, Find networks, join a known network, confirm, a hotspot from a phone, Wi-Fi off and back. The owner wants the box on Wi-Fi with the wired port for a projector (the wired port 192.168.10.1/24 with no router, the projector 192.168.10.2).
- Box care (D39): export and import, the diagnostics file, a factory reset with the PIN screen returning. A factory reset cannot remove the root-only update backups in `/var/lib/pvj/backups` (they hold the old devices and passwords, and a rollback brings them back); it needs a `pvj-sysd` action.
- The persistent log (D35): after the Pi's next restart, `journalctl --list-boots` should show more than one boot.
- The shader engine by hand: drag each kind of control and the speed and look for a hitch; let Vibes run at 720 lines and see the guard act; let a staff member start ambience from the Room screen with two sets present.
- The System pages (D50) and the Room screen's ambience button (D52) have run only in CI's browser test: see them on a real phone at arm's length.

**How to work here.** Three or four agents at once hit the usage limit four times on 2026-10-04 and each time killed every agent mid-task; later the same day the Mac's disk filled and did the same. Run one or two agents, keep scratch files small and delete them, have each agent open the pull request after the first commit and push after every step, and expect to resume from the branch. The dev Mac has no mpv and no Playwright, so the browser test runs only in CI. The owner allows merging reviewed, green pull requests and deploying to the test Pi over SSH without asking (no new images); say when a deploy happened, since it restarts the panel and the player.

## Test failures that come and go

Looked at together on 2026-10-05 in pull request #86 (not merged when this was written). The journal entry of that day has the table with the evidence for each. A new one goes in this list with the run it was seen in.

| Failure | State |
| --- | --- |
| Browser test, mapper step ("Main stage" not seen in `#mapsets`) | Cause shown and fixed in the panel (#86): a rebuilt mapping card took the name field away while it was being typed in. |
| `tests.test_netd`, a refused caller: broken pipe | Cause shown and fixed in the three helper clients (#86). |
| `tests.test_projector`, an input change as an edit lands: 'ERR3' != 'stopped' | Cause shown and fixed in `Monitor.set_input` (#86). |
| MIDI: a button held when MIDI goes off (`tests.test_lights`, `test_m1_...`) | Cause shown and fixed in the hub (#86). |
| "Cancelled" `test` jobs | Not a fault of the tests. A job's annotation says what ended it: the matrix's fail-fast after the other Python had failed (23 of 60 runs; switched off in #86), or a cancel by hand (every other one that was read; superseded runs are cancelled to free runners). |
| Browser test, Shaders page: `#shadernow #detailhigh` not seen (once) | A lost update of the shader settings was found and fixed (#86). That it was the cause that one time is inferred from the request times. The step prints its state if it happens again. |
| Browser test: a staff device not seeing the painting wall "Warming up" (once, 2026-10-04) | Not explained. The step's diagnostic and the report of every open page print the state. |
| A `test` job that sat in the unit tests for 35 minutes (once, controller-lights) | Not explained and not seen again in 189 runs of that step. The step ends after 20 minutes with every thread's stack. |
| A `panel-ui` job twenty minutes in "Install mpv and a browser" (once, 2026-10-05) | That runner's package mirror was slow (about 50 kB a second); nothing of ours. Every job of `pvj.yml` has a time limit now and every install step ten minutes (#86): rerun it. |
| `test_projectors_are_not_all_asked_at_once` (twice: 0.295 and 0.248 s where more than 0.3 was wanted) | Cause shown and the test fixed (#86): it measured the gap between two projectors' first checks, which a slow "who are you" narrows. It is three projectors now, each measured from the start, and it fails against one delay for all. |
| Jobs "cancelled" after half an hour in the queue (2026-10-05, several) | GitHub's: the annotation says "The job was not acquired by Runner of type hosted even after multiple attempts". Nothing ran. Rerun the run when the queue is shorter. |
| `tests.test_shaderlive_gpu` `test_time_goes_on_across_a_change_of_a_value_and_of_the_speed`: "no reply from mpv" to a screenshot (once, 2026-10-05, Python 3.9, the GLSL 1.40 step, run 37339188136 attempt 3) | Not explained. The control socket waits 2 seconds; the speed had just been set to 0. |
| `test_no_secret_is_in_it` (once, on the Mac: the PIN's digits inside a byte count) | Not fixed (LESSONS). |

When a browser step fails, the job's log has, after the `FAILED:` line, what every open page showed (screen, message line, cursor, the elements the step named, the whole screen's text) and the box's last answers to it. Read that first. The server's request log, with times, is in the same job log. The two `test` jobs no longer cancel each other, so both logs are there.

## What exists

The System screen is an index of rows in three groups with one page per row (D42): a module is switched on its own page (System > Projectors, System > Shaders and Vibes and so on), and each row shows a state chip. The page switch is the only switch for a feature (D43): for DMX, MIDI and the schedule it turns on the module and then the feature's own flag, for OSC and Remote support it is the flag. Everything about shaders is on the Shaders and Vibes page (also the Shaders link next to the big Vibes button on Live); nothing of it is on Mix. A page holds its own controls by default (the Shaders page has the MIDI teach rows and the DMX channel, System > Projection mapping has the mapping card, System > Room has the Room screen's cards), and a link to another page is fine where it is clearer (D52). Every System page is built from one set of patterns and asks in place before a Remove or a power action (D50). The Room screen, where staff land, starts and stops ambience with one big button: ambience is the Vibes rotation under the name staff use (D52).

Merged to `master`: the security hotfix, the platform layer, the installer and services, the image definition, and the new core in Python 3 (API, panel, modules, themes, OSC receive, signed updates with rollback). On top of that, each with its own notes:

| Feature | Notes | Default |
| --- | --- | --- |
| Library: upload, rename, delete; play from a USB drive; copy a clip from USB to the box | `pvj/README.md`, `docs/MANUAL.md` | on |
| Playback: pads in banks, seek, skip, prev/next, play all, shuffle, end-of-clip, slideshow, audio files, fade in/out, freeze, blackout | `docs/MANUAL.md` | on |
| Mix: opacity, size, position, speed, volume, rotate, mirror, PNG overlay, transitions (cut, dip to black) | `docs/MANUAL.md` | on |
| Clip advice (what plays well on this board) | `pvj/probe.py` | on |
| Sound output, test tones | `docs/MANUAL.md` | on |
| Screen snapshot (on request; live view dropped, D20) | `docs/MANUAL.md` | on |
| PIN and the box's address drawn on the screen | `pvj/pinscreen.py` | on |
| Health card (power, temperature, helpers, addresses) | `pvj/health.py` | on |
| Updates card: signed bundles from USB or upload, rollback (D33) | `pvj/README.md` | on (needs a signing key) |
| Settings export and import, diagnostics file, factory reset (D39; not run on hardware) | `pvj/boxcare.py`, `docs/MANUAL.md` | on |
| Remote support over WireGuard, on request only | `docs/REMOTE-SUPPORT.md` | off |
| Old OSC command names | `pvj/OSC.md` | with OSC |
| Autostart (file, all, slideshow, pad, USB, preset) | `pvj/AUTOSTART.md` | off |
| Weekly schedule | `pvj/SCHEDULE.md` | off (beta) |
| Projectors (PJLink class 1: power, input with labels, picture and sound mute, live status, lamp hours, warnings, edit in place; no real projector tested) | `pvj/PROJECTORS.md` | off (beta) |
| Room: groups of projectors, scenes tapped once (power, source, mutes, what the box plays), a Room screen for staff with a button for ambience (D52), scenes from the schedule, OSC and MIDI (D40; no real projector tested) | `pvj/ROOM.md` | off (beta) |
| Projection mapper (quads, triangles, grids) | `pvj/MAPPER.md` | off (beta) |
| Effects: ISF filters over what plays, one at a time, with Amount, live values, presets and MIDI; 12 of the project's own and 25 from ISF-Files (D55; all 37 drawn and measured on the Pi 4, D56) | `pvj/SHADERS.md` ("Effects") | with Shaders and Vibes |
| Shaders and Vibes: ISF generator shaders (40 of the project's own in an Ambient and a Performance family, and a pack of 7 from Vidvox's ISF-Files, D44), Vibes, an endless rotation from one tap, with sets; the engine (D46: live values of every input type, speed, presets, a guard for heavy shaders) and the Shaders page as an instrument (D47); the API, autostart, the schedule, OSC, MIDI and DMX | `pvj/SHADERS.md` | off (beta) |
| Multi-box sync and video wall | `pvj/SYNC.md` | off (beta) |
| Streams and live input (SRT, RTSP, RTMP, USB capture) | `pvj/STREAMS.md` | off (beta) |
| DMX (Art-Net, sACN), MIDI controllers, with built-in layouts for the nanoKONTROL2, MIDI Mix and Launchpad Mini (D49), and their lights (D53, pull request #82, not merged, not tried on hardware) | `pvj/DMX.md`, `pvj/MIDI.md` | off (beta) |
| Guest codes that a presenter can make, show on the room screen and end (D48) | `docs/MANUAL.md` | on |
| A runtime folder per service under a root-owned `/run/pvj` (D45) | `pvj/paths.py`, `tools/DEVICE-TESTING.md` | on |
| Network settings with confirm-or-revert: wired, and Wi-Fi (join a network, own hotspot, Wi-Fi off, find networks; D41) | `pvj/NETWORK.md` | off (beta) |
| System log kept across restarts, 64 MB cap (D35) | `docs/MANUAL.md` | on |

Docs for people: [docs/MANUAL.md](docs/MANUAL.md) and pictures in [docs/UI.md](docs/UI.md) (made by `tests/ui/screenshots.js`, which with `MOCKUPS` set also writes the editable mock-ups, `tests/ui/mockups.js`). Module manifests: `pvj/modules.d`.

## What has been run on real hardware, and what has not

One test Raspberry Pi 4 (Model B Rev 1.5, Debian 13 trixie, wired Ethernet, a 2560x1440 monitor, a USB drive, three USB MIDI controllers, a USB HDMI capture adapter) has run the CI image since 2026-09-30 and is kept on current master. **Verified there:** boot, panel and PIN pairing, uploads, playback (720p and 1080p H.264), HDMI sound, USB automount and playing from it, reboot and crash recovery, DMX over the LAN, the weekly schedule, autostart across reboot and crash, MIDI reading with all three controllers, the screen snapshot, the mapper's output (checked through screenshots; nobody watched the monitor), sync between two players on the box and against a stand-in server (within 5 ms after catching up), remote support against a stand-in hub, the health card, and a signed update through the Updates card followed by a rollback (2026-10-01). On 2026-10-04: the runtime folders (checklist steps R0, R1, R5, R6, R9 and R10, and the snapshot through the panel's sandbox while a clip played, on systemd 257), the shader engine drawing silk, tunnel and bloom, and the three controllers recognised by their profiles (no message received yet). On 2026-10-05, shaders: all 47 bundled ones drawn and timed at 540 and 720 lines, live value changes, the speed control, presets, Vibes with its dip and the guard (from snapshots and the player's counters over SSH). On 2026-10-05, effects: all 37 bundled filters drawn and timed over a 1080p and a 720p clip at full and half size, colours and geometry against the plain picture, Blackout and a fade under an invert, live value changes, presets, Previous and Next, the gap between switches, the effect's life across clips, Stop, a generator and Vibes, a mapped surface, TIME (the same way: snapshots and counters). On 2026-10-05, Effect detail (#87's code, installed for the run and master put back after it): thirteen filters over the 1080p clip at full size, 720 and 540 lines, the other twenty-four at 720 lines, all 37 at Automatic, the 720p clip at Automatic, amount 0 and 0.5, the old `half`, and a two-pass text given to the player by hand. Details and numbers are in the journal.

**Not verified on hardware (do not claim it works):**
- Sync and the wall on two real boxes (waiting for the Pi 3B's spare card); a real support server (VPS); swapping USB sticks while running; copy from USB on the box.
- MIDI Learn with the real controllers, and any control of a controller profile pressed or turned; the PIN on screen on a fresh box; streams with a real source; HEVC, 4K, 1080p60; 24 fps judder; the second HDMI port; a real projector.
- Effects: nobody has watched one on a display (both Pi 4 runs were snapshots and counters), so a stutter that drops no frame, what a frozen screen shows and how 720 or 540 lines really look are unknown; no controller, no browser on the box, no stream or live input, no upload, no restart of the player, no board but the Pi 4 (so Automatic scales nothing on a Pi 5 or x86, and a Pi 3's 540 lines are a guess), no screen but 2560 x 1440 at 75 Hz, no clip that stands or is shown turned under a cap (CI only), no mapping together with a cap.
- Shaders and Vibes: nobody has watched it on a display (both Pi 4 runs were judged from snapshots and the player's counters, so a single black frame or a stutter that drops no frame would not have been seen); the engine's controls never dragged by hand on a box; its MIDI, OSC and DMX controls only against fakes; the Shaders page and the ambience button on the Room screen only in the browser test; 360 and 1080 lines, a 60 Hz screen and a projector not measured; no other board measured.
- Since the runtime folders (D45): a reboot, a boot with no network, an update from the old version through the panel, a rollback.
- The read-only root (`pvj-rootfs`), the Network module and its Wi-Fi control (test with a keyboard and monitor on the box, never over SSH or Wi-Fi on the only link), TouchOSC.
- Pi 3, Pi 5 and x86.

Not built: crossfade (needs a second player), display mode, company (802.1X) Wi-Fi and setting the Wi-Fi country, updates from the network, NDI, AES67/Dante, presenter, importing old mapper files, controller lights (feedback), a chain of effects and ISF transitions (one effect at a time is in #81), PJLink Phase 2, custom DMX layouts.

Known limits: the panel cannot restart a wedged mpv (unprivileged by design). Merged branches on GitHub are not deleted (ask the owner). GitHub ruleset "Protect master" requires 9 checks and pull requests; do not change it without asking.

## Working on the test Pi

- SSH as `nxlx@nxlx-mastercontrol.local` (or 192.168.0.169). A session key is set up per session; ask the owner to `ssh-copy-id` a new one.
- Since 2026-10-05 there is also `ssh pvj-dev@nxlx-mastercontrol.local` (key only, the dev Mac's key, sudo without a password), made by the owner for deploys and measuring runs, so the staged password below is not needed with it. Test box only: before the box goes to the venue, `sudo deluser --remove-home pvj-dev && sudo rm /etc/sudoers.d/pvj-dev`.
- `sudo` needs the owner's password, which is never written into the repo, memory or chat. When needed, the owner stages it on the Pi with `read -rsp "password: " p; printf '%s' "$p" > /tmp/.pw; chmod 600 /tmp/.pw; unset p; exit` (gone at reboot). Check it with `test -s /tmp/.pw`, not `test -f`: typed from a phone it has come out empty (see LESSONS). Use it only as `S(){ cat /tmp/.pw | sudo -k -S -p "" "$@"; }`, never pipe data into an `S` command (see LESSONS), and delete `/tmp/.pw` when done.
- Deploy master: `git archive --format=tar.gz --prefix=nxlx-src/ origin/master | ssh pvj-dev@<box> 'rm -rf /tmp/nxlx-src && tar -xzf - -C /tmp'`, then on the box `cd /tmp/nxlx-src && sudo ./install/install.sh --offline` (it restarts the panel and the player; the five services must be active afterwards). To see what a box runs, compare `sha256sum` of every file under `/opt/pvj/current/pvj` and `bin` with the same files of `git archive <ref> pvj bin`. Settings have a schema number; never put older code on a box with newer settings.

## Testing

- `python3 -m unittest discover -s tests` (about a minute; needs mpv).
- Browser test: `node tests/ui/panel.test.js` (needs Playwright and Chromium).
- On a real board: **Option C** in [tools/DEVICE-TESTING.md](tools/DEVICE-TESTING.md) (flash the CI image, then the numbered checklist). `tools/device-test.sh` and the manual "Device test" workflow are Options A and B. `tools/artnet-send.py` sends Art-Net to test DMX from a laptop.
- Static checks of the systemd units run everywhere (`tests/test_units.py`): the ordering-cycle bug that stopped the player at boot could not be seen in a container.
- Risky features got an independent read-only review; every finding was fixed with a test that reproduces it. Keep doing that for anything touching root, the network, uploads or auth.

## Working agreements

- Open a PR per finished branch. When it is green and ready, add a short comment and merge. Do not rewrite git history.
- Ask before destructive or outward actions (deleting remote branches, moving the repository, overwriting a card). Read the check list for failures before merging: zero failures, not just "nothing pending".
- Anything that reads devices, takes network input or handles filenames from removable media gets an independent read-only review before merging, and every finding gets a test.
- Report outcomes faithfully. Do not claim something works on hardware it has not run on.

## Lessons and limits

- The panel browser test once failed only in CI. The cause was real: the Network form kept typed values only through input events and lost one on a redraw; it now reads the page before every rebuild. When a UI test fails only in CI, add a diagnostic that prints the state at failure and rerun until it shows; do not merge over a red check and do not loosen the assertion.
- The first boot on real hardware found two service bugs no container test could (a systemd ordering cycle that dropped the player's start job, and mpv's owner-only control socket) and four Pi-specific defaults (cheap scaling, HDMI sound, USB media, the cost of a live preview). Read `project-log/LESSONS.md` before trusting a green CI run for anything that touches devices.
- A/B measure performance claims on the real device (A, B, A, B, A) and trust the person watching over a counter.
- A claude session in the cloud could not push tags or delete branches (HTTP 403 from the proxy). Do those from a normal clone or on github.com.
- The session's GitHub scope is fixed to the repo name it started with; after a rename, start new sessions on the new name.
- A claude cloud session cannot reach devices on your LAN. Use the self-hosted runner in DEVICE-TESTING.md or run the test script by hand.
