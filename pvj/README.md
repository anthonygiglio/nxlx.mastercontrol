# pvj: platform layer (Phase 2, work in progress)

Python 3, standard library only. Replaces omxplayer, D-Bus, `tvservice` and `vcgencmd` with mpv and `/sys`.

| Piece | What it does |
| --- | --- |
| `bin/pvj-player` | One command for playback: `play`, `stop`, `pause`, `seek`, `speed`, `volume`, `opacity`, `size`, `position`, `status`, `info` |
| `pvj/hardware.py` | Board, OS, temperatures and display modes from `/proc` and `/sys` (Pi 3, 4, 5, x86, other ARM) |
| `pvj/player.py` | One long-lived mpv controlled over its JSON IPC socket. Changing clips is `loadfile`, so no black gap |
| `bin/pvj-selftest` | Run on each device; prints a JSON report to send back. `--play` also plays a test pattern on the real screen |

## Old command to new command

| Legacy (`dbuscontrol.sh`, `omxplayer`) | New |
| --- | --- |
| `omxplayer --loop file` | `pvj-player play file` |
| `omxplayer file` (once) | `pvj-player play --once file` |
| `dbuscontrol.sh pause` | `pvj-player pause` |
| `dbuscontrol.sh seek <us>` | `pvj-player seek <seconds>` |
| `dbuscontrol.sh rate <x>` | `pvj-player speed <x>` |
| `dbuscontrol.sh setalpha <0-255>` | `pvj-player opacity <0-255>` |
| `omxsizetocenter <percent>` | `pvj-player size <percent>` |
| `omxXposition <x>` | `pvj-player position <x>` |
| `stopall` (player part) | `pvj-player stop` |

## Behaviour differences to know about

- **Opacity** fades to black. mpv cannot blend against other layers, which is right on a black stage background.
- **Position** is in thousandths of the picture width, not omxplayer pixels.
- **Display output**: on a desktop session mpv uses the default output; on a bare console it uses DRM/KMS directly.
- **Pi 5** has no hardware H.264 decode; use HEVC files or expect software decoding.

## Testing

```
python3 -m unittest discover -s tests -t .      # needs mpv; the tests run it headless
bin/pvj-selftest --play --json report.json       # on a real device
```

Not yet done: the old PHP panel does not call `pvj-player`; its audio, overlay and mapper buttons still run the legacy scripts. The new panel (`pvj/web`) and the OSC receiver have their own versions of all three.

## Legacy start scripts

`pvj-player start startlessonce05` runs a legacy preset by its old script name. One table in `pvj/presets.py` replaces ~290 scripts (`startless`, `startseamless`, `startlessonce`, `startlesseronce`, `startmaster`, `startmasterone`, `startmasterusb`, each with an optional two-digit index). `tests/test_presets.py` checks that table against every real script in `sync/`; it matches all but two upstream defects:

- `startless` (no number) is a wrapper that loops every file; the preset does the same.
- `startmaster95` plays `65*` upstream (copy-paste error); the preset plays `95*`.

Not ported yet: slave, stream and wifi presets, network sync between boxes (master presets play locally and print a warning), the "play a slideshow after the video" option, and the audio output flag (`local`, `both`); Pi 5 has no headphone jack, so audio selection needs its own module.

## Running as a service

`install/pvj-player.service` runs `pvj-player serve` under systemd as the account that owns the screen and sound card (`@PVJ_USER@`, `@PVJ_DIR@` are filled in by the installer, which is not written yet). It restarts the player if it dies, and puts the control socket in `/run/pvj`, group-writable for the `pvj` group. The web panel and OSC then run as their own users, join that group, and use `pvj-player play --no-spawn ...` (or the socket directly). A world-accessible runtime directory is refused.

Not verified on a real device: the unit passes `systemd-analyze verify` here, but restart-on-crash, the group permissions and DRM access need a test on a Pi.

## USB drives

