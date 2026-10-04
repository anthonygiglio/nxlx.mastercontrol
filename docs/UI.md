<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# The panel

The pictures on this page are made by `tests/ui/screenshots.js`, which drives the real panel against the same test harness as the browser test: a headless player, two-second test clips and a **fake network helper**. So the clip names, the addresses (`192.168.1.9`, `192.168.0.40`, the projectors at `192.168.1.50` and `.51`), the join codes and the network ports shown are made up for the picture; nothing here was taken from a real board. The Box and Sound output cards show the machine that took the pictures (a CI runner), not a Pi. Regenerate them with:

```
SHOTS=docs/images/ui node tests/ui/screenshots.js     # needs playwright, Chromium and mpv, like tests/ui/panel.test.js
```

The CI job "panel-ui" also runs it and uploads the result as the `ui-screenshots` artifact, so a change that breaks a screen shows up there.

## Pairing

Enter the four digit PIN shown on the box (`sudo pvj-pin`, or the projector test screen). A device is remembered until it is removed.

![Connect screen with a four digit PIN](images/ui/connect.png)

## Live

Twelve pads per bank, three banks. The pad that is playing is lit. Fade out, Freeze and Blackout are always at the bottom.

![Live screen with six labelled pads and one playing](images/ui/live.png)

The wide layout on a laptop or tablet, and one of the four themes (Dark stage is the default; Night red keeps a dark room dark):

![Live screen at desktop width](images/ui/live-desktop.png)
![Live screen in the Night red theme](images/ui/live-night-red.png)

### Transport and snapshot

Under Now playing: the position slider, Prev and Next (greyed out here because one clip is playing, not a list), 10 seconds back and forward, Fade in and Test pattern. Below it, Take snapshot shows one picture of what the box is showing, only when tapped.

![Now playing card with the transport, and the Screen card with Take snapshot](images/ui/live-transport.png)

## Mix

![Mix screen: sliders, transition, rotate](images/ui/mix.png)

### Mirror and overlay

Flip left-right and upside down for rear projection or a mirror rig. The overlay puts a PNG from the media folder over the video (a logo, or a mask).

![Mirror card with the two flip buttons](images/ui/mix-mirror.png)
![Overlay picture card with logo.png chosen](images/ui/mix-overlay.png)

### Projection mapping (beta)

Shown when the Projection mapper module is on. The canvas is the screen, drawn small: here a 3x2 grid named Back wall and a quad named Side panel, the chosen surface in yellow with its chosen corner in pink. Below: nudge buttons, the surface list in layer order, and saved mappings. See [MAPPER.md](../pvj/MAPPER.md).

![Projection mapping card with a grid and a quad on the canvas](images/ui/mapper.png)

## Media

Upload from the phone, play, rename or delete. Only full-access devices can change files.

![Media screen with uploaded clips](images/ui/media.png)

### Quick play and slideshow

Quick play plays the whole folder (looping, once or shuffled) or the clips whose names start with a number. The slideshow shows the pictures of the media folder or a USB drive, one after another.

![Quick play card: play all, play once, shuffle, play by number](images/ui/media-quickplay.png)
![Slideshow card: source, time per picture, ending, random order](images/ui/slideshow.png)

The Live input card (USB capture stick or webcam) appears only when the box has such a device; the machine that takes these pictures has none, so it has no picture here.

## System

![Vitals card](images/ui/system-vitals.png)

Modules are switched on here. Beta modules are off until you turn them on; modules that are not built yet say so.

![Modules card](images/ui/system-modules.png)

### Box and sound output

The Box card: software versions, free space, the screen outputs and their modes, the box clock, and Restart the box and Power off. Sound output chooses where the sound goes and plays a test tone on the left, right or both speakers.

![Box card](images/ui/box.png)
![Sound output card with test tone buttons](images/ui/sound-output.png)

### Autostart

![Autostart card](images/ui/autostart.png)

### Beta modules

| Weekly schedule (see [SCHEDULE.md](../pvj/SCHEDULE.md)) | Streams (see [STREAMS.md](../pvj/STREAMS.md)) |
| --- | --- |
| ![Schedule card](images/ui/schedule.png) | ![Streams card](images/ui/streams.png) |

The schedule above has an entry of each kind: projectors on, play a clip, a legacy start script, blackout and projectors off.

Projectors (see [PROJECTORS.md](../pvj/PROJECTORS.md)): two projectors at private addresses, each with On, Off, Picture mute, Unmute and Check, and All on and All off for both. The one with a password says so; the password itself is never shown.

![Projectors card with two projectors](images/ui/projectors.png)

| DMX (see [DMX.md](../pvj/DMX.md)) | MIDI (see [MIDI.md](../pvj/MIDI.md)) |
| --- | --- |
| ![DMX card](images/ui/dmx.png) | ![MIDI card](images/ui/midi.png) |

Network settings (wired and Wi-Fi) always revert by themselves unless you confirm them. See [NETWORK.md](../pvj/NETWORK.md).

![Network card](images/ui/network.png)

### Control, appearance and access

| OSC (see [OSC.md](../pvj/OSC.md)) | Appearance | Access |
| --- | --- | --- |
| ![OSC card](images/ui/control-osc.png) | ![Appearance card](images/ui/appearance.png) | ![Access card with a guest and a presenter code and their QR codes](images/ui/access.png) |

The Access card shows a guest code and a presenter code, each with its QR code, how long it lasts and how many uses are left.
