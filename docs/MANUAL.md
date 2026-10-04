<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# nxlx.mastercontrol: user manual

For the person running visuals at a gig. It covers the new Python panel (the `pvj/` folder). The old PHP panel for the legacy Pi 3 line has its own manual in `docs/html`.

**Status, read this first.** The new core has been built and tested in containers and CI, and the image has been booted and used on **one Raspberry Pi 4** (what was and was not verified there is listed in [HANDOFF.md](../HANDOFF.md)). Other boards, a projector, and several features (MIDI Learn, streams, the read-only root, the Network module) have not been tried on hardware. Where this manual describes them, that is what the code is written to do. Treat anything not listed as verified as untested on hardware, and do not use it at a paid show without a rehearsal and a backup plan. The checklist is in [tools/DEVICE-TESTING.md](../tools/DEVICE-TESTING.md).

The pictures come from the test suite (test clips and a fake network), see [UI.md](UI.md).

## 1. Get it running

**With the image.** Download the `nxlx-mastercontrol-image` artifact from the "Build image" workflow (Actions tab), check it against `SHA256SUMS`, and flash the `.img.xz` with Raspberry Pi Imager ("Use custom"). The image has **no password and SSH is off**: the first boot asks for a user name and password on the Pi's own screen and keyboard. (For a headless first boot see "Option C" in [tools/DEVICE-TESTING.md](../tools/DEVICE-TESTING.md).)

**On an existing system** (Raspberry Pi OS Bookworm or Trixie, Debian, Ubuntu): `sudo install/install.sh`. See [install/README.md](../install/README.md).

The web panel listens on port 80. Connect the box and your phone to the same network. A direct Ethernet cable between a laptop and the box also works (the network module has a "Direct cable" mode for that).

## 2. Pair your phone

Open `http://<address of the box>/` in a browser. The box makes a new four digit PIN every time it starts. Until the first device has paired, the box draws the PIN and its address on its own screen whenever nothing is playing; after that it stays off, so it cannot show at a gig. If every paired device is later removed it comes back, which is the way in for a box nobody can reach; a clip that starts clears it at once. You can always read it with `sudo pvj-pin`, and any full-access device can make a new PIN or a guest link in System.

![Connect screen](images/ui/connect.png)

A paired phone is remembered until you remove it. A wrong PIN is throttled. If someone locks new pairing by guessing, a paired full-access device clears it with **New PIN** in System.

There are three access levels: **view** (look only), **live** (play and mix) and **full** (change pads, modules, files and settings).

### Letting other people in: codes and QR codes on the display

For a studio or a gig, the person running the show (a full-access device) opens System > Access:

- **New guest code** (watch only) or **New presenter code** (play and mix) makes a 6 digit code. Codes expire (15 minutes by default, up to 2 hours), work a limited number of times, and a new code for the same role replaces the old one. They are never written to disk, so a restart clears them.
- **Show on display** puts the chosen codes on the box's screen, each with a big **QR code** in the top right corner (guest on the left, presenter on the right), together with the panel address, for 1 minute to 1 hour, even over a playing clip. **Hide from display** takes them off at once. A phone camera that scans a code opens the panel with the code filled in; one tap on **Join with code** and it is in, with exactly that access. The code travels in the part of the address after `#`, which a browser does not send to the box or anyone else, and the panel removes it from the address bar straight away (a phone's camera or scanner app may still remember what it scanned).
- The full access PIN can be shown too (as text, never as a QR code), but the panel asks first: everyone who can see the screen can then take over the box. While anything is on the display, a snapshot taken by a guest or presenter shows only the video, never the text or the QR codes, so nobody can read the PIN or a presenter code remotely.
- If someone blocks joining by guessing wrong codes, **Unblock joining** opens it again without changing the PIN.
- The panel answers only to its IP addresses, `localhost`, its own name and `<name>.local`. To use another name (a studio DNS name), add it to `PVJ_ALLOWED_HOSTS` in `/etc/pvj/pvj.env` (comma separated). This stops a web page elsewhere from driving the panel through a visitor's browser (DNS rebinding).
- **Print access sheet** prints a page to pin up: a QR code for the panel address (no access in it; people still need a code), and the current guest and presenter codes.
- **Create guest link** makes a link that does not expire (until you remove the device), with its QR code; hand it to a resident operator.

