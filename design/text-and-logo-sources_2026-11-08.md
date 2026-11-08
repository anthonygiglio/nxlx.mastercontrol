<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Text and logo sources: a design (2026-11-08)

Status: design only, no code. Proposed decision: D129 (`project-log/decisions/D129-text-and-logo-graphics.md`).
Written for Anthony (owner). Plain words first; the builders' detail is in the sections after "What you do".

## 0. The ask, and why it is built in

On 30 October Anthony asked for two things:

1. "We should include a Text Generator source."
2. "I want to be able to upload images, DJ logos for example, use them as a source or overlay, and apply some effects or
   keying to the image."

On 8 November he decided to build both into Mastercontrol instead of sending HTML over NDI from a laptop. That test
reached about 24 fps, ran 0.8 s behind, and loaded the laptop heavily. The target here is the opposite: the node draws
text and logos itself, in step with the clip, and keeps 60 Hz.

Words used in this document:

- **Graphic**: a text or a logo that the node draws. This word is new on purpose. "Layer" already means two things in
  Mastercontrol (source 1's two video layers in D98, and the nanoKONTROL2's Geometry layers in D75), and "overlay"
  already means the mask list of D100. Using "graphic" avoids a third meaning.
- **Mask**: what D100 calls the PNGs combined into the one overlay picture. They stay as they are (section 9).
- **Node**: the Pi or desktop that plays. **Source**: a clip layer of the New renderer (D101).

## 1. What exists today (read before this design)

| Fact | Where |
| --- | --- |
| Sources 2 to 8 are one libmpv core each in `pvj-render`; source 1 is the player. The mapping picks a source per surface. | D101, `pvj/PANEL.md` section 12 |
| Generators (ISF shaders) and effects are drawn by `pvj-render` itself, with values set every frame; each source has a chain of up to 3 effects. | D106, D74, D55 |
| One overlay picture (mpv `overlay-add`, id 10) is the mask list: up to 8 PNGs combined by mpv into one premultiplied BGRA picture at screen size, in a worker thread, newest change wins. Works on classic mpv too. | D100, `pvj/overlay.py` |
| `pvj-render` has a "text layer": a second libmpv that plays nothing and draws overlays and `show-text` above everything. | `render/src/main.c` |
| Media upload: Operator and up, one at a time, hidden temp file, size and rate limits, extension list `VIDEO + IMAGE + AUDIO`. Images are `.png .jpg .jpeg .bmp .gif` and are playable as clips. There are no thumbnails. | `pvj/api.py`, `pvj/player.py` |
| Fonts on the node: Archivo and JetBrains Mono (OFL), bundled for the panel's Signal look as web fonts only. `LICENSES/OFL-1.1.txt` is in the repo. | `pvj/web/fonts`, D54 |
| Tiers: `hardware.tier()` gives lean (Pi 3, and unknown boards by D122), standard (Pi 4, Pi 400, CM4), full (Pi 5, x86). A Pi 3's graphics chip is OpenGL ES 2, so it runs classic mpv, never `pvj-render`. | D122, D123, journal 2026-11-07 board tiers |
| Roles: Owner (`full`), Operator (`live`), Guest (`view`). A Guest may only send a fixed list (`policy.GUEST`). | `pvj/PANEL.md` section 1 |
| No Python imaging library on the node (no Pillow, no numpy). | D100 |

Not found: the "universal hardware plan" the brief mentions is not on `origin/master` (there is no `design/` folder
there). The tier rules above come from `pvj/hardware.py`, `pvj/profile.py` and decisions D122 and D123. This file
creates `design/`.

## 2. What you do (the experience)

### 2.1 Text, on a phone

1. Open **Play > Graphics** (a new screen, section 6). Tap **Add text**.
2. Type a line ("NEXT UP: DJ MARA"). A preview of how it will look appears under the box. The preview is drawn by the
   node with the same code that draws the screen, so what you see is what the wall gets.
