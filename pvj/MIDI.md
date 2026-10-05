<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# MIDI controllers (beta)

Play pads, fade and mix from USB MIDI controllers: pad grids, fader boxes, keyboards. Switch it on under System > MIDI controller (full-access devices only) with the switch at the top of the page: it is the only switch, and the box reads controllers as soon as it says On. Off until you switch it on; switching off switches the module off and keeps your mappings. In the API these are still two things: the `control-midi` module and `enabled` in `POST /api/midi`.

**Every controller that is plugged in is read at once**, and a controller unplugged and replugged is picked up again within a couple of seconds. The box lights the buttons and pads of a controller it knows (see "Lights"); nothing else is written to a controller, and nothing at all to one it does not know (no motor faders).

**A controller the box knows works as soon as it is plugged in**: a Korg nanoKONTROL2, an Akai MIDI Mix and a Novation Launchpad Mini each have a ready-made layout (a profile). Nothing has to be taught. Any other controller is taught with Learn, as before.

## Controller profiles

A profile is one file in `pvj/controllers.d/`: which controller it is for, a drawing of it, what each control sends and what each does. When a matching controller appears (the box looks every 2 seconds) its layout applies by itself and the MIDI page shows a card for it: "Korg nanoKONTROL2: recognised, standard layout on."

**None of the three layouts has been tried on the real controllers yet.** They come from documents and one recording (see each table), not from pressing every control. The check list below takes two minutes per controller.

### How it behaves

- **What wins.** For each control: a mapping you made (by Learn, or by tapping the control on its card) wins; else the controller's standard layout; else the built-in map. The built-in map is **not** used for a controller whose standard layout is on (otherwise knob 5 of a nanoKONTROL2, which sends CC 20, would also be the built-in map's opacity). A mapping made for "any controller" also wins over a standard layout for that control. If a control has both, the controller's own mapping is the one that runs.
- **Standard layout: on or off**, a switch on the controller's card, per controller and kept in the settings. Off leaves your own mappings and the built-in map, without unplugging anything. Only a controller that is or was plugged in since the panel started can be switched.
- **Unplugged or switched off means silent.** A message still on its way from a controller that has gone, or after MIDI was switched off, is dropped; it is never handed to the built-in map.
- **Several controllers at once** each use their own layout. A second controller of the same model (ALSA calls it `Mini_1`) uses the same layout; its switch and your mappings are its own.
- **Matching.** The surest key is the USB id (`/proc/asound/card<n>/usbid`, such as `1235:0036`): a controller whose USB id a profile lists is that controller. Otherwise the ALSA card id must be one the profile lists (it is only the last word of the product name: `nanoKONTROL2`, `Mix`, `Mini`; ALSA adds `_1` for a second unit) **and** the product name in `/proc/asound/cards` must be one the profile lists ("Launchpad Mini", so a Launchkey Mini is not taken for one). Names are compared exactly; a profile holds no patterns. The card list is read as bytes and only the row with the card's own number and id counts. If the list could be read but gives no usable name (odd characters, too long), the controller is **not** matched by its card id alone; only where there is no card list at all does the id decide. The ids and names in the three files are what the owner's Pi shows (2026-10-05).
- **Pickup (soft takeover).** Some levels would visibly jump if a fader took effect where it happens to stand, so on a recognised controller they wait: nothing changes until the fader or knob reaches (or passes) the value the box has, and from then on it is followed. The card shows such a control with a dotted edge while it waits. If the value is changed elsewhere while the control rests, it waits again. The last few steps at each end count as the end, so a worn fader that tops out at 122 still reaches 100 percent.

  | Level | Pickup | Why |
  | --- | --- | --- |
  | Opacity | yes | a fader left at the bottom would black the screen out |
  | Volume, clip speed | yes | a jump is heard or seen. The box remembers what was last set through it (the panel, OSC, a controller); before anything was set it takes the player's own start values, 100 percent and 1x |
  | Shader speed, shader brightness | yes | a jump to frozen or to black. For a shader of the category Performance the box keeps the speed at 1 or below unless "faster" is switched on; the fader then does nothing above a quarter of its way |
  | Shader control 1 to 8, shader colour turn | no | a jump is part of playing a shader, and a knob that waits feels broken |
  | Size, position, Vibes time | no | not in a standard layout's faders; they follow at once |

  Mappings you make on a recognised controller pick up the same way. Plain Learn mappings on a controller with no profile behave as before (no pickup).
