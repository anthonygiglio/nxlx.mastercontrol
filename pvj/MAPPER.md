<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Projection mapping (beta)

Put pieces of the picture onto the surfaces of a set, a building or a curved screen. This replaces the old Mapping tab (ofxPiMapper, steered by a USB mouse and a fake keyboard): there is no mouse on the box and no separate program; the player's own GPU draws the mapping, and you set it up from a phone or laptop.

Switch it on under System > Projection mapping (beta, off by default). The card is on the **Mix** screen; the System page has a button that takes you there. Full-access devices edit; everyone else sees the state.

## Surfaces

| Type | Corners | Use it for |
| --- | --- | --- |
| Quad | 4, with perspective | a wall, a box face, keystone correction |
| Triangle | 3 | a roof, a pointed shape |
| Grid | columns x rows cells, every point movable (up to 8 x 8) | a curved or uneven screen; the picture bends smoothly between points |

Up to 16 surfaces and 64 cells in all. The first surface in the list is drawn on top; use the arrows to change the order. Hide a surface to keep it without drawing it. Everything outside every surface is black.

Each surface shows a part of the picture: its **picture corners** (0 to 1 across the picture). A new surface shows the whole picture; move its picture corners to show only a piece, for example the left half on the left wall and the right half on the right wall.

## Lining up

1. Play something to line up with: Live > Test pattern is ideal.
2. Press **Edit on the display**: the projector shows every surface's outline (the chosen one yellow, the chosen corner as a pink dot).
3. Drag a corner on the small screen in the card, or choose it and use the arrows (1, 10 or 50 pixels a press; the arrow keys work too when the drawing has focus). **Next corner** steps through them; **Move the whole surface** moves all corners together.
4. Switch between **Screen corners** (where it lands) and **Picture corners** (what it shows).
5. Press **Edit on the display** again to finish. The show picture is then prepared in the background and replaces the editing view: on a Pi 4, under a second for a few quads and about 1.5 seconds for a 64-cell grid at 1920x1080 (up to 2 seconds at 2560x1440; unusual shapes, such as many long thin triangles, take longer). Until it is ready the previous picture stays.
6. **Mapping on** keeps it on for the show; it comes back by itself after a restart or a player crash.

Save a finished set-up under a name (up to 8). A saved mapping keeps its place in proportion if the screen size changes (a different projector).

**The picture** is the whole frame of the clip, stretched to the screen while a mapping is shown (so a 4:3 clip fills the width too; choose picture corners to keep its shape). This is needed because the mapping is in screen pixels: with the usual letterboxing, every surface moved into the clip's own area (seen on the Pi with a 720x576 clip). The Mix sliders for size and position still move the picture under the mapping.

**Masks**: use the overlay picture on the same screen (a PNG, black where no light should fall, transparent elsewhere). It is drawn over the mapped picture, in screen space.

## How it works, and what it costs

The picture that would be on screen without mapping is the texture. For the show, the box computes a table (one entry every 4 pixels) of where each screen point comes from, with two layers (the top surface and the one under it) and the distance to each surface's edge, so edges and overlaps stay smooth. The GPU reads the table and the picture: the cost is the same for one surface or sixty-four. While editing, a direct shader that tests each cell is used instead, so changes show at once (it costs more, so playback can stutter while you edit).

Measured on a Raspberry Pi 4 (Debian 13, mpv 0.40) playing a 1080p H.264 film:

| Output | Mapping | Dropped frames a second |
| --- | --- | --- |
| 1920 x 1080 | none, a 2x2 grid over a quad, a 64-cell grid | 0 |
| 2560 x 1440 | none | 0 |
| 2560 x 1440 | any | about 3 |

So map at 1920 x 1080 or less on a Pi 4. The old manual recommended 1280 x 720 video for the mapper, which is still a good idea for heavy files.

While a mapping is shown the player uses 8-bit buffers between its GPU passes (with its usual 16-bit ones, the extra pass alone dropped 8 frames a second at 2560 x 1440); without a mapping it is back to normal. The table holds picture points to about 1/2048 of the picture (under a pixel of a 1920-wide clip), and across a sharp bend in a grid the interpolation is off by up to about half a pixel.

## Safety

- Only numbers from validated surfaces go into the shader; names never do. Corners must stay convex (a folded or flat surface is refused), coordinates are bounded, and the whole list is checked on every change.
- The shader files are written to the player's runtime folder with fresh names (never over a file in use) and removed when replaced, including files left by an earlier run; a newer change's file is never removed by an older one.
- One background build at a time: a drag that sends many changes builds only the newest, and a build that has been overtaken stops early. The panel sends a dragged corner one request at a time, and only the final position unless you are editing on the display.
- A module switched off takes the mapping off the screen at once.

## API

- `GET /api/mapper` (view): `{"enabled", "on", "screen": [w, h], "surfaces", "sets", "edit": {"on", "selected", "corner", "target"}, "status": {"state": "off|building|on|editing|error", "message"}, "limits"}`.
- `POST /api/mapper` (full), one action at a time: `add` (`type`), `remove`, `order` (`dir` up or down), `show` (`on`), `rename` (`name`), `move` (`corner` or -1 for all, `target` screen or picture, `dx`, `dy`; picture moves are in screen pixels too), `place` (`corner`, `target`, `x`, `y`), `grid` (`cols`, `rows`), `set` (`surfaces`), `edit` (`on`, `selected`, `corner`, `target`), `on` (`on`), `save`, `load`, `delete` (`name`). Answers the state.

A surface is `{"id", "type": "quad|triangle|grid", "name", "on", "vertices": [[x, y], ...], "tex": [[u, v], ...], "cols"?, "rows"?}`; screen corners in pixels, clockwise from the top left; a grid's points row by row.

## Not verified

- Only on one Pi 4 with a monitor; not with a projector, and not by the owner at the screen (all checks were screenshots of the player's output).
- Not on a Pi 5 or x86. The Pi 3 is not supported.
- Importing ofxPiMapper's saved `mappersetting*.xml` files is not built yet.
