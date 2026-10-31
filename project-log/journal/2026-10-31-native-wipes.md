<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-10-31, pvj-render draws the wipes and the slides itself (D111)

- **Why:** at 4 sources with the mapping on, a wipe or a slide stepped by Python made 26 to 33 of 60 steps and dropped 24 to 38 frames, and MAX_DROPS sent the box to dips for 5 minutes (`measurements/2026-10-31-hdmi`). The crossfade, drawn by the renderer, had no such trouble.
- **What:** `pvj-crossfade` takes a fifth word, one of the panel's eight names. Same layers, clock, sound and ends as the crossfade; `move_pass` cuts the picture along an edge where `mix_pass` mixes, at the inside size, one texture read a pixel. The panel asks `moves` once a process and keeps the still for classic, an older renderer, and out of a generator or a live input.
- **Said differently from the brief:** the slide stays "off, uncovering", not a push. The brief said it moves both pictures as it does now; the code, the panel's words and the manual say one. D111 has the reason.
- **Break-checks:** 21 made, 21 caught, in the end. Each of: the wrong side for a wipe, the wrong way for a slide, no move in a slide, the sign of the shift, the layers swapped, the edge at half, the kind not stored, the kind kept for the next crossfade, the fifth word unchecked, the steps not counted, the flag off; on the panel's side the name not passed, the crossfade alone taken, what plays not asked, the status keeping the reason, the word dropped, a fifth word for a crossfade, layers taken for moves, the question asked every time. One survived the first round (the incoming picture moved along with the outgoing one): every test's incoming picture was one colour, so `test_a_slide_leaves_the_new_picture_where_it_is` was added and it is caught now.

**On the node:** not run yet at this commit; `measurements/2026-10-31-native-wipes` will have it.
