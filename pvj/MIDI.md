<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# MIDI controllers (beta)

Play pads, fade and mix from USB MIDI controllers: pad grids, fader boxes, keyboards. Switch it on under System > MIDI controller (full-access devices only) with the switch at the top of the page: it is the only switch, and the box reads controllers as soon as it says On. Off until you switch it on; switching off switches the module off and keeps your mappings. In the API these are still two things: the `control-midi` module and `enabled` in `POST /api/midi`.

**Every controller that is plugged in is read at once**, and a controller unplugged and replugged is picked up again within a couple of seconds. Nothing is written back to a controller (no lights or motor faders yet).

**A controller the box knows works as soon as it is plugged in**: a Korg nanoKONTROL2, an Akai MIDI Mix and a Novation Launchpad Mini each have a ready-made layout (a profile). Nothing has to be taught. Any other controller is taught with Learn, as before.

## Controller profiles

A profile is one file in `pvj/controllers.d/`: which controller it is for, a drawing of it, what each control sends and what each does. When a matching controller appears (the box looks every 2 seconds) its layout applies by itself and the MIDI page shows a card for it: "Korg nanoKONTROL2: recognised, standard layout on."

**None of the three layouts has been tried on the real controllers yet.** They come from documents and one recording (see each table), not from pressing every control. The check list below takes two minutes per controller.

### How it behaves

- **What wins.** For each control: a mapping you made (by Learn, or by tapping the control on its card) wins; else the controller's standard layout; else the built-in map. The built-in map is **not** used for a controller whose standard layout is on (otherwise knob 5 of a nanoKONTROL2, which sends CC 20, would also be the built-in map's opacity). A mapping made for "any controller" also wins over a standard layout for that control. If a control has both, the controller's own mapping is the one that runs.
- **Standard layout: on or off**, a switch on the controller's card, per controller and kept in the settings. Off leaves your own mappings and the built-in map, without unplugging anything.
- **Several controllers at once** each use their own layout. A second controller of the same model (ALSA calls it `Mini_1`) uses the same layout; its switch and your mappings are its own.
- **Matching** is by the ALSA card id (the last word of the product name: `nanoKONTROL2`, `Mix`, `Mini`) and, because other products end in the same word, by the product name ALSA shows in `/proc/asound/cards` ("Launchpad Mini", so a Launchkey Mini is not taken for one). Both are matched as a whole against the patterns in the file.
- **Pickup (soft takeover).** Some levels would visibly jump if a fader took effect where it happens to stand, so on a recognised controller they wait: nothing changes until the fader or knob reaches (or passes) the value the box has, and from then on it is followed. The card shows such a control with a dotted edge while it waits. If the value is changed elsewhere while the control rests, it waits again.

  | Level | Pickup | Why |
  | --- | --- | --- |
  | Opacity | yes | a fader left at the bottom would black the screen out |
  | Volume, clip speed | yes | a jump is heard or seen. The box does not keep these two in memory, so the value a controller last set is used, and 100 percent and 1x before that: after a change in the panel the first move may still jump |
  | Shader speed, shader brightness | yes | a jump to frozen or to black |
  | Shader control 1 to 8, shader colour turn | no | a jump is part of playing a shader, and a knob that waits feels broken |
  | Size, position, Vibes time | no | not in a standard layout's faders; they follow at once |

  Mappings you make on a recognised controller pick up the same way. Plain Learn mappings on a controller with no profile behave as before (no pickup).
