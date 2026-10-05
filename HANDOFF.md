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

## Start here (next session, written 2026-10-04, midday)

The owner is moving to a new computer, and the session that wrote this was cut off by usage limits several times. Everything that matters is on GitHub; nothing is only on the old Mac except the items under "On the old Mac only".

**State:** master is at the merge of #69. The test Pi 4 runs exactly that (deployed over SSH on 2026-10-04, files checked by hash, all four services active). It is still on the wired network at 192.168.0.169; Wi-Fi is not joined.

**Merged on 2026-10-03 and 04, all on the Pi:** PJLink Phase 1 (#61, D37), box care (#62, D39), Shaders and Vibes with MIDI and DMX control (#63, D38), two CI flakes (#66), Wi-Fi control (#68, D41, from a cloud session), the System index with a page per item (#67, D42), the Room screen with groups and scenes (#64, D40), one switch per feature and the Shaders page with the big Vibes button on Live (#69, D43). Each risky one had an independent read-only review with every finding fixed.

**Open pull requests (all unfinished, none merged):**

| PR | Branch | State | What is left |
| --- | --- | --- | --- |
| #72 | `shader-engine` | Draft. One finished commit (three small fixes), then a "work in progress" commit saved at the cut-off: untested, may not even import. | The whole brief: per-board default detail (540 lines on a Pi 4), measured cost classes, every ISF input type adjustable, changes that keep TIME and never flash, speed, presets, rotation sets, MIDI "shader control" knobs, a guard for heavy shaders. Read the diff of the last commit before building on it. |
| #71 | `run-folder` | Draft. One finished commit (runtime paths in one module), then a work-in-progress commit across 18 files, untested. | A security fix: units with different users share `RuntimeDirectory=pvj`, so /run/pvj (the root network helper's socket, the PIN file, the player socket) is owned by whichever service started last (seen on the Pi: pvj-player after a player restart, pvj-web after a reinstall). Finish the per-service folders under a root-owned parent, the upgrade path in install.sh, tests, the hardware checklist; then an independent review before merging (it touches root helpers). |
| #70 | `isf-library` | Draft, nearly done: a pack of ten Vidvox ISF-Files generators, packs in the library, translator gap fixes, a survey, D44 and the log in the PR. 2 of 20 checks were failing at the cut-off; not looked at yet. | Read the failures, fix the cause, then an independent review of the licence handling and the translator changes before merging. Its report (licence finding, survey numbers, what would unlock the rest) was never delivered: read the PR body and `pvj/SHADERS.md` on the branch. |
| #65 | `ux-proposal` | Open, proposal only, do not merge as is. | The click-through prototype (Panel UX Proposal, https://claude.ai/artifact/NwPErwtYzJb6WTaAj9UqKj) and a written spec for Live, Media, Mix and the Shaders page under tools/. Its agent was cut off before reporting; the owner said "excellent" to the first cut. `tools/panel-playground/src/panel.css` is out of date against app.css. |

**The owner's direction for the panel (2026-10-03 and 04), in their words:** "the way users interact with each module doesn't seem intuitive or easy to use"; "no telling the user to go to a different page to find the controls"; "don't be shy about really making the control panels engaging and userfriendly, the panel design and layout and pages should be organized in a logical fashion"; "someone doing more intense work will have a laptop, usually"; "a more robust shader playback and control system. i want to have more shaders available to perform with or have as auto-playing vibes"; "don't forget about ISF shaders (https://github.com/Vidvox/ISF-Files)"; and every screen should get "the same audit and design treatment". Approved and built: one switch per feature, the Shaders page, the System index. Approved, built and in pull request #75 (not merged; its independent security review is done and every finding is fixed with a test, see the journal; merging is the owner's or the coordinator's call): an Edit action for a projector (`{"edit": ...}` in `POST /api/projectors`, and an Edit form on the Projectors page), and guest codes that a presenter can make, show on the room screen and end (D48; `pvj/api.py` "join codes and access on the display", `tests/test_join.py` `PresenterGuestCodeTest`). The owner also wants to run the box on Wi-Fi and use the wired port for a projector: join Wi-Fi from System > Network while the cable is still in, reopen the panel over Wi-Fi, then give the wired port 192.168.10.1/24 with no router and the projector 192.168.10.2. Not done; the Wi-Fi feature has never run on hardware.

**Controller profiles (#77, D49, open):** a nanoKONTROL2, MIDI Mix or Launchpad Mini is recognised when plugged in and works with a built-in layout, drawn on the MIDI page. Not tried on the real controllers: the owner's two-minute check is in `pvj/MIDI.md`. No lights.

**The System audit is built in pull request #78 (D50, open, not merged, never on a box):** one set of patterns for every System page, a question in place before every Remove and power action (no browser dialogs left), the Projectors page with one power button per projector, the Schedule with edit and day shortcuts, labels and Save changes on the rest, an idle snapshot that says "Nothing is on the screen right now.", two columns on a laptop, and the mock-up export of every page for Figma (`docs/mockups/README.md`). Left from it: the MIDI teach flow, then Live, Media and Mix from the proposal's spec. **What the audit asked for, as it was written before #78:** the Projectors page (one state-aware power button per projector, readable input names, "Name the inputs"), a confirm on every Remove, a visible label on every field and readable helper text (the small-caps `.k` style is used for whole sentences), Schedule (sorted, edit, day shortcuts, the box's time zone), People and codes (one vocabulary for roles and a chosen duration are in #75; a confirm on Remove for a paired device is not), the MIDI teach flow and a DMX channel table, then Live, Media and Mix from the proposal's spec. The Shaders page is now the instrument for the shader engine (D47): its controls send values while dragged and do not end Vibes.

**How to work here (learned the hard way on 2026-10-04):** three or four agents at once hit the usage limit four times in one day and each time killed every agent mid-task. Run one or two, have them open the pull request after the first commit and push after every step, and expect to resume them from the branch. The owner allows merging reviewed, green pull requests and deploying to the test Pi over SSH without asking (no new images); say when a deploy happened, since it restarts the panel and the player.

**On the old Mac only (not in git):** the SSH key that the Pi accepts (`~/.ssh/id_ed25519`; on a new computer make a new key and `ssh-copy-id nxlx@nxlx-mastercontrol.local`), the staged sudo password on the Pi (`/tmp/.pw`, gone at its next restart), the shader measurement screenshots and raw data (`~/nxlx-shader-measure-2026-10-04`, about 5 MB, and the second run's in `/tmp/nxlx-measure.FCbYZw`, about 35 MB with its snapshots and contact sheets, which macOS will empty in time: move it if it is wanted; the numbers of both are in the journal), and Claude's memory notes for this project (`~/.claude/projects/-Users-anthony-nxlx-mastercontrol/memory`; their substance is in this file and the project log).

Next, in order:

1. **PJLink Phase 1: built (PR #61, D37), not tested on a real projector.** Identify on add, input selection with labels and the 90 second retry, separate picture and sound mute, lamp hours and warnings in Health, background status; the fake projector is written from the standard; independent review done, all findings fixed with tests. Next for it: the first test with a real Epson (what it answers in standby, how long it is unavailable after power-on, separate mutes, lamp hours), and record what it does in `pvj/PROJECTORS.md`.
2. **PJLink Phase 2:** class 2 volume steps, freeze, input names, signal resolution, "Find projectors" search, status notices.
3. **Groups, scenes and the Room screen are built** (D40, `pvj/ROOM.md`: the Room module with its row under System > Everyday, scenes from the schedule, OSC and MIDI; no DMX, no volume; independent review done, every finding fixed with a test), **not tried on a real projector or in a real room.** Next for it: with the first real Epson, set up "Main wall" and one scene and watch a tap (is 90 seconds enough for the source and the mute after power-on; does All off behave during warm-up); then let a staff member use the Room screen on a phone and write down what confused them.
4. **ISF shader playback is built** (D38, `pvj/SHADERS.md`: generator shaders, 47 bundled, and Vibes, the endless rotation) and **all 47 were measured on the test Pi 4 on 2026-10-05, at 540 and 720 lines, through the shader engine** (D46, D51; pull request #79; the table and what it means are in `pvj/SHADERS.md`, the method and everything else in the journal; judged from snapshots and the player's counters, nobody has watched the monitor itself). The GPU took every one and every picture was right. 33 are light, 11 medium (they hold 30 frames a second at 540 lines, the Pi 4's default, and not at 720), 3 heavy (nxlx-drift, nxlx-nebula, isf-ridgelines); the default sets did not have to change. **The engine ran on hardware in the same run:** live values of every input type (the new picture within about 0.35 seconds, no frames dropped by a change), speed with continuous TIME (the carrier that counts its frames reads correctly on V3D), a preset, Previous and Next, Vibes on both sets, the guard. **What the run left to do:** (a) the test box still has 720 lines saved from the first version's default, where eight of Ambient's shaders drop frames: set Picture detail to 540 on it; (b) its shader settings are now saved in the engine's form with both sets written out, so a later change of the default sets will not reach it by itself (remove the `shaders` section of its settings file with the panel stopped, or edit the sets on the page, if that is wanted); (c) the guard lets a shader that drops frames in bursts stay (nxlx-lantern at 720 lines): judge the rate over the whole window (`Guard.sample`); (d) every number is from a 75 Hz monitor, where 30 pictures a second fit badly: measure on a 60 Hz projector before trusting the classes there; (e) the small findings listed in the journal entry. The Shaders page shows all of it (D47: controls by type, Speed with Freeze, presets, sets, the load, MIDI buttons beside the controls, keys on a laptop, a strip on Live); **it has run only in the browser test, never in a browser against the box**: on the Pi, drag each kind of control and watch the screen for a hitch (the run sent the same requests from a script and saw none in snapshots), teach a knob with a real controller, and let a staff member start Vibes with two sets present. Still open: live coding with a last-good fallback, then **projectM** as an optional module on boards with OpenGL ES 3 (measure on the Pi 4 first).
5. **Painting-wall masks** on the second projector (likely the Pi 3B as a second synced box).
6. **Network notes** for the private NXLX network (a dedicated router, fixed addresses, projectors without internet, staff Wi-Fi or a wall tablet).
7. **Video matrix driver** (serial, TCP, HTTP, OSC command templates) once the owner picks a model; audio routing is undecided.
8. The earlier parity list, now after the above: display mode (owner at the monitor); mapper workflow; wall improvements; schedule with dates; follow-on actions.

**Wi-Fi control (2026-10-04, D41)** is built and reviewed but has never run on a box. First test on the Pi, from the wired port or a keyboard: set the Wi-Fi country, switch the Network module on, Find networks, join a known network, confirm, then a hotspot from a phone, then Wi-Fi off and back. Questions to answer are at the end of the journal entry. See "Open pull requests" above for what is in flight.

**Runtime folders (2026-10-04, D45, PR #71)**: each service has its own folder under a root-owned `/run/pvj` (paths in `pvj/paths.py`). Never run on a box. Before anything else is installed on the Pi after this merges, run the "Runtime folders" checklist in `tools/DEVICE-TESTING.md` (R1 is the upgrade over the running older version) and keep the output.

Small follow-ups: a factory reset cannot remove the root-only update backups in `/var/lib/pvj/backups` (they hold the old devices and passwords, and `pvj-update rollback` after a reset brings them back); it needs a pvj-sysd action. Box care (PR #62, D39) has not been run on the Pi: export and import, the diagnostics file (does it say the log is not readable), a factory reset with the PIN screen returning. Check after the Pi's next restart that `journalctl --list-boots` shows more than one boot (the persistent log, D35, was installed but not yet seen across a restart).

Owner's open items: a layout board at https://claude.ai/artifact/Bc24QHaMhyeS3eZfFyamLJ (press Save, then ask Claude to read it back) and editable mock-ups (SVG, layered PSD, PNG) in `docs/mockups/current/` (not in git; CI artifact `ui-mockups`). The design playground (D34, `tools/panel-playground`) has a preview at https://claude.ai/artifact/DYUr95vVi1jJyGquxhqZTu. A Pi 3B for two-box sync tests is waiting for a spare SD card (never touch its current card).

## What exists

The System screen is an index of rows in three groups with one page per row (D42): a module is switched on its own page (System > Projectors, System > Shaders and Vibes and so on), and each row shows a state chip. The page switch is the only switch for a feature (D43): for DMX, MIDI and the schedule it turns on the module and then the feature's own flag, for OSC and Remote support it is the flag. Everything about shaders is on the Shaders and Vibes page (also the Shaders link next to the big Vibes button on Live); nothing of it is on Mix. No page points to another screen for its controls: the Shaders page has the MIDI teach rows and the DMX channel, System > Projection mapping has the mapping card, System > Room has the Room screen's cards. Next for it: see both on a phone and on the Pi (neither has run outside CI), and sync `tools/panel-playground/src/panel.css`.

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
| Projectors (PJLink class 1: power, input with labels, picture and sound mute, live status, lamp hours, warnings; no real projector tested) | `pvj/PROJECTORS.md` | off (beta) |
| Room: groups of projectors, scenes tapped once (power, source, mutes, what the box plays), a Room screen for staff, scenes from the schedule, OSC and MIDI (D40; no real projector tested) | `pvj/ROOM.md` | off (beta) |
| Projection mapper (quads, triangles, grids) | `pvj/MAPPER.md` | off (beta) |
| Shaders and Vibes: ISF generator shaders, ten bundled, an endless rotation from one tap, the API, autostart, the schedule, OSC, MIDI and DMX | `pvj/SHADERS.md` | off (beta) |
| Multi-box sync and video wall | `pvj/SYNC.md` | off (beta) |
| Streams and live input (SRT, RTSP, RTMP, USB capture) | `pvj/STREAMS.md` | off (beta) |
| DMX (Art-Net, sACN), MIDI controllers | `pvj/DMX.md`, `pvj/MIDI.md` | off (beta) |
| Network settings with confirm-or-revert: wired, and Wi-Fi (join a network, own hotspot, Wi-Fi off, find networks; D41) | `pvj/NETWORK.md` | off (beta) |
| System log kept across restarts, 64 MB cap (D35) | `docs/MANUAL.md` | on |

Docs for people: [docs/MANUAL.md](docs/MANUAL.md) and pictures in [docs/UI.md](docs/UI.md) (made by `tests/ui/screenshots.js`, which with `MOCKUPS` set also writes the editable mock-ups, `tests/ui/mockups.js`). Module manifests: `pvj/modules.d`.

## What has been run on real hardware, and what has not

One test Raspberry Pi 4 (Model B Rev 1.5, Debian 13 trixie, wired Ethernet, a 2560x1440 monitor, a USB drive, three USB MIDI controllers, a USB HDMI capture adapter) has run the CI image since 2026-09-30 and is kept on current master. **Verified there:** boot, panel and PIN pairing, uploads, playback (720p and 1080p H.264), HDMI sound, USB automount and playing from it, reboot and crash recovery, DMX over the LAN, the weekly schedule, autostart across reboot and crash, MIDI reading with all three controllers, the screen snapshot, the mapper's output (checked through screenshots; nobody watched the monitor), sync between two players on the box and against a stand-in server (within 5 ms after catching up), remote support against a stand-in hub, the health card, a signed update through the Updates card followed by a rollback (2026-10-01), and shaders: all 47 bundled ones drawn and timed at 540 and 720 lines, live value changes, the speed control, presets, Vibes with its dip and the guard (2026-10-05, from snapshots and the player's counters over SSH). Details and numbers are in the journal.

**Not verified on hardware (do not claim it works):**
- Sync and the wall on two real boxes (waiting for the Pi 3B's spare card); a real support server (VPS); swapping USB sticks while running; copy from USB on the box.
- MIDI Learn with the real controllers; the PIN on screen on a fresh box; streams with a real source; HEVC, 4K, 1080p60; 24 fps judder; the second HDMI port; a real projector.
- Shaders and Vibes: nobody has watched it on a display (both Pi 4 runs were judged from snapshots and the player's counters, so a single black frame or a stutter that drops no frame would not have been seen); its MIDI, OSC and DMX controls only against fakes; the Shaders page only in the browser test; 360 and 1080 lines, a 60 Hz screen and a projector not measured.
- The read-only root (`pvj-rootfs`), the Network module and its Wi-Fi control (test with a keyboard and monitor on the box, never over SSH or Wi-Fi on the only link), TouchOSC.
- Pi 3, Pi 5 and x86.

Not built: crossfade (needs a second player), display mode, company (802.1X) Wi-Fi and setting the Wi-Fi country, updates from the network, NDI, AES67/Dante, presenter, importing old mapper files, MIDI controller profiles and feedback, custom DMX layouts.

Known limits: the panel cannot restart a wedged mpv (unprivileged by design). Merged branches on GitHub are not deleted (ask the owner). GitHub ruleset "Protect master" requires 9 checks and pull requests; do not change it without asking.

## Working on the test Pi

- SSH as `nxlx@nxlx-mastercontrol.local` (or 192.168.0.169). A session key is set up per session; ask the owner to `ssh-copy-id` a new one.
- `sudo` needs the owner's password, which is never written into the repo, memory or chat. When needed, the owner stages it on the Pi with `read -rsp "password: " p; printf '%s' "$p" > /tmp/.pw; chmod 600 /tmp/.pw; unset p; exit` (gone at reboot). Use it only as `S(){ cat /tmp/.pw | sudo -k -S -p "" "$@"; }`, never pipe data into an `S` command (see LESSONS), and delete `/tmp/.pw` when done.
- Deploy master: `git archive origin/master`, copy it over, run `install/install.sh --offline` as root. Settings have a schema number; never put older code on a box with newer settings.

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