`pvj-usb` (run by `pvj-usb@<partition>.service`, started from `install/99-pvj-usb.rules`) mounts each USB partition under `/media/pvj/<label>` and points `/media/usb` at the most recent one, which is what the old `startmasterusb` presets expect. Pulling the drive stops the unit, which unmounts it.

- Read-only by default, with `nosuid,nodev,noexec`; set `PVJ_USB_RW=1` in `/etc/pvj/pvj.env` to allow writing. A drive pulled mid-write is the usual way to corrupt one at a gig.
- Filesystems: vfat, exfat, ext2/3/4, ntfs (kernel `ntfs3` driver). Anything else is refused.
- Labels are cleaned to `a-z 0-9 . _ -`; two drives with the same label get `-2`, `-3`.
- It never touches a disk that also holds the running system, so a mini PC booted from a USB SSD keeps its own disk safe. The old backend mounted `/dev/sda` blindly.
- `/media/usb` is only replaced if it is already a link; a real folder there (legacy image) is left alone.

Tested with a fake `blkid`/`mount` (label cleaning, options, collisions, refusal cases, unmount). Not tested: a real udev event, real filesystems, or the udev rule itself (`udevadm` was not available to verify its syntax).

## Read-only root (power-loss protection)

`sudo pvj-rootfs enable` makes the system disk effectively read-only: everything written after boot lives in RAM and is gone at the next reboot, so pulling the plug cannot corrupt the SD card or SSD. `pvj-rootfs disable` turns it off, `pvj-rootfs status` reports the state. It uses the tools the system already has: `raspi-config` on Raspberry Pi OS, the `overlayroot` package elsewhere (`sudo apt install overlayroot`). It never reboots for you.

- **Media must not live on the system disk.** Files added while the overlay is active are lost at reboot, so `enable` refuses if `PVJ_MEDIA_DIR` is on the same filesystem as `/` (override with `--force`). Use a second disk or a USB drive.
- **Settings are read-only too.** Changes to `/etc/pvj` need `pvj-rootfs disable`, a reboot, the edit, then `enable` and another reboot.
- USB drives already mount read-only by default, so they are unaffected.

Tested with fake tools and fake `/proc/mounts`. Not tested on a real device: the `raspi-config` and `overlayroot` commands (especially `overlayroot-chroot` when disabling from inside an active overlay) and the reboot behaviour. Do that on a spare card first.

## Web panel and control API (Phase 4, in progress)

`bin/pvj-web` (service `install/pvj-web.service`) replaces the ~1900 line PHP backend with a small Python 3 server (standard library only) and a plain HTML, CSS and JavaScript panel with no framework and no CDN, so it works offline. Screens built so far, from the approved wireframes: Connect (pairing), Live (banks, pads, now playing, fade out, freeze, blackout), Mix (opacity, size, position, speed, transition, rotate, loop, mute, reset), Media, and System (vitals, modules, autostart, weekly schedule, streams, network, OSC, DMX, MIDI, appearance, access). Pictures: [docs/UI.md](../docs/UI.md). User manual: [docs/MANUAL.md](../docs/MANUAL.md).

**Pairing.** The box makes a fresh 4-digit PIN at every start and keeps it in `/run/pvj/pin` (RAM, gone at reboot); read it with `sudo pvj-pin`. Entering it on a phone gives that phone a token (HttpOnly, SameSite=Strict cookie, or `Authorization: Bearer` for scripts). Paired devices survive restarts. Guest links give `view` (look only) or `live` (play and mix) access; only paired `full` devices can change pads, modules, theme and access. Guessing is throttled per client and globally (attempts are counted exactly, even under a flood). The global limit means someone on your network can block *new* pairing for a few minutes by guessing badly; paired devices are never affected, and a paired full-access device clears the lockout with "New PIN" in System.

