<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Weekly schedule (beta)

Play a clip or a legacy start script, stop, black out or show the screen, or switch the projectors on or off at a set time on chosen days. Switch the **Weekly schedule** module on under System > Modules, then use System > Schedule (full-access devices change it; view devices can read it). Off by default, and nothing runs until the module and the schedule switch are both on.

## What an entry can do

`play` (a clip from the media folder, loop on or off), `preset` (a legacy start script such as `startlessonce05`, in `"preset"`), `stop`, `blackout`, `show`, `projector_on` and `projector_off` (every projector added under System > Projectors, see [PROJECTORS.md](PROJECTORS.md)), and `vibes` (start the endless shader rotation, see [SHADERS.md](SHADERS.md); the module must be on; a `stop` entry ends it). Nothing else is schedulable: no shutdown, reboot or settings changes.

## Clock rules

- Times use the box's own clock and time zone. A Pi has no battery clock: until the network sets the time, its clock is wrong. Check the "Box clock" line in the panel before a show.
- Each entry fires at most once per minute.
- The scheduler only fires for minutes it actually watched. If the clock jumps (the network sets the time, someone changes it) or the service stalls for more than two minutes, the skipped minutes are not replayed. A wrong clock therefore cannot fire old events. Nothing fires for the minute the service starts.
- A failed entry (for example, a clip that was deleted) is recorded in the panel under "Last run" and does not stop other entries.

## API

`GET /api/schedule` (view) and `POST /api/schedule` (full) with `{"enabled": bool, "entries": [{"id"?, "label"?, "time": "HH:MM", "days": [0-6, Monday is 0], "action": "play|preset|stop|blackout|show|projector_on|projector_off|vibes", "file"?, "loop"?, "preset"?}]}`. The whole list is replaced on each save. At most 50 entries.

## Not verified on real hardware

Tested with a fake clock and a fake player in a container-style setup only. Not run on a Pi, and not tested across a real time-zone or daylight-saving change (entries follow the local wall clock; a change may skip or repeat a minute-of-day once).