3. Pick a **font** from a short list, a **size** (slider, as a share of the screen's height), a **colour**
   (swatches, plus a custom one), and optionally an **outline**, a **shadow** or a **back plate**.
4. Pick a **position**: a nine-point grid (corners, edges, centre) and fine nudge sliders for X and Y.
5. Pick a **motion**: None, Fade, Typewriter, Scroll (credits, bottom to top) or Crawl (ticker, right to left), with a
   speed or a duration.
6. Choose **where it shows**: *Over the screen* (the default: on top of everything, like a lower third), *Over Source N*
   (drawn on that source's picture, so the mapping carries it onto its surfaces), or *As Source N* (the text is the
   source's picture, on a background colour).
7. Tap **Show**. A big **Show / Hide** button stays at the top of the screen so a performer can fire it with a thumb.
8. For lines you use again, open **Lines** and add up to 20. Tapping a line sends it to the screen at once with the
   chosen motion. This is the "push a line from the phone" case.

On a laptop the same screen is two columns: the form on the left, the preview and the list of graphics on the right,
and the keyboard works for typing and for Enter to send a line.

### 2.2 Logo, on a phone and a laptop

1. In **Play > Library**, under a new **Graphics** tab, tap **Upload logo**. Choose a PNG, JPG or SVG. A phone can pick
   from Photos or Files; a laptop can drag a file in.
2. The node checks it (section 7), turns it into a clean PNG, and shows a thumbnail. If it refuses, it says why in
   plain words ("This SVG uses text. Convert the text to outlines and try again.").
3. In **Play > Graphics**, tap **Add logo** and pick the file.
4. **Place and scale**: the same nine-point grid and nudge sliders, a **Size** slider (a share of the screen's width),
   **Opacity**, and **Rotate**. On a laptop the preview can be dragged and resized with the mouse.
5. **Key it** (section 5.3): None, **Alpha** (use the transparency the file already has; the usual case for a PNG),
   **Luma** (make dark or bright areas transparent: a JPG logo on black or white), or **Chroma** (make one colour
   transparent: a green or blue backdrop). Chroma has a colour picker: tap the preview where the colour to remove is.
   Sliders: Threshold, Softness, and for chroma Spill.
6. **Effects**: pick up to three from a short list (Glow, Drop shadow, Outline, Colour shift, Invert, Posterise, RGB
   split, Pixelate, Flicker, Wave). Each has an Amount slider.
7. Same **Where it shows** choice as text. Tap Show.

### 2.3 What is deliberately not here

No video in a graphic (use a clip source), no animated GIF or APNG (first frame only, said at upload), no emoji and no
right-to-left or CJK text in the first release (section 4.3), no HTML or web page rendering (that was the laptop test
this replaces).

## 3. Where each thing is drawn, by tier

The rule: text and logos are made into a small premultiplied picture by a separate rasteriser process (section 7), and
then drawn by whatever draws the screen on that node.

| Tier | Drawn by | Static text and logo | Fade | Typewriter | Scroll, crawl | Key | Effects | As Source N |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Full (Pi 5, desktop) and standard (Pi 4, Pi 400, CM4) on the New renderer | `pvj-render` GPU, a new graphics stage (section 3.1) | yes | yes, per frame | yes, per frame | yes, per frame | live on the GPU | up to 3, live | yes, on a source slot |
| Standard or full, Classic renderer chosen | mpv `overlay-add`, small bitmaps, no `pvj-render` | yes | stepped, about 12 steps a second | stepped, about 12 steps a second | refused with plain words, unless the spike below passes | baked in when the picture is built | baked in (colour effects only) | text on black only |
| Lean (Pi 3, unknown boards) | same as Classic: mpv `overlay-add` | yes | stepped | stepped | refused, unless the spike passes | baked in | baked in (colour effects only) | text on black only |

"Baked in" means the rasteriser applies the key and the colour effect once, when the graphic changes, so nothing is
computed per frame. On a Pi 3 the panel shows the motions and effects that tier cannot do as greyed out with the line
"This node draws graphics without the New renderer: only None, Fade and Typewriter, with steps." (D121 wording: it says
node.)

**Spike for the lean tier (slice 2).** mpv's `osd-overlay` command takes ASS events, and libass can animate `\move`,
`\fad` and `\t`. If mpv 0.37 on a Pi 3 animates those smoothly in an `osd-overlay`, scroll and crawl and true fades can
go to the lean tier at no per-frame Python cost. It is not known whether the OSD's clock advances the ASS time. The
spike decides it. If it fails, the table above stands.

### 3.1 The graphics stage in `pvj-render` (standard and full)

- Each on graphic is one texture (premultiplied RGBA) and one set of per-frame numbers: position, scale, rotation,
  opacity, and for motion a time value. The rasteriser writes the picture to a file in a runtime folder (like the
  overlay's raw file, `overlay_file_ok` in `render/src/ipc.c` already checks sizes before mpv maps one) and the
  renderer uploads it once per change. Per frame the cost is one textured quad.
- **Motion on the GPU, not by rebuilding pictures.**
  - Fade: opacity from a time value.
  - Scroll and crawl: the text is rasterised once as a tall or wide strip; the quad's texture coordinates move. A crawl
    is limited to the node's `GL_MAX_TEXTURE_SIZE` (2048 is the ES 3 floor, 4096 on a Pi 4); longer text is refused with
    "Too long for this node's ticker at this size: shorten it or lower the size." The renderer reports the limit.
  - Typewriter: the rasteriser also returns the right edge of each character on each line. The shader reveals up to the
    chosen character with a short soft edge, line by line. The text is laid out once, so letters do not shift as they
    appear.
- **Keying** is a built-in pass on the graphic's texture, run only when its controls change, into a cached texture
  (section 5.3). **Effects** (the D106 chain, up to 3) run on the graphic's own texture, so their cost follows the
  graphic's area, not the screen's, at the effect rate (D123: the screen's rate on the full tier, 30 on the others).
- **Where in the frame.** *Over the screen*: after the mapping's final pass (so a graphic is not warped by a surface and
  stays put, as the masks do), below the text layer, so masks and the pairing code stay on top. *Over Source N*: drawn
  into that source's texture after its chain, before the mapping reads it. *As Source N*: the source's texture is
  cleared to the background colour and the graphic is drawn into it, in place of a clip (a clip playing there is
  stopped, and that is said in the panel). The exact insertion points are for the slice 3 builder to confirm in
  `render/src/main.c` and `out.c`.
- **Sharpness.** Over the screen, a graphic is rasterised for the size the final pass draws at (D108: 720 lines when the
  display's scaler brings the picture up to the output), not for the clip's inner size (540 lines on a Pi 4, D122). Text
  over a source on a Pi 4 is therefore softer than text over the screen, and the panel's hint says "For sharp text, show
  it over the screen."

### 3.2 Frame cost targets (to be measured, not yet measured)

The node must keep 60 flips a second with a clip playing. Targets, to be proven in slice 3 and 4 by the same counters
D101 used (`drawn`, flips a second, dropped frames) and the Epson X27 only sparingly and batched (it is a show unit):

| Case | Target |
| --- | --- |
| A graphic that is off | no cost at all: not drawn, not uploaded (as an idle source, D101) |
| One static text or logo over the screen, no key, no effect | under 0.5 ms of GPU time a frame at 1080p on a Pi 4; no change in flips a second |
| One scroll or crawl, one keyed logo with one effect, over a 1080p clip, Pi 4 | 60 flips a second, 0 dropped frames over a 10 minute run |
| Four graphics on at once, Pi 5 and desktop | 60 flips a second, 0 dropped |
| Pi 3 (classic): static text and a logo over a 720p clip | clip keeps its rate; the graphic changes cost the player nothing between changes |
| A text change (OSC push) to a visible graphic | on screen within 100 ms on a Pi 4, and within 250 ms on a Pi 3 |
| Memory | all graphic textures together at most 48 MB (inside the renderer's arena cap, D114); a graphic that would not fit is refused with the amount |

Limits that keep it there: at most 8 graphics defined, at most 4 on at once on the lean and standard tiers (8 on full),
each stored logo at most 2048 pixels on its long side after normalising, text rasterised no larger than the final
pass.

## 4. Text

### 4.1 What a text graphic holds

Text up to 280 characters and 6 lines; a font from the bundled list; size (1 to 60 percent of the screen height);
colour; weight (the bundled font's own weights only); alignment; line spacing; outline (colour, width); shadow (on,
colour, softness); a back plate (colour, opacity, padding). Motion and position as in section 2.1.

Text is cleaned before use: control characters removed (a newline is kept), Unicode normalised, leading and trailing
space trimmed, length cut with the reason said. It is never interpreted: no markup, no ASS tags (an ASS-based lean path
escapes `{`, `}` and `\` so a typed line cannot become a tag).

### 4.2 Fonts: bundled, open licence only

Every bundled font must be under the SIL Open Font Licence 1.1 or the Apache licence 2.0, ship with its licence text in
`LICENSES/` and `THIRD_PARTY_LICENSES.md`, and be listed in `REUSE.toml`. The list proposed for the first release
(each licence to be re-read from the font's own release when it is added; this list is from the maintainers' published
licences as understood at writing):

| Font | Why it is here | Licence |
| --- | --- | --- |
| Archivo (regular, bold, condensed cut) | already bundled; the Signal look; clean titles | OFL 1.1 |
| JetBrains Mono | already bundled; monospace, good for the typewriter | OFL 1.1 |
| Inter | neutral, readable at distance | OFL 1.1 |
| Bebas Neue | tall condensed capitals: the classic DJ name | OFL 1.1 |
| Oswald | condensed, strong, many weights | OFL 1.1 |
| Anton | heavy poster capitals | OFL 1.1 |
| Montserrat | wide geometric, good for lower thirds | OFL 1.1 |
| Orbitron | techno look | OFL 1.1 |
| Press Start 2P | pixel look | OFL 1.1 |
| Roboto Condensed | tickers and crawls | Apache 2.0 |
| Noto Sans (fallback) | covers Latin, Cyrillic and Greek so a missing letter is not a hole | OFL 1.1 |

About 1.5 to 3 MB of font files in total on the image. Anthony can strike or add from this list before slice 2 ships;
the only rule is the licence.

An OFL font may be bundled and used to make pictures; it may not be sold alone and its reserved name must not be reused
for a modified copy. We do not modify any. Nothing here needs the fonts' licence text shown on screen, but the About
page lists them.

### 4.3 Languages

First release: Latin (with accents), Cyrillic, Greek. Shaping is plain left to right. Not in the first release:
emoji (Noto Color Emoji is about 10 MB and a colour-bitmap format), right to left, Indic scripts, CJK (needs HarfBuzz
shaping and 15 MB or more of font). A character the chosen font lacks falls to Noto Sans; one that Noto lacks is drawn
as a small box and the panel says "1 character cannot be drawn." A later "Extended languages" pack can add HarfBuzz and
Noto CJK, Arabic and Hebrew as an optional install.

### 4.4 Uploading fonts: not in the first release

Reasons against, in order of weight:

1. **Parsing is a classic attack surface.** TrueType and OpenType files are programs-in-data (hinting bytecode, many
   tables); FreeType and every other font engine have a long record of memory bugs. We already put image decoding behind
   a sandbox (section 7); fonts need the same, and a font is more complex than a PNG.
2. **Licence.** A user can upload a font they may not have the right to use on a wall, or to include in an exported
   settings bundle (section 8) that is passed to someone else. We would be copying it around.
3. **Failures at the worst time.** A font with no Cyrillic, a broken kerning table, or enormous glyphs shows up on stage.
4. **Role.** An Operator already uploads clips; handing Operators a code-parsing upload widens what a lost phone can do.

If wanted later (slice 7): **Owner only**, TrueType and OpenType files only (no WOFF, no collections), at most 5 MB each
and 12 in all, parsed only inside the rasteriser, checked by a test render at upload (must draw "AaBbCc 0123" without
error and with sane glyph sizes), stored in a folder of their own, **never sent to a browser** (the panel previews with
node-made pictures, so a font never reaches a client), left out of exported bundles unless the Owner ticks "include
fonts" with the licence line, and listed with their names. A bundle that names a font the node lacks falls back to
Archivo and says so.

## 5. Logo

### 5.1 Files and the library

Accepted: **PNG, JPEG, SVG**. Everything is normalised at upload (section 7) to a stored **PNG** in a folder of its
own beside the media folder (the "graphics store"). They are kept out of the clip list on purpose: otherwise a logo
would show up as a pad clip and in "Play all". Names follow the media folder's rules (`valid_name`, no overwriting
without asking). The Library gets a **Graphics** tab (thumbnails, size, Rename, Delete, with the question in place),
which is new: today there are no thumbnails for media, so a thumbnail route is added for graphics only.

SVG is rasterised once at upload (2048 pixels on the long side) and the SVG itself is **not kept**: the stored file is
the PNG. That removes the SVG from every later path. The cost is that a very large wall at 4K shows a logo from a
2048 px bitmap; a logo that wide is rare, and the panel can warn "This logo is drawn from a 2048 px picture."

### 5.2 Place and scale

Position X and Y as percentages of the screen centred on the logo (-100 to 100, as the Picture sliders do), size as a
share of the screen's width, rotation in degrees (any, not only quarter turns), opacity, mirror. A safe-area guide
(5 percent in) can be shown on the preview.

### 5.3 Keying

All three produce an alpha value per pixel before the logo is drawn. They run on the GPU on the New renderer and are
baked on the classic path.

| Key | What it does | Controls | Notes |
| --- | --- | --- | --- |
| **Alpha** | uses the picture's own transparency | none (a Premultiplied / Straight toggle for odd files) | default for PNG; for a JPG there is none, so the panel suggests Luma or Chroma |
| **Luma** | makes pixels transparent by brightness | Mode (Black out, White out), Threshold, Softness, Invert | the "logo on black" case; soft edge so antialiasing does not leave a halo |
| **Chroma** | makes pixels transparent by distance from one colour | Key colour (picker on the preview), Tolerance, Softness, Spill suppression | colour distance is measured in a luma-independent space so shadows on a green backdrop still key; spill suppression pulls the key colour out of edge pixels |

The keyed result is cached in a texture and recomputed only when a key control changes, so dragging a Threshold slider
costs one small pass per frame while it moves and nothing after. All three unpremultiply, key, then premultiply, so
edges stay clean.

### 5.4 Effects

The graphic uses the same chain as a source (D106, section 12 of `PANEL.md`): up to three effects in order, each with
the effect's own controls. Two facts to settle early (slice 4 spike):

- The renderer's effect passes were written for a picture with no transparency (the mpv carrier is `rgb0`). A graphic
  needs alpha kept. The plan: effects change the colour only and the key's alpha is applied after them. Shape-changing
  effects (Glow, Drop shadow, Outline, Wave, RGB split) need to make their own alpha, so they are new effect files
  marked "alpha-aware". `render/src/fx.c` already keeps what a pass writes, alpha included, so this should be small.
- The existing colour effects (D74 list) work on a graphic unchanged once the alpha is held back and re-applied.

On the lean tier only colour effects exist, baked in by the rasteriser.

## 6. Where it lives in the panel

Following `pvj/PANEL.md` (D102): colours by area, pages "as it is", and the rule that a change to a screen changes its
section in the same pull request.

- **Play > Graphics** (new, area Play, colour `clips`): the live screen. A list of up to 8 graphics, each a card with
  name, a big Show / Hide, and a fold for the form. At the top a "Hide all" for an emergency. Lines for text.
- **Play > Library > Graphics tab**: upload, thumbnails, rename, delete.
- **Play > Pads**: a pad kind **Graphic** (slice 6), so a controller pad shows or hides one, or sends one of its lines.
- **Shape > Picture > Overlay pictures** stays as it is (masks).
- Layout widths 390, 820, 1440 as the design brief (PR #154); touch targets per section 14.

## 7. Upload safety

This feature accepts files from phones and laptops and decodes them. It needs the same care as the NDI helper (D62):
the risky work happens in a separate, unprivileged process. **It needs an independent review before it ships** (see the
end of this section).

### 7.1 The pipeline

1. **Receive** with the existing upload path (`Library.upload`): one at a time, hidden temp file in the graphics
   store's own temp area, the rate and size limits already there. Operator and up. Disk space checked first.
2. **Cheap checks in the panel's process, without decoding**: size on disk, **magic bytes** (not the extension) say PNG
   (`\x89PNG`), JPEG (`\xFF\xD8\xFF`) or SVG (text starting, after optional BOM and whitespace, with `<?xml` or
   `<svg`); the extension must agree with the magic; PNG and JPEG **dimensions read from the header** and refused above
   the limits below (as `overlay.py` does for masks) before any decoder sees the file.
3. **Decode in `pvj-raster`**, a new small program run as its own systemd service, one process per job
   (`Accept=yes` socket activation), under its own account, with: no network (`PrivateNetwork=yes`), no access to the
   media or settings folders, read-only system (`ProtectSystem=strict`), `NoNewPrivileges`, a syscall filter
   (`SystemCallFilter=@system-service` minus `@privileged`), `RLIMIT_AS` 512 MB, `RLIMIT_CPU` 10 s, `MemoryMax=256M`, and
   a wall-clock kill at 15 s. It reads the file from a pipe and writes back only a normalised PNG and a few numbers
   (size, whether it has alpha). It runs no user script, loads no user configuration, opens no file by a name the
   upload supplied.
4. **Normalise**: decode, apply a JPEG's orientation, convert to sRGB, drop every metadata chunk (EXIF, ICC, text,
   XMP), flatten APNG to its first frame, scale down so the long side is at most 2048, write a fresh PNG. A
   file that carries extra data after the image (a polyglot) loses it, because the new PNG is written from pixels.
5. **Store** the new PNG under the chosen name in the graphics store. The original upload is deleted.
6. The panel never serves an original, and never serves a user SVG. Thumbnails are the normalised PNG scaled down by
   the same helper.

### 7.2 Limits

| What | Limit |
| --- | --- |
| Types | PNG, JPEG, SVG. Not WebP, GIF, BMP, TIFF, HEIC or PDF (plain refusal). |
| File size | PNG and JPEG 10 MB; SVG 1 MB |
| Dimensions | 8192 on a side and 40 megapixels (the same limits as a mask, D100), read from the header |
| After normalising | long side at most 2048; the stored PNG is typically under 2 MB |
| Count and space | 64 files, 100 MB in all; the oldest unused is not deleted for you |
| Rate | one upload at a time; 5 new graphic files a minute |
| JPEG | no CMYK or lossless variants; progressive is fine |
| PNG | 16 bit is reduced to 8; interlaced is fine |

### 7.3 SVG

SVG is the riskiest type: it is XML with scripts, external references, entities and filters. We take only its shapes.

- **Rasterise only, with a small parser**: a single-file renderer (nanosvg, zlib licence, vendored with its licence)
  that handles paths, basic shapes, fills, strokes and gradients and has **no DTD, no entities, no scripting, no
  external fetch, no `<image>` and no text**. Chosen over a full SVG library (librsvg) because a smaller engine has a
  smaller surface.
- **Refuse in plain words** if the file contains `<!DOCTYPE` with `<!ENTITY`, `<script`, `<foreignObject`,
  `<image`, `<use` with a non-local `href`, or any `href`/`src` that is not `#...` or `data:image/png`. The parser
  would ignore most of these, but a refusal tells the user and a sneaky file never gets as far as the parser.
  Text elements: "This SVG uses text. Convert the text to outlines (paths) and try again."
- **Nothing is kept**: the SVG is not stored (5.1), not served, not exported.
- Cap the work: 1 MB, 20 000 path points, 2048 px output, 5 s of CPU.

### 7.4 Why a separate process

Image and font code written in C has a long history of memory bugs; the decoder will see files from phones on a venue
network. If it is hijacked, it can only read the one file it was given and write one PNG: no network, no settings, no
other files, no raising its rights, no more than 512 MB, no more than 15 s. This is the same reason the NDI runtime
is in its own unprivileged service (D62). The libraries are libpng, libjpeg-turbo, FreeType (text and, later, fonts)
and the vendored SVG parser; the first three are already on the node as dependencies of mpv, which is what D100 uses
to decode masks today. The difference is that mpv runs with the whole of ffmpeg's demuxers, while `pvj-raster` links
only the three it needs. (The mask converter of D100 stays as it is; masks can move to `pvj-raster` later.)

### 7.5 What the independent review must cover (a separate task, after slice 1)

- The header checks, the magic-byte check and the order (nothing decoded before them).
- `pvj-raster`'s unit file and its seccomp list; try to read `/var/lib/pvj/settings.json` and open a socket from inside.
- Fuzz the PNG, JPEG and SVG paths for an hour (libFuzzer or AFL) and run the existing sample sets of malformed images.
- SVG: entity expansion, huge coordinates, deep nesting, a 1 MB file of nothing but `<g>`.
- Decompression bombs (a 40 MP PNG of one colour, a small file that expands); the memory and time kills.
- Name handling and path traversal on upload, rename, delete and the thumbnail route.
- Roles: that a Guest cannot reach any of it.
- That nothing user-made is ever sent to a browser with a type a browser would run.

## 8. Data shapes

### 8.1 Settings (`settings.json`)

No schema bump: new keys are read with defaults everywhere, as D100 did, so the release before this one ignores them.

```
"graphics": {
  "on": true,                          // the whole stage's switch (Hide all)
  "items": [                           // at most 8, in drawing order, later over earlier
    {
      "id": "g3f9a",                   // stable, not shown
      "slot": 1,                       // 1 to 8: the number controllers use
      "kind": "text",                  // "text" or "logo"
      "name": "Next up",
      "on": false,
      "where": {"mode": "screen"},     // {"mode":"screen"} | {"mode":"over","source":2} | {"mode":"as","source":2,"back":"#000000"}
      "place": {"x": 0, "y": 80, "scale": 100, "rotate": 0, "opacity": 100, "mirror": false, "anchor": "bc"},
      "text": {"value": "NEXT UP: DJ MARA", "font": "bebas-neue", "size": 9, "colour": "#ffffff",
               "align": "center", "outline": {"colour": "#000000", "width": 2}, "shadow": {"on": true, "colour": "#000000", "soft": 40},
               "plate": {"on": false, "colour": "#000000", "opacity": 60, "pad": 8},
               "lines": ["NEXT UP: DJ MARA", "THANK YOU, SASKATOON"], "line": 0},
      "logo": {"file": "mara-logo.png", "size": 30},
      "motion": {"kind": "typewriter", "seconds": 2.0, "speed": 60, "loop": false},
      "key": {"kind": "none"},         // or {"kind":"luma","mode":"black","threshold":15,"soft":10,"invert":false} or {"kind":"chroma","colour":"#00ff00","tolerance":30,"soft":10,"spill":50}
      "fx": [{"id": "glow", "amount": 40, "controls": {}}]   // at most 3
    }
  ]
}
```

A text item has `text` and no `logo`; a logo item the reverse. A damaged section is read, never trusted: unknown keys
dropped, numbers clamped, a missing font becomes Archivo, a missing logo file shows as "not in the Library" and is left
out, as masks do (`overlay.masks_of`).

### 8.2 Routes and roles

| Route | Does | Role |
| --- | --- | --- |
| `GET /api/graphics` | the whole state: items, status (`building`, `ready`, `error`), limits, which motions and effects this node's tier allows, the font list, the file list | Operator and up |
| `POST /api/graphics` | `{add: "text"\|"logo"}`, `{id, set: {...}}`, `{id, remove: true}`, `{id, on: true\|false\|"toggle"}`, `{id, text: "..."}`, `{id, line: N\|"next"\|"prev"}`, `{id, replay: true}`, `{move: id, dir: "up"\|"down"}`, `{all: false}` (Hide all) | Operator and up |
| `POST /api/graphics/upload?name=` | the raw file, then the pipeline of section 7; the answer is the stored name or the plain reason | Operator and up |
| `GET /api/graphics/file` | the file list with sizes | Operator and up |
| `GET /api/graphics/file/<name>.png` | thumbnail | Operator and up |
| `POST /api/graphics/file` | `{rename}`, `{delete}` | Operator and up |
| `GET /api/graphics/preview.png?id=` | the node-made preview of one graphic, rate limited (5 a second) | Operator and up |
| `GET /api/graphics/fonts` | the bundled list: id, name, licence | Operator and up |
| `POST /api/graphics/fonts` | add or remove an uploaded font (slice 7 only) | **Owner** |
| `GET /api/status` | gains `graphics_on` (a count) so every role's header can show that something is on the screen | everyone |

Text in a request: 280 characters, 6 lines; an OSC or API change to a visible text is rebuilt in the rasteriser by a
worker whose newest request wins (`overlay.Masks` pattern), at most 5 rebuilds a second per graphic and 20 a second in
all; a burst keeps the last. `where.source` above the node's source count is kept and said ("Source 3 needs the New
renderer: until then this shows over the screen"), as `PANEL.md` section 12 does for surfaces.

**Guest.** A Guest watches. The Guest list (`policy.GUEST`) is a fixed list of request forms and none of these is added.
This is a decision, not an accident: a Guest on a venue's open Wi-Fi putting words on the wall is a risk we do not need.
Owner and Operator both reach all of it except fonts.

### 8.3 Controllers: MIDI, OSC, TouchOSC

Actions (`pvj/actions.py` names, so MIDI learn and the OSC table both reach them):

- `graphic_N_toggle` (N = slot 1 to 8): show or hide.
- `graphic_N_line_next`, `graphic_N_line_prev`: step through the Lines and send.
- `graphic_N_opacity` (a level), `graphic_N_x`, `graphic_N_y`, `graphic_N_scale` (levels, like the Picture sliders).
- `graphic_replay_N`: play the motion again.
- `graphics_hide_all`.

**OSC** (addresses follow `pvj/OSC.md`: behind the same three locks; the Owner's OSC switch must be on):

| Address | Arguments | What it does |
| --- | --- | --- |
| `/pvj/graphic/N` | none or 0/1 | switch over, or set on or off |
| `/pvj/graphic/N/text` | one string | set the text (cleaned as 4.1) and show it if the graphic is on |
| `/pvj/graphic/N/send` | one string | set the text **and** switch on, with the motion: one message pushes a line |
| `/pvj/graphic/N/line` | int | choose line number M from the list and send it |
| `/pvj/graphic/N/opacity`, `/x`, `/y`, `/scale` | float | the same ranges as the panel's |
| `/pvj/graphic/N/replay` | none | play the motion again |
| `/pvj/graphic/hide` | none | hide all |

State sent back (so a TouchOSC label shows what is on): `/pvj/state/graphic/N/on` (0/1) and
`/pvj/state/graphic/N/text` (string, cut to 64 characters). Not sent: the logo file (as with the overlay's file, OSC.md).
For TouchOSC (`docs/TouchOSC_Layout V.01.md`): a text field bound to `/pvj/graphic/1/send` with "send on return" is the
phone-pushes-a-line case; a button bound to `.../N` toggles; a label receiving `/pvj/state/graphic/1/text` shows the
current line. The Deck/Hub layout work (panel coordination) can place these when the graphics ship.

**Pads and scenes.** A Graphic pad (slice 6) toggles one graphic or sends one line. A Room scene or a saved "Mix" can
list graphics to show, like it lists a shader, as a later add-on; not in this plan's slices.

## 9. How it fits D74, D100, D101 and D106

- **D74 (effects per source).** A source's chain filters the picture of that source. A graphic *over Source N* is drawn
  after that chain, so an effect on the clip does not distort the logo. A graphic *as Source N* is the source's picture,
  so the source's own chain (the Sources card) can apply to it as to any generator. The graphic's own up-to-three
  effects are separate and run first. No change to D74's rules is needed.
- **D100 (masks) and the "general layer" question.** Should the PNG overlay become a general layer? **Recommended: no,
  not now.** Masks are full-screen frames, vignettes and credits plates, combined once into a picture the player
  draws; they work on every tier, have been through review (#152) and a lot of use. Graphics move, scale, fade, key and
  take effects, which a combined static picture cannot. Merging them would put the proven path at risk and would make
  the Pi 3 pay for a GPU feature. They do overlap (a logo can be a mask), so the panel says it: *Masks: full-screen
  pictures that never move. Graphics: logos and text you place, move and key.* The one thing that is shared is the
  mechanism of the build worker (`Masks` pattern) and the overlay draw call on the classic path (graphics use mpv
  overlay ids 20 to 27; the masks keep 10, the pairing code 1 to 4 and transitions 63). If, after use, Anthony finds
  two lists confusing, a later PR can show masks as a "Full-screen" kind inside Graphics; the settings keys can be read
  into either.
- **D101 (sources).** *As Source N* and *Over Source N* use D101's source texture and its mapping, so a surface can show
  "the DJ's name" while another shows a clip. Sources 2 and up on the default Pi 4 are limited to 2 (D122), so *As
  Source 2* takes the only extra source; the panel says "Uses Source 2 while it shows. A clip playing there stops."
  *Over the screen* costs no source.
- **D106 (renderer's generators and effects).** The graphics stage reuses the renderer's shader programs and the chain
  structure; keys are two fixed programs added next to the generators. The "text layer" libmpv stays for masks and the
  OSD.
- **D123 (effect rate).** Effects on a graphic run at the effect rate, so on a Pi 4 an animated effect is at 30 a second
  while the graphic is drawn at 60; a moving crawl is therefore smooth even with an effect on.
- **D55, D58, D93 (effects choose their own size on small boards).** A graphic's effects run at the graphic's own size,
  which is usually smaller than a source's, so they are cheaper than a source's.

## 10. Slices (pull requests, in build order)

Each is shippable alone and leaves the node working. "Device rows" go into `tools/DEVICE-TESTING.md`, to be run on the
real boards (Pi 4 and Pi 3 on the test rig, the desktop; the X27 only for the final show check).

| # | Slice | Gives | Device rows |
| --- | --- | --- | --- |
| 1 | **The rasteriser and safe logo upload.** `pvj-raster` (PNG, JPEG, SVG to normalised PNG), its socket-activated unit, the graphics store, Library > Graphics tab with upload, thumbnails, rename, delete, the limits of 7.2. Nothing is drawn yet. Then the **independent review** (7.5) before slice 2 builds on it. | Logos can be uploaded safely and seen as thumbnails. | Pi 4 and Pi 3: upload a 3 MB PNG, a JPG, a clean SVG; a text-in-SVG and a 12 MB PNG are refused in plain words; a truncated PNG is refused; node memory and CPU do not move during a refused bomb; `systemctl status` shows the unit with no network. |
| 2 | **Static text and logos on every tier, through mpv.** Rasteriser gets text (FreeType, bundled fonts, licences added); the settings key, the routes, Play > Graphics (type, font, size, colour, outline, shadow, position, show and hide, Lines); drawn with `overlay-add` ids 20 to 27 from a worker; OSC `text`, `send`, `hide`. The lean-tier ASS spike. | The first thing Anthony can use on any node, including a Pi 3. | Pi 3, Pi 4 (classic and New), desktop: type a line and see it within 250 ms; change fonts; a phone and a laptop; the OSC push from TouchOSC; the clip keeps its rate; node restart brings back what was on; the X27 once. |
| 3 | **The GPU graphics stage and motion.** In `pvj-render`: texture upload, the graphic quad, opacity, fade, typewriter, scroll, crawl, drawn over the screen after the final pass; classic and lean keep slice 2's path. Frame cost numbers recorded. | Smooth motion on Pi 4, Pi 5 and desktop. | Pi 4 at 1080p output with a 1080p clip: scroll and crawl for 10 minutes at 60 flips and 0 dropped; Pi 5 and desktop with four graphics; fade tested at 0.5 and 5 s; a very long crawl refused with words; a node that falls back to classic mid-show still shows the static graphic. |
| 4 | **Keying and effects.** Alpha, luma and chroma keys with the picker; up to three effects with alpha-aware effects added; baked versions for classic and lean. The alpha spike. | Logos from JPGs and green backdrops; glow and shadow. | A black-backed JPG with luma key; a green-screen logo with chroma; edges at 2 m; Pi 4 cost with a key moving and an effect on; the Pi 3 baked key matches the Pi 4's live key to the eye. |
| 5 | **As a source and over a source.** `where` gains `over` and `as`; D101 mapping carries them; the warning about using a source; text and logos as a surface's picture; the source's chain applies. | A surface that shows only a DJ's name; a logo on one surface's clip. | Pi 4 with two sources and the mapping: a graphic on Source 2 shown by one surface; a clip on the other; switching a pad away from Source 2 and back; Pi 3 shows the "needs the New renderer" line. |
| 6 | **Controllers and pads.** MIDI actions, learn, the controller lights; OSC addresses for position, opacity, line; state sent back; the Graphic pad; settings export and import including the graphics (and, if ticked, the files, re-run through the rasteriser on import); `PANEL.md`, `OSC.md`, `MIDI.md` brought up to date. | Fire a line from a pad, a knob, or a phone. | The three MIDI controllers; TouchOSC on the tablet and phone: send, toggle, label receives; an import of a bundle with a bad PNG is refused. |
| 7 | **Optional later: Owner's font upload and the Extended languages pack** (4.4, 4.3). Only if Anthony asks, after the review of 7.5 includes fonts. | Own fonts, other scripts. | Fuzz of the font path; a font with no Cyrillic; a 5 MB font refused; node log shows the font never left the node. |

Slice 1's review is the only gate between slices. Slices 2 to 6 each need the normal pull request review; slice 3 and 4
also need the frame cost numbers in their descriptions.

## 11. Risks

- **Texture size limits** on a Pi 4 limit the longest crawl. Said in words, not hidden.
- **Alpha through the effect passes** may need more than the plan says; slice 4's first step is the spike.
- **Two lists (masks and graphics)** may confuse. Section 9 explains the line and the panel will say it.
- **Typed text as a safety issue.** An Operator can put any words on the wall; that is by design. A Guest cannot.
- **A new C program** (`pvj-raster`) is more to maintain and to build in the image (FreeType, libpng, libjpeg-turbo
  headers needed to build; the runtime libraries are already on the node as mpv's). Slice 1 includes the image's
  package list and a CI build with warnings as errors, as `render/` has.
- **mpv 0.37 on the lean tier** may not animate ASS in an OSD overlay; the table has a fallback.
- **Clock and flips on a Pi 3** with overlay-add at 12 steps a second are cheap for small bitmaps but have not been
  measured; slice 2 measures them.

## 12. Open questions for Anthony (at most five)

1. **Where do graphics live, and do the masks stay separate?** Recommended: yes. A new Play > Graphics screen, masks
   stay in Shape > Picture, and the words in the panel say which is which. Merging can come later.
2. **May a Guest push text?** Recommended: no. Operators and the Owner only; a phone with an Operator code (or a
   TouchOSC layout, with OSC on) can push a line.
3. **Fonts: bundled list only for now?** Recommended: yes. The 11 fonts in section 4.2 (name any you want struck or
   added; the rule is open licence); uploading fonts waits, Owner only, as slice 7 if you ever need a house font.
4. **SVG logos: is it fine that the SVG is turned into a PNG and not kept?** Recommended: yes. It is the safe way, and
   2048 px is enough for a wall; text inside an SVG must be converted to outlines first.
5. **Does *As Source N* using up a source slot on a Pi 4 (two sources) suit you?** Recommended: yes, and make *Over the
   screen* the default so most shows never spend a source. If you want text sources and two clips at once on a Pi 4,
   that needs more sources on that board, which is a measured, separate change (D122).

## 13. What this design could not settle

- Whether mpv 0.37's `osd-overlay` animates ASS time on a Pi 3 (slice 2 spike).
- Whether the renderer's effect passes keep alpha well enough for the colour effects (slice 4 spike).
- The exact insertion point of the graphics stage in `render/src/main.c` (slice 3 confirms).
- The final font list's licences, which must be re-read from each release when added.
- The frame cost numbers in 3.2 are targets; nothing here is measured yet.
- The decision number: D127 and D128 are already named in open pull requests (#224 and #230), so this uses D129. If
  another is taken first, `tools/project-log.py next` on the day gives the free one and the file is renamed.
