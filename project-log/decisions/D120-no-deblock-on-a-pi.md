<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## D120. No deblocking on a Pi, so clips stay smooth through a crossfade (2026-11-01)

The final soak showed what D111 said of the incoming clip holds for the rest: with 4 sources of 720p clips, a crossfade's fifth software decoder on four cores held the clips to 6 to 16 frames a second for up to 4 s, while the output stayed at about 60 flips. A test on the node, 6 crossfades a variant, measured `vd-lavc-skiploopfilter`: `nonref` left CRT under 25 for 1.83 s a crossfade, against 2.67 s; `all` left 0 s on every source, lowest second 27, and took the box from 81 to 64 percent in plain playback. `vd-lavc-fast` added nothing. Only `all` does the job.

The cost is the picture: the filter is there to hide block edges, and how much shows on a poor clip was not measured. The owner saw stills of one clip and said to build it.

So pvj-render takes `--skip-deblock`, and `renderer.command` gives it on a Pi 3 or 4, on every video core, set before the core starts as mpv reads it when it opens a decoder. Other boards keep the filter, as nothing was measured on them. Classic mpv is not changed.

`renderer.deblock` true in the settings is the way back, with no page, as `renderer.loop_lists` (D118). Unlike that switch it is part of a run, so the supervisor counts it in `_wish_changed`: it restarts the player once and takes hold. Only True is read, so a file without it is as before and a rollback trips on nothing.

**Not settled:** short runs on one set of clips. The option was read back from source 1 only, so sources 2 to 4 rest on the code and on their frame counts. An hour with crossfades is row DB2. The look on a projector with a poor clip is DB4 and his to judge. Other codecs are not measured.

