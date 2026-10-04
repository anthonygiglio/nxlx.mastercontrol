<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Shaders and Vibes (beta)

Moving pictures drawn by the box's GPU instead of played from a file, and **Vibes**: one tap that plays them endlessly for ambience.

Switch it on under System > Shaders and Vibes (beta, off by default). Switching it off while a shader is on the screen asks first, because it stops at once. It is offered on a Raspberry Pi 4, a Pi 5 and x86; not on a Pi 3.

**Read this first: nothing here has been seen on a display, and no speed has been measured on any board.** It has run only in automated tests: against a real mpv with Mesa's software GPU on a virtual display in CI (the picture is checked through screenshots), and against fakes. How many frames a second each shader reaches on a Pi 4, a Pi 5 or a PC is unknown. The cost notes below are counts of work per pixel, not measurements.

## Using it

- **Live screen: the big Vibes button** (presenter, that is live access, and above; full width on a phone). It reads "Start Vibes" and starts the rotation; while that runs it reads "Vibes is playing: Aurora" and a tap stops it. "Now playing" shows `Vibes: <shader>`. **Next one** appears beside it while Vibes runs. The **Shaders** link next to it opens the page below, and Back returns to Live.
- **The Shaders and Vibes page** (System > Shaders and Vibes, or the Shaders link on Live; it is the same page). It replaces the card that was on the Mix screen. Top to bottom:
  1. **On screen now**: the shader's name, the time until the next one, **Start Vibes** / **Stop Vibes** and **Next one** (live access). Why Vibes last ended is said here.
  2. **Shaders**: one row each, with its cost note as "Light work", "Medium work" or "Heavy work" (the first word of the author's `COST` text; any other text is shown as it is). **Play** (live access) shows one shader until something else is played. Full access: the switch **In the Vibes rotation** per shader, applied on tap, and **Remove** for uploads, which asks first. A shader the GPU refused, or a file that cannot be shown, has the reason in red on its row.
  3. **Controls** for the shader on screen (live access): one slider per `float` input with its label, range and default, **Reset** per slider and **Reset all**. A change is sent when you let go. The values are not saved anywhere: they live in the panel until the page is reloaded, and the next start uses the shader's own values. Sending them is the same call as Play, so it ends the Vibes rotation and keeps that shader on screen.
  4. **Vibes settings** (full access), applied on tap with a brief "Saved": **Each one stays for** (30 s, 1, 2, 3, 5, 10, 15, 30 min, 1 hour; a stored time that is not in the list, set from a MIDI knob or the API, shows as "Custom (N s)") and **Change speed and colours a little each round**. Under **Advanced**: picture detail (lines; fewer lines is lighter work) and **+ Add a shader file (.fs)**; a refused file says why under the button.
  5. **Run Vibes from a controller** (full access): the MIDI switch and three teach rows (Vibes on and off, next one, the time each stays) that use the Learn call in place and list what is mapped with Remove; the DMX switch, the ninth channel's number, ranges and last level. Both switches are the feature's one switch (D43).
  From about 900 px the page has two columns and from 1200 px three: the library scrolls by itself (with a filter by name and by work), the stage and its controls are beside it, settings and controllers last. Each card is drawn again only when its own part of the answer changed, and a rotation switch changes only its row. The controls card draws an input by its type in one function (`inputControl`): today `float` only; presets and other input types have their place there.
  A guest (view access) sees what is playing and the list, with no controls.
- **Autostart**: mode **Vibes** (System > At power-up). **Schedule**: the action **Start Vibes**. **OSC**: `/pvj/vibes` (press) starts it, `/pvj/vibes/next` goes to the next shader, the usual `/pvj/stop` ends it.
- **MIDI**: the actions **Vibes on / off**, **Vibes: next shader** and **Vibes: time each shader stays** (a knob or fader), assigned with Learn under System > MIDI controller ([MIDI.md](MIDI.md)). **DMX**: an optional ninth channel: 50 to 99 stop, 100 to 149 start, 150 to 199 next ([DMX.md](DMX.md)). Both act as a presenter through the same call as the panel, under the same limit of 50 commands a second, and do nothing while this module is off (the log says so once). Neither has been tried with a real controller or console.
- Opacity, Fade in and out, Blackout, the overlay picture, mirror, rotate and the projection mapping apply to a shader as to a clip. Speed changes how fast it moves; Freeze stops it.

### Vibes

- Picks from the shaders that are **In Vibes** (bundled and uploaded), in a shuffled order; every shader is shown once before any comes again, and never the same one twice in a row.
- Each stays for the **dwell time** (default 180 seconds, 10 to 3600).
- Between shaders: a **dip to black** over the Mix screen's transition duration (down for half of it, up for half). During a Blackout, and after you have faded the picture out, nothing fades and the screen stays dark: the shaders go on changing unseen until you show the picture again.
- **Variation each round** (can be switched off): every number input gets a new value between its MIN and MAX, pulled towards the default (at most 60 percent of the way to either end, since the author chose the default as the good place); the palette is turned by up to 120 degrees either way around the grey axis (black, white and greys stay as they are); and the shader's time starts somewhere else. The values come from a random generator seeded when the service starts.
- **It never fights the operator** (the autostart rule, D18). It ends when anything else is played (a pad, a clip, a stream, a live input, the test pattern, a schedule entry, a sync server's clip), when Stop is pressed, when one shader is chosen by hand, when the module is switched off, and when the player is restarted. It does not come back by itself; after a player restart it starts again only if Autostart is set to Vibes. The reason it ended is shown on the card. A clip tapped in the middle of a change plays, also with the Mix transition on Dip to black (the first version lost such a clip; see the review notes in the project log).
- A shader the GPU refuses is left out for the rest of that run and the next one is shown; if every shader is refused, Vibes ends and the screen is cleared.

## The bundled shaders

Original generator shaders, written for this project (Apache-2.0, in `pvj/shaders.d`): the first ten below, and two families added later (the next section). All use short fixed loops (at most 5 rounds) and no textures. **Cost is a rough count of work per pixel, not a measurement.**

### The first ten

All slow and quiet, and all in the Vibes rotation until taken out.

| Shader | Picture | Inputs | Rough cost per pixel |
| --- | --- | --- | --- |
| nxlx-aurora | curtains of light over a dark sky | speed, height, tint | low: 4 bands, about 12 sines and 4 colour blends |
| nxlx-drift | soft clouds of colour | speed, scale, warmth | medium: 2 clouds of 3 octaves, 24 lattice values |
| nxlx-ember | warm blobs that merge and part | speed, blob size, glow colour | low: 5 blobs, 10 sines |
| nxlx-horizon | a dusk sky with a slow sun and haze | speed, sun height, haze | low: 4 sines, one length, 4 exponentials |
| nxlx-lattice | two grids turning against each other (moire) | speed, density, colour | low: 4 sines |
| nxlx-nebula | clouds bent by more clouds | speed, fold, scale | medium to high: 3 clouds of 2 octaves, 24 lattice values |
| nxlx-prism | rings of colour around the centre | speed, rings, petals | low: one atan, one length, 2 cosines, a colour blend |
| nxlx-pulse | ripples from three wandering points | speed, ripples, calm | low: 3 lengths, 9 sines |
| nxlx-silk | fine flowing lines | speed, lines, sheen | low: 7 sines |
| nxlx-tide | layers of slow waves | speed, swell, night colours | low: 5 layers, 10 sines |

If the picture stutters, draw fewer lines (the **drawing size** on the card: 360, 540, 720 or 1080 lines, 720 by default, never more than the screen has; mpv scales the result to the screen) and take nxlx-nebula and nxlx-drift out of Vibes first.

### Two more families: Ambient and Performance

Written after the first ten, for two uses, and named by the `CATEGORIES` entry in each file:

- **Ambient**: slow and calm, soft edges, nothing that flashes; made to stay on a wall for hours behind people or beside paintings. They are **in the Vibes rotation** from the start, like the first ten.
- **Performance**: stronger, rhythmic and graphic, made to be played by hand. There is no sound input, so each has a rate or a beat control to ride. They are in the library, and **not in the Vibes rotation until someone puts them there** (the switch on the shader's row): one tap on Vibes in a room never starts a strobe. The settings keep these in a list `included`, beside `disabled` for the ones taken out; only bundled shaders of the category "Performance" start outside, an uploaded file is in the rotation whatever it calls itself.

| Shader | Family | Picture | Inputs | Estimated cost per pixel |
| --- | --- | --- | --- | --- |
| nxlx-bloom | Ambient | Large soft discs of light that cross slowly, like street lamps far out of focus | Speed, Disc size, Out of focus, Bright rim, First colour (colour), Second colour (colour), Background colour (colour) | low: 4 discs, 8 sines a pixel |
| nxlx-caustic | Ambient | A slow net of light, like sun on the floor of a shallow pool | Speed, Scale, Ripple, Sharpness of the light, Water colour (colour), Light colour (colour) | low: 3 rounds of bending, 9 sines and one division a pixel |
| nxlx-contour | Ambient | The contour lines of a map whose hills rise and sink very slowly | Speed, Contour lines, Scale, Line strength, Shade each level flat (switch), Valley colour (colour), Peak colour (colour), Line colour (colour) | low: 4 sines a pixel |
| nxlx-dusk | Ambient | A dusk or dawn sky: a slow glow low on the wall under thin drifting streaks of cloud | Speed, Glow height, Cloud streaks, Hour (choice), Sky colour (colour), Horizon colour (colour) | low: 5 sines and one division a pixel |
| nxlx-fringe | Ambient | Soft fringes where slow ripples from two or three wandering sources meet | Speed, Ripples, Sources apart, Crispness, Third source (switch), Crest colour (colour), Trough colour (colour) | low: 3 square roots and 9 sines a pixel |
| nxlx-kaleido | Ambient | A slow kaleidoscope: soft shapes of three colours, mirrored around the centre and turning gently | Speed, Mirrors (choice), Size of the shapes, Turn slowly (switch), First colour (colour), Second colour (colour), Third colour (colour) | low: one atan, one square root and 5 sines a pixel |
| nxlx-lantern | Ambient | Four paper lanterns that sway a little and glow, each breathing at its own pace | Speed, Lantern size, Sway, Paper ribs, Shape (choice), Light colour (colour), Paper colour (colour) | low: 4 lanterns, 9 sines and 5 divisions a pixel |
| nxlx-moire | Ambient | Two sets of fine rings, one circling the other slowly, and the large soft shapes that appear between them | Speed, Rings, Distance between the two, Softness, Invert (switch), Line colour (colour), Background colour (colour), Centre (point) | low: 2 square roots and 4 sines a pixel |
| nxlx-petal | Ambient | A flower of three rings of petals that open and close a little and turn slowly against each other | Speed, Petals (choice), Size, Softness, Outer colour (colour), Inner colour (colour), Background colour (colour), Centre (point) | low: 3 rings, one atan, one square root, 6 sines a pixel |
| nxlx-ribbon | Ambient | Wide bands of colour lying over each other and swaying slowly, like layers of cloth | Speed, Bands, Sway, Soft edges, Colours (choice), Own colour, bottom (colour), Own colour, top (colour) | low: 4 sines a pixel |
| nxlx-ridge | Ambient | Three ranges of hills in morning mist under a pale sun, each drifting at its own slow pace | Speed, Hill height, Mist, Sun (switch), Sky colour (colour), Mist colour (colour), Hill colour (colour) | low: 3 ranges, 6 sines and one division a pixel |
| nxlx-stars | Ambient | A night sky that barely moves: two layers of stars that twinkle a little over a faint band of light | Drift, Stars, Twinkle, Star size, Band of light (switch), Sky colour (colour), Band colour (colour) | low: 2 layers of stars, 3 sines and about 90 sums and products a pixel |
| nxlx-tiles | Ambient | A wall of soft tiles, each brightening and dimming at its own slow pace | Speed, Rows of tiles, Seams, Contrast, Tiling (choice), Dim colour (colour), Bright colour (colour) | low: one sine and about 70 sums and products a pixel |
| nxlx-veil | Ambient | Three colours folding slowly into one another, like light through a thin curtain | Speed, Scale, Folds, First colour (colour), Second colour (colour), Third colour (colour) | low: 5 sines a pixel |
| nxlx-bars | Performance | Bars that flash on the beat in four patterns; at most 3 flashes a second unless Fast is on, which doubles the rate (up to 6 a second: not for photosensitive people) | Beats a second, Bars, Fade after each beat, Pattern (choice), Lying bars (switch), Fast (switch), Lit colour (colour), Unlit colour (colour) | low: one sine and about 40 sums and products a pixel |
| nxlx-beam | Performance | Up to four beams of light that sweep to and fro from one point, like searchlights in haze | Sweeps a second, Beams (choice), Beam width, Sweep, Haze, First colour (colour), Second colour (colour), Where the beams start (point) | low: 4 beams, one atan, one square root, 5 sines and 2 divisions a pixel |
| nxlx-burst | Performance | Streaks of light that shoot out from one point, with a ring that leaves the middle on every beat (at most 3 a second) | Beats a second, Streaks, Streak length, Ring on the beat (switch), Fly inward (switch), Streak colour (colour), Background colour (colour), Centre (point) | low: one atan, one square root and about 60 sums and products a pixel |
| nxlx-checker | Performance | A checkerboard that travels and is bent three ways: into waves, into a bulge or into a twirl | Travel rate, Squares, Bend, Kind of bend (choice), Travel upward (switch), Invert (switch), First colour (colour), Second colour (colour) | low: at most 5 sines a pixel |
| nxlx-glitch | Performance | A striped picture torn into blocks that jump sideways and split their colours on every step; at most 3 steps a second unless Fast is on (up to 6 a second: not for photosensitive people) | Steps a second, How much is torn, Rows of blocks, Jump, Colour split, Fast (switch), First colour (colour), Second colour (colour) | low: 3 sines and about 80 sums and products a pixel |
| nxlx-grid | Performance | A wall of tiles that light on the beat in four patterns; at most 3 flashes a second unless Fast is on, which doubles the rate (up to 6 a second: not for photosensitive people) | Beats a second, Rows of tiles, Fade after each beat, Tile size, Pattern (choice), Fast (switch), Lit colour (colour), Unlit colour (colour) | low: no sines, about 60 sums and products a pixel |
| nxlx-halftone | Performance | A field of dots that swell and shrink as a wave runs through them: a sweep, rings from a point, or a beat | Waves a second, Rows of dots, Wave (choice), Dots on the diagonal (switch), Invert (switch), Dot colour (colour), Background colour (colour), Centre of the rings (point) | low: 2 square roots and 2 sines a pixel |
| nxlx-scope | Performance | Up to three glowing traces like an oscilloscope, kicked by a beat (at most 3 a second) | Beats a second, Waves across, Height, Kick on the beat, Traces (choice), Wave (choice), Trace colour (colour), Screen colour (colour) | medium: 3 traces, 9 sines, 3 square roots and 6 divisions a pixel |
| nxlx-spokes | Performance | A wheel of spokes cut into bands that turn against each other, with the turning rate, the curl and the centre to play | Turning rate, Spokes (choice), Bands, Curl, Mirror left and right (switch), First colour (colour), Second colour (colour), Centre (point) | low: one atan, one square root and one sine a pixel |
| nxlx-stripes | Performance | Travelling stripes at any angle that can be bent into waves, a zigzag or bricks, and mirrored | Travel rate, Stripes, Angle, Bend, Kind of bend (choice), Mirror left and right (switch), First colour (colour), Second colour (colour) | low: 3 sines a pixel |
| nxlx-tunnel | Performance | A flight through a patterned tunnel, with the travel rate, the twist and the vanishing point to play | Travel rate, Twist, Depth, Pattern (choice), Segments (choice), Fly backwards (switch), Wall colour (colour), Vanishing point (point) | low: one atan, one square root, one division and 2 sines a pixel |

The rules every one of them keeps (the tests check what a test can check):

- **Cost.** The owner measured the first ten on a Raspberry Pi 4 at 720 lines, where about 16 ms is there for the shader: nxlx-silk 7.5 ms, nxlx-lattice 8.3, nxlx-horizon 9.4, nxlx-prism 9.4, nxlx-pulse 10.6, nxlx-ember 11.2 (these hold 30 pictures a second), nxlx-tide 15.1, nxlx-aurora 20.6, nxlx-drift 30, nxlx-nebula 32.7. The new ones were written to stay at or under nxlx-ember: a handful of sines, at most one loop of 3 or 4 rounds, no marching, no clouds of noise. "low" in the table means that by the count of operations against those measured ones; "medium" means somewhat above nxlx-ember (and only Performance shaders may be medium, since they are not in the rotation). **None of the new shaders has been measured on a Pi**: the class is an estimate.
- **Inputs.** 4 to 8 each, with plain labels: numbers, switches (`bool`), choices (`long` with `VALUES` and `LABELS`), colours (so a picture can be matched to a room or a show) and, in a few, a point (in 0 to 1 of the picture, so it does not move with the drawing size). Today the panel shows sliders for the numbers only and Vibes varies only numbers, so a switch, a choice, a colour and a point keep their `DEFAULT` until the panel has controls for them; each default is the look the shader was made for.
- **Time.** `TIME` is never fed into a sine as it is. It is folded first, `fract(TIME * rate)`, a place in a cycle between 0 and 1, so the picture is as smooth after a day as in its first minute (a large number added to a small coordinate loses the coordinate's fine steps).
- **Shape.** Everything is laid out from `RENDERSIZE`, so circles stay round on a 16:9 and on a 4:3 projector.
- **Flashing.** No Ambient shader flashes. A Performance shader that flashes does so at most 3 times a second at any setting of its sliders: the rate is capped in the code, not only by the slider's MAX. Where a switch **Fast** exists it doubles that (up to 6 a second), it is off by default and the description says so; do not switch it on in a room where someone may be photosensitive. A shader that flashes never goes to black between flashes: the picture dims to a floor.
- **Own construction.** No code was taken from Shadertoy, The Book of Shaders, ISF-Files or anywhere else, and none of the hash and noise one-liners that are passed around is used: where a shader needs scrambled numbers it makes them from whole-number arithmetic that a 32-bit float holds exactly (see `scramble` in nxlx-bars.fs).
- **What CI draws.** Every bundled shader is drawn by a real mpv on Mesa's software GPU, on OpenGL ES and on desktop OpenGL, with its defaults and with varied numbers; every new one also with all numbers at MIN, all at MAX, late in `TIME`, and once per switch, per choice and per corner of a point. Each picture must be varied and not dark. That proves they compile and draw on that GPU; it says nothing about speed or about the Pi's driver.

## ISF: what is supported

An ISF file is a GLSL fragment shader with a JSON comment at the top (see isf.video). Only **generators** are supported: shaders that draw from nothing.

| ISF | Here |
| --- | --- |
| `INPUTS` of type `float`, `bool`, `long`, `color`, `point2D`, `event` | written into the shader as constants with their `DEFAULT` (an `event` is never pressed). `float` inputs are sliders in the panel and are varied by Vibes; the others keep their default. |
| `TIME` | seconds, counted from frames (see below) |
| `TIMEDELTA`, `FRAMEINDEX` | 1/30, and mpv's frame number |
| `DATE` | the date and time when the shader was put on (it does not tick) |
| `RENDERSIZE` | the drawing size in pixels |
| `isf_FragNormCoord` (and the old `vv_FragNormCoord`) | 0 to 1, origin at the bottom left |
| `gl_FragCoord` | pixels of the drawing size, origin at the bottom left |
| `gl_FragColor` | the result; alpha is laid over black |
| `PASSINDEX` | 0 |

Refused, with a message that names the reason:

- more than one pass, a pass with a `TARGET`, its own size or `PERSISTENT` (`PASSES`), and `PERSISTENT_BUFFERS`: there is no picture kept between frames;
- `image` and `cube` inputs and every `IMG_...` call (`IMG_PIXEL`, `IMG_NORM_PIXEL`, `IMG_THIS_PIXEL`, `IMG_SIZE`): filters that need a picture are not supported. With no picture inputs there is nothing sensible for the `IMG_` calls to read, so none of them is mapped;
- `audio` and `audioFFT` inputs;
- `IMPORTED` pictures;
- vertex shaders (`.vs` files) are not read at all.

## How it sits in the player

Checked against a real mpv 0.37 in CI (`tests/test_shaders_gpu.py`); the reasons are worth knowing before changing any of it.

- **The carrier.** A shader needs frames to draw on. The player plays `av://lavfi:color=c=black:size=64x36:rate=30,format=rgb0` (the size is the screen's shape reduced to whole numbers, so the picture fills the screen exactly): a black picture made by the player itself, 30 times a second, for ever. It is the same kind of address as the test pattern and the test tones, which the hardened player already plays, so **none of the D22 flags changed** (`--access-references=no` and the rest stay as they are). The address is built from whole numbers only; nothing from a shader file or a request goes into it.
- **The stage.** The shader is an mpv user shader on the **NATIVE** hook, the first one, and sets its own size with `WIDTH` and `HEIGHT`, so the carrier stays tiny whatever the drawing size. The first design used the MAIN hook: there the panel's opacity, fades and Blackout did nothing, because mpv applies brightness when it converts the picture to RGB, before MAIN. With an RGB carrier and the NATIVE hook, brightness comes after the shader and works as on a clip (seen in CI on both stages).
- **Together with the projection mapping.** The mapping stays what it was: a shader on the OUTPUT hook, the last one. The player object now keeps both layers and writes them to mpv's one shader list together (`[source] + mapping`), so the mapper replacing its file does not wipe the source and the other way round; `pvj/mapper.py` is unchanged. The 8-bit buffers that the mapper asks for are used while either layer is on (the shader adds a little noise of half a step, so slow gradients do not show bands). A quad over a shader, and a clip taking over while the mapping stays, are tested on the real mpv.
- **Never over a clip.** The source shader draws in place of the picture, so it must be gone before anything else plays. `Player.play`, the live input and Stop take it off under the player's lock before they load; a restarted mpv has lost it and the layer is dropped when that is noticed.
- **Whose screen it is.** The player counts an epoch that goes up on every play and stop. Vibes remembers the epoch of its own last shader and passes it with each change; the player compares it under its lock at the moment of the change. So a rotation that was just about to change cannot land on a clip someone started a moment before.

### TIME, and how exact it is

mpv gives user shaders no clock, only `frame`, the number of pictures its renderer has drawn. TIME is `frame / 30` (the carrier's rate) plus the round's start offset.

- While the GPU keeps up, TIME runs at real time (CI: within the loose bounds of the test, 0.5 to 1.5 times the wall clock on a busy runner; not measured more closely).
- **When the GPU is too slow, frames are dropped and TIME runs slower**: the picture slows down instead of jumping. So a shader that is too heavy shows as slow motion, not as wrong positions.
- Every extra redraw adds a frame (a change of the overlay or the on-screen PIN); Speed scales it; Freeze stops it.
- `frame` counts since the player started, not since the shader started, so TIME begins anywhere.
- It is put together in high precision from small whole numbers. What was seen in CI (OpenGL ES on Mesa's software GPU): a shader that computed `mod(float(frame), 1048576.0)` drew a **black picture without any error**, while `float(frame) / 30.0` and `mod(float(frame), 1024.0)` drew correctly; on desktop OpenGL all of them did. The likely reason is that `frame` has medium precision there and a medium-precision number cannot hold more than 65504. With the high-precision build TIME was right in CI at 100000 seconds, and it starts again after 4,194,304 frames (38.8 hours). **Not tested: a large `frame` itself** (every run was short). If a GPU keeps `frame` in 16 bits, TIME would jump back about every 36 minutes; that is a guess, and whether the Pi's GPU does it is not known.

### A shader the player refuses

A bad shader must never leave a broken show. Before a shader the GPU has not yet taken is put on, the panel opens a second connection to the player that receives its error log (`request_log_messages`), then waits until mpv reports a drawn pass with that shader's name (`vo-passes`) or an error, at most 4 seconds. Only a pass that mpv timed counts as drawn: a refused shader is listed too, with a time of 0. On an error the shader before it is put back if it was on the screen, otherwise the screen goes black (measured in CI: mpv itself draws black for a shader that does not compile, and keeps doing so until the shader is removed), the generated file is removed, and the message says which line of the ISF file the GPU complained about. `GET /api/shaders` keeps the last refusal in `error`. A shader that was taken once is not waited for again, so slider changes and Vibes rounds are quick. Every generated text carries its own name in a comment: mpv remembers a text it could not compile and says nothing the second time (seen in CI, where a second try of the same broken shader passed as taken), so no two texts may be alike.

With a player that has no GPU output (the tests' `--vo=null`), nothing can be checked and `playing.checked` is null.

**What this cannot catch:** a shader that compiles but is so heavy that the GPU stalls. Only full-access devices can upload; watch a new shader before putting it in Vibes.

## Safety

- An uploaded file is at most 32 KB of text, its JSON header at most 8 KB, with at most 24 inputs; at most 64 uploads.
- Input names are checked with a full match (a letter, then letters, digits or `_`, 32 at most) and may not be a word of the shader language or a name the player uses; numbers must be finite (JSON's `NaN` and `Infinity` are refused); labels and descriptions are cut and stripped of control characters and only ever shown as text.
- The text `//!` is refused anywhere in the file, comments and JSON included: mpv reads such lines as commands wherever they stand, and a file could otherwise add its own hook on the OUTPUT stage (over the mapping), a texture or a second pass.
- The code is checked as the compiler will read it: comments are taken out first (and are not passed on), and a backslash is refused, so nothing can hide behind `/**/` or a continued line. Preprocessor lines other than `#define`, `#undef` and the `#if` family are refused (no `#include`, `#version`, `#extension`, `#pragma`, `#line`), and `#define` or `#undef` of a name that belongs to the shader language, to ISF or to the player. `uniform`, `varying`, `layout` and `attribute` are refused anywhere, and `in` or `out` as a declaration of their own (they stay allowed for function parameters). The player's own names (`hook`, `HOOKED...`, `pvj_...`, mpv's `texture0` and its companions) are refused in any letter case. The code must be plain ASCII and have exactly one `void main()`.
- The JSON header may not name a key twice (the last one would win over the one that was checked), and `NaN` and `Infinity` are refused. A file that trips the checks in a way nobody foresaw is listed as broken and skipped; it cannot take the list or Vibes down.
- File names: letters, digits, spaces and `. _ -`, ending in `.fs`, checked with a full match and the same rules as media names; a bundled name cannot be taken. Uploads are stored in `<state>/shaders` (beside the settings file), never through a link, written to a temporary name first and never over an existing file unless replacement is asked for. They are translated before they are stored; what is refused is never written.
- The generated GLSL is written to the player's runtime folder (`/run/pvj`) as `shader-<pid>-<n>.glsl`, mode 0640, under a fresh name each time, and removed when replaced, like the mapper's files; the player cannot read the state folder.
- What remains: the shader code itself is GLSL from a full-access device and runs on the GPU. It cannot read files or reach the network, but it can be slow. A remote-support login that the studio gave the full role may upload shaders too, like any full-access device, for as long as the session lasts.

## API

- `GET /api/shaders` (view): `{"enabled", "shaders": [{"id", "name", "source": "bundled|uploaded", "description", "credit", "cost", "vibes", "inputs": [{"name", "type", "label", "default", "min"?, "max"?, "values"?}], "error"}], "playing": {"id", "name", "values", "checked", "pass_ms"?} | null, "error": {"id", "message", "at"} | null, "vibes": {"running", "current", "next_in", "rounds", "last"}, "config": {"dwell", "vary", "height"}, "render": {"width", "height", "fps", "heights"}, "limits"}`. `pass_ms` is mpv's own timing of the shader pass, when the GPU reports one.
- `POST /api/shaders/play` (live): `{"id": "nxlx-tide.fs", "values"?: {"speed": 1.5}}`. Shows one shader. 422 with the reason if the file cannot be translated or the player refuses it.
- `POST /api/vibes` (live): `{"on": true}` starts (it answers at once; the first shader is on within a second), `{"on": false}` stops and clears the screen if Vibes still has it, `{"next": true}` goes to the next shader, `{"dwell": seconds}` sets how long each shader stays (10 to 3600; saved only when it changes).
- `POST /api/shaders` (full), one action: `{"action": "upload", "name": "x.fs", "source": "<the file's text>", "replace"?: bool}`, `{"action": "delete", "id"}`, `{"action": "vibes", "id", "on": bool}`, `{"action": "config", "dwell"?, "vary"?, "height"?}`.

Uploads go through the JSON API (auth, the request header and the Origin check as for every other change), not through the raw media upload path: that path streams gigabytes into the media folder and accepts media extensions only, and a shader is a few kilobytes of text that has to be parsed before it is stored.

Settings live under `"shaders"` in the settings file once something is changed; there is no settings migration (a missing or damaged entry means the defaults).

## Not verified

- **Never seen on a display, by anyone.** Checked only through screenshots of a 320 x 180 window on a software GPU in CI.
- **No speed measured on any board.** Not run on a Raspberry Pi, a Pi's GPU driver, or any real GPU. The 8-bit buffers and the default of 720 lines are choices made from the mapper's measurements on a Pi 4, not from measurements of this module.
- mpv 0.37 only (Ubuntu's, in CI). Not run on mpv 0.35 (Raspberry Pi OS Bookworm) or 0.40 (Trixie, on the test Pi).
- **Sync and the video wall.** A sync server that plays a shader or Vibes sends its clients "stop" (a shader is not a file they could play), so the clients go black; shaders in step on several boxes are not built. The wall crop over the carrier picture has not been tried.
- TIME over hours; the 16-bit question above; a player crash and restart during Vibes on a real box; Vibes from autostart across a real reboot; OSC from a real controller.
- Uploaded ISF files from other programs: the translator was tested with files written for the tests, not with a collection of real-world ISF shaders.
