# Mastercontrol: platform layer

Python 3, standard library only. Replaces omxplayer, D-Bus, `tvservice` and `vcgencmd` with mpv and `/sys`.

| Piece | What it does |
| --- | --- |
| `bin/nxlx-player` | One command for playback: `play`, `stop`, `pause`, `seek`, `speed`, `volume`, `opacity`, `size`, `position`, `status`, `info` |
| `mctl/hardware.py` | Board, OS, temperatures and display modes from `/proc` and `/sys` (Pi 3, 4, 5, x86, other ARM) |
| `mctl/player.py` | One long-lived mpv controlled over its JSON IPC socket. Changing clips is `loadfile`, so no black gap |
| `bin/nxlx-selftest` | Run on each device; prints a JSON report to send back. `--play` also plays a test pattern on the real screen |

## Old command to new command

| Legacy (`dbuscontrol.sh`, `omxplayer`) | New |
| --- | --- |
| `omxplayer --loop file` | `nxlx-player play file` |
| `omxplayer file` (once) | `nxlx-player play --once file` |
| `dbuscontrol.sh pause` | `nxlx-player pause` |
| `dbuscontrol.sh seek <us>` | `nxlx-player seek <seconds>` |
| `dbuscontrol.sh rate <x>` | `nxlx-player speed <x>` |
| `dbuscontrol.sh setalpha <0-255>` | `nxlx-player opacity <0-255>` |
| `omxsizetocenter <percent>` | `nxlx-player size <percent>` |
| `omxXposition <x>` | `nxlx-player position <x>` |
| `stopall` (player part) | `nxlx-player stop` |

## Behaviour differences to know about

- **Opacity** fades to black. mpv cannot blend against other layers, which is right on a black stage background.
- **Position** is in thousandths of the picture width, not omxplayer pixels.
- **Display output**: on a desktop session mpv uses the default output; on a bare console it uses DRM/KMS directly.
- **Pi 5** has no hardware H.264 decode; use HEVC files or expect software decoding.

## Testing

```
python3 -m unittest discover -s tests -t .      # needs mpv; the tests run it headless
bin/nxlx-selftest --play --json report.json       # on a real device
```

Not yet done: the old PHP panel does not call `nxlx-player`; its audio, overlay and mapper buttons still run the legacy scripts. The new panel (`mctl/web`) and the OSC receiver have their own versions of all three.

## Legacy start scripts

`nxlx-player start startlessonce05` runs a legacy preset by its old script name. One table in `mctl/presets.py` replaces ~290 scripts (`startless`, `startseamless`, `startlessonce`, `startlesseronce`, `startmaster`, `startmasterone`, `startmasterusb`, each with an optional two-digit index). `tests/test_presets.py` checks that table against every real script in `sync/`; it matches all but two upstream defects:

- `startless` (no number) is a wrapper that loops every file; the preset does the same.
- `startmaster95` plays `65*` upstream (copy-paste error); the preset plays `95*`.

Not ported yet: slave, stream and wifi presets, network sync between boxes (master presets play locally and print a warning), the "play a slideshow after the video" option, and the audio output flag (`local`, `both`); Pi 5 has no headphone jack, so audio selection needs its own module.

## Running as a service

