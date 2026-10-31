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
| X28 | D112. Crossfade set, a clip playing on the box. Play a pad, and kill the panel on purpose during the transition (`sudo systemctl kill -s KILL pvj-web`, then it restarts). Do it on classic mpv and on the New renderer | The panel comes back and the old clip plays again within a few seconds; `ls /run/pvj/web` shows no `transition-frozen.json` | Whether the picture stayed frozen, and `journalctl -u pvj-web` for "an earlier panel froze" or not. The kill must land between the freeze and the load, so repeat it a few times with a heavy clip; a kill outside that window proves nothing |

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
| A4 | Vibes on Ambient under Automatic for 20 minutes | No shader marked "Too heavy on this box"; nxlx-nebula now comes round too, at 540 lines | Any shader marked, with its rate |
| A5 | nxlx-nebula at 540 lines (the GPU was 96 percent busy) | Its slow clouds: smooth, or an occasional hitch | Whether it looks smooth |

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

### pvj-render, the box's own renderer (D95): do these before anyone chooses it for a show

The first run on the test Pi 4 (2026-10-18, project-log/JOURNAL.md) found four faults, fixed since: the socket jammed
(the process had run out of file descriptors: libmpv left a GL fence, a sync file on V3D, at every frame, and Mesa
said `Export failed` at every frame after), a flip only on a new clip frame, and a switch back to classic that did
not take. Before: the box updated to this release, and `ls -l /opt/pvj/current/render/pvj-render` shows the program
(if not: `cat /opt/pvj/current/render/build.log`). Use one 1080p30 H.264 clip (the owner's own, or the spike's).
Measuring: stop the player (`sudo systemctl stop pvj-player`), run as the display's account
`/opt/pvj/current/render/pvj-render --socket=/tmp/r.sock --drm-device=/dev/dri/card1 --drm-connector=HDMI-A-1
--drm-mode=1920x1080@60.00 --detail=540 --fast` (a picture goes out at every vblank; `--every-vblank` is still taken),
load the clip over the socket (`echo '{"command":["loadfile","/path/clip.mp4"]}' | socat - /tmp/r.sock`, then
`["set_property","loop-file","inf"]`), and read `["get_property","pvj-render"]` (flips, drawn, missed-vblanks,
gl-fences, which must stay near 0) and `["get_property","frame-drop-count"]` after each run, and
`journalctl -u pvj-player | grep -c "Export failed"` (must be 0). Start the player again afterwards.

| # | Do | Look for | Send back |
| --- | --- | --- | --- |
| R1 | One 1080p30 clip at `--detail=540`, 10 minutes, `--every-vblank` | missed-vblanks after the first 3 seconds, frame-drop-count, `top` (CPU per core), the GPU's busy share (`/sys/devices/platform/v3dbus/fec00000.v3d/gpu_stats`, as in the spike) | The four numbers and the temperature at the end |
| R2 | The same at `--detail=720` | As R1 | As R1 |
| R3 | The same at `--detail=output` (1080 lines) | As R1. The spike's two clips did not hold at 1080; one may | As R1 |
| R4 | Setup > Screen output > Renderer: New (pvj-render), detail Automatic; play a clip from the panel, pause, seek, Loop, volume, Stop | The page says "Drawing now: New (pvj-render)"; everything as with classic, black after Stop; the player journal says "pvj-render is drawing" | What differed from classic |
| R5 | With New: a mapping (one quad, then a 4 x 4 grid) on Mapping, editing and then shown | The surfaces where they are with classic, to the pixel at the corners; the outlines while editing; masks over it | A photo of each with classic and with New |
| R6 | With New: Show PIN on the display, a join QR code, the test pattern's words, the logo overlay | The PIN screen's text and the QR code as with classic, sharp and in screen space (not warped by a mapping) | A photo of the PIN screen |
| R7 | The fallback: with New chosen, `sudo mv /opt/pvj/current/render/pvj-render /tmp/` and Restart player; then put it back. Then `sudo pkill -x -SEGV pvj-render` twice within a minute | The first: classic draws and the page says "pvj-render is not built on this box" or why; the second: one restart, then classic, and the page says "ended twice in a row (signal 11)". A picture all the time but a few seconds | The page's words and `journalctl -u pvj-player -n 30` |
| R8 | Classic again (the default) after R4 to R7 | Classic draws within about 10 seconds with no restart by hand; the Renderer card says how pvj-render was ended (asked, SIGTERM or SIGKILL); everything as before this release; no pvj-render running (`pgrep -x pvj-render` says nothing) | Yes or no, and the card's words |

### pvj-render phase 2, the renderer's own crossfade (D98): never run on a Pi

Everything of D98 ran on a software GPU in a 320 x 180 headless window. Before: R1 to R8 above done, New chosen on
Setup > Screen output, two 1080p30 clips of the owner's on pads A1 and A2, Mix > Transition between clips set to
**Crossfade**, duration 2 s. For the numbers, read `["get_property","pvj-render"]` over the socket as in R1 (`flips`,
`missed-vblanks`, `drawn`, `back-drawn`, `fade`, `fades`, `fades-done`, `fades-cut`, `gl-fences`, `gl-objects`)
before and after each run; the GPU's busy share as in R1.

