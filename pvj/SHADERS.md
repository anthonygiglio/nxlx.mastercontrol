<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Shaders and Vibes (beta)

Moving pictures drawn by the box's GPU instead of played from a file, and **Vibes**: one tap that plays them endlessly for ambience.

Switch the **Shaders and Vibes** module on under System > Modules (beta, off by default). It is offered on a Raspberry Pi 4, a Pi 5 and x86; not on a Pi 3.

**Read this first: nothing here has been seen on a display, and no speed has been measured on any board.** It has run only in automated tests: against a real mpv with Mesa's software GPU on a virtual display in CI (the picture is checked through screenshots), and against fakes. How many frames a second each shader reaches on a Pi 4, a Pi 5 or a PC is unknown. The cost notes below are counts of work per pixel, not measurements.

## Using it

- **Live screen: the Vibes button** (presenter, that is live access, and above). It starts the rotation; while it is on, the button stops it. "Now playing" shows `Vibes: <shader>`.
- **Mix screen: the Shaders and Vibes card.** The list of shaders with **Play** (live access): one shader, until something else is played. The shader on screen shows its number inputs as sliders; a change is sent when you let go. Full-access devices also see: **In Vibes / Not in Vibes** per shader, **Delete** for uploads, the dwell time, variation on or off, the drawing size, and **Upload an ISF shader**.
- **Autostart**: mode **Vibes** (System > Autostart). **Schedule**: the action **Start Vibes**. **OSC**: `/pvj/vibes` (press) starts it, `/pvj/vibes/next` goes to the next shader, the usual `/pvj/stop` ends it. MIDI and DMX have no control for it yet.
- Opacity, Fade in and out, Blackout, the overlay picture, mirror, rotate and the projection mapping apply to a shader as to a clip. Speed changes how fast it moves; Freeze stops it.

### Vibes