`install/nxlx-player.service` runs `nxlx-player serve` under systemd as the account that owns the screen and sound card (`@NXLX_USER@`, `@NXLX_DIR@` are filled in by the installer). It restarts the player if it dies, and puts the control socket in its own folder `/run/nxlx/player` (the player's account, group `nxlx-svc`, mode 0750: the group can reach the socket, only the player can replace it; the preview picture is written there too, and the panel only reads it). The web panel runs as its own user in that group and uses the socket. Every service has such a folder of its own under a `/run/nxlx` that belongs to root; all the paths are in `mctl/paths.py` (D45). A world-accessible runtime directory is refused.

Not verified on a real device: the unit passes `systemd-analyze verify` here, but restart-on-crash, the group permissions and DRM access need a test on a Pi.

## USB drives

`nxlx-usb` (run by `nxlx-usb@<partition>.service`, started from `install/99-nxlx-usb.rules`) mounts each USB partition under `/media/nxlx/<label>` and points `/media/usb` at the most recent one, which is what the old `startmasterusb` presets expect. Pulling the drive stops the unit, which unmounts it.

- Read-only by default, with `nosuid,nodev,noexec`; set `NXLX_USB_RW=1` in `/etc/nxlx/nxlx.env` to allow writing. A drive pulled mid-write is the usual way to corrupt one at a gig.
- Filesystems: vfat, exfat, ext2/3/4, ntfs (kernel `ntfs3` driver). Anything else is refused.
- Labels are cleaned to `a-z 0-9 . _ -`; two drives with the same label get `-2`, `-3`.
- It never touches a disk that also holds the running system, so a mini PC booted from a USB SSD keeps its own disk safe. The old backend mounted `/dev/sda` blindly.
- `/media/usb` is only replaced if it is already a link; a real folder there (legacy image) is left alone.

Tested with a fake `blkid`/`mount` (label cleaning, options, collisions, refusal cases, unmount). Not tested: a real udev event, real filesystems, or the udev rule itself (`udevadm` was not available to verify its syntax).

## Read-only root (power-loss protection)

`sudo nxlx-rootfs enable` makes the system disk effectively read-only: everything written after boot lives in RAM and is gone at the next reboot, so pulling the plug cannot corrupt the SD card or SSD. `nxlx-rootfs disable` turns it off, `nxlx-rootfs status` reports the state. It uses the tools the system already has: `raspi-config` on Raspberry Pi OS, the `overlayroot` package elsewhere (`sudo apt install overlayroot`). It never reboots for you.

- **Media must not live on the system disk.** Files added while the overlay is active are lost at reboot, so `enable` refuses if `NXLX_MEDIA_DIR` is on the same filesystem as `/` (override with `--force`). Use a second disk or a USB drive.
- **Settings are read-only too.** Changes to `/etc/nxlx` need `nxlx-rootfs disable`, a reboot, the edit, then `enable` and another reboot.
- USB drives already mount read-only by default, so they are unaffected.

Tested with fake tools and fake `/proc/mounts`. Not tested on a real device: the `raspi-config` and `overlayroot` commands (especially `overlayroot-chroot` when disabling from inside an active overlay) and the reboot behaviour. Do that on a spare card first.

## Web panel and control API

`bin/nxlx-web` (service `install/nxlx-web.service`) replaces the ~1900 line PHP backend with a small Python 3 server (standard library only) and a plain HTML, CSS and JavaScript panel with no framework and no CDN, so it works offline. Screens built so far, from the approved wireframes: Connect (pairing), Live (banks, pads, now playing, fade out, freeze, blackout), Mix (opacity, size, position, speed, transition, rotate, loop, mute, reset), Media, and System (vitals, modules, autostart, weekly schedule, streams, network, OSC, DMX, MIDI, appearance, access). Pictures: [docs/UI.md](../docs/UI.md). User manual: [docs/MANUAL.md](../docs/MANUAL.md).

**Pairing.** The box makes a fresh 4-digit PIN at every start and keeps it in `/run/nxlx/web/pin` (RAM, gone at reboot; readable by the panel's account and root only); read it with `sudo nxlx-pin`. Entering it on a phone gives that phone a token (HttpOnly, SameSite=Strict cookie, or `Authorization: Bearer` for scripts). Paired devices survive restarts. Links give `view` (guest, can watch) or `live` (presenter, can play and mix) access; only paired `full` (owner) devices can change pads, modules, theme and access, with one exception: a presenter may make, show and end the guest join code (D48). Guessing is throttled per client and globally (attempts are counted exactly, even under a flood). The global limit means someone on your network can block *new* pairing for a few minutes by guessing badly; paired devices are never affected, and a paired full-access device clears the lockout with "New PIN" in System.

**What the server enforces.** State-changing calls are POST only, JSON, with the header `X-MCTL-Request: 1` (for one release the name before the rename, `X-PVJ-Request`, is still taken, D138) and a matching `Origin`, so another website cannot drive the box, and a browser reconnect cannot replay a reboot. Every number is range-checked; media names are plain file names resolved inside the media folder (no paths, no dot files, symlinks that leave the folder are refused); nothing user-supplied reaches a shell. The panel is served with a Content-Security-Policy that forbids inline script, external resources and framing. The service runs as its own `nxlx-web` user, sandboxed by systemd, and reaches the player only through its socket.

| Path | Access | Purpose |
| --- | --- | --- |
| `GET /api/hello`, `POST /api/pair`, `POST /api/session` | none | discovery, PIN pairing, guest link |
| `GET /api/preview.jpg` | view | a JPEG of what the screen is showing; 409 "nothing is on the screen right now" when the player runs and plays nothing (one shared screenshot every 3 s at most; failures are remembered for the same time) |
| `GET /api/status`, `/api/media`, `/api/pads`, `/api/modules`, `/api/theme`, `/api/osc`, `/api/autostart`, `/api/schedule`, `/api/streams`, `/api/projectors`, `/api/room`, `/api/mapper`, `/api/shaders`, `/api/effects` (the filters, the effect that is on, and why none can go on now), `/api/support` | view | read state (a stream's saved address is shown with its login, stream key and passphrase hidden; a projector's details, inputs and last known status are shown, never its password) |
| `POST /api/play`, `/api/control`, `/api/blackout`, `/api/fadeout`, `/api/mix`, `/api/autostart/test`, `/api/projector`, `/api/shaders/play` (show one shader), `/api/shaders/values` (change the inputs, speed, hue and brightness of the shader on screen), `/api/shaders/step` (the next one or the one before), `/api/shaders/preset` (apply a preset), `/api/vibes` (start, stop or skip the shader rotation, optionally through a named set; [SHADERS.md](SHADERS.md#api)), `/api/effects` (put a filter on over what plays, `{"off": true}` takes it off), `/api/effects/values` (its inputs, amount, speed and half resolution), `/api/effects/step`, `/api/effects/preset` ([SHADERS.md](SHADERS.md#effects-api)), `/api/room/scene` (apply a scene), `/api/room/group` (on, off, source or mute for a group of projectors; both answer at once, [ROOM.md](ROOM.md)) | live | play and mix; `/api/projector` takes `on`, `off`, `mute`, `unmute`, `mute_picture`, `unmute_picture`, `mute_sound`, `unmute_sound`, `input` (with `"input": "31"`), `identify` and `state`, and with Epson control (D99) `freeze`, `unfreeze`, `brightness`, `light` and `colour_mode` with a `"value"` ([PROJECTORS.md](PROJECTORS.md); tried by hand on an Epson X27; through remote support `off` and the picture settings are refused) |
| `POST /api/access/controller` | full | the pairing code a MIDI controller can put on the display (D61, [MIDI.md](MIDI.md)): `{"enabled"?: bool, "owner"?: bool}` sets the two switches (off takes `owner` off too), `{"cancel": true}` ends the code that is showing (404 if none). Nothing here makes a code; only a hold on a controller does. `GET /api/access` carries `controller` for a full-access device: the switches and `status` (`active`, `kind`, `shown_ago`, `seconds_left`, `last`, `made_this_hour`), never the digits. Refused through remote support like every `/api/access` route |
| `GET /api/access`, `POST /api/access/code`, `/api/access/cancel`, `/api/access/screen`, `GET /api/qr.svg?for=panel\|view\|live` | live for the guest code, full for the rest | join codes and the room screen (D23, D48). Full: `{"role": "view"\|"live", "minutes"?: 1 to 120 (15 if left out; the panel always sends its choice, 60 unless changed), "uses"?: 1 to 50}`; cancel by `{"code"}`, `{"role"}` or `{"all": true}`; show `{"show": true, "items": ["pin", "view", "live", "address"], "seconds"?: 10 to 3600}` or `{"show": false}`. A presenter (live): only the guest code. `code` takes `{"role": "view", "minutes": 15, 60 or 120, "uses"?: 1 to 20, "replace"?: true}` (409 when a guest code is active and `replace` is not true); `cancel` takes `{"role": "view"}` and nothing else; `screen` takes `"items": ["view"]`, is refused with 409 while a full-access device has the PIN or the presenter code on the screen, and `{"show": false}` takes only the guest code off; `GET` lists the guest code only and says of the screen only whether the guest code is on it and whether something else is (`"other"`); the QR code for `panel` and `view`. Anything else a presenter asks for is 403. Each code says who made it (`"by": "owner"\|"presenter"`). A presenter's actions are written to the log with the device id. Bounds (`mctl/auth.py`): presenters together make at most 6 guest codes an hour (429 with `retry_after`, also when a show would make one); a code made by a presenter's show lasts 15, 60 or 120 minutes, the shortest that covers the show, with 20 uses, and replaces nothing; at most 200 paired devices, 20 of them only for full access (a join or a link beyond that answers 409 and uses up nothing); a device that joined with a guest code is dropped after 7 days without use. All of it is refused through the support tunnel, for every role |
| `GET /api/midi` | live | the MIDI switches, the mappings, Learn's state and, per connected controller (`controllers`): its profile (`id`, `name`, `description`, `note`, `sources`, `rows`, `cols`) or null, `standard` (its standard layout is on), and each drawn control (`id`, `name`, `row`, `col`, `kind`, `send`, `unverified`, `action` now, `origin`: `yours`, `standard` or null, `standard`: what the profile says, `guard`, `pickup`, `waiting`, `value` and `ago`: what it last sent and how many seconds ago); also `bank` (the controllers' bank) and `profiles` (the shipped ones) |
| `POST /api/midi` (`enabled`, `builtin`, or `{"controller": name, "standard": bool}` for one controller's standard layout), `/api/midi/learn`, `/api/midi/map` (`add`, `remove`, `clear`, and for a recognised controller `{"set": {"controller", "control", "action": {...}}}` and `{"reset": {"controller", "control"?}}`) | full | MIDI set-up ([MIDI.md](MIDI.md)) |
| `GET /api/output`, `POST /api/output`, `POST /api/output/av` | full | which connector the picture goes to (Automatic or one the board lists) and the AV (composite) output on a Pi, switched in the boot files by the system helper with backups ([screenmode.py](screenmode.py), [bootcfg.py](bootcfg.py), D128); the writes are refused through the support tunnel |
| `GET /api/events` | full | the last 500 events on this node, newest first, with the device or address in `who` (the Owner's only); the diagnostics file carries the same lines without it ([events.py](events.py), D125) |
| `POST /api/system/settings/export`, `/api/system/settings/import?confirm=import` (the body is the exported file), `/api/system/factory-reset`, `GET /api/system/diagnostics` | full | settings file, diagnostics, factory reset ([boxcare.py](boxcare.py)); import, export with passwords and reset are refused through the support tunnel |
| `POST /api/pads`, `/api/theme`, `/api/modules/<id>`, `/api/devices/invite`, `/api/devices/revoke`, `/api/pin/rotate`, `/api/player/restart`, `/api/osc`, `/api/autostart`, `/api/schedule`, `/api/streams`, `/api/projectors`, `/api/room` (groups and scenes), `/api/mapper`, `/api/shaders` (upload, delete, Vibes settings, rotation sets), `/api/shaders/presets` (save, rename, delete a preset), `/api/effects/presets` (the same for an effect), `/api/effects/library` (add or remove a filter file), `/api/effects/config` (Effect detail: the size effects work at on this box), `/api/dmx`, `/api/media/*`, `/api/network/*`, `GET /api/devices`, `/api/dmx`, `/api/network` | full | configure |

**Settings** are one JSON file (`/var/lib/nxlx/settings.json`, mode 0600), written atomically with a backup and automatic recovery if a power cut tears the file. A schema change backs the old file up (`settings.json.bak-v<old>`) and migrates it; a file from a newer version is never rewritten, so rolling the program back cannot destroy settings.

**Settings file, diagnostics, factory reset** (`mctl/boxcare.py`, System, full access; the panel does all of it itself, no root helper). Export: `{"passwords": false}` gives `{"name", "file"}`; the file never holds `auth`, `devices`, `support` or `support_log`, and holds projector passwords and the secrets of a stream address only with `"passwords": true` (not through the support tunnel); without it a stream address is cut down by `streams.strip_login` (no login; RTMP without the last path part and the query; SRT with only the query names in `streams.SRT_PLAIN`; RTSP without query names that sound like a secret), and an import keeps the box's own full address when the file holds its shortened form. Import: the file is the request body, at most 1 MiB, parsed strictly (UTF-8, no repeated keys, no NaN, Infinity or numbers too large to hold, bounded nesting), then through the same migrations as a load (newer refused, older migrated) and every section's own check; nothing changes unless all of it passes; the previous settings are kept as `settings.json.before-import-<time>` (the last three); the box's own PIN, devices and remote support stay. The checks know the settings as of schema 13 (`KNOWN_SCHEMA`): the Shaders and Vibes settings (`shaders`, not in the defaults, so only present once changed) and a projector's `details` and `labels` are exported and checked too; a section a later schema adds is neither exported nor imported until its check is added to `SECTIONS`, and fields a later schema adds to a known section come back from that schema's own migration (run on the imported sections only); a test fails when `default_settings()` gains a section that is in neither `SECTIONS` nor `NEVER`. Diagnostics: one JSON file with versions, board, modules, health, the last update and the settings built from a list of what may be shown, every text in it scrubbed of the secrets the box knows; the system log is asked for with a fixed `journalctl` command and is normally not readable by the unprivileged panel, which the file says. Factory reset: `{"confirm": "factory-reset", "media": "keep" | "delete"}`; defaults, no devices, codes or support session, a new PIN, copies of old settings beside the settings file removed, playback stopped and the mix reset so the PIN shows, the media files in the media folder deleted on request (top level, plain files and links only; refused when the media folder is on a USB drive). An import and a reset refuse with 409 while the other one or an update runs (a reset also during an upload or a copy from USB), and no update or USB copy starts while a reset runs; a reset that cannot save the settings changes nothing. It cannot remove the root-only update backups in `<state>/backups`.

**Modules** are JSON manifests in `mctl/modules.d`: core modules are locked on, optional ones switch on and off, board support and dependencies are enforced, and modules that are not built yet (AES67/Dante, presenter, wall, layout import, media library, PiWall) are listed as "Not built yet" and cannot be switched on. **Themes** are token files (`mctl/themes.d`, plus your own in `<state>/addons/themes`, which updates never touch); colours are validated as `#rrggbb` and text on an accent is chosen for contrast automatically. A theme may also name a **style**, one of a fixed set of looks that `app.css` has a block for (today the default one and `signal`); see [THEMES.md](THEMES.md).

**Library file tools** (Media screen, full-access devices only): upload clips from the phone or laptop with a progress bar, rename, delete, and see free space. Uploads stream to a hidden temporary file and are renamed into place only when complete, so a lost connection never leaves a half file in the list; only video, image and audio extensions are accepted; a name that already exists is refused unless replacement is asked for; files larger than `NXLX_MAX_UPLOAD_MB` (default 8192) or that would leave less than 200 MB free are refused before any data is read; only one upload runs at a time; a sender slower than 20 KB/s after the first 30 seconds is cut off, and so is one whose device is removed mid-upload. A new file is published without ever overwriting (if the name appears during the upload, the upload is refused). Left-over temporary files from a power cut are swept at start-up and before each upload. Names are checked in bytes and for invisible or direction-changing characters. Renaming and deleting only touch real files inside the media folder (never links, never paths); pads follow a renamed clip, and deleting a clip tells you how many pads used it. Not built: folders, tags, thumbnails and clip preview from the desktop Library wireframe.

**OSC** is built, off by default, receive-only and limited to private networks; see [OSC.md](OSC.md) for the addresses and the safety rules.

**Nodes that find each other** (D127): each node announces its name, model and version by mDNS (`_nxlx._tcp`) and lists the others at `GET /api/nodes`; no package is installed, and the Owner can switch it off; see [FLEET.md](FLEET.md). Not yet run on hardware.

**Wired network settings** (System, Network; a beta module, off by default, needs NetworkManager): DHCP, fixed address, direct cable (link-local) or serving addresses, changed through a small root helper (`nxlx-netd`) with a confirm-or-revert timer so a wrong setting cannot lock you out; see [NETWORK.md](NETWORK.md). Never tested against a real NetworkManager or a real device.

**Known limits:** "Restart player now" asks the player to quit and relies on systemd to bring it back; if mpv is completely wedged and ignores that request, restart it from a terminal (`sudo systemctl restart nxlx-player`), because the panel runs unprivileged by design. A hardware watchdog for that case is not built. The server caps simultaneous connections at 64 and closes any connection after 30 seconds, so a flood of slow clients cannot exhaust it, but it does not replace a private network.

**Also built (all beta or off by default):** [autostart](AUTOSTART.md), [weekly schedule](SCHEDULE.md), [streams](STREAMS.md), [sound reactivity](SOUND.md) (levels only, from a capture device; never run on a Pi), [NDI input](NDI.md) (one short first run on a Pi 4 on 2026-10-08, by SSH; nobody has watched it on a screen yet; needs NDI's runtime from the owner), [projectors](PROJECTORS.md) (PJLink, and Epson control for freeze, brightness, Eco and the colour mode), [projection mapping](MAPPER.md), [shaders and Vibes](SHADERS.md), [remote support](../docs/REMOTE-SUPPORT.md), [DMX](DMX.md) and [MIDI](MIDI.md), each with its own safety notes and a list of what was not verified.

**Not built yet:** on classic mpv, a crossfade in which both pictures move (there the crossfade blends from a still of the screen, as do the wipes and the slides; see `mctl/transitions.py` and D71; the New renderer draws all of them itself with both clips playing, D98 and D111), a push, a luma dissolve and ISF transitions, the desktop screens (Presenter, Wall), AES67/Dante (ST 2110 only through a gateway, see D30), Wi-Fi and hotspot, updates from the network, and shutdown and reboot buttons. The old PHP panel still exists for the legacy Pi 3 line.

**Tested:** unit tests for settings, auth, modules and themes; HTTP tests for authentication, CSRF, roles, path confinement and validation; an end-to-end test through HTTP into a real headless mpv; and a real-browser test (Playwright) that pairs, assigns and plays a pad, drags a slider, switches theme, opens a guest link and fails on any CSP violation. Not tested on a real Pi, on real touch hardware, or with a real display.

## Settings by environment variable

The services read their settings from environment variables named `NXLX_*` (`NXLX_USB_RW`, `NXLX_MEDIA_DIR`, `NXLX_PORT` and so on), set by their units and by `/etc/nxlx/nxlx.env`, which the installer creates once and then leaves to you. The code reads only these names. A node that still has the old `PVJ_*` names (installed before the rename) gets them rewritten, in the env file and in the unit files, by `nxlx-convert`, which keeps the original of every file it rewrites; `sudo nxlx-convert --undo` puts the old names back.

## Updates and rollback

Releases are signed bundles, installed by `sudo nxlx-update`. Nothing is fetched from the internet by default; an update works from a USB stick at a venue with no network.

**One-time setup (your signing key).**

1. On your own computer: `ssh-keygen -t ed25519 -f ~/.ssh/nxlx-release -C nxlx-release`. Keep the private key safe and offline.
2. On each box add one line to `/etc/nxlx/allowed_signers` (the installer creates the file empty; until a key is listed every update is refused):
   `nxlx-release namespaces="nxlx-release" ssh-ed25519 AAAA...your public key...`

**Names.** A release bundle is `nxlx-N.N.N.tar.gz` with `nxlx-N.N.N.tar.gz.sig` and (optional) `.sha256` beside it, signed with the principal and namespace `nxlx-release`. The updater takes only these names: a file called `pvj-N.N.N.tar.gz`, or a signature made under the namespace `pvj-release`, is not offered and does not verify. On a node converted by `nxlx-convert` the line in `/etc/nxlx/allowed_signers` is rewritten from `pvj-release` to `nxlx-release` (the key stays as it was; `--undo` puts it back). To use a bundle built before the rename, build it again from its commit with the current `tools/make-release.sh`, or rename it and sign it again with your key: `mv pvj-N.N.N.tar.gz nxlx-N.N.N.tar.gz; ssh-keygen -Y sign -f ~/.ssh/nxlx-release -n nxlx-release nxlx-N.N.N.tar.gz`, then write the checksum again (`sha256sum nxlx-N.N.N.tar.gz > nxlx-N.N.N.tar.gz.sha256`). A node that runs a release of the system rename without these names (the state between rename steps 7 and 8, which no node is meant to be in) cannot take this release: `install/install.sh` asks the converter, refuses before it changes anything and lists what the converter would still change; its own updater looks for the old bundle name. An expert can run this release's converter by hand (`sudo python3 bin/nxlx-convert --plan`, then `--run`), and an undo of that puts back old variable names this release does not read, so the earlier release has to be installed again. A bundle of a release with the old program names is refused whatever it is called; the way back to one is `sudo nxlx-convert --undo`. The recovery stick file is `nxlx-recover` (or `nxlx-recover.txt`).

**Making a release.** Set the version in `mctl/__init__.py`, commit, then `tools/make-release.sh 4.0.1 --key ~/.ssh/nxlx-release`. It writes `dist/nxlx-4.0.1.tar.gz`, `.sha256` and `.sig`. The archive is reproducible (same commit, same bytes) and contains only `mctl/`, `bin/` and `install/`, never the legacy code.

**Installing.** Put the three files in a `nxlx-update/` folder on a USB stick, plug it in and run `sudo nxlx-update usb`; or `sudo nxlx-update apply nxlx-4.0.1.tar.gz`. `nxlx-update check FILE` verifies without installing (it unpacks the bundle in the install, so it takes the update lock and is refused while an update runs); `nxlx-update status` shows the current and previous release. The panel's Updates card (System > Updates) lists what is in the `nxlx-update/` folder of a stick and what was uploaded, and installs it the same way.

**What it checks before touching anything:** SHA-256, the OpenSSH Ed25519 signature against your key, safe unpacking (no absolute or `..` paths, no links or device files, no setuid bits, size limits), enough disk space, a newer version, and that the settings format is not being downgraded (`--force` overrides the last two). It then backs up `settings.json`, installs into a new folder under `/opt/nxlx/releases`, switches `/opt/nxlx/current` atomically, restarts the services and waits for the panel to answer. **If it does not answer, the previous release and your settings are put back automatically.**

**Rolling back by hand:** `sudo nxlx-update rollback` (the panel's Updates card then says "rolled back to N.N.N"). Settings migrations only go forward, so rollback restores the backup taken before the update; anything changed since is set aside as `settings.json.rolled-back-<time>`, not deleted. The last five backups are kept.

**Not built yet:** fetching updates over the network, and the Stable/Beta/Nightly channels, which need a hosted release feed. Tested here with the real installer in stage mode and real OpenSSH signatures; not tested with a real `systemctl` restart, on a Pi, or with a real USB stick.