**What the server enforces.** State-changing calls are POST only, JSON, with the header `X-PVJ-Request: 1` and a matching `Origin`, so another website cannot drive the box, and a browser reconnect cannot replay a reboot. Every number is range-checked; media names are plain file names resolved inside the media folder (no paths, no dot files, symlinks that leave the folder are refused); nothing user-supplied reaches a shell. The panel is served with a Content-Security-Policy that forbids inline script, external resources and framing. The service runs as its own `pvj-web` user, sandboxed by systemd, and reaches the player only through its socket.

| Path | Access | Purpose |
| --- | --- | --- |
| `GET /api/hello`, `POST /api/pair`, `POST /api/session` | none | discovery, PIN pairing, guest link |
| `GET /api/preview.jpg` | view | a JPEG of what the screen is showing (one shared screenshot every 3 s at most; failures are remembered for the same time) |
| `GET /api/status`, `/api/media`, `/api/pads`, `/api/modules`, `/api/theme`, `/api/osc`, `/api/autostart`, `/api/schedule`, `/api/streams`, `/api/projectors`, `/api/mapper`, `/api/shaders`, `/api/support` | view | read state (a stream's saved address is shown with its login hidden) |
| `POST /api/play`, `/api/control`, `/api/blackout`, `/api/fadeout`, `/api/mix`, `/api/autostart/test`, `/api/projector`, `/api/shaders/play` (show one shader), `/api/vibes` (start, stop or skip the shader rotation) | live | play and mix |
| `POST /api/pads`, `/api/theme`, `/api/modules/<id>`, `/api/devices/invite`, `/api/devices/revoke`, `/api/pin/rotate`, `/api/player/restart`, `/api/osc`, `/api/autostart`, `/api/schedule`, `/api/streams`, `/api/projectors`, `/api/mapper`, `/api/shaders` (upload, delete, Vibes settings), `/api/dmx`, `/api/midi`, `/api/media/*`, `/api/network/*`, `GET /api/devices`, `/api/dmx`, `/api/midi`, `/api/network` | full | configure |

**Settings** are one JSON file (`/var/lib/pvj/settings.json`, mode 0600), written atomically with a backup and automatic recovery if a power cut tears the file. A schema change backs the old file up (`settings.json.bak-v<old>`) and migrates it; a file from a newer version is never rewritten, so rolling the program back cannot destroy settings.

**Modules** are JSON manifests in `pvj/modules.d`: core modules are locked on, optional ones switch on and off, board support and dependencies are enforced, and modules that are not built yet (NDI, AES67/Dante, presenter, wall, layout import, media library, PiWall) are listed as "Not built yet" and cannot be switched on. **Themes** are token files (`pvj/themes.d`, plus your own in `<state>/addons/themes`, which updates never touch); colours are validated as `#rrggbb` and text on an accent is chosen for contrast automatically.

**Library file tools** (Media screen, full-access devices only): upload clips from the phone or laptop with a progress bar, rename, delete, and see free space. Uploads stream to a hidden temporary file and are renamed into place only when complete, so a lost connection never leaves a half file in the list; only video, image and audio extensions are accepted; a name that already exists is refused unless replacement is asked for; files larger than `PVJ_MAX_UPLOAD_MB` (default 8192) or that would leave less than 200 MB free are refused before any data is read; only one upload runs at a time; a sender slower than 20 KB/s after the first 30 seconds is cut off, and so is one whose device is removed mid-upload. A new file is published without ever overwriting (if the name appears during the upload, the upload is refused). Left-over temporary files from a power cut are swept at start-up and before each upload. Names are checked in bytes and for invisible or direction-changing characters. Renaming and deleting only touch real files inside the media folder (never links, never paths); pads follow a renamed clip, and deleting a clip tells you how many pads used it. Not built: folders, tags, thumbnails and clip preview from the desktop Library wireframe.

**OSC** is built, off by default, receive-only and limited to private networks; see [OSC.md](OSC.md) for the addresses and the safety rules.

**Wired network settings** (System, Network; a beta module, off by default, needs NetworkManager): DHCP, fixed address, direct cable (link-local) or serving addresses, changed through a small root helper (`pvj-netd`) with a confirm-or-revert timer so a wrong setting cannot lock you out; see [NETWORK.md](NETWORK.md). Never tested against a real NetworkManager or a real device.

**Known limits:** "Restart player now" asks the player to quit and relies on systemd to bring it back; if mpv is completely wedged and ignores that request, restart it from a terminal (`sudo systemctl restart pvj-player`), because the panel runs unprivileged by design. A hardware watchdog for that case is not built. The server caps simultaneous connections at 64 and closes any connection after 30 seconds, so a flood of slow clients cannot exhaust it, but it does not replace a private network.

**Also built (all beta or off by default):** [autostart](AUTOSTART.md), [weekly schedule](SCHEDULE.md), [streams](STREAMS.md), [projectors](PROJECTORS.md), [projection mapping](MAPPER.md), [shaders and Vibes](SHADERS.md), [remote support](../docs/REMOTE-SUPPORT.md), [DMX](DMX.md) and [MIDI](MIDI.md), each with its own safety notes and a list of what was not verified.

**Not built yet:** crossfade (needs a second player; "Dip to black" and "Cut" work), the desktop screens (Presenter, Wall), NDI and AES67/Dante (ST 2110 only through a gateway, see D30), Wi-Fi and hotspot, updates from the network, a panel update button, and shutdown and reboot buttons. The old PHP panel still exists for the legacy Pi 3 line.

**Tested:** unit tests for settings, auth, modules and themes; HTTP tests for authentication, CSRF, roles, path confinement and validation; an end-to-end test through HTTP into a real headless mpv; and a real-browser test (Playwright) that pairs, assigns and plays a pad, drags a slider, switches theme, opens a guest link and fails on any CSP violation. Not tested on a real Pi, on real touch hardware, or with a real display.

## Updates and rollback

Releases are signed bundles, installed by `sudo pvj-update`. Nothing is fetched from the internet by default; an update works from a USB stick at a venue with no network.

**One-time setup (your signing key).**

1. On your own computer: `ssh-keygen -t ed25519 -f ~/.ssh/pvj-release -C nxlx-release`. Keep the private key safe and offline.
2. On each box add one line to `/etc/pvj/allowed_signers` (the installer creates the file empty; until a key is listed every update is refused):
   `pvj-release namespaces="pvj-release" ssh-ed25519 AAAA...your public key...`

**Making a release.** Set the version in `pvj/__init__.py`, commit, then `tools/make-release.sh 4.0.1 --key ~/.ssh/pvj-release`. It writes `dist/pvj-4.0.1.tar.gz`, `.sha256` and `.sig`. The archive is reproducible (same commit, same bytes) and contains only `pvj/`, `bin/` and `install/`, never the legacy code.

**Installing.** Put the three files in a `pvj-update/` folder on a USB stick, plug it in and run `sudo pvj-update usb`; or `sudo pvj-update apply pvj-4.0.1.tar.gz`. `pvj-update check FILE` verifies without installing; `pvj-update status` shows the current and previous release.

**What it checks before touching anything:** SHA-256, the OpenSSH Ed25519 signature against your key, safe unpacking (no absolute or `..` paths, no links or device files, no setuid bits, size limits), enough disk space, a newer version, and that the settings format is not being downgraded (`--force` overrides the last two). It then backs up `settings.json`, installs into a new folder under `/opt/pvj/releases`, switches `/opt/pvj/current` atomically, restarts the services and waits for the panel to answer. **If it does not answer, the previous release and your settings are put back automatically.**

**Rolling back by hand:** `sudo pvj-update rollback`. Settings migrations only go forward, so rollback restores the backup taken before the update; anything changed since is set aside as `settings.json.rolled-back-<time>`, not deleted. The last five backups are kept.

**Not built yet:** a button in the panel (the panel runs unprivileged and cannot install), fetching updates over the network, and the Stable/Beta/Nightly channels, which need a hosted release feed. Tested here with the real installer in stage mode and real OpenSSH signatures; not tested with a real `systemctl` restart, on a Pi, or with a real USB stick.
