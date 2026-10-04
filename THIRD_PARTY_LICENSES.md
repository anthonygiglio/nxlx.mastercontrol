# Third-party components

Inventory of components bundled with or fetched by the legacy code. Licences are listed as understood from the upstream projects and must be verified before any release. This file is a work in progress.

| Component | Where | Licence (to verify) |
| --- | --- | --- |
| omxplayer, omxplayer-sync (debs and fork) | `sync/` | GPLv3 |
| ofxPiMapper, openFrameworks | `sync/` (mapper zips) | MIT |
| Bootstrap 4.6.1 | `assets/bootstrap/` | MIT |
| jQuery 3.5.1 (also unused 3.3.1, 2.2.0) | `assets/js/`, `js/` | MIT |
| Moment.js | `js/moment.js` | MIT |
| offline-js | `offline.min.js` | MIT |
| Tooltipster | `assets/` (to locate) | MIT |
| html5shiv | `js/html5shiv.min.js` | MIT / GPLv2 |
| filebrowser | `sync/` | Apache-2.0 |
| tty-clock (xorg62) | `sync/tty-clock` | BSD-3-Clause |
| TCPSClient (Nozomu Miura) | `sync/TCPSClient.bin` | Unknown, no source in repo |
| pwomxplayer / PiWall (Alex Goodyear) | `sync/` | Unknown |
| HPlayer (Atelier de Dispositifs Numériques) | `sync/` | Unknown |
| ISF-Files generator shaders (VIDVOX and contributors) | `pvj/shaders.d/isf-files/` | MIT (verified, see below) |

## Shader pack: ISF-Files (new code's side of the tree)

Generator shaders from Vidvox's public ISF collection, bundled with the Shaders and Vibes module as a pack of their own. They are **not** this project's work and are not under its Apache-2.0 licence.

- Source: https://github.com/Vidvox/ISF-Files, folder `ISF/`
- Commit: `395072d48b3ce7351ccb20a5fda54470591324df` (2026-06-04)
- Licence: MIT. The repository's `LICENSE` covers the whole repository, names the year 2018 and **no copyright holder**; it is copied unchanged to `pvj/shaders.d/isf-files/LICENSE` and printed below. GitHub reports the repository as MIT as well.
- Each file is **byte for byte the upstream file**; only the file name differs (lower case, hyphens for spaces, the prefix `isf-`, because the release tooling and the shader ids are happier without spaces). `pvj/shaders.d/isf-files/SHA256SUMS` holds the checksums and a test compares them. Each file keeps its own `CREDIT` field, which the panel shows.
- Declared in `REUSE.toml` as MIT (the two noise shaders with Ashima Arts and Stefan Gustavson as further copyright holders); the licence text for REUSE is `LICENSES/MIT.txt`.
- **The notices travel with the files.** A release and the image carry only `pvj/`, `bin/` and `install/`, not this document, so both licence texts lie in the pack folder itself: `pvj/shaders.d/isf-files/LICENSE` (ISF-Files) and `pvj/shaders.d/isf-files/LICENSE.webgl-noise` (the simplex noise inside two of the files). A test pins both by checksum.

How the files were chosen (2026-10-04): of the 327 `.fs` files upstream, only generators that the box's translator takes and that drew a varied picture on a real player in CI were candidates, and of those only files whose origin is clear. A file was left out when its own comments point to code from somewhere whose terms could not be established as allowing redistribution; see "Not bundled" below.

| File here | Upstream name | CREDIT in the file |
| --- | --- | --- |
| `isf-color-bars.fs` | Color Bars.fs | VIDVOX |
| `isf-corner-colors.fs` | Corner Colors.fs | VIDVOX |
| `isf-linear-gradient.fs` | Linear Gradient.fs | by Carter Rosenberg |
| `isf-radial-gradient.fs` | Radial Gradient.fs | by Carter Rosenberg |
| `isf-ridgelines.fs` | Ridgelines.fs | by VIDVOX (simplex by Ashima Arts / Stefan Gustavson) |
| `isf-simplex-noise.fs` | Simplex Noise.fs | by VIDVOX (simplex by Ashima Arts / Stefan Gustavson) |
| `isf-sine-warp-gradient.fs` | Sine Warp Gradient.fs | by VIDVOX |

### The licence of ISF-Files (verbatim)

