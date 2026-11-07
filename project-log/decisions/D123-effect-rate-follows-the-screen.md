<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## D123. On the full tier the effect rate follows the screen, and a screen is never drawn by a software rasteriser (2026-11-07)

D106 runs a generator, and an effect that reads the clock, at most 30 times a second (`--fx-fps`, the carrier's rate on classic). That number was chosen for a Pi 4, where the 2026-10 runs showed the graphics chip had no room for more. A Pi 5 and a desktop (the full tier of `pvj/profile.py`) are not measured, and on a 60 Hz screen 30 a second is visibly half the rate of the clip.

Now the full tier passes `--fx-fps` as the screen's refresh rate, rounded, at most 60 and at least 1, taken from the mode the player chose (`--drm-mode=WxH@HZ`); with no rate in the mode it passes nothing and pvj-render keeps 30. The lean, standard and unknown tiers do not pass it: their argument list is what it was, and 30 stays. Why: the cost of a pass is the board's to bear, and the full tier has the room to try; 60 is the most a pass is asked for because nothing is measured above it. Not measured on a Pi 5 or a desktop yet; the profile row says so, and `--fx-fps` is the only thing to change if it drops frames.

Also decided here: on a screen, pvj-render refuses a software rasteriser (llvmpipe, softpipe, swrast, "Software Rasterizer") with exit code 5, and the player falls back to classic with "this node has no graphics chip the new renderer can use". Headless runs, which are the tests' and CI's, are not checked. Cost / rules out: a desktop with no working graphics driver draws with classic mpv, not slowly with the new renderer.
