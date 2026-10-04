<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Shaders and Vibes (beta)

Moving pictures drawn by the box's GPU instead of played from a file, and **Vibes**: one tap that plays them endlessly for ambience.

Switch it on under System > Vibes (beta, off by default). Switching it off while a shader is on the screen asks first, because it stops at once. It is offered on a Raspberry Pi 4, a Pi 5 and x86; not on a Pi 3.

**Read this first: nothing here has been seen on a display, and no speed has been measured on any board.** It has run only in automated tests: against a real mpv with Mesa's software GPU on a virtual display in CI (the picture is checked through screenshots), and against fakes. How many frames a second each shader reaches on a Pi 4, a Pi 5 or a PC is unknown. The cost notes below are counts of work per pixel, not measurements.

## Using it

- **Live screen: the Vibes button** (presenter, that is live access, and above). It starts the rotation; while it is on, the button stops it. "Now playing" shows `Vibes: <shader>`.
- **Mix screen: the Shaders and Vibes card.** The list of shaders with **Play** (live access): one shader, until something else is played. The shader on screen shows its number inputs as sliders; a change is sent when you let go. Full-access devices also see: **In Vibes / Not in Vibes** per shader, **Delete** for uploads, the dwell time, variation on or off, the drawing size, and **Upload an ISF shader**.
- **Autostart**: mode **Vibes** (System > At power-up). **Schedule**: the action **Start Vibes**. **OSC**: `/pvj/vibes` (press) starts it, `/pvj/vibes/next` goes to the next shader, the usual `/pvj/stop` ends it.
- **MIDI**: the actions **Vibes on / off**, **Vibes: next shader** and **Vibes: time each shader stays** (a knob or fader), assigned with Learn under System > MIDI controller ([MIDI.md](MIDI.md)). **DMX**: an optional ninth channel: 50 to 99 stop, 100 to 149 start, 150 to 199 next ([DMX.md](DMX.md)). Both act as a presenter through the same call as the panel, under the same limit of 50 commands a second, and do nothing while this module is off (the log says so once). Neither has been tried with a real controller or console.
- Opacity, Fade in and out, Blackout, the overlay picture, mirror, rotate and the projection mapping apply to a shader as to a clip. Speed changes how fast it moves; Freeze stops it.

### Vibes

