<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## D108. The final pass may draw at 720 lines and let the display's scaler bring it to the output: a setting, on when the box is busy (2026-10-30)

**The owner's word.** The display scaler is built as a setting, on when the box is busy, and wipes and slides must be mended first. Why: at 1080p with 4 sources and mapping on, flips go from 47 to a steady 60, gaps over 2 vblanks at HDMI from 212 to 4, GPU from 89 to 62 percent (`measurements/2026-10-30-scaler`). The cost: mapped edges and renderer text a little softer, and thin lines in a squeezed mapped area can break into dashes. That is why it must not be on when the box has room.

**The setting.** `renderer.final` beside `detail`: "auto" (the default, no key in the file), "full", 720. Owner only, not exported, not through the support tunnel, as `detail` is. A change restarts pvj-render, as a detail change does. "full" passes no argument, and pvj-render then flips through the legacy calls exactly as before: no atomic capability is even asked for. "auto" gives `--final=auto` on a Pi 3 or 4 (SMALL_BOARDS) and nothing elsewhere. 720 gives `--final=720` on any board.

**How it draws.** One GBM surface of the mode's size. While small, the final pass draws in the top left 1280 x 720 of the buffer (the width follows the output's shape, as detail does) and the primary plane's SRC_W and SRC_H say how much of it is shown. The rectangle goes in the same atomic commit that flips to that frame, so a frame never goes out with another frame's rectangle. The draw size is taken once a frame, at its top. Wherever the plane shows the whole buffer the flip is the legacy page flip, so "auto" at full size is the path as shipped: the atomic commit is used only while small and for the one frame that comes back. The first frame is always the legacy mode set; the plane is looked for and a DRM_MODE_ATOMIC_TEST_ONLY commit made after it. No plane, no atomic support, an output of 720 lines or fewer, or a refused test: full size, said once in the log, on the Renderer card and in `final-why`, and the start goes on. A scaled frame the kernel refuses at run time goes the same way, and the next frame is the atomic commit back to the whole buffer. A refusal is said once and is for good: `pvj-final` is refused from then on.

**One surface, not two: measured.** The design feared the cost of the rest of the buffer on Mesa's V3D. It is real and it depends on what the frame does to that rest: 4 sources at 720p, mapping on, 1080p60, 20 s windows, final pass in the corner.

| The corner's start | flips/s | GPU render busy |
|---|---|---|
| whole `glClear` | 60.0 | 69% |
| scissored `glClear` | 59.5 | 83% |
| `glInvalidateFramebuffer`, no clear | 59.9, then 60.0 | 62 to 63% |

The scratch build with a surface of 720 lines had 62%, so a second surface would gain nothing. The final pass draws every pixel that goes out, so nothing needs the clear. Both final passes are unchanged at full size: they keep the clear of D106's addendum.

**Busy, in Automatic.** `renderer.final_rule`, a pure function the supervisor applies every half second to pvj-render's counters. To 720 when more than 5 in 100 vblanks were missed over 2 s and something is being drawn. Back to full only when the load (the sources that play, their programs, the mapping on or off) has become lighter than it was at the switch, has stayed lighter for 5 s, and at least 30 s after the switch down. So it never needs a trial at full size, and every way back needs a load lighter than the last. The price of that rule: a lighter load that is still too heavy comes back to full for a few seconds, misses again and goes down to stay at the new load. It was seen once in the run below (back for 4.5 s at 3 sources). A load that stays stays at 720 until it is lighter, which may be a long time after it stopped being needed; that is the choice that stops the flap.

**The rule lives in the supervisor, not in pvj-render.** It can be tested with made-up numbers, it needs no build, and the supervisor already wakes twice a second and writes the report. Its look has a timeout of 0.2 s, so a jammed pvj-render holds up neither the stop's kill nor the check of the wish. pvj-render only counts, and takes `["pvj-final", "full"|720]`.

**The real screen stays.** `app.out_w` and `app.out_h` never change, so the socket's sizes, `/api/mapper` `screen`, saved surfaces and everything placed by screen size are the output's whichever way the pass draws. Only the viewport changes. HOOKED_size stays the output's: HOOKED_pos goes from 0 to 1 over the viewport at either size, so the same surfaces land in the same place, in the warp table and in the editing shader. This differs in one way from the scratch build the picture was judged on: that one built its warp table for a 1280 x 720 screen, and this one keeps the table of the real screen, so a mapped edge's ramp is one screen pixel, which is 0.67 of a drawn pixel at 720. Its effect on jaggies has not been looked at.

