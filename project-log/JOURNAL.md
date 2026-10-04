<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Journal

Newest entry first. One entry per working session: what was done, what merged, what is open.

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
