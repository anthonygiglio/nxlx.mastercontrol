<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# MIDI controllers (beta)

Play pads, fade and mix from USB MIDI controllers: pad grids, fader boxes, keyboards. Switch it on under System > MIDI controller (full-access devices only) with the switch at the top of the page: it is the only switch, and the box reads controllers as soon as it says On. Off until you switch it on; switching off switches the module off and keeps your mappings. In the API these are still two things: the `control-midi` module and `enabled` in `POST /api/midi`.

**Every controller that is plugged in is read at once**, and a controller unplugged and replugged is picked up again within a couple of seconds. The box lights the buttons and pads of a controller it knows (see "Lights"); nothing else is written to a controller, and nothing at all to one it does not know (no motor faders).

**A controller the box knows works as soon as it is plugged in**: a Korg nanoKONTROL2, an Akai MIDI Mix and a Novation Launchpad Mini each have a ready-made layout (a profile). Nothing has to be taught. Any other controller is taught with Learn, as before.

## Controller profiles

A profile is one file in `pvj/controllers.d/`: which controller it is for, a drawing of it, what each control sends and what each does. When a matching controller appears (the box looks every 2 seconds) its layout applies by itself and the MIDI page shows a card for it: "Korg nanoKONTROL2: recognised, standard layout on."

**The numbers each control sends come from documents and one recording (see each table), not from pressing every control.** The owner used all three on 2026-10-10 and found the first layouts "sort of, odd, or not helpful, or incomplete. especially, the zoom and x/y position", with things "in places that make no sense". The three layouts below were drawn afresh after that (D75): each controller in zones that belong together, with the reasons written beside it. **The new layouts have not been on a controller either.** The check list below takes two minutes per controller.

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
  | Zoom (size), position X, position Y | yes (since D75) | a knob that is not where the picture is would throw the picture across the screen at the first touch |
  | Volume, clip speed | yes | a jump is heard or seen. The box remembers what was last set through it (the panel, OSC, a controller); before anything was set it takes the player's own start values, 100 percent and 1x |
  | Shader speed, shader brightness | yes | a jump to frozen or to black. For a shader of the category Performance the box keeps the speed at 1 or below unless "faster" is switched on; the fader then does nothing above its middle |
  | Shader control 1 to 8, shader colour turn | no | a jump is part of playing a shader, and a knob that waits feels broken |
  | Effect amount | yes | a fader left at the bottom would take the effect out of the picture at the first touch, one left at the top would put it in full |
  | Effect control 1 to 8 | no | as for a shader control |
  | Vibes time | no | it picks one of fifteen steps; nothing is seen to jump |

  Pickup catches where the control really gives the box's value: the reverse of the level's own curve (see "How a level follows a knob"), not a straight line. **Two controllers on one level share it this way**: each takes the level over only where it meets it, and the one that was left behind waits again (tested with a nanoKONTROL2's and a MIDI Mix's zoom knob).

  Mappings you make on a recognised controller pick up the same way. Plain Learn mappings on a controller with no profile behave as before (no pickup). A mapping can say otherwise for itself: `"takeover": "pickup"` or `"jump"` (see "How a level follows a knob").
