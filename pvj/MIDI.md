<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# MIDI controllers (beta)

Play pads, fade and mix from USB MIDI controllers: pad grids, fader boxes, keyboards. Switch it on under System > MIDI controller (full-access devices only) with the switch at the top of the page: it is the only switch, and the box reads controllers as soon as it says On. Off until you switch it on; switching off switches the module off and keeps your mappings. In the API these are still two things: the `control-midi` module and `enabled` in `POST /api/midi`.

**Every controller that is plugged in is read at once**, and a controller unplugged and replugged is picked up again within a couple of seconds. Nothing is written back to a controller (no lights or motor faders yet).

## Learn: assign a control to an action

1. Choose the action (and the pad, for "Play a pad").
2. Tap **Learn a control**, then move or press the control on any controller (you have 20 seconds). Nothing runs while it is listening.
3. It is saved and shown in the list, for example `nanoKONTROL2 · CC 0 → Opacity (fader)`.

A mapping belongs to one controller (by its ALSA card id, such as `nanoKONTROL2`, `Mix` or `Mini`, which stays the same when USB numbering changes between boots). Learning the same control again replaces its mapping. Up to 200 mappings. **A control you have mapped uses only your mapping**: it replaces the built-in one for that control instead of firing next to it (so learning note 36 as Stop does not also play pad 1). Controls you have not mapped still use the built-in map, and a mapping made for one controller does not switch off the built-in map on another.

## Actions

| Action | Kind | Does |
| --- | --- | --- |
| Play a pad | trigger | Plays that pad (bank A to C, pads 1 to 12) |
| Stop, Pause / resume, Blackout on / off, Fade out, Reset mix | trigger | As in the panel |
| Opacity, Size, Position X, Speed, Volume | level | Follows the control, 0 to 127 spread over the range (opacity 0 to 100 percent, size 1 to 200, position -100 to 100, speed 0.25x to 2x, volume 0 to 100) |
| Blackout while held up | level | Black at 64 or more, shown below |
| Vibes on / off | trigger | Starts the endless shader rotation, or stops it if it is running (see [SHADERS.md](SHADERS.md)) |
| Vibes: next shader | trigger | Goes to the next shader now (only while Vibes is running) |
| Vibes: time each shader stays | level | A knob or fader picks one of 15 steps from bottom to top: 15, 30, 45, 60, 90, 120, 180, 240, 300, 420, 600, 900, 1200, 1800, 3600 seconds. It is saved, and the shader on screen follows the new time |
| Shader control 1 to 8 (`shader_control_1` ...) | follows the control | The n-th input of the shader on screen, whatever it is (numbers, switches, choices and events, in the file's order; colours and points have no knob). From a **knob or fader**: a number spreads over its MIN to MAX, a switch is on from 64 up, a choice is picked by position, an event fires at 64 or more. From a **pad or button**: a switch toggles, a choice steps to the next (round the end), an event fires, a number goes back to the file's own value. A shader with fewer inputs ignores the rest |
| Shader speed (`shader_speed`) | level | 0 (frozen) to 4 times; the shader's own pace, 1, is a quarter of the way up. TIME goes on from where it is |
| Previous shader, Next shader (`shader_prev`, `shader_next`) | trigger | Within the active set: steps Vibes while it runs, otherwise puts on the neighbour of the shader on screen |
| Preset 1 to 8 (`shader_preset_1` ...) | trigger | Applies that preset of the shader on screen, in the order they were saved |

A fourth kind of action, **Apply a Room scene** (`scene`, a trigger that carries the scene's id), is assigned from the Room screen: under "Set up the room" each scene has a MIDI button that starts the same Learn. The mapping is then listed here like the others (as "scene"), and removed here. It needs the Room module; a scene removed later leaves a mapping that does nothing, and the log says so. See [ROOM.md](ROOM.md).

The Vibes and shader actions are assigned with Learn like any other; they are not in the built-in map. They need the **Shaders and Vibes** module to be on: while it is off they do nothing, and the log says so once. They go through the same calls as the panel (`/api/shaders/values`, `/api/shaders/step`, `/api/shaders/preset`), as a presenter: every value is checked there, and a call only notes the wish and answers, so the thread that reads the controller never waits for the GPU. A knob sweep reaches the box at 20 changes a second and the GPU at five at most; the last position always lands. The page that teaches these controls offers only the three Vibes actions so far; the others can be added through `POST /api/midi/map` until the page lists them.

A trigger fires once per press (a note-on, or a CC that goes from below 64 to 64 or more), not on release or repeat, and a button cannot fire again within a quarter of a second, so contact bounce cannot repeat it. Right after Learn captures a control, that control is ignored for about half a second so a fader you are still moving does not run its old mapping. A fader sweep is thinned to 20 changes a second and the last position always lands.

## Built-in map

On unless you turn it off (System > MIDI controller > Built-in map). It exists so a plain pad controller works with no setup: notes 36 to 71 (and program changes 0 to 35) play pads 1 to 36 (A is 1 to 12, B 13 to 24, C 25 to 36); notes 72 to 76 are stop, pause, blackout, fade out and reset; CC 20 to 24 are opacity, size, position, speed and volume; CC 25 is blackout while up. Real controllers rarely use these numbers (a Novation Launchpad Mini sends notes 20 to 103 and CC 104 to 111), so expect to learn your own.

## Safety

- Only paths of the form `/dev/snd/midiC<n>D<n>` are ever opened, and only if they are character devices (no links).
- Only the actions in the table are reachable: nothing shuts down, reboots or changes settings (the one setting a controller can change is the Vibes dwell time, between 15 seconds and an hour).
- At most 50 commands a second reach the player, whatever the controllers send, and a pad or button can fire at most four times a second (a single "play" is many round trips to the player, so the second limit is the one that matters for pads).
- The web service reads the device through systemd: it needs the `audio` group and read access to ALSA devices, and the unit has both (`DeviceAllow=char-alsa r`).

## Not built yet

- **Controller profiles** (a ready-made layout for a known controller, like Ableton's control-surface scripts): the learn map is the base for them; a profile would be a shipped file of mappings for a named controller.
- Lights and motor-fader feedback to the controller (needs MIDI output).
- Mapping to pads by name, banks that follow the controller's own bank buttons, and relative (endless) encoders.

## Verified, and not

Verified on a real Raspberry Pi 4 (2026-09-30): the module reads a controller through the systemd sandbox and handled 112 messages in a few seconds from a Launchpad Mini. Three controllers (Korg nanoKONTROL2, Akai MIDI Mix, Novation Launchpad Mini) enumerate. **Learn, the multi-controller hub and the new map have only run against pipes standing in for controllers and the browser test, not yet against the real hardware.** The Vibes and shader actions have run only in unit tests with a fake player and a fake clock: no real controller has sent them.