| # | Do | Look for | Send back |
| --- | --- | --- | --- |
| CF1 | Play A1, then A2, by eye | A1 goes on moving while A2 comes in over two seconds; no freeze before the fade, no step in it, no flash of black or of an old picture at its start or end; A1's sound stops at the tap and A2's begins with it | Smooth or stepped; anything that flashed; how the sound changed; `sound-held` in the property (must stay 0) and any line "still holds the sound device" in `journalctl -u pvj-player` |
| CF2 | Flips and missed vblanks during a fade at detail **Automatic** (540 on a Pi 4): read the property, play A2, read it again 2.5 s later | `flips` up by about 150 (60 a second, 2.5 s), `missed-vblanks` up by 0 or a few, `drawn` and `back-drawn` each up by about 60 to 75 (30 fps each) | The four differences |
| CF3 | The same at detail **540** | As CF2 | As CF2 |
| CF4 | The same at detail **720** | As CF2. The spike missed 3 vblanks in 2 minutes at 720 with two layers | As CF2 |
| CF5 | The same at detail **output** (1080 lines) | As CF2. Two layers did not hold at 1080 in the spike: missed vblanks are expected here; say how many | As CF2 |
| CF6 | The GPU's busy share during a fade at each detail of CF2 to CF5 (`gpu_stats` read before and after one fade) | Under 100 percent at 540 and 720 | The share at each detail |
| CF7 | 200 fades, with pvj-render run by hand as in R1 and A1's clip loaded: `ls /proc/$(pgrep -x pvj-render)/fd \| wc -l`, then `for i in $(seq 100); do echo '{"command":["pvj-crossfade","/path/a2.mp4",0.5]}' \| socat - /tmp/r.sock; sleep 1.5; echo '{"command":["pvj-crossfade","/path/a1.mp4",0.5]}' \| socat - /tmp/r.sock; sleep 1.5; done` (five minutes), then the count again and the property | The descriptor count the same as before the run (within a few), `gl-fences` near 0, `gl-objects` no higher than after the first ten fades, `fades-done` 200, `journalctl -u pvj-player | grep -c "Export failed"` 0 | The count before and after, the property, the grep |
| CF8 | During a fade press **Blackout**; then **Stop** during another; then a pad during a fade; then choose **Classic** on Setup > Screen output and play A1 then A2 | Blackout: black at once (both pictures); Stop: black at once; a pad: the fade ends and the new clip is cut to; on Classic: D71's crossfade as before (X1 above), with no line "no crossfade on the new renderer" in `journalctl -u pvj-web` | Anything that stayed lit, and whether Classic looked as before |
| CF9 | Out of a generator (a shader pad) into A1, and out of the NDI input into A1, both with Crossfade | D71's crossfade as on classic: the generator's or the NDI picture stands still and melts into A1; for NDI the next NDI source still opens (D62) | Whether both looked as on classic |
| CF10 | Stop, then play A1 with Crossfade | A1 is there at once, as a cut (no rise from black); `fades-from-nothing` up by 1 | Yes or no |

### pvj-render's own wipes and slides (D111)

Everything of D111 ran on a software GPU in a headless window first. MW1, MW2 and MW4 were run at the dongle on 2026-10-31 (`measurements/2026-10-31-native-wipes`); MW3, MW5 and MW6 have not been run on a Pi. Before: New chosen, the same two clips on A1 and A2, a wipe or a slide chosen under the pads, duration 2 s. `fade-kind` and `fade-steps` in `["get_property","pvj-render"]` say which one ran and how many pictures it drew; `mix.fallback` in `GET /api/status` must stay empty.

| # | Do | Look for | Send back |
| --- | --- | --- | --- |
| MW1 | Each of the four wipes and four slides from A1 to A2, by eye at the output | The edge comes from the side the button names, straight, with no softness; the outgoing clip goes on playing; a slide uncovers A2, which does not move; no freeze before it, no flash at its end | Yes or no, and any that runs the wrong way |
| MW2 | 4 sources of 720p clips, mapping on, final Automatic, then final full: the same transitions | At Automatic `fade-steps` about 120 in 2 s; the incoming clip drops about what a crossfade's does at this load (12 to 15 frames a second on the test node: five software decoders); no line "transitions given up", `mix.fallback` empty | Steps, drops, flips, missed vblanks, GPU share at each |
| MW3 | A mapping with a turned surface and a wipe | The wipe runs along the surface, warped with the picture, and nothing moves outside the surfaces. The Owner decides whether this is what he wants: the still ran across the whole screen | Yes or no |
| MW4 | During a slide: Blackout, then the same with Stop, then a pad, then an effect on through it | Blackout: black at once and both pictures come back with the transition still running if it has not ended. Stop: black. A pad: the first ends and the pad's clip comes in. Effect: on both pictures | Yes or no for each |
| MW5 | Out of a shader pad into A1, and out of the NDI input into A1, with a wipe | The still's wipe, as on classic: the old picture stands | Yes or no |
| MW6 | A logo or a mask on, a slide | The logo or mask stays where it is, over both pictures | Yes or no |

**MW1 to MW6, on the node with the renderer's own wipes, 2026-10-31 (Pi 4, 1920 x 1080 at 60 Hz, HDMI through the dongle; `measurements/2026-10-31-native-wipes`):**

| Row | Result | What was seen |
|---|---|---|
| MW1 | Passed, one clip | All eight, 119 or 120 steps each, no refusal, no fallback, no black frame. By eye on the strips: each comes from the side its button names; a wipe leaves the old picture standing; a slide moves the old picture off and the new one stands, both playing. The edge is straight and hard |
| MW2 | Run | 2 s, 4 sources, mapping on. At Automatic (720): wipe 116, 119, 119 steps of 120, slide 116, 120, 120, crossfade 111, 114, 115; 58.0 to 60.1 flips a second; nothing given up. At full: wipe 82 to 92, slide 95 to 100, crossfade 74 to 94; 41.3 to 51.9 flips a second, 33 to 76 missed vblanks, nothing given up. The incoming clip's drops: 24 to 41 at 720 in the wipes and slides against 14 to 24 for the crossfade (59 to 76 against 60 to 63 in 5 s windows inside a 9 s transition). The drops are not mended: 12 to 15 of 30 frames a second while a transition runs, with the box at 99% CPU. Before this build the still gave up every time |
| MW3 | Not run | The look under a turned surface is seen only on the node's own quad and triangle. What was seen: the wipe runs inside the surface and is warped with it, and a surface of another source plays on |
| MW4 | Run | Blackout during a slide, 4 sources: black at HDMI, the transition went on and the picture came back; one strip frame shows the quad dark and the triangle (source 2) still lit, as the level reached source 1 first by at most a third of a second (not chased). Stop during a wipe: off at once, `fades-cut` up by one. A new play during a wipe: the first ended, the new slide running. Effect on through a wipe: 120 steps, on both pictures, the edge mirrored with them |
| MW5 | Not run | The still's path on this build, out of a shader or the NDI input |
| MW6 | Not run | A logo or a mask over a slide |

Also run: 24 transitions in a row, all eight in turn, 4 sources, mapping on, Automatic: 24 done, 0 cut, 59 of about 5870 vblanks missed (1%), `mix.fallback` empty every time, nothing in the journal. Not run: the projector or a monitor, and a long soak (24 in a row is not one).

### Sound reactivity (D96): never run on a Pi or with a real audio input

Before: the box updated to this release; `systemctl status pvj-sound` says active; `arecord --version` answers (if
not: `sudo apt install alsa-utils`). A USB audio input (a class-compliant one) plugged into the Pi, with music or a
metronome into it. The page is Setup > Sound reactivity. It analyses levels only; nothing is recorded.

