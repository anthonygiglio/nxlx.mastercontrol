<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Testing on a real board

Everything so far was tested in a container. This lets a real Raspberry Pi (or mini PC) run the tests and send the results back, so a claude session or you can read them.

## What runs

`tools/device-test.sh` collects board details (model, OS, mpv version, temperature, throttling), runs `pvj-selftest` (with a test pattern on the display if you ask), and runs the unit tests with the real mpv. It never changes the network, never installs anything and needs no root. Output goes to a `device-report` folder.

## Option A: run it by hand (no GitHub setup)

On the Pi, in a clone of the repo:

```
tools/device-test.sh            # add PVJ_DEVICE_PLAY=1 in front to also play a test pattern
```

Send me `device-report/system.txt`, `selftest.txt` and the last lines of `unittest.txt`.

## Option B: the Pi as a GitHub Actions runner (I can then start tests myself)

The Pi connects out to GitHub over HTTPS; nothing is opened on your network.

1. Use a **dedicated Pi** with Raspberry Pi OS Lite (64-bit), `git`, `python3` and `mpv` installed. Do not use the Pi that runs your shows.
2. Make an unprivileged user for it, for example `sudo adduser --disabled-password runner`.
3. On GitHub: **Settings, Actions, Runners, New self-hosted runner**, pick Linux and ARM64 (or x64), and follow the commands it shows as the `runner` user. When it asks for labels, add `pvj-device`.
4. Install it as a service: `sudo ./svc.sh install runner` then `sudo ./svc.sh start` (run in the runner folder).
5. On GitHub: **Actions, Device test, Run workflow**. Tick "play" if a screen is attached. The report appears as the `device-report` artifact.

## Option C: test the built image on a Pi (recommended first)

This is the test that matters most, because it is the only one that checks the thing you would actually ship: the image boots, the services start, the panel answers, and each feature works on real hardware. Do this before A or B. Nothing on this list has ever been run on a real board, so expect to find problems and write them down.

**What you need:** a Pi 3, 4 or 5 with a blank-enough SD card (it is erased), Raspberry Pi Imager, a wired connection to the same network as your laptop, and ideally a monitor on the Pi's HDMI. A monitor is not required for SSH tests, but the playback checks need a screen (or at least a way to see the result).

1. **Build or download the image.** Run the "Build image" workflow from the Actions tab (about 35 minutes) and download the `nxlx-mastercontrol-image` artifact. Check the file against `SHA256SUMS` (`shasum -a 256 -c SHA256SUMS`). Always use an image built from the current `master`; an old one lacks newer modules.
2. **Flash it.** In Raspberry Pi Imager choose "Use custom", pick the `.img.xz`, pick the SD card (check the size and name; everything on it is erased), and skip "Edit settings".
3. **Give it a login without a keyboard.** The image has no password and SSH is off. While the card is still in the laptop and the `bootfs` volume is mounted, add two files to it: an empty file called `ssh`, and `userconf.txt` containing one line, `USERNAME:HASH`, where `HASH` comes from `openssl passwd -6` (it asks for the password). Eject, put the card in the Pi, power it on. If it does not show up on the network within about 3 minutes, connect a monitor and keyboard: the first boot then asks for a user name and password itself.
4. **Find it.** Look for a new device on your network with a Raspberry Pi hardware address (`dc:a6:32`, `e4:5f:01`, `d8:3a:dd`, `2c:cf:67`, `88:a2:9e` or `b8:27:eb`), or try `ssh USER@nxlx-mastercontrol.local`. Your router's client list is the surest way.
5. **Put a key on it** so a claude session (or a script) can connect without a password: `ssh-copy-id -o StrictHostKeyChecking=accept-new -i KEY.pub USER@HOST` (run in your own terminal; it asks for the password once). Remove the key from `~/.ssh/authorized_keys` when you are done.

### Checklist on the Pi

Run over SSH. Each line says what "good" looks like. Write down anything else.

| # | Check | Command | Good |
| --- | --- | --- | --- |
| 1 | Board and OS | `cat /proc/device-tree/model; . /etc/os-release; echo $PRETTY_NAME; uname -m` | The model you expect, Trixie, `aarch64` |
| 2 | Services | `systemctl is-active pvj-player pvj-web pvj-netd` | `active` three times (if `pvj-player` is not active and no screen is attached, note the log below) |
| 3 | Logs are quiet | `journalctl -b -u pvj-player -u pvj-web -u pvj-netd --no-pager \| tail -60` | No tracebacks; "listening on 0.0.0.0:80" from `pvj-web` |
| 4 | Panel answers | `curl -s http://localhost/api/hello` | JSON with `"name": "nxlx.mastercontrol"` |
| 5 | Pairing PIN | `sudo pvj-pin` (also shown on the screen) | Four digits |
| 6 | Panel in a browser | open `http://HOST/` on the laptop, pair with the PIN | The Live screen loads; no console errors |
| 7 | Self-test | `sudo -u pvj-player /opt/pvj/current/bin/pvj-selftest` (add `--play` with a screen) | No failures; note board, temperature, throttling |
| 8 | Upload and play | upload a short clip in Media, press Play (screen attached) | The clip plays; Now playing shows it; Blackout and Fade work |
| 9 | Player survives | `sudo systemctl kill -s KILL pvj-player; sleep 3; systemctl is-active pvj-player` | `active` again within a few seconds |
| 10 | Power cut | pull the power while playing, boot again | Boots; the panel and settings are back; no upload leftovers |
| 11 | Read-only root (later) | `sudo pvj-rootfs enable`, reboot | Boots and plays; media on a second disk or USB |
| 12 | USB drive | plug in a stick with a clip | Appears under `/media/usb`; the clip plays from a preset |
| 13 | Device test script | copy the repo (`rsync -a --exclude .git ./ USER@HOST:nxlx/`), then `cd nxlx && tools/device-test.sh` | Exit status 0; send `device-report/system.txt`, `selftest.txt` and the tail of `unittest.txt`. The image has no `git` and no `tests/` folder, so the script needs a copy of the repository |

### Beta modules (switch each on under System > Modules first)

| Module | Test | Good |
| --- | --- | --- |
| DMX | System > DMX: turn on, Art-Net, universe 0, start 1. From the laptop: `tools/artnet-send.py HOST blackout`, then `show`, `opacity=50`, `pad=1`, `stop`, `fade` | Each does what `pvj/DMX.md` says. A second run of the same command right after switching on must not fire from the first frame |
| MIDI | Plug in a USB MIDI controller, `ls /dev/snd/midi*` on the Pi, choose it in System > MIDI controller, turn on. Press pads (notes 36 to 71) | The line shows "Connected" and the last message; pads play. Unplug and replug: it reconnects. `journalctl -u pvj-web` shows no permission errors (the unit needs the `audio` group and `DeviceAllow=char-alsa r`) |
| Streams | Save an address such as `srt://LAPTOP:9000` (send with `ffmpeg` or OBS) or an RTSP camera, press Play | Video appears; the password is hidden in the list. Note the latency and what happens when the source stops |
| Schedule | Check the box clock (System > Schedule shows it), add an entry two minutes ahead, turn the schedule on | It fires once at that minute and shows "Last run"; reboot and confirm nothing fires for old times |
| Network | **Last, with a monitor and keyboard on the Pi**, never over SSH on the only connection. Try a change, confirm, then one you do not confirm | A change you do not confirm reverts by itself. Never test this remotely on the only link |

### The layouts, the levels and the fade button (D75): do these first

**None of this has been on a controller.** The three layouts were drawn afresh on 2026-10-10 after the owner's first report; the numbers each control sends are still from documents. `pvj/MIDI.md` has each layout as a drawing. Before: MIDI on, a clip on pad A1, B1 and C1, something playing, Shaders and Vibes on. Say what you see for each row, also "fine".