- **The same press twice.** Blackout and the Room scenes need two presses of the same button within a second (and at least a quarter of a second apart); one press does nothing. They are marked "2x" on the card. A Room scene can switch projectors off for the whole room, so it is treated like blackout. This guard belongs to the standard layout: if you put blackout on a control yourself, one press is enough.
- **The controllers' bank.** A nanoKONTROL2 and a MIDI Mix have one row of eight pad buttons, so the box keeps a bank for controllers (A at start, not saved): the row plays pads 1 to 8 of that bank and two buttons step it. Pads 9 to 12 are not on these two layouts. The Launchpad Mini has room for all three banks and needs no bank button.
- **Same path as everything else.** A profile only chooses which API call a control makes; the call goes through the API as a presenter, so the module switches, every check and the 50 commands a second apply, and no control waits for the shader engine (tested with the engine's lock held, for every control of every layout).
- **Lights are not built.** See "Not built yet".

### Korg nanoKONTROL2 (`korg-nanokontrol2`)

**Unverified.** Korg's nanoKONTROL2 Parameter Guide describes every parameter of CC mode but prints no table of the factory numbers. The numbers below are the factory assignments as widely reported and as used by the Mixxx project's mapping for this controller. The controller must be in **CC mode**: hold SET MARKER and CYCLE while plugging it in. In a DAW mode its faders send pitch bend, which the box does not read, and the layout looks dead. Factory reset (Parameter Guide p. 13): hold PREV TRACK, NEXT TRACK and CYCLE while plugging it in.

| Control | Sends | Does |
| --- | --- | --- |
| Knob 1 to 8 | CC 16 to 23 | Shader control 1 to 8 of the shader on screen |
| Fader 1 | CC 0 | Opacity (pickup) |
| Fader 2 | CC 1 | Volume (pickup) |
| Fader 3 | CC 2 | Clip speed (pickup) |
| Fader 4 | CC 3 | Shader speed (pickup) |
| Fader 5 | CC 4 | Shader colour turn |
| Fader 6 | CC 5 | Shader brightness (pickup) |
| Fader 7, 8 | CC 6, 7 | Spare |
| S 1 to 8 | CC 32 to 39 | Pad 1 to 8 of the controllers' bank |
| M 1 to 8 | CC 48 to 55 | Shader preset 1 to 8 |
| R 1 to 4 | CC 64 to 67 | Room scene 1 to 4 (press twice) |
| R 5 | CC 68 | Spare |
| R 6, R 7 | CC 69, 70 | Fade in, fade out |
| R 8 | CC 71 | Blackout on / off (press twice) |
| Track <, Track > | CC 58, 59 | Previous shader, next shader |
| Cycle | CC 46 | Vibes on / off |
| Marker Set | CC 60 | Spare |
| Marker <, Marker > | CC 61, 62 | Controllers' bank: the one before, the next |
| Rewind, Forward | CC 43, 44 | Previous clip, next clip (of a playlist) |
| Stop, Play | CC 42, 41 | Stop, Pause / resume |
| Rec | CC 45 | Spare |

### Akai MIDI Mix (`akai-midimix`)

**Unverified.** No Akai document with the factory numbers was found (the support article on the editor gives none; the User Guide could not be read when this was written). The numbers are those of a public Ableton Live script written for the factory preset (billmoser/MIDIMixD). Holding **Solo** makes the Mute buttons send a second set of notes; they are drawn as a row of their own, "Solo+Mute".

| Control | Sends | Does |
| --- | --- | --- |
| Knob A1 to A8 (top row) | CC 16, 20, 24, 28, 46, 50, 54, 58 | Shader control 1 to 8 |
| Knob B1, B2, B3 (middle row) | CC 17, 21, 25 | Size, position X, Vibes: time each shader stays |
| Knob B4 to B8 | CC 29, 47, 51, 55, 59 | Spare |
| Knob C1 to C8 (bottom row) | CC 18, 22, 26, 30, 48, 52, 56, 60 | Spare |
| Fader 1 to 6 | CC 19, 23, 27, 31, 49, 53 | As on the nanoKONTROL2: opacity, volume, clip speed, shader speed, colour turn, shader brightness |
| Fader 7, 8, Master | CC 57, 61, 62 | Spare |
| Mute 1 to 8 | Note 1, 4, 7, 10, 13, 16, 19, 22 | Pad 1 to 8 of the controllers' bank |
| Rec Arm 1 to 8 | Note 3, 6, 9, 12, 15, 18, 21, 24 | Shader preset 1 to 8 |
| Solo+Mute 1 to 8 | Note 2, 5, 8, 11, 14, 17, 20, 23 | Stop, Pause / resume, Vibes on / off, previous shader, next shader, fade in, fade out, blackout (press twice) |
| Bank Left, Bank Right | Note 25, 26 | Controllers' bank: the one before, the next |
| Solo | Note 27 | Spare (it is the shift for the row above) |

Room scenes are not on this layout; put one on a spare control from the Room screen or the card.

### Novation Launchpad Mini (`novation-launchpad-mini`)

The **original** Launchpad Mini, not the MK3 (which sends other numbers and has another card id). From Novation's Launchpad S Programmer's Reference Manual 1.02, whose protocol the original Mini shares: in the power-on X-Y layout a pad sends note 16 x row + column (from 0, top left), the round buttons on the right are column 8 (notes 8, 24 ... 120) and the round buttons along the top send CC 104 to 111. The recording of the owner's unit (D21: notes 20 to 103 and CC 104 to 111) fits: every number is one of that layout, and note 20 does not exist in the other (drum rack) layout. The recording did not cover every pad.

| Control | Sends | Does |
| --- | --- | --- |
| Top 1 to 8 | CC 104 to 111 | Previous shader, next shader, Vibes on / off, previous clip, next clip, Pause / resume, fade in, fade out |
| Grid rows 1 and 2, columns 1 to 6 | Notes 0 to 5, 16 to 21 | Bank A, pads 1 to 6 and 7 to 12 |
| Grid rows 3 and 4, columns 1 to 6 | Notes 32 to 37, 48 to 53 | Bank B, pads 1 to 12 |
| Grid rows 5 and 6, columns 1 to 6 | Notes 64 to 69, 80 to 85 | Bank C, pads 1 to 12 |
| Grid rows 1 to 6, columns 7 and 8 | Notes 6, 7, 22, 23 ... 86, 87 | Spare |
| Grid row 7 | Notes 96 to 103 | Shader preset 1 to 8 |
| Grid row 8 | Notes 112 to 119 | Shader control 1 to 8, as a press (a switch toggles, a choice steps, an event fires, a number goes back to its own value) |
| A to D (right) | Notes 8, 24, 40, 56 | Room scene 1 to 4 (press twice) |
| E, F | Notes 72, 88 | Spare |
| G | Note 104 | Stop |
| H | Note 120 | Blackout on / off (press twice) |

### The two-minute check, per controller

With the controller plugged in, open System > MIDI controller and look at its card.

1. The card says "recognised, standard layout on". If there is no card or it says "No built-in layout", note the name it shows: the card id or product name in the profile is wrong.
2. Move or press **each control once** and watch its box on the card light up. A box that does not light is a wrong number in the profile (or, on the nanoKONTROL2, the controller is not in CC mode). A different box lighting is a swapped number.
3. For the MIDI Mix: hold Solo and press each Mute; the Solo+Mute row should light.
4. Tell us what did not light. Anything can be put right for now by tapping the control and choosing its action, or with Learn.

### On the page

Each connected controller has a card: its name and state line, the switch "Standard layout", the drawn layout (a grid built from the profile's positions; on a phone a wide controller scrolls sideways inside its card), and under it the profile's note. A control shows what it does now; it lights while it is moved; a dashed edge means "changed by you"; a dotted edge means it waits for pickup. Tap a control to see what it sends and to choose another action (Save), or "Nothing" to switch it off; "Back to the standard" undoes that for the control, and "Back to the standard for the whole controller" for all of them, after a question in place. Both remove only that controller's own mappings on the controls of its layout; a mapping made for any controller is shown on the control as such and is removed in the list of mappings. A controller with no profile gets a card that says "No built-in layout for this one yet. Teach it below."

### Writing a profile for another controller

Add one file, `pvj/controllers.d/<id>.json`; nothing else changes. All keys are required unless marked; unknown keys are refused, and a file that fails the check is left out with a line in the log (`journalctl -u pvj-web`). The test `tests/test_controllers.py` checks every file in the folder.

```json
{
  "id": "maker-model",
  "name": "Maker Model",
  "match": {"card_ids": ["Model(_[0-9]+)?"], "card_names": ["Maker Model( [0-9]+)?"]},
  "description": "What it is and which mode it must be in.",
  "note": "(optional) what is and is not verified, shown on the card",
  "sources": ["The document the numbers come from, with its version"],
  "layout": {"rows": 2, "cols": 8},
  "controls": [
    {"id": "fader1", "name": "Fader 1", "row": 1, "col": 0, "kind": "fader",
     "send": {"type": "cc", "channel": 0, "number": 0}, "action": {"action": "opacity"}},
    {"id": "pad1", "name": "Pad 1", "row": 0, "col": 0, "kind": "pad",
     "send": {"type": "note", "channel": 0, "number": 36}, "action": {"action": "pad", "bank": 0, "index": 0}},
    {"id": "b8", "name": "B 8", "row": 0, "col": 7, "kind": "button",
     "send": {"type": "note", "channel": 0, "number": 43}, "action": {"action": "blackout"}, "guard": true, "unverified": true}
  ]
}
```

- `id` is the file's name: small letters, digits, dashes. `match.card_ids` (one to eight) and `match.card_names` (none to eight) are patterns matched against the whole card id and the whole product name (`cat /proc/asound/cards` on the box shows both: ` 2 [Mini ]: USB-Audio - Launchpad Mini`). Allow the `_1` that ALSA adds for a second unit.
- `layout` is the grid of the drawing (up to 16 by 16); each control has its own `row` and `col` from 0, a `name` as printed on the hardware (up to 24 characters), and a `kind`: `fader`, `knob`, `button` or `pad`.
- `send`: `type` is `note` or `cc`, `number` 0 to 127, `channel` 1 to 16 or 0 for any (use 0 unless two controls differ only by channel).
- `action` is `null` for a spare control, or an action from the table below with its fields (`pad` needs `bank` 0 to 2 and `index` 0 to 11, `bank_pad` needs `index`). A fader or knob needs an action that follows it; a button or pad one that is pressed. A scene by id and `none` are not allowed in a profile.
- `guard` (optional, buttons and pads): the press is needed twice. Use it for blackout and anything room-wide. `unverified` (optional): the number is not confirmed by the maker's document; say so in `note` too.
- Up to 160 controls; no two in one place, no two sending the same message. There is no `lights` key.

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
| Shader colour turn, Shader brightness (`shader_hue`, `shader_brightness`) | level | The palette shift, -180 to 180 degrees (the middle is none), and the brightness trim, 0 to 2 (the middle leaves it alone) |
| Previous clip, Next clip (`clip_prev`, `clip_next`) | trigger | The neighbour in the playlist that is playing (a folder, a preset); nothing when there is none |
| Fade in (`fadein`) | trigger | From black up to the mix opacity in 2 seconds; also ends a blackout |
| Room scene 1 to 8 (`scene_1` ...) | trigger | Applies the n-th scene of the Room screen's list (needs the Room module; see below for a scene by its id) |
| Pad of the controllers' bank (`bank_pad`, with the pad 1 to 12) | trigger | Plays that pad of the bank the controllers are on |
| Controllers' bank: the one before, the next (`bank_prev`, `bank_next`) | trigger | Steps that bank through A, B, C and round. It starts at A and is not saved; it is not the bank a phone shows |
| Nothing (`none`) | trigger | Does nothing: switches one control of a standard layout off |

A fourth kind of action, **Apply a Room scene** (`scene`, a trigger that carries the scene's id), is assigned from the Room screen: under "Set up the room" each scene has a MIDI button that starts the same Learn. The mapping is then listed here like the others (as "scene"), and removed here. It needs the Room module; a scene removed later leaves a mapping that does nothing, and the log says so. See [ROOM.md](ROOM.md).

The Vibes and shader actions are assigned with Learn like any other; they are not in the built-in map. They need the **Shaders and Vibes** module to be on: while it is off they do nothing, and the log says so once. They go through the same calls as the panel (`/api/shaders/values`, `/api/shaders/step`, `/api/shaders/preset`), as a presenter: every value is checked there, and a call only notes the wish and answers, so the thread that reads the controller never waits for the GPU. A knob sweep reaches the box at 20 changes a second and the GPU at five at most; the last position always lands. The MIDI page's action list offers all of them; the Shaders page teaches the three Vibes actions.

A trigger fires once per press (a note-on, or a CC that goes from below 64 to 64 or more), not on release or repeat, and a button cannot fire again within a quarter of a second, so contact bounce cannot repeat it. Right after Learn captures a control, that control is ignored for about half a second so a fader you are still moving does not run its old mapping. A fader sweep is thinned to 20 changes a second and the last position always lands.

## Built-in map

On unless you turn it off (System > MIDI controller > Built-in map). It exists so a plain pad controller works with no setup: notes 36 to 71 (and program changes 0 to 35) play pads 1 to 36 (A is 1 to 12, B 13 to 24, C 25 to 36); notes 72 to 76 are stop, pause, blackout, fade out and reset; CC 20 to 24 are opacity, size, position, speed and volume; CC 25 is blackout while up. Real controllers rarely use these numbers (a Novation Launchpad Mini sends notes 0 to 120 and CC 104 to 111), so a controller without a profile is taught with Learn. The built-in map is not used for a controller whose standard layout is on.

## Safety

- Only paths of the form `/dev/snd/midiC<n>D<n>` are ever opened, and only if they are character devices (no links).
- Only the actions in the table are reachable: nothing shuts down, reboots or changes settings (the one setting a controller can change is the Vibes dwell time, between 15 seconds and an hour).
- At most 50 commands a second reach the player, whatever the controllers send, and a pad or button can fire at most four times a second (a single "play" is many round trips to the player, so the second limit is the one that matters for pads).
- The web service reads the device through systemd: it needs the `audio` group and read access to ALSA devices, and the unit has both (`DeviceAllow=char-alsa r`).

## Not built yet

- **Lights** (a lit pad for a pad with a clip, brighter while it plays; the nanoKONTROL2's button lights). Left out on purpose, with what it would take written down (D49):
  - The panel's service may only **read** ALSA devices (`DeviceAllow=char-alsa r`, checked by a test). Writing needs `rw` in `install/pvj-web.service`, and a unit file is not part of an update or of a quick deploy, so a box would have code that wants to write under a unit that forbids it; the writer would have to notice and say "lights need the updated service file".
  - A small writer per profiled controller on a second, write-only handle to the same device (never the reader's), non-blocking, dropping what does not fit, sending only what changed and at most a few dozen messages a second; an optional `lights` section in the profile; never anything to a device without a profile, which is why matching also checks the product name.
  - What to show needs the pads and what is playing, read from memory only (the writer must not ask the player from the reader's thread).
  - For the Launchpad (Programmer's Reference): a note-on to a pad's note with a colour as the velocity (its examples: 12 off, 15 red, 60 green, 63 amber; dimmer ones by its formula), `B0 00 00` resets it. For the nanoKONTROL2 (Parameter Guide p. 9): its **LED Mode must be set to External in the KORG KONTROL Editor**; a light then follows a message with that button's own CC number. In Internal mode a button's light follows the button itself.
  - And it has to be tried on the hardware, which this work could not do.
- Motor-fader feedback (needs MIDI output too).
- Mapping to pads by name, banks that follow the controller's own bank buttons, and relative (endless) encoders.

## Verified, and not

Verified on a real Raspberry Pi 4 (2026-09-30): the module reads a controller through the systemd sandbox and handled 112 messages in a few seconds from a Launchpad Mini. Three controllers (Korg nanoKONTROL2, Akai MIDI Mix, Novation Launchpad Mini) enumerate. **Learn, the multi-controller hub and the new map have only run against pipes standing in for controllers and the browser test, not yet against the real hardware.** The Vibes and shader actions have run only in unit tests with a fake player and a fake clock: no real controller has sent them.

**Controller profiles (2026-10-04): not tried on any real controller.** The three layouts are from the documents named above and one recording of the Launchpad Mini; the nanoKONTROL2's and the MIDI Mix's numbers are not confirmed by a manufacturer's document at all. Matching by `/proc/asound/cards` was written from the format of that file and tested against a made-up copy, not read on the Pi. Pickup, the double press, hot-plug and the drawn layout ran against pipes, a fake clock and the browser test's fake controller. Please run the two-minute check.
