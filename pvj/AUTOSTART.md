<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Autostart

What the box plays by itself, with nobody at the panel: after power-up, and after a crash. It replaces the legacy Autostart tab. Set it under System > Autostart (full-access devices change it; everyone can see it). **Off by default.**

## Modes

| Mode | Plays |
| --- | --- |
| Off | Nothing; the box waits for you |
| Play one clip | One file from the media folder, looping or once |
| Play every clip | Every clip in the media folder as a playlist, looping or once (the old `startless` and `startlessonce`), in name order or shuffled |
| Slideshow of the pictures | The pictures in the media folder, a set time each (1 to 3600 seconds), in order or shuffled, looping or once |
| Play a pad | What a pad plays, with its own end-of-clip setting |
| Play the USB stick | The clips at the top of the newest USB drive, looping, in order or shuffled; and **each time a drive with clips is plugged in**, that drive (a venue technician swaps the stick and it plays). A drive with no clips is ignored. |
| Legacy start script | A name such as `startlessonce05` (see `pvj/README.md` for the table); slave, stream and wifi presets are not ported |
| Vibes | The endless shader rotation of the Shaders and Vibes module (see [SHADERS.md](SHADERS.md)). The module must be on, or the last result says so. |

An optional wait (0 to 120 seconds) lets a projector wake up before the first clip.

## When it runs

- When the web service starts and it finds the player running (the player service starts first at boot).
- Again whenever the **player has been restarted** (for example systemd brought mpv back after a crash). It checks the player's process id every two seconds. After a restart the wait is at most five seconds, so a recovery is quick.
- **Never just because playback stopped.** If you press Stop, or a clip ends, and the same player is still running, nothing starts. That is deliberate: a box that fights the operator is worse than one that waits.

"Run it now" in the panel does what a restart would do, so you can check the setting without rebooting. The panel shows the last result; a missing clip is reported there and does not stop anything else.

## Not verified

Tested with a fake player (process ids, delays, failure handling) and through the real API; **never run through a real reboot or a real mpv crash on a board**. Not built: starting from a USB drive when it appears (the legacy `startmasterusb` presets work through "Legacy start script" only if the drive is already mounted when it runs), a per-day choice (use the schedule), and audio output selection.
