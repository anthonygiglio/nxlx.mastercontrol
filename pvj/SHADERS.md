<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Shaders and Vibes (beta)

Moving pictures drawn by the box's GPU instead of played from a file, and **Vibes**: one tap that plays them endlessly for ambience.

Switch it on under System > Shaders and Vibes (beta, off by default). Switching it off while a shader is on the screen asks first, because it stops at once. It is offered on a Raspberry Pi 4, a Pi 5 and x86; not on a Pi 3.

**Read this first: what is measured and what is not.** The ten bundled shaders were run on a real Raspberry Pi 4 on 2026-10-03 (mpv 0.40, a 2560 x 1440 screen at 75 Hz): all ten compile and draw correctly, and their speed is in the table below. Everything built since (live values, the speed control, presets, rotation sets, the guard, the carrier that counts its own frames) has run only in automated tests: against a real mpv with Mesa's software GPU in CI, where the picture is checked through screenshots, and against fakes. A Pi 5 and x86 are not measured at all.

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

- Picks from the shaders of the **active set** (or the set a start names; see [Performing](#performing)), in a shuffled order, or in the set's own order if the set says so; shuffled, every shader is shown once before any comes again, and never the same one twice in a row. A shader that is broken, that this box's GPU refused, or that the guard found too heavy here is passed over.
- Each stays for the set's **dwell time** (default 180 seconds, 10 to 3600).
- Between shaders: a **dip to black** over the Mix screen's transition duration (down for half of it, up for half). During a Blackout, and after you have faded the picture out, nothing fades and the screen stays dark: the shaders go on changing unseen until you show the picture again.
- **Variation each round** (can be switched off): every number input gets a new value between its MIN and MAX, pulled towards the default, or towards the preset's value (at most 60 percent of the way to either end, since the author chose the default as the good place); the palette is turned by up to 120 degrees either way around the grey axis (black, white and greys stay as they are); and the shader's time starts somewhere else. The values come from a random generator seeded when the service starts.
- **It never fights the operator** (the autostart rule, D18). It ends when anything else is played (a pad, a clip, a stream, a live input, the test pattern, a schedule entry, a sync server's clip), when Stop is pressed, when one shader is chosen by hand, when the module is switched off, and when the player is restarted. It does not come back by itself; after a player restart it starts again only if Autostart is set to Vibes. The reason it ended is shown on the card. A clip tapped in the middle of a change plays, also with the Mix transition on Dip to black (the first version lost such a clip; see the review notes in the project log).
- A shader the GPU refuses is left out and the next one is shown; the refusal is remembered until the file changes, so no later run tries it again (a Play by hand does, and a success clears it). If every shader is refused, Vibes ends and the screen is cleared.
- **The dip takes the Mix duration.** Its steps are paced against the clock; the first version slept a full step and then paid the round trip to the player on top, and a 1.0 second dip took 1.15 to 1.49 seconds on the Pi 4. The way up gets what the way down and the change left of the duration, and at least a quarter of it.

## Performing

For the shader that is on the screen (presenters and above; [the API](#api) is below, the page that uses it is in [docs/MANUAL.md](../docs/MANUAL.md)):

- **Every input can be changed while it plays**: numbers, switches (`bool`), choices (`long` with `VALUES` and `LABELS`), colours, points, and events (a button that is true for a quarter of a second). `GET /api/shaders` describes each input with its type, label, range, default, choice labels and the value it has now.
- **Three controls every shader has**: **speed** (0 to 4; 1 is the shader's own pace, 0 freezes it), **hue** (a palette shift of -180 to 180 degrees around the grey axis) and **brightness** (0 to 2; 1 leaves it alone). They are separate from the shader's own inputs: several bundled shaders have an input that is also called `speed`.
- **A change never restarts the shader, never resets its TIME and never ends Vibes.** A change of the speed goes on from the TIME the shader had reached. (A Play of the shader that is already on also goes on from its TIME, with the palette turn and the controls it has; like every Play by hand it ends Vibes. The page's sliders send a Play today; `/api/shaders/values` is the call made for them.)
- **Presets**: the values and controls that are on the screen, kept under a name per shader (up to 16 for one shader). Applying one sets every input. The preset called **default** is what a plain Play and Vibes use for that shader.
- **Rotation sets** for Vibes: named lists of shaders ("Ambient", "Show"), each with its own dwell time, variation on or off, and order (shuffled, or as listed). An entry may name the preset to use. One set is active: Vibes, the schedule, autostart, OSC, MIDI and DMX use it unless a start names another. Until someone edits a set there is one, **Ambient**, and it is not written to the settings: the project's own shaders without the two heavy ones (nxlx-nebula and nxlx-drift). An uploaded shader starts **in the library only**; put it into a set with its switch.
- **The shader before and the next one** work with and without Vibes: while Vibes runs they step the rotation, otherwise they put on the neighbour of the shader on screen in the active set.
- **From a MIDI controller** ([MIDI.md](MIDI.md)): shader control 1 to 8, shader speed, previous and next shader, preset 1 to 8.

### What a change costs

mpv's GPU output (`--vo=gpu`) cannot hand a new number to a user shader that is running. Its user shaders get four values from the player (`frame`, `random`, sizes and an offset) and nothing else; `//!PARAM` and the option `glsl-shader-opts` belong to mpv's other output, gpu-next. This was read in mpv's source at 0.37 and 0.40 (`video/out/gpu/user_shaders.c` knows no `PARAM`; `glsl-shader-opts` is declared and never read by this output), and CI checks on its mpv 0.37 that a shader with a `//!PARAM` line is refused. The output is not switched: the mapping, the brightness order and every measurement here are on `--vo=gpu`.

So a change is a new shader text that the GPU compiles. It is made cheap and seamless instead:

- A request only checks the values and notes them down; it answers at once. One worker applies the newest values at most **five times a second**, so a dragged slider or a MIDI knob at 50 messages a second is at most five compiles a second, and the last value lands at the latest 0.2 seconds after it came, plus the compile.
- Only the shader file is exchanged: the carrier keeps playing, nothing is reloaded, the opacity is not touched. mpv draws the old shader until the new one is compiled.
- On the Pi 4 the first version answered a slider change in 0.10 to 0.15 seconds, which was a full Play each time (measured 2026-10-03). The exchange alone is fewer round trips to the player; it has not been timed on the Pi. In CI (mpv 0.37, Mesa's software GPU, a 320 x 180 window) writing the new text and exchanging it took about 2 ms for a number, a colour or a point, and about 0.58 s the first time a switch or a choice gave the text a new shape, almost all of it the wait for the player's verdict; `tests/test_shaderlive_gpu.py` prints both. Neither number says anything about a Pi.
- A plain number, colour or point cannot change what the compiler makes of the code, so the GPU is not asked again. A switch, a choice or an event can, so the first time a shader is shown in that shape the panel waits for the player's verdict as it does for a new shader; if the GPU refuses it, the text before it is put back and the values stay as they were.
- **Not verified on hardware:** that the picture does not hitch for a frame or two while a new text compiles on a Pi 4. In CI no screenshot taken during 100 changes was dark.

### The guard for a weak GPU

While a shader is on, the frames the player drops are counted (and on a Pi the kernel's GPU figures in `/sys/devices/platform/v3dbus/*/gpu_stats` are read and shown, when they are there; their form is not verified against a real Pi by this code). More than **2 dropped frames a second for 6 seconds in a row** is "too heavy on this box at this drawing size". The first 3 seconds after a shader comes on or is changed are not counted.

- **In Vibes** such a shader is recorded (with the rate and the drawing height), the rotation goes on to the next one at once, and no rotation shows it again until someone puts it back (its switch, or `{"action": "heavy", "id", "on": false}`). The note is kept in the settings.
- **A shader chosen by hand** is only reported: `playing.load` is `ok`, `tight` (half a frame a second or more) or `heavy`, with `playing.drops_per_second`.
- It can be switched off (`{"action": "config", "guard": false}`).
- **Variation does not make a shader heavier.** An input named in the head of a loop or in a condition can change how much the GPU has to do; such an input is marked `"varies": false` and Vibes leaves it at its value. A shader seen dropping frames in a run is shown without the palette turn for the rest of that run: the turn is one more multiplication for every pixel, and on the Pi 4 variation pushed nxlx-pulse from 0 to about 1 dropped frame a second at 720 lines. That the turn was the cause is likely, not proven.
- What it cannot see: frames that are not drawn while a snapshot is taken (0.3 to 0.4 seconds each on the Pi 4); the player does not count those.

## The bundled shaders

Ten original generator shaders, written for this project (Apache-2.0, in `pvj/shaders.d`). All are slow and quiet, use short fixed loops (at most 5 rounds) and no textures.

**Measured on a Raspberry Pi 4** (2026-10-03, mpv 0.40, 2560 x 1440 at 75 Hz, the first version of this module). mpv made a desktop OpenGL 3.1 context (GLSL 1.40, V3D, Mesa 26), not OpenGL ES. The GPU is the only limit (the CPU was at 2 to 4 percent). Every frame pays two fixed passes whatever the shader: scaling to the screen (12.9 ms at 1440p) and a remainder pass (1.0, 2.3, 4.0 and 8.9 ms at 360, 540, 720 and 1080 lines), which leaves about 16 ms for the shader at 720 lines in a frame of 33.3 ms.

| Shader | Picture | Class | Pass at 720 lines | Dropped frames a second at 360 / 540 / 720 lines |
| --- | --- | --- | --- | --- |
| nxlx-silk | fine flowing lines | light | 7.5 to 11.2 ms (the six light ones; not noted one by one) | - / - / 0 |
| nxlx-lattice | two grids turning against each other (moire) | light | as above | - / - / 0 |
| nxlx-horizon | a dusk sky with a slow sun and haze | light | as above | - / - / 0 |
| nxlx-prism | rings of colour around the centre | light | as above | - / - / 0 |
| nxlx-ember | warm blobs that merge and part | light | as above | - / - / 0 |
| nxlx-pulse | ripples from three wandering points | light, at the edge | as above | - / - / 0 (about 1 with Vibes variation) |
| nxlx-tide | layers of slow waves | medium | 15.1 ms | - / 0 / 3.3 |
| nxlx-aurora | curtains of light over a dark sky | medium | 20.6 ms | - / 0 / 6.7 |
| nxlx-drift | soft clouds of colour | heavy | 30 ms (before its look was changed) | 0 / 2.1 / 10.1 |
| nxlx-nebula | clouds bent by more clouds | heavy | 32.7 ms | 0 / 3.9 / 11 |

A dash is "not measured". At 1080 lines even nxlx-silk and nxlx-lattice dropped 7 to 8 frames a second. First play of a shader took 0.62 to 0.78 seconds including the GPU check; after 7 hours 43 minutes TIME was still right.

What follows from it:

- **The drawing size depends on the board**: 540 lines by default on a Pi 4 and on a board that is unknown, and 1080 is not offered on a Pi 4. A Pi 5 and x86 start at 720 and keep 1080; **neither is measured**, so those are guesses. mpv scales the result to the screen.
- **light** kept up at 720 lines, **medium** at 540, **heavy** only at 360. `GET /api/shaders` gives each shader's class as `weight` and these numbers as `measured`; each file's own `COST` note starts with low, medium or high to match.
- **The default rotation leaves out the two heavy ones.** nxlx-aurora and nxlx-tide are in: at the Pi 4's default of 540 lines they dropped nothing.
- **nxlx-drift looked dull** on the screen (grey, low contrast, blocky). Its two palettes are now laid side by side instead of blended half and half, the cloud is stretched to the full range, and each octave is turned as well as doubled so the grid does not show. The work per pixel was meant to stay the same; **it has not been measured or looked at again on the Pi**, and its 30 ms is from before.

## ISF: what is supported

An ISF file is a GLSL fragment shader with a JSON comment at the top (see isf.video). Only **generators** are supported: shaders that draw from nothing.

| ISF | Here |
| --- | --- |
| `INPUTS` of type `float`, `bool`, `long`, `color`, `point2D`, `event` | written into the shader as constants: the value that is set, else the `DEFAULT`. All can be changed while the shader plays. A `long` takes one of its `VALUES` (its `LABELS` are shown; missing labels are the numbers) or, without `VALUES`, a whole number between `MIN` and `MAX`; a `point2D` is kept inside `MIN` and `MAX` if it has them; an `event` is true for a quarter of a second when pressed. Vibes varies `float` inputs only. |
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

TIME is `offset + speed x (carrier frames since the anchor) / 30`.

- **The carrier counts its own frames.** Each frame of the carrier is painted with its own number in its three colour bytes (a `geq` filter in the same `av://lavfi:` address; nobody sees it, the shader draws in its place), and the shader reads the number from the picture it is hooked on. The player reports the same number as its playing position (`time-pos` x 30). So the panel knows what TIME the shader shows, which is what lets the speed change without a jump: the anchor moves to the frame that plays now and the offset to the TIME reached. The first version counted mpv's own `frame` number instead. That number is `frames_uploaded` in mpv's source: every picture the player has uploaded since it started, clips included; nothing outside the shader can read it, so the speed could not have been changed without a jump of TIME.
- **Verified in CI** on OpenGL ES and desktop OpenGL (and desktop OpenGL 3.1 with GLSL 1.40, what mpv makes on a Pi 4), on Mesa's software GPU: TIME starts at the offset, runs at the wall clock's pace, goes on through a change of a value, runs three times as fast at speed 3 with no jump at the change, and stands still at speed 0. **Not run on a Pi**: the Pi measurements above were made with the first version's clock. If a GPU does not read the carrier's colour correctly, `{"action": "config", "clock": "frame"}` goes back to the first version's clock (the speed control then makes TIME jump).
- While the GPU keeps up, TIME runs at real time. **When the GPU is too slow** the player drops frames; with the carrier's clock TIME stays at real time and the picture moves in larger steps (with the first version's clock TIME ran slower instead).
- The Mix screen's Speed scales it (the carrier plays faster or slower); Freeze stops it.
- A redraw does not add a frame (the first version's notes said it did; mpv's source says `frame` counts uploads, not redraws).
- **Precision.** The number is put together from small whole numbers (hi x 512 + lo) in high precision. Seen in CI on OpenGL ES: a shader that computed `mod(float(frame), 1048576.0)` drew a black picture without any error, most likely because of medium precision. The three bytes start again after 16,777,216 frames (6.4 days); the frames since the anchor are counted across that, so TIME only jumps if one shader plays for 6.4 days without any change. With the first version's clock TIME started again after 38.8 hours.
- **A Pi does not use the stricter OpenGL ES language.** mpv made a desktop OpenGL 3.1 context with GLSL 1.40 on the Pi 4. CI runs every shader test on OpenGL ES and on desktop OpenGL, and the live tests a third time with Mesa told to be a 3.1 driver; none of them is the Pi's own driver.

### A shader the player refuses

A bad shader must never leave a broken show. Before a shader the GPU has not yet taken is put on, the panel opens a second connection to the player that receives its error log (`request_log_messages`), then waits until mpv reports a drawn pass with that shader's name (`vo-passes`) or an error, at most 4 seconds. Only a pass that mpv timed counts as drawn: a refused shader is listed too, with a time of 0. On an error the shader before it is put back if it was on the screen, otherwise the screen goes black (measured in CI: mpv itself draws black for a shader that does not compile, and keeps doing so until the shader is removed; where there is no shader to go back to, a shader that draws black is put on, because the carrier's own colour is its frame number), the generated file is removed, and the message says which line of the ISF file the GPU complained about. `GET /api/shaders` keeps the last refusal in `error`. A shader that was taken once is not waited for again, so slider changes and Vibes rounds are quick. Every generated text carries its own name in a comment: mpv remembers a text it could not compile and says nothing the second time (seen in CI, where a second try of the same broken shader passed as taken), so no two texts may be alike.

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

`GET /api/shaders` (view):

```
{"enabled": true,
 "shaders": [{"id": "nxlx-tide.fs", "name": "nxlx-tide", "source": "bundled" | "uploaded", "description", "credit",
              "cost": "medium: 5 layers, ...",            the file's own note, as before
              "weight": "light" | "medium" | "heavy" | "",   measured for the bundled ones, else from the note's first word
              "measured": {"board": "pi4", "lines": 720, "pass_ms": 15.1 | null, "pass_ms_range"?: [7.5, 11.2],
                           "drops_per_second": {"540": 0, "720": 3.3}, "stale": false} | null,
              "vibes": true,                                in the active set
              "heavy": {"at", "drops", "height"} | null,    what the guard noted on this box
              "refused": "line 6: ..." | null,              what this box's GPU said about this file
              "presets": ["default", "Bright"],
              "inputs": [{"name", "type", "label", "default", "value", "varies",
                          "min"?, "max"?, "values"?, "labels"?}],
              "error": null}],
 "playing": {"id", "name", "values": {input: value, every input but events}, "controls": {"speed", "hue", "brightness"},
             "preset": name | null, "pending": bool, "checked": true | null, "pass_ms"?,
             "load"?: "ok" | "tight" | "heavy" | null, "drops_per_second"?} | null,
 "error": {"id", "message", "at"} | null,
 "vibes": {"running", "current", "next_in", "rounds", "last", "set"?: {"id", "name"}},
 "config": {"dwell", "vary", "height", "guard", "clock"},      dwell and vary are the active set's
 "render": {"width", "height", "fps", "heights": [360, 540, 720], "default": 540, "measured": true, "board": "pi4"},
 "sets": [{"id", "name", "shaders": [{"id", "preset"?}], "dwell", "vary", "order": "shuffle" | "listed"}],
 "active": "00000000",
 "controls": {"speed": {"min": 0, "max": 4, "default": 1}, "hue": {"min": -180, "max": 180, "default": 0}, "brightness": {"min": 0, "max": 2, "default": 1}},
 "gpu": {"busy_percent", "render_jobs_per_second"} | null,
 "limits": {"bytes", "inputs", "uploads", "dwell", "presets", "sets", "set_shaders", "name", "controls"}}
```

An input's `value` is what is on the screen for the playing shader, and what a plain Play would use for the others. By type: `float` has `min`, `max`; `long` has `values` and `labels`, or `min` and `max`; `point2D` may have `min` and `max` as `[x, y]`; `color` is `[r, g, b, a]`, each 0 to 1; `bool` and `event` are true or false. `pass_ms` is mpv's own timing of the playing shader's pass, when the GPU reports one. `pending` is true while a change waits for the worker.

Live access (presenters):

- `POST /api/shaders/play`: `{"id": "nxlx-tide.fs", "values"?: {...}, "controls"?: {...}, "preset"?: "Bright"}`. Shows one shader and ends Vibes. Without values or a preset it uses the preset called default, else the file's defaults; values given go on top. Answers with the whole state. 422 with the reason if the file cannot be translated or the player refuses it.
- `POST /api/shaders/values`: `{"values"?: {input: value}, "controls"?: {"speed"?, "hue"?, "brightness"?}, "id"?: the shader it is meant for}`. Changes the shader on screen; only what is named changes. Answers at once with `{"ok", "id", "values", "controls"}` as they will be; the GPU gets it within 0.2 seconds. 400 for a value of the wrong kind or an unknown name, 409 if no shader is on or `id` is not the one on screen. From a controller: `{"control": 1 to 8, "level": 0 to 127}` or `{"control": n, "press": true}` for the shader's n-th input (numbers, switches, choices and events, in the file's order).
- `POST /api/shaders/step`: `{"dir": 1 | -1}`. The next shader of the active set, or the one before. Answers at once.
- `POST /api/shaders/preset`: `{"name": "Bright"}` or `{"index": 1 to 16}`, with `"id"` for a shader that is not on (it is then put on). Answers at once.
- `POST /api/vibes`: `{"on": true, "set"?: id or name}` starts (it answers at once; the first shader is on within a second), `{"on": false}` stops and clears the screen if Vibes still has it, `{"next": true}`, `{"previous": true}`, `{"dwell": seconds}` (10 to 3600; of the set that runs).

Full access:

- `POST /api/shaders/presets`: `{"action": "save", "name", "id"?}` keeps what is on the screen (over a preset of that name, in any letter case), `{"action": "rename", "id", "name", "to"}`, `{"action": "delete", "id", "name"}`.
- `POST /api/shaders`, one action: `{"action": "upload", "name": "x.fs", "source": "<the file's text>", "replace"?: bool}`, `{"action": "delete", "id"}` (also removes its presets and its place in the sets), `{"action": "vibes", "id", "on": bool}` (in or out of the active set), `{"action": "config", "dwell"?, "vary"?, "height"?, "guard"?, "clock"?}`, `{"action": "set", "op": "add", "name", "shaders"?: ["a.fs", {"id": "b.fs", "preset": "x"}], "dwell"?, "vary"?, "order"?}`, `{"action": "set", "op": "update", "id", ...}`, `{"action": "set", "op": "delete", "id"}` (never the last one), `{"action": "set", "op": "activate", "id"}`, `{"action": "heavy", "id", "on": bool}`.

Uploads go through the JSON API (auth, the request header and the Origin check as for every other change), not through the raw media upload path: that path streams gigabytes into the media folder and accepts media extensions only, and a shader is a few kilobytes of text that has to be parsed before it is stored.

Settings live under `"shaders"` in the settings file once something is changed: `dwell`, `vary`, `height`, `disabled` from the first version, and `presets`, `sets`, `active`, `heavy`, `guard`, `clock` and `v` (2 once this version has saved the section). **There is no settings migration and the schema is unchanged** (13): a missing or damaged entry means the defaults. A box whose shader settings the first version saved keeps its rotation: on the first save its list (everything that was not switched off, uploads included, without the two heavy shaders) is written as the set Ambient. A box where shaders were uploaded but no shader setting was ever saved has no such section, and is treated as new: its uploads are in the library and have to be put into the rotation once. Settings export and import check every key ([boxcare.py](boxcare.py)); a factory reset removes the section.

## Not verified

- **On a display, only the first version**, on one Pi 4 with one screen. Not seen or run on hardware: live values of each type, the speed control and the carrier that counts its frames, presets, rotation sets, the guard, the MIDI actions, nxlx-drift's new look, and a change while the room watches (does the picture hitch).
- **No Pi 5 and no x86 is measured.** Their default of 720 lines and the offer of 1080 are guesses.
- CI runs mpv 0.37 (Ubuntu's). The Pi 4 has 0.40; mpv 0.35 (Raspberry Pi OS Bookworm) has not been run.
- **Sync and the video wall.** A sync server that plays a shader or Vibes sends its clients "stop" (a shader is not a file they could play), so the clients go black; shaders in step on several boxes are not built. The wall crop over the carrier picture has not been tried.
- A player crash and restart during Vibes on a real box; Vibes from autostart across a real reboot; OSC and MIDI from a real controller.
- The guard's thresholds (2 frames a second for 6 seconds) are chosen from the Pi 4 table, not tuned on a running box.
- A GPU refusal is remembered until the file changes or the service restarts (it is not written to the settings; after a restart the shader is tried once more).
- Uploaded ISF files from other programs: see the survey that comes with the ISF pack.