![Access card with a guest and a presenter code and their QR codes](images/ui/access.png)

Tested on a Raspberry Pi 4: the codes and QR codes were drawn on the display, decoded off a snapshot of the screen by a real QR scanner (zbar), and a join with each code gave exactly guest or presenter access.

## 3. Put clips on it

Media > **Upload clips** (full access). Video and image files only. Uploads go to a hidden temporary file and appear only when complete, so a dropped connection never leaves a half file. Or plug in a USB drive: it mounts read-only under `/media/pvj/<label>` (and `/media/usb` for the newest one), and its video and image files at the top level of the drive appear on the Media screen under "USB drive" with a **Play** button, so you can play straight from the stick without copying (a 3 GB film played fine this way on a Pi 4). Files in folders on the drive are not listed yet; put the clips at the top. To keep a clip after the stick goes, press **Copy to the box** next to it (full access): it is copied in the background with a progress line and a Cancel button, with the same checks as an upload (free space, a hidden temporary file, never replacing a clip unless you confirm). Cancel acts between chunks, within a moment.

![Media screen](images/ui/media.png)

Clips live in `/var/lib/pvj/video`. **If you turn on the read-only root (`sudo pvj-rootfs enable`), move media to a second disk or USB drive first**: everything on the system disk is lost at reboot afterwards, and the command refuses if media is on the system disk.

### Prepare your clips

What plays smoothly depends on the board. What this project has measured, and what it has not:

| Board | Plays well | Notes |
| --- | --- | --- |
| Raspberry Pi 4 | H.264 up to 1080p (a 1080p 24 fps film: about one core, no dropped frames; 720p 30 fps: a fifth of a core) | H.264 is decoded in software on this build. 1080p at 50 or 60 fps, HEVC and 4K are **not tested yet**. |
| Raspberry Pi 5 | HEVC (in hardware), H.264 up to 1080p (in software) | **Not tested on a Pi 5 yet.** |
| Raspberry Pi 3 | 720p H.264 | Larger is **not tested** and may stutter. |

Media > **Info** on a clip shows its format and, when it may not play smoothly on this box, says why.

A safe recipe (HandBrake, free, for Mac, Windows and Linux):
- Format **MP4**; video **H.264** (or **H.265/HEVC** for 4K on a Pi 4 or 5), profile Main or High, **8-bit**;
- **Constant frame rate**, "Same as source" (phone footage is often variable: switch to constant); 25, 30, 50 or 60 fps;
- Resolution: the screen's (1920x1080 for most projectors); **no** automatic cropping;
- Quality: constant quality around 20 to 22 (RF), or 8 to 12 Mbit/s for 1080p;
- Audio: AAC, 48 kHz, stereo.

Avoid editing formats (ProRes, DNxHD and similar): they are many times too heavy. Very high bit rates from a slow USB stick can stutter too; copy those to the box.

Names: anything readable works (spaces too). Start a name with two digits (`01_intro.mp4`, `02_loop.mp4`) to play it by number from Quick play, the old start scripts and OSC. On a USB drive, put clips at the top level.

For a synced show or a video wall, use the same files with the same names on every box, and start each clip with a second of black or a still: the clients jump into step during the first seconds.

Power matters as much as the clip: a weak power supply makes a Pi stutter, drop the network and damage its SD card. Use the official supply (Pi 4: 5 V 3 A; Pi 5: 5 V 5 A); System > Health warns when the voltage drops.

## 4. Play

**Live** has three banks of twelve pads. **Edit pads** (full access) assigns a clip to a pad. Tap a pad to play it. **Fade out**, **Freeze** (pause), **Stop** (end the clip and leave the screen black and the player ready) and **Blackout** (black without stopping) are always at the bottom.

![Live screen](images/ui/live.png)

**Screen** (Live, under Now playing): tap **Take snapshot** to see what the box is putting on the display. It is a screenshot of the player's own output, so a blackout, brightness or size change shows up. It is a single picture on request, not a live view, and it is deliberately not automatic: on a Raspberry Pi 4 each snapshot stalls playback for about a quarter of a second (measured: a continuous preview made video visibly choppy). Any paired device may take one, including view-only guests. For a real-time picture use an HDMI capture device on the display's output.

