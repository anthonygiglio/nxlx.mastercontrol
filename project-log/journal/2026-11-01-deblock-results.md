<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
## 2026-11-01, Deblocking off: the soak, the way back, and where the stutter went

- DB1 to DB3 run on master 7d6b151 and written up in `tools/DEVICE-TESTING.md`. The hour: 59.99 flips, 0.008 missed a second, box 62 percent, 51.6 C at the end, memory flat at 434 to 437 MB from minute 30, 22 of 22 transitions. The way back restarts the player once in each direction, with no loop.
- DB2's own row failed: CRT and Oscilloscope still fell under 25. That disagreed with the short test, so the scripts were compared. The soak's script polled the node over ssh through each crossfade. Blocks with and without it showed the polling was the cause. Added an addendum to D120 and a lesson.
- An open panel was tested as well, as that is the normal case: one and two clients polling as Play > Pads does, 0 s under 25 in all 32 crossfades. Imitated from the page's code, not a real browser.
- The final soak's paragraph in the device notes is corrected, and the bug it opened is closed by the addendum for the setup tested.