| Row | Do | Expect | Send back |
| --- | --- | --- | --- |
| SR1 | `journalctl -u pvj-sound -n 20`; then on the page choose the USB input and switch Listen on | The service says "ready"; the device is in the list by its name; the state says Listening; nothing in the journal about a refused system call or memory | The device's name as listed; every line of the journal after the switch |
| SR2 | Play music; watch the meter | The level and the six bands move with the music, the bass bars with the kick, the high bar with the hats; quiet is near 0 | Whether the bars lag behind what you hear (a rough guess is fine); the dB with music at a normal level |
| SR3 | Play a click track or a four-on-the-floor track at a known tempo (120 BPM) for a minute | The Beat chip flashes on every beat; the tempo reads about 120 | How many beats were missed or doubled in a minute; the tempo it showed |
| SR4 | Gain trim: -12 and +12 dB | The bars fall and rise; the sound itself is not changed | Whether the trim was needed for this input, and which value suits it |
| SR5 | Play a shader that has a number input (for example nxlx-silk); under "A shader input that follows the sound" choose that input, the level, depth 0.5, Save, switch it on | The input moves with the music, slowly (at most twice a second); the picture does not stutter more than when a knob is turned | Whether it looks like it follows the music at all; any frame drops (Setup > Health) |
| SR6 | With music playing and Listen on for five minutes: `systemctl show pvj-sound -p CPUUsageNSec` before and after, or `top -p $(pgrep -f bin/pvj-sound)`, and the arecord process beside it | The service under about 10 percent of one core; arecord a few percent | Both numbers; the page's own "uses about N percent of one core" |
| SR7 | OSC: with "Send state back" on and a tablet or a computer as a target, switch "Send sound to the state targets" on; watch `/nxlx/sound/level`, `/nxlx/sound/bands` and `/nxlx/sound/beat` in TouchOSC's or Max's OSC monitor | About 20 bundles a second of the level, 5 of the bands (they move slowly, averaged over 200 ms), and a message at each beat, only to the listed target | Whether TouchOSC takes the bundle (D82's open question); what arrives per second |
| SR8 | Unplug the USB input while Listen is on; plug it back | The page says it is not plugged in; when it is back the box listens again by itself within a few seconds | The words; how long until it listened again |

### Sound sources (D97): never run on a Pi

Setup > Sound reactivity, as the Owner. Under "Advanced: sources and levels sent in" allow the sources first.

| Row | Do | Expect | Send back |
| --- | --- | --- | --- |
| SS1 | A USB interface with two or more inputs: choose it, then Input 1, Input 2 and the mix, with a signal in only one input | The meter moves on the input that has the signal and stays at the floor on the other | The card's name, the number of inputs the page shows, and whether that is right |
| SS2 | Source: the box's own sound; play a clip with music | The meter follows the music (level and beat; no bands, the page says so); the sound in the room does not change or stutter when the source is chosen | Whether the music stuttered once at the switch; how far ahead of the loudspeakers the meter seems |
| SS3 | `journalctl -u pvj-player -n 20` after SS2; then `top` with the box's sound as the source | No error about the filter; the player's CPU not visibly higher than without it | The player's CPU with and without |
| SS4 | Source: the NDI sound, with an NDI picture whose sender has sound (D87) | The meter follows the NDI sound; with no NDI picture the page says none is playing | What the page said in each case |
| SS5 | Source: levels sent in over OSC; from Max/RNBO on a render node send `/nxlx/sound/in/level` and `/nxlx/sound/in/beat` on a metronome | The meter and the Beat chip follow what Max sends; from a machine that is neither a node nor listed, nothing | Whether a sender not listed changed anything |
| SS6 | As an Operator: switch between the allowed sources; through a remote support session try to switch to the capture device | The Operator switches; the session is refused with words, and listening is never switched on by either | The words |

### Projector control: Epson control beside PJLink (D99): never pressed against a real projector from the panel

Before: the X27 (or an X39) on the network and on, its network control on; Projectors on under Setup. Write down anything the panel says in red.

| Row | Do | Expect | Send back |
| --- | --- | --- | --- |
| PX1 | Setup > Projectors > + Add a projector: the X27's address, then **Test** | "PJLink answers: EPSON EPSON X27. Epson control answers (serial ...). Best choice: PJLink and Epson control." and Control set to that | The words; how long Test took |
| PX2 | Add it, wait for On | The row says On, the input, lamp hours; under it Mute (black), Freeze, Brightness, Eco light, Colour mode | Whether the brightness and the mode match the projector's own menu |
| PX3 | Mute (black), then Unmute; Freeze, then Unfreeze, with a clip playing | The wall goes black and comes back; the wall's picture stops while the box plays on, then follows again; the buttons change their words within a few seconds | How long each took, as you saw it |
| PX4 | Move Brightness to about 40 and let go; then back to 125 | The wall dims, then is as before; the number may move a little (the projector's own steps) | The number it settled on |
| PX5 | Eco light on, then off | The wall dims a little and the fan is quieter; back after | Whether you saw or heard a difference |
| PX6 | Colour mode: choose 06, then a mode marked "not tried yet", then back to 04 | 06 changes the colours in about 3 seconds; a mode the projector does not have says so in red and goes from the list | Which modes it took and which it refused, and what each looked like |
| PX7 | Turn off: the question, then Turn off | "Turn off Main? It needs about a minute to cool..."; then Cooling down..., then Turn on | How long it cooled |
| PX8 | Through a remote support session: open Projectors | Turn off is "On (switched off in the room)", no All off, no picture settings; Mute and Freeze work | That a helper could not switch it off |

### Masks: several PNGs combined into one overlay (D100): never run on a Pi

Before: the node on the projector (the X27 at 1024 x 768 is fine), a shader or a clip playing, and three or more test masks in the Library (PNGs, black where no light should fall, transparent elsewhere; at least one with a soft edge). The webcam aimed at the wall for MK1 and MK4.

| Row | Do | Expect | Send back |
| --- | --- | --- | --- |
| MK1 | Shape > Picture > Overlay pictures: add one mask ("Add and show"), then a second and a third | The line says Combining..., then "1 mask on", "2 masks on", "3 masks on"; on the wall each new mask appears with the earlier ones still there; soft edges stay soft | A webcam photo with three on; anything that looked wrong at an edge |
| MK2 | Time a change: add or switch a mask with 8 in the list, at 1024 x 768 and at 1920 x 1080 (`journalctl -u pvj-web` has no timing; use a stopwatch from the tap to the wall) | About 1 s or less for one mask; this branch's estimate is 2 to 5 s for 8 at 1080 on a Pi 4 (0.44 s and 0.74 s on a laptop) | The seconds, per size and count |
| MK3 | Move a mask up and down; switch one off and on; Remove one (the question, then Remove) | The order on the wall follows the list (a later one over an earlier one); off takes it off the wall at once after combining; Remove asks first and leaves the file in the Library | Whether the order matched |
| MK4 | With 3 masks on, play crossfades between two clips (Classic), 10 of 2 s | The masks stay on the wall during and after every fade; no flash of the wall without masks | Webcam burst of one fade; any frame without the masks |
| MK5 | The same as MK1 and MK4 with Setup > Renderer on New (pvj-render), then back to Classic | The same picture on the wall; crossfades keep the masks | Any difference between the two renderers |
| MK6 | Change the screen mode (Setup > Screen output, D90 to D92) with masks on | The masks are drawn again at the new size after the player restarts, fitted and centred | Whether they lined up after the change |
| MK7 | Delete a mask's PNG in the Library while it is on | The others stay; the line says "... is not in the media folder any more (left out)" and the list marks it "(not in the Library)"; nothing repeats in `journalctl -u pvj-web` | The words; the log's tail |
| MK8 | With masks on, draw a mapping (Shape > Mapping) and move a surface | The masks stay where they are on the screen while the surface moves under them | A photo |
| MK10 | The memory cap on the Pi (review of #152): 8 masks at 1920 x 1080, then 4 at 3840 x 2160 if the screen has the mode; then add a PNG that says 16000 x 16000 | Each combines (the cap is 2 GB of address space, 1 MB thread stacks); the big one is left out at once with "larger than a mask may be"; `free -m` shows no swap storm | The seconds, and anything in `journalctl -u pvj-web` |
| MK9 | GPU and frames with 8 masks on over a shader: `vcgencmd` / the health page as in test D | The same cost as one mask (one overlay, as measured on 2026-10-19 with two) and 0 dropped frames | The numbers |

### Sources and surfaces, step 1 (D101): never run on a Pi

Everything of D101 ran on a software GPU in a 320 x 180 headless window. Before: the node on the X27, Setup > Renderer on **New** (its card says 4 sources on a Pi 4), four clips of the owner's at **720p30** and two at **1080p30** in the Library, each of a clearly different picture. Read `["get_property","pvj-render"]` (`flips`, `missed-vblanks`, `gl-fences`, `gl-objects`) and `["get_property","pvj-sources"]` (each source's `drawn`, `path`, `idle`) over the socket as in R1, before and after each run; CPU with `top` (per core); the GPU's busy share as in R1. The webcam aimed at the wall for SO3 and SO4.

| # | Do | Look for | Send back |
| --- | --- | --- | --- |
| SO1 | Four sources at 720p, the X27 at **1024 x 768**: a clip on Source 1 from a pad, and on Sources 2, 3 and 4 from the Sources strip on Shape > Mapping; 2 minutes | `flips` up by about 60 a second, `missed-vblanks` up by few; each source's `drawn` up by about 30 a second; no line "Export failed" | Flips and missed vblanks a second, CPU per core, the GPU's share, the temperature at the end |
| SO2 | The same with the output at **1920 x 1080** (Setup > Screen output) | As SO1. The decode measure says 4 at 720p fit with some room | As SO1 |
| SO3 | Two quads side by side, the left on Source 1 and the right on Source 2 (Shows), with two different clips; then **Edit on the display**, then off | Each quad shows its own clip; their shared edge soft, no line of the wrong picture, no step every 4 pixels; the editing view shows the same | Webcam photos of the edge, shown and while editing |
| SO4 | A crossfade on Source 1 (Mix on Crossfade, 2 s, a pad) while Sources 2 to 4 play, 10 times | Source 1 fades as in CF1; the quads on Sources 2 to 4 go on moving without a stutter; `fades-done` up by 10 | Missed vblanks during the 10 fades; anything that stuttered |
| SO5 | A 1080p clip to Source 2 while Sources 3 and 4 play | The panel says "this may drop frames on this box; 720p clips are lighter"; whether it does drop (`missed-vblanks`, the clip's own `frame-drop-count` cannot be read on a source: watch it) | The words; missed vblanks a second with it |
| SO6 | The fd and GL counts over many loads: pvj-render run by hand as in R1 with `--sources=4`; `ls /proc/$(pgrep -x pvj-render)/fd \| wc -l`; then 300 loads round Sources 2 to 4 (`for i in $(seq 100); do for k in 2 3 4; do echo '{"command":["pvj-source",'$k',"loadfile","/path/a.mp4"]}' \| socat - /tmp/r.sock; sleep 0.5; done; done`), stop them all, and count again | The descriptor count as before (within a few), `gl-fences` near 0, `gl-objects` no higher than after the first 30 loads, the thread count (`ls /proc/PID/task \| wc -l`) as before | The counts before and after |
| SO7 | Blackout with Sources 2 to 4 on surfaces; then Stop (the transport) | Blackout: every surface black at once; Stop: Source 1's surfaces black, the others play on (each stops with its own Stop in the strip) | Anything that stayed lit under Blackout |
| SO8 | The switch back to classic (Setup > Renderer: Classic) with surfaces on Sources 2 and 3 | Classic draws within about 10 s; every surface shows Source 1; the card says Source 2 and 3 "need the New renderer"; switching New on again brings the sources back as surfaces of 2 and 3 (their clips must be played again) | Yes or no, and the card's words |
| SO9 | The source race (D101, open): the owner's mapping with the quad on Source 1 and the triangle on Source 2 (pictures of different colours), then change the mapping three times (edit on and off, Shows on each surface); `pvj-capture` after each | The triangle shows Source 2's picture every time, never black or a dim fixed colour that does not change when Source 2 plays something else | The captures, and whether a dim or black surface came back after loading another clip to Source 2 |

### pvj-render phase 3, step a (D106): the renderer's own generators and effects, never run on a Pi

Everything of D106 ran on a software GPU in a 320 x 180 headless window. Before: the node on a monitor or TV (not the X27 unless it must be), Setup > Renderer on **New**, Picture detail on Automatic. Read `["get_property","pvj-render"]` (`flips`, `missed-vblanks`, `fx-gl-objects`, `gl-objects`) and `["get_property","pvj-shaders"]` (each slot's `state`, `runs`, `drops`, `rebuilds`, `width`, `height`) over the socket as in R1; the GPU's busy share as in R1; CPU with `top`.

| # | Do | Look for | Send back |
| --- | --- | --- | --- |
| FX1 | Every bundled shader for 20 s each through Vibes (or the Shaders page), at the box's Automatic heights | Each slot 0 `state` "on" (never "constants" for a bundled shader), `runs` up by about 30 a second, `drops` about 0, the picture as on classic | Per shader: `drops` a second, missed vblanks a second, the GPU's share; any that did not compile (the panel says the GPU's words) |
| FX2 | The 5 of the 6 that dropped at 720 on classic that are still here (nebula, ridgelines, simplex-noise, aurora, lantern; nxlx-drift is gone) at 540 | As FX1 | As FX1, beside classic's numbers in D93 |
| FX3 | The light shaders at **720** (Automatic gives them 720 at 1080p): bars, silk, tiles, stars for 60 s each | `drops` and missed vblanks near 0; the GPU's share against classic's (at most 52 percent there) | The GPU's share and drops at 720 on New; whether 720 should stay the New renderer's Automatic height for them |
| FX4 | Move a value fast: a MIDI knob or the Shaders page's slider on nxlx-silk for 30 s | The picture follows the knob smoothly (classic: five steps a second), `rebuilds` 0, no stutter | Whether it is smooth; missed vblanks during the 30 s |
| FX5 | An effect over a 720p clip (fx-grade, then isf-rgb-invert), 60 s each | `runs` about the clip's frame rate, the picture as on classic, no stutter | Missed vblanks, the GPU's share, the CPU |
| FX6 | An effect over a generator (fx-kaleido over nxlx-nebula at 540) | As classic's pair; `drops` near 0 | As FX5; whether the effects' floor took the effect off |
| FX7 | Blackout and Fade out with an effect on (isf-rgb-invert over a clip), then with a generator | Black at once under Blackout (never the inverted white), the fade smooth, the level back as it was | Anything lit under Blackout |
| FX8 | A crossfade out of a generator into a clip (Mix on Crossfade, 2 s) | D71's still is the generator's picture (not black, not the carrier's colours), then the clip | Yes or no; a photo if it is wrong |
| FX9 | The panel's preview (the Live page's snapshot) with a generator and with an effect on, watched for a minute (a preview every 3 s) | The snapshot shows the shader and the effect, at the screen's size; no stutter on the screen as each preview is taken | The time of one preview at 1920 x 1080; `missed-vblanks` during the minute against a minute without the Live page open |
| FX10 | 200 changes of shader through Vibes' Next (a controller or the page) and 500 value changes; then the counts | `fx-gl-objects` back to what it was with the same shader on, descriptors and threads as before (`ls /proc/PID/fd \| wc -l`, `/task`) | The counts before and after |

### pvj-render phase 3, step b (D106): chains of effects, never run on a Pi

As the step a rows, with `--sources=4` (the renderer card's 4 sources) and a mapping of four quads, one a source. The chain is put on with `POST /api/effects/chain` (`curl` with the Owner's token, or the browser's console), as the panel has no control for it before step e.

| # | Do | Look for | Send back |
| --- | --- | --- | --- |
| FX11 | Four 720p clips on Sources 1 to 4, one effect on each (fx-grade), 2 minutes | Every quad shows its clip graded; no stutter | Missed vblanks a second, the GPU's share, the CPU, against the same four clips with no effect |
| FX12 | Three effects on Source 2 (fx-grade, isf-rgb-invert, fx-vignette) while Sources 1, 3 and 4 play | The order shows (the vignette dark at the edges after the invert); no stutter | As FX11 |
| FX13 | Blackout, then Fade in, with an inverting effect on Source 3 | Source 3's quad black under Blackout (never white), and up with the others | Anything lit under Blackout |
| FX14 | Stop on Source 2 in the Sources strip with its chain on; then play a clip to it again | The chain is off with the Stop; the new clip plays plain | Yes or no |
### The 2-hour soak's memory, again (D106's soak findings)

| # | Do | Look for | Send back |
| --- | --- | --- | --- |
| SK1 | The soak of 2026-10-24 again: 4 sources at 720p looping, the mapping on, 2 hours; `ps -o rss= -p $(pgrep -x pvj-render)` every 10 minutes | Resident memory flat after the first minutes, well under the 875 MB of before (about 150 MB less a source 2 to 4) | The readings |
| SK3 | New drawing, a clip on source 1: `SIGKILL` to pvj-render twice in a row, then Stop in the panel, then choose New again | Classic takes over and says why; the panel still stops it (no "Permission denied"); New chosen again draws within a few seconds | Yes or no |
| SK2 | Make pvj-render end twice in a row (`sudo kill -9` it twice within a minute), so classic draws; then System > Screen output, New, Use this | The player restarts and New draws again; the card says New | Yes or no |

### pvj-render phase 3, step c (D106): passes and buffers, never run on a Pi

Uploads of ISF files with PASSES (a feedback trail effect and a two-pass blur; any from the ISF library that uses PERSISTENT), on the New renderer.

| # | Do | Look for | Send back |
| --- | --- | --- | --- |
| FX15 | A feedback trail effect over a 720p clip for 60 s | Trails that build and fade as on a desktop ISF host; no stutter; `pvj-shaders` slot 1 "on" (not "8-bit") | Missed vblanks, the GPU's share, `fx-passes-per-second` |
| FX16 | A two-pass blur generator at 540 and at 720 lines | As on a desktop host; the slot's `width` and `height` | As FX15 |
| FX17 | The same uploads on classic | Refused in words that say the New renderer draws them | The words |

### pvj-render phase 3, step d (D106): a generator as a source, never run on a Pi

| # | Do | Look for | Send back |
| --- | --- | --- | --- |
| FX18 | A mapping of two quads, the right on Source 2; a shader pad played to Source 2 (Play to) while a 720p clip plays on Source 1 | The right quad shows the shader, the left the clip; no stutter | Missed vblanks, the GPU's share, `fx-passes-per-second` |
| FX19 | Three shaders on Sources 2 to 4 at once (Automatic heights) | Each quad its shader; how many hold | The same numbers |

### pvj-render phase 3, step e (D106): the chain controls in the Sources card, never run on a Pi

| # | Do | Look for | Send back |
| --- | --- | --- | --- |
| FX20 | Projection mapping > Sources on the New renderer, from a phone and a laptop: an effect on Source 2 (On, its amount, Off), a shader played to Source 3 from its Shader row | The quads change as the card says; the rows fit at 390 px | Yes or no, and a screenshot if a row overruns |
| FX21 | With a shader on Source 2, Shaders > Picture detail to another height; then upload a new version of that shader (replace) | Source 2's shader drawn again each time, at the new detail and from the new file, with its values | Yes or no |

### Run on the test node, 2026-10-24 (FX11 to FX21, SK1)

One projector session on the owner's test Pi 4 with the X27. The script was `nxlx_shared/scripts/phase3_session.py`, run by the coordinator. The results are in `measurements/2026-10-25-phase3/run-20261024-160437/` (results.json, session.log, pixel captures, webcam photos).

**The setup.** The New renderer, detail auto, at the mapping's 1024 x 768 screen, with a 2 x 2 mapping of quads on Sources 1 to 4. The clips were 720p H.264, decoded in software. Effects worked at 720 x 540 and generators at 960 x 720 (Automatic). The projector was on 62.0 minutes and is now in standby. Everything was put back as found.

**What was not read.** The GPU's share: the run has no GPU counter. FX1 to FX10 and SK2 were not part of this session. FX20 ran through the API only, not on a phone or a laptop, so the rows were not seen at 390 px.

| # | Result | Numbers |
| --- | --- | --- |
| FX11 | Ran. Every slot "on", no effect dropped a run. Effects on all four sources cost about a quarter of the frame rate | 4 clips plain: 58.3 flips/s, 207 missed in 122 s, pvj-render 297% of one core, the box 79%. fx-grade on all 4: **42.7 flips/s**, 2115 missed in 122 s, pvj-render 312%, the box 83%, 78.9 passes/s, each slot about 30 runs/s |
| FX12 | Ran. A chain of 3 on Source 2 and fx-grade on Sources 1, 3, 4 | **32.3 flips/s**, 1715 missed in 62 s, pvj-render 323%, the box 87%, 121.8 passes/s; no drops in any slot (slots 2 and 3 at about 31 runs/s) |
| FX13 | Passed | Capture mean 86.2 before, **0.0 under Blackout**, 52.6 after Fade in |
| FX14 | Passed | The Stop left no chain on Source 2; the clip played again plain |
| SK1 | Passed for 44 minutes (not the 2 hours of the row) | **RSS flat at 586 to 589 MB** (about 875 MB and climbing before #169); 58.8 to 58.9 flips/s, about 1.2 missed vblanks/s (328 to 371 per 5 minutes); fds 24 to 26, threads 83 to 86, GL objects 26; 58 to 60 C; the box 78 to 80%, pvj-render 296 to 299% of one core; memory available about 2.97 GB |
| FX15 | Ran. Slot 1 "on" (not "8-bit"), no drops | A feedback trail over a 720p clip: **55.2 flips/s**, 299 missed in 62 s, 46.5 passes/s, pvj-render 313% |
| FX16 | Ran. No program was refused; a few drops | A two-pass blur generator: **59.4 flips/s at 540** (720 x 540, 8 drops in 47 s) and **59.2 at 720** (960 x 720, 11 drops) |
| FX17 | Passed | Classic listed each upload with passes as "it draws in several passes (PASSES), which is not supported on classic mpv (the New renderer draws it: System > Screen output)" |
| FX18 | Ran | One shader (nxlx-silk) on Source 2 at 960 x 720, a 720p clip on Source 1: **59.4 flips/s**, 39 missed in 62 s, 10 drops |
| FX19 | Ran, and did not hold | Shaders on Sources 2 to 4 (silk, bars, tiles) at 960 x 720: **50.5 flips/s**, 867 missed in 92 s, **545 to 570 drops per shader** in 92 s (about one run in five); pvj-render only 51% of one core, the box 15% |
| FX20 | Passed through the API (the calls the Sources card makes) | Effect on, amount 0.4, off, a shader to Source 3: every call 200, the state as sent |
| FX21 | Passed | Source 2's shader was built again on the change of Picture detail and on the replaced upload, with its value (level 0.8) kept |

**What it means.**
- **Effects or shaders on all four sources at once cost a quarter to almost half the frame rate on a Pi 4.** Four effects took 58.3 down to 42.7; a chain of 3 plus three effects took it to 32.3; three generators on Sources 2 to 4 took it to 50.5 and dropped about one run in five. One program on one source costs little: FX15, FX16 and FX18 held 55 to 59.
- **Most likely GPU-bound, not proven.** With four clips decoding, the CPU sat at 79% of the box and rose only to 83 to 87% with the effects, while the frame rate fell by a quarter to a half. In FX19 the CPU was at 15% of the box and the shaders still dropped about one run in five (545 drops against 2450 runs on Sources 3 and 4). Nothing measured the GPU itself, so this is read from the CPU staying flat, not from a GPU counter.
- **For D108's spike.** The program pass draws the whole picture every frame, where an effect here ran about 30 times a second. So expect it to cost at least what one effect costs, not less. Two decks decode two clips, not four, which leaves the CPU room; the GPU is the limit to measure. The spike should therefore measure, at 540 and at 720:
  - deck B decoding while deck A crossfades and the program pass runs, as planned;
  - the same with one effect on each deck.

  Skipping the pass when nothing changed matters more than the design said.
- **For the effects' cost estimate.** It judges one program alone (light, medium, heavy), and every program here was light enough alone. What lost frames was several at once. So a warning needs a box-wide count. One option is the passes a second the renderer already reports (`fx-passes-per-second`), weighted by each program's area. The box would warn as D101's source warning does. From this one run, about 80 passes a second of a light effect at 720 x 540 (four fx-grade) already cost a quarter of the frames on a Pi 4; a threshold needs more than one run to set.

### The overnight soak on the test node, 2026-10-30 (SK3)

10 hours on New: 4 sources at 720p in software, the mapping on, an output of 1920 x 1080 at 60 Hz, 1199 crossfades on source 1, all with a dongle at the real HDMI. Results in `measurements/2026-10-29-soak` on the HP.

| Row | Result | What was measured |
|---|---|---|
| SK3 | **Failed, before this fix** | Classic took over and said why, but its socket stayed owner-only (mode 600), so every panel call got "Permission denied" and New chosen again did nothing. To be run again on the fix |
| SK3 | Passed on master 331e6b6, 2026-10-30 | Killed twice: classic in 0.8 s and said why; socket mode 660; Stop in the panel answered 200; New chosen again answered 200 and was drawing in 3.1 s. Run by `nxlx_shared/scripts/sk3_check.py` |

### The render thread at real-time priority (fix 1 of the playback measurements, 2026-10-30): not yet run on a node

Everything of this ran on a WSL kernel, never on a Pi. Before: the node on a release with this change (the unit is rewritten by the installer or an update, then `sudo systemctl daemon-reload` and Restart player), Setup > Renderer on **New**, 4 sources at 720p30 playing and the mapping on, 1920 x 1080 at 60 Hz. The numbers to beat come from a scratch build in 20 s windows: 40.7 flips a second as shipped, 45.3 with this change, no flip longer than 2 vblanks.

| Row | What to do | What should happen | What it would mean if not |
|---|---|---|---|
| RT1 | `cat /proc/sys/kernel/sched_rt_runtime_us /proc/sys/kernel/sched_rt_period_us; systemctl show pvj-player -p LimitRTPRIO; journalctl -b -u pvj-player --no-pager \| grep "render thread"` | `950000` and `1000000`; `LimitRTPRIO=1`; one line, "the render thread runs at real-time priority 1 (SCHED_RR)", for each start of pvj-render | `-1` as the first number: the kernel's throttle is off and a runaway real-time thread could hold a core; do not ship until that is settled. "stays at normal priority" with a reason: the unit was not reloaded, or the limit did not reach pvj-render |
| RT2 | `ps -L -o tid,cls,rtprio,pri,pcpu,comm -p $(pgrep -x pvj-render)` while 4 clips play | Exactly one thread with class `RR` and rtprio `1`: the one whose tid equals the pid. Every other thread `TS` (or `B`) | A second real-time thread (a decoder, the socket's thread): the flag that keeps children at normal priority did not hold |
| RT3 | `grep Cap /proc/$(pgrep -x pvj-render)/status` | `CapEff` and `CapPrm` all zeros: the right came from the limit, not from a capability | Any capability set: the unit grew a right it should not have |
| RT4 | `q() { echo '{"command":["get_property","pvj-render"]}' \| sudo socat - /run/pvj/player/player.sock; }; q; sleep 20; q`; take `flips` from each (R1 names the fields) and divide the difference by 20 | About 45 flips a second (the scratch build reached 45.3; as shipped 40.7), and the picture unchanged: the mapped quad and triangle where they were, black round them | Not above 40.7: the change did nothing on this build. A picture that differs (a black edge, a stale strip) is a bug in the clear: report it |
| RT5 | During RT4, open the panel from a laptop and click around; `uptime` | The panel answers as usual; the load average is that of 4 decoders, not higher than before the change | A panel that stalls for seconds: the throttle is not holding; stop the player and report the RT1 numbers |
| RT6 | Setup > Renderer: **Classic**, play a clip; `ps -L -o tid,cls,rtprio,comm -p $(pgrep -x mpv)` | Every mpv thread `TS`: classic uses none of the limit | An `RR` thread under mpv |

**RT1 and RT4, on the shipped build, 2026-10-30 (the test node, 1920 x 1080 at 60 Hz):**

| Row | Result | What was measured |
|---|---|---|
| RT1 | Passed | 45.6 to 46.2 flips a second over 12 minutes, counters only, 4 sources at 720p, mapping on, 1080p60. Read today: `950000` and `1000000`; `LimitRTPRIO=1`; 34 lines "real-time priority 1" and none "stays at normal priority" this boot. Those flips are also what RT4 asks for: above the 40.7 of before, and the picture was not looked at |

### The display scaler (D108): counters and the dongle on the test node, the projector not yet

Numbers and what they do not show are in D108. Before: the node on a release with this change, New, mapped surfaces on, 4 clips of 720p ready, a screen or the dongle on the HDMI. `q()` is RT4's. Setup > Screen output, renderer card, is where **Final picture** is chosen.

| Row | What to do | What should happen | What it would mean if not |
|---|---|---|---|
| RT7 | Final picture **720 lines**. Play 4 clips with the mapping on; `q; sleep 20; q`, then watch 60 s of the dongle's capture | About 60 flips a second, `final-lines` 720, `missed-vblanks` hardly moving. At HDMI no more than a few gaps over 2 vblanks a minute. The picture fills the screen with nothing of the unused buffer at any edge | A stale strip at the right or bottom edge: the plane shows more than the corner. Flips below 60: the build differs from the node's measurement |
| RT8 | Final picture **Automatic**. One clip, mapping off; capture while running `pvj-final 720` and `pvj-final full` on the socket every second for 30 s | No flash, no black frame, no frame out of step, and no visible jump of the picture at a switch | A flash or a dropped frame is a bug in the commit. A small jump is the sub-pixel shift of the scaler README (+0.4 px): report it, it is not chased here |
| RT9 | Automatic. Start 4 clips with the mapping on and wait 10 s; stop one source and wait 60 s; capture a wipe in the middle of a switch | The card's line goes to 720 lines with the reason, then follows the rule of D108. No change to the mapped surfaces' place on the screen at either size | Surfaces that move at a switch: HOOKED_size or the viewport is wrong |
| RT10 | At 720 lines look at the owner's mapping with thin lines, on the projector and on a monitor, against **The screen's own size** | Softer edges, and thin lines in squeezed areas may break into dashes. Judge whether Automatic is acceptable for a show | The owner may want the setting left at full for fine mapped work. The edge of a surface may differ from the scratch build's picture (D108, the warp table) |
| RT11 | At 720 lines put the logo, the masks and a text up; `GET /api/mapper` | Masks and the logo where they are at full size; `screen` is the output's | An overlay out of place: something read the draw size |
| RT12 | Unplug the screen and plug in a 720-line one or lower, or set a mode of 720 lines or fewer | The card says "the output has no more than 720 lines", the box plays at the screen's own size, no restart loop | A failed start: the refusal must never stop pvj-render |

**RT7 to RT12, on the deployed build, 2026-10-31 (the test node, master 3d4e9ca, 1920 x 1080 at 60 Hz, HDMI through the dongle; `measurements/2026-10-31-hdmi`):**

| Row | Result | What was measured |
|---|---|---|
| RT7 | Passed | 4 clips, mapping on, 60 s, final 720: 60.0 flips a second, missed 0.0 a second, `final-lines` 720, 3 gaps over 2 vblanks in 60 s, nothing of the unused buffer at any edge (a grey picture reads 43 up to the last column and row at both sizes) |
| RT8 | Passed | Automatic, one clip, a switch every second for 30 s: each switch is one step between two frames, with no flash, no black frame, no torn or extra frame. The visible effect is the +0.4 px step (a change of 27 dB in PSNR on a fine PNG, 49.6 dB on a clip), reported and not chased |
| RT9 | Passed | For the line and the rule: to 720 about 3 s after the 4th source started, with the reason text, and back to full 21 s after source 4 stopped (33 s or more after the switch down, as D108 says). The wipe in the middle of a switch was driven by the socket, not by the rule, which cannot be timed. `/api/mapper` gave `screen` 1920 x 1080 and the same vertices at both sizes; the mapped edges moved 0.01 to 0.60 px |
| RT10 | Not settled | The dongle cannot show the look on the projector or a monitor, or whether the owner's own mapping with thin lines is acceptable. What it shows: thin lines in a squeezed quad break into dashes at 720 and stay whole at full. Needs the owner's eye on a projector. The X27 was not touched |
| RT11 | Passed at the dongle | A 1920 x 1080 PNG with alpha (12 px red frame at the edge, blocks, a bar) and the renderer text over a grey picture: the frame is on rows 0 to 11 and 1068 to 1079 and columns 0 to 11 and 1908 to 1919 at both sizes, the bar at x 100 to 499 (101 to 499 at 720), edges softer at 720. `screen` is 1920 x 1080. The logo is the same thing as a mask |
| RT12 | Not run | It changes the box's display mode (a system setting, with confirm and revert), so it was left; the dongle was also set to 1920 x 1080 only |
| A long run | Not settled | The longest run was 3 minutes |

Also seen: 54 switches on static pictures and 60 on a playing clip showed no black frame beyond the clip's own loop seam. With 4 sources and the mapping on, a wipe or a slide ran choppy (26 to 33 of 60 steps) and the box's guard gave up, the same at full and at 720 (D108 says this already); no black frame in the captures. Not shown: the projector or a monitor, masks other than one PNG, the wipe in the middle of a switch made by the rule rather than the socket, a Pi 3.

### The gap when one NDI source is switched to another (D113): not yet run on a node

Before: the node on a release with this change (the helper and the panel both new), two NDI senders on the network, one 1920x1080@60 and one 1280x720@30 as in `measurements/2026-10-31-ndi-switch`, the HDMI dongle watching the screen. The unit tests prove the order of the calls and the waits with a stand-in for NDI's library and a stand-in for the player; none of this has been run against the real library, mpv 0.40 or a screen.

| Row | What to do | What should happen | What it would mean if not |
|---|---|---|---|
| NS1 | The same 21 switches as before (A and B in turn, 16 s apart, through the panel), sound off (the default) | The longest time with no new frame at the dongle is shorter than the 0.83 s median of 2026-10-31, and the old picture is on the screen until the new one's first frame | The rest is mpv letting go and loading, or mpv shows black when its pipe stands still |
| NS2 | The same, with sound switched on (Setup > NDI), both senders without sound | The first switch to each is as long as before; from the second on about half a second shorter | The wait for sound is still being paid, or a source without sound is not remembered |
| NS3 | Sound on, a sender that has sound (NDI Tools Test Patterns with sound, or a Mac): switch to it, away, to it again | Sound plays after every switch, in step with the picture (`avsync` as before); no switch is slower than the first | A source with sound was taken for one without, or sound starts late |
| NS4 | Sound on: a sender without sound is switched to twice (it is now remembered as silent), then start its sound | Within a second or two the panel opens it again by itself and the sound plays; the next switch to it plays sound at once | Sound that turns up late is lost, or the source stays labelled as silent |
| NS5 | Press a source that is on the list but sends no picture (switch the sender off after the list was read, or a sound-only sender) while another source plays | 409 with the reason on the page, and the screen goes black as a failed press always did; the old picture does not stay frozen | The old picture stands for ever after a failed press |
| NS6 | Press the source that is already on the screen, and press the other source twice quickly | Each ends on the source of the last press; no frozen picture; `journalctl -u pvj-ndi` has no "did not hand the new source over" line | A hand-over lost to a second press |

Results, 2026-10-31, test node on master with D113, mpv 0.40.0 (measurements/2026-10-31-ndi-gap):

| Row | Result | Seen |
|---|---|---|
| NS1 | Passed | 21 of 21. Longest time with no new frame 0.25 s, range 0.20 to 0.32. The old picture holds, then about 0.10 to 0.20 s of black, then the new one: old frame to new frame 0.33 s or 0.47 s by source |
| NS2 | Passed | First switch 0.82 s; from the second about 0.45 s |
| NS3 | Passed, not listened to | Written with a 440 Hz sender of ours. Sound after every switch, nothing dropped, avsync about 0, no switch slower than the first |
| NS4 | Passed | Reopened with sound by itself 1.52 s after the tone began |
| NS5 | Passed, not as written | 409 after 6.3 s with the reason on the page. The old picture holds for those 6 s, black for about 0.6 s, then the old source returns by itself |
| NS6 | Passed | Each ended on the last press; no "did not hand the new source over" line in 25 minutes |

### An effect over a paused clip on classic mpv (D115): not yet looked at on a node

Before: a node running classic mpv (not pvj-render), a clip from the library, the screen watched by eye or by the HDMI dongle. The unit tests prove the order of the commands and a real mpv on a software GPU proves the shown frame passes through the effect (its pass list); nobody has seen a Pi's screen.

| Row | What to do | What should happen | What it would mean if not |
|---|---|---|---|
| PC1 | Play a clip, press Pause, put an effect on (an invert is the plainest), then take it off | The screen shows the effect within a frame or two of the press, and the plain picture within the same time of Off; the panel answers at once, not after 4 s | The seek did not redraw on this mpv or this GPU (0.40, V3D) |
| PC2 | Same, with the effect on: move an amount or a value slider while paused | The frozen picture follows the slider; the clip does not creep forward or back (watch the time) | The seek moves the clip on this file type, or redraws too slowly for a slider |
| PC3 | Same with a long 1080p clip paused deep in it (a few seconds past a keyframe), and with a still picture and an NDI source | The 1080p redraw takes under about a quarter of a second; the picture and the NDI source take the effect at their next frame without an error on the page | A long GOP makes the exact seek too slow on a Pi 4, or a source that cannot seek is upset by it |

### What to send back

The output of checks 1 to 7 and 13, the `journalctl` tail for anything that failed, and a note of what you saw on screen. Say clearly what you did **not** test.

## Safety

- The workflow starts by **manual dispatch only**, and only for the repository owner. It never runs for pushes, pull requests or forks, so nobody else's code reaches the Pi. Keep it that way.
- Under **Settings, Actions, General**, set "Fork pull request workflows" to require approval, and keep the runner group limited to this repository.
- The runner user has no sudo. Anything that needs root (the network helper, USB mounting) is not exercised by this workflow.
- Network tests are deliberately not included: a bad change could cut the runner off. Test the Network module by hand, with a monitor and keyboard on the Pi.
- If the Pi is a shared or public-facing machine, do not register it.