- **The same press twice.** Blackout and the Room scenes need two presses of the same button within a second (and at least a quarter of a second apart); one press does nothing. The time is counted from the LAST press: a second press that comes too early or too late does nothing itself and is the first press of a new pair, so a third press a quarter of a second to a second after it acts. They are marked "2x" on the card. A Room scene can switch projectors off for the whole room, so it is treated like blackout. If you put blackout or a Room scene on a control yourself (on its card), it is guarded too: the chooser shows a switch **Press twice**, on unless you switch it off. Saving a control without changing it stores nothing, so the guard cannot be lost by pressing Save. A mapping made with plain Learn is not guarded, as before.
- **The controllers' bank.** A nanoKONTROL2 and a MIDI Mix have one row of eight pad buttons, so the box keeps a bank for controllers (A at start, not saved): the row plays pads 1 to 8 of that bank and two buttons step it. Pads 9 to 12 are not on these two layouts. The Launchpad Mini has room for all three banks and needs no bank button.
- **Zones.** Every control of a layout belongs to one part of the controller: Pads, The clip, The screen (fade, freeze, stop, black), The picture (zoom, place, turn), Sound, Shaders and Vibes, Effects, Room, Access. The card tints the controls of a zone alike (a bar of colour along the foot of each) and names the zones once under the drawing.
- **Same path as everything else.** A profile only chooses which API call a control makes; the call goes through the API as a presenter, so the module switches, every check and the 50 commands a second apply, and no control waits for the shader engine (tested with the engine's lock held, for every control of every layout).
- **Lights.** The box shows its state on the controller's own lights: see "Lights" below. Switching a standard layout off also switches that controller's lights off.

### Korg nanoKONTROL2 (`korg-nanokontrol2`)

**Unverified.** Korg's nanoKONTROL2 Parameter Guide describes every parameter of CC mode but prints no table of the factory numbers. The numbers below are the factory assignments as widely reported and as used by the Mixxx project's mapping for this controller. The controller must be in **CC mode**: hold SET MARKER and CYCLE while plugging it in. It starts in the mode it was last used in, so one that came from a DAW set-up stays in that DAW's mode until you do this once. In a DAW mode its faders send pitch bend, which the box does not read, and the layout looks dead (the card then says "Nothing received yet"). In Korg's editor a button can be set to Toggle instead of Momentary; a toggling button then acts on every other press only. The layout expects the factory setting, Momentary. Factory reset (Parameter Guide p. 13): hold PREV TRACK, NEXT TRACK and CYCLE while plugging it in.

**How to read it.** Eight strips, left to right. The owner, 2026-10-10: "all eight knobs for shaders, zoom x y on faders, if they make sense. otherwise, use a button to switch to the geometry controls and flash the button when in that state". The faders do not make sense for zoom and position: all are in use for levels that are ridden (opacity, volume, the speeds, the colour, the effect), a fader has no middle a hand can feel, and one left low would be a picture left small or off centre. So it is the button: **all eight knobs are the shader's, and M 6, "Geometry", turns knobs 1 to 3 into zoom, position X and position Y for as long as its light flashes.** The three button rows run the whole width: **S plays pads**, **M picks the shader's presets** and, at its right end, holds the three buttons for the picture (Geometry, Rotate, Mapping mode), **R is the row to be careful with** (the room on the left, the screen on the right, Blackout at the far end).

In normal use:

```
            knob     1 Sh c1   2 Sh c2   3 Sh c3   4 Sh c4   5 Sh c5   6 Sh c6   7 Sh c7   8 Sh c8
Track < >   S        pad 1     pad 2     pad 3     pad 4     pad 5     pad 6     pad 7     pad 8
Cycle  Marker        M        preset 1  preset 2  preset 3  preset 4  preset 5  GEOMETRY  Rotate    MAPPING 2x
<< >> Stop Play Rec  R        scene 1   scene 2   scene 3   scene 4   Effect    (spare)   FADE      BLACKOUT
            fader    Opacity   Volume    Clip spd  Sh speed  Sh hue    Sh bright Fx amount (spare)
```

With Geometry on (M 6 flashing), only the knobs change:

```
            knob     1 ZOOM    2 POS X   3 POS Y   4 .       5 .       6 .       7 .       8 .
```

- **Into it:** one plain press of M 6 (it changes nothing on the screen by itself). Each of the three knobs waits until it meets the picture's own value, so nothing jumps; the middle of each is exactly 100 percent and exactly centred. Knobs 4 to 8 rest: they do not go on moving the shader unseen.
- **Out of it:** M 6 again, or by itself two minutes after the last touch of one of the three knobs, so a forgotten Geometry does not leave the shader knobs dead; the light stops flashing. **Each shader knob then waits until it is back where it stood when it last set the shader**, so nothing jumps on the way back either. (It waits for its own old place, not for the shader's value: if the same control was changed on the panel meanwhile, the knob takes it over where it stands, as a shader knob always did.)
- Faders, pads, presets, scenes, the transport and the screen's buttons are the same in both.
- Why M 6: it has a light, it is in the row of the picture's buttons, and the button under it (R 6) is spare; Fade and Blackout are two and three places away, in another row.
- Not on it: the two mirrors and Reset mix. No free button is left that is not beside something tapped often; put one on a control from the card.

| Control | Sends | Does | Why there |
| --- | --- | --- | --- |
| Knob 1 to 8 | CC 16 to 23 | Shader control 1 to 8 of the shader on screen. **With Geometry on**: knob 1 zoom, knob 2 position X, knob 3 position Y (pickup), knobs 4 to 8 nothing. In mapping mode knobs 2 and 3 nudge the chosen corner | the owner's word |
| Fader 1, 2, 3 | CC 0, 1, 2 | Opacity, volume, clip speed (pickup) | unchanged: the hand knows them |
| Fader 4, 5, 6 | CC 3, 4, 5 | Shader speed (pickup), colour turn, brightness (pickup) | unchanged |
| Fader 7 | CC 6 | Effect amount (pickup) | unchanged |
| Fader 8 | CC 7 | Spare | |
| S 1 to 8 | CC 32 to 39 | Pad 1 to 8 of the controllers' bank | the top row of buttons is the one pressed most |
| M 1 to 5 | CC 48 to 52 | Shader preset 1 to 5 | |
| M 6 | CC 53 | **Geometry** on / off (one press; flashes while on) | above |
| M 7 | CC 54 | Rotate a quarter turn | over Fade, not beside it: the buttons a hand reaches for in the dark have nothing drastic at their side |
| M 8 | CC 55 | **Mapping mode** on / off (press twice; flashes while on; see "Mapping mode") | over Blackout; it does nothing until the owner's switch is on |
| R 1 to 4 | CC 64 to 67 | Room scene 1 to 4 (press twice) | |
| R 5 | CC 68 | Effect on / off | under the effect's strips |
| R 6 | CC 69 | Spare | left empty on purpose, beside Fade |
| R 7 | CC 70 | **Fade out, then in** (one button; its light flashes while the picture is down) | beside Blackout: the two that darken the screen |
| R 8 | CC 71 | Blackout on / off (press twice; flashes while on) | the last button of the row |
| Track <, Track > | CC 58, 59 | Previous shader, next shader. In mapping mode: the surface before, the next surface | |
| Cycle | CC 46 | Vibes on / off. In mapping mode: the step (1, 10, 50 pixels) | |
| Marker Set | CC 60 | Vibes: start the set Ambient. In mapping mode: undo the last nudge | |
| Marker <, Marker > | CC 61, 62 | Controllers' bank: the one before, the next. In mapping mode: the corner before, the next corner | |
| Rewind, Forward | CC 43, 44 | Previous clip, next clip (of a playlist) | as printed |
| Stop, Play | CC 42, 41 | Stop, Freeze / resume | as printed |
| Rec | CC 45 | Vibes: start the set Show | |

Vibes time and shader presets 6 to 8 are not on this layout any more.

### Akai MIDI Mix (`akai-midimix`)

**Unverified.** No Akai document with the factory numbers was found (the support article on the editor gives none; the User Guide could not be read when this was written). The numbers are those of a public Ableton Live script written for the factory preset (billmoser/MIDIMixD). Holding **Solo** makes the Mute buttons send a second set of notes; they are drawn as a row of their own, "Solo+Mute".

**How to read it.** Here the rows are the zones, because the controller has three rows of knobs: the **top row is the shader**, the **middle row the picture**, the **bottom row the effect**. The faders are the same as on the nanoKONTROL2. **Mute plays pads**, **Rec Arm picks presets**, and the row you reach only by holding **Solo** holds what must not be hit by accident: the screen's four buttons at its right end, in the panel's own order.

```
knob A   Sh c1    Sh c2    Sh c3    Sh c4    Sh c5    Sh c6    Sh c7    Sh c8
knob B   Zoom     Pos X    Pos Y    Vibes t. (spare)  (spare)  (spare)  (spare)
knob C   Fx c1    Fx c2    Fx c3    Fx c4    Fx c5    Fx c6    Fx c7    Fx c8
Mute     pad 1    pad 2    pad 3    pad 4    pad 5    pad 6    pad 7    pad 8      Bank <
Solo+M   Vibes    Shader < Shader > Effect   FADE     Freeze   Stop     BLACKOUT   Bank >
Rec Arm  preset 1 preset 2 preset 3 preset 4 preset 5 preset 6 preset 7 Rotate     Solo (shift)
fader    Opacity  Volume   Clip spd Sh speed Sh hue   Sh brt   Fx amt   (spare)    Master (spare)
```

| Control | Sends | Does |
| --- | --- | --- |
| Knob A1 to A8 (top row) | CC 16, 20, 24, 28, 46, 50, 54, 58 | Shader control 1 to 8 |
| Knob B1, B2, B3 (middle row) | CC 17, 21, 25 | **Zoom, position X, position Y** (pickup; the middle is exactly 100 percent and exactly centred) |
| Knob B4 | CC 29 | Vibes: time each shader stays |
| Knob B5 to B8 | CC 47, 51, 55, 59 | Spare |
| Knob C1 to C8 (bottom row) | CC 18, 22, 26, 30, 48, 52, 56, 60 | Effect control 1 to 8 of the effect that is on |
| Fader 1 to 6 | CC 19, 23, 27, 31, 49, 53 | As on the nanoKONTROL2: opacity, volume, clip speed, shader speed, colour turn, shader brightness |
| Fader 7 | CC 57 | Effect amount (pickup) |
| Fader 8, Master | CC 61, 62 | Spare |
| Mute 1 to 8 | Note 1, 4, 7, 10, 13, 16, 19, 22 | Pad 1 to 8 of the controllers' bank |
| Rec Arm 1 to 7 | Note 3, 6, 9, 12, 15, 18, 21 | Shader preset 1 to 7 |
| Rec Arm 8 | Note 24 | Rotate a quarter turn (not on the Solo row, between two switches, where the first layout of D75 had it) |
| Solo+Mute 1 to 4 | Note 2, 5, 8, 11 | Vibes on / off, previous shader, next shader, effect on / off |
| Solo+Mute 5 to 8 | Note 14, 17, 20, 23 | **Fade out, then in** (one button), Freeze / resume, Stop, Blackout (press twice): the panel's own order |
| Bank Left, Bank Right | Note 25, 26 | Controllers' bank: the one before, the next |
| Solo | Note 27 | Spare (it is the shift for the row above) |

Room scenes, the two Vibes sets and the effect's previous and next are not on this layout (it has no free buttons); put one on a control from the card or the Room screen. **The fade button has no light here**: the box cannot light the Solo+Mute row, so on a MIDI Mix the flashing is on the panel only.

### Novation Launchpad Mini (`novation-launchpad-mini`)

The **original** Launchpad Mini, not the MK3 (which sends other numbers and has another card id). From Novation's Launchpad S Programmer's Reference Manual 1.02, whose protocol the original Mini shares: in the power-on X-Y layout a pad sends note 16 x row + column (from 0, top left), the round buttons on the right are column 8 (notes 8, 24 ... 120) and the round buttons along the top send CC 104 to 111. The recording of the owner's unit (D21: notes 20 to 103 and CC 104 to 111) fits: every number is one of that layout, and note 20 does not exist in the other (drum rack) layout. The recording did not cover every pad.

**Drawn afresh** after the owner's "the launchpad button layouts dont make sense to me" (2026-10-10), and then in rows at his word ("rows"). The first layout laid each bank as two rows of six and filled what was left with single pads of every kind. A second one drew each bank as a block of three rows of four, as the panel draws a bank on a laptop; it was built and reviewed, and rows won, because a performer reads an 8 by 8 grid in rows, not in blocks. This one has five rules:

1. **The grid is read in rows, and the banks follow one another from the top.** Each bank is a pair of rows, six pads and six, pad 1 at the left of the first: **bank A on rows 1 and 2, bank B on rows 3 and 4, bank C on rows 5 and 6**. Six and six, not eight and four: every bank has the same shape, pad 7 sits under pad 1, and the two columns left over run straight down beside all three banks as one strip.
2. **A lit pad means there is something on it; the one that plays is bright green; an empty pad is dark.** Bank A is amber, bank B yellow-green, bank C orange.
3. **One zone per thing, each a row or a strip, never scattered.** The strip beside the banks, two pads wide, is the presets of the shader on screen, 1 to 8 from the top. The fourth pair of rows is for what is not a pad: row 7 has shaders and Vibes on its left half and the effect on its right; row 8 has the clip in the panel's order (Prev, back 10 s, forward 10 s, Next) on its left and the shader's first four controls, as presses, on its right.
4. **The round buttons are the ones to be careful with**, and you can feel that they are round. On the right, top half: the four **Room scenes** (press twice). Bottom half: **the screen**, in the panel's order from the top: **Fade, Freeze, Stop, Blackout**, Blackout in the corner. Fade and Blackout flash red for as long as they are on.
5. **The top row holds what is used least**: which set Vibes plays, the presenter code (a hold of three seconds, on the third button: away from every pad of a bank), the quarter turn (far from the right-hand column), the overlay, sound off, and mapping mode on the last (press twice).

```
top (round)   Vibes:Amb  Vibes:Show  code    .       Rotate   Overlay  Mute    Mapping 2x
row 1         A1   A2   A3   A4   A5   A6   |  preset 1  preset 2     (A) Room scene 1   2x
row 2         A7   A8   A9   A10  A11  A12  |  preset 3  preset 4     (B) Room scene 2   2x
row 3         B1   B2   B3   B4   B5   B6   |  preset 5  preset 6     (C) Room scene 3   2x
row 4         B7   B8   B9   B10  B11  B12  |  preset 7  preset 8     (D) Room scene 4   2x
row 5         C1   C2   C3   C4   C5   C6   |  .         .            (E) FADE out / in
row 6         C7   C8   C9   C10  C11  C12  |  .         .            (F) Freeze
row 7         Vibes Shader< Shader> .  |  Effect  Effect<  Effect>  .  (G) Stop
row 8         Prev  -10 s   +10 s  Next |  control 1  2  3  4          (H) BLACKOUT      2x
```

| Control | Sends | Does |
| --- | --- | --- |
| Grid rows 1 and 2, columns 1 to 6 | Notes 0 to 5, 16 to 21 | Bank A, pads 1 to 6 and 7 to 12 |
| Grid rows 3 and 4, columns 1 to 6 | Notes 32 to 37, 48 to 53 | Bank B, pads 1 to 12 |
| Grid rows 5 and 6, columns 1 to 6 | Notes 64 to 69, 80 to 85 | Bank C, pads 1 to 12 |
| Grid rows 1 to 4, columns 7 and 8 | Notes 6, 7, 22, 23, 38, 39, 54, 55 | Shader preset 1 to 8 |
| Grid rows 5 and 6, columns 7 and 8 | Notes 70, 71, 86, 87 | Spare |
| Grid row 7, columns 1 to 3 | Notes 96, 97, 98 | Vibes on / off, previous shader, next shader (column 4, note 99, is spare) |
| Grid row 7, columns 5 to 7 | Notes 100, 101, 102 | Effect on / off, previous effect, next effect (column 8, note 103, is spare) |
| Grid row 8, columns 1 to 4 | Notes 112 to 115 | Previous clip, back 10 seconds, forward 10 seconds, next clip |
| Grid row 8, columns 5 to 8 | Notes 116 to 119 | Shader control 1 to 4, as a press (a switch toggles, a choice steps, an event fires, a number goes back to its own value) |
| A to D (right) | Notes 8, 24, 40, 56 | Room scene 1 to 4 (press twice) |
| E, F, G, H (right) | Notes 72, 88, 104, 120 | **Fade out, then in**; Freeze / resume; Stop; Blackout on / off (press twice) |
| Top 1, 2 | CC 104, 105 | Vibes: start the set Ambient, start the set Show |
| Top 5, 6, 7 | CC 108, 109, 110 | Rotate a quarter turn, overlay picture on / off, sound off / on |
| Top 3 | CC 106 | Show a one-time presenter code (hold 3 seconds and let go; does nothing until switched on, see "A pairing code on the display"). It was on the pad of row 6, column 8, right beside bank C's pad 12, with a spare pad on its other side: a hold meant for a clip could land on it. Up here nothing next to it is a pad |
| Top 4 | CC 107 | Spare |
| Top 8 | CC 111 | Mapping mode on / off (press twice; see "Layers") |

The effect's controls as presses, which filled eight pads of the first layout, and the shader's controls 5 to 8 as presses are not on this one. **The banks are where the very first layout had them** (rows of six, A, B, C from the top): a hand that knew those pads still finds them.

### Layers: Geometry and mapping mode

A **layer** is a state of a controller in which a few of its controls do something else. Its button's light flashes for as long as it is on, the card says so and shows each control's other self, and only one layer is on at a time. Everything that has no other self in the layer works as always. **Whenever the layer that is on for a controller changes, by whatever road, nothing jumps**: its levels start their pickup afresh, a knob that sets a shader's or an effect's control waits until it is back where it stood, and a knob that nudges in mapping mode forgets where it stood (its first touch in the mode moves nothing, each time the mode comes on, wherever the knob was turned as the shader's in between). That holds for Geometry's own button and its two minutes, and as much for mapping mode coming or going, which a controller is never told about (another controller's button, OSC, the owner's switch, the three minutes): the box notes the layer at each controller's last message and sees the change at its next. Until 2026-10-10 only Geometry's button did this, and a shader knob used as a nudge in mapping mode set the shader to wherever it had been turned as soon as the mode ended. A controller unplugged in a layer comes back plain (Geometry is forgotten with it; mapping mode is the box's and goes on); two controllers each have their own Geometry; your own mapping for a control wins inside a layer as outside. A profile gives a control its other self with `"layers"` (see "Writing a profile"); your own mapping for a control wins in every layer.

**Geometry** (the nanoKONTROL2 only) belongs to that one controller: see its section above.

**Mapping mode** belongs to the box. The owner, 2026-10-10: "the controller can handle corner or point nudges once in that mode."

- **It needs the owner's switch**: on the Mapping card, "Controllers may adjust the mapping", set by a full-access device only, **off unless switched on**, never taken from an imported settings file and off after a factory reset. Every change of the mapping otherwise needs full access; with the switch on, whoever is at a MIDI controller (or sends OSC) can move the corners of the room's mapping. Decide with the room in mind.
- **Nothing is nudged outside the mode.** The mode is entered on purpose: its button (the action "Mapping mode on / off"), pressed twice like Blackout, because it changes what other controls do. The box's display then shows every surface's outline with the chosen one yellow and the chosen corner as a pink dot (the panel's own "Edit on the display"), the Mapping card says a controller is adjusting the mapping (and that the card's own "Edit on the display" and selection are borrowed meanwhile; they are put back when the mode ends), and the button flashes (green on the Launchpad).
- **In the mode**: the surface before and the next, the corner before and the next (round and round), a nudge left, right, up and down by the step, the step itself (1, 10 or 50 pixels; it starts at 1), and one step of undo: the last run of nudges, which is every nudge of that corner with less than a second between one and the next (a whole turn of the knob, not its last click). A knob with the action nudges by being turned, a step for each step of the knob; its first touch moves nothing. One message moves a corner 200 pixels at most.
- **Every nudge is the panel's own move**: the whole mapping is checked again, a surface cannot be folded or flattened, and what is refused changes nothing. Each nudge counts at once; the settings are written at most twice a second while they come and once more when they stop or the mode ends (`pvj/MAPPER.md` says what a power cut in that half second costs).
- **It ends** by the same button (twice), when the switch goes off, and by itself three minutes after the last thing done in it. Then the outlines leave the display and the show picture is prepared again. **Blackout, Stop, a fade and playing a pad do not end it**: lining up is done with something playing and with the screen dark in between, and those buttons keep working in the mode.
- A larger step **while a button is held** is not built: the step button cycles 1, 10, 50 instead. Choosing between screen corners and picture corners, adding, removing and hiding surfaces, and saved mappings stay on the panel.

| Controller | Into and out of the mode | In the mode |
| --- | --- | --- |
| nanoKONTROL2 | M 8, twice | Track < and > choose the surface, Marker < and > the corner, **knob 2 nudges left and right, knob 3 up and down**, Cycle is the step, Marker Set undoes |
| Launchpad Mini | the last round button along the top (Top 8), twice | row 7: surface before, next surface, corner before, next corner, then **up** on its sixth pad and undo on its eighth; row 8: the step on its fourth pad, then **left, down, right**: the arrows of a keyboard, under the sixth column |
| MIDI Mix | no button for it in the standard layout (it has none free with a light); put "Mapping mode on / off" and the nudges on controls from the card | |

Over OSC: the same, behind the same switch (`pvj/OSC.md`, `/pvj/mapping/...`), and behind the three locks on who may send when they are on.

**None of this has been on a controller, a projector or the Pi.** Unit tests with a fake player and pipes only.

### What changes when you update

**From the version with the first layouts to this one (D75).** A box that has one of the three controllers plugged in behaves differently after this update. Mappings you made keep working and still win over the standard for their control. What changes:

- **Most controls of the three layouts do something else.** The tables above are the whole truth; the ones a hand had learned:
  - *nanoKONTROL2*: the eight knobs are the shader's controls 1 to 8, as they were; new is that M 6 turns knobs 1 to 3 into zoom and the two positions while it flashes. R 6 was Fade in and is spare; R 7 was Fade out and is the one fade button; M 6 was preset 6 and is Geometry, M 7 was preset 7 and is the quarter turn, M 8 was preset 8 and is mapping mode (nothing until the owner's switch is on). Presets 6 to 8 are not on it any more.
  - *MIDI Mix*: knob B2 was position X and still is; B1 (size) is the same under the name Zoom; B3 was Vibes time and is position Y; B4 was spare and is Vibes time. The Solo+Mute row was Stop, Pause, Vibes, previous shader, next shader, Fade in, Fade out, Blackout and is Vibes, previous shader, next shader, effect on / off, fade, Freeze, Stop, Blackout. Rec Arm 8 was preset 8 and is the quarter turn.
  - *Launchpad Mini*: the pads of the three banks are where they were (rows of six, A, B, C from the top). Everything else moved: the two columns beside the banks were the effect's buttons and controls and are the shader's presets 1 to 8; row 7 was the presets and is Vibes and the shader's steps (left) and the effect (right); row 8 was the shader's controls 1 to 8 and is the clip's transport (left) and the shader's controls 1 to 4 (right). The top row was previous and next shader, Vibes, previous and next clip, Pause, Fade in, Fade out and is the two Vibes sets, the quarter turn, the overlay and Mute. On the right, E and F were the two Vibes sets and are Fade and Freeze; Stop and Blackout (G and H) and the Room scenes (A to D) are where they were. The presenter code moved from the pad with note 7 to the pad with note 87, and after the review from there (it sat right beside bank C's pad 12) to the round button Top 3 (CC 106).
- **A zoom or position knob waits for pickup** on these three controllers, also one you mapped yourself there: it does nothing until it reaches the value the box has.
- **Zoom, position, clip speed, shader speed, colour turn and shader brightness have their normal value at the middle of the control**, over nine steps (60 to 68 of 0 to 127). For every mapping of these actions, yours and the built-in map's too:
  - zoom went from 1 to 200 percent in a straight line (100 percent near 63) and goes from 25 to 200 with 100 at the middle. A mapping that needs the old bottom can say `"min": 1`.
  - clip speed had 1x at about 54 of 127 and has it at the middle; shader speed had its own pace a quarter of the way up and has it at the middle.
  - position, colour turn and brightness had their middle between two steps and now have it on nine.
- **Fade out and Fade in on the panel are one button.** The two old calls and the two old actions still work.
- **The Launchpad Mini is put in its flash mode** when it is plugged in (one more message after the reset), and its pads show three bank colours.
- To have the old behaviour back for one controller there is no switch: the old layouts are gone. Switch **Standard layout** off to use only your own mappings and the built-in map.

**From a version before controller profiles.** Every control you never mapped now does something (the tables above); before, most did nothing. **The built-in map no longer applies to these three controllers**: on the Launchpad Mini notes 36 to 71 used to be the built-in pads 1 to 36 and note 72 the built-in Stop; on the nanoKONTROL2 and the MIDI Mix CC 20 to 25 were the built-in levels. Now each number is what its table says.

### The two-minute check, per controller

With the controller plugged in, open System > MIDI controller and look at its card.

1. The card says "recognised, standard layout on". If there is no card or it says "No built-in layout", note the name it shows: the card id or product name in the profile is wrong.
2. Move or press **each control once** and watch its box on the card light up. A box that does not light is a wrong number in the profile (or, on the nanoKONTROL2, the controller is not in CC mode). A different box lighting is a swapped number.
3. For the MIDI Mix: hold Solo and press each Mute; the Solo+Mute row should light.
4. Tell us what did not light. Anything can be put right for now by tapping the control and choosing its action, or with Learn.

### On the page

Each connected controller has a card: its name and state line, the switch "Standard layout", the drawn layout (a grid built from the profile's positions; on a phone a wide controller scrolls sideways inside its card), and under it the profile's note. A control shows what it does now and carries its zone's bar of colour, and the zones are named once under the drawing; it lights while it is moved; a dashed edge means "changed by you"; a dotted edge means it waits for pickup. Tap a control to see what it sends and to choose another action (Save), or "Nothing" to switch it off; Save is offered only when the choice differs from what the control does now. "Back to the standard" undoes that for the control, and "Back to the standard for the whole controller" for all of them, after a question in place. Both remove only that controller's own mappings on the controls of its layout; a mapping made for any controller is shown on the control as such and is removed in the list of mappings. A recognised controller that has sent nothing yet says "Nothing received yet", with the CC mode hint for a nanoKONTROL2. A controller with no profile gets a card that says "No built-in layout for this one yet. Teach it below." The page is shown to full-access devices; a presenter can read the same through `GET /api/midi` but has no page for it yet.

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
- `zone` (optional): which part of the controller the control belongs to, for the drawing: `pads`, `clips`, `screen`, `picture`, `sound`, `shaders`, `effects`, `room` or `access`.
- `layers` (optional): the control's other self while a layer is on, `{"geometry": {"action": "size"}}` or `{"mapping": {"action": "map_left"}}`; `null` in `geometry` means "nothing in that layer". A fader or knob needs an action that follows it, a button or pad one that is pressed; in `mapping` only the `map_...` actions. The layer's own button is a control with the action `layer_geometry` or `mapping_mode`.
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
- `styles`: per thing a light can show, the value for each of the four states `off`, `on` (there is something here), `active` (it is the one on now) and `busy`. The names are fixed: `clip`, `clip_b` and `clip_c` (a pad of bank B and C, where a controller has colours; without them every pad is `clip`), `preset`, `control`, `vibes`, `set`, `step`, `play`, `stop`, `blackout`, `fadeout`, `fadein`, `fade` (the one fade button; without it, as `fadeout`), `room`, `bank`, `effect` (the table under "What a light shows" says which actions belong to which). `pulse` (optional) gives, for `on`, `active` or `busy`, a second value the light alternates with every 0.6 seconds; use it where a light has one colour. With `"brightness": true` each style holds three of these instead, under `low`, `medium` and `high`, and the card offers the choice.
- `controls`: the ids of the controls that have a light; only buttons and pads. What a light shows is not written here: it follows from what its control does (the table under "What a light shows"), so a spare control that is given an action later, by a new version of the profile or by the person on the card, lights for that action with no change to this section. An action with nothing to show, or with no style in this file, leaves its light dark.
- `flash` (optional): `"device"` where the maker's document gives values at which the controller flashes a light by itself; a style may then hold `flash`, that value. Without it (`"timer"`) the box switches a flashing light on and off itself.
- `setup` and `clear` (optional, up to eight messages each, every one three numbers: status, data, data; a note-off, note-on or control change on the section's `channel`, and not controllers 120 to 127): sent once when the controller is plugged in, and to darken everything. Only where the maker's document gives them. Without `clear` each light is sent `off`.
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

A light follows **what its control does now**: if you put another action on a control, its light shows that action's state. Five states exist: off, "there is something here", "it is the one on now", "busy", and **flashing** (D75), for the two buttons that darken the screen: the fade button flashes while the picture goes down and while it is black from a fade, and Blackout flashes for as long as it is on.

**How a light flashes.** Where the maker's document gives a way for the controller to flash a light by itself, the box uses it: one message, and nothing more is sent while it flashes (the Launchpad, below). Everywhere else the light's own writer switches it on and off, a quarter of a second each (two flashes a second), on its own thread and its own clock, through the same limit of 200 messages a second as every other light: a flashing light costs four messages a second, the thread that reads the controller never waits for it, and it ends with the state. **If the panel's service dies without sending its clear message** (a crash, the power of the box and not of the controller), a light stays as it was at that moment: one the box was flashing itself stays lit or dark, whichever half it was in, and a Launchpad goes on flashing by itself until it is unplugged or the service is back. A profile says which: `"flash": "device"` in its lights section and a `flash` value in a style, or nothing (the writer does it).

| The control does | Something here | On now | Busy |
| --- | --- | --- | --- |
| Play a pad; pad of the controllers' bank | the pad holds a clip | that clip is playing | |
| Shader preset 1 to 8 | the shader on screen has that preset | it is the preset in use | |
| Shader control 1 to 8 (as a press) | a shader is on screen (not checked per input) | | |
| Vibes on / off | Shaders and Vibes is switched on | Vibes is running | |
| Vibes: start the set Ambient, Show | that set exists | Vibes is running with it | |
| Previous / next shader; Vibes: next shader | a shader is on screen, or Vibes runs | | |
| Previous / next clip | a playlist of more than one clip plays | | |
| Effect on / off (style `effect`) | the player is running (the light does not ask whether something with a picture plays: a controller's thread asks the player nothing; a press with nothing playing answers on the Effect card, "did not go on") | an effect is on | |
| The effect before, the next effect (style `step`) | an effect could go on, or one is on | | |
| Effect control 1 to 8, as a press (style `control`) | an effect is on (not checked per input) | | |
| Pause / resume | | something is playing | it is paused |
| Stop | something is playing | nothing is playing | |
| Blackout on / off | (marks the button) | | **Flashing for as long as Blackout is on** (the owner, 2026-10-10: "flashing when active, same with blackout"). Each of the two buttons shows its own state: under Blackout the fade button is steady unless a fade out is also on |
| Geometry on / off, mapping mode on / off (styles `layer` and `mapping`, or the style of Vibes where a file has none) | mapping mode: the owner's switch is on | | **Flashing**: that layer is on |
| **Fade out, then in** (the one button; style `fade`, or the style of Fade out where a file has none) | (marks the button) | | a fade in is running. **Flashing**: the picture is going down or is black from a fade. Under Blackout it is steady: Blackout's own button is the one that shows it |
| Back 10 seconds, forward 10 seconds (style `step`) | something is playing | | |
| Fade out | (marks the button) | the picture was faded out and is still down | |
| Fade in | (marks the button) | | a fade in is running |
| Room scene 1 to 8, a scene by id | the scene exists (and Room is on) | | it is being applied |
| Controllers' bank: before, next | | a place mark: the left button on bank A, both on B, the right one on C | |
| Nothing, a spare control, a level, a pairing code on the display, the quarter turn, the mirrors, the overlay, Mute, Loop, the test pattern | | | |

"Which clip is playing" is decided by the file's name. Two pads with the same clip both show it.

### Novation Launchpad Mini: lights on, with colour

Source: Novation, *Launchpad S Programmer's Reference Manual* 1.02 (https://fael-downloads-prod.focusrite.com/customer/dev/s3fs-public/novation/downloads/10753/launchpad-s-prm.pdf), read in full by the reviewer of pull request #82. It confirms: a note-on on channel 1 per pad (`90h`, the pad's key, a velocity); in the velocity, red in bits 0 to 1 and green in bits 4 to 5, each 0 to 3, and off is `0Ch`, so the value is 16 x green + red + 12; control changes `68h` to `6Fh` for the round buttons along the top, with the same value; and `B0h 00h 00h`, which turns all LEDs off and restores the power-on settings. The manual also describes double buffering and a rapid update; neither is used, only one message per pad. **Its flashing is used since D75**, for the one fade button, from the same manual ("Adventures in Double Buffering" and "Example: Flashing LEDs", read for this change with macOS's own PDF reader): `B0h 00h 28h` puts the Launchpad in flash mode ("write to buffer 0, and swap the display buffer every 280ms"); a colour written with bits 3 and 2 set (copy mode, which every value here has: the 12 in the formula) is lit in both buffers and stays steady; the same colour with bit 2 cleared ("subtracting 4 from the velocity value") is lit in one buffer and flashes. So the box sends `B0h 00h 28h` once after the reset, and flashing full red is `0Fh` less 4, `0Bh` (11). 280 ms a half is a little slower than the box's own two a second. The box sends 200 messages a second at most; that figure is this program's own limit, not one from the manual (an earlier version of this page gave "400 a second" as the manual's, which is not in it).

**Not confirmed from a primary source: that the original Launchpad Mini (USB id `1235:0036`) uses the Launchpad S protocol.** The manual is for the Launchpad S. That the Mini shares it is widely reported, and the recording of the owner's unit fits its key numbers, but Novation's document does not say so. Not seen on the hardware.

| Colour | Means | Value (low, medium, high brightness) |
| --- | --- | --- |
| dark | nothing here | 12 |
| amber | there is something here: a pad of **bank A** with something on it, a preset, a scene, a button that does something now | 29, 29, 46 |
| yellow-green (green 2, red 1; at High green 3, red 2) | a pad of **bank B** with something on it | 45, 45, 62 |
| orange (green 1, red 2; at High green 2, red 3) | a pad of **bank C** with something on it | 30, 30, 47 |
| bright green | **the pad that is playing**, in every bank and at every brightness | 60 |
| green | it is on now: the preset in use, Vibes running, a scene being applied, a fade in running | 28, 44, 60 |
| dim red | this button darkens the screen (blackout, the fade button) or stops what plays | 13, 13, 14 |
| full red, flashing | **the screen is dark, or going dark**: Blackout is on (its button), the picture is going down or is black from a fade (the fade button) | 11 at every brightness |

Every value is the manual's formula, 16 x green + red + 12. **Whether three bank colours can be told apart on the real pads, above all at Low, has not been seen.**

Pause / resume is green while something plays and amber while it is paused. Every pad and round button has a light (80). When the controller is plugged in the box sends the reset first (so it is in the X-Y layout the profile expects, with everything dark), then the flash mode, and then all 80 lights; once per plug-in, not again when a failed writer is tried again. When lights go off it sends the reset again.

### Korg nanoKONTROL2: off until you switch it on

**Set LED mode to External in Korg's editor first.** In the KORG KONTROL Editor: select the nanoKONTROL2, open Common, set **LED Mode** to **External**, and write the scene to the controller (Communication > Write Scene Data). In the factory setting, Internal, each light follows its own button and ignores the box, so switching Lights on here does nothing you can see. In External mode, as reported, a button no longer lights when you press it: only the box lights it.

Source: Korg's nanoKONTROL2 Parameter Guide (E1), page 9 (https://cdn.korg.com/us/support/download/files/c8d0cd6808e12d3672845cadcdbbfe9b.pdf), read by the reviewer of pull request #82. It says: **LED Mode** [Internal, External] covers the transport buttons and the S, M and R buttons; in External mode an LED lights when the controller receives that button's **On Value** and goes dark when it receives its **Off Value**.

**So the box's messages are right only with the factory values.** The box sends a control change with the button's number and 127 for on, 0 for off, on channel 1. That holds while each button still has its factory On Value (127) and Off Value (0) and the controller is on its factory MIDI channel (1). If a scene was edited in Korg's editor (another channel, other values), the lights stay dark or behave oddly: put the buttons back to On Value 127, Off Value 0 and the channel to 1, or do the factory reset (hold PREV TRACK, NEXT TRACK and CYCLE while plugging in; that also sets LED Mode back to Internal). The guide has no table of factory values, so "127, 0, channel 1" is itself from secondary reports. **Unverified** on a real unit. Which buttons have no LED is not in the guide; from a secondary report (nickhwang.com, "Korg nanoKontrol2 and Max", 2012: "A few buttons on the nanoKontrol2 do not have LEDs behind them") and the guide's list, the box lights S, M and R of each strip, Cycle, and Rewind, Forward, Stop, Play and Rec (30), and takes Track <, Track >, Marker Set, Marker < and Marker > to have none, so the controllers' bank has no light on this controller.

**What Internal mode looks like** (the factory setting, and what you see if the editor step was skipped): each of those buttons lights while you hold it (or toggles, if the button is set to Toggle) and nothing the box does changes any light. Lights "on" on the card with a nanoKONTROL2 whose buttons only light under your finger means LED Mode is still Internal.

| Light | Lit | Slowly pulsing | 
| --- | --- | --- |
| S 1 to 8 | pad 1 to 8 of the controllers' bank holds a clip | that clip is playing |
| M 1 to 8 | the shader on screen has that preset | it is the one in use |
| R 1 to 4 | the Room scene exists | it is being applied |
| R 7 (the fade button) | a fade in is running. **Flashing, by the box switching it on and off**: the picture is going down or is black from a fade | |
| R 5 | an effect is on | |
| R 8 | **Flashing, by the box switching it on and off**: Blackout is on | |
| Cycle | Vibes is running | |
| Rec | Vibes is running with the set Show | |
| Play | something is playing | it is paused |
| Stop | nothing is playing | |
| Rewind, Forward | a playlist is playing | |

### Akai MIDI Mix: off, unverified

**Nothing about the MIDI Mix's lights is confirmed from a primary source.** No Akai document describes them (the MIDImix User Guide 1.0 says nothing about messages the unit receives), so they are off until you switch them on. The source is secondary: Tero Heikkinen, "AKAI MIDImix & Processing Midibus" (oldmachinery.blogspot.com, 2018-04-15): "Sending note-ons to MUTE, REC ARM or Bank button values with velocity 127 will turn the associated lights on. Sending note-ons with velocity 0 will turn the lights off. Sending note-offs does nothing." Open controller scripts (mfeyx/akai-midimix-bitwig, tstriker/akai-midimix) do the same. The Solo button cannot be lit, and the Solo+Mute row shows only while Solo is held, so neither gets a light from the box. **So the fade button and Blackout of this layout, which are on that row, have no light and do not flash on a MIDI Mix**; the panel's two buttons do.

| Light | Lit | Slowly pulsing |
| --- | --- | --- |
| Mute 1 to 8 | pad 1 to 8 of the controllers' bank holds a clip | that clip is playing |
| Rec Arm 1 to 8 | the shader on screen has that preset | it is the one in use |
| Bank Left, Bank Right | the bank the controllers are on: left on A, both on B, right on C | |

### How it works

- **Only a controller that is certainly the profile's is opened for writing**, on a second, write-only handle. "Certainly" is stricter than what gives a controller its layout: its USB id is one the profile lists, or the card list was read and gave a product name the profile lists. A match by the card id alone (the fallback when `/proc/asound` cannot be read; "Mini" and "Mix" are the last word of many product names) gives the layout as before and **never** a writer: the card then says "Lights are off: the box could not make sure which controller this is, so it sends it nothing." and the box looks again every ten seconds. A controller without a profile, or with a profile that has no `lights` section, never is opened for writing. The path must be `/dev/snd/midiC<n>D<n>`, not a link, and a character device with ALSA's major number (116).
- **Only fixed messages are written**: for each light a note-on or a control change on the section's channel with the control's own number, and as its value a number written out in the profile's styles; plus the profile's set-up and clear messages. No SysEx: a profile may list only note-off, note-on and control change, on the section's own channel, and no channel mode message (controllers 120 to 127); the check refuses anything else. A request can say three things about lights (on or off, one of three brightness words, "test") and is refused if it carries anything else; no byte of a request, a clip name or a shader name reaches a controller.
- **One writer per controller, on its own thread.** It keeps a table of the value each light should have and sends what differs from what the device took, at most 200 messages a second. A burst of changes costs one message per light, not one per change. A device that takes nothing is not waited for and nothing piles up. A write error ends that controller's writer only (it is tried again after ten seconds while the controller is still there); the other controllers and the controls go on.
- **Unplugged and plugged in again**, the controller gets its set-up message and the whole state. A writer that failed and is tried again sends the whole state without the set-up message.
- **Closing.** The kernel waits for a MIDI output to drain when its handle is closed, up to about ten seconds, and holds the device's open lock meanwhile. So when a controller is not taking its bytes, the writer drops what is waiting first (`SNDRV_RAWMIDI_IOCTL_DROP`) and the close is quick. If the drop fails, the close can take those ten seconds; it happens on the writer's own thread, and stopping waits 2.5 seconds for it at most. Not run against a real device.
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
| Stop, Pause / resume (the panel's Freeze), Blackout on / off, Fade out, Reset mix | trigger | As in the panel |
| **Fade out, then in** (`fade`) | trigger | The one fade button: fades out in 2 seconds, and at the next press in. The box decides which from what the screen is doing (`POST /api/fade`): the picture is down when a Fade out is running or has left it black, or when Blackout is on, and then the press fades in (which also ends the Blackout). It is not a count of presses, so it stays right when a fade was started elsewhere, when Blackout was used and when a play brought the picture back. A press in the middle of a fade turns it round from where the picture is, and two presses that meet are taken one after the other |
| Opacity, Zoom (`size`), Position X (`position`), Position Y (`position_y`), Speed, Volume | level | Follows the control: see "How a level follows a knob" |
| Rotate a quarter turn (`rotate`) | trigger | 0, 90, 180, 270 degrees and round |
| Mirror left to right, mirror top to bottom (`flip_h`, `flip_v`) | trigger | The other state |
| Overlay picture on / off (`overlay`) | trigger | The other state of the overlay's switch; nothing when no picture was chosen. It is saved, as from the panel |
| Back 10 seconds, forward 10 seconds (`seek_back`, `seek_forward`) | trigger | As the panel's two buttons |
| Loop on / off, sound off / on (`loop`, `mute`) | trigger | The other state; the box asks the player which it has |
| Geometry on / off (`layer_geometry`) | trigger | Puts that controller's Geometry layer on or off: the controls that have another self in it (in the standard layouts, the nanoKONTROL2's knobs 1 to 3) take it. Nothing is asked of the box |
| Mapping mode on / off (`mapping_mode`) | trigger | Enters and leaves mapping mode (press twice; needs the owner's switch). See "Layers" |
| Mapping mode: the surface before, the next; the corner before, the next; nudge left, right, up, down; the step; undo (`map_surface_prev`, `map_surface_next`, `map_corner_prev`, `map_corner_next`, `map_left`, `map_right`, `map_up`, `map_down`, `map_step`, `map_undo`) | trigger | Only in mapping mode; outside it the box refuses them |
| Mapping mode: nudge by turning (`map_x`, `map_y`) | a knob or fader | Only in mapping mode: a step for each step the knob is turned |
| Test pattern on / off (`test_pattern`) | trigger | The colour bars, or off again (which clears the screen). In no standard layout: it takes the screen |
| Blackout while held up | level | Black at 64 or more, shown below |
| Vibes on / off | trigger | Starts the endless shader rotation, or stops it if it is running (see [SHADERS.md](SHADERS.md)) |
| Vibes: next shader | trigger | Goes to the next shader now (only while Vibes is running) |
| Vibes: time each shader stays | level | A knob or fader picks one of 15 steps from bottom to top: 15, 30, 45, 60, 90, 120, 180, 240, 300, 420, 600, 900, 1200, 1800, 3600 seconds. It is saved, and the shader on screen follows the new time |
| Shader control 1 to 8 (`shader_control_1` ...) | follows the control | The n-th input of the shader on screen, whatever it is (numbers, switches, choices and events, in the file's order; colours and points have no knob). From a **knob or fader**: a number spreads over its MIN to MAX, a switch is on from 64 up, a choice is picked by position, an event fires at 64 or more. From a **pad or button**: a switch toggles, a choice steps to the next (round the end), an event fires, a number goes back to the file's own value. A shader with fewer inputs ignores the rest |
| Shader speed (`shader_speed`) | level | 0 (frozen) to 4 times; the shader's own pace, 1, is the middle of the control. TIME goes on from where it is |
| Previous shader, Next shader (`shader_prev`, `shader_next`) | trigger | Within the active set: steps Vibes while it runs, otherwise puts on the neighbour of the shader on screen |
| Preset 1 to 8 (`shader_preset_1` ...) | trigger | Applies that preset of the shader on screen, in the order they were saved |
| Shader colour turn, Shader brightness (`shader_hue`, `shader_brightness`) | level | The palette shift, -180 to 180 degrees (the middle is none), and the brightness trim, 0 to 2 (the middle leaves it alone) |
| Previous clip, Next clip (`clip_prev`, `clip_next`) | trigger | The neighbour in the playlist that is playing (a folder, a preset); nothing when there is none |
| Fade in (`fadein`) | trigger | From black up to the mix opacity in 2 seconds; also ends a blackout |
| Room scene 1 to 8 (`scene_1` ...) | trigger | Applies the n-th scene of the Room screen's list (needs the Room module; see below for a scene by its id) |
| Pad of the controllers' bank (`bank_pad`, with the pad 1 to 12) | trigger | Plays that pad of the bank the controllers are on |
| Controllers' bank: the one before, the next (`bank_prev`, `bank_next`) | trigger | Steps that bank through A, B, C and round. It starts at A and is not saved; it is not the bank a phone shows |
| Vibes: start the set Ambient, start the set Show (`vibes_ambient`, `vibes_show`) | trigger | Starts Vibes with that set, by its name; these are the two sets a box starts with. If the set was renamed or removed nothing happens and the log says so |
| Effect amount (`effect_amount`) | level | The mix between the picture as it is (the bottom) and the filtered picture (the top) of the effect that is on ([SHADERS.md](SHADERS.md#effects)) |
| Effect control 1 to 8 (`effect_control_1` ...) | follows the control | The n-th input of the effect that is on, exactly as a shader control does for a shader |
| Effect on / off (`effect_toggle`) | trigger | Takes the effect off; with none on, puts the one that was on last back (the first of the list if there was none) |
| Previous effect, Next effect (`effect_prev`, `effect_next`) | trigger | The neighbour in the list of effects, put on in place of the one that is on (the first or the last when none is) |
| Show a one-time presenter code, Show a one-time full access code (`code_join`, `code_owner`) | hold | Held for 3 to 10 seconds and let go: the box draws a pairing code on its own display for 2 minutes. Off until switched on under People and codes. See "A pairing code on the display" |
| Nothing (`none`) | trigger | Does nothing: switches one control of a standard layout off |

A fourth kind of action, **Apply a Room scene** (`scene`, a trigger that carries the scene's id), is assigned from the Room screen: under "Set up the room" each scene has a MIDI button that starts the same Learn. The mapping is then listed here like the others (as "scene"), and removed here. It needs the Room module; a scene removed later leaves a mapping that does nothing, and the log says so. See [ROOM.md](ROOM.md).

The Vibes and shader actions are assigned with Learn like any other; they are not in the built-in map. They need the **Shaders and Vibes** module to be on: while it is off they do nothing, and the log says so once. They go through the same calls as the panel (`/api/shaders/values`, `/api/shaders/step`, `/api/shaders/preset`), as a presenter: every value is checked there, and a call only notes the wish and answers, so the thread that reads the controller never waits for the GPU. A knob sweep reaches the box at 20 changes a second and the GPU at five at most; the last position always lands. The MIDI page's action list offers all of them; the Shaders page teaches the three Vibes actions.

The **effect** actions are the same in every way: they need the Shaders and Vibes module, go through the panel's own calls (`/api/effects`, `/api/effects/values`, `/api/effects/step`) as a presenter, and never wait for the GPU, for the player or for its lock: a value, a step and the one button are noted from what the panel remembers, and a worker does the rest. They do nothing, and the log says why, while a generator shader or Vibes has the screen; with nothing playing a step or the button is noted and then comes to nothing (the Effects card says why). **The one button acts on what was asked for last**, so two quick presses are on and off. **Switching is held to about three a second**: an effect goes on at the earliest 0.35 seconds after the last switch (Off never waits), so a button held down or a sequencer on it cannot flash a filter; "Allow faster than the flash limit" lifts that. Their lights are in the table under "What a light shows". The Effects card on Mix has a MIDI button beside the amount, beside each of the first eight inputs a knob can drive, and under Previous, On / Off and Next. **On the three shipped layouts the effect actions sit only on controls that were spare** (the tables above); no control that had an action was moved. None of them has been pressed on a real controller.

### How a level follows a knob

The first report from the real controllers named the zoom and the positions. Three things were wrong by design, not by a wrong number: a knob has no notch and 0 to 127 has no middle step, so the picture never sat exactly at 100 percent or exactly centred; a knob left anywhere made the picture jump at the first touch; and zoom spent the bottom eighth of its way under 25 percent, where a picture is a speck, with 100 percent at no place a hand could find.

| Action | Range on a control | Centre (nine steps, 60 to 68) | Curve | Takeover on a controller with a layout | Why |
| --- | --- | --- | --- | --- | --- |
| Zoom (`size`) | 25 to 200 percent | 100 percent | 1.6 | pickup | under 25 percent nothing is seen; the curve gives a seventh of a percent a step beside the centre and about 2.7 at the ends. `"min": 1` gives the panel's whole range back |
| Position X, position Y | -100 to 100 | 0 | 1.6 | pickup | fine where a picture is lined up, the whole way still in reach |
| Clip speed | 0.25x to 2x | 1x | straight (two halves) | pickup | the clip's own speed where the hand rests |
| Shader speed | 0 to 4 times | 1 (its own pace) | straight (two halves) | pickup | the same rule as the clip's speed; it was a quarter of the way up |
| Shader colour turn | -180 to 180 degrees | 0 | straight | jump | a colour turn hides nothing |
| Shader brightness | 0 to 2 | 1 | straight | pickup | |
| Opacity, volume | 0 to 100 | none | straight | pickup | as before |
| Effect amount | 0 to 1 | none | straight | pickup | as before |

A level with a centre gives exactly the centre for nine steps around the middle of the control; below and above, the way from the centre to each end is the fraction of that half raised to the curve. A level without one is a straight line, as it always was.

**A mapping can say four things about its own level** (stored with the mapping; an older version of the program ignores them): `"min"` and `"max"` (another range, within what the panel allows: zoom 1 to 200, speed 0.25 to 2; if the range no longer holds the centre, the control spreads evenly over it), `"invert": true` (the other way round), and `"takeover": "pickup"` or `"jump"` (in place of the action's own rule; `"pickup"` also works on a controller with no layout). The card's chooser does not offer them yet (that is the editor, the next part of D75); the API takes them in `{"set": {"action": {...}}}` and `{"add": {...}}`.

### What a controller still cannot do

Looked at against the whole panel for D75. **Built with it:** position Y, the quarter turn, the two mirrors, the overlay's switch, ten seconds back and forward, loop, mute, the test pattern, the one fade button. **Not built, and why:**

- **Of the Mapping screen**, a controller has mapping mode (above): the surface, the corner, nudges and undo, behind the owner's switch. Not: Mapping on / off, screen or picture corners, adding and removing surfaces, saved mappings. Those need a full-access device.
- Seek to a place, a pad by its name, volume in steps, the transition and its duration, anything of System.

A trigger fires once per press (a note-on, or a CC that goes from below 64 to 64 or more), not on release or repeat, and a button cannot fire again within a quarter of a second, so contact bounce cannot repeat it. Right after Learn captures a control, that control is ignored for about half a second so a fader you are still moving does not run its old mapping. A fader sweep is thinned to 20 changes a second and the last position always lands.

## A pairing code on the display

For when you stand at the box with a controller and no phone or laptop that is paired. **Hold** the control that has the action for **3 to 10 seconds and let go**: the box draws a one-time code on its own display, with its address, for 2 minutes. On the new phone open the address, type the code in the "6 digit code" field of the pairing page (or scan the QR code), and it is paired.

**It is off until a full-access device switches it on**: System > People and codes > "A code from a controller". There are two switches, because there are two actions:

| Action | Pairs one device as | Switch |
| --- | --- | --- |
| Show a one-time presenter code (`code_join`) | Presenter (can play and mix) | "Presenter codes from a controller" |
| Show a one-time full access code (`code_owner`) | Owner (everything), like the PIN | "Full access codes too", which is only there while the first is on |

Anyone who can reach a controller plugged into the box can do this once it is on, so decide with the room in mind. A presenter code gives a stranger at the controller little they did not have (the controller already plays and mixes), except that the paired phone keeps working after they walk away, until you remove it from Paired devices. A full access code gives everything.

**The rules.**

- **A hold that ends, not a press.** Nothing happens when the control goes down. The code is asked for when it comes up again, and only if it was down for 3 to 10 seconds. So a tap does nothing, a stuck note does nothing (it never ends), something left lying on a pad does nothing (it ends too late or never), and a control held down does not repeat. There is no sign on the display or the controller that 3 seconds have passed: count to four and let go. The hold is timed by when the box read the press and the release from the controller, not by when it got round to them: one part of the box only reads and notes the time, another acts, so a tap stays a tap even while the box waits seconds for the player on another pad. A hold is also forgotten, with no code, when Learn starts or ends, when a mapping is saved or a switch on the MIDI page changes, and when MIDI is switched off: let go and hold again.
- **A button, not a fader.** A control that sends a note is held as said. A control that sends a controller number (the nanoKONTROL2's buttons) must send 127 when it goes down and 0 when it comes up, with nothing between; any other value ends the hold with no code. That is what a button set to Momentary sends, and what no fader or knob can send, so a fader given the action by Learn does nothing. A nanoKONTROL2 button with other On and Off values (changed in Korg's editor) does not work for this.
- **Once, 2 minutes.** The code pairs one device and is gone. Unused, it stops working after 2 minutes and leaves the display by itself.
- **Press again to hide it.** Any press of a control with one of the two actions takes a showing code off the display and ends it, at once and without a hold. That press starts no new code; hold again for a new one.
- **6 an hour**, both kinds together, counted whether or not they were used. After that the display says "No more codes from a controller for now" with the minutes to wait, for a few seconds.
- **Wrong guesses count like wrong PINs**: five from one phone, or twenty in all, and the box takes no PIN and no code for a while. A paired full-access device can lift that (Unblock joining); from the controller you cannot. **So someone on the network who types twenty wrong codes can keep your code from working**: after twenty wrong guesses nobody pairs for 5 minutes, which is longer than a code lasts. The box does not weaken that lock for the controller. It tells you instead: while pairing is locked, a hold shows "Pairing is locked for 5 minutes after wrong guesses. No code was made." and makes none (and counts none); if the lock is shorter than a code's 2 minutes the code is shown with that line under it; and a code that is already showing gets the line when a lock begins. Wait it out, or lift it from a paired device.
- **Devices paired this way.** A presenter paired with a code from a controller is forgotten once it has not opened the panel for 7 days, like a guest (anyone at the controller can make such a device, so they must not pile up; a presenter paired with a code you made on the panel stays, as before). A full-access device paired this way stays until you remove it, and it never takes one of the 20 places kept for the PIN: it is counted with the guests and presenters among the other 180, in whatever order devices were paired, so when those 180 are taken no code of either kind is made, no guest or presenter joins, and the PIN still pairs. The code is used up only once its device is in the list; if the list filled up that instant, the code is still good.
- **On the display only.** The digits are never in an answer of the API (a full-access device sees that a code is showing, which kind, since when and for how long, never the code), never in the log, never sent to a controller: a control with one of these actions has no light, and nothing about a code goes out over MIDI, OSC, DMX or to another box. While the code shows, the preview picture a guest or presenter gets has no on-screen text. The presenter kind is drawn with a QR code; the full access kind is text only, like the PIN.
- **Only from a controller plugged into the box by USB.** These two actions are not calls into the API, so nothing that reaches the box through the API can ask for a code: not OSC, not DMX, not the schedule, not a Room scene, not a paired device, not remote support. The box also checks that the controller's sound card has a USB id; a MIDI port with none (a virtual or network port) is refused and the log says so. **The USB id proves a USB MIDI interface, not a person.** A DIN to USB interface (with any MIDI cable behind it), a wireless MIDI dongle, and a computer that presents itself as a USB MIDI device all have one and all count. Whatever is plugged into the box's USB ports can ask, once the switch is on. (The press that hides a code is taken from any MIDI port and with the switch off too: it can only end a code.)
- **A full-access device sees it and can end it.** The card on People and codes says "A one-time presenter code is on the box's display now", with **End this code**, and afterwards what became of the last one (used, and by which device; ran out; hidden at the controller; ended from the panel). Switching either switch off ends a code that switch allowed. Neither the switches nor End work through remote support.
- **Not in a settings file.** The two switches are never exported or imported, and a factory reset switches both off.

**Giving a control the action.** On a controller's card (System > MIDI controller) tap a pad or a button and choose the action, or use Learn. A fader, a knob and a program change cannot have it (a fader given it through Learn does nothing, see "A button, not a fader"). **On a Launchpad Mini the third round button along the top (Top 3, CC 106) shows a presenter code**; while the switch is off it does nothing. No layout has the full access code: put it on a control yourself if you want it. The nanoKONTROL2 has no spare button (its one spare control is fader 8). The MIDI Mix's one spare button is Solo, which is held down while playing to reach the Solo+Mute row, so a hold on it would show a code by accident; it stays spare. On those two, give the action to a button you can do without.

If nothing happens: is MIDI on, is the switch on (People and codes), did you hold for at least 3 seconds and then let go, and is the player running (the code is drawn by the player; if it cannot draw, no code is made). `journalctl -u pvj-web | grep "controller code"` says why a request was refused.

**None of this has run on the Pi or met a real controller.** It ran in unit tests with a fake clock, a fake player and messages handed to the hub.

## Built-in map

On unless you turn it off (System > MIDI controller > Built-in map). It exists so a plain pad controller works with no setup: notes 36 to 71 (and program changes 0 to 35) play pads 1 to 36 (A is 1 to 12, B 13 to 24, C 25 to 36); notes 72 to 76 are stop, pause, blackout, fade out and reset; CC 20 to 24 are opacity, size, position, speed and volume; CC 25 is blackout while up. Real controllers rarely use these numbers (a Novation Launchpad Mini sends notes 0 to 120 and CC 104 to 111), so a controller without a profile is taught with Learn. The built-in map is not used for a controller whose standard layout is on.

## Safety

- Only paths of the form `/dev/snd/midiC<n>D<n>` are ever opened, and only if they are character devices (no links).
- Only the actions in the table are reachable: nothing shuts down, reboots or changes settings (the one setting a controller can change is the Vibes dwell time, between 15 seconds and an hour).
- One action hands out access: the pairing code on the display. It is off until a full-access device switches it on, needs a hold, and has its own rules ("A pairing code on the display").
- At most 50 commands a second reach the player, whatever the controllers send, and a pad or button can fire at most four times a second (a single "play" is many round trips to the player, so the second limit is the one that matters for pads).
- The web service reaches the device through systemd: it needs the `audio` group and access to ALSA devices, and the unit has both (`DeviceAllow=char-alsa rw`: read for the controls, write for the lights; D53).
- Writing: only to a controller that matched a profile with a `lights` section, only the fixed messages of that section, at most 200 a second per controller. See "Lights > How it works".

## Not built yet

- Lights that are addressed differently from their control, more than one light per control, the Launchpad's double buffering, and lights for the Solo row of a MIDI Mix.
- **The editor of D75's second and third part**: a control's name, kind, place and what it sends corrected by pressing it; a layout for a controller the box does not know; a layout as a file; the Learn section folded into the cards; the chooser's options for range, direction and takeover. Planned, not built.
- **Map Mode** for MIDI and OSC (the owner, 2026-10-10: switch it on, pick a control on the screen, turn a knob, mapped; one controller per control, or several sharing it by soft pickup). The sharing by pickup is what the levels do already; the mode itself is not built.
- In mapping mode: a larger step while a button is held; a light for each control's other self (only the mode's own button shows the mode).
- A light per shader input (row 8 of the Launchpad is lit while any shader is on screen; the box does not read the shader's input list for it).
- Motor-fader feedback (needs MIDI output too).
- Mapping to pads by name, banks that follow the controller's own bank buttons, and relative (endless) encoders.

## Verified, and not

**A pairing code on the display (2026-10-07): not tried on the Pi and not on any real controller.** The hold, the two switches, the limits, the display's text and the refusals ran in unit tests only (`tests/test_controller_code.py`). Not known from hardware: whether each controller's buttons send a release the box sees as one (a nanoKONTROL2 button set to Toggle in Korg's editor sends its "off" only at the next press, so its gesture is press, wait, press), whether `/proc/asound/card<n>/usbid` is there for each controller on the Pi's kernel (without it the request is refused), and how the text reads on a projector.

Verified on a real Raspberry Pi 4 (2026-09-30): the module reads a controller through the systemd sandbox and handled 112 messages in a few seconds from a Launchpad Mini. Three controllers (Korg nanoKONTROL2, Akai MIDI Mix, Novation Launchpad Mini) enumerate. **Learn, the multi-controller hub and the new map have only run against pipes standing in for controllers and the browser test, not yet against the real hardware.** The Vibes and shader actions have run only in unit tests with a fake player and a fake clock: no real controller has sent them.

**Controller profiles (2026-10-04): not tried on any real controller.** The three layouts are from the documents named above and one recording of the Launchpad Mini; the nanoKONTROL2's and the MIDI Mix's numbers are not confirmed by a manufacturer's document at all. Matching uses the card ids, product names and USB ids reported from the owner's Pi and is tested against a copy of that card list, but the code has not run on the Pi. Pickup, the double press, hot-plug and the drawn layout ran against pipes, a fake clock and the browser test's fake controller. Please run the two-minute check.

**Controller lights (2026-10-04): not tried on any real controller, and not on the Pi.** The messages are from Novation's Programmer's Reference for the Launchpad S (that the Mini shares its protocol is not confirmed by a primary source), from Korg's Parameter Guide (nanoKONTROL2: only with LED Mode set to External and the factory On and Off values and channel; unverified) and from a secondary source alone (MIDI Mix; nothing confirmed). An independent review of the pull request read both makers' documents and found one medium and seven low points; each is fixed with a test (the journal lists them). The writer, the mapping from the box's state to each light, unplug and replug, the switch, the brightness, Test lights and the refusal under an older service file ran against pipes and the browser test's fake controller. Whether `DeviceAllow=char-alsa rw` lets the panel open a controller for writing on the Pi's systemd has not been run there. The check list is in `tools/DEVICE-TESTING.md`.

**The layouts, the levels and the fade button of D75 (2026-10-10): not tried on any real controller, and not on the Pi.** The three layouts were drawn afresh from the owner's report and from how each controller is built, not from pressing them. How zoom and position feel on a real knob (does the centre sit, does anything jump, is the curve too slow or too fast), whether the Launchpad's flash mode flashes the fade button and leaves every other light steady, whether a nanoKONTROL2's R 7 flashes in External LED mode, and whether three bank colours can be told apart, are all unknown. They ran in unit tests with a fake clock, pipes and a fake player, and the panel's button in the browser test. The check list is "The layouts, the levels and the fade button (D75)" in `tools/DEVICE-TESTING.md`.

**Layers (2026-10-10): the nanoKONTROL2's Geometry and mapping mode have not been on a controller, a projector or the Pi.** Whether a shader knob's wait on the way back feels right or feels dead, whether two minutes is the right time for Geometry and three for mapping mode, whether a nudge of one pixel a knob step is too fine or too coarse on a real wall, and whether the outline with its yellow surface and pink corner can be seen from where the controller stands, are unknown. Rows G1 to G4 and P1 to P11 in `tools/DEVICE-TESTING.md`.
