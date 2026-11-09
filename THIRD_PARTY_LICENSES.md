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
| ISF-Files filter shaders, for effects (VIDVOX and contributors) | `pvj/effects.d/isf-files/` | MIT (verified, see below) |
| Archivo (The Archivo Project Authors), a Latin subset | `pvj/web/fonts/` | SIL OFL 1.1 (verified, see below) |
| JetBrains Mono (The JetBrains Mono Project Authors), a Latin subset of the Medium weight | `pvj/web/fonts/` | SIL OFL 1.1 (verified, see below) |
| Eleven fonts for text on the screen (Archivo, JetBrains Mono, Inter, Bebas Neue, Oswald, Anton, Montserrat, Orbitron, Press Start 2P, Roboto Condensed, Noto Sans), unchanged TrueType files | `pvj/fonts/` | SIL OFL 1.1, each read from its own release (verified, see below) |
| FreeType: linked by pvj-raster to draw text; not shipped, the box's own package | `render/raster/` builds against it | FreeType licence (FTL) or GPL-2.0, at the user's choice |
| libmpv (mpv), linked by pvj-render; not shipped, the box's own package | `render/` builds against it | GPLv2 or later as Debian builds it (see below) |
| Mesa's EGL, OpenGL ES and GBM, libdrm, glibc: linked by pvj-render; not shipped | `render/` builds against them | MIT (Mesa, libdrm), LGPL-2.1 (glibc) |
| Ableton Link and asio, compiled into pvj-link at install; not shipped, the box's own packages | `render/link/` builds against them | GPL-2.0-or-later (Link), BSL-1.0 (asio) (see below) |
| nanosvg (Mikko Mononen), the SVG reader of pvj-raster, vendored unchanged | `render/raster/vendor/` | zlib (verified, see below) |
| libpng, libjpeg-turbo: linked by pvj-raster; not shipped, the box's own packages | `render/raster/` builds against them | libpng licence, IJG / BSD-3-Clause / zlib (libjpeg-turbo) |

## Not bundled: jit.ndi for the Max render node patch (D94)