| # | Do | See |
| --- | --- | --- |
| K1 | For each of the three controllers: open Setup > MIDI controller and press or move **every control once** | Its box on the card lights up while you do. Note each one that does not, and each one that lights another box: that is a wrong number |
| K2 | Look at each card | The controls that belong together carry one bar of colour, and the zones are named under the drawing. Does the arrangement make sense now? Say which control you looked for in another place |
| K3 | nanoKONTROL2, knob 1 (zoom), with the picture at 100 percent and the knob anywhere | Nothing happens until the knob passes its middle; from there the picture follows. **Does it jump at any point?** |
| K4 | Turn knob 1 slowly through its middle | The picture stays at exactly 100 percent for a short way around the middle (the Mix screen's Size reads 100). Is that rest too wide, too narrow, right? |
| K5 | Turn knob 1 to both ends | 25 percent at the left, 200 at the right. Is the way near the middle fine enough to set a size, and the ends not too abrupt? |
| K6 | The same with knob 2 (position X) and knob 3 (position Y) | Centred at the middle of each knob, to the edge of the screen and beyond at the ends. Do left, right, up and down go the way you expect? (If Y feels upside down, say so: it can be turned round) |
| K7 | MIDI Mix: the same with the middle row's first three knobs | As K3 to K6 |
| K8 | Move the zoom on the nanoKONTROL2, then touch the MIDI Mix's zoom knob | The MIDI Mix's knob does nothing until it meets the picture's size; then it has it, and the nanoKONTROL2's knob waits in its turn |
| K9 | Panel, Live: tap **Fade out** | The picture fades out in 2 seconds; the button reads **Fade in** and flashes about twice a second, without moving anything around it. Readable in both halves of the flash? |
| K10 | Tap it again | The picture fades in; the button is steady while it comes up and reads Fade out afterwards |
| K10a | Tap it, and tap again half way down; then again half way up | Each time the picture turns round from where it is: no jump to black and none to full |
| K11 | Launchpad Mini: press the round button **E** (right side, fifth from the top) | The same fade. While the picture is down E **flashes red** by itself and every other light stays steady. **If other lights flash too, or the pads look wrong after plugging in, say so at once and switch Lights off**: that is the flash mode misbehaving |
| K12 | nanoKONTROL2 with lights on (LED Mode External, L10 below): press **R 7** | The same fade; R 7 flashes while the picture is down, lit while it comes up, dark afterwards |
| K13 | MIDI Mix: hold Solo and press **Mute 5** | The same fade. No light (this row has none); the panel's button flashes |
| K14 | Fade out with a controller, then press Blackout twice, then twice again | After the Blackout is off the picture is back and the fade button is steady; its next press fades out |
| K15 | Launchpad Mini with clips on A1, B1 and C1 | Three colours down the left edge: row 1 amber, row 3 yellow-green, row 5 orange (each bank is two rows of six). Can you tell them apart at Low, Medium and High? The pad that plays is bright green |
| K16 | Launchpad Mini: the two bottom rows and the strip beside the banks | Row 7: Vibes, previous shader, next shader, then effect on / off, previous, next. Row 8: previous clip, back 10 s, forward 10 s, next clip, then shader controls 1 to 4. Beside the banks, two wide: presets 1 to 8. Do the rows read right now? |
| K17 | The quarter turn: M 7 (nanoKONTROL2, over Fade), Rec Arm 8 (MIDI Mix), the fifth round button along the top (Launchpad) | The picture turns a quarter each press, back to upright after four |

### Layers: Geometry on the nanoKONTROL2, and mapping mode (D75)

**Never on a controller or a projector.**

| # | Do | See |
| --- | --- | --- |
| G1 | nanoKONTROL2, a shader on screen: turn knobs 1 to 8 | Each moves one of the shader's first eight controls |
| G2 | Press **M 6** once (lights on, LED Mode External) | M 6 flashes; the card says "Geometry is on" and knobs 1 to 3 read Zoom, Position X, Position Y. Turn knob 1: nothing until it passes the picture's size, then the zoom follows. Knobs 4 to 8 do nothing |
| G3 | Set zoom and position, press M 6 again | M 6 stops flashing. Turn knob 1: the shader's control does NOT jump; it follows again once the knob is back where it stood before G2. Does that wait feel right, or does the knob feel dead? |
| G4 | Press M 6 and leave it for two minutes | It stops flashing by itself and the knobs are the shader's again |
| P1 | Without the switch: press the mapping mode button twice (M 8 on the nanoKONTROL2, the last round button along the top of the Launchpad) | Nothing. The log says controllers may not adjust the mapping |
| P2 | Mix > Projection mapping: switch **Controllers may adjust the mapping** on (full access), add two surfaces, press the button twice | The display shows the outlines, one surface yellow, one corner a pink dot; the button flashes; the card says a controller is in mapping mode |
| P3 | nanoKONTROL2: turn knob 2 and knob 3. Launchpad: the arrow pads on rows 7 and 8 | The pink corner moves a pixel a step. Too fine, too coarse? Press the step (Cycle; the fourth pad of row 8) for 10 and 50 |
| P4 | Next corner, next surface (Marker and Track buttons; the first four pads of row 7). Turn knob 2 a quarter turn in one go, then undo | The marks on the display follow; undo takes the whole turn back, not its last click. Does a second between runs feel right? |
| P5 | Press Blackout twice, Stop, a pad | Each does what it always does and the mode stays on |
| P6 | Press the mode button twice; and once more, enter it and wait three minutes | The outlines leave the display, the mapped picture comes back, the light stops flashing |
| P7 | nanoKONTROL2, a shader on screen, knob 2 low. Enter the mode, turn knob 2 far up (it nudges), leave the mode, touch knob 2 | The shader's second control does NOT jump; it follows again once the knob is back where it stood before the mode. The same when the mode ends by itself, and when it is ended from OSC or by the owner's switch while the hand is on the knob |
| P8 | In the mode, spin knob 2 back and forth for ten seconds with `journalctl -u pvj-web -f` open | The corner follows the knob without a wait you can feel and ends where the knob ended; nothing in the log. Pull the power a second after the last turn: after the start the corner is where it ended |
| P9 | On a full-access phone: Edit on the display on, choose the second surface and its third corner. Enter the mode from the controller, choose another surface, nudge, leave | The card says a controller is adjusting the mapping; afterwards Edit on the display is still on and the second surface and its third corner are chosen again |
| P10 | TouchOSC: a momentary button on `/pvj/mapping/mode`, another on `/pvj/mapping/mode/off`, a third on `/pvj/mapping/mode/toggle` | The first enters on the press and the mode stays on when the finger comes up; the second leaves; the third does both, one press each |
| P11 | Launchpad: with "Presenter codes from a controller" on, hold the third round button along the top for 4 seconds; then hold pad 12 of bank C and the two pads right of it | The code comes from the round button only; the pads right of bank C do nothing |
| P12 | nanoKONTROL2, a shader on screen. Enter the mode, nudge a corner with knob 2, leave. Turn knob 2 a long way as the shader's knob. Enter the mode again and touch knob 2 lightly | The corner does NOT leap by what the knob was turned in between: the first touch moves nothing, then it moves a step for a step. The same the third time |

### TouchOSC: what the owner's layout can now be wired to (D75)

The layout open in the owner's TouchOSC editor (`touchosc/mastercontrol_tOSC-layout_0001.tosc`: pages Room, Live, Mix, Shaders and Vibes, Tools, and a strip) was built on the addresses the box had. Its controls were read on 2026-10-10 (read only, nothing in it was changed); each control is named after the address it sends. **Nothing here was sent from TouchOSC, and the box still sends nothing back**, so no control can show the box's state.

**Controls that are there and can be wired to something better:**

| Where | Control in the layout | Now | Can be |
| --- | --- | --- | --- |
| Strip | `pvj/fadeout` | fades out only; there is no way back from the layout | `/pvj/fade`, sending 1 on press: out, then in at the next press, and up out of a Blackout |
| Mix | `fliph`, `flipv` | the old names: each press switches the mirror over, so the layout cannot know which way it is | `/pvj/flip/h`, `/pvj/flip/v` as two switches sending 1 and 0 |
| Tools | `overlay`, `stopoverlay` | two buttons, the old names | one switch on `/pvj/overlay` sending 1 and 0 (the two old ones keep working) |

**Controls the layout does not have yet and the box now takes:**

| Page | Add | Address | Send |
| --- | --- | --- | --- |
| Mix | a sixth fader under `pvj/position` | `/pvj/position/y` | -100 to 100 (`pvj/position` stays X) |
| Mix | Transition (a radio or a row of buttons) | `/pvj/transition` | the name as a string: `cut`, `dip`, `crossfade`, `wipe-from-left` ... |
| Mix | Duration fader | `/pvj/transition/duration` | 0.1 to 10 |
| Mix | Effect on / off, previous, next | `/pvj/effect`, `/pvj/effect/prev`, `/pvj/effect/next` | 1 and 0; 1 on press |
| Mix | Effect amount fader, effect knobs 1 to 8 | `/pvj/effect/amount`, `/pvj/effect/control/1` ... `/8` | 0 to 100; 0 to 1 |
| Strip or Live | Prev and Next clip (beside the two `pvj/seek` buttons) | `/pvj/clip/prev`, `/pvj/clip/next` | 1 on press |
| Strip | Fade in, if wanted as its own button | `/pvj/fadein` | no argument, or the seconds |
| Shaders and Vibes | Previous, next shader (the page has `pvj/vibes/previous` and `/next`, which only step Vibes) | `/pvj/shader/prev`, `/pvj/shader/next` | 1 on press |
| Shaders and Vibes | Speed, colour turn, brightness | `/pvj/shader/speed`, `/hue`, `/brightness` | 0 to 4, -180 to 180, 0 to 2 |
| Shaders and Vibes | Shader knobs 1 to 8, presets 1 to 8 | `/pvj/shader/control/<n>`, `/pvj/shader/preset/<n>` | 0 to 1; 1 on press |
| Shaders and Vibes | A button per shader | `/pvj/shader` | the shader's name as a string (`silk`) |
| Shaders and Vibes | How long each stays | `/pvj/vibes/dwell` | 15 to 3600 seconds |

Left as they are: the Room page (`pvj/scene/1` to `8`, `pvj/group/...`), Live's pads and banks, `pvj/vibes`, `pvj/vibes/set`, the Mix faders `pvj/opacity`, `pvj/size`, `pvj/position`, `pvj/speed`, `pvj/volume`, `pvj/rotate`, `pvj/loop`, `pvj/mute`, `pvj/mix/reset`, the Tools page's test screen and tones, and the strip's `pvj/seek`, `pvj/pause` (Freeze is pause), `pvj/stop` and `pvj/blackout`.

**The layout's `pvj/fadeout` button as it is:** a plain TouchOSC button sends 1 on press and 0 on release. The box used to read those as seconds (a fade of one second, then a refused 0 in its log); now the 1 is a press with the panel's 2 seconds and the 0 does nothing. For another time use `/pvj/fadeout/seconds` and `/pvj/fadein/seconds`.

To try: O1, send each new address from the layout with a clip playing and look at the screen and the panel; O2, the fade button twice (out, in), then Blackout from the panel and the fade button once (the picture comes up); O3, `/pvj/shader` with a name while Vibes runs (Vibes ends, that shader shows); O4, `journalctl -u pvj-web | grep osc` for a refused value.

### Controller lights (D53)

**The rows L1, L4 and L5 below describe the Launchpad's first layout.** With D75: after plugging in, pad 1 is amber if A1 holds a clip, E (the fade button) and H (Blackout) are dim red, G (Stop) lights once something plays; Vibes is the first pad of row 7, and Freeze is F on the right.

**Nothing of this has been seen on a real controller.** The messages come from Novation's document for the Launchpad S (that the Mini takes the same ones is not confirmed by Novation), from Korg's guide (nanoKONTROL2: External LED mode, and only with the factory On and Off values and channel) and from a secondary source alone (MIDI Mix). Five minutes; please say what you see at each step, also when it is right.

Before: the box runs a version with D53 installed **by the installer** (an update from the panel or `install/install.sh --offline`), MIDI is on, a clip is on pad A1, and Shaders and Vibes is on.

| # | Do | See on the controller | See on its card |
| --- | --- | --- | --- |
| L1 | Plug in the Launchpad Mini | Within about three seconds, seven lights and no more: pad 1 (top left of the grid) amber; along the top the third (Vibes) and the seventh (fade in) amber and the eighth (fade out) dim red; on the right E and F amber (the two Vibes sets) and H (bottom) dim red. Everything else dark. Dim, not glaring | "Lights on.", the switch Lights on, Brightness Low |
| L2 | If the card says "Lights need the box's installer to run once." | Nothing lights | Run the installer once, then unplug and plug in. Tell us: this is the older service file |
| L3 | Press **Test lights** | Every light comes on in turn, the top row first, then row by row, left to right; all 80 stay on for a second; then back to L1. Note any that stay dark or come on out of turn, and the colours you see (amber, green, red) | "Testing: each light comes on in turn." |
| L4 | Press pad 1 | It plays, and pad 1 turns green. The sixth round button along the top (pause) turns green and G (right, second from the bottom: stop) turns dim red | The ring on pad 1 is filled |
| L5 | Press the third round button along the top (Vibes) | Vibes starts: that button and E turn green; the first two along the top (previous and next shader) and the whole bottom row of the grid turn amber; pad 1 should go back to amber (the clip no longer plays) | |
| L6 | Press H twice within a second (blackout) | The screen goes black and H turns **full red**. Twice again: back to dim red | |
| L7 | Brightness: Medium, then High | Green gets brighter at Medium; amber and dim red too at High | |
| L8 | Unplug the Launchpad, wait five seconds, plug it in | The same lights as before come back by themselves | The card goes and comes back |
| L9 | Switch Lights off | Every light goes dark | "Lights off." |
| L10a | nanoKONTROL2 as it is, **before** the editor step (LED Mode Internal, the factory setting): switch Lights on on its card and press Test lights | Nothing the box does shows: a button lights only while you hold it (or toggles), as always. That is what Internal mode looks like; it is not a fault | "Lights on." (the box cannot tell) |
| L10 | nanoKONTROL2: now set **LED Mode to External** in the KORG KONTROL Editor (Common) and check there that the buttons have On Value 127, Off Value 0 and the MIDI channel is 1 (the factory values: the box's messages are right only with those); write the scene; then switch Lights on on its card and press Test lights | S, M, R of each strip, Cycle, Rewind, Forward, Stop, Play, Rec come on in turn (30). Tell us if Track or Marker buttons have a light too. Afterwards, with nothing playing, exactly two are lit: S 1 (pad A1 holds a clip) and Stop. Play pad A1 with S 1: S 1 pulses slowly, Play is lit, Stop goes dark. Press R 8 twice: R 8 is lit while the screen is black | "Lights on." |
| L11 | MIDI Mix: switch Lights on on its card, press Test lights | Mute 1 to 8, Rec Arm 1 to 8, Bank Left and Bank Right come on in turn (18). Afterwards exactly two are lit: Mute 1 (pad A1 holds a clip) and Bank Left (bank A). Play pad A1 with Mute 1: it pulses slowly. Press Bank Right: both bank buttons are lit (bank B) and Mute 1 goes dark | "Lights on." |
| L12 | With lights on on every controller: `sudo systemctl stop pvj-web`, look, then `sudo systemctl start pvj-web` | Every light on every controller goes dark within about two seconds of the stop, and comes back after the start | The panel is away meanwhile |
| L13 | `journalctl -u pvj-web -b \| grep -i -e light -e midi` | | No permission errors, no line that repeats |

### A pairing code from a controller (D61)

**Never run on a box or with a real controller.** Before: MIDI is on, a Launchpad Mini is plugged in (or give "Show a one-time guest code" to a button of another controller on its card), a screen is on the box, one phone is paired with full access and a second phone is not paired.

| # | Do | See on the box's display | See on the paired phone (System > People and codes) |
| --- | --- | --- | --- |
| C1 | With "A code from a controller" off: hold the third round button along the top (Top 3) for 4 seconds, let go | Nothing | "No code has been shown from a controller since the box started." |
| C2 | Switch "Guest codes from a controller" on (answer the question). Tap the button | Nothing | The second switch appears |
| C3 | Hold the button for 4 seconds, let go | The address, "One-time guest code" with 6 digits, a QR code, "Hides in ... s" counting down. Note whether it reads well from where you stand | Within 5 seconds: "A one-time guest code is on the box's display now", and End this code. No digits |
| C4 | On the second phone open the address, type the code | The code leaves the display within a second or two | "The last one: a guest code, just now, used by the device ..." and the phone is in Paired devices as Guest (can watch) (D80: it was Presenter before) |
| C5 | Hold again, then press the pad once | The code appears, then goes at the press | "hidden at the controller" |
| C6 | Hold again, wait 2 minutes | The code goes by itself | "ran out unused" |
| C7 | Hold the pad for 15 seconds, let go. Then lay something on the pad for a minute | Nothing either time | |
| C8 | Hold again, then press End this code on the phone | The code goes | "ended from the panel" |
| C9 | `journalctl -u pvj-web -b \| grep "controller code"` on the Pi | | A line for each code shown and each refusal, and no line with 6 digits in it |
| C10 | Other controllers: give the action to a button on the card, repeat C3. On a nanoKONTROL2 say whether the button needs holding or two presses | | |

### The owner PIN from the panel, and Log out (D77)

**Never run on a box.** Before: a phone paired with the PIN (owner) and a second phone joined with a guest code; both on System > People and codes (the owner) and System > About and power (the guest).

| # | Do | See on the phone | See on the Pi |
| --- | --- | --- | --- |
| P1 | Owner: press **Show** under "The Owner (everything) PIN"; then **Copy**; then leave the page and come back | Four digits in the field, gone by themselves after about 12 seconds; "The PIN is copied" (or "Select the PIN and copy it"); the field empty again on return. Compare the digits with `sudo pvj-pin` | `journalctl -u pvj-web -b \| grep "owner PIN"`: one line per press with the device's id and name and no four digits of a PIN in it |
| P2 | Guest: About and power > **Log out**, answer Log out; reload the page. Then owner: Log out on your own row under Paired devices, read the question, press Stay; then Log out and answer Log out | The guest is on the pairing screen after the reload, and gone from the owner's list. The owner's question names the PIN (last full-access device) or says "You will need the PIN"; after the real Log out the pairing screen, and the old PIN pairs again | When no device is left and nothing plays: the PIN screen on the display within a few seconds; `journalctl -u pvj-web -b \| grep "logged out"` has a line per logout |

### Owner recovery: codes and the stick (D81)

**Never run on a box, never met a real USB stick.** Before: a phone paired with the PIN (owner), a second phone with nothing, and a clip playing on the Pi's screen.

| # | Do | See on the phone | See on the Pi |
| --- | --- | --- | --- |
| R1 | Owner: People and codes, last card, **Make recovery codes**; **Copy**; **Done, I kept them** | Eight codes like `XXXX-XXXX-XXXX`, then "8 of 8 codes left · made (now) by (your device)"; the codes nowhere on the card | `journalctl -u pvj-web -b \| grep recovery`: "8 recovery codes made by device ..."; `sudo grep -c hashes /var/lib/pvj/settings.json` is 1 and the codes themselves are not in the file |
| R2 | Second phone: open the panel, **I have a recovery code**, type one code in lower case with spaces, **Pair with recovery code**; then try the same code on a third browser | The second phone is an owner (System shows everything); the third try says "wrong or was used already"; the owner's card says "A recovery code was used by ... ; 7 left" | one line "a recovery code was used by device ... ; 7 left", never the code |
| R3 | With the clip playing: put in a stick that holds an empty file `pvj-recover` at its top (and a clip or two) | On the second phone (logged out first) the new PIN pairs it as an owner; the owner's card shows "The PIN was shown on the box's screen at ..." and, under Clips, the stick's clips still list | Within 3 s the screen shows "Recovery stick found", the panel address and "Owner PIN  dddd" over the clip; it goes the moment the PIN is used or the stick comes out; `sudo pvj-pin` then prints a different PIN |
| R4 | Switch **A USB stick at the box shows the PIN** off; put the stick in again; then leave the stick in and reboot | Nothing on the card but the switch off | Nothing on the screen; the journal says "switched off; nothing shown". After the reboot with the switch on again and the stick in: one PIN screen for two minutes, then a new PIN |
| R5 | With a certificate installed and **Owner access only over the secure connection** on: on the second phone over `http://`, **I have a recovery code** with a good code | "This box takes owner codes over https:// only. Open https://... and type the code there"; the code is not spent (the owner's card still says 8 left); over `https://` the same code pairs the phone as an owner | One journal line for the use over https, none for the refusal over http |
| R6 | After R3, on the Pi's screen the moment the PIN is used; then People and codes on the owner's phone | The device list says "came in with the PIN from a recovery stick" beside the second phone; the card says "The PIN shown for a recovery stick was used by ..." | "The PIN was used by <the phone's name>" for about ten seconds, then the clip alone |
| R7 | Leave the stick in and `sudo systemctl restart pvj-web` within an hour of R3; then take it out and put it back | Nothing new on the card for the restart; a new notice for the re-insertion | Nothing on the screen after the restart (the journal: "is in at start ... nothing shown"); the PIN screen again when the stick goes out and back in |
| R8 | On a phone over `http://` with the owner-only switch on: **I have a recovery code** | No code field: "Open https://..." with one sentence why; the link opens the secure page where the field is | Nothing (the code never left the phone) |

### Secure connection (D79)

**Never run on a box or a phone.** You need your computer with `openssl` (a Mac has it), the box reachable at `http://nxlx-mastercontrol.local/` with full access, and the devices below. Everything is in `docs/HTTPS.md`; this is the order to try it in.

| # | Do | See |
| --- | --- | --- |
| H1 | On your computer: `python3 tools/boxcert.py make-root` (a passphrase twice) | `~/nxlx-root-ca/root.key` and `root.pem` exist; a second `make-root` refuses. Back the folder up now |
| H2 | On the box's panel over http://, System > Secure connection: check the names, press **Make the request**, then **Download the request** | The row says "HTTP only · request made"; the file `nxlx-mastercontrol.csr` is on your computer; on the box `sudo ls -la /var/lib/pvj/tls` shows the folder `drwx------ pvj-web` and `key.pem` `-rw-------` |
| H3 | `python3 tools/boxcert.py sign ~/Downloads/nxlx-mastercontrol.csr`, then on the page **Upload the certificate...** with the file it wrote | "Certificate in use for nxlx-mastercontrol.local, <addresses>, until <a date about 13 months away>"; the row says "On · until ..."; `journalctl -u pvj-web -b \| grep certificate` has one line without any key material |
| H4 | iPhone: **Download the root certificate**, follow the four steps on the page (the profile, then Settings > General > About > Certificate Trust Settings), then **Does this device trust the box?**, then **Open https://...** and pair with the PIN | "Yes"; Safari shows the lock with no warning at `https://nxlx-mastercontrol.local/`; the pairing works; the page over https says "You are reading this over https://" |
| H5 | The same on an iPad, an Android phone (Chrome), a Mac (Safari and Chrome; Firefox needs its own import), and Windows if you have one | Each says Yes and opens https:// without a warning. Write down which words on the page did not match what the device showed |
| H6 | Over https:// on the iPhone, switch **Owner access only over the secure connection** on. Then on any device open `http://nxlx-mastercontrol.local/` and try to pair with the PIN; then try a guest code there | The PIN is refused with the https address in the message; the guest code works; an owner device that is still on http:// sees the notice with the link at the top |
| H7 | On the Mac over http://, with the switch on, try System > People and codes > Show | Refused: the notice points at https:// |
| H8 | A short-lived certificate: `boxcert.py sign <the request> --days 1 --out short.pem`, upload it, wait a day (or set a phone's clock two days ahead) | The row turns to "Certificate ends ..." then "Certificate ran out ..."; the phone refuses https:// with its own warning; http:// still opens the page, which says what is wrong; **Back to the one before** restores the long one |
| H9 | Backup and reset > Reset to factory settings (clips kept) | The confirm says the key and certificate go; afterwards the box is HTTP only, `/var/lib/pvj/tls` is gone, the root on the phones still trusts the next certificate you sign |
| H10 | `sudo pvj-update` to a newer bundle, or the Updates card | `/var/lib/pvj/tls` is untouched and https:// works after the restart |

### Runtime folders: who owns what in /run (D45)

**Not run on any box yet.** The change that gives each service its own runtime folder ran in CI only: the unit tests, and one job that runs the installer for real as root under the runner's systemd (`tests/real_install_test.sh`: no display, not a Pi, not the box's systemd). This list is the proof for a box; until someone has run it, nothing may be claimed about how the folders behave on hardware.

Every command below is run on the Pi over SSH. **sudo** in the last column means the step needs root (the listing itself always does). Paste the two setup lines once per SSH session:

```
L() { sudo find /run/pvj /run/pvj-sysd /run/pvj-supportd /run/pvj-update -printf '%u:%g %m %y %p %l\n' 2>&1 | sort -k4; }
A=$(sed -n 's/.*"user": "\([^"]*\)".*/\1/p' /etc/pvj/install.json); echo "display account: $A"
```

`L` prints owner:group, mode, type (`d` folder, `s` socket, `f` file, `l` link, `p` pipe), path and, for a link, where it points. **The table**: what `L` must print whenever the five services are active, with `A` standing for the display account. Lines marked "when" appear only then; a "No such file or directory" line for `/run/pvj-update` is normal until an update has run from the panel.

```
root:root 755 d /run/pvj
root:root 644 f /run/pvj/.d45
root:pvj 750 d /run/pvj/netd
root:pvj 660 s /run/pvj/netd/netd.sock
A:pvj 750 d /run/pvj/player
A:pvj 660 s /run/pvj/player/player.sock
A:pvj 660 f /run/pvj/player/preview.jpg                  (when the panel has shown a preview)
pvj-web:pvj 750 d /run/pvj/web
root:root 777 l /run/pvj/web/netd.sock /run/pvj/netd/netd.sock
pvj-web:pvj 600 f /run/pvj/web/pin
root:root 777 l /run/pvj/web/player.sock /run/pvj/player/player.sock
pvj-web:pvj 640 f /run/pvj/web/shader-N-N.glsl           (when a shader plays; also mapper-N-N.glsl, overlay.bgra, overlay-N.bgra)
pvj-web:pvj 640 p /run/pvj/web/capture.fifo              (when a live input plays)
pvj-web:pvj 640 f /run/pvj/web/undervoltage-seen         (when low power was seen since the boot)
root:pvj 750 d /run/pvj-supportd
root:pvj 660 s /run/pvj-supportd/supportd.sock
root:pvj 750 d /run/pvj-sysd
root:pvj 660 s /run/pvj-sysd/sysd.sock
root:root 755 d /run/pvj-update                          (when an update has run from the panel)
root:root 644 f /run/pvj-update/result.json
```

Three one-line checks used below, each with the output that is good:

```
systemctl is-active pvj-player pvj-web pvj-netd pvj-sysd pvj-supportd     # active (five times)
sudo find /run/pvj -mindepth 1 -maxdepth 1 ! -name .d45 ! -name player ! -name web ! -name netd     # prints nothing
sudo stat -c '%U:%G %a' /run/pvj                                         # root:root 755
```

| # | When | Commands | Good | Needs |
| --- | --- | --- | --- | --- |
| R0 | First | `systemctl --version \| head -n 1; . /etc/os-release; echo "$PRETTY_NAME"; cat /opt/pvj/current/pvj/__init__.py \| grep version` | Write down the systemd version (252 on Bookworm, 257 on Trixie) and the installed version | |
| R1 | Upgrade over the running OLD version, by the installer | `L` first (old layout: `/run/pvj` mode 770 with `player.sock`, `netd.sock`, `pin` directly in it). Then, in the copy of the new tree: `sudo install/install.sh 2>&1 \| tee /tmp/install.log; grep -c "a folder per service" /tmp/install.log; grep -c "tmpfiles reported" /tmp/install.log; sleep 8; L` and the three checks | `1`, then `0`; the table; five times `active`; nothing; `root:root 755` | sudo |
| R1b | **Skip unless you have such a bundle.** Instead of R1, on a box that still runs the old version: update from the panel, started on the OLD version. `pvj-update` refuses a bundle that is not newer than what is installed, and this change does not raise the version (0.1.0 both before and after), so this needs a bundle built from this tree with `__version__` raised in `pvj/__init__.py` (`tools/make-release.sh`), signed with a key that is listed in `/etc/pvj/allowed_signers` on the box | System > Updates, install the bundle. Then `cat /run/pvj-update/result.json; L` and the three checks, and open System > Network | The result says done; the table; the Network page shows the addresses without a reboot (the old updater restarts only the player and the panel; the new installer moves the network helper) | sudo, a browser |
| R2 | After a boot | `sudo reboot`; when it is back, set up `L` and `A` again, then `L; journalctl -b \| grep -ci "ordering cycle"; journalctl -b -u systemd-tmpfiles-setup.service \| grep -ci pvj` and the three checks | The table, including the two links and `.d45`; `0`; `0`; five times `active`; nothing; `root:root 755` | sudo |
| R3 | It works across the folders | In the panel: play a clip; open the preview (Live); System > Network shows the addresses and "Find networks" answers; play a shader; a PNG overlay; a mapping if one is set up; a live input if a capture stick is there. On the box: `sudo pvj-pin` | Each works; the preview is a picture, not "no picture to show"; four digits. Then `L`: the table with `preview.jpg` and the "when" lines | sudo, a browser |
| R3b | The preview from inside the panel's sandbox, without a browser | `PIN=$(sudo pvj-pin); T=$(curl -s -H 'Content-Type: application/json' -H 'X-PVJ-Request: 1' -d "{\"pin\": \"$PIN\", \"name\": \"check\"}" http://localhost/api/pair \| sed -n 's/.*"token": *"\([^"]*\)".*/\1/p'); curl -s -o /tmp/p.jpg -w '%{http_code}\n' -H "Authorization: Bearer $T" http://localhost/api/preview.jpg; head -c 3 /tmp/p.jpg \| od -An -tx1; sleep 4; curl -s -o /tmp/p2.jpg -w '%{http_code}\n' -H "Authorization: Bearer $T" http://localhost/api/preview.jpg` | `200`, ` ff d8 ff`, `200` (the second one is the case where a picture is already there). A `503` with "Read-only file system" is the bug the review found; a `503` with another text and no screen attached means the player has no picture, which is not this bug (attach a screen or play a clip and repeat). If the first `curl` printed nothing, `echo "$T"` is empty: the PIN was refused (after wrong guesses pairing is locked for a while; wait and repeat). CI could not show a picture here (no display), so this step is the only proof that the panel reads the picture through its sandbox. Remove the device "check" afterwards under System > People and codes | sudo |
| R4 | Player restart from the panel | System > About and power > Restart player now; then `sleep 5; L` | The table. `netd.sock` is still `root:pvj`, `pin` still `pvj-web:pvj 600` | sudo, a browser |
| R5 | Restart of each unit | `for u in pvj-player pvj-web pvj-netd pvj-sysd pvj-supportd; do sudo systemctl restart $u; sleep 6; echo "== after $u"; L; done` | The table after every one | sudo |
| R6 | A crash (killed, restarted by systemd) | `sudo systemctl kill -s KILL pvj-player; sleep 5; L; sudo systemctl kill -s KILL pvj-netd; sleep 6; L; sudo systemctl kill -s KILL pvj-web; sleep 5; L` and the three checks | The table each time; five times `active`. Not known from the source: whether `/run/pvj/player` and `/run/pvj/netd` are removed and made again on an automatic restart; either is fine as long as the table holds afterwards | sudo |
| R7 | Stop, and start in the other order | `sudo systemctl stop pvj-player pvj-web pvj-netd; L; sudo systemctl start pvj-web; sleep 4; systemctl is-active pvj-web; curl -s http://localhost/api/hello; sudo systemctl start pvj-netd pvj-player; sleep 6; L` | After the stop: no `/run/pvj/player` and no `/run/pvj/netd` lines, `/run/pvj` still `root:root 755`, `/run/pvj/web` and `.d45` still there. Then `active` and JSON from the panel without a player. At the end the table | sudo |
| R8 | Boot with no network | Unplug the network cable (and have no Wi-Fi in range or switch it off first), `sudo reboot`, log in at a keyboard or plug the cable back in after two minutes; `L` and the three checks | The table; five times `active` | sudo, someone at the box |
| R9 | Reinstall | `sudo install/install.sh 2>&1 \| tee /tmp/install2.log; grep -c "a folder per service" /tmp/install2.log; grep -c "tmpfiles reported" /tmp/install2.log; sleep 8; L` | `0`, `0`; the table | sudo |
| R10 | The accounts cannot take each other's things | The block under this table | `refused` fourteen times and no line starting with `WORKED`; then `L` is the table and `ls /run/pvj` shows only `netd player web` | sudo |
| R11 | An emptying that was cut short is finished | `sudo systemctl stop pvj-netd; sudo rm /run/pvj/.d45; sudo install -d -o "$A" -g pvj /run/pvj/netd; sudo -u "$A" touch /run/pvj/netd/netd.sock; sudo install/install.sh 2>&1 \| grep -c "a folder per service"; sleep 8; L` | `1`; the table (`netd.sock` is a socket of `root:pvj` again) | sudo |
| R12 | **Skip unless R1b was done.** Rollback: there is a previous release to go back to only after an update to a higher version (`cat /opt/pvj/previous` prints a path; after a same-version install there is no such file and `pvj-update rollback` says so) | `sudo pvj-update rollback; sleep 8; L; systemctl is-active pvj-player pvj-web pvj-netd`; play a clip from the panel; then go forward again with the bundle (System > Updates) or `sudo install/install.sh` from the new tree, and `L` | After the rollback the old panel answers and plays a clip (it finds the sockets through the two links); known and written in D45: its preview does not work, and it writes `pin` with mode 640 again. After going forward: the table, `pin` 600 | sudo, a browser |
| R13 | A box without NetworkManager (only if there is one; not the Pi OS image) | `command -v nmcli \|\| echo none; systemctl is-active pvj-netd; L` | `none`, `inactive`, and no `/run/pvj/netd` line at all | sudo |

R10, paste as one block:

```
no() { who=$1; shift; if sudo -u "$who" "$@" >/dev/null 2>&1; then echo "WORKED and must not ($who): $*"; else echo "refused ($who): $*"; fi; }
no "$A" cat /run/pvj/web/pin
no "$A" touch /run/pvj/x
no "$A" ln -s /etc /run/pvj/x
no "$A" mv /run/pvj/netd /run/pvj/n2
no "$A" rm /run/pvj/netd/netd.sock
no "$A" touch /run/pvj/web/x
no "$A" rm /run/pvj/.d45
no pvj-web touch /run/pvj/x
no pvj-web ln -s /etc /run/pvj/x
no pvj-web mv /run/pvj/netd /run/pvj/n2
no pvj-web rm /run/pvj/netd/netd.sock
no pvj-web touch /run/pvj/netd/x
no pvj-web touch /run/pvj/player/x
no pvj-web rm /run/pvj/player/player.sock
L; ls /run/pvj
```

`sudo -u` runs outside the services' sandboxes, so R10 shows what the accounts may do, not what the services may do; the panel's sandbox is tighter still (R3b is the check that runs inside it).

Send back the output of `L` at every step, R0, and the output of R10. If any line differs from the table, stop and send it: do not "fix" an owner by hand.

### Crossfade (D71)

**Run on the test Pi 4 by a script on 2026-10-09, with nobody at the monitor** (the journal of that afternoon): what a script can see of X2, X3, X4, X10, X11, X12, X14 and X16 held at 2560 x 1440, and a play answered after 0.93 to 1.09 s. **Everything that needs eyes is open**, and so is every row not named. In CI it ran on a software GPU in a 320 x 180 window, which says the picture is right and nothing about time. Ten minutes; the numbers matter more than the look, so please send them.

Before: two clips of 1920 x 1080 on pads A1 and A2, Mix > Transition between clips set to **Crossfade**, duration 2s. After each play, `GET /api/status` holds `mix.fallback` once the box has given up, and the panel's journal (`journalctl -u pvj-web`) says "crossfade given up: ..." with the reason.

| # | Do | Look for | Send back |
| --- | --- | --- | --- |
| X1 | Play A1, then A2 | A1 freezes, then melts into A2 over two seconds; A2 moves from its first frame | How long A1 stood still before the fade began (it should be well under a second), and whether the fade looked smooth or stepped |
| X2 | The same again, three times, with `journalctl -u pvj-web -f` open | No line "crossfade given up" and no line "no crossfade, a cut instead" | The line, if one comes. "given up" names the still's time, the steps or the dropped frames. "a cut instead" with "error running command" or a permission in it means the player could not write the still into the panel's folder (`/run/pvj/web`, a file of group `pvj`); with "rows are not plain bytes" or "not an 8-bit picture" it means this mpv writes its PNG another way than CI's 0.37 did. Either would make every crossfade a cut, and only a box can show it |
| X3 | During a fade press **Blackout** | Black at once, no picture left behind | Anything that stayed lit |
| X4 | During a fade press **Stop** | The screen clears at once | |
| X5 | With **Freeze** on, play A2 | The frozen picture melts into A2, which plays | |
| X6 | Put on an effect and a mapping, play A1 then A2 | The outgoing picture keeps its shape and its effect while it fades; nothing is drawn twice or in another place | A photo if a surface jumps |
| X7 | Play a stream (or a live input), then a clip, then the stream again | The old picture waits for the stream's first frame, then fades | How long it waited |
| X8 | On the 2560 x 1440 screen: X1 again | Either a fade, or a dip to black from the second play on | Which, and the journal's line |
| X9 | Restart the player during a fade (`sudo systemctl restart pvj-player`) | The new player starts clean; no old picture over it | |
| X10 | After all of it: `ls /run/pvj/web` | No `transition-*.png` and no `overlay-63.bgra` | The listing, if there is one |
| X11 | While A1 plays, play A2 and press **Stop** at once, within the freeze | The screen clears and stays clear; A2 does not start after it | If A2 started, or how long Stop took: this is whether mpv answers while it takes the screenshot, which no test here can show. **Press it DURING the screenshot itself, the first 0.6 s or so after the tap** (the script on 2026-10-09 pressed 0.7 s in and right after the play had answered, both after the screenshot): that number is the one nobody has |
| X12 | The same with **Blackout**, from the panel and from a MIDI controller (a pad, then the Blackout button straight after) | Dark at once; A2 plays under the dark and is there when you show the picture again | How long the dark took to come from the controller, **and from the panel when pressed within the first half second after the tap, while the player is still taking the screenshot**: that is the unmeasured number (pressed later, it answered in 0.08 to 0.09 s on 2026-10-09) |
| X13 | During a crossfade at 1920 x 1080, move a MIDI fader for volume and watch a sync client if there is one | The fader answers as always; the client does not drift | Whether the controller felt slow during the fade: every crossfade step scales 8 MB in the panel |
| X14 | Set the transition to **Wipe from left**, then **Slide off up**; play A1, A2 | An edge that moves evenly; the old picture stands still (wipe) or moves off as one piece (slide) | Whether the edge moved smoothly or in jumps, and the journal's line if the box gave up. A wipe should ask less of the box than a crossfade; say if it did not look so |
| X15 | X8 again with a wipe on the 2560 x 1440 screen | | Whether a wipe holds where a crossfade gave up |
| X16 | Run a slow wipe (duration 5s) ten times, then `journalctl -u pvj-player -n 50` | The player never restarts during a wipe (the still's file is replaced and removed while mpv has had its bytes: mpv's manual says it copies them during the command, and CI's 0.37 did) | Any restart of the player, or a line with SIGBUS or "overlay" in it: that would mean this mpv does not copy |
| X17 | If the box ever stalls for seconds during a transition (a heavy clip, a slow card): look at the screen ten seconds later | No still left over the picture. The panel gives up after two seconds without an answer and sends the removal once more five seconds on | A still that stayed, what was played, and `mix.fallback` from `GET /api/status`: a command that timed out and was run later is the one case nobody could test |
| X18 | Start a live input, then play a clip, then Stop; after each: `pgrep -af "mpv.*--o=" ` on the box (the live input's helper is an mpv that writes into a pipe) | The helper runs while the live input is on the screen and never afterwards | A helper that is still there under a clip or after a Stop: the device would stay open. The panel decides this by what it loaded last, not by asking the player; whether the player's own `path` is empty right after a Stop was not looked at and is not relied on |
| X19 | Start a live input (it takes a second or more) and press **Fade out** at once, before its picture is there | The picture stays faded out when the input arrives | If it came up lit: a wish for the level made while something loads must win |
| X20 | Press **Blackout**, then restart the player (`sudo systemctl restart pvj-player`), wait five seconds, play a clip | The screen stays dark through the restart and under the clip; Show brings the clip up | If the clip came up lit: a new player starts at full brightness, and the panel must put the level back when it sees the new one |
| X21 | With a rotation of Vibes running and a clip of 1920 x 1080 loading (tap a pad), press **Blackout** at once, ten times over | Dark at once every time, also while the clip is still loading; nothing hangs | How long the dark took when a load was in the way. In the tests a Blackout waits for one write of the level and no more (under half a second against a load of two); whether mpv answers that write while it loads is the player's matter |
| X22 | Choose a generator (a shader from the Shaders page), start a live input, and choose another generator within a second of starting the input, before the camera's picture is there. Then once more, waiting for the camera's picture before the second generator. After each: `pgrep -af "mpv.*--o="` | The generator is on the screen, not the camera's picture and not a frozen frame of it; the helper is gone | What is on the screen, and whether the helper still runs. The seventh review found that a generator chosen just after a live input could leave the pipe on the screen with its helper stopped (the player went by its `path`, which follows a load a moment late). The tests show the repair against a stand-in for mpv; how late a real mpv's `path` is, is not known here |
| X23 | With a live input on the screen, restart the player with the panel's **Restart player** button, and once more with `sudo systemctl restart pvj-player`; `pgrep -af "mpv.*--o="` five seconds after each. Then the same with **Blackout** on, and play a clip afterwards | After the panel's restart the helper is gone at once. After the restart from the shell it may run until the next thing is played (nothing tells the panel), and must be gone then. Under Blackout the screen is dark again within ten seconds of the restart and stays dark under the clip | A helper that stays after the panel's restart; a lit screen under Blackout more than ten seconds after a restart, and the journal's line "could not put the picture's level back on the restarted player" if there is one (the panel offers the level to the new player five times, two seconds apart) |
| X24 | D89. On the 2560 x 1440 screen with the logo off: X1 with two 1920 x 1080 clips, three times; then `curl -sk https://<box>/api/status` with an owner token and look at `mix.last`. Then twice more: once with an effect on (Wash, say), once with the Opacity at 60 | `"still": "video"`, `"still_size": [1920, 1080]`, `"drawn": [0, 0, 2560, 1440]`; no journal line "cannot draw an overlay at another size". With the effect on and with the opacity down, the still at the tap looks exactly as the screen did: the effect and the dimming are in it, not lost and not twice | `still_ms`, `hold_ms`, `steps`, `seconds`, `dropped` of each play, and how long A1 stood still before the fade began against X8 on 2026-10-09 (about 0.9 s). The freeze is shorter, not gone: the player's screenshot of the clip was 0.38 to 0.44 s on the coordinator's probe. For the effect and the dimming: any jump in look or brightness at the tap (the Pi decodes in hardware, which CI does not) |
| X25 | D89. The same with a picture over the video on (a logo: the overlay setting), on the 2560 x 1440 screen and once more on a 1920 x 1080 screen | `"still": "window"`, `"still_size": [1280, 720]` (on the 1920 x 1080 screen `[960, 540]`); `"drawn"` the whole screen; the logo fades with the old picture and is not doubled | Whether the half-size still looks softer than the screen at the moment of the tap (the player draws it at twice its size), on each screen, and the same figures as X24 |
| X26 | D89. X14 and X15 on the 2560 x 1440 screen | The edge moves evenly; the still's part is where the old picture was | Whether the edge looked smooth (on a halved still it moves in steps of two pixels; on a 1920 x 1080 clip's own still drawn at 2560 x 1440 in steps of one or two), and `steps` from `mix.last` |
| X27 | D89. A clip of another shape first (4:3, or a tall phone clip), then a 1920 x 1080 one: a crossfade, then a wipe from left and a slide off left | The crossfade: the old picture fades where it was, and beside it the new clip is there at once (a still of the clip has no bars). The wipe and the slide: a still of the window (`mix.last.still` "window"), so the old picture with its bars moves or is cut across the whole screen and does not stop at the bar's edge | Whether the crossfade beside the bars looks acceptable to the owner; if not, a still of the window whenever the clip does not fill the screen is a one-line change in `Player.video_place`. And whether the wipe and the slide went edge to edge |

### A shader pad with an effect on (D73 and D74 together): do these two first

**Nothing of this has run on a board.** The two features were tested together only against a stand-in for the player and on CI's software GPU.

| # | Do | Look for | Send back |
| --- | --- | --- | --- |
| PE1 | Screen at 2560 x 1440. A shader on pad A3, a clip on A1, Transition set to Crossfade, 2s. Tap A3, put the effect Wash on, then tap A1. Do it three times, then once more with Edge Glow | The filtered shader stands still, then melts into the clip, which has the effect on it | `mix.fallback` after each (does the box give up, and with which words: with an effect on it should say "frames were dropping during the change with the effect ... on; taking the effect off may help", not "the clip dropped"); what the Effects card and the line under the Mix picker say; `last.still_ms` |
| PE2 | From a Launchpad, as fast as the fingers go: a shader pad, effect Next, another shader pad, effect Off, a clip pad. Three times, with `journalctl -u pvj-web -f` open | Each press does what it says, in order; the last thing pressed is on the screen | Anything that did not happen, and every journal line with "dropped", "was not shown" or "did not go on" |

### Shaders on pads (D73)

**Nothing of this has run on a board.** Before: the Shaders and Vibes module on, Live > Edit pads, pad 1 a clip, pad 2 a shader (Shader, then for example Aurora), pad 3 a shader that has a preset, with that preset.

| # | Do | Look for | Send back |
| --- | --- | --- | --- |
| P1 | Tap pad 2, then pad 1, then pad 2, with the Mix transition on Cut and then on Crossfade | The shader comes at once each time (a cut, also with Crossfade); the clip after it blends from the shader's picture with Crossfade | Whether the pad lit up while its shader was on, and anything that flashed between the two |
| P2 | Start Vibes, then tap pad 3 | Vibes stops, the shader starts with the preset's values (compare with the Shaders page: the preset is the one marked) | |
| P3 | The same pads from a MIDI controller, and from OSC if there is a sender | The shader follows the press without a wait you can feel; the controller's light for the pad is on, and brighter or green while its shader plays | How long from the press to the picture for a shader the box had not shown since it started (the first time the GPU looks at it) |
| P4 | Press a shader pad on the controller and a clip pad straight after it | The clip plays and stays: the shader does not come over it a moment later | If the shader came after the clip |
| P5 | Delete the shader of pad 2 on the Shaders page (an uploaded one), then tap the pad | The panel says the pad's shader is not on the box any more; the screen keeps what it had | |
| P7 | **With Vibes running, tap a shader pad on the controller, twenty times over an evening's worth of shaders: also right after the box started, when Vibes shows each shader for the first time and the GPU takes seconds over it** | The pad's shader comes every time; the screen is never black in between (Vibes' picture stays until the pad's is on); Vibes has stopped | Any black screen or a tap that showed nothing, with the time and `journalctl -u pvj-web` around it ("was not shown" is the line of a tap that was dropped). This is the case the fifth read found broken, and no test here runs a real GPU's seconds |
| P6 | System > At power-up: Pad, pad 2; restart the box | The shader is on the screen after the start, at the level the mix says | The journal line of the autostart if it did not start |

### An effect over a generator (D74)

**Nothing of this has run on a board.** In CI the pair ran on a software GPU in a 320 x 180 window, which says the picture is right and nothing about time. The question for the board is what two shader stages cost. Twenty minutes. The numbers come from two requests while a pair is on: `GET /api/effects` (`on.load`, `on.drops_per_second`, `on.pass_ms`, `on.working`) and `GET /api/shaders` (`playing.pass_ms`, `playing.load`, `playing.drops_per_second`, `playing.effect`, `gpu.busy_percent`). Wait ten seconds after each change before reading: the guard does not count the first three and judges over six.

Before: Shaders and Vibes on, nothing mapped, Effect detail on Automatic. Note the screen's size and rate.

| # | Do | Look for | Send back |
| --- | --- | --- | --- |
| E1 | Picture detail 540 lines. Play the shader Silk. Put on the effect Wash | The shader with the wash over it, moving as before | Both requests' numbers; the same two numbers for Silk alone first (the effect Off) |
| E2 | The same shader with Vignette, Kaleido, RGB Halftone and Edge Glow, one after the other | Each effect over the shader | The numbers for each pair; which lights the Effects card shows |
| E3 | The same four effects over a medium shader (Aurora) and over a heavy one (Nebula) | | The numbers for each pair. This is where frames are expected to drop: say from which pair on |
| E4 | Picture detail 720 lines, then E1 and E2 again | Softer under Edge Glow, Corner Color Tint and Edge Blowout (Automatic keeps those at 540 lines) | The numbers; whether the three look softer than the others |
| E5 | Effect detail 540 lines with Picture detail 720, any light effect | The whole shader softer | The numbers beside E4's for the same pair |
| E6 | Start Vibes on Ambient with Wash on, and let it run for five shaders | The effect stays through every change; the dip to black and back takes the wash with it; no flash of the unfiltered shader | Anything seen without the effect, and for how long; `GET /api/effects` `on.id` after each change |
| E7 | During E6 with a pair that drops frames (from E3): leave it for a minute | The Effects card turns to "Too heavy with this shader"; Vibes does not leave the shader out and does not end with "the box is dropping frames"; the Shaders page says "An effect is on over it: Vibes does not judge the shader meanwhile". **After about 23 seconds of dropping 2 frames a second or more the box takes the effect off** and the card says "The last effect came off: the picture was dropping frames with it on over the shader for 20 seconds ..." (and adds "Frames are still dropping without it ..." if the shader alone goes on dropping them); the shader plays on and the rotation goes on. The two numbers (2 a second, 20 seconds) are guesses: say whether it came off too soon, too late, or for a pair that looked fine | `GET /api/shaders`: whether any shader's `heavy` is set now that was not before (none should be), and `vibes.last` |
| E8 | Take the effect off during E7 and wait twenty seconds | Vibes judges the shader by itself again | Whether a shader was marked heavy now, and whether that seems right for the shader alone |
| E9 | With a pair on: Blackout, Blackout off, Fade out, Fade in, Opacity 50 | Black is black; the pair comes back as it was | Anything lit under Blackout |
| E10 | With a pair on and Transition set to Crossfade: play a clip | The filtered shader stands still, then melts into the clip, which has the effect on it | `mix.fallback`; how long the shader stood still |
| E11 | With a pair on: Stop. Then play the shader again | The screen clears; the shader comes back with no effect | |
| E12 | With an effect on over a clip: play a shader | The shader comes up with the effect on it from its first picture. (Expected and unverified: for a moment the effect runs with the text made for the clip, and the worker hands the GPU a new one at once. If the GPU ever refused that, the screen might be black for a moment before the effect comes off; never seen, and it cannot be made on purpose) | Any flash of black or of the plain shader |
| E13 | With a pair on, restart the player (`sudo systemctl restart pvj-player`) | Nothing on the screen; the Effects card says the player was restarted | |
| E14 | With a pair on, move a control of the effect and a control of the shader in turn | Each follows; neither resets the other | Any hitch, and which control made it |
| E15 | With Vibes running and an effect on: press a controller's effect Next three times quickly, and once more just as Vibes changes its shader | The effect goes three on, then one more: no press is lost, also at the change | Which effect was on before and after each burst |
| E16 | Press a controller's effect Next and at once Stop on the panel, then play a clip | Nothing goes on over the clip; the Effects card says the effect "did not go on" because the screen was cleared | |

What cannot be made on purpose: a pair the GPU refuses. If the Effects card ever says "the player refused ... over the shader", send the whole sentence and the two names.

### OSC: who may send (D78)

**Never run on a box or with a real TouchOSC.** Before: OSC is switched on (System > OSC), a tablet with TouchOSC is on the same network with the box's address and port 9876 as its target, a control in the layout sends `/pvj/stop`, a clip plays, and a paired full-access phone or laptop has System > OSC open. A second device that can send OSC (a laptop) helps for O3.

| # | Do | See on System > OSC | See on the screen |
| --- | --- | --- | --- |
| O1 | With all three switches off, press the control on the tablet | The tablet's address under "Senders in the last ten minutes" with "Allow this one", and `/pvj/stop` under "Last messages" | The clip stops |
| O2 | Switch "Only these devices may send" on. Play a clip, press the control | "Nobody may send yet"; the tablet's row says "Refused: not in the list of devices that may send" | The clip goes on playing |
| O3 | Press "Allow this one" beside the tablet, press the control. If there is a second sender, send `/pvj/stop` from it too | The tablet is in the list and its row says the last one was `/pvj/stop`; the second sender is refused | The clip stops for the tablet only |
| O4 | Press "Remove" beside the tablet's address, play a clip, press the control | Refused again, and the count of refused packets goes up | The clip goes on playing |
| O5 | Switch the list off and "A sender must be a paired device" on. Press the control before the panel was ever opened on the tablet | "Refused: no paired device at this address" | The clip goes on playing |
| O6 | Open the panel on the tablet (pair it if needed), go back to TouchOSC, press the control | "Counts now:" names the tablet's address; the message is let in | The clip stops |
| O7 | Remove the tablet under System > People and codes, play a clip, press the control | Refused at once: "no paired device at this address" | The clip goes on playing |
| O7b | Pair the tablet again, open the panel on it, press the control (it works); then About and power > Log out on the tablet, play a clip, press the control | Refused at once: "no paired device at this address" | The clip goes on playing |
| O8 | Switch that off and "A key in the address" on. Press the control | "Refused: no key in the address" | The clip goes on playing |
| O9 | Press "Show the key", put `/k/<key>` in front of the address in the layout, press the control. Wait 30 seconds | The message is let in, shown as `/pvj/stop` without the key; the key hides itself | The clip stops |
| O10 | "Make a new key" (answer the question), play a clip, press the control with the old key in the layout | "Refused: wrong key" | The clip goes on playing |
| O11 | On an operator's and a guest's device, open System | No OSC page with lists or a key | Nothing |
| O12 | Write down how the tablet's address looked in O1 and whether it changed during the test (a new address from the router) | | |

### OSC: the first switch-on, the open warning and the control names (D83)

**Never run on a box, with a real TouchOSC or a real tablet.**

| # | Do | See |
| --- | --- | --- |
| D1 | On a box where OSC is on with every lock off (as before the update), update and open Setup | The tablet still works with nothing changed. The OSC row says "Open: anyone on this network can control the box", the OSC page and the health page say "OSC is open..." |
| D2 | On an Operator's device, open Setup > Health | The same warning, ending "The owner can switch on a lock"; no OSC page |
| D3 | Switch any one lock on | The warning goes from all three places within a few seconds |
| D4 | After a factory reset (or on a new box), switch OSC on | A message says the paired-device lock came on; the note beside "A sender must be a paired device" says the tablet must pair once. A control on an unpaired tablet does nothing ("Refused: no paired device at this address") |
| D5 | Open the panel on the tablet, pair it as an Owner device, press the control | It works |
| D6 | With "Send state back" on and a shader with named inputs on the screen: bind eight labels to `/pvj/state/shader/control/1/name` to `8/name` | The labels read as the knobs do on the Shaders page; they change when the shader changes; empty for a knob that is not there. **Write down whether TouchOSC shows a string sent in a bundle in a label** |
| D7 | The same for an effect (`/pvj/state/effect/control/<n>/name`) and bind a fader to `/pvj/state/effect/control/1` | The names of the effect's controls; the fader follows the panel's control |

### OSC: state sent back (D82)

**Never run on a box, with a real TouchOSC or a real tablet.** Before: OSC is on, a tablet with TouchOSC sends to the box (O1 works), the tablet has a fixed address, and TouchOSC's connection has a receive port (9000 here). In the layout a few controls have a receiving message: a pad on `/pvj/state/pad/1/1`, the Blackout button on `/pvj/state/blackout`, the fade button on `/pvj/state/fade/on`, the opacity fader on `/pvj/state/opacity`, a label on `/pvj/state/clip/name`, a light on `/pvj/state/alive`.

| # | Do | See on Setup > OSC | See in the layout |
| --- | --- | --- | --- |
| F1 | Before anything is switched: play, black out and fade from the panel | "Send state back" is off, the list is empty | Nothing changes by itself (the box sends nothing) |
| F2 | Press a control on the tablet, then "Use the device that is sending now"; type 9000 and a name; "Add this device"; switch "Send state back" on | The address filled in is the tablet's own; the row says "Last sent just now" | The alive light is on; the label shows the clip's name. **Write down whether anything arrives at all: the box sends OSC bundles, and whether TouchOSC takes them is not known** |
| F3 | From the panel: play pad 1 of bank A, then another pad, Blackout on and off, Fade out and in, the Opacity slider | | The pad's light follows within half a second; Blackout's and the fade's lights follow; the fader moves |
| F4 | The same from a MIDI controller if there is one | | The layout and the controller's own lights agree |
| F5 | A second tablet, added as a second device: act on one | Two rows, both "Last sent" | The other stays in step |
| F6 | Drag the opacity fader on the tablet slowly | | **Write down whether the fader jumps or stutters under the finger** (its own value comes back) |
| F7 | Take the tablet off the network (Wi-Fi off) for a minute, play and stop clips from the panel, then back | The row still says "Last sent" (the box cannot know); nothing else on the page changes | The panel and the screen stay as quick as before; back on the network the layout is right within 30 seconds, or at once after a control that sends `/pvj/state` |
| F8 | Switch "Only these devices may send" on without the tablet in it; then remove the tablet's row | "Not let in by the locks that are on, nothing is sent to it"; after Remove the list is empty | The alive light goes out (after 6 seconds with the script) |
| F8b | Move the box to another network (or change its address range) with the tablet still in the list; add the tablet's new address | The old row says "Not on this network now, nothing is sent to it" and has Remove; the new one is added beside it | The layout follows again at the new address |
| F9 | On an Operator's device and through a remote support session, open Setup > OSC | No card "Send state back" | |

### Owner, Operator, Guest and the lock on guest controls (D80)

**Never run on a box, with a phone, a projector or a controller.** Before: one phone paired with the PIN (Owner), one with an Operator link, one with a guest code; Projectors and Room on with at least one projector, one scene that switches a group on and one that switches it off; a clip on a pad.

| # | Do | See on the guest's phone | See elsewhere |
| --- | --- | --- | --- |
| R1 | Right after the update, open the panel on a phone that was a guest before | A card "Guest controls" on Live (or Room): Previous, Next, Stop, Blackout, a clip to play, scenes, shaders, Projectors on and off. The page's own buttons stay off | Nothing |
| R2 | Guest: tap a pad, Next, Stop, Blackout twice | Each does it within a second; no error line | The screen follows; the journal (`journalctl -u pvj-web`) has one line per tap with the phone's name |
| R3 | Guest: tap a scene that switches the wall on | "(name): started" | The projector switches on |
| R4 | Guest: tap Projectors off | A question "Switch the projectors off?"; nothing happens until "Switch off" | After "Switch off" the projector goes off. Note how long the question stays (it goes by itself after 8 seconds; the box takes the answer for 30) |
| R5 | Guest: Projectors on, then Projectors off again within five minutes | After the question: a line that the projectors were switched off by a guest a moment ago | The projector stays on |
| R6 | Operator: Room, "Lock guest controls for a show" | Within a few seconds the card is gone and one line reads "The room is locked for a show: you can watch" | The Owner's People and codes shows "Locked" after a reload |
| R7 | Restart the box (Owner), open the guest's panel | Still locked | |
| R8 | Owner: "Open guest controls" | The card is back within a few seconds | |
| R9 | Guest: tap Next twelve times fast | After ten: "too many guest actions at once; wait a moment" | The Operator's own buttons still work at once |
| R10 | Operator: System | Schedule, At power-up, Projection mapping (when on) are there; Network, Updates, Remote support, Backup and reset, Look, MIDI, DMX, OSC are not | |
| R11 | Operator: Media, upload a clip, rename it, delete it; Live, Edit pads, give a pad a clip | Each works | |
| R12 | Operator: People and codes | "Let someone in" with the guest code only, a card "Guests" with the guest's phone and Remove, "Create a guest link", the lock. No PIN, no operator code, no owner or operator device | Remove the guest: its panel returns to the pairing screen |
| R13 | A MIDI controller and an OSC sender that worked before the update: every control once | Nothing | Each does what it did before, no more; with guest controls locked too (the lock is for guests only) |
| R14 | Hold the controller's code pad (switch on first), pair a phone with the code | The phone is a Guest | Paired devices names it Guest (can watch) |
| R15 | If a phone was paired from a controller before the update: open its panel | It is a guest: the Guest controls card, and "Guest (can watch)" under About and power | The owner's Paired devices names it Guest |
| R16 | Owner: People and codes, A code from a controller, "A controller's join code pairs": Operator (answer the question). Hold the code pad, pair a phone | The display says "One-time operator code"; the phone is an Operator | Set it back to Guest: that phone stays an Operator, the next one is a Guest |
| R17 | Operator: Room, set up, edit a scene, tick "Not for guests", save | On the guest's phone the scene is gone from the Guest controls card after a reload | The Operator still starts it |
| R18 | Operator: System > MIDI controller: teach a pad with Learn, change one control of a drawn layout, switch the lights | Each works | The pad that shows a code reads "Only the owner changes it"; no code action in the lists |
| R19 | Operator A: People and codes, remove Operator B | B's panel returns to the pairing screen at once | A has no Remove on his own row and sees no owner |
### NDI input (D62): one first run on a Pi 4 (2026-10-08); most of this is still to do

**What has run (2026-10-08, the owner's test Pi 4, Debian 13, mpv 0.40, by the coordinator over SSH, nobody looking at the monitor then; the owner looked at the moving source on the monitor afterwards and said "The pi looked good", which is the first look by eye, at a moving source only):** N0, N2, a first look at N4 and N5 judged from the preview picture only, one 61 second run of a moving source, and N9. The figures and what they do not say are in `pvj/NDI.md`, "First run on a Pi 4". **Not done: everything that needs eyes on the monitor, N6 as written (five minutes per size, 60 frames a second, 1080p60, HX), N7, N8, N10, N11, N13 to N18, and N12's question whether a still picture is on the screen.** Two things the image does not have: `socat` (the steps below use a python3 line instead) and memory accounting for the helper's unit (N14). **Before N2: the owner must have decided that NDI's licence covers this box** (`pvj/NDI.md`, "The SDK License Agreement itself"). On 2026-10-08 he decided that for his own test Pi only; on any other box, a venue's above all, stop after N17. **The order: N0, N1, N17 (all three on the box before it opts in), then N2 (the opt-in), N3 to N16, and N18 last.** Sound is built since 2026-10-08 and **nobody has heard it**: N10 is its step, after N5. Do these in that order; stop at the first that fails and send the journal lines (`journalctl -u pvj-ndi -b`). You need the NDI SDK for Linux (from ndi.video, with its licence read and accepted by the owner) and a sender on the same wired network: Resolume's NDI output, or NDI Tools "Test Patterns" and "Screen Capture" on a laptop.

N0. **Before anything: the settings schema.** Since 2026-10-11 this branch makes no schema change (the key `ndi` is read with defaults), so a box can go from master to it and back without touching the settings file. A box that ran the branch before that date (the test Pi) holds a schema 15 file, which neither master nor this branch loads: put the schema 14 copy back first (the hand-off says where it is). Check: `grep -o '"schema": [0-9]*' /var/lib/pvj/settings.json` says 14 before and after the install.
N1. **A box that did not opt in, in the panel.** System > NDI input, switch on. The row on System says "NDI is not set up on this box" (chip "Set up", not "Problem"); the page says "NDI is not set up on this box" with the licence note and the one command, and no word about a helper that stopped. Add an address under "Addresses to ask": it is saved. `curl` or the browser's network tab: `GET /api/ndi` answers at once with `"setup": false`. `journalctl -u pvj-web -b | grep -i ndi` shows no error.
N2. **The opt-in: the one command.** First try the x86_64 file on the Pi on purpose: `sudo pvj-ndi-runtime install "<the SDK>/lib/x86_64-linux-gnu/libndi.so.6"` must refuse with "is for x86_64, this box is aarch64" (in the SDK that name is a link to `libndi.so.6.3.2` beside it; on the first run the link itself was refused as "a link" and the real file name had to be given, which did refuse as it should; a link to a plain name in the same folder is followed since then, and that has not been tried on the box yet: try both names), and the checks of N0 must still all hold (a refused file sets nothing up and leaves no `/opt/pvj-ndi`). Unpack the SDK in your own home or on a stick, never in the media folder. If the read-only root is on (`sudo pvj-rootfs status`), switch it off and restart first, or the setup is gone at the next restart; the command says so if it sees it. Then, with something on the screen so the restart can be seen: `sudo pvj-ndi-runtime install "<the SDK folder>"`. Write down everything it prints. It must name the file it took, say whether it installed avahi packages (on Raspberry Pi OS it should not need to), say that it restarts the panel and the player, and end with "NDI is set up on this box: the helper pvj-ndi is installed and was running 3 seconds after its start". If it ends with "NOT running", that is the first finding: send `journalctl -u pvj-ndi -n 50` and go to N3 (the setup is left in place for that). Note how long the screen was dark. Then: `id pvj-ndi` shows the account in group `pvj-ndi` only; `systemctl is-enabled pvj-ndi` says enabled; `systemctl is-active pvj-player pvj-web pvj-ndi` says active three times; `systemctl show -p SupplementaryGroups pvj-web pvj-player` shows each unit's own groups followed by `pvj-ndi` (the drop-in adds, it must not replace: the panel still has `audio video`, the player `video render audio input`); `ls -ld /run/pvj-ndi` is `pvj-ndi pvj-ndi drwxr-x---`; `systemctl cat pvj-web pvj-player` shows the drop-in `50-pvj-ndi.conf` under each. A clip still plays, a MIDI controller and the capture input still work (the groups they need are still there). Reload the NDI page: no "not set up" notice any more. Read `journalctl -u pvj-ndi -n 20`: "loaded the NDI runtime (...)" once the page has been opened with the module on. Run the same command a second time: it must end the same way and change nothing else. **First run, 2026-10-08:** with the folder that holds `lib/aarch64-rpi4-linux-gnueabi` it worked the first time: the library, the group and the account (in no other group), the unit enabled, "was running 3 seconds after its start", `pvj-web` with the groups `audio video pvj-ndi` and `PVJ_NDI_DIR`, `NRestarts` 0. Not done yet: the second run of the command, the clip, MIDI and capture checks, and how long the screen was dark.
N3. **The sandbox, line by line.** If the runtime does not load or finds nothing, find the line: `sudo systemd-run --pty -p User=pvj-ndi -p Group=pvj-ndi -p ProtectSystem=strict ... python3 -c 'import ctypes; ctypes.CDLL("/opt/pvj-ndi/libndi.so.6")'`, adding the unit's lines one at a time. The ones most likely to matter: `IPAddressDeny=any` with its allow list (does discovery still work? does video arrive?), `PrivateDevices`, `RestrictAddressFamilies`, `ProtectHome` (the runtime's settings folder is pointed at `/opt/pvj-ndi`), and whether `libavahi-client.so.3` is installed and `avahi-daemon` runs. Then try adding `MemoryDenyWriteExecute=yes` and `SystemCallFilter=@system-service`; if both hold through N4 to N8, add them to the unit and to `tests/test_units.py`.
N4. Discovery: the sender appears in the list within about 5 seconds, with its real name. Stop the sender: it leaves the list. Unplug mDNS (put the sender behind a router, or block UDP 5353): add its address under "Addresses to ask" and it appears.
N5. Play at 720p30 from Test Patterns. The picture is right: colours (the bars are in the right order, so the byte order `uyvy422` is right), no green or purple cast, no shear (the line stride), right way up. "Now playing" says NDI and the name.
N6. **Smooth or not: measure, do not guess.** For each of 720p30, 720p60, 1080p30, 1080p60 (full NDI from Resolume or Test Patterns), and once NDI HX if a sender has it, play for 5 minutes and write down: the page's "shown" and "dropped" at the start and the end; mpv's own drops (`socat` is not on the image, so ask with python3, one property a call: `sudo python3 -c 'import socket,json,sys; s=socket.socket(socket.AF_UNIX); s.connect("/run/pvj/player/player.sock"); s.sendall((json.dumps({"command":["get_property",sys.argv[1]]})+"\n").encode()); print(s.recv(65536).decode().strip())' frame-drop-count`; the same with `decoder-frame-drop-count`, `vo-delayed-frame-count`, `estimated-vf-fps`); `top -H -p $(pidof -s python3)` for the two `ndi-` threads and the runtime's own threads, and mpv's share; the temperature; and `cat /proc/sys/fs/pipe-max-size` (the helper asks for a pipe of 8, then 4, then 1 MiB, and an account without privileges gets at most this figure, 1 MiB unless the box raises it; if 1080p drops frames, try `sysctl fs.pipe-max-size=8388608` and measure again). Alternate with a plain 1080p clip for the same time as the control (LESSONS: an A/B run, and trust the person watching over a counter). Watch a moving pattern for stutter. **First run, 2026-10-08, one short run and not this step:** Scan Converter from a Mac on Wi-Fi, 1764 x 992 at 30, 61 seconds: received 1769, shown 1770, dropped 0; mpv after 59 seconds `estimated-vf-fps` 30.0, `frame-drop-count` 1; one 3 second `top -H` sample, mpv about 117 percent of one core and the helper about 68; 46.7 C. The frames came unevenly (238 to 333 in ten seconds), which may be the Wi-Fi or the sender. Do the step itself with the sender on a cable. **2026-10-08, five minutes at speed 1 without an effect (still the Wi-Fi sender):** received 6228, shown 6201, dropped 28, dropped by the library 0; mpv `frame-drop-count` 1 to 8, decoder drops 0; about 20.4 frames a second arrived although the sender says 30, so the shortfall is before the Pi; mpv 93 percent of a core, the helper 52, load 2.4, 50 to 52 C. With an effect on top (fx-kaleido) the load is much higher (mpv 150 percent, the helper 87, load 3.85, at a Mix speed of 1.86 that has since been taken away from live sources): measure NDI with an effect as a row of its own. The Mix speed must now make no difference: set it to 1.86, play the source, and `speed` (the python3 line) must answer 1.0 and the drops must be those of speed 1; play a clip afterwards: it must run at 1.86.
N7. Latency: a running clock or frame counter on the sender's own screen and on the box's screen, both in one phone photo, at 720p30 and 1080p60; three photos each, write down the difference.
N8. A sender that changes: change the sender's resolution while it plays (the picture should go for a second or two and come back in the new size); change 30 to 60; quit the sender (the last frame stays, the page says "Waiting for the source") and start it again (the picture returns by itself); pull the network cable for 10 seconds; switch the module off while it plays (the screen clears); play a clip while NDI is on (`journalctl -u pvj-ndi` must show no error, and `ls /run/pvj-ndi` no leftover pipe). **2026-10-08, the first half, once:** Scan Converter changed size twelve times in nine minutes (between 1764 x 992 and 844 x 474); each was logged as "is W x H at 30.0", no error, no restart of the helper, playback carried on. Not done: a change of rate, the sender quitting and returning, the cable, the module off while it plays, a clip while NDI is on.
N9. From the box, as the helper's account, the internet must be out of reach. One `-p` for each allowed range: systemd 257 refuses a list in one `-p` that holds `localhost` ("Failed to parse IP address prefix: localhost"), and the unit itself allows no localhost. `sudo systemd-run --pty -p User=pvj-ndi -p IPAddressDeny=any -p IPAddressAllow=link-local -p IPAddressAllow=multicast -p IPAddressAllow=10.0.0.0/8 -p IPAddressAllow=172.16.0.0/12 -p IPAddressAllow=192.168.0.0/16 curl -m 6 https://example.com` must fail, and the same to the sender's address must connect. **First run, 2026-10-08:** the curl timed out (exit 28), a TCP connection to the sender's port succeeded, and the same curl without the rules answered 200. What that does not show: whether it was the name lookup or the connection that was stopped. To tell them apart, run it once more with an address on the internet in place of the name (`curl -m 6 http://1.1.1.1`): that must also fail.
N10. **Sound (built 2026-10-08, never heard).** System > NDI input: "Play a source's sound" is on. Keep the box's volume low for the first try. Each part by itself; write down what you hear, and the page's sound line (channels, rate, loudest, dropped) each time. (a) **The tone.** Play NDI Tools "Test Patterns". A steady 1 kHz tone must come out of the box's output, without clicks or gaps, for five minutes. The page must say 2 channels at 48000 and "loudest at -20 dB". By ear it should be clearly quieter than a clip at the same volume setting (it is an alignment tone, 20 dB under full scale). If you have a meter on the output, or a recorder: the tone's level against a 1 kHz full-scale tone played as a clip should be 20 dB lower; write down the number. (b) **A film.** Scan Converter with a film playing in VLC, people talking in close-up. Lips and voice: in step, sound early, or sound late? If it is off, by roughly how much (a hand clap in the film helps), and is it the same after five minutes as after ten seconds (drift)? Compare the loudness with the same film played as a clip on the box at the same volume: about the same, or much quieter (20 dB quieter means the sender puts full scale at NDI's reference level, and `AUDIO_HEADROOM_DB` in `pvj/ndi.py` is the constant to change)? (c) **Delay.** Clap in front of the sending computer's microphone if it sends one, or watch the sender's own level meter against the box's sound: about how long after the sender does the box sound? (d) **The box's own controls.** Volume up and down, mute and unmute on the panel while the film plays: they must act on the NDI sound as on a clip. If the box has more than one sound output, choose the other on the Sound page: the NDI sound must move with it. (e) **The sender quits and returns.** Quit Scan Converter while it plays: the sound must stop (no buzz, no loop of the last moment). Start it again: picture and sound return by themselves, in step. (f) **Another source.** Play Test Patterns, then the film, then Test Patterns: each time the right sound, and never the sound of the one before. (g) **The switch.** Switch "Play a source's sound" off while the film plays: the picture goes for a second or two and returns silent, and the page says "Sound is off". Switch it on: sound returns. (h) **A source without sound**, then with: play a sender that sends no sound (Scan Converter with nothing playing), then start the film: the picture goes for a second or two once, and returns with sound. (i) **The picture with sound against the picture without**: with sound on, repeat N6's 1764 x 992 run for five minutes and compare shown, dropped and the helper's share of the processor with the same run with sound off; the sound must not cost frames. (j) `journalctl -u pvj-ndi -n 50` after all of it: send any line about audio. **mpv's own words are not in the journal** (the player starts mpv with no terminal output), so listen to them directly: in a second terminal, before you play, run `sudo python3 -c 'import socket,json,sys; s=socket.socket(socket.AF_UNIX); s.connect("/run/pvj/player/player.sock"); s.sendall((json.dumps({"command":["request_log_messages",sys.argv[1]]})+"\n").encode()); f=s.makefile(); [print(l.strip()) for l in f if "log-message" in l]' warn` and leave it running (Ctrl-C ends it); it prints every warning and error mpv has while you test. Send what it printed. For mpv's own view, with the python3 line of N6: `audio-params`, `avsync`, `audio-pts`, `current-ao`. (k) **The player's queue at 1080p.** With the listener of (j) running, play a 1080p source with sound for five minutes (1080p60 if a sender has it). The line to watch for is **"Too many packets in the demuxer packet queues"**, followed by one line for video and one for audio with their packets and bytes: it means the picture waiting for its sound filled the queue the player was given (119 MiB at 1080p30, 238 MiB at 1080p60), and the sound will have cut out at that moment. It must not appear. If it does, send the three lines and what the page's sound line said. Also from the listener: "Excluding codecs is deprecated" is expected once for each of the two decoders and is harmless; "Failed to initialize a decoder" must NOT appear (it would mean the narrowing to raw video and float PCM refuses the helper's own stream on this mpv: no picture or no sound). And with the python3 line of N6, while it plays: `vd` must answer `rawvideo,-` and `ad` must answer `pcm_f32le,-`; after playing a clip both must answer an empty string, and the clip must play with its sound (the narrowing was taken back). (l) **Corrections of the count.** The helper cuts sound, or puts silence in, to keep the sound at its clock; each time is counted as `adjusted` in what `GET /api/ndi` answers (`playing.audio.counts`; ask as in step R3b, with `/api/ndi` in place of the preview). Over ten minutes of the film write down `adjusted`, `filled` and `dropped` at the start and the end, and say whether a tick or a skip was heard, and how often. One at the start is expected. One every few minutes means the sender's clock and the box's differ; many a minute is a fault: send the figures. (m) **The switch really goes back to bare frames.** With sound on and a source with sound playing, the python3 line of N6 with `file-format` must answer `mkv` (or `matroska`); switch sound off, wait for the picture to return, and it must answer `rawvideo`.
N11. **A silent helper and the player.** Play a source, and while it connects run `sudo systemctl kill -s STOP pvj-ndi` (the helper frozen: the pipe exists, nobody writes). The panel must stay usable; play a clip: it must start. Then `sudo systemctl kill -s CONT pvj-ndi`. Do this on the mpv the box really has (`mpv --version`; Bookworm has 0.35, Trixie 0.40): mpv's current source opens a file for reading without blocking and waits in a way a new command interrupts, but that was read from its master branch, not tried, and not checked for 0.35. If a clip does not start, write down the mpv version; the panel then needs a timeout of its own.
N12. **A still source.** Play NDI Tools "Test Patterns" (one picture, then only a tone). The picture must appear and stay; the page must say "On the screen, a still picture" after two seconds and never "waiting"; `journalctl -u pvj-web` must show no "not shown again" lines over ten minutes. Whether mpv draws a single frame from the raw pipe at all is not known: if the screen stays black with a still source and works with a moving one, say so. **2026-10-08, what is known.** At the first run play answered 1920 x 1080 at 29.97, the helper said "still" with shown 1, and mpv had the pipe open as raw video with `vo-configured` true but `time-pos` 0.0, `estimated-frame-number` 0, `core-idle` true, and a snapshot failed (503, "mpv: error running command"). That evening a camera pointed at the monitor showed the test pattern on it: **the still picture is on the screen**, on that build, with bare frames. Still to do here: the ten minutes and the journal check; the same with sound on (the stream is then Matroska: is the pattern on the screen, and the tone heard, N10); and **why the snapshot fails**, which is now answered by the panel as "no snapshot of this NDI source could be made ... the picture on the screen is not affected" and is not understood. To find out, with the still source up: (1) mpv's own words. They are NOT in the journal (the player starts mpv with no terminal output; an earlier version of this step sent you to `journalctl -u pvj-player`, which shows nothing of them). Run the listener of N10 (j) with `v` in place of `warn` in a second terminal, then "Take snapshot" on Pads, and send the lines that follow (what does mpv say about the screenshot?); (2) the same line with `["screenshot-to-file","/run/pvj/player/t.jpg","video"]` and then with `"window"` in place of `"video"`: does one of the two work? (3) `["frame-step"]` and then the snapshot again; (4) the snapshot while a moving NDI source plays (it worked at the first run: 200, 156 kB), for comparison. Send what each printed.
N13. **An interlaced sender**, if one can be found (a camera or a converter set to 1080i): the page must say "the source sends interlaced video, which this input does not show". Write down what the journal shows; which of the three interlaced frame types really arrive is not known.
N14. **The bounds.** While 1080p60 plays: `systemctl show pvj-ndi -p MemoryCurrent -p TasksCurrent -p CPUUsageNSec` twice a minute apart. If memory is near 512M, tasks near 96 (64 on a box whose unit is older than 2026-10-12; with one sound source for the box about 52 are expected), or the helper is held at 300 percent while frames drop, the figures in the unit (chosen by reasoning) are wrong for this board: send the numbers. Check `findmnt /tmp` from inside the unit (`sudo nsenter -t $(systemctl show -p MainPID --value pvj-ndi) -m findmnt /tmp`) shows a tmpfs of 8M, and that discovery still works without localhost in the allow list. **First run, 2026-10-08:** `MemoryCurrent` said "[not set]": memory accounting is not on for this unit's group on that box, so the figure cannot be read that way (and whether `MemoryMax` is enforced there is not known either: `cat /sys/fs/cgroup/system.slice/pvj-ndi.service/memory.max` says, if the file exists). Read the helper's memory from the process instead: `grep VmRSS /proc/$(systemctl show -p MainPID --value pvj-ndi)/status` (50 MB on the first run, with 25 of 64 tasks). **Watch for growth with each change of source:** the helper was at 50 MB before many changes of source and at 108 MB an hour and many changes later (flat during a five minute run; mpv 409 MB, flat). Note the figure, play ten different sources or change the sender's size ten times, note it again; do that three times. If it climbs each time, send the three figures.
N15. **Off means off.** Switch the module on, open the page (the journal says "loaded the NDI runtime"), switch it off: `journalctl -u pvj-ndi` shows "switched off; starting again without the NDI runtime" and a new start, and `sudo grep -c libndi /proc/$(systemctl show -p MainPID --value pvj-ndi)/maps` is 0.
N16. **Many senders and a second machine with the same name**, if two computers are to hand: give both the same NDI name; the page must list two rows with different addresses, and playing one must stay with it when the other starts or stops.

N17. **A box that did not opt in shows nothing of NDI on the network.** On the box before N2 (or on a second box that never opted in, or after N18's `remove`), with the NDI module switched ON in the panel, which is the worst case: `pgrep -af pvj-ndi` finds nothing; `sudo ss -lntup | grep -E ':(59[6-9][0-9]|6960|7960)'` shows nothing listening on NDI's ports; `sudo ss -xl | grep ndi` shows no socket. Then watch the wire for two minutes while a sender is running on the same network and someone opens the NDI page: `sudo tcpdump -ni any -c 200 'udp port 5353 or tcp portrange 5959-5970'`. Nothing in it may be a question from this box for `_ndi._tcp`, and there must be no connection from this box to the sender's port 5960 and up. (Raspberry Pi OS runs `avahi-daemon`, so the box answering to its own name on port 5353 is expected and is not NDI; write down whether `avahi-daemon` was on the box before anything of this project was installed: `dpkg -s avahi-daemon`.) From the sender's computer, NDI Studio Monitor or `avahi-browse -t _ndi._tcp` must not list this box (it never should: the helper only receives). For comparison, do the same capture once after N2 with the module on: now the questions for `_ndi._tcp` are there. **First run, 2026-10-08:** `avahi-daemon` was already installed and running on that image before the opt-in. With the module on, the helper holds UDP sockets on port 5960 (four lines in `ss`); after `remove`, or with the module off, there must be none.
N18. **An update keeps the opt-in; remove takes it off.** After N2 to N16: install an update the way the box gets them (a bundle through the panel, and once `sudo install/install.sh` by hand). The installer says "this box has NDI set up: writing the NDI helper's unit again"; afterwards `systemctl is-enabled pvj-ndi` is still enabled, `systemctl is-active pvj-player pvj-web pvj-ndi` says active three times, and a source still plays. Then `sudo pvj-ndi-runtime remove` with a clip playing: it says what it leaves (the account, the group, avahi); the screen goes dark for a moment and the clip can be played again; all the checks of N0 hold again except that `id pvj-ndi` still finds the account; the page says "NDI is not set up on this box"; N17 holds again. Install an update once more: the installer says nothing about NDI and the box stays not set up. Before the `remove`, once: `sudo pvj-update rollback` to the release before (one that has the NDI input), and `systemctl show -p ExecMainStartTimestamp pvj-ndi` must be newer than before the rollback (the helper was restarted with the panel). If a release from before the NDI input is on the box, roll back to that too: `systemctl status pvj-ndi` must say the condition was not met, and `journalctl -u pvj-ndi --since -1min` must not fill with failed starts; then update forward again. Opt in again with N2's command if the box is to keep NDI.

N19. **Root's pipe (the review of 2026-10-11, M3).** After N2: `ls -ld /run/pvj-ndi-pipe` says `drwxr-x--- root pvj-ndi`; `ls -l /run/pvj-ndi-pipe/ndi.fifo` says `prw-rw---- root pvj-ndi`; `sudo -u pvj-ndi rm /run/pvj-ndi-pipe/ndi.fifo` and `sudo -u pvj-ndi ln -sf /etc/passwd /run/pvj-ndi-pipe/ndi.fifo` are both refused ("Permission denied"); N6 still plays; after a reboot the folder and the pipe are there before the helper starts (`journalctl -b -u systemd-tmpfiles-setup`). Then, as root, `chmod g+w /run/pvj-ndi-pipe` and press Play: the page must refuse with "the NDI helper's pipe is in a folder that is not root's alone"; `chmod g-w` and it plays again.
N20. **To try, not set blind: tighter sandbox lines.** One at a time, in a drop-in, with N6 after each: `SystemCallFilter=@system-service`, then `MemoryDenyWriteExecute=yes`, then `ProcSubset=pid`. A line under which the library no longer finds senders or plays is written down as refused, with the journal's words, and taken out.
N21. **To try: the box's own address denied to the helper.** The helper can reach the box's own LAN address (inside the allowed private ranges). In a drop-in: `IPAddressDeny=<the box's LAN address>` (`ip -4 -o addr`), then N6 and the address list of N9 (a sender on the same box, if any, is expected to vanish): if the picture still plays and discovery still works, `pvj-ndi-runtime refresh` is the place to write that line at each install and update (D62, "the trust boundary"); if not, say what broke.
N22. **An NDI pad with a real sender (never run).** NDI input on, a sender on the network. Play > Pads, Edit pads, a pad, NDI source: the sender is listed with its address and "on the network now"; choose it. The pad says NDI and the name. Press it: the picture comes within a few seconds, the pad is lit, the strip says "NDI: name". Press a clip pad: the clip plays (with a crossfade set: from the still, or a cut). Press the NDI pad from a MIDI controller and by OSC `/pvj/pad/1/<n>`: it shows, and the controller's pad lights. As a guest: the pad is greyed with "Operator", and a tap says to ask an operator.
N23. **The sender pulled off the network mid-play, and a second sender with the same name (never run).** While the pad plays, pull the sender's network cable: the page says the connection dropped; press the pad: "NDI source 'X' is not on the network now", at once (under a second, not 14). Bring it back and press: it plays. Start a sender with the SAME name on another computer and stop the first: the pad refuses and names the other address. With both up: the pad plays the first one only. On one computer, two senders with one name (two instances): the pad refuses with "the box does not pick one". Restart the sending program and press at once: note whether the library lists the old and the new port together for a few seconds, in which case the pad says the same until the old one goes.
N24. **A sender in another group is not listed (never run).** On the sending computer put the sender in a group "Leyline" only (NDI Access Manager, or the program's own setting). With no group set on the box: it is not listed. Add the group "Leyline" on the NDI page: it appears within seconds and the page says "Looking in the group Leyline". Type it as "leyline": note whether it still appears (the SDK does not say whether names fold case). A sender moved from Public into the box's group appears; one moved out disappears. A pad that holds a sender now outside the group says it is not on the network.
N25. **A discovery server (never run).** After an update and a restart: `ls -ld /run/pvj-ndi-config` says `drwxr-x--- pvj-web pvj-ndi`. Run NDI's Discovery Server on another machine; point the sender at it. Add its address on the NDI page: `/run/pvj-ndi-config/ndi-config.v1.json` holds it, `journalctl -u pvj-ndi` shows the helper starting again, the page says the server is in force, and the sender (which no longer announces by mDNS) is listed. Remove the server: the sender disappears. `sudo -u pvj-ndi touch /run/pvj-ndi-config/x` must fail. Note whether a second sender that still announces by mDNS is listed too (expected: yes, a finder uses both).
N26. **What only a network shows about mDNS (never run).** With a discovery server set on the box and on every sender: confirm with `avahi-browse -a` on another machine that the senders are no longer announced, and that a sender NOT given the server is still found by the box. This is the measure of how far "lock the NDI network" goes.
N31. **An update over the set-up the test Pi has now (the review of 8cfde96; run in CI's real-install job with a stand-in, never on a Pi).** The Pi was set up for NDI by an earlier state of this branch. Install this release over it WITHOUT restarting the box: `ls -ld /run/pvj-ndi-pipe /run/pvj-ndi-config` shows both (root:pvj-ndi and pvj-web:pvj-ndi, 750), `ls -l /run/pvj-ndi-pipe/ndi.fifo` shows the pipe, `systemctl is-active pvj-ndi` says active, and a source plays. If not: note what `journalctl -u pvj-ndi -n 30` says, then restart the box and try again.
N27. **The discovery server starts on the Pi (never run; NDI's program has never been run by this project).** `sudo pvj-ndi-runtime server install "<the NDI SDK folder>"`: it prints the sha256 (compare with `sha256sum` of the SDK's `bin/aarch64-rpi4-linux-gnueabi/ndi-discovery-server`), `ls -l /opt/pvj-ndi-server` says `-rwxr-xr-x root root`, `id pvj-ndisc` shows no other group, `systemctl is-active pvj-ndi-discovery` says active, and `pgrep -f ndi-discovery-server` finds nothing yet. Setup, NDI discovery server, switch on: the page says "Running: listening on <the wired address>, port 5959"; `pgrep -af ndi-discovery-server` shows exactly `-service -bind <address> -port 5959`; `ss -ltnp | grep 5959` shows it bound to that address only, not 0.0.0.0. THIS IS THE FIRST THING TO CHECK: whether NDI's program honours `-bind` and `-port` in service mode is not known. If `ss` shows 0.0.0.0 while one network was chosen, switch it off and report (the supervisor stops it only if the program itself prints the other address). Note the exact "Listening..." line the page shows. `journalctl -u pvj-ndi-discovery -n 30`: note every line the program prints, and whether any sandbox line stops it (if it does not start: remove `ProtectProc`, then `RestrictAddressFamilies`, then `IPAddressDeny`/`IPAddressAllow`, one at a time in a drop-in, and note which).
N28. **A sender and the box's own input through it (never run).** In NDI Access Manager on the sending computer set the discovery server to the line the page shows; with `avahi-browse -a` on a third machine confirm the sender is no longer announced. On NDI input switch on "Use this box's own discovery server": the helper starts again, the page says the server is in force, and the sender is listed and plays. Switch the discovery server off: note how long the sender stays on the list, and what the sending program does without its server.
N29. **The network pulled, and the address changed (never run).** While it runs, pull the box's cable: within ten seconds the page says the wired network has no address and the server is stopped; `pgrep` finds nothing; it has NOT moved to the Wi-Fi. Plug in again: it runs again. Give the box another address (a new lease, or Setup, Network): within ten seconds the program runs with the new `-bind` and the page's line shows the new address; the sender, still set to the old address, no longer registers, as the page warns.
N30. **What the sandbox keeps from it, and what a crash does (never run).** `sudo -u pvj-ndisc cat /var/lib/pvj/settings.json`, `ls /run/pvj/web`, `ls /run/pvj-ndi`: all refused. `sudo kill -9 $(pgrep -f ndi-discovery-server)`: the page says it stopped and is being started again, it is back within a few seconds, and the picture, the panel and the NDI helper never noticed. Kill it five times in a row: the pause between starts grows. From another machine: `nc <box> 5959` connects from the LAN; note what, if anything, an unregistered client can read (a discovery server is not authentication).

Send back the table from N6 and N7, anything in N3 that had to change, and what N2, N17, N18 and N19 printed, and the outcome of N20 and N21. Only then may any document say what the NDI input manages on a Pi 4.

### Screen output (D90): never run on a Pi; do these before the next show

Everything here was written against mpv's own source and run in CI without a screen: no mode has been set on a real
screen by this code. **After the update the box drives the owner's monitor at 1920 x 1080 at 60 Hz, not 2560 x 1440
at 75.** Before: `journalctl -u pvj-player -b | grep "screen output"` after each start says what was chosen and why;
`cat /run/pvj/player/screen.json` has the whole list. For the numbers: `GET /api/status` (`screen`), `GET /api/screen`
(`display` is what mpv says it draws at), Setup > Health (dropped frames), and the times the journal prints for a
crossfade's still. Clips: one 1080p H.264 at 29.97, one at 25, one 720p at 30.

| # | Do | Look for | Send back |
| --- | --- | --- | --- |
| S1 | `mpv --no-config --drm-mode=help` on the box (player stopped and running), for each screen you have: the 1440p monitor, the Epson X27 or X39, any 1080p TV | A list per connected screen; the player journal's "screen output" line names the card and connector: on a Pi 4 `--drm-device=/dev/dri/card1 --drm-connector=HDMI-A-1 --drm-mode=...` (D91; never a number before the connector's name), and no line "has no screen" in `journalctl -u pvj-web` | The whole list for each, and on a Pi 5 or any board with two cards (HDMI and composite or DSI): that the HDMI screen was chosen and its card number is the one mpv lists (it is the test data: the tests here were written from the kernel's usual order and the Epson's spec sheets, not from a real list), and what Automatic chose (Setup > Screen output) |
| S2 | The update itself, with the 1440p monitor attached | The player restarts once; the monitor says 1920 x 1080 at 60 Hz in its own menu | What the monitor's menu says, and whether the picture is sharp (scaled once, by the monitor) |
| S3 | Dropped frames at 1080p60 and at 1024 x 768 at 60: each clip for two minutes, then a shader, an effect, the mapping on, a crossfade between two pads | Setup > Health's dropped frames; the journal's crossfade lines | Dropped frames a second for each, and the crossfade's still and step times, beside the 2560 x 1440 numbers in D28, D58 and pvj/SHADERS.md (8.4 to 12.8 ms just to scale to 1440p) |
| S4 | Judder: a slow pan at 29.97 and at 25 on 60 Hz, then on 50 Hz; then the same with display-resample: `sudo systemctl edit pvj-player`, and in the drop-in an empty `ExecStart=` and then the `ExecStart=` line that `systemctl cat pvj-player` shows with `--mpv-arg=--video-sync=display-resample` put before `serve`; restart the player, and remove the drop-in afterwards | A pan that moves evenly | Which of the eight looked even. Display-resample is NOT built; this is to decide whether it should be |
| S5 | Setup > Screen output: choose 1920 x 1080 at 50 Hz, "Try it" | The question in place first; then the screen dark for a few seconds; then "Keep this mode? Going back in 15 s" counting down | How long the screen was dark, and whether the count started only when the picture was back |
| S6 | S5 again and do nothing | Back to Automatic by itself after 15 s, with the line "Nobody kept ..." | That it came back |
| S7 | S5 again and press Keep; restart the box | After the restart the screen is still at 50 Hz | |
| S8 | Choose a mode the projector says it cannot show (on the monitor, the 75 Hz one; an X39 at 1920 x 1080) and do nothing | Black or "out of range" on the screen, back by itself after 15 s | That it came back |
| S9 | Boot with the screen OFF or unplugged, then switch it on | As before D90: the player takes the screen's own mode when it comes (the journal says "no screen is connected") | What the screen shows, and whether Restart player (Setup > About and power) then brings Automatic's mode |
| S10 | Hot-plug: unplug the monitor while it plays, plug the projector in | Note what happens. Nothing here follows a hot-plug: mpv keeps the mode it has until the player restarts | What the projector shows before and after Restart player |
| S11 | The Epson X27 or X39 at 1024 x 768 (Automatic) against 1920 x 1080 at 60 (by hand): the test pattern in each | At 1024 x 768 the grid's squares are square, the red border is on every edge and the words say "1024 x 768 at 60 Hz (Automatic)" | A photo of each: at 1080p the projector shows 1024 x 576 with bars and scales twice; whether that is visibly softer is the reason for Automatic's choice |
| S12 | The test pattern on each screen | A red line one pixel wide at the very edge on all four sides; squares in a grid; the words at the top left | Which edges are cut off (overscan): the TV or projector's own setting, not the box's |
| S13 | A mapping made on the 1440p monitor, then the X39 at 1024 x 768 | The mapping page says the mapping was made on another shape and asks for a check; every surface keeps its place on the screen, squeezed sideways | A photo; whether the squeeze matters for the owner's real mappings |
| S14 | `sudo -u <the display account> cat /var/lib/pvj/screen-mode.json` and `ls -l /var/lib/pvj/screen-mode.json /run/pvj/player/screen.json` | The first is readable by the player (0640, group pvj); the second 0640 too | The listing |

### Automatic picture detail for shaders (D93): only eyes can judge these

The numbers behind it were measured on the test Pi 4 at 1920 x 1080 at 60 Hz on 2026-10-16 (nobody watched a screen).
What is not known is whether 720 lines look better than 540 on the room's own screen, which is the reason to draw at
720. Before: Setup > Shaders and Vibes > Picture detail should read Automatic (on the test box it reads 720 lines with
"saved by an earlier version": press Use Automatic first).

| # | Do | Look for | Send back |
| --- | --- | --- | --- |
| A1 | On the projector at its own Automatic mode (D90), play nxlx-silk, nxlx-lattice, nxlx-grid and nxlx-halftone, each under Automatic (720 lines) and then with Picture detail at 540 lines by hand, from where the audience stands | Fine lines, dots and edges: sharper at 720, or no difference you would notice | For each: 720 better, the same, or 540 better. If the same everywhere, 540 for all would leave the GPU more room |
| A2 | The same four on the 1440p monitor set to 1920 x 1080 | As A1, close up | As A1 |
| A3 | nxlx-tide and nxlx-bloom under Automatic (720 lines; the GPU was 87 to 90 percent busy there) with an effect on over them, then with the mapping on | The load line beside the shader on the Shaders page; dropped frames in Setup > Health | Dropped frames a second for each. If they drop, these two may need 540 the way two effects got it in D58 |
| A4 | Vibes on Ambient under Automatic for 20 minutes | No shader marked "Too heavy on this box"; nxlx-drift and nxlx-nebula now come round too, at 540 lines | Any shader marked, with its rate |
| A5 | nxlx-drift and nxlx-nebula at 540 lines (the GPU was 97 percent busy) | Their slow clouds: smooth, or an occasional hitch | Whether they look smooth |

### Render nodes (D94): never run with a real render node

What runs is the loopback tests. Needs: the Mac with Max 9 and jit.ndi installed (tools/max/README.md), the box
with NDI set up (D62), both on the same network. Before: Setup > Render nodes > Add, Tool "Max/Jitter (the nxlx
patch)", port 7400, its picture `nxlx-render-node`.

| # | Do | Look for | Send back |
| --- | --- | --- | --- |
| RN1 | Open `tools/max/nxlx-render-node.maxpat` in Max 9 | It opens; the Max window has no red lines; the world window shows a torus | Every error Max prints, word for word |
| RN2 | On the box's NDI input page | `nxlx-render-node` from the Mac is listed | Its name and address as listed |
| RN3 | Send a cue: Go to scene 1, 2, 3; Set a parameter `spin` 0.25; Set a colour `bg` 0 0 1 | The shape and the colours change on the Mac; the Max window prints what came in | Which cue did nothing |
| RN4 | Cue timing: tap a pad with Go, 20 times, watching the Mac | Each tap changes the shape once, at once | Roughly how long from the tap to the change; any tap lost |
| RN5 | A cue with "Then show": the box's screen | The Mac's picture on the box within a few seconds; the chip says its picture is on the screen | Seconds from the tap to the picture; frames dropped (Setup > Health) |
| RN6 | The fallback: a clip plays, show the node's picture, then quit Max (or pull the Mac's network cable) | Within about 5 seconds the clip is back, never black or a frozen frame for longer; the page says why it went back | Seconds until the clip came back; what the screen showed meanwhile; the words on the page |
| RN7 | The same with Max left running and its patch closed (the sender gone, the machine there), and with the patch frozen (stop the world) | Back to the clip; for the frozen one, after the still time (10 s) and 3 more | Seconds; whether a still picture from Max ever counted as gone when it should not |
| RN8 | A node by `.local` name instead of the address; then a node on another network (a Wi-Fi the box is not on) | The `.local` node takes cues; the other is refused when added and says why | The words |

### pvj-render, the box's own renderer (D95): never run on a Pi; do these before anyone chooses it for a show

Only the spike ran on the Pi (two clips at 960 x 540 held for 10 minutes; 720 held 2 minutes with 3 misses; 1080 did
not). pvj-render itself has run only headless, on a PC's software GPU. Before: the box updated to this release, and
`ls -l /opt/pvj/current/render/pvj-render` shows the program (if not: `cat /opt/pvj/current/render/build.log`).
Use one 1080p30 H.264 clip (the owner's own, or the spike's). Measuring: stop the player (`sudo systemctl stop
pvj-player`), run as the display's account `/opt/pvj/current/render/pvj-render --socket=/tmp/r.sock --drm-device=/dev/dri/card1
--drm-connector=HDMI-A-1 --drm-mode=1920x1080@60.00 --detail=540 --fast --every-vblank`, load the clip over the socket
(`echo '{"command":["loadfile","/path/clip.mp4"]}' | socat - /tmp/r.sock`, then `["set_property","loop-file","inf"]`),
and read `["get_property","pvj-render"]` (missed-vblanks) and `["get_property","frame-drop-count"]` after each run.
Start the player again afterwards.

| # | Do | Look for | Send back |
| --- | --- | --- | --- |
| R1 | One 1080p30 clip at `--detail=540`, 10 minutes, `--every-vblank` | missed-vblanks after the first 3 seconds, frame-drop-count, `top` (CPU per core), the GPU's busy share (`/sys/devices/platform/v3dbus/fec00000.v3d/gpu_stats`, as in the spike) | The four numbers and the temperature at the end |
| R2 | The same at `--detail=720` | As R1 | As R1 |
| R3 | The same at `--detail=output` (1080 lines) | As R1. The spike's two clips did not hold at 1080; one may | As R1 |
| R4 | Setup > Screen output > Renderer: New (pvj-render), detail Automatic; play a clip from the panel, pause, seek, Loop, volume, Stop | The page says "Drawing now: New (pvj-render)"; everything as with classic, black after Stop; the player journal says "pvj-render is drawing" | What differed from classic |
| R5 | With New: a mapping (one quad, then a 4 x 4 grid) on Mapping, editing and then shown | The surfaces where they are with classic, to the pixel at the corners; the outlines while editing; masks over it | A photo of each with classic and with New |
| R6 | With New: Show PIN on the display, a join QR code, the test pattern's words, the logo overlay | The PIN screen's text and the QR code as with classic, sharp and in screen space (not warped by a mapping) | A photo of the PIN screen |
| R7 | The fallback: with New chosen, `sudo mv /opt/pvj/current/render/pvj-render /tmp/` and Restart player; then put it back. Then `sudo pkill -x -SEGV pvj-render` twice within a minute | The first: classic draws and the page says "pvj-render is not built on this box" or why; the second: one restart, then classic, and the page says "ended twice in a row (signal 11)". A picture all the time but a few seconds | The page's words and `journalctl -u pvj-player -n 30` |
| R8 | Classic again (the default) after R4 to R7 | Everything as before this release; no pvj-render running (`pgrep -x pvj-render` says nothing) | Yes or no |

### What to send back

The output of checks 1 to 7 and 13, the `journalctl` tail for anything that failed, and a note of what you saw on screen. Say clearly what you did **not** test.

## Safety

- The workflow starts by **manual dispatch only**, and only for the repository owner. It never runs for pushes, pull requests or forks, so nobody else's code reaches the Pi. Keep it that way.
- Under **Settings, Actions, General**, set "Fork pull request workflows" to require approval, and keep the runner group limited to this repository.
- The runner user has no sudo. Anything that needs root (the network helper, USB mounting) is not exercised by this workflow.
- Network tests are deliberately not included: a bad change could cut the runner off. Test the Network module by hand, with a monitor and keyboard on the Pi.
- If the Pi is a shared or public-facing machine, do not register it.
