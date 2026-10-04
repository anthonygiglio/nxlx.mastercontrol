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

### Runtime folders: who owns what in /run (D44)

**Not run on any box yet.** The change that gives each service its own runtime folder was tested only in CI, which cannot start the units. This list is the proof; until someone has run it, nothing may be claimed about how the folders behave on hardware.

One command lists every owner, group, mode and type. Run it at each step below and keep the output:

```
L() { sudo find /run/pvj /run/pvj-sysd /run/pvj-supportd /run/pvj-update -printf '%u:%g %m %y %p\n' 2>&1 | sort -k4; }
A=$(sed -n 's/.*"user": "\([^"]*\)".*/\1/p' /etc/pvj/install.json); echo "display account: $A"
L
```

What `L` must print every time (`A` is the display account; `d` folder, `s` socket, `f` file, `l` link, `p` pipe):

| Path | Owner:group | Mode | Type |
| --- | --- | --- | --- |
| `/run/pvj` | `root:root` | 755 | d |
| `/run/pvj/netd` | `root:pvj` | 750 | d |
| `/run/pvj/netd/netd.sock` | `root:pvj` | 660 | s |
| `/run/pvj/player` | `A:pvj` | 750 | d |
| `/run/pvj/player/player.sock` | `A:pvj` | 660 | s |
| `/run/pvj/player/preview.jpg` (after the panel showed a preview) | `A:pvj` | 660 | f |
| `/run/pvj/web` | `pvj-web:pvj` | 750 | d |
| `/run/pvj/web/pin` | `pvj-web:pvj` | 600 | f |
| `/run/pvj/web/player.sock`, `/run/pvj/web/netd.sock` | `root:root` | 777 | l |
| `/run/pvj/web/shader-*.glsl`, `mapper-*.glsl`, `overlay*.bgra`, `undervoltage-seen` (when in use) | `pvj-web:pvj` | 640 | f |
| `/run/pvj/web/capture.fifo` (while a live input plays) | `pvj-web:pvj` | 640 | p |
| `/run/pvj-sysd`, `/run/pvj-supportd` | `root:pvj` | 750 | d |
| `/run/pvj-sysd/sysd.sock`, `/run/pvj-supportd/supportd.sock` | `root:pvj` | 660 | s |
| `/run/pvj-update` (only after an update from the panel) | `root:root` | 755 | d |

Nothing else may be directly in `/run/pvj`: `sudo find /run/pvj -maxdepth 1 ! -type d` prints nothing.

| # | When | Do | Good |
| --- | --- | --- | --- |
| R1 | Upgrade over the running older version | Before: `L` (expect one shared `/run/pvj`, mode 770, with `player.sock`, `netd.sock`, `pin` in it). Then `sudo install/install.sh`, then `L` | The installer says "moving the runtime files to a folder per service"; `L` matches the table; `systemctl is-active pvj-player pvj-web pvj-netd pvj-sysd pvj-supportd` says `active` five times |
| R2 | After a boot | `sudo reboot`, then `L`, and `journalctl -b \| grep -i cycle` | The table; no "ordering cycle" line; all five services active |
| R3 | It still works across the folders | Play a clip; open the preview; `sudo pvj-pin`; the Network page shows the addresses and "Find networks" answers; play a shader; show a PNG overlay; a mapping if one is set up; a live input if a capture stick is there | Each works. Each one crosses from one service's folder to another's |
| R4 | Player restart from the panel | System > About and power > Restart player now, wait 5 s, `L` | The table. `netd.sock` and `pin` did not change owner |
| R5 | Restart of each unit | For each of `pvj-player pvj-web pvj-netd pvj-sysd pvj-supportd`: `sudo systemctl restart NAME; sleep 5; L` | The table after every one, and R3's play and Network page still work at the end |
| R6 | Stop and start in the other order | `sudo systemctl stop pvj-player pvj-web pvj-netd; sudo systemctl start pvj-web; sleep 3; systemctl is-active pvj-web; L; sudo systemctl start pvj-netd pvj-player; sleep 5; L` | The panel is `active` without the player (it says the player is not running); after the stop `/run/pvj/player` and `/run/pvj/netd` are gone and `/run/pvj` is still `root:root 755`; at the end the table |
| R7 | Reinstall | `sudo install/install.sh; L` | The table; no "moving the runtime files" line this time |
| R8 | The accounts cannot take each other's things | `sudo -u "$A" -g pvj cat /run/pvj/web/pin`; `sudo -u "$A" touch /run/pvj/x`; `sudo -u "$A" mv /run/pvj/netd /run/pvj/n2`; `sudo -u "$A" rm /run/pvj/netd/netd.sock`; `sudo -u pvj-web rm /run/pvj/netd/netd.sock`; `sudo -u pvj-web touch /run/pvj/player/x`; `sudo -u "$A" touch /run/pvj/web/x` | "Permission denied" seven times, and `L` is unchanged |
| R9 | Update from the panel (when a signed bundle is at hand) | Install it from System > Updates, then `L` and R3's Network page | The table; the network helper answers without a reboot |

Send back the output of `L` at every step and the output of R8. If any line differs, stop and send it: do not "fix" an owner by hand.

### What to send back

The output of checks 1 to 7 and 13, the `journalctl` tail for anything that failed, and a note of what you saw on screen. Say clearly what you did **not** test.

## Safety

- The workflow starts by **manual dispatch only**, and only for the repository owner. It never runs for pushes, pull requests or forks, so nobody else's code reaches the Pi. Keep it that way.
- Under **Settings, Actions, General**, set "Fork pull request workflows" to require approval, and keep the runner group limited to this repository.
- The runner user has no sudo. Anything that needs root (the network helper, USB mounting) is not exercised by this workflow.
- Network tests are deliberately not included: a bad change could cut the runner off. Test the Network module by hand, with a monitor and keyboard on the Pi.
- If the Pi is a shared or public-facing machine, do not register it.