`tools/max/nxlx-render-node.maxpat` uses `jit.ndi.send~` from jit.ndi by 2bbb (<https://github.com/2bbb/jit.ndi>), a
third-party Max package. Nothing of it is in this repository or on the box: the owner installs it in Max 9 himself,
under its own licence, as NDI's runtime (D62). The patch is this project's (Apache-2.0).

## Fonts of the panel's "Signal" look (new code's side of the tree)

Two typefaces are shipped with the panel, in `pvj/web/fonts/`, for the look called Signal (D54 in the project log). They are served by the box itself; nothing is fetched from the internet. They are **not** this project's work and are not under its Apache-2.0 licence.

| File | What it is | SHA-256 |
| --- | --- | --- |
| `archivo-latin.06fa7831.woff2` (26788 bytes) | Archivo, upright, the weight axis from 400 to 900 (Regular to Black), the width axis fixed at 100 | `06fa7831060c673ef6e553b846635fb1e7eaf558e717ddfeb7c0a24fd9280529` |
| `jetbrains-mono-500-latin.6c95bc2f.woff2` (8164 bytes) | JetBrains Mono, upright, the one weight 500 (Medium) | `6c95bc2faff7653603df02e7dca2fef5341d7ba49ebe8c952907f8ded2c0eb20` |
| `OFL-Archivo.txt` | Archivo's `OFL.txt`, unchanged | `108b4e57c9c796d3d38d0428ca7ee39de47ad93187302718d9b2d8864b9b716b` |
| `OFL-JetBrainsMono.txt` | JetBrains Mono's `OFL.txt`, unchanged | `b2fe5e8987594e9ffd1d2ca52a2f5d73eb8335243893c5d6254b5ad69269591d` |

- Source: https://github.com/google/fonts at commit `9710da1eacb3be272583c3224dcb70f9da6eadbb` (read 2026-10-04): `ofl/archivo/Archivo[wdth,wght].ttf` (SHA-256 `0e094a7d3c7c4c25cf1310c4b30014f1dae9332220b1c2c88f4fa996f0b05053`) and `ofl/jetbrainsmono/JetBrainsMono[wght].ttf` (SHA-256 `48715a42ec242c21e9f02692891e147d022299a52e48d5e413e1a942193ffeda`), each with the `OFL.txt` beside it. The families' own projects are https://github.com/Omnibus-Type/Archivo and https://github.com/JetBrains/JetBrainsMono.
- Licence: SIL Open Font License 1.1. Copyright lines, from the two `OFL.txt` files: "Copyright 2020 The Archivo Project Authors (https://github.com/Omnibus-Type/Archivo)" and "Copyright 2020 The JetBrains Mono Project Authors (https://github.com/JetBrains/JetBrainsMono)". Neither file declares a Reserved Font Name, so a changed version may keep the family's name.
- **The shipped files are Modified Versions in the licence's sense**, made with fontTools 4.62.1 (`fontTools.varLib.instancer` and `fontTools.subset`, WOFF2 through the `brotli` module): the axes were limited as the table says, and the glyphs cut down to Basic Latin (U+0020 to U+007E), Latin-1 Supplement (U+00A0 to U+00FF) and the signs U+2013, U+2014, U+2018, U+2019, U+201C, U+201D, U+2022, U+2026, U+2039, U+203A, U+2190 to U+2193 and U+2212. Layout features kept: `kern`, `liga`, `calt`, `tnum`, `zero` for Archivo; `kern`, `zero` for JetBrains Mono. The name table is unchanged. Nothing else was altered. A sign outside this set (the transport arrows on the Live screen, a name in another script) is drawn in the device's own font.
- The licence asks that the fonts are not sold by themselves and that the copyright notice and the licence travel with every copy: the two `OFL-*.txt` files are in the same folder, so they are in every release bundle and on every box (a release holds only `pvj/`, `bin/` and `install/`). The licence text for REUSE is `LICENSES/OFL-1.1.txt`; both families are declared in `REUSE.toml`.
- Each font's file name holds the first eight digits of its SHA-256, because the box lets a browser keep a font for a day: a font that is ever rebuilt gets a new name (rename the file, and the name in `pvj/web/app.css`, `REUSE.toml`, this file and the tests).
- A test (`tests/test_license.py`) pins the four checksums, the name and the declaration.

## Fonts for text on the screen (D129, slice 2)

The node draws text itself (design/text-and-logo-sources_2026-11-08.md). Eleven open-licence families are bundled in `pvj/fonts/`, one unchanged TrueType file each, and are read by `pvj-raster` (render/raster/gfx.inc.c) to make a picture of a line of text. They are **not** this project's work and are not under its Apache-2.0 licence. They are never sent to a browser: the panel previews with pictures the node makes.

- Source: https://github.com/google/fonts at commit `9710da1eacb3be272583c3224dcb70f9da6eadbb` (the commit the two panel fonts above were read from), folder `ofl/<family>/`, read on 8 November 2026. The font file and the family's `OFL.txt` were fetched from that one commit; each `OFL.txt` was read in full and each begins with the copyright line below and "This Font Software is licensed under the SIL Open Font License, Version 1.1". **None of the files was changed** (not subset, not instanced, not renamed inside); only the file names here are ours (lower case, no brackets).
- The design document of 8 November expected the Apache licence 2.0 for Roboto Condensed. The release says SIL OFL 1.1 (copyright line below), so that is what is recorded. Licences were taken from the releases, not from the design.
- Reserved Font Names are declared by two families: **Orbitron** ("Orbitron") and **Press Start 2P** ("Press Start 2P"). The files are bundled unchanged, so nothing is reused under another name. The other nine declare none.
- The licence asks that the fonts are not sold by themselves and that the copyright notice and the licence travel with every copy: each family's `OFL-<Family>.txt` is in `pvj/fonts/` beside the font, so it is in every release bundle and on every node. The licence text for REUSE is `LICENSES/OFL-1.1.txt`; every file is declared in `REUSE.toml`. The two OFL files of Archivo and JetBrains Mono are the same files as in `pvj/web/fonts/` (the same checksums).
- Axes. The variable fonts keep all their axes; the node sets only the weight (`wght`) and leaves every other axis at its default. A static font has the one weight.

| File | Family, copyright line from its `OFL.txt` | Weights | SHA-256 of the file | SHA-256 of its `OFL-*.txt` |
| --- | --- | --- | --- | --- |
| `archivo.ttf` (658596 bytes), from `ofl/archivo/Archivo[wdth,wght].ttf` | Archivo: "Copyright 2020 The Archivo Project Authors (https://github.com/Omnibus-Type/Archivo)" | weight 100 to 900, width 62 to 125 (the width is left at 100) | `0e094a7d3c7c4c25cf1310c4b30014f1dae9332220b1c2c88f4fa996f0b05053` | `108b4e57c9c796d3d38d0428ca7ee39de47ad93187302718d9b2d8864b9b716b` (`OFL-Archivo.txt`) |
| `jetbrains-mono.ttf` (187208 bytes), from `ofl/jetbrainsmono/JetBrainsMono[wght].ttf` | JetBrains Mono: "Copyright 2020 The JetBrains Mono Project Authors (https://github.com/JetBrains/JetBrainsMono)" | weight 100 to 800 | `48715a42ec242c21e9f02692891e147d022299a52e48d5e413e1a942193ffeda` | `b2fe5e8987594e9ffd1d2ca52a2f5d73eb8335243893c5d6254b5ad69269591d` (`OFL-JetBrainsMono.txt`) |
| `inter.ttf` (876576 bytes), from `ofl/inter/Inter[opsz,wght].ttf` | Inter: "Copyright 2020 The Inter Project Authors (https://github.com/rsms/inter)" | weight 100 to 900, optical size 14 to 32 (left at 14) | `29160a80ff49ddcab2c97711247e08b1fab27a484a329ce8b813d820dc559031` | `5b9321a4298cfeb6b34354164a1c3afc3db114569984c502b9b35d988fd58c57` (`OFL-Inter.txt`) |
| `bebas-neue.ttf` (61400 bytes), from `ofl/bebasneue/BebasNeue-Regular.ttf` | Bebas Neue: "Copyright © 2010 by Dharma Type." | one weight (Regular) | `08e4623805102d819f58601e46e345648846075e363b2ceb23313c2d1c83ec73` | `72082f6cb4d04be2ecf7cc7d9e1e7d73787f0af8a5a278a47cade70c16b78341` (`OFL-BebasNeue.txt`) |
| `oswald.ttf` (172088 bytes), from `ofl/oswald/Oswald[wght].ttf` | Oswald: "Copyright 2016 The Oswald Project Authors (https://github.com/googlefonts/OswaldFont)" | weight 200 to 700 | `5b38c246e255a12f5712d640d56bcced0472466fc68983d2d0410ec0457c2817` | `0fd731a904b729a4e02eaf5e8ebd06783edd9abe400e8882760160230675b652` (`OFL-Oswald.txt`) |
| `anton.ttf` (170812 bytes), from `ofl/anton/Anton-Regular.ttf` | Anton: "Copyright 2020 The Anton Project Authors (https://github.com/googlefonts/AntonFont.git)" | one weight (Regular) | `a4ba3a92350ebb031da0cb47630ac49eb265082ca1bc0450442f4a83ab947cab` | `ee67e6ee22790b7929f1a3769ca2801d565c64b5a9096942c1adf5596de9c9e4` (`OFL-Anton.txt`) |
| `montserrat.ttf` (744936 bytes), from `ofl/montserrat/Montserrat[wght].ttf` | Montserrat: "Copyright 2024 The Montserrat.Git Project Authors (https://github.com/JulietaUla/Montserrat.git)" | weight 100 to 900 | `0f7b311b2f3279e4eef9b2f968bcdbab6e28f4daeb1f049f4f278a902bcd82f7` | `8b7141c03fa4f8d44e6345d5d4931709290f0f67875e452e95ac1fd3a027802e` (`OFL-Montserrat.txt`) |
| `orbitron.ttf` (38576 bytes), from `ofl/orbitron/Orbitron[wght].ttf` | Orbitron: "Copyright 2018 The Orbitron Project Authors (https://github.com/theleagueof/orbitron), with Reserved Font Name: "Orbitron"" | weight 400 to 900 | `f42db2dd16e642258e35782916eceb1dcdbea06fb958d77ad71dc5963587e8fd` | `ab609b0e110d622435ff337cdf233288556e011bbf9bd0550be98846c0630819` (`OFL-Orbitron.txt`) |
| `press-start-2p.ttf` (118204 bytes), from `ofl/pressstart2p/PressStart2P-Regular.ttf` | Press Start 2P: "Copyright 2012 The Press Start 2P Project Authors (cody@zone38.net), with Reserved Font Name "Press Start 2P"." | one weight (Regular) | `034c77f1f05ec89421e4a63f0e3a4ca1ecf852cc6d2bf611f126f275728e017d` | `705960c3281a5765ecc0b59bd4ed7ca59eed165748076bc2fc3e8fdbfeb944b0` (`OFL-PressStart2P.txt`) |
| `roboto-condensed.ttf` (371616 bytes), from `ofl/robotocondensed/RobotoCondensed[wght].ttf` | Roboto Condensed: "Copyright 2011 The Roboto Project Authors (https://github.com/googlefonts/roboto-classic)" | weight 100 to 900 | `dace262afcee68a5276f200d8026c57221735c0118ab5fda8c2c0d3dc409a8d0` | `0e4cc6ece88573545be2ed25835363662a6182ba4a4c1b5c8feda52add30e8a6` (`OFL-RobotoCondensed.txt`) |
| `noto-sans.ttf` (2049096 bytes), from `ofl/notosans/NotoSans[wdth,wght].ttf` | Noto Sans: "Copyright 2022 The Noto Project Authors (https://github.com/notofonts/latin-greek-cyrillic)" | weight 100 to 900, width 62.5 to 100 (left at 100) | `bfb7bb691513f12e734dc346c03a03f784912432d7e3fa8e56efcf906fe86b3d` | `cee9892f9f0cc8fe882c9e9537ee6a89621d86ee7ceaf70b02e2b2b1c25c061a` (`OFL-NotoSans.txt`) |

- Orbitron: It declares the Reserved Font Name "Orbitron": it is bundled unchanged, so the name is not reused for a modified copy.
- Press Start 2P: It declares the Reserved Font Name "Press Start 2P": it is bundled unchanged, so the name is not reused for a modified copy.
- Roboto Condensed: The design document expected the Apache licence 2.0 for this one; the release says SIL OFL 1.1, and that is what is recorded.
- Total size of the font files: 5.2 MB (the design expected 1.5 to 3 MB; the variable files of Noto Sans, Inter and Montserrat are the large ones, and none was cut down so that all stay the releases' own).
- A test (`tests/test_graphics.py`, `FontsTest`) pins every checksum above, the licence text's heading and copyright line, and the `REUSE.toml` entry of each file.

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

## Shader pack: ISF-Files filters, for effects (new code's side of the tree)

Filter shaders from the same collection, the same commit and under the same MIT licence as the generators above, bundled as the pack of **effects** (filters put on over the playing picture, [pvj/SHADERS.md](pvj/SHADERS.md#effects)). They are **not** this project's work and are not under its Apache-2.0 licence. The project's own filters lie beside the pack folder (`pvj/effects.d/fx-*.fs`, Apache-2.0).

- Source: https://github.com/Vidvox/ISF-Files, folder `ISF/`, commit `395072d48b3ce7351ccb20a5fda54470591324df`.
- Licence: MIT, the repository's `LICENSE` (printed above), copied unchanged to `pvj/effects.d/isf-files/LICENSE`, so the notice travels with the files in a release.
- Each file is **byte for byte the upstream file** under a name without spaces (lower case, hyphens, the prefix `isf-`). `pvj/effects.d/isf-files/SHA256SUMS` holds the checksums; a test compares every file with it and pins the list itself.
- Declared in `REUSE.toml` as MIT. None of these files holds the simplex noise or names any other project, so no further notice is needed.

| File here | Upstream name | CREDIT in the file |
| --- | --- | --- |
| `isf-chromatic-aberration.fs` | Chromatic Aberration.fs | by VIDVOX |
| `isf-color-monochrome.fs` | Color Monochrome.fs | by zoidberg |
| `isf-corner-color-tint.fs` | Corner Color Tint.fs | VIDVOX |
| `isf-double-vision.fs` | Double Vision.fs | by VIDVOX |
| `isf-duotone.fs` | Duotone.fs | by zoidberg |
| `isf-edge-blowout.fs` | Edge Blowout.fs | VIDVOX |
| `isf-false-color.fs` | False Color.fs | by zoidberg |
| `isf-flip-h.fs` | Flip H.fs | by VIDVOX |
| `isf-flip-v.fs` | Flip V.fs | by VIDVOX |
| `isf-gamma-correction.fs` | Gamma Correction.fs | by zoidberg |
| `isf-hyperspace.fs` | Hyperspace.fs | VIDVOX |
| `isf-interlace-mirror.fs` | Interlace Mirror.fs | by Carter Rosenberg |
| `isf-kaleidoscope-tile.fs` | Kaleidoscope Tile.fs | by VIDVOX |
| `isf-kaleidoscope.fs` | Kaleidoscope.fs | by VIDVOX |
| `isf-lgg.fs` | LGG.fs | VIDVOX |
| `isf-mirror.fs` | Mirror.fs | by VIDVOX |
| `isf-posterize.fs` | Posterize.fs | VIDVOX |
| `isf-quad-tile.fs` | Quad Tile.fs | by VIDVOX |
| `isf-rgb-eq.fs` | RGB EQ.fs | by VIDVOX |
| `isf-rgb-halftone.fs` | RGB Halftone.fs | by zoidberg |
| `isf-rgb-invert.fs` | RGB Invert.fs | by VIDVOX |
| `isf-sine-warp-tile.fs` | Sine Warp Tile.fs | by VIDVOX |
| `isf-triple-rotate.fs` | Triple Rotate.fs | by VIDVOX |
| `isf-white-point-adjust.fs` | White Point Adjust.fs | by zoidberg |
| `isf-zoom.fs` | Zoom.fs | by VIDVOX |

How these were chosen (2026-10-05): of the 327 `.fs` files upstream, 112 translate as effects (114 before the review's stricter rules) (one picture input called `inputImage`, one pass, no vertex shader). Candidates were only files that **credit no source outside the repository**, in their `CREDIT` or in a comment, and in which no well-known snippet was recognised on reading; of those, the ones that are useful to perform with and distinct from each other. Each candidate was then drawn by a real player in CI over a real picture, in three ways of drawing (OpenGL ES, desktop OpenGL, desktop OpenGL 3.1 with GLSL 1.40), with its defaults, with other values in every input and at amount 0; a file had to pass every time to stay.

### Filters not bundled, although the box can run them: the origin is not clear enough

| Upstream file | What it holds or says | Why it is left out |
| --- | --- | --- |
| Color Controls.fs, Solarize.fs, Vibrance.fs, Multi Hue Shift.fs, Luminance Posterize.fs, Soft Flip.fs | the `rgb2hsv` and `hsv2rgb` functions found all over the web (the `vec4 K = vec4(...)` form, usually attributed to Sam Hocevar), without a credit | the same uncredited snippet for which Color Test Grid was taken out of the generators' pack. Hue, saturation, exposure and contrast are in the project's own `fx-grade.fs` instead, written without it |
| Collage.fs, Convergence.fs, Noise Displace.fs, Shake.fs, Multi-Pixellate.fs | the one-line `fract(sin(dot(..., vec2(12.9898, 78.233))) * 43758.5453)` random number that is passed around, without a credit | an uncredited well-known snippet |
| Triangle Mosaic.fs | "iq" hash (its comment names Inigo Quilez) | an outside author's code, terms not read |
| Exposure Adjust.fs | "based on" Apple's Core Image filter reference (a link in a comment) | credits an outside source |
| Sepia Tone.fs | "Adapted from https://www.omniref.com/ruby/gems/essytas/..." | credits an outside source, terms not read |
| Trio Tone.fs | "partly adapted from http://coding-experiments.blogspot.com/..." | credits an outside source, terms not read |
| Toon.fs | "with help from https://github.com/neilmendoza/ofxPostProcessing/..." | credits an outside source, terms not read |
| ASCII Art.fs, Dirty Lens.fs, VHS Glitch.fs, Video Bricks.fs, Dither-Bayer.fs | their `CREDIT` names a port or an adaptation of somebody else's shader ("Ported from https://www.shadertoy.com/...", "Inigo Quilez ported by ...", "original by Staffan Widegarn Ahlvik", "adapted from patriciogv bricks.fs", "Hugh Kennedy, adapted by David Lublin") | credits an outside source; Dither-Bayer would also need glsl-dither's own MIT notice |
| Frosted Glass.fs, Smoke Screen.fs, Boxinator.fs | credited to a web site or a person outside VIDVOX (geeks3d, jackdavenport, mojovideotech) | whether they were written for this repository was not established, and they were not needed |
| the four `v002` files (Bleach Bypass, Crosshatch, Technicolor, Vignette) and v002-CRT-Displacement.fs | credited to v002 and vade, whose plug-ins have their own licence | an outside project's code, terms not read |
| Layer Position.fs, Sliding Strips.fs, Power Warp.fs, Quad Mask.fs, Replicate.fs, Replicate Random.fs | an empty `CREDIT` | nobody is named as the author; with enough credited files to choose from they were not needed |
| Shockwave.fs | credited "by VIDVOX"; its lines 60 to 77 match the "2D Shockwave" of the Geeks3D Shader Library almost line for line, and no licence is stated there | **bundled at first, taken out after the independent review** (2026-10-05): a widely circulated snippet without a credit, which this pack's own rule excludes. The reader who chose the pack did not recognise it; the reviewer did. The project's own `fx-ring.fs` is a ring of another construction |
| Twirl.fs | credited "by VIDVOX"; it matches the Geeks3D "Swirl" post-processing filter, which says it is adapted from evanw's webgl-filter | **bundled at first, taken out after the review**, for the same reason. The project's own `fx-twirl.fs` stands in (the turning done on the angle of a polar form, with another falloff) |
| Sphere Map.fs, Pixellate.fs, Bump Distortion.fs, Ripples.fs, Circle Splash Distortion.fs | credited to VIDVOX or to Carter Rosenberg; each is a short, classic distortion of the kind the same public libraries hold (the Geeks3D shader library, evanw's glfx.js and webgl-filter, The Book of Shaders) | **bundled at first, taken out after the review, as a precaution**: after two of the pack's files turned out to be such snippets, the coordinator decided that these five come out unless each could be shown, against those sources, not to be one. That comparison was not made (it would have had to prove an absence from sources that were not all read), so nothing is claimed about them either way. The project's own `fx-ripple.fs` and `fx-pixel-grid.fs` cover a ripple and big pixels; a bulge, a ball and a splash are not in this version |

As before, the sources outside GitHub were not opened: "terms not read" means not established, not that anything is wrong with them. And as before, this rule catches borrowing that a file admits to and snippets a reader recognises; it is a reading, not a proof.

**Dropped after drawing: none.** In the three rounds of CI after the test rig itself was reliable, all 32 files that were candidates then (the 25 above and the seven taken out after the review) drew right in all three ways of drawing (123 draws a way with the project's own nine of that time). In the rounds before that, draws failed for reasons that were not the filters': screenshots that the rig drew black for any picture, and a limit for "the picture again" set tighter than one more pass through the GPU allows; no file failed for a reason of its own. A file that passes only sometimes in a rig that works is to be taken out, not retried.


## pvj-render and the libraries it links (D95)

`render/` holds pvj-render, a program of this project written for the box (not third-party code). It is under
**GPL-3.0-or-later**, not Apache-2.0, because it links **libmpv** from the box's own `libmpv2` package: mpv is GPL-2.0
or later by default (LGPL only when built with `-Dgpl=false`, which Debian and Raspberry Pi OS do not do), so a program
linking it is distributed under the GPL. GPL-3.0-or-later is compatible with GPLv2-or-later code. The text is
`render/LICENSE` and `LICENSES/GPL-3.0-or-later.txt` (the FSF's text, SHA-256
`3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986`). The rest of the tree stays Apache-2.0: the
panel only starts pvj-render and talks to it over a Unix socket, as it does with the mpv program.

Nothing of these libraries is in the repository, a release bundle or the image's own files: pvj-render is built from
source on the box, against the distribution's packages, which come with their own copyright files
(`/usr/share/doc/<package>/copyright`). The versions it is built and tested against (a distribution's package is
pinned by the distribution's release; CI names what it got in its log):

| Library | On the Pi (Debian 13) | Built and tested in CI | Licence |
| --- | --- | --- | --- |
| libmpv (render API, client API 2.x) | `libmpv-dev` of mpv 0.40.0 | Ubuntu's `libmpv-dev`, and Debian 13's (mpv 0.40.x, the job fails on another) | GPL-2.0-or-later as built |
| EGL, OpenGL ES 3, GBM | Mesa's `libegl-dev`, `libgles-dev`, `libgbm-dev` | Ubuntu's and Debian 13's | MIT |
| libdrm | `libdrm-dev` | Ubuntu's and Debian 13's | MIT |

Nothing else is linked, and no code of mpv, Mesa, kmscube or another project is copied into `render/`; the way mpv
picks a connector and a mode (`video/out/drm_common.c`) and mpv's user-shader format were read in mpv's open source and
are followed as behaviour, so that the panel's arguments and shaders mean the same to both programs.

## pvj-link and Ableton Link (D117)

`render/link/` holds pvj-link, a program of this project written for the box (not third-party code), and a stand-in peer
for its test. Both are under **GPL-3.0-or-later**, like the rest of `render/`, because they compile in Ableton Link, which
is GPL-2.0-or-later in the form Debian packages it (Ableton also sells other terms; this project does not use them).
GPL-3.0-or-later is compatible with GPLv2-or-later code. Link's headers come with asio's, which are under the Boost
Software License 1.0, compatible with the GPL. The rest of the tree stays Apache-2.0: the panel only talks to pvj-link
over a Unix socket and reads a file it writes, as it does with pvj-render.

Nothing of Link or asio is in the repository, a release bundle or the image's own files: the headers are Debian's
`ableton-link-dev` and `libasio-dev`, installed on the box like the renderer's build packages, with their copyright files
at `/usr/share/doc/<package>/copyright`, and pvj-link is built from source on the box. Without them there is no pvj-link,
and nothing else changes.

| Library | On the Pi (Debian 13) | Built and tested in CI | Licence |
| --- | --- | --- | --- |
| Ableton Link, headers only | `ableton-link-dev` 3.1.2 | Debian 13's (the job pvj-link) | GPL-2.0-or-later |
| asio, standalone, headers only | `libasio-dev` | Debian 13's | BSL-1.0 |

The name "Ableton Link" says what the box can join. It carries no logo, and this project is not made by or with Ableton.

## pvj-raster, nanosvg, libpng and libjpeg (D129)

`render/raster/pvj-raster.c` is a program of this project, written for the box, under **GPL-3.0-or-later** like the rest of
`render/`. It decodes the pictures people upload (PNG with libpng, JPEG with libjpeg-turbo, SVG with nanosvg) and writes a
clean PNG. libpng and libjpeg-turbo are the box's own packages (`libpng-dev`, `libjpeg-dev`), linked and not shipped, with
their copyright files at `/usr/share/doc/<package>/copyright`.

Since slice 2 the same program also draws text and logos for the screen (`render/raster/gfx.inc.c`, part of the same
program), with **FreeType**, the box's own package (`libfreetype-dev`; linked and not shipped; its licence is the FreeType
Licence or the GPL version 2, at the user's choice, and its copyright file is at `/usr/share/doc/libfreetype6/copyright`),
and the eleven fonts of the section above. The picture of a logo is scaled and turned by code of this project.

nanosvg is single-header C code by Mikko Mononen under the **zlib licence**, which is compatible with the GPL. Its two
headers are in this repository unchanged, so that the box builds the same code the tests ran, and its licence text is
kept beside them. It has no scripting, no entities, no external fetch, no `<image>` and no text, which is why it was
chosen over a full SVG library.

| File | What it is | SHA-256 |
| --- | --- | --- |
| `render/raster/vendor/nanosvg.h` | nanosvg, the parser | `e34fd5d084be106cea972d19ce5d27fd96d17ba89f8d06bdceee058420c8b2b0` |
| `render/raster/vendor/nanosvgrast.h` | nanosvg's rasteriser | `79a9c5f4db19debf9f3a648a1589e96d92854f245a5cb4f3d823f263785234d8` |
| `render/raster/vendor/LICENSE.nanosvg` | its licence, as upstream's `LICENSE.txt` | `e82bc3dd03400aecabe12201219ba14750dbc4b36faab58663a7a6068548d372` |

Taken on 2026-11-08 from <https://github.com/memononen/nanosvg> at commit `239e102ec2c691f2902e20ace2ed36ee4a35cfe6`
(the head of `master`, 2026-07-09), files `src/nanosvg.h`, `src/nanosvgrast.h` and `LICENSE.txt`.
