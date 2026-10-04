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

## Start here (next session, written 2026-10-01)

State at the end of 2026-10-01: master is green, **no open pull requests**, the test Pi 4 runs current master (0.1.0) with every service active.

**Direction (D36):** the box is a room controller staff can use at Leyline, and the owner's VJ and mapping tool. Weigh new work against that first. The PJLink plan is in [pvj/PROJECTORS.md](pvj/PROJECTORS.md#plan-a-general-pjlink-control-system-d36).

**Morning of 2026-10-02:** the owner will power one Epson projector (an X27 or X39, model to confirm from its label) and connect it to the network. First the restart check below; then, with the owner, the first real PJLink test with the existing module: switch PJLink on in the projector's network menu, note its address and password, add it under System > Projectors, then on, off, mute and state. Record what it answers to `CLSS ?`, `INF1 ?`, `INF2 ?`, `INST ?` and `LAMP ?` (class and inputs decide Phase 1 and 2). Do not claim anything about real projectors before this.

Next, in order:

1. **PJLink Phase 1: built (PR #61, D37), not tested on a real projector.** Identify on add, input selection with labels and the 90 second retry, separate picture and sound mute, lamp hours and warnings in Health, background status; the fake projector is written from the standard; independent review done, all findings fixed with tests. Next for it: the first test with a real Epson (what it answers in standby, how long it is unavailable after power-on, separate mutes, lamp hours), and record what it does in `pvj/PROJECTORS.md`.
2. **PJLink Phase 2:** class 2 volume steps, freeze, input names, signal resolution, "Find projectors" search, status notices.
3. **Groups, scenes and the Room screen are built** (D40, `pvj/ROOM.md`: the Room module with its row under System > Everyday, scenes from the schedule, OSC and MIDI; no DMX, no volume; independent review done, every finding fixed with a test), **not tried on a real projector or in a real room.** Next for it: with the first real Epson, set up "Main wall" and one scene and watch a tap (is 90 seconds enough for the source and the mute after power-on; does All off behave during warm-up); then let a staff member use the Room screen on a phone and write down what confused them.
4. **ISF shader playback is built** (D38, `pvj/SHADERS.md`: generator shaders, ten bundled, and Vibes, the endless rotation), but **not seen on any display and its speed is unmeasured on every board**: first put it on the test Pi, watch each bundled shader, and measure dropped frames at 720 and 1080 lines (A, B, A, B, A). Still open: live coding with a last-good fallback, then **projectM** as an optional module on boards with OpenGL ES 3 (measure on the Pi 4 first).
5. **Painting-wall masks** on the second projector (likely the Pi 3B as a second synced box).
6. **Network notes** for the private NXLX network (a dedicated router, fixed addresses, projectors without internet, staff Wi-Fi or a wall tablet).
7. **Video matrix driver** (serial, TCP, HTTP, OSC command templates) once the owner picks a model; audio routing is undecided.
8. The earlier parity list, now after the above: display mode (owner at the monitor); mapper workflow; wall improvements; schedule with dates; follow-on actions.

**Wi-Fi control (2026-10-04, D41)** is built and reviewed but has never run on a box. First test on the Pi, from the wired port or a keyboard: set the Wi-Fi country, switch the Network module on, Find networks, join a known network, confirm, then a hotspot from a phone, then Wi-Fi off and back. Questions to answer are at the end of the journal entry. Open PRs at the end of 2026-10-04: #64 (room, review findings being fixed), #65 (UX proposal, waits for the owner), #69 (one switch per feature and the Shaders page, D43; waits for the owner to merge).

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

One test Raspberry Pi 4 (Model B Rev 1.5, Debian 13 trixie, wired Ethernet, a 2560x1440 monitor, a USB drive, three USB MIDI controllers, a USB HDMI capture adapter) has run the CI image since 2026-09-30 and is kept on current master. **Verified there:** boot, panel and PIN pairing, uploads, playback (720p and 1080p H.264), HDMI sound, USB automount and playing from it, reboot and crash recovery, DMX over the LAN, the weekly schedule, autostart across reboot and crash, MIDI reading with all three controllers, the screen snapshot, the mapper's output (checked through screenshots; nobody watched the monitor), sync between two players on the box and against a stand-in server (within 5 ms after catching up), remote support against a stand-in hub, the health card, and a signed update through the Updates card followed by a rollback (2026-10-01). Details and numbers are in the journal.

**Not verified on hardware (do not claim it works):**
- Sync and the wall on two real boxes (waiting for the Pi 3B's spare card); a real support server (VPS); swapping USB sticks while running; copy from USB on the box.
- MIDI Learn with the real controllers; the PIN on screen on a fresh box; streams with a real source; HEVC, 4K, 1080p60; 24 fps judder; the second HDMI port; a real projector.
- Shaders and Vibes: never seen on a display, no speed measured on any board, run only on a software GPU in CI; its MIDI and DMX controls only against fakes.
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