**Stills and captures.** `pvj-capture` answers at the screen's size in both states: while small it draws the picture once more into a framebuffer of its own. And the fault as shipped is mended at its root, not only under the scaler: libmpv's "window" on pvj-render is the inside size with no mapping, so a wipe or slide whose still had to be of the window was refused (a 1080p picture, or any clip under a mapping). `Player.still` now asks pvj-render for `screenshot-to-file PATH pvj-screen`: the whole final picture at the screen's size, mapping and text in it, drawn into the readback's own framebuffer. Classic mpv is asked as it always was, and the preview stays libmpv's own. The cause the scaler README read from the code held: with the screen reported at 1280 x 720, a 720p clip was not smaller than the screen, so the clip's own still did not apply and the window fell to 960 x 540. Keeping the real screen ends it by itself. And one still of the wrong size is now a cut (`WrongSize`), not five minutes without transitions; two in a row, counted as slow stills are (SLOW_IN_A_ROW), give up with that reason in the status, and one good still clears the count, so a player whose still is always wrong does not freeze the clip at every play.

**Measured on the test node** (a Pi 4, Mesa 26.2.2, this branch's panel, supervisor and pvj-render from tmpfs through runtime drop-ins, /opt/pvj untouched, 4 sources at 720p, mapping on, 1080p60, software decoding). Counters only: they say what was queued and flipped, not what the screen showed. Plain flip counters cannot show the screen. The dongle was left alone; the HDMI check comes after merge.

| | flips/s | missed/s | GPU render |
|---|---|---|---|
| final full, 2 x 25 s | 46.4, 46.1 | 13.6, 14.0 | 88% |
| final 720, 2 x 25 s | 60.0, 60.0 | 0.0, 0.0 | 63% |
| final auto, after it went down, 2 x 25 s | 60.0, 59.9 | 0.0, 0.1 | 63% |

- **A switch costs no vblank:** 20 switches at 1 s spacing with one clip and mapping off, and 20 more with mapping on: 0 missed in both, flips unbroken (1602 and 1600).
- **Automatic across switches:** it went down within seconds of 4 sources starting (8 in 100 late); with source 4 stopped it held, came back at 5.8 s of lighter load and at more than 30 s after the switch, went down again 4.5 s later (7 in 100 at a load of 4), then held through 4 sources again and mapping off (load 4, not lighter than 4). 3 switches in 3 minutes, 24 vblanks missed in all over them.
- **Sizes in both states:** `osd-dimensions`, `/api/mapper` `screen` and `pvj-capture` all 1920 x 1080, surfaces unchanged.
- **Wipes and slides, one clip:** full and 720 between 720p clips, between 1080p PNGs, under the mapping, and with a switch in the middle: all ran, 60 steps in 2.0 s, none refused. Still 76 to 102 ms for a clip, 221 to 261 ms for the screen.
- **Wipes and slides at 4 sources with the mapping (720):** they run (38 and 41 steps, still 371 and 469 ms), but the clip dropped 27 and 32 frames in 2 s, so the box's own guard (MAX_DROPS, D71) gave up and dips for 5 minutes. That is the guard working, unchanged here; those transitions were refused outright before this.
- **The plane:** the debug state of the plane shows no scaling filter line, and `modetest` is not on the node, so the property list was not read. Nothing is built for it.

**Not tested:** the KMS branch of `out_present` has no automated test: CI has no KMS, so the plane, the atomic commits and the way back to the legacy flip rest on the node's counters above and on rows RT7 to RT12 of tools/DEVICE-TESTING.md.

**Not measured or not known:** the picture of this build at HDMI or on a projector (a frame at a switch, the corner's edges, the mapped edge's ramp above). The sub-pixel shift of the scaler README at a switch: nobody has looked at whether +0.4 px shows as a jump. A long run. Detail 720 or output with the scaler. A Pi 3 (it has OpenGL ES 2 only and pvj-render does not run there). Masks and logos placed by screen size: they keep the real size by construction, and were not looked at.

**Not changed:** lock to 30 (q98), the loop seam, classic mpv, hardware decoding, the shift.
