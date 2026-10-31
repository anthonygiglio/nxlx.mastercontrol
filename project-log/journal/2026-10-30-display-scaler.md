<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-10-30, the display scaler as a setting (D108), and wipes and slides mended

- **Built:** `renderer.final` (auto, full, 720), the final pass at 720 lines in a corner of the one GBM surface with the plane's source rectangle set in the frame's own atomic commit, the busy rule in the supervisor, `final_now` on the Renderer card, `pvj-final` and the new `pvj-render` keys (`load`, `final-lines`, `final-switches`, `final-why`).
- **Mended:** `pvj-capture` at the screen's size in both states; a still of the window on pvj-render is now the final picture at the screen's size (`pvj-screen`), which also ends the shipped refusal between two 1080p pictures and under a mapping; a still of the wrong size is a cut, not five minutes without transitions.
- **Decided by measuring:** one surface, with `glInvalidateFramebuffer` in place of the clear: 62 to 63% GPU, against 69% for a whole clear and 83% for a scissored one.
- **Measured on the test node, counters only, 4 sources at 720p, mapping on, 1080p60:** full 46.4 and 46.1 flips, 88% GPU; 720 60.0 and 60.0, 63%; auto 60.0 and 59.9, 63%. 40 switches at a light load, 0 vblanks missed. All numbers are in D108. The node was restored as found.
- **Found:** at 4 sources with the mapping a wipe runs but the clip drops about 30 frames in 2 s, so MAX_DROPS gives up and the box dips for 5 minutes. Left as it is.
- **RT1 written up** in tools/DEVICE-TESTING.md, with what was read from the node today.
- **Not done:** the HDMI check of this build (the coordinator runs it after merge), a long run, the rows RT7 to RT12.