```
MIT License

Copyright (c) 2018 

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### Notices of code inside those files

Two files say they contain code from another MIT project and only name the licence. The notice that project asks to be kept, which is also shipped unchanged as `pvj/shaders.d/isf-files/LICENSE.webgl-noise`:

`isf-simplex-noise.fs` and `isf-ridgelines.fs` hold the 2D simplex noise of https://github.com/ashima/webgl-noise (its `LICENSE`, read 2026-10-04):

```
Copyright (C) 2011 by Ashima Arts (Simplex noise)
Copyright (C) 2011-2016 by Stefan Gustavson (Classic noise and others)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

### Not bundled, although the box can run them: the origin is not clear enough

These generators translate, and the repository they are in is MIT, but their own comments point to code from elsewhere. Vidvox's licence can only cover what Vidvox had the right to license, so they are left for the owner to add to a box by hand (Shaders > Upload), which is use, not redistribution by this project.

| Upstream file | What its comments say | Why it is left out |
| --- | --- | --- |
| Brick Pattern.fs | "Based on Brick example from https://thebookofshaders.com/09/, Author @patriciogv, 2015" | The Book of Shaders has its own terms (GitHub reports its licence as "Other"); not MIT |
| Truchet Tile.fs | "adapted from https://github.com/pjkarlik/TruchetTiles/..." | that repository no longer exists (404 on 2026-10-04); no licence to read |
| Star.fs | "Adapted from https://glsl.io/transition/d1f891c5585fc40b55ea", and maths from pixeleuphoria.com | sources not reachable from here; terms unknown |
| Heart.fs | "Adapted from https://glsl.io/transition/d71472a550601b96d69d" | The glsl.io page was not opened, but the same transition is on GitHub as `transitions/heart.glsl` in gl-transitions/gl-transitions ("Author: gre, License: MIT", read 2026-10-04), so the origin is very likely fine. It stays out for the other reason: it is a shape of two colours and would not pass the picture check, and bundling it properly would also mean shipping gre's notice |
| Worley Cells.fs | "Standard 'iq' sin-based hash" (line 121) | names an outside author (Inigo Quilez) for a piece of its code; his terms for that piece were not read. Small as the piece is, this is the same rule that keeps Triangle and Star out. It was in the first version of this pack and was taken out after review |
| Color Test Grid.fs | nothing (lines 48 to 65 hold the `rgb2hsv` and `hsv2rgb` functions found all over the web, usually attributed to Sam Hocevar) | uncredited upstream, terms not checked; taken out after review |
| Triangle.fs | "functions via http://stackoverflow.com/questions/2049582/..." | Stack Overflow text is CC BY-SA, a share-alike licence that MIT does not satisfy |
| Digital Clock.fs | "a simplified version of the number drawing from http://www.interactiveshaderformat.com/sketches/120" | the sketch's terms are unknown (and the clock would stand still here: DATE does not tick) |
| Random Characters.fs | "Inspired / adapted from Tim Gfrerer's ... blog post", font data from the Tamsyn font | two outside sources with their own terms, neither read |
| Color Scales.fs, Color Organ Polyphonic.fs | "Color Scales via http://rhythmiclight.com/archives/ideas/colorscales.html" | tables of colours taken from a web page whose terms were not read |

The sources outside GitHub were not opened (this work was limited to GitHub), so "unknown" here means not established, not that anything is wrong with them.

**What this rule can and cannot do.** It catches borrowing that a file admits to, in a comment or its credit. It cannot catch code that was copied without a word, as Color Test Grid shows: that one was found only because a reviewer recognised the snippet. The seven files that remain were read again for well-known snippets (colour conversions, the usual one-line hashes, noise) and none was found beyond the credited simplex noise; that is a reading by people who know some of the common ones, not a proof.

### Not bundled for another reason: the licence is clear, the picture check was not passed

Eighteen more generators are, as far as their comments say, VIDVOX's own under the same MIT licence (Line Group.fs also holds a dither based on https://github.com/hughsk/glsl-dither, MIT, by Hugh Kennedy). They were tried on the real player in CI and did not pass the check every bundled shader must pass (taken by the player on OpenGL ES and on desktop OpenGL, a varied picture of more than 40 colours, not dark), so they are not in the pack. Nothing about their licence stands in the way of adding them later or of uploading them. The list and what CI saw are in [pvj/SHADERS.md](pvj/SHADERS.md).