**Sound output** (System): on a Raspberry Pi "Automatic" sends the sound to the HDMI port that has the screen on it (the player's own default is the 3.5 mm headphone jack, which is silent on a monitor). Pick another output, such as the headphones or a USB sound device, in System > Sound output. The choice is remembered and re-applied if the player restarts.

**Now playing** (Live) has the controls of a normal player: a **position slider** (drag and release to jump), **Prev** and **Next** (when several clips are playing as a list, for example Play all), **- 10 s** and **+ 10 s**, **Fade in** (from a blackout or a fade out, over 2 s) and **Test pattern** (SMPTE colour bars from the player itself, for lining up a projector; tap again to stop).

![Now playing with the transport, and Take snapshot](images/ui/live-transport.png)

**Quick play** (Media) plays the whole folder, looping or once, or in a random order (**Shuffle all**), or the clips whose names start with a number (`01_intro.mp4` is clip 01), like the old Video tab. Play all leaves audio files out.

![Quick play](images/ui/media-quickplay.png)

**When a clip ends.** Each pad has its own ending (Edit pads, then the pad): **Loop**, **Play once, then black**, or **Play once, hold the last frame** (for a sting that should stay on screen).

**Slideshow** (Media, like the old Presenter tab): the pictures of the media folder or of a USB drive, each for 0.1 second to a minute, then start again, keep the last picture, or go black; optionally in a random order. Prev and Next on Live step through them.

![Slideshow](images/ui/slideshow.png)

**Live input** (Media): an HDMI capture stick or a webcam on USB, shown like a clip, at 720p or 1080p at 30 frames a second. Playing anything else, Stop, the test pattern switched off or Restart player ends it (switching away from it cuts straight to the next clip, without the dip to black). On a Raspberry Pi 4 with a USB3 HDMI capture stick both sizes played without dropping frames (720p60 dropped frames and is not offered); at 1080p the picture kept slightly behind real time in one test, so prefer 720p until that is checked with a real source. The device is read by a separate helper, so a crash in the capture code cannot stop the player (a fault in the kernel's USB or camera driver still could). Only webcams that can send YUYV are known to work; others are converted, untested. The input's sound is not played yet, and the delay from the source to the screen has not been measured.

**Audio files** (mp3, wav, flac, ogg, m4a, aac, opus) can be uploaded and played like clips; the screen stays black while they play.

**Overlay picture** (Mix): put a PNG from the media folder over the video, like the old panel's overlay.png: a logo, a watermark, or a mask that blacks out the parts of the picture that miss the screen. Make it transparent where the video should show. It is fitted to the screen once (about a second on a Pi 4), stays on top of the video, and comes back by itself if the player restarts. It cost no measurable playback on a Pi 4 with a 1080p film.

![Overlay picture](images/ui/mix-overlay.png)

**Mirror and position** (Mix): Flip left-right or upside down for rear projection or a mirror rig (live, no reboot; on a Pi 4 it costs about half a processor core at 1080p, and dropped no frames in the test), and Position Y next to Position X.

![Mirror](images/ui/mix-mirror.png)

**Clip details** (Media > Info): codec, picture size, frame rate, length and sound of a clip, read by the player without showing it (the old Movie Codec and Movie Resolution buttons).

**Box** (System): software versions, free space for media, what is connected to each screen output and the modes it offers, the mode the player is using now, and the box clock. Full-access devices also get **Restart the box** and **Power off** (each asks first; neither is reachable over OSC, MIDI or DMX), and, while the clock has not been set from the network (a Pi has no clock battery, so a box with no internet starts at the time it was last shut down), **Set the box clock to this phone's time**. These go through a small helper, `pvj-sysd`, that runs as root with no capabilities of its own (systemd and timedated do the work), in its own folder that only root can change, and answers only root and the panel's account. A clock is only accepted up to 2036: a phone set to a far future year would otherwise stick, because the box saves its clock. If setting the clock goes wrong, the panel says so, including when network time could not be switched back on (the helper switches it back on at its next start). **Test tones** (System > Sound output): 5 seconds of 440 Hz on the left, right or both speakers.

![Box](images/ui/box.png)
![Sound output](images/ui/sound-output.png)

**Mix** has opacity, volume, size, position, speed, rotate, loop and mute, and how one clip changes to the next: **Cut** or **Dip to black** (a real crossfade is not built; it needs a second player).

![Mix screen](images/ui/mix.png)

## 5. Optional modules (System)

Beta modules are **off** until you switch them on under System > Modules. Modules that are not built yet say "Not built yet".

![Modules](images/ui/system-modules.png)

| What | Where to read |
| --- | --- |
| **Autostart**: what plays at power-up and after a crash | [pvj/AUTOSTART.md](../pvj/AUTOSTART.md) |
| **Weekly schedule**: play, start scripts, stop, blackout, show and projector power at set times | [pvj/SCHEDULE.md](../pvj/SCHEDULE.md) |
| **Video wall and sync**: boxes play in step (server and client), each can show a tile of the picture | [pvj/SYNC.md](../pvj/SYNC.md) |
| **Projection mapper**: quads, triangles and grids, lined up from the phone (card on Mix) | [pvj/MAPPER.md](../pvj/MAPPER.md) |
| **Shaders and Vibes**: moving pictures made by the GPU, and one button that plays them endlessly | below, and [pvj/SHADERS.md](../pvj/SHADERS.md) |
| **Projector control**: on, off and picture mute over PJLink | [pvj/PROJECTORS.md](../pvj/PROJECTORS.md) |
| **Streams**: SRT, RTSP, RTMP | [pvj/STREAMS.md](../pvj/STREAMS.md) |
| **DMX over the network**: Art-Net and sACN | [pvj/DMX.md](../pvj/DMX.md) |
| **MIDI controller** (USB) | [pvj/MIDI.md](../pvj/MIDI.md) |
| **OSC**: TouchOSC, Resolume, QLab and others | [pvj/OSC.md](../pvj/OSC.md) |
| **Network settings** (wired) | [pvj/NETWORK.md](../pvj/NETWORK.md) |

The Projection mapping card on Mix, with a grid and a quad:

![Projection mapping](images/ui/mapper.png)

Projectors and the schedule (here with projector power, a clip, a start script and a blackout):

![Projectors](images/ui/projectors.png)
![Schedule](images/ui/schedule.png)

Check the box clock before relying on the schedule: a Pi has no battery clock, and until the network sets the time the clock is wrong.

### Shaders and Vibes (beta)

A shader is a small program that the box's graphics chip runs to draw a moving picture: there is no clip, so it never ends and never repeats exactly. The box takes shaders in the **ISF** format (Interactive Shader Format, `.fs` files) and comes with ten slow, quiet ones made for ambience.

Switch **Shaders and Vibes** on under System > Modules (beta, off by default; not offered on a Raspberry Pi 3).

- **Vibes** is the big button that then appears on the **Live** screen (presenters and full-access devices). One tap and the box plays shaders endlessly: a shuffled order, three minutes each, a dip to black between them, and slightly different speeds, sizes and colours every round. It ends when you press **Stop**, press the button again, or play anything else; it never takes the screen back by itself.
- On the **Mix** screen the **Shaders and Vibes** card lists the shaders. **Play** shows one until you play something else; its number inputs appear as sliders. Full-access devices choose which shaders Vibes may use, how long each stays (10 seconds to an hour), how many lines the shader is drawn with (fewer is lighter work), and upload their own `.fs` files (generator shaders only; a file the box cannot show is refused with the reason).
- Opacity, the fades, Blackout, the overlay picture and the projection mapping all work on a shader as they do on a clip.
- Vibes can also start by itself: choose **Vibes** under System > Autostart, add a **Start Vibes** entry to the schedule, or send the OSC address `/pvj/vibes`.

**Not measured, and not seen on a screen yet.** How smoothly each shader runs on a Pi 4, a Pi 5 or a PC has not been measured on any board, and nobody has watched this on a display: so far it has only run in automated tests on a software graphics chip. Watch the screen the first time; if the picture stutters, choose fewer lines on the card or take the heavier shaders out of Vibes (each shader's cost is listed). Details: [pvj/SHADERS.md](../pvj/SHADERS.md).

## 6. Keep it safe and recoverable

- **Keep the show network private.** The panel is protected by a PIN and per-device tokens, but it is not built to face the internet. OSC, DMX and MIDI are off until you switch them on; OSC and DMX only accept senders on private networks (plus ranges you add).
- **Power cuts.** The read-only root protects the system disk from a pulled plug (`sudo pvj-rootfs enable`, then reboot). Not tested on a real board.
- **Remote support** is off until you allow it, and even then only open while you run a session you started (time-limited, panel only, visible on every device, stoppable). See [REMOTE-SUPPORT.md](REMOTE-SUPPORT.md).
- **Updates** are signed bundles (`pvj-N.N.N.tar.gz` with its `.sig`; a `.sha256` is optional). System > **Updates** (full access) installs one from a `pvj-update` folder on a USB stick, or one you upload there; it is checked against your signing key, an older version is refused, and if the panel does not come back the box goes back to the version before by itself. The panel and the player restart during an update. From a terminal: `sudo pvj-update`. See [pvj/README.md](../pvj/README.md#updates-and-rollback).
- **Player stuck?** System > Restart player asks it to quit and systemd brings it back. If mpv ignores that, run `sudo systemctl restart pvj-player` on the box.
- **Network changes** always revert by themselves unless you confirm them. Test them with a keyboard and monitor on the box, never over SSH on the only link.

**Hostile drives and files.** A USB stick or an upload can hold a file that is named `.mp4` but is really a playlist or a script for the player. The player is started so that it plays media only: it does not follow references inside files (playlists, EDL), load sidecar subtitle or audio files, load scripts or run youtube-dl. This was tested on a Raspberry Pi 4 with fake playlists. A drive is also mounted read-only with `nosuid,nodev,noexec`, and only the top level of a drive is listed, up to a limit.

## Health

System > Health says in plain words whether the box is well: the power supply (a Pi warns when the voltage drops; any drop is remembered until the next reboot, because it is the most common cause of odd stutters, network drops and damaged SD cards: use the official supply, 5 V 3 A for a Pi 4), the temperature (above 80 C a Pi slows down), the player (decoded in hardware or software, and dropped frames a second while playing), the load, and whether the helpers are running. It also lists the addresses to open the panel from another device, and a full-access device can put the address on the display for 2 minutes.

## 7. Troubleshooting

| Symptom | Try |
| --- | --- |
| The browser says the site is not secure or cannot be reached | Type `http://` in front of the address: the panel uses plain HTTP on your own network, and browsers that try HTTPS first fail |
| A clip stutters | Media > Info on the clip says if it is too heavy for this box; see Prepare your clips |
| Stutters, network drops, odd restarts | System > Health: a "Power" warning means the power supply is too weak. After an unexpected restart, `journalctl -b -1 -e` on the box shows the end of the log before it (the log is kept across restarts, up to 64 MB) |
| The page does not load | Same network as the box? `systemctl status pvj-web` on the box; the address may have changed (check the router's client list) |
| "Wrong PIN" | The PIN changes at every start. `sudo pvj-pin`, or System > New PIN from a paired device |
| A pad is grey and says Empty | Edit pads (full access) and assign a clip |
| Clip will not play | Check the file plays in `mpv` on the box; on a Pi 5 use HEVC (no hardware H.264 decode) |
| Nothing shows on the projector | `pvj-selftest --play` on the box; check `journalctl -u pvj-player` |
| DMX or MIDI does nothing | The module must be on **and** the card turned on; read the card's status line and `journalctl -u pvj-web` |
| A projector does not answer | PJLink must be switched on in the projector's network menu; check its address and password; a projector that is warming up or cooling down answers "busy" |
| The mapped picture stutters | Map at 1920x1080 or less on a Pi 4, and leave Edit on the display when you are done (editing costs more) |
| Schedule fires at the wrong time | Check the box clock shown on the Schedule card and the time zone |

## 8. Not built yet

Crossfade, Wi-Fi and hotspot, updates from the network, a panel update button, NDI, AES67/Dante, the presenter, importing old mapper files and custom DMX layouts. See [ROADMAP.md](../ROADMAP.md).

**SMPTE ST 2110** is not supported directly and not planned: use a converter from 2110 to HDMI into the live input (USB capture), or from 2110 to NDI once NDI is built.