- Picks from the shaders that are **In Vibes** (bundled and uploaded), in a shuffled order; every shader is shown once before any comes again, and never the same one twice in a row.
- Each stays for the **dwell time** (default 180 seconds, 10 to 3600).
- Between shaders: a **dip to black** with the panel's own fade, over the Mix screen's transition duration (down for half of it, up for half). During a Blackout nothing fades.
- **Variation each round** (can be switched off): every number input gets a new value between its MIN and MAX, pulled towards the default (at most 60 percent of the way to either end, since the author chose the default as the good place); the palette is turned by up to 120 degrees either way around the grey axis (black, white and greys stay as they are); and the shader's time starts somewhere else. The values come from a random generator seeded when the service starts.
- **It never fights the operator** (the autostart rule, D18). It ends when anything else is played (a pad, a clip, a stream, a live input, the test pattern, a schedule entry, a sync server's clip), when Stop is pressed, when one shader is chosen by hand, when the module is switched off, and when the player is restarted. It does not come back by itself; after a player restart it starts again only if Autostart is set to Vibes. The reason it ended is shown on the card.
- A shader the GPU refuses is left out for the rest of that run and the next one is shown; if every shader is refused, Vibes ends and the screen is cleared.

## The bundled shaders

Ten original generator shaders, written for this project (Apache-2.0, in `pvj/shaders.d`). All are slow and quiet, use short fixed loops (at most 5 rounds) and no textures. **Cost is a rough count of work per pixel, not a measurement.**

| Shader | Picture | Inputs | Rough cost per pixel |
| --- | --- | --- | --- |
| nxlx-aurora | curtains of light over a dark sky | speed, height, tint | low: 4 bands, about 12 sines |
| nxlx-drift | soft clouds of colour | speed, scale, warmth | medium: 2 clouds of 3 octaves, 24 hashes |
| nxlx-ember | warm blobs that merge and part | speed, blob size, glow colour | low: 5 blobs, 10 sines |
| nxlx-horizon | a dusk sky with a slow sun and haze | speed, sun height, haze | low: 4 sines, one length, 4 exponentials |
| nxlx-lattice | two grids turning against each other (moire) | speed, density, colour | low: 4 sines |
| nxlx-nebula | clouds bent by more clouds | speed, fold, scale | medium to high: 3 clouds of 2 octaves, 24 hashes |
| nxlx-prism | rings of colour around the centre | speed, rings, petals | low: one atan, one length, 5 cosines |
| nxlx-pulse | ripples from three wandering points | speed, ripples, calm | low: 3 lengths, 9 sines |
| nxlx-silk | fine flowing lines | speed, lines, sheen | low: 7 sines |
| nxlx-tide | layers of slow waves | speed, swell, night colours | low: 5 layers, 10 sines |

If the picture stutters, draw fewer lines (the **drawing size** on the card: 360, 540, 720 or 1080 lines, 720 by default, never more than the screen has; mpv scales the result to the screen) and take nxlx-nebula and nxlx-drift out of Vibes first.

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
- The text `//!` is refused anywhere in the file, comments and JSON included: mpv reads such lines as commands wherever they stand, and a file could otherwise add its own hook on the OUTPUT stage (over the mapping), a texture or a second pass. Preprocessor lines other than `#define`, `#undef` and the `#if` family are refused (no `#include`, `#version`, `#extension`, `#pragma`, `#line`), and so are `uniform`, `varying`, `in`, `out`, `layout` and `attribute` declarations. The code must be plain ASCII and have exactly one `void main()`.
- File names: letters, digits, spaces and `. _ -`, ending in `.fs`, checked with a full match and the same rules as media names; a bundled name cannot be taken. Uploads are stored in `<state>/shaders` (beside the settings file), never through a link, written to a temporary name first and never over an existing file unless replacement is asked for. They are translated before they are stored; what is refused is never written.
- The generated GLSL is written to the player's runtime folder (`/run/pvj`) as `shader-<pid>-<n>.glsl`, mode 0640, under a fresh name each time, and removed when replaced, like the mapper's files; the player cannot read the state folder.
- What remains: the shader code itself is GLSL from a full-access device and runs on the GPU. It cannot read files or reach the network, but it can be slow.

## API

- `GET /api/shaders` (view): `{"enabled", "shaders": [{"id", "name", "source": "bundled|uploaded", "description", "credit", "cost", "vibes", "inputs": [{"name", "type", "label", "default", "min"?, "max"?, "values"?}], "error"}], "playing": {"id", "name", "values", "checked", "pass_ms"?} | null, "error": {"id", "message", "at"} | null, "vibes": {"running", "current", "next_in", "rounds", "last"}, "config": {"dwell", "vary", "height"}, "render": {"width", "height", "fps", "heights"}, "limits"}`. `pass_ms` is mpv's own timing of the shader pass, when the GPU reports one.
- `POST /api/shaders/play` (live): `{"id": "nxlx-tide.fs", "values"?: {"speed": 1.5}}`. Shows one shader. 422 with the reason if the file cannot be translated or the player refuses it.
- `POST /api/vibes` (live): `{"on": true}` starts (it answers at once; the first shader is on within a second), `{"on": false}` stops and clears the screen if Vibes still has it, `{"next": true}` goes to the next shader.
- `POST /api/shaders` (full), one action: `{"action": "upload", "name": "x.fs", "source": "<the file's text>", "replace"?: bool}`, `{"action": "delete", "id"}`, `{"action": "vibes", "id", "on": bool}`, `{"action": "config", "dwell"?, "vary"?, "height"?}`.

Uploads go through the JSON API (auth, the request header and the Origin check as for every other change), not through the raw media upload path: that path streams gigabytes into the media folder and accepts media extensions only, and a shader is a few kilobytes of text that has to be parsed before it is stored.

Settings live under `"shaders"` in the settings file once something is changed; there is no settings migration (a missing or damaged entry means the defaults).

## Not verified

- **Never seen on a display, by anyone.** Checked only through screenshots of a 320 x 180 window on a software GPU in CI.
- **No speed measured on any board.** Not run on a Raspberry Pi, a Pi's GPU driver, or any real GPU. The 8-bit buffers and the default of 720 lines are choices made from the mapper's measurements on a Pi 4, not from measurements of this module.
- mpv 0.37 only (Ubuntu's, in CI). Not run on mpv 0.35 (Raspberry Pi OS Bookworm) or 0.40 (Trixie, on the test Pi).
- TIME over hours; the 16-bit question above; a player crash and restart during Vibes on a real box; Vibes from autostart across a real reboot; OSC from a real controller.
- Uploaded ISF files from other programs: the translator was tested with files written for the tests, not with a collection of real-world ISF shaders.
