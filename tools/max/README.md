<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# The nxlx render node patch for Max 9 (D94)

`nxlx-render-node.maxpat` is a small Max patch that is a render node for Mastercontrol: it takes the box's `/nxlx/` cues
(the "max" profile, `pvj/RENDERNODES.md`) on UDP port 7400, drives a placeholder Jitter look, and sends the picture
as NDI so the box can show it.

**Untested in Max.** It was written as Max's JSON patch format by hand and checked only as JSON (the box's tests hold
its addresses to the profile's). The object names were taken from Cycling '74's reference: `udpreceive`, `route`,
`counter`, `sel`, `pack`, `prepend`, `jit.world`, `jit.gl.gridshape`. Open it in Max 9 and fix what Max says.

## What you need

- Max 9 (Jitter is part of it).
- **jit.ndi by 2bbb**, <https://github.com/2bbb/jit.ndi>: a third-party Max package that sends Jitter matrices as NDI.
  You install it yourself in Max 9 (Package Manager, or the release from its own page) under its own licence. It is
  not part of this repository and never will be. The patch uses `jit.ndi.send~ @name nxlx-render-node`; check that
  object's name and its `@name` attribute against the help file of the version you install, and correct the box if
  they differ.
- NDI's own runtime on the Mac if jit.ndi asks for it (NDI's terms, your download).

## How it works

- `[udpreceive 7400]` takes the box's OSC; `[route /nxlx/scene /nxlx/param /nxlx/color /nxlx/go /nxlx/hello]` splits it.
- `/nxlx/scene N`: `N % 4` picks the shape (sphere, torus, cube, plane). `/nxlx/go`: the next one.
- `/nxlx/param scale V` scales the shape; `/nxlx/param spin V` turns it (V times 360 degrees).
- `/nxlx/color bg R G B` is the background (`erase_color`); `/nxlx/color shape R G B` is the shape's colour.
- `/nxlx/hello` prints in the Max window. The box does not read an answer yet.
- `[jit.world nxlx @output_matrix 1]` renders and hands its picture to `jit.ndi.send~`, which sends it as NDI.

## With the box

1. On the Mac: open the patch, check the Max window for errors, see the shape in the world's window.
2. On the box: Setup > NDI input on; Setup > Render nodes > Add: the Mac's address or `name.local`, port 7400, Tool
   "Max/Jitter (the nxlx patch)", its picture `nxlx-render-node` from the list.
3. Send a cue (Setup > Render nodes > Send a cue, "Go to scene") and see the shape change on the Mac and, with "Then
   show", on the box's screen.

The rows RN1 to RN8 in `tools/DEVICE-TESTING.md` are the steps to report back.