- **The same press twice.** Blackout and the Room scenes need two presses of the same button within a second (and at least a quarter of a second apart); one press does nothing. They are marked "2x" on the card. A Room scene can switch projectors off for the whole room, so it is treated like blackout. If you put blackout or a Room scene on a control yourself (on its card), it is guarded too: the chooser shows a switch **Press twice**, on unless you switch it off. Saving a control without changing it stores nothing, so the guard cannot be lost by pressing Save. A mapping made with plain Learn is not guarded, as before.
- **The controllers' bank.** A nanoKONTROL2 and a MIDI Mix have one row of eight pad buttons, so the box keeps a bank for controllers (A at start, not saved): the row plays pads 1 to 8 of that bank and two buttons step it. Pads 9 to 12 are not on these two layouts. The Launchpad Mini has room for all three banks and needs no bank button.
- **Same path as everything else.** A profile only chooses which API call a control makes; the call goes through the API as a presenter, so the module switches, every check and the 50 commands a second apply, and no control waits for the shader engine (tested with the engine's lock held, for every control of every layout).
- **Lights.** The box shows its state on the controller's own lights: see "Lights" below. Switching a standard layout off also switches that controller's lights off.

### Korg nanoKONTROL2 (`korg-nanokontrol2`)

**Unverified.** Korg's nanoKONTROL2 Parameter Guide describes every parameter of CC mode but prints no table of the factory numbers. The numbers below are the factory assignments as widely reported and as used by the Mixxx project's mapping for this controller. The controller must be in **CC mode**: hold SET MARKER and CYCLE while plugging it in. It starts in the mode it was last used in, so one that came from a DAW set-up stays in that DAW's mode until you do this once. In a DAW mode its faders send pitch bend, which the box does not read, and the layout looks dead (the card then says "Nothing received yet"). In Korg's editor a button can be set to Toggle instead of Momentary; a toggling button then acts on every other press only. The layout expects the factory setting, Momentary. Factory reset (Parameter Guide p. 13): hold PREV TRACK, NEXT TRACK and CYCLE while plugging it in.

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
| Marker Set | CC 60 | Vibes: start the set Ambient |
| Marker <, Marker > | CC 61, 62 | Controllers' bank: the one before, the next |
| Rewind, Forward | CC 43, 44 | Previous clip, next clip (of a playlist) |
| Stop, Play | CC 42, 41 | Stop, Pause / resume |
| Rec | CC 45 | Vibes: start the set Show |

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

Room scenes and the two Vibes sets are not on this layout (it has no free buttons); put one on a spare control from the card or the Room screen.

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
| E, F | Notes 72, 88 | Vibes: start the set Ambient, start the set Show |
| G | Note 104 | Stop |
| H | Note 120 | Blackout on / off (press twice) |

### What changes when you update

A box that already had MIDI switched on behaves differently for these three controllers once this version is on it. Mappings you learned keep working and still win. What is new:

- **Every control you never mapped now does something** (the tables above). Before, most did nothing.
- **Learned opacity, volume, clip speed, shader speed and shader brightness on these three controllers now wait for pickup**: the fader does nothing until it reaches the value the box has.
- **The built-in map no longer applies to them.** On the Launchpad Mini notes 36 to 71 used to be the built-in pads 1 to 36; now note 36 is bank B pad 5, notes 38 and 39 are spare, and so on by the grid; note 72 was the built-in Stop and is now "Vibes: start the set Ambient" (button E); notes 73 to 76 (pause, blackout, fade out, reset) are not on its grid. On the nanoKONTROL2 CC 20 to 23 were the built-in opacity, size, position and speed and are now knobs 5 to 8 (shader controls 5 to 8). On the MIDI Mix CC 20 to 25 change the same way.
- To have the old behaviour back for one controller, switch its **Standard layout** off.

### The two-minute check, per controller

With the controller plugged in, open System > MIDI controller and look at its card.

1. The card says "recognised, standard layout on". If there is no card or it says "No built-in layout", note the name it shows: the card id or product name in the profile is wrong.
2. Move or press **each control once** and watch its box on the card light up. A box that does not light is a wrong number in the profile (or, on the nanoKONTROL2, the controller is not in CC mode). A different box lighting is a swapped number.
3. For the MIDI Mix: hold Solo and press each Mute; the Solo+Mute row should light.
4. Tell us what did not light. Anything can be put right for now by tapping the control and choosing its action, or with Learn.

### On the page

Each connected controller has a card: its name and state line, the switch "Standard layout", the drawn layout (a grid built from the profile's positions; on a phone a wide controller scrolls sideways inside its card), and under it the profile's note. A control shows what it does now; it lights while it is moved; a dashed edge means "changed by you"; a dotted edge means it waits for pickup. Tap a control to see what it sends and to choose another action (Save), or "Nothing" to switch it off; Save is offered only when the choice differs from what the control does now. "Back to the standard" undoes that for the control, and "Back to the standard for the whole controller" for all of them, after a question in place. Both remove only that controller's own mappings on the controls of its layout; a mapping made for any controller is shown on the control as such and is removed in the list of mappings. A recognised controller that has sent nothing yet says "Nothing received yet", with the CC mode hint for a nanoKONTROL2. A controller with no profile gets a card that says "No built-in layout for this one yet. Teach it below." The page is shown to full-access devices; a presenter can read the same through `GET /api/midi` but has no page for it yet.

### Writing a profile for another controller

Add one file, `pvj/controllers.d/<id>.json`; nothing else changes. All keys are required unless marked; unknown keys are refused, and a file that fails the check is left out with a line in the log (`journalctl -u pvj-web`). The test `tests/test_controllers.py` checks every file in the folder.

```json
{
  "id": "maker-model",
  "name": "Maker Model",
  "match": {"card_ids": ["Model"], "card_names": ["Maker Model"], "usb_ids": ["1234:abcd"]},
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

- `id` is the file's name: small letters, digits, dashes. `match.card_ids` (one to eight) and `match.card_names` (none to eight) are exact names, not patterns: `cat /proc/asound/cards` on the box shows both (` 2 [Mini ]: USB-Audio - Launchpad Mini`: the id in brackets, the name after the dash). The `_1` that ALSA adds for a second unit is allowed for by the box. `match.usb_ids` (optional, none to eight) are the USB ids from `cat /proc/asound/card2/usbid` (`1235:0036`, small letters); list every revision you know, because one that matches decides alone.
- `layout` is the grid of the drawing (up to 16 by 16); each control has its own `row` and `col` from 0, a `name` as printed on the hardware (up to 24 characters), and a `kind`: `fader`, `knob`, `button` or `pad`.
- `send`: `type` is `note` or `cc`, `number` 0 to 127, `channel` 1 to 16 or 0 for any (use 0 unless two controls differ only by channel).
- `action` is `null` for a spare control, or an action from the table below with its fields (`pad` needs `bank` 0 to 2 and `index` 0 to 11, `bank_pad` needs `index`). A fader or knob needs an action that follows it; a button or pad one that is pressed. A scene by id and `none` are not allowed in a profile.
- `guard` (optional, buttons and pads): the press is needed twice. Use it for blackout and anything room-wide. `unverified` (optional): the number is not confirmed by the maker's document; say so in `note` too.
- Up to 160 controls; no two in one place, no two sending the same message.

**A `lights` section** (optional) says which controls have a light and what the box may send to them. It is the only source of bytes that are ever written to the controller, and it is checked as strictly as the rest: an unknown key, a number out of range or a message that is not a note-off, note-on or control change leaves the whole file out.

```json
"lights": {
  "default": false,
  "unverified": true,
  "note": "Shown on the card: what to set on the controller first, what the colours mean, what is unverified.",
  "sources": ["The maker's document, its version, the section that gives the messages"],
  "channel": 1,
  "brightness": false,
  "off": 0,
  "setup": [],
  "clear": [],
  "styles": {
    "clip": {"off": 0, "on": 127, "active": 127, "busy": 127, "pulse": {"active": 0}},
    "blackout": {"off": 0, "on": 0, "active": 127, "busy": 127}
  },
  "controls": ["pad1", "b8"]
}
```

- `default`: are the lights on before anyone chose. Use `false` unless the maker's document gives the messages and the controller needs no setting changed first. `unverified`: the messages are not from a maker's document.
- `channel` (1 to 16): the MIDI channel the lights are sent on. `off` (0 to 127): the value that darkens a light.
- A light is addressed like its control: a control that sends a note gets a note-on with that note, one that sends a control change gets a control change with that number, and the value is the light. A controller whose lights are addressed differently cannot be described yet.
- `styles`: per thing a light can show, the value for each of the four states `off`, `on` (there is something here), `active` (it is the one on now) and `busy`. The names are fixed: `clip`, `preset`, `control`, `vibes`, `set`, `step`, `play`, `stop`, `blackout`, `fadeout`, `fadein`, `room`, `bank` (the table under "What a light shows" says which actions belong to which). `pulse` (optional) gives, for `on`, `active` or `busy`, a second value the light alternates with every 0.6 seconds; use it where a light has one colour. With `"brightness": true` each style holds three of these instead, under `low`, `medium` and `high`, and the card offers the choice.
- `controls`: the ids of the controls that have a light; only buttons and pads. What a light shows is not written here: it follows from what its control does (the table under "What a light shows"), so a spare control that is given an action later, by a new version of the profile or by the person on the card, lights for that action with no change to this section. An action with nothing to show, or with no style in this file, leaves its light dark.
- `setup` and `clear` (optional, up to eight messages each, every one three numbers: status, data, data): sent once when the controller is opened, and to darken everything. Only where the maker's document gives them. Without `clear` each light is sent `off`.
- Then add the controller to `tools/DEVICE-TESTING.md` and say plainly, in `note` and in this file, what was and was not tried on the hardware.

## Lights

The box lights the buttons and pads of a controller it knows, so the controller shows what the box is doing: which pads hold a clip and which one plays, whether Vibes runs, whether the screen is black.

**None of this has been seen on a real controller.** The messages are from the makers' documents where there are any (cited per controller below) and from secondary sources where there are none. Pipes stood in for the devices in every test. Please run the check list in `tools/DEVICE-TESTING.md` ("Controller lights") and say what you see.

### On the page

Each recognised controller's card has, under "Standard layout":

- a state line: "Lights on.", "Lights off.", "Lights are off while the standard layout is off.", "Lights need the box's installer to run once." (see "The service file" below) or "Lights could not be opened (another program may be using the controller). Trying again.";
- the switch **Lights** (a real switch; it applies on tap). On for a Launchpad Mini from the start; **off for a nanoKONTROL2 and a MIDI Mix until you switch it on**;
- **Brightness** (Low, Medium, High) where the controller has more than one level: the Launchpad. Low from the start, for a dark room;
- **Test lights**: every light comes on in turn in the order of the drawing, top left first, all stay on for a second, then they go back to what the box says. A light that stays dark, or one that comes on out of turn, is a wrong number in the profile.

In the drawing a small ring marks each control that has a light; the ring is filled while the box has that light on. The ring shows what the box sent, not what the hardware does.

### What a light shows

A light follows **what its control does now**: if you put another action on a control, its light shows that action's state. Four states exist: off, "there is something here", "it is the one on now", and "busy".

| The control does | Something here | On now | Busy |
| --- | --- | --- | --- |
| Play a pad; pad of the controllers' bank | the pad holds a clip | that clip is playing | |
| Shader preset 1 to 8 | the shader on screen has that preset | it is the preset in use | |
| Shader control 1 to 8 (as a press) | a shader is on screen (not checked per input) | | |
| Vibes on / off | Shaders and Vibes is switched on | Vibes is running | |
| Vibes: start the set Ambient, Show | that set exists | Vibes is running with it | |
| Previous / next shader; Vibes: next shader | a shader is on screen, or Vibes runs | | |
| Previous / next clip | a playlist of more than one clip plays | | |
| Pause / resume | | something is playing | it is paused |
| Stop | something is playing | nothing is playing | |
| Blackout on / off | (marks the button) | **the screen is black** | |
| Fade out | (marks the button) | the picture was faded out and is still down | |
| Fade in | (marks the button) | | a fade in is running |
| Room scene 1 to 8, a scene by id | the scene exists (and Room is on) | | it is being applied |
| Controllers' bank: before, next | | a place mark: the left button on bank A, both on B, the right one on C | |
| Nothing, a spare control, a level | | | |

"Which clip is playing" is decided by the file's name. Two pads with the same clip both show it.

### Novation Launchpad Mini: lights on, with colour

Source: Novation, *Launchpad S Programmer's Reference Manual* 1.02 (https://fael-downloads-prod.focusrite.com/customer/dev/s3fs-public/novation/downloads/10753/launchpad-s-prm.pdf), whose protocol the original Mini shares: "Set grid LEDs" (note-on `90h`, the pad's key, velocity), "Set Automap/Live control LEDs" (`B0h`, `68h` to `6Fh`, the same value) and "Reset Launchpad" (`B0h 00h 00h`: all LEDs off, power-on settings). The velocity is 16 x green + red + 12, green and red each 0 to 3; 12 is the flags for normal use. The manual also describes double buffering, flashing and a rapid update; none of them is used, only one message per pad. It gives 400 messages a second as the most the unit takes; the box sends 200 at most. **How this was read:** the PDF's text could not be extracted on the development machine (only its key tables came out, which match the layout); the formula and the three message forms are as quoted from that PDF by a web search of it and as the earlier profile work read them. Not seen on the hardware.

| Colour | Means | Value (low, medium, high brightness) |
| --- | --- | --- |
| dark | nothing here | 12 |
| amber | there is something here: a clip on the pad, a preset, a scene, a button that does something now | 29, 29, 46 |
| green | it is on now: the playing clip, the preset in use, Vibes running, a scene being applied, a fade in running | 28, 44, 60 |
| dim red | this button darkens the screen (blackout, fade out) or stops what plays | 13, 13, 14 |
| full red | **the screen is dark**: blackout is on, or the picture was faded out | 15 at every brightness |

Pause / resume is green while something plays and amber while it is paused. Every pad and round button has a light (80). When the controller is plugged in the box sends the reset first (so it is in the X-Y layout the profile expects, with everything dark) and then all 80 lights; when lights go off it sends the reset again.

### Korg nanoKONTROL2: off until you switch it on

**Set LED mode to External in Korg's editor first.** In the KORG KONTROL Editor: select the nanoKONTROL2, open Common, set **LED Mode** to **External**, and write the scene to the controller (Communication > Write Scene Data). In the factory setting, Internal, each light follows its own button and ignores the box, so switching Lights on here does nothing you can see. In External mode, as reported, a button no longer lights when you press it: only the box lights it.

Source: Korg's nanoKONTROL2 Parameter Guide, "LED Mode [Internal, External]" (p. 9, as the earlier profile work read it; https://cdn.korg.com/us/support/download/files/c8d0cd6808e12d3672845cadcdbbfe9b.pdf; its text could not be read again on the development machine for this work): in External mode an LED follows a message from the computer with the control change number of its button. **Unverified:** the guide names the mode and not the values, and it does not list which buttons have a light. From secondary reports (for instance nickhwang.com, "Korg nanoKontrol2 and Max", 2012: "A few buttons on the nanoKontrol2 do not have LEDs behind them"): value 127 lights, 0 darkens, on the controller's global MIDI channel (factory: 1), and the lit buttons are S, M and R of each strip, Cycle, and Rewind, Forward, Stop, Play and Rec (30). Track <, Track >, Marker Set, Marker < and Marker > are taken to have none, so the controllers' bank has no light on this controller.

| Light | Lit | Slowly pulsing | 
| --- | --- | --- |
| S 1 to 8 | pad 1 to 8 of the controllers' bank holds a clip | that clip is playing |
| M 1 to 8 | the shader on screen has that preset | it is the one in use |
| R 1 to 4 | the Room scene exists | it is being applied |
| R 6, R 7 | a fade in is running; the picture is faded out | |
| R 8 | **the screen is black** | |
| Cycle | Vibes is running | |
| Rec | Vibes is running with the set Show | |
| Play | something is playing | it is paused |
| Stop | nothing is playing | |
| Rewind, Forward | a playlist is playing | |

### Akai MIDI Mix: off, unverified

**No Akai document describes the lights** (the MIDImix User Guide 1.0 says nothing about messages the unit receives), so they are off until you switch them on. The source is secondary: Tero Heikkinen, "AKAI MIDImix & Processing Midibus" (oldmachinery.blogspot.com, 2018-04-15): "Sending note-ons to MUTE, REC ARM or Bank button values with velocity 127 will turn the associated lights on. Sending note-ons with velocity 0 will turn the lights off. Sending note-offs does nothing." Open controller scripts (mfeyx/akai-midimix-bitwig, tstriker/akai-midimix) do the same. The Solo button cannot be lit, and the Solo+Mute row shows only while Solo is held, so neither gets a light from the box.

| Light | Lit | Slowly pulsing |
| --- | --- | --- |
| Mute 1 to 8 | pad 1 to 8 of the controllers' bank holds a clip | that clip is playing |
| Rec Arm 1 to 8 | the shader on screen has that preset | it is the one in use |
| Bank Left, Bank Right | the bank the controllers are on: left on A, both on B, right on C | |

### How it works

- **Only a controller that matched a profile with a `lights` section is opened for writing**, on a second, write-only handle; a controller without a profile never is. The path must be `/dev/snd/midiC<n>D<n>`, not a link, and a character device with ALSA's major number (116).
- **Only fixed messages are written**: for each light a note-on or a control change on the section's channel with the control's own number, and as its value a number written out in the profile's styles; plus the profile's set-up and clear messages. No SysEx: a profile may list only note-off, note-on and control change, and the check refuses anything else. A request can say three things about lights (on or off, one of three brightness words, "test"); no byte of a request, a clip name or a shader name reaches a controller.
- **One writer per controller, on its own thread.** It keeps a table of the value each light should have and sends what differs from what the device took, at most 200 messages a second. A burst of changes costs one message per light, not one per change. A device that takes nothing is not waited for and nothing piles up. A write error ends that controller's writer only (it is tried again after ten seconds while the controller is still there); the other controllers and the controls go on.
- **Unplugged and plugged in again**, the controller gets its set-up message and the whole state.
- **Where the state comes from.** A few times a second (every 0.3 s, and at once after a press) one thread looks at what the box holds in memory (the pads, the mix, Vibes, the shader on screen, the Room's scenes) and asks the player one status question at most every 0.6 s for all lights together. It never takes the shader engine's lock and is never on the thread that reads the controller.
- **Lights go off** (each light's off value, or the profile's clear message) when you switch Lights off, switch the standard layout off, switch MIDI off, and when the panel shuts down. If the box loses power the lights stay as they were until the controller is unplugged.

### The service file

Writing to a controller needs `DeviceAllow=char-alsa rw` in `pvj-web.service`; it was `r` before (D53 says what that allows and why). The installer writes the service file, and an update from the panel or a USB stick runs the installer, so both bring it. A box whose program files were copied over without the installer keeps the old service file: there the open for writing is refused, the card says **"Lights need the box's installer to run once."**, the controls work as before, and the box does not try again until the controller is plugged in again or a MIDI setting is changed. Run `install/install.sh --offline` as root once.

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
| Vibes: start the set Ambient, start the set Show (`vibes_ambient`, `vibes_show`) | trigger | Starts Vibes with that set, by its name; these are the two sets a box starts with. If the set was renamed or removed nothing happens and the log says so |
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
- The web service reaches the device through systemd: it needs the `audio` group and access to ALSA devices, and the unit has both (`DeviceAllow=char-alsa rw`: read for the controls, write for the lights; D53).
- Writing: only to a controller that matched a profile with a `lights` section, only the fixed messages of that section, at most 200 a second per controller. See "Lights > How it works".

## Not built yet

- Lights that are addressed differently from their control, more than one light per control, the Launchpad's flashing and double buffering, and lights for the Solo row of a MIDI Mix.
- A light per shader input (row 8 of the Launchpad is lit while any shader is on screen; the box does not read the shader's input list for it).
- Motor-fader feedback (needs MIDI output too).
- Mapping to pads by name, banks that follow the controller's own bank buttons, and relative (endless) encoders.

## Verified, and not

Verified on a real Raspberry Pi 4 (2026-09-30): the module reads a controller through the systemd sandbox and handled 112 messages in a few seconds from a Launchpad Mini. Three controllers (Korg nanoKONTROL2, Akai MIDI Mix, Novation Launchpad Mini) enumerate. **Learn, the multi-controller hub and the new map have only run against pipes standing in for controllers and the browser test, not yet against the real hardware.** The Vibes and shader actions have run only in unit tests with a fake player and a fake clock: no real controller has sent them.

**Controller profiles (2026-10-04): not tried on any real controller.** The three layouts are from the documents named above and one recording of the Launchpad Mini; the nanoKONTROL2's and the MIDI Mix's numbers are not confirmed by a manufacturer's document at all. Matching uses the card ids, product names and USB ids reported from the owner's Pi and is tested against a copy of that card list, but the code has not run on the Pi. Pickup, the double press, hot-plug and the drawn layout ran against pipes, a fake clock and the browser test's fake controller. Please run the two-minute check.

**Controller lights (2026-10-04): not tried on any real controller, and not on the Pi.** The messages are from Novation's Programmer's Reference (Launchpad Mini), from Korg's Parameter Guide plus secondary reports (nanoKONTROL2, unverified, and only with LED Mode set to External) and from a secondary source alone (MIDI Mix, unverified). The writer, the mapping from the box's state to each light, unplug and replug, the switch, the brightness, Test lights and the refusal under an older service file ran against pipes and the browser test's fake controller. Whether `DeviceAllow=char-alsa rw` lets the panel open a controller for writing on the Pi's systemd has not been run there. The check list is in `tools/DEVICE-TESTING.md`.