- Picks from the shaders that are **In Vibes** (the project's ten and uploads unless switched out; the ISF-Files pack only when switched in), in a shuffled order; every shader is shown once before any comes again, and never the same one twice in a row.
- Each stays for the **dwell time** (default 180 seconds, 10 to 3600).
- Between shaders: a **dip to black** over the Mix screen's transition duration (down for half of it, up for half). During a Blackout, and after you have faded the picture out, nothing fades and the screen stays dark: the shaders go on changing unseen until you show the picture again.
- **Variation each round** (can be switched off): every number input gets a new value between its MIN and MAX, pulled towards the default (at most 60 percent of the way to either end, since the author chose the default as the good place); the palette is turned by up to 120 degrees either way around the grey axis (black, white and greys stay as they are); and the shader's time starts somewhere else. The values come from a random generator seeded when the service starts.
- **It never fights the operator** (the autostart rule, D18). It ends when anything else is played (a pad, a clip, a stream, a live input, the test pattern, a schedule entry, a sync server's clip), when Stop is pressed, when one shader is chosen by hand, when the module is switched off, and when the player is restarted. It does not come back by itself; after a player restart it starts again only if Autostart is set to Vibes. The reason it ended is shown on the card. A clip tapped in the middle of a change plays, also with the Mix transition on Dip to black (the first version lost such a clip; see the review notes in the project log).
- A shader the GPU refuses is left out for the rest of that run and the next one is shown; if every shader is refused, Vibes ends and the screen is cleared.

## The bundled shaders

Ten original generator shaders, written for this project (Apache-2.0, in `pvj/shaders.d`). All are slow and quiet, use short fixed loops (at most 5 rounds) and no textures. **Cost is a rough count of work per pixel, not a measurement.**

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

## The ISF-Files pack (third-party shaders)

Eleven generators from Vidvox's public ISF collection (https://github.com/Vidvox/ISF-Files, commit `395072d4`, MIT), in `pvj/shaders.d/isf-files`. **They are not this project's work**: each is the upstream file byte for byte under a name without spaces, keeps its own `CREDIT` (shown in the list), and the licence, the notices and the full file list are in [THIRD_PARTY_LICENSES.md](../THIRD_PARTY_LICENSES.md). In `GET /api/shaders` they carry `"pack": "isf-files"`; the project's own ten carry `"pack": "nxlx"` and uploads `"pack": "uploads"`, so a page can group and filter by pack.

**They are in the library, not in the Vibes rotation.** A third-party shader joins Vibes only when a full-access device switches it to **In Vibes** (the same switch as for every other shader; `{"action": "vibes", "id": "isf-spiral.fs", "on": true}`). The reason: seven of the eleven are still pictures, made as building blocks for a VJ and not as ambience, and none has been timed on a board.

| Shader | Upstream name | Picture | Moves by itself | Rough cost per pixel (a count, not a measurement) |
| --- | --- | --- | --- | --- |
| isf-color-bars | Color Bars | broadcast colour bars | no | low: no loop |
| isf-color-test-grid | Color Test Grid | a grid of test colours | no | low: no loop |
| isf-corner-colors | Corner Colors | a blend between four corner colours | no | low: no loop |
| isf-linear-gradient | Linear Gradient | a two-colour gradient at an angle | no | low: no loop |
| isf-radial-gradient | Radial Gradient | a two-colour gradient from a centre | no | low: no loop |
| isf-random-lines | Random Lines | coloured lines that wobble | yes | **heavy: a loop of 60 rounds, a line distance in each** |
| isf-ridgelines | Ridgelines | sharp ridges of noise, like terrain | yes | medium: up to 6 octaves of simplex noise |
| isf-simplex-noise | Simplex Noise | soft clouds of noise | yes | medium: up to 6 octaves of simplex noise |
| isf-sine-warp-gradient | Sine Warp Gradient | a gradient bent by sines | no | low: no loop |
| isf-spiral | Spiral | a two-colour spiral | no | low: no loop |
| isf-worley-cells | Worley Cells | cells like a honeycomb or cracked mud | yes | low to medium: 9 neighbour cells |

What is known about speed, and what is not. On the owner's Raspberry Pi 4 (mpv 0.40, desktop OpenGL 3.1 on V3D, measured for the project's own shaders in another branch, not here) about 16 ms are available for the shader pass at 720 lines: passes of 7.5 to 11 ms held 30 frames a second, 15 ms dropped 3 a second, 20 ms dropped 7. **None of the eleven has been timed on any board.** By that yardstick anything with more than a few dozen rounds per pixel is heavy on a Pi 4: `isf-random-lines` is, and should stay out of Vibes there until it is timed; `isf-ridgelines` and `isf-simplex-noise` have an `octaves` input, and fewer octaves cost less. Play one and read `playing.pass_ms` in `GET /api/shaders` (mpv's own timing of the pass) before putting it into Vibes.

How these eleven were chosen: see "The survey of ISF-Files" below.

### Adding more ISF files to a box

Mix > Shaders and Vibes > **Upload an ISF shader** (full access), one `.fs` file at a time, at most 32 KB and 24 inputs; an upload joins Vibes at once and can be switched out. Generators from ISF-Files, from https://editor.isf.video or from VDMX go in this way as long as they draw from nothing in one pass (see the table below for what is refused, and why). A file that is refused says why and is not stored. Take care of the licence yourself: many ISF files on the web are ports of shaders published under terms that forbid commercial use.

To see beforehand what a whole folder of ISF files would do: `python3 tools/isf-survey.py /path/to/folder` (a developer's tool, in the repository, not on the box).

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
| a variable called `out_color`, an input called `color` (common in real ISF files; the player owns both words) | renamed inside the generated shader (`pvj_u_out_color`, `pvj_in_color`); the panel and the API keep the file's own input name |
| comments | cut out before anything is checked and never passed on, so they may hold any text (dashes, arrows, accents) |

Refused, with a message that names the reason:

- more than one pass, a pass with a `TARGET`, its own size or `PERSISTENT` (`PASSES`), and `PERSISTENT_BUFFERS`: there is no picture kept between frames;
- `image` and `cube` inputs and every `IMG_...` call (`IMG_PIXEL`, `IMG_NORM_PIXEL`, `IMG_THIS_PIXEL`, `IMG_SIZE`): filters that need a picture are not supported. With no picture inputs there is nothing sensible for the `IMG_` calls to read, so none of them is mapped;
- `audio` and `audioFFT` inputs;
- `IMPORTED` pictures;
- vertex shaders (`.vs` files) are not read at all.

## The survey of ISF-Files (2026-10-04)

All 327 `.fs` files of ISF-Files at commit `395072d4` were put through this translator with `tools/isf-survey.py`. What a file needs was read from its header and code independently, so a file that needs two things is counted under both.

| | Files |
| --- | --- |
| Translate (generators in one pass) | **36** (17 before the three translator fixes of this change) |
| Need the playing picture as input (filters, `inputImage`) | 210, of which 121 need nothing else |
| Need two pictures and a progress value (transitions, `startImage` and `endImage`) | 68, none needs anything else |
| Need more than one pass, or a pass with its own target or size | 72, always together with something else |
| Need a picture kept between frames (persistent buffers) | 49, always with several passes |
| Have a vertex shader of their own (`.vs`) | 38 |
| Need sound (waveform or FFT) | 7, of which 3 need nothing else |
| Load pictures from other files (`IMPORTED`) | 4, of which 1 needs nothing else |
| Over the upload limits (more than 24 inputs) | 2 |
| Would still trip a safety check with all of that in place (a header key twice, `texcoord0` and the like, an input called `length`) | 8 |

Translating is not drawing. Of the 36, **nine were not tried** because their own comments point to code from elsewhere under terms that could not be established (listed in THIRD_PARTY_LICENSES.md). The other 27 ran in CI on a real mpv with Mesa's software GPU, once on OpenGL ES and once on desktop OpenGL, with their defaults and with varied inputs, and were held to the rule for the project's own ten (the player takes it, draws a frame, more than 40 colours in the picture, not dark):

- **11 passed** on both and are the pack.
- **11 drew, but a flat picture by design** (1 to 39 colours on at least one of the four runs): Checkerboard, Stripes, Poly Star, Solid Color, Graph Paper, Grid Warp, Color Schemes, Lines, Noise, Random Checkerboard, Random Stripes. These work; they are shapes and patterns of a few colours, which the rule for ambience pictures does not let through. They can be uploaded, and a pack of "shapes" with a rule of its own would be a small follow-up if the owner wants them bundled.
- **5 failed**: Basic Shape and Random Shape (they define a function `sign`, which OpenGL ES refuses as a built-in name; on desktop OpenGL Random Shape drew black and Basic Shape two colours), VU Meter (defines `round`, refused on OpenGL ES), Bordered Box (compares a float with a whole number, refused on OpenGL ES; black on desktop OpenGL with its default inputs), Line Group (returns a whole number from a float function, refused on OpenGL ES). On desktop OpenGL all five were taken by the player, and Line Group drew a varied picture there. The owner's Pi 4 uses desktop OpenGL, so Line Group may well be usable there by upload; none is bundled because a bundled file must work on both.

What the crude cost count said about the 36: no loop at all in 30; a short fixed loop in Ridgelines, Simplex Noise (6 rounds) and Worley Cells (3 by 3); a long one in Random Lines (60) and Random Shape (90); a loop whose end is not a written number in Line Group. One file (Digital Clock) holds a number above 65504, the value that drew black on OpenGL ES at medium precision. Color Bars needs arrays with initial values and Random Characters whole-number bit work, both OpenGL ES 3 and not 2.

### What would unlock the rest

Ranked by how many more files of this library each engine feature would let through, in the order they build on each other. None of this is built. What is said about mpv below comes from how this module and the mapper already use it and from reading; **each point marked "to verify" needs a short CI spike before any design**, as the first version of this module did (see LESSONS).

| Step | Feature | More files (running total of 327) | How, in an mpv user shader on `--vo=gpu` | How hard | Cost on a Pi 4 |
| --- | --- | --- | --- | --- | --- |
| 1 | **Filters on the picture that is playing** | +121 (157) | The hook already binds the picture as `HOOKED`: `IMG_THIS_PIXEL(inputImage)` is `HOOKED_tex(HOOKED_pos)`, `IMG_NORM_PIXEL` the same at another place, `IMG_PIXEL` divides by `HOOKED_size`, `IMG_SIZE` is `HOOKED_size`. The filter hooks the clip (or a generator's result, via a second pass), not the black carrier. | Medium. The translator part is small. The hard part is the stage: a clip is YUV at NATIVE, and mpv applies the brightness that opacity, fades and Blackout use before MAIN, so a filter at MAIN works on the dimmed picture and a filter that adds light (invert, glow) would undo a Blackout. Likely answer: the filter at MAIN and the fade as a last pass of our own; to verify. The player object also needs a third layer (effect) beside source and mapping. | One more pass at the clip's size, not at 720 lines: a 1080p clip has 2.25 times the pixels. A colour change (one read) is cheap; a blur with dozens of reads per pixel is heavy. Read `pass_ms`. |
| 2 | **Transitions** between two pictures | +68 (225) | mpv plays one picture at a time, so `startImage` and `endImage` cannot both be live. Reachable: a still of the outgoing picture as `startImage` (a screenshot written into the shader file as a `//!TEXTURE` block, the way the mapper writes its warp table) and the incoming clip as `endImage`, with `progress` driven by time. To verify: the size and build time of such a texture (the mapper's table took 1.3 s on the Pi). | Hard: step 1 first, then the still, the timing, and removal when the transition ends. | One pass for the length of the transition; most read each picture once or twice. |
| 3 | **Several passes** in one frame | +10 (235) | One hook block per ISF pass in the same file, each `//!SAVE name` and the next `//!BIND name`; ISF's `WIDTH` and `HEIGHT` expressions become mpv's `//!WIDTH` and `//!HEIGHT`; `PASSINDEX` is a constant per block. | Medium, after step 1 (all 10 are filters): the code is written out once per pass and the size expressions must be translated safely. | The sum of the passes; many use small passes (a blur at a quarter of the size), which is cheaper than it sounds. |
| 4 | **A picture kept between frames** (persistent buffers) | +40 (275), and 9 more with steps 5, 6 and 8 | **Not possible with mpv user shaders as far as is known**: a saved texture lives for one frame. To verify in the mpv manual and by a spike. If confirmed, feedback, trails, Life and the optical-flow files need a renderer of our own beside mpv (the same border at which projectM sits, D36). | Very hard: a new component, not a translator feature. | Not known. |
| 5 | **Vertex shaders** | +32 (307), 16 of them need only step 1 as well | mpv has none for users. Almost all of these `.vs` files only work out the places of neighbouring pixels (`left_coord`, `right_coord`), which a fragment shader can do itself: the translator would read the `.vs` and move those sums into the fragment code. | Medium to hard: a second file per upload, and only the common pattern can be moved safely; anything else stays refused. | A few more sums per pixel; nothing to speak of. |
| 6 | **Sound** (waveform, FFT) | +3 (and 4 more with steps 1, 3 and 4) | There is no audio texture in mpv. Possible in principle: let ffmpeg draw the spectrum or waveform as the carrier picture (`showspectrum`, `showwaves`) and read it as `HOOKED`. That needs a sound source in the player and a filter graph, which touches D22; to verify. | Hard for 7 files. | The carrier would no longer cost next to nothing. |
| 7 | **Imported pictures** | +1 (and 3 with step 1) | A `//!TEXTURE` block holding the picture, converted by mpv as other pictures are (D24). | Medium, for 4 files; low priority. | A texture in memory; no cost per frame to speak of. |
| 8 | Limits and checks | +2 (more than 24 inputs), 8 with a check | Raise the input limit; the checks stay (a header key twice is refused on purpose). | Easy; decide when filters exist. | None. |

So: filters are the one step that matters most (121 files at once, and 89 more depend on it), transitions come second, and persistent buffers are the wall that user shaders cannot climb.

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
- The code is checked as the compiler will read it: comments are taken out first (and are not passed on), and a backslash is refused, so nothing can hide behind `/**/` or a continued line. Preprocessor lines other than `#define`, `#undef` and the `#if` family are refused (no `#include`, `#version`, `#extension`, `#pragma`, `#line`), and `#define` or `#undef` of a name that belongs to the shader language, to ISF or to the player. `uniform`, `varying`, `layout` and `attribute` are refused anywhere, and `in` or `out` as a declaration of their own (they stay allowed for function parameters). The player's own names (`hook`, `HOOKED...`, `pvj_...`, mpv's `texture0` and its companions) are refused in any letter case. The code, once its comments are cut out, must be plain ASCII and have exactly one `void main()`. `##` (joining two pieces into one name) is refused, because a joined name is one the checks never saw whole. Two of the player's words are renamed instead of refused, since many real ISF files use them: `out_color` in the code (spelled exactly so) and an input called `color`; both become `pvj_` names, which a file cannot write itself, and a `#define` of either stays refused.
- The JSON header may not name a key twice (the last one would win over the one that was checked), and `NaN` and `Infinity` are refused. A file that trips the checks in a way nobody foresaw is listed as broken and skipped; it cannot take the list or Vibes down.
- File names: letters, digits, spaces and `. _ -`, ending in `.fs`, checked with a full match and the same rules as media names; a bundled name, of the project's own shaders or of a pack, cannot be taken. A pack is a plain folder inside `pvj/shaders.d` with a lower-case name; a link is not followed, and a pack's file can never stand in for one of the project's own (those are looked for first). An upload that already had the name of a pack file when the pack arrived is hidden by it and stays on disk unused. Uploads are stored in `<state>/shaders` (beside the settings file), never through a link, written to a temporary name first and never over an existing file unless replacement is asked for. They are translated before they are stored; what is refused is never written.
- The generated GLSL is written to the player's runtime folder (`/run/pvj`) as `shader-<pid>-<n>.glsl`, mode 0640, under a fresh name each time, and removed when replaced, like the mapper's files; the player cannot read the state folder.
- What remains: the shader code itself is GLSL from a full-access device and runs on the GPU. It cannot read files or reach the network, but it can be slow. A remote-support login that the studio gave the full role may upload shaders too, like any full-access device, for as long as the session lasts.

## API

- `GET /api/shaders` (view): `{"enabled", "shaders": [{"id", "name", "source": "bundled|uploaded", "pack": "nxlx|isf-files|uploads", "description", "credit", "cost", "vibes", "inputs": [{"name", "type", "label", "default", "min"?, "max"?, "values"?}], "error"}], "playing": {"id", "name", "values", "checked", "pass_ms"?} | null, "error": {"id", "message", "at"} | null, "vibes": {"running", "current", "next_in", "rounds", "last"}, "config": {"dwell", "vary", "height"}, "render": {"width", "height", "fps", "heights"}, "limits"}`. `pass_ms` is mpv's own timing of the shader pass, when the GPU reports one.
- `POST /api/shaders/play` (live): `{"id": "nxlx-tide.fs", "values"?: {"speed": 1.5}}`. Shows one shader. 422 with the reason if the file cannot be translated or the player refuses it.
- `POST /api/vibes` (live): `{"on": true}` starts (it answers at once; the first shader is on within a second), `{"on": false}` stops and clears the screen if Vibes still has it, `{"next": true}` goes to the next shader, `{"dwell": seconds}` sets how long each shader stays (10 to 3600; saved only when it changes).
- `POST /api/shaders` (full), one action: `{"action": "upload", "name": "x.fs", "source": "<the file's text>", "replace"?: bool}`, `{"action": "delete", "id"}`, `{"action": "vibes", "id", "on": bool}`, `{"action": "config", "dwell"?, "vary"?, "height"?}`.

Uploads go through the JSON API (auth, the request header and the Origin check as for every other change), not through the raw media upload path: that path streams gigabytes into the media folder and accepts media extensions only, and a shader is a few kilobytes of text that has to be parsed before it is stored.

Settings live under `"shaders"` in the settings file once something is changed; there is no settings migration (a missing or damaged entry means the defaults). The project's shaders and uploads are in Vibes unless named in `disabled`; a third-party pack's shaders are out unless named in `included`, a key that appears only once one was put in (and is exported and imported with the rest).

## Not verified

- **Never seen on a display, by anyone.** Checked only through screenshots of a 320 x 180 window on a software GPU in CI.
- **No speed measured on any board.** Not run on a Raspberry Pi, a Pi's GPU driver, or any real GPU. The 8-bit buffers and the default of 720 lines are choices made from the mapper's measurements on a Pi 4, not from measurements of this module.
- mpv 0.37 only (Ubuntu's, in CI). Not run on mpv 0.35 (Raspberry Pi OS Bookworm) or 0.40 (Trixie, on the test Pi).
- **Sync and the video wall.** A sync server that plays a shader or Vibes sends its clients "stop" (a shader is not a file they could play), so the clients go black; shaders in step on several boxes are not built. The wall crop over the carrier picture has not been tried.
- TIME over hours; the 16-bit question above; a player crash and restart during Vibes on a real box; Vibes from autostart across a real reboot; OSC from a real controller.
- Uploaded ISF files from other programs: the translator has now been run over one real collection (Vidvox's ISF-Files, see the survey above) and 27 of its files were drawn by a real player in CI. Other collections, and files from the ISF editor's web site, have not been tried.
- The ISF-Files pack: never seen on a display and never timed on a board; its cost notes are counts from the text.
