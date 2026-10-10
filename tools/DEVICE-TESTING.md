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

### Controller lights (D53)

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

**Never run on a box or with a real controller.** Before: MIDI is on, a Launchpad Mini is plugged in (or give "Show a one-time presenter code" to a button of another controller on its card), a screen is on the box, one phone is paired with full access and a second phone is not paired.

| # | Do | See on the box's display | See on the paired phone (System > People and codes) |
| --- | --- | --- | --- |
| C1 | With "A code from a controller" off: hold the eighth pad of the top row for 4 seconds, let go | Nothing | "No code has been shown from a controller since the box started." |
| C2 | Switch "Presenter codes from a controller" on (answer the question). Tap the pad | Nothing | The second switch appears |
| C3 | Hold the pad for 4 seconds, let go | The address, "One-time presenter code" with 6 digits, a QR code, "Hides in ... s" counting down. Note whether it reads well from where you stand | Within 5 seconds: "A one-time presenter code is on the box's display now", and End this code. No digits |
| C4 | On the second phone open the address, type the code | The code leaves the display within a second or two | "The last one: a presenter code, just now, used by the device ..." and the phone is in Paired devices as Presenter |
| C5 | Hold again, then press the pad once | The code appears, then goes at the press | "hidden at the controller" |
| C6 | Hold again, wait 2 minutes | The code goes by itself | "ran out unused" |
| C7 | Hold the pad for 15 seconds, let go. Then lay something on the pad for a minute | Nothing either time | |
| C8 | Hold again, then press End this code on the phone | The code goes | "ended from the panel" |
| C9 | `journalctl -u pvj-web -b \| grep "controller code"` on the Pi | | A line for each code shown and each refusal, and no line with 6 digits in it |
| C10 | Other controllers: give the action to a button on the card, repeat C3. On a nanoKONTROL2 say whether the button needs holding or two presses | | |

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
| O8 | Switch that off and "A key in the address" on. Press the control | "Refused: no key in the address" | The clip goes on playing |
| O9 | Press "Show the key", put `/k/<key>` in front of the address in the layout, press the control. Wait 30 seconds | The message is let in, shown as `/pvj/stop` without the key; the key hides itself | The clip stops |
| O10 | "Make a new key" (answer the question), play a clip, press the control with the old key in the layout | "Refused: wrong key" | The clip goes on playing |
| O11 | On a presenter's and a guest's device, open System | No OSC page with lists or a key | Nothing |
| O12 | Write down how the tablet's address looked in O1 and whether it changed during the test (a new address from the router) | | |

### What to send back

The output of checks 1 to 7 and 13, the `journalctl` tail for anything that failed, and a note of what you saw on screen. Say clearly what you did **not** test.

## Safety

- The workflow starts by **manual dispatch only**, and only for the repository owner. It never runs for pushes, pull requests or forks, so nobody else's code reaches the Pi. Keep it that way.
- Under **Settings, Actions, General**, set "Fork pull request workflows" to require approval, and keep the runner group limited to this repository.
- The runner user has no sudo. Anything that needs root (the network helper, USB mounting) is not exercised by this workflow.
- Network tests are deliberately not included: a bad change could cut the runner off. Test the Network module by hand, with a monitor and keyboard on the Pi.
- If the Pi is a shared or public-facing machine, do not register it.
