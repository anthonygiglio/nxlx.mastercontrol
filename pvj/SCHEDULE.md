<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Weekly schedule (beta)

Play a clip or a legacy start script, stop, black out or show the screen, or switch the projectors on or off at a set time on chosen days. Switch it on under System > Schedule with the switch at the top of the page: it is the only switch (full-access devices change it; view devices can read it). Off by default. The switch turns on the module and then the schedule itself, and shows On only when both are on. If there are saved entries it asks first, because they start running at their times at once ("Switch the schedule on? 5 entries will start running at their times."), and the System row then shows the next one. Switching off stops the schedule and keeps the entries (the module stays on, so they can still be read). In the API these are still two things: the `scheduler` module and `enabled` in `POST /api/schedule`.

## What an entry can do

`play` (a clip from the media folder, loop on or off), `preset` (a legacy start script such as `startlessonce05`, in `"preset"`), `stop`, `blackout`, `show`, `projector_on` and `projector_off` (every projector added under System > Projectors, see [PROJECTORS.md](PROJECTORS.md)), and `vibes` (start the endless shader rotation, see [SHADERS.md](SHADERS.md); the module must be on; a `stop` entry ends it; an optional `"set"` holds the id of the rotation set to run, without it the active set runs), and `scene` (apply a scene of the Room module, by its id in `"scene"`: groups of projectors on or off, their source and mutes, and what the box plays, see [ROOM.md](ROOM.md); a scene that plays a clip, a pad, a stream or Vibes also takes a blackout off, so a `blackout` entry before it does not leave the screen dark; the entry answers at once and the projectors follow in the background, so "Last run" says whether the scene was started, and the Room screen how it went). Nothing else is schedulable: no shutdown, reboot or settings changes.

## Clock rules

- Times use the box's own clock and time zone. A Pi has no battery clock: until the network sets the time, its clock is wrong. Check the "Box clock" line in the panel before a show.
- Each entry fires at most once per minute.
- The scheduler only fires for minutes it actually watched. If the clock jumps (the network sets the time, someone changes it) or the service stalls for more than two minutes, the skipped minutes are not replayed. A wrong clock therefore cannot fire old events. Nothing fires for the minute the service starts.
- A failed entry (for example, a clip that was deleted) is recorded in the panel under "Last run" and does not stop other entries.

## API

`GET /api/schedule` (view) and `POST /api/schedule` (full) with `{"enabled": bool, "entries": [{"id"?, "label"?, "time": "HH:MM", "days": [0-6, Monday is 0], "action": "play|preset|stop|blackout|show|projector_on|projector_off|vibes|scene", "file"?, "loop"?, "preset"?, "scene"?}]}`. The whole list is replaced on each save. At most 50 entries.

## Not verified on real hardware

Tested with a fake clock and a fake player in a container-style setup only. Not run on a Pi, and not tested across a real time-zone or daylight-saving change (entries follow the local wall clock; a change may skip or repeat a minute-of-day once).
